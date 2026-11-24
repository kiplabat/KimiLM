"""Sharing apps through kimios-registry (2.35.0), the way KimiOS shares drivers and controllers (D214, D216).

An entry is one JSON file, apps/<name>/app-<id>.json, holding the app's files as text and what it is:

    {"schema": 1, "kind": "app", "slug": "<name>", "app": {"kind": "game"|"device", "name", "about", "icon",
     "dims", "for": {"kind", "model", "bench"} (panels), "files": {"index.html": "...", ...}},
     "prompt", "model", "note", "kimilm", "date", "licence": "Apache-2.0"}

<id> comes from "app" itself, so the same app shared twice is the same file. Sharing never sends anything: it
writes the entry on this computer and gives a link to the registry's page for a new file, where the person reads
it and presses Propose changes. Installing from the registry checks the entry against the registry's index
(sha256), then installs it like any imported app (the same checks as a build).

The registry checks entries with this module (check_entry), so it accepts exactly what KimiLM would install.
Nothing here needs more than the standard library, so the registry's checker can import it."""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import re
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

REGISTRY = "kiplabat/kimios-registry"
WEB = f"https://github.com/{REGISTRY}"
RAW = f"https://raw.githubusercontent.com/{REGISTRY}/main"
MAX_ENTRY = 512 * 1024            # apps are larger than drivers (64 KB); a game is typically 10–40 KB
LINK_MAX = 7000
TEXT = (".html", ".htm", ".js", ".mjs", ".css", ".json", ".svg", ".txt", ".md", ".py", ".csv")
FIELDS = {"schema", "kind", "slug", "app", "prompt", "model", "note", "kimilm", "date", "licence"}
APP_FIELDS = {"kind", "name", "about", "icon", "dims", "for", "files"}
PATH = re.compile(r"apps/([a-z0-9-]+)/(app-[0-9a-f]{12})\.json")


class ShareError(Exception):
    pass


def source():
    """Where entries are read from: the registry on GitHub, or a folder (an offline copy, the tests)."""
    return os.environ.get("KIMIOS_REGISTRY_URL", RAW).rstrip("/")


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:60].strip("-")
    if not s:
        raise ShareError("the app has no name")
    return s


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_id(body):
    return f"app-{hashlib.sha256(canonical(body).encode()).hexdigest()[:12]}"


def entry_path(entry):
    return f"apps/{entry['slug']}/{entry_id(entry['app'])}.json"


def make_entry(manifest, folder, note="", version=""):
    """An entry for the version in `folder` of the app `manifest` (only text files can be shared)."""
    folder = Path(folder)
    files = {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        if rel.startswith(".") or "/." in rel:
            continue
        if not rel.lower().endswith(TEXT):
            raise ShareError(f"{rel} is not a text file; only apps made of text files (html, js, css, json, svg, "
                             "…) can be shared")
        files[rel] = p.read_text(encoding="utf-8")
    dev = manifest.get("device") or {}
    app = {"kind": manifest["kind"], "name": manifest["name"], "about": manifest.get("about") or "",
           "icon": manifest.get("icon"), "dims": manifest.get("dims"), "files": files}
    if manifest["kind"] == "device":
        app["for"] = {"kind": dev.get("kind"), "model": dev.get("model") or dev.get("name"), "bench": bool(dev.get("bench"))}
    cur = next((v for v in manifest.get("versions") or [] if v["n"] == manifest.get("current")), {})
    e = {"schema": 1, "kind": "app", "slug": slug(manifest["name"]), "app": app,
         "prompt": str(cur.get("prompt") or "")[:2000], "model": str(cur.get("model") or "")[:120],
         "note": str(note or cur.get("notes") or "")[:500], "kimilm": version,
         "date": datetime.date.today().isoformat(), "licence": "Apache-2.0"}
    if len(canonical(e).encode()) > MAX_ENTRY:
        raise ShareError(f"this app is too large to share (an entry is at most {MAX_ENTRY // 1024} KB)")
    return e


def prepare(entry, folder):
    """Write the entry for the person to look at and give the link that proposes it to the registry."""
    rel = entry_path(entry)
    rel_dir, name = rel.rsplit("/", 1)
    text = json.dumps(entry, indent=1, ensure_ascii=False) + "\n"
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    local = folder / name
    local.write_text(text, encoding="utf-8")
    link = f"{WEB}/new/main/{rel_dir}?" + urllib.parse.urlencode({"filename": name, "value": text})
    out = {"file": str(local), "path": rel, "registry": WEB}
    if len(link) <= LINK_MAX:
        out.update(link=link, upload=False)
    else:
        out.update(link=f"{WEB}/upload/main/{rel_dir}", upload=True)
    return out


def check_entry(rel, data, check_files=None):
    """What is wrong with an entry (a list of problems; empty when it may be installed). `check_files` is KimiLM's
    own check of an app's folder (dva.apps.check_files); internet addresses count as problems here."""
    errs = []
    if len(data) > MAX_ENTRY:
        return [f"{rel}: larger than {MAX_ENTRY // 1024} KB"]
    try:
        e = json.loads(data)
    except ValueError as exc:
        return [f"{rel}: not JSON ({exc})"]
    if not isinstance(e, dict) or e.get("kind") != "app" or not isinstance(e.get("app"), dict):
        return [f"{rel}: kind is app, with the app itself under app"]
    a = e["app"]
    if set(e) - FIELDS:
        errs.append(f"{rel}: unknown fields {sorted(set(e) - FIELDS)}")
    if set(a) - APP_FIELDS:
        errs.append(f"{rel}: unknown app fields {sorted(set(a) - APP_FIELDS)}")
    if a.get("kind") not in ("game", "device"):
        errs.append(f"{rel}: app.kind is game or device")
    try:
        want = f"apps/{slug(a.get('name'))}/{entry_id(a)}.json"
        if rel != want:
            errs.append(f"{rel}: belongs at {want}")
        if e.get("slug") != slug(a.get("name")):
            errs.append(f"{rel}: slug is the app's name in lower case with dashes")
    except ShareError as exc:
        errs.append(f"{rel}: {exc}")
    if e.get("licence") != "Apache-2.0":
        errs.append(f"{rel}: licence must be Apache-2.0 (the registry's licence)")
    if a.get("kind") == "device":
        f = a.get("for")
        if not isinstance(f, dict) or not f.get("kind"):
            errs.append(f"{rel}: a control panel says which kind of device it is for (app.for.kind)")
        elif set(f) - {"kind", "model", "bench"}:
            errs.append(f"{rel}: app.for keeps only kind, model and bench (nothing that names a machine)")
    files = a.get("files")
    if not isinstance(files, dict) or "index.html" not in files:
        return errs + [f"{rel}: app.files holds the app's text files, index.html among them"]
    with tempfile.TemporaryDirectory(prefix="kimi-app-check-") as tmp:
        for name, text in files.items():
            if (not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_][\w./-]*", name) or ".." in name
                    or not name.lower().endswith(TEXT)):
                errs.append(f"{rel}: {name!r} is not a text file with a plain name inside the app")
                continue
            p = Path(tmp) / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        if check_files and not errs:
            problems, warnings = check_files(tmp, a.get("kind"))
            errs += [f"{rel}: {x}" for x in problems]
            errs += [f"{rel}: {x}" for x in warnings if "internet" in x or "network" in x]
    return errs


