"""The App builder's tool (2.34.0): save the app this chat is building (apps.py does the work).

save_app checks the draft folder (index.html there, no syntax errors in its scripts, nothing it cannot use) and
keeps it as the app's next version, which becomes the one in use; earlier versions stay. When it cannot be saved
it says what to fix, so the builder can fix it and call it again."""
from ..apps import AppError
from . import Tool, ToolError


async def save_app(ctx, notes=""):
    try:
        m, got = ctx.app.apps.saved_by_tool(ctx.chat["id"], notes, model=ctx.model_id or "")
    except AppError as exc:
        raise ToolError(str(exc)) from None
    lines = [f"Saved “{m['name']}” as version {got['version']}: it is the one in use now (the person opens it from "
             f"{'Games' if m['kind'] == 'game' else 'the device’s Control panels'} or Apps)."]
    if got.get("device_part"):
        lines.append(f"It has a part for the device ({got['device_part']['target']}): the person installs it from "
                     "the panel.")
    if got.get("warnings"):
        lines.append("Worth fixing (then save again):\n" + "\n".join(f"- {w}" for w in got["warnings"]))
    # 2.35.0: tried for a few seconds before the person sees it; what it showed goes back to the builder
    ctx.reporter()("Trying the app for a few seconds…")
    try:
        tried = await ctx.app.apps.preview(m["id"], got["version"])
    except Exception as exc:                       # noqa: BLE001 — never lose a save over a preview
        tried = {"skipped": str(exc)[:200]}
    lines.append(ctx.app.apps.preview_text(tried))
    return "\n".join(lines)


async def build_app(ctx, description, kind="game", name="", dims="", device=""):
    """2.35.0: from any chat: start the App builder on a game or a device's control panel, in a chat of its own."""
    apps = ctx.app.apps
    kind = "device" if str(kind).lower().startswith(("dev", "panel", "control")) else "game"
    try:
        dev = await apps.find_device(device) if kind == "device" else None
        got = apps.build(prompt=description, name=name, kind=kind, device=dev,
                         dims=str(dims).upper() if str(dims).upper() in ("2D", "3D") else None,
                         project_id=(ctx.chat or {}).get("project_id"))
    except AppError as exc:
        raise ToolError(str(exc)) from None
    a = got["app"]
    where = "Games and Apps" if kind == "game" else f"{dev['name']} → Control panels, and Apps"
    return (f"The App builder is making “{a['name']}” in a chat of its own (chat {got['chat_id']}). It is tried for a "
            f"few seconds when it is saved, and opens from {where}. Tell the person; do not build it yourself.")


TOOLS = [
    Tool("build_app", "Have an app built: a game (2D or 3D, the computer as a player) or a control panel for one of "
         "the attached devices (sliders, switches, live charts). The App builder makes it in its own chat; it is kept "
         "in Apps, versioned and rebuilt later. Use it when the person asks for a game, an app or a panel to control "
         "a device.",
         {"type": "object", "properties": {
             "description": {"type": "string", "description": "what the app does, in the person's words and detail"},
             "kind": {"type": "string", "enum": ["game", "device"], "description": "game, or device (a control panel)"},
             "name": {"type": "string", "description": "a short name (optional)"},
             "dims": {"type": "string", "enum": ["2D", "3D"], "description": "games: 2D or 3D (optional)"},
             "device": {"type": "string", "description": "control panels: the device's id or name, e.g. 'the monitor'"}},
          "required": ["description"]},
         build_app, "automation", changes=True, label="Build an app",
         summary=lambda a: f"Build {'a control panel' if str(a.get('kind')) == 'device' else 'a game'}: "
                           f"{str(a.get('description') or '')[:100]}"),
    Tool("save_app", "Save the app you are building (index.html and the files beside it in your folder) as its next "
         "version. It checks the scripts first and says what to fix if it cannot be saved. Call it when the app "
         "works, and again after each fix.",
         {"type": "object", "properties": {"notes": {"type": "string", "description": "one line: what this version "
                                                                                       "does or changed"}}},
         save_app, "apps", changes=False, label="Save the app",
         summary=lambda a: f"Save the app: {str(a.get('notes') or '')[:120]}"),
]
