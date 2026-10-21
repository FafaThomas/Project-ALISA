
"""Read-only assessment of interrupted workspace changes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from coding_agent.change_journal import ChangeJournal, JournalEntry
from coding_agent.change_tracking import content_hash
from coding_agent.workspace import Workspace, WorkspaceError

from coding_agent.workspace_writer import (
    RecoveryRecord,
    WorkspaceWriteError,
    WorkspaceWriter,
)


FINAL_EVENTS = {"verified", "failed", "rolled_back"}
ACTIVE_EVENTS = {"apply_intent", "applied", "rollback_intent"}


class RecoveryStatus(str, Enum):
    ORIGINAL = "original"
    APPLIED = "applied"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class RecoveryCandidate:
    """Assessment of a change that may need recovery."""

    change_id: str
    relative_path: str
    operation: str
    last_event: str
    state: str
    original_hash: str | None
    resulting_hash: str | None
    recovery_bytes: bytes | None
    assessment: str
    status: RecoveryStatus
    current_hash: str | None


class RecoveryAction(str, Enum):
    """Recommended next action for an interrupted change."""

    FINALIZE_APPLY = "finalize_apply"
    ABORT_UNAPPLIED = "abort_unapplied"
    VERIFY = "verify"
    RESTORE_ORIGINAL = "restore_original"
    FINALIZE_ROLLBACK = "finalize_rollback"
    MANUAL_INTERVENTION = "manual_intervention"


@dataclass(frozen=True)
class RecoveryDecision:
    """A recovery recommendation; it does not modify workspace files."""

    change_id: str
    action: RecoveryAction
    reason: str



def _inspect_current_hash(
    workspace: Workspace,
    relative_path: str,
) -> tuple[bool, str | None]:
    """Return (safe_to_inspect, current_hash); None hash means absent."""
    raw = relative_path.replace("\\", "/")
    supplied = Path(raw)

    if (
        not raw.strip()
        or supplied.is_absolute()
        or supplied.drive
        or any(part == ".." for part in raw.split("/"))
    ):
        return False, None

    candidate = workspace.root / supplied

    try:
        if candidate.exists() or candidate.is_symlink():
            resolved = workspace.resolve_path(relative_path)

            if not resolved.is_file():
                return False, None

            if resolved.stat().st_size > workspace.max_file_bytes:
                return False, None

            data = resolved.read_bytes()
            if len(data) > workspace.max_file_bytes:
                return False, None

            return True, content_hash(data)

        # For a missing target, ensure its nearest existing ancestor
        # resolves inside the workspace. This also handles missing files
        # beneath an existing nested directory.
        ancestor = candidate.parent
        while not ancestor.exists() and not ancestor.is_symlink():
            parent = ancestor.parent
            if parent == ancestor:
                return False, None
            ancestor = parent

        resolved_ancestor = ancestor.resolve(strict=True)
        resolved_ancestor.relative_to(workspace.root)

        if ancestor.is_symlink():
            # A dangling symlink or symlinked parent is not a safe
            # assumption for recovery, even if its target is missing.
            return False, None

        return True, None

    except (OSError, RuntimeError, ValueError, WorkspaceError):
        return False, None


def _classify(
    current_hash: str | None,
    original_hash: str | None,
    resulting_hash: str | None,
) -> RecoveryStatus:
    """Classify current contents against the recorded before/after hashes."""
    matches_original = current_hash == original_hash
    matches_result = current_hash == resulting_hash

    if matches_original and not matches_result:
        return RecoveryStatus.ORIGINAL

    if matches_result and not matches_original:
        return RecoveryStatus.APPLIED

    # If both states have the same hash, there is no way to distinguish
    # the states from content alone. Treat that as a conflict.
    return RecoveryStatus.CONFLICT


def assess_recovery(
    journal: ChangeJournal,
    workspace: Workspace,
) -> list[RecoveryCandidate]:
    """Assess interrupted changes against current files without modifying them."""
    entries = journal.read_all()
    latest_by_change: dict[str, JournalEntry] = {}

    for entry in entries:
        latest_by_change[entry.change_id] = entry

    candidates: list[RecoveryCandidate] = []

    for change_id, entry in latest_by_change.items():
        if entry.event in FINAL_EVENTS or entry.event not in ACTIVE_EVENTS:
            continue

        safe, current_hash = _inspect_current_hash(
            workspace,
            entry.relative_path,
        )

        if not safe:
            status = RecoveryStatus.CONFLICT
            assessment = (
                "Workspace path could not be inspected safely; "
                "manual investigation is required."
            )
        else:
            status = _classify(
                current_hash,
                entry.original_hash,
                entry.resulting_hash,
            )

            if status == RecoveryStatus.ORIGINAL:
                assessment = (
                    "Current contents match the original state; "
                    "the operation may not have taken effect."
                )
            elif status == RecoveryStatus.APPLIED:
                assessment = (
                    "Current contents match the expected result; "
                    "the operation may have completed."
                )
            else:
                assessment = (
                    "Current contents match neither a uniquely identifiable "
                    "original nor resulting state; manual investigation is required."
                )

        candidates.append(
            RecoveryCandidate(
                change_id=change_id,
                relative_path=entry.relative_path,
                operation=entry.operation,
                last_event=entry.event,
                state=entry.state,
                original_hash=entry.original_hash,
                resulting_hash=entry.resulting_hash,
                recovery_bytes=entry.recovery_bytes(),
                assessment=assessment,
                status=status,
                current_hash=current_hash,
            )
        )

    return candidates


def decide_recovery(candidate: RecoveryCandidate) -> RecoveryDecision:
    """Recommend a recovery action without modifying files."""
    def decision(action: RecoveryAction, reason: str) -> RecoveryDecision:
        return RecoveryDecision(candidate.change_id, action, reason)

    if candidate.status == RecoveryStatus.CONFLICT:
        return decision(
            RecoveryAction.MANUAL_INTERVENTION,
            "Current contents are unexpected or cannot be inspected safely.",
        )

    if candidate.last_event == "apply_intent":
        if candidate.status == RecoveryStatus.ORIGINAL:
            return decision(
                RecoveryAction.ABORT_UNAPPLIED,
                "The original state remains; do not replay the interrupted operation automatically.",
            )

        return decision(
            RecoveryAction.FINALIZE_APPLY,
            "Contents match the expected result; reconcile the missing applied event.",
        )

    if candidate.last_event == "applied":
        if candidate.status == RecoveryStatus.APPLIED:
            return decision(
                RecoveryAction.VERIFY,
                "The applied contents remain; continue with normal verification.",
            )

        return decision(
            RecoveryAction.MANUAL_INTERVENTION,
            "The journal records an applied change, but the original contents are present.",
        )

    if candidate.last_event == "rollback_intent":
        if candidate.status == RecoveryStatus.ORIGINAL:
            return decision(
                RecoveryAction.FINALIZE_ROLLBACK,
                "Contents match the original state; reconcile the interrupted rollback.",
            )

        if candidate.operation == "create" or candidate.recovery_bytes is not None:
            return decision(
                RecoveryAction.RESTORE_ORIGINAL,
                "The change appears applied; original-state recovery data is available or the created file can be removed.",
            )

        return decision(
            RecoveryAction.MANUAL_INTERVENTION,
            "The change appears applied, but the original contents needed for rollback are unavailable.",
        )

    return decision(
        RecoveryAction.MANUAL_INTERVENTION,
        "The journal event has no defined recovery policy.",
    )


@dataclass(frozen=True)
class RecoveryExecutionResult:
    """Outcome of attempting a recovery decision."""

    change_id: str
    action: RecoveryAction
    executed: bool
    journaled: bool
    message: str


def execute_recovery(
    candidate: RecoveryCandidate,
    decision: RecoveryDecision,
    journal: ChangeJournal,
    workspace: Workspace,
) -> RecoveryExecutionResult:
    """Execute a safe recovery decision after rechecking workspace state.

    This function does not automatically retry the original file operation.
    """
    def result(
        executed: bool,
        journaled: bool,
        message: str,
    ) -> RecoveryExecutionResult:
        return RecoveryExecutionResult(
            change_id=candidate.change_id,
            action=decision.action,
            executed=executed,
            journaled=journaled,
            message=message,
        )

    if decision.change_id != candidate.change_id:
        return result(False, False, "Decision and candidate IDs do not match.")

    if decision.action == RecoveryAction.MANUAL_INTERVENTION:
        return result(False, False, "Manual intervention is required.")

    if decision.action == RecoveryAction.VERIFY:
        return result(
            False,
            False,
            "Run the normal change verification workflow before finalizing.",
        )

    # Recheck the target immediately before deciding whether to act.
    safe, current_hash = _inspect_current_hash(
        workspace,
        candidate.relative_path,
    )
    if not safe:
        return result(False, False, "Workspace path is no longer safe to inspect.")

    if current_hash != candidate.current_hash:
        return result(
            False,
            False,
            "Workspace contents changed since assessment; reassess recovery.",
        )

    current_status = _classify(
        current_hash,
        candidate.original_hash,
        candidate.resulting_hash,
    )
    if current_status != candidate.status:
        return result(
            False,
            False,
            "Workspace classification changed; reassess recovery.",
        )

    if decision.action == RecoveryAction.ABORT_UNAPPLIED:
        if (
            candidate.last_event != "apply_intent"
            or current_status != RecoveryStatus.ORIGINAL
        ):
            return result(
                False,
                False,
                "Preconditions for aborting the unapplied operation are not met.",
            )

        try:
            journal.append(
                change_id=candidate.change_id,
                event="failed",
                relative_path=candidate.relative_path,
                operation=candidate.operation,
                state="failed",
                original_hash=candidate.original_hash,
                resulting_hash=candidate.resulting_hash,
                recovery_bytes=candidate.recovery_bytes,
                error="Recovery determined that the operation had not taken effect.",
            )
        except Exception as exc:
            return result(
                False,
                False,
                f"Operation was not changed, but abort could not be journaled: {exc}",
            )

        return result(True, True, "Unapplied operation marked as failed.")

    if decision.action == RecoveryAction.FINALIZE_APPLY:
        if (
            candidate.last_event != "apply_intent"
            or current_status != RecoveryStatus.APPLIED
        ):
            return result(
                False,
                False,
                "Preconditions for finalizing the applied change are not met.",
            )

        try:
            journal.append(
                change_id=candidate.change_id,
                event="applied",
                relative_path=candidate.relative_path,
                operation=candidate.operation,
                state="applied",
                original_hash=candidate.original_hash,
                resulting_hash=candidate.resulting_hash,
                recovery_bytes=candidate.recovery_bytes,
                error="Applied outcome reconciled during recovery.",
            )
        except Exception as exc:
            return result(
                False,
                False,
                f"File contents were not changed, but reconciliation failed: {exc}",
            )

        return result(True, True, "Applied outcome reconciled in the journal.")

    if decision.action == RecoveryAction.FINALIZE_ROLLBACK:
        if (
            candidate.last_event != "rollback_intent"
            or current_status != RecoveryStatus.ORIGINAL
        ):
            return result(
                False,
                False,
                "Preconditions for finalizing rollback are not met.",
            )

        try:
            journal.append(
                change_id=candidate.change_id,
                event="rolled_back",
                relative_path=candidate.relative_path,
                operation=candidate.operation,
                state="rolled_back",
                original_hash=candidate.original_hash,
                resulting_hash=candidate.resulting_hash,
                recovery_bytes=candidate.recovery_bytes,
                error="Rollback outcome reconciled during recovery.",
            )
        except Exception as exc:
            return result(
                False,
                False,
                f"Original contents remain, but rollback reconciliation failed: {exc}",
            )

        return result(True, True, "Rollback outcome reconciled in the journal.")

    if decision.action == RecoveryAction.RESTORE_ORIGINAL:
        if (
            candidate.last_event != "rollback_intent"
            or current_status != RecoveryStatus.APPLIED
        ):
            return result(
                False,
                False,
                "Preconditions for restoring the original state are not met.",
            )

        if candidate.operation != "create" and candidate.recovery_bytes is None:
            return result(
                False,
                False,
                "Original recovery bytes are missing; nothing was changed.",
            )

        writer = WorkspaceWriter(workspace)
        recovery = RecoveryRecord(
            change_id=candidate.change_id,
            relative_path=candidate.relative_path,
            operation=candidate.operation,
            original_bytes=candidate.recovery_bytes,
            resulting_hash=candidate.resulting_hash,
        )

        try:
            writer.rollback(recovery)
        except WorkspaceWriteError as exc:
            return result(False, False, f"Rollback refused or failed: {exc}")

        # The file has now been restored. Report journal failure honestly;
        # do not claim that the complete recovery was durably recorded.
        try:
            journal.append(
                change_id=candidate.change_id,
                event="rolled_back",
                relative_path=candidate.relative_path,
                operation=candidate.operation,
                state="rolled_back",
                original_hash=candidate.original_hash,
                resulting_hash=candidate.resulting_hash,
                recovery_bytes=candidate.recovery_bytes,
                error="Interrupted rollback completed during recovery.",
            )
        except Exception as exc:
            return result(
                True,
                False,
                f"Original state restored, but outcome journaling failed: {exc}",
            )

        return result(True, True, "Original state restored and journaled.")

    return result(False, False, "Unsupported recovery action.")


