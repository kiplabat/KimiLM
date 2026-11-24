"""How the computer is being used, second by second (2.27.0): processor, graphics, memory, network, disks,
temperatures and power, what KimiLM and its models take of each, and what the models are doing (reading or
writing, and how many tokens a second). Shown live in Performance.

A thread samples once a second (psutil, sysfs, a long-running nvidia-smi or, on Windows without NVIDIA, a
PowerShell loop reading the GPU counters Task Manager shows). It keeps the last hour second by second and the last
day minute by minute, in memory only. Listeners (the window's stream) get each sample as it is taken.

On KimiOS, the system service kimi-telemetry adds what only the system can read and writes it to
/run/kimios/telemetry.json every second: how busy an Intel GPU really is (its performance counters, not its
clock), processor and graphics power from RAPL, fans, throttling and KimiLM's memory slice. When that file is
fresh its values are merged in and the samples say `kimios: true`."""
import asyncio
import collections
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil

from .hardware import NO_WINDOW, cpu_name, gpus as list_gpus

FINE_SECONDS = 3600          # one sample a second for an hour
COARSE_MINUTES = 1440        # one a minute for a day
KIMIOS_FILE = os.environ.get("KIMIOS_TELEMETRY_JSON", "/run/kimios/telemetry.json")
KIMIOS_FRESH = 5.0           # seconds: an older file means the service is not running

# scalar fields kept in the history (everything else is only in the live sample)
SCALARS = ("cpu", "cpu_mhz", "load1", "mem_used_mb", "mem_avail_mb", "swap_used_mb", "kimilm_cpu", "kimilm_mem_mb",
           "models_cpu", "models_mem_mb", "gpu", "gpu_mem_mb", "gpu_temp", "gpu_power_w", "kimilm_gpu", "net_rx",
           "net_tx", "disk_read", "disk_write", "temp_cpu", "battery", "power_w", "power_cpu_w", "power_gpu_w",
           "fan_rpm", "tok_read", "tok_write", "reading", "writing", "psi_cpu", "psi_mem", "psi_io", "slice_mem_mb")


def _num(v, digits=1):
    if v is None:
        return None
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return None


def _read(path, default=None):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


