"""Trying an app before the person sees it (2.35.0).

When an app is saved, it is opened for a few seconds in a headless browser (and pictured: 2.36.0), in the same sandbox as in the window,
under engine/builtin/apps/preview.html: every kimi call gets a stand-in answer (nothing reaches a device or a
model), a few keys are pressed and the middle is clicked, and the page writes what happened: errors, whether its
animation loop ran, what it shows, whether it reacted. The builder gets that as text and fixes what it shows.

The browser: Microsoft Edge or Google Chrome on Windows and macOS, a Chrome-like browser on Linux (or the one
Playwright downloaded for the browser tools), run with --headless and --screenshot; otherwise KimiLM's own desktop
app with --kimilm-preview (electron/main.cjs). Without either, apps are saved without a preview, and it says so."""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_browser():
    """A Chromium-family browser to run headless, or None."""
    override = os.environ.get("DVA_PREVIEW_BROWSER")
    if override:
        return override if Path(override).exists() else None
    system = platform.system()
    if system == "Windows":
        roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        for root in filter(None, roots):
            for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                if (Path(root) / rel).exists():
                    return str(Path(root) / rel)
        return None
    if system == "Darwin":
        for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"):
            if Path(p).exists():
                return p
        return None
    from .services import system_browser
    found = system_browser()
    if found:
        return found
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache" / "ms-playwright")
    for chrome in sorted(cache.glob("chromium-*/chrome-linux*/chrome"), reverse=True):
        return str(chrome)
    return None


def electron():
    """KimiLM's own desktop app, which can open a page off screen (--kimilm-preview), or None."""
    node = os.environ.get("DVA_NODE")
    if os.environ.get("DVA_NODE_IS_ELECTRON") and node and Path(node).exists():
        extra = [os.environ["DVA_ELECTRON_APP"]] if os.environ.get("DVA_ELECTRON_APP") else []
        return [node, *extra]
    return None


def how():
    """How apps are tried here, in words (for the window), or None."""
    b = find_browser()
    if b:
        return f"headless {Path(b).stem}"
    if electron():
        return "KimiLM itself, off screen"
    return None


async def _run(argv, timeout):
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                creationflags=NO_WINDOW)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None, "the browser took too long"
    return out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def run(url, shot, posted, timeout=60):
    """Open the preview page and take its picture (`shot`, a .png). `posted()` gives the result the page sent to
    the engine (./_result). Returns (result dict or None, how it was tried or why it could not be)."""
    browser = find_browser()
    root = platform.system() == "Linux" and hasattr(os, "geteuid") and os.geteuid() == 0
    with tempfile.TemporaryDirectory(prefix="kimi-preview-") as tmp:
        if browser:
            how = f"headless {Path(browser).stem}"
            argv = [browser, "--headless=new", "--no-first-run", "--no-default-browser-check", "--mute-audio",
                    f"--user-data-dir={tmp}", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                    "--window-size=1024,700", "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required",
                    f"--screenshot={shot}", url]
            if root:
                argv.insert(1, "--no-sandbox")
        elif electron():
            how = "KimiLM off screen"
            out = Path(tmp) / "result.json"
            argv = [*electron(), *(["--no-sandbox"] if root else []), f"--kimilm-preview={url}",
                    f"--kimilm-preview-out={out}", f"--kimilm-preview-shot={shot}"]
        else:
            return None, "no browser to try it in (Edge, Chrome or Chromium)"
        stdout, err = await _run(argv, timeout)
        result = posted()
        if result is None and not browser:
            try:
                result = json.loads((Path(tmp) / "result.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result = None
        if result is None:
            if stdout is None:
                return None, err
            return None, f"{how} gave no result ({(err or '').strip()[-200:] or 'nothing printed'})"
        return result, how


def summary(result, kind):
    """(problems, notes) in words for the builder: problems are what it should fix."""
    problems, notes = [], []
    if not result:
        return problems, notes
    rep = result.get("report") or {}
    seen = {}
    errors = result.get("errors") or []
    if any(re.search(r"WebGL context|creating WebGL|WebGL.*not supported", str(e.get("message"))) for e in errors):
        # the preview's browser could not draw 3D here: say so, and do not hold it against the app
        notes.append("3D could not be drawn where it was tried (no WebGL there), so the 3D part was not checked")
        errors = [e for e in errors if not re.search(r"WebGL", str(e.get("message")))]
    for e in errors:
        where = f" ({e.get('source')}{':' + str(e.get('line')) if e.get('line') else ''})" if e.get("source") else ""
        text = f"error while it ran: {str(e.get('message'))[:300]}{where}"
        seen[text] = seen.get(text, 0) + 1
    problems += [t + (f" ({k} times)" if k > 1 else "") for t, k in seen.items()]
    if not result.get("hello"):
        problems.append("the page never loaded the kimi API (a script stopped before it, or the page did not load)")
    if not rep:
        problems.append("it did not answer the preview's poke: a script error stopped it, or it froze")
        return problems, notes
    canv = rep.get("canvases") or []
    if canv:
        notes.append("draws on " + ", ".join(f"a {w}×{h} {t} canvas" for w, h, t in canv[:3]))
    if rep.get("text_length"):
        notes.append(f"shows {rep['text_length']} characters of text")
    if rep.get("buttons") or rep.get("inputs"):
        notes.append(f"{rep.get('buttons', 0)} buttons, {rep.get('inputs', 0)} inputs")
    moving = (rep.get("frames_after") or 0) - (rep.get("frames") or 0)
    if rep.get("frames_after"):
        notes.append(f"its animation loop ran ({rep['frames_after']} frames)")
    if kind == "game":
        if not canv and not rep.get("text_length"):
            problems.append("the page shows nothing (no canvas, no text)")
        if not rep.get("frames_after") and not any("WebGL" in n for n in notes):
            problems.append("nothing moves: no requestAnimationFrame loop ran (a game needs a game loop)")
        elif rep.get("frames_after") and moving <= 0:
            notes.append("the loop stopped after the keys and the click")
    else:
        if not (result.get("calls") or {}).get("device.info") and not (result.get("calls") or {}).get("device.state"):
            problems.append("the panel never asked for the device (kimi.device.info or kimi.device.state)")
        if not canv and not rep.get("buttons") and not rep.get("inputs"):
            problems.append("the panel has no controls and no chart")
    if rep.get("changed"):
        notes.append("it reacted to the keys and the click")
    elif kind == "game":
        notes.append("nothing visible changed after the keys and the click (fine if it waits for something else)")
    if result.get("sent"):
        notes.append("it tried to act on the device (" + ", ".join(sorted(set(result["sent"])))[:120]
                     + "; not sent in the preview)")
    return problems, notes
