#!/usr/bin/env python3
"""A stand-in for llama-server for engine tests: same command-line flags and OpenAI-style endpoints, scripted
answers. Tool calls: if tools are offered and the last user message contains 'list files', it calls list_files
once; 'write' → write_file; then answers. JSON-schema requests get a minimal valid object.

Planner (2.4.0): a plan request answers 'team' when the conversation says "as a team" (or when 'direct' is not
allowed) with two tasks, t2 depending on t1; a checkpoint asks t1 to fix its work once when the request says
"retry-me", and answers agents' questions; agents call ask_lead when their task says "ask the planner"."""
import argparse
import json
import sys
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

p = argparse.ArgumentParser()
p.add_argument("-m")
p.add_argument("--port", type=int)
p.add_argument("--host")
p.add_argument("--api-key")
p.add_argument("--alias")
p.add_argument("--list-devices", action="store_true")
args, _ = p.parse_known_args()
if args.list_devices:
    print("Available devices:\n  Vulkan0: Fake GPU (8192 MiB, 8000 MiB free)")
    sys.exit(0)
app = FastAPI()


def minimal(schema):
    if "oneOf" in schema:
        return minimal(schema["oneOf"][0])
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    t = schema.get("type")
    if t == "object":
        out = {}
        for k in schema.get("required", []):
            out[k] = minimal(schema["properties"][k])
        if "memories" in schema.get("properties", {}):
            out["memories"] = [{"scope": "global", "kind": "preference", "text": "Prefers short answers with tables."}]
        if "lessons" in schema.get("properties", {}):
            out["lessons"] = ["Narrow subjects find downloadable CSV files more often than broad ones."]
        if "steps" in schema.get("properties", {}):
            out["steps"] = [{"title": "Look at the folder", "detail": ""}, {"title": "Report", "detail": ""}]
        return out
    if t == "array":
        return []
    if t == "string":
        return "fake summary of the earlier conversation" if schema.get("maxLength", 0) > 1000 else "x" * max(schema.get("minLength", 1), 1)
    if t in ("integer", "number"):
        return 0
    if t == "boolean":
        return False
    return None


def text_of(content):
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if p.get("type") == "text") + \
            f" [{sum(p.get('type') == 'image_url' for p in content)} images]"
    return content or ""


def decide(body):
    msgs = body["messages"]
    tools = {t["function"]["name"] for t in body.get("tools") or []}
    last_user = next((m for m in reversed(msgs) if m["role"] == "user"), {"content": ""})
    after_tool = msgs and msgs[-1]["role"] == "tool"
    text = text_of(last_user.get("content"))
    if "show system prompt" in text and not after_tool:        # tests read what reached the prompt
        return {"text": msgs[0]["content"][:8000].replace("\n", " ")}
    for trigger, tool, key in (("search earlier chats for ", "search_chats", "query"),
                               ("search knowledge for ", "search_knowledge", "query"),
                               ("note decision: ", "update_project_notes", "text")):
        if trigger in text and tool in tools and not after_tool:
            args = {key: text.split(trigger, 1)[1].strip()}
            if tool == "update_project_notes":
                args["section"] = "Decisions"
            return {"tool": (tool, args)}
    if after_tool and msgs[-1].get("content", "").startswith(("- chat_id", "[", "Added to the project notes",
                                                              "No earlier chat")):
        return {"text": "Found: " + msgs[-1]["content"][:600].replace("\n", " ")}
    users = " ".join(text_of(m.get("content")) for m in msgs if m["role"] == "user")
    if "images]" in text and "[0 images]" not in text:
        return {"text": "I can see " + text.split("[")[-1].rstrip("]")}
    if tools and "keep looping" in users and "list_files" in tools:   # a model stuck in a loop
        return {"tool": ("list_files", {"path": "."})}
    if "slow" in users and not tools:
        return {"text": " ".join(["word"] * 25), "delay": 0.12}
    if "slowly" in text and tools and not after_tool and "list_files" in tools:
        return {"tool": ("list_files", {"path": "."}), "delay": 1.5}
    if "Message from me while you were working" in text:
        return {"text": "Noted your new message: " + text.split(":)")[-1].strip()[:60]}
    if tools and not after_tool:
        if "list files" in text and "list_files" in tools:
            return {"tool": ("list_files", {"path": "."})}
        if "write" in text and "write_file" in tools:
            return {"tool": ("write_file", {"path": "note.txt", "content": "hello"})}
        if "ask the planner" in text.split("Your task", 1)[-1].split("Done when")[0] and "ask_lead" in tools:
            return {"tool": ("ask_lead", {"question": "Which language?"})}
        if "whoami" in text:
            name = next((n for n in tools if n.endswith("whoami")), None)
            if name:
                return {"tool": (name, {})}
    if after_tool:
        return {"text": f"Done. The tool said: {msgs[-1]['content'][:60]}"}
    return {"text": "Hello from the fake model. " + ("I remember: " + msgs[0]["content"][-80:] if "remembered" in msgs[0]["content"] else "")}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path.startswith("/v1") and request.headers.get("authorization") != f"Bearer {args.api_key}":
        return JSONResponse({"error": "unauthorised"}, 401)
    return await call_next(request)


