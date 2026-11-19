"""Failure recovery (2.15.0): what failed, why, and what has to be different next time.

Doing the same thing again and expecting another result is not a recovery. Every failure of a step (of a planned
run or of a long-running task) is written down as a record: what was attempted (the agent, its model, its time,
its goal), the error, and the class of the failure. The class decides what may happen next:

  transient    the outside world hiccuped (a connection dropped, a service was busy). The same attempt may be made
               again after a wait, a small number of times.
  stall        the model stopped answering. The model is started again and the same step is given to a fresh agent
               once.
  timeout      the step ran out of time. If it was still getting somewhere, it gets more time; if it was going
               round in circles, more time would only waste it: the work is cut into a smaller piece.
  memory       the computer ran out of memory: a smaller context, a smaller model or a smaller piece.
  context      the material did not fit the model's context: a larger context, or less to read at once.
  approach     the way of working led nowhere (a tool failed the same way, a source gave nothing, the agent went
               round in circles): the step is rewritten to go another way. It is never run again as it was.
  capability   the result was wrong in the same way twice: a stronger model or another kind of agent.
  missing      something the work needs is not there (a model, a program, a file, a connector): get it another
               way, or ask.
  permission   the user declined, or something needs the user (a sign-in, a key): ask; never try again unasked.

Two rules are kept by code and not left to a model's goodwill:
  1. An attempt that equals a failed one (the same goal for the same kind of agent on the same model with the
     same time) is refused, unless the class above says that the same attempt may be made again.
  2. After a few different recoveries in a row have failed too, the work pauses and the user is told what was
     tried (missions.py), instead of burning hours on a problem that needs a person.

Agents are told the same in their brief, and each leaves a 'Failed' line in its handoff: what it tried that did
not work and why. What its tools reported is added by code (tool_failures), so the record does not depend on a
small model remembering to write it.

Fail fast (2.20.0). An agent of a run that cannot finish says so at once (report_failure), and the app stops it
without waiting for its model when the answer is already plain (tripwire): a tool failed twice with the same error,
code failed twice with the same error (one fix is allowed: that is how code gets written), the user declined, a
service is unreachable after one more try, something only the user can give is missing, or two paths were refused.
Either way an entry is written to the run's register at that moment, and the planner decides by rule where the
answer is clear (fast_rule) and asks its model only where it is not."""
import re

from .db import now

# class -> what the user reads, whether the very same attempt may be made again (how often), and what a planner
# is told to do about it
CLASSES = {
    "transient": {"label": "a passing fault", "repeat": 2,
                  "advice": "Nothing was wrong with the step itself: the same step may be given again."},
    "stall": {"label": "the model stopped answering", "repeat": 1,
              "advice": "The model was started again: the same step may be given once more."},
    "timeout": {"label": "it ran out of time", "repeat": 0,
                "advice": "If it was still getting somewhere, give the same step with more time ('minutes'). If it "
                          "was going round in circles, give a smaller piece of it instead."},
    "memory": {"label": "the computer ran out of memory", "repeat": 0,
               "advice": "Give a smaller piece, or less material at once."},
    "context": {"label": "the material did not fit the model", "repeat": 0,
                "advice": "Give less to read at once: one file or one source per step."},
    "approach": {"label": "this way of doing it did not work", "repeat": 0,
                 "advice": "Go another way: another tool, another source, another order, or a smaller piece. "
                           "Say in the goal what to avoid."},
    "capability": {"label": "the result was wrong the same way twice", "repeat": 0,
                   "advice": "Give it to another kind of agent or a stronger model (role: strategist), or split it "
                             "so that each piece is easy to check."},
    "output": {"label": "its files were not left where they belong", "repeat": 0,
               "advice": "The agent was told about the missing files before it finished and still did not leave "
                         "them. Do not repeat the step as it was: give each file its own small step, say in the "
                         "goal to save a tool's answer with save_to (or to page through a service in one run_python "
                         "program with kimilm.call), or give it to a stronger model."},
    "missing": {"label": "something it needs is not there", "repeat": 0,
                "advice": "Get what is missing another way in a step of its own, or ask me for it ('ask')."},
    "permission": {"label": "it needs you", "repeat": 0,
                   "advice": "Do not try it again. Ask me ('ask'), or do without it."},
    "stopped": {"label": "it was stopped", "repeat": 0, "advice": "It was stopped by the user."},
}
TRANSIENT = re.compile(
    r"connection (reset|aborted|closed|refused)|server disconnected|temporar|try again later|rate.?limit|too many "
    r"requests|\b(429|502|503|504)\b|overloaded|service unavailable|bad gateway|gateway time|network is unreachable|"
    r"name resolution|getaddrinfo|dns|remote end closed|readerror|connecterror|read timed out|connect timeout|"
    r"did not start within|could not be reached", re.I)
