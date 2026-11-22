import json
import subprocess
import sys

from coding_agent.engineering_spec import (
    EngineeringSpecification, ImplementationUnit, Requirement, RequirementKind,
    VerificationCriterion, VerificationKind,
)
from coding_agent.specification_check_planner import SpecificationCheckPlanner
from coding_agent.specification_checks import run_checks, CheckFailure


def _wp1_spec(tmp_path):
    requirements = (
        Requirement("REQ-002", "Create docker-compose.yml with frontend, backend, PostgreSQL services."),
        Requirement("REQ-003", "Configure PostgreSQL with environment-driven credentials, a persistent named volume, and a real readiness health check.", acceptance_criteria=("PostgreSQL has credentials, named volume and pg_isready healthcheck",)),
        Requirement("REQ-004", "Configure service startup dependencies using health conditions."),
        Requirement("REQ-005", "Create safe root .env.example values and document each setting."),
        Requirement("REQ-007", "Create project.yaml with goal, approved stack, architecture, six parent phases, and acceptance status."),
        Requirement("REQ-008", "Create README prerequisites, environment setup, and Compose workflow."),
    )
    units = (
        ImplementationUnit("U-COMPOSE", "Compose", "docker-compose.yml", satisfies=("REQ-002", "REQ-003", "REQ-004")),
        ImplementationUnit("U-ENV", "Env", ".env.example", satisfies=("REQ-005",)),
        ImplementationUnit("U-PROJECT", "Project", "project.yaml", satisfies=("REQ-007",)),
        ImplementationUnit("U-README", "Readme", "README.md", satisfies=("REQ-008",)),
    )
    verification = tuple(
        VerificationCriterion(f"V-{req.requirement_id}", f"Verify {req.requirement_id}", VerificationKind.STATIC, covers=(req.requirement_id,))
        for req in requirements
    )
    return EngineeringSpecification("spec", "task", "Scaffold", str(tmp_path), requirements=requirements,
                                    implementation_units=units, verification=verification)


def _write_good_wp1(root):
    (root / "docker-compose.yml").write_text("""services:
  frontend:
    image: frontend
    depends_on:
      backend:
        condition: service_healthy
  backend:
    image: backend
    depends_on:
      postgres:
        condition: service_healthy
  postgres:
    image: postgres:17
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: [\"CMD-SHELL\", \"pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}\"]
volumes:
  postgres_data:
""", encoding="utf-8")
    (root / ".env.example").write_text("""# Local database name used by Compose
POSTGRES_DB=example_db
# Local database username used by Compose
POSTGRES_USER=example_user
# Replace this placeholder for local development
POSTGRES_PASSWORD=example_password
""", encoding="utf-8")
    (root / "README.md").write_text("""# Project
## Prerequisites
Install Docker.
## Environment setup
Copy .env.example to .env.
## Compose workflow
Run docker compose up.
""", encoding="utf-8")
    (root / "project.yaml").write_text("""goal: Build the burger application
stack: PostgreSQL, Docker
architecture: frontend, backend, postgres
phases:
  - phase A
  - phase B
  - phase C
  - phase D
  - phase E
  - phase F
acceptance_status: in_progress
""", encoding="utf-8")


def test_specification_check_planner_generates_commands_for_supported_static_criteria(tmp_path):
    spec = _wp1_spec(tmp_path)
    prepared, notes = SpecificationCheckPlanner(tmp_path).prepare(spec)
    assert all(item.command for item in prepared.verification)
    assert all(item.kind == VerificationKind.STATIC for item in prepared.verification)
    assert len(notes) == len(spec.verification)


def test_specification_check_planner_converts_process_rule_to_manual(tmp_path):
    from dataclasses import replace
    spec = _wp1_spec(tmp_path)
    process = Requirement("REQ-P", "Follow global working rules and report changed files.", RequirementKind.CONSTRAINT)
    criterion = VerificationCriterion("V-P", "Verify global working rules", VerificationKind.STATIC, covers=("REQ-P",))
    spec = replace(spec, requirements=(*spec.requirements, process), verification=(*spec.verification, criterion))
    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    manual = next(item for item in prepared.verification if item.criterion_id == "V-P")
    assert manual.kind == VerificationKind.MANUAL
    assert not manual.command
    assert "Human evidence required" in manual.description


def test_read_only_checks_accept_semantically_complete_wp1_artifacts(tmp_path):
    spec = _wp1_spec(tmp_path)
    _write_good_wp1(tmp_path)
    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    criterion = next(item for item in prepared.verification if item.criterion_id == "V-REQ-003")
    payload = json.loads(criterion.command[-1])
    findings = run_checks(tmp_path, payload)
    assert findings
    assert any("named volume" in item for item in findings)


