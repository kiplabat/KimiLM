"""Models: a short recommended list, browsing Hugging Face GGUF models ranked for this computer, downloads
(resumable, retried, checked, remembered across restarts), vision parts (mmproj), importing a GGUF already on disk,
finding models other apps already downloaded (Ollama, LM Studio, Hugging Face cache, GPT4All, Jan), per-model
settings (CPU/GPU, context, threads, simultaneous replies) and "will it fit" estimates."""
import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from . import gguf
from .db import now
from .download import DownloadCancelled, DownloadStalled, describe_error, download, fetch_json

HF = "https://huggingface.co"

# Recommended models. Files are resolved from the repository listing at download time (publishers rename
# files), by quantisation label. `min_ram_gb` is total system memory for comfortable CPU use.
CATALOG = [
    {"key": "qwen3.5-4b", "name": "Qwen3.5 4B", "repo": "unsloth/Qwen3.5-4B-GGUF", "quant": "Q4_K_M",
     "approx_gb": 2.8, "min_ram_gb": 8, "tools": True, "default": True,
     "about": "Best all-rounder for laptops. Good at tools and following instructions; can see images. The proven default."},
    {"key": "qwen3.5-2b", "name": "Qwen3.5 2B", "repo": "unsloth/Qwen3.5-2B-GGUF", "quant": "Q4_K_M",
     "approx_gb": 1.4, "min_ram_gb": 6, "tools": True,
     "about": "Faster on older computers; simpler answers."},
    {"key": "qwen3.5-0.8b", "name": "Qwen3.5 0.8B", "repo": "unsloth/Qwen3.5-0.8B-GGUF", "quant": "Q8_0",
     "approx_gb": 0.9, "min_ram_gb": 4, "tools": False,
     "about": "Tiny and very fast. Good for quick wording tasks and memory learning; weak at tools."},
    {"key": "qwen3.5-9b", "name": "Qwen3.5 9B", "repo": "unsloth/Qwen3.5-9B-GGUF", "quant": "Q4_K_M",
     "approx_gb": 5.7, "min_ram_gb": 16, "tools": True,
     "about": "Noticeably smarter; slower on CPU. Great with a graphics card that has 8 GB or more."},
    {"key": "qwen3.5-35b-a3b", "name": "Qwen3.5 35B-A3B (MoE)", "repo": "unsloth/Qwen3.5-35B-A3B-GGUF",
     "quant": "Q4_K_M", "approx_gb": 21, "min_ram_gb": 32, "tools": True,
     "about": "Large 'mixture of experts' model: only ~3B parameters work per word, so it is usable on a "
              "32 GB computer even without a graphics card, at much higher quality."},
    {"key": "qwen3.5-27b", "name": "Qwen3.5 27B", "repo": "unsloth/Qwen3.5-27B-GGUF", "quant": "Q4_K_M",
     "approx_gb": 16.5, "min_ram_gb": 32, "tools": True,
     "about": "High quality; needs a strong graphics card (16 GB+) to be pleasant."},
    {"key": "gemma-3-4b", "name": "Gemma 3 4B", "repo": "ggml-org/gemma-3-4b-it-GGUF", "quant": "Q4_K_M",
     "approx_gb": 2.5, "min_ram_gb": 8, "tools": False,
     "about": "Google's small model; fluent writing and good at images, weaker at tool use."},
    {"key": "llama-3.2-3b", "name": "Llama 3.2 3B", "repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
     "quant": "Q4_K_M", "approx_gb": 2.0, "min_ram_gb": 8, "tools": True,
     "about": "Meta's small model; quick, decent tools."},
    {"key": "gpt-oss-20b", "name": "gpt-oss 20B", "repo": "ggml-org/gpt-oss-20b-GGUF", "quant": "mxfp4",
     "approx_gb": 12.1, "min_ram_gb": 24, "tools": True,
     "about": "OpenAI's open-weight reasoning model (MoE). Strong reasoning; needs 16 GB+ free memory."},
]

QUANT_RE = re.compile(r"(?i)(?:^|[-_.])((?:UD-)?(?:IQ\d_[A-Z0-9_]+|Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4(?:_MOE)?))"
                      r"(?:[-_.]|$)")
SPLIT_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")
# llama.cpp `general.file_type` → the usual quantisation label (files from Ollama have no label in their name)
FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
              12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
              19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL",
              26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 38: "MXFP4"}
MIN_MODEL_BYTES = 50 * 2**20   # smaller .gguf files are vocabularies, adapters or test files


def slug(text):
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-")[:80]


def quant_of(filename):
    m = QUANT_RE.search(Path(filename).stem)
    return m.group(1).upper() if m else "?"


TRUSTED = {"unsloth", "ggml-org", "bartowski", "lmstudio-community", "qwen", "google", "microsoft", "mistralai",
           "meta-llama", "ibm-granite", "nvidia", "openai", "deepseek-ai", "allenai", "huggingfacetb", "tiiuae",
           "ibm", "liquidai", "moonshotai", "zai-org", "thudm", "openbmb", "nousresearch", "mradermacher",
           "second-state", "gaianet", "lmstudio-ai", "jan-hq", "menlo"}
MODIFIED = re.compile(r"(?i)abliterat|uncensor|heretic|nsfw|erotic|roleplay|-rp-|lewd")
BROWSE_CATEGORIES = {
    "all": {}, "chat": {"pipeline_tag": "text-generation"}, "vision": {"pipeline_tag": "image-text-to-text"},
    "coding": {"search": "coder"}, "small": {},
}
QUANT_BYTES = {"Q8_0": 1.07, "Q6_K": 0.83, "Q5_K_M": 0.72, "Q4_K_M": 0.61, "IQ4_XS": 0.55, "Q3_K_M": 0.49,
               "IQ3_XXS": 0.41, "IQ2_M": 0.34}       # bytes per parameter, roughly
MMPROJ_PREF = ("F16", "BF16", "Q8_0", "F32")


def params_from_name(name):
    """(total, active) parameters in billions guessed from a name like 'Qwen3.5-35B-A3B' or 'gemma-3-4b-it'."""
    low = name.lower().replace("_", "-")
    m = re.search(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z])", low) or re.search(r"(\d+(?:\.\d+)?)b(?=[-.]|$)", low)
    total = float(m.group(1)) if m else None
    x = re.search(r"(\d+)x(\d+(?:\.\d+)?)b", low)
    if x:
        total = float(x.group(1)) * float(x.group(2))
    a = re.search(r"-a(\d+(?:\.\d+)?)b", low)
    active = float(a.group(1)) if a else total
    return total, active


def pick_mmproj(entries):
    """Best vision projector file from a repo listing: F16 first (what llama.cpp recommends)."""
    if not entries:
        return None
    def rank(e):
        name = Path(e["path"]).name.upper()
        return next((i for i, q in enumerate(MMPROJ_PREF) if q in name), len(MMPROJ_PREF))
    return sorted(entries, key=lambda e: (rank(e), e.get("size") or 0))[0]


