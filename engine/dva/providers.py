"""Online model providers (KimiLM 2.3.0): which ones exist, their keys, the models each offers, prices, the
monthly budget and what has been spent.

Local models (llama.cpp) stay the default and cost nothing. Online models are added per provider with an API key
(kept in the Windows Credential Manager), then the models to use are picked from the provider's live list, so new
models appear without an app update. Every call is recorded in `usage` with its tokens and cost; a monthly cap
(warnings at 50 % and 80 %) makes the router fall back to local models once it is reached.

Model ids: local models keep their ids; online ones are "cloud:<provider>:<remote model id>".

Prices: OpenRouter lists its own prices; for the other providers the public OpenRouter catalogue is used as a price
list (matched by name, refreshed daily). A model with no known price is charged at a cautious estimate
(settings: cloud_unknown_price) and marked "estimated" until you enter its price."""
import asyncio
import json
import re
import time
from datetime import datetime, timezone

import httpx

from .db import new_id, now

PREFIX = "cloud:"

# api: openai_responses (OpenAI's Responses API), anthropic (Messages API), chat (OpenAI-style chat completions)
# quirks: gemini_signatures (thought signatures on tool calls), echo_reasoning (reasoning_content must be sent back
# in tool loops), reasoning_details (OpenRouter's reasoning blocks), json_object_only (no json_schema answers)
PROVIDERS = {
    "openai": {"name": "OpenAI", "api": "openai_responses", "base": "https://api.openai.com/v1",
               "key_url": "https://platform.openai.com/api-keys", "price_prefix": "openai/"},
    "anthropic": {"name": "Anthropic (Claude)", "api": "anthropic", "base": "https://api.anthropic.com/v1",
                  "key_url": "https://console.anthropic.com/settings/keys", "price_prefix": "anthropic/"},
    "google": {"name": "Google Gemini", "api": "chat", "base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_url": "https://aistudio.google.com/apikey", "price_prefix": "google/",
               "quirks": ["gemini_signatures"]},
    "openrouter": {"name": "OpenRouter (many models, one key)", "api": "chat", "base": "https://openrouter.ai/api/v1",
                   "key_url": "https://openrouter.ai/keys", "quirks": ["reasoning_details"], "own_prices": True},
    "moonshot": {"name": "Kimi (Moonshot AI)", "api": "chat", "base": "https://api.moonshot.ai/v1",
                 "key_url": "https://platform.kimi.ai", "price_prefix": "moonshotai/", "quirks": ["echo_reasoning"]},
    "deepseek": {"name": "DeepSeek", "api": "chat", "base": "https://api.deepseek.com",
                 "key_url": "https://platform.deepseek.com/api_keys", "price_prefix": "deepseek/",
                 "quirks": ["echo_reasoning", "json_object_only"]},
    "mistral": {"name": "Mistral", "api": "chat", "base": "https://api.mistral.ai/v1",
                "key_url": "https://console.mistral.ai/api-keys", "price_prefix": "mistralai/"},
    "groq": {"name": "Groq", "api": "chat", "base": "https://api.groq.com/openai/v1",
             "key_url": "https://console.groq.com/keys", "price_prefix": ""},
    "xai": {"name": "xAI (Grok)", "api": "chat", "base": "https://api.x.ai/v1",
            "key_url": "https://console.x.ai", "price_prefix": "x-ai/"},
}
CUSTOM_PREFIX = "custom-"          # custom OpenAI-compatible endpoints: "custom-<slug>"
NOT_CHAT = re.compile(r"(embed|whisper|tts|dall-?e|davinci|babbage|moderation|transcribe|realtime|audio|image|imagen|"
                      r"veo|aqa|search-preview|computer-use|codex-mini|sora|omni-moderation|rerank|guard|"
                      r"text-embedding|learnlm|gemma-?3n?-?e)", re.I)
ROLES = ("strategist", "worker", "both", "off")
DEFAULT_BUDGET = 10.0
DEFAULT_UNKNOWN_PRICE = {"in": 5.0, "out": 25.0}      # USD per million tokens, deliberately on the high side


class ProviderError(RuntimeError):
    pass


