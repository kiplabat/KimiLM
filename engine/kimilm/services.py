"""Helper programs the engine starts on demand: the browser helper (Playwright MCP) used by pipelines, by web
search when the search engines block plain requests, and by the browser tools (2.9.0).

Inside the installed app, Node programs run on the app's own Electron (no Node.js install needed). In
development, it falls back to `npx @playwright/mcp`."""
import asyncio
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VERSIONS = json.loads((Path(__file__).with_name("versions.json")).read_text(encoding="utf-8"))


# 2.29.0: the browser the helper needs is not there: its own words name what to install
NOT_INSTALLED = re.compile(r'(?:Browser "?([\w.-]+)"? is not installed|install-browser ([\w.-]+))', re.I)
# browsers a Linux computer may already have, used before Playwright's own is downloaded
SYSTEM_BROWSERS = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "microsoft-edge-stable",
                   "microsoft-edge", "brave-browser")


def system_browser():
    """A Chrome-like browser installed on this Linux computer (a Flatpak cannot be driven this way)."""
    for name in SYSTEM_BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port(preferred=8931):
    if not port_open(preferred):
        return preferred
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BrowserSession:
    """One browser page kept open for one chat (2.9.0). The helper gives every connection its own page, so an
    agent that opens a page, then clicks, then types needs one connection that stays open between its tool
    calls. The connection lives in a task of its own, because it has to be opened and closed by the same task."""

    def __init__(self, url):
        self.url = url
        self.queue = asyncio.Queue()
        self.ready = asyncio.Event()
        self.error = None
        self.used = time.monotonic()
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        from fastmcp import Client
        try:
            async with Client(self.url, timeout=120) as client:
                self.ready.set()
                while True:
                    item = await self.queue.get()
                    if item is None:
                        return
                    name, args, fut = item
                    try:
                        res = await client.call_tool(name, args, raise_on_error=False)
                        text = "\n".join(getattr(c, "text", "") or "" for c in (res.content or []))
                        if not fut.done():
                            fut.set_result((not res.is_error, text))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if not fut.done():
                            fut.set_exception(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = exc
        finally:
            self.ready.set()
            while not self.queue.empty():          # nobody is left waiting for an answer that will not come
                item = self.queue.get_nowait()
                if item and not item[2].done():
                    item[2].set_exception(ConnectionError("The browser was closed"))

    def alive(self):
        return not self.task.done()

    async def call(self, name, args, timeout=120):
        await self.ready.wait()
        if self.error or not self.alive():
            raise ConnectionError(f"The browser helper is not reachable ({type(self.error).__name__ if self.error else 'closed'})")
        self.used = time.monotonic()
        fut = asyncio.get_running_loop().create_future()
        await self.queue.put((name, args, fut))
        return await asyncio.wait_for(fut, timeout)

    async def close(self):
        if self.alive():
            await self.queue.put(None)
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 10)
            except (asyncio.TimeoutError, Exception):
                self.task.cancel()


