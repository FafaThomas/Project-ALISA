import json

from coding_agent.engineering_spec import (
    EngineeringSpecification, ImplementationUnit, Requirement,
    SpecificationStatus, VerificationCriterion, VerificationKind,
)
from coding_agent.verification_delegation import (
    build_infrastructure_handoff,
    classify_infrastructure_verification_criteria,
    write_infrastructure_handoff,
)


def _spec(tmp_path):
    requirement = Requirement(
        "REQ-INFRA", "Run PostgreSQL in Docker with persistent storage",
        acceptance_criteria=("The PostgreSQL container becomes healthy.",),
    )
    acceptance_id = requirement.acceptance_criterion_ids[0]
    unit = ImplementationUnit(
        "UNIT-INFRA", "Configure container", "docker-compose.yml",
        satisfies=(requirement.requirement_id,),
        implements_acceptance_criteria=(acceptance_id,),
    )
    criteria = (
        VerificationCriterion(
            "VER-DOCKER", "Verify Docker Compose services and health checks",
            VerificationKind.RUNTIME, command=("docker", "compose", "ps"),
            covers=(requirement.requirement_id, unit.unit_id),
            covers_acceptance_criteria=(acceptance_id,),
        ),
        VerificationCriterion(
            "VER-MANUAL", "Manually inspect docker-compose.yml and .dockerignore files",
            VerificationKind.MANUAL, covers=(requirement.requirement_id,),
        ),
        VerificationCriterion(
            "VER-APP", "Unit tests pass for the Python business logic",
            VerificationKind.BEHAVIORAL, command=("python", "-m", "pytest", "-q"),
        ),
        VerificationCriterion(
            "VER-STATIC", "Static analysis confirms the Compose YAML has required keys",
            VerificationKind.STATIC, command=("python", "check_compose_yaml.py"),
        ),
    )
    return EngineeringSpecification(
        "spec-infra", "task-infra-01", "Set up infrastructure", str(tmp_path),
        requirements=(requirement,), implementation_units=(unit,),
        verification=criteria, status=SpecificationStatus.VALIDATED,
    )


def test_classifier_delegates_infrastructure_but_keeps_application_tests_local(tmp_path):
    spec = _spec(tmp_path)
    assert classify_infrastructure_verification_criteria(spec) == (
        "VER-DOCKER", "VER-MANUAL", "VER-STATIC",
    )


def test_handoff_contains_evidence_contract_and_is_written_atomically(tmp_path):
    spec = _spec(tmp_path)
    payload = build_infrastructure_handoff(spec, ("VER-DOCKER", "VER-MANUAL"))

    assert payload["assigned_agent"] == "infrastructure_verification_agent"
    assert payload["status"] == "PENDING"
    assert payload["policy"]["must_not_assume_pass"] is True
    assert payload["result_contract"]["allowed_statuses"] == [
        "PASS", "FAIL", "BLOCKED", "NOT_VERIFIED",
    ]
    assert [item["criterion_id"] for item in payload["criteria"]] == [
        "VER-DOCKER", "VER-MANUAL",
    ]
    assert payload["criteria"][0]["implementation_units"][0]["target_path"] == "docker-compose.yml"

    path = write_infrastructure_handoff(tmp_path, spec, ("VER-DOCKER", "VER-MANUAL"))
    assert path == tmp_path / ".coding-agent" / "handoffs" / "task-infra-01.infrastructure-verification.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["handoff_id"] == payload["handoff_id"]
    assert not list(path.parent.glob("*.tmp"))



def test_classifier_delegates_environment_file_review_to_infrastructure_agent(tmp_path):
    requirement = Requirement(
        "REQ-ENV",
        "Create root .env.example and document each environment configuration setting.",
        acceptance_criteria=("Every environment variable has a documented safe default.",),
    )
    criterion = VerificationCriterion(
        "VER-ENV",
        "Review .env.example for documented environment settings and safe placeholders",
        VerificationKind.MANUAL,
        covers=(requirement.requirement_id,),
        covers_acceptance_criteria=requirement.acceptance_criterion_ids,
    )
    spec = EngineeringSpecification(
        "spec-env", "task-env-01", "Prepare environment configuration", str(tmp_path),
        requirements=(requirement,), verification=(criterion,),
        status=SpecificationStatus.VALIDATED,
    )

    assert classify_infrastructure_verification_criteria(spec) == ("VER-ENV",)
