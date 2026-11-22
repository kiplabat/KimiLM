"""KimiOS's device bench and envelope, as KimiLM sees them (2.30.0).

On KimiOS (0.9.0 and later) devices that move, heat, cool or switch something are run by `kimi-envelope`, the guard
that applies each device's datasheet whatever is asked; simulated devices (a fridge, a heated room, an air sensor)
come from `kimi-simbench`. Agents work with them through KimiOS's tools (bench, driver, bench_experiment,
controller, bench_event). This module is what KimiLM's own window needs: the list, a device's state and history
for the live chart, scenario events, and stopping a controller. Elsewhere (Windows, macOS, Linux without KimiOS)
`available()` is False and nothing here is used."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path


class BenchError(RuntimeError):
    pass


def envelope_socket():
    return Path(os.environ.get("KIMIOS_ENVELOPE_SOCKET", "/run/kimios-envelope/envelope.sock"))


def sim_socket():
    return Path(os.environ.get("KIMIOS_SIM_DIR", "/run/kimios-sim")) / "control.sock"


def available():
    return os.name != "nt" and envelope_socket().exists()


def _ask(path, req, timeout):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(str(path))
        except OSError as exc:
            raise BenchError(f"KimiOS's device service is not running ({exc})") from None
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
    try:
        out = json.loads(buf or b"{}")
    except ValueError:
        raise BenchError("KimiOS's device service gave an answer that could not be read") from None
    if not out.get("ok"):
        raise BenchError(out.get("error") or "refused")
    return out


async def envelope(req, timeout=15.0):
    return await asyncio.to_thread(_ask, envelope_socket(), req, timeout)


async def simulator(req, timeout=10.0):
    return await asyncio.to_thread(_ask, sim_socket(), req, timeout)


_last = {"rows": [], "at": 0.0}


def listing_sync(timeout=5.0):
    """For the device watch (a thread): the bench's devices, or [] when there is no bench. 2.32.0: a slow answer
    (the envelope busy with an experiment) keeps the last list for a minute instead of making the devices vanish."""
    if not available():
        return []
    try:
        rows = _ask(envelope_socket(), {"op": "devices"}, timeout).get("devices") or []
    except (BenchError, OSError, ValueError):
        return _last["rows"] if time.monotonic() - _last["at"] < 60 else []
    _last.update(rows=rows, at=time.monotonic())
    return rows


UNITS = ("kimios-envelope-setup", "kimi-simbench", "kimi-envelope")
FIX = "sudo systemctl restart kimios-envelope-setup kimi-simbench kimi-envelope"


def on_kimios():
    if os.name == "nt":
        return False
    if Path(os.environ.get("KIMIOS_LIBEXEC", "/usr/libexec/kimios")).is_dir():
        return True
    try:
        return "kimios" in Path("/etc/os-release").read_text().lower()
    except OSError:
        return False


def _run(cmd, timeout=4):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def services():
    """Each of KimiOS's device services and whether it runs ('active', 'failed', 'inactive', '' unknown)."""
    if not shutil.which("systemctl"):
        return {}
    states = _run(["systemctl", "is-active", *UNITS]).splitlines()
    return {u: (states[i].strip() if i < len(states) else "") for i, u in enumerate(UNITS)}


def journal(units, lines=8):
    """The last lines the services wrote (needs the journal to be readable by this user; else [])."""
    if not shutil.which("journalctl"):
        return []
    cmd = ["journalctl", "--no-pager", "-o", "short", "-n", str(lines)]
    for u in units:
        cmd += ["-u", f"{u}.service"]
    return [ln for ln in _run(cmd).splitlines() if ln and not ln.startswith("-- ")][-lines:]


def status_sync(timeout=5.0):
    """2.32.0: the bench with the reason it is missing, so Devices never just shows nothing. Returns
    {"kimios", "available", "devices", "problems", "reason", "services", "journal", "fix"}."""
    out = {"kimios": on_kimios(), "available": False, "devices": [], "problems": [], "reason": "",
           "services": {}, "journal": [], "fix": ""}
    if os.name == "nt" or (not out["kimios"] and not envelope_socket().exists()):
        return out                                    # not KimiOS: there is no bench, and nothing is wrong
    if not envelope_socket().exists():
        out["reason"] = "KimiOS's device service (kimi-envelope) is not running, so the simulated devices cannot be shown."
    else:
        try:
            got = _ask(envelope_socket(), {"op": "devices"}, timeout)
            out.update(available=True, devices=got.get("devices") or [], problems=list(got.get("problems") or []))
            out["problems"] += [f"{name}: {'; '.join(errs)}" for name, errs in (got.get("invalid") or {}).items()]
            _last.update(rows=out["devices"], at=time.monotonic())
        except (BenchError, OSError, ValueError) as exc:
            out["reason"] = f"KimiOS's device service did not answer ({exc})."
    if out["reason"] or out["problems"]:
        out["services"] = services()
        bad = [u for u, st in out["services"].items() if st and st != "active"]
        out["journal"] = journal(bad or UNITS[1:])
        out["fix"] = FIX
    return out


def as_device(row):
    """A bench device as one of KimiLM's Devices."""
    sim = bool(row.get("simulated"))
    driver = row.get("driver")
    state = ("no driver yet" if driver is None else f"driver v{driver} · {row.get('mode')}")
    return {"id": f"bench:{row['id']}", "bench_id": row["id"], "kind": row.get("kind") or "appliance",
            "name": row.get("model") or row["id"], "connection": "simulated (serial)" if sim else "serial",
            "status": "connected", "what": ("Simulated " if sim else "") + "device run by KimiOS's guard",
            "state": state, "bench": True, "simulated": sim, "drivers": [f"manifest v{driver}"] if driver else [],
            "key": f"bench:{row['id']}"}
