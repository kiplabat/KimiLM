"""Suggestions (2.24.0): better ways of working, found in what the user already asks KimiLM.

KimiOS scope §2.6 and principle 8. Off until the user turns it on (setting suggestions_enabled). It looks only at
KimiLM's own chats on this computer (nothing else is watched), sends nothing anywhere, and never acts: a
suggestion is shown, and the user takes it or dismisses it for good.

The first kind: the same request, started as a new chat on several different days, becomes "make it a scheduled
task", with the usual time of day and whether it was only on weekdays. Requests are compared after removing what
changes from day to day (numbers, dates, punctuation); two requests are the same when most of their words are.
"""
import hashlib
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

WINDOW_DAYS = 45
MIN_DAYS = 3
SIMILAR = 0.7
WORD = re.compile(r"[a-z][a-z']+")
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "me", "my", "please", "can", "you", "it", "is",
        "with", "from", "this", "that", "what", "today", "yesterday", "tomorrow", "now", "again"}


def words(text):
    return {w for w in WORD.findall((text or "").lower()[:600]) if w not in STOP}


def similar(a, b):
    return len(a & b) / max(1, len(a | b))


def _local(stamp):
    t = datetime.fromisoformat(str(stamp))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone()


def repeated_requests(rows, min_days=MIN_DAYS):
    """rows: the first user message of each chat, as {"content", "created"}. Groups of the same request asked on at
    least min_days different days, most frequent first."""
    groups = []
    for r in rows:
        w = words(r.get("content"))
        if len(w) < 3:
            continue                                      # "hi", "thanks": not a request worth scheduling
        for g in groups:
            if similar(w, g["words"]) >= SIMILAR:
                g["rows"].append(r)
                break
        else:
            groups.append({"words": set(w), "rows": [r]})
    out = []
    for g in groups:
        times = [_local(r["created"]) for r in g["rows"]]
        days = {t.date() for t in times}
        if len(days) < min_days:
            continue
        hour = sorted(t.hour for t in times)[len(times) // 2]
        weekdays = all(t.weekday() < 5 for t in times)
        latest = max(g["rows"], key=lambda r: str(r["created"]))
        out.append({"days": len(days), "times": len(times), "hour": hour, "weekdays_only": weekdays,
                    "prompt": latest["content"].strip()[:2000], "last": str(latest["created"]),
                    "cron": f"0 {hour} * * {'1-5' if weekdays else '*'}"})
    out.sort(key=lambda s: (-s["days"], -s["times"]))
    return out


def suggestion_id(prompt):
    return "sug_" + hashlib.sha1(" ".join(sorted(words(prompt))).encode()).hexdigest()[:12]


class Suggestions:
    def __init__(self, app):
        self.app, self.db = app, app.db

    def enabled(self):
        return bool(self.db.setting("suggestions_enabled", False))

    def _first_requests(self):
        since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat(timespec="milliseconds")
        return self.db.all(
            "SELECT m.content, m.created FROM messages m JOIN chats c ON c.id = m.chat_id "
            "WHERE m.role = 'user' AND c.origin = 'user' AND c.schedule_id IS NULL AND m.created >= ? "
            "AND m.created = (SELECT MIN(created) FROM messages WHERE chat_id = m.chat_id AND role = 'user') "
            "ORDER BY m.created", (since,))

    def list(self):
        if not self.enabled():
            return {"enabled": False, "items": []}
        dismissed = set(self.db.setting("suggestions_dismissed") or [])
        scheduled = {(s.get("prompt") or "").strip() for s in self.db.all("SELECT prompt FROM schedules")}
        items = []
        for r in repeated_requests(self._first_requests()):
            sid = suggestion_id(r["prompt"])
            if sid in dismissed or r["prompt"] in scheduled:
                continue
            when = f"{'weekdays' if r['weekdays_only'] else 'every day'} at {r['hour']:02d}:00"
            items.append({"id": sid, "kind": "schedule",
                          "title": f"You asked this on {r['days']} different days",
                          "offer": f"Make it a scheduled task, {when}?", **r})
        return {"enabled": True, "items": items[:5]}

    def dismiss(self, sid):
        dismissed = list(self.db.setting("suggestions_dismissed") or [])
        if sid not in dismissed:
            self.db.set_setting("suggestions_dismissed", dismissed + [sid])
        return {"ok": True}

    def accept(self, sid):
        item = next((i for i in self.list()["items"] if i["id"] == sid), None)
        if not item:
            raise KeyError(sid)
        name = " ".join(item["prompt"].split()[:8])
        schedule = self.app.scheduler.create({"name": name, "cron": item["cron"], "prompt": item["prompt"],
                                              "only_on_ac": False, "catch_up": 1})
        self.dismiss(sid)
        return schedule
