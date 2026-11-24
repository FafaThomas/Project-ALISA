from coding_agent.engineering_spec import (
    ArchitectureModule,
    EngineeringSpecification,
    ImplementationUnit,
    Requirement,
    RequirementKind,
    UnitKind,
    VerificationCriterion,
    VerificationKind,
)
from coding_agent.models import ChangeType, FileChange
from coding_agent.specification_check_planner import (
    SpecificationCheckPlanner,
    command_rejection,
    project_toolchain,
)
from coding_agent.verification_planner import VerificationPlanner


def _change(path, content="x = 1\n"):
    return FileChange(path, ChangeType.CREATE, "Create file", content)


def test_python_commands_are_always_allowed():
    allowed = project_toolchain([])

    assert command_rejection(("python", "-m", "pytest", "-q"), allowed) is None
    assert command_rejection(("python", "main.py", "2", "+", "3"), allowed) is None


def test_other_toolchains_need_their_project_marker():
    assert command_rejection(("npm", "test"), project_toolchain(["main.py"])) is not None
    assert command_rejection(("npm", "test"), project_toolchain(["frontend/package.json"])) is None
    assert command_rejection(("go", "test", "./..."), project_toolchain(["go.mod"])) is None
    assert command_rejection(("dotnet", "test"), project_toolchain(["App/App.csproj"])) is None


def test_joined_commands_are_rejected_for_any_toolchain():
    allowed = project_toolchain(["package.json"])

    assert "joins several commands" in command_rejection(
        ("python", "main.py", "2", "+", "3", "python", "main.py", "2", "*", "0"), allowed
    )
    assert "joins several commands" in command_rejection(("npm", "test", "npm", "run", "lint"), allowed)


def test_plan_suggested_junk_command_is_not_run(tmp_path):
    # Regression: the calculator plan suggested ("command", "description"), which
    # could not start and failed verification.
    commands = VerificationPlanner(tmp_path).build(
        [_change("main.py")],
        [("command", "description"), ("grep", "-E", "eval", "main.py"), ("python", "main.py", "2", "+", "3")],
    )

    programs = [command[0] for command in commands]
    assert "command" not in programs
    assert "grep" not in programs
    assert ("python", "main.py", "2", "+", "3") in commands


def test_plan_suggested_npm_command_runs_in_a_node_project(tmp_path):
    commands = VerificationPlanner(tmp_path).build(
        [_change("package.json", '{"name": "app"}\n'), _change("src/index.js", "export {}\n")],
        [("npm", "test")],
    )

    assert ("npm", "test") in commands


def _spec_with_readability(tmp_path, *, with_gate):
    verification = [
        VerificationCriterion(
            "VER-002", "Verify code structure meets readability and maintainability standards for beginners",
            VerificationKind.MANUAL, covers=("REQ-004",),
        ),
    ]
    if with_gate:
        verification.append(VerificationCriterion(
            "VER-001", "Run the tests", VerificationKind.BEHAVIORAL,
            command=("python", "-m", "pytest", "-q"), covers=("REQ-001",),
        ))
    return EngineeringSpecification(
        "spec-readability", "task-readability", "Build a calculator", str(tmp_path),
        requirements=(
            Requirement("REQ-001", "Add two numbers", acceptance_criteria=("add(2, 3) returns 5",)),
            Requirement("REQ-004", "Keep the code beginner-readable", RequirementKind.NON_FUNCTIONAL,
                        acceptance_criteria=("Code is easy for beginners to follow",)),
        ),
        architecture=(ArchitectureModule("MOD-1", "Calc", "Calculator", file_paths=("calculator.py", "tests/test_calc.py")),),
        implementation_units=(
            ImplementationUnit("UNIT-1", "Calculator", "calculator.py", satisfies=("REQ-001", "REQ-004")),
            ImplementationUnit("UNIT-2", "Tests", "tests/test_calc.py", kind=UnitKind.TEST, satisfies=("REQ-001",)),
        ),
        verification=tuple(verification),
    )


def test_readability_review_is_non_blocking_when_tests_decide(tmp_path):
    prepared, notes = SpecificationCheckPlanner(tmp_path).prepare(_spec_with_readability(tmp_path, with_gate=True))
    review = next(item for item in prepared.verification if item.criterion_id == "VER-002")

    assert review.kind == VerificationKind.MANUAL
    assert review.required is False
    assert any("judgment review of non-functional requirements is non-blocking" in note for note in notes)


def test_readability_review_stays_required_when_it_is_the_only_gate(tmp_path):
    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(_spec_with_readability(tmp_path, with_gate=False))

    assert prepared.verification[0].required is True
