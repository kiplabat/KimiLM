"""A tiny MCP server for engine tests: one read-only tool and one that changes something (stdio by default).

2.10.0: it also has a resource and a resource template, a prompt, a tool with a title that returns a picture,
and tools that show what reached the server (an environment variable, a request header). `--http PORT` serves it
over Streamable HTTP, `--sse PORT` over the older SSE transport, and `--oauth` (with --http) makes it ask for a
sign-in, with an in-memory authorization server that needs no person. The extras are there with `--full`."""
import base64
import os
import sys

from fastmcp import FastMCP

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
auth = None
if "--oauth" in sys.argv:
    from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
    port = int(sys.argv[sys.argv.index("--http") + 1])
    from mcp.server.auth.settings import ClientRegistrationOptions
    auth = InMemoryOAuthProvider(base_url=f"http://127.0.0.1:{port}",
                                 client_registration_options=ClientRegistrationOptions(enabled=True))
mcp = FastMCP("test", version="9.9.9", instructions="A server for tests.", auth=auth)
STATE = {"value": 0}


@mcp.tool(annotations={"readOnlyHint": True})
def whoami() -> dict:
    """Who is connected."""
    return {"username": "tester", "companies": ["acme"]}


@mcp.tool()
def set_value(value: int) -> str:
    """Change the stored value."""
    STATE["value"] = value
    return f"value is now {value}"


if "--catalogue" in sys.argv:       # a paged list and a long answer (2.19.0 tests)
    @mcp.tool(annotations={"readOnlyHint": True})
    def list_items(page: int = 1, per_page: int = 10) -> dict:
        """One page of the 47 items in the catalogue."""
        start = (page - 1) * per_page
        items = [{"id": i, "title": f"Item {i}"} for i in range(start + 1, min(47, start + per_page) + 1)]
        return {"page": page, "items": items, "has_more": start + per_page < 47}


    @mcp.tool(annotations={"readOnlyHint": True})
    def get_guide() -> str:
        """The publishing guide: long."""
        return "GUIDE START\n" + "".join(f"Rule {i}: keep every field within its limit.\n" for i in range(1, 900)) + "GUIDE END"


if "--full" in sys.argv:            # everything beyond the two basic tools (2.10.0 tests)
    @mcp.tool(title="Wipe everything", annotations={"destructiveHint": True})
    def wipe() -> str:
        """Remove the stored value."""
        STATE["value"] = 0
        return "wiped"


    @mcp.tool(title="Take a picture", annotations={"readOnlyHint": True})
    def snapshot():
        """Return a picture."""
        from fastmcp.utilities.types import Image
        return Image(data=PNG, format="png")


    @mcp.tool(annotations={"readOnlyHint": True})
    def show_env(name: str) -> str:
        """The value of an environment variable as the server sees it."""
        return os.environ.get(name, "(not set)")


    @mcp.tool(annotations={"readOnlyHint": True})
    def show_header(name: str) -> str:
        """The value of a request header as the server sees it."""
        from fastmcp.server.dependencies import get_http_headers
        return get_http_headers(include_all=True).get(name.lower(), "(not sent)")


    @mcp.resource("notes://today", name="Today's note", description="What happened today", mime_type="text/plain")
    def today() -> str:
        return "Today the value was " + str(STATE["value"])


    @mcp.resource("notes://{day}", name="A day's note")
    def day_note(day: str) -> str:
        return f"Nothing happened on {day}."


    @mcp.prompt(name="summarise", description="Ask for a summary of a topic")
    def summarise(topic: str) -> str:
        return f"Summarise what is known about {topic} in three sentences."


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="http", host="127.0.0.1", port=int(sys.argv[sys.argv.index("--http") + 1]), show_banner=False)
    elif "--sse" in sys.argv:
        mcp.run(transport="sse", host="127.0.0.1", port=int(sys.argv[sys.argv.index("--sse") + 1]), show_banner=False)
    else:
        mcp.run(show_banner=False)
