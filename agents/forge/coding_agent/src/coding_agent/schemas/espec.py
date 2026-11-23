"""Pydantic contracts for LLM-produced Engineering Specification stages.

These models validate the shape and primitive values crossing the LLM boundary.
The existing EngineeringSpecification dataclasses and semantic validators remain
responsible for domain behavior, cross-artifact references, and engineering rules.
"""
from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated

from coding_agent.engineering_spec import make_acceptance_criterion_id
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StageModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        use_enum_values=True,
    )


class RequirementPayload(StageModel):
    requirement_id: NonEmptyString
    description: NonEmptyString
    # The domain parser owns aliases and canonical enum diagnostics.
    kind: str = "functional"
    acceptance_criteria: list[NonEmptyString] = Field(default_factory=list)
    acceptance_criterion_ids: list[NonEmptyString] = Field(default_factory=list)
    priority: str = "normal"

    @model_validator(mode="after")
    def assign_stable_acceptance_criterion_ids(self):
        if not self.acceptance_criterion_ids:
            object.__setattr__(self, "acceptance_criterion_ids", [
                make_acceptance_criterion_id(self.requirement_id, description)
                for description in self.acceptance_criteria
            ])
        if len(self.acceptance_criterion_ids) != len(self.acceptance_criteria):
            raise ValueError(
                "acceptance_criterion_ids must have one ID for each acceptance criterion"
            )
        if len(self.acceptance_criterion_ids) != len(set(self.acceptance_criterion_ids)):
            raise ValueError("acceptance_criterion_ids must be unique")
        return self

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str) -> str:
        if value not in {"low", "normal", "high", "critical"}:
            raise ValueError("priority must be low, normal, high, or critical")
        return value


class RequirementsStagePayload(StageModel):
    requirements: list[RequirementPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_requirement_ids(self):
        ids = [item.requirement_id for item in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("requirement_id values must be unique")
        acceptance_ids = [
            criterion_id for item in self.requirements
            for criterion_id in item.acceptance_criterion_ids
        ]
        if len(acceptance_ids) != len(set(acceptance_ids)):
            raise ValueError("acceptance criterion IDs must be unique across requirements")
        return self


class ArchitectureModulePayload(StageModel):
    module_id: NonEmptyString
    name: NonEmptyString
    responsibility: NonEmptyString
    depends_on: list[NonEmptyString] = Field(default_factory=list)
    file_paths: list[NonEmptyString] = Field(default_factory=list)

    @field_validator("depends_on", "file_paths")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("values must be unique")
        return values

    @field_validator("file_paths")
    @classmethod
    def safe_workspace_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            path = PurePosixPath(value.replace("\\", "/"))
            if path.is_absolute() or path.anchor or ".." in path.parts:
                raise ValueError(f"file path must be workspace-relative and safe: {value!r}")
        return values


class ArchitectureStagePayload(StageModel):
    architecture: list[ArchitectureModulePayload] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_module_graph(self):
        ids = [item.module_id for item in self.architecture]
        if len(ids) != len(set(ids)):
            raise ValueError("module_id values must be unique")
        known = set(ids)
        graph = {item.module_id: item.depends_on for item in self.architecture}
        for module_id, dependencies in graph.items():
            missing = set(dependencies) - known
            if missing:
                raise ValueError(f"module {module_id!r} references unknown dependencies: {sorted(missing)}")
            if module_id in dependencies:
                raise ValueError(f"module {module_id!r} cannot depend on itself")
        _assert_acyclic(graph, "module")
        return self


class InterfaceFieldPayload(StageModel):
    name: NonEmptyString
    type_name: NonEmptyString
    required: bool = True
    description: str = ""


class InterfaceContractPayload(StageModel):
    contract_id: NonEmptyString
    owner: NonEmptyString
    name: NonEmptyString
    kind: NonEmptyString
    inputs: list[InterfaceFieldPayload] = Field(default_factory=list)
    outputs: list[InterfaceFieldPayload] = Field(default_factory=list)
    invariants: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_field_names(self):
        for label, fields in (("input", self.inputs), ("output", self.outputs)):
            names = [item.name for item in fields]
            if len(names) != len(set(names)):
                raise ValueError(f"{label} field names must be unique")
        return self


class ContractsStagePayload(StageModel):
    contracts: list[InterfaceContractPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_contract_ids(self):
        ids = [item.contract_id for item in self.contracts]
        if len(ids) != len(set(ids)):
            raise ValueError("contract_id values must be unique")
        return self


class ImplementationUnitPayload(StageModel):
    unit_id: NonEmptyString
    title: NonEmptyString
    target_path: str
    # The domain parser normalizes legacy aliases and reports allowed values.
    kind: str = "source"
    depends_on: list[NonEmptyString] = Field(default_factory=list)
    satisfies: list[NonEmptyString] = Field(default_factory=list)
    implements: list[NonEmptyString] = Field(default_factory=list)
    acceptance_criteria: list[NonEmptyString] = Field(default_factory=list)
    implements_acceptance_criteria: list[NonEmptyString] = Field(default_factory=list)

    @field_validator("target_path")
    @classmethod
    def safe_target_path(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or path.anchor or ".." in path.parts:
            raise ValueError("target_path must be a safe workspace-relative path")
        return value

    @field_validator("depends_on", "satisfies", "implements", "implements_acceptance_criteria")
    @classmethod
    def unique_references(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("references must be unique")
        return values


class VerificationPayload(StageModel):
    criterion_id: NonEmptyString | None = Field(
        default=None,
        validation_alias=AliasChoices("criterion_id", "verification_id", "id"),
    )
    description: NonEmptyString
    # The domain parser normalizes aliases such as api-test and type_check.
    kind: str
    required: bool = True
    command: list[str] = Field(default_factory=list)
    covers: list[NonEmptyString] = Field(default_factory=list)
    covers_acceptance_criteria: list[NonEmptyString] = Field(default_factory=list)

    @field_validator("command")
    @classmethod
    def command_arguments_are_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value for value in values):
            raise ValueError("verification command arguments must not be empty")
        return values

    @field_validator("covers", "covers_acceptance_criteria")
    @classmethod
    def unique_coverage(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("verification coverage values must be unique")
        return values


class IntegrationStagePayload(StageModel):
    implementation_units: list[ImplementationUnitPayload] = Field(default_factory=list)
    verification: list[VerificationPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unit_graph(self):
        ids = [item.unit_id for item in self.implementation_units]
        if len(ids) != len(set(ids)):
            raise ValueError("unit_id values must be unique")
        known = set(ids)
        graph = {item.unit_id: item.depends_on for item in self.implementation_units}
        for unit_id, dependencies in graph.items():
            missing = set(dependencies) - known
            if missing:
                raise ValueError(f"unit {unit_id!r} references unknown dependencies: {sorted(missing)}")
            if unit_id in dependencies:
                raise ValueError(f"unit {unit_id!r} cannot depend on itself")
        _assert_acyclic(graph, "implementation unit")
        criterion_ids = [item.criterion_id for item in self.verification if item.criterion_id]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criterion_id values must be unique")
        return self


def _assert_acyclic(graph: dict[str, list[str]], label: str) -> None:
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
