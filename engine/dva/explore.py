"""Pushing boundaries a bit (2.24.0): measuring better setups while the computer is idle.

KimiOS scope §2.5 and principle 9: widen what this computer can do one small, measured, reversible step at a
time, and keep a step only if it is measurably better. The first step that fits is the one KimiLM can already
measure:

  1. "Find the fastest setup" (Models.tune) for a local model that has not been measured, or not for a long time;
  2. when every setup is measured: the evaluation (Check this setup, 12 tasks) for a local model that was never
     evaluated, at most once a week (explore_evaluate, on). Its score is what KimiOS's `abilities` tool reports.

It runs only when

  - it is switched on (setting explore_when_idle, on by default; Settings shows what was tried),
  - nothing else is happening: no chat, task or evaluation running or queued, no model in use, no measuring,
  - KimiLM has been idle for explore_idle_minutes (30), and so has the person at the computer where the desktop
    can tell (keyboard and mouse: GNOME, Windows), and the processor is quiet,
  - the computer is on mains power,
  - the last exploration was more than explore_every_hours (24) ago,
  - the model's file is there (a drive is never mounted for this).

Anything that starts while it measures stops it at once, and so does someone using the computer again. The result is the model's suggested configuration, as when
the user presses the button: nothing is applied on its own, and the model card says what was found.
"""
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import now
from .providers import is_cloud

CHECK_SECONDS = 60
HISTORY = 10


def age(stamp):
    """How long ago a stored time was (KimiLM stores UTC ISO times; a time without a zone is taken as UTC)."""
    t = datetime.fromisoformat(str(stamp))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - t


