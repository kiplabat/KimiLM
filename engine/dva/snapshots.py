"""Prompt snapshots (2.6.0): the work a model did to read a prompt is kept on disk and never done twice.

Reading is the slow half of a local model on a CPU: the system prompt and tool list of an agent are about 3,000
tokens, minutes of work on a laptop, and they are the same for every chat with that harness and model. llama.cpp
keeps a prompt it has read in the server's memory, so a follow-up is fast, but that is gone when the model is
unloaded, and KimiLM unloads models as soon as nothing needs them (2.5.0). A snapshot closes that gap:

  first time    the prefix (system prompt + tools) is read once on its own and the model's state after reading
                it is saved to a file (llama-server's slot save, `--slot-save-path`)
  later         when a freshly loaded model meets the same prefix, the file is read back (about a second)
                instead of reading the prompt again (minutes); only what is new in the request is read

A snapshot belongs to exactly one model file, llama.cpp build, context-memory type and prefix text (the key is a
hash of all of them), so a changed tool list or system prompt simply makes a new one. Files live in the data
folder under prompt-cache/, are limited in total size (`snapshot_disk_mb`, least recently used first out), and
hold only what the model computed from the system prompt and tool list: no chat messages.

Checked on the real llama.cpp b12207 server: after a restart, a restored prefix of 1,994 tokens left 20 to read.
Snapshots are used when a model has one reply slot (the way planned runs load models); with several slots the
server's own in-memory cache does the work."""
import hashlib
import json
import re
import time

from .llm import estimate_tokens

MIN_PREFIX_TOKENS = 300      # below this, reading again is cheaper than the bookkeeping


class Snapshots:
    def __init__(self, paths, db, tag):
        self.root = paths.data / "prompt-cache"
        self.db, self.tag = db, tag
        self.stats = {"saved": 0, "restored": 0, "tokens_restored": 0}

    def enabled(self):
        return bool(self.db.setting("prompt_snapshots", True))

    def folder(self, model_id):
        folder = self.root / (re.sub(r"[^A-Za-z0-9._-]", "_", model_id)[:80] or "model")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def usable(self, server):
        return self.enabled() and getattr(server, "kind", None) != "cloud" and bool(getattr(server, "slot_dir", None)) \
            and int(server.plan.get("parallel") or 1) == 1 and not getattr(server, "no_snapshots", False)

    def key(self, server, model, messages, tools):
        """(file name, estimated prefix tokens) for a request, or (None, 0) when there is no prefix worth keeping."""
        system = messages[0] if messages and messages[0].get("role") == "system" else None
        text = system.get("content") if system else ""
        if not isinstance(text, str):
            text = json.dumps(text, sort_keys=True, default=str)
        tools_text = json.dumps(tools, sort_keys=True) if tools else ""
        tokens = estimate_tokens(text + tools_text)
        if tokens < MIN_PREFIX_TOKENS:
            return None, 0
        ident = "|".join(str(x) for x in (
            self.tag, model.get("path"), model.get("size"), server.plan.get("kv_type") or "f16",
            server.plan.get("backend"), server.plan.get("compute")))
        digest = hashlib.sha1((ident + "\n" + text + "\n" + tools_text).encode("utf-8", "ignore")).hexdigest()[:24]
        return f"{digest}.bin", tokens

    def touch(self, path):
        try:
            now_ = time.time()
            import os
            os.utime(path, (now_, now_))
        except OSError:
            pass

    def files(self):
        try:
            return sorted((p for p in self.root.rglob("*.bin") if p.is_file()), key=lambda p: p.stat().st_mtime)
        except OSError:
            return []

    def prune(self):
        """Keep the newest snapshots within the size limit."""
        limit = int(float(self.db.setting("snapshot_disk_mb", 4096) or 0) * 2**20)
        files = self.files()
        total = sum(p.stat().st_size for p in files)
        for p in files:
            if total <= limit:
                break
            try:
                total -= p.stat().st_size
                p.unlink()
            except OSError:
                pass

    def clear(self):
        n = 0
        for p in self.files():
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n

    def info(self):
        files = self.files()
        return {"enabled": self.enabled(), "files": len(files),
                "size_mb": round(sum(p.stat().st_size for p in files) / 2**20, 1),
                "limit_mb": int(float(self.db.setting("snapshot_disk_mb", 4096) or 0)), **self.stats}
