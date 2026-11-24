"""Run code: Python with the app's own Python (pandas, matplotlib, openpyxl are included), and commands in a shell
(PowerShell on Windows) for tests, git and build tools. Both run in the chat's folders. They are not sandboxed, so
they always ask first unless the chat/task auto-approves.

When code fails, the result points at the failing line of the script with a few lines around it, so a model can
fix its own code and run it again (the loop guard allows re-running after a change).

2.19.0: both run through `procs.Procs.run`: a program is ended with everything it started when it runs too long
or the agent's task ends; it runs at low priority, a few at a time; the time limit has a ceiling. In run_python,
`from kimilm import call` calls the agent's connected services from the code (`pyhelper/kimilm.py`)."""
import asyncio
import os
import re
import time

from .. import installer
from ..procs import HELPERS, python_argv
from . import Tool, ToolError

OUTPUT_LIMIT = 9000


def _snapshot(folder):
    out = {}
    try:
        for p in folder.rglob("*"):
            if p.is_file() and ".runs" not in p.parts:
                out[str(p)] = p.stat().st_mtime
                if len(out) > 5000:
                    break
    except OSError:
        pass
    return out


def limit_of(ctx, asked):
    """The time a program may run: what the call asks, never more than `code_timeout_max_seconds` (2.19.0: a
    model once asked for hours)."""
    usual = int(ctx.app.db.setting("python_timeout_seconds", 180) or 180)
    most = int(ctx.app.db.setting("code_timeout_max_seconds", 900) or 900)
    try:
        asked = int(asked) if asked not in (None, "") else usual
    except (TypeError, ValueError):
        asked = usual
    return max(5, min(asked, most))


def owner_of(ctx):
    return ctx.job_id


def waiting(ctx):
    def said(text):
        if text is None:
            ctx.progress("Waiting for another program to finish (at most "
                         f"{ctx.app.db.setting('code_runs_at_once', 2)} run at once)…")
    return said