@app.get("/v1/models")
async def models():
    return {"data": [{"id": args.alias, "aliases": [args.alias]}]}


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    """Word-hash vectors: texts that share words point the same way (enough to test search by meaning)."""
    import hashlib
    import math
    import re
    body = await request.json()
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    data = []
    for i, t in enumerate(inputs):
        v = [0.0] * 64
        for w in re.findall(r"\w{3,}", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        data.append({"index": i, "embedding": [x / n for x in v]})
    return {"data": data, "model": "embedding"}


TIMINGS = {"prompt_n": 50, "prompt_ms": 100.0, "predicted_n": 10, "predicted_ms": 200.0,
           "prompt_per_second": 500.0, "predicted_per_second": 50.0, "cache_n": 0}


def structured(schema, body):
    """Planner answers for the orchestrator; everything else gets a minimal valid object."""
    props = schema.get("properties", {})
    users = " ".join(text_of(m.get("content")) for m in body["messages"] if m["role"] == "user").lower()
    if "approach" in props and "tasks" in props:
        allowed = props["approach"]["enum"]
        if "team" in allowed and ("direct" not in allowed or "as a team" in users):
            agents = props["tasks"]["items"]["properties"].get("agent", {}).get("enum")
            return {"approach": "team", "reason": "two steps", "final": "both parts", "tasks": [
                {"id": "t1", "name": "Collect", "goal": "say hello" + (" and ask the planner" if "ask the planner" in users else ""),
                 "done_when": "a greeting", "agent": (agents or ["Collector"])[0], "role": "worker", "harness": "agent"},
                {"id": "t2", "name": "Summarise", "goal": "summarise t1", "done_when": "one line",
                 "depends_on": ["t1"], "agent": (agents or ["Collector", "Writer"])[-1], "role": "worker", "harness": "agent"}]}
        return {"approach": "direct", "tasks": []}
    if "actions" in props:
        last = text_of(body["messages"][-1].get("content"))
        actions = []
        if "retry-me" in users and "### t1" in last and "(attempt 1)" in last:
            actions.append({"do": "retry", "task": "t1", "text": "add a second greeting"})
        if "Question from t1" in last:
            actions.append({"do": "answer", "task": "t1", "text": "use English"})
        return {"actions": actions}
    return minimal(schema)


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    rf = body.get("response_format")
    if rf:
        content = json.dumps(structured(rf["json_schema"]["schema"], body))
        return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "timings": TIMINGS}
    d = decide(body)
    if d.get("delay") and "tool" in d:
        time.sleep(d["delay"])
    if not body.get("stream"):
        msg = {"role": "assistant", "content": d.get("text", "")}
        if "tool" in d:
            msg["tool_calls"] = [{"id": "call_1", "type": "function", "function": {"name": d["tool"][0], "arguments": json.dumps(d["tool"][1])}}]
        return {"choices": [{"message": msg, "finish_reason": "stop"}], "timings": TIMINGS}

    def gen():
        def chunk(delta, finish=None):
            return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"
        yield chunk({"role": "assistant", "content": None})
        if body.get("chat_template_kwargs", {}).get("enable_thinking"):
            yield chunk({"reasoning_content": "thinking…"})
        if "tool" in d:
            name, a = d["tool"]
            yield chunk({"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": name, "arguments": ""}}]})
            yield chunk({"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(a)}}]})
            yield chunk({}, "tool_calls")
        else:
            for word in d["text"].split(" "):
                yield chunk({"content": word + " "})
                time.sleep(d.get("delay") or 0.005)
            yield chunk({}, "stop")
        yield "data: " + json.dumps({"choices": [], "timings": TIMINGS, "usage": {}}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