class _NvidiaReader:
    """One `nvidia-smi -lms` process for as long as the engine runs: a line per card per interval, no process
    start per sample."""
    QUERY = "utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,name"

    def __init__(self, interval_ms):
        self.latest = {}
        self.proc = subprocess.Popen(["nvidia-smi", f"--query-gpu={self.QUERY}", "--format=csv,noheader,nounits",
                                      f"-lms={int(interval_ms)}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, creationflags=NO_WINDOW)
        threading.Thread(target=self._loop, daemon=True, name="telemetry-nvidia").start()

    def _loop(self):
        index = 0
        for line in self.proc.stdout:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            name = ",".join(parts[5:])
            val = lambda i: None if parts[i] in ("", "[N/A]", "N/A", "[Not Supported]") else _num(parts[i])
            self.latest[index] = {"name": name, "util": val(0), "mem_used_mb": val(1), "mem_total_mb": val(2),
                                  "temp": val(3), "power_w": val(4), "source": "nvidia-smi", "t": time.time()}
            index = (index + 1) % max(1, self.cards)

    cards = 1

    def read(self):
        now = time.time()
        return [g for _, g in sorted(self.latest.items()) if now - g["t"] < 5]

    def stop(self):
        try:
            self.proc.kill()
        except Exception:
            pass


WIN_GPU_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
while ($true) {
  $all = (Get-Counter -Counter @('\GPU Engine(*engtype_3D)\Utilization Percentage', '\GPU Adapter Memory(*)\Shared Usage', '\GPU Adapter Memory(*)\Dedicated Usage')).CounterSamples
  $util = 0; $sh = 0; $de = 0; $mine = @{}
  foreach ($s in $all) {
    if ($s.Path -like '*utilization percentage') {
      $util += $s.CookedValue
      if ($s.InstanceName -match 'pid_(\d+)_') { $p = [string]$Matches[1]; $mine[$p] = $mine[$p] + $s.CookedValue }
    } elseif ($s.Path -like '*shared usage') { $sh += $s.CookedValue } else { $de += $s.CookedValue }
  }
  $o = @{ util = $util; shared = $sh; dedicated = $de; pids = $mine } | ConvertTo-Json -Compress
  [Console]::Out.WriteLine($o); [Console]::Out.Flush()
  Start-Sleep -Milliseconds 2000
}
"""


class _WindowsGpuReader:
    """Windows' own GPU counters (what Task Manager shows as 3D), for graphics without nvidia-smi: Intel and AMD."""

    def __init__(self):
        self.latest = None
        exe = shutil.which("powershell") or shutil.which("pwsh")
        self.proc = subprocess.Popen([exe, "-NoProfile", "-NonInteractive", "-Command", WIN_GPU_SCRIPT],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     creationflags=NO_WINDOW)
        threading.Thread(target=self._loop, daemon=True, name="telemetry-wingpu").start()

    def _loop(self):
        for line in self.proc.stdout:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            d["t"] = time.time()
            self.latest = d

    def read(self):
        d = self.latest
        return d if d and time.time() - d["t"] < 6 else None

    def stop(self):
        try:
            self.proc.kill()
        except Exception:
            pass


class Telemetry:
    def __init__(self, runtime, db, log=None, interval=1.0):
        self.runtime = runtime
        self.db = db
        self.log = log or (lambda m: None)
        self.interval = interval
        self.fine = collections.deque(maxlen=FINE_SECONDS)
        self.coarse = collections.deque(maxlen=COARSE_MINUTES)
        self.marks = collections.deque(maxlen=400)
        self._mark_n = 0                # marks are numbered; each sample carries the ones made since the last
        self._mark_sent = 0
        self.latest = None
        self.lock = threading.Lock()
        self.listeners = set()          # (loop, asyncio.Queue)
        self.calls = {}                 # call id -> {"model", "phase", "tokens", "server"}
        self._call_ids = 0
        self._written = 0               # tokens written by local models since the engine started
        self._written_seen = 0
        self._stop = threading.Event()
        self._thread = None
        self._net = self._disk = None
        self._procs = {}                # pid -> psutil.Process (kept: cpu_percent needs the previous reading)
        self._drm = {}                  # (pid, fd) -> nanoseconds busy at the last sample (Linux fdinfo)
        self._minute = []
        self._nvidia = self._wingpu = None
        self._want_top = 0.0
        self.top = []
        self._last_model_state = {}
        self.info = {}

    # ------------------------------------------------------------------ model calls (from llm.py)
    def call_started(self, server):
        with self.lock:
            self._call_ids += 1
            cid = self._call_ids
            self.calls[cid] = {"model": getattr(server, "model_id", ""), "phase": "reading", "tokens": 0,
                               "read_tps": float(getattr(server, "read_tps", 0) or 0), "t": time.monotonic()}
        return cid

    def call_token(self, cid):
        c = self.calls.get(cid)
        if c is not None:
            c["phase"] = "writing"
            c["tokens"] += 1
            self._written += 1

    def call_ended(self, cid):
        with self.lock:
            self.calls.pop(cid, None)

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        if self._thread or not self.db.setting("telemetry_enabled", True):
            return
        cards = []
        try:
            cards = list_gpus()
        except Exception:
            pass
        vm = psutil.virtual_memory()
        self.info = {"cpu": cpu_name(), "cores": psutil.cpu_count(logical=False) or 1,
                     "threads": psutil.cpu_count() or 1, "ram_mb": int(vm.total / 2**20),
                     "swap_mb": int(psutil.swap_memory().total / 2**20),
                     "gpus": [{k: g.get(k) for k in ("name", "vram_mb", "integrated", "vendor")} for g in cards],
                     "os": f"{platform.system()} {platform.release()}", "interval": self.interval}
        try:
            if shutil.which("nvidia-smi") and any(g.get("vendor") == "nvidia" for g in cards):
                self._nvidia = _NvidiaReader(self.interval * 1000)
                self._nvidia.cards = max(1, sum(1 for g in cards if g.get("vendor") == "nvidia"))
            elif os.name == "nt" and cards and (shutil.which("powershell") or shutil.which("pwsh")):
                self._wingpu_possible = True          # started while Performance is open (see _windows_gpu)
        except Exception as exc:
            self.log(f"telemetry: no GPU reader ({type(exc).__name__}: {exc})")
        psutil.cpu_percent(percpu=True)
        self._thread = threading.Thread(target=self._loop, args=(self._stop,), daemon=True, name="telemetry")
        self._thread.start()

    def stop(self):
        self._stop.set()
        for r in (self._nvidia, self._wingpu):
            if r:
                r.stop()
        self._nvidia = self._wingpu = None
        self._thread = None

    def set_enabled(self, on):
        """Settings → Performance and usage: readings on or off, at once."""
        if on and not self._thread:
            self._stop = threading.Event()
            self.start()
        elif not on and self._thread:
            self.stop()

    def _loop(self, stop):
        next_at = time.monotonic()
        while not stop.is_set():
            try:
                sample = self.sample()
                self._keep(sample)
            except Exception as exc:                       # pragma: no cover - never let the thread die
                self.log(f"telemetry sample: {type(exc).__name__}: {exc}")
            next_at += self.interval
            delay = next_at - time.monotonic()
            if delay < 0:                                  # fell behind (a suspended laptop): start afresh
                next_at = time.monotonic()
                delay = self.interval
            stop.wait(delay)

    # ------------------------------------------------------------------ one sample
    def _ours(self):
        """The engine and every program it started (model servers, media workers, code an agent runs)."""
        me = psutil.Process()
        procs = [me]
        try:
            procs += me.children(recursive=True)
        except psutil.Error:
            pass
        models = set()
        for s in list(getattr(self.runtime, "servers", {}).values()):
            p = getattr(s, "proc", None)
            if p is not None and getattr(p, "pid", None):
                models.add(p.pid)
        return procs, models

    def _proc(self, p):
        """The kept psutil object for p (its cpu_percent remembers the last reading)."""
        kept = self._procs.get(p.pid)
        if kept is None:
            kept = self._procs[p.pid] = p
            try:
                kept.cpu_percent(None)
            except psutil.Error:
                pass
        return kept

    def _drm_busy(self, pids, elapsed):
        """Linux: GPU engine time used by these processes, from /proc/<pid>/fdinfo (drm-engine-*: ns busy).
        Returns percent of one GPU, or None where the kernel does not report it."""
        if platform.system() != "Linux":
            return None
        total, seen, now_keys = 0, False, {}
        for pid in pids:
            try:
                fds = os.listdir(f"/proc/{pid}/fd")
            except OSError:
                continue
            for fd in fds:
                try:                                   # only the graphics device's files, not every file and socket
                    if "/dev/dri/" not in os.readlink(f"/proc/{pid}/fd/{fd}"):
                        continue
                except OSError:
                    continue
                e = Path(f"/proc/{pid}/fdinfo/{fd}")
                text = _read(e, "")
                if not text or "drm-engine-" not in text:
                    continue
                seen = True
                ns = 0
                for line in text.splitlines():
                    if line.startswith("drm-engine-") and not line.startswith("drm-engine-capacity"):
                        try:
                            ns += int(line.split(":", 1)[1].split()[0])
                        except (ValueError, IndexError):
                            pass
                cid = next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("drm-client-id")),
                           e.name)
                key = (pid, cid)
                if key in now_keys:
                    continue
                now_keys[key] = ns
                before = self._drm.get(key)
                if before is not None and ns >= before:
                    total += ns - before
        self._drm = now_keys
        if not seen:
            return None
        return min(100.0, 100.0 * total / (elapsed * 1e9)) if elapsed > 0 else 0.0

    def _windows_gpu(self):
        """Windows without nvidia-smi: Task Manager's GPU counters, read by a PowerShell loop that runs only while
        the Performance window is open (and two minutes after), since reading them costs processor time."""
        if not getattr(self, "_wingpu_possible", False):
            return None
        now = time.monotonic()
        if self.listeners:
            self._watched = now
            if self._wingpu is None:
                try:
                    self._wingpu = _WindowsGpuReader()
                except Exception as exc:
                    self._wingpu_possible = False
                    self.log(f"telemetry: no Windows GPU counters ({type(exc).__name__}: {exc})")
                    return None
        elif self._wingpu is not None and now - getattr(self, "_watched", now) > 120:
            self._wingpu.stop()
            self._wingpu = None
        return self._wingpu

    @staticmethod
    def _linux_gpus():
        out = []
        for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
            dev = card / "device"
            driver = os.path.basename(os.path.realpath(dev / "driver")) if (dev / "driver").exists() else ""
            g = {"name": card.name, "driver": driver, "source": "sysfs"}
            busy = _read(dev / "gpu_busy_percent")
            if busy is not None:                                       # amdgpu
                g["util"] = _num(busy)
                used, tot = _read(dev / "mem_info_vram_used"), _read(dev / "mem_info_vram_total")
                if used and tot:
                    g["mem_used_mb"], g["mem_total_mb"] = int(used) // 2**20, int(tot) // 2**20
            else:                                                      # i915: the clock, as a share of its maximum
                act = _read(card / "gt_act_freq_mhz")
                top = _read(card / "gt_RP0_freq_mhz") or _read(card / "gt_max_freq_mhz")
                if act and top and float(top) > 0:
                    g["clock_pct"] = _num(100 * float(act) / float(top))
                    g["clock_mhz"] = _num(act, 0)
            for hw in (dev / "hwmon").glob("hwmon*"):
                t = _read(hw / "temp1_input")
                if t:
                    g["temp"] = _num(int(t) / 1000)
                p = _read(hw / "power1_average") or _read(hw / "power1_input")
                if p:
                    g["power_w"] = _num(int(p) / 1e6)
            if len(g) > 4:
                out.append(g)
        return out

    @staticmethod
    def _psi():
        out = {}
        for k in ("cpu", "memory", "io"):
            text = _read(f"/proc/pressure/{k}")
            if text:
                for part in text.splitlines()[0].split():
                    if part.startswith("avg10="):
                        out[k] = _num(part[6:])
        return out

    def _kimios(self):
        try:
            st = os.stat(KIMIOS_FILE)
            if time.time() - st.st_mtime > KIMIOS_FRESH:
                return None
            return json.loads(Path(KIMIOS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def sample(self):
        now = time.time()
        mono = time.monotonic()
        elapsed = mono - getattr(self, "_last_mono", mono - self.interval)
        self._last_mono = mono
        s = {"t": round(now, 3)}
        cores = psutil.cpu_percent(percpu=True)
        s["cpu"] = _num(sum(cores) / len(cores)) if cores else None
        s["cpu_cores"] = [_num(c, 0) for c in cores]
        try:
            f = psutil.cpu_freq()
            s["cpu_mhz"] = _num(f.current, 0) if f else None
        except Exception:
            s["cpu_mhz"] = None
        try:
            s["load1"] = _num(os.getloadavg()[0], 2)
        except (OSError, AttributeError):
            s["load1"] = None
        vm = psutil.virtual_memory()
        s["mem_used_mb"] = int((vm.total - vm.available) / 2**20)
        s["mem_avail_mb"] = int(vm.available / 2**20)
        s["mem_total_mb"] = int(vm.total / 2**20)
        try:
            s["swap_used_mb"] = int(psutil.swap_memory().used / 2**20)
        except Exception:
            s["swap_used_mb"] = None

        # KimiLM and its models
        procs, model_pids = self._ours()
        threads = psutil.cpu_count() or 1
        k_cpu = k_mem = m_cpu = m_mem = 0.0
        alive = set()
        per_model = {}
        pid_model = {getattr(sv.proc, "pid", None): mid for mid, sv in getattr(self.runtime, "servers", {}).items()
                     if getattr(sv, "proc", None)}
        for p in procs:
            alive.add(p.pid)
            kp = self._proc(p)
            try:
                c = kp.cpu_percent(None) / threads
                m = kp.memory_info().rss / 2**20
            except psutil.Error:
                continue
            k_cpu += c
            k_mem += m
            if p.pid in model_pids:
                m_cpu += c
                m_mem += m
                per_model[pid_model.get(p.pid) or str(p.pid)] = {"cpu": _num(c), "mem_mb": int(m)}
        for pid in [x for x in self._procs if x not in alive]:
            self._procs.pop(pid, None)
        s.update(kimilm_cpu=_num(k_cpu), kimilm_mem_mb=int(k_mem), models_cpu=_num(m_cpu), models_mem_mb=int(m_mem))
        s["models"] = per_model

        # graphics
        cards = []
        if self._nvidia:
            cards = self._nvidia.read()
        elif self._windows_gpu():
            w = self._wingpu.read()
            if w:
                gpu_mem = ((w.get("dedicated") or 0) + (w.get("shared") or 0)) / 2**20
                cards = [{"name": (self.info.get("gpus") or [{}])[0].get("name") or "GPU",
                          "util": _num(min(100.0, float(w.get("util") or 0))), "mem_used_mb": int(gpu_mem),
                          "source": "windows"}]
                mine = sum(float(v or 0) for k, v in (w.get("pids") or {}).items() if int(k) in alive)
                s["kimilm_gpu"] = _num(min(100.0, mine))
        elif platform.system() == "Linux":
            cards = self._linux_gpus()
        kos = self._kimios()
        s["kimios"] = bool(kos)
        if kos:
            for g in kos.get("gpus") or []:
                match = next((c for c in cards if c.get("name") == g.get("card")), None)
                if match is None:
                    match = {"name": g.get("card") or g.get("name") or "GPU", "source": "kimios"}
                    cards.append(match)
                for k in ("util", "temp", "power_w", "mem_used_mb", "mem_total_mb", "name_full"):
                    if g.get(k) is not None:
                        match[k] = g[k]
                if g.get("util") is not None:
                    match["source"] = "kimios"
        names = [g.get("name") for g in self.info.get("gpus") or []]
        for i, g in enumerate(cards):
            if g.get("source") == "sysfs" and i < len(names) and names[i]:
                g["label"] = names[i]
        s["gpus"] = cards
        utils = [g["util"] for g in cards if g.get("util") is not None]
        clocks = [g["clock_pct"] for g in cards if g.get("clock_pct") is not None]
        s["gpu"] = _num(max(utils)) if utils else None
        s["gpu_clock"] = _num(max(clocks)) if clocks and not utils else None
        mems = [g["mem_used_mb"] for g in cards if g.get("mem_used_mb") is not None]
        s["gpu_mem_mb"] = int(sum(mems)) if mems else None
        temps = [g["temp"] for g in cards if g.get("temp") is not None]
        s["gpu_temp"] = max(temps) if temps else None
        pw = [g["power_w"] for g in cards if g.get("power_w") is not None]
        s["gpu_power_w"] = _num(sum(pw)) if pw else None
        if s.get("kimilm_gpu") is None:
            s["kimilm_gpu"] = _num(self._drm_busy(alive, elapsed))

        # network and disks: bytes a second since the last sample
        try:
            n = psutil.net_io_counters()
            if self._net and elapsed > 0:
                s["net_rx"] = max(0, int((n.bytes_recv - self._net.bytes_recv) / elapsed))
                s["net_tx"] = max(0, int((n.bytes_sent - self._net.bytes_sent) / elapsed))
            self._net = n
        except Exception:
            pass
        try:
            d = psutil.disk_io_counters()
            if d and self._disk and elapsed > 0:
                s["disk_read"] = max(0, int((d.read_bytes - self._disk.read_bytes) / elapsed))
                s["disk_write"] = max(0, int((d.write_bytes - self._disk.write_bytes) / elapsed))
            self._disk = d
        except Exception:
            pass

        # temperatures, battery, power
        s["temps"] = {}
        try:
            for chip, entries in (psutil.sensors_temperatures() or {}).items():
                for e in entries:
                    if e.current and e.current > 0:
                        s["temps"][f"{chip} {e.label}".strip()] = _num(e.current)
                if chip in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz") and entries:
                    pkg = next((e.current for e in entries if "package" in (e.label or "").lower() or
                                (e.label or "").lower() in ("tctl", "tdie")), None)
                    s["temp_cpu"] = _num(pkg if pkg else max(e.current for e in entries))
        except Exception:
            pass
        try:
            b = psutil.sensors_battery()
            if b is not None:
                s["battery"] = _num(b.percent, 0)
                s["on_ac"] = bool(b.power_plugged)
        except Exception:
            pass
        if platform.system() == "Linux":
            for bat in Path("/sys/class/power_supply").glob("BAT*"):
                pn = _read(bat / "power_now")
                if pn is None:
                    cur, volt = _read(bat / "current_now"), _read(bat / "voltage_now")
                    pn = int(cur) * int(volt) / 1e6 if cur and volt else None
                if pn and _read(bat / "status") == "Discharging":
                    s["power_w"] = _num(int(float(pn)) / 1e6)
            psi = self._psi()
            s["psi_cpu"], s["psi_mem"], s["psi_io"] = psi.get("cpu"), psi.get("memory"), psi.get("io")
        if kos:
            for k in ("power_cpu_w", "power_gpu_w", "fan_rpm", "throttled", "slice_mem_mb", "slice_high_mb",
                      "slice_max_mb"):
                if kos.get(k) is not None:
                    s[k] = kos[k]
            if kos.get("temp_cpu") is not None and s.get("temp_cpu") is None:
                s["temp_cpu"] = kos["temp_cpu"]
            if kos.get("power_w") is not None and s.get("power_w") is None:
                s["power_w"] = kos["power_w"]

        # what the models are doing
        with self.lock:
            calls = list(self.calls.values())
        written = self._written
        s["tok_write"] = _num(max(0, written - self._written_seen) / elapsed if elapsed > 0 else 0)
        self._written_seen = written
        reading = [c for c in calls if c["phase"] == "reading"]
        s["reading"], s["writing"] = len(reading), len(calls) - len(reading)
        s["tok_read"] = _num(sum(c["read_tps"] for c in reading)) if reading else 0.0
        s["active"] = [{"model": c["model"], "phase": c["phase"], "tokens": c["tokens"],
                        "seconds": round(mono - c["t"], 1)} for c in calls]
        if self._want_top > mono:
            self.top = self._top(threads)
        return s

    def _top(self, threads, n=10):
        """The programs using the most processor time and memory right now (anyone's, not only KimiLM's)."""
        ours = {p.pid for p in self._ours()[0]}
        rows = []
        cache = getattr(self, "_all", {})
        fresh = {}
        for p in psutil.process_iter(["name", "memory_info"]):
            kept = cache.get(p.pid) or p
            try:
                c = kept.cpu_percent(None) / threads
                mem = (p.info["memory_info"].rss if p.info.get("memory_info") else 0) / 2**20
            except psutil.Error:
                continue
            fresh[p.pid] = kept
            rows.append({"pid": p.pid, "name": p.info.get("name") or "?", "cpu": _num(c), "mem_mb": int(mem),
                         "kimilm": p.pid in ours})
        self._all = fresh
        by_cpu = sorted(rows, key=lambda r: -(r["cpu"] or 0))[:n]
        by_mem = sorted(rows, key=lambda r: -r["mem_mb"])[:n]
        return {"cpu": by_cpu, "memory": by_mem}

    # ------------------------------------------------------------------ history
    def _keep(self, s):
        row = {k: s.get(k) for k in SCALARS}
        row["t"] = s["t"]
        with self.lock:
            s["new_marks"] = [m for m in self.marks if m["n"] > self._mark_sent]
            self._mark_sent = self._mark_n
            self.latest = s
            self.fine.append(row)
            self._minute.append(row)
            if self._minute and s["t"] - self._minute[0]["t"] >= 60:
                self.coarse.append(self._aggregate(self._minute))
                self._minute = []
        for loop, queue in list(self.listeners):
            try:
                loop.call_soon_threadsafe(self._offer, queue, s)
            except RuntimeError:
                self.listeners.discard((loop, queue))

    @staticmethod
    def _offer(queue, s):
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(s)

    @staticmethod
    def _aggregate(rows):
        out = {"t": rows[-1]["t"]}
        for k in SCALARS:
            vals = [r[k] for r in rows if r.get(k) is not None]
            out[k] = _num(sum(vals) / len(vals), 2) if vals else None
            if vals and k in ("cpu", "gpu", "kimilm_cpu", "tok_write", "net_rx", "net_tx", "disk_read", "disk_write"):
                out[k + "_max"] = max(vals)
        return out

    def history(self, seconds=300):
        seconds = max(10, min(int(seconds or 300), 86400))
        cutoff = time.time() - seconds
        with self.lock:
            if seconds <= FINE_SECONDS:
                rows = [r for r in self.fine if r["t"] >= cutoff]
                step = self.interval
            else:
                rows = [r for r in self.coarse if r["t"] >= cutoff] + ([self._aggregate(self._minute)]
                                                                       if self._minute else [])
                step = 60
            marks = [m for m in self.marks if m["t"] >= cutoff]
            latest = self.latest
        return {"info": self.info, "samples": rows, "step": step, "marks": marks, "latest": latest,
                "running": bool(self._thread), "kept": {"fine_seconds": FINE_SECONDS, "coarse_minutes": COARSE_MINUTES}}

    def processes(self):
        """Top programs: switched on for a while each time the window asks (it is the costliest part)."""
        self._want_top = time.monotonic() + 10
        if not self.top:
            self.top = self._top(psutil.cpu_count() or 1)
        return self.top

    def summary(self, seconds=300):
        """Peaks and averages over a window, for agents and the window's stat tiles."""
        rows = self.history(seconds)["samples"]
        out = {}
        for k in ("cpu", "gpu", "kimilm_cpu", "mem_used_mb", "kimilm_mem_mb", "tok_write", "net_rx", "net_tx",
                  "temp_cpu", "power_w"):
            vals = [r[k] for r in rows if r.get(k) is not None]
            if vals:
                out[k] = {"avg": _num(sum(vals) / len(vals)), "max": max(vals), "min": min(vals), "now": vals[-1]}
        return out

    async def subscribe(self, queue_size=30):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=queue_size)
        item = (loop, queue)
        self.listeners.add(item)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield None                 # keep-alive
        finally:
            self.listeners.discard(item)

    # ------------------------------------------------------------------ marks on the charts (what happened when)
    def mark(self, text, kind="info", t=None):
        with self.lock:
            self._mark_n += 1
            self.marks.append({"t": round(t or time.time(), 3), "text": text, "kind": kind, "n": self._mark_n})

    async def watch(self, bus, name_of=lambda mid: mid):
        """Mark models loading and unloading and tasks starting and ending, so a rise on a chart has a cause."""
        async for e in bus.stream(bus.buffer[-1]["seq"] if bus.buffer else 0):     # from now on
            if e is None:
                continue
            kind = e.get("kind")
            try:
                if kind == "model_status":
                    if e.get("unloaded"):
                        self.mark(f"Unloaded {name_of(e['unloaded'])}", "unload", e.get("time"))
                    mid, state = e.get("model_id"), e.get("state")
                    if mid and state and self._last_model_state.get(mid) != state and state in ("loading", "ready", "failed"):
                        self._last_model_state[mid] = state
                        self.mark({"loading": f"Loading {name_of(mid)}", "ready": f"Loaded {name_of(mid)}",
                                   "failed": f"{name_of(mid)} could not load"}[state],
                                  {"loading": "load", "ready": "load", "failed": "error"}[state], e.get("time"))
                    if e.get("unloaded"):
                        self._last_model_state.pop(e["unloaded"], None)
                elif kind == "job" and e.get("status") in ("running", "done", "failed", "cancelled"):
                    title = (e.get("title") or "a task")[:80]
                    word = {"running": "Started", "done": "Finished", "failed": "Failed", "cancelled": "Stopped"}[e["status"]]
                    self.mark(f"{word}: {title}", "start" if e["status"] == "running" else
                              ("error" if e["status"] == "failed" else "end"), e.get("time"))
            except Exception:                     # pragma: no cover
                pass
