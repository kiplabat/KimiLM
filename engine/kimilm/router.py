"""Choosing models (KimiLM 2.3.0): local models for routine work, online models for strategy and hard reasoning.

Every usable model, local or online, is a candidate with a role (strategist, worker, both, off) and a strength
(1–5: local from its size, online from its family; both can be changed in Models). Roles:
  strategist  plans, decides, reviews, writes the final answer of hard tasks
  worker      does the parts: routine, repetitive, well-defined work
  vision      a worker that can see images and video

"Auto" (a chat's model set to auto) picks per request: requests that look like strategy, planning, analysis or
design go to the best strategist; everything else to the best local worker. Online models are skipped when there
are none, the monthly budget is used up, or the privacy setting keeps this chat local. Each choice comes with a
sentence saying why, shown in the chat."""
import re

from .providers import is_cloud

AUTO = "auto"
STRATEGY = re.compile(
    r"\b(strateg\w*|plan(?:s|ning)?|roadmap|architect\w*|design\w*|trade-?offs?|decid\w*|decision|recommend\w*|"
    r"evaluat\w*|assess\w*|analy[sz]\w*|compar\w*|critique|critici[sz]\w*|review\w*|prioriti[sz]\w*|risks?|forecast\w*|"
    r"why\b|root cause|diagnos\w*|prove|proof|derive|reason\w*|investigat\w*|synthes\w*|propos\w*|negotiat\w*|"
    r"policy|business case|framework|hypothes\w*|theor\w*|debug\w*|refactor\w*|optimi[sz]\w*|pros and cons)\b", re.I)
ROUTINE = re.compile(
    r"\b(rename|reformat|format|convert|extract|list|translate|typo|sort|count|copy|move|tag|classify|fill in|"
    r"template|transcribe|clean up|tidy|bulk|batch|each (file|row|item)|every (file|row|item)|repeat\w*)\b", re.I)
HEAVY_HARNESSES = {"planner", "team", "lead-workers"}


def complexity(text, harness=None):
    """0–1: how much the request needs a strong reasoner. Deliberately simple and explainable."""
    t = text or ""
    score = 0.25
    hits = len(set(m.group(0).lower() for m in STRATEGY.finditer(t)))
    score += min(3, hits) * 0.15
    if ROUTINE.search(t):
        score -= 0.25
    if len(t) > 600:
        score += 0.15
    if len(t) > 2500:
        score += 0.1
    h = harness or {}
    if h.get("mode") in HEAVY_HARNESSES or h.get("id") in HEAVY_HARNESSES:
        score += 0.25
    return max(0.0, min(1.0, score))


def local_strength(row):
    from .models import params_from_name
    total, _ = params_from_name(row.get("name") or row.get("path") or "")
    if not total and row.get("size"):
        total = row["size"] / 0.6e9          # roughly the parameters of a Q4 file
    total = total or 4
    for limit, s in ((4.5, 2), (9.5, 2.5), (16, 3), (36, 3.5), (80, 4)):
        if total <= limit:
            return s
    return 4.5


class Router:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------ candidates
    def local(self):
        out = []
        for r in self.app.models.installed():
            if not r.get("exists"):
                continue
            st = r.get("settings") or {}
            role = st.get("role") or "worker"
            if role == "off":
                continue
            out.append({"id": r["id"], "name": r["name"], "kind": "local", "role": role,
                        "strength": float(st.get("strength") or local_strength(r)),
                        "vision": bool(st.get("mmproj") and st.get("vision") is not False), "cost": 0.0,
                        "default": r["id"] == self.app.db.setting("default_model")})
        return out

    def online(self, chat=None, project=None):
        """Usable online models, or [] with the reason they are not."""
        p = self.app.providers
        rows = [r for r in p.models(enabled_only=True) if r["role"] != "off"]
        if not rows:
            return [], "no online model is set up"
        if p.over_budget():
            return [], "this month's online budget is used up"
        if not self.online_allowed(chat, project):
            return [], "this chat is set to local models only"
        out = []
        for r in rows:
            pin, pout, _ = p.price_of(r)
            out.append({"id": r["id"], "name": r["name"], "kind": "cloud", "role": r["role"],
                        "strength": float(r["strength"]), "vision": bool((r.get("caps") or {}).get("vision")),
                        "cost": pin + 4 * pout, "provider": r["provider_name"]})
        return out, ""

    def online_allowed(self, chat=None, project=None):
        db = self.app.db
        if db.setting("cloud_enabled", True) is False:
            return False
        mode = db.setting("cloud_privacy", "ask")
        if mode != "local_unless_on":
            return True
        if chat and db.setting(f"chat_online:{chat['id']}", False):
            return True
        if project and db.setting(f"project_online:{project['id']}", False):
            return True
        return False

    # ------------------------------------------------------------------ picking
    @staticmethod
    def _best(cands, role, vision=False):
        pool = [c for c in cands if (not vision or c["vision"])]
        if role == "strategist":
            pool = [c for c in pool if c["role"] in ("strategist", "both")] or pool
            return max(pool, key=lambda c: (c["strength"], c["kind"] == "cloud", -c["cost"]), default=None)
        pool = [c for c in pool if c["role"] in ("worker", "both")] or pool
        # workers: good enough and cheap; the default local model first among equals
        return max(pool, key=lambda c: (c["kind"] == "local", c.get("default", False), c["strength"], -c["cost"]),
                   default=None)

    def pick(self, role="worker", vision=False, chat=None, project=None, prefer_local=None):
        """(model id, reason) for a role, or (None, reason)."""
        local = self.local()
        online, why_not = self.online(chat, project)
        if role == "vision":
            vision, role = True, "worker"
        if role == "strategist":
            best = self._best(online + local, "strategist", vision)
        else:
            best = self._best(local, "worker", vision) if prefer_local is not False else None
            if best is None:
                best = self._best(online, "worker", vision)
        if best is None:
            if vision:
                return self.pick("strategist" if role == "strategist" else "worker", False, chat, project)
            return None, "no model is installed or set up" + (f" ({why_not})" if why_not else "")
        where = "online" if best["kind"] == "cloud" else "on this computer"
        return best["id"], f"{best['name']} ({where})"

    def auto(self, chat, project, harness, text, needs_vision=False):
        """The model for one request in a chat whose model is Auto: (id, sentence for the chat, role)."""
        score = complexity(text, harness)
        role = "strategist" if score >= 0.5 or (harness or {}).get("lead_role") == "strategist" else "worker"
        mid, name = self.pick(role, needs_vision, chat, project)
        if mid is None:
            return None, name, role
        online, why_not = self.online(chat, project)
        if role == "strategist":
            reason = (f"Auto: this looks like planning or hard reasoning, so {name} leads." if is_cloud(mid) else
                      f"Auto: this looks like planning or hard reasoning; using {name}, the strongest model available"
                      + (f" ({why_not})." if why_not else "."))
        else:
            reason = f"Auto: a routine task, so {name} does it."
        return mid, reason, role

    def roster(self, chat=None, project=None):
        """What the router can choose from right now, and whom Auto would pick (for the Models page)."""
        online, why_not = self.online(chat, project)
        strategist, worker = self.pick("strategist", chat=chat, project=project), self.pick("worker", chat=chat, project=project)
        names = {c["id"]: c for c in online + self.local()}
        preview = {role: ({"id": mid, "name": names[mid]["name"], "kind": names[mid]["kind"]} if mid in names else None)
                   for role, (mid, _) in (("strategist", strategist), ("worker", worker))}
        return {"local": self.local(), "online": online, "online_unavailable": why_not, "preview": preview}
