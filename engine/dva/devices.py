"""Devices attached to this computer (2.29.0): what is plugged in, what drives it, what can be done with it.

Plug in a phone, a Raspberry Pi, a sensor, a display, a keyboard-and-mouse dongle or connect a Bluetooth headset,
and KimiLM sees it, says what it is and offers what it can do. Agents reach the same through two tools
(tools/devices.py): `devices` (what is attached, everything about one device, or with an action: do one of the
things a device offers).

What a device is (its kind) comes from what it says about itself: USB classes and interfaces (an ADB interface
makes an Android phone, HID keyboard and mouse interfaces make a dongle, a CDC serial interface a board or sensor),
well-known vendor ids (Raspberry Pi, Arduino, Espressif, the USB-serial chips boards use, phone makers), display
connectors and their EDID, the input devices the kernel made, BlueZ for Bluetooth, the sound server for audio.

For each device: its kernel driver(s), the kernel modules that could drive it when none does, the programs that
control it (adb for a phone, ddcutil for an external display's brightness, bluetoothctl, pactl, picotool) and
whether they are installed, whether this user may open it, and the actions. A program an action needs is
installed when it is first needed (installer.py), unless installing is switched off.

Linux is read from /sys and /proc (no root), with KimiOS's capability graph (/var/lib/kimios/hardware.json) added
when present. Windows: Plug and Play devices (PowerShell). macOS: system_profiler. Phones (adb) and serial ports
work everywhere.

Safety (KimiOS principle 5): writing to a serial port or a board can move, heat or switch something, so those
actions ask every time, even with auto-approve, unless the user marked that device as safe to control (Devices).
Nothing here writes to a disk or to firmware except copying a .uf2 file to a board's own boot drive when asked."""
import asyncio
import json
import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path

from . import installer

ROOT = Path(os.environ.get("DVA_DEVICES_ROOT") or "/")    # another root only for tests and demos
USB_CLASS = {0x01: "audio", 0x02: "communications", 0x03: "hid", 0x05: "physical", 0x06: "image", 0x07: "printer",
             0x08: "storage", 0x09: "hub", 0x0a: "cdc-data", 0x0b: "smart-card", 0x0d: "security", 0x0e: "video",
             0x0f: "health", 0x10: "audio-video", 0xe0: "wireless", 0xef: "misc", 0xfe: "application",
             0xff: "vendor"}
PHONE_VENDORS = {"18d1": "Google", "04e8": "Samsung", "22b8": "Motorola", "2717": "Xiaomi", "12d1": "Huawei",
                 "2a70": "OnePlus", "0bb4": "HTC", "1004": "LG", "0fce": "Sony", "2d95": "vivo", "22d9": "OPPO",
                 "2ae5": "Fairphone", "1949": "Amazon", "05c6": "Qualcomm", "0e8d": "MediaTek", "29a9": "Tecno",
                 "1782": "Spreadtrum", "2970": "Wiko", "1ebf": "Coolpad", "19d2": "ZTE", "17ef": "Lenovo"}
BOARD_VENDORS = {"2e8a": "Raspberry Pi", "2341": "Arduino", "2a03": "Arduino", "303a": "Espressif", "1366": "SEGGER",
                 "239a": "Adafruit", "1b4f": "SparkFun", "16c0": "Teensy", "0d28": "micro:bit (Arm)"}
SERIAL_CHIPS = {("10c4", "ea60"): "CP210x USB-serial", ("1a86", "7523"): "CH340 USB-serial",
                ("1a86", "55d4"): "CH9102 USB-serial", ("0403", "6001"): "FTDI USB-serial",
                ("0403", "6015"): "FTDI USB-serial", ("067b", "2303"): "PL2303 USB-serial"}
PI_GADGETS = {("0525", "a4a2"): "Raspberry Pi (USB network gadget)", ("1d6b", "0104"): "Raspberry Pi (USB gadget)",
              ("2e8a", "0003"): "Raspberry Pi Pico in boot mode (RPI-RP2 drive)",
              ("2e8a", "000f"): "Raspberry Pi Pico 2 in boot mode (RP2350 drive)",
              ("2e8a", "0005"): "Raspberry Pi Pico (MicroPython)", ("2e8a", "000a"): "Raspberry Pi Pico (serial)"}
SENSOR_WORDS = re.compile(r"sensor|thermo|temp|humid|co2|weather|meter|gauge|lux|light sensor|accelero|gyro|"
                          r"barometer|pressure|air quality|particulate|geiger|radiation|scale", re.I)
PHONE_WORDS = re.compile(r"android|pixel|galaxy|iphone|ipad|phone|redmi|oneplus|huawei|xperia|moto ", re.I)
KINDS = ("appliance", "heater", "phone", "board", "sensor", "display", "keyboard", "mouse", "input", "audio", "bluetooth", "camera",
         "storage", "serial", "printer", "network", "other")


class DeviceError(ValueError):
    pass


def read(path, default=None):
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return default


def hexint(v):
    try:
        return int(str(v), 16)
    except (TypeError, ValueError):
        return None


