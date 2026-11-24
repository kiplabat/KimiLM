"""An evaluation built into the app (2.7.0): small, realistic tasks with answers that code can check (twelve in
2.7.0; twenty in 2.32.0, each tagged with the skills it tests, so that the planner knows which model is good at
what: see kimilm/abilities.py).

Why. Whether a setup is good (this model, these settings, this computer) was a matter of impression. The
evaluation gives numbers that can be compared: how many tasks pass, how long they take, how many tokens were read
and written, how much came from the cache. It answers "is the 4B model enough for my work?", "did this setting
help?" and, between versions of KimiLM, "did we get better?".

How. Each task gets its own folder with its input files, and a chat with approvals switched off that may use only
that folder (no web, nothing outside it). The task's request is sent like any message; when the reply is done, a
check written in code looks at the reply and the files (never a model's opinion). Tasks run one after another.
Results are stored per run (table eval_runs) with the app version and the model, and shown in Settings.

The tasks need no internet and nothing personal. They cover the things agents do here: reading and computing,
writing and editing files, fixing code, finding things, summarising within limits, and one planned run."""
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import VERSION
from .db import new_id, now

SALES = "region,month,amount\nNorth,Jan,120\nSouth,Jan,95\nNorth,Feb,210\nEast,Jan,310\nSouth,Feb,149\nEast,Feb,400\n"
ARTICLE = ("In 2019 a utility in the highlands began replacing diesel generators with solar mini-grids. By the end "
           "of the programme 42% of the households in the district had a connection, up from 11%. Evening study "
           "hours for children rose, shops stayed open later, and spending on kerosene fell sharply. The utility "
           "reports that maintenance costs were lower than planned because local technicians were trained early. "
           "Critics note that tariffs remain high for the poorest families and that batteries will need replacing "
           "within eight years. The programme is now being copied in two neighbouring districts.")
BUGGY = "def mean(values):\n    total = 0\n    for v in values:\n        total += v\n    return total / (len(values) + 1)\n"
BUGGY_TEST = ("from buggy import mean\nassert mean([2, 4, 6]) == 4, mean([2, 4, 6])\nassert mean([10]) == 10\n"
              "print('OK')\n")
INVOICE = "INVOICE\nNumber: INV-2031\nDate: 2026-03-14\nCustomer: Mara Tea Estates\nItems: 3 crates\nTotal due: 18,450.00 KES\n"
CONFIG = "[server]\nhost = 127.0.0.1\nport = 8080\n\n[logging]\nlevel = info\nport_note = keep 8080 in the docs\n"


def _num(text):
    return re.sub(r"[,\s]", "", text or "")


def _read(folder, name):
    p = Path(folder) / name
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
    except OSError:
        return None


def _find(folder, name):
    """A file by name anywhere under the task folder (agents sometimes use a sub-folder)."""
    hits = [p for p in Path(folder).rglob(name) if p.is_file()]
    return hits[0] if hits else None


def check_sum(folder, reply):
    return "1284" in _num(reply), "the total 1,284 in the reply"


def check_note(folder, reply):
    text = _read(folder, "notes.txt")
    return bool(text is not None and text.strip() == "KimiLM works"), "notes.txt with exactly the line"


def check_fizz(folder, reply):
    p = _find(folder, "out.txt")
    lines = [x.strip() for x in p.read_text(encoding="utf-8", errors="replace").split("\n") if x.strip()] if p else []
    want = ["FizzBuzz" if i % 15 == 0 else "Fizz" if i % 3 == 0 else "Buzz" if i % 5 == 0 else str(i)
            for i in range(1, 16)]
    return lines == want, "out.txt with the 15 FizzBuzz lines"


def check_bug(folder, reply):
    if not (Path(folder) / "buggy.py").is_file():
        return False, "buggy.py still there"
    (Path(folder) / "test_buggy.py").write_text(BUGGY_TEST, encoding="utf-8")     # the original test, unchanged
    try:
        out = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, '.'); "
                              "exec(open('test_buggy.py').read())"], cwd=str(folder), capture_output=True,
                             text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError):
        return False, "the test ran"
    return out.returncode == 0 and "OK" in out.stdout, "the test passes"


def check_invoice(folder, reply):
    p = _find(folder, "invoice.json")
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p else None
    except (ValueError, OSError):
        data = None
    if not isinstance(data, dict):
        return False, "invoice.json with number, date and total"
    return (str(data.get("number")) == "INV-2031" and str(data.get("date")) == "2026-03-14"
            and _num(str(data.get("total"))).startswith("18450")), "invoice.json with number, date and total"


