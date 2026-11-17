#!/usr/bin/env python3
"""Stand-ins for online model providers, for engine tests: OpenAI's Responses API (/openai/v1), Anthropic's Messages
API (/anthropic/v1) and Gemini's OpenAI-compatible chat completions (/gemini). Scripted answers:

  'list files' with tools  → a tool call (list_files) carrying the provider's hidden state (encrypted reasoning,
                              a thinking signature, a thought signature); the follow-up checks that the state came
                              back exactly and answers "<provider> done: ok" (or "…: state missing")
  'fail online'            → HTTP 503 every time (tests falling back to a local model)
  a JSON schema            → a minimal valid object
  anything else            → "Hello from <provider>"

Every request is kept at GET /_requests. Run: python fake_cloud_server.py --port N"""
import argparse
import json

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
REQUESTS = []


def minimal(schema):
    if "oneOf" in schema:
        return minimal(schema["oneOf"][0])
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    t = schema.get("type")
    if t == "object":
        out = {k: minimal(schema["properties"][k]) for k in schema.get("required", [])}
        if "met" in out and "reason" in out:      # the strict check of a step's result (2.5.0): it passes
            out.update(reason="complete", met=True)
        return out
    if t == "array":
        return [minimal(schema["items"])] * int(schema.get("minItems", 0))
    if t == "string":
        return "x" * max(schema.get("minLength", 1), 1)
    if t in ("integer", "number"):
        return 0
    if t == "boolean":
        return False
    return None


def sse(events):
    async def gen():
        for e in events:
            if isinstance(e, tuple):
                yield f"event: {e[0]}\ndata: {json.dumps(e[1])}\n\n"
            else:
                yield f"data: {json.dumps(e)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


def failing():
    return JSONResponse({"error": {"message": "overloaded"}}, 503, headers={"retry-after": "0"})


def all_text(obj):
    return json.dumps(obj)


@app.get("/_requests")
async def requests_():
    return REQUESTS


# ------------------------------------------------------------------------------------------------ OpenAI Responses
@app.get("/openai/v1/models")
async def openai_models(request: Request):
    if request.headers.get("authorization") != "Bearer sk-test":
        return JSONResponse({"error": {"message": "Incorrect API key"}}, 401)
    return {"data": [{"id": "gpt-test-pro"}, {"id": "gpt-test-mini"}, {"id": "text-embedding-3-small"}]}


@app.post("/openai/v1/responses")
async def openai_responses(request: Request):
    body = await request.json()
    REQUESTS.append({"provider": "openai", "body": body, "headers": dict(request.headers)})
    if "fail online" in all_text(body["input"]):
        return failing()
    items = body["input"]
    usage = {"input_tokens": 1000, "output_tokens": 200}

    def message(text):
        return [("response.output_text.delta", {"type": "response.output_text.delta", "delta": text}),
                ("response.output_item.done", {"type": "response.output_item.done", "item": {
                    "type": "message", "id": "msg_1", "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}}),
                ("response.completed", {"type": "response.completed", "response": {"status": "completed",
                                        "usage": usage, "output": [{"type": "message", "id": "msg_1", "role": "assistant",
                                        "content": [{"type": "output_text", "text": text}]}]}})]
    if body.get("text", {}).get("format", {}).get("type") == "json_schema":
        return sse(message(json.dumps(minimal(body["text"]["format"]["schema"]))))
    outputs = [i for i in items if i.get("type") == "function_call_output"]
    if outputs:
        rs = [i for i in items if i.get("type") == "reasoning"]
        fc = [i for i in items if i.get("type") == "function_call"]
        ok = (rs and rs[0].get("encrypted_content") == "enc-1" and fc and "id" not in fc[0]
              and items.index(rs[0]) < items.index(fc[0]) and body.get("store") is False)
        return sse(message("OpenAI done: " + ("ok" if ok else "state missing")))
    if body.get("tools") and "list files" in all_text(items):
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "Plan: list."}],
                     "encrypted_content": "enc-1"}
        call = {"type": "function_call", "id": "fc_1", "call_id": "call_o1", "name": "list_files",
                "arguments": json.dumps({"path": "."}), "status": "completed"}
        return sse([("response.reasoning_summary_text.delta", {"type": "response.reasoning_summary_text.delta",
                                                               "delta": "Plan: list."}),
                    ("response.output_item.done", {"type": "response.output_item.done", "item": reasoning}),
                    ("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta",
                                                                "delta": "{\"path\""}),
                    ("response.output_item.done", {"type": "response.output_item.done", "item": call}),
                    ("response.completed", {"type": "response.completed", "response": {
                        "status": "completed", "usage": usage, "output": [reasoning, call]}})])
    return sse(message("Hello from OpenAI"))


