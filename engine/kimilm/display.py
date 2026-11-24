"""Displays in detail (KimiLM 2.33.0): the monitor's own settings over DDC/CI, and the computer's side of the
picture (resolution, refresh, scale, rotation, position, on/off).

The monitor (external screens, over the HDMI/DisplayPort cable's DDC channel, with ddcutil):
  - `monitor_state()`: what this monitor says it supports (its capabilities string: every VCP feature, and for
    the ones with a fixed set of values, those values with the monitor's own names, such as its inputs) and the
    current value of the settings people use: brightness, contrast, sharpness, red/green/blue gain, speaker volume
    and mute, input, colour preset, picture mode, power mode.
  - `monitor_set()`: one setting by its name, checked against what the monitor supports (a value for a list
    setting must be one of the monitor's own; a level is kept within 0 and the monitor's maximum).
The computer's side (GNOME, KimiOS's desktop: Mutter's DisplayConfig over D-Bus, the interface GNOME Settings and
gdctl use, through busctl; wlroots compositors such as sway or cage: wlr-randr):
  - `layout()`: each screen, its modes, the current one, scale, rotation, position, primary, on or off.
  - `set_layout()`: mode, refresh, scale, rotation, position, primary, on/off for one screen. GNOME asks the person
    to keep a change ("Keep these display settings?") and puts the old one back after 20 seconds without an answer,
    so a picture nobody can see is never left behind.
Nothing here runs on Windows or macOS (their display settings are their own)."""
import json
import os
import re
import sys

from .devices import DeviceError, sh

# the settings people use, by name: (VCP code, kind) — C = a level 0..max, NC = one of a list of values
SETTINGS = {
    "brightness": ("10", "C"), "contrast": ("12", "C"), "sharpness": ("87", "C"),
    "red": ("16", "C"), "green": ("18", "C"), "blue": ("1A", "C"),
    "speaker_volume": ("62", "C"), "mute": ("8D", "NC"),
    "input": ("60", "NC"), "color_preset": ("14", "NC"), "picture_mode": ("DC", "NC"), "power": ("D6", "NC"),
}
LABELS = {"brightness": "Brightness", "contrast": "Contrast", "sharpness": "Sharpness", "red": "Red",
          "green": "Green", "blue": "Blue", "speaker_volume": "Speaker volume", "mute": "Speaker mute",
          "input": "Input", "color_preset": "Colour preset", "picture_mode": "Picture mode", "power": "Power"}
# MCCS's own names where a monitor lists a code without one
STANDARD_VALUES = {
    "8D": {"01": "muted", "02": "unmuted"},
    "D6": {"01": "on", "02": "standby", "03": "suspend", "04": "off (soft)", "05": "off (hard)"},
    "60": {"01": "VGA-1", "03": "DVI-1", "0f": "DisplayPort-1", "10": "DisplayPort-2", "11": "HDMI-1",
           "12": "HDMI-2", "1b": "USB-C"},
}


def supported():
    return os.name != "nt" and sys.platform != "darwin"


# ------------------------------------------------------------------------------------------------ the monitor
_FEATURE = re.compile(r"^\s*Feature:\s*([0-9A-Fa-f]{2})\s*\(([^)]*)\)")
_VALUE = re.compile(r"^\s*([0-9A-Fa-f]{2}):\s*(.+?)\s*$")


def parse_capabilities(text):
    """{code (upper hex): {"name", "values": {hex: name}}} from `ddcutil capabilities`."""
    feats, cur, in_values = {}, None, False
    for line in (text or "").splitlines():
        m = _FEATURE.match(line)
        if m:
            cur = m.group(1).upper()
            feats[cur] = {"name": m.group(2).strip(), "values": {}}
            in_values = False
            continue
        if cur and line.strip().startswith("Values:"):
            in_values = True
            rest = line.split("Values:", 1)[1].strip()
            for v in re.findall(r"\b[0-9A-Fa-f]{2}\b", rest.split("(")[0]):     # "Values: 01 02 (…)" on one line
                feats[cur]["values"].setdefault(v.lower(), "")
            continue
        if cur and in_values:
            v = _VALUE.match(line)
            if v:
                feats[cur]["values"][v.group(1).lower()] = v.group(2)
            elif line.strip() and not line.startswith(" " * 6):
                in_values = False
    return feats


