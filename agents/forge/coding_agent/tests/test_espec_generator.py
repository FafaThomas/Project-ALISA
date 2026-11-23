import json

from coding_agent.engineering_spec import ArchitectureModule, ImplementationUnit, Requirement, RequirementKind

from coding_agent.espec_generator import (
    EngineeringSpecificationGenerator,
    SpecificationGenerationError,
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
    assert "verification-only obligation" in client.prompts[0]
    assert "are verification-only constraints" in client.prompts[0]


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
        "implements_acceptance_criteria": [],
    })
    from coding_agent.engineering_spec import make_acceptance_criterion_id
    payload["implementation_units"][0]["implements_acceptance_criteria"] = [
        make_acceptance_criterion_id(
            "REQ-001", "A submitted task appears in the list"
        )
    ]
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
    from coding_agent.engineering_spec import make_acceptance_criterion_id

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
    criterion_ids = [
        make_acceptance_criterion_id("REQ-001", item)
        for item in requirements["requirements"][0]["acceptance_criteria"]
    ]
    units = [
        {"unit_id": "UNIT-001", "title": "Root ignore rules", "target_path": ".dockerignore",
         "kind": "configuration", "depends_on": [], "satisfies": ["REQ-001"],
         "implements": [], "acceptance_criteria": ["Root ignore file exists"],
         "implements_acceptance_criteria": [criterion_ids[0]]},
        {"unit_id": "UNIT-002", "title": "Frontend ignore rules", "target_path": "frontend/.dockerignore",
         "kind": "configuration", "depends_on": [], "satisfies": ["REQ-001"],
         "implements": [], "acceptance_criteria": ["Frontend ignore file exists"],
         "implements_acceptance_criteria": [criterion_ids[1]]},
        {"unit_id": "UNIT-003", "title": "Backend ignore rules", "target_path": "backend/.dockerignore",
         "kind": "configuration", "depends_on": [], "satisfies": ["REQ-001"],
         "implements": [], "acceptance_criteria": ["Backend ignore file exists"],
         "implements_acceptance_criteria": [criterion_ids[2]]},
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


def test_service_appropriate_dockerignore_paths_are_not_inferred_from_other_requirements():
    from coding_agent.engineering_spec import Requirement, RequirementKind

    requirements = [
        Requirement(
            "REQ-001", "Establish separate frontend/, backend/, and postgres/ areas.",
            RequirementKind.FUNCTIONAL,
        ),
        Requirement(
            "REQ-006", "Add root and service-appropriate .dockerignore files.",
            RequirementKind.FUNCTIONAL,
            (
                "Root directory contains a .dockerignore file.",
                "Additional service .dockerignore files should be created as needed.",
            ),
        ),
    ]

    required = EngineeringSpecificationGenerator._required_architecture_paths(requirements)

    assert required == {".dockerignore": {"REQ-006"}}


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


def test_manual_verification_rejects_executable_command():
    payloads = responses()
    payloads[3]["verification"][0]["kind"] = "manual"
    payloads[3]["verification"][0]["command"] = ["python", "-m", "pytest"]
    client = FakeClient(payloads)
    generator = EngineeringSpecificationGenerator(client, max_stage_attempts=1)

    try:
        generator.generate(task_id="manual-executable", objective="Build app", workspace="/w")
    except SpecificationGenerationError as exc:
        assert "manual but its command contains executable-looking content" in str(exc)
        assert "python" in str(exc)
    else:
        raise AssertionError("manual verification must not accept executable commands")



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
        assert set(by_path[path].satisfies) == {"REQ-007"}
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
        item["action"] == "remap_unit_requirement"
        and item["target_path"] == ".env.example"
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


def test_directory_requirement_is_not_mapped_to_incidental_child_file_units():
    requirements = [
        Requirement(
            "REQ-001",
            "Establish a clean root layout with separate frontend/, backend/, and postgres/ areas.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=(
                "Directories frontend/, backend/, and postgres/ exist in the root of the workspace.",
            ),
        ),
        Requirement(
            "REQ-006",
            "Add root and service-appropriate .dockerignore files.",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("A root .dockerignore file exists.",),
        ),
    ]
    units = [
        ImplementationUnit(
            unit_id=f"UNIT-{index:03d}",
            title=f"Create {path}",
            target_path=path,
            kind="configuration",
            satisfies=("REQ-006", "REQ-001"),
            acceptance_criteria=(f"{path} exists.",),
        )
        for index, path in enumerate(
            (".dockerignore", "frontend/.dockerignore", "backend/.dockerignore"),
            start=1,
        )
    ]

    normalized, repairs = EngineeringSpecificationGenerator._normalize_requirement_file_mappings(
        units, requirements, {unit.target_path for unit in units}
    )

    assert all("REQ-001" not in unit.satisfies for unit in normalized)
    assert sum(
        item["action"] == "remove_directory_requirement_from_file_unit"
        for item in repairs
    ) == 3


