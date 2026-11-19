"""The programs KimiLM starts for agents, and the guarantee that none of them outlives its purpose (2.19.0).

A long-running task of 18 November 2026 left the computer full of Python processes. The causes, all fixed here:

  run_python and run_command waited for their program in a thread (`subprocess.run` inside `asyncio.to_thread`).
  A thread cannot be cancelled: when the planner retired an agent, a step ran out of time or the user pressed
  Stop, the agent's task ended and its program ran on. At the program's own time limit only the top process was
  ended (a program it had started kept running), and on Windows the wait itself could then hang for good, with
  the thread. The model chose the time limit, with no ceiling. Every retried round added more.

  When the engine itself ended (closed, crashed, killed), Windows did not end what it had started.

So:
  `Procs.run(cmd, ...)` starts a program as an asyncio process, reads what it prints with a cap, and ends it
  with everything it started (`end_tree`) when it runs too long, prints too much, or the task waiting for it is
  cancelled. Programs run at low priority, so the model and the window stay responsive, and at most
  `code_runs_at_once` run together (more wait their turn).

  Each program is written down with the task that owns it (`owner`: a task id). When a task ends, whatever it
  still owns is ended (`end_owner`). The list is also kept in `<data>/procs.json`: programs left by an engine
  that went away are ended at the next start (`end_leftovers`), only if the program with that number is still
  the one that was started then.

  On Windows the engine puts itself in a job object that ends every process in it when the engine's last handle
  closes (`contain`): whatever happens to the engine, its programs, models and helpers end with it.

A program that ends but leaves another running (a program in the background, a server) does not leave it
unaccounted for (2.21.0): what it started is noted while it runs, and what is still running when it ends stays on
the list as "left running", with the same task; it ends when the task ends (or at once, with no task).

`GET /api/procs` lists what runs now; `DELETE /api/procs/{pid}` ends one (Activity shows them)."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import psutil

from .db import now

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
BELOW_NORMAL = 0x00004000                 # Windows priority class


def end_tree(pid, grace=0.0):
    """End a program and every program it started. Returns how many processes were ended."""
    try:
        top = psutil.Process(int(pid))
    except (psutil.Error, ValueError, TypeError):
        return 0
    try:
        family = top.children(recursive=True) + [top]
    except psutil.Error:
        family = [top]
    for p in family:
        try:
            p.resume()                    # a frozen program (2.17.0's pause) ends only once it is thawed
        except psutil.Error:
            pass
    if grace > 0:
        for p in family:
            try:
                p.terminate()
            except psutil.Error:
                pass
        _, family = psutil.wait_procs(family, timeout=grace)
    for p in family:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(family, timeout=3)
    return len(family)


def lower(pid):
    """Run a program at low priority: it still gets every spare cycle, but not the model's or the window's."""
    try:
        psutil.Process(pid).nice(BELOW_NORMAL if os.name == "nt" else 10)
    except (psutil.Error, OSError, ValueError):
        pass


def contain():
    """Windows: put the engine in a job object that ends all of its processes when the engine ends, however it
    ends. Processes started later belong to the job too. Returns the job's handle (kept for the engine's life),
    or None where this is not possible."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        # kill on job close | die on unhandled exception | breakaway allowed (a browser's sandbox asks for it; a
        # program that breaks away is one its own parent ends)
        info.BasicLimitInformation.LimitFlags = 0x2000 | 0x400 | 0x800
        if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(wintypes.HANDLE(job))
            return None
        if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(k32.GetCurrentProcess())):
            k32.CloseHandle(wintypes.HANDLE(job))       # already in a job that does not allow this: leave it
            return None
        return job
    except Exception:
        return None


