"""Calling online models (KimiLM 2.3.0) with the same interface as the local llama-server, so every part of the
engine (chats, agents, teams, planners, memory) can use them unchanged.

Three protocols:
  OpenAIResponses  OpenAI's Responses API: reasoning models with tools, reasoning summaries, encrypted reasoning
                   carried between tool calls (nothing is stored at OpenAI: store=false).
  Anthropic        Claude's Messages API: adaptive or budgeted thinking (from the model's own capability list),
                   thinking blocks with signatures carried between tool calls, prompt caching of the instructions.
  Chat             OpenAI-style chat completions for Gemini, OpenRouter, Kimi, DeepSeek, Mistral, Groq, xAI and custom
                   endpoints, with each provider's quirks: Gemini thought signatures on tool calls, reasoning_content
                   sent back (DeepSeek, Kimi), OpenRouter reasoning_details, json_object-only answers.

Provider-specific state that has to travel with a conversation (signatures, encrypted reasoning) is returned as
`native` in the final event; the runner keeps it on the assistant message (`_native`) and the adapter puts it back
on the next request. Local servers never see it.

Every call is metered (tokens and cost) into `usage`. Requests that fail with 429/5xx are retried with backoff;
a request the provider rejects for an unsupported parameter is retried once without it, and remembered."""
import asyncio
import contextvars
import json
import random
import re
import time
from contextlib import asynccontextmanager

import httpx
from jsonschema import ValidationError, validate

from .db import now
from .llm import LLMError
from .providers import ProviderError
from .runtime import ACTIVE, RuntimeError_

USAGE = contextvars.ContextVar("dva_usage", default=None)    # {"chat_id", "job_id", "purpose"} for metering
CALL_START = contextvars.ContextVar("dva_call_start", default=None)   # when the current online call began (2.27.0)
RETRY_STATUS = (408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529)


class BudgetExceeded(RuntimeError_):
    pass


class CloudError(LLMError):
    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status, self.body = status, body or ""


class CloudServer:
    """A leased online model: what the runner and LLM need from a 'server', without a process."""
    kind = "cloud"

    def __init__(self, row, conf, providers, cloud):
        self.model_id = row["id"]
        self.model = row
        self.provider = row["provider"]
        self.remote = row["remote_id"]
        self.conf = conf
        self.providers, self.cloud = providers, cloud
        caps = row.get("caps") or {}
        self.caps = caps
        self.plan = {"ctx": int(caps.get("context") or 128000), "mmproj": "online" if caps.get("vision") else None,
                     "compute": "online", "parallel": int((row.get("settings") or {}).get("max_parallel") or 4),
                     "max_output": caps.get("max_output")}
        self.users = 0
        self.last_used = time.monotonic()
        self.loaded_at = now()
        self.port = None
        self.proc = None
        self.api_key = ""                    # never handed to scripts (pipelines run on local models)
        api = conf.get("api")
        self.adapter = (OpenAIResponses if api == "openai_responses" else Anthropic if api == "anthropic"
                        else Chat)(self)

    alive = True

    @property
    def base_url(self):
        return self.conf.get("base", "").rstrip("/")

    @property
    def headers(self):
        return self.providers.headers(self.provider)

    @property
    def current(self):
        return {"model_id": self.model_id, "compute": "online", "ctx": self.plan["ctx"], "users": self.users,
                "vision": bool(self.plan["mmproj"]), "provider": self.provider, "loaded_at": self.loaded_at}


