"""Credential storage in a user-only JSON file."""

import json
import os
from pathlib import Path


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(os.environ.get("ADMIRE_CONFIG_DIR") or Path(base) / "admire-cli")


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else config_dir() / "credentials.json"

    def load(self):
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    def clear(self):
        self.path.unlink(missing_ok=True)
