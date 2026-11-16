"""Orchestrated runs (2.5.0): a planner turns a request into a sequence of steps, and code runs them in order.

What changed from 2.4 and why. A small local planner often forgot which step needs which result, so agents
started together, worked without their inputs (and made them up), and every one of them kept a model and a full
context in memory. Now:

  Sequence   Steps run one after another, in plan order (Settings → Teams can allow steps side by side). A step
             starts only when the steps it needs are accepted. Needs are also found in code: a step that mentions
             another step in its goal depends on it, and a planned step with no stated need builds on the step
             before it. A step whose input failed does not run on invented input.
  Loop       Every step is a small cycle: the agent works, code checks the result (an answer, no error, the files
             it had to save), then a short strict check by the planner's model against the step's "done when".
             If it does not pass, the agent gets only what is missing and tries again, up to the step's rounds.
             The same objection twice means a fresh agent with a different approach. When the rounds are used up
             the best attempt is kept and marked incomplete, or the step fails.
  Forecast   Before a step starts its prompt is measured, its growth and output are predicted from earlier steps
             (forecast.py), and the model is loaded with the context the run needs (one reply slot), not the
             largest context for every slot.
  Memory     A model stays loaded only while a remaining step uses it; waiting steps hold nothing.
  Recovery   Every model call is watched (llm.py). A step that hangs, runs out of memory or overflows its context
             is stopped, classified and tried again with a fitting change; a failed planner call falls back to
             code, so one bad call cannot end the run.

2.7.0 adds: the run's state is saved after every change (table `runs`), so a run that was stopped, or cut off
by a crash or restart, continues from its accepted steps (Orchestra.restore); each step of the general agent
gets only the tool groups its goal calls for (lean_kit), the full set on a fresh attempt; a step that keeps
failing moves to a stronger model if one is installed; the result check can run on a smaller model
(`check_model`); and briefs ask for a result length that fits the computer's writing speed.

Agents can read the board and ask the planner a question. Chats, projects and scheduled tasks use this
automatically when a request looks multi-step; team harnesses, the team builder and `delegate` always use it."""
import asyncio
import json
import re
import time
from pathlib import Path

from .db import new_id, now
from .forecast import pretty_seconds, pretty_tokens
from .llm import LLMError, estimate_tokens
from .providers import is_cloud
from .tools import Tool

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




# ---------------------------------------------------------------------- sizing a run's models (memory first)
class RunFit:
    """How the models of one run are loaded: the context each model needs for the run's largest step, and the
    number of reply slots. Every chat of the run (the planner's and each agent's) points at the same RunFit, so
    they all ask for the same settings and a model is loaded once for the run."""
    BASE = 12000

    def __init__(self, sequence=True):
        self.need = {}            # model id -> tokens of context one reply needs
        self.cap = {}             # model id -> upper limit after the model ran out of memory
        self.sequence = sequence

    def want(self, model_id, tokens):
        """Raise a model's need (it never shrinks during a run, so the model is not reloaded back and forth)."""
        self.need[model_id] = max(int(tokens), self.need.get(model_id, 0))

    def tokens(self, model_id):
        n = self.need.get(model_id, self.BASE)
        return min(n, self.cap[model_id]) if model_id in self.cap else n

    def overrides(self, runtime, model_id):
        out = runtime.fit(model_id, self.tokens(model_id), 1)
        if not self.sequence:
            out.pop("parallel")   # steps side by side: keep the model's own number of reply slots
        return out


# ---------------------------------------------------------------------- schemas
def _task_props(harness_ids, roles, agents=None):
    props = {"id": {"type": "string", "maxLength": 12}, "name": {"type": "string", "maxLength": 40},
             "goal": {"type": "string", "maxLength": 900}, "done_when": {"type": "string", "maxLength": 240},
             "depends_on": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 12}},
             "loop": {"type": "boolean"},
             "expects": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 80}},
             "role": {"enum": list(roles)}, "harness": {"enum": list(harness_ids)}}
    if agents:
        props["agent"] = {"enum": list(agents)}
    return props


def plan_schema(harness_ids, roles, max_tasks, allow_direct, agents=None):
    return {"type": "object", "required": ["approach", "tasks"], "properties": {
        "approach": {"enum": ["direct", "team"] if allow_direct else ["team"]},
        "reason": {"type": "string", "maxLength": 200},
        "tasks": {"type": "array", "minItems": 0 if allow_direct else 1, "maxItems": max_tasks, "items": {
            "type": "object", "required": ["id", "name", "goal", "done_when"],
            "properties": _task_props(harness_ids, roles, agents)}},
        "final": {"type": "string", "maxLength": 300}}}


def check_schema(harness_ids, roles, agents=None):
    props = {"do": {"enum": list(ACTIONS)}, "task": {"type": "string", "maxLength": 12},
             "text": {"type": "string", "maxLength": 900}}
    props.update({k: v for k, v in _task_props(harness_ids, roles, agents).items() if k != "id"})
    return {"type": "object", "required": ["actions"], "properties": {
        "summary": {"type": "string", "maxLength": 300},
        "actions": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "required": ["do", "task"], "properties": props}}}}


# the reason comes first, so a small model thinks before it gives its verdict
VERIFY_SCHEMA = {"type": "object", "required": ["reason", "met"], "properties": {
    "reason": {"type": "string", "maxLength": 300}, "met": {"type": "boolean"}}}
VERIFY_SYSTEM = ("You check one result against its acceptance test. Be strict and literal: the result must really "
                 "contain what the test asks for (the facts, numbers, sources, files, or code that was actually "
                 "run). A result that says something is missing, could not be found or could not be done does not "
                 "pass. Do not judge style. Answer in JSON: 'reason' = in one or two sentences what is missing or "
                 "wrong (or 'complete'), then 'met' = true or false.")


def _clip(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + f"\n…({len(text) - n:,} more characters)"


def _words(text):
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))


