
from pathlib import Path
import json
from dataclasses import replace

import pytest

from coding_agent.change_journal import ChangeJournal
from coding_agent.coordinator import ChangeCoordinator
from coding_agent.models import (
    ChangeType,
    CodingTask,
    CommandResult,
    FileChange,
    ImplementationPlan,
    TaskStatus,
)
from coding_agent.orchestrator import TaskOrchestrator
from coding_agent.verification import VerificationEngine
from coding_agent.workspace import Workspace


class FakePlanner:
    def __init__(self, plan=None, error=None):
        self.plan_result = plan
        self.error = error
        self.calls = []
        self.contexts = []

    def plan(self, task, context=""):
        self.calls.append(task)
        self.contexts.append(context)

        if self.error:
            raise self.error

        return self.plan_result


class FakeCommandRunner:
    def __init__(self, workspace, results=None, error=None):
        self.workspace = workspace
        self.results = list(results or [])
        self.error = error
        self.calls = []

    def run(self, command, *, approved_commands):
        self.calls.append((tuple(command), tuple(approved_commands)))

        if self.error:
            raise self.error

        if self.results:
            return replace(self.results.pop(0), command=tuple(command))

        return CommandResult(
            command=tuple(command),
            exit_code=0,
        )


class TrackingCoordinator:
    """Delegate to a real coordinator while recording rollback order."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.workspace = delegate.workspace
        self.rollback_order = []

    def apply(self, change, proposal):
        return self.delegate.apply(change, proposal)

    def verify(self, coordinated):
        return self.delegate.verify(coordinated)

    def rollback(self, coordinated):
        self.rollback_order.append(coordinated.change.relative_path)
        return self.delegate.rollback(coordinated)


def make_task(tmp_path):
    return CodingTask(
        task_id="task-001",
        description="Update application configuration",
        workspace=str(tmp_path),
    )


def make_plan(task, changes=None, commands=None, requires_approval=True):
    return ImplementationPlan(
        task_id=task.task_id,
        summary="Update configuration",
        changes=tuple(changes or []),
        verification_commands=tuple(
            [("python", "-m", "pytest")] if commands is None else commands
        ),
        requires_approval=requires_approval,
        risks=(),
    )


def make_orchestrator(tmp_path, plan, *, results=None, runner_error=None):
    workspace = Workspace(tmp_path)
    planner = FakePlanner(plan)
    coordinator = ChangeCoordinator(workspace)
    runner = FakeCommandRunner(
        workspace,
        results=results,
        error=runner_error,
    )

    orchestrator = TaskOrchestrator(
        planner,
        coordinator,
        runner,
        VerificationEngine(),
    )

    return orchestrator, workspace, planner, runner


def test_successful_task_applies_and_verifies_changes(tmp_path):
    task = make_task(tmp_path)
    target = tmp_path / "config.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")

    plan = make_plan(
        task,
        changes=[
            FileChange(
                path="config.py",
                change_type=ChangeType.MODIFY,
                reason="Update value",
                content="VALUE = 2\n",
            )
        ],
    )

    orchestrator, _, _, runner = make_orchestrator(tmp_path, plan)

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
    )

    assert result.status == TaskStatus.COMPLETED, result.errors
    assert result.succeeded is True
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert result.changed_files == ("config.py",)
    assert len(runner.calls) == 1


def test_rejected_plan_does_not_modify_workspace(tmp_path):
    task = make_task(tmp_path)
    target = tmp_path / "config.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")

    plan = make_plan(
        task,
        changes=[
            FileChange(
                path="config.py",
                change_type=ChangeType.MODIFY,
                reason="Update value",
                content="VALUE = 2\n",
            )
        ],
    )

    orchestrator, _, _, runner = make_orchestrator(tmp_path, plan)

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: False,
    )

    assert result.status == TaskStatus.BLOCKED
    assert result.changed_files == ()
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert runner.calls == []


def test_model_cannot_bypass_human_approval(tmp_path):
    task = make_task(tmp_path)

    plan = make_plan(
        task,
        changes=[
            FileChange(
                path="new.py",
                change_type=ChangeType.CREATE,
                reason="Create module",
                content="VALUE = 1\n",
            )
        ],
        requires_approval=False,
    )

    orchestrator, _, _, _ = make_orchestrator(tmp_path, plan)
    approval_calls = []

    def reject(proposed):
        approval_calls.append(proposed)
        return False

    result = orchestrator.run(
        task,
        approval_handler=reject,
    )

    assert len(approval_calls) == 1
    assert result.status == TaskStatus.BLOCKED
    assert not (tmp_path / "new.py").exists()


def test_failed_verification_rolls_back_all_changes_in_reverse_order(
    tmp_path,
):
    task = make_task(tmp_path)

    first = tmp_path / "first.py"
    second = tmp_path / "second.py"

    first.write_text("VALUE = 1\n", encoding="utf-8")
    second.write_text("VALUE = 10\n", encoding="utf-8")

    plan = make_plan(
        task,
        changes=[
            FileChange(
                path="first.py",
                change_type=ChangeType.MODIFY,
                reason="Update first value",
                content="VALUE = 2\n",
            ),
            FileChange(
                path="second.py",
                change_type=ChangeType.MODIFY,
                reason="Update second value",
                content="VALUE = 20\n",
            ),
        ],
    )

    failed_result = CommandResult(
        command=("python", "-m", "pytest"),
        exit_code=1,
        stdout="Tests failed",
    )

    orchestrator, workspace, _, _ = make_orchestrator(
        tmp_path,
        plan,
        results=[failed_result],
    )

    tracking = TrackingCoordinator(orchestrator.coordinator)
    orchestrator.coordinator = tracking

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
    )

    assert result.status == TaskStatus.FAILED
    assert result.succeeded is False

    assert first.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert second.read_text(encoding="utf-8") == "VALUE = 10\n"

    assert tracking.rollback_order == [
        "second.py",
        "first.py",
    ]


def test_create_operation_is_removed_after_verification_failure(tmp_path):
    task = make_task(tmp_path)

    plan = make_plan(
        task,
        changes=[
            FileChange(
                path="new_module.py",
                change_type=ChangeType.CREATE,
                reason="Add new module",
                content="VALUE = 42\n",
            )
        ],
    )

    failed_result = CommandResult(
        command=("python", "-m", "pytest"),
        exit_code=1,
        stderr="Test failure",
    )

    orchestrator, _, _, _ = make_orchestrator(
        tmp_path,
        plan,
        results=[failed_result],
    )

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
    )

    assert result.status == TaskStatus.FAILED
    assert not (tmp_path / "new_module.py").exists()


def test_command_runner_exception_triggers_rollback(tmp_path):
    task = make_task(tmp_path)

    target = tmp_path / "config.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")

    plan = make_plan(
        task,
        changes=[
            FileChange(
                path="config.py",
                change_type=ChangeType.MODIFY,
                reason="Update value",
                content="VALUE = 2\n",
            )
        ],
    )

    orchestrator, _, _, _ = make_orchestrator(
        tmp_path,
        plan,
        runner_error=RuntimeError("runner unavailable"),
    )

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
    )

    assert result.status == TaskStatus.FAILED
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert any("runner unavailable" in error for error in result.errors)


def test_plan_without_model_commands_gets_deterministic_verification(tmp_path):
    task = make_task(tmp_path)

    plan = make_plan(
        task,
        changes=[
            FileChange(
                path="new.py",
                change_type=ChangeType.CREATE,
                reason="Create module",
                content="VALUE = 1\n",
            )
        ],
        commands=[],
    )

    orchestrator, _, _, runner = make_orchestrator(tmp_path, plan)
    approved_plans = []

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: approved_plans.append(proposed) or True,
    )

    assert result.status == TaskStatus.COMPLETED, result.errors
    assert len(approved_plans) == 1
    assert approved_plans[0].verification_commands[0][0:2] == ("python", "-c")
    assert len(runner.calls) == 1
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_plan_for_another_task_is_rejected(tmp_path):
    task = make_task(tmp_path)

    plan = ImplementationPlan(
        task_id="different-task",
        summary="Wrong task",
        changes=(
            FileChange(
                path="new.py",
                change_type=ChangeType.CREATE,
                reason="Create module",
                content="VALUE = 1\n",
            ),
        ),
        verification_commands=(("python", "-m", "pytest"),),
    )

    orchestrator, _, _, runner = make_orchestrator(tmp_path, plan)
    approval_calls = []

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: approval_calls.append(proposed),
    )

    assert result.status == TaskStatus.FAILED
    assert approval_calls == []
    assert runner.calls == []


def test_planning_failure_returns_failed_result(tmp_path):
    task = make_task(tmp_path)

    workspace = Workspace(tmp_path)
    planner = FakePlanner(error=RuntimeError("LLM unavailable"))
    coordinator = ChangeCoordinator(workspace)
    runner = FakeCommandRunner(workspace)

    orchestrator = TaskOrchestrator(
        planner,
        coordinator,
        runner,
        VerificationEngine(),
    )

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
    )

    assert result.status == TaskStatus.FAILED
    assert any("LLM unavailable" in error for error in result.errors)
    assert runner.calls == []


def test_task_cannot_target_a_different_workspace(tmp_path):
    authorized = tmp_path / "authorized"
    unauthorized = tmp_path / "unauthorized"
    authorized.mkdir()
    unauthorized.mkdir()

    task = make_task(unauthorized)

    plan = make_plan(
        task,
        changes=[
            FileChange(
                path="new.py",
                change_type=ChangeType.CREATE,
                reason="Create module",
                content="VALUE = 1\n",
            )
        ],
    )

    orchestrator, _, _, runner = make_orchestrator(
        authorized,
        ImplementationPlan(
            task_id=task.task_id,
            summary=plan.summary,
            changes=plan.changes,
            verification_commands=plan.verification_commands,
        ),
    )

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
    )

    assert result.status == TaskStatus.BLOCKED
    assert runner.calls == []
    assert not (unauthorized / "new.py").exists()


def test_orchestrator_passes_workspace_context_to_planner(tmp_path):
    task = make_task(tmp_path)

    readme = tmp_path / "README.md"
    readme.write_text(
        "# Existing Project\nAuthentication service.",
        encoding="utf-8",
    )

    plan = make_plan(
        task,
        changes=[
            FileChange(
                path="example.py",
                change_type=ChangeType.CREATE,
                reason="Create a test module",
                content="VALUE = 42\n",
            )
        ],
    )

    orchestrator, _, planner, _ = make_orchestrator(
        tmp_path,
        plan,
    )

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: False,
    )

    assert result.status == TaskStatus.BLOCKED
    assert len(planner.contexts) == 1
    assert "PROJECT CONTEXT" in planner.contexts[0]
    assert "README.md" in planner.contexts[0]
    assert "Authentication service" in planner.contexts[0]



class SequencePlanner:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.contexts = []

    def plan(self, task, context=""):
        self.calls.append(task)
        self.contexts.append(context)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SequencedCommandRunner:
    def __init__(self, workspace, exit_codes):
        self.workspace = workspace
        self.exit_codes = list(exit_codes)
        self.calls = []

    def run(self, command, *, approved_commands):
        self.calls.append(tuple(command))
        code = self.exit_codes.pop(0)
        return CommandResult(
            command=tuple(command),
            exit_code=code,
            stderr="assertion failed: expected corrected behavior" if code else "",
        )


def test_verification_failure_can_retry_with_diagnostics_and_fresh_approval(tmp_path):
    task = make_task(tmp_path)
    first_plan = make_plan(task, changes=[
        FileChange("module.py", ChangeType.CREATE, "Initial implementation", "VALUE = 1\n")
    ], commands=[])
    corrected_plan = make_plan(task, changes=[
        FileChange("module.py", ChangeType.CREATE, "Corrected implementation", "VALUE = 2\n")
    ], commands=[])

    workspace = Workspace(tmp_path)
    planner = SequencePlanner([first_plan, corrected_plan])
    coordinator = ChangeCoordinator(workspace)
    runner = SequencedCommandRunner(workspace, [1, 0])
    orchestrator = TaskOrchestrator(
        planner, coordinator, runner, VerificationEngine()
    )
    approvals = []
    retry_requests = []

    result = orchestrator.run(
        task,
        approval_handler=lambda plan: approvals.append(plan) or True,
        retry_handler=lambda request: retry_requests.append(request) or True,
    )

    assert result.status == TaskStatus.COMPLETED, result.errors
    assert result.succeeded
    assert result.attempt_count == 2
    assert [item.outcome for item in result.attempt_history] == ["failed", "completed"]
    assert result.attempt_history[0].retry_decision == "approved"
    assert len(approvals) == 2
    assert len(retry_requests) == 1
    assert retry_requests[0].phase == "verification"
    assert not (tmp_path / "RECOVERY FEEDBACK FROM A PREVIOUS ATTEMPT").exists()
    assert "RECOVERY FEEDBACK FROM A PREVIOUS ATTEMPT" in planner.contexts[1]
    assert "assertion failed" in planner.contexts[1]
    assert "STRUCTURED FAILURE DIAGNOSIS" in planner.contexts[1]
    assert "assertion_failure" in planner.contexts[1]
    assert "FOCUSED REPAIR STRATEGY" in planner.contexts[1]
    assert "Do not modify unrelated files" in planner.contexts[1]
    assert (tmp_path / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    import json
    events = [
        json.loads(line)
        for line in (tmp_path / ".coding-agent" / "tasks.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == [
        "attempt_started", "attempt_failed", "failure_diagnosed",
        "repair_strategy_generated", "retry_approved",
        "attempt_started", "task_completed",
    ]


def test_retry_declined_leaves_failed_attempt_rolled_back(tmp_path):
    task = make_task(tmp_path)
    plan = make_plan(task, changes=[
        FileChange("new.py", ChangeType.CREATE, "Create module", "VALUE = 1\n")
    ], commands=[])
    orchestrator, _, planner, _ = make_orchestrator(
        tmp_path,
        plan,
        results=[CommandResult(("python", "-c"), 1, stderr="failed")],
    )

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
        retry_handler=lambda request: False,
    )

    assert result.status == TaskStatus.BLOCKED
    assert result.attempt_count == 1
    assert result.attempt_history[0].retry_decision == "declined"
    assert not (tmp_path / "new.py").exists()
    assert len(planner.calls) == 1


def test_planning_failure_can_retry_with_error_context(tmp_path):
    task = make_task(tmp_path)
    plan = make_plan(task, changes=[
        FileChange("new.py", ChangeType.CREATE, "Create module", "VALUE = 1\n")
    ], commands=[])
    workspace = Workspace(tmp_path)
    planner = SequencePlanner([RuntimeError("Ollama returned malformed JSON"), plan])
    coordinator = ChangeCoordinator(workspace)
    runner = SequencedCommandRunner(workspace, [0])
    orchestrator = TaskOrchestrator(planner, coordinator, runner, VerificationEngine())

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
        retry_handler=lambda request: request.phase == "planning",
    )

    assert result.status == TaskStatus.COMPLETED, result.errors
    assert result.attempt_count == 2
    assert "Ollama returned malformed JSON" in planner.contexts[1]
    assert result.attempt_history[0].phase == "planning"


def test_timeout_is_not_automatically_retried(tmp_path):
    task = make_task(tmp_path)
    plan = make_plan(task, changes=[
        FileChange("new.py", ChangeType.CREATE, "Create module", "VALUE = 1\n")
    ], commands=[])
    orchestrator, _, planner, _ = make_orchestrator(
        tmp_path,
        plan,
        results=[CommandResult(("python", "-c"), None, timed_out=True)],
    )
    retry_calls = []

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
        retry_handler=lambda request: retry_calls.append(request) or True,
    )

    assert result.status == TaskStatus.FAILED
    assert retry_calls == []
    assert len(planner.calls) == 1
    assert not (tmp_path / "new.py").exists()


def test_retry_limit_is_enforced(tmp_path):
    from coding_agent.retry import RetryPolicy

    task = make_task(tmp_path)
    plans = [
        make_plan(task, changes=[
            FileChange("new.py", ChangeType.CREATE, "Create module", f"VALUE = {index}\n")
        ], commands=[])
        for index in (1, 2, 3)
    ]
    workspace = Workspace(tmp_path)
    planner = SequencePlanner(plans)
    coordinator = ChangeCoordinator(workspace)
    runner = SequencedCommandRunner(workspace, [1, 1, 1])
    orchestrator = TaskOrchestrator(
        planner, coordinator, runner, VerificationEngine(),
        retry_policy=RetryPolicy(max_attempts=2),
    )

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
        retry_handler=lambda request: True,
    )

    assert result.status == TaskStatus.FAILED
    assert result.attempt_count == 2
    assert len(planner.calls) == 2
    assert any("Retry limit reached" in note for note in result.notes)
    assert not (tmp_path / "new.py").exists()


def test_corrective_plan_requires_its_own_approval(tmp_path):
    task = make_task(tmp_path)
    plans = [
        make_plan(task, changes=[
            FileChange("module.py", ChangeType.CREATE, "Initial", "VALUE = 1\n")
        ], commands=[]),
        make_plan(task, changes=[
            FileChange("module.py", ChangeType.CREATE, "Correction", "VALUE = 2\n")
        ], commands=[]),
    ]
    workspace = Workspace(tmp_path)
    planner = SequencePlanner(plans)
    coordinator = ChangeCoordinator(workspace)
    runner = SequencedCommandRunner(workspace, [1])
    orchestrator = TaskOrchestrator(planner, coordinator, runner, VerificationEngine())
    approval_decisions = iter([True, False])

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: next(approval_decisions),
        retry_handler=lambda request: True,
    )

    assert result.status == TaskStatus.BLOCKED
    assert result.attempt_count == 2
    assert result.attempt_history[0].retry_decision == "approved"
    assert result.attempt_history[1].outcome == "blocked"
    assert not (tmp_path / "module.py").exists()


def test_task_can_create_file_under_missing_nested_directories(tmp_path):
    task = make_task(tmp_path)
    plan = make_plan(task, changes=[
        FileChange(
            path="src/modules/greeting.py",
            change_type=ChangeType.CREATE,
            reason="Create nested greeting module",
            content="def greet():\n    return 'Hello'\n",
        )
    ], commands=[])
    orchestrator, _, _, _ = make_orchestrator(tmp_path, plan)

    result = orchestrator.run(task, approval_handler=lambda proposed: True)

    assert result.status == TaskStatus.COMPLETED, result.errors
    assert (tmp_path / "src/modules/greeting.py").read_text(encoding="utf-8") == "def greet():\n    return 'Hello'\n"



def test_orchestrator_normalizes_create_for_existing_file_before_approval(tmp_path):
    existing = tmp_path / "App.jsx"
    existing.write_text("old", encoding="utf-8")
    task = make_task(tmp_path)
    plan = make_plan(
        task,
        [FileChange(
            path="App.jsx",
            change_type=ChangeType.CREATE,
            reason="Model misclassified existing app file",
            content="new",
        )],
    )
    planner = FakePlanner(plan)
    workspace = Workspace(tmp_path)
    coordinator = ChangeCoordinator(workspace, journal=ChangeJournal(tmp_path / ".coding-agent" / "changes.jsonl"))
    runner = FakeCommandRunner(workspace)
    orchestrator = TaskOrchestrator(
        planner=planner,
        coordinator=coordinator,
        command_runner=runner,
        verification_engine=VerificationEngine(),
    )
    approvals = []

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: approvals.append(proposed) or True,
    )

    assert result.status == TaskStatus.COMPLETED, result.errors
    assert approvals[0].changes[0].change_type == ChangeType.MODIFY
    assert len(planner.calls) == 1
    assert existing.read_text(encoding="utf-8") == "new"
    assert any("file content was preserved" in note for note in result.notes)


def test_orchestrator_normalizes_modify_for_missing_file_before_approval(tmp_path):
    task = make_task(tmp_path)
    plan = make_plan(
        task,
        [FileChange(
            path="new/nested.py",
            change_type=ChangeType.MODIFY,
            reason="Model misclassified new file",
            content="print('new')",
        )],
    )
    planner = FakePlanner(plan)
    workspace = Workspace(tmp_path)
    coordinator = ChangeCoordinator(workspace, journal=ChangeJournal(tmp_path / ".coding-agent" / "changes.jsonl"))
    runner = FakeCommandRunner(workspace)
    orchestrator = TaskOrchestrator(
        planner=planner,
        coordinator=coordinator,
        command_runner=runner,
        verification_engine=VerificationEngine(),
    )
    approvals = []

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: approvals.append(proposed) or True,
    )

    assert result.status == TaskStatus.COMPLETED, result.errors
    assert approvals[0].changes[0].change_type == ChangeType.CREATE
    assert len(planner.calls) == 1
    assert (tmp_path / "new" / "nested.py").read_text(encoding="utf-8") == "print('new')"
    assert any("file content was preserved" in note for note in result.notes)


def test_historical_context_is_bounded_and_excludes_recovery_payload(tmp_path):
    (tmp_path / ".coding-agent").mkdir()
    (tmp_path / ".coding-agent" / "tasks.jsonl").write_text(
        '{"task_id":"old-task","event":"task_completed","attempt":1,"details":{"plan_summary":"Updated dashboard"}}\n',
        encoding="utf-8",
    )
    (tmp_path / ".coding-agent" / "changes.jsonl").write_text(
        '{"change_id":"old-task-0-change","relative_path":"src/App.jsx","operation":"modify","event":"verified","state":"verified","recovery_bytes_b64":"SECRET_PAYLOAD"}\n',
        encoding="utf-8",
    )
    workspace = Workspace(tmp_path)
    coordinator = ChangeCoordinator(workspace, journal=ChangeJournal(tmp_path / ".coding-agent" / "changes.jsonl"))
    runner = FakeCommandRunner(workspace)
    orchestrator = TaskOrchestrator(
        planner=FakePlanner(),
        coordinator=coordinator,
        command_runner=runner,
        verification_engine=VerificationEngine(),
    )

    history = orchestrator._historical_context("old-task")

    assert "old-task" in history
    assert "Updated dashboard" in history
    assert "src/App.jsx" in history
    assert "SECRET_PAYLOAD" not in history


def test_historical_context_excludes_unrelated_task_and_change_history(tmp_path):
    journal_dir = tmp_path / ".coding-agent"
    journal_dir.mkdir()
    (journal_dir / "tasks.jsonl").write_text(
        '\n'.join([
            '{"task_id":"previous-phase","event":"failed","attempt":1,"details":{"errors":"manage.py syntax failure"}}',
            '{"task_id":"current-phase","event":"task_started","attempt":1,"details":{"summary":"Implement staff administration"}}',
        ]) + "\n",
        encoding="utf-8",
    )
    (journal_dir / "changes.jsonl").write_text(
        '\n'.join([
            '{"change_id":"previous-phase-0-change","relative_path":"backend/manage.py","operation":"modify","event":"rolled_back","state":"rolled_back"}',
            '{"change_id":"current-phase-0-change","relative_path":"staff/views.py","operation":"create","event":"verified","state":"verified"}',
        ]) + "\n",
        encoding="utf-8",
    )
    workspace = Workspace(tmp_path)
    orchestrator = TaskOrchestrator(
        planner=FakePlanner(),
        coordinator=ChangeCoordinator(workspace, journal=ChangeJournal(journal_dir / "changes.jsonl")),
        command_runner=FakeCommandRunner(workspace),
        verification_engine=VerificationEngine(),
    )

    history = orchestrator._historical_context("current-phase")

    assert "current-phase" in history
    assert "staff/views.py" in history
    assert "previous-phase" not in history
    assert "manage.py" not in history
    assert "\\n" not in history


def test_operation_normalization_preserves_semantically_valid_espec_content(tmp_path):
    from coding_agent.engineering_spec import (
        EngineeringSpecification, ImplementationUnit, Requirement,
        VerificationCriterion, VerificationKind,
    )

    task = make_task(tmp_path)
    (tmp_path / "README.md").write_text("Old README\\n", encoding="utf-8")
    content = (
        "# Project\\n\\n"
        "## Prerequisites\\nDocker is required.\\n\\n"
        "## Environment setup\\nCopy .env.example to .env.\\n\\n"
        "## Compose workflow\\nRun docker compose up.\\n"
    )
    plan = make_plan(task, changes=[
        FileChange(
            path="README.md",
            change_type=ChangeType.CREATE,
            reason="Initialize project README",
            content=content,
        )
    ], commands=[])
    orchestrator, _, planner, _ = make_orchestrator(tmp_path, plan)
    specification = EngineeringSpecification(
        specification_id="spec-readme-operation-normalization",
        task_id=task.task_id,
        objective="Initialize README",
        workspace=str(tmp_path),
        requirements=(
            Requirement(
                "REQ-README",
                "Create README.md documenting prerequisites, environment setup, and Compose workflow.",
                acceptance_criteria=(
                    "README documents prerequisites.",
                    "README documents environment setup.",
                    "README documents Compose workflow.",
                ),
            ),
        ),
        implementation_units=(
            ImplementationUnit(
                "UNIT-README",
                "Create project README",
                "README.md",
                satisfies=("REQ-README",),
            ),
        ),
        verification=(
            VerificationCriterion(
                "VER-README",
                "A reviewer confirms the README is understandable.",
                VerificationKind.MANUAL,
            ),
        ),
    )
    approved = []

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: approved.append(proposed) or True,
        specification=specification,
        manual_verification_handler=lambda criteria: {
            "VER-README": {
                "status": "PASS",
                "evidence": "Reviewed README headings and setup instructions.",
            }
        },
    )

    assert result.status == TaskStatus.COMPLETED, result.errors
    assert len(planner.calls) == 1
    assert approved[0].changes[0].change_type == ChangeType.MODIFY
    assert approved[0].changes[0].content == content
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == content


def test_delete_operation_is_never_normalized_for_missing_target(tmp_path):
    task = make_task(tmp_path)
    plan = make_plan(task, changes=[
        FileChange(
            path="missing.txt",
            change_type=ChangeType.DELETE,
            reason="Delete an explicitly named file",
            content=None,
        )
    ])
    orchestrator, _, planner, _ = make_orchestrator(tmp_path, plan)
    approval_calls = []

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: approval_calls.append(proposed) or True,
        retry_handler=lambda request: False,
    )

    assert result.status == TaskStatus.FAILED
    assert len(planner.calls) == 1
    assert approval_calls == []
    assert not (tmp_path / "missing.txt").exists()


def test_environment_command_failure_is_diagnosed_and_not_retried(tmp_path):
    task = make_task(tmp_path)
    plan = make_plan(task, changes=[
        FileChange("new.py", ChangeType.CREATE, "Create module", "VALUE = 1\n")
    ], commands=[])
    orchestrator, _, planner, _ = make_orchestrator(
        tmp_path,
        plan,
        results=[
            CommandResult(
                ("npm", "test"),
                1,
                stderr="'npm' is not recognized as an internal or external command",
            )
        ],
    )
    retry_requests = []

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: True,
        retry_handler=lambda request: retry_requests.append(request) or True,
    )

    assert result.status == TaskStatus.FAILED
    assert len(planner.calls) == 1
    assert retry_requests == []
    assert any("Repair not attempted" in note for note in result.notes)
    events = [
        json.loads(line)
        for line in (tmp_path / ".coding-agent" / "tasks.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]
    diagnosed = [event for event in events if event["event"] == "failure_diagnosed"]
    assert len(diagnosed) == 1
    assert diagnosed[0]["details"]["category"] == "environment_failure"
    assert diagnosed[0]["details"]["actionable"] is False


def test_specification_commands_are_approved_executed_and_journaled(tmp_path):
    from coding_agent.engineering_spec import (
        EngineeringSpecification, Requirement, VerificationCriterion, VerificationKind,
    )
    task = make_task(tmp_path)
    plan = make_plan(task, changes=[FileChange(
        path="greeting.py", change_type=ChangeType.CREATE,
        reason="Create greeting", content="def greet(): return 'hello'\n",
    )], commands=[])
    orchestrator, _, _, runner = make_orchestrator(tmp_path, plan)
    specification = EngineeringSpecification(
        specification_id="spec-task-001", task_id=task.task_id,
        objective="Create greeting", workspace=str(tmp_path),
        requirements=(Requirement("REQ-1", "Greeting is returned"),),
        verification=(VerificationCriterion(
            "VC-1", "Behavioral tests pass", VerificationKind.BEHAVIORAL,
            command=("python", "-m", "pytest", "-q"), covers=("REQ-1",),
        ),),
    )
    approved_plans = []
    result = orchestrator.run(
        task, approval_handler=lambda proposed: approved_plans.append(proposed) or True,
        specification=specification,
    )
    assert result.status == TaskStatus.COMPLETED, result.errors
    assert ("python", "-m", "pytest", "-q") in approved_plans[0].verification_commands
    assert any(call[0] == ("python", "-m", "pytest", "-q") for call in runner.calls)
    events = [json.loads(line) for line in orchestrator.task_journal.path.read_text(encoding="utf-8").splitlines()]
    assert any(item["event"] == "specification_verification_completed" for item in events)
    assert any("Specification verification passed" in note for note in result.notes)
    assert result.specification_report["passed"] is True
    assert result.specification_report["requirements"][0]["status"] == "PASS"


def test_required_manual_criterion_blocks_completion_and_rolls_back(tmp_path):
    from coding_agent.engineering_spec import (
        EngineeringSpecification, VerificationCriterion, VerificationKind,
    )
    task = make_task(tmp_path)
    target = tmp_path / "new.py"
    plan = make_plan(task, changes=[FileChange(
        path="new.py", change_type=ChangeType.CREATE,
        reason="Create module", content="VALUE = 1\n",
    )], commands=[])
    orchestrator, _, _, _ = make_orchestrator(tmp_path, plan)
    specification = EngineeringSpecification(
        specification_id="spec-manual", task_id=task.task_id,
        objective="Create module", workspace=str(tmp_path),
        verification=(VerificationCriterion(
            "VC-MANUAL", "Human review", VerificationKind.MANUAL,
        ),),
    )
    result = orchestrator.run(task, approval_handler=lambda proposed: True,
                              specification=specification)
    assert result.status == TaskStatus.FAILED
    assert not target.exists()
    assert any("UNVERIFIED" in error for error in result.errors)
    assert result.specification_report["criteria"][0]["status"] == "UNVERIFIED"


def test_espec_generation_failure_is_journaled_before_attempt_loop(tmp_path):
    class ESpecClient:
        def __init__(self):
            requirement = {"requirement_id": "REQ-001", "description": "Task form works",
                           "kind": "functional", "acceptance_criteria": ["Can submit"],
                           "priority": "high"}
            architecture = {"module_id": "MOD-001", "name": "Form",
                            "responsibility": "Render form", "depends_on": [],
                            "file_paths": ["src/Form.jsx"]}
            contract = {"contract_id": "CON-001", "owner": "MOD-001", "name": "Form",
                        "kind": "component", "inputs": [], "outputs": [], "invariants": []}
            bad_integration = {
                "implementation_units": [{
                    "unit_id": "UNIT-001", "title": "Build form",
                    "target_path": "src/Form.jsx", "kind": "source", "depends_on": [],
                    "satisfies": [], "implements": ["CON-001"], "acceptance_criteria": ["Renders"],
                }],
                "verification": [{
                    "criterion_id": "VER-001", "description": "Form test", "kind": "behavioral",
                    "required": True, "command": ["python", "-m", "pytest"], "covers": ["REQ-001"],
                }],
            }
            self.payloads = [
                {"requirements": [requirement]}, {"architecture": [architecture]},
                {"contracts": [contract]}, bad_integration, bad_integration,
            ]
        def generate_json(self, prompt):
            return self.payloads.pop(0)

    task = make_task(tmp_path)
    orchestrator, _, planner, runner = make_orchestrator(tmp_path, make_plan(task, changes=[], commands=[]))
    planner.client = ESpecClient()
    result = orchestrator.run(task, approval_handler=lambda proposed: True)
    assert result.status == TaskStatus.FAILED
    assert any("lack implementation-unit coverage" in e for e in result.errors)
    assert runner.calls == []
    events = [json.loads(line) for line in
              (tmp_path / ".coding-agent" / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["event"] for e in events] == ["engineering_specification_failed"]
    detail = events[0]["details"]
    assert detail["exception_type"] == "SpecificationGenerationError"
    assert detail["generation_trace"][-1]["stage"] == "integration"
    diagnostics = Path(detail["diagnostics_dir"])
    assert (diagnostics / "integration_attempt_1.json").is_file()
    assert (diagnostics / "integration_attempt_2.json").is_file()
    assert (diagnostics / "validation_errors.json").is_file()



def test_espec_plan_correction_targets_missing_units_and_exempts_unmapped_constraints(tmp_path):
    from coding_agent.engineering_spec import (
        ArchitectureModule, EngineeringSpecification, ImplementationUnit,
        Requirement, RequirementKind,
    )

    task = make_task(tmp_path)
    specification = EngineeringSpecification(
        specification_id="spec-plan-correction",
        task_id=task.task_id,
        objective="Create configuration and documentation",
        workspace=str(tmp_path),
        requirements=(
            Requirement("REQ-1", "Create the required configuration and documentation"),
            Requirement(
                "REQ-2", "Do not introduce SQLite",
                kind=RequirementKind.CONSTRAINT,
            ),
        ),
        architecture=(
            ArchitectureModule(
                "config", "Configuration", "Application configuration",
                file_paths=("config/app.yml",),
            ),
            ArchitectureModule(
                "docs", "Documentation", "Project documentation",
                file_paths=("README.md",),
            ),
        ),
        implementation_units=(
            ImplementationUnit(
                "UNIT-1", "Application configuration", "config/app.yml",
                satisfies=("REQ-1",),
            ),
            ImplementationUnit(
                "UNIT-2", "Project documentation", "README.md",
                satisfies=("REQ-1",),
            ),
        ),
    )
    incomplete = make_plan(task, changes=[
        FileChange(
            "config/app.yml", ChangeType.CREATE, "Create configuration", "setting: true\n"
        ),
    ], commands=[])
    complete = make_plan(task, changes=[
        FileChange(
            "config/app.yml", ChangeType.CREATE, "Create configuration", "setting: true\n"
        ),
        FileChange(
            "README.md", ChangeType.CREATE, "Create documentation", "# Project\n"
        ),
    ], commands=[])

    class SequencedPlanner:
        def __init__(self):
            self.plans = [incomplete, complete]
            self.contexts = []

        def plan(self, requested_task, context=""):
            self.contexts.append(context)
            return self.plans.pop(0)

    workspace = Workspace(tmp_path)
    planner = SequencedPlanner()
    runner = FakeCommandRunner(workspace)
    orchestrator = TaskOrchestrator(
        planner,
        ChangeCoordinator(workspace),
        runner,
        VerificationEngine(),
        max_planning_corrections=1,
    )

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: False,
        specification=specification,
    )

    assert result.status == TaskStatus.BLOCKED
    assert len(planner.contexts) == 2
    correction = planner.contexts[1]
    assert "UNIT-2 -> README.md" in correction
    assert "MISSING IMPLEMENTATION UNITS" in correction
    assert "constraint with no implementation-unit mapping is a compliance boundary" in correction
    assert "Every requirement must have at least one changed file" not in correction
    assert "one change per unique target_path" in correction
    assert "one changed file does not cover sibling units" not in correction
    assert "PREVIOUS DRAFT FILE CONTENTS" in correction
    assert '"path": "config/app.yml"' in correction
    assert "setting: true" in correction
    assert runner.calls == []
    assert not (tmp_path / "config/app.yml").exists()
    assert not (tmp_path / "README.md").exists()


def test_manual_verification_handler_records_human_evidence_and_completes(tmp_path):
    from coding_agent.engineering_spec import (
        EngineeringSpecification, Requirement, RequirementKind,
        VerificationCriterion, VerificationKind,
    )

    task = make_task(tmp_path)
    plan = make_plan(task, changes=[FileChange(
        "config.txt", ChangeType.CREATE, "Create config", "ready\n"
    )], commands=[])
    orchestrator, _, _, runner = make_orchestrator(tmp_path, plan)
    spec = EngineeringSpecification(
        "spec-manual", task.task_id, "Create config", str(tmp_path),
        requirements=(Requirement("REQ-M", "A reviewer confirms the configuration is usable.", RequirementKind.CONSTRAINT),),
        verification=(VerificationCriterion(
            "VER-M", "Human confirms the resulting configuration is usable.",
            VerificationKind.MANUAL, covers=("REQ-M",),
        ),),
    )
    seen = []
    def attest(criteria):
        seen.extend(criteria)
        return {"VER-M": {"status": "PASS", "evidence": "Inspected config.txt and confirmed expected content."}}

    result = orchestrator.run(task, approval_handler=lambda _: True,
                              specification=spec, manual_verification_handler=attest)
    assert result.status == TaskStatus.COMPLETED, result.errors
    assert len(seen) == 1
    assert result.specification_report["criteria"][0]["human_evidence"].startswith("Inspected config.txt")