def test_check_rejects_postgres_bind_mount_instead_of_named_volume(tmp_path):
    spec = _wp1_spec(tmp_path)
    _write_good_wp1(tmp_path)
    compose = (tmp_path / "docker-compose.yml").read_text()
    compose = compose.replace("postgres_data:/var/lib/postgresql/data", "./postgres:/var/lib/postgresql/data")
    (tmp_path / "docker-compose.yml").write_text(compose)
    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    criterion = next(item for item in prepared.verification if item.criterion_id == "V-REQ-003")
    payload = json.loads(criterion.command[-1])
    try:
        run_checks(tmp_path, payload)
    except CheckFailure as exc:
        assert "named volume" in str(exc) or "bind mount" in str(exc)
    else:
        raise AssertionError("bind mount incorrectly passed the named-volume check")


def test_cli_check_module_runs_and_reports_actual_failure(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  backend: {}\n", encoding="utf-8")
    payload = {"requirements": [{"requirement_id": "R", "description": "Create docker-compose.yml with frontend backend postgres services", "acceptance_criteria": []}],
               "units": [{"unit_id": "U", "target_path": "docker-compose.yml"}]}
    result = subprocess.run([sys.executable, "-m", "coding_agent.specification_checks", str(tmp_path), json.dumps(payload)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "frontend" in result.stderr


def test_project_yaml_check_rejects_incomplete_parent_phase_list(tmp_path):
    spec = _wp1_spec(tmp_path)
    (tmp_path / "project.yaml").write_text(
        "goal: Burger app\nstack: Docker PostgreSQL\narchitecture: frontend backend\n"
        "phases:\n  - phase A\n  - phase B\nacceptance_status: in_progress\n",
        encoding="utf-8",
    )
    criterion = next(item for item in SpecificationCheckPlanner(tmp_path).prepare(spec)[0].verification
                     if item.criterion_id == "V-REQ-007")
    payload = json.loads(criterion.command[-1])
    try:
        run_checks(tmp_path, payload)
    except CheckFailure as exc:
        assert "six parent phases" in str(exc)
    else:
        raise AssertionError("incomplete parent phase list incorrectly passed")


def test_nested_dockerignore_rejects_parent_directory_negation(tmp_path):
    spec = _wp1_spec(tmp_path)
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".dockerignore").write_text("*\n!backend/\n!backend/*\n", encoding="utf-8")
    from coding_agent.specification_checks import _check_dockerignore
    try:
        _check_dockerignore(tmp_path, ["backend/.dockerignore"])
    except CheckFailure as exc:
        assert "build-context-inappropriate" in str(exc)
    else:
        raise AssertionError("incorrect nested .dockerignore negation incorrectly passed")


def test_test_artifact_requirement_gets_pytest_command_instead_of_manual_fallback(tmp_path):
    from coding_agent.engineering_spec import UnitKind
    requirement = Requirement("REQ-TEST", "Add meaningful tests for the new behavior.", RequirementKind.FUNCTIONAL)
    unit = ImplementationUnit("UNIT-TEST", "Add infrastructure tests", "tests/test_infrastructure.py",
                              kind=UnitKind.TEST, satisfies=("REQ-TEST",))
    criterion = VerificationCriterion("VER-AUTO-001", "Manually verify REQ-TEST: tests exist.",
                                      VerificationKind.MANUAL, covers=("REQ-TEST",))
    spec = EngineeringSpecification("spec-tests", "task-tests", "Add tests", str(tmp_path),
                                    requirements=(requirement,), implementation_units=(unit,),
                                    verification=(criterion,))
    prepared, notes = SpecificationCheckPlanner(tmp_path).prepare(spec)
    assert prepared.verification[0].kind == VerificationKind.BEHAVIORAL
    assert prepared.verification[0].command == ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider")
    assert "mapped explicit test-artifact requirement to pytest" in notes[0]


def test_generated_manual_fallback_is_promoted_when_a_deterministic_check_exists(tmp_path):
    requirement = Requirement(
        "REQ-DB", "Keep PostgreSQL as the runtime database; do not substitute SQLite.",
        RequirementKind.CONSTRAINT,
        acceptance_criteria=("Runtime configuration uses PostgreSQL, not SQLite.",),
    )
    criterion = VerificationCriterion(
        "VER-AUTO-001", "Manually verify REQ-DB: runtime database remains PostgreSQL.",
        VerificationKind.MANUAL, covers=("REQ-DB",),
    )
    spec = EngineeringSpecification("spec-db", "task-db", "Database boundary", str(tmp_path),
                                    requirements=(requirement,), verification=(criterion,))
    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    assert prepared.verification[0].kind == VerificationKind.STATIC
    assert prepared.verification[0].command


def test_source_control_constraint_remains_manual_despite_secret_scan_heuristic(tmp_path):
    requirement = Requirement(
        "REQ-SECRET", "Keep secrets out of source control and use environment variables.",
        RequirementKind.CONSTRAINT,
    )
    criterion = VerificationCriterion(
        "VER-AUTO-001", "Manually verify REQ-SECRET: no secrets are committed.",
        VerificationKind.MANUAL, covers=("REQ-SECRET",),
    )
    spec = EngineeringSpecification("spec-secret", "task-secret", "Secret boundary", str(tmp_path),
                                    requirements=(requirement,), verification=(criterion,))
    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    assert prepared.verification[0].kind == VerificationKind.MANUAL
    assert not prepared.verification[0].command



def test_runtime_check_accepts_realistic_non_secret_env_defaults(tmp_path):
    spec = _wp1_spec(tmp_path)
    _write_good_wp1(tmp_path)
    (tmp_path / ".env.example").write_text(
        "# Compose service hostname for PostgreSQL\n"
        "DB_HOST=postgres\n"
        "# PostgreSQL port inside the Compose network\n"
        "DB_PORT=5432\n"
        "# Non-secret local database user\n"
        "DB_USER=fafas_app\n"
        "# Replace this local-only password placeholder\n"
        "DB_PASSWORD=change_me\n",
        encoding="utf-8",
    )
    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    criterion = next(item for item in prepared.verification if item.criterion_id == "V-REQ-005")
    findings = run_checks(tmp_path, json.loads(criterion.command[-1]))
    assert any("documented settings" in item for item in findings)


def test_runtime_check_accepts_project_yaml_with_six_id_based_phases(tmp_path):
    spec = _wp1_spec(tmp_path)
    _write_good_wp1(tmp_path)
    (tmp_path / "project.yaml").write_text(
        "goal: Build the burger application\n"
        "approved_stack:\n  - Docker Compose\n  - PostgreSQL\n"
        "repository_architecture:\n  frontend: UI\n  backend: API\n  postgres: Database\n"
        "phases:\n"
        "  - id: A\n    name: Foundation\n"
        "  - id: B\n    name: Backend\n"
        "  - id: C\n    name: Frontend\n"
        "  - id: D\n    name: Integration\n"
        "  - id: E\n    name: Testing\n"
        "  - id: F\n    name: Release\n"
        "acceptance_status: pending\n",
        encoding="utf-8",
    )
    prepared, _ = SpecificationCheckPlanner(tmp_path).prepare(spec)
    criterion = next(item for item in prepared.verification if item.criterion_id == "V-REQ-007")
    findings = run_checks(tmp_path, json.loads(criterion.command[-1]))
    assert any("six phases" in item for item in findings)


def test_compose_semantic_check_accepts_four_space_indentation_and_long_named_volume(tmp_path):
    from coding_agent.specification_checks import compose_content_issues

    compose = """services:
    backend:
        depends_on:
            postgres:
                condition: service_healthy
    postgres:
        image: postgres:17
        environment:
            POSTGRES_DB: ${POSTGRES_DB}
            POSTGRES_USER: ${POSTGRES_USER}
            POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
        volumes:
            - type: volume
              source: postgres_data
              target: /var/lib/postgresql/data
        healthcheck:
            test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
volumes:
    postgres_data: {}
"""
    requirement_text = (
        "Configure PostgreSQL with environment-driven credentials, a persistent named volume, "
        "and a real readiness health check using pg_isready. Configure service dependencies "
        "using health conditions."
    )
    assert compose_content_issues(compose, requirement_text) == []


def test_project_yaml_check_accepts_nested_metadata_and_six_phase_objects():
    from coding_agent.specification_checks import project_yaml_content_issues

    content = """project:
  goal: Build a burger-shop application
  approved_stack:
    frontend: React and TypeScript
    backend: Django
    database: PostgreSQL
  repository_architecture:
    frontend: Customer-facing application
    backend: REST API
    postgres: Persistent database
  phases:
    - id: A
      name: Phase A
    - id: B
      name: Phase B
    - id: C
      name: Phase C
    - id: D
      name: Phase D
    - id: E
      name: Phase E
    - id: F
      name: Phase F
  acceptance_status:
    phase_1: in_progress
"""
    assert project_yaml_content_issues(content) == []


def test_env_example_accepts_inline_documentation_but_still_rejects_real_secret():
    from coding_agent.specification_checks import env_example_content_issues

    safe = """POSTGRES_DB=fafas_burger # Local database name
POSTGRES_USER=fafas_app # Local application user
POSTGRES_PASSWORD=change_me # Non-secret placeholder only
"""
    assert env_example_content_issues(safe) == []

    unsafe = safe.replace("POSTGRES_PASSWORD=change_me", "POSTGRES_PASSWORD=RealPassword123")
    assert any("non-secret placeholder" in issue for issue in env_example_content_issues(unsafe))
