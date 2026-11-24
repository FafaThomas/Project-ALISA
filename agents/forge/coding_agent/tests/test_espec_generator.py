import json

import pytest

from coding_agent.engineering_spec import ArchitectureModule, ImplementationUnit, Requirement, RequirementKind, UnitKind, VerificationKind

from coding_agent.espec_generator import (
    EngineeringSpecificationGenerator,
    SpecificationGenerationError,
    integration_json_schema,
)


def responses():
    return [
        {"requirements": [{
            "requirement_id": "REQ-001",
            "description": "Users can add tasks",
            "kind": "functional",
            "acceptance_criteria": ["A submitted task appears in the list"],
            "priority": "high",
        }]},
        {"architecture": [{
            "module_id": "MOD-001", "name": "Task UI",
            "responsibility": "Render and manage task interactions",
            "depends_on": [], "file_paths": ["src/App.jsx", "src/TaskForm.jsx", "tests/task_validation.py"],
        }]},
        {"contracts": [{
            "contract_id": "CON-001", "owner": "MOD-001",
            "name": "AddTask", "kind": "component",
            "inputs": [{"name": "onAddTask", "type_name": "function",
                        "required": True, "description": "Creates a task"}],
            "outputs": [], "invariants": [],
        }]},
        {"implementation_units": [{
            "unit_id": "UNIT-001", "title": "Implement task form",
            "target_path": "src/TaskForm.jsx", "kind": "source",
            "depends_on": [], "satisfies": ["REQ-001"],
            "implements": ["CON-001"],
            "acceptance_criteria": ["Submit invokes onAddTask"],
        }], "verification": [{
            "criterion_id": "VER-001", "description": "Task form behavior test",
            "kind": "behavioral", "required": True, "command": ["npm", "test"],
            "covers": ["REQ-001", "UNIT-001"],
        }]}
    ]


class FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.prompts = []

    def generate_json(self, prompt):
        self.prompts.append(prompt)
        result = self.payloads.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_generates_complete_task_scoped_specification():
    client = FakeClient(responses())
    generator = EngineeringSpecificationGenerator(client)
    spec = generator.generate(
        task_id="task-123",
        objective="Build a task dashboard",
        workspace="D:/workspace",
    )
    assert spec.task_id == "task-123"
    assert spec.execution_profile == "sandbox"
    assert spec.requirements[0].requirement_id == "REQ-001"
    assert spec.implementation_units[0].implements == ("CON-001",)
    assert spec.status.value == "validated"
    assert len(generator.last_trace) == 4
    assert all(item.success for item in generator.last_trace)
    assert len(client.prompts) == 4


def test_generation_stages_receive_prior_stage_context():
    client = FakeClient(responses())
    EngineeringSpecificationGenerator(client).generate(
        task_id="task-1", objective="Build app", workspace="/workspace"
    )
    assert "REQ-001" in client.prompts[1]
    assert "MOD-001" in client.prompts[2]
    assert "CON-001" in client.prompts[3]
    assert "IDENTIFIER NAMESPACES ARE DISTINCT" in client.prompts[3]
    assert "depends_on contains ONLY implementation unit IDs" in client.prompts[3]
    assert "implements contains ONLY contract IDs" in client.prompts[3]
    assert "NEVER put a CON-ID in depends_on" in client.prompts[3]
    assert "verification-only obligation" in client.prompts[0]
    assert "are verification-only constraints" in client.prompts[0]


def test_integration_json_schema_documents_identifier_namespaces():
    payloads = responses()
    from coding_agent.engineering_spec import (
        ArchitectureModule, InterfaceContract, Requirement, RequirementKind
    )

    requirement = Requirement(
        requirement_id="REQ-001",
        description="Create task form",
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=("Task form appears",),
        priority="high",
    )
    module = ArchitectureModule(
        module_id="MOD-001",
        name="Task UI",
        responsibility="Render task interactions",
        depends_on=(),
        file_paths=("src/TaskForm.jsx",),
    )
    contract = InterfaceContract(
        contract_id="CON-001",
        owner="MOD-001",
        name="AddTask",
        kind="component",
        inputs=(),
        outputs=(),
        invariants=(),
    )
    schema = integration_json_schema([requirement], [module], [contract])
    description = schema["properties"]["implementation_units"]["items"]["properties"]["depends_on"]["description"]
    assert "unit_id" in description
    assert "Never put CON-IDs here" in description
    assert "implements" in description


def test_retries_invalid_stage_with_diagnostic():
    payloads = responses()
    payloads[0] = {"requirements": []}
    payloads.insert(1, responses()[0])
    client = FakeClient(payloads)
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=2)
    spec = generator.generate(task_id="t", objective="Build app", workspace="/w")
    assert spec.requirements
    assert generator.last_trace[0].attempts == 2
    assert "PREVIOUS OUTPUT INVALID" in client.prompts[1]


def test_fails_after_bounded_retries():
    client = FakeClient([{"requirements": []}, {"requirements": []}])
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=2)
    try:
        generator.generate(task_id="t", objective="Build app", workspace="/w")
    except SpecificationGenerationError as exc:
        assert "requirements stage failed after 2" in str(exc)
    else:
        raise AssertionError("expected bounded generation failure")


def test_integration_retries_when_contract_id_is_misused_as_unit_dependency():
    payloads = responses()
    invalid_integration = json.loads(json.dumps(payloads[3]))
    invalid_integration["implementation_units"][0]["depends_on"] = ["CON-001"]
    client = FakeClient(payloads[:3] + [invalid_integration, payloads[3]])
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=2)

    spec = generator.generate(task_id="t", objective="Build app", workspace="/w")

    assert spec.status.value == "validated"
    assert generator.last_trace[-1].stage == "integration"
    assert generator.last_trace[-1].attempts == 2
    assert "depends_on must contain implementation unit_id values only" in client.prompts[4]
    assert "put contract references in implements" in client.prompts[4]


def test_retries_then_rejects_uncovered_requirement():
    first = responses()[3]
    first["implementation_units"][0]["satisfies"] = []
    second = responses()[3]
    second["implementation_units"][0]["satisfies"] = []
    client = FakeClient(responses()[:3] + [first, second])
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=2)
    try:
        generator.generate(task_id="t", objective="Build app", workspace="/w")
    except SpecificationGenerationError as exc:
        assert "lack implementation-unit coverage" in str(exc)
    else:
        raise AssertionError("expected coverage validation failure")
    assert generator.last_trace[-1].stage == "integration"
    assert generator.last_trace[-1].success is False


def test_rejects_contract_owner_not_in_architecture():
    payloads = responses()
    payloads[2]["contracts"][0]["owner"] = "MOD-DOES-NOT-EXIST"
    payloads.insert(3, responses()[2])
    client = FakeClient(payloads)
    generator = EngineeringSpecificationGenerator(client)
    try:
        generator.generate(task_id="t", objective="Build app", workspace="/w")
    except Exception:
        raise
    assert generator.last_trace[2].attempts == 2
    assert generator.last_trace[2].success


def test_invalid_constructor_configuration():
    client = FakeClient(responses())
    for attempts in (0, 6):
        try:
            EngineeringSpecificationGenerator(client, max_stage_attempts=attempts)
        except ValueError:
            pass
        else:
            raise AssertionError("expected max_stage_attempts validation")


def test_rejects_empty_task_metadata():
    generator = EngineeringSpecificationGenerator(FakeClient(responses()))
    try:
        generator.generate(task_id="", objective="Build app", workspace="/w")
    except ValueError as exc:
        assert "task_id" in str(exc)
    else:
        raise AssertionError("expected task id validation")



def test_integration_stage_retries_when_requirement_coverage_is_incomplete():
    first = responses()[3]
    first["implementation_units"][0]["satisfies"] = []
    client = FakeClient(responses()[:3] + [first, responses()[3]])
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=2)

    spec = generator.generate(task_id="t", objective="Build app", workspace="/w")

    assert spec.status.value == "validated"
    assert generator.last_trace[-1].stage == "integration"
    assert generator.last_trace[-1].attempts == 2
    assert generator.last_trace[-1].success is True
    assert "requirements lack implementation-unit coverage" in client.prompts[4]
    # The repair request must include the prior response, not merely tell the
    # model to preserve it. generate_json is stateless between calls.
    assert '"target_path": "src/TaskForm.jsx"' in client.prompts[4]
    assert '"satisfies": []' in client.prompts[4]


def test_verification_coverage_can_be_inferred_through_covered_unit():
    payload = responses()[3]
    payload["verification"][0]["covers"] = ["UNIT-001"]
    client = FakeClient(responses()[:3] + [payload])
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=2)

    spec = generator.generate(task_id="t", objective="Build app", workspace="/w")

    assert spec.status.value == "validated"
    assert spec.verification[0].covers == ("UNIT-001",)
    assert len(client.prompts) == 4
    assert generator.last_trace[-1].attempts == 1


def test_verification_coverage_supports_direct_and_indirect_requirement_mapping():
    payload = responses()[3]
    payload["implementation_units"].append({
        "unit_id": "UNIT-002", "title": "Additional task validation",
        "target_path": "tests/task_validation.py", "kind": "test",
        "depends_on": [], "satisfies": ["REQ-001"],
        "implements": [], "acceptance_criteria": ["Reject invalid task input"],
    })
    payload["verification"][0]["covers"] = ["UNIT-002"]
    client = FakeClient(responses()[:3] + [payload])

    spec = EngineeringSpecificationGenerator(client).generate(
        task_id="t", objective="Build app", workspace="/w"
    )

    assert spec.status.value == "validated"
    assert spec.verification[0].covers == ("UNIT-002",)


def test_integration_stage_retries_when_target_path_is_missing():
    first = responses()[3]
    first["implementation_units"][0]["target_path"] = ""
    client = FakeClient(responses()[:3] + [first, responses()[3]])
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=2)

    spec = generator.generate(task_id="t", objective="Build app", workspace="/w")

    assert spec.implementation_units[0].target_path == "src/TaskForm.jsx"
    assert "target_path" in client.prompts[4]


def test_integration_stage_exhaustion_records_trace_and_reports_uncovered_requirements():
    first = responses()[3]
    first["implementation_units"][0]["satisfies"] = []
    second = responses()[3]
    second["implementation_units"][0]["satisfies"] = []
    client = FakeClient(responses()[:3] + [first, second])
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=2)

    try:
        generator.generate(task_id="t", objective="Build app", workspace="/w")
    except SpecificationGenerationError as exc:
        assert "integration stage failed after 2" in str(exc)
        assert "REQ-001" in str(exc)
    else:
        raise AssertionError("expected bounded integration failure")

    assert generator.last_trace[-1].stage == "integration"
    assert generator.last_trace[-1].success is False
    assert generator.last_trace[-1].attempts == 2


def test_common_verification_kind_aliases_normalize_to_canonical_values():
    aliases = {
        "test": "behavioral",
        "api": "behavioral",
        "unit": "behavioral",
        "integration": "behavioral",
        "e2e": "behavioral",
        "api-test": "behavioral",
        "lint": "static",
        "type_check": "static",
        "structure": "structural",
        "compile": "build",
        "smoke": "runtime",
        "human_review": "manual",
        " STATIC ": "static",
    }
    for alias, expected in aliases.items():
        payloads = responses()
        payloads[3]["verification"][0]["kind"] = alias
        if expected == "manual":
            payloads[3]["verification"][0]["command"] = []
        spec = EngineeringSpecificationGenerator(FakeClient(payloads)).generate(
            task_id="t", objective="Build app", workspace="/w"
        )
        assert spec.verification[0].kind.value == expected, alias


def test_unknown_verification_kind_reports_supported_values():
    payloads = responses()
    payloads[3]["verification"][0]["kind"] = "not_a_real_kind"
    client = FakeClient(payloads + [payloads[3]])
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=1)
    try:
        generator.generate(task_id="t", objective="Build app", workspace="/w")
    except SpecificationGenerationError as exc:
        message = str(exc)
        assert "invalid verification kind" in message
        assert "behavioral" in message and "structural" in message
    else:
        raise AssertionError("expected invalid verification kind")



def test_verification_kind_alias_does_not_expand_canonical_enum():
    from coding_agent.engineering_spec import VerificationKind

    assert {item.value for item in VerificationKind} == {
        "static", "structural", "build", "behavioral", "runtime", "manual"
    }
    assert EngineeringSpecificationGenerator._parse_verification_kind("api").value == "behavioral"


