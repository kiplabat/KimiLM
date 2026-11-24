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
    return "\n".join(lines)


TOOLS = [
    Tool("save_app", "Save the app you are building (index.html and the files beside it in your folder) as its next "
         "version. It checks the scripts first and says what to fix if it cannot be saved. Call it when the app "
         "works, and again after each fix.",
         {"type": "object", "properties": {"notes": {"type": "string", "description": "one line: what this version "
                                                                                       "does or changed"}}},
         save_app, "apps", changes=False, label="Save the app",
         summary=lambda a: f"Save the app: {str(a.get('notes') or '')[:120]}"),
]
