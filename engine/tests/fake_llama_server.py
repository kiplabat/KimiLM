#!/usr/bin/env python3
"""A stand-in for llama-server for engine tests: same command-line flags and OpenAI-style endpoints, scripted
answers. Tool calls: if tools are offered and the last user message contains 'list files', it calls list_files
once; 'write' → write_file; then answers. JSON-schema requests get a minimal valid object.

Planner (2.4.0): a plan request answers 'team' when the conversation says "as a team" (or when 'direct' is not
allowed) with two tasks, t2 depending on t1; a checkpoint asks t1 to fix its work once when the request says
"retry-me", and answers agents' questions; agents call ask_lead when their task says "ask the planner".

Sequenced runs (2.5.0): "forgetful plan" gives three steps without depends_on (t2 only names t1 in its goal), like a
small planner does; "explode" in a step's goal makes its agent fail with a server error; the result check
('met') fails once for goals with "retry-me" and always for goals with "never-pass"; "hang-once" makes the
server send nothing for a long time, once per model file (a file beside the model remembers it, because the
engine restarts this process); /tokenize counts four characters per token.

Long-running tasks (2.9.0): a "next move" request (schema with decision + steps) is scripted from the number of
steps done, which the prompt states: three steps Survey → Draft → Polish, then finish. Words in the request
change the script: "hits a blocker" (the first agent reports one, the planner changes the plan), "needs my
answer" (the planner asks after the first step), "planner breaks" (the second move fails with a server error),
"note-step" (the first step writes a file), "slow-steps". An agent whose brief asks for a handoff ends its reply
with one. A set-up request (schema with team + limits) answers with values out of range, to be repaired."""
import argparse
import json
import re
import sys
import time
from pathlib import Path

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
p.add_argument("-ngl", default="auto")
p.add_argument("--device", "-dev")
p.add_argument("-t", type=int, default=4)
p.add_argument("-tb")      # declared so "-tb 12" is not read as "-t b"
p.add_argument("--spec-type", default="none")
p.add_argument("--slot-save-path")
p.add_argument("--cache-ram", default="8192")
# The engine's random API key can start with "-" (about 1 start in 64); argparse would then read it as a flag
# and exit, which looked like a rare random test failure. Pass it as --api-key=VALUE. llama-server is not affected.
argv = sys.argv[1:]
for i, a in enumerate(argv[:-1]):
    if a == "--api-key":
        argv[i:i + 2] = [f"--api-key={argv[i + 1]}"]
        break
args, _ = p.parse_known_args(argv)
if not args.list_devices and args.device != "none":     # report placement like llama.cpp does (read by the runtime)
    on_gpu = 33 if args.ngl in ("auto", "all") else min(33, int(args.ngl))
    print(f"load_tensors: offloaded {on_gpu}/33 layers to GPU", flush=True)
    print(f"load_tensors:   CPU_Mapped model buffer size = {(33 - on_gpu) * 10 + 5}.00 MiB", flush=True)
    print(f"load_tensors:      Vulkan0 model buffer size = {on_gpu * 10}.00 MiB", flush=True)
    print("llama_kv_cache:    Vulkan0 KV buffer size =   64.00 MiB", flush=True)
if args.list_devices:
    import os
    print("Available devices:\n" + os.environ.get("FAKE_DEVICES", "  Vulkan0: Fake GPU (8192 MiB, 8000 MiB free)"))
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
        return {"text": (msgs[0]["content"][:8000] + " || " + text[:6000]).replace("\n", " ")}
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
        return {"text": f"Done. The tool said: {msgs[-1]['content'][:60]}" + handoff(text)}
    # memory chosen for the request arrives in a <context> block on the latest message (2.4.2)
    ctx = text.split("</context>")[0] if "<context>" in text else ""
    return {"text": "Hello from the fake model. " + ("I remember: " + ctx[-80:] if "remembered" in ctx else "")
            + handoff(text)}


def step_goal(last, said=None):
    """The agent's own task in a brief of a planned run ("Your task …") or a long-running task ("Your step …").
    In a later round of the same step the last message is the planner's objection: all of it counts then."""
    for mark in ("Your task", "Your step"):
        if mark in (said if said is not None else last):
            return last.split("Done when")[0].split(mark)[-1]
    return ""


def handoff(text):
    """Agents of a long-running task end with a handoff block (their brief asks for it)."""
    m = re.search(r"Your step \((s\d+) “(.+?)”\)", text)
    if not m or "## Handoff" not in text:
        return ""
    found = "blocker: the site is down" if "report a blocker" in step_goal(text) else "nothing new"
    return (f"\n\n## Handoff\nDone: finished {m.group(2)} with a greeting\nFound: {found}\n"
            f"Next: do the next part after {m.group(2)}")


