"""Runs one chat turn with the chosen harness and model, streaming everything to the UI.

Loop harnesses (mode):
  chat      one reply, no tools
  agent     tool loop with a step limit
  planner   short plan → each step with tools → final answer
  research  fixed stages run by code (plan searches → search → rank → read → download files → extract facts →
            cited report). The model only answers small questions, so it cannot loop.
  team      a planner turns the request into a sequence of steps (orchestrator.py): steps run one after another,
            each starts when the results it needs are accepted, every result is checked and sent round again
            until it passes, then the planner writes the answer. "agent" and "planner" turns hand multi-step
            requests to it automatically (Settings → Teams), and the delegate tool uses it too.
Pipeline harnesses: a Python program run as a task; its output streams into the chat.

Every tool-using turn has a loop guard: repeated or near-identical calls are refused (the model is told why), tools
have per-turn caps (searches especially), several unproductive results in a row trigger a "change approach" note and
then a forced write-up, and a time limit stops the turn with a summary of what was found.

The user can send follow-ups while a reply is being written: queued ones run right after it; "steer" ones are handed
to the agent at its next step.

Each turn is saved as one assistant message whose meta.steps lists text, tool calls and results in order, so a
reloaded chat looks exactly like the live one.

Recovery (2.5.0): a model call that hangs or loses its server (llm.LLMTimeout, llm.LLMUnreachable) restarts the
model and is tried once more before the turn gives up with a clear message. Turns that belong to a planned run
load their model with the context the run needs (orchestrator.RunFit), and give memory back when the run ends."""
import asyncio
import hashlib
import json
import os
import platform
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import media, run_script as launcher
from .cloud import USAGE, CloudError
from .db import new_id, now
from .llm import LLMContextFull, LLMError, LLMTimeout, LLMUnreachable, estimate_tokens
from .mcp import tool_key
from .orchestrator import Orchestra, RunFit, looks_multistep, team_tools
from .providers import is_cloud
from .runtime import RuntimeError_
from .tools import GROUPS, Tool, ToolContext, ToolDenied, ToolError, builtin_tools

TOOL_RESULT_LIMIT = 6000
ROLES = ("worker", "strategist", "vision")
ROLE_HELP = ("worker = a model on this computer for routine, well-defined parts; strategist = the strongest model "
             "(often online) for planning and hard reasoning; vision = a model that can see images and video.")
PLAN_SCHEMA_STEP = {"type": "object", "required": ["title"], "properties": {
    "title": {"type": "string", "maxLength": 90}, "detail": {"type": "string", "maxLength": 300}}}
TEXT_TOOLCALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
URL_RE = re.compile(r"https?://[^\s)>\]]+")

BASE_PROMPT = """You are KimiLM, an assistant on the user's own computer. {where}
Today is {today}. The computer runs {os}.
Be direct and practical. Use Markdown. Never invent file contents, numbers or sources: read, fetch or compute them."""

AGENT_RULES = """You can use tools. Use one when it helps; otherwise just answer. After tool results, continue until \
the task is done, then give a clear final answer (what you did, what you found, where files are).
Never repeat a tool call you already made; if something did not work twice, try a different approach or answer with \
what you have. When code or a command fails, read the error, fix the cause and run it again; check results before \
you rely on them. If a search says NO RELEVANT RESULTS, do not search for the same thing again.
Changes (writing files, running code, scheduling, connector actions that change things) are shown to the user for \
approval by the app; if one is declined, do not retry it — explain or choose another way."""


_PRIVATE_TOOLS = None
PRIVATE_MEMORY_TOOLS = {"search_chats": "chats", "read_chat": "chats", "search_knowledge": "knowledge",
                        "search_memory": "memory"}


def private_tool_names():
    """Tools whose results can contain the user's files (file reads, images, Python output)."""
    global _PRIVATE_TOOLS
    if _PRIVATE_TOOLS is None:
        from .tools import builtin_tools
        _PRIVATE_TOOLS = {t.name for t in builtin_tools() if t.group in ("files", "vision", "python")} | \
            set(PRIVATE_MEMORY_TOOLS)
    return _PRIVATE_TOOLS


class TurnStopped(Exception):
    pass


def _norm_args(args):
    return json.dumps(args or {}, sort_keys=True, ensure_ascii=False).lower()


def _terms(text):
    from .tools.web import terms_of
    return set(terms_of(text))


class LoopGuard:
    """Stops agents from going round in circles. Shared by every model call of a turn."""
    UNPRODUCTIVE = ("NO RELEVANT RESULTS", "Error:", "There is no tool called", "The arguments were not valid JSON",
                    "Blocked:")
    # Results that can't change by doing something on this computer: an exact repeat stays blocked even after a
    # change. Everything else (reading or listing files, running code, connector reads) may be repeated once a
    # change succeeded, because the answer can be different now (e.g. read_file after edit_file).
    WORLD_TOOLS = ("web_search", "web_fetch", "download_file")

    def __init__(self, turn):
        lim, h, db = turn.limits, turn.harness, turn.db
        self.max_calls = int(lim.get("max_tool_calls") or h.get("max_tool_calls") or 40)
        self.caps = {"web_search": int(lim.get("max_searches") or h.get("max_searches") or 6),
                     "web_fetch": 14, "download_file": 15, "look_at_video": 6, "delegate": 2}
        self.caps.update({k: int(v) for k, v in (h.get("tool_limits") or {}).items()})
        minutes = float(lim.get("time_limit_minutes") or h.get("time_limit_minutes") or
                        db.setting("turn_time_limit_minutes", 30) or 30)
        self.minutes = minutes
        self.deadline = time.monotonic() + minutes * 60
        self.counts = Counter()
        self.total = 0
        self.done = {}            # call signature -> short result
        self.results = set()      # hashes of results already seen
        self.searches = []        # term sets of earlier searches
        self.streak = 0           # unproductive results in a row
        self.blocked = 0
        self.nudged_at = 0
        self.stop_reason = None

    def check(self, name, args):
        """None if the call may run, else the message returned to the model instead of running it."""
        sig = name + _norm_args(args)
        if sig in self.done:
            return (f"Blocked: you already called {name} with exactly these arguments. Result was: "
                    f"{self.done[sig][:400]}\nUse that result, or do something different.")
        if self.total >= self.max_calls:
            return f"Blocked: this reply has used its {self.max_calls} tool calls. Write your answer now."
        cap = self.caps.get(name)
        if cap is not None and self.counts[name] >= cap:
            return (f"Blocked: {name} was already used {cap} times in this reply (the limit). Work with what you "
                    "have, try another kind of source, or answer now.")
        if name == "web_search":
            terms = _terms((args or {}).get("query", ""))
            for prev in self.searches:
                if terms and prev and len(terms & prev) / len(terms | prev) >= 0.6:
                    return ("Blocked: this search is nearly the same as one you already ran. Searching again with "
                            "similar words gives the same results. Use different words, open a specific source with "
                            "web_fetch, or answer with what you have.")
        return None

    def record(self, name, args, ok, result, blocked=False, changed=False):
        sig = name + _norm_args(args)
        if blocked:
            self.blocked += 1
            self.streak += 1
            return
        if changed:   # a successful change (file written, code run, connector action): earlier reads may be stale
            world = tuple(n + "{" for n in self.WORLD_TOOLS)
            self.done = {s: r for s, r in self.done.items() if s.startswith(world)}
        self.total += 1
        self.counts[name] += 1
        self.done[sig] = result
        if name == "web_search":
            self.searches.append(_terms((args or {}).get("query", "")))
        digest = hashlib.sha1(result[:4000].encode("utf-8", "ignore")).hexdigest()
        useless = (not ok) or result.startswith(self.UNPRODUCTIVE) or digest in self.results
        self.results.add(digest)
        self.streak = self.streak + 1 if useless else 0

    def nudge(self):
        if self.streak >= 3 and self.streak != self.nudged_at:
            self.nudged_at = self.streak
            return ("Note from the app: your last few tool calls did not produce anything new. Do not repeat them. "
                    "Either try a clearly different approach (another source, another tool) or stop using tools "
                    "and answer with what you have, saying what you could not find.")
        return None

    def should_stop(self):
        if self.stop_reason:
            return self.stop_reason
        if time.monotonic() > self.deadline:
            self.stop_reason = f"the time limit of {self.minutes:g} minutes was reached"
        elif self.streak >= 6:
            self.stop_reason = "the last six tool calls produced nothing new"
        elif self.blocked >= 5:
            self.stop_reason = "the agent kept repeating calls that were already made"
        return self.stop_reason


RESEARCH_PLAN = {"type": "object", "required": ["action", "queries"], "properties": {
    "action": {"enum": ["research", "answer"]},
    "queries": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 120}},
    "want_files": {"type": "boolean"},
    "known_sources": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 200}}}}
EXTRACT_SCHEMA = {"type": "object", "required": ["relevant", "facts"], "properties": {
    "relevant": {"type": "boolean"},
    "facts": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 400}},
    "files": {"type": "array", "maxItems": 4, "items": {"type": "integer"}}}}


