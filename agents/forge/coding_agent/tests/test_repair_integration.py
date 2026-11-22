from coding_agent.command_runner import CommandRunner
from coding_agent.coordinator import ChangeCoordinator
from coding_agent.models import (
    ChangeType,
    CodingTask,
    FileChange,
    ImplementationPlan,
    TaskStatus,
)
from coding_agent.orchestrator import TaskOrchestrator
from coding_agent.verification import VerificationEngine
from coding_agent.workspace import Workspace


class TwoStageRepairPlanner:
    def __init__(self, task_id):
        self.task_id = task_id
        self.calls = 0
        self.contexts = []

    def plan(self, task, context=""):
        self.calls += 1
        self.contexts.append(context)
        content = "def answer():\n    return 1\n"
        summary = "Initial implementation with incorrect result"
        if self.calls == 2:
            content = "def answer():\n    return 42\n"
            summary = "Corrected implementation after assertion failure"
        return ImplementationPlan(
            task_id=self.task_id,
            summary=summary,
            changes=(
                FileChange(
                    path="app.py",
                    change_type=ChangeType.CREATE,
                    reason="Implement answer function",
                    content=content,
                ),
            ),
            verification_commands=(),
            requires_approval=True,
        )


def test_end_to_end_repair_recovers_from_real_pytest_assertion_failure(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text(
        "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
        encoding="utf-8",
    )
    task = CodingTask(
        task_id="a6-real-repair",
        description="Implement answer() so it returns 42.",
        workspace=str(tmp_path),
        acceptance_criteria=("answer() returns 42",),
    )
    workspace = Workspace(tmp_path)
    planner = TwoStageRepairPlanner(task.task_id)
    orchestrator = TaskOrchestrator(
        planner=planner,
        coordinator=ChangeCoordinator(workspace),
        command_runner=CommandRunner(workspace, timeout=30),
        verification_engine=VerificationEngine(),
    )
    approval_calls = []
    retry_calls = []

    result = orchestrator.run(
        task,
        approval_handler=lambda plan: approval_calls.append(plan.summary) or True,
        retry_handler=lambda request: retry_calls.append(request) or True,
    )

    assert result.status == TaskStatus.COMPLETED, result.errors
    assert result.attempt_count == 2
    assert planner.calls == 2
    assert len(approval_calls) == 2
    assert len(retry_calls) == 1
    assert retry_calls[0].diagnosis is not None
    assert retry_calls[0].diagnosis.category.value == "assertion_failure"
    assert retry_calls[0].repair_strategy is not None
    assert "FOCUSED REPAIR STRATEGY" in planner.contexts[1]
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "def answer():\n    return 42\n"
