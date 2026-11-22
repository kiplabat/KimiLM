"""End-to-end engine checks against a running engine process (python -m dva serve) with stand-ins:
fake_llama_server.py replaces llama-server (scripted answers), fake_mcp_server.py is a stdio MCP connector.

    cd engine && python -m pytest -q tests/test_engine.py
Optional real-model check: set DVA_TEST_LLAMA_SERVER=/path/to/llama-server and DVA_TEST_GGUF=/path/to/model.gguf
"""
import io
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx
import pytest

ENGINE = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent


def tiny_gguf(path, arch="llama", name="fake-model"):
    def s(text):
        b = text.encode()
        return struct.pack("<Q", len(b)) + b
    kvs = [("general.architecture", arch), ("general.name", name)]
    data = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(kvs))
    for k, v in kvs:
        data += s(k) + struct.pack("<I", 8) + s(v)
    Path(path).write_bytes(data + b"\0" * 1024)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def engine():
    tmp = Path(tempfile.mkdtemp(prefix="dva-test-"))
    wrapper = tmp / ("fake-llama-server.cmd" if os.name == "nt" else "fake-llama-server")
    if os.name == "nt":
        wrapper.write_text(f'@"{sys.executable}" "{TESTS / "fake_llama_server.py"}" %*\n')
    else:
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{TESTS / "fake_llama_server.py"}" "$@"\n')
        wrapper.chmod(0o755)
    painter = tmp / ("fake-sd-cli.cmd" if os.name == "nt" else "fake-sd-cli")     # stands in for sd-cli (2.11.0)
    if os.name == "nt":
        painter.write_text(f'@"{sys.executable}" "{TESTS / "fake_sd_cli.py"}" %*\n')
    else:
        painter.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{TESTS / "fake_sd_cli.py"}" "$@"\n')
        painter.chmod(0o755)
    ace = tmp / "ace-programs"                       # stand in for acestep.cpp's two programs (2.13.0)
    ace.mkdir()
    for tool in ("lm", "synth"):
        if os.name != "nt":                          # on Windows a script cannot stand in for an .exe: test skipped there
            prog = ace / f"ace-{tool}"
            prog.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{TESTS / "fake_ace.py"}" {tool} "$@"\n')
            prog.chmod(0o755)
    # KimiLM ships no plugin (2.15.0). The plugin the tests exercise (settings, a set-up script, a pipeline
    # harness, a connector) is installed the way a user installs one: a folder in the data folder's plugins.
    import shutil
    shutil.copytree(TESTS / "plugins" / "datavera", tmp / "data" / "plugins" / "datavera")
    port = free_port()
    token = "test-token"
    env = {**os.environ, "DVA_TEST_CPU": "6x12", "DVA_TOKEN": token, "DVA_LLAMA_SERVER": str(wrapper), "DVA_SECRETS": "file",
           "DVA_SD_CLI": str(painter), "DVA_SOUND_WORKER": str(TESTS / "fake_sound_worker.py"),
           "DVA_SOUND_LIBRARY": "2.0.1", "DVA_SOUND_LOG": str(tmp / "sound-jobs.log"),
           "PYTHONPATH": str(ENGINE), "OLLAMA_MODELS": str(tmp / "ollama"), "HF_HOME": str(tmp / "hf"),
           "DVA_SYSTEM_PLUGINS": str(tmp / "system-plugins")}      # 2.24.0: not the computer's (KimiOS installs one)
    # 2.21.0: the engine under test has no internet, on any computer (the tests never need it; a test such as
    # research_without_internet failed where a real search found real sources). Only this computer is reached.
    for k in [k for k in env if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")]:
        del env[k]
    env.update({"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1,localhost,::1"})
    log = tmp / "engine-output.log"
    proc = subprocess.Popen([sys.executable, "-m", "dva", "serve", "--port", str(port), "--data-dir", str(tmp / "data")],
                            cwd=str(ENGINE), env=env, stdout=log.open("w"), stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        if "DVA_READY" in log.read_text() or proc.poll() is not None:
            break
        time.sleep(0.1)
    assert "DVA_READY" in log.read_text(), log.read_text()[-3000:]
    print("engine log:", log)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"},
                          timeout=60, trust_env=False)
    yield {"client": client, "tmp": tmp, "port": port, "token": token, "ace": ace}
    try:
        client.post("/api/shutdown")
    except httpx.HTTPError:
        pass
    proc.wait(10)


def wait_job(c, job_id, timeout=60, approve=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        if approve and job["status"] == "waiting":
            chat = c.get(f"/api/chats/{job['chat_id']}").json()
            for ap in chat["pending_approvals"]:
                c.post(f"/api/approvals/{ap['approval_id']}", json={"allow": approve == "allow"})
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} still {job['status']}")


def last_assistant(c, chat_id):
    msgs = c.get(f"/api/chats/{chat_id}").json()["messages"]
    return [m for m in msgs if m["role"] == "assistant"][-1]


def test_auth_and_bootstrap(engine):
    c = engine["client"]
    assert httpx.get(f"http://127.0.0.1:{engine['port']}/api/bootstrap", trust_env=False).status_code == 401
    boot = c.get("/api/bootstrap").json()
    assert boot["version"]
    ids = {h["id"] for h in boot["harnesses"]}
    assert {"chat", "agent", "planner", "researcher", "data-analyst", "datavera:dataset-publisher"} <= ids
    assert "custom" in boot["runtime"]["installed"]


def test_model_import_and_settings(engine):
    c, tmp = engine["client"], engine["tmp"]
    tiny_gguf(tmp / "fake-model-Q4_K_M.gguf")
    m = c.post("/api/models/import", json={"path": str(tmp / "fake-model-Q4_K_M.gguf")}).json()
    assert m["id"] == "fake-model-q4-k-m" and m["quant"] == "Q4_K_M"
    rows = c.get("/api/models").json()
    assert rows["default"] == m["id"] and rows["models"][0]["fit"]["cpu"] in ("fits", "tight", "too_big")
    upd = c.put(f"/api/models/{m['id']}", json={"compute": "cpu", "ctx": 4096}).json()
    assert upd["settings"]["compute"] == "cpu" and upd["settings"]["ctx"] == 4096


def test_agent_tool_loop_and_learning(engine):
    c, tmp = engine["client"], engine["tmp"]
    c.put("/api/settings", json={"learn_delay_seconds": 0})
    folder = tmp / "project-files"
    folder.mkdir(exist_ok=True)
    (folder / "data.csv").write_text("year,value\n2020,1\n2021,2\n")
    prj = c.post("/api/projects", json={"name": "Test project", "folders": [str(folder)],
                                        "instructions": "Answer briefly."}).json()
    chat = c.post("/api/chats", json={"project_id": prj["id"], "harness": "agent"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "please list files"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    msg = last_assistant(c, chat["id"])
    tools = [s for s in msg["meta"]["steps"] if s["type"] == "tool"]
    assert tools and tools[0]["name"] == "list_files" and tools[0]["ok"] and "data.csv" in tools[0]["result"]
    assert msg["content"].startswith("Done.")
    # learning pass runs after the turn (delay 0) and stores a global preference
    for _ in range(50):
        mem = c.get("/api/memory").json()["memories"]
        if mem:
            break
        time.sleep(0.2)
    assert any("short answers" in m["text"] for m in mem)


def test_approval_flow(engine):
    c, tmp = engine["client"], engine["tmp"]
    folder = tmp / "project-files"
    chat = c.post("/api/chats", json={"harness": "agent", "folders": [str(folder)]}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "write a note"}).json()
    assert wait_job(c, job["job_id"], approve="deny")["status"] == "done"
    step = [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"][0]
    assert step["declined"] and not (folder / "note.txt").exists()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "write a note"}).json()
    assert wait_job(c, job["job_id"], approve="allow")["status"] == "done"
    assert (folder / "note.txt").read_text() == "hello"


def test_sandbox_blocks_outside_paths(engine):
    import asyncio
    sys.path.insert(0, str(ENGINE))
    from dva.tools import ToolContext, ToolDenied
    ctx = ToolContext(app=None, chat={"id": "x"}, project=None, workspace=engine["tmp"] / "ws",
                      folders=[str(engine["tmp"] / "project-files")], readonly=[], emit=lambda *a, **k: None,
                      approve=None)
    with pytest.raises(ToolDenied):
        ctx.resolve("/etc/passwd")
    with pytest.raises(ToolDenied):
        ctx.resolve("../../outside.txt", write=True)
    assert ctx.resolve("data.csv").name == "data.csv"


def test_chat_and_planner_harnesses(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "chat"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "hello"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert "remember" in msg["content"].lower()  # memory reached the prompt
    chat = c.post("/api/chats", json={"harness": "planner"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "tidy my notes"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert len(msg["meta"]["plan"]) == 2 and all(s["state"] == "done" for s in msg["meta"]["plan"])


def test_skills_and_harnesses(engine):
    c = engine["client"]
    names = {s["name"] for s in c.get("/api/skills").json()["skills"]}
    assert {"skill-creator", "data-analysis", "datavera-publishing"} <= names
    s = c.post("/api/skills", json={"name": "Weekly Report", "description": "Use when writing the weekly report",
                                    "body": "1. Summarise\n2. List risks"}).json()
    assert s["id"] == "weekly-report" and s["editable"]
    raw = c.get("/api/skills/weekly-report/detail").json()["raw"]
    c.put("/api/skills/weekly-report/raw", json={"raw": raw.replace("List risks", "List risks and owners")})
    assert "owners" in c.get("/api/skills/weekly-report/detail").json()["body"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("my-skill/SKILL.md", "---\nname: zipped-skill\ndescription: Use when testing zips\n---\nDo it.")
        z.writestr("my-skill/helper.py", "print(1)")
    up = c.post("/api/skills-upload", files={"file": ("skill.zip", buf.getvalue())}).json()
    assert up["name"] == "zipped-skill" and "helper.py" in up["files"]
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("SKILL.md", "---\nname: evil\ndescription: x\n---\n")
        z.writestr("../escape.txt", "x")
    assert c.post("/api/skills-upload", files={"file": ("e.zip", bad.getvalue())}).status_code == 400
    h = c.post("/api/harnesses", json={"spec": {"name": "Note taker", "type": "loop", "mode": "agent",
                                                "tools": ["files"], "max_steps": 4},
                                       "prompt": "Take notes."}).json()
    assert h["id"] == "note-taker" and h["system_prompt"] == "Take notes."
    assert c.post("/api/harnesses", json={"spec": {"name": "x", "type": "loop", "max_steps": 999}}).status_code == 400


def test_schedules(engine):
    c = engine["client"]
    p = c.post("/api/schedules/parse", json={"when": "weekdays at 9:15"}).json()
    assert p["cron"] == "15 9 * * 1-5" and p["when"] == "Weekdays at 09:15"
    assert c.post("/api/schedules/parse", json={"when": "every 5 minutes"}).status_code == 400
    s = c.post("/api/schedules", json={"name": "Morning check", "when": "every day at 07:00", "prompt": "hello",
                                       "harness": "chat"}).json()
    assert s["cron"] == "0 7 * * *" and s["next_run"]
    run = c.post(f"/api/schedules/{s['id']}/run").json()
    assert wait_job(c, run["job_id"])["status"] == "done"
    chat = c.get(f"/api/chats/{run['chat_id']}").json()
    assert chat["origin"] == "schedule" and chat["title"].startswith("Morning check")


def test_connector_stdio(engine):
    c = engine["client"]
    conn = c.post("/api/connectors", json={"name": "Test MCP", "transport": "stdio", "command": "{python}",
                                           "args": [str(TESTS / "fake_mcp_server.py")]}).json()
    tested = c.post(f"/api/connectors/{conn['id']}/test").json()
    assert tested["ok"], tested
    names = {t["name"]: t for t in tested["tools"]}
    assert names["whoami"]["readonly"] and not names["set_value"]["readonly"]
    chat = c.post("/api/chats", json={"harness": "agent", "connectors": [conn["id"]]}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "whoami please"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"][0]
    assert step["ok"] and "tester" in step["result"]


def test_plugins(engine):
    c = engine["client"]
    plugins = {p["id"]: p for p in c.get("/api/plugins").json()["plugins"]}
    dv = plugins["datavera"]
    assert dv["source"] == "user"                       # installed by the test: nothing is built in any more
    assert not list((ENGINE / "builtin" / "plugins").glob("*/plugin.json")) if (ENGINE / "builtin" / "plugins").exists() else True
    assert dv["enabled"] and not dv["configured"] and dv["connectors"][0]["url"].startswith("https://")
    c.put("/api/plugins/datavera/settings", json={"api_key": "not-a-key", "topics": "glaciers"})
    dv = {p["id"]: p for p in c.get("/api/plugins").json()["plugins"]}["datavera"]
    assert dv["values"]["api_key"] is True and dv["values"]["topics"] == "glaciers"  # secret never returned
    result = c.post("/api/plugins/datavera/setup").json()
    assert result["ok"] is False and "dv_sk_" in result["message"]
    created = c.post("/api/plugins-create", json={"name": "My kit", "description": "test",
                                                  "skills": ["weekly-report"], "harnesses": ["note-taker"]}).json()
    assert created["id"] == "my-kit"
    export = c.get("/api/plugins/my-kit/export")
    assert export.status_code == 200 and zipfile.ZipFile(io.BytesIO(export.content)).namelist()


def test_memory_api_and_secret_filter(engine):
    c = engine["client"]
    ok = c.post("/api/memory", json={"scope": "global", "text": "Works on Datavera"})
    assert ok.status_code == 200
    bad = c.post("/api/memory", json={"scope": "global", "text": "my key is dv_sk_abcdef123456"})
    assert bad.status_code == 400


def test_events_stream(engine):
    with httpx.stream("GET", f"http://127.0.0.1:{engine['port']}/api/events?token={engine['token']}&since=0",
                      timeout=10, trust_env=False) as r:
        kinds = set()
        for line in r.iter_lines():
            if line.startswith("data:"):
                kinds.add(json.loads(line[5:])["kind"])
            if {"chat", "job"} <= kinds:
                break
    assert {"chat", "job"} <= kinds


@pytest.mark.skipif(not os.environ.get("DVA_TEST_LLAMA_SERVER"), reason="real llama-server not configured")
def test_real_llama_server():
    tmp = Path(tempfile.mkdtemp(prefix="dva-real-"))
    port = free_port()
    env = {**os.environ, "DVA_TOKEN": "t", "DVA_LLAMA_SERVER": os.environ["DVA_TEST_LLAMA_SERVER"],
           "DVA_SECRETS": "file", "PYTHONPATH": str(ENGINE)}
    proc = subprocess.Popen([sys.executable, "-m", "dva", "serve", "--port", str(port), "--data-dir", str(tmp)],
                            cwd=str(ENGINE), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout.readline().startswith("DVA_READY")
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer t"}, timeout=120,
                     trust_env=False)
    try:
        m = c.post("/api/models/import", json={"path": os.environ["DVA_TEST_GGUF"]}).json()
        c.put(f"/api/models/{m['id']}", json={"compute": "cpu", "ctx": 2048})
        c.put("/api/settings", json={"max_reply_tokens": 24, "learn_enabled": False})
        status = c.post(f"/api/models/{m['id']}/load", json={}).json()
        assert status["state"] == "ready" and status["compute"] == "cpu"
        chat = c.post("/api/chats", json={"harness": "chat", "model": m["id"]}).json()
        job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "hi"}).json()
        assert wait_job(c, job["job_id"], timeout=120)["status"] == "done"
        msg = last_assistant(c, chat["id"])
        assert msg["meta"]["stats"]["output_tokens"] > 0
        bench = c.post(f"/api/models/{m['id']}/benchmark").json()
        assert bench["reply_seconds"] > 0 and "json_valid" in bench
        r = c.post("/api/runtime/unload").json()
        assert r.get("ok"), r
    finally:
        c.post("/api/shutdown")
        proc.wait(10)


def test_pipeline_harness(engine):
    c = engine["client"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("echo/harness.json", json.dumps({
            "name": "Echo pipeline", "type": "pipeline", "entry": "run.py", "args": ["go"],
            "args_map": {"topic": "--topic"}, "inputs": {"type": "object", "properties": {"topic": {"type": "string"}}}}))
        z.writestr("echo/run.py", "import json, sys\nprint('::progress ' + json.dumps({'text': 'working'}))\n"
                                  "print('line one')\nprint('::result ' + json.dumps({'summary_markdown': 'Echo: ' + ' '.join(sys.argv[1:])}))\n")
    h = c.post("/api/harnesses-upload", files={"file": ("echo.zip", buf.getvalue())}).json()
    assert h["type"] == "pipeline"
    chat = c.post("/api/chats", json={"harness": h["id"]}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "honey bees"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert msg["content"] == "Echo: go --topic honey bees"
    assert "line one" in c.get(f"/api/jobs/{job['job_id']}").json()["log"]


def test_datavera_pipeline_without_key(engine):
    c = engine["client"]
    c.put("/api/plugins/datavera/settings", json={"api_key": None})
    chat = c.post("/api/chats", json={"harness": "datavera:dataset-publisher"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "run"}).json()
    done = wait_job(c, job["job_id"], timeout=90)
    msg = last_assistant(c, chat["id"])
    assert done["status"] == "done", done
    assert "Set up the Datavera plugin first" in json.dumps(msg["meta"]["steps"])


def test_finds_models_from_other_apps(engine):
    c, tmp = engine["client"], engine["tmp"]
    # an Ollama-style store: manifest name:tag -> blob without extension
    blob = tmp / "ollama" / "blobs" / ("sha256-" + "a" * 64)
    blob.parent.mkdir(parents=True)
    tiny_gguf(blob, name="Qwen3.5 4B")
    os.truncate(blob, 60 * 2**20)
    mf = tmp / "ollama" / "manifests" / "registry.ollama.ai" / "library" / "qwen3.5" / "4b"
    mf.parent.mkdir(parents=True)
    mf.write_text(json.dumps({"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:" + "a" * 64}]}))
    # a folder the user added (LM Studio style), with a split model and a tiny vocab file that must be ignored
    lm = tmp / "lmstudio" / "lmstudio-community" / "gemma"
    lm.mkdir(parents=True)
    for name in ("gemma-3-4b-it-Q4_K_M.gguf", "big-Q8_0-00001-of-00002.gguf", "big-Q8_0-00002-of-00002.gguf", "vocab.gguf"):
        tiny_gguf(lm / name, name=name)
        if name != "vocab.gguf":
            os.truncate(lm / name, 60 * 2**20)
    c.put("/api/settings", json={"model_scan_folders": [str(tmp / "lmstudio")]})
    found = c.get("/api/models/local").json()["found"]
    names = {f["name"]: f for f in found}
    assert "qwen3.5:4b" in names and names["qwen3.5:4b"]["source"] == "Ollama"
    assert "gemma-3-4b-it-Q4_K_M" in names and "big-Q8_0-00001-of-00002" in names
    assert not any(n.startswith("big-Q8_0-00002") or "vocab" in n for n in names)
    m = c.post("/api/models/import", json={"path": names["qwen3.5:4b"]["path"], "name": "qwen3.5:4b"}).json()
    assert m["id"] == "qwen3.5-4b" and m["path"].endswith("a" * 64)
    again = c.post("/api/models/import", json={"path": names["qwen3.5:4b"]["path"], "name": "qwen3.5:4b"}).json()
    assert again["id"] == m["id"]  # no duplicates
    assert next(f for f in c.get("/api/models/local").json()["found"] if f["name"] == "qwen3.5:4b")["added_as"] == m["id"]
    c.delete(f"/api/models/{m['id']}")
    assert blob.exists()  # removing it from the app never deletes another app's file


# ---------------------------------------------------------------------- loop guard, follow-ups, teams, vision, web
def chat_messages(c, chat_id):
    return c.get(f"/api/chats/{chat_id}").json()["messages"]


def test_loop_guard_stops_a_looping_agent(engine):
    c, tmp = engine["client"], engine["tmp"]
    chat = c.post("/api/chats", json={"harness": "agent", "folders": [str(tmp / "project-files")]}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "keep looping please"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    msg = last_assistant(c, chat["id"])
    tools = [s for s in msg["meta"]["steps"] if s["type"] == "tool"]
    assert tools[0]["ok"] and all(s.get("blocked") for s in tools[1:]) and len(tools) <= 8
    assert "repeating" in msg["meta"]["guard"] or "nothing new" in msg["meta"]["guard"]
    assert any(s["type"] == "note" and "Stopped using tools" in s["text"] for s in msg["meta"]["steps"])


def test_queued_follow_up_runs_after_the_reply(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "chat"}).json()
    first = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "a slow reply please"}).json()
    time.sleep(0.6)
    queued = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "and a second thing", "mode": "queue"}).json()
    assert queued["state"] == "queue"
    assert wait_job(c, first["job_id"])["status"] == "done"
    for _ in range(100):
        msgs = chat_messages(c, chat["id"])
        if len([m for m in msgs if m["role"] == "assistant"]) == 2 and not c.get(f"/api/chats/{chat['id']}").json()["running_job"]:
            break
        time.sleep(0.2)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[2]["content"] == "and a second thing" and not msgs[2]["meta"].get("pending")


def test_steer_reaches_the_running_agent(engine):
    c, tmp = engine["client"], engine["tmp"]
    chat = c.post("/api/chats", json={"harness": "agent", "folders": [str(tmp / "project-files")]}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "list files slowly"}).json()
    time.sleep(0.5)
    r = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "only count the csv files", "mode": "steer"}).json()
    assert r["state"] == "steer"
    assert wait_job(c, job["job_id"])["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert any(s["type"] == "steer" for s in msg["meta"]["steps"])
    assert "only count the csv files" in msg["content"]
    time.sleep(0.5)
    assert len([m for m in chat_messages(c, chat["id"]) if m["role"] == "assistant"]) == 1  # not run twice


def test_stop_keeps_queued_messages_unsent(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "chat"}).json()
    first = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "slow again"}).json()
    time.sleep(0.4)
    q = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "queued then stopped"}).json()
    c.post(f"/api/chats/{chat['id']}/stop")
    assert wait_job(c, first["job_id"])["status"] == "cancelled"
    time.sleep(0.5)
    row = next(m for m in chat_messages(c, chat["id"]) if m["id"] == q["message_id"])
    assert row["meta"].get("not_sent")
    again = c.post(f"/api/chats/{chat['id']}/messages/{q['message_id']}/resend").json()
    assert again["state"] == "started" and wait_job(c, again["job_id"])["status"] == "done"


def test_team_builder_runs_each_agent(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "chat"}).json()
    team = [{"name": "Alpha", "harness": "chat", "task": "say hello", "model": "role:worker"},
            {"name": "Beta", "harness": "chat", "task": "say goodbye", "limits": {"max_reply_tokens": 300}}]
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "greet in two ways", "team": team}).json()
    assert wait_job(c, job["job_id"], timeout=90)["status"] == "done"
    full = c.get(f"/api/chats/{chat['id']}").json()
    assert {a["agent_name"] for a in full["agents"]} == {"Alpha", "Beta"}
    msg = last_assistant(c, chat["id"])
    team_step = next(s for s in msg["meta"]["steps"] if s["type"] == "orchestra")
    assert all(t["status"] == "done" and t["output"] for t in team_step["tasks"]), team_step
    assert {t["agent"] for t in team_step["tasks"]} == {"Alpha", "Beta"}
    assert all(not str(t["model"]).startswith("role:") and t["model_name"] for t in team_step["tasks"]), team_step
    beta = next(a for a in full["agents"] if a["agent_name"] == "Beta")
    assert c.get(f"/api/chats/{beta['id']}").json()["settings"]["max_reply_tokens"] == 300
    listed = {r["id"] for r in c.get("/api/chats").json()["chats"]}
    assert beta["id"] not in listed            # agents' chats stay out of the sidebar


def test_per_chat_limits_and_harnesses(engine):
    c = engine["client"]
    ids = {h["id"] for h in c.get("/api/harnesses").json()["harnesses"]}
    assert {"coder", "writer", "document-reader", "vision-analyst", "team-lead", "researcher"} <= ids
    chat = c.post("/api/chats", json={"harness": "chat"}).json()
    upd = c.put(f"/api/chats/{chat['id']}", json={"settings": {"max_reply_tokens": "512", "max_steps": 0,
                                                               "temperature": "0.1", "junk": 5}}).json()
    assert upd["settings"] == {"max_reply_tokens": 512, "temperature": 0.1}


def test_research_without_internet_says_so(engine):
    c = engine["client"]
    c.put("/api/settings", json={"browser_search": False})
    chat = c.post("/api/chats", json={"harness": "researcher"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "electricity statistics for Kenya"}).json()
    assert wait_job(c, job["job_id"], timeout=120)["status"] == "done"
    msg = last_assistant(c, chat["id"])
    kinds = [s.get("name") for s in msg["meta"]["steps"] if s["type"] == "tool"]
    assert kinds and set(kinds) <= {"web_search", "web_fetch", "extract", "download_file"}
    assert any(s["type"] == "note" and "No relevant sources" in s["text"] for s in msg["meta"]["steps"])


def test_vision_attachment_reaches_vision_model(engine):
    c, tmp = engine["client"], engine["tmp"]
    from PIL import Image
    tiny_gguf(tmp / "eye-model-Q4_K_M.gguf", name="eye-model")
    tiny_gguf(tmp / "mmproj-eye-F16.gguf", arch="clip", name="eye-proj")
    m = c.post("/api/models/import", json={"path": str(tmp / "eye-model-Q4_K_M.gguf")}).json()
    assert m["settings"].get("mmproj", "").endswith("mmproj-eye-F16.gguf")   # found next to the model
    chat = c.post("/api/chats", json={"harness": "chat", "model": m["id"]}).json()
    img = tmp / "pic.png"
    Image.new("RGB", (40, 30), (200, 50, 50)).save(img)
    att = {"name": "pic.png", "path": str(img), "size": img.stat().st_size}
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "what is this?", "attachments": [att]}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    assert "1 images" in last_assistant(c, chat["id"])["content"]
    log = (tmp / "data" / "logs" / "llama-server.log").read_text()
    assert "--mmproj" in log


def test_web_parsing_and_relevance_offline():
    sys.path.insert(0, str(ENGINE))
    import base64
    from dva.tools import web
    real = "https://www.epra.go.ke/statistics/energy-2024.pdf"
    ck = "https://www.bing.com/ck/a?!&&p=abc&u=a1" + base64.urlsafe_b64encode(real.encode()).decode().rstrip("=")
    assert web.unwrap(ck) == real
    assert web.unwrap("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa", "https://html.duckduckgo.com/html/") == "https://example.org/a"
    bing = f"""<ol><li class="b_algo"><h2><a href="{ck}">Kenya energy statistics 2024 (EPRA)</a></h2>
      <div class="b_caption"><p>Electricity generation and capacity in Kenya</p></div></li>
      <li class="b_algo"><h2><a href="https://outlook.live.com/">Outlook – free personal email</a></h2>
      <p>Sign in to Outlook</p></li></ol>"""
    rows = web.parse_bing(bing, "https://www.bing.com/search?q=x")
    terms = web.terms_of("Kenya electricity generation statistics 2024")
    kept = [r for r in rows if web.score(r, terms) > 0]
    assert [r["url"] for r in kept] == [real]
    ddg = """<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fkplc.co.ke%2Felectricity-kenya">
      Electricity in Kenya – KPLC</a><a class="result__snippet">Kenya generation mix</a></div>"""
    assert web.parse_ddg(ddg, "https://html.duckduckgo.com/html/")[0]["url"] == "https://kplc.co.ke/electricity-kenya"
    title, text, files = web.html_to_text('<html><title>T</title><body><main><h2>Data</h2><p>Table</p>'
                                          '<a href="/files/data.csv">CSV</a></main></body></html>', "https://x.org/p")
    assert "## Data" in text and files[0]["url"] == "https://x.org/files/data.csv"


def test_loop_guard_rules():
    sys.path.insert(0, str(ENGINE))
    from dva.runner import LoopGuard

    class DB:
        def setting(self, k, d=None):
            return d

    class T:
        limits, harness, db = {"max_searches": 3}, {}, DB()
    g = LoopGuard(T())
    assert g.check("web_search", {"query": "kenya electricity generation 2024"}) is None
    g.record("web_search", {"query": "kenya electricity generation 2024"}, True, "results A")
    assert "nearly the same" in g.check("web_search", {"query": "electricity generation kenya 2024 data"})
    assert "already called" in g.check("web_search", {"query": "kenya electricity generation 2024"})
    for q in ("solar farms", "wind power turbines"):
        g.record("web_search", {"query": q}, True, "NO RELEVANT RESULTS")
    assert "limit" in g.check("web_search", {"query": "geothermal output olkaria"})
    g.record("read_file", {"path": "a"}, False, "Error: x")
    assert g.nudge() and g.should_stop() is None
    for i, error in enumerate(("Error: no such file", "Error: it is a folder")):
        g.record("read_file", {"path": str(i)}, False, error)
    assert g.should_stop() is None
    g.record("read_file", {"path": "z"}, False, "Error: the disk is full")
    assert "nothing new" in g.should_stop()                       # six results in a row that brought nothing


def test_scripts_can_import_their_neighbours_like_the_installed_app(tmp_path):
    """The installer's embedded Python leaves a script's folder off sys.path (python._pth). Emulated with -P:
    a plain run fails, the engine's launcher works."""
    folder = tmp_path / "plugin"
    folder.mkdir()
    (folder / "helper.py").write_text("VALUE = 42\n")
    (folder / "main.py").write_text("import sys\nfrom helper import VALUE\nprint('ok', VALUE, sys.argv[1:])\n")
    env = {**os.environ, "PYTHONPATH": str(ENGINE)}
    plain = subprocess.run([sys.executable, "-P", str(folder / "main.py")], capture_output=True, text=True,
                           cwd=str(tmp_path), env=env)
    assert "No module named 'helper'" in plain.stderr
    launched = subprocess.run([sys.executable, "-P", "-m", "dva.run_script", str(folder / "main.py"), "x"],
                              capture_output=True, text=True, cwd=str(tmp_path), env=env)
    assert launched.stdout.strip() == "ok 42 ['x']", launched.stderr


# ---------------------------------------------------------------- 2.2.0: KimiLM rename, restart and guard fixes
def test_loop_guard_allows_rereading_after_a_change():
    sys.path.insert(0, str(ENGINE))
    from dva.runner import LoopGuard

    class DB:
        def setting(self, k, d=None):
            return d

    class T:
        limits, harness, db = {}, {}, DB()
    g = LoopGuard(T())
    g.record("read_file", {"path": "app.py"}, True, "v1")
    g.record("web_fetch", {"url": "https://x.org"}, True, "page")
    assert "already called" in g.check("read_file", {"path": "app.py"})
    g.record("edit_file", {"path": "app.py", "old": "a", "new": "b"}, True, "edited", changed=True)
    assert g.check("read_file", {"path": "app.py"}) is None, "a file that just changed can be read again"
    assert "already called" in g.check("web_fetch", {"url": "https://x.org"}), "web pages don't change by editing"
    assert "already called" in g.check("edit_file", {"path": "app.py", "old": "a", "new": "b"})
    g.record("run_python", {"code": "x"}, False, "Error: boom", changed=False)
    assert "already called" in g.check("run_python", {"code": "x"}), "a failed run changes nothing"


def _legacy_data_folder(root):
    """A data folder as Datavera Agent left it, after the shell moved it to KimiLM's folder."""
    import sqlite3
    old = root / "Datavera Agent"
    new = root / "KimiLM"
    new.mkdir(parents=True)
    con = sqlite3.connect(str(new / "datavera-agent.sqlite3"))
    con.executescript("CREATE TABLE models (id TEXT PRIMARY KEY, name TEXT, path TEXT, files_json TEXT);"
                      "CREATE TABLE messages (id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, "
                      "content TEXT DEFAULT '', meta_json TEXT DEFAULT '{}', created TEXT);")
    model_path = str(old / "models" / "qwen.gguf")
    con.execute("INSERT INTO models VALUES ('m1', 'Qwen', ?, ?)", (model_path, json.dumps([model_path])))
    con.execute("INSERT INTO messages VALUES ('u1', 'c1', 'user', 'later please', ?, '2026-11-15T08:00:00.000+00:00')",
                (json.dumps({"attachments": [], "pending": "queue"}),))
    con.execute("INSERT INTO messages VALUES ('u2', 'c1', 'user', 'see file', ?, '2026-11-15T08:00:01.000+00:00')",
                (json.dumps({"attachments": [{"name": "a.csv", "path": str(old / "uploads" / "a.csv")}]}),))
    con.commit()
    con.close()
    (new / "migrated-from.json").write_text(json.dumps({"from": str(old), "to": str(new), "paths_rewritten": False}))
    return old, new


def test_data_moved_from_datavera_agent_is_adopted(tmp_path, monkeypatch):
    sys.path.insert(0, str(ENGINE))
    monkeypatch.setenv("DVA_SECRETS", "file")
    from dva.app_state import App
    old, new = _legacy_data_folder(tmp_path)
    app = App(str(new))
    assert (new / "kimilm.sqlite3").exists() and not (new / "datavera-agent.sqlite3").exists()
    model = app.db.get("models", "m1")
    assert model["path"] == str(new.resolve() / "models" / "qwen.gguf")
    assert model["files"] == [str(new.resolve() / "models" / "qwen.gguf")], "paths inside JSON are rewritten too"
    att = app.db.get("messages", "u2")["meta"]["attachments"][0]["path"]
    assert att == str(new.resolve() / "uploads" / "a.csv")
    queued = app.db.get("messages", "u1")["meta"]
    assert queued.get("not_sent") == "restart" and "pending" not in queued, "a queued message is released"
    note = json.loads((new / "migrated-from.json").read_text())
    assert note["paths_rewritten"] is True and note["rows_changed"] >= 3
    app.db.conn.close()
    again = App(str(new))                       # second start: nothing to do, nothing breaks
    assert again.db.get("models", "m1")["path"] == model["path"]
    again.db.conn.close()


def test_keys_saved_under_the_old_name_are_found(monkeypatch):
    sys.path.insert(0, str(ENGINE))
    import keyring
    from keyring.backend import KeyringBackend
    from dva import secrets as sec
    monkeypatch.delenv("DVA_SECRETS", raising=False)

    class Memory(KeyringBackend):
        priority = 1
        store = {}

        def get_password(self, service, user):
            return self.store.get((service, user))

        def set_password(self, service, user, password):
            self.store[(service, user)] = password

        def delete_password(self, service, user):
            self.store.pop((service, user), None)

    before = keyring.get_keyring()
    keyring.set_keyring(Memory())
    try:
        Memory.store[("DataveraAgent", "plugin:datavera:api_key")] = "dv_sk_old"
        s = sec.Secrets(tempfile.mkdtemp())
        assert s.secure
        assert s.get("plugin:datavera:api_key") == "dv_sk_old"
        assert Memory.store[("KimiLM", "plugin:datavera:api_key")] == "dv_sk_old", "copied to the new name"
        assert Memory.store[("DataveraAgent", "plugin:datavera:api_key")] == "dv_sk_old", "old entry kept"
        s.delete("plugin:datavera:api_key")
        assert not s.has("plugin:datavera:api_key"), "deleting removes both"
    finally:
        keyring.set_keyring(before)


# ---------------------------------------------------------------- 2.4.0: the planner runs agents as a task graph
def team_of(msg):
    return next(s for s in msg["meta"]["steps"] if s["type"] == "orchestra")


def first_user_text(c, chat_id):
    return next(m for m in c.get(f"/api/chats/{chat_id}").json()["messages"] if m["role"] == "user")["content"]


def test_planner_runs_a_task_graph(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={
        "text": "As a team: collect some greetings, then summarise them for me in one line."}).json()
    assert wait_job(c, job["job_id"], timeout=120, approve="allow")["status"] == "done"
    team = team_of(last_assistant(c, chat["id"]))
    tasks = {t["id"]: t for t in team["tasks"]}
    assert set(tasks) == {"t1", "t2"} and all(t["status"] == "done" for t in tasks.values()), team
    assert tasks["t2"]["depends_on"] == ["t1"]
    brief = first_user_text(c, tasks["t2"]["chat_id"])
    assert "Results you build on" in brief and "### t1 Collect" in brief      # t2 started with t1's result
    assert "team" in brief.lower() and team["folder"] in brief                 # and the shared team folder
    assert any("Planned 2 tasks" in line["text"] for line in team["log"])
    assert c.get(f"/api/chats/{tasks['t1']['chat_id']}").json()["parent_chat_id"] == chat["id"]


def test_planner_sends_work_back_to_the_same_agent(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={
        "text": "As a team (retry-me): collect some greetings, then summarise them in one line."}).json()
    assert wait_job(c, job["job_id"], timeout=120, approve="allow")["status"] == "done"
    t1 = next(t for t in team_of(last_assistant(c, chat["id"]))["tasks"] if t["id"] == "t1")
    assert t1["attempts"] == 2 and t1["status"] == "done"
    users = [m["content"] for m in c.get(f"/api/chats/{t1['chat_id']}").json()["messages"] if m["role"] == "user"]
    assert len(users) == 2 and users[1].startswith("The planner reviewed your result")   # same chat, same context


def test_agents_can_ask_the_planner(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={
        "text": "As a team, ask the planner when unsure: collect greetings, then summarise them."}).json()
    assert wait_job(c, job["job_id"], timeout=120, approve="allow")["status"] == "done"
    t1 = next(t for t in team_of(last_assistant(c, chat["id"]))["tasks"] if t["id"] == "t1")
    assert "use English" in t1["output"], t1


def test_planner_keeps_simple_work_with_one_agent(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    text = ("Please collect the greetings from the notes, analyse which ones sound friendliest, and write a short "
            "report that compares them.")
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": text}).json()
    assert wait_job(c, job["job_id"], timeout=60, approve="allow")["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert not any(s["type"] == "orchestra" for s in msg["meta"]["steps"])
    assert c.get(f"/api/chats/{chat['id']}").json()["agents"] == []
    c.put("/api/settings", json={"team_mode": "off"})
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "As a team: collect greetings, then summarise."}).json()
    assert wait_job(c, job["job_id"], timeout=60, approve="allow")["status"] == "done"
    assert not any(s["type"] == "orchestra" for s in last_assistant(c, chat["id"])["meta"]["steps"])
    c.put("/api/settings", json={"team_mode": "auto"})


def test_pause_and_resume_an_agent(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    c.put(f"/api/chats/{chat['id']}", json={"auto_approve": True})
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "slowly list files please"}).json()
    time.sleep(0.5)
    assert c.post(f"/api/chats/{chat['id']}/pause", json={"reason": "checking something"}).json()["paused"]
    time.sleep(2.5)
    assert c.get(f"/api/jobs/{job['job_id']}").json()["status"] == "running"
    assert c.get(f"/api/chats/{chat['id']}").json()["paused"]
    c.post(f"/api/chats/{chat['id']}/resume")
    assert wait_job(c, job["job_id"], timeout=60)["status"] == "done"
    notes = [s["text"] for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "note"]
    assert "Paused: checking something." in notes and "Resumed." in notes


