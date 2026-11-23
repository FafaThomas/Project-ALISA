"""Hybrid, staged generation of task-scoped Engineering Specifications."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import inspect
from pathlib import Path, PurePosixPath
import re
from typing import Any, Callable, Mapping, Type

from pydantic import BaseModel

from coding_agent.engineering_spec import (
    ArchitectureModule,
    EngineeringSpecification,
    ImplementationUnit,
    InterfaceContract,
    InterfaceField,
    Requirement,
    RequirementKind,
    SpecificationStage,
    SpecificationStatus,
    UnitKind,
    VerificationCriterion,
    VerificationKind,
)
from coding_agent.schemas.espec import (
    ArchitectureStagePayload,
    ContractsStagePayload,
    IntegrationStagePayload,
    RequirementsStagePayload,
)


# Ollama accepts a JSON Schema object in /api/chat's ``format`` field. These
# schemas constrain response shape; the parsers below remain authoritative for
# semantic rules such as reference integrity and requirement coverage.
def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _array_schema(items: dict[str, Any], *, min_items: int = 0) -> dict[str, Any]:
    return {"type": "array", "items": items, "minItems": min_items}


def _string_array_schema() -> dict[str, Any]:
    return _array_schema({"type": "string"})


_REQUIREMENT_ITEM_SCHEMA = _object_schema(
    {
        "requirement_id": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "enum": ["functional", "non_functional", "constraint"]},
        "acceptance_criteria": _string_array_schema(),
        "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
    },
    ["requirement_id", "description", "kind", "acceptance_criteria", "priority"],
)

REQUIREMENTS_JSON_SCHEMA = _object_schema(
    {"requirements": _array_schema(_REQUIREMENT_ITEM_SCHEMA, min_items=1)},
    ["requirements"],
)

_ARCHITECTURE_MODULE_SCHEMA = _object_schema(
    {
        "module_id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "responsibility": {"type": "string", "minLength": 1},
        "depends_on": _string_array_schema(),
        "file_paths": _string_array_schema(),
    },
    ["module_id", "name", "responsibility", "depends_on", "file_paths"],
)

ARCHITECTURE_JSON_SCHEMA = _object_schema(
    {"architecture": _array_schema(_ARCHITECTURE_MODULE_SCHEMA, min_items=1)},
    ["architecture"],
)

_INTERFACE_FIELD_SCHEMA = _object_schema(
    {
        "name": {"type": "string", "minLength": 1},
        "type_name": {"type": "string", "minLength": 1},
        "required": {"type": "boolean"},
        "description": {"type": "string"},
    },
    ["name", "type_name", "required", "description"],
)

_CONTRACT_ITEM_SCHEMA = _object_schema(
    {
        "contract_id": {"type": "string", "minLength": 1},
        "owner": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "enum": ["component", "function", "data", "api", "event"]},
        "inputs": _array_schema(_INTERFACE_FIELD_SCHEMA),
        "outputs": _array_schema(_INTERFACE_FIELD_SCHEMA),
        "invariants": _string_array_schema(),
    },
    ["contract_id", "owner", "name", "kind", "inputs", "outputs", "invariants"],
)

CONTRACTS_JSON_SCHEMA = _object_schema(
    {"contracts": _array_schema(_CONTRACT_ITEM_SCHEMA)},
    ["contracts"],
)


def integration_json_schema(
    requirements: list[Requirement] | tuple[Requirement, ...],
    architecture: list[ArchitectureModule] | tuple[ArchitectureModule, ...],
    contracts: list[InterfaceContract] | tuple[InterfaceContract, ...],
) -> dict[str, Any]:
    """Build the integration schema using IDs and paths from prior stages."""
    requirement_ids = sorted(item.requirement_id for item in requirements)
    acceptance_criterion_ids = sorted(
        criterion_id
        for requirement in requirements
        for criterion_id in requirement.acceptance_criterion_ids
    )
    contract_ids = sorted(item.contract_id for item in contracts)
    architecture_paths = {
        path.replace("\\", "/")
        for module in architecture
        for path in module.file_paths
    }
    # Permit exact architecture files and their real parent directories only.
    target_paths = set()
    for path in architecture_paths:
        parts = path.split("/")
        for index in range(1, len(parts)):
            target_paths.add("/".join(parts[:index]))
        target_paths.add(path)

    implementation_unit_schema = _object_schema(
        {
            "unit_id": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1},
            "target_path": {
                "type": "string",
                "enum": sorted(target_paths),
            },
            "kind": {
                "type": "string",
                "enum": ["source", "test", "configuration", "documentation", "asset"],
            },
            "depends_on": _string_array_schema(),
            "satisfies": _array_schema({"type": "string", "enum": requirement_ids}),
            # Ollama rejects enum=[]. When no contracts were generated, the
            # only valid implementation is to reference no contracts at all.
            "implements": (
                _array_schema({"type": "string", "enum": contract_ids})
                if contract_ids
                else {**_string_array_schema(), "maxItems": 0}
            ),
            "acceptance_criteria": _string_array_schema(),
            "implements_acceptance_criteria": (
                _array_schema({"type": "string", "enum": acceptance_criterion_ids})
                if acceptance_criterion_ids
                else {**_string_array_schema(), "maxItems": 0}
            ),
        },
        [
            "unit_id", "title", "target_path", "kind", "depends_on",
            "satisfies", "implements", "acceptance_criteria",
            "implements_acceptance_criteria",
        ],
    )
    verification_schema = _object_schema(
        {
            "criterion_id": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "kind": {
                "type": "string",
                "enum": ["static", "structural", "build", "behavioral", "runtime", "manual"],
            },
            "required": {"type": "boolean"},
            "command": {**_string_array_schema(), "description": "Executable command arguments for automated verification. MUST be [] when kind is manual; put human inspection steps in description."},
            # Direct requirement coverage is sufficient and avoids requiring
            # the model to predict implementation-unit IDs in another field.
            "covers": _array_schema({"type": "string", "enum": requirement_ids}),
            "covers_acceptance_criteria": (
                _array_schema({"type": "string", "enum": acceptance_criterion_ids})
                if acceptance_criterion_ids
                else {**_string_array_schema(), "maxItems": 0}
            ),
        },
        ["criterion_id", "description", "kind", "required", "command", "covers", "covers_acceptance_criteria"],
    )
    return _object_schema(
        {
            "implementation_units": _array_schema(implementation_unit_schema),
            "verification": _array_schema(verification_schema, min_items=1),
        },
        ["implementation_units", "verification"],
    )


class SpecificationGenerationError(RuntimeError):
    """Raised when an ESpec stage cannot produce a valid structured result."""


@dataclass(frozen=True)
class GenerationTrace:
    stage: str
    attempts: int
    success: bool
    error: str = ""


class EngineeringSpecificationGenerator:
    """Generate an ESpec through requirements, architecture, contracts and integration.

    The client must expose ``generate_json(prompt) -> dict`` and may also
    accept an optional ``schema=`` keyword for structured generation. Each
    stage is schema-checked locally; the model never marks its own output valid.
    """

    def __init__(
        self, client: Any, *, max_stage_attempts: int = 2,
        diagnostics_dir: str | Path | None = None,
    ) -> None:
        if not 1 <= max_stage_attempts <= 5:
            raise ValueError("max_stage_attempts must be between 1 and 5")
        if not callable(getattr(client, "generate_json", None)):
            raise TypeError("client must provide generate_json(prompt)")
        self.client = client
        self.max_stage_attempts = max_stage_attempts
        self.last_trace: tuple[GenerationTrace, ...] = ()
        self.diagnostics_dir = Path(diagnostics_dir) if diagnostics_dir is not None else None
        self._validation_errors: list[dict[str, Any]] = []

    def _write_diagnostic(self, filename: str, payload: Any) -> bool:
        # Best-effort only: diagnostic I/O must never mask the original result.
        if self.diagnostics_dir is None:
            return False
        try:
            self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
            target = self.diagnostics_dir / filename
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            temporary.replace(target)
            return True
        except Exception:
            return False

    @staticmethod
    def _trace_payload(trace) -> list[dict[str, Any]]:
        return [
            {"stage": item.stage, "attempts": item.attempts,
             "success": item.success, "error": item.error}
            for item in trace
        ]

    def _persist_validation_errors(self) -> None:
        self._write_diagnostic("validation_errors.json", self._validation_errors)

    def generate(
        self,
        *,
        task_id: str,
        objective: str,
        workspace: str,
        constraints: tuple[str, ...] | list[str] = (),
        acceptance_criteria: tuple[str, ...] | list[str] = (),
        project_context: str = "",
    ) -> EngineeringSpecification:
        for name, value in (("task_id", task_id), ("objective", objective), ("workspace", workspace)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")

        trace: list[GenerationTrace] = []
        self._validation_errors = []

        requirements_data = self._stage(
            "requirements", trace,
            f"""Extract a precise requirements model for this software objective.
Return JSON: {{"requirements":[{{"requirement_id":"REQ-001","description":"...","kind":"functional|non_functional|constraint","acceptance_criteria":["..."],"priority":"low|normal|high|critical"}}]}}
Workspace inventory/inspection is a context-preparation precondition, not a deliverable by itself. If the task also requires creating a directory layout, express that as the functional requirement; do not bundle "inspect the workspace" into the implementation obligation.
Classify requirements by what the requirement obligates the implementation to do:
- functional: a product capability or concrete project artifact that must be created or changed.
- non_functional: a quality attribute of the product or artifact, such as security, reliability, performance, compatibility, or maintainability. It still needs implementation-unit coverage.
- constraint: a boundary, prohibition, preservation rule, reporting obligation, environment condition, or verification-only obligation that constrains or checks the work but does not itself require a product artifact to be created or changed. It requires verification coverage, not an implementation unit.
In particular, classify a negative technology prohibition as a constraint when it only forbids an implementation choice and does not direct this work package to create or modify a concrete artifact. For example, "Do not use SQLite for runtime" or "the runtime must not use SQLite" is a constraint when no application database configuration is being implemented in this work package. By contrast, "configure Django to connect to PostgreSQL through environment variables" is a functional implementation requirement. Do not label a prohibition non_functional merely because it describes an undesirable runtime property.
In particular, requirements phrased solely as "verify/check/validate/report whether X" are verification-only constraints, not non-functional implementation requirements. For example, "verify Compose syntax if Docker is available" is a constraint; "the Compose configuration must use a valid supported syntax" is a non-functional requirement if it describes a quality property of the delivered artifact.
Split mixed requirements when one part requires a concrete implementation and another part is only a prohibition or preservation rule. Do not combine a later work package's implementation task with the current package's guardrail. For every functional or non-functional requirement, name the concrete file or artifact to create or modify in its description and at least one acceptance criterion whenever that target is known from the objective, constraints, or project context. Use canonical paths and filenames (for example, docker-compose.yml, not a vague phrase such as "service configuration"). For service startup dependencies and health-conditioned Compose ordering, explicitly identify docker-compose.yml as the implementation target when Compose is part of the approved stack.
Include explicit functional and relevant non-functional requirements. Make acceptance criteria observable and testable. Do not invent user requirements beyond reasonable engineering necessities.
OBJECTIVE: {objective}
CONSTRAINTS: {list(constraints)}
USER ACCEPTANCE CRITERIA: {list(acceptance_criteria)}
PROJECT CONTEXT: {project_context}""",
            lambda d: self._parse_requirements(d),
            schema=REQUIREMENTS_JSON_SCHEMA,
            payload_model=RequirementsStagePayload,
        )
        requirements = requirements_data

        mandatory_architecture_paths = self._required_architecture_paths(requirements)
        mandatory_paths_text = (
            "\n".join(f"- {path}" for path in sorted(mandatory_architecture_paths))
            if mandatory_architecture_paths
            else "- None inferred from explicit file requirements."
        )
        architecture = self._stage(
            "architecture", trace,
            f"""Design a minimal coherent modular architecture satisfying the requirements.