class Procs:
    def __init__(self, app):
        self.app = app
        self.live = {}                    # pid -> {"pid", "started", "kind", "label", "owner", "since"}
        self.turns = None                 # asyncio.Semaphore for code runs, made on first use (needs the loop)
        self.turns_n = 0
        self.job = None

    # ------------------------------------------------------------------ the list, on disk too
    @property
    def file(self):
        return self.app.paths.data / "procs.json"

    def _write(self):
        try:
            self.file.write_text(json.dumps(list(self.live.values())), encoding="utf-8")
        except OSError:
            pass

    def end_leftovers(self):
        """Programs written down by an engine that went away: ended, if they are still those programs."""
        try:
            rows = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = []
        ended = []
        for row in rows if isinstance(rows, list) else []:
            try:
                p = psutil.Process(int(row["pid"]))
                if abs(p.create_time() - float(row["started"])) > 2:
                    continue
            except (psutil.Error, KeyError, ValueError, TypeError):
                continue
            end_tree(row["pid"])
            ended.append(f"{row.get('kind')} {row.get('label', '')[:60]} (pid {row['pid']})")
        self.live = {}
        self._write()
        if ended:
            self.app.log("ended programs left by the engine before: " + "; ".join(ended))
        return ended

    def add(self, proc, kind, label="", owner=None):
        try:
            started = psutil.Process(proc.pid).create_time()
        except psutil.Error:
            started = 0
        self.live[proc.pid] = {"pid": proc.pid, "started": started, "kind": kind, "label": str(label)[:200],
                               "owner": owner, "since": now()}
        self._write()

    def drop(self, pid):
        if self.live.pop(pid, None) is not None:
            self._write()

    def listing(self):
        out = []
        for row in list(self.live.values()):
            try:
                p = psutil.Process(row["pid"])
                family = [p] + p.children(recursive=True)
                cpu = sum(x.cpu_times().user + x.cpu_times().system for x in family)
                mem = sum(x.memory_info().rss for x in family)
                out.append({**row, "processes": len(family), "cpu_seconds": round(cpu, 1), "memory_mb": round(mem / 2**20)})
            except psutil.Error:
                self.drop(row["pid"])
        return out

    def end(self, pid):
        row = self.live.get(int(pid))
        if not row:
            raise KeyError(pid)
        end_tree(row["pid"])
        self.drop(row["pid"])
        return row

    def end_owner(self, owner):
        """A task has ended: whatever it still runs ends too."""
        gone = [row for row in list(self.live.values()) if owner and row.get("owner") == owner]
        for row in gone:
            end_tree(row["pid"])
            self.drop(row["pid"])
        return len(gone)

    def end_all(self):
        for row in list(self.live.values()):
            end_tree(row["pid"])
        self.live = {}
        self._write()

    # ------------------------------------------------------------------ what a program leaves behind (2.21.0)
    async def _watch(self, pid, family):
        """Note what a program starts while it runs, so that what it leaves running can be found when it is gone
        (a program whose parent has ended is no longer anyone's child)."""
        while True:
            try:
                kids = await asyncio.to_thread(lambda: psutil.Process(pid).children(recursive=True))
                for c in kids:
                    try:
                        family.setdefault(c.pid, c.create_time())
                    except psutil.Error:
                        pass
            except psutil.Error:
                return
            await asyncio.sleep(1)

    def strays(self, top, family, since):
        """Programs that `top` started (seen while it ran, or, on Windows, naming it or one of them as parent) and
        that are still running: only the outermost of each group (ending it ends what it started)."""
        found = {}
        for pid, created in list(family.items()):
            try:
                p = psutil.Process(pid)
                if abs(p.create_time() - created) <= 1 and p.status() != psutil.STATUS_ZOMBIE:
                    found[pid] = p
            except psutil.Error:
                pass
        if os.name == "nt":                # Windows keeps an orphan's parent number: ones started too late to be seen
            known = set(family) | {top}
            for p in psutil.process_iter(["ppid", "create_time"]):
                if p.info["ppid"] in known and (p.info["create_time"] or 0) >= since and p.pid not in found:
                    found[p.pid] = p
        out = []
        for pid, p in found.items():
            try:
                if p.ppid() not in found:
                    out.append(p)
            except psutil.Error:
                pass
        return out

    def keep(self, p, label, owner):
        try:
            self.live[p.pid] = {"pid": p.pid, "started": p.create_time(), "kind": "left running", "label": label,
                                "owner": owner, "since": now()}
            self._write()
            if hasattr(self.app, "log"):
                self.app.log(f"a program left {p.pid} running; it ends with its task ({owner})")
        except psutil.Error:
            pass

    @staticmethod
    def _end_strays(left):
        for p in left:
            end_tree(p.pid)

    # ------------------------------------------------------------------ running one program
    async def run(self, argv, *, cwd, env, timeout, kind="code", label="", owner=None, limit=4_000_000,
                  on_line=None, low=True, queue=True):
        """Run a program to its end and return (exit code, output). Output (stdout and stderr together) is kept up
        to `limit` characters: the start and the end, the middle is dropped. Raises asyncio.TimeoutError when it
        ran longer than `timeout` seconds; it and what it started are ended then, and when the caller is
        cancelled. `queue`: wait for a free place among `code_runs_at_once`."""
        n = max(1, int(self.app.db.setting("code_runs_at_once", 2) or 2))
        if self.turns is None or n != self.turns_n:            # a changed setting applies to the next program
            self.turns, self.turns_n = asyncio.Semaphore(n), n
        if queue and self.turns.locked() and on_line:
            on_line(None)                  # "waiting for another program to finish"
        async with (self.turns if queue else _Nothing()):
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, creationflags=NO_WINDOW)
            if low:
                lower(proc.pid)
            self.add(proc, kind, label, owner)
            since = time.time() - 1
            family = {}                    # what it starts, seen while it runs: pid -> start time (2.21.0)
            watch = asyncio.create_task(self._watch(proc.pid, family))
            head, tail, size = "", "", 0
            half = limit // 2

            async def read():
                nonlocal head, tail, size
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        return
                    text = chunk.decode("utf-8", errors="replace")
                    if on_line:
                        on_line(text)
                    size += len(text)
                    if len(head) < half:                   # the start is kept, then the latest part
                        take = text[: half - len(head)]
                        head, text = head + take, text[len(take):]
                    if text:
                        tail = (tail + text)[-half:]
            try:
                await asyncio.wait_for(read(), timeout)
                code = await asyncio.wait_for(proc.wait(), 10)
            except BaseException:          # too long, or the task waiting for it was cancelled: end it all
                watch.cancel()
                await asyncio.to_thread(end_tree, proc.pid)
                await asyncio.to_thread(self._end_strays, self.strays(proc.pid, family, since))
                raise
            finally:
                watch.cancel()
                self.drop(proc.pid)
            # 2.21.0: what it started and left running (a program in the background, a server) stays on the list
            # with the same task, is shown in Activity, and ends when the task ends; with no task, it ends now
            left = await asyncio.to_thread(self.strays, proc.pid, family, since)
            if left and owner:
                for p in left:
                    self.keep(p, f"left running by {label or kind}"[:200], owner)
            elif left:
                await asyncio.to_thread(self._end_strays, left)
            cut = size - len(head) - len(tail)
            return code, head + (f"\n…({cut:,} characters of output left out)…\n" if cut > 0 else "") + tail


class _Nothing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def python_argv(script, helpers=None):
    """The app's own Python, isolated from the user's settings, running `script` so that errors still name the
    script and its lines; `helpers` is a folder put first on the import path (the kimilm module)."""
    boot = ("import sys, runpy; sys.argv = [sys.argv[1]]"
            + (f"; sys.path.insert(0, {str(helpers)!r})" if helpers else "")
            + "; runpy.run_path(sys.argv[0], run_name='__main__')")
    return [sys.executable, "-I", "-c", boot, str(script)]


HELPERS = Path(__file__).parent / "pyhelper"
