"""Long-running tasks (2.9.0): work that goes on until a problem is solved, not until a plan is ticked off.

A planned run (orchestrator.py) writes its whole plan first and then carries it out. That fits a request whose
steps are known in advance. It does not fit research, building something, securing a system or any problem
where what to do next depends on what was just found. A long-running task ("mission" in the code) works like
a relay instead:

  Understanding  The planner keeps a short written understanding of the problem: what it is, what is known,
                 what the approach is. It is rewritten after every step and given to every agent, so the state
                 of the work lives in a few hundred words and not in a conversation that grows for hours.
  One step       The planner gives the next step (sometimes the next two) to an agent: a goal, a test for the
                 result, and which kind of agent does it. It may create a new kind of agent when none fits.
                 Agents run one at a time, each in a fresh chat, so a small model starts every step with a
                 small context; a model is loaded for the step that needs it and unloaded when another is.
  Handoff        Every agent ends with a handoff: what it did, what it found that changes the picture, and
                 what the next agent should do. The next agent gets that handoff, the understanding and one
                 line per earlier step. Its result is checked like any step of a run (code checks, then the
                 strict check against "done when", up to the step's rounds).
  Next move      The planner reads only handoffs. From the latest one it decides: continue with a next step,
                 change the plan (the outline of what it expected is replaced and the change is logged),
                 ask the user, or finish. If the planner cannot be reached, the step the last agent proposed
                 in its handoff is run, so the relay keeps moving.
  Limits         A task has limits (hours of work, steps, tokens, online cost), set when it is set up. Reaching
                 one pauses the task between two steps and tells the user; nothing is lost and the user can
                 raise the limit and continue. A question to the user pauses it the same way.
  Always on      The state is saved after every change (table `runs`, kind "mission"). A task that was cut
                 off by a restart or a crash continues by itself when the app starts (Settings), a stopped or
                 paused one continues from the chat. A new message in the task's chat reaches the planner at
                 its next move.

The set-up (who is on the team, the limits, what may be done without asking) can be written by a planner too:
plan_setup turns one sentence into a proposal the user confirms once. Scheduled tasks use the same set-up
(scheduler.py), so a schedule can start a long-running task with its team and limits already in place."""
import asyncio
import re
import time

from . import recovery
from .meter import purpose as usage_purpose
from .db import now
from .forecast import pretty_seconds, pretty_tokens
from .orchestrator import SETTLED, Orchestra, _clip, same_objection
from .providers import is_cloud

DEFAULT_LIMITS = {"hours": 8.0, "steps": 40, "step_minutes": 45, "tokens": 0, "cost": 0.0}
LIMIT_RANGE = {"hours": (0.1, 240.0), "steps": (1, 400), "step_minutes": (5, 480), "tokens": (0, 10**9),
               "cost": (0.0, 10000.0)}
# What a task can be allowed to do without asking, by tool group (the label is what the user reads).
ALLOWABLE = {
    "files": "create, change and delete files in its folders",
    "python": "run Python code and commands",
    "browser": "click, type and choose on websites",
    "connector": "use the actions of its connectors",
    "skills": "save skills",
    "automation": "create scheduled tasks and harnesses",
}
OPEN = ("paused", "stopped", "interrupted")
NOTHING = re.compile(r"^\W*(nothing|none|no\b|n/?a|not applicable|-+|—+)", re.I)
HANDOFF = re.compile(r"(?:^|\n)#{0,4}\s*\**handoff\**:?\s*\n(.*)\Z", re.I | re.S)
FIELD = re.compile(r"^\s*[-*]?\s*\**(done|found|failed|next)\**\s*:\s*\**[ \t]*", re.I | re.M)


# ---------------------------------------------------------------------- the set-up of a task
def clean_limits(values, base=None):
    out = dict(base or DEFAULT_LIMITS)
    for key, (low, high) in LIMIT_RANGE.items():
        value = (values or {}).get(key)
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number <= 0 and key in ("tokens", "cost"):
            out[key] = 0                                  # 0 = no limit of this kind
            continue
        number = max(low, min(high, number))
        out[key] = number if key in ("hours", "cost") else int(number)
    return out


def default_limits(db):
    return clean_limits({"hours": db.setting("mission_hours", DEFAULT_LIMITS["hours"]),
                         "steps": db.setting("mission_steps", DEFAULT_LIMITS["steps"]),
                         "step_minutes": db.setting("mission_step_minutes", DEFAULT_LIMITS["step_minutes"])})


def loop_harnesses(app):
    """Harnesses an agent of a task can work with (not teams or tasks themselves, not pipelines)."""
    return [h for h in app.harnesses.all() if h.get("type") == "loop" and h.get("enabled", True)
            and h.get("mode") not in ("team", "mission")]


def clean_setup(setup, app):
    """A set-up as the app uses it, whatever a model or a form sent: known harnesses, limits within range,
    only permissions that exist."""
    from .runner import ROLES
    setup = setup if isinstance(setup, dict) else {}
    ids = [h["id"] for h in loop_harnesses(app)]
    team, seen = [], set()
    for member in (setup.get("team") or [])[:8]:
        if not isinstance(member, dict):
            continue
        name = " ".join(str(member.get("name") or "").split())[:30]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        model = member.get("model") or None
        if model and not str(model).startswith("role:") and model != "auto" and not app.models.get_any(model):
            model = None
        team.append({"name": name, "harness": member.get("harness") if member.get("harness") in ids else "agent",
                     "role": member.get("role") if member.get("role") in ROLES else None,
                     "about": " ".join(str(member.get("about") or "").split())[:200], "model": model})
    if not team:
        team = [{"name": "Agent", "harness": "agent", "role": None, "model": None,
                 "about": "general work with files, the web, the browser, code and commands"}]
    allow = [g for g in dict.fromkeys(setup.get("allow") or []) if g in ALLOWABLE]
    return {"name": " ".join(str(setup.get("name") or "").split())[:70],
            "goal": str(setup.get("goal") or "").strip()[:4000],
            "done_when": " ".join(str(setup.get("done_when") or "").split())[:400],
            "kind": "reply" if setup.get("kind") == "reply" else "mission",
            "team": team, "limits": clean_limits(setup.get("limits"), default_limits(app.db)), "allow": allow,
            "outline": [" ".join(str(x).split())[:160] for x in (setup.get("outline") or []) if str(x).strip()][:6],
            "when": " ".join(str(setup.get("when") or "").split())[:80]}


