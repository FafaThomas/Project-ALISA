"""Change records and state comparison for controlled workspace edits."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum


class ChangeState(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    APPLIED = "applied"
    VERIFIED = "verified"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


def content_hash(content: bytes | None) -> str | None:
    """Return a SHA-256 digest, preserving None as a missing-file marker."""
    if content is None:
        return None
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class WorkspaceChange:
    """Audit record for one intended workspace file operation.

    Hashes represent raw file bytes, not decoded or normalized text.
    A None hash represents a file that did not exist at that snapshot.
    """

    change_id: str
    relative_path: str
    operation: str
    state: ChangeState = ChangeState.PROPOSED
    original_hash: str | None = None
    resulting_hash: str | None = None
    reason: str = ""
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.change_id.strip():
            raise ValueError("change_id must not be empty")
        if not self.relative_path.strip():
            raise ValueError("relative_path must not be empty")
        if self.operation not in {"create", "modify", "delete"}:
            raise ValueError("operation must be create, modify, or delete")
        if not self.reason.strip():
            raise ValueError("reason must not be empty")

        if self.operation == "create" and self.original_hash is not None:
            raise ValueError("create operation must start with a missing file")
        if self.operation == "delete" and self.resulting_hash is not None:
            raise ValueError("delete operation must result in a missing file")

    def transition(self, new_state: ChangeState, *, error: str | None = None):
        """Return a new record after a valid state transition."""
        allowed = {
            ChangeState.PROPOSED: {ChangeState.APPROVED, ChangeState.FAILED},
            ChangeState.APPROVED: {ChangeState.APPLIED, ChangeState.FAILED},
            ChangeState.APPLIED: {
                ChangeState.VERIFIED,
                ChangeState.FAILED,
                ChangeState.ROLLED_BACK,
            },
            ChangeState.VERIFIED: {ChangeState.ROLLED_BACK},
            ChangeState.FAILED: {ChangeState.ROLLED_BACK},
            ChangeState.ROLLED_BACK: set(),
        }

        if new_state not in allowed[self.state]:
            raise ValueError(
                f"Invalid change transition: {self.state.value} -> {new_state.value}"
            )

        from dataclasses import replace

        return replace(self, state=new_state, error=error)