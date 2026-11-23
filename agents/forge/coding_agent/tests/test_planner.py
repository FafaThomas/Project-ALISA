import pytest

from coding_agent.models import CodingTask
from coding_agent.planner import CodingPlanner, PlanningError


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.prompt = None

    def generate_json(self, prompt):
        self.prompt = prompt
        return self.response


def _response(content):
    return {
        "summary": "Create greeting module",
        "changes": [
            {
                "path": "hello.py",
                "change_type": "create",
                "reason": "Implement greeting function",
                "content": content,
            }
        ],
        "verification_commands": [],
        "requires_approval": True,
        "risks": [],
    }


def _task():
    return CodingTask(
        task_id="task-test",
        description="Create hello.py",
        workspace="D:/ALISA-TEST",
    )


def test_planner_normalizes_double_escaped_multiline_python():
    client = FakeClient(
        _response(r"def greet():\n    return 'Hello, ALISA!'")
    )

    plan = CodingPlanner(client).plan(_task())

    assert plan.changes[0].content == (
        "def greet():\n    return 'Hello, ALISA!'"
    )
    compile(plan.changes[0].content, "hello.py", "exec")


def test_planner_preserves_literal_newline_escape_in_single_line_string():
    source = r'MESSAGE = "Hello\\nALISA"'
    client = FakeClient(_response(source))

    plan = CodingPlanner(client).plan(_task())

    assert plan.changes[0].content == source


def test_planner_rejects_invalid_python_before_approval():
    client = FakeClient(_response("def greet(:\n    pass\n"))

    with pytest.raises(PlanningError, match="rejected before approval"):
        CodingPlanner(client).plan(_task())


def test_planner_keeps_real_multiline_content_unchanged():
    source = "def greet():\n    return 'Hello, ALISA!'\n"
    client = FakeClient(_response(source))

    plan = CodingPlanner(client).plan(_task())

    assert plan.changes[0].content == source


def test_planner_requests_behavioral_tests_for_acceptance_criteria():
    client = FakeClient(_response("VALUE = 1\n"))
    task = CodingTask(
        task_id="task-acceptance",
        description="Create a greeting function",
        workspace="D:/ALISA-TEST",
        acceptance_criteria=("greet() returns Hello, ALISA!",),
    )

    CodingPlanner(client).plan(task)

    assert "include focused behavioral tests" in client.prompt
    assert "Never claim behavioral correctness based only on syntax compilation" in client.prompt


def test_planner_receives_authoritative_engineering_specification(tmp_path):
    from coding_agent.engineering_spec import (
        EngineeringSpecification, ImplementationUnit, Requirement,
    )

    specification = EngineeringSpecification(
        specification_id="spec-task-test", task_id="task-test",
        objective="Create hello.py", workspace=str(tmp_path),
        requirements=(Requirement("REQ-1", "Provide greeting"),),
        implementation_units=(ImplementationUnit(
            "UNIT-1", "Greeting module", "hello.py", satisfies=("REQ-1",)
        ),),
    )
    client = FakeClient(_response("def greet():\n    return 'Hello'\n"))

    CodingPlanner(client).plan(_task(), specification=specification)

    assert "ENGINEERING SPECIFICATION (AUTHORITATIVE REQUIREMENTS AND DESIGN)" in client.prompt
    assert '"requirement_id": "REQ-1"' in client.prompt



def test_planner_prompt_distinguishes_implementation_units_from_constraints(tmp_path):
    from coding_agent.engineering_spec import (
        EngineeringSpecification, ImplementationUnit, Requirement, RequirementKind,
    )

    specification = EngineeringSpecification(
        specification_id="spec-coverage", task_id="task-test",
        objective="Create service configuration", workspace=str(tmp_path),
        requirements=(
            Requirement("REQ-1", "Create the config file"),
            Requirement(
                "REQ-2", "Do not use SQLite",
                kind=RequirementKind.CONSTRAINT,
            ),
        ),
        implementation_units=(
            ImplementationUnit(
                "UNIT-1", "Create config", "config.yml", satisfies=("REQ-1",)
            ),
        ),
    )
    client = FakeClient(_response("VALUE = 1\n"))

    CodingPlanner(client).plan(_task(), specification=specification)

    assert "Every implementation unit is mandatory" in client.prompt
    assert "A constraint with no implementation-unit mapping is a compliance boundary" in client.prompt
    assert "Do not omit any implementation unit" in client.prompt


def test_planner_prompt_groups_units_by_target_path_without_conflicting_instructions(tmp_path):
    from coding_agent.engineering_spec import (
        ArchitectureModule, EngineeringSpecification, ImplementationUnit, Requirement,
    )

    specification = EngineeringSpecification(
        specification_id="spec-compose-group", task_id="task-test",
        objective="Configure Compose", workspace=str(tmp_path),
        requirements=(
            Requirement("REQ-1", "Define services"),
            Requirement("REQ-2", "Configure PostgreSQL"),
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
        ),
    )
    client = FakeClient(_response("VALUE = 1\n"))

    CodingPlanner(client).plan(_task(), specification=specification)

    prompt = client.prompt
    assert "one operation for each unique target_path" in prompt
    assert "One change DOES cover all units in the same-path group" in prompt
    assert "- FILE: docker-compose.yml" in prompt
    assert "UNIT: UNIT-1 — Define services" in prompt
    assert "UNIT: UNIT-2 — Configure PostgreSQL" in prompt
    assert "\\n- FILE:" not in prompt
    assert "one changed file does not cover sibling units" not in prompt
    assert 'Use mkdir only for an explicitly required DIRECTORY target' in prompt