def parse_getvcp(text):
    """{code: {"kind": "C", "current", "max"} | {"kind": "NC", "value": hex}} from `ddcutil getvcp … --terse`."""
    out = {}
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) < 3 or p[0] != "VCP":
            continue
        code = p[1].upper()
        if p[2] == "C" and len(p) >= 5:
            out[code] = {"kind": "C", "current": int(p[3]), "max": int(p[4])}
        elif p[2] in ("SNC", "CNC") and len(p) >= 4:
            vals = [x.lower().lstrip("x") for x in p[3:]]
            # CNC: mh ml sh sl; the value is the low byte (sl)
            out[code] = {"kind": "NC", "value": (vals[-1] if p[2] == "CNC" else vals[0]).zfill(2)[-2:]}
        elif p[2] == "ERR":
            out[code] = {"kind": "ERR"}
    return out


async def bus_for(app, port):
    """The i2c bus ddcutil reaches the monitor on DRM connector `port` (card1-HDMI-A-1) through, or None."""
    code, out = await sh(["ddcutil", "detect", "--terse"], 30, app)
    if code == 127:
        raise DeviceError("ddcutil is not installed")
    found = None
    for block in out.split("\n\n"):
        m = re.search(r"/dev/i2c-(\d+)", block)
        if not m:
            continue
        if port and port in block:
            return m.group(1)
        if "Invalid display" not in block and found is None:
            found = m.group(1)
    return found if not port else None


async def monitor_state(app, port):
    bus = await bus_for(app, port)
    if not bus:
        raise DeviceError("The monitor does not answer over DDC/CI: switch DDC/CI on in its own menu (often under "
                          "Setup, Management or System), and use a cable straight to the computer (some docks and "
                          "adapters do not pass it)")
    code, caps = await sh(["ddcutil", "--bus", bus, "capabilities"], 40, app)
    if code != 0:
        raise DeviceError("The monitor did not say what it supports: " + caps.strip()[-200:])
    feats = parse_capabilities(caps)
    codes = [c for c, _k in SETTINGS.values() if c in feats]
    current = {}
    if codes:
        code, out = await sh(["ddcutil", "--bus", bus, "getvcp", *codes, "--terse"], 40, app)
        current = parse_getvcp(out)
    model = re.search(r"^\s*Model:\s*(.+)$", caps, re.M)
    settings = []
    for name, (vcp, kind) in SETTINGS.items():
        if vcp not in feats:
            continue
        cur = current.get(vcp) or {}
        row = {"name": name, "label": LABELS[name], "code": vcp, "kind": kind}
        if kind == "C":
            row.update(current=cur.get("current"), max=cur.get("max") or 100)
        else:
            vals = dict(STANDARD_VALUES.get(vcp, {}))
            vals.update({k: v for k, v in feats[vcp]["values"].items() if v})
            listed = list(feats[vcp]["values"]) or list(vals)
            row.update(value=cur.get("value"), options=[{"value": v, "name": vals.get(v) or f"x{v}"} for v in listed])
            row["current_name"] = vals.get(cur.get("value") or "", None)
        settings.append(row)
    other = [{"code": c, "name": f["name"]} for c, f in sorted(feats.items())
             if c not in {v for v, _ in SETTINGS.values()}]
    return {"bus": bus, "model": model.group(1).strip() if model else None, "settings": settings, "other": other}


