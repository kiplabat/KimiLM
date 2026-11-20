"""An evaluation built into the app (2.7.0): twelve small, realistic tasks with answers that code can check.

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


TASKS = [
    {"id": "sum", "title": "Add up a column", "harness": "agent", "files": {"sales.csv": SALES},
     "prompt": "What is the total of the amount column in sales.csv? Reply with the number.", "check": check_sum},
    {"id": "note", "title": "Write a file exactly", "harness": "agent", "files": {},
     "prompt": "Create the file notes.txt in the folder you may use. It must contain exactly this one line: KimiLM works",
     "check": check_note},
    {"id": "fizz", "title": "Write and run a script", "harness": "coder", "files": {},
     "prompt": "Write fizz.py that prints FizzBuzz for the numbers 1 to 15 (one per line: Fizz for multiples of 3, "
               "Buzz for multiples of 5, FizzBuzz for both, otherwise the number). Run it and save exactly its "
               "output in out.txt.", "check": check_fizz},
    {"id": "bug", "title": "Fix a bug until the test passes", "harness": "coder",
     "files": {"buggy.py": BUGGY, "test_buggy.py": BUGGY_TEST},
     "prompt": "Running test_buggy.py fails. Find the bug in buggy.py and fix it so the test prints OK. Do not "
               "change the test.", "check": check_bug},
    {"id": "invoice", "title": "Extract fields to JSON", "harness": "agent", "files": {"invoice.txt": INVOICE},
     "prompt": "Read invoice.txt and save invoice.json with exactly these keys: number, date (as written), total "
               "(a number, no currency).", "check": check_invoice},
    {"id": "summary", "title": "Summarise within a limit", "harness": "agent", "files": {"article.txt": ARTICLE},
     "prompt": "Summarise article.txt in at most 60 words. The summary must mention the year the programme began "
               "and the share of households connected at the end. Reply with the summary only.",
     "check": check_summary},
    {"id": "sort", "title": "Organise files", "harness": "agent",
     "files": {"a.csv": "x\n1\n", "b.csv": "x\n2\n", "todo.txt": "call the supplier\n", "ideas.txt": "a new report\n"},
     "prompt": "Tidy this folder: move every .csv file into a sub-folder called data and every .txt file into a "
               "sub-folder called notes.", "check": check_sorted},
    {"id": "config", "title": "Change one setting only", "harness": "agent", "files": {"config.ini": CONFIG},
     "prompt": "In config.ini change the server port from 8080 to 9090. Change nothing else.", "check": check_config},
    {"id": "diff", "title": "Find the line that differs", "harness": "agent",
     "files": {"first.txt": "\n".join(f"line {i} of the list" for i in range(1, 13)) + "\n",
               "second.txt": "\n".join(("line 7 of the lot" if i == 7 else f"line {i} of the list")
                                       for i in range(1, 13)) + "\n"},
     "prompt": "first.txt and second.txt differ in exactly one line. Which line number? Reply with the number.",
     "check": check_diff},
    {"id": "report", "title": "A planned run: compute, then report", "harness": "agent", "files": {"sales.csv": SALES},
     "prompt": "Work on sales.csv in these steps: first compute the average amount per region with Python, then "
               "write report.md with a table of the three regions and their averages, and finally say which region "
               "has the highest average.", "check": check_report},
    {"id": "find", "title": "Find a code in several files", "harness": "agent",
     "files": {"parts-a.txt": "AB-1001 bolt\nAB-1002 nut\n", "parts-b.txt": "ZX-4470 gasket\nZX-4471 valve seat\n",
               "parts-c.txt": "QR-77 hose\n", "readme.txt": "Part lists for the workshop.\n"},
     "prompt": "Which file in this folder mentions the part code ZX-4471? Reply with the file name.",
     "check": check_code},
    {"id": "calc", "title": "Compute with a tool", "harness": "agent", "files": {},
     "prompt": "Use Python to compute 37 * 481 + 12 and reply with the result.", "check": check_calc},
]


class Evals:
    def __init__(self, app):
        self.app, self.db = app, app.db
        self.current = None           # the run in progress: {"id", "done", "total", "task"}

    def tasks(self):
        return [{"id": t["id"], "title": t["title"], "harness": t["harness"]} for t in TASKS]

    def list(self, limit=12):
        return self.db.all("SELECT * FROM eval_runs ORDER BY created DESC LIMIT ?", (limit,))

    def start(self, model=None, only=None, tasks=None):
        if self.current:
            raise ValueError("An evaluation is already running.")
        chosen = [t for t in (tasks or TASKS) if not only or t["id"] in only]
        if not chosen:
            raise ValueError("No such tasks.")
        model = model or self.db.setting("default_model")
        run_id = new_id("eval_")
        self.db.insert("eval_runs", {"id": run_id, "created": now(), "version": VERSION, "model": model,
                                     "status": "running", "results": [], "summary": {}, "settings": self.settings()})
        self.current = {"id": run_id, "done": 0, "total": len(chosen), "task": chosen[0]["title"]}
        job = self.app.jobs.create("eval", "Evaluation", lambda row: self._run(run_id, model, chosen),
                                   model=model, payload={"eval": run_id})
        self.current["job_id"] = job["id"]
        return {"id": run_id, "job_id": job["id"], "total": len(chosen)}

    async def _one(self, run_id, model, task, root):
        folder = root / task["id"]
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in task["files"].items():
            (folder / name).write_text(text, encoding="utf-8")
        from .app_state import clean_limits
        chat = self.app.create_chat(title=f"Evaluation: {task['title']}", model=model, harness=task["harness"],
                                    folders=[str(folder)], origin="eval",
                                    settings=clean_limits({"tools": ["files", "python", "agents"]}))   # no web
        self.db.set_setting(f"chat_auto_approve:{chat['id']}", True)
        started = time.monotonic()
        result = {"id": task["id"], "title": task["title"], "chat_id": chat["id"], "passed": False}
        try:
            job = self.app.start_turn(chat["id"], task["prompt"], kind="agent",
                                      payload={"auto_approve": True})
            handle = self.app.jobs.tasks.get(job["id"])
            if handle:
                try:
                    await asyncio.wait_for(asyncio.shield(handle),
                                           float(self.db.setting("eval_task_minutes", 45)) * 60)
                except asyncio.TimeoutError:
                    self.app.jobs.cancel(job["id"])
                    result["error"] = "took too long and was stopped"
                except asyncio.CancelledError:
                    self.app.jobs.cancel(job["id"])
                    raise
                except Exception:
                    pass
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

    async def _run(self, run_id, model, tasks):
        root = self.app.paths.data / "evals" / run_id
        results, status = [], "done"
        # 2.23.0: start with only the model under test loaded (fair numbers, memory for it), and give the memory
        # back when the run ends, instead of leaving models loaded until the idle timer
        await self._release(keep=(model,))
        try:
            for i, task in enumerate(tasks):
                self.current.update(done=i, task=task["title"])
                self.app.bus.publish("eval", run_id=run_id, done=i, total=len(tasks), task=task["title"])
                results.append(await self._one(run_id, model, task, root))
                self.db.update("eval_runs", run_id, {"results": results, "summary": self.summary(results, len(tasks))})
        except asyncio.CancelledError:
            status = "stopped"
            raise
        finally:
            self.db.update("eval_runs", run_id, {"status": status, "results": results,
                                                 "summary": self.summary(results, len(tasks))})
            self.current = None
            self.app.bus.publish("eval", run_id=run_id, done=len(results), total=len(tasks), finished=True)
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
                "writing_budget_seconds", "learned_model_choice", "max_reply_tokens")

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
