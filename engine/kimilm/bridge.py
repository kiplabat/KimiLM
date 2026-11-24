"""From code to connected services (2.19.0): the other end of `kimilm.call` (pyhelper/kimilm.py).

A long-running task asked a small model to page through 23 pages of a service's datasets and write them to a
file: 23 turns, each reading a page back into a context that could not hold them. With the bridge the same work
is one run_python program that calls the service in a loop and writes the file.

Each run_python call gets a token of its own (`open`), valid until that call ends (`close`). With the token the
program reaches only the connectors of the agent that started it, through `Turn.bridge_call`: the same tools,
the same approvals, written in the task's trail. The routes are `POST /api/bridge/call` and `/api/bridge/tools`,
authorised by the header X-KimiLM-Bridge instead of the app's own token, which code never sees."""
import os
import secrets


class Bridge:
    def __init__(self, app):
        self.app = app
        self.open_calls = {}                      # token -> the run_python call's tool context

    def open(self, ctx):
        port = os.environ.get("KIMILM_PORT")
        if not port or ctx.turn is None:
            return None
        token = secrets.token_urlsafe(24)
        self.open_calls[token] = ctx
        return {"token": token, "env": {"KIMILM_BRIDGE": f"http://127.0.0.1:{port}/api/bridge",
                                        "KIMILM_BRIDGE_TOKEN": token}}

    def close(self, token):
        self.open_calls.pop(token, None)

    def turn_of(self, token):
        ctx = self.open_calls.get(str(token or ""))
        if ctx is None:
            raise PermissionError("This code may not call KimiLM (it runs only while its run_python call runs)")
        return ctx.turn

    async def call(self, token, tool, arguments):
        return await self.turn_of(token).bridge_call(str(tool or ""), arguments or {})

    def tools(self, token):
        return self.turn_of(token).bridge_tools()
