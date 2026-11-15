"""Run code: Python with the app's own Python (pandas, matplotlib, openpyxl are included), and commands in a shell
(PowerShell on Windows) for tests, git and build tools. Both run in the chat's folders. They are not sandboxed, so
they always ask first unless the chat/task auto-approves.

When code fails, the result points at the failing line of the script with a few lines around it, so a model can
fix its own code and run it again (the loop guard allows re-running after a change)."""
import asyncio
import os
import re
import subprocess
import sys
import time

from . import Tool, ToolError

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
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


async def run_python(ctx, code, timeout=None):
    timeout = int(timeout or ctx.app.db.setting("python_timeout_seconds", 180))
    runs = ctx.workspace / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    script = runs / f"run_{int(time.time() * 1000)}.py"
    script.write_text(code, encoding="utf-8")
    cwd = ctx.primary
    before = await asyncio.to_thread(_snapshot, cwd)
    env = {**os.environ, "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "DVA_WORKSPACE": str(ctx.workspace)}
    env.pop("DVA_TOKEN", None)
    ctx.emit("status", text="Running Python…")

    def go():
        try:
            return subprocess.run([sys.executable, "-I", str(script)], cwd=str(cwd), env=env, capture_output=True,
                                  text=True, timeout=timeout, encoding="utf-8", errors="replace",
                                  creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return None
    proc = await asyncio.to_thread(go)
    if proc is None:
        raise ToolError(f"The code ran longer than {timeout} seconds and was stopped")
    after = await asyncio.to_thread(_snapshot, cwd)
    changed = [p for p, t in after.items() if before.get(p) != t]
    out = (proc.stdout or "")
    if proc.stderr:
        out += ("\n[stderr]\n" + proc.stderr)
    if len(out) > OUTPUT_LIMIT:
        out = out[:OUTPUT_LIMIT // 2] + "\n…(output trimmed)…\n" + out[-OUTPUT_LIMIT // 2:]
    for p in changed[:20]:
        ctx.emit("file_changed", path=p, action="written")
    files = ("\nFiles created/changed:\n" + "\n".join(changed[:20])) if changed else ""
    if proc.returncode != 0:
        return (f"FAILED (exit code {proc.returncode})\n{out.strip() or '(no output)'}{where_it_failed(proc.stderr, script, code)}"
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
        out += (f"\nHint: the module '{missing.group(1)}' is not installed in the app's Python. Use what is available "
                "(pandas, numpy, matplotlib, openpyxl and the standard library), or run_command if the computer has "
                "its own Python with that package.")
    if "FileNotFoundError" in (stderr or ""):
        out += "\nHint: paths are relative to the chat's main folder; list_files shows what is there."
    return out


async def run_command(ctx, command, folder="", timeout=None):
    timeout = int(timeout or ctx.app.db.setting("python_timeout_seconds", 180))
    cwd = ctx.resolve(folder, write=True) if folder else ctx.primary
    if not cwd.is_dir():
        raise ToolError(f"{cwd} is not a folder")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "DVA_WORKSPACE": str(ctx.workspace)}
    env.pop("DVA_TOKEN", None)
    if os.name == "nt":
        argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
                + command]
    else:
        argv = ["/bin/bash", "-lc", command]
    ctx.emit("status", text=f"Running: {command[:80]}")
    before = await asyncio.to_thread(_snapshot, cwd)

    def go():
        try:
            return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                                  creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return None
        except FileNotFoundError as exc:
            raise ToolError(f"Could not start the shell: {exc}") from exc
    proc = await asyncio.to_thread(go)
    if proc is None:
        raise ToolError(f"The command ran longer than {timeout} seconds and was stopped (it may be waiting for "
                        "input; pass options that make it non-interactive)")
    after = await asyncio.to_thread(_snapshot, cwd)
    changed = [p for p, t in after.items() if before.get(p) != t]
    for p in changed[:20]:
        ctx.emit("file_changed", path=p, action="written")
    out = (proc.stdout or "")
    if proc.stderr:
        out += ("\n[stderr]\n" + proc.stderr)
    if len(out) > OUTPUT_LIMIT:      # errors are usually at the end: keep more of the end
        out = out[:OUTPUT_LIMIT // 4] + "\n…(output trimmed)…\n" + out[-OUTPUT_LIMIT * 3 // 4:]
    files = ("\nFiles created/changed:\n" + "\n".join(changed[:20])) if changed else ""
    head = f"exit code {proc.returncode}" if proc.returncode == 0 else f"FAILED (exit code {proc.returncode})"
    return f"{head} in {cwd}\n{out.strip() or '(no output)'}{files}"


TOOLS = [
    Tool("run_python", "Run a Python script (pandas, numpy, matplotlib, openpyxl available) in the chat's main "
         "folder and return its printed output. Save charts/files to disk; print results you need.",
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
