"""Helper programs the engine starts on demand: the browser helper (Playwright MCP) used by pipelines, by web
search when the search engines block plain requests, and by the browser tools (2.9.0).

Inside the installed app, Node programs run on the app's own Electron (no Node.js install needed). In
development, it falls back to `npx @playwright/mcp`."""
import asyncio
import json
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VERSIONS = json.loads((Path(__file__).with_name("versions.json")).read_text(encoding="utf-8"))


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

    def _browser_cmd(self, port):
        channel = self.app.db.setting("browser_channel") or {"Windows": "msedge", "Darwin": "chrome"}.get(
            platform.system(), "chromium")
        tail = ["--browser", channel, "--isolated", "--port", str(port), "--host", "127.0.0.1",
                "--output-dir", str(self.browser_dir)]
        if self.app.db.setting("browser_headless", False):
            tail.append("--headless")
        tail += (os.environ.get("DVA_BROWSER_ARGS") or "").split()     # e.g. --no-sandbox when run as root (tests)
        node, cli = os.environ.get("DVA_NODE"), os.environ.get("DVA_PLAYWRIGHT_MCP")
        env = {**os.environ}
        env.pop("DVA_TOKEN", None)
        if node and cli and Path(cli).exists():
            if os.environ.get("DVA_NODE_IS_ELECTRON"):
                env["ELECTRON_RUN_AS_NODE"] = "1"
            return [node, cli, *tail], env
        npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
        if not npx:
            raise RuntimeError("The browser helper needs the app's bundled Node or Node.js (npx) in development")
        return [npx, "-y", f"@playwright/mcp@{VERSIONS['playwright_mcp']}", *tail], env

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

    async def browser_call(self, key, name, args, timeout=120):
        """Call a tool of the browser helper on the page that belongs to `key` (a chat). Returns (ok, text)."""
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