async def run_python(ctx, code, timeout=None):
    timeout = limit_of(ctx, timeout)
    runs = ctx.workspace / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    script = runs / f"run_{int(time.time() * 1000)}.py"
    script.write_text(code, encoding="utf-8")
    cwd = ctx.primary
    before = await asyncio.to_thread(_snapshot, cwd)
    env = installer.with_path(ctx.app, {**os.environ, "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8",
                                        "PYTHONUTF8": "1", "KIMILM_WORKSPACE": str(ctx.workspace)})
    env.pop("KIMILM_TOKEN", None)
    bridge = ctx.app.bridge.open(ctx) if getattr(ctx, "turn", None) is not None else None
    if bridge:                                         # kimilm.call(...) reaches this agent's connectors
        env.update(bridge["env"])
    ctx.emit("status", text="Running Python…")
    try:
        argv = python_argv(script, HELPERS, extra=[installer.python_dir(ctx.app)])   # packages agents installed
        code_, out = await ctx.app.procs.run(argv, cwd=cwd, env=env, timeout=timeout,
                                             kind="python", label=(code.strip().splitlines() or [""])[0],
                                             owner=owner_of(ctx), on_line=waiting(ctx))
    except asyncio.TimeoutError:
        raise ToolError(f"The code ran longer than {timeout} seconds and was stopped, with every program it "
                        "started. Make it faster or do less in one run (save results to files and continue from "
                        "them), or pass a larger timeout if the work really needs it.") from None
    finally:
        if bridge:
            ctx.app.bridge.close(bridge["token"])
    after = await asyncio.to_thread(_snapshot, cwd)
    changed = [p for p, t in after.items() if before.get(p) != t]
    if len(out) > OUTPUT_LIMIT:
        out = out[:OUTPUT_LIMIT // 2] + "\n…(output trimmed)…\n" + out[-OUTPUT_LIMIT // 2:]
    for p in changed[:20]:
        ctx.emit("file_changed", path=p, action="written")
    files = ("\nFiles created/changed:\n" + "\n".join(changed[:20])) if changed else ""
    if code_ != 0:
        return (f"FAILED (exit code {code_})\n{out.strip() or '(no output)'}{where_it_failed(out, script, code)}"
                f"{files}\nRead the error, fix the cause in your code, and run it again (do not repeat the same code).")
    return f"exit code 0\n{out.strip() or '(no output)'}{files}"


TRACE_LINE = re.compile(r'File "([^"]+)", line (\d+)')


def where_it_failed(stderr, script, code):
    """The failing line of the script with its neighbours, and a hint for common errors."""
    lines = code.splitlines()
    hits = [int(n) for f, n in TRACE_LINE.findall(stderr or "") if os.path.basename(f) == script.name]
    out = ""
    if hits and 0 < hits[-1] <= len(lines):
        n = hits[-1]
        around = [f"{'>>' if i == n else '  '} {i:>4}  {lines[i - 1]}" for i in range(max(1, n - 3), min(len(lines), n + 2) + 1)]
        out += f"\n\nThe error is at line {n} of your code:\n" + "\n".join(around)
    missing = re.search(r"No module named '([\w.]+)'", stderr or "")
    if missing:
        out += (f"\nHint: the module '{missing.group(1)}' is not installed in the app's Python. Install it with "
                f"install_program(name='{missing.group(1).split('.')[0]}', kind='python') and run the code again.")
    if "FileNotFoundError" in (stderr or ""):
        out += "\nHint: paths are relative to the chat's main folder; list_files shows what is there."
    return out


async def run_command(ctx, command, folder="", timeout=None):
    timeout = limit_of(ctx, timeout)
    cwd = ctx.resolve(folder, write=True) if folder else ctx.primary
    if not cwd.is_dir():
        raise ToolError(f"{cwd} is not a folder")
    env = installer.with_path(ctx.app, {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                                        "KIMILM_WORKSPACE": str(ctx.workspace)})
    env.pop("KIMILM_TOKEN", None)
    if os.name == "nt":
        argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
                + command]
    else:
        argv = ["/bin/bash", "-lc", command]
    ctx.emit("status", text=f"Running: {command[:80]}")
    before = await asyncio.to_thread(_snapshot, cwd)
    try:
        code_, out = await ctx.app.procs.run(argv, cwd=cwd, env=env, timeout=timeout, kind="command",
                                             label=command, owner=owner_of(ctx), on_line=waiting(ctx))
    except asyncio.TimeoutError:
        raise ToolError(f"The command ran longer than {timeout} seconds and was stopped, with every program it "
                        "started (it may be waiting for input; pass options that make it non-interactive)") from None
    except FileNotFoundError as exc:
        raise ToolError(f"Could not start the shell: {exc}") from exc
    after = await asyncio.to_thread(_snapshot, cwd)
    changed = [p for p, t in after.items() if before.get(p) != t]
    for p in changed[:20]:
        ctx.emit("file_changed", path=p, action="written")
    if len(out) > OUTPUT_LIMIT:      # errors are usually at the end: keep more of the end
        out = out[:OUTPUT_LIMIT // 4] + "\n…(output trimmed)…\n" + out[-OUTPUT_LIMIT * 3 // 4:]
    files = ("\nFiles created/changed:\n" + "\n".join(changed[:20])) if changed else ""
    head = f"exit code {code_}" if code_ == 0 else f"FAILED (exit code {code_})"
    return f"{head} in {cwd}\n{out.strip() or '(no output)'}{files}"


TOOLS = [
    Tool("run_python", "Run a Python script (pandas, numpy, matplotlib, openpyxl available) in the chat's main "
         "folder and return its printed output. Save charts/files to disk; print results you need. In the code, "
         "`from kimilm import call` calls a connected service's tool: call('tool_name', arg=value) returns its "
         "answer (for paging through many results in one run).",
         {"type": "object", "properties": {"code": {"type": "string"}, "timeout": {"type": "integer"}},
          "required": ["code"]},
         run_python, "python", changes=True, label="Run Python",
         summary=lambda a: "Run Python:\n" + (a.get("code") or "")[:600]),
    Tool("run_command", "Run a command in a shell (PowerShell on Windows, bash elsewhere) in one of the chat's "
         "folders: tests (pytest, npm test), git, build tools, the computer's own Python or Node. Returns the exit "
         "code and output. Commands must not wait for input.",
         {"type": "object", "properties": {"command": {"type": "string"},
                                           "folder": {"type": "string", "description": "folder to run in (default: "
                                                                                       "the chat's main folder)"},
                                           "timeout": {"type": "integer"}},
          "required": ["command"]},
         run_command, "python", changes=True, label="Run command",
         summary=lambda a: f"Run in {a.get('folder') or 'the main folder'}:\n{(a.get('command') or '')[:600]}"),
]
