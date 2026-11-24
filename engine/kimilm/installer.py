"""Programs agents install for themselves (2.29.0).

An agent that needs a program it does not have (a browser for the browser tools, a command-line tool, a Python or
Node package, an app) installs it, as a person at the computer would, instead of reporting that it is missing.
How depends on what kind of program it is and on the computer:

  python    a Python package: into KimiLM's own tools folder (pip --target), on run_python's import path
  node      a Node package with its commands: into KimiLM's tools folder (npm --prefix); its commands are on the
            PATH of run_command
  browser   the browser the browser tools drive (Playwright's Chrome for Testing), into the user's cache
  app       a desktop app: Flatpak from Flathub, for this user, on Linux; winget on Windows; a Homebrew cask on macOS
  command   a command-line program. On KimiOS and other image-based systems (Silverblue, bootc, ostree) the system
            itself is read-only, so it goes into a toolbox container of KimiLM's own ("kimilm-tools", where
            installing needs no password) and a small launcher in KimiLM's tools folder runs it from there. Elsewhere:
            the system's package manager when it needs no password (sudo -n), winget on Windows, Homebrew on macOS.

Nothing here needs root on KimiOS. When the only way left needs the user's password, the error says the exact
command to run, and it reads as needing the user (recovery.PERMISSION), so a planner asks instead of retrying.
Every install is written down (tools/installed.json), shown in Settings → Safety, and can be removed there."""
import asyncio
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

TOOLBOX = "kimilm-tools"
NAME = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9@._+/:-]{0,120}$")
BROWSERS = {"chrome-for-testing", "chromium", "chrome", "browser", "playwright", "playwright-browser", "msedge",
            "web-browser", "headless-chrome"}
# what a command is called in each package manager, where it differs from the command's own name
PACKAGES = {
    "adb": {"dnf": "android-tools", "apt": "adb", "pacman": "android-tools", "brew": "android-platform-tools",
            "winget": "Google.PlatformTools"},
    "fastboot": {"dnf": "android-tools", "apt": "fastboot", "pacman": "android-tools",
                 "brew": "android-platform-tools", "winget": "Google.PlatformTools"},
    "ideviceinfo": {"dnf": "libimobiledevice-utils", "apt": "libimobiledevice-utils", "brew": "libimobiledevice"},
    "idevice_id": {"dnf": "libimobiledevice-utils", "apt": "libimobiledevice-utils", "brew": "libimobiledevice"},
    "ddcutil": {"dnf": "ddcutil", "apt": "ddcutil", "pacman": "ddcutil"},
    "bluetoothctl": {"dnf": "bluez", "apt": "bluez", "pacman": "bluez-utils"},
    "wpctl": {"dnf": "wireplumber", "apt": "wireplumber", "pacman": "wireplumber"},
    "pactl": {"dnf": "pulseaudio-utils", "apt": "pulseaudio-utils", "pacman": "libpulse"},
    "picotool": {"dnf": "picotool", "apt": "picotool", "brew": "picotool"},
    "ffmpeg": {"dnf": "ffmpeg-free", "apt": "ffmpeg", "brew": "ffmpeg", "winget": "Gyan.FFmpeg"},
    "git": {"winget": "Git.Git"},
    "node": {"dnf": "nodejs", "apt": "nodejs", "brew": "node", "winget": "OpenJS.NodeJS.LTS"},
    "npm": {"dnf": "nodejs-npm", "apt": "npm", "brew": "node", "winget": "OpenJS.NodeJS.LTS"},
    "npx": {"dnf": "nodejs-npm", "apt": "npm", "brew": "node", "winget": "OpenJS.NodeJS.LTS"},
    "sqlite3": {"dnf": "sqlite", "apt": "sqlite3", "winget": "SQLite.SQLite"},
    "dig": {"dnf": "bind-utils", "apt": "dnsutils", "brew": "bind"},
    "nslookup": {"dnf": "bind-utils", "apt": "dnsutils", "brew": "bind"},
    "7z": {"dnf": "p7zip-plugins", "apt": "p7zip-full", "brew": "p7zip", "winget": "7zip.7zip"},
    "tesseract": {"dnf": "tesseract", "apt": "tesseract-ocr", "winget": "UB-Mannheim.TesseractOCR"},
    "testssl": {"dnf": "testssl", "apt": "testssl.sh", "brew": "testssl"},
    "nmap": {"winget": "Insecure.Nmap"},
    "jq": {"winget": "jqlang.jq"},
    "pandoc": {"winget": "JohnMacFarlane.Pandoc"},
    "python3": {"winget": "Python.Python.3.12", "brew": "python"},
    "screen": {}, "minicom": {}, "picocom": {},
}