def mission_move(body, users):
    """The planner of a long-running task: what happens after `n` steps."""
    last = text_of(body["messages"][-1].get("content"))
    n = int((re.search(r"Steps done so far: (\d+)", last) or [0, 0])[1])
    slow = " slowly" if "slow-steps" in users else ""
    move = {"understanding": f"The task is a relay of greetings; {n} steps are done so far.",
            "reason": f"after {n} steps", "decision": "continue", "steps": [],
            "outline": ["Survey", "Draft", "Polish"]}
    step = lambda name, goal, **kw: {"name": name, "goal": goal, "done_when": "a greeting", **kw}  # noqa: E731
    if "hits a blocker" in users:
        if n == 0:
            move["steps"] = [step("Fetch", "say hello and report a blocker")]
        elif n == 1 and "blocker: the site is down" in last:
            move.update(reason="the site is down, so use the saved copy", changed=True,
                        outline=["Use the saved copy instead", "Report"],
                        steps=[step("Workaround", "say hello from the saved copy", agent="Archivist", harness="agent")])
        else:
            move["decision"] = "finish"
        return move
    if "needs my answer" in users:
        if n == 0:
            move["steps"] = [step("Prepare", "say hello before painting")]
        elif n == 1 and "My latest message to you:" not in last:
            move.update(decision="ask", question="Which colour should it be?")
        elif n == 1:
            colour = last.split("My latest message to you:", 1)[1].split("\n")[0].strip()
            move["steps"] = [step(f"Paint {colour}"[:40], f"say hello in {colour}")]
        else:
            move["decision"] = "finish"
        return move
    if "planner breaks" in users and n == 1:
        return None
    if "note-step" in users and n == 0:
        move["steps"] = [step("Note", "write a note for the user")]
        return move
    plan = [("Survey", "say hello: survey the notes"), ("Draft", "say hello: draft from the survey" + slow),
            ("Polish", "say hello: polish the draft")]
    if "note-step" in users or "planner breaks" in users:
        plan = plan[:2]
    if n < len(plan):
        move["steps"] = [step(*plan[n])]
    else:
        move["decision"] = "finish"
    return move


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
CACHED = []     # recent prompts (llama-server keeps several in RAM with --cache-ram and picks the best match)


def timings(body):
    """Like llama-server: speed depends on threads (best at 6 here), CPU vs GPU, and speculative decoding on
    repetitive output; a prompt cache reuses the longest common prefix of the rendered prompt (system, then
    tools, then the conversation — the order of Qwen's chat template)."""
    msgs = body.get("messages") or []
    system = msgs[0] if msgs and msgs[0]["role"] == "system" else None
    rendered = (text_of(system.get("content")) if system else "") + json.dumps(body.get("tools") or []) + \
        "".join(m["role"] + text_of(m.get("content")) for m in msgs if m is not system)
    common = 0
    for prev in CACHED:
        n = 0
        for a, b in zip(prev, rendered):
            if a != b:
                break
            n += 1
        common = max(common, n)
    CACHED.insert(0, rendered)
    del CACHED[8:]
    writes = 50.0 - abs(args.t - 6) * 2.5 + (5.0 if args.device == "none" else 0.0)
    last = text_of(msgs[-1].get("content")) if msgs else ""
    if args.spec_type != "none" and "Copy this list exactly" in last:
        writes *= 2
    return {**TIMINGS, "cache_n": common // 4, "prompt_n": max(1, (len(rendered) - common) // 4),
            "predicted_per_second": writes}


def structured(schema, body):
    """Planner answers for the orchestrator; everything else gets a minimal valid object."""
    props = schema.get("properties", {})
    users = " ".join(text_of(m.get("content")) for m in body["messages"] if m["role"] == "user").lower()
    if "decision" in props and "steps" in props:      # a long-running task's next move (2.9.0)
        return mission_move(body, users)
    if "team" in props and "limits" in props:         # the set-up of a task, with values to be repaired
        harnesses = props["team"]["items"]["properties"]["harness"]["enum"]
        out = {"name": "Greeting study", "goal": "Collect greetings and write them up, without asking anything.",
               "done_when": "a report of the greetings exists", "kind": "reply" if "single-reply-task" in users else "mission",
               "team": [{"name": "Researcher", "harness": "agent", "role": "worker", "about": "finds greetings"},
                        {"name": "Writer", "harness": "writer" if "writer" in harnesses else "agent",
                         "about": "writes the report"},
                        {"name": "researcher", "harness": "agent", "about": "a duplicate to be dropped"}],
               "limits": {"hours": 5000, "steps": 0, "step_minutes": 1}, "allow": ["files", "files"],
               "outline": ["Collect", "Write"]}
        if "when" in props:
            out["when"] = "every day at 07:30"
        return out
    if "approach" in props and "tasks" in props:
        allowed = props["approach"]["enum"]
        if "team" in allowed and "forgetful plan" in users:
            return {"approach": "team", "reason": "three steps", "final": "all parts", "tasks": [
                {"id": "t1", "name": "Find", "goal": "say hello" + (" then explode" if "explode-first" in users else ""),
                 "done_when": "a greeting"},
                {"id": "t2", "name": "Summarise", "goal": "summarise what t1 found" + (" slowly" if "slow-steps" in users else ""),
                 "done_when": "one line"},
                {"id": "t3", "name": "Publish", "goal": "publish the summary", "done_when": "a report"}]}
        if "team" in allowed and ("direct" not in allowed or "as a team" in users):
            agents = props["tasks"]["items"]["properties"].get("agent", {}).get("enum")
            marks = "".join(f" ({m})" for m in ("retry-me", "never-pass") if m in users)
            return {"approach": "team", "reason": "two steps", "final": "both parts", "tasks": [
                {"id": "t1", "name": "Collect", "goal": "say hello" + marks + (" and ask the planner" if "ask the planner" in users else ""),
                 "done_when": "a greeting", "agent": (agents or ["Collector"])[0], "role": "worker", "harness": "agent"},
                {"id": "t2", "name": "Summarise", "goal": "summarise t1", "done_when": "one line",
                 "depends_on": ["t1"], "agent": (agents or ["Collector", "Writer"])[-1], "role": "worker", "harness": "agent"}]}
        return {"approach": "direct", "tasks": []}
    if "actions" in props:
        last = text_of(body["messages"][-1].get("content"))
        actions = []
        if "Question from t1" in last:
            actions.append({"do": "answer", "task": "t1", "text": "use English"})
        return {"actions": actions}
    if "met" in props and "reason" in props:        # the strict check of one step's result
        task = text_of(body["messages"][-1].get("content")).split("Acceptance test")[0]
        CHECKED[task] = CHECKED.get(task, 0) + 1
        if "never-pass" in task:
            return {"reason": "the second greeting is still missing", "met": False}
        if "retry-me" in task and CHECKED[task] == 1:
            return {"reason": "add a second greeting", "met": False}
        return {"reason": "complete", "met": True}
    return minimal(schema)


