"""Tool calls are checked, and where possible repaired, before they run (2.16.0).

A small model often knows which tool it wants and what it wants from it, and still gets the call slightly wrong:
`file` where the tool says `path`, "5" where it says 5, the arguments wrapped in another object, a tool name
with a dash, JSON with a trailing comma. Running such a call fails with a Python error the model cannot learn
from; refusing it with "wrong arguments" costs a round of minutes on a processor. So:

  what is plainly meant is repaired, and the answer says what was read as what (the model sees the right
  form once and usually uses it afterwards);
  what cannot be repaired is not run, and the answer says exactly what is missing or wrong and how the tool is
  called: its parameters, which are required, what each is.

Only what a tool's own description of its parameters says is used. A call that the description allows is never
touched."""
import ast
import difflib
import json
import re

# other names models use for the same thing, tried only when the tool has no parameter of the given name
ALIASES = {
    "path": ("file", "filename", "file_path", "filepath", "file_name", "dir", "directory", "folder", "location"),
    "query": ("q", "search", "search_query", "keywords", "term", "terms", "question"),
    "url": ("link", "href", "address", "uri", "page", "website"),
    "text": ("content", "contents", "body", "message", "string", "value", "input"),
    "content": ("text", "contents", "body", "data"),
    "command": ("cmd", "shell", "commandline"),
    "code": ("script", "python", "source"),
    "prompt": ("description", "text", "instruction"),
    "ref": ("reference", "element", "id"),
}
WRAPS = ("arguments", "args", "parameters", "params", "input", "kwargs", "tool_input")
TRUE, FALSE = ("true", "yes", "1", "on"), ("false", "no", "0", "off")


def loose_json(raw):
    """Arguments that are almost JSON: code fences, trailing commas, single quotes, a missing closing brace."""
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    tries = [text, re.sub(r",\s*([}\]])", r"\1", text)]
    if text.count("{") > text.count("}"):
        tries.append(re.sub(r",\s*$", "", tries[-1]) + "}" * (text.count("{") - text.count("}")))
    for t in tries:
        try:
            return json.loads(t)
        except ValueError:
            pass
    for t in tries:
        try:
            value = ast.literal_eval(t)                 # {'path': 'a.txt'}: a Python dict, not JSON
            if isinstance(value, (dict, list, str, int, float, bool)):
                return value
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            pass
    raise ValueError("not JSON")


def how_to_call(tool):
    """A tool's call in one or two lines, from its own description of its parameters."""
    schema = tool.parameters if isinstance(tool.parameters, dict) else {}
    props = schema.get("properties") or {}
    required = list(schema.get("required") or [])
    order = required + [p for p in props if p not in required]
    head = f"{tool.name}(" + ", ".join(p if p in required else f"{p}?" for p in order[:10]) + ")"
    lines = []
    for p in order[:10]:
        spec = props.get(p) if isinstance(props.get(p), dict) else {}
        kind = spec.get("type") or "any"
        kind = "/".join(kind) if isinstance(kind, list) else kind
        if spec.get("enum"):
            kind = "one of " + ", ".join(str(e) for e in spec["enum"][:12])
        about = " ".join(str(spec.get("description") or "").split())[:90]
        lines.append(f"- {p} ({kind}{', required' if p in required else ''})" + (f": {about}" if about else ""))
    return "How to call it: " + head + ("\n" + "\n".join(lines) if lines else " — it takes no arguments")


def find_tool(tool_map, name):
    """(the tool's real name or None, a note when the name had to be corrected)."""
    if name in tool_map:
        return name, None
    raw = str(name or "").strip()
    clean = re.sub(r"^(functions?|tools?)[.:/]", "", raw).strip()
    clean = re.sub(r"[\s\-.]+", "_", clean).strip("_")
    by_lower = {n.lower(): n for n in tool_map}
    if clean.lower() in by_lower:
        real = by_lower[clean.lower()]
        return real, (f"`{raw}` was read as the tool `{real}`." if real != raw else None)
    close = difflib.get_close_matches(clean.lower(), list(by_lower), n=2, cutoff=0.82)
    if len(close) == 1 or (len(close) == 2 and difflib.SequenceMatcher(None, clean.lower(), close[0]).ratio() > 0.93):
        return by_lower[close[0]], f"`{raw}` was read as the tool `{by_lower[close[0]]}`."
    return None, None


