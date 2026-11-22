"""Append-only journal for task attempts and recovery decisions."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from contextlib import contextmanager


class TaskJournalError(RuntimeError):
    """Raised when task lifecycle events cannot be persisted."""


class TaskEventJournal:
    """Durably append task events to a separate JSONL journal."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.path.touch(exist_ok=True)

    @contextmanager
    def _process_lock(self):
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def append(
        self,
        *,
        task_id: str,
        event: str,
        attempt: int,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not task_id.strip() or not event.strip() or attempt < 0:
            raise TaskJournalError("task_id/event must be non-empty and attempt non-negative")
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "event": event,
            "attempt": attempt,
            "details": details or {},
        }
        payload = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock, self._process_lock():
            try:
                with self.path.open("ab") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise TaskJournalError(f"Could not append task event: {exc}") from exc
        return entry
