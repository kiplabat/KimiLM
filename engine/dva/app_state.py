"""The engine's services wired together (one instance per running app)."""
import asyncio
import time
from datetime import datetime

from .cloud import Cloud
from .db import DB, new_id, now
from .embed import Embedder
from .events import EventBus
from .forecast import Forecaster
from .harness import Harnesses
from .jobs import Jobs
from .knowledge import Knowledge
from .llm import LLM
from .mcp import Connectors
from .memory import Memory
from .migration import rewrite_moved_paths
from .models import Models
from .paths import Paths
from .plugins import Plugins
from .providers import Providers
from .router import Router
from .runtime import Runtime
from .scheduler import Scheduler
from .secrets import Secrets
from .services import Services
from .skills import Skills

STATE = None

DEFAULT_SETTINGS = {
    "default_harness": "agent", "default_compute": "auto", "default_ctx": 8192, "max_reply_tokens": 2048,
    "unload_after_minutes": 20, "memory_enabled": True, "learn_enabled": True, "learn_delay_seconds": 45,
    "allow_python": True, "approval_timeout_minutes": 360, "max_native_tools": 28, "python_timeout_seconds": 180,
    "max_loaded_models": 3, "browser_search": True, "max_parallel_agents": 4, "turn_time_limit_minutes": 30,
    "cloud_privacy": "ask", "cloud_budget_monthly": 10.0, "cloud_max_parallel": 4,
    "team_mode": "auto", "max_team_tasks": 8, "team_time_limit_minutes": 120,
    # 2.5.0: sequenced, looped runs. agent_order: sequence | parallel; step_rounds: tries per step before the best
    # attempt is kept; fit_context: load a run's models with the context its steps need
    "agent_order": "sequence", "step_rounds": 3, "verify_results": True, "plan_approval": False,
    "fit_context": True, "unload_after_run": False, "stall_timeout_seconds": 180, "first_token_timeout_seconds": 0,
    # 2.6.0: doing less work. prompt_snapshots: keep what a model read of its instructions and tools on disk;
    # prompt_cache_mb: memory per loaded model for prompts already read (None = about 5% of memory);
    # reading_budget_seconds: a tool result is cut to about this much reading at the measured speed (0 = fixed)
    "prompt_snapshots": True, "snapshot_disk_mb": 4096, "prompt_cache_mb": None, "reading_budget_seconds": 60,
    # 2.7.0: resume_runs: ask | auto (continue cut-off runs when the app starts); lean_tools: a step's agent gets
    # only the tool groups its goal calls for; escalate_models: a step that keeps failing moves to a stronger
    # model; check_model: "" (the planner's) | smallest | a model id; writing_budget_seconds: result length hint
    "resume_runs": "ask", "lean_tools": True, "escalate_models": True, "check_model": "",
    "writing_budget_seconds": 180,
    # 2.8.0: learned_model_choice: a step whose model nobody fixed runs on the model that did such steps best here
    "learned_model_choice": True, "runs_kept": 300,
}
CHAT_LIMIT_KEYS = ("max_reply_tokens", "max_steps", "time_limit_minutes", "temperature", "max_searches",
                   "max_tool_calls")