async def monitor_set(app, port, setting, value):
    """Set one setting by name; returns the sentence to show."""
    state = await monitor_state(app, port)
    row = next((s for s in state["settings"] if s["name"] == setting), None)
    if row is None:
        names = ", ".join(s["name"] for s in state["settings"]) or "nothing"
        raise DeviceError(f"This monitor does not offer {setting!r} over DDC/CI. It offers: {names}")
    if row["kind"] == "C":
        try:
            v = int(round(float(str(value).rstrip("%"))))
        except (TypeError, ValueError):
            raise DeviceError(f"Give {row['label'].lower()} as a number from 0 to {row['max']}") from None
        v = max(0, min(int(row["max"]), v))
        arg, shown = str(v), str(v)
    else:
        want = str(value).strip().lower()
        opt = next((o for o in row["options"] if want in (o["value"], "x" + o["value"], o["name"].lower())), None)
        if opt is None:                         # "hdmi 1", "HDMI1", "display port" …
            flat = re.sub(r"[\s_-]", "", want)
            opt = next((o for o in row["options"] if re.sub(r"[\s_-]", "", o["name"].lower()) == flat), None)
        if opt is None:
            raise DeviceError(f"{row['label']} can be: " + ", ".join(o["name"] for o in row["options"]))
        arg, shown = "x" + opt["value"], opt["name"]
    code, out = await sh(["ddcutil", "--bus", state["bus"], "setvcp", row["code"], arg], 30, app)
    if code != 0:
        raise DeviceError(f"The monitor did not take {row['label'].lower()} = {shown}: " + out.strip()[-200:])
    return f"{row['label']} set to {shown}" + (f" of {row['max']}" if row["kind"] == "C" else "")


# ------------------------------------------------------------------------------------------------ the computer side
MUTTER = ["org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig", "org.gnome.Mutter.DisplayConfig"]
ROTATIONS = {0: "0", 1: "90", 2: "180", 3: "270"}
TRANSFORM_OF = {"0": 0, "normal": 0, "90": 1, "180": 2, "270": 3}
WLR_TRANSFORM = {0: "normal", 1: "90", 2: "180", 3: "270"}


def screen_key(name):
    """One name for a connector whoever names it: card1-HDMI-A-1 (the kernel), HDMI-A-1 (wlroots), HDMI-1 (GNOME)."""
    n = re.sub(r"^card\d+-", "", name or "")
    return re.sub(r"^HDMI-[AB]-", "HDMI-", n)


def mode_name(w, h, rate):
    return f"{w}x{h}@" + f"{rate:.2f}".rstrip("0").rstrip(".")


def _v(props, key, default=None):
    x = (props or {}).get(key)
    if isinstance(x, dict) and "data" in x:
        return x["data"]
    return default if x is None else x


def parse_mutter(doc):
    """GetCurrentState (busctl --json=short) → {"serial", "layout_mode", "outputs"}."""
    serial, monitors, logical, props = doc["data"]
    where = {}
    for i, (x, y, scale, transform, primary, mons, _p) in enumerate(logical):
        for m in mons:
            where[m[0]] = {"x": x, "y": y, "scale": scale, "transform": transform, "primary": primary, "group": i}
    outs = []
    for (conn, vendor, product, _serial), modes, mprops in monitors:
        ms = [{"id": mid, "mode": mode_name(w, h, rate), "width": w, "height": h, "refresh": round(rate, 2),
               "current": bool(_v(mp, "is-current", False)), "preferred": bool(_v(mp, "is-preferred", False)),
               "preferred_scale": pref, "scales": [round(x, 4) for x in scales]}
              for (mid, w, h, rate, pref, scales, mp) in modes]
        lm = where.get(conn)
        cur = next((m for m in ms if m["current"]), None) if lm else None
        outs.append({"name": conn, "description": _v(mprops, "display-name") or f"{vendor} {product}".strip(),
                     "builtin": bool(_v(mprops, "is-builtin", False)), "enabled": lm is not None,
                     "mode": cur["mode"] if cur else None, "mode_id": cur["id"] if cur else None, "modes": ms,
                     "scale": lm["scale"] if lm else None,
                     "rotation": ROTATIONS.get(lm["transform"] % 4) if lm else None,
                     "transform": lm["transform"] if lm else None,
                     "position": {"x": lm["x"], "y": lm["y"]} if lm else None,
                     "primary": bool(lm and lm["primary"]), "group": lm["group"] if lm else None})
    return {"serial": serial, "layout_mode": _v(props, "layout-mode", 1), "outputs": outs}


