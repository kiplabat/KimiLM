"""What each model is good at, measured on this computer (KimiLM 2.32.0).

Why. A planner that gives every step to the model it runs on wastes what else is installed: a small model may
browse well and plan badly; a larger one may write code reliably but slowly; an online model may be the only one
that combines sources well. "Check this setup" (dva/evals.py) now tags each of its tasks with the skills it tests
(planning, research, browsing, coding, data, tools, extraction, writing, reasoning, long documents). This module
turns the results into a profile per model and gives the planner what it needs to choose, and say why:
  - profiles(): for every model ever tested, per skill: passed / tried, and the time taken
  - candidates(): the models a run may use now (local ones installed, online ones the chat may use), with their
    memory, speed, whether they are loaded and what an online one costs
  - brief(): the lines the planner reads ("research 2/2, browsing 0/2 …") and the rules for choosing
  - best_for(): the model a step of a kind should run on when the planner did not say (by results only)
  - planners(): models that planned better than the current one (to hand the planning to)
Only results count: a model that was never tested for a skill is never chosen for it on a guess (online models
that were never tested are offered to the planner by their strength, marked as not tested)."""
from .providers import is_cloud

SKILLS = [
    ("planning", "planning", "splitting a request into steps, in order, with the parts that can run at the same time"),
    ("research", "research", "finding things in several sources and combining them, with sources"),
    ("browsing", "browsing", "moving through web pages: following links, reading what a page shows"),
    ("coding", "coding", "writing code, running it and fixing it until it works"),
    ("data", "data", "computing with tables and numbers (Python)"),
    ("tools", "file work", "doing exactly what was asked with files and tools: create, move, change one thing"),
    ("extraction", "extraction", "pulling fields out of documents into JSON, CSV or tables"),
    ("writing", "writing", "summarising and writing for readers within limits"),
    ("reasoning", "reasoning", "careful step-by-step reasoning (logic, comparisons)"),
    ("long", "long documents", "finding and combining details far apart in a long document"),
]
SKILL_IDS = [s[0] for s in SKILLS]
LABEL = {s[0]: s[1] for s in SKILLS}


def task_skills():
    from .evals import TASKS
    return {t["id"]: list(t.get("skills") or []) for t in TASKS}


def profiles(app, runs=80):
    """{model id: {"skills": {skill: {"passed", "ran", "score", "seconds"}}, "passed", "ran", "tested", "version"}}.
    The newest result of each task on each model counts (a model gets better with settings and versions)."""
    tags = task_skills()
    latest = {}                      # (model, task) -> result
    when = {}
    for row in app.db.all("SELECT * FROM eval_runs ORDER BY created DESC LIMIT ?", (runs,)):
        model = row.get("model")
        if not model:
            continue
        for r in row.get("results") or []:
            key = (model, r.get("id"))
            if key in latest or not r.get("id"):
                continue
            if "took too long" not in str(r.get("error") or "") and r.get("error") and not r.get("read"):
                continue             # it never got to work (the app closed, the model failed to load): no verdict
            latest[key] = r
            if model not in when:
                when[model] = (row.get("created"), row.get("version"))
    out = {}
    for (model, task), r in latest.items():
        p = out.setdefault(model, {"skills": {}, "passed": 0, "ran": 0, "tested": when[model][0],
                                   "version": when[model][1]})
        p["ran"] += 1
        p["passed"] += 1 if r.get("passed") else 0
        for sk in tags.get(task) or r.get("skills") or []:
            s = p["skills"].setdefault(sk, {"passed": 0, "ran": 0, "seconds": 0.0})
            s["ran"] += 1
            s["passed"] += 1 if r.get("passed") else 0
            s["seconds"] += float(r.get("seconds") or 0)
    for p in out.values():
        for s in p["skills"].values():
            s["score"] = round(s["passed"] / s["ran"], 2) if s["ran"] else 0.0
            s["seconds"] = round(s["seconds"] / s["ran"], 1) if s["ran"] else 0.0
    return out


