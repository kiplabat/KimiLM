"""Browser tools (2.9.0; reworked in 2.16.0): an agent works in a real browser window, the way a careful person would.

web_fetch reads a page as text and is the right tool for reading. These tools are for what reading cannot do:
pages that only work with JavaScript, buttons and menus, forms, downloads that start from a click. They drive the
browser helper (Playwright MCP with the computer's own Edge or Chrome) that web search already uses as its fallback.

How an agent sees a page: as a short list of what is on it (headings, text, links with their addresses, buttons,
fields, tables row by row), where everything that can be acted on has a reference such as e12. It acts by
reference, or by the words on the element: browser_click(text="Download prices"). Every action answers with the
page as it is afterwards, so a small model never has to guess what happened.

What the tools do by themselves, as a person would without thinking (2.16.0):
  wait        after opening a page or clicking, they wait until the page has stopped loading;
  cookies     a cookie banner is answered with the choice that allows least ("Reject all", "Only necessary"),
              or accepted when that is the only way on (setting browser_consent: necessary | accept | ask);
  messages    a message box of the page (alert) is read, reported and closed; a question (confirm, prompt) is
              put to the agent, which answers it with browser_click(text="OK" / "Cancel");
  new tabs    a link that opens a new tab is followed there;
  downloads   a file that a click or an address starts downloading is moved to the chat's downloads folder;
  stale refs  a reference from an older view of the page is looked up again by the element's words.

Two groups of tools. "browser" is what every agent with a browser has: open, look, click, type, select, press,
screenshot. "browsing" adds what a browsing specialist needs (the Browser harness): how a page is served and where
its data comes from, whole forms at once, waiting for something, going back, tabs, dialogs, saving a file with the
page's own sign-in, uploading a file.

Each chat has its own page (services.BrowserSession), kept open between calls and closed when it is idle.
Looking is free; clicking, typing, choosing and pressing keys change things on a website, so they ask first
unless the chat, the scheduled task or the long-running task allows browser actions. Paying or ordering asks
every time, and so does sending one of the user's files to a website."""
import base64
import difflib
import json
import re
import shutil
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from . import Tool, ToolDenied, ToolError

