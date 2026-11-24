import sys

from coding_agent.command_runner import CommandRunner
from coding_agent.coordinator import ChangeCoordinator
from coding_agent.engineering_spec import (
    ArchitectureModule,
    EngineeringSpecification,
    ImplementationUnit,
    Requirement,
)
from coding_agent.models import ChangeType, CodingTask, FileChange, TaskStatus
from coding_agent.orchestrator import TaskOrchestrator
from coding_agent.planner import (
    CodingPlanner,
    import_time_argument_issue,
    import_time_argument_read,
)
from coding_agent.verification import VerificationEngine
from coding_agent.workspace import Workspace


def _change(path, content):
    return FileChange(path, ChangeType.CREATE, "Create file", content)


def test_detects_argv_read_at_module_level():
    # Regression: the calculator's main.py ran float(sys.argv[1]) on import.
    source = "import sys\nfrom calculator import add\n\nnum1 = float(sys.argv[1])\n"

    assert import_time_argument_read(source) == 4


def test_argv_inside_main_guard_or_function_is_fine():
    guarded = "import sys\n\nif __name__ == '__main__':\n    print(sys.argv[1])\n"
    in_function = "import sys\n\ndef main(argv=None):\n    return (argv or sys.argv)[1:]\n"

    assert import_time_argument_read(guarded) is None
    assert import_time_argument_read(in_function) is None


def test_detects_from_sys_import_argv_and_module_level_parse_args():
    assert import_time_argument_read("from sys import argv\nfirst = argv[1]\n") == 2
    assert import_time_argument_read(
        "import argparse\nparser = argparse.ArgumentParser()\nargs = parser.parse_args()\n"
    ) == 3


def test_plan_is_rejected_when_a_test_imports_a_module_that_reads_argv_on_import():
    changes = (
        _change("main.py", "import sys\nnum1 = float(sys.argv[1])\n"),
        _change("tests/test_req_004.py", "import main\n\ndef test_x():\n    assert main\n"),
    )

    issue = import_time_argument_issue(changes)

    assert issue is not None
    assert "'main.py' reads command-line arguments at import time (line 2)" in issue
    assert "tests/test_req_004.py imports it" in issue


def test_module_run_only_as_a_subprocess_is_not_flagged():
    changes = (
        _change("main.py", "import sys\nprint(sys.argv[1])\n"),
        _change("tests/test_cli.py",
                "import subprocess\nimport sys\n\ndef test_cli():\n"
                "    subprocess.run([sys.executable, 'main.py', '2'])\n"),
    )

    assert import_time_argument_issue(changes) is None


def test_verification_commands_leave_no_pytest_cache(tmp_path):
    # Regression: a model-proposed pytest command left .pytest_cache in the benchmark workspace.
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    command = (sys.executable, "-m", "pytest", "-q")

    result = CommandRunner(Workspace(tmp_path)).run(command, approved_commands=[command])

    assert result.exit_code == 0
    assert not (tmp_path / ".pytest_cache").exists()


class _ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1
        return self.responses.pop(0)


def _plan_response(content):
    return {
        "summary": "Create the tool",
        "changes": [{"path": "tool.py", "change_type": "create", "reason": "Create tool", "content": content}],
        "verification_commands": [],
        "requires_approval": True,
        "risks": [],
    }


def test_real_planner_content_error_gets_the_bounded_correction(tmp_path):
    # Regression: the real planner wraps content errors in PlanningError, which the
    # orchestrator's correction path (catching ValueError) never saw.
    task = CodingTask(
        task_id="task-correction", description="Create a tool", workspace=str(tmp_path),
        constraints=(), acceptance_criteria=(),
    )
    specification = EngineeringSpecification(
        "spec-correction", task.task_id, "Create a tool", str(tmp_path),
        requirements=(Requirement("REQ-1", "Create the tool."),),
        architecture=(ArchitectureModule("tool", "Tool", "The tool", file_paths=("tool.py",)),),
        implementation_units=(ImplementationUnit("UNIT-1", "Tool", "tool.py", satisfies=("REQ-1",)),),
    )
    client = _ScriptedClient([
        _plan_response("print(undefined_thing)\n"),
        _plan_response("VALUE = 1\n"),
    ])
    workspace = Workspace(tmp_path)
    orchestrator = TaskOrchestrator(
        CodingPlanner(client), ChangeCoordinator(workspace), CommandRunner(workspace), VerificationEngine(),
        max_planning_corrections=1,
    )

    result = orchestrator.run(task, approval_handler=lambda proposed: False, specification=specification)

    # The second, corrected plan reached the approval step (declined here), so
    # the content error did not fail the attempt.
    assert client.calls == 2
    assert result.status == TaskStatus.BLOCKED
