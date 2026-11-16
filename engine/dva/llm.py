"""Talking to the local llama-server (OpenAI-compatible API): streaming replies, native tool calls, JSON-schema
answers (grammar-constrained, so always valid), reasoning ('thinking') split from the answer, and timings.

Every local call is streamed, also the ones that return one object (2.5.0). A call that is not streamed sends
nothing until the whole answer exists, so a slow computer looked exactly like a stuck one and the call died with
a read timeout. Streaming lets a watchdog tell them apart:
  - first-token limit: how long the model may read the prompt before its first token. It grows with the size of
    the prompt and uses the reading speed measured on this server.
  - stall limit: the longest silence allowed between two tokens once it writes.
A call that breaks either limit raises LLMTimeout; a server that cannot be reached raises LLMUnreachable; a prompt
that does not fit raises LLMContextFull. The caller (runner.Turn) restarts the model and tries again."""
import asyncio
import json

import httpx
from jsonschema import ValidationError, validate


class LLMError(RuntimeError):
    pass


class LLMTimeout(LLMError):
    """The model sent nothing for too long (stage: 'first' = before its first token, 'stall' = between tokens)."""
    def __init__(self, message, stage="stall"):
        super().__init__(message)
        self.stage = stage


class LLMUnreachable(LLMError):
    """The model server closed the connection or cannot be reached (it crashed or was stopped)."""


class LLMContextFull(LLMError):
    """The prompt is larger than the context the model was loaded with."""


def is_recoverable(exc):
    """Errors that a restart of the model server can fix."""
    return isinstance(exc, (LLMTimeout, LLMUnreachable))


