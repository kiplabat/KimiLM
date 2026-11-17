"""Browser tools (2.9.0): an agent works in a real browser window, the way a person would.

web_fetch reads a page as text and is the right tool for reading. These tools are for what reading cannot do:
pages that only work with JavaScript, buttons and menus, forms, sign-in pages the user is asked to fill in,
downloads that start from a click. They drive the browser helper (Playwright MCP with the computer's own Edge
or Chrome) that web search already uses as its fallback.

How an agent sees a page: as a list of what is on it (headings, text, links, buttons, fields), each with a short
reference such as e12. It acts by reference: browser_click(ref="e12"). Every action answers with the page as it
is afterwards, so a small model never has to guess what happened.

Each chat has its own page (services.BrowserSession), kept open between calls and closed when it is idle.
Looking is free; clicking, typing, choosing and pressing keys change things on a website, so they ask first
unless the chat, the scheduled task or the long-running task allows browser actions."""
import re
import shutil
import time
from pathlib import Path

from . import Tool, ToolError

VIEW_LIMIT = 9000
REF = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
DOWNLOADED = re.compile(r'Downloaded file (.+?) to "([^"]+)"')


def page_head(text):
    url = re.search(r"- Page URL: (.+)", text or "")
    title = re.search(r"- Page Title: (.+)", text or "")
    return (title.group(1).strip() if title else ""), (url.group(1).strip() if url else "")


def page_view(text, limit=VIEW_LIMIT):
    """The part of the helper's answer a model needs: the page's elements, without the helper's own notes."""
    m = re.search(r"```yaml\n(.*?)```", text or "", re.S)
    body = (m.group(1) if m else re.sub(r"^### .*$", "", text or "", flags=re.M)).strip()
    if len(body) > limit:
        body = body[:limit] + (f"\n…(the page is longer: {len(body) - limit:,} more characters. Use browser_look "
                               "with find=\"words\" to see the part you need.)")
    return body or "(the page is empty)"


def error_text(text):
    m = re.search(r"### Error\n(.*?)(?:\n### |\Z)", text or "", re.S)
    first = (m.group(1) if m else text or "the browser did not answer").strip().splitlines()
    return re.sub(r"\x1b\[[0-9;]*m", "", first[0] if first else "")[:400]


async def call(ctx, name, args, timeout=120):
    try:
        ok, text = await ctx.app.services.browser_call(ctx.chat["id"], name, args, timeout)
    except Exception as exc:
        raise ToolError(f"The browser is not available ({type(exc).__name__}: {str(exc)[:200]})") from exc
    if not ok:
        hint = ""
        if "not found in the current page snapshot" in text:
            hint = " The page has changed: call browser_look and use a reference from its answer."
        raise ToolError(error_text(text) + hint)
    return text


def take_downloads(ctx, text):
    """Files the browser saved during the last action are moved into the chat's downloads folder."""
    saved = []
    for name, where in DOWNLOADED.findall(text or ""):
        src = Path(where)
        if not src.is_absolute() or not src.exists():
            src = ctx.app.services.browser_dir / Path(where).name
        if not src.exists():
            continue
        folder = ctx.workspace / "downloads"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / re.sub(r"[^\w.\-]+", "_", Path(name).name)[:120]
        n = 1
        while dest.exists():
            dest = dest.with_name(f"{Path(name).stem}-{n}{Path(name).suffix}")
            n += 1
        shutil.move(str(src), str(dest))
        ctx.emit("file_changed", path=str(dest), action="downloaded")
        saved.append(dest)
    return saved


async def view(ctx, find="", first=None):
    """The current page as text; `first` is the helper's answer to the action that led here."""
    if find:
        text = await call(ctx, "browser_find", {"text": find, "maxResults": 25})
        body = re.sub(r"^### Result\n", "", text).strip()
        head = await call(ctx, "browser_snapshot", {"depth": 1})
        title, url = page_head(head)
        return f"Page: {title}\nURL: {url}\n\n{body[:VIEW_LIMIT]}"
    text = await call(ctx, "browser_snapshot", {})
    title, url = page_head(text)
    return f"Page: {title}\nURL: {url}\n\n{page_view(text)}"


async def after(ctx, text, said):
    saved = take_downloads(ctx, text)
    note = "".join(f"\nDownloaded: {p} ({p.stat().st_size:,} bytes)" for p in saved)
    return f"{said}{note}\n\n{await view(ctx)}"


def need_ref(ref):
    ref = str(ref or "").strip().strip("[]").removeprefix("ref=")
    if not REF.match(ref):
        raise ToolError("Give the reference of an element as shown by browser_look, for example e12")
    return ref


