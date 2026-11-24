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

from kimilm import hardware, plugins  # noqa: E402

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
    monkeypatch.setenv("KIMILM_SYSTEM_PLUGINS", str(system))
    p = plugins.Plugins(FakePaths(tmp_path / "data"), FakeDB(), secrets=None, bus=None)
    found = {x["id"]: x for x in p.all()}
    assert found["kimios"]["source"] == "system" and found["kimios"]["enabled"]
    assert found["mine"]["source"] == "user"
    conn = [c for c in p.connectors() if c["plugin"] == "kimios"]
    assert len(conn) == 1 and conn[0]["command"] == "/usr/libexec/kimios/kimios-mcp" and conn[0]["ready"]
    with pytest.raises(ValueError):
        p.uninstall("kimios")                  # part of the operating system: switch off, not remove
    assert conn[0]["system"] is True
    from kimilm import mcp
    connectors = mcp.Connectors.__new__(mcp.Connectors)
    connectors.plugins = p
    assert connectors.system_ids() == ["kimios:kimios"]      # every agent chat gets it (2.24.0)


def test_no_system_plugin_folder_is_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMILM_SYSTEM_PLUGINS", str(tmp_path / "does-not-exist"))
    p = plugins.Plugins(FakePaths(tmp_path / "data"), FakeDB(), secrets=None, bus=None)
    assert p.all() == []


# ---------------------------------------------------------------------------------------------- evaluation
def test_evaluation_releases_other_models_first_and_all_idle_models_at_the_end(tmp_path):
    from kimilm import evals

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
    from kimilm import app_state

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
    from kimilm import procs
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
    from kimilm import explore, hardware
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
    from kimilm import hardware
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
    from kimilm.db import now
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
    from kimilm import explore
    real_sleep = asyncio.sleep
    monkeypatch.setattr(explore.asyncio, "sleep", lambda s: real_sleep(0))
    entry = asyncio.run(ex.explore(app.db.models[0]))
    assert app.models.stopped == ["qwen"] and entry["stopped"] == "a chat or task started"


# ---------------------------------------------------------------------------------------------- suggestions
def _at(day, hour):
    from datetime import datetime
    return datetime(2026, 11, day, hour, 5).astimezone().isoformat()     # local time, stored with its zone


def test_a_request_made_on_three_weekdays_becomes_a_schedule_suggestion():
    from kimilm import suggest
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
    from kimilm import suggest
    rows = [{"content": "Check the server logs for errors", "created": _at(16, 8)},
            {"content": "Check the server logs for errors", "created": _at(16, 15)},
            {"content": "Check the server logs for errors", "created": _at(17, 8)}]
    assert suggest.repeated_requests(rows) == []


def test_suggestions_are_off_until_switched_on():
    from kimilm import suggest

    class DB:
        def setting(self, key, default=None):
            return default
    app = type("A", (), {"db": DB()})()
    assert suggest.Suggestions(app).list() == {"enabled": False, "items": []}


def test_age_reads_utc_and_zoned_times():
    from datetime import datetime, timedelta, timezone
    from kimilm.explore import age
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    assert abs(age(hour_ago.isoformat()).total_seconds() - 3600) < 5
    assert abs(age(hour_ago.replace(tzinfo=None).isoformat()).total_seconds() - 3600) < 5      # no zone: UTC
    assert abs(age(hour_ago.astimezone(timezone(timedelta(hours=3))).isoformat()).total_seconds() - 3600) < 5


def test_explorer_evaluates_and_stops_when_someone_uses_the_computer(tmp_path, monkeypatch):
    from kimilm import explore
    from kimilm.db import now
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
    from kimilm import hardware
    if os.name == "nt":
        return
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "/usr/bin/gdbus")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    assert hardware.user_idle_seconds(lambda cmd, timeout=3: "(uint64 90500,)\n") == 90.5
    assert hardware.user_idle_seconds(lambda cmd, timeout=3: "") is None              # not GNOME (kiosk, KDE)


# ---------------------------------------------------------------------------------------------- ARM64 Linux (2.25.0)
def test_arm64_linux_gets_its_own_llama_builds(monkeypatch):
    from kimilm import images, runtime
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
    from kimilm import toolcheck
    from kimilm.tools import Tool
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
    from kimilm.runtime import Server

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
    from kimilm.runner import Turn
    msgs = [{"role": "system", "content": "s"}] + [{"role": "tool", "content": "x" * 5000} for _ in range(4)]
    assert Turn.shorten_results(msgs)
    assert [len(m["content"]) < 1000 for m in msgs[1:]] == [True, True, False, False]   # the last two are kept
    assert not Turn.shorten_results(msgs[:1])


def test_a_run_that_ran_out_of_context_may_load_its_model_larger():
    from kimilm.orchestrator import RunFit

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
    from kimilm.cloud import USAGE
    from kimilm.db import DB
    from kimilm.meter import Meter
    db = DB(tmp_path / "u.db")
    return db, Meter(db, USAGE), USAGE


class _Srv:
    model_id = "qwen-9b"
    read_tps = 14.0


def test_usage_counts_calls_by_model_purpose_and_hour(tmp_path):
    import time as _t
    from kimilm.meter import purpose
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
    from kimilm import cloud
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
    from kimilm.llm import LLM, LLMTimeout
    from kimilm.telemetry import Telemetry
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
    from kimilm import telemetry
    from kimilm.db import DB
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
    from kimilm.telemetry import Telemetry
    rows = [{"t": i, "cpu": v, "gpu": None} for i, v in enumerate([10, 90, 20])]
    agg = Telemetry._aggregate(rows)
    assert agg["cpu"] == 40 and agg["cpu_max"] == 90 and agg["gpu"] is None


# ---------------------------------------------------------------------------------------------- 2.28.0
def test_connector_tools_are_found_by_stem_and_by_their_short_name():
    from kimilm.runner import rank_tools, resolve_tool
    from kimilm.tools import Tool
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


# ---------------------------------------------------------------------------------------------- 2.29.0
class _Bus:
    def __init__(self):
        self.events = []

    def publish(self, kind, **data):
        self.events.append({"kind": kind, **data})


