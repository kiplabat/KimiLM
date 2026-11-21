"""Unit checks that need no running engine (2.23.0): drives that are not mounted, plugins installed by the
operating system (KimiOS), and the evaluation giving memory back.

    cd engine && python -m pytest -q tests/test_units.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

from dva import hardware, plugins  # noqa: E402

posix_only = pytest.mark.skipif(os.name != "posix", reason="removable-media folders are a Linux idea")


# ---------------------------------------------------------------------------------------------- drives
@posix_only
def test_unmounted_drive_names_the_label_of_a_missing_media_path():
    assert hardware.unmounted_drive("/run/media/labatt/Labatt/models/x.gguf") == "Labatt"
    assert hardware.unmounted_drive("/media/labatt/Data Drive/m.gguf") == "Data Drive"


@posix_only
def test_unmounted_drive_ignores_other_paths(tmp_path):
    existing = tmp_path / "m.gguf"
    existing.write_text("x")
    assert hardware.unmounted_drive(str(existing)) is None            # the file is there
    assert hardware.unmounted_drive("/home/someone/models/x.gguf") is None   # not a media folder
    assert hardware.unmounted_drive("/run/media/labatt") is None      # no drive in the path


LSBLK = {"blockdevices": [
    {"path": "/dev/sda", "label": None, "mountpoint": None, "children": [
        {"path": "/dev/sda1", "label": "SYSTEM", "mountpoint": "/boot/efi"},
        {"path": "/dev/sda5", "label": "Labatt", "mountpoint": None}]},
    {"path": "/dev/sdb", "label": None, "mountpoint": None, "children": [
        {"path": "/dev/sdb1", "label": "Mounted", "mountpoint": "/run/media/u/Mounted"}]}]}


def fake_run(calls, udisks_out="Mounted /dev/sda5 at /run/media/labatt/Labatt\n"):
    def run(cmd, timeout=8):
        calls.append(cmd)
        if cmd[0] == "lsblk":
            return json.dumps(LSBLK)
        if cmd[0] == "udisksctl":
            return udisks_out
        return ""
    return run


def test_block_device_is_found_by_label_among_children():
    assert hardware._block_device_with_label("Labatt", fake_run([])) == "/dev/sda5"
    assert hardware._block_device_with_label("Mounted", fake_run([])) is None       # already mounted
    assert hardware._block_device_with_label("Nope", fake_run([])) is None


@posix_only
def test_mount_drive_asks_udisks_for_the_right_device(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []
    ok, why = hardware.mount_drive("/run/media/labatt/Labatt/models/x.gguf", fake_run(calls))
    assert ["udisksctl", "mount", "-b", "/dev/sda5"] in calls
    assert not ok and "could not be mounted" in why            # the file is still not there in this test

    calls.clear()
    ok, why = hardware.mount_drive("/run/media/labatt/Gone/x.gguf", fake_run(calls))
    assert not ok and "not connected" in why and not any(c[0] == "udisksctl" for c in calls)


def test_mount_drive_does_nothing_for_ordinary_paths(tmp_path):
    calls = []
    assert hardware.mount_drive(str(tmp_path / "missing.gguf"), fake_run(calls)) == (False, "")
    assert calls == []


# ---------------------------------------------------------------------------------------------- system plugins
class FakeDB:
    def __init__(self):
        self.rows = {}

    def get(self, table, key, key_name=None, **kw):
        return self.rows.get((table, key))

    def item_enabled(self, kind, item_id, default=True):
        return default


class FakePaths:
    def __init__(self, data):
        self.data = data


def write_plugin(folder, pid, name, **extra):
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps({"id": pid, "name": name, "version": "1.0", **extra}))


def test_plugins_installed_by_the_system_are_listed_with_their_server(tmp_path, monkeypatch):
    system = tmp_path / "system"
    write_plugin(system / "kimios", "kimios", "KimiOS",
                 mcpServers={"kimios": {"command": "/usr/libexec/kimios/kimios-mcp", "args": []}})
    write_plugin(tmp_path / "data" / "plugins" / "mine", "mine", "Mine")
    monkeypatch.setenv("DVA_SYSTEM_PLUGINS", str(system))
    p = plugins.Plugins(FakePaths(tmp_path / "data"), FakeDB(), secrets=None, bus=None)
    found = {x["id"]: x for x in p.all()}
    assert found["kimios"]["source"] == "system" and found["kimios"]["enabled"]
    assert found["mine"]["source"] == "user"
    conn = [c for c in p.connectors() if c["plugin"] == "kimios"]
    assert len(conn) == 1 and conn[0]["command"] == "/usr/libexec/kimios/kimios-mcp" and conn[0]["ready"]
    with pytest.raises(ValueError):
        p.uninstall("kimios")                  # part of the operating system: switch off, not remove
    assert conn[0]["system"] is True
    from dva import mcp
    connectors = mcp.Connectors.__new__(mcp.Connectors)
    connectors.plugins = p
    assert connectors.system_ids() == ["kimios:kimios"]      # every agent chat gets it (2.24.0)


def test_no_system_plugin_folder_is_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("DVA_SYSTEM_PLUGINS", str(tmp_path / "does-not-exist"))
    p = plugins.Plugins(FakePaths(tmp_path / "data"), FakeDB(), secrets=None, bus=None)
    assert p.all() == []


# ---------------------------------------------------------------------------------------------- evaluation
def test_evaluation_releases_other_models_first_and_all_idle_models_at_the_end(tmp_path):
    from dva import evals

    class Runtime:
        def __init__(self):
            self.calls = []

        async def release(self, only=None, keep=()):
            self.calls.append(tuple(keep))
            return ["other"] if keep else ["model-a"]

    class Bus:
        def __init__(self):
            self.events = []

        def publish(self, name, **kw):
            self.events.append(name)

    class DB:
        def update(self, *a, **k):
            pass

        def all(self, *a, **k):
            return []

        def setting(self, key, default=None):
            return default

    class App:
        pass

    app = App()
    app.runtime, app.bus, app.db = Runtime(), Bus(), DB()
    app.paths = FakePaths(tmp_path)
    e = evals.Evals(app)
    e.current = {}

    async def one(run_id, model, task, root):
        return {"id": task["id"], "passed": True, "seconds": 1}
    e._one = one
    asyncio.run(e._run("eval_x", "model-a", [{"id": "t1", "title": "T1"}]))
    assert app.runtime.calls == [("model-a",), ()]
    assert app.bus.events.count("eval_released") == 2


# ---------------------------------------------------------------------------------------------- Windows memory limit
def test_engine_memory_limit_is_a_share_of_the_computers_memory():
    import psutil
    from dva import app_state

    class DB:
        def __init__(self, value):
            self.value = value

        def setting(self, key, default=None):
            return self.value if key == "engine_memory_percent" else default

    total = psutil.virtual_memory().total
    limit = app_state.App.memory_limit
    assert limit(type("A", (), {"db": DB(85)})()) == int(total * 0.85)
    assert limit(type("A", (), {"db": DB(0)})()) == 0                 # turned off
    assert limit(type("A", (), {"db": DB("junk")})()) == int(total * 0.85)
    assert limit(type("A", (), {"db": DB(150)})()) == 0               # nonsense: no limit rather than a wrong one


def test_contain_does_nothing_outside_windows():
    from dva import procs
    if os.name != "nt":
        assert procs.contain(10 ** 9) is None


# ---------------------------------------------------------------------------------------------- exploring while idle
class ExploreDB:
    def __init__(self, models, settings=None, active_jobs=0, last_job=None, evaluated=()):
        self.models, self.settings, self.active_jobs, self.last_job = models, dict(settings or {}), active_jobs, last_job
        self.evaluated, self.eval_rows = list(evaluated), {}

    def setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def one(self, sql, params=()):
        if "COUNT(*)" in sql:
            return {"n": self.active_jobs}
        return {"t": self.last_job}

    def all(self, sql, params=()):
        if "eval_runs" in sql:
            return [{"model": m} for m in self.evaluated]
        return list(self.models)

    def get(self, table, key, **kw):
        return self.eval_rows.get(key)


def explore_app(tmp_path, monkeypatch, **db_kw):
    import time as _time
    from dva import explore, hardware
    model_file = tmp_path / "m.gguf"
    model_file.write_text("x")
    models = db_kw.pop("models", [{"id": "qwen", "path": str(model_file), "bench": {}},
                                  {"id": "cloud:x", "path": "", "bench": {}}])
    db = ExploreDB(models, **db_kw)

    class Tunes:
        def __init__(self):
            self.tunes, self.started, self.stopped = {}, [], []

        def tune(self, model_id, llm):
            self.started.append(model_id)
            self.tunes[model_id] = {"state": "done", "best": "CPU only"}

        def stop_tune(self, model_id):
            self.stopped.append(model_id)
            self.tunes[model_id]["state"] = "stopped"

    class Runtime:
        servers = {}
        last_used = _time.monotonic() - 3600          # idle for an hour

    class Bus:
        def __init__(self):
            self.events = []

        def publish(self, name, **kw):
            self.events.append((name, kw))

    class App:
        pass
    app = App()
    app.db, app.models, app.runtime, app.bus, app.llm = db, Tunes(), Runtime(), Bus(), None
    app.evals = type("E", (), {"current": None})()
    app.log = lambda msg: None
    monkeypatch.setattr(hardware, "power", lambda: {"on_ac": True})
    monkeypatch.setattr(explore.Explorer, "quiet_processor", staticmethod(lambda: True))
    monkeypatch.setattr(explore.Explorer, "desktop_idle_minutes", staticmethod(lambda: None))
    return app, explore.Explorer(app)


def test_explorer_measures_an_unmeasured_local_model_when_idle(tmp_path, monkeypatch):
    app, ex = explore_app(tmp_path, monkeypatch)
    model, why = ex.check()
    assert why is None and model["id"] == "qwen"
    entry = asyncio.run(ex.explore(model))
    assert app.models.started == ["qwen"] and entry["result"] == "done" and entry["best"] == "CPU only"
    assert app.db.settings["explore_history"][-1]["model_id"] == "qwen"
    assert ex.check() == (None, "already explored in the last day")


def test_explorer_waits_for_quiet(tmp_path, monkeypatch):
    from dva import hardware
    app, ex = explore_app(tmp_path, monkeypatch, active_jobs=1)
    assert ex.check()[1] == "1 chat or task running"
    app, ex = explore_app(tmp_path, monkeypatch, settings={"explore_when_idle": False})
    assert ex.check()[1] == "switched off"
    app, ex = explore_app(tmp_path, monkeypatch)
    monkeypatch.setattr(hardware, "power", lambda: {"on_ac": False})
    assert ex.check()[1] == "on battery"
    app, ex = explore_app(tmp_path, monkeypatch)
    import time as _time
    app.runtime.last_used = _time.monotonic() - 60
    assert ex.check()[1].startswith("waiting for 30 idle minutes")


def test_explorer_skips_measured_and_missing_models(tmp_path, monkeypatch):
    from dva.db import now
    app, ex = explore_app(tmp_path, monkeypatch, models=[
        {"id": "fresh", "path": str(tmp_path / "m.gguf"), "bench": {"tune": {"at": now()}}},
        {"id": "gone", "path": "/run/media/u/Unplugged/x.gguf", "bench": {}}])
    assert ex.check()[0]["_what"] == "evaluation"          # measured, but never evaluated
    app, ex = explore_app(tmp_path, monkeypatch, evaluated=["fresh"], models=[
        {"id": "fresh", "path": str(tmp_path / "m.gguf"), "bench": {"tune": {"at": now()}}}])
    assert ex.check() == (None, "every local model is measured and evaluated")
    app, ex = explore_app(tmp_path, monkeypatch, settings={"explore_last_evaluation": now()}, models=[
        {"id": "fresh", "path": str(tmp_path / "m.gguf"), "bench": {"tune": {"at": now()}}}])
    assert ex.check() == (None, "every local model is measured and evaluated")      # one a week


def test_explorer_stops_when_the_user_starts_something(tmp_path, monkeypatch):
    app, ex = explore_app(tmp_path, monkeypatch)

    def tune(model_id, llm):
        app.models.started.append(model_id)
        app.models.tunes[model_id] = {"state": "running"}
        app.db.active_jobs = 1                       # the user starts a chat right away
    app.models.tune = tune
    from dva import explore
    real_sleep = asyncio.sleep
    monkeypatch.setattr(explore.asyncio, "sleep", lambda s: real_sleep(0))
    entry = asyncio.run(ex.explore(app.db.models[0]))
    assert app.models.stopped == ["qwen"] and entry["stopped"] == "a chat or task started"


# ---------------------------------------------------------------------------------------------- suggestions
def _at(day, hour):
    from datetime import datetime
    return datetime(2026, 11, day, hour, 5).astimezone().isoformat()     # local time, stored with its zone


def test_a_request_made_on_three_weekdays_becomes_a_schedule_suggestion():
    from dva import suggest
    rows = [{"content": "Summarise the new files in my reports folder", "created": _at(16, 8)},   # Monday
            {"content": "summarise the NEW files in my reports folder please", "created": _at(17, 8)},
            {"content": "Summarise new files in my reports folder (3 of them)", "created": _at(18, 9)},
            {"content": "Write a poem about rain", "created": _at(18, 10)},
            {"content": "hi", "created": _at(19, 8)}]
    found = suggest.repeated_requests(rows)
    assert len(found) == 1
    s = found[0]
    assert (s["days"], s["weekdays_only"], s["hour"], s["cron"]) == (3, True, 8, "0 8 * * 1-5")
    assert "reports folder" in s["prompt"]
    assert suggest.suggestion_id(s["prompt"]) == suggest.suggestion_id(rows[0]["content"] + " (3 of them)")


def test_the_same_day_twice_is_not_a_habit():
    from dva import suggest
    rows = [{"content": "Check the server logs for errors", "created": _at(16, 8)},
            {"content": "Check the server logs for errors", "created": _at(16, 15)},
            {"content": "Check the server logs for errors", "created": _at(17, 8)}]
    assert suggest.repeated_requests(rows) == []


def test_suggestions_are_off_until_switched_on():
    from dva import suggest

    class DB:
        def setting(self, key, default=None):
            return default
    app = type("A", (), {"db": DB()})()
    assert suggest.Suggestions(app).list() == {"enabled": False, "items": []}


def test_age_reads_utc_and_zoned_times():
    from datetime import datetime, timedelta, timezone
    from dva.explore import age
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    assert abs(age(hour_ago.isoformat()).total_seconds() - 3600) < 5
    assert abs(age(hour_ago.replace(tzinfo=None).isoformat()).total_seconds() - 3600) < 5      # no zone: UTC
    assert abs(age(hour_ago.astimezone(timezone(timedelta(hours=3))).isoformat()).total_seconds() - 3600) < 5


def test_explorer_evaluates_and_stops_when_someone_uses_the_computer(tmp_path, monkeypatch):
    from dva import explore
    from dva.db import now
    app, ex = explore_app(tmp_path, monkeypatch, models=[
        {"id": "qwen", "path": str(tmp_path / "m.gguf"), "bench": {"tune": {"at": now()}}}])
    cancelled = []
    app.jobs = type("J", (), {"cancel": staticmethod(lambda job_id: cancelled.append(job_id))})()

    def start(model=None):
        app.db.eval_rows["eval_1"] = {"status": "running", "summary": {}}
        return {"id": "eval_1", "job_id": "job_1"}
    app.evals.start = start
    model, why = ex.check()
    assert why is None and model["_what"] == "evaluation"
    monkeypatch.setattr(explore.Explorer, "desktop_idle_minutes", staticmethod(lambda: 0.1))   # someone is back
    real_sleep = asyncio.sleep
    monkeypatch.setattr(explore.asyncio, "sleep", lambda s: real_sleep(0))
    entry = asyncio.run(ex.explore(model))
    assert cancelled == ["job_1"] and entry["stopped"] == "someone is using the computer"
    assert app.db.settings["explore_last_evaluation"]


def test_desktop_idle_time_from_gnome(monkeypatch):
    from dva import hardware
    if os.name == "nt":
        return
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "/usr/bin/gdbus")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    assert hardware.user_idle_seconds(lambda cmd, timeout=3: "(uint64 90500,)\n") == 90.5
    assert hardware.user_idle_seconds(lambda cmd, timeout=3: "") is None              # not GNOME (kiosk, KDE)


# ---------------------------------------------------------------------------------------------- ARM64 Linux (2.25.0)
def test_arm64_linux_gets_its_own_llama_builds(monkeypatch):
    from dva import images, runtime
    monkeypatch.setattr(runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "aarch64")
    assert runtime.os_key() == "linux-arm64"
    assert set(runtime.VERSIONS["backend_assets"]["linux-arm64"]) == {"cpu", "vulkan"}
    monkeypatch.setattr(runtime.platform, "machine", lambda: "x86_64")
    assert runtime.os_key() == "linux"
    monkeypatch.setattr(images.sys, "platform", "linux")
    monkeypatch.setattr(images.platform, "machine", lambda: "aarch64")
    assert images.os_key() == "linux-arm64" and images.SD_ASSETS.get("linux-arm64") is None   # no image engine build


# ---------------------------------------------------------------------------------------------- 2.26.0
def test_a_value_that_repeats_the_whole_call_is_unwrapped_or_refused():
    from dva import toolcheck
    from dva.tools import Tool
    tool = Tool("run_python", "Run Python", {"type": "object", "properties": {"code": {"type": "string"}},
                                              "required": ["code"]}, None, "python")
    tm = {"run_python": tool}
    whole = json.dumps({"code": "print(37 * 481 + 12)"})
    out = toolcheck.repair(tm, "run_python", {"code": whole})
    assert out["error"] is None and out["args"]["code"] == "print(37 * 481 + 12)" and out["notes"]
    cut = '{"code":"import numpy as np\\nK = 2.0\\nsettled_idx'
    out = toolcheck.repair(tm, "run_python", {"code": cut})
    assert out["error"] and "cut off" in out["error"]
    plain = toolcheck.repair(tm, "run_python", {"code": "x = {'code': 1}"})
    assert plain["error"] is None and plain["args"]["code"] == "x = {'code': 1}"


def test_a_lease_whose_task_ended_does_not_keep_a_model_in_use():
    from dva.runtime import Server

    async def main():
        s = Server({"id": "m"}, {"ctx": 8192}, "k")

        async def holder(leak):
            token = s.hold()
            if not leak:
                s.let_go(token)
        await asyncio.create_task(holder(False))
        assert s.users == 0
        await asyncio.create_task(holder(True))       # never gave it back: its task is gone, so it does not count
        assert s.users == 0 and not s.holder_tasks()
        token = s.hold()                               # held by this task, which is still running
        assert s.users == 1 and s.holder_tasks()
        s.let_go(token)
        assert s.users == 0
    asyncio.run(main())


def test_older_tool_results_are_shortened_to_fit_the_context():
    from dva.runner import Turn
    msgs = [{"role": "system", "content": "s"}] + [{"role": "tool", "content": "x" * 5000} for _ in range(4)]
    assert Turn.shorten_results(msgs)
    assert [len(m["content"]) < 1000 for m in msgs[1:]] == [True, True, False, False]   # the last two are kept
    assert not Turn.shorten_results(msgs[:1])


def test_a_run_that_ran_out_of_context_may_load_its_model_larger():
    from dva.orchestrator import RunFit

    class RT:
        def fit(self, model_id, need, parallel=1, top=None):
            return {"ctx": min(need, max(8192, top or 0)), "parallel": parallel}
    fit = RunFit()
    fit.want("m", 20000)
    assert fit.overrides(RT(), "m")["ctx"] == 8192         # the model's own setting is the ceiling…
    fit.grow("m", 20000)
    assert fit.overrides(RT(), "m")["ctx"] == 20000        # …until a step ran out of context


# ---------------------------------------------------------------------------------------------- usage and telemetry (2.27.0)
def _meter(tmp_path):
    from dva.cloud import USAGE
    from dva.db import DB
    from dva.meter import Meter
    db = DB(tmp_path / "u.db")
    return db, Meter(db, USAGE), USAGE


class _Srv:
    model_id = "qwen-9b"
    read_tps = 14.0


def test_usage_counts_calls_by_model_purpose_and_hour(tmp_path):
    import time as _t
    from dva.meter import purpose
    db, m, usage = _meter(tmp_path)
    db.insert("models", {"id": "qwen-9b", "name": "Qwen 9B"})
    db.insert("chats", {"id": "c1", "title": "Prices", "origin": "user"})
    usage.set({"chat_id": "c1", "purpose": "chat", "harness": "agent"})
    for _ in range(3):
        m.record_local(_Srv(), {"prompt_n": 200, "cache_n": 800, "predicted_n": 40, "prompt_ms": 14000, "predicted_ms": 10000}, 25)
    with purpose("planner"):
        m.record_local(_Srv(), {"prompt_n": 100, "predicted_n": 20, "prompt_ms": 7000, "predicted_ms": 5000}, 12)
    m.record_local(_Srv(), {}, 180, status="timeout", error="no token")
    s = m.summary(7)
    t = s["totals"]
    assert t["calls"] == 5 and t["failed"] == 1
    assert t["read"] == 700 and t["cached"] == 2400 and t["written"] == 140
    assert t["cache_percent"] == round(100 * 2400 / 3100)
    assert t["read_tps"] == round(700 / 49, 1) and t["write_tps"] == round(140 / 35, 1)
    assert s["by_model"][0]["name"] == "Qwen 9B" and s["by_model"][0]["share"] == 100
    assert {p["key"]: p["calls"] for p in s["by_purpose"]} == {"chat": 4, "planner": 1}
    now = _t.localtime()
    assert s["heat"][now.tm_wday][now.tm_hour] == t["tokens"]
    assert s["daily"][-1]["tokens"] == t["tokens"] and len(s["daily"]) == 7
    assert s["top_chats"][0]["title"] == "Prices"
    assert s["highlights"]["streak_days"] == 1
    assert s["highlights"]["cache_saved_seconds"] == round(2400 / t["read_tps"])
    assert s["latency"]["first_p50"] == 14.0


def test_usage_adds_older_replies_once_and_can_be_switched_off(tmp_path):
    from datetime import datetime, timedelta, timezone
    db, m, usage = _meter(tmp_path)
    db.insert("chats", {"id": "c2", "title": "Old", "origin": "schedule"})
    when = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    db.insert("messages", {"id": "m1", "chat_id": "c2", "role": "assistant", "content": "x", "created": when,
                           "meta": {"model": "qwen-9b", "harness": "agent", "stats": {"model_calls": 3, "prompt_tokens": 500,
                                    "cached_tokens": 100, "output_tokens": 60, "seconds": 90}}})
    s = m.summary(7)
    assert s["totals"]["calls"] == 3 and s["totals"]["written"] == 60 and s["has_history"]
    assert s["by_purpose"][0]["key"] == "schedule"
    assert Meter_rows(db) == 1
    m._backfilled = False
    m.summary(7)                                   # once only
    assert Meter_rows(db) == 1
    db.set_setting("usage_enabled", False)
    m.record_local(_Srv(), {"prompt_n": 1}, 1)
    assert Meter_rows(db) == 1
    m.clear()
    assert m.summary(7)["totals"]["calls"] == 0


def Meter_rows(db):
    return db.one("SELECT COUNT(*) AS n FROM model_calls")["n"]


def test_online_calls_are_counted_with_their_time_and_cost(tmp_path):
    import time as _t
    from dva import cloud
    db, m, usage = _meter(tmp_path)

    class Prov:
        def record(self, *a):
            return 0.0123, False
    srv = type("S", (), {"model_id": "cloud:openai:gpt", "cloud": type("C", (), {"meter": m})(), "providers": Prov()})()
    a = cloud.Adapter.__new__(cloud.Adapter)
    a.s, a.providers = srv, Prov()
    cloud.CALL_START.set(_t.monotonic() - 2)
    out = a.meter(1000, 200)
    assert out["online"] and out["prompt_n"] == 1000
    row = db.one("SELECT * FROM model_calls")
    assert row["online"] == 1 and row["cost"] == 0.0123 and 1500 < row["total_ms"] < 10000


def test_the_llm_stream_is_metered_and_shown_as_reading_then_writing(tmp_path):
    from dva.llm import LLM, LLMTimeout
    from dva.telemetry import Telemetry
    db, m, usage = _meter(tmp_path)

    class RT:
        servers = {}
    rt = RT()
    rt.db, rt.meter = db, m
    rt.telemetry = Telemetry(rt, db)
    llm = LLM(rt)
    phases = []

    async def fake(server, body):
        phases.append([c["phase"] for c in rt.telemetry.calls.values()])
        yield "token", "a"
        phases.append([c["phase"] for c in rt.telemetry.calls.values()])
        yield "token", "b"
        yield "end", {"timings": {"prompt_n": 50, "predicted_n": 2, "prompt_ms": 1000, "predicted_ms": 500}}

    async def broken(server, body):
        yield "token", "a"
        raise LLMTimeout("stuck", "stall")

    async def go():
        llm._stream = fake
        async for _ in llm._events(_Srv(), {}):
            pass
        llm._stream = broken
        with pytest.raises(LLMTimeout):
            async for _ in llm._events(_Srv(), {}, purpose="snapshot"):
                pass
    asyncio.run(go())
    assert phases == [["reading"], ["writing"]]
    assert rt.telemetry.calls == {} and rt.telemetry._written == 3
    rows = db.all("SELECT status, purpose, read_tokens FROM model_calls ORDER BY id")
    assert [r["status"] for r in rows] == ["ok", "timeout"] and rows[0]["read_tokens"] == 50
    assert rows[1]["purpose"] == "snapshot"


def test_telemetry_samples_keeps_history_marks_and_kimios_readings(tmp_path, monkeypatch):
    import time as _t
    from dva import telemetry
    from dva.db import DB
    db = DB(tmp_path / "t.db")
    kos = tmp_path / "telemetry.json"
    kos.write_text(json.dumps({"gpus": [{"card": "card0", "util": 37.5}], "power_cpu_w": 9.2, "fan_rpm": 2400,
                               "throttled": False, "slice_mem_mb": 6000, "slice_max_mb": 27000}))
    monkeypatch.setattr(telemetry, "KIMIOS_FILE", str(kos))

    class RT:
        servers = {}
    t = telemetry.Telemetry(RT(), db, interval=0.2)
    t.mark("Loaded Qwen", "load")
    t.start()
    try:
        _t.sleep(1.1)
        h = t.history(60)
        assert len(h["samples"]) >= 3 and h["running"]
        s = h["latest"]
        assert 0 <= s["cpu"] <= 100 and s["mem_used_mb"] > 0 and s["kimilm_mem_mb"] > 0
        assert s["kimios"] and s["gpu"] == 37.5 and s["power_cpu_w"] == 9.2 and s["slice_max_mb"] == 27000
        assert h["marks"][0]["text"] == "Loaded Qwen"
        assert set(h["samples"][-1]) <= set(telemetry.SCALARS) | {"t"}
        top = t.processes()
        assert top["cpu"] and any(p["kimilm"] for p in top["memory"] + top["cpu"]) or top["memory"]
        t.set_enabled(False)
        assert t._thread is None
        n = len(t.fine)
        _t.sleep(0.5)
        assert len(t.fine) == n                          # stopped
        t.set_enabled(True)
        _t.sleep(0.5)
        assert len(t.fine) > n
    finally:
        t.stop()
    kos.write_text("{}")
    os.utime(kos, (0, 0))                                # an old file: the service is not running
    assert t._kimios() is None


def test_telemetry_minute_averages_keep_peaks():
    from dva.telemetry import Telemetry
    rows = [{"t": i, "cpu": v, "gpu": None} for i, v in enumerate([10, 90, 20])]
    agg = Telemetry._aggregate(rows)
    assert agg["cpu"] == 40 and agg["cpu_max"] == 90 and agg["gpu"] is None


# ---------------------------------------------------------------------------------------------- 2.28.0
def test_connector_tools_are_found_by_stem_and_by_their_short_name():
    from dva.runner import rank_tools, resolve_tool
    from dva.tools import Tool
    S = {"type": "object", "properties": {}}
    mk = lambda n, d: Tool("mcp__kimios_kimios__" + n, "[KimiOS] " + d, S, None, "connector")  # noqa: E731
    tools = [mk("machine", "This computer's hardware."),
             mk("setting_get", "Read a desktop setting (gsettings schema and key)."),
             mk("control_simulate", "Practise a controller. Compare settings before it touches a device."),
             mk("setting_set", "Change an allowed desktop or display setting: dark or light mode (colour scheme, "
                               "theme: org.gnome.desktop.interface color-scheme).")]
    found = [t.name.rsplit("__", 1)[-1] for t in rank_tools(tools, "display settings color scheme")]
    assert found[0] == "setting_set" and "setting_get" in found
    assert rank_tools(tools, "zzz") == [] and len(rank_tools(tools, "")) == 4
    assert resolve_tool(tools, "machine").name == "mcp__kimios_kimios__machine"
    assert resolve_tool(tools, "mcp__other__machine").name == "mcp__kimios_kimios__machine"
    assert resolve_tool(tools, "nothing") is None