def same_objection(a, b):
    """Is this feedback the one the agent already got? Then repeating it will not help."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.7


def classify(error, timed_out=False):
    """What kind of failure a step had, so the next round changes the right thing."""
    low = (error or "").lower()
    if timed_out or any(k in low for k in ("did not start answering", "stopped writing", "did not respond",
                                           "not reachable", "stopped responding", "took more than")):
        return "timeout"
    if "not enough memory" in low or "out of memory" in low or "failed to allocate" in low:
        return "memory"
    if "does not fit the model's context" in low or ("context" in low and "exceed" in low):
        return "context"
    return "error" if low else None


# What a step's goal has to mention for a tool group to be handed to its agent (files are always given).
KIT_WORDS = {
    "web": r"web|online|internet|search|look up|url|https?:|site|page|latest|news|download|research|source|publish|api\b",
    "python": r"python|code|script|run\b|test|comput|calculat|analy[sz]|csv|xlsx|excel|data|chart|plot|graph|command|"
              r"shell|git\b|install|build|convert|parse|json|sql|statistic|average|sum\b|count",
    "memory": r"remember|memory|earlier chat|previous chat|chat history|knowledge|project notes?",
    "skills": r"skill",
    "automation": r"schedul|harness|every (day|week|hour|morning)|recurring|remind",
    "vision": r"image|photo|picture|screenshot|video|diagram|scan",
}


def lean_kit(goal, harness_groups=None):
    """The tool groups a step needs, from the words of its goal. A smaller tool list is less for the model to
    read and to hold in context, and a small model picks the right tool more often from a short list."""
    groups = ["files"] + [g for g, words in KIT_WORDS.items() if re.search(words, goal or "", re.I)]
    if harness_groups is not None:
        groups = [g for g in groups if g in harness_groups]
    return groups


# the fields of a step that are saved so a run can continue after a stop, a crash or a restart
SAVED = ("id", "name", "goal", "done_when", "depends_on", "role", "harness", "agent", "model", "limits", "status",
         "attempts", "output", "files", "loop", "expects", "rounds", "feedback", "partial", "note", "result_file",
         "chat_id", "used", "forecast", "brief_tokens", "kind", "best", "error", "result_state", "full_kit")


class Orchestra:
    """One run inside a chat turn. `turn` is the lead's Turn (its model plans and checks)."""

    def __init__(self, turn, roster=None, overall=None, fit=None):
        self.turn, self.app, self.db = turn, turn.app, turn.db
        self.id = new_id("team_")
        self.tasks = {}                       # id -> task dict (insertion order = plan order)
        self.events = asyncio.Queue()
        self.roster = [dict(r) for r in roster] if roster else None
        self.agent_chats = {}                 # agent name -> chat id (same agent = same context)
        self.handles = {}
        self.settled = {}                     # id -> asyncio.Event
        self.questions = {}                   # id -> asyncio.Future (ask_lead)
        self.overall = overall if overall is not None else turn.last_user_text()
        self.final_hint = ""
        self.finished = False
        self.dir = turn.workspace / "team"
        self.dir.mkdir(parents=True, exist_ok=True)
        s = self.db.setting
        self.sequence = s("agent_order", "sequence") != "parallel"
        self.chain = False                    # planned steps with no stated need build on the step before
        self.max_tasks = int(s("max_team_tasks", 8))
        self.max_parallel = 1 if self.sequence else int(s("max_parallel_agents", 4))
        self.rounds = max(1, min(8, int(s("step_rounds", 3) or 3)))
        self.verify_results = bool(s("verify_results", True))
        self.time_limit = float(s("team_time_limit_minutes", 120)) * 60
        self.max_checks = self.max_tasks * 3 + 4
        self.checks = 0
        self.started = time.monotonic()
        self.fit = fit                        # RunFit when this run sizes its models (None: use them as loaded)
        self.used = set()                     # local models this run started steps on
        self.warm = set()                     # (model, harness) pairs whose fixed prompt part is already cached
        self.step = {"type": "orchestra", "id": self.id, "tasks": [], "log": [], "folder": str(self.dir),
                     "mode": "sequence" if self.sequence else "parallel", "models": [], "totals": {}}
        self._wake = asyncio.Event()
        self._tick = time.monotonic()
        self.outcome = "done"                 # how drive() ended: done | stopped | interrupted
        self.resumes = 0
        self.lean = bool(s("lean_tools", True))
        self.escalate = bool(s("escalate_models", True))

    # ------------------------------------------------------------------ saved state: a run can continue later
    def save(self, status="running"):
        """Write the run's state to the database (after every start and every settled step)."""
        try:
            state = {"overall": self.overall, "final_hint": self.final_hint, "sequence": self.sequence,
                     "chain": self.chain, "roster": self.roster, "dir": str(self.dir), "log": self.step["log"][-40:],
                     "agent_chats": self.agent_chats,
                     "need": dict(self.fit.need) if self.fit is not None else {},
                     "tasks": [{k: t.get(k) for k in SAVED} for t in self.tasks.values()]}
            row = {"chat_id": self.turn.chat["id"], "status": status, "state": state, "resumes": self.resumes,
                   "updated": now()}
            if self.db.get("runs", self.id):
                self.db.update("runs", self.id, row)
            else:                             # a new run in this chat replaces older ones that were left open
                self.db.run("UPDATE runs SET status='superseded' WHERE chat_id=? AND status IN "
                                "('interrupted','stopped','running')", (self.turn.chat["id"],))
                self.db.insert("runs", {"id": self.id, "created": now(), **row})
        except Exception as exc:              # saving must never stop a run
            self.app.log(f"saving run {self.id}: {type(exc).__name__}: {exc}")

    @classmethod
    def restore(cls, turn, row, fit=None):
        """Continue a saved run: accepted steps keep their results; a step that was working, being checked or
        waiting starts again (the interrupted attempt is not counted); failed steps stay failed."""
        state = row.get("state") or {}
        self = cls(turn, roster=state.get("roster"), overall=state.get("overall"), fit=fit)
        self.id = self.step["id"] = row["id"]
        self.final_hint = state.get("final_hint") or ""
        self.chain = bool(state.get("chain"))
        self.resumes = int(row.get("resumes") or 0) + 1
        self.step["log"] = list(state.get("log") or [])
        if state.get("dir"):
            self.dir = Path(state["dir"])
            self.dir.mkdir(parents=True, exist_ok=True)
            self.step["folder"] = str(self.dir)
        if self.fit is not None:
            for model, tokens in (state.get("need") or {}).items():
                self.fit.want(model, tokens)
        for saved in state.get("tasks") or []:
            t = {"output": "", "files": [], "feedback": [], "attempts": 0, "depends_on": [], "limits": {}, **saved}
            if t["status"] in ("running", "review", "paused"):
                t["attempts"] = max(0, int(t.get("attempts") or 0) - 1)
                t.update(status="waiting", chat_id=None, error=None, result_state=None)
            elif t["status"] == "cancelled" and t.get("note") in ("the run ended before this step", "left paused"):
                t.update(status="waiting", note=None)
            self.tasks[t["id"]] = t
            self.settled[t["id"]] = asyncio.Event()
            if t["status"] in SETTLED:
                self.settled[t["id"]].set()
        done = sum(t["status"] == "done" for t in self.tasks.values())
        self.logline(f"Continuing this run: {done} of {len(self.tasks)} steps were already accepted.")
        return self

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
        self.step["log"] = self.step["log"][-80:]
        self.publish()

    def view(self, t):
        model = self.app.models.get_any(t["model"]) if t.get("model") else None
        out = {k: t.get(k) for k in ("id", "name", "agent", "done_when", "depends_on", "role", "harness", "model",
                                     "status", "attempts", "chat_id", "progress", "note", "files", "wait_reason",
                                     "rounds", "loop", "partial", "forecast", "used", "result_file", "kind", "kit")}
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
        loaded = {s["model_id"]: s for s in self.app.runtime.loaded()}
        models = []
        for mid in dict.fromkeys([self.turn.model_id] + [t.get("model") for t in self.tasks.values()]):
            if not mid:
                continue
            row = self.app.models.get_any(mid) or {}
            live = loaded.get(mid)
            models.append({"id": mid, "name": row.get("name") or mid, "online": is_cloud(mid),
                           "loaded": bool(live), "ctx": (live or {}).get("ctx"),
                           "memory_mb": (live or {}).get("memory_mb")})
        self.step["models"] = models
        done = [t for t in self.tasks.values() if t.get("used")]
        self.step["totals"] = {"tokens": sum(t["used"].get("tokens", 0) for t in done),
                               "seconds": round(time.monotonic() - self.started)}
        self.turn.emit("orchestra", **{k: v for k, v in self.step.items() if k != "type"})

    def model_name(self, model_id):
        return (self.app.models.get_any(model_id) or {}).get("name") or model_id

    # ------------------------------------------------------------------ the plan, validated and repaired in code
    def _clean(self, raw, existing=()):
        """Validate planned steps: unique ids, known needs only, needs found in the text, no cycles."""
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
            loop = bool(r.get("loop"))
            expects = [str(x).strip()[:80] for x in (r.get("expects") or []) if str(x).strip()][:4]
            item = {"id": tid, "name": (str(r.get("name") or tid).strip() or tid)[:40], "goal": goal[:1500],
                    "done_when": (r.get("done_when") or "").strip()[:300], "depends_on": list(r.get("depends_on") or []),
                    "role": role, "harness": harness, "agent": agent, "model": r.get("model"),
                    "limits": r.get("limits") or {}, "status": "waiting", "attempts": 0, "output": "", "files": [],
                    "loop": loop, "expects": expects, "rounds": self.rounds + 2 if loop else self.rounds,
                    "feedback": [], "_stated": bool(r.get("depends_on"))}
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
        known = list(existing) + [t["id"] for t in out]
        earlier = list(existing)
        for t in out:
            deps = [d for d in dict.fromkeys(t["depends_on"]) if d in known and d != t["id"]]
            # a step that names another step in its goal needs that step's result, stated or not
            text = f"{t['goal']} {t['done_when']}"
            for other in known:
                if other != t["id"] and other not in deps and re.search(
                        rf"(?<![A-Za-z0-9_-]){re.escape(other)}(?![A-Za-z0-9_-])", text, re.I):
                    deps.append(other)
            # a planned step that states no need builds on the step before it
            if self.chain and not deps and earlier:
                deps.append(earlier[-1])
            t["depends_on"] = deps
            t.pop("_stated", None)
            earlier.append(t["id"])
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

    def _order(self):
        """Keep plan order, but never a step before one it needs."""
        placed, order, left = set(), [], list(self.tasks.values())
        while left:
            pick = next((t for t in left if all(d in placed or d not in self.tasks for d in t["depends_on"])), left[0])
            left.remove(pick)
            placed.add(pick["id"])
            order.append(pick)
        self.tasks = {t["id"]: t for t in order}

    def add(self, items):
        for t in self._clean(items, existing=self.tasks):
            self.tasks[t["id"]] = t
            self.settled[t["id"]] = asyncio.Event()
        self._order()
        self.turn.assign_models([t for t in self.tasks.values() if t["status"] == "waiting" and
                                 (not t.get("model") or str(t["model"]).startswith("role:") or t["model"] == "auto")])

    def preset(self, items):
        """Steps given directly (team builder with tasks, the delegate tool): no planning call."""
        self.add([{"id": r.get("id") or f"t{i}", **r} for i, r in enumerate(items, 1)])
        return bool(self.tasks)

    # ------------------------------------------------------------------ planning
    async def plan(self, messages, allow_direct=True):
        """Ask the planner for a sequence of steps. False when it decides one agent should simply do the work."""
        turn = self.turn
        turn.status("The planner is looking at the request…")
        options = "; ".join(f"{h['id']}: {h.get('description', '')[:70]}" for h in turn.team_harnesses())
        from .runner import ROLE_HELP
        roster = ""
        if self.roster:
            roster = ("\nAgents you have (give every step to one of them with 'agent'; the same agent keeps its "
                      "context between its steps): " + "; ".join(
                          f"{r['name']} (harness {r.get('harness') or 'agent'}" +
                          (f", task hint: {r['task'][:200]}" if r.get("task") else "") + ")" for r in self.roster))
        direct = ("Choose approach 'direct' with no tasks when one agent can do this alone in about ten tool "
                  "steps or fewer — that is the usual case; a plan costs time. " if allow_direct else "")
        limit = min(self.max_tasks, 6)
        prompt = (
            "You plan work for AI agents on the user's computer. " + direct +
            f"Otherwise write a plan of 2 to {limit} steps for my last request (fewer is better). The steps run one "
            "after another, in the order you write them.\n"
            "- One agent does each step with tools (files in my folders, the web, Python and shell commands, memory, "
            "skills, connectors). goal: everything that agent needs to know (paths, sources, formats, limits); it "
            "sees only its goal and the results it builds on. done_when: a test the result can be checked against.\n"
            "- depends_on: the ids of the earlier steps whose results this step needs; it receives them. With no "
            "depends_on a step receives the result of the step before it.\n"
            "- loop: true for a step that usually takes several rounds of try, check and fix (writing and running "
            "code, collecting until complete, drafting to a standard).\n"
            "- expects: names of files the step must save (leave out if none).\n"
            f"- role: {ROLE_HELP} harness: {options}. Use 'coder' for writing or running code.\n"
            "- Usual shapes: find → extract → analyse → write; write code → run and fix → review; get data → clean → "
            "chart → report; outline → draft → edit. Do not add a step only to check another step: every result is "
            "checked for you.\n"
            '- Two examples of good steps: {"id":"t1","name":"Collect prices","goal":"Read prices.csv in the project '
            'folder and list each product with its 2024 and 2025 price.","done_when":"a table with every product and '
            'both prices","harness":"agent"} then {"id":"t2","name":"Price changes","goal":"Using the table from t1, '
            'compute the percentage change per product with Python and save changes.csv.","done_when":"changes.csv '
            'exists and lists every product with its change","depends_on":["t1"],"loop":true,"expects":["changes.csv"]}.\n'
            "- 'final' says what the final answer must contain." + roster + " JSON only.")
        schema = plan_schema(self.harness_ids, self.roles(), limit, allow_direct, self.agents())
        try:
            out = (await turn.complete(messages + [{"role": "user", "content": prompt}], schema=schema,
                                       max_tokens=1400, temperature=0.2))["json"]
        except LLMError as exc:
            if allow_direct:
                turn.app.log(f"planning failed, one agent does the work: {exc}")
                return False
            out = {"approach": "team", "tasks": [{"id": "t1", "name": "Agent", "goal": self.overall,
                                                  "done_when": "the request is answered"}]}
            turn.note(f"The planner's plan could not be read ({str(exc)[:120]}); one agent takes the whole task.")
        if out.get("approach") != "team" or not out.get("tasks"):
            if allow_direct:
                return False
            out["tasks"] = [{"id": "t1", "name": "Agent", "goal": self.overall, "done_when": "the request is answered"}]
        self.final_hint = (out.get("final") or "").strip()
        self.chain = self.sequence and not self.roster
        stated = sum(len(t.get("depends_on") or []) for t in out["tasks"])
        self.add(out["tasks"][:self.max_tasks])
        if not self.tasks:
            return False
        found = sum(len(t["depends_on"]) for t in self.tasks.values()) - stated
        reason = (out.get("reason") or "").strip()
        n = len(self.tasks)
        self.logline(f"Planned {n} task{'s' if n > 1 else ''}"
                     + (" to run one after another" if self.sequence and n > 1 else "")
                     + (f": {reason}" if reason else ".")
                     + (f" Added {found} link{'s' if found != 1 else ''} between steps that the plan implied."
                        if found > 0 else ""))
        return True

    async def approve_plan(self):
        """Settings → Teams → 'Ask me before a plan starts'. Scheduled and auto-approved chats start right away."""
        turn = self.turn
        if not self.db.setting("plan_approval", False) or turn.auto_approve or turn.chat.get("origin") == "schedule":
            return
        lines = []
        for i, t in enumerate(self.tasks.values(), 1):
            f = t.get("forecast") or {}
            lines.append(f"{i}. {t['name']} — {_clip(t['goal'], 220)}"
                         + (f"\n   after {', '.join(t['depends_on'])}" if t["depends_on"] else "")
                         + (f"\n   about {pretty_tokens(f['tokens'])} tokens, {pretty_seconds(f['seconds'])}"
                            if f else ""))
        tool = Tool("run_plan", "", {}, None, "team", changes=True, label="Start this plan")
        if not await turn.approve(tool, {"steps": [t["name"] for t in self.tasks.values()]},
                                  "Run this plan?\n\n" + "\n".join(lines)):
            from .runner import TurnStopped
            raise TurnStopped("You declined the plan, so nothing was run. Change the request, or turn off "
                              "'Ask me before a plan starts' in Settings → Teams.")

    # ------------------------------------------------------------------ forecasting and sizing
    def harness_of(self, t):
        return self.app.harnesses.get(t.get("harness") or "agent") or {"id": "agent", "mode": "agent"}

    def reply_limit(self, t):
        return int((t.get("limits") or {}).get("max_reply_tokens") or self.harness_of(t).get("max_reply_tokens")
                   or self.db.setting("max_reply_tokens", 2048))

    def dep_budget(self, t):
        """Characters of each earlier result a step is given, so its brief fits the model's context."""
        n = max(1, len(t["depends_on"]))
        model = t.get("model") or self.turn.model_id
        top = 32768 if is_cloud(model) else self.app.runtime.ceiling(model)
        room = max(1200, top - 3000 - self.reply_limit(t) - 2500)
        return max(1200, min(9000 // n, room * 3 // n))

    async def forecast(self, t, brief=None):
        """Predict a step's tokens, context and time; raise its model's need for the run."""
        model = t.get("model") or self.turn.model_id
        fc = self.app.forecaster
        if brief is None:       # before it can start: its own text plus room for the results it will be given
            tokens, exact = estimate_tokens(self.brief(t, planned=True)), False
            tokens += sum(min(self.dep_budget(t) // 3, 1500) for _ in t["depends_on"])
        else:
            tokens, exact = await fc.count(brief, model)
        snaps = self.app.runtime.snapshots       # a saved snapshot means the system prompt and tools are not read
        saved = not is_cloud(model) and snaps.enabled() and any(snaps.folder(model).glob("*.bin"))
        f = fc.step(self.harness_of(t), model, tokens, self.reply_limit(t), exact=exact,
                    cached_fixed=saved or (model, t.get("harness")) in self.warm)
        t["forecast"] = f
        t["brief_tokens"] = tokens
        if self.fit is not None and not is_cloud(model):
            self.fit.want(model, f["ctx"])
        return f

    async def size_run(self, messages):
        """Forecast every step and decide the context each model is loaded with for this run."""
        turn = self.turn
        if self.fit is not None and not is_cloud(turn.model_id):
            lead = sum(estimate_tokens(m["content"] if isinstance(m.get("content"), str) else
                                       json.dumps(m.get("content"), default=str)) for m in messages)
            self.fit.want(turn.model_id, int((lead + 3500 + min(turn.max_reply, 2048)) * 1.3))
        for t in self.tasks.values():
            if t["status"] == "waiting":
                await self.forecast(t)
        if self.fit is None:
            return
        said = []
        for model in dict.fromkeys(t.get("model") or turn.model_id for t in self.tasks.values()):
            if is_cloud(model):
                continue
            ov = self.fit.overrides(self.app.runtime, model)
            top = self.app.runtime.ceiling(model)
            said.append(f"{self.model_name(model)} with a {ov['ctx']:,}-token context"
                        + (f" (its setting allows {top:,})" if top > ov["ctx"] else ""))
        total = sum((t.get("forecast") or {}).get("seconds", 0) for t in self.tasks.values())
        if said:
            self.logline("Forecast: this run needs " + "; ".join(said) + f". About {pretty_seconds(total)} of "
                         "model time.")

    # ------------------------------------------------------------------ running
    def deps_ready(self, t):
        return all(self.tasks.get(d, {}).get("status") == "done" for d in t["depends_on"])

    def deps_dead(self, t, _seen=None):
        """True when a step it needs failed or was cancelled, directly or further up the chain."""
        seen = _seen or set()
        seen.add(t["id"])
        for d in t["depends_on"]:
            dep = self.tasks.get(d)
            if not dep or d in seen:
                continue
            if dep["status"] in ("failed", "cancelled") or (dep["status"] == "waiting" and self.deps_dead(dep, seen)):
                return True
        return False

    def capacity(self, model):
        if is_cloud(model):
            return int(self.db.setting("cloud_max_parallel", 4))
        m = self.app.models.get_any(model) or {}
        return max(1, int((m.get("settings") or {}).get("parallel") or 1))

    def startable(self):
        running = [t for t in self.tasks.values() if t["status"] in ACTIVE or t["status"] == "review"]
        if self.sequence:
            # one step at a time, in plan order; a waiting step holds no model and no reply slot
            for t in self.tasks.values():
                if t["status"] != "waiting":
                    continue
                if not self.deps_ready(t):
                    missing = [d for d in t["depends_on"] if self.tasks.get(d, {}).get("status") != "done"]
                    t["wait_reason"] = ("cannot start: " if self.deps_dead(t) else "waiting for ") + ", ".join(missing)
                    continue
                if running:
                    t["wait_reason"] = f"next, after {running[0]['id']}"
                    return []
                t["wait_reason"] = None
                return [t]
            return []
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
                t["wait_reason"] = f"waiting for {self.model_name(model)} (raise its Simultaneous replies to run more at once)"
                continue
            t["wait_reason"] = None
            per_model[model] = per_model.get(model, 0) + 1
            if t.get("agent"):
                busy_agents.add(t["agent"])
            out.append(t)
        return out

    def brief(self, t, planned=False, redo=None):
        """What an agent is told when its step starts: only its goal and the results it builds on."""
        ids = list(self.tasks)
        where = f"step {ids.index(t['id']) + 1} of {len(ids)}" if t["id"] in ids else "one step"
        parts = [f"You do {where} of a plan that a planner runs. Other agents do the other steps."]
        if self.overall and self.overall.strip() != t["goal"].strip():
            parts.append(f"Overall request (for context only — do just your task): {_clip(self.overall, 700)}")
        parts.append(f"Your task ({t['id']} “{t['name']}”): {t['goal']}")
        if t.get("done_when"):
            parts.append(f"Done when: {t['done_when']}")
        if t.get("expects"):
            parts.append("Save these files in the shared team folder: " + ", ".join(t["expects"]))
        deps = [self.tasks[d] for d in t["depends_on"] if d in self.tasks]
        if deps and not planned:
            budget = self.dep_budget(t)
            parts.append("Results you build on (accepted by the planner):\n\n" + "\n\n".join(
                f"### {d['id']} {d['name']}" + (" (incomplete: " + _clip(d.get('note'), 200) + ")" if d.get("partial") else "")
                + f"\n{_clip(d['output'], budget)}"
                + (f"\nFull text: {d['result_file']}" if d.get("result_file") and len(d["output"] or "") > budget else "")
                + (f"\nFiles: {', '.join(d['files'][:12])}" if d.get("files") else "") for d in deps))
        if redo:
            parts.append(redo)
        parts.append(f"Shared team folder: {self.dir} — save files other agents or the final answer need there.\n"
                     "Use only the results above and what you find or compute with your tools. If something you need "
                     "is not there, say exactly what is missing and stop: never rebuild it from memory or from "
                     "earlier chats.\n"
                     "Work efficiently: use tools only as needed. If code fails, read the error, fix the cause and run "
                     "it again; check outputs before you rely on them. Use ask_lead only if you are truly blocked.\n"
                     "Finish with your result: what you did, the result itself (facts, numbers, sources, file paths), "
                     "and whether 'done when' is met." + self.length_hint(t))
        return "\n\n".join(parts)

    def length_hint(self, t):
        """Writing is the slowest thing a local model does, so a result should be as long as it needs to be and
        no longer: about `writing_budget_seconds` of writing at this computer's speed, long material in a file."""
        budget = float(self.db.setting("writing_budget_seconds", 180) or 0)
        model = t.get("model") or self.turn.model_id
        if budget <= 0 or is_cloud(model):
            return ""
        _, writes = self.app.forecaster.speeds(model)
        words = int(max(120, min(700, writes * budget * 0.7)) // 10 * 10)
        return (f" Keep the reply under about {words} words; put anything longer (full texts, tables, code) in a "
                "file in the shared team folder and give its path.")

    def _files_since(self, since):
        try:
            return [str(p) for p in sorted(self.dir.rglob("*")) if p.is_file() and p.stat().st_mtime >= since - 1
                    and p.parent.name != "results"][:30]
        except OSError:
            return []

    async def admit(self, t, model):
        """Compare what loading the step's model needs with free memory; shrink the context if it would not fit."""
        if self.fit is None or is_cloud(model):
            return
        rt = self.app.runtime
        ov = self.fit.overrides(rt, model)
        mem = rt.memory(model, ov)
        if not mem["need"] or mem["need"] <= (mem["free"] + mem["reclaimable"]) * 0.9:
            return
        floor = max(4096, int(((t.get("forecast") or {}).get("prompt", 3000) + 1024) * 1.15))
        ctx = ov["ctx"]
        while ctx // 2 >= floor:
            ctx //= 2
            self.fit.cap[model] = ctx
            ov = self.fit.overrides(rt, model)
            mem = rt.memory(model, ov)
            if mem["need"] <= (mem["free"] + mem["reclaimable"]) * 0.9:
                self.logline(f"Memory is short: {self.model_name(model)} is loaded with a {ov['ctx']:,}-token context "
                             f"for {t['id']} (needs {mem['need'] / 2**30:.1f} GB, {mem['free'] / 2**30:.1f} GB free).")
                return
        self.logline(f"Memory is short for {t['id']}: {self.model_name(model)} needs about {mem['need'] / 2**30:.1f} GB "
                     f"and {mem['free'] / 2**30:.1f} GB is free. Trying anyway; close other apps if it fails.")

    async def start(self, t, text=None, fresh=False):
        from .app_state import clean_limits
        parent = self.turn.chat
        model = t.get("model") or self.turn.model_id
        if model and not self.app.models.get_any(model):
            model = self.turn.model_id
        t["model"] = model
        if fresh:                                   # a new agent with an empty context
            if t.get("chat_id"):
                self.app.team_of_chat.pop(t["chat_id"], None)
                self.app.run_fit.pop(t["chat_id"], None)
            if t.get("agent") and self.agent_chats.get(t["agent"]) == t.get("chat_id"):
                self.agent_chats.pop(t["agent"], None)
            t["chat_id"] = None
        chat_id = t.get("chat_id") or (self.agent_chats.get(t["agent"]) if t.get("agent") else None)
        message = text or self.brief(t)
        limits = dict(t.get("limits") or {})
        if self.lean and not limits.get("tools") and (t.get("harness") or "agent") == "agent" and not t.get("full_kit"):
            groups = self.harness_of(t).get("tools")
            limits["tools"] = lean_kit(f"{t['goal']} {t.get('done_when') or ''}", groups)
        t["kit"] = limits.get("tools")
        if not chat_id:
            name = t.get("agent") or t["name"]
            chat = self.app.create_chat(project_id=parent.get("project_id"), title=f"{name}: {t['goal'][:60]}",
                                        model=model, harness=t.get("harness") or "agent",
                                        connectors=parent.get("connectors"),
                                        folders=self.turn.folders() + [str(self.dir)], origin="agent",
                                        settings=clean_limits(limits), parent_chat_id=parent["id"],
                                        agent_name=name)
            chat_id = chat["id"]
            if t.get("agent"):
                self.agent_chats[t["agent"]] = chat_id
            await self.forecast(t, message)
        else:
            self.db.update("chats", chat_id, {"model": model, "harness": t.get("harness") or "agent"})
        await self.admit(t, model)
        t["chat_id"] = chat_id
        self.app.team_of_chat[chat_id] = (self, t["id"])
        if self.fit is not None:
            self.app.run_fit[chat_id] = self.fit
        if not is_cloud(model):
            self.used.add(model)
        t["attempts"] += 1
        t["status"] = "running"
        t["started_at"] = time.time()
        t["timed_out"] = False
        # a step may run this long before it is stopped: generous against the forecast and the reply time limit
        minutes = float((t.get("limits") or {}).get("time_limit_minutes") or self.harness_of(t).get("time_limit_minutes")
                        or self.db.setting("turn_time_limit_minutes", 30) or 30)
        t["deadline"] = time.monotonic() + max((minutes + 10) * 60, (t.get("forecast") or {}).get("seconds", 0) * 4)
        job = self.app.start_turn(chat_id, message, kind="agent",
                                  payload={"auto_approve": self.turn.auto_approve})
        t["job_id"] = job["id"]
        self.handles[t["id"]] = self.app.spawn(self._watch(t, job["id"]))
        self.save()
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
        tokens = int(stats.get("prompt_tokens") or 0) + int(stats.get("cached_tokens") or 0) + \
            int(stats.get("output_tokens") or 0)
        used = t.get("used") or {"tokens": 0, "seconds": 0}
        t["used"] = {"tokens": used["tokens"] + tokens, "seconds": round(used["seconds"] + float(stats.get("seconds") or 0)),
                     "peak": max(int(used.get("peak") or 0), int(stats.get("peak_prompt") or 0)),
                     # the timeline of a step: what was read, what came from the cache, what was written
                     "read": int(used.get("read") or 0) + int(stats.get("prompt_tokens") or 0),
                     "cached": int(used.get("cached") or 0) + int(stats.get("cached_tokens") or 0),
                     "written": int(used.get("written") or 0) + int(stats.get("output_tokens") or 0),
                     "calls": int(used.get("calls") or 0) + int(stats.get("model_calls") or 0),
                     "tools": int(used.get("tools") or 0) + sum(1 for s in meta.get("steps", []) if s.get("type") == "tool")}
        ok = not error and bool(t["output"])
        try:
            if t["attempts"] == 1 or t.get("_fresh"):   # later rounds in the same chat start from a longer prompt
                self.app.forecaster.record(t.get("harness") or "agent", t["model"], stats,
                                           int(t.get("brief_tokens") or 0), ok)
            self.warm.add((t["model"], t.get("harness")))
        except Exception as exc:
            self.app.log(f"learning from step {t['id']}: {type(exc).__name__}: {exc}")
        if t["status"] != "cancelled":
            t["status"] = "review"
            t["result_state"] = "stopped" if job.get("status") == "cancelled" else ("failed" if not ok else "done")
        await self.events.put(("finished", t["id"]))
        self.wake()

    def settle(self, t, status, note=None):
        t["status"] = status
        if note:
            t["note"] = note
        if status == "done":
            self._save_result(t)
        self.settled[t["id"]].set()
        if status in ("cancelled",) and t.get("job_id"):
            self.app.jobs.cancel(t["job_id"])
        if self.outcome == "done":             # (not while a stopped run is being wound down: its saved state stays)
            self.save()

    def _save_result(self, t):
        """Every accepted result is a file in the team folder: later steps can read all of it, and the run's
        state lives in files rather than in a growing conversation."""
        try:
            folder = self.dir / "results"
            folder.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", t["name"].lower()).strip("-")[:40] or "result"
            path = folder / f"{t['id']}-{slug}.md"
            path.write_text(f"# {t['id']} {t['name']}\n\nGoal: {t['goal']}\n\n"
                            + (f"Incomplete: {t.get('note')}\n\n" if t.get("partial") else "")
                            + (t.get("output") or ""), encoding="utf-8")
            t["result_file"] = str(path)
        except OSError:
            pass

    # ------------------------------------------------------------------ the loop of one step: check, then fix
    def code_check(self, t):
        """Checks that cost no model call. Returns the problems found (an empty list passes)."""
        problems = []
        if t.get("error"):
            problems.append(f"it ended with an error: {t['error'][:300]}")
        if not (t.get("output") or "").strip():
            problems.append("it gave no result")
        for name in t.get("expects") or []:
            p = Path(name)
            found = (p.is_absolute() and p.exists()) or (self.dir / name).exists() or \
                any(f.name.lower() == p.name.lower() for f in self.dir.rglob("*") if f.is_file())
            if not found:
                problems.append(f"the file {name} was not saved in the shared team folder ({self.dir})")
        return problems

    async def verify(self, t):
        """A short, strict check by the planner's model: does the result meet 'done when'? (met, what is missing)"""
        if not self.verify_results or not (t.get("done_when") or "").strip():
            return True, ""
        turn = self.turn
        self.checks += 1
        turn.status(f"Checking the result of {t['id']}…")
        ctx = 8192
        if turn.server:
            ctx = int(turn.server.plan.get("ctx") or 8192)
        room = max(1500, min(9000, (ctx - 1200) * 3))
        user = (f"Task: {_clip(t['goal'], 600)}\n\nAcceptance test (done when): {t['done_when']}\n\n"
                f"Result:\n{_clip(t['output'], room)}"
                + (f"\n\nFiles saved: {', '.join(t['files'][:12])}" if t.get("files") else ""))
        checker = self.check_model()
        try:
            if checker:                        # a smaller local model does the check (Settings → Teams)
                overrides = None
                if self.fit is not None:
                    self.fit.want(checker, 6000)
                    overrides = self.fit.overrides(self.app.runtime, checker)
                self.used.add(checker)
                async with self.app.runtime.lease(checker, on_wait=turn.status, overrides=overrides) as server:
                    out = await self.app.llm.json([{"role": "system", "content": VERIFY_SYSTEM},
                                                   {"role": "user", "content": user}], VERIFY_SCHEMA,
                                                  max_tokens=220, server=server)
            else:
                await turn.hold_model()
                out = (await turn.complete([{"role": "system", "content": VERIFY_SYSTEM},
                                            {"role": "user", "content": user}], schema=VERIFY_SCHEMA,
                                           max_tokens=220, temperature=0.1))["json"]
        except Exception as exc:               # the check itself failed: code checks passed, so accept and say so
            if isinstance(exc, asyncio.CancelledError):
                raise
            turn.app.log(f"checking {t['id']} failed: {type(exc).__name__}: {exc}")
            self.logline(f"The check of {t['id']} could not be made ({type(exc).__name__}); its result is accepted "
                         "on the code checks alone.")
            return True, ""
        finally:
            if not is_cloud(turn.model_id):
                await turn.drop_model()        # the next step gets the model
        return bool(out.get("met")), (out.get("reason") or "").strip()

    def check_model(self):
        """The model that checks results when it is not the planner's own: 'check_model' in Settings is empty
        (the planner's model), 'smallest' (the smallest installed local model, if smaller than the planner's),
        or a model's id. A small model checks in a fraction of the time; whether its verdicts are good enough
        on a given computer is what the evaluation (Settings) is for."""
        choice = self.db.setting("check_model", "") or ""
        if not choice or choice == "planner":
            return None
        rows = [r for r in self.db.all("SELECT id, size FROM models") if r.get("size")]
        mine = next((r["size"] for r in rows if r["id"] == self.turn.model_id), None)
        if choice == "smallest":
            rows = sorted((r for r in rows if r["id"] != self.turn.model_id), key=lambda r: r["size"])
            if rows and (mine is None or rows[0]["size"] < mine * 0.75 or is_cloud(self.turn.model_id)):
                return rows[0]["id"]
            return None
        return choice if any(r["id"] == choice for r in rows) and choice != self.turn.model_id else None

    def keep_best(self, t, problems, hard):
        """Remember the best attempt so far (no hard problem beats one with; then the longer result)."""
        score = (0 if hard else 1, len(t.get("output") or ""))
        best = t.get("best")
        if not best or score > best["score"]:
            t["best"] = {"score": score, "output": t.get("output") or "", "files": list(t.get("files") or []),
                         "problems": list(problems), "hard": hard}

    async def review(self, t):
        """A step's agent finished: accept it, send it round again, or stop the loop with the best attempt."""
        if t["status"] != "review":
            return
        state = t.get("result_state")
        if state == "stopped" and not t.get("timed_out"):
            self.settle(t, "failed", "stopped")
            self.logline(f"{t['id']} was stopped.")
            return
        kind = classify(t.get("error"), t.get("timed_out"))
        t["kind"] = kind
        problems = self.code_check(t) if not t.get("timed_out") else ["it ran past its time limit and was stopped"]
        hard = bool(problems)
        if not problems:
            met, reason = await self.verify(t)
            if met:
                t["kind"] = None
                self.settle(t, "done", None if t["attempts"] == 1 else f"passed in round {t['attempts']}")
                self.logline(f"Accepted {t['id']}" + (f" in round {t['attempts']}" if t["attempts"] > 1 else "") + ".")
                return
            problems = [reason or "it does not meet its 'done when'"]
        self.keep_best(t, problems, hard)
        why = "; ".join(problems)
        if t["attempts"] >= t["rounds"] or self.finished:
            return self.exhausted(t, why)
        repeated = bool(t["feedback"]) and same_objection(t["feedback"][-1], why)
        t["feedback"].append(why)
        t["note"] = why
        model = t["model"]
        if kind == "memory" and self.fit is not None and not is_cloud(model):
            now_ctx = self.fit.overrides(self.app.runtime, model)["ctx"]
            if now_ctx > 4096:
                self.fit.cap[model] = max(4096, now_ctx // 2)
                self.logline(f"{t['id']} ran out of memory; {self.model_name(model)} is reloaded with a "
                             f"{self.fit.cap[model]:,}-token context.")
        elif kind == "context" and self.fit is not None and not is_cloud(model):
            self.fit.cap.pop(model, None)
            self.fit.want(model, self.fit.tokens(model) * 2)
            self.logline(f"{t['id']} did not fit its context; {self.model_name(model)} gets a larger one "
                         f"({self.fit.overrides(self.app.runtime, model)['ctx']:,} tokens).")
        fresh = repeated or kind in ("timeout", "context", "memory")
        t["_fresh"] = fresh
        if fresh:
            t["full_kit"] = True              # a fresh attempt gets every tool of its harness
            if self.escalate and repeated:
                self.step_up(t)
            self.logline(f"Round {t['attempts'] + 1} of {t['rounds']} for {t['id']} with a fresh agent"
                         + (" (the same objection twice)" if repeated else f" ({kind})") + f": {why[:160]}")
            await self.start(t, self.brief(t, redo=(
                f"An earlier attempt at this task did not pass: {_clip(why, 500)}\n"
                "Take a different approach this time" +
                (", and keep it short: work in smaller pieces and read less at once." if kind in ("timeout", "context", "memory") else "."))),
                fresh=True)
        else:
            self.logline(f"Round {t['attempts'] + 1} of {t['rounds']} for {t['id']}: {why[:160]}")
            await self.start(t, "The planner reviewed your result and asks for this:\n" + _clip(why, 800)
                             + "\n\nFix it, check it, and reply with the complete corrected result.")

    def step_up(self, t):
        """A step that failed the same way twice moves to a stronger model, if one is installed and allowed."""
        strong, _why = self.app.router.pick("strategist", chat=self.turn.chat, project=self.turn.project)
        names = {c["id"]: c for c in self.app.router.local()}
        if not strong or strong == t["model"]:
            return
        if is_cloud(strong):
            if "files" in self.turn.private:   # the step may hold your files: keep it on this computer
                return
        elif names.get(strong, {}).get("strength", 0) <= names.get(t["model"], {}).get("strength", 0):
            return
        self.logline(f"{t['id']} moves from {self.model_name(t['model'])} to {self.model_name(strong)}: "
                     "the same objection came twice.")
        t["model"] = strong

    def exhausted(self, t, why):
        """The rounds are used up: keep the best attempt if it is usable, else the step fails."""
        best = t.get("best") or {}
        if best and not best.get("hard") and best.get("output"):
            t["output"], t["files"] = best["output"], best["files"] or t.get("files") or []
            t["partial"] = True
            self.settle(t, "done", f"best of {t['attempts']} rounds; still open: {_clip('; '.join(best['problems']), 240)}")
            self.logline(f"Kept the best attempt of {t['id']} after {t['attempts']} rounds (marked incomplete).")
        else:
            self.settle(t, "failed", f"not done after {t['attempts']} round{'s' if t['attempts'] > 1 else ''}: {_clip(why, 240)}")
            self.logline(f"{t['id']} failed after {t['attempts']} round{'s' if t['attempts'] > 1 else ''}: {why[:160]}")

    def watch_limits(self):
        """Stop a step that runs far past its forecast (time spent paused or waiting for you does not count)."""
        now_, gap = time.monotonic(), time.monotonic() - self._tick
        self._tick = now_
        for t in self.tasks.values():
            if t["status"] != "running" or not t.get("deadline"):
                continue
            job = self.db.get("jobs", t.get("job_id") or "") or {}
            if job.get("status") == "waiting" or (t.get("chat_id") and is_paused(self.app, t["chat_id"])) \
                    or is_paused(self.app, self.turn.chat["id"]):
                t["deadline"] += gap
            elif now_ > t["deadline"]:
                t["timed_out"] = True
                self.logline(f"{t['id']} ran far past its forecast and was stopped.")
                self.app.jobs.cancel(t["job_id"])

    async def free_unneeded(self):
        """Unload models no remaining step uses (the planner's own model stays for its checks and the answer)."""
        if not self.used:
            return
        needed = {t.get("model") or self.turn.model_id for t in self.tasks.values() if t["status"] not in SETTLED}
        needed.add(self.turn.model_id)
        spare = {m for m in self.used if m not in needed}
        if not spare:
            return
        gone = await self.app.runtime.release(only=spare)
        self.used -= set(gone)
        if gone:
            self.logline("Unloaded " + ", ".join(self.model_name(m) for m in gone) + ": no remaining step uses it.")

    def close(self):
        """After the final answer: forget the run's sizing; say which models the turn should unload when it ends."""
        for chat_id in [c for c, f in list(self.app.run_fit.items()) if f is self.fit and c != self.turn.chat["id"]]:
            self.app.run_fit.pop(chat_id, None)
        spare = {m for m in self.used if m != self.turn.model_id}
        if self.turn.chat.get("origin") == "schedule" or self.db.setting("unload_after_run", False):
            spare.add(self.turn.model_id)       # nobody is waiting to write a follow-up
        self.turn.unload_after |= {m for m in spare if m and not is_cloud(m)}

    # ------------------------------------------------------------------ the planner's wider decisions
    def board_text(self, fresh_ids):
        lines = []
        for t in self.tasks.values():
            state = t["status"]
            if state == "waiting":
                missing = [d for d in t["depends_on"] if self.tasks.get(d, {}).get("status") != "done"]
                state = f"waiting for {', '.join(missing)}" if missing else "ready to start"
            elif state == "running":
                state = f"running ({t.get('progress') or 'working'})"
            elif state in ("failed", "cancelled") and t.get("note"):
                state = f"{state} ({_clip(t['note'], 160)})"
            lines.append(f"- {t['id']} “{t['name']}” [{t.get('role') or 'worker'}, {t.get('harness')}"
                         f"{', agent ' + t['agent'] if t.get('agent') else ''}] — {state}"
                         + (f"; depends on {', '.join(t['depends_on'])}" if t['depends_on'] else "")
                         + (f"; attempt {t['attempts']}" if t['attempts'] > 1 else ""))
        return "\n".join(lines)

    def fresh_text(self, questions, notes):
        out = []
        for tid, q in questions:
            out.append(f"### Question from {tid} “{self.tasks[tid]['name']}” (answer it with do=answer)\n{q}")
        out += notes
        return "\n\n".join(out)

    async def checkpoint(self, messages, questions=(), notes=()):
        """One planner call for what code cannot decide: an agent's question, or a run that is stuck."""
        turn = self.turn
        self.checks += 1
        out = {"actions": []}
        try:
            await turn.hold_model()
            turn.status("The planner is deciding what to do…")
            prompt = (
                f"You lead a team working on my request. Board:\n{self.board_text([])}\n\nNew since your last check:\n\n"
                f"{self.fresh_text(questions, notes)}\n\n"
                "Decide what happens next. You may: 'answer' a question (text), 'message' a running task with "
                "information it needs, 'pause' or 'resume' a task, 'cancel' a task that is no longer needed, 'update' "
                "a task that has not started (new goal, done_when or depends_on), 'add' tasks (task = new id, plus "
                "name, goal, done_when, depends_on) — for example a different way to get what a failed task could "
                "not — or 'finish' when nothing more can usefully be done. Only give actions that are needed. "
                "JSON only.")
            out = (await turn.complete(messages + [{"role": "user", "content": prompt}],
                                       schema=check_schema(self.harness_ids, self.roles(), self.agents()),
                                       max_tokens=900, temperature=0.2))["json"]
        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            turn.app.log(f"planner checkpoint failed: {type(exc).__name__}: {exc}")
            out = {"actions": []}
        finally:
            if not is_cloud(turn.model_id):
                await turn.drop_model()   # workers on the same local model get it back
        return await self.apply(out, {tid: self.questions[tid] for tid, _ in questions if tid in self.questions})

    async def apply(self, out, questions):
        said = []
        for a in out.get("actions") or []:
            kind, tid, text = a.get("do"), str(a.get("task") or ""), (a.get("text") or "").strip()
            t = self.tasks.get(tid)
            if kind == "finish":
                open_ = [x for x in self.tasks.values() if x["status"] in ("running", "review") or
                         (x["status"] == "waiting" and not self.deps_dead(x))]
                if open_:        # a small planner likes to stop early: steps that can still run are run
                    said.append("wanted to finish, but " + ", ".join(x["id"] for x in open_) + " can still run")
                    continue
                self.finished = True
                said.append("finished the work")
                continue
            if kind == "add":
                if len(self.tasks) >= self.max_tasks * 2:
                    continue
                before = set(self.tasks)
                chain, self.chain = self.chain, False      # an added step needs only what it says it needs
                self.add([{**a, "id": tid or None}])
                self.chain = chain
                new = [x for x in self.tasks if x not in before]
                for n in new:
                    await self.forecast(self.tasks[n])
                if new:
                    said.append("added " + ", ".join(f"{n} “{self.tasks[n]['name']}”" for n in new))
                continue
            if not t:
                continue
            if kind == "message" and t["status"] == "running" and text:
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
                said.append(f"cancelled {tid}")
            elif kind == "update" and t["status"] in ("waiting", "paused"):
                for key in ("goal", "done_when", "name"):
                    if a.get(key):
                        t[key] = str(a[key])[:1500]
                if text and not a.get("goal"):
                    t["goal"] = f"{t['goal']}\n\nNote from the planner: {text}"
                if isinstance(a.get("depends_on"), list):
                    t["depends_on"] = [d for d in a["depends_on"] if d in self.tasks and d != tid]
                    self._order()
                if a.get("role") in self.roles() and a["role"] != t.get("role"):
                    t["role"], t["model"] = a["role"], None
                    self.turn.assign_models([t])
                said.append(f"updated {tid}")
            elif kind == "answer" and tid in questions and text:
                fut = questions.pop(tid)
                if not fut.done():
                    fut.set_result(text)
                said.append(f"answered {tid}")
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
        """Run the plan until every step is settled. Returns the accepted steps."""
        turn, app = self.turn, self.app
        app.orchestras[turn.chat["id"]] = self
        turn.steps.append(self.step)
        try:
            await self.size_run(messages)
            if not self.resumes:
                await self.approve_plan()
            self.save()
        except BaseException:
            app.orchestras.pop(turn.chat["id"], None)
            raise
        if not is_cloud(turn.model_id):
            await turn.drop_model()
        self.publish()
        stalled_once = False
        try:
            while not self.finished:
                if time.monotonic() - self.started > self.time_limit:
                    turn.note("The run reached its time limit; the planner writes up what is done.")
                    break
                batch = []
                while not self.events.empty():
                    batch.append(self.events.get_nowait())
                for e in batch:
                    if e[0] == "finished":
                        await self.review(self.tasks[e[1]])
                        self.publish()
                questions = [(e[1], e[2]) for e in batch if e[0] == "question"
                             and e[1] in self.questions and not self.questions[e[1]].done()]
                if questions:
                    if self.checks >= self.max_checks:
                        await self.apply({"actions": []}, {tid: self.questions[tid] for tid, _ in questions})
                    else:
                        await self.checkpoint(messages, questions)
                await self.free_unneeded()
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
                        turn.status("The run is paused. Press Resume to continue.")
                        await self._wake.wait()
                        continue
                    blocked = [t for t in pending if t["status"] == "waiting" and self.deps_dead(t)]
                    held = [t for t in pending if t["status"] == "paused"]
                    if stalled_once or self.checks >= self.max_checks:
                        for t in pending:
                            self.settle(t, "cancelled", "could not start: a step it needs failed or was cancelled"
                                        if t in blocked else "left paused")
                        break
                    stalled_once = True
                    note = ("### Stalled\nNothing is running. " +
                            (f"These tasks cannot start because a task they need failed or was cancelled: "
                             f"{', '.join(t['id'] for t in blocked)}. " if blocked else "") +
                            (f"These tasks are paused: {', '.join(t['id'] for t in held)}. " if held else "") +
                            "Add a task that gets what is missing another way and update the blocked tasks to depend "
                            "on it, or resume or cancel them, or finish.")
                    await self.checkpoint(messages, notes=[note])
                    continue
                stalled_once = False
                done = sum(t["status"] in SETTLED for t in self.tasks.values())
                now_running = running[0] if running else None
                turn.status(f"Step {min(done + 1, len(self.tasks))} of {len(self.tasks)}"
                            + (f": {now_running['name']}" if now_running else ""))
                self.watch_limits()
                if self.events.empty():
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
                    if self.events.empty():
                        self.publish()
                elif not self.sequence:
                    await asyncio.sleep(0.5)          # results that arrive together are handled together
        except asyncio.CancelledError:
            self.outcome = "stopped"
            for t in self.tasks.values():
                if t.get("job_id") and t["status"] in ACTIVE:
                    app.jobs.cancel(t["job_id"])
            raise
        except BaseException:
            self.outcome = "interrupted"
            raise
        finally:
            if self.outcome != "done":               # keep the state as it was, so the run can be continued
                self.save(self.outcome)
            for t in self.tasks.values():
                if t["status"] == "review":          # finished, but the run stopped before it was checked
                    self.settle(t, "done" if t.get("result_state") == "done" else "failed")
                elif t["status"] not in SETTLED:
                    self.settle(t, "cancelled", "the run ended before this step")
                if t.get("chat_id"):
                    app.team_of_chat.pop(t["chat_id"], None)
                    if is_paused(app, t["chat_id"]):
                        resume_chat(app, t["chat_id"])
            for fut in self.questions.values():
                if not fut.done():
                    fut.set_result("The team has finished.")
            app.orchestras.pop(turn.chat["id"], None)
            if self.outcome == "done":
                self.save("done")
            self.publish()
        try:
            await self.free_unneeded()
        except Exception as exc:
            app.log(f"unloading after a run: {type(exc).__name__}: {exc}")
        return [t for t in self.tasks.values() if t["status"] == "done"]

    def results_text(self, budget):
        out = []
        for t in self.tasks.values():
            if t["status"] == "done":
                out.append(f"### {t['id']} {t['name']}" + (f" — incomplete: {t.get('note')}" if t.get("partial") else "")
                           + f"\n{_clip(t['output'], budget)}" +
                           (f"\nFiles: {', '.join(t['files'][:15])}" if t.get("files") else ""))
            else:
                out.append(f"### {t['id']} {t['name']} — {t['status']}: {t.get('note') or t.get('error') or ''}")
        return "\n\n".join(out)

    async def final(self, messages):
        """The planner writes the answer. If that call fails, the results are put together in code instead, so a
        finished run is never lost to one bad call."""
        turn = self.turn
        done = [t for t in self.tasks.values() if t["status"] == "done"]
        open_ = [t for t in self.tasks.values() if t["status"] != "done" or t.get("partial")]
        try:
            await turn.hold_model()
            ctx = int(turn.server.plan.get("ctx") or 8192) if turn.server else 8192
            budget = max(1500, int((ctx - turn.max_reply - 3000) * 3 / max(1, len(self.tasks) + 2)))
            turn.status("The planner is writing the answer…")
            messages.append({"role": "user", "content": (
                f"Your team has finished. Results:\n\n{self.results_text(budget)}\n\nShared team folder: {self.dir}\n\n"
                "Now write the final answer to my request" + (f" ({self.final_hint})" if self.final_hint else "") +
                ". Keep facts, numbers, sources and file paths. Say plainly which steps failed, were not run or are "
                "incomplete, and do not present their missing results as done.")})
            turn.text = []
            await turn.stream_text(messages, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            turn.app.log(f"writing the final answer failed: {type(exc).__name__}: {exc}")
            text = (f"The planner could not write the final answer ({str(exc)[:160]}), so here are the results of "
                    f"the steps as they are. {len(done)} of {len(self.tasks)} steps were accepted"
                    + (f"; not done or incomplete: {', '.join(t['id'] + ' ' + t['name'] for t in open_)}" if open_ else "")
                    + f".\n\n{self.results_text(6000)}\n\nShared team folder: {self.dir}")
            turn.steps.append({"type": "text", "text": text})
            turn.text = [text]
            turn.emit("token", text=text)

    # ------------------------------------------------------------------ tools for the agents in the team
    async def ask(self, tid, question):
        fut = asyncio.get_running_loop().create_future()
        self.questions[tid] = fut
        await self.events.put(("question", tid, question))
        self.wake()
        return fut


def team_tools(orch, tid, turn):
    """Added to an agent's tools while it works in a run. (There is no 'wait for a teammate' any more: a step
    starts only when the results it needs exist, so an agent never sits loaded while it waits.)"""
    async def team_board(ctx):
        lines = [orch.board_text([])]
        for t in orch.tasks.values():
            if t["status"] == "done" and t["id"] != tid:
                lines.append(f"\n### {t['id']} {t['name']} (accepted)\n{_clip(t['output'], 1500)}"
                             + (f"\nFull text: {t['result_file']}" if t.get("result_file") else ""))
        return "\n".join(lines) + f"\n\nShared team folder: {orch.dir}"

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
        Tool("team_board", "See the plan's steps, their status and the accepted results of the other agents.",
             S({}, []), team_board, "team", label="Team board", summary=lambda a: "Read the team board"),
        Tool("ask_lead", "Ask the planner a question when you are blocked (missing information or a decision "
             "only the planner can make).", S({"question": {"type": "string"}}, ["question"]), ask_lead, "team",
             label="Ask the planner", summary=lambda a: f"Ask the planner: {a.get('question', '')[:200]}"),
    ]
