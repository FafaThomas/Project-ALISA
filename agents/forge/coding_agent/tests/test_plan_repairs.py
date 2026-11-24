from coding_agent.engineering_spec import (
    ArchitectureModule,
    EngineeringSpecification,
    ImplementationUnit,
    Requirement,
)
from coding_agent.models import ChangeType, FileChange, ImplementationPlan
from coding_agent.plan_repairs import normalize_env_example, repair_plan_artifacts


def test_repairs_omitted_dockerignore_unit_without_touching_other_changes(tmp_path):
    specification = EngineeringSpecification(
        "spec-repair", "task-repair", "Create Docker ignore files", str(tmp_path),
        requirements=(Requirement("REQ-DOCKER", "Add root and service .dockerignore files."),),
        architecture=(ArchitectureModule(
            "root", "Root", "Repository files",
            file_paths=(".dockerignore", "backend/.dockerignore"),
        ),),
        implementation_units=(
            ImplementationUnit("UNIT-ROOT", "Root ignore", ".dockerignore", satisfies=("REQ-DOCKER",)),
            ImplementationUnit("UNIT-BACKEND", "Backend ignore", "backend/.dockerignore", satisfies=("REQ-DOCKER",)),
        ),
    )
    original = FileChange(
        "backend/.dockerignore", ChangeType.CREATE, "Create backend ignore", "node_modules\n"
    )
    plan = ImplementationPlan("task-repair", "Add ignore files", (original,))

    repaired, repairs = repair_plan_artifacts(plan, specification)

    assert [change.path for change in repaired.changes] == [
        "backend/.dockerignore", ".dockerignore"
    ]
    added = repaired.changes[1]
    assert added.change_type == ChangeType.CREATE
    assert added.content is not None
    assert ".env" in added.content
    assert ".git" in added.content
    assert "node_modules/" in added.content
    assert r"\n" not in added.content
    assert len(added.content.splitlines()) > 5
    assert repaired.changes[0] == original
    assert any("added omitted .dockerignore" in item for item in repairs)


def test_env_example_repair_documents_each_setting_and_sanitizes_secret_values():
    original = (
        "# Example environment\n"
        "DB_USER=fafas_app\n"
        "DB_PASSWORD=real-looking-password\n"
        "FRONTEND_PORT=3000\n"
    )

    repaired, repairs = normalize_env_example(original)

    assert "# Application database username.\nDB_USER=fafas_app" in repaired
    assert "# Non-secret placeholder for the application database password.\nDB_PASSWORD=change_me" in repaired
    assert "# Host port exposed by the frontend.\nFRONTEND_PORT=3000" in repaired
    assert "real-looking-password" not in repaired
    assert len([line for line in repaired.splitlines() if "=" in line]) == 3
    assert any("replaced the non-placeholder secret" in item for item in repairs)


def test_env_example_repair_is_idempotent():
    first, _ = normalize_env_example("DB_NAME=fafas_burger\n")
    second, repairs = normalize_env_example(first)

    assert second == first
    assert repairs == ("documented DB_NAME",)


def test_plan_repair_only_touches_explicit_espec_artifacts(tmp_path):
    specification = EngineeringSpecification(
        "spec-repair-env", "task-repair-env", "Create env example", str(tmp_path),
        requirements=(Requirement("REQ-ENV", "Create a documented .env.example file."),),
        architecture=(ArchitectureModule(
            "root", "Root", "Repository configuration",
            file_paths=(".env.example", "README.md"),
        ),),
        implementation_units=(
            ImplementationUnit("UNIT-ENV", "Environment example", ".env.example", satisfies=("REQ-ENV",)),
        ),
    )
    readme = FileChange("README.md", ChangeType.CREATE, "Create docs", "# Docs\n")
    env = FileChange(
        ".env.example", ChangeType.CREATE, "Create env", "POSTGRES_PASSWORD=unsafe\n"
    )
    plan = ImplementationPlan("task-repair-env", "Add env docs", (readme, env))

    repaired, repairs = repair_plan_artifacts(plan, specification)

    assert repaired.changes[0] == readme
    assert repaired.changes[1].content == (
        "# Non-secret placeholder for the local database password.\n"
        "POSTGRES_PASSWORD=change_me\n"
    )
    assert repairs


def test_env_example_repair_preserves_quoted_non_secret_values():
    repaired, _ = normalize_env_example('APP_LABEL="Fafas Burger Local" # display label\\n')
    assert 'APP_LABEL="Fafas Burger Local" # display label' in repaired


def test_plan_repair_adds_project_level_acceptance_status_not_just_phase_statuses(tmp_path):
    specification = EngineeringSpecification(
        "spec-project", "task-project", "Create project metadata", str(tmp_path),
        requirements=(Requirement(
            "REQ-PROJECT",
            "Create project.yaml with six phases and an acceptance status.",
        ),),
        architecture=(ArchitectureModule(
            "root", "Root", "Project metadata", file_paths=("project.yaml",),
        ),),
        implementation_units=(ImplementationUnit(
            "UNIT-PROJECT", "Create project metadata", "project.yaml",
            satisfies=("REQ-PROJECT",),
        ),),
    )
    project = FileChange(
        "project.yaml", ChangeType.CREATE, "Create project metadata",
        "product_goal: Build a burger app\\n"
        "phases:\\n"
        "  - phase: A\\n    status: in_progress\\n"
        "  - phase: B\\n    status: not_started\\n"
        "  - phase: C\\n    status: not_started\\n"
        "  - phase: D\\n    status: not_started\\n"
        "  - phase: E\\n    status: not_started\\n"
        "  - phase: F\\n    status: not_started\\n",
    )
    plan = ImplementationPlan("task-project", "Add project metadata", (project,))

    repaired, repairs = repair_plan_artifacts(plan, specification)

    content = repaired.changes[0].content
    assert content.endswith("acceptance_status: pending\n")
    assert "added pending root acceptance_status" in repairs[0]
