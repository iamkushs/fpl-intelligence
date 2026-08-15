from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import RunRecord


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.records: dict[int, RunRecord] = {}

    def load(self) -> dict[int, RunRecord]:
        if self.path.is_file():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = {int(key): RunRecord.from_dict(value) for key, value in raw.get("issues", {}).items()}
        return self.records

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        payload: dict[str, Any] = {"version": 2, "issues": {str(key): value.to_dict() for key, value in self.records.items()}}
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.path)


class RunnerLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("Another FPL Symphony runner appears to be active.") from exc
        self.handle.seek(0); self.handle.truncate(); self.handle.write(str(os.getpid())); self.handle.flush()

    def release(self) -> None:
        if not self.handle:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0); msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close(); self.handle = None

    def __enter__(self) -> RunnerLock:
        self.acquire(); return self

    def __exit__(self, *_: object) -> None:
        self.release()