class Cloud:
    """Leases for online models (no loading, no memory limits), per-provider request limits, the budget gate."""

    def __init__(self, providers, db):
        self.providers, self.db = providers, db
        self._sems = {}
        self.fixes = {}                      # model id -> parameters the provider rejected (learned per run)

    def sem(self, provider):
        n = int(self.db.setting("cloud_max_parallel", 4) or 4)
        s = self._sems.get(provider)
        if s is None or getattr(s, "_n", None) != n:
            s = asyncio.Semaphore(n)
            s._n = n
            self._sems[provider] = s
        return s

    def check(self, model_id):
        """Raise a readable error if this online model can't be used now."""
        row = self.providers.get(model_id)
        if not row:
            raise RuntimeError_(f"The online model {model_id.split(':', 2)[-1]} is not added. Add it in Models → Online.")
        if not row["enabled"]:
            raise RuntimeError_(f"{row['name']} is turned off in Models → Online.")
        if not self.providers.ready(row["provider"]):
            raise RuntimeError_(f"{row['provider_name']} has no API key yet (or is turned off). Add it in Models → Online.")
        if self.providers.over_budget():
            raise BudgetExceeded(f"This month's budget for online models (${self.providers.budget():.2f}) is used up, "
                                 "so online models are paused until next month. Raise the budget in Models → Online, "
                                 "or choose a local model.")
        return row

    @asynccontextmanager
    async def lease(self, model_id, on_wait=None):
        row = self.check(model_id)
        server = CloudServer(row, self.providers.conf(row["provider"]), self.providers, self)
        server.users += 1
        token = ACTIVE.set(server)
        try:
            yield server
        finally:
            server.users -= 1
            try:
                ACTIVE.reset(token)
            except ValueError:
                ACTIVE.set(None)


# ---------------------------------------------------------------------------------------------------- helpers
def _data_url(url):
    m = re.match(r"data:([^;,]+);base64,(.*)$", url or "", re.S)
    return (m.group(1), m.group(2)) if m else (None, None)


def _parse_args(text):
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {"value": value}
    except json.JSONDecodeError:
        return {"_unparsed": text}


def _text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")


def _check_json(text, schema):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
        validate(value, schema)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMError(f"model returned invalid JSON ({str(exc)[:160]})") from exc
    return value


