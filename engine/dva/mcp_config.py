"""MCP server configuration the way other apps write it (2.10.0), so a server set up elsewhere works here.

Most apps describe MCP servers with the same small JSON shape, usually under "mcpServers":

    {"mcpServers": {"files":  {"command": "npx", "args": ["-y", "@scope/server"], "env": {"KEY": "…"}},
                    "notes":  {"type": "http", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer …"}}}}

The differences are small and all handled by `parse`: VS Code calls the map "servers" and writes JSON with
comments; Zed calls it "context_servers" and may nest the command; some apps write "serverUrl" or "httpUrl", mark
a server "disabled", or reach a remote server through the `mcp-remote` bridge (read here as the remote server it
points at, signed in with OAuth). Values may refer to environment variables as ${NAME}, ${env:NAME} or
${NAME:-default}; they are kept as written and filled in when the server is started (`expand_vars`).

`find_configs` looks for the config files of apps installed on this computer, `export` writes KimiLM's
connectors in the same shape (secrets become ${NAME} references, never values), and `bundle_to_plugin` turns the
manifest of an MCP bundle (.mcpb, the one-file server packages) into a KimiLM plugin."""
import json
import os
import re
import sys
from pathlib import Path

VAR = re.compile(r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
SECRET_NAME = re.compile(r"key|token|secret|password|passwd|credential|authorization|cookie", re.I)
MAPS = ("mcpServers", "servers", "context_servers", "mcp_servers")


def strip_jsonc(text):
    """JSON with // and /* */ comments and trailing commas (VS Code, Zed) → plain JSON."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def loads(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(strip_jsonc(text))


def expand_vars(value, values=None, missing=None):
    """Fill ${NAME}, ${env:NAME} and ${NAME:-default} from `values`, then from the environment. A name with no
    value and no default becomes empty and is added to `missing`."""
    def one(m):
        name, default = m.group(1), m.group(2)
        got = (values or {}).get(name)
        if got in (None, ""):
            got = os.environ.get(name)
        if got in (None, ""):
            if default is None and missing is not None:
                missing.add(name)
            return default or ""
        return str(got)
    return VAR.sub(one, str(value))


def normalise(name, raw):
    """One server entry from any app's config → KimiLM's fields, or None if it describes no server."""
    if not isinstance(raw, dict):
        return None
    command = raw.get("command")
    if isinstance(command, dict):                        # Zed: {"command": {"path": …, "args": […], "env": {…}}}
        raw = {**raw, "command": command.get("path"), "args": command.get("args") or raw.get("args"),
               "env": command.get("env") or raw.get("env")}
    if isinstance(raw.get("command"), list) and raw["command"]:      # a few apps write the command as a list
        raw = {**raw, "command": raw["command"][0], "args": list(raw["command"][1:]) + list(raw.get("args") or [])}
    url = raw.get("url") or raw.get("serverUrl") or raw.get("httpUrl") or raw.get("uri")
    kind = str(raw.get("type") or raw.get("transport") or "").lower().replace("_", "-")
    args = [str(a) for a in (raw.get("args") or [])]
    headers = {str(k): str(v) for k, v in (raw.get("headers") or {}).items()}
    entry = {"name": str(name).strip()[:60] or "server", "description": str(raw.get("description") or "")[:300],
             "enabled": not raw.get("disabled") and raw.get("enabled", True) is not False, "note": ""}
    auth = "oauth" if raw.get("auth") == "oauth" or raw.get("oauth") else "none"
    bridge = next((i for i, a in enumerate(args) if re.fullmatch(r"mcp-remote(@.+)?", a)), None)
    if not url and raw.get("command") and bridge is not None:
        url = next((a for a in args[bridge + 1:] if re.match(r"https?://", a)), None)
        if url:                                          # the bridge only exists to reach this remote server
            for i, a in enumerate(args):
                if a == "--header" and i + 1 < len(args) and ":" in args[i + 1]:
                    key, value = args[i + 1].split(":", 1)
                    headers[key.strip()] = value.strip()
            auth = "none" if headers else "oauth"
            entry["note"] = "was set up through the mcp-remote bridge; KimiLM connects to the server directly"
    if url:
        token = ""
        bearer = next((k for k in headers if k.lower() == "authorization"), None)
        if bearer and headers[bearer].lower().startswith("bearer ") and not VAR.search(headers[bearer]):
            token, auth = headers.pop(bearer)[7:].strip(), "bearer"
        sse = kind == "sse" or (not kind and str(url).split("?")[0].rstrip("/").endswith("/sse"))
        entry.update(transport="http", type="sse" if sse else "http", url=str(url), headers=headers, auth=auth,
                     token=token, command="", args=[], env={}, cwd="")
    elif raw.get("command"):
        entry.update(transport="stdio", type="stdio", command=str(raw["command"]), args=args,
                     env={str(k): str(v) for k, v in (raw.get("env") or {}).items()}, cwd=str(raw.get("cwd") or ""),
                     url="", headers={}, auth="none", token="")
    else:
        return None
    return entry


def parse(source):
    """Any of the usual config shapes (text or already-parsed) → a list of server entries."""
    data = loads(source) if isinstance(source, str) else source
    if not isinstance(data, dict):
        raise ValueError("This is not an MCP server configuration (expected a JSON object)")
    servers = None
    for key in MAPS:
        if isinstance(data.get(key), dict):
            servers = data[key]
            break
    if servers is None and isinstance(data.get("mcp"), dict):          # VS Code settings.json: {"mcp": {"servers": …}}
        servers = next((data["mcp"][k] for k in MAPS if isinstance(data["mcp"].get(k), dict)), None)
    if servers is None:
        if data.get("command") or data.get("url"):                     # one bare server entry
            servers = {data.get("name") or "server": data}
        elif data and all(isinstance(v, dict) and (v.get("command") or v.get("url") or v.get("serverUrl"))
                          for v in data.values()):
            servers = data                                             # the map itself, without its wrapper
        else:
            raise ValueError('No servers found. Paste a configuration with an "mcpServers" section, or one '
                             'server with "command" or "url".')
    out = [e for e in (normalise(name, raw) for name, raw in servers.items()) if e]
    if not out:
        raise ValueError("None of the entries has a command or a URL")
    return out


def _user_dirs(home=None, env=None):
    home = Path(home or Path.home())
    env = os.environ if env is None else env
    if sys.platform == "win32" or env.get("APPDATA"):
        roaming = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        roaming = home / "Library" / "Application Support"
    else:
        roaming = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return home, roaming


def find_configs(home=None, env=None):
    """MCP servers already set up in other apps on this computer: [{app, path, servers}]. Files are only read."""
    home, roaming = _user_dirs(home, env)
    places = [("Claude Desktop", roaming / "Claude" / "claude_desktop_config.json"),
              ("Claude Code", home / ".claude.json"),
              ("Cursor", home / ".cursor" / "mcp.json"),
              ("VS Code", roaming / "Code" / "User" / "mcp.json"),
              ("VS Code Insiders", roaming / "Code - Insiders" / "User" / "mcp.json"),
              ("Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
              ("LM Studio", home / ".lmstudio" / "mcp.json"),
              ("Gemini CLI", home / ".gemini" / "settings.json"),
              ("Zed", roaming / "Zed" / "settings.json")]
    found = []
    for app, path in places:
        try:
            if not path.is_file() or path.stat().st_size > 8 * 2**20:
                continue
            data = loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        groups = [(app, data)]
        if app == "Claude Code" and isinstance(data, dict):            # servers kept per project folder
            for folder, proj in (data.get("projects") or {}).items():
                if isinstance(proj, dict) and proj.get("mcpServers"):
                    groups.append((f"Claude Code · {Path(folder).name}", {"mcpServers": proj["mcpServers"]}))
        for label, group in groups:
            try:
                servers = parse(group)
            except ValueError:
                continue
            found.append({"app": label, "path": str(path), "servers": servers})
    return found


def masked(entry):
    """An entry for showing: secret values are replaced by dots (the names stay)."""
    hide = lambda d: {k: ("••••" if v and not VAR.fullmatch(v) else v) for k, v in (d or {}).items()}  # noqa: E731
    return {**entry, "env": hide(entry.get("env")), "headers": hide(entry.get("headers")),
            "token": "••••" if entry.get("token") else ""}


def export(connectors):
    """KimiLM's connectors in the shape other apps read. Secret values are never written: they become
    ${NAME} references the person fills in (or sets as environment variables) where the file is used."""
    out = {}
    for c in connectors:
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", c.get("name") or "server").strip("-").lower() or "server"
        while name in out:
            name += "-2"
        ref = lambda key: "${" + re.sub(r"[^A-Za-z0-9]+", "_", key).upper().strip("_") + "}"   # noqa: E731
        if c.get("transport") == "http":
            entry = {"type": "sse" if c.get("type") == "sse" else "http", "url": c.get("url") or ""}
            headers = {k: (ref(f"{name}_{k}") if SECRET_NAME.search(k) else (v or ref(f"{name}_{k}")))
                       for k, v in (c.get("headers") or {}).items()}
            if c.get("auth") == "bearer":
                headers["Authorization"] = "Bearer " + ref(f"{name}_token")
            if headers:
                entry["headers"] = headers
        else:
            # {python} and {node} mean "the app's own" here; other apps need the plain program names
            entry = {"command": (c.get("command") or "").replace("{python}", "python").replace("{node}", "node"),
                     "args": list(c.get("args") or [])}
            env = {k: (ref(k) if SECRET_NAME.search(k) or not v else v) for k, v in (c.get("env") or {}).items()}
            if env:
                entry["env"] = env
            if c.get("cwd"):
                entry["cwd"] = c["cwd"]
        out[name] = entry
    return {"mcpServers": out}


# ---------------------------------------------------------------------- MCP bundles (.mcpb)
def bundle_to_plugin(manifest):
    """The manifest.json of an MCP bundle → the plugin.json KimiLM installs it as. The bundle's own files stay
    as they are; its server becomes a connector whose ${__dirname} and ${user_config.…} are filled in at start."""
    server = manifest.get("server") if isinstance(manifest, dict) else None
    if not isinstance(server, dict) or not manifest.get("name"):
        raise ValueError("This is not an MCP bundle: manifest.json has no name or no server")
    conf = dict(server.get("mcp_config") or {})
    platform = {"win32": "win32", "darwin": "darwin"}.get(sys.platform, "linux")
    conf.update((conf.pop("platform_overrides", None) or {}).get(platform) or {})
    kind = server.get("type")
    if not conf.get("command"):
        entry = server.get("entry_point") or ""
        if kind == "uv":                                  # the host runs it: uv reads the bundle's pyproject.toml
            conf = {"command": "uv", "args": ["run", "--directory", "${__dirname}", entry]}
        elif kind in ("node", "python") and entry:
            conf = {"command": kind, "args": ["${__dirname}/" + entry]}
        else:
            raise ValueError("The bundle does not say how to start its server (server.mcp_config.command)")
    platforms = (manifest.get("compatibility") or {}).get("platforms")
    if platforms and platform not in platforms:
        raise ValueError(f"This bundle is made for {', '.join(platforms)}, not for this computer ({platform})")
    settings = []
    for key, spec in (manifest.get("user_config") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        kind_ = {"boolean": "boolean", "directory": "folder", "file": "text", "number": "text"}.get(spec.get("type"), "text")
        if spec.get("sensitive"):
            kind_ = "secret"
        field = {"key": key, "label": spec.get("title") or key, "type": kind_, "required": bool(spec.get("required"))}
        if spec.get("description"):
            field["help"] = str(spec["description"])
        if spec.get("default") not in (None, "") and kind_ != "secret":
            field["default"] = spec["default"]
        settings.append(field)
    pid = re.sub(r"[^a-z0-9]+", "-", str(manifest["name"]).lower()).strip("-")[:40]
    if len(pid) < 2:
        raise ValueError("The bundle's name cannot be used as an id")
    author = manifest.get("author") or {}
    return {"id": pid, "name": manifest.get("display_name") or manifest["name"],
            "version": str(manifest.get("version") or "1.0.0"), "description": str(manifest.get("description") or ""),
            "author": author.get("name", "") if isinstance(author, dict) else str(author),
            "homepage": str(manifest.get("homepage") or ""), "settings": settings,
            "mcpServers": {pid: {"command": conf["command"], "args": list(conf.get("args") or []),
                                 "env": dict(conf.get("env") or {}), "description": str(manifest.get("description") or "")}},
            "bundle": {"manifest_version": str(manifest.get("manifest_version") or ""), "server_type": kind or ""}}


def fill_bundle_vars(value, folder, settings):
    """${__dirname}, ${user_config.KEY}, ${HOME}, ${DOCUMENTS}… in a bundle's command, arguments and environment."""
    home = Path.home()
    fixed = {"__dirname": str(folder), "HOME": str(home), "DESKTOP": str(home / "Desktop"),
             "DOCUMENTS": str(home / "Documents"), "DOWNLOADS": str(home / "Downloads"),
             "pathSeparator": os.sep, "/": os.sep}

    def one(m):
        name = m.group(1)
        if name.startswith("user_config."):
            got = (settings or {}).get(name[12:])
            return "" if got in (None, False) else ("true" if got is True else str(got))
        return fixed.get(name, m.group(0))
    return re.sub(r"\$\{(__dirname|HOME|DESKTOP|DOCUMENTS|DOWNLOADS|pathSeparator|/|user_config\.[A-Za-z0-9_.-]+)\}", one,
                  str(value))