STALL = re.compile(r"did not start answering|stopped writing|did not respond|not reachable|stopped responding", re.I)
TIMEOUT = re.compile(r"time limit of [\d.]+ minutes|took more than|ran past its time|longer than the limit|"
                     r"ran far past|used all \d+ steps|used its \d+ tool calls", re.I)
MEMORY = re.compile(r"not enough memory|out of memory|failed to allocate|bad_alloc|ran out of memory", re.I)
CONTEXT = re.compile(r"does not fit the model's context|context.{0,40}exceed|exceed.{0,40}context", re.I)
PERMISSION = re.compile(r"declined|needs you to sign in|needs its key|needs your approval|not allowed to|"
                        r"was not approved|sign in first|unauthori[sz]ed|\b401\b", re.I)
MISSING = re.compile(r"is not installed|not set up|no (image|video|sound|speech|music|vision)? ?model|there is no "
                     r"tool called|no model is installed|is unavailable|needs .{0,40} first|install it", re.I)
NOT_SAVED = re.compile(r"was not saved|files? (?:is|are) missing|not left", re.I)
LOOP = re.compile(r"produced nothing new|kept repeating|same error|did not produce anything", re.I)


def signature(text):
    """An error message without what changes from one time to the next (numbers, paths, addresses, ids), so that
    'the same error' can be recognised."""
    low = (text or "").lower()
    low = re.sub(r"https?://\S+", " URL ", low)
    low = re.sub(r"[a-z]:\\[^\s'\"]+|(?:/[\w.\-]+){2,}", " PATH ", low)
    low = re.sub(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{8,}\b", " ID ", low)
    low = re.sub(r"\d+(?:[.,:]\d+)*", " N ", low)
    return " ".join(re.findall(r"[a-z_]{2,}|N|URL|PATH|ID", low)[:14])


def tool_failures(steps):
    """What an agent's tools reported as failed, from the steps of its reply: one entry per tool and kind of
    error, with how often it came. Calls the app refused as repeats are not failures of the tool."""
    found = {}
    for s in steps or []:
        if s.get("type") != "tool" or s.get("ok") is not False or s.get("blocked"):
            continue
        error = (str(s.get("result") or "").strip().splitlines() or [""])[0]
        error = re.sub(r"^Error:\s*", "", error)[:220]
        key = (s.get("name"), signature(error))
        if key in found:
            found[key]["times"] += 1
            continue
        args = s.get("arguments") or {}
        what = next((str(args[k]) for k in ("url", "query", "path", "command", "tool", "name", "prompt") if args.get(k)), "")
        found[key] = {"tool": s.get("name"), "what": " ".join(what.split())[:100], "error": error, "sig": key[1],
                      "times": 1, "declined": bool(s.get("declined"))}
    return list(found.values())[:8]


def progressing(steps, guard=None):
    """Was the agent still getting somewhere when it ended? (Then more time helps; otherwise it does not.)"""
    if guard and LOOP.search(guard):
        return False
    tools = [s for s in steps or [] if s.get("type") == "tool" and not s.get("blocked")]
    if len(tools) < 2:
        return False
    recent = tools[-4:]
    return sum(bool(s.get("ok")) for s in recent) * 2 >= len(recent)


def classify(error=None, *, timed_out=False, guard=None, failures=(), objection=None, repeated=False,
             stopped=False):
    """The class of a step's failure (a key of CLASSES), from everything that is known about how it ended:
    its error, whether the app stopped it for time, why its agent stopped using tools, what its tools reported
    and what the check objected to."""
    if stopped and not timed_out:
        return "stopped"
    text = " ".join(str(x) for x in (error, guard) if x)
    if timed_out or TIMEOUT.search(text):
        return "timeout"
    if STALL.search(text):
        return "stall"
    if MEMORY.search(text):
        return "memory"
    if CONTEXT.search(text):
        return "context"
    tools = list(failures or [])
    if any(f.get("declined") for f in tools) or (error and PERMISSION.search(error)):
        return "permission"
    tool_text = " ".join(f.get("error") or "" for f in tools)
    if MISSING.search(text) or (tools and MISSING.search(tool_text)):
        return "missing"
    if error and TRANSIENT.search(error):
        return "transient"
    if guard and LOOP.search(guard):
        return "approach"
    if tools and all(TRANSIENT.search(f.get("error") or "") for f in tools) and not objection:
        return "transient"
    if error or tools:
        return "approach"
    if objection and NOT_SAVED.search(str(objection)):
        return "output"
    if objection:
        return "capability" if repeated else "quality"
    return None


def record(t, why, kind, minutes=None, round_=None):
    """One failed attempt at a step, as it is kept with the run."""
    tools = [{k: f.get(k) for k in ("tool", "what", "error", "times")} for f in (t.get("failures") or [])[:4]]
    return {"step": t.get("id"), "name": t.get("name"), "goal": (t.get("goal") or "")[:400], "agent": t.get("agent"),
            "harness": t.get("harness") or "agent", "model": t.get("model"), "minutes": minutes,
            "round": round_ if round_ is not None else t.get("attempts"), "class": kind or "approach",
            "error": " ".join(str(why or "").split())[:300], "sig": signature(why), "tools": tools,
            "progress": bool(t.get("progressing")), "change": "", "at": now()}


def describe(rec, model_name=None):
    """One record as a line a planner (or a person) can read."""
    label = CLASSES.get(rec.get("class"), {}).get("label") or rec.get("class") or "failed"
    who = ", ".join(str(x) for x in (rec.get("agent") or rec.get("harness"), model_name or rec.get("model"),
                                     f"{rec['minutes']:g} min" if rec.get("minutes") else None) if x)
    tools = "; ".join(f"{f['tool']}" + (f" ×{f['times']}" if (f.get("times") or 1) > 1 else "") + f": {f['error'][:90]}"
                      for f in rec.get("tools") or [])
    return (f"- {rec.get('step')} “{rec.get('name')}” [{who}] — {label}: {rec.get('error') or 'no result'}"
            + (f" (tools: {tools})" if tools else "")
            + (f" → then: {rec['change']}" if rec.get("change") else "")).replace("\n", " ")


def _words(text):
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))


