"""Orchestrated teams (2.4.0): a planner runs agents as a task graph, and code schedules them.

The planner (the chat's model; under Auto the strongest one) turns a request into a few tasks. Each task has a
goal, a "done when" check, the tasks whose results it needs (depends_on), a role (which model runs it) and a
harness (how the agent works). Then a scheduler written in code, which costs no model calls, runs the graph:

  - a task starts only when every task it depends on has been accepted; it receives their results and works in a
    shared team folder, so files pass between agents directly
  - independent tasks run at the same time, as far as the models' simultaneous-reply slots allow
  - when agents finish, the planner reads their results together (one call for everything that finished at about
    the same time) and decides: accept, retry with feedback (the same agent continues with its context, so it can
    fix its own code), message a running agent, pause / resume / cancel, rewrite or add tasks, answer a question,
    or finish
  - agents can read the team board, wait for another task's result, and ask the planner a question

Chats, projects and scheduled tasks use this automatically when a request looks multi-step (Settings → Teams);
the planner may still decide that one agent is enough. Team harnesses, the composer's team builder and the
`delegate` tool always use it, so agents never run uncoordinated."""
import asyncio
import json
import re
import time
from pathlib import Path

from .db import new_id
from .llm import LLMError
from .providers import is_cloud
from .tools import Tool, ToolError

ACTIONS = ("accept", "retry", "message", "pause", "resume", "cancel", "update", "add", "answer", "finish")
SETTLED = ("done", "failed", "cancelled")
ACTIVE = ("running",)
VERBS = re.compile(
    r"\b(find|collect|gather|download|scrape|fetch|research|look up|analy[sz]e|compare|evaluate|assess|calculate|"
    r"compute|model|forecast|chart|plot|visuali[sz]e|summari[sz]e|write|draft|build|create|make|implement|code|"
    r"program|test|debug|fix|refactor|review|check|verify|clean|merge|convert|translate|design|plan|prepare|"
    r"publish|report|present|deploy|organi[sz]e|extract|read)\b", re.I)
LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+\S", re.M)
TEAM_WORDS = re.compile(r"\b(agents?|team|in parallel|at the same time|several steps|step by step|end[- ]to[- ]end|"
                        r"pipeline|workflow|project plan)\b", re.I)


def looks_multistep(text):
    """Cheap first filter (no model call): could this request need more than one agent? The planner decides."""
    t = (text or "").strip()
    if len(t) < 40:
        return False
    verbs = {m.group(0).lower() for m in VERBS.finditer(t)}
    items = len(LIST_ITEM.findall(t))
    return items >= 2 or bool(TEAM_WORDS.search(t)) or (len(verbs) >= 3 and len(t) >= 120) or \
        (len(verbs) >= 2 and len(t) >= 400)


# ---------------------------------------------------------------------- pausing any chat's agent loop
def pause_chat(app, chat_id, reason="paused"):
    p = app.pauses.get(chat_id)
    if p is None:
        p = app.pauses[chat_id] = {"event": asyncio.Event(), "reason": reason}
    p["event"].clear()
    p["reason"] = reason
    orch = app.orchestras.get(chat_id)
    if orch:                                 # pausing a team pauses its running agents and the scheduler
        for t in orch.tasks.values():
            if t["status"] == "running" and t.get("chat_id"):
                pause_chat(app, t["chat_id"], reason)
    app.bus.publish("chat", chat_id=chat_id, type="paused", reason=reason)


def resume_chat(app, chat_id):
    p = app.pauses.pop(chat_id, None)
    if p:
        p["event"].set()
    orch = app.orchestras.get(chat_id)
    if orch:
        for t in orch.tasks.values():
            if t.get("chat_id") and t["chat_id"] in app.pauses and not t.get("held_by_lead"):
                resume_chat(app, t["chat_id"])
        orch.wake()
    app.bus.publish("chat", chat_id=chat_id, type="resumed")


def is_paused(app, chat_id):
    p = app.pauses.get(chat_id)
    return bool(p and not p["event"].is_set())


# ---------------------------------------------------------------------- schemas
def _task_props(harness_ids, roles, agents=None):
    props = {"id": {"type": "string", "maxLength": 12}, "name": {"type": "string", "maxLength": 40},
             "goal": {"type": "string", "maxLength": 1500}, "done_when": {"type": "string", "maxLength": 300},
             "depends_on": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 12}},
             "role": {"enum": list(roles)}, "harness": {"enum": list(harness_ids)}}
    props["agent"] = {"enum": list(agents)} if agents else {"type": "string", "maxLength": 40}
    return props