def test_directory_coverage_validator_requires_structural_verification_not_child_files():
    from coding_agent.engineering_spec import VerificationCriterion, VerificationKind

    requirement = Requirement(
        "REQ-001",
        "Establish separate frontend/, backend/, and postgres/ directories.",
        kind=RequirementKind.FUNCTIONAL,
        acceptance_criteria=("Directories frontend/, backend/, and postgres/ exist.",),
    )
    incidental_units = [
        ImplementationUnit(
            "UNIT-001", "Create frontend ignore file", "frontend/.dockerignore",
            kind="configuration", satisfies=("REQ-001",),
        ),
        ImplementationUnit(
            "UNIT-002", "Create backend ignore file", "backend/.dockerignore",
            kind="configuration", satisfies=("REQ-001",),
        ),
    ]
    manual_verification = [
        VerificationCriterion(
            "VER-001", "Manually verify all required directories exist.",
            VerificationKind.MANUAL, covers=("REQ-001",),
        )
    ]

    with __import__("pytest").raises(ValueError, match="structural verification coverage"):
        EngineeringSpecificationGenerator._validate_integration_coverage(
            incidental_units, manual_verification, [requirement],
            architecture_paths={"frontend/.dockerignore", "backend/.dockerignore"},
        )

    structural_verification = [
        VerificationCriterion(
            "VER-002", "Verify the frontend, backend, and postgres directories exist.",
            VerificationKind.STRUCTURAL, covers=("REQ-001",),
        )
    ]
    EngineeringSpecificationGenerator._validate_integration_coverage(
        incidental_units, structural_verification, [requirement],
        architecture_paths={"frontend/.dockerignore", "backend/.dockerignore"},
    )


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
    structural = next(
        item for item in repaired
        if item.kind == VerificationKind.STRUCTURAL and item.covers == ("REQ-001",)
    )
    assert structural.command[:2] == ("python", "-c")


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


def test_integration_normalizes_directory_requirements_without_file_side_effect_mappings():
    from coding_agent.engineering_spec import (
        ArchitectureModule, Requirement, RequirementKind, VerificationKind,
    )

    requirements = [
        Requirement(
            "REQ-001",
            "Establish frontend/, backend/, and postgres/ directories.",
            RequirementKind.FUNCTIONAL,
            ("Directories frontend/, backend/, and postgres/ exist.",),
        ),
        Requirement(
            "REQ-002",
            "Create root docker-compose.yml with frontend, backend, and PostgreSQL services.",
            RequirementKind.FUNCTIONAL,
            ("docker-compose.yml defines all three services.",),
        ),
    ]
    architecture = [
        ArchitectureModule(
            "MOD-ROOT", "Root", "Root configuration",
            file_paths=("docker-compose.yml",),
        ),
        ArchitectureModule(
            "MOD-FE", "Frontend", "Frontend files",
            file_paths=("frontend/.dockerignore",),
        ),
        ArchitectureModule(
            "MOD-BE", "Backend", "Backend files",
            file_paths=("backend/.dockerignore",),
        ),
    ]
    generator = EngineeringSpecificationGenerator(FakeClient([]))
    integration = {
        "implementation_units": [{
            "unit_id": "UNIT-001",
            "title": "Create Compose configuration",
            "target_path": "docker-compose.yml",
            "kind": "configuration",
            "depends_on": [],
            "satisfies": ["REQ-002", "REQ-001"],
            "implements": [],
            "acceptance_criteria": ["Define the required services."],
        }],
        "verification": [{
            "criterion_id": "VER-001",
            "description": "Verify frontend, backend, and postgres directories exist.",
            "kind": "structural",
            "required": True,
            "command": ["python", "-c", "from pathlib import Path; assert all(Path(p).is_dir() for p in ('frontend','backend','postgres'))"],
            "covers": ["REQ-001"],
        }],
    }

    units, verification = generator._parse_integration(
        integration, requirements, architecture, contracts=[]
    )

    assert len(units) == 1
    assert units[0].satisfies == ("REQ-002",)
    directory_check = next(
        item for item in verification if item.covers == ("REQ-001",)
    )
    assert directory_check.kind == VerificationKind.STRUCTURAL
    assert "UNIT-001" not in directory_check.covers


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
        Requirement("REQ-IGNORE", "Add root and service-appropriate .dockerignore files as needed."),
        Requirement("REQ-REPORT", "Report exact verification commands/results at the end."),
    ]
    paths = EngineeringSpecificationGenerator._required_architecture_paths(requirements)
    assert paths == {".dockerignore": {"REQ-IGNORE"}}