def test_code_errors_point_at_the_line_and_commands_run(tmp_path):
    import asyncio
    sys.path.insert(0, str(ENGINE))
    import types
    from dva.procs import Procs
    from dva.tools import python_exec
    from dva.tools import ToolContext

    class DB:
        def setting(self, key, default=None):
            return default

    class App:
        db = DB()
        paths = types.SimpleNamespace(data=tmp_path)
        log = print
    App.procs = Procs(App)                  # 2.19.0: programs run through the app's list of programs
    ctx = ToolContext(app=App(), chat={"id": "c"}, project=None, workspace=tmp_path, folders=[str(tmp_path)],
                      readonly=[], emit=lambda *a, **k: None, approve=None)
    out = asyncio.run(python_exec.run_python(ctx, "x = 1\ny = 2\nprint(x / (y - 2))\n"))
    assert out.startswith("FAILED") and ">>    3  print(x / (y - 2))" in out and "ZeroDivisionError" in out
    out = asyncio.run(python_exec.run_python(ctx, "import not_a_real_module\n"))
    assert "not installed in the app's Python" in out
    cmd = "Write-Output hello" if os.name == "nt" else "echo hello && echo made > made.txt"
    out = asyncio.run(python_exec.run_command(ctx, cmd))
    assert out.startswith("exit code 0") and "hello" in out
    if os.name != "nt":
        assert "made.txt" in out
    assert asyncio.run(python_exec.run_command(ctx, "exit 3")).startswith("FAILED (exit code 3)")


# ---------------------------------------------------------------- 2.4.0: memory — past chats, knowledge, notes, isolation
def say(c, chat_id, text, timeout=60):
    job = c.post(f"/api/chats/{chat_id}/messages", json={"text": text}).json()
    assert wait_job(c, job["job_id"], timeout=timeout, approve="allow")["status"] == "done"
    return last_assistant(c, chat_id)


def test_past_chats_are_searchable_with_isolated_projects_kept_apart(engine):
    c = engine["client"]
    old = c.post("/api/chats", json={"harness": "chat"}).json()
    say(c, old["id"], "Our solar tender in Kisumu closes on the last Friday of March.")
    secret = c.post("/api/projects", json={"name": "Private work"}).json()
    c.put(f"/api/projects/{secret['id']}", json={"memory_mode": "isolated"})
    inside = c.post("/api/chats", json={"harness": "chat", "project_id": secret["id"]}).json()
    say(c, inside["id"], "The Zanzibar expansion plan stays confidential until June.")
    # the sidebar search (the user's own) sees everything
    found = c.get("/api/search", params={"q": "Kisumu tender"}).json()["results"]
    assert found and found[0]["id"] == old["id"] and "Kisumu" in found[0]["matches"][0]["snippet"]
    assert c.get("/api/search", params={"q": "Zanzibar"}).json()["results"][0]["id"] == inside["id"]
    # an agent outside the isolated project finds the Kisumu chat but not the Zanzibar one
    agent = c.post("/api/chats", json={"harness": "agent"}).json()
    msg = say(c, agent["id"], "search earlier chats for Kisumu tender")
    assert old["id"] in msg["content"]
    msg = say(c, agent["id"], "search earlier chats for Zanzibar expansion")
    assert "No earlier chat matches" in msg["content"]
    # inside the isolated project it is found, and the global memory is not in the prompt
    c.post("/api/memory", json={"scope": "global", "text": "Global note that must stay outside isolated projects"})
    inner = c.post("/api/chats", json={"harness": "agent", "project_id": secret["id"]}).json()
    assert inside["id"] in say(c, inner["id"], "search earlier chats for Zanzibar expansion")["content"]
    prompt = say(c, inner["id"], "show system prompt")["content"]
    assert "Global note that must stay outside" not in prompt and "About the user" not in prompt


def test_project_knowledge_files_reach_the_prompt_and_search(engine):
    c = engine["client"]
    prj = c.post("/api/projects", json={"name": "Plants"}).json()
    body = ("Annual report\n\nThe capacity factor of the Garissa solar plant was 23.4 percent in 2024.\n\n" +
            "\n\n".join(f"Section {i}: maintenance schedules, staffing and procurement notes." for i in range(30)))
    files = {"file": ("plants.md", body.encode(), "text/markdown")}
    doc = c.post(f"/api/projects/{prj['id']}/knowledge", files=files).json()
    for _ in range(50):
        doc = next(d for d in c.get(f"/api/projects/{prj['id']}/knowledge").json()["docs"] if d["id"] == doc["id"])
        if doc["status"] != "indexing":
            break
        time.sleep(0.1)
    assert doc["status"] == "ready" and doc["chunks"] >= 2, doc
    chat = c.post("/api/chats", json={"harness": "agent", "project_id": prj["id"]}).json()
    prompt = say(c, chat["id"], "show system prompt: what was the capacity factor of the Garissa plant?")["content"]
    assert "[plants.md]" in prompt and "23.4 percent" in prompt
    found = say(c, chat["id"], "search knowledge for Garissa capacity factor")["content"]
    assert "[plants.md]" in found and "Garissa" in found
    bad = c.post(f"/api/projects/{prj['id']}/knowledge", files={"file": ("x.exe", b"MZ\0\0", "application/octet-stream")})
    assert bad.status_code == 400
    c.delete(f"/api/knowledge/{doc['id']}")
    assert c.get(f"/api/projects/{prj['id']}/knowledge").json()["docs"] == []


def test_project_notes_and_relevant_memory(engine):
    c = engine["client"]
    prj = c.post("/api/projects", json={"name": "Notes project"}).json()
    c.put(f"/api/projects/{prj['id']}", json={"notes": "## Status\n- Kick-off done\n"})
    chat = c.post("/api/chats", json={"harness": "agent", "project_id": prj["id"]}).json()
    say(c, chat["id"], "note decision: we use PostgreSQL for the field data")
    notes = c.get(f"/api/projects/{prj['id']}").json()["notes"]
    assert "## Decisions" in notes and "we use PostgreSQL for the field data" in notes and "Kick-off done" in notes
    assert "PostgreSQL" in say(c, chat["id"], "show system prompt")["content"]
    # many notes: the relevant one still reaches the prompt even though it is the oldest
    c.post("/api/memory", json={"scope": "project", "scope_id": prj["id"], "text": "Writes Kotlin coroutines for the field app"})
    topics = ["gardening", "astronomy", "cycling", "chess", "pottery", "sailing", "baking", "violin", "hiking",
              "birdwatching", "origami", "climbing", "fencing", "rowing", "knitting", "surfing", "archery", "skiing",
              "judo", "calligraphy", "beekeeping", "kayaking", "juggling", "woodwork", "tennis", "rugby"]
    for t in topics:
        c.post("/api/memory", json={"scope": "project", "scope_id": prj["id"], "text": f"Enjoys {t} on most {t} weekends"})
    prompt = say(c, chat["id"], "show system prompt: how should I structure kotlin coroutines here?")["content"]
    assert "Kotlin coroutines" in prompt


def test_search_by_meaning_with_the_search_model(engine):
    c = engine["client"]
    model = engine["tmp"] / "embed.gguf"
    tiny_gguf(model, name="qwen3-embedding")
    c.put("/api/settings", json={"embedding_model_path": str(model)})
    status = c.get("/api/search/status").json()
    assert status["model"]["path"] == str(model)
    prj = c.post("/api/projects", json={"name": "Vectors"}).json()
    files = {"file": ("grid.txt", b"Mini-grids in rural Turkana reach 4,000 households.\n\nTariffs are flat.", "text/plain")}
    doc = c.post(f"/api/projects/{prj['id']}/knowledge", files=files).json()
    for _ in range(100):
        stats = c.get("/api/search/status").json()["index"]
        if stats["passages_with_meaning"] >= 1:
            break
        time.sleep(0.2)
    assert stats["passages_with_meaning"] >= 1, stats
    chat = c.post("/api/chats", json={"harness": "agent", "project_id": prj["id"]}).json()
    assert "Turkana" in say(c, chat["id"], "search knowledge for households turkana minigrids")["content"]
    assert c.get("/api/search/status").json()["model"]["running"]
    c.put("/api/settings", json={"embedding_model_path": ""})
    c.delete(f"/api/knowledge/{doc['id']}")


# ---------------------------------------------------------------- 2.4.1: GPU runtime choice and CPU/GPU placement
def _runtime_with(backends, devices, settings=None):
    sys.path.insert(0, str(ENGINE))
    from dva.runtime import Runtime

    class DB:
        def setting(self, key, default=None):
            return (settings or {}).get(key, default)
    rt = Runtime.__new__(Runtime)
    rt.db, rt.api_key, rt._recommended, rt._devices_cache = DB(), "k", "cpu", {}
    rt.backends = lambda: {b: Path(f"/x/{b}/llama-server") for b in backends}
    rt.list_devices = lambda b: devices.get(b, [])
    return rt


def test_gpu_runtime_without_a_card_is_skipped_and_parts_can_be_placed():
    igpu = {"id": "Vulkan0", "name": "Intel(R) UHD Graphics", "total_mb": 16288, "free_mb": 16000}
    rt = _runtime_with(["cpu", "cuda", "vulkan"], {"cuda": [], "vulkan": [igpu]})
    assert rt.gpu_backends() == ["vulkan"]          # CUDA found no NVIDIA card: never picked
    model = {"id": "m", "path": "/m.gguf", "settings": {
        "compute": "gpu", "gpu_layers": 20, "kv_on": "cpu", "moe_cpu": 10, "mmproj_on": "cpu",
        "gpu_device": "Vulkan0", "mmproj": __file__}}
    plan = rt.plan(model)
    assert plan["backend"] == "vulkan" and plan["compute"] == "gpu" and plan["gpu_layers"] == 20
    cmd = rt._command(model, plan, 1234)
    joined = " ".join(cmd)
    assert "-ngl 20" in joined and "-dev Vulkan0" in joined and "--no-kv-offload" in cmd
    assert "--n-cpu-moe 10" in joined and "--no-mmproj-offload" in cmd
    model["settings"].update(moe_cpu="all", kv_on="gpu", gpu_layers="all")
    cmd = rt._command(model, rt.plan(model), 1234)
    assert "--cpu-moe" in cmd and "--no-kv-offload" not in cmd and "-ngl all" in " ".join(cmd)
    # only CUDA installed on a computer without an NVIDIA card: runs on the CPU and says why
    rt = _runtime_with(["cpu", "cuda"], {"cuda": []})
    plan = rt.plan({"id": "m", "path": "/m.gguf", "settings": {"compute": "gpu"}})
    assert plan["compute"] == "cpu" and plan["backend"] == "cpu" and "no graphics card" in plan["note"]
    # a real graphics card comes before integrated graphics
    rt = _runtime_with(["cpu", "vulkan", "cuda"], {"vulkan": [igpu], "cuda": [
        {"id": "CUDA0", "name": "NVIDIA GeForce RTX 3060", "total_mb": 12288, "free_mb": 11000}]})
    assert rt.gpu_backends()[0] == "cuda"


def test_hybrid_models_need_less_context_memory():
    sys.path.insert(0, str(ENGINE))
    from dva.gguf import kv_cache_bytes
    meta = {"block_count": 32, "head_count": 16, "head_count_kv": 4, "key_length": 256, "value_length": 256}
    full = kv_cache_bytes(meta, 65536)
    assert kv_cache_bytes({**meta, "full_attention_interval": 4}, 65536) == full // 4
    assert kv_cache_bytes({**meta, "head_count_kv": [0, 0, 0, 4] * 8}, 65536) == full // 4


def test_find_the_fastest_setup(engine):
    c = engine["client"]
    model_id = c.get("/api/models").json()["models"][0]["id"]
    c.put(f"/api/models/{model_id}", json={"compute": "gpu", "gpu_layers": 12})
    c.post(f"/api/models/{model_id}/load", json={})
    loaded = next(l for l in c.get("/api/models").json()["loaded"] if l["model_id"] == model_id)
    assert loaded["placement"]["gpu_layers"] == 12 and loaded["placement"]["layers"] == 33
    assert loaded["placement"]["devices"]["CPU"]["model_mb"] == 215 and loaded["placement"]["devices"]["Vulkan0"]["kv_mb"] == 64
    c.post("/api/runtime/unload", json={"model_id": model_id})
    assert c.post(f"/api/models/{model_id}/tune").json()["state"] == "running"
    for _ in range(600):
        st = c.get(f"/api/models/{model_id}/tune").json()
        if st["state"] != "running":
            break
        time.sleep(0.2)
    assert st["state"] == "done", (st.get("error"), [(r["label"], r.get("error")) for r in st["results"]])
    labels = [r["label"] for r in st["results"]]
    assert labels[:2] == ["CPU only", "Everything on the GPU"] and all(not r.get("error") for r in st["results"]), st
    # the stand-in is fastest on the CPU with 6 threads, and twice as fast on repeated text with ngram drafting
    assert st["best"] == "CPU only" and st["suggested"]["threads"] == 6 and st["suggested"]["spec"] == "ngram", st
    assert {"Threads", "Speed-ups", "Follow-ups"} <= {r["stage"] for r in st["results"]}
    assert st["reuse"]["percent"] >= 50, st["reuse"]                  # the follow-up came mostly from the cache
    model = next(m for m in c.get("/api/models").json()["models"] if m["id"] == model_id)
    assert model["settings"]["compute"] == "gpu"                      # measuring never changes settings by itself
    sug = model["suggested"]
    assert sug["measured"] and sug["settings"]["compute"] == "cpu" and "compute" in sug["differs"]
    assert sug["settings"]["threads_batch"] == 12 and sug["settings"]["priority"] == "medium"
    # Use suggested configuration, then adjust one thing by hand
    applied = c.post(f"/api/models/{model_id}/suggested").json()
    assert applied["differs"] == [], applied
    c.put(f"/api/models/{model_id}", json={"threads": 5})
    assert c.get(f"/api/models/{model_id}/suggested").json()["differs"] == ["threads"]
    c.post(f"/api/models/{model_id}/load", json={})
    log = (engine["tmp"] / "data" / "logs" / "llama-server.log").read_text(errors="replace")
    cmd = log.rsplit("\n==== ", 1)[1].split("\n", 1)[0]
    for flag in ("--spec-type ngram-mod", "-tb 12", "--prio 1", "-fa on", "-t 5", "--device none"):
        assert flag in cmd, (flag, cmd)
    c.post("/api/runtime/unload", json={"model_id": model_id})
    c.put(f"/api/models/{model_id}", json={"compute": "auto", "gpu_layers": None, "spec": None, "threads": None,
                                            "threads_batch": None, "priority": None, "flash_attn": None,
                                            "kv_type": None})


def test_follow_up_turns_reuse_the_cached_prompt(engine):
    """Memory and knowledge chosen per request go on the latest message, so the system prompt and the tool
    list are identical between turns and the model reuses them instead of re-reading them."""
    c = engine["client"]
    c.post("/api/memory", json={"scope": "global", "text": "Prefers answers with numbers"})
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    say(c, chat["id"], "show system prompt: tell me about the solar tender numbers")
    second = say(c, chat["id"], "show system prompt: and the battery sizes?")
    stats = second["meta"]["stats"]
    assert stats["cached_tokens"] > stats["prompt_tokens"], stats
    shown = second["content"]
    system, user = shown.split(" || ", 1)
    assert "Prefers answers with numbers" not in system and "Prefers answers with numbers" in user


# ---------------------------------------------------------------- 2.5.0: sequenced, looped runs
def model_id(c):
    data = c.get("/api/models").json()
    rows = data["models"] if isinstance(data, dict) else data
    return c.get("/api/bootstrap").json()["settings"].get("default_model") or rows[0]["id"]


def loaded_models(c):
    return c.get("/api/runtime").json()["loaded"]


def run_team(c, text, timeout=150, approve="allow", **chat):
    made = c.post("/api/chats", json={"harness": "agent", **chat}).json()
    job = c.post(f"/api/chats/{made['id']}/messages", json={"text": text}).json()
    status = wait_job(c, job["job_id"], timeout=timeout, approve=approve)["status"]
    return made, status, last_assistant(c, made["id"])


def test_steps_run_in_order_with_links_the_plan_forgot(engine):
    """A small planner leaves depends_on out. Code finds the links (t2 names t1; t3 follows t2), runs one step
    at a time, and each agent starts only after the result it needs was accepted."""
    c = engine["client"]
    chat, status, msg = run_team(c, "As a team with a forgetful plan: find greetings, summarise them, publish.")
    assert status == "done"
    team = team_of(msg)
    tasks = {t["id"]: t for t in team["tasks"]}
    assert team["mode"] == "auto" and list(tasks) == ["t1", "t2", "t3"]     # 2.31.0: side by side by default
    assert tasks["t2"]["depends_on"] == ["t1"] and tasks["t3"]["depends_on"] == ["t2"], tasks
    assert all(t["status"] == "done" for t in tasks.values()), team
    assert any("links between steps" in line["text"] or "link between steps" in line["text"] for line in team["log"])
    ends = {}
    for tid, t in tasks.items():
        rows = c.get(f"/api/chats/{t['chat_id']}").json()["messages"]
        ends[tid] = (rows[0]["created"], rows[-1]["created"])
    assert ends["t1"][1] <= ends["t2"][0] and ends["t2"][1] <= ends["t3"][0], ends      # never two at once
    assert "### t1 Find" in first_user_text(c, tasks["t2"]["chat_id"])
    assert "### t2 Summarise" in first_user_text(c, tasks["t3"]["chat_id"])
    assert "never rebuild it from memory" in first_user_text(c, tasks["t3"]["chat_id"])
    # every accepted result is a file the next steps (and you) can read
    assert all(t["result_file"] and Path(t["result_file"]).exists() for t in tasks.values())


def test_independent_parts_run_at_the_same_time_and_the_step_that_needs_both_waits(engine):
    """2.31.0: the planner names the parts that do not need each other; they run together on reply slots of the
    one loaded model, and the step that combines them starts when both are done."""
    c = engine["client"]
    chat, status, msg = run_team(c, "As a team in parts: hellos and goodbyes, then combine them.")
    assert status == "done", msg
    team = team_of(msg)
    tasks = {t["id"]: t for t in team["tasks"]}
    assert team["mode"] == "auto" and tasks["t1"]["depends_on"] == [] and tasks["t2"]["depends_on"] == []
    assert sorted(tasks["t3"]["depends_on"]) == ["t1", "t2"]
    span = {}
    for tid, t in tasks.items():
        rows = c.get(f"/api/chats/{t['chat_id']}").json()["messages"]
        span[tid] = (rows[0]["created"], rows[-1]["created"])
    assert span["t1"][0] < span["t2"][1] and span["t2"][0] < span["t1"][1], span     # they overlapped
    assert span["t3"][0] >= max(span["t1"][1], span["t2"][1]), span
    log = " ".join(line["text"] for line in team["log"])
    assert "at the same time" in log and "agents at a time" in log, log


def test_a_step_can_be_a_team_with_a_planner_of_its_own(engine):
    """2.31.0: a planned step that is several pieces of work goes to a sub-planner, which runs agents of its own
    (here one of them a team again, up to the levels allowed) and hands back one result."""
    c = engine["client"]
    chat, status, msg = run_team(c, "As a team with a sub-team: gather greetings in parts, then summarise.",
                                 timeout=240)
    assert status == "done", msg
    tasks = {t["id"]: t for t in team_of(msg)["tasks"]}
    assert tasks["t1"]["team"] and tasks["t1"]["status"] == "done" and not tasks["t2"].get("team")
    sub_msg = last_assistant(c, tasks["t1"]["chat_id"])
    sub = team_of(sub_msg)                                       # the sub-planner's own plan, in its chat
    sub_tasks = {t["id"]: t for t in sub["tasks"]}
    assert set(sub_tasks) == {"t1", "t2"} and all(t["status"] == "done" for t in sub_tasks.values())
    assert sub_tasks["t1"]["team"]                               # level 2 of 2: allowed
    micro = team_of(last_assistant(c, sub_tasks["t1"]["chat_id"]))
    assert not any(t.get("team") for t in micro["tasks"])       # level 3: not allowed, plain steps


def test_a_failed_step_stops_the_steps_that_need_it(engine):
    """The 16 November run: a later agent worked without its input and invented it. Now it does not start."""
    c = engine["client"]
    chat, status, msg = run_team(c, "As a team with a forgetful plan (explode-first): find, summarise, publish.")
    assert status == "done"                      # the run ends with an honest answer, not a crash
    tasks = {t["id"]: t for t in team_of(msg)["tasks"]}
    assert tasks["t1"]["status"] == "failed" and tasks["t1"]["attempts"] == 3, tasks["t1"]
    assert tasks["t2"]["status"] == "cancelled" and not tasks["t2"]["chat_id"], tasks["t2"]   # never started
    assert tasks["t3"]["status"] == "cancelled" and not tasks["t3"]["chat_id"], tasks["t3"]
    assert "could not start" in tasks["t3"]["note"]
    assert msg["content"]


def test_a_step_loops_until_it_passes_or_keeps_its_best_attempt(engine):
    c = engine["client"]
    _, status, msg = run_team(c, "As a team (never-pass): collect some greetings, then summarise them in one line.")
    assert status == "done"
    team = team_of(msg)
    tasks = {t["id"]: t for t in team["tasks"]}
    t1 = tasks["t1"]
    assert t1["status"] == "done" and t1["partial"] and t1["attempts"] == t1["rounds"] == 3, t1
    assert "best of 3 rounds" in t1["note"]
    assert any("fresh agent" in line["text"] for line in team["log"]), team["log"]    # same objection twice
    brief = first_user_text(c, tasks["t2"]["chat_id"])
    assert "(incomplete:" in brief                                  # the next step is told, not misled
    assert tasks["t2"]["status"] == "done"


def test_a_run_loads_its_model_sized_for_its_steps_and_learns(engine):
    """The model is set to 32,768 tokens for 4 replies at once (131,072 tokens of context memory). A run needs a
    fraction of that: it loads one reply slot with the context its steps need, and says so."""
    c = engine["client"]
    mid = model_id(c)
    c.put(f"/api/models/{mid}", json={"ctx": 32768, "parallel": 4})
    try:
        c.post("/api/runtime/unload", json={})
        _, status, msg = run_team(c, "As a team: collect some greetings, then summarise them for me in one line.")
        assert status == "done"
        team = team_of(msg)
        now_loaded = next(m for m in loaded_models(c) if m["model_id"] == mid)
        assert now_loaded["parallel"] == 1 and now_loaded["ctx"] < 32768, now_loaded
        assert any(line["text"].startswith("Forecast: this run needs") for line in team["log"]), team["log"]
        for t in team["tasks"]:
            assert t["forecast"]["tokens"] > 0 and t["forecast"]["ctx"] > 0 and t["used"]["tokens"] > 0, t
        assert any(m["id"] == mid and m["loaded"] for m in team["models"])
        # a short chat right after the run uses the model as the run loaded it (no reload to the full setting)
        small = c.post("/api/chats", json={"harness": "chat"}).json()
        say(c, small["id"], "hello there")
        again = next(m for m in loaded_models(c) if m["model_id"] == mid)
        assert (again["ctx"], again["parallel"]) == (now_loaded["ctx"], 1), again
        # the second run forecasts from what the first one measured
        _, status, msg = run_team(c, "As a team: collect some greetings, then summarise them for me in one line.")
        assert status == "done" and all(t["forecast"]["learned"] for t in team_of(msg)["tasks"])
    finally:
        c.put(f"/api/models/{mid}", json={"ctx": 8192, "parallel": 1})
        c.post("/api/runtime/unload", json={})


def test_steps_can_run_side_by_side_when_allowed(engine):
    c = engine["client"]
    c.put("/api/settings", json={"agent_order": "parallel"})
    try:
        chat = c.post("/api/chats", json={"harness": "chat"}).json()
        team = [{"name": "Alpha", "harness": "chat", "task": "say hello"},
                {"name": "Beta", "harness": "chat", "task": "say goodbye"}]
        job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "greet in two ways", "team": team}).json()
        assert wait_job(c, job["job_id"], timeout=90)["status"] == "done"
        step = team_of(last_assistant(c, chat["id"]))
        assert step["mode"] == "parallel" and all(t["status"] == "done" for t in step["tasks"]), step
    finally:
        c.put("/api/settings", json={"agent_order": "sequence"})


def test_models_are_unloaded_when_a_run_no_longer_needs_them(engine):
    c = engine["client"]
    c.put("/api/settings", json={"unload_after_run": True})
    try:
        _, status, _ = run_team(c, "As a team: collect some greetings, then summarise them for me in one line.")
        assert status == "done"
        for _ in range(30):
            if not loaded_models(c):
                break
            time.sleep(0.2)
        assert loaded_models(c) == []
    finally:
        c.put("/api/settings", json={"unload_after_run": False})


def test_a_hung_model_is_restarted_and_the_reply_still_comes(engine):
    c = engine["client"]
    c.put("/api/settings", json={"first_token_timeout_seconds": 3})
    try:
        chat = c.post("/api/chats", json={"harness": "chat"}).json()
        msg = say(c, chat["id"], "hang-once, then say hello", timeout=90)
        notes = [s["text"] for s in msg["meta"]["steps"] if s["type"] == "note"]
        assert any("stopped responding" in n and "Restarting" in n for n in notes), msg["meta"]["steps"]
        assert "Hello from the fake model" in msg["content"] and not msg["meta"]["error"]
    finally:
        c.put("/api/settings", json={"first_token_timeout_seconds": 0})


def test_a_plan_can_wait_for_approval(engine):
    c = engine["client"]
    c.put("/api/settings", json={"plan_approval": True})
    try:
        _, status, msg = run_team(c, "As a team: collect some greetings, then summarise them.", approve="deny")
        errors = [s["text"] for s in msg["meta"]["steps"] if s["type"] == "error"]
        assert any("declined the plan" in e for e in errors), msg["meta"]["steps"]
        assert all(t["status"] == "cancelled" or not t["chat_id"] for t in team_of(msg)["tasks"])
    finally:
        c.put("/api/settings", json={"plan_approval": False})


def test_plan_repair_and_failure_kinds():
    """The pure parts: which failure a step had, and whether feedback repeats itself."""
    from dva.orchestrator import RunFit, same_objection
    from dva.recovery import classify
    assert classify("TurnStopped: The model stopped responding twice in a row") == "stall"
    assert classify(None, timed_out=True) == "timeout"
    assert classify("RuntimeError_: Not enough memory to load this model with these settings.") == "memory"
    assert classify("LLMContextFull: the prompt does not fit the model's context: 9000 > 8192") == "context"
    assert classify("ToolError: no such file") == "approach" and classify("") is None
    assert same_objection("the second greeting is still missing", "The second greeting is still missing.")
    assert not same_objection("add a second greeting", "the file report.csv was not saved")
    fit = RunFit()
    fit.want("m", 9000)
    fit.want("m", 5000)                 # a run's need never shrinks: the model is not reloaded back and forth
    assert fit.tokens("m") == 9000 and fit.tokens("other") == RunFit.BASE
    fit.cap["m"] = 4096                 # after it ran out of memory
    assert fit.tokens("m") == 4096


def test_the_forecast_sizes_the_context():
    from dva.runtime import CTX_STEPS, Runtime

    class FakeDB:
        def get(self, table, row_id, key="id"):
            return {"id": row_id, "settings": {"ctx": 65536, "parallel": 4}}

        def setting(self, key, default=None):
            return default
    rt = Runtime.__new__(Runtime)
    rt.db, rt.servers = FakeDB(), {}
    assert rt.fit("m", 9000) == {"ctx": 12288, "parallel": 1}
    assert rt.fit("m", 500000)["ctx"] == 65536            # never more than the user's own setting
    assert rt.fit("m", 100)["ctx"] == CTX_STEPS[0]


# ---------------------------------------------------------------- 2.6.0: doing less work
def test_a_reloaded_model_gets_its_prompt_back_from_a_snapshot(engine):
    """The agent's system prompt and tool list are read once and saved. After the model is unloaded and loaded
    again (which 2.5.0 does as soon as nothing needs it), they come from the file, not from reading."""
    c = engine["client"]
    mid = model_id(c)
    c.post("/api/runtime/snapshots/clear")
    c.post("/api/runtime/unload", json={})
    first = c.post("/api/chats", json={"harness": "agent"}).json()
    say(c, first["id"], "hello there")
    info = c.get("/api/runtime").json()
    now_loaded = next(m for m in info["loaded"] if m["model_id"] == mid)
    assert now_loaded["snapshot"] and now_loaded["snapshot"]["restored"] is False, now_loaded
    assert info["snapshots"]["files"] >= 1 and info["snapshots"]["saved"] >= 1, info["snapshots"]
    assert now_loaded["cache_ram_mb"] and 256 <= info["cache_ram_mb"] <= 2048
    c.post("/api/runtime/unload", json={})                          # a new server process: nothing in memory
    second = c.post("/api/chats", json={"harness": "agent"}).json()
    msg = say(c, second["id"], "hello again")
    info = c.get("/api/runtime").json()
    now_loaded = next(m for m in info["loaded"] if m["model_id"] == mid)
    assert now_loaded["snapshot"]["restored"] is True and now_loaded["snapshot"]["tokens"] > 300, now_loaded
    stats = msg["meta"]["stats"]
    assert stats["cached_tokens"] > 3 * stats["prompt_tokens"], stats      # almost nothing was read again
    assert info["snapshots"]["restored"] >= 1 and info["snapshots"]["tokens_restored"] > 300
    # switching snapshots off stops using them; deleting them works
    assert c.post("/api/runtime/snapshots/clear").json()["files"] == 0


def test_the_date_is_not_in_the_system_prompt(engine):
    """A date in the system prompt made every model read its whole tool list again each day."""
    import datetime
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "chat"}).json()
    text = say(c, chat["id"], "show system prompt")["content"]
    system, _, user = text.partition(" || ")
    year = str(datetime.date.today().year)
    assert year not in system and "Today is" in user and year in user, text[:600]


def test_tool_result_size_follows_reading_speed():
    from dva.runner import TOOL_RESULT_LIMIT, Turn

    class DB:
        def __init__(self, budget):
            self.budget = budget

        def setting(self, key, default=None):
            return self.budget if key == "reading_budget_seconds" else default

    class Server:
        def __init__(self, tps):
            self.read_tps = tps

    def limit(tps, budget=60, model="local-model"):
        t = Turn.__new__(Turn)
        t.db, t.server, t.model_id = DB(budget), Server(tps), model
        return t.result_limit()
    assert limit(14) == int(14 * 60 * 3.6)                 # the reference laptop: about a minute of reading
    assert limit(2) == 2500 and limit(2000) == 24000       # floor for very slow, ceiling for fast graphics cards
    assert limit(0) == TOOL_RESULT_LIMIT and limit(14, budget=0) == TOOL_RESULT_LIMIT
    assert limit(500, model="cloud:openai:gpt") == TOOL_RESULT_LIMIT


