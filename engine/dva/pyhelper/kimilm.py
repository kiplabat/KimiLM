"""Call the services connected to KimiLM from code run by run_python (2.19.0).

    from kimilm import call, tools
    page = call("list_my_datasets", page=2, per_page=50)    # a dict or list when the answer is JSON, else text
    tools()                                                 # the tools this agent may call, with a line on each

Every call goes through KimiLM with the same rules as a call the agent makes itself: the same connectors, the
same approvals, written in the task's trail. It works only while the run_python call that started this code is
running. Use it for work with many calls (paging through results, one call per row of a table): one program
instead of many turns of the model."""
import json
import os
import urllib.error
import urllib.request

__all__ = ["call", "tools", "ToolError"]


class ToolError(Exception):
    """The service, or KimiLM, refused or failed the call. The message says why."""


def _post(path, body):
    url, token = os.environ.get("KIMILM_BRIDGE"), os.environ.get("KIMILM_BRIDGE_TOKEN")
    if not url or not token:
        raise ToolError("Connected services can be called only from code that run_python runs inside KimiLM")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))       # KimiLM is on this computer
    req = urllib.request.Request(url + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-KimiLM-Bridge": token})
    try:
        with opener.open(req, timeout=360) as r:
            out = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            out = json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            out = {}
        raise ToolError(out.get("error") or f"KimiLM answered {exc.code}") from None
    except urllib.error.URLError as exc:
        raise ToolError(f"KimiLM could not be reached: {exc.reason}") from None
    if not out.get("ok"):
        raise ToolError(out.get("error") or "The call did not work")
    return out


def call(tool, **arguments):
    """Call a connected service's tool by its name. Returns the answer: parsed JSON when it is JSON, else text."""
    text = _post("/call", {"tool": str(tool), "arguments": arguments})["result"]
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def tools():
    """The tools of connected services this code may call: {name: description}."""
    return _post("/tools", {})["tools"]
