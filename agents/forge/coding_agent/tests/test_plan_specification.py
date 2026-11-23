import pytest

from coding_agent.engineering_spec import (
    ArchitectureModule, EngineeringSpecification, ImplementationUnit,
    Requirement, RequirementKind, VerificationCriterion, VerificationKind,
)
from coding_agent.models import ChangeType, FileChange, ImplementationPlan
from coding_agent.plan_specification import PlanSpecificationValidator


@pytest.fixture
def specification(tmp_path):
    return EngineeringSpecification(
        "spec-1", "task-1", "Build a small app", str(tmp_path),
        requirements=(Requirement("REQ-1", "Users can sign in"),
                      Requirement("REQ-2", "Users can sign out")),
        architecture=(ArchitectureModule("auth", "Auth", "Authentication", file_paths=("src/auth.py",)),),
        implementation_units=(
            ImplementationUnit("login", "Login", "src/auth.py", satisfies=("REQ-1",)),
            ImplementationUnit("logout", "Logout", "src/logout.py", satisfies=("REQ-2",)),
        ),
        verification=(VerificationCriterion("VER-1", "Auth tests", VerificationKind.BEHAVIORAL,
                                             command=("pytest",), covers=("REQ-1", "REQ-2")),),
    )


def plan(*paths):
    return ImplementationPlan("task-1", "Implement auth", tuple(
        FileChange(path, ChangeType.CREATE, "Implement ESpec unit", "pass\n") for path in paths
    ))


def test_validator_accepts_complete_declared_requirement_coverage(specification):
    report = PlanSpecificationValidator().validate(plan("src/auth.py", "src/logout.py"), specification)
    assert report.valid
    assert report.covered_requirements == ("REQ-1", "REQ-2")
    assert not report.issues


def test_validator_detects_omitted_requirement(specification):
    report = PlanSpecificationValidator().validate(plan("src/auth.py"), specification)
    assert not report.valid
    assert report.missing_requirements == ("REQ-2",)
    assert "REQ-2" in report.issues[0]


def test_validator_rejects_undeclared_unrelated_file(specification):
    report = PlanSpecificationValidator().validate(
        plan("src/auth.py", "src/logout.py", "environment/Dockerfile"), specification
    )
    assert not report.valid
    assert report.undeclared_paths == ("environment/Dockerfile",)


def test_validator_rejects_task_mismatch(specification):
    wrong = ImplementationPlan("other-task", "Wrong task", (FileChange(
        "src/auth.py", ChangeType.CREATE, "reason", "pass\n"),))
    with pytest.raises(ValueError, match="task IDs"):
        PlanSpecificationValidator().validate(wrong, specification)



def test_directory_target_path_covers_and_authorizes_descendant_files(specification):
    report = PlanSpecificationValidator().validate(
        plan("src/auth.py", "src/logout.py"), specification
    )
    assert report.valid


def test_directory_target_path_does_not_authorize_similarly_named_sibling(specification):
    report = PlanSpecificationValidator().validate(
        plan("src/auth.py", "src-extra/secret.py", "src/logout.py"), specification
    )
    assert not report.valid
    assert report.undeclared_paths == ("src-extra/secret.py",)


def test_unit_directory_scope_covers_nested_implementation_file(specification):
    from dataclasses import replace

    units = tuple(
        replace(unit, target_path="src")
        if unit.unit_id == "login" else unit
        for unit in specification.implementation_units
    )
    scoped_spec = replace(specification, implementation_units=units)
    report = PlanSpecificationValidator().validate(
        plan("src/auth/views.py", "src/logout.py"), scoped_spec
    )
    assert report.valid
    assert report.covered_requirements == ("REQ-1", "REQ-2")



def test_unmapped_constraints_are_not_file_change_obligations(specification):
    from dataclasses import replace

    constrained = replace(
        specification,
        requirements=(
            *specification.requirements,
            Requirement(
                "REQ-3",
                "Do not substitute the approved database.",
                kind=RequirementKind.CONSTRAINT,
                acceptance_criteria=("The approved database remains in use.",),
            ),
        ),
    )

    report = PlanSpecificationValidator().validate(
        plan("src/auth.py", "src/logout.py"), constrained
    )

    assert report.valid
    assert report.required_requirements == ("REQ-1", "REQ-2")
    assert report.covered_requirements == ("REQ-1", "REQ-2")
    assert report.missing_requirements == ()
    assert report.missing_units == ()


