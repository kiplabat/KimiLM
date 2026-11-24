"""The engine's services wired together (one instance per running app)."""
import asyncio
import os
import time
from datetime import datetime

from .bridge import Bridge
from .cloud import USAGE, Cloud
from .db import DB, new_id, now
from .embed import Embedder
from .events import EventBus
from .forecast import Forecaster
from .harness import Harnesses
from .images import Images
from .jobs import Jobs
from .knowledge import Knowledge
from .llm import LLM
from .mcp import Connectors
from .memory import Memory
from .meter import Meter
from .models import Models
from .procs import Procs, contain
from .paths import Paths
from .plugins import Plugins
from .providers import Providers
from .router import Router
from .runtime import Runtime
from .scheduler import Scheduler
from .secrets import Secrets
from .services import Services
from .devices import Devices
from .skills import Skills
from .sound import Sound
from .telemetry import Telemetry
from .video import Video
from .voices import Voices

STATE = None

DEFAULT_SETTINGS = {
    "default_harness": "agent", "default_compute": "auto", "default_ctx": 8192, "max_reply_tokens": 2048,
    "unload_after_minutes": 20, "engine_memory_percent": 85, "explore_when_idle": True, "explore_idle_minutes": 30, "explore_evaluate": True, "suggestions_enabled": False, "memory_enabled": True, "learn_enabled": True, "learn_delay_seconds": 45,
    "allow_python": True, "approval_timeout_minutes": 360, "max_native_tools": 50, "python_timeout_seconds": 180,
    "apps_preview": True,              # 2.35.0: a saved app is tried for a few seconds in a headless browser
    # 2.19.0: the most a tool call may ask for a program's time, and how many programs agents may run at once
    "code_timeout_max_seconds": 900, "code_runs_at_once": 2,
    "max_loaded_models": 3, "browser_search": True, "max_parallel_agents": 4, "max_team_depth": 2, "turn_time_limit_minutes": 30,
    "cloud_privacy": "ask", "cloud_budget_monthly": 10.0, "cloud_max_parallel": 4,
    "team_mode": "auto", "max_team_tasks": 8, "team_time_limit_minutes": 120,
    # 2.5.0: sequenced, looped runs. agent_order: sequence | parallel; step_rounds: tries per step before the best
    # attempt is kept; fit_context: load a run's models with the context its steps need
    "agent_order": "auto",
    # 2.32.0: choose_step_models: a planner gives each step the model that measured best at its kind of work
    # (Check this setup); planner_model: decide (the planner may hand planning to a model that planned better)
    # | best (always the best measured planner) | default (the chat's own model plans)
    "choose_step_models": True, "planner_model": "decide", "snapshot_before_changes": True, "step_rounds": 3, "verify_results": True, "plan_approval": False,
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
    # 2.9.0: long-running tasks. Default limits of a new task (hours of work, steps, minutes one step may take);
    # mission_auto_resume: a task that was cut off by a restart or a crash continues by itself at start
    "mission_hours": 8, "mission_steps": 40, "mission_step_minutes": 45, "mission_auto_resume": True,
    # 2.11.0: pictures. image_compute: auto | cpu | vulkan | cuda | metal (which build of the image engine works);
    # image_timeout_minutes: one request on this computer is stopped after this long
    "image_compute": "auto", "image_timeout_minutes": 60,
    # 2.13.0: music on this computer. music_engine_dir: a folder with ace-lm and ace-synth, if the user has one
    "music_engine_dir": "", "music_timeout_minutes": 90,
    # 2.14.0: video. video_online: Google's video model may be used (it costs money); video_clip_cost: what a clip
    # is counted as against the online budget (KimiLM does not know the provider's price); video_timeout_minutes:
    # a clip made by a model on this computer is stopped after this long
    "video_online": False, "video_online_model": "gemini-omni-1.1-flash", "video_clip_cost": 1.0,
    "video_timeout_minutes": 240,
    # 2.17.0: pictures and video can be paused. pause_stop_minutes: a paused one is stopped after this long, so
    # that the memory its engine holds is free again (what was finished is kept and it can be continued); 0: never
    "pause_stop_minutes": 20,
    # 2.15.0: failure recovery. recovery_attempts: after this many different recoveries in a row have failed too,
    # a long-running task pauses and says what was tried; retry_wait_seconds: the first wait before a step that
    # met a passing fault (a dropped connection, a busy service) is given again (the next wait is three times it)
    "recovery_attempts": 3, "retry_wait_seconds": 15,
    # 2.20.0: fail fast. fail_fast_target_seconds: the most seconds from a failure written to the run's register to
    # the planner's decision (model calls not counted), shown against the measures of a run; connector_retry_seconds:
    # the wait before a service that could not be reached is tried once more (after that an agent of a run stops)
    "fail_fast_target_seconds": 10, "connector_retry_seconds": 3,
    # 2.16.0: browsing. browser_consent: how a cookie banner is answered without asking: necessary (the choice
    # that allows least, else accept) | accept | ask (left to the agent); browser_save_mb: the largest file
    # browser_save fetches through the browser
    "browser_consent": "necessary", "browser_save_mb": 300,
    # 2.29.0: unattended_auto_approve: scheduled and long-running tasks (and their agents) approve their own actions;
    # thinking_default: new chats and every agent a planner starts think before they answer; install_programs:
    # agents may install a program they need (install_program); notify_done_seconds: a reply that took longer than
    # this tells you when it is done (0: never); notify_sound: a short sound when something needs you;
    # devices_watch: attached devices are watched and their arrival is announced
    "unattended_auto_approve": True, "thinking_default": True, "install_programs": True,
    "notify_done_seconds": 20, "notify_sound": True, "devices_watch": True,
}
CHAT_LIMIT_KEYS = ("max_reply_tokens", "max_steps", "time_limit_minutes", "temperature", "max_searches",
                   "max_tool_calls", "ctx")


