"""Forecasting what a step will need before it starts (2.5.0): tokens, context, memory and time.

An agent step's prompt has three parts:
  fixed    the system prompt and the tool list of its harness (the same for every step with that harness)
  brief    what this step is told: its goal and the results it builds on (counted with the model's own tokenizer
           when the model is loaded, estimated from its length otherwise)
  growth   what the step adds while it works: its tool calls, their results and its own text
The fixed part, the growth, the number of model calls, the output and the speeds are learned from every step that
runs (table step_stats, one row per harness and model, a moving average), so the forecast gets closer with use.
Before the first run of a harness on a model, cautious defaults are used.

The forecast decides the context a model is loaded with for a run (Runtime.fit): what the largest step needs plus
a margin, instead of the largest context for every reply slot. It is also shown on the board ("≈ 9k tokens,
≈ 4 min"), and the scheduler compares it with free memory before a step starts."""
from .db import now
from .llm import estimate_tokens
from .providers import is_cloud

MARGIN = 1.3
DEFAULTS = {            # harness mode -> (fixed tokens, model calls, growth per call, output per call)
    "chat": (350, 1, 0, 500),
    "agent": (3000, 5, 900, 300),
    "planner": (3000, 7, 900, 300),
    "research": (500, 8, 250, 350),
}


