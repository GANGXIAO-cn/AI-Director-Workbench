"""Keys entered in the workbench are stored in its local configuration file."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path

from .storage import write_atomic


class LocalCredentials:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "credentials.local.json"

    def _read(self):
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
            raise ValueError("本机密钥配置文件格式无效")
        return values

    def get(self, name):
        return self._read().get(name)

    @contextmanager
    def _edit(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            values = self._read()
            yield values
            write_atomic(self.path, (json.dumps(values, ensure_ascii=False, indent=2) + "\n").encode())

    def set(self, name, value):
        with self._edit() as values:
            values[name] = value

    def forget(self, name):
        if self.get(name) is not None:
            with self._edit() as values:
                values.pop(name, None)
