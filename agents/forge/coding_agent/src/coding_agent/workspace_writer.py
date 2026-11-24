"""Controlled file writes for an authorized Coding Agent workspace."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from coding_agent.change_tracking import (
    ChangeState,
    WorkspaceChange,
    content_hash,
)
from coding_agent.models import ChangeType, FileChange
from coding_agent.workspace import Workspace, WorkspaceError
from coding_agent.write_guard import (
    WriteGuardError,
    validate_approved_change,
)


class WorkspaceWriteError(Exception):
    """Raised when a controlled workspace write cannot be completed."""


@dataclass(frozen=True)
class RecoveryRecord:
    """In-memory recovery information for one applied operation."""

    change_id: str
    relative_path: str
    operation: str
    original_bytes: bytes | None
    resulting_hash: str | None
    created_directories: tuple[str, ...] = ()


@dataclass(frozen=True)
class WriteResult:
    """Outcome of a controlled file operation."""

    change_id: str
    relative_path: str
    operation: str
    applied: bool
    resulting_hash: str | None
    recovery: RecoveryRecord | None = None


class WorkspaceWriter:
    """Apply approved changes within a Workspace."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def apply(
        self,
        change: WorkspaceChange,
        proposal: FileChange,
    ) -> WriteResult:
        """Apply one approved proposal after validating its preconditions."""
        try:
            authorization = validate_approved_change(
                self.workspace, change, proposal
            )
        except (WriteGuardError, WorkspaceError) as exc:
            raise WorkspaceWriteError(str(exc)) from exc

        # Resolve again immediately before the operation. This reduces the
        # validation window but does not eliminate all filesystem races.
        try:
            if proposal.change_type == ChangeType.CREATE:
                return self._create(change, proposal, authorization)
            if proposal.change_type == ChangeType.MODIFY:
                return self._modify(change, proposal, authorization)
            if proposal.change_type == ChangeType.DELETE:
                return self._delete(change, proposal, authorization)
        except WorkspaceWriteError:
            raise
        except OSError as exc:
            raise WorkspaceWriteError(
                f"File operation failed: {exc}"
            ) from exc

        raise WorkspaceWriteError(
            f"Unsupported operation: {proposal.change_type}"
        )

    def _resolve_existing(self, relative_path: str) -> Path:
        try:
            return self.workspace.resolve_path(relative_path)
        except WorkspaceError as exc:
            raise WorkspaceWriteError(str(exc)) from exc

    
    def _resolve_create_target(self, relative_path: str) -> Path:
        try:
            return self.workspace.resolve_create_path(relative_path)
        except WorkspaceError as exc:
            raise WorkspaceWriteError(str(exc)) from exc


    @staticmethod
    def _atomic_replace(target: Path, data: bytes) -> None:
        """Write bytes to a sibling temporary file, then atomically replace."""
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _create(self, change, proposal, authorization) -> WriteResult:
        target = self._resolve_create_target(proposal.path)
        data = proposal.content.encode("utf-8")
        if content_hash(data) != authorization.intended_result_hash:
            raise WorkspaceWriteError("Proposed content changed after validation")

        # Create only missing parents after approval, recording exactly which
        # directories this operation owns so rollback never removes old dirs.
        missing: list[Path] = []
        parent = target.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        created: list[Path] = []
        try:
            for directory in reversed(missing):
                directory.mkdir()
                created.append(directory)
            with target.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            result_hash = content_hash(target.read_bytes())
            if result_hash != authorization.intended_result_hash:
                raise WorkspaceWriteError(
                    "Created file did not match the intended content hash"
                )
        except Exception as exc:
            # If the write fails midway, remove only this operation's file and
            # empty directories. Never recursively delete user content.
            try:
                if target.exists() and target.is_file():
                    target.unlink()
                for directory in reversed(created):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            except OSError:
                pass
            if isinstance(exc, WorkspaceWriteError):
                raise
            if isinstance(exc, FileExistsError):
                raise WorkspaceWriteError(
                    "Creation target appeared before file creation"
                ) from exc
            raise WorkspaceWriteError(f"Could not create file: {exc}") from exc

        created_relative = tuple(
            directory.relative_to(self.workspace.root).as_posix()
            for directory in created
        )
        recovery = RecoveryRecord(
            change_id=change.change_id,
            relative_path=proposal.path,
            operation="create",
            original_bytes=None,
            resulting_hash=result_hash,
            created_directories=created_relative,
        )
        return WriteResult(
            change_id=change.change_id,
            relative_path=proposal.path,
            operation="create",
            applied=True,
            resulting_hash=result_hash,
            recovery=recovery,
        )

    def _modify(self, change, proposal, authorization) -> WriteResult:
        target = self._resolve_existing(proposal.path)

        original_bytes = target.read_bytes()
        if content_hash(original_bytes) != authorization.expected_original_hash:
            raise WorkspaceWriteError(
                "File changed after authorization; refusing to overwrite"
            )

        data = proposal.content.encode("utf-8")
        if content_hash(data) != authorization.intended_result_hash:
            raise WorkspaceWriteError("Proposed content changed after validation")

        self._atomic_replace(target, data)

        result_hash = content_hash(target.read_bytes())
        if result_hash != authorization.intended_result_hash:
            raise WorkspaceWriteError(
                "Modified file did not match the intended content hash"
            )

        recovery = RecoveryRecord(
            change_id=change.change_id,
            relative_path=proposal.path,
            operation="modify",
            original_bytes=original_bytes,
            resulting_hash=result_hash,
        )
        return WriteResult(
            change_id=change.change_id,
            relative_path=proposal.path,
            operation="modify",
            applied=True,
            resulting_hash=result_hash,
            recovery=recovery,
        )

    def _delete(self, change, proposal, authorization) -> WriteResult:
        target = self._resolve_existing(proposal.path)

        original_bytes = target.read_bytes()
        if content_hash(original_bytes) != authorization.expected_original_hash:
            raise WorkspaceWriteError(
                "File changed after authorization; refusing to delete"
            )

        target.unlink()

        recovery = RecoveryRecord(
            change_id=change.change_id,
            relative_path=proposal.path,
            operation="delete",
            original_bytes=original_bytes,
            resulting_hash=None,
        )
        return WriteResult(
            change_id=change.change_id,
            relative_path=proposal.path,
            operation="delete",
            applied=True,
            resulting_hash=None,
            recovery=recovery,
        )

    
    def rollback(self, recovery: RecoveryRecord) -> WriteResult:
        """Undo a write only if the current workspace state is still expected."""
        try:
            relative = Path(recovery.relative_path)

            if (
                relative.is_absolute()
                or not recovery.relative_path.strip()
                or ".." in relative.parts
            ):
                raise WorkspaceWriteError(
                    "Unsafe path in recovery record"
                )

            if recovery.operation not in {"create", "modify", "delete"}:
                raise WorkspaceWriteError(
                    f"Unsupported recovery operation: {recovery.operation}"
                )

            if recovery.operation == "delete":
                if recovery.original_bytes is None:
                    raise WorkspaceWriteError(
                        "Missing original bytes for deleted file"
                    )
                target = self._resolve_create_target(
                    recovery.relative_path
                )
                self._atomic_replace(target, recovery.original_bytes)
                restored_hash = content_hash(target.read_bytes())

                if restored_hash != content_hash(recovery.original_bytes):
                    raise WorkspaceWriteError(
                        "Restored file did not match its original content"
                    )

            else:
                target = self._resolve_existing(recovery.relative_path)

                if not target.is_file():
                    raise WorkspaceWriteError(
                        "Rollback target is not a regular file"
                    )

                current_hash = content_hash(target.read_bytes())
                if current_hash != recovery.resulting_hash:
                    raise WorkspaceWriteError(
                        "File changed after the operation; refusing rollback"
                    )

                if recovery.operation == "create":
                    target.unlink()
                    restored_hash = None
                    for relative_directory in reversed(recovery.created_directories):
                        directory = self.workspace.root / relative_directory
                        try:
                            resolved_directory = directory.resolve(strict=True)
                            resolved_directory.relative_to(self.workspace.root)
                            if resolved_directory.is_dir():
                                resolved_directory.rmdir()
                        except (OSError, RuntimeError, ValueError):
                            # Preserve directories that are no longer empty,
                            # missing, or no longer safely inside the workspace.
                            continue

                else:
                    if recovery.original_bytes is None:
                        raise WorkspaceWriteError(
                            "Missing original bytes for modified file"
                        )

                    self._atomic_replace(target, recovery.original_bytes)
                    restored_hash = content_hash(target.read_bytes())

                    if restored_hash != content_hash(recovery.original_bytes):
                        raise WorkspaceWriteError(
                            "Restored file did not match its original content"
                        )

            return WriteResult(
                change_id=recovery.change_id,
                relative_path=recovery.relative_path,
                operation=f"rollback_{recovery.operation}",
                applied=True,
                resulting_hash=restored_hash,
                recovery=None,
            )

        except WorkspaceWriteError:
            raise
        except (OSError, WorkspaceError) as exc:
            raise WorkspaceWriteError(
                f"Rollback failed: {exc}"
            ) from exc
