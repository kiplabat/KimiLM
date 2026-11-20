"""Connectors: MCP servers the agent can use (remote over HTTPS, or local programs over stdio).

Each connector has one long-lived session owned by its own background task (MCP sessions must be opened and
closed in the same task), restarted automatically after an error. Tool lists are cached.

2.10.0 — set up like in other apps, and more of the protocol:
  Configuration  A connector is described by the fields other apps use (mcp_config.py): command, args, env and
                 cwd for a local program; url, type (http = Streamable HTTP, sse = the older transport) and
                 headers for a remote one. Configurations can be pasted, taken over from apps on this computer,
                 and exported. Values of env and headers are kept in the credential store; the database holds
                 only their names. ${NAME} in a value is filled from the environment when the server starts.
  Sign-in        Remote servers: no sign-in, a key (bearer token), custom headers, or OAuth in the browser
                 (the MCP authorization flow, done by the client library). OAuth tokens are kept under
                 <data>/mcp-auth; a run with nobody watching never opens a browser: it says "sign in first".
  Protocol       The client library (fastmcp 4, MCP SDK 2) speaks protocol 2026-07-28 and the earlier
                 revisions and picks what the server supports. After connecting, the server's name, version,
                 protocol revision and capabilities are kept for the window (connectors.info_json).
  Beyond tools   Resources can be listed and read (also by agents, through two tools added when a connected
                 server has resources); prompts can be listed and filled in. Tool titles and the read-only and
                 destructive hints are used; images and files a tool returns are saved to the chat's folder.
  Commands       A command that is not installed gets a plain message saying what to install; "node" falls
                 back to the app's own Node and "python" to the app's Python."""
import asyncio
import base64
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from . import VERSION, mcp_config
from .db import new_id, now

READONLY_NAME = re.compile(r"^(get|list|search|find|read|inspect|validate|whoami|suggest|describe|query|fetch|show|"
                           r"browser_(snapshot|take_screenshot|console|network|tabs))", re.I)
RESULT_LIMIT = 12_000

PRESETS = [
    {"key": "browser", "name": "Browser (Playwright)", "transport": "stdio", "command": "{node}",
     "args": ["{playwright_mcp}", "--browser", "msedge", "--isolated"],
     "description": "Lets the agent open and read web pages in a private Microsoft Edge window "
                    "(clicks, forms, screenshots). Many tools: best with larger models."},
    {"key": "custom-http", "name": "Custom (HTTPS URL)", "transport": "http", "url": "", "auth": "none",
     "description": "Any MCP server reachable at an https:// address."},
    {"key": "custom-stdio", "name": "Custom (local program)", "transport": "stdio", "command": "", "args": [],
     "description": "A local MCP server started with a command, e.g. npx or uvx (Node.js / uv must be installed)."},
]


def safe_name(text):
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", text)[:40].strip("_") or "c"


def tool_key(connector_id, tool_name):
    return f"mcp__{safe_name(connector_id)}__{safe_name(tool_name)}"[:64]


EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "audio/wav": ".wav",
       "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "application/pdf": ".pdf", "text/csv": ".csv",
       "application/json": ".json", "text/plain": ".txt"}


def save_blob(data, mime, save_dir, stem):
    """Write what a tool returned as a file in the chat's folder; returns its path (None if it cannot be kept)."""
    if not save_dir or not data:
        return None
    try:
        folder = Path(save_dir) / "connector-files"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stem}-{int(time.time() * 1000) % 10**9}{EXT.get((mime or '').split(';')[0], '.bin')}"
        path.write_bytes(base64.b64decode(data))
        return path
    except (OSError, ValueError):
        return None