def parse_wlr_randr(text):
    outs = []
    for o in json.loads(text or "[]"):
        ms = [{"id": None, "mode": mode_name(m["width"], m["height"], m.get("refresh", 0)), "width": m["width"],
               "height": m["height"], "refresh": round(m.get("refresh", 0), 2), "current": bool(m.get("current")),
               "preferred": bool(m.get("preferred")), "scales": []} for m in o.get("modes", [])]
        on = bool(o.get("enabled"))
        cur = next((m for m in ms if m["current"]), None) if on else None
        t = {"normal": 0, "90": 1, "180": 2, "270": 3}.get(o.get("transform"), 0)
        outs.append({"name": o["name"], "description": o.get("description"), "builtin": o["name"].startswith("eDP"),
                     "enabled": on, "mode": cur["mode"] if cur else None, "mode_id": None, "modes": ms,
                     "scale": o.get("scale") if on else None, "rotation": ROTATIONS[t] if on else None,
                     "transform": t if on else None, "position": o.get("position") if on else None,
                     "primary": False, "group": None})
    return outs


def which_compositor():
    desk = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "gnome" in desk:
        return "gnome"
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wlroots"
    return None


async def layout(app):
    """{"how": "gnome" | "wlr-randr" | None, "outputs": [...], "serial", "layout_mode", "note"}"""
    comp = which_compositor()
    if comp in ("gnome", None):
        code, out = await sh(["busctl", "--user", "--json=short", "call", *MUTTER, "GetCurrentState"], 15, app)
        if code == 0:
            try:
                return {"how": "gnome", **parse_mutter(json.loads(out))}
            except (ValueError, KeyError, TypeError):
                pass
    if comp in ("wlroots", None):
        code, out = await sh(["wlr-randr", "--json"], 15, app)
        if code == 0:
            try:
                return {"how": "wlr-randr", "outputs": parse_wlr_randr(out)}
            except ValueError:
                pass
    return {"how": None, "outputs": [], "note": "Screens can be read and changed here in a GNOME session (KimiOS's "
                                                "desktop) or a wlroots one (sway, cage) with wlr-randr"}


def find_screen(state, port):
    k = screen_key(port)
    return next((o for o in state["outputs"] if screen_key(o["name"]) == k), None)


def pick_mode(modes, mode=None, refresh=None, current=None):
    """The mode a person means by "1920x1080", "1920x1080@75", "2560x1440 60" or only a refresh rate."""
    w = h = None
    if mode:
        m = re.match(r"^\s*(\d+)\s*[x×]\s*(\d+)\s*(?:[@ ]\s*([\d.]+))?\s*(?:hz)?\s*$", str(mode), re.I)
        if not m:
            raise DeviceError("Give the resolution as width x height, such as 1920x1080 or 1920x1080@75")
        w, h = int(m.group(1)), int(m.group(2))
        refresh = refresh if refresh not in (None, "") else m.group(3)
    elif current:
        w, h = current["width"], current["height"]
    same = [x for x in modes if (w is None or (x["width"], x["height"]) == (w, h))]
    if not same:
        raise DeviceError("It cannot show " + str(mode) + "; it can: " + ", ".join(
            dict.fromkeys(f"{x['width']}x{x['height']}" for x in modes)))
    if refresh not in (None, ""):
        try:
            r = float(str(refresh).lower().rstrip("hz").strip())
        except ValueError:
            raise DeviceError("Give the refresh rate in Hz, such as 60 or 75") from None
        best = min(same, key=lambda x: abs(x["refresh"] - r))
        if abs(best["refresh"] - r) > 1.5:
            raise DeviceError(f"At {w}x{h} it can refresh at: "
                              + ", ".join(dict.fromkeys(f"{round(x['refresh'])} Hz" for x in same)))
        return best
    return next((x for x in same if x["preferred"]), None) or max(same, key=lambda x: x["refresh"])


def logical_size(mode, scale, transform, layout_mode=1):
    w, h = mode["width"], mode["height"]
    if transform % 2:
        w, h = h, w
    if layout_mode == 1 and scale:
        w, h = round(w / scale), round(h / scale)
    return w, h


