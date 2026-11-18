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
small model remembering to write it."""
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
    r"did not start within", re.I)
STALL = re.compile(r"did not start answering|stopped writing|did not respond|not reachable|stopped responding", re.I)
TIMEOUT = re.compile(r"time limit of [\d.]+ minutes|took more than|ran past its time|longer than the limit|"
                     r"ran far past|used all \d+ steps|used its \d+ tool calls", re.I)
MEMORY = re.compile(r"not enough memory|out of memory|failed to allocate|bad_alloc|ran out of memory", re.I)
CONTEXT = re.compile(r"does not fit the model's context|context.{0,40}exceed|exceed.{0,40}context", re.I)
PERMISSION = re.compile(r"declined|needs you to sign in|needs its key|needs your approval|not allowed to|"
                        r"was not approved|sign in first|unauthori[sz]ed|\b401\b", re.I)
MISSING = re.compile(r"is not installed|not set up|no (image|video|sound|speech|music|vision)? ?model|there is no "
                     r"tool called|no model is installed|is unavailable|needs .{0,40} first|install it", re.I)
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