def test_fast_cores_are_found_on_mixed_processors(tmp_path):
    from dva.hardware import _linux_performance_cores
    # Intel 12th generation style: 4 fast cores with 2 threads each (cpus 0-7), 8 efficient cores (8-15)
    (tmp_path / "cpu_core").mkdir()
    (tmp_path / "cpu_atom").mkdir()
    (tmp_path / "cpu_core" / "cpus").write_text("0-7\n")
    (tmp_path / "cpu_atom" / "cpus").write_text("8-15\n")
    for cpu in range(16):
        d = tmp_path / "system" / "cpu" / f"cpu{cpu}" / "topology"
        d.mkdir(parents=True)
        (d / "core_id").write_text(str(cpu // 2 if cpu < 8 else cpu))
    assert _linux_performance_cores(str(tmp_path)) == 4
    plain = tmp_path / "plain"
    (plain / "system" / "cpu").mkdir(parents=True)
    assert _linux_performance_cores(str(plain)) is None    # all cores alike


def test_the_server_is_started_with_a_sized_prompt_cache_and_a_snapshot_folder(tmp_path):
    rt = _runtime_with(["cpu"], {})
    rt.cache_ram_mb = lambda: 512

    class Snaps:
        def enabled(self):
            return True

        def folder(self, model_id):
            return tmp_path
    rt.snapshots = Snaps()
    model = {"id": "m", "path": "/m.gguf", "size": 4 * 2**30, "settings": {}}
    plan = rt.plan(model)
    cmd = " ".join(rt._command(model, plan, 1234))
    assert "--cache-ram 512" in cmd and f"--slot-save-path {tmp_path}" in cmd
    with_cache = rt._need_bytes(model, plan)
    assert with_cache - rt._need_bytes(model, {**plan, "cache_ram": 0}) == 256 * 2**20     # half its limit is counted


@pytest.mark.skipif(not os.environ.get("DVA_TEST_LLAMA_SERVER"), reason="real llama-server not configured")
def test_real_llama_server_restores_a_prompt_snapshot():
    """Against the real llama.cpp server: a planned run's two prompts (planner and agent) both survive a reload."""
    tmp = Path(tempfile.mkdtemp(prefix="dva-real-snap-"))
    port = free_port()
    env = {**os.environ, "DVA_TOKEN": "t", "DVA_LLAMA_SERVER": os.environ["DVA_TEST_LLAMA_SERVER"],
           "DVA_SECRETS": "file", "PYTHONPATH": str(ENGINE)}
    proc = subprocess.Popen([sys.executable, "-m", "dva", "serve", "--port", str(port), "--data-dir", str(tmp)],
                            cwd=str(ENGINE), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout.readline().startswith("DVA_READY")
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer t"}, timeout=180,
                     trust_env=False)
    try:
        m = c.post("/api/models/import", json={"path": os.environ["DVA_TEST_GGUF"]}).json()
        c.put(f"/api/models/{m['id']}", json={"compute": "cpu", "ctx": 8192})
        c.put("/api/settings", json={"max_reply_tokens": 8, "learn_enabled": False, "team_mode": "off"})

        def turn(harness, text):
            chat = c.post("/api/chats", json={"harness": harness, "model": m["id"]}).json()
            job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": text}).json()
            assert wait_job(c, job["job_id"], timeout=170)["status"] in ("done", "failed")
            return last_assistant(c, chat["id"])["meta"]["stats"]
        cold = turn("agent", "hi")              # the prefix is read once on its own and saved; the turn reuses it
        assert cold["cached_tokens"] > 300, cold
        assert c.get("/api/runtime").json()["snapshots"]["saved"] >= 1
        c.post("/api/runtime/unload", json={})
        warm = turn("agent", "hi again")                             # a new server process
        loaded = c.get("/api/runtime").json()["loaded"][0]
        assert loaded["snapshot"]["restored"] is True, loaded
        assert warm["cached_tokens"] > 3 * warm["prompt_tokens"], (cold, warm)   # only the request itself is read
    finally:
        c.post("/api/shutdown")
        proc.wait(timeout=20)


# ---------------------------------------------------------------- 2.7.0: runs that continue, lean steps, evaluation
def test_a_stopped_run_continues_from_its_accepted_steps(engine):
    """Stop a run while its second step works; Resume runs steps 2 and 3 and does not repeat step 1."""
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages",
                 json={"text": "As a team with a forgetful plan (slow-steps): find, summarise, publish."}).json()
    for _ in range(300):                                  # until the second agent has started
        if len(c.get(f"/api/chats/{chat['id']}").json()["agents"]) >= 2:
            break
        time.sleep(0.05)
    c.post(f"/api/jobs/{job['job_id']}/cancel")
    assert wait_job(c, job["job_id"])["status"] == "cancelled"
    run = c.get(f"/api/chats/{chat['id']}").json()["open_run"]
    assert run and run["status"] == "stopped" and (run["done"], run["total"]) == (1, 3), run
    assert run["next"] == "Summarise"
    first_agents = [a["id"] for a in c.get(f"/api/chats/{chat['id']}").json()["agents"]]
    again = c.post(f"/api/chats/{chat['id']}/run/resume").json()
    assert wait_job(c, again["job_id"], timeout=120)["status"] == "done"
    team = team_of(last_assistant(c, chat["id"]))
    tasks = {t["id"]: t for t in team["tasks"]}
    assert all(t["status"] == "done" for t in tasks.values()), tasks
    assert tasks["t1"]["chat_id"] == first_agents[0] and tasks["t1"]["attempts"] == 1      # not run again
    assert tasks["t2"]["chat_id"] not in first_agents and tasks["t2"]["attempts"] == 1     # the cut-off try is not counted
    assert any(line["text"].startswith("Continuing this run: 1 of 3") for line in team["log"])
    assert "### t1 Find" in first_user_text(c, tasks["t2"]["chat_id"])                     # it got step 1's saved result
    assert c.get(f"/api/chats/{chat['id']}").json()["open_run"] is None
    users = [m for m in c.get(f"/api/chats/{chat['id']}").json()["messages"] if m["role"] == "user"]
    assert len(users) == 1                                                                 # no second request was added
    assert c.post(f"/api/chats/{chat['id']}/run/resume").status_code == 400                # nothing left to continue


def test_a_step_gets_only_the_tools_its_goal_needs(engine):
    from dva.orchestrator import lean_kit
    assert lean_kit("Read notes.txt and list the names") == ["files"]
    assert lean_kit("Search the web for the latest tariff and compute the average with Python") == ["files", "web", "python"]
    assert lean_kit("Look at the screenshot and describe it") == ["files", "vision"]
    assert lean_kit("Compute the totals", ["files", "web"]) == ["files"]         # never more than its harness has
    c = engine["client"]
    _, status, msg = run_team(c, "As a team with a forgetful plan: find greetings, summarise them, publish.")
    assert status == "done"
    tasks = {t["id"]: t for t in team_of(msg)["tasks"]}
    assert tasks["t1"]["kit"] == ["files"] and "web" in tasks["t3"]["kit"], tasks          # "publish" may need the web
    # the timeline of each step: what it read, took from the cache and wrote
    assert all(set(("read", "cached", "written", "calls", "tools")) <= set(t["used"]) for t in tasks.values())


def test_the_evaluation_runs_tasks_and_keeps_the_numbers(engine):
    c = engine["client"]
    listing = c.get("/api/evals").json()
    assert len(listing["tasks"]) == 12 and listing["current"] is None
    started = c.post("/api/evals/run", json={"only": ["calc", "summary"]}).json()
    assert started["total"] == 2
    assert c.post("/api/evals/run", json={}).status_code == 400                            # one at a time
    assert wait_job(c, started["job_id"], timeout=120)["status"] == "done"
    run = c.get("/api/evals").json()["runs"][0]
    assert run["id"] == started["id"] and run["status"] == "done" and run["version"]
    assert sorted(r["id"] for r in run["results"]) == ["calc", "summary"]
    for r in run["results"]:
        assert isinstance(r["passed"], bool) and r["seconds"] >= 0 and r["chat_id"] and "read" in r, r
    assert run["summary"]["ran"] == 2 and run["summary"]["total"] == 2 and 0 <= run["summary"]["passed"] <= 2
    assert run["settings"]["agent_order"] == "sequence" and "lean_tools" in run["settings"]      # what it ran with
    listed = {x["id"] for x in c.get("/api/chats").json()["chats"]} if isinstance(c.get("/api/chats").json(), dict) \
        else {x["id"] for x in c.get("/api/chats").json()}
    assert not ({r["chat_id"] for r in run["results"]} & listed)                           # not in the chat list


def test_the_evaluation_checks_judge_files_and_replies(tmp_path):
    from dva import evals
    by_id = {t["id"]: t for t in evals.TASKS}

    def folder(task_id):
        d = tmp_path / task_id
        d.mkdir()
        for name, text in by_id[task_id]["files"].items():
            (d / name).write_text(text, encoding="utf-8")
        return d
    assert evals.check_sum(None, "The total is 1,284.")[0] and not evals.check_sum(None, "1,248")[0]
    d = folder("bug")
    assert not evals.check_bug(d, "")[0]                                   # the bug is still there
    (d / "buggy.py").write_text("def mean(values):\n    return sum(values) / len(values)\n", encoding="utf-8")
    (d / "test_buggy.py").write_text("print('OK')\n", encoding="utf-8")    # a cheated test is replaced by the real one
    assert evals.check_bug(d, "")[0]
    d = folder("fizz")
    (d / "out.txt").write_text("\n".join(["1", "2", "Fizz", "4", "Buzz", "Fizz", "7", "8", "Fizz", "Buzz", "11",
                                           "Fizz", "13", "14", "FizzBuzz"]) + "\n", encoding="utf-8")
    assert evals.check_fizz(d, "")[0]
    d = folder("config")
    assert not evals.check_config(d, "")[0]
    (d / "config.ini").write_text(evals.CONFIG.replace("port = 8080", "port = 9090"), encoding="utf-8")
    assert evals.check_config(d, "")[0]
    (d / "config.ini").write_text(evals.CONFIG.replace("8080", "9090"), encoding="utf-8")   # changed the note too
    assert not evals.check_config(d, "")[0]
    d = folder("sort")
    assert not evals.check_sorted(d, "")[0]
    for sub, names in (("data", ("a.csv", "b.csv")), ("notes", ("todo.txt", "ideas.txt"))):
        (d / sub).mkdir()
        for n in names:
            (d / n).rename(d / sub / n)
    assert evals.check_sorted(d, "")[0]
    d = folder("invoice")
    (d / "invoice.json").write_text('{"number": "INV-2031", "date": "2026-03-14", "total": 18450.0}', encoding="utf-8")
    assert evals.check_invoice(d, "")[0]
    assert evals.check_diff(None, "Line 7 differs.")[0] and not evals.check_diff(None, "line 17")[0]
    assert evals.check_summary(None, "Begun in 2019, the programme connected 42% of households.")[0]
    d = folder("report")
    (d / "report.md").write_text("| North | 165 |\n| South | 122 |\n| East | 355 |\n", encoding="utf-8")
    assert evals.check_report(d, "")[0]
    assert evals.Evals.summary([{"passed": True, "read": 100, "cached": 300, "written": 20, "seconds": 3.2},
                                {"passed": False}], 12) == {
        "passed": 1, "ran": 2, "total": 12, "seconds": 3, "read": 100, "cached": 300, "written": 20,
        "agent_tokens": 0, "cache_percent": 75}


# ---------------------------------------------------------------- 2.8.0: history, learned model choice
def test_runs_are_kept_with_their_timeline(engine):
    c = engine["client"]
    chat, status, msg = run_team(c, "As a team with a forgetful plan: find greetings, summarise them, publish.")
    assert status == "done"
    runs = c.get("/api/runs").json()["runs"]
    mine = next(r for r in runs if r["chat_id"] == chat["id"])
    assert mine["status"] == "done" and (mine["done"], mine["total"], mine["failed"]) == (3, 3, 0), mine
    assert mine["tokens"] > 0 and mine["read"] + mine["cached"] > 0 and mine["rounds"] == 3 and mine["models"]
    assert "forgetful plan" in mine["request"]
    detail = c.get(f"/api/runs/{mine['id']}").json()
    assert [s["id"] for s in detail["steps"]] == ["t1", "t2", "t3"] and detail["can_continue"] is False
    for step in detail["steps"]:
        assert step["status"] == "done" and step["used"]["calls"] >= 1 and step["model_name"] and step["kit"], step
    assert any(line["text"].startswith("Planned 3 tasks") for line in detail["log"])
    assert c.get("/api/runs/nope").status_code == 404
    # every judged step is counted for its harness and model
    records = c.get("/api/models/records").json()["records"]
    agent = next(r for r in records if r["harness"] == "agent")
    assert agent["judged"] >= 3 and agent["passed"] >= 3 and agent["model_name"]


def test_model_choice_learns_from_measured_steps(tmp_path):
    from dva.db import DB
    from dva.forecast import Forecaster

    class App:
        db = DB(tmp_path / "t.db")
    fc = Forecaster(App())

    def history(model, passed, failed, seconds):
        for i in range(passed + failed):
            fc.judge("coder", model, i < passed)
        fc.db.update("step_stats", fc.key("coder", model), {"seconds": seconds}, key="key")
    history("small", 1, 5, 60)        # the usual model fails this kind of step
    history("big", 5, 1, 200)
    history("new", 1, 0, 10)          # one step is not enough to judge a model
    picked, why = fc.choose("coder", "small", ["small", "big", "new"])
    assert picked == "big" and "5 of 6" in why and "1 of 6" in why
    assert fc.choose("coder", "big", ["small", "big", "new"]) == ("big", None)        # nothing better: stay
    history("quick", 9, 1, 80)        # as reliable as "big", well under 70% of its time
    picked, why = fc.choose("coder", "big", ["small", "big", "quick"])
    assert picked == "quick" and "as reliably" in why
    assert fc.choose("writer", "small", ["small", "big"]) == ("small", None)          # no record for this harness
    rec = fc.record_of("coder", "quick")
    assert rec["judged"] == 10 and rec["passed"] == 9 and abs(rec["rate"] - 0.9) < 1e-9


# ---------------------------------------------------------------- 2.9.0: long-running tasks, set-ups, browser
def start_task(c, text, setup=None, timeout=150, approve="allow"):
    made = c.post("/api/missions", json={"text": text, "setup": setup or {}}).json()
    status = wait_job(c, made["job_id"], timeout=timeout, approve=approve)["status"]
    return made["chat_id"], status, last_assistant(c, made["chat_id"])


def test_a_long_task_is_a_relay_of_handoffs(engine):
    """The planner gives one step at a time. Each agent works in a fresh chat, gets the planner's understanding
    and the handoff of the step before it, and leaves a handoff of its own."""
    c = engine["client"]
    chat_id, status, msg = start_task(c, "Long task: a relay of greetings.")
    assert status == "done"
    team = team_of(msg)
    tasks = team["tasks"]
    assert [t["name"] for t in tasks] == ["Survey", "Draft", "Polish"] and all(t["status"] == "done" for t in tasks)
    assert len({t["chat_id"] for t in tasks}) == 3                      # a fresh agent for every step
    m = team["mission"]
    assert [h["id"] for h in m["handoffs"]] == ["s1", "s2", "s3"] and m["handoffs"][0]["next"].startswith("do the next")
    assert "3 steps are done" in m["understanding"] and m["spent"]["steps"] == 3
    first, second = first_user_text(c, tasks[0]["chat_id"]), first_user_text(c, tasks[1]["chat_id"])
    assert "Handoff from the step before yours" not in first and "## Handoff" in first
    assert "Handoff from the step before yours" in second and "### s1 Survey (done)" in second
    assert "Next: do the next part after Survey" in second and "1 steps are done" in second    # the understanding
    assert "Earlier steps:" in first_user_text(c, tasks[2]["chat_id"])
    assert any(line["text"].startswith("The planner finished the task") for line in team["log"])
    row = next(r for r in c.get("/api/missions").json()["missions"] if r["chat_id"] == chat_id)
    assert row["status"] == "done" and (row["done"], row["steps"]) == (3, 3) and row["last"]["id"] == "s3"
    assert c.get(f"/api/chats/{chat_id}").json()["open_run"] is None
    assert c.get(f"/api/chats/{chat_id}").json()["harness"] == "long-task"
    kinds = {r["chat_id"]: r["kind"] for r in c.get("/api/runs").json()["runs"]}
    assert kinds[chat_id] == "mission"


def test_a_long_task_changes_its_plan_when_a_step_finds_a_problem(engine):
    c = engine["client"]
    _, status, msg = start_task(c, "Long task that hits a blocker on the way.")
    assert status == "done"
    team = team_of(msg)
    assert [t["name"] for t in team["tasks"]] == ["Fetch", "Workaround"]
    m = team["mission"]
    assert m["changes"] == 1 and m["outline"][0] == "Use the saved copy instead"
    assert any(line["text"].startswith("The plan changed: the site is down") for line in team["log"]), team["log"]
    assert "blocker" in m["handoffs"][0]["found"]
    # the planner created the kind of agent it needed
    assert team["tasks"][1]["agent"] == "Archivist" and any(a["name"] == "Archivist" for a in m["team"])
    assert any("added an agent to the team: Archivist" in line["text"] for line in team["log"])


def test_a_long_task_pauses_at_its_limit_and_continues_when_it_is_raised(engine):
    c = engine["client"]
    chat_id, status, msg = start_task(c, "Long task: a relay of greetings, kept short.", {"limits": {"steps": 2}})
    assert status == "done"                                   # the session ended cleanly; the task is paused
    assert msg["content"].startswith("**Paused:** it has used its 2 steps")
    run = c.get(f"/api/chats/{chat_id}").json()["open_run"]
    assert run["mission"] and run["status"] == "paused" and run["pause"]["kind"] == "limit" and run["done"] == 2
    row = next(r for r in c.get("/api/missions").json()["missions"] if r["chat_id"] == chat_id)
    assert row["status"] == "paused" and row["limits"]["steps"] == 2
    assert c.put(f"/api/missions/{run['id']}/limits", json={"steps": 6}).json()["limits"]["steps"] == 6
    again = c.post(f"/api/chats/{chat_id}/run/resume").json()
    assert wait_job(c, again["job_id"], timeout=120)["status"] == "done"
    team = team_of(last_assistant(c, chat_id))
    assert [t["name"] for t in team["tasks"]] == ["Survey", "Draft", "Polish"]
    assert [t["attempts"] for t in team["tasks"]] == [1, 1, 1]                 # nothing was done twice
    assert team["mission"]["pause"] is None and team["mission"]["limits"]["steps"] == 6
    assert c.get(f"/api/chats/{chat_id}").json()["open_run"] is None
    users = [m for m in c.get(f"/api/chats/{chat_id}").json()["messages"] if m["role"] == "user"]
    assert len(users) == 1


def test_a_long_task_asks_and_goes_on_with_the_answer(engine):
    c = engine["client"]
    chat_id, status, msg = start_task(c, "Long task that needs my answer before the second step.")
    assert status == "done" and "Which colour should it be?" in msg["content"]
    run = c.get(f"/api/chats/{chat_id}").json()["open_run"]
    assert run["pause"]["kind"] == "question"
    job = c.post(f"/api/chats/{chat_id}/messages", json={"text": "teal"}).json()     # an ordinary reply in the chat
    assert wait_job(c, job["job_id"], timeout=120)["status"] == "done"
    team = team_of(last_assistant(c, chat_id))
    assert [t["name"] for t in team["tasks"]] == ["Prepare", "Paint teal"]
    assert any("You answered the planner's question: teal" in line["text"] for line in team["log"])


def test_the_relay_moves_on_the_last_handoff_when_the_planner_cannot_be_asked(engine):
    c = engine["client"]
    _, status, msg = start_task(c, "Long task where the planner breaks after the first step.")
    assert status == "done"
    team = team_of(msg)
    assert [t["name"] for t in team["tasks"]] == ["Survey", "Continue"]
    assert team["tasks"][1]["goal"].startswith("do the next part after Survey")
    assert any("follows the last agent's handoff" in line["text"] for line in team["log"]), team["log"]


def test_a_step_that_failed_is_not_given_again_the_same_way(engine):
    """Two failures must not be the same (2.15.0). A step that failed is written down with the class of its
    failure; a planner that gives the same step again is refused by code and told why, and the agent that takes
    the other way knows what failed before it."""
    c = engine["client"]
    _, status, msg = start_task(c, "Long task with a stubborn planner.")
    assert status == "done"
    team = team_of(msg)
    assert [t["name"] for t in team["tasks"]] == ["Fetch", "Mirror"], team["log"]
    fetch, mirror = team["tasks"]
    assert fetch["status"] == "failed" and fetch["kind"] == "approach" and mirror["status"] == "done"
    assert 2 <= fetch["attempts"] <= 3
    log = [line["text"] for line in team["log"]]
    assert any(t.startswith("Not run again: “Fetch” already failed this way") for t in log), log
    assert any(t.startswith("Round 3 of 3 for s1 with a fresh agent") for t in log), log    # the third round differed
    h = team["mission"]["handoffs"][0]
    assert h["status"] == "failed" and h["class"] == "approach" and "exploded" in h["found"]
    brief = first_user_text(c, mirror["chat_id"])
    assert "do not do it again the same way" in brief and "\nFailed: what you tried that did not work" in brief
    assert "### s1 Fetch (failed)" in brief
    assert team["mission"]["changes"] == 1


def test_a_task_that_cannot_get_past_a_failure_pauses_and_says_what_was_tried(engine):
    """After several different recoveries have failed too, a person is needed: the task pauses, says what was
    tried and how each try failed, and goes on with what the user answers."""
    c = engine["client"]
    c.put("/api/settings", json={"recovery_attempts": 1})
    try:
        chat_id, status, msg = start_task(c, "Long task: a hopeless task that fails whatever is tried.")
        assert status == "done" and msg["content"].startswith("**Paused:** 2 steps in a row failed"), msg["content"][:300]
        assert "What was tried and how it failed" in msg["content"]
        assert "s1 “Try 1”" in msg["content"] and "s2 “Try 2”" in msg["content"] and "exploded" in msg["content"]
        run = c.get(f"/api/chats/{chat_id}").json()["open_run"]
        assert run["status"] == "paused" and run["pause"]["kind"] == "recovery"
        assert [t["status"] for t in team_of(msg)["tasks"]] == ["failed", "failed"]
        job = c.post(f"/api/chats/{chat_id}/messages", json={"text": "leave it and write up what you have"}).json()
        assert wait_job(c, job["job_id"], timeout=120)["status"] == "done"
        assert c.get(f"/api/chats/{chat_id}").json()["open_run"] is None
        assert len(team_of(last_assistant(c, chat_id))["tasks"]) == 2            # nothing more was tried
    finally:
        c.put("/api/settings", json={"recovery_attempts": 3})


def test_tasks_with_nobody_watching_know_and_use_the_connected_services(engine):
    """(2.15.0) A long-running or scheduled task that was given no connectors may use every connector that is
    switched on. Its planner is told what each service promises, its agents are told to go there first, and
    the service's tools are in their hands."""
    c = engine["client"]
    conn = next(x for x in c.get("/api/connectors").json()["connectors"] if x["name"] == "Test MCP")
    chat_id, status, msg = start_task(c, "Long task: a relay of greetings, kept short.", {"limits": {"steps": 1}})
    assert status == "done"
    assert conn["id"] in c.get(f"/api/chats/{chat_id}").json()["connectors"]
    team = team_of(msg)
    told = team["mission"]["understanding"]
    assert "The planner was told of these services: - Test MCP" in told and "whoami" in told, told
    agent = team["tasks"][0]
    brief = first_user_text(c, agent["chat_id"])
    assert "Connected services you have tools for: Test MCP." in brief and "only for what they do not offer" in brief
    assert conn["id"] in c.get(f"/api/chats/{agent['chat_id']}").json()["connectors"]
    # a schedule with no connectors chosen: its run has the service's tools and uses them
    s = c.post("/api/schedules", json={"name": "Who am I", "when": "every day at 06:00", "prompt": "whoami please",
                                       "harness": "agent"}).json()
    run = c.post(f"/api/schedules/{s['id']}/run").json()
    assert wait_job(c, run["job_id"])["status"] == "done"
    assert conn["id"] in c.get(f"/api/chats/{run['chat_id']}").json()["connectors"]
    step = [x for x in last_assistant(c, run["chat_id"])["meta"]["steps"] if x["type"] == "tool"][0]
    assert step["name"].endswith("whoami") and step["ok"], step
    c.delete(f"/api/schedules/{s['id']}")


def test_a_stopped_long_task_continues_where_it_was(engine):
    c = engine["client"]
    made = c.post("/api/missions", json={"text": "Long task: a relay of greetings (slow-steps)."}).json()
    chat_id = made["chat_id"]
    for _ in range(400):                                   # until the second agent has started
        if len(c.get(f"/api/chats/{chat_id}").json()["agents"]) >= 2:
            break
        time.sleep(0.05)
    c.post(f"/api/jobs/{made['job_id']}/cancel")
    assert wait_job(c, made["job_id"])["status"] == "cancelled"
    run = c.get(f"/api/chats/{chat_id}").json()["open_run"]
    assert run["mission"] and run["status"] == "stopped" and run["done"] == 1 and run["next"] == "Draft"
    first = [a["id"] for a in c.get(f"/api/chats/{chat_id}").json()["agents"]]
    again = c.post(f"/api/chats/{chat_id}/run/resume").json()
    assert wait_job(c, again["job_id"], timeout=120)["status"] == "done"
    team = team_of(last_assistant(c, chat_id))
    tasks = team["tasks"]
    assert [t["name"] for t in tasks] == ["Survey", "Draft", "Polish"] and all(t["status"] == "done" for t in tasks)
    assert tasks[0]["chat_id"] == first[0] and tasks[0]["attempts"] == 1 and tasks[1]["attempts"] == 1
    assert [h["id"] for h in team["mission"]["handoffs"]] == ["s1", "s2", "s3"]


def approvals_seen(c, job_id, chat_id, timeout=120):
    """Wait for a task, allowing what its agents ask for. Returns (status, the tools that asked)."""
    asked, deadline = [], time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job["status"], asked
        for ap in c.get(f"/api/chats/{chat_id}").json()["pending_approvals"]:
            asked.append(ap["tool"])
            c.post(f"/api/approvals/{ap['approval_id']}", json={"allow": True})
        time.sleep(0.2)
    raise AssertionError("the task did not end")


def test_a_task_does_without_asking_only_what_its_setup_allows(engine):
    c = engine["client"]
    made = c.post("/api/missions", json={"text": "Long task with a note-step first.", "setup": {"allow": ["files"]}}).json()
    status, asked = approvals_seen(c, made["job_id"], made["chat_id"])
    assert status == "done" and asked == []                    # writing files was agreed when it was set up
    team = team_of(last_assistant(c, made["chat_id"]))
    note = next(m for m in c.get(f"/api/chats/{team['tasks'][0]['chat_id']}").json()["messages"] if m["role"] == "assistant")
    assert any(s["type"] == "tool" and s["name"] == "write_file" and s["ok"] for s in note["meta"]["steps"])
    c.put("/api/settings", json={"unattended_auto_approve": False})     # 2.29.0: the set-up's list decides
    try:
        made = c.post("/api/missions", json={"text": "Long task with a note-step first.", "setup": {"allow": ["browser"]}}).json()
        status, asked = approvals_seen(c, made["job_id"], made["chat_id"])
        assert status == "done" and asked == ["write_file"]        # not agreed: it asked (and the user allowed it)
    finally:
        c.put("/api/settings", json={"unattended_auto_approve": True})
    made = c.post("/api/missions", json={"text": "Long task with a note-step first.", "setup": {"allow": ["browser"]}}).json()
    status, asked = approvals_seen(c, made["job_id"], made["chat_id"])
    assert status == "done" and asked == []                    # 2.29.0 default: unattended work approves its own


def test_a_planner_sets_up_a_task_and_code_repairs_the_setup(engine):
    c = engine["client"]
    out = c.post("/api/missions/plan", json={"text": "Study greetings and report on them."}).json()
    s = out["setup"]
    assert s["name"] == "Greeting study" and s["kind"] == "mission" and s["planned_by"]
    assert [m["name"] for m in s["team"]] == ["Researcher", "Writer"]         # the duplicate is dropped
    assert s["team"][1]["harness"] == "writer"
    assert s["limits"]["hours"] == 240 and s["limits"]["steps"] == 1 and s["limits"]["step_minutes"] == 5
    assert s["allow"] == ["files"] and "files" in out["allowable"] and s["outline"] == ["Collect", "Write"]
    sched = c.post("/api/missions/plan", json={"text": "Each morning, study greetings.", "scheduled": True}).json()["setup"]
    assert sched["cron"] == "30 7 * * *" and sched["when"] == "Every day at 07:30"
    assert c.post("/api/missions/plan", json={"text": " "}).status_code == 400


def test_a_scheduled_task_can_be_a_long_task_with_its_setup(engine):
    c = engine["client"]
    setup = {"kind": "mission", "team": [{"name": "Greeter", "harness": "agent", "about": "says hello"}],
             "limits": {"hours": 1, "steps": 9}, "allow": ["files"]}
    s = c.post("/api/schedules", json={"name": "Nightly relay", "when": "every day at 02:00",
                                       "prompt": "Long task: a relay of greetings at night.", "setup": setup}).json()
    assert s["setup"]["kind"] == "mission" and s["setup"]["limits"]["steps"] == 9
    run = c.post(f"/api/schedules/{s['id']}/run").json()
    assert wait_job(c, run["job_id"], timeout=150)["status"] == "done"
    chat = c.get(f"/api/chats/{run['chat_id']}").json()
    assert chat["origin"] == "schedule" and chat["harness"] == "long-task"
    team = team_of(last_assistant(c, run["chat_id"]))
    assert len(team["tasks"]) == 3 and team["mission"]["limits"]["steps"] == 9
    assert {t["agent"] for t in team["tasks"]} == {"Greeter"} and team["mission"]["allow"] == ["files"]
    row = next(r for r in c.get("/api/missions").json()["missions"] if r["chat_id"] == run["chat_id"])
    assert row["schedule_id"] == s["id"]
    plain = c.put(f"/api/schedules/{s['id']}", json={"setup": {}}).json()
    assert plain["setup"] == {}
    assert c.get("/api/busy").json()["active"] == 0


def test_handoffs_limits_and_tool_kits_in_code():
    from dva.missions import clean_limits, parse_handoff
    from dva.orchestrator import lean_kit
    h = parse_handoff("I looked at three files.\n\n## Handoff\nDone: read a.csv, 12 rows\nFound: nothing new\n"
                      "Next: chart the rows\nand save it")
    assert h == {"done": "read a.csv, 12 rows", "found": "nothing new", "failed": "", "next": "chart the rows\nand save it"}
    h = parse_handoff("## Handoff\nDone: nothing usable\nFound: the site blocks robots\nFailed: web_fetch on the "
                      "ministry site, HTTP 403 twice\nNext: try the web archive")
    assert h["failed"] == "web_fetch on the ministry site, HTTP 403 twice" and h["next"] == "try the web archive"
    h = parse_handoff("**Handoff**\n- **Done:** fixed the test\n- **Next:** nothing: the task is complete")
    assert h["done"] == "fixed the test" and h["next"].startswith("nothing") and h["found"] == ""
    assert parse_handoff("Just an answer without the block.")["done"] == "Just an answer without the block."
    assert clean_limits({"hours": "2.5", "steps": 10**9, "tokens": -5, "cost": "x"}) == \
        {"hours": 2.5, "steps": 400, "step_minutes": 45, "tokens": 0, "cost": 0.0}
    assert "browser" in lean_kit("Log in to the portal and click the Export button")
    assert "browser" not in lean_kit("Read notes.txt and list the names")


def test_failures_have_a_class_and_the_same_attempt_is_not_made_twice():
    """recovery.py: what kind of failure it was decides what may happen next, and an attempt that equals a failed
    one is refused unless its class says the same may be tried again."""
    from types import SimpleNamespace
    from dva import recovery as rc
    from dva.orchestrator import Orchestra
    # the same error is recognised whatever numbers, paths and addresses it carries
    assert rc.signature("HTTP 403 for https://a.example/x?id=7 after 12.5 s") == rc.signature("HTTP 403 for https://b.example/y after 3 s")
    assert rc.signature("no such file C:\\Users\\a\\x.csv") == rc.signature("no such file /home/b/y.csv")
    assert rc.signature("HTTP 403 for https://a.example") != rc.signature("HTTP 404 not found")[:0] + rc.signature("the page is empty")
    # classes
    assert rc.classify("ConnectError: connection reset by peer") == "transient"
    assert rc.classify("CloudError: 503 service unavailable, try again later") == "transient"
    assert rc.classify(None, guard="the time limit of 30 minutes was reached") == "timeout"
    assert rc.classify(None, guard="it used all 10 steps this harness allows", objection="not complete") == "timeout"
    assert rc.classify(None, guard="web_fetch failed three times with the same error (HTTP 403)") == "approach"
    assert rc.classify(None, guard="the last six tool calls produced nothing new") == "approach"
    assert rc.classify("ConnectionError: Notes needs you to sign in first") == "permission"
    assert rc.classify("ImageError: The image engine is not installed. Open Images") == "missing"
    assert rc.classify(None, failures=[{"error": "The user declined this action.", "declined": True}]) == "permission"
    assert rc.classify(None, objection="the table has no totals") == "quality"
    assert rc.classify(None, objection="the table has no totals", repeated=True) == "capability"
    assert rc.classify("Stopped", stopped=True) == "stopped" and rc.classify(None) is None
    assert all(k in rc.CLASSES for k in ("transient", "stall", "timeout", "memory", "context", "approach",
                                         "capability", "missing", "permission"))
    # what an agent's tools reported, one entry per tool and kind of error
    steps = [{"type": "tool", "name": "web_fetch", "arguments": {"url": "https://a.example/1"}, "ok": False,
              "result": "Error: HTTP 403 for https://a.example/1"},
             {"type": "tool", "name": "web_fetch", "arguments": {"url": "https://a.example/2"}, "ok": False,
              "result": "Error: HTTP 403 for https://a.example/2"},
             {"type": "tool", "name": "web_fetch", "arguments": {"url": "https://a.example/2"}, "ok": False, "blocked": True,
              "result": "Blocked: you already called web_fetch"},
             {"type": "tool", "name": "read_file", "arguments": {"path": "a.csv"}, "ok": True, "result": "1,2"},
             {"type": "text", "text": "…"}]
    fails = rc.tool_failures(steps)
    assert len(fails) == 1 and fails[0]["tool"] == "web_fetch" and fails[0]["times"] == 2 and "403" in fails[0]["error"]
    assert not rc.progressing(steps) and not rc.progressing(steps[3:4])
    good = [{"type": "tool", "name": "read_file", "ok": True}] * 3 + [steps[0]]
    assert rc.progressing(good) and not rc.progressing(good, guard="the agent kept repeating calls that were already made")
    # the same attempt again
    t = {"id": "s1", "name": "Fetch", "goal": "Download the annual report from the ministry site and save it",
         "agent": "Researcher", "harness": "agent", "model": "m1", "attempts": 3, "failures": fails}
    failed = rc.record(t, "it ended with an error: HTTP 403", "approach", minutes=45)
    again = {"goal": "Download the annual report from the ministry site and save it as a file", "harness": "agent",
             "model": "m1", "minutes": 45}
    why = rc.refusal(again, [failed])
    assert why and "already failed this way" in why and "HTTP 403" in why
    assert rc.refusal({**again, "model": "m2"}, [failed]) is None                  # a stronger model is a difference
    assert rc.refusal({**again, "harness": "researcher"}, [failed]) is None        # so is another kind of agent
    assert rc.refusal({**again, "goal": "Find the annual report in the web archive instead"}, [failed]) is None
    late = rc.record({**t, "progressing": True}, "it ran past its time limit and was stopped", "timeout", minutes=45)
    assert rc.refusal(again, [late]) and rc.refusal({**again, "minutes": 90}, [late]) is None     # more time is one
    stuck = rc.record(t, "it ran past its time limit and was stopped", "timeout", minutes=45)     # going in circles
    assert rc.refusal({**again, "minutes": 90}, [stuck])
    blip = rc.record(t, "ConnectError: connection reset by peer", "transient", minutes=45)
    assert rc.refusal(again, [blip]) is None and rc.refusal(again, [blip, blip]) is None and rc.refusal(again, [blip] * 3)
    line = rc.describe({**failed, "change": "a fresh agent told to go another way"}, "Qwen 9B")
    assert line.startswith("- s1 “Fetch” [Researcher, Qwen 9B, 45 min] — this way of doing it did not work:")
    assert "web_fetch ×2" in line and line.endswith("→ then: a fresh agent told to go another way")
    # more time after a timeout: twice as long, never past what the run has left
    run = SimpleNamespace(step_minutes=lambda t: float(t["limits"].get("time_limit_minutes") or 30),
                          minutes_left=lambda: 100.0, harness_of=lambda t: {"max_steps": 10})
    step = {"limits": {"time_limit_minutes": 30}}
    assert Orchestra.more_time(run, step) == 60 and step["limits"] == {"time_limit_minutes": 60.0, "max_steps": 20}
    run.minutes_left = lambda: 35.0
    assert Orchestra.more_time(run, {"limits": {"time_limit_minutes": 30}}) is None     # not a real difference


def test_the_same_error_twice_is_said_and_three_times_stops_the_agent():
    from types import SimpleNamespace
    from dva.runner import LoopGuard
    turn = SimpleNamespace(limits={}, harness={}, db=SimpleNamespace(setting=lambda k, d=None: d))
    g = LoopGuard(turn)
    g.record("web_fetch", {"url": "https://a.example/1"}, False, "Error: HTTP 403 for https://a.example/1")
    assert g.advice is None
    g.record("run_python", {"code": "x"}, False, "Error: NameError: name 'x' is not defined")     # another failure
    g.record("web_fetch", {"url": "https://a.example/2"}, False, "Error: HTTP 403 for https://a.example/2")
    assert "web_fetch has now failed twice with the same error" in g.advice and not g.should_stop()
    g.advice = None
    g.record("web_fetch", {"url": "https://a.example/3"}, True, "the page")
    g.record("web_fetch", {"url": "https://a.example/4"}, False, "Error: HTTP 403 for https://a.example/4")
    assert g.should_stop().startswith("web_fetch failed three times with the same error (Error: HTTP 403")


def test_the_image_engines_own_lines_are_read_as_it_really_prints_them():
    """Lines copied from a real run of the pinned image engine on Windows (18 Nov 2026): a redraw of 6 steps at
    strength 0.6 draws 4, and the bars it prints while loading are not steps."""
    from dva.images import INSTRUCTION, PICTURE_LINE, step_line
    assert step_line("|============>                                     | 1/4 - 223.44s/it - ETA 11:10") == (1, 4, 223.44)
    assert step_line("|=========================>                        | 2/4 - 222.96s/it - ETA 07:26") == (2, 4, 222.96)
    assert step_line("|###                                               |  16/386 -  51.82MB/s") is None
    assert step_line("|##################################################| 452/452 - 231.85MB/s") is None
    assert step_line("[I] target t_enc is 3 steps --- image.cpp:245") is None
    n, total, per = step_line("|=====>              | 3/20 - 4.00it/s")
    assert (n, total) == (3, 20) and abs(per - 0.25) < 1e-9
    assert PICTURE_LINE.search("[I] generating image: 1/2 - seed 10 --- image.cpp:1005").groups() == ("1", "2")
    assert not PICTURE_LINE.search("[I] generate_image 768x768 --- image.cpp:954")
    assert INSTRUCTION.match("Make the picture glow on its outline. Deliver in the same format.")
    assert INSTRUCTION.match("please remove the car") and not INSTRUCTION.match("A red kite over a green hill")


def test_changes_that_need_no_model(tmp_path):
    from PIL import Image, ImageDraw
    from dva import effects
    assert effects.find("Make the picture glow on its outline. Deliver in the same format.") == ("glow", {"amount": 1.0})
    assert effects.find("add a strong blue glow around the edges")[1] == {"colour": "blue", "amount": 1.6}
    assert effects.find("make it black and white")[0] == "grayscale" and effects.find("blur it slightly") == ("blur", {"amount": 0.6})
    assert effects.find("resize it to 800 x 600")[1]["width"] == 800 and effects.find("make it smaller")[1]["scale"] == 0.5
    assert effects.find("rotate the image 90 degrees right")[1]["degrees"] == -90
    assert effects.find("convert the picture to jpg")[1]["format"] == "jpg"
    # more than an effect, or no effect at all: a model has to do it
    for text in ("Make the sky glow orange and add a dragon flying over the castle", "a castle on a hill at sunset",
                 "make the cat wear a hat", "make the background brighter and remove the tree on the left side"):
        assert effects.find(text) is None, text
    cut = tmp_path / "cut out.png"                              # a cut-out: the glow goes around what is not transparent
    img = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((100, 50, 200, 150), fill=(200, 40, 40, 255))
    img.save(cut)
    path, facts = effects.apply(cut, tmp_path / "out", "glow", {"colour": "blue"})
    out = Image.open(path)
    assert path.suffix == ".png" and out.size == (300, 200) and facts["how"] == "the edge of the cut-out"
    assert out.getpixel((150, 100)) == (200, 40, 40, 255)                         # the subject is untouched
    edge = out.getpixel((96, 100))
    assert edge[3] > 60 and edge[2] > edge[0] and out.getpixel((5, 5))[3] == 0    # blue light just outside, none far away
    plain = tmp_path / "plain.jpg"                              # a plain background: around what stands out from it
    img = Image.new("RGB", (300, 200), (245, 245, 240))
    ImageDraw.Draw(img).rectangle((100, 50, 200, 150), fill=(20, 110, 80))
    img.save(plain, quality=95)
    path, facts = effects.apply(plain, tmp_path / "out", "glow", {})
    out = Image.open(path)
    assert path.suffix == ".jpg" and out.format == "JPEG" and out.size == (300, 200)      # the same format and size
    assert facts["how"] == "what stands out from the plain background"
    far, near, inside = out.getpixel((10, 10)), out.getpixel((95, 100)), out.getpixel((150, 100))
    assert abs(far[2] - 240) < 6 and near[2] < far[2] - 15 and abs(inside[1] - 110) < 8   # warm light at the outline only
    path, facts = effects.apply(plain, tmp_path / "out", "resize", {"width": 150, "height": 100})
    assert Image.open(path).size == (150, 100) and facts["was"] == "300 × 200"
    path, _ = effects.apply(plain, tmp_path / "out", "format", {"format": "png"})
    assert path.suffix == ".png" and Image.open(path).format == "PNG"
    grey = Image.open(effects.apply(cut, tmp_path / "out", "grayscale", {})[0]).getpixel((150, 100))
    assert grey[0] == grey[1] == grey[2] and grey[3] == 255


def test_what_connected_services_promise_is_put_into_words():
    from dva.mcp import Connectors
    cn = Connectors.__new__(Connectors)
    cn.tool_cache, cn.info, cn.unreachable = {}, {}, {}
    confs = {"a": {"id": "a", "name": "Datasets", "enabled": True, "tools": [],
                   "description": "A catalogue of public datasets. It has more to say."},
             "b": {"id": "b", "name": "Mail", "enabled": True, "description": "",
                   "tools": [{"name": "send_mail", "description": "Send a message."},
                             {"name": "list_mail", "description": "List the inbox."}]},
             "c": {"id": "c", "name": "Off", "enabled": False},
             "d": {"id": "d", "name": "Far away", "enabled": True, "description": "A remote service", "tools": []}}
    cn.get = lambda cid, reveal=True: confs.get(cid)
    cn.tool_cache["a"] = (time.time(), [
        {"name": "publish", "description": "Publish a dataset. It becomes public.", "readonly": False},
        {"name": "search_datasets", "readonly": True,
         "description": "Search the catalogue by keyword and year, with a great many options that go on and on and on"}])
    cn.mark_down("d", ConnectionError("no route to host"))
    lines = cn.digest(["a", "b", "c", "d", "gone"]).splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("- Datasets: A catalogue of public datasets. Tools: search_datasets (Search the catalogue by")
    assert "…); publish (Publish a dataset)" in lines[0]               # reading tools first, a few words on each
    assert lines[1] == "- Mail. Tools: list_mail (List the inbox); send_mail (Send a message)"
    assert lines[2] == "- Far away: A remote service (it could not be reached: ConnectionError: no route to host)"
    assert cn.names(["a", "b", "c", "gone"]) == ["Datasets", "Mail"] and cn.down("d") and not cn.down("a")
    assert cn.digest(["a"], tools=1).endswith("; also publish")                # the rest by name (2.26.0)


SHOP = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Test shop</title>
<meta name="generator" content="TestKit 1.0"><link rel="next" href="page2.html"></head><body>
<div role="dialog" aria-label="Cookie consent"><p>We use cookies to improve your experience.</p>
<button onclick="document.cookie='consent=all;path=/';this.parentNode.remove()">Accept all</button>
<button onclick="document.cookie='consent=necessary;path=/';this.parentNode.remove()">Reject all</button></div>
<h1>Test shop</h1><a href="prices.html">Price list</a> <a href="login.html">Sign in</a>
<h2>Datasets</h2><div id="items">Loading…</div>
<form onsubmit="document.getElementById('out').textContent='Hello, '+document.getElementById('n').value;return false">
<label for="n">Your name</label><input id="n"><button>Greet</button></form><p id="out"></p>
<form id="order" action="/order" method="post" onsubmit="document.getElementById('out').textContent='Ordered '+this.qty.value+' of '+this.item.value+(this.gift.checked?' as a gift':'');return false">
<label for="item">Dataset</label><select id="item" name="item"><option>Glaciers</option><option>Rainfall</option></select>
<label for="qty">Copies</label><input id="qty" name="qty" type="number" value="1" required>
<label><input type="checkbox" name="gift"> This is a gift</label><button type="submit">Place order</button></form>
<a href="/files/prices.csv">Download prices</a> <a href="/files/report.pdf">Annual report</a>
<a href="terms.html" target="_blank">Terms</a>
<select aria-label="Colour"><option>Red</option><option>Blue</option></select>
<button onclick="alert('Saved!')">Save</button>
<button onclick="document.getElementById('out').textContent=confirm('Delete everything?')?'deleted':'kept'">Delete</button>
<button onclick="setTimeout(()=>{document.getElementById('late').textContent='The late result is 42'},1200)">Compute</button><p id="late"></p>
<label for="f">Your file</label><input type="file" id="f" onchange="document.getElementById('out').textContent='Got '+this.files[0].name">
<table><caption>Stock</caption><tr><th>Item</th><th>Units</th></tr><tr><td>Glaciers</td><td>12</td></tr><tr><td>Rainfall</td><td>7</td></tr></table>
<script>fetch('/api/items.json').then(r=>r.json()).then(d=>{document.getElementById('items').innerHTML='<ul>'+d.items.map(i=>'<li>'+i.name+': '+i.rows+' rows</li>').join('')+'</ul>'})</script>
</body></html>"""


def test_the_browser_tools_work_a_website_like_a_careful_person(tmp_path):
    """With a real Chrome and the bundled browser helper (skipped where there is none). 2.9.0: open a page, type,
    click, follow a download, take a screenshot, each answer showing the page afterwards. 2.16.0: the page is
    waited for and its cookie banner answered with the choice that allows least; elements are found by their
    words; the page's messages and questions are handled; a new tab is followed; a whole form is filled in;
    how the page is served and where its data comes from is found out; files are saved with the page's own
    session; a file is given to the page only after asking; paying asks even when clicks are allowed."""
    import asyncio
    import functools
    import http.server
    import shutil
    import threading
    cli = ENGINE.parent / "app" / "node_modules" / "@playwright" / "mcp" / "cli.js"
    chrome = shutil.which("google-chrome") or shutil.which("chrome")
    node = shutil.which("node")
    if not (cli.exists() and chrome and node) or os.name == "nt":
        pytest.skip("needs Node, the browser helper (app/node_modules) and Chrome")
    site = tmp_path / "site"
    (site / "files").mkdir(parents=True)
    (site / "api").mkdir()
    (site / "index.html").write_text(SHOP, encoding="utf-8")
    (site / "prices.html").write_text("<!doctype html><title>Price list</title><h1>Price list</h1><p>Tea 4.50</p>")
    (site / "terms.html").write_text("<!doctype html><title>Terms</title><h1>Terms of use</h1><button>I agree</button>")
    (site / "login.html").write_text("<!doctype html><title>Sign in</title><form><label for='u'>User name</label><input id='u'>"
                                     "<label for='p'>Password</label><input id='p' type='password'><button>Sign in</button></form>")
    (site / "files" / "prices.csv").write_text("item,price\ntea,4.50\n")
    (site / "files" / "report.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 5000)
    (site / "api" / "items.json").write_text('{"items":[{"name":"Glacier mass balance","rows":1200}]}')

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            if self.path.endswith(".csv"):
                self.send_header("Content-Disposition", "attachment; filename=prices.csv")
            self.send_header("Set-Cookie", "session=abc123; HttpOnly; Path=/")
            super().end_headers()

        def log_message(self, *a):
            pass
    port = free_port()
    web = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=str(site)))
    threading.Thread(target=web.serve_forever, daemon=True).start()
    env = {"DVA_NODE": node, "DVA_PLAYWRIGHT_MCP": str(cli), "DVA_BROWSER_ARGS": "--no-sandbox", "DVA_SECRETS": "file"}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    os.environ.pop("DVA_NODE_IS_ELECTRON", None)
    home = f"http://127.0.0.1:{port}"

    async def scenario():
        from dva.app_state import App
        from dva.tools import ToolContext, ToolDenied, ToolError, browser
        app = App(str(tmp_path / "data"))
        app.db.set_setting("browser_channel", "chrome")
        app.db.set_setting("browser_headless", True)
        events, asked, allow = [], [], [True]

        async def approve(tool, args, description, ignore_auto=False):
            asked.append((tool.name, ignore_auto))
            return allow[0]
        ctx = ToolContext(app=app, chat={"id": "chat_x"}, project=None, workspace=tmp_path / "work", folders=[],
                          readonly=[], emit=lambda kind, **d: events.append((kind, d)), approve=approve)
        (tmp_path / "work").mkdir()
        downloads = tmp_path / "work" / "downloads"
        try:
            page = await browser.browser_open(ctx, f"{home}/index.html")
            assert "Page: Test shop" in page and 'textbox "Your name"' in page and 'button "Greet"' in page
            # the banner was answered with the choice that allows least, and is gone; what loads late is there
            assert "Its cookie banner was answered with “Reject all”." in page and "Accept all" not in page
            assert "Glacier mass balance: 1200 rows" in page
            # the page as an agent reads it: links carry their address, table rows are one line each, and only
            # what can be acted on has a reference
            assert f'link "Price list" [ref=' in page and f"→ {home}/prices.html" in page
            assert "- row: Glaciers | 12" in page and 'heading "Test shop" [level=1]\n' in page
            assert re.search(r"On it: .*", page).group(0).startswith("On it: 2 headings, 5 links, 6 buttons, 5 fields, 1 table.")
            ref = lambda label: re.search(re.escape(label) + r".*?\[ref=(\w+)\]", page).group(1)   # noqa: E731
            typed = await browser.browser_type(ctx, ref('textbox "Your name"'), "Labatt", submit=True)
            assert "Hello, Labatt" in typed
            chosen = await browser.browser_select(ctx, ref('combobox "Colour"'), "Blue")
            assert 'option "Blue" [selected]' in chosen
            got = await browser.browser_click(ctx, ref('link "Download prices"'), "Download prices")
            saved = downloads / "prices.csv"
            assert "Downloaded:" in got and saved.read_text().startswith("item,price")
            assert ("file_changed", {"path": str(saved), "action": "downloaded"}) in events
            other = await browser.browser_click(ctx, text="Price list")              # by its words
            assert "Page: Price list" in other and "Tea 4.50" in other
            assert "paragraph: Tea 4.50" in await browser.browser_look(ctx, "Tea")
            assert "Nothing on the page has the words “coffee”" in await browser.browser_look(ctx, "coffee")
            shot = await browser.browser_screenshot(ctx)
            files = list((tmp_path / "work" / "screenshots").glob("*.png"))
            assert len(files) == 1 and files[0].stat().st_size > 1000 and str(files[0]) in shot
            with pytest.raises(ToolError, match="browser_look"):
                await browser.browser_click(ctx, "e999")          # a reference from an older page
            with pytest.raises(ToolError):
                await browser.browser_open(ctx, "file:///etc/passwd")
            assert "Page: Test shop" in await browser.browser_back(ctx)
            # other ways to look at a page
            assert "The page's structure:" in await browser.browser_look(ctx, show="outline")
            links = await browser.browser_look(ctx, show="links")
            assert f"Annual report → {home}/files/report.pdf  (a file)" in links and "5 links:" in links
            assert "Table 1 “Stock” (3 rows):\nItem | Units\nGlaciers | 12" in await browser.browser_look(ctx, show="tables")
            assert 'spinbutton "Copies"' in await browser.browser_look(ctx, show="forms")
            assert "Glacier mass balance: 1200 rows" in await browser.browser_look(ctx, show="text")
            # words that fit several elements, or none: said, with what there is
            with pytest.raises(ToolError, match="Several elements have the words “e”"):
                await browser.browser_click(ctx, text="e")
            with pytest.raises(ToolError, match='Close: "Download prices"'):
                await browser.browser_click(ctx, text="Download price list")
            # a message of the page is read and closed; a question waits for the agent's answer
            said = await browser.browser_click(ctx, text="Save")
            assert "The page showed a message: “Saved!” (closed)." in said and "Page: Test shop" in said
            asks = await browser.browser_click(ctx, text="Delete")
            assert "The page asks: “Delete everything?”" in asks and "Page:" not in asks
            with pytest.raises(ToolError, match="waits for an answer"):
                await browser.browser_look(ctx)
            kept = await browser.browser_click(ctx, text="Cancel")
            assert "with Cancel" in kept and "paragraph: kept" in kept
            # something that takes a moment
            await browser.browser_click(ctx, text="Compute")
            assert "“late result is 42” is on the page now." in await browser.browser_wait(ctx, text="late result is 42")
            with pytest.raises(ToolError, match="did not happen"):
                await browser.browser_wait(ctx, text="never shown", seconds=1)
            # a whole form at once; ordering asks every time, even where clicks are allowed
            asked.clear()
            done = await browser.browser_fill(ctx, [{"field": "Dataset", "value": "Rainfall"}, {"field": "Copies", "value": "3"},
                                                    {"field": "This is a gift", "value": "true"}], submit="Place order")
            assert "Ordered 3 of Rainfall as a gift" in done and asked == [("browser_pay", True)]
            allow[0] = False
            with pytest.raises(ToolDenied, match="paying or ordering"):
                await browser.browser_click(ctx, text="Place order")
            with pytest.raises(ToolError, match="no field “Shoe size”"):
                await browser.browser_fill(ctx, [{"field": "Shoe size", "value": "9"}])
            # how the page is served, where its data comes from, what it keeps
            told = await browser.browser_inspect(ctx)
            assert "Answer of the server: 200, text/html; made with TestKit 1.0" in told
            assert "the content is in the page as the server sends it" in told
            assert f"- GET {home}/api/items.json → 200" in told
            assert "session [not readable by scripts]" in told and "consent" in told and "from other sites" not in told
            assert f"Annual report → {home}/files/report.pdf" in told and "next page:" in told
            assert f"Form: sends POST to {home}/order with item, qty*, gift" in told
            # files saved by the browser itself: one it would only show, and the data address
            pdf = await browser.browser_save(ctx, text="Annual report")
            assert (downloads / "report.pdf").stat().st_size == 5009 and "application/pdf" in pdf
            await browser.browser_save(ctx, url="/api/items.json")
            assert json.loads((downloads / "items.json").read_text())["items"][0]["rows"] == 1200
            # a link that opens a new tab is followed; tabs are listed and closed
            terms = await browser.browser_click(ctx, text="Terms")
            assert "It opened in a new tab (tab 2 of 2)" in terms and "Page: Terms" in terms
            assert "cookie banner" not in terms                     # "I agree" to terms is not a cookie banner
            assert "- 2: Terms" in await browser.browser_tabs(ctx) and "Page: Test shop" in await browser.browser_tabs(ctx, "close", 2)
            # a page that wants a password
            assert "It asks for a password: never guess one." in await browser.browser_click(ctx, text="Sign in")
            await browser.browser_back(ctx)
            # giving the page one of the user's files: asked every time, and declined here
            note = tmp_path / "work" / "note.txt"
            note.write_text("hello")
            asked.clear()
            with pytest.raises(ToolDenied):
                await browser.browser_upload(ctx, "note.txt", field="Your file")
            assert asked == [("browser_send_file", True)]
            allow[0] = True
            assert "Got note.txt" in await browser.browser_upload(ctx, "note.txt", field="Your file")
            assert not list((app.services.browser_dir / "uploads").iterdir())       # the copy for the helper is gone
            # the setting that leaves banners to the agent
            app.db.set_setting("browser_consent", "ask")
            again = await browser.browser_open(ctx, f"{home}/index.html")
            assert 'button "Reject all"' in again and "was answered" not in again
        finally:
            await app.services.shutdown()
    import re
    try:
        asyncio.run(scenario())
    finally:
        web.shutdown()
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_a_page_is_read_the_way_an_agent_needs_it():
    """The page view without a browser: the helper's snapshot made short and usable."""
    from dva.tools import browser as b
    snap = """### Page
- Page URL: https://shop.example/list
- Page Title: Shop
### Snapshot
```yaml
- generic [active] [ref=e1]:
  - dialog "Cookie consent" [ref=e2]:
    - paragraph [ref=e3]: We use cookies and similar technologies.
    - button "Accept all" [ref=e5]
    - button "Only necessary" [ref=e6]
  - main [ref=e13]:
    - heading "Shop \\"Best\\"" [level=1] [ref=e14]
    - generic [ref=e15]:
      - link "Prices" [ref=e16] [cursor=pointer]:
        - /url: /prices.csv
      - textbox "Email" [ref=e25]:
        - /placeholder: you@example.org
      - generic [ref=e26] [cursor=pointer]: Open menu
    - paragraph
    - table "Stock" [ref=e39]:
      - rowgroup [ref=e41]:
        - row [ref=e42]:
          - columnheader "Item" [ref=e43]
          - columnheader "Units" [ref=e44]
      - rowgroup [ref=e46]:
        - row [ref=e47]:
          - cell "Glaciers" [ref=e48]
          - cell "12" [ref=e49]
        - row [ref=e50]:
          - cell [ref=e51]:
            - link "Rain" [ref=e52] [cursor=pointer]:
              - /url: rain.html
          - cell "7" [ref=e53]
```"""
    assert b.page_head(snap) == ("Shop", "https://shop.example/list")
    nodes = b.parse(snap)
    lines = b.compact(nodes, "https://shop.example/list")
    text = "\n".join(lines)
    assert lines[0] == '- dialog "Cookie consent"' and '  - button "Only necessary" [ref=e6]' in lines
    assert '  - heading "Shop "Best"" [level=1]' in lines                      # no reference: nothing to act on
    assert '  - link "Prices" [ref=e16] → https://shop.example/prices.csv' in lines
    assert '  - textbox "Email" [ref=e25] (placeholder: you@example.org)' in lines
    assert "  - generic [ref=e26]: Open menu" in lines                         # a clickable box keeps its reference
    assert "    - row: Item | Units" in lines and "    - row: Glaciers | 12" in lines
    assert '- link "Rain" [ref=e52] → https://shop.example/rain.html' in text  # a row with a link in it stays whole
    assert "rowgroup" not in text and "cursor" not in text and "[active]" not in text and len(text) < len(snap) * 0.62
    assert b.digest(nodes) == "On it: 1 heading, 2 links, 2 buttons, 1 field, 1 table."
    assert "answered with “Only necessary”" in b.digest(nodes, "Only necessary")
    # the banner's buttons: the choice that allows least is known from its words
    for words in ("Reject all", "Only necessary", "Necessary only", "Decline", "Use necessary cookies only",
                  "Reject non-essential", "Continue without accepting", "No, thanks"):
        assert b.REJECT.match(words), words
    for words in ("Accept all", "Accept", "I agree", "Got it", "OK", "Allow all cookies", "Agree and close"):
        assert b.ACCEPT.match(words) and not b.REJECT.match(words), words
    for words in ("Accept terms and pay", "Reject this offer", "Sign in", "Continue"):
        assert not b.ACCEPT.match(words) and not b.REJECT.match(words), words
    for words in ("Pay now", "Place order", "Buy", "Checkout", "Confirm payment", "Subscribe"):
        assert b.PAY.search(words), words
    assert not b.PAY.search("Download prices") and not b.PAY.search("Display options")
    # finding an element by its words; the parts of a page that have some words; a long page in parts
    assert [n["ref"] for n in b.match(nodes, "prices", b.ACT)] == ["e16"]
    assert [n["ref"] for n in b.match(nodes, "a", b.ACT)] == ["e5", "e6", "e25", "e52"]
    hit = b.found(lines, "glaciers")
    assert hit.startswith("1 place with “glaciers”:") and '- table "Stock"' in hit and "Rain" not in hit
    assert "Nothing on the page has the words “tea”" in b.found(lines, "tea")
    long = [f"- paragraph: line {i} " + "x" * 80 for i in range(300)]
    first, parts = b.paged(long, 1)
    assert parts == 5 and "part 1 of 5" in first and "browser_look(page=2)" in first
    assert "line 299" in b.paged(long, 9)[0] and "browser_look(page=1)" in b.paged(long, 5)[0]
    assert b.dialog_in('- ["confirm" dialog with message "Delete everything?"]: can be handled by browser_handle_dialog') == \
        {"kind": "confirm", "message": "Delete everything?"}
    assert b.result_of('### Result\n{"a": 1}\n### Ran Playwright code\n```js\nx\n```') == {"a": 1}
    groups = {t.name: t.group for t in b.TOOLS}
    assert sum(g == "browser" for g in groups.values()) == 7 and groups["browser_inspect"] == "browsing"


def test_tool_calls_are_repaired_or_refused_with_what_to_change():
    """2.16.0: what a model plainly meant is repaired and said; what cannot be repaired is not run, and the answer
    says what is missing and how the tool is called."""
    from dva.toolcheck import how_to_call, loose_json, repair
    from dva.tools import Tool
    S = lambda props, req, **kw: {"type": "object", "properties": props, "required": req, **kw}  # noqa: E731
    tools = {t.name: t for t in (
        Tool("read_file", "Read a file", S({"path": {"type": "string", "description": "The file to read."},
                                            "max_chars": {"type": "integer"}}, ["path"]), None, "files"),
        Tool("write_file", "Write", S({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]), None, "files"),
        Tool("web_search", "Search", S({"query": {"type": "string"}, "fresh": {"type": "boolean"}}, ["query"]), None, "web"),
        Tool("browser_look", "Look", S({"show": {"type": "string", "enum": ["page", "outline", "links"]}}, []), None, "browser"),
        Tool("make_list", "List", S({"items": {"type": "array", "items": {"type": "string"}}, "count": {"type": "number"}}, ["items"]), None, "files"),
        Tool("notes__add", "A connector's tool", S({"title": {"type": "string"}}, ["title"]), None, "connector", source="connector:notes"),
        Tool("free", "No parameters said", {"type": "object"}, None, "connector", source="connector:x"))}
    ok = repair(tools, "read_file", {"path": "a.txt", "max_chars": 500})
    assert ok == {"name": "read_file", "args": {"path": "a.txt", "max_chars": 500}, "notes": [], "error": None}   # untouched
    # the tool's name
    got = repair(tools, "Read-File", {"path": "a.txt"})
    assert got["name"] == "read_file" and got["notes"] == ["`Read-File` was read as the tool `read_file`."]
    assert repair(tools, "functions.web_search", {"query": "x"})["name"] == "web_search"
    assert repair(tools, "web_serch", {"query": "x"})["name"] == "web_search"
    none = repair(tools, "open_door", {})
    assert none["error"].startswith("There is no tool called 'open_door'.") and "read_file" in none["error"]
    # the parameters' names, the wrapping, the types
    got = repair(tools, "read_file", {"file": "a.txt", "max_chars": "500", "colour": "red"})
    assert got["args"] == {"path": "a.txt", "max_chars": 500} and not got["error"]
    assert got["notes"] == ["`file` was read as `path`.", "`colour` is not a parameter of read_file and was left out."]
    got = repair(tools, "web_search", {"arguments": {"q": "glaciers", "fresh": "yes"}})
    assert got["args"] == {"query": "glaciers", "fresh": True} and "inside `arguments`" in got["notes"][0]
    assert repair(tools, "make_list", {"items": "tea", "count": "2.5"})["args"] == {"items": ["tea"], "count": 2.5}
    assert repair(tools, "make_list", {"items": '["tea", "milk"]'})["args"] == {"items": ["tea", "milk"]}
    assert repair(tools, "read_file", {"path": "a.txt", "max_chars": None})["args"] == {"path": "a.txt"}
    assert repair(tools, "read_file", {"Path": "a.txt"})["args"] == {"path": "a.txt"}
    assert repair(tools, "write_file", {"path": "a.txt", "content": ""})["error"] is None      # an empty file is a file
    assert repair(tools, "write_file", {"path": "a.txt", "text": "hi"})["args"] == {"path": "a.txt", "content": "hi"}
    # arguments that are almost JSON, or just the one thing the tool needs
    assert loose_json("```json\n{'path': 'a.txt',}\n```") == {"path": "a.txt"} and loose_json('{"a": [1, 2,], "b": {"c": 1') == {"a": [1, 2], "b": {"c": 1}}
    got = repair(tools, "read_file", {"_unparsed": '{"path": "a.txt",}'})
    assert got["args"] == {"path": "a.txt"} and got["notes"] == ["The arguments were not valid JSON and were repaired."]
    assert repair(tools, "read_file", {"_unparsed": "a.txt"})["args"] == {"path": "a.txt"}
    bad = repair(tools, "write_file", {"_unparsed": "path=a.txt content=hi"})
    assert bad["error"].startswith("The arguments were not valid JSON") and "write_file(path, content)" in bad["error"]
    # a value outside the choices: the nearest one, else said
    assert repair(tools, "browser_look", {"show": "Outline"})["args"] == {"show": "outline"}
    wrong = repair(tools, "browser_look", {"show": "everything"})
    assert wrong["error"] == "Error: 'everything' is not a value `show` of browser_look can have. It is one of: page, outline, links."
    # what is missing is said, with how the tool is called
    miss = repair(tools, "read_file", {"max_chars": 10})
    assert miss["error"].startswith("Error: read_file was not run: it needs `path`, which is missing.")
    assert "How to call it: read_file(path, max_chars?)\n- path (string, required): The file to read.\n- max_chars (integer)" in miss["error"]
    assert "which are missing" in repair(tools, "write_file", {})["error"]
    # a connector's tool may take more than it says; one that says nothing is not checked
    assert repair(tools, "notes__add", {"title": "x", "tags": ["a"]})["args"] == {"title": "x", "tags": ["a"]}
    assert repair(tools, "free", {"anything": 1})["args"] == {"anything": 1}
    assert how_to_call(tools["free"]) == "How to call it: free() — it takes no arguments"


def test_a_nearly_right_call_runs_and_the_browser_specialist_has_its_own_kit(engine):
    """2.16.0, end to end: an agent's call with the wrong tool spelling, the arguments wrapped and a parameter by
    another name is repaired, run, and the agent is told what was read as what; a call that lacks what the tool
    needs is not run and says how the tool is called. The Browser harness has the specialist's tools; an
    ordinary agent does not get them."""
    c, tmp = engine["client"], engine["tmp"]
    folder = tmp / "calls"
    folder.mkdir()
    (folder / "note.txt").write_text("the note says hello")
    chat = c.post("/api/chats", json={"harness": "agent", "folders": [str(folder)]}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "make a sloppy call for note.txt please"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["name"] == "read_file" and step["ok"] and step["arguments"] == {"path": "note.txt"}, step
    assert "the note says hello" in step["result"] and "`file` was read as `path`." in step["result"]
    assert "`max_chars` is not a parameter of read_file and was left out." in step["result"]
    assert step["repaired"][0] == "`Read-File` was read as the tool `read_file`."
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "now a call without a path"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert not step["ok"] and step["result"].startswith("Error: read_file was not run: it needs `path`, which is missing.")
    assert "How to call it: read_file(path" in step["result"]
    # the Browser harness: its method, and a kit of its own
    listed = {h["id"]: h for h in c.get("/api/harnesses").json()["harnesses"]}
    assert listed["browser"]["tools"] == ["browser", "browsing", "web", "files"] and "browsing" not in listed["agent"]["tools"]
    chat = c.post("/api/chats", json={"harness": "browser"}).json()
    shown = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "show system prompt"}).json()
    assert wait_job(c, shown["job_id"])["status"] == "done"
    said = last_assistant(c, chat["id"])["content"]
    assert "Prefer the data to the page." in said and "never guess a user name or a password" in said