def result_text(result, save_dir=None, limit=None):
    if getattr(result, "structured_content", None) and not any(
            getattr(c, "type", "") in ("image", "audio") for c in getattr(result, "content", None) or []):
        text = json.dumps(result.structured_content, ensure_ascii=False, default=str)
    else:
        parts = []
        for c in getattr(result, "content", None) or []:
            kind = getattr(c, "type", "")
            if kind == "text":
                parts.append(c.text)
            elif kind in ("image", "audio"):
                mime = getattr(c, "mime_type", None) or getattr(c, "mimeType", None)
                path = save_blob(getattr(c, "data", None), mime, save_dir, kind)
                parts.append(f"[{kind} saved to {path}]" if path else f"[{kind} returned]")
            elif kind == "resource":                    # a document handed back inside the result
                res = getattr(c, "resource", None)
                body = getattr(res, "text", None)
                if body is not None:
                    parts.append(f"[{getattr(res, 'uri', 'resource')}]\n{body}")
                else:
                    mime = getattr(res, "mime_type", None) or getattr(res, "mimeType", None)
                    path = save_blob(getattr(res, "blob", None), mime, save_dir, "file")
                    parts.append(f"[file saved to {path}]" if path else f"[file {getattr(res, 'uri', '')} returned]")
            elif kind == "resource_link":
                parts.append(f"[resource {getattr(c, 'uri', '')}: read it with read_connector_resource]")
            else:
                parts.append(str(c)[:500])
        text = "\n".join(parts)
    limit = RESULT_LIMIT if limit is None else limit
    if len(text) > limit:
        text = text[:limit] + f"\n…(trimmed {len(text) - limit:,} characters)"
    return text


NEEDS = {"npx": "Node.js (nodejs.org)", "npm": "Node.js (nodejs.org)", "node": "Node.js (nodejs.org)",
         "uvx": "uv (docs.astral.sh/uv)", "uv": "uv (docs.astral.sh/uv)", "docker": "Docker Desktop",
         "bunx": "Bun (bun.sh)", "bun": "Bun (bun.sh)", "deno": "Deno (deno.com)", "pipx": "pipx"}


def resolve_command(command):
    """The program a local connector starts with → (executable, extra environment). A program that is not on
    this computer gets a message that says what to install instead of a bare "file not found"."""
    command = expand(command or "").strip()
    if not command:
        raise ValueError("This connector has no command")
    extra = {}
    if os.environ.get("DVA_NODE_IS_ELECTRON") and command == os.environ.get("DVA_NODE"):
        extra["ELECTRON_RUN_AS_NODE"] = "1"  # the app's own Electron runs Node programs; no Node.js install needed
    if Path(command).is_absolute() or os.sep in command or (os.altsep and os.altsep in command):
        return command, extra
    found = shutil.which(command)
    if found:
        return found, extra
    base = re.sub(r"\.(exe|cmd|bat)$", "", command.lower())
    if base == "node" and os.environ.get("DVA_NODE"):       # no Node.js installed: the app's own Node runs it
        if os.environ.get("DVA_NODE_IS_ELECTRON"):
            extra["ELECTRON_RUN_AS_NODE"] = "1"
        return os.environ["DVA_NODE"], extra
    if base in ("python", "python3", "py"):
        return sys.executable, extra
    need = NEEDS.get(base)
    raise ValueError(f"This connector starts with “{command}”, which is not installed on this computer. "
                     + (f"Install {need}, then test the connector again." if need else
                        "Install it, or give the full path of the program."))