def index_row(rel, data):
    e = json.loads(data)
    a = e["app"]
    return {"path": rel, "kind": "app", "app_kind": a["kind"], "name": a["name"], "slug": e["slug"],
            "about": a.get("about", ""), "icon": a.get("icon"), "dims": a.get("dims"), "for": a.get("for"),
            "note": e.get("note", ""), "model": e.get("model", ""), "date": e.get("date"), "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


# ---------------------------------------------------------------------------------------- reading the registry
async def _fetch(rel, limit=MAX_ENTRY * 8):
    base = source()
    if not base.startswith(("https://", "http://")):
        p = Path(base) / rel
        if not p.is_file():
            raise ShareError(f"{rel} is not in the registry copy at {base}")
        return p.read_bytes()
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "KimiLM"}) as c:
            r = await c.get(f"{base}/{urllib.parse.quote(rel)}")
    except httpx.HTTPError as exc:
        raise ShareError(f"the registry could not be reached ({exc}): is there a network?") from None
    if r.status_code == 404:
        raise ShareError(f"{rel} is not in the registry")
    if r.status_code != 200:
        raise ShareError(f"the registry answered {r.status_code} for {rel}")
    if len(r.content) > limit:
        raise ShareError(f"{rel} is too large")
    return r.content


async def index():
    try:
        rows = json.loads(await _fetch("index.json"))
    except ValueError:
        raise ShareError("the registry's index is not readable") from None
    rows = rows.get("entries", []) if isinstance(rows, dict) else []
    return [r for r in rows if r.get("kind") == "app"]


async def get(rel):
    """One entry, checked: its path, its sha256 in the index, and that it is the app its name says."""
    rel = str(rel or "").strip().lstrip("/")
    m = PATH.fullmatch(rel)
    if not m:
        raise ShareError("entry is a path from the registry's list, such as apps/<name>/app-<id>.json")
    data = await _fetch(rel)
    listed = next((r for r in await index() if r.get("path") == rel), None)
    if listed is None:
        raise ShareError(f"{rel} is not in the registry's index (not reviewed and merged yet)")
    if hashlib.sha256(data).hexdigest() != listed.get("sha256"):
        raise ShareError(f"{rel} does not match the registry's index: not installed")
    e = json.loads(data)
    if e.get("kind") != "app" or entry_id(e.get("app") or {}) != m.group(2):
        raise ShareError(f"{rel} is not the app its name says")
    return e


def as_kimiapp(entry):
    """The entry as the bytes of a .kimiapp file (so it installs like any imported app)."""
    a = entry["app"]
    meta = {"schema": 1, "format": "kimiapp", "name": a["name"], "kind": a["kind"], "about": a.get("about", ""),
            "icon": a.get("icon"), "dims": a.get("dims"), "prompt": entry.get("prompt", ""),
            "model": entry.get("model", ""), "notes": entry.get("note") or "From the registry",
            "device": {"kind": (a.get("for") or {}).get("kind"), "model": (a.get("for") or {}).get("model"),
                       "bench": bool((a.get("for") or {}).get("bench"))} if a["kind"] == "device" else None}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("app.json", json.dumps(meta, ensure_ascii=False))
        for name, text in a["files"].items():
            z.writestr("files/" + name, text)
    return buf.getvalue()
