"""Model usage statistics (2.27.0): every call to a model, on this computer or online, is one row of
`model_calls` (tokens read, read again from the cache, written; how long reading and writing took; who asked and
why). `Meter.summary` turns the rows into what the Usage window shows: totals against the period before, each
model, each day, the hours of the week, chats, harnesses, purposes, evaluations, media and a few records.

Calls are recorded where they end: `LLM._events` for local models (llama-server's timings) and `Adapter.meter`
for online ones. What asked for the call comes from the `USAGE` context (set by a reply, memory, the planner…).
Replies made before 2.27.0 are added once from their messages (one row per reply, `calls` = its model calls)."""
import contextlib
import contextvars
import math
import time
from datetime import date, datetime, timedelta, timezone

PURPOSE = contextvars.ContextVar("dva_usage_purpose", default=None)   # overrides USAGE's purpose (planner, check…)

# How many words a token is, roughly, for English prose; a page is about 500 words. Shown as "about".
WORDS_PER_TOKEN = 0.75
WORDS_PER_PAGE = 500


@contextlib.contextmanager
def purpose(name):
    """Calls made inside are counted as `name` (e.g. the planner's calls inside a team's reply)."""
    token = PURPOSE.set(name)
    try:
        yield
    finally:
        PURPOSE.reset(token)


def _local(ts):
    return datetime.fromtimestamp(ts).astimezone()


def _pct(values, q):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] if lo == hi else values[lo] + (values[hi] - values[lo]) * (k - lo)