class Session:
    def __init__(self, conf, auth_dir=None, interactive=False):
        self.conf = conf
        self.auth_dir = auth_dir          # where this connector's OAuth tokens are kept
        self.interactive = interactive    # may open the browser to sign in (the user pressed a button)
        self.queue = asyncio.Queue()
        self.task = None
        self.ready = asyncio.Event()
        self.error = None
        self.info = {}

    def _client(self):
        from fastmcp import Client
        c = self.conf
        missing = set()
        fill = lambda v: mcp_config.expand_vars(v, missing=missing)   # noqa: E731
        try:
            from mcp.types import Implementation
            who = {"client_info": Implementation(name="KimiLM", version=VERSION)}
        except Exception:
            who = {}
        if c["transport"] == "http":
            url = fill(c.get("url", ""))
            if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
                raise ValueError("Remote connectors must use https://")
            headers = {k: fill(v) for k, v in (c.get("headers") or {}).items() if v}
            auth = None
            if c.get("auth") == "oauth":
                if not self.interactive and not (c.get("signed_in")):
                    raise ConnectionError(f"{c.get('name') or 'This connector'} needs you to sign in first: open "
                                          "Plugins & connectors → Connectors and press Sign in.")
                from fastmcp.client.auth import OAuth
                from key_value.aio.stores.filetree import (FileTreeStore, FileTreeV1CollectionSanitizationStrategy,
                                                           FileTreeV1KeySanitizationStrategy)
                folder = Path(self.auth_dir)
                folder.mkdir(parents=True, exist_ok=True)
                # keys are server addresses: the strategies turn them into names every file system accepts
                store = FileTreeStore(data_directory=folder,
                                      key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(folder),
                                      collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(folder))
                auth = OAuth(mcp_url=url, client_name="KimiLM", scopes=c.get("scopes") or None, token_storage=store)
            elif c.get("token"):
                auth = fill(c["token"])
            if missing:
                raise ValueError("This connector needs a value for " + ", ".join(sorted(missing))
                                 + " (set it in the connector, or as an environment variable).")
            from fastmcp.client.transports import SSETransport, StreamableHttpTransport
            transport = (SSETransport if c.get("type") == "sse" else StreamableHttpTransport)(
                url, headers=headers or None, auth=auth)
            return Client(transport, timeout=120, **who)
        from fastmcp.client.transports import StdioTransport
        command, extra = resolve_command(c.get("command") or "")
        env = {**os.environ, **{k: fill(v) for k, v in (c.get("env") or {}).items()}, **extra}
        env.pop("DVA_TOKEN", None)
        args = [fill(expand(a)) for a in (c.get("args") or [])]
        cwd = fill(c.get("cwd") or "") or None
        if cwd and not Path(cwd).is_dir():
            raise ValueError(f"The connector's working folder does not exist: {cwd}")
        return Client(StdioTransport(command=command, args=args, env=env, cwd=cwd, keep_alive=False), timeout=120,
                      **who)

    def _describe(self, client):
        """What the server said about itself when the connection was made."""
        try:
            who = getattr(client, "server_info", None)
            caps = getattr(client, "server_capabilities", None)
            self.info = {"name": getattr(who, "name", None), "version": getattr(who, "version", None),
                         "title": getattr(who, "title", None),
                         "protocol": str(getattr(client, "protocol_version", "") or ""),
                         "capabilities": [k for k in ("tools", "resources", "prompts")
                                          if caps is not None and getattr(caps, k, None) is not None],
                         "instructions": (getattr(client, "instructions", None) or "")[:600]}
        except Exception:
            self.info = {}

    async def _run(self):
        try:
            async with self._client() as client:
                self.error = None
                self._describe(client)
                self.ready.set()
                while True:
                    fut, fn = await self.queue.get()
                    if fn is None:
                        fut.set_result(None)
                        return
                    try:
                        fut.set_result(await fn(client))
                    except Exception as exc:  # tool errors stay inside the session
                        if not fut.done():
                            fut.set_exception(exc)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {str(exc)[:300]}"
            self.ready.set()
            while not self.queue.empty():
                fut, _ = self.queue.get_nowait()
                if not fut.done():
                    fut.set_exception(ConnectionError(self.error))

    async def call(self, fn, timeout=180):
        if not self.task or self.task.done():
            self.ready = asyncio.Event()
            self.task = asyncio.create_task(self._run())
        start = 330 if self.interactive and self.conf.get("auth") == "oauth" else 90   # signing in takes a person
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=start)
        except asyncio.TimeoutError:
            raise ConnectionError(f"The connector did not start within {start} seconds")
        if self.error and self.task.done():
            raise ConnectionError(self.error)
        fut = asyncio.get_running_loop().create_future()
        await self.queue.put((fut, fn))
        return await asyncio.wait_for(fut, timeout)

    async def close(self):
        if self.task and not self.task.done():
            fut = asyncio.get_running_loop().create_future()
            await self.queue.put((fut, None))
            try:
                await asyncio.wait_for(fut, 10)
            except Exception:
                self.task.cancel()


def expand(value, env=None):
    value = value.replace("{node}", os.environ.get("DVA_NODE") or "node")
    value = value.replace("{playwright_mcp}", os.environ.get("DVA_PLAYWRIGHT_MCP", ""))
    return value.replace("{python}", sys.executable)