class Turn:
    def __init__(self, app, chat_id, job):
        self.app, self.db = app, app.db
        self.job = job
        self.chat = self.db.get("chats", chat_id)
        self.project = self.db.get("projects", self.chat["project_id"]) if self.chat.get("project_id") else None
        hid = self.chat.get("harness") or (self.project or {}).get("default_harness") or app.harnesses.default_id()
        self.harness = app.harnesses.get(hid) or app.harnesses.get("agent")
        if (job.get("payload") or {}).get("team") and self.harness.get("mode") != "team":
            # the user put a team together in the composer: the team lead runs this turn
            self.harness = app.harnesses.get("team-lead") or {**self.harness, "mode": "team", "name": "Team"}
        self.model_id = self.chat.get("model") or (self.project or {}).get("default_model") or app.models.default_model()
        self.auto = self.model_id in (None, "", "auto")   # the router picks the model per request
        self.route_reason = ""
        self.route_role = None
        self.private = set()         # private material in this turn's messages (memory, attachments, files)
        self.cloud_ok = set()        # … that the user let an online model see in this turn
        self._last_native = None     # provider state of the last online reply (signatures, reasoning)
        self.limits = dict(self.chat.get("settings") or {})
        self.message_id = new_id("msg_")
        self.steps = []
        self.reasoning = []
        self.text = []
        self.stats = {"model_calls": 0, "prompt_tokens": 0, "output_tokens": 0, "seconds": 0, "cached_tokens": 0}
        self.tool_map = {}
        self.workspace = app.paths.workspace(chat_id)
        team = app.team_of_chat.get(chat_id)
        if team:      # agents of one run share the run's folder, so their prompts start the same (and are cached)
            self.workspace = team[0].dir
        self.run_fit = app.run_fit.get(chat_id)   # how a planned run loads its models (None: as set in Models)
        self.unload_after = set()                 # models to unload when this turn ends
        self.auto_approve = bool(job.get("payload", {}).get("auto_approve")) or \
            self.db.setting(f"chat_auto_approve:{chat_id}", False)
        self.allowed_always = set(self.db.setting(f"chat_allow:{chat_id}", []) or [])
        self.ctx = None
        self.server = None
        self._lease = None
        self.guard = LoopGuard(self)

    # ------------------------------------------------------------------ settings
    @property
    def max_reply(self):
        return int(self.limits.get("max_reply_tokens") or self.harness.get("max_reply_tokens") or
                   self.db.setting("max_reply_tokens", 2048))

    @property
    def temperature(self):
        t = self.limits.get("temperature")
        return float(t if t not in (None, "") else self.harness.get("temperature", 0.4))

    @property
    def vision(self):
        return bool(self.server and self.server.plan.get("mmproj"))

    # ------------------------------------------------------------------ model lease (released while waiting on others)
    async def hold_model(self):
        if self._lease is None:
            overrides = None
            if self.run_fit is not None and not is_cloud(self.model_id):
                overrides = self.run_fit.overrides(self.app.runtime, self.model_id)
            elif not is_cloud(self.model_id) and self.model_id:
                overrides = self.app.runtime.keep(self.model_id, self.rough_need())
            self._lease = self.app.runtime.lease(self.model_id, on_wait=self.status, overrides=overrides)
            try:
                self.server = await self._lease.__aenter__()
            except RuntimeError_ as exc:
                self._lease = None
                if is_cloud(self.model_id) and self.auto:
                    return await self.switch_to_local(str(exc).split(". ")[0].lower())
                if is_cloud(self.model_id):
                    raise TurnStopped(str(exc))
                raise
            if self.ctx is not None:
                self.ctx.model_id = self.model_id

    async def drop_model(self):
        lease, self._lease = self._lease, None
        self.server = None
        if lease is not None:
            await lease.__aexit__(None, None, None)

    # ------------------------------------------------------------------ online models (2.3.0)
    def _needs_vision(self):
        row = self.db.one("SELECT meta_json FROM messages WHERE chat_id=? AND role='user' ORDER BY created DESC, "
                          "rowid DESC LIMIT 1", (self.chat["id"],))
        return any(media.kind_of(a.get("path", "")) for a in ((row or {}).get("meta") or {}).get("attachments", []))

    def resolve_auto(self):
        """Auto: the router picks the model for this request (a strategist for hard work, a local worker else)."""
        mid, reason, _role = self.app.router.auto(self.chat, self.project, self.harness, self.last_user_text(),
                                                  self._needs_vision())
        if mid:
            self.model_id, self.route_reason, self.route_role = mid, reason, _role
        else:
            self.model_id = None

    def local_model_id(self):
        """A model on this computer (pipelines and learning run locally)."""
        if self.model_id and self.model_id != "auto" and not is_cloud(self.model_id):
            return self.model_id
        mid, _ = self.app.router.pick("worker", chat=self.chat, project=self.project, prefer_local=True)
        return mid if mid and not is_cloud(mid) else None

    PRIVATE_LABELS = {"memory": "your memory notes", "attachments": "the files you attached",
                      "files": "what it read from files and folders on this computer",
                      "knowledge": "passages from the project's knowledge files",
                      "notes": "the project's notes", "chats": "text from your earlier chats"}

    async def before_model_call(self, messages):
        """Before anything goes to an online model: the monthly budget (Auto falls back to local models) and the
        privacy check (asks before your memory, attachments or file contents are sent, unless allowed)."""
        if not is_cloud(self.model_id):
            return
        if self.auto and self.app.providers.over_budget():
            return await self.switch_to_local("this month's online budget is used up")
        need = set(self.private)
        for m in messages:
            c = m.get("content")
            if m.get("role") == "user" and (
                    (isinstance(c, list) and any(p.get("type") == "image_url" for p in c)) or
                    (isinstance(c, str) and "[Attached " in c)):
                need.add("attachments")
        need -= self.cloud_ok
        if not need or self.db.setting("cloud_privacy", "ask") == "auto":
            return
        row = self.app.models.get_any(self.model_id) or {}
        tool = Tool("send_to_online_model", "", {}, None, "online", changes=True, label="Send to an online model")
        what = ", ".join(self.PRIVATE_LABELS[c] for c in sorted(need))
        description = (f"Send this to {row.get('name') or self.model_id} (online, {row.get('provider_name') or ''})? "
                       f"It would see {what}. 'Always in this chat' stops asking here; 'Keep it on this computer' "
                       "continues with a local model instead.")
        if await self.approve(tool, {"model": row.get("name"), "would_see": sorted(need)}, description,
                              ignore_auto=True):
            self.cloud_ok |= need
            return
        await self.switch_to_local("you chose to keep it on this computer")

    async def switch_to_local(self, reason):
        mid, name = self.app.router.pick("vision" if self.vision else "worker", chat=self.chat, project=self.project,
                                         prefer_local=True)
        if not mid or is_cloud(mid):
            raise TurnStopped(f"Not sent to the online model ({reason}), and there is no model on this computer to "
                              "use instead. Download one in Models.")
        await self.drop_model()
        self.model_id = mid
        self.note(f"Continuing with {name} because {reason}.")
        await self.hold_model()

    # ------------------------------------------------------------------ events
    def emit(self, type_, **data):
        self.app.bus.publish("chat", chat_id=self.chat["id"], job_id=self.job["id"], type=type_,
                             message_id=self.message_id, **data)

    def status(self, text):
        self.emit("status", text=text)
        self.app.jobs.progress(self.job["id"], text)

    def note(self, text):
        self.steps.append({"type": "note", "text": text})
        self.emit("note", text=text)

    # ------------------------------------------------------------------ approvals
    async def approve(self, tool, args, description, ignore_auto=False):
        if not tool.changes:
            return True
        if (self.auto_approve and not ignore_auto) or tool.name in self.allowed_always:
            return True
        approval_id = new_id("ap_")
        fut = asyncio.get_running_loop().create_future()
        self.app.approvals[approval_id] = {"future": fut, "chat_id": self.chat["id"], "job_id": self.job["id"],
                                           "tool": tool.name, "label": tool.label or tool.name, "description": description,
                                           "parent_chat_id": self.chat.get("parent_chat_id"),
                                           "agent": self.chat.get("agent_name")}
        self.emit("approval", approval_id=approval_id, tool=tool.name, label=tool.label or tool.name,
                  description=description, arguments=_preview(args))
        if self.chat.get("parent_chat_id"):  # agents in a team ask in the lead's chat too
            self.app.bus.publish("chat", chat_id=self.chat["parent_chat_id"], type="agent_approval",
                                 approval_id=approval_id, agent=self.chat.get("agent_name") or "Agent",
                                 agent_chat_id=self.chat["id"], tool=tool.name, label=tool.label or tool.name,
                                 description=description, arguments=_preview(args))
        self.app.jobs.set_status(self.job["id"], "waiting")
        self.app.bus.publish("notify", title="Approval needed",
                             body=f"{self.chat.get('title') or 'A chat'}: {tool.label or tool.name}",
                             chat_id=self.chat.get("parent_chat_id") or self.chat["id"])
        timeout = float(self.db.setting("approval_timeout_minutes", 360)) * 60
        try:
            decision = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            decision = {"allow": False, "reason": "timed out"}
        finally:
            self.app.approvals.pop(approval_id, None)
            self.app.jobs.set_status(self.job["id"], "running")
        self.emit("approval_done", approval_id=approval_id, allow=bool(decision.get("allow")))
        if self.chat.get("parent_chat_id"):
            self.app.bus.publish("chat", chat_id=self.chat["parent_chat_id"], type="agent_approval_done",
                                 approval_id=approval_id)
        if decision.get("allow") and decision.get("always"):
            self.allowed_always.add(tool.name)
            self.db.set_setting(f"chat_allow:{self.chat['id']}", sorted(self.allowed_always))
        return bool(decision.get("allow"))

    # ------------------------------------------------------------------ prompt building
    def folders(self):
        folders = list((self.project or {}).get("folders") or []) + list(self.chat.get("folders") or [])
        return [f for f in dict.fromkeys(folders) if Path(f).exists()]

    def tool_groups(self):
        groups = self.harness.get("tools")
        if groups is None:
            groups = list(GROUPS) if self.harness.get("mode", "agent") != "chat" else []
        if "*" in groups:
            groups = list(GROUPS)
        groups = set(groups)
        if self.limits.get("tools"):
            groups &= set(self.limits["tools"])
        if not self.db.setting("allow_python", True):
            groups.discard("python")
        if not self.vision:
            groups.discard("vision")
        if self.chat.get("parent_chat_id"):   # agents in a team do not start their own teams
            groups.discard("agents")
        return groups

    async def build_tools(self):
        if self.harness.get("mode") == "chat":
            return []
        groups = self.tool_groups()
        tools = [t for t in builtin_tools() if t.group in groups]
        if "agents" in groups:
            tools.append(self._delegate_tool())
        team = self.app.team_of_chat.get(self.chat["id"])
        if team:                                   # this chat is an agent in a running team
            tools += team_tools(team[0], team[1], self)
        connector_ids = list(self.chat.get("connectors") or []) or list((self.project or {}).get("connectors") or [])
        connector_tools = []
        if self.harness.get("connectors", True) and connector_ids:
            for cid in connector_ids:
                conf = self.app.connectors.get(cid, reveal=False)
                if not conf or not conf.get("enabled", True):
                    continue
                try:
                    listed = await self.app.connectors.list_tools(cid)
                except Exception as exc:
                    self.status(f"Connector {conf['name']} is unavailable: {str(exc)[:120]}")
                    continue
                mode = self.app.connectors.approval_mode(conf)
                for t in listed:
                    connector_tools.append(self._connector_tool(cid, conf, t, mode))
        limit = int(self.harness.get("max_native_tools") or self.db.setting("max_native_tools", 28))
        if len(tools) + len(connector_tools) > limit and connector_tools:
            tools += self._connector_meta_tools(connector_ids, connector_tools)
            self.connector_index = connector_tools
        else:
            tools += connector_tools
        self.tool_map = {t.name: t for t in tools}
        return tools

    def _connector_tool(self, cid, conf, t, mode):
        async def call(ctx, **args):
            ok, text = await self.app.connectors.call(cid, t["name"], args)
            if not ok:
                raise ToolError(text)
            return text
        changes = mode == "always" or (mode == "changes" and not t["readonly"])
        return Tool(tool_key(cid, t["name"]), f"[{conf['name']}] {t['description']}", t["parameters"], call,
                    "connector", changes=changes, label=f"{conf['name']}: {t['name']}", source=f"connector:{cid}",
                    summary=lambda a, n=t["name"], c=conf["name"]: f"{c} → {n} {json.dumps(a, ensure_ascii=False)[:300]}")

    def _connector_meta_tools(self, connector_ids, connector_tools):
        """Too many connector tools for a small model's prompt: offer a lookup + call pair instead."""
        by_name = {t.name: t for t in connector_tools}

        async def find(ctx, query=""):
            words = query.lower().split()
            rows = [t for t in connector_tools if not words or any(w in (t.name + t.description).lower() for w in words)]
            return "\n\n".join(f"{t.name}: {t.description[:300]}\nparameters: {json.dumps(t.parameters)[:600]}"
                               for t in rows[:12]) or "No matching tools"

        async def use(ctx, tool, arguments=None):
            t = by_name.get(tool)
            if not t:
                raise ToolError(f"Unknown connector tool {tool!r}; use find_connector_tools first")
            if not await self.approve(t, arguments or {}, t.describe_call(arguments or {})):
                raise ToolDenied("The user declined this action")
            return await t.func(ctx, **(arguments or {}))
        return [Tool("find_connector_tools", "Find connector tools by keyword (shows their parameters).",
                     {"type": "object", "properties": {"query": {"type": "string"}}}, find, "connector",
                     label="Find connector tools"),
                Tool("use_connector_tool", "Call a connector tool found with find_connector_tools.",
                     {"type": "object", "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"}},
                      "required": ["tool"]}, use, "connector", label="Use connector tool",
                     summary=lambda a: f"{a.get('tool')} {json.dumps(a.get('arguments') or {})[:300]}")]

    def _delegate_tool(self):
        harness_ids = [h["id"] for h in self.app.harnesses.all() if h.get("type") == "loop"
                       and h.get("mode") not in ("team",) and h.get("enabled", True)]

        async def delegate(ctx, tasks):
            """Runs through the orchestrator: steps run in order, each waits for the results it needs, and every
            result is checked before it is accepted, so helpers never run uncoordinated. The helpers use this
            agent's model as it is loaded (no resizing in the middle of its turn)."""
            if not isinstance(tasks, list) or not tasks:
                raise ToolError("Give a list of tasks")
            items = [{"id": str(t.get("id") or f"t{i}"), "name": str(t.get("name") or f"Agent {i}")[:40],
                      "goal": str(t.get("task") or "")[:1500], "done_when": str(t.get("done_when") or "")[:300],
                      "depends_on": t.get("depends_on") or [],
                      "harness": t.get("harness") if t.get("harness") in harness_ids else "agent",
                      "role": t.get("role") if t.get("role") in ROLES else None}
                     for i, t in enumerate(tasks[:int(self.db.setting("max_team_tasks", 8))], 1)]
            orch = Orchestra(self, overall=self.last_user_text())
            if not orch.preset(items):
                raise ToolError("Each task needs a 'task' text")
            system = (getattr(self, "loop_messages", None) or [{"role": "system", "content": ""}])[0]
            try:
                done = await orch.drive([system, {"role": "user", "content": self.last_user_text()}])
            finally:
                await self.hold_model()
            budget = max(1500, 9000 // max(1, len(orch.tasks)))
            return (f"{len(done)} of {len(orch.tasks)} tasks accepted.\n\n" + orch.results_text(budget) +
                    f"\n\nShared team folder: {orch.dir}")
        return Tool("delegate", "Hand parts of the task to other agents, each in its own chat. They run one after "
                    "another in the order given; a task with depends_on receives those tasks' results. Every "
                    "result is checked against its done_when before it is accepted. Returns their results.",
                    {"type": "object", "required": ["tasks"], "properties": {"tasks": {"type": "array", "maxItems": 8,
                     "items": {"type": "object", "required": ["name", "task"], "properties": {
                         "id": {"type": "string", "description": "short id such as t1, used in depends_on"},
                         "name": {"type": "string"}, "task": {"type": "string"},
                         "done_when": {"type": "string", "description": "how to tell the result is good enough"},
                         "depends_on": {"type": "array", "items": {"type": "string"}},
                         "harness": {"enum": harness_ids},
                         "role": {"enum": list(ROLES), "description": ROLE_HELP}}}}}},
                    delegate, "agents", label="Start agents",
                    summary=lambda a: "Start agents: " + ", ".join(t.get("name", "?") for t in a.get("tasks") or []))

    def system_prompt(self, tools):
        model = self.app.models.get_any(self.model_id) if self.model_id else None
        name = (model or {}).get("name") or self.model_id or "local model"
        where = (f"This reply comes from {name}, an online model the user chose for this work; KimiLM's tools "
                 "run on the user's computer." if is_cloud(self.model_id) else
                 f"This reply comes from a local model ({name}): nothing the user shares leaves this computer "
                 "unless a tool sends it (web pages, connectors).")
        parts = [BASE_PROMPT.format(where=where,
                                    today=datetime.now().strftime("%A %d %B %Y"),  # date only: a changing prefix defeats prompt caching
                                    os=f"{platform.system()} {platform.release()}")]
        if self.chat.get("parent_chat_id"):
            # No agent name or task here: agents with the same harness and model then share this whole prompt
            # and the tool list, so the model reads them once per run and takes them from its cache afterwards.
            parts.append("You are one agent in a plan run by a planner. Do only your task, then reply with a clear, "
                         "self-contained result (facts, numbers, file paths) that the planner can check and combine "
                         "with the other agents' results."
                         + (" You can read the team board or ask the planner."
                            if "team_board" in self.tool_map else ""))
        if tools:
            parts.append(AGENT_RULES)
            folders = self.folders()
            parts.append("Folders you may use: " + ", ".join(dict.fromkeys(folders + [str(self.workspace)])) +
                         f"\nRelative paths start in {folders[0] if folders else self.workspace}. "
                         f"Files you create without a folder go to {self.workspace}.")
        if self.harness.get("system_prompt"):
            parts.append(self.harness["system_prompt"].strip())
        if self.project and (self.project.get("instructions") or "").strip():
            parts.append(f"Project “{self.project['name']}” instructions:\n{self.project['instructions'].strip()}")
        # Memory, notes and knowledge passages chosen for this request are NOT here: they change from request to
        # request, and anything that changes in the system prompt makes the model re-read everything after it
        # (including the tool list) on every turn. They go on the latest message instead (request_context).
        recall = getattr(self, "recall_blocks", {})
        if "search_chats" in self.tool_map:
            parts.append("When I refer to something from an earlier conversation, look it up with search_chats and "
                         "read_chat instead of guessing." + (" This project has knowledge files: use "
                                                             "search_knowledge to look things up in them."
                                                             if recall.get("has_docs") else ""))
        if "load_skill" in self.tool_map:
            index = self.app.skills.index()
            if index:
                parts.append("Skills (reusable instructions). If one fits the task, call load_skill first:\n" + index)
        if getattr(self, "connector_index", None):
            names = sorted({t.label.split(':')[0] for t in self.connector_index})
            parts.append("Connected services: " + ", ".join(names) +
                         ". Use find_connector_tools then use_connector_tool to work with them.")
        if self.chat.get("summary"):
            parts.append("Summary of the earlier conversation:\n" + self.chat["summary"])
        return "\n\n".join(parts)

    def history(self):
        rows = self.db.all("SELECT * FROM messages WHERE chat_id=? ORDER BY created, rowid", (self.chat["id"],))
        if self.chat.get("summary_upto"):
            ids = [r["id"] for r in rows]
            if self.chat["summary_upto"] in ids:
                rows = rows[ids.index(self.chat["summary_upto"]) + 1:]
        out = []
        for r in rows:
            meta = r.get("meta") or {}
            if meta.get("pending") or meta.get("not_sent"):
                continue
            if r["role"] == "user":
                item = {"role": "user", "content": self._user_content(r), "_id": r["id"],
                        "_media": [a["path"] for a in meta.get("attachments", []) if media.kind_of(a.get("path", ""))]}
                if out and out[-1]["role"] == "user":     # follow-ups sent while the agent worked: one message
                    out[-1]["content"] += "\n\n" + item["content"]
                    out[-1]["_media"] += item["_media"]
                    out[-1]["_id"] = r["id"]
                else:
                    out.append(item)
            elif r["role"] == "assistant":
                used = [s for s in meta.get("steps", []) if s.get("type") == "tool"]
                note = ""
                if used:
                    note = "\n\n(Tools used: " + "; ".join(
                        f"{s.get('label') or s['name']}{'' if s.get('ok', True) else ' (failed)'}" for s in used[:8]) + ")"
                content = (r["content"] or "").strip() or "(no answer)"
                out.append({"role": "assistant", "content": content + note, "_id": r["id"]})
        return out

    def _user_content(self, row):
        meta = row.get("meta") or {}
        text = row["content"] or ""
        for att in meta.get("attachments", []):
            p = Path(att["path"])
            kind = media.kind_of(p)
            text += f"\n\n[Attached {kind or 'file'}: {att['name']} ({att.get('size', 0):,} bytes) at {p}]"
            if att.get("inline") and p.exists() and p.stat().st_size <= 12000:
                try:
                    text += f"\n```\n{p.read_text(encoding='utf-8', errors='replace')[:12000]}\n```"
                except OSError:
                    pass
        return text

    def last_user_text(self):
        row = self.db.one("SELECT content FROM messages WHERE chat_id=? AND role='user' ORDER BY created DESC, "
                          "rowid DESC LIMIT 1", (self.chat["id"],))
        return (row or {}).get("content") or ""

    async def attach_media(self, messages, history):
        """Put images / video frames of the latest message in front of a vision model."""
        last = history[-1] if history else None
        if not last or last["role"] != "user" or not last.get("_media"):
            return messages
        files = [p for p in last["_media"] if Path(p).exists()]
        if not files:
            return messages
        if not self.vision:
            self.note("This model cannot see images or videos, so it only knows the file names. Pick a model with "
                      "the eye icon (vision) to have them analysed.")
            return messages
        ctx = self.server.plan["ctx"]
        frames = max(3, min(8, (ctx - 3000) // 700))
        parts = [{"type": "text", "text": messages[-1]["content"]}]
        self.status("Looking at the attached media…")
        for p in files[:6]:
            try:
                parts += await asyncio.to_thread(media.parts_for, p, frames)
            except Exception as exc:
                parts.append({"type": "text", "text": f"({Path(p).name} could not be opened: {type(exc).__name__})"})
        messages[-1] = {"role": "user", "content": parts}
        return messages

    async def fit_history(self, system, tools, messages):
        ctx_tokens = self.server.plan["ctx"] if self.server else 8192
        reserve = self.max_reply
        fixed = estimate_tokens(system) + estimate_tokens(json.dumps([t.schema() for t in tools]))
        media_tokens = 900 * len((messages[-1] if messages else {}).get("_media") or []) if self.vision else 0
        budget = ctx_tokens - reserve - fixed - media_tokens - 200
        total = sum(estimate_tokens(m["content"]) for m in messages)
        if total <= budget * 0.85 or len(messages) <= 3:
            if total > budget:  # one huge message: trim it
                last = messages[-1]
                limit = max(2000, int(budget * 3.2))
                if isinstance(last["content"], str) and len(last["content"]) > limit:   # only when something is cut
                    last["content"] = last["content"][:limit] + "\n…(trimmed to fit the model's memory)"
            return messages
        keep = messages[-3:]
        older = messages[:-3]
        self.status("Summarising the earlier conversation to fit the model's context…")
        try:
            summary = await self.app.memory.summarise(self.chat, older)
            self.chat["summary"] = summary
            self.db.update("chats", self.chat["id"], {"summary": summary, "summary_upto": older[-1]["_id"]})
            self.stats["model_calls"] += 1
        except Exception:
            pass
        return keep

    # ------------------------------------------------------------------ follow-ups ("steer")
    def take_steer(self, messages):
        rows = self.app.pending_messages(self.chat["id"], "steer")
        for r in rows:
            self.app.mark_message(r, pending=None, steered=True)
            messages.append({"role": "user", "content": "(Message from me while you were working — take it into "
                                                        f"account from now on:)\n{r['content']}"})
            self.steps.append({"type": "steer", "text": r["content"], "message_id": r["id"]})
            self.emit("steer", text=r["content"], steer_id=r["id"])
        return bool(rows)

    # ------------------------------------------------------------------ running
    def rough_need(self):
        """About how much context this turn needs (history, tools, its steps, the reply)."""
        if self.chat.get("summary"):
            history = 3000
        else:
            rows = self.db.all("SELECT content FROM messages WHERE chat_id=?", (self.chat["id"],))
            history = sum(estimate_tokens(r["content"]) for r in rows)
        tools = 0 if self.harness.get("mode") == "chat" else 3000 + 4500
        return int((history + tools + self.max_reply) * 1.2)

    def prefit(self):
        """A request that will probably become a planned run loads its model sized for the run from the start
        (one reply slot, the context its steps need) instead of the largest setting, so it is loaded once."""
        if self.chat.get("parent_chat_id") or self.run_fit is not None or is_cloud(self.model_id) or \
                not self.db.setting("fit_context", True) or (self.job.get("payload") or {}).get("no_team"):
            return
        mode = self.harness.get("mode", "agent")
        team_mode = self.db.setting("team_mode", "auto")
        likely = mode == "team" or (mode in ("agent", "planner") and (
            team_mode == "always" or (team_mode == "auto" and looks_multistep(self.last_user_text()))))
        if not likely:
            return
        fit = RunFit(sequence=self.db.setting("agent_order", "sequence") != "parallel")
        fit.want(self.model_id, min(self.rough_need(), 24000))
        self.run_fit = self.app.run_fit[self.chat["id"]] = fit

    async def refit(self, messages, tools, max_steps):
        """The request stays with one agent after all: size the context for that (history, tools, its steps)."""
        if self.run_fit is None or is_cloud(self.model_id) or self.server is None:
            return
        size = sum(estimate_tokens(m["content"] if isinstance(m.get("content"), str) else
                                   json.dumps(m.get("content"), default=str)) for m in messages)
        size += estimate_tokens(json.dumps([t.schema() for t in tools])) if tools else 0
        self.run_fit.want(self.model_id, int((size + min(max_steps * 900, 6000) + self.max_reply) * 1.3))
        if self.run_fit.overrides(self.app.runtime, self.model_id).get("ctx") != self.server.plan.get("ctx"):
            await self.drop_model()
            await self.hold_model()

    async def run(self):
        try:
            await self._run()
        finally:
            if not self.chat.get("parent_chat_id"):
                self.app.run_fit.pop(self.chat["id"], None)
            if self.unload_after:              # the run's models are not needed any more: give the memory back
                try:
                    await self.app.runtime.release(only=self.unload_after)
                except Exception as exc:
                    self.app.log(f"unloading after a run: {type(exc).__name__}: {exc}")

    async def _run(self):
        started = time.monotonic()
        USAGE.set({"chat_id": self.chat["id"], "job_id": self.job["id"],
                   "purpose": "agent" if self.chat.get("parent_chat_id") else
                   ("schedule" if self.chat.get("origin") == "schedule" else "chat")})
        if self.harness.get("type") != "pipeline" and self.auto:
            self.resolve_auto()
        self.emit("assistant_start", harness=self.harness["name"], model=self.model_id,
                  agent=self.chat.get("agent_name"))
        if self.route_reason:
            self.note(self.route_reason)
        try:
            if self.harness.get("type") == "pipeline":
                await self.run_pipeline()
            else:
                if not self.model_id or self.model_id == "auto":
                    raise TurnStopped("No model is installed yet. Open Models and download one (the recommended "
                                      "one is marked).")
                self.prefit()
                if self.harness.get("mode") == "team":
                    await self.run_orchestra()
                else:
                    await self.hold_model()
                    await self.run_loop()
        except asyncio.CancelledError:
            self.steps.append({"type": "note", "text": "Stopped by the user."})
            await self._safe_drop()
            self.finish(time.monotonic() - started, stopped=True)
            raise
        except (TurnStopped, CloudError) as exc:
            await self._safe_drop()
            self.steps.append({"type": "error", "text": str(exc)})
            self.emit("error", text=str(exc))
            self.finish(time.monotonic() - started, error=str(exc))
            return
        except Exception as exc:
            await self._safe_drop()
            msg = f"{type(exc).__name__}: {str(exc)[:500]}"
            self.steps.append({"type": "error", "text": msg})
            self.emit("error", text=msg)
            self.finish(time.monotonic() - started, error=msg)
            raise
        await self._safe_drop()
        self.finish(time.monotonic() - started)

    async def _safe_drop(self):
        try:
            await self.drop_model()
        except Exception as exc:
            self.app.log(f"releasing the model after a turn: {type(exc).__name__}: {exc}")

    def finish(self, seconds, stopped=False, error=None):
        self.stats["seconds"] = round(seconds, 1)
        content = "".join(self.text).strip()
        model = self.app.models.get_any(self.model_id) if self.model_id and self.model_id != "auto" else None
        meta = {"steps": self.steps, "model": self.model_id, "harness": self.harness["id"],
                "model_name": (model or {}).get("name"), "online": is_cloud(self.model_id), "auto": self.auto,
                "harness_name": self.harness["name"], "stats": self.stats,
                "reasoning": "".join(self.reasoning)[-20000:], "skills_loaded": sorted(self.ctx.loaded_skills) if self.ctx else [],
                "stopped": stopped, "error": error, "job_id": self.job["id"]}
        if getattr(self, "plan", None):
            meta["plan"] = self.plan
        if self.guard.stop_reason:
            meta["guard"] = self.guard.stop_reason
        self.db.insert("messages", {"id": self.message_id, "chat_id": self.chat["id"], "role": "assistant",
                                    "content": content, "meta": meta, "created": now()})
        self.db.update("chats", self.chat["id"], {"updated": now()})
        self.emit("assistant_end", content=content, meta=meta)
        if not error and not stopped and self.harness.get("learn", True) and self.harness.get("type") != "pipeline" \
                and not self.chat.get("parent_chat_id"):
            self.app.memory.schedule_learning(self.chat["id"])

    def new_ctx(self):
        self.ctx = ToolContext(app=self.app, chat=self.chat, project=self.project, workspace=self.workspace,
                               folders=self.folders(), readonly=self.app.skills.roots(), emit=self.emit,
                               approve=self.approve, job_id=self.job["id"], model_id=self.model_id,
                               harness=self.harness)
        return self.ctx

    async def recall(self):
        """Memory, project notes and knowledge passages for this request (chosen by relevance, so it is async)."""
        blocks = {}
        query = self.last_user_text()
        if self.harness.get("memory", True):
            try:
                blocks["memory"] = await self.app.memory.context(self.chat, self.project, self.harness["id"], query)
            except Exception as exc:
                self.app.log(f"memory context: {type(exc).__name__}: {exc}")
            if self.project and self.app.memory.enabled(self.project):
                notes = (self.project.get("notes") or "").strip()
                if notes:
                    blocks["notes"] = (f"Project notes (kept by the user and you; add decisions and status with "
                                       f"update_project_notes):\n{notes[-4000:]}")
                blocks["has_docs"] = bool(self.db.one("SELECT 1 FROM kb_docs WHERE project_id=? AND status='ready'",
                                                      (self.project["id"],)))
                if blocks["has_docs"] and query:
                    try:
                        blocks["knowledge"], n = await self.app.knowledge.knowledge_block(self.project, query)
                        if n:
                            self.status(f"Found {n} matching passage{'s' if n > 1 else ''} in the project's files")
                    except Exception as exc:
                        self.app.log(f"knowledge passages: {type(exc).__name__}: {exc}")
        self.recall_blocks = blocks

    def request_context(self):
        """What memory, project notes and knowledge files say about this request: put in front of the latest
        message, so the system prompt and tool list stay identical between turns and the model reuses them from
        its cache instead of re-reading them (on a laptop CPU that is minutes per turn)."""
        recall = getattr(self, "recall_blocks", {})
        parts = []
        if recall.get("memory"):
            self.private.add("memory")
            parts.append(recall["memory"] + "\nUse these when relevant; the user can edit them on the Memory page.")
        if recall.get("notes"):
            self.private.add("notes")
            parts.append(recall["notes"])
        if recall.get("knowledge"):
            self.private.add("knowledge")
            parts.append(recall["knowledge"])
        return "\n\n".join(parts)

    @staticmethod
    def with_context(messages, context):
        if not context:
            return messages
        for m in reversed(messages):
            if m.get("role") == "user":
                note = f"<context>\n{context}\n</context>\n\n"
                if isinstance(m.get("content"), list):
                    m["content"] = [{"type": "text", "text": note}] + m["content"]
                else:
                    m["content"] = note + (m.get("content") or "")
                break
        return messages

    async def base_messages(self, tools):
        await self.recall()
        system = self.system_prompt(tools)
        context = self.request_context()
        history = self.history()
        history = await self.fit_history(system + ("\n\n" + context if context else ""), tools, history)
        messages = [{"role": "system", "content": system}] + [
            {k: v for k, v in m.items() if not k.startswith("_")} for m in history]
        return self.with_context(await self.attach_media(messages, history), context)

    async def run_loop(self):
        self.new_ctx()
        mode = self.harness.get("mode", "agent")
        tools = await self.build_tools() if mode != "research" else []
        messages = await self.base_messages(tools)
        if mode in ("agent", "planner") and tools and self.wants_team():
            orch = Orchestra(self, fit=self.run_fit)
            if await orch.plan(messages, allow_direct=self.db.setting("team_mode", "auto") != "always"):
                try:
                    await orch.drive(messages)
                    return await orch.final(messages)
                finally:
                    orch.close()
            self.status("Working…")
        if mode in ("agent", "planner") and tools:
            await self.refit(messages, tools, int(self.limits.get("max_steps") or self.harness.get("max_steps") or 10))
        if mode == "research":
            await self.run_research(messages)
        elif mode == "chat" or not tools:
            await self.stream_text(messages, None)
        elif mode == "planner":
            await self.run_planner(messages, tools)
        else:
            steps = int(self.limits.get("max_steps") or self.harness.get("max_steps") or 10)
            await self.agent_loop(messages, tools, steps)

    # ---- one model call, streaming text, collecting tool calls
    async def recover_model(self, exc):
        """The model hung or its server went away: stop that server and load a fresh one."""
        server = self.server
        self.note(f"The model stopped responding ({str(exc)[:160]}). Restarting it and trying again.")
        self.app.log(f"restarting {self.model_id} in chat {self.chat['id']}: {type(exc).__name__}: {exc}")
        await self.drop_model()
        if server is not None:
            await self.app.runtime.restart(server)
        await self.hold_model()

    @staticmethod
    def gave_up(exc):
        return TurnStopped(f"The model stopped responding twice in a row ({str(exc)[:200]}). It was restarted in "
                           "between. Try again with a smaller context or a smaller model, or close other apps to "
                           "free memory.")

    async def stream_text(self, messages, tools, final=True, max_tokens=None, _retry=False):
        thinking = bool(self.chat.get("thinking")) or bool(self.harness.get("thinking"))
        buffer = []
        await self.before_model_call(messages)
        self.stats["model_calls"] += 1
        self.status("Thinking…" if self.stats["model_calls"] == 1 else "Working…")
        end = None
        thinking = thinking or (self.route_role == "strategist" and is_cloud(self.model_id))
        try:
            async for kind, value in self.app.llm.stream(messages, tools=[t.schema() for t in tools] if tools else None,
                                                         max_tokens=int(max_tokens or self.max_reply),
                                                         temperature=self.temperature, thinking=thinking,
                                                         server=self.server):
                if kind == "token":
                    buffer.append(value)
                    self.emit("token", text=value)
                elif kind == "reasoning":
                    self.reasoning.append(value)
                    self.emit("reasoning", text=value)
                elif kind == "end":
                    end = value
        except CloudError as exc:
            if not (self.auto and is_cloud(self.model_id)) or buffer:
                raise
            await self.switch_to_local(f"the online model failed ({str(exc)[:160]})")
            return await self.stream_text(messages, tools, final=final, max_tokens=max_tokens)
        except (LLMTimeout, LLMUnreachable) as exc:
            if is_cloud(self.model_id):
                raise
            if _retry:
                raise self.gave_up(exc) from exc
            await self.recover_model(exc)
            if buffer:                              # drop the half-written text; it is written again
                self.emit("text_replace", text="")
            return await self.stream_text(messages, tools, final=final, max_tokens=max_tokens, _retry=True)
        self._count(end)
        self._last_native = end.get("native")
        text = "".join(buffer)
        calls = end["tool_calls"]
        if not calls and tools and "<tool_call>" in text:  # model wrote the call as text
            for m in TEXT_TOOLCALL.finditer(text):
                try:
                    obj = json.loads(m.group(1))
                    calls.append({"id": new_id("call_"), "name": obj.get("name"), "arguments": obj.get("arguments") or {}})
                except json.JSONDecodeError:
                    pass
            if calls:
                text = TEXT_TOOLCALL.sub("", text).strip()
                self.emit("text_replace", text=text)
        if not tools and "<tool_call>" in text:   # asked for a final answer but it still tried to call tools
            text = TEXT_TOOLCALL.sub("", text).strip()
            self.emit("text_replace", text=text)
        if text.strip():
            self.steps.append({"type": "text", "text": text})
            self.text = [text] if final or not calls else self.text
        if end.get("finish_reason") == "length" and not calls:
            self.note(f"The reply reached its length limit ({int(max_tokens or self.max_reply):,} tokens). Raise "
                      "'Reply length' in this chat's limits for longer answers.")
        return text, calls

    def _count(self, end):
        timings = (end or {}).get("timings") or {}
        if timings.get("online"):
            self.stats["cost"] = round(float(self.stats.get("cost") or 0) + float(timings.get("cost") or 0), 6)
            if timings.get("cost_estimated"):
                self.stats["cost_estimated"] = True
        self.stats["prompt_tokens"] += int(timings.get("prompt_n") or 0)
        self.stats["cached_tokens"] += int(timings.get("cache_n") or 0)   # reused from the model's cache, not re-read
        size = int(timings.get("prompt_n") or 0) + int(timings.get("cache_n") or 0)   # this call's whole prompt
        if size:
            self.stats.setdefault("first_prompt", size)       # system prompt + tools + the request (forecaster)
            self.stats["peak_prompt"] = max(size, int(self.stats.get("peak_prompt") or 0))
        if timings.get("prompt_per_second") and int(timings.get("prompt_n") or 0) >= 8:
            self.stats["read_tps"] = round(timings["prompt_per_second"], 1)
        self.stats["output_tokens"] += int(timings.get("predicted_n") or 0)
        if timings.get("predicted_per_second"):
            self.stats["tokens_per_second"] = round(timings["predicted_per_second"], 1)

    async def complete(self, messages, **kw):
        await self.before_model_call(messages)
        self.stats["model_calls"] += 1
        try:
            out = await self.app.llm.complete(messages, server=self.server, **kw)
        except CloudError as exc:
            if not (self.auto and is_cloud(self.model_id)):
                raise
            await self.switch_to_local(f"the online model failed ({str(exc)[:160]})")
            out = await self.app.llm.complete(messages, server=self.server, **kw)
        except (LLMTimeout, LLMUnreachable) as exc:
            if is_cloud(self.model_id):
                raise
            await self.recover_model(exc)
            try:
                out = await self.app.llm.complete(messages, server=self.server, **kw)
            except (LLMTimeout, LLMUnreachable) as again:
                raise self.gave_up(again) from again
        self._count(out)
        return out

    async def run_tool(self, call):
        tool = self.tool_map.get(call["name"])
        step = {"type": "tool", "id": call["id"], "name": call["name"], "arguments": call["arguments"],
                "label": tool.label if tool else call["name"]}
        self.steps.append(step)
        self.emit("tool_call", call={**step, "description": tool.describe_call(call["arguments"]) if tool else ""})
        blocked = self.guard.check(call["name"], call["arguments"]) if tool else None
        if blocked:
            result, ok = blocked, False
            step["blocked"] = True
        elif not tool:
            result, ok = f"There is no tool called {call['name']!r}. Available: {', '.join(self.tool_map)}", False
        elif "_unparsed" in (call["arguments"] or {}):
            result, ok = "The arguments were not valid JSON; try again with valid JSON arguments.", False
        else:
            try:
                if call["name"] != "use_connector_tool" and not await self.approve(
                        tool, call["arguments"], tool.describe_call(call["arguments"])):
                    raise ToolDenied("The user declined this action.")
                if tool.group == "vision" and is_cloud(self.model_id):
                    self.private.add("files")        # the tool sends the picture itself: ask first
                    await self.before_model_call([])
                self.app.jobs.progress(self.job["id"], f"{tool.label or tool.name}")
                result = await tool.func(self.ctx, **(call["arguments"] or {}))
                ok = True
                if tool.group in ("files", "vision", "python"):
                    self.private.add("files")
                if call["name"] in PRIVATE_MEMORY_TOOLS:
                    self.private.add(PRIVATE_MEMORY_TOOLS[call["name"]])
            except ToolDenied as exc:
                result, ok = str(exc), False
                step["declined"] = True
            except (ToolError, ValueError, FileNotFoundError, PermissionError, ConnectionError, KeyError) as exc:
                result, ok = f"Error: {exc}", False
            except TypeError as exc:
                result, ok = f"Error: wrong arguments ({exc})", False
            except Exception as exc:
                result, ok = f"Error: {type(exc).__name__}: {str(exc)[:400]}", False
        result = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        self.guard.record(call["name"], call["arguments"], ok, result, blocked=bool(blocked),
                          changed=bool(ok and tool and (tool.changes or call["name"] == "use_connector_tool")))
        step.update(ok=ok, result=result[:4000])
        self.emit("tool_result", call_id=call["id"], ok=ok, result=result[:4000], declined=step.get("declined", False),
                  blocked=bool(blocked))
        if len(result) > TOOL_RESULT_LIMIT:
            result = result[:TOOL_RESULT_LIMIT] + f"\n…(trimmed; {len(result) - TOOL_RESULT_LIMIT:,} more characters)"
        return result

    def wants_team(self):
        """Should the planner look at this request? Cheap filter first; the planner can still say 'direct'."""
        if self.chat.get("parent_chat_id") or "delegate" not in self.tool_map or \
                (self.job.get("payload") or {}).get("no_team"):
            return False
        mode = self.db.setting("team_mode", "auto")
        return mode == "always" or (mode == "auto" and looks_multistep(self.last_user_text()))

    async def wait_if_paused(self):
        """Paused by the user or the planner: give the model back and wait (the time does not count)."""
        p = self.app.pauses.get(self.chat["id"])
        if not p or p["event"].is_set():
            return
        reason = p.get("reason") or "paused"
        began = time.monotonic()
        self.note(f"Paused: {reason}.")
        self.status(f"Paused: {reason}")
        await self.drop_model()
        await p["event"].wait()
        self.guard.deadline += time.monotonic() - began
        self.note("Resumed.")
        await self.hold_model()

    async def run_orchestra(self):
        """Team harnesses and the composer's team builder: the planner always runs a team."""
        spec = [dict(a) for a in (self.job.get("payload", {}).get("team") or []) if a.get("name")]
        await self.hold_model()
        self.new_ctx()
        messages = await self.base_messages([])
        orch = Orchestra(self, roster=spec or None, fit=self.run_fit)
        if spec and all((a.get("task") or "").strip() for a in spec):
            orch.preset([{"id": f"t{i}", "name": a["name"], "agent": a["name"], "goal": a["task"],
                          "harness": a.get("harness"), "model": a.get("model"), "limits": a.get("limits"),
                          "role": a.get("role")} for i, a in enumerate(spec, 1)])
            orch.logline(f"Your team: {len(orch.tasks)} agents with their own tasks.")
        else:
            await orch.plan(messages, allow_direct=False)
        try:
            await orch.drive(messages)
            await orch.final(messages)
        finally:
            orch.close()

    async def agent_loop(self, messages, tools, max_steps):
        use_json = self.harness.get("tool_mode") == "json"
        self.loop_messages = messages
        for step in range(max_steps):
            await self.wait_if_paused()
            self.take_steer(messages)
            reason = self.guard.should_stop()
            if reason:
                return await self.write_up(messages, reason)
            if use_json:
                calls, done = await self.json_decide(messages, tools)
                if done:
                    await self.stream_text(messages, None)
                    return
                text = ""
            else:
                try:
                    text, calls = await self.stream_text(messages, tools, final=True)
                except LLMError as exc:
                    if isinstance(exc, (LLMTimeout, LLMUnreachable, LLMContextFull)):
                        raise
                    if "tool" in str(exc).lower() or "500" in str(exc):
                        self.status("This model's template has no native tool calling; switching to JSON mode")
                        use_json = True
                        continue
                    raise
            if not calls:
                if self.take_steer(messages):     # a follow-up arrived while it was answering: carry on
                    messages.insert(len(messages) - 1, {"role": "assistant", "content": text or "(working)"})
                    continue
                return
            messages.append({"role": "assistant", "content": text or "", "_native": self._last_native,
                             "tool_calls": [{"id": c["id"], "type": "function",
                                             "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                                            for c in calls]})
            for call in calls:
                result = await self.run_tool(call)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            nudge = self.guard.nudge()
            if nudge:
                self.note("The agent was not making progress, so the app asked it to change approach.")
                messages.append({"role": "user", "content": nudge})
        await self.write_up(messages, f"it used all {max_steps} steps this harness allows")

    async def write_up(self, messages, reason):
        self.status("Writing up what was found…")
        self.note(f"Stopped using tools because {reason}.")
        messages.append({"role": "user", "content": f"Stop using tools now ({reason}). Using only what you have "
                                                     "already found, tell me: what you did, what you found (with "
                                                     "sources/files), and what is still missing or could not be done."})
        self.text = []
        await self.stream_text(messages, None)

    async def json_decide(self, messages, tools):
        """Grammar-constrained decision for models without native tool calling."""
        options = [{"type": "object", "required": ["action"], "properties": {"action": {"const": "answer"}}}]
        for t in tools:
            params = t.parameters if len(json.dumps(t.parameters)) < 2500 else {"type": "object"}
            options.append({"type": "object", "required": ["action", "tool", "arguments"],
                            "properties": {"action": {"const": "use_tool"}, "tool": {"const": t.name},
                                           "arguments": params}})
        guide = ("Decide the next step. Tools:\n" + "\n".join(f"- {t.name}: {t.description[:200]}" for t in tools) +
                 "\nReply with JSON: {\"action\":\"use_tool\",\"tool\":…,\"arguments\":{…}} or {\"action\":\"answer\"}.")
        self.status("Deciding the next step…")
        out = await self.complete(messages + [{"role": "user", "content": guide}], schema={"oneOf": options},
                                  max_tokens=700)
        decision = out["json"]
        if decision.get("action") != "use_tool":
            return [], True
        call = {"id": new_id("call_"), "name": decision["tool"], "arguments": decision.get("arguments") or {}}
        messages.append({"role": "assistant", "content": json.dumps(decision)})
        result = await self.run_tool(call)
        messages.append({"role": "user", "content": f"Tool result for {call['name']}:\n{result}"})
        return [], False

    async def run_planner(self, messages, tools):
        n = int(self.harness.get("plan_max_steps") or 5)
        schema = {"type": "object", "required": ["steps"], "properties": {
            "steps": {"type": "array", "minItems": 1, "maxItems": n, "items": PLAN_SCHEMA_STEP}}}
        self.status("Making a plan…")
        try:
            plan = (await self.complete(
                messages + [{"role": "user", "content": f"Before doing anything, break this request into at most {n} "
                                                         "concrete steps (fewer is better). JSON only."}],
                schema=schema, max_tokens=600))["json"]["steps"]
        except LLMError:
            plan = [{"title": "Do the task", "detail": ""}]
        self.plan = [{"title": s["title"], "detail": s.get("detail", ""), "state": "todo"} for s in plan]
        self.emit("plan", steps=self.plan)
        total = int(self.limits.get("max_steps") or self.harness.get("max_steps") or 12)
        per_step = max(2, total // max(1, len(plan)))
        messages.append({"role": "assistant", "content": "Plan:\n" + "\n".join(
            f"{i}. {s['title']}" for i, s in enumerate(self.plan, 1))})
        for i, s in enumerate(self.plan, 1):
            if self.guard.should_stop():
                s["state"] = "skipped"
                continue
            s["state"] = "doing"
            self.emit("plan", steps=self.plan, current=i - 1)
            self.steps.append({"type": "plan_step", "index": i, "title": s["title"]})
            messages.append({"role": "user", "content": f"Now do step {i}: {s['title']}. {s['detail']}\n"
                                                         "When this step is done, reply with a short result."})
            await self.agent_loop(messages, tools, per_step)
            messages.append({"role": "assistant", "content": "".join(self.text)[-1500:] or "(done)"})
            s["state"] = "done"
        self.emit("plan", steps=self.plan, current=len(self.plan))
        stopped = self.guard.stop_reason
        messages.append({"role": "user", "content": ("Some steps were skipped because " + stopped + ". " if stopped else "")
                         + "Give me the final answer now: results, files created, and anything left open."})
        self.text = []
        self.steps.append({"type": "plan_step", "index": len(self.plan) + 1, "title": "Final answer"})
        await self.stream_text(messages, None)

    # ------------------------------------------------------------------ research (fixed stages, cannot loop)
    def pseudo_tool(self, name, label, args, description):
        step = {"type": "tool", "id": new_id("call_"), "name": name, "arguments": args, "label": label}
        self.steps.append(step)
        self.emit("tool_call", call={**step, "description": description})
        return step

    def pseudo_done(self, step, ok, result):
        step.update(ok=ok, result=result[:4000])
        self.emit("tool_result", call_id=step["id"], ok=ok, result=result[:4000], declined=False)

    async def run_research(self, messages):
        from .tools import web
        question = self.last_user_text()
        self.take_steer(messages)
        self.status("Planning the searches…")
        n_queries = int(self.harness.get("queries") or 4)
        try:
            plan = (await self.complete(messages + [{"role": "user", "content": (
                "Plan web research for my last message. If it can be answered from this conversation alone (for "
                "example it asks to reformat or explain earlier results) set action to 'answer'. Otherwise set action "
                f"'research' and write up to {n_queries} different search queries of 3-8 plain words each (different "
                "angles; include the organisation that publishes such information when you know it). Set want_files "
                "true if I want data, tables or documents downloaded. known_sources: full URLs of official pages you "
                "are confident exist for this topic (or none). JSON only.")}], schema=RESEARCH_PLAN, max_tokens=500,
                temperature=0.2))["json"]
        except LLMError:
            plan = {"action": "research", "queries": [question[:120]], "want_files": False, "known_sources": []}
        if plan["action"] == "answer" and self.history()[:-1]:
            self.text = []
            return await self.stream_text(messages, None)
        queries = [q for q in dict.fromkeys(q.strip() for q in plan["queries"] if q.strip())][:n_queries] or [question[:120]]
        want_files = bool(plan.get("want_files")) or bool(re.search(r"(?i)\b(download|dataset|data|csv|excel|xlsx|pdf|report|table)s?\b", question))

        # 1. search -------------------------------------------------------------
        found = {}
        for q in queries:
            step = self.pseudo_tool("web_search", "Search the web", {"query": q}, f"Search: {q}")
            self.status(f"Searching: {q}")
            try:
                rows, report = await web.search(q, 8, self.app)
            except Exception as exc:
                rows, report = [], {"engines": [f"error {type(exc).__name__}"], "dropped": 0}
            for r in rows:
                key = r["url"].split("#")[0].rstrip("/")
                if key in found:
                    found[key]["score"] += r["score"] * 0.6
                    found[key]["queries"].append(q)
                else:
                    found[key] = {**r, "queries": [q]}
            self.pseudo_done(step, bool(rows), ("\n".join(f"- {r['title']} — {r['url']}" for r in rows) or
                                                "No relevant results") + f"\n({'; '.join(report['engines'])})")
        for url in plan.get("known_sources") or []:
            if re.match(r"https?://", url) and url.rstrip("/") not in found:
                found[url.rstrip("/")] = {"title": "Known source", "url": url, "snippet": "", "score": 0.9,
                                          "queries": ["known source"]}
        if self.take_steer(messages):
            question += "\n" + messages[-1]["content"]
        # 2. rank and pick pages -------------------------------------------------
        per_host, picked = Counter(), []
        for r in sorted(found.values(), key=lambda r: -r["score"]):
            host = web.host_of(r["url"])
            if per_host[host] >= 2:
                continue
            per_host[host] += 1
            picked.append(r)
            if len(picked) >= int(self.harness.get("pages") or 6):
                break
        if not picked:
            self.note("No relevant sources were found online for this question.")
            messages.append({"role": "user", "content": (
                "Web research found no relevant sources (searches tried: " + "; ".join(queries) + "). Tell me plainly "
                "that nothing reliable was found, give what you know with a clear warning that it is unverified, and "
                "suggest where I could look (organisations, websites) or better search words.")})
            self.text = []
            return await self.stream_text(messages, None)
        # 3. read pages (in parallel) -------------------------------------------
        sem = asyncio.Semaphore(4)

        async def read(r):
            async with sem:
                step = self.pseudo_tool("web_fetch", "Read page", {"url": r["url"]}, f"Read {r['url']}")
                try:
                    page = await asyncio.wait_for(web.fetch_page(r["url"]), 60)
                except asyncio.TimeoutError:
                    page = {"kind": "error", "url": r["url"], "error": "took too long"}
                ok = page["kind"] not in ("error",)
                self.pseudo_done(step, ok, page.get("error") or f"{page.get('title') or r['title']} "
                                 f"({len(page.get('text') or ''):,} characters, {len(page.get('files') or [])} file links)")
                return r, page
        self.status(f"Reading {len(picked)} sources…")
        pages = await asyncio.gather(*(read(r) for r in picked))
        folder = self.workspace / "research" / datetime.now().strftime("%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        terms = set(web.terms_of(question + " " + " ".join(queries)))
        downloads = []
        for r, page in pages:            # a link that was itself a data file or PDF: keep it
            if page["kind"] == "binary" or (page["kind"] == "pdf" and want_files):
                downloads.append(r["url"])
        # 4. extract facts, page by page ----------------------------------------
        ctx_chars = max(4000, min(14000, (self.server.plan["ctx"] - 2200) * 3))
        sources = []
        for r, page in pages:
            if page["kind"] in ("error", "binary") or not (page.get("text") or "").strip():
                continue
            self.take_steer(messages)
            idx = len(sources) + 1
            text = focus(page["text"], terms, ctx_chars)
            files = page.get("files") or []
            file_list = ""
            if want_files and files:
                ranked = sorted(files, key=lambda f: -sum(t in (f["label"] + " " + f["url"]).lower() for t in terms))[:15]
                file_list = "\n\nFile links on the page:\n" + "\n".join(f"{i}. {f['label'] or '(no label)'} — {f['url']}"
                                                                       for i, f in enumerate(ranked, 1))
                files = ranked
            step = self.pseudo_tool("extract", "Take notes", {"source": idx}, f"Notes from {page.get('title') or r['url']}")
            self.status(f"Taking notes from source {idx}…")
            try:
                got = (await self.complete([
                    {"role": "system", "content": "You extract facts from one web page for a research question. Copy "
                     "numbers, dates and names exactly as written. Only facts stated on the page."},
                    {"role": "user", "content": f"Question: {question}\n\nPage: {page.get('title')}\nURL: {page['url']}\n\n"
                     f"{text}{file_list}\n\nReturn relevant=false if the page does not help. facts: up to 8 short "
                     "facts that answer the question." + (" files: numbers of the file links that contain the "
                                                          "requested data (at most 4)." if file_list else "")}],
                    schema=EXTRACT_SCHEMA, max_tokens=900, temperature=0.1))["json"]
            except LLMError as exc:
                self.pseudo_done(step, False, f"Could not take notes ({str(exc)[:120]})")
                continue
            if not got["relevant"] or not got["facts"]:
                self.pseudo_done(step, False, "Not relevant")
                continue
            for i in got.get("files") or []:
                if 1 <= i <= len(files):
                    downloads.append(files[i - 1]["url"])
            sources.append({"n": idx, "title": page.get("title") or r["title"], "url": page["url"],
                            "facts": got["facts"]})
            self.pseudo_done(step, True, "\n".join(f"- {f}" for f in got["facts"]))
        # 5. download files -----------------------------------------------------
        saved = []
        for url in list(dict.fromkeys(downloads))[:int(self.harness.get("max_downloads") or 6)]:
            step = self.pseudo_tool("download_file", "Download", {"url": url}, f"Download {url}")
            try:
                dest, size = await web.save_url(url, folder / "files", max_bytes=100 * 2**20)
                saved.append({"path": str(dest), "url": url, "size": size})
                self.emit("file_changed", path=str(dest), action="downloaded")
                self.pseudo_done(step, True, f"Saved {size:,} bytes to {dest}")
            except Exception as exc:
                self.pseudo_done(step, False, f"Error: {exc}")
        # 6. write the report ---------------------------------------------------
        notes = "\n\n".join(f"[{s['n']}] {s['title']} — {s['url']}\n" + "\n".join(f"- {f}" for f in s["facts"])
                            for s in sources) or "(no page had relevant facts)"
        files_txt = "\n".join(f"- {f['path']} (from {f['url']})" for f in saved)
        (folder / "notes.md").write_text(f"# Research notes\n\nQuestion: {question}\n\nSearches: {'; '.join(queries)}\n\n"
                                         f"{notes}\n\n## Downloaded files\n{files_txt or '(none)'}\n", encoding="utf-8")
        (folder / "sources.json").write_text(json.dumps({"question": question, "queries": queries, "sources": sources,
                                                         "files": saved}, indent=2, ensure_ascii=False), encoding="utf-8")
        self.steps.append({"type": "note", "text": f"Notes and sources saved in {folder}"})
        messages.append({"role": "user", "content": (
            f"Research notes from {len(sources)} sources (numbered):\n\n{notes}\n\n"
            + (f"Files downloaded:\n{files_txt}\n\n" if saved else "") +
            "Now answer my question using only these notes. Cite sources like [1] after each claim, say clearly what "
            "the sources did not cover, and end with a 'Sources' list (number, title, URL)"
            + (" and the downloaded files with their paths" if saved else "") + ".")})
        self.text = []
        self.status("Writing the answer…")
        await self.stream_text(messages, None)

    # ------------------------------------------------------------------ planned runs: who runs each step
    def assign_models(self, agents):
        """Agents without a model get one by role: workers on this computer when possible, strategists on the
        strongest model. With a local lead and no roles, agents share the chat's model as before."""
        default_role = self.harness.get("worker_role") or ("worker" if (self.auto or is_cloud(self.model_id)) else None)
        for a in agents:
            if str(a.get("model") or "").startswith("role:"):      # the team builder's "by role" choices
                a["role"], a["model"] = a["model"][5:], None
            if a.get("model") == "auto":
                a["model"] = None
            if a.get("model"):
                continue
            role = a.get("role") or default_role
            if not role:
                continue
            mid, _name = self.app.router.pick(role, chat=self.chat, project=self.project)
            if mid:
                a["model"] = mid

    def team_harnesses(self):
        return [h for h in self.app.harnesses.all() if h.get("type") == "loop" and h.get("mode") != "team"
                and h.get("enabled", True)]

    # ------------------------------------------------------------------ pipelines
    async def pipeline_inputs(self, user_text):
        schema = self.harness.get("inputs") or {"type": "object", "properties": {}}
        props = schema.get("properties", {})
        values = {}
        url = URL_RE.search(user_text or "")
        for key, spec in props.items():
            if "default" in spec:
                values[key] = spec["default"]
        for key, spec in props.items():
            if url and ("url" in key or spec.get("format") == "uri"):
                values[key] = url.group(0)
        local = self.local_model_id()
        if self.harness.get("input_prompt") and local and props:
            try:
                async with self.app.runtime.lease(local, on_wait=self.status) as server:
                    self.status("Reading your request…")
                    got = await self.app.llm.json([
                        {"role": "system", "content": self.harness["input_prompt"]},
                        {"role": "user", "content": user_text}], {**schema, "additionalProperties": False},
                        max_tokens=300, server=server)
                values.update({k: v for k, v in got.items() if v not in (None, "")})
            except Exception:
                pass
        text_key = next((k for k in ("topic", "query", "prompt", "subject", "text") if k in props), None)
        if text_key and not values.get(text_key) and not url:
            cleaned = (user_text or "").strip()
            if cleaned and cleaned.lower() not in ("run", "go", "start", "run it", "run now"):
                values[text_key] = cleaned[:200]
        if self.job.get("payload", {}).get("inputs"):
            values.update(self.job["payload"]["inputs"])
        return values

    async def run_pipeline(self):
        h = self.harness
        last_user = self.db.one("SELECT * FROM messages WHERE chat_id=? AND role='user' ORDER BY created DESC, rowid DESC LIMIT 1",
                                (self.chat["id"],))
        inputs = await self.pipeline_inputs((last_user or {}).get("content", ""))
        self.steps.append({"type": "pipeline", "harness": h["name"], "inputs": inputs})
        self.emit("pipeline_start", harness=h["name"], inputs=inputs)
        args = list(h.get("args") or [])
        flags = h.get("args_map") or {}
        for key, value in inputs.items():
            flag = flags.get(key)
            if not flag or value in (None, "", False):
                continue
            args += [flag] if value is True else [flag, str(value)]
        env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "DVA_WORKSPACE": str(self.workspace),
               "DVA_INPUTS": json.dumps(inputs)}
        if h.get("plugin"):
            plugin = self.app.plugins.get(h["plugin"])
            if not self.app.plugins.configured(plugin):
                raise TurnStopped(f"Set up the {plugin['name']} plugin first: Plugins & connectors → {plugin['name']} "
                                  "→ fill in the settings and press 'Save, check and connect'.")
            base_env = self.app.plugins.script_env(plugin)
        else:
            base_env = {**os.environ}
            base_env.pop("DVA_TOKEN", None)
        base_env.update(env)
        entry = (Path(h["dir"]) / h["entry"]).resolve()
        root = Path(h["dir"]).resolve() if not h.get("plugin") else Path(self.app.plugins.get(h["plugin"])["dir"]).resolve()
        if root not in entry.parents:
            raise TurnStopped("The pipeline's entry file must be inside its folder")
        timeout = int(h.get("timeout_minutes") or 60) * 60
        log_tail = []
        result = {}

        async def execute():
            nonlocal result
            if "browser" in (h.get("needs") or []):
                self.status("Starting the browser helper…")
                base_env["DVA_BROWSER_MCP_URL"] = await self.app.services.browser()
            proc = await asyncio.create_subprocess_exec(
                *launcher.command(entry, args), cwd=str(entry.parent), env=launcher.environment(base_env),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                creationflags=0x08000000 if os.name == "nt" else 0)
            try:
                async for raw in proc.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    if line.startswith("::result "):
                        try:
                            result = json.loads(line[9:])
                        except json.JSONDecodeError:
                            pass
                        continue
                    if line.startswith("::progress "):
                        try:
                            self.status(json.loads(line[11:]).get("text", ""))
                        except json.JSONDecodeError:
                            pass
                        continue
                    log_tail.append(line)
                    del log_tail[:-400]
                    self.emit("log", text=line)
                    self.app.jobs.log(self.job["id"], line)
                await proc.wait()
            except asyncio.CancelledError:
                proc.kill()
                raise
            return proc.returncode

        async def with_model():
            if "model" in (h.get("needs") or []):
                local = self.local_model_id()      # pipelines run on a model on this computer
                if not local:
                    raise TurnStopped("This pipeline needs a model on this computer; download one in Models first.")
                async with self.app.runtime.lease(local, on_wait=self.status) as server:
                    base_env.update({"DV_LLM_API": "openai", "LLM_BASE_URL": server.base_url,
                                     "LLM_API_KEY": server.api_key, "LLM_MODEL": local})
                    return await execute()
            return await execute()

        try:
            code = await asyncio.wait_for(with_model(), timeout)
        except asyncio.TimeoutError:
            raise TurnStopped(f"The pipeline ran longer than {timeout // 60} minutes and was stopped")
        summary = result.get("summary_markdown") or result.get("summary") or "\n".join(log_tail[-25:])
        self.text = [summary]
        self.steps.append({"type": "pipeline_end", "code": code, "result": result, "log_tail": log_tail[-60:]})
        self.emit("pipeline_end", code=code, result=result)
        learner = self.local_model_id()
        if learner and h.get("learn", True):
            outcome = f"exit code {code}\n{summary}\n" + "\n".join(log_tail[-40:])
            self.app.spawn(self.app.memory.reflect(h["id"], h["name"], outcome, learner))


def focus(text, terms, limit):
    """The beginning of a long page plus the lines that mention the question's words (with neighbours)."""
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    keep, size = set(), 0
    for i, line in enumerate(lines):
        if size > min(1500, limit // 4):
            break
        keep.add(i)
        size += len(line) + 1
    scored = sorted(((sum(t in line.lower() for t in terms), i) for i, line in enumerate(lines)),
                    key=lambda x: (-x[0], x[1]))
    for sc, i in scored:
        if sc == 0 or size >= limit:
            break
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(lines) and j not in keep and size + len(lines[j]) < limit:
                keep.add(j)
                size += len(lines[j]) + 1
    out, prev = [], -2
    for i in sorted(keep):
        if i != prev + 1:
            out.append("…")
        out.append(lines[i])
        prev = i
    return "\n".join(out)[:limit]


def _preview(args):
    text = json.dumps(args, ensure_ascii=False, default=str)
    return text if len(text) < 3000 else text[:3000] + "…"