class App:
    def __init__(self, data_dir=None, resources_dir=None):
        global STATE
        STATE = self
        self.started = time.time()
        self.paths = Paths(data_dir, resources_dir)
        self.db = DB(self.paths.db)
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
        # 2.27.0: every model call is counted (Usage), and the computer is watched second by second (Performance)
        self.meter = Meter(self.db, USAGE, self.log)
        self.runtime.meter = self.runtime.cloud.meter = self.meter
        self.telemetry = Telemetry(self.runtime, self.db, self.log)
        self.runtime.telemetry = self.telemetry
        self.models.providers = self.providers
        self.router = Router(self)
        self.memory = Memory(self)
        self.embedder = Embedder(self)
        self.knowledge = Knowledge(self)
        self.jobs = Jobs(self)
        self.models.jobs = self.jobs          # downloads are tasks too
        self.scheduler = Scheduler(self)
        self.services = Services(self)
        self.devices = Devices(self)                 # what is plugged in (2.29.0)
        from .apps import Apps
        self.apps = Apps(self)                       # device panels and games a model builds (2.34.0)
        self.images = Images(self)
        self.sound = Sound(self)
        self.voices = Voices(self)                   # your own voice: takes, checks, the reference (2.18.0)
        # the programs agents run, and code's way to their connectors (2.19.0)
        self.procs = Procs(self)
        self.bridge = Bridge(self)
        self.video = Video(self)
        self.approvals = {}
        self.pauses = {}          # chat id -> {"event", "reason"}: paused agent loops (orchestrator.pause_chat)
        self.orchestras = {}      # chat id -> running Orchestra (a team led from that chat)
        self.team_of_chat = {}    # agent chat id -> (Orchestra, task id)
        self.run_fit = {}         # chat id -> orchestrator.RunFit: how a planned run loads its models
        self.forecaster = Forecaster(self)
        from .evals import Evals
        self.evals = Evals(self)
        from .explore import Explorer
        self.explorer = Explorer(self)          # measuring better setups while idle (2.24.0)
        from .suggest import Suggestions
        self.suggestions = Suggestions(self)    # better ways of working, opt-in (2.24.0)
        self._bg = []
        for key, value in DEFAULT_SETTINGS.items():
            if self.db.setting(key) is None:
                self.db.set_setting(key, value)
        # 2.9.0 adds seven browser tools. The earlier default of 28 tools would push every connector's tools
        # behind the find/use pair, so the default moves with the tool list (a value the user chose is kept).
        if self.db.setting("max_native_tools") == 28 and not self.db.setting("native_tools_2_9"):
            self.db.set_setting("max_native_tools", 36)
        self.db.set_setting("native_tools_2_9", True)
        # 2.11.0 adds two picture tools (offered once an image model is set up): the default moves again.
        if self.db.setting("max_native_tools") == 36 and not self.db.setting("native_tools_2_11"):
            self.db.set_setting("max_native_tools", 38)
        self.db.set_setting("native_tools_2_11", True)
        # 2.12.0 adds up to five sound tools (offered once sound models are set up).
        if self.db.setting("max_native_tools") == 38 and not self.db.setting("native_tools_2_12"):
            self.db.set_setting("max_native_tools", 44)
        self.db.set_setting("native_tools_2_12", True)
        # 2.14.0 adds up to three video tools (two always, the clip tool once a video model is set up).
        if self.db.setting("max_native_tools") == 44 and not self.db.setting("native_tools_2_14"):
            self.db.set_setting("max_native_tools", 47)
        self.db.set_setting("native_tools_2_14", True)
        # 2.31.0: steps that do not need each other run at the same time (the operator asked for it): a stored
        # "sequence" from before becomes "auto" once; choosing "sequence" again later is kept
        if self.db.setting("agent_order") == "sequence" and not self.db.setting("agent_order_2_31"):
            self.db.set_setting("agent_order", "auto")
        self.db.set_setting("agent_order_2_31", True)
        # 2.29.0 adds two tools every agent has (install_program, devices)
        if self.db.setting("max_native_tools") == 47 and not self.db.setting("native_tools_2_29"):
            self.db.set_setting("max_native_tools", 49)
        self.db.set_setting("native_tools_2_29", True)
        # 2.35.0 adds build_app (automation: a game or a control panel from any chat)
        if self.db.setting("max_native_tools") == 49 and not self.db.setting("native_tools_2_35"):
            self.db.set_setting("max_native_tools", 50)
        self.db.set_setting("native_tools_2_35", True)

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

    def memory_limit(self):
        """Bytes the engine and its programs may commit together on Windows (2.23.0): engine_memory_percent of the
        computer's memory, 85 by default as on KimiOS; 0 turns the limit off. Read once, at start."""
        import psutil
        try:
            percent = float(self.db.setting("engine_memory_percent", 85) or 0)
        except (TypeError, ValueError):
            percent = 85
        return int(psutil.virtual_memory().total * percent / 100) if 0 < percent <= 100 else 0

    def log(self, message):
        line = f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
        with (self.paths.data / "logs" / "engine.log").open("a", encoding="utf-8") as f:
            f.write(line)

    async def start(self):
        self.bus.loop = asyncio.get_running_loop()
        # 2.19.0: nothing this engine starts outlives it (Windows: a job object), and what an engine before it
        # left running is ended now
        self.procs.job = contain(self.memory_limit())
        await asyncio.to_thread(self.procs.end_leftovers)
        self._bg = [asyncio.create_task(self.scheduler.loop()), asyncio.create_task(self.runtime.idle_watch()),
                    asyncio.create_task(self.knowledge.loop()), asyncio.create_task(self.explorer.loop())]
        await asyncio.to_thread(self.telemetry.start)
        self._bg.append(asyncio.create_task(self.telemetry.watch(
            self.bus, lambda mid: (self.models.get_any(mid) or {}).get("name") or str(mid).split(":")[-1])))
        self._bg.append(asyncio.create_task(self.devices._watch()))     # 2.29.0: devices coming and going
        try:
            await asyncio.to_thread(self.apps.seed)                     # 2.34.0: the example games, once
            if not os.environ.get("KIMILM_NO_APP_PREVIEW_ON_START"):     # 2.36.0: their pictures, a little later
                self._bg.append(asyncio.create_task(self.apps.preview_examples()))
        except Exception as exc:                                        # noqa: BLE001 — never stops the start
            self.log(f"apps: the examples could not be added: {exc}")
        self.log(f"engine started; data {self.paths.data}; secrets backend {self.secrets.backend}")
        # runs that were going when the app closed or crashed can be continued (2.7.0)
        cut = self.db.all("SELECT id, chat_id, resumes, kind FROM runs WHERE status='running'")
        for row in cut:
            self.db.update("runs", row["id"], {"status": "interrupted", "updated": now()})
        # a planned run continues by itself only if the user asked for that; a long-running task does unless
        # the user switched it off (2.9.0): it is meant to keep going until it is done
        again = [r for r in cut if (self.db.setting("mission_auto_resume", True) if r.get("kind") == "mission"
                                    else self.db.setting("resume_runs", "ask") == "auto")]
        if again:
            self._bg.append(asyncio.create_task(self._resume_cut_runs(again)))

    async def _resume_cut_runs(self, rows):
        await asyncio.sleep(float(self.db.setting("resume_delay_seconds", 20)))
        for row in rows:
            try:
                if row.get("kind") == "mission":
                    # counted since the last accepted step, so a task that works for days may be continued after
                    # every restart, while one that cuts the app off again and again is left alone
                    full = self.db.get("runs", row["id"]) or {}
                    state = full.get("state") or {}
                    mission = state.get("mission") or {}
                    if int(mission.get("auto_resumes") or 0) >= 3:
                        continue
                    mission["auto_resumes"] = int(mission.get("auto_resumes") or 0) + 1
                    self.db.update("runs", row["id"], {"state": {**state, "mission": mission}})
                elif int(row.get("resumes") or 0) >= 2:  # a run that keeps cutting the app off is left alone
                    continue
                self.resume_run(row["chat_id"])
            except Exception as exc:
                self.log(f"continuing run {row['id']}: {type(exc).__name__}: {exc}")

    @staticmethod
    def _run_summary(row, title=None):
        state = row.get("state") or {}
        tasks = state.get("tasks") or []
        used = [t.get("used") or {} for t in tasks]
        return {"id": row["id"], "chat_id": row["chat_id"], "title": title, "status": row["status"],
                "kind": row.get("kind") or "run", "created": row.get("created"), "updated": row.get("updated"), "resumes": row.get("resumes") or 0,
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

    def effective_settings(self, chat):
        """What a chat runs with (its pickers show it): as the last reply ran, else what the next one would use."""
        from .runner import describe_settings
        project = self.db.get("projects", chat["project_id"]) if chat.get("project_id") else None
        hid = chat.get("harness") or (project or {}).get("default_harness") or self.harnesses.default_id()
        harness = self.harnesses.get(hid) or self.harnesses.get("agent") or {}
        model = chat.get("model") or (project or {}).get("default_model") or self.models.default_model()
        last = self.db.setting(f"chat_effective:{chat['id']}")
        if last and last.get("harness_id") == harness.get("id") and \
                (last.get("auto") if model in (None, "", "auto") else last.get("model") == model):
            fresh = describe_settings(self, harness, last.get("model"), chat.get("settings") or {},
                                      auto=last.get("auto"), route_reason=last.get("route_reason"))
            return {**fresh, "ctx": last.get("ctx", fresh.get("ctx")), "ctx_by": last.get("ctx_by", fresh.get("ctx_by"))} \
                if not (chat.get("settings") or {}).get("ctx") else fresh
        return describe_settings(self, harness, None if model in (None, "", "auto") else model,
                                 chat.get("settings") or {}, auto=model in (None, "", "auto"))

    # ------------------------------------------------------------------ who is using a model (2.26.0)
    def holder_jobs(self, model_id=None):
        """The tasks (Activity rows) using a model right now: [(job id or None, asyncio task)]."""
        by_task = {t: jid for jid, t in self.jobs.tasks.items()}
        return [(by_task.get(t), t) for t in self.runtime.holders_of(model_id)]

    def loaded_with_holders(self):
        out = []
        for row in self.runtime.loaded():
            names = []
            for jid, _task in self.holder_jobs(row["model_id"]):
                job = self.db.get("jobs", jid) if jid else None
                names.append((job or {}).get("title") or "background work")
            out.append({**row, "held_by": names})
        return out

    def stop_holders(self, model_id=None):
        """Stop whatever is using a model so it can be unloaded. Returns what was stopped."""
        stopped = []
        for jid, task in self.holder_jobs(model_id):
            if jid:
                job = self.db.get("jobs", jid) or {}
                if job.get("chat_id"):
                    resume_paused = self.pauses.get(job["chat_id"])
                    if resume_paused:                  # a paused reply must wake to see that it was stopped
                        resume_paused["event"].set()
                chat = self.db.get("chats", job["chat_id"]) if job.get("chat_id") else None
                if (chat or {}).get("origin") == "eval" and (self.evals.current or {}).get("job_id"):
                    self.jobs.cancel(self.evals.current["job_id"])   # else the evaluation loads it for its next task
                    stopped.append("Evaluation")
                self.jobs.cancel(jid)
                stopped.append(job.get("title") or jid)
            else:
                task.cancel()
                stopped.append("background work")
        return stopped

    def open_run(self, chat_id):
        """The chat's latest run if it can be continued (stopped, or cut off by a crash or restart)."""
        row = self.db.one("SELECT * FROM runs WHERE chat_id=? ORDER BY created DESC, rowid DESC LIMIT 1", (chat_id,))
        if not row or row["status"] not in ("interrupted", "stopped", "paused") or self.jobs.running_for_chat(chat_id):
            return None
        tasks = (row.get("state") or {}).get("tasks") or []
        if row.get("kind") == "mission":
            # a long-running task is open until it is finished, also between two steps (2.9.0)
            m = (row.get("state") or {}).get("mission") or {}
            return {"id": row["id"], "status": row["status"], "mission": True, "total": len(tasks),
                    "done": sum(t.get("status") == "done" for t in tasks), "updated": row.get("updated"),
                    "pause": m.get("pause"), "limits": m.get("limits"), "spent": m.get("spent"),
                    "next": next((t.get("name") for t in tasks if t.get("status") not in
                                  ("done", "failed", "cancelled")), None)}
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
        self.db.run("UPDATE runs SET status='dismissed' WHERE chat_id=? AND status IN "
                    "('interrupted','stopped','paused')", (chat_id,))

    # ------------------------------------------------------------------ long-running tasks (2.9.0)
    def missions(self, limit=80):
        from .missions import summary
        rows = self.db.all("SELECT * FROM runs WHERE kind='mission' AND status NOT IN ('superseded','dismissed') "
                           "ORDER BY created DESC LIMIT ?", (limit,))
        return [summary(self, r) for r in rows]

    def start_mission(self, text, setup=None, project_id=None, model=None, folders=None, connectors=None,
                      origin="user", schedule_id=None, chat_id=None, kind="chat", notify=False, reason=None):
        """Start a long-running task in a chat of its own (or in `chat_id`). The set-up's permissions become
        the chat's unattended policy, so its agents may do those things without asking and nothing else."""
        from .missions import clean_setup
        setup = clean_setup(setup, self)
        text = (text or setup["goal"]).strip()
        if not text:
            raise ValueError("Describe the task first")
        if not connectors:                     # none chosen: every connector that can be used with nobody there
            project = self.db.get("projects", project_id) if project_id else None
            connectors = list((project or {}).get("connectors") or []) or self.connectors.usable()
        if not chat_id:
            chat = self.create_chat(project_id=project_id, title=setup["name"] or " ".join(text.split())[:70],
                                    model=model or None, harness="long-task", connectors=connectors,
                                    folders=folders, origin=origin, schedule_id=schedule_id)
            chat_id = chat["id"]
        elif (self.db.get("chats", chat_id) or {}).get("harness") != "long-task":
            self.db.update("chats", chat_id, {"harness": "long-task"})
        self.db.set_setting(f"chat_policy:{chat_id}", {"allow": setup["allow"]} if setup["allow"] else None)
        # later sessions of this chat keep the team, the limits and the permissions (not the task's wording)
        self.db.set_setting(f"chat_mission:{chat_id}", {k: setup[k] for k in ("name", "team", "limits", "allow")})
        payload = {"mission": setup, "policy": {"allow": setup["allow"]} if setup["allow"] else None}
        if reason:
            payload["reason"] = reason
        return self.start_turn(chat_id, text, kind=kind, schedule_id=schedule_id, notify=notify, payload=payload)

    def set_mission_limits(self, run_id, values):
        """Change a task's limits while it runs or while it is paused."""
        from .missions import clean_limits
        row = self.db.get("runs", run_id)
        if not row or row.get("kind") != "mission":
            raise KeyError(run_id)
        live = self.orchestras.get(row["chat_id"])
        if live is not None and live.id == run_id:
            live.limits = clean_limits(values, live.limits)
            live.save()
            live.publish()
            return live.limits
        state = row.get("state") or {}
        mission = state.get("mission") or {}
        mission["limits"] = clean_limits(values, mission.get("limits"))
        self.db.update("runs", run_id, {"state": {**state, "mission": mission}, "updated": now()})
        cfg = self.db.setting(f"chat_mission:{row['chat_id']}", None)
        if cfg:
            self.db.set_setting(f"chat_mission:{row['chat_id']}", {**cfg, "limits": mission["limits"]})
        return mission["limits"]

    async def stop(self):
        for t in self._bg:
            t.cancel()
        self.telemetry.stop()
        await self.jobs.shutdown()
        await asyncio.to_thread(self.procs.end_all)
        await self.connectors.shutdown()
        await self.services.shutdown()
        await self.embedder.stop()
        await self.runtime.shutdown()
        self.log("engine stopped")

    # ------------------------------------------------------------------ chats
    def create_chat(self, project_id=None, title="", model=None, harness=None, connectors=None, folders=None,
                    origin="user", schedule_id=None, thinking=None, settings=None, parent_chat_id=None,
                    agent_name=None):
        project = self.db.get("projects", project_id) if project_id else None
        if thinking is None:                   # 2.29.0: thinking is on unless the chat says otherwise
            thinking = bool(self.db.setting("thinking_default", True))
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
        try:
            self.apps.after_turn(chat_id, status)      # 2.34.0: a builder that changed its app but did not save it
        except Exception as exc:                       # noqa: BLE001 — a reply must never fail on this
            self.log(f"apps: after the turn: {exc}")
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
