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
