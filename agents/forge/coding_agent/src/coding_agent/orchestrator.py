
"""Coordinate the complete coding task lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from dataclasses import replace
from pathlib import Path
from inspect import signature
from uuid import uuid4

from coding_agent.context_engine import ContextEngine
from coding_agent.change_tracking import (
    ChangeState,
    WorkspaceChange,
    content_hash,
)
from coding_agent.coordinator import ChangeCoordinator, CoordinatedChange
from coding_agent.models import (
    AttemptRecord,
    ChangeType,
    CodingTask,
    ExecutionResult,
    ImplementationPlan,
    TaskStatus,
)
from coding_agent.repair_intelligence import FailureDiagnoser, RepairStrategyGenerator
from coding_agent.retry import (
    FailureClassifier,
    RetryPolicy,
    RetryRequest,
    build_recovery_context,
)
from coding_agent.task_journal import TaskEventJournal, TaskJournalError
from coding_agent.planner import CodingPlanner
from coding_agent.command_runner import CommandRunner
from coding_agent.verification_planner import VerificationPlanner
from coding_agent.workspace import WorkspacePathError
from coding_agent.verification import (
    VerificationEngine,
    VerificationReport,
)
from coding_agent.engineering_spec import EngineeringSpecification
from coding_agent.specification_verification import SpecificationVerificationEngine
from coding_agent.specification_check_planner import SpecificationCheckPlanner
from coding_agent.espec_generator import EngineeringSpecificationGenerator
from coding_agent.plan_specification import PlanSpecificationValidator


ApprovalHandler = Callable[[ImplementationPlan], bool]
ManualVerificationHandler = Callable[[tuple], Mapping[str, object]]


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
        context_engine: ContextEngine | None = None,
        retry_policy: RetryPolicy | None = None,
        task_journal: TaskEventJournal | None = None,
        max_planning_corrections: int = 2,
    ) -> None:
        self.planner = planner
        self.coordinator = coordinator
        self.command_runner = command_runner
        self.verification_engine = verification_engine
        self.retry_policy = retry_policy or RetryPolicy()
        self.failure_diagnoser = FailureDiagnoser()
        self.repair_strategy_generator = RepairStrategyGenerator()
        if max_planning_corrections < 0 or max_planning_corrections > 5:
            raise ValueError("max_planning_corrections must be between 0 and 5")
        self.max_planning_corrections = max_planning_corrections

        coordinator_root = coordinator.workspace.root.resolve()
        self.task_journal = task_journal or TaskEventJournal(
            coordinator_root / ".coding-agent" / "tasks.jsonl"
        )
        runner_root = command_runner.workspace.root.resolve()

        if coordinator_root != runner_root:
            raise ValueError(
                "ChangeCoordinator and CommandRunner must use "
                "the same workspace"
            )

        self.context_engine = context_engine or ContextEngine(
            coordinator.workspace
        )

        if self.context_engine.workspace.root != coordinator_root:
            raise ValueError(
                "ContextEngine and ChangeCoordinator must use "
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

    def _normalize_proposal_operation(self, proposal):
        """Align create/modify metadata with the authorized workspace inventory.

        This is a deterministic path-state correction, not a reason to ask the
        LLM to regenerate file contents. Replanning an otherwise valid plan
        can discard semantically correct content while fixing only an operation
        label. Delete operations are never rewritten.
        """
        workspace = self.coordinator.workspace

        if proposal.change_type == ChangeType.DELETE:
            try:
                target = workspace.resolve_path(proposal.path)
            except WorkspacePathError as exc:
                raise OrchestrationError(
                    f"Cannot safely delete '{proposal.path}': target is unavailable."
                ) from exc
            if not target.is_file():
                raise OrchestrationError(
                    f"Cannot delete '{proposal.path}': target is not a regular file."
                )
            return proposal, None

        try:
            existing_target = workspace.resolve_path(proposal.path)
        except WorkspacePathError:
            # Validate that the missing target is a safe, creatable workspace
            # path. This also rejects traversal and unsafe symlink ancestors.
            try:
                workspace.resolve_create_path(proposal.path)
            except WorkspacePathError as exc:
                raise OrchestrationError(
                    f"Cannot safely classify '{proposal.path}' against the workspace inventory."
                ) from exc
            desired_type = ChangeType.CREATE
        else:
            if not existing_target.is_file():
                raise OrchestrationError(
                    f"Cannot update '{proposal.path}': target is not a regular file."
                )
            desired_type = ChangeType.MODIFY

        if proposal.change_type == desired_type:
            return proposal, None

        normalized = replace(proposal, change_type=desired_type)
        note = (
            f"Normalized operation for '{proposal.path}' from "
            f"{proposal.change_type.value} to {desired_type.value} "
            "using the current workspace inventory; file content was preserved."
        )
        return normalized, note

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
            try:
                workspace.resolve_create_path(proposal.path)
            except Exception as exc:
                raise OrchestrationError(
                    f"Cannot create '{proposal.path}': target must not already exist. "
                    "If this file is part of the existing project, propose modify instead."
                ) from exc
            original_bytes = None
        else:
            try:
                target = workspace.resolve_path(proposal.path)
            except Exception as exc:
                raise OrchestrationError(
                    f"Cannot {proposal.change_type.value} '{proposal.path}': "
                    "the target must already exist inside the workspace."
                ) from exc
            if not target.is_file():
                raise OrchestrationError(
                    f"Cannot {proposal.change_type.value} '{proposal.path}': target is not a file."
                )
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

    def _historical_context(
        self, task_id: str | None = None, *, limit: int = 30
    ) -> str:
        """Return bounded, sanitized history, optionally scoped to one task.

        When ``task_id`` is supplied, only task events for that exact task and
        change events belonging to that task are included. Change journals in
        older workspaces may not store a task_id field, so their change_id
        prefix (``<task_id>-``) is used as the compatibility key.
        Journal payloads, recovery bytes, and raw file contents are never exposed.
        """
        root = self.coordinator.workspace.root
        task_path = root / ".coding-agent" / "tasks.jsonl"
        change_path = root / ".coding-agent" / "changes.jsonl"
        lines: list[str] = []

        def read_jsonl(path: Path) -> list[dict]:
            if not path.is_file():
                return []
            records = []
            try:
                raw_lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                return []
            for raw in raw_lines[-limit:]:
                try:
                    value = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if isinstance(value, dict):
                    records.append(value)
            return records

        task_events = read_jsonl(task_path)
        change_events = read_jsonl(change_path)

        if task_id is not None:
            task_events = [
                item for item in task_events
                if item.get("task_id") == task_id
            ]
            change_events = [
                item for item in change_events
                if item.get("task_id") == task_id
                or str(item.get("change_id") or "").startswith(f"{task_id}-")
            ]

        if task_events:
            lines.append("RECENT TASK HISTORY (summaries only):")
            for item in task_events:
                details = item.get("details") or {}
                summary = details.get("plan_summary") or details.get("summary") or details.get("errors")
                lines.append(
                    f"- task={item.get('task_id')} event={item.get('event')} "
                    f"attempt={item.get('attempt')} summary={str(summary)[:500]}"
                )

        if change_events:
            lines.append("RECENT FILE CHANGE HISTORY (metadata only):")
            for item in change_events:
                lines.append(
                    f"- path={item.get('relative_path')} operation={item.get('operation')} "
                    f"event={item.get('event')} state={item.get('state')} "
                    f"error={str(item.get('error') or '')[:240]}"
                )

        return "\n".join(lines)

    def _planner_plan(self, task: CodingTask, context: str,
                      specification: EngineeringSpecification | None = None) -> ImplementationPlan:
        """Call modern planners with ESpec while preserving injected test/custom planners."""
        parameters = signature(self.planner.plan).parameters
        if specification is not None and "specification" in parameters:
            return self.planner.plan(task, context=context, specification=specification)
        return self.planner.plan(task, context=context)

    def _run_once(
        self,
        task: CodingTask,
        *,
        approval_handler: ApprovalHandler,
        additional_context: str = "",
        specification: EngineeringSpecification | None = None,
        attempt_number: int = 1,
        manual_verification_handler: ManualVerificationHandler | None = None,
    ) -> ExecutionResult:
        """Execute one fully approved implementation attempt."""

        errors: list[str] = []
        notes: list[str] = []
        coordinated_changes: list[CoordinatedChange] = []
        command_results = []

        try:
            self._validate_task_workspace(task)
            if specification is not None:
                if specification.task_id != task.task_id:
                    raise OrchestrationError("Engineering Specification task ID does not match the task")
                if Path(specification.workspace).expanduser().resolve() != self.coordinator.workspace.root.resolve():
                    raise OrchestrationError("Engineering Specification workspace does not match the authorized workspace")
        except (OSError, RuntimeError, OrchestrationError, ValueError) as exc:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.BLOCKED,
                errors=(str(exc),),
            )

        # 1. Build repository context and generate the implementation plan.
        try:
            context_package = self.context_engine.build(task)

            notes.append(
                f"Planner context assembled from "
                f"{len(context_package.files)} file(s)."
            )

            planner_context = context_package.render()
            historical_context = self._historical_context(task.task_id)
            if historical_context:
                planner_context += "\n\n" + historical_context
            if additional_context:
                planner_context += "\n\n" + additional_context
            plan = self._planner_plan(task, planner_context, specification)

            if specification is not None:
                validator = PlanSpecificationValidator()
                for correction in range(self.max_planning_corrections + 1):
                    report = validator.validate(plan, specification)
                    if report.valid:
                        notes.append(
                            "ESpec plan validation passed: "
                            f"{len(report.covered_requirements)}/{len(report.required_requirements)} "
                            "implementation requirements covered; "
                            f"{len(report.covered_units)}/{len(specification.implementation_units)} "
                            "implementation units covered; all changed paths declared. "
                            "Unmapped constraints remain compliance boundaries."
                        )
                        break
                    if correction >= self.max_planning_corrections:
                        raise OrchestrationError(
                            "ESpec plan validation failed after bounded corrections: "
                            + " | ".join(report.issues)
                        )
                    declared_files = sorted({
                        path
                        for module in specification.architecture
                        for path in module.file_paths
                    })
                    units_by_path = {}
                    for unit in specification.implementation_units:
                        units_by_path.setdefault(unit.target_path, []).append(unit)
                    unit_scopes = [
                        f"{path} -> "
                        + ", ".join(
                            f"{unit.unit_id} (satisfies: {', '.join(unit.satisfies) or 'none'})"
                            for unit in grouped_units
                        )
                        for path, grouped_units in sorted(units_by_path.items())
                    ]
                    requirement_kinds = sorted({
                        f"{requirement.requirement_id} [{getattr(requirement.kind, 'value', requirement.kind)}]"
                        for requirement in specification.requirements
                    })
                    issue_text = " ".join(report.issues).casefold()
                    repair_guidance = []
                    if ".env.example" in issue_text or "environment setting" in issue_text:
                        repair_guidance.append(
                            "ENVIRONMENT FILE RULES:\n"
                            "- Include the exact .env.example implementation unit; do not omit it or leave it empty.\n"
                            "- Document EVERY setting with a meaningful comment immediately above it or an explanatory inline comment.\n"
                            "- Use the variable names actually referenced by docker-compose.yml: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, and POSTGRES_PORT where applicable.\n"
                            "- Non-secret local defaults are valid: POSTGRES_DB=fafas_burger, POSTGRES_USER=fafas_app, POSTGRES_HOST=postgres, POSTGRES_PORT=5432.\n"
                            "- POSTGRES_PASSWORD must be an obvious non-secret placeholder such as change_me or example_password. Never include real credentials.\n"
                            "Example:\n"
                            "# Database name used by PostgreSQL\nPOSTGRES_DB=fafas_burger\n"
                            "# Application database user\nPOSTGRES_USER=fafas_app\n"
                            "# Non-secret local placeholder\nPOSTGRES_PASSWORD=change_me\n"
                            "# PostgreSQL service DNS name\nPOSTGRES_HOST=postgres\n"
                            "# PostgreSQL container port\nPOSTGRES_PORT=5432"
                        )
                    if "project.yaml" in issue_text or "parent phase" in issue_text:
                        repair_guidance.append(
                            "PROJECT METADATA RULES:\n"
                            "- Include the exact project.yaml implementation unit with non-empty goal, approved_stack, repository_architecture, phases, and acceptance_status fields.\n"
                            "- The task provides six parent phase IDs A through F but not all phase titles. Do NOT invent titles. Enumerate six entries with IDs A, B, C, D, E, F and neutral names 'Phase A' through 'Phase F' unless actual titles are explicitly present in task context.\n"
                            "- Mark Phase A / phase_1 as in_progress; do not mark it complete.\n"
                            "Required YAML shape:\n"
                            "goal: Build a polished, realistic burger-shop web application for customer browsing/ordering and staff administration.\n"
                            "approved_stack:\n  frontend: React + TypeScript + Vite\n  backend: Python + Django + Django REST Framework\n  database: PostgreSQL\n  orchestration: Docker Compose\n"
                            "repository_architecture:\n  frontend: Customer-facing React application\n  backend: Django REST API\n  postgres: PostgreSQL persistence\n"
                            "phases:\n  - id: A\n    name: Phase A\n  - id: B\n    name: Phase B\n  - id: C\n    name: Phase C\n  - id: D\n    name: Phase D\n  - id: E\n    name: Phase E\n  - id: F\n    name: Phase F\n"
                            "acceptance_status:\n  phase_1: in_progress\n  overall: in_progress"
                        )
                    if any(token in issue_text for token in ("pg_isready", "service_healthy", "named volume", "healthcheck")):
                        repair_guidance.append(
                            "DOCKER COMPOSE RULES:\n"
                            "- PostgreSQL data MUST use a named volume and declare the same name in the top-level Compose volumes mapping.\n"
                            "Example:\n"
                            "services:\n  postgres:\n    volumes:\n      - postgres_data:/var/lib/postgresql/data\n"
                            "    healthcheck:\n      test: [\\\"CMD-SHELL\\\", \\\"pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}\\\"]\n"
                            "      interval: 10s\n      timeout: 5s\n      retries: 5\n"
                            "volumes:\n  postgres_data:\n"
                            "- The postgres_data declaration must be root-level, aligned with services:, not nested under services or postgres.\n"
                            "- Backend must wait for postgres using depends_on.postgres.condition: service_healthy. Do not claim backend/frontend health unless that service has a healthcheck. For this infrastructure-only package, frontend may use service_started for backend if no backend healthcheck exists."
                        )
                    diagnostic = (
                        "ENGINEERING SPECIFICATION PLAN VALIDATION FAILURE\n"
                        + "\n".join(report.issues)
                        + "\nIMPLEMENTATION COVERAGE CONTRACT:\n"
                        "- Every implementation unit is mandatory. The plan schema permits one file change per path.\n"
                        "- Group units by target_path: create exactly one changed file for each unique target_path.\n"
                        "- One change at a shared target_path covers every unit in that path group only when it satisfies the combined acceptance criteria and contract obligations of all those units.\n"
                        "- Distinct target_path values each require a separate file change. Never duplicate a path.\n"
                        "- Functional and non-functional requirements are implementation obligations.\n"
                        "- A constraint with no implementation-unit mapping is a compliance boundary, not a request to create or modify a file solely to demonstrate compliance. Respect it in the changes and verification.\n"
                        "- A constraint explicitly mapped to implementation units must be implemented through all of those units.\n"
                        "Revise the complete plan. Preserve exact ESpec filenames and extensions. Do not invent files, rename docker-compose.yml to docker-compose.yaml, or move files to different locations.\n"
                        + "REQUIREMENT KINDS:\n- "
                        + "\n- ".join(requirement_kinds)
                        + "\nEXACT ARCHITECTURE FILE PATHS:\n- "
                        + "\n- ".join(declared_files)
                        + "\nIMPLEMENTATION FILE GROUPS (one change per unique target_path):\n- "
                        + "\n- ".join(unit_scopes)
                        + "\nMISSING IMPLEMENTATION UNITS:\n- "
                        + ("\n- ".join(report.missing_units) if report.missing_units else "(none)")
                        + "\n\nPREVIOUS DRAFT FILE CONTENTS (preserve all valid content; edit only artifacts implicated by the errors):\n"
                        + json.dumps([
                            {
                                "path": change.path,
                                "change_type": change.change_type.value,
                                "reason": change.reason,
                                "content": change.content,
                            }
                            for change in plan.changes
                        ], ensure_ascii=False, indent=2)
                        + ("\n\nVALIDATION-SPECIFIC REPAIR GUIDANCE:\n" + "\n\n".join(repair_guidance) if repair_guidance else "")
                    )
                    plan = self._planner_plan(
                        task, planner_context + "\n\n" + diagnostic, specification
                    )

        except Exception as exc:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                errors=(f"Context preparation or planning failed: {exc}",),
                notes=tuple(notes),
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

        # Verification commands from the model are suggestions, not authority.
        # Add deterministic checks for the proposed paths and retain pytest only
        # when the workspace actually contains tests.
        try:
            verification_commands = VerificationPlanner(
                self.coordinator.workspace.root
            ).build(plan.changes, plan.verification_commands)
            if specification is not None:
                specification_commands = SpecificationVerificationEngine.required_commands(specification)
                verification_commands = tuple(dict.fromkeys(verification_commands + specification_commands))
            plan = replace(plan, verification_commands=verification_commands)
        except Exception as exc:
            return ExecutionResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                errors=(f"Could not prepare verification checks: {exc}",),
            )

        # 2. Validate operations and capture baselines. Correct only clear
        # create/modify/delete state mismatches; path-safety failures remain hard stops.
        approved_changes = []
        planning_corrections = 0

        while True:
            try:
                approved_changes = []
                normalized_proposals = []
                for index, proposal in enumerate(plan.changes):
                    proposal, normalization_note = self._normalize_proposal_operation(
                        proposal
                    )
                    if normalization_note:
                        notes.append(normalization_note)
                        warning = self._record_task_event(
                            task.task_id,
                            "planning_operation_normalized",
                            0,
                            {
                                "path": proposal.path,
                                "operation": proposal.change_type.value,
                                "reason": normalization_note,
                            },
                        )
                        if warning:
                            notes.append(warning)
                    normalized_proposals.append(proposal)
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
                if tuple(normalized_proposals) != plan.changes:
                    plan = replace(plan, changes=tuple(normalized_proposals))
                break
            except (WorkspacePathError, OrchestrationError) as exc:
                message = str(exc)
                lowered = message.casefold()
                correctable = (
                    "target already exists" in lowered
                    or "must not already exist" in lowered
                    or "does not resolve to an existing target" in lowered
                    or "target does not exist" in lowered
                    or "target must already exist" in lowered
                    or "target must not already exist" in lowered
                )
                if not correctable:
                    return ExecutionResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        errors=(f"Could not prepare implementation changes: {exc}",),
                        notes=tuple(notes),
                        plan_summary=plan.summary,
                    )
                if planning_corrections >= self.max_planning_corrections:
                    return ExecutionResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        errors=(
                            f"Planning correction limit reached ({self.max_planning_corrections}). "
                            f"Last validation error: {exc}",
                        ),
                        notes=tuple(notes),
                        plan_summary=plan.summary,
                    )

                planning_corrections += 1
                diagnostic = (
                    "PLAN VALIDATION FAILURE\n"
                    f"{message}\n"
                    "Correct the implementation plan. Re-check the workspace file inventory. "
                    "Use modify for an existing file that needs changes, create only for a "
                    "genuinely missing file, and delete only for an existing file explicitly "
                    "intended for removal. Do not weaken path safety, do not alter unrelated "
                    "files, and return a complete corrected plan for the entire task."
                )
                warning = self._record_task_event(
                    task.task_id,
                    "planning_correction_requested",
                    0,
                    {"correction": planning_corrections, "diagnostic": message[:1000]},
                )
                if warning:
                    notes.append(warning)
                notes.append(
                    f"Planning correction {planning_corrections}/{self.max_planning_corrections} "
                    "requested after deterministic workspace validation."
                )
                try:
                    plan = self._planner_plan(
                        task, planner_context + "\n\n" + diagnostic, specification
                    ) if specification is not None else self.planner.plan(
                        task,
                        context=planner_context + "\n\n" + diagnostic,
                    )
                except Exception as planning_exc:
                    return ExecutionResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        errors=(f"Corrective planning failed: {planning_exc}",),
                        notes=tuple(notes),
                        plan_summary=plan.summary,
                    )
                if not isinstance(plan, ImplementationPlan) or plan.task_id != task.task_id or not plan.changes:
                    return ExecutionResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        errors=("Corrective planner returned an invalid or empty plan.",),
                        notes=tuple(notes),
                    )
                if specification is not None:
                    coverage_report = PlanSpecificationValidator().validate(plan, specification)
                    if not coverage_report.valid:
                        return ExecutionResult(
                            task_id=task.task_id,
                            status=TaskStatus.FAILED,
                            errors=("Corrective plan failed ESpec validation: " + " | ".join(coverage_report.issues),),
                            notes=tuple(notes),
                            plan_summary=plan.summary,
                        )

                warning = self._record_task_event(
                    task.task_id,
                    "planning_correction_generated",
                    0,
                    {"correction": planning_corrections, "plan_summary": plan.summary},
                )
                if warning:
                    notes.append(warning)

                try:
                    verification_commands = VerificationPlanner(
                        self.coordinator.workspace.root
                    ).build(plan.changes, plan.verification_commands)
                    if specification is not None:
                        specification_commands = SpecificationVerificationEngine.required_commands(specification)
                        verification_commands = tuple(dict.fromkeys(verification_commands + specification_commands))
                    plan = replace(plan, verification_commands=verification_commands)
                except Exception as verify_exc:
                    return ExecutionResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        errors=(f"Could not prepare verification checks for corrected plan: {verify_exc}",),
                        notes=tuple(notes),
                    )
            except Exception as exc:
                return ExecutionResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    errors=(f"Could not prepare implementation changes: {exc}",),
                    notes=tuple(notes),
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

        specification_report = None
        if specification is not None:
            try:
                verification_engine = SpecificationVerificationEngine()
                specification_report = verification_engine.evaluate(
                    specification, command_results
                )
                manual_criteria = tuple(
                    item for item in specification_report.criteria
                    if item.required and item.kind.value == "manual"
                )
                automated_failure = any(
                    item.required and item.kind.value != "manual"
                    and item.status.value == "FAIL"
                    for item in specification_report.criteria
                )
                if manual_criteria and manual_verification_handler is not None and not automated_failure:
                    try:
                        manual_evidence = manual_verification_handler(manual_criteria)
                    except Exception as exc:
                        raise OrchestrationError(f"Manual verification handler failed: {exc}") from exc
                    if manual_evidence is None:
                        manual_evidence = {}
                    if not isinstance(manual_evidence, Mapping):
                        raise OrchestrationError("Manual verification handler must return a mapping")
                    specification_report = verification_engine.evaluate(
                        specification, command_results, manual_evidence=manual_evidence
                    )
                    warning = self._record_task_event(
                        task.task_id, "manual_verification_completed", attempt_number,
                        {"evidence": dict(manual_evidence)},
                    )
                    if warning:
                        notes.append(warning)
                notes.append(specification_report.summary)
                journal_warning = self._record_task_event(
                    task.task_id,
                    "specification_verification_completed",
                    attempt_number,
                    specification_report.to_dict(),
                )
                if journal_warning:
                    notes.append(journal_warning)
            except Exception as exc:
                errors.append(f"Specification verification evaluation failed: {exc}")

        if (report is None or not report.passed
                or (specification is not None and
                    (specification_report is None or not specification_report.passed))):
            if report is not None:
                errors.extend(issue.message for issue in report.issues)
            if specification is not None and specification_report is not None and not specification_report.passed:
                errors.append(specification_report.summary)
                errors.extend(
                    f"Required criterion {item.criterion_id}: {item.status.value} — {item.reason}"
                    for item in specification_report.criteria
                    if item.required and item.status.value != "PASS"
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
                command_results=tuple(command_results),
                errors=tuple(errors),
                notes=tuple(notes),
                plan_summary=plan.summary,
                proposed_changes=plan.changes,
                specification_report=(specification_report.to_dict() if specification_report else None),
            )

        # 8. Only now is the task considered complete.
        return ExecutionResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            changed_files=self._changed_files(coordinated_changes),
            command_results=tuple(command_results),
            notes=tuple(notes),
            plan_summary=plan.summary,
            proposed_changes=plan.changes,
            specification_report=(specification_report.to_dict() if specification_report else None),
        )


    def _record_task_event(
        self,
        task_id: str,
        event: str,
        attempt: int,
        details: dict | None = None,
    ) -> str | None:
        """Persist task lifecycle telemetry without masking task outcomes."""
        try:
            self.task_journal.append(
                task_id=task_id,
                event=event,
                attempt=attempt,
                details=details or {},
            )
        except TaskJournalError as exc:
            return f"Task journal warning: {exc}"
        return None

    def run(
        self,
        task: CodingTask,
        *,
        approval_handler: ApprovalHandler,
        retry_handler: Callable[[RetryRequest], bool] | None = None,
        specification: EngineeringSpecification | None = None,
        manual_verification_handler: ManualVerificationHandler | None = None,
    ) -> ExecutionResult:
        """Run bounded attempts, asking the caller before every recovery retry.

        Every corrective implementation is a fresh plan and passes through the
        same approval handler as the initial plan. Failed applied changes are
        rolled back by ``_run_once`` before a retry can be requested.
        """
        history: list[AttemptRecord] = []
        recovery_context = ""
        if specification is None:
            client = getattr(self.planner, "client", None)
            if callable(getattr(client, "generate_json", None)):
                diagnostics_dir = (
                    self.coordinator.workspace.root / ".coding-agent" / "diagnostics"
                    / EngineeringSpecificationGenerator._slug(task.task_id)
                )
                generator = None
                try:
                    project_context = self.context_engine.build(task).render()
                    generator = EngineeringSpecificationGenerator(client, diagnostics_dir=diagnostics_dir)
                    specification = generator.generate(
                        task_id=task.task_id,
                        objective=task.description,
                        workspace=task.workspace,
                        constraints=task.constraints,
                        acceptance_criteria=task.acceptance_criteria,
                        project_context=project_context,
                    )
                    warning = self._record_task_event(
                        task.task_id, "engineering_specification_created", 0,
                        {"specification": specification.to_dict()},
                    )
                    if warning:
                        recovery_context += warning
                except Exception as exc:
                    trace = generator._trace_payload(generator.last_trace) if generator is not None else []
                    warning = self._record_task_event(
                        task.task_id, "engineering_specification_failed", 0,
                        {
                            "exception_type": type(exc).__name__,
                            "error": str(exc),
                            "generation_trace": trace,
                            "diagnostics_dir": str(diagnostics_dir),
                        },
                    )
                    errors = (f"Engineering Specification generation failed: {exc}",)
                    if warning:
                        errors += (warning,)
                    return ExecutionResult(
                        task_id=task.task_id, status=TaskStatus.FAILED, errors=errors,
                    )
        if specification is not None:
            try:
                specification, check_notes = SpecificationCheckPlanner(
                    self.coordinator.workspace.root
                ).prepare(specification)
                for note in check_notes:
                    recovery_context += ("\n" if recovery_context else "") + note
            except Exception as exc:
                return ExecutionResult(
                    task_id=task.task_id, status=TaskStatus.FAILED,
                    errors=(f"Could not prepare ESpec verification criteria: {exc}",),
                )

        journal_warnings: list[str] = []
        final_result: ExecutionResult | None = None

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            warning = self._record_task_event(
                task.task_id,
                "attempt_started",
                attempt,
                {"max_attempts": self.retry_policy.max_attempts},
            )
            if warning:
                journal_warnings.append(warning)

            result = self._run_once(
                task,
                approval_handler=approval_handler,
                additional_context=recovery_context,
                specification=specification,
                attempt_number=attempt,
                manual_verification_handler=manual_verification_handler,
            )
            final_result = result

            if result.status == TaskStatus.COMPLETED:
                history.append(AttemptRecord(
                    attempt_number=attempt,
                    phase="implementation",
                    outcome="completed",
                    summary=result.plan_summary,
                    errors=(),
                ))
                warning = self._record_task_event(
                    task.task_id,
                    "task_completed",
                    attempt,
                    {"plan_summary": result.plan_summary},
                )
                if warning:
                    journal_warnings.append(warning)
                return replace(
                    result,
                    attempt_history=tuple(history),
                    notes=result.notes + tuple(journal_warnings),
                )

            if result.status != TaskStatus.FAILED:
                history.append(AttemptRecord(
                    attempt_number=attempt,
                    phase="approval" if result.status == TaskStatus.BLOCKED else "execution",
                    outcome=result.status.value,
                    summary=result.plan_summary,
                    errors=result.errors,
                ))
                self._record_task_event(
                    task.task_id,
                    "task_blocked" if result.status == TaskStatus.BLOCKED else "task_ended",
                    attempt,
                    {"errors": list(result.errors)},
                )
                return replace(
                    result,
                    attempt_history=tuple(history),
                    notes=result.notes + tuple(journal_warnings),
                )

            phase = FailureClassifier.classify(result, self.retry_policy)
            history.append(AttemptRecord(
                attempt_number=attempt,
                phase=phase or "execution",
                outcome="failed",
                summary=result.plan_summary,
                errors=result.errors,
            ))
            warning = self._record_task_event(
                task.task_id,
                "attempt_failed",
                attempt,
                {
                    "phase": phase or "non_recoverable",
                    "errors": list(result.errors),
                    "commands": [
                        {
                            "command": list(item.command),
                            "exit_code": item.exit_code,
                            "timed_out": item.timed_out,
                            "stdout": item.stdout[:6000],
                            "stderr": item.stderr[:6000],
                        }
                        for item in result.command_results
                    ],
                },
            )
            if warning:
                journal_warnings.append(warning)

            if phase is None:
                return replace(
                    result,
                    attempt_history=tuple(history),
                    notes=result.notes + tuple(journal_warnings),
                )

            diagnosis = self.failure_diagnoser.diagnose(result, phase)
            warning = self._record_task_event(
                task.task_id,
                "failure_diagnosed",
                attempt,
                {
                    "phase": diagnosis.phase.value,
                    "category": diagnosis.category.value,
                    "confidence": diagnosis.confidence,
                    "actionable": diagnosis.actionable,
                    "summary": diagnosis.summary,
                    "evidence": list(diagnosis.evidence),
                },
            )
            if warning:
                journal_warnings.append(warning)

            if not diagnosis.actionable:
                history[-1] = replace(history[-1], phase=diagnosis.phase.value)
                return replace(
                    result,
                    attempt_history=tuple(history),
                    notes=result.notes + (
                        f"Repair not attempted: {diagnosis.summary}",
                    ) + tuple(journal_warnings),
                )

            repair_strategy = self.repair_strategy_generator.generate(
                diagnosis, result
            )
            warning = self._record_task_event(
                task.task_id,
                "repair_strategy_generated",
                attempt,
                {
                    "category": diagnosis.category.value,
                    "objective": repair_strategy.objective,
                    "affected_paths": list(repair_strategy.affected_paths),
                    "instructions": list(repair_strategy.instructions),
                },
            )
            if warning:
                journal_warnings.append(warning)

            if attempt >= self.retry_policy.max_attempts:
                return replace(
                    result,
                    attempt_history=tuple(history),
                    notes=result.notes + (
                        f"Retry limit reached ({self.retry_policy.max_attempts} total attempts).",
                    ) + tuple(journal_warnings),
                )

            request = RetryRequest(
                task_id=task.task_id,
                attempt_number=attempt,
                max_attempts=self.retry_policy.max_attempts,
                phase=phase,
                summary=(
                    "Implementation verification failed."
                    if phase == "verification"
                    else "The planner could not produce a valid implementation plan."
                ),
                errors=result.errors,
                result=result,
                diagnosis=diagnosis,
                repair_strategy=repair_strategy,
            )

            if retry_handler is None:
                history[-1] = replace(history[-1], retry_decision="not_requested")
                return replace(
                    result,
                    attempt_history=tuple(history),
                    notes=result.notes + (
                        "A retry is available, but no retry approval handler was provided.",
                    ) + tuple(journal_warnings),
                )

            try:
                retry_approved = retry_handler(request) is True
            except Exception as exc:
                history[-1] = replace(history[-1], retry_decision="approval_error")
                return replace(
                    result,
                    status=TaskStatus.BLOCKED,
                    errors=result.errors + (f"Retry approval handler failed: {exc}",),
                    attempt_history=tuple(history),
                    notes=result.notes + tuple(journal_warnings),
                )

            history[-1] = replace(
                history[-1],
                retry_decision="approved" if retry_approved else "declined",
            )
            warning = self._record_task_event(
                task.task_id,
                "retry_approved" if retry_approved else "retry_declined",
                attempt,
                {"phase": phase, "summary": request.summary},
            )
            if warning:
                journal_warnings.append(warning)

            if not retry_approved:
                return replace(
                    result,
                    status=TaskStatus.BLOCKED,
                    attempt_history=tuple(history),
                    notes=result.notes + (
                        "Retry declined. The failed attempt was rolled back; no further changes were applied.",
                    ) + tuple(journal_warnings),
                )

            recovery_context = build_recovery_context(request)

        # The loop always returns from one of the branches above.
        assert final_result is not None
        return replace(
            final_result,
            attempt_history=tuple(history),
            notes=final_result.notes + tuple(journal_warnings),
        )