def setup_schema(harness_ids, scheduled):
    from .runner import ROLES
    props = {
        "name": {"type": "string", "maxLength": 60},
        "goal": {"type": "string", "maxLength": 1500},
        "done_when": {"type": "string", "maxLength": 300},
        "kind": {"enum": ["mission", "reply"]},
        "team": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
            "type": "object", "required": ["name", "harness", "about"], "properties": {
                "name": {"type": "string", "maxLength": 30}, "harness": {"enum": list(harness_ids)},
                "role": {"enum": list(ROLES)}, "about": {"type": "string", "maxLength": 160}}}},
        "limits": {"type": "object", "required": ["hours", "steps"], "properties": {
            "hours": {"type": "number"}, "steps": {"type": "integer"}, "step_minutes": {"type": "integer"}}},
        "allow": {"type": "array", "maxItems": len(ALLOWABLE), "items": {"enum": list(ALLOWABLE)}},
        "outline": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 140}}}
    required = ["name", "goal", "done_when", "kind", "team", "limits", "allow"]
    if scheduled:
        props["when"] = {"type": "string", "maxLength": 60}
        required.append("when")
    return {"type": "object", "required": required, "properties": props}


async def plan_setup(app, text, scheduled=False, model=None):
    """One sentence from the user → a proposed set-up: the task written out for a run nobody watches, the team,
    the limits and what it may do without asking. The user sees it and changes or confirms it; nothing runs
    before that. If no model can be asked, the proposal is made from defaults and says so."""
    from .runner import ROLE_HELP
    text = (text or "").strip()
    if not text:
        raise ValueError("Describe the task first")
    harnesses = loop_harnesses(app)
    options = "; ".join(f"{h['id']}: {(h.get('description') or '')[:70]}" for h in harnesses)
    allow = "; ".join(f"{k}: {v}" for k, v in ALLOWABLE.items())
    ids = app.connectors.usable()             # what is installed is what the task can use
    try:
        await app.connectors.learn(ids)
    except Exception as exc:
        app.log(f"a task's set-up: asking the connectors what they offer: {type(exc).__name__}: {exc}")
    services = app.connectors.digest(ids) if ids else ""
    prompt = (
        "Set up a task that AI agents will do on my computer" + (" on a schedule" if scheduled else "") +
        ", with nobody watching. A planner will run it one step at a time and give each step to one agent of "
        "the team. My request:\n\n" + text[:3000] + "\n\nAnswer in JSON:\n"
        "- name: a short title.\n"
        "- goal: my request written out so that it can be done without asking me anything: what to do, where "
        "(folders, sites, sources), what to produce, and what to do when something is missing or nothing is new.\n"
        "- done_when: how to tell that the whole task is finished.\n"
        "- kind: 'reply' when one agent can do it in one go (about ten tool steps); 'mission' when it needs "
        "several steps whose order depends on what is found (research, building or fixing something, "
        "checking and securing a system).\n"
        "- team: the kinds of agent the work needs, one to four. name: a role name such as Researcher, Coder, "
        f"Reviewer, Writer. harness: {options}. role: {ROLE_HELP} about: what this agent is for.\n"
        "- limits: hours of work and number of steps that are enough but not wasteful (a small task: 1 hour, "
        "8 steps; a large one: 8 hours, 40 steps), and step_minutes, the longest one step may take.\n"
        f"- allow: what it may do without asking me each time, only what the task really needs — {allow}. "
        "Leave out what it does not need.\n"
        "- outline: the phases you expect, in order (it may change as the work goes on).\n"
        + ("Connected services the agents will have tools for (what each promises):\n" + services + "\nIn "
           "'goal', name the service to use for what it offers: the web is only for what no service offers. "
           "If the task needs an action of a service that changes something, put 'connector' in allow.\n"
           if services else "")
        + ("- when: the schedule in plain words, such as 'every day at 08:30', 'weekdays at 9', 'every 3 "
           "hours', 'every monday at 10:00'.\n" if scheduled else "") + "JSON only.")
    out, note = {}, ""
    model_id = model if model and model != "auto" else None
    if not model_id:
        model_id, _ = app.router.pick("strategist")
    if not model_id:
        note = "No model is installed yet, so this set-up was made from defaults."
    else:
        try:
            async with app.runtime.lease(model_id, as_loaded=True) as server:    # no reload just for this
                out = await app.llm.json([
                    {"role": "system", "content": "You set up work for AI agents. Be concrete and economical."},
                    {"role": "user", "content": prompt}],
                    setup_schema([h["id"] for h in harnesses], scheduled), max_tokens=1400, temperature=0.2,
                    server=server)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app.log(f"planning a task's set-up failed: {type(exc).__name__}: {exc}")
            note = f"The planner could not be asked ({str(exc)[:140]}), so this set-up was made from defaults."
    if not isinstance(out, dict):
        out = {}
    setup = clean_setup({**out, "goal": out.get("goal") or text}, app)
    setup["name"] = setup["name"] or " ".join(text.split())[:60]
    setup["planned_by"] = (app.models.get_any(model_id) or {}).get("name") or model_id if out else None
    setup["note"] = note
    if scheduled:
        from .scheduler import describe, parse_when
        try:
            setup["cron"] = parse_when(setup["when"] or text)
        except ValueError:
            try:
                setup["cron"] = parse_when(text)
            except ValueError:
                setup["cron"] = "0 9 * * *"
                setup["note"] = (note + " " if note else "") + "No time was found in the request, so it is set to " \
                    "every day at 09:00: change it below."
        setup["when"] = describe(setup["cron"])
    return setup


def open_mission(app, chat_id):
    """The chat's long-running task if it can be continued (paused, stopped or cut off), else None."""
    row = app.db.one("SELECT * FROM runs WHERE chat_id=? AND kind='mission' ORDER BY created DESC, rowid DESC "
                     "LIMIT 1", (chat_id,))
    return row if row and row["status"] in OPEN else None


