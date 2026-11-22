"""Connect ESpec generation and artifact validation to the approval-plan boundary.

This module is a preparation stage only: it never writes workspace files or
executes commands. A prepared ImplementationPlan can be handed to the existing
human-approval and transactional execution layer by the consuming application.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from coding_agent.artifact_validator import (
    ArtifactValidationReport,
    GeneratedArtifactValidator,
)
from coding_agent.code_generator import ImplementationArtifact, UnitCodeGenerator
from coding_agent.engineering_spec import EngineeringSpecification
from coding_agent.implementation_graph import ImplementationGraphBuilder
from coding_agent.models import ChangeType, FileChange, ImplementationPlan
from coding_agent.workspace import Workspace


class ArtifactPipelineError(RuntimeError):
    """Raised when artifact preparation cannot safely proceed."""


@dataclass(frozen=True)
class PreparedArtifactBatch:
    """Generated artifacts and their validation result before user approval."""

    task_id: str
    artifacts: tuple[ImplementationArtifact, ...]
    validation: ArtifactValidationReport
    plan: ImplementationPlan | None
    policy_accepted: bool = False

    @property
    def ready_for_approval(self) -> bool:
        return self.policy_accepted and self.plan is not None


class ArtifactGenerationPipeline:
    """Generate ESpec units in dependency order, validate, then prepare a plan."""

    def __init__(
        self,
        generator: UnitCodeGenerator,
        workspace: Workspace,
        *,
        validator: GeneratedArtifactValidator | None = None,
        graph_builder: ImplementationGraphBuilder | None = None,
    ) -> None:
        if not callable(getattr(generator, "generate", None)):
            raise TypeError("generator must provide generate(specification, unit_id, ...)")
        self.generator = generator
        self.workspace = workspace
        self.validator = validator or GeneratedArtifactValidator()
        self.graph_builder = graph_builder or ImplementationGraphBuilder()

    def prepare(
        self,
        specification: EngineeringSpecification,
        *,
        project_files: Mapping[str, str] | None = None,
        verification_commands: Sequence[Sequence[str]] = (),
        allow_unverified: bool = False,
    ) -> PreparedArtifactBatch:
        """Generate and validate all units without modifying the workspace.

        By default, both FAILED and UNVERIFIED checks block approval. Setting
        ``allow_unverified`` is an explicit caller policy decision; failed
        checks always block.
        """
        requested_root = Path(specification.workspace).expanduser().resolve(strict=True)
        authorized_root = self.workspace.root.resolve()
        if requested_root != authorized_root:
            raise ArtifactPipelineError(
                "Specification workspace does not match the authorized workspace"
            )

        graph = self.graph_builder.build(specification)
        units = {unit.unit_id: unit for unit in specification.implementation_units}
        inventory = dict(project_files or {})
        generated: dict[str, ImplementationArtifact] = {}

        for unit_id in graph.ordered_unit_ids:
            unit = units[unit_id]
            dependencies = {
                dependency: generated[dependency].content
                for dependency in unit.depends_on
                if dependency in generated
            }
            artifact = self.generator.generate(
                specification,
                unit_id,
                dependency_artifacts=dependencies,
                project_files=inventory,
            )
            if not isinstance(artifact, ImplementationArtifact):
                raise ArtifactPipelineError(
                    f"Generator returned an invalid artifact for unit {unit_id!r}"
                )
            generated[unit_id] = artifact

        artifacts = tuple(generated[unit_id] for unit_id in graph.ordered_unit_ids)
        validation_inventory = dict(inventory)
        for artifact in artifacts:
            validation_inventory.setdefault(artifact.path, artifact.content)
        report = self.validator.validate(
            specification,
            artifacts,
            project_files=validation_inventory,
        )

        accepted = not report.failed and (allow_unverified or not report.unverified)
        if not accepted:
            return PreparedArtifactBatch(
                task_id=specification.task_id,
                artifacts=artifacts,
                validation=report,
                plan=None,
                policy_accepted=False,
            )

        changes = []
        for artifact in artifacts:
            try:
                target = self.workspace.resolve_path(artifact.path)
                exists = target.is_file()
                if target.exists() and not exists:
                    raise ArtifactPipelineError(
                        f"Artifact target is not a regular file: {artifact.path}"
                    )
            except FileNotFoundError:
                exists = False
            except Exception as exc:
                if isinstance(exc, ArtifactPipelineError):
                    raise
                # A missing target is a valid create; authorization/path errors
                # must not be converted into creates.
                if not self._is_missing_target_error(exc):
                    raise ArtifactPipelineError(
                        f"Cannot resolve artifact target {artifact.path!r}: {exc}"
                    ) from exc
                try:
                    self.workspace.resolve_create_path(artifact.path)
                except Exception as create_exc:
                    raise ArtifactPipelineError(
                        f"Cannot safely create artifact target {artifact.path!r}: {create_exc}"
                    ) from create_exc
                exists = False

            changes.append(FileChange(
                path=artifact.path,
                change_type=ChangeType.MODIFY if exists else ChangeType.CREATE,
                reason=f"Implement ESpec unit {artifact.unit_id}",
                content=artifact.content,
            ))

        plan = ImplementationPlan(
            task_id=specification.task_id,
            summary=f"Implement {len(artifacts)} ESpec unit(s): {specification.objective}",
            changes=tuple(changes),
            verification_commands=tuple(tuple(command) for command in verification_commands),
            requires_approval=True,
            risks=(("Generated artifacts passed all checks." if not report.unverified else
                    "One or more checks remain unverified under the explicitly selected permissive policy."),),
        )
        return PreparedArtifactBatch(
            task_id=specification.task_id,
            artifacts=artifacts,
            validation=report,
            plan=plan,
            policy_accepted=True,
        )

    @staticmethod
    def _is_missing_target_error(exc: Exception) -> bool:
        message = str(exc).casefold()
        return isinstance(exc, FileNotFoundError) or "does not resolve to an existing target" in message or "target does not exist" in message
