"""Image generation and editing (2.11.0): on this computer first, online when the user chooses.

On this computer   stable-diffusion.cpp (the same family as llama.cpp: plain C/C++, no Python), downloaded in the
                   app from a pinned release like the llama.cpp runtimes: a CPU build, and Vulkan or CUDA builds
                   for graphics cards. KimiLM runs its command-line program once per request: the model is
                   loaded, the pictures are made, the process ends and all of its memory is free again. That is
                   slower per picture than a server that keeps the model loaded, and it is the right trade here:
                   memory is the scarce thing, and a text model is usually waiting for it.
One memory budget  Before a local picture is made, the text-model runtime is asked to make room (idle models
                   are unloaded, least recently used first), and only one picture job runs at a time.
Models             A recipe names the parts a model needs (the diffusion model, its text encoder, its VAE) and
                   where they are published; the exact files are chosen from the repository's listing when they
                   are downloaded, because publishers rename files. A model the user already has (a checkpoint
                   from another app) is added by its path and used in place.
Online             OpenAI (the Images API) and Google (Gemini's native image output), with the keys already
                   saved for those providers, counted against the monthly online budget. A picture is only sent
                   online for editing after the user allowed it. Anthropic's Claude does not make pictures (it
                   reads them): a chat on Claude can still ask for one through the tools below, and the picture
                   is made by whichever image model is chosen here.
Records            Every picture is a file in the chat's folder (images/) and a row in table `media` with the
                   prompt, model, seed and settings, so it can be found, repeated and varied."""
import asyncio
import base64
import json
import os
import random
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

import httpx

from .db import new_id, now
from .download import download, fetch_json
from .providers import cloud_id, month_key