def summary(app, row):
    """One task for the list of long-running tasks."""
    state = row.get("state") or {}
    m = state.get("mission") or {}
    tasks = state.get("tasks") or []
    live = app.orchestras.get(row["chat_id"])
    if live is not None and live.id == row["id"] and hasattr(live, "mission_view"):
        m, tasks = {**m, **live.mission_view()}, list(live.tasks.values())
    chat = app.db.get("chats", row["chat_id"]) or {}
    last = (m.get("handoffs") or [None])[-1]
    return {"id": row["id"], "chat_id": row["chat_id"], "title": chat.get("title") or m.get("name") or "Task",
            "status": "running" if live is not None and live.id == row["id"] else row["status"],
            "created": row.get("created"), "updated": row.get("updated"),
            "request": (state.get("overall") or "")[:400], "understanding": m.get("understanding") or "",
            "outline": m.get("outline") or [], "limits": m.get("limits") or {}, "spent": m.get("spent") or {},
            "pause": m.get("pause"), "team": m.get("team") or [], "allow": m.get("allow") or [],
            "steps": len(tasks), "done": sum(t.get("status") == "done" for t in tasks),
            "failed": sum(t.get("status") == "failed" for t in tasks),
            "now": next((t.get("name") for t in tasks if t.get("status") in ("running", "review")), None),
            "last": last, "folder": state.get("dir"), "schedule_id": chat.get("schedule_id")}


# ---------------------------------------------------------------------- handoffs
def parse_handoff(text):
    """The handoff block at the end of an agent's reply → {done, found, failed, next}. A reply without the block
    is its own 'done' (small models forget the block; the work is not thrown away for that)."""
    text = (text or "").strip()
    m = HANDOFF.search(text)
    block = m.group(1) if m else ""
    out = {"done": "", "found": "", "failed": "", "next": ""}
    marks = list(FIELD.finditer(block))
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(block)
        out[mark.group(1).lower()] = block[mark.end():end].strip()
    if not out["done"]:
        body = text[:m.start()].strip() if m else text
        out["done"] = body or block.strip()
    return out


def move_schema(harness_ids, roles):
    step = {"type": "object", "required": ["name", "goal", "done_when"], "properties": {
        "name": {"type": "string", "maxLength": 40}, "goal": {"type": "string", "maxLength": 1000},
        "done_when": {"type": "string", "maxLength": 240}, "agent": {"type": "string", "maxLength": 30},
        "harness": {"enum": list(harness_ids)}, "role": {"enum": list(roles)}, "loop": {"type": "boolean"},
        "minutes": {"type": "integer", "minimum": 5, "maximum": 480},
        "expects": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 80}}}}
    # the understanding and the reason come first, so a small model thinks before it decides
    return {"type": "object", "required": ["understanding", "reason", "decision", "steps"], "properties": {
        "understanding": {"type": "string", "maxLength": 1400}, "changed": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 300},
        "decision": {"enum": ["continue", "finish", "ask"]}, "question": {"type": "string", "maxLength": 400},
        "steps": {"type": "array", "maxItems": 2, "items": step},
        "outline": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 140}}}}