def test_every_unit_mapped_to_one_requirement_must_be_implemented(tmp_path):
    specification = EngineeringSpecification(
        "spec-shared", "task-1", "Create service ignore files", str(tmp_path),
        requirements=(Requirement("REQ-1", "Create ignore files in both services"),),
        architecture=(
            ArchitectureModule(
                "frontend", "Frontend", "Frontend configuration",
                file_paths=("frontend/.dockerignore",),
            ),
            ArchitectureModule(
                "backend", "Backend", "Backend configuration",
                file_paths=("backend/.dockerignore",),
            ),
        ),
        implementation_units=(
            ImplementationUnit(
                "UNIT-1", "Frontend ignore", "frontend/.dockerignore",
                satisfies=("REQ-1",),
            ),
            ImplementationUnit(
                "UNIT-2", "Backend ignore", "backend/.dockerignore",
                satisfies=("REQ-1",),
            ),
        ),
    )

    report = PlanSpecificationValidator().validate(
        plan("frontend/.dockerignore"), specification
    )

    assert not report.valid
    assert report.covered_units == ("UNIT-1",)
    assert report.missing_units == ("UNIT-2 -> backend/.dockerignore",)
    assert report.missing_requirements == ("REQ-1",)
    assert any("UNIT-2 -> backend/.dockerignore" in issue for issue in report.issues)


def test_unmapped_functional_requirement_remains_a_hard_failure(specification):
    from dataclasses import replace

    without_mapping = replace(
        specification,
        implementation_units=(
            specification.implementation_units[0],
        ),
    )

    report = PlanSpecificationValidator().validate(
        plan("src/auth.py"), without_mapping
    )

    assert not report.valid
    assert report.missing_requirements == ("REQ-2",)


def test_constraint_with_explicit_implementation_unit_is_enforced(tmp_path):
    specification = EngineeringSpecification(
        "spec-constraint-unit", "task-1", "Preserve runtime database config",
        str(tmp_path),
        requirements=(
            Requirement(
                "REQ-C",
                "Keep the database configuration on PostgreSQL.",
                kind=RequirementKind.CONSTRAINT,
            ),
        ),
        architecture=(
            ArchitectureModule(
                "database", "Database", "Runtime database configuration",
                file_paths=("config/database.py",),
            ),
        ),
        implementation_units=(
            ImplementationUnit(
                "UNIT-C", "Configure PostgreSQL", "config/database.py",
                satisfies=("REQ-C",),
            ),
        ),
    )

    missing = PlanSpecificationValidator().validate(
        ImplementationPlan("task-1", "Missing database config", ()), specification
    )
    complete = PlanSpecificationValidator().validate(
        plan("config/database.py"), specification
    )

    assert not missing.valid
    assert missing.missing_requirements == ("REQ-C",)
    assert missing.missing_units == ("UNIT-C -> config/database.py",)
    assert complete.valid
    assert complete.covered_requirements == ("REQ-C",)


def test_unmapped_implementation_unit_is_still_mandatory(tmp_path):
    specification = EngineeringSpecification(
        "spec-unmapped-unit", "task-1", "Create config and docs", str(tmp_path),
        requirements=(Requirement("REQ-1", "Create config"),),
        architecture=(
            ArchitectureModule(
                "config", "Config", "Configuration",
                file_paths=("config/app.yml", "README.md"),
            ),
        ),
        implementation_units=(
            ImplementationUnit(
                "UNIT-1", "App config", "config/app.yml", satisfies=("REQ-1",),
            ),
            ImplementationUnit(
                "UNIT-2", "Project documentation", "README.md",
            ),
        ),
    )

    report = PlanSpecificationValidator().validate(
        plan("config/app.yml"), specification
    )

    assert not report.valid
    assert report.missing_units == ("UNIT-2 -> README.md",)


