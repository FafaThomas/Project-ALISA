"""Bounded incremental impact analysis and implementation-plan selection.

A5 reuses the existing CodeIndex relationships, ESpec implementation graph,
and validated artifact pipeline. This module does not index, generate, write,
execute, repair, or persist workspace state.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from coding_agent.artifact_pipeline import PreparedArtifactBatch
from coding_agent.artifact_validator import ArtifactValidationReport
from coding_agent.code_generator import ImplementationArtifact
from coding_agent.resolvers.models import Relationship
from coding_agent.code_index import CodeIndex
from coding_agent.engineering_spec import EngineeringSpecification
from coding_agent.implementation_graph import ImplementationGraphBuilder
from coding_agent.models import ChangeType, FileChange, ImplementationPlan
from coding_agent.workspace import Workspace


class IncrementalAnalysisError(ValueError):
    """Raised when incremental inputs are invalid or inconsistent."""


def _normalize_workspace_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IncrementalAnalysisError("changed paths must be non-empty strings")
    normalized = value.replace("\\", "/")
    raw_parts = normalized.split("/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or path.anchor or any(part in {"", ".", ".."} for part in raw_parts):
        raise IncrementalAnalysisError(f"unsafe workspace-relative path: {value!r}")
    if len(path.parts) and ":" in path.parts[0]:
        raise IncrementalAnalysisError(f"unsafe workspace-relative path: {value!r}")
    return path.as_posix()


@dataclass(frozen=True)
class ImpactReport:
    """Deterministic impact results from a single CodeIndex snapshot."""

    changed_paths: tuple[str, ...]
    affected_paths: tuple[str, ...]
    dependent_paths: tuple[str, ...]
    unresolved_relationships: tuple[Relationship, ...] = ()
    ambiguous_relationships: tuple[Relationship, ...] = ()
    unindexed_changed_paths: tuple[str, ...] = ()

    @property
    def has_uncertainty(self) -> bool:
        return bool(self.unresolved_relationships or self.ambiguous_relationships)

    @property
    def complete(self) -> bool:
        return not self.has_uncertainty and not self.unindexed_changed_paths


class ChangeImpactAnalyzer:
    """Find transitive resolved dependents of changed workspace files."""

    def analyze(self, index: CodeIndex, changed_paths: Iterable[str]) -> ImpactReport:
        changed = tuple(sorted({_normalize_workspace_path(path) for path in changed_paths}))
        if not changed:
            raise IncrementalAnalysisError("at least one changed path is required")

        indexed_paths = {module.relative_path.replace("\\\\", "/") for module in index.modules}
        relationships = tuple(index.relationships)
        # Older/custom indexes may only provide Dependency records.
        resolved_pairs = {
            (rel.source_path.replace("\\\\", "/"), rel.target_path.replace("\\\\", "/"))
            for rel in relationships
            if rel.status == "resolved" and rel.target_path
            and rel.kind not in {"contains", "candidate"}
        }
        if not relationships:
            module_by_name = {
                alias: module.relative_path.replace("\\\\", "/")
                for module in index.modules for alias in module.module_names
            }
            resolved_pairs.update(
                (module_by_name[dep.source_module], module_by_name[dep.target_module])
                for dep in index.dependencies
                if dep.source_module in module_by_name and dep.target_module in module_by_name
            )

        reverse: dict[str, set[str]] = {}
        for dependent, dependency in resolved_pairs:
            reverse.setdefault(dependency, set()).add(dependent)

        affected = set(changed)
        frontier = list(changed)
        while frontier:
            current = frontier.pop()
            for dependent in sorted(reverse.get(current, ())):
                if dependent not in affected:
                    affected.add(dependent)
                    frontier.append(dependent)

        dependents = affected - set(changed)
        order = lambda values: tuple(sorted(values, key=lambda p: (p.casefold(), p)))
        relevant_uncertain = [
            rel for rel in relationships
            if rel.status in {"unresolved", "ambiguous"}
            and (
                rel.source_path.replace("\\\\", "/") in affected
                or any(candidate.replace("\\\\", "/") in affected for candidate in rel.candidates)
            )
        ]
        return ImpactReport(
            changed_paths=changed,
            affected_paths=order(affected),
            dependent_paths=order(dependents),
            unresolved_relationships=tuple(sorted(
                (r for r in relevant_uncertain if r.status == "unresolved"),
                key=lambda r: (r.source_path, r.line, r.target_symbol or ""),
            )),
            ambiguous_relationships=tuple(sorted(
                (r for r in relevant_uncertain if r.status == "ambiguous"),
                key=lambda r: (r.source_path, r.line, r.target_symbol or ""),
            )),
            unindexed_changed_paths=order(set(changed) - indexed_paths),
        )


@dataclass(frozen=True)
class IncrementalUnitSelection:
    """Implementation units selected for an impact report."""

    selected_unit_ids: tuple[str, ...]
    directly_affected_unit_ids: tuple[str, ...]
    unmatched_affected_paths: tuple[str, ...]
    omitted_unit_ids: tuple[str, ...]


class IncrementalUnitSelector:
    """Map affected file paths to ESpec units and include their downstream units."""

    def __init__(self, graph_builder: ImplementationGraphBuilder | None = None) -> None:
        self.graph_builder = graph_builder or ImplementationGraphBuilder()

    def select(
        self,
        specification: EngineeringSpecification,
        impact: ImpactReport,
    ) -> IncrementalUnitSelection:
        graph = self.graph_builder.build(specification)
        path_to_units: dict[str, set[str]] = {}
        for unit in specification.implementation_units:
            path = _normalize_workspace_path(unit.target_path)
            path_to_units.setdefault(path, set()).add(unit.unit_id)

        direct = {
            unit_id
            for path in impact.affected_paths
            for unit_id in path_to_units.get(path, ())
        }
        selected = set(direct)
        for unit_id in sorted(direct):
            selected.update(graph.downstream_impact(unit_id))

        order = graph.ordered_unit_ids
        affected_paths = set(impact.affected_paths)
        matched_paths = set(path_to_units) & affected_paths
        unmatched = affected_paths - matched_paths
        return IncrementalUnitSelection(
            selected_unit_ids=tuple(unit for unit in order if unit in selected),
            directly_affected_unit_ids=tuple(unit for unit in order if unit in direct),
            unmatched_affected_paths=tuple(sorted(unmatched, key=lambda p: (p.casefold(), p))),
            omitted_unit_ids=tuple(unit for unit in order if unit not in selected),
        )


@dataclass(frozen=True)
class IncrementalImplementation:
    """Selective implementation plan derived from a validated artifact batch."""

    task_id: str
    impact: ImpactReport
    selection: IncrementalUnitSelection
    artifacts: tuple[ImplementationArtifact, ...]
    plan: ImplementationPlan
    validation: ArtifactValidationReport


class IncrementalPlanBuilder:
    """Project an already validated artifact batch into a selective plan."""

    def build(
        self,
        workspace: Workspace,
        batch: PreparedArtifactBatch,
        impact: ImpactReport,
        selection: IncrementalUnitSelection,
    ) -> IncrementalImplementation:
        if not batch.ready_for_approval or batch.plan is None:
            raise IncrementalAnalysisError(
                "incremental planning requires an artifact batch accepted for approval"
            )
        selected = set(selection.selected_unit_ids)
        artifacts = tuple(
            artifact for artifact in batch.artifacts
            if artifact.unit_id in selected
        )
        artifact_ids = {artifact.unit_id for artifact in artifacts}
        missing = selected - artifact_ids
        if missing:
            raise IncrementalAnalysisError(
                f"validated artifact batch is missing selected units: {sorted(missing)}"
            )
        if not artifacts:
            raise IncrementalAnalysisError(
                "impact analysis selected no implementation units; refusing an empty plan"
            )

        changes = []
        for artifact in artifacts:
            try:
                target = workspace.resolve_path(artifact.path)
                exists = target.is_file()
                if target.exists() and not exists:
                    raise IncrementalAnalysisError(
                        f"artifact target is not a regular file: {artifact.path}"
                    )
            except FileNotFoundError:
                exists = False
            except Exception as exc:
                message = str(exc).casefold()
                if "does not resolve to an existing target" in message or "target does not exist" in message:
                    workspace.resolve_create_path(artifact.path)
                    exists = False
                else:
                    raise IncrementalAnalysisError(
                        f"cannot safely resolve incremental target {artifact.path!r}: {exc}"
                    ) from exc
            changes.append(FileChange(
                path=artifact.path,
                change_type=ChangeType.MODIFY if exists else ChangeType.CREATE,
                reason=f"Incremental implementation of ESpec unit {artifact.unit_id}",
                content=artifact.content,
            ))

        plan = ImplementationPlan(
            task_id=batch.task_id,
            summary=f"Incrementally implement {len(artifacts)} affected ESpec unit(s)",
            changes=tuple(changes),
            verification_commands=batch.plan.verification_commands,
            requires_approval=True,
            risks=batch.plan.risks + ("Plan is scoped to the supplied impact analysis.",),
        )
        return IncrementalImplementation(
            task_id=batch.task_id,
            impact=impact,
            selection=selection,
            artifacts=artifacts,
            plan=plan,
            validation=batch.validation,
        )