def test_integration_retries_when_unit_kind_is_requirement_kind():
    invalid = responses()[3]
    invalid["implementation_units"][0]["kind"] = "constraint"
    corrected = responses()[3]
    client = FakeClient(responses()[:3] + [invalid, corrected])

    spec = EngineeringSpecificationGenerator(
        client, max_stage_attempts=2
    ).generate(task_id="t", objective="Build app", workspace="/w")

    assert spec.status.value == "validated"
    assert spec.implementation_units[0].kind.value == "source"
    assert "implementation_units[0].kind" in client.prompts[4]
    assert "RequirementKind" in client.prompts[4]
    assert "configuration" in client.prompts[4]


def test_invalid_implementation_unit_kind_reports_allowed_values():
    payloads = responses()
    payloads[3]["implementation_units"][0]["kind"] = "constraint"
    client = FakeClient(payloads)
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=1)

    try:
        generator.generate(task_id="t", objective="Build app", workspace="/w")
    except SpecificationGenerationError as exc:
        message = str(exc)
        assert "implementation_units[0].kind" in message
        assert "'constraint'" in message
        assert "source" in message and "configuration" in message
        assert "RequirementKind" in message
        assert "not an ImplementationUnit kind" in message
    else:
        raise AssertionError("expected invalid implementation-unit kind")


def test_unit_kind_enum_remains_artifact_oriented():
    from coding_agent.engineering_spec import UnitKind

    assert {item.value for item in UnitKind} == {
        "source", "test", "configuration", "documentation", "asset"
    }


def test_integration_assigns_deterministic_id_when_model_omits_criterion_id():
    payload = responses()[3]
    payload["verification"][0].pop("criterion_id")
    client = FakeClient(responses()[:3] + [payload])

    spec = EngineeringSpecificationGenerator(client).generate(
        task_id="t", objective="Build app", workspace="/w"
    )

    assert spec.status.value == "validated"
    assert spec.verification[0].criterion_id == "VER-001"
    assert spec.verification[0].covers == ("REQ-001", "UNIT-001")


def test_integration_accepts_verification_id_alias():
    payload = responses()[3]
    payload["verification"][0]["verification_id"] = payload["verification"][0].pop("criterion_id")
    client = FakeClient(responses()[:3] + [payload])

    spec = EngineeringSpecificationGenerator(client).generate(
        task_id="t", objective="Build app", workspace="/w"
    )

    assert spec.verification[0].criterion_id == "VER-001"



def _multi_requirement_payloads():
    requirements = {"requirements": [
        {"requirement_id": "REQ-001", "description": "Create root, frontend, and backend .dockerignore files.",
         "kind": "functional", "acceptance_criteria": [
             "A .dockerignore file exists in the root directory.",
             "A .dockerignore file exists in the frontend/ directory.",
             "A .dockerignore file exists in the backend/ directory."], "priority": "high"},
        {"requirement_id": "REQ-002", "description": "No unrelated application features are introduced.",
         "kind": "constraint", "acceptance_criteria": ["No unrelated application features are introduced."],
         "priority": "high"},
    ]}
    architecture = {"architecture": [{
        "module_id": "MOD-001", "name": "Repository", "responsibility": "Repository structure",
        "depends_on": [], "file_paths": [".dockerignore", "frontend/.dockerignore", "backend/.dockerignore"]
    }]}
    contracts = {"contracts": [{
        "contract_id": "CON-001", "owner": "MOD-001", "name": "RepositoryLayout", "kind": "data",
        "inputs": [], "outputs": [], "invariants": []
    }]}
    units = [
        {"unit_id": "UNIT-001", "title": "Root ignore rules", "target_path": ".dockerignore",
         "kind": "configuration", "depends_on": [], "satisfies": ["REQ-001"],
         "implements": [], "acceptance_criteria": ["Root ignore file exists"]},
        {"unit_id": "UNIT-002", "title": "Frontend ignore rules", "target_path": "frontend/.dockerignore",
         "kind": "configuration", "depends_on": [], "satisfies": ["REQ-001"],
         "implements": [], "acceptance_criteria": ["Frontend ignore file exists"]},
        {"unit_id": "UNIT-003", "title": "Backend ignore rules", "target_path": "backend/.dockerignore",
         "kind": "configuration", "depends_on": [], "satisfies": ["REQ-001"],
         "implements": [], "acceptance_criteria": ["Backend ignore file exists"]},
    ]
    verification = {"verification": [{
        "criterion_id": "VER-001", "description": "Verify all ignore files and ensure no unrelated features exist",
        "kind": "structural", "required": True, "command": [], "covers": ["REQ-001", "REQ-002"]
    }]}
    return requirements, architecture, contracts, {"implementation_units": units, **verification}


def test_architecture_auto_adds_explicit_root_file_to_root_module(tmp_path):
    import json

    payloads = responses()
    payloads[0] = {"requirements": [{
        "requirement_id": "REQ-001", "description": "Create root docker-compose.yml.",
        "kind": "functional", "acceptance_criteria": ["The root docker-compose.yml file exists."],
        "priority": "high"
    }]}
    payloads[1] = {"architecture": [{
        "module_id": "MOD-001", "name": "Root", "responsibility": "Root configuration",
        "depends_on": [], "file_paths": ["README.md"]
    }]}
    payloads[3]["implementation_units"][0]["target_path"] = "docker-compose.yml"
    diagnostics = tmp_path / "architecture-diagnostics"
    client = FakeClient(payloads)
    generator = EngineeringSpecificationGenerator(client, diagnostics_dir=diagnostics)

    spec = generator.generate(
        task_id="t", objective="Create root docker-compose.yml", workspace="/w"
    )

    assert spec.status.value == "validated"
    root_module = next(module for module in spec.architecture if module.name == "Root")
    assert set(root_module.file_paths) == {"README.md", "docker-compose.yml"}
    assert generator.last_trace[1].attempts == 1
    assert "MANDATORY FILE PATHS DERIVED FROM THE REQUIREMENTS" in client.prompts[1]
    assert "- docker-compose.yml" in client.prompts[1]
    repairs = json.loads(
        (diagnostics / "architecture_path_repairs.json").read_text(encoding="utf-8")
    )
    assert repairs == [{
        "path": "docker-compose.yml",
        "requirement_ids": ["REQ-001"],
        "module_id": "MOD-001",
        "module_name": "Root",
        "reason": "explicitly required path omitted by model; assigned to its unambiguous owning module",
    }]


def test_service_appropriate_dockerignore_paths_use_directories_from_other_requirements():
    from coding_agent.engineering_spec import Requirement, RequirementKind

    requirements = [
        Requirement(
            requirement_id="REQ-001",
            description="Establish separate frontend/, backend/, and postgres/ areas.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(),
            priority="high",
        ),
        Requirement(
            requirement_id="REQ-006",
            description="Add root and service-appropriate .dockerignore files.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(
                "Root directory contains a .dockerignore file.",
                "Each service directory contains a .dockerignore file.",
            ),
            priority="normal",
        ),
    ]

    required = EngineeringSpecificationGenerator._required_architecture_paths(requirements)

    assert required[".dockerignore"] == {"REQ-006"}
    assert required["frontend/.dockerignore"] == {"REQ-006"}
    assert required["backend/.dockerignore"] == {"REQ-006"}
    assert required["postgres/.dockerignore"] == {"REQ-006"}


def test_architecture_path_repair_refuses_ambiguous_root_module():
    from coding_agent.engineering_spec import Requirement, RequirementKind

    requirement = Requirement(
        requirement_id="REQ-001",
        description="Create root docker-compose.yml.",
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=("The root docker-compose.yml file exists.",),
        priority="high",
    )
    generator = EngineeringSpecificationGenerator(FakeClient([]))

    try:
        generator._parse_architecture(
            {
                "architecture": [{
                    "module_id": "MOD-001",
                    "name": "Frontend",
                    "responsibility": "Frontend service",
                    "depends_on": [],
                    "file_paths": ["frontend/Dockerfile"],
                }]
            },
            [requirement],
        )
    except ValueError as exc:
        assert "no unambiguous owning architecture module" in str(exc)
    else:
        raise AssertionError("ambiguous ownership must not be guessed")


def test_constraint_requires_verification_but_not_an_implementation_unit():
    req, arch, contracts, integration = _multi_requirement_payloads()
    req["requirements"] = [req["requirements"][1]]
    integration["implementation_units"] = []
    # A constraint is verified directly; no implementation unit is required.
    integration["verification"][0]["covers"] = ["REQ-002"]
    client = FakeClient([req, arch, contracts, integration])
    spec = EngineeringSpecificationGenerator(client).generate(
        task_id="t", objective="Respect scope constraint", workspace="/w"
    )
    assert spec.status.value == "validated"
    assert spec.implementation_units == ()
    assert "REQ-002" in spec.verification[0].covers


def test_coverage_validator_still_rejects_constraint_without_verification():
    req, arch, contracts, integration = _multi_requirement_payloads()
    requirement = Requirement(
        requirement_id="REQ-002",
        description="No unrelated application features are introduced.",
        kind=RequirementKind.CONSTRAINT,
        acceptance_criteria=("No unrelated application features are introduced.",),
        priority="high",
    )
    try:
        EngineeringSpecificationGenerator._validate_integration_coverage(
            [], [], [requirement], architecture_paths=set()
        )
    except ValueError as exc:
        assert "requirements lack verification coverage: ['REQ-002']" in str(exc)
    else:
        raise AssertionError("the authoritative coverage validator must remain strict")


def test_multi_file_requirement_completes_one_unit_per_architecture_file():
    req, arch, contracts, incomplete = _multi_requirement_payloads()
    incomplete["implementation_units"] = incomplete["implementation_units"][:1]
    client = FakeClient([req, arch, contracts, incomplete])
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=2)
    spec = generator.generate(
        task_id="t", objective="Create ignore files", workspace="/w"
    )
    paths = {unit.target_path for unit in spec.implementation_units}
    assert paths == {".dockerignore", "frontend/.dockerignore", "backend/.dockerignore"}
    assert len(client.prompts) == 4
    assert generator.last_trace[-1].attempts == 1
    assert {
        unit.target_path: unit.satisfies
        for unit in spec.implementation_units
    } == {
        ".dockerignore": ("REQ-001",),
        "frontend/.dockerignore": ("REQ-001",),
        "backend/.dockerignore": ("REQ-001",),
    }


def test_implementation_target_path_must_belong_to_architecture():
    payloads = responses()
    payloads[3]["implementation_units"][0]["target_path"] = "src/not_in_architecture.jsx"
    client = FakeClient(payloads + [responses()[3]])
    spec = EngineeringSpecificationGenerator(client, max_stage_attempts=2).generate(
        task_id="t", objective="Build app", workspace="/w"
    )
    assert spec.implementation_units[0].target_path == "src/TaskForm.jsx"
    assert "is not an architecture file path or its parent directory" in client.prompts[4]


def test_empty_target_path_error_identifies_unit_and_field():
    payloads = responses()
    payloads[3]["implementation_units"][0]["target_path"] = ""
    client = FakeClient(payloads + [responses()[3]])
    spec = EngineeringSpecificationGenerator(client, max_stage_attempts=2).generate(
        task_id="t", objective="Build app", workspace="/w"
    )
    assert spec.status.value == "validated"
    assert "UNIT-001" in client.prompts[4]
    assert "target_path must be a non-empty string" in client.prompts[4]
    assert "Preserve all previously valid IDs" in client.prompts[4]
    assert "PREVIOUS OUTPUT (the exact response" in client.prompts[4]
    assert '"title": "Implement task form"' in client.prompts[4]
    assert '"target_path": ""' in client.prompts[4]


def test_integration_prompt_defines_workspace_relative_target_paths():
    client = FakeClient(responses())
    EngineeringSpecificationGenerator(client).generate(
        task_id="t", objective="Build app", workspace="/w"
    )
    prompt = client.prompts[3]
    assert "relative to the workspace root" in prompt
    assert 'Never prepend "root/"' in prompt
    assert "target_path MUST exactly match one concrete file path" in prompt
    assert 'Do not use "." as an implementation target' in prompt
    assert "Do NOT create implementation units for workspace inspection" in prompt
    assert "one implementation unit per unique target_path" in prompt