def test_one_file_change_covers_all_units_for_the_same_target_path(tmp_path):
    specification = EngineeringSpecification(
        "spec-same-path", "task-1", "Configure Compose", str(tmp_path),
        requirements=(
            Requirement("REQ-1", "Define services"),
            Requirement("REQ-2", "Configure PostgreSQL"),
            Requirement("REQ-3", "Configure startup dependencies"),
        ),
        architecture=(
            ArchitectureModule(
                "compose", "Compose", "Container orchestration",
                file_paths=("docker-compose.yml",),
            ),
        ),
        implementation_units=(
            ImplementationUnit(
                "UNIT-1", "Define services", "docker-compose.yml",
                satisfies=("REQ-1",),
            ),
            ImplementationUnit(
                "UNIT-2", "Configure PostgreSQL", "docker-compose.yml",
                satisfies=("REQ-2",),
            ),
            ImplementationUnit(
                "UNIT-3", "Configure dependencies", "docker-compose.yml",
                satisfies=("REQ-3",),
            ),
        ),
    )

    report = PlanSpecificationValidator().validate(
        plan("docker-compose.yml"), specification
    )

    assert report.valid
    assert report.covered_units == ("UNIT-1", "UNIT-2", "UNIT-3")
    assert report.covered_requirements == ("REQ-1", "REQ-2", "REQ-3")
    assert report.missing_units == ()


def test_workspace_root_target_is_not_treated_as_covered_by_arbitrary_file(tmp_path):
    specification = EngineeringSpecification(
        "spec-root-target", "task-1", "Create repository layout", str(tmp_path),
        requirements=(Requirement("REQ-1", "Create the required project layout"),),
        architecture=(
            ArchitectureModule(
                "root", "Root", "Project files", file_paths=("README.md",),
            ),
        ),
        implementation_units=(
            ImplementationUnit(
                "UNIT-ROOT", "Create root layout", ".",
                satisfies=("REQ-1",),
            ),
        ),
    )

    report = PlanSpecificationValidator().validate(
        plan("README.md"), specification
    )

    assert not report.valid
    assert report.missing_units == (
        "UNIT-ROOT -> . (workspace-root targets cannot be represented as file changes)",
    )


def test_validator_rejects_postgres_bind_mount_when_named_volume_is_required(tmp_path):
    specification = EngineeringSpecification(
        "spec-compose", "task-1", "Scaffold services", str(tmp_path),
        requirements=(Requirement(
            "REQ-COMPOSE", "Configure PostgreSQL with a persistent named volume.",
            acceptance_criteria=("PostgreSQL data uses a persistent named volume.",),
        ),),
        architecture=(ArchitectureModule("root", "Root", "Infrastructure", file_paths=("docker-compose.yml",)),),
        implementation_units=(ImplementationUnit(
            "UNIT-COMPOSE", "Compose configuration", "docker-compose.yml", satisfies=("REQ-COMPOSE",)
        ),),
    )
    compose = """services:
  frontend:
    image: frontend
  backend:
    image: backend
  postgres:
    image: postgres:17
    volumes:
      - ./postgres:/var/lib/postgresql/data
volumes:
  postgres_data:
"""
    plan_with_content = ImplementationPlan("task-1", "Compose", (
        FileChange("docker-compose.yml", ChangeType.CREATE, "Compose", compose),
    ))
    report = PlanSpecificationValidator().validate(plan_with_content, specification)
    assert not report.valid
    assert any("host bind mount" in issue for issue in report.semantic_issues)


def test_validator_rejects_readme_without_required_sections(tmp_path):
    specification = EngineeringSpecification(
        "spec-readme", "task-1", "Document setup", str(tmp_path),
        requirements=(Requirement(
            "REQ-README", "Create a README.md section covering prerequisites, environment setup, and Compose workflow.",
        ),),
        architecture=(ArchitectureModule("root", "Root", "Docs", file_paths=("README.md",)),),
        implementation_units=(ImplementationUnit(
            "UNIT-README", "README", "README.md", satisfies=("REQ-README",)
        ),),
    )
    plan_with_content = ImplementationPlan("task-1", "Docs", (
        FileChange("README.md", ChangeType.CREATE, "Docs", "# Project\n\nA sample project.\n"),
    ))
    report = PlanSpecificationValidator().validate(plan_with_content, specification)
    assert not report.valid
    assert any("README does not document prerequisites" in issue for issue in report.semantic_issues)


