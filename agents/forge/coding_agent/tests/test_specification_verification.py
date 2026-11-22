import pytest

from coding_agent.engineering_spec import (
    EngineeringSpecification,
    ImplementationUnit,
    Requirement,
    RequirementKind,
    VerificationCriterion,
    VerificationKind,
)
from coding_agent.models import CommandResult
from coding_agent.specification_verification import (
    SpecificationVerificationEngine,
    SpecificationVerificationError,
    VerificationStatus,
)


@pytest.fixture
def specification(tmp_path):
    return EngineeringSpecification(
        specification_id="spec-1",
        task_id="task-1",
        objective="Provide a working greeting function",
        workspace=str(tmp_path),
        requirements=(Requirement("REQ-1", "The greeting is returned", RequirementKind.FUNCTIONAL),),
        implementation_units=(ImplementationUnit(
            "UNIT-1", "Greeting implementation", "greeting.py", satisfies=("REQ-1",)
        ),),
        verification=(
            VerificationCriterion(
                "VC-1", "Greeting behavior passes", VerificationKind.BEHAVIORAL,
                command=("python", "-m", "pytest", "-q"), covers=("REQ-1", "UNIT-1"),
            ),
            VerificationCriterion(
                "VC-2", "Human confirms usability", VerificationKind.MANUAL,
                covers=("REQ-1",),
            ),
        ),
    )


def test_required_commands_excludes_manual_and_deduplicates(specification):
    assert SpecificationVerificationEngine.required_commands(specification) == (
        ("python", "-m", "pytest", "-q"),
    )


def test_successful_behavioral_evidence_does_not_pass_manual_criterion(specification):
    report = SpecificationVerificationEngine().evaluate(specification, [
        CommandResult(("python", "-m", "pytest", "-q"), 0, stdout="1 passed"),
    ])

    assert report.passed is False
    assert report.criteria[0].status == VerificationStatus.PASS
    assert report.criteria[1].status == VerificationStatus.UNVERIFIED
    assert report.requirements[0].status == VerificationStatus.UNVERIFIED
    assert report.implementation_units[0].status == VerificationStatus.PASS
    assert report.to_dict()["criteria"][0]["evidence"]["stdout"] == "1 passed"


def test_failed_command_fails_criterion_and_covered_requirement(specification):
    report = SpecificationVerificationEngine().evaluate(specification, [
        CommandResult(("python", "-m", "pytest", "-q"), 1, stdout="1 failed"),
    ])
    assert report.criteria[0].status == VerificationStatus.FAIL
    assert report.requirements[0].status == VerificationStatus.FAIL
    assert report.passed is False


def test_missing_command_evidence_is_unverified(specification):
    report = SpecificationVerificationEngine().evaluate(specification, [])
    assert report.criteria[0].status == VerificationStatus.UNVERIFIED
    assert report.requirements[0].status == VerificationStatus.UNVERIFIED


def test_optional_failed_criterion_does_not_block_acceptance(tmp_path):
    spec = EngineeringSpecification(
        "spec-2", "task-2", "Objective", str(tmp_path),
        verification=(VerificationCriterion(
            "VC-optional", "Optional check", VerificationKind.RUNTIME,
            required=False, command=("python", "optional_check.py"),
        ),),
    )
    report = SpecificationVerificationEngine().evaluate(spec, [
        CommandResult(("python", "optional_check.py"), 2),
    ])
    assert report.passed is False  # No required criteria means acceptance is not established.


def test_invalid_result_type_is_rejected(specification):
    with pytest.raises(SpecificationVerificationError):
        SpecificationVerificationEngine().evaluate(specification, [object()])


def test_manual_criterion_passes_only_with_explicit_decision_and_evidence(specification):
    report = SpecificationVerificationEngine().evaluate(
        specification,
        [CommandResult(("python", "-m", "pytest", "-q"), 0, stdout="1 passed")],
        manual_evidence={"VC-2": {"status": "PASS", "evidence": "Reviewed the UI and confirmed the greeting is usable."}},
    )
    assert report.criteria[1].status == VerificationStatus.PASS
    assert report.criteria[1].human_evidence.startswith("Reviewed the UI")
    assert report.passed is True


def test_manual_failure_evidence_fails_the_covered_requirement(specification):
    report = SpecificationVerificationEngine().evaluate(
        specification,
        [CommandResult(("python", "-m", "pytest", "-q"), 0)],
        manual_evidence={"VC-2": {"status": "FAIL", "evidence": "The page is not usable."}},
    )
    assert report.criteria[1].status == VerificationStatus.FAIL
    assert report.requirements[0].status == VerificationStatus.FAIL


def test_manual_evidence_without_decision_or_detail_is_not_accepted(specification):
    report = SpecificationVerificationEngine().evaluate(
        specification,
        [CommandResult(("python", "-m", "pytest", "-q"), 0)],
        manual_evidence={"VC-2": {"status": "PASS", "evidence": "  "}},
    )
    assert report.criteria[1].status == VerificationStatus.UNVERIFIED


def test_manual_evidence_rejects_unknown_or_non_manual_criterion_ids(specification):
    with pytest.raises(SpecificationVerificationError, match="unknown or non-manual"):
        SpecificationVerificationEngine().evaluate(
            specification, [],
            manual_evidence={"VC-1": {"status": "PASS", "evidence": "Not a manual criterion."}},
        )