def test_failed_stage_persists_raw_attempts_validation_errors_and_trace(tmp_path):
    import json
    first, second = responses()[3], responses()[3]
    first["implementation_units"][0]["satisfies"] = []
    second["implementation_units"][0]["satisfies"] = []
    diagnostics = tmp_path / "espec-diagnostics"
    generator = EngineeringSpecificationGenerator(
        FakeClient(responses()[:3] + [first, second]),
        max_stage_attempts=2, diagnostics_dir=diagnostics,
    )
    try:
        generator.generate(task_id="diagnostic-task", objective="Build app", workspace="/w")
    except SpecificationGenerationError as exc:
        assert "REQ-001" in str(exc)
    else:
        raise AssertionError("expected integration coverage failure")
    raw1 = json.loads((diagnostics / "integration_attempt_1.json").read_text(encoding="utf-8"))
    raw2 = json.loads((diagnostics / "integration_attempt_2.json").read_text(encoding="utf-8"))
    errors = json.loads((diagnostics / "validation_errors.json").read_text(encoding="utf-8"))
    trace = json.loads((diagnostics / "generation_trace.json").read_text(encoding="utf-8"))
    assert raw1["implementation_units"][0]["satisfies"] == []
    assert raw2["implementation_units"][0]["satisfies"] == []
    assert len(errors) == 2 and all(x["stage"] == "integration" for x in errors)
    assert trace[-1]["stage"] == "integration" and trace[-1]["success"] is False


def test_diagnostic_io_failure_does_not_mask_espec_failure(tmp_path):
    first = responses()[3]
    first["implementation_units"][0]["satisfies"] = []
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block", encoding="utf-8")
    generator = EngineeringSpecificationGenerator(
        FakeClient(responses()[:3] + [first]), max_stage_attempts=1,
        diagnostics_dir=blocker / "child",
    )
    try:
        generator.generate(task_id="diagnostic-task", objective="Build app", workspace="/w")
    except SpecificationGenerationError as exc:
        assert "REQ-001" in str(exc)
    else:
        raise AssertionError("expected coverage failure")


def test_explicit_requirement_paths_excludes_prohibited_real_env_file():
    requirement = Requirement(
        requirement_id="REQ-005",
        description=(
            "Create safe root .env.example values and document each non-secret setting. "
            "Do not create a real .env containing secrets."
        ),
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=(
            "The root directory contains a .env.example file.",
            "No real .env file containing secrets is created.",
        ),
        priority="high",
    )

    paths = EngineeringSpecificationGenerator._explicit_requirement_paths(requirement)

    assert ".env.example" in paths
    assert ".env" not in paths


def test_architecture_accepts_env_example_without_prohibited_real_env():
    requirement = Requirement(
        requirement_id="REQ-005",
        description=(
            "Create safe root .env.example values. "
            "Do not create a real .env containing secrets."
        ),
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=(
            "The root directory contains a .env.example file.",
            "No real .env file containing secrets is created.",
        ),
        priority="high",
    )
    generator = EngineeringSpecificationGenerator(FakeClient([]))

    architecture = generator._parse_architecture(
        {
            "architecture": [{
                "module_id": "MOD-001",
                "name": "Root",
                "responsibility": "Root configuration",
                "depends_on": [],
                "file_paths": [".env.example"],
            }]
        },
        [requirement],
    )

    assert architecture[0].file_paths == (".env.example",)


def test_bare_filenames_resolve_against_unique_architecture_paths():
    requirements = [
        Requirement(
            "REQ-README",
            "Complete README setup, startup, and testing instructions.",
            acceptance_criteria=("README is updated with the required instructions.",),
        ),
        Requirement(
            "REQ-PROJECT",
            "Update project.yaml to reflect the actual phase status.",
            acceptance_criteria=("project.yaml records the current status.",),
        ),
    ]

    required = EngineeringSpecificationGenerator._required_architecture_paths(
        requirements, {"README.md", "project.yaml", "backend/settings.py"}
    )

    assert required["README.md"] == {"REQ-README"}
    assert required["project.yaml"] == {"REQ-PROJECT"}


def test_prohibited_readme_is_not_inferred_as_a_required_artifact():
    requirement = Requirement(
        "REQ-NO-README",
        "Do not create README.md.",
        acceptance_criteria=("README.md must not be added.",),
    )

    assert not EngineeringSpecificationGenerator._explicit_requirement_basenames(
        requirement
    )


def test_bare_filename_is_not_guessed_when_architecture_path_is_ambiguous():
    requirement = Requirement(
        "REQ-CONFIG",
        "Update settings.py with the required configuration.",
        acceptance_criteria=("settings.py contains the configuration.",),
    )

    required = EngineeringSpecificationGenerator._required_architecture_paths(
        [requirement],
        {"backend/settings.py", "frontend/settings.py"},
    )

    assert "backend/settings.py" not in required
    assert "frontend/settings.py" not in required


def test_explicitly_required_real_env_file_is_still_detected():
    requirement = Requirement(
        requirement_id="REQ-ENV",
        description="Create a root .env file with safe local development defaults.",
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=("The root directory contains a .env file.",),
        priority="high",
    )

    paths = EngineeringSpecificationGenerator._explicit_requirement_paths(requirement)

    assert ".env" in paths



def test_manual_verification_moves_natural_language_command_entries_into_description():
    payloads = responses()
    payloads[3]["verification"][0]["kind"] = "manual"
    payloads[3]["verification"][0]["command"] = [
        "Check that the root directory contains frontend/ and backend/.",
        "Verify that the README describes the setup workflow.",
    ]
    client = FakeClient(payloads)

    spec = EngineeringSpecificationGenerator(client).generate(
        task_id="manual-instructions", objective="Build app", workspace="/w"
    )

    criterion = spec.verification[0]
    assert criterion.kind.value == "manual"
    assert criterion.command == ()
    assert "Task form behavior test" in criterion.description
    assert "Manual verification steps:" in criterion.description
    assert "- Check that the root directory contains frontend/ and backend/." in criterion.description
    assert "- Verify that the README describes the setup workflow." in criterion.description
    assert "command MUST be an empty array" in client.prompts[3]


def test_manual_verification_moves_executable_command_suggestions_into_description(tmp_path):
    payloads = responses()
    payloads[3]["verification"][0]["kind"] = "manual"
    payloads[3]["verification"][0]["command"] = ["python", "-m", "pytest"]
    client = FakeClient(payloads)

    spec = EngineeringSpecificationGenerator(
        client, diagnostics_dir=tmp_path
    ).generate(
        task_id="manual-executable-suggestion", objective="Build app", workspace="/w"
    )

    criterion = spec.verification[0]
    assert criterion.kind.value == "manual"
    assert criterion.command == ()
    assert "Suggested reviewer commands (not executed by ALISA):" in criterion.description
    assert "- `python -m pytest`" in criterion.description
    repairs = json.loads(
        (tmp_path / "integration_completion_repairs.json").read_text(encoding="utf-8")
    )
    assert repairs["verification"][0]["action"] == "move_manual_command_suggestions_to_description"



def test_report_titled_documentation_unit_is_not_treated_as_verification_only():
    from coding_agent.engineering_spec import UnitKind

    unit = ImplementationUnit(
        unit_id="UNIT-REPORT",
        title="Report changed files, verification results, and known limitations",
        target_path="README.md",
        kind=UnitKind.DOCUMENTATION,
        satisfies=("REQ-REPORT",),
    )

    assert not EngineeringSpecificationGenerator._is_verification_only_unit(unit)


def test_manual_command_suggestions_preserve_multiple_shell_commands_separately():
    suggestions = EngineeringSpecificationGenerator._format_manual_command_suggestions(
        ("ls backend/", "curl http://localhost:8000/health/", "cat docker-compose.yml")
    )
    assert suggestions == (
        "ls backend/",
        "curl http://localhost:8000/health/",
        "cat docker-compose.yml",
    )


def test_integration_completion_repairs_file_mappings_and_adds_missing_service_units(tmp_path):
    import json
    from coding_agent.engineering_spec import (
        ArchitectureModule, InterfaceContract, VerificationKind,
    )

    requirements = [
        Requirement(
            requirement_id="REQ-002",
            description="Establish separate frontend/, backend/, and postgres/ areas.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(
                "Directories frontend/, backend/, and postgres/ exist in the root.",
            ),
            priority="high",
        ),
        Requirement(
            requirement_id="REQ-003",
            description="Create root docker-compose.yml.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("docker-compose.yml exists.",),
            priority="high",
        ),
        Requirement(
            requirement_id="REQ-006",
            description="Create safe root .env.example values and document each non-secret setting.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(".env.example exists and contains safe defaults.",),
            priority="high",
        ),
        Requirement(
            requirement_id="REQ-007",
            description="Add root and service-appropriate .dockerignore files.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(
                "Root and each service directory contain a .dockerignore file.",
                "Files exclude unnecessary files and directories for Docker builds.",
            ),
            priority="high",
        ),
        Requirement(
            requirement_id="REQ-008",
            description="Create the initial root project.yaml describing the project.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("project.yaml exists and describes the approved stack.",),
            priority="high",
        ),
        Requirement(
            requirement_id="REQ-009",
            description="Create a minimal root README.md covering setup.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("README.md covers prerequisites and environment setup.",),
            priority="high",
        ),
    ]
    architecture = [
        ArchitectureModule(
            module_id="MOD-001", name="Root/Repository",
            responsibility="Root project configuration", depends_on=(),
            file_paths=(
                "docker-compose.yml", ".env.example", ".dockerignore",
                "project.yaml", "README.md",
            ),
        ),
        ArchitectureModule(
            module_id="MOD-002", name="Frontend", responsibility="Frontend",
            depends_on=(), file_paths=("frontend/.dockerignore",),
        ),
        ArchitectureModule(
            module_id="MOD-003", name="Backend", responsibility="Backend",
            depends_on=(), file_paths=("backend/.dockerignore",),
        ),
        ArchitectureModule(
            module_id="MOD-004", name="PostgreSQL", responsibility="PostgreSQL",
            depends_on=(), file_paths=("postgres/.dockerignore",),
        ),
    ]
    generator = EngineeringSpecificationGenerator(FakeClient([]), diagnostics_dir=tmp_path)
    generator._known_module_ids = {module.module_id for module in architecture}
    units_raw = [
        ("UNIT-001", "Create root directory layout", ".", "REQ-002"),
        ("UNIT-002", "Compose", "docker-compose.yml", "REQ-003"),
        ("UNIT-003", "Environment example", ".env.example", "REQ-003"),
        ("UNIT-004", "Ignore rules", ".dockerignore", "REQ-003"),
        ("UNIT-005", "Project manifest", "project.yaml", "REQ-007"),
        ("UNIT-006", "Readme", "README.md", "REQ-007"),
    ]
    integration = {
        "implementation_units": [
            {
                "unit_id": unit_id, "title": title, "target_path": path,
                "kind": "documentation" if path == "README.md" else "configuration",
                "depends_on": [], "satisfies": [requirement_id],
                "implements": [], "acceptance_criteria": ["Create the named artifact."],
            }
            for unit_id, title, path, requirement_id in units_raw
        ],
        "verification": [{
            "criterion_id": "CRIT-001",
            "description": "Verify Docker Compose syntax",
            "kind": "manual", "required": True, "command": [],
            "covers": ["REQ-003", "REQ-006"],
        }],
    }

    units, verification = generator._parse_integration(
        integration, requirements, architecture
    )

    by_path = {unit.target_path: unit for unit in units}
    assert by_path[".env.example"].satisfies == ("REQ-006",)
    assert by_path[".dockerignore"].satisfies == ("REQ-007",)
    assert by_path["project.yaml"].satisfies == ("REQ-008",)
    assert by_path["README.md"].satisfies == ("REQ-009",)
    assert by_path["docker-compose.yml"].satisfies == ("REQ-003",)
    assert {
        "frontend/.dockerignore", "backend/.dockerignore", "postgres/.dockerignore"
    }.issubset(by_path)
    for path in ("frontend/.dockerignore", "backend/.dockerignore", "postgres/.dockerignore"):
        assert set(by_path[path].satisfies) == {"REQ-007", "REQ-002"}
        assert by_path[path].kind.value == "configuration"

    # The false REQ-006 reference is removed from the Compose criterion.
    compose_criterion = next(item for item in verification if item.criterion_id == "CRIT-001")
    assert compose_criterion.covers == ("REQ-003",)
    covered = {
        reference for criterion in verification for reference in criterion.covers
    }
    assert {item.requirement_id for item in requirements}.issubset(covered)

    repairs = json.loads(
        (tmp_path / "integration_completion_repairs.json").read_text(encoding="utf-8")
    )
    assert any(
        item["action"] == "remove_misplaced_requirement_mapping"
        and item["requirement_id"] == "REQ-003"
        and item["wrong_target_path"] == ".env.example"
        for item in repairs["implementation_units"]
    )
    assert not any(
        item["action"] == "remap_unit_requirement"
        for item in repairs["implementation_units"]
    )
    assert sum(
        item["action"] == "create_required_file_unit"
        for item in repairs["implementation_units"]
    ) == 3
    assert any(
        item["action"] == "remove_unrelated_verification_coverage"
        and "REQ-006" in item["removed_requirement_ids"]
        for item in repairs["verification"]
    )
    assert any(
        item["action"] == "create_requirement_verification"
        and item["covers"] == ["REQ-006"]
        for item in repairs["verification"]
    )