def check_summary(folder, reply):
    words = len((reply or "").split())
    return 8 <= words <= 90 and "2019" in reply and "42" in reply, "at most 60 words with 2019 and 42%"


def check_sorted(folder, reply):
    f = Path(folder)
    ok = all((f / "data" / n).is_file() for n in ("a.csv", "b.csv")) and \
        all((f / "notes" / n).is_file() for n in ("todo.txt", "ideas.txt")) and \
        not any((f / n).exists() for n in ("a.csv", "b.csv", "todo.txt", "ideas.txt"))
    return ok, "csv files in data/, txt files in notes/"


def check_config(folder, reply):
    text = _read(folder, "config.ini") or ""
    return ("port = 9090" in text and "port = 8080" not in text and "keep 8080 in the docs" in text
            and "host = 127.0.0.1" in text), "port 9090, everything else unchanged"


def check_diff(folder, reply):
    return bool(re.search(r"(?<!\d)7(?!\d)", reply or "")), "line 7 named in the reply"


def check_report(folder, reply):
    p = _find(folder, "report.md")
    text = p.read_text(encoding="utf-8", errors="replace") if p else ""
    return all(r in text for r in ("North", "South", "East")) and "355" in _num(text), \
        "report.md with the three regions and East's average 355"


def check_code(folder, reply):
    return "parts-b.txt" in (reply or ""), "the file parts-b.txt named in the reply"


def check_calc(folder, reply):
    return "17809" in _num(reply), "17,809 in the reply"


# ---------------------------------------------------------------------------------------------- 2.32.0: skills
# Eight more tasks so that each kind of work agents do here is tested at least twice, and the planner can see
# which model is good at what (kimilm/abilities.py). Like the others they need no internet: the "website" is a few
# pages saved in the task folder, read the way an agent reads any page.
SOURCES = {
    "src-1.txt": "Lakeside Water Project, first report (March 2024). Construction began in 2022. The contractor "
                 "estimated the cost at 2.4 million dollars. Pipes reach the north and east wards.\n",
    "src-2.txt": "Audit of the Lakeside Water Project (January 2026). The final cost was 3.1 million dollars, not the "
                 "2.4 million first reported, because the south ward was added in 2024. The audit found no "
                 "missing funds.\n",
    "src-3.txt": "Ward survey, Lakeside (October 2025). 5,860 households now have a tap at home. Residents of the "
                 "south ward report lower pressure in the evenings.\n",
}
SITE = {
    "site/index.html": '<html><body><h1>Rift Valve &amp; Pump Supplies</h1><ul><li><a href="catalogue/index.html">'
                       'Catalogue</a></li><li><a href="reports.html">Reports</a></li><li><a href="sale.html">Last '
                       "year's sale</a></li></ul></body></html>\n",
    "site/sale.html": "<html><body><h1>Sale (2025, ended)</h1><table><tr><td>ZX-4471 valve seat</td><td>KES 990"
                      "</td></tr></table></body></html>\n",
    "site/catalogue/index.html": '<html><body><h1>Catalogue</h1><p><a href="valves.html">Valves and seats</a> · '
                                 '<a href="pumps.html">Pumps</a></p></body></html>\n',
    "site/catalogue/pumps.html": "<html><body><h1>Pumps</h1><p>PX-20 surface pump, KES 18,900</p></body></html>\n",
    "site/catalogue/valves.html": '<html><body><h1>Valves</h1><ul><li><a href="items/zx-4470.html">ZX-4470 gasket'
                                  '</a></li><li><a href="items/zx-4471.html">ZX-4471 valve seat</a></li></ul>'
                                  "</body></html>\n",
    "site/catalogue/items/zx-4470.html": "<html><body><h1>ZX-4470 gasket</h1><p>Price: KES 310</p></body></html>\n",
    "site/catalogue/items/zx-4471.html": "<html><body><h1>ZX-4471 valve seat</h1><p>Current price: <b>KES 1,240"
                                         "</b> (from 1 January 2026)</p></body></html>\n",
    "site/reports.html": '<html><body><h1>Reports</h1><p><a href="reports/energy-2025.html">Energy use by region, '
                         "2025</a></p></body></html>\n",
    "site/reports/energy-2025.html": "<html><body><h1>Energy use by region, 2025</h1><table><tr><th>Region</th>"
                                     "<th>2024 (MWh)</th><th>2025 (MWh)</th></tr><tr><td>Coast</td><td>910</td>"
                                     "<td>870</td></tr><tr><td>Highlands</td><td>640</td><td>1,020</td></tr><tr>"
                                     "<td>Lake</td><td>990</td><td>955</td></tr></table></body></html>\n",
}
CONTACTS = ("Notes from the supplier day. Wanjiru Kamau (wanjiru.kamau@example.org) runs procurement at Mara Tea. "
            "We also met Otieno Odhiambo - his address is o.odhiambo@example.net - about pumps. Finally Achieng Atieno, "
            "achieng@example.com, asked for the catalogue.\n")