def test_a_cut_off_long_task_continues_by_itself_but_not_forever(tmp_path, monkeypatch):
    """After a restart a long-running task goes on without being asked (a planned run only if the user chose
    that). One that cuts the app off three times in a row since its last accepted step is left alone."""
    import asyncio
    from dva.app_state import App
    from dva.db import now
    monkeypatch.setenv("DVA_SECRETS", "file")

    async def scenario():
        app = App(str(tmp_path / "data"))
        app.db.set_setting("resume_delay_seconds", 0)
        for rid, kind, tries in (("r_task", "mission", 0), ("r_plan", "run", 0), ("r_loop", "mission", 3)):
            chat = app.create_chat(title=rid)
            app.db.insert("runs", {"id": rid, "chat_id": chat["id"], "status": "running", "kind": kind,
                                   "state": {"tasks": [], "mission": {"auto_resumes": tries}}, "created": now(),
                                   "updated": now()})
        resumed = []
        monkeypatch.setattr(app, "resume_run", lambda chat_id: resumed.append(app.db.get("chats", chat_id)["title"]))
        await app.start()
        await asyncio.sleep(0.3)
        rows = {r["id"]: r for r in app.db.all("SELECT * FROM runs")}
        assert all(r["status"] == "interrupted" for r in rows.values())
        assert resumed == ["r_task"]                       # not the planned run, not the task that keeps failing
        assert rows["r_task"]["state"]["mission"]["auto_resumes"] == 1
        app.db.set_setting("mission_auto_resume", False)
        app.db.update("runs", "r_task", {"status": "running"})
        for t in app._bg:
            t.cancel()
        await app.start()
        await asyncio.sleep(0.3)
        assert resumed == ["r_task"]                       # switched off in Settings: it waits for Continue
        for t in app._bg:
            t.cancel()
        await app.runtime.shutdown()
    asyncio.run(scenario())


# ---------------------------------------------------------------- 2.10.0: MCP servers set up like in other apps
def test_mcp_configurations_of_other_apps_are_understood(tmp_path, monkeypatch):
    from dva import mcp_config as mc
    claude = mc.parse('{"mcpServers": {"files": {"command": "npx", "args": ["-y", "@x/files", "C:/data"], '
                      '"env": {"API_KEY": "abc"}}, "off": {"command": "uvx", "args": ["thing"], "disabled": true}}}')
    assert [(e["name"], e["transport"], e["enabled"]) for e in claude] == [("files", "stdio", True), ("off", "stdio", False)]
    assert claude[0]["args"] == ["-y", "@x/files", "C:/data"] and claude[0]["env"] == {"API_KEY": "abc"}
    vscode = mc.parse("""{
      // VS Code writes comments, and calls the map "servers"
      "servers": {
        "docs": {"type": "http", "url": "https://example.com/mcp", "headers": {"X-Team": "a//b", "Authorization": "Bearer ${env:DOCS_TOKEN}"}},
        "old":  {"type": "sse", "url": "https://example.com/events",},   /* trailing commas too */
      },
    }""")
    assert vscode[0]["type"] == "http" and vscode[0]["headers"]["X-Team"] == "a//b"      # "//" inside a string stays
    assert vscode[0]["auth"] == "none" and "Authorization" in vscode[0]["headers"]       # a reference, not a value
    assert vscode[1]["type"] == "sse"
    cursor = mc.parse('{"mcpServers": {"api": {"url": "https://api.example.com/sse", "headers": {"Authorization": "Bearer sk-1"}}}}')[0]
    assert (cursor["type"], cursor["auth"], cursor["token"], cursor["headers"]) == ("sse", "bearer", "sk-1", {})
    zed = mc.parse('{"context_servers": {"db": {"command": {"path": "db-mcp", "args": ["--ro"], "env": {"DSN": "x"}}}}}')[0]
    assert (zed["command"], zed["args"], zed["env"]) == ("db-mcp", ["--ro"], {"DSN": "x"})
    bridged = mc.parse('{"mcpServers": {"notion": {"command": "npx", "args": ["-y", "mcp-remote", "https://mcp.example.com/mcp"]}}}')[0]
    assert (bridged["transport"], bridged["url"], bridged["auth"]) == ("http", "https://mcp.example.com/mcp", "oauth")
    assert "mcp-remote" in bridged["note"]
    assert mc.parse('{"command": "python", "args": ["server.py"]}')[0]["name"] == "server"       # one bare entry
    assert mc.parse('{"a": {"command": "x"}, "b": {"url": "https://b.example/mcp"}}')[1]["transport"] == "http"
    for bad in ('{"name": "nothing here"}', '[1, 2]', '{"mcpServers": {"x": {"note": "no command"}}}'):
        with pytest.raises(ValueError):
            mc.parse(bad)
    monkeypatch.setenv("DOCS_TOKEN", "tok")
    missing = set()
    assert mc.expand_vars("Bearer ${env:DOCS_TOKEN} ${REGION:-eu} ${NOT_SET}", missing=missing) == "Bearer tok eu "
    assert missing == {"NOT_SET"} and mc.expand_vars("${K}", {"K": "from-values"}) == "from-values"
    out = mc.export([{"name": "My Files", "transport": "stdio", "command": "{python}", "args": ["s.py"],
                      "env": {"API_KEY": "abc", "REGION": "eu"}},
                     {"name": "Docs", "transport": "http", "type": "http", "url": "https://d.example/mcp",
                      "auth": "bearer", "headers": {"X-Team": "blue"}}])["mcpServers"]
    assert out["my-files"] == {"command": "python", "args": ["s.py"], "env": {"API_KEY": "${API_KEY}", "REGION": "eu"}}
    assert out["docs"]["headers"] == {"X-Team": "blue", "Authorization": "Bearer ${DOCS_TOKEN}"}     # no secret leaves
    # what other apps on this computer have set up
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "mcp.json").write_text('{"mcpServers": {"search": {"command": "npx", "args": ["s"], "env": {"KEY": "v"}}}}')
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"gh": {"type": "http", "url": "https://gh.example/mcp"}},
                                                    "projects": {"/work/site": {"mcpServers": {"db": {"command": "db"}}}}}))
    roaming = tmp_path / "roaming"
    (roaming / "Claude").mkdir(parents=True)
    (roaming / "Claude" / "claude_desktop_config.json").write_text("{not json")          # a broken file is skipped
    found = mc.find_configs(home, {"APPDATA": str(roaming)})
    assert [(f["app"], [s["name"] for s in f["servers"]]) for f in found] == \
        [("Claude Code", ["gh"]), ("Claude Code · site", ["db"]), ("Cursor", ["search"])]
    assert mc.masked(found[2]["servers"][0])["env"] == {"KEY": "••••"}


