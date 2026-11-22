"""Bounded recovery policy for coding-task attempts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from coding_agent.models import ExecutionResult
from coding_agent.repair_intelligence import FailureDiagnosis, RepairStrategy


RetryPhase = Literal["planning", "verification"]


@dataclass(frozen=True)
class RetryPolicy:
    """Limits recovery attempts; the initial run counts as attempt one."""

    max_attempts: int = 3
    retry_planning_failures: bool = True
    retry_verification_failures: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")


@dataclass(frozen=True)
class RetryRequest:
    """A recoverable failure presented to the consuming application."""

    task_id: str
    attempt_number: int
    max_attempts: int
    phase: RetryPhase
    summary: str
    errors: tuple[str, ...]
    result: ExecutionResult
    diagnosis: FailureDiagnosis | None = None
    repair_strategy: RepairStrategy | None = None


class FailureClassifier:
    """Classify failures that a new implementation attempt can reasonably fix."""

    @staticmethod
    def classify(result: ExecutionResult, policy: RetryPolicy) -> RetryPhase | None:
        if result.status.value != "failed":
            return None

        if not result.command_results:
            if (
                policy.retry_planning_failures
                and any(
                    "planning failed" in error.casefold()
                    or "context preparation" in error.casefold()
                    for error in result.errors
                )
            ):
                return "planning"
            return None

        if not policy.retry_verification_failures:
            return None

        # A timeout or inability to launch a command is an environment/execution
        # problem, not evidence that another implementation is the right fix.
        if any(
            item.timed_out or item.exit_code is None
            for item in result.command_results
        ):
            return None

        if any(item.exit_code != 0 for item in result.command_results):
            return "verification"

        return None


def build_recovery_context(request: RetryRequest) -> str:
    """Render bounded, explicit feedback for the next planner iteration."""
    lines = [
        "\n\nRECOVERY FEEDBACK FROM A PREVIOUS ATTEMPT",
        f"Attempt: {request.attempt_number} of {request.max_attempts}",
        f"Failure phase: {request.phase}",
        f"Failure summary: {request.summary}",
        "Previous errors:",
    ]
    lines.extend(f"- {error}" for error in request.errors)

    if request.diagnosis is not None:
        diagnosis = request.diagnosis
        lines.extend((
            "",
            "STRUCTURED FAILURE DIAGNOSIS:",
            f"Phase: {diagnosis.phase.value}",
            f"Category: {diagnosis.category.value}",
            f"Summary: {diagnosis.summary}",
            f"Confidence: {diagnosis.confidence}",
            "Likely causes (hypotheses, not confirmed facts):",
        ))
        lines.extend(f"- {item}" for item in diagnosis.likely_causes)
        lines.append("Observed evidence:")
        lines.extend(f"- {item}" for item in diagnosis.evidence)

    if request.repair_strategy is not None:
        strategy = request.repair_strategy
        lines.extend(("", "FOCUSED REPAIR STRATEGY:", strategy.objective, "Instructions:"))
        lines.extend(f"- {item}" for item in strategy.instructions)
        lines.append("Constraints:")
        lines.extend(f"- {item}" for item in strategy.constraints)
        if strategy.affected_paths:
            lines.append("Previously affected paths:")
            lines.extend(f"- {item}" for item in strategy.affected_paths)

    if request.result.plan_summary:
        lines.extend(("Previous plan:", request.result.plan_summary))

    if request.result.proposed_changes:
        lines.append("Previous proposed changes (already rolled back):")
        for change in request.result.proposed_changes:
            lines.extend((
                f"Path: {change.path}",
                f"Operation: {change.change_type.value}",
                f"Reason: {change.reason}",
                "Previous content:",
                (change.content or "<deleted>")[:6000],
            ))

    if request.result.command_results:
        lines.append("Verification diagnostics:")
        for index, item in enumerate(request.result.command_results, start=1):
            lines.append(f"Check {index}: {' '.join(item.command)}")
            lines.append(f"Exit code: {item.exit_code}; timed out: {item.timed_out}")
            if item.stdout:
                lines.append("stdout:")
                lines.append(item.stdout[:6000])
            if item.stderr:
                lines.append("stderr:")
                lines.append(item.stderr[:6000])

    lines.extend((
        "Analyze the diagnostics rather than repeating the previous approach.",
        "Propose a corrected plan for the original task. Do not claim that checks passed.",
    ))
    return "\n".join(lines)[:24000]
