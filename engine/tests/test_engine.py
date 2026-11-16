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
    port = free_port()
    token = "test-token"
    env = {**os.environ, "DVA_TEST_CPU": "6x12", "DVA_TOKEN": token, "DVA_LLAMA_SERVER": str(wrapper), "DVA_SECRETS": "file",
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
    yield {"client": client, "tmp": tmp, "port": port, "token": token}
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