def plan_schema(harness_ids, roles, max_tasks, allow_direct, agents=None):
    return {"type": "object", "required": ["approach", "tasks"], "properties": {
        "approach": {"enum": ["direct", "team"] if allow_direct else ["team"]},
        "reason": {"type": "string", "maxLength": 300},
        "tasks": {"type": "array", "minItems": 0 if allow_direct else 1, "maxItems": max_tasks, "items": {
            "type": "object", "required": ["id", "name", "goal", "done_when"],
            "properties": _task_props(harness_ids, roles, agents)}},
        "final": {"type": "string", "maxLength": 400}}}


def check_schema(harness_ids, roles, agents=None):
    props = {"do": {"enum": list(ACTIONS)}, "task": {"type": "string", "maxLength": 12},
             "text": {"type": "string", "maxLength": 1500}}
    props.update({k: v for k, v in _task_props(harness_ids, roles, agents).items() if k != "id"})
    return {"type": "object", "required": ["actions"], "properties": {
        "summary": {"type": "string", "maxLength": 300},
        "actions": {"type": "array", "maxItems": 14, "items": {
            "type": "object", "required": ["do", "task"], "properties": props}}}}


def _clip(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + f"\n…({len(text) - n:,} more characters)"


class Orchestra:
    """One team run inside a chat turn. `turn` is the lead's Turn (its model plans and checks)."""

    def __init__(self, turn, roster=None, overall=None):
        self.turn, self.app, self.db = turn, turn.app, turn.db
        self.id = new_id("team_")
        self.tasks = {}                       # id -> task dict (insertion order = plan order)
        self.events = asyncio.Queue()
        self.roster = [dict(r) for r in roster] if roster else None
        self.agent_chats = {}                 # agent name -> chat id (same agent = same context)
        self.handles = {}
        self.settled = {}                     # id -> asyncio.Event (for wait_for)
        self.questions = {}                   # id -> asyncio.Future (ask_lead)
        self.overall = overall if overall is not None else turn.last_user_text()
        self.final_hint = ""
        self.finished = False
        self.dir = turn.workspace / "team"
        self.dir.mkdir(parents=True, exist_ok=True)
        s = self.db.setting
        self.max_tasks = int(s("max_team_tasks", 8))
        self.max_parallel = int(s("max_parallel_agents", 4))
        self.max_attempts = 3
        self.time_limit = float(s("team_time_limit_minutes", 120)) * 60
        self.max_checks = self.max_tasks * 3 + 4
        self.checks = 0
        self.started = time.monotonic()
        self.step = {"type": "orchestra", "id": self.id, "tasks": [], "log": [], "folder": str(self.dir)}
        self._wake = asyncio.Event()

    # ------------------------------------------------------------------ helpers
    @property
    def harness_ids(self):
        return [h["id"] for h in self.turn.team_harnesses()]

    def roles(self):
        from .runner import ROLES
        return ROLES

    def agents(self):
        return [r["name"] for r in self.roster] if self.roster else None

    def wake(self):
        self._wake.set()

    def logline(self, text):
        self.step["log"].append({"at": round(time.monotonic() - self.started, 1), "text": text})
        self.step["log"] = self.step["log"][-60:]
        self.publish()

    def view(self, t):
        model = self.app.models.get_any(t["model"]) if t.get("model") else None
        out = {k: t.get(k) for k in ("id", "name", "agent", "done_when", "depends_on", "role", "harness", "model",
                                     "status", "attempts", "chat_id", "progress", "note", "files", "wait_reason")}
        out["goal"] = _clip(t.get("goal"), 400)
        out["output"] = _clip(t.get("output"), 700)
        out["model_name"] = (model or {}).get("name") or t.get("model")
        out["online"] = is_cloud(t.get("model"))
        out["paused"] = bool(t.get("chat_id") and is_paused(self.app, t["chat_id"])) or t["status"] == "paused"
        return out

    def publish(self):
        for t in self.tasks.values():
            if t["status"] == "running" and t.get("job_id"):
                t["progress"] = self.app.jobs.live.get(t["job_id"], t.get("progress") or "")
        self.step["tasks"] = [self.view(t) for t in self.tasks.values()]
        self.turn.emit("orchestra", **{k: v for k, v in self.step.items() if k != "type"})

    # ------------------------------------------------------------------ the graph
    def _clean(self, raw, existing=()):
        """Validate planned tasks: unique ids, known dependencies only, no cycles."""
        out, seen = [], set(existing)
        for i, r in enumerate(raw or [], 1):
            goal = (r.get("goal") or "").strip()
            if not goal:
                continue
            tid = re.sub(r"[^a-zA-Z0-9_-]", "", str(r.get("id") or "")) or f"t{len(seen) + 1}"
            while tid in seen:
                tid = f"t{len(seen) + i}"
            seen.add(tid)
            harness = r.get("harness") if r.get("harness") in self.harness_ids else "agent"
            role = r.get("role") if r.get("role") in self.roles() else None
            agent = (str(r.get("agent") or "").strip()[:40]) or None
            item = {"id": tid, "name": (str(r.get("name") or tid).strip() or tid)[:40], "goal": goal[:1500],
                    "done_when": (r.get("done_when") or "").strip()[:300], "depends_on": list(r.get("depends_on") or []),
                    "role": role, "harness": harness, "agent": agent, "model": r.get("model"),
                    "limits": r.get("limits") or {}, "status": "waiting", "attempts": 0, "output": "", "files": []}
            if self.roster and agent:
                spec = next((x for x in self.roster if x["name"] == agent), None)
                if spec:
                    item["harness"] = spec.get("harness") or item["harness"]
                    item["model"] = spec.get("model") or item["model"]
                    item["limits"] = spec.get("limits") or item["limits"]
                    item["role"] = spec.get("role") or item["role"]
            out.append(item)
        if self.roster:
            names = [r["name"] for r in self.roster]
            for i, t in enumerate(x for x in out if not x.get("agent") or x["agent"] not in names):
                spec = self.roster[i % len(self.roster)]
                t.update(agent=spec["name"], harness=spec.get("harness") or t["harness"],
                         model=spec.get("model") or t["model"], limits=spec.get("limits") or t["limits"])
        known = set(existing) | {t["id"] for t in out}
        for t in out:
            t["depends_on"] = [d for d in dict.fromkeys(t["depends_on"]) if d in known and d != t["id"]]
        # break cycles: walk in order, keep only edges to tasks that cannot reach back
        graph = {t["id"]: set(t["depends_on"]) for t in out}
        graph.update({k: set(v["depends_on"]) for k, v in self.tasks.items()})

        def reaches(a, b, seen_=None):
            seen_ = seen_ or set()
            if a == b:
                return True
            seen_.add(a)
            return any(reaches(n, b, seen_) for n in graph.get(a, ()) if n not in seen_)
        for t in out:
            for d in list(t["depends_on"]):
                if reaches(d, t["id"]):
                    t["depends_on"].remove(d)
                    graph[t["id"]].discard(d)
        return out

    def add(self, items):
        for t in self._clean(items, existing=self.tasks):
            self.tasks[t["id"]] = t
            self.settled[t["id"]] = asyncio.Event()
        self.turn.assign_models([t for t in self.tasks.values() if t["status"] == "waiting" and
                                 (not t.get("model") or str(t["model"]).startswith("role:") or t["model"] == "auto")])

    def preset(self, items):
        """Tasks given directly (team builder with tasks, the delegate tool): no planning call."""
        self.add([{"id": r.get("id") or f"t{i}", **r} for i, r in enumerate(items, 1)])
        return bool(self.tasks)

    # ------------------------------------------------------------------ planning
    async def plan(self, messages, allow_direct=True):
        """Ask the planner for a task graph. False when it decides one agent should simply do the work."""
        turn = self.turn
        turn.status("The planner is looking at the request…")
        options = "; ".join(f"{h['id']}: {h.get('description', '')[:90]}" for h in turn.team_harnesses())
        from .runner import ROLE_HELP
        roster = ""
        if self.roster:
            roster = ("\nAgents you have (assign every task to one of them with 'agent'; the same agent keeps its "
                      "context between its tasks): " + "; ".join(
                          f"{r['name']} (harness {r.get('harness') or 'agent'}" +
                          (f", task hint: {r['task'][:200]}" if r.get("task") else "") + ")" for r in self.roster))
        direct = ("Choose approach 'direct' with no tasks when one agent can do this alone in about ten tool "
                  "steps or fewer — that is the usual case; a team costs time. " if allow_direct else "")
        prompt = (
            "You are the planner of a team of AI agents working on the user's computer. " + direct +
            f"Otherwise break my last request into a task graph of 2 to {self.max_tasks} substantial tasks:\n"
            "- Each task is done by one agent with tools (files in my folders, the web, Python and shell commands, "
            "memory, skills, connectors). Write a self-contained goal with every detail it needs (paths, sources, "
            "formats, constraints) and a concrete done_when the result can be checked against.\n"
            "- depends_on lists the tasks whose results this one needs. A task starts only after those are accepted, "
            "and it receives their results and the files they saved in the shared team folder. Tasks without "
            "dependencies run at the same time, so do not add dependencies that are not needed.\n"
            "- Give the same 'agent' name to tasks that should continue in one agent's context (write code, then "
            "fix it after a test task); otherwise give each task its own agent name.\n"
            f"- role: {ROLE_HELP} harness: {options}. Use 'coder' for writing or running code.\n"
            "- 'final' says what the final answer must contain." + roster + " JSON only.")
        schema = plan_schema(self.harness_ids, self.roles(), self.max_tasks, allow_direct, self.agents())
        try:
            out = (await turn.complete(messages + [{"role": "user", "content": prompt}], schema=schema,
                                       max_tokens=2400, temperature=0.2))["json"]
        except LLMError as exc:
            if allow_direct:
                return False
            out = {"approach": "team", "tasks": [{"id": "t1", "name": "Agent", "goal": self.overall,
                                                  "done_when": "the request is answered"}]}
            turn.note(f"The planner's plan could not be read ({str(exc)[:120]}); one agent takes the whole task.")
        if out.get("approach") != "team" or not out.get("tasks"):
            if allow_direct:
                return False
            out["tasks"] = [{"id": "t1", "name": "Agent", "goal": self.overall, "done_when": "the request is answered"}]
        self.final_hint = (out.get("final") or "").strip()
        self.add(out["tasks"][:self.max_tasks])
        if not self.tasks:
            return False
        reason = (out.get("reason") or "").strip()
        self.logline(f"Planned {len(self.tasks)} task{'s' if len(self.tasks) > 1 else ''}"
                     + (f": {reason}" if reason else "."))
        return True

    # ------------------------------------------------------------------ running
    def deps_ready(self, t):
        return all(self.tasks.get(d, {}).get("status") == "done" for d in t["depends_on"])

    def deps_dead(self, t):
        return any(self.tasks.get(d, {}).get("status") in ("failed", "cancelled") for d in t["depends_on"])

    def capacity(self, model):
        if is_cloud(model):
            return int(self.db.setting("cloud_max_parallel", 4))
        m = self.app.models.get_any(model) or {}
        return max(1, int((m.get("settings") or {}).get("parallel") or 1))

    def startable(self):
        running = [t for t in self.tasks.values() if t["status"] in ACTIVE]
        busy_agents = {t["agent"] for t in running if t.get("agent")}
        per_model = {}
        for t in running:
            per_model[t["model"]] = per_model.get(t["model"], 0) + 1
        out = []
        for t in self.tasks.values():
            if t["status"] != "waiting" or not self.deps_ready(t):
                continue
            if len(running) + len(out) >= self.max_parallel:
                t["wait_reason"] = "waiting for a free agent slot"
                continue
            if t.get("agent") and t["agent"] in busy_agents:
                t["wait_reason"] = f"{t['agent']} is busy with another task"
                continue
            model = t.get("model") or self.turn.model_id
            if per_model.get(model, 0) >= self.capacity(model):
                name = (self.app.models.get_any(model) or {}).get("name") or model
                t["wait_reason"] = f"waiting for {name} (raise its Simultaneous replies to run more at once)"
                continue
            t["wait_reason"] = None
            per_model[model] = per_model.get(model, 0) + 1
            if t.get("agent"):
                busy_agents.add(t["agent"])
            out.append(t)
        return out

    def brief(self, t):
        """What an agent is told when its task starts."""
        parts = []
        if self.overall and self.overall.strip() != t["goal"].strip():
            parts.append(f"Overall request (for context only — do just your task): {_clip(self.overall, 1500)}")
        parts.append(f"Your task ({t['id']} “{t['name']}”): {t['goal']}")
        if t.get("done_when"):
            parts.append(f"Done when: {t['done_when']}")
        deps = [self.tasks[d] for d in t["depends_on"] if d in self.tasks]
        if deps:
            budget = max(1500, 9000 // len(deps))
            parts.append("Results you build on (accepted by the planner):\n\n" + "\n\n".join(
                f"### {d['id']} {d['name']}\n{_clip(d['output'], budget)}" +
                (f"\nFiles: {', '.join(d['files'][:12])}" if d.get("files") else "") for d in deps))
        parts.append(f"Shared team folder: {self.dir} — save files other agents or the final answer need there.\n"
                     "Work efficiently: use tools only as needed. If code fails, read the error, fix the cause and run "
                     "it again; check outputs before you rely on them. Use ask_lead only if you are truly blocked.\n"
                     "Finish with your result: what you did, the result itself (facts, numbers, sources, file paths), "
                     "and whether 'done when' is met.")
        return "\n\n".join(parts)

    def _files_since(self, since):
        try:
            return [str(p) for p in sorted(self.dir.rglob("*")) if p.is_file() and p.stat().st_mtime >= since - 1][:30]
        except OSError:
            return []

    async def start(self, t, text=None):
        from .app_state import clean_limits
        parent = self.turn.chat
        model = t.get("model") or self.turn.model_id
        if model and not self.app.models.get_any(model):
            model = self.turn.model_id
        t["model"] = model
        chat_id = t.get("chat_id") or (self.agent_chats.get(t["agent"]) if t.get("agent") else None)
        if not chat_id:
            name = t.get("agent") or t["name"]
            chat = self.app.create_chat(project_id=parent.get("project_id"), title=f"{name}: {t['goal'][:60]}",
                                        model=model, harness=t.get("harness") or "agent",
                                        connectors=parent.get("connectors"),
                                        folders=self.turn.folders() + [str(self.dir)], origin="agent",
                                        settings=clean_limits(t.get("limits")), parent_chat_id=parent["id"],
                                        agent_name=name)
            chat_id = chat["id"]
            if t.get("agent"):
                self.agent_chats[t["agent"]] = chat_id
        else:
            self.db.update("chats", chat_id, {"model": model, "harness": t.get("harness") or "agent"})
        t["chat_id"] = chat_id
        self.app.team_of_chat[chat_id] = (self, t["id"])
        t["attempts"] += 1
        t["status"] = "running"
        t["started_at"] = time.time()
        job = self.app.start_turn(chat_id, text or self.brief(t), kind="agent",
                                  payload={"auto_approve": self.turn.auto_approve})
        t["job_id"] = job["id"]
        self.handles[t["id"]] = self.app.spawn(self._watch(t, job["id"]))
        self.publish()

    async def _watch(self, t, job_id):
        task = self.app.jobs.tasks.get(job_id)
        while task:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.done():
                    raise
            except Exception:
                pass
            nxt = self.app.jobs.running_for_chat(t["chat_id"])   # a message sent to it ran as a follow-up
            task = self.app.jobs.tasks.get(nxt) if nxt else None
            if task:
                t["job_id"] = nxt
        job = self.db.get("jobs", t["job_id"]) or {}
        msg = self.db.one("SELECT content, meta_json FROM messages WHERE chat_id=? AND role='assistant' "
                          "ORDER BY created DESC, rowid DESC LIMIT 1", (t["chat_id"],)) or {}
        meta = msg.get("meta") or {}
        error = meta.get("error") or (job.get("error") if job.get("status") != "done" else None)
        t["output"] = msg.get("content") or ""
        t["error"] = error
        t["files"] = self._files_since(t.get("started_at") or 0)
        from .runner import private_tool_names
        if any(s.get("type") == "tool" and s.get("name") in private_tool_names() for s in meta.get("steps", [])):
            self.turn.private.add("files")        # its result may quote your files: ask before it goes online
        stats = meta.get("stats") or {}
        if stats.get("cost"):
            self.turn.stats["cost"] = round(float(self.turn.stats.get("cost") or 0) + float(stats["cost"]), 6)
        if t["status"] != "cancelled":
            t["status"] = "review"
            t["result_state"] = "stopped" if job.get("status") == "cancelled" else ("failed" if error or not t["output"]
                                                                                     else "done")
        await self.events.put(("finished", t["id"]))
        self.wake()

    def settle(self, t, status, note=None):
        t["status"] = status
        if note:
            t["note"] = note
        self.settled[t["id"]].set()
        if status in ("cancelled",) and t.get("job_id"):
            self.app.jobs.cancel(t["job_id"])

    # ------------------------------------------------------------------ the planner reads results
    def board_text(self, fresh_ids):
        lines = []
        for t in self.tasks.values():
            state = t["status"]
            if state == "waiting":
                missing = [d for d in t["depends_on"] if self.tasks.get(d, {}).get("status") != "done"]
                state = f"waiting for {', '.join(missing)}" if missing else "ready to start"
            elif state == "running":
                state = f"running ({t.get('progress') or 'working'})"
            lines.append(f"- {t['id']} “{t['name']}” [{t.get('role') or 'worker'}, {t.get('harness')}"
                         f"{', agent ' + t['agent'] if t.get('agent') else ''}] — {state}"
                         + (f"; depends on {', '.join(t['depends_on'])}" if t['depends_on'] else "")
                         + (f"; attempt {t['attempts']}" if t['attempts'] > 1 else ""))
        return "\n".join(lines)

    def fresh_text(self, ids, questions, notes):
        ctx = int(self.turn.server.plan.get("ctx") or 8192) if self.turn.server else 8192
        budget = max(1200, int((ctx - 3500) * 3 / max(1, len(ids) + 2)))
        out = []
        for tid in ids:
            t = self.tasks[tid]
            head = f"### {tid} “{t['name']}” — {t.get('result_state')} (attempt {t['attempts']})"
            body = [f"Goal: {_clip(t['goal'], 500)}", f"Done when: {t.get('done_when') or '(not given)'}"]
            if t.get("error"):
                body.append(f"Error: {t['error'][:500]}")
            body.append("Result:\n" + (_clip(t["output"], budget) or "(empty)"))
            if t.get("files"):
                body.append("Files: " + ", ".join(t["files"][:15]))
            out.append(head + "\n" + "\n".join(body))
        for tid, q in questions:
            out.append(f"### Question from {tid} “{self.tasks[tid]['name']}” (answer it with do=answer)\n{q}")
        out += notes
        return "\n\n".join(out)

    async def checkpoint(self, messages, ids, questions=(), notes=()):
        """One planner call for everything that happened since the last one."""
        turn = self.turn
        self.checks += 1
        await turn.hold_model()
        turn.status("The planner is reading the results…")
        prompt = (
            f"You lead a team working on my request. Board:\n{self.board_text(ids)}\n\nNew since your last check:\n\n"
            f"{self.fresh_text(ids, questions, notes)}\n\n"
            "Decide what happens next. For each finished task: 'accept' if the result meets its done_when (be strict "
            "about facts, numbers, files and code that was actually run), or 'retry' with specific feedback in text "
            "(the same agent continues with its context, so it can fix its own work). You may also: 'message' a "
            "running task with information it needs (for example from a result that just came in), 'pause' or "
            "'resume' a task, 'cancel' a task that is no longer needed, 'update' a task that has not started "
            "(new goal, done_when or depends_on), 'add' tasks (task = new id, plus name, goal, done_when, "
            "depends_on), 'answer' a question (text), or 'finish' when my request can be fully answered now "
            "(unfinished tasks are cancelled). Only give actions that are needed. JSON only.")
        try:
            out = (await turn.complete(messages + [{"role": "user", "content": prompt}],
                                       schema=check_schema(self.harness_ids, self.roles(), self.agents()),
                                       max_tokens=1800, temperature=0.2))["json"]
        except LLMError as exc:
            turn.app.log(f"planner checkpoint failed: {exc}")
            out = {"actions": []}
        finally:
            if not is_cloud(turn.model_id):
                await turn.drop_model()   # workers on the same local model get the slot back
        return await self.apply(out, ids, {tid: self.questions[tid] for tid, _ in questions
                                           if tid in self.questions})

    async def apply(self, out, ids, questions):
        done_now, said = set(), []
        for a in out.get("actions") or []:
            kind, tid, text = a.get("do"), str(a.get("task") or ""), (a.get("text") or "").strip()
            t = self.tasks.get(tid)
            if kind == "finish":
                self.finished = True
                said.append("finished the work")
                continue
            if kind == "add":
                if len(self.tasks) >= self.max_tasks * 2:
                    continue
                before = set(self.tasks)
                self.add([{**a, "id": tid or None}])
                new = [x for x in self.tasks if x not in before]
                if new:
                    said.append("added " + ", ".join(f"{n} “{self.tasks[n]['name']}”" for n in new))
                continue
            if not t:
                continue
            if kind == "accept" and t["status"] == "review":
                self.settle(t, "done", text or None)
                done_now.add(tid)
                said.append(f"accepted {tid}")
            elif kind == "retry" and t["status"] == "review":
                done_now.add(tid)
                if t["attempts"] >= self.max_attempts:
                    self.settle(t, "failed", f"still not right after {t['attempts']} attempts")
                    said.append(f"gave up on {tid} after {t['attempts']} attempts")
                    continue
                t["note"] = text
                said.append(f"asked {tid} to fix: {text[:160]}")
                await self.start(t, "The planner reviewed your result and asks for this:\n" + (text or "Improve it.")
                                 + "\n\nFix it, check it, and reply with the complete corrected result.")
            elif kind == "message" and t["status"] == "running" and text:
                self.app.post_message(t["chat_id"], f"[From the planner] {text}", mode="steer")
                said.append(f"told {tid}: {text[:120]}")
            elif kind == "pause":
                if t["status"] == "running" and t.get("chat_id"):
                    t["held_by_lead"] = True
                    pause_chat(self.app, t["chat_id"], text or "paused by the planner")
                elif t["status"] == "waiting":
                    t["status"] = "paused"
                said.append(f"paused {tid}")
            elif kind == "resume":
                t.pop("held_by_lead", None)
                if t.get("chat_id") and is_paused(self.app, t["chat_id"]):
                    resume_chat(self.app, t["chat_id"])
                if t["status"] == "paused":
                    t["status"] = "waiting"
                said.append(f"resumed {tid}")
            elif kind == "cancel" and t["status"] not in SETTLED:
                self.settle(t, "cancelled", text or "no longer needed")
                done_now.add(tid)
                said.append(f"cancelled {tid}")
            elif kind == "update" and t["status"] in ("waiting", "paused"):
                for key in ("goal", "done_when", "name"):
                    if a.get(key):
                        t[key] = str(a[key])[:1500]
                if text and not a.get("goal"):
                    t["goal"] = f"{t['goal']}\n\nNote from the planner: {text}"
                if isinstance(a.get("depends_on"), list):
                    t["depends_on"] = [d for d in a["depends_on"] if d in self.tasks and d != tid]
                if a.get("role") in self.roles() and a["role"] != t.get("role"):
                    t["role"], t["model"] = a["role"], None
                    self.turn.assign_models([t])
                said.append(f"updated {tid}")
            elif kind == "answer" and tid in questions and text:
                fut = questions.pop(tid)
                if not fut.done():
                    fut.set_result(text)
                said.append(f"answered {tid}")
        # defaults for results the planner did not act on
        for tid in ids:
            t = self.tasks[tid]
            if tid in done_now or t["status"] != "review":
                continue
            if t.get("result_state") == "done":
                self.settle(t, "done")
                said.append(f"accepted {tid}")
            elif t.get("result_state") == "failed" and t["attempts"] < 2 and not self.finished:
                said.append(f"retried {tid} after an error")
                await self.start(t, f"That did not work ({(t.get('error') or 'no result')[:300]}). Try a different "
                                    "approach and reply with the complete result.")
            else:
                self.settle(t, "failed", t.get("error") or "no result")
        for tid, fut in questions.items():
            if not fut.done():
                fut.set_result("The planner did not answer; use your best judgement and say what you assumed.")
        if out.get("summary"):
            self.turn.note(f"Planner: {out['summary'].strip()[:300]}")
        if said:
            self.logline("Planner " + "; ".join(said) + ".")
        self.publish()

    # ------------------------------------------------------------------ the scheduler
    async def drive(self, messages):
        """Run the graph until every task is settled or the planner finishes. Returns the accepted tasks."""
        turn, app = self.turn, self.app
        app.orchestras[turn.chat["id"]] = self
        turn.steps.append(self.step)
        if not is_cloud(turn.model_id):
            await turn.drop_model()
        self.publish()
        stalled_once = False
        try:
            while not self.finished:
                if time.monotonic() - self.started > self.time_limit:
                    turn.note("The team reached its time limit; the planner writes up what is done.")
                    break
                if not is_paused(app, turn.chat["id"]):
                    for t in self.startable():
                        await self.start(t)
                running = [t for t in self.tasks.values() if t["status"] in ACTIVE]
                pending = [t for t in self.tasks.values() if t["status"] in ("waiting", "paused")]
                if not running and self.events.empty():
                    if not pending:
                        break
                    if is_paused(app, turn.chat["id"]):
                        self._wake.clear()
                        turn.status("The team is paused. Press Resume to continue.")
                        await self._wake.wait()
                        continue
                    blocked = [t for t in pending if t["status"] == "waiting" and self.deps_dead(t)]
                    held = [t for t in pending if t["status"] == "paused"]
                    if stalled_once or self.checks >= self.max_checks:
                        for t in pending:
                            self.settle(t, "cancelled", "could not start: a task it needs failed or was cancelled"
                                        if t in blocked else "left paused")
                        break
                    stalled_once = True
                    note = ("### Stalled\nNothing is running. " +
                            (f"These tasks cannot start because a task they need failed or was cancelled: "
                             f"{', '.join(t['id'] for t in blocked)}. " if blocked else "") +
                            (f"These tasks are paused: {', '.join(t['id'] for t in held)}. " if held else "") +
                            "Update, replace, resume or cancel them, or finish.")
                    await self.checkpoint(messages, [], notes=[note])
                    continue
                stalled_once = False
                done = sum(t["status"] in SETTLED for t in self.tasks.values())
                turn.status(f"Team: {done}/{len(self.tasks)} tasks done, {len(running)} running")
                if self.events.empty():
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
                    if self.events.empty():
                        self.publish()
                        continue
                await asyncio.sleep(0.8)          # results that arrive together are read together
                batch = []
                while not self.events.empty():
                    batch.append(self.events.get_nowait())
                ids = [e[1] for e in batch if e[0] == "finished" and self.tasks[e[1]]["status"] == "review"]
                questions = [(e[1], e[2]) for e in batch if e[0] == "question"
                             and e[1] in self.questions and not self.questions[e[1]].done()]
                if not ids and not questions:
                    continue
                if self.checks >= self.max_checks:
                    await self.apply({"actions": []}, ids, {tid: self.questions[tid] for tid, _ in questions})
                    continue
                await self.checkpoint(messages, ids, questions)
        except asyncio.CancelledError:
            for t in self.tasks.values():
                if t.get("job_id") and t["status"] in ACTIVE:
                    app.jobs.cancel(t["job_id"])
            raise
        finally:
            for t in self.tasks.values():
                if t["status"] == "review":          # finished, but the planner stopped before reading it
                    self.settle(t, "done" if t.get("result_state") == "done" else "failed")
                elif t["status"] not in SETTLED:
                    self.settle(t, "cancelled", "the planner finished without it")
            for fut in self.questions.values():
                if not fut.done():
                    fut.set_result("The team has finished.")
                if t.get("chat_id"):
                    app.team_of_chat.pop(t["chat_id"], None)
                    if is_paused(app, t["chat_id"]):
                        resume_chat(app, t["chat_id"])
            app.orchestras.pop(turn.chat["id"], None)
            self.publish()
        return [t for t in self.tasks.values() if t["status"] == "done"]

    def results_text(self, budget):
        out = []
        for t in self.tasks.values():
            if t["status"] == "done":
                out.append(f"### {t['id']} {t['name']}\n{_clip(t['output'], budget)}" +
                           (f"\nFiles: {', '.join(t['files'][:15])}" if t.get("files") else ""))
            else:
                out.append(f"### {t['id']} {t['name']} — {t['status']}: {t.get('note') or t.get('error') or ''}")
        return "\n\n".join(out)

    async def final(self, messages):
        turn = self.turn
        await turn.hold_model()
        ctx = int(turn.server.plan.get("ctx") or 8192) if turn.server else 8192
        budget = max(1500, int((ctx - turn.max_reply - 3000) * 3 / max(1, len(self.tasks) + 2)))
        turn.status("The planner is writing the answer…")
        messages.append({"role": "user", "content": (
            f"Your team has finished. Results:\n\n{self.results_text(budget)}\n\nShared team folder: {self.dir}\n\n"
            "Now write the final answer to my request" + (f" ({self.final_hint})" if self.final_hint else "") +
            ". Keep facts, numbers, sources and file paths; point out anything that failed, is missing or "
            "disagrees between agents.")})
        turn.text = []
        await turn.stream_text(messages, None)

    # ------------------------------------------------------------------ tools for the agents in the team
    async def ask(self, tid, question):
        fut = asyncio.get_running_loop().create_future()
        self.questions[tid] = fut
        await self.events.put(("question", tid, question))
        self.wake()
        return fut


def team_tools(orch, tid, turn):
    """Added to an agent's tools while it works in a team."""
    async def team_board(ctx):
        lines = [orch.board_text([])]
        for t in orch.tasks.values():
            if t["status"] == "done" and t["id"] != tid:
                lines.append(f"\n### {t['id']} {t['name']} (accepted)\n{_clip(t['output'], 1500)}")
        return "\n".join(lines) + f"\n\nShared team folder: {orch.dir}"

    async def wait_for(ctx, task):
        t = orch.tasks.get(task)
        if not t:
            raise ToolError(f"No task {task!r}. Tasks: {', '.join(orch.tasks)}")
        if task == tid:
            raise ToolError("That is your own task")
        if t["status"] not in SETTLED:
            turn.status(f"Waiting for {task} “{t['name']}”…")
            await turn.drop_model()             # let others use the model while this agent waits
            try:
                await asyncio.wait_for(orch.settled[task].wait(), timeout=45 * 60)
            except asyncio.TimeoutError:
                return f"{task} is still not finished after 45 minutes; continue without it and say so."
            finally:
                await turn.hold_model()
        if t["status"] != "done":
            return f"{task} ended {t['status']}: {t.get('note') or t.get('error') or ''}"
        return f"Result of {task} “{t['name']}”:\n{_clip(t['output'], 6000)}" + \
            (f"\nFiles: {', '.join(t['files'][:15])}" if t.get("files") else "")

    async def ask_lead(ctx, question):
        fut = await orch.ask(tid, question)
        turn.status("Waiting for the planner's answer…")
        await turn.drop_model()
        try:
            return "The planner says: " + await asyncio.wait_for(fut, timeout=30 * 60)
        except asyncio.TimeoutError:
            return "The planner did not answer in time; use your best judgement and say what you assumed."
        finally:
            await turn.hold_model()

    S = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
    return [
        Tool("team_board", "See the team's tasks, their status and the accepted results of the other agents.",
             S({}, []), team_board, "team", label="Team board", summary=lambda a: "Read the team board"),
        Tool("wait_for", "Wait until another task in the team is finished and get its result (use only when you "
             "cannot continue without it).", S({"task": {"type": "string", "description": "task id, e.g. t2"}},
                                             ["task"]), wait_for, "team", label="Wait for a teammate",
             summary=lambda a: f"Wait for {a.get('task')}"),
        Tool("ask_lead", "Ask the planner a question when you are blocked (missing information or a decision "
             "only the planner can make).", S({"question": {"type": "string"}}, ["question"]), ask_lead, "team",
             label="Ask the planner", summary=lambda a: f"Ask the planner: {a.get('question', '')[:200]}"),
    ]
