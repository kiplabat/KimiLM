"""Undo on KimiOS (KimiLM 2.32.0, KimiOS 0.11.0): snapshots of the home folder and putting files back.

Before an unattended or auto-approved reply changes anything (and before a tool that deletes or overwrites),
the runner asks KimiOS for a snapshot (`kimios-snapshot take`: instant on btrfs; without btrfs nothing is taken,
because a copy needs to know which folders will change). Activity → Undo lists the snapshots, shows what was
added, changed and removed in a folder since one, and puts a file or folder back; KimiOS keeps the current version
beside it, so an undo can itself be undone. Everything runs as the signed-in user. Elsewhere `available()` is False."""
import asyncio
import json
import os
import shutil
import subprocess


class UndoError(RuntimeError):
    pass


def program():
    return os.environ.get("KIMIOS_SNAPSHOT_CLI") or shutil.which("kimios-snapshot")


def available():
    return os.name != "nt" and bool(program())


def run(*args, timeout=300):
    exe = program()
    if not exe:
        raise UndoError("snapshots need KimiOS (kimios-snapshot)")
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UndoError(f"kimios-snapshot did not run: {exc}") from None
    try:
        data = json.loads(out.stdout or "{}")
    except ValueError:
        raise UndoError((out.stderr or out.stdout or "kimios-snapshot gave no answer").strip()[-300:]) from None
    if not data.get("ok"):
        raise UndoError(data.get("error") or "refused")
    data.pop("ok", None)
    return data


async def take(reason):
    """A whole-home snapshot, or None (not KimiOS, no btrfs, the service is down): never fails the caller."""
    try:
        return await asyncio.to_thread(run, "take", "--reason", reason[:200], timeout=120)
    except UndoError:
        return None