def month_key(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m")


def is_cloud(model_id):
    return bool(model_id) and str(model_id).startswith(PREFIX)


def cloud_id(provider, remote):
    return f"{PREFIX}{provider}:{remote}"


def split_id(model_id):
    rest = str(model_id)[len(PREFIX):]
    provider, _, remote = rest.partition(":")
    return provider, remote


def _norm(name):
    """For matching names across providers: claude-opus-4.5 == claude-opus-4-5-20251101."""
    s = name.lower().split("/")[-1]
    s = re.sub(r"^models/", "", s)
    s = s.replace(".", "-").replace("_", "-")
    s = re.sub(r"-(20\d{6}|20\d{2}-\d{2}-\d{2}|latest|preview|exp)$", "", s)
    s = re.sub(r"-(20\d{6}|20\d{2}-\d{2}-\d{2})$", "", s)
    return s


def strength_of(provider, remote, caps=None):
    """1–5: how much reasoning to expect. A rough guide for the router; every model's role can be changed."""
    s = remote.lower()
    line = (caps or {}).get("line")
    if line in ("opus", "mythos", "fable") or re.search(r"opus|mythos|fable", s):
        return 5
    if line == "sonnet" or "sonnet" in s:
        return 4.5
    if line == "haiku" or "haiku" in s:
        return 3.5
    if re.search(r"nano|flash-?lite|\blite\b|-lite|8b|7b|3b|1b|tiny|ministral", s):
        return 2.5
    if re.search(r"mini|small|flash|turbo|instant|fast|haiku|air\b", s):
        return 3.5
    if re.search(r"\bpro\b|-pro|ultra|large|max|reasoner|thinking|opus|k3|grok-[4-9]|gpt-[5-9]|\bo[1-9]\b|o[1-9]-", s):
        return 5
    return 4


class Providers:
    def __init__(self, db, secrets, bus, log=print):
        self.db, self.secrets, self.bus, self.log = db, secrets, bus, log
        self._price_cache = {"at": 0, "rows": {}}
        self._lists = {}

    # ------------------------------------------------------------------ configuration
    def _state(self):
        return dict(self.db.setting("cloud_providers", {}) or {})

    def _save_state(self, state):
        self.db.set_setting("cloud_providers", state)
        self.bus.publish("models_changed")

    def conf(self, pid):
        if pid in PROVIDERS:
            return {"id": pid, **PROVIDERS[pid], **{k: v for k, v in (self._state().get(pid) or {}).items()
                                                    if k in ("base",) and v}}
        custom = (self._state().get(pid) or {}) if pid.startswith(CUSTOM_PREFIX) else None
        if custom:
            return {"id": pid, "name": custom.get("name") or pid, "api": "chat", "base": custom.get("base", ""),
                    "custom": True, "key_optional": True, "quirks": custom.get("quirks") or []}
        raise ProviderError(f"Unknown provider {pid!r}")

    def key(self, pid):
        return self.secrets.get(f"provider:{pid}:api_key")

    def has_key(self, pid):
        return self.secrets.has(f"provider:{pid}:api_key")

    def ready(self, pid):
        try:
            c = self.conf(pid)
        except ProviderError:
            return False
        if (self._state().get(pid) or {}).get("enabled") is False:
            return False
        return bool(c.get("base")) and (self.has_key(pid) or c.get("key_optional"))

    def list(self):
        state = self._state()
        ids = list(PROVIDERS) + sorted(p for p in state if p.startswith(CUSTOM_PREFIX))
        out = []
        for pid in ids:
            c = self.conf(pid)
            st = state.get(pid) or {}
            out.append({"id": pid, "name": c["name"], "api": c["api"], "base": c.get("base", ""),
                        "key_url": c.get("key_url"), "custom": bool(c.get("custom")),
                        "has_key": self.has_key(pid), "enabled": st.get("enabled", True), "ready": self.ready(pid),
                        "checked": st.get("checked"), "error": st.get("error"),
                        "models": self.db.one("SELECT COUNT(*) AS n FROM cloud_models WHERE provider=?", (pid,))["n"]})
        return out

    def set_key(self, pid, key):
        self.conf(pid)
        if key:
            self.secrets.set(f"provider:{pid}:api_key", key.strip())
        else:
            self.secrets.delete(f"provider:{pid}:api_key")
        state = self._state()
        state.setdefault(pid, {}).update(error=None, checked=None)
        self._save_state(state)

    def update(self, pid, values):
        state = self._state()
        st = state.setdefault(pid, {})
        for k in ("enabled", "base", "name"):
            if k in values:
                st[k] = values[k]
        self._save_state(state)

    def add_custom(self, name, base, key=None):
        slug = re.sub(r"[^a-z0-9]+", "-", (name or "custom").lower()).strip("-")[:30] or "custom"
        pid = CUSTOM_PREFIX + slug
        state = self._state()
        n = 2
        while pid in state:
            pid = f"{CUSTOM_PREFIX}{slug}-{n}"
            n += 1
        state[pid] = {"name": name or "Custom endpoint", "base": base.rstrip("/"), "enabled": True}
        self._save_state(state)
        if key:
            self.secrets.set(f"provider:{pid}:api_key", key.strip())
        return pid

    def remove(self, pid):
        """Forget a provider's key and models (custom endpoints are removed entirely)."""
        self.secrets.delete(f"provider:{pid}:api_key")
        self.db.run("DELETE FROM cloud_models WHERE provider=?", (pid,))
        state = self._state()
        if pid.startswith(CUSTOM_PREFIX):
            state.pop(pid, None)
        else:
            state[pid] = {"enabled": True}
        self._save_state(state)

    # ------------------------------------------------------------------ HTTP
    def headers(self, pid):
        c = self.conf(pid)
        key = self.key(pid)
        if c["api"] == "anthropic":
            return {"x-api-key": key or "", "anthropic-version": "2023-06-01"}
        h = {"Authorization": f"Bearer {key}"} if key else {}
        if pid == "openrouter":
            h.update({"HTTP-Referer": "https://github.com/kiplabat/KimiLM", "X-Title": "KimiLM"})
        return h

    @staticmethod
    def readable(status, text):
        t = (text or "")[:400]
        if status in (401, 403):
            return "The API key was refused. Check it in Models → Online, or make a new one."
        if status == 402 or re.search(r"credit|balance|billing|quota|insufficient", t, re.I):
            return "The provider says there is no credit or quota left on this account. Add credit on its website."
        if status == 404:
            return f"The provider does not know this model or address (HTTP 404). {t}"
        if status == 429:
            return "The provider is limiting requests right now (too many, or too fast). Try again shortly."
        return f"HTTP {status}: {t}"

    async def fetch_models(self, pid):
        """The provider's live list, normalised."""
        c = self.conf(pid)
        if not c.get("base"):
            raise ProviderError("Set the endpoint address first.")
        if not self.has_key(pid) and not c.get("key_optional"):
            raise ProviderError("Add the API key first.")
        url = c["base"].rstrip("/") + "/models"
        rows, params = [], {"limit": 1000} if c["api"] == "anthropic" else {}
        async with httpx.AsyncClient(timeout=30, trust_env=True) as http:
            for _ in range(20):
                r = await http.get(url, headers=self.headers(pid), params=params)
                if r.status_code != 200:
                    raise ProviderError(self.readable(r.status_code, r.text))
                data = r.json()
                rows += data.get("data") or data.get("models") or []
                if c["api"] == "anthropic" and data.get("has_more") and data.get("last_id"):
                    params = {"limit": 1000, "after_id": data["last_id"]}
                    continue
                break
        out = []
        for m in rows:
            mid = str(m.get("id") or m.get("name") or "")
            if mid.startswith("models/"):
                mid = mid[len("models/"):]
            if not mid or NOT_CHAT.search(mid):
                continue
            out.append(self._normalise(pid, c, mid, m))
        self._lists[pid] = {m["remote_id"]: m for m in out}
        state = self._state()
        state.setdefault(pid, {}).update(checked=now(), error=None)
        self.db.set_setting("cloud_providers", state)
        await self.attach_prices(pid, out)
        out.sort(key=lambda m: (-m["strength"], m["name"].lower()))
        return out

    def _normalise(self, pid, c, mid, m):
        caps = {"context": None, "max_output": None, "vision": None, "tools": True, "reasoning": None}
        if c["api"] == "anthropic":
            cap = m.get("capabilities") or {}
            th = (cap.get("thinking") or {}).get("types") or {}
            caps.update(context=m.get("max_input_tokens") or 200000, max_output=m.get("max_tokens") or None,
                        vision=bool((cap.get("image_input") or {}).get("supported", True)),
                        reasoning=bool((cap.get("thinking") or {}).get("supported")),
                        thinking={k: bool((v or {}).get("supported")) for k, v in th.items()},
                        efforts=[k for k in ("low", "medium", "high", "xhigh", "max")
                                 if ((cap.get("effort") or {}).get(k) or {}).get("supported")],
                        structured=bool((cap.get("structured_outputs") or {}).get("supported")),
                        line=m.get("line"), lifecycle=m.get("lifecycle"))
            name = m.get("display_name") or mid
        elif pid == "openrouter":
            arch = m.get("architecture") or {}
            sp = m.get("supported_parameters") or []
            caps.update(context=m.get("context_length"),
                        max_output=(m.get("top_provider") or {}).get("max_completion_tokens"),
                        vision="image" in (arch.get("input_modalities") or []),
                        tools="tools" in sp, reasoning="reasoning" in sp or "include_reasoning" in sp)
            name = m.get("name") or mid
        else:
            caps.update(context=m.get("context_length") or m.get("context_window") or m.get("input_token_limit"),
                        max_output=m.get("max_output_tokens") or m.get("output_token_limit"))
            plain = m.get("name") if isinstance(m.get("name"), str) and "/" not in m["name"] else None
            name = m.get("display_name") or plain or mid
            low = mid.lower()
            caps["vision"] = bool(re.search(r"gpt-4o|gpt-4\.1|gpt-[5-9]|\bo[134]\b|o[34]-|gemini|grok-[2-9].*vision|"
                                            r"grok-[4-9]|pixtral|mistral-(medium|large)|vision|-vl|kimi-k2\.[5-9]|"
                                            r"kimi-k[3-9]|llama-4", low))
            caps["reasoning"] = bool(re.search(r"\bo[1-9]|gpt-[5-9]|reason|think|gemini-(2\.5|[3-9])|grok-[3-9]|"
                                               r"kimi-k2\.[5-9]|kimi-k[3-9]|deepseek-(r|v[3-9])|magistral", low))
        price = {}
        if pid == "openrouter":
            p = m.get("pricing") or {}
            try:
                price = {"in": float(p.get("prompt") or 0) * 1e6, "out": float(p.get("completion") or 0) * 1e6,
                         "source": "OpenRouter"}
            except (TypeError, ValueError):
                price = {}
        return {"remote_id": mid, "name": name, "caps": caps, "price": price,
                "strength": strength_of(pid, mid, caps)}

    # ------------------------------------------------------------------ prices
    async def price_list(self):
        """OpenRouter's public catalogue as a price list (no key needed), refreshed daily."""
        if time.time() - self._price_cache["at"] < 86400 and self._price_cache["rows"]:
            return self._price_cache["rows"]
        cached = self.db.setting("cloud_price_list") or {}
        if cached.get("at") and time.time() - cached["at"] < 86400:
            self._price_cache = {"at": cached["at"], "rows": cached["rows"]}
            return cached["rows"]
        rows = {}
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=True) as http:
                r = await http.get("https://openrouter.ai/api/v1/models")
                r.raise_for_status()
                for m in r.json().get("data") or []:
                    p = m.get("pricing") or {}
                    try:
                        rows[m["id"]] = {"in": float(p.get("prompt") or 0) * 1e6,
                                         "out": float(p.get("completion") or 0) * 1e6}
                    except (TypeError, ValueError, KeyError):
                        continue
        except Exception as exc:
            self.log(f"price list unavailable: {type(exc).__name__}: {exc}")
            return cached.get("rows") or {}
        self._price_cache = {"at": time.time(), "rows": rows}
        self.db.set_setting("cloud_price_list", {"at": time.time(), "rows": rows})
        return rows

    async def attach_prices(self, pid, models):
        c = self.conf(pid)
        if c.get("own_prices"):
            return
        prefix = c.get("price_prefix")
        if prefix is None:
            return
        rows = await self.price_list()
        index = {}
        for rid, p in rows.items():
            if not prefix or rid.startswith(prefix):
                index.setdefault(_norm(rid), p)
        for m in models:
            if m.get("price"):
                continue
            p = index.get(_norm(m["remote_id"]))
            if p:
                m["price"] = {**p, "source": "OpenRouter price list"}

    # ------------------------------------------------------------------ chosen models
    def models(self, enabled_only=False):
        rows = self.db.all("SELECT * FROM cloud_models ORDER BY provider, name")
        out = []
        for r in rows:
            r["kind"] = "cloud"
            r["ready"] = self.ready(r["provider"])
            r["role"] = (r.get("settings") or {}).get("role") or self.default_role(r)
            r["strength"] = (r.get("settings") or {}).get("strength") or strength_of(r["provider"], r["remote_id"],
                                                                                   r.get("caps"))
            r["provider_name"] = self._pname(r["provider"])
            if enabled_only and not (r["enabled"] and r["ready"]):
                continue
            out.append(r)
        return out

    def _pname(self, pid):
        try:
            return self.conf(pid)["name"]
        except ProviderError:
            return pid

    @staticmethod
    def default_role(row):
        s = (row.get("settings") or {}).get("strength") or strength_of(row["provider"], row["remote_id"],
                                                                      row.get("caps"))
        return "strategist" if s >= 4.5 else "worker"

    def get(self, model_id):
        r = self.db.get("cloud_models", model_id)
        if r:
            r["kind"] = "cloud"
            r["ready"] = self.ready(r["provider"])
            r["role"] = (r.get("settings") or {}).get("role") or self.default_role(r)
            r["provider_name"] = self._pname(r["provider"])
        return r

    def add_model(self, pid, entry):
        mid = cloud_id(pid, entry["remote_id"])
        values = {"provider": pid, "remote_id": entry["remote_id"], "name": entry.get("name") or entry["remote_id"],
                  "caps": entry.get("caps") or {}, "price": entry.get("price") or {}}
        if self.db.get("cloud_models", mid):
            self.db.update("cloud_models", mid, values)
        else:
            self.db.insert("cloud_models", {"id": mid, **values, "enabled": 1, "settings": {}, "added": now()})
        self.bus.publish("models_changed")
        return self.get(mid)

    async def add_by_id(self, pid, remote_id):
        listing = self._lists.get(pid) or {m["remote_id"]: m for m in await self.fetch_models(pid)}
        entry = listing.get(remote_id) or {"remote_id": remote_id, "name": remote_id, "caps": {"tools": True},
                                           "price": {}, "strength": strength_of(pid, remote_id)}
        return self.add_model(pid, entry)

    def update_model(self, model_id, values):
        row = self.db.get("cloud_models", model_id)
        if not row:
            raise KeyError(model_id)
        changes = {}
        if "enabled" in values:
            changes["enabled"] = 1 if values["enabled"] else 0
        if "name" in values and values["name"]:
            changes["name"] = str(values["name"])[:100]
        settings = dict(row.get("settings") or {})
        for k in ("role", "strength", "effort", "max_parallel"):
            if k in values:
                if values[k] in (None, ""):
                    settings.pop(k, None)
                elif k == "role" and values[k] not in ROLES:
                    raise ValueError("role must be strategist, worker, both or off")
                else:
                    settings[k] = values[k]
        changes["settings"] = settings
        if "price_in" in values or "price_out" in values:
            price = dict(row.get("price") or {})
            for k, dst in (("price_in", "in"), ("price_out", "out")):
                if k in values:
                    if values[k] in (None, ""):
                        price.pop(dst, None)
                    else:
                        price[dst] = float(values[k])
            price["source"] = "you"
            changes["price"] = price
        self.db.update("cloud_models", model_id, changes)
        self.bus.publish("models_changed")
        return self.get(model_id)

    def delete_model(self, model_id):
        self.db.delete("cloud_models", model_id)
        self.bus.publish("models_changed")

    # ------------------------------------------------------------------ spending
    def budget(self):
        try:
            return float(self.db.setting("cloud_budget_monthly", DEFAULT_BUDGET))
        except (TypeError, ValueError):
            return DEFAULT_BUDGET

    def spent(self, month=None):
        row = self.db.one("SELECT COALESCE(SUM(cost), 0) AS c FROM usage WHERE month=?", (month or month_key(),))
        return float(row["c"] or 0)

    def budget_left(self):
        cap = self.budget()
        return None if cap <= 0 else cap - self.spent()

    def over_budget(self):
        left = self.budget_left()
        return left is not None and left <= 0

    def price_of(self, row):
        p = row.get("price") or {}
        if p.get("in") is not None and p.get("out") is not None:
            return float(p["in"]), float(p["out"]), False
        unknown = self.db.setting("cloud_unknown_price") or DEFAULT_UNKNOWN_PRICE
        return float(unknown.get("in", 5)), float(unknown.get("out", 25)), True

    def record(self, model_id, input_tokens, output_tokens, ctx=None):
        row = self.get(model_id) or {"provider": split_id(model_id)[0], "price": {}}
        pin, pout, estimated = self.price_of(row)
        cost = (input_tokens or 0) / 1e6 * pin + (output_tokens or 0) / 1e6 * pout
        ctx = ctx or {}
        self.db.insert("usage", {"id": new_id("use_"), "created": now(), "month": month_key(), "model_id": model_id,
                                 "provider": row["provider"], "chat_id": ctx.get("chat_id"), "job_id": ctx.get("job_id"),
                                 "purpose": ctx.get("purpose") or "chat", "input_tokens": int(input_tokens or 0),
                                 "output_tokens": int(output_tokens or 0), "cost": round(cost, 6),
                                 "estimated": 1 if estimated else 0})
        if self.db.get("cloud_models", model_id):
            self.db.update("cloud_models", model_id, {"last_used": now()})
        self._warn()
        return cost, estimated

    def _warn(self):
        cap = self.budget()
        if cap <= 0:
            return
        spent = self.spent()
        month = month_key()
        warned = dict(self.db.setting("cloud_budget_warned", {}) or {})
        done = set(warned.get(month) or [])
        for level in (50, 80, 100):
            if spent >= cap * level / 100 and level not in done:
                done.add(level)
                body = (f"Online models have used ${spent:.2f} of your ${cap:.2f} monthly budget."
                        + (" Auto now uses local models until next month, or until you raise the budget in Models → "
                           "Online." if level == 100 else ""))
                self.bus.publish("notify", title="Monthly budget reached" if level == 100 else f"{level}% of the "
                                 "monthly budget used", body=body)
                self.bus.publish("budget", level=level, spent=spent, cap=cap)
        warned = {month: sorted(done)}             # only this month is kept
        self.db.set_setting("cloud_budget_warned", warned)

    def usage_summary(self, month=None):
        month = month or month_key()
        by_model = self.db.all("SELECT model_id, provider, SUM(input_tokens) AS input, SUM(output_tokens) AS output, "
                               "SUM(cost) AS cost, COUNT(*) AS calls, MAX(estimated) AS estimated FROM usage "
                               "WHERE month=? GROUP BY model_id ORDER BY cost DESC", (month,))
        for r in by_model:
            m = self.get(r["model_id"])
            r["name"] = (m or {}).get("name") or split_id(r["model_id"])[1]
        by_purpose = self.db.all("SELECT purpose, SUM(cost) AS cost, COUNT(*) AS calls FROM usage WHERE month=? "
                                 "GROUP BY purpose ORDER BY cost DESC", (month,))
        months = self.db.all("SELECT month, SUM(cost) AS cost FROM usage GROUP BY month ORDER BY month DESC LIMIT 12")
        cap = self.budget()
        spent = self.spent(month)
        return {"month": month, "spent": round(spent, 4), "budget": cap,
                "left": None if cap <= 0 else round(cap - spent, 4), "by_model": by_model, "by_purpose": by_purpose,
                "months": months}

    def chat_cost(self, chat_id):
        row = self.db.one("SELECT COALESCE(SUM(cost), 0) AS c FROM usage WHERE chat_id=?", (chat_id,))
        return float(row["c"] or 0)

    async def test(self, pid):
        """Check the key by listing models; returns how many chat models it offers."""
        try:
            listing = await self.fetch_models(pid)
        except ProviderError as exc:
            state = self._state()
            state.setdefault(pid, {}).update(error=str(exc), checked=now())
            self._save_state(state)
            raise
        except httpx.HTTPError as exc:
            msg = f"Could not reach the provider ({type(exc).__name__}). Check the internet connection."
            state = self._state()
            state.setdefault(pid, {}).update(error=msg, checked=now())
            self._save_state(state)
            raise ProviderError(msg) from exc
        self.bus.publish("models_changed")
        return {"ok": True, "models": len(listing)}

    async def suggested(self, pid, listing=None):
        """A sensible first pick when a key is added: the strongest model and a fast, cheaper one."""
        listing = listing or await self.fetch_models(pid)
        if pid == "openrouter" or not listing:
            return []
        usable = [m for m in listing if (m["caps"].get("lifecycle") in (None, "active"))]
        strong = sorted(usable, key=lambda m: (-m["strength"], -(m["price"].get("out") or 0)))[:1]
        fast = [m for m in sorted(usable, key=lambda m: (abs(m["strength"] - 3.5), m["price"].get("out") or 99))
                if m not in strong][:1]
        return strong + fast