def test_validator_rejects_placeholder_test_file_without_test_case_or_assertion(tmp_path):
    from coding_agent.engineering_spec import UnitKind
    specification = EngineeringSpecification(
        "spec-tests", "task-1", "Add tests", str(tmp_path),
        requirements=(Requirement("REQ-TEST", "Add meaningful tests for new behavior."),),
        architecture=(ArchitectureModule("tests", "Tests", "Regression tests", file_paths=("tests/test_feature.py",)),),
        implementation_units=(ImplementationUnit(
            "UNIT-TEST", "Feature tests", "tests/test_feature.py", kind=UnitKind.TEST,
            satisfies=("REQ-TEST",),
        ),),
    )
    plan_with_content = ImplementationPlan("task-1", "Add tests", (
        FileChange("tests/test_feature.py", ChangeType.CREATE, "Test", "VALUE = 1\n"),
    ))
    report = PlanSpecificationValidator().validate(plan_with_content, specification)
    assert not report.valid
    assert any("recognizable test case and assertion" in issue for issue in report.semantic_issues)


def test_validator_accepts_python_test_case_with_assertion(tmp_path):
    from coding_agent.engineering_spec import UnitKind
    specification = EngineeringSpecification(
        "spec-tests-ok", "task-1", "Add tests", str(tmp_path),
        requirements=(Requirement("REQ-TEST", "Add meaningful tests for new behavior."),),
        architecture=(ArchitectureModule("tests", "Tests", "Regression tests", file_paths=("tests/test_feature.py",)),),
        implementation_units=(ImplementationUnit(
            "UNIT-TEST", "Feature tests", "tests/test_feature.py", kind=UnitKind.TEST,
            satisfies=("REQ-TEST",),
        ),),
    )
    plan_with_content = ImplementationPlan("task-1", "Add tests", (
        FileChange("tests/test_feature.py", ChangeType.CREATE, "Test", "def test_value():\n    assert 1 + 1 == 2\n"),
    ))
    report = PlanSpecificationValidator().validate(plan_with_content, specification)
    assert report.valid, report.issues



def test_validator_accepts_documented_env_values_that_are_not_secrets(tmp_path):
    specification = EngineeringSpecification(
        "spec-env", "task-1", "Create environment example", str(tmp_path),
        requirements=(Requirement(
            "REQ-ENV",
            "Create safe root .env.example values and document each non-secret setting.",
        ),),
        architecture=(ArchitectureModule("root", "Root", "Environment", file_paths=(".env.example",)),),
        implementation_units=(ImplementationUnit(
            "UNIT-ENV", "Environment example", ".env.example", satisfies=("REQ-ENV",),
        ),),
    )
    env = """# PostgreSQL service hostname on the Compose network
DB_HOST=postgres
# PostgreSQL service port
DB_PORT=5432
# Non-secret local database username
DB_USER=fafas_app
# Replace this placeholder with a local-only password
DB_PASSWORD=change_me
"""
    proposed = ImplementationPlan("task-1", "Environment", (
        FileChange(".env.example", ChangeType.CREATE, "Environment", env),
    ))
    report = PlanSpecificationValidator().validate(proposed, specification)
    assert report.valid, report.semantic_issues


def test_validator_rejects_real_secret_in_env_example(tmp_path):
    specification = EngineeringSpecification(
        "spec-env-secret", "task-1", "Create environment example", str(tmp_path),
        requirements=(Requirement(
            "REQ-ENV",
            "Create safe root .env.example values and document each non-secret setting.",
        ),),
        architecture=(ArchitectureModule("root", "Root", "Environment", file_paths=(".env.example",)),),
        implementation_units=(ImplementationUnit(
            "UNIT-ENV", "Environment example", ".env.example", satisfies=("REQ-ENV",),
        ),),
    )
    env = """# Password for local PostgreSQL
DB_PASSWORD=RealPassword123!
"""
    proposed = ImplementationPlan("task-1", "Environment", (
        FileChange(".env.example", ChangeType.CREATE, "Environment", env),
    ))
    report = PlanSpecificationValidator().validate(proposed, specification)
    assert not report.valid
    assert any("placeholder" in issue for issue in report.semantic_issues)