def same_goal(a, b, level=0.7):
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= level


def refusal(candidate, records):
    """Why a proposed step may not run: it equals an attempt that already failed. None when it may run.
    `candidate`: {goal, harness, model, minutes}. A failure whose class allows the same attempt again (a passing
    fault, a model that stalled) is allowed that many times; a timeout is allowed again with more time."""
    same = [r for r in records if same_goal(r.get("goal"), candidate.get("goal"))
            and (r.get("harness") or "agent") == (candidate.get("harness") or "agent")
            and (r.get("model") or None) == (candidate.get("model") or None)]
    if not same:
        return None
    last = same[-1]
    kind = last.get("class") or "approach"
    allowed = CLASSES.get(kind, {}).get("repeat", 0)
    repeats = sum(r.get("class") == kind and r.get("sig") == last.get("sig") for r in same)
    if allowed and repeats <= allowed:
        return None
    if kind == "timeout" and last.get("progress") and (candidate.get("minutes") or 0) > (last.get("minutes") or 0):
        return None
    label = CLASSES.get(kind, {}).get("label") or kind
    return (f"“{last.get('name')}” already failed this way ({label}: {last.get('error') or 'no result'}), and this "
            "step is the same again: the same kind of agent, the same model, the same goal")


def ledger_text(records, last=6, names=None):
    rows = records[-last:]
    more = len(records) - len(rows)
    return ((f"({more} earlier failures not shown)\n" if more > 0 else "")
            + "\n".join(describe(r, (names or {}).get(r.get("model"))) for r in rows))


AGENT_RULES = (
    "When something fails, do not do it again the same way: the same action gives the same failure. Read the "
    "error, change what caused it (the input, the tool, the source, the size of the piece) and try once more. "
    "If it fails twice, stop trying that and say so in your handoff, so that nobody repeats it.")


# ---------------------------------------------------------------------- fail fast (2.20.0)
FAIL_FAST_RULE = (
    "The moment you know you cannot finish your task (something you need is not there: information, a tool, a "
    "permission, a stronger model or more time; or the fix you tried failed the same way again), call "
    "report_failure at once with what you tried, the exact error and what you need. Do not keep trying: the "
    "planner decides what happens next within seconds. The app stops you too when a tool fails twice with the "
    "same error, or when something only the user can give is missing.")
# what an agent can say it needs (report_failure), as the planner and the user read it
NEEDS = {"information": "information it does not have", "tool": "a tool it does not have",
         "permission": "something only you can give (a permission, a sign-in, a key)",
         "stronger_model": "a stronger model", "more_time": "more time", "other": "another way"}
