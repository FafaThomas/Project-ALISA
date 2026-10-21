"""Pre-write authorization and stale-file checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coding_agent.change_tracking import (
    ChangeState,
    WorkspaceChange,
    content_hash,
)
from coding_agent.models import ChangeType, FileChange
from coding_agent.workspace import Workspace, WorkspaceError


class WriteGuardError(Exception):
    """Base exception for rejected write operations."""


class ApprovalRequiredError(WriteGuardError):
    """Raised when a change has not been approved."""


class StaleFileError(WriteGuardError):
    """Raised when the current file differs from the expected original."""


@dataclass(frozen=True)
class WriteAuthorization:
    """A validated, one-time-use authorization for a proposed change."""

    change_id: str
    relative_path: str
    operation: str
    expected_original_hash: str | None
    intended_result_hash: str | None


def validate_approved_change(
    workspace: Workspace,
    change: WorkspaceChange,
    proposal: FileChange,
) -> WriteAuthorization:
    """Validate approval, path agreement, and original file state.

    This function performs no writes. Call it immediately before applying a
    change, and revalidate within the eventual write operation.
    """
    if change.state != ChangeState.APPROVED:
        raise ApprovalRequiredError(
            f"Change {change.change_id!r} is not approved"
        )

    expected_operation = proposal.change_type.value
    if change.operation != expected_operation:
        raise WriteGuardError("Change record and proposal operations differ")

    if Path(change.relative_path).as_posix() != Path(proposal.path).as_posix():
        raise WriteGuardError("Change record and proposal paths differ")

    try:
        target = workspace.resolve_path(proposal.path)
    except WorkspaceError:
        if proposal.change_type != ChangeType.CREATE:
            raise StaleFileError(
                "Expected existing file is missing or cannot be resolved"
            )
        target = workspace.root / proposal.path

    if proposal.change_type == ChangeType.CREATE:
        if target.exists():
            raise StaleFileError("Cannot create file: target already exists")
        actual_hash = None
    else:
        try:
            actual_bytes = target.read_bytes()
        except OSError as exc:
            raise StaleFileError(
                f"Could not inspect current file state: {exc}"
            ) from exc
        actual_hash = content_hash(actual_bytes)

    if actual_hash != change.original_hash:
        raise StaleFileError(
            "Current file state differs from the recorded original hash"
        )

    intended_hash = (
        content_hash(proposal.content.encode("utf-8"))
        if proposal.content is not None
        else None
    )

    if proposal.change_type == ChangeType.DELETE:
        if change.resulting_hash is not None:
            raise WriteGuardError("Delete record must have no resulting hash")
    elif change.resulting_hash != intended_hash:
        raise WriteGuardError(
            "Proposed content does not match the recorded resulting hash"
        )

    return WriteAuthorization(
        change_id=change.change_id,
        relative_path=proposal.path,
        operation=proposal.change_type.value,
        expected_original_hash=actual_hash,
        intended_result_hash=intended_hash,
    )