VIEW_LIMIT = 7000                 # characters of a page shown at once; the rest is on the next part
REF = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
DOWNLOADED = re.compile(r'Downloaded file (.+?) to "([^"]+)"')
MODAL = re.compile(r'- \["?(alert|confirm|prompt|beforeunload)"? dialog with message "(.*)"\]')
CHOOSER = re.compile(r"- \[File chooser\]")
TABS = re.compile(r"^- (\d+): (\(current\) )?\[(.*?)\]\((.*?)\)\s*$", re.M)
LINE = re.compile(r'^(\s*)- ([^\s":]+)(?: "((?:[^"\\]|\\.)*)")?((?: \[[^\]]*\])*)(?::(?: (.*))?)?$')
# what can be clicked, typed into or chosen: these keep their reference in the view
ACT = {"link", "button", "textbox", "searchbox", "combobox", "checkbox", "radio", "spinbutton", "slider", "switch",
       "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "listbox", "treeitem"}
FIELDS = {"textbox", "searchbox", "combobox", "checkbox", "radio", "spinbutton", "slider", "switch", "listbox"}
WRAPPERS = {"generic", "group", "none", "presentation", "rowgroup"}
CELLS = {"cell", "columnheader", "rowheader", "gridcell"}
KEEP_ATTR = re.compile(r"\[(level=\d+|selected|checked|checked=mixed|disabled|expanded|pressed|required|readonly)\]")
FILE_LINK = re.compile(r"\.(csv|tsv|xlsx?|ods|json|xml|zip|gz|7z|rar|pdf|docx?|pptx?|parquet|geojson|kml|txt|nc|shp)(\?|#|$)", re.I)
# cookie banners: the words that show a banner is one, and its buttons from "allows least" to "allows all"
COOKIE = re.compile(r"cookie|consent|gdpr|privacy (preferences|settings|choices)|tracking technolog|personal data", re.I)
REJECT = re.compile(r"^(reject( all| non[- ]essential| optional)?( cookies)?|decline( all)?( cookies)?|deny( all)?|refuse( all)?|"
                    r"(use |accept |allow )?(only )?(strictly )?(necessary|essential|required)( cookies)?( only)?|"
                    r"continue without (accepting|agreeing)|no,? thanks?|disagree|do not (accept|agree|consent))\W*$", re.I)
ACCEPT = re.compile(r"^(accept( all| cookies| all cookies)?( (&|and) (close|continue))?|allow( all)?( cookies)?|"
                    r"agree( (&|and) (close|continue|proceed))?|i agree|i accept|got it|ok(ay)?|understood|"
                    r"yes,? i (agree|accept)|consent)\W*$", re.I)
# paying or ordering is never covered by a general permission to click on websites
PAY = re.compile(r"\b(pay( now)?|buy( now)?|purchase|place (the |my |your )?order|order now|check ?out|"
                 r"confirm (the |my )?(order|payment|purchase|booking)|complete (the |my )?(order|purchase|payment)|"
                 r"subscribe|donate|book now)\b", re.I)
YES = re.compile(r"^(ok(ay)?|yes|accept|confirm|continue|leave|proceed|sure)\b", re.I)
NO = re.compile(r"^(cancel|no|dismiss|stay|close|decline|reject)\b", re.I)
PAYING = Tool("browser_pay", "", {}, None, "browser", changes=True, label="Pay or order on a website")
SENDING = Tool("browser_send_file", "", {}, None, "browser", changes=True, label="Send a file to a website")
STATE = {}                        # chat id -> {"dialog": {...} | None, "tabs": n, "consent": {host: what was chosen}}


def state(ctx):
    if len(STATE) > 200:          # chats come and go; the newest ones matter
        for key in list(STATE)[:100]:
            STATE.pop(key, None)
    return STATE.setdefault(ctx.chat["id"], {"dialog": None, "tabs": 1, "consent": {}})


# ---------------------------------------------------------------------- talking to the helper
def page_head(text):
    url = re.search(r"- Page URL: (.+)", text or "")
    title = re.search(r"- Page Title: (.+)", text or "")
    return (title.group(1).strip() if title else ""), (url.group(1).strip() if url else "")


def error_text(text):
    m = re.search(r"### Error\n(.*?)(?:\n### |\Z)", text or "", re.S)
    first = (m.group(1) if m else text or "the browser did not answer").strip().splitlines()
    return re.sub(r"\x1b\[[0-9;]*m", "", first[0] if first else "")[:400]


def result_of(text):
    """The value a helper tool handed back (its '### Result' part), as Python."""
    m = re.search(r"### Result\n(.*?)(?:\n### |\Z)", text or "", re.S)
    body = (m.group(1) if m else "").strip()
    try:
        return json.loads(body)
    except ValueError:
        return body


def dialog_in(text):
    m = MODAL.search(text or "")
    return {"kind": m.group(1), "message": m.group(2)} if m else None


async def call(ctx, name, args, timeout=120):
    try:
        ok, text = await ctx.app.services.browser_call(ctx.chat["id"], name, args, timeout)
    except Exception as exc:
        raise ToolError(f"The browser is not available ({type(exc).__name__}: {str(exc)[:200]})") from exc
    if not ok:
        hint = ""
        if "not found in the current page snapshot" in text:
            hint = " The page has changed: call browser_look and use a reference from its answer."
        elif "does not handle the modal state" in text:
            box = dialog_in(text)
            if box:
                state(ctx)["dialog"] = box
                raise ToolError(f"The page is asking something and waits for an answer: “{box['message']}”. Answer "
                                'it first with browser_click(text="OK") or browser_click(text="Cancel").')
            if CHOOSER.search(text):
                raise ToolError("The page has opened a file chooser and waits for it. Give it a file with "
                                "browser_upload, or open the page again to leave it.")
        raise ToolError(error_text(text) + hint)
    return text


async def run(ctx, code, timeout=90):
    """Run a piece of Playwright code that KimiLM wrote (never text from a model) on the chat's page."""
    return result_of(await call(ctx, "browser_run_code_unsafe", {"code": code}, timeout))


def tidy(ctx):
    """The helper writes a file for every page it reports; they are of no use afterwards."""
    try:
        old = time.time() - 120
        for path in ctx.app.services.browser_dir.glob("*-20*.*"):
            if path.suffix in (".yml", ".log") and path.stat().st_mtime < old:
                path.unlink(missing_ok=True)
    except OSError:
        pass


def take_downloads(ctx, text):
    """Files the browser saved during the last action are moved into the chat's downloads folder."""
    saved = []
    for name, where in DOWNLOADED.findall(text or ""):
        src = Path(where)
        if not src.is_absolute() or not src.exists():
            src = ctx.app.services.browser_dir / Path(where).name
        if not src.exists():
            continue
        saved.append(keep_file(ctx, name, src=src))
    return saved


def keep_file(ctx, name, src=None, data=None):
    folder = ctx.workspace / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    clean = re.sub(r"[^\w.\-]+", "_", Path(str(name)).name)[:120] or "download"
    dest = folder / clean
    n = 1
    while dest.exists():
        dest = dest.with_name(f"{Path(clean).stem}-{n}{Path(clean).suffix}")
        n += 1
    if src is not None:
        shutil.move(str(src), str(dest))
    else:
        dest.write_bytes(data)
    ctx.emit("file_changed", path=str(dest), action="downloaded")
    return dest


# ---------------------------------------------------------------------- reading a page
def parse(snapshot):
    """The helper's page snapshot (YAML lines) as a list of elements in page order."""
    m = re.search(r"```yaml\n(.*?)```", snapshot or "", re.S)
    body = m.group(1) if m else ""
    nodes = []
    for raw in body.splitlines():
        got = LINE.match(raw)
        if not got:
            if raw.strip() and nodes:          # a continued text line
                nodes[-1]["value"] = (nodes[-1]["value"] + " " + raw.strip()).strip()
            continue
        indent, role, name, attrs, value = got.groups()
        ref = re.search(r"\[ref=([^\]]+)\]", attrs or "")
        nodes.append({"indent": len(indent), "role": role, "name": (name or "").replace('\\"', '"'),
                      "attrs": " ".join(f"[{a}]" for a in KEEP_ATTR.findall(attrs or "")),
                      "ref": ref.group(1) if ref else None, "pointer": "[cursor=pointer]" in (attrs or ""),
                      "value": (value or "").strip(), "url": "", "hint": ""})
    # an element's address and placeholder are lines of their own in the snapshot: they belong to the element
    out = []
    for n in nodes:
        if n["role"] in ("/url", "/placeholder") and out:
            out[-1]["url" if n["role"] == "/url" else "hint"] = n["value"]
        else:
            out.append(n)
    return out


def subtree(nodes, i):
    j = i + 1
    while j < len(nodes) and nodes[j]["indent"] > nodes[i]["indent"]:
        j += 1
    return nodes[i + 1:j]


def compact(nodes, base=""):
    """The page as an agent reads it: wrappers without meaning are left out, a link carries its address, a table
    row is one line, and only what can be acted on has a reference. About half the size of the raw snapshot."""
    lines, stack, i = [], [], 0
    while i < len(nodes):
        n = nodes[i]
        while stack and stack[-1][0] >= n["indent"]:
            stack.pop()
        depth = sum(kept for _, kept in stack)
        kids = subtree(nodes, i)
        role, text = n["role"], n["value"]
        if role == "row" and kids and all(k["role"] in CELLS for k in kids):
            cells = [(k["name"] or k["value"]).strip() for k in kids]
            lines.append("  " * depth + "- row: " + " | ".join(cells))
            i += 1 + len(kids)
            continue
        acts = role in ACT or n["pointer"]
        empty = not n["name"] and not text
        if (role in WRAPPERS and empty and not acts) or (role in ("paragraph", "text") and empty and not kids):
            stack.append((n["indent"], 0))     # left out: its children move up
            i += 1
            continue
        line = "  " * depth + "- " + role + (f' "{n["name"]}"' if n["name"] else "")
        line += (" " + n["attrs"] if n["attrs"] else "") + (f" [ref={n['ref']}]" if acts and n["ref"] else "")
        if n["url"]:
            line += " → " + (urljoin(base, n["url"]) if base else n["url"])
        if n["hint"]:
            line += f" (placeholder: {n['hint']})"
        if text:
            line += ": " + text
        lines.append(line)
        stack.append((n["indent"], 1))
        i += 1
    return lines


def digest(nodes, answered=None):
    """One line on what the page holds, and what was just done about its banner."""
    count = lambda *roles: sum(n["role"] in roles for n in nodes)        # noqa: E731
    some = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"           # noqa: E731
    parts = [some(count("heading"), "heading"), some(count("link"), "link"), some(count("button"), "button"),
             some(sum(n["role"] in FIELDS for n in nodes), "field"), some(count("table", "grid"), "table")]
    line = "On it: " + (", ".join(p for p in parts if not p.startswith("0 ")) or "no headings, links or fields") + "."
    if answered:
        line += f" Its cookie banner was answered with “{answered}”."
    if any(n["role"] == "textbox" and re.search(r"pass ?word|passcode|\bpin\b", n["name"], re.I) for n in nodes):
        line += (" It asks for a password: never guess one. Say that a sign-in is needed (the user can type it "
                 "in the browser window).")
    return line


def paged(lines, page, limit=VIEW_LIMIT):
    """Part `page` of a long page, cut at line ends. Returns (text, parts)."""
    parts, now, size = [], [], 0
    for line in lines:
        if now and size + len(line) + 1 > limit:
            parts.append(now)
            now, size = [], 0
        now.append(line[:1200])
        size += min(len(line), 1200) + 1
    if now or not parts:
        parts.append(now)
    page = max(1, min(len(parts), int(page or 1)))
    text = "\n".join(parts[page - 1]) or "(the page is empty)"
    if len(parts) > 1:
        text += (f"\n…(part {page} of {len(parts)}. browser_look(page={page + 1 if page < len(parts) else 1}) shows "
                 'another part; browser_look(find="words") only the parts with those words; '
                 'browser_look(show="outline") the page\'s structure.)')
    return text, len(parts)


def found(lines, words):
    """The parts of the page that have these words: each matching line with what it sits in and what is in it."""
    want = " ".join(str(words).lower().split())
    depth = lambda ln: len(ln) - len(ln.lstrip())          # noqa: E731
    keep, hits = set(), 0
    for i, line in enumerate(lines):
        if want not in " ".join(line.lower().split()):
            continue
        hits += 1
        level = depth(line)
        for j in range(i - 1, -1, -1):                     # what it sits in
            if depth(lines[j]) < level:
                keep.add(j)
                level = depth(lines[j])
        keep.add(i)
        for j in range(i + 1, min(len(lines), i + 13)):     # what is in it
            if depth(lines[j]) <= depth(line):
                break
            keep.add(j)
    if not hits:
        return (f"Nothing on the page has the words “{words}”. Try fewer or other words, or "
                'browser_look(show="text") to read the page.')
    text, _ = paged([lines[i] for i in sorted(keep)], 1)
    return f"{hits} place{'s' if hits != 1 else ''} with “{words}”:\n{text}"


async def snapshot(ctx):
    text = await call(ctx, "browser_snapshot", {})
    title, url = page_head(text)
    return title, url, parse(text)


async def consent(ctx, nodes, url):
    """Answer a cookie banner the way a careful person does: with the choice that allows least. Returns the
    words of the button that was pressed (the page then has to be read again), or None. A banner that comes
    back is answered again, a few times: one that never goes away is left to the agent."""
    st = state(ctx)
    mode = ctx.app.db.setting("browser_consent", "necessary")
    host = urlparse(url).netloc
    tries = st.setdefault("consent_tries", {})
    if mode == "ask" or tries.get(host, 0) >= 3:
        return None
    found = {"reject": None, "accept": None}
    for i, n in enumerate(nodes):
        if n["role"] not in ("button", "link") or not n["ref"] or not n["name"]:
            continue
        kind = "reject" if REJECT.match(n["name"].strip()) else "accept" if ACCEPT.match(n["name"].strip()) else None
        if not kind or found[kind]:
            continue
        near = nodes[max(0, i - 14):i + 6]         # a banner says what it is about, close to its buttons
        if any(COOKIE.search(f"{x['name']} {x['value']}") for x in near):
            found[kind] = n
    pick = found["reject"] if mode != "accept" else None
    pick = pick or found["accept"]
    if not pick:
        return None
    tries[host] = tries.get(host, 0) + 1
    try:
        await call(ctx, "browser_click", {"element": pick["name"], "target": pick["ref"]})
    except ToolError:
        return None
    st["consent"][host] = pick["name"]
    return pick["name"]


async def view(ctx, find="", page=1, show="page"):
    """The current page as text."""
    title, url, nodes = await snapshot(ctx)
    if find:
        return f"Page: {title}\nURL: {url}\n\n{found(compact(nodes, url), find)}"
    answered = await consent(ctx, nodes, url)
    if answered:
        await settle(ctx)
        title, url, nodes = await snapshot(ctx)
    head = f"Page: {title}\nURL: {url}\n{digest(nodes, answered)}"
    show = (show or "page").lower()
    if show == "outline":
        keep = {"heading", "banner", "navigation", "main", "contentinfo", "complementary", "form", "search", "table",
                "grid", "dialog", "alertdialog", "region", "article", "tablist", "caption"}
        lines = [ln for ln in compact([n for n in nodes if n["role"] in keep or n["role"] in WRAPPERS], url)]
        return f"{head}\n\nThe page's structure:\n" + ("\n".join(lines)[:VIEW_LIMIT] or "(no headings or regions)")
    if show == "links":
        seen, rows = set(), []
        for n in nodes:
            if n["role"] == "link" and n["url"]:
                full = urljoin(url, n["url"])
                if (n["name"], full) in seen:
                    continue
                seen.add((n["name"], full))
                rows.append(f"- [ref={n['ref']}] {n['name'] or '(no words)'} → {full}"
                            + ("  (a file)" if FILE_LINK.search(full) else ""))
        text, _ = paged(rows, page)
        return f"{head}\n\n{len(rows)} links:\n{text}"
    if show == "forms":
        rows = compact([n for n in nodes if n["role"] in FIELDS or n["role"] in ("button", "option")
                        or n["role"] in WRAPPERS], url)
        return (f"{head}\n\nFields and buttons (fill fields with browser_type or browser_select, by reference or by "
                f"the field's label):\n" + ("\n".join(rows)[:VIEW_LIMIT] or "(no fields on this page)"))
    if show in ("tables", "text"):
        return f"{head}\n\n{await extract(ctx, show, page)}"
    text, _ = paged(compact(nodes, url), page)
    return f"{head}\n\n{text}"


TABLES_JS = """() => Array.from(document.querySelectorAll('table')).slice(0, 8).map((t) => ({
  caption: (t.caption && t.caption.innerText || t.getAttribute('aria-label') || '').trim().slice(0, 120),
  total: t.rows.length,
  rows: Array.from(t.rows).slice(0, 200).map((r) => Array.from(r.cells).map((c) => (c.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 160)))
}))"""
TEXT_JS = "() => (document.body ? document.body.innerText : '').replace(/\\n{3,}/g, '\\n\\n')"


async def extract(ctx, what, page=1):
    """Tables as rows, or the page's readable text: read from the page itself, where it is complete."""
    got = result_of(await call(ctx, "browser_evaluate", {"function": TABLES_JS if what == "tables" else TEXT_JS}))
    if what == "text":
        text, _ = paged(str(got or "").splitlines(), page)
        return "The page's text:\n" + text
    if not isinstance(got, list) or not got:
        return "There is no table on this page."
    rows = []
    for i, t in enumerate(got, 1):
        rows.append(f"Table {i}" + (f" “{t.get('caption')}”" if t.get("caption") else "") + f" ({t.get('total')} rows):")
        rows += [" | ".join(r) for r in t.get("rows") or [] if any(r)]
        if (t.get("total") or 0) > 200:
            rows.append(f"…({t['total'] - 200} more rows)")
        rows.append("")
    text, _ = paged(rows, page)
    return text


async def settle(ctx):
    """Wait until the page has stopped loading (at most a few seconds): what loads late is then on the page when
    it is read."""
    try:
        await run(ctx, "async (page) => { await page.waitForLoadState('domcontentloaded', {timeout: 8000}).catch(() => {});"
                       " await page.waitForLoadState('networkidle', {timeout: 3500}).catch(() => {}); return 'ok'; }", 30)
    except ToolError:
        pass


async def after(ctx, text, said):
    """What every action ends with: messages of the page, downloads, a new tab, waiting, then the page."""
    st = state(ctx)
    notes = []
    for _ in range(4):                         # message boxes: read, reported, closed; questions go to the agent
        box = dialog_in(text)
        if not box:
            break
        if box["kind"] in ("alert", "beforeunload"):
            notes.append(f"The page showed a message: “{box['message']}” (closed)." if box["kind"] == "alert"
                         else "The page asked whether to leave it: yes.")
            text = await call(ctx, "browser_handle_dialog", {"accept": True})
            continue
        st["dialog"] = box
        ask = "asks" if box["kind"] == "confirm" else "asks for a text"
        return (f"{said}\n" + "".join(n + "\n" for n in notes) + f"The page {ask}: “{box['message']}”. Nothing else can "
                'be done on it until you answer: browser_click(text="OK") to agree, browser_click(text="Cancel") to '
                "refuse" + ('; to answer with a text, browser_type(text="…", submit=true).' if box["kind"] == "prompt" else "."))
    st["dialog"] = None
    if CHOOSER.search(text or ""):             # nobody asked for it here: closed, so the page can be used
        try:
            await call(ctx, "browser_file_upload", {})
        except ToolError:
            pass
        notes.append("The page opened a file chooser, which was closed again. To give the page a file, use "
                     "browser_upload with the file's path.")
    for p in take_downloads(ctx, text):
        notes.append(f"Downloaded: {p} ({p.stat().st_size:,} bytes)")
    tabs = TABS.findall(re.search(r"### Open tabs\n(.*?)(?:\n### |\Z)", text or "", re.S).group(1)) \
        if "### Open tabs" in (text or "") else []
    if tabs:
        if len(tabs) > st["tabs"] and not tabs[-1][1]:       # the action opened a new tab: go where it leads
            await call(ctx, "browser_tabs", {"action": "select", "index": int(tabs[-1][0])})
            notes.append(f"It opened in a new tab (tab {len(tabs)} of {len(tabs)}): you are on it now.")
        st["tabs"] = len(tabs)
    await settle(ctx)
    tidy(ctx)
    return f"{said}" + "".join("\n" + n for n in notes) + f"\n\n{await view(ctx)}"


# ---------------------------------------------------------------------- finding the element that is meant
def need_ref(ref):
    ref = str(ref or "").strip().strip("[]").removeprefix("ref=")
    if not REF.match(ref):
        raise ToolError("Give the reference of an element as shown by browser_look, for example e12")
    return ref


def match(nodes, words, roles):
    """The elements whose words are these: exactly, else containing them. (list of nodes)"""
    want = " ".join(str(words).lower().split())
    pool = [n for n in nodes if n["ref"] and (n["role"] in roles or (n["pointer"] and roles is ACT)) and n["name"]]
    exact = [n for n in pool if " ".join(n["name"].lower().split()) == want]
    return exact or [n for n in pool if want in " ".join(n["name"].lower().split())]


async def target(ctx, ref, words, roles, kind="element"):
    """(reference, the element's words) for an action given a reference or the words on the element."""
    if ref not in (None, ""):
        return need_ref(ref), str(words or "")
    if not str(words or "").strip():
        raise ToolError(f"Say which {kind}: its reference from browser_look (ref), or the words on it")
    _, _, nodes = await snapshot(ctx)
    hits = match(nodes, words, roles)
    if len(hits) == 1 or (hits and all(h["name"] == hits[0]["name"] and h["url"] == hits[0]["url"] for h in hits)):
        return hits[0]["ref"], hits[0]["name"]
    if hits:
        shown = "; ".join(f'{h["ref"]} {h["role"]} "{h["name"]}"' for h in hits[:8])
        raise ToolError(f"Several {kind}s have the words “{words}”: {shown}. Give the reference (ref) of the one you mean.")
    names = [n["name"] for n in nodes if n["ref"] and n["role"] in roles and n["name"]]
    close = difflib.get_close_matches(str(words), names, n=5, cutoff=0.5)
    raise ToolError(f"No {kind} on the page has the words “{words}”."
                    + (" Close: " + "; ".join(f'"{c}"' for c in close) + "." if close else "")
                    + " Look at the page with browser_look to see what is there.")


async def acted(ctx, name, args, words, roles):
    """Do one action on an element. A reference that the page no longer knows is looked up again by the
    element's words, once."""
    try:
        return await call(ctx, name, args)
    except ToolError as exc:
        if "browser_look" not in str(exc) or not words:
            raise
        _, _, nodes = await snapshot(ctx)
        hits = match(nodes, words, roles)
        if len(hits) != 1:
            raise
        return await call(ctx, name, {**args, "target": hits[0]["ref"], "element": hits[0]["name"]})


async def guard_payment(ctx, words):
    if words and PAY.search(words) and ctx.approve is not None:
        ok = await ctx.approve(PAYING, {"element": words}, f"“{words}” looks like paying or ordering on a website. "
                               "Go ahead?", ignore_auto=True)
        if not ok:
            raise ToolDenied("The user did not allow paying or ordering.")


async def answer_dialog(ctx, accept, text=None):
    st = state(ctx)
    box = st["dialog"]
    args = {"accept": bool(accept)}
    if text is not None and accept:
        args["promptText"] = str(text)
    out = await call(ctx, "browser_handle_dialog", args)
    st["dialog"] = None
    said = (f"Answered the page's question “{box['message']}” with " if box else "Answered the dialog with ") + \
        ("OK" if accept else "Cancel") + (f" and the text “{text}”." if text is not None and accept else ".")
    return await after(ctx, out, said)


# ---------------------------------------------------------------------- the tools every browsing agent has
async def browser_open(ctx, url):
    if not re.match(r"https?://", url or ""):
        raise ToolError("Give a full http(s) URL")
    state(ctx)["dialog"] = None
    try:
        text = await call(ctx, "browser_navigate", {"url": url})
    except ToolError as exc:
        if "Download is starting" not in str(exc):       # a link to a file starts a download instead of a page
            raise
        text = await call(ctx, "browser_snapshot", {"depth": 1}) + "\n" + str(exc)
    return await after(ctx, text, f"Opened {url}")


async def browser_look(ctx, find="", show="page", page=1):
    return await view(ctx, (find or "").strip(), page, show)


async def browser_click(ctx, ref=None, what="", text=""):
    words = str(text or what or "").strip()
    if state(ctx)["dialog"]:                              # the page is waiting for an answer to its question
        if YES.match(words) or NO.match(words):
            return await answer_dialog(ctx, bool(YES.match(words)))
        raise ToolError(f"The page asks “{state(ctx)['dialog']['message']}” and waits. Answer with "
                        'browser_click(text="OK") or browser_click(text="Cancel").')
    ref, name = await target(ctx, ref, words, ACT)
    await guard_payment(ctx, name or words)
    out = await acted(ctx, "browser_click", {"element": name or ref, "target": ref}, name, ACT)
    return await after(ctx, out, f"Clicked {name or ref}.")


async def browser_type(ctx, ref=None, text="", submit=False, field=""):
    st = state(ctx)
    if st["dialog"] and st["dialog"]["kind"] == "prompt":
        return await answer_dialog(ctx, True, text)
    ref, name = await target(ctx, ref, field, FIELDS, "field")
    out = await acted(ctx, "browser_type", {"element": name or ref, "target": ref, "text": str(text),
                                            "submit": bool(submit)}, name, FIELDS)
    return await after(ctx, out, "Typed the text" + (f" into {name}" if name else "")
                       + (" and pressed Enter." if submit else "."))


async def browser_select(ctx, ref=None, value="", field=""):
    ref, name = await target(ctx, ref, field, FIELDS, "field")
    values = [str(v) for v in value] if isinstance(value, list) else [str(value)]
    out = await acted(ctx, "browser_select_option", {"element": name or ref, "target": ref, "values": values}, name, FIELDS)
    return await after(ctx, out, f"Chose {', '.join(values)}" + (f" in {name}." if name else "."))


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


# ---------------------------------------------------------------------- what a browsing specialist has besides
INSPECT_JS = """async (page) => {
  const url = page.url();
  let raw = null, status = null, ctype = '';
  try {
    const r = await page.context().request.get(url, {timeout: 15000});
    status = r.status(); ctype = r.headers()['content-type'] || ''; raw = await r.text();
  } catch (e) {}
  const dom = await page.evaluate(() => {
    const q = (s) => Array.from(document.querySelectorAll(s));
    const at = (s, a) => { const e = document.querySelector(s); return e ? (e.getAttribute(a) || '') : ''; };
    const abs = (h) => { try { return new URL(h, location.href).href; } catch (e) { return h; } };
    const file = /\\.(csv|tsv|xlsx?|ods|json|xml|zip|gz|7z|rar|pdf|docx?|pptx?|parquet|geojson|kml|txt|nc|shp)(\\?|#|$)/i;
    return {
      title: document.title, lang: document.documentElement.lang || '',
      text: (document.body && document.body.innerText || '').replace(/\\s+/g, ' ').length,
      generator: at('meta[name=generator]', 'content'), robots: at('meta[name=robots]', 'content'),
      canonical: abs(at('link[rel=canonical]', 'href')), next: abs(at('link[rel=next], a[rel=next]', 'href')),
      feeds: q('link[rel=alternate][type]').map((l) => ({type: l.type, href: l.href})).slice(0, 6),
      frames: q('iframe[src]').map((f) => f.src).slice(0, 6),
      forms: q('form').slice(0, 6).map((f) => ({action: abs(f.getAttribute('action') || location.href),
        method: (f.getAttribute('method') || 'get').toLowerCase(),
        fields: Array.from(f.elements).filter((e) => e.name || e.id).slice(0, 30).map((e) => (e.name || e.id) + (e.required ? '*' : ''))})),
      files: q('a[href]').map((a) => ({text: (a.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80), href: a.href,
        download: a.hasAttribute('download')})).filter((a) => a.download || file.test(a.href)).slice(0, 40),
      made: [window.__NEXT_DATA__ ? 'Next.js' : '', window.__NUXT__ ? 'Nuxt' : '',
        document.querySelector('[ng-version]') ? 'Angular' : '', document.querySelector('[data-reactroot], #__next, #root') ? 'React-style root' : '',
        document.querySelector('#app[data-v-app], [data-v-app]') ? 'Vue' : ''].filter(Boolean),
      passwords: q('input[type=password]').length
    };
  });
  const cookies = (await page.context().cookies()).map((c) => ({name: c.name, domain: c.domain, httpOnly: c.httpOnly,
    session: c.expires === -1}));
  const strip = (h) => h.replace(/<script[\\s\\S]*?<\\/script>/gi, ' ').replace(/<style[\\s\\S]*?<\\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim().length;
  return {url, status, ctype, served: raw === null ? null : strip(raw), dom, cookies};
}"""
REQUEST = re.compile(r"^\d+\. \[(\w+)\] (\S+) => \[(\d+)\]", re.M)


async def browser_inspect(ctx):
    """How the page is served: where its content and its data come from, what it stores, what it offers."""
    got = await run(ctx, INSPECT_JS, 60)
    if not isinstance(got, dict):
        raise ToolError("The page could not be inspected (it may still be loading, or it is not a web page).")
    dom, host = got.get("dom") or {}, urlparse(got.get("url") or "").hostname or ""
    out = [f"Page: {dom.get('title')}", f"URL: {got.get('url')}",
           f"Answer of the server: {got.get('status') or 'unknown'}, {got.get('ctype') or 'unknown type'}"
           + (f"; made with {dom['generator']}" if dom.get("generator") else "")
           + (f"; looks like {', '.join(dom['made'])}" if dom.get("made") else "")]
    served, shown = got.get("served"), int(dom.get("text") or 0)
    if served is None:
        out.append("How it is made: the page as the server sends it could not be read.")
    elif shown > 200 and served < shown * 0.5:
        out.append(f"How it is made: most of what you see ({shown:,} characters) is put there by scripts after "
                   f"loading; the server sends only {served:,}. web_fetch on this address reads little: use the "
                   "browser, or better the data addresses below.")
    else:
        out.append(f"How it is made: the content is in the page as the server sends it (it sends {served:,} "
                   f"characters of text, you see {shown:,}): web_fetch can read this address too.")
    try:
        net = REQUEST.findall(await call(ctx, "browser_network_requests", {}))
    except ToolError:
        net = []
    data = [(m, u, s) for m, u, s in net if u != got.get("url")][:20]
    if data:
        out.append("Data the page asked for after loading (these addresses give the data itself; a GET can be read "
                   "with web_fetch or saved with browser_save):\n" + "\n".join(f"- {m} {u} → {s}" for m, u, s in data))
    cookies = got.get("cookies") or []
    if cookies:
        other = sorted({c["domain"].lstrip(".") for c in cookies if host and not host.endswith(c["domain"].lstrip("."))})
        out.append(f"Cookies kept for this visit: {len(cookies)} ("
                   + ", ".join(c["name"] + (" [not readable by scripts]" if c["httpOnly"] else "") for c in cookies[:12])
                   + ")" + (f"; from other sites: {', '.join(other[:6])}" if other else "")
                   + ". They are gone when this browser page is closed.")
    else:
        out.append("Cookies: none.")
    chose = state(ctx)["consent"].get(urlparse(got.get("url") or "").netloc)
    if chose:
        out.append(f"Cookie banner: answered with “{chose}”.")
    if dom.get("files"):
        out.append("Files it links to:\n" + "\n".join(f"- {f['text'] or '(no words)'} → {f['href']}" for f in dom["files"]))
    for f in dom.get("forms") or []:
        out.append(f"Form: sends {f['method'].upper()} to {f['action']} with {', '.join(f['fields']) or 'no named fields'}"
                   " (* = required)")
    extra = [f"next page: {dom['next']}" if dom.get("next") else "",
             f"canonical address: {dom['canonical']}" if dom.get("canonical") and dom["canonical"] != got.get("url") else "",
             f"robots: {dom['robots']}" if dom.get("robots") else "",
             "feeds: " + ", ".join(f["href"] for f in dom["feeds"]) if dom.get("feeds") else "",
             "frames: " + ", ".join(dom["frames"]) if dom.get("frames") else "",
             "a sign-in form is on the page" if dom.get("passwords") else ""]
    if any(extra):
        out.append("Also: " + "; ".join(e for e in extra if e) + ".")
    return "\n".join(out)


async def browser_fill(ctx, fields, submit=""):
    """A whole form in one go: [{"field": label or reference, "value": text | true/false | option}]."""
    if not isinstance(fields, list) or not fields:
        raise ToolError('Give the fields as a list: [{"field": "Your name", "value": "Ada"}, …]')
    _, _, nodes = await snapshot(ctx)
    by_ref = {n["ref"]: n for n in nodes if n["ref"]}
    plan, done = [], []
    for f in fields[:40]:
        key = str((f or {}).get("field") or (f or {}).get("ref") or "").strip()
        node = by_ref.get(key.removeprefix("ref="))
        if not node:
            hits = match(nodes, key, FIELDS)
            if len(hits) != 1:
                names = [n["name"] for n in nodes if n["role"] in FIELDS and n["name"]]
                raise ToolError(f"There is {'no' if not hits else 'more than one'} field “{key}” on the page. "
                                "Fields here: " + "; ".join(f'"{n}"' for n in names[:20]) + ".")
            node = hits[0]
        value = (f or {}).get("value")
        kind = {"checkbox": "checkbox", "radio": "radio", "switch": "checkbox", "combobox": "combobox",
                "listbox": "combobox", "slider": "slider"}.get(node["role"], "textbox")
        if kind in ("checkbox", "radio"):
            value = "true" if str(value).lower() in ("true", "yes", "1", "on", "checked") else "false"
        plan.append({"name": node["name"] or node["ref"], "type": kind, "target": node["ref"],
                     "element": node["name"] or node["ref"], "value": str(value if value is not None else "")})
        done.append(f"{node['name'] or node['ref']} = {value}")
    out = await call(ctx, "browser_fill_form", {"fields": plan})
    said = "Filled in: " + "; ".join(done) + "."
    if submit:
        ref, name = await target(ctx, None, submit, ACT, "button")
        await guard_payment(ctx, name)
        out = await call(ctx, "browser_click", {"element": name, "target": ref})
        said += f" Clicked {name}."
    return await after(ctx, out, said)


async def browser_wait(ctx, text="", gone="", seconds=None):
    args = {}
    if text:
        args["text"] = str(text)
    if gone:
        args["textGone"] = str(gone)
    if not args:
        args["time"] = max(0.5, min(30.0, float(seconds or 2)))
    else:
        args["time"] = max(1.0, min(30.0, float(seconds or 20)))      # how long to wait for it at most
    try:
        out = await call(ctx, "browser_wait_for", args, timeout=75)
    except ToolError as exc:
        raise ToolError(f"Waited, and it did not happen: {str(exc)[:200]} Look at the page with browser_look.") from exc
    said = (f"“{text}” is on the page now." if text else f"“{gone}” is gone from the page." if gone
            else f"Waited {args['time']:g} seconds.")
    return await after(ctx, out, said)


async def browser_back(ctx):
    out = await call(ctx, "browser_navigate_back", {})
    return await after(ctx, out, "Went back to the page before.")


async def browser_tabs(ctx, action="list", index=None):
    action = str(action or "list").lower()
    if action not in ("list", "switch", "close"):
        raise ToolError("action is one of: list, switch, close")
    if action == "list":
        rows = TABS.findall(await call(ctx, "browser_tabs", {"action": "list"}))
        state(ctx)["tabs"] = max(1, len(rows))
        return "Open tabs:\n" + "\n".join(f"- {int(i) + 1}: {t or '(no title)'} — {u}" + ("  (you are here)" if cur else "")
                                          for i, cur, t, u in rows)
    if index is None:
        raise ToolError("Say which tab by its number in the list (index)")
    out = await call(ctx, "browser_tabs", {"action": "select" if action == "switch" else "close", "index": int(index) - 1})
    rows = TABS.findall(out)
    state(ctx)["tabs"] = max(1, len(rows)) if rows else max(1, state(ctx)["tabs"] - (action == "close"))
    return await after(ctx, "", f"{'Switched to' if action == 'switch' else 'Closed'} tab {int(index)}.")


async def browser_dialog(ctx, accept=True, text=None):
    if not state(ctx)["dialog"]:
        raise ToolError("The page is not asking anything right now.")
    return await answer_dialog(ctx, accept, text)


async def browser_save(ctx, url="", ref=None, text="", name=""):
    """Save what an address gives (a PDF the browser would only show, a data address, a picture) to the downloads
    folder, asked for by the browser itself: with the cookies and the sign-in of the page."""
    _, here, nodes = await snapshot(ctx)
    if not url:
        r, _ = await target(ctx, ref, text, {"link"}, "link")
        node = next((n for n in nodes if n["ref"] == r), None)
        if not node or not node["url"]:
            raise ToolError("That element has no address to save. Give a link, or the address itself (url).")
        url = node["url"]
    url = urljoin(here, str(url))
    if not re.match(r"https?://", url):
        raise ToolError("Only http(s) addresses can be saved")
    limit = int(float(ctx.app.db.setting("browser_save_mb", 300)) * 2**20)
    info = await run(ctx, "async (page) => { const r = await page.context().request.get(" + json.dumps(url) + ", {timeout: 180000});"
                     " const b = await r.body(); page.__kimilmFile = b; return {status: r.status(), type: r.headers()['content-type'] || '',"
                     " disposition: r.headers()['content-disposition'] || '', bytes: b.length}; }", 240)
    if not isinstance(info, dict) or int(info.get("status") or 0) >= 400:
        raise ToolError(f"The server answered {info.get('status') if isinstance(info, dict) else 'nothing'} for {url}")
    size = int(info.get("bytes") or 0)
    if size > limit:
        raise ToolError(f"The file is {size / 2**20:.0f} MB, more than the {limit / 2**20:.0f} MB this tool saves.")
    data, step = bytearray(), 3 * 2**20
    for start in range(0, size, step):
        part = await run(ctx, f"async (page) => page.__kimilmFile.subarray({start}, {min(size, start + step)}).toString('base64')", 120)
        data += base64.b64decode(part)
    await run(ctx, "async (page) => { page.__kimilmFile = null; return 'ok'; }", 30)
    said = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", info.get("disposition") or "", re.I)
    name = name or (unquote(said.group(1)) if said else "") or unquote(Path(urlparse(url).path).name) or "download"
    dest = keep_file(ctx, name, data=bytes(data))
    return f"Saved {url} to {dest} ({len(data):,} bytes, {info.get('type') or 'unknown type'})."


async def browser_upload(ctx, path, ref=None, field=""):
    """Give one of the user's files to a page's file field. Sending a file to a website always asks."""
    src = ctx.resolve(path)
    if not src.is_file():
        raise ToolError(f"There is no file at {src}")
    if ctx.approve is not None and not await ctx.approve(
            SENDING, {"file": str(src)}, f"Send the file {src.name} to the website in the browser?", ignore_auto=True):
        raise ToolDenied("The user did not allow sending the file.")
    if ref or field:
        r, name = await target(ctx, ref, field, ACT | FIELDS, "file field")
        try:
            await call(ctx, "browser_click", {"element": name or r, "target": r})
        except ToolError as exc:
            if "file chooser" not in str(exc).lower():
                raise
    # the helper reads files only from its own folder: the file goes there for the moment it takes
    folder = ctx.app.services.browser_dir / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / src.name
    shutil.copyfile(src, copy)
    try:
        out = await call(ctx, "browser_file_upload", {"paths": [str(copy)]})
    except ToolError as exc:
        try:
            await call(ctx, "browser_file_upload", {})        # leave no file chooser open behind
        except ToolError:
            pass
        raise ToolError(f"The page did not take the file ({str(exc)[:200]}). Give its file field or its "
                        "“Choose file” button as ref or field.") from exc
    finally:
        copy.unlink(missing_ok=True)
    return await after(ctx, out, f"Gave the page the file {src.name}.")


S = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
REF_HELP = {"type": "string", "description": "the element's reference from browser_look, e.g. e12"}
TEXT_HELP = {"type": "string", "description": "or the words on the element, e.g. Download prices"}
FIELD_HELP = {"type": "string", "description": "or the field's label, e.g. Your name"}
TOOLS = [
    Tool("browser_open", "Open a web page in a real browser (for pages that need clicks, forms or JavaScript; "
         "use web_fetch just to read). It waits for the page, answers a cookie banner and returns what is on the "
         "page, with a reference for everything that can be clicked or filled in.",
         S({"url": {"type": "string"}}, ["url"]), browser_open, "browser", label="Open in browser",
         summary=lambda a: f"Open {a.get('url')} in the browser"),
    Tool("browser_look", "See the page the browser is on. show: page (everything, default), outline (its "
         "headings and regions), links (every link with its address), forms (fields and buttons), tables (tables "
         "row by row), text (the readable text). find: only the parts with those words. page: a later part of a "
         "long page.",
         S({"find": {"type": "string"},
            "show": {"type": "string", "enum": ["page", "outline", "links", "forms", "tables", "text"]},
            "page": {"type": "integer", "minimum": 1}}, []), browser_look, "browser", label="Look at the page",
         summary=lambda a: "Look at the page" + (f" for “{a.get('find')}”" if a.get("find") else "")
         + (f" ({a.get('show')})" if a.get("show") and a.get("show") != "page" else "")),
    Tool("browser_click", "Click a link, button or other element on the page: by its reference, or by the words "
         "on it. A click that starts a download saves the file to the downloads folder. Also answers a question "
         'the page asks: text="OK" or text="Cancel".',
         S({"ref": REF_HELP, "text": TEXT_HELP, "what": {"type": "string", "description": "what it is, in a few words"}}, []),
         browser_click, "browser", changes=True, label="Click on the page",
         summary=lambda a: f"Click {a.get('text') or a.get('what') or a.get('ref')}"),
    Tool("browser_type", "Type text into a field on the page (by its reference or its label); submit=true presses "
         "Enter afterwards.",
         S({"ref": REF_HELP, "field": FIELD_HELP, "text": {"type": "string"}, "submit": {"type": "boolean"}}, ["text"]),
         browser_type, "browser", changes=True, label="Type on the page",
         summary=lambda a: f"Type “{str(a.get('text', ''))[:120]}” into {a.get('field') or a.get('ref')}"
         + (" and press Enter" if a.get("submit") else "")),
    Tool("browser_select", "Choose an option in a drop-down list on the page (by its reference or its label).",
         S({"ref": REF_HELP, "field": FIELD_HELP, "value": {"type": "string"}}, ["value"]), browser_select, "browser",
         changes=True, label="Choose on the page",
         summary=lambda a: f"Choose {a.get('value')} in {a.get('field') or a.get('ref')}"),
    Tool("browser_press", "Press a key in the browser, e.g. Enter, Escape, PageDown, End (to load what a long "
         "page adds at its end), ArrowDown.",
         S({"key": {"type": "string"}}, ["key"]), browser_press, "browser", changes=True, label="Press a key",
         summary=lambda a: f"Press {a.get('key')}"),
    Tool("browser_screenshot", "Save a picture of the page the browser is on (a file you can look at or give "
         "to the user).", S({"full_page": {"type": "boolean"}}, []), browser_screenshot, "browser",
         label="Screenshot the page", summary=lambda a: "Take a screenshot of the page"),
    # ---- group "browsing": the Browser harness, and harnesses that ask for it
    Tool("browser_inspect", "Find out how the page the browser is on is served: whether its content is in the page "
         "or put there by scripts, the data addresses it loads from (often the best source), its cookies, the "
         "files it links to, where its forms send, the next page.",
         S({}, []), browser_inspect, "browsing", label="Inspect how the page is served",
         summary=lambda a: "Inspect how the page is served"),
    Tool("browser_fill", "Fill in a whole form at once and, with submit, press its button.",
         S({"fields": {"type": "array", "items": {"type": "object", "properties": {
             "field": {"type": "string", "description": "the field's label or reference"},
             "value": {"type": "string", "description": "the text, the option, or true/false for a tick box"}},
             "required": ["field", "value"]}},
            "submit": {"type": "string", "description": "the words on the button to press afterwards"}}, ["fields"]),
         browser_fill, "browsing", changes=True, label="Fill in a form",
         summary=lambda a: "Fill in " + ", ".join(str((f or {}).get("field")) for f in (a.get("fields") or [])[:8])
         + (f" and press {a.get('submit')}" if a.get("submit") else "")),
    Tool("browser_wait", "Wait until words appear on the page (text), until words are gone (gone), or for some "
         "seconds: for results that take a moment.",
         S({"text": {"type": "string"}, "gone": {"type": "string"}, "seconds": {"type": "number"}}, []),
         browser_wait, "browsing", label="Wait for the page", summary=lambda a: "Wait for "
         + (f"“{a.get('text')}”" if a.get("text") else f"“{a.get('gone')}” to go" if a.get("gone") else f"{a.get('seconds') or 2} s")),
    Tool("browser_back", "Go back to the page before.", S({}, []), browser_back, "browsing", changes=True,
         label="Go back", summary=lambda a: "Go back to the page before"),
    Tool("browser_tabs", "List the open tabs, switch to one or close one (by its number in the list).",
         S({"action": {"type": "string", "enum": ["list", "switch", "close"]}, "index": {"type": "integer"}}, []),
         browser_tabs, "browsing", label="Browser tabs",
         summary=lambda a: f"Tabs: {a.get('action') or 'list'}" + (f" {a.get('index')}" if a.get("index") else "")),
    Tool("browser_dialog", "Answer a question the page asks in a dialog: accept or not, with a text if it asks "
         "for one.", S({"accept": {"type": "boolean"}, "text": {"type": "string"}}, ["accept"]), browser_dialog,
         "browsing", changes=True, label="Answer the page's question",
         summary=lambda a: "Answer the page's question with " + ("OK" if a.get("accept") else "Cancel")),
    Tool("browser_save", "Save a file to the downloads folder with the page's own sign-in and cookies: a PDF or "
         "picture the browser would only show, or a data address. Give a link (ref or its words) or the address.",
         S({"url": {"type": "string"}, "ref": REF_HELP, "text": TEXT_HELP,
            "name": {"type": "string", "description": "a file name, if the address has none"}}, []),
         browser_save, "browsing", label="Save a file from the site",
         summary=lambda a: f"Save {a.get('url') or a.get('text') or a.get('ref')}"),
    Tool("browser_upload", "Give a file from the chat's folders to a page's file field (asks the user every time).",
         S({"path": {"type": "string"}, "ref": REF_HELP, "field": FIELD_HELP}, ["path"]), browser_upload, "browsing",
         changes=True, label="Give the page a file", summary=lambda a: f"Give the page {a.get('path')}"),
]