Return JSON: {{"architecture":[{{"module_id":"MOD-001","name":"...","responsibility":"...","depends_on":[],"file_paths":["relative/path"]}}]}}
Use workspace-relative file paths. List concrete files, not vague directory labels. Every concrete filename positively required to be created or modified in a requirement or acceptance criterion MUST appear in architecture file_paths, in the correct module. Do NOT list files that the requirement explicitly says must NOT be created, added, committed, or included. For example, a requirement to create root .env.example while explicitly prohibiting a real .env requires .env.example and forbids .env; do not declare .env as a deliverable. For example, if a requirement says root docker-compose.yml, include exactly docker-compose.yml in the Root module. If a requirement calls for root, frontend, and backend .dockerignore files, include all three exact paths. Include appropriate test files in the relevant modules when tests are required by the requirements. Use consistent canonical names and extensions (for example, docker-compose.yml is not docker-compose.yaml). Dependencies must reference module IDs in this response. Avoid unnecessary modules.
MANDATORY FILE PATHS DERIVED FROM THE REQUIREMENTS (every path below MUST appear in architecture.file_paths):
{mandatory_paths_text}
Assign root-level files to the root/repository module. Assign service-specific files to the module responsible for that service. Do not omit a mandatory path merely because it appears in an acceptance criterion rather than the requirement description.
OBJECTIVE: {objective}
REQUIREMENTS: {[self._requirement_dict(x) for x in requirements]}
PROJECT CONTEXT: {project_context}""",
            lambda data: self._parse_architecture(data, requirements),
            schema=ARCHITECTURE_JSON_SCHEMA,
            payload_model=ArchitectureStagePayload,
        )

        self._known_module_ids = {module.module_id for module in architecture}
        contracts = self._stage(
            "contracts", trace,
            f"""Define explicit interfaces and data contracts between the proposed modules, only where such interfaces genuinely exist.
Return JSON: {{"contracts":[{{"contract_id":"CON-001","owner":"MOD-001","name":"...","kind":"component|function|data|api|event","inputs":[{{"name":"...","type_name":"...","required":true,"description":""}}],"outputs":[{{"name":"...","type_name":"...","required":true,"description":""}}],"invariants":["..."]}}]}}
Do not create contracts for workspace inspection, file creation checklists, verification commands, reporting obligations, prohibitions, preservation rules, or scope guardrails. Those are requirements/verification concerns, not software interfaces. Infrastructure scaffolding and documentation tasks may legitimately have an empty contracts array. Every returned contract owner must reference an architecture module. Make actual interfaces compatible and unambiguous.
REQUIREMENTS: {[self._requirement_dict(x) for x in requirements]}
ARCHITECTURE: {[self._module_dict(x) for x in architecture]}""",
            self._parse_contracts,
            schema=CONTRACTS_JSON_SCHEMA,
            payload_model=ContractsStagePayload,
        )

        required_path_mapping = self._required_architecture_paths(requirements)
        required_path_mapping_text = (
            "\\n".join(
                f"- {path}: {', '.join(sorted(requirement_ids))}"
                for path, requirement_ids in sorted(required_path_mapping.items())
            )
            if required_path_mapping
            else "- None."
        )
        required_directory_mapping = self._required_architecture_directories(requirements)
        required_directory_mapping_text = (
            "\\n".join(
                f"- {directory}/: {', '.join(sorted(requirement_ids))}"
                for directory, requirement_ids in sorted(required_directory_mapping.items())
            )
            if required_directory_mapping
            else "- None."
        )
        integration = self._stage(
            "integration", trace,
            f"""Create implementation units and verification criteria that integrate this design.
Create implementation units for every functional and non-functional requirement. Constraint requirements are NOT file-creation tasks and do not require implementation units; they MUST still have explicit verification criteria.
For every requirement that names multiple concrete files, create a separate implementation unit for EACH named file. Do not bundle root/frontend/backend .dockerignore files into one unit. A unit has exactly one target_path. Multiple units may satisfy the same REQ-ID.
PATH CONTRACT (mandatory):
- All paths are relative to the workspace root. Never prepend "root/" or another workspace-folder name.
- For work that creates or changes a file, target_path MUST exactly match one concrete file path in ARCHITECTURE.file_paths.
- When a directory-layout requirement also names files, create concrete file units for those files beneath the required directories. Do not attach directory existence to incidental child-file units.
- Do not use "." as an implementation target or mkdir target; workspace inspection is not a directory-creation operation. Directory-only deliverables remain structural requirements and are represented by explicit mkdir operations in the implementation plan; do not invent marker files.
- Do not invent alternate filenames or extensions. Include test paths explicitly in ARCHITECTURE before creating test implementation units.
For each required file path named by a requirement, create at least one unit satisfying that requirement whose target_path is exactly that file path.
AUTHORITATIVE REQUIRED-FILE MAPPING (path -> requirement ID(s)):
{required_path_mapping_text}
AUTHORITATIVE REQUIRED-DIRECTORY MAPPING (directory -> requirement ID(s)):
{required_directory_mapping_text}
For each required directory, ensure that implementation units create at least one required artifact beneath that directory. When a directory requirement names several directories, units beneath EVERY named directory must satisfy that requirement; include a directory-existence acceptance criterion on each corresponding child-file unit. Do not invent marker files solely to make an empty directory persist.
Create only one implementation unit per unique target_path. If several activities affect the same file, combine them into one unit with all applicable REQ-IDs, contract IDs, and acceptance criteria. Do not create competing units that write the same file.
Do NOT create implementation units for workspace inspection, verification, syntax checks, reporting, scope guardrails, prohibitions, or "no unrelated features" rules. These are preconditions, constraints, or verification criteria. Keep them in verification[] and/or the constraint requirements. Implementation units must describe concrete file creation or modification only.
Verification-only activities such as "Verify Docker Compose Syntax" belong in verification[], never in implementation_units[].
Use these mappings when assigning implementation_units[].satisfies. Do not assign a file unit to an unrelated requirement merely because it shares a nearby concern.
Verification criteria must cover only requirements their descriptions and checks genuinely verify. Do not use generic overlap such as "root directory" or "file exists" as evidence that a criterion verifies a specific directory or file requirement.

IMPORTANT: The "kind" fields in this response belong to different enums and MUST NOT be copied between objects:
- requirements[].kind classifies the requirement: functional, non_functional, or constraint.
- implementation_units[].kind classifies the artifact being created: source, test, configuration, documentation, or asset.
- verification[].kind classifies the verification method: static, structural, build, behavioral, runtime, or manual.
A requirement whose kind is "constraint" does NOT make its implementation unit kind "constraint". For example, implementing a Docker Compose requirement produces an implementation unit with kind "configuration"; implementing a test requirement produces kind "test"; implementing application code produces kind "source". Choose implementation_units[].kind from the artifact's actual nature, not from the requirement it satisfies.

