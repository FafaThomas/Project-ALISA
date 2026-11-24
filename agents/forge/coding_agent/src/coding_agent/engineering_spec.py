"""Task-scoped Engineering Specification (ESpec) contracts.

A1 defines validated, serializable data models only. It deliberately does not
call an LLM, persist specifications, modify workspaces, or execute commands.
Those responsibilities belong to later milestones and the orchestrator.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any, Mapping


class SpecificationStage(str, Enum):
    REQUIREMENTS = "requirements"
    ARCHITECTURE = "architecture"
    CONTRACTS = "contracts"
    INTEGRATION = "integration"


class RequirementKind(str, Enum):
    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"
    CONSTRAINT = "constraint"


class UnitKind(str, Enum):
    SOURCE = "source"
    TEST = "test"
    CONFIGURATION = "configuration"
    DOCUMENTATION = "documentation"
    ASSET = "asset"


class VerificationKind(str, Enum):
    STATIC = "static"
    STRUCTURAL = "structural"
    BUILD = "build"
    BEHAVIORAL = "behavioral"
    RUNTIME = "runtime"
    MANUAL = "manual"


class SpecificationStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"


def _nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _unique(values: tuple[str, ...], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must contain unique values")




def make_acceptance_criterion_id(requirement_id: str, description: str) -> str:
    """Create a stable criterion identifier from its parent and wording.

    The digest is based on normalized criterion text, not its position in the
    requirement, so reordering criteria does not change their identifiers.
    """
    _nonempty(requirement_id, "requirement_id")
    _nonempty(description, "acceptance criterion description")
    normalized = " ".join(description.casefold().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10].upper()
    safe_requirement_id = re.sub(r"[^A-Za-z0-9_-]+", "-", requirement_id.strip())
    return f"{safe_requirement_id}-AC-{digest}"

def _workspace_path(value: str, field_name: str = "path") -> None:
    _nonempty(value, field_name)
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or path.anchor or any(part in ("..", "") for part in path.parts):
        raise ValueError(f"{field_name} must be a safe workspace-relative path")


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    description: str
    kind: RequirementKind = RequirementKind.FUNCTIONAL
    acceptance_criteria: tuple[str, ...] = ()
    priority: str = "normal"
    acceptance_criterion_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.requirement_id, "requirement_id")
        _nonempty(self.description, "description")
        if self.priority not in {"low", "normal", "high", "critical"}:
            raise ValueError("priority must be low, normal, high, or critical")
        if any(not item.strip() for item in self.acceptance_criteria):
            raise ValueError("acceptance criteria must not contain empty values")
        ids = self.acceptance_criterion_ids or tuple(
            make_acceptance_criterion_id(self.requirement_id, description)
            for description in self.acceptance_criteria
        )
        if len(ids) != len(self.acceptance_criteria):
            raise ValueError(
                "acceptance_criterion_ids must have one ID for each acceptance criterion"
            )
        _unique(ids, "acceptance_criterion_ids")
        for criterion_id in ids:
            _nonempty(criterion_id, "acceptance criterion ID")
        object.__setattr__(self, "acceptance_criterion_ids", tuple(ids))


@dataclass(frozen=True)
class ArchitectureModule:
    module_id: str
    name: str
    responsibility: str
    depends_on: tuple[str, ...] = ()
    file_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("module_id", "name", "responsibility"):
            _nonempty(getattr(self, name), name)
        _unique(self.depends_on, "depends_on")
        _unique(self.file_paths, "file_paths")
        for path in self.file_paths:
            _workspace_path(path, "file path")
        if self.module_id in self.depends_on:
            raise ValueError("a module cannot depend on itself")


@dataclass(frozen=True)
class InterfaceField:
    name: str
    type_name: str
    required: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        _nonempty(self.name, "field name")
        _nonempty(self.type_name, "field type")


@dataclass(frozen=True)
class InterfaceContract:
    contract_id: str
    owner: str
    name: str
    kind: str
    inputs: tuple[InterfaceField, ...] = ()
    outputs: tuple[InterfaceField, ...] = ()
    invariants: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("contract_id", "owner", "name", "kind"):
            _nonempty(getattr(self, name), name)
        _unique(tuple(item.name for item in self.inputs), "input names")
        _unique(tuple(item.name for item in self.outputs), "output names")


@dataclass(frozen=True)
class ImplementationUnit:
    unit_id: str
    title: str
    target_path: str
    kind: UnitKind = UnitKind.SOURCE
    depends_on: tuple[str, ...] = ()
    satisfies: tuple[str, ...] = ()
    implements: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    implements_acceptance_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("unit_id", "title"):
            _nonempty(getattr(self, name), name)
        _workspace_path(self.target_path, "target_path")
        for name in ("depends_on", "satisfies", "implements", "implements_acceptance_criteria"):
            _unique(getattr(self, name), name)
        if self.unit_id in self.depends_on:
            raise ValueError("an implementation unit cannot depend on itself")


@dataclass(frozen=True)
class VerificationCriterion:
    criterion_id: str
    description: str
    kind: VerificationKind
    required: bool = True
    command: tuple[str, ...] = ()
    covers: tuple[str, ...] = ()
    covers_acceptance_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.criterion_id, "criterion_id")
        _nonempty(self.description, "description")
        if self.command and any(not part for part in self.command):
            raise ValueError("verification command arguments must not be empty")
        _unique(self.covers, "covers")
        _unique(self.covers_acceptance_criteria, "covers_acceptance_criteria")
        if self.kind == VerificationKind.MANUAL and self.command:
            raise ValueError("manual verification criteria cannot define a command")


@dataclass(frozen=True)
class EngineeringSpecification:
    """Complete task-scoped design contract for one engineering objective."""

    specification_id: str
    task_id: str
    objective: str
    workspace: str
    requirements: tuple[Requirement, ...] = ()
    architecture: tuple[ArchitectureModule, ...] = ()
    contracts: tuple[InterfaceContract, ...] = ()
    implementation_units: tuple[ImplementationUnit, ...] = ()
    verification: tuple[VerificationCriterion, ...] = ()
    generation_stages: tuple[SpecificationStage, ...] = (
        SpecificationStage.REQUIREMENTS,
        SpecificationStage.ARCHITECTURE,
        SpecificationStage.CONTRACTS,
        SpecificationStage.INTEGRATION,
    )
    execution_profile: str = "sandbox"
    status: SpecificationStatus = SpecificationStatus.DRAFT
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("specification_id", "task_id", "objective", "workspace"):
            _nonempty(getattr(self, name), name)
        if self.execution_profile != "sandbox":
            raise ValueError("A1 specifications must use the sandbox execution profile")
        for attr, key in (
            (self.requirements, "requirement_id"),
            (self.architecture, "module_id"),
            (self.contracts, "contract_id"),
            (self.implementation_units, "unit_id"),
            (self.verification, "criterion_id"),
        ):
            _unique(tuple(getattr(item, key) for item in attr), key)
        self._validate_references()
        self._validate_module_graph()
        self._validate_unit_graph()

    def _validate_references(self) -> None:
        requirement_ids = {item.requirement_id for item in self.requirements}
        module_ids = {item.module_id for item in self.architecture}
        contract_ids = {item.contract_id for item in self.contracts}
        unit_ids = {item.unit_id for item in self.implementation_units}
        for module in self.architecture:
            missing = set(module.depends_on) - module_ids
            if missing:
                raise ValueError(f"module {module.module_id!r} references unknown dependencies: {sorted(missing)}")
        for unit in self.implementation_units:
            for values, known, label in (
                (unit.depends_on, unit_ids, "unit dependencies"),
                (unit.satisfies, requirement_ids, "requirements"),
                (unit.implements, contract_ids, "contracts"),
            ):
                missing = set(values) - known
                if missing:
                    raise ValueError(f"unit {unit.unit_id!r} references unknown {label}: {sorted(missing)}")
        acceptance_criterion_owner = {
            criterion_id: requirement.requirement_id
            for requirement in self.requirements
            for criterion_id in requirement.acceptance_criterion_ids
        }
        if len(acceptance_criterion_owner) != sum(
            len(requirement.acceptance_criterion_ids) for requirement in self.requirements
        ):
            raise ValueError("acceptance criterion IDs must be unique across requirements")
        acceptance_criterion_ids = set(acceptance_criterion_owner)
        for unit in self.implementation_units:
            missing = set(unit.implements_acceptance_criteria) - set(acceptance_criterion_owner)
            if missing:
                raise ValueError(
                    f"unit {unit.unit_id!r} implements unknown acceptance criteria: {sorted(missing)}"
                )
            unrelated = {
                criterion_id for criterion_id in unit.implements_acceptance_criteria
                if acceptance_criterion_owner[criterion_id] not in unit.satisfies
            }
            if unrelated:
                raise ValueError(
                    f"unit {unit.unit_id!r} maps acceptance criteria outside its satisfied requirements: "
                    f"{sorted(unrelated)}"
                )
        for criterion in self.verification:
            missing = set(criterion.covers) - (requirement_ids | unit_ids)
            if missing:
                raise ValueError(f"verification {criterion.criterion_id!r} covers unknown items: {sorted(missing)}")
            missing_acceptance = set(criterion.covers_acceptance_criteria) - acceptance_criterion_ids
            if missing_acceptance:
                raise ValueError(
                    f"verification {criterion.criterion_id!r} covers unknown acceptance criteria: {sorted(missing_acceptance)}"
                )

    @staticmethod
    def _assert_acyclic(graph: dict[str, tuple[str, ...]], label: str) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError(f"{label} dependency graph contains a cycle at {node!r}")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)
        for node in graph:
            visit(node)

    def _validate_module_graph(self) -> None:
        self._assert_acyclic({m.module_id: m.depends_on for m in self.architecture}, "module")

    def _validate_unit_graph(self) -> None:
        self._assert_acyclic({u.unit_id: u.depends_on for u in self.implementation_units}, "implementation")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation with enum values."""
        def convert(value: Any) -> Any:
            if isinstance(value, Enum):
                return value.value
            if is_dataclass(value):
                return {k: convert(v) for k, v in asdict(value).items()}
            if isinstance(value, Mapping):
                return {str(k): convert(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [convert(item) for item in value]
            return value
        return convert(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)
