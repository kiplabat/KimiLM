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
import socket
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


def listing_sync(timeout=2.0):
    """For the device watch (a thread): the bench's devices, or [] when there is no bench."""
    if not available():
        return []
    try:
        return _ask(envelope_socket(), {"op": "devices"}, timeout).get("devices") or []
    except (BenchError, OSError, ValueError):
        return []


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