class LLM:
    """Calls go to the model server leased by the current task (see Runtime.lease), so several agents can use
    different models at the same time. Pass server= to choose one explicitly. An online model's lease (cloud.py)
    answers through its own adapter with the same results."""
    def __init__(self, runtime):
        self.runtime = runtime

    def _pick(self, server):
        server = server or self.runtime.active()
        if server is None:
            raise LLMError("No model is loaded")
        return server

    def _server(self, server):
        server = self._pick(server)
        return server.base_url, server.headers

    @staticmethod
    def _local(messages):
        """Engine-only keys ('_native' state of online models, '_id'…) never go to llama-server."""
        return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]

    def _body(self, messages, *, tools=None, schema=None, max_tokens=1024, temperature=0.4, thinking=False,
              stream=False, tool_choice=None, stop=None):
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": stream,
                "cache_prompt": True, "chat_template_kwargs": {"enable_thinking": bool(thinking)}}
        if stream:
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools
            if tool_choice:
                body["tool_choice"] = tool_choice
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema,
                                                                              "strict": True}}
        if stop:
            body["stop"] = stop
        return body

    # ------------------------------------------------------------------ the watchdog's limits
    def limits(self, server, body):
        """(seconds before the first token, seconds of silence between tokens) for this call."""
        setting = self.runtime.db.setting
        stall = float(setting("stall_timeout_seconds", 180) or 180)
        fixed = float(setting("first_token_timeout_seconds", 0) or 0)
        if fixed > 0:
            return fixed, stall
        tokens = len(json.dumps(body, ensure_ascii=False)) / 3.6
        # half the reading speed measured on this server (or its benchmark); with no measurement at all,
        # assume a slow laptop CPU
        reads = max(1.0, float(getattr(server, "read_tps", 0) or 0) * 0.5) if getattr(server, "read_tps", 0) else 2.0
        queue = max(1, int(getattr(server, "users", 1) or 1))       # other replies may be ahead on this server
        return min(3600.0, (120.0 + tokens / reads) * queue), stall

    @staticmethod
    def _learn(server, timings):
        """Remember how fast this server reads and writes (used by the watchdog and the forecaster)."""
        for key, attr in (("prompt_per_second", "read_tps"), ("predicted_per_second", "write_tps")):
            value = float(timings.get(key) or 0)
            n = int(timings.get("prompt_n" if attr == "read_tps" else "predicted_n") or 0)
            if value > 0 and n >= 8:                     # a handful of tokens says nothing about speed
                old = float(getattr(server, attr, 0) or 0)
                setattr(server, attr, value if not old else old * 0.6 + value * 0.4)

    async def _events(self, server, body):
        """Stream one local call. Yields ('reasoning', text), ('token', text) and finally ('end', {...})."""
        first, stall = self.limits(server, body)
        calls = {}
        timings, usage, finish = {}, {}, None
        content, reasoning = [], []
        started = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10), trust_env=False) as http:
                request = http.build_request("POST", server.base_url + "/v1/chat/completions", json=body,
                                             headers=server.headers)
                try:        # llama-server answers (even with its headers) only once it has read the prompt
                    r = await asyncio.wait_for(http.send(request, stream=True), first)
                except asyncio.TimeoutError:
                    raise LLMTimeout(f"the model did not start answering within {first:.0f} seconds",
                                     "first") from None
                try:
                    if r.status_code != 200:
                        text = (await r.aread()).decode("utf-8", errors="replace")
                        low = text.lower()
                        if r.status_code in (400, 413) and "context" in low and (
                                "exceed" in low or "too long" in low or "too large" in low or "available" in low):
                            raise LLMContextFull(f"the prompt does not fit the model's context: {text[:200]}")
                        raise LLMError(f"model server HTTP {r.status_code}: {text[:300]}")
                    if "text/event-stream" not in r.headers.get("content-type", ""):
                        # a server that answers in one piece: read it whole (still under the first-token limit)
                        try:
                            raw = await asyncio.wait_for(r.aread(), first)
                        except asyncio.TimeoutError:
                            raise LLMTimeout(f"the model sent nothing for {first:.0f} seconds", "first") from None
                        data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                        choice = (data.get("choices") or [{}])[0]
                        msg = choice.get("message") or {}
                        if msg.get("reasoning_content"):
                            yield "reasoning", msg["reasoning_content"]
                        if msg.get("content"):
                            yield "token", msg["content"]
                        tool_calls = []
                        for i, tc in enumerate(msg.get("tool_calls") or []):
                            fn = tc.get("function") or {}
                            try:
                                args = json.loads(fn.get("arguments") or "{}")
                            except json.JSONDecodeError:
                                args = {"_unparsed": fn.get("arguments")}
                            tool_calls.append({"id": tc.get("id") or f"call_{i}", "name": fn.get("name") or "",
                                               "arguments": args})
                        self._learn(server, data.get("timings") or {})
                        yield "end", {"tool_calls": tool_calls, "timings": data.get("timings") or {},
                                      "usage": data.get("usage") or {}, "finish_reason": choice.get("finish_reason"),
                                      "content": msg.get("content") or "", "reasoning": msg.get("reasoning_content") or ""}
                        return
                    lines = r.aiter_lines().__aiter__()
                    while True:
                        limit = stall if started else first
                        try:
                            line = await asyncio.wait_for(lines.__anext__(), limit)
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            if started:
                                raise LLMTimeout(f"the model stopped writing (no token for {limit:.0f} seconds)",
                                                 "stall") from None
                            raise LLMTimeout(f"the model did not start answering within {limit:.0f} seconds",
                                             "first") from None
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            raise LLMError(str(chunk["error"])[:300])
                        started = True
                        timings = chunk.get("timings") or timings
                        usage = chunk.get("usage") or usage
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if delta.get("reasoning_content"):
                                reasoning.append(delta["reasoning_content"])
                                yield "reasoning", delta["reasoning_content"]
                            if delta.get("content"):
                                content.append(delta["content"])
                                yield "token", delta["content"]
                            for tc in delta.get("tool_calls") or []:
                                slot = calls.setdefault(tc.get("index", len(calls)),
                                                        {"id": tc.get("id"), "name": "", "arguments": ""})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] += fn["name"]
                                if fn.get("arguments"):
                                    slot["arguments"] += fn["arguments"]
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
                finally:
                    await r.aclose()
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"the model server did not respond ({type(exc).__name__})",
                             "stall" if started else "first") from exc
        except httpx.HTTPError as exc:
            raise LLMUnreachable(f"the model server is not reachable ({type(exc).__name__})") from exc
        tool_calls = []
        for i, slot in sorted(calls.items()):
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": slot["arguments"]}
            tool_calls.append({"id": slot["id"] or f"call_{i}", "name": slot["name"], "arguments": args})
        self._learn(server, timings)
        yield "end", {"tool_calls": tool_calls, "timings": timings, "usage": usage, "finish_reason": finish,
                      "content": "".join(content), "reasoning": "".join(reasoning)}

    # ------------------------------------------------------------------ calls
    async def complete(self, messages, *, schema=None, tools=None, max_tokens=1024, temperature=0.2,
                       thinking=False, timeout=900, server=None):
        """One call, returned whole. With a schema, returns the parsed object in result['json'] (or raises).
        Local models are streamed underneath, so the watchdog sees a stuck model (timeout applies to online
        models, which have their own adapters)."""
        picked = self._pick(server)
        if getattr(picked, "kind", None) == "cloud":
            return await picked.adapter.complete(messages, schema=schema, tools=tools, max_tokens=max_tokens,
                                                 temperature=temperature, thinking=thinking, timeout=timeout)
        body = self._body(self._local(messages), tools=tools, schema=schema, max_tokens=max_tokens,
                          temperature=temperature, thinking=thinking, stream=True)
        end = {}
        async for kind, value in self._events(picked, body):
            if kind == "end":
                end = value
        out = {"content": end.get("content") or "", "reasoning": end.get("reasoning") or "",
               "tool_calls": [{"id": c["id"], "type": "function",
                               "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                              for c in end.get("tool_calls") or []],
               "timings": end.get("timings") or {}, "usage": end.get("usage") or {},
               "finish_reason": end.get("finish_reason")}
        if schema is not None:
            text = out["content"].strip()
            try:
                value = json.loads(text)
                validate(value, schema)
            except (json.JSONDecodeError, ValidationError) as exc:
                raise LLMError(f"model returned invalid JSON ({str(exc)[:160]})") from exc
            out["json"] = value
        return out

    async def json(self, messages, schema, *, max_tokens=512, retries=1, temperature=0.1, server=None):
        last = None
        for _ in range(retries + 1):
            try:
                return (await self.complete(messages, schema=schema, max_tokens=max_tokens,
                                            temperature=temperature, server=server))["json"]
            except LLMError as exc:
                if is_recoverable(exc) or isinstance(exc, LLMContextFull):
                    raise                     # asking again does not help: the caller restarts or shrinks
                last = exc
        raise last

    async def stream(self, messages, *, tools=None, max_tokens=2048, temperature=0.4, thinking=False, timeout=1800,
                     tool_choice=None, server=None):
        """Yields ('reasoning', text), ('token', text), then ('end', {...tool_calls, timings, finish_reason})."""
        picked = self._pick(server)
        if getattr(picked, "kind", None) == "cloud":
            async for item in picked.adapter.stream(messages, tools=tools, max_tokens=max_tokens,
                                                    temperature=temperature, thinking=thinking, timeout=timeout,
                                                    tool_choice=tool_choice):
                yield item
            return
        body = self._body(self._local(messages), tools=tools, max_tokens=max_tokens, temperature=temperature,
                          thinking=thinking, stream=True, tool_choice=tool_choice)
        async for item in self._events(picked, body):
            yield item

    async def count_tokens(self, text, server=None):
        """Exact token count from the loaded model's tokenizer; None when it cannot be asked."""
        try:
            picked = self._pick(server)
        except LLMError:
            return None
        if getattr(picked, "kind", None) == "cloud" or not text:
            return None
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as http:
                r = await http.post(picked.base_url + "/tokenize", json={"content": text}, headers=picked.headers)
            if r.status_code != 200:
                return None
            tokens = r.json().get("tokens")
            return len(tokens) if isinstance(tokens, list) else None
        except (httpx.HTTPError, ValueError):
            return None


def estimate_tokens(text):
    """Rough token count without a tokenizer (good enough for budgeting)."""
    return int(len(text or "") / 3.6) + 4