def _mode_of(o):
    return next((m for m in o["modes"] if m["mode"] == o["mode"]), None) or (o["modes"][0] if o["modes"] else None)


def plan(state, port, mode=None, refresh=None, scale=None, rotation=None, position=None, enabled=None,
         primary=None, normalize=True):
    """The new layout as a list of screens {name, mode (a mode dict), scale, transform, x, y, primary, group}, and
    the words for what changed. Pure: no program is run."""
    target = find_screen(state, port)
    if target is None:
        raise DeviceError(f"No screen {screen_key(port)} in this session; there are: "
                          + ", ".join(o["name"] for o in state["outputs"]))
    lmode = state.get("layout_mode", 1)
    screens = [{"name": o["name"], "mode": _mode_of(o), "scale": o["scale"] or 1, "transform": o["transform"] or 0,
                "x": o["position"]["x"], "y": o["position"]["y"], "primary": o["primary"], "group": o["group"]}
               for o in state["outputs"] if o["enabled"]]
    me = next((s for s in screens if s["name"] == target["name"]), None)
    said = []
    if enabled is not None:
        on = str(enabled).strip().lower() in ("1", "true", "yes", "on")
        if not on:
            if me is None:
                raise DeviceError(f"{target['name']} is already off")
            if len(screens) == 1:
                raise DeviceError("That is the only screen that is on: turning it off would leave nothing to see")
            screens.remove(me)
            if me["primary"]:
                screens[0]["primary"] = True
            me = None
            said.append("off")
        elif me is None:
            m = pick_mode(target["modes"], mode, refresh)
            sc = m.get("preferred_scale") or 1
            right = max((s["x"] + logical_size(s["mode"], s["scale"], s["transform"], lmode)[0] for s in screens),
                        default=0)
            me = {"name": target["name"], "mode": m, "scale": sc, "transform": 0, "x": right, "y": 0,
                  "primary": not screens, "group": None}
            screens.append(me)
            said.append(f"on, {m['width']}×{m['height']} at {round(m['refresh'])} Hz")
            mode = refresh = None
    if me is None and any(v not in (None, "") for v in (mode, refresh, scale, rotation, position, primary)):
        raise DeviceError(f"{target['name']} is off: turn it on first (on=true)")
    if me is not None:
        if mode not in (None, "") or refresh not in (None, ""):
            m = pick_mode(target["modes"], mode, refresh, current=me["mode"])
            me["mode"] = m
            said.append(f"{m['width']}×{m['height']} at {round(m['refresh'])} Hz")
        if scale not in (None, ""):
            try:
                want = float(str(scale).rstrip("%")) / (100 if str(scale).endswith("%") else 1)
            except ValueError:
                raise DeviceError("Give the scale as a number such as 1, 1.25 or 2 (or 125%)") from None
            if not 0.5 <= want <= 4:
                raise DeviceError("The scale must be between 0.5 and 4")
            offered = me["mode"].get("scales") or []
            got = min(offered, key=lambda x: abs(x - want)) if offered else want
            me["scale"] = got
            said.append(f"scale {got:g}" + (f" (the nearest it offers to {want:g})" if abs(got - want) > 0.01 else ""))
        elif me["mode"].get("scales") and not any(abs(me["scale"] - x) < 0.01 for x in me["mode"]["scales"]):
            me["scale"] = min(me["mode"]["scales"], key=lambda x: abs(x - me["scale"]))
        if rotation not in (None, ""):
            t = TRANSFORM_OF.get(str(rotation).strip().lower().rstrip("°"))
            if t is None:
                raise DeviceError("Rotation is 0, 90, 180 or 270 degrees")
            me["transform"] = t
            said.append(f"rotated {ROTATIONS[t]}°")
        if primary not in (None, ""):
            if str(primary).strip().lower() in ("1", "true", "yes", "on"):
                for s in screens:
                    s["primary"] = s is me
                said.append("main screen")
        if position not in (None, ""):
            pos = str(position).strip().lower()
            rel = re.match(r"^(left-of|right-of|above|below|left of|right of)\s+(\S+)$", pos)
            if rel:
                ref = next((s for s in screens if screen_key(s["name"]) == screen_key(rel.group(2).upper())
                            or s["name"].lower() == rel.group(2)), None)
                if not ref or ref is me:
                    raise DeviceError(f"No other screen {rel.group(2)} to place it next to")
                rw, rh = logical_size(ref["mode"], ref["scale"], ref["transform"], lmode)
                ow, oh = logical_size(me["mode"], me["scale"], me["transform"], lmode)
                where = rel.group(1).replace(" ", "-")
                me["x"], me["y"] = {"right-of": (ref["x"] + rw, ref["y"]), "left-of": (ref["x"] - ow, ref["y"]),
                                    "above": (ref["x"], ref["y"] - oh), "below": (ref["x"], ref["y"] + rh)}[where]
                said.append(f"{where.replace('-', ' ')} {ref['name']}")
            else:
                m = re.match(r"^(-?\d+)\s*,\s*(-?\d+)$", pos)
                if not m:
                    raise DeviceError("Position is x,y (such as 1920,0) or right-of / left-of / above / below "
                                      "another screen (such as right-of eDP-1)")
                me["x"], me["y"] = int(m.group(1)), int(m.group(2))
                said.append(f"at {me['x']},{me['y']}")
    if not said:
        raise DeviceError("Say what to change: mode, refresh, scale, rotation, position, primary or on")
    # screens shown together (mirrored) move together; then nothing may sit left of or above 0,0 (GNOME's rule)
    if me is not None and me["group"] is not None:
        for s in screens:
            if s is not me and s["group"] == me["group"]:
                s["x"], s["y"], s["scale"], s["transform"] = me["x"], me["y"], me["scale"], me["transform"]
    dx, dy = (min(s["x"] for s in screens), min(s["y"] for s in screens)) if normalize else (0, 0)
    for s in screens:
        s["x"] -= dx
        s["y"] -= dy
    return screens, target["name"] + ": " + ", ".join(said)