class _Settings:
    def __init__(self, **values):
        self.values = values

    def setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value


def _fake_app(tmp_path, **settings):
    from types import SimpleNamespace
    (tmp_path / "data").mkdir(exist_ok=True)
    return SimpleNamespace(paths=SimpleNamespace(data=tmp_path / "data"), db=_Settings(**settings), bus=_Bus(),
                           log=lambda *a: None)


def _w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _usb(root, name, vid, pid, product, interfaces, maker="", serial=None, driver_root=None):
    """A USB device as sysfs shows it: the device under /sys/devices, symlinks in /sys/bus/usb/devices."""
    dev = root / "sys/devices/pci0000:00/usb1" / name
    for k, v in {"idVendor": vid, "idProduct": pid, "product": product, "manufacturer": maker,
                 "busnum": "1", "devnum": name.split("-")[-1], "speed": "480"}.items():
        _w(dev / k, v + "\n")
    if serial:
        _w(dev / "serial", serial)
    link_dir = root / "sys/bus/usb/devices"
    link_dir.mkdir(parents=True, exist_ok=True)
    (link_dir / name).symlink_to(dev)
    for i, (cls, sub, proto, drv) in enumerate(interfaces):
        intf = dev / f"{name}:1.{i}"
        for k, v in {"bInterfaceClass": cls, "bInterfaceSubClass": sub, "bInterfaceProtocol": proto}.items():
            _w(intf / k, v)
        _w(intf / "modalias", f"usb:v{vid.upper()}p{pid.upper()}d0100dc00dsc00dp00ic{cls}isc{sub}ip{proto}in0{i}")
        if drv:
            target = root / "sys/bus/usb/drivers" / drv
            target.mkdir(parents=True, exist_ok=True)
            (intf / "driver").symlink_to(target)
        (link_dir / intf.name).symlink_to(intf)
    return dev


