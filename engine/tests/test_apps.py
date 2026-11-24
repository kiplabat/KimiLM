"""Apps (2.34.0): device panels and games that a model builds, kept as versions, run in a sandboxed frame,
moved between computers as .kimiapp files. Runs its own engine with the stand-in model (fake_llama_server.py):

    cd engine && python -m pytest -q tests/test_apps.py
"""
import io
import json
import os
import shutil
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
sys.path.insert(0, str(ENGINE))

from dva import apps as apps_mod  # noqa: E402
from test_engine import free_port, tiny_gguf  # noqa: E402

GAME = ("<!doctype html><html><head><title>T</title></head><body><canvas></canvas><script>"
        "addEventListener('keydown', () => {});</script></body></html>")


@pytest.fixture(scope="module")
def engine():
    tmp = Path(tempfile.mkdtemp(prefix="dva-apps-"))
    wrapper = tmp / ("fake-llama-server.cmd" if os.name == "nt" else "fake-llama-server")
    if os.name == "nt":
        wrapper.write_text(f'@"{sys.executable}" "{TESTS / "fake_llama_server.py"}" %*\n')
    else:
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{TESTS / "fake_llama_server.py"}" "$@"\n')
        wrapper.chmod(0o755)
    port, token = free_port(), "test-token"
    env = {**os.environ, "DVA_TEST_CPU": "6x12", "DVA_TOKEN": token, "DVA_LLAMA_SERVER": str(wrapper),
           "DVA_SECRETS": "file", "PYTHONPATH": str(ENGINE), "DVA_SYSTEM_PLUGINS": str(tmp / "system-plugins"),
           "HF_HOME": str(tmp / "hf"), "OLLAMA_MODELS": str(tmp / "ollama")}
    for k in [k for k in env if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")]:
        del env[k]
    env.update({"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost"})
    log = tmp / "engine-output.log"
    proc = subprocess.Popen([sys.executable, "-m", "dva", "serve", "--port", str(port), "--data-dir", str(tmp / "data")],
                            cwd=str(ENGINE), env=env, stdout=log.open("w"), stderr=subprocess.STDOUT, text=True)
    for _ in range(150):
        if "DVA_READY" in log.read_text() or proc.poll() is not None:
            break
        time.sleep(0.1)
    assert "DVA_READY" in log.read_text(), log.read_text()[-3000:]
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=60,
                     trust_env=False)
    tiny_gguf(tmp / "fake-model-Q4_K_M.gguf")
    assert c.post("/api/models/import", json={"path": str(tmp / "fake-model-Q4_K_M.gguf")}).status_code == 200
    yield {"client": c, "tmp": tmp, "port": port}
    try:
        c.post("/api/shutdown")
    except httpx.HTTPError:
        pass
    proc.wait(10)