class Models:
    def __init__(self, paths, db, bus, runtime):
        self.paths, self.db, self.bus, self.runtime = paths, db, bus, runtime
        self.downloads = {}  # id -> state
        self._cancel = {}
        self._tasks = set()
        self._browse_cache = {}
        self.tunes = {}      # model id -> progress of "Find the fastest setup"
        for row in self.db.all("SELECT * FROM downloads WHERE state NOT IN ('done', 'cancelled')"):
            spec = row.get("spec") or {}
            state = {**spec.get("state", {}), "id": row["id"], "state": "paused", "rate": 0, "eta": None,
                     "phase": "", "error": "The app was closed during this download. Press Resume to continue "
                                           "where it stopped."}
            self.downloads[row["id"]] = state

    def _hf_headers(self):
        from .app_state import STATE
        token = STATE.secrets.get("hf_token") if STATE else None
        return {"Authorization": f"Bearer {token}"} if token else {}

    # ------------------------------------------------------------------ registry
    def installed(self):
        rows = self.db.all("SELECT * FROM models ORDER BY COALESCE(last_used, added) DESC")
        for r in rows:
            r["exists"] = bool(r["path"]) and Path(r["path"]).exists()
        return rows

    def get_any(self, model_id):
        """A local or an online model's row (online rows have kind 'cloud'), or None."""
        if not model_id:
            return None
        if str(model_id).startswith("cloud:"):
            providers = getattr(self, "providers", None)
            return providers.get(model_id) if providers else None
        return self.db.get("models", model_id)

    def default_model(self):
        chosen = self.db.setting("default_model")
        if chosen == "auto" or (chosen and self.get_any(chosen)):
            return chosen
        rows = self.installed()
        return rows[0]["id"] if rows else None

    def update_settings(self, model_id, values):
        row = self.db.get("models", model_id)
        if not row:
            raise KeyError(model_id)
        settings = {**(row["settings"] or {}), **values}
        settings = {k: v for k, v in settings.items() if v not in (None, "")}
        self.db.update("models", model_id, {"settings": settings})
        return self.db.get("models", model_id)

    def delete(self, model_id):
        row = self.db.get("models", model_id)
        if not row:
            return
        if self.runtime.is_loaded(model_id):
            raise RuntimeError("Unload the model before deleting it")
        managed = self.paths.models_dir(self._settings())
        for f in row.get("files") or [row["path"]]:
            p = Path(f)
            if p.exists() and managed in p.parents:  # never delete files the user imported from elsewhere
                p.unlink()
        folder = Path(row["path"]).parent if row["path"] else None
        if folder and managed in folder.parents and folder.exists() and not any(folder.iterdir()):
            folder.rmdir()
        self.db.delete("models", model_id)

    def _settings(self):
        return {"models_dir": self.db.setting("models_dir")}

    def register(self, path, repo=None, name=None, files=None, sha256=None, catalog_key=None, mmproj=None):
        path = Path(path)
        try:
            meta = gguf.read_metadata(path)
        except Exception as exc:
            raise ValueError(f"That file is not a usable GGUF model ({exc})") from exc
        files = files or [str(path)]
        size = sum(Path(f).stat().st_size for f in files if Path(f).exists())
        for row in self.db.all("SELECT * FROM models"):
            if row["path"] and Path(row["path"]) == path:
                return row  # already added
        model_id = slug(catalog_key or name or (Path(path).name.replace(".gguf", "")))
        base, n = model_id, 2
        while self.db.get("models", model_id):
            existing = self.db.get("models", model_id)
            if existing["path"] == str(path):
                return existing
            model_id, n = f"{base}-{n}", n + 1
        thinking = meta.get("template_supports_thinking")
        mmproj = mmproj or self.sibling_mmproj(path)
        settings = {"compute": "auto", "thinking_supported": bool(thinking)}
        if mmproj:
            settings["mmproj"] = str(mmproj)
        self.db.insert("models", {
            "id": model_id, "name": name or meta.get("name") or path.stem, "repo": repo or "", "files": files,
            "path": str(path), "size": size, "sha256": sha256 or "",
            "quant": quant_of(path.name) if quant_of(path.name) != "?" else FILE_TYPES.get(meta.get("file_type"), "?"),
            "meta": meta, "settings": settings, "added": now()})
        self.bus.publish("models_changed")
        if not self.db.setting("default_model"):
            self.db.set_setting("default_model", model_id)
        return self.db.get("models", model_id)

    @staticmethod
    def sibling_mmproj(path):
        """A vision projector next to the model (how LM Studio and Hugging Face downloads keep them)."""
        try:
            hits = [p for p in Path(path).parent.glob("*.gguf") if "mmproj" in p.name.lower()]
        except OSError:
            return None
        if not hits:
            return None
        best = pick_mmproj([{"path": str(p), "size": p.stat().st_size} for p in hits])
        return best["path"] if best else None

    # ------------------------------------------------------------------ fit
    def fit(self, size_bytes, meta=None, ctx=None, hw=None):
        """'fits' | 'tight' | 'too_big' for CPU, and for the best GPU if any."""
        from .hardware import snapshot
        hw = hw or snapshot()
        ctx = ctx or int(self.db.setting("default_ctx", 8192))
        kv = gguf.kv_cache_bytes(meta, ctx) if meta else None
        if kv is None:
            kv = max(0.35, size_bytes / 2**30 * 0.12) * (ctx / 8192) * 2**30
        need = size_bytes + kv + 0.5 * 2**30
        ram = hw["ram_mb"] * 2**20
        cpu = "fits" if need <= ram * 0.6 else ("tight" if need <= ram * 0.85 else "too_big")
        vram = max([g.get("vram_mb", 0) for g in hw["gpus"] if not g.get("integrated")] or [0]) * 2**20
        gpu = None
        if vram:
            gpu = "fits" if need <= vram * 0.92 else ("partial" if need <= vram + ram * 0.6 else "too_big")
        return {"need_mb": int(need / 2**20), "cpu": cpu, "gpu": gpu}

    # ------------------------------------------------------------------ Hugging Face
    async def catalog(self):
        from .hardware import snapshot
        hw = await asyncio.to_thread(snapshot)
        have = {r["repo"]: r["id"] for r in self.installed() if r["repo"]}
        out = []
        for item in CATALOG:
            entry = dict(item)
            entry["fit"] = self.fit(int(item["approx_gb"] * 2**30), None, None, hw)
            entry["installed"] = have.get(item["repo"])
            out.append(entry)
        return {"hardware": hw, "models": out}

    async def search(self, query, limit=30):
        url = f"{HF}/api/models?search={quote(query)}&filter=gguf&sort=downloads&direction=-1&limit={limit}"
        rows = await fetch_json(url, self._hf_headers())
        return [{"repo": r.get("id") or r.get("modelId"), "downloads": r.get("downloads"), "likes": r.get("likes"),
                 "updated": r.get("lastModified"), "gated": r.get("gated", False)} for r in rows]

    async def repo_files(self, repo):
        """GGUF files in a repository, grouped so split models (-00001-of-00003) appear once."""
        tree = await fetch_json(f"{HF}/api/models/{repo}/tree/main?recursive=true", self._hf_headers())
        groups = {}
        for f in tree:
            path = f.get("path", "")
            if f.get("type") != "file" or not path.endswith(".gguf"):
                continue
            name = Path(path).name
            if "mmproj" in name.lower():
                continue
            size = (f.get("lfs") or {}).get("size") or f.get("size") or 0
            sha = (f.get("lfs") or {}).get("oid")
            m = SPLIT_RE.search(name)
            key = SPLIT_RE.sub(".gguf", path) if m else path
            g = groups.setdefault(key, {"file": key, "quant": quant_of(name), "size": 0, "parts": []})
            g["parts"].append({"path": path, "size": size, "sha256": sha})
            g["size"] += size
        mmproj = [{"path": f.get("path"), "size": (f.get("lfs") or {}).get("size") or f.get("size") or 0,
                   "sha256": (f.get("lfs") or {}).get("oid")}
                  for f in tree if f.get("path", "").endswith(".gguf") and "mmproj" in f.get("path", "").lower()]
        from .hardware import snapshot
        hw = await asyncio.to_thread(snapshot)
        files = sorted(groups.values(), key=lambda g: g["size"])
        for g in files:
            g["parts"].sort(key=lambda p: p["path"])
            g["fit"] = self.fit(g["size"], None, None, hw)
        rec = self.recommend_file(files, hw)
        if rec:
            rec["recommended"] = True
        best = pick_mmproj(mmproj)
        return {"repo": repo, "files": files, "mmproj": mmproj, "vision": bool(best),
                "mmproj_file": best, "hardware": hw}

    def recommend_file(self, files, hw):
        """The best quality file that fits comfortably (Q4_K_M is the usual sweet spot)."""
        order = ["Q4_K_M", "UD-Q4_K_XL", "Q4_K_S", "IQ4_XS", "IQ4_NL", "Q5_K_M", "Q6_K", "Q3_K_M", "UD-Q3_K_XL",
                 "IQ3_XXS", "UD-Q2_K_XL", "IQ2_M", "Q8_0", "MXFP4", "MXFP4_MOE"]
        small = files and min(f["size"] for f in files) < 2.5 * 2**30
        if small:   # small models: higher precision costs little
            order = ["Q8_0", "Q6_K", "Q5_K_M"] + order
        ok = lambda f: "fits" in (f["fit"].get("cpu"), f["fit"].get("gpu"))  # noqa: E731
        for q in order:
            f = next((f for f in files if f["quant"] == q and ok(f)), None)
            if f:
                return f
        fitting = [f for f in files if ok(f)]
        return max(fitting, key=lambda f: f["size"]) if fitting else None

    async def browse(self, category="all", sort="fit", q="", trusted=True, vision_only=False):
        """Hugging Face GGUF models ranked for this computer: estimated size at Q4, whether it fits, rough speed."""
        from .hardware import snapshot
        params = {"filter": "gguf", "limit": "200", "direction": "-1",
                  "sort": {"fit": "downloads", "downloads": "downloads", "trending": "trendingScore",
                           "recent": "lastModified", "likes": "likes"}.get(sort, "downloads")}
        params.update(BROWSE_CATEGORIES.get(category, {}))
        if q:
            params["search"] = (params.get("search", "") + " " + q).strip()
        key = json.dumps(params, sort_keys=True)
        cached = self._browse_cache.get(key)
        if cached and time.time() - cached[0] < 600:
            rows = cached[1]
        else:
            query = "&".join(f"{k}={quote(v)}" for k, v in params.items())
            expand = "&".join(f"expand[]={e}" for e in ("gguf", "downloads", "likes", "pipeline_tag", "tags",
                                                        "lastModified", "gated", "trendingScore"))
            rows = await fetch_json(f"{HF}/api/models?{query}&{expand}", self._hf_headers())
            self._browse_cache[key] = (time.time(), rows)
        hw = await asyncio.to_thread(snapshot)
        have = {r["repo"]: r["id"] for r in self.installed() if r["repo"]}
        out = []
        for r in rows:
            repo = r.get("id") or r.get("modelId") or ""
            owner = repo.split("/")[0].lower()
            if trusted and owner not in TRUSTED:
                continue
            if trusted and MODIFIED.search(repo):
                continue
            g = r.get("gguf") or {}
            name_total, name_active = params_from_name(repo.split("/")[-1])
            total = (g.get("total") / 1e9) if g.get("total") and g.get("architecture") != "clip" else name_total
            if not total:
                continue
            active = name_active if name_active and name_active < total else total
            tags = [t.lower() for t in r.get("tags") or []]
            vision = r.get("pipeline_tag") == "image-text-to-text" or "image-text-to-text" in tags or \
                any(t in tags for t in ("vision", "multimodal", "vlm"))
            if vision_only and not vision:
                continue
            if category == "small" and total > 4.5:
                continue
            size = int(total * 1e9 * QUANT_BYTES["Q4_K_M"])
            fit = self.fit(size, None, None, hw)
            out.append({
                "repo": repo, "name": repo.split("/")[-1].replace("-GGUF", "").replace("-gguf", ""),
                "publisher": repo.split("/")[0], "params_b": round(total, 1), "active_b": round(active, 1),
                "moe": active < total * 0.6, "architecture": g.get("architecture"),
                "context": g.get("context_length"), "downloads": r.get("downloads") or 0, "likes": r.get("likes") or 0,
                "updated": r.get("lastModified"), "gated": bool(r.get("gated")), "vision": vision,
                "coder": "coder" in repo.lower() or "code" in tags, "est_size": size, "fit": fit,
                "speed": self.speed_label(active, fit, hw), "installed": have.get(repo)})
        rank = {"fits": 0, "partial": 1, "tight": 1, "too_big": 3, None: 3}

        def fit_rank(m):
            return min(rank.get(m["fit"]["cpu"], 3), rank.get(m["fit"].get("gpu"), 3))
        if sort == "fit":
            out.sort(key=lambda m: (fit_rank(m), -m["downloads"]))
        return {"hardware": hw, "models": out, "count": len(out)}

    @staticmethod
    def speed_label(active_b, fit, hw):
        """Very rough words-per-second feel. CPU speed is limited by memory bandwidth (~20 GB/s on laptops)."""
        if fit.get("gpu") == "fits":
            return "fast"
        if fit.get("cpu") == "too_big":
            return "too big"
        gb = active_b * QUANT_BYTES["Q4_K_M"]
        tps = 20 / max(gb, 0.1)
        return "fast" if tps >= 15 else ("ok" if tps >= 6 else "slow")

    async def resolve_catalog(self, key, with_listing=False):
        item = next((c for c in CATALOG if c["key"] == key), None)
        if not item:
            raise KeyError(key)
        listing = await self.repo_files(item["repo"])
        want = item["quant"].upper()
        match = next((f for f in listing["files"] if f["quant"] == want), None) or \
            next((f for f in listing["files"] if want in f["file"].upper()), None)
        if not match:
            raise ValueError(f"{item['repo']} no longer has a {item['quant']} file")
        return (item, match, listing) if with_listing else (item, match)

    # ------------------------------------------------------------------ downloads
    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)          # keep a reference, or Python may drop the running task
        task.add_done_callback(self._tasks.discard)
        return task

    def _save_download(self, dl_id, spec=None):
        state = self.downloads.get(dl_id)
        if state is None:
            return
        row = self.db.get("downloads", dl_id)
        spec = spec or (row or {}).get("spec") or {}
        spec["state"] = {k: v for k, v in state.items() if k not in ("rate", "eta")}
        if row:
            self.db.update("downloads", dl_id, {"spec": spec, "state": state["state"], "error": state.get("error"),
                                                "updated": now()})
        else:
            self.db.insert("downloads", {"id": dl_id, "spec": spec, "state": state["state"],
                                         "error": state.get("error"), "created": now(), "updated": now()})

    def list_downloads(self):
        return sorted(self.downloads.values(), key=lambda d: d.get("started") or 0, reverse=True)

    def start_download(self, repo, file_group, name=None, catalog_key=None, mmproj=None, model_id=None):
        """Download a model (all parts of a split file) and, when given, its vision projector (mmproj)."""
        dl_id = slug(f"{repo}-{Path(file_group['file']).stem}" if file_group else f"{repo}-{mmproj['path']}")
        if dl_id in self.downloads and self.downloads[dl_id]["state"] in ("queued", "downloading", "verifying",
                                                                            "connecting", "retrying"):
            return dl_id
        parts = list((file_group or {}).get("parts") or [])
        total = sum(p["size"] or 0 for p in parts) + ((mmproj or {}).get("size") or 0)
        state = {"id": dl_id, "repo": repo, "file": (file_group or {}).get("file") or mmproj["path"],
                 "name": name or Path((file_group or {}).get("file") or mmproj["path"]).stem, "total": total,
                 "done": 0, "rate": 0, "eta": None, "state": "queued", "phase": "Waiting to start", "error": None,
                 "attempt": 0, "vision": bool(mmproj), "started": time.time()}
        self.downloads[dl_id] = state
        spec = {"repo": repo, "group": file_group, "name": name, "catalog_key": catalog_key, "mmproj": mmproj,
                "model_id": model_id}
        self._save_download(dl_id, spec)
        cancel = asyncio.Event()
        self._cancel[dl_id] = cancel
        self._spawn(self._download(dl_id, spec, cancel))
        self.bus.publish("download", **state)
        return dl_id

    def resume_download(self, dl_id):
        row = self.db.get("downloads", dl_id)
        if not row:
            raise KeyError(dl_id)
        spec = row["spec"] or {}
        self.downloads.pop(dl_id, None)
        return self.start_download(spec["repo"], spec.get("group"), spec.get("name"), spec.get("catalog_key"),
                                   spec.get("mmproj"), spec.get("model_id"))

    async def _download(self, dl_id, spec, cancel):
        state = self.downloads[dl_id]
        repo, group, mmproj = spec["repo"], spec.get("group"), spec.get("mmproj")
        folder = self.paths.models_dir(self._settings()) / repo.replace("/", "__")
        folder.mkdir(parents=True, exist_ok=True)
        disk = shutil.disk_usage(folder)
        if disk.free < state["total"] * 1.05:
            state.update(state="failed", phase="", error=f"Not enough free disk space ({disk.free // 2**30} GB free, "
                         f"{state['total'] // 2**30 + 1} GB needed). Move the models folder in Settings.")
            self._finish_download(dl_id)
            return
        parts = [dict(p, kind="model") for p in (group or {}).get("parts") or []]
        if mmproj:
            parts.append(dict(mmproj, kind="mmproj"))
        done_before = 0
        paths, mmproj_path = [], None
        max_attempts = 8
        try:
            for part in parts:
                dest = folder / Path(part["path"]).name     # an mmproj keeps its name: one repo's models share it

                def progress(done, total, rate, base=done_before):
                    remaining = state["total"] - (base + done)
                    state.update(done=base + done, rate=rate, state="downloading",
                                 eta=int(remaining / rate) if rate > 1024 else None)
                    self.bus.publish("download", **state)

                def phase(name, detail, label=("vision part" if part["kind"] == "mmproj" else
                                               (f"part {parts.index(part) + 1} of {len(parts)}" if len(parts) > 1 else ""))):
                    text = {"connecting": "Connecting", "downloading": "Downloading"}.get(name, name)
                    state.update(state="downloading" if name == "downloading" else "connecting",
                                 phase=" ".join(x for x in (text, label, detail) if x))
                    self.bus.publish("download", **state)
                if not (dest.exists() and (not part["size"] or dest.stat().st_size == part["size"])):
                    url = f"{HF}/{repo}/resolve/main/{quote(part['path'])}?download=true"
                    attempt = 0
                    while True:
                        attempt += 1
                        state["attempt"] = attempt
                        try:
                            await download(url, dest, headers=self._hf_headers(), expected_size=part["size"] or None,
                                           sha256=part.get("sha256"), progress=progress, cancel=cancel, on_phase=phase)
                            break
                        except DownloadCancelled:
                            raise
                        except Exception as exc:
                            retry = self._retryable(exc) and attempt < max_attempts
                            message = describe_error(exc)
                            self._log(f"download {dl_id} attempt {attempt}: {message}")
                            if not retry:
                                raise RuntimeError(message) from exc
                            wait = min(60, 3 * 2 ** (attempt - 1))
                            for left in range(wait, 0, -1):
                                if cancel.is_set():
                                    raise DownloadCancelled()
                                state.update(state="retrying", rate=0, eta=None,
                                             phase=f"{message} Retrying in {left} s (attempt {attempt + 1} of {max_attempts})")
                                if left == wait or left % 5 == 0:
                                    self.bus.publish("download", **state)
                                await asyncio.sleep(1)
                            self._save_download(dl_id)
                done_before += part["size"] or dest.stat().st_size
                if part["kind"] == "mmproj":
                    mmproj_path = str(dest)
                else:
                    paths.append(str(dest))
            state.update(state="verifying", phase="Checking the model file", done=state["total"], rate=0, eta=None)
            self.bus.publish("download", **state)
            if paths:
                row = await asyncio.to_thread(self.register, paths[0], repo, state["name"], paths,
                                              group["parts"][0].get("sha256"), spec.get("catalog_key"), mmproj_path)
                if mmproj_path and not (row.get("settings") or {}).get("mmproj"):
                    self.update_settings(row["id"], {"mmproj": mmproj_path})
                state.update(state="done", phase="Ready", model_id=row["id"])
            else:   # vision part for a model that is already installed
                self.update_settings(spec["model_id"], {"mmproj": mmproj_path, "vision": True})
                self.bus.publish("models_changed")
                state.update(state="done", phase="Vision added", model_id=spec["model_id"])
        except DownloadCancelled:
            state.update(state="cancelled", phase="", rate=0, eta=None)
        except Exception as exc:
            state.update(state="failed", phase="", rate=0, eta=None,
                         error=str(exc) if isinstance(exc, RuntimeError) else describe_error(exc))
            self._log(f"download {dl_id} failed: {state['error']}")
        finally:
            self._finish_download(dl_id)

    def _finish_download(self, dl_id):
        self._cancel.pop(dl_id, None)
        state = self.downloads.get(dl_id)
        if state:
            self._save_download(dl_id)
            if state["state"] in ("done", "cancelled"):
                self.db.delete("downloads", dl_id)
            self.bus.publish("download", **state)

    @staticmethod
    def _retryable(exc):
        import httpx
        if isinstance(exc, (DownloadStalled, httpx.TransportError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in (408, 429, 500, 502, 503, 504)
        if isinstance(exc, ValueError):        # size or checksum mismatch: the .part is resumed or restarted
            return True
        return False

    def _log(self, text):
        from .app_state import STATE
        if STATE:
            STATE.log(text)

    def cancel_download(self, dl_id):
        if dl_id in self._cancel:
            self._cancel[dl_id].set()
        else:
            self.downloads.pop(dl_id, None)
            self.db.delete("downloads", dl_id)
            self.bus.publish("download", id=dl_id, state="removed")

    async def add_vision(self, model_id):
        """Give an installed model its vision part: from its own repository, or a matching official one."""
        row = self.db.get("models", model_id)
        if not row:
            raise KeyError(model_id)
        repos = [row["repo"]] if row.get("repo") else []
        meta = row.get("meta") or {}
        if "basename" not in meta and row.get("path") and Path(row["path"]).exists():
            try:
                meta = await asyncio.to_thread(gguf.read_metadata, row["path"])
            except Exception:
                pass
        base = (meta.get("basename") or meta.get("name") or "").strip()
        size_label = (meta.get("size_label") or "").strip()
        guess = base if (not size_label or size_label.lower() in base.lower()) else f"{base}-{size_label}"
        guess = re.sub(r"\s+", "-", guess)
        if guess:
            try:
                found = await self.search(f"{guess} GGUF", limit=20)
                ranked = sorted((r["repo"] for r in found if r["repo"]),
                                key=lambda r: (r.split("/")[0].lower() not in ("unsloth", "ggml-org", "bartowski"),
                                               guess.lower() not in r.lower()))
                repos += [r for r in ranked if r not in repos][:4]
            except Exception:
                pass
        for repo in repos:
            try:
                listing = await self.repo_files(repo)
            except Exception:
                continue
            if listing.get("mmproj_file"):
                return {"id": self.start_download(repo, None, f"{row['name']} (vision)", None,
                                                  listing["mmproj_file"], model_id), "repo": repo}
        raise ValueError("No vision part was found for this model. It may not support images; vision models are "
                         "marked with an eye in Discover.")

    def import_file(self, path, name=None, mmproj=None):
        path = Path(path)
        if not path.exists() or not path.is_file():
            raise ValueError("That file does not exist")
        if path.suffix.lower() != ".gguf" and not path.name.startswith("sha256-"):  # Ollama blobs have no extension
            raise ValueError("Choose a .gguf model file")
        if mmproj and not Path(mmproj).exists():
            mmproj = None
        return self.register(path, name=name, mmproj=mmproj)

    # ------------------------------------------------------------------ models other apps already downloaded
    def scan_folders(self):
        """Where other local-AI apps keep GGUF models on this computer, plus folders the user added."""
        home, env = Path.home(), os.environ
        hf_home = Path(env.get("HF_HOME") or home / ".cache" / "huggingface")
        local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
        roaming = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
        folders = [
            ("Ollama", Path(env.get("OLLAMA_MODELS") or home / ".ollama" / "models")),
            ("LM Studio", home / ".lmstudio" / "models"),
            ("LM Studio", home / ".cache" / "lm-studio" / "models"),
            ("Hugging Face cache", Path(env.get("HF_HUB_CACHE") or hf_home / "hub")),
            ("GPT4All", local / "nomic.ai" / "GPT4All"),
            ("Jan", home / "jan" / "models"),
            ("Jan", roaming / "Jan" / "data" / "models"),
            ("Models folder", self.paths.models_dir(self._settings())),
        ]
        for extra in self.db.setting("model_scan_folders") or []:
            folders.append(("Your folder", Path(extra)))
        seen, out = set(), []
        for source, folder in folders:
            key = str(folder).lower()
            if key not in seen:
                seen.add(key)
                out.append((source, folder))
        return out

    def _ollama(self, root):
        """Ollama keeps each model as a manifest (name:tag) pointing at a GGUF blob in blobs/."""
        found = []
        manifests = root / "manifests"
        if not manifests.exists():
            return found
        for mf in manifests.rglob("*"):
            if not mf.is_file():
                continue
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            layer = next((l for l in data.get("layers", []) if l.get("mediaType") == "application/vnd.ollama.image.model"), None)
            if not layer or ":" not in str(layer.get("digest", "")):
                continue
            blob = root / "blobs" / layer["digest"].replace(":", "-")
            if not blob.exists():
                continue
            parts = mf.relative_to(manifests).parts            # host / namespace / model / tag
            model, tag = parts[-2], parts[-1]
            namespace = parts[-3] if len(parts) >= 3 else "library"
            name = f"{model}:{tag}" if namespace == "library" else f"{namespace}/{model}:{tag}"
            proj = next((l for l in data.get("layers", []) if l.get("mediaType") == "application/vnd.ollama.image.projector"), None)
            proj_path = root / "blobs" / proj["digest"].replace(":", "-") if proj and ":" in str(proj.get("digest")) else None
            found.append({"path": blob, "name": name, "mmproj": str(proj_path) if proj_path and proj_path.exists() else None})
        return found

    def _gguf_files(self, root, limit=400):
        found = []
        try:
            for dirpath, dirs, files in os.walk(root, followlinks=True):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if not f.lower().endswith(".gguf") or "mmproj" in f.lower():
                        continue
                    m = SPLIT_RE.search(f)
                    if m and m.group(1) != "00001":
                        continue  # only the first part of a split model
                    p = Path(dirpath) / f
                    name = p.stem
                    hf = re.search(r"models--([^\\/]+?)--([^\\/]+)", str(p))
                    if hf:
                        name = f"{hf.group(1)}/{hf.group(2)} · {p.stem}"
                    found.append({"path": p, "name": name})
                    if len(found) >= limit:
                        return found
        except OSError:
            pass
        return found

    def scan_local(self):
        from .hardware import snapshot
        hw = snapshot()
        known = {str(Path(r["path"])).lower(): r["id"] for r in self.db.all("SELECT id, path FROM models") if r["path"]}
        results, scanned = [], []
        for source, folder in self.scan_folders():
            if not folder.exists():
                continue
            scanned.append({"source": source, "folder": str(folder)})
            items = self._ollama(folder) if source == "Ollama" else self._gguf_files(folder)
            for item in items:
                p = item["path"]
                try:
                    real = p.resolve()
                    size = real.stat().st_size
                except OSError:
                    continue
                if size < MIN_MODEL_BYTES:
                    continue
                try:
                    meta = gguf.read_metadata(real)
                except Exception:
                    continue  # not a GGUF llama.cpp can read
                quant = quant_of(p.name)
                mmproj = item.get("mmproj") or (self.sibling_mmproj(p) if source != "Ollama" else None)
                results.append({
                    "path": str(p), "name": item["name"], "source": source, "size": size, "mmproj": mmproj,
                    "vision": bool(mmproj),
                    "architecture": meta.get("architecture"), "quant": quant if quant != "?" else FILE_TYPES.get(meta.get("file_type"), "?"),
                    "added_as": known.get(str(p).lower()) or known.get(str(real).lower()),
                    "fit": self.fit(size, meta, None, hw)})
        uniq = {}
        for r in results:  # the same file can be reachable twice (e.g. a symlinked cache)
            uniq.setdefault(str(Path(r["path"]).resolve()).lower(), r)
        return {"found": sorted(uniq.values(), key=lambda r: (r["added_as"] is not None, r["source"], r["name"])),
                "scanned": scanned}

    # ------------------------------------------------------------------ benchmark
    async def benchmark(self, model_id, llm):
        """Two short calls: a plain reply and a JSON-schema answer. Records prompt/generation speed."""
        results = {}
        async with self.runtime.lease(model_id) as server:
            results["backend"], results["compute"] = server.plan["backend"], server.plan["compute"]
            t0 = time.monotonic()
            r1 = await llm.complete([{"role": "user", "content": "In one sentence, what is a dataset?"}],
                                    max_tokens=60)
            results["reply_seconds"] = round(time.monotonic() - t0, 2)
            results.update({k: r1["timings"].get(k) for k in ("prompt_per_second", "predicted_per_second")})
            schema = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["sum", "mean"]}},
                      "required": ["choice"]}
            t0 = time.monotonic()
            r2 = await llm.complete([{"role": "user", "content": "Airport passengers per month: should yearly "
                                      "totals use sum or mean? Answer as JSON."}], schema=schema, max_tokens=30)
            results["json_seconds"] = round(time.monotonic() - t0, 2)
            results["json_valid"] = isinstance(r2.get("json"), dict)
        results["at"] = now()
        old = (self.db.get("models", model_id) or {}).get("bench") or {}
        if old.get("tune"):
            results["tune"] = old["tune"]
        self.db.update("models", model_id, {"bench": results})
        return results

    # ------------------------------------------------------------------ speed: the suggested configuration
    SPEED_DEFAULTS = {"compute": "auto", "gpu_layers": "auto", "kv_on": "gpu", "moe_cpu": "none", "threads": None,
                      "threads_batch": None, "kv_type": "f16", "flash_attn": "auto", "priority": "normal",
                      "ubatch": None, "spec": "none", "draft_model": None, "ctx": None, "parallel": None}
    TUNE_PROMPT = ("Read this note and answer the question at the end.\n\n" + " ".join([
        "Solar home systems in rural areas combine a panel, a battery and efficient appliances.",
        "Households often start with lights and phone charging, then add a radio, a fan or a television.",
        "Efficient appliances matter because every watt saved shrinks the panel and battery that are needed.",
        "Field data shows that many systems are used for more hours a day than their designers expected."] * 2) +
        "\n\nQuestion: in two sentences, why do efficient appliances matter for solar home systems?")
    REPEAT_PROMPT = ("Copy this list exactly, changing only 'kWh' to 'kilowatt-hours'. Output the list only.\n\n" +
                     "\n".join(f"- Site {i}: daily use {round(1.5 + i * 0.37, 2)} kWh, battery {12 + i} kWh, "
                               f"panel {300 + 25 * i} W" for i in range(1, 9)))
    FOLLOW_UP = "Thanks. Now in one sentence: what is the main risk for these systems?"
    _hw_cache = (0.0, None)

    def hardware(self):
        from .hardware import snapshot
        at, hw = self._hw_cache
        if not hw or time.monotonic() - at > 120:
            hw = snapshot()
            fake = os.environ.get("DVA_TEST_CPU")          # tests only: "6x12" = 6 cores, 12 threads
            if fake:
                hw["cores"], hw["threads"] = (int(x) for x in fake.split("x"))
            self._hw_cache = (time.monotonic(), hw)
        return hw

    def _devices(self):
        from .runtime import is_integrated
        out = []
        for b in self.runtime.gpu_backends():
            for d in self.runtime.list_devices(b):
                if not d.get("error"):
                    out.append({**d, "backend": b, "integrated": is_integrated(d.get("name"))})
        return out

    def _draft_candidates(self, model):
        """Small installed models of the same family that could draft for this one (same architecture)."""
        meta = self.runtime._meta(model)
        out = []
        for row in self.installed():
            if row["id"] == model["id"] or not row["exists"]:
                continue
            m = self.runtime._meta(row)
            if m.get("architecture") == meta.get("architecture") and 0 < (row["size"] or 0) <= 0.3 * (model["size"] or 0):
                out.append(row)
        return sorted(out, key=lambda r: r["size"])

    def suggest(self, model):
        """The configuration that should make this model fastest here, with plain reasons. Measured results from
        "Find the fastest setup" win over rules of thumb."""
        from . import gguf
        hw = self.hardware()
        meta = self.runtime._meta(model)
        s = model.get("settings") or {}
        measured = ((model.get("bench") or {}).get("tune") or {}).get("suggested") or {}
        out, why = {}, []
        devices = self._devices()
        dedicated = [d for d in devices if not d["integrated"]]
        size = int(model.get("size") or 0)
        moe = int(meta.get("expert_count") or 0) > 1
        if "compute" in measured:
            for k in ("compute", "gpu_layers", "kv_on", "moe_cpu"):
                if k in measured:
                    out[k] = measured[k]
            why.append(f"Where it runs was measured on this computer: {measured.get('placement_label', out['compute'].upper())}.")
        elif dedicated:
            vram = max(d["total_mb"] for d in dedicated) * 2**20
            if size * 1.2 < vram:
                out.update(compute="gpu", gpu_layers="all")
                why.append(f"The whole model fits in the {dedicated[0]['name']}'s own memory: everything on the GPU.")
            else:
                out.update(compute="gpu", gpu_layers="auto", moe_cpu="all" if moe else "none")
                why.append("The model is larger than the graphics card's memory: as many layers as fit on the GPU, "
                           "the rest on the CPU" + (", with the MoE experts on the CPU" if moe else "") + ".")
        else:
            out["compute"] = "cpu"
            why.append("Integrated graphics share the computer's memory and were slower than the CPU on a typical "
                       "laptop, so the CPU does the work (measure it with Find the fastest setup)."
                       if devices else "No graphics card with its own memory: the CPU does the work.")
        cores, logical = int(hw.get("cores") or 4), int(hw.get("threads") or 4)
        out["threads"] = int(measured.get("threads") or cores)
        why.append(f"{out['threads']} threads for writing" + (" (measured fastest)" if measured.get("threads") else
                                                              f" — one per physical core ({cores})") + ".")
        if logical > out["threads"]:
            out["threads_batch"] = logical
            why.append(f"{logical} threads for reading prompts (reading gains from every thread).")
        ctx, parallel = int(s.get("ctx") or self.db.setting("default_ctx", 8192)), int(s.get("parallel") or 1)
        out["flash_attn"] = "on"
        if ctx * parallel >= 16384:
            out["kv_type"] = "q8_0"
            why.append("Compact context memory (8-bit): half the memory and faster long chats, with no noticeable "
                       "loss in quality.")
        else:
            out["kv_type"] = "f16"
        # memory: keep the model and its context well inside the computer's memory (swapping is the slowest of all)
        budget = hw["ram_mb"] * 2**20 * (0.7 if out["compute"] == "cpu" or (devices and not dedicated) else 0.85)

        def need(c, p):
            plan = {"ctx": c, "parallel": p, "kv_type": out["kv_type"], "hybrid": gguf.is_hybrid(meta),
                    "mmproj": s.get("mmproj") if s.get("vision", True) else None, "draft": None}
            return self.runtime._need_bytes(model, plan)
        if need(ctx, parallel) > budget:
            fits = [c for c in (65536, 32768, 16384, 8192, 4096) if c <= ctx and need(c, parallel) <= budget]
            if fits:
                out["ctx"] = fits[0]
                why.append(f"Context {fits[0]:,} tokens so the model and its memory fit comfortably "
                           f"({ctx:,} × {parallel} replies would need about {need(ctx, parallel) / 2**30:.0f} GB).")
            else:
                out.update(ctx=8192, parallel=1)
                why.append("Context 8,192 tokens and one reply at a time: anything larger does not fit in memory.")
        out["priority"] = "medium"
        why.append("Above-normal priority, so other programs slow the model down less.")
        if out["compute"] == "gpu" and dedicated:
            out["ubatch"] = 1024
        if measured.get("spec") and measured["spec"] != "none":
            out["spec"] = measured["spec"]
            if measured.get("draft_model"):
                out["draft_model"] = measured["draft_model"]
            why.append({"ngram": "Repeat-aware speculative decoding: replies that reuse text from the conversation "
                                 "(code edits, lists, quotes) are written several tokens at a time (measured faster).",
                        "mtp": "The model's own multi-token prediction writes several tokens at a time (measured faster).",
                        "draft": "A small model of the same family drafts tokens that this one checks (measured faster)."
                        }[measured["spec"]])
        else:
            out["spec"] = "none"
        if gguf.is_hybrid(meta):
            why.append("This is a hybrid model: KimiLM keeps close checkpoints so follow-up messages re-read only "
                       "what is new.")
        defaults = {**self.SPEED_DEFAULTS, "threads": cores, "ctx": ctx, "parallel": parallel}
        defaults["threads_batch"] = s.get("threads") or cores
        differs = []
        for k, v in out.items():
            have = s.get(k)
            have = defaults.get(k) if have in (None, "") else have
            if (defaults.get(k) if v in (None, "") else v) != have:
                differs.append(k)
        tune = (model.get("bench") or {}).get("tune") or {}
        return {"settings": out, "reasons": why, "differs": differs, "measured": bool(measured),
                "measured_at": tune.get("at"), "reuse": tune.get("reuse")}

    def apply_suggested(self, model_id):
        model = self.db.get("models", model_id)
        if not model:
            raise KeyError(model_id)
        sug = self.suggest(model)
        values = {k: v for k, v in sug["settings"].items()}
        for k in ("gpu_layers", "kv_on", "moe_cpu", "ubatch", "draft_model"):
            values.setdefault(k, None)
        self.update_settings(model_id, values)
        self.bus.publish("models_changed")
        return self.suggest(self.db.get("models", model_id))

    # ------------------------------------------------------------------ find the fastest setup (measured)
    def placements(self, model):
        """The setups worth comparing on this computer for this model."""
        meta = self.runtime._meta(model)
        out = [("CPU only", {"compute": "cpu"})]
        backends = self.runtime.gpu_backends()
        if not backends:
            return out
        layers = int(meta.get("block_count") or 0) + 1        # llama.cpp also counts the output layer
        out.append(("Everything on the GPU", {"compute": "gpu", "gpu_layers": "all"}))
        if layers > 2:
            out.append(("Half the layers on the GPU", {"compute": "gpu", "gpu_layers": layers // 2}))
        if int(meta.get("expert_count") or 0) > 1:
            out.append(("GPU, with the MoE experts on the CPU", {"compute": "gpu", "gpu_layers": "all", "moe_cpu": "all"}))
        devices = [d for b in backends for d in self.runtime.list_devices(b)]
        from .runtime import is_integrated
        if any(not is_integrated(d.get("name")) for d in devices):
            out.append(("GPU, context memory on the CPU", {"compute": "gpu", "gpu_layers": "all", "kv_on": "cpu"}))
        return out

    def tune(self, model_id, llm):
        """Start measuring in the background; progress arrives as "tune" events. The result becomes the
        suggested configuration (applied only when the user presses Use suggested configuration)."""
        model = self.db.get("models", model_id)
        if not model:
            raise KeyError(model_id)
        current = self.tunes.get(model_id)
        if current and current.get("state") == "running":
            return current
        meta = self.runtime._meta(model)
        from . import gguf
        hw = self.hardware()
        extra = 3 if int(hw.get("threads") or 4) > int(hw.get("cores") or 4) else 2
        total = len(self.placements(model)) + extra + 2 + 1 + int(gguf.has_mtp(meta)) + int(bool(self._draft_candidates(model)))
        self.tunes[model_id] = {"model_id": model_id, "state": "running", "step": 0, "total": total, "results": [],
                                "label": "", "stage": ""}
        task = asyncio.ensure_future(self._tune(model_id, llm))
        self._tune_tasks = getattr(self, "_tune_tasks", {})
        self._tune_tasks[model_id] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self.tunes[model_id]

    def stop_tune(self, model_id):
        task = getattr(self, "_tune_tasks", {}).get(model_id)
        if task and not task.done():
            task.cancel()
        return self.tunes.get(model_id) or {"state": "idle"}

    async def _measure(self, model_id, llm, setup, repeat=False, follow_up=False):
        out = {}
        async with self.runtime.lease(model_id, compute=setup.get("compute"),
                                      overrides={**setup, "ctx": 4096, "parallel": 1}) as server:
            msgs = [{"role": "user", "content": self.TUNE_PROMPT}]
            r = await llm.complete(msgs, max_tokens=40, temperature=0, server=server)
            t = r.get("timings") or {}
            out.update(reads=round(t.get("prompt_per_second") or 0, 1), writes=round(t.get("predicted_per_second") or 0, 2),
                       backend=server.plan["backend"], placement=getattr(server, "placement", {}),
                       note=server.plan.get("note"))
            if repeat:
                r2 = await llm.complete([{"role": "user", "content": self.REPEAT_PROMPT}], max_tokens=96,
                                        temperature=0, server=server)
                out["writes_repeat"] = round((r2.get("timings") or {}).get("predicted_per_second") or 0, 2)
            if follow_up:   # how much of a follow-up message is reused from the cache instead of re-read
                msgs = msgs + [{"role": "assistant", "content": r.get("content") or "OK."},
                               {"role": "user", "content": self.FOLLOW_UP}]
                r3 = await llm.complete(msgs, max_tokens=24, temperature=0, server=server)
                t3 = r3.get("timings") or {}
                cached, read = int(t3.get("cache_n") or 0), int(t3.get("prompt_n") or 0)
                out["reuse"] = {"cached": cached, "read": read,
                                "percent": round(100 * cached / max(1, cached + read))}
        return out

    async def _tune(self, model_id, llm):
        from . import gguf
        state = self.tunes[model_id]
        model = self.db.get("models", model_id)
        meta = self.runtime._meta(model)
        hw = self.hardware()
        cores, logical = int(hw.get("cores") or 4), int(hw.get("threads") or 4)
        base = {"threads": cores, "threads_batch": logical if logical > cores else None, "flash_attn": "on",
                "priority": "medium", "spec": "none"}

        def publish():
            self.bus.publish("tune", **state)

        async def run(stage, label, setup, **kw):
            state.update(step=state["step"] + 1, label=label, stage=stage)
            publish()
            row = {"stage": stage, "label": label, "settings": setup}
            try:
                row.update(await self._measure(model_id, llm, {**base, **setup}, **kw))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                row["error"] = str(exc)[:300]
            state["results"].append(row)
            publish()
            try:
                await self.runtime.unload(model_id)        # the next setup starts from a fresh load
            except Exception:
                pass
            return row

        ok = lambda r: not r.get("error") and r.get("writes")   # noqa: E731
        try:
            # 1. where it runs
            places = []
            for label, setup in self.placements(model):
                places.append(await run("Where it runs", label, setup))
            good = [r for r in places if ok(r)]
            if not good:
                raise RuntimeError("No setup could be measured: " + (places[0].get("error") or "unknown error"))
            top = max(r["writes"] for r in good)
            best = max((r for r in good if r["writes"] >= top * 0.95), key=lambda r: r["reads"])
            chosen = {k: v for k, v in base.items() if v is not None}     # what was measured, in full
            chosen.update(best["settings"])
            chosen_label = best["label"]
            # 2. threads (when the CPU does part of the work)
            on_cpu = chosen["compute"] == "cpu" or chosen.get("gpu_layers") not in ("all",) or chosen.get("moe_cpu")
            if on_cpu:
                tries = sorted({max(2, cores - 2), max(2, (cores + logical) // 2), logical} - {cores})
                rows = [best]
                for n in tries:
                    rows.append(await run("Threads", f"{n} threads for writing", {**chosen, "threads": n}))
                good = [r for r in rows if ok(r)]
                fastest = max(good, key=lambda r: r["writes"])
                if fastest is not best and fastest["writes"] >= best["writes"] * 1.03:
                    chosen["threads"] = fastest["settings"]["threads"]
                    best = fastest
            # 3. writing several tokens at a time (speculative decoding)
            baseline = await run("Speed-ups", "Without speculative decoding", chosen, repeat=True)
            options = [("ngram", "Repeat-aware speculative decoding", {"spec": "ngram"})]
            if gguf.has_mtp(meta):
                options.append(("mtp", "Built-in multi-token prediction", {"spec": "mtp"}))
            drafts = self._draft_candidates(model)
            if drafts:
                options.append(("draft", f"Draft model: {drafts[0]['name']}", {"spec": "draft", "draft_model": drafts[0]["id"]}))
            spec_pick = None
            if ok(baseline):
                for key, label, extra in options:
                    r = await run("Speed-ups", label, {**chosen, **extra}, repeat=True)
                    if ok(r) and r.get("writes_repeat") and r["writes"] >= baseline["writes"] * 0.95 and \
                            r["writes_repeat"] >= (baseline.get("writes_repeat") or 0) * 1.12:
                        gain = r["writes_repeat"] / max(0.01, baseline.get("writes_repeat") or 0.01)
                        if not spec_pick or gain > spec_pick[1]:
                            spec_pick = (extra, gain)
            if spec_pick:
                chosen.update(spec_pick[0])
            # 4. follow-up messages: how much is reused from the cache
            final = await run("Follow-ups", "Follow-up message (cache reuse)", chosen, follow_up=True)
            suggested = {k: v for k, v in chosen.items() if k in self.SPEED_DEFAULTS}
            suggested["placement_label"] = chosen_label
            state.update(state="done", best=chosen_label, suggested=suggested, reuse=final.get("reuse"))
            row = self.db.get("models", model_id)
            bench = {**(row.get("bench") or {}), "tune": {"at": now(), "best": chosen_label, "suggested": suggested,
                                                         "results": state["results"], "reuse": final.get("reuse")}}
            self.db.update("models", model_id, {"bench": bench})
        except asyncio.CancelledError:
            state.update(state="stopped")
            try:
                await self.runtime.unload(model_id)
            except Exception:
                pass
        except Exception as exc:
            state.update(state="failed", error=str(exc)[:300])
        finally:
            publish()
            self.bus.publish("models_changed")