# ------------------------------------------------------------------------------------------------ Anthropic Messages
ANTHROPIC_MODELS = [
    {"id": "claude-test-opus", "display_name": "Claude Test Opus", "line": "opus", "max_input_tokens": 200000,
     "max_tokens": 64000, "lifecycle": "active", "capabilities": {
         "image_input": {"supported": True}, "structured_outputs": {"supported": True},
         "thinking": {"supported": True, "types": {"adaptive": {"supported": True}, "enabled": {"supported": False},
                                                   "disabled": {"supported": True}}},
         "effort": {"supported": True, "low": {"supported": True}, "medium": {"supported": True},
                    "high": {"supported": True}, "max": {"supported": True}}}},
    {"id": "claude-test-haiku", "display_name": "Claude Test Haiku", "line": "haiku", "max_input_tokens": 200000,
     "max_tokens": 32000, "lifecycle": "active", "capabilities": {
         "image_input": {"supported": True},
         "thinking": {"supported": True, "types": {"adaptive": {"supported": False}, "enabled": {"supported": True}}}}},
]


@app.get("/anthropic/v1/models")
async def anthropic_models(request: Request):
    if request.headers.get("x-api-key") != "sk-ant-test" or not request.headers.get("anthropic-version"):
        return JSONResponse({"type": "error", "error": {"message": "invalid x-api-key"}}, 401)
    return {"data": ANTHROPIC_MODELS, "has_more": False, "last_id": "claude-test-haiku"}


@app.post("/anthropic/v1/messages")
async def anthropic_messages(request: Request):
    body = await request.json()
    REQUESTS.append({"provider": "anthropic", "body": body, "headers": dict(request.headers)})
    if "fail online" in all_text(body["messages"]):
        return failing()
    usage_start = {"type": "message_start", "message": {"usage": {"input_tokens": 900, "cache_read_input_tokens": 100}}}

    def text(t):
        return [usage_start,
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 150}},
                {"type": "message_stop"}]
    choice = body.get("tool_choice") or {}
    if choice.get("type") == "tool" and choice.get("name") == "answer":
        schema = next(t["input_schema"] for t in body["tools"] if t["name"] == "answer")
        return sse([usage_start,
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_a",
                                                                                 "name": "answer", "input": {}}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta",
                                                                         "partial_json": json.dumps(minimal(schema))}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 40}}])
    msgs = body["messages"]
    last = msgs[-1]
    results = [b for b in last["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    if results:
        prev = msgs[-2]["content"]
        ok = (prev[0].get("type") == "thinking" and prev[0].get("signature") == "sig-1"
              and any(b.get("type") == "tool_use" and b.get("id") == results[0]["tool_use_id"] for b in prev)
              and body.get("thinking") == {"type": "adaptive"})
        return sse(text("Claude done: " + ("ok" if ok else "state missing")))
    if body.get("tools") and "list files" in all_text(msgs):
        return sse([usage_start,
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "I should list."}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig-1"}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1",
                                                                                 "name": "list_files", "input": {}}},
                    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta",
                                                                         "partial_json": "{\"path\": \".\"}"}},
                    {"type": "content_block_stop", "index": 1},
                    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 120}}])
    return sse(text("Hello from Claude"))


# ------------------------------------------------------------------------------------------------ Gemini (chat)
@app.get("/gemini/models")
async def gemini_models(request: Request):
    if request.headers.get("authorization") != "Bearer gm-test":
        return JSONResponse([{"error": {"message": "API key not valid"}}], 400)
    return {"object": "list", "data": [{"id": "models/gemini-test-pro"}, {"id": "models/gemini-test-flash"},
                                       {"id": "models/text-embedding-004"}]}


@app.post("/gemini/chat/completions")
async def gemini_chat(request: Request):
    body = await request.json()
    REQUESTS.append({"provider": "gemini", "body": body, "headers": dict(request.headers)})
    msgs = body["messages"]
    if "fail online" in all_text(msgs):
        return failing()
    usage = {"prompt_tokens": 800, "completion_tokens": 100}

    def text(t):
        return [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": t}}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": usage}]
    if (body.get("response_format") or {}).get("type") == "json_schema":
        return sse(text(json.dumps(minimal(body["response_format"]["json_schema"]["schema"]))))
    if msgs[-1]["role"] == "tool":
        asst = msgs[-2]
        sig = ((asst["tool_calls"][0].get("extra_content") or {}).get("google") or {}).get("thought_signature")
        return sse(text("Gemini done: " + ("ok" if sig == "gsig-1" else "state missing")))
    if body.get("tools") and "list files" in all_text(msgs):
        return sse([{"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{
                        "index": 0, "id": "gcall_1", "type": "function",
                        "function": {"name": "list_files", "arguments": "{\"path\": \".\"}"},
                        "extra_content": {"google": {"thought_signature": "gsig-1"}}}]}}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                    {"choices": [], "usage": usage}])
    return sse(text("Hello from Gemini"))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    uvicorn.run(app, host="127.0.0.1", port=p.parse_args().port, log_level="warning")