def mutter_argv(serial, screens, method=2):
    """ApplyMonitorsConfig for busctl: screens shown together share one logical monitor."""
    groups = {}
    for s in screens:
        key = ("g", s["group"]) if s["group"] is not None else ("s", s["name"])
        groups.setdefault(key, []).append(s)
    argv = ["busctl", "--user", "call", *MUTTER, "ApplyMonitorsConfig", "uua(iiduba(ssa{sv}))a{sv}",
            str(serial), str(method), str(len(groups))]
    for members in groups.values():
        s = members[0]
        argv += [str(s["x"]), str(s["y"]), repr(float(s["scale"])), str(s["transform"]),
                 "true" if any(m["primary"] for m in members) else "false", str(len(members))]
        for m in members:
            argv += [m["name"], m["mode"]["id"], "0"]
    return argv + ["0"]


async def set_layout(app, port, **change):
    state = await layout(app)
    if not state["how"]:
        raise DeviceError(state.get("note") or "Screens cannot be changed from here in this session")
    screens, said = plan(state, port, normalize=state["how"] == "gnome", **change)
    if state["how"] == "gnome":
        code, text = await sh(mutter_argv(state["serial"], screens), 20, app)
        if code != 0:
            raise DeviceError("GNOME did not take it: " + text.strip()[-240:])
        return said + ". GNOME asks on the screen whether to keep it; without an answer it goes back in 20 seconds"
    name = find_screen(state, port)["name"]
    me = next((s for s in screens if s["name"] == name), None)
    argv = ["wlr-randr", "--output", name]
    if me is None:
        argv.append("--off")
    else:
        m = me["mode"]
        argv += ["--on", "--mode", f"{m['width']}x{m['height']}@{m['refresh']}Hz", "--scale", f"{me['scale']:g}",
                 "--transform", WLR_TRANSFORM[me["transform"]], "--pos", f"{me['x']},{me['y']}"]
    code, text = await sh(argv, 20, app)
    if code != 0:
        raise DeviceError("The screen was not changed: " + text.strip()[-200:])
    return said
