import json

from coding_agent.engineering_spec import (
    ArchitectureModule,
    EngineeringSpecification,
    ImplementationUnit,
    Requirement,
    UnitKind,
    VerificationCriterion,
    VerificationKind,
)
from coding_agent.models import ChangeType, FileChange
from coding_agent.specification_check_planner import (
    SpecificationCheckPlanner,
    command_rejection,
    project_python_modules,
    project_toolchain,
)
from coding_agent.specification_checks import run_checks
from coding_agent.verification_planner import VerificationPlanner


def test_python_dash_m_is_limited_to_runners_and_project_modules():
    allowed = project_toolchain([])
    modules = {"tests", "tests.test_calc", "calculator"}

    assert command_rejection(("python", "-m", "pytest", "-q"), allowed, modules) is None
    assert command_rejection(("python", "-m", "unittest", "discover"), allowed, modules) is None
    assert command_rejection(("python", "-m", "py_compile", "main.py"), allowed, modules) is None
    assert command_rejection(("python", "-m", "tests.test_calc"), allowed, modules) is None
    assert command_rejection(("python", "-m", "coding_agent.specification_checks", "."), allowed, modules) is None
    # Regression: the calculator spec ran "python -m flake8 ..." and flake8 is not installed.
    assert "module 'flake8'" in command_rejection(
        ("python", "-m", "flake8", "--select=F4", "main.py"), allowed, modules
    )


def test_project_python_modules_from_paths():
    modules = project_python_modules(["calculator.py", "tests/test_calc.py", "pkg/__init__.py", "README.md"])

    assert modules == {"calculator", "tests", "tests.test_calc", "pkg"}


def test_plan_suggested_python_module_tool_is_not_run(tmp_path):
    commands = VerificationPlanner(tmp_path).build(
        [FileChange("main.py", ChangeType.CREATE, "Create", "x = 1\n")],
        [("python", "-m", "flake8", "main.py"), ("python", "-m", "unittest", "discover")],
    )

    assert ("python", "-m", "flake8", "main.py") not in commands
    assert ("python", "-m", "unittest", "discover") in commands


def _separation_spec(tmp_path, verification, *, req1_description="Implement calculator.py with add"):
    return EngineeringSpecification(
        "spec-separation", "task-separation", "Build a calculator", str(tmp_path),
        requirements=(
            Requirement("REQ-001", req1_description, acceptance_criteria=("add(2, 3) returns 5",)),
            Requirement("REQ-005", "Separate calculation logic, command-line handling, and automated tests",
                        acceptance_criteria=("Each concern lives in its own file",)),
        ),
        architecture=(ArchitectureModule("MOD-1", "Calc", "Calculator",
                                         file_paths=("calculator.py", "main.py", "tests/test_calc.py")),),
        implementation_units=(
            ImplementationUnit("UNIT-1", "Calculator", "calculator.py", satisfies=("REQ-001", "REQ-005")),
            ImplementationUnit("UNIT-2", "CLI", "main.py", satisfies=("REQ-005",)),
            ImplementationUnit("UNIT-3", "Tests", "tests/test_calc.py", kind=UnitKind.TEST, satisfies=("REQ-001",)),
        ),
        verification=tuple(verification),
    )


def test_separation_requirement_is_checked_by_file_layout_not_tagged_tests(tmp_path):
    # Regression: "Separate calculation logic, ..." was sent to pytest -m req_005 and exited 5.
    spec = _separation_spec(tmp_path, [
        VerificationCriterion("VER-AUTO-004", "Manually verify REQ-005: Separate calculation logic",
                              VerificationKind.MANUAL, covers=("REQ-005",)),
    ])

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    criterion = prepared.verification[0]

    assert "coding_agent.specification_checks" in criterion.command
    assert not any(part.startswith("req_") for part in criterion.command)
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("import calculator\n", encoding="utf-8")
    assert run_checks(tmp_path, json.loads(criterion.command[-1]))


def test_single_file_requirement_mentioning_separation_keeps_its_behavior_tests(tmp_path):
    # "separate" in a one-file requirement is not a layout requirement.
    spec = _separation_spec(
        tmp_path,
        [VerificationCriterion("VER-001", "Adding works", VerificationKind.BEHAVIORAL, covers=("REQ-001",))],
        req1_description="Keep add separate from printing and return the sum",
    )

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)

    assert "req_001" in prepared.verification[0].command