async def browser_open(ctx, url):
    if not re.match(r"https?://", url or ""):
        raise ToolError("Give a full http(s) URL")
    try:
        text = await call(ctx, "browser_navigate", {"url": url})
    except ToolError as exc:
        if "Download is starting" not in str(exc):       # a link to a file starts a download instead of a page
            raise
        text = await call(ctx, "browser_snapshot", {})
    return await after(ctx, text, f"Opened {url}")


async def browser_look(ctx, find=""):
    return await view(ctx, (find or "").strip())


async def browser_click(ctx, ref, what=""):
    ref = need_ref(ref)
    text = await call(ctx, "browser_click", {"element": what or ref, "target": ref})
    return await after(ctx, text, f"Clicked {what or ref}.")


async def browser_type(ctx, ref, text, submit=False):
    ref = need_ref(ref)
    out = await call(ctx, "browser_type", {"element": ref, "target": ref, "text": str(text), "submit": bool(submit)})
    return await after(ctx, out, "Typed the text" + (" and pressed Enter." if submit else "."))


async def browser_select(ctx, ref, value):
    ref = need_ref(ref)
    values = value if isinstance(value, list) else [str(value)]
    out = await call(ctx, "browser_select_option", {"element": ref, "target": ref, "values": values})
    return await after(ctx, out, f"Chose {', '.join(values)}.")


async def browser_press(ctx, key):
    out = await call(ctx, "browser_press_key", {"key": str(key)})
    return await after(ctx, out, f"Pressed {key}.")


async def browser_screenshot(ctx, full_page=False):
    folder = ctx.workspace / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"page-{time.strftime('%Y%m%d-%H%M%S')}.png"
    temp = ctx.app.services.browser_dir / name
    await call(ctx, "browser_take_screenshot", {"filename": str(temp), "fullPage": bool(full_page)})
    if not temp.exists():
        raise ToolError("The browser did not save the screenshot")
    dest = folder / name
    shutil.move(str(temp), str(dest))
    ctx.emit("file_changed", path=str(dest), action="created")
    return f"Saved a screenshot of the page to {dest}. Use look_at_image on it if you need to see the page."


S = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
REF_HELP = {"type": "string", "description": "the element's reference from browser_look, e.g. e12"}
TOOLS = [
    Tool("browser_open", "Open a web page in a real browser (for pages that need clicks, forms or JavaScript; "
         "use web_fetch just to read). Returns the page's elements with their references.",
         S({"url": {"type": "string"}}, ["url"]), browser_open, "browser", label="Open in browser",
         summary=lambda a: f"Open {a.get('url')} in the browser"),
    Tool("browser_look", "See the page the browser is on: its text, links, buttons and fields with their "
         "references. With find, only the parts that contain those words.",
         S({"find": {"type": "string"}}, []), browser_look, "browser", label="Look at the page",
         summary=lambda a: "Look at the page" + (f" for “{a.get('find')}”" if a.get("find") else "")),
    Tool("browser_click", "Click a link, button or other element on the page. A click that starts a download "
         "saves the file to the downloads folder.",
         S({"ref": REF_HELP, "what": {"type": "string", "description": "what it is, in a few words"}}, ["ref"]),
         browser_click, "browser", changes=True, label="Click on the page",
         summary=lambda a: f"Click {a.get('what') or a.get('ref')}"),
    Tool("browser_type", "Type text into a field on the page; submit=true presses Enter afterwards.",
         S({"ref": REF_HELP, "text": {"type": "string"}, "submit": {"type": "boolean"}}, ["ref", "text"]),
         browser_type, "browser", changes=True, label="Type on the page",
         summary=lambda a: f"Type “{str(a.get('text', ''))[:120]}” into {a.get('ref')}"
         + (" and press Enter" if a.get("submit") else "")),
    Tool("browser_select", "Choose an option in a drop-down list on the page.",
         S({"ref": REF_HELP, "value": {"type": "string"}}, ["ref", "value"]), browser_select, "browser",
         changes=True, label="Choose on the page", summary=lambda a: f"Choose {a.get('value')} in {a.get('ref')}"),
    Tool("browser_press", "Press a key in the browser, e.g. Enter, Escape, PageDown, ArrowDown.",
         S({"key": {"type": "string"}}, ["key"]), browser_press, "browser", changes=True, label="Press a key",
         summary=lambda a: f"Press {a.get('key')}"),
    Tool("browser_screenshot", "Save a picture of the page the browser is on (a file you can look at or give "
         "to the user).", S({"full_page": {"type": "boolean"}}, []), browser_screenshot, "browser",
         label="Screenshot the page", summary=lambda a: "Take a screenshot of the page"),
]