CHECKED = {}
HUNG = Path(args.m).with_suffix(".hung") if args.m else None


@app.post("/slots/{slot}")
async def slots(slot: int, request: Request):
    """Like llama-server: 'save' writes what the slot holds to a file; 'restore' reads a file into the slot and
    overwrites what it held (a prompt survives that only if a later request moved it to the in-memory cache)."""
    body = await request.json()
    action = request.query_params.get("action")
    if not args.slot_save_path:
        return JSONResponse({"error": {"message": "slot save path is not set"}}, 501)
    path = Path(args.slot_save_path) / Path(body.get("filename") or "x").name
    if action == "save":
        if not CACHED:
            return JSONResponse({"error": {"message": "empty slot"}}, 400)
        path.write_text(CACHED[0], encoding="utf-8")
        return {"id_slot": slot, "filename": path.name, "n_saved": len(CACHED[0]) // 4}
    if action == "restore":
        if not path.exists():
            return JSONResponse({"error": {"message": "no such file"}}, 404)
        text = path.read_text(encoding="utf-8")
        if CACHED:
            CACHED[0] = text
        else:
            CACHED.append(text)
        return {"id_slot": slot, "filename": path.name, "n_restored": len(text) // 4}
    return JSONResponse({"error": {"message": "unknown action"}}, 400)


@app.post("/tokenize")
async def tokenize(request: Request):
    body = await request.json()
    return {"tokens": list(range(len(body.get("content") or "") // 4))}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    said = " ".join(text_of(m.get("content")) for m in body["messages"] if m["role"] == "user")
    if "hang-once" in said and HUNG is not None and not HUNG.exists():
        HUNG.write_text("hung once")
        time.sleep(40)                              # a stuck model: no bytes at all
    goal = step_goal(text_of(body["messages"][-1].get("content")), said)
    if "slowly" in goal:
        time.sleep(4)                               # a step that takes a while (tests stop a run in the middle)
    if "explode" in goal:
        return JSONResponse({"error": {"message": "the fake model exploded"}}, 500)
    rf = body.get("response_format")
    if rf:
        answer = structured(rf["json_schema"]["schema"], body)
        if answer is None:                          # a planner call that fails (tests: the relay still moves)
            return JSONResponse({"error": {"message": "the fake planner broke"}}, 500)
        content = json.dumps(answer)
        if body.get("stream"):                      # llama-server streams schema answers like any other reply
            def one():
                yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {"role": "assistant", "content": content},
                                                          "finish_reason": None}]}) + "\n\n"
                yield "data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\n"
                yield "data: " + json.dumps({"choices": [], "timings": timings(body), "usage": {}}) + "\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(one(), media_type="text/event-stream")
        return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "timings": timings(body)}
    d = decide(body)
    if d.get("delay") and "tool" in d:
        time.sleep(d["delay"])
    if not body.get("stream"):
        msg = {"role": "assistant", "content": d.get("text", "")}
        if "tool" in d:
            msg["tool_calls"] = [{"id": "call_1", "type": "function", "function": {"name": d["tool"][0], "arguments": json.dumps(d["tool"][1])}}]
        return {"choices": [{"message": msg, "finish_reason": "stop"}], "timings": timings(body)}

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
        yield "data: " + json.dumps({"choices": [], "timings": timings(body), "usage": {}}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
