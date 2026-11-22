"""Generate one implementation artifact from a validated ESpec unit.

Generation is deliberately side-effect free: this module never writes files,
executes commands, or changes the Engineering Specification.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from coding_agent.engineering_spec import EngineeringSpecification, ImplementationUnit
from coding_agent.generation_context import (
    DependencyArtifact,
    GenerationContext,
    GenerationContextBuilder,
)


class CodeGenerationError(RuntimeError):
    """Raised when a unit cannot produce a valid implementation artifact."""


@dataclass(frozen=True)
class ImplementationArtifact:
    """Unwritten source artifact returned by the generator."""

    unit_id: str
    path: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"unit_id": self.unit_id, "path": self.path, "content": self.content}


@dataclass(frozen=True)
class GenerationAttempt:
    attempt: int
    succeeded: bool
    diagnostic: str = ""


class UnitCodeGenerator:
    """Generate exactly the target artifact for one ESpec implementation unit."""

    def __init__(
        self,
        client: Any,
        *,
        context_builder: GenerationContextBuilder | None = None,
        max_attempts: int = 2,
    ) -> None:
        if not callable(getattr(client, "generate_json", None)):
            raise TypeError("client must provide generate_json(prompt)")
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        self.client = client
        self.context_builder = context_builder or GenerationContextBuilder()
        self.max_attempts = max_attempts
        self.last_trace: tuple[GenerationAttempt, ...] = ()

    def generate(
        self,
        specification: EngineeringSpecification,
        unit_id: str,
        *,
        dependency_artifacts: Mapping[str, str] | None = None,
        project_files: Mapping[str, str] | None = None,
    ) -> ImplementationArtifact:
        """Generate one artifact; never writes it to the target workspace."""
        context = self.context_builder.build(
            specification,
            unit_id,
            dependency_artifacts=dependency_artifacts,
            project_files=project_files,
        )
        unit = next(item for item in specification.implementation_units if item.unit_id == unit_id)
        prompt = self._prompt(context, unit)
        trace: list[GenerationAttempt] = []
        last_error = ""

        for attempt in range(1, self.max_attempts + 1):
            current_prompt = prompt
            if attempt > 1:
                current_prompt += (
                    "\n\nYour previous response was rejected for this reason:\n"
                    f"{last_error}\nCorrect the response. Return only the required JSON object."
                )
            try:
                response = self.client.generate_json(current_prompt)
                artifact = self._parse_response(response, unit)
                trace.append(GenerationAttempt(attempt, True))
                self.last_trace = tuple(trace)
                return artifact
            except Exception as exc:
                last_error = str(exc)
                trace.append(GenerationAttempt(attempt, False, last_error))

        self.last_trace = tuple(trace)
        raise CodeGenerationError(
            f"implementation unit {unit_id!r} failed after {self.max_attempts} attempt(s): {last_error}"
        )

    @staticmethod
    def _prompt(context: GenerationContext, unit: ImplementationUnit) -> str:
        return f"""You are ALISA Forge's unit-level code generator.
Generate the implementation for exactly one implementation unit.

Return one JSON object with this exact shape:
{{"artifacts":[{{"path":"{unit.target_path}","content":"complete source text"}}]}}

Rules:
- Return exactly one artifact and use exactly the requested target path.
- The content must be complete source text, not a diff, patch, explanation, or Markdown code fence.
- Follow the unit's requirements, interface contracts, dependencies, and acceptance criteria.
- Preserve compatibility with supplied project files and dependency artifacts.
- Do not invent additional files, dependencies, APIs, or requirements.
- Do not claim tests were run. Do not execute commands or modify files.
- Escape the source text correctly as a JSON string.

GENERATION CONTEXT:
{context.to_json()}
"""

    @staticmethod
    def _parse_response(response: Any, unit: ImplementationUnit) -> ImplementationArtifact:
        if not isinstance(response, Mapping):
            raise ValueError("model response must be a JSON object")
        if set(response.keys()) != {"artifacts"}:
            raise ValueError("response must contain only the 'artifacts' field")
        artifacts = response.get("artifacts")
        if not isinstance(artifacts, list) or len(artifacts) != 1:
            raise ValueError("response must contain exactly one artifact")
        item = artifacts[0]
        if not isinstance(item, Mapping) or set(item.keys()) != {"path", "content"}:
            raise ValueError("artifact must contain exactly 'path' and 'content'")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or path != unit.target_path:
            raise ValueError(
                f"artifact path must exactly match the authorized target {unit.target_path!r}"
            )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("artifact content must be a non-empty string")
        if content.lstrip().startswith("```"):
            raise ValueError("artifact content must be raw source text, not a Markdown code fence")
        return ImplementationArtifact(unit_id=unit.unit_id, path=path, content=content)