class Adapter:
    """Shared plumbing: HTTP with retries, parameter fixes, metering."""

    def __init__(self, server):
        self.s = server
        self.providers = server.providers
        self.fixes = server.cloud.fixes.setdefault(server.model_id, set())

    @property
    def effort(self):
        return (self.s.model.get("settings") or {}).get("effort")

    def fix(self, body, text):
        """A body without the parameter the provider just rejected, or None."""
        return None

    def meter(self, input_tokens, output_tokens):
        cost, estimated = self.providers.record(self.s.model_id, input_tokens, output_tokens, USAGE.get())
        stats = getattr(self.s.cloud, "meter", None)          # usage statistics (meter.py, 2.27.0)
        if stats is not None:
            began = CALL_START.get()
            stats.record(self.s.model_id, online=True, read=input_tokens, written=output_tokens, cost=cost,
                         total_ms=(time.monotonic() - began) * 1000 if began else 0)
        return {"prompt_n": int(input_tokens or 0), "predicted_n": int(output_tokens or 0), "cost": cost,
                "cost_estimated": estimated, "online": True}

    @asynccontextmanager
    async def request(self, path, body, timeout=1800, stream=True):
        """POST with retries; yields an open (streaming) response with status 200."""
        url = self.s.base_url + path
        CALL_START.set(time.monotonic())
        async with self.s.cloud.sem(self.s.provider):
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=20), trust_env=True) as http:
                fixed = False
                for attempt in range(5):
                    try:
                        r = await http.send(http.build_request("POST", url, json=body, headers=self.s.headers),
                                            stream=stream)
                    except httpx.HTTPError as exc:
                        if attempt < 3:
                            await asyncio.sleep(1.5 * 2 ** attempt)
                            continue
                        raise CloudError(f"Could not reach {self.s.conf.get('name')}: {type(exc).__name__}. "
                                         "Check the internet connection.") from exc
                    if r.status_code == 200:
                        try:
                            yield r
                        finally:
                            await r.aclose()
                        return
                    text = (await r.aread()).decode("utf-8", errors="replace")
                    await r.aclose()
                    if r.status_code == 400 and not fixed:
                        new = self.fix(body, text)
                        if new is not None:
                            body, fixed = new, True
                            continue
                    if r.status_code in RETRY_STATUS and attempt < 4:
                        wait = r.headers.get("retry-after")
                        try:
                            wait = float(wait)
                        except (TypeError, ValueError):
                            wait = 2 * 2 ** attempt + random.random()
                        await asyncio.sleep(min(wait, 30))
                        continue
                    raise CloudError(f"{self.s.conf.get('name')}: {self.providers.readable(r.status_code, text)}",
                                     r.status_code, text)

    @staticmethod
    async def events(r):
        """Server-sent events as dicts (with 'event' when named)."""
        name = None
        async for line in r.aiter_lines():
            if not line:
                name = None
                continue
            if line.startswith("event:"):
                name = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if name and isinstance(data, dict):
                data.setdefault("type", name)
            yield data

    async def complete(self, messages, *, schema=None, tools=None, max_tokens=1024, temperature=0.2, thinking=False,
                       timeout=900, tool_choice=None):
        """One call, collected from the stream (so long answers don't time out)."""
        content, reasoning, end = [], [], None
        async for kind, value in self.stream(messages, tools=tools, max_tokens=max_tokens, temperature=temperature,
                                             thinking=thinking, timeout=timeout, tool_choice=tool_choice,
                                             schema=schema):
            if kind == "token":
                content.append(value)
            elif kind == "reasoning":
                reasoning.append(value)
            elif kind == "end":
                end = value
        out = {"content": "".join(content), "reasoning": "".join(reasoning),
               "tool_calls": [{"id": c["id"], "type": "function",
                               "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                              for c in end["tool_calls"]],
               "timings": end["timings"], "usage": end["usage"], "finish_reason": end["finish_reason"],
               "native": end.get("native")}
        if schema is not None:
            out["json"] = _check_json(end.get("json_text") or out["content"], schema)
        return out


# ---------------------------------------------------------------------------------------------------- OpenAI Responses
class OpenAIResponses(Adapter):
    def reasoning_model(self):
        if "no_reasoning" in self.fixes:
            return False
        r = self.s.caps.get("reasoning")
        return True if r is None else bool(r)

    def fix(self, body, text):
        low = text.lower()
        if "reasoning" in body and "summary" in low and ("verif" in low or "summar" in low) and \
                body["reasoning"].get("summary"):           # summaries need a verified organisation
            self.fixes.add("no_summary")
            return dict(body, reasoning={k: v for k, v in body["reasoning"].items() if k != "summary"})
        if "reasoning" in low and "reasoning" in body and ("unsupported" in low or "not supported" in low):
            self.fixes.add("no_reasoning")
            return {k: v for k, v in body.items() if k not in ("reasoning", "include")}
        if "temperature" in low and "temperature" in body:
            self.fixes.add("no_temperature")
            return {k: v for k, v in body.items() if k != "temperature"}
        return None

    @staticmethod
    def _parts(content):
        if isinstance(content, str):
            return [{"type": "input_text", "text": content}]
        out = []
        for p in content or []:
            if p.get("type") == "text":
                out.append({"type": "input_text", "text": p.get("text", "")})
            elif p.get("type") == "image_url":
                out.append({"type": "input_image", "image_url": (p.get("image_url") or {}).get("url"), "detail": "auto"})
        return out

    def convert(self, messages):
        instructions, items = [], []
        for m in messages:
            role = m.get("role")
            if role == "system":
                instructions.append(_text_of(m.get("content")))
            elif role == "user":
                items.append({"role": "user", "content": self._parts(m.get("content"))})
            elif role == "assistant":
                native = m.get("_native") or {}
                if native.get("api") == "openai_responses" and native.get("items"):
                    items.extend(native["items"])
                    continue
                if m.get("content"):
                    items.append({"role": "assistant", "content": _text_of(m["content"])})
                for tc in m.get("tool_calls") or []:
                    items.append({"type": "function_call", "call_id": tc["id"], "name": tc["function"]["name"],
                                  "arguments": tc["function"].get("arguments") or "{}"})
            elif role == "tool":
                items.append({"type": "function_call_output", "call_id": m.get("tool_call_id"),
                              "output": m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"))})
        return "\n\n".join(i for i in instructions if i), items

    def body(self, messages, tools, max_tokens, temperature, thinking, tool_choice, schema, stream):
        instructions, items = self.convert(messages)
        body = {"model": self.s.remote, "input": items, "store": False, "stream": stream,
                "max_output_tokens": int(max_tokens)}
        if instructions:
            body["instructions"] = instructions
        if tools:
            body["tools"] = [{"type": "function", "name": t["function"]["name"],
                              "description": t["function"].get("description", ""),
                              "parameters": t["function"].get("parameters") or {"type": "object"}, "strict": False}
                             for t in tools]
            if tool_choice == "required":
                body["tool_choice"] = "required"
            elif isinstance(tool_choice, dict):
                body["tool_choice"] = {"type": "function", "name": (tool_choice.get("function") or {}).get("name")}
        if self.reasoning_model():
            effort = "high" if thinking else (self.effort or ("low" if schema is not None else "medium"))
            body["reasoning"] = {"effort": effort} if "no_summary" in self.fixes else {"effort": effort, "summary": "auto"}
            body["include"] = ["reasoning.encrypted_content"]
            room = {"low": 4000, "medium": 12000, "high": 32000}.get(effort, 12000)
            cap = self.s.plan.get("max_output") or 128000
            body["max_output_tokens"] = int(min(cap, max_tokens + room))
        elif "no_temperature" not in self.fixes:
            body["temperature"] = float(temperature)
        if schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": "answer", "schema": schema, "strict": False}}
        return body

    @staticmethod
    def _clean(item):
        """An output item ready to be sent back with store=false (ids of unsaved items would be refused)."""
        if item.get("type") == "reasoning":
            return {k: v for k, v in item.items() if k in ("type", "id", "summary", "encrypted_content")}
        if item.get("type") == "function_call":
            return {k: item[k] for k in ("type", "call_id", "name", "arguments") if k in item}
        if item.get("type") == "message":
            parts = [{"type": "output_text", "text": p.get("text", "")} for p in item.get("content") or []
                     if p.get("type") == "output_text"]
            return {"role": "assistant", "content": parts or [{"type": "output_text", "text": ""}]}
        return None

    async def stream(self, messages, *, tools=None, max_tokens=2048, temperature=0.4, thinking=False, timeout=1800,
                     tool_choice=None, schema=None):
        body = self.body(messages, tools, max_tokens, temperature, thinking, tool_choice, schema, True)
        output, final, finish = [], None, "stop"
        async with self.request("/responses", body, timeout) as r:
            async for ev in self.events(r):
                t = ev.get("type", "")
                if t == "response.output_text.delta":
                    yield "token", ev.get("delta", "")
                elif t in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                    yield "reasoning", ev.get("delta", "")
                elif t == "response.reasoning_summary_part.done":
                    yield "reasoning", "\n\n"
                elif t == "response.output_item.done":
                    output.append(ev.get("item") or {})
                elif t in ("response.completed", "response.incomplete"):
                    final = ev.get("response") or {}
                    if t == "response.incomplete":
                        finish = "length"
                elif t in ("response.failed", "error"):
                    err = (ev.get("response") or {}).get("error") or ev.get("error") or ev
                    raise CloudError(f"OpenAI: {(err or {}).get('message') or err}")
        final = final or {}
        items = final.get("output") or output
        calls = [{"id": it.get("call_id") or it.get("id"), "name": it.get("name"), "arguments": _parse_args(it.get("arguments"))}
                 for it in items if it.get("type") == "function_call"]
        usage = final.get("usage") or {}
        timings = self.meter(usage.get("input_tokens"), usage.get("output_tokens"))
        text = "".join(p.get("text", "") for it in items if it.get("type") == "message"
                       for p in it.get("content") or [] if p.get("type") == "output_text")
        native = {"api": "openai_responses", "items": [c for c in (self._clean(i) for i in items) if c]} if calls else None
        yield "end", {"tool_calls": calls, "timings": timings, "usage": usage,
                      "finish_reason": "tool_calls" if calls else finish, "content": text, "native": native,
                      "json_text": text}


# ---------------------------------------------------------------------------------------------------- Anthropic
class Anthropic(Adapter):
    def thinking_config(self, thinking):
        th = self.s.caps.get("thinking") or {}
        if not thinking:
            return None, None
        if th.get("adaptive"):
            effort = self.effort or "high"
            if self.s.caps.get("efforts") and effort not in self.s.caps["efforts"]:
                effort = None
            return {"type": "adaptive"}, effort
        if th.get("enabled") or (not th and self.s.caps.get("reasoning")):
            return {"type": "enabled", "budget_tokens": 8000}, None
        return None, None

    def convert(self, messages):
        system, out = [], []

        def push(role, blocks):
            blocks = [b for b in blocks if not (b.get("type") == "text" and not b.get("text", "").strip())]
            if not blocks:
                return
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": blocks})

        for m in messages:
            role = m.get("role")
            if role == "system":
                system.append(_text_of(m.get("content")))
            elif role == "user":
                content = m.get("content")
                blocks = []
                if isinstance(content, str):
                    blocks.append({"type": "text", "text": content})
                else:
                    for p in content or []:
                        if p.get("type") == "text":
                            blocks.append({"type": "text", "text": p.get("text", "")})
                        elif p.get("type") == "image_url":
                            url = (p.get("image_url") or {}).get("url", "")
                            mime, data = _data_url(url)
                            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                                                                       "data": data}} if data else
                                          {"type": "image", "source": {"type": "url", "url": url}})
                push("user", blocks)
            elif role == "assistant":
                native = m.get("_native") or {}
                if native.get("api") == "anthropic" and native.get("blocks"):
                    push("assistant", [dict(b) for b in native["blocks"]])
                    continue
                blocks = [{"type": "text", "text": _text_of(m.get("content"))}] if m.get("content") else []
                for tc in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"],
                                   "input": _parse_args(tc["function"].get("arguments"))})
                push("assistant", blocks or [{"type": "text", "text": "(no answer)"}])
            elif role == "tool":
                text = m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
                push("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"), "content": text or "(empty)"}])
        if out and out[0]["role"] != "user":
            out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continuing the conversation)"}]})
        if not out:
            out = [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]
        return "\n\n".join(s for s in system if s), out

    def body(self, messages, tools, max_tokens, temperature, thinking, tool_choice, schema, stream):
        system, msgs = self.convert(messages)
        cap = self.s.plan.get("max_output") or 64000
        body = {"model": self.s.remote, "messages": msgs, "stream": stream, "max_tokens": int(min(cap, max_tokens))}
        if system:
            body["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        all_tools = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                      "input_schema": t["function"].get("parameters") or {"type": "object"}} for t in tools or []]
        think, effort = self.thinking_config(thinking and schema is None)
        if schema is not None:
            all_tools.append({"name": "answer", "description": "Give the answer in this exact structure.",
                              "input_schema": schema})
            body["tool_choice"] = {"type": "tool", "name": "answer"}
        elif tool_choice == "required" and all_tools and not think:
            body["tool_choice"] = {"type": "any"}
        elif isinstance(tool_choice, dict) and not think:
            body["tool_choice"] = {"type": "tool", "name": (tool_choice.get("function") or {}).get("name")}
        if all_tools:
            body["tools"] = all_tools
        if think:
            body["thinking"] = think
            if effort:
                body["output_config"] = {"effort": effort}
            if think.get("type") == "enabled":
                body["max_tokens"] = int(min(cap, max_tokens + think["budget_tokens"]))
            else:
                body["max_tokens"] = int(min(cap, max_tokens + 16000))
        else:
            body["temperature"] = max(0.0, min(1.0, float(temperature)))
        return body

    def fix(self, body, text):
        low = text.lower()
        if "thinking" in body and ("thinking" in low or "effort" in low or "output_config" in low):
            new = {k: v for k, v in body.items() if k not in ("thinking", "output_config")}
            new["temperature"] = 0.4
            return new
        if "temperature" in low and "temperature" in body:
            return {k: v for k, v in body.items() if k != "temperature"}
        return None

    async def stream(self, messages, *, tools=None, max_tokens=2048, temperature=0.4, thinking=False, timeout=1800,
                     tool_choice=None, schema=None):
        body = self.body(messages, tools, max_tokens, temperature, thinking, tool_choice, schema, True)
        blocks, usage, stop = {}, {}, None
        async with self.request("/messages", body, timeout) as r:
            async for ev in self.events(r):
                t = ev.get("type")
                if t == "message_start":
                    usage.update((ev.get("message") or {}).get("usage") or {})
                elif t == "content_block_start":
                    b = dict(ev.get("content_block") or {})
                    if b.get("type") == "tool_use":
                        b["_json"] = ""
                    blocks[ev.get("index", len(blocks))] = b
                elif t == "content_block_delta":
                    b = blocks.setdefault(ev.get("index", 0), {"type": "text", "text": ""})
                    d = ev.get("delta") or {}
                    dt = d.get("type")
                    if dt == "text_delta":
                        b["text"] = b.get("text", "") + d.get("text", "")
                        if not (schema is not None and b.get("type") == "tool_use"):
                            yield "token", d.get("text", "")
                    elif dt == "thinking_delta":
                        b["thinking"] = b.get("thinking", "") + d.get("thinking", "")
                        yield "reasoning", d.get("thinking", "")
                    elif dt == "signature_delta":
                        b["signature"] = b.get("signature", "") + d.get("signature", "")
                    elif dt == "input_json_delta":
                        b["_json"] = b.get("_json", "") + d.get("partial_json", "")
                elif t == "message_delta":
                    stop = (ev.get("delta") or {}).get("stop_reason") or stop
                    usage.update(ev.get("usage") or {})
                elif t == "error":
                    err = ev.get("error") or {}
                    raise CloudError(f"Claude: {err.get('message') or err}", body=json.dumps(err))
        ordered = [blocks[i] for i in sorted(blocks)]
        for b in ordered:
            if b.get("type") == "tool_use":
                b["input"] = _parse_args(b.pop("_json", "") or json.dumps(b.get("input") or {}))
        answer = next((b for b in ordered if b.get("type") == "tool_use" and b.get("name") == "answer"), None) \
            if schema is not None else None
        calls = [{"id": b["id"], "name": b["name"], "arguments": b["input"]} for b in ordered
                 if b.get("type") == "tool_use" and b is not answer]
        input_tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                            "cache_read_input_tokens"))
        timings = self.meter(input_tokens, usage.get("output_tokens"))
        native_blocks = [{k: v for k, v in b.items() if k in ("type", "text", "thinking", "signature", "data", "id",
                                                               "name", "input")}
                         for b in ordered if b.get("type") in ("thinking", "redacted_thinking", "text", "tool_use")]
        text = "".join(b.get("text", "") for b in ordered if b.get("type") == "text")
        finish = "tool_calls" if calls else ("length" if stop == "max_tokens" else "stop")
        yield "end", {"tool_calls": calls, "timings": timings, "usage": usage, "finish_reason": finish,
                      "content": text, "native": {"api": "anthropic", "blocks": native_blocks} if calls else None,
                      "json_text": json.dumps(answer["input"]) if answer else text}


