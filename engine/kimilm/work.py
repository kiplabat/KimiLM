"""Media work that can be paused, stopped, recovered and continued (2.17.0).

A picture on a processor takes a quarter of an hour, a video with six scenes takes several of those. Work that
long has to survive what happens meanwhile: the user needs the computer for something else (pause), changes
their mind (stop), the engine runs out of memory or the app is closed (recover), and afterwards nobody wants to
pay for the finished part again (continue).

  Control   what Pause and Stop reach of a running task: the programs it has started (frozen and thawed as a
            whole: a frozen program uses no processor time and keeps its place) and a gate the work passes
            between its parts. Time spent paused does not count against a time limit. A pause that lasts longer
            than `pause_stop_minutes` turns into a stop, because a frozen image engine still holds gigabytes
            of memory; what was finished is kept and the job can be continued.
  Works     the jobs of a media window (Images, Video), kept in table `media_work` with what was asked and how
            far it got. They outlive the app: a job that was running when the app closed comes back as "cut
            off", and one that was stopped, cut off or failed can be continued. Continuing does only what is
            missing: the pictures that were not made yet, with the seeds they would have had; the scenes of a
            video whose pictures do not exist yet.

What cannot be continued in the middle: a single picture or clip. The image engine keeps no checkpoint while it
draws (its options at the pinned release have none), so a picture that was cut off is drawn again from its
first step. Pausing keeps it; only stopping loses the steps of the picture in hand. (Checked in the source of
the pinned release, master-1106-532d2c1: it can write a preview picture while it draws, but has no option to
start from saved progress. Picture b of a batch is drawn with seed + b, which is why a picture made later, on
its own, is the same picture it would have been.)"""
import asyncio
import shutil
import threading
import time

import psutil

from .db import new_id, now

OPEN = ("stopped", "interrupted", "failed")       # states a job can be continued from


class Control:
    def __init__(self):
        self.go = asyncio.Event()
        self.go.set()
        self.free = threading.Event()      # the same gate for work that runs in a thread (the video encoder)
        self.free.set()
        self.pids = {}             # the programs this work runs: pid -> when that program started
        self.listen = None         # called when the work is paused or continued, or its programs change
        self.since = None          # when the pause that is on began
        self.idle = 0.0            # seconds spent paused before that
        self.why = None            # why the work was stopped, when it was not the user ("paused for 20 minutes…")

    @property
    def paused(self):
        return not self.go.is_set()

    @staticmethod
    def _each(pid):
        try:
            proc = psutil.Process(pid)
            return proc.children(recursive=True) + [proc]
        except psutil.Error:
            return []

    def _freeze(self, pids, on):
        for pid in pids:
            for proc in self._each(pid):
                try:
                    proc.suspend() if on else proc.resume()
                except psutil.Error:
                    pass

    def tell(self):
        if self.listen:
            try:
                self.listen()
            except Exception:
                pass

    def programs(self):
        return [[pid, started] for pid, started in self.pids.items()]

    def attach(self, proc):
        """A program the work has started. Started during a pause, it is frozen at once."""
        try:
            self.pids[proc.pid] = psutil.Process(proc.pid).create_time()
        except psutil.Error:
            self.pids[proc.pid] = 0
        if self.paused:
            self._freeze([proc.pid], True)
        self.tell()

    def detach(self, proc):
        self.pids.pop(proc.pid, None)
        self.tell()

    def pause(self):
        if not self.paused:
            self.go.clear()
            self.free.clear()
            self.since = time.monotonic()
            self._freeze(list(self.pids), True)

    def resume(self):
        if self.paused:
            self._freeze(list(self.pids), False)
            self.idle += time.monotonic() - self.since
            self.since = None
            self.go.set()
            self.free.set()

    def paused_for(self):
        """Seconds this work has spent paused, in all."""
        return self.idle + (time.monotonic() - self.since if self.since is not None else 0.0)

    async def wait(self):
        """The gate between two parts of the work: passes at once unless the work is paused."""
        await self.go.wait()

    def hold(self, stop=None):
        """The same gate for work in a thread: returns when the work may go on, or when `stop()` says it ended."""
        while not self.free.wait(0.25):
            if stop and stop():
                return


def end_leftover(programs):
    """Programs a job ran when the app went away (it crashed, or was ended while the job was paused): they would
    hold their memory for ever, frozen ones without even working. Ended only when the program with that number
    is still the one that was started then. Returns how many were ended."""
    ended = 0
    for pid, started in programs or []:
        try:
            proc = psutil.Process(int(pid))
            if not started or abs(proc.create_time() - float(started)) > 2:
                continue
            for p in proc.children(recursive=True) + [proc]:
                try:
                    p.resume()
                    p.kill()
                except psutil.Error:
                    pass
            ended += 1
        except (psutil.Error, ValueError, TypeError):
            continue
    return ended


async def watch(proc, reading, limit, ctl):
    """Follow a program the work started until it ends: `reading` is the coroutine that reads what it prints.
    Raises asyncio.TimeoutError when it has worked (not counting pauses) longer than `limit` seconds. Whatever
    ends the wait early (the limit, Stop), the program is ended too. Returns the program's exit code."""
    ctl.attach(proc)
    task = asyncio.ensure_future(reading)
    began, idle = time.monotonic(), ctl.paused_for()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=1.0)
            if done:
                break
            if time.monotonic() - began - (ctl.paused_for() - idle) > limit:
                raise asyncio.TimeoutError()
        task.result()
        return await proc.wait()
    except BaseException:
        task.cancel()
        for p in ctl._each(proc.pid):             # the program and what it started (a wrapper script's child)
            try:
                p.resume()                        # a frozen program is thawed so that it can be ended cleanly
                p.kill()
            except psutil.Error:
                pass
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        raise
    finally:
        ctl.detach(proc)