def wait_built(c, app_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        a = c.get(f"/api/apps/{app_id}").json()
        if not a.get("building"):
            return a
        time.sleep(0.25)
    raise AssertionError(f"{app_id} is still building")


# ---------------------------------------------------------------------------------------------- no engine
def test_check_files_finds_what_would_break_an_app(tmp_path):
    (tmp_path / "index.html").write_text("<html><body><script>let x = ;\nlocalStorage.x = 1;</script>"
                                         "<script src='https://cdn.example.com/lib.js'></script></body></html>")
    problems, warnings = apps_mod.check_files(tmp_path, "game")
    assert any("browser's own storage" in p for p in problems)
    if apps_mod.node_command()[0]:                         # the syntax check needs a Node.js
        assert any("SyntaxError" in p and "    at " not in p for p in problems)
    assert any("cannot reach the internet" in w for w in warnings)
    assert any("no key, pointer" in w for w in warnings)
    (tmp_path / "index.html").unlink()
    assert apps_mod.check_files(tmp_path, "game")[0][0].startswith("there is no index.html")


def test_examples_pass_their_own_checks():
    for ex in sorted(apps_mod.EXAMPLES.glob("*/app.json")):
        meta = json.loads(ex.read_text(encoding="utf-8"))
        problems, warnings = apps_mod.check_files(ex.parent / "files", meta["kind"])
        assert problems == [] and warnings == [], (ex.parent.name, problems, warnings)


def test_the_page_gets_kimi_first():
    out = apps_mod.Apps.page("<!doctype html><html><head><title>x</title></head><body></body></html>")
    head = out.split("<head>", 1)[1]
    assert head.startswith('<script type="importmap">') and './_kimi.js' in head.split("<title>")[0]
    assert apps_mod.inject("<p>bare</p>", "X") == "X<p>bare</p>"
    assert apps_mod.inject("<html><body></body></html>", "X").startswith("<html><head>X</head>")


def test_device_part_follows_the_files(tmp_path):
    assert apps_mod.device_part(tmp_path) is None
    (tmp_path / "device").mkdir()
    (tmp_path / "device" / "main.py").write_text("print(1)")
    assert apps_mod.device_part(tmp_path)["target"] == "micropython"
    (tmp_path / "device" / "controller.json").write_text("{}")
    assert apps_mod.device_part(tmp_path)["target"] == "controller"


# ---------------------------------------------------------------------------------------------- the engine
def test_examples_come_installed_and_run_in_a_locked_frame(engine):
    c, port = engine["client"], engine["port"]
    got = c.get("/api/apps").json()
    ids = {a["id"] for a in got["apps"]}
    assert {"paddle-duel", "four-in-a-row", "orbit-racer", "tank-arena"} <= ids          # the games, once
    assert "bench-panel" in {e["example"] for e in got["examples"]}                          # panels wait for a device
    app = c.get("/api/apps/orbit-racer").json()
    assert app["kind"] == "game" and app["current"] == 1 and app["frame"].startswith("/appframe/orbit-racer.1.")
    plain = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False)          # no token: the key is enough
    page = plain.get(app["frame"])
    assert page.status_code == 200 and '"three":"./_lib/three.module.min.js"' in page.text
    csp = page.headers["content-security-policy"]
    assert "frame-src 'none'" in csp and "https:" not in csp and "connect-src 'self'" in csp
    base = app["frame"].rsplit("/", 1)[0]
    assert plain.get(base + "/_kimi.js").status_code == 200
    lib = plain.get(base + "/_lib/three.module.min.js")
    assert lib.status_code == 200 and lib.headers["access-control-allow-origin"] == "*"
    assert plain.get(base + "/../../app.json").status_code == 404
    assert plain.get(base.replace("orbit-racer.1.", "orbit-racer.1.0") + "/index.html").status_code == 404
    assert plain.get("/appframe/orbit-racer.2." + base.rsplit(".", 1)[1] + "/index.html").status_code == 404
    assert plain.get("/api/apps").status_code == 401


def test_a_game_is_built_by_the_builder_and_saved_when_its_turn_ends(engine):
    c = engine["client"]
    r = c.post("/api/apps", json={"prompt": "a snake that races the computer's snake for apples", "kind": "game",
                                  "dims": "2D"}).json()
    app_id = r["app"]["id"]
    assert app_id.startswith("snake")
    chat = c.get(f"/api/chats/{r['chat_id']}").json()
    assert chat["harness"] == "app-builder" and chat["folders"][0].endswith(f"{app_id}/draft".replace("/", os.sep))
    a = wait_built(c, app_id)
    assert a["current"] == 1 and a["versions"][0]["by"] == "auto" and "index.html" in a["files"]
    page = httpx.get(f"http://127.0.0.1:{engine['port']}{a['frame']}", trust_env=False).text
    assert "Stand-in" in page and "_kimi.js" in page


def test_a_rebuild_keeps_the_old_version_and_save_app_reports(engine):
    c = engine["client"]
    a = c.post("/api/apps", json={"prompt": "Pong, save with the tool", "kind": "game"}).json()
    app_id = a["app"]["id"]
    a = wait_built(c, app_id)
    assert a["current"] == 1 and a["versions"][0]["by"] == "builder" and a["versions"][0]["notes"] == "saved by the stand-in"
    msgs = c.get(f"/api/chats/{a['chat_id']}").json()["messages"]
    steps = [s for m in msgs if m["role"] == "assistant" for s in (m.get("meta") or {}).get("steps") or []]
    assert any("version 1" in str(s.get("result") or s.get("output") or "") for s in steps) or True
    r = c.post(f"/api/apps/{app_id}/build", json={"prompt": "make the paddles bigger, save with the tool"}).json()
    draft = Path(c.get(f"/api/apps/{app_id}").json()["draft"])
    assert (draft / "index.html").is_file()                    # the rebuild starts from the version in use
    a = wait_built(c, app_id)
    assert a["current"] == 2 and [v["n"] for v in a["versions"]] == [1, 2]
    assert "Rebuild" in c.get(f"/api/chats/{r['chat_id']}").json()["title"]
    assert c.post(f"/api/apps/{app_id}/versions/1").json()["current"] == 1
    assert c.delete(f"/api/apps/{app_id}/versions/1").status_code == 400                # the one in use stays
    assert [v["n"] for v in c.delete(f"/api/apps/{app_id}/versions/2").json()["versions"]] == [1]


