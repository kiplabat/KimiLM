"""Setting the computer up for the work (2.29.0): make sure a program is there, and install it if not.

Every agent has these two tools, whatever its harness or the tools its step was given: a missing program is a
step of the work, not a reason to stop (installer.py says how each kind of program is installed). Looking is
free; installing changes the computer, so it asks unless the chat or the task approves on its own. One tool, not
two: every tool in a small model's list costs reading time."""
from .. import installer
from . import Tool, ToolDenied, ToolError

KINDS = ["auto", "command", "python", "node", "app", "browser"]


INSTALL = Tool("install_program", "", {}, None, "setup", changes=True, label="Install a program")


async def install_program(ctx, name, kind="auto", package="", why="", check_only=False):
    """Says whether the program is there; installs it when it is not (that asks unless approvals are automatic)."""
    info = await installer.find(ctx.app, name, kind)
    if info["found"] and not package:
        return (f"{info['name']} is installed ({info['kind']}): {info['found']}"
                + (f" — {info['version']}" if info.get("version") else "") + ". Use it.")
    if check_only:
        return f"{info['name']} is not installed ({info['kind']}); it would be installed with {info['how']}."
    if not ctx.app.db.setting("install_programs", True):
        raise ToolError("Installing programs is switched off in Settings → Teams and long tasks; say what is "
                        "needed instead")
    if not await ctx.approve(INSTALL, {"name": name, "kind": info["kind"], "how": info["how"]},
                             f"Install {info['name']} ({info['how']})" + (f": {why}" if why else "")):
        raise ToolDenied("The user declined installing it")
    try:
        out = await installer.install(ctx.app, name, kind, package, on_status=ctx.progress, owner=ctx.job_id,
                                      by=(ctx.chat.get("title") or "")[:80])
    except installer.InstallError as exc:
        raise ToolError(str(exc)) from None
    where = out.get("found") or out.get("package")
    return (f"Installed {out['name']} with {out['how']}: {where}"
            + (f" ({out['version']})" if out.get("version") else "")
            + ". It is on the PATH of run_command"
            + (" and on run_python's import path" if out["kind"] == "python" else "")
            + ". Carry on with the work.")


TOOLS = [
    Tool("install_program", "Make sure a program the work needs is on this computer, and install it if it is not, "
         "then carry on: a command-line tool (adb, ffmpeg, nmap, testssl), a Python package (kind python, for "
         "run_python), a Node package (kind node), a desktop app (kind app: a Flathub id such as org.gimp.GIMP, or a "
         "winget id), or the browser the browser tools drive (kind browser). KimiLM picks the right way for this "
         "computer (on KimiOS a toolbox, no password). package: the package's name when it differs from the "
         "command's. check_only: only say whether it is there.",
         {"type": "object", "properties": {"name": {"type": "string"}, "kind": {"type": "string", "enum": KINDS},
                                           "package": {"type": "string"},
                                           "why": {"type": "string", "description": "what it is needed for"},
                                           "check_only": {"type": "boolean"}},
          "required": ["name"]},
         install_program, "setup", label="Install a program",
         summary=lambda a: f"Install {a.get('name')}" + (f" ({a.get('kind')})" if a.get("kind") not in (None, "", "auto")
                                                          else "") + (f": {a.get('why')}" if a.get("why") else "")),
]
