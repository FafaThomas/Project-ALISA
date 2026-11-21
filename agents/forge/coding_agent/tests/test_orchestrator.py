
from pathlib import Path

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

    def plan(self, task, context=""):
        self.calls.append(task)

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
            return self.results.pop(0)

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

    assert result.status == TaskStatus.COMPLETED
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


def test_plan_without_verification_commands_is_rejected(tmp_path):
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
    approval_calls = []

    result = orchestrator.run(
        task,
        approval_handler=lambda proposed: approval_calls.append(proposed),
    )

    assert result.status == TaskStatus.FAILED
    assert approval_calls == []
    assert runner.calls == []
    assert not (tmp_path / "new.py").exists()


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