def test_missing_non_file_implementation_requirement_is_not_fabricated():
    requirements = [
        Requirement(
            requirement_id="REQ-001",
            description="Implement a reliable application capability.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("The capability works reliably.",),
            priority="high",
        )
    ]
    units, repairs = EngineeringSpecificationGenerator._complete_required_file_units(
        [], requirements, set()
    )
    assert units == []
    assert repairs == []


def test_generated_manual_verification_is_explicit_and_has_no_command():
    from coding_agent.engineering_spec import VerificationKind

    requirement = Requirement(
        requirement_id="REQ-011",
        description="Verify Compose syntax if Docker is available.",
        kind=RequirementKind.CONSTRAINT,
        acceptance_criteria=(
            "If Docker is available, Compose syntax is verified.",
            "If Docker is unavailable, report the exact unavailability and provide the command for the user.",
        ),
        priority="high",
    )
    verification, repairs = EngineeringSpecificationGenerator._complete_verification_coverage(
        [], [], [requirement]
    )
    assert len(verification) == 1
    assert verification[0].kind == VerificationKind.MANUAL
    assert verification[0].command == ()
    assert verification[0].covers == ("REQ-011",)
    assert "If Docker is available" in verification[0].description
    assert repairs[0]["action"] == "create_requirement_verification"



def test_root_readme_without_extension_resolves_to_canonical_readme_md():
    requirement = Requirement(
        requirement_id="REQ-009",
        description="Create a minimal root README section covering prerequisites and setup.",
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=("README file exists in the root.",),
        priority="high",
    )
    paths = EngineeringSpecificationGenerator._explicit_requirement_paths(requirement)
    assert "README.md" in paths


def test_sqlite_runtime_prohibition_is_normalized_to_constraint(tmp_path):
    generator = EngineeringSpecificationGenerator(
        FakeClient([]), diagnostics_dir=tmp_path
    )
    requirements = generator._parse_requirements({
        "requirements": [{
            "requirement_id": "REQ-010",
            "description": "Keep the PostgreSQL application configuration aligned with PostgreSQL.",
            "kind": "non_functional",
            "acceptance_criteria": [
                "PostgreSQL configuration does not use SQLite for runtime."
            ],
            "priority": "high",
        }]
    })

    assert requirements[0].kind == RequirementKind.CONSTRAINT
    repairs = json.loads((tmp_path / "requirements_normalization.json").read_text())
    normalized = json.loads((tmp_path / "requirements_normalized.json").read_text())
    assert normalized["requirements"][0]["kind"] == "constraint"
    assert repairs == [{
        "requirement_id": "REQ-010",
        "previous_kind": "non_functional",
        "kind": "constraint",
        "reason": (
            "the requirement only prohibits SQLite for runtime and does not "
            "direct implementation of an application database configuration"
        ),
    }]


def test_positive_postgresql_configuration_task_remains_implementation_requirement():
    generator = EngineeringSpecificationGenerator(FakeClient([]))
    requirements = generator._parse_requirements({
        "requirements": [{
            "requirement_id": "REQ-003",
            "description": "Configure Django to connect to PostgreSQL through environment variables.",
            "kind": "functional",
            "acceptance_criteria": [
                "Django database settings use PostgreSQL and environment variables."
            ],
            "priority": "high",
        }]
    })

    assert requirements[0].kind == RequirementKind.FUNCTIONAL


def test_compose_startup_dependency_requirement_inherits_declared_compose_target():
    requirements = [
        Requirement(
            requirement_id="REQ-003",
            description="Create the root docker-compose.yml with frontend, backend, and PostgreSQL services.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("docker-compose.yml defines the required services.",),
            priority="high",
        ),
        Requirement(
            requirement_id="REQ-005",
            description="Configure service startup dependencies using health conditions where supported.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("Service startup dependencies are configured using health conditions.",),
            priority="high",
        ),
    ]

    required_paths = EngineeringSpecificationGenerator._required_architecture_paths(requirements)

    assert required_paths["docker-compose.yml"] == {"REQ-003", "REQ-005"}


def test_compose_startup_dependency_gap_gets_dedicated_unit():
    requirements = [
        Requirement(
            requirement_id="REQ-003",
            description="Create root docker-compose.yml with the required services.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("docker-compose.yml defines the required services.",),
            priority="high",
        ),
        Requirement(
            requirement_id="REQ-005",
            description="Configure service startup dependencies using health conditions where supported.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("Service startup dependencies are configured using health conditions.",),
            priority="high",
        ),
    ]
    existing_units = [
        ImplementationUnit(
            unit_id="UNIT-003",
            title="Create Docker Compose services",
            target_path="docker-compose.yml",
            kind="configuration",
            satisfies=("REQ-003",),
            acceptance_criteria=("Define frontend, backend, and PostgreSQL services.",),
        )
    ]

    units, repairs = EngineeringSpecificationGenerator._complete_required_file_units(
        existing_units, requirements, {"docker-compose.yml"}
    )

    startup_units = [unit for unit in units if "REQ-005" in unit.satisfies]
    assert len(startup_units) == 1
    assert startup_units[0].target_path == "docker-compose.yml"
    assert startup_units[0].unit_id == "UNIT-003"
    assert len([unit for unit in units if unit.target_path == "docker-compose.yml"]) == 1
    assert any(item["action"] == "augment_shared_file_unit" for item in repairs)


def test_requirements_prompt_demands_concrete_artifact_targets():
    client = FakeClient(responses())
    EngineeringSpecificationGenerator(client).generate(
        task_id="prompt-targets", objective="Build a task dashboard", workspace="/workspace"
    )

    prompt = client.prompts[0]
    assert "negative technology prohibition as a constraint" in prompt
    assert "name the concrete file or artifact" in prompt
    assert "docker-compose.yml" in prompt


def test_compose_startup_dependency_path_inference_requires_declared_compose_artifact():
    requirements = [
        Requirement(
            requirement_id="REQ-005",
            description="Configure service startup dependencies using health conditions where supported.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("Service startup dependencies use health conditions.",),
            priority="high",
        )
    ]

    assert EngineeringSpecificationGenerator._required_architecture_paths(requirements) == {}


def test_bare_filename_requirement_is_merged_into_existing_shared_file_unit():
    requirements = [
        Requirement(
            requirement_id="REQ-002",
            description="Create the root docker-compose.yml.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("docker-compose.yml defines frontend, backend, and PostgreSQL services.",),
        ),
        Requirement(
            requirement_id="REQ-003",
            description="Configure PostgreSQL with environment-driven credentials, a persistent named volume, and a real readiness health check.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(
                "PostgreSQL service in docker-compose.yml uses environment variables for credentials.",
                "PostgreSQL service has a persistent named volume.",
                "PostgreSQL service has a readiness health check.",
            ),
        ),
        Requirement(
            requirement_id="REQ-004",
            description="Configure service startup dependencies using health conditions.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("Service startup dependencies are configured using health conditions in docker-compose.yml.",),
        ),
    ]
    existing = [
        ImplementationUnit(
            unit_id="UNIT-004",
            title="Create root docker-compose.yml",
            target_path="docker-compose.yml",
            kind="configuration",
            satisfies=("REQ-002", "REQ-004"),
            acceptance_criteria=(
                "docker-compose.yml defines services for frontend, backend, and PostgreSQL.",
                "Service startup dependencies are configured using health conditions.",
            ),
        )
    ]

    units, repairs = EngineeringSpecificationGenerator._complete_required_file_units(
        existing, requirements, {"docker-compose.yml"}
    )

    assert len(units) == 1, "shared target path must not produce a second writer"
    compose = units[0]
    assert set(compose.satisfies) == {"REQ-002", "REQ-003", "REQ-004"}
    assert all(item in compose.acceptance_criteria for item in requirements[1].acceptance_criteria)
    assert any(
        item["action"] == "augment_shared_file_unit"
        and item["target_path"] == "docker-compose.yml"
        and "REQ-003" in item["satisfies"]
        for item in repairs
    )


def test_directory_requirement_maps_to_child_artifact_units_for_every_directory():
    requirements = [
        Requirement(
            requirement_id="REQ-001",
            description="Establish a clean root layout with separate frontend/, backend/, and postgres/ areas.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(
                "Directories frontend/, backend/, and postgres/ exist in the root of the workspace.",
            ),
        ),
        Requirement(
            requirement_id="REQ-006",
            description="Add root and service-appropriate .dockerignore files.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(
                ".dockerignore files exist in the root, frontend/, backend/, and postgres/ directories.",
            ),
        ),
    ]
    paths = {
        ".dockerignore",
        "frontend/.dockerignore",
        "backend/.dockerignore",
        "postgres/.dockerignore",
    }
    existing = [
        ImplementationUnit(
            unit_id=f"UNIT-{index:03d}",
            title=f"Create {path}",
            target_path=path,
            kind="configuration",
            satisfies=("REQ-006",),
            acceptance_criteria=(f"{path} exists.",),
        )
        for index, path in enumerate(sorted(paths), start=1)
    ]

    units, repairs = EngineeringSpecificationGenerator._complete_required_file_units(
        existing, requirements, paths
    )

    for directory in ("frontend", "backend", "postgres"):
        child = next(unit for unit in units if unit.target_path == f"{directory}/.dockerignore")
        assert "REQ-001" in child.satisfies
        assert f"Directory {directory}/ exists in the workspace." in child.acceptance_criteria

    assert sum(
        item["action"] == "map_directory_requirement_to_child_unit"
        and item["requirement_id"] == "REQ-001"
        for item in repairs
    ) == 3


def test_directory_coverage_validator_requires_a_unit_beneath_each_named_directory():
    from coding_agent.engineering_spec import VerificationCriterion, VerificationKind

    requirement = Requirement(
        requirement_id="REQ-001",
        description="Establish separate frontend/, backend/, and postgres/ directories.",
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=("Directories frontend/, backend/, and postgres/ exist.",),
    )
    units = [
        ImplementationUnit(
            unit_id="UNIT-001",
            title="Create frontend ignore file",
            target_path="frontend/.dockerignore",
            kind="configuration",
            satisfies=("REQ-001",),
        ),
        ImplementationUnit(
            unit_id="UNIT-002",
            title="Create backend ignore file",
            target_path="backend/.dockerignore",
            kind="configuration",
            satisfies=("REQ-001",),
        ),
    ]
    verification = [
        VerificationCriterion(
            criterion_id="VER-001",
            description="Manually verify all required directories exist.",
            kind=VerificationKind.MANUAL,
            covers=("REQ-001",),
        )
    ]

    try:
        EngineeringSpecificationGenerator._validate_integration_coverage(
            units,
            verification,
            [requirement],
            architecture_paths={
                "frontend/.dockerignore",
                "backend/.dockerignore",
                "postgres/.dockerignore",
            },
        )
    except ValueError as exc:
        assert "directory requirements lack implementation units beneath every required directory" in str(exc)
        assert "REQ-001: postgres/" in str(exc)
    else:
        raise AssertionError("every explicitly required directory must have implementation coverage")


def test_generic_root_directory_file_check_does_not_claim_directory_layout_coverage():
    from coding_agent.engineering_spec import VerificationCriterion, VerificationKind

    requirement = Requirement(
        requirement_id="REQ-001",
        description="Establish a clean root layout with separate frontend/, backend/, and postgres/ areas.",
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=(
            "Directories frontend/, backend/, and postgres/ exist in the root of the workspace.",
        ),
    )
    criterion = VerificationCriterion(
        criterion_id="VER-001",
        description="Check if .dockerignore file exists in the root directory.",
        kind=VerificationKind.STATIC,
        command=("ls", ".dockerignore"),
        covers=("REQ-001",),
    )

    repaired, repairs = EngineeringSpecificationGenerator._complete_verification_coverage(
        [criterion], [], [requirement]
    )

    assert "REQ-001" not in repaired[0].covers
    assert any(
        item["action"] == "remove_unrelated_verification_coverage"
        and item["criterion_id"] == "VER-001"
        and "REQ-001" in item["removed_requirement_ids"]
        for item in repairs
    )
    assert any(
        item.kind == VerificationKind.MANUAL and item.covers == ("REQ-001",)
        for item in repaired
    )