NOTICE = ("NOTICE. Water supply interruption. Due to maintenance of the main line the supply in the east ward will "
          "be off on 14 March 2026 from 08:00 to 16:00. Store water in advance. For questions call 0711 555 019. "
          "Management regrets any inconvenience caused.\n")
BRIEF = ("Request (do not do it, only plan it): prepare a market brief on home solar. Collect panel prices from "
         "panels.csv, collect battery prices from batteries.csv, compare the two in a chart, and then write a "
         "two-page brief that uses the comparison.")


def _minutes():
    """About 50,000 characters of meeting minutes with two facts far apart."""
    rows = []
    for i in range(1, 301):
        rows.append(f"Item {i}. The committee reviewed site {(i * 7) % 23 + 1}: meter readings were checked, the "
                    f"fence was inspected and the logbook was signed by the duty officer. No further action.")
        if i == 18:
            rows.append("Item 18b. The backup generator at site 14 was serviced on 3 May 2025.")
        if i == 260:
            rows.append("Item 260b. Site 14's backup generator failed again on 19 September 2025.")
    return "\n".join(rows) + "\n"


def check_sources(folder, reply):
    r = reply or ""
    return ("3.1" in r and "5860" in _num(r) and "src-2" in r and "src-3" in r), \
        "3.1 million (src-2.txt) and 5,860 households (src-3.txt), with their sources"


def check_browse(folder, reply):
    r = reply or ""
    return "1240" in _num(r) and "zx-4471.html" in r.lower(), "KES 1,240 on zx-4471.html"


def check_web_table(folder, reply):
    return "highlands" in (reply or "").lower(), "Highlands"


def check_contacts(folder, reply):
    import csv
    p = _find(folder, "contacts.csv")
    try:
        rows = list(csv.DictReader(p.open(encoding="utf-8"))) if p else []
    except (OSError, csv.Error):
        rows = []
    got = {(r.get("name") or "").strip(): (r.get("email") or "").strip() for r in rows}
    want = {"Wanjiru Kamau": "wanjiru.kamau@example.org", "Otieno Odhiambo": "o.odhiambo@example.net",
            "Achieng Atieno": "achieng@example.com"}
    return got == want, "contacts.csv with name,email for the three people"


def check_rewrite(folder, reply):
    r = reply or ""
    words = len(r.split())
    return (15 <= words <= 95 and "14 March" in r and "0711 555 019" in r
            and "NOTICE" not in r), "a polite email of at most 80 words with 14 March and 0711 555 019"


def check_logic(folder, reply):
    lines = [x for x in (reply or "").strip().splitlines() if x.strip()]
    return bool(lines) and "Amina" in lines[-1] and "Dawit" not in lines[-1], "Amina"


def check_plan(folder, reply):
    p = _find(folder, "plan.json")
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p else None
    except (ValueError, OSError):
        data = None
    if isinstance(data, dict):
        data = data.get("steps") or data.get("plan") or data.get("tasks")
    if not isinstance(data, list) or not 3 <= len(data) <= 8 or not all(isinstance(x, dict) for x in data):
        return False, "plan.json: a list of 3 to 8 steps"
    by = {str(x.get("id")): x for x in data}

    def find(*words):
        return [k for k, x in by.items() if all(w in json.dumps(x).lower() for w in words)]

    def before(a, b, seen=None):          # does step b (transitively) depend on a?
        seen = seen or set()
        for d in by.get(b, {}).get("depends_on") or []:
            d = str(d)
            if d == a or (d not in seen and before(a, d, seen | {d})):
                return True
        return False
    panels, batteries = find("panel"), find("batter")
    starts = [k for k in by if not (by[k].get("depends_on") or [])]
    brief = [k for k in find("brief") if k not in starts]
    ok = (any(k in starts for k in panels) and any(k in starts for k in batteries)
          and any(a != b for a in panels for b in batteries)
          and any(all(before(a, b) for a in (panels[0], batteries[0])) for b in brief))
    return ok, "panels and batteries collected at the same time (no depends_on), the brief after both"