CODE_TOOLS = ("run_python", "run_command")
CODE_FAILED = re.compile(r"^FAILED \(exit code -?\d+\)")
ERROR_LINE = re.compile(r"^\s*[\w.]*(?:Error|Exception|Exit|Interrupt|Warning)\b\s*:?.*$", re.M)
REFUSED_PATH = re.compile(r"is outside the folders this chat may use", re.I)
NO_TOOL = re.compile(r"There is no tool called '([^']+)'")
# words a 'done when' may use and still ask for nothing but files: code can check that, no model is needed
FILE_WORDS = set("is are be been has have was were saved save exists exist written created left made there in at to "
                 "into the a an team folder folders file files shared and with it its of as on both all each "
                 "present non empty not".split())


def code_error(result):
    """The error of code that failed (run_python / run_command answer 'FAILED (exit code N)'), as one line: the
    last 'SomethingError: …' line of its output, else its first line of output. None when the code did not fail."""
    text = str(result or "")
    if not CODE_FAILED.match(text):
        return None
    body = text.split("\n\nThe error is at line")[0].split("\nHint:")[0].split("\nFiles created/changed:")[0]
    errors = [m.group(0).strip() for m in ERROR_LINE.finditer(body.split("\n", 1)[-1])]
    if errors:
        return errors[-1][:200]
    rest = [x.strip() for x in body.splitlines()[1:] if x.strip()]
    return (rest[-1] if rest else body.splitlines()[0])[:200]


def tripwire(name, error, *, declined=False, world=False, connector=False, code=False, times=1, refused=0,
             wanted=None):
    """Should the app stop an agent of a run now, without another turn of its model? (class, reason) or None.

    `error`: the first line of what the tool said; `times`: how often this tool has now failed with this same
    error in this reply; `refused`: paths refused so far; `world`: a web or browser tool (a page that fails can
    have another source, so only the same error twice stops it); `code`: code that failed (one fix is allowed);
    `wanted`: a tool the agent called that exists but is not in its kit."""
    error = re.sub(r"^Error:\s*", "", " ".join(str(error or "").split()))[:200]
    if declined:
        return "permission", f"you declined {name}"
    if wanted:
        return "missing", f"it needs {wanted}, which is not among its tools"
    if REFUSED_PATH.search(error):
        return ("approach", f"two paths it chose were outside its folders ({error[:120]})") if refused >= 2 else None
    if not world and not code:
        if PERMISSION.search(error):
            return "permission", f"{name} needs you: {error}"
        if MISSING.search(error) and not NO_TOOL.search(error):
            return "missing", f"{name}: {error}"
        if connector and (TRANSIENT.search(error) or STALL.search(error)):
            return "transient", f"the service could not be reached, also when tried again ({error[:140]})"
    if times >= 2:
        what = "the code" if code else name
        return "approach", f"{what} failed twice with the same error ({error[:140]})"
    return None


def declared_class(need, error):
    """The class of a failure an agent reported itself: what its error says when that is specific, else what it
    says it needs."""
    said = classify(error) if error else None
    if said in ("permission", "missing", "transient", "memory", "context", "stall"):
        return said
    return {"permission": "permission", "tool": "missing", "information": "missing", "stronger_model": "capability",
            "more_time": "timeout"}.get(need, "approach")


def fast_rule(entry):
    """What the planner does about an entry of the register when the answer is clear without asking its model:
    'retry' (a passing fault), 'ask' (only the user can give it), 'handover' (an agent with the tool), 'stronger'
    (a stronger model), 'more_time', 'output' (files not left: save_to, kimilm.call). None: not clear, the planner's
    model decides with the entry in hand."""
    kind, need = entry.get("class"), entry.get("need")
    if need == "information":
        return None                   # the planner (or another step) may know it
    if kind == "transient":
        return "retry"
    if kind == "permission" or need == "permission":
        return "ask"
    if kind == "missing" or need == "tool":
        return "handover"             # the caller asks the user when no agent has what is missing
    if need == "stronger_model" or kind == "capability":
        return "stronger"
    if need == "more_time" or kind == "timeout":
        return "more_time"
    if kind == "output":
        return "output"
    return None


def files_only(done_when, names):
    """True when 'done when' asks for nothing but these files being there: code checks that at once, and the
    planner's model is not asked (2.20.0)."""
    text = (done_when or "").lower()
    if not names or not text.strip():
        return False
    forms = sorted({f for n in names for f in (str(n).lower().replace("\\", "/"),
                                               str(n).lower().replace("\\", "/").rsplit("/", 1)[-1]) if f},
                   key=len, reverse=True)
    found = False
    for form in forms:
        if form in text.replace("\\", "/"):
            text = text.replace("\\", "/").replace(form, " ")
            found = True
    words = re.findall(r"[a-z]+", text)
    return found and all(w in FILE_WORDS for w in words)


def public(entry):
    """An entry of the register as it is saved and shown (without what only this process can read)."""
    return {k: v for k, v in (entry or {}).items() if k != "mono"}