class Explorer:
    def __init__(self, app):
        self.app, self.db = app, app.db
        self.current = None              # {"model_id", "started"} while measuring
        self.reason = "not checked yet"  # why it is not exploring right now

    # ------------------------------------------------------------------ settings
    def _s(self, key, default):
        try:
            return type(default)(self.db.setting(key, default))
        except (TypeError, ValueError):
            return default

    def enabled(self):
        return bool(self.db.setting("explore_when_idle", True))

    # ------------------------------------------------------------------ conditions
    def busy(self):
        """What is happening that exploring must not compete with, or None."""
        n = (self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running','waiting')") or {}).get("n", 0)
        if n:
            return f"{n} chat or task{'s' if n != 1 else ''} running"
        if getattr(self.app, "evals", None) and self.app.evals.current:
            return "an evaluation is running"
        if any(s.users for s in self.app.runtime.servers.values()):
            return "a model is in use"
        tunes = getattr(self.app.models, "tunes", {}) or {}
        if any(t.get("state") == "running" for t in tunes.values()):
            return "a setup is being measured"
        return None

    @staticmethod
    def desktop_idle_minutes():
        from .hardware import user_idle_seconds
        s = user_idle_seconds()
        return None if s is None else s / 60

    def idle_minutes(self):
        last = getattr(self.app.runtime, "last_used", None)
        since_model = (time.monotonic() - last) / 60 if last else float("inf")
        row = self.db.one("SELECT MAX(COALESCE(finished, started, created)) AS t FROM jobs") or {}
        since_job = float("inf")
        if row.get("t"):
            try:
                since_job = age(row["t"]).total_seconds() / 60
            except ValueError:
                pass
        desk = self.desktop_idle_minutes()
        return min(since_model, since_job, desk if desk is not None else float("inf"))

    @staticmethod
    def quiet_processor():
        try:
            if hasattr(os, "getloadavg"):
                return os.getloadavg()[0] / max(1, os.cpu_count() or 1) < 0.35
            import psutil
            return psutil.cpu_percent(interval=1) < 30
        except Exception:
            return True

    def due(self):
        last = self.db.setting("explore_last")
        if not last:
            return True
        try:
            return age(last) > timedelta(hours=self._s("explore_every_hours", 24.0))
        except ValueError:
            return True

    def _local_models(self):
        """Local models whose file is there, the default model first, then the most recently used."""
        default = self.db.setting("default_model")
        rows = [m for m in self.db.all("SELECT * FROM models ORDER BY last_used DESC") if not is_cloud(m["id"])]
        rows.sort(key=lambda m: m["id"] != default)
        return [m for m in rows if m.get("path") and Path(m["path"]).exists()]

    def pick(self):
        """(what, model): a setup to measure (never measured, or not for explore_retune_days), else a model to
        evaluate (never evaluated; at most every explore_evaluate_days), else (None, None)."""
        days = self._s("explore_retune_days", 30.0)
        models = self._local_models()
        for m in models:
            tune = (m.get("bench") or {}).get("tune") or {}
            try:
                fresh = bool(tune.get("at")) and age(tune["at"]) < timedelta(days=days)
            except ValueError:
                fresh = False
            if not fresh:
                return "fastest setup", m
        if not self.db.setting("explore_evaluate", True):
            return None, None
        last = self.db.setting("explore_last_evaluation")
        try:
            if last and age(last) < timedelta(days=self._s("explore_evaluate_days", 7.0)):
                return None, None
        except ValueError:
            pass
        evaluated = {r["model"] for r in self.db.all("SELECT DISTINCT model FROM eval_runs WHERE status='done'")}
        for m in models:
            if m["id"] not in evaluated:
                return "evaluation", m
        return None, None

    def check(self):
        """(model, None) when it may explore now, else (None, the reason it does not)."""
        from .hardware import power
        if not self.enabled():
            return None, "switched off"
        if self.current:
            return None, "measuring now"
        why = self.busy()
        if why:
            return None, why
        idle, need = self.idle_minutes(), self._s("explore_idle_minutes", 30.0)
        if idle < need:
            return None, f"waiting for {need:.0f} idle minutes ({idle:.0f} so far)"
        if not power().get("on_ac", True):
            return None, "on battery"
        if not self.due():
            return None, "already explored in the last day"
        if not self.quiet_processor():
            return None, "the processor is busy with other programs"
        what, model = self.pick()
        if not model:
            return None, "every local model is measured and evaluated"
        return {**model, "_what": what}, None

    # ------------------------------------------------------------------ running
    async def loop(self):
        while True:
            await asyncio.sleep(CHECK_SECONDS)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:                    # exploring must never disturb the engine
                self.reason = f"error: {type(exc).__name__}"
                self.app.log(f"explore: {type(exc).__name__}: {exc}")

    async def tick(self):
        model, why = self.check()
        self.reason = why or "starting"
        if not model:
            return None
        return await self.explore(model)

    async def explore(self, model):
        model_id, what = model["id"], model.get("_what") or "fastest setup"
        self.current = {"model_id": model_id, "what": what, "started": now()}
        self.db.set_setting("explore_last", now())
        self.app.bus.publish("explore", state="running", model_id=model_id, what=what)
        self.app.log(f"explore: {what} of {model_id} while idle")
        entry = {"at": now(), "model_id": model_id, "what": what}
        try:
            if what == "evaluation":
                await self._evaluate(model_id, entry)
            else:
                await self._tune(model_id, entry)
        finally:
            self.current = None
            history = (self.db.setting("explore_history") or [])[-(HISTORY - 1):]
            self.db.set_setting("explore_history", history + [entry])
            self.app.bus.publish("explore", state="done", **{k: v for k, v in entry.items() if k != "at"})
            self.app.log(f"explore: {model_id}: {entry.get('result')}"
                         + (f", stopped ({entry['stopped']})" if entry.get("stopped") else ""))
        return entry

    async def _tune(self, model_id, entry):
        self.app.models.tune(model_id, self.app.llm)
        while True:
            await asyncio.sleep(5)
            state = (self.app.models.tunes.get(model_id) or {}).get("state")
            if state != "running":
                break
            why = self._interrupted()
            if why:
                self.app.models.stop_tune(model_id)
                entry["stopped"] = why
                break
        result = self.app.models.tunes.get(model_id) or {}
        entry.update(result=result.get("state"), best=result.get("best"))

    async def _evaluate(self, model_id, entry):
        self.db.set_setting("explore_last_evaluation", now())
        run = self.app.evals.start(model=model_id)
        entry["eval_id"] = run["id"]
        while True:
            await asyncio.sleep(10)
            row = self.db.get("eval_runs", run["id"]) or {}
            if row.get("status") != "running":
                break
            why = self._interrupted(own_eval=True)
            if why:
                self.app.jobs.cancel(run["job_id"])
                entry["stopped"] = why
                break
        row = self.db.get("eval_runs", run["id"]) or {}
        summary = row.get("summary") or {}
        entry.update(result=row.get("status"), passed=summary.get("passed"), of=summary.get("total"))

    def _interrupted(self, own_eval=False):
        """Something the user started while measuring (the measuring itself uses the model, so model use is not
        counted here; nor, for an evaluation exploring started, the evaluation's own jobs)."""
        if own_eval:
            n = (self.db.one("SELECT COUNT(*) AS n FROM jobs j LEFT JOIN chats c ON c.id = j.chat_id "
                             "WHERE j.status IN ('queued','running','waiting') AND j.kind != 'eval' "
                             "AND COALESCE(c.origin, '') != 'eval'") or {}).get("n", 0)
        else:
            n = (self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running','waiting')") or {}).get("n", 0)
        if n:
            return "a chat or task started"
        if not own_eval and getattr(self.app, "evals", None) and self.app.evals.current:
            return "an evaluation started"
        desk = self.desktop_idle_minutes()
        if desk is not None and desk < 1:
            return "someone is using the computer"
        from .hardware import power
        if not power().get("on_ac", True):
            return "unplugged"
        return None

    def status(self):
        return {"enabled": self.enabled(), "current": self.current, "reason": self.reason,
                "last": self.db.setting("explore_last"), "history": self.db.setting("explore_history") or []}