def test_connectors_are_taken_over_tested_and_used(engine):
    c = engine["client"]
    c.put("/api/settings", json={"max_native_tools": 60})        # this server's eight tools stay native
    config = json.dumps({"mcpServers": {"Notes": {"command": "{python}", "args": [str(TESTS / "fake_mcp_server.py"), "--full"],
                                                  "env": {"API_KEY": "s3cret-value"}}}})
    seen = c.post("/api/connectors-preview", json={"text": config}).json()["servers"]
    assert seen[0]["name"] == "Notes" and seen[0]["env"] == {"API_KEY": "••••"}
    assert c.post("/api/connectors-preview", json={"text": "not a config"}).status_code == 400
    got = c.post("/api/connectors-import", json={"text": config}).json()
    cid = got["added"][0]["id"]
    assert c.post("/api/connectors-import", json={"text": config}).json() == \
        {"added": [], "skipped": [{"name": "Notes", "why": "already a connector here"}]}
    row = next(x for x in c.get("/api/connectors").json()["connectors"] if x["id"] == cid)
    assert row["env"] == {"API_KEY": ""} and row["type"] == "stdio" and "s3cret-value" not in json.dumps(row)
    assert "s3cret-value" not in (engine["tmp"] / "data" / "kimilm.sqlite3").read_bytes().decode("latin-1")
    t = c.post(f"/api/connectors/{cid}/test").json()
    assert t["ok"] and t["info"]["name"] == "test" and t["info"]["version"] == "9.9.9", t
    assert t["info"]["protocol"] == "2026-07-28" and set(t["info"]["capabilities"]) == {"tools", "resources", "prompts"}
    tools = {x["name"]: x for x in t["tools"]}
    assert tools["wipe"]["title"] == "Wipe everything" and tools["wipe"]["destructive"] and not tools["wipe"]["readonly"]
    assert tools["snapshot"]["readonly"] and not tools["set_value"]["readonly"]
    assert [r["uri"] for r in t["resources"]] == ["notes://today", "notes://{day}"] and t["resources"][1]["template"]
    assert t["prompts"][0]["name"] == "summarise" and t["prompts"][0]["arguments"][0]["required"]
    assert c.post(f"/api/connectors/{cid}/prompt", json={"name": "summarise", "arguments": {"topic": "tea"}}).json()["text"] \
        == "Summarise what is known about tea in three sentences."
    assert c.get(f"/api/connectors/{cid}/resource", params={"uri": "notes://friday"}).json()["text"] == "Nothing happened on friday."
    shown = next(x for x in c.get("/api/connectors").json()["connectors"] if x["id"] == cid)
    assert shown["info"]["protocol"] == "2026-07-28"                                   # kept for the window
    assert c.get("/api/connectors-export", params={"ids": cid}).json()["mcpServers"]["notes"]["env"] == {"API_KEY": "${API_KEY}"}
    # an agent reads a resource of the server (two tools for all servers with resources), without being asked
    chat = c.post("/api/chats", json={"harness": "agent", "connectors": [cid]}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "read resource notes://today"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"][0]
    assert step["name"] == "read_connector_resource" and step["ok"] and "Today the value was" in step["result"]
    # the value saved in the credential store reaches the server; changing other fields keeps it
    c.put(f"/api/connectors/{cid}/config", json={"env": {"API_KEY": "", "REGION": "eu"}, "description": "notes"})
    row = next(x for x in c.get("/api/connectors").json()["connectors"] if x["id"] == cid)
    assert row["env"] == {"API_KEY": "", "REGION": ""}
    assert c.delete(f"/api/connectors/{cid}/delete").json()["ok"]
    c.put("/api/settings", json={"max_native_tools": 49})


def test_remote_connectors_send_headers_speak_both_transports_and_sign_in(tmp_path, monkeypatch):
    """Streamable HTTP with a key and a custom header, the older SSE transport, and OAuth: the browser is opened
    only when a person asked for it, and a later run uses the saved sign-in."""
    import asyncio
    import threading
    import webbrowser
    monkeypatch.setenv("DVA_SECRETS", "file")
    ports = [free_port() for _ in range(3)]
    server = str(TESTS / "fake_mcp_server.py")
    procs = [subprocess.Popen([sys.executable, server, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for args in (["--full", "--http", str(ports[0])], ["--full", "--sse", str(ports[1])],
                          ["--full", "--http", str(ports[2]), "--oauth"])]
    for port in ports:
        for _ in range(150):
            with socket.socket() as sock:
                sock.settimeout(0.3)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
    opened = []

    def person_signs_in(url, *a, **k):        # stands in for the browser: the test authorization server says yes
        opened.append(url)

        def follow():
            # R125 (2.20.0): the client opens the browser before its callback server listens. A browser waits for a
            # page; a plain request was refused and the sign-in then waited 300 s. Each hop is tried until it answers.
            nxt = url
            for _ in range(10):
                for _ in range(150):
                    try:
                        r = httpx.get(nxt, timeout=20, trust_env=False)
                        break
                    except httpx.ConnectError:
                        time.sleep(0.1)
                else:
                    return
                if not r.is_redirect:
                    return
                nxt = str(r.url.join(r.headers["location"]))
        threading.Thread(target=follow, daemon=True).start()
        return True
    monkeypatch.setattr(webbrowser, "open", person_signs_in)

    async def scenario():
        from dva.app_state import App
        app = App(str(tmp_path / "data"))
        cn = app.connectors
        try:
            monkeypatch.setenv("TEAM_NAME", "blue")
            http = cn.create({"name": "Remote", "url": f"http://127.0.0.1:{ports[0]}/mcp", "token": "tok-1",
                              "headers": {"X-Api-Key": "abc123", "X-Team": "${TEAM_NAME}"}})
            assert http["transport"] == "http" and http["type"] == "http" and http["auth"] == "bearer"
            assert (await cn.test(http["id"]))["info"]["protocol"] == "2026-07-28"
            for name, value in (("X-Api-Key", "abc123"), ("X-Team", "blue"), ("Authorization", "Bearer tok-1")):
                assert (await cn.call(http["id"], "show_header", {"name": name}))[1] == json.dumps({"result": value})
            ok, text = await cn.call(http["id"], "snapshot", {}, save_dir=tmp_path / "ws")
            saved = list((tmp_path / "ws" / "connector-files").glob("image-*.png"))
            assert ok and len(saved) == 1 and str(saved[0]) in text                 # a picture becomes a file
            sse = cn.create({"name": "Older", "type": "sse", "url": f"http://127.0.0.1:{ports[1]}/sse"})
            assert len((await cn.test(sse["id"]))["tools"]) == 6
            signed = cn.create({"name": "Signed", "url": f"http://127.0.0.1:{ports[2]}/mcp", "auth": "oauth"})
            assert not cn.get(signed["id"])["ready"]
            with pytest.raises(ConnectionError, match="sign in first"):
                await cn.list_tools(signed["id"])                                   # nobody is there: no browser
            assert opened == []
            assert len((await cn.test(signed["id"]))["tools"]) == 6 and len(opened) == 1   # the person pressed Sign in
            assert cn.get(signed["id"])["ready"] and cn.get(signed["id"])["signed_in"]
            await cn.reset(signed["id"])
            assert len(await cn.list_tools(signed["id"], refresh=True)) == 6 and len(opened) == 1   # saved sign-in
            await cn.sign_out(signed["id"])
            assert not cn.get(signed["id"])["signed_in"] and not list((app.paths.data / "mcp-auth").iterdir())
            missing = cn.create({"name": "Needs a value", "url": f"http://127.0.0.1:{ports[0]}/mcp",
                                 "headers": {"X-Key": "${KIMILM_NOT_SET_ANYWHERE}"}})
            with pytest.raises(ConnectionError, match="KIMILM_NOT_SET_ANYWHERE"):
                await cn.test(missing["id"])
        finally:
            await cn.shutdown()
            await app.runtime.shutdown()
    try:
        asyncio.run(scenario())
    finally:
        for proc in procs:
            proc.terminate()


def test_an_mcp_bundle_installs_as_a_plugin(engine):
    """A .mcpb file (manifest.json + the server's files) becomes a plugin: its user_config are the plugin's
    settings (sensitive ones in the credential store) and its server a connector."""
    c = engine["client"]
    manifest = {"manifest_version": "0.3", "name": "Note Keeper", "display_name": "Note keeper", "version": "1.2.0",
                "description": "Keeps notes.", "author": {"name": "Tester"},
                "server": {"type": "python", "entry_point": "server/main.py",
                           "mcp_config": {"command": "no-such-program", "args": ["${__dirname}/server/main.py"],
                                          "env": {"API_KEY": "${user_config.api_key}", "WHERE": "${__dirname}"},
                                          "platform_overrides": {p: {"command": sys.executable} for p in ("win32", "darwin", "linux")}}},
                "user_config": {"api_key": {"type": "string", "title": "API key", "sensitive": True, "required": True},
                                "folder": {"type": "directory", "title": "Notes folder", "default": "${HOME}/notes"}}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("server/main.py", (TESTS / "fake_mcp_server.py").read_text())
    p = c.post("/api/plugins-upload", files={"file": ("note-keeper.mcpb", buf.getvalue())}).json()
    assert p["id"] == "note-keeper" and p["name"] == "Note keeper" and p["version"] == "1.2.0", p
    assert [(f["key"], f["type"]) for f in p["settings"]] == [("api_key", "secret"), ("folder", "folder")]
    assert not p["configured"]
    conn = next(x for x in c.get("/api/connectors").json()["connectors"] if x["id"] == "note-keeper:note-keeper")
    assert conn["name"] == "Note keeper" and not conn["ready"] and conn["source"] == "plugin:note-keeper"
    c.put("/api/plugins/note-keeper/settings", json={"api_key": "bundle-secret"})
    t = c.post("/api/connectors/note-keeper:note-keeper/test").json()
    assert t["ok"] and len(t["tools"]) == 2, t
    chat = c.post("/api/chats", json={"harness": "agent", "connectors": ["note-keeper:note-keeper"]}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "whoami please"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    assert "tester" in [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"][0]["result"]
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("readme.txt", "nothing")
    assert c.post("/api/plugins-upload", files={"file": ("x.zip", bad.getvalue())}).status_code == 400


def test_a_bundle_gets_its_folder_and_settings_and_missing_programs_are_named(tmp_path, monkeypatch):
    from dva import mcp_config as mc
    from dva.mcp import resolve_command
    plugin = mc.bundle_to_plugin({"name": "x-tool", "version": "1", "server": {"type": "node", "entry_point": "server/index.js"}})
    assert plugin["mcpServers"]["x-tool"] == {"command": "node", "args": ["${__dirname}/server/index.js"], "env": {}, "description": ""}
    assert mc.fill_bundle_vars("${__dirname}/a ${user_config.key} ${user_config.none} ${OTHER}", "/b", {"key": "v"}) == "/b/a v  ${OTHER}"
    with pytest.raises(ValueError, match="made for"):
        mc.bundle_to_plugin({"name": "mac-only", "server": {"mcp_config": {"command": "x"}},
                             "compatibility": {"platforms": ["plan9"]}})
    monkeypatch.setenv("PATH", str(tmp_path))                     # nothing is installed here
    monkeypatch.delenv("DVA_NODE", raising=False)
    with pytest.raises(ValueError, match=r"npx.*not installed.*Node\.js"):
        resolve_command("npx")
    with pytest.raises(ValueError, match="uv "):
        resolve_command("uvx")
    assert resolve_command("python")[0] == sys.executable          # the app's own Python
    monkeypatch.setenv("DVA_NODE", "/app/KimiLM.exe")
    monkeypatch.setenv("DVA_NODE_IS_ELECTRON", "1")
    assert resolve_command("node") == ("/app/KimiLM.exe", {"ELECTRON_RUN_AS_NODE": "1"})   # the app's own Node



# ---------------------------------------------------------------------------------------------- pictures (2.11.0)
def wait_picture(c, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = next(j for j in c.get("/api/images").json()["jobs"] if j["id"] == job_id)
        if job["state"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError(f"picture job still running: {job}")


def asked(path):
    """What the stand-in image program was asked for (it writes that into the PNG)."""
    from PIL import Image
    with Image.open(path) as img:
        return json.loads(img.text["asked"]), img.size


def test_pictures_are_made_and_changed_on_this_computer(engine):
    c, tmp = engine["client"], engine["tmp"]
    start = c.get("/api/images").json()
    assert start["engines"]["installed"] == ["custom"] and start["engines"]["tag"].startswith("master-")
    assert not start["models"] and start["default"] is None
    assert {"z-image-turbo", "sd15", "flux2-klein-4b"} <= {r["key"] for r in start["recipes"]}
    assert all(r["licence"] and r["page"].startswith("https://") for r in start["recipes"])
    assert "does not make" in start["claude"]
    # no image model yet: an agent is not offered the picture tools, and the window says what to do
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "paint a picture of a red kite"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    assert not [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"]
    none = wait_picture(c, c.post("/api/images/generate", json={"prompt": "a red kite"}).json()["id"])
    assert none["state"] == "failed" and "No image model is set up" in none["error"]
    assert c.post("/api/images/generate", json={"prompt": " "}).status_code == 400

    # a model whose file is already on this computer is used where it is
    weights = tmp / "checkpoints" / "tiny-sd.safetensors"
    weights.parent.mkdir()
    weights.write_bytes(b"\0" * 4096)
    assert c.post("/api/images/models", json={"name": "x", "files": {"model": str(tmp / "nope.safetensors")}}).status_code == 400
    assert c.post("/api/images/models", json={"name": "x", "files": {"vae": str(weights)}}).status_code == 400
    model = c.post("/api/images/models", json={"name": "Tiny SD", "files": {"model": str(weights)},
                                               "settings": {"steps": 6, "cfg": 5.5}}).json()
    assert model["id"] == "tiny-sd" and model["default"] and model["ready"] and model["size"] == 4096
    made = wait_picture(c, c.post("/api/images/generate", json={
        "prompt": "a red kite over a green hill", "negative": "people", "width": 650, "height": 480, "seed": 7,
        "count": 2}).json()["id"])
    assert made["state"] == "done" and len(made["images"]) == 2, made
    first, size = asked(made["images"][0]["path"])
    assert size == (640, 480)                                     # sizes are rounded to what the models accept
    assert first["model"] == str(weights) and first["prompt"] == "a red kite over a green hill"
    # each picture is a run of the engine of its own, so that a finished one is kept whatever happens to the
    # next; picture i is drawn with seed + i, as the engine does within a batch
    assert (first["seed"], first["count"], first["steps"], first["cfg"], first["negative"]) == ("7", "1", "6", "5.5", "people")
    second, _ = asked(made["images"][1]["path"])
    assert (second["seed"], second["count"]) == ("8", "1") and made["images"][1]["params"]["seed"] == 8
    assert made["images"][0]["path"].endswith("-01.png") and made["images"][1]["path"].endswith("-02.png")
    assert made["images"][0]["params"]["seed"] == 7 and made["images"][0]["params"]["mode"] == "new"
    assert made["images"][0]["params"]["engine"] == "custom" and made["images"][1]["params"]["index"] == 1
    assert Path(made["images"][0]["path"]).parent == tmp / "data" / "images"
    assert c.get("/api/file", params={"path": made["images"][0]["path"]}).headers["content-type"] == "image/png"
    # no seed given: one is chosen and recorded, so the picture can be made again
    again = wait_picture(c, c.post("/api/images/generate", json={"prompt": "a blue kite"}).json()["id"])
    assert again["images"][0]["params"]["seed"] > 0 and again["images"][0]["params"]["steps"] == 6

    # changing a picture: redrawn from the original; with a mask only there; a model that follows instructions
    # about a picture is given it as a reference instead
    source = made["images"][0]["path"]
    told = wait_picture(c, c.post("/api/images/generate", json={"prompt": "make the kite blue", "image": source,
                                                                "strength": 0.4}).json()["id"])
    # an instruction to a model that can only redraw fails at once and says what to do (2.15.0): it does not
    # spend minutes on a different picture
    assert told["state"] == "failed" and "cannot follow an instruction" in told["error"] and "redraw" in told["error"]
    redo = wait_picture(c, c.post("/api/images/generate", json={"prompt": "make the kite blue", "image": source,
                                                                "strength": 0.4, "redraw": True}).json()["id"])
    a, _ = asked(redo["images"][0]["path"])
    assert a["init"] == source and a["strength"] == "0.4" and not a["refs"] and redo["images"][0]["params"]["mode"] == "redraw"
    part = wait_picture(c, c.post("/api/images/generate", json={"prompt": "a cloud", "image": source, "mask": source}).json()["id"])
    a, _ = asked(part["images"][0]["path"])
    assert a["mask"] == source and part["images"][0]["params"]["mode"] == "inpaint"
    editor = c.post("/api/images/models", json={"name": "Tiny Edit", "files": {"diffusion_model": str(weights), "vae": str(weights)},
                                                "edit": "reference", "settings": {"steps": 4, "cfg": 1.0, "args": "--offload-to-cpu"}}).json()
    edit = wait_picture(c, c.post("/api/images/generate", json={"prompt": "remove the hill", "image": source,
                                                                "model": editor["id"]}).json()["id"])
    a, _ = asked(edit["images"][0]["path"])
    assert a["refs"] == [source] and "init" not in a and a["diffusion_model"] == str(weights) and a["vae"] == str(weights)
    assert "--offload-to-cpu" in a["flags"] and edit["images"][0]["params"]["mode"] == "edit"

    # failures are said in plain words
    oom = wait_picture(c, c.post("/api/images/generate", json={"prompt": "out of memory please"}).json()["id"])
    assert oom["state"] == "failed" and "ran out of memory" in oom["error"]
    c.put(f"/api/images/models/{editor['id']}", json={"settings": {"args": "--no-such-option"}})
    bad = wait_picture(c, c.post("/api/images/generate", json={"prompt": "a kite", "model": editor["id"]}).json()["id"])
    assert bad["state"] == "failed" and "did not accept its options" in bad["error"] and "--no-such-option" in bad["error"]
    slow = c.post("/api/images/generate", json={"prompt": "slow picture of a kite"}).json()
    time.sleep(0.6)
    assert c.get("/api/busy").json()["active"] >= 1               # the computer is kept awake meanwhile
    c.delete(f"/api/images/jobs/{slow['id']}")
    assert wait_picture(c, slow["id"])["state"] == "stopped"

    # the library remembers every picture with what made it; forgetting one can delete its file
    library = c.get("/api/images/library").json()["images"]
    assert len(library) == 6 and library[0]["prompt"] == "remove the hill" and library[0]["model_name"] == "Tiny Edit"
    c.delete(f"/api/images/library/{library[0]['id']}", params={"file": 1})
    assert not Path(library[0]["path"]).exists() and len(c.get("/api/images/library").json()["images"]) == 5
    # choosing the default, removing a model (files the app did not download stay where they are)
    c.put(f"/api/images/models/{editor['id']}", json={"default": True, "settings": {"args": ""}})
    assert c.get("/api/images").json()["default"] == editor["id"]
    c.delete(f"/api/images/models/{editor['id']}", params={"files": 1})
    state = c.get("/api/images").json()
    assert [m["id"] for m in state["models"]] == ["tiny-sd"] and state["default"] == "tiny-sd" and weights.exists()
    assert c.put("/api/images/settings", json={"image_compute": "gpu"}).status_code == 400
    assert c.put("/api/images/settings", json={"image_compute": "cpu", "image_timeout_minutes": 5}).json()["timeout_minutes"] == 5
    c.put("/api/images/settings", json={"image_compute": "auto", "image_timeout_minutes": 60})


def test_an_agent_makes_a_picture_and_changes_it(engine):
    c, tmp = engine["client"], engine["tmp"]
    folder = tmp / "picture-project"
    folder.mkdir()
    chat = c.post("/api/chats", json={"harness": "agent", "folders": [str(folder)]}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "paint a picture of a red kite"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    msg = last_assistant(c, chat["id"])
    step = next(s for s in msg["meta"]["steps"] if s["type"] == "tool")
    assert step["name"] == "generate_image" and step["ok"] and step["label"] == "Make picture", step
    picture = step["media"][0]
    assert picture["kind"] == "image" and picture["model"] == "Tiny SD" and picture["prompt"] == "a red kite"
    assert Path(picture["path"]).parent == folder / "images" and Path(picture["path"]).exists()   # in the chat's folder
    assert picture["path"] in step["result"] and "look_at_image" in step["result"]
    assert c.get("/api/images/library", params={"chat_id": chat["id"]}).json()["images"][0]["id"] == picture["id"]
    # the next request changes that picture; it keeps its shape
    job = c.post(f"/api/chats/{chat['id']}/messages",
                 json={"text": f"repaint {picture['path']} so that the kite is blue"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["name"] == "edit_image" and step["ok"], step
    a, size = asked(step["media"][0]["path"])
    assert a["init"] == picture["path"] and a["prompt"] == "the kite is blue" and size == (512, 512)
    assert a["steps"] == "6" and a["strength"] == "0.6"            # (the engine then draws 4 of the 6 steps)
    # a picture outside the chat's folders is not touched
    job = c.post(f"/api/chats/{chat['id']}/messages",
                 json={"text": f"repaint {tmp / 'checkpoints' / 'tiny-sd.safetensors'} so that it is blue"}).json()
    wait_job(c, job["job_id"])
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert not step["ok"] and "outside the folders" in step["result"] and "media" not in step


def test_simple_changes_need_no_model_and_instructions_go_to_a_model_that_follows_them(engine):
    """(2.15.0) "Make the picture glow on its outline" is an image operation: done in code in a moment, at the
    picture's own size and in its own format. An instruction that is more than that goes to a model that follows
    instructions when one is installed; and the bar of a redraw follows the steps the engine really does."""
    from PIL import Image, ImageDraw
    c, tmp = engine["client"], engine["tmp"]
    folder = tmp / "logo-project"
    folder.mkdir()
    src = folder / "logo.png"
    img = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((100, 50, 200, 150), fill=(200, 40, 40, 255))
    img.save(src)
    job = wait_picture(c, c.post("/api/images/generate", json={
        "prompt": "Make the picture glow on its outline. Deliver in the same format.", "image": str(src), "count": 2}).json()["id"])
    assert job["state"] == "done" and len(job["images"]) == 1, job
    p = job["images"][0]
    assert p["params"]["mode"] == "effect" and p["params"]["effect"] == "glow" and p["source"] == "code"
    assert p["model_name"].startswith("KimiLM itself") and p["params"]["seconds"] < 5
    out = Image.open(p["path"])
    assert out.size == (300, 200) and out.format == "PNG" and out.getpixel((150, 100)) == (200, 40, 40, 255)
    assert out.getpixel((96, 100))[3] > 60 and out.getpixel((5, 5))[3] == 0
    task = c.get(f"/api/jobs/{job['job_id']}").json()
    assert task["detail"]["stages"] == ["Change the picture", "Save"] and task["detail"]["picture"]["change"] == "a glow on its outline"
    assert any("no image model is needed" in e["text"] for e in task["trail"])
    # an agent asked for a simple change: the same way, and it says so
    chat = c.post("/api/chats", json={"harness": "agent", "folders": [str(folder)]}).json()
    run = c.post(f"/api/chats/{chat['id']}/messages", json={"text": f"repaint {src} so that it is black and white"}).json()
    assert wait_job(c, run["job_id"])["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["ok"] and "Changed the picture in code" in step["result"] and step["media"][0]["params"]["effect"] == "grayscale"
    # more than an effect: the model that follows instructions takes it, when nobody chose the model
    weights = tmp / "checkpoints" / "tiny-sd.safetensors"
    editor = c.post("/api/images/models", json={"name": "Tiny Edit 2", "files": {"diffusion_model": str(weights), "vae": str(weights)},
                                                "edit": "reference", "settings": {"steps": 4, "cfg": 1.0}}).json()
    c.put("/api/images/models/tiny-sd", json={"default": True})      # the default is the model that only redraws
    done = wait_picture(c, c.post("/api/images/generate", json={"prompt": "remove the red circle and add a tree",
                                                                "image": str(src)}).json()["id"])
    assert done["state"] == "done", done
    a, _ = asked(done["images"][0]["path"])
    assert a["refs"] == [str(src)] and done["images"][0]["model_name"] == "Tiny Edit 2"
    note = c.get(f"/api/jobs/{done['job_id']}").json()["trail"]
    assert any("Tiny Edit 2 is used instead of Tiny SD" in e["text"] for e in note), [e["text"] for e in note if e["kind"] != "output"]
    c.delete(f"/api/images/models/{editor['id']}")
    # a redraw of 8 steps at strength 0.5 draws 5: the bar counts what the engine does, for each picture in turn
    slow = c.post("/api/images/generate", json={"prompt": "a slow picture, redrawn", "image": str(src), "redraw": True,
                                                "steps": 8, "strength": 0.5, "count": 2}).json()
    seen = None
    for _ in range(150):
        rows = c.get("/api/jobs", params={"status": "active", "kinds": "images"}).json()["jobs"]
        seen = next((r for r in rows if r["id"] == slow["job_id"] and r.get("fraction")
                     and "picture 2 of 2, step" in (r.get("progress") or "")), None)
        if seen:
            break
        time.sleep(0.1)
    assert seen and " of 5" in seen["progress"] and 0.5 < seen["fraction"] <= 1, seen
    live = c.get(f"/api/jobs/{slow['job_id']}").json()
    assert live["detail"]["stage"] == 2 and live["detail"]["picture_run"]["mode"] == "redraw"
    drawn = [e["text"] for e in live["trail"] if e["kind"] == "step"]
    assert "Tiny SD: drawing picture 1 of 2" in drawn and drawn[-1] == "Tiny SD: drawing picture 2 of 2"
    c.delete(f"/api/images/jobs/{slow['id']}")
    assert wait_picture(c, slow["id"])["state"] == "stopped"


def test_picture_sizes_recipes_and_file_choice():
    from dva.images import RECIPES, SD_ASSETS, SLOT_FLAGS, nearest_ratio, openai_size, pick_file
    from dva.tools.images import size_for
    row = {"settings": {"width": 1024, "height": 1024}}
    assert size_for(row, None, None, None) == (1024, 1024) and size_for(row, "square", None, None) == (1024, 1024)
    wide = size_for(row, "landscape", None, None)
    assert wide == (1248, 832) and abs(wide[0] * wide[1] - 1024 * 1024) < 1024 * 1024 * 0.03   # same number of pixels
    assert size_for(row, "tall", None, None) == (768, 1360) and size_for(row, "wide", 800, 600) == (800, 600)
    assert size_for({"settings": {}}, "portrait", None, None) == (416, 624)
    assert openai_size(1024, 1024) == "1024x1024" and openai_size(1248, 832) == "1536x1024" and openai_size(768, 1360) == "1024x1536"
    assert nearest_ratio(1248, 832) == "3:2" and nearest_ratio(768, 1360) == "9:16" and nearest_ratio(512, 512) == "1:1"
    # every recipe names parts the image program has an option for, a licence and where to read it
    for r in RECIPES:
        assert {p["slot"] for p in r["parts"]} <= set(SLOT_FLAGS) and r["licence"] and r["page"].startswith("https://huggingface.co/")
        assert {"model", "diffusion_model"} & {p["slot"] for p in r["parts"]} and {"steps", "cfg", "width", "height"} <= set(r["defaults"])
    assert set(SD_ASSETS["windows"]) == {"cpu", "vulkan", "cuda"}
    # the file of a repository that fits: the preferred quantisation first; a second source when the first has none
    part = next(p for p in RECIPES[0]["parts"] if p["slot"] == "diffusion_model")
    listing = {"leejet/Z-Image-Turbo-GGUF": [{"path": "z_image_turbo-Q8_0.gguf", "size": 7_000}, {"path": "README.md", "size": 1},
                                              {"path": "z_image_turbo-Q4_K.gguf", "size": 4_000}, {"path": "z_image_turbo-Q3_K.gguf", "size": 3_000}]}
    assert pick_file(listing, part)[1]["path"] == "z_image_turbo-Q4_K.gguf"
    listing["leejet/Z-Image-Turbo-GGUF"] = [f for f in listing["leejet/Z-Image-Turbo-GGUF"] if "Q4" not in f["path"]]
    assert pick_file(listing, part)[1]["path"] == "z_image_turbo-Q3_K.gguf"
    vae = next(p for p in RECIPES[0]["parts"] if p["slot"] == "vae")
    assert pick_file({"black-forest-labs/FLUX.1-schnell": [{"path": "ae.safetensors", "size": 300}, {"path": "vae/x.safetensors", "size": 1}]},
                     vae) == ("black-forest-labs/FLUX.1-schnell", {"path": "ae.safetensors", "size": 300})
    assert pick_file({}, vae) == (None, None)


def test_the_image_engine_is_chosen_by_the_cards_and_memory_is_shared_with_text_models(tmp_path, monkeypatch):
    import asyncio
    import types
    from dva import images
    from dva.images import EXE, Images
    monkeypatch.delenv("DVA_SD_CLI", raising=False)
    settings = {}
    db = types.SimpleNamespace(setting=lambda k, d=None: settings.get(k, d), set_setting=settings.__setitem__,
                               all=lambda *a: [])
    stopped = []

    class Runtime:
        free = 4 * 2**30

        def __init__(self):
            mk = lambda name, users, used: types.SimpleNamespace(model_id=name, users=users, last_used=used)   # noqa: E731
            self.servers = {s.model_id: s for s in (mk("old-idle", 0, 1), mk("in-use", 1, 2), mk("new-idle", 0, 3))}

        def _free_bytes(self):
            return self.free

        async def _stop_server(self, server):
            stopped.append(server.model_id)
            del self.servers[server.model_id]
            self.free += 3 * 2**30
    app = types.SimpleNamespace(db=db, bus=types.SimpleNamespace(publish=lambda *a, **k: None), log=lambda m: None,
                                paths=types.SimpleNamespace(data=tmp_path), runtime=Runtime())
    im = Images(app)
    assert im.choose_engine() == (None, None) and im.available_engines()
    for backend in ("cpu", "vulkan", "cuda"):
        im.engine_dir(backend).mkdir(parents=True)
        (im.engine_dir(backend) / EXE).write_text("")
    cards = []
    monkeypatch.setattr(Images, "_cards", lambda self: cards)
    assert im.choose_engine()[0] == "cpu"                      # no real graphics card: the processor build
    cards.append({"name": "AMD Radeon RX 7600", "vendor": "amd"})
    assert im.choose_engine()[0] == "vulkan"                   # a card that is not NVIDIA's: Vulkan, never CUDA
    cards.append({"name": "NVIDIA GeForce RTX 4060", "vendor": "nvidia"})
    assert im.choose_engine()[0] == "cuda"
    settings["image_compute"] = "cpu"
    assert im.choose_engine()[0] == "cpu"                      # the user's choice wins
    settings["image_compute"] = "vulkan"
    assert im.choose_engine()[0] == "vulkan"
    im.remove_engine("vulkan")
    assert im.choose_engine()[0] == "cuda" and set(im.engines()) == {"cpu", "cuda"}
    # memory: idle text models go, least recently used first, only as many as needed; one in use stays
    assert asyncio.run(im._free_memory(5 * 2**30)) and stopped == ["old-idle"]
    assert asyncio.run(im._free_memory(3 * 2**30)) and stopped == ["old-idle"]            # fits already
    assert not asyncio.run(im._free_memory(40 * 2**30)) and stopped == ["old-idle", "new-idle"]
    assert list(app.runtime.servers) == ["in-use"]
    # what failed is said in plain words
    row = {"name": "Tiny"}
    assert "ran out of memory" in images.Images._explain(["ggml_aligned_malloc: failed to allocate 9000 MB"], 1, row)
    assert "did not accept its options" in images.Images._explain(["error: unknown argument: --nope"], 1, row)
    assert "unsupported model" in images.Images._explain(["loading", "[ERROR] load failed: unsupported model"], 1, row)


# ---------------------------------------------------------------------------------------------- sound (2.12.0)
def wait_sound(c, job, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = next(j for j in c.get("/api/sound").json()["jobs"] if j["id"] == job["id"])
        if state["state"] != "running":
            return state
        time.sleep(0.1)
    raise AssertionError(f"sound job still running: {state}")


def sound_jobs(tmp, kind=None):
    log = tmp / "sound-jobs.log"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return [r for r in rows if not kind or r["kind"] == kind]


def model_folder(tmp, name):
    folder = tmp / "sound-models" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.onnx").write_bytes(b"\0" * 2048)
    return str(folder)


def test_speech_voices_and_transcription_on_this_computer(engine):
    c, tmp = engine["client"], engine["tmp"]
    start = c.get("/api/sound").json()
    assert start["library"] == "2.0.1" and not start["espeak"] and not any(start["can"].values())
    assert "English and Chinese" in start["note"] and "does not speak" in start["claude"]
    catalog = {m["key"]: m for m in start["catalog"]}
    assert catalog["kokoro-v1"]["usable"] and catalog["kokoro-v1"]["recommended"] and catalog["kokoro-v1"]["voices"] == 54
    assert catalog["pocket-tts"]["role"] == "clone" and catalog["whisper-base"]["role"] == "stt"
    # models that pronounce through the component the library's 2.x versions left out are not offered
    assert not catalog["kitten-nano"]["usable"] and not catalog["piper-sw-cd"]["usable"]
    assert c.post("/api/sound/catalog/kitten-nano/download").status_code == 400
    none = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": "hello"}).json())
    assert none["state"] == "failed" and "No voice is set up" in none["error"]
    assert c.post("/api/sound/jobs", json={"kind": "speak", "text": " "}).status_code == 400
    assert c.post("/api/sound/models", json={"key": "kokoro-v1", "path": str(tmp / "nowhere")}).status_code == 400

    kokoro = c.post("/api/sound/models", json={"key": "kokoro-v1", "path": model_folder(tmp, "kokoro")}).json()
    assert kokoro["ready"] and kokoro["role"] == "tts" and kokoro["family"] == "kokoro"
    state = c.get("/api/sound").json()
    voices = {v["id"]: v for v in state["voices"]}
    assert state["can"] == {"tts": True, "stt": False, "music": False, "clone": False}
    assert len(voices) == 36 and {v["language"] for v in voices.values()} == {"English (US)", "English (UK)", "Chinese"}
    assert state["default_voice"] == "kokoro-v1:af_heart" and voices["kokoro-v1:bm_george"]["gender"] == "man"
    # one text: numbers are spelled out, because a voice that reads from a dictionary would skip them
    one = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": "In 2026, 3 stoves cost $45.", "speed": 1.2}).json())
    assert one["state"] == "done", one
    rec = one["result"]
    assert rec["kind"] == "audio" and rec["name"].endswith(".mp3") and Path(rec["path"]).parent == tmp / "data" / "sound"
    assert rec["params"]["seconds"] > 1 and rec["params"]["voices"] == {"Voice": "Heart"} and rec["source"] == "local"
    asked = sound_jobs(tmp, "speak")[-1]
    assert asked["segments"] == [{"model": "kokoro-v1", "text": "In twenty twenty-six, three stoves cost forty-five dollars.",
                                  "speaker": "", "speed": 1.2, "sid": 3}]
    assert asked["models"] == {"kokoro-v1": {"family": "kokoro", "dir": kokoro["path"]}}
    assert c.get("/api/file", params={"path": rec["path"]}).headers["content-type"].startswith("audio/")
    # a script: every speaker gets a voice (the second one sounds different), in one recording with the times
    script = "Host: Welcome to the show.\nGuest: Thank you.\nIt is good to be here.\nHost: Let us begin."
    pod = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": script, "name": "Episode 1", "format": "wav"}).json())
    p = pod["result"]["params"]
    assert pod["state"] == "done" and p["mode"] == "script" and p["voices"] == {"Host": "Heart", "Guest": "Michael"}, pod
    assert [(s["speaker"], s["text"]) for s in p["segments"]] == [
        ("Host", "Welcome to the show."), ("Guest", "Thank you. It is good to be here."), ("Host", "Let us begin.")]
    assert p["segments"][1]["start"] > p["segments"][0]["end"] and pod["result"]["name"].startswith("episode-1-")
    assert [s["sid"] for s in sound_jobs(tmp, "speak")[-1]["segments"]] == [3, 16, 3]
    cast = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": script, "voices": {"Guest": "George"},
                                                         "voice": "Emma"}).json())
    assert cast["result"]["params"]["voices"] == {"Host": "Emma", "Guest": "George"}
    wrong = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": "hello", "voice": "Pavarotti"}).json())
    assert wrong["state"] == "failed" and "no voice called 'Pavarotti'" in wrong["error"] and "Heart" in wrong["error"]
    odd = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": "an unpronounceable text"}).json())
    assert odd["state"] == "failed" and "could not pronounce" in odd["error"]
    assert c.put("/api/sound/settings", json={"voice": "kokoro-v1:bf_emma"}).json()["default_voice"] == "kokoro-v1:bf_emma"
    assert c.put("/api/sound/settings", json={"voice": "nobody"}).status_code == 409

    # a voice from a recording: your own after you confirm it, someone else's only with their recorded consent
    c.post("/api/sound/models", json={"key": "pocket-tts", "path": model_folder(tmp, "pocket")})
    up = c.post("/api/sound/upload", files={"file": ("me.wav", Path(pod["result"]["path"]).read_bytes(), "audio/wav")}).json()
    for consent, message in (({}, "your own voice or another person's"), ({"whose": "mine"}, "Confirm"),
                             ({"whose": "other", "person": "Amina"}, "recording in which they agree"),
                             ({"whose": "other", "consent_audio": up["path"]}, "whose voice")):
        r = c.post("/api/sound/voices", json={"name": "A voice", "audio": up["path"], "consent": consent})
        assert r.status_code == 400 and message in r.json()["error"], r.json()
    short = c.post("/api/sound/upload", files={"file": ("short.mp3", Path(rec["path"]).read_bytes(), "audio/mpeg")}).json()
    r = c.post("/api/sound/voices", json={"name": "Short", "audio": short["path"], "consent": {"whose": "mine", "confirmed": True}})
    assert r.status_code == 400 and "shorter than 5 seconds" in r.json()["error"]
    long_text = "Speaker: " + " ".join(["word"] * 40)
    long = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": long_text, "format": "wav"}).json())["result"]
    mine = c.post("/api/sound/voices", json={"name": "My voice", "audio": long["path"], "transcript": "word word",
                                             "consent": {"whose": "mine", "confirmed": True}}).json()
    assert mine["kind"] == "recording" and mine["ready"] and mine["whose"] == "mine" and mine["id"].startswith("voice:vc_")
    other = c.post("/api/sound/voices", json={"name": "Amina", "audio": long["path"],
                                              "consent": {"whose": "other", "person": "Amina W.", "consent_audio": up["path"]}}).json()
    assert other["whose"] == "other" and len(list((tmp / "data" / "voices").glob("*-consent.wav"))) == 1
    spoken = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": "This is me.", "voice": "My voice"}).json())
    assert spoken["state"] == "done" and spoken["result"]["model_name"] == "My voice", spoken
    seg = sound_jobs(tmp, "speak")[-1]
    assert seg["models"]["pocket-tts"]["family"] == "pocket" and seg["segments"][0]["ref_text"] == "word word"
    assert Path(seg["segments"][0]["ref_audio"]).parent == tmp / "data" / "voices" and "sid" not in seg["segments"][0]
    c.delete(f"/api/sound/voices/{other['id']}")
    assert not list((tmp / "data" / "voices").glob("*-consent.wav")) and len([v for v in c.get("/api/sound").json()["voices"] if v["kind"] == "recording"]) == 1

    # transcription: the words with times, saved as text and as subtitles
    c.post("/api/sound/models", json={"key": "whisper-base", "path": model_folder(tmp, "whisper")})
    (tmp / "data" / "models" / "sound").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "models" / "sound" / "silero_vad.onnx").write_bytes(b"\0" * 64)
    heard = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "transcribe", "audio": pod["result"]["path"], "language": "sw"}).json())
    res = heard["result"]
    assert heard["state"] == "done" and res["text"].startswith("These are the words of episode-1") and "Language: sw." in res["text"], heard
    assert Path(res["file"]).read_text(encoding="utf-8").strip() == res["text"]
    assert "00:00:01,500 --> 00:00:02,400" in Path(res["subtitles"]).read_text(encoding="utf-8")
    assert sound_jobs(tmp, "transcribe")[-1]["model"] == {"family": "whisper", "dir": str(tmp / "sound-models" / "whisper"), "language": "sw"}
    missing = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "transcribe", "audio": str(tmp / "nope.wav")}).json())
    assert missing["state"] == "failed" and "no file" in missing["error"]
    # mixing: a voice over a quieter second track, as long as the voice (plus the fade)
    mix = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "mix", "name": "Voice over", "tracks": [
        {"path": rec["path"]}, {"path": long["path"], "volume": 0.2, "start": 0.5}]}).json())
    assert mix["state"] == "done" and abs(mix["result"]["params"]["seconds"] - (rec["params"]["seconds"] + 2)) < 0.3, mix
    items = c.get("/api/sound/library").json()["items"]
    assert items[0]["params"]["mode"] == "mix" and {"speech", "script", "mix"} <= {i["params"]["mode"] for i in items}
    c.delete(f"/api/sound/library/{items[0]['id']}", params={"file": 1})
    assert not Path(items[0]["path"]).exists()
    assert c.get("/api/sound").json()["can"] == {"tts": True, "stt": True, "music": False, "clone": True}
    c.put("/api/sound/settings", json={"voice": ""})


def test_an_agent_speaks_and_writes_down_a_recording(engine):
    c, tmp = engine["client"], engine["tmp"]
    folder = tmp / "sound-project"
    folder.mkdir()
    chat = c.post("/api/chats", json={"harness": "agent", "folders": [str(folder)]}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "say aloud: Welcome to KimiLM 2.12"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["name"] == "speak" and step["ok"] and step["label"] == "Speak", step
    clip = step["media"][0]
    assert clip["kind"] == "audio" and Path(clip["path"]).parent == folder / "sound" and clip["path"] in step["result"]
    assert "segments" not in clip["params"] and clip["params"]["seconds"] > 0 and "Voice: Heart" in step["result"]
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": f"write down what is said in {clip['path']}"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["name"] == "transcribe_audio" and step["ok"] and "These are the words of welcome-to-k" in step["result"], step
    assert len(list((folder / "sound" / "transcripts").glob("*.srt"))) == 1
    # music has no model here: the tool is not offered, so the request is answered in words
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "compose music: a calm piano piece"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    assert not [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"]


def test_sound_helpers_numbers_scripts_files_and_the_catalogue(tmp_path):
    import numpy as np
    from dva.sound import CATALOG, ONLINE, RELEASES, Sound, read_pcm, seconds_of, spell_english, srt, write_audio
    assert spell_english("In 2026, 3 stoves cost $45.50 & 12% less") == \
        "In twenty twenty-six, three stoves cost forty-five point five zero dollars and twelve percent less"
    assert spell_english("1,250,000 people; $3m; 1999; 2005; call 0712") == ("one million two hundred and fifty thousand people; "
                                                                           "three million dollars; nineteen ninety-nine; twenty oh five; call zero seven one two")
    assert spell_english("No numbers here.") == "No numbers here."
    parse = Sound.parse_script(None, "Host: Hello there.\n\n**Guest**: Hi.\nMore from the guest.\nHost: Bye.")
    assert parse == [{"speaker": "Host", "text": "Hello there."}, {"speaker": "Guest", "text": "Hi. More from the guest."},
                     {"speaker": "Host", "text": "Bye."}]
    plain = Sound.parse_script(None, "A plain text.\nNote: this is not a speaker.\nSecond line.", "v")
    assert plain == [{"speaker": "", "text": "A plain text. Note: this is not a speaker. Second line.", "voice": "v"}]
    assert srt([{"start": 0, "end": 1.5, "text": "One"}, {"start": 3661.25, "end": 3662, "text": "Two"}]) == \
        "1\n00:00:00,000 --> 00:00:01,500\nOne\n\n2\n01:01:01,250 --> 01:01:02,000\nTwo\n"
    # sound files: written and read back in every format the app saves
    t = np.arange(44100) / 44100
    stereo = np.stack([0.4 * np.sin(2 * np.pi * 440 * t), 0.4 * np.sin(2 * np.pi * 660 * t)], axis=1).astype("float32")
    for ext in ("wav", "mp3", "m4a"):
        path = write_audio(tmp_path / f"tone.{ext}", stereo, 44100)
        back = read_pcm(path, 44100, 2)
        assert abs(len(back) - 44100) < 4000 and 0.2 < float(np.abs(back).max()) <= 1.0, ext
        assert abs(seconds_of(path) - 1.0) < 0.15, ext
    assert read_pcm(tmp_path / "tone.mp3", 16000, 1).shape[1] == 1
    with pytest.raises(RuntimeError, match="can be saved as"):
        write_audio(tmp_path / "tone.xyz", stereo, 44100)
    # the catalogue: where each model comes from, its licence, and voices that match the models' own lists
    for m in CATALOG:
        assert m["licence"] and m["page"].startswith("https://") and [v["sid"] for v in m["voices"]] == list(range(len(m["voices"])))
        if m["role"] == "music":                       # four files from one Hugging Face repository
            assert [p["slot"] for p in m["hf"]] == ["lm", "dit", "encoder", "vae"] and m["family"] == "ace"
        else:
            assert m["url"].startswith(RELEASES) and m["url"].endswith(".tar.bz2") and m["role"] in ("tts", "stt", "clone")
    kokoro = next(m for m in CATALOG if m["key"] == "kokoro-v1")["voices"]
    assert (kokoro[3]["speaker"], kokoro[26]["speaker"], kokoro[53]["speaker"]) == ("af_heart", "bm_george", "em_santa")
    assert sum(not v.get("needs_espeak") for v in kokoro) == 36 and kokoro[28]["language"] == "Spanish"
    assert set(ONLINE["google"]) >= {"tts", "stt", "music"} and "music" not in ONLINE["openai"] and len(ONLINE["google"]["voices"]) == 30


@pytest.mark.skipif(not os.environ.get("DVA_TEST_SOUND_MODELS"), reason="needs real speech models (DVA_TEST_SOUND_MODELS=folder)")
def test_real_voices_speak_and_real_whisper_reads_them_back(tmp_path):
    """With the real library and models: a two-voice script and a voice from a recording, transcribed back."""
    models = Path(os.environ["DVA_TEST_SOUND_MODELS"])

    def worker(job):
        spec = tmp_path / "job.json"
        spec.write_text(json.dumps(job))
        out = subprocess.run([sys.executable, "-m", "dva.sound_worker", str(spec)], cwd=str(ENGINE), capture_output=True, text=True)
        last = json.loads(out.stdout.strip().splitlines()[-1])
        assert last.get("done"), (last, out.stderr[-600:])
        return last
    said = worker({"kind": "speak", "out": str(tmp_path / "talk.wav"),
                   "models": {"k": {"family": "kokoro", "dir": str(models / "kokoro-multi-lang-v1_0")},
                              "p": {"family": "pocket", "dir": str(models / "sherpa-onnx-pocket-tts-int8-2026-01-26")}},
                   "segments": [{"model": "k", "sid": 3, "speaker": "Host", "text": "Welcome to the show about clean cooking."},
                                {"model": "k", "sid": 26, "speaker": "Guest", "text": "Thank you for inviting me today."},
                                {"model": "p", "speaker": "Clone", "text": "This voice comes from a short recording.",
                                 "ref_audio": str(models / "sherpa-onnx-pocket-tts-int8-2026-01-26" / "test_wavs" / "bria.wav")}]})
    assert said["seconds"] > 6 and [s["speaker"] for s in said["segments"]] == ["Host", "Guest", "Clone"]
    heard = worker({"kind": "transcribe", "audio": str(tmp_path / "talk.wav"), "vad": str(models / "silero_vad.onnx"),
                    "model": {"family": "whisper", "dir": str(models / "sherpa-onnx-whisper-tiny"), "language": "en"}})
    text = heard["text"].lower()
    assert all(w in text for w in ("welcome", "clean cooking", "thank you", "short recording")), text
    assert len(heard["segments"]) >= 2 and heard["segments"][0]["start"] < 1


def test_models_for_pictures_and_sound_really_download_and_can_be_cancelled(tmp_path, monkeypatch):
    """The downloads of 2.11.0 failed on the first real try ("'function' object has no attribute 'is_set'"):
    this runs them with the real downloader against a local server."""
    import asyncio
    import tarfile
    import threading
    import types
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from dva import images, sound
    from dva.db import DB
    bundle = tmp_path / "voice.tar.bz2"
    (tmp_path / "voice-model").mkdir()
    (tmp_path / "voice-model" / "model.onnx").write_bytes(b"\1" * 5000)
    (tmp_path / "voice-model" / "tokens.txt").write_text("a 1\n")
    with tarfile.open(bundle, "w:bz2") as tar:
        tar.add(tmp_path / "voice-model", arcname="voice-model")
    engine_bundle = tmp_path / "engine.tar.gz"         # what the build workflow publishes: the programs in one folder
    (tmp_path / "built").mkdir()
    for name in ("ace-lm", "ace-synth", "libggml.so"):
        (tmp_path / "built" / name).write_text("#!/bin/sh\n")
    with tarfile.open(engine_bundle, "w:gz") as tar:
        for f in (tmp_path / "built").iterdir():
            tar.add(f, arcname=f.name)
    asked = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)
            if "not-built" in self.path:
                self.send_response(404)
                self.end_headers()
                return
            data = bundle.read_bytes() if self.path.endswith(".tar.bz2") else engine_bundle.read_bytes() if "acestep-linux" in self.path \
                else (b"\2" * 300_000 if "big" in self.path else b"\3" * 4000)
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("DVA_SOUND_LIBRARY", "2.0.1")
    events = []
    paths = types.SimpleNamespace(data=tmp_path / "data", models_dir=lambda settings=None: tmp_path / "models")
    (tmp_path / "data").mkdir()
    app = types.SimpleNamespace(db=DB(tmp_path / "t.sqlite3"), paths=paths, log=lambda m: events.append(("log", m)),
                                bus=types.SimpleNamespace(publish=lambda kind, **k: events.append((kind, k))),
                                secrets=types.SimpleNamespace(get=lambda k: None),
                                providers=types.SimpleNamespace(ready=lambda p: False), runtime=None,
                                router=types.SimpleNamespace(pick=lambda role: (None, "")))

    async def listing(url, headers=None):
        repo = url.split("/api/models/")[1].split("/tree/")[0]
        return [{"type": "file", "path": "weights/big-model-Q4_K.gguf", "size": 300_000},
                {"type": "file", "path": "vae/ae.safetensors", "size": 4000}, {"type": "file", "path": "README.md", "size": 10}] \
            if repo.startswith("test/") else []
    monkeypatch.setattr(images, "fetch_json", listing)
    monkeypatch.setattr(images, "HF", base)
    recipe = {"key": "tiny", "name": "Tiny model", "licence": "MIT", "edit": "img2img",
              "defaults": {"steps": 4, "cfg": 1.0, "width": 512, "height": 512, "args": ""},
              "parts": [{"slot": "diffusion_model", "sources": [{"repo": "test/tiny", "match": r"\.gguf$"}], "prefer": ["Q4_K"]},
                        {"slot": "vae", "sources": [{"repo": "test/tiny", "match": r"vae/.*\.safetensors$"}]}]}

    async def run():
        im = images.Images(app)
        state = {"key": "tiny", "name": "Tiny model", "state": "starting", "done": 0, "total": 0, "file": "", "error": None, "cancel": False}
        await im._download(recipe, state)
        assert state["state"] == "done" and state["done"] == 304_000, state
        row = im.models()[0]
        assert row["recipe"] == "tiny" and row["size"] == 304_000 and row["default"]
        assert Path(row["files"]["diffusion_model"]) == tmp_path / "models" / "image" / "tiny" / "big-model-Q4_K.gguf"
        assert Path(row["files"]["vae"]).read_bytes() == b"\3" * 4000
        assert "/test/tiny/resolve/main/weights/big-model-Q4_K.gguf?download=true" in asked
        json.dumps(im.recipes())                                  # what the window is sent stays plain data
        # cancelled: nothing is added, and the state says so instead of "failed"
        im.remove(row["id"], delete_files=True)
        stopped = {**state, "state": "starting", "cancel": True}
        await im._download(recipe, stopped)
        assert stopped["state"] == "cancelled" and not im.models(), stopped
        missing = {**state, "state": "starting", "cancel": False}
        await im._download({**recipe, "parts": [{"slot": "model", "sources": [{"repo": "nobody/none", "match": r"\.x$"}]}]}, missing)
        assert missing["state"] == "failed" and "was not found in nobody/none" in missing["error"]

        so = sound.Sound(app)
        entry = {"key": "test-voice", "role": "tts", "family": "kokoro", "name": "Test voice", "mb": 1, "licence": "MIT",
                 "url": f"{base}/voice.tar.bz2", "voices": []}
        s = {"key": "test-voice", "name": "Test voice", "state": "starting", "done": 0, "total": 1, "error": None, "cancel": False}
        await so._download(entry, s)
        assert s["state"] == "done", s
        folder = tmp_path / "models" / "sound" / "test-voice"
        assert (folder / "model.onnx").stat().st_size == 5000 and not (tmp_path / "models" / "sound" / "voice.tar.bz2").exists()
        assert so.get("test-voice")["path"] == str(folder) and so.get("test-voice")["size"] > 5000
        s2 = {**s, "state": "starting", "cancel": True}
        await so._download({**entry, "key": "other-voice"}, s2)
        assert s2["state"] == "cancelled" and not so.get("other-voice")
        json.dumps(so.catalog())
        # a music model: four files chosen from the repository's listing (2.13.0)
        async def ace_listing(url, headers=None):
            return [{"type": "file", "path": n, "size": 4000} for n in (
                "acestep-5Hz-lm-0.6B-BF16.gguf", "acestep-5Hz-lm-0.6B-Q8_0.gguf", "acestep-5Hz-lm-4B-Q8_0.gguf",
                "acestep-v15-turbo-Q4_K_M.gguf", "acestep-v15-turbo-Q8_0.gguf", "acestep-v15-turbo-continuous-Q4_K_M.gguf",
                "Qwen3-Embedding-0.6B-Q8_0.gguf", "vae-BF16.gguf", "README.md")]
        monkeypatch.setattr(sound, "fetch_json", ace_listing)
        monkeypatch.setattr(sound, "HF", base)
        light = next(m for m in sound.CATALOG if m["key"] == "ace-step-light")
        ms = {"key": light["key"], "name": light["name"], "state": "starting", "done": 0, "total": 1, "error": None, "cancel": False}
        await so._download(light, ms)
        assert ms["state"] == "done" and ms["done"] == 16000, ms
        got = so.get("ace-step-light")
        assert sorted(p.name for p in Path(got["path"]).iterdir()) == [
            "Qwen3-Embedding-0.6B-Q8_0.gguf", "acestep-5Hz-lm-0.6B-Q8_0.gguf", "acestep-v15-turbo-Q4_K_M.gguf", "vae-BF16.gguf"]
        assert got["settings"]["lm"] == "acestep-5Hz-lm-0.6B-Q8_0.gguf" and got["settings"]["dit"] == "acestep-v15-turbo-Q4_K_M.gguf"
        assert not got["ready"] and got["why"] == "the music engine is not installed"
        # the music programs: from KimiLM's own release; said plainly when that release was not built yet
        monkeypatch.setattr(sound, "ACE_ASSET", "acestep-linux-x64.tar.gz")
        monkeypatch.setattr(sound, "ACE_LM", "ace-lm")
        monkeypatch.setattr(sound, "ACE_SYNTH", "ace-synth")
        where = {"repo": "kiplabat/KimiLM", "tag": "music-engine-test", "commit": "c", "built_from": "x", "workflow": "w"}
        monkeypatch.setattr(sound.Sound, "engine_source", lambda self: {**where, "url": f"{base}/not-built/acestep-linux-x64.tar.gz"})
        await so.install_music_engine()
        failed = [k for kind, k in events if kind == "music_engine" and k.get("action") == "failed"]
        assert failed and "has not been built yet" in failed[-1]["error"] and "Run workflow" in failed[-1]["error"] and not so.music_engine()
        monkeypatch.setattr(sound.Sound, "engine_source", lambda self: {**where, "url": f"{base}/acestep-linux-x64.tar.gz"})
        await so.install_music_engine()
        assert so.music_engine() == {"dir": str(tmp_path / "data" / "runtimes" / "acestep"), "source": "installed"}
        assert sorted(p.name for p in (tmp_path / "data" / "runtimes" / "acestep").iterdir()) == ["ace-lm", "ace-synth", "libggml.so"]
        assert so.get("ace-step-light")["ready"] and so.can()["music"] and so.local_music()
        json.dumps(so.status())
    try:
        asyncio.run(run())
    finally:
        server.shutdown()
    assert not [e for e in events if e[0] == "log" and "is_set" in e[1]]


# ------------------------------------------------------------------------- music here, podcasts (2.13.0)
def music_folder(tmp, name="ace", leave_out=()):
    folder = tmp / "sound-models" / name
    folder.mkdir(parents=True, exist_ok=True)
    for f in ("acestep-5Hz-lm-0.6B-Q8_0.gguf", "acestep-v15-turbo-Q4_K_M.gguf", "Qwen3-Embedding-0.6B-Q8_0.gguf", "vae-BF16.gguf"):
        if f not in leave_out:
            (folder / f).write_bytes(b"GGUF" + b"\0" * 1000)
    return str(folder)


@pytest.mark.skipif(os.name == "nt", reason="a script cannot stand in for ace-lm.exe on Windows")
def test_music_is_made_on_this_computer_without_any_online_service(engine):
    c, tmp = engine["client"], engine["tmp"]
    start = c.get("/api/sound").json()
    eng = start["music_engine"]
    assert not eng["installed"] and not start["can"]["music"] and eng["commit"].startswith("ed53caf")
    # the programs come from a public repository of their own (2.15.0), so that KimiLM's repository can be private
    assert eng["url"].startswith("https://github.com/kiplabat/kimilm-engines/releases/download/music-engine-ed53caf/acestep-")
    light = next(m for m in start["catalog"] if m["key"] == "ace-step-light")
    assert light["role"] == "music" and light["recommended"] and light["licence"] == "MIT" and light["mb"] == 3300
    # a model folder needs its four files; without the engine it is there but cannot be used yet
    r = c.post("/api/sound/models", json={"key": "ace-step-light", "path": music_folder(tmp, "broken", leave_out=("vae-BF16.gguf",))})
    assert r.status_code == 400 and "needs four files" in r.json()["error"]
    model = c.post("/api/sound/models", json={"key": "ace-step-light", "path": music_folder(tmp)}).json()
    assert model["role"] == "music" and not model["ready"] and model["why"] == "the music engine is not installed"
    assert model["settings"] == {"lm": "acestep-5Hz-lm-0.6B-Q8_0.gguf", "dit": "acestep-v15-turbo-Q4_K_M.gguf", "steps": 8, "shift": 3.0}
    none = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "music", "prompt": "a tune"}).json())
    assert none["state"] == "failed" and "install the music engine" in none["error"]
    assert c.post("/api/sound/music-engine", json={"folder": str(tmp)}).status_code == 400
    state = c.post("/api/sound/music-engine", json={"folder": str(engine["ace"])}).json()
    assert "music_engine" in state, state
    assert state["music_engine"]["installed"] and state["music_engine"]["source"] == "a folder you chose" and state["can"]["music"], state
    # a piece without singing: planned, then rendered, kept with what the planner chose
    made = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "music", "prompt": "Amapiano groove with log drums and soft keys",
                                                         "seconds": 12, "instrumental": True, "seed": 7}).json())
    assert made["state"] == "done", made
    rec = made["result"]
    assert rec["source"] == "local" and rec["model_name"] == "ACE-Step 1.5 (light)" and rec["name"].startswith("amapiano-groove")
    assert rec["name"].endswith(".mp3") and Path(rec["path"]).parent == tmp / "data" / "sound" and abs(rec["params"]["seconds"] - 12) < 0.5
    assert rec["params"]["bpm"] == 96 and rec["params"]["keyscale"] == "A minor" and rec["params"]["words"] == "" and "cost" not in rec["params"]
    assert not list((tmp / "data" / "tmp").glob("mus_*"))                      # nothing is left behind
    song = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "music", "prompt": "A hopeful folk song", "seconds": 3,
                                                         "instrumental": False, "language": "sw"}).json())
    assert song["state"] == "done" and "[Chorus]" in song["result"]["params"]["words"] and song["result"]["params"]["vocal_language"] == "sw"
    assert abs(song["result"]["params"]["seconds"] - 10) < 0.5                 # the model's shortest piece is 10 seconds
    for prompt, said in (("please explode", "while planning"), ("a silent piece", "while rendering")):
        bad = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "music", "prompt": prompt}).json())
        assert bad["state"] == "failed" and said in bad["error"], bad
    # an agent makes music without being asked for permission: nothing leaves the computer
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "compose music: a calm piano piece"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["name"] == "generate_music" and step["ok"] and step["media"][0]["kind"] == "audio" and "US$" not in step["result"], step
    # the engine can be taken away again; the model stays, waiting for it
    state = c.delete("/api/sound/music-engine").json()
    assert not state["music_engine"]["installed"] and not state["can"]["music"]
    c.post("/api/sound/music-engine", json={"folder": str(engine["ace"])})


