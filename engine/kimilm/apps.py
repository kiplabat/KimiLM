"""Apps (2.34.0): small programs a model builds for you, kept, opened like any other app and rebuilt when you have
a better model. Two kinds:

  device  a control panel for one attached device: sliders, switches, choices and live charts that act on the
          device through KimiLM (never directly), and optionally code that runs on the device itself (a KimiOS
          controller, a MicroPython program, a .uf2, files for a phone), installed from the panel.
  game    a game you describe: 2D or 3D, with things that move and the computer as a player.

An app is a folder of web files (index.html and whatever it loads) with a manifest (app.json). Every build is a
new numbered version; older versions stay until you delete them, so a rebuild with a better model never loses the
app you had. An app is built by an agent in a chat (harness "App builder") that works in the app's draft folder
and saves with the save_app tool; when the turn ends without a save, a changed draft is saved for it.

Apps run in a sandboxed frame with no origin of their own (server: /appframe/<key>/...): they cannot read
KimiLM's API, other apps or the internet. Everything they do goes through the window, which shows what they ask
for: the device the app was made for (changes need the person's yes once per app; anything that may move, heat
or switch a real device asks every time unless the device is marked safe), a small store of their own, and a
question to a model (once per app). Files: <data>/apps/<id>/{app.json, v<n>/, draft/, storage.json}.

Apps travel as .kimiapp files (a zip: app.json + the current version's files), imported from a file or a web
address. The examples (four games and a panel for KimiOS's simulated devices) come with KimiLM."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from .db import now
from .paths import BUILTIN

KINDS = ("device", "game")
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
MAX_APP_BYTES = 8 * 1024 * 1024          # one version's files (three.js is not counted: KimiLM serves it)
MAX_FILES = 300
MAX_STORAGE = 1024 * 1024
SKIP = re.compile(r"(^|/)(\.|__pycache__|node_modules)")
EXAMPLES = BUILTIN / "apps"
LIB = EXAMPLES / "lib"
SDK = EXAMPLES / "kimi.js"
COLORS = ["#E8705A", "#4E8DE0", "#46A37B", "#C9A227", "#9B6FD6", "#D9658F", "#3FA7B5", "#7E8A97"]
TEXT_TYPES = (".html", ".htm", ".js", ".mjs", ".css", ".json", ".svg", ".txt", ".md", ".py", ".csv")


class AppError(ValueError):
    """Said in words the person (or the agent building the app) can act on."""


def slug(text, fallback="app"):
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40].strip("-")
    return s or fallback


def folder_size(folder):
    return sum(p.stat().st_size for p in Path(folder).rglob("*") if p.is_file())


def app_files(folder):
    folder = Path(folder)
    return sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*")
                  if p.is_file() and not SKIP.search(str(p.relative_to(folder)).replace("\\", "/")))


def inject(html, extra):
    """Put `extra` at the start of the document's head (before any script of the app), adding a head if needed."""
    m = re.search(r"<head\b[^>]*>", html, re.I)
    if m:
        return html[:m.end()] + extra + html[m.end():]
    m = re.search(r"<html\b[^>]*>", html, re.I)
    if m:
        return html[:m.end()] + "<head>" + extra + "</head>" + html[m.end():]
    m = re.match(r"\s*<!doctype[^>]*>", html, re.I)
    if m:
        return html[:m.end()] + extra + html[m.end():]
    return extra + html


SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.I | re.S)


def scripts_of(html):
    """The inline scripts of a page: [(is_module, text)] (import maps and JSON blocks are not code)."""
    out = []
    for attrs, body in SCRIPT.findall(html):
        if re.search(r"\bsrc\s*=", attrs, re.I) or not body.strip():
            continue
        typ = (re.search(r"type\s*=\s*[\"']?([^\"'\s>]+)", attrs, re.I) or [None, ""])[1].lower()
        if typ in ("importmap", "application/json", "application/ld+json", "text/template", "x-shader/x-vertex",
                   "x-shader/x-fragment", "text/plain") or (typ and "shader" in typ):
            continue
        out.append((typ == "module", body))
    return out


def node_command():
    """A Node.js to check scripts with: the desktop app's own (Electron as Node), or one on PATH; None otherwise."""
    node = os.environ.get("KIMILM_NODE")
    if node and Path(node).exists():
        env = {**os.environ, "ELECTRON_RUN_AS_NODE": "1"} if os.environ.get("KIMILM_NODE_IS_ELECTRON") else None
        return node, env
    found = shutil.which("node")
    return (found, None) if found else (None, None)


def check_syntax(code, module):
    """A syntax error in one script, as text, or None (also None when there is no Node.js to ask)."""
    node, env = node_command()
    if not node:
        return None
    with tempfile.TemporaryDirectory(prefix="kimi-app-") as tmp:
        path = Path(tmp) / ("s.mjs" if module else "s.js")
        path.write_text(code, encoding="utf-8")
        try:
            out = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=30, env=env,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return None
    if out.returncode == 0:
        return None
    text = (out.stderr or out.stdout or "").replace(str(path), "script")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("Node.js v")
             and not re.match(r"^\s+at ", ln)]
    return "\n".join(lines[:6])[:600] or "a syntax error"