def test_bare_filename_is_not_mapped_when_architecture_context_is_ambiguous():
    requirements = [
        Requirement(
            requirement_id="REQ-001",
            description="Create root docker-compose.yml.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("docker-compose.yml exists in the root.",),
        ),
        Requirement(
            requirement_id="REQ-002",
            description="Create frontend/docker-compose.yml.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("frontend/docker-compose.yml exists.",),
        ),
        Requirement(
            requirement_id="REQ-003",
            description="Verify docker-compose.yml syntax.",
            kind=RequirementKind.CONSTRAINT,
            acceptance_criteria=("docker-compose.yml syntax is checked.",),
        ),
    ]

    mapping = EngineeringSpecificationGenerator._required_architecture_paths(requirements)

    assert mapping["docker-compose.yml"] == {"REQ-001"}
    assert mapping["frontend/docker-compose.yml"] == {"REQ-002"}
    assert "REQ-003" not in mapping["docker-compose.yml"]


def test_integration_normalizes_root_verification_and_shared_path_units(tmp_path):
    from coding_agent.engineering_spec import (
        ArchitectureModule, InterfaceContract, Requirement, RequirementKind,
    )

    requirements = [
        Requirement("REQ-001", "Establish frontend/, backend/, and postgres/ directories."),
        Requirement("REQ-002", "Create the root docker-compose.yml with all required services."),
        Requirement("REQ-003", "Configure PostgreSQL in docker-compose.yml with credentials, volume, and health check."),
        Requirement("REQ-004", "Configure service startup dependencies using health conditions."),
        Requirement("REQ-010", "Verify Compose syntax if Docker is available; otherwise report unavailability.",
                    kind=RequirementKind.CONSTRAINT),
        Requirement("REQ-011", "Ensure no unrelated application features are introduced.",
                    kind=RequirementKind.CONSTRAINT),
    ]
    architecture = [
        ArchitectureModule(
            "MOD-ROOT", "Root", "Root configuration",
            file_paths=("docker-compose.yml", ".dockerignore"),
        ),
        ArchitectureModule(
            "MOD-FE", "Frontend", "Frontend files",
            file_paths=("frontend/.dockerignore",),
        ),
        ArchitectureModule(
            "MOD-BE", "Backend", "Backend files",
            file_paths=("backend/.dockerignore",),
        ),
        ArchitectureModule(
            "MOD-DB", "Postgres", "Postgres files",
            file_paths=("postgres/.dockerignore",),
        ),
    ]
    contracts = [
        InterfaceContract("CON-INSPECT", "MOD-ROOT", "InspectWorkspace", "function"),
        InterfaceContract("CON-LAYOUT", "MOD-ROOT", "CreateRootLayout", "function"),
        InterfaceContract("CON-COMPOSE", "MOD-ROOT", "CreateCompose", "function"),
        InterfaceContract("CON-PG", "MOD-ROOT", "ConfigurePostgres", "function"),
        InterfaceContract("CON-DEPS", "MOD-ROOT", "ConfigureDependencies", "function"),
        InterfaceContract("CON-VERIFY", "MOD-ROOT", "VerifyComposeSyntax", "function"),
        InterfaceContract("CON-SCOPE", "MOD-ROOT", "EnsureNoUnrelatedFeatures", "function"),
    ]
    generator = EngineeringSpecificationGenerator(FakeClient([]), diagnostics_dir=tmp_path)
    generator._known_module_ids = {module.module_id for module in architecture}
    integration = {
        "implementation_units": [
            {
                "unit_id": "UNIT-001", "title": "Inspect Workspace and Create Root Layout",
                "target_path": ".", "kind": "configuration", "depends_on": [],
                "satisfies": ["REQ-001"], "implements": ["CON-INSPECT", "CON-LAYOUT"],
                "acceptance_criteria": [
                    "The workspace is inspected.",
                    "Directories frontend/, backend/, and postgres/ exist.",
                ],
            },
            {
                "unit_id": "UNIT-002", "title": "Create Compose",
                "target_path": "docker-compose.yml", "kind": "configuration",
                "depends_on": ["UNIT-001"], "satisfies": ["REQ-002"],
                "implements": ["CON-COMPOSE"],
                "acceptance_criteria": ["Define frontend, backend, and postgres services."],
            },
            {
                "unit_id": "UNIT-003", "title": "Configure PostgreSQL",
                "target_path": "docker-compose.yml", "kind": "configuration",
                "depends_on": ["UNIT-002"], "satisfies": ["REQ-002"],
                "implements": ["CON-PG"],
                "acceptance_criteria": ["Configure PostgreSQL credentials, volume, and health check."],
            },
            {
                "unit_id": "UNIT-004", "title": "Configure Service Dependencies",
                "target_path": "docker-compose.yml", "kind": "configuration",
                "depends_on": ["UNIT-003"], "satisfies": ["REQ-002"],
                "implements": ["CON-DEPS"],
                "acceptance_criteria": ["Use health-conditioned startup dependencies."],
            },
            {
                "unit_id": "UNIT-005", "title": "Verify Docker Compose Syntax",
                "target_path": "docker-compose.yml", "kind": "configuration",
                "depends_on": ["UNIT-002"], "satisfies": ["REQ-002"],
                "implements": ["CON-VERIFY"],
                "acceptance_criteria": ["If Docker is available, verify Compose syntax."],
            },
            {
                "unit_id": "UNIT-006", "title": "Ensure No Unrelated Features",
                "target_path": ".", "kind": "configuration",
                "depends_on": ["UNIT-001"], "satisfies": ["REQ-002"],
                "implements": ["CON-SCOPE"],
                "acceptance_criteria": ["No unrelated application features are introduced."],
            },
            *[
                {
                    "unit_id": f"UNIT-{index:03d}",
                    "title": f"Create {directory} ignore file",
                    "target_path": f"{directory}/.dockerignore",
                    "kind": "configuration", "depends_on": ["UNIT-001"],
                    "satisfies": ["REQ-001"],
                    "implements": [],
                    "acceptance_criteria": [f"Directory {directory}/ exists."],
                }
                for index, directory in enumerate(("backend", "frontend", "postgres"), start=7)
            ],
            {
                "unit_id": "UNIT-010", "title": "Create root ignore file",
                "target_path": ".dockerignore", "kind": "configuration",
                "depends_on": ["UNIT-001"], "satisfies": ["REQ-001"],
                "implements": [], "acceptance_criteria": ["Root .dockerignore exists."],
            },
        ],
        "verification": [
            {
                "criterion_id": "VER-001",
                "description": "Verify workspace layout.",
                "kind": "manual", "required": True, "command": [],
                "covers": ["REQ-001"],
            },
            {
                "criterion_id": "VER-002",
                "description": "Verify PostgreSQL Compose configuration and readiness.",
                "kind": "manual", "required": True, "command": [],
                "covers": ["REQ-003"],
            },
        ],
    }

    units, verification = generator._parse_integration(
        integration, requirements, architecture, contracts
    )

    assert len(units) == 5
    by_path = {unit.target_path: unit for unit in units}
    assert set(by_path) == {
        "docker-compose.yml", "backend/.dockerignore",
        "frontend/.dockerignore", "postgres/.dockerignore",
        ".dockerignore",
    }
    compose = by_path["docker-compose.yml"]
    assert set(compose.satisfies) == {"REQ-002", "REQ-003", "REQ-004"}
    assert set(compose.implements) == {"CON-COMPOSE", "CON-PG", "CON-DEPS"}
    assert len(compose.acceptance_criteria) == 3
    assert all(unit.target_path != "." for unit in units)
    assert all("UNIT-001" not in unit.depends_on for unit in units)
    assert set(by_path["backend/.dockerignore"].satisfies) == {"REQ-001"}
    assert set(by_path["frontend/.dockerignore"].satisfies) == {"REQ-001"}
    assert set(by_path["postgres/.dockerignore"].satisfies) == {"REQ-001"}
    assert all(
        "CON-LAYOUT" in by_path[path].implements
        for path in (
            ".dockerignore", "backend/.dockerignore",
            "frontend/.dockerignore", "postgres/.dockerignore",
        )
    )
    assert "CON-INSPECT" not in {
        contract_id for unit in units for contract_id in unit.implements
    }

    verification_by_covers = {
        reference: criterion
        for criterion in verification
        for reference in criterion.covers
        if reference.startswith("REQ-")
    }
    assert "REQ-010" in verification_by_covers
    assert "REQ-011" in verification_by_covers
    assert any(
        item["action"] == "merge_same_target_path_units"
        and item["target_path"] == "docker-compose.yml"
        for item in json.loads(
            (tmp_path / "integration_completion_repairs.json").read_text()
        )["implementation_units"]
    )


def test_constraint_file_reference_does_not_become_an_implementation_path():
    from coding_agent.engineering_spec import Requirement, RequirementKind

    requirements = [
        Requirement(
            "REQ-1", "Create the root docker-compose.yml with required services.",
            kind=RequirementKind.FUNCTIONAL,
        ),
        Requirement(
            "REQ-2", "Verify docker-compose.yml syntax if Docker is available.",
            kind=RequirementKind.CONSTRAINT,
        ),
    ]

    mapping = EngineeringSpecificationGenerator._required_architecture_paths(
        requirements
    )

    assert mapping == {"docker-compose.yml": {"REQ-1"}}


def test_manual_verification_drops_instructions_that_ask_reviewer_to_create_files():
    from coding_agent.engineering_spec import VerificationCriterion, VerificationKind

    criteria = [
        VerificationCriterion(
            "CR-001",
            "Instructions: Manually create docker-compose.yml and define services.",
            VerificationKind.MANUAL,
            covers=("REQ-001",),
        ),
        VerificationCriterion(
            "VER-001",
            "Review docker-compose.yml and confirm the PostgreSQL health check is valid.",
            VerificationKind.MANUAL,
            covers=("REQ-002",),
        ),
    ]

    retained, repairs = EngineeringSpecificationGenerator._filter_manual_implementation_instructions(
        criteria
    )

    assert [item.criterion_id for item in retained] == ["VER-001"]
    assert repairs == [{
        "action": "replace_manual_implementation_instructions",
        "criterion_id": "CR-001",
        "covers": ["REQ-001"],
        "reason": (
            "manual verification must inspect completed artifacts; "
            "requirement acceptance criteria will be emitted as verification checklists"
        ),
    }]


def test_architecture_drops_unrequested_service_dockerignore_path():
    from coding_agent.engineering_spec import Requirement, RequirementKind

    generator = EngineeringSpecificationGenerator(FakeClient([]))
    requirements = [
        Requirement("REQ-001", "Establish frontend/, backend/, and postgres/ areas."),
        Requirement("REQ-006", "Add root and service-appropriate .dockerignore files."),
    ]
    architecture = generator._parse_architecture({"architecture": [
        {"module_id": "MOD-ROOT", "name": "Root", "responsibility": "Root files", "depends_on": [], "file_paths": [".dockerignore"]},
        {"module_id": "MOD-CMD", "name": "Commands", "responsibility": "Commands service", "depends_on": [], "file_paths": ["commands/.dockerignore"]},
        {"module_id": "MOD-FE", "name": "frontend", "responsibility": "Frontend", "depends_on": [], "file_paths": ["frontend/.dockerignore"]},
        {"module_id": "MOD-BE", "name": "backend", "responsibility": "Backend", "depends_on": [], "file_paths": ["backend/.dockerignore"]},
        {"module_id": "MOD-DB", "name": "postgres", "responsibility": "Database", "depends_on": [], "file_paths": ["postgres/.dockerignore"]},
    ]}, requirements)
    paths = {path for module in architecture for path in module.file_paths}
    assert ".dockerignore" in paths
    assert "commands/.dockerignore" not in paths


def test_explicit_add_tests_requirement_is_normalized_to_functional():
    from coding_agent.engineering_spec import RequirementKind
    generator = EngineeringSpecificationGenerator(FakeClient([]))
    requirements = generator._parse_requirements({"requirements": [{
        "requirement_id": "REQ-TEST", "description": "Add meaningful tests for introduced behavior.",
        "kind": "constraint", "acceptance_criteria": ["Meaningful tests cover the new behavior."],
        "priority": "high",
    }]})
    assert requirements[0].kind == RequirementKind.FUNCTIONAL


def test_reporting_phrase_commands_results_does_not_invent_commands_directory():
    requirements = [
        Requirement("REQ-LAYOUT", "Establish frontend/, backend/, and postgres/ areas."),
        Requirement("REQ-IGNORE", "Add root and service-appropriate .dockerignore files."),
        Requirement("REQ-REPORT", "Report exact verification commands/results at the end."),
    ]
    paths = EngineeringSpecificationGenerator._required_architecture_paths(requirements)
    assert "frontend/.dockerignore" in paths
    assert "backend/.dockerignore" in paths
    assert "postgres/.dockerignore" in paths
    assert "commands/.dockerignore" not in paths


