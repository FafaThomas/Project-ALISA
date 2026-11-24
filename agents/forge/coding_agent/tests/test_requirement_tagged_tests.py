import subprocess
import sys

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
from coding_agent.specification_check_planner import SpecificationCheckPlanner, requirement_marker


def _spec(tmp_path, verification, *, with_tests=True):
    units = [ImplementationUnit("UNIT-002", "Game logic", "game.py", satisfies=("REQ-002",))]
    paths = ["game.py"]
    if with_tests:
        units.append(ImplementationUnit("UNIT-006", "Tests", "tests/test_game.py",
                                        kind=UnitKind.TEST, satisfies=("REQ-006",)))
        paths.append("tests/test_game.py")
    return EngineeringSpecification(
        "spec-tags", "task-tags", "Build a game", str(tmp_path),
        requirements=(
            Requirement("REQ-002", "Eating food increases score",
                        acceptance_criteria=("Score increases after eating food",)),
            Requirement("REQ-006", "Create automated tests for core mechanics",
                        acceptance_criteria=("Tests run without a window",)),
            Requirement("REQ-008", "Use Windows-compatible verification commands",
                        RequirementKind.CONSTRAINT,
                        acceptance_criteria=("Commands run on Windows",)),
            Requirement("REQ-009", "Create README.md with project description and usage instructions",
                        acceptance_criteria=("README.md explains how to run the program",)),
        ),
        architecture=(ArchitectureModule("MOD-1", "Game", "Game and tests", file_paths=tuple(paths)),),
        implementation_units=tuple(units),
        verification=tuple(verification),
    )


def test_marker_name_is_lowercase_with_underscores():
    assert requirement_marker("REQ-002") == "req_002"
    assert requirement_marker("REQ-AUTH.1") == "req_auth_1"


def test_commandless_behavioral_criterion_runs_requirement_tagged_tests(tmp_path):
    spec = _spec(tmp_path, [VerificationCriterion(
        "VER-002", "Eating food increases the score", VerificationKind.BEHAVIORAL, covers=("REQ-002",),
    )])

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    criterion = next(item for item in prepared.verification if item.criterion_id == "VER-002")

    assert criterion.kind == VerificationKind.BEHAVIORAL
    assert criterion.command[:3] == ("python", "-m", "pytest")
    marker_index = criterion.command.index("-m", 3)
    assert criterion.command[marker_index + 1] == "req_002"
    assert criterion.command[-1] == "tests/test_game.py"
    assert criterion.required is True


def test_without_python_tests_a_behavioral_criterion_is_not_given_a_test_command(tmp_path):
    spec = _spec(tmp_path, [VerificationCriterion(
        "VER-002", "Eating food increases the score", VerificationKind.BEHAVIORAL, covers=("REQ-002",),
    )], with_tests=False)

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    criterion = prepared.verification[0]

    assert "pytest" not in criterion.command


def test_constraint_review_is_non_blocking_when_an_executable_check_decides(tmp_path):
    spec = _spec(tmp_path, [
        VerificationCriterion("VER-002", "Eating food increases the score",
                              VerificationKind.BEHAVIORAL, covers=("REQ-002",)),
        VerificationCriterion("VER-008", "Commands are Windows compatible",
                              VerificationKind.MANUAL, covers=("REQ-008",)),
    ])

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    review = next(item for item in prepared.verification if item.criterion_id == "VER-008")

    assert review.required is False


def test_constraint_review_stays_required_when_it_is_the_only_gate(tmp_path):
    spec = _spec(tmp_path, [
        VerificationCriterion("VER-008", "Commands are Windows compatible",
                              VerificationKind.MANUAL, covers=("REQ-008",)),
    ])

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)

    assert prepared.verification[0].required is True


def test_pytest_marker_selection_passes_tagged_tests_and_fails_untagged_requirements(tmp_path):
    # Confirms the mechanism the verifier relies on: -m selects tagged tests,
    # and a requirement with no tagged test collects nothing (exit code 5).
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_game.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.req_002\n"
        "def test_score_increases():\n"
        "    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )

    def run(marker):
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", marker, "tests/test_game.py"],
            cwd=tmp_path, capture_output=True, text=True,
        ).returncode

    assert run("req_002") == 0
    assert run("req_003") == 5


def test_gap_fill_checklist_uses_tagged_tests_when_no_artifact_check_applies(tmp_path):
    spec = _spec(tmp_path, [
        VerificationCriterion("VER-AUTO-001", "Manually verify REQ-002: Eating food increases score",
                              VerificationKind.MANUAL, covers=("REQ-002",)),
    ])

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    criterion = prepared.verification[0]

    assert criterion.kind == VerificationKind.BEHAVIORAL
    assert "req_002" in criterion.command


def test_model_written_manual_criterion_is_left_for_a_human(tmp_path):
    spec = _spec(tmp_path, [
        VerificationCriterion("VER-001", "A person confirms the score display is readable",
                              VerificationKind.MANUAL, covers=("REQ-002",)),
    ])

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)

    assert prepared.verification[0].kind == VerificationKind.MANUAL
    assert prepared.verification[0].command == ()


def test_model_written_manual_behavior_criterion_is_checked_by_tagged_tests(tmp_path):
    # A program-output criterion is checkable, so it must not block as manual.
    spec = _spec(tmp_path, [
        VerificationCriterion("VER-001", "Running python main.py 2 + 3 prints 5",
                              VerificationKind.MANUAL, covers=("REQ-002",)),
    ])

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    criterion = prepared.verification[0]

    assert criterion.kind == VerificationKind.BEHAVIORAL
    assert "req_002" in criterion.command


def test_error_message_criterion_with_clear_wording_is_still_checkable(tmp_path):
    spec = _spec(tmp_path, [
        VerificationCriterion("VER-002", "Dividing by zero raises a ValueError with a clear message",
                              VerificationKind.MANUAL, covers=("REQ-002",)),
    ])

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)

    assert prepared.verification[0].kind == VerificationKind.BEHAVIORAL


def test_model_proposed_non_python_command_is_dropped(tmp_path):
    # grep and pydoc are not the project's toolchain and were never checked.
    spec = _spec(tmp_path, [
        VerificationCriterion("VER-004", "Ensure no eval/exec usage", VerificationKind.STATIC,
                              command=("grep", "-E", "'eval|exec'", "game.py"), covers=("REQ-002",)),
    ])

    prepared, notes = SpecificationCheckPlanner(tmp_path).prepare(spec)

    assert all(not item.command or item.command[0] != "grep" for item in prepared.verification)
    assert any("dropped proposed command 'grep'" in note for note in notes)


def test_model_proposed_python_command_is_kept(tmp_path):
    spec = _spec(tmp_path, [
        VerificationCriterion("VER-002", "Running python main.py 2 + 3 runs",
                              VerificationKind.BEHAVIORAL, command=("python", "main.py", "2", "+", "3"),
                              covers=("REQ-002",)),
    ])

    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)

    assert prepared.verification[0].command == ("python", "main.py", "2", "+", "3")


def test_readme_requirement_is_an_artifact_check_not_a_tagged_test(tmp_path):
    # Regression: a README gap-fill checklist was sent to pytest and failed with exit 5.
    spec = _spec(tmp_path, [
        VerificationCriterion("VER-AUTO-001", "Manually verify REQ-009: Create README.md with usage",
                              VerificationKind.MANUAL, covers=("REQ-009",)),
    ])
    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    criterion = prepared.verification[0]

    assert "coding_agent.specification_checks" in criterion.command
    assert not any(part.startswith("req_") for part in criterion.command)