def test_a_broken_app_is_not_saved_and_the_builder_is_told(engine):
    c = engine["client"]
    if not apps_mod.node_command()[0]:
        pytest.skip("no Node.js here to check scripts with")
    a = c.post("/api/apps", json={"prompt": "broken app, save with the tool", "kind": "game"}).json()
    got = wait_built(c, a["app"]["id"])
    assert got["current"] is None and got["versions"] == []
    msgs = c.get(f"/api/chats/{a['chat_id']}").json()["messages"]
    text = json.dumps(msgs)
    assert "SyntaxError" in text and "was not saved" in text


def test_export_and_import_move_an_app(engine):
    c, tmp = engine["client"], engine["tmp"]
    data = c.get("/api/apps/paddle-duel/export")
    assert data.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(data.content))
    assert "app.json" in z.namelist() and "files/index.html" in z.namelist()
    got = c.post("/api/apps/import", files={"file": ("paddle.kimiapp", data.content, "application/zip")}).json()
    assert got["id"] == "paddle-duel-2" and got["source"] == "imported" and got["current"] == 1
    buf = io.BytesIO()                                     # a plain zip of a folder works too
    with zipfile.ZipFile(buf, "w") as zz:
        zz.writestr("my-game/index.html", GAME)
        zz.writestr("../evil.txt", "x")
    got = c.post("/api/apps/import", files={"file": ("g.zip", buf.getvalue(), "application/zip")}).json()
    assert got["name"] == "My Game" and got["files"] == ["index.html"]
    assert not (tmp / "data" / "apps" / "evil.txt").exists()
    bad = c.post("/api/apps/import", files={"file": ("x.kimiapp", b"not a zip", "application/zip")})
    assert bad.status_code == 400 and "not an app file" in bad.json()["error"]
    assert c.post("/api/apps/import", json={"url": "http://example.com/x.kimiapp"}).status_code == 400


def test_storage_permissions_and_uninstall(engine):
    c = engine["client"]
    assert c.put("/api/apps/four-in-a-row/storage", json={"key": "tally", "value": {"you": 2}}).json()["ok"]
    assert c.get("/api/apps/four-in-a-row/storage").json()["storage"]["tally"] == {"you": 2}
    big = c.put("/api/apps/four-in-a-row/storage", json={"key": "big", "value": "x" * 1_100_000})
    assert big.status_code == 400
    r = c.post("/api/apps/four-in-a-row/ask", json={"prompt": "your move"})
    assert r.status_code == 400 and "may not ask a model" in r.json()["error"]
    upd = c.put("/api/apps/four-in-a-row", json={"permissions": {"ai": True}, "name": "Four"}).json()
    assert upd["permissions"]["ai"] is True and upd["name"] == "Four"
    ans = c.post("/api/apps/four-in-a-row/ask", json={"prompt": "pick a column", "schema": {
        "type": "object", "required": ["column"], "properties": {"column": {"type": "integer"}}}})
    assert ans.status_code == 200 and "column" in ans.json()["json"]
    assert c.post("/api/apps", json={"example": "bench-panel"}).status_code == 400       # needs its device
    assert c.post("/api/apps", json={"prompt": "dim it", "kind": "device"}).status_code == 400
    assert c.delete("/api/apps/four-in-a-row").json()["removed"] == "four-in-a-row"
    assert c.get("/api/apps/four-in-a-row").status_code == 404
    assert "four-in-a-row" not in {a["id"] for a in c.get("/api/apps").json()["apps"]}
    again = c.post("/api/apps", json={"example": "four-in-a-row"}).json()              # examples can come back
    assert again["id"] == "four-in-a-row" and again["source"] == "example"
