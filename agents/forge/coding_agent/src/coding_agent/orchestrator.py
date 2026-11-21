
"""Coordinate the complete coding task lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from coding_agent.change_tracking import (
    ChangeState,
    WorkspaceChange,
    content_hash,
)
from coding_agent.coordinator import ChangeCoordinator, CoordinatedChange
from coding_agent.models import (
    CodingTask,
    ExecutionResult,
    ImplementationPlan,
    TaskStatus,
)
from coding_agent.planner import CodingPlanner
from coding_agent.command_runner import CommandRunner
from coding_agent.verification import (
    VerificationEngine,
    VerificationReport,
)


ApprovalHandler = Callable[[ImplementationPlan], bool]


class OrchestrationError(RuntimeError):
    """Raised when the task lifecycle cannot be coordinated safely."""


class TaskOrchestrator:
    """Run approved implementation plans as transactional task executions."""

    def __init__(
        self,
        planner: CodingPlanner,
        coordinator: ChangeCoordinator,
        command_runner: CommandRunner,
        verification_engine: VerificationEngine,
    ) -> None:
        self.planner = planner
        self.coordinator = coordinator
        self.command_runner = command_runner
        self.verification_engine = verification_engine

        coordinator_root = coordinator.workspace.root.resolve()
        runner_root = command_runner.workspace.root.resolve()

        if coordinator_root != runner_root:
            raise ValueError(
                "ChangeCoordinator and CommandRunner must use "
                "the same workspace"
            )

    def _validate_task_workspace(self, task: CodingTask) -> None:
        """Ensure the task targets the orchestrator's authorized workspace."""

        requested_root = Path(task.workspace).expanduser().resolve(strict=True)
        authorized_root = self.coordinator.workspace.root.resolve()

        if requested_root != authorized_root:
            raise OrchestrationError(
                "Task workspace does not match the authorized workspace"
            )

    def _build_workspace_change(
        self,
        proposal,
        *,
        task_id: str,
        index: int,
    ) -> WorkspaceChange:
        """Capture the original state and construct a proposed change record."""

        workspace = self.coordinator.workspace

        if proposal.change_type.value == "create":
            workspace.resolve_create_path(proposal.path)
            original_bytes = None
        else:
            target = workspace.resolve_path(proposal.path)
            original_bytes = target.read_bytes()

        intended_bytes = (
            proposal.content.encode("utf-8")
            if proposal.content is not None
            else None
        )

        change = WorkspaceChange(
            change_id=f"{task_id}-{index}-{uuid4().hex}",
            relative_path=proposal.path,
            operation=proposal.change_type.value,
            state=ChangeState.PROPOSED,
            original_hash=content_hash(original_bytes),
            resulting_hash=content_hash(intended_bytes),
            reason=proposal.reason,
        )

        return change.transition(ChangeState.APPROVED)

    @staticmethod
    def _rollback_changes(
        coordinator: ChangeCoordinator,
        coordinated_changes: list[CoordinatedChange],
    ) -> tuple[list[CoordinatedChange], list[str]]:
        """Roll back recoverable changes in reverse application order."""

        updated = list(coordinated_changes)
        errors: list[str] = []

        for index in range(len(updated) - 1, -1, -1):
            coordinated = updated[index]
            state = coordinated.change.state

            if state not in {
                ChangeState.APPLIED,
                ChangeState.VERIFIED,
                ChangeState.FAILED,
            }:
                continue

            if coordinated.recovery is None:
                errors.append(
                    f"{coordinated.change.relative_path}: "
                    "no recovery record is available"
                )
                continue

            try:
                rolled_back = coordinator.rollback(coordinated)
            except Exception as exc:
                errors.append(
                    f"{coordinated.change.relative_path}: "
                    f"rollback raised an exception: {exc}"
                )
                continue

            updated[index] = rolled_back

            if rolled_back.change.state != ChangeState.ROLLED_BACK:
                errors.append(
                    f"{coordinated.change.relative_path}: "
                    f"rollback did not complete: "
                    f"{rolled_back.change.error or 'unknown error'}"
                )

        return updated, errors

    @staticmethod
    def _changed_files(
        coordinated_changes: list[CoordinatedChange],
    ) -> tuple[str, ...]:
        """Return paths for changes that reached or may have reached disk."""

        paths = []

        for coordinated in coordinated_changes:
            if coordinated.recovery is not None:
                paths.append(coordinated.change.relative_path)

        return tuple(dict.fromkeys(paths))

    def run(
        self,
        task: CodingTask,
        *,
        approval_handler: ApprovalHandler,
    ) -> ExecutionResult:
        """Plan, obtain approval, apply, verify, and finalize one task."""

        errors: list[str] = []
        notes: list[str] = []
        coordinated_changes: list[CoordinatedChange] = []
        command_results = []

        try:
            self._validate_task_workspace(task)
        except (OSError, OrchestrationError, ValueError) as exc:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.BLOCKED,
                errors=(str(exc),),
            )

        # 1. Generate the implementation plan.
        try:
            plan = self.planner.plan(task)
        except Exception as exc:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                errors=(f"Planning failed: {exc}",),
            )

        if not isinstance(plan, ImplementationPlan):
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                errors=("Planner returned an invalid plan object.",),
            )

        if plan.task_id != task.task_id:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                errors=("Plan task ID does not match the requested task.",),
            )

        if not plan.changes:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                errors=("Implementation plan contains no file changes.",),
            )

        if not plan.verification_commands:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                errors=("Implementation plan contains no verification commands.",),
            )

        # 2. Capture file baselines before requesting approval.
        # This allows the coordinator to detect changes made while
        # the user is reviewing the plan.
        approved_changes = []

        try:
            for index, proposal in enumerate(plan.changes):
                approved_changes.append(
                    (
                        self._build_workspace_change(
                            proposal,
                            task_id=task.task_id,
                            index=index,
                        ),
                        proposal,
                    )
                )
        except Exception as exc:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                errors=(f"Could not prepare implementation changes: {exc}",),
            )

        # 3. Require explicit human approval, regardless of the LLM flag.
        try:
            approved = approval_handler(plan)
        except Exception as exc:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.BLOCKED,
                errors=(f"Approval handler failed: {exc}",),
            )

        if approved is not True:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.BLOCKED,
                notes=("Implementation plan was not approved. No changes applied.",),
            )

        # 4. Apply changes transactionally.
        for change, proposal in approved_changes:
            try:
                coordinated = self.coordinator.apply(change, proposal)
            except Exception as exc:
                errors.append(
                    f"Applying {proposal.path} raised an exception: {exc}"
                )
                coordinated_changes, rollback_errors = self._rollback_changes(
                    self.coordinator,
                    coordinated_changes,
                )
                errors.extend(rollback_errors)

                return ExecutionResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    changed_files=self._changed_files(coordinated_changes),
                    errors=tuple(errors),
                    notes=tuple(notes),
                )

            if coordinated.change.state == ChangeState.APPLIED:
                coordinated_changes.append(coordinated)
                continue

            # An apply may have changed the file but failed to persist its
            # journal outcome. Preserve its recovery record if one exists.
            if coordinated.recovery is not None:
                coordinated_changes.append(coordinated)

            errors.append(
                f"Applying {proposal.path} failed: "
                f"{coordinated.change.error or coordinated.change.state.value}"
            )

            coordinated_changes, rollback_errors = self._rollback_changes(
                self.coordinator,
                coordinated_changes,
            )
            errors.extend(rollback_errors)

            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                changed_files=self._changed_files(coordinated_changes),
                errors=tuple(errors),
                notes=tuple(notes),
            )

        # 5. Verify file integrity before running project-level checks.
        for index, coordinated in enumerate(coordinated_changes):
            try:
                verified = self.coordinator.verify(coordinated)
            except Exception as exc:
                errors.append(
                    f"File integrity verification raised an exception: {exc}"
                )
                coordinated_changes, rollback_errors = self._rollback_changes(
                    self.coordinator,
                    coordinated_changes,
                )
                errors.extend(rollback_errors)

                return ExecutionResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    changed_files=self._changed_files(coordinated_changes),
                    errors=tuple(errors),
                    notes=tuple(notes),
                )

            coordinated_changes[index] = verified

            if verified.change.state != ChangeState.VERIFIED:
                errors.append(
                    f"File integrity verification failed for "
                    f"{verified.change.relative_path}: "
                    f"{verified.change.error or 'unknown error'}"
                )

                coordinated_changes, rollback_errors = self._rollback_changes(
                    self.coordinator,
                    coordinated_changes,
                )
                errors.extend(rollback_errors)

                return ExecutionResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    changed_files=self._changed_files(coordinated_changes),
                    errors=tuple(errors),
                    notes=tuple(notes),
                )

        # 6. Execute the exact commands that were included in the approved plan.
        for command in plan.verification_commands:
            try:
                result = self.command_runner.run(
                    command,
                    approved_commands=plan.verification_commands,
                )
            except Exception as exc:
                from coding_agent.models import CommandResult

                result = CommandResult(
                    command=tuple(command),
                    exit_code=None,
                    stderr=f"Command runner failed: {exc}",
                )
                command_results.append(result)
                errors.append(
                    f"Could not execute verification command {command}: {exc}"
                )
                break

            command_results.append(result)

        # 7. Evaluate project-level verification results.
        try:
            report: VerificationReport = self.verification_engine.evaluate(
                plan.verification_commands,
                command_results,
            )
        except Exception as exc:
            errors.append(f"Verification evaluation failed: {exc}")
            report = None

        if report is not None:
            notes.append(report.summary)

        if report is None or not report.passed:
            if report is not None:
                errors.extend(issue.message for issue in report.issues)

            coordinated_changes, rollback_errors = self._rollback_changes(
                self.coordinator,
                coordinated_changes,
            )
            errors.extend(rollback_errors)

            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                changed_files=self._changed_files(coordinated_changes),
                command_results=tuple(command_results),
                errors=tuple(errors),
                notes=tuple(notes),
            )

        # 8. Only now is the task considered complete.
        return ExecutionResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            changed_files=self._changed_files(coordinated_changes),
            command_results=tuple(command_results),
            notes=tuple(notes),
        )
