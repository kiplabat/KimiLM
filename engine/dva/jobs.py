"""Tasks: every chat turn, pipeline run, scheduled run and plugin action runs as a task that keeps going when the
window is closed (the app stays in the tray). Status, progress and a log are kept for the Activity page.

From 2.14.0 everything that works in the background is a task here, also pictures, sound, video, downloads and
installs, and every task keeps a trail that can be read while it runs and afterwards:
  trail    what it did, step by step, with the time of each step (`<data>/jobs/<id>.trail`, one JSON object a line);
           lines a program printed are kept as kind "output";
  detail   its plan and what is known so far: the model, the settings, an episode's parts, the script written so
           far (`<data>/jobs/<id>.detail.json`);
  quiet    for a running task, the seconds since its last sign of life, so that "working on a long step" and
           "stuck" can be told apart by looking."""
import asyncio
import contextlib
import json
import re
import time

from .db import new_id, now

ACTIVE = ("queued", "running", "waiting")
MEDIA = ("images", "sound", "video")          # tasks that are one piece of work, with stages of their own


@contextlib.asynccontextmanager
async def turn(lock, status, waiting):
    """Take the lock of work that goes one at a time on this computer (pictures, sound), and say so when that
    means waiting: otherwise a task that waits its turn looks like one that is stuck."""
    try:
        await asyncio.wait_for(lock.acquire(), 0.05)
    except asyncio.TimeoutError:
        status(waiting)
        await lock.acquire()
    try:
        yield
    finally:
        lock.release()


class Track:
    """What a piece of work reports through. Made by Jobs.track(job_id); with no task it does nothing."""
    def __init__(self, jobs, job_id):
        self.jobs, self.id = jobs, job_id
        self.owns = False        # this work said the task's stages (work that is a part of other work did not)
        # a chat reply or an agent's run does many things in turn: a tool's stages are not the whole task's plan
        self.whole = bool(job_id) and (jobs.db.get("jobs", job_id) or {}).get("kind") in MEDIA

    def step(self, text, **data):
        if self.id and text:
            self.jobs.trail(self.id, text, "step", **data)

    def output(self, line):
        if self.id and line:
            self.jobs.trail(self.id, line, "output")

    def note(self, text, **data):
        if self.id and text:
            self.jobs.trail(self.id, text, "note", **data)

    def plan(self, **detail):
        if self.id:
            self.jobs.plan(self.id, detail)

    def stages(self, names):
        """The stages this work goes through, said before it starts. The first caller decides: work that is a part
        of other work (the voices of a podcast) does not replace the whole's stages."""
        if self.id and self.whole and "stages" not in self.jobs.read_detail(self.id):
            self.owns = True
            self.jobs.plan(self.id, {"stages": list(names), "stage": 0})

    def at(self, name):
        """The stage that is being worked on now. Only the work that said the stages moves through them: a
        picture made for a video has a "Save" of its own, which is not the video's."""
        if self.id and self.owns:
            stages = self.jobs.read_detail(self.id).get("stages") or []
            if name in stages:
                self.jobs.plan(self.id, {"stage": stages.index(name)})


WORDS = {"starting": "Starting…", "queued": "Waiting to start", "connecting": "Connecting…", "downloading": "Downloading",
         "verifying": "Checking the file", "extracting": "Unpacking", "unpacking": "Unpacking", "retrying": "Trying again",
         "loading": "Loading", "paused": "Paused"}


def size_text(n):
    n = float(n or 0)
    return f"{n / 2**30:.2f} GB" if n >= 2**30 else f"{n / 2**20:.0f} MB" if n >= 2**20 else f"{n / 1024:.0f} KB"