def test_architecture_requires_test_artifact_path_for_explicit_test_requirement():
    from coding_agent.engineering_spec import Requirement
    generator = EngineeringSpecificationGenerator(FakeClient([]))
    requirements = [Requirement(
        "REQ-TEST", "Add meaningful tests for introduced behavior.",
        acceptance_criteria=("Tests cover the new behavior.",),
    )]
    with __import__('pytest').raises(ValueError, match="concrete test file path"):
        generator._parse_architecture({"architecture": [{
            "module_id": "MOD-ROOT", "name": "Root", "responsibility": "Project files",
            "depends_on": [], "file_paths": ["README.md"],
        }]}, requirements)


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
    from coding_agent.engineering_spec import make_acceptance_criterion_id
    ignore_criterion_id = make_acceptance_criterion_id(
        "REQ-007", requirements[4].acceptance_criteria[0]
    )
    repository_criterion_id = make_acceptance_criterion_id(
        "REQ-014", requirements[7].acceptance_criteria[0]
    )
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
                    "implements_acceptance_criteria": [ignore_criterion_id],
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


def test_fafas_work_package_one_directory_and_conditional_ignore_requirements_are_separate():
    from coding_agent.engineering_spec import Requirement, RequirementKind

    requirements = [
        Requirement(
            "REQ-001",
            "Establish a clean root layout with separate frontend/, backend/, and postgres/ areas.",
            RequirementKind.FUNCTIONAL,
            ("The workspace should contain directories named 'frontend', 'backend', and 'postgres'.",),
        ),
        Requirement(
            "REQ-006",
            "Add root and service-appropriate .dockerignore files.",
            RequirementKind.FUNCTIONAL,
            (
                "A .dockerignore file should be created at the root.",
                "Additional .dockerignore files should be created in the 'frontend' and 'backend' directories as needed.",
            ),
        ),
    ]

    generator = EngineeringSpecificationGenerator(FakeClient([]))
    assert generator._is_directory_only_requirement(requirements[0])
    assert not generator._is_directory_only_requirement(requirements[1])
    assert generator._required_architecture_paths(requirements) == {
        ".dockerignore": {"REQ-006"},
    }

    architecture = generator._parse_architecture({"architecture": [
        {
            "module_id": "MOD-ROOT", "name": "Repository",
            "responsibility": "Root configuration", "depends_on": [],
            "file_paths": [".dockerignore", "frontend/.dockerignore", "backend/.dockerignore", "postgres/.dockerignore"],
        },
    ]}, requirements)
    paths = {path for module in architecture for path in module.file_paths}
    assert paths == {".dockerignore"}


def test_directory_only_task_can_have_no_file_implementation_units():
    from coding_agent.engineering_spec import Requirement, RequirementKind, VerificationKind

    requirement = Requirement(
        "REQ-DIR", "Establish frontend/, backend/, and postgres/ directories.",
        RequirementKind.FUNCTIONAL,
        ("The frontend, backend, and postgres directories exist.",),
    )
    generator = EngineeringSpecificationGenerator(FakeClient([]))

    units, verification = generator._parse_integration({
        "implementation_units": [],
        "verification": [{
            "criterion_id": "VER-DIR",
            "description": "Verify frontend, backend, and postgres directories exist.",
            "kind": "structural",
            "required": True,
            "command": [
                "python", "-c",
                "from pathlib import Path; import sys; paths=['frontend','backend','postgres']; sys.exit(any(not Path(p).is_dir() for p in paths))",
            ],
            "covers": ["REQ-DIR"],
        }],
    }, [requirement], architecture=[], contracts=[])

    assert units == []
    assert verification[0].kind == VerificationKind.STRUCTURAL
    assert verification[0].covers == ("REQ-DIR",)


def test_generated_spec_has_stable_criterion_ids_and_explicit_implementation_and_verification_coverage():
    spec = EngineeringSpecificationGenerator(FakeClient(responses())).generate(
        task_id="task-criterion", objective="Build task dashboard", workspace="/workspace"
    )

    requirement = spec.requirements[0]
    criterion_id = requirement.acceptance_criterion_ids[0]
    assert criterion_id.startswith("REQ-001-AC-")
    assert criterion_id in spec.implementation_units[0].implements_acceptance_criteria
    assert any(
        criterion_id in item.covers_acceptance_criteria and item.required
        for item in spec.verification
    )
