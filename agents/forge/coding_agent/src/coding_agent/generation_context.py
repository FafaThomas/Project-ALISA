"""Build bounded, contract-aware context for one implementation unit.

This module only assembles an immutable prompt context. It does not call an
LLM, read/write workspace files, execute commands, or mutate the ESpec.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Mapping

from coding_agent.engineering_spec import EngineeringSpecification, ImplementationUnit


class GenerationContextError(ValueError):
    """Raised when a unit cannot be given a complete, safe generation context."""


@dataclass(frozen=True)
class DependencyArtifact:
    unit_id: str
    target_path: str
    content: str


@dataclass(frozen=True)
class GenerationContext:
    specification_id: str
    task_id: str
    objective: str
    workspace: str
    unit: dict
    requirements: tuple[dict, ...]
    architecture_modules: tuple[dict, ...]
    contracts: tuple[dict, ...]
    dependency_artifacts: tuple[dict, ...]
    project_files: tuple[dict, ...]
    acceptance_criteria: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


class GenerationContextBuilder:
    """Assemble only the specification context relevant to a single unit."""

    def __init__(
        self,
        *,
        max_file_chars: int = 12_000,
        max_total_project_chars: int = 40_000,
    ) -> None:
        if max_file_chars < 1 or max_total_project_chars < 1:
            raise ValueError("context character limits must be positive")
        self.max_file_chars = max_file_chars
        self.max_total_project_chars = max_total_project_chars

    def build(
        self,
        specification: EngineeringSpecification,
        unit_id: str,
        *,
        dependency_artifacts: Mapping[str, str] | None = None,
        project_files: Mapping[str, str] | None = None,
    ) -> GenerationContext:
        units = {unit.unit_id: unit for unit in specification.implementation_units}
        unit = units.get(unit_id)
        if unit is None:
            raise GenerationContextError(f"unknown implementation unit: {unit_id}")

        supplied_artifacts = dict(dependency_artifacts or {})
        unknown_artifacts = set(supplied_artifacts) - set(units)
        if unknown_artifacts:
            raise GenerationContextError(
                f"dependency artifacts reference unknown units: {sorted(unknown_artifacts)}"
            )
        missing_artifacts = set(unit.depends_on) - supplied_artifacts.keys()
        if missing_artifacts:
            raise GenerationContextError(
                f"unit {unit_id!r} is not ready; missing dependency artifacts: {sorted(missing_artifacts)}"
            )

        requirements_by_id = {item.requirement_id: item for item in specification.requirements}
        contracts_by_id = {item.contract_id: item for item in specification.contracts}
        modules_by_id = {item.module_id: item for item in specification.architecture}

        requirements = tuple(
            self._requirement(requirements_by_id[key])
            for key in sorted(unit.satisfies)
        )
        contracts = tuple(
            self._contract(contracts_by_id[key])
            for key in sorted(unit.implements)
        )
        owner_ids = {contract.owner for contract in (contracts_by_id[key] for key in unit.implements)}
        architecture_modules = tuple(
            self._module(modules_by_id[key])
            for key in sorted(owner_ids)
        )

        artifacts = tuple(
            {
                "unit_id": dep_id,
                "target_path": units[dep_id].target_path,
                "content": supplied_artifacts[dep_id],
            }
            for dep_id in sorted(unit.depends_on)
        )

        files = self._project_file_context(project_files or {})
        return GenerationContext(
            specification_id=specification.specification_id,
            task_id=specification.task_id,
            objective=specification.objective,
            workspace=specification.workspace,
            unit=self._unit(unit),
            requirements=requirements,
            architecture_modules=architecture_modules,
            contracts=contracts,
            dependency_artifacts=artifacts,
            project_files=files,
            acceptance_criteria=tuple(unit.acceptance_criteria),
        )

    def _project_file_context(self, files: Mapping[str, str]) -> tuple[dict, ...]:
        normalized: dict[str, str] = {}
        for path, content in files.items():
            if not isinstance(path, str) or not path.strip():
                raise GenerationContextError("project file paths must be non-empty strings")
            if not isinstance(content, str):
                raise GenerationContextError(f"project file content must be text: {path}")
            safe = path.replace("\\", "/")
            from pathlib import PurePosixPath
            candidate = PurePosixPath(safe)
            if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
                raise GenerationContextError(f"project file path must be workspace-relative: {path}")
            normalized[safe] = content

        result = []
        total = 0
        for path in sorted(normalized):
            content = normalized[path]
            clipped = content[: self.max_file_chars]
            remaining = self.max_total_project_chars - total
            if remaining <= 0:
                break
            clipped = clipped[:remaining]
            if clipped:
                result.append({"path": path, "content": clipped, "truncated": len(clipped) < len(content)})
                total += len(clipped)
        return tuple(result)

    @staticmethod
    def _unit(item: ImplementationUnit) -> dict:
        return {
            "unit_id": item.unit_id, "title": item.title,
            "target_path": item.target_path, "kind": item.kind.value,
            "depends_on": list(item.depends_on), "satisfies": list(item.satisfies),
            "implements": list(item.implements),
            "acceptance_criteria": list(item.acceptance_criteria),
        }

    @staticmethod
    def _requirement(item) -> dict:
        return {
            "requirement_id": item.requirement_id, "description": item.description,
            "kind": item.kind.value, "priority": item.priority,
            "acceptance_criteria": list(item.acceptance_criteria),
        }

    @staticmethod
    def _module(item) -> dict:
        return {
            "module_id": item.module_id, "name": item.name,
            "responsibility": item.responsibility,
            "depends_on": list(item.depends_on), "file_paths": list(item.file_paths),
        }

    @staticmethod
    def _contract(item) -> dict:
        def fields(values):
            return [
                {"name": field.name, "type_name": field.type_name,
                 "required": field.required, "description": field.description}
                for field in values
            ]
        return {
            "contract_id": item.contract_id, "owner": item.owner,
            "name": item.name, "kind": item.kind,
            "inputs": fields(item.inputs), "outputs": fields(item.outputs),
            "invariants": list(item.invariants),
        }
