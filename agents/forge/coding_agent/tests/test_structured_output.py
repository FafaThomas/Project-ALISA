import json
from unittest.mock import patch

from coding_agent.espec_generator import (
    EngineeringSpecificationGenerator,
    integration_json_schema,
)
from coding_agent.engineering_spec import (
    ArchitectureModule,
    InterfaceContract,
    Requirement,
    RequirementKind,
)
from coding_agent.llm_client import OllamaClient


class _Response:
    def __init__(self, payload):
        self.lines = [
            json.dumps({
                **payload,
                "done": True,
                "done_reason": "stop",
            }).encode("utf-8") + b"\n"
        ]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def readline(self):
        return self.lines.pop(0) if self.lines else b""


def test_ollama_client_sends_json_schema_as_format(monkeypatch):
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response({"message": {"content": '{"answer":"ok"}'}})

    monkeypatch.setattr("coding_agent.llm_client.urlopen", fake_urlopen)
    client = OllamaClient(model="test-model", base_url="http://localhost:11434")

    result = client.generate_json("Return an answer.", schema=schema)

    assert result == {"answer": "ok"}
    assert captured["payload"]["format"] == schema
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["options"]["num_predict"] > 0
    assert captured["payload"]["model"] == "test-model"


def test_ollama_client_retains_json_mode_when_no_schema_is_supplied(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response({"message": {"content": '{"ok":true}'}})

    monkeypatch.setattr("coding_agent.llm_client.urlopen", fake_urlopen)
    client = OllamaClient(model="test-model")

    assert client.generate_json("Return JSON.") == {"ok": True}
    assert captured["payload"]["format"] == "json"


def test_stage_passes_schema_to_schema_aware_client():
    schema = {"type": "object"}
    calls = []

    class Client:
        def generate_json(self, prompt, *, schema=None):
            calls.append((prompt, schema))
            return {"valid": True}

    generator = EngineeringSpecificationGenerator(Client())
    result = generator._stage(
        "test", [], "prompt", lambda response: response, schema=schema
    )

    assert result == {"valid": True}
    assert calls == [("prompt", schema)]


def test_stage_preserves_legacy_one_argument_client_compatibility():
    calls = []

    class LegacyClient:
        def generate_json(self, prompt):
            calls.append(prompt)
            return {"valid": True}

    generator = EngineeringSpecificationGenerator(LegacyClient())
    result = generator._stage(
        "test", [], "prompt", lambda response: response, schema={"type": "object"}
    )

    assert result == {"valid": True}
    assert calls == ["prompt"]


def test_stage_reuses_same_schema_during_validation_retry():
    schema = {"type": "object"}
    calls = []

    class Client:
        def generate_json(self, prompt, *, schema=None):
            calls.append((prompt, schema))
            if len(calls) == 1:
                return {"invalid": True}
            return {"valid": True}

    def parser(response):
        if "valid" not in response:
            raise ValueError("missing valid field")
        return response

    generator = EngineeringSpecificationGenerator(Client(), max_stage_attempts=2)
    result = generator._stage("test", [], "prompt", parser, schema=schema)

    assert result == {"valid": True}
    assert len(calls) == 2
    assert all(call_schema == schema for _, call_schema in calls)
    assert "PREVIOUS OUTPUT INVALID" in calls[1][0]


def test_integration_schema_constrains_top_level_and_required_verification_fields():
    requirements = [
        Requirement("REQ-001", "Create the frontend", RequirementKind.FUNCTIONAL),
        Requirement("REQ-002", "Check configuration", RequirementKind.CONSTRAINT),
    ]
    architecture = [
        ArchitectureModule(
            "MOD-001", "Root", "Root configuration",
            file_paths=("docker-compose.yml", "frontend/Dockerfile"),
        )
    ]
    contracts = [InterfaceContract("CON-001", "MOD-001", "Config", "data")]

    schema = integration_json_schema(requirements, architecture, contracts)
    properties = schema["properties"]

    assert set(properties) == {"implementation_units", "verification"}
    assert "implementation_units" in schema["required"]
    assert "units" not in properties

    unit_schema = properties["implementation_units"]["items"]
    target_path = unit_schema["properties"]["target_path"]
    assert set(target_path["enum"]) == {
        "docker-compose.yml", "frontend", "frontend/Dockerfile"
    }
    assert "" not in target_path["enum"]
    assert "." not in target_path["enum"]
    assert "root/" not in target_path["enum"]

    verification_schema = properties["verification"]["items"]
    assert {
        "criterion_id", "description", "kind", "required", "command", "covers"
    } == set(verification_schema["required"])
    assert verification_schema["properties"]["kind"]["enum"] == [
        "static", "structural", "build", "behavioral", "runtime", "manual"
    ]


def test_integration_schema_allows_empty_contract_catalog_without_empty_enum():
    requirements = [
        Requirement("REQ-001", "Create README.md", RequirementKind.FUNCTIONAL),
    ]
    architecture = [
        ArchitectureModule(
            "MOD-001", "Root", "Root documentation",
            file_paths=("README.md",),
        )
    ]

    schema = integration_json_schema(requirements, architecture, contracts=[])
    implements_schema = (
        schema["properties"]["implementation_units"]["items"]
        ["properties"]["implements"]
    )

    # Ollama's schema converter rejects enum=[]; with no contracts, the array
    # is constrained to be empty instead of accepting invented contract IDs.
    assert implements_schema["maxItems"] == 0
    assert implements_schema["items"] == {"type": "string"}


def test_generator_selects_a_distinct_schema_for_each_espec_stage():
    captured = []

    class Client:
        def generate_json(self, prompt, *, schema=None):
            captured.append(schema)
            if prompt.startswith("Extract a precise requirements model"):
                return {
                    "requirements": [{
                        "requirement_id": "REQ-001",
                        "description": "Create README.md",
                        "kind": "functional",
                        "acceptance_criteria": ["README exists"],
                        "priority": "normal",
                    }]
                }
            if prompt.startswith("PHASE: ARCHITECTURE DESIGN"):
                return {
                    "architecture": [{
                        "module_id": "MOD-001",
                        "name": "Root",
                        "responsibility": "Project documentation",
                        "depends_on": [],
                        "file_paths": ["README.md"],
                    }]
                }
            if prompt.startswith("PHASE: IMPLEMENTATION PLANNING — CONTRACTS"):
                return {
                    "contracts": [{
                        "contract_id": "CON-001",
                        "owner": "MOD-001",
                        "name": "Documentation contract",
                        "kind": "data",
                        "inputs": [],
                        "outputs": [],
                        "invariants": [],
                    }]
                }
            return {
                "implementation_units": [{
                    "unit_id": "UNIT-001",
                    "title": "Create README",
                    "target_path": "README.md",
                    "kind": "documentation",
                    "depends_on": [],
                    "satisfies": ["REQ-001"],
                    "implements": ["CON-001"],
                    "acceptance_criteria": ["README exists"],
                }],
                "verification": [{
                    "criterion_id": "VER-001",
                    "description": "Check README exists",
                    "kind": "structural",
                    "required": True,
                    "command": [],
                    "covers": ["REQ-001"],
                }],
            }

    generator = EngineeringSpecificationGenerator(Client())
    specification = generator.generate(
        task_id="schema-wiring-test",
        objective="Create a README",
        workspace="/tmp/schema-wiring-test",
    )

    assert specification.status.value == "validated"
    assert len(captured) == 4
    assert [set(schema["properties"]) for schema in captured] == [
        {"requirements"},
        {"architecture"},
        {"contracts"},
        {"implementation_units", "verification"},
    ]


def test_contract_schema_allows_no_contracts_for_non_interface_scaffolding():
    from coding_agent.espec_generator import CONTRACTS_JSON_SCHEMA
    from coding_agent.espec_generator import EngineeringSpecificationGenerator

    class Client:
        def generate_json(self, prompt, *, schema=None):
            return {"contracts": []}

    generator = EngineeringSpecificationGenerator(Client())
    assert generator._parse_contracts({"contracts": []}) == []
    assert CONTRACTS_JSON_SCHEMA["properties"]["contracts"]["minItems"] == 0