def candidates(app, chat=None, project=None):
    """The models a run may give steps to now, each with what the planner should know about it."""
    loaded = {s["model_id"] for s in app.runtime.loaded()} if hasattr(app.runtime, "loaded") else set()
    out = []
    for c in app.router.local():
        row = app.db.get("models", c["id"]) or {}
        bench = row.get("bench") or {}
        out.append({**c, "size_gb": round((row.get("size") or 0) / 2 ** 30, 1), "loaded": c["id"] in loaded,
                    "speed": round(float(bench.get("predicted_per_second") or 0))})
    online, _why = app.router.online(chat, project)
    for c in online:
        out.append({**c, "size_gb": 0, "loaded": True, "speed": 0})
    return out


def _rec(profs, mid, skill):
    s = (profs.get(mid) or {}).get("skills", {}).get(skill)
    return s if s and s.get("ran") else None


def _cell(s):
    return f"{s['passed']}/{s['ran']}" if s else "not tested"


def brief(cands, profs, default_id=None):
    """The planner's view of the models it can use, one line each."""
    lines = []
    for c in cands:
        where = "online, paid per use" if c["kind"] == "cloud" else "on this computer"
        bits = [where]
        if c["kind"] != "cloud":
            if c.get("size_gb"):
                bits.append(f"{c['size_gb']} GB")
            if c.get("speed"):
                bits.append(f"writes about {c['speed']} tokens/s")
            bits.append("loaded now" if c.get("loaded") else "not loaded (loading takes a while)")
        if c.get("vision"):
            bits.append("can see images")
        if c["id"] == default_id:
            bits.append("the default model")
        p = profs.get(c["id"])
        if p:
            skills = ", ".join(f"{LABEL[k]} {_cell(_rec(profs, c['id'], k))}" for k in SKILL_IDS
                               if _rec(profs, c["id"], k))
            measured = f"passed {p['passed']} of {p['ran']} tasks: {skills}"
        else:
            measured = f"not tested here; strength {c.get('strength', 3):g} of 5"
        lines.append(f"  {c['id']} = {c['name']} ({'; '.join(bits)}): {measured}")
    return "\n".join(lines)


def best_for(skill, cands, profs, current, margin=0.25):
    """(model id, reason) for a step of this kind that the planner gave no model, or (None, ""). Chosen only on
    results: another model must have passed clearly more of these tasks than the current one (or all of them,
    when the current one was never tested for it). Local models first among equals; online ones only if better."""
    if skill not in LABEL:
        return None, ""
    mine = _rec(profs, current, skill)
    rows = [(c, _rec(profs, c["id"], skill)) for c in cands if c["id"] != current]
    rows = [(c, s) for c, s in rows if s]
    if not rows:
        return None, ""
    c, s = max(rows, key=lambda x: (x[1]["score"], x[1]["ran"], x[0]["kind"] != "cloud", -x[1]["seconds"]))
    if mine is None:
        if s["score"] < 1.0:
            return None, ""
    elif s["score"] < mine["score"] + margin:
        return None, ""
    names = {x["id"]: x["name"] for x in cands}
    why = f"{c['name']} passed {_cell(s)} {LABEL[skill]} tasks in Check this setup"
    if mine is not None:
        why += f"; {names.get(current, current)} passed {_cell(mine)}"
    return c["id"], why


def planners(cands, profs, current, margin=0.25):
    """Models that plan better than the current one, best first: measured (planning passed clearly more), or, for
    an online model never tested, a strategist stronger than the current model when the current one plans badly
    or was never tested for planning. [(candidate, reason)]"""
    mine = _rec(profs, current, "planning")
    cur = next((c for c in cands if c["id"] == current), None)
    out = []
    for c in cands:
        if c["id"] == current:
            continue
        s = _rec(profs, c["id"], "planning")
        if s:
            if (mine is None and s["score"] >= 1.0) or (mine is not None and s["score"] >= mine["score"] + margin):
                out.append((c, s["score"] + 1, f"it passed {_cell(s)} planning tasks here"
                            + (f", the current planner {_cell(mine)}" if mine else "")))
        elif c["kind"] == "cloud" and c.get("role") in ("strategist", "both") and \
                (mine is None or mine["score"] < 0.75) and c.get("strength", 0) > (cur or {}).get("strength", 0) + 0.5:
            out.append((c, c["strength"] / 10, f"an online strategist (strength {c['strength']:g} of 5), not tested "
                                               "here"))
    out.sort(key=lambda x: -x[1])
    return [(c, why) for c, _, why in out]