def check_long(folder, reply):
    return bool(re.search(r"(?<!\d)139(?!\d)", reply or "")), "139 days"


TASKS = [
    {"id": "sum", "skills": ["data"], "title": "Add up a column", "harness": "agent", "files": {"sales.csv": SALES},
     "prompt": "What is the total of the amount column in sales.csv? Reply with the number.", "check": check_sum},
    {"id": "note", "skills": ["tools"], "title": "Write a file exactly", "harness": "agent", "files": {},
     "prompt": "Create the file notes.txt in the folder you may use. It must contain exactly this one line: KimiLM works",
     "check": check_note},
    {"id": "fizz", "skills": ["coding"], "title": "Write and run a script", "harness": "coder", "files": {},
     "prompt": "Write fizz.py that prints FizzBuzz for the numbers 1 to 15 (one per line: Fizz for multiples of 3, "
               "Buzz for multiples of 5, FizzBuzz for both, otherwise the number). Run it and save exactly its "
               "output in out.txt.", "check": check_fizz},
    {"id": "bug", "skills": ["coding"], "title": "Fix a bug until the test passes", "harness": "coder",
     "files": {"buggy.py": BUGGY, "test_buggy.py": BUGGY_TEST},
     "prompt": "Running test_buggy.py fails. Find the bug in buggy.py and fix it so the test prints OK. Do not "
               "change the test.", "check": check_bug},
    {"id": "invoice", "skills": ["extraction"], "title": "Extract fields to JSON", "harness": "agent", "files": {"invoice.txt": INVOICE},
     "prompt": "Read invoice.txt and save invoice.json with exactly these keys: number, date (as written), total "
               "(a number, no currency).", "check": check_invoice},
    {"id": "summary", "skills": ["writing"], "title": "Summarise within a limit", "harness": "agent", "files": {"article.txt": ARTICLE},
     "prompt": "Summarise article.txt in at most 60 words. The summary must mention the year the programme began "
               "and the share of households connected at the end. Reply with the summary only.",
     "check": check_summary},
    {"id": "sort", "skills": ["tools"], "title": "Organise files", "harness": "agent",
     "files": {"a.csv": "x\n1\n", "b.csv": "x\n2\n", "todo.txt": "call the supplier\n", "ideas.txt": "a new report\n"},
     "prompt": "Tidy this folder: move every .csv file into a sub-folder called data and every .txt file into a "
               "sub-folder called notes.", "check": check_sorted},
    {"id": "config", "skills": ["tools"], "title": "Change one setting only", "harness": "agent", "files": {"config.ini": CONFIG},
     "prompt": "In config.ini change the server port from 8080 to 9090. Change nothing else.", "check": check_config},
    {"id": "diff", "skills": ["reasoning"], "title": "Find the line that differs", "harness": "agent",
     "files": {"first.txt": "\n".join(f"line {i} of the list" for i in range(1, 13)) + "\n",
               "second.txt": "\n".join(("line 7 of the lot" if i == 7 else f"line {i} of the list")
                                       for i in range(1, 13)) + "\n"},
     "prompt": "first.txt and second.txt differ in exactly one line. Which line number? Reply with the number.",
     "check": check_diff},
    {"id": "report", "skills": ["data", "planning"], "title": "A planned run: compute, then report", "harness": "agent", "files": {"sales.csv": SALES},
     "prompt": "Work on sales.csv in these steps: first compute the average amount per region with Python, then "
               "write report.md with a table of the three regions and their averages, and finally say which region "
               "has the highest average.", "check": check_report},
    {"id": "find", "skills": ["research"], "title": "Find a code in several files", "harness": "agent",
     "files": {"parts-a.txt": "AB-1001 bolt\nAB-1002 nut\n", "parts-b.txt": "ZX-4470 gasket\nZX-4471 valve seat\n",
               "parts-c.txt": "QR-77 hose\n", "readme.txt": "Part lists for the workshop.\n"},
     "prompt": "Which file in this folder mentions the part code ZX-4471? Reply with the file name.",
     "check": check_code},
    {"id": "calc", "skills": ["data"], "title": "Compute with a tool", "harness": "agent", "files": {},
     "prompt": "Use Python to compute 37 * 481 + 12 and reply with the result.", "check": check_calc},
    # 2.32.0
    {"id": "sources", "skills": ["research"], "title": "Combine three sources", "harness": "agent", "files": SOURCES,
     "prompt": "Using the three sources in this folder, what was the final cost of the Lakeside Water Project and how "
               "many households have a tap at home? Where sources disagree, go by the latest. Name the source file "
               "for each figure.", "check": check_sources},
    {"id": "browse", "skills": ["browsing"], "title": "Follow links on a website", "harness": "agent", "files": SITE,
     "prompt": "The site folder holds a supplier's website; start at site/index.html and follow its links as a "
               "browser would. What is the current price of the ZX-4471 valve seat? Reply with the price and the "
               "name of the page that shows it.", "check": check_browse},
    {"id": "webtable", "skills": ["browsing", "extraction"], "title": "Read a table on a web page", "harness": "agent",
     "files": SITE, "prompt": "On the website in the site folder (start at site/index.html), find the report on energy "
                              "use by region. Which region used the most energy in 2025? Reply with the region.",
     "check": check_web_table},
    {"id": "contacts", "skills": ["extraction"], "title": "Names and emails to CSV", "harness": "agent",
     "files": {"notes.txt": CONTACTS},
     "prompt": "Save contacts.csv with a header row name,email and one row per person mentioned in notes.txt "
               "(full name as written, their email).", "check": check_contacts},
    {"id": "rewrite", "skills": ["writing"], "title": "Rewrite for readers", "harness": "agent",
     "files": {"notice.txt": NOTICE},
     "prompt": "Rewrite notice.txt as a short, polite email to customers (at most 80 words, no heading in capitals). "
               "Keep the date and the phone number exactly. Reply with the email only.", "check": check_rewrite},
    {"id": "logic", "skills": ["reasoning"], "title": "Reason it out", "harness": "agent", "files": {},
     "prompt": "Amina is older than Bilal. Chebet is younger than Bilal. Dawit is older than Amina. Esther is younger "
               "than Chebet. Who is the second oldest? Think it through, then give the name alone on the last line.",
     "check": check_logic},
    {"id": "plan", "skills": ["planning"], "title": "Plan parallel work", "harness": "agent", "files": {},
     "prompt": BRIEF + " Write plan.json: a JSON list of steps, each {\"id\", \"goal\", \"depends_on\": [ids of "
               "the steps it needs]}. Steps that do not need each other must have an empty depends_on so they can "
               "run at the same time.", "check": check_plan},
    {"id": "long", "skills": ["long"], "title": "Two details in a long document", "harness": "agent",
     "files": {"minutes.txt": _minutes()},
     "prompt": "minutes.txt is a long set of meeting minutes. How many days passed between the service of the backup "
               "generator at site 14 and its next failure? Reply with the number of days.", "check": check_long},
]