# ---------------------------------------------------------------------------------------------------- Chat completions
class Chat(Adapter):
    @property
    def quirks(self):
        return set(self.s.conf.get("quirks") or [])

    def convert(self, messages, tools_present):
        out = []
        for m in messages:
            msg = {k: v for k, v in m.items() if not k.startswith("_")}
            if m.get("role") == "assistant":
                native = m.get("_native") or {}
                if "gemini_signatures" in self.quirks and msg.get("tool_calls"):
                    extra = native.get("tool_extra") or {}
                    calls = []
                    for i, tc in enumerate(msg["tool_calls"]):
                        tc = dict(tc)
                        ex = extra.get(tc["id"])
                        if ex:
                            tc["extra_content"] = ex
                        elif i == 0:          # history without a signature: Google's documented placeholder
                            tc["extra_content"] = {"google": {"thought_signature": "skip_thought_signature_validator"}}
                        calls.append(tc)
                    msg["tool_calls"] = calls
                if "echo_reasoning" in self.quirks and tools_present:
                    msg["reasoning_content"] = native.get("reasoning") or ""
                if "reasoning_details" in self.quirks and native.get("reasoning_details"):
                    msg["reasoning_details"] = native["reasoning_details"]
                if msg.get("content") is None:
                    msg["content"] = ""
            out.append(msg)
        return out

    def body(self, messages, tools, max_tokens, temperature, thinking, tool_choice, schema, stream):
        body = {"model": self.s.remote, "messages": self.convert(messages, bool(tools)), "stream": stream,
                "max_tokens": int(max_tokens)}
        if stream and "no_stream_options" not in self.fixes:
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools
            if tool_choice:
                body["tool_choice"] = tool_choice
        if "no_temperature" not in self.fixes:
            body["temperature"] = float(temperature)
        p = self.s.provider
        if thinking:
            if p == "google":
                body["reasoning_effort"] = self.effort or "high"
            elif p == "openrouter":
                body["reasoning"] = {"effort": self.effort or "high"}
            elif p == "deepseek":
                body["thinking"] = {"type": "enabled"}
                body.pop("temperature", None)
            elif self.effort and p not in ("moonshot",):
                body["reasoning_effort"] = self.effort
            body["max_tokens"] = int(max_tokens + 8000)
        if self.s.plan.get("max_output"):
            body["max_tokens"] = int(min(body["max_tokens"], self.s.plan["max_output"]))
        if schema is not None:
            if "json_object_only" in self.quirks or "json_object_only" in self.fixes:
                body["response_format"] = {"type": "json_object"}
                body["messages"] = body["messages"] + [{"role": "user", "content": (
                    "Reply with JSON only, matching this JSON schema exactly:\n" + json.dumps(schema))}]
            else:
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": "answer", "schema": schema, "strict": False}}
        return body

    def fix(self, body, text):
        low = text.lower()
        if "stream_options" in low and "stream_options" in body:
            self.fixes.add("no_stream_options")
            return {k: v for k, v in body.items() if k != "stream_options"}
        if "response_format" in body and ("response_format" in low or "json_schema" in low):
            self.fixes.add("json_object_only")
            schema = ((body["response_format"].get("json_schema") or {}).get("schema")) or {}
            new = dict(body, response_format={"type": "json_object"})
            new["messages"] = body["messages"] + [{"role": "user", "content": (
                "Reply with JSON only, matching this JSON schema exactly:\n" + json.dumps(schema))}]
            return new
        if "temperature" in low and "temperature" in body:
            self.fixes.add("no_temperature")
            return {k: v for k, v in body.items() if k != "temperature"}
        if "max_tokens" in low and "max_completion_tokens" in low and "max_tokens" in body:
            new = {k: v for k, v in body.items() if k != "max_tokens"}
            new["max_completion_tokens"] = body["max_tokens"]
            return new
        for k in ("reasoning_effort", "reasoning", "thinking"):
            if k in body and k in low:
                return {kk: v for kk, v in body.items() if kk != k}
        return None

    async def stream(self, messages, *, tools=None, max_tokens=2048, temperature=0.4, thinking=False, timeout=1800,
                     tool_choice=None, schema=None):
        body = self.body(messages, tools, max_tokens, temperature, thinking, tool_choice, schema, True)
        calls, usage, finish = {}, {}, None
        content, reasoning, details = [], [], {}
        async with self.request("/chat/completions", body, timeout) as r:
            async for chunk in self.events(r):
                if chunk.get("error"):
                    err = chunk["error"]
                    raise CloudError(f"{self.s.conf.get('name')}: {(err or {}).get('message') if isinstance(err, dict) else err}")
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or []:
                    d = choice.get("delta") or choice.get("message") or {}
                    thought = ((d.get("extra_content") or {}).get("google") or {}).get("thought")
                    r_text = d.get("reasoning_content") or d.get("reasoning")
                    if isinstance(r_text, str) and r_text:
                        reasoning.append(r_text)
                        yield "reasoning", r_text
                    for det in d.get("reasoning_details") or []:
                        slot = details.setdefault(det.get("index", len(details)), {})
                        for k, v in det.items():
                            if k in ("text", "summary", "data") and isinstance(v, str):
                                slot[k] = slot.get(k, "") + v
                            else:
                                slot[k] = v
                    if d.get("content"):
                        if thought:
                            reasoning.append(d["content"])
                            yield "reasoning", d["content"]
                        else:
                            content.append(d["content"])
                            yield "token", d["content"]
                    for tc in d.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", len(calls)), {"id": None, "name": "", "arguments": "",
                                                                              "extra": None})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"] if isinstance(fn["arguments"], str) \
                                else json.dumps(fn["arguments"])
                        if tc.get("extra_content"):
                            slot["extra"] = tc["extra_content"]
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
        tool_calls, extra = [], {}
        for i, slot in sorted(calls.items()):
            cid = slot["id"] or f"call_{i}_{int(time.time() * 1000) % 100000}"
            tool_calls.append({"id": cid, "name": slot["name"], "arguments": _parse_args(slot["arguments"])})
            if slot["extra"]:
                extra[cid] = slot["extra"]
        timings = self.meter(usage.get("prompt_tokens") or usage.get("input_tokens"),
                             usage.get("completion_tokens") or usage.get("output_tokens"))
        native = None
        if tool_calls or details:
            native = {"api": "chat", "tool_extra": extra, "reasoning": "".join(reasoning),
                      "reasoning_details": [details[i] for i in sorted(details)] or None}
        text = "".join(content)
        yield "end", {"tool_calls": tool_calls, "timings": timings, "usage": usage,
                      "finish_reason": "tool_calls" if tool_calls else ("length" if finish == "length" else finish or "stop"),
                      "content": text, "native": native, "json_text": text}