class Mission(Orchestra):
    """One long-running task inside a chat. It is an Orchestra whose steps are decided one move at a time."""
    kind = "mission"

    def __init__(self, turn, roster=None, overall=None, fit=None, setup=None):
        super().__init__(turn, roster=None, overall=overall, fit=fit)
        setup = clean_setup(setup or self.db.setting(f"chat_mission:{turn.chat['id']}", None), self.app)
        self.sequence, self.max_parallel, self.chain = True, 1, False
        self.step["mode"] = "sequence"
        self.name = setup["name"]
        self.done_when = setup["done_when"]
        self.team = setup["team"]
        self.allow = setup["allow"]
        self.limits = setup["limits"]
        self.outline = setup["outline"]
        if setup["goal"] and setup["goal"].strip() not in (self.overall or ""):
            # the task as its planner wrote it out for a run nobody watches (set-up), after the user's own words
            self.overall = f"{self.overall}\n\nWritten out: {setup['goal']}" if self.overall else setup["goal"]
        self.understanding = ""
        self.handoffs = []                    # one per settled step: {id, name, agent, status, done, found, next}
        self.changes = 0                      # times the plan was changed on the way
        self.moves = 0
        self.said = ""                        # the user's latest message, when it is not the request itself
        self.pause = None                     # {"kind": limit | question | planner | stuck, "text": …}
        self.before = {"seconds": 0.0, "tokens": 0, "cost": 0.0}   # used in earlier sessions of this task
        self.auto_resumes = 0
        self.planner_failures = 0
        self.last_error = "no answer"
        self.stuck = 0
        self.refused = []                     # steps the planner proposed that equal a failed attempt (this move)
        self.failed_from = 0                  # failures before this step no longer pause the task (the user said go on)
        if not turn.chat.get("connectors"):   # a task that was given no connectors may use what is installed
            ids = self.app.connectors.usable()
            if ids:
                self.db.update("chats", turn.chat["id"], {"connectors": ids})
                turn.chat["connectors"] = ids
        self.services = None                  # what the task's connectors offer, as text (None: not looked up yet)
        self.max_tasks = self.max_checks = 10**6      # the task's own limits decide, not a planned run's
        self.time_limit = float("inf")

    # ------------------------------------------------------------------ what was used, against the limits
    def spent(self):
        stats = self.turn.stats
        tokens = sum(int((t.get("used") or {}).get("tokens") or 0) for t in self.tasks.values())
        tokens += int(stats.get("prompt_tokens") or 0) + int(stats.get("cached_tokens") or 0) + \
            int(stats.get("output_tokens") or 0)
        return {"steps": len(self.tasks),
                "seconds": round(self.before["seconds"] + time.monotonic() - self.started),
                "tokens": self.before["tokens"] + tokens,
                "cost": round(self.before["cost"] + float(stats.get("cost") or 0), 4)}

    def limit_reached(self):
        """Which limit the task has reached, as a sentence, or None."""
        used, lim = self.spent(), self.limits
        if used["steps"] >= lim["steps"]:
            return f"it has used its {lim['steps']} steps"
        if used["seconds"] >= lim["hours"] * 3600:
            return f"it has worked for its {lim['hours']:g} hours"
        if lim.get("tokens") and used["tokens"] >= lim["tokens"]:
            return f"it has used its {pretty_tokens(lim['tokens'])} tokens"
        if lim.get("cost") and used["cost"] >= lim["cost"]:
            return f"it has spent its US${lim['cost']:.2f} on online models"
        return None

    def over_limit(self):
        return None                           # a task stops between two steps (advance), never in the middle of one

    def minutes_left(self):
        """What the task's own limit on hours leaves: a step never gets more time than that."""
        return (self.limits["hours"] * 3600 - self.spent()["seconds"]) / 60

    def failing(self, since=0):
        """How many steps in a row have failed since the last one that was accepted (counting only the steps
        from number `since` on: after the user said 'try once more', earlier failures do not pause the task)."""
        n = 0
        for h in reversed(self.handoffs[since:]):
            if h["status"] not in ("failed", "not run"):
                break
            n += 1
        return n

    def failure_lines(self, last=5):
        """The failed steps as the planner reads them: one line per step (its last attempt), with what its tools
        reported and what was changed afterwards."""
        by_step = {}
        for r in self.failures:
            by_step[r.get("step")] = r        # the last attempt at each step
        failed = {h["id"] for h in self.handoffs if h["status"] in ("failed", "not run")}
        rows = [r for sid, r in by_step.items() if sid in failed]
        names = {r.get("model"): self.model_name(r["model"]) for r in rows if r.get("model")}
        return recovery.ledger_text(rows, last=last, names=names)

    async def approve_plan(self):
        """Nothing to approve here: the set-up was confirmed when the task was created, and steps are decided
        one at a time."""

    # ------------------------------------------------------------------ saved state and the view for the window
    def mission_view(self):
        return {"name": self.name, "understanding": self.understanding, "outline": self.outline,
                "done_when": self.done_when, "team": self.team, "allow": self.allow, "limits": self.limits,
                "spent": self.spent(), "handoffs": self.handoffs[-60:], "pause": self.pause,
                "changes": self.changes, "moves": self.moves}

    def extra_state(self):
        return {"mission": {**self.mission_view(), "auto_resumes": self.auto_resumes,
                            "failed_from": self.failed_from}}

    def load_extra(self, state):
        m = state.get("mission") or {}
        self.name = m.get("name") or self.name
        self.understanding = m.get("understanding") or ""
        self.outline = list(m.get("outline") or [])
        self.done_when = m.get("done_when") or self.done_when
        self.team = m.get("team") or self.team
        self.allow = m.get("allow") or []
        self.limits = clean_limits(m.get("limits"), self.limits)
        self.handoffs = list(m.get("handoffs") or [])
        self.changes, self.moves = int(m.get("changes") or 0), int(m.get("moves") or 0)
        self.pause = m.get("pause")
        self.auto_resumes = int(m.get("auto_resumes") or 0)
        self.failed_from = int(m.get("failed_from") or 0)
        used = m.get("spent") or {}
        # steps keep what they used; what the planner itself used in earlier sessions is the rest
        steps = sum(int((t.get("used") or {}).get("tokens") or 0) for t in state.get("tasks") or [])
        self.before = {"seconds": float(used.get("seconds") or 0), "cost": float(used.get("cost") or 0),
                       "tokens": max(0, int(used.get("tokens") or 0) - steps)}

    def publish(self):
        self.step["mission"] = self.mission_view()
        super().publish()

    def came_back(self, by_button=False):
        """The task continues after a pause, a stop or a restart."""
        was = self.pause or {}
        self.pause = None
        if was.get("kind") == "recovery":     # the user said: go on. The count of failures in a row starts again
            self.failed_from = len(self.handoffs)
        self.overall = self.overall or ""
        latest = (self.turn.last_user_text() or "").strip()
        if latest and not by_button and latest not in self.overall:
            self.said = latest                # an answer to the planner's question, or a new instruction
        if was.get("kind") == "question" and self.said:
            self.logline(f"You answered the planner's question: {_clip(self.said, 200)}")

    # ------------------------------------------------------------------ the team
    def member(self, raw):
        """The team member a step is given to. A name the team does not have yet becomes a new member: the
        planner creates the kind of agent it needs."""
        name = " ".join(str(raw.get("agent") or "").split())[:30]
        found = next((m for m in self.team if m["name"].lower() == name.lower()), None) if name else None
        if found:
            return found
        ids = self.harness_ids
        harness = raw.get("harness") if raw.get("harness") in ids else None
        if not name:                          # no name given: the member with that harness, else the first one
            return next((m for m in self.team if harness and m["harness"] == harness), self.team[0])
        new = {"name": name, "harness": harness or "agent", "about": _clip(raw.get("goal"), 120),
               "role": raw.get("role") if raw.get("role") in self.roles() else None, "model": None}
        if len(self.team) < 12:
            self.team.append(new)
            self.logline(f"The planner added an agent to the team: {name} ({new['harness']}).")
        return new

    def add_steps(self, raw):
        """Turn the planner's next step(s) into steps of the run. Returns the new step ids."""
        items = []
        for r in raw[:2]:
            goal = str(r.get("goal") or "").strip()
            if not goal:
                continue
            # a step that already passed is not done again (a small planner likes to repeat itself)
            if any(t["status"] == "done" and not t.get("partial") and same_objection(t["goal"], goal)
                   for t in list(self.tasks.values())[-4:]):
                continue
            who = self.member(r)
            minutes = self.minutes_for(r, goal, who)
            # a step that equals an attempt that already failed is not run: it would fail the same way
            why = recovery.refusal({"goal": goal, "harness": who["harness"], "model": self.model_of(who),
                                    "minutes": minutes}, self.failures)
            if why:
                self.refused.append(why)
                self.logline(f"Not run again: {why}.")
                continue
            n = len(self.tasks) + len(items) + 1
            items.append({"id": f"s{n}", "name": str(r.get("name") or f"Step {n}")[:40], "goal": goal,
                          "done_when": r.get("done_when") or "", "loop": bool(r.get("loop")),
                          "expects": r.get("expects") or [], "agent": who["name"], "harness": who["harness"],
                          "role": who.get("role"), "model": who.get("model"),
                          "limits": {"time_limit_minutes": minutes}})
        before = set(self.tasks)
        self.add(items)
        return [tid for tid in self.tasks if tid not in before]

    def model_of(self, who):
        """The model a step given to this member would run on (as steps are given their models when added)."""
        probe = {"model": who.get("model"), "role": who.get("role")}
        self.turn.assign_models([probe])
        return probe.get("model") or self.turn.model_id

    def minutes_for(self, raw, goal, who):
        """The time a new step may take: the task's own minutes per step; more when the planner asks for it, or
        when the same step ran out of time while it was still getting somewhere (then twice as long). Never more
        than the hours the task has left."""
        base = float(self.limits["step_minutes"])
        try:
            asked = float(raw.get("minutes") or 0)
        except (TypeError, ValueError):
            asked = 0.0
        late = [r for r in self.failures if r.get("class") == "timeout" and r.get("progress")
                and recovery.same_goal(r.get("goal"), goal) and (r.get("harness") or "agent") == who["harness"]]
        due = float(late[-1].get("minutes") or base) * 2 if late else 0.0
        minutes = max(base, asked, due)
        if minutes > base:
            minutes = max(base, min(minutes, LIMIT_RANGE["step_minutes"][1], max(5.0, self.minutes_left() - 2)))
            if minutes > base:
                self.logline(f"“{str(raw.get('name') or 'The next step')[:40]}” may take {minutes:g} minutes instead "
                             f"of {base:g}" + (": the same step ran out of time while it was still getting somewhere."
                                              if due >= asked else ": the planner asked for more time."))
        return int(minutes)

    async def start(self, t, text=None, fresh=False):
        if not t.get("chat_id"):              # every step starts in a fresh chat, also for the same team member:
            self.agent_chats.pop(t.get("agent"), None)     # what a step needs to know is in its brief
        await super().start(t, text=text, fresh=fresh)

    async def fast_settle(self, t, flag, rec, rule, why, last, earlier):
        """A step that stopped fast and that no rule could carry on (2.20.0): it fails now, without more rounds,
        and its planner decides the next move with the entry in hand; what only you can give is asked for at the
        next move without a planner call (advance). A declared failure counts toward the failures in a row that
        pause the task, like any other."""
        decision = ("it needs you: the task asks you" if rule == "ask" else
                    "no round is left: handed to the planner" if last else "handed to the planner, which decides "
                                                                            "the next step")
        rec["change"] = decision
        self.decided(t, flag, decision)
        return self.exhausted(t, why)

    async def free_unneeded(self):
        """Between two steps nothing is unloaded: the next step usually needs the same model again. A model
        the next step does not use goes when that step has started."""
        if any(t["status"] not in SETTLED for t in self.tasks.values()) or self.finished or self.pause:
            await super().free_unneeded()

    # ------------------------------------------------------------------ handoffs
    def note_handoffs(self):
        have = {h["id"] for h in self.handoffs}
        for t in self.tasks.values():
            if t["status"] not in SETTLED or t["id"] in have:
                continue
            # what its tools reported as failed is added by code: the record must not depend on the model
            tools = "; ".join(f"{f['tool']}" + (f" ({f['what']})" if f.get("what") else "")
                              + (f" ×{f['times']}" if (f.get("times") or 1) > 1 else "") + f": {f['error'][:120]}"
                              for f in (t.get("failures") or [])[:4])
            if t["status"] == "done":
                h = parse_handoff(t.get("output"))
                status = "incomplete" if t.get("partial") else "done"
                if t.get("partial") and t.get("note"):
                    h["found"] = (h["found"] + " " if h["found"] and not NOTHING.match(h["found"]) else "") + \
                        f"Still open after the check: {t['note']}"
                self.auto_resumes = 0         # the task moves: a later restart may continue it by itself again
            else:
                said = parse_handoff(t.get("output")) if t.get("output") else {}
                h = {"done": "", "found": t.get("note") or t.get("error") or "it did not finish", "next": "",
                     "failed": said.get("failed") or ""}
                status = "failed" if t["status"] == "failed" else "not run"
            failed = "" if NOTHING.match(h.get("failed") or "") else (h.get("failed") or "")
            if tools and status != "done":
                failed = (failed + " " if failed else "") + f"Its tools reported: {tools}"
            kind = t.get("kind") if status in ("failed", "incomplete") else None
            flag = t.get("flag") if status != "done" else None      # it stopped fast (2.20.0)
            self.handoffs.append({"id": t["id"], "name": t["name"], "agent": t.get("agent"), "status": status,
                                  "done": _clip(h["done"], 2400), "found": _clip(h["found"], 700),
                                  "failed": _clip(failed, 600), "class": kind,
                                  "next": _clip(h["next"], 500), "files": list(t.get("files") or [])[:10],
                                  "result_file": t.get("result_file"),
                                  "flag": {k: flag.get(k) for k in ("by", "reason", "need", "detail", "rule",
                                                                    "decision")} if flag else None})

    def ledger(self, skip_last=False, last=8):
        rows = self.handoffs[:-1] if skip_last else self.handoffs
        lines = []
        for h in rows[-last:]:
            found = "" if not h.get("found") or NOTHING.match(h["found"]) else f" Found: {_clip(h['found'], 160)}"
            lines.append(f"- {h['id']} “{h['name']}” [{h.get('agent') or 'agent'}] — {h['status']}: "
                         f"{_clip(h.get('done') or '(no result)', 170)}{found}".replace("\n", " "))
        more = len(rows) - len(lines)
        return (f"({more} earlier steps not shown)\n" if more > 0 else "") + "\n".join(lines)

    @staticmethod
    def handoff_text(h, budget):
        return (f"### {h['id']} {h['name']} ({h['status']})\nDone: {_clip(h.get('done') or '(nothing)', budget)}"
                + (f"\nFound: {h['found']}" if h.get("found") else "")
                + (f"\nFailed: {h['failed']}" if h.get("failed") else "")
                + (f"\nNext: {h['next']}" if h.get("next") else "")
                + (f"\nFull text: {h['result_file']}" if h.get("result_file") else "")
                + (f"\nFiles: {', '.join(h['files'])}" if h.get("files") else ""))

    def brief(self, t, planned=False, redo=None):
        """What a step's agent is told: its step, the planner's understanding, and the handoff before it."""
        parts = ["You do one step of a long task that a planner runs step by step. Agents before you did the "
                 "earlier steps and another agent may continue after you: what you hand back is all they will "
                 "know of your work."]
        parts.append(f"The task as a whole (for context only — do just your step): {_clip(self.overall, 700)}")
        if self.understanding:
            parts.append(f"What is known so far (the planner's notes): {_clip(self.understanding, 1400)}")
        names = self.app.connectors.names(self.connector_ids())
        if names:
            parts.append("Connected services you have tools for: " + ", ".join(names) + ". When one of them offers "
                         "what your step needs, use its tools: they are made for it. Search or browse the web only "
                         "for what they do not offer.")
        if self.handoffs and not planned:
            earlier = self.ledger(skip_last=True, last=6)
            if earlier:
                parts.append("Earlier steps:\n" + earlier)
            parts.append("Handoff from the step before yours:\n\n"
                         + self.handoff_text(self.handoffs[-1], self.dep_budget(t)))
        parts.append(f"Your step ({t['id']} “{t['name']}”): {t['goal']}")
        if t.get("done_when"):
            parts.append(f"Done when: {t['done_when']}")
        if t.get("expects"):
            # the exact paths (2.19.0), as in a planned run's brief
            parts.append("Leave these files, at exactly these paths (the app checks them):\n"
                         + "\n".join(f"- {self.expected_path(n)}" for n in t["expects"]))
        if redo:
            parts.append(redo)
        parts.append(
            f"Shared folder of the task: {self.dir} — save files that later steps or the user need there.\n"
            "Use only what is given above and what you find or compute with your tools. If something you need "
            "is not there, say exactly what is missing: never rebuild it from memory.\n"
            "Work efficiently. If code fails, read the error, fix the cause and run it again; check outputs "
            "before you rely on them.\n"
            + recovery.AGENT_RULES + "\n" + recovery.FAIL_FAST_RULE + "\n"
            "Finish your reply with this block, filled in:\n"
            "## Handoff\n"
            "Done: what you did and the result itself (facts, numbers, sources, file paths)\n"
            "Found: anything that changes what should happen next (a problem, a surprise, a better way), or "
            "'nothing new'\n"
            "Failed: what you tried that did not work and why (the tool, the source, the error), so that nobody "
            "tries it again the same way, or 'nothing failed'\n"
            "Next: what the next agent should do, in one or two sentences, or 'nothing: the task is complete'"
            + self.length_hint(t))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ the planner's next move
    def move_prompt(self):
        from .runner import ROLE_HELP
        used, lim = self.spent(), self.limits
        options = "; ".join(f"{h['id']}: {(h.get('description') or '')[:70]}" for h in self.turn.team_harnesses())
        team = "; ".join(f"{m['name']} ({m['harness']}" + (f": {m['about']}" if m.get("about") else "") + ")"
                         for m in self.team)
        parts = [
            "You run a long task for me. Agents on my computer do the work one step at a time; you never do it "
            "yourself. After every step you read what its agent handed back and decide what happens next. The "
            "plan is yours to change whenever the findings call for it.",
            f"The task: {_clip(self.overall, 1800)}"]
        if self.done_when:
            parts.append(f"It is finished when: {self.done_when}")
        if self.said:
            parts.append(f"My latest message to you: {_clip(self.said, 900)}")
        parts.append("Your understanding so far: " + (self.understanding or "(none yet — write it now: what the "
                                                      "problem is, what is known, how you will go about it)"))
        if self.outline:
            parts.append("What you expected to come next: " + "; ".join(self.outline))
        if self.services:
            parts.append("Connected services the agents have tools for (what each promises):\n" + self.services
                         + "\nWhen a service offers what a step needs, say in the step's goal which service and "
                         "tool to use. Send an agent to search or browse the web only for what no service offers.")
        from .orchestrator import crew_facts
        facts = crew_facts(self.turn)
        if facts:
            parts.append(facts)
        parts.append(f"Steps done so far: {len(self.handoffs)}"
                     + ("\n" + self.ledger(skip_last=True) if len(self.handoffs) > 1 else ""))
        if self.handoffs:
            parts.append("The latest handoff:\n\n" + self.handoff_text(self.handoffs[-1], 2400))
        failing = self.failing()
        if failing:
            last = self.handoffs[-1]
            kind = last.get("class") or "approach"
            parts.append(
                f"Failures since the last accepted step ({failing}):\n" + self.failure_lines()
                + f"\nThe latest one is of this kind: {recovery.CLASSES.get(kind, {}).get('label', kind)}. "
                + recovery.CLASSES.get(kind, recovery.CLASSES["approach"])["advice"]
                + "\nA step that is the same as one that failed (the same goal for the same kind of agent on the "
                "same model with the same time) will not be run: the app refuses it. Change what caused the failure.")
            flag = last.get("flag")
            if flag:                          # the entry of the register, in hand (2.20.0)
                need = recovery.NEEDS.get(flag.get("need"))
                parts.append(("Its agent reported at once that it could not finish" if flag.get("by") == "agent"
                              else "The app stopped its agent at once") + f": {flag.get('reason')}"
                             + (f". It needs {need}" + (f": {flag['detail']}" if flag.get("detail") else "") if need else "")
                             + ". Decide the next step from this: give what is needed, or go another way.")
        if self.refused:
            parts.append("Not run, because it repeats a failure:\n" + "\n".join(f"- {x}" for x in self.refused[-3:])
                         + "\nGive a step that differs in what matters: another approach in the goal, another "
                         "agent or role, a smaller piece, or more time ('minutes') after a timeout.")
        parts.append(f"Used so far: {used['steps']} of {lim['steps']} steps, {pretty_seconds(used['seconds'])} of "
                     f"{lim['hours']:g} hours. A step may take {lim['step_minutes']:g} minutes unless you give it "
                     "'minutes'.")
        parts.append(
            f"Your team: {team}. Give a step to one of them by name with 'agent', or name a new agent (with a "
            "harness) when none of them fits.\n"
            "Answer in JSON:\n"
            "- understanding: your understanding now — keep what still holds, add what the last step showed, "
            "drop what turned out wrong (about 150 words at most).\n"
            "- changed: true when what the last step found made you change the plan you had; false when the "
            "work simply goes on as expected.\n"
            "- reason: one or two sentences on why this is the right next move (and, if the plan changed, why).\n"
            "- decision: 'continue' to give the next step; 'finish' when the task is done or nothing useful is "
            "left to try; 'ask' only when I must decide or provide something (put it in 'question').\n"
            "- steps: for 'continue', the next step — one, or two when the second is certain to follow. goal: "
            "everything its agent needs to know (it sees only the goal, your understanding and the latest "
            "handoff): paths, sources, formats, what to avoid. done_when: a test the result can be checked "
            f"against. harness: {options}" + (" (use 'browser' for a step that has to use a website: clicking "
                                              "through, forms, a download behind a click)"
                                              if "browser" in self.harness_ids else "")
            + f". role: {ROLE_HELP} loop: true for work that takes rounds of try, check "
            "and fix. expects: files it must save. minutes: only when a step needs longer than usual (it ran "
            "out of time while it was still getting somewhere).\n"
            "- outline: the few phases you expect after that; change it when the findings change the plan.\n"
            "Never repeat a step that passed. When a step failed, try another way or a smaller piece, not the "
            "same thing again. JSON only.")
        return "\n\n".join(parts)

    def stop_for(self, kind, text):
        """Pause the task between two steps (a limit, a question, a planner that cannot be reached)."""
        self.pause = {"kind": kind, "text": text, "at": now()}
        self.outcome = "paused"
        self.logline(f"Paused: {text}")
        return False

    async def advance(self, messages):
        """Every step given so far is settled: read the handoffs and decide the next move. True when the task
        goes on with new steps."""
        self.note_handoffs()
        if self.finished:
            return False
        why = self.limit_reached()
        if why:
            return self.stop_for("limit", why)
        # a problem that survived several different recoveries needs a person, not a fifth try
        tries = max(1, int(self.db.setting("recovery_attempts", 3) or 3))
        if self.failing(self.failed_from) > tries and not self.said:
            return self.stop_for("recovery", f"{self.failing()} steps in a row failed, each one a different attempt "
                                             "to get past the same point")
        last = self.handoffs[-1] if self.handoffs else {}
        flag = last.get("flag") or {}
        if last.get("status") == "failed" and flag.get("rule") == "ask" and not self.said:
            # only you can give what it needs: asked now, by rule, without a planner call (2.20.0)
            need = recovery.NEEDS.get(flag.get("need")) or "something only you can give"
            return self.stop_for("question", f"{last.get('agent') or 'An agent'} could not finish “{last['name']}”: "
                                             f"{flag.get('reason')}. It needs {need}"
                                             + (f" ({flag['detail']})" if flag.get("detail") else "")
                                             + ". Reply with what to do, or give what it needs, and the task goes on.")
        if self.services is None:             # once per session: what the task's connectors promise
            ids = self.connector_ids()
            try:
                await self.app.connectors.learn(ids)
            except Exception as exc:
                self.app.log(f"task {self.id}: asking the connectors what they offer: {type(exc).__name__}: {exc}")
            self.services = self.app.connectors.digest(ids) if ids else ""
        turn = self.turn
        if turn.take_steer(messages):         # something the user said while the last step was running
            self.said = messages[-1]["content"]
        move = await self.next_move(messages)
        if move is None:
            return self.fallback_move()
        self.planner_failures = 0
        self.moves += 1
        self.said = ""
        new_understanding = " ".join((move.get("understanding") or "").split())
        if len(new_understanding) >= 20:
            self.understanding = new_understanding[:1600]
        outline = [" ".join(str(x).split())[:160] for x in (move.get("outline") or []) if str(x).strip()][:6]
        reason = " ".join((move.get("reason") or "").split())
        decision = move.get("decision")
        if move.get("changed") and self.handoffs and decision == "continue":
            self.changes += 1                 # the planner says the findings made it change the plan
            self.logline("The plan changed" + (f": {reason}" if reason else "."))
        if outline and decision == "continue":
            self.outline = outline
        if decision == "ask" and (move.get("question") or "").strip():
            return self.stop_for("question", move["question"].strip())
        if decision == "finish" and self.handoffs:
            self.finished = True
            self.logline("The planner finished the task" + (f": {reason}" if reason else "."))
            self.save()
            return False
        self.refused = []
        new = self.add_steps(move.get("steps") or [])
        if not new and not self.handoffs:     # nothing to start with: one agent takes the task as it is
            new = self.add_steps([{"name": "First step", "goal": self.overall,
                                   "done_when": self.done_when or "the request is answered"}])
        if not new:
            self.stuck += 1
            if self.stuck >= 2:
                if self.refused:
                    return self.stop_for("recovery", "the planner kept giving a step that already failed the same "
                                                     "way, and found no other way")
                return self.stop_for("stuck", "the planner gave no new step twice (it kept proposing work that is "
                                              "already done). Tell it what to do next, or finish the task.")
            return await self.advance(messages)
        self.stuck = 0
        self.refused = []
        for tid in new:
            await self.forecast(self.tasks[tid])
        names = ", ".join(f"{tid} “{self.tasks[tid]['name']}” → {self.tasks[tid].get('agent')}" for tid in new)
        self.logline(f"Next: {names}" + (f" — {reason}" if reason else ""))
        self.save()
        return True

    async def next_move(self, messages):
        """One planner call. None when it could not be made (the caller falls back on the last handoff)."""
        turn = self.turn
        try:
            await turn.hold_model()
            turn.status("The planner is reading the handoff and deciding the next step…" if self.handoffs
                        else "The planner is working out how to start…")
            schema = move_schema(self.harness_ids, self.roles())
            with usage_purpose("planner"):           # counted as the planner's work in Usage (2.27.0)
                return (await turn.complete(messages + [{"role": "user", "content": self.move_prompt()}],
                                            schema=schema, max_tokens=1800, temperature=0.2))["json"]
        except asyncio.CancelledError:
            raise
        except Exception as exc:            # one bad call must not end hours of work: fall back, then pause
            self.planner_failures += 1
            self.app.log(f"task {self.id}: the planner's move failed: {type(exc).__name__}: {exc}")
            self.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            return None
        finally:
            if not is_cloud(turn.model_id):
                await turn.drop_model()       # the step's agent gets the model

    def fallback_move(self):
        """The planner could not be asked. The relay still moves: the step the last agent left as 'Next' is
        run. Twice in a row, or with nothing to go on, the task pauses instead of guessing."""
        last = self.handoffs[-1] if self.handoffs else None
        nxt = (last or {}).get("next") or ""
        err = self.last_error
        if self.planner_failures >= 2 or (last and (last["status"] == "failed" or not nxt or NOTHING.match(nxt))):
            return self.stop_for("planner", f"the planner could not decide the next step ({err})")
        raw = {"name": "Continue" if last else "First step", "goal": nxt or self.overall,
               "done_when": "what the step before asked for is done" if last else
               (self.done_when or "the request is answered"), "agent": (last or {}).get("agent") or ""}
        new = self.add_steps([raw])
        if not new:
            return self.stop_for("planner", f"the planner could not decide the next step ({err})")
        self.logline(f"The planner could not be asked ({err}); {new[0]} follows the last agent's handoff.")
        self.save()
        return True

    # ------------------------------------------------------------------ the end of a session
    def close(self):
        super().close()
        if self.turn.model_id and not is_cloud(self.turn.model_id):
            self.turn.unload_after.add(self.turn.model_id)    # nobody is waiting on a long task: free the memory

    def paused_text(self):
        used, p = self.spent(), self.pause or {}
        done = sum(h["status"] in ("done", "incomplete") for h in self.handoffs)
        lines = []
        if p.get("kind") == "question":
            lines.append(f"**The planner has a question for you:** {p.get('text')}\n\nReply here and the task "
                         "continues with your answer.")
        else:
            lines.append(f"**Paused:** {p.get('text')}.")
        lines.append(f"{done} of {len(self.handoffs)} steps were accepted so far, in {pretty_seconds(used['seconds'])} "
                     f"of work and {pretty_tokens(used['tokens'])} tokens.")
        if self.understanding:
            lines.append(f"**What is known so far:** {self.understanding}")
        if self.handoffs:
            h = self.handoffs[-1]
            lines.append(f"**Latest step ({h['id']} {h['name']}, {h['status']}):** {_clip(h.get('done') or h.get('found'), 900)}"
                         + (f"\n\nIt proposed next: {h['next']}" if h.get("next") and not NOTHING.match(h["next"]) else ""))
        if p.get("kind") == "recovery":
            tried = self.failure_lines(last=6)
            if tried:
                lines.append("**What was tried and how it failed:**\n" + tried)
            lines.append("Reply here with what to do differently (another source, another way, what to leave out), "
                         "or press **Continue** to let the planner try once more; nothing that was done is lost.")
        elif p.get("kind") == "limit":
            lines.append("Raise the limit and press **Continue** to carry on; nothing that was done is lost.")
        elif p.get("kind") not in ("question", "recovery"):
            lines.append("Press **Continue** to try again, or reply here to tell the planner what to do next.")
        lines.append(f"Files are in {self.dir}")
        return "\n\n".join(lines)

    def say(self, text):
        turn = self.turn
        turn.steps.append({"type": "text", "text": text})
        turn.text = [text]
        turn.emit("token", text=text)

    async def final(self, messages):
        """A finished task gets its report from the planner; a paused one says where it stands (no model call)."""
        turn = self.turn
        title = turn.chat.get("title") or self.name or "Long-running task"
        if self.pause:
            self.say(self.paused_text())
            ask = self.pause.get("kind") == "question"
            self.app.bus.publish("notify", title=("A question about: " if ask else "Paused: ") + title,
                                 body=_clip(self.pause.get("text"), 180), chat_id=turn.chat["id"], level="action")
            return
        done = [h for h in self.handoffs if h["status"] in ("done", "incomplete")]
        open_ = [h for h in self.handoffs if h["status"] != "done"]
        try:
            await turn.hold_model()
            ctx = int(turn.server.plan.get("ctx") or 8192) if turn.server else 8192
            room = max(2000, int((ctx - turn.max_reply - 3500) * 3))
            recent = self.handoffs[-3:]
            each = max(600, (room - 2500) // max(1, len(recent)))
            turn.status("The planner is writing the report…")
            messages.append({"role": "user", "content": (
                "The task has ended. Your understanding: " + (self.understanding or "(none)") + "\n\n"
                "All steps:\n" + self.ledger(last=40) + "\n\nThe last handoffs in full:\n\n"
                + "\n\n".join(self.handoff_text(h, each) for h in recent) + f"\n\nShared folder: {self.dir}\n\n"
                "Now write the final report on my task: what was achieved, the results themselves (facts, numbers, "
                "sources, file paths), and what is still open. Say plainly which steps failed or are incomplete, "
                "and do not present their missing results as done.")})
            turn.text = []
            await turn.stream_text(messages, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.app.log(f"writing the task's report failed: {type(exc).__name__}: {exc}")
            self.say(f"The planner could not write the report ({str(exc)[:160]}), so here is where the task "
                     f"stands. {len(done)} of {len(self.handoffs)} steps were accepted"
                     + (f"; failed or incomplete: {', '.join(h['id'] + ' ' + h['name'] for h in open_)}" if open_ else "")
                     + f".\n\n**What is known:** {self.understanding or '(nothing was written down)'}\n\n"
                     + "\n\n".join(self.handoff_text(h, 1500) for h in self.handoffs[-4:])
                     + f"\n\nShared folder: {self.dir}")
        self.app.bus.publish("notify", title="Finished: " + title, level="done",
                             body=f"{len(done)} of {len(self.handoffs)} steps accepted. Open it to read the report.",
                             chat_id=turn.chat["id"])