def test_architecture_repairs_missing_test_artifact_path_for_explicit_test_requirement():
    from coding_agent.engineering_spec import Requirement
    generator = EngineeringSpecificationGenerator(FakeClient([]))
    requirements = [Requirement(
        "REQ-TEST", "Add meaningful tests for introduced behavior.",
        acceptance_criteria=("Tests cover the new behavior.",),
    )]
    architecture = generator._parse_architecture({"architecture": [{
        "module_id": "MOD-ROOT", "name": "Root", "responsibility": "Project files",
        "depends_on": [], "file_paths": ["README.md"],
    }]}, requirements)
    assert "tests/test_req_test.py" in architecture[0].file_paths


def test_architecture_repairs_backend_and_frontend_smoke_test_paths_to_owning_modules():
    from coding_agent.engineering_spec import Requirement
    generator = EngineeringSpecificationGenerator(FakeClient([]))
    requirements = [
        Requirement(
            "REQ-STACK",
            "Create a Django backend under backend/ and a React TypeScript frontend under frontend/.",
            acceptance_criteria=(
                "Django backend is created under backend/.",
                "React TypeScript frontend is created under frontend/.",
            ),
        ),
        Requirement(
            "REQ-TEST",
            "Add basic backend and frontend smoke tests where practical.",
            acceptance_criteria=(
                "Basic backend smoke tests are added.",
                "Basic frontend smoke tests are added.",
            ),
        ),
    ]
    architecture = generator._parse_architecture({"architecture": [
        {
            "module_id": "MOD-ROOT", "name": "Root", "responsibility": "Project files",
            "depends_on": [], "file_paths": ["README.md"],
        },
        {
            "module_id": "MOD-BACKEND", "name": "Backend", "responsibility": "Django API",
            "depends_on": [], "file_paths": ["backend/manage.py"],
        },
        {
            "module_id": "MOD-FRONTEND", "name": "Frontend", "responsibility": "React UI",
            "depends_on": [], "file_paths": ["frontend/src/App.tsx"],
        },
    ]}, requirements)
    paths_by_module = {module.module_id: set(module.file_paths) for module in architecture}
    assert "backend/tests/test_smoke.py" in paths_by_module["MOD-BACKEND"]
    assert "frontend/tests/smoke.test.tsx" in paths_by_module["MOD-FRONTEND"]
    assert "backend/tests/test_smoke.py" not in paths_by_module["MOD-ROOT"]
    assert "frontend/tests/smoke.test.tsx" not in paths_by_module["MOD-ROOT"]


def test_architecture_accepts_colocated_typescript_test_file():
    from coding_agent.engineering_spec import Requirement
    generator = EngineeringSpecificationGenerator(FakeClient([]))
    requirements = [Requirement(
        "REQ-TEST", "Add meaningful tests for introduced behavior.",
        acceptance_criteria=("Tests cover the new behavior.",),
    )]
    architecture = generator._parse_architecture({"architecture": [{
        "module_id": "MOD-ROOT", "name": "Root", "responsibility": "Project files",
        "depends_on": [], "file_paths": ["README.md", "src/App.test.tsx"],
    }]}, requirements)
    assert "tests/test_req_test.py" in architecture[0].file_paths
    assert "src/App.test.tsx" in architecture[0].file_paths


def test_architecture_accepts_explicit_test_artifact_path():
    from coding_agent.engineering_spec import Requirement
    generator = EngineeringSpecificationGenerator(FakeClient([]))
    requirements = [Requirement(
        "REQ-TEST", "Add meaningful tests for introduced behavior.",
        acceptance_criteria=("Tests cover the new behavior.",),
    )]
    architecture = generator._parse_architecture({"architecture": [{
        "module_id": "MOD-ROOT", "name": "Root", "responsibility": "Project files",
        "depends_on": [], "file_paths": ["README.md", "tests/test_work_package.py"],
    }]}, requirements)
    assert "tests/test_work_package.py" in architecture[0].file_paths


def test_postgresql_service_configuration_maps_to_compose_not_dockerignore():
    from coding_agent.engineering_spec import (
        ArchitectureModule,
        InterfaceContract,
        ImplementationUnit,
        Requirement,
        UnitKind,
    )

    requirements = (
        Requirement(
            "REQ-002",
            "Create the root docker-compose.yml with frontend, backend, and PostgreSQL services.",
        ),
        Requirement(
            "REQ-003",
            "Configure PostgreSQL with environment-driven credentials, a persistent named volume, and a real readiness health check.",
            acceptance_criteria=(
                "PostgreSQL should use environment variables for credentials.",
                "PostgreSQL should have a persistent named volume.",
                "PostgreSQL should have a readiness health check.",
            ),
        ),
    )
    architecture_paths = {
        "docker-compose.yml",
        "postgres/.dockerignore",
    }
    contracts = (
        InterfaceContract(
            "CON-003",
            "MOD-004",
            "ConfigurePostgreSQL",
            "function",
            invariants=(
                "PostgreSQL should use environment variables for credentials.",
                "PostgreSQL should have a persistent named volume.",
                "PostgreSQL should have a readiness health check.",
            ),
        ),
    )
    units = (
        ImplementationUnit(
            "UNIT-004",
            "Create root docker-compose.yml",
            "docker-compose.yml",
            kind=UnitKind.CONFIGURATION,
            satisfies=("REQ-002",),
            acceptance_criteria=("Define the required services.",),
        ),
        ImplementationUnit(
            "UNIT-008",
            "Create postgres/.dockerignore",
            "postgres/.dockerignore",
            kind=UnitKind.CONFIGURATION,
            satisfies=("REQ-003",),
            implements=("CON-003",),
            acceptance_criteria=("Create the Postgres ignore file.",),
        ),
    )

    required_paths = EngineeringSpecificationGenerator._required_architecture_paths(
        requirements
    )
    assert "REQ-003" in required_paths["docker-compose.yml"]

    completed, _ = EngineeringSpecificationGenerator._complete_required_file_units(
        units, requirements, architecture_paths
    )
    normalized, repairs = EngineeringSpecificationGenerator._normalize_requirement_file_mappings(
        completed, requirements, architecture_paths, contracts
    )

    compose = next(
        item for item in normalized if item.target_path == "docker-compose.yml"
    )
    postgres_ignore = next(
        item for item in normalized if item.target_path == "postgres/.dockerignore"
    )
    assert "REQ-003" in compose.satisfies
    assert "CON-003" in compose.implements
    assert "REQ-003" not in postgres_ignore.satisfies
    assert "CON-003" not in postgres_ignore.implements
    assert any(
        item["action"] == "remove_misplaced_requirement_mapping"
        for item in repairs
    )


def test_required_architecture_paths_accept_sentence_punctuation_and_map_overlapping_requirements():
    requirements = [
        Requirement(
            "REQ-002",
            "Establish frontend/, backend/, and postgres/ directories.",
            RequirementKind.FUNCTIONAL,
            ("The directory structure includes frontend/, backend/, and postgres/ directories.",),
            "high",
        ),
        Requirement(
            "REQ-003",
            "Create the root docker-compose.yml.",
            RequirementKind.FUNCTIONAL,
            ("docker-compose.yml is created at the root level.",),
            "high",
        ),
        Requirement(
            "REQ-004",
            "Configure PostgreSQL with environment-driven credentials.",
            RequirementKind.FUNCTIONAL,
            ("PostgreSQL credentials use environment variables.",),
            "high",
        ),
        Requirement(
            "REQ-006",
            "Create safe root .env.example values.",
            RequirementKind.FUNCTIONAL,
            (".env.example is created at the root level.",),
            "high",
        ),
        Requirement(
            "REQ-007",
            "Add root and service-appropriate .dockerignore files.",
            RequirementKind.FUNCTIONAL,
            (".dockerignore files are added for root and service directories.",),
            "high",
        ),
        Requirement(
            "REQ-012",
            "PostgreSQL has a persistent named volume and readiness health check.",
            RequirementKind.NON_FUNCTIONAL,
            ("PostgreSQL service has a persistent named volume and readiness health check.",),
            "high",
        ),
        Requirement(
            "REQ-013",
            "Environment configuration uses safe non-secret defaults.",
            RequirementKind.NON_FUNCTIONAL,
            ("Environment configuration uses safe non-secret defaults in .env.example.",),
            "high",
        ),
        Requirement(
            "REQ-014",
            "Repository structure and ignore rules are coherent.",
            RequirementKind.NON_FUNCTIONAL,
            ("Repository structure and .dockerignore files are coherent and appropriate.",),
            "high",
        ),
    ]

    required = EngineeringSpecificationGenerator._required_architecture_paths(requirements)

    assert "REQ-012" in required["docker-compose.yml"]
    assert "REQ-013" in required[".env.example"]
    for path in (
        ".dockerignore",
        "frontend/.dockerignore",
        "backend/.dockerignore",
        "postgres/.dockerignore",
    ):
        assert "REQ-014" in required[path]


def test_integration_normalizer_attaches_overlapping_requirements_to_existing_file_units():
    requirements = [
        Requirement(
            "REQ-002", "Establish frontend/, backend/, and postgres/ directories.",
            RequirementKind.FUNCTIONAL,
            ("The directory structure includes frontend/, backend/, and postgres/ directories.",),
            "high",
        ),
        Requirement(
            "REQ-003", "Create the root docker-compose.yml.",
            RequirementKind.FUNCTIONAL,
            ("docker-compose.yml is created at the root level.",), "high",
        ),
        Requirement(
            "REQ-004", "Configure PostgreSQL with environment-driven credentials.",
            RequirementKind.FUNCTIONAL,
            ("PostgreSQL credentials use environment variables.",), "high",
        ),
        Requirement(
            "REQ-006", "Create safe root .env.example values.",
            RequirementKind.FUNCTIONAL,
            (".env.example is created at the root level.",), "high",
        ),
        Requirement(
            "REQ-007", "Add root and service-appropriate .dockerignore files.",
            RequirementKind.FUNCTIONAL,
            (".dockerignore files are added for root and service directories.",), "high",
        ),
        Requirement(
            "REQ-012", "PostgreSQL has a persistent named volume and readiness health check.",
            RequirementKind.NON_FUNCTIONAL,
            ("PostgreSQL service has a persistent named volume and readiness health check.",), "high",
        ),
        Requirement(
            "REQ-013", "Environment configuration uses safe non-secret defaults.",
            RequirementKind.NON_FUNCTIONAL,
            ("Environment configuration uses safe non-secret defaults in .env.example.",), "high",
        ),
        Requirement(
            "REQ-014", "Repository structure and ignore rules are coherent.",
            RequirementKind.NON_FUNCTIONAL,
            ("Repository structure and .dockerignore files are coherent and appropriate.",), "high",
        ),
    ]
    architecture = [
        ArchitectureModule(
            "MOD-001", "Repository Root", "Root infrastructure",
            file_paths=(".env.example", ".dockerignore", "docker-compose.yml"),
        ),
        ArchitectureModule(
            "MOD-002", "Frontend", "Frontend build context",
            file_paths=("frontend/.dockerignore",),
        ),
        ArchitectureModule(
            "MOD-003", "Backend", "Backend build context",
            file_paths=("backend/.dockerignore",),
        ),
        ArchitectureModule(
            "MOD-004", "PostgreSQL", "PostgreSQL build context",
            file_paths=("postgres/.dockerignore",),
        ),
    ]
    data = {
        "implementation_units": [
            {
                "unit_id": "UNIT-001", "title": "Create Compose configuration",
                "target_path": "docker-compose.yml", "kind": "configuration",
                "depends_on": [], "satisfies": ["REQ-003", "REQ-004"],
                "implements": [], "acceptance_criteria": ["Define PostgreSQL service."],
            },
            {
                "unit_id": "UNIT-002", "title": "Create environment example",
                "target_path": ".env.example", "kind": "configuration",
                "depends_on": [], "satisfies": ["REQ-006"],
                "implements": [], "acceptance_criteria": ["Document environment settings."],
            },
            *[
                {
                    "unit_id": f"UNIT-{index:03d}", "title": f"Create {path}",
                    "target_path": path, "kind": "configuration",
                    "depends_on": [], "satisfies": ["REQ-007"],
                    "implements": [], "acceptance_criteria": ["Add appropriate ignore rules."],
                }
                for index, path in enumerate(
                    (".dockerignore", "frontend/.dockerignore",
                     "backend/.dockerignore", "postgres/.dockerignore"),
                    start=3,
                )
            ],
        ],
        "verification": [
            {
                "criterion_id": "VER-001",
                "description": "Review the Compose configuration.",
                "kind": "manual",
                "required": True,
                "command": [],
                "covers": ["REQ-003"],
            }
        ],
    }

    units, _verification = EngineeringSpecificationGenerator(FakeClient([]))._parse_integration(
        data, requirements, architecture, contracts=[]
    )
    by_path = {unit.target_path: set(unit.satisfies) for unit in units}

    assert "REQ-012" in by_path["docker-compose.yml"]
    assert "REQ-013" in by_path[".env.example"]
    for path in (
        ".dockerignore",
        "frontend/.dockerignore",
        "backend/.dockerignore",
        "postgres/.dockerignore",
    ):
        assert "REQ-014" in by_path[path]



