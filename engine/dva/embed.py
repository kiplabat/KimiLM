"""Local text embeddings for search by meaning (2.4.0).

A small embedding model (Qwen3-Embedding-0.6B, Q8_0, about 640 MB, 100+ languages, Apache-2.0) runs in its own
llama-server on the CPU, started when something needs it and stopped after a few idle minutes. It is downloaded
only when the user asks (Memory → Search). Without it, every search still works by keywords (SQLite FTS5).

Vectors are L2-normalised float32, so a dot product is the cosine similarity. Queries get the instruction prefix
the model was trained with; passages do not."""
import asyncio
import secrets
import subprocess
import time
from pathlib import Path

import httpx
import numpy as np

from .runtime import NO_WINDOW, free_port

MODEL = {"name": "Qwen3 Embedding 0.6B", "repo": "Qwen/Qwen3-Embedding-0.6B-GGUF",
         "file": "Qwen3-Embedding-0.6B-Q8_0.gguf", "size": 639_000_000, "licence": "Apache-2.0"}
QUERY_PREFIX = "Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery:"
MAX_CHARS = 6000          # per text (≈1,500 tokens): passages are cut to this
BATCH = 16
IDLE_SECONDS = 300


def to_blob(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob):
    return np.frombuffer(blob, dtype=np.float32) if blob else None