def coerce(value, spec):
    """A value as the parameter's type wants it, when what was meant is plain. Returns (value, changed)."""
    kind = spec.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if isinstance(value, str):
        s = value.strip()
        if "integer" in kinds and re.fullmatch(r"[-+]?\d+", s):
            return int(s), True
        if ("number" in kinds or "integer" in kinds) and re.fullmatch(r"[-+]?\d+\.0+", s) and "integer" in kinds:
            return int(float(s)), True
        if "number" in kinds and "string" not in kinds and re.fullmatch(r"[-+]?\d*\.?\d+(e[-+]?\d+)?", s, re.I):
            return float(s), True
        if "boolean" in kinds and "string" not in kinds and s.lower() in TRUE + FALSE:
            return s.lower() in TRUE, True
        if ("array" in kinds or "object" in kinds) and "string" not in kinds and s[:1] in "[{":
            try:
                got = loose_json(s)
                if isinstance(got, list if "array" in kinds else dict):
                    return got, True
            except ValueError:
                pass
        if "array" in kinds and "string" not in kinds and s:
            return [value], True
    elif isinstance(value, bool):
        if "string" in kinds and "boolean" not in kinds:
            return ("true" if value else "false"), True
    elif isinstance(value, (int, float)):
        if "string" in kinds and "number" not in kinds and "integer" not in kinds:
            return str(value), True
        if "integer" in kinds and isinstance(value, float) and value.is_integer():
            return int(value), True
        if "boolean" in kinds and len(kinds) == 1 and value in (0, 1):
            return bool(value), True
    elif isinstance(value, dict) and "array" in kinds and "object" not in kinds:
        return [value], True
    if spec.get("enum") and isinstance(value, str) and value not in spec["enum"]:
        names = {str(e).lower(): e for e in spec["enum"]}
        hit = names.get(value.strip().lower())
        if hit is None:
            close = difflib.get_close_matches(value.strip().lower(), list(names), n=1, cutoff=0.8)
            hit = names[close[0]] if close else None
        if hit is not None:
            return hit, True
    return value, False