class Connectors:
    def __init__(self, db, secrets, plugins, bus):
        self.db, self.secrets, self.plugins, self.bus = db, secrets, plugins, bus
        self.sessions = {}
        self.tool_cache = {}  # id -> (time, tools)
        self.info = {}        # id -> what the server said about itself at the last connection
        self.unreachable = {}  # id -> (when, why) its tools could not be listed
        self.auth_root = plugins.paths.data / "mcp-auth"

    # ------------------------------------------------------------------ values kept in the credential store
    def _values(self, connector_id):
        """The values of a connector's env and headers ({"env": {…}, "headers": {…}}): secrets, not database."""
        try:
            got = json.loads(self.secrets.get(f"connector_values:{connector_id}") or "{}")
        except (ValueError, TypeError):
            got = {}
        return {"env": dict(got.get("env") or {}), "headers": dict(got.get("headers") or {})}

    def _save_values(self, connector_id, env=None, headers=None):
        """Merge new values in. A key that is gone is removed; an empty value for a known key keeps what was saved
        (the window never gets values back, so it sends empty for 'unchanged')."""
        old = self._values(connector_id)
        out = {}
        for part, new in (("env", env), ("headers", headers)):
            if new is None:
                out[part] = old[part]
            else:
                out[part] = {k: (str(v) if v not in (None, "") else old[part].get(k, "")) for k, v in new.items()}
        if out["env"] or out["headers"]:
            self.secrets.set(f"connector_values:{connector_id}", json.dumps(out))
        else:
            self.secrets.delete(f"connector_values:{connector_id}")
        return out

    def _auth_dir(self, connector_id):
        return self.auth_root / safe_name(connector_id)

    def signed_in(self, connector_id):
        return (self.auth_root / (safe_name(connector_id) + ".ok")).exists()

    # ------------------------------------------------------------------ configuration
    def all(self, reveal=False):
        out = []
        for row in self.db.all("SELECT * FROM connectors ORDER BY name"):
            token = self.secrets.get(f"connector:{row['id']}") if row["auth"] == "bearer" else None
            values = self._values(row["id"])
            env = {**(row.get("env") or {}), **values["env"]}             # (before 2.10.0 values were in the database)
            headers = {**(row.get("headers") or {}), **values["headers"]}
            kind = row.get("type") or ("stdio" if row["transport"] == "stdio" else "http")
            signed = row["auth"] == "oauth" and self.signed_in(row["id"])
            out.append({**row, "source": "user", "enabled": bool(row["enabled"]), "type": kind,
                        "env": env if reveal else {k: "" for k in env},
                        "headers": headers if reveal else {k: "" for k in headers},
                        "token": token if reveal else None, "has_token": bool(token), "signed_in": signed,
                        "ready": (row["auth"] != "bearer" or bool(token)) and (row["auth"] != "oauth" or signed)})
        for conf in self.plugins.connectors():
            item = dict(conf)
            item["has_token"] = bool(item.get("token"))
            item.setdefault("type", "stdio" if item.get("transport") == "stdio" else "http")
            if item.get("auth") == "oauth":
                item["signed_in"] = self.signed_in(item["id"])
                item["ready"] = item.get("ready", True) and item["signed_in"]
            if not reveal:
                item["token"] = None
                item["env"] = {k: "" for k in (item.get("env") or {})}
                item["headers"] = {k: "" for k in (item.get("headers") or {})}
            item.setdefault("approval", "changes")
            out.append(item)
        for item in out:
            cached = self.tool_cache.get(item["id"])
            item["tool_count"] = len(cached[1]) if cached else len(item.get("tools") or [])
            item["info"] = self.info.get(item["id"]) or item.get("info") or {}
        return out

    def get(self, connector_id, reveal=True):
        return next((c for c in self.all(reveal=reveal) if c["id"] == connector_id), None)

    @staticmethod
    def _shape(data):
        """Transport and type from whatever the form or an imported configuration gave."""
        transport = data.get("transport") or ("http" if data.get("url") else "stdio")
        kind = str(data.get("type") or "").lower()
        kind = "stdio" if transport == "stdio" else ("sse" if kind == "sse" else "http")
        return transport, kind

    def create(self, data):
        data = dict(data)
        if not str(data.get("name") or "").strip():
            raise ValueError("Give the connector a name")
        transport, kind = self._shape(data)
        if transport == "http" and not str(data.get("url") or "").strip():
            raise ValueError("A remote connector needs its URL")
        if transport == "stdio" and not str(data.get("command") or "").strip():
            raise ValueError("A local connector needs its command")
        cid = new_id("c_")
        token = data.pop("token", None)
        env = {str(k): str(v) for k, v in (data.get("env") or {}).items()}
        headers = {str(k): str(v) for k, v in (data.get("headers") or {}).items()}
        auth = data.get("auth") or ("bearer" if token else "none")
        self.db.insert("connectors", {
            "id": cid, "name": str(data["name"]).strip()[:60], "transport": transport, "type": kind,
            "url": data.get("url", ""), "command": data.get("command", ""), "args": data.get("args") or [],
            "env": {k: "" for k in env}, "headers": {k: "" for k in headers}, "cwd": data.get("cwd") or "",
            "auth": auth, "approval": data.get("approval", "changes"),
            "description": data.get("description", ""), "enabled": int(data.get("enabled", True) is not False),
            "created": now(), "updated": now()})
        if env or headers:
            self._save_values(cid, env, headers)
        if token:
            self.secrets.set(f"connector:{cid}", token)
        self.bus.publish("connectors_changed")
        return self.get(cid, reveal=False)

    async def update(self, connector_id, data):
        if ":" in connector_id:  # plugin connector: only enable/disable here
            if "enabled" in data:
                self.db.set_item_enabled("connector", connector_id, data["enabled"])
            if "approval" in data:
                self.db.set_setting(f"connector_approval:{connector_id}", data["approval"])
        else:
            data = dict(data)
            token = data.pop("token", None)
            allowed = {k: v for k, v in data.items() if k in ("name", "url", "command", "args", "auth", "cwd",
                                                             "approval", "description", "enabled")}
            if "type" in data or "transport" in data:
                row = self.db.get("connectors", connector_id) or {}
                allowed["type"] = self._shape({"transport": row.get("transport"), **data})[1]
            if "enabled" in allowed:
                allowed["enabled"] = int(bool(allowed["enabled"]))
            if "env" in data or "headers" in data:
                saved = self._save_values(connector_id, data.get("env"), data.get("headers"))
                if "env" in data:
                    allowed["env"] = {k: "" for k in saved["env"]}
                if "headers" in data:
                    allowed["headers"] = {k: "" for k in saved["headers"]}
            allowed["updated"] = now()
            self.db.update("connectors", connector_id, allowed)
            if token:
                self.secrets.set(f"connector:{connector_id}", token)
        await self.reset(connector_id)
        self.bus.publish("connectors_changed")
        return self.get(connector_id, reveal=False)

    async def delete(self, connector_id):
        await self.reset(connector_id)
        self.db.delete("connectors", connector_id)
        self.secrets.delete(f"connector:{connector_id}")
        self.secrets.delete(f"connector_values:{connector_id}")
        self._forget_sign_in(connector_id)
        self.bus.publish("connectors_changed")

    def _forget_sign_in(self, connector_id):
        shutil.rmtree(self._auth_dir(connector_id), ignore_errors=True)
        (self.auth_root / (safe_name(connector_id) + ".ok")).unlink(missing_ok=True)

    async def sign_out(self, connector_id):
        await self.reset(connector_id)
        self._forget_sign_in(connector_id)
        self.bus.publish("connectors_changed")

    async def reset(self, connector_id):
        session = self.sessions.pop(connector_id, None)
        self.tool_cache.pop(connector_id, None)
        if session:
            await session.close()

    def approval_mode(self, conf):
        return self.db.setting(f"connector_approval:{conf['id']}") or conf.get("approval") or "changes"

    # ------------------------------------------------------------------ taking over and handing on configurations
    def import_servers(self, entries):
        """Add servers from another app's configuration. One that is already here (same program or address) is
        skipped, so importing twice does no harm."""
        have = {(c["transport"], c.get("url") or "", c.get("command") or "", json.dumps(c.get("args") or []))
                for c in self.all()}
        names = {c["name"].lower() for c in self.all()}
        added, skipped = [], []
        for e in entries:
            key = (e["transport"], e.get("url") or "", e.get("command") or "", json.dumps(e.get("args") or []))
            if key in have:
                skipped.append({"name": e["name"], "why": "already a connector here"})
                continue
            name, n = e["name"], 2
            while name.lower() in names:
                name = f"{e['name']} {n}"
                n += 1
            names.add(name.lower())
            have.add(key)
            row = self.create({**e, "name": name, "description": e.get("description") or e.get("note") or ""})
            added.append({"id": row["id"], "name": row["name"], "auth": row["auth"], "note": e.get("note") or ""})
        return {"added": added, "skipped": skipped}

    def export_config(self, ids=None):
        rows = [c for c in self.all(reveal=True) if c["source"] == "user" and (not ids or c["id"] in ids)]
        return mcp_config.export(rows)

    # ------------------------------------------------------------------ use
    def _session(self, conf, interactive=False):
        s = self.sessions.get(conf["id"])
        if not s:
            s = self.sessions[conf["id"]] = Session(conf, self._auth_dir(conf["id"]), interactive)
        return s

    async def list_tools(self, connector_id, refresh=False, interactive=False):
        cached = self.tool_cache.get(connector_id)
        if cached and not refresh and time.time() - cached[0] < 600:
            return cached[1]
        conf = self.get(connector_id)
        if not conf:
            raise KeyError(connector_id)
        if not conf.get("ready", True) and not (interactive and conf.get("auth") == "oauth"):
            raise ConnectionError(f"{conf['name']} needs you to sign in first" if conf.get("auth") == "oauth"
                                  else f"{conf['name']} needs its key or token first")
        session = self._session(conf, interactive)
        tools = await session.call(lambda client: client.list_tools(), timeout=90)
        if conf.get("auth") == "oauth":       # the sign-in worked: later runs may use it without a browser
            self.auth_root.mkdir(parents=True, exist_ok=True)
            (self.auth_root / (safe_name(connector_id) + ".ok")).write_text(now(), encoding="utf-8")
        rows = []
        for t in tools:
            ann = getattr(t, "annotations", None)
            fields = getattr(type(ann), "model_fields", {}) if ann is not None else {}   # SDK 1 and 2 name them differently
            pick = lambda *names: next((getattr(ann, n) for n in names if n in fields and  # noqa: E731
                                        getattr(ann, n) is not None), None)
            hint = pick("read_only_hint", "readOnlyHint") if ann is not None else None
            destructive = pick("destructive_hint", "destructiveHint") if ann is not None else None
            title = getattr(t, "title", None) or (pick("title") if ann is not None else None)
            schema = getattr(t, "input_schema", None) if "input_schema" in getattr(type(t), "model_fields", {}) else \
                getattr(t, "inputSchema", None)
            # the server's own hint decides; without one, the tool's name is the guide
            readonly = bool(hint) if hint is not None else bool(READONLY_NAME.match(t.name))
            rows.append({"name": t.name, "title": title or "", "description": (t.description or "")[:600],
                         "parameters": schema or {"type": "object", "properties": {}},
                         "readonly": readonly, "destructive": bool(destructive) and not readonly})
        self.tool_cache[connector_id] = (time.time(), rows)
        self.unreachable.pop(connector_id, None)
        info = self.info[connector_id] = dict(session.info)
        # a server may announce resources and prompts and have none: count them, so nothing is offered in vain
        for kind, listing in (("resources", self.resources), ("prompts", self.prompts)):
            info[f"{kind}_count"] = 0
            if kind in (info.get("capabilities") or []):
                try:
                    info[f"{kind}_count"] = len(await listing(connector_id))
                except Exception:
                    pass
        if ":" not in connector_id:
            self.db.update("connectors", connector_id, {
                "tools": [{"name": r["name"], "description": r["description"][:200]} for r in rows],
                "info": info})
        return rows

    def has(self, connector_id, kind):
        """Does this server really have resources / prompts (as of its last connection)?"""
        return int((self.info.get(connector_id) or {}).get(f"{kind}_count") or 0) > 0

    # ------------------------------------------------------------------ what planners and agents are told (2.15.0)
    def usable(self):
        """The connectors a task may use without anyone choosing them for it: switched on, and able to connect
        with nobody there (its key is saved, its sign-in was made). A scheduled or long-running task that was
        given no connectors gets these, so that what is installed is what it can use."""
        return [c["id"] for c in self.all() if c.get("enabled", True) and c.get("ready", True)]

    def system_ids(self):
        """Connectors the operating system installed (KimiOS's tool server, 2.24.0): every agent chat gets them,
        as it gets the built-in tools, unless they are switched off in Plugins & connectors."""
        return [c["id"] for c in self.plugins.connectors() if c.get("system") and c.get("enabled", True)
                and c.get("ready", True)]

    async def learn(self, ids):
        """Make sure each of these servers has said what it offers (one connection each; kept for ten minutes).
        A server that cannot be reached is remembered with the reason and does not stop the others."""
        for cid in ids or []:
            if self.tool_cache.get(cid) and time.time() - self.tool_cache[cid][0] < 600:
                continue
            if self.down(cid):
                continue
            try:
                await self.list_tools(cid)
            except Exception as exc:
                self.mark_down(cid, exc)

    def mark_down(self, connector_id, exc):
        self.unreachable[connector_id] = (time.time(), f"{type(exc).__name__}: {str(exc)[:140]}")

    def down(self, connector_id):
        """Why this server could not be reached, if that was found out in the last ten minutes: a task with
        nobody watching does not wait for the same dead server again at every step."""
        when, why = self.unreachable.get(connector_id) or (0, "")
        return why if why and time.time() - when < 600 else None

    def offers(self, connector_id):
        """The tools of a server as last heard (from this session, else from what was saved at its last test)."""
        cached = self.tool_cache.get(connector_id)
        if cached:
            return cached[1]
        conf = self.get(connector_id, reveal=False) or {}
        return [{"name": t.get("name") or "", "description": t.get("description") or "",
                 "readonly": bool(READONLY_NAME.match(t.get("name") or ""))}
                for t in conf.get("tools") or [] if isinstance(t, dict) and t.get("name")]

    def names(self, ids):
        return [c["name"] for c in (self.get(cid, reveal=False) for cid in ids or []) if c and c.get("enabled", True)]

    def digest(self, ids, tools=10, width=70):
        """What the connected services promise, in a line or two each: the name, what it is for, and its tools
        with a few words on each. This is what makes a planner write 'look it up with the X connector' instead of
        'search the web', and what tells an agent that the endpoint it needs exists. No connection is made here:
        the text is built from what each server said when it was last connected."""
        def first(text, n):
            text = " ".join(str(text or "").split())
            cut = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
            return cut[:n].rstrip(" .,;:") + ("…" if len(cut) > n else "")

        lines = []
        for cid in ids or []:
            conf = self.get(cid, reveal=False)
            if not conf or not conf.get("enabled", True):
                continue
            info = self.info.get(cid) or conf.get("info") or {}
            about = first(conf.get("description") or info.get("instructions") or info.get("title") or "", 140)
            rows = self.offers(cid)
            head = f"- {conf['name']}" + (f": {about}" if about else "")
            if self.down(cid) and not rows:
                lines.append(f"{head} (it could not be reached: {self.down(cid)})")
                continue
            if not rows:
                lines.append(f"{head} (its tools are listed once it is connected)")
                continue
            # reading tools first: they are what a step usually needs, and they never ask
            ordered = sorted(rows, key=lambda t: not t.get("readonly"))
            shown = "; ".join(t["name"] + (f" ({first(t['description'], width)})" if t.get("description") else "")
                              for t in ordered[:tools])
            # 2.26.0: the rest by name, so an agent knows they exist (a 9B model wrote its own controller
            # tuning in Python because `control_tune` was hidden behind "and 9 more")
            rest = [t["name"] for t in ordered[tools:]]
            lines.append(f"{head}. Tools: {shown}" + (f"; also {', '.join(rest[:30])}" if rest else ""))
        return "\n".join(lines)

    async def resources(self, connector_id):
        """What the server offers to read: documents, records, files (and templates for addresses with blanks)."""
        conf = self.get(connector_id)

        async def both(client):
            rows = [{"uri": str(r.uri), "name": getattr(r, "title", None) or r.name,
                     "description": (getattr(r, "description", None) or "")[:300],
                     "mime": getattr(r, "mime_type", None) or getattr(r, "mimeType", None) or ""}
                    for r in await client.list_resources()]
            try:
                for t in await client.list_resource_templates():
                    uri = getattr(t, "uri_template", None) or getattr(t, "uriTemplate", None)
                    rows.append({"uri": str(uri), "name": getattr(t, "title", None) or t.name, "template": True,
                                 "description": (getattr(t, "description", None) or "")[:300], "mime": ""})
            except Exception:
                pass
            return rows
        return await self._session(conf).call(both, timeout=90)

    async def read_resource(self, connector_id, uri, save_dir=None):
        conf = self.get(connector_id)
        parts = await self._session(conf).call(lambda client: client.read_resource(uri), timeout=180)
        out = []
        for part in parts or []:
            body = getattr(part, "text", None)
            if body is not None:
                out.append(body)
            else:
                mime = getattr(part, "mime_type", None) or getattr(part, "mimeType", None)
                path = save_blob(getattr(part, "blob", None), mime, save_dir, "resource")
                out.append(f"[file saved to {path}]" if path else "[a file that cannot be shown as text]")
        text = "\n".join(out)
        return text if len(text) <= RESULT_LIMIT * 2 else text[:RESULT_LIMIT * 2] + "\n…(trimmed)"

    async def prompts(self, connector_id):
        conf = self.get(connector_id)
        rows = await self._session(conf).call(lambda client: client.list_prompts(), timeout=90)
        return [{"name": p.name, "title": getattr(p, "title", None) or "", "description": (p.description or "")[:300],
                 "arguments": [{"name": a.name, "description": getattr(a, "description", None) or "",
                                "required": bool(getattr(a, "required", False))} for a in (p.arguments or [])]}
                for p in rows]

    async def prompt(self, connector_id, name, arguments=None):
        """A prompt of the server, filled in: the text to put in front of a model."""
        conf = self.get(connector_id)
        got = await self._session(conf).call(lambda client: client.get_prompt(name, arguments or {}), timeout=90)
        lines = []
        for m in getattr(got, "messages", None) or []:
            content = getattr(m, "content", None)
            text = getattr(content, "text", None)
            if text:
                lines.append(text if getattr(m, "role", "user") == "user" else f"({m.role}) {text}")
        return "\n\n".join(lines)

    async def call(self, connector_id, tool_name, arguments, timeout=300, save_dir=None, limit=None):
        """`limit`: characters of the answer kept (default RESULT_LIMIT). An agent's call keeps far more (2.19.0):
        the runner gives the model what it can read and keeps the whole answer in a file."""
        conf = self.get(connector_id)
        result = await self._session(conf).call(
            lambda client: client.call_tool(tool_name, arguments or {}, raise_on_error=False), timeout=timeout)
        text = result_text(result, save_dir, limit)
        if getattr(result, "is_error", False):
            return False, text or "The connector reported an error"
        return True, text

    async def test(self, connector_id):
        """Connect (signing in if the server asks for it) and say what the server offers."""
        await self.reset(connector_id)
        tools = await self.list_tools(connector_id, refresh=True, interactive=True)
        out = {"ok": True, "tools": tools, "info": self.info.get(connector_id) or {}, "resources": [], "prompts": []}
        if self.has(connector_id, "resources"):
            try:
                out["resources"] = await self.resources(connector_id)
            except Exception as exc:
                out["resources_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        if self.has(connector_id, "prompts"):
            try:
                out["prompts"] = await self.prompts(connector_id)
            except Exception as exc:
                out["prompts_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        self.bus.publish("connectors_changed")
        return out

    async def shutdown(self):
        for cid in list(self.sessions):
            await self.reset(cid)
