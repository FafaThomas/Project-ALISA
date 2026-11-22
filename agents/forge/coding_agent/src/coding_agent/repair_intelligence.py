"""Evidence-grounded failure diagnosis and bounded repair strategy generation.

This module does not modify workspace files, execute commands, or authorize retries.
It converts observed task failures into structured guidance for the existing planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable

from coding_agent.models import ExecutionResult


class FailureCategory(str, Enum):
    PLANNING_FAILURE = "planning_failure"
    ENVIRONMENT_FAILURE = "environment_failure"
    SYNTAX_FAILURE = "syntax_failure"
    DEPENDENCY_FAILURE = "dependency_failure"
    ASSERTION_FAILURE = "assertion_failure"
    TEST_FAILURE = "test_failure"
    BUILD_FAILURE = "build_failure"
    UNKNOWN_FAILURE = "unknown_failure"


class FailurePhase(str, Enum):
    PLANNING = "planning"
    VERIFICATION = "verification"
    EXECUTION = "execution"


@dataclass(frozen=True)
class FailureDiagnosis:
    """A concise diagnosis that separates evidence from inferred causes."""

    phase: FailurePhase
    category: FailureCategory
    summary: str
    evidence: tuple[str, ...]
    likely_causes: tuple[str, ...]
    confidence: str
    actionable: bool

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("diagnosis summary must not be empty")
        if self.confidence not in {"high", "medium", "low"}:
            raise ValueError("confidence must be high, medium, or low")


@dataclass(frozen=True)
class RepairStrategy:
    """Focused planner guidance; never an executable operation."""

    objective: str
    instructions: tuple[str, ...]
    constraints: tuple[str, ...]
    affected_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("repair objective must not be empty")


class FailureDiagnoser:
    """Classify failures using observed diagnostics and conservative signatures."""

    _ENVIRONMENT_PATTERNS = (
        "connection refused",
        "failed to connect",
        "ollama is not running",
        "model is not available",
        "command not found",
        "not recognized as an internal or external command",
        "no such file or directory",
        "permission denied",
        "access is denied",
        "executable not found",
        "runner unavailable",
        "timed out",
        "timeout",
        "max retries exceeded",
        "name or service not known",
        "temporary failure in name resolution",
        "network is unreachable",
    )

    _SYNTAX_PATTERNS = (
        "syntaxerror",
        "unexpected token",
        "unexpected end of input",
        "parsing error",
        "failed to parse",
    )

    _DEPENDENCY_PATTERNS = (
        "modulenotfounderror",
        "no module named",
        "cannot find module",
        "cannot resolve module",
        "module not found",
        "package not found",
        "could not resolve",
    )

    _ASSERTION_PATTERNS = (
        "assertionerror",
        "assertion failed",
        "assert ",
    )

    _BUILD_PATTERNS = (
        "build failed",
        "failed to compile",
        "compilation failed",
        "tsc ",
        "vite build",
        "webpack",
    )

    @staticmethod
    def _bounded_evidence(result: ExecutionResult, limit: int = 12) -> tuple[str, ...]:
        evidence: list[str] = []
        for error in result.errors:
            value = str(error).strip()
            if value:
                evidence.append(value[:1200])
        for command in result.command_results:
            label = " ".join(command.command)[:300]
            evidence.append(
                f"Command: {label}; exit_code={command.exit_code}; "
                f"timed_out={command.timed_out}"
            )
            for stream_name, stream in (("stderr", command.stderr), ("stdout", command.stdout)):
                value = str(stream).strip()
                if value:
                    evidence.append(f"{stream_name}: {value[:1800]}")
        # Preserve order while deduplicating.
        return tuple(dict.fromkeys(evidence))[:limit]

    @staticmethod
    def _contains(text: str, patterns: Iterable[str]) -> bool:
        return any(pattern in text for pattern in patterns)

    def diagnose(self, result: ExecutionResult, phase: str) -> FailureDiagnosis:
        """Produce a diagnosis from an already failed attempt."""

        if phase not in {"planning", "verification"}:
            phase_value = FailurePhase.EXECUTION
        else:
            phase_value = FailurePhase(phase)

        evidence = self._bounded_evidence(result)
        combined = "\n".join(evidence).casefold()

        if any(item.timed_out or item.exit_code is None for item in result.command_results):
            category = FailureCategory.ENVIRONMENT_FAILURE
            causes = ("A verification command timed out or could not be launched.",)
            confidence, actionable = "high", False
        elif self._contains(combined, self._ENVIRONMENT_PATTERNS):
            category = FailureCategory.ENVIRONMENT_FAILURE
            causes = ("A required tool, service, permission, or execution environment may be unavailable.",)
            confidence, actionable = "high", False
        elif self._contains(combined, self._DEPENDENCY_PATTERNS):
            category = FailureCategory.DEPENDENCY_FAILURE
            causes = ("A required package or module could not be resolved in the project environment.",)
            confidence, actionable = "high", True
        elif self._contains(combined, self._SYNTAX_PATTERNS):
            category = FailureCategory.SYNTAX_FAILURE
            causes = ("Generated or modified source may contain a syntax or parsing error.",)
            confidence, actionable = "high", True
        elif self._contains(combined, self._ASSERTION_PATTERNS):
            category = FailureCategory.ASSERTION_FAILURE
            causes = ("Observed behavior did not satisfy an assertion or expected value.",)
            confidence, actionable = "medium", True
        elif self._contains(combined, self._BUILD_PATTERNS):
            category = FailureCategory.BUILD_FAILURE
            causes = ("The project build or compilation step rejected the current implementation.",)
            confidence, actionable = "medium", True
        elif phase_value == FailurePhase.PLANNING:
            category = FailureCategory.PLANNING_FAILURE
            causes = ("The implementation planner failed to produce a valid, usable plan.",)
            confidence, actionable = "medium", True
        elif result.command_results:
            category = FailureCategory.TEST_FAILURE
            causes = ("A project verification command returned a non-zero exit code; the underlying cause is not yet identified.",)
            confidence, actionable = "low", True
        else:
            category = FailureCategory.UNKNOWN_FAILURE
            causes = ("The failure does not match a known recoverable signature.",)
            confidence, actionable = "low", False

        summary = (
            f"{category.value.replace('_', ' ')} during {phase_value.value}; "
            f"{'repair may be attempted' if actionable else 'automatic repair is not recommended'}."
        )
        return FailureDiagnosis(
            phase=phase_value,
            category=category,
            summary=summary,
            evidence=evidence,
            likely_causes=causes,
            confidence=confidence,
            actionable=actionable,
        )


class RepairStrategyGenerator:
    """Turn a diagnosis into bounded, non-executable planner instructions."""

    def generate(
        self,
        diagnosis: FailureDiagnosis,
        result: ExecutionResult,
    ) -> RepairStrategy:
        paths = tuple(dict.fromkeys(
            change.path.replace("\\", "/")
            for change in result.proposed_changes
        ))

        common_constraints = (
            "Preserve the original task requirements and acceptance criteria.",
            "Do not modify unrelated files or weaken existing safety checks.",
            "Do not claim verification passed; propose a correction for the next attempt.",
            "Return a complete fresh implementation plan and keep human approval required.",
        )

        instructions_by_category = {
            FailureCategory.PLANNING_FAILURE: (
                "Re-read the workspace inventory and existing source context.",
                "Correct the specific plan-format, path, or contract issue indicated by the diagnostics.",
                "Do not repeat an invalid create/modify/delete classification.",
            ),
            FailureCategory.SYNTAX_FAILURE: (
                "Inspect the reported file and exact syntax location.",
                "Correct the syntax while preserving the intended behavior and public interfaces.",
                "Check nearby delimiters, indentation, imports, and language-specific syntax.",
            ),
            FailureCategory.DEPENDENCY_FAILURE: (
                "Trace the unresolved import to its intended package or local module.",
                "Determine whether the dependency is missing from the project manifest or the import path is incorrect.",
                "Prefer the smallest project-consistent correction; do not invent package versions.",
            ),
            FailureCategory.ASSERTION_FAILURE: (
                "Compare the failing assertion with the implementation and stated acceptance criteria.",
                "Correct the behavior or data contract rather than weakening or deleting the assertion.",
                "Preserve unrelated behavior and existing tests.",
            ),
            FailureCategory.TEST_FAILURE: (
                "Inspect the complete available test diagnostics and trace the failure to the affected implementation.",
                "Identify the violated behavior before changing code.",
                "Do not modify tests merely to make the current implementation pass unless the test itself contradicts the stated requirements.",
            ),
            FailureCategory.BUILD_FAILURE: (
                "Inspect the compiler or build diagnostic and locate the first actionable source error.",
                "Correct the implementation or configuration with the smallest necessary change.",
                "Do not replace or bypass the project's declared build tooling.",
            ),
        }

        instructions = instructions_by_category.get(
            diagnosis.category,
            ("Inspect the supplied evidence and relevant source context before proposing any correction.",),
        )
        return RepairStrategy(
            objective=f"Address the diagnosed {diagnosis.category.value.replace('_', ' ')}.",
            instructions=instructions,
            constraints=common_constraints,
            affected_paths=paths,
        )