def minimal_docx(path, paragraphs):
    import zipfile
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    return path


def test_a_podcast_is_made_from_a_document_in_one_request(engine):
    c, tmp = engine["client"], engine["tmp"]
    assert c.get("/api/sound").json()["text_model"]
    assert c.post("/api/sound/jobs", json={"kind": "podcast"}).status_code == 400
    # a long document: read part by part, outlined, and each part of the script written with the pieces of the
    # document that belong to it in front of the model
    topics = [("Solar pumps lift water for small farms in dry seasons", "solar pumps water farms irrigation"),
              ("Costs and savings were measured over three years of use", "costs savings money years diesel"),
              ("Next steps are training for technicians and spare parts", "next steps training technicians parts")]
    paragraphs = [f"{head}. " + " ".join(f"Finding {i} about {words}." for i in range(60)) for head, words in topics]
    doc = minimal_docx(tmp / "Solar pumps report.docx", paragraphs)
    up = c.post("/api/sound/upload", files={"file": (doc.name, doc.read_bytes(), "application/octet-stream")}).json()
    assert Path(up["path"]).name.startswith("Solar-pumps-report-") and up["path"].endswith(".docx")
    job = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "podcast", "sources": [up["path"]], "minutes": 5}).json(), timeout=120)
    assert job["state"] == "done" and job["title"].startswith("Podcast: Solar-pumps-report-"), job
    rec = job["result"]
    p = rec["params"]
    assert p["mode"] == "podcast" and p["title"] == "The Stand-in Report" and p["parts"] == 3 and p["voices"] == {"Alex": "Heart", "Sam": "Michael"}
    assert rec["name"].startswith("the-stand-in-report-") and rec["name"].endswith(".mp3") and p["seconds"] > 5 and p["words"] > 30
    lines = rec["lines"]
    assert [ln["speaker"] for ln in lines] == ["Alex", "Sam"] * 3
    assert lines[0]["text"].startswith("Part 1 of 3. The document says: Solar pumps lift water for small farms")
    assert lines[2]["text"].startswith("Part 2 of 3. The document says: Costs and savings were measured over")
    assert lines[4]["text"].startswith("Part 3 of 3. The document says: Next steps are training for technicians")
    # a hesitation is spelled so that the voice can say it, a stage direction is gone, pauses are kept as pauses
    assert lines[1]["text"] == "Um… that is interesting, and it costs $45… really."
    script = Path(p["script"]).read_text(encoding="utf-8")       # the app writes UTF-8; Windows would guess otherwise
    assert script.startswith("# The Stand-in Report") and "**Sam:** Um… that is interesting" in script and "Made from: Solar-pumps-report-" in script
    spoken = sound_jobs(tmp, "speak")[-1]["segments"]
    assert [(s["text"], s["pause"]) for s in spoken[:5]] == [
        ("Part one of three. The document says: Solar pumps lift water for small farms.", 0.32),
        ("Um", 0.6), ("that is interesting, and it costs forty-five dollars", 0.45), ("really.", 0.9),      # then a new part begins
        ("Part two of three. The document says: Costs and savings were measured over three.", 0.38)]
    assert spoken[-1]["pause"] == 0.0 and {s["sid"] for s in spoken} == {3, 16} and len(spoken) == 12
    item = c.get("/api/sound/library").json()["items"][0]
    assert item["id"] == rec["id"] and item["params"]["mode"] == "podcast" and item["prompt"].startswith("The Stand-in Report:")
    # a short pasted text is used as it is; one narrator; a length of two minutes is two parts
    short = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "podcast", "text": "Electric pressure cookers save energy. " * 12,
                                                          "minutes": 2, "style": "narration", "hosts": ["Mia"]}).json(), timeout=120)
    assert short["state"] == "done" and short["result"]["params"]["voices"] == {"Mia": "Heart"} and short["result"]["params"]["parts"] == 2, short
    assert short["result"]["lines"][0]["text"].startswith("Part 1 of 2. The document says: Electric pressure cookers save energy.")
    assert short["result"]["lines"][1]["text"] == "Um… that is interesting, and it costs $45… really."
    for body, said in (({"text": "too short"}, "too little text"), ({"sources": [str(tmp / "nope.pdf")]}, "no file")):
        bad = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "podcast", **body}).json())
        assert bad["state"] == "failed" and said in bad["error"], bad
    # in a chat: attach the document and ask; the agent needs no path and writes no script itself
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    c.post(f"/api/chats/{chat['id']}/upload", files={"file": (doc.name, doc.read_bytes(), "application/octet-stream")})
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "make a podcast from the attached document"}).json()
    assert wait_job(c, job["job_id"], timeout=120)["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["name"] == "make_podcast" and step["ok"] and step["label"] == "Make podcast", step
    assert "The podcast “The Stand-in Report” is recorded" in step["result"] and "Script: " in step["result"]
    clip = step["media"][0]
    assert clip["kind"] == "audio" and clip["params"]["mode"] == "podcast" and clip["params"]["parts"] == 2 and Path(clip["path"]).exists()


def test_podcast_pieces_and_what_belongs_to_a_segment():
    from dva.podcast import nearest, pieces, script_markdown
    text = "--- page 1 ---\nAlpha beta gamma.\n\nMore of alpha.\n--- page 2 ---\n" + "Delta epsilon zeta. " * 300 + "\n\nEnd."
    got = pieces(text, size=1000)
    assert got[0] == {"text": "Alpha beta gamma.\nMore of alpha.", "page": 2} or got[0]["text"].startswith("Alpha beta gamma.")
    assert all(len(g["text"]) <= 1100 for g in got) and got[-1]["text"].endswith("End.") and len(got) >= 6
    assert "--- page" not in " ".join(g["text"] for g in got)
    chunks = [{"text": "Solar pumps lift water on farms."}, {"text": "The budget and the costs of diesel."},
              {"text": "Training of technicians comes next."}, {"text": "Solar panels and pumps need parts."}]
    assert [c["text"][:5] for c in nearest("costs of diesel", chunks, 1)] == ["The b"]
    assert [c["text"][:5] for c in nearest("solar pumps", chunks, 2)] == ["Solar", "Solar"]        # in the document's order
    assert nearest("", chunks, 2) == chunks[:2]
    # talk, not text: hesitations the voices can say, pauses as pieces, and a little added when the script has none
    from dva.podcast import natural, parts_of, timed
    assert parts_of("Uhm... that is **nice** (laughs), and [pause] yes.") == ["Um", "that is nice, and", "yes."]
    assert parts_of("Mm-hmm. So the pump, erm , pays… in four seasons [chuckles].") == ["Mhm. So the pump, um, pays", "in four seasons."]
    assert parts_of(" [music] ") == []
    mk = lambda *rows: [{"speaker": s, "parts": parts_of(t), "new_part": n} for s, t, n in rows]      # noqa: E731
    clean = mk(("A", "Welcome to the show about solar pumps today.", True), ("B", "Glad to be here with you.", False),
               ("A", "The study followed farmers for three years.", False), ("B", "Three years is a long time.", False),
               ("A", "The pumps paid for themselves in under four seasons of use.", True), ("B", "Really?", False),
               ("A", "Yes, because diesel is expensive.", False), ("B", "That makes sense, but who pays first?", False))
    out = natural(clean)
    assert out[1]["parts"] == ["Glad to be here with you."] and out[5]["parts"] == ["Really?"]            # greeting and reaction untouched
    assert out[4]["parts"] == ["So, the pumps paid for themselves in under four seasons of use."]
    assert out[7]["parts"] == ["Hmm", "That makes sense, but who pays first?"]                                # thinks, then asks
    gaps = [(t["text"][:6], t["pause"]) for t in timed(out, {"A": "Alex", "B": "Sam"})]
    assert gaps[3] == ("Three ", 0.9) and gaps[4] == ("So, th", 0.18) and gaps[5] == ("Really", 0.18)        # long before a new part, short around a reaction
    assert gaps[-2] == ("Hmm", 0.6) and gaps[-1][1] == 0.0
    talky = mk(("A", "Well, here we go again.", True), ("B", "Oh, nice one.", False), ("A", "Right, so it is.", False), ("B", "Hmm, I see.", False))
    assert [ln["parts"] for ln in natural(talky)] == [["Well, here we go again."], ["Oh, nice one."], ["Right, so it is."], ["Hmm, I see."]]
    md = script_markdown("T", "S", ["a.pdf"], [{"speaker": "A", "text": "Hi."}, {"speaker": "B", "text": "Yo."}], {"A": "Alex", "B": "Sam"})
    assert md.startswith("# T\n\nS\n\nMade from: a.pdf") and "**Alex:** Hi." in md and "**Sam:** Yo." in md


def test_the_composer_cuts_fades_previews_and_saves(engine):
    c, tmp = engine["client"], engine["tmp"]
    made = [wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": text, "format": "wav"}).json())["result"]
            for text in ("one two three four five six seven eight", "nine ten eleven twelve")]
    a, b = made
    drawn = c.get("/api/sound/peaks", params={"path": a["path"], "n": 50}).json()
    assert abs(drawn["seconds"] - a["params"]["seconds"]) < 0.1 and len(drawn["peaks"]) == 50 and 0.2 < max(drawn["peaks"]) <= 1
    assert c.get("/api/sound/peaks", params={"path": str(tmp / "sound-jobs.log")}).status_code == 404      # only files the window may see
    count = len(c.get("/api/sound/library").json()["items"])
    # listening is a preview: not kept in the library, and the next one replaces it
    tracks = [{"path": a["path"], "from": 0.5, "to": 1.5, "fade_in": 0.2, "fade_out": 0.2},
              {"path": b["path"], "start": 2.0, "volume": 0.3}, {"path": b["path"], "start": 30, "mute": True}]
    first = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "mix", "preview": True, "tracks": tracks, "length": "longest", "fade": 0}).json())
    assert first["state"] == "done" and first["result"]["id"] == "" and Path(first["result"]["path"]).parent.name == "previews", first
    assert abs(first["result"]["params"]["seconds"] - (2.0 + b["params"]["seconds"])) < 0.05 and first["result"]["name"].endswith(".wav")
    second = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "mix", "preview": True, "tracks": tracks[:1], "length": "longest", "fade": 0}).json())
    assert abs(second["result"]["params"]["seconds"] - 1.0) < 0.05                      # only the part from 0.5 to 1.5 seconds
    assert len(list((tmp / "data" / "sound" / "previews").glob("*"))) == 1 and len(c.get("/api/sound/library").json()["items"]) == count
    from dva.sound import read_pcm
    cut = read_pcm(second["result"]["path"], 8000, 1).reshape(-1)
    assert abs(cut[:80]).max() < 0.05 < abs(cut[3000:5000]).max() and abs(cut[-80:]).max() < 0.05      # fades in and out
    saved = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "mix", "name": "My mix", "tracks": tracks, "length": "longest", "fade": 0,
                                                          "format": "mp3"}).json())
    assert saved["state"] == "done" and saved["result"]["id"] and saved["result"]["name"].startswith("my-mix-") and saved["result"]["name"].endswith(".mp3")
    assert len(c.get("/api/sound/library").json()["items"]) == count + 1
    nothing = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "mix", "tracks": [{"path": a["path"], "mute": True}]}).json())
    assert nothing["state"] == "failed" and "Give the recordings" in nothing["error"]


def test_words_the_dictionary_lacks_are_still_pronounced():
    from dva.sound_worker import ed_ending, pronounce, s_ending
    lex = {"household": "h ˈ W s h ˌ O l d", "make": "m ˈ A k", "describe": "d ə s k ɹ ˈ I b", "season": "s ˈ i z ᵊ n", "replace": "ɹ ə p l ˈ A s",
           "class": "k l ˈ æ s", "stop": "s t ˈ ɑ p", "run": "ɹ ˈ ʌ n", "times": "t ˈ I m z", "quick": "k w ˈ ɪ k", "user": "j ˈ u z ə ɹ",
           "open": "ˈ O p ə n", "carry": "k ˈ æ ɹ i", "c": "s ˈ i", "p": "p ˈ i", "u": "j ˈ u", "a": "ˈ A", "i": "ˈ I", "l": "ˈ ɛ l", "m": "ˈ ɛ m"}
    tokens = set("h ˈ W s ˌ O l d m A k ə ɹ I b i z ᵊ n p æ t ɑ ʌ w ɪ j u ɛ ŋ ᵻ ʃ ʧ θ f v ɡ ʤ Y".split())
    said = lambda w: pronounce(w, lex, tokens)                                        # noqa: E731
    assert said("households") == "h ˈ W s h ˌ O l d z" and said("makes") == "m ˈ A k s" and said("classes") == "k l ˈ æ s ᵻ z"
    assert said("described") == "d ə s k ɹ ˈ I b d" and said("replaced") == "ɹ ə p l ˈ A s t" and said("stopped") == "s t ˈ ɑ p t"
    assert said("making") == "m ˈ A k ɪ ŋ" and said("running") == "ɹ ˈ ʌ n ɪ ŋ" and said("quickly") == "k w ˈ ɪ k l i"
    assert said("user's") == "j ˈ u z ə ɹ z" and said("carries") == "k ˈ æ ɹ i z" and said("runtimes") == "ɹ ˈ ʌ n t ˈ I m z"
    assert said("CPU") == "s ˈ i p ˈ i j ˈ u" and said("OpenAI") == "ˈ O p ə n ˈ A ˈ I"                     # as letters; Open + AI
    assert said("KimiLM") == "k ˈ i m i ˈ ɛ l ˈ ɛ m" and said("amapiano") == "ˈ ɑ m ɑ p i ɑ n O"             # names: plain vowels
    assert said("make") == "m ˈ A k" and pronounce("§", lex, tokens) is None
    assert s_ending("k æ t") == "k æ t s" and s_ending("d ɔ ɡ") == "d ɔ ɡ z" and ed_ending("w ɑ n t") == "w ɑ n t ᵻ d"


# ---------------------------------------------------------------------- 2.14.0: what is happening, and video
def task_of(c, job_id, timeout=60):
    """A task once it has ended, with its trail and its plan."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = c.get(f"/api/jobs/{job_id}").json()
        if task["status"] not in ("queued", "running", "waiting"):
            return task
        time.sleep(0.1)
    raise AssertionError(f"task still running: {task}")


def wait_video(c, job, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = next(j for j in c.get("/api/video").json()["jobs"] if j["id"] == job["id"])
        if state["state"] != "running":
            return state
        time.sleep(0.1)
    raise AssertionError(f"video job still running: {state}")


def ready_for_media(c, tmp):
    """A voice, a transcription model and an image model, whatever the tests before left."""
    if not c.get("/api/sound").json()["can"]["tts"]:
        c.post("/api/sound/models", json={"key": "kokoro-v1", "path": model_folder(tmp, "kokoro-v")})
    if not c.get("/api/sound").json()["can"]["stt"]:
        c.post("/api/sound/models", json={"key": "whisper-base", "path": model_folder(tmp, "whisper-v")})
    if not c.get("/api/images").json()["default"]:
        weights = tmp / "checkpoints-v" / "small-sd.safetensors"
        weights.parent.mkdir(exist_ok=True)
        weights.write_bytes(b"\0" * 4096)
        c.post("/api/images/models", json={"name": "Small SD", "files": {"model": str(weights)}, "settings": {"steps": 6}})
    assert c.get("/api/sound").json()["can"]["tts"] and c.get("/api/images").json()["default"]


def test_every_piece_of_work_is_a_task_with_a_plan_and_a_trail(engine):
    """2.14.0: nobody could tell what a picture or a recording was doing, or whether it was stuck. Everything that
    works in the background is a task now: listed in Activity, with its plan, each step with its time, what the
    program printed, and the seconds since its last sign of life."""
    c, tmp = engine["client"], engine["tmp"]
    ready_for_media(c, tmp)
    # a recording made in the Sound window
    job = c.post("/api/sound/jobs", json={"kind": "speak", "text": "Host: Hello and welcome.\nGuest: Thank you for having me."}).json()
    assert job["job_id"].startswith("job_")
    assert wait_sound(c, job)["state"] == "done"
    task = task_of(c, job["job_id"])
    assert task["kind"] == "sound" and task["status"] == "done" and task["title"].startswith("Speak: ") and task["quiet"] is None
    steps = [e for e in task["trail"] if e["kind"] == "step"]
    assert len(steps) >= 2 and all(e["t"] and e["text"] for e in steps) and task["trail"][-1]["text"] == "Finished"
    assert len({e["text"] for e in steps}) == len(steps)                   # a step said twice is one line
    plan = task["detail"]
    assert plan["stages"] == ["Prepare the voices", "Speak", "Join and save"] and plan["stage"] == 2
    assert plan["speech"]["lines"] == 2 and plan["speech"]["where"] == "on this computer" and plan["speech"]["words"] == 8
    assert task["result"]["media"][0]["path"].endswith(".mp3") and Path(task["result"]["media"][0]["path"]).exists()
    # a failure is in the trail too, in words
    bad = c.post("/api/sound/jobs", json={"kind": "transcribe", "audio": str(tmp / "no-such.wav")}).json()
    failed = task_of(c, bad["job_id"])
    assert failed["status"] == "failed" and "There is no file" in failed["error"]
    assert failed["trail"][-1]["kind"] == "error" and "There is no file" in failed["trail"][-1]["text"]

    # a picture: while it works the list says where it is and how long it has been quiet; the program's own lines are kept
    slow = c.post("/api/images/generate", json={"prompt": "a slow picture of a boat", "steps": 8, "seed": 3}).json()
    seen = None
    for _ in range(100):
        rows = c.get("/api/jobs", params={"status": "active", "kinds": "images"}).json()["jobs"]
        seen = next((r for r in rows if r["id"] == slow["job_id"] and r.get("fraction")), None)
        if seen:
            break
        time.sleep(0.1)
    assert seen and seen["status"] == "running" and 0 < seen["fraction"] <= 1 and " of 8" in seen["progress"]
    assert isinstance(seen["quiet"], int) and seen["quiet"] < 5
    live = c.get(f"/api/jobs/{slow['job_id']}").json()
    assert live["detail"]["stages"] == ["Get ready", "Load the model", "Draw", "Save"] and live["detail"]["stage"] == 2
    assert live["detail"]["picture"]["prompt"] == "a slow picture of a boat" and live["detail"]["picture_run"]["steps"] == 8
    assert live["detail"]["picture_run"]["seed"] == 3 and "stable-diffusion.cpp" in live["detail"]["picture_run"]["engine"]
    assert any(e["kind"] == "output" and "stand-in" in e["text"] for e in live["trail"])
    assert any(e["kind"] == "note" and e["text"].startswith("Started: ") and "--steps 8" in e["text"] for e in live["trail"])
    drawn = [e["text"] for e in live["trail"] if e["kind"] == "step"]          # a counter that moves is not a line each time
    assert drawn[-1].endswith(": drawing") and not any("step 2 of 8" in t for t in drawn)
    # the work says how long a silence is normal now (the stand-in's steps take "1.20s/it"), and what to expect
    assert live["pace"] and 1 <= live["pace"] <= 200
    assert live["detail"]["picture_run"]["expected"].startswith("about ")
    assert any(e["kind"] == "output" and "/8 " in e["text"] for e in live["trail"])       # the program's own lines have every step
    assert c.post(f"/api/jobs/{slow['job_id']}/cancel").json()["cancelled"]          # stopped from Activity
    assert wait_picture(c, slow["id"])["state"] == "stopped"
    assert task_of(c, slow["job_id"])["status"] == "cancelled"
    listed = c.get("/api/jobs", params={"kinds": "sound,images"}).json()["jobs"]
    assert {r["kind"] for r in listed} == {"sound", "images"} and c.get("/api/busy").json()["active"] == 0

    # in a chat the tool's own steps are shown inside its step and kept with it; the chat's task has them in its trail
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "paint a picture of a red kite"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    said = [t["text"] for t in step["trail"]]
    assert step["ok"] and any(t.startswith("Loading ") for t in said) and said[-1].endswith(": drawing")
    trail = c.get(f"/api/jobs/{job['job_id']}").json()["trail"]
    texts = [e["text"] for e in trail]
    assert any(t.startswith("Make picture") or "red kite" in t for t in texts) and any(t.startswith("Done: ") for t in texts)
    assert any(e["kind"] == "output" for e in trail)
    # a chat does many things in turn: one tool's stages are not the chat's plan (what the tool made is noted)
    chat_plan = c.get(f"/api/jobs/{job['job_id']}").json()["detail"]
    assert "stages" not in chat_plan and chat_plan["picture"]["prompt"] == "a red kite"


def test_a_followed_download_is_a_task_that_can_be_stopped(tmp_path):
    """Downloads and installs keep a state of their own; as tasks they are followed: a new file is a step, bytes
    are progress, and stopping the task asks the download to stop in its own way."""
    import asyncio
    import types
    from dva.db import DB
    from dva.jobs import Jobs
    events = []
    paths = types.SimpleNamespace(data=tmp_path / "data")
    paths.data.mkdir()
    app = types.SimpleNamespace(db=DB(tmp_path / "t.sqlite3"), paths=paths, log=lambda m: None, approvals={},
                                bus=types.SimpleNamespace(publish=lambda kind, /, **k: events.append((kind, k))))

    async def run():
        jobs = app.jobs = Jobs(app)
        state = {"state": "starting", "done": 0, "total": 3000, "file": "", "cancel": False}

        async def work(fail=False, forever=False):
            for name in ("model.gguf", "vae.safetensors"):
                state.update(state="downloading", file=name)
                for _ in range(3):
                    await asyncio.sleep(0.45)
                    state["done"] += 500
                    if state["cancel"]:
                        state.update(state="cancelled")
                        return
            if fail:
                state.update(state="failed", error="The publisher moved the file")
                return
            while forever and not state["cancel"]:
                await asyncio.sleep(0.1)
            state.update(state="cancelled" if state["cancel"] else "done")
        row = jobs.follow("download", "Download: Test model", work(), lambda: state, stop=lambda: state.update(cancel=True))
        await asyncio.wait_for(jobs.tasks[row["id"]], 20)
        done = jobs.get(row["id"])
        assert done["status"] == "done" and done["kind"] == "download"
        steps = [e["text"] for e in done["trail"] if e["kind"] == "step"]
        assert steps == ["Downloading model.gguf", "Downloading vae.safetensors"] and done["trail"][-1]["text"] == "Finished"
        moved = [k for kind, k in events if kind == "job_progress"]
        assert moved and moved[-1]["fraction"] == 1.0 and " of " in moved[-1]["text"]
        # it fails in the download's own words
        state.update(state="starting", done=0, cancel=False)
        row = jobs.follow("download", "Download: Test model", work(fail=True), lambda: state)
        await asyncio.wait_for(jobs.tasks[row["id"]], 20)
        failed = jobs.get(row["id"])
        assert failed["status"] == "failed" and "The publisher moved the file" in failed["error"]
        # stopped from Activity: the download is asked to stop, and the task says stopped
        state.update(state="starting", done=0, cancel=False, error=None)
        row = jobs.follow("download", "Download: Test model", work(forever=True), lambda: state, stop=lambda: state.update(cancel=True))
        await asyncio.sleep(1.2)
        assert jobs.quiet(row["id"]) is not None and jobs.list(status="active", kinds=["download"])[0]["id"] == row["id"]
        task = jobs.tasks[row["id"]]
        assert jobs.cancel(row["id"])
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 20)
        assert jobs.get(row["id"])["status"] == "cancelled" and state["state"] == "cancelled"
        # quiet: the seconds since the last sign of life, only while it runs
        assert jobs.quiet(row["id"]) is None
        # work that goes one at a time says when it waits its turn (it would look stuck otherwise), and only then
        from dva.jobs import turn
        lock, said = asyncio.Lock(), []

        async def one(delay, hold):
            await asyncio.sleep(delay)
            async with turn(lock, said.append, "Waiting for another picture"):
                await asyncio.sleep(hold)
        await asyncio.gather(one(0, 0.4), one(0.1, 0))
        assert said == ["Waiting for another picture"] and not lock.locked()
        await one(0, 0)
        assert len(said) == 1
    asyncio.run(run())


def test_a_video_is_built_from_scenes_changed_and_kept(engine):
    """2.14.0: a video made entirely on this computer from scenes (pictures, narration, captions), written by a
    text model from an idea, then cut, joined and subtitled; every one of them a task with a plan and a trail."""
    import av
    from PIL import Image
    c, tmp = engine["client"], engine["tmp"]
    ready_for_media(c, tmp)
    start = c.get("/api/video").json()
    assert start["can"]["assemble"] and start["can"]["voice"] and start["can"]["pictures"] and not start["can"]["clip"]
    assert "ended in September 2026" in start["notes"]["openai"] and start["recipes"][0]["key"] == "wan21-t2v-small"
    assert not start["online"]["on"] and start["online"]["model"] == "gemini-omni-1.1-flash"
    assert c.post("/api/video/jobs", json={"kind": "make", "scenes": []}).status_code == 400
    assert c.post("/api/video/jobs", json={"kind": "clip", "prompt": " "}).status_code == 400
    # the user's own pictures, a narration for each, captions in step with the voice
    for name, colour in (("one.png", (200, 120, 40)), ("two.png", (40, 120, 200))):
        Image.new("RGB", (900, 600), colour).save(tmp / name)
    job = c.post("/api/video/jobs", json={"kind": "make", "title": "Two pictures", "shape": "square", "scenes": [
        {"image": str(tmp / "one.png"), "narration": "Solar pumps lift water with sunlight, and they cost less every year."},
        {"image": str(tmp / "two.png"), "narration": "That is why farmers choose them."},
        {"caption": "The end", "seconds": 2}]}).json()
    made = wait_video(c, job)
    assert made["state"] == "done", made
    video = made["result"]
    p = video["params"]
    assert video["kind"] == "video" and video["path"].endswith(".mp4") and p["mode"] == "assembled" and len(p["scenes"]) == 3
    assert (p["width"], p["height"]) == (960, 960) and p["captions"] and p["voices"] and p["music"] is None
    facts = c.get("/api/video/info", params={"path": video["path"]}).json()
    assert facts["audio"] and facts["width"] == 960 and abs(facts["seconds"] - p["seconds"]) < 0.3 and p["seconds"] > 5
    subtitles = Path(p["subtitles"]).read_text(encoding="utf-8")
    assert "Solar pumps lift water" in subtitles and "The end" in subtitles and "-->" in subtitles
    task = task_of(c, job["job_id"])
    assert task["kind"] == "video" and task["status"] == "done" and task["result"]["media"][0]["path"] == video["path"]
    assert task["detail"]["stages"] == ["Record the narration", "Put it together", "Save"] and task["detail"]["stage"] == 2
    assert task["detail"]["video"]["scenes"] == 3 and task["detail"]["video_run"]["frames"] > 100
    assert task["detail"]["narration"]["scenes_spoken"] == 2 and "speech" not in task["detail"]
    texts = [e["text"] for e in task["trail"]]
    assert "Narration 1 of 3…" in texts and any(t.startswith("Putting 3 scenes together") for t in texts)
    assert any(t.startswith("Saved ") and "3 scenes" in t for t in texts)
    with av.open(video["path"]) as box:                     # the picture is really in the frames: the first scene is orange
        frame = next(box.decode(video=0)).to_image()
    r, g, b = frame.getpixel((480, 200))
    assert r > 150 and b < 90, (r, g, b)
    assert not [m for m in c.get("/api/sound/library").json()["items"] if m["name"].startswith("scene-")]   # no leftovers

    # from an idea: a text model writes the scenes, the image model makes their pictures
    board = wait_video(c, c.post("/api/video/jobs", json={"kind": "storyboard", "idea": "tea farming", "minutes": 0.5}).json())
    assert board["state"] == "done", board
    scenes = board["result"]["scenes"]
    assert len(scenes) == 3 and all(s["narration"] and s["picture"] for s in scenes) and "tea farming" in board["result"]["title"]
    job = c.post("/api/video/jobs", json={"kind": "make", "title": board["result"]["title"], "scenes": scenes, "captions": False}).json()
    film = wait_video(c, job)
    assert film["state"] == "done", film
    fp = film["result"]["params"]
    assert len(fp["pictures_made"]) == 3 and (fp["width"], fp["height"]) == (1280, 720) and not fp["captions"]
    assert all(Path(s["image"]).exists() for s in fp["scenes"])
    ftask = task_of(c, job["job_id"])
    assert ftask["detail"]["stages"] == ["Make the pictures", "Record the narration", "Put it together", "Save"]
    assert ftask["detail"]["pictures"]["made"] == 3 and "picture_run" not in ftask["detail"]
    # a picture made for the video has a "Save" of its own: it did not move the video's plan to its last stage
    saved = [n for n, e in enumerate(ftask["trail"]) if e["text"].startswith("Saved 1 picture")]
    spoke = [n for n, e in enumerate(ftask["trail"]) if e["text"].startswith("Narration 1 of 3")]
    assert len(saved) == 3 and saved[-1] < spoke[0] and ftask["detail"]["stage"] == 3
    assert any(e["kind"] == "output" for e in ftask["trail"])              # the image engine's own lines are in the video's trail

    # changing a video: a part of it without its sound; two joined; subtitles written from what is said
    part = wait_video(c, c.post("/api/video/jobs", json={"kind": "edit", "source": video["path"], "start": 1, "end": 3, "mute": True}).json())
    assert part["state"] == "done", part
    cut = c.get("/api/video/info", params={"path": part["result"]["path"]}).json()
    assert abs(cut["seconds"] - 2.0) < 0.2 and not cut["audio"] and part["result"]["params"]["sound"] == "none"
    joined = wait_video(c, c.post("/api/video/jobs", json={"kind": "edit", "subtitles": "auto", "name": "Both", "clips": [
        {"path": video["path"], "start": 0, "end": 2}, part["result"]["path"]]}).json())
    assert joined["state"] == "done", joined
    jp = joined["result"]["params"]
    assert jp["mode"] == "joined" and abs(jp["seconds"] - 4.0) < 0.3 and jp["sound"] == "own" and jp["captions"]
    assert Path(jp["subtitles"]).exists() and Path(joined["result"]["path"]).name.startswith("both-")
    empty = wait_video(c, c.post("/api/video/jobs", json={"kind": "edit", "source": video["path"], "start": 50, "end": 60}).json())
    assert empty["state"] == "failed" and "is empty" in empty["error"]
    gone = wait_video(c, c.post("/api/video/jobs", json={"kind": "edit", "source": str(tmp / "nope.mp4")}).json())
    assert gone["state"] == "failed" and "There is no video" in gone["error"]

    # a recording from the camera or the screen is kept like the others; what is not a video is refused
    with open(part["result"]["path"], "rb") as f:
        up = c.post("/api/video/upload", params={"keep": 1}, files={"file": ("My recording.mp4", f, "video/mp4")}).json()
    assert up["record"]["source"] == "recorded" and up["info"]["width"] == 960
    assert c.post("/api/video/upload", files={"file": ("notes.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert c.post("/api/video/upload", files={"file": ("broken.mp4", b"not a video", "video/mp4")}).status_code == 400
    library = c.get("/api/video/library").json()["videos"]
    assert {v["source"] for v in library} == {"local", "recorded"} and len(library) == 5 and all(v["exists"] for v in library)
    c.delete(f"/api/video/library/{up['record']['id']}", params={"file": 1})
    assert not Path(up["record"]["path"]).exists()

    # a clip by a video model on this computer: the engine is asked the way its own notes say
    none = wait_video(c, c.post("/api/video/jobs", json={"kind": "clip", "prompt": "a kite in the wind"}).json())
    assert none["state"] == "failed" and "No video model is set up" in none["error"]
    files = {}
    for slot in ("diffusion_model", "vae", "t5xxl"):
        files[slot] = str(tmp / f"wan-{slot}.safetensors")
        Path(files[slot]).write_bytes(b"\0" * 2048)
    assert c.post("/api/video/models", json={"name": "x", "files": {"vae": files["vae"]}}).status_code == 400
    model = c.post("/api/video/models", json={"name": "Test Wan", "files": files, "settings": {"steps": 4, "frames": 9, "width": 320, "height": 176}}).json()
    assert model["ready"] and model["settings"]["video"]
    assert model["id"] not in {m["id"] for m in c.get("/api/images").json()["models"]}          # not offered for pictures
    assert c.get("/api/images").json()["default"] != model["id"] and c.get("/api/video").json()["can"]["clip_local"]
    job = c.post("/api/video/jobs", json={"kind": "clip", "prompt": "a kite in the wind", "seed": 11}).json()
    clip = wait_video(c, job)
    assert clip["state"] == "done", clip
    sent = json.loads((tmp / "wan-asked-video.json").read_text()) if (tmp / "wan-asked-video.json").exists() \
        else json.loads(Path(files["diffusion_model"]).with_name("asked-video.json").read_text())
    assert sent["mode"] == "vid_gen" and sent["diffusion_model"] == files["diffusion_model"] and sent["t5xxl"] == files["t5xxl"]
    assert (sent["frames"], sent["fps"], sent["steps"], sent["seed"], sent["width"], sent["height"]) == ("9", "16", "4", "11", "320", "176")
    assert sent["output"].endswith(".avi") and sent["flow_shift"] == "3" and sent["sampler"] == "euler"
    cp = clip["result"]["params"]
    assert clip["result"]["path"].endswith(".mp4") and cp["mode"] == "clip" and cp["width"] == 320 and cp["seconds"] > 0.3
    ctask = task_of(c, job["job_id"])
    assert ctask["detail"]["stages"] == ["Get ready", "Load the model", "Make the frames", "Save"]
    assert ctask["detail"]["clip"]["frames"] == 9 and any(e["kind"] == "output" for e in ctask["trail"])
    c.delete(f"/api/video/models/{model['id']}")
    assert not c.get("/api/video").json()["models"]


def test_an_agent_makes_a_video_and_cuts_it(engine):
    c, tmp = engine["client"], engine["tmp"]
    ready_for_media(c, tmp)
    chat = c.post("/api/chats", json={"harness": "video-producer"}).json()
    shown = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "show system prompt"}).json()
    assert wait_job(c, shown["job_id"])["status"] == "done"
    assert "make_video once with all of them" in last_assistant(c, chat["id"])["content"]
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "make a film about a lighthouse"}).json()
    assert wait_job(c, job["job_id"], timeout=120)["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["name"] == "make_video" and step["ok"], step
    film = step["media"][0]
    assert film["kind"] == "video" and film["path"].endswith(".mp4") and Path(film["path"]).parent.name == "video"
    assert "2 scenes" in step["result"] and "narrated" in step["result"] and "with captions" in step["result"]
    said = [t["text"] for t in step["trail"]]                    # the chat's step says what the tool was doing
    assert any(t.startswith("Picture 1 of 2") for t in said) and "Narration 2 of 2…" in said
    assert any(t.startswith("Putting 2 scenes together") for t in said)
    # the same chat cuts it
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": f"cut the video {film['path']} to 0.5-2.5"}).json()
    assert wait_job(c, job["job_id"], timeout=120)["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["name"] == "edit_video" and step["ok"] and "2 seconds" in step["result"], step
    assert len(c.get("/api/video/library", params={"chat_id": chat["id"]}).json()["videos"]) == 2
    # no video model is set up: the clip tool is not offered, so the model cannot call it in vain
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "film a clip of a kite"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    assert not [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"]


# ---------------------------------------------------------------------------------------------- 2.17.0
def work_job(c, window, job_id, until, timeout=60):
    """A request of the Images or the Video window, once `until(job)` holds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = next(j for j in c.get(f"/api/{window}").json()["jobs"] if j["id"] == job_id)
        if until(job):
            return job
        time.sleep(0.1)
    raise AssertionError(f"{window} job never got there: {job}")


