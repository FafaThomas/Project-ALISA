"""Durable append-only journal for Coding Agent changes."""

from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from contextlib import contextmanager
from typing import Iterator


class JournalError(Exception):
    """Raised when a journal operation fails."""


@dataclass(frozen=True)
class JournalEntry:
    """One durable change-lifecycle event."""

    sequence: int
    timestamp: str
    change_id: str
    event: str
    relative_path: str
    operation: str
    state: str
    original_hash: str | None = None
    resulting_hash: str | None = None
    recovery_bytes_b64: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "change_id": self.change_id,
            "event": self.event,
            "relative_path": self.relative_path,
            "operation": self.operation,
            "state": self.state,
            "original_hash": self.original_hash,
            "resulting_hash": self.resulting_hash,
            "recovery_bytes_b64": self.recovery_bytes_b64,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "JournalEntry":
        try:
            return cls(**value)
        except (TypeError, ValueError) as exc:
            raise JournalError(f"Invalid journal entry: {exc}") from exc

    def recovery_bytes(self) -> bytes | None:
        """Decode persisted original bytes, if present."""
        if self.recovery_bytes_b64 is None:
            return None
        try:
            return base64.b64decode(
                self.recovery_bytes_b64,
                validate=True,
            )
        except (ValueError, base64.binascii.Error) as exc:
            raise JournalError("Invalid recovery payload encoding") from exc


class ChangeJournal:
    """Append-only JSONL journal with process-local write serialization."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if not self.path.exists():
            with self.path.open("xb"):
                pass

    def append(
        self,
        *,
        change_id: str,
        event: str,
        relative_path: str,
        operation: str,
        state: str,
        original_hash: str | None = None,
        resulting_hash: str | None = None,
        recovery_bytes: bytes | None = None,
        error: str | None = None,
    ) -> JournalEntry:
        """Persist one event before returning it to the caller."""
        if not change_id.strip():
            raise JournalError("change_id must not be empty")
        if not event.strip():
            raise JournalError("event must not be empty")
        if not relative_path.strip():
            raise JournalError("relative_path must not be empty")

        with self._lock:
            with self._process_lock():
                try:
                    self._truncate_incomplete_tail()
                    sequence = self._next_sequence()
                    entry = JournalEntry(
                        sequence=sequence,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        change_id=change_id,
                        event=event,
                        relative_path=relative_path,
                        operation=operation,
                        state=state,
                        original_hash=original_hash,
                        resulting_hash=resulting_hash,
                        recovery_bytes_b64=(
                            base64.b64encode(recovery_bytes).decode("ascii")
                            if recovery_bytes is not None
                            else None
                        ),
                        error=error,
                    )

                    payload = (
                        json.dumps(entry.to_dict(), separators=(",", ":")) + "\n"
                    ).encode("utf-8")

                    with self.path.open("ab") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())

                    return entry
                except (OSError, ValueError, TypeError) as exc:
                    raise JournalError(f"Could not append journal entry: {exc}") from exc

    
    def read_all(self) -> list[JournalEntry]:
        """Read valid journal entries, ignoring an incomplete final record."""
        with self._lock:
            with self._process_lock():
                try:
                    raw = self.path.read_bytes()
                except OSError as exc:
                    raise JournalError(f"Could not read journal: {exc}") from exc

        entries: list[JournalEntry] = []
        lines = raw.splitlines(keepends=True)

        for line_number, line in enumerate(lines, start=1):
            # append() always terminates records with a newline. A final
            # unterminated fragment may be the result of a process crash.
            if not line.endswith(b"\n"):
                if line_number == len(lines):
                    break
                raise JournalError(
                    f"Invalid journal record on line {line_number}: "
                    "unterminated record before end of file"
                )

            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise JournalError("Entry must be a JSON object")

                entry = JournalEntry.from_dict(value)
                if entry.sequence != len(entries) + 1:
                    raise JournalError("Journal sequence is invalid")

                entries.append(entry)
            except (json.JSONDecodeError, JournalError) as exc:
                raise JournalError(
                    f"Invalid journal record on line {line_number}: {exc}"
                ) from exc

        return entries


    def _next_sequence(self) -> int:
        """Determine the next sequence number from the existing journal."""
        if not self.path.exists():
            return 1

        try:
            with self.path.open("rb") as handle:
                return sum(1 for line in handle if line.strip()) + 1
        except OSError as exc:
            raise JournalError(f"Could not inspect journal: {exc}") from exc

    
    def _truncate_incomplete_tail(self) -> None:
        """Remove an unterminated final record before appending new data.

        A missing final newline is treated as an interrupted append.
        This method is only called when adding a new entry, never when reading.
        """
        try:
            with self.path.open("r+b") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()

                if size == 0:
                    return

                handle.seek(size - 1)
                if handle.read(1) == b"\n":
                    return

                # Preserve all newline-terminated records.
                handle.seek(0)
                data = handle.read()
                last_newline = data.rfind(b"\n")
                valid_end = last_newline + 1

                handle.truncate(valid_end)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise JournalError(
                f"Could not repair incomplete journal tail: {exc}"
            ) from exc

    
    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        """Serialize journal access across processes on Windows and POSIX."""
        try:
            with self.path.with_suffix(
                self.path.suffix + ".lock"
            ).open("a+b") as handle:
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
                        msvcrt.locking(
                            handle.fileno(), msvcrt.LK_UNLCK, 1
                        )
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        except OSError as exc:
            raise JournalError(
                f"Could not acquire or release journal process lock: {exc}"
            ) from exc