def test_numeric_work_package_fraction_is_not_treated_as_required_directory():
    requirement = Requirement(
        requirement_id="REQ-006",
        description=(
            "Create frontend and backend Dockerfiles matching the Compose "
            "build contexts from Work Package 1/12."
        ),
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=(
            "Dockerfiles for frontend and backend are created in respective directories.",
        ),
    )

    directories = EngineeringSpecificationGenerator._explicit_requirement_directories(
        requirement
    )

    assert directories == set()


def test_named_directories_are_still_detected_when_requirement_mentions_work_package_fraction():
    requirement = Requirement(
        requirement_id="REQ-LAYOUT",
        description=(
            "Create frontend/ and backend/ directories matching the layout "
            "from Work Package 1/12."
        ),
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=(
            "The frontend/ and backend/ directories exist.",
        ),
    )

    directories = EngineeringSpecificationGenerator._explicit_requirement_directories(
        requirement
    )

    assert directories == {"frontend", "backend"}



def test_same_target_settings_units_normalize_to_configuration_and_merge():
    from coding_agent.engineering_spec import UnitKind

    requirements = [
        Requirement(
            "REQ-001",
            "Create the Django backend project.",
            acceptance_criteria=("Django project files are created.",),
        ),
        Requirement(
            "REQ-003",
            "Configure Django to connect to PostgreSQL through environment variables.",
            acceptance_criteria=("Django database settings use environment variables.",),
        ),
    ]
    units = [
        ImplementationUnit(
            unit_id="UNIT-004",
            title="Create Django project",
            target_path="backend/settings.py",
            kind=UnitKind.SOURCE,
            satisfies=("REQ-001",),
            acceptance_criteria=("Django project settings are created.",),
        ),
        ImplementationUnit(
            unit_id="UNIT-007",
            title="Configure Django database settings",
            target_path="backend/settings.py",
            kind=UnitKind.CONFIGURATION,
            satisfies=("REQ-003",),
            acceptance_criteria=("Database settings use environment variables.",),
        ),
    ]

    merged, verification, repairs = (
        EngineeringSpecificationGenerator._normalize_implementation_units(
            units, [], requirements
        )
    )

    assert verification == []
    assert len(merged) == 1
    assert merged[0].target_path == "backend/settings.py"
    assert merged[0].kind == UnitKind.CONFIGURATION
    assert merged[0].satisfies == ("REQ-001", "REQ-003")
    assert "Django project settings are created." in merged[0].acceptance_criteria
    assert "Database settings use environment variables." in merged[0].acceptance_criteria
    assert any(
        repair["action"] == "normalize_artifact_kind_from_path"
        and repair["target_path"] == "backend/settings.py"
        and repair["artifact_kind"] == "configuration"
        for repair in repairs
    )


def test_generic_python_file_kind_conflict_is_not_silently_normalized():
    from coding_agent.engineering_spec import UnitKind

    requirements = [
        Requirement("REQ-001", "Create the application module."),
        Requirement("REQ-002", "Configure the application module."),
    ]
    units = [
        ImplementationUnit(
            unit_id="UNIT-001",
            title="Create application module",
            target_path="backend/app.py",
            kind=UnitKind.SOURCE,
            satisfies=("REQ-001",),
        ),
        ImplementationUnit(
            unit_id="UNIT-002",
            title="Configure application module",
            target_path="backend/app.py",
            kind=UnitKind.CONFIGURATION,
            satisfies=("REQ-002",),
        ),
    ]

    try:
        EngineeringSpecificationGenerator._normalize_implementation_units(
            units, [], requirements
        )
    except ValueError as exc:
        assert "disagree on artifact kind" in str(exc)
    else:
        raise AssertionError("ambiguous generic Python path conflict must remain an error")



def test_settings_path_kind_inference_normalizes_windows_separators():
    from coding_agent.engineering_spec import UnitKind

    assert (
        EngineeringSpecificationGenerator._unit_kind_for_required_path(
            r"backend\\settings.py"
        )
        == UnitKind.CONFIGURATION
    )
    assert EngineeringSpecificationGenerator._has_unambiguous_path_artifact_kind(
        r"backend\\settings.py"
    )


def test_integration_repairs_service_dockerfiles_and_compose_health_requirements(tmp_path):
    """Replay Step 2's omitted Dockerfile/Compose requirement mappings."""
    requirements = {"requirements": [
        {
            "requirement_id": "REQ-006",
            "description": (
                "Create frontend and backend Dockerfiles matching the Compose "
                "build contexts from Work Package 1/12."
            ),
            "kind": "functional",
            "acceptance_criteria": [
                "Frontend and backend Dockerfiles are created.",
                "Dockerfiles match the Compose build contexts.",
            ],
            "priority": "high",
        },
        {
            "requirement_id": "REQ-007",
            "description": (
                "Ensure Compose startup dependencies and health checks align "
                "with actual health endpoints and service readiness."
            ),
            "kind": "functional",
            "acceptance_criteria": [
                "Compose startup dependencies and health checks are aligned "
                "with health endpoints.",
                "Service readiness is correctly configured.",
            ],
            "priority": "high",
        },
    ]}
    architecture = {"architecture": [
        {
            "module_id": "MOD-001",
            "name": "Repository",
            "responsibility": "Own root project configuration.",
            "depends_on": [],
            "file_paths": ["docker-compose.yml"],
        },
        {
            "module_id": "MOD-002",
            "name": "Backend",
            "responsibility": "Own the Django backend service.",
            "depends_on": [],
            "file_paths": ["backend/Dockerfile", "backend/manage.py"],
        },
        {
            "module_id": "MOD-003",
            "name": "Frontend",
            "responsibility": "Own the React frontend service.",
            "depends_on": ["MOD-002"],
            "file_paths": ["frontend/Dockerfile", "frontend/src/App.tsx"],
        },
        {
            "module_id": "MOD-004",
            "name": "Database",
            "responsibility": "Own PostgreSQL service configuration.",
            "depends_on": [],
            "file_paths": ["postgres/Dockerfile"],
        },
    ]}
    contracts = {"contracts": []}
    integration = {
        "implementation_units": [
            {
                "unit_id": "UNIT-001",
                "title": "Create backend container build file",
                "target_path": "backend/Dockerfile",
                "kind": "configuration",
                "depends_on": [],
                "satisfies": [],
                "implements": [],
                "acceptance_criteria": ["Backend image can be built."],
            },
            {
                "unit_id": "UNIT-002",
                "title": "Create frontend container build file",
                "target_path": "frontend/Dockerfile",
                "kind": "configuration",
                "depends_on": [],
                "satisfies": [],
                "implements": [],
                "acceptance_criteria": ["Frontend image can be built."],
            },
        ],
        "verification": [
            {
                "criterion_id": "VER-001",
                "description": (
                    "Review the frontend and backend Dockerfiles against "
                    "their Compose build contexts."
                ),
                "kind": "manual",
                "required": True,
                "command": [],
                "covers": ["REQ-006"],
            },
            {
                "criterion_id": "VER-002",
                "description": (
                    "Review Compose startup dependencies and health checks "
                    "for service readiness."
                ),
                "kind": "manual",
                "required": True,
                "command": [],
                "covers": ["REQ-007"],
            },
        ],
    }

    client = FakeClient([requirements, architecture, contracts, integration])
    diagnostics = tmp_path / "step2-replay"
    generator = EngineeringSpecificationGenerator(
        client, diagnostics_dir=diagnostics
    )
    spec = generator.generate(
        task_id="fafas-step-02-docker-compose-mapping",
        objective="Create Django/React skeleton and Compose health checks",
        workspace="/workspace",
    )

    units_by_path = {unit.target_path: unit for unit in spec.implementation_units}
    assert "REQ-006" in units_by_path["backend/Dockerfile"].satisfies
    assert "REQ-006" in units_by_path["frontend/Dockerfile"].satisfies
    required_paths = generator._required_architecture_paths(
        spec.requirements,
        {
            path
            for module in spec.architecture
            for path in module.file_paths
        },
    )
    assert "postgres/Dockerfile" not in required_paths
    assert "docker-compose.yml" in units_by_path
    assert units_by_path["docker-compose.yml"].satisfies == ("REQ-007",)
    assert units_by_path["docker-compose.yml"].kind.value == "configuration"

    # The model is shown the architecture-grounded mappings before integration,
    # and the validator independently confirms exact file ownership afterward.
    assert "- backend/Dockerfile: REQ-006" in client.prompts[3]
    assert "- frontend/Dockerfile: REQ-006" in client.prompts[3]
    assert "- docker-compose.yml: REQ-007" in client.prompts[3]
    assert generator.last_trace[-1].success is True


def test_cross_cutting_environment_security_requirement_maps_to_env_and_settings():
    requirements = [
        Requirement(
            requirement_id="REQ-015",
            description="Implement security best practices for environment variables.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(
                "Environment variables are securely managed.",
                "Non-secret placeholders are used where necessary.",
            ),
            priority="high",
        )
    ]
    units = [
        ImplementationUnit(
            unit_id="UNIT-001",
            title="Configure Django settings",
            target_path="backend/fafas_burger/settings.py",
            kind=UnitKind.CONFIGURATION,
            satisfies=("REQ-003",),
        )
    ]
    completed, repairs = EngineeringSpecificationGenerator._complete_cross_cutting_requirement_coverage(
        units, requirements,
        {"backend/fafas_burger/settings.py", ".env.example"},
    )
    mapped = {
        unit.target_path: unit
        for unit in completed
        if "REQ-015" in unit.satisfies
    }
    assert set(mapped) == {
        "backend/fafas_burger/settings.py", ".env.example"
    }
    assert any(
        repair["action"] == "create_cross_cutting_security_unit"
        and repair["target_path"] == ".env.example"
        for repair in repairs
    )


def test_cross_cutting_coding_standards_requirement_maps_to_source_and_test_units():
    requirements = [
        Requirement(
            requirement_id="REQ-016",
            description="Ensure consistent coding standards across the project.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(
                "Consistent coding standards are applied.",
                "Code is well-structured and maintainable.",
            ),
            priority="high",
        )
    ]
    units = [
        ImplementationUnit(
            unit_id="UNIT-001", title="Backend endpoint",
            target_path="backend/urls.py", kind=UnitKind.SOURCE,
        ),
        ImplementationUnit(
            unit_id="UNIT-002", title="Frontend app",
            target_path="frontend/App.tsx", kind=UnitKind.SOURCE,
        ),
        ImplementationUnit(
            unit_id="UNIT-003", title="Backend tests",
            target_path="backend/tests/test_smoke.py", kind=UnitKind.TEST,
        ),
        ImplementationUnit(
            unit_id="UNIT-004", title="Readme",
            target_path="README.md", kind=UnitKind.DOCUMENTATION,
        ),
    ]
    completed, repairs = EngineeringSpecificationGenerator._complete_cross_cutting_requirement_coverage(
        units, requirements, set()
    )
    mapped = {
        unit.target_path for unit in completed
        if "REQ-016" in unit.satisfies
    }
    assert mapped == {
        "backend/urls.py", "frontend/App.tsx",
        "backend/tests/test_smoke.py",
    }
    assert all(
        "Code is well-structured and maintainable." in unit.acceptance_criteria
        for unit in completed if "REQ-016" in unit.satisfies
    )
    assert len(repairs) == 3


def test_cross_cutting_repair_does_not_fabricate_unrelated_requirement_coverage():
    requirement = Requirement(
        requirement_id="REQ-017",
        description="Implement a novel checkout capability.",
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=("Checkout works.",),
        priority="high",
    )
    units, repairs = EngineeringSpecificationGenerator._complete_cross_cutting_requirement_coverage(
        [], [requirement], {".env.example"}
    )
    assert units == []
    assert repairs == []


