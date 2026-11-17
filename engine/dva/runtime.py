"""llama.cpp runtime: which builds are installed (CPU / Vulkan / CUDA / Metal), installing them from the pinned
llama.cpp release, and running `llama-server` processes for the models in use.

Several models can be loaded at once (one llama-server each) when memory allows, so agents in a team can use
different models in parallel. A chat or task "leases" a model for as long as it needs it; when memory is short,
idle models are unloaded first, and if every loaded model is busy the new request waits. Requests to the same model
share its server (its "simultaneous replies" setting decides how many run at the same time; the rest queue).
Models never leave this computer: servers listen on 127.0.0.1 only and need a random API key.

Memory comes first (2.5.0). A run of agents sizes the context it loads a model with to what its steps need
(Runtime.fit) instead of the largest context for every reply slot, releases a model the moment no remaining step
uses it (Runtime.release), and can stop a model server that no longer answers so the next call loads a fresh
one (Runtime.restart)."""
import asyncio
import contextvars
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from .download import download, fetch_json

VERSIONS = json.loads((Path(__file__).with_name("versions.json")).read_text(encoding="utf-8"))
EXE = "llama-server.exe" if os.name == "nt" else "llama-server"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
DEVICE_LINE = re.compile(r"^\s*(\S+):\s+(.+?)\s+\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)")
BACKEND_LABELS = {"cpu": "CPU", "vulkan": "GPU (Vulkan: AMD, Intel, NVIDIA)", "cuda": "GPU (NVIDIA CUDA)",
                  "metal": "GPU (Apple Metal)", "custom": "Custom llama-server"}


def os_key():
    return {"Windows": "windows", "Darwin": "darwin"}.get(platform.system(), "linux")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RuntimeError_(RuntimeError):
    pass


ACTIVE = contextvars.ContextVar("dva_active_server", default=None)


class Server:
    """One running llama-server."""
    def __init__(self, model, plan, api_key):
        self.model_id = model["id"]
        self.model = model
        self.plan = plan
        self.api_key = api_key
        self.port = None
        self.proc = None
        self.users = 0
        self.last_used = time.monotonic()
        self.loaded_at = None
        self.need_bytes = 0
        self.read_tps = 0.0         # tokens per second measured on this server (reading the prompt / writing)
        self.write_tps = 0.0

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    @property
    def alive(self):
        return bool(self.proc and self.proc.poll() is None)

    @property
    def current(self):
        return {"model_id": self.model_id, **{k: v for k, v in self.plan.items() if k != "exe"}, "port": self.port,
                "loaded_at": self.loaded_at, "users": self.users, "vision": bool(self.plan.get("mmproj"))}

    def matches(self, plan):
        return all(self.plan.get(k) == plan.get(k) for k in PLAN_KEYS)


# a loaded model restarts when any of these change
PLAN_KEYS = ("compute", "backend", "ctx", "parallel", "mmproj", "gpu_layers", "kv_on", "moe_cpu", "mmproj_on",
             "device", "threads", "threads_batch", "kv_type", "flash_attn", "priority", "ubatch", "spec", "draft",
             "hybrid")
KV_TYPES = {"f16": 2.0, "q8_0": 1.0625, "q4_0": 0.5625}          # bytes per value in the context cache
PRIORITY = {"normal": 0, "medium": 1, "high": 2}
# context sizes a run may ask for: few steps, so a model is not reloaded for every small change in need
CTX_STEPS = (4096, 8192, 12288, 16384, 24576, 32768, 49152, 65536, 98304, 131072, 196608, 262144)
SPEC_TYPES = ("none", "ngram", "mtp", "draft")
HYBRID_CHECKPOINTS = 8          # saved states per reply slot for hybrid models …
HYBRID_CHECKPOINT_STEP = 256    # … at most this many tokens apart, so a follow-up re-reads little
HYBRID_STATE_MB = 64            # rough size of one saved state
# what llama-server reports about where it put the model (read from its log after loading)
OFFLOAD_LINE = re.compile(r"offloaded (\d+)/(\d+) layers to GPU")
BUFFER_LINE = re.compile(r"(\S+)\s+(model|KV|compute) buffer size\s*=\s*([\d.]+) MiB")


def is_integrated(name):
    from .hardware import INTEGRATED
    return bool(INTEGRATED.search(name or ""))