class Evals:
    def __init__(self, app):
        self.app, self.db = app, app.db
        self.current = None           # the run in progress: {"id", "done", "total", "task", "chat_id", "job_id"}
        # 2.26.0: a run that was going when the app closed is cut off, not forever "running": it can be continued
        for row in self.db.all("SELECT id FROM eval_runs WHERE status='running'"):
            self.db.update("eval_runs", row["id"], {"status": "interrupted"})

    def tasks(self):
        return [{"id": t["id"], "title": t["title"], "harness": t["harness"], "skills": t.get("skills") or []}
                for t in TASKS]

    def list(self, limit=12):
        rows = self.db.all("SELECT * FROM eval_runs ORDER BY created DESC LIMIT ?", (limit,))
        for r in rows:
            r["can_continue"] = r.get("status") in ("stopped", "interrupted") and bool(self._left(r)) \
                and not self.current
        return rows

    def start(self, model=None, only=None, tasks=None):
        if self.current:
            raise ValueError("An evaluation is already running.")
        # 2.32.0: `only` may name tasks or skills ("research", "browsing"): every task that tests one of them
        chosen = [t for t in (tasks or TASKS) if not only or t["id"] in only or set(t.get("skills") or []) & set(only)]
        if not chosen:
            raise ValueError("No such tasks.")
        model = model or self.db.setting("default_model")
        run_id = new_id("eval_")
        self.db.insert("eval_runs", {"id": run_id, "created": now(), "version": VERSION, "model": model,
                                     "status": "running", "results": [], "summary": {}, "settings": self.settings(),
                                     "tasks": [t["id"] for t in chosen]})
        return self._launch(run_id, model, chosen, [], len(chosen))

    def _left(self, row):
        done = {r.get("id") for r in row.get("results") or []}
        wanted = row.get("tasks") or [t["id"] for t in TASKS]
        return [t for t in TASKS if t["id"] in wanted and t["id"] not in done]

    def continue_run(self, run_id):
        """2.26.0: go on with a stopped or cut-off evaluation. Finished tasks are kept; the task that was cut off
        and the ones after it run now, with the same model, into the same row."""
        if self.current:
            raise ValueError("An evaluation is already running.")
        row = self.db.get("eval_runs", run_id)
        if not row:
            raise KeyError(run_id)
        if row.get("status") not in ("stopped", "interrupted"):
            raise ValueError("Only a stopped or cut-off evaluation can be continued.")
        left = self._left(row)
        if not left:
            raise ValueError("Every task of this evaluation has run.")
        results = list(row.get("results") or [])
        self.db.update("eval_runs", run_id, {"status": "running"})
        return self._launch(run_id, row.get("model"), left, results, len(results) + len(left))

    def delete(self, run_id):
        """Take an evaluation off the list, with its task chats and files. A running one is stopped first."""
        row = self.db.get("eval_runs", run_id)
        if not row:
            raise KeyError(run_id)
        if self.current and self.current.get("id") == run_id:
            raise ValueError("Stop the evaluation before deleting it.")
        for r in row.get("results") or []:
            cid = r.get("chat_id")
            if cid:
                self.db.run("DELETE FROM messages WHERE chat_id=?", (cid,))
                self.db.run("DELETE FROM memories WHERE scope='chat' AND scope_id=?", (cid,))
                self.db.delete("chats", cid)
        shutil.rmtree(self.app.paths.data / "evals" / run_id, ignore_errors=True)
        self.db.delete("eval_runs", run_id)
        return {"ok": True}

    def pause(self):
        """Pause the task being done: its agent stops at its next step and gives its model back."""
        from .orchestrator import pause_chat
        cur = self.current or {}
        if not cur.get("chat_id"):
            raise ValueError("No evaluation task is running.")
        pause_chat(self.app, cur["chat_id"], "evaluation paused")
        cur["paused"] = True
        return {"ok": True}

    def resume(self):
        from .orchestrator import resume_chat
        cur = self.current or {}
        if cur.get("chat_id"):
            resume_chat(self.app, cur["chat_id"])
        cur["paused"] = False
        return {"ok": True}

    def _launch(self, run_id, model, chosen, results, total):
        self.current = {"id": run_id, "done": len(results), "total": total, "task": chosen[0]["title"],
                        "paused": False}
        job = self.app.jobs.create("eval", "Evaluation", lambda row: self._run(run_id, model, chosen, results, total),
                                   model=model, payload={"eval": run_id})
        self.current["job_id"] = job["id"]
        return {"id": run_id, "job_id": job["id"], "total": total}

    async def _one(self, run_id, model, task, root):
        folder = root / task["id"]
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in task["files"].items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)      # 2.32.0: pages in sub-folders
            (folder / name).write_text(text, encoding="utf-8")
        from .app_state import clean_limits
        chat = self.app.create_chat(title=f"Evaluation: {task['title']}", model=model, harness=task["harness"],
                                    folders=[str(folder)], origin="eval",
                                    settings=clean_limits({"tools": ["files", "python", "agents"]}))   # no web
        self.db.set_setting(f"chat_auto_approve:{chat['id']}", True)
        started = time.monotonic()
        result = {"id": task["id"], "title": task["title"], "chat_id": chat["id"], "passed": False,
                  "skills": list(task.get("skills") or [])}
        if self.current:
            self.current["chat_id"] = chat["id"]
        try:
            job = self.app.start_turn(chat["id"], task["prompt"], kind="agent",
                                      payload={"auto_approve": True})
            handle = self.app.jobs.tasks.get(job["id"])
            if handle:
                from .orchestrator import is_paused
                allowed = float(self.db.setting("eval_task_minutes", 45)) * 60
                worked, last = 0.0, time.monotonic()
                try:
                    while not handle.done():
                        await asyncio.wait({asyncio.shield(handle)}, timeout=2)
                        t = time.monotonic()
                        if not is_paused(self.app, chat["id"]):   # time paused does not count against the limit
                            worked += t - last
                        last = t
                        if worked > allowed and not handle.done():
                            self.app.jobs.cancel(job["id"])
                            result["error"] = "took too long and was stopped"
                            break
                except asyncio.CancelledError:
                    from .orchestrator import resume_chat
                    resume_chat(self.app, chat["id"])           # a paused task must wake to see the stop
                    self.app.jobs.cancel(job["id"])
                    raise
            msg = self.db.one("SELECT content, meta_json FROM messages WHERE chat_id=? AND role='assistant' "
                              "ORDER BY created DESC, rowid DESC LIMIT 1", (chat["id"],)) or {}
            meta = msg.get("meta") or {}
            stats = meta.get("stats") or {}
            reply = msg.get("content") or ""
            try:
                passed, expected = await asyncio.to_thread(task["check"], folder, reply)
            except Exception as exc:
                passed, expected = False, f"the check failed to run ({type(exc).__name__})"
            team = next((s for s in meta.get("steps", []) if s.get("type") == "orchestra"), None)
            sub = (team or {}).get("totals", {}).get("tokens", 0)
            result.update(passed=bool(passed), expected=expected, error=result.get("error") or meta.get("error"),
                          read=int(stats.get("prompt_tokens") or 0), cached=int(stats.get("cached_tokens") or 0),
                          written=int(stats.get("output_tokens") or 0), agent_tokens=int(sub or 0),
                          calls=int(stats.get("model_calls") or 0),
                          tools=sum(1 for s in meta.get("steps", []) if s.get("type") == "tool"),
                          planned=bool(team), reply=reply[:400])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["seconds"] = round(time.monotonic() - started, 1)
        return result

    async def _run(self, run_id, model, tasks, results=None, total=None):
        root = self.app.paths.data / "evals" / run_id
        results, status = list(results or []), "done"
        total = total or len(tasks)
        before = len(results)
        # 2.23.0: start with only the model under test loaded (fair numbers, memory for it), and give the memory
        # back when the run ends, instead of leaving models loaded until the idle timer
        await self._release(keep=(model,))
        try:
            for i, task in enumerate(tasks):
                self.current.update(done=before + i, task=task["title"])
                self.app.bus.publish("eval", run_id=run_id, done=before + i, total=total, task=task["title"])
                results.append(await self._one(run_id, model, task, root))
                self.db.update("eval_runs", run_id, {"results": results, "summary": self.summary(results, total)})
        except asyncio.CancelledError:
            status = "stopped"
            raise
        finally:
            self.db.update("eval_runs", run_id, {"status": status, "results": results,
                                                 "summary": self.summary(results, total)})
            self.current = None
            self.app.bus.publish("eval", run_id=run_id, done=len(results), total=total, finished=True)
            if not self.db.setting("eval_keep_files", False):
                shutil.rmtree(root, ignore_errors=True)
            await self._release()

    async def _release(self, keep=()):
        """Unload idle models (those no chat or task is using), except `keep`. Never fails the run."""
        try:
            gone = await self.app.runtime.release(keep=tuple(k for k in keep if k))
            if gone:
                self.app.bus.publish("eval_released", models=gone)
        except Exception:
            pass

    # the settings that change how tasks are done: stored with each run, so two runs can be told apart
    SETTINGS = ("agent_order", "team_mode", "step_rounds", "verify_results", "check_model", "lean_tools",
                "escalate_models", "fit_context", "prompt_snapshots", "reading_budget_seconds",
                "writing_budget_seconds", "learned_model_choice", "max_reply_tokens",
                "choose_step_models", "planner_model")

    def settings(self):
        return {k: self.db.setting(k) for k in self.SETTINGS}

    @staticmethod
    def summary(results, total):
        read = sum(r.get("read", 0) for r in results)
        cached = sum(r.get("cached", 0) for r in results)
        return {"passed": sum(1 for r in results if r.get("passed")), "ran": len(results), "total": total,
                "seconds": round(sum(r.get("seconds", 0) for r in results)),
                "read": read, "cached": cached, "written": sum(r.get("written", 0) for r in results),
                "agent_tokens": sum(r.get("agent_tokens", 0) for r in results),
                "cache_percent": round(100 * cached / (read + cached)) if read + cached else 0}
