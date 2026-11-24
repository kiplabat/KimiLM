"""Attached devices as tools (2.29.0): see what is plugged in and use it (devices.py does the work).

`devices` with no device lists what is attached; with one, says everything about it: what it is, its drivers,
the programs that control it and whether they are installed, who may open it, and the actions it offers; with an
action as well, does it (one tool, not two: a small model reads every tool in its list). Looking is free. Actions that change something ask unless the chat or task
approves on its own; those that may move or switch something (writing to a board) ask every time unless the user
marked that device as safe to control."""
import json

from ..devices import DeviceError
from . import Tool, ToolError

PHYSICAL = Tool("device_physical", "", {}, None, "devices", changes=True,
                label="Control a device that may move or switch something")
CHANGE = Tool("device_change", "", {}, None, "devices", changes=True, label="Change something on a device")


def short(d):
    bits = [d["kind"], d.get("connection") or ""]
    if d.get("state"):
        bits.append(d["state"])
    if d.get("battery") is not None:
        bits.append(f"battery {d['battery']} %")
    return f"- {d['id']}: {d['name']} ({', '.join(b for b in bits if b)})"


async def devices(ctx, device=""):
    dv = ctx.app.devices
    try:
        if not device:
            found = await dv.look(fresh=True)
            if not found:
                return "No devices were found attached to this computer."
            outside = [d for d in found if not d.get("internal")]
            inside = [d for d in found if d.get("internal")]
            return ("Attached:\n" + ("\n".join(short(d) for d in outside) or "(nothing plugged in)")
                    + ("\n\nBuilt in:\n" + "\n".join(short(d) for d in inside) if inside else "")
                    + "\n\nCall devices(device=<id>) for its drivers, programs and what it can do.")
        d = await dv.describe(device)
    except DeviceError as exc:
        raise ToolError(str(exc)) from None
    lines = [f"{d['name']} — {d.get('what') or d['kind']} ({d['id']}, {d.get('connection') or ''})"]
    if d.get("state"):
        lines.append(f"State: {d['state']}")
    lines.append("Kernel driver: " + (", ".join(d.get("drivers") or []) or "none bound"))
    if d.get("modules"):
        lines.append("Kernel modules that match: " + ", ".join(
            f"{m['module']} ({'loaded' if m['loaded'] else 'not loaded'})" for m in d["modules"]))
    for p in d.get("programs") or []:
        lines.append(f"Program: {p['program']} for {p['for']}: "
                     + ("installed" if p["installed"] else f"not installed (installed on first use: {p['install']})"))
    if d.get("access"):
        lines.append("Access: " + ", ".join(f"{k} {v}" for k, v in d["access"].items()))
    details = {k: v for k, v in (d.get("details") or {}).items() if v not in (None, [], "")}
    if details:
        lines.append("Details: " + json.dumps(details, ensure_ascii=False)[:600])
    if d.get("ports"):
        lines.append("Serial ports: " + ", ".join(d["ports"]))
    lines.append("Actions (devices with device and action):")
    for a in d["actions"]:
        args = ", ".join(f"{k}: {v}" for k, v in a["args"].items())
        lines.append(f"- {a['name']}: {a['about']}" + (f" [{args}]" if args else "")
                     + (" — asks you every time" if a["physical"] and not d["trusted"] else ""))
    if d.get("next"):
        lines.append("Next: " + d["next"])
    return "\n".join(lines)


async def control(ctx, device, action, arguments=None):
    dv = ctx.app.devices
    args = arguments if isinstance(arguments, dict) else {}
    try:
        d = await dv.describe(device)
    except DeviceError as exc:
        raise ToolError(str(exc)) from None
    act = next((a for a in d["actions"] if a["name"] == action), None)
    if act and act["physical"] and not d["trusted"]:
        # KimiOS principle 5: what may move, heat or switch something is approved by a person, every time
        ok = await ctx.approve(PHYSICAL, {"device": d["name"], "action": action, **args},
                               f"{d['name']}: {action} {json.dumps(args, ensure_ascii=False)[:200]}. This may make "
                               "the device move or switch something. Go ahead?", ignore_auto=True)
        if not ok:
            raise ToolError("The user did not allow this action on the device")
    elif act and act["changes"]:                     # looking (info, a screenshot, a reading) never asks
        if not await ctx.approve(CHANGE, {"device": d["name"], "action": action, **args},
                                 f"{d['name']}: {action} {json.dumps(args, ensure_ascii=False)[:200]}"):
            raise ToolError("The user declined this action on the device")
    for key in ("file", "apk"):                      # files the action reads come from the chat's folders
        if args.get(key):
            args[key] = str(ctx.resolve(args[key]))
    try:
        return await dv.control(d["id"], action, args, workspace=ctx.primary, on_status=ctx.progress)
    except DeviceError as exc:
        raise ToolError(str(exc)) from None


async def devices_tool(ctx, device="", action="", arguments=None):
    if action and action not in ("look", "info") and device:
        return await control(ctx, device, action, arguments)
    return await devices(ctx, device)


TOOLS = [
    Tool("devices", "The devices attached to this computer: phones, Raspberry Pi and other boards, sensors, "
         "displays, keyboards and mice (dongles too), sound devices, Bluetooth, cameras, serial ports. No arguments: "
         "a list. device (an id from the list, or its name): what drives it (kernel driver, modules, programs), "
         "whether this user may open it, and its actions. device and action: do it, with its arguments — on a phone "
         "phone_info, list_files, pull, push, screenshot, open_url, install_app, shell; on a display brightness; on "
         "sound volume, mute, make_default; on Bluetooth connect, disconnect; on a serial port or board serial_read, "
         "serial_write, flash_uf2; on a sensor read; on a camera photo; battery. A program an action needs is "
         "installed on first use.",
         {"type": "object", "properties": {"device": {"type": "string"}, "action": {"type": "string"},
                                           "arguments": {"type": "object", "description": "the action's arguments, "
                                                         "e.g. {\"path\": \"/sdcard/DCIM\"}"}}},
         devices_tool, "devices", label="Devices",
         summary=lambda a: (f"{a.get('device')}: {a.get('action')} "
                            f"{json.dumps(a.get('arguments') or {}, ensure_ascii=False)[:200]}" if a.get("action")
                            else f"Look at {a.get('device') or 'attached devices'}")),
]