def test_planner_repeats_per_file_acceptance_obligations_as_readable_lines(tmp_path):
    from coding_agent.engineering_spec import (
        EngineeringSpecification, ImplementationUnit, Requirement,
    )

    specification = EngineeringSpecification(
        specification_id="spec-artifact-obligations", task_id="task-test",
        objective="Create database configuration", workspace=str(tmp_path),
        requirements=(
            Requirement(
                "REQ-DB", "Configure PostgreSQL",
                acceptance_criteria=(
                    "Use a persistent named volume.",
                    "Add a pg_isready health check.",
                ),
            ),
        ),
        implementation_units=(
            ImplementationUnit(
                "UNIT-DB", "Create Compose file", "docker-compose.yml",
                satisfies=("REQ-DB",),
                acceptance_criteria=("Declare the postgres service.",),
            ),
        ),
    )
    client = FakeClient(_response("VALUE = 1\n"))

    CodingPlanner(client).plan(_task(), specification=specification)

    prompt = client.prompt
    assert "- FILE: docker-compose.yml\n" in prompt
    assert "UNIT ACCEPTANCE: Declare the postgres service." in prompt
    assert "REQUIRED RESULT: Use a persistent named volume." in prompt
    assert "REQUIRED RESULT: Add a pg_isready health check." in prompt


def test_planner_includes_concrete_compose_env_and_project_yaml_examples(tmp_path):
    from coding_agent.engineering_spec import (
        ArchitectureModule, EngineeringSpecification, ImplementationUnit, Requirement,
    )

    specification = EngineeringSpecification(
        "spec-wp1-templates", "task-test", "Scaffold Fafa's Burger", str(tmp_path),
        requirements=(
            Requirement("REQ-C", "Create docker-compose.yml with PostgreSQL, a named volume and pg_isready healthcheck."),
            Requirement("REQ-E", "Create .env.example with documented settings and safe placeholders."),
            Requirement("REQ-P", "Create project.yaml with approved stack, architecture, six parent phases and acceptance status."),
        ),
        architecture=(
            ArchitectureModule("root", "Repository", "Root configuration", file_paths=(
                "docker-compose.yml", ".env.example", "project.yaml",
            )),
        ),
        implementation_units=(
            ImplementationUnit("U-C", "Compose", "docker-compose.yml", satisfies=("REQ-C",)),
            ImplementationUnit("U-E", "Environment", ".env.example", satisfies=("REQ-E",)),
            ImplementationUnit("U-P", "Project metadata", "project.yaml", satisfies=("REQ-P",)),
        ),
    )
    client = FakeClient(_response("VALUE = 1\n"))

    CodingPlanner(client).plan(_task(), specification=specification)

    prompt = client.prompt
    assert "postgres_data:/var/lib/postgresql/data" in prompt
    assert "condition: service_healthy" in prompt
    assert "POSTGRES_PASSWORD=change_me" in prompt
    assert "Phase F" in prompt
    assert "phase_1: in_progress" in prompt


def test_planner_separates_validation_feedback_from_workspace_context():
    client = FakeClient(_response("VALUE = 1\n"))
    context = (
        "PROJECT CONTEXT:\nWorkspace inventory: (empty)\n"
        "ENGINEERING SPECIFICATION PLAN VALIDATION FAILURE\n"
        "REQ-1: PostgreSQL volume is not declared at the Compose root\n"
        "PREVIOUS DRAFT FILE CONTENTS:\n[{\"path\":\"docker-compose.yml\",\"content\":\"services: {}\"}]"
    )

    CodingPlanner(client).plan(_task(), context=context)

    prompt = client.prompt
    assert "VALIDATION FEEDBACK (when present" in prompt
    assert "PostgreSQL volume is not declared at the Compose root" in prompt
    assert "PREVIOUS DRAFT FILE CONTENTS" in prompt
    assert "Workspace inventory: (empty)" in prompt


def test_planner_accepts_explicit_directory_creation_operation():
    response = _response("VALUE = 1\n")
    response["changes"].insert(0, {
        "path": "frontend",
        "change_type": "mkdir",
        "reason": "Create the required frontend directory",
        "content": None,
    })
    client = FakeClient(response)

    plan = CodingPlanner(client).plan(_task())

    assert plan.changes[0].change_type.value == "mkdir"
    assert plan.changes[0].path == "frontend"
    assert plan.changes[0].content is None


def test_planner_rejects_directory_creation_with_file_content():
    response = _response("VALUE = 1\n")
    response["changes"].insert(0, {
        "path": "frontend",
        "change_type": "mkdir",
        "reason": "Create the required frontend directory",
        "content": "not allowed",
    })

    with pytest.raises(PlanningError, match="must use content null"):
        CodingPlanner(FakeClient(response)).plan(_task())