class Works:
    """The jobs of one media window, kept in the database."""

    def __init__(self, app, kind, event, keep=24):
        self.app, self.kind, self.event, self.keep = app, kind, event, keep
        self.jobs = {}
        rows = app.db.all("SELECT * FROM media_work WHERE kind=? ORDER BY created DESC LIMIT ?", (kind, keep))
        for row in reversed(rows):
            job = {**(row.get("result") or {}), "id": row["id"], "what": row["what"], "title": row["title"],
                   "state": row["state"], "text": row["text"] or "", "error": row["error"], "started": row["created"],
                   "request": row.get("request") or {}, "job_id": row["job_id"]}
            if job["state"] in ("running", "paused"):       # the app went away while it worked
                if end_leftover(job.pop("programs", None)):
                    app.log(f"{kind} job {job['id']}: ended the engine it had left running")
                job.update(state="interrupted", text="The app was closed while this was running")
                self._write(job)
            job.pop("programs", None)
            self.jobs[job["id"]] = job

    def new(self, what, title, request, **fields):
        job = {"id": new_id("img_" if self.kind == "images" else "vid_"), "what": what, "title": str(title)[:200],
               "state": "running", "text": "Starting…", "error": None, "started": now(), "request": request,
               "job_id": None, **fields}
        self.jobs[job["id"]] = job
        for old in list(self.jobs)[:-self.keep]:
            if self.jobs[old]["state"] not in ("running", "paused"):
                self._drop(old)
        self.app.db.insert("media_work", {"id": job["id"], "kind": self.kind, "what": what, "title": job["title"],
                                         "state": "running", "text": job["text"], "request": request, "result": {},
                                         "created": job["started"], "updated": job["started"]})
        return job

    def view(self, job):
        """A job as the window gets it."""
        out = {k: v for k, v in job.items() if not k.startswith("_")}
        out["can_continue"] = job["state"] in OPEN and bool(job.get("request"))
        out["can_pause"] = job["state"] in ("running", "paused") and bool(job.get("job_id")) \
            and job["job_id"] in self.app.jobs.controls
        return out

    def _write(self, job):
        keep = ("id", "what", "title", "state", "text", "error", "started", "request", "job_id")
        try:
            self.app.db.update("media_work", job["id"], {
                "state": job["state"], "text": str(job.get("text") or "")[:500], "error": job.get("error"),
                "request": job.get("request") or {}, "job_id": job.get("job_id"), "updated": now(),
                "result": {k: v for k, v in job.items() if k not in keep and not k.startswith("_")}})
        except Exception as exc:              # the work goes on even if it could not be written down
            self.app.log(f"saving {self.kind} job {job['id']}: {type(exc).__name__}: {exc}")

    def _drop(self, job_id):
        self.jobs.pop(job_id, None)
        self.app.db.delete("media_work", job_id)
        shutil.rmtree(self.folder(job_id, make=False), ignore_errors=True)

    def folder(self, job_id, make=True):
        """Where a job keeps what it has made so far and still needs (a video's narration and music), so that
        continuing does not make it again. Removed when the job is done or forgotten."""
        path = self.app.paths.data / "work" / job_id
        if make:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def save(self, job, **changes):
        """Write the job down and tell the windows. What the job made so far is its result."""
        job.update(changes)
        self._write(job)
        self.app.bus.publish(self.event, **self.view(job))

    def bind(self, job, task_id):
        """Follow the task's pauses: the job says "paused" while it is, whoever paused it (this window or the
        task's own page), says whether it can be paused at all, and remembers the programs it runs."""
        def changed():
            ctl = self.app.jobs.controls.get(task_id)
            if ctl and job["state"] in ("running", "paused"):
                job["programs"] = ctl.programs()
                self.save(job, state="paused" if ctl.paused else "running")
        self.app.jobs.listeners[task_id] = changed

    def ended(self, job, state, **changes):
        """The job has ended (done, stopped, failed)."""
        self.app.jobs.listeners.pop(job.get("job_id"), None)
        job.pop("programs", None)
        self.save(job, state=state, **changes)
        if state == "done":
            shutil.rmtree(self.folder(job["id"], make=False), ignore_errors=True)

    def pause(self, job_id):
        job = self.get(job_id)
        if not (job.get("job_id") and self.app.jobs.pause(job["job_id"])):
            raise ValueError("This cannot be paused right now")
        return self.view(job)

    def resume(self, job_id):
        job = self.get(job_id)
        if not (job.get("job_id") and self.app.jobs.resume(job["job_id"])):
            raise ValueError("This is not paused")
        return self.view(job)

    def stop(self, job_id):
        job = self.jobs.get(job_id)
        if job and job.get("job_id") and job["state"] in ("running", "paused"):
            self.app.jobs.cancel(job["job_id"])

    def tell(self, job, text):
        """A step said by the work: shown at once, written down with the next save."""
        job["text"] = text
        self.app.bus.publish(self.event, **self.view(job))

    def list(self, n=8):
        return [self.view(j) for j in list(self.jobs.values())[-n:]]

    def get(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return job

    def forget(self, job_id):
        job = self.get(job_id)
        if job["state"] in ("running", "paused"):
            raise ValueError("Stop it first")
        self._drop(job_id)