class Forecaster:
    def __init__(self, app):
        self.app, self.db = app, app.db

    @staticmethod
    def key(harness_id, model_id):
        return f"{harness_id}|{model_id}"

    def row(self, harness_id, model_id):
        return self.db.get("step_stats", self.key(harness_id, model_id), key="key")

    async def count(self, text, model_id=None):
        """Tokens in a text: exact when the model is loaded (its tokenizer is asked), else estimated."""
        server = self.app.runtime.servers.get(model_id) if model_id and not is_cloud(model_id) else None
        if server is not None and server.alive:
            n = await self.app.llm.count_tokens(text, server=server)
            if n is not None:
                return n, True
        return estimate_tokens(text), False

    def speeds(self, model_id, row=None):
        """(reading, writing) tokens per second for a model: measured on its server, else learned, else its
        benchmark, else a slow-laptop guess."""
        if is_cloud(model_id):
            return 2000.0, 60.0
        server = self.app.runtime.servers.get(model_id)
        reads = float(getattr(server, "read_tps", 0) or 0) or float((row or {}).get("read_tps") or 0)
        writes = float(getattr(server, "write_tps", 0) or 0) or float((row or {}).get("write_tps") or 0)
        if not reads or not writes:
            bench = (self.db.get("models", model_id) or {}).get("bench") or {}
            reads = reads or float(bench.get("prompt_per_second") or 0)
            writes = writes or float(bench.get("predicted_per_second") or 0)
        return reads or 20.0, writes or 5.0

    def step(self, harness, model_id, brief_tokens, max_reply=2048, exact=False, cached_fixed=False):
        """Forecast for one step. Returns prompt, output, calls, peak, ctx (context to load), seconds, learned."""
        mode = (harness or {}).get("mode", "agent")
        fixed, calls, per_call, out = DEFAULTS.get(mode, DEFAULTS["agent"])
        if mode != "chat" and (harness or {}).get("tools") == []:
            fixed = 350
        cap = int((harness or {}).get("max_steps") or 10)
        row = self.row((harness or {}).get("id") or mode, model_id)
        learned = bool(row and row["runs"] >= 1)
        if learned:
            fixed = max(200, int(row["fixed"]))
            calls = max(1.0, float(row["calls"]))
            growth = max(0, int(row["growth"]))
            out_total = max(60, int(row["output"]))
        else:
            calls = min(calls, cap)
            growth = int(calls * per_call)
            out_total = int(calls * out)
        peak = int(fixed + brief_tokens + growth)
        ctx = int((peak + min(max_reply, max(512, out_total))) * MARGIN)
        reads, writes = self.speeds(model_id, row)
        read_tokens = brief_tokens + growth + (0 if cached_fixed else fixed)
        seconds = read_tokens / reads + out_total / writes
        return {"prompt": int(fixed + brief_tokens), "output": int(out_total), "calls": round(calls, 1),
                "peak": peak, "ctx": ctx, "seconds": int(seconds), "learned": learned, "exact": bool(exact),
                "tokens": int(peak + out_total)}

    def record(self, harness_id, model_id, stats, brief_tokens, ok):
        """Learn from a finished step (stats = the turn's stats)."""
        calls = int(stats.get("model_calls") or 0)
        first = int(stats.get("first_prompt") or 0)
        peak = int(stats.get("peak_prompt") or 0)
        if not calls or not first:
            return
        obs = {"calls": float(calls), "fixed": float(max(100, first - brief_tokens)),
               "growth": float(max(0, peak - first)), "output": float(stats.get("output_tokens") or 0),
               "seconds": float(stats.get("seconds") or 0), "read_tps": float(stats.get("read_tps") or 0),
               "write_tps": float(stats.get("tokens_per_second") or 0)}
        key = self.key(harness_id, model_id)
        row = self.db.get("step_stats", key, key="key")
        if not row:
            self.db.insert("step_stats", {"key": key, "harness": harness_id, "model_id": model_id, "runs": 1,
                                          "ok": int(bool(ok)), **obs, "updated": now()})
            return
        w = 0.35                                   # weight of the newest run
        new = {}
        for k, v in obs.items():
            old = float(row.get(k) or 0)
            if k in ("read_tps", "write_tps") and not v:
                new[k] = old
            elif k == "growth":                    # sized for the larger runs, so the context is rarely too small
                new[k] = max(v, old * (1 - w) + v * w) if v > old else old * (1 - w * 0.5) + v * w * 0.5
            else:
                new[k] = old * (1 - w) + v * w if old else v
        self.db.update("step_stats", key, {**new, "runs": int(row["runs"]) + 1, "ok": int(row["ok"]) + int(bool(ok)),
                                           "updated": now()}, key="key")


    def judge(self, harness_id, model_id, passed, rounds=1):
        """Record the verdict on a step: accepted complete, or not (failed, or kept as an incomplete best
        attempt, or moved to a stronger model). This is what model choice learns from."""
        key = self.key(harness_id, model_id)
        row = self.db.get("step_stats", key, key="key")
        if not row:
            self.db.insert("step_stats", {"key": key, "harness": harness_id, "model_id": model_id, "runs": 0, "ok": 0,
                                          "judged": 1, "passed": int(bool(passed)), "rounds": float(rounds),
                                          "updated": now()})
            return
        n = int(row.get("judged") or 0)
        self.db.update("step_stats", key, {
            "judged": n + 1, "passed": int(row.get("passed") or 0) + int(bool(passed)),
            "rounds": (float(row.get("rounds") or 0) * n + float(rounds)) / (n + 1), "updated": now()}, key="key")

    def record_of(self, harness_id, model_id):
        """How a harness has fared on a model: steps judged, share accepted complete, average seconds."""
        row = self.row(harness_id, model_id) or {}
        judged = int(row.get("judged") or 0)
        return {"judged": judged, "passed": int(row.get("passed") or 0),
                "rate": (int(row.get("passed") or 0) / judged) if judged else None,
                "seconds": float(row.get("seconds") or 0), "rounds": float(row.get("rounds") or 0)}

    def choose(self, harness_id, current, candidates, minimum=3):
        """Pick the model for a step from what was measured on this computer. Returns (model id, reason) or
        (current, None). Only models with at least `minimum` judged steps of this harness are compared; a model
        that has never run this kind of step is never picked blind.
          - the current model fails often (under half) and another passes clearly more: use the other
          - the current model passes reliably (80%+) and another does too in under 70% of the time: use the faster"""
        mine = self.record_of(harness_id, current)
        rows = []
        for mid in candidates:
            if mid == current:
                continue
            rec = self.record_of(harness_id, mid)
            if rec["judged"] >= minimum:
                rows.append((mid, rec))
        if not rows or mine["judged"] < minimum:
            return current, None
        if mine["rate"] < 0.5:
            mid, rec = max(rows, key=lambda r: (r[1]["rate"], -r[1]["seconds"]))
            if rec["rate"] >= mine["rate"] + 0.25:
                return mid, (f"it passed {rec['passed']} of {rec['judged']} {harness_id} steps here; the usual model "
                             f"passed {mine['passed']} of {mine['judged']}")
            return current, None
        if mine["rate"] >= 0.8 and mine["seconds"] > 0:
            quick = [(mid, rec) for mid, rec in rows if rec["rate"] >= 0.8 and 0 < rec["seconds"] < mine["seconds"] * 0.7]
            if quick:
                mid, rec = min(quick, key=lambda r: r[1]["seconds"])
                return mid, (f"it passes as reliably ({rec['passed']} of {rec['judged']}) in about "
                             f"{pretty_seconds(rec['seconds'])} per step instead of {pretty_seconds(mine['seconds'])}")
        return current, None


def pretty_tokens(n):
    n = int(n or 0)
    return f"{n / 1000:.0f}k" if n >= 10000 else (f"{n / 1000:.1f}k" if n >= 1000 else str(n))


def pretty_seconds(s):
    s = int(s or 0)
    if s < 90:
        return f"{max(1, s)} s"
    if s < 5400:
        return f"{round(s / 60)} min"
    return f"{s / 3600:.1f} h"
