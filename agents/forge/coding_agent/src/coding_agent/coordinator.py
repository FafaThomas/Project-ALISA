"""Coordinate file operations with the change lifecycle and durable journal."""

from __future__ import annotations

from dataclasses import dataclass

from coding_agent.change_journal import ChangeJournal, JournalError
from coding_agent.change_tracking import ChangeState, WorkspaceChange, content_hash
from coding_agent.models import ChangeType, FileChange
from coding_agent.workspace import Workspace, WorkspaceError
from coding_agent.workspace_writer import (
    RecoveryRecord,
    WorkspaceWriteError,
    WorkspaceWriter,
)


@dataclass(frozen=True)
class CoordinatedChange:
    """Current lifecycle record and optional recovery information."""

    change: WorkspaceChange
    recovery: RecoveryRecord | None = None


class ChangeCoordinator:
    """Coordinate approved writes, lifecycle states, and journal events."""

    def __init__(
        self,
        workspace: Workspace,
        journal: ChangeJournal | None = None,
    ) -> None:
        self.workspace = workspace
        self.writer = WorkspaceWriter(workspace)
        self.journal = journal

    def _record(
        self,
        change: WorkspaceChange,
        event: str,
        *,
        recovery_bytes: bytes | None = None,
        error: str | None = None,
    ) -> None:
        """Append an event when journaling is enabled."""
        if self.journal is None:
            return

        self.journal.append(
            change_id=change.change_id,
            event=event,
            relative_path=change.relative_path,
            operation=change.operation,
            state=change.state.value,
            original_hash=change.original_hash,
            resulting_hash=change.resulting_hash,
            recovery_bytes=recovery_bytes,
            error=error,
        )

    def _original_bytes(self, change: WorkspaceChange) -> bytes | None:
        """Capture original bytes before a modification or deletion."""
        if change.operation == ChangeType.CREATE.value:
            return None

        target = self.workspace.resolve_path(change.relative_path)
        original = target.read_bytes()

        if content_hash(original) != change.original_hash:
            raise WorkspaceWriteError(
                "File changed before journal intent was captured"
            )

        return original

    
    def apply(
        self,
        change: WorkspaceChange,
        proposal: FileChange,
    ) -> CoordinatedChange:
        """Journal intent, apply an approved change, and record its outcome."""
        if change.state != ChangeState.APPROVED:
            raise ValueError("Change must be approved before applying")

        try:
            original_bytes = self._original_bytes(change)
        except (OSError, WorkspaceError, WorkspaceWriteError) as exc:
            failed = change.transition(ChangeState.FAILED, error=str(exc))
            try:
                self._record(failed, "failed", error=str(exc))
            except JournalError:
                pass
            return CoordinatedChange(failed)

        # Do not modify the workspace unless intent is durably recorded.
        try:
            self._record(
                change,
                "apply_intent",
                recovery_bytes=original_bytes,
            )
        except JournalError as exc:
            failed = change.transition(
                ChangeState.FAILED,
                error=f"Journal rejected write intent: {exc}",
            )
            return CoordinatedChange(failed)

        try:
            result = self.writer.apply(change, proposal)
        except WorkspaceWriteError as exc:
            failed = change.transition(ChangeState.FAILED, error=str(exc))
            try:
                self._record(failed, "failed", error=str(exc))
            except JournalError:
                pass
            return CoordinatedChange(failed)

        applied = change.transition(ChangeState.APPLIED)

        try:
            self._record(
                applied,
                "applied",
                recovery_bytes=original_bytes,
            )
        except JournalError as exc:
            # The file has changed, but the outcome could not be persisted.
            try:
                self.writer.rollback(result.recovery)
            except WorkspaceWriteError as rollback_exc:
                failed = applied.transition(
                    ChangeState.FAILED,
                    error=(
                        f"Journal outcome failed: {exc}; "
                        f"automatic rollback failed: {rollback_exc}"
                    ),
                )
                return CoordinatedChange(failed, result.recovery)

            rolled_back = applied.transition(
                ChangeState.ROLLED_BACK,
                error=f"Journal outcome failed; change was rolled back: {exc}",
            )

            # Best effort: persist that the recovery happened.
            try:
                self._record(
                    rolled_back,
                    "rolled_back",
                    recovery_bytes=original_bytes,
                    error=rolled_back.error,
                )
            except JournalError:
                pass

            return CoordinatedChange(rolled_back)

        return CoordinatedChange(applied, result.recovery)

    
    def verify(
        self,
        coordinated: CoordinatedChange,
    ) -> CoordinatedChange:
        """Verify the resulting filesystem state and record the outcome."""
        change = coordinated.change
        if change.state != ChangeState.APPLIED:
            raise ValueError("Only applied changes can be verified")

        try:
            if change.operation == ChangeType.DELETE.value:
                candidate = self.workspace.root / change.relative_path
                resolved = candidate.resolve(strict=False)
                resolved.relative_to(self.workspace.root.resolve())
                actual_hash = None if not candidate.exists() else content_hash(
                    candidate.read_bytes()
                )
            else:
                target = self.workspace.resolve_path(change.relative_path)
                actual_hash = content_hash(target.read_bytes())

            if actual_hash != change.resulting_hash:
                raise WorkspaceWriteError(
                    "Verification failed: resulting content hash differs"
                )

        except (OSError, ValueError, WorkspaceError, WorkspaceWriteError) as exc:
            failed = change.transition(
                ChangeState.FAILED,
                error=f"Verification failed: {exc}",
            )
            try:
                self._record(
                    failed,
                    "failed",
                    recovery_bytes=(
                        coordinated.recovery.original_bytes
                        if coordinated.recovery is not None
                        else None
                    ),
                    error=str(exc),
                )
            except JournalError:
                pass
            return CoordinatedChange(failed, coordinated.recovery)

        verified = change.transition(ChangeState.VERIFIED)
        try:
            self._record(
                verified,
                "verified",
                recovery_bytes=(
                    coordinated.recovery.original_bytes
                    if coordinated.recovery is not None
                    else None
                ),
            )
        except JournalError as exc:
            failed = verified.transition(
                ChangeState.FAILED,
                error=f"Verification passed, but journal recording failed: {exc}",
            )
            return CoordinatedChange(failed, coordinated.recovery)

        return CoordinatedChange(verified, coordinated.recovery)

    def rollback(
        self,
        coordinated: CoordinatedChange,
    ) -> CoordinatedChange:
        """Journal and perform rollback, recording the resulting state."""
        change = coordinated.change
        if change.state not in {
            ChangeState.APPLIED,
            ChangeState.VERIFIED,
            ChangeState.FAILED,
        }:
            raise ValueError(
                "Only applied, verified, or failed changes can be rolled back"
            )

        recovery = coordinated.recovery
        if recovery is None:
            failed = change.transition(
                ChangeState.FAILED,
                error="Rollback failed: recovery information is missing",
            )
            try:
                self._record(failed, "failed", error=failed.error)
            except JournalError:
                pass
            return CoordinatedChange(failed)

        try:
            self._record(
                change,
                "rollback_intent",
                recovery_bytes=recovery.original_bytes,
            )
        except JournalError as exc:
            failed = change.transition(
                ChangeState.FAILED,
                error=f"Journal rejected rollback intent: {exc}",
            )
            return CoordinatedChange(failed, recovery)

        try:
            self.writer.rollback(recovery)
        except WorkspaceWriteError as exc:
            failed = change.transition(ChangeState.FAILED, error=str(exc))
            try:
                self._record(
                    failed,
                    "failed",
                    recovery_bytes=recovery.original_bytes,
                    error=str(exc),
                )
            except JournalError:
                pass
            return CoordinatedChange(failed, recovery)

        rolled_back = change.transition(ChangeState.ROLLED_BACK)
        try:
            self._record(
                rolled_back,
                "rolled_back",
                recovery_bytes=recovery.original_bytes,
            )
        except JournalError as exc:
            # The filesystem rollback succeeded; don't claim it failed.
            rolled_back = rolled_back.transition(
                ChangeState.ROLLED_BACK,
                error=f"Rollback succeeded, but journal outcome failed: {exc}",
            )

        return CoordinatedChange(rolled_back)