def test_validator_accepts_project_yaml_with_six_id_based_phase_entries(tmp_path):
    specification = EngineeringSpecification(
        "spec-project", "task-1", "Create project metadata", str(tmp_path),
        requirements=(Requirement(
            "REQ-PROJECT",
            "Create project.yaml describing product goal, approved stack, repository architecture, six parent phases, and acceptance status.",
        ),),
        architecture=(ArchitectureModule("root", "Root", "Metadata", file_paths=("project.yaml",)),),
        implementation_units=(ImplementationUnit(
            "UNIT-PROJECT", "Project metadata", "project.yaml", satisfies=("REQ-PROJECT",),
        ),),
    )
    project = """goal: Build the Fafa's Burger application
approved_stack:
  - Docker Compose
  - PostgreSQL
repository_architecture:
  frontend: Browser application
  backend: API service
  postgres: Persistent database
phases:
  - id: A
    name: Foundation
  - id: B
    name: Backend
  - id: C
    name: Frontend
  - id: D
    name: Integration
  - id: E
    name: Testing
  - id: F
    name: Release
acceptance_status: pending
"""
    proposed = ImplementationPlan("task-1", "Project metadata", (
        FileChange("project.yaml", ChangeType.CREATE, "Metadata", project),
    ))
    report = PlanSpecificationValidator().validate(proposed, specification)
    assert report.valid, report.semantic_issues


def test_validator_rejects_project_yaml_with_only_two_parent_phases(tmp_path):
    specification = EngineeringSpecification(
        "spec-project-short", "task-1", "Create project metadata", str(tmp_path),
        requirements=(Requirement(
            "REQ-PROJECT",
            "Create project.yaml describing product goal, approved stack, repository architecture, six parent phases, and acceptance status.",
        ),),
        architecture=(ArchitectureModule("root", "Root", "Metadata", file_paths=("project.yaml",)),),
        implementation_units=(ImplementationUnit(
            "UNIT-PROJECT", "Project metadata", "project.yaml", satisfies=("REQ-PROJECT",),
        ),),
    )
    project = """goal: Burger app
stack: Docker PostgreSQL
architecture: frontend backend postgres
phases:
  - id: A
    name: Foundation
  - id: B
    name: Backend
acceptance_status: pending
"""
    proposed = ImplementationPlan("task-1", "Project metadata", (
        FileChange("project.yaml", ChangeType.CREATE, "Metadata", project),
    ))
    report = PlanSpecificationValidator().validate(proposed, specification)
    assert not report.valid
    assert any("six parent phases" in issue for issue in report.semantic_issues)


def test_plan_semantic_validation_accepts_valid_compose_with_nonstandard_yaml_indentation(tmp_path):
    specification = EngineeringSpecification(
        "spec-compose-indent", "task-1", "Scaffold PostgreSQL", str(tmp_path),
        requirements=(Requirement(
            "REQ-COMPOSE",
            "Create docker-compose.yml with PostgreSQL, a persistent named volume, "
            "environment-driven credentials and a pg_isready readiness health check.",
            acceptance_criteria=("Use a declared named volume and pg_isready healthcheck.",),
        ),),
        architecture=(ArchitectureModule(
            "root", "Root", "Infrastructure", file_paths=("docker-compose.yml",),
        ),),
        implementation_units=(ImplementationUnit(
            "UNIT-COMPOSE", "Compose configuration", "docker-compose.yml",
            satisfies=("REQ-COMPOSE",),
        ),),
    )
    compose = """services:
    postgres:
        image: postgres:17
        environment:
            POSTGRES_DB: ${POSTGRES_DB}
            POSTGRES_USER: ${POSTGRES_USER}
            POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
        volumes:
            - postgres_data:/var/lib/postgresql/data
        healthcheck:
            test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
volumes:
    postgres_data:
"""
    plan_with_content = ImplementationPlan("task-1", "Compose", (
        FileChange("docker-compose.yml", ChangeType.CREATE, "Compose", compose),
    ))
    report = PlanSpecificationValidator().validate(plan_with_content, specification)
    assert report.valid, report.semantic_issues