class Meter:
    def __init__(self, db, usage_var=None, log=None):
        self.db = db
        self.usage_var = usage_var
        self.log = log or (lambda m: None)
        self._backfilled = False

    # ------------------------------------------------------------------ recording
    def context(self):
        ctx = dict((self.usage_var.get() if self.usage_var else None) or {})
        p = PURPOSE.get()
        if p:
            ctx["purpose"] = p
        return ctx

    def record(self, model_id, *, online=False, read=0, cached=0, written=0, read_ms=0.0, write_ms=0.0,
               total_ms=0.0, cost=0.0, status="ok", error=None, ctx=None, ts=None, calls=1):
        """One finished (or failed) model call. Never raises: statistics must not break a reply."""
        try:
            ctx = ctx if ctx is not None else self.context()
            ts = ts or time.time()
            when = _local(ts)
            self.db.run(
                "INSERT INTO model_calls (ts, day, hour, weekday, model_id, online, chat_id, job_id, purpose, harness, "
                "read_tokens, cached_tokens, written_tokens, read_ms, write_ms, total_ms, cost, status, error, calls) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, when.strftime("%Y-%m-%d"), when.hour, when.weekday(), model_id or "", 1 if online else 0,
                 ctx.get("chat_id"), ctx.get("job_id"), ctx.get("purpose") or "other", ctx.get("harness"),
                 int(read or 0), int(cached or 0), int(written or 0), float(read_ms or 0), float(write_ms or 0),
                 float(total_ms or 0), float(cost or 0), status, (str(error)[:300] if error else None), int(calls or 1)))
        except Exception as exc:                       # pragma: no cover - a full disk, a closed database
            self.log(f"usage statistics: {type(exc).__name__}: {exc}")

    def record_local(self, server, timings, seconds, status="ok", error=None, purpose=None):
        t = timings or {}
        ctx = self.context()
        if purpose:
            ctx["purpose"] = purpose
        read_ms = float(t.get("prompt_ms") or 0)
        write_ms = float(t.get("predicted_ms") or 0)
        self.record(getattr(server, "model_id", ""), online=False, read=t.get("prompt_n"), cached=t.get("cache_n"),
                    written=t.get("predicted_n"), read_ms=read_ms, write_ms=write_ms,
                    total_ms=max(seconds * 1000.0, read_ms + write_ms), status=status, error=error, ctx=ctx)

    # ------------------------------------------------------------------ history from before 2.27.0
    def backfill(self):
        """Add the replies made before calls were recorded, once (from each reply's own statistics)."""
        if self._backfilled or self.db.setting("usage_backfilled"):
            self._backfilled = True
            return 0
        first = self.db.one("SELECT MIN(ts) AS t FROM model_calls WHERE status != 'history'") or {}
        before = first.get("t") or time.time() + 1
        rows = self.db.all("SELECT m.chat_id, m.meta_json, m.created, c.origin, c.parent_chat_id FROM messages m "
                           "LEFT JOIN chats c ON c.id = m.chat_id WHERE m.role='assistant'")
        n = 0
        for r in rows:
            meta = r.get("meta") or {}
            st = meta.get("stats") or {}
            if not st.get("model_calls") or not r.get("created"):
                continue
            try:
                ts = datetime.fromisoformat(r["created"]).timestamp()
            except ValueError:
                continue
            if ts >= before:
                continue
            origin = r.get("origin") or "user"
            why = ("eval" if origin == "eval" else "agent" if r.get("parent_chat_id") else
                   "schedule" if origin == "schedule" else "chat")
            self.record(meta.get("model") or "", online=bool(meta.get("online")), read=st.get("prompt_tokens"),
                        cached=st.get("cached_tokens"), written=st.get("output_tokens"),
                        total_ms=float(st.get("seconds") or 0) * 1000, cost=float(st.get("cost") or 0),
                        status="history", ts=ts, calls=int(st.get("model_calls") or 1),
                        ctx={"chat_id": r["chat_id"], "job_id": meta.get("job_id"), "purpose": why,
                             "harness": meta.get("harness")})
            n += 1
        self.db.set_setting("usage_backfilled", True)
        self._backfilled = True
        return n

    # ------------------------------------------------------------------ summaries
    def names(self):
        out = {}
        for r in self.db.all("SELECT id, name FROM models"):
            out[r["id"]] = r["name"] or r["id"]
        for r in self.db.all("SELECT id, name, remote_id FROM cloud_models"):
            out[r["id"]] = r["name"] or r["remote_id"] or r["id"]
        return out

    @staticmethod
    def _totals(rows):
        t = {"calls": 0, "read": 0, "cached": 0, "written": 0, "seconds": 0.0, "failed": 0, "cost": 0.0,
             "read_ms": 0.0, "write_ms": 0.0, "timed_read": 0, "timed_written": 0}
        chats, models = set(), set()
        for r in rows:
            t["calls"] += r["calls"] or 1
            t["read"] += r["read_tokens"] or 0
            t["cached"] += r["cached_tokens"] or 0
            t["written"] += r["written_tokens"] or 0
            t["seconds"] += (r["total_ms"] or 0) / 1000
            t["cost"] += r["cost"] or 0
            if r["status"] not in ("ok", "history"):
                t["failed"] += r["calls"] or 1
            if (r["read_ms"] or 0) > 0:
                t["read_ms"] += r["read_ms"]
                t["timed_read"] += r["read_tokens"] or 0
            if (r["write_ms"] or 0) > 0:
                t["write_ms"] += r["write_ms"]
                t["timed_written"] += r["written_tokens"] or 0
            if r["chat_id"]:
                chats.add(r["chat_id"])
            if r["model_id"]:
                models.add(r["model_id"])
        prompt = t["read"] + t["cached"]
        out = {"calls": t["calls"], "read": t["read"], "cached": t["cached"], "written": t["written"],
               "tokens": prompt + t["written"], "seconds": round(t["seconds"], 1), "failed": t["failed"],
               "cost": round(t["cost"], 4), "chats": len(chats), "models": len(models),
               "cache_percent": round(100 * t["cached"] / prompt) if prompt else 0,
               "read_tps": round(t["timed_read"] / (t["read_ms"] / 1000), 1) if t["read_ms"] > 0 else None,
               "write_tps": round(t["timed_written"] / (t["write_ms"] / 1000), 1) if t["write_ms"] > 0 else None}
        return out

    def summary(self, days=30, now=None):
        self.backfill()
        days = max(1, min(int(days or 30), 3650))
        now = now or time.time()
        today = _local(now).date()
        start_day = today - timedelta(days=days - 1)
        start = datetime.combine(start_day, datetime.min.time()).astimezone().timestamp()
        prev_start = datetime.combine(start_day - timedelta(days=days), datetime.min.time()).astimezone().timestamp()
        rows = self.db.all("SELECT * FROM model_calls WHERE ts >= ? AND ts <= ? ORDER BY ts", (start, now + 1))
        prev = self.db.all("SELECT * FROM model_calls WHERE ts >= ? AND ts < ?", (prev_start, start))
        names = self.names()
        name = lambda mid: names.get(mid) or (mid.split(":", 2)[-1] if mid else "unknown")

        # each model
        groups = {}
        for r in rows:
            groups.setdefault(r["model_id"] or "", []).append(r)
        total = self._totals(rows)
        by_model = []
        for mid, rs in groups.items():
            t = self._totals(rs)
            t.update({"model_id": mid, "name": name(mid), "online": bool(rs[0]["online"]),
                      "share": round(100 * t["tokens"] / total["tokens"], 1) if total["tokens"] else 0,
                      "last": rs[-1]["ts"]})
            by_model.append(t)
        by_model.sort(key=lambda m: -m["tokens"])

        # each day, every day of the range (empty days are zero, so a chart's x axis is true time)
        daily = {}
        d = start_day
        while d <= today:
            daily[d.isoformat()] = {"day": d.isoformat(), "read": 0, "cached": 0, "written": 0, "tokens": 0,
                                    "calls": 0, "seconds": 0.0, "models": {}}
            d += timedelta(days=1)
        for r in rows:
            row = daily.get(r["day"])
            if row is None:
                continue
            tok = (r["read_tokens"] or 0) + (r["cached_tokens"] or 0) + (r["written_tokens"] or 0)
            row["read"] += r["read_tokens"] or 0
            row["cached"] += r["cached_tokens"] or 0
            row["written"] += r["written_tokens"] or 0
            row["tokens"] += tok
            row["calls"] += r["calls"] or 1
            row["seconds"] += (r["total_ms"] or 0) / 1000
            row["models"][r["model_id"] or ""] = row["models"].get(r["model_id"] or "", 0) + tok
        for row in daily.values():
            row["seconds"] = round(row["seconds"], 1)

        # the hours of the week (Monday first)
        heat = [[0] * 24 for _ in range(7)]
        heat_calls = [[0] * 24 for _ in range(7)]
        for r in rows:
            if r["weekday"] is None or r["hour"] is None:
                continue
            heat[r["weekday"]][r["hour"]] += (r["read_tokens"] or 0) + (r["cached_tokens"] or 0) + (r["written_tokens"] or 0)
            heat_calls[r["weekday"]][r["hour"]] += r["calls"] or 1

        def grouped(key):
            g = {}
            for r in rows:
                g.setdefault(r[key] or "other", []).append(r)
            out = []
            for k, rs in g.items():
                t = self._totals(rs)
                out.append({"key": k, "calls": t["calls"], "tokens": t["tokens"], "seconds": t["seconds"],
                            "written": t["written"], "failed": t["failed"]})
            return sorted(out, key=lambda x: -x["tokens"])

        # chats that used the most
        per_chat = {}
        for r in rows:
            if r["chat_id"]:
                per_chat.setdefault(r["chat_id"], []).append(r)
        top = sorted(per_chat.items(), key=lambda kv: -self._totals(kv[1])["tokens"])[:12]
        titles = {}
        if top:
            ids = [cid for cid, _ in top]
            for c in self.db.all(f"SELECT id, title, origin, parent_chat_id FROM chats WHERE id IN ({','.join('?' * len(ids))})", ids):
                titles[c["id"]] = c
        top_chats = []
        for cid, rs in top:
            t = self._totals(rs)
            c = titles.get(cid) or {}
            top_chats.append({"chat_id": cid, "title": c.get("title") or ("(deleted chat)" if not c else "New chat"),
                              "exists": bool(c), "origin": c.get("origin"), "agent": bool(c.get("parent_chat_id")),
                              "calls": t["calls"], "tokens": t["tokens"], "seconds": t["seconds"],
                              "models": sorted({name(r["model_id"]) for r in rs if r["model_id"]}), "last": rs[-1]["ts"]})

        # chat activity: messages written by you and replies, per day
        iso_start = datetime.fromtimestamp(start, timezone.utc).isoformat()     # stored times are UTC
        # created is stored in UTC; a day earlier covers any time zone, the day is then read locally
        msgs = self.db.all("SELECT role, created FROM messages WHERE created >= ? AND role IN ('user', 'assistant')",
                           (datetime.fromtimestamp(start - 86400, timezone.utc).isoformat(),))
        act = {k: {"day": k, "you": 0, "replies": 0} for k in daily}
        for m in msgs:
            try:
                day = datetime.fromisoformat(m["created"]).astimezone().strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                continue
            if day in act:
                act[day]["you" if m["role"] == "user" else "replies"] += 1
        new_chats = (self.db.one("SELECT COUNT(*) AS n FROM chats WHERE created >= ? AND COALESCE(origin,'') != 'eval' "
                                 "AND parent_chat_id IS NULL", (iso_start,)) or {}).get("n", 0)

        # days in a row with any use, ending today (or yesterday), and the longest run in the range
        used = sorted({r["day"] for r in self.db.all("SELECT DISTINCT day FROM model_calls WHERE ts >= ?",
                                                      (now - 400 * 86400,))})
        used_set = set(used)
        streak, d = 0, today
        if d.isoformat() not in used_set:
            d -= timedelta(days=1)
        while d.isoformat() in used_set:
            streak += 1
            d -= timedelta(days=1)
        longest, run, last = 0, 0, None
        for s in used:
            cur = date.fromisoformat(s)
            run = run + 1 if last and (cur - last).days == 1 else 1
            longest = max(longest, run)
            last = cur

        # evaluations ("Check this setup") in the range
        tests = []
        for e in self.db.all("SELECT id, created, model, version, status, summary_json FROM eval_runs WHERE created >= ? "
                             "ORDER BY created", (iso_start,)):
            s = e.get("summary") or {}
            tests.append({"id": e["id"], "created": e["created"], "model": name(e["model"]) if e["model"] else "default",
                          "version": e["version"], "status": e["status"], "passed": s.get("passed", 0),
                          "total": s.get("total", 0), "seconds": s.get("seconds", 0)})

        # pictures, sound and video made in the range
        media = {r["kind"]: r["n"] for r in self.db.all(
            "SELECT kind, COUNT(*) AS n FROM media WHERE created >= ? GROUP BY kind", (iso_start,))}

        # records and estimates
        live = [r for r in rows if r["status"] != "history"]
        firsts = [r["read_ms"] / 1000 for r in live if (r["read_ms"] or 0) > 0]
        totals_s = [r["total_ms"] / 1000 for r in live if (r["total_ms"] or 0) > 0]
        biggest = max(rows, key=lambda r: ((r["read_tokens"] or 0) + (r["cached_tokens"] or 0)) / (r["calls"] or 1),
                      default=None)
        longest_call = max(live, key=lambda r: r["total_ms"] or 0, default=None)
        busiest = max(daily.values(), key=lambda x: x["tokens"], default=None)
        speed = {m["model_id"]: m["read_tps"] for m in by_model if m.get("read_tps")}
        saved = sum((r["cached_tokens"] or 0) / speed[r["model_id"]] for r in rows if speed.get(r["model_id"]))
        fastest = max((m for m in by_model if m.get("write_tps") and not m["online"]), key=lambda m: m["write_tps"],
                      default=None)
        words = total["written"] * WORDS_PER_TOKEN
        hour_tot = [sum(heat[w][h] for w in range(7)) for h in range(24)]
        peak_hour = max(range(24), key=lambda h: hour_tot[h]) if any(hour_tot) else None

        def call_ref(r):
            if not r:
                return None
            c = self.db.get("chats", r["chat_id"]) if r["chat_id"] else None
            return {"model": name(r["model_id"]), "ts": r["ts"], "chat_id": r["chat_id"] if c else None,
                    "chat": (c or {}).get("title"), "purpose": r["purpose"]}

        highlights = {
            "biggest_prompt": {**(call_ref(biggest) or {}), "tokens": round(((biggest["read_tokens"] or 0) + (biggest["cached_tokens"] or 0)) / (biggest["calls"] or 1))} if biggest and total["tokens"] else None,
            "longest_call": {**(call_ref(longest_call) or {}), "seconds": round((longest_call["total_ms"] or 0) / 1000, 1)} if longest_call and (longest_call["total_ms"] or 0) > 0 else None,
            "busiest_day": {"day": busiest["day"], "tokens": busiest["tokens"], "calls": busiest["calls"]} if busiest and busiest["tokens"] else None,
            "fastest_model": {"name": fastest["name"], "write_tps": fastest["write_tps"]} if fastest else None,
            "cache_saved_seconds": round(saved),
            "words_written": round(words), "pages_written": round(words / WORDS_PER_PAGE, 1),
            "peak_hour": peak_hour,
            "streak_days": streak, "longest_streak": longest,
        }
        latency = {"first_p50": _pct(firsts, .5), "first_p90": _pct(firsts, .9),
                   "total_p50": _pct(totals_s, .5), "total_p90": _pct(totals_s, .9), "calls": len(live)}
        latency = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in latency.items()}

        # how each model's writing speed moved, per day (only days with timed calls)
        speed_days = {}
        for r in live:
            if (r["write_ms"] or 0) > 0 and (r["written_tokens"] or 0) >= 8:
                s = speed_days.setdefault((r["model_id"], r["day"]), [0, 0.0])
                s[0] += r["written_tokens"]
                s[1] += r["write_ms"] / 1000
        speed_trend = {}
        for (mid, day), (tok, sec) in speed_days.items():
            speed_trend.setdefault(mid, {})[day] = round(tok / sec, 2) if sec else None

        return {
            "range": {"days": days, "from": start_day.isoformat(), "to": today.isoformat()},
            "totals": total, "previous": self._totals(prev),
            "by_model": by_model, "daily": list(daily.values()), "heat": heat, "heat_calls": heat_calls,
            "by_purpose": grouped("purpose"), "by_harness": grouped("harness"), "top_chats": top_chats,
            "activity": {"daily": list(act.values()), "new_chats": new_chats,
                         "you": sum(a["you"] for a in act.values()), "replies": sum(a["replies"] for a in act.values())},
            "tests": tests, "media": media, "highlights": highlights, "latency": latency,
            "speed_trend": speed_trend, "names": {m["model_id"]: m["name"] for m in by_model},
            "has_history": any(r["status"] == "history" for r in rows),
        }

    def export_rows(self, days=30, now=None):
        now = now or time.time()
        start = now - days * 86400
        names = self.names()
        out = []
        for r in self.db.all("SELECT * FROM model_calls WHERE ts >= ? ORDER BY ts", (start,)):
            r["model_name"] = names.get(r["model_id"]) or r["model_id"]
            r["when"] = _local(r["ts"]).isoformat(timespec="seconds")
            out.append(r)
        return out

    def clear(self):
        self.db.run("DELETE FROM model_calls")
        self.db.set_setting("usage_backfilled", True)       # cleared means cleared: the old replies stay out
