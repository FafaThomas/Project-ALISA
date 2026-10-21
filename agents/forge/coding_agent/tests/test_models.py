import pytest

from src.coding_agent.models import (
    ChangeType,
    CodingTask,
    CommandResult,
    ExecutionResult,
    FileChange,
    ImplementationPlan,
    TaskStatus,
)


def test_coding_task_requires_description():
    with pytest.raises(ValueError, match="description"):
        CodingTask(task_id="task-1", description=" ", workspace=".")


def test_file_change_rejects_parent_traversal():
    with pytest.raises(ValueError, match="traverse"):
        FileChange(
            path="../outside.py",
            change_type=ChangeType.CREATE,
            reason="Invalid path",
            content="print('unsafe')",
        )


def test_file_change_requires_content_for_create():
    with pytest.raises(ValueError, match="require content"):
        FileChange(
            path="src/example.py",
            change_type=ChangeType.CREATE,
            reason="Create module",
        )


def test_delete_does_not_accept_content():
    with pytest.raises(ValueError, match="must not include"):
        FileChange(
            path="src/example.py",
            change_type=ChangeType.DELETE,
            reason="Remove obsolete module",
            content="old content",
        )


def test_plan_rejects_duplicate_paths():
    change = FileChange(
        path="src/example.py",
        change_type=ChangeType.CREATE,
        reason="Create module",
        content="VALUE = 1",
    )
    with pytest.raises(ValueError, match="same path"):
        ImplementationPlan(
            task_id="task-1",
            summary="Duplicate change",
            changes=(change, change),
        )


def test_plan_requires_approval_by_default():
    plan = ImplementationPlan(
        task_id="task-1",
        summary="Add a module",
        changes=(),
    )
    assert plan.requires_approval is True


def test_command_result_detects_failure_and_timeout():
    assert CommandResult(("pytest",), 0).succeeded
    assert not CommandResult(("pytest",), 1).succeeded
    assert not CommandResult(("pytest",), 0, timed_out=True).succeeded


def test_execution_requires_verification_results():
    result = ExecutionResult(
        task_id="task-1",
        status=TaskStatus.COMPLETED,
    )
    assert not result.verification_passed
    assert not result.succeeded


def test_execution_succeeds_only_with_passing_verification():
    result = ExecutionResult(
        task_id="task-1",
        status=TaskStatus.COMPLETED,
        changed_files=("src/example.py",),
        command_results=(CommandResult(("pytest",), 0),),
    )
    assert result.verification_passed
    assert result.succeeded


def test_failed_verification_prevents_success():
    result = ExecutionResult(
        task_id="task-1",
        status=TaskStatus.COMPLETED,
        command_results=(CommandResult(("pytest",), 1, stderr="test failed"),),
    )
    assert not result.verification_passed
    assert not result.succeeded