class App:
    def __init__(self, data_dir=None, resources_dir=None):
        global STATE
        STATE = self
        self.started = time.time()
        self.paths = Paths(data_dir, resources_dir)
        self.db = DB(self.paths.db)
        try:   # the data folder was moved from the app's earlier name: fix stored paths once
            rewrite_moved_paths(self.paths.data, self.db, self.log)
        except Exception as exc:
            self.log(f"rewriting moved paths failed: {type(exc).__name__}: {exc}")
        self._release_pending_after_restart()
        self.bus = EventBus()
        self.secrets = Secrets(self.paths.data)
        self.runtime = Runtime(self.paths, self.db, self.bus)
        self.llm = LLM(self.runtime)
        self.plugins = Plugins(self.paths, self.db, self.secrets, self.bus)
        self.skills = Skills(self.paths, self.db, self.plugins)
        self.harnesses = Harnesses(self.paths, self.db, self.plugins)
        self.connectors = Connectors(self.db, self.secrets, self.plugins, self.bus)
        self.models = Models(self.paths, self.db, self.bus, self.runtime)
        self.providers = Providers(self.db, self.secrets, self.bus, self.log)
        self.runtime.cloud = Cloud(self.providers, self.db)
        self.models.providers = self.providers
        self.router = Router(self)
        self.memory = Memory(self)
        self.embedder = Embedder(self)
        self.knowledge = Knowledge(self)
        self.jobs = Jobs(self)
        self.scheduler = Scheduler(self)
        self.services = Services(self)
        self.approvals = {}
        self.pauses = {}          # chat id -> {"event", "reason"}: paused agent loops (orchestrator.pause_chat)
        self.orchestras = {}      # chat id -> running Orchestra (a team led from that chat)
        self.team_of_chat = {}    # agent chat id -> (Orchestra, task id)
        self.run_fit = {}         # chat id -> orchestrator.RunFit: how a planned run loads its models
        self.forecaster = Forecaster(self)
        from .evals import Evals
        self.evals = Evals(self)
        self._bg = []
        for key, value in DEFAULT_SETTINGS.items():
            if self.db.setting(key) is None:
                self.db.set_setting(key, value)

    def _release_pending_after_restart(self):
        """Follow-ups queued while a reply was running are not sent if the app closed meanwhile: they show as
        "Not sent (the app was closed)" with Send now, instead of staying "Queued" forever."""
        rows = self.db.all("SELECT id, meta_json FROM messages WHERE role='user' AND meta_json LIKE '%\"pending\"%'")
        for row in rows:
            meta = row.get("meta") or {}
            if meta.get("pending"):
                meta.pop("pending", None)
                meta["not_sent"] = "restart"
                self.db.update("messages", row["id"], {"meta": meta})

    def spawn(self, coro):
        """Run in the background, keeping a reference (asyncio may otherwise drop a running task)."""
        task = asyncio.create_task(coro)
        self._spawned = getattr(self, "_spawned", set())
        self._spawned.add(task)
        task.add_done_callback(self._spawned.discard)
        return task

    def log(self, message):
        line = f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
        with (self.paths.data / "logs" / "engine.log").open("a", encoding="utf-8") as f:
            f.write(line)

    async def start(self):
        self.bus.loop = asyncio.get_running_loop()
        self._bg = [asyncio.create_task(self.scheduler.loop()), asyncio.create_task(self.runtime.idle_watch()),
                    asyncio.create_task(self.knowledge.loop())]
        self.log(f"engine started; data {self.paths.data}; secrets backend {self.secrets.backend}")
        # runs that were going when the app closed or crashed can be continued (2.7.0)
        cut = self.db.all("SELECT id, chat_id, resumes FROM runs WHERE status='running'")
        for row in cut:
            self.db.update("runs", row["id"], {"status": "interrupted", "updated": now()})
        if cut and self.db.setting("resume_runs", "ask") == "auto":
            self._bg.append(asyncio.create_task(self._resume_cut_runs(cut)))

    async def _resume_cut_runs(self, rows):
        await asyncio.sleep(float(self.db.setting("resume_delay_seconds", 20)))
        for row in rows:
            if int(row.get("resumes") or 0) >= 2:        # a run that keeps cutting the app off is left alone
                continue
            try:
                self.resume_run(row["chat_id"])
            except Exception as exc:
                self.log(f"continuing run {row['id']}: {type(exc).__name__}: {exc}")

    @staticmethod
    def _run_summary(row, title=None):
        state = row.get("state") or {}
        tasks = state.get("tasks") or []
        used = [t.get("used") or {} for t in tasks]
        return {"id": row["id"], "chat_id": row["chat_id"], "title": title, "status": row["status"],
                "created": row.get("created"), "updated": row.get("updated"), "resumes": row.get("resumes") or 0,
                "request": (state.get("overall") or "")[:240], "total": len(tasks),
                "done": sum(t.get("status") == "done" for t in tasks),
                "failed": sum(t.get("status") == "failed" for t in tasks),
                "incomplete": sum(bool(t.get("partial")) for t in tasks),
                "rounds": sum(int(t.get("attempts") or 0) for t in tasks),
                "tokens": sum(int(u.get("tokens") or 0) for u in used),
                "read": sum(int(u.get("read") or 0) for u in used),
                "cached": sum(int(u.get("cached") or 0) for u in used),
                "written": sum(int(u.get("written") or 0) for u in used),
                "seconds": sum(int(u.get("seconds") or 0) for u in used),
                "models": sorted({t.get("model") for t in tasks if t.get("model")})}

    def runs(self, limit=60):
        """The history of planned runs, newest first."""
        rows = self.db.all("SELECT r.*, c.title AS chat_title FROM runs r LEFT JOIN chats c ON c.id = r.chat_id "
                           "ORDER BY r.created DESC LIMIT ?", (limit,))
        return [self._run_summary(r, r.get("chat_title")) for r in rows]

    def run_detail(self, run_id):
        """One run with the timeline of every step."""
        row = self.db.get("runs", run_id)
        if not row:
            return None
        chat = self.db.get("chats", row["chat_id"]) or {}
        state = row.get("state") or {}
        steps = []
        for t in state.get("tasks") or []:
            model = self.models.get_any(t.get("model")) if t.get("model") else None
            steps.append({k: t.get(k) for k in ("id", "name", "status", "attempts", "rounds", "depends_on", "harness",
                                                "model", "partial", "note", "kit", "chat_id", "result_file", "forecast",
                                                "used", "kind")}
                         | {"goal": (t.get("goal") or "")[:500], "output": (t.get("output") or "")[:1500],
                            "model_name": (model or {}).get("name") or t.get("model")})
        return {**self._run_summary(row, chat.get("title")), "steps": steps, "log": state.get("log") or [],
                "folder": state.get("dir"), "can_continue": bool(self.open_run(row["chat_id"]))
                and (self.open_run(row["chat_id"]) or {}).get("id") == run_id}

    def open_run(self, chat_id):
        """The chat's latest run if it can be continued (stopped, or cut off by a crash or restart)."""
        row = self.db.one("SELECT * FROM runs WHERE chat_id=? ORDER BY created DESC, rowid DESC LIMIT 1", (chat_id,))
        if not row or row["status"] not in ("interrupted", "stopped") or self.jobs.running_for_chat(chat_id):
            return None
        tasks = (row.get("state") or {}).get("tasks") or []
        left = [t for t in tasks if t.get("status") not in ("done", "failed")]
        if not tasks or not left:
            return None
        return {"id": row["id"], "status": row["status"], "total": len(tasks),
                "done": sum(t.get("status") == "done" for t in tasks), "updated": row.get("updated"),
                "next": next((t.get("name") for t in tasks if t.get("status") not in ("done", "failed", "cancelled")),
                             left[0].get("name"))}

    def resume_run(self, chat_id):
        """Continue the chat's cut-off run from its accepted steps. Returns the new job."""
        info = self.open_run(chat_id)
        if not info:
            raise ValueError("There is no run to continue in this chat.")
        last = self.db.one("SELECT id, content FROM messages WHERE chat_id=? AND role='user' "
                           "ORDER BY created DESC, rowid DESC LIMIT 1", (chat_id,))
        if not last:
            raise ValueError("The request this run belongs to is gone.")
        auto = bool(self.db.setting(f"chat_auto_approve:{chat_id}", False))
        return self.start_turn(chat_id, last["content"], message_id=last["id"],
                               payload={"resume_run": info["id"], "auto_approve": auto})

    def dismiss_run(self, chat_id):
        self.db.run("UPDATE runs SET status='dismissed' WHERE chat_id=? AND status IN ('interrupted','stopped')",
                        (chat_id,))

    async def stop(self):
        for t in self._bg:
            t.cancel()
        await self.jobs.shutdown()
        await self.connectors.shutdown()
        await self.services.shutdown()
        await self.embedder.stop()
        await self.runtime.shutdown()
        self.log("engine stopped")

    # ------------------------------------------------------------------ chats
    def create_chat(self, project_id=None, title="", model=None, harness=None, connectors=None, folders=None,
                    origin="user", schedule_id=None, thinking=False, settings=None, parent_chat_id=None,
                    agent_name=None):
        project = self.db.get("projects", project_id) if project_id else None
        chat_id = new_id("chat_")
        self.db.insert("chats", {
            "id": chat_id, "project_id": project_id, "title": title,
            "model": model or (project or {}).get("default_model") or None,
            "harness": harness or (project or {}).get("default_harness") or None,
            "connectors": connectors if connectors is not None else ((project or {}).get("connectors") or []),
            "folders": folders or [], "origin": origin, "schedule_id": schedule_id, "thinking": int(bool(thinking)),
            "settings": clean_limits(settings), "parent_chat_id": parent_chat_id, "agent_name": agent_name,
            "created": now(), "updated": now()})
        self.bus.publish("chats_changed", project_id=project_id)
        return self.db.get("chats", chat_id)

    def start_turn(self, chat_id, text, attachments=None, kind="chat", schedule_id=None, notify=False, payload=None,
                   message_id=None):
        """Start a reply. With message_id, the user message is already saved (queued follow-ups)."""
        from .runner import Turn
        chat = self.db.get("chats", chat_id)
        if not chat:
            raise KeyError(chat_id)
        if self.jobs.running_for_chat(chat_id):
            raise ValueError("This chat is still working on the previous message. Wait, or press Stop.")
        values = {"updated": now()}
        if not message_id:
            message_id = new_id("msg_")
            self.db.insert("messages", {"id": message_id, "chat_id": chat_id, "role": "user", "content": text,
                                        "meta": {"attachments": attachments or []}, "created": now()})
            self.bus.publish("chat", chat_id=chat_id, type="user_message", message_id=message_id, content=text,
                             meta={"attachments": attachments or []})
        if not chat.get("title"):
            values["title"] = " ".join(text.split())[:70] or "New chat"
        self.db.update("chats", chat_id, values)
        self.bus.publish("chats_changed", project_id=chat.get("project_id"))
        harness = chat.get("harness") or self.harnesses.default_id()
        title = (values.get("title") or chat.get("title") or "Chat")
        return self.jobs.create(kind, title, lambda row: Turn(self, chat_id, row).run(), chat_id=chat_id,
                                schedule_id=schedule_id, harness=harness, model=chat.get("model"),
                                payload=payload or {}, notify=notify,
                                on_done=lambda status: self.after_turn(chat_id, status))

    # ------------------------------------------------------------------ follow-ups while a reply is being written
    def post_message(self, chat_id, text, attachments=None, mode="queue"):
        """Send a message. If the chat is busy: 'queue' runs it right after the current reply; 'steer' hands it to
        the running agent at its next step (if the reply ends before that, it is queued instead)."""
        if not self.jobs.running_for_chat(chat_id):
            job = self.start_turn(chat_id, text, attachments)
            return {"job_id": job["id"], "state": "started"}
        mode = "steer" if mode == "steer" else "queue"
        msg_id = new_id("msg_")
        meta = {"attachments": attachments or [], "pending": mode}
        self.db.insert("messages", {"id": msg_id, "chat_id": chat_id, "role": "user", "content": text,
                                    "meta": meta, "created": now()})
        self.bus.publish("chat", chat_id=chat_id, type="user_message", message_id=msg_id, content=text, meta=meta)
        return {"message_id": msg_id, "state": mode}

    def pending_messages(self, chat_id, mode=None):
        rows = self.db.all("SELECT * FROM messages WHERE chat_id=? AND role='user' AND meta_json LIKE '%\"pending\"%' "
                           "ORDER BY created, rowid", (chat_id,))
        return [r for r in rows if (r.get("meta") or {}).get("pending") and
                (mode is None or r["meta"]["pending"] == mode)]

    def mark_message(self, row, **changes):
        meta = {k: v for k, v in {**(row.get("meta") or {}), **changes}.items() if v is not None}
        values = {"meta": meta}
        if changes.get("pending") is None and "created_now" in changes:
            meta.pop("created_now", None)
            values["created"] = now()
        self.db.update("messages", row["id"], values)
        self.bus.publish("chat", chat_id=row["chat_id"], type="message_meta", message_id=row["id"], meta=meta)

    def after_turn(self, chat_id, status):
        """Run queued follow-ups (together, as one new turn). After Stop they are kept but not sent."""
        pending = self.pending_messages(chat_id)
        if not pending:
            return
        if status == "cancelled":
            for row in pending:
                self.mark_message(row, pending=None, not_sent=True)
            return
        for row in pending:  # move them after the reply that was being written, keep their order
            self.mark_message(row, pending=None, created_now=True)
        last = pending[-1]
        self.start_turn(chat_id, last["content"], message_id=last["id"])

    def resend(self, chat_id, message_id):
        row = self.db.get("messages", message_id)
        if not row or row["chat_id"] != chat_id:
            raise KeyError(message_id)
        self.db.delete("messages", message_id)
        self.bus.publish("chat", chat_id=chat_id, type="message_deleted", message_id=message_id)
        return self.post_message(chat_id, row["content"], (row.get("meta") or {}).get("attachments"))


def clean_limits(values):
    """Per-chat / per-agent limits; anything empty means 'use the harness or app default'."""
    out = {}
    for key in CHAT_LIMIT_KEYS:
        value = (values or {}).get(key)
        if value in (None, "", 0, "0"):
            continue
        try:
            out[key] = float(value) if key == "temperature" else int(value)
        except (TypeError, ValueError):
            continue
    for key in ("tools",):
        if isinstance((values or {}).get(key), list):
            out[key] = [str(x) for x in values[key]]
    return out