def repair(tool_map, name, args):
    """A tool call made ready to run. Returns {"name", "args", "notes": [what was read as what], "error": text or
    None}. With an error the call must not run: the text tells the model what to change."""
    out = {"name": name, "args": args, "notes": [], "error": None}
    real, note = find_tool(tool_map, name)
    if not real:
        names = sorted(tool_map)
        close = difflib.get_close_matches(str(name or "").lower(), [n.lower() for n in names], n=4, cutoff=0.5)
        shown = [n for n in names if n.lower() in close] or names[:30]
        out["error"] = (f"There is no tool called {name!r}. " + ("The closest are: " if close else "Available: ")
                        + ", ".join(shown) + ("." if close or len(names) <= 30 else ", …"))
        return out
    if note:
        out["notes"].append(note)
    out["name"], tool = real, tool_map[real]
    schema = tool.parameters if isinstance(tool.parameters, dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = [r for r in (schema.get("required") or []) if isinstance(r, str)]
    # ---- the arguments as an object
    if isinstance(args, dict) and "_unparsed" in args:
        args = args["_unparsed"]
    if isinstance(args, str):
        try:
            args = loose_json(args) if args.strip() else {}
            if not isinstance(out["args"], str):
                out["notes"].append("The arguments were not valid JSON and were repaired.")
        except ValueError:
            if len(required) == 1 and (props.get(required[0]) or {}).get("type") in ("string", None) and args.strip():
                args = {required[0]: args}             # just the one thing the tool needs, as plain text
            else:
                out["args"] = {}
                out["error"] = "The arguments were not valid JSON; send them as one JSON object.\n" + how_to_call(tool)
                return out
    if args is None:
        args = {}
    if not isinstance(args, dict):
        if len(required) == 1 or len(props) == 1:
            args = {(required or list(props))[0]: args}
        else:
            out["args"] = {}
            out["error"] = "Error: the arguments must be one JSON object.\n" + how_to_call(tool)
            return out
    args = dict(args)
    if not props:                                       # the tool does not say what it takes: nothing to check
        out["args"] = args
        return out
    # ---- arguments wrapped in another object
    if len(args) == 1:
        key, inner = next(iter(args.items()))
        if key in WRAPS and key not in props:
            if isinstance(inner, str):
                try:
                    inner = loose_json(inner)
                except ValueError:
                    pass
            if isinstance(inner, dict):
                args = dict(inner)
                out["notes"].append(f"The arguments were inside `{key}`: they belong at the top.")
    # ---- names
    # a built-in tool takes exactly what it says; a connector's tool may take more unless it says it does not
    open_schema = schema.get("additionalProperties") is not False and getattr(tool, "source", "builtin") != "builtin"
    for key in [k for k in list(args) if k not in props]:
        target = next((p for p, others in ALIASES.items() if p in props and p not in args and key.lower() in others), None)
        if not target:
            lower = {p.lower().replace("_", ""): p for p in props if p not in args}
            target = lower.get(key.lower().replace("_", "").replace("-", ""))
        if not target:
            close = difflib.get_close_matches(key, [p for p in props if p not in args], n=1, cutoff=0.8)
            target = close[0] if close else None
        if target:
            args[target] = args.pop(key)
            out["notes"].append(f"`{key}` was read as `{target}`.")
        elif not open_schema:
            args.pop(key)
            out["notes"].append(f"`{key}` is not a parameter of {real} and was left out.")
    # ---- a value that is the whole call again: {"code": "{\"code\": \"…\"}"} (2.26.0, seen on a 9B model)
    for key in list(args):
        value = args[key]
        if not (isinstance(value, str) and re.match(r'\s*\{\s*"' + re.escape(key) + r'"\s*:', value)):
            continue
        try:
            inner = loose_json(value)
        except ValueError:
            inner = None
        if isinstance(inner, dict) and key in inner:
            args[key] = inner[key]
            for k, v in inner.items():
                if k != key and k in props and k not in args:
                    args[k] = v
            out["notes"].append(f"`{key}` held the whole call written as JSON: its own value was used.")
        else:
            out["args"] = args
            out["error"] = (f"Error: {real} was not run. The value of `{key}` is the whole call written as JSON "
                            "again, and it is cut off (probably the reply reached its length limit). Send only the "
                            f"value itself as `{key}`. If it is long, write it to a file in parts first.")
            return out
    # ---- values
    for key in list(args):
        spec = props.get(key) if isinstance(props.get(key), dict) else None
        if spec is None:
            continue
        if args[key] is None and key not in required:
            args.pop(key)
            continue
        new, changed = coerce(args[key], spec)
        if changed:
            args[key] = new
        if spec.get("enum") and not isinstance(args[key], (dict, list)) and args[key] not in spec["enum"]:
            out["args"] = args
            out["error"] = (f"Error: {args[key]!r} is not a value `{key}` of {real} can have. It is one of: "
                            + ", ".join(str(e) for e in spec["enum"][:20]) + ".")
            return out
    missing = [r for r in required if r not in args or args[r] is None]
    out["args"] = args
    if missing:
        out["error"] = (f"Error: {real} was not run: it needs " + ", ".join(f"`{m}`" for m in missing)
                        + (", which is missing.\n" if len(missing) == 1 else ", which are missing.\n") + how_to_call(tool))
    return out