class Runtime:
    def __init__(self, paths, db, bus):
        self.paths, self.db, self.bus = paths, db, bus
        self.api_key = secrets.token_urlsafe(24)
        self.servers = {}            # model_id -> Server
        self.cond = asyncio.Condition()
        self.last_used = time.monotonic()
        self.status = {"state": "idle"}
        self.last_server = None
        self.installing = {}
        self.log_path = paths.data / "logs" / "llama-server.log"
        self._devices_cache = {}
        self._recommended = None
        self.cloud = None            # online models (cloud.Cloud), wired by the app

    # ------------------------------------------------------------------ backends
    @property
    def tag(self):
        return VERSIONS["llama_cpp_tag"]

    def backend_dir(self, backend):
        return self.paths.data / "runtimes" / "llama" / self.tag / backend

    def backends(self):
        """Installed llama-server builds, best first."""
        found = {}
        custom = os.environ.get("DVA_LLAMA_SERVER") or self.db.setting("llama_server_path")
        if custom and Path(custom).exists():
            found["custom"] = Path(custom)
        for backend in ("cuda", "vulkan", "metal", "cpu"):
            for folder in (self.backend_dir(backend), self.paths.bundled_runtime(backend)):
                if folder and (folder / EXE).exists():
                    found.setdefault(backend, folder / EXE)
                elif folder and folder.exists():
                    hits = list(folder.rglob(EXE))
                    if hits:
                        found.setdefault(backend, hits[0])
        return found

    def available_backends(self):
        return list(VERSIONS["backend_assets"].get(os_key(), {}).keys() - {"cuda_runtime"})

    def list_devices(self, backend):
        exe = self.backends().get(backend)
        if not exe:
            return []
        if backend in self._devices_cache:
            return self._devices_cache[backend]
        try:
            out = subprocess.run([str(exe), "--list-devices"], capture_output=True, text=True, timeout=30,
                                 cwd=str(exe.parent), creationflags=NO_WINDOW)
            text = out.stdout + "\n" + out.stderr
        except Exception as exc:
            return [{"error": f"{type(exc).__name__}"}]
        devices = []
        for line in text.splitlines():
            m = DEVICE_LINE.match(line)
            if m:
                devices.append({"id": m.group(1), "name": m.group(2), "total_mb": int(m.group(3)),
                                "free_mb": int(m.group(4))})
        self._devices_cache[backend] = devices
        return devices

    async def install_backend(self, backend):
        """Download the matching llama.cpp release build (and the CUDA runtime DLLs for CUDA)."""
        if backend in self.installing:
            return
        patterns = VERSIONS["backend_assets"].get(os_key(), {})
        if backend not in patterns:
            raise RuntimeError_(f"No {backend} build is published for this operating system")
        self.installing[backend] = {"state": "starting"}
        try:
            api = f"https://api.github.com/repos/{VERSIONS['llama_cpp_repo']}/releases"

            def choose(assets):
                def pick(pats, exclude=None):
                    for a in assets:
                        name = a["name"]
                        if all(p in name for p in pats) and (name.endswith(".zip") or name.endswith(".tar.gz")) \
                                and not (exclude and name.startswith(exclude)):
                            return a
                    return None
                found = [pick(patterns[backend], exclude="cudart")]
                if backend == "cuda" and "cuda_runtime" in patterns:
                    found.append(pick(patterns["cuda_runtime"]))
                return found if all(found) else None
            try:
                wanted = choose((await fetch_json(f"{api}/tags/{self.tag}")).get("assets", []))
            except Exception:
                wanted = None
            if not wanted:
                # Some llama.cpp releases are published without binaries; use the newest one that has them.
                for rel in await fetch_json(f"{api}?per_page=30"):
                    wanted = choose(rel.get("assets", []))
                    if wanted:
                        self.bus.publish("runtime", action="install_note", backend=backend,
                                         note=f"llama.cpp {self.tag} has no {backend} build; using {rel.get('tag_name')}")
                        break
            if not wanted:
                raise RuntimeError_(f"No recent llama.cpp release has a {backend} download for this computer")
            target = self.backend_dir(backend)
            tmp = target.with_name(target.name + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            for asset in wanted:
                archive = self.paths.data / "runtimes" / asset["name"]

                def progress(done, total, rate, name=asset["name"]):
                    self.installing[backend] = {"state": "downloading", "file": name, "done": done, "total": total}
                    self.bus.publish("runtime", action="install_progress", backend=backend, file=name,
                                     done=done, total=total, rate=rate)
                await download(asset["browser_download_url"], archive, expected_size=asset.get("size"),
                               progress=progress)
                self.installing[backend] = {"state": "extracting"}
                await asyncio.to_thread(self._extract, archive, tmp)
                archive.unlink(missing_ok=True)
            exe = next(iter(tmp.rglob(EXE)), None)
            if not exe:
                raise RuntimeError_("The download did not contain llama-server")
            # place all files of the folder that holds llama-server directly in the backend folder
            shutil.rmtree(target, ignore_errors=True)
            if exe.parent != tmp:
                for item in list(tmp.rglob("*")):  # e.g. CUDA runtime DLLs extracted beside the build folder
                    if item.is_file() and item.parent != exe.parent and not (exe.parent / item.name).exists():
                        shutil.move(str(item), str(exe.parent / item.name))
                shutil.move(str(exe.parent), str(target))
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                shutil.move(str(tmp), str(target))
            if os.name != "nt":
                (target / EXE).chmod(0o755)
            self._devices_cache.pop(backend, None)
            self.bus.publish("runtime", action="installed", backend=backend)
        except Exception as exc:
            self.bus.publish("runtime", action="install_failed", backend=backend, error=str(exc)[:300])
            raise
        finally:
            self.installing.pop(backend, None)

    @staticmethod
    def _extract(archive, dest):
        if str(archive).endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(dest)
        else:
            with tarfile.open(archive) as t:
                t.extractall(dest, filter="data")

    def remove_backend(self, backend):
        if any(s.plan["backend"] == backend for s in self.servers.values()):
            raise RuntimeError_("That build is running the current model; unload the model first")
        shutil.rmtree(self.backend_dir(backend), ignore_errors=True)
        self._devices_cache.pop(backend, None)

    def info(self):
        installed = self.backends()
        return {
            "tag": self.tag, "installed": {k: str(v) for k, v in installed.items()},
            "available": self.available_backends(), "labels": BACKEND_LABELS,
            "devices": {b: [{**d, "integrated": is_integrated(d.get("name"))} for d in self.list_devices(b)]
                        for b in installed if b not in ("cpu",)},
            "usable": self.gpu_backends(),
            "installing": self.installing, "status": self.status, "current": self.current, "loaded": self.loaded(),
        }

    # ------------------------------------------------------------------ choose how to run a model
    def gpu_backends(self):
        """Installed GPU runtimes that can actually see a graphics card, best first. A CUDA build on a computer
        without an NVIDIA card finds no device (and would quietly run everything on the CPU), so it is skipped;
        Intel and AMD graphics are reached through Vulkan."""
        installed = self.backends()
        out = []
        for b in ("custom", "cuda", "metal", "vulkan"):
            if b not in installed:
                continue
            if b in ("custom", "metal"):
                out.append(b)
                continue
            devices = [d for d in self.list_devices(b) if not d.get("error")]
            if devices:
                out.append(b)
        dedicated = [b for b in out if any(not is_integrated(d["name"]) for d in self.list_devices(b))]
        return dedicated + [b for b in out if b not in dedicated]     # a real graphics card before integrated

    def plan(self, model, compute=None, overrides=None):
        """Decide backend and flags for a model. compute: auto | cpu | gpu. overrides: settings for this load only
        (the speed tuner tries placements without saving them)."""
        settings = {**(model.get("settings") or {}), **(overrides or {})}
        compute = compute or settings.get("compute") or self.db.setting("default_compute", "auto")
        installed = self.backends()
        usable = self.gpu_backends()
        note = None
        if compute == "gpu" and not usable:
            if not any(b in installed for b in ("custom", "cuda", "metal", "vulkan")):
                raise RuntimeError_("GPU was chosen but no GPU runtime is installed. Install one in Models → "
                                    "Hardware & GPU, or switch this model to CPU.")
            note = ("GPU was chosen, but the installed GPU runtime found no graphics card it can use (CUDA needs "
                    "an NVIDIA card; Intel and AMD graphics need the Vulkan runtime), so it runs on the CPU.")
            compute = "cpu"
        if compute == "auto":
            if self._recommended is None:
                from .hardware import gpus, recommend_backend
                self._recommended = recommend_backend(gpus())
            compute = "gpu" if usable and self._recommended in usable else "cpu"
        if compute == "gpu":
            wanted = settings.get("gpu_backend")
            backend = wanted if wanted in usable else usable[0]
        else:
            backend = "cpu" if "cpu" in installed else (next(iter(installed), None))
        if not backend:
            raise RuntimeError_("No llama.cpp runtime is installed. Reinstall the app, or install one in Models → Hardware.")
        threads = settings.get("threads") or self.db.setting("threads") or None
        if not threads:
            import psutil
            threads = psutil.cpu_count(logical=False) or 4
        ctx = int(settings.get("ctx") or self.db.setting("default_ctx", 8192))
        mmproj = settings.get("mmproj") if settings.get("vision", True) else None
        if mmproj and not Path(mmproj).exists():
            mmproj = None
        from . import gguf
        meta = self._meta(model)
        kv_type = settings.get("kv_type") if settings.get("kv_type") in KV_TYPES else "f16"
        spec = settings.get("spec") if settings.get("spec") in SPEC_TYPES else "none"
        draft = None
        if spec == "draft":
            row = self.db.get("models", settings.get("draft_model") or "") if settings.get("draft_model") else None
            draft = row["path"] if row and row.get("path") and Path(row["path"]).exists() else None
            spec = "draft" if draft else "none"
        if spec == "mtp" and not gguf.has_mtp(meta):
            spec = "none"
        plan = {"backend": backend, "exe": str(installed[backend]), "compute": compute, "ctx": ctx,
                "threads": int(threads), "parallel": max(1, min(8, int(settings.get("parallel") or 1))),
                "mmproj": mmproj, "note": note, "gpu_layers": None, "kv_on": None, "moe_cpu": None,
                "mmproj_on": None, "device": None,
                # speed: reading threads, context-cache type, flash attention, priority, batch, speculative decoding
                "threads_batch": int(settings.get("threads_batch") or 0) or None, "kv_type": kv_type,
                "flash_attn": settings.get("flash_attn") if settings.get("flash_attn") in ("on", "off") else "auto",
                "priority": settings.get("priority") if settings.get("priority") in PRIORITY else "normal",
                "ubatch": int(settings.get("ubatch") or 0) or None, "spec": spec, "draft": draft,
                "hybrid": gguf.is_hybrid(meta)}
        if compute == "gpu":
            # where each part runs: layers on the GPU (the rest on the CPU), the context cache, MoE experts,
            # the vision part, and which card
            layers = settings.get("gpu_layers", "auto")
            plan["gpu_layers"] = layers if layers in ("auto", "all") else max(0, int(layers or 0))
            plan["kv_on"] = "cpu" if settings.get("kv_on") == "cpu" else "gpu"
            moe = settings.get("moe_cpu") or "none"
            plan["moe_cpu"] = moe if moe in ("none", "all") else max(0, int(moe))
            plan["mmproj_on"] = "cpu" if settings.get("mmproj_on") == "cpu" else "gpu"
            ids = [d["id"] for d in self.list_devices(backend) if d.get("id")]
            plan["device"] = settings.get("gpu_device") if settings.get("gpu_device") in ids else None
        return plan

    # ------------------------------------------------------------------ process
    def _command(self, model, plan, port):
        # -c is shared by all parallel slots, so each simultaneous reply still gets the full context
        cmd = [plan["exe"], "-m", model["path"], "--host", "127.0.0.1", "--port", str(port),
               "--api-key", self.api_key, "-c", str(plan["ctx"] * plan["parallel"]), "-t", str(plan["threads"]),
               "-np", str(plan["parallel"]), "--jinja", "--reasoning-format", "deepseek", "--no-webui",
               "--alias", model["id"], "--cache-reuse", "256"]
        if plan["compute"] == "cpu":
            cmd += ["--device", "none", "-ngl", "0"]
        else:
            # -ngl: layers on the GPU, the rest on the CPU ("auto" fits as many as the card's memory allows)
            cmd += ["-ngl", str(plan["gpu_layers"] if plan["gpu_layers"] is not None else "auto")]
            if plan.get("device"):
                cmd += ["-dev", plan["device"]]
            if plan.get("kv_on") == "cpu":
                cmd += ["--no-kv-offload"]              # the context cache stays in the computer's memory
            if plan.get("moe_cpu") == "all":
                cmd += ["--cpu-moe"]                    # MoE experts on the CPU, the rest on the GPU
            elif isinstance(plan.get("moe_cpu"), int) and plan["moe_cpu"] > 0:
                cmd += ["--n-cpu-moe", str(plan["moe_cpu"])]
        if plan.get("mmproj"):
            cmd += ["--mmproj", plan["mmproj"]]
            if plan["compute"] == "cpu" or plan.get("mmproj_on") == "cpu":
                cmd += ["--no-mmproj-offload"]
        cmd += self._speed_args(plan)
        extra = (model.get("settings") or {}).get("extra_args")
        if extra:
            cmd += [a for a in extra.split() if a]
        return cmd

    def _meta(self, model):
        """The model's header facts; headers read before 2.4.1 are read again (hybrid models were overcounted)."""
        from . import gguf
        meta = model.get("meta") or {}
        if (meta.get("meta_v") or 1) < gguf.META_VERSION and model.get("path") and Path(model["path"]).exists():
            try:
                meta = gguf.read_metadata(model["path"])
                self.db.update("models", model["id"], {"meta": meta})
                model["meta"] = meta
            except Exception:
                pass
        return meta

    @staticmethod
    def _speed_args(plan):
        """Flags that make a model faster on this computer (see Models.suggest and the speed tuner)."""
        args = []
        if plan.get("threads_batch"):
            args += ["-tb", str(plan["threads_batch"])]       # reading the prompt is compute-bound: more threads help
        fa = plan.get("flash_attn") or "auto"
        kv = plan.get("kv_type") or "f16"
        if kv != "f16":
            args += ["-ctk", kv, "-ctv", kv]                  # a compact context cache: less memory to stream
            fa = "on"                                         # a quantised V cache needs flash attention
        if fa != "auto":
            args += ["-fa", fa]
        if PRIORITY.get(plan.get("priority") or "normal"):
            args += ["--prio", str(PRIORITY[plan["priority"]])]
        if plan.get("ubatch"):
            args += ["-ub", str(plan["ubatch"]), "-b", str(max(2048, plan["ubatch"]))]
        spec = plan.get("spec") or "none"
        if spec == "ngram":
            args += ["--spec-type", "ngram-mod"]              # drafts from text already in the conversation
        elif spec == "mtp":
            args += ["--spec-type", "draft-mtp"]              # the model's own multi-token-prediction layers
        elif spec == "draft" and plan.get("draft"):
            args += ["--spec-type", "draft-simple", "-md", plan["draft"]]
            if plan["compute"] == "cpu":
                args += ["-devd", "none", "-ngld", "0"]
        if plan.get("hybrid"):
            # recurrent layers cannot rewind to any point: keep a few close checkpoints so the next turn
            # re-reads only what changed instead of most of the prompt
            args += ["--ctx-checkpoints", str(HYBRID_CHECKPOINTS), "--checkpoint-min-step", str(HYBRID_CHECKPOINT_STEP)]
        return args

    def _need_bytes(self, model, plan):
        from . import gguf
        self._meta(model)
        size = int(model.get("size") or 0)
        if plan.get("mmproj"):
            try:
                size += Path(plan["mmproj"]).stat().st_size
            except OSError:
                pass
        per_value = KV_TYPES.get(plan.get("kv_type") or "f16", 2.0)
        kv = gguf.kv_cache_bytes(model.get("meta"), plan["ctx"] * plan["parallel"], per_value) \
            if model.get("meta") else None
        if kv is None:
            kv = max(0.35, size / 2**30 * 0.12) * (plan["ctx"] * plan["parallel"] / 8192) * 2**30 * per_value / 2
        if plan.get("hybrid"):
            kv += HYBRID_CHECKPOINTS * HYBRID_STATE_MB * 2**20 * plan["parallel"]
        if plan.get("draft"):
            try:
                size += Path(plan["draft"]).stat().st_size
            except OSError:
                pass
        return int(size + kv + 0.4 * 2**30)

    def _free_bytes(self):
        import psutil
        return int(psutil.virtual_memory().available)

    async def _start(self, model, plan):
        if not Path(model["path"]).exists():
            raise RuntimeError_(f"The model file is missing: {model['path']}")
        server = Server(model, plan, self.api_key)
        server.port = free_port()
        server.need_bytes = self._need_bytes(model, plan)
        cmd = self._command(model, plan, server.port)
        self.status = {"state": "loading", "model_id": model["id"], "backend": plan["backend"], "since": time.time()}
        self._publish()
        log = self.log_path.open("ab")
        log.write(f"\n==== {time.ctime()} {' '.join(c if c != self.api_key else '***' for c in cmd)}\n".encode())
        log.flush()
        log_start = log.tell()
        try:
            server.proc = await asyncio.to_thread(
                subprocess.Popen, cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(Path(plan["exe"]).parent),
                creationflags=NO_WINDOW)
        except OSError as exc:
            self.status = {"state": "failed", "model_id": model["id"], "error": f"Could not start llama-server: {exc}"}
            self._publish()
            raise RuntimeError_(self.status["error"]) from exc
        finally:
            log.close()
        deadline = time.monotonic() + 600
        async with httpx.AsyncClient(timeout=5, trust_env=False) as http:
            while time.monotonic() < deadline:
                if server.proc.poll() is not None:
                    tail = self.log_tail(20)
                    self.status = {"state": "failed", "model_id": model["id"], "error": self.friendly_error(tail)}
                    self._publish()
                    raise RuntimeError_(self.status["error"])
                try:
                    r = await http.get(f"{server.base_url}/health")
                    if r.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            else:
                await self._stop_server(server)
                raise RuntimeError_("The model took more than 10 minutes to load")
        server.loaded_at = time.time()
        bench = model.get("bench") or {}          # speeds from the model's benchmark until this server is measured
        server.read_tps = float(bench.get("prompt_per_second") or 0)
        server.write_tps = float(bench.get("predicted_per_second") or 0)
        server.placement = self._read_placement(log_start)
        self.servers[model["id"]] = server
        self.status = {"state": "ready", "model_id": model["id"], "backend": plan["backend"],
                       "compute": plan["compute"], "ctx": plan["ctx"], "vision": bool(plan.get("mmproj"))}
        self._publish()
        self.db.update("models", model["id"], {"last_used": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return server

    def _read_placement(self, start):
        """Where llama-server actually put the model: layers on the GPU, and memory per device (from its log)."""
        try:
            with self.log_path.open("rb") as f:
                f.seek(start)
                text = f.read(400_000).decode("utf-8", errors="replace")
        except OSError:
            return {}
        out = {"devices": {}}
        m = OFFLOAD_LINE.search(text)
        if m:
            out["gpu_layers"], out["layers"] = int(m.group(1)), int(m.group(2))
        for dev, kind, mib in BUFFER_LINE.findall(text):
            dev = "CPU" if dev.upper().startswith("CPU") or "HOST" in dev.upper() else dev
            d = out["devices"].setdefault(dev, {"model_mb": 0, "kv_mb": 0, "compute_mb": 0})
            d[{"model": "model_mb", "KV": "kv_mb", "compute": "compute_mb"}[kind]] += round(float(mib))
        return out

    def _publish(self):
        self.bus.publish("model_status", **self.status, loaded=self.loaded())

    def loaded(self):
        return [{"model_id": s.model_id, "backend": s.plan["backend"], "compute": s.plan["compute"],
                 "ctx": s.plan["ctx"], "parallel": s.plan["parallel"], "users": s.users,
                 "vision": bool(s.plan.get("mmproj")), "memory_mb": s.need_bytes // 2**20,
                 "placement": getattr(s, "placement", {}), "note": s.plan.get("note"),
                 "read_tps": round(s.read_tps, 1), "write_tps": round(s.write_tps, 2),
                 "kv_on": s.plan.get("kv_on"), "moe_cpu": s.plan.get("moe_cpu"),
                 "speed": {k: s.plan.get(k) for k in ("threads", "threads_batch", "kv_type", "spec", "priority")}}
                for s in self.servers.values()]

    def friendly_error(self, tail):
        low = tail.lower()
        if "out of memory" in low or "failed to allocate" in low or "unable to allocate" in low:
            return ("Not enough memory to load this model with these settings. Try CPU, a smaller context, "
                    "fewer simultaneous replies, or a smaller model.")
        if "unknown model architecture" in low or "unknown architecture" in low:
            return "This llama.cpp build does not support this model's architecture yet."
        if "clip_init" in low or ("mmproj" in low and "fail" in low):
            return ("The vision part (mmproj file) of this model could not be loaded. Turn vision off for this model "
                    "in its settings, or download the matching mmproj file.")
        if "vulkan" in low and "error" in low:
            return "The Vulkan GPU build failed to start on this graphics card. Switch this model to CPU."
        return "The model server stopped while loading. Details are in logs/llama-server.log. " + tail[-300:]

    def log_tail(self, lines=40):
        try:
            data = self.log_path.read_bytes()[-20000:].decode("utf-8", errors="replace")
            return "\n".join(data.splitlines()[-lines:])
        except OSError:
            return ""

    async def _stop_server(self, server):
        proc, server.proc = server.proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.to_thread(proc.wait, 15)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self.servers.get(server.model_id) is server:
            del self.servers[server.model_id]
        if self.last_server is server:
            self.last_server = None
        self.status = {"state": "idle"} if not self.servers else {"state": "ready", **self._summary()}
        self.bus.publish("model_status", **self.status, unloaded=server.model_id, loaded=self.loaded())

    def _summary(self):
        s = self.last_server or next(iter(self.servers.values()))
        return {"model_id": s.model_id, "backend": s.plan["backend"], "compute": s.plan["compute"], "ctx": s.plan["ctx"]}

    async def _stop(self):
        for server in list(self.servers.values()):
            await self._stop_server(server)

    # ------------------------------------------------------------------ leasing
    def _max_loaded(self):
        return max(1, int(self.db.setting("max_loaded_models", 3) or 1))

    async def _make_room(self, need):
        """Unload idle models (least recently used first) until the new one fits. True when it fits now."""
        idle = sorted([s for s in self.servers.values() if s.users == 0], key=lambda s: s.last_used)
        while True:
            fits_count = len(self.servers) < self._max_loaded()
            fits_memory = not self.servers or need <= self._free_bytes() * 0.9
            if fits_count and fits_memory:
                return True
            if not idle:
                return False
            await self._stop_server(idle.pop(0))

    @asynccontextmanager
    async def lease(self, model_id, compute=None, on_wait=None, overrides=None, as_loaded=False):
        """Use a model for the duration of a block; yields its Server. Loads it if needed (alongside the models
        already loaded when memory allows), otherwise waits for a busy model to finish. Online models
        ("cloud:…") are leased from cloud.Cloud: nothing to load.
        as_loaded: small background work (learning from a chat) uses the model the way it is loaded right now
        instead of reloading it with its full settings; if it is not loaded, it gets a small context."""
        if self.cloud is not None and str(model_id or "").startswith("cloud:"):
            async with self.cloud.lease(model_id, on_wait=on_wait) as server:
                yield server
            return
        model = self.db.get("models", model_id)
        if not model:
            raise RuntimeError_(f"Model {model_id!r} is not installed. Download it in Models.")
        async with self.cond:
            current = self.servers.get(model_id)
            if as_loaded and overrides is None:
                if current and current.alive:
                    overrides = {"ctx": current.plan["ctx"], "parallel": current.plan["parallel"]}
                else:
                    overrides = self.fit(model_id, 8192, 1)
            plan = self.plan(model, compute, overrides)
            waited = False
            while True:
                server = self.servers.get(model_id)
                if server and not server.alive:
                    await self._stop_server(server)
                    server = None
                if server and server.matches(plan):
                    break
                if server and server.users == 0:
                    await self._stop_server(server)      # same model, different settings: restart it
                    server = None
                if not server and await self._make_room(self._need_bytes(model, plan)):
                    try:
                        server = await self._start(model, plan)
                        server.fitted = bool(overrides and "ctx" in overrides)   # sized for a run, not by its setting
                    except RuntimeError_:
                        if not plan.get("mmproj"):
                            raise
                        # the vision part does not load with this build or model: run without it, say so
                        settings = {**(model.get("settings") or {}), "vision": False,
                                    "vision_error": self.status.get("error") or "the vision part could not be loaded"}
                        self.db.update("models", model_id, {"settings": settings})
                        self.bus.publish("models_changed")
                        if on_wait:
                            on_wait("The model's vision part could not be loaded; continuing without vision")
                        plan = {**plan, "mmproj": None}
                        server = await self._start(model, plan)
                    break
                if on_wait and not waited:
                    busy = ", ".join(s.model_id for s in self.servers.values() if s.users) or "another task"
                    on_wait(f"Waiting for memory: {busy} is busy…")
                    waited = True
                await self.cond.wait()
            server.users += 1
            self.last_server = server
        token = ACTIVE.set(server)
        try:
            server.last_used = self.last_used = time.monotonic()
            yield server
        finally:
            server.users -= 1                 # first: nothing below may stop the model being released
            server.last_used = self.last_used = time.monotonic()
            try:
                ACTIVE.reset(token)
            except ValueError:                # released from another task's context
                ACTIVE.set(None)
            async with self.cond:
                self.cond.notify_all()

    def active(self):
        """The server leased by the current task, else the most recently used one."""
        server = ACTIVE.get()
        if server and server.alive:
            return server
        if self.last_server and self.last_server.alive:
            return self.last_server
        return next((s for s in self.servers.values() if s.alive), None)

    @property
    def users(self):
        return sum(s.users for s in self.servers.values())

    @property
    def current(self):
        s = self.active()
        return s.current if s else None

    @property
    def base_url(self):
        s = self.active()
        if not s:
            raise RuntimeError_("No model is loaded")
        return s.base_url

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def is_loaded(self, model_id):
        return model_id in self.servers

    # ------------------------------------------------------------------ memory-first loading (2.5.0)
    def ceiling(self, model_id):
        """The largest context a run may use for this model: the context the user set for it."""
        model = self.db.get("models", model_id) or {}
        return int((model.get("settings") or {}).get("ctx") or self.db.setting("default_ctx", 8192))

    def fit(self, model_id, need_tokens, parallel=1):
        """Settings for loading a model for a run that needs `need_tokens` of context per reply: the smallest
        step that holds it (never more than the user's own setting), and only as many reply slots as the run
        uses. A model that is already loaded a little larger is kept as it is, so it is not loaded twice."""
        top = self.ceiling(model_id)
        want = next((c for c in CTX_STEPS if c >= need_tokens), CTX_STEPS[-1])
        want = max(min(want, top), min(4096, top))
        parallel = max(1, int(parallel or 1))
        s = self.servers.get(model_id)
        if s and s.alive and s.plan.get("parallel") == parallel and want <= int(s.plan.get("ctx") or 0) <= want * 2:
            want = int(s.plan["ctx"])
        return {"ctx": want, "parallel": parallel}

    def keep(self, model_id, need_tokens):
        """A model that a run loaded with a smaller context stays as it is for the chats that follow, as long as
        it is large enough for them: no reload back to the full setting until something needs it."""
        s = self.servers.get(model_id)
        if s and s.alive and getattr(s, "fitted", False) and int(s.plan.get("ctx") or 0) >= need_tokens:
            return {"ctx": s.plan["ctx"], "parallel": s.plan["parallel"]}
        return None

    def need_for(self, model_id, overrides=None):
        """Bytes of memory this model needs when loaded with these settings (0 for online or unknown models)."""
        model = self.db.get("models", model_id) if model_id else None
        if not model:
            return 0
        try:
            return self._need_bytes(model, self.plan(model, None, overrides))
        except Exception:
            return 0

    def memory(self, model_id=None, overrides=None):
        """Free memory now, what idle models would give back, and what loading `model_id` would take."""
        idle = sum(s.need_bytes for s in self.servers.values() if s.users == 0 and s.model_id != model_id)
        loaded = self.servers.get(model_id) if model_id else None
        need = 0
        same = bool(loaded and loaded.alive and all(loaded.plan.get(k) == v for k, v in (overrides or {}).items()
                                                    if k in ("ctx", "parallel")))
        if model_id and not same:
            need = self.need_for(model_id, overrides)
            if loaded and loaded.users == 0:
                idle += loaded.need_bytes            # it restarts with the new settings: its memory comes back
        return {"free": self._free_bytes(), "reclaimable": idle, "need": need}

    async def release(self, only=None, keep=()):
        """Unload idle models now (those in `only`, or all), except the ones in `keep`. Returns what was unloaded."""
        gone = []
        async with self.cond:
            for s in list(self.servers.values()):
                if s.users == 0 and s.model_id not in keep and (only is None or s.model_id in only):
                    await self._stop_server(s)
                    gone.append(s.model_id)
            if gone:
                self.cond.notify_all()
        return gone

    async def restart(self, server):
        """Stop a model server that no longer answers; the next lease loads a fresh one. A server other replies
        still use is left alone unless its process has died."""
        async with self.cond:
            if self.servers.get(server.model_id) is server and (server.users <= 0 or not server.alive):
                await self._stop_server(server)
                self.cond.notify_all()
                return True
        return False

    async def unload(self, model_id=None):
        async with self.cond:
            targets = [self.servers[model_id]] if model_id and model_id in self.servers else \
                list(self.servers.values()) if not model_id else []
            if any(s.users for s in targets):
                raise RuntimeError_("The model is in use by a chat or task right now")
            for s in targets:
                await self._stop_server(s)
            self.cond.notify_all()

    async def idle_watch(self):
        """Unload models after the configured idle time to give memory back."""
        while True:
            await asyncio.sleep(30)
            minutes = float(self.db.setting("unload_after_minutes", 20) or 0)
            if not minutes:
                continue
            async with self.cond:
                for s in list(self.servers.values()):
                    if s.users == 0 and time.monotonic() - s.last_used > minutes * 60:
                        await self._stop_server(s)
                self.cond.notify_all()

    async def shutdown(self):
        await self._stop()
