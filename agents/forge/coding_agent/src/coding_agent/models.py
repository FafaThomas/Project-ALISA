"""Core data contracts for the ALISA Coding Agent.

These models describe tasks, proposed changes, implementation plans,
and observed execution outcomes. They do not perform file operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Sequence


class ChangeType(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class TaskStatus(str, Enum):
    PENDING = "pending"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    IMPLEMENTING = "implementing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CodingTask:
    """A software change requested by a user."""

    task_id: str
    description: str
    workspace: str
    constraints: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.description.strip():
            raise ValueError("description must not be empty")
        if not self.workspace.strip():
            raise ValueError("workspace must not be empty")


@dataclass(frozen=True)
class FileChange:
    """A proposed operation on a workspace-relative file path."""

    path: str
    change_type: ChangeType
    reason: str
    content: str | None = None

    def __post_init__(self) -> None:
        normalized = self.path.replace("\\", "/")
        candidate = PurePosixPath(normalized)

        if not normalized.strip():
            raise ValueError("path must not be empty")
        if candidate.is_absolute() or candidate.anchor:
            raise ValueError("path must be workspace-relative")
        if any(part == ".." for part in candidate.parts):
            raise ValueError("path must not traverse outside the workspace")
        if not self.reason.strip():
            raise ValueError("reason must not be empty")

        if self.change_type == ChangeType.DELETE and self.content is not None:
            raise ValueError("delete operations must not include file content")
        if self.change_type in (ChangeType.CREATE, ChangeType.MODIFY):
            if self.content is None:
                raise ValueError(
                    f"{self.change_type.value} operations require content"
                )


@dataclass(frozen=True)
class ImplementationPlan:
    """An ordered proposal; creating a plan does not authorize execution."""

    task_id: str
    summary: str
    changes: tuple[FileChange, ...]
    verification_commands: tuple[tuple[str, ...], ...] = ()
    requires_approval: bool = True
    risks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.summary.strip():
            raise ValueError("summary must not be empty")

        paths = [change.path.replace("\\", "/") for change in self.changes]
        if len(paths) != len(set(paths)):
            raise ValueError("a plan must not change the same path more than once")

        for command in self.verification_commands:
            if not command or any(not part for part in command):
                raise ValueError("verification commands must not be empty")


@dataclass(frozen=True)
class CommandResult:
    """Observed result of one command invocation."""

    command: tuple[str, ...]
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class ExecutionResult:
    """Observed outcome of applying a plan and running verification."""

    task_id: str
    status: TaskStatus
    changed_files: tuple[str, ...] = ()
    command_results: tuple[CommandResult, ...] = ()
    errors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def verification_passed(self) -> bool:
        """True only when at least one verification command ran and all passed."""
        return bool(self.command_results) and all(
            result.succeeded for result in self.command_results
        )

    @property
    def succeeded(self) -> bool:
        """Task completion requires an explicit completed status and passing tests."""
        return (
            self.status == TaskStatus.COMPLETED
            and self.verification_passed
            and not self.errors
        )
