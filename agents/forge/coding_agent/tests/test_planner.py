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
    assert "one planned file change for each unique target_path" in prompt
    assert "One change DOES cover all units in the same-path group" in prompt
    assert "- FILE: docker-compose.yml" in prompt
    assert "UNIT: UNIT-1 — Define services" in prompt
    assert "UNIT: UNIT-2 — Configure PostgreSQL" in prompt
    assert "\\n- FILE:" not in prompt
    assert "one changed file does not cover sibling units" not in prompt
    assert 'Never use "." or a directory-only path' in prompt



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


def test_focused_espec_repair_requests_only_missing_artifact_paths(tmp_path):
    from coding_agent.engineering_spec import (
        EngineeringSpecification, ImplementationUnit, InterfaceContract, InterfaceField, Requirement,
    )
    from coding_agent.models import ChangeType, FileChange, ImplementationPlan

    specification = EngineeringSpecification(
        specification_id="spec-focused-repair",
        task_id="task-test",
        objective="Complete application skeleton",
        workspace=str(tmp_path),
        requirements=(
            Requirement(
                "REQ-COMPOSE",
                "Configure PostgreSQL readiness with a pg_isready healthcheck.",
                acceptance_criteria=("Compose defines the PostgreSQL healthcheck.",),
            ),
            Requirement(
                "REQ-PROJECT",
                "Update project.yaml with approved stack and all parent phases.",
                acceptance_criteria=("All six parent phases are listed.",),
            ),
        ),
        contracts=(
            InterfaceContract(
                "CON-DB", "MOD-ROOT", "Database readiness", "configuration",
                outputs=(InterfaceField("health", "boolean"),),
                invariants=("PostgreSQL must pass pg_isready before dependents start.",),
            ),
        ),
        implementation_units=(
            ImplementationUnit(
                "UNIT-COMPOSE", "Configure Compose healthcheck", "docker-compose.yml",
                satisfies=("REQ-COMPOSE",), implements=("CON-DB",),
            ),
            ImplementationUnit(
                "UNIT-PROJECT", "Update project metadata", "project.yaml",
                satisfies=("REQ-PROJECT",),
            ),
        ),
    )
    task = _task()
    previous = ImplementationPlan(
        task_id=task.task_id,
        summary="Initial application plan",
        changes=(
            FileChange(
                "frontend/src/App.tsx", ChangeType.CREATE,
                "Create frontend shell", "export default function App() { return null; }\\n",
            ),
        ),
    )
    responses = [
        {
            "summary": "Repair Compose",
            "changes": [{
                "path": "docker-compose.yml",
                "change_type": "create",
                "reason": "Add PostgreSQL readiness healthcheck",
                "content": "services:\\n  postgres:\\n    image: postgres:17\\n",
            }],
        },
        {
            "summary": "Repair project metadata",
            "changes": [{
                "path": "project.yaml",
                "change_type": "create",
                "reason": "Record approved project metadata",
                "content": "goal: Burger app\\nphases: []\\n",
            }],
        },
    ]

    class SequencedClient:
        def __init__(self):
            self.prompts = []

        def generate_json(self, prompt):
            self.prompts.append(prompt)
            return responses[len(self.prompts) - 1]

    client = SequencedClient()
    repaired = CodingPlanner(client).repair_missing_changes(
        task,
        context="WORKSPACE FILE INVENTORY: README.md exists",
        specification=specification,
        existing_plan=previous,
        required_paths=("docker-compose.yml", "project.yaml"),
        issues=("REQ-COMPOSE: pg_isready healthcheck is missing",),
    )

    assert [item.path for item in repaired] == ["docker-compose.yml", "project.yaml"]
    # One model call per target file.
    assert len(client.prompts) == 2
    first, second = client.prompts
    for prompt in client.prompts:
        assert "FOCUSED FILE REPAIR" in prompt
        assert "Return exactly one FileChange for every TARGET FILE" in prompt
        assert "frontend/src/App.tsx" not in prompt
    assert "TARGET FILE: docker-compose.yml" in first
    assert "TARGET FILE: project.yaml" not in first
    assert "TARGET FILE: project.yaml" in second
    assert "TARGET FILE: docker-compose.yml" not in second
    # The later call sees the file repaired earlier in the same pass.
    assert "FILES ALREADY REPAIRED IN THIS PASS" in second
    assert "FILE: docker-compose.yml" in second
    compose_change = next(item for item in repaired if item.path == "docker-compose.yml")
    assert "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}" in compose_change.content


def test_focused_espec_repair_rejects_omitted_target_path(tmp_path):
    from coding_agent.engineering_spec import (
        EngineeringSpecification, ImplementationUnit, Requirement,
    )
    from coding_agent.models import ImplementationPlan

    specification = EngineeringSpecification(
        specification_id="spec-focused-repair-strict",
        task_id="task-test",
        objective="Create config and docs",
        workspace=str(tmp_path),
        requirements=(Requirement("REQ-1", "Create configuration"),),
        implementation_units=(
            ImplementationUnit("UNIT-1", "Create config", "config.yml", satisfies=("REQ-1",)),
            ImplementationUnit("UNIT-2", "Create docs", "README.md", satisfies=("REQ-1",)),
        ),
    )
    client = FakeClient({
        "summary": "Incomplete repair",
        "changes": [{
            "path": "config.yml",
            "change_type": "create",
            "reason": "Create configuration",
            "content": "enabled: true\\n",
        }],
    })

    with pytest.raises(PlanningError, match="must return exactly the requested paths"):
        CodingPlanner(client).repair_missing_changes(
            _task(),
            context="",
            specification=specification,
            existing_plan=ImplementationPlan(
                task_id="task-test",
                summary="Initial",
                changes=(),
            ),
            required_paths=("config.yml", "README.md"),
            issues=("README is missing",),
        )


def test_single_file_repair_keeps_the_requested_file_and_ignores_extras(tmp_path):
    from coding_agent.engineering_spec import (
        EngineeringSpecification, ImplementationUnit, Requirement,
    )
    from coding_agent.models import ImplementationPlan

    specification = EngineeringSpecification(
        specification_id="spec-focused-repair-extras",
        task_id="task-test",
        objective="Create config and docs",
        workspace=str(tmp_path),
        requirements=(Requirement("REQ-1", "Create configuration"),),
        implementation_units=(
            ImplementationUnit("UNIT-1", "Create config", "config.yml", satisfies=("REQ-1",)),
            ImplementationUnit("UNIT-2", "Create docs", "README.md", satisfies=("REQ-1",)),
        ),
    )
    # The model was asked only for config.yml but also re-sent README.md.
    client = FakeClient({
        "summary": "Repair config",
        "changes": [
            {"path": "config.yml", "change_type": "create",
             "reason": "Create configuration", "content": "enabled: true\n"},
            {"path": "README.md", "change_type": "create",
             "reason": "Unrequested", "content": "# Docs\n"},
        ],
    })

    repaired = CodingPlanner(client).repair_missing_changes(
        _task(),
        context="",
        specification=specification,
        existing_plan=ImplementationPlan(task_id="task-test", summary="Initial", changes=()),
        required_paths=("config.yml",),
        issues=("config.yml is missing",),
    )

    assert [item.path for item in repaired] == ["config.yml"]