async def sh(argv, timeout=20, app=None, input_bytes=None, binary=False):
    """(exit code, output) of a short program; (127, why) when it is not there."""
    env = installer.with_path(app, {**os.environ}) if app is not None else {**os.environ}
    env.pop("DVA_TOKEN", None)
    exe = (installer.which(app, argv[0]) if app is not None else None) or shutil.which(argv[0], path=env.get("PATH"))
    if not exe:
        return 127, f"{argv[0]} is not installed"
    try:
        proc = await asyncio.create_subprocess_exec(exe, *argv[1:], stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT,
                                                    stdin=asyncio.subprocess.PIPE if input_bytes else
                                                    asyncio.subprocess.DEVNULL, env=env,
                                                    creationflags=0x08000000 if os.name == "nt" else 0)
        out, _ = await asyncio.wait_for(proc.communicate(input_bytes), timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return 124, f"{argv[0]} did not finish within {timeout} s"
    except OSError as exc:
        return 126, str(exc)
    return proc.returncode, (out if binary else out.decode("utf-8", "replace"))


# ================================================================================================ Linux: /sys
def edid_info(raw):
    """Maker (three letters) and the monitor's own name from an EDID block."""
    if not raw or len(raw) < 128 or raw[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return {}
    word = (raw[8] << 8) | raw[9]
    maker = "".join(chr(((word >> s) & 0x1f) + 64) for s in (10, 5, 0))
    name = ""
    for off in (54, 72, 90, 108):
        d = raw[off:off + 18]
        if d[:3] == b"\x00\x00\x00" and d[3] == 0xfc:
            name = d[5:18].split(b"\n")[0].decode("ascii", "replace").strip()
    size = (raw[21], raw[22])                       # cm
    out = {"maker": maker, "name": name}
    if all(size):
        out["size_inches"] = round(((size[0] ** 2 + size[1] ** 2) ** 0.5) / 2.54, 1)
    return out


CONNECTOR = re.compile(r"^card\d+-(.+)$")


def linux_displays(root=ROOT):
    out = []
    for conn in sorted((root / "sys/class/drm").glob("card*-*")):
        m = CONNECTOR.match(conn.name)
        if not m or not (conn / "status").exists():
            continue
        status = read(conn / "status", "")
        if status != "connected":
            continue
        port = m.group(1)
        modes = [x for x in (read(conn / "modes", "") or "").splitlines() if x]
        try:
            edid = edid_info((conn / "edid").read_bytes())
        except OSError:
            edid = {}
        internal = port.startswith(("eDP", "LVDS", "DSI"))
        kind_port = re.sub(r"-\d+$", "", port).replace("-A", "")
        name = edid.get("name") or ("Built-in screen" if internal else f"Display on {kind_port}")
        out.append({"id": f"display:{conn.name}", "kind": "display", "name": name, "connection": kind_port,
                    "what": "Built-in screen" if internal else "External display",
                    "internal": internal, "status": "connected", "port": conn.name,
                    "details": {**edid, "preferred_mode": modes[0] if modes else None, "modes": modes[:12],
                                "enabled": read(conn / "enabled")},
                    "drivers": [d for d in [_driver(conn.parent / conn.name.split("-")[0] / "device")] if d]})
    return out


def _driver(dev):
    try:
        return os.path.basename(os.readlink(Path(dev) / "driver"))
    except OSError:
        return None


def usb_kind(vid, pid, product, interfaces, manufacturer=""):
    """(kind, what it is in a few words) from what a USB device says about itself."""
    classes = {(i["class"], i.get("subclass"), i.get("protocol")) for i in interfaces}
    words = f"{manufacturer} {product}"
    if (vid, pid) in PI_GADGETS:
        return "board", PI_GADGETS[(vid, pid)]
    if any(c == 0xff and s == 0x42 and p == 0x01 for c, s, p in classes):
        return "phone", "Android phone or tablet (USB debugging on)"
    if vid == "05ac" and re.search(r"iphone|ipad|ipod", words, re.I):
        return "phone", "Apple iPhone or iPad"
    if vid in PHONE_VENDORS and (any(c == 0x06 for c, _s, _p in classes) or PHONE_WORDS.search(words)
                                 or any(c == 0xff for c, _s, _p in classes)):
        return "phone", f"{PHONE_VENDORS[vid]} phone or tablet (file transfer)"
    if PHONE_WORDS.search(words) and any(c in (0x06, 0xff) for c, _s, _p in classes):
        return "phone", "Phone or tablet"
    if vid in BOARD_VENDORS:
        return "board", f"{BOARD_VENDORS[vid]} board"
    if SENSOR_WORDS.search(words):
        return "sensor", "Sensor"
    if (vid, pid) in SERIAL_CHIPS:
        return "serial", f"{SERIAL_CHIPS[(vid, pid)]} (often a microcontroller board or a sensor)"
    hid = [p for c, _s, p in classes if c == 0x03]
    if 1 in hid and 2 in hid:
        return "keyboard", "Keyboard and mouse receiver (dongle)"
    if 1 in hid:
        return "keyboard", "Keyboard"
    if 2 in hid:
        return "mouse", "Mouse or touchpad"
    if any(c == 0xe0 for c, _s, _p in classes):
        return "bluetooth", "Bluetooth adapter"
    if any(c == 0x0e for c, _s, _p in classes):
        return "camera", "Camera"
    if any(c == 0x01 for c, _s, _p in classes):
        return "audio", "Audio device"
    if any(c == 0x08 for c, _s, _p in classes):
        return "storage", "Storage (drive or card reader)"
    if any(c == 0x07 for c, _s, _p in classes):
        return "printer", "Printer"
    if any(c == 0x02 and s == 0x02 for c, s, _p in classes):
        return "serial", "Serial device (CDC ACM)"
    if any(c == 0x02 for c, _s, _p in classes):
        return "network", "Network adapter"
    if hid:
        return "input", "Input device (HID)"
    return "other", "USB device"


def linux_usb(root=ROOT):
    out = []
    base = root / "sys/bus/usb/devices"
    for dev in sorted(base.glob("*")):
        if ":" in dev.name or dev.name.startswith("usb") or not (dev / "idVendor").exists():
            continue
        vid, pid = read(dev / "idVendor", ""), read(dev / "idProduct", "")
        interfaces = []
        for intf in sorted(base.glob(dev.name + ":*")):
            interfaces.append({"class": hexint(read(intf / "bInterfaceClass")),
                               "subclass": hexint(read(intf / "bInterfaceSubClass")),
                               "protocol": hexint(read(intf / "bInterfaceProtocol")),
                               "driver": _driver(intf), "modalias": read(intf / "modalias"),
                               "path": str(intf)})
        if any(i["class"] == 0x09 for i in interfaces):
            continue                                 # hubs carry other devices; they are not one themselves
        product, maker = read(dev / "product", "") or "", read(dev / "manufacturer", "") or ""
        kind, what = usb_kind(vid, pid, product, interfaces, maker)
        drivers = sorted({i["driver"] for i in interfaces if i["driver"]})
        name = " ".join(x for x in (maker if maker and maker.lower() not in product.lower() else "", product) if x)
        out.append({"id": f"usb:{dev.name}", "kind": kind, "name": name or what, "what": what, "connection": "USB",
                    "status": "connected", "key": f"{vid}:{pid}:{read(dev / 'serial', '') or dev.name}",
                    "vendor_id": vid, "product_id": pid, "serial": read(dev / "serial"),
                    "speed_mbps": read(dev / "speed"), "busnum": read(dev / "busnum"), "devnum": read(dev / "devnum"),
                    "drivers": drivers, "interfaces": [{k: v for k, v in i.items() if k != "path"} for i in interfaces],
                    "sysfs": str(dev)})
    return out


def linux_serial(root=ROOT):
    """Serial ports with the USB device each belongs to."""
    out = []
    for tty in sorted((root / "sys/class/tty").glob("tty*")):
        if not tty.name.startswith(("ttyUSB", "ttyACM", "ttyAMA", "ttyS")) or not (tty / "device").exists():
            continue
        try:
            real = (tty / "device").resolve()
        except OSError:
            continue
        if tty.name.startswith("ttyS") and _driver(tty / "device") in (None, "serial8250") \
                and read(tty / "type") in (None, "0"):
            continue                                 # an empty legacy port
        usb = None
        for parent in [real, *real.parents]:
            if (parent / "idVendor").exists():
                usb = parent.name
                break
        out.append({"port": f"/dev/{tty.name}", "usb": usb, "driver": _driver(tty / "device")})
    return out


def linux_input(root=ROOT):
    """Keyboards, mice and other input devices the kernel made (Bluetooth and built-in ones included)."""
    text = read(root / "proc/bus/input/devices", "") or ""
    out = []
    for block in text.split("\n\n"):
        name = re.search(r'^N: Name="(.*)"$', block, re.M)
        handlers = re.search(r"^H: Handlers=(.*)$", block, re.M)
        bus = re.search(r"^I: Bus=(\w+)", block, re.M)
        phys = re.search(r"^P: Phys=(.*)$", block, re.M)
        if not name or not handlers:
            continue
        h = handlers.group(1).split()
        has_leds = bool(re.search(r"^B: LED=", block, re.M))      # real keyboards have lock lights
        if "kbd" in h and (has_leds or re.search(r"keyboard", name.group(1), re.I)):
            kind = "keyboard"
        elif any(x.startswith("mouse") for x in h):
            kind = "mouse"
        else:
            kind = None
        if not kind:
            continue
        out.append({"name": name.group(1), "kind": kind,
                    "bus": {"0003": "USB", "0005": "Bluetooth", "0011": "built-in", "0018": "built-in"}.get(
                        (bus.group(1) if bus else "").lower().zfill(4), "other"),
                    "phys": phys.group(1) if phys else ""})
    return out


def linux_sensors(root=ROOT):
    out = []
    for dev in sorted((root / "sys/bus/iio/devices").glob("iio:device*")):
        channels = sorted({re.sub(r"_(raw|input|scale|offset)$", "", p.name) for p in dev.glob("in_*")
                           if re.search(r"_(raw|input)$", p.name)})
        if not channels:
            continue
        usb = "usb" in str(dev.resolve())
        out.append({"id": f"iio:{dev.name}", "kind": "sensor", "name": read(dev / "name", dev.name),
                    "connection": "USB" if usb else "built-in", "status": "connected", "internal": not usb,
                    "drivers": [d for d in [_driver(dev / "device")] if d], "channels": channels, "sysfs": str(dev)})
    return out


def kernel_modules(modaliases, root=ROOT):
    """Kernel modules whose aliases match these devices (could drive them), and whether each is loaded."""
    import fnmatch
    release = platform.release()
    table = None
    for base in (root / "usr/lib/modules" / release, root / "lib/modules" / release):
        if (base / "modules.alias").exists():
            table = base / "modules.alias"
            break
    if not table or not modaliases:
        return []
    found = {}
    try:
        with open(table, errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 3 and parts[0] == "alias" and parts[1].startswith("usb:"):
                    for m in modaliases:
                        if m and fnmatch.fnmatchcase(m, parts[1]):
                            found[parts[2]] = True
    except OSError:
        return []
    loaded = {ln.split()[0] for ln in (read(root / "proc/modules", "") or "").splitlines() if ln.strip()}
    return [{"module": m, "loaded": m.replace("-", "_") in loaded} for m in sorted(found)[:12]]


def kimios_graph(root=ROOT):
    try:
        return json.loads((root / "var/lib/kimios/hardware.json").read_text())
    except (OSError, ValueError):
        return None


# ================================================================================================ programs per kind
def programs_for(dev):
    """The programs that control a device of this kind: [(command, what it is for)]."""
    kind, what = dev.get("kind"), dev.get("what") or ""
    if kind == "phone":
        return [("ideviceinfo", "talk to the iPhone")] if "Apple" in what else \
            [("adb", "files, screenshots, apps and commands on the phone")]
    if kind == "display" and not dev.get("internal"):
        return [("ddcutil", "the display's brightness and input")]
    if kind in ("audio", "bluetooth") or dev.get("bluetooth"):
        return [("bluetoothctl", "connect and disconnect")] if dev.get("bluetooth") else \
            [("pactl", "volume and the default output")]
    if kind == "board" and "boot mode" in what:
        return [("picotool", "information about the board (copying a .uf2 to its drive needs nothing)")]
    return []


# ================================================================================================ the inventory
class Devices:
    """What is attached, kept fresh by a cheap watch (names under /sys every few seconds; BlueZ less often)."""

    def __init__(self, app, root=ROOT):
        self.app, self.root = app, root
        self.known = None             # id -> short description, from the last look
        self.cache = (0.0, [])
        self.bt_cache = (0.0, [])

    # ---------------------------------------------------------------- the watch
    async def _watch(self):
        slow = os.name == "nt" or sys.platform == "darwin"
        while True:
            try:
                if self.app.db.setting("devices_watch", True):
                    await self.look(announce=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.app.log(f"devices: {type(exc).__name__}: {exc}")
            await asyncio.sleep(15 if slow else 3)

    def signature(self):
        """A cheap fingerprint of what is attached (Linux), to skip a full look when nothing changed."""
        if os.name == "nt" or sys.platform == "darwin":
            return None
        parts = sorted(p.name for p in (self.root / "sys/bus/usb/devices").glob("*") if ":" not in p.name)
        parts += sorted(f"{c.name}={read(c / 'status', '')}" for c in (self.root / "sys/class/drm").glob("card*-*"))
        parts.append(str(len(read(self.root / "proc/bus/input/devices", "") or "")))
        sim = Path(os.environ.get("KIMIOS_SIM_DIR", "/run/kimios-sim"))
        parts += sorted(p.name for p in sim.glob("sim-*.json")) if sim.is_dir() else []
        return "|".join(parts)

    async def look(self, announce=False, fresh=False):
        sig = self.signature()
        if not fresh and sig is not None and getattr(self, "_sig", None) == sig and \
                time.monotonic() - self.cache[0] < 60 and not self._bt_due():
            return self.cache[1]
        self._sig = sig
        found = await self.inventory()
        self.cache = (time.monotonic(), found)
        now = {d["id"]: d for d in found}
        if self.known is not None and announce:
            for did, d in now.items():
                if did not in self.known and self.worth_saying(d):
                    self.announce(d, True)
            for did, d in self.known.items():
                if did not in now and self.worth_saying(d):
                    self.announce(d, False)
        changed = self.known is not None and set(now) != set(self.known)
        self.known = {k: {x: v.get(x) for x in ("id", "kind", "name", "internal", "what", "connection")}
                      for k, v in now.items()}
        if changed:
            self.app.bus.publish("devices", ids=sorted(now))
        return found

    @staticmethod
    def worth_saying(d):
        """Arrivals worth a notification: not built-in parts, not unknown USB bits, not a display's own sound."""
        return not d.get("internal") and d.get("kind") != "other" and not (
            str(d.get("id", "")).startswith("sink:") and d.get("connection") == "HDMI")

    def _bt_due(self):
        return time.monotonic() - self.bt_cache[0] > 15

    def announce(self, d, arrived):
        acts = [a["name"] for a in self.actions(d) if a["name"] != "info"][:4]
        body = (d.get("what") or d["kind"]) + (f". It can: {', '.join(a.replace('_', ' ') for a in acts)}" if
                                               acts and arrived else "")
        self.app.bus.publish("notify", title=("Connected: " if arrived else "Disconnected: ") + d["name"],
                             body=body, level="device", device_id=d["id"], arrived=arrived)
        self.app.bus.publish("device", id=d["id"], arrived=arrived, name=d["name"], device_kind=d["kind"])

    # ---------------------------------------------------------------- what is attached
    async def inventory(self):
        if os.name == "nt":
            found = await self._windows()
        elif sys.platform == "darwin":
            found = await self._macos()
        else:
            found = await asyncio.to_thread(self._linux)
            bt = await self._bluetooth()
            sinks = await self._audio()
            for d in bt:                       # a Bluetooth headset's sound output is part of it
                mine = next((x for x in sinks if d["mac"].replace(":", "_") in str(x.get("sink"))), None)
                if mine:
                    sinks.remove(mine)
                    d.update({k: mine[k] for k in ("sink", "volume", "muted", "default")})
            found += bt + sinks
            from . import bench                      # 2.30.0: KimiOS's guarded (and simulated) devices
            found += [bench.as_device(r) for r in await asyncio.to_thread(bench.listing_sync)]
        await self._adb_states(found)
        return found

    def _linux(self):
        usb = linux_usb(self.root)
        serial = linux_serial(self.root)
        by_dir = {d["sysfs"].rsplit("/", 1)[-1]: d for d in usb}
        for s in serial:
            if s["usb"] in by_dir:
                by_dir[s["usb"]].setdefault("ports", []).append(s["port"])
        out = list(usb)
        out += [{"id": f"serial:{s['port']}", "kind": "serial", "name": f"Serial port {s['port']}",
                 "connection": "built-in", "status": "connected", "internal": True, "ports": [s["port"]],
                 "drivers": [s["driver"]] if s["driver"] else []} for s in serial if not s["usb"]]
        out += linux_displays(self.root)
        out += linux_sensors(self.root)
        for i in linux_input(self.root):            # Bluetooth and built-in keyboards and mice (USB ones are above)
            if i["bus"] == "USB":
                continue
            out.append({"id": "input:" + re.sub(r"\W+", "-", i["name"].lower()).strip("-"), "kind": i["kind"],
                        "name": i["name"], "connection": i["bus"], "status": "connected",
                        "internal": i["bus"] == "built-in", "what": f"{i['kind'].title()} ({i['bus']})",
                        "drivers": []})
        graph = kimios_graph(self.root)
        if graph:                                    # KimiOS's capability graph: unclaimed devices it knows of
            unclaimed = {(u.get("vendor_id"), u.get("device_id")) for u in graph.get("unclaimed") or []}
            for d in out:
                if (d.get("vendor_id"), d.get("product_id")) in unclaimed:
                    d["kimios_unclaimed"] = True
        return out

    async def _bluetooth(self):
        if not self._bt_due():
            return self.bt_cache[1]
        out = []
        code, text = await sh(["bluetoothctl", "devices", "Connected"], 8, self.app)
        if code == 0:
            for mac, name in re.findall(r"^Device ((?:[0-9A-F]{2}:){5}[0-9A-F]{2}) (.*)$", text, re.M):
                c2, info = await sh(["bluetoothctl", "info", mac], 8, self.app)
                icon = (re.search(r"Icon: (\S+)", info) or [None, ""])[1]
                battery = re.search(r"Battery Percentage: 0x\w+ \((\d+)\)", info)
                kind = ("audio" if icon.startswith("audio") else "keyboard" if "keyboard" in icon else
                        "mouse" if "mouse" in icon else "phone" if "phone" in icon else
                        "input" if icon.startswith("input") else "bluetooth")
                out.append({"id": f"bt:{mac}", "kind": kind, "name": name.strip(), "connection": "Bluetooth",
                            "status": "connected", "bluetooth": True, "mac": mac, "icon": icon,
                            "what": f"Bluetooth {icon.replace('-', ' ') or 'device'}",
                            "battery": int(battery.group(1)) if battery else None, "drivers": ["bluez"]})
        self.bt_cache = (time.monotonic(), out)
        return out

    async def _audio(self):
        """Sound outputs and inputs the sound server offers (headsets, speakers, HDMI audio, USB sound cards)."""
        code, text = await sh(["pactl", "--format=json", "list", "sinks"], 6, self.app)
        if code != 0:
            return []
        try:
            sinks = json.loads(text)
        except ValueError:
            return []
        code, default = await sh(["pactl", "get-default-sink"], 6, self.app)
        out = []
        for s in sinks:
            name = s.get("description") or s.get("name")
            vol = next(iter((s.get("volume") or {}).values()), {}).get("value_percent", "")
            out.append({"id": f"sink:{s.get('name')}", "kind": "audio", "name": name, "connection":
                        "Bluetooth" if "bluez" in str(s.get("name")) else "HDMI" if "hdmi" in str(s.get("name")).lower()
                        else "USB" if "usb" in str(s.get("name")).lower() else "built-in",
                        "status": "connected", "internal": "pci" in str(s.get("name")) and "hdmi" not in
                        str(s.get("name")).lower(), "what": "Sound output", "sink": s.get("name"),
                        "volume": vol, "muted": s.get("mute"), "default": s.get("name") == default.strip(),
                        "drivers": [s.get("driver") or "pipewire"]})
        return out

    async def _adb_states(self, found):
        phones = [d for d in found if d["kind"] == "phone" and "Apple" not in (d.get("what") or "")]
        if not phones:
            return
        code, text = await sh(["adb", "devices", "-l"], 10, self.app)
        if code == 127:
            for d in phones:
                d["state"] = "adb is not installed"
            return
        states = dict(re.findall(r"^(\S+)\s+(device|unauthorized|offline|recovery|sideload)\b", text, re.M))
        for d in phones:
            st = states.get(d.get("serial") or "")
            d["state"] = {"device": "ready", "unauthorized": "allow USB debugging on the phone's screen",
                          None: "turn on USB debugging on the phone"}.get(st, st)
            d["adb_serial"] = d.get("serial") if st else None

    async def _windows(self):
        script = ("Get-PnpDevice -PresentOnly | Where-Object { $_.Class -in 'USB','HIDClass','Keyboard','Mouse',"
                  "'Monitor','Bluetooth','AudioEndpoint','Camera','Image','Ports','WPD','AndroidUsbDeviceClass',"
                  "'Sensor','DiskDrive','Media' } | Select-Object FriendlyName,Class,Status,InstanceId,Manufacturer "
                  "| ConvertTo-Json -Compress")
        code, text = await sh(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], 30, self.app)
        try:
            rows = json.loads(text) if code == 0 and text.strip() else []
        except ValueError:
            rows = []
        rows = [rows] if isinstance(rows, dict) else rows
        kind_of = {"Keyboard": "keyboard", "Mouse": "mouse", "Monitor": "display", "Bluetooth": "bluetooth",
                   "AudioEndpoint": "audio", "Camera": "camera", "Image": "camera", "Ports": "serial", "WPD": "phone",
                   "AndroidUsbDeviceClass": "phone", "Sensor": "sensor", "DiskDrive": "storage", "Media": "audio",
                   "HIDClass": "input", "USB": "other"}
        out, seen = [], set()
        for r in rows:
            iid = r.get("InstanceId") or ""
            name = r.get("FriendlyName") or iid
            if not name or iid in seen or re.search(r"root hub|host controller|composite device|hub$", name, re.I):
                continue
            seen.add(iid)
            m = re.search(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(?:[^\\]*\\(.+))?", iid, re.I)
            vid, pid = (m.group(1).lower(), m.group(2).lower()) if m else (None, None)
            kind = kind_of.get(r.get("Class"), "other")
            if vid and kind in ("other", "input", "serial"):
                k2, _what = usb_kind(vid, pid, name, [], r.get("Manufacturer") or "")
                kind = k2 if k2 != "other" else kind
            port = re.search(r"\((COM\d+)\)", name)
            out.append({"id": "pnp:" + iid, "kind": kind, "name": name, "connection": "USB" if "USB" in iid else
                        "Bluetooth" if iid.startswith("BTH") else "other", "status": (r.get("Status") or "").lower(),
                        "vendor_id": vid, "product_id": pid, "serial": m.group(3) if m and m.group(3) and
                        "&" not in m.group(3) else None, "ports": [port.group(1)] if port else [],
                        "what": r.get("Class"), "drivers": []})
        return [d for d in out if d["kind"] != "other" or d["vendor_id"]]

    async def _macos(self):
        code, text = await sh(["system_profiler", "-json", "SPUSBDataType", "SPDisplaysDataType",
                               "SPBluetoothDataType"], 30, self.app)
        try:
            data = json.loads(text) if code == 0 else {}
        except ValueError:
            data = {}
        out = []

        def walk(items):
            for it in items or []:
                if it.get("_items"):
                    walk(it["_items"])
                vid = str(it.get("vendor_id", "")).split()[0].replace("0x", "").lower()[:4]
                pid = str(it.get("product_id", "")).replace("0x", "").lower()[:4]
                if not vid:
                    continue
                kind, what = usb_kind(vid, pid, it.get("_name", ""), [], it.get("manufacturer", ""))
                out.append({"id": f"usb:{it.get('location_id', it.get('_name'))}", "kind": kind,
                            "name": it.get("_name"), "what": what, "connection": "USB", "status": "connected",
                            "vendor_id": vid, "product_id": pid, "serial": it.get("serial_num"), "drivers": []})
        walk(data.get("SPUSBDataType"))
        for gpu in data.get("SPDisplaysDataType") or []:
            for disp in gpu.get("spdisplays_ndrvs") or []:
                internal = "internal" in str(disp.get("spdisplays_connection_type", "")).lower()
                out.append({"id": f"display:{disp.get('_name')}", "kind": "display", "name": disp.get("_name"),
                            "connection": disp.get("spdisplays_connection_type", ""), "internal": internal,
                            "status": "connected", "details": {"resolution": disp.get("_spdisplays_resolution")},
                            "drivers": []})
        return out

    # ---------------------------------------------------------------- one device
    async def get(self, device_id, fresh=False):
        found = await self.look(fresh=fresh)
        want = str(device_id or "").strip()
        hit = next((d for d in found if d["id"] == want), None)
        if not hit:                                   # by name, or the start of an id
            low = want.lower()
            hits = [d for d in found if low and (low in d["name"].lower() or d["id"].lower().startswith(low)
                                                  or low == d["kind"])]
            if len(hits) == 1:
                hit = hits[0]
            elif len(hits) > 1:
                raise DeviceError(f"More than one device matches {want!r}: "
                                  + "; ".join(f"{d['id']} ({d['name']})" for d in hits[:8]))
        if not hit:
            raise DeviceError(f"No attached device {want!r}. Attached: "
                              + ("; ".join(f"{d['id']} ({d['name']})" for d in found[:20]) or "none"))
        return hit

    async def describe(self, device_id):
        """Everything about one device: what drives it, what controls it, who may open it, what it can do."""
        d = dict(await self.get(device_id))
        if os.name != "nt" and sys.platform != "darwin":
            aliases = [i.get("modalias") for i in d.get("interfaces") or [] if not i.get("driver")]
            d["modules"] = await asyncio.to_thread(kernel_modules, aliases, self.root) if aliases else []
            d["access"] = self.access(d)
        progs = []
        for cmd, why in programs_for(d):
            progs.append({"program": cmd, "for": why, "installed": bool(installer.which(self.app, cmd)),
                          "install": installer.how_it_would_install("command", cmd)})
        d["programs"] = progs
        d["actions"] = self.actions(d)
        d["trusted"] = self.trusted(d)
        d["next"] = self.next_step(d)
        return d

    def access(self, d):
        nodes = list(d.get("ports") or [])
        if d.get("busnum") and d.get("devnum"):
            nodes.append(f"/dev/bus/usb/{int(d['busnum']):03d}/{int(d['devnum']):03d}")
        out = {}
        for n in nodes:
            p = self.root / n.lstrip("/")
            if p.exists():
                out[n] = "read and write" if os.access(p, os.R_OK | os.W_OK) else \
                    "read only" if os.access(p, os.R_OK) else "no access"
        return out

    def next_step(self, d):
        kind, state = d["kind"], d.get("state") or ""
        if d.get("bench"):
            return ("Run by KimiOS's guard: agents use the KimiOS tools bench, driver, bench_experiment, controller"
                    + (" and bench_event" if d.get("simulated") else "") + "; Devices shows it live.")
        if kind == "phone" and "Apple" not in (d.get("what") or ""):
            if "not installed" in state:
                return "adb is installed by the first phone action (or install_program name='adb')."
            if "USB debugging" in state:
                return ("On the phone: Settings → About phone → tap Build number seven times; then Settings → "
                        "System → Developer options → USB debugging on; plug in again and allow this computer.")
        if any(v == "no access" for v in (d.get("access") or {}).values()):
            return ("This user may not open the device. KimiOS gives the signed-in user access by itself; on other "
                    "Linux systems add yourself to the 'dialout' (serial) or 'plugdev' group and plug it in again.")
        if d.get("modules") and not d.get("drivers"):
            m = [x["module"] for x in d["modules"] if not x["loaded"]]
            if m:
                return f"No driver is bound; the kernel has one that matches: {', '.join(m)} (KimiOS loads it)."
        if not d.get("drivers") and kind == "other":
            return "No driver is bound and none is known: KimiOS can try a user-space driver (device_inspect)."
        return ""

    def trusted(self, d):
        return bool(self.app.db.setting(f"device_trust:{d.get('key') or d['id']}", False))

    # ---------------------------------------------------------------- actions
    def actions(self, d):
        """What can be done with a device: [{name, about, args, changes, physical}]."""
        A = lambda name, about, args=None, changes=False, physical=False: {  # noqa: E731
            "name": name, "about": about, "args": args or {}, "changes": changes, "physical": physical}
        kind, what = d["kind"], d.get("what") or ""
        out = [A("info", "what it is, what drives it, its state")]
        if d.get("bench"):          # driven only through KimiOS's guard: its tools, not here
            return out
        if kind == "phone" and "Apple" in what:
            out.append(A("phone_info", "model, iOS version, battery (ideviceinfo)"))
        elif kind == "phone":
            out += [A("phone_info", "model, Android version, battery, storage"),
                    A("list_files", "list a folder on the phone", {"path": "folder on the phone (default /sdcard)"}),
                    A("pull", "copy a file or folder from the phone to the chat's folder",
                      {"path": "path on the phone"}),
                    A("push", "copy a file to the phone", {"file": "a file in the chat's folders",
                                                           "to": "folder on the phone (default /sdcard/Download)"},
                      changes=True),
                    A("screenshot", "a picture of the phone's screen, saved in the chat's folder"),
                    A("open_url", "open a web address on the phone", {"url": "the address"}, changes=True),
                    A("install_app", "install an .apk on the phone", {"file": "the .apk"}, changes=True),
                    A("shell", "run a command on the phone (adb shell)", {"command": "the command"}, changes=True)]
        if kind == "display":
            out.append(A("brightness", "set the screen's brightness (built-in: the backlight; external: DDC/CI "
                         "with ddcutil)", {"percent": "0 to 100"}, changes=True))
            if os.name != "nt" and sys.platform != "darwin":       # 2.33.0: dva/display.py
                if not d.get("internal"):
                    out += [A("monitor_settings", "what the monitor's own menu holds and offers over DDC/CI: "
                              "brightness, contrast, sharpness, red/green/blue, speaker volume and mute, input, "
                              "colour preset, picture mode, power"),
                            A("monitor_set", "change one of the monitor's own settings",
                              {"setting": "brightness, contrast, sharpness, red, green, blue, speaker_volume, mute, "
                                          "input, color_preset, picture_mode or power",
                               "value": "a level (0 to its maximum) or one of the values monitor_settings lists"},
                              changes=True)]
                out += [A("screen_layout", "how the computer drives it: resolution, refresh, scale, rotation, "
                          "position next to the other screens, main screen, on or off, and the modes it offers"),
                        A("screen_set", "change how the computer drives it (GNOME asks on the screen to keep it, "
                          "and goes back after 20 seconds without an answer)",
                          {"mode": "resolution, such as 1920x1080 or 1920x1080@75", "refresh": "Hz, such as 75",
                           "scale": "1, 1.25, 1.5, 2 …", "rotation": "0, 90, 180 or 270",
                           "position": "right-of / left-of / above / below another screen, or x,y",
                           "primary": "true to make it the main screen", "on": "true or false"}, changes=True)]
        if d.get("sink"):
            out += [A("volume", "set the volume", {"percent": "0 to 150"}, changes=True),
                    A("mute", "mute or unmute", {"on": "true or false"}, changes=True),
                    A("make_default", "send all sound here", changes=True)]
        if d.get("bluetooth"):
            out += [A("disconnect", "disconnect it", changes=True), A("connect", "connect it again", changes=True)]
        if d.get("ports"):
            out += [A("serial_read", "listen to the serial port for a few seconds",
                      {"seconds": "how long (default 3)", "baud": "speed (default 115200)"}),
                    A("serial_write", "send a line to the serial port and read the answer (it may make a board move "
                      "or switch something)", {"text": "what to send", "baud": "speed (default 115200)"},
                      changes=True, physical=True)]
        if kind == "board" and "boot mode" in what:
            out.append(A("flash_uf2", "copy a .uf2 program to the board's boot drive (the board restarts with it)",
                         {"file": "the .uf2 file"}, changes=True, physical=True))
        if kind == "sensor" and d.get("channels"):
            out.append(A("read", "read the sensor's values now"))
        if kind == "camera" and os.name != "nt":
            out.append(A("photo", "take a picture with the camera, saved in the chat's folder"))
        if d.get("battery") is not None or kind in ("keyboard", "mouse"):
            out.append(A("battery", "battery level, where the device reports it"))
        return out

    async def control(self, device_id, action, args=None, workspace=None, on_status=None):
        d = await self.describe(device_id)
        args = dict(args or {})
        act = next((a for a in d["actions"] if a["name"] == action), None)
        if not act:
            raise DeviceError(f"{d['name']} cannot do {action!r}. It can: "
                              + ", ".join(a["name"] for a in d["actions"]))
        workspace = Path(workspace or installer.tools_dir(self.app))
        fn = getattr(self, "_do_" + action)
        return await fn(d, args, workspace, on_status)

    async def need(self, command, on_status=None):
        """A program an action needs: installed on first use (installer.py), unless that is switched off."""
        if installer.which(self.app, command):
            return
        if not self.app.db.setting("install_programs", True):
            raise DeviceError(f"{command} is needed and is not installed (installing programs is switched off)")
        try:
            await installer.install(self.app, command, "command", on_status=on_status, by="Devices")
        except installer.InstallError as exc:
            raise DeviceError(f"{command} is needed and could not be installed: {exc}") from None

    async def adb(self, d, *argv, timeout=60, binary=False, on_status=None):
        await self.need("adb", on_status)
        serial = d.get("adb_serial") or d.get("serial")
        code, out = await sh(["adb", *(["-s", serial] if serial else []), *argv], timeout, self.app, binary=binary)
        text = out if not binary else b""
        if code != 0:
            msg = (out.decode("utf-8", "replace") if binary else out).strip()
            if re.search(r"unauthorized", msg, re.I):
                raise DeviceError("The phone has not allowed this computer yet: unlock it and accept 'Allow USB "
                                  "debugging?'")
            if re.search(r"no devices|not found", msg, re.I):
                raise DeviceError("The phone is not available to adb: " + self.next_step({**d, "state":
                                                                                          "turn on USB debugging"}))
            raise DeviceError(f"adb: {msg[-400:]}")
        return out if binary else text

    # -- general
    async def _do_info(self, d, args, ws, st):
        keep = {k: v for k, v in d.items() if k not in ("actions", "sysfs", "interfaces") and v not in (None, [], {}, "")}
        return json.dumps(keep, indent=1, ensure_ascii=False)

    async def _do_battery(self, d, args, ws, st):
        if d.get("battery") is not None:
            return f"{d['name']}: battery {d['battery']} %"
        code, text = await sh(["upower", "-d"], 10, self.app)
        for block in text.split("\n\n"):
            if d["name"].split()[0].lower() in block.lower():
                pct = re.search(r"percentage:\s+(\d+)", block)
                if pct:
                    return f"{d['name']}: battery {pct.group(1)} %"
        return f"{d['name']} does not report a battery level"

    # -- phones
    async def _do_phone_info(self, d, args, ws, st):
        if "Apple" in (d.get("what") or ""):
            await self.need("ideviceinfo", st)
            code, out = await sh(["ideviceinfo", "-s"], 20, self.app)
            if code != 0:
                raise DeviceError("The iPhone did not answer: unlock it and tap 'Trust' on its screen. " + out[-200:])
            return out
        props = await self.adb(d, "shell", "getprop ro.product.manufacturer; getprop ro.product.model; "
                               "getprop ro.build.version.release; dumpsys battery | grep -E 'level|status|powered'; "
                               "df -h /sdcard | tail -1", on_status=st)
        lines = [x.strip() for x in props.splitlines() if x.strip()]
        return ("Maker: {}\nModel: {}\nAndroid: {}\n".format(*(lines + ["", "", ""])[:3])
                + "\n".join(lines[3:]))

    async def _do_list_files(self, d, args, ws, st):
        path = str(args.get("path") or "/sdcard")
        return await self.adb(d, "shell", "ls", "-la", path, on_status=st)

    async def _do_pull(self, d, args, ws, st):
        path = str(args.get("path") or "")
        if not path:
            raise DeviceError("Say which file or folder on the phone (path)")
        target = ws / "from-phone"
        target.mkdir(parents=True, exist_ok=True)
        out = await self.adb(d, "pull", path, str(target), timeout=1800, on_status=st)
        return f"{out.strip()[-300:]}\nSaved in {target / Path(path).name}"

    async def _do_push(self, d, args, ws, st):
        src = Path(str(args.get("file") or ""))
        if not src.is_file():
            raise DeviceError(f"There is no file {src}")
        to = str(args.get("to") or "/sdcard/Download/")
        return (await self.adb(d, "push", str(src), to, timeout=1800, on_status=st)).strip()[-300:]

    async def _do_screenshot(self, d, args, ws, st):
        data = await self.adb(d, "exec-out", "screencap", "-p", binary=True, timeout=60, on_status=st)
        if not data or data[:4] != b"\x89PNG":
            raise DeviceError("The phone did not send a picture (is its screen locked?)")
        path = ws / f"phone-screen-{time.strftime('%Y%m%d-%H%M%S')}.png"
        path.write_bytes(data)
        return f"Saved the phone's screen as {path}"

    async def _do_open_url(self, d, args, ws, st):
        url = str(args.get("url") or "")
        if not re.match(r"^https?://\S+$", url):
            raise DeviceError("Give a web address that starts with http:// or https://")
        await self.adb(d, "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url, on_status=st)
        return f"Opened {url} on the phone"

    async def _do_install_app(self, d, args, ws, st):
        src = Path(str(args.get("file") or ""))
        if src.suffix.lower() != ".apk" or not src.is_file():
            raise DeviceError("Give an .apk file in the chat's folders")
        return (await self.adb(d, "install", "-r", str(src), timeout=900, on_status=st)).strip()[-300:]

    async def _do_shell(self, d, args, ws, st):
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            raise DeviceError("Say what to run on the phone (command)")
        return (await self.adb(d, "shell", cmd, timeout=120, on_status=st))[-6000:] or "(no output)"

    # -- displays
    async def _do_brightness(self, d, args, ws, st):
        try:
            pct = max(0, min(100, int(float(args.get("percent")))))
        except (TypeError, ValueError):
            raise DeviceError("Give the brightness as a percentage (0 to 100)") from None
        if d.get("internal"):
            for bl in sorted((self.root / "sys/class/backlight").glob("*")):
                top = int(read(bl / "max_brightness", "0") or 0)
                if not top:
                    continue
                value = max(1, round(top * pct / 100))
                # logind lets the signed-in user set the backlight without root
                code, out = await sh(["busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
                                      "org.freedesktop.login1.Session", "SetBrightness", "ssu", "backlight", bl.name,
                                      str(value)], 10, self.app)
                if code != 0:
                    try:
                        (bl / "brightness").write_text(str(value))
                    except OSError:
                        raise DeviceError(f"The brightness could not be set: {out.strip()[-200:]}") from None
                return f"Brightness of {d['name']} set to {pct} %"
            raise DeviceError("This screen's brightness is not controlled by the computer")
        if os.name == "nt" or sys.platform == "darwin":
            raise DeviceError("An external display's brightness is set from the display's own menu here")
        await self.need("ddcutil", st)
        code, out = await sh(["ddcutil", "detect", "--terse"], 30, self.app)
        bus = None
        for block in out.split("\n\n"):
            if d.get("port") and d["port"] in block:
                m = re.search(r"/dev/i2c-(\d+)", block)
                bus = m.group(1) if m else None
        argv = ["ddcutil", *(["--bus", bus] if bus else []), "setvcp", "10", str(pct)]
        code, out = await sh(argv, 30, self.app)
        if code != 0:
            raise DeviceError("The display did not take the setting over DDC/CI (switch DDC/CI on in its own menu): "
                              + out.strip()[-200:])
        return f"Brightness of {d['name']} set to {pct} %"

    async def _do_monitor_settings(self, d, args, ws, st):
        from . import display
        await self.need("ddcutil", st)
        state = await display.monitor_state(self.app, d.get("port"))
        lines = [f"{state.get('model') or d['name']} (over DDC/CI, i2c bus {state['bus']})"]
        for row in state["settings"]:
            if row["kind"] == "C":
                lines.append(f"- {row['name']}: {row['current']} of {row['max']}")
            else:
                lines.append(f"- {row['name']}: {row.get('current_name') or row.get('value') or '?'} "
                             f"(can be: {', '.join(o['name'] for o in row['options'])})")
        if state["other"]:
            lines.append("Other features it lists: " + ", ".join(f"{o['code']} {o['name']}" for o in state["other"]))
        return "\n".join(lines)

    async def _do_monitor_set(self, d, args, ws, st):
        from . import display
        setting = str(args.get("setting") or "").strip().lower().replace(" ", "_").replace("colour", "color")
        value = args.get("value")
        if not setting or value in (None, ""):
            raise DeviceError("Say which setting and the value (monitor_settings lists them)")
        if setting == "power" and str(value).strip().lower() not in ("on", "01", "x01"):
            lay = await display.layout(self.app)
            others = [o for o in lay["outputs"] if o["enabled"]
                      and display.screen_key(o["name"]) != display.screen_key(d.get("port"))]
            if lay["how"] and not others:
                raise DeviceError("This monitor is the only screen that is on: switching it off would leave "
                                  "nothing to see")
        await self.need("ddcutil", st)
        return await display.monitor_set(self.app, d.get("port"), setting, value)

    async def _do_screen_layout(self, d, args, ws, st):
        from . import display
        lay = await display.layout(self.app)
        if not lay["how"]:
            return lay["note"]
        lines = [f"Screens ({'GNOME' if lay['how'] == 'gnome' else 'wlr-randr'}):"]
        for o in lay["outputs"]:
            me = display.screen_key(o["name"]) == display.screen_key(d.get("port"))
            state = (f"{o['mode']} Hz, scale {o['scale']:g}, rotated {o['rotation']}°, at "
                     f"{o['position']['x']},{o['position']['y']}" + (", main screen" if o["primary"] else "")) \
                if o["enabled"] else "off"
            lines.append(f"{'→ ' if me else '- '}{o['name']} ({o.get('description') or ''}): {state}")
            if me:
                lines.append("  modes: " + ", ".join(dict.fromkeys(m["mode"] for m in o["modes"]))[:900])
                cur = next((m for m in o["modes"] if m["current"]), None)
                if cur and cur.get("scales"):
                    lines.append("  scales at this mode: " + ", ".join(f"{x:g}" for x in cur["scales"]))
        return "\n".join(lines)

    async def _do_screen_set(self, d, args, ws, st):
        from . import display
        return await display.set_layout(self.app, d.get("port"), mode=args.get("mode"), refresh=args.get("refresh"),
                                        scale=args.get("scale"), rotation=args.get("rotation"),
                                        position=args.get("position"), primary=args.get("primary"),
                                        enabled=args.get("on"))

    # -- sound
    async def _do_volume(self, d, args, ws, st):
        try:
            pct = max(0, min(150, int(float(args.get("percent")))))
        except (TypeError, ValueError):
            raise DeviceError("Give the volume as a percentage") from None
        code, out = await sh(["pactl", "set-sink-volume", d["sink"], f"{pct}%"], 10, self.app)
        if code != 0:
            raise DeviceError(out.strip()[-200:])
        return f"Volume of {d['name']} set to {pct} %"

    async def _do_mute(self, d, args, ws, st):
        on = str(args.get("on", True)).lower() not in ("false", "0", "no", "off")
        code, out = await sh(["pactl", "set-sink-mute", d["sink"], "1" if on else "0"], 10, self.app)
        if code != 0:
            raise DeviceError(out.strip()[-200:])
        return f"{d['name']} {'muted' if on else 'unmuted'}"

    async def _do_make_default(self, d, args, ws, st):
        code, out = await sh(["pactl", "set-default-sink", d["sink"]], 10, self.app)
        if code != 0:
            raise DeviceError(out.strip()[-200:])
        return f"Sound now goes to {d['name']}"

    # -- Bluetooth
    async def _bt(self, d, verb):
        code, out = await sh(["bluetoothctl", verb, d["mac"]], 25, self.app)
        self.bt_cache = (0.0, [])
        if code != 0 or re.search(r"Failed|not available", out):
            raise DeviceError(out.strip()[-200:])
        return f"{d['name']}: {verb}ed" if verb == "connect" else f"{d['name']}: disconnected"

    async def _do_connect(self, d, args, ws, st):
        return await self._bt(d, "connect")

    async def _do_disconnect(self, d, args, ws, st):
        return await self._bt(d, "disconnect")

    # -- serial ports and boards
    async def _do_serial_read(self, d, args, ws, st):
        port = d["ports"][0]
        seconds = max(0.5, min(60.0, float(args.get("seconds") or 3)))
        baud = int(args.get("baud") or 115200)
        return await asyncio.to_thread(serial_exchange, port, baud, None, seconds)

    async def _do_serial_write(self, d, args, ws, st):
        port = d["ports"][0]
        baud = int(args.get("baud") or 115200)
        text = str(args.get("text") or "")
        return await asyncio.to_thread(serial_exchange, port, baud, text, float(args.get("seconds") or 2))

    async def _do_flash_uf2(self, d, args, ws, st):
        src = Path(str(args.get("file") or ""))
        if src.suffix.lower() != ".uf2" or not src.is_file():
            raise DeviceError("Give a .uf2 file in the chat's folders")
        code, out = await sh(["lsblk", "-J", "-o", "LABEL,MOUNTPOINT,PATH"], 10, self.app)
        try:
            disks = json.loads(out).get("blockdevices") or []
        except ValueError:
            disks = []
        flat = []

        def walk(items):
            for it in items:
                flat.append(it)
                walk(it.get("children") or [])
        walk(disks)
        drive = next((x for x in flat if (x.get("label") or "") in ("RPI-RP2", "RP2350")), None)
        if not drive:
            raise DeviceError("The board's boot drive (RPI-RP2) is not there: hold BOOTSEL while plugging it in")
        mount = drive.get("mountpoint")
        if not mount:
            code, out = await sh(["udisksctl", "mount", "-b", drive["path"]], 20, self.app)
            m = re.search(r" at (.+?)\.?$", out.strip())
            mount = m.group(1) if m else None
        if not mount:
            raise DeviceError("The board's boot drive could not be opened: " + out.strip()[-200:])
        await asyncio.to_thread(shutil.copyfile, src, Path(mount) / src.name)
        return f"Copied {src.name} to the board; it restarts with the new program"

    # -- sensors and cameras
    async def _do_read(self, d, args, ws, st):
        base = Path(d["sysfs"])
        out = []
        for ch in d.get("channels") or []:
            raw = read(base / f"{ch}_input") or read(base / f"{ch}_raw")
            family = ch.split("_")[1] if ch.count("_") >= 1 else ch       # in_accel_x → accel (shared scale)
            scale = None if read(base / f"{ch}_input") else (read(base / f"{ch}_scale") or
                                                             read(base / f"in_{family}_scale"))
            offset = None if read(base / f"{ch}_input") else (read(base / f"{ch}_offset") or
                                                              read(base / f"in_{family}_offset"))
            try:
                value = (float(raw) + float(offset or 0)) * float(scale or 1) if raw is not None else None
            except ValueError:
                value = raw
            out.append(f"{ch[3:]}: {value if value is not None else '?'}")
        return f"{d['name']} — " + "; ".join(out)

    async def _do_photo(self, d, args, ws, st):
        node = next((f"/dev/{p.name}" for p in Path(d.get("sysfs", "/nonexistent")).glob("*/video4linux/video*")), None)
        node = node or "/dev/video0"

        def grab():
            import av
            with av.open(node, format="v4l2" if sys.platform.startswith("linux") else None) as cam:
                for frame in cam.decode(video=0):
                    path = ws / f"camera-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
                    frame.to_image().save(path, quality=90)
                    return path
        try:
            path = await asyncio.wait_for(asyncio.to_thread(grab), 20)
        except Exception as exc:
            raise DeviceError(f"The camera did not give a picture: {exc}") from None
        return f"Saved a picture from {d['name']} as {path}"


def serial_exchange(port, baud=115200, text=None, seconds=3.0):
    """Open a serial port, send `text` (and a newline) if given, and return what arrives within `seconds`."""
    if os.name == "nt":
        raise DeviceError("On Windows, serial ports are reached with run_command (PowerShell's SerialPort); "
                          f"the port is {port}")
    import termios
    import tty
    speeds = {9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400, 57600: termios.B57600,
              115200: termios.B115200, 230400: getattr(termios, "B230400", termios.B115200)}
    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except PermissionError:
        raise DeviceError(f"This user may not open {port}: on KimiOS this is given by itself; elsewhere join the "
                          "'dialout' group") from None
    except OSError as exc:
        raise DeviceError(f"{port} could not be opened: {exc}") from None
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        speed = speeds.get(int(baud), termios.B115200)
        attrs[4] = attrs[5] = speed
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        if text is not None:
            os.write(fd, (text.rstrip("\r\n") + "\r\n").encode())
        got, ends = b"", time.monotonic() + seconds
        while time.monotonic() < ends and len(got) < 64_000:
            try:
                chunk = os.read(fd, 4096)
                if chunk:
                    got += chunk
                    continue
            except BlockingIOError:
                pass
            time.sleep(0.05)
    finally:
        os.close(fd)
    shown = got.decode("utf-8", "replace").strip()
    return (f"Sent {text!r}. " if text is not None else "") + (f"Received from {port}:\n{shown}" if shown else
                                                                f"Nothing came from {port} in {seconds:g} s")