def test_pictures_are_paused_stopped_and_continued_with_what_they_made(engine):
    """2.17.0. A picture on a processor takes a quarter of an hour: the request can be paused where it is (the
    engine is frozen and goes on from the same step), stopped (the finished pictures are kept), and continued
    (only the missing pictures are made, with the seeds they would have had)."""
    import psutil
    c, tmp = engine["client"], engine["tmp"]
    ready_for_media(c, tmp)
    assert c.get("/api/images").json()["pause_stop_minutes"] == 20
    job = c.post("/api/images/generate", json={"prompt": "a slow picture of three kites", "count": 3, "steps": 4,
                                               "width": 256, "height": 256}).json()
    assert job["state"] == "running" and job["count"] == 3 and job["request"]["seed"] > 0      # the seed is chosen and kept
    seed = job["request"]["seed"]
    # pause while the first picture is drawn: the engine is frozen, nothing moves, nothing is called stuck
    work_job(c, "images", job["id"], lambda j: "picture 1 of 3, step" in j["text"] and j["can_pause"])
    paused = c.post(f"/api/images/jobs/{job['id']}/pause").json()
    assert paused["state"] == "paused" and paused["programs"], paused
    pid = paused["programs"][0][0]
    if os.name != "nt":
        assert psutil.Process(pid).status() == psutil.STATUS_STOPPED
    row = c.get(f"/api/jobs/{job['job_id']}").json()
    assert row["status"] == "running" and row["paused"] and row["can_pause"] and row["quiet"] is None
    at = row["progress"]
    time.sleep(1.6)                                           # three steps' worth of time
    row = c.get(f"/api/jobs/{job['job_id']}").json()
    assert row["progress"] == at and row["paused"]
    assert [j for j in c.get("/api/jobs", params={"status": "active", "kinds": "images"}).json()["jobs"]
            if j["id"] == job["job_id"]][0]["paused"]
    assert c.post(f"/api/images/jobs/{job['id']}/resume").json()["state"] == "running"
    assert not c.get(f"/api/jobs/{job['job_id']}").json()["paused"]
    # stop when the first picture is finished: it is kept, and is in the library although the request did not finish
    work_job(c, "images", job["id"], lambda j: len(j["images"]) >= 1)
    c.delete(f"/api/images/jobs/{job['id']}")
    stopped = work_job(c, "images", job["id"], lambda j: j["state"] not in ("running", "paused"))
    kept = len(stopped["images"])
    assert stopped["state"] == "stopped" and 1 <= kept < 3 and stopped["can_continue"], stopped
    assert stopped["text"] == f"Stopped: {kept} of 3 pictures are kept" and "programs" not in stopped
    first = stopped["images"][0]
    assert Path(first["path"]).exists() and first["id"] in {m["id"] for m in c.get("/api/images/library").json()["images"]}
    for _ in range(50):                                       # the engine it had started is ended, not left behind
        if not psutil.pid_exists(pid):
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(pid)
    trail = [e["text"] for e in c.get(f"/api/jobs/{job['job_id']}").json()["trail"]]
    assert any(t.startswith("Paused: it stops by itself after 20 minutes") for t in trail) and "Continued" in trail
    # continue: the same request goes on as a new task; only the missing pictures are drawn, with their own seeds
    again = c.post(f"/api/images/jobs/{job['id']}/continue").json()
    assert again["id"] == job["id"] and again["state"] == "running" and again["job_id"] != job["job_id"]
    done = work_job(c, "images", job["id"], lambda j: j["state"] not in ("running", "paused"))
    assert done["state"] == "done" and len(done["images"]) == 3 and not done["can_continue"], done
    assert done["images"][0]["path"] == first["path"]
    assert [asked(m["path"])[0]["seed"] for m in done["images"]] == [str(seed), str(seed + 1), str(seed + 2)]
    assert [m["params"]["index"] for m in done["images"]] == [0, 1, 2]
    task = task_of(c, again["job_id"])
    assert any(e["text"] == f"Continuing: {kept} of 3 pictures exist already and are kept" for e in task["trail"])
    assert task["detail"]["picture"]["already_made"] == kept and len(task["result"]["media"]) == 3
    started = [e["text"] for e in task["trail"] if e["text"].startswith("Started: ")]
    assert len(started) == 3 - kept                           # one run of the engine for each missing picture
    assert c.post(f"/api/images/jobs/{job['id']}/continue").status_code == 400      # nothing to continue
    assert c.post(f"/api/images/jobs/{job['id']}/pause").status_code == 400

    # a pause that lasts too long becomes a stop, so that the engine's memory is free again; it can be continued
    c.put("/api/settings", json={"pause_stop_minutes": 0.02})
    long = c.post("/api/images/generate", json={"prompt": "a slow picture, paused too long", "steps": 6, "seed": 5,
                                                "width": 256, "height": 256}).json()
    work_job(c, "images", long["id"], lambda j: ", step" in j["text"] or "step " in j["text"])
    assert c.post(f"/api/jobs/{long['job_id']}/pause").json() == {"paused": True}      # the task's own page pauses it too
    assert work_job(c, "images", long["id"], lambda j: j["state"] != "running")["state"] == "paused"
    ended = work_job(c, "images", long["id"], lambda j: j["state"] not in ("running", "paused"), timeout=20)
    assert ended["state"] == "stopped" and ended["text"].startswith("Stopped after being paused for 0.02 minutes")
    assert ended["can_continue"] and not ended["images"]
    assert task_of(c, long["job_id"])["error"].startswith("Stopped after being paused")
    c.put("/api/settings", json={"pause_stop_minutes": 20})
    full = work_job(c, "images", c.post(f"/api/images/jobs/{long['id']}/continue").json()["id"],
                    lambda j: j["state"] not in ("running", "paused"))
    assert full["state"] == "done" and asked(full["images"][0]["path"])[0]["seed"] == "5"
    # a finished request is taken off the list; what it made stays
    assert c.post(f"/api/images/jobs/{full['id']}/forget").json() == {"ok": True}
    assert full["id"] not in {j["id"] for j in c.get("/api/images").json()["jobs"]} and Path(full["images"][0]["path"]).exists()
    assert c.post(f"/api/images/jobs/{full['id']}/forget").status_code == 404
    # 2.26.0: a chat reply pauses too (at its next step, giving its model back) and resumes; a finished task cannot
    chat = c.post("/api/chats", json={}).json()
    reply = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "hello"}).json()
    if c.post(f"/api/jobs/{reply['job_id']}/pause").json() == {"paused": True}:
        assert c.post(f"/api/jobs/{reply['job_id']}/resume").json() == {"resumed": True}
    assert wait_job(c, reply["job_id"])["status"] == "done"
    assert c.post(f"/api/jobs/{reply['job_id']}/pause").json() == {"paused": False}


def test_a_video_is_paused_stopped_and_continued_without_redoing_its_parts(engine):
    """2.17.0. A video keeps the pictures of its scenes, the narration and the music it has made: one that was
    stopped goes on with what is missing. The encoder and a clip's engine can be paused where they are."""
    c, tmp = engine["client"], engine["tmp"]
    ready_for_media(c, tmp)
    scenes = [{"picture": "a slow picture of a tea field", "narration": "Tea grows on the hills."},
              {"picture": "a slow picture of a tea factory", "narration": "The leaves are dried here."},
              {"caption": "The end", "seconds": 40}]
    job = c.post("/api/video/jobs", json={"kind": "make", "title": "Tea, continued", "scenes": scenes, "shape": "square"}).json()
    assert job["state"] == "running" and len(job["request"]["scenes"]) == 3
    # stop after the first scene's picture: it is kept in the request, so continuing does not draw it again
    work_job(c, "video", job["id"], lambda j: len(j["made"]) >= 1, timeout=90)
    c.delete(f"/api/video/jobs/{job['id']}")
    stopped = work_job(c, "video", job["id"], lambda j: j["state"] not in ("running", "paused"))
    assert stopped["state"] == "stopped" and stopped["can_continue"] and len(stopped["made"]) == 1, stopped
    assert stopped["text"] == "Stopped: the pictures of 1 scene are kept"
    first_picture = stopped["request"]["scenes"][0]["image"]
    assert Path(first_picture).exists() and not stopped["request"]["scenes"][1].get("image")
    again = c.post(f"/api/video/jobs/{job['id']}/continue").json()
    assert again["id"] == job["id"] and again["state"] == "running"
    # pause while it is put together: the encoder waits between two frames; then stop it there
    work_job(c, "video", job["id"], lambda j: j["text"].startswith("Encoding: frame") and j["can_pause"], timeout=120)
    assert c.post(f"/api/video/jobs/{job['id']}/pause").json()["state"] == "paused"
    time.sleep(0.6)                                           # the frame in hand is finished
    at = c.get(f"/api/jobs/{again['job_id']}").json()["progress"]
    time.sleep(1.0)
    row = c.get(f"/api/jobs/{again['job_id']}").json()
    assert row["progress"] == at and row["paused"] and row["quiet"] is None
    c.delete(f"/api/video/jobs/{job['id']}")
    halfway = work_job(c, "video", job["id"], lambda j: j["state"] not in ("running", "paused"))
    assert halfway["state"] == "stopped" and len(halfway["made"]) == 2 and not halfway.get("result")
    for _ in range(30):                                       # a stopped video leaves no half file
        if not list((tmp / "data" / "video").glob("tea-continued-*.mp4")):
            break
        time.sleep(0.1)
    assert not list((tmp / "data" / "video").glob("tea-continued-*.mp4"))
    second = task_of(c, again["job_id"])
    assert any(e["text"] == "Continuing: the pictures of 1 scene exist already and are kept" for e in second["trail"])
    assert len([e for e in second["trail"] if e["text"].startswith("Saved 1 picture")]) == 1      # only the missing one
    work = tmp / "data" / "work" / job["id"]
    assert (work / "scene-1.json").exists() and (work / "scene-2.json").exists()
    # continue again: nothing is drawn or spoken a second time, the video is put together
    third = c.post(f"/api/video/jobs/{job['id']}/continue").json()
    done = work_job(c, "video", job["id"], lambda j: j["state"] not in ("running", "paused"), timeout=120)
    assert done["state"] == "done" and not done["can_continue"], done
    p = done["result"]["params"]
    assert len(p["pictures_made"]) == 2 and p["scenes"][0]["image"] == first_picture and p["voices"] and p["seconds"] > 40
    last = task_of(c, third["job_id"])
    texts = [e["text"] for e in last["trail"]]
    assert "Narration 1 of 3: recorded before, used as it is" in texts and "Narration 2 of 3: recorded before, used as it is" in texts
    assert not any(t.startswith("Started: ") for t in texts) and "Make the pictures" not in last["detail"]["stages"]
    assert not work.exists()                                  # what was kept for continuing is cleared when it is done
    assert not [m for m in c.get("/api/sound/library").json()["items"] if m["name"].startswith("scene-")]

    # a clip made by a model on this computer: the engine is frozen and thawed; continuing makes the same clip
    files = {}
    for slot in ("diffusion_model", "vae", "t5xxl"):
        files[slot] = str(tmp / f"wan2-{slot}.safetensors")
        Path(files[slot]).write_bytes(b"\0" * 2048)
    model = c.post("/api/video/models", json={"name": "Test Wan 2", "files": files,
                                              "settings": {"steps": 6, "frames": 9, "width": 320, "height": 176}}).json()
    clip = c.post("/api/video/jobs", json={"kind": "clip", "prompt": "a slow picture of a kite that moves"}).json()
    seed = clip["request"]["seed"]
    assert seed > 0
    work_job(c, "video", clip["id"], lambda j: "step " in j["text"] and j["can_pause"])
    assert c.post(f"/api/video/jobs/{clip['id']}/pause").json()["state"] == "paused"
    at = c.get(f"/api/jobs/{clip['job_id']}").json()["progress"]
    time.sleep(1.6)
    assert c.get(f"/api/jobs/{clip['job_id']}").json()["progress"] == at
    c.delete(f"/api/video/jobs/{clip['id']}")                 # stopped while paused: the frozen engine is ended
    assert work_job(c, "video", clip["id"], lambda j: j["state"] not in ("running", "paused"))["state"] == "stopped"
    made = work_job(c, "video", c.post(f"/api/video/jobs/{clip['id']}/continue").json()["id"],
                    lambda j: j["state"] not in ("running", "paused"), timeout=90)
    assert made["state"] == "done" and made["result"]["params"]["seed"] == seed, made
    sent = json.loads(Path(files["diffusion_model"]).with_name("asked-video.json").read_text())
    assert sent["seed"] == str(seed)
    c.delete(f"/api/video/models/{model['id']}")


def test_work_that_was_cut_off_comes_back_and_leaves_no_engine_behind(tmp_path):
    """2.17.0. The requests of the Images and Video windows are kept in the database: one that was running when
    the app went away comes back as cut off and can be continued, and an engine it left behind (frozen, if the
    request was paused) is ended, so that it does not hold its memory for ever."""
    import asyncio
    import subprocess
    import types
    import psutil
    from dva.db import DB
    from dva.work import Control, Works, end_leftover, watch
    db = DB(tmp_path / "t.db")
    said = []
    app = types.SimpleNamespace(db=db, log=said.append, paths=types.SimpleNamespace(data=tmp_path),
                                bus=types.SimpleNamespace(publish=lambda *a, **k: None),
                                jobs=types.SimpleNamespace(controls={}, listeners={}))
    works = Works(app, "images", "image_job")
    job = works.new("picture", "a kite", {"prompt": "a kite", "count": 2, "seed": 3}, images=[], count=2)
    left = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        psutil.Process(left.pid).suspend()                     # the request was paused when the app went away
        works.save(job, state="paused", images=[{"path": str(tmp_path / "one.png"), "name": "one.png"}],
                   programs=[[left.pid, psutil.Process(left.pid).create_time()],
                             [other.pid, psutil.Process(other.pid).create_time() - 500]])      # that number is another program now
        works.folder(job["id"]).joinpath("kept.txt").write_text("x")
        back = Works(app, "images", "image_job")               # the app starts again
        again = back.get(job["id"])
        assert again["state"] == "interrupted" and again["text"] == "The app was closed while this was running"
        assert again["request"] == {"prompt": "a kite", "count": 2, "seed": 3} and again["images"][0]["name"] == "one.png"
        assert back.view(again)["can_continue"] and not back.view(again)["can_pause"] and "programs" not in again
        assert left.wait(timeout=10) != 0 and other.poll() is None           # only the program that was really its own
        assert any("ended the engine it had left running" in line for line in said)
        assert db.get("media_work", job["id"])["state"] == "interrupted"
        assert end_leftover([[other.pid, 1.0], [99999999, 5.0], ["x", None]]) == 0 and other.poll() is None
        with pytest.raises(ValueError):
            back.pause(job["id"])
        back.forget(job["id"])
        assert not db.get("media_work", job["id"]) and not (tmp_path / "work" / job["id"]).exists()
        with pytest.raises(KeyError):
            back.get(job["id"])
    finally:
        for p in (left, other):
            if p.poll() is None:
                p.kill()

    async def limits():
        # time spent paused does not count against a time limit; a limit that is passed ends the program
        ctl = Control()
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(0.5); print('ok')",
                                                    stdout=asyncio.subprocess.PIPE)
        out = []

        async def read():
            out.append(await proc.stdout.read())
        loop = asyncio.get_running_loop()
        loop.call_later(0.2, ctl.pause)
        loop.call_later(3.2, ctl.resume)
        began = time.monotonic()
        code = await watch(proc, read(), 3.0, ctl)             # frozen for 3 s, with a limit of 3 s of work
        assert code == 0 and out == [b"ok\n".replace(b"\n", os.linesep.encode())] and time.monotonic() - began > 3.1
        assert 2.8 < ctl.paused_for() < 3.6 and not ctl.pids
        slow = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)",
                                                    stdout=asyncio.subprocess.PIPE)
        with pytest.raises(asyncio.TimeoutError):
            await watch(slow, slow.stdout.read(), 1.0, ctl)
        assert await slow.wait() != 0
        # the gate for work in a thread (the video encoder) opens on resume, or when the work is stopped
        ctl.pause()
        loop.call_later(0.4, ctl.resume)
        t = time.monotonic()
        await asyncio.to_thread(ctl.hold)
        assert 0.3 < time.monotonic() - t < 1.5
        ctl.pause()
        t = time.monotonic()
        await asyncio.to_thread(ctl.hold, lambda: time.monotonic() - t > 0.3)
        assert time.monotonic() - t < 1.5
    asyncio.run(limits())


# ---------------------------------------------------------------------------------------------- 2.18.0
def speechlike(path, seconds=5.0, loud=0.3, noise=0.002, rate=24000):
    """A recording shaped like a spoken sentence: a breath of room at both ends, stretches of sound with short gaps
    between them, and the room's noise underneath. Values above 1 are cut off, as an overdriven microphone does."""
    import wave
    import numpy as np
    rng = np.random.default_rng(7)
    t = np.arange(int(seconds * rate)) / rate
    on = ((t % 1.45) < 1.3) & (t > 0.3) & (t < seconds - 0.3)
    x = np.clip(loud * np.sin(2 * np.pi * 220 * t) * on + rng.normal(0, noise, len(t)), -1, 1)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def test_your_own_voice_is_recorded_sentence_by_sentence_checked_kept_and_changed(engine):
    """2.18.0. A voice is made from sentences read aloud: each take is checked at once; the voice is taken from
    the clearest second of one take, which the user chooses by ear; it keeps its takes and can be heard, renamed,
    extended, recorded again take by take, and deleted with all that belongs to it."""
    import sqlite3
    import numpy as np
    from dva.sound import read_pcm
    from dva.voices import SAMPLE, SCRIPT, SECOND, words_of
    c, tmp = engine["client"], engine["tmp"]
    ready_for_media(c, tmp)
    if not c.get("/api/sound").json()["can"].get("clone"):
        c.post("/api/sound/models", json={"key": "pocket-tts", "path": model_folder(tmp, "pocket")})
    script = c.get("/api/sound/voices/script").json()
    assert [s["id"] for s in script["sentences"]] == ["plain", "question", "joy", "list", "calm", "emphasis", "soft", "sounds"]
    assert script["ready"] and script["words_checked"] and script["heard"] == 1.04 and len(script["tips"]) == 5
    assert all(s["text"] and s["how"] for s in script["sentences"]) and "English" in script["language"]
    texts = {s["id"]: s["text"] for s in SCRIPT}
    uploads, voices_dir = tmp / "data" / "sound" / "uploads", tmp / "data" / "voices"

    def record(sentence=None, name=None, **shape):
        """A take as the window sends it: recorded, uploaded. Its name tells the stand-in what was said."""
        said = name or "says-" + "-".join(words_of(texts[sentence]))
        src = speechlike(tmp / "take.wav", **shape)
        return c.post("/api/sound/upload", files={"file": (said + ".wav", src.read_bytes(), "audio/wav")}).json()["path"]

    def begins_with(reference, take):
        """The reference starts with the take's best second: that second is what the voice model hears."""
        ref = read_pcm(reference, 24000, 1).reshape(-1)[:SECOND]
        own = read_pcm(take["path"], 24000, 1).reshape(-1)[int(take["best"] * 24000):][:SECOND]
        return len(ref) == len(own) == SECOND and float(np.corrcoef(ref, own)[0, 1]) > 0.99

    # every take is checked at once: what is in it, and whether the words of the sentence were heard
    good = record("question")
    check = c.post("/api/sound/voices/check", json={"audio": good, "sentence": "question"}).json()
    assert check["verdict"] == "good" and not check["problems"] and not check["notes"], check
    assert check["match"] >= 0.8 and check["heard"].startswith("did you know") and check["text"] == texts["question"]
    assert 3 < check["speech_seconds"] < 5 and -16 < check["level_db"] < -11 and check["noise_db"] < -45 and check["clipped"] == 0
    assert check["steady"] == 1.0 and 0.25 <= check["best"] <= 3.7            # there is a full second of speech in it
    for shape, said, told in (({"loud": 0.003, "noise": 0.0002}, None, "far too quiet"),
                              ({"loud": 1.6}, None, "too loud and distorts"),
                              ({"noise": 0.1}, None, "a lot of noise"),
                              ({"loud": 0.0, "noise": 0.0005}, None, "Almost nothing was heard"),
                              ({"seconds": 2.0}, None, "much shorter than the sentence"),
                              ({}, "says-something-else-entirely-now", "Other words were heard")):
        bad = c.post("/api/sound/voices/check", json={"audio": record("question", name=said, **shape), "sentence": "question"}).json()
        assert bad["verdict"] == "again" and any(told in p for p in bad["problems"]), (told, bad)
    fair = c.post("/api/sound/voices/check", json={"audio": record("question", noise=0.03), "sentence": "question"}).json()
    assert fair["verdict"] == "fair" and "some background noise" in fair["notes"][0] and not fair["problems"]
    own = c.post("/api/sound/voices/check", json={"audio": good, "text": "My own words.", "words": False}).json()
    assert own["sentence"] is None and own["heard"] is None and own["match"] is None and own["verdict"] == "good"
    assert c.post("/api/sound/voices/check", json={"audio": str(tmp / "nope.wav")}).status_code == 400
    # what was tried and not kept is removed, and only from the app's own folder for recordings
    outside = speechlike(tmp / "mine-elsewhere.wav")
    assert c.post("/api/sound/voices/discard", json={"paths": [p.as_posix() for p in uploads.glob("says-*")] + [str(outside)]}).json()["removed"] >= 7
    assert not list(uploads.glob("says-*")) and outside.exists()

    # the voice: whose it is has to be said; too little speech is not a voice; nothing is lost when it is refused
    takes = [{"sentence": s, "audio": record(s)} for s in ("plain", "question", "joy", "list", "calm", "emphasis", "soft")]
    takes[1]["check"] = c.post("/api/sound/voices/check", json=takes[1]).json()
    r = c.post("/api/sound/voices", json={"name": "Labatt", "takes": takes, "consent": {"whose": "mine"}})
    assert r.status_code == 400 and "Confirm" in r.json()["error"] and all(Path(t["audio"]).exists() for t in takes)
    lone = record("question", seconds=2.0)
    r = c.post("/api/sound/voices", json={"name": "Labatt", "takes": [{"sentence": "question", "audio": lone}],
                                          "consent": {"whose": "mine", "confirmed": True}})
    assert r.status_code == 400 and "too little speech" in r.json()["error"] and Path(lone).exists()
    assert c.post("/api/sound/voices/discard", json={"paths": [lone]}).json() == {"removed": 1}      # the window, on closing
    assert c.post("/api/sound/voices", json={"name": "Heart", "takes": takes, "consent": {"whose": "mine", "confirmed": True}}).status_code == 400
    voice = c.post("/api/sound/voices", json={"name": "Labatt", "note": "recorded at home", "takes": takes,
                                              "consent": {"whose": "mine", "confirmed": True}}).json()
    vid = voice["id"].removeprefix("voice:")
    path = f"/api/sound/voices/{voice['id']}"
    assert voice["kind"] == "recording" and voice["ready"] and voice["whose"] == "mine" and voice["note"] == "recorded at home"
    assert voice["statement"] == "I confirm this is my own voice." and voice["missing"] == ["sounds"] and voice["sample"] is None
    assert [t["sentence"] for t in voice["takes"]] == ["plain", "question", "joy", "list", "calm", "emphasis", "soft"]
    assert all(t["exists"] and Path(t["path"]).parent == voices_dir / vid and t["steady"] == 1.0 for t in voice["takes"])
    assert voice["takes"][1]["verdict"] == "good" and voice["takes"][1]["heard"].startswith("did you know")
    assert not any(Path(t["audio"]).exists() for t in takes)               # the uploads went into the voice
    # the voice model hears about one second: the voice is taken from the clearest second of one take, an even
    # way of speaking unless the user chooses another
    assert voice["heard"] == 1.04 and not voice["old"] and voice["lead"] == voice["takes"][0]["id"]
    assert [t["lead"] for t in voice["takes"]] == [True] + [False] * 6
    assert Path(voice["reference"]) == voices_dir / f"{vid}.wav" and begins_with(voice["reference"], voice["takes"][0])
    assert c.get("/api/sound/peaks", params={"path": voice["reference"]}).json()["seconds"] > 5
    listed = next(v for v in c.get("/api/sound").json()["voices"] if v["id"] == voice["id"])
    assert listed["ready"] and listed["name"] == "Labatt" and listed["kind"] == "recording"

    # it speaks like any other voice, asked for by its name
    spoken = wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": "This is me.", "voice": "Labatt"}).json())
    assert spoken["state"] == "done" and spoken["result"]["model_name"] == "Labatt", spoken
    seg = sound_jobs(tmp, "speak")[-1]["segments"][0]
    assert seg["ref_audio"] == voice["reference"] and seg["ref_text"] == texts["plain"]
    # hearing it: a sentence spoken by the voice, kept with the voice and not in the library
    before = len(c.get("/api/sound/library").json()["items"])
    task = task_of(c, c.post(f"{path}/sample", json={}).json()["job_id"])
    assert task["status"] == "done" and task["kind"] == "sound" and task["title"].startswith("Hear the voice Labatt")
    assert task["detail"]["stages"] == ["Prepare the voice", "Speak", "Save"] and "1.04 seconds" in task["detail"]["voice"]["what_the_model_hears"]
    heard = c.get(path).json()
    assert heard["sample"]["text"] == SAMPLE and Path(heard["sample"]["path"]).parent == voices_dir / vid and heard["sample"]["seconds"] > 1
    assert heard["takes"][0]["try"] == heard["sample"] and not any(t["try"] for t in heard["takes"][1:]) and not heard["sampling"]
    assert len(c.get("/api/sound/library").json()["items"]) == before
    # choosing by ear: the voice says the same sentence taken from every take in turn
    task_of(c, c.post(f"{path}/sample", json={"which": "all", "text": "Which of these sounds like me?"}).json()["job_id"])
    every = c.get(path).json()
    assert all(t["try"] and t["try"]["text"] == "Which of these sounds like me?" and Path(t["try"]["path"]).exists() for t in every["takes"])
    assert len({t["try"]["path"] for t in every["takes"]}) == 7 and not Path(heard["sample"]["path"]).exists()
    asked = sound_jobs(tmp, "speak")[-1]
    assert len(asked["segments"]) == 7 and len({s["ref_audio"] for s in asked["segments"]}) == 7       # one second of each take
    # … and the one that sounds most like its owner becomes what the voice is taken from
    chosen = c.put(path, json={"name": "Labatt, calm", "note": "evening voice", "lead": every["takes"][4]["id"]}).json()
    assert chosen["name"] == "Labatt, calm" and chosen["note"] == "evening voice" and chosen["lead"] == every["takes"][4]["id"]
    assert [t["lead"] for t in chosen["takes"]] == [False] * 4 + [True] + [False] * 2 and chosen["sample"] == every["takes"][4]["try"]
    assert begins_with(chosen["reference"], chosen["takes"][4])
    assert c.put(path, json={"name": "My voice"}).status_code == 400       # taken
    assert c.put(path, json={"lead": "nope"}).status_code == 404
    wait_sound(c, c.post("/api/sound/jobs", json={"kind": "speak", "text": "Still me.", "voice": "Labatt, calm"}).json())
    assert sound_jobs(tmp, "speak")[-1]["segments"][0]["ref_text"] == texts["calm"]
    # a sentence recorded again replaces its take (the voice follows when it was taken from it); a missing one is added
    old = chosen["takes"][4]
    again = c.post(f"{path}/takes", json={"sentence": "calm", "audio": record("calm", loud=0.2)}).json()
    new = next(t for t in again["takes"] if t["sentence"] == "calm")
    assert new["id"] != old["id"] and new["lead"] and again["lead"] == new["id"] and len(again["takes"]) == 7
    assert not Path(old["path"]).exists() and not Path(old["try"]["path"]).exists() and new["try"] is None and again["sample"] is None
    more = c.post(f"{path}/takes", json={"sentence": "sounds", "audio": record("sounds")}).json()
    assert len(more["takes"]) == 8 and more["missing"] == [] and more["lead"] == new["id"] and not list(uploads.glob("says-*"))
    assert c.post(f"{path}/takes", json={"sentence": "joy", "audio": record("joy", loud=0.0, noise=0.0003)}).status_code == 400
    # a take is removed; when it is the one the voice was taken from, the voice is taken from another
    less = c.delete(f"{path}/takes/{more['takes'][7]['id']}").json()
    assert len(less["takes"]) == 7 and not Path(more["takes"][7]["path"]).exists() and less["lead"] == new["id"]
    moved = c.delete(f"{path}/takes/{new['id']}").json()
    assert len(moved["takes"]) == 6 and moved["lead"] == moved["takes"][0]["id"] and begins_with(moved["reference"], moved["takes"][0])
    assert c.delete(f"{path}/takes/nope").status_code == 404

    # a voice from one recording you have: it is taken from that recording's clearest second, not its first
    late = tmp / "late.wav"
    speechlike(late, seconds=9.0)
    data = np.frombuffer(late.read_bytes()[44:], dtype="<i2").copy()
    data[: 24000 * 2] //= 200                                # two seconds of murmur before the speech proper
    late.write_bytes(late.read_bytes()[:44] + data.tobytes())
    up = c.post("/api/sound/upload", files={"file": ("my-old-recording.wav", late.read_bytes(), "audio/wav")}).json()
    file_voice = c.post("/api/sound/voices", json={"name": "From a file", "audio": up["path"], "transcript": "what I said",
                                                   "consent": {"whose": "mine", "confirmed": True}}).json()
    fpath = f"/api/sound/voices/{file_voice['id']}"
    assert len(file_voice["takes"]) == 1 and file_voice["takes"][0]["whole"] and file_voice["takes"][0]["lead"] and not file_voice["old"]
    assert file_voice["takes"][0]["best"] >= 2.0 and begins_with(file_voice["reference"], file_voice["takes"][0])
    assert len(file_voice["missing"]) == 8 and not Path(up["path"]).exists()
    grown = c.post(f"{fpath}/takes", json={"sentence": "plain", "audio": record("plain")}).json()
    assert [t["sentence"] for t in grown["takes"]] == ["plain", None] and grown["lead"] == file_voice["lead"] and len(grown["missing"]) == 7
    # a voice made before this release has no takes: it is shown with its recording, speaks from it as it was
    # given, and is taken from its clearest second once the user says so
    fid = file_voice["id"].removeprefix("voice:")
    db = sqlite3.connect(str(tmp / "data" / "kimilm.sqlite3"))
    db.execute("UPDATE voices SET detail_json='{}' WHERE id=?", (fid,))
    db.commit()
    db.close()
    before = c.get(fpath).json()
    assert before["old"] and before["lead"] == "whole" and [t["id"] for t in before["takes"]] == ["whole"] and before["sample"] is None
    task_of(c, c.post(f"{fpath}/sample", json={"text": "As I was."}).json()["job_id"])
    assert sound_jobs(tmp, "speak")[-1]["segments"][0]["ref_audio"] == before["reference"]
    assert c.get(fpath).json()["sample"]["text"] == "As I was."
    renewed = c.put(fpath, json={"lead": "whole"}).json()
    assert not renewed["old"] and len(renewed["takes"]) == 1 and renewed["takes"][0]["whole"] and renewed["takes"][0]["lead"]
    assert renewed["sample"] is None and begins_with(renewed["reference"], renewed["takes"][0])
    mine = {v["name"]: v for v in c.get("/api/sound/voices/mine").json()["voices"]}
    assert {"Labatt, calm", "From a file"} <= set(mine) and mine["Labatt, calm"]["updated"] >= mine["Labatt, calm"]["created"]

    # deleting a voice takes everything recorded for it; a default that pointed at it is cleared
    assert c.put("/api/sound/settings", json={"voice": voice["id"]}).json()["default_voice"] == voice["id"]
    assert c.get(path).json()["default"]
    c.delete(path)
    c.delete(fpath)
    assert not (voices_dir / vid).exists() and not (voices_dir / f"{vid}.wav").exists() and not (voices_dir / fid).exists()
    assert c.get(path).status_code == 404
    assert c.get("/api/sound").json()["default_voice"] != voice["id"]
    assert "Labatt, calm" not in {v["name"] for v in c.get("/api/sound").json()["voices"]}
    # agents use voices; none of their tools makes, changes or deletes one
    from dva.tools import builtin_tools
    assert [t.name for t in builtin_tools() if "voice" in t.name] == ["list_voices"]