def test_directory_only_requirement_is_covered_by_required_structural_verification(tmp_path):
    spec = EngineeringSpecification(
        "spec-layout", "task-1", "Create repository layout", str(tmp_path),
        requirements=(
            Requirement(
                "REQ-DIR",
                "Establish frontend/, backend/, and postgres/ directories.",
                RequirementKind.FUNCTIONAL,
                ("The frontend, backend, and postgres directories exist.",),
            ),
            Requirement(
                "REQ-FILE", "Create the root .dockerignore file.",
                RequirementKind.FUNCTIONAL,
                ("The root .dockerignore file exists.",),
            ),
        ),
        architecture=(ArchitectureModule(
            "root", "Root", "Root configuration", file_paths=(".dockerignore",)
        ),),
        implementation_units=(ImplementationUnit(
            "ignore", "Root ignore rules", ".dockerignore",
            satisfies=("REQ-FILE",),
        ),),
        verification=(
            VerificationCriterion(
                "VER-DIR", "Verify frontend, backend, and postgres directories exist.",
                VerificationKind.STRUCTURAL,
                command=("python", "-c", "assert True"),
                covers=("REQ-DIR",),
            ),
            VerificationCriterion(
                "VER-FILE", "Verify the root .dockerignore file exists.",
                VerificationKind.STRUCTURAL,
                command=("python", "-c", "assert True"),
                covers=("REQ-FILE",),
            ),
        ),
    )

    directory_changes = tuple(
        FileChange(path, ChangeType.MKDIR, "Create required repository directory")
        for path in ("frontend", "backend", "postgres")
    )
    complete_plan = ImplementationPlan(
        "task-1", "Create repository layout", directory_changes + plan(".dockerignore").changes
    )
    report = PlanSpecificationValidator().validate(complete_plan, spec)

    assert report.valid
    assert report.covered_requirements == ("REQ-DIR", "REQ-FILE")


def test_directory_only_requirement_without_structural_verification_is_not_covered(tmp_path):
    spec = EngineeringSpecification(
        "spec-layout", "task-1", "Create repository layout", str(tmp_path),
        requirements=(Requirement(
            "REQ-DIR",
            "Establish frontend/, backend/, and postgres/ directories.",
            RequirementKind.FUNCTIONAL,
            ("The frontend, backend, and postgres directories exist.",),
        ),),
        architecture=(ArchitectureModule(
            "root", "Root", "Root configuration", file_paths=(".dockerignore",)
        ),),
        implementation_units=(),
        verification=(VerificationCriterion(
            "VER-MANUAL", "Review the repository files.",
            VerificationKind.MANUAL,
            covers=("REQ-DIR",),
        ),),
    )

    report = PlanSpecificationValidator().validate(
        ImplementationPlan("task-1", "No file changes", ()), spec
    )

    assert not report.valid
    assert report.missing_requirements == ("REQ-DIR",)


def test_directory_only_requirement_fails_without_mkdir_when_directories_are_missing(tmp_path):
    spec = EngineeringSpecification(
        "spec-layout-missing", "task-1", "Create repository layout", str(tmp_path),
        requirements=(Requirement(
            "REQ-DIR", "Establish frontend/ and backend/ directories.",
            RequirementKind.FUNCTIONAL,
            ("The frontend and backend directories exist.",),
        ),),
        verification=(VerificationCriterion(
            "VER-DIR", "Verify frontend and backend directories exist.",
            VerificationKind.STRUCTURAL,
            command=("python", "-c", "assert True"),
            covers=("REQ-DIR",),
        ),),
    )
    report = PlanSpecificationValidator().validate(
        ImplementationPlan("task-1", "No directory operations", ()), spec
    )
    assert not report.valid
    assert report.missing_requirements == ("REQ-DIR",)


