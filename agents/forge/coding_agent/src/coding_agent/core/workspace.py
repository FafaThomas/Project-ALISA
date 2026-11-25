"""Workspace access: guarded reads, a proposal overlay, and journaled writes."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .models import FileChange

AGENT_DIR = ".coding-agent"
IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", AGENT_DIR, ".venv", "venv", "env", "__pycache__", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".idea", ".vscode", "dist", "build",
    "target", "bin", "obj", ".next", ".gradle", "coverage",
})
SECRET_NAMES = frozenset({".env", "id_rsa", "id_ed25519", "credentials.json", "secrets.json"})
SECRET_SUFFIXES = (".pem", ".key", ".pfx", ".p12")
MAX_READ_BYTES = 400_000


class WorkspaceError(ValueError):
    """A path or write was rejected by the workspace guard."""


# Templates that document settings without holding real values.
SECRET_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def is_secret(path: PurePosixPath) -> bool:
    name = path.name.lower()
    if name.startswith(".env.") and name.endswith(SECRET_TEMPLATE_SUFFIXES):
        return False
    return name in SECRET_NAMES or name.startswith(".env.") or name.endswith(SECRET_SUFFIXES)


class Workspace:
    """A project directory the team may read and (in apply mode) write."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    @property
    def agent_dir(self) -> Path:
        return self.root / AGENT_DIR

    def normalize(self, relative: str) -> str:
        """Validate a model-proposed path and return it in POSIX form."""
        text = (relative or "").strip().replace("\\", "/")
        if not text:
            raise WorkspaceError("empty path")
        pure = PurePosixPath(text)
        if pure.is_absolute() or (len(text) > 1 and text[1] == ":"):
            raise WorkspaceError(f"absolute paths are not allowed: {relative!r}")
        parts = [p for p in pure.parts if p not in ("", ".")]
        if not parts or any(p == ".." for p in parts):
            raise WorkspaceError(f"path escapes the workspace: {relative!r}")
        if parts[0] in (".git", AGENT_DIR):
            raise WorkspaceError(f"path is reserved: {relative!r}")
        normalized = PurePosixPath(*parts)
        if is_secret(normalized):
            raise WorkspaceError(f"refusing to touch a secrets file: {relative!r}")
        full = (self.root / normalized).resolve()
        if full != self.root and self.root not in full.parents:
            raise WorkspaceError(f"path escapes the workspace: {relative!r}")
        return normalized.as_posix()

    def path(self, relative: str) -> Path:
        return self.root / self.normalize(relative)

    def read_text(self, relative: str) -> str | None:
        try:
            target = self.path(relative)
        except WorkspaceError:
            return None
        if not target.is_file() or target.stat().st_size > MAX_READ_BYTES:
            return None
        try:
            return target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return None

    def list_files(self, limit: int = 2000) -> list[str]:
        if not self.exists:
            return []
        found: list[str] = []
        stack = [self.root]
        while stack and len(found) < limit:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir(), key=lambda p: p.name.lower(), reverse=True)
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir():
                    if entry.name not in IGNORED_DIRS and not entry.name.endswith(".egg-info"):
                        stack.append(entry)
                elif entry.is_file():
                    rel = PurePosixPath(entry.relative_to(self.root).as_posix())
                    if not is_secret(rel):
                        found.append(rel.as_posix())
        return sorted(found)[:limit]

    # ------------------------------------------------------------------ writes
    def apply(self, changes: list[FileChange], run_dir: Path) -> list[str]:
        """Write changes, backing up anything overwritten. Returns written paths."""
        backup_dir = run_dir / "backup"
        journal = run_dir / "changes.jsonl"
        run_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        with journal.open("a", encoding="utf-8") as log:
            for change in changes:
                rel = self.normalize(change.path)
                target = self.root / rel
                existed = target.is_file()
                if existed:
                    backup = backup_dir / rel
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                if change.action == "delete":
                    if existed:
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(change.content, encoding="utf-8", newline="\n")
                written.append(rel)
                entry = {k: v for k, v in asdict(change).items() if k != "content"}
                entry.update(path=rel, existed=existed, at=datetime.now(timezone.utc).isoformat())
                log.write(json.dumps(entry) + "\n")
        return written


# Work-item ids that may change any file: repair rounds fix whatever the files gate reports.
UNOWNED_WRITERS = frozenset({"repair", ""})


class ChangeSet:
    """Proposed changes layered over the workspace.

    Files are owned by the work item a lead assigned them to. A change made for
    a different work item is rejected, so one specialist cannot overwrite a
    teammate's file. Files nobody claimed (e.g. a new helper module) are open
    to whoever creates them, and repairs may change any file.
    """

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self._changes: dict[str, FileChange] = {}
        self._owners: dict[str, tuple[str, str]] = {}   # path -> (work item id, owner role)

    def claim(self, path: str, work_item_id: str, owner_role: str) -> None:
        """Record that ``path`` belongs to a work item. The first claim wins."""
        try:
            rel = self.workspace.normalize(path)
        except WorkspaceError:
            return
        self._owners.setdefault(rel, (work_item_id, owner_role))

    def owner(self, path: str) -> tuple[str, str] | None:
        try:
            return self._owners.get(self.workspace.normalize(path))
        except WorkspaceError:
            return None

    @staticmethod
    def _same_item(owner_item: str, writer_item: str) -> bool:
        # Sub-items of a team specialist are named "<item>.<sub>".
        return writer_item == owner_item or writer_item.startswith(owner_item + ".")

    def add(self, change: FileChange) -> FileChange:
        change.path = self.workspace.normalize(change.path)
        owner = self._owners.get(change.path)
        if owner and change.work_item_id not in UNOWNED_WRITERS and not self._same_item(owner[0], change.work_item_id):
            raise WorkspaceError(
                f"{change.path} belongs to work item {owner[0]} ({owner[1]}); "
                f"{change.author_role or 'this specialist'} on {change.work_item_id} may not change it")
        if change.action != "delete":
            # The label describes the effect on disk, however many times the file changed this run.
            on_disk = (self.workspace.root / change.path).is_file()
            change.action = "modify" if on_disk else "create"
        self._changes[change.path] = change
        return change

    def current(self, path: str) -> str | None:
        """Content of a path as the team currently sees it."""
        try:
            rel = self.workspace.normalize(path)
        except WorkspaceError:
            return None
        change = self._changes.get(rel)
        if change is not None:
            return None if change.action == "delete" else change.content
        return self.workspace.read_text(rel)

    def paths(self) -> list[str]:
        return list(self._changes)

    def changes(self) -> list[FileChange]:
        return list(self._changes.values())

    def __len__(self) -> int:
        return len(self._changes)