class InstallError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------- where things go
def tools_dir(app):
    folder = app.paths.data / "tools"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def python_dir(app):
    return tools_dir(app) / "python"


def bin_dirs(app):
    """Folders with commands agents installed: launchers (toolbox), Node packages' commands."""
    base = tools_dir(app)
    node_bin = base / "node" / ("" if os.name == "nt" else "bin")
    return [base / "bin", node_bin]


def with_path(app, env):
    """`env` with the installed commands first on its PATH (run_command, run_python)."""
    extra = [str(d) for d in bin_dirs(app) if d.is_dir()]
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH") or os.environ.get("PATH", "")])
    return env


def which(app, name):
    path = with_path(app, {"PATH": os.environ.get("PATH", "")})["PATH"]
    return shutil.which(name, path=path)


# ---------------------------------------------------------------------------------------------- the computer
def computer():
    """What this computer offers for installing (cheap: names on the PATH and a few files)."""
    if os.name == "nt":
        return {"os": "windows", "winget": bool(shutil.which("winget")), "managers": []}
    if sys.platform == "darwin":
        return {"os": "macos", "brew": bool(shutil.which("brew")), "managers": []}
    managers = [m for m, cmd in (("dnf", "dnf"), ("apt", "apt-get"), ("pacman", "pacman"), ("zypper", "zypper"))
                if shutil.which(cmd)]
    return {"os": "linux", "immutable": Path("/run/ostree-booted").exists(), "toolbox": bool(shutil.which("toolbox")),
            "flatpak": bool(shutil.which("flatpak")), "brew": bool(shutil.which("brew")), "managers": managers,
            "kimios": Path("/usr/share/kimilm/plugins/kimios").exists()}


def kind_of(name, kind="auto"):
    low = (name or "").lower().strip()
    if kind and kind != "auto":
        return kind
    if low in BROWSERS:
        return "browser"
    if low.startswith(("pip:", "python:")):
        return "python"
    if low.startswith(("npm:", "node:")):
        return "node"
    if re.fullmatch(r"[a-z0-9-]+(\.[A-Za-z0-9_-]+){2,}", name or "") and "/" not in name:
        return "app"                             # a Flathub id (org.gimp.GIMP) or a winget id (Git.Git)
    return "command"


def bare(name):
    return re.sub(r"^(pip|python|npm|node):", "", (name or "").strip(), flags=re.I)


def package_for(name, manager):
    return (PACKAGES.get(name.lower()) or {}).get(manager) or name


# ---------------------------------------------------------------------------------------------- the record
def _record_file(app):
    return tools_dir(app) / "installed.json"


