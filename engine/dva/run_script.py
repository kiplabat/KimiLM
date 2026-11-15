"""Runs a plugin or pipeline script the way `python script.py` does on a normal Python install: the script's own
folder comes first on sys.path, so it can import the files next to it.

The installer's embedded Python (python._pth) leaves that folder out, so a script that imports a neighbour (the
Datavera plugin's setup_hook.py imports pipeline_entry.py) failed in the installed app but not in development.

    python -m dva.run_script SCRIPT [ARGS...]
"""
import os
import runpy
import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent


def command(script, args=()):
    """The command line the engine uses to start a plugin or pipeline script."""
    return [sys.executable, "-u", "-m", "dva.run_script", str(script), *[str(a) for a in args]]


def environment(env):
    """Makes `dva` importable for the launcher (development runs; the embedded Python finds it via python._pth)."""
    env = dict(env)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(ENGINE_DIR), env.get("PYTHONPATH", "")) if p)
    return env


def main():
    if len(sys.argv) < 2:
        print("usage: python -m dva.run_script SCRIPT [ARGS...]", file=sys.stderr)
        sys.exit(2)
    script = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