class Embedder:
    def __init__(self, app):
        self.app = app
        self.proc = None
        self.port = None
        self.key = secrets.token_urlsafe(18)
        self.last_used = 0.0
        self.lock = asyncio.Lock()
        self.state = {"state": "ready" if self.path().exists() else "missing", "done": 0, "total": MODEL["size"],
                      "error": None}
        self._cancel = None
        self._task = None

    # ------------------------------------------------------------------ the model file
    def path(self):
        custom = self.app.db.setting("embedding_model_path")
        if custom:
            return Path(custom)
        return self.app.paths.models_dir({"models_dir": self.app.db.setting("models_dir")}) / "embedding" / MODEL["file"]

    def available(self):
        return self.path().exists() and self.app.db.setting("embeddings_enabled", True) is not False

    def status(self):
        if self.state["state"] in ("ready", "missing", "failed"):     # the file may have appeared or gone since
            have = self.path().exists()
            if have and self.state["state"] != "ready":
                self.state = {"state": "ready", "done": MODEL["size"], "total": MODEL["size"], "error": None}
            elif not have and self.state["state"] == "ready":
                self.state = {"state": "missing", "done": 0, "total": MODEL["size"], "error": None}
        return {**self.state, "model": MODEL["name"], "path": str(self.path()), "running": self.running,
                "enabled": self.app.db.setting("embeddings_enabled", True) is not False}

    def _publish(self):
        self.app.bus.publish("embedder", **self.status())

    def download(self):
        if self.state["state"] in ("downloading", "connecting"):
            return self.status()
        self._cancel = asyncio.Event()
        self.state = {"state": "connecting", "done": 0, "total": MODEL["size"], "error": None}
        self._task = self.app.spawn(self._download())
        self._publish()
        return self.status()

    async def _download(self):
        from .download import DownloadCancelled, describe_error, download
        url = f"https://huggingface.co/{MODEL['repo']}/resolve/main/{MODEL['file']}"
        dest = self.path()
        last = 0.0

        def progress(done, total, rate):
            nonlocal last
            self.state.update(state="downloading", done=done, total=total or MODEL["size"], rate=rate)
            if time.monotonic() - last > 0.5:
                last = time.monotonic()
                self._publish()
        for attempt in range(6):
            try:
                await download(url, dest, headers=self.app.models._hf_headers(), progress=progress,
                               cancel=self._cancel)
                self.state = {"state": "ready", "done": MODEL["size"], "total": MODEL["size"], "error": None}
                self._publish()
                self.app.knowledge.backfill_soon()        # give existing passages, chats and notes their vectors
                return
            except DownloadCancelled:
                self.state = {"state": "missing", "done": 0, "total": MODEL["size"], "error": None}
                self._publish()
                return
            except Exception as exc:      # network trouble: retry, keeping what was downloaded
                self.state.update(state="retrying", error=describe_error(exc))
                self._publish()
                await asyncio.sleep(min(30, 3 * (attempt + 1)))
        self.state.update(state="failed")
        self._publish()

    def cancel(self):
        if self._cancel:
            self._cancel.set()

    async def remove(self):
        await self.stop()
        try:
            self.path().unlink()
        except OSError:
            pass
        self.app.db.run("UPDATE kb_chunks SET vec=NULL")
        self.app.db.run("DELETE FROM msg_vec")
        self.app.db.run("DELETE FROM mem_vec")
        self.state = {"state": "missing", "done": 0, "total": MODEL["size"], "error": None}
        self._publish()

    # ------------------------------------------------------------------ the server
    @property
    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    def _exe(self):
        backends = self.app.runtime.backends()
        for b in ("cpu", "custom", "vulkan", "cuda", "metal"):    # CPU first: it is small and leaves the GPU free
            if b in backends:
                return str(backends[b])
        return None

    async def _ensure(self):
        if self.running:
            return
        exe = self._exe()
        if not exe:
            raise RuntimeError("No llama.cpp runtime is installed")
        import psutil
        threads = max(2, min(8, (psutil.cpu_count(logical=False) or 4) - 1))
        self.port = free_port()
        cmd = [exe, "-m", str(self.path()), "--embeddings", "--pooling", "last", "-c", "8192", "-b", "8192",
               "-ub", "8192", "-np", "1", "--host", "127.0.0.1", "--port", str(self.port), "--api-key", self.key,
               "-t", str(threads), "--device", "none", "-ngl", "0", "--no-webui", "--alias", "embedding"]
        log = (self.app.paths.data / "logs" / "embeddings.log").open("ab")
        try:
            self.proc = await asyncio.to_thread(subprocess.Popen, cmd, stdout=log, stderr=subprocess.STDOUT,
                                                cwd=str(Path(exe).parent), creationflags=NO_WINDOW)
        finally:
            log.close()
        deadline = time.monotonic() + 120
        async with httpx.AsyncClient(timeout=5, trust_env=False) as http:
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError("The search model stopped while loading (details in logs/embeddings.log)")
                try:
                    if (await http.get(f"http://127.0.0.1:{self.port}/health")).status_code == 200:
                        self.app.spawn(self._idle_watch())
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.3)
        await self.stop()
        raise RuntimeError("The search model took too long to start")

    async def _idle_watch(self):
        proc = self.proc
        while self.proc is proc and self.running:
            await asyncio.sleep(30)
            if time.monotonic() - self.last_used > IDLE_SECONDS and not self.lock.locked():
                await self.stop()

    async def stop(self):
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.to_thread(proc.wait, 10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ------------------------------------------------------------------ embedding
    async def embed(self, texts, query=False):
        """Normalised vectors (numpy arrays) for texts, or None when no search model is available."""
        if not texts or not self.available():
            return None
        texts = [(QUERY_PREFIX + t if query else t)[:MAX_CHARS] or " " for t in texts]
        async with self.lock:
            await self._ensure()
            self.last_used = time.monotonic()
            out = []
            async with httpx.AsyncClient(timeout=180, trust_env=False) as http:
                for i in range(0, len(texts), BATCH):
                    r = await http.post(f"http://127.0.0.1:{self.port}/v1/embeddings",
                                        headers={"Authorization": f"Bearer {self.key}"},
                                        json={"input": texts[i:i + BATCH], "model": "embedding"})
                    r.raise_for_status()
                    rows = sorted(r.json()["data"], key=lambda d: d["index"])
                    for row in rows:
                        v = np.asarray(row["embedding"], dtype=np.float32)
                        n = float(np.linalg.norm(v)) or 1.0
                        out.append(v / n)
            self.last_used = time.monotonic()
        return out

    async def embed_one(self, text, query=True):
        try:
            got = await self.embed([text], query=query)
        except Exception as exc:
            self.app.log(f"embedding failed: {type(exc).__name__}: {exc}")
            return None
        return got[0] if got else None