HF = "https://huggingface.co"
EXE = "sd-cli.exe" if os.name == "nt" else "sd-cli"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
# release files by operating system and backend: every word must be in the file's name, none of `not`
SD_ASSETS = {
    "windows": {"cpu": {"has": ["bin-win-cpu-x64"]}, "vulkan": {"has": ["bin-win-vulkan-x64"]},
                "cuda": {"has": ["bin-win-cuda12-x64"], "runtime": ["cudart-sd-bin-win-cu12-x64"]}},
    "linux": {"cpu": {"has": ["bin-Linux-", "x86_64"], "not": ["vulkan", "rocm"]},
              "vulkan": {"has": ["bin-Linux-", "x86_64", "vulkan"]}},
    "darwin": {"metal": {"has": ["bin-Darwin-"]}},
}
SLOT_FLAGS = {"model": "-m", "diffusion_model": "--diffusion-model", "vae": "--vae", "llm": "--llm",
              "clip_l": "--clip_l", "clip_g": "--clip_g", "t5xxl": "--t5xxl", "tae": "--tae"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")

# Recommended models for this computer. `sources` are tried in order; `prefer` are substrings of file names, best
# first. Sizes are rough. Licences are what the publishers stated when this list was written: the model's page is
# the authority, and the window links to it.
RECIPES = [
    {"key": "z-image-turbo", "name": "Z-Image Turbo", "approx_gb": 6.5, "min_ram_gb": 12, "edit": "img2img",
     "about": "The recommended start: good pictures in 8 steps, and it runs on a laptop without a graphics card "
              "(minutes per picture) as well as on small graphics cards.",
     "licence": "Apache-2.0", "page": "https://huggingface.co/Tongyi-MAI/Z-Image-Turbo",
     "defaults": {"steps": 8, "cfg": 1.0, "width": 768, "height": 768, "args": "--offload-to-cpu"},
     "parts": [
         {"slot": "diffusion_model", "sources": [{"repo": "leejet/Z-Image-Turbo-GGUF", "match": r"\.gguf$"}],
          "prefer": ["Q4_K", "Q4_0", "Q3_K", "Q5", "Q6", "Q8"]},
         {"slot": "llm", "sources": [{"repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF", "match": r"\.gguf$"}],
          "prefer": ["Q4_K_M", "Q4_K_S", "Q4_0", "Q5_K_M"]},
         {"slot": "vae", "sources": [{"repo": "Comfy-Org/z_image_turbo", "match": r"vae/.*\.(safetensors|sft)$"},
                                     {"repo": "black-forest-labs/FLUX.1-schnell", "match": r"^ae\.(safetensors|sft)$"}]}]},
    {"key": "sd15", "name": "Stable Diffusion 1.5", "approx_gb": 4.3, "min_ram_gb": 8, "edit": "img2img",
     "about": "The smallest and most widely supported model: 512-pixel pictures, modest quality, the least memory. "
              "A good first test on an older computer.",
     "licence": "CreativeML OpenRAIL-M", "page": "https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5",
     "defaults": {"steps": 20, "cfg": 7.0, "width": 512, "height": 512, "args": ""},
     "parts": [{"slot": "model", "sources": [{"repo": "stable-diffusion-v1-5/stable-diffusion-v1-5",
                                              "match": r"^v1-5-pruned-emaonly\.safetensors$"}]}]},
    {"key": "sdxl-turbo", "name": "SDXL Turbo", "approx_gb": 7.0, "min_ram_gb": 12, "edit": "img2img",
     "about": "Fast drafts: a 512-pixel picture in 1 to 4 steps.",
     "licence": "Stability AI Community License (read its terms before commercial use)",
     "page": "https://huggingface.co/stabilityai/sdxl-turbo",
     "defaults": {"steps": 4, "cfg": 1.0, "width": 512, "height": 512, "args": ""},
     "parts": [{"slot": "model", "sources": [{"repo": "stabilityai/sdxl-turbo", "match": r"sd_xl_turbo.*fp16\.safetensors$"},
                                             {"repo": "stabilityai/sdxl-turbo", "match": r"sd_xl_turbo.*\.safetensors$"}]}]},
    {"key": "flux2-klein-4b", "name": "FLUX.2 klein 4B", "approx_gb": 5.5, "min_ram_gb": 16, "edit": "reference",
     "about": "Higher quality in 4 steps, and it can edit: give it a picture and say what to change. Best with a "
              "graphics card of 8 GB or more; slow on a processor alone.",
     "licence": "Apache-2.0", "page": "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B",
     "defaults": {"steps": 4, "cfg": 1.0, "width": 1024, "height": 1024, "args": "--offload-to-cpu"},
     "parts": [
         {"slot": "diffusion_model", "sources": [{"repo": "leejet/FLUX.2-klein-4B-GGUF", "match": r"\.gguf$"}],
          "prefer": ["Q4_K", "Q4_0", "Q5", "Q6", "Q8"]},
         {"slot": "llm", "sources": [{"repo": "unsloth/Qwen3-4B-GGUF", "match": r"\.gguf$"}],
          "prefer": ["Q4_K_M", "Q4_K_S", "Q4_0", "Q5_K_M"]},
         # the FLUX.2 VAE as one file; the small decoder only as a last resort, with its encoder (editing needs it)
         {"slot": "vae", "sources": [{"repo": "Comfy-Org/flux2-dev", "match": r"vae/flux2-vae\.safetensors$"},
                                     {"repo": "unsloth/FLUX.2-VAE", "match": r"\.safetensors$"},
                                     {"repo": "black-forest-labs/FLUX.2-small-decoder",
                                      "match": r"full_encoder_small_decoder\.safetensors$"}]}]},
]

# Online image models. The names are suggestions for when the provider's own list cannot be read: providers
# rename and retire image models often (OpenAI's first one was switched off in October 2026).
ONLINE = {
    "openai": {"name": "OpenAI", "match": r"image", "skip": r"embed|moderation",
               "suggest": ["gpt-image-2.6-flare", "gpt-image-2.6-sunburst", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"],
               "per_image": 0.04},
    "google": {"name": "Google", "match": r"image", "skip": r"embed|imagen-.*-ultra-never",
               "suggest": ["gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview", "gemini-2.5-flash-image"],
               "per_image": 0.04},
}
RATIOS = {"1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "3:2": 1.5, "2:3": 2 / 3, "16:9": 16 / 9, "9:16": 9 / 16}


class ImageError(RuntimeError):
    pass


def os_key():
    return {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:48] or "image-model"


def nearest_ratio(width, height):
    r = (width or 1) / (height or 1)
    return min(RATIOS, key=lambda k: abs(RATIOS[k] - r))


def openai_size(width, height):
    if not width or not height or abs(width - height) < 64:
        return "1024x1024"
    return "1536x1024" if width > height else "1024x1536"


def pick_file(files, part):
    """The file of a repository listing that fits a recipe's part: the first `prefer` word wins, else the smallest."""
    for source in part["sources"]:
        rows = [f for f in files.get(source["repo"], []) if re.search(source["match"], f["path"], re.I)]
        if not rows:
            continue
        for word in part.get("prefer") or []:
            hit = [f for f in rows if word.lower() in f["path"].lower()]
            if hit:
                return source["repo"], min(hit, key=lambda f: f["size"] or 0)
        return source["repo"], min(rows, key=lambda f: f["size"] or 0)
    return None, None


class Images:
    def __init__(self, app):
        self.app, self.db = app, app.db
        self.lock = asyncio.Lock()            # one picture job at a time: they are heavy
        self.installing = {}                  # backend -> state
        self.downloads = {}                   # recipe key -> state
        self.jobs = {}                        # requests from the Images window: id -> state
        self.tasks = {}                       # id -> the asyncio task (to stop it)
        versions = json.loads(Path(__file__).with_name("versions.json").read_text(encoding="utf-8"))
        self.tag, self.repo = versions["sd_cpp_tag"], versions["sd_cpp_repo"]

    # ------------------------------------------------------------------ the engine (stable-diffusion.cpp)
    def engine_dir(self, backend):
        return self.app.paths.data / "runtimes" / f"sd-{backend}"

    def engines(self):
        """Installed builds: {backend: path of sd-cli}. DVA_SD_CLI points at a build of your own."""
        out = {}
        custom = os.environ.get("DVA_SD_CLI")
        if custom and Path(custom).exists():
            out["custom"] = Path(custom)
        for backend in ("cuda", "metal", "vulkan", "cpu"):
            exe = self.engine_dir(backend) / EXE
            if exe.exists():
                out[backend] = exe
        return out

    def available_engines(self):
        return list(SD_ASSETS.get(os_key(), {}))

    def choose_engine(self):
        """Which build makes the pictures: the user's choice, else a graphics-card build when a real card is there
        (integrated graphics are often slower than the processor), else the processor build."""
        have = self.engines()
        if not have:
            return None, None
        want = self.db.setting("image_compute", "auto")
        if want in have:
            return want, have[want]
        if "custom" in have:
            return "custom", have["custom"]
        if want != "cpu":
            cards = self._cards()
            for backend in ("cuda", "metal", "vulkan"):
                if backend not in have:
                    continue
                if backend == "metal" or (backend == "vulkan" and cards) or \
                        (backend == "cuda" and any(g.get("vendor") == "nvidia" for g in cards)):
                    return backend, have[backend]
        backend = "cpu" if "cpu" in have else next(iter(have))
        return backend, have[backend]

    def _cards(self):
        """Graphics cards that are not integrated ones (looked up at most once a minute: it starts programs)."""
        at, cards = getattr(self, "_cards_seen", (0, []))
        if time.monotonic() - at > 60 or not at:
            try:
                from .hardware import gpus
                cards = [g for g in gpus() if not g.get("integrated")]
            except Exception:
                cards = []
            self._cards_seen = (time.monotonic(), cards)
        return cards

    async def install_engine(self, backend):
        """Download a build of the pinned stable-diffusion.cpp release (and the CUDA runtime files for CUDA)."""
        if backend in self.installing:
            return
        spec = SD_ASSETS.get(os_key(), {}).get(backend)
        if not spec:
            raise ImageError(f"No {backend} build of the image engine is published for this operating system")
        self.installing[backend] = {"state": "starting"}
        publish = lambda **kw: self.app.bus.publish("image_engine", backend=backend, **kw)   # noqa: E731
        try:
            def choose(assets):
                def one(has, skip=()):
                    return next((a for a in assets if a["name"].endswith(".zip") and all(w in a["name"] for w in has)
                                 and not any(w in a["name"] for w in skip)), None)
                found = [one(spec["has"], spec.get("not", ()))]
                if spec.get("runtime"):
                    found.append(one(spec["runtime"]))
                return found if all(found) else None
            wanted, tag = None, self.tag
            api = f"https://api.github.com/repos/{self.repo}/releases"
            try:
                wanted = choose((await fetch_json(f"{api}/tags/{tag}")).get("assets", []))
                if not wanted:                 # a release without this build: the newest one that has it
                    for rel in await fetch_json(f"{api}?per_page=20"):
                        wanted = choose(rel.get("assets", []))
                        if wanted:
                            tag = rel.get("tag_name")
                            publish(action="install_note", note=f"using release {tag}")
                            break
            except Exception as exc:           # GitHub's listing is rate-limited: Windows file names are known
                self.app.log(f"image engine: release listing failed ({type(exc).__name__}: {exc})")
            if not wanted and os_key() == "windows":
                short = tag.rsplit("-", 1)[-1]
                base = f"https://github.com/{self.repo}/releases/download/{tag}"
                names = [f"sd-master-{short}-{spec['has'][0]}.zip"] + [f"{r}.zip" for r in spec.get("runtime", [])]
                wanted = [{"name": n, "browser_download_url": f"{base}/{n}", "size": None} for n in names]
            if not wanted:
                raise ImageError(f"No download of the image engine ({backend}) was found for this computer")
            target = self.engine_dir(backend)
            tmp = target.with_name(target.name + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            from .runtime import Runtime
            for asset in wanted:
                archive = self.app.paths.data / "runtimes" / asset["name"]

                def progress(done, total, rate, name=asset["name"]):
                    self.installing[backend] = {"state": "downloading", "file": name, "done": done, "total": total}
                    publish(action="install_progress", file=name, done=done, total=total, rate=rate)
                await download(asset["browser_download_url"], archive, expected_size=asset.get("size"), progress=progress)
                self.installing[backend] = {"state": "extracting"}
                await asyncio.to_thread(Runtime._extract, archive, tmp)
                archive.unlink(missing_ok=True)
            exe = next(iter(tmp.rglob(EXE)), None)
            if not exe:
                raise ImageError("The download did not contain the image program (sd-cli)")
            shutil.rmtree(target, ignore_errors=True)
            if exe.parent != tmp:
                for item in list(tmp.rglob("*")):
                    if item.is_file() and item.parent != exe.parent and not (exe.parent / item.name).exists():
                        shutil.move(str(item), str(exe.parent / item.name))
                shutil.move(str(exe.parent), str(target))
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                shutil.move(str(tmp), str(target))
            if os.name != "nt":
                (target / EXE).chmod(0o755)
            self.db.set_setting(f"image_engine_tag:{backend}", tag)
            publish(action="installed")
        except Exception as exc:
            publish(action="install_failed", error=str(exc)[:300])
            raise
        finally:
            self.installing.pop(backend, None)

    def remove_engine(self, backend):
        shutil.rmtree(self.engine_dir(backend), ignore_errors=True)

    # ------------------------------------------------------------------ models
    def models(self):
        rows = self.db.all("SELECT * FROM image_models ORDER BY added")
        default = self.db.setting("image_model", "")
        for r in rows:
            r["default"] = r["id"] == default
            if r["kind"] == "local":
                r["missing"] = [slot for slot, path in (r.get("files") or {}).items() if not Path(path).exists()]
                r["ready"] = not r["missing"] and bool(self.engines())
            else:
                r["ready"] = self.app.providers.ready(r["provider"])
        return rows

    def get(self, model_id):
        return next((m for m in self.models() if m["id"] == model_id), None)

    def default_model(self):
        """The model a request without a choice uses: the chosen default, else a ready local one, else online."""
        rows = [m for m in self.models() if m["ready"]]
        return next((m for m in rows if m["default"]), None) or next((m for m in rows if m["kind"] == "local"), None) \
            or (rows[0] if rows else None)

    def available(self):
        return self.default_model() is not None

    def _insert(self, row):
        base, n = row["id"], 2
        while self.db.get("image_models", row["id"]):
            row["id"] = f"{base}-{n}"
            n += 1
        self.db.insert("image_models", {**row, "added": now()})
        if not self.db.setting("image_model", ""):
            self.db.set_setting("image_model", row["id"])
        self.app.bus.publish("images_changed")
        return self.get(row["id"])

    def add_local(self, name, files, settings=None, recipe="", licence="", edit="img2img"):
        """A model whose files are already on this computer (used in place, never moved or deleted)."""
        files = {slot: str(Path(path)) for slot, path in (files or {}).items() if path and slot in SLOT_FLAGS}
        if not files or not ({"model", "diffusion_model"} & set(files)):
            raise ValueError("Give the model's file: a checkpoint (.safetensors, .ckpt, .gguf) or a diffusion model")
        for slot, path in files.items():
            if not Path(path).is_file():
                raise ValueError(f"There is no file at {path}")
        defaults = {"steps": 20, "cfg": 7.0, "width": 512, "height": 512, "args": "", **(settings or {})}
        size = sum(Path(p).stat().st_size for p in files.values())
        return self._insert({"id": slug(name or Path(next(iter(files.values()))).stem), "kind": "local",
                             "name": (name or Path(next(iter(files.values()))).stem)[:80], "recipe": recipe,
                             "provider": "", "remote_id": "", "files": files, "settings": {**defaults, "edit": edit},
                             "size": size, "licence": licence})

    def add_online(self, provider, remote_id, name=None):
        if provider not in ONLINE:
            raise ValueError("Online pictures come from OpenAI or Google. Claude reads pictures but does not make them.")
        remote_id = str(remote_id or "").strip().removeprefix("models/")
        if not remote_id:
            raise ValueError("Choose the provider's image model")
        return self._insert({"id": slug(f"{provider}-{remote_id}"), "kind": "online", "provider": provider,
                             "remote_id": remote_id, "name": name or f"{ONLINE[provider]['name']} · {remote_id}",
                             "recipe": "", "files": {}, "size": 0, "licence": "",
                             "settings": {"width": 1024, "height": 1024, "edit": "reference",
                                          "per_image": ONLINE[provider]["per_image"]}})

    def update(self, model_id, data):
        row = self.db.get("image_models", model_id)
        if not row:
            raise KeyError(model_id)
        if data.get("default"):
            self.db.set_setting("image_model", model_id)
        values = {}
        if "name" in data:
            values["name"] = str(data["name"])[:80]
        if isinstance(data.get("settings"), dict):
            values["settings"] = {**(row.get("settings") or {}), **data["settings"]}
        if values:
            self.db.update("image_models", model_id, values)
        self.app.bus.publish("images_changed")
        return self.get(model_id)

    def remove(self, model_id, delete_files=False):
        row = self.db.get("image_models", model_id)
        if not row:
            raise KeyError(model_id)
        if delete_files and row.get("recipe"):          # only what KimiLM downloaded itself
            folder = self.app.paths.models_dir({"models_dir": self.db.setting("models_dir")}) / "image" / row["recipe"]
            shutil.rmtree(folder, ignore_errors=True)
        self.db.delete("image_models", model_id)
        if self.db.setting("image_model", "") == model_id:
            self.db.set_setting("image_model", "")
        self.app.bus.publish("images_changed")

    def recipes(self):
        try:
            import psutil
            ram_gb = psutil.virtual_memory().total / 2**30
        except Exception:
            ram_gb = 0
        have = {m.get("recipe") for m in self.models()}
        return [{k: r[k] for k in ("key", "name", "about", "approx_gb", "min_ram_gb", "licence", "page", "edit")}
                | {"installed": r["key"] in have, "fits": not ram_gb or ram_gb >= r["min_ram_gb"] * 0.93,   # "8 GB" reports as 7.8
                   "download": self.downloads.get(r["key"])} for r in RECIPES]

    def _hf_headers(self):
        token = self.app.secrets.get("hf_token")
        return {"Authorization": f"Bearer {token}"} if token else {}

    def start_download(self, key):
        recipe = next((r for r in RECIPES if r["key"] == key), None)
        if not recipe:
            raise KeyError(key)
        state = self.downloads.get(key)
        if state and state["state"] in ("starting", "downloading"):
            return state
        state = self.downloads[key] = {"key": key, "name": recipe["name"], "state": "starting", "done": 0, "total": 0,
                                       "file": "", "error": None, "cancel": False}
        self.app.spawn(self._download(recipe, state))
        return state

    def cancel_download(self, key):
        if key in self.downloads:
            self.downloads[key]["cancel"] = True

    async def _download(self, recipe, state):
        publish = lambda: self.app.bus.publish("image_download", **{k: v for k, v in state.items() if k != "cancel"})  # noqa: E731
        try:
            repos = {s["repo"] for p in recipe["parts"] for s in p["sources"]}
            listing = {}
            for repo in repos:
                try:
                    tree = await fetch_json(f"{HF}/api/models/{repo}/tree/main?recursive=true", self._hf_headers())
                    listing[repo] = [{"path": f["path"], "size": (f.get("lfs") or {}).get("size") or f.get("size") or 0}
                                     for f in tree if f.get("type") == "file"]
                except Exception as exc:
                    self.app.log(f"image model {recipe['key']}: listing {repo} failed: {type(exc).__name__}: {exc}")
            chosen = []
            for part in recipe["parts"]:
                repo, f = pick_file(listing, part)
                if not f:
                    names = ", ".join(s["repo"] for s in part["sources"])
                    raise ImageError(f"The {part['slot'].replace('_', ' ')} was not found in {names}. The publisher "
                                     "may have moved it, or the repository needs a Hugging Face token (Settings).")
                chosen.append((part["slot"], repo, f))
            state["total"] = sum(f["size"] for _, _, f in chosen)
            folder = self.app.paths.models_dir({"models_dir": self.db.setting("models_dir")}) / "image" / recipe["key"]
            files, before = {}, 0
            from urllib.parse import quote
            for slot, repo, f in chosen:
                dest = folder / Path(f["path"]).name
                state.update(state="downloading", file=Path(f["path"]).name)

                def progress(done, total, rate, before=before):
                    state.update(done=before + done, rate=rate)
                    publish()
                if not (dest.exists() and f["size"] and dest.stat().st_size == f["size"]):
                    await download(f"{HF}/{repo}/resolve/main/{quote(f['path'])}?download=true", dest,
                                   headers=self._hf_headers(), expected_size=f["size"] or None, progress=progress,
                                   cancel=lambda: state["cancel"])
                before += f["size"]
                files[slot] = str(dest)
            self.add_local(recipe["name"], files, recipe["defaults"], recipe=recipe["key"], licence=recipe["licence"],
                           edit=recipe.get("edit", "img2img"))
            state.update(state="done", done=state["total"])
        except Exception as exc:
            state.update(state="cancelled" if state["cancel"] else "failed", error=str(exc)[:300])
            self.app.log(f"image model {recipe['key']}: {type(exc).__name__}: {exc}")
        publish()
        self.app.bus.publish("images_changed")

    async def online_candidates(self, provider):
        """The provider's own image models (from its model list), with suggestions when the list cannot be read."""
        spec = ONLINE[provider]
        try:
            conf = self.app.providers.conf(provider)
            async with httpx.AsyncClient(timeout=30) as http:
                r = await http.get(conf["base"].rstrip("/") + "/models", headers=self.app.providers.headers(provider))
            r.raise_for_status()
            data = r.json()
            ids = [str(m.get("id") or m.get("name") or "").removeprefix("models/") for m in data.get("data") or data.get("models") or []]
            found = sorted({i for i in ids if re.search(spec["match"], i, re.I) and not re.search(spec["skip"], i, re.I)},
                           reverse=True)
            if found:
                return {"models": found, "from": "provider"}
        except Exception as exc:
            self.app.log(f"image models of {provider}: {type(exc).__name__}: {exc}")
        return {"models": spec["suggest"], "from": "suggestions"}

    def status(self):
        backend, _ = self.choose_engine()
        return {"engines": {"installed": list(self.engines()), "available": self.available_engines(),
                            "installing": self.installing, "in_use": backend, "tag": self.tag,
                            "compute": self.db.setting("image_compute", "auto")},
                "models": self.models(), "recipes": self.recipes(),
                "online": [{"id": p, "name": s["name"], "ready": self.app.providers.ready(p)} for p, s in ONLINE.items()],
                "default": (self.default_model() or {}).get("id"),
                "jobs": list(self.jobs.values())[-6:],
                "timeout_minutes": self.db.setting("image_timeout_minutes", 60),
                "claude": "Claude reads pictures but does not make them. A chat that runs on Claude can still ask "
                          "for pictures: they are made by the image model chosen here."}

    # ------------------------------------------------------------------ making pictures
    def _record(self, path, model, prompt, params, chat_id, source):
        row = {"id": new_id("med_"), "kind": "image", "path": str(path), "chat_id": chat_id, "prompt": prompt[:4000],
               "model": model["id"], "model_name": model["name"], "source": source, "params": params, "created": now()}
        self.db.insert("media", row)
        return {**row, "name": Path(path).name}

    def library(self, limit=60, chat_id=None):
        sql, args = "SELECT * FROM media WHERE kind='image'", []
        if chat_id:
            sql += " AND chat_id=?"
            args.append(chat_id)
        rows = self.db.all(sql + " ORDER BY created DESC LIMIT ?", [*args, limit])
        return [{**r, "name": Path(r["path"]).name, "exists": Path(r["path"]).exists()} for r in rows]

    @staticmethod
    def shape_of(image, settings):
        """Width and height with the picture's proportions and about as many pixels as the model is good at."""
        try:
            from PIL import Image
            with Image.open(image) as img:
                w, h = img.size
            area = int(settings.get("width") or 512) * int(settings.get("height") or 512)
            scale = (area / max(1, w * h)) ** 0.5
            return max(64, int(round(w * scale / 16) * 16)), max(64, int(round(h * scale / 16) * 16))
        except Exception:
            return None, None

    async def generate(self, prompt, *, model=None, out_dir, negative="", width=None, height=None, steps=None,
                       cfg=None, seed=None, count=1, image=None, mask=None, strength=None, chat_id=None,
                       job_id=None, on_status=None):
        """Make `count` pictures from a prompt; with `image`, change that picture instead (optionally only where
        `mask` is white). Returns the records of the saved pictures."""
        prompt = (prompt or "").strip()
        if not prompt:
            raise ImageError("Say what the picture should show")
        row = self.get(model) if model else self.default_model()
        if not row and model:      # by name, as an agent would say it
            row = next((m for m in self.models() if model.lower() in (m["name"].lower(), m["remote_id"].lower())), None)
        if not row:
            raise ImageError("No image model is set up yet. Open Images and download one for this computer, or "
                             "switch on an online one (OpenAI or Google)." if not model else
                             f"There is no image model called {model!r}. Installed: "
                             + (", ".join(m["id"] for m in self.models()) or "none"))
        if not row["ready"]:
            raise ImageError(f"{row['name']} cannot be used right now: " + (
                "its files are missing (" + ", ".join(row.get("missing") or []) + ")" if row.get("missing") else
                "the image engine is not installed (Images → Engine)" if row["kind"] == "local" else
                "its provider has no key (Models → Online)"))
        count = max(1, min(4, int(count or 1)))
        s = row.get("settings") or {}
        if image and not (width and height):          # a change keeps the picture's own shape
            width, height = self.shape_of(image, s)
        width, height = int(width or s.get("width") or 512), int(height or s.get("height") or 512)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        status = on_status or (lambda text: None)
        started = time.monotonic()
        if row["kind"] == "online":
            paths, params = await self._online(row, prompt, out_dir, width, height, count, image, mask, status, chat_id, job_id)
        else:
            async with self.lock:
                paths, params = await self._local(row, prompt, out_dir, negative, width, height, steps, cfg, seed, count,
                                                  image, mask, strength, status)
        params["seconds"] = round(time.monotonic() - started, 1)
        self.db.update("image_models", row["id"], {"last_used": now()})
        records = [self._record(p, row, prompt, {**params, "index": i}, chat_id, row["kind"]) for i, p in enumerate(paths)]
        self.app.bus.publish("images_changed")
        return records

    async def _free_memory(self, need):
        """One memory budget with the text models: idle ones are unloaded (least recently used first) until the
        image model fits. Models in use stay. True when it fits now."""
        runtime = self.app.runtime
        idle = sorted([s for s in runtime.servers.values() if s.users == 0], key=lambda s: s.last_used)
        while need > runtime._free_bytes() * 0.9 and idle:
            server = idle.pop(0)
            self.app.log(f"images: unloading {server.model_id} to make room ({need / 2**30:.1f} GB needed)")
            await runtime._stop_server(server)
        return need <= runtime._free_bytes() * 0.9

    def local_default(self):
        """True when a request without a model choice is made on this computer (the chat's own text model is
        then lent back for the time the picture takes, so its memory can be used)."""
        row = self.default_model()
        return bool(row and row["kind"] == "local")

    def start_job(self, data):
        """A request from the Images window: runs in the background, reports through event `image_job`."""
        job = {"id": new_id("img_"), "state": "running", "text": "Starting…", "prompt": str(data.get("prompt") or ""),
               "model": data.get("model"), "images": [], "error": None, "started": now()}
        self.jobs[job["id"]] = job
        for old in list(self.jobs)[:-12]:
            if self.jobs[old]["state"] != "running":
                self.jobs.pop(old)
        publish = lambda: self.app.bus.publish("image_job", **job)        # noqa: E731

        def status(text):
            job["text"] = text
            publish()

        async def run():
            try:
                image = data.get("image") or None
                if image and not Path(image).is_file():
                    raise ImageError(f"There is no picture at {image}")
                job["images"] = await self.generate(
                    job["prompt"], model=data.get("model") or None, out_dir=self.app.paths.data / "images",
                    negative=str(data.get("negative") or ""), width=data.get("width"), height=data.get("height"),
                    steps=data.get("steps"), cfg=data.get("cfg"), seed=data.get("seed"), count=data.get("count") or 1,
                    image=image, mask=data.get("mask") or None, strength=data.get("strength"), on_status=status)
                job.update(state="done", text="Done")
            except asyncio.CancelledError:
                job.update(state="stopped", text="Stopped")
            except Exception as exc:
                job.update(state="failed", text="", error=str(exc)[:500])
                if not isinstance(exc, ImageError):
                    self.app.log(f"image job: {type(exc).__name__}: {exc}")
            finally:
                self.tasks.pop(job["id"], None)
                publish()
        self.tasks[job["id"]] = self.app.spawn(run())
        return job

    def stop_job(self, job_id):
        task = self.tasks.get(job_id)
        if task:
            task.cancel()

    def forget(self, media_id, delete_file=False):
        row = self.db.get("media", media_id)
        if not row:
            raise KeyError(media_id)
        if delete_file:
            Path(row["path"]).unlink(missing_ok=True)
        self.db.delete("media", media_id)
        self.app.bus.publish("images_changed")

    async def _local(self, row, prompt, out_dir, negative, width, height, steps, cfg, seed, count, image, mask,
                     strength, status):
        backend, exe = self.choose_engine()
        if not exe:
            raise ImageError("The image engine is not installed. Open Images → Engine and install it.")
        s = row.get("settings") or {}
        steps = int(steps or s.get("steps") or 20)
        cfg = float(cfg if cfg not in (None, "") else s.get("cfg", 7.0))
        seed = int(seed) if seed not in (None, "", -1) else random.randint(1, 2**31 - 1)
        width, height = max(64, width // 16 * 16), max(64, height // 16 * 16)      # the models work in blocks of 16
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = f"{slug(prompt)[:32]}-{stamp}"
        out = out_dir / (f"{name}-%02d.png" if count > 1 else f"{name}.png")
        cmd = [str(exe)]
        for slot, path in (row.get("files") or {}).items():
            cmd += [SLOT_FLAGS[slot], path]
        cmd += ["-p", prompt, "-W", str(width), "-H", str(height), "--steps", str(steps), "--cfg-scale", f"{cfg:g}",
                "-s", str(seed), "-b", str(count), "-o", str(out)]
        if negative:
            cmd += ["-n", negative]
        mode = "new"
        if image:
            if s.get("edit") == "reference" and not mask:      # a model that follows an instruction about a picture
                cmd += ["-r", str(image)]
                mode = "edit"
            else:                                              # redraw the picture (or only its masked part)
                cmd += ["-i", str(image), "--strength", f"{float(strength if strength not in (None, '') else 0.6):g}"]
                mode = "redraw"
                if mask:
                    cmd += ["--mask", str(mask)]
                    mode = "inpaint"
        if width * height > 1024 * 1024:
            cmd.append("--vae-tiling")                         # decode large pictures in tiles: far less memory
        if self.db.setting("image_compute", "auto") == "cpu" and backend not in ("cpu", "custom"):
            cmd += ["--backend", "cpu"]                        # a graphics-card build told to use the processor
        cmd += shlex.split(str(s.get("args") or ""), posix=os.name != "nt")
        need = int(sum(Path(p).stat().st_size for p in (row.get("files") or {}).values()) * 1.1) + int(1.5 * 2**30)
        status("Making room in memory for the image model…")
        try:
            fits = await self._free_memory(need)
        except Exception:
            fits = True
        if not fits:
            status(f"Memory is short for {row['name']} (it needs about {need / 2**30:.1f} GB): trying anyway.")
        env = {**os.environ}
        env.pop("DVA_TOKEN", None)
        if os.name != "nt":
            env["LD_LIBRARY_PATH"] = f"{exe.parent}{os.pathsep}{env.get('LD_LIBRARY_PATH', '')}"
        status(f"Loading {row['name']}…")
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(exe.parent), env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT, creationflags=NO_WINDOW)
        tail = []

        async def pump():
            buffer = b""
            while True:
                chunk = await proc.stdout.read(512)
                if not chunk:
                    break
                buffer += chunk
                *lines, buffer = re.split(rb"[\r\n]", buffer)          # progress bars rewrite one line
                for raw in lines:
                    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw.decode("utf-8", "replace")).strip()
                    if not line:
                        continue
                    tail.append(line)
                    del tail[:-60]
                    m = re.search(r"(\d+)\s*/\s*(\d+)", line)
                    if m and int(m.group(2)) == steps and int(m.group(1)) <= steps:
                        status(f"{row['name']}: step {m.group(1)} of {steps}")
        try:
            await asyncio.wait_for(pump(), float(self.db.setting("image_timeout_minutes", 60)) * 60)
            code = await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            raise ImageError(f"{row['name']} worked for longer than the limit of "
                             f"{self.db.setting('image_timeout_minutes', 60)} minutes and was stopped. Try a smaller "
                             "picture or fewer steps, or raise the limit in Images.")
        except asyncio.CancelledError:
            proc.kill()
            raise
        paths = sorted(p for p in out_dir.glob(f"{name}*") if p.suffix.lower() in IMAGE_EXT)
        if code != 0 or not paths:
            self.app.log(f"image engine ({backend}) exit {code}: " + " | ".join(tail[-12:]))
            raise ImageError(self._explain(tail, code, row))
        return paths, {"mode": mode, "engine": backend, "width": width, "height": height, "steps": steps, "cfg": cfg,
                       "seed": seed, "negative": negative, "source_image": str(image) if image else None}

    @staticmethod
    def _explain(tail, code, row):
        text = " ".join(tail[-20:]).lower()
        if "alloc" in text or "out of memory" in text or "bad_alloc" in text or code in (-9, 137):
            return (f"{row['name']} ran out of memory. Close other apps, make a smaller picture, or use a smaller "
                    "model (Images → Models).")
        if "unknown argument" in text or "invalid argument" in text or "usage:" in text:
            return ("The image engine did not accept its options: " + " ".join(tail[-3:])[:300]
                    + " Check the model's extra options in Images, or update the engine.")
        last = next((line for line in reversed(tail) if "error" in line.lower() or "fail" in line.lower()), "")
        return (f"The image engine stopped (code {code})" + (f": {last[:300]}" if last else ".")
                + " The engine's last lines are in the app's log.")

    async def _online(self, row, prompt, out_dir, width, height, count, image, mask, status, chat_id, job_id):
        providers = self.app.providers
        pid = row["provider"]
        if providers.over_budget():
            raise ImageError("This month's online budget is used up. Use a model on this computer, or raise the "
                             "budget in Models → Online.")
        conf = providers.conf(pid)
        status(f"Asking {row['name']}…")
        stamp, blobs, usage = time.strftime("%Y%m%d-%H%M%S"), [], {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=20)) as http:
            if pid == "openai":
                base = conf["base"].rstrip("/")
                headers = providers.headers(pid)
                if image:
                    files = [("image[]", (Path(image).name, Path(image).read_bytes(), "image/png"))]
                    if mask:
                        files.append(("mask", (Path(mask).name, Path(mask).read_bytes(), "image/png")))
                    r = await http.post(f"{base}/images/edits", headers=headers, files=files,
                                        data={"model": row["remote_id"], "prompt": prompt, "n": str(count),
                                              "size": openai_size(width, height)})
                else:
                    r = await http.post(f"{base}/images/generations", headers=headers,
                                        json={"model": row["remote_id"], "prompt": prompt, "n": count,
                                              "size": openai_size(width, height)})
                data = self._json(r, row)
                usage = data.get("usage") or {}
                for item in data.get("data") or []:
                    if item.get("b64_json"):
                        blobs.append(("png", base64.b64decode(item["b64_json"])))
                    elif item.get("url"):
                        got = await http.get(item["url"])
                        blobs.append(("png", got.content))
            else:                                # Google: Gemini's own endpoint (the OpenAI-style one is for chat)
                root = re.sub(r"/openai/?$", "", conf["base"].rstrip("/"))
                parts = [{"text": prompt}]
                if image:
                    kind = "image/jpeg" if Path(image).suffix.lower() in (".jpg", ".jpeg") else "image/png"
                    parts.append({"inlineData": {"mimeType": kind, "data": base64.b64encode(Path(image).read_bytes()).decode()}})
                body = {"contents": [{"role": "user", "parts": parts}],
                        "generationConfig": {"responseModalities": ["IMAGE"],
                                             "imageConfig": {"aspectRatio": nearest_ratio(width, height)}}}
                for _ in range(count):
                    r = await http.post(f"{root}/models/{row['remote_id']}:generateContent", json=body,
                                        headers={"x-goog-api-key": providers.key(pid) or ""})
                    data = self._json(r, row)
                    for cand in data.get("candidates") or []:
                        for part in (cand.get("content") or {}).get("parts") or []:
                            blob = part.get("inlineData") or part.get("inline_data")
                            if blob and blob.get("data"):
                                ext = "jpg" if "jpeg" in (blob.get("mimeType") or blob.get("mime_type") or "") else "png"
                                blobs.append((ext, base64.b64decode(blob["data"])))
        if not blobs:
            raise ImageError(f"{row['name']} answered without a picture (it may have declined the request).")
        paths = []
        for i, (ext, blob) in enumerate(blobs):
            path = out_dir / f"{slug(prompt)[:32]}-{stamp}{f'-{i + 1:02d}' if len(blobs) > 1 else ''}.{ext}"
            path.write_bytes(blob)
            paths.append(path)
        # what it cost: the provider's token counts are not priced per image model here, so this is an estimate
        cost = round(float((row.get("settings") or {}).get("per_image") or ONLINE[pid]["per_image"]) * len(paths), 4)
        self.db.insert("usage", {"id": new_id("use_"), "created": now(), "month": month_key(),
                                 "model_id": cloud_id(pid, row["remote_id"]),
                                 "provider": pid, "chat_id": chat_id, "job_id": job_id, "purpose": "image",
                                 "input_tokens": int(usage.get("input_tokens") or 0),
                                 "output_tokens": int(usage.get("output_tokens") or 0), "cost": cost, "estimated": 1})
        return paths, {"mode": "edit" if image else "new", "engine": pid, "width": width, "height": height,
                       "cost": cost, "source_image": str(image) if image else None}

    @staticmethod
    def _json(response, row):
        if response.status_code >= 400:
            try:
                err = response.json().get("error")
                message = err.get("message") if isinstance(err, dict) else str(err)
            except Exception:
                message = response.text[:300]
            raise ImageError(f"{row['name']} refused the request ({response.status_code}): {str(message)[:300]}")
        return response.json()