class Jobs:
    def __init__(self, app):
        self.app = app
        self.db = app.db
        self.tasks = {}
        self.live = {}       # id -> last progress text
        self._last_pub = {}
        self.seen = {}       # id -> time of the last sign of life
        self.details = {}    # id -> plan and facts of a running task
        self.fraction = {}   # id -> how far it is (0..1), when that is known
        self._last_step = {}
        for row in self.db.all("SELECT id FROM jobs WHERE status IN ('queued','running','waiting')"):
            self.db.update("jobs", row["id"], {"status": "interrupted", "finished": now(),
                                               "error": "The app was closed while this was running"})

    def create(self, kind, title, coro_factory, chat_id=None, schedule_id=None, harness=None, model=None,
               payload=None, notify=False, on_done=None):
        job_id = new_id("job_")
        row = {"id": job_id, "kind": kind, "title": title[:200], "status": "queued", "chat_id": chat_id,
               "schedule_id": schedule_id, "harness": harness, "model": model, "payload": payload or {},
               "created": now()}
        self.db.insert("jobs", row)
        row = self.db.get("jobs", job_id)
        self.tasks[job_id] = asyncio.create_task(self._run(row, coro_factory, notify, on_done))
        self.app.bus.publish("job", id=job_id, status="queued", title=row["title"], job_kind=kind, chat_id=chat_id)
        return row

    async def _run(self, row, coro_factory, notify, on_done=None):
        job_id = row["id"]
        self.seen[job_id] = time.time()
        self.set_status(job_id, "running", started=now())
        status, error, result = "done", None, {}
        try:
            result = await coro_factory(row) or {}
        except asyncio.CancelledError:
            status, error = "cancelled", "Stopped"
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {str(exc)[:500]}"
            self.app.log(f"job {job_id} failed: {error}")
        finally:
            self.tasks.pop(job_id, None)
            self.live.pop(job_id, None)
            self.seen.pop(job_id, None)
            self.details.pop(job_id, None)
            self.fraction.pop(job_id, None)
            self._last_step.pop(job_id, None)
        if error:
            self.trail(job_id, error if status != "cancelled" else "Stopped", "error" if status == "failed" else "note", final=True)
        else:
            self.trail(job_id, "Finished", "note", final=True)
        self.db.update("jobs", job_id, {"status": status, "error": error, "finished": now(),
                                        "result": result if isinstance(result, dict) else {"value": str(result)}})
        self.app.bus.publish("job", id=job_id, status=status, title=row["title"], job_kind=row["kind"],
                             chat_id=row["chat_id"], error=error)
        if notify:
            self.app.bus.publish("notify", title=("Task finished: " if status == "done" else "Task " + status + ": ")
                                 + row["title"], body=(error or "Open it to see the result."), chat_id=row["chat_id"])
        if on_done:
            try:
                on_done(status)
            except Exception as exc:
                self.app.log(f"after job {job_id}: {type(exc).__name__}: {exc}")

    def set_status(self, job_id, status, **extra):
        self.db.update("jobs", job_id, {"status": status, **extra})
        row = self.db.get("jobs", job_id)
        self.app.bus.publish("job", id=job_id, status=status, title=row["title"], job_kind=row["kind"],
                             chat_id=row["chat_id"], progress=self.live.get(job_id))

    def track(self, job_id):
        return Track(self, job_id)

    def _file(self, job_id, ending):
        folder = self.app.paths.data / "jobs"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{job_id}.{ending}"

    def trail(self, job_id, text, kind="step", final=False, **data):
        """Add a step (or a program's output line, a note, an error) to a task's trail."""
        text = str(text)[:600]
        if kind == "step":
            if self._last_step.get(job_id) == text:            # the same step said again: a sign of life, not a new line
                self.seen[job_id] = time.time()
                return
            self._last_step[job_id] = text
            self.live[job_id] = text
            self.fraction.pop(job_id, None)                    # how far the step before was says nothing about this one
        if not final:
            self.seen[job_id] = time.time()
        entry = {"t": now(), "kind": kind, "text": text, **({"data": data} if data else {})}
        try:
            with self._file(job_id, "trail").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            return
        t = time.monotonic()
        if kind != "output" or t - self._last_pub.get(job_id + ":o", 0) > 0.5:      # output can come fast
            self._last_pub[job_id + (":o" if kind == "output" else ":s")] = t
            self.app.bus.publish("job_trail", id=job_id, entry=entry)

    def plan(self, job_id, detail):
        """Merge facts into a task's plan (what it will do, with what, and what is known so far)."""
        merged = {**self.read_detail(job_id), **detail}
        merged = {k: v for k, v in merged.items() if v is not None}          # None takes a part of the plan away
        self.details[job_id] = merged
        self.seen[job_id] = time.time()
        try:
            self._file(job_id, "detail.json").write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        except (OSError, TypeError, ValueError):
            pass
        self.app.bus.publish("job_detail", id=job_id, detail=merged)

    def read_trail(self, job_id, limit=600):
        path = self._file(job_id, "trail")
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def read_detail(self, job_id):
        if job_id in self.details:
            return self.details[job_id]
        path = self._file(job_id, "detail.json")
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except ValueError:
            return {}

    def quiet(self, job_id):
        """Seconds since a running task's last sign of life (None when it is not running)."""
        if job_id not in self.tasks:
            return None
        return round(time.time() - self.seen[job_id]) if job_id in self.seen else None

    def reporter(self, on_status, job_id):
        """What a piece of work reports through: every step goes to the caller (a window's job, a chat's status
        line) and to the task's trail, where it can be read with its time. Returns (status, track)."""
        track = self.track(job_id)

        def status(text):
            if on_status:
                on_status(text)
            track.step(text)

        def beat(text):
            """A sign of life that is not a new step (a counter that moves): shown, not added to the trail."""
            if on_status:
                getattr(on_status, "beat", on_status)(text)
            if job_id:
                self.progress(job_id, text)
        status.beat = beat
        return status, track

    def follow(self, kind, title, work, state, stop=None, payload=None):
        """Run `work` as a task: a coroutine that keeps a state of its own up to date (a download, an install).
        `state()` returns that state: {"state", "phase" | "file", "done", "total", "rate", "error"}. It is read once a
        second: a new phase or file is a step in the trail, bytes that moved are progress and a sign of life.
        `stop()` asks the work to stop in its own way (so that it can keep a part-downloaded file)."""
        async def run(row):
            job_id = row["id"]
            task = asyncio.ensure_future(work)
            said, moved, st = None, None, {}
            try:
                while True:
                    done, _ = await asyncio.wait({task}, timeout=1.0)
                    st = state() or {}
                    text = st.get("phase") or " ".join(x for x in (
                        WORDS.get(st.get("state"), ""), st.get("file") if st.get("state") == "downloading" else "") if x)
                    key = re.sub(r"in \d+ s\b", "", text)
                    if text and key != said:
                        said = key
                        self.trail(job_id, text, "step")
                    if st.get("total") and st.get("done") != moved:
                        moved = st.get("done")
                        self.progress(job_id, f"{size_text(moved)} of {size_text(st['total'])}"
                                      + (f" · {size_text(st['rate'])} a second" if st.get("rate") else ""),
                                      fraction=min(1.0, (moved or 0) / st["total"]))
                    if done:
                        break
            except asyncio.CancelledError:
                (stop or task.cancel)()
                try:
                    await asyncio.wait_for(asyncio.shield(task), 10)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    task.cancel()
                raise
            task.result()
            if st.get("state") in ("failed",):
                raise RuntimeError(st.get("error") or "It failed")
            if st.get("state") in ("cancelled", "stopped", "removed", "paused"):
                raise asyncio.CancelledError()
            return {k: st[k] for k in ("file", "total") if st.get(k)}
        return self.create(kind, title, run, payload=payload)

    def progress(self, job_id, text, fraction=None):
        self.live[job_id] = text
        self.seen[job_id] = time.time()
        if fraction is not None:
            self.fraction[job_id] = round(float(fraction), 4)
        t = time.monotonic()
        if t - self._last_pub.get(job_id, 0) > 0.7:
            self._last_pub[job_id] = t
            self.app.bus.publish("job_progress", id=job_id, text=text, fraction=self.fraction.get(job_id))

    def log(self, job_id, line):
        path = self.app.paths.data / "jobs" / f"{job_id}.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log_tail(self, job_id, lines=200):
        path = self.app.paths.data / "jobs" / f"{job_id}.log"
        if not path.exists():
            return ""
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])

    def cancel(self, job_id):
        task = self.tasks.get(job_id)
        if task:
            task.cancel()
            for ap_id, ap in list(self.app.approvals.items()):
                if ap["job_id"] == job_id and not ap["future"].done():
                    ap["future"].set_result({"allow": False})
            return True
        return False

    def running_for_chat(self, chat_id):
        return next((jid for jid, _ in self.tasks.items()
                     if (self.db.get("jobs", jid) or {}).get("chat_id") == chat_id), None)

    def busy_with_model(self):
        return any((self.db.get("jobs", jid) or {}).get("kind") in ("chat", "schedule", "agent") for jid in self.tasks)

    def list(self, limit=100, status=None, kinds=None):
        sql = "SELECT * FROM jobs"
        params, where = [], []
        if status == "active":
            where.append("status IN ('queued','running','waiting')")
        if kinds:
            where.append("kind IN (" + ",".join("?" * len(kinds)) + ")")
            params += list(kinds)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created DESC LIMIT ?"
        params.append(limit)
        rows = self.db.all(sql, params)
        for r in rows:
            r["progress"] = self.live.get(r["id"])
            r["quiet"] = self.quiet(r["id"])
            r["fraction"] = self.fraction.get(r["id"])
        return rows

    def get(self, job_id):
        row = self.db.get("jobs", job_id)
        if row:
            row["progress"] = self.live.get(job_id)
            row["log"] = self.log_tail(job_id)
            row["trail"] = self.read_trail(job_id)
            row["detail"] = self.read_detail(job_id)
            row["quiet"] = self.quiet(job_id)
            row["fraction"] = self.fraction.get(job_id)
        return row

    async def shutdown(self):
        for task in list(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.wait(list(self.tasks.values()), timeout=10)
