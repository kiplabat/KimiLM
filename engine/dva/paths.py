"""Where things live.

DATA   = the user's data folder (database, workspaces, user skills/harnesses/plugins, logs).
MODELS = model files (can be moved to another drive in Settings; defaults to DATA/models).
ENGINE = this package's folder (built-in harnesses, skills and plugins ship beside it).
RES    = the packaged app's resources folder (bundled llama.cpp, embedded Python), if any.
"""
import os
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
BUILTIN = ENGINE / "builtin"
DB_NAME = "kimilm.sqlite3"
LEGACY_DB_NAMES = ("datavera-agent.sqlite3",)   # the app was called Datavera Agent before 2.2.0


class Paths:
    def __init__(self, data_dir=None, resources_dir=None):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
        default = base / "KimiLM"
        if not default.exists() and (base / "DataveraAgent").exists():   # development data from before the rename
            default = base / "DataveraAgent"
        self.data = Path(data_dir or os.environ.get("DVA_DATA_DIR") or default).resolve()
        res = resources_dir or os.environ.get("DVA_RESOURCES")
        self.resources = Path(res).resolve() if res else None
        for sub in ("workspaces", "skills", "harnesses", "plugins", "plugin-data", "jobs", "logs", "runtimes",
                    "uploads", "exports"):
            (self.data / sub).mkdir(parents=True, exist_ok=True)
        self._adopt_legacy_db()

    def _adopt_legacy_db(self):
        """Give the database from before the rename its new file name (with its -wal/-shm files, so nothing
        written but not yet checkpointed is lost). All or nothing: on an error the files go back."""
        if (self.data / DB_NAME).exists():
            return
        for name in LEGACY_DB_NAMES:
            if not (self.data / name).exists():
                continue
            done = []
            try:
                for suffix in ("-wal", "-shm", ""):          # the main file last
                    src = self.data / (name + suffix)
                    if src.exists():
                        src.rename(self.data / (DB_NAME + suffix))
                        done.append(suffix)
            except OSError:
                for suffix in done:
                    (self.data / (DB_NAME + suffix)).rename(self.data / (name + suffix))
                raise
            return

    @property
    def db(self):
        return self.data / DB_NAME

    def models_dir(self, settings=None):
        custom = (settings or {}).get("models_dir")
        folder = Path(custom) if custom else self.data / "models"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def workspace(self, chat_id):
        folder = self.data / "workspaces" / chat_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def plugin_data(self, plugin_id):
        folder = self.data / "plugin-data" / plugin_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def bundled_runtime(self, backend):
        """llama.cpp shipped inside the installer (CPU build)."""
        if self.resources:
            folder = self.resources / "llama" / backend
            if folder.exists():
                return folder
        return None

    @property
    def python(self):
        """The Python that runs pipelines, skills' scripts and run_python: the engine's own interpreter."""
        return sys.executable