def _edid(name, maker="DEL"):
    raw = bytearray(128)
    raw[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    word = sum((ord(c) - 64) << s for c, s in zip(maker, (10, 5, 0)))
    raw[8], raw[9] = word >> 8, word & 0xff
    raw[21], raw[22] = 53, 30
    raw[54:72] = b"\x00\x00\x00\xfc\x00" + name.encode().ljust(13, b"\n")[:13]
    return bytes(raw)


def _fake_sys(root):
    _usb(root, "1-1", "18d1", "4ee7", "Pixel 8", [("ff", "42", "01", None), ("06", "01", "01", None)],
         maker="Google", serial="34AB12")
    _usb(root, "1-2", "046d", "c52b", "USB Receiver", [("03", "01", "01", "usbhid"), ("03", "01", "02", "usbhid")],
         maker="Logitech")
    pico = _usb(root, "1-3", "2e8a", "000a", "Pico", [("02", "02", "00", "cdc_acm"), ("0a", "00", "00", "cdc_acm")],
                maker="Raspberry Pi", serial="E66")
    _usb(root, "1-4", "1d6b", "0002", "hub", [("09", "00", "00", "hub")])
    tty = root / "sys/class/tty/ttyACM0"
    tty.mkdir(parents=True)
    (tty / "device").symlink_to(pico / "1-3:1.0")
    drm = root / "sys/class/drm"
    _w(drm / "card1-HDMI-A-1/status", "connected")
    _w(drm / "card1-HDMI-A-1/modes", "1920x1080\n1280x720\n")
    (drm / "card1-HDMI-A-1/edid").write_bytes(_edid("DELL P2419H"))
    _w(drm / "card1-eDP-1/status", "connected")
    _w(drm / "card1-DP-1/status", "disconnected")
    _w(root / "proc/bus/input/devices",
       'I: Bus=0005 Vendor=05ac Product=024f Version=0001\nN: Name="Keychron K2"\nP: Phys=aa:bb\n'
       'H: Handlers=sysrq kbd leds event7\nB: LED=1f\n\n'
       'I: Bus=0019 Vendor=0000 Product=0001\nN: Name="Power Button"\nH: Handlers=kbd event2\n\n')


def test_usb_devices_are_told_apart_by_what_they_say_about_themselves():
    from kimilm.devices import usb_kind
    I = lambda *c: [{"class": a, "subclass": b, "protocol": p} for a, b, p in c]  # noqa: E731
    assert usb_kind("18d1", "4ee7", "Pixel 8", I((0xff, 0x42, 0x01)))[0] == "phone"
    assert usb_kind("04e8", "6860", "SAMSUNG_Android", I((0x06, 1, 1)))[0] == "phone"
    assert usb_kind("046d", "c52b", "USB Receiver", I((3, 1, 1), (3, 1, 2))) == ("keyboard", "Keyboard and mouse "
                                                                                 "receiver (dongle)")
    assert usb_kind("2e8a", "0003", "RP2 Boot", I((8, 6, 80)))[1].startswith("Raspberry Pi Pico in boot mode")
    assert usb_kind("0525", "a4a2", "RNDIS", I((2, 6, 0)))[0] == "board"
    assert usb_kind("1a86", "7523", "USB Serial", I((0xff, 1, 2)))[0] == "serial"
    assert usb_kind("1234", "0001", "CO2 Sensor", I((3, 0, 0)))[0] == "sensor"
    assert usb_kind("8087", "0026", "", I((0xe0, 1, 1)))[0] == "bluetooth"


def test_linux_devices_are_read_from_sys_with_ports_displays_and_bluetooth_input(tmp_path):
    from kimilm.devices import Devices, edid_info
    _fake_sys(tmp_path)
    dv = Devices(_fake_app(tmp_path), root=tmp_path)
    found = {d["id"]: d for d in dv._linux()}
    assert found["usb:1-1"]["kind"] == "phone" and found["usb:1-1"]["name"] == "Google Pixel 8"
    assert found["usb:1-2"]["kind"] == "keyboard" and found["usb:1-2"]["drivers"] == ["usbhid"]
    pico = found["usb:1-3"]
    assert pico["kind"] == "board" and pico["ports"] == ["/dev/ttyACM0"] and pico["drivers"] == ["cdc_acm"]
    assert "usb:1-4" not in found                                         # a hub is not a device of its own
    hdmi = found["display:card1-HDMI-A-1"]
    assert hdmi["name"] == "DELL P2419H" and hdmi["connection"] == "HDMI" and not hdmi["internal"]
    assert hdmi["details"]["preferred_mode"] == "1920x1080" and hdmi["details"]["maker"] == "DEL"
    assert found["display:card1-eDP-1"]["internal"] and "display:card1-DP-1" not in found
    kb = found["input:keychron-k2"]
    assert kb["kind"] == "keyboard" and kb["connection"] == "Bluetooth"
    assert not any("power" in k for k in found)                           # a power button is not a keyboard
    assert edid_info(b"junk") == {}
    acts = {a["name"]: a for a in dv.actions(pico)}
    assert acts["serial_write"]["physical"] and not acts["serial_read"]["changes"]
    phone_acts = [a["name"] for a in dv.actions(found["usb:1-1"])]
    assert {"phone_info", "pull", "screenshot", "shell"} <= set(phone_acts)
    assert "brightness" in [a["name"] for a in dv.actions(hdmi)]


def test_devices_coming_and_going_are_announced_once(tmp_path):
    import shutil as _sh
    from kimilm.devices import Devices
    _fake_sys(tmp_path)
    app = _fake_app(tmp_path)
    dv = Devices(app, root=tmp_path)

    async def go():
        first = await dv.look(announce=True, fresh=True)          # what is there at start is not announced
        assert not [e for e in app.bus.events if e["kind"] == "notify"]
        _usb(tmp_path, "1-5", "2341", "0043", "Uno", [("02", "02", "01", "cdc_acm")], maker="Arduino")
        await dv.look(announce=True)
        said = [e for e in app.bus.events if e["kind"] == "notify"]
        assert len(said) == 1 and said[0]["title"] == "Connected: Arduino Uno" and said[0]["level"] == "device"
        (tmp_path / "sys/bus/usb/devices/1-1").unlink()
        for p in (tmp_path / "sys/bus/usb/devices").glob("1-1:*"):
            p.unlink()
        await dv.look(announce=True)
        said = [e for e in app.bus.events if e["kind"] == "notify"]
        assert said[-1]["title"] == "Disconnected: Google Pixel 8" and not said[-1]["arrived"]
        assert any(e["kind"] == "devices" for e in app.bus.events)
        return first
    assert len(asyncio.run(go())) >= 5
    _sh.rmtree(tmp_path / "sys")


@posix_only
def test_a_serial_port_is_read_and_written(tmp_path):
    import threading
    from kimilm.devices import serial_exchange
    master, slave = os.openpty()
    port = os.ttyname(slave)

    def board():                                         # answers a line like a MicroPython prompt would
        import time as _t
        _t.sleep(0.2)
        os.write(master, b"temp=21.5\r\n")
    threading.Thread(target=board).start()
    got = serial_exchange(port, 115200, None, 1.0)
    assert "temp=21.5" in got
    out = serial_exchange(port, 9600, "print(1)", 0.3)
    assert out.startswith("Sent 'print(1)'")
    assert os.read(master, 100).startswith(b"print(1)\r\n")
    os.close(master)
    os.close(slave)


def test_programs_install_into_kimilms_toolbox_on_an_image_based_system(tmp_path, monkeypatch):
    from kimilm import installer
    app = _fake_app(tmp_path)
    calls = []

    async def run(app_, argv, timeout=1800, on_status=None, owner=None, env=None):
        calls.append(argv)
        if argv[:2] == ["toolbox", "list"]:
            return 0, "CONTAINER ID  CONTAINER NAME\n"
        if "rpm" in argv:
            return 0, "/usr/bin/adb\n/usr/bin/fastboot\n/usr/share/doc/x\n"
        return 0, ""
    monkeypatch.setattr(installer, "_run", run)
    monkeypatch.setattr(installer, "computer", lambda: {"os": "linux", "immutable": True, "toolbox": True,
                                                        "flatpak": True, "brew": False, "managers": []})
    out = asyncio.run(installer.install(app, "adb", on_status=lambda t: None, by="test"))
    assert ["toolbox", "create", "-y", installer.TOOLBOX] in calls
    assert ["toolbox", "run", "-c", installer.TOOLBOX, "sudo", "dnf", "install", "-y", "android-tools"] in calls
    launcher = installer.tools_dir(app) / "bin" / "adb"
    assert launcher.exists() and f"toolbox run -c {installer.TOOLBOX} /usr/bin/adb" in launcher.read_text()
    assert out["found"] == str(launcher) and out["how"].startswith("toolbox")
    rec = installer.installed(app)[0]
    assert rec["name"] == "adb" and str(launcher) in rec["launchers"]
    env = installer.with_path(app, {"PATH": "/usr/bin"})
    assert env["PATH"].split(os.pathsep)[0] == str(installer.tools_dir(app) / "bin")
    asyncio.run(installer.remove(app, "adb"))
    assert not launcher.exists() and installer.installed(app) == []
    assert installer.kind_of("org.gimp.GIMP") == "app" and installer.kind_of("chrome-for-testing") == "browser"
    assert installer.kind_of("pip:requests") == "python" and installer.kind_of("nmap") == "command"
    with pytest.raises(installer.InstallError):
        asyncio.run(installer.install(app, "adb; rm -rf /"))


def test_a_program_that_needs_a_password_reads_as_needing_the_user(tmp_path, monkeypatch):
    from kimilm import installer, recovery
    app = _fake_app(tmp_path)

    async def run(app_, argv, timeout=1800, on_status=None, owner=None, env=None):
        return (1, "sudo: a password is required") if argv[:2] == ["sudo", "-n"] else (0, "")
    monkeypatch.setattr(installer, "_run", run)
    monkeypatch.setattr(installer, "computer", lambda: {"os": "linux", "immutable": False, "toolbox": False,
                                                        "flatpak": False, "brew": False, "managers": ["apt"]})
    monkeypatch.setattr(installer, "which", lambda app_, name: None)
    with pytest.raises(installer.InstallError) as err:
        asyncio.run(installer.install(app, "ffmpeg"))
    assert "sudo apt-get install -y ffmpeg" in str(err.value)
    assert recovery.classify(str(err.value)) == "permission"


def test_a_missing_program_is_installed_not_asked_about():
    from kimilm import recovery
    assert recovery.fast_rule({"class": "missing", "reason": 'Browser "chromium" is not installed. Run `npx '
                                                             '@playwright/mcp install-browser chromium`'}) == "install"
    assert recovery.fast_rule({"class": "approach", "need": "tool", "reason": "bash: nmap: command not found"}) == "install"
    assert recovery.fast_rule({"class": "missing", "reason": "No image model is installed"}) == "handover"
    assert recovery.fast_rule({"class": "permission", "reason": "the user declined"}) == "ask"


def test_the_browser_helper_installs_its_browser_when_it_is_missing(tmp_path):
    from kimilm.services import Services
    app = _fake_app(tmp_path, install_programs=True)
    sv = Services(app)
    answers = [(False, 'Error: Browser "chromium" is not installed; expected executable at /x. Run `npx '
                       '@playwright/mcp install-browser chromium` to install'), (True, "page")]
    did = []

    async def call(key, name, args, timeout=120):
        return answers.pop(0)

    async def install(target, on_status=None):
        did.append(target)

    async def nothing(*a):
        did.append("restart")
    sv._browser_call, sv.install_browser, sv.close_browser, sv.restart_browser = call, install, nothing, nothing
    assert asyncio.run(sv.browser_call("c1", "browser_navigate", {})) == (True, "page")
    assert did == ["chromium", "restart", "restart"]


def test_auto_approve_is_read_at_every_approval(tmp_path):
    from types import SimpleNamespace
    from kimilm.runner import Turn
    from kimilm.tools import Tool
    app = _fake_app(tmp_path)
    app.approvals = {}
    app.jobs = SimpleNamespace(set_status=lambda *a, **k: None)
    t = Turn.__new__(Turn)
    t.app, t.db, t.emit = app, app.db, (lambda *a, **k: None)
    t.chat, t.parent_chat, t.job = {"id": "c1", "title": "x", "parent_chat_id": "lead"}, {"id": "lead"}, {"id": "j"}
    t._auto_payload, t.policy, t.allowed_always = False, None, set()
    tool = Tool("write_file", "", {}, None, "files", changes=True)

    async def go():
        waiting = asyncio.create_task(t.approve(tool, {}, "write it"))
        always = asyncio.create_task(t.approve(tool, {}, "pay", ignore_auto=True))
        await asyncio.sleep(0.2)
        assert not waiting.done() and len(app.approvals) == 2
        note = [e for e in app.bus.events if e["kind"] == "notify"][0]
        assert note["level"] == "action" and note["approval_id"] in app.approvals
        app.db.set_setting("chat_auto_approve:lead", True)     # switched on in the lead's chat
        assert await asyncio.wait_for(waiting, 5) is True
        assert not always.done()                               # paying still asks
        always.cancel()
    asyncio.run(go())
    app.db.values.clear()
    assert t.auto_approve is False
    t.job = {"id": "j", "kind": "schedule"}
    assert t.auto_approve is True                              # unattended work approves on its own by default
    app.db.set_setting("unattended_auto_approve", False)
    assert t.auto_approve is False


def test_planners_are_told_that_agents_think_install_and_are_approved():
    from types import SimpleNamespace
    from kimilm.orchestrator import crew_facts
    turn = SimpleNamespace(db=_Settings(), auto_approve=True)
    text = crew_facts(turn)
    assert "thinks before it acts" in text and "install_program" in text and "approved in advance" in text
    turn = SimpleNamespace(db=_Settings(thinking_default=False, install_programs=False), auto_approve=False)
    assert crew_facts(turn) == ""


def test_a_chat_reply_that_took_a_while_tells_you_when_it_is_done(tmp_path):
    from types import SimpleNamespace
    from kimilm.db import DB
    from kimilm.jobs import Jobs
    db = DB(tmp_path / "j.db")
    app = SimpleNamespace(db=db, bus=_Bus(), log=lambda *a: None)
    jobs = Jobs(app)
    db.insert("chats", {"id": "c1", "title": "Prices"})
    db.insert("messages", {"id": "m1", "chat_id": "c1", "role": "assistant", "content": "The prices rose 4 %.",
                           "created": "2026-11-21T10:00:00+00:00"})
    db.insert("jobs", {"id": "j1", "kind": "chat", "title": "Prices", "status": "running", "chat_id": "c1",
                       "created": "2026-11-21T09:00:00+00:00", "started": "2026-11-21T09:00:00+00:00"})
    row = db.get("jobs", "j1")
    assert jobs.worth_telling(row, "done")
    jobs.tell_done(row, "done", None)
    e = app.bus.events[-1]
    assert e["title"] == "Done: Prices" and e["body"] == "The prices rose 4 %." and e["level"] == "done"
    assert not jobs.worth_telling({**row, "kind": "agent"}, "done")
    assert not jobs.worth_telling({**row, "payload": {"mission": {}}}, "done")
    db.set_setting("notify_done_seconds", 0)
    assert not jobs.worth_telling(row, "done")


# ---------------------------------------------------------------------------------------------- 2.30.0
def test_a_physical_tool_asks_a_person_for_a_real_device_and_not_for_a_simulated_one(tmp_path):
    from types import SimpleNamespace
    from kimilm.runner import Turn
    from kimilm.tools import Tool
    app = _fake_app(tmp_path)
    app.approvals = {}
    app.jobs = SimpleNamespace(set_status=lambda *a, **k: None)
    t = Turn.__new__(Turn)
    t.app, t.db, t.emit = app, app.db, (lambda *a, **k: None)
    t.chat, t.parent_chat, t.job = {"id": "c1", "title": "x"}, None, {"id": "j"}
    t._auto_payload, t.policy, t.allowed_always = True, None, {"kimios__controller"}   # auto-approve and "always"
    tool = Tool("kimios__controller", "", {}, None, "connector", changes=False, physical=True)

    async def go():
        assert await t.approve(tool, {"device": "sim-fridge", "action": "start"}, "start") is True
        real = asyncio.create_task(t.approve(tool, {"device": "fridge-kitchen", "action": "start"}, "start"))
        await asyncio.sleep(0.2)
        assert not real.done() and len(app.approvals) == 1           # neither auto nor "always" let it through
        assert next(iter(app.approvals.values()))["always_asks"]
        real.cancel()
    asyncio.run(go())


def test_kimios_bench_devices_join_the_device_list(tmp_path, monkeypatch):
    import socketserver
    import threading
    from kimilm import bench
    from kimilm.devices import Devices
    sock = tmp_path / "envelope.sock"
    rows = [{"id": "sim-fridge", "model": "simulated two-compartment refrigerator", "kind": "appliance",
             "simulated": True, "driver": 2, "mode": "custom"},
            {"id": "sim-air", "model": "simulated air sensor", "kind": "sensor", "simulated": True, "driver": None,
             "mode": "no driver"}]

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            req = json.loads(self.rfile.readline())
            out = {"ok": True, "devices": rows} if req["op"] == "devices" else {"ok": False, "error": "no driver"}
            self.wfile.write((json.dumps(out) + "\n").encode())
    srv = socketserver.ThreadingUnixStreamServer(str(sock), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("KIMIOS_ENVELOPE_SOCKET", str(sock))
    assert bench.available()
    got = {d["id"]: d for d in bench.listing_sync() and [bench.as_device(r) for r in bench.listing_sync()]}
    fridge = got["bench:sim-fridge"]
    assert fridge["kind"] == "appliance" and fridge["state"] == "driver v2 · custom" and fridge["simulated"]
    assert got["bench:sim-air"]["state"] == "no driver yet"
    dv = Devices(_fake_app(tmp_path), root=tmp_path)
    assert [a["name"] for a in dv.actions(fridge)] == ["info"]        # driven through KimiOS's tools only
    assert "bench_event" in dv.next_step(fridge)
    try:
        asyncio.run(bench.envelope({"op": "history", "device": "sim-air"}))
        raise AssertionError("expected a refusal")
    except bench.BenchError as exc:
        assert "no driver" in str(exc)
    srv.shutdown()
    monkeypatch.setenv("KIMIOS_ENVELOPE_SOCKET", str(tmp_path / "none.sock"))
    assert not bench.available() and bench.listing_sync() == []


# ---------------------------------------------------------------------------------------------- 2.31.0
def test_plan_width_counts_the_steps_that_can_run_together():
    from kimilm.orchestrator import plan_width
    T = lambda deps, model=None, status="waiting": {"depends_on": deps, "model": model, "status": status}  # noqa: E731
    two_topics = {"a": T([]), "b": T([]), "c": T(["a", "b"])}
    assert plan_width(two_topics) == 2
    chain = {"a": T([]), "b": T(["a"]), "c": T(["b"])}
    assert plan_width(chain) == 1
    fan = {"get": T([]), "x": T(["get"]), "y": T(["get"]), "z": T(["get"]), "report": T(["x", "y", "z"])}
    assert plan_width(fan) == 3
    fan["x"]["status"] = "done"
    assert plan_width(fan) == 2                                   # what is done no longer counts
    mixed = {"a": T([], "m1"), "b": T([], "m1"), "c": T([], "cloud:x")}
    assert plan_width(mixed, "m1") == 2 and plan_width(mixed) == 3


def test_reply_slots_are_as_many_as_memory_holds():
    from types import SimpleNamespace
    from kimilm.runtime import Runtime
    GB = 2**30
    fake = SimpleNamespace(fit=lambda m, tokens, n, top=None: {"ctx": 16384, "parallel": n},
                           memory=lambda m, over: {"free": 6 * GB, "reclaimable": 0,
                                                   "need": int(4 * GB + over["parallel"] * 0.5 * GB)})
    assert Runtime.slots_for(fake, "m", 16000, 4) == 2            # 4+2×0.5+1 margin ≤ 6; three slots would not fit
    fake.memory = lambda m, over: {"free": 20 * GB, "reclaimable": 0, "need": 5 * GB}
    assert Runtime.slots_for(fake, "m", 16000, 4) == 4
    fake.memory = lambda m, over: {"free": 1 * GB, "reclaimable": 0, "need": 5 * GB}
    assert Runtime.slots_for(fake, "m", 16000, 4) == 1


def test_auto_order_starts_independent_steps_together_on_one_local_model(tmp_path):
    from types import SimpleNamespace
    from kimilm.orchestrator import Orchestra, RunFit
    o = Orchestra.__new__(Orchestra)
    o.db = _Settings(max_parallel_agents=4)
    o.order, o.sequence, o.max_parallel = "auto", False, 4
    o.fit = RunFit(mode="auto")
    o.fit.slots["m1"] = 3                                         # two agents and the planner's checks
    o.turn = SimpleNamespace(model_id="m1")
    o.app = SimpleNamespace(models=SimpleNamespace(get_any=lambda m: {}))
    o.model_name = lambda m: m
    W = lambda deps, model="m1": {"status": "waiting", "depends_on": deps, "model": model}  # noqa: E731
    o.tasks = {"a": {**W([]), "id": "a"}, "b": {**W([]), "id": "b"}, "c": {**W([]), "id": "c"},
               "d": {**W(["a"]), "id": "d"}, "e": {**W([], "m2"), "id": "e"}, "f": {**W([], "cloud:x"), "id": "f"}}
    ids = [t["id"] for t in o.startable()]
    assert [i for i in ids if i != "f"][:2] == ["a", "b"] and "c" not in ids and "d" not in ids
    assert "f" in ids                                             # an online model runs alongside
    assert "e" not in ids and "one local model at a time" in o.tasks["e"]["wait_reason"]
    assert "free reply slot" in o.tasks["c"]["wait_reason"]
    assert o.fit.overrides(SimpleNamespace(fit=lambda m, t, n, top=None: {"ctx": 8192, "parallel": n}), "m1") == \
        {"ctx": 8192, "parallel": 3}
    o.order, o.sequence = "sequence", True
    assert [t["id"] for t in o.startable()] == ["a"]


def _eval_app(rows):
    from types import SimpleNamespace
    return SimpleNamespace(db=SimpleNamespace(all=lambda sql, args=(): rows))


def test_skill_profiles_count_the_newest_result_of_each_task():
    """2.32.0: Check this setup's tasks are tagged with skills; a model's profile is the newest result per task."""
    from kimilm import abilities
    from kimilm.evals import TASKS
    assert {s for t in TASKS for s in t["skills"]} == set(abilities.SKILL_IDS)
    assert all(sum(1 for t in TASKS if k in t["skills"]) >= 1 for k in abilities.SKILL_IDS)
    rows = [  # newest first, as the query orders them
        {"model": "big", "created": "2026-11-20", "version": "2.32.0", "results": [
            {"id": "sources", "passed": True, "seconds": 40, "read": 900},
            {"id": "browse", "passed": True, "seconds": 30, "read": 800},
            {"id": "plan", "passed": True, "seconds": 20, "read": 700}]},
        {"model": "small", "created": "2026-11-19", "version": "2.32.0", "results": [
            {"id": "sources", "passed": False, "seconds": 10, "read": 500},
            {"id": "find", "passed": True, "seconds": 5, "read": 300},
            {"id": "browse", "passed": True, "seconds": 9, "read": 400},
            {"id": "plan", "passed": False, "seconds": 8, "read": 300},
            {"id": "calc", "passed": False, "error": "LLMUnreachable: gone"}]},      # never worked: no verdict
        {"model": "big", "created": "2026-11-01", "version": "2.31.0", "results": [
            {"id": "sources", "passed": False, "seconds": 90, "read": 900}]},           # older: replaced
    ]
    profs = abilities.profiles(_eval_app(rows))
    assert profs["big"]["skills"]["research"] == {"passed": 1, "ran": 1, "seconds": 40.0, "score": 1.0}
    assert profs["small"]["skills"]["research"]["passed"] == 1 and profs["small"]["skills"]["research"]["ran"] == 2
    assert "data" not in profs["small"]["skills"]
    assert profs["big"]["tested"] == "2026-11-20"
    cands = [{"id": "small", "name": "Small 4B", "kind": "local", "strength": 2, "role": "worker"},
             {"id": "big", "name": "Big 14B", "kind": "local", "strength": 3, "role": "worker"},
             {"id": "cloud:x", "name": "Online X", "kind": "cloud", "strength": 5, "role": "strategist"}]
    mid, why = abilities.best_for("research", cands, profs, "small")
    assert mid == "big" and "1/1 research" in why and "Small 4B passed 1/2" in why
    assert abilities.best_for("browsing", cands, profs, "small") == (None, "")    # both pass: no reason to swap
    assert abilities.best_for("coding", cands, profs, "small") == (None, "")      # nobody tested: no guess
    better = abilities.planners(cands, profs, "small")
    assert [c["id"] for c, _ in better] == ["big", "cloud:x"] and "1/1 planning" in better[0][1]
    text = abilities.brief(cands, profs, "small")
    assert "small = Small 4B" in text and "research 1/2" in text and "the default model" in text
    assert "Online X (online, paid per use): not tested here; strength 5 of 5" in text


def test_planned_steps_go_to_the_model_that_measured_best_at_their_kind_of_work():
    from types import SimpleNamespace
    from kimilm.orchestrator import Orchestra, plan_schema
    o = Orchestra.__new__(Orchestra)
    o.turn = SimpleNamespace(model_id="small")
    o.logs = []
    o.logline = o.logs.append
    o.model_name = lambda m: m
    cands = [{"id": "small", "name": "Small", "kind": "local"}, {"id": "big", "name": "Big", "kind": "local"}]
    o.choices = {"cands": cands, "profs": {"small": {"skills": {"research": {"passed": 0, "ran": 2, "score": 0.0,
                                                                            "seconds": 9}}},
                                           "big": {"skills": {"research": {"passed": 2, "ran": 2, "score": 1.0,
                                                                          "seconds": 30}}}}}
    steps = [{"id": "t1", "skill": "research"}, {"id": "t2", "skill": "writing"}, {"id": "t3"}]
    o.assign_by_skill(steps)
    assert steps[0]["model"] == "big" and steps[0]["model_by"] == "results" and "2/2 research" in steps[0]["model_why"]
    assert "model" not in steps[1] and "model" not in steps[2]
    assert o.choice_ids() == ["small", "big"]
    schema = plan_schema(["agent"], ["worker"], 4, True, models=["small", "big"], skills=["research"],
                         planners=["big"])
    task = schema["properties"]["tasks"]["items"]["properties"]
    assert task["model"]["enum"] == ["small", "big"] and task["skill"]["enum"] == ["research"]
    assert schema["properties"]["planner"]["enum"] == ["big"] and schema["properties"]["model"]["enum"]
    assert "planner" not in plan_schema(["agent"], ["worker"], 4, True)["properties"]


def test_a_snapshot_is_taken_once_before_the_first_change_of_an_unattended_reply(tmp_path, monkeypatch):
    """2.32.0 (KimiOS 0.11.0): before an auto-approved change, KimiOS keeps the home folder as it was."""
    import asyncio
    import sys
    from types import SimpleNamespace
    from kimilm import undo
    from kimilm.runner import Turn
    from kimilm.tools import Tool
    log = tmp_path / "calls.txt"
    cli = tmp_path / "kimios-snapshot"
    cli.write_text(f"#!{sys.executable}\nimport json, sys\nopen({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + "
                   "'\\n')\nprint(json.dumps({'ok': True, 'id': 's1', 'kind': 'btrfs'} if sys.argv[1] == 'take' else "
                   "{'ok': False, 'error': 'no snapshot \\'x\\''}))\n")
    cli.chmod(0o755)
    monkeypatch.setenv("KIMIOS_SNAPSHOT_CLI", str(cli))
    assert undo.available()
    with pytest.raises(undo.UndoError, match="no snapshot"):
        undo.run("restore", "x", "~/a")
    turn = SimpleNamespace(db=_Settings(snapshot_before_changes=True), chat={"title": "Monthly report"}, steps=[],
                           notes=[])
    turn.note = turn.notes.append
    tool = Tool("write_file", "", {}, None, "files", changes=True, label="Write file")
    asyncio.run(Turn.snapshot_first(turn, tool))
    asyncio.run(Turn.snapshot_first(turn, tool))                     # once per reply
    assert log.read_text().count("take --reason before Write file in “Monthly report”") == 1
    assert turn.steps == [{"type": "snapshot", "id": "s1", "reason": "before Write file in “Monthly report”"}]
    assert "Activity → Undo" in turn.notes[0]
    off = SimpleNamespace(db=_Settings(snapshot_before_changes=False), chat={}, steps=[], notes=[])
    asyncio.run(Turn.snapshot_first(off, tool))
    assert log.read_text().count("take") == 1                        # switched off: nothing taken


# ---------------------------------------------------------------------------------------------- displays (2.33.0)
CAPS = """Model: HP M27fw FHD
MCCS version: 2.1
VCP Features:
   Feature: 02 (New control value)
   Feature: 10 (Brightness)
   Feature: 12 (Contrast)
   Feature: 14 (Select color preset)
      Values:
         05: 6500 K
         08: 9300 K
         0b: User 1
   Feature: 16 (Video gain: Red)
   Feature: 18 (Video gain: Green)
   Feature: 1A (Video gain: Blue)
   Feature: 60 (Input Source)
      Values:
         11: HDMI-1
         12: HDMI-2
   Feature: D6 (Power mode)
      Values: 01 04 (interpretation unavailable)
   Feature: DF (VCP Version)
"""
GETVCP = "VCP 10 C 70 100\nVCP 12 C 80 100\nVCP 14 SNC x05\nVCP 16 C 50 100\nVCP 18 C 50 100\n" \
         "VCP 1A C 48 100\nVCP 60 SNC x11\nVCP D6 SNC x01\n"
DETECT = "Display 1\n   I2C bus:  /dev/i2c-5\n   DRM connector:  card1-HDMI-A-1\n   Monitor: HPN:HP M27fw FHD\n"


def _fake_ddc(calls):
    async def sh(argv, *a, **k):
        calls.append(argv)
        if argv[1:3] == ["detect", "--terse"]:
            return 0, DETECT
        if "capabilities" in argv:
            return 0, CAPS
        if "getvcp" in argv:
            return 0, GETVCP
        return 0, ""
    return sh


def test_the_monitor_says_what_it_offers_and_takes_its_own_names(monkeypatch):
    from kimilm import display
    feats = display.parse_capabilities(CAPS)
    assert feats["60"]["values"] == {"11": "HDMI-1", "12": "HDMI-2"}
    assert set(feats["D6"]["values"]) == {"01", "04"}               # listed on one line, named from MCCS
    assert display.parse_getvcp(GETVCP)["60"] == {"kind": "NC", "value": "11"}
    calls = []
    monkeypatch.setattr(display, "sh", _fake_ddc(calls))
    st = asyncio.run(display.monitor_state(None, "card1-HDMI-A-1"))
    by = {r["name"]: r for r in st["settings"]}
    assert st["bus"] == "5" and st["model"] == "HP M27fw FHD"
    assert by["contrast"]["current"] == 80 and by["input"]["current_name"] == "HDMI-1"
    assert [o["name"] for o in by["power"]["options"]] == ["on", "off (soft)"]
    assert "speaker_volume" not in by                                # not offered by this monitor
    assert asyncio.run(display.monitor_set(None, "card1-HDMI-A-1", "input", "hdmi 2")) == "Input set to HDMI-2"
    assert calls[-1][-3:] == ["setvcp", "60", "x12"]
    asyncio.run(display.monitor_set(None, "card1-HDMI-A-1", "red", "140"))
    assert calls[-1][-3:] == ["setvcp", "16", "100"]                 # kept within the monitor's maximum
    with pytest.raises(display.DeviceError, match="HDMI-1, HDMI-2"):
        asyncio.run(display.monitor_set(None, "card1-HDMI-A-1", "input", "VGA"))
    with pytest.raises(display.DeviceError, match="does not offer"):
        asyncio.run(display.monitor_set(None, "card1-HDMI-A-1", "speaker_volume", 30))


def _mode(mid, w, h, r, cur=False, pref=False, scales=(1.0, 1.25, 1.5, 2.0)):
    return [mid, w, h, r, 1.0, list(scales), {"is-current": {"type": "b", "data": cur},
                                              "is-preferred": {"type": "b", "data": pref}}]


MUTTER_STATE = {"type": "ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv}", "data": [
    7,
    [[["eDP-1", "BOE", "0x0a1c", "0"], [_mode("1920x1080@60.000", 1920, 1080, 60.0, True, True)],
      {"is-builtin": {"type": "b", "data": True}, "display-name": {"type": "s", "data": "Built-in display"}}],
     [["HDMI-1", "HPN", "HP M27fw FHD", "X"], [_mode("1920x1080@60.000", 1920, 1080, 60.0, True, True),
                                               _mode("1920x1080@74.973", 1920, 1080, 74.973),
                                               _mode("1280x720@60.000", 1280, 720, 60.0, scales=(1.0,))],
      {"display-name": {"type": "s", "data": "HP 27\""}}]],
    [[0, 0, 1.25, 0, True, [["eDP-1", "BOE", "0x0a1c", "0"]], {}],
     [1536, 0, 1.0, 0, False, [["HDMI-1", "HPN", "HP M27fw FHD", "X"]], {}]],
    {"layout-mode": {"type": "u", "data": 1}}]}


def test_gnome_screens_are_read_and_changed_as_gnome_wants_them():
    from kimilm import display
    st = display.parse_mutter(MUTTER_STATE)
    hdmi = display.find_screen(st, "card1-HDMI-A-1")                 # the kernel's name finds GNOME's
    assert hdmi["name"] == "HDMI-1" and hdmi["mode"] == "1920x1080@60" and hdmi["position"] == {"x": 1536, "y": 0}
    screens, said = display.plan(st, "card1-HDMI-A-1", refresh="75", position="left-of eDP-1")
    me = next(s for s in screens if s["name"] == "HDMI-1")
    edp = next(s for s in screens if s["name"] == "eDP-1")
    assert me["mode"]["id"] == "1920x1080@74.973"
    assert (me["x"], edp["x"]) == (0, 1920)                          # moved so nothing is left of 0
    assert "75 Hz" in said and "left of eDP-1" in said
    screens, said = display.plan(st, "card1-HDMI-A-1", mode="1280x720", scale="1.25")
    assert next(s for s in screens if s["name"] == "HDMI-1")["scale"] == 1.0   # the only scale it offers there
    screens, _ = display.plan(st, "card1-HDMI-A-1", rotation="90", primary="true")
    argv = display.mutter_argv(7, screens)
    assert argv[argv.index("ApplyMonitorsConfig") + 1:][:4] == ["uua(iiduba(ssa{sv}))a{sv}", "7", "2", "2"]
    tail = argv[argv.index("HDMI-1") - 6:argv.index("HDMI-1") + 3]
    assert tail == ["1536", "0", "1.0", "1", "true", "1", "HDMI-1", "1920x1080@60.000", "0"]
    assert argv[argv.index("eDP-1") - 2] == "false"                  # one main screen
    screens, said = display.plan(st, "card1-HDMI-A-1", enabled="false")
    assert [s["name"] for s in screens] == ["eDP-1"] and said == "HDMI-1: off"
    only = {**st, "outputs": [o for o in st["outputs"] if o["name"] == "HDMI-1"]}
    with pytest.raises(display.DeviceError, match="only screen"):
        display.plan(only, "card1-HDMI-A-1", enabled="false")
    with pytest.raises(display.DeviceError, match="it can: 1920x1080, 1280x720"):
        display.plan(st, "card1-HDMI-A-1", mode="3840x2160")


def test_wlroots_screens_are_read_with_wlr_randr():
    from kimilm import display
    st = {"outputs": display.parse_wlr_randr(json.dumps([
        {"name": "HDMI-A-1", "description": "HP M27fw", "enabled": True, "scale": 1.0, "transform": "normal",
         "position": {"x": 0, "y": 0}, "modes": [{"width": 1920, "height": 1080, "refresh": 60.0, "current": True,
                                                  "preferred": True}]}]))}
    o = display.find_screen(st, "card0-HDMI-A-1")
    assert o["mode"] == "1920x1080@60" and o["rotation"] == "0"
    screens, said = display.plan(st, "card0-HDMI-A-1", rotation="180", normalize=False)
    assert screens[0]["transform"] == 2 and said == "HDMI-A-1: rotated 180°"


def test_an_external_display_offers_its_own_settings_and_a_built_in_one_only_the_layout(tmp_path):
    from kimilm.devices import Devices
    dev = Devices.__new__(Devices)
    ext = [a["name"] for a in dev.actions({"kind": "display", "internal": False, "name": "HP"})]
    built = [a["name"] for a in dev.actions({"kind": "display", "internal": True, "name": "Built-in screen"})]
    if os.name == "posix" and sys.platform != "darwin":
        assert {"monitor_settings", "monitor_set", "screen_layout", "screen_set"} <= set(ext)
        assert "monitor_set" not in built and "screen_set" in built


# ---------------------------------------------------------------------------------------------- 2.37.0: names
def test_settings_given_by_their_old_names_still_work():
    import subprocess
    code = ("import json, os, kimilm; print(json.dumps({k: os.environ.get(k) for k in "
            "('KIMILM_SD_CLI', 'KIMILM_UI_DIR', 'KIMILM_TOKEN', 'DVA_TOKEN')}))")
    env = {**os.environ, "DVA_SD_CLI": "/opt/sd", "DVA_UI_DIR": "/old", "KIMILM_UI_DIR": "/new", "DVA_TOKEN": "t"}
    env.pop("KIMILM_SD_CLI", None)
    env.pop("KIMILM_TOKEN", None)
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ENGINE), env=env, capture_output=True, text=True)
    seen = json.loads(out.stdout)
    assert seen["KIMILM_SD_CLI"] == "/opt/sd", "an old name is read as the new one"
    assert seen["KIMILM_UI_DIR"] == "/new", "the new name wins when both are given"
    assert seen["KIMILM_TOKEN"] == "t" and seen["DVA_TOKEN"] is None, "the old token is not passed on"
    from kimilm import with_old_names
    assert with_old_names({"KIMILM_PLUGIN_DATA": "/d", "KIMILM_TOKEN": "x", "PATH": "/bin"}) == {
        "KIMILM_PLUGIN_DATA": "/d", "DVA_PLUGIN_DATA": "/d", "KIMILM_TOKEN": "x", "PATH": "/bin"}


def test_queued_messages_are_released_after_a_restart(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("KIMILM_SECRETS", "file")
    from kimilm.app_state import App
    data = tmp_path / "KimiLM"
    data.mkdir()
    con = sqlite3.connect(str(data / "kimilm.sqlite3"))
    con.executescript("CREATE TABLE messages (id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, "
                      "content TEXT DEFAULT '', meta_json TEXT DEFAULT '{}', created TEXT);")
    con.execute("INSERT INTO messages VALUES ('u1', 'c1', 'user', 'later please', ?, '2026-11-15T08:00:00.000+00:00')",
                (json.dumps({"attachments": [], "pending": "queue"}),))
    con.commit()
    con.close()
    app = App(str(data))
    queued = app.db.get("messages", "u1")["meta"]
    assert queued.get("not_sent") == "restart" and "pending" not in queued
    app.db.conn.close()
