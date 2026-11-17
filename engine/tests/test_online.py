"""Online models (2.3.0) end to end: a real engine process, the fake llama-server for local models and
fake_cloud_server.py for OpenAI, Anthropic and Gemini.

    cd engine && python -m pytest -q tests/test_online.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from test_engine import ENGINE, TESTS, free_port, last_assistant, tiny_gguf, wait_job, wait_picture


@pytest.fixture(scope="module")
def env():
    tmp = Path(tempfile.mkdtemp(prefix="dva-online-"))
    cloud_port = free_port()
    cloud = subprocess.Popen([sys.executable, str(TESTS / "fake_cloud_server.py"), "--port", str(cloud_port)],
                             stdout=(tmp / "cloud.log").open("w"), stderr=subprocess.STDOUT)
    wrapper = tmp / ("fake-llama-server.cmd" if os.name == "nt" else "fake-llama-server")
    if os.name == "nt":
        wrapper.write_text(f'@"{sys.executable}" "{TESTS / "fake_llama_server.py"}" %*\n')
    else:
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{TESTS / "fake_llama_server.py"}" "$@"\n')
        wrapper.chmod(0o755)
    port, token = free_port(), "test-token"
    env = {**os.environ, "DVA_TOKEN": token, "DVA_LLAMA_SERVER": str(wrapper), "DVA_SECRETS": "file",
           "PYTHONPATH": str(ENGINE), "OLLAMA_MODELS": str(tmp / "ollama"), "HF_HOME": str(tmp / "hf"),
           "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    log = tmp / "engine-output.log"
    proc = subprocess.Popen([sys.executable, "-m", "dva", "serve", "--port", str(port), "--data-dir", str(tmp / "data")],
                            cwd=str(ENGINE), env=env, stdout=log.open("w"), stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        if "DVA_READY" in log.read_text() or proc.poll() is not None:
            break
        time.sleep(0.1)
    assert "DVA_READY" in log.read_text(), log.read_text()[-3000:]
    for _ in range(50):
        try:
            if httpx.get(f"http://127.0.0.1:{cloud_port}/_requests", trust_env=False).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=60,
                     trust_env=False)
    base = f"http://127.0.0.1:{cloud_port}"
    # no internet in tests: a price list so costs are known
    c.put("/api/settings", json={"cloud_price_list": {"at": time.time(), "rows": {
        "openai/gpt-test-pro": {"in": 2.0, "out": 10.0}, "openai/gpt-test-mini": {"in": 0.2, "out": 0.8},
        "anthropic/claude-test-opus": {"in": 5.0, "out": 25.0}, "google/gemini-test-pro": {"in": 1.25, "out": 10.0}}},
        "learn_enabled": False})
    tiny_gguf(tmp / "local-worker-8B-Q4_K_M.gguf", name="local-worker")
    local = c.post("/api/models/import", json={"path": str(tmp / "local-worker-8B-Q4_K_M.gguf")}).json()
    folder = tmp / "files"
    folder.mkdir()
    (folder / "notes.txt").write_text("private notes\n")
    yield {"c": c, "tmp": tmp, "base": base, "local": local["id"], "folder": str(folder), "cloud": cloud_port}
    try:
        c.post("/api/shutdown")
    except httpx.HTTPError:
        pass
    proc.wait(10)
    cloud.terminate()


def cloud_requests(e, provider):
    return [r for r in httpx.get(f"{e['base']}/_requests", trust_env=False).json() if r["provider"] == provider]


def chat_with(c, model, folder=None, harness="agent"):
    return c.post("/api/chats", json={"model": model, "harness": harness, "folders": [folder] if folder else []}).json()


def test_providers_keys_and_suggested_models(env):
    c, base = env["c"], env["base"]
    listed = {p["id"]: p for p in c.get("/api/online").json()["providers"]}
    assert {"openai", "anthropic", "google", "openrouter", "moonshot", "deepseek", "mistral", "groq", "xai"} <= set(listed)
    assert not listed["openai"]["ready"]
    c.put("/api/online/providers/openai", json={"base": f"{base}/openai/v1"})
    c.put("/api/online/providers/anthropic", json={"base": f"{base}/anthropic/v1"})
    c.put("/api/online/providers/google", json={"base": f"{base}/gemini"})
    bad = c.put("/api/online/providers/openai/key", json={"key": "wrong"})
    assert bad.status_code == 409 and "refused" in bad.json()["error"]
    ok = c.put("/api/online/providers/openai/key", json={"key": "sk-test"}).json()
    assert ok["ok"] and ok["models"] == 2 and "gpt-test-pro" in ok["added"]        # embeddings filtered out
    assert c.put("/api/online/providers/anthropic/key", json={"key": "sk-ant-test"}).json()["added"]
    assert c.put("/api/online/providers/google/key", json={"key": "gm-test"}).json()["added"]
    models = {m["id"]: m for m in c.get("/api/online").json()["models"]}
    opus = models["cloud:anthropic:claude-test-opus"]
    assert opus["role"] == "strategist" and opus["caps"]["thinking"]["adaptive"] and opus["caps"]["vision"]
    assert models["cloud:openai:gpt-test-pro"]["price"]["in"] == 2.0                 # from the price list
    boot = c.get("/api/bootstrap").json()
    assert boot["online"]["ready"] and any(m["id"] == "cloud:google:gemini-test-pro" for m in boot["cloud_models"])
    listing = c.get("/api/online/providers/anthropic/models", params={"q": "haiku"}).json()
    assert [m["remote_id"] for m in listing["models"]] == ["claude-test-haiku"] and listing["models"][0]["added"], "added as the fast pick"


@pytest.mark.parametrize("model, provider, expect", [
    ("cloud:openai:gpt-test-pro", "openai", "OpenAI done: ok"),
    ("cloud:anthropic:claude-test-opus", "anthropic", "Claude done: ok"),
    ("cloud:google:gemini-test-pro", "gemini", "Gemini done: ok"),
])
def test_tool_loop_keeps_provider_state_and_asks_before_sending_files(env, model, provider, expect):
    c = env["c"]
    chat = chat_with(c, model, env["folder"])
    c.put(f"/api/chats/{chat['id']}", json={"thinking": True})
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "please list files"}).json()
    asked = None
    for _ in range(100):                      # the turn stops and asks before the file listing goes online
        pending = c.get(f"/api/chats/{chat['id']}").json()["pending_approvals"]
        if pending:
            asked = pending[0]
            break
        time.sleep(0.1)
    assert asked and asked["tool"] == "send_to_online_model", asked
    assert "files and folders" in asked["description"]
    assert "notes.txt" not in json.dumps(cloud_requests(env, provider)[-1]["body"])
    assert wait_job(c, job["job_id"], approve="allow")["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert msg["content"] == expect, msg
    steps = msg["meta"]["steps"]
    assert any(s["type"] == "tool" and s["name"] == "list_files" and "notes.txt" in s["result"] for s in steps)
    assert msg["meta"]["online"] and msg["meta"]["stats"]["cost"] > 0
    second = cloud_requests(env, provider)[-1]["body"]
    assert "notes.txt" in json.dumps(second), "the file listing reached the model only after approval"


def test_deny_keeps_files_local(env):
    c = env["c"]
    chat = chat_with(c, "cloud:openai:gpt-test-pro", env["folder"])
    before = len(cloud_requests(env, "openai"))
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "please list files"}).json()
    assert wait_job(c, job["job_id"], approve="deny")["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert any("Continuing with" in s.get("text", "") for s in msg["meta"]["steps"] if s["type"] == "note")
    assert msg["meta"]["model"] == env["local"] and not msg["meta"]["online"]
    sent = cloud_requests(env, "openai")[before:]
    assert len(sent) == 1 and "notes.txt" not in json.dumps(sent[0]["body"]), "nothing private went online"


def test_auto_routes_strategy_online_and_routine_locally(env):
    c = env["c"]
    chat = chat_with(c, "auto", harness="chat")
    job = c.post(f"/api/chats/{chat['id']}/messages", json={
        "text": "Plan our strategy: compare the options, weigh the trade-offs and recommend one."}).json()
    assert wait_job(c, job["job_id"], approve="allow")["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert msg["meta"]["online"] and msg["meta"]["auto"] and "leads" in msg["meta"]["steps"][0]["text"]
    assert msg["content"].startswith("Hello from")
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "rename these files to lowercase"}).json()
    assert wait_job(c, job["job_id"], approve="allow")["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert msg["meta"]["model"] == env["local"] and "routine" in msg["meta"]["steps"][0]["text"]


def test_lead_and_local_workers(env):
    c = env["c"]
    chat = chat_with(c, "auto", harness="lead-workers")
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "Write two short notes about solar power"}).json()
    assert wait_job(c, job["job_id"], timeout=120, approve="allow")["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert msg["meta"]["online"], "the lead is the strongest model"
    team = [s for s in msg["meta"]["steps"] if s["type"] == "orchestra"][0]
    assert team["tasks"] and all(t["model"] == env["local"] for t in team["tasks"]), team   # workers run locally
    assert all(t["status"] == "done" for t in team["tasks"])
    assert any(line["text"].startswith("Accepted") for line in team["log"])                  # the lead checked them


def test_online_failure_falls_back_to_local_under_auto(env):
    c = env["c"]
    chat = chat_with(c, "auto", harness="chat")
    job = c.post(f"/api/chats/{chat['id']}/messages", json={
        "text": "fail online: analyse the strategy and recommend a plan"}).json()
    assert wait_job(c, job["job_id"], timeout=90)["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert msg["meta"]["model"] == env["local"]
    assert any("online model failed" in s.get("text", "") for s in msg["meta"]["steps"] if s["type"] == "note")


def test_usage_and_budget(env):
    c = env["c"]
    usage = c.get("/api/online/usage").json()
    assert usage["spent"] > 0 and usage["by_model"] and usage["budget"] == 10.0
    c.put("/api/online/settings", json={"cloud_budget_monthly": 0.000001})
    chat = chat_with(c, "auto", harness="chat")
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "Plan a strategy and compare the options"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    msg = last_assistant(c, chat["id"])
    assert msg["meta"]["model"] == env["local"] and "budget" in msg["meta"]["steps"][0]["text"]
    chat = chat_with(c, "cloud:openai:gpt-test-pro", harness="chat")
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "hello"}).json()
    wait_job(c, job["job_id"])
    msg = last_assistant(c, chat["id"])
    assert "budget" in (msg["meta"].get("error") or ""), msg
    c.put("/api/online/settings", json={"cloud_budget_monthly": 10})


def test_local_only_privacy_mode(env):
    c = env["c"]
    c.put("/api/online/settings", json={"cloud_privacy": "local_unless_on"})
    chat = chat_with(c, "auto", harness="chat")
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "Plan a strategy and compare the options"}).json()
    assert wait_job(c, job["job_id"])["status"] == "done"
    assert last_assistant(c, chat["id"])["meta"]["model"] == env["local"]
    c.put(f"/api/chats/{chat['id']}/online", json={"allowed": True})
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "Plan a strategy and compare the options"}).json()
    assert wait_job(c, job["job_id"], approve="allow")["status"] == "done"
    assert last_assistant(c, chat["id"])["meta"]["online"]
    c.put("/api/online/settings", json={"cloud_privacy": "ask"})


def test_online_pictures_with_openai_and_google_and_not_with_claude(env):
    c, tmp = env["c"], env["tmp"]
    from PIL import Image
    state = c.get("/api/images").json()
    assert {p["id"]: p["ready"] for p in state["online"]} == {"openai": True, "google": True}
    assert not state["engines"]["installed"] and not state["models"]
    # Claude reads pictures and does not make them: said plainly, not offered as an image model
    for call in (c.get("/api/images/online/anthropic/models"),
                 c.post("/api/images/models", json={"kind": "online", "provider": "anthropic", "remote_id": "claude-test-opus"})):
        assert call.status_code == 400 and "Claude reads pictures but does not make them" in call.json()["error"]
    # the image models are read from each provider's own list (their names change often)
    assert c.get("/api/images/online/openai/models").json() == {"models": ["gpt-image-test"], "from": "provider"}
    assert c.get("/api/images/online/google/models").json() == {"models": ["gemini-test-flash-image"], "from": "provider"}
    assert "gpt-image-test" not in {m["remote_id"] for m in c.get("/api/online").json()["models"]}     # not a chat model
    gpt = c.post("/api/images/models", json={"kind": "online", "provider": "openai", "remote_id": "gpt-image-test"}).json()
    gem = c.post("/api/images/models", json={"kind": "online", "provider": "google",
                                             "remote_id": "models/gemini-test-flash-image"}).json()
    assert gpt["ready"] and gpt["default"] and gem["remote_id"] == "gemini-test-flash-image" and gem["ready"]
    before = c.get("/api/online/usage").json()["spent"]

    made = wait_picture(c, c.post("/api/images/generate", json={"prompt": "a lighthouse at dusk", "count": 2,
                                                                "width": 1248, "height": 832}).json()["id"])
    assert made["state"] == "done" and len(made["images"]) == 2, made
    with Image.open(made["images"][0]["path"]) as img:
        assert img.size == (64, 48)
    sent = [r for r in cloud_requests(env, "openai-images")][-1]
    assert sent["body"] == {"model": "gpt-image-test", "prompt": "a lighthouse at dusk", "n": 2, "size": "1536x1024"}
    assert sent["headers"]["authorization"] == "Bearer sk-test"
    usage = c.get("/api/online/usage").json()
    assert round(usage["spent"] - before, 4) == 0.08                           # counted against the monthly budget
    assert any(p["purpose"] == "image" and p["calls"] == 1 for p in usage["by_purpose"])
    assert made["images"][0]["params"]["cost"] == 0.08 and made["images"][0]["source"] == "online"
    # changing a picture sends the picture itself (OpenAI: a form with the file; Google: inside the request)
    source = made["images"][0]["path"]
    wait_picture(c, c.post("/api/images/generate", json={"prompt": "add a boat", "image": source}).json()["id"])
    sent = cloud_requests(env, "openai-images")[-1]
    assert sent["edit"] and sent["body"]["images"] == [Path(source).name] and sent["body"]["bytes"][0] == Path(source).stat().st_size
    assert sent["body"]["prompt"] == "add a boat" and sent["body"]["model"] == "gpt-image-test"
    g = wait_picture(c, c.post("/api/images/generate", json={"prompt": "a harbour", "model": gem["id"], "width": 768,
                                                             "height": 1360}).json()["id"])
    assert g["state"] == "done" and g["images"][0]["model_name"] == "Google · gemini-test-flash-image", g
    sent = cloud_requests(env, "gemini-images")[-1]
    assert sent["model"] == "gemini-test-flash-image" and sent["headers"]["x-goog-api-key"] == "gm-test"
    assert sent["body"]["generationConfig"] == {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "9:16"}}
    assert sent["body"]["contents"][0]["parts"] == [{"text": "a harbour"}]
    wait_picture(c, c.post("/api/images/generate", json={"prompt": "add fog", "model": gem["id"], "image": source}).json()["id"])
    part = cloud_requests(env, "gemini-images")[-1]["body"]["contents"][0]["parts"][1]["inlineData"]
    assert part["mimeType"] == "image/png" and len(part["data"]) > 50
    # a provider's refusal is passed on in its own words; an exhausted budget stops online pictures
    no = wait_picture(c, c.post("/api/images/generate", json={"prompt": "a forbidden picture"}).json()["id"])
    assert no["state"] == "failed" and "rejected by the safety system" in no["error"]
    c.put("/api/online/settings", json={"cloud_budget_monthly": 0.000001})
    broke = wait_picture(c, c.post("/api/images/generate", json={"prompt": "a lighthouse"}).json()["id"])
    assert broke["state"] == "failed" and "budget" in broke["error"]
    c.put("/api/online/settings", json={"cloud_budget_monthly": 10})

    # an agent on a local model asks before a prompt goes to an online image model; declined, nothing is sent
    count = len(cloud_requests(env, "openai-images"))
    chat = chat_with(c, env["local"])
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "paint a picture of a red kite"}).json()
    assert wait_job(c, job["job_id"], approve="deny")["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["declined"] and "media" not in step and len(cloud_requests(env, "openai-images")) == count
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "paint a picture of a red kite"}).json()
    assert wait_job(c, job["job_id"], approve="allow")["status"] == "done"
    step = next(s for s in last_assistant(c, chat["id"])["meta"]["steps"] if s["type"] == "tool")
    assert step["ok"] and step["media"][0]["source"] == "online" and "about US$0.04" in step["result"]
    assert cloud_requests(env, "openai-images")[-1]["body"]["prompt"] == "a red kite"
    for m in (gpt, gem):
        c.delete(f"/api/images/models/{m['id']}")


# ------------------------------------------------------------------------------------------- adapters, offline
def _server(api, provider="x", quirks=None, caps=None):
    sys.path.insert(0, str(ENGINE))
    from dva import cloud

    class P:
        def headers(self, pid):
            return {}

    class C:
        fixes = {}

        def sem(self, p):
            import asyncio
            return asyncio.Semaphore(1)
    row = {"id": f"cloud:{provider}:m", "provider": provider, "remote_id": "m", "caps": caps or {}, "settings": {}}
    return cloud.CloudServer(row, {"api": api, "base": "http://x", "name": "X", "quirks": quirks or []}, P(), C())


def test_anthropic_conversion_merges_tool_results_and_images():
    s = _server("anthropic")
    system, msgs = s.adapter.convert([
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": [{"type": "text", "text": "look"},
                                     {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            {"id": "t2", "type": "function", "function": {"name": "b", "arguments": "{\"x\": 1}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "one"},
        {"role": "tool", "tool_call_id": "t2", "content": "two"},
        {"role": "user", "content": "go on"}])
    assert system == "Be brief."
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"][1] == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}
    assert [b["type"] for b in msgs[2]["content"]] == ["tool_result", "tool_result", "text"]
    assert msgs[1]["content"][1]["input"] == {"x": 1}


def test_chat_quirks_gemini_placeholder_and_echoed_reasoning():
    g = _server("chat", "google", ["gemini_signatures"])
    out = g.adapter.convert([{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "a", "arguments": "{}"}}]}], True)
    assert out[0]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator"
    assert out[0]["content"] == ""
    d = _server("chat", "deepseek", ["echo_reasoning"])
    out = d.adapter.convert([{"role": "assistant", "content": "x", "_native": {"reasoning": "because"}}], True)
    assert out[0]["reasoning_content"] == "because" and "_native" not in out[0]


def test_router_complexity():
    sys.path.insert(0, str(ENGINE))
    from dva.router import complexity
    assert complexity("Plan the strategy and compare the trade-offs of three options") >= 0.5
    assert complexity("rename every file in the folder to lowercase") < 0.5
    assert complexity("hi") < 0.5