def test_a_take_is_measured_and_judged_and_its_best_second_found():
    """2.18.0. What is said about a take comes from its sound: how much speech, how loud, how clear of the room,
    and where its fullest second of speech is: the second the voice model is given."""
    import numpy as np
    from dva.voices import SECOND, cut, judge, measure
    rate = 24000
    t = np.arange(5 * rate) / rate
    # short words with gaps, then from 2.6 s a stretch of unbroken speech
    on = (((t % 0.47) < 0.3) & (t > 0.5) & (t < 2.4)) | ((t > 2.6) & (t < 4.2))
    x = (0.1 * np.sin(2 * np.pi * 200 * t) * on + np.random.default_rng(1).normal(0, 0.0005, len(t))).astype("float32")
    facts = measure(x)
    assert facts["seconds"] == 5.0 and 2.5 < facts["speech_seconds"] < 3.1 and abs(facts["level_db"] + 23) < 1.5
    assert facts["noise_db"] < -60 and 0.45 <= facts["start"] <= 0.55 and 4.1 <= facts["end"] <= 4.25 and facts["clipped"] == 0
    assert facts["steady"] == 1.0 and 2.58 <= facts["best"] <= 3.2           # inside the unbroken stretch
    assert judge(facts, "one two three four five six seven")["verdict"] == "good"
    assert judge(facts, "one two three", heard="one two three")["match"] == 1.0
    assert judge(facts, "one two three four", heard="one two three for")["verdict"] == "fair"
    assert "Other words" in judge(facts, "one two three four", heard="")["problems"][0]
    assert "long silences" in judge({**facts, "seconds": 30.0}, "one two three")["notes"][0]
    assert "one breath" in judge({**facts, "steady": 0.5})["notes"][0]
    part = cut(x, facts)                                     # only the speech, at the common loudness, never overdriven
    assert 3.8 < len(part) / rate < 4.1 and abs(measure(part)["level_db"] + 23) < 0.6 and float(np.abs(part).max()) <= 0.95
    lead = cut(x, facts, lead=True)                          # from the best second on: what the model hears comes first
    assert abs(len(lead) / rate - (facts["end"] + 0.12 - facts["best"])) < 0.05 and measure(lead[:SECOND])["speech_seconds"] > 1.0
    loud = cut(x * 9, measure(x * 9))
    assert float(np.abs(loud).max()) <= 0.951
    choppy = measure((0.1 * np.sin(2 * np.pi * 200 * t) * ((t % 0.4) < 0.2)).astype("float32"))
    assert choppy["steady"] < 0.6 and judge(choppy)["verdict"] == "fair"
    assert measure(np.zeros(100, dtype="float32"))["speech_seconds"] == 0.0
    assert judge(measure(np.zeros(rate, dtype="float32")))["verdict"] == "again"


@pytest.mark.skipif(not os.environ.get("DVA_TEST_SOUND_MODELS"), reason="needs real speech models (DVA_TEST_SOUND_MODELS=folder)")
def test_the_real_voice_model_hears_one_second_of_a_recording(tmp_path):
    """2.18.0 is built on this, measured with the model KimiLM installs: the voice comes from the first 1.04
    seconds of the recording it is given, however long the recording is, and another second gives another voice.
    If a later model or library hears more, this fails, and voices.py should give it more."""
    import numpy as np
    from dva.sound import read_pcm
    from dva.voices import SECOND
    folder = Path(os.environ["DVA_TEST_SOUND_MODELS"]) / "sherpa-onnx-pocket-tts-int8-2026-01-26"
    voice = read_pcm(folder / "test_wavs" / "bria.wav", 24000, 1)

    def says(name, samples):
        ref = tmp_path / f"{name}.wav"
        with wave_file(ref) as f:
            f.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        spec = tmp_path / "job.json"
        spec.write_text(json.dumps({"kind": "speak", "out": str(tmp_path / f"{name}-out.wav"),
                                    "models": {"p": {"family": "pocket", "dir": str(folder)}},
                                    "segments": [{"model": "p", "text": "This voice comes from a short recording.", "ref_audio": str(ref)}]}))
        out = subprocess.run([sys.executable, "-c", "import sys, sherpa_onnx as so, dva.sound_worker as w\n"
                              "make = so.GenerationConfig\n"
                              "def seeded():\n    c = make(); c.extra = {'seed': '1'}; return c\n"
                              "so.GenerationConfig = seeded\n"                      # the same throw of the dice each time
                              "sys.exit(w.main([sys.argv[1]]))", str(spec)], cwd=str(ENGINE), capture_output=True, text=True)
        assert json.loads(out.stdout.strip().splitlines()[-1]).get("done"), out.stderr[-600:]
        return read_pcm(tmp_path / f"{name}-out.wav", 24000, 1).reshape(-1)
    same = lambda a, b: len(a) == len(b) and bool(np.allclose(a, b, atol=1e-3))      # noqa: E731
    one, three, ten = says("one", voice[:SECOND]), says("three", voice[: 3 * 24000]), says("ten", voice[: 10 * 24000])
    assert same(one, three) and same(three, ten)                 # what follows the first 1.04 seconds changes nothing
    assert not same(ten, says("later", voice[5 * 24000: 15 * 24000]))       # another second of the same speaker: another voice
    assert not same(one, says("less", voice[: SECOND // 2]))     # … and less than that second is not the same either


def wave_file(path, rate=24000):
    import wave
    f = wave.open(str(path), "wb")
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(rate)
    return f


# ---------------------------------------------------------------------------------------------- 2.19.0
def catalogue_chat(c):
    """A chat whose agent has the stand-in connector with a paged catalogue and a long guide."""
    conn = c.post("/api/connectors", json={"name": "Catalogue", "transport": "stdio", "command": "{python}",
                                           "args": [str(TESTS / "fake_mcp_server.py"), "--catalogue"]}).json()
    assert c.post(f"/api/connectors/{conn['id']}/test").json()["ok"]
    chat = c.post("/api/chats", json={"harness": "agent", "connectors": [conn["id"]]}).json()
    return conn, chat, Path(c.get(f"/api/chats/{chat['id']}").json()["workspace"])


def test_a_tool_result_is_saved_whole_and_a_long_one_is_kept_in_a_file(engine):
    """2.19.0. A small model could not copy a service's long answer into a file: it saw only the start, and had to
    type it again. Now any tool call can take save_to (the app writes the whole answer), and an answer too long to
    read whole is kept in a file whose path the model is given."""
    c = engine["client"]
    conn, chat, ws = catalogue_chat(c)
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "please save the guide to team-notes/guide.txt"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"][0]
    saved = Path(step["saved"])
    assert saved == ws / "team-notes" / "guide.txt" and step["ok"], step
    text = saved.read_text(encoding="utf-8")
    guide = json.loads(text)["result"]                   # the service's answer as it came, whole (not 12,000 characters)
    assert guide.startswith("GUIDE START") and guide.endswith("GUIDE END") and len(guide) > 40_000
    assert step["result"].startswith(f"Saved the whole result ({len(text):,} characters) to {saved}")
    assert "save_to" not in json.dumps(step["arguments"])                # the service never saw it
    # read without save_to: the model reads the start, the whole answer is in a file it is told about
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "read the guide"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    step = [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"][0]
    kept = Path(step["saved"])
    assert kept.parent == ws / "tool-results" and kept.name.endswith("get-guide.txt") and kept.read_text(encoding="utf-8") == text
    assert step["result"].endswith(f"(the whole result is in {kept})")
    # the rule is in every agent's instructions
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "show system prompt"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    prompt = last_assistant(c, chat["id"])["content"]
    assert "save_to (a file path)" in prompt and "from kimilm import call" in prompt
    c.delete(f"/api/connectors/{conn['id']}")


def test_code_pages_through_a_service_and_only_while_it_runs(engine):
    """2.19.0. Paging through a service was a turn of the model per page. In run_python, kimilm.call reaches the
    agent's own connectors: one program pages through all of it and writes the file. Its token is valid only
    while that program runs, and code never sees the app's own token."""
    c = engine["client"]
    conn, chat, ws = catalogue_chat(c)
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "page through the items into items.csv"}).json()
    done = wait_job(c, job["job_id"], approve="allow")
    assert done["status"] == "done", done
    step = [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"][0]
    assert step["ok"] and "47 items from 5 pages" in step["result"], step["result"]
    rows = (ws / "items.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "id,title" and len(rows) == 48 and rows[-1] == "47,Item 47"
    trail = [e["text"] for e in c.get(f"/api/jobs/{job['job_id']}").json()["trail"]]
    assert len([t for t in trail if t.startswith("From code: Catalogue")]) == 5          # each call is in the trail
    # without a valid token the bridge answers nothing, and the app's own token is not the bridge's
    r = httpx.post(f"http://127.0.0.1:{engine['port']}/api/bridge/call", json={"tool": "list_items"},
                   headers={"X-KimiLM-Bridge": "made-up"}, trust_env=False)
    assert r.status_code == 403 and "only while its run_python call runs" in r.json()["error"]
    r = httpx.post(f"http://127.0.0.1:{engine['port']}/api/bridge/tools", trust_env=False,
                   headers={"X-KimiLM-Bridge": engine["token"]})
    assert r.status_code == 403
    c.delete(f"/api/connectors/{conn['id']}")


def test_a_program_that_hangs_is_ended_with_everything_it_started(engine):
    """2.19.0. A long-running task left the computer full of Python processes: code kept running after its agent
    was stopped, and a program it had started outlived it. Now Stop, the time limit and the end of the task end
    the program and every program it started; the time limit has a ceiling; Activity lists what runs."""
    import psutil
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    ws = Path(c.get(f"/api/chats/{chat['id']}").json()["workspace"])
    pids_file = ws / "hang-pids.txt"

    def started(job_id):
        deadline = time.time() + 30
        while time.time() < deadline:
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] == "waiting":
                for ap in c.get(f"/api/chats/{chat['id']}").json()["pending_approvals"]:
                    c.post(f"/api/approvals/{ap['approval_id']}", json={"allow": True})
            if pids_file.exists() and len(pids_file.read_text().split()) == 2:
                return [int(x) for x in pids_file.read_text().split()]
            time.sleep(0.2)
        raise AssertionError("the code did not start")

    def gone(pids, within=10):
        deadline = time.time() + within
        while time.time() < deadline:
            if not any(psutil.pid_exists(p) and psutil.Process(p).status() != psutil.STATUS_ZOMBIE for p in pids):
                return True
            time.sleep(0.2)
        return False

    # Stop: the program and the one it started end with the task
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "hang in code"}).json()
    pids = started(job["job_id"])
    listed = c.get("/api/procs").json()["procs"]
    mine = [p for p in listed if p["owner"] == job["job_id"]]
    assert len(mine) == 1 and mine[0]["kind"] == "python" and mine[0]["processes"] >= 2, listed   # the script and its child
    if os.name != "nt":
        assert psutil.Process(mine[0]["pid"]).nice() >= 10                            # it runs at low priority
    c.post(f"/api/jobs/{job['job_id']}/cancel")
    assert wait_job(c, job["job_id"])["status"] == "cancelled"
    assert gone(pids) and not [p for p in c.get("/api/procs").json()["procs"] if p["owner"] == job["job_id"]]
    # the time limit the model asks for has a ceiling; at the limit, everything the program started ends too
    pids_file.unlink()
    c.put("/api/settings", json={"code_timeout_max_seconds": 6})
    try:
        job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "hang in code for 99999 seconds"}).json()
        pids = started(job["job_id"])
        assert wait_job(c, job["job_id"], timeout=60, approve="allow")["status"] == "done"
        step = [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"][0]
        assert not step["ok"] and "ran longer than 6 seconds and was stopped, with every program it started" in step["result"]
        assert gone(pids)
    finally:
        c.put("/api/settings", json={"code_timeout_max_seconds": 900})
    # a program in the list can be ended by hand (Activity)
    pids_file.unlink()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "hang in code"}).json()
    pids = started(job["job_id"])
    top = [p for p in c.get("/api/procs").json()["procs"] if p["owner"] == job["job_id"]][0]
    assert c.delete(f"/api/procs/{top['pid']}").json()["owner"] == job["job_id"]
    assert gone(pids)
    assert wait_job(c, job["job_id"], timeout=60, approve="allow")["status"] == "done"
    step = [s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool"][0]
    assert step["result"].startswith("FAILED")                         # the agent is told its code did not finish
    assert c.delete(f"/api/procs/{top['pid']}").status_code == 404


def test_programs_left_by_an_engine_that_went_away_are_ended(tmp_path):
    """2.19.0. The list of running programs is kept on disk: at the next start, what an engine that crashed or was
    killed left running is ended, but only if the program with that number is still the one it started."""
    import subprocess
    import types
    import psutil
    from dva.procs import Procs, end_tree, python_argv
    said = []
    app = types.SimpleNamespace(paths=types.SimpleNamespace(data=tmp_path), log=said.append,
                                db=types.SimpleNamespace(setting=lambda k, d=None: d))
    script = tmp_path / "parent.py"
    script.write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; "
                      "time.sleep(60)'])\ntime.sleep(60)\n")
    left = subprocess.Popen(python_argv(script))
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not psutil.Process(left.pid).children():
            time.sleep(0.1)
        child = psutil.Process(left.pid).children()[0].pid
        (tmp_path / "procs.json").write_text(json.dumps([
            {"pid": left.pid, "started": psutil.Process(left.pid).create_time(), "kind": "python", "label": "parent"},
            {"pid": other.pid, "started": psutil.Process(other.pid).create_time() - 100, "kind": "python"},
            {"pid": 99999999, "started": 1.0, "kind": "python"}]))
        ended = Procs(app).end_leftovers()
        assert len(ended) == 1 and "parent" in ended[0] and left.wait(timeout=10) is not None
        deadline = time.time() + 10
        while time.time() < deadline and psutil.pid_exists(child) and psutil.Process(child).status() != psutil.STATUS_ZOMBIE:
            time.sleep(0.1)
        assert not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE
        assert other.poll() is None and json.loads((tmp_path / "procs.json").read_text()) == []
        assert said and "ended programs left by the engine before" in said[0]
        assert end_tree(99999999) == 0
    finally:
        for p in (left, other):
            if p.poll() is None:
                p.kill()

    async def queue():
        # at most `code_runs_at_once` programs run together; a long one's output is kept start and end
        procs = Procs(types.SimpleNamespace(paths=app.paths, log=said.append,
                                            db=types.SimpleNamespace(setting=lambda k, d=None: 1 if k == "code_runs_at_once" else d)))
        waits = []
        env = {**os.environ}
        argv = [sys.executable, "-c", "import time; time.sleep(0.8); print('x' * 50000)"]
        began = time.monotonic()
        both = await asyncio.gather(*(procs.run(argv, cwd=tmp_path, env=env, timeout=30, limit=1000,
                                                on_line=lambda t: waits.append(t) if t is None else None)
                                      for _ in range(2)))
        assert time.monotonic() - began > 1.5 and waits == [None]           # the second waited for the first
        code, out = both[0]
        assert code == 0 and out.startswith("x" * 400) and "characters of output left out" in out and len(out) < 1200
        with pytest.raises(asyncio.TimeoutError):
            await procs.run([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, env=env, timeout=1)
        assert procs.live == {}
    import asyncio
    asyncio.run(queue())


def test_a_step_names_its_files_exactly_and_recovery_knows_files_not_left(tmp_path):
    """2.19.0. A planner wrote "team/guide.json" for a file of the team folder, and the agent and the check read
    it differently. Now one place says where an expected file belongs; the brief lists exact paths; a step whose
    agent did not leave its files is a failure of its own kind, with advice that is not "do the same again"."""
    import types
    from dva.orchestrator import Orchestra
    from dva.recovery import CLASSES, classify
    team = tmp_path / "team"
    team.mkdir()
    o = types.SimpleNamespace(dir=team)
    where = lambda n: Orchestra.expected_path(o, n)                    # noqa: E731
    assert where("team/guide.json") == team / "guide.json" == where("guide.json") == where("./guide.json")
    assert where("team\\\\inventory.csv".replace("\\\\", os.sep)) == team / "inventory.csv"
    assert where("cycles/a/source.md") == team / "cycles" / "a" / "source.md"
    assert where(str(tmp_path / "elsewhere.txt")) == tmp_path / "elsewhere.txt"
    o.expected_path = where
    has = lambda n: Orchestra.has_output(o, n)                         # noqa: E731
    assert not has("team/guide.json")
    (team / "guide.json").write_text("")
    assert not has("guide.json")                                       # an empty file is not a result
    (team / "guide.json").write_text("{}")
    assert has("team/guide.json") and has("guide.json")
    (team / "deep").mkdir()
    (team / "deep" / "inventory.csv").write_text("id\n1\n")
    assert has("inventory.csv")                                        # found by name, as before
    assert classify(objection="the file /x/team/coverage.json was not saved") == "output"
    assert classify(objection="the summary leaves out the second source") == "quality"
    assert "save_to" in CLASSES["output"]["advice"] and CLASSES["output"]["repeat"] == 0


def test_an_agent_is_told_of_a_missing_file_before_it_finishes(engine):
    """2.19.0. An agent that ended without the file its step must leave was told so only by the check after its
    round, twenty minutes later on a slow computer. Now the app tells it before it finishes, with the exact path,
    and the file lands where the planner meant it ("team/report.txt" is report.txt in the team folder)."""
    c = engine["client"]
    made = c.post("/api/chats", json={"harness": "agent"}).json()
    c.put(f"/api/chats/{made['id']}", json={"auto_approve": True})      # the agent writes without asking
    job = c.post(f"/api/chats/{made['id']}/messages", json={"text": "As a team with a file-leaving plan: keep a report."}).json()
    status, msg = wait_job(c, job["job_id"], timeout=150)["status"], None
    msg = last_assistant(c, made["id"])
    assert status == "done"
    team = team_of(msg)
    t = team["tasks"][0]
    assert t["status"] == "done", t
    report = Path(team["folder"]) / "report.txt"
    assert report.read_text(encoding="utf-8") == "the report" and not (Path(team["folder"]) / "team").exists()
    brief = first_user_text(c, t["chat_id"])
    assert f"Leave these files, at exactly these paths (the app checks them):\n- {report}" in brief
    steps = [s for m in c.get(f"/api/chats/{t['chat_id']}").json()["messages"] if m["role"] == "assistant"
             for s in (m.get("meta") or {}).get("steps") or []]
    assert any(s.get("type") == "note" and "Reminded the agent of 1 missing file" in (s.get("text") or "") for s in steps), steps
    assert [s["arguments"]["path"] for s in steps if s.get("type") == "tool"] == [str(report)]


# ---------------------------------------------------------------------- 2.20.0: fail fast
def fail_fast_run(c, goal, auto_approve=False):
    made = c.post("/api/chats", json={"harness": "agent"}).json()
    if auto_approve:
        c.put(f"/api/chats/{made['id']}", json={"auto_approve": True})
    job = c.post(f"/api/chats/{made['id']}/messages", json={"text": f"As a team with a fail-fast plan: {goal}."}).json()
    status = wait_job(c, job["job_id"], timeout=150)["status"]
    return made["id"], status, team_of(last_assistant(c, made["id"]))


def agent_messages(c, chat_id):
    return [m for m in c.get(f"/api/chats/{chat_id}").json()["messages"] if m["role"] == "assistant"]


def test_fail_fast_rules_in_code():
    """recovery.py (2.20.0): when the app stops an agent without waiting for its model, which class that is, what
    the planner does about it without a model call, and when 'done when' asks only for files."""
    from types import SimpleNamespace
    from dva import recovery as rc
    from dva.runner import LoopGuard
    trip = rc.tripwire
    assert trip("write_file", "Error: The user declined this action.", declined=True)[0] == "permission"
    assert trip("notes__fetch", "Error: Notes needs you to sign in first")[0] == "permission"
    assert trip("generate_image", "Error: The image engine is not installed. Open Images")[0] == "missing"
    assert trip("x__list", "Error: Archive could not be reached, also when tried again: refused", connector=True)[0] == "transient"
    assert trip("web_fetch", "Error: HTTP 503 service unavailable", world=True) is None          # another source may do
    assert trip("web_fetch", "Error: HTTP 503 service unavailable", world=True, times=2)[0] == "approach"
    assert trip("read_file", "Error: No file at /a/b.txt") is None
    assert trip("read_file", "Error: No file at /a/c.txt", times=2)[1].startswith("read_file failed twice")
    assert trip("write_file", "Error: /x is outside the folders this chat may use (a)", refused=1) is None
    assert trip("write_file", "Error: /y is outside the folders this chat may use (a)", refused=2)[0] == "approach"
    assert trip("run_python", "Hint: install it", code=True) is None                            # code gets one fix
    assert trip("run_python", "ValueError: the gate is shut", code=True, times=2)[1].startswith("the code failed twice")
    assert trip("run_python", "There is no tool called 'run_python'", wanted="run_python")[0] == "missing"
    # code that failed is an answer of the tool, not an error: its error line is found
    out = ("FAILED (exit code 1)\nTraceback (most recent call last):\n  File \"run_1.py\", line 2, in <module>\n"
           "ValueError: the gate is shut\n\nThe error is at line 2 of your code:\n>>    2  raise ValueError('x')")
    assert rc.code_error(out) == "ValueError: the gate is shut" and rc.code_error("exit code 0\nfine") is None
    # the planner's rules
    rule = rc.fast_rule
    assert rule({"class": "transient"}) == "retry" and rule({"class": "permission"}) == "ask"
    assert rule({"class": "missing", "need": "tool"}) == "handover" and rule({"need": "information", "class": "missing"}) is None
    assert rule({"class": "capability", "need": "stronger_model"}) == "stronger" and rule({"class": "approach"}) is None
    assert rule({"class": "timeout", "need": "more_time"}) == "more_time" and rule({"class": "output"}) == "output"
    assert rc.declared_class("permission", "") == "permission" and rc.declared_class("other", "") == "approach"
    assert rc.declared_class("other", "ConnectError: connection reset by peer") == "transient"
    # 'done when' that asks only for files: code checks it
    assert rc.files_only("report.txt is saved in the team folder", ["team/report.txt"])
    assert rc.files_only("Both a.csv and b.json exist", ["a.csv", "team/b.json"])
    assert not rc.files_only("report.txt lists every dataset with its title", ["team/report.txt"])
    assert not rc.files_only("a greeting", ["team/report.txt"]) and not rc.files_only("report.txt is saved", [])
    # the guard of an agent of a run: the same error twice stops it, and code gets its one fix first
    turn = SimpleNamespace(limits={}, harness={}, fast=True, db=SimpleNamespace(setting=lambda k, d=None: d))
    g = LoopGuard(turn)
    g.record("run_python", {"code": "1"}, True, "FAILED (exit code 1)\nValueError: the gate is shut")
    assert not g.tripped and not g.should_stop()
    g.record("run_python", {"code": "2"}, True, "FAILED (exit code 1)\nKeyError: 'b'")      # another error: fixing
    assert not g.tripped
    g.record("run_python", {"code": "3"}, True, "FAILED (exit code 1)\nValueError: the gate is shut")
    assert g.tripped["class"] == "approach" and g.should_stop().startswith("the code failed twice")
    g = LoopGuard(turn)
    g.record("web_fetch", {"url": "https://a.example/1"}, False, "Error: HTTP 403 for https://a.example/1", world=True)
    assert not g.tripped
    g.record("web_fetch", {"url": "https://a.example/2"}, False, "Error: HTTP 403 for https://a.example/2", world=True)
    assert g.tripped and g.stop_reason.startswith("web_fetch failed twice")
    g = LoopGuard(turn)
    g.record("write_file", {"path": "a"}, False, "The user declined this action.", declined=True)
    assert g.tripped["class"] == "permission"
    plain = LoopGuard(SimpleNamespace(limits={}, harness={}, db=turn.db))                    # a chat: as before
    plain.record("write_file", {"path": "a"}, False, "The user declined this action.", declined=True)
    assert not plain.tripped and not plain.should_stop()


def test_an_agent_that_fails_the_same_way_twice_is_stopped_at_once(engine):
    """2.20.0. The same error twice stops an agent of a run without another model turn and without a write-up:
    the entry is in the run's register at once, the planner decides in seconds (here: a fresh agent told what
    failed), and the run's card shows how fast that was."""
    c = engine["client"]
    lead, status, team = fail_fast_run(c, "say hello after reading the gate files")
    assert status == "done"
    t = team["tasks"][0]
    assert t["status"] == "done" and t["attempts"] == 2, t
    reg = team["register"]
    assert len(reg) == 1 and reg[0]["by"] == "app" and reg[0]["step"] == "t1" and reg[0]["round"] == 1
    assert reg[0]["reason"].startswith("read_file failed twice with the same error") and reg[0]["rule"] is None
    assert reg[0]["decision"] == "a fresh agent told what failed and to go another way"
    assert reg[0]["decided_in"] < 10
    assert reg[0]["failures"] == [{"tool": "read_file", "error": reg[0]["failures"][0]["error"], "times": 2}]
    m = team["measures"]
    assert m["failures"] == 1 and m["by_app"] == 1 and m["met"] is True and m["rounds"] == 2 and m["early"] == 0
    log = [line["text"] for line in team["log"]]
    assert any(x.startswith("The app stopped t1 at once: read_file failed twice") for x in log), log
    assert any(x.startswith("t1: decided in ") for x in log), log
    # the round that failed (its own chat: the next round was a fresh agent): two calls, then a reply written by
    # code (no write-up by the model)
    chats = [a["id"] for a in c.get(f"/api/chats/{lead}").json()["agents"]]
    assert len(chats) == 2 and t["chat_id"] == chats[-1]
    stopped = next(m for m in agent_messages(c, chats[0]) if (m["meta"] or {}).get("fail_fast"))
    calls = [s for s in stopped["meta"]["steps"] if s["type"] == "tool"]
    assert [s["name"] for s in calls] == ["read_file", "read_file"]
    assert stopped["content"].startswith("The app stopped me at once: read_file failed twice")
    assert stopped["meta"]["stats"]["model_calls"] == 2
    # the fresh agent knew what failed
    brief = [m for m in c.get(f"/api/chats/{t['chat_id']}").json()["messages"] if m["role"] == "user"][-1]["content"]
    assert "An earlier attempt at this task stopped early: read_file failed twice" in brief
    assert "report_failure at once" in brief


def test_code_gets_one_fix_and_then_the_agent_is_stopped(engine):
    c = engine["client"]
    _, status, team = fail_fast_run(c, "say hello with broken code", auto_approve=True)
    assert status == "done"
    entry = team["register"][0]
    assert entry["reason"] == "the code failed twice with the same error (ValueError: the gate is shut)"
    assert team["tasks"][0]["status"] == "done" and team["tasks"][0]["attempts"] == 2


def test_a_tool_the_kit_left_out_is_handed_over_and_files_are_checked_by_code(engine):
    """An agent called a tool its lean kit did not give it: stopped at once and given all its tools. Its 'done when'
    asks only for a file: code finds it and accepts the step without asking the planner's model."""
    c = engine["client"]
    _, status, team = fail_fast_run(c, "say hello through the snake program", auto_approve=True)
    assert status == "done"
    t, entry = team["tasks"][0], team["register"][0]
    assert entry["class"] == "missing" and entry["rule"] == "handover"
    assert entry["decision"].startswith("the same kind of agent with all its tools (run_python was not in its kit)")
    assert t["status"] == "done" and t["attempts"] == 2 and t["kit"] is None      # the second round had every tool
    assert (Path(team["folder"]) / "report.txt").read_text(encoding="utf-8") == "hello from the snake"
    assert any(line["text"] == "Accepted t1: its files are there (round 2)." for line in team["log"]), team["log"]


def test_an_agent_that_needs_you_says_so_and_the_task_asks_at_once(engine):
    """A long-running task. The agent reports at once that it needs a key: its step fails after one round (no
    check, no more rounds), and the task asks you by rule, without a planner call. Your answer reaches the
    planner, which goes another way. The failure counted like any other (recovery_attempts)."""
    c = engine["client"]
    chat_id, status, msg = start_task(c, "Long task: needs a key it lacks.")
    assert status == "done"
    run = c.get(f"/api/chats/{chat_id}").json()["open_run"]
    assert run["pause"]["kind"] == "question", run["pause"]
    assert "could not finish “Fetch”" in run["pause"]["text"] and "an API key for the archive" in run["pause"]["text"]
    team = team_of(msg)
    s1 = team["tasks"][0]
    assert s1["status"] == "failed" and s1["attempts"] == 1 and team["mission"]["moves"] == 1     # no planner call
    entry = team["register"][0]
    assert entry["by"] == "agent" and entry["need"] == "permission" and entry["rule"] == "ask"
    assert entry["decision"] == "it needs you: the task asks you" and entry["decided_in"] < 10 and not entry["early"]
    h = team["mission"]["handoffs"][0]
    assert h["status"] == "failed" and h["flag"]["need"] == "permission"
    reply = agent_messages(c, s1["chat_id"])[-1]
    assert reply["content"].startswith("I stopped and reported it to the planner") and "## Handoff" in reply["content"]
    assert reply["meta"]["stats"]["model_calls"] == 1
    job = c.post(f"/api/chats/{chat_id}/messages", json={"text": "use the public mirror"}).json()
    assert wait_job(c, job["job_id"], timeout=120)["status"] == "done"
    team = team_of(last_assistant(c, chat_id))
    assert [t["name"] for t in team["tasks"]] == ["Fetch", "Mirror"] and team["tasks"][1]["status"] == "done"
    assert team["measures"]["by_agent"] == 1 and team["measures"]["met"] is True



# ---------------------------------------------------------------------- 2.21.0
def _gone(pid, seconds=10):
    import psutil
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.Error:
            return True
        time.sleep(0.1)
    return False


def test_a_program_left_running_by_an_agents_code_ends_with_its_task(tmp_path):
    """2.21.0. After the evaluation one Python process was left: code an agent ran had ended, but a program it had
    started went on, and once its parent was gone nothing knew of it. Now what a program starts is noted while it
    runs; what is still running when it ends stays on the list (Activity) with the same task and ends with it."""
    import asyncio
    from types import SimpleNamespace
    from dva.procs import Procs
    app = SimpleNamespace(paths=SimpleNamespace(data=tmp_path), log=[].append,
                          db=SimpleNamespace(setting=lambda k, d=None: d))
    script = ("import subprocess, sys, time\n"
              "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, "
              "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
              "print(p.pid, flush=True)\ntime.sleep(1.6)\n")

    async def go():
        procs = Procs(app)
        env = {**os.environ}
        code, out = await procs.run([sys.executable, "-c", script], cwd=tmp_path, env=env, timeout=30, owner="job-1",
                                    label="python run_1.py")
        pid = int(out.split()[0])
        assert code == 0 and not _gone(pid, 0.2)
        # on Windows a virtual environment's python.exe is a launcher that starts the real interpreter, so the
        # program left running can be two processes; each is listed with the task, and all end with it
        rows = procs.listing()
        assert pid in [r["pid"] for r in rows] and len(rows) <= 2, rows
        assert all(r["kind"] == "left running" and r["owner"] == "job-1" for r in rows)
        assert procs.live[pid]["label"] == "left running by python run_1.py"
        assert pid in [r["pid"] for r in json.loads((tmp_path / "procs.json").read_text())]   # ended after a restart too
        assert procs.end_owner("job-2") == 0 and procs.end_owner("job-1") >= 1 and _gone(pid)
        code, out = await procs.run([sys.executable, "-c", script], cwd=tmp_path, env=env, timeout=30)   # no task
        assert _gone(int(out.split()[0])) and procs.live == {}
        code, out = await procs.run([sys.executable, "-c", "print('alone')"], cwd=tmp_path, env=env, timeout=30,
                                    owner="job-3")
        assert out.strip() == "alone" and procs.live == {}
    asyncio.run(go())


def test_an_agent_that_names_its_own_folder_means_that_folder(tmp_path):
    """2.21.0. In the evaluation's folder 'fizz' the model wrote 'fizz/fizz.py': the file went to fizz/fizz/, and its
    code, run in fizz/, could not find it. A relative path that starts with the folder's own name means the folder,
    unless a sub-folder of that name really is there."""
    from dva.tools import ToolContext
    root = tmp_path / "fizz"
    root.mkdir()
    ctx = ToolContext(app=None, chat={}, project=None, workspace=root, folders=[str(root)], readonly=[],
                      emit=lambda *a, **k: None, approve=None)
    assert ctx.resolve("fizz/fizz.py", write=True) == (root / "fizz.py").resolve()
    assert ctx.resolve("FIZZ/out.txt") == (root / "out.txt").resolve()
    assert ctx.resolve("fizz") == root.resolve() and ctx.resolve("fizz.py") == (root / "fizz.py").resolve()
    assert ctx.resolve("other/fizz.py") == (root / "other" / "fizz.py").resolve()
    (root / "fizz").mkdir()                       # a real sub-folder of that name is used as it is
    assert ctx.resolve("fizz/fizz.py") == (root / "fizz" / "fizz.py").resolve()


def test_an_evaluation_can_be_stopped_continued_and_deleted(engine):
    c = engine["client"]
    started = c.post("/api/evals/run", json={"only": ["calc", "summary"]}).json()
    for _ in range(100):                                 # stop it once its first task is under way
        cur = c.get("/api/evals").json()["current"]
        if cur and cur.get("chat_id"):
            break
        time.sleep(0.1)
    assert c.post("/api/evals/pause").status_code == 200
    assert c.get("/api/evals").json()["current"]["paused"] is True
    assert c.post("/api/evals/resume").status_code == 200
    c.post("/api/evals/stop")
    wait_job(c, started["job_id"], timeout=60)
    run = next(r for r in c.get("/api/evals").json()["runs"] if r["id"] == started["id"])
    assert run["status"] == "stopped" and run["can_continue"], run
    jobs = c.get("/api/jobs").json()["jobs"]
    assert next(j for j in jobs if j["id"] == started["job_id"])["can_continue"]      # also from Activity
    again = c.post(f"/api/evals/{started['id']}/continue").json()
    assert again["id"] == started["id"] and again["total"] == 2
    assert wait_job(c, again["job_id"], timeout=120)["status"] == "done"
    run = next(r for r in c.get("/api/evals").json()["runs"] if r["id"] == started["id"])
    assert run["status"] == "done" and sorted(r["id"] for r in run["results"]) == ["calc", "summary"]
    assert not run["can_continue"]
    assert c.post(f"/api/evals/{started['id']}/continue").status_code == 400
    assert c.delete(f"/api/evals/{started['id']}").status_code == 200
    assert started["id"] not in {r["id"] for r in c.get("/api/evals").json()["runs"]}
    for r in run["results"]:
        assert c.get(f"/api/chats/{r['chat_id']}").status_code == 404                  # its chats went with it
    assert c.delete(f"/api/jobs/{again['job_id']}").status_code == 200                   # off the Activity list
    assert again["job_id"] not in {j["id"] for j in c.get("/api/jobs").json()["jobs"]}


def test_a_chat_shows_what_it_runs_with_and_a_busy_model_can_be_unloaded(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"title": "settings"}).json()
    eff = c.get(f"/api/chats/{chat['id']}").json()["effective"]
    assert eff and eff["harness"] and eff["max_steps"] and eff["max_steps_by"] in ("harness", "default")
    assert eff["max_reply_tokens"] and "ctx" in eff
    upd = c.put(f"/api/chats/{chat['id']}", json={"settings": {"ctx": 12288, "max_steps": 7}}).json()
    assert upd["settings"]["ctx"] == 12288
    assert upd["effective"]["ctx"] == 12288 and upd["effective"]["ctx_by"] == "you"
    assert upd["effective"]["max_steps"] == 7 and upd["effective"]["max_steps_by"] == "you"
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "Say hello."}).json()
    wait_job(c, job["job_id"] if "job_id" in job else job["id"], timeout=60)
    after = c.get(f"/api/chats/{chat['id']}").json()["effective"]
    assert after["model"] and after["ctx"] == 12288 and after["ctx_by"] == "you"
    loaded = c.get("/api/models").json().get("loaded") or []
    assert all("held_by" in l for l in loaded)
    for l in loaded:                                     # force works when nothing uses it, too
        assert c.post("/api/runtime/unload", json={"model_id": l["model_id"], "force": True}).status_code == 200


def test_performance_readings_and_usage_statistics(engine):
    """2.27.0: the computer's readings (history, the live stream, programs, switching off) and every model call
    counted (by model, purpose and chat), with a CSV of the calls."""
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "chat"}).json()
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "hello again"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    time.sleep(2.5)
    h = c.get("/api/telemetry?seconds=120").json()
    assert h["running"] and len(h["samples"]) >= 2 and h["info"]["threads"] >= 1
    assert h["latest"]["mem_total_mb"] > 0 and "tok_write" in h["samples"][-1]
    assert any(m["text"].startswith(("Started", "Finished", "Loaded", "Loading")) for m in h["marks"])
    with httpx.Client(base_url=f"http://127.0.0.1:{engine['port']}", timeout=10, trust_env=False) as raw:
        with raw.stream("GET", f"/api/telemetry/stream?token={engine['token']}") as r:
            assert r.status_code == 200
            for line in r.iter_lines():
                if line.startswith("data:"):
                    sample = json.loads(line[5:])
                    assert "cpu" in sample and "new_marks" in sample
                    break
    top = c.get("/api/telemetry/processes").json()
    assert top["memory"]
    assert "cpu" in c.get("/api/telemetry/summary?seconds=60").json()
    u = c.get("/api/usage?days=7").json()
    assert u["totals"]["calls"] >= 1 and u["totals"]["written"] > 0
    assert any(p["key"] == "chat" for p in u["by_purpose"])
    assert u["top_chats"] and all(t["tokens"] > 0 for t in u["top_chats"])
    assert len(u["daily"]) == 7 and len(u["heat"]) == 7
    csv = c.get("/api/usage/export?days=7")
    assert csv.status_code == 200 and csv.text.startswith("when,model_name") and chat["id"] in csv.text
    c.put("/api/settings", json={"telemetry_enabled": False})
    assert c.get("/api/telemetry?seconds=60").json()["running"] is False
    c.put("/api/settings", json={"telemetry_enabled": True})
    assert c.get("/api/telemetry?seconds=60").json()["running"] is True