def test_required_file_completion_preserves_existing_requirement_mapping_for_single_path_owner():
    requirements = [
        Requirement(
            "REQ-009",
            "Complete README setup, startup, shutdown, migrations, testing, and troubleshooting instructions.",
            RequirementKind.FUNCTIONAL,
            ("README.md contains complete operational instructions.",),
        ),
        Requirement(
            "REQ-018",
            "Documentation is updated to reflect changes.",
            RequirementKind.NON_FUNCTIONAL,
            ("Documentation reflects the implemented changes.",),
        ),
    ]
    existing = ImplementationUnit(
        "UNIT-010",
        "Update README",
        "README.md",
        kind=UnitKind.DOCUMENTATION,
        satisfies=("REQ-018",),
        acceptance_criteria=("Document the changes.",),
    )

    units, repairs = EngineeringSpecificationGenerator._complete_required_file_units(
        [existing], requirements, {"README.md"}
    )

    readme = next(item for item in units if item.target_path == "README.md")
    assert set(readme.satisfies) == {"REQ-009", "REQ-018"}
    assert any(
        item["action"] == "augment_required_file_unit"
        and item["unit_id"] == "UNIT-010"
        and set(item["satisfies"]) == {"REQ-018", "REQ-009"}
        for item in repairs
    )


def test_smoke_test_file_completion_preserves_overlapping_requirement_ids():
    requirements = [
        Requirement(
            "REQ-001",
            "Create the Django backend under backend/.",
            RequirementKind.FUNCTIONAL,
        ),
        Requirement(
            "REQ-004",
            "Create the React frontend under frontend/.",
            RequirementKind.FUNCTIONAL,
        ),
        Requirement(
            "REQ-008",
            "Add basic backend and frontend smoke tests where practical.",
            RequirementKind.FUNCTIONAL,
            (
                "Basic backend smoke tests are added.",
                "Basic frontend smoke tests are added.",
            ),
        ),
        Requirement(
            "REQ-015",
            "Basic backend and frontend smoke tests are added.",
            RequirementKind.NON_FUNCTIONAL,
            (
                "Basic backend smoke tests are added.",
                "Basic frontend smoke tests are added.",
            ),
        ),
    ]
    paths = {
        "backend/tests/test_smoke.py",
        "frontend/tests/smoke.test.tsx",
    }
    existing = [
        ImplementationUnit(
            "UNIT-008",
            "Add backend smoke tests",
            "backend/tests/test_smoke.py",
            kind=UnitKind.TEST,
            satisfies=("REQ-015",),
        ),
        ImplementationUnit(
            "UNIT-009",
            "Add frontend smoke tests",
            "frontend/tests/smoke.test.tsx",
            kind=UnitKind.TEST,
            satisfies=("REQ-015",),
        ),
    ]

    units, _ = EngineeringSpecificationGenerator._complete_required_file_units(
        existing, requirements, paths
    )

    for path in paths:
        unit = next(item for item in units if item.target_path == path)
        assert set(unit.satisfies) == {"REQ-008", "REQ-015"}


def test_preservation_only_requirement_is_normalized_to_constraint(tmp_path):
    generator = EngineeringSpecificationGenerator(
        FakeClient([]), diagnostics_dir=tmp_path
    )
    requirements = generator._parse_requirements({
        "requirements": [{
            "requirement_id": "REQ-016",
            "description": "Existing code and configuration files are preserved.",
            "kind": "non_functional",
            "acceptance_criteria": [
                "Existing code and configuration files are preserved."
            ],
            "priority": "high",
        }]
    })

    assert requirements[0].kind == RequirementKind.CONSTRAINT
    repairs = json.loads(
        (tmp_path / "requirements_normalization.json").read_text(encoding="utf-8")
    )
    assert repairs[0]["requirement_id"] == "REQ-016"
    assert repairs[0]["kind"] == "constraint"


def test_dependency_management_requirement_maps_to_declared_manifests():
    requirement = Requirement(
        "REQ-017",
        "Dependencies are managed correctly.",
        RequirementKind.NON_FUNCTIONAL,
        ("Dependencies are managed correctly.",),
    )
    architecture_paths = {
        "backend/requirements.txt",
        "frontend/package.json",
    }
    startup_requirement = Requirement(
        "REQ-019",
        "The frontend and backend services depend on PostgreSQL being healthy before starting.",
        RequirementKind.NON_FUNCTIONAL,
    )
    required = EngineeringSpecificationGenerator._required_architecture_paths(
        [requirement, startup_requirement], architecture_paths
    )

    assert required == {
        "backend/requirements.txt": {"REQ-017"},
        "frontend/package.json": {"REQ-017"},
    }


def test_dependency_management_completion_adds_missing_backend_manifest_and_preserves_frontend_mapping():
    requirement = Requirement(
        "REQ-017",
        "Dependencies are managed correctly.",
        RequirementKind.NON_FUNCTIONAL,
        ("Dependencies are managed correctly.",),
    )
    existing_requirement = Requirement(
        "REQ-004",
        "Create the React + TypeScript + Vite frontend under frontend/.",
        RequirementKind.FUNCTIONAL,
        ("The frontend project is created under frontend/.",),
    )
    existing = ImplementationUnit(
        "UNIT-004",
        "Create frontend package manifest",
        "frontend/package.json",
        kind=UnitKind.CONFIGURATION,
        satisfies=("REQ-004",),
    )

    units, repairs = EngineeringSpecificationGenerator._complete_required_file_units(
        [existing],
        [requirement, existing_requirement],
        {"backend/requirements.txt", "frontend/package.json"},
    )

    frontend = next(item for item in units if item.target_path == "frontend/package.json")
    backend = next(item for item in units if item.target_path == "backend/requirements.txt")
    assert set(frontend.satisfies) == {"REQ-004", "REQ-017"}
    assert backend.satisfies == ("REQ-017",)
    assert backend.kind == UnitKind.CONFIGURATION
    assert any(
        item["action"] == "create_required_file_unit"
        and item["target_path"] == "backend/requirements.txt"
        and item["satisfies"] == ["REQ-017"]
        for item in repairs
    )


def test_repair_coverage_invariant_rejects_lost_implementation_requirement():
    requirement = Requirement(
        "REQ-015",
        "Add smoke tests.",
        RequirementKind.NON_FUNCTIONAL,
    )
    original = [
        ImplementationUnit(
            "UNIT-008",
            "Add smoke tests",
            "tests/test_smoke.py",
            kind=UnitKind.TEST,
            satisfies=("REQ-015",),
        )
    ]
    repaired = [
        ImplementationUnit(
            "UNIT-008",
            "Add smoke tests",
            "tests/test_smoke.py",
            kind=UnitKind.TEST,
            satisfies=("REQ-008",),
        )
    ]

    with pytest.raises(ValueError, match="removed existing.*REQ-015"):
        EngineeringSpecificationGenerator._assert_repairs_preserve_implementation_coverage(
            original, repaired, [requirement]
        )


@pytest.mark.parametrize(
    "stage,payload",
    [
        (
            "requirements",
            {"requirements": [{"requirement_id": "REQ-1", "description": "Build it"}],
             "unexpected": True},
        ),
        (
            "architecture",
            {"architecture": [{"module_id": "MOD-1", "name": "Root",
                               "responsibility": "Root files"}],
             "unexpected": True},
        ),
        ("contracts", {"contracts": [], "unexpected": True}),
        (
            "integration",
            {"implementation_units": [],
             "verification": [{"description": "Review the result", "kind": "manual"}],
             "unexpected": True},
        ),
    ],
)
def test_stage_applies_pydantic_boundary_contract_before_domain_parser(stage, payload):
    generator = EngineeringSpecificationGenerator(
        FakeClient([payload]), max_stage_attempts=1
    )

    with pytest.raises(
        SpecificationGenerationError, match="Extra inputs are not permitted"
    ):
        generator._stage(stage, [], "prompt", lambda data: data)


def test_architecture_repair_adds_omitted_named_service_dockerfile():
    requirements = {
        "requirements": [
            {
                "requirement_id": "REQ-001",
                "description": "Create the Django backend project under backend/.",
                "kind": "functional",
                "acceptance_criteria": ["The backend project exists under backend/."],
                "priority": "high",
            },
            {
                "requirement_id": "REQ-004",
                "description": "Create the React frontend under frontend/.",
                "kind": "functional",
                "acceptance_criteria": ["The frontend project exists under frontend/."],
                "priority": "high",
            },
            {
                "requirement_id": "REQ-006",
                "description": (
                    "Create frontend and backend Dockerfiles matching the Compose build contexts."
                ),
                "kind": "functional",
                "acceptance_criteria": [
                    "Frontend and backend Dockerfiles are created."
                ],
                "priority": "high",
            },
        ]
    }
    architecture = {
        "architecture": [
            {
                "module_id": "MOD-001",
                "name": "Backend",
                "responsibility": "Django backend",
                "depends_on": [],
                "file_paths": ["backend/Dockerfile", "backend/manage.py"],
            },
            {
                "module_id": "MOD-002",
                "name": "Frontend",
                "responsibility": "React frontend",
                "depends_on": [],
                "file_paths": ["frontend/src/App.tsx"],
            },
        ]
    }
    generator = EngineeringSpecificationGenerator(FakeClient([]))
    parsed_requirements = generator._parse_requirements(requirements)

    required_paths = generator._required_architecture_paths(parsed_requirements)
    assert required_paths["backend/Dockerfile"] == {"REQ-006"}
    assert required_paths["frontend/Dockerfile"] == {"REQ-006"}

    parsed_architecture = generator._parse_architecture(
        architecture, parsed_requirements
    )
    frontend = next(
        item for item in parsed_architecture if item.module_id == "MOD-002"
    )
    assert "frontend/Dockerfile" in frontend.file_paths


def test_test_artifact_path_does_not_nest_generic_tests_directory():
    # Regression: a requirement path under tests/ made "tests" look like a
    # service directory, producing tests/tests/test_smoke.py for test work.
    requirements = (
        Requirement(
            "REQ-004", "Create main.py as the application entry point",
            RequirementKind.FUNCTIONAL, ("The game launches using python main.py",),
        ),
        Requirement(
            "REQ-005", "Create automated tests in tests/ directory",
            RequirementKind.FUNCTIONAL,
            ("Automated tests cover core game mechanics without opening a graphical window",),
        ),
    )

    paths = EngineeringSpecificationGenerator._required_test_artifact_paths(requirements)

    assert paths == {"tests/test_req_005.py": {"REQ-005"}}


def test_requirement_covered_by_python_test_unit_is_verified_by_running_that_test():
    requirements = [Requirement("REQ-002", "Eating food increases score",
                                acceptance_criteria=("Score increases after eating food",))]
    units = [ImplementationUnit("UNIT-006", "Tests", "tests/test_game.py",
                                kind=UnitKind.TEST, satisfies=("REQ-002",))]

    result, repairs = EngineeringSpecificationGenerator._complete_verification_coverage([], units, requirements)

    assert len(result) == 1
    criterion = result[0]
    assert criterion.kind == VerificationKind.BEHAVIORAL
    assert criterion.command[-1] == "tests/test_game.py"
    assert "pytest" in criterion.command
    assert criterion.covers == ("REQ-002",)
    assert repairs[0]["action"] == "create_test_backed_verification"


def test_requirement_without_test_unit_still_gets_a_manual_checklist():
    requirements = [Requirement("REQ-003", "Game launches with python main.py",
                                acceptance_criteria=("The game launches",))]
    units = [ImplementationUnit("UNIT-001", "Entry", "main.py", satisfies=("REQ-003",))]

    result, _ = EngineeringSpecificationGenerator._complete_verification_coverage([], units, requirements)

    assert len(result) == 1
    assert result[0].kind == VerificationKind.MANUAL
    assert result[0].command == ()


def test_unautomated_constraint_review_is_reported_but_not_blocking():
    requirements = [Requirement("REQ-010", "Use Windows-compatible verification commands",
                                RequirementKind.CONSTRAINT,
                                acceptance_criteria=("Commands run on Windows",))]
    units = [ImplementationUnit("UNIT-001", "Entry", "main.py", satisfies=("REQ-010",))]

    result, _ = EngineeringSpecificationGenerator._complete_verification_coverage([], units, requirements)

    assert len(result) == 1
    assert result[0].kind == VerificationKind.MANUAL
    assert result[0].required is False
    assert "non-blocking constraint" in result[0].description


def test_unautomated_functional_requirement_still_blocks_acceptance():
    requirements = [Requirement("REQ-011", "Game launches with python main.py",
                                acceptance_criteria=("The game launches",))]
    units = [ImplementationUnit("UNIT-001", "Entry", "main.py", satisfies=("REQ-011",))]

    result, _ = EngineeringSpecificationGenerator._complete_verification_coverage([], units, requirements)

    assert result[0].required is True
