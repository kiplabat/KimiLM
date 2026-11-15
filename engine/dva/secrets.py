"""Secrets (API keys, tokens) go to the operating system's credential store: Windows Credential Manager,
macOS Keychain. If no store is available (some Linux setups), they fall back to a file only this user can read,
and the UI says so. Secrets are never returned to the UI after they are saved.

Keys saved before the app was renamed (service "DataveraAgent") are still found: the first read copies them to
the KimiLM service and leaves the old entry in place; deleting a key removes both."""
import json
import os
import stat
from pathlib import Path

SERVICE = "KimiLM"
LEGACY_SERVICES = ("DataveraAgent",)


class Secrets:
    def __init__(self, data_dir):
        self.fallback_path = Path(data_dir) / "secrets.json"
        self.backend = "file"
        if os.environ.get("DVA_SECRETS") == "file":
            return
        try:
            import keyring
            keyring.set_password(SERVICE, "__probe__", "ok")
            if keyring.get_password(SERVICE, "__probe__") == "ok":
                keyring.delete_password(SERVICE, "__probe__")
                self.backend = type(keyring.get_keyring()).__name__
                self._kr = keyring
        except Exception:
            self.backend = "file"

    @property
    def secure(self):
        return self.backend != "file"

    def _load(self):
        if self.fallback_path.exists():
            return json.loads(self.fallback_path.read_text(encoding="utf-8"))
        return {}

    def _save(self, values):
        self.fallback_path.write_text(json.dumps(values), encoding="utf-8")
        if os.name != "nt":
            self.fallback_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def get(self, name):
        if self.secure:
            try:
                value = self._kr.get_password(SERVICE, name)
            except Exception:
                return None
            if value is None:
                value = self._legacy(name)
            return value
        return self._load().get(name)

    def _legacy(self, name):
        for service in LEGACY_SERVICES:
            try:
                value = self._kr.get_password(service, name)
            except Exception:
                continue
            if value is not None:
                try:
                    self._kr.set_password(SERVICE, name, value)
                except Exception:
                    pass
                return value
        return None

    def set(self, name, value):
        if not value:
            return self.delete(name)
        if self.secure:
            self._kr.set_password(SERVICE, name, value)
        else:
            values = self._load()
            values[name] = value
            self._save(values)

    def delete(self, name):
        if self.secure:
            for service in (SERVICE, *LEGACY_SERVICES):
                try:
                    self._kr.delete_password(service, name)
                except Exception:
                    pass
        else:
            values = self._load()
            values.pop(name, None)
            self._save(values)

    def has(self, name):
        return bool(self.get(name))