class Services:
    BROWSER_IDLE = 15 * 60        # a chat's browser page is closed after this many seconds without a call

    def __init__(self, app):
        self.app = app
        self.browser_proc = None
        self.browser_port = None
        self.lock = asyncio.Lock()
        self.sessions = {}        # chat id -> BrowserSession

    @property
    def browser_dir(self):
        """Where the browser helper writes what it saves (downloads, screenshots)."""
        folder = self.app.paths.data / "browser"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _helper(self):
        """How the browser helper is started: (argv before its options, environment)."""
        node, cli = os.environ.get("KIMILM_NODE"), os.environ.get("KIMILM_PLAYWRIGHT_MCP")
        env = {**os.environ}
        env.pop("KIMILM_TOKEN", None)
        if node and cli and Path(cli).exists():
            if os.environ.get("KIMILM_NODE_IS_ELECTRON"):
                env["ELECTRON_RUN_AS_NODE"] = "1"
            return [node, cli], env
        npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
        if not npx:
            raise RuntimeError("The browser helper needs the app's bundled Node or Node.js (npx) in development")
        return [npx, "-y", f"@playwright/mcp@{VERSIONS['playwright_mcp']}"], env

    def _browser_choice(self):
        """--browser (and --executable-path): the user's choice; else Edge on Windows, Chrome on macOS; on Linux a
        Chrome or Chromium the computer has, else Playwright's own (installed when it is first needed, 2.29.0)."""
        channel = self.app.db.setting("browser_channel")
        if channel:
            return ["--browser", channel]
        system = platform.system()
        if system == "Windows":
            return ["--browser", "msedge"]
        if system == "Darwin":
            return ["--browser", "chrome"]
        found = system_browser()
        if found:
            return ["--browser", "chrome", "--executable-path", found]
        return ["--browser", "chromium"]

    async def install_browser(self, target="chromium", on_status=None):
        """Download the browser the helper drives (Playwright's Chrome for Testing) into the user's own cache. No
        password is needed; it happens once. Returns what was installed."""
        target = target if re.fullmatch(r"[\w.-]{2,40}", target or "") else "chromium"
        head, env = self._helper()
        if on_status:
            on_status(f"Installing the browser the browser tools need ({target}); this happens once…")
        log = (self.app.paths.data / "logs" / "browser-helper.log").open("ab")
        try:
            proc = await asyncio.create_subprocess_exec(*head, "install-browser", target, stdout=log,
                                                        stderr=subprocess.STDOUT, env=env, creationflags=NO_WINDOW)
            try:
                code = await asyncio.wait_for(proc.wait(), 1800)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("Installing the browser took more than 30 minutes and was stopped")
        finally:
            log.close()
        if code != 0:
            raise RuntimeError(f"Installing the browser ({target}) failed (exit code {code}); see "
                               "logs/browser-helper.log")
        from . import installer
        installer.record(self.app, target, "browser", how="Playwright (install-browser)", package=target)
        self.app.bus.publish("notify", title="Installed a browser for the agents", body=target, level="info")
        return target

    def _browser_cmd(self, port):
        tail = [*self._browser_choice(), "--isolated", "--port", str(port), "--host", "127.0.0.1",
                "--output-dir", str(self.browser_dir)]
        if self.app.db.setting("browser_headless", False):
            tail.append("--headless")
        tail += (os.environ.get("KIMILM_BROWSER_ARGS") or "").split()     # e.g. --no-sandbox when run as root (tests)
        head, env = self._helper()
        return [*head, *tail], env

    async def browser(self):
        async with self.lock:
            if self.browser_proc and self.browser_proc.poll() is None and port_open(self.browser_port):
                return f"http://127.0.0.1:{self.browser_port}/mcp"
            port = free_port()
            if port_open(port):  # someone else's helper (e.g. the command-line agent) is already there
                return f"http://127.0.0.1:{port}/mcp"
            cmd, env = self._browser_cmd(port)
            log = (self.app.paths.data / "logs" / "browser-helper.log").open("ab")
            self.browser_proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env,
                                                 creationflags=NO_WINDOW)
            log.close()
            self.browser_port = port
            for _ in range(240):
                if port_open(port):
                    return f"http://127.0.0.1:{port}/mcp"
                if self.browser_proc.poll() is not None:
                    raise RuntimeError("The browser helper stopped while starting; see logs/browser-helper.log")
                await asyncio.sleep(0.5)
            raise RuntimeError("The browser helper did not start within 2 minutes")

    async def browser_call(self, key, name, args, timeout=120, on_status=None, _installed=False):
        """Call a tool of the browser helper on the page that belongs to `key` (a chat). Returns (ok, text).
        2.29.0: when the helper says its browser is not installed, it is installed and the call made again."""
        ok, text = await self._browser_call(key, name, args, timeout)
        miss = None if ok or _installed else NOT_INSTALLED.search(text or "")
        if miss and self.app.db.setting("install_programs", True):
            try:
                await self.install_browser(miss.group(1) or miss.group(2), on_status)
            except Exception as exc:
                return False, (f"{text}\nKimiLM tried to install it and could not: {exc}")
            await self.close_browser(key)
            await self.restart_browser()
            return await self.browser_call(key, name, args, timeout, on_status, _installed=True)
        return ok, text

    async def restart_browser(self):
        """End the helper, so the next call starts it again (with a browser that was just installed)."""
        async with self.lock:
            proc, self.browser_proc = self.browser_proc, None
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, 10)
                except Exception:
                    proc.kill()

    async def _browser_call(self, key, name, args, timeout=120):
        for old, sess in list(self.sessions.items()):
            if old != key and (not sess.alive() or time.monotonic() - sess.used > self.BROWSER_IDLE):
                self.sessions.pop(old, None)
                self.app.spawn(sess.close())
        sess = self.sessions.get(key)
        if sess is None or not sess.alive():
            sess = self.sessions[key] = BrowserSession(await self.browser())
        try:
            return await sess.call(name, args, timeout)
        except (ConnectionError, asyncio.TimeoutError):
            self.sessions.pop(key, None)
            self.app.spawn(sess.close())
            raise

    async def close_browser(self, key):
        sess = self.sessions.pop(key, None)
        if sess:
            await sess.close()

    async def shutdown(self):
        for key in list(self.sessions):
            try:
                await self.close_browser(key)
            except Exception:
                pass
        proc, self.browser_proc = self.browser_proc, None
        if proc and proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                               creationflags=NO_WINDOW)
            else:
                proc.terminate()