def check_files(folder, kind):
    """(problems that stop a save, warnings) for a draft of an app."""
    folder = Path(folder)
    problems, warnings = [], []
    index = folder / "index.html"
    if not index.is_file():
        return ["there is no index.html in the app's folder: write the app as index.html (it may load other files "
                "from the same folder)"], []
    files = app_files(folder)
    if len(files) > MAX_FILES:
        problems.append(f"the app has {len(files)} files; at most {MAX_FILES}")
    size = sum((folder / f).stat().st_size for f in files)
    if size > MAX_APP_BYTES:
        problems.append(f"the app's files take {size / 1e6:.1f} MB; at most {MAX_APP_BYTES / 1e6:.0f} MB "
                        "(three.js is provided: import it from 'three', do not copy it)")
    texts = {}
    for f in files:
        if f.lower().endswith(TEXT_TYPES):
            try:
                texts[f] = (folder / f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    html = texts.get("index.html", "")
    if not re.search(r"<(body|canvas|div|main|script)\b", html, re.I):
        problems.append("index.html has no body, canvas or script: it would show nothing")
    for f, text in texts.items():
        if not f.endswith((".html", ".htm", ".js", ".mjs", ".css")):
            continue
        for url in re.findall(r"(?:src|href)\s*=\s*[\"'](https?://[^\"']+)", text, re.I) + \
                re.findall(r"(?:import\s[^;]*?from\s*|import\s*\(\s*)[\"'](https?://[^\"']+)", text):
            warnings.append(f"{f} loads {url[:90]}: apps cannot reach the internet, so it will not load. Use files in "
                            "the app's folder, or import three.js from 'three'")
        if re.search(r"\bfetch\s*\(\s*[\"']https?://|new\s+WebSocket\s*\(", text):
            warnings.append(f"{f} reaches out to the network: apps cannot (devices are reached with kimi.device)")
        if re.search(r"\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b", text):
            problems.append(f"{f} uses the browser's own storage, which apps do not have: use kimi.storage.get/set")
        if re.search(r"\b(alert|confirm|prompt)\s*\(", text) and not re.search(r"kimi\.(ai\.)?(ask|prompt)", text):
            warnings.append(f"{f} uses alert/confirm/prompt, which do nothing in an app: show it on the page")
    for f, text in texts.items():
        if f.endswith((".html", ".htm")):
            for i, (module, code) in enumerate(scripts_of(text)):
                err = check_syntax(code, module)
                if err:
                    problems.append(f"{f}, script {i + 1}: {err}")
        elif f.endswith((".js", ".mjs")):
            err = check_syntax(text, f.endswith(".mjs") or bool(re.search(r"^\s*(import|export)\b", text, re.M)))
            if err:
                problems.append(f"{f}: {err}")
    every = "\n".join(texts.values())
    if kind == "device" and "kimi.device" not in every:
        warnings.append("the panel never uses kimi.device, so it cannot show or change the device")
    if kind == "game" and not re.search(r"keydown|pointerdown|mousedown|touchstart|click|getGamepads", every):
        warnings.append("the game listens to no key, pointer, touch or gamepad: the player cannot move anything")
    return problems, warnings


def device_part(folder):
    """What a version would install on its device: {"target", "files"} or None. The target follows the files:
    device/controller.json (KimiOS's guard), device/*.py (MicroPython), device/*.uf2 (a board's boot drive), anything
    else under device/ (copied to a phone)."""
    d = Path(folder) / "device"
    if not d.is_dir():
        return None
    files = app_files(d)
    if not files:
        return None
    if "controller.json" in files:
        return {"target": "controller", "files": ["controller.json"]}
    uf2 = [f for f in files if f.lower().endswith(".uf2")]
    if uf2:
        return {"target": "uf2", "files": uf2[:1]}
    py = [f for f in files if f.endswith(".py")]
    if py:
        return {"target": "micropython", "files": py}
    return {"target": "files", "files": files}


class Apps:
    def __init__(self, app):
        self.app = app
        self.root = app.paths.data / "apps"
        self.root.mkdir(parents=True, exist_ok=True)
        self.secret = secrets.token_bytes(24)       # frame addresses change at each start
        self.asking = {}                            # app id -> lock: one question to a model at a time
        self.preview_data = {}                      # frame key -> what the preview page answers with (2.35.0)
        self.preview_results = {}                   # frame key -> what the preview page sent back (2.36.0)
        self.previewing = asyncio.Lock()            # one preview at a time: a headless browser is not small

    # ---------------------------------------------------------------- the store
    def _dir(self, app_id):
        if not ID.match(str(app_id or "")):
            raise KeyError(app_id)
        return self.root / app_id

    def manifest(self, app_id):
        path = self._dir(app_id) / "app.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KeyError(app_id) from None

    def _save(self, m):
        m["updated"] = now()
        folder = self._dir(m["id"])
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "app.json.tmp"
        tmp.write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, folder / "app.json")
        self.app.bus.publish("apps_changed", app_id=m["id"])
        return m

    def _ids(self):
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and (p / "app.json").is_file()
                      and ID.match(p.name))

    @staticmethod
    def device_ref(d):
        """What an app keeps of its device (from Devices.describe): enough to find it again and to brief a builder."""
        return {"id": d["id"], "key": d.get("key") or d["id"], "name": d["name"], "kind": d["kind"],
                "bench": bool(d.get("bench")), "bench_id": d.get("bench_id"), "simulated": bool(d.get("simulated")),
                "model": d.get("name"), "actions": [{k: x.get(k) for k in ("name", "about", "args", "changes", "physical")}
                                                    for x in d.get("actions") or []]}

    async def find_device(self, words):
        """A device by id, or by words of its name or kind (for an agent that says 'the monitor')."""
        words = str(words or "").strip()
        if not words:
            raise AppError("Say which device the panel is for")
        found = await self.app.devices.look()
        for d in found:
            if words in (d.get("id"), d.get("key")):
                return self.device_ref(await self.app.devices.describe(d["id"]))
        low = set(re.findall(r"[a-z0-9]+", words.lower())) - {"the", "my", "a", "an", "device"}
        scored = sorted(((len(low & set(re.findall(r"[a-z0-9]+", f"{d.get('name')} {d.get('kind')} {d.get('id')}".lower()))), d)
                         for d in found), key=lambda x: -x[0])
        if not scored or scored[0][0] == 0:
            names = ", ".join(f"{d.get('name')} ({d.get('id')})" for d in found[:12])
            raise AppError(f"No device matches “{words}”. Devices here: {names or 'none'}")
        return self.device_ref(await self.app.devices.describe(scored[0][1]["id"]))

    def summary(self, m):
        cur = next((v for v in m.get("versions") or [] if v["n"] == m.get("current")), None)
        out = {k: m.get(k) for k in ("id", "name", "kind", "about", "icon", "device", "source", "current", "shortcut",
                                     "chat_id", "building", "created", "updated", "installed", "permissions", "dims",
                                     "example", "from")}
        out["versions"] = len(m.get("versions") or [])
        out["version"] = cur
        out["ready"] = cur is not None
        shot = ((cur or {}).get("preview") or {}).get("shot")
        out["shot"] = shot if shot and Path(shot).is_file() else None       # 2.36.0: its picture when tried
        return out

    def list(self, kind=None, device=None):
        out = []
        for app_id in self._ids():
            try:
                m = self.manifest(app_id)
            except KeyError:
                continue
            if kind and m.get("kind") != kind:
                continue
            if device and (m.get("device") or {}).get("id") != device and (m.get("device") or {}).get("key") != device:
                continue
            out.append(self.summary(m))
        out.sort(key=lambda a: a.get("updated") or "", reverse=True)
        return out

    def get(self, app_id):
        m = self.manifest(app_id)
        out = {**m, **self.summary(m), "versions": m.get("versions") or []}
        folder = self._dir(app_id)
        if m.get("current"):
            vdir = folder / f"v{m['current']}"
            out["files"] = app_files(vdir)
            out["device_part"] = device_part(vdir)
            out["frame"] = self.frame_url(app_id, m["current"])
        draft = folder / "draft"
        out["draft"] = str(draft)
        return out

    def _unique_id(self, name):
        base = slug(name)
        app_id, n = base, 2
        while (self.root / app_id).exists():
            app_id, n = f"{base}-{n}", n + 1
        return app_id

    def create(self, name, kind, about="", device=None, icon=None, source="built", dims=None):
        if kind not in KINDS:
            raise AppError("An app is a device panel ('device') or a game ('game')")
        name = " ".join(str(name or "").split())[:60]
        if not name:
            raise AppError("Give the app a name")
        if kind == "device" and not device:
            raise AppError("A device panel is made for one device: choose it in Devices")
        app_id = self._unique_id(name)
        m = {"schema": 1, "id": app_id, "name": name, "kind": kind, "about": str(about or "")[:400],
             "icon": icon or {"emoji": "🎮" if kind == "game" else "🎛️",
                              "color": COLORS[int(hashlib.sha1(app_id.encode()).hexdigest(), 16) % len(COLORS)]},
             "device": device, "dims": dims, "versions": [], "current": None, "source": source,
             "permissions": {"control": False, "ai": False}, "shortcut": False, "chat_id": None, "building": None,
             "installed": None, "created": now()}
        (self._dir(app_id) / "draft").mkdir(parents=True, exist_ok=True)
        return self._save(m)

    def update(self, app_id, changes):
        m = self.manifest(app_id)
        if "name" in changes and str(changes["name"]).strip():
            m["name"] = " ".join(str(changes["name"]).split())[:60]
        if "about" in changes:
            m["about"] = str(changes["about"] or "")[:400]
        if isinstance(changes.get("icon"), dict):
            m["icon"] = {"emoji": str(changes["icon"].get("emoji") or m["icon"].get("emoji"))[:8],
                         "color": str(changes["icon"].get("color") or m["icon"].get("color"))[:9]}
        if isinstance(changes.get("permissions"), dict):
            m["permissions"] = {**(m.get("permissions") or {}),
                                **{k: bool(v) for k, v in changes["permissions"].items() if k in ("control", "ai")}}
        if "shortcut" in changes:
            m["shortcut"] = bool(changes["shortcut"])
        return self._save(m)

    def delete(self, app_id):
        """Uninstall: the app, every version and its stored data. A device keeps what was installed on it."""
        folder = self._dir(app_id)
        if not folder.exists():
            raise KeyError(app_id)
        m = self.manifest(app_id)
        shutil.rmtree(folder, ignore_errors=True)
        self.app.bus.publish("apps_changed", app_id=app_id, removed=True)
        return {"removed": app_id, "shortcut": bool(m.get("shortcut"))}

    # ---------------------------------------------------------------- versions
    def save_version(self, app_id, source=None, notes="", prompt="", model="", chat_id=None, by="builder"):
        """Check a folder (the draft by default) and keep it as the next version, which becomes the current one.
        Returns {"version", "warnings"}; raises AppError with what to fix when it cannot be saved."""
        m = self.manifest(app_id)
        folder = self._dir(app_id)
        src = Path(source) if source else folder / "draft"
        problems, warnings = check_files(src, m["kind"])
        if problems:
            raise AppError("The app was not saved. Fix: " + " | ".join(problems))
        n = max([v["n"] for v in m.get("versions") or []] or [0]) + 1
        dest = folder / f"v{n}"
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for f in app_files(src):
            (dest / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / f, dest / f)
        part = device_part(dest)
        m.setdefault("versions", []).append({
            "n": n, "created": now(), "prompt": str(prompt or "")[:2000], "model": str(model or "")[:120],
            "notes": str(notes or "")[:1000], "chat_id": chat_id, "by": by, "size": folder_size(dest),
            "files": len(app_files(dest)), "warnings": warnings[:10], "device_part": part["target"] if part else None,
            "fingerprint": self.fingerprint(dest)})
        m["current"] = n
        m["building"] = None
        self._save(m)
        return {"version": n, "warnings": warnings, "device_part": part}

    @staticmethod
    def fingerprint(folder):
        h = hashlib.sha256()
        for f in app_files(folder):
            h.update(f.encode())
            h.update(hashlib.sha256((Path(folder) / f).read_bytes()).digest())
        return h.hexdigest()[:16]

    def use_version(self, app_id, n):
        m = self.manifest(app_id)
        if not any(v["n"] == int(n) for v in m.get("versions") or []):
            raise KeyError(f"version {n}")
        m["current"] = int(n)
        return self._save(m)

    def delete_version(self, app_id, n):
        m = self.manifest(app_id)
        n = int(n)
        if not any(v["n"] == n for v in m.get("versions") or []):
            raise KeyError(f"version {n}")
        if m.get("current") == n:
            raise AppError("This is the version in use: use another one first (or uninstall the app)")
        shutil.rmtree(self._dir(app_id) / f"v{n}", ignore_errors=True)
        (self._dir(app_id) / "shots" / f"v{n}.png").unlink(missing_ok=True)
        m["versions"] = [v for v in m["versions"] if v["n"] != n]
        return self._save(m)

    def version_dir(self, app_id, n=None):
        m = self.manifest(app_id)
        n = int(n or m.get("current") or 0)
        if not n or not any(v["n"] == n for v in m.get("versions") or []):
            raise AppError(f"{m['name']} has no version {n or ''} yet: build it first".replace("  ", " "))
        return self._dir(app_id) / f"v{n}"

    def reset_draft(self, app_id, fresh=False):
        """The builder's folder: a copy of the version in use (so a rebuild improves it), or empty (fresh)."""
        m = self.manifest(app_id)
        draft = self._dir(app_id) / "draft"
        if draft.exists():
            shutil.rmtree(draft)
        draft.mkdir(parents=True)
        if not fresh and m.get("current"):
            src = self._dir(app_id) / f"v{m['current']}"
            for f in app_files(src):
                (draft / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / f, draft / f)
        return draft

    # ---------------------------------------------------------------- running in a frame
    def frame_key(self, app_id, n):
        sig = hmac.new(self.secret, f"{app_id}.{n}".encode(), hashlib.sha256).hexdigest()[:24]
        return f"{app_id}.{n}.{sig}"

    def frame_url(self, app_id, n):
        return f"/appframe/{self.frame_key(app_id, n)}/index.html"

    def frame_file(self, key, path):
        """(file, app id, version) for an address under /appframe/<key>/; KeyError when the key or path is not
        one of an app's."""
        try:
            app_id, n, sig = str(key).split(".")
        except ValueError:
            raise KeyError("frame") from None
        if not hmac.compare_digest(self.frame_key(app_id, n), str(key)):
            raise KeyError("frame")
        path = str(path or "index.html").replace("\\", "/").lstrip("/")
        if path == "_kimi.js":
            return SDK, app_id, int(n)
        if path in ("_preview.html", "_preview.js"):          # 2.35.0: the page an app is tried in
            return EXAMPLES / path[1:], app_id, int(n)
        if path.startswith("_lib/"):
            base = LIB.resolve()
            target = (base / path[5:]).resolve()
            if base not in target.parents or not target.is_file():
                raise KeyError(path)
            return target, app_id, int(n)
        base = (self._dir(app_id) / f"v{int(n)}").resolve()
        target = (base / path).resolve()
        if (target != base and base not in target.parents) or not target.is_file() or SKIP.search(path):
            raise KeyError(path)
        return target, app_id, int(n)

    @staticmethod
    def page(html):
        """The app's index.html with KimiLM's part first: an import map (three.js and its controls, and the
        kimi/game engine, all served by KimiLM) and the kimi API."""
        extra = ('<script type="importmap">{"imports":{"three":"./_lib/three.module.min.js",'
                 '"three/addons/":"./_lib/addons/","kimi/game":"./_lib/kimi-game/kimi-game.js"}}</script>'
                 '<script src="./_kimi.js"></script>')
        return inject(html, extra)

    @staticmethod
    def frame_policy(host):
        """The frame's own content security policy: its files and KimiLM's libraries; nothing from the internet."""
        me = f"http://{host}" if host else ""
        own = f"'self' {me} data: blob:".strip()
        return (f"default-src {own}; script-src {own} 'unsafe-inline' 'unsafe-eval'; style-src {own} 'unsafe-inline'; "
                f"img-src {own}; media-src {own}; font-src {own}; connect-src {own}; worker-src {own}; "
                "frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'self'")

    # ---------------------------------------------------------------- trying it before the person does (2.35.0)
    def preview_result(self, key, data):
        """What a preview page sent back (only while that preview runs)."""
        if key not in self.preview_data:
            raise KeyError(key)
        self.preview_results[key] = data
        return {"ok": True}

    def preview_page(self, key):
        """The preview page (builtin/apps/preview.html) with the stand-in answers for this app filled in."""
        data = self.preview_data.get(key) or {}
        text = (EXAMPLES / "preview.html").read_text(encoding="utf-8")
        blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        return text.replace("__PREVIEW_DATA__", blob)

    async def _preview_answers(self, m):
        """What the preview answers kimi.device with: the device's real description and state (reading only)."""
        dev = m.get("device") or {}
        out = {"app": {"id": m["id"], "name": m["name"], "kind": m["kind"], "version": m.get("current")},
               "device": {k: dev.get(k) for k in ("id", "name", "kind", "bench", "simulated")} if dev.get("id") else None}
        if m["kind"] != "device" or not dev.get("id"):
            return out
        try:
            if dev.get("bench"):
                from . import bench
                bid = dev.get("bench_id") or str(dev["id"]).removeprefix("bench:")
                out["info"] = await bench.envelope({"op": "device", "device": bid})
                try:
                    out["history"] = (await bench.envelope({"op": "history", "device": bid, "limit": 300})).get("rows") or []
                except Exception:                                     # noqa: BLE001 — no driver yet: no history
                    out["history"] = []
            else:
                out["info"] = await self.app.devices.describe(dev["id"])
        except Exception as exc:                                      # noqa: BLE001 — unplugged: describe what it was
            out["info"] = {"id": dev.get("id"), "name": dev.get("name"), "kind": dev.get("kind"),
                           "actions": dev.get("actions") or [], "note": f"not reachable now: {exc}"}
        return out

    async def preview(self, app_id, n=None):
        """Open a version for a few seconds in a headless browser with stand-in answers, and keep what it showed
        with the version: {"ok", "problems", "notes", "how", "at"}; {"skipped": why} when it cannot be tried."""
        from . import app_preview
        m = self.manifest(app_id)
        n = int(n or m.get("current") or 0)
        if not any(v["n"] == n for v in m.get("versions") or []):
            raise AppError(f"{m['name']} has no version {n or ''} to try".replace("  ", " "))
        if self.app.db.setting("apps_preview") is False:
            return {"skipped": "trying apps is switched off (Settings)"}
        port = os.environ.get("KIMILM_PORT")
        if not port:
            return {"skipped": "the engine's address is not known"}
        key = self.frame_key(app_id, n)
        shot = self._dir(app_id) / "shots" / f"v{n}.png"          # 2.36.0: its picture, outside the app's files
        shot.parent.mkdir(parents=True, exist_ok=True)
        shot.unlink(missing_ok=True)
        async with self.previewing:
            self.preview_data[key] = await self._preview_answers(m)
            self.preview_results.pop(key, None)
            try:
                result, how = await app_preview.run(f"http://127.0.0.1:{port}/appframe/{key}/_preview.html", shot,
                                                    lambda: self.preview_results.get(key))
            finally:
                self.preview_data.pop(key, None)
                self.preview_results.pop(key, None)
        if result is None:
            got = {"skipped": how, "at": now()}
        else:
            problems, notes = app_preview.summary(result, m["kind"])
            got = {"ok": not problems, "problems": problems, "notes": notes, "how": how, "at": now()}
        if shot.is_file() and shot.stat().st_size > 0:
            got["shot"] = str(shot)
        m = self.manifest(app_id)
        for v in m.get("versions") or []:
            if v["n"] == n:
                v["preview"] = got
        self._save(m)
        return got

    async def preview_examples(self, delay=45):
        """Once after a start: try the examples that have never been tried, so their tiles have pictures. Late and
        one at a time, so the start itself is not slowed down."""
        await asyncio.sleep(delay)
        for app_id in self._ids():
            try:
                m = self.manifest(app_id)
                cur = next((v for v in m.get("versions") or [] if v["n"] == m.get("current")), None)
                if m.get("source") == "example" and cur and not cur.get("preview"):
                    got = await self.preview(app_id, cur["n"])
                    if got.get("skipped"):
                        return                     # no browser here, or switched off: do not try the others
            except Exception as exc:               # noqa: BLE001 — pictures are a nicety
                self.app.log(f"apps: trying the example {app_id}: {exc}")

    @staticmethod
    def preview_text(got):
        """The preview, in words for the builder (and the chat)."""
        if not got:
            return ""
        if got.get("skipped"):
            return f"It could not be tried before the person sees it: {got['skipped']}."
        lines = [f"Tried it for a few seconds ({got.get('how')}), with keys pressed and a click; nothing reached a "
                 "device or a model:"]
        if got.get("notes"):
            lines.append("- " + "; ".join(got["notes"]))
        if got.get("problems"):
            lines.append("To fix (then save again):\n" + "\n".join(f"- {p}" for p in got["problems"]))
        else:
            lines.append("- no problems seen.")
        if got.get("shot"):
            lines.append(f"A picture of it after the keys and the click: {got['shot']} (look at it with look_at_image "
                         "if you can see pictures: is it what was asked, readable, not empty or cut off?)")
        return "\n".join(lines)

    # ---------------------------------------------------------------- the app's own store
    def storage(self, app_id):
        try:
            return json.loads((self._dir(app_id) / "storage.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.manifest(app_id)                  # KeyError for an app that does not exist
            return {}

    def storage_set(self, app_id, key, value):
        data = self.storage(app_id)
        key = str(key)[:200]
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        text = json.dumps(data, ensure_ascii=False)
        if len(text.encode()) > MAX_STORAGE:
            raise AppError("The app's store is full (1 MB): it should keep less")
        (self._dir(app_id) / "storage.json").write_text(text, encoding="utf-8")
        return {"ok": True}

    # ---------------------------------------------------------------- moving apps between computers
    def export(self, app_id):
        """A .kimiapp file of the version in use (app.json + its files), in <data>/exports."""
        m = self.manifest(app_id)
        vdir = self.version_dir(app_id)
        cur = next(v for v in m["versions"] if v["n"] == m["current"])
        meta = {k: m.get(k) for k in ("name", "kind", "about", "icon", "dims")}
        meta.update({"schema": 1, "format": "kimiapp", "version": cur["n"], "notes": cur.get("notes"),
                     "prompt": cur.get("prompt"), "model": cur.get("model"),
                     "device": {k: (m.get("device") or {}).get(k) for k in ("kind", "model", "name", "bench")}
                     if m.get("device") else None, "exported": now(), "kimilm": self.app_version()})
        out = self.app.paths.data / "exports" / f"{m['id']}-v{cur['n']}.kimiapp"
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("app.json", json.dumps(meta, indent=1, ensure_ascii=False))
            for f in app_files(vdir):
                z.write(vdir / f, "files/" + f)
        return out

    @staticmethod
    def app_version():
        from . import VERSION
        return VERSION

    def import_bytes(self, data, origin="a file", device=None):
        """Install an app from a .kimiapp file's bytes (or a zip of a folder with index.html). A device panel made
        elsewhere is bound to `device` (one of this computer's) when given; otherwise it waits to be bound."""
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise AppError("That is not an app file (.kimiapp)") from None
        names = [n for n in z.namelist() if not n.endswith("/")]
        if len(names) > MAX_FILES + 1:
            raise AppError("The app file has too many files")
        if sum(i.file_size for i in z.infolist()) > MAX_APP_BYTES + 200_000:
            raise AppError("The app file is too large once unpacked")
        meta = {}
        if "app.json" in names:
            try:
                meta = json.loads(z.read("app.json").decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise AppError("The app file's app.json cannot be read") from None
        prefix = "files/" if any(n.startswith("files/") for n in names) else ""
        if not prefix:              # a plain zip of a folder: find index.html
            idx = sorted((n for n in names if n.rsplit("/", 1)[-1] == "index.html"), key=len)
            if not idx:
                raise AppError("The file has no index.html: it is not an app")
            prefix = idx[0][:-len("index.html")]
        kind = meta.get("kind") if meta.get("kind") in KINDS else "game"
        name = meta.get("name") or Path(prefix.rstrip("/") or "Imported app").name.replace("-", " ").title()
        dev = device
        if kind == "device" and not dev:
            dev = {**(meta.get("device") or {}), "id": None, "unbound": True}
        m = self.create(name, kind, about=meta.get("about") or "", device=dev or None,
                        icon=meta.get("icon") if isinstance(meta.get("icon"), dict) else None, source="imported",
                        dims=meta.get("dims"))
        m["from"] = origin[:300]
        self._save(m)
        draft = self._dir(m["id"]) / "draft"
        try:
            for n in names:
                if not n.startswith(prefix) or n == "app.json":
                    continue
                rel = n[len(prefix):]
                if not rel or ".." in Path(rel).parts or rel.startswith("/") or ":" in rel:
                    continue
                dest = draft / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(n))
            self.save_version(m["id"], notes=meta.get("notes") or f"Imported from {origin}",
                              prompt=meta.get("prompt") or "", model=meta.get("model") or "", by="import")
        except AppError:
            shutil.rmtree(self._dir(m["id"]), ignore_errors=True)
            raise
        return self.get(m["id"])

    async def import_url(self, url, device=None):
        url = str(url or "").strip()
        if not re.match(r"^https://\S+$", url):
            raise AppError("Give a web address that starts with https://")
        import httpx
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
                async with client.stream("GET", url) as r:
                    if r.status_code != 200:
                        raise AppError(f"The address answered {r.status_code}")
                    data = b""
                    async for chunk in r.aiter_bytes():
                        data += chunk
                        if len(data) > MAX_APP_BYTES + 500_000:
                            raise AppError("The file at that address is too large to be an app")
        except httpx.HTTPError as exc:
            raise AppError(f"The app could not be downloaded: {exc}") from None
        return self.import_bytes(data, origin=url, device=device)

    # ---------------------------------------------------------------- kimios-registry (2.35.0)
    def share(self, app_id, note=""):
        """Write the version in use as a registry entry on this computer and give the link that proposes it."""
        from . import app_share
        m = self.manifest(app_id)
        vdir = self.version_dir(app_id)
        try:
            entry = app_share.make_entry(m, vdir, note, self.app_version())
        except app_share.ShareError as exc:
            raise AppError(str(exc)) from None
        problems = app_share.check_entry(app_share.entry_path(entry), json.dumps(entry).encode("utf-8"), check_files)
        if problems:
            raise AppError("The registry would not take it yet: " + "; ".join(p.split(": ", 1)[-1] for p in problems))
        return app_share.prepare(entry, self.app.paths.data / "exports" / "share")

    async def registry(self):
        """The apps in the registry, marked when one is installed here."""
        from . import app_share
        try:
            rows = await app_share.index()
        except app_share.ShareError as exc:
            raise AppError(str(exc)) from None
        have = {}
        for app_id in self._ids():
            try:
                origin = self.manifest(app_id).get("from") or ""
            except KeyError:
                continue
            if origin.startswith("the registry: "):
                have[origin[len("the registry: "):]] = app_id
        return [{**r, "installed": have.get(r.get("path"))} for r in rows]

    async def install_from_registry(self, rel, device=None):
        from . import app_share
        try:
            entry = await app_share.get(rel)
        except app_share.ShareError as exc:
            raise AppError(str(exc)) from None
        return await asyncio.to_thread(self.import_bytes, app_share.as_kimiapp(entry), f"the registry: {rel}", device)

    def bind(self, app_id, device):
        """Give an imported device panel one of this computer's devices."""
        m = self.manifest(app_id)
        if m["kind"] != "device":
            raise AppError("Only a device panel is made for a device")
        m["device"] = device
        m["permissions"] = {**(m.get("permissions") or {}), "control": False}
        return self._save(m)

    # ---------------------------------------------------------------- examples
    def examples(self):
        out = []
        for f in sorted(EXAMPLES.glob("*/app.json")):
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"example": f.parent.name, **{k: meta.get(k) for k in ("name", "kind", "about", "icon",
                                                                             "dims", "for")}})
        return out

    def add_example(self, example, device=None):
        src = EXAMPLES / str(example)
        if not ID.match(str(example)) or not (src / "app.json").is_file():
            raise KeyError(example)
        meta = json.loads((src / "app.json").read_text(encoding="utf-8"))
        if meta.get("kind") == "device" and not device:
            raise AppError("Choose the device this panel is for (Devices → the device → Control panels)")
        m = self.create(meta["name"], meta["kind"], about=meta.get("about") or "", device=device,
                        icon=meta.get("icon"), source="example", dims=meta.get("dims"))
        m["example"] = example
        self._save(m)
        self.save_version(m["id"], source=src / "files", notes=meta.get("notes") or "The example that comes with KimiLM",
                          prompt=meta.get("prompt") or "", model="", by="example")
        return self.get(m["id"])

    def seed(self):
        """Once: the example games, so the Games window is not empty the first time."""
        if self.app.db.setting("apps_seeded"):
            return
        for ex in self.examples():
            if ex["kind"] == "game":
                try:
                    self.add_example(ex["example"])
                except (AppError, KeyError, OSError) as exc:
                    self.app.log(f"apps: example {ex['example']}: {exc}")
        self.app.db.set_setting("apps_seeded", True)

    # ---------------------------------------------------------------- building with a model
    def build(self, *, app_id=None, prompt="", name="", kind="game", device=None, model=None, fresh=False,
              dims=None, project_id=None):
        """Start (or continue) building an app: a chat with the App builder working in the app's draft folder.
        A rebuild keeps every earlier version; the draft starts from the version in use unless `fresh`."""
        prompt = str(prompt or "").strip()
        if not prompt:
            raise AppError("Say what the app should do")
        if app_id:
            m = self.manifest(app_id)
        else:
            m = self.create(name or self._name_from(prompt, kind), kind, about=prompt[:400], device=device, dims=dims)
        if m.get("kind") == "device" and not (m.get("device") or {}).get("id"):
            raise AppError("Choose this panel's device first (it was made on another computer)")
        draft = self.reset_draft(m["id"], fresh=fresh or not m.get("current"))
        message = self.request(m, prompt, draft, rebuild=bool(m.get("current")) and not fresh)
        chat = self.app.create_chat(project_id=project_id, title=f"{'Rebuild' if m.get('current') else 'Build'}: {m['name']}",
                                    model=model or None, harness="app-builder", folders=[str(draft)], origin="app")
        # the builder writes only in the app's own draft folder (the chat's one folder): that needs no yes each time
        self.app.db.set_setting(f"chat_policy:{chat['id']}", {"allow": ["files"]})
        m = self.manifest(m["id"])               # the chat is known before its turn starts (a turn can end at once)
        m["chat_id"] = chat["id"]
        m["chats"] = (m.get("chats") or [])[-19:] + [chat["id"]]
        m["building"] = {"chat_id": chat["id"], "job_id": None, "started": now(), "prompt": prompt[:2000],
                         "fingerprint": self.fingerprint(draft), "model": model or ""}
        self._save(m)
        job = self.app.start_turn(chat["id"], message, payload={"app_id": m["id"]})
        m = self.manifest(m["id"])
        if m.get("building"):
            m["building"]["job_id"] = job["id"]
            self._save(m)
        return {"app": self.summary(m), "chat_id": chat["id"], "job_id": job["id"]}

    @staticmethod
    def _name_from(prompt, kind):
        """A short name from a description: "a snake game where the computer…" → "Snake game"."""
        words = re.findall(r"[A-Za-z0-9']+", prompt)
        while words and words[0].lower() in ("a", "an", "the", "make", "build", "create", "me", "please", "i", "want",
                                             "like", "would", "need", "game:", "us", "my", "simple", "small"):
            words.pop(0)
        out = []
        for w in words:
            if w.lower() in ("where", "that", "which", "who", "with", "in", "for", "and", "to", "of", "on", "using",
                             "about", "against", "so", "from", "by") and out:
                break
            out.append(w)
            if len(out) == 4:
                break
        text = " ".join(out).strip()
        return (text[:1].upper() + text[1:]) if text else ("New game" if kind == "game" else "Control panel")

    def request(self, m, prompt, draft, rebuild=False):
        """The first message to the App builder: what to make, for which device, and where."""
        lines = []
        if m["kind"] == "game":
            dims = (m.get("dims") or "").upper()
            lines.append(f"Build a game called “{m['name']}”{f' in {dims}' if dims in ('2D', '3D') else ''}: {prompt}")
            lines.append("Things the player moves must move smoothly (requestAnimationFrame, keys and touch, a gamepad "
                         "if one is there), and the computer must play too (an opponent, enemies or a rival with a "
                         "simple, beatable strategy written in the code). Show the score and how to play on the "
                         "screen, and let the player start again." + (" For 3D use three.js: import * as THREE from "
                                                                      "'three' in a module script." if dims == "3D" else ""))
        else:
            dev = m.get("device") or {}
            lines.append(f"Build a control panel called “{m['name']}” for the device {dev.get('name')} "
                         f"({dev.get('id')}, {dev.get('kind')}): {prompt}")
            lines.append(self.device_brief(dev))
        if rebuild:
            lines.append(f"This is a rebuild: the folder already holds version {m['current']} of the app. Read it first "
                         "and improve it (keep what works, change what was asked); do not start over unless asked.")
        lines.append(f"Work in {draft} (your folder): write index.html there (and any other files it loads), check "
                     "it, then call save_app with a one-line note. If save_app lists problems, fix them and call it "
                     "again.")
        return "\n\n".join(lines)

    def device_brief(self, dev):
        """What the builder needs to know about the device, in a few lines."""
        if dev.get("bench"):
            return ("It is run by KimiOS's guard. In the panel use: await kimi.device.info() for its datasheet "
                    "(sensors and outputs with units and limits, the set points it allows, its own controller in "
                    "datasheet.default) and status; await kimi.device.history() for recent readings; to make it hold "
                    "new targets, build a controller spec from datasheet.default (same loops, new setpoint values "
                    "within datasheet.sensors[s].setpoints) and call await kimi.device.control(spec) (it is checked "
                    "first); kimi.device.release() gives control back to the device's own controller"
                    + ("; await kimi.device.event(name, args) makes something happen to this simulated device "
                       "(door_open {seconds}, warm_load {compartment, degrees}, ambient {value})"
                       if dev.get("simulated") else "") + ". Call await kimi.device.info() early and build the "
                    "controls from what it returns rather than guessing names. Optionally write "
                    "device/controller.json (a controller spec) for 'Install on the device'.")
        acts = dev.get("actions") or []
        listed = "\n".join(f"- {a['name']}: {a['about']}" + (f" (arguments: {', '.join(f'{k}: {v}' for k, v in a['args'].items())})"
                                                              if a.get("args") else "")
                           for a in acts if a.get("name") != "info")
        part = {"board": "device/main.py (MicroPython) or a .uf2 file in device/", "phone": "files in device/ "
                "(copied to the phone's Download/KimiLM folder)", "serial": "device/main.py (MicroPython)"}.get(
                    dev.get("kind"), "")
        return ("In the panel use await kimi.device.info() (what it is and its actions) and "
                "await kimi.device.act(name, arguments) for these actions (arguments are strings):\n"
                + (listed or "- (it offers no actions)")
                + "\nShow each result on the page (act returns text). Poll with kimi.device.watch(fn, ms) only for "
                  "actions that read." + (f" Code that runs on the device itself goes in {part}." if part else ""))

    def after_turn(self, chat_id, status):
        """A builder's turn ended: if it changed the draft but did not save, save it for it (with its problems
        told in the chat when it cannot be saved). Called by AppState.after_turn."""
        for app_id in self._ids():
            try:
                m = self.manifest(app_id)
            except KeyError:
                continue
            if chat_id not in (m.get("chats") or []):
                continue
            b = m.get("building") or {}
            draft = self._dir(app_id) / "draft"
            fp = self.fingerprint(draft) if (draft / "index.html").is_file() else None
            last = next((v for v in m.get("versions") or [] if v["n"] == m.get("current")), {})
            if fp and fp != b.get("fingerprint") and fp != last.get("fingerprint"):
                try:
                    got = self.save_version(app_id, notes="Saved when the builder finished",
                                            prompt=b.get("prompt") or "", model=self._chat_model(chat_id),
                                            chat_id=chat_id, by="auto")
                    self._say(chat_id, f"Saved the app as version {got['version']}.")
                    self._later(self._preview_and_say(app_id, got["version"], chat_id))
                except AppError as exc:
                    self._say(chat_id, f"The app could not be saved: {exc}. Ask me to fix it.")
            m = self.manifest(app_id)
            if m.get("building"):
                m["building"] = None
                self._save(m)
            self.app.bus.publish("app_built", app_id=app_id, chat_id=chat_id, status=status,
                                 version=m.get("current"))
            return

    def _chat_model(self, chat_id):
        """The model that wrote the chat's last reply (its name where known)."""
        row = self.app.db.one("SELECT meta_json FROM messages WHERE chat_id=? AND role='assistant' ORDER BY created DESC, "
                              "rowid DESC LIMIT 1", (chat_id,))
        mid = ((row or {}).get("meta") or {}).get("model") or (self.app.db.get("chats", chat_id) or {}).get("model") or ""
        named = self.app.models.get_any(mid) if mid else None
        return (named or {}).get("name") or mid

    def _later(self, coro):
        try:
            task = asyncio.get_running_loop().create_task(coro)
            self._tasks = getattr(self, "_tasks", set())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except RuntimeError:                       # no loop (a test calling this directly)
            coro.close()

    async def _preview_and_say(self, app_id, n, chat_id):
        try:
            got = await self.preview(app_id, n)
        except Exception as exc:                   # noqa: BLE001 — a preview never breaks a saved app
            self.app.log(f"apps: preview of {app_id} v{n}: {exc}")
            return
        if got.get("problems"):
            self._say(chat_id, f"Version {n} was tried and showed problems: {'; '.join(got['problems'])[:300]}. "
                               "Ask the builder in this chat to fix them.")

    def _say(self, chat_id, text):
        self.app.bus.publish("notify", title="Apps", body=text, level="info", chat_id=chat_id)

    def saved_by_tool(self, chat_id, notes, model=""):
        """save_app: the app this chat builds, saved from its draft."""
        for app_id in self._ids():
            try:
                m = self.manifest(app_id)
            except KeyError:
                continue
            if chat_id in (m.get("chats") or []):
                got = self.save_version(app_id, notes=notes, prompt=(m.get("building") or {}).get("prompt") or "",
                                        model=model, chat_id=chat_id, by="builder")
                return m, got
        raise AppError("This chat is not building an app: start one from Games, Apps or a device's Control panels")

    # ---------------------------------------------------------------- what an app asks for
    async def ask(self, app_id, prompt, schema=None, max_tokens=400, once=False):
        """A question an app puts to a model (a move, a line of dialogue). Needs the app's 'ai' permission, or the
        person's yes for this time (`once`, said in the window)."""
        m = self.manifest(app_id)
        if not (m.get("permissions") or {}).get("ai") and not once:
            raise AppError("This app may not ask a model yet: allow it in the app's bar")
        prompt = str(prompt or "").strip()[:6000]
        if not prompt:
            raise AppError("Ask something")
        model_id, why = self.app.router.pick("worker")
        if not model_id:
            raise AppError(f"No model can answer: {why}")
        lock = self.asking.setdefault(app_id, asyncio.Lock())
        async with lock:
            async with self.app.runtime.lease(model_id, as_loaded=True) as server:
                messages = [{"role": "system", "content": f"You are part of the app “{m['name']}”. Answer briefly and "
                                                          "exactly in the form asked."},
                            {"role": "user", "content": prompt}]
                if isinstance(schema, dict):
                    got = await self.app.llm.json(messages, schema, max_tokens=int(max_tokens), server=server)
                    return {"json": got, "model": why}
                out = await self.app.llm.complete(messages, max_tokens=min(1200, int(max_tokens)), temperature=0.5,
                                                  server=server)
                return {"text": (out.get("content") or "").strip(), "model": why}

    # ---------------------------------------------------------------- installing on the device
    async def install(self, app_id, on_status=None):
        """Put the device part of the version in use on the device: start its controller (KimiOS), copy its
        MicroPython program to the board, flash its .uf2, or copy its files to the phone."""
        m = self.manifest(app_id)
        dev = m.get("device") or {}
        if m["kind"] != "device" or not dev.get("id"):
            raise AppError("Only a device panel bound to a device has something to install")
        vdir = self.version_dir(app_id)
        part = device_part(vdir)
        if not part:
            raise AppError("This version has nothing to install on the device (no device/ folder). Ask the builder "
                           "for code that runs on the device")
        say = on_status or (lambda text: None)
        target = part["target"]
        if target == "controller":
            from . import bench
            spec = json.loads((vdir / "device" / "controller.json").read_text(encoding="utf-8"))
            bid = dev.get("bench_id") or str(dev["id"]).removeprefix("bench:")
            say("Checking the controller against the device's datasheet…")
            checked = await bench.envelope({"op": "controller_check", "device": bid, "spec": spec})
            if not checked.get("valid"):
                raise AppError("KimiOS's guard did not accept the controller: " + "; ".join(checked.get("problems") or []))
            say("Starting it…")
            await bench.envelope({"op": "controller_start", "device": bid, "spec": spec, "by": f"app {m['name']}"})
            result = "The controller runs on the device (KimiOS's guard keeps it inside the datasheet)."
        elif target == "uf2":
            result = await self.app.devices.control(dev["id"], "flash_uf2", {"file": str(vdir / "device" / part["files"][0])},
                                                    on_status=say)
        elif target == "micropython":
            result = await self._mpremote(dev, vdir / "device", part["files"], say)
        else:
            d = await self.app.devices.describe(dev["id"])
            if d["kind"] != "phone":
                raise AppError(f"{d['name']} does not take files (only phones do); put MicroPython code in device/")
            to = f"/sdcard/Download/KimiLM/{m['id']}/"
            await self.app.devices.adb(d, "shell", "mkdir", "-p", to, on_status=say)
            for f in part["files"]:
                say(f"Copying {f}…")
                await self.app.devices.adb(d, "push", str(vdir / "device" / f), to + f, timeout=600, on_status=say)
            result = f"Copied {len(part['files'])} file(s) to the phone's {to}"
        m = self.manifest(app_id)
        m["installed"] = {"version": m["current"], "when": now(), "target": target, "result": str(result)[:500]}
        self._save(m)
        return {"result": result, "target": target}

    async def _mpremote(self, dev, folder, files, say):
        """MicroPython boards: mpremote (pip, into KimiLM's tools folder on first use) copies the files and resets."""
        from . import installer
        import sys
        d = await self.app.devices.describe(dev["id"])
        port = (d.get("ports") or [None])[0]
        if not port:
            raise AppError(f"{d['name']} has no serial port to reach MicroPython on")
        tools = installer.python_dir(self.app)
        if not (tools / "mpremote").is_dir():
            say("Installing mpremote (once)…")
            try:
                await installer.install(self.app, "mpremote", "python", on_status=say, by="Apps")
            except installer.InstallError as exc:
                raise AppError(f"mpremote is needed and could not be installed: {exc}") from None
        env = {**os.environ, "PYTHONPATH": str(tools) + os.pathsep + os.environ.get("PYTHONPATH", "")}

        async def run(*argv, timeout=120):
            proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "mpremote", "connect", port, *argv,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                        env=env)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return 124, "timed out"
            return proc.returncode, out.decode("utf-8", "replace")
        for f in files:
            say(f"Copying {f} to the board…")
            code, out = await run("fs", "cp", str(folder / f), f":{f}")
            if code != 0:
                raise AppError(f"mpremote could not copy {f}: {out.strip()[-300:]} (is MicroPython on the board?)")
        say("Restarting the board…")
        await run("reset", timeout=30)
        return f"Copied {', '.join(files)} to the board and restarted it" + (
            "" if "main.py" in files else " (only main.py runs by itself at start)")
