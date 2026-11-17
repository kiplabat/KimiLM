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
    port = free_port()
    token = "test-token"
    env = {**os.environ, "DVA_TEST_CPU": "6x12", "DVA_TOKEN": token, "DVA_LLAMA_SERVER": str(wrapper), "DVA_SECRETS": "file",
           "DVA_SD_CLI": str(painter), "DVA_SOUND_WORKER": str(TESTS / "fake_sound_worker.py"),
           "DVA_SOUND_LIBRARY": "2.0.1", "DVA_SOUND_LOG": str(tmp / "sound-jobs.log"),
           "PYTHONPATH": str(ENGINE), "OLLAMA_MODELS": str(tmp / "ollama"), "HF_HOME": str(tmp / "hf")}
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
    for i in range(3):
        g.record("read_file", {"path": str(i)}, False, "Error: x")
    assert "nothing new" in g.should_stop()


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
    from dva.tools import python_exec
    from dva.tools import ToolContext

    class DB:
        def setting(self, key, default=None):
            return default

    class App:
        db = DB()
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
    assert team["mode"] == "sequence" and list(tasks) == ["t1", "t2", "t3"]
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
    from dva.orchestrator import RunFit, classify, same_objection
    assert classify("TurnStopped: The model stopped responding twice in a row") == "timeout"
    assert classify(None, timed_out=True) == "timeout"
    assert classify("RuntimeError_: Not enough memory to load this model with these settings.") == "memory"
    assert classify("LLMContextFull: the prompt does not fit the model's context: 9000 > 8192") == "context"
    assert classify("ToolError: no such file") == "error" and classify("") is None
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
    made = c.post("/api/missions", json={"text": "Long task with a note-step first.", "setup": {"allow": ["browser"]}}).json()
    status, asked = approvals_seen(c, made["job_id"], made["chat_id"])
    assert status == "done" and asked == ["write_file"]        # not agreed: it asked (and the user allowed it)


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
    assert h == {"done": "read a.csv, 12 rows", "found": "nothing new", "next": "chart the rows\nand save it"}
    h = parse_handoff("**Handoff**\n- **Done:** fixed the test\n- **Next:** nothing: the task is complete")
    assert h["done"] == "fixed the test" and h["next"].startswith("nothing") and h["found"] == ""
    assert parse_handoff("Just an answer without the block.")["done"] == "Just an answer without the block."
    assert clean_limits({"hours": "2.5", "steps": 10**9, "tokens": -5, "cost": "x"}) == \
        {"hours": 2.5, "steps": 400, "step_minutes": 45, "tokens": 0, "cost": 0.0}
    assert "browser" in lean_kit("Log in to the portal and click the Export button")
    assert "browser" not in lean_kit("Read notes.txt and list the names")


def test_the_browser_tools_click_type_and_download_in_a_real_browser(tmp_path):
    """With a real Chrome and the bundled browser helper (skipped where there is none): open a page, type into
    a field, click a button, follow a download, take a screenshot — each answer shows the page afterwards."""
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
    (site / "index.html").write_text(
        "<!doctype html><title>Test shop</title><h1>Test shop</h1><a href='prices.html'>Price list</a>"
        "<form onsubmit=\"document.getElementById('out').textContent='Hello, '+document.getElementById('n').value;"
        "return false\"><label for='n'>Your name</label><input id='n'><button>Greet</button></form><p id='out'></p>"
        "<a href='/files/prices.csv'>Download prices</a>"
        "<select aria-label='Colour'><option>Red</option><option>Blue</option></select>")
    (site / "prices.html").write_text("<!doctype html><title>Price list</title><h1>Price list</h1><p>Tea 4.50</p>")
    (site / "files" / "prices.csv").write_text("item,price\ntea,4.50\n")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            if self.path.endswith(".csv"):
                self.send_header("Content-Disposition", "attachment; filename=prices.csv")
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

    async def scenario():
        from dva.app_state import App
        from dva.tools import ToolContext, ToolError, browser
        app = App(str(tmp_path / "data"))
        app.db.set_setting("browser_channel", "chrome")
        app.db.set_setting("browser_headless", True)
        events = []
        ctx = ToolContext(app=app, chat={"id": "chat_x"}, project=None, workspace=tmp_path / "work", folders=[],
                          readonly=[], emit=lambda kind, **d: events.append((kind, d)), approve=None)
        (tmp_path / "work").mkdir()
        try:
            page = await browser.browser_open(ctx, f"http://127.0.0.1:{port}/index.html")
            assert "Page: Test shop" in page and 'textbox "Your name"' in page and 'button "Greet"' in page
            ref = lambda label: re.search(re.escape(label) + r".*?\[ref=(\w+)\]", page).group(1)   # noqa: E731
            typed = await browser.browser_type(ctx, ref('textbox "Your name"'), "Labatt", submit=True)
            assert "Hello, Labatt" in typed
            chosen = await browser.browser_select(ctx, ref('combobox "Colour"'), "Blue")
            assert 'option "Blue" [selected]' in chosen
            got = await browser.browser_click(ctx, ref('link "Download prices"'), "Download prices")
            saved = tmp_path / "work" / "downloads" / "prices.csv"
            assert "Downloaded:" in got and saved.read_text().startswith("item,price")
            assert ("file_changed", {"path": str(saved), "action": "downloaded"}) in events
            other = await browser.browser_click(ctx, ref('link "Price list"'), "Price list")
            assert "Page: Price list" in other and "Tea 4.50" in other
            assert "Tea 4.50" in await browser.browser_look(ctx, "Tea")
            shot = await browser.browser_screenshot(ctx)
            files = list((tmp_path / "work" / "screenshots").glob("*.png"))
            assert len(files) == 1 and files[0].stat().st_size > 1000 and str(files[0]) in shot
            with pytest.raises(ToolError, match="browser_look"):
                await browser.browser_click(ctx, "e999")          # a reference from an older page
            with pytest.raises(ToolError):
                await browser.browser_open(ctx, "file:///etc/passwd")
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
    c.put("/api/settings", json={"max_native_tools": 36})


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
        threading.Thread(target=lambda: httpx.get(url, follow_redirects=True, timeout=20, trust_env=False), daemon=True).start()
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
    assert (first["seed"], first["count"], first["steps"], first["cfg"], first["negative"]) == ("7", "2", "6", "5.5", "people")
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
    redo = wait_picture(c, c.post("/api/images/generate", json={"prompt": "make the kite blue", "image": source,
                                                                "strength": 0.4}).json()["id"])
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
    # a picture outside the chat's folders is not touched
    job = c.post(f"/api/chats/{chat['id']}/messages",
                 json={"text": f"repaint {tmp / 'checkpoints' / 'tiny-sd.safetensors'} so that it is blue"}).json()
    wait_job(c, job["job_id"])
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert not step["ok"] and "outside the folders" in step["result"] and "media" not in step


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
    db = types.SimpleNamespace(setting=lambda k, d=None: settings.get(k, d), set_setting=settings.__setitem__)
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
    rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
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
    assert Path(res["file"]).read_text().strip() == res["text"] and "00:00:01,500 --> 00:00:02,400" in Path(res["subtitles"]).read_text()
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
    assert eng["url"].startswith("https://github.com/kiplabat/KimiLM/releases/download/music-engine-ed53caf/acestep-")
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
    script = Path(p["script"]).read_text()
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