Return JSON: {{"implementation_units":[{{"unit_id":"UNIT-001","title":"...","target_path":"relative/path","kind":"source|test|configuration|documentation|asset","depends_on":[],"satisfies":["REQ-001"],"implements":["CON-001"],"acceptance_criteria":["..."],"implements_acceptance_criteria":["REQ-001-AC-a1b2c3d4e5f6"]}}],"verification":[{{"criterion_id":"VER-001","description":"...","kind":"static|structural|build|behavioral|runtime|manual","required":true,"command":[],"covers":["REQ-001","UNIT-001"],"covers_acceptance_criteria":["REQ-001-AC-a1b2c3d4e5f6"]}}]}}
Use only the canonical verification kinds listed above. API, unit, integration, end-to-end,
and generic tests use "behavioral"; linting and type checks use "static"; smoke checks use
"runtime"; human review uses "manual".
For every verification criterion with kind "manual", command MUST be an empty array ([]).
Put all human inspection actions and step-by-step instructions in the description field.
The command field is reserved for executable command arguments for non-manual criteria;
never put natural-language instructions in command.
All references must point to IDs supplied below. Every functional and non-functional requirement must be covered by at least one implementation unit. Every acceptance criterion has a stable ID shown in REQUIREMENTS as acceptance_criterion_ids. Each implementation unit MUST list the exact acceptance criterion IDs it implements in implements_acceptance_criteria; do not claim criteria merely because the unit satisfies the parent requirement. Each acceptance criterion MUST also be covered by one or more verification criteria using covers_acceptance_criteria. A verification criterion may additionally cover a requirement directly by listing its REQ-ID in "covers", or indirectly by listing a UNIT-ID whose "satisfies" includes that requirement. Prefer direct references where practical. Ensure each implementation unit's target_path is consistent with the architecture paths supplied below. Do not execute commands or modify files.
REQUIREMENTS: {[self._requirement_dict(x) for x in requirements]}
ARCHITECTURE: {[self._module_dict(x) for x in architecture]}
CONTRACTS: {[self._contract_dict(x) for x in contracts]}""",
            lambda data: self._parse_integration(data, requirements, architecture, contracts),
            schema=integration_json_schema(requirements, architecture, contracts),
            payload_model=IntegrationStagePayload,
        )
        units, verification = integration

        spec = EngineeringSpecification(
            specification_id=f"espec-{self._slug(task_id)}",
            task_id=task_id,
            objective=objective,
            workspace=workspace,
            requirements=tuple(requirements),
            architecture=tuple(architecture),
            contracts=tuple(contracts),
            implementation_units=tuple(units),
            verification=tuple(verification),
            generation_stages=(
                SpecificationStage.REQUIREMENTS,
                SpecificationStage.ARCHITECTURE,
                SpecificationStage.CONTRACTS,
                SpecificationStage.INTEGRATION,
            ),
            execution_profile="sandbox",
            status=SpecificationStatus.DRAFT,
            metadata={"generator": "hybrid_staged_v1"},
        )
        self._validate_coverage(spec)
        spec = replace(spec, status=SpecificationStatus.VALIDATED)
        self.last_trace = tuple(trace)
        self._write_diagnostic("generation_trace.json", self._trace_payload(trace))
        return spec

    def _generate_json(
        self, prompt: str, *, schema: Mapping[str, Any] | None = None
    ) -> dict:
        """Call schema-aware clients while retaining legacy client compatibility."""
        method = self.client.generate_json
        if schema is None:
            return method(prompt)
        try:
            parameters = inspect.signature(method).parameters.values()
        except (TypeError, ValueError):
            # Some extension/builtin callables do not expose signatures. Keep
            # their historical one-argument contract rather than guessing.
            return method(prompt)
        supports_schema = any(
            parameter.name == "schema"
            or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if supports_schema:
            return method(prompt, schema=schema)
        return method(prompt)

    def _stage(
        self,
        name: str,
        trace: list[GenerationTrace],
        prompt: str,
        parser: Callable,
        *,
        schema: Mapping[str, Any] | None = None,
        payload_model: Type[BaseModel] | None = None,
    ):
        last_error = ""
        previous_response = None
        for attempt in range(1, self.max_stage_attempts + 1):
            response = None
            response_saved = False
            try:
                if attempt == 1:
                    request_prompt = prompt
                else:
                    previous_output = (
                        json.dumps(previous_response, ensure_ascii=False, indent=2, default=str)
                        if previous_response is not None
                        else "[No structured response was returned by the previous attempt.]"
                    )
                    request_prompt = (
                        f"{prompt}\n\n"
                        f"PREVIOUS OUTPUT INVALID: {last_error}\n\n"
                        "PREVIOUS OUTPUT (the exact response from the immediately preceding attempt):\n"
                        f"{previous_output}\n\n"
                        "REPAIR RULES: Return the complete corrected JSON object. "
                        "Treat PREVIOUS OUTPUT as the source of truth for all content that "
                        "was not identified by the validation error. Preserve all previously "
                        "valid IDs, fields, target paths, dependencies, requirement mappings, "
                        "acceptance criteria, and verification criteria. Make the smallest "
                        "changes necessary to fix the listed validation errors. Do not remove "
                        "valid units or change their target_path values unless the diagnostic "
                        "explicitly identifies those values as invalid. Do not replace valid "
                        "paths with empty strings, null, placeholders, or paths prefixed with "
                        "'root/'. Re-check every requirement and every target path before "
                        "returning JSON only."
                    )
                response = self._generate_json(request_prompt, schema=schema)
                response_saved = self._write_diagnostic(f"{name}_attempt_{attempt}.json", response)
                if not isinstance(response, Mapping):
                    raise ValueError("response must be a JSON object")
                if payload_model is not None:
                    # Validate the model boundary before domain parsing. Keep the
                    # existing parser as the authority for normalization and
                    # engineering-specific semantic checks.
                    response = payload_model.model_validate(response).model_dump(
                        mode="python"
                    )
                result = parser(response)
                self._write_diagnostic(f"{name}.json", response)
                trace.append(GenerationTrace(name, attempt, True))
                self.last_trace = tuple(trace)
                self._write_diagnostic("generation_trace.json", self._trace_payload(trace))
                return result
            except Exception as exc:
                last_error = str(exc)
                self._validation_errors.append({
                    "stage": name, "attempt": attempt,
                    "exception_type": type(exc).__name__,
                    "error": last_error, "response_saved": response_saved,
                })
                self._persist_validation_errors()
                previous_response = response
        trace.append(GenerationTrace(name, self.max_stage_attempts, False, last_error))
        self.last_trace = tuple(trace)
        self._write_diagnostic("generation_trace.json", self._trace_payload(trace))
        raise SpecificationGenerationError(
            f"ESpec {name} stage failed after {self.max_stage_attempts} attempt(s): {last_error}"
        )

    @staticmethod
    def _items(data: Mapping[str, Any], key: str, *, allow_empty: bool = False) -> list:
        value = data.get(key)
        if not isinstance(value, list) or (not allow_empty and not value):
            qualifier = "an array" if allow_empty else "a non-empty array"
            raise ValueError(f"{key} must be {qualifier}")
        if not all(isinstance(item, Mapping) for item in value):
            raise ValueError(f"every {key} item must be an object")
        return value

    @staticmethod
    def _is_prohibition_only_runtime_database_requirement(description, acceptance_criteria):
        """Identify a narrow SQLite runtime prohibition with no implementation directive.

        This is an architectural guardrail, not a request to create application
        database configuration. A positive instruction such as "configure Django
        to use PostgreSQL" deliberately does not match this rule.
        """
        text = " ".join((description, *acceptance_criteria)).casefold()
        if "sqlite" not in text or not re.search(r"\bruntime\b", text):
            return False
        negative_sqlite = re.search(
            r"(?:does\s+not\s+use|do\s+not\s+use|must\s+not\s+use|"
            r"should\s+not\s+use|not\s+use|no)\s+sqlite\b|"
            r"sqlite\b[^.;]*\b(?:must\s+not|should\s+not|is\s+not|cannot)\b",
            text,
        )
        if not negative_sqlite:
            return False
        # Only an explicit implementation verb in the requirement description
        # turns this into a task to change application/configuration artifacts.
        implementation_directive = re.search(
            r"\b(?:configure|implement|create|add|modify|update|write|set\s+up|"
            r"connect|migrate|switch)\b",
            description.casefold(),
        )
        return implementation_directive is None

    def _parse_requirements(self, data):
        result = []
        repairs = []
        for x in self._items(data, "requirements"):
            description = x["description"]
            acceptance_criteria = tuple(x.get("acceptance_criteria", ()))
            kind = RequirementKind(x.get("kind", "functional"))
            # An explicit instruction to add/create/write tests is itself a
            # concrete artifact obligation, not merely a compliance boundary.
            if (
                kind == RequirementKind.CONSTRAINT
                and re.search(r"\b(?:add|create|write|implement)\b", description, re.I)
                and re.search(r"\btests?\b", description, re.I)
                and not re.search(r"\b(?:do not|don't|must not|never)\b", description, re.I)
            ):
                repairs.append({
                    "requirement_id": x["requirement_id"],
                    "previous_kind": kind.value,
                    "kind": RequirementKind.FUNCTIONAL.value,
                    "reason": "explicitly requires test artifacts to be added or changed",
                })
                kind = RequirementKind.FUNCTIONAL
            if (
                kind != RequirementKind.CONSTRAINT
                and self._is_prohibition_only_runtime_database_requirement(
                    description, acceptance_criteria
                )
            ):
                repairs.append({
                    "requirement_id": x["requirement_id"],
                    "previous_kind": kind.value,
                    "kind": RequirementKind.CONSTRAINT.value,
                    "reason": (
                        "the requirement only prohibits SQLite for runtime and does not "
                        "direct implementation of an application database configuration"
                    ),
                })
                kind = RequirementKind.CONSTRAINT
            result.append(Requirement(
                requirement_id=x["requirement_id"], description=description,
                kind=kind,
                acceptance_criteria=acceptance_criteria,
                priority=x.get("priority", "normal"),
                acceptance_criterion_ids=tuple(x.get("acceptance_criterion_ids", ())),))
        self._unique_ids(result, "requirement_id")
        if repairs:
            self._write_diagnostic("requirements_normalization.json", repairs)
            self._write_diagnostic(
                "requirements_normalized.json",
                {"requirements": [self._requirement_dict(item) for item in result]},
            )
        return result

    @staticmethod
    def _explicit_requirement_paths(requirement):
        """Infer concrete file paths positively required by one requirement.

        Bare filenames are treated as root-level only when the requirement says
        root. Prohibited deliverables are excluded. Service-specific directory
        expansion is handled by _required_architecture_paths using the complete
        requirements set, because service names may be introduced elsewhere.
        """
        text = " ".join((requirement.description, *requirement.acceptance_criteria))
        paths = set()
        file_pattern = re.compile(
            r"(?<![A-Za-z0-9_./-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:yml|yaml|json|md|txt|toml|py|tsx?|jsx?|html|css|sql)(?!(?:[A-Za-z0-9_-]|\.[A-Za-z0-9_-]))"
            r"|(?<![A-Za-z0-9_./-])\.(?:dockerignore|gitignore|env(?:\.[A-Za-z0-9_-]+)?)(?!(?:[A-Za-z0-9_-]|\.[A-Za-z0-9_-]))",
            re.IGNORECASE,
        )
        for match in file_pattern.finditer(text):
            token = match.group(0).replace("\\", "/")

            # A filename mentioned only as a prohibited deliverable is not
            # an architecture requirement. In particular, "Do not create a
            # real .env" must not cause the validator to demand .env.
            clause_start = max(
                text.rfind(".", 0, match.start()),
                text.rfind(";", 0, match.start()),
                text.rfind("!", 0, match.start()),
                text.rfind("?", 0, match.start()),
                -1,
            ) + 1
            preceding_clause = text[clause_start:match.start()]
            prohibits_creation = re.search(
                r"\b(?:do\s+not|don't|must\s+not|should\s+not|never)\s+"
                r"(?:create|add|include|generate|write|commit)\b",
                preceding_clause,
                re.IGNORECASE,
            )
            prohibits_real_file = re.search(
                r"\bno\s+(?:real\s+)?(?:root\s+)?$",
                preceding_clause,
                re.IGNORECASE,
            )
            if prohibits_creation or prohibits_real_file:
                continue

            if "/" in token:
                paths.add(token)
                continue
            if re.search(r"\broot\b", text, re.IGNORECASE):
                paths.add(token)
        if (
            re.search(r"\bREADME(?:\.md)?\b", text, re.IGNORECASE)
            and re.search(r"\broot\b", text, re.IGNORECASE)
        ):
            paths.add("README.md")
        return paths

    @staticmethod
    def _explicit_requirement_basenames(requirement):
        """Return positively referenced bare filenames for contextual path resolution.

        A bare filename is not assumed to be root-level. It can be resolved only
        when another requirement or the architecture establishes one unique path
        with the same basename.
        """
        text = " ".join((requirement.description, *requirement.acceptance_criteria))
        pattern = re.compile(
            r"(?<![A-Za-z0-9_./-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:yml|yaml|json|md|txt|toml|py|tsx?|jsx?|html|css|sql)(?!(?:[A-Za-z0-9_-]|\.[A-Za-z0-9_-]))"
            r"|(?<![A-Za-z0-9_./-])\.(?:dockerignore|gitignore|env(?:\.[A-Za-z0-9_-]+)?)(?!(?:[A-Za-z0-9_-]|\.[A-Za-z0-9_-]))",
            re.IGNORECASE,
        )
        result = set()
        for match in pattern.finditer(text):
            token = match.group(0).replace("\\", "/")
            if "/" in token or re.search(r"\broot\b", text, re.IGNORECASE):
                continue
            clause_start = max(
                text.rfind(".", 0, match.start()),
                text.rfind(";", 0, match.start()),
                text.rfind("!", 0, match.start()),
                text.rfind("?", 0, match.start()),
                -1,
            ) + 1
            preceding_clause = text[clause_start:match.start()]
            if re.search(
                r"\b(?:do\s+not|don't|must\s+not|should\s+not|never)\s+"
                r"(?:create|add|include|generate|write|commit)\b",
                preceding_clause,
                re.IGNORECASE,
            ):
                continue
            result.add(token.casefold())
        return result

    @staticmethod
    def _explicit_requirement_directories(requirement):
        """Return explicitly named directories for requirements about repository layout."""
        text = " ".join((requirement.description, *requirement.acceptance_criteria))
        if not re.search(
            r"\b(?:directory|directories|folder|folders|area|areas|layout|structure)\b",
            text,
            re.IGNORECASE,
        ):
            return set()
        return {
            match.group(1).rstrip("/").replace("\\", "/")
            for match in re.finditer(
                r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+)/",
                text,
            )
            if match.group(1).lower() not in {"root", "."}
        }

    @classmethod
    def _has_directory_obligation(cls, requirement):
        """Whether a requirement explicitly commits to workspace directory layout."""
        return (
            requirement.kind != RequirementKind.CONSTRAINT
            and bool(cls._explicit_requirement_directories(requirement))
        )

    @classmethod
    def _is_directory_only_requirement(cls, requirement):
        """Return True for directory existence/layout obligations without file work."""
        return cls._has_directory_obligation(requirement) and not bool(
            cls._explicit_requirement_paths(requirement)
        )

    @classmethod
    def _required_architecture_directories(cls, requirements):
        """Map explicitly required directory paths to their owning requirements."""
        result = {}
        for requirement in requirements:
            if requirement.kind == RequirementKind.CONSTRAINT:
                continue
            for directory in cls._explicit_requirement_directories(requirement):
                result.setdefault(directory, set()).add(requirement.requirement_id)
        return result

    @classmethod
    def _required_architecture_paths(cls, requirements):
        """Return only file paths positively required by task requirements.

        A directory mentioned by a separate layout requirement does not imply
        a service-specific file. Conditional "as needed" wording is not a
        mandatory deliverable.
        """
        required = {}
        task_directories = set().union(*(
            cls._explicit_requirement_directories(item)
            for item in requirements
            if item.kind != RequirementKind.CONSTRAINT
        )) if requirements else set()
        for requirement in requirements:
            if requirement.kind == RequirementKind.CONSTRAINT:
                continue
            requirement_text = " ".join(
                (requirement.description, *requirement.acceptance_criteria)
            )
            paths = cls._explicit_requirement_paths(requirement)
            conditional = bool(re.search(
                r"\b(?:as needed|if needed|when needed|where appropriate|if applicable)\b",
                requirement_text,
                re.IGNORECASE,
            ))
            if ".dockerignore" in requirement_text.casefold() and not conditional:
                service_directories = set(
                    cls._explicit_requirement_directories(requirement)
                )
                if re.search(
                    r"\b(?:each|every|service[- ]appropriate|service)\s+"
                    r"(?:service\s+)?directories?\b|\beach service directory\b",
                    requirement_text,
                    re.IGNORECASE,
                ):
                    service_directories.update(task_directories)
                for match in re.finditer(
                    r"\broot\s*,\s*([^.;]+?)\s+\.dockerignore\b",
                    requirement_text,
                    re.IGNORECASE,
                ):
                    service_directories.update(
                        name for name in re.findall(
                            r"[A-Za-z][A-Za-z0-9_-]*", match.group(1)
                        )
                        if name.casefold() not in {
                            "and", "service", "services", "appropriate"
                        }
                    )
                paths.update(
                    f"{directory}/.dockerignore"
                    for directory in service_directories
                    if directory.casefold() not in {"root", "."}
                )
            for path in paths:
                required.setdefault(path.replace("\\", "/"), set()).add(
                    requirement.requirement_id
                )

        # Broad repository-ignore quality requirements may apply to all
        # ignore files already established by explicit file obligations, but
        # they must never create additional service-specific paths.
        known_ignore_paths = sorted(
            path for path in required
            if PurePosixPath(path).name.casefold() == ".dockerignore"
        )
        for requirement in requirements:
            if requirement.kind == RequirementKind.CONSTRAINT:
                continue
            text = " ".join(
                (requirement.description, *requirement.acceptance_criteria)
            ).casefold()
            if (
                ".dockerignore" in text
                and re.search(r"\b(?:repository|structure|ignore rules|rules)\b", text)
            ):
                for path in known_ignore_paths:
                    required.setdefault(path, set()).add(requirement.requirement_id)

        known_paths = list(required)
        for requirement in requirements:
            if requirement.kind == RequirementKind.CONSTRAINT:
                continue
            for basename in cls._explicit_requirement_basenames(requirement):
                candidates = [
                    path for path in known_paths
                    if PurePosixPath(path).name.casefold() == basename
                ]
                if len(candidates) == 1:
                    required.setdefault(candidates[0], set()).add(
                        requirement.requirement_id
                    )

        compose_paths = sorted(
            path for path in required
            if PurePosixPath(path).name.casefold() in {
                "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"
            }
        )
        if compose_paths:
            for requirement in requirements:
                if requirement.kind == RequirementKind.CONSTRAINT:
                    continue
                text = " ".join(
                    (requirement.description, *requirement.acceptance_criteria)
                ).casefold()
                describes_compose_dependencies = (
                    re.search(r"\b(?:service|services)\b", text)
                    and re.search(r"\b(?:startup|start[- ]up|dependency|dependencies|depends_on)\b", text)
                    and re.search(r"\b(?:health|healthy|health[- ]condition|health[- ]conditions)\b", text)
                )
                describes_postgres_compose_config = (
                    re.search(r"\bpostgres(?:ql)?\b", text)
                    and re.search(
                        r"\b(?:credential|environment|volume|persistent|health|readiness|database)\b",
                        text,
                    )
                )
                if describes_compose_dependencies or describes_postgres_compose_config:
                    for compose_path in compose_paths:
                        required.setdefault(compose_path, set()).add(
                            requirement.requirement_id
                        )
        return required

    @staticmethod
    def _module_index_for_required_path(path, modules):
        """Find an unambiguous architecture module for an explicit required path."""
        if "/" in path:
            prefix = path.split("/", 1)[0].casefold()
            named = [
                index for index, module in enumerate(modules)
                if module.name.strip().casefold() == prefix
            ]
            if len(named) == 1:
                return named[0]
            existing = [
                index for index, module in enumerate(modules)
                if any(
                    item.replace("\\", "/").startswith(prefix + "/")
                    for item in module.file_paths
                )
            ]
            return existing[0] if len(existing) == 1 else None

        # Prefer an explicitly named root/repository module.
        root_named = [
            index for index, module in enumerate(modules)
            if module.name.strip().casefold() in {"root", "repository", "workspace"}
        ]
        if len(root_named) == 1:
            return root_named[0]

        # Otherwise, a unique module already owning root-level files is the
        # deterministic owner for another root-level deliverable.
        root_file_owners = [
            index for index, module in enumerate(modules)
            if any("/" not in item.replace("\\", "/") for item in module.file_paths)
        ]
        return root_file_owners[0] if len(root_file_owners) == 1 else None

    def _parse_architecture(self, data, requirements=None):
        result = [ArchitectureModule(
            module_id=x["module_id"], name=x["name"], responsibility=x["responsibility"],
            depends_on=tuple(x.get("depends_on", ())), file_paths=tuple(x.get("file_paths", ())))
            for x in self._items(data, "architecture")]
        self._unique_ids(result, "module_id")
        known = {x.module_id for x in result}
        for x in result:
            if set(x.depends_on) - known:
                raise ValueError(f"module {x.module_id} references unknown module")
        EngineeringSpecification._assert_acyclic({x.module_id: x.depends_on for x in result}, "module")

        if requirements is not None:
            required_paths = self._required_architecture_paths(requirements)
            repairs = []
            # A service-specific .dockerignore is a mandatory file only when
            # the requirement mapping positively establishes that exact path.
            # Directory names alone are not evidence of a Docker build context.
            grounded_modules = []
            for module in result:
                retained_paths = []
                for raw_path in module.file_paths:
                    path = raw_path.replace("\\", "/")
                    if PurePosixPath(path).name.casefold() == ".dockerignore" and path not in required_paths:
                        repairs.append({
                            "action": "drop_unrequired_dockerignore",
                            "path": path,
                            "module_id": module.module_id,
                            "reason": (
                                "directory mention or conditional 'as needed' wording "
                                "does not make this service-specific ignore file mandatory"
                            ),
                        })
                        continue
                    retained_paths.append(raw_path)
                grounded_modules.append(replace(module, file_paths=tuple(retained_paths)))
            result = grounded_modules
            architecture_paths = {
                path.replace("\\", "/")
                for module in result
                for path in module.file_paths
            }
            missing = []
            for path, requirement_ids in sorted(required_paths.items()):
                if path in architecture_paths:
                    continue
                module_index = self._module_index_for_required_path(path, result)
                if module_index is None:
                    missing.append(
                        f"{', '.join(sorted(requirement_ids))}: {path} "
                        "(no unambiguous owning architecture module)"
                    )
                    continue
                module = result[module_index]
                result[module_index] = replace(
                    module,
                    file_paths=tuple((*module.file_paths, path)),
                )
                architecture_paths.add(path)
                repairs.append({
                    "path": path,
                    "requirement_ids": sorted(requirement_ids),
                    "module_id": module.module_id,
                    "module_name": module.name,
                    "reason": "explicitly required path omitted by model; assigned to its unambiguous owning module",
                })

            if repairs:
                self._write_diagnostic("architecture_path_repairs.json", repairs)
            if missing:
                raise ValueError(
                    "architecture omits explicitly required file path(s): "
                    + ", ".join(missing)
                )
            explicit_test_requirements = [
                item for item in requirements
                if item.kind != RequirementKind.CONSTRAINT
                and re.search(r"\b(?:add|create|write|implement)\b", item.description, re.I)
                and re.search(r"\btests?\b", item.description, re.I)
            ]
            if explicit_test_requirements:
                test_paths = [
                    path.replace("\\", "/")
                    for module in result for path in module.file_paths
                    if (
                        any(part.casefold() in {"test", "tests"} for part in PurePosixPath(path).parts[:-1])
                        and re.search(r"(?:^test_|_test\.)", PurePosixPath(path).name, re.I)
                    )
                ]
                if not test_paths:
                    raise ValueError(
                        "architecture must declare at least one concrete test file path under tests/ "
                        "for explicit test-artifact requirement(s): "
                        + ", ".join(item.requirement_id for item in explicit_test_requirements)
                    )
        return result

    def _parse_contracts(self, data):
        result = []
        for x in self._items(data, "contracts", allow_empty=True):
            result.append(InterfaceContract(
                contract_id=x["contract_id"], owner=x["owner"], name=x["name"], kind=x["kind"],
                inputs=tuple(InterfaceField(**v) for v in x.get("inputs", ())),
                outputs=tuple(InterfaceField(**v) for v in x.get("outputs", ())),
                invariants=tuple(x.get("invariants", ()))))
        self._unique_ids(result, "contract_id")
        # Contract ownership must resolve to a module from the preceding stage.
        known_owners = getattr(self, "_known_module_ids", None)
        if known_owners is not None:
            for contract in result:
                if contract.owner not in known_owners:
                    raise ValueError(
                        f"contract {contract.contract_id!r} references unknown architecture module {contract.owner!r}"
                    )
        return result

    @staticmethod
    def _parse_unit_kind(value, index):
        allowed = tuple(item.value for item in UnitKind)
        if not isinstance(value, str):
            raise ValueError(
                f"implementation_units[{index}].kind must be a string; "
                f"allowed values: {', '.join(allowed)}"
            )

        normalized = value.strip().lower()
        try:
            return UnitKind(normalized)
        except ValueError:
            requirement_kinds = {item.value for item in RequirementKind}
            semantic_hint = ""
            if normalized in requirement_kinds:
                semantic_hint = (
                    f" {normalized!r} is a RequirementKind, not an ImplementationUnit "
                    "kind. Choose the kind of artifact being created instead."
                )
            raise ValueError(
                f"implementation_units[{index}].kind has invalid value {value!r}; "
                f"allowed values: {', '.join(allowed)}.{semantic_hint}"
            ) from None

    @staticmethod
    def _is_manual_instruction(value: str) -> bool:
        """Return True only for natural-language manual inspection instructions."""
        if not isinstance(value, str) or not value.strip():
            return False
        first_word = re.match(r"[A-Za-z]+", value.strip())
        if first_word is None:
            return False
        return first_word.group(0).lower() in {
            "check", "confirm", "compare", "ensure", "examine", "inspect",
            "if", "look", "manually", "open", "read", "review", "verify",
        }

    @staticmethod
    def _next_unit_id(units):
        """Return a deterministic, unused implementation-unit ID."""
        existing = {unit.unit_id for unit in units}
        numeric = [
            int(match.group(1))
            for value in existing
            if (match := re.fullmatch(r"UNIT-(\d+)", value))
        ]
        if numeric or not existing:
            candidate = max(numeric, default=0) + 1
            while f"UNIT-{candidate:03d}" in existing:
                candidate += 1
            return f"UNIT-{candidate:03d}"
        candidate = 1
        while f"UNIT-AUTO-{candidate:03d}" in existing:
            candidate += 1
        return f"UNIT-AUTO-{candidate:03d}"

    @staticmethod
    def _unit_kind_for_required_path(path):
        """Choose a conservative artifact kind from an explicitly required path."""
        name = PurePosixPath(path).name.lower()
        if name in {"readme.md", "changelog.md", "contributing.md"}:
            return UnitKind.DOCUMENTATION
        if name.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".sql")):
            return UnitKind.SOURCE
        if name.endswith((".png", ".jpg", ".jpeg", ".svg", ".webp", ".ico")):
            return UnitKind.ASSET
        return UnitKind.CONFIGURATION

    @classmethod
    def _normalize_requirement_file_mappings(
        cls, units, requirements, architecture_paths, contracts=()
    ):
        """Move file-specific obligations and their contracts onto the required artifact.

        A model may associate a requirement with a nearby but semantically unrelated
        architecture path (for example, PostgreSQL runtime configuration with
        postgres/.dockerignore). When the requirement-to-file mapping is explicit
        or deterministically inferred from another requirement in this task, the
        required artifact is authoritative.
        """
        required_paths = cls._required_architecture_paths(requirements)
        paths_by_requirement = {}
        for path, requirement_ids in required_paths.items():
            for requirement_id in requirement_ids:
                paths_by_requirement.setdefault(requirement_id, set()).add(
                    path.replace("\\", "/")
                )

        result = list(units)
        repairs = []
        directory_only_ids = {
            item.requirement_id for item in requirements
            if cls._is_directory_only_requirement(item)
        }
        for index, unit in enumerate(result):
            misplaced = directory_only_ids.intersection(unit.satisfies)
            if misplaced:
                result[index] = replace(
                    unit,
                    satisfies=tuple(
                        requirement_id for requirement_id in unit.satisfies
                        if requirement_id not in directory_only_ids
                    ),
                )
                for requirement_id in sorted(misplaced):
                    repairs.append({
                        "action": "remove_directory_requirement_from_file_unit",
                        "requirement_id": requirement_id,
                        "unit_id": unit.unit_id,
                        "target_path": unit.target_path,
                        "reason": (
                            "a file beneath a directory is not evidence that the "
                            "directory-existence requirement is implemented"
                        ),
                    })
        contract_by_id = {item.contract_id: item for item in contracts}
        requirement_by_id = {
            item.requirement_id: item for item in requirements
        }

        # First remove requirement claims from unrelated files and attach them
        # to the required file units. Multiple paths remain valid for requirements
        # that genuinely require several artifacts (e.g. service .dockerignore files).
        for requirement_id, required in sorted(paths_by_requirement.items()):
            if not required:
                continue
            requirement = requirement_by_id.get(requirement_id)
            if requirement is None or requirement.kind == RequirementKind.CONSTRAINT:
                continue
            for index, unit in enumerate(result):
                target = unit.target_path.replace("\\", "/")
                if requirement_id in unit.satisfies and target not in required:
                    result[index] = replace(
                        unit,
                        satisfies=tuple(
                            item for item in unit.satisfies
                            if item != requirement_id
                        ),
                    )
                    repairs.append({
                        "action": "remove_misplaced_requirement_mapping",
                        "requirement_id": requirement_id,
                        "unit_id": unit.unit_id,
                        "wrong_target_path": target,
                        "required_target_paths": sorted(required),
                    })
            for path in sorted(required & set(architecture_paths)):
                matching = [
                    index for index, unit in enumerate(result)
                    if unit.target_path.replace("\\", "/") == path
                ]
                if not matching:
                    continue
                index = matching[0]
                unit = result[index]
                new_satisfies = tuple(dict.fromkeys(
                    (*unit.satisfies, requirement_id)
                ))
                new_acceptance = tuple(dict.fromkeys(
                    (*unit.acceptance_criteria, *requirement.acceptance_criteria)
                ))
                if (
                    new_satisfies != unit.satisfies
                    or new_acceptance != unit.acceptance_criteria
                ):
                    result[index] = replace(
                        unit,
                        satisfies=new_satisfies,
                        acceptance_criteria=new_acceptance,
                    )
                    repairs.append({
                        "action": "attach_requirement_to_required_file",
                        "requirement_id": requirement_id,
                        "unit_id": unit.unit_id,
                        "target_path": path,
                    })

        # Transfer a contract when its invariants clearly describe one requirement
        # that is now anchored to a unique required file. Ambiguous contracts are
        # left untouched for human review rather than guessed.
        for contract_id, contract in contract_by_id.items():
            contract_text = " ".join(
                (contract.name, *contract.invariants)
            )
            contract_tokens = cls._coverage_tokens(contract_text)
            scored = []
            for requirement_id, paths in paths_by_requirement.items():
                if len(paths) != 1:
                    continue
                requirement = requirement_by_id.get(requirement_id)
                if requirement is None:
                    continue
                requirement_text = " ".join(
                    (requirement.description, *requirement.acceptance_criteria)
                )
                score = len(
                    contract_tokens & cls._coverage_tokens(requirement_text)
                )
                if score >= 2:
                    scored.append((score, requirement_id, next(iter(paths))))
            if not scored:
                continue
            best_score = max(item[0] for item in scored)
            best = [item for item in scored if item[0] == best_score]
            if len(best) != 1:
                continue
            _, requirement_id, target_path = best[0]
            if target_path not in set(architecture_paths):
                continue
            target_indices = [
                index for index, unit in enumerate(result)
                if unit.target_path.replace("\\", "/") == target_path
                and requirement_id in unit.satisfies
            ]
            if not target_indices:
                continue
            for index, unit in enumerate(result):
                if contract_id in unit.implements and unit.target_path.replace("\\", "/") != target_path:
                    result[index] = replace(
                        unit,
                        implements=tuple(
                            item for item in unit.implements
                            if item != contract_id
                        ),
                    )
                    repairs.append({
                        "action": "remove_misplaced_contract_mapping",
                        "contract_id": contract_id,
                        "unit_id": unit.unit_id,
                        "wrong_target_path": unit.target_path,
                        "required_target_path": target_path,
                    })
            index = target_indices[0]
            unit = result[index]
            if contract_id not in unit.implements:
                result[index] = replace(
                    unit,
                    implements=tuple((*unit.implements, contract_id)),
                )
                repairs.append({
                    "action": "attach_contract_to_required_file",
                    "contract_id": contract_id,
                    "unit_id": unit.unit_id,
                    "target_path": target_path,
                    "requirement_id": requirement_id,
                })

        return result, repairs

    @classmethod
    def _complete_required_file_units(cls, units, requirements, architecture_paths):
        """Repair requirement coverage using explicit file and directory evidence.

        Existing units for a shared file are augmented rather than duplicated.
        File requirements are mapped to concrete architecture paths. Directory
        existence remains a separate structural obligation and is implemented
        with explicit mkdir operations during planning.
        """
        result = list(units)
        repairs = []
        requirement_by_id = {item.requirement_id: item for item in requirements}
        required_paths = cls._required_architecture_paths(requirements)
        normalized_architecture_paths = {
            path.replace("\\\\", "/") for path in architecture_paths
        }

        for path, requirement_ids in sorted(required_paths.items()):
            normalized_path = path.replace("\\\\", "/")
            if normalized_path not in normalized_architecture_paths:
                continue
            owners = [
                requirement_id for requirement_id in sorted(requirement_ids)
                if requirement_by_id[requirement_id].kind != RequirementKind.CONSTRAINT
            ]
            for requirement_id in owners:
                exact_units = [
                    index for index, unit in enumerate(result)
                    if unit.target_path.replace("\\\\", "/") == normalized_path
                ]
                if exact_units:
                    index = exact_units[0]
                    previous = result[index]
                    already_covered = requirement_id in previous.satisfies
                    if already_covered:
                        continue

                    if len(owners) == 1:
                        new_satisfies = (requirement_id,)
                    else:
                        new_satisfies = tuple(dict.fromkeys(
                            (*previous.satisfies, requirement_id)
                        ))
                    requirement = requirement_by_id[requirement_id]
                    new_acceptance = tuple(dict.fromkeys(
                        (*previous.acceptance_criteria, *requirement.acceptance_criteria)
                    ))
                    result[index] = replace(
                        previous,
                        satisfies=new_satisfies,
                        acceptance_criteria=new_acceptance,
                    )
                    repairs.append({
                        "action": (
                            "remap_unit_requirement" if len(owners) == 1
                            else "augment_shared_file_unit"
                        ),
                        "unit_id": previous.unit_id,
                        "target_path": normalized_path,
                        "previous_satisfies": list(previous.satisfies),
                        "satisfies": list(new_satisfies),
                        "added_acceptance_criteria": [
                            item for item in requirement.acceptance_criteria
                            if item not in previous.acceptance_criteria
                        ],
                        "reason": (
                            "the requirement explicitly targets this existing artifact; "
                            "the unit was extended instead of creating a conflicting second writer"
                        ),
                    })
                    continue

                requirement = requirement_by_id[requirement_id]
                basename = PurePosixPath(normalized_path).name.casefold()
                templates = [
                    unit for unit in result
                    if PurePosixPath(unit.target_path.replace("\\\\", "/")).name.casefold() == basename
                    and requirement_id in unit.satisfies
                ]
                template = templates[0] if templates else None
                unit_id = cls._next_unit_id(result)
                if template is not None:
                    kind = template.kind
                    implements = template.implements
                    depends_on = template.depends_on
                else:
                    kind = cls._unit_kind_for_required_path(normalized_path)
                    implements = ()
                    depends_on = ()
                acceptance = tuple(requirement.acceptance_criteria) or (
                    f"Create and maintain the required artifact at {normalized_path}.",
                )
                created = ImplementationUnit(
                    unit_id=unit_id,
                    title=f"Create {normalized_path}",
                    target_path=normalized_path,
                    kind=kind,
                    depends_on=depends_on,
                    satisfies=(requirement_id,),
                    implements=implements,
                    acceptance_criteria=acceptance,
                )
                result.append(created)
                repairs.append({
                    "action": "create_required_file_unit",
                    "unit_id": unit_id,
                    "target_path": normalized_path,
                    "satisfies": [requirement_id],
                    "template_unit_id": template.unit_id if template is not None else None,
                    "reason": "the requirement explicitly requires this architecture file but no dedicated unit covered it",
                })

        # Directory-only obligations are handled as structural requirements,
        # not by attaching their IDs to incidental child file units.
        return result, repairs

    @staticmethod
    def _coverage_tokens(value):
        tokens = set(re.findall(r"[a-z0-9]+", str(value).lower()))
        aliases = {
            "configuration": "config",
            "configure": "config",
            "configured": "config",
            "configuring": "config",
            "compose": "compose",
            "dockercompose": "compose",
            "postgresql": "postgresql",
        }
        tokens = {aliases.get(token, token) for token in tokens}
        stop = {
            "a", "an", "and", "are", "as", "at", "be", "by", "check",
            "checks", "criterion", "ensure", "for", "from", "if", "in",
            "into", "is", "it", "its", "must", "of", "on", "or", "that",
            "the", "their", "this", "to", "use", "using", "verify",
            "verification", "with", "without", "whether", "requirement",
            "requirements", "all", "only", "should", "can", "root",
            "directory", "directories", "folder", "folders", "workspace",
            "file", "files", "exist", "exists", "present", "presence",
            "area", "areas", "layout", "structure",
        }
        return {token for token in tokens if len(token) > 2 and token not in stop}

    @classmethod
    def _criterion_supports_requirement(cls, criterion, requirement):
        """Reject obvious false-positive REQ references in model verification output."""
        criterion_tokens = cls._coverage_tokens(criterion.description)
        requirement_text = " ".join(
            (requirement.description, *requirement.acceptance_criteria)
        )
        requirement_tokens = cls._coverage_tokens(requirement_text)
        return bool(criterion_tokens & requirement_tokens)

    @staticmethod
    def _acceptance_criterion_targets_unit(description, unit):
        """Recognize an explicit file-and-directory target in criterion text."""
        text = " ".join(str(description).casefold().replace("\\", "/").split())
        target = unit.target_path.replace("\\", "/").casefold()
        basename = PurePosixPath(target).name
        if basename not in text:
            return False
        parent = PurePosixPath(target).parent.as_posix()
        if parent in {"", "."}:
            return bool(re.search(r"\b(?:root|repository root|workspace root)\b", text))
        return parent in text or parent.rstrip("/") + "/" in text

    @classmethod
    def _normalize_acceptance_criterion_implementation_mapping(cls, units, requirements):
        """Bind each acceptance criterion to explicit implementation units.

        A single-unit requirement has an unambiguous legacy mapping. When a
        requirement spans multiple units, the model must explicitly identify
        which unit(s) implement each criterion; the generator does not infer
        that mapping from a shared parent requirement or copied description.
        """
        result = list(units)
        repairs = []
        for requirement in requirements:
            if (
                not requirement.acceptance_criterion_ids
                or requirement.kind == RequirementKind.CONSTRAINT
                or cls._is_directory_only_requirement(requirement)
            ):
                continue
            indices = [
                index for index, unit in enumerate(result)
                if requirement.requirement_id in unit.satisfies
            ]
            if not indices:
                continue
            for criterion_id, description in zip(
                requirement.acceptance_criterion_ids,
                requirement.acceptance_criteria,
            ):
                already_mapped = [
                    index for index in indices
                    if criterion_id in result[index].implements_acceptance_criteria
                ]
                if already_mapped:
                    continue
                target_matches = [
                    index for index in indices
                    if cls._acceptance_criterion_targets_unit(description, result[index])
                ]
                required_paths = {
                    path for path, requirement_ids in
                    cls._required_architecture_paths(requirements).items()
                    if requirement.requirement_id in requirement_ids
                }
                all_units_are_required_files = bool(required_paths) and all(
                    result[index].target_path.replace("\\", "/") in required_paths
                    for index in indices
                )
                candidates = target_matches or (
                    indices
                    if len(indices) == 1 or all_units_are_required_files
                    else []
                )
                if not candidates:
                    raise ValueError(
                        f"acceptance criterion {criterion_id!r} for {requirement.requirement_id} "
                        "must be explicitly mapped to implementation unit(s); "
                        f"multiple candidate units exist: {[result[index].unit_id for index in indices]}"
                    )
                for index in candidates:
                    unit = result[index]
                    result[index] = replace(
                        unit,
                        implements_acceptance_criteria=tuple(dict.fromkeys((
                            *unit.implements_acceptance_criteria, criterion_id
                        ))),
                    )
                    repairs.append({
                        "action": "map_acceptance_criterion_to_implementation_unit",
                        "criterion_id": criterion_id,
                        "unit_id": unit.unit_id,
                        "requirement_id": requirement.requirement_id,
                        "reason": (
                            "acceptance criterion names the implementation path"
                            if target_matches
                            else "the criterion spans all explicitly required file units"
                            if all_units_are_required_files
                            else "the requirement has exactly one implementation unit"
                        ),
                    })
        return result, repairs

    @classmethod
    def _complete_verification_coverage(cls, verification, units, requirements):
        """Keep grounded coverage and add deterministic checks for uncovered obligations."""
        requirement_by_id = {item.requirement_id: item for item in requirements}
        requirement_ids = set(requirement_by_id)
        unit_ids = {unit.unit_id for unit in units}
        result = []
        repairs = []

        for criterion in verification:
            kept_refs = []
            removed_refs = []
            for reference in criterion.covers:
                if reference in requirement_ids:
                    referenced_requirement = requirement_by_id[reference]
                    if cls._is_directory_only_requirement(referenced_requirement):
                        supported = (
                            criterion.kind == VerificationKind.STRUCTURAL
                            and cls._criterion_supports_requirement(
                                criterion, referenced_requirement
                            )
                        )
                    else:
                        supported = cls._criterion_supports_requirement(
                            criterion, referenced_requirement
                        )
                    if supported:
                        kept_refs.append(reference)
                    else:
                        removed_refs.append(reference)
                elif reference in unit_ids:
                    kept_refs.append(reference)
                else:
                    # Preserve unknown references so the authoritative validator
                    # still rejects them instead of silently hiding the defect.
                    kept_refs.append(reference)
            if removed_refs:
                repairs.append({
                    "action": "remove_unrelated_verification_coverage",
                    "criterion_id": criterion.criterion_id,
                    "removed_requirement_ids": removed_refs,
                    "reason": "criterion description has no meaningful term overlap with the referenced requirement",
                })
            # Keep unknown acceptance-criterion references visible so the
            # authoritative cross-reference validator rejects them.
            result.append(replace(
                criterion,
                covers=tuple(dict.fromkeys(kept_refs)),
                covers_acceptance_criteria=tuple(dict.fromkeys(
                    criterion.covers_acceptance_criteria
                )),
            ))

        covered = {
            reference for criterion in result for reference in criterion.covers
            if reference in requirement_ids
        }
        covered_unit_ids = {
            reference for criterion in result for reference in criterion.covers
            if reference in unit_ids
        }
        covered.update(
            requirement_id
            for unit in units if unit.unit_id in covered_unit_ids
            for requirement_id in unit.satisfies
        )

        next_number = 1
        existing_ids = {criterion.criterion_id for criterion in result}
        for requirement in requirements:
            if requirement.requirement_id in covered and not (
                cls._is_directory_only_requirement(requirement)
                and not any(
                    requirement.requirement_id in criterion.covers
                    and criterion.kind == VerificationKind.STRUCTURAL
                    for criterion in result
                )
            ):
                continue
            while f"VER-AUTO-{next_number:03d}" in existing_ids:
                next_number += 1
            criterion_id = f"VER-AUTO-{next_number:03d}"
            next_number += 1
            existing_ids.add(criterion_id)
            checks = tuple(requirement.acceptance_criteria) or (
                requirement.description,
            )
            if cls._is_directory_only_requirement(requirement):
                directories = sorted(cls._explicit_requirement_directories(requirement))
                command_code = (
                    "from pathlib import Path; import sys; "
                    f"paths={directories!r}; "
                    "missing=[p for p in paths if not Path(p).is_dir()]; "
                    "print('Missing directories:', ', '.join(missing) if missing else 'none'); "
                    "sys.exit(1 if missing else 0)"
                )
                description = (
                    f"Verify that all required directories exist: "
                    + ", ".join(f"{directory}/" for directory in directories)
                )
                kind = VerificationKind.STRUCTURAL
                command = ("python", "-c", command_code)
                reason = "created a deterministic structural check for directory existence"
            else:
                description = (
                    f"Manually verify {requirement.requirement_id}: {requirement.description}\n"
                    "Acceptance checks:\n"
                    + "\n".join(f"- {check}" for check in checks)
                )
                kind = VerificationKind.MANUAL
                command = ()
                reason = "requirement lacked a semantically grounded verification criterion; created a manual checklist from its acceptance criteria"
            result.append(VerificationCriterion(
                criterion_id=criterion_id,
                description=description,
                kind=kind,
                required=True,
                command=command,
                covers=(requirement.requirement_id,),
            ))
            result[-1] = replace(
                result[-1],
                covers_acceptance_criteria=(
                    requirement.acceptance_criterion_ids
                    if not cls._is_directory_only_requirement(requirement)
                    else ()
                ),
            )
            repairs.append({
                "action": "create_requirement_verification",
                "criterion_id": criterion_id,
                "covers": [requirement.requirement_id],
                "reason": reason,
            })

        for requirement in requirements:
            if not cls._is_directory_only_requirement(requirement):
                continue
            structural_indices = [
                index for index, criterion in enumerate(result)
                if criterion.required
                and criterion.kind == VerificationKind.STRUCTURAL
                and requirement.requirement_id in criterion.covers
            ]
            if structural_indices:
                index = structural_indices[0]
                criterion = result[index]
                result[index] = replace(
                    criterion,
                    covers_acceptance_criteria=tuple(dict.fromkeys((
                        *criterion.covers_acceptance_criteria,
                        *requirement.acceptance_criterion_ids,
                    ))),
                )

        verified_acceptance_ids = {
            reference for criterion in result
            if criterion.required
            for reference in criterion.covers_acceptance_criteria
        }
        for requirement in requirements:
            for criterion_id, description in zip(
                requirement.acceptance_criterion_ids,
                requirement.acceptance_criteria,
            ):
                if criterion_id in verified_acceptance_ids:
                    continue
                while f"VER-AC-{next_number:03d}" in existing_ids:
                    next_number += 1
                verification_id = f"VER-AC-{next_number:03d}"
                next_number += 1
                existing_ids.add(verification_id)
                result.append(VerificationCriterion(
                    criterion_id=verification_id,
                    description=(
                        f"Manually verify acceptance criterion {criterion_id}: {description}"
                    ),
                    kind=VerificationKind.MANUAL,
                    required=True,
                    command=(),
                    covers=(requirement.requirement_id,),
                    covers_acceptance_criteria=(criterion_id,),
                ))
                repairs.append({
                    "action": "create_acceptance_criterion_verification",
                    "criterion_id": verification_id,
                    "covers_acceptance_criteria": [criterion_id],
                    "reason": "acceptance criterion lacked explicit verification coverage",
                })
        return result, repairs

    @classmethod
    def _semantic_requirement_ids_for_unit(cls, unit, requirements):
        """Find the most semantically relevant requirements for a non-file activity."""
        unit_text = " ".join((unit.title, *unit.acceptance_criteria))
        unit_tokens = cls._coverage_tokens(unit_text)
        scores = {}
        for requirement in requirements:
            requirement_text = " ".join(
                (requirement.description, *requirement.acceptance_criteria)
            )
            score = len(unit_tokens & cls._coverage_tokens(requirement_text))
            if score:
                scores[requirement.requirement_id] = score
        if not scores:
            return tuple(unit.satisfies)
        best = max(scores.values())
        best_ids = tuple(
            requirement_id for requirement_id, score in scores.items()
            if score == best
        )
        # Generic words such as "create", "artifact", or "file" can tie
        # unrelated requirements. Only override the model's mapping when the
        # semantic match is both strong and unambiguous.
        if best < 2 or len(best_ids) != 1:
            return tuple(unit.satisfies)
        return best_ids

    @staticmethod
    def _is_verification_only_unit(unit):
        """Recognize units whose title describes checking, not changing, an artifact."""
        if unit.kind == UnitKind.TEST:
            return False
        return bool(re.match(
            r"^\s*(?:verify|validate|check|review|report)\b",
            unit.title,
            re.IGNORECASE,
        ))

    @staticmethod
    def _filter_manual_implementation_instructions(verification):
        """Discard manual criteria that tell a person to implement the deliverable.

        The requirements' acceptance criteria are converted into proper manual
        verification checklists later in the pipeline. Human review criteria must
        inspect completed work, not ask the reviewer to create the files.
        """
        retained = []
        repairs = []
        for criterion in verification:
            description = criterion.description
            is_implementation_instruction = (
                criterion.kind == VerificationKind.MANUAL
                and (
                    "instructions:" in description.casefold()
                    or re.search(
                        r"\bmanually\s+(?:create|add|configure|write|update|implement)\b",
                        description,
                        re.IGNORECASE,
                    )
                )
            )
            if is_implementation_instruction:
                repairs.append({
                    "action": "replace_manual_implementation_instructions",
                    "criterion_id": criterion.criterion_id,
                    "covers": list(criterion.covers),
                    "reason": (
                        "manual verification must inspect completed artifacts; "
                        "requirement acceptance criteria will be emitted as verification checklists"
                    ),
                })
                continue
            retained.append(criterion)
        return retained, repairs

    @classmethod
    def _normalize_implementation_units(
        cls, units, verification, requirements, contracts=()
    ):
        """Convert integration activities into one file-change unit per target path.

        The planner's output schema permits one change per path. Activities that
        inspect the workspace, verify an artifact, or enforce a global constraint
        are represented by verification criteria, not artificial file units.
        """
        requirement_by_id = {
            item.requirement_id: item for item in requirements
        }
        contract_by_id = {item.contract_id: item for item in contracts}
        root_units = [
            unit for unit in units
            if unit.target_path.replace("\\", "/").strip().rstrip("/") in {"", "."}
        ]
        verification_units = [
            unit for unit in units
            if unit not in root_units and cls._is_verification_only_unit(unit)
        ]
        removed_units = root_units + verification_units
        removed_ids = {unit.unit_id for unit in removed_units}
        retained = [
            unit for unit in units if unit.unit_id not in removed_ids
        ]
        repairs = []

        # A root-scoped unit cannot be represented by a FileChange. It may be
        # removed only when every implementation requirement it claims is
        # independently represented by concrete file units.
        for unit in root_units:
            semantic_ids = cls._semantic_requirement_ids_for_unit(
                unit, requirements
            )
            required_ids = set(unit.satisfies) | set(semantic_ids)
            uncovered = [
                requirement_id for requirement_id in sorted(required_ids)
                if requirement_id in requirement_by_id
                and requirement_by_id[requirement_id].kind != RequirementKind.CONSTRAINT
                and not cls._is_directory_only_requirement(requirement_by_id[requirement_id])
                and not any(
                    requirement_id in candidate.satisfies
                    for candidate in retained
                )
            ]
            if uncovered:
                raise ValueError(
                    f"root-scoped implementation unit {unit.unit_id!r} targets "
                    f"{unit.target_path!r}, which cannot be represented as a file change; "
                    f"no concrete implementation units cover {uncovered}. Add explicit "
                    "architecture file paths and concrete units beneath the required directories."
                )
            missing_contracts = [
                contract_id for contract_id in unit.implements
                if contract_id not in contract_by_id
            ]
            if missing_contracts:
                raise ValueError(
                    f"root-scoped unit {unit.unit_id!r} references unknown contracts: "
                    f"{missing_contracts}"
                )
            # Transfer only artifact-creation contracts to the concrete units
            # that now implement the root-layout requirement. Inspection is a
            # planning precondition, not an application interface.
            transferable = []
            for contract_id in unit.implements:
                contract = contract_by_id[contract_id]
                if re.match(
                    r"^\s*(?:create|build|initialize|establish|add|configure|set[\s_-]*up)",
                    contract.name,
                    re.IGNORECASE,
                ):
                    transferable.append(contract_id)
            replacement_indices = [
                index for index, candidate in enumerate(retained)
                if set(candidate.satisfies) & required_ids
            ]
            for index in replacement_indices:
                candidate = retained[index]
                retained[index] = replace(
                    candidate,
                    implements=tuple(dict.fromkeys(
                        (*candidate.implements, *transferable)
                    )),
                )
            repairs.append({
                "action": "remove_non_file_scoped_root_unit",
                "unit_id": unit.unit_id,
                "target_path": unit.target_path,
                "semantic_requirement_ids": list(semantic_ids),
                "transferred_contracts": transferable,
                "reason": (
                    "workspace-wide inspection/layout work cannot be represented by a "
                    "single file change; concrete child-file units cover its implementation "
                    "requirements, while inspection remains a verification/precondition"
                ),
            })

        # Verification-only units must not compete with implementation units.
        for unit in verification_units:
            semantic_ids = cls._semantic_requirement_ids_for_unit(
                unit, requirements
            )
            required_ids = set(unit.satisfies) | set(semantic_ids)
            uncovered = [
                requirement_id for requirement_id in sorted(required_ids)
                if requirement_id in requirement_by_id
                and requirement_by_id[requirement_id].kind != RequirementKind.CONSTRAINT
                and not cls._is_directory_only_requirement(requirement_by_id[requirement_id])
                and not any(
                    requirement_id in candidate.satisfies
                    for candidate in retained
                )
            ]
            if uncovered:
                raise ValueError(
                    f"verification-only unit {unit.unit_id!r} cannot be removed because "
                    f"functional/non-functional requirements {uncovered} have no concrete "
                    "implementation unit"
                )
            repairs.append({
                "action": "move_verification_only_unit_to_verification",
                "unit_id": unit.unit_id,
                "target_path": unit.target_path,
                "semantic_requirement_ids": list(semantic_ids),
                "reason": (
                    "verification activities belong in verification criteria, not "
                    "file-change implementation units"
                ),
            })

        # Keep the removed activities' checks visible in the verification plan.
        next_verification_number = 1
        existing_verification_ids = {
            item.criterion_id for item in verification
        }
        normalized_verification = list(verification)
        for unit in removed_units:
            semantic_ids = tuple(
                item for item in cls._semantic_requirement_ids_for_unit(
                    unit, requirements
                )
                if item in requirement_by_id
            )
            if not semantic_ids:
                semantic_ids = tuple(
                    item for item in unit.satisfies if item in requirement_by_id
                )
            if not semantic_ids:
                continue
            description = (
                f"Manual verification/precondition for {unit.unit_id} — {unit.title}.\n"
                "Checks:\n"
                + "\n".join(
                    f"- {criterion}" for criterion in unit.acceptance_criteria
                )
            )
            # Augment an existing criterion when it already verifies the same
            # requirement; otherwise create a dedicated manual criterion.
            matching = next((
                index for index, criterion in enumerate(normalized_verification)
                if set(criterion.covers) & set(semantic_ids)
            ), None)
            if matching is not None:
                criterion = normalized_verification[matching]
                if description not in criterion.description:
                    normalized_verification[matching] = replace(
                        criterion,
                        description=criterion.description.rstrip() + "\n\n" + description,
                        covers=tuple(dict.fromkeys(
                            (*criterion.covers, *semantic_ids)
                        )),
                    )
                continue
            while True:
                criterion_id = f"VER-UNIT-{next_verification_number:03d}"
                next_verification_number += 1
                if criterion_id not in existing_verification_ids:
                    break
            existing_verification_ids.add(criterion_id)
            normalized_verification.append(VerificationCriterion(
                criterion_id=criterion_id,
                description=description,
                kind=VerificationKind.MANUAL,
                required=True,
                command=(),
                covers=semantic_ids,
            ))

        # Drop dependencies on removed workspace-preflight/verification units.
        # Their checks now live in the verification plan; they are not file
        # changes that other file changes can depend on.
        for index, unit in enumerate(retained):
            removed_dependencies = set(unit.depends_on) & removed_ids
            if removed_dependencies:
                retained[index] = replace(
                    unit,
                    depends_on=tuple(
                        dependency for dependency in unit.depends_on
                        if dependency not in removed_ids
                    ),
                )
                repairs.append({
                    "action": "remove_non_file_dependency",
                    "unit_id": unit.unit_id,
                    "removed_dependencies": sorted(removed_dependencies),
                    "reason": "dependency referred to a non-file precondition or verification activity",
                })

        # Merge all activities that write the same file. The plan format permits
        # one FileChange per path, so these activities must become one unit.
        groups = {}
        for unit in retained:
            key = unit.target_path.replace("\\", "/").rstrip("/").casefold()
            groups.setdefault(key, []).append(unit)

        canonical_id_by_old_id = {}
        for group in groups.values():
            canonical_id = group[0].unit_id
            for unit in group:
                canonical_id_by_old_id[unit.unit_id] = canonical_id

        merged_units = []
        for group in groups.values():
            first = group[0]
            kinds = {unit.kind for unit in group}
            if len(kinds) != 1:
                raise ValueError(
                    f"units targeting the same file {first.target_path!r} disagree "
                    f"on artifact kind: {sorted(item.value for item in kinds)}"
                )
            group_ids = {unit.unit_id for unit in group}
            dependencies = []
            for unit in group:
                for dependency in unit.depends_on:
                    if dependency in removed_ids:
                        continue
                    mapped = canonical_id_by_old_id.get(dependency, dependency)
                    if mapped not in group_ids and mapped != first.unit_id:
                        dependencies.append(mapped)
            merged = replace(
                first,
                title=(
                    first.title if len(group) == 1
                    else f"Implement {first.target_path} (combined {len(group)} activities)"
                ),
                depends_on=tuple(dict.fromkeys(dependencies)),
                satisfies=tuple(dict.fromkeys(
                    item for unit in group for item in unit.satisfies
                )),
                implements=tuple(dict.fromkeys(
                    item for unit in group for item in unit.implements
                )),
                acceptance_criteria=tuple(dict.fromkeys(
                    item for unit in group for item in unit.acceptance_criteria
                )),
            )
            merged_units.append(merged)
            if len(group) > 1:
                repairs.append({
                    "action": "merge_same_target_path_units",
                    "target_path": first.target_path,
                    "canonical_unit_id": first.unit_id,
                    "merged_unit_ids": [unit.unit_id for unit in group],
                    "satisfies": list(merged.satisfies),
                    "implements": list(merged.implements),
                    "reason": (
                        "the plan schema permits one file change per path; all activities "
                        "for this file are combined into one implementation unit"
                    ),
                })

        # Remap verification references from merged/removed units.
        removed_semantic_refs = {
            unit.unit_id: tuple(
                item for item in cls._semantic_requirement_ids_for_unit(
                    unit, requirements
                )
                if item in requirement_by_id
            ) or tuple(
                item for item in unit.satisfies if item in requirement_by_id
            )
            for unit in removed_units
        }
        remapped_verification = []
        for criterion in normalized_verification:
            covers = []
            for reference in criterion.covers:
                if reference in removed_semantic_refs:
                    covers.extend(removed_semantic_refs[reference])
                elif reference in canonical_id_by_old_id:
                    covers.append(canonical_id_by_old_id[reference])
                else:
                    covers.append(reference)
            remapped_verification.append(replace(
                criterion,
                covers=tuple(dict.fromkeys(covers)),
            ))

        return merged_units, remapped_verification, repairs

    def _parse_integration(self, data, requirements=None, architecture=None, contracts=None):
        units = []
        architecture_paths = {
            path.replace("\\", "/")
            for module in (architecture or ())
            for path in module.file_paths
        }
        raw_units = data.get("implementation_units")
        if not isinstance(raw_units, list):
            raise ValueError("implementation_units must be an array")
        if not all(isinstance(item, Mapping) for item in raw_units):
            raise ValueError("every implementation_units item must be an object")
        if not raw_units and any(
            requirement.kind != RequirementKind.CONSTRAINT
            and not self._is_directory_only_requirement(requirement)
            for requirement in (requirements or ())
        ):
            raise ValueError(
                "implementation_units must be a non-empty array when functional or non-functional requirements exist"
            )
        for index, item in enumerate(raw_units):
            unit_id = item.get("unit_id", f"implementation_units[{index}]")
            target_path = item.get("target_path")
            if not isinstance(target_path, str) or not target_path.strip():
                raise ValueError(
                    f"implementation_units[{index}] ({unit_id}).target_path must be a non-empty string; "
                    "choose an exact ARCHITECTURE.file_paths entry, or a non-empty parent directory "
                    "only for genuinely directory-scoped work"
                )
            normalized_path = target_path.replace("\\", "/").strip()
            is_architecture_file = normalized_path in architecture_paths
            is_architecture_parent = normalized_path in (".", "") or any(
                path.startswith(normalized_path.rstrip("/") + "/")
                for path in architecture_paths
            )
            if architecture_paths and not (is_architecture_file or is_architecture_parent):
                raise ValueError(
                    f"implementation_units[{index}] ({unit_id}).target_path {target_path!r} "
                    "is not an architecture file path or its parent directory"
                )
            units.append(ImplementationUnit(
                unit_id=item["unit_id"],
                title=item["title"],
                target_path=target_path,
                kind=self._parse_unit_kind(item.get("kind", "source"), index),
                depends_on=tuple(item.get("depends_on", ())),
                satisfies=tuple(item.get("satisfies", ())),
                implements=tuple(item.get("implements", ())),
                acceptance_criteria=tuple(item.get("acceptance_criteria", ())),
                implements_acceptance_criteria=tuple(item.get("implements_acceptance_criteria", ())),
            ))
        verification = []
        for index, item in enumerate(self._items(data, "verification"), start=1):
            criterion_id = (
                item.get("criterion_id")
                or item.get("verification_id")
                or item.get("id")
                or f"VER-{index:03d}"
            )
            missing_fields = [
                field for field in ("description", "kind")
                if field not in item or item[field] in (None, "")
            ]
            if missing_fields:
                raise ValueError(
                    f"verification[{index - 1}] is missing required field(s): "
                    f"{', '.join(missing_fields)}"
                )
            verification_kind = self._parse_verification_kind(item["kind"])
            verification_command = tuple(item.get("command", ()))
            verification_description = item["description"]
            if verification_kind == VerificationKind.MANUAL and verification_command:
                # Models sometimes place human-readable inspection steps in the
                # command field. Preserve those steps as description text, but
                # never reinterpret executable-looking commands as manual checks.
                manual_steps = tuple(str(part).strip() for part in verification_command)
                if not all(self._is_manual_instruction(part) for part in manual_steps):
                    raise ValueError(
                        f"verification[{index - 1}] ({criterion_id}) is manual but its "
                        f"command contains executable-looking content: {manual_steps!r}; "
                        "set command to [] "
                        "and put human instructions in description, or use an automated "
                        "verification kind for executable commands"
                    )
                verification_description = (
                    str(verification_description).rstrip()
                    + "\nManual verification steps:\n"
                    + "\n".join(f"- {part}" for part in manual_steps)
                )
                verification_command = ()
            verification.append(VerificationCriterion(
                criterion_id=str(criterion_id), description=verification_description,
                kind=verification_kind,
                required=item.get("required", True),
                command=verification_command,
                covers=tuple(item.get("covers", ())),
                covers_acceptance_criteria=tuple(item.get("covers_acceptance_criteria", ())),
            ))
        verification, manual_instruction_repairs = (
            self._filter_manual_implementation_instructions(verification)
        )
        completion_repairs = {
            "implementation_units": [],
            "verification": [],
        }
        if requirements is not None:
            units, required_path_repairs = self._complete_required_file_units(
                units, requirements, architecture_paths
            )
            completion_repairs["implementation_units"].extend(required_path_repairs)
            units, mapping_repairs = self._normalize_requirement_file_mappings(
                units,
                requirements,
                architecture_paths,
                contracts or (),
            )
            completion_repairs["implementation_units"].extend(mapping_repairs)
            units, verification, normalization_repairs = (
                self._normalize_implementation_units(
                    units, verification, requirements, contracts or ()
                )
            )
            completion_repairs["implementation_units"].extend(
                normalization_repairs
            )
            units, criterion_mapping_repairs = self._normalize_acceptance_criterion_implementation_mapping(
                units, requirements
            )
            completion_repairs["implementation_units"].extend(criterion_mapping_repairs)
            verification, verification_repairs = (
                self._complete_verification_coverage(
                    verification, units, requirements
                )
            )
            completion_repairs["verification"] = [
                *manual_instruction_repairs,
                *verification_repairs,
            ]
            if any(completion_repairs.values()):
                self._write_diagnostic(
                    "integration_completion_repairs.json", completion_repairs
                )

        self._unique_ids(units, "unit_id")
        self._unique_ids(verification, "criterion_id")

        if requirements is not None:
            self._validate_integration_coverage(
                units, verification, requirements, architecture_paths=architecture_paths
            )

        return units, verification

    @classmethod
    def _validate_integration_coverage(
        cls, units, verification, requirements, *, architecture_paths=None
    ):
        """Validate semantic coverage, file-level work, and all cross references."""
        requirement_by_id = {item.requirement_id: item for item in requirements}
        requirement_ids = set(requirement_by_id)
        unit_ids = {item.unit_id for item in units}
        required_unit_ids = {
            item.requirement_id for item in requirements
            if item.kind != RequirementKind.CONSTRAINT
            and not cls._is_directory_only_requirement(item)
        }

        unit_coverage = {req_id for unit in units for req_id in unit.satisfies}
        verification_coverage = {
            ref for criterion in verification for ref in criterion.covers
            if ref in requirement_ids
        }
        covered_unit_ids = {
            ref for criterion in verification for ref in criterion.covers
            if ref in unit_ids
        }
        verification_coverage.update(
            req_id for unit in units if unit.unit_id in covered_unit_ids
            for req_id in unit.satisfies
        )

        unknown_unit_requirements = unit_coverage - requirement_ids
        unknown_verification_refs = {
            ref for criterion in verification for ref in criterion.covers
            if ref not in requirement_ids and ref not in unit_ids
        }
        unknown_dependencies = {
            dependency for unit in units for dependency in unit.depends_on
            if dependency not in unit_ids
        }
        missing_units = required_unit_ids - unit_coverage
        missing_verification = requirement_ids - verification_coverage
        all_acceptance_ids = {
            criterion_id for requirement in requirements
            for criterion_id in requirement.acceptance_criterion_ids
        }
        required_implementation_acceptance_ids = {
            criterion_id
            for requirement in requirements
            if requirement.kind != RequirementKind.CONSTRAINT
            and not cls._is_directory_only_requirement(requirement)
            for criterion_id in requirement.acceptance_criterion_ids
        }
        implemented_acceptance_ids = {
            criterion_id for unit in units
            for criterion_id in unit.implements_acceptance_criteria
        }
        verified_acceptance_ids = {
            criterion_id for criterion in verification if criterion.required
            for criterion_id in criterion.covers_acceptance_criteria
        }
        # Compatibility for hand-authored/legacy specifications: a structural
        # check directly covering a directory-only requirement verifies its
        # directory-existence acceptance criteria. Generated specifications
        # receive explicit covers_acceptance_criteria mappings above.
        for criterion in verification:
            if not criterion.required or criterion.kind != VerificationKind.STRUCTURAL:
                continue
            for requirement_id in criterion.covers:
                requirement = requirement_by_id.get(requirement_id)
                if requirement and cls._is_directory_only_requirement(requirement):
                    verified_acceptance_ids.update(requirement.acceptance_criterion_ids)
        missing_implementation_acceptance = (
            required_implementation_acceptance_ids - implemented_acceptance_ids
        )
        missing_verification_acceptance = all_acceptance_ids - verified_acceptance_ids
        unknown_implementation_acceptance = implemented_acceptance_ids - all_acceptance_ids
        unknown_verification_acceptance = {
            criterion_id for criterion in verification
            for criterion_id in criterion.covers_acceptance_criteria
            if criterion_id not in all_acceptance_ids
        }

        missing_file_units = []
        missing_directory_verification = []
        if architecture_paths is not None:
            for requirement in requirements:
                if not cls._is_directory_only_requirement(requirement):
                    continue
                structural_coverage = [
                    criterion for criterion in verification
                    if requirement.requirement_id in criterion.covers
                    and criterion.kind == VerificationKind.STRUCTURAL
                ]
                if not structural_coverage:
                    missing_directory_verification.append(requirement.requirement_id)
            for path, requirement_ids_for_path in sorted(
                cls._required_architecture_paths(requirements).items()
            ):
                for requirement_id in sorted(requirement_ids_for_path):
                    requirement = requirement_by_id[requirement_id]
                    if requirement.kind == RequirementKind.CONSTRAINT:
                        continue
                    if path in architecture_paths and not any(
                        unit.target_path.replace("\\", "/") == path
                        and requirement_id in unit.satisfies
                        for unit in units
                    ):
                        missing_file_units.append(f"{requirement_id}: {path}")

        diagnostics = []
        if missing_units:
            diagnostics.append(
                f"functional/non-functional requirements lack implementation-unit coverage: {sorted(missing_units)}"
            )
        if missing_verification:
            diagnostics.append(f"requirements lack verification coverage: {sorted(missing_verification)}")
        if missing_implementation_acceptance:
            diagnostics.append(
                "acceptance criteria lack implementation-unit coverage: "
                f"{sorted(missing_implementation_acceptance)}"
            )
        if missing_verification_acceptance:
            diagnostics.append(
                "acceptance criteria lack verification coverage: "
                f"{sorted(missing_verification_acceptance)}"
            )
        if unknown_implementation_acceptance:
            diagnostics.append(
                "implementation units reference unknown acceptance criteria: "
                f"{sorted(unknown_implementation_acceptance)}"
            )
        if unknown_verification_acceptance:
            diagnostics.append(
                "verification criteria reference unknown acceptance criteria: "
                f"{sorted(unknown_verification_acceptance)}"
            )
        if missing_file_units:
            diagnostics.append(
                "requirements lack a dedicated implementation unit for required file path(s): "
                + ", ".join(missing_file_units)
            )
        if missing_directory_verification:
            diagnostics.append(
                "directory-only requirements need structural verification coverage: "
                + ", ".join(sorted(missing_directory_verification))
            )
        if unknown_unit_requirements:
            diagnostics.append(f"implementation units reference unknown requirements: {sorted(unknown_unit_requirements)}")
        if unknown_verification_refs:
            diagnostics.append(f"verification criteria cover unknown references: {sorted(unknown_verification_refs)}")
        if unknown_dependencies:
            diagnostics.append(f"implementation units reference unknown dependencies: {sorted(unknown_dependencies)}")

        if diagnostics:
            raise ValueError("; ".join(diagnostics))

    @staticmethod
    def _parse_verification_kind(value):
        """Normalize common model vocabulary into the canonical ESpec enum.

        The model may describe the *method* of verification (for example,
        ``api`` or ``unit_test``) rather than the ESpec verification category.
        Normalize known, semantically clear aliases at this boundary while
        keeping the persisted schema restricted to VerificationKind.
        """
        aliases = {
            # Tests that assert application or interface behavior.
            "test": VerificationKind.BEHAVIORAL.value,
            "tests": VerificationKind.BEHAVIORAL.value,
            "unit": VerificationKind.BEHAVIORAL.value,
            "unit_test": VerificationKind.BEHAVIORAL.value,
            "integration": VerificationKind.BEHAVIORAL.value,
            "integration_test": VerificationKind.BEHAVIORAL.value,
            "e2e": VerificationKind.BEHAVIORAL.value,
            "end_to_end": VerificationKind.BEHAVIORAL.value,
            "api": VerificationKind.BEHAVIORAL.value,
            "api_test": VerificationKind.BEHAVIORAL.value,
            "functional": VerificationKind.BEHAVIORAL.value,
            # Static analysis and source-level checks.
            "lint": VerificationKind.STATIC.value,
            "linting": VerificationKind.STATIC.value,
            "type_check": VerificationKind.STATIC.value,
            "typecheck": VerificationKind.STATIC.value,
            "syntax": VerificationKind.STATIC.value,
            "static_analysis": VerificationKind.STATIC.value,
            # Workspace/file/layout checks.
            "structure": VerificationKind.STRUCTURAL.value,
            "file_check": VerificationKind.STRUCTURAL.value,
            "filesystem": VerificationKind.STRUCTURAL.value,
            # Compilation and packaging.
            "compile": VerificationKind.BUILD.value,
            "compilation": VerificationKind.BUILD.value,
            "build_check": VerificationKind.BUILD.value,
            # Executing the application or a deployed component.
            "smoke": VerificationKind.RUNTIME.value,
            "smoke_test": VerificationKind.RUNTIME.value,
            "runtime_check": VerificationKind.RUNTIME.value,
            # Human-operated checks.
            "human_review": VerificationKind.MANUAL.value,
            "inspection": VerificationKind.MANUAL.value,
            "manual_check": VerificationKind.MANUAL.value,
        }
        normalized = value.strip().lower() if isinstance(value, str) else value
        if isinstance(normalized, str):
            normalized = normalized.replace("-", "_").replace(" ", "_")
            normalized = aliases.get(normalized, normalized)
        try:
            return VerificationKind(normalized)
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(item.value for item in VerificationKind)
            raise ValueError(
                f"invalid verification kind {value!r}; use one of: {allowed}. "
                "Known aliases include api/unit/integration/e2e/test -> behavioral, "
                "lint/type_check -> static, smoke -> runtime, and human_review -> manual."
            ) from exc

    @staticmethod
    def _unique_ids(items, attr):
        values = [getattr(x, attr) for x in items]
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate {attr}")

    @staticmethod
    def _validate_coverage(spec):
        req_ids = {r.requirement_id for r in spec.requirements}
        required_unit_ids = {
            r.requirement_id for r in spec.requirements
            if r.kind != RequirementKind.CONSTRAINT
            and not EngineeringSpecificationGenerator._is_directory_only_requirement(r)
        }
        unit_coverage = {r for u in spec.implementation_units for r in u.satisfies}
        verified = {
            ref
            for criterion in spec.verification
            for ref in criterion.covers
            if ref in req_ids
        }
        covered_unit_ids = {
            ref
            for criterion in spec.verification
            for ref in criterion.covers
            if ref in {unit.unit_id for unit in spec.implementation_units}
        }
        verified.update(
            req_id
            for unit in spec.implementation_units
            if unit.unit_id in covered_unit_ids
            for req_id in unit.satisfies
        )
        missing_units = required_unit_ids - unit_coverage
        missing_verification = req_ids - verified
        if missing_units:
            raise ValueError(
                f"functional/non-functional requirements lack implementation coverage: {sorted(missing_units)}"
            )
        if missing_verification:
            raise ValueError(f"requirements lack verification coverage: {sorted(missing_verification)}")
        for requirement in spec.requirements:
            if not EngineeringSpecificationGenerator._is_directory_only_requirement(requirement):
                continue
            if not any(
                requirement.requirement_id in criterion.covers
                and criterion.kind == VerificationKind.STRUCTURAL
                for criterion in spec.verification
            ):
                raise ValueError(
                    "directory-only requirement needs structural verification coverage: "
                    f"{requirement.requirement_id}"
                )

    @staticmethod
    def _slug(value):
        slug = re.sub(r"[^a-zA-Z0-9-]+", "-", value).strip("-").lower()
        return slug or "task"

    @staticmethod
    def _requirement_dict(x):
        return {"requirement_id":x.requirement_id,"description":x.description,"kind":x.kind.value,
                "acceptance_criteria":list(x.acceptance_criteria),
                "acceptance_criterion_ids":list(x.acceptance_criterion_ids),"priority":x.priority}
    @staticmethod
    def _module_dict(x):
        return {"module_id":x.module_id,"name":x.name,"responsibility":x.responsibility,
                "depends_on":list(x.depends_on),"file_paths":list(x.file_paths)}
    @staticmethod
    def _contract_dict(x):
        return {"contract_id":x.contract_id,"owner":x.owner,"name":x.name,"kind":x.kind,
                "inputs":[vars(v) for v in x.inputs],"outputs":[vars(v) for v in x.outputs],
                "invariants":list(x.invariants)}