def installed(app):
    try:
        rows = json.loads(_record_file(app).read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def record(app, name, kind, how="", package="", path="", by="", launchers=()):
    rows = [r for r in installed(app) if not (r.get("name") == name and r.get("kind") == kind)]
    rows.append({"name": name, "kind": kind, "how": how, "package": package or name, "path": path or "",
                 "by": by or "", "when": time.strftime("%Y-%m-%d %H:%M"), "launchers": list(launchers)})
    _record_file(app).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    try:
        app.bus.publish("programs_changed")
    except Exception:
        pass
    return rows[-1]


def forget(app, name):
    rows = installed(app)
    keep = [r for r in rows if r.get("name") != name]
    _record_file(app).write_text(json.dumps(keep, indent=1), encoding="utf-8")
    return [r for r in rows if r.get("name") == name]


# ---------------------------------------------------------------------------------------------- running
async def _run(app, argv, timeout=1800, on_status=None, owner=None, env=None):
    env = with_path(app, {**os.environ, **(env or {})})
    env.pop("KIMILM_TOKEN", None)
    env.setdefault("DEBIAN_FRONTEND", "noninteractive")
    last = [0.0]

    def line(text):
        if text and on_status and time.monotonic() - last[0] > 3:     # what it is doing, now and then
            tail = [t for t in str(text).strip().splitlines() if t.strip()]
            if tail:
                last[0] = time.monotonic()
                on_status(tail[-1][:160])
    try:
        return await app.procs.run(argv, cwd=tools_dir(app), env=env, timeout=timeout, kind="install",
                                   label=" ".join(str(a) for a in argv)[:200], owner=owner, on_line=line, queue=False)
    except asyncio.TimeoutError:
        raise InstallError(f"Installing took longer than {timeout // 60} minutes and was stopped") from None
    except FileNotFoundError as exc:
        raise InstallError(f"Could not start {argv[0]}: {exc}") from None


async def _toolbox_ready(app, on_status, owner):
    code, out = await _run(app, ["toolbox", "list", "--containers"], timeout=60, owner=owner)
    if code == 0 and re.search(rf"\b{TOOLBOX}\b", out):
        return
    if on_status:
        on_status(f"Making KimiLM's toolbox ({TOOLBOX}) for command-line programs; the first time downloads "
                  "a small Fedora system…")
    code, out = await _run(app, ["toolbox", "create", "-y", TOOLBOX], timeout=1800, on_status=on_status, owner=owner)
    if code != 0:
        raise InstallError(f"The toolbox could not be made: {out.strip()[-300:]}")


def _launcher(app, command):
    """A command in KimiLM's toolbox, run from the host as if it were installed there."""
    folder = tools_dir(app) / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(command).name
    path.write_text(f'#!/bin/sh\nexec toolbox run -c {TOOLBOX} {command} "$@"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


async def _install_command(app, name, package, on_status, owner):
    pc = computer()
    if pc["os"] == "windows":
        if not pc["winget"]:
            raise InstallError("winget is not available on this Windows computer: install 'App Installer' from the "
                               "Microsoft Store, or install the program yourself")
        pkg = package or package_for(name, "winget")
        code, out = await _run(app, ["winget", "install", "--exact", "--id", pkg, "--silent", "--disable-interactivity",
                                     "--accept-package-agreements", "--accept-source-agreements"],
                               on_status=on_status, owner=owner)
        if code != 0:          # not an id: let winget find it by name
            code, out = await _run(app, ["winget", "install", pkg, "--silent", "--disable-interactivity",
                                         "--accept-package-agreements", "--accept-source-agreements"],
                                   on_status=on_status, owner=owner)
        if code != 0:
            raise InstallError(f"winget could not install {pkg}: {out.strip()[-300:]}")
        return f"winget ({pkg})", pkg
    if pc["os"] == "macos":
        if not pc["brew"]:
            raise InstallError("Homebrew is not installed; it needs your password once. Install it from "
                               "https://brew.sh, then ask again")
        pkg = package or package_for(name, "brew")
        code, out = await _run(app, ["brew", "install", pkg], on_status=on_status, owner=owner,
                               env={"HOMEBREW_NO_AUTO_UPDATE": "1", "NONINTERACTIVE": "1"})
        if code != 0:
            raise InstallError(f"brew could not install {pkg}: {out.strip()[-300:]}")
        return f"Homebrew ({pkg})", pkg
    # Linux
    if pc["immutable"] or (pc["toolbox"] and not pc["managers"]):
        if not pc["toolbox"]:
            raise InstallError("This system is read-only (image-based) and has no toolbox to install programs into")
        pkg = package or package_for(name, "dnf")
        await _toolbox_ready(app, on_status, owner)
        if on_status:
            on_status(f"Installing {pkg} in KimiLM's toolbox…")
        code, out = await _run(app, ["toolbox", "run", "-c", TOOLBOX, "sudo", "dnf", "install", "-y", pkg],
                               on_status=on_status, owner=owner)
        if code != 0:
            raise InstallError(f"{pkg} could not be installed in the toolbox: {out.strip()[-300:]}")
        code, out = await _run(app, ["toolbox", "run", "-c", TOOLBOX, "rpm", "-ql", pkg], timeout=120, owner=owner)
        commands = sorted({p for p in out.split() if re.match(r"^/usr/s?bin/[^/]+$", p)})
        if f"/usr/bin/{name}" not in commands and f"/usr/sbin/{name}" not in commands:
            commands.append(f"/usr/bin/{name}")
        # a launcher for each command the package brings, unless the computer has that command itself
        made = [str(_launcher(app, c)) for c in commands[:40]
                if Path(c).name == name or not shutil.which(Path(c).name)]
        return f"toolbox {TOOLBOX} (dnf {pkg})", pkg, made
    if pc["brew"]:
        pkg = package or package_for(name, "brew")
        code, out = await _run(app, ["brew", "install", pkg], on_status=on_status, owner=owner,
                               env={"HOMEBREW_NO_AUTO_UPDATE": "1", "NONINTERACTIVE": "1"})
        if code == 0:
            return f"Homebrew ({pkg})", pkg
    manager = (pc["managers"] or [None])[0]
    if not manager:
        raise InstallError("No package manager was found on this computer")
    pkg = package or package_for(name, manager)
    argv = {"dnf": ["dnf", "install", "-y", pkg], "apt": ["apt-get", "install", "-y", pkg],
            "pacman": ["pacman", "-S", "--noconfirm", pkg], "zypper": ["zypper", "--non-interactive", "install", pkg]}[manager]
    code, _ = await _run(app, ["sudo", "-n", "true"], timeout=20, owner=owner)
    if code != 0:
        raise InstallError(f"Installing {pkg} needs your password, which an agent does not have. Run this once in "
                           f"a terminal: sudo {' '.join(argv)}")
    if manager == "apt":
        await _run(app, ["sudo", "-n", "apt-get", "update"], timeout=600, on_status=on_status, owner=owner)
    code, out = await _run(app, ["sudo", "-n", *argv], on_status=on_status, owner=owner)
    if code != 0:
        raise InstallError(f"{manager} could not install {pkg}: {out.strip()[-300:]}")
    return f"{manager} ({pkg})", pkg


async def _install_app(app, name, package, on_status, owner):
    pc = computer()
    if pc["os"] == "linux":
        if not pc["flatpak"]:
            return await _install_command(app, name, package, on_status, owner)
        await _run(app, ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
                         "https://dl.flathub.org/repo/flathub.flatpakrepo"], timeout=300, owner=owner)
        pkg = package or name
        code, out = await _run(app, ["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", pkg],
                               on_status=on_status, owner=owner)
        if code != 0:
            raise InstallError(f"Flatpak could not install {pkg}: {out.strip()[-300:]} (give the app's Flathub id, "
                               "such as org.gimp.GIMP)")
        return f"Flatpak, Flathub ({pkg})", pkg
    if pc["os"] == "macos" and pc["brew"]:
        pkg = package or name
        code, out = await _run(app, ["brew", "install", "--cask", pkg], on_status=on_status, owner=owner,
                               env={"HOMEBREW_NO_AUTO_UPDATE": "1", "NONINTERACTIVE": "1"})
        if code != 0:
            raise InstallError(f"brew could not install the app {pkg}: {out.strip()[-300:]}")
        return f"Homebrew cask ({pkg})", pkg
    return await _install_command(app, name, package, on_status, owner)


async def _install_python(app, name, package, on_status, owner):
    pkg = package or bare(name)
    target = python_dir(app)
    target.mkdir(parents=True, exist_ok=True)
    code, out = await _run(app, [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                                 "--no-input", "--upgrade", "--target", str(target), pkg],
                           on_status=on_status, owner=owner)
    if code != 0:
        raise InstallError(f"pip could not install {pkg}: {out.strip()[-300:]}")
    return "pip (KimiLM's tools folder)", pkg


async def _install_node(app, name, package, on_status, owner):
    pkg = package or bare(name)
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm") or which(app, "npm")
    if not npm:
        await install(app, "npm", "command", on_status=on_status, owner=owner)
        npm = which(app, "npm")
    if not npm:
        raise InstallError("npm is needed for Node packages and could not be installed")
    prefix = tools_dir(app) / "node"
    code, out = await _run(app, [npm, "install", "--global", "--prefix", str(prefix), "--no-fund", "--no-audit", pkg],
                           on_status=on_status, owner=owner)
    if code != 0:
        raise InstallError(f"npm could not install {pkg}: {out.strip()[-300:]}")
    return "npm (KimiLM's tools folder)", pkg


# ---------------------------------------------------------------------------------------------- the two calls
async def version_of(app, path):
    for flag in ("--version", "-V", "version"):
        try:
            proc = await asyncio.create_subprocess_exec(path, flag, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.STDOUT,
                                                        stdin=asyncio.subprocess.DEVNULL,
                                                        env=with_path(app, {**os.environ}))
            out, _ = await asyncio.wait_for(proc.communicate(), 8)
        except (OSError, asyncio.TimeoutError):
            continue
        first = next((ln.strip() for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()), "")
        if proc.returncode == 0 and first:
            return first[:120]
    return ""


def python_has(app, module):
    import importlib.util
    target = python_dir(app)
    if (target / module).exists() or any(target.glob(f"{module}*.dist-info")):
        return str(target)
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        spec = None
    return (spec.origin or "built in") if spec else None


async def find(app, name, kind="auto"):
    """Is the program there? {name, kind, found, version, how} — `how` says how it would be installed."""
    kind = kind_of(name, kind)
    plain = bare(name)
    out = {"name": plain, "kind": kind, "found": None, "version": ""}
    if kind == "python":
        out["found"] = python_has(app, plain.replace("-", "_").split("[")[0])
    elif kind == "browser":
        from .services import system_browser
        out["found"] = system_browser() or next((r.get("how") for r in installed(app) if r.get("kind") == "browser"),
                                                None)
    elif kind == "app" and shutil.which("flatpak"):
        proc = await asyncio.create_subprocess_exec("flatpak", "info", "--user", plain, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate()
        out["found"] = plain if proc.returncode == 0 else None
    else:
        path = which(app, plain)
        out["found"] = path
        if path:
            out["version"] = await version_of(app, path)
    out["how"] = how_it_would_install(kind, plain)
    return out


def how_it_would_install(kind, name):
    pc = computer()
    if kind == "python":
        return "pip into KimiLM's tools folder"
    if kind == "node":
        return "npm into KimiLM's tools folder"
    if kind == "browser":
        return "Playwright's Chrome for Testing, into this user's cache"
    if pc["os"] == "windows":
        return f"winget ({package_for(name, 'winget')})" if pc.get("winget") else "not possible here (no winget)"
    if pc["os"] == "macos":
        return f"Homebrew ({package_for(name, 'brew')})" if pc.get("brew") else "needs Homebrew first"
    if kind == "app" and pc.get("flatpak"):
        return "Flatpak from Flathub, for this user"
    if pc.get("immutable") or (pc.get("toolbox") and not pc.get("managers")):
        return f"KimiLM's toolbox (dnf {package_for(name, 'dnf')})" if pc.get("toolbox") else "not possible here"
    manager = (pc.get("managers") or [None])[0]
    return f"{manager} ({package_for(name, manager)}), needs sudo without a password" if manager else "not possible here"


async def install(app, name, kind="auto", package="", on_status=None, owner=None, by=""):
    """Install a program. Returns {name, kind, how, package, found, version}. Raises InstallError."""
    if not NAME.match(str(name or "")) or (package and not NAME.match(str(package))):
        raise InstallError("Give the program's plain name (adb, ffmpeg, requests, org.gimp.GIMP), without spaces "
                           "or shell characters")
    kind = kind_of(name, kind)
    plain = bare(name)
    if kind == "browser":
        target = package or ("chromium" if plain in BROWSERS else plain)
        await app.services.install_browser(target, on_status)
        return {"name": target, "kind": kind, "how": "Playwright (install-browser)", "package": target,
                "found": target, "version": ""}
    before = await find(app, plain, kind)
    if before["found"] and not package:
        return {**before, "how": "already installed", "package": plain, "already": True}
    if on_status:
        on_status(f"Installing {plain} ({before['how']})…")
    do = {"python": _install_python, "node": _install_node, "app": _install_app}.get(kind, _install_command)
    done = await do(app, plain, package, on_status, owner)
    how, pkg, launchers = (tuple(done) + ([],))[:3]
    after = await find(app, plain, kind)
    record(app, plain, kind, how=how, package=pkg, path=str(after.get("found") or ""), by=by, launchers=launchers)
    if not after["found"] and kind == "command":
        raise InstallError(f"{pkg} was installed with {how}, but there is still no command called {plain}. Look "
                           f"for the command it provides (install_program with check_only), or name the right package")
    return {**after, "how": how, "package": pkg}


async def remove(app, name):
    """Undo an install KimiLM made (the record says how)."""
    rows = [r for r in installed(app) if r.get("name") == name]
    if not rows:
        raise InstallError(f"KimiLM did not install {name}")
    for r in rows:
        kind, pkg, how = r.get("kind"), r.get("package") or name, r.get("how") or ""
        if kind == "python":
            for p in list(python_dir(app).glob(f"{pkg.replace('-', '_')}*")) + list(python_dir(app).glob(f"{pkg}*")):
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        elif kind == "node":
            npm = shutil.which("npm") or which(app, "npm")
            if npm:
                await _run(app, [npm, "uninstall", "--global", "--prefix", str(tools_dir(app) / "node"), pkg], 600)
        elif how.startswith("toolbox"):
            await _run(app, ["toolbox", "run", "-c", TOOLBOX, "sudo", "dnf", "remove", "-y", pkg], 600)
            for launcher in r.get("launchers") or []:
                Path(launcher).unlink(missing_ok=True)
        elif how.startswith("Flatpak"):
            await _run(app, ["flatpak", "uninstall", "--user", "-y", "--noninteractive", pkg], 600)
        elif how.startswith("winget"):
            await _run(app, ["winget", "uninstall", "--exact", "--id", pkg, "--silent"], 900)
        elif how.startswith("Homebrew"):
            await _run(app, ["brew", "uninstall", pkg], 600)
        # system packages (sudo) and browsers are left: other programs may use them now
    forget(app, name)
    app.bus.publish("programs_changed")
    return rows