def test_directory_only_requirement_can_reuse_existing_directories(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend").mkdir()
    spec = EngineeringSpecification(
        "spec-layout-existing", "task-1", "Ensure repository layout", str(tmp_path),
        requirements=(Requirement(
            "REQ-DIR", "Establish frontend/ and backend/ directories.",
            RequirementKind.FUNCTIONAL,
            ("The frontend and backend directories exist.",),
        ),),
        verification=(VerificationCriterion(
            "VER-DIR", "Verify frontend and backend directories exist.",
            VerificationKind.STRUCTURAL,
            command=("python", "-c", "assert True"),
            covers=("REQ-DIR",),
        ),),
    )
    report = PlanSpecificationValidator().validate(
        ImplementationPlan("task-1", "Reuse existing directories", ()), spec
    )
    assert report.valid
    assert report.covered_requirements == ("REQ-DIR",)


def test_explicit_directory_is_not_satisfied_only_as_a_file_creation_side_effect(tmp_path):
    spec = EngineeringSpecification(
        "spec-dir-file", "task-1", "Create frontend ignore file and directory", str(tmp_path),
        requirements=(Requirement(
            "REQ-1",
            "Create frontend/ directory and frontend/.dockerignore.",
            RequirementKind.FUNCTIONAL,
            ("The frontend directory and ignore file exist.",),
        ),),
        architecture=(ArchitectureModule(
            "frontend", "Frontend", "Frontend setup",
            file_paths=("frontend/.dockerignore",),
        ),),
        implementation_units=(ImplementationUnit(
            "UNIT-1", "Frontend ignore", "frontend/.dockerignore",
            satisfies=("REQ-1",),
        ),),
    )
    file_only = plan("frontend/.dockerignore")
    report = PlanSpecificationValidator().validate(file_only, spec)
    assert not report.valid
    assert report.missing_requirements == ("REQ-1",)

    complete = ImplementationPlan(
        "task-1", "Create frontend directory and ignore file",
        (FileChange("frontend", ChangeType.MKDIR, "Create frontend directory"),
         FileChange("frontend/.dockerignore", ChangeType.CREATE, "Create ignore file", "node_modules\n")),
    )
    assert PlanSpecificationValidator().validate(complete, spec).valid


def test_plan_validator_reports_unimplemented_acceptance_criterion(tmp_path):
    requirement = Requirement(
        "REQ-1", "Users can sign in.",
        acceptance_criteria=("Valid credentials create a session.",),
    )
    criterion_id = requirement.acceptance_criterion_ids[0]
    spec = EngineeringSpecification(
        "spec-acceptance", "task-1", "Implement sign-in", str(tmp_path),
        requirements=(requirement,),
        implementation_units=(ImplementationUnit(
            "UNIT-1", "Sign-in implementation", "src/auth.py",
            satisfies=("REQ-1",),
            implements_acceptance_criteria=(criterion_id,),
        ),),
        verification=(VerificationCriterion(
            "VER-1", "Verify valid credentials create a session.",
            VerificationKind.BEHAVIORAL,
            command=("pytest",),
            covers=("REQ-1",),
            covers_acceptance_criteria=(criterion_id,),
        ),),
    )

    report = PlanSpecificationValidator().validate(
        ImplementationPlan("task-1", "No implementation", ()), spec
    )

    assert not report.valid
    assert report.missing_acceptance_criteria == (criterion_id,)
    assert any(criterion_id in issue for issue in report.issues)


def test_plan_validator_reports_acceptance_criterion_when_mapped_unit_is_omitted(tmp_path):
    requirement = Requirement(
        "REQ-1", "Users can sign in.",
        acceptance_criteria=("Valid credentials create a session.",),
    )
    criterion_id = requirement.acceptance_criterion_ids[0]
    spec = EngineeringSpecification(
        "spec-acceptance", "task-1", "Implement sign-in", str(tmp_path),
        requirements=(requirement,),
        implementation_units=(ImplementationUnit(
            "UNIT-1", "Sign-in implementation", "src/auth.py",
            satisfies=("REQ-1",),
            implements_acceptance_criteria=(criterion_id,),
        ),),
    )

    report = PlanSpecificationValidator().validate(
        ImplementationPlan("task-1", "Omit auth", ()), spec
    )
    assert not report.valid
    assert report.missing_acceptance_criteria == (criterion_id,)
