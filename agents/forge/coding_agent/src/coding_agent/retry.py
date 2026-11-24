"""Bounded recovery policy for coding-task attempts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
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
    original_task_description: str = ""
    original_constraints: tuple[str, ...] = ()
    original_acceptance_criteria: tuple[str, ...] = ()




@dataclass(frozen=True)
class RetryProgressObservation:
    """Whether a failure signature has already been observed."""

    fingerprint: str
    occurrence: int

    @property
    def repeated(self) -> bool:
        return self.occurrence > 1


class RetryProgressTracker:
    """Detect exact repeated failed plans without blocking changed repairs."""

    def __init__(self) -> None:
        self._occurrences: dict[str, int] = {}

    @staticmethod
    def _fingerprint_payload(result: ExecutionResult) -> dict:
        return {
            "task_id": result.task_id,
            "status": result.status.value,
            "errors": list(result.errors),
            "proposed_changes": [
                {
                    "path": change.path.replace("\\", "/"),
                    "change_type": change.change_type.value,
                    "reason": change.reason,
                    "content": change.content,
                }
                for change in result.proposed_changes
            ],
            "commands": [
                {
                    "command": list(item.command),
                    "exit_code": item.exit_code,
                    "stdout": item.stdout,
                    "stderr": item.stderr,
                    "timed_out": item.timed_out,
                }
                for item in result.command_results
            ],
        }

    def observe(self, result: ExecutionResult) -> RetryProgressObservation:
        """Record a result and return its stable repetition observation."""
        payload = self._fingerprint_payload(result)
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        occurrence = self._occurrences.get(fingerprint, 0) + 1
        self._occurrences[fingerprint] = occurrence
        return RetryProgressObservation(fingerprint=fingerprint, occurrence=occurrence)


class FailureClassifier:
    """Classify failures that a new implementation attempt can reasonably fix."""

    @staticmethod
    def classify(result: ExecutionResult, policy: RetryPolicy) -> RetryPhase | None:
        if result.status.value != "failed":
            return None

        if not result.command_results:
            planning_signals = (
                "planning failed",
                "context preparation",
                "espec",
                "missing implementation coverage",
                "malformed json",
                "planner could not produce",
                "architecture stage failed",
                "requirements stage failed",
                "integration stage failed",
            )
            if policy.retry_planning_failures and any(
                any(signal in error.casefold() for signal in planning_signals)
                for error in result.errors
            ):
                return "planning"
            return None

        if not policy.retry_verification_failures:
            return None

        # A timeout is an execution problem, not evidence that another
        # implementation is the right fix. A command that could not be launched
        # (for example, a tool that is not installed) is different: the planner
        # can stop using that tool, and the launch error is included in the
        # recovery feedback, so it is retried.
        if any(item.timed_out for item in result.command_results):
            return None

        if any(item.exit_code != 0 for item in result.command_results):
            return "verification"

        return None


_FAILURE_LINE_PATTERNS = (
    re.compile(r"^(?:FAILED|ERROR)\s+\S"),            # pytest short summary lines
    re.compile(r"^E\s{1,}\S"),                         # pytest assertion / exception detail
    re.compile(r"^\S+\.\w+:\d+:\s"),                   # file.py:12: AssertionError (location)
    re.compile(r"^_{3,}\s.+\s_{3,}$"),                 # ____ test_name ____ block headers
    re.compile(r"^(?:FAIL|ERROR):\s"),                 # unittest failure headers
    re.compile(r"^\w*(?:Error|Exception|Failure)\b"),  # bare exception lines
)
_MAX_DIGEST_LINES = 80
_MAX_DIGEST_CHARS = 5000


def _failure_lines(output: str) -> list[str]:
    """Lines of tool output that explain a failure, in order, without duplicates."""
    return list(dict.fromkeys(
        line.rstrip() for line in output.splitlines()
        if any(pattern.search(line) for pattern in _FAILURE_LINE_PATTERNS)
    ))


def failing_check_digest(result) -> list[str]:
    """Summarize only the checks that failed, keeping the lines that explain why.

    Test runners print their summary at the end of the output, so this reads
    the whole output rather than its first few thousand characters.
    """
    lines: list[str] = []
    # Several checks often run the same tests. Show each failure line once.
    shown: set[str] = set()
    for item in result.command_results:
        if item.exit_code == 0 and not item.timed_out:
            continue
        label = " ".join(item.command)
        if len(label) > 200:
            label = label[:200] + " ..."
        status = "timed out" if item.timed_out else (
            "could not start" if item.exit_code is None else f"exit code {item.exit_code}"
        )
        lines.append(f"FAILED CHECK: {label} ({status})")
        command = tuple(item.command)
        # "python -m pytest" has its own -m; the marker option comes after "pytest".
        pytest_index = next((i for i, part in enumerate(command) if part == "pytest"), -1)
        marker_positions = [
            i for i, part in enumerate(command)
            if part == "-m" and i > pytest_index and i + 1 < len(command)
        ]
        if item.exit_code == 5 and pytest_index >= 0 and marker_positions:
            marker = command[marker_positions[0] + 1]
            lines.append(
                f"No test was collected for marker expression '{marker}'. Add tests decorated with "
                f"@pytest.mark.<marker> (for example @pytest.mark.{marker.split(' or ')[0]}) that check "
                "this requirement's behavior."
            )
        output = "\n".join(part for part in (item.stdout, item.stderr) if part)
        selected = _failure_lines(output)
        new_lines = [line for line in selected if line not in shown]
        shown.update(selected)
        if selected and not new_lines:
            text = "(same failures as shown for an earlier check above)"
        elif new_lines:
            text = "\n".join(new_lines[:_MAX_DIGEST_LINES])
        else:
            # No recognizable failure lines: show the end of the output, where
            # most tools report the outcome.
            text = output.strip()[-2500:] or "(no output)"
        lines.append(text[:_MAX_DIGEST_CHARS])
        lines.append("")
    return lines


def build_recovery_context(request: RetryRequest) -> str:
    """Render bounded, explicit feedback for the next planner iteration.

    The failing check output comes first and the previous file contents come
    last, so that when the context is truncated it is the old file contents,
    not the reason for the failure, that is lost.
    """
    lines = [
        "\n\nRECOVERY FEEDBACK FROM A PREVIOUS ATTEMPT",
        f"Attempt: {request.attempt_number} of {request.max_attempts}",
        f"Failure phase: {request.phase}",
        f"Failure summary: {request.summary}",
        "Previous errors:",
    ]
    lines.extend(f"- {error}" for error in request.errors)

    digest = failing_check_digest(request.result)
    if digest:
        lines.extend((
            "",
            "FAILING CHECKS FROM THE PREVIOUS ATTEMPT (fix these first; each line is real tool output):",
            *digest,
            "Read each failure: an assertion shows expected vs. actual values, and a NameError or "
            "ImportError names something your code does not define or import. Fix the code, and fix a "
            "test only when it contradicts the requirement.",
        ))

    if request.original_task_description:
        lines.extend(("", "ORIGINAL TASK SCOPE:", request.original_task_description))
    if request.original_constraints:
        lines.append("Original constraints (must preserve):")
        lines.extend(f"- {item}" for item in request.original_constraints)
    if request.original_acceptance_criteria:
        lines.append("Original acceptance criteria (must preserve):")
        lines.extend(f"- {item}" for item in request.original_acceptance_criteria)

    report = request.result.specification_report or {}
    criteria = report.get("acceptance_criteria", []) if isinstance(report, dict) else []
    if criteria:
        lines.extend(("", "PREVIOUS ACCEPTANCE CRITERION STATUS:"))
        for criterion in criteria:
            if not isinstance(criterion, dict):
                continue
            criterion_id = criterion.get("criterion_id", "UNIDENTIFIED")
            status = criterion.get("status", "NOT VERIFIED")
            description = criterion.get("description", "")
            lines.append(f"- {criterion_id} [{status}] {description}".rstrip())
        lines.append("A previous PASS is historical evidence only; re-check it after changes.")
        lines.append("Preserve previously passing behavior while correcting failed criteria.")

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

    failed_results = [
        item for item in request.result.command_results
        if item.exit_code != 0 or item.timed_out
    ]
    if failed_results:
        lines.append("Full output of failed checks (end of output, where results are reported):")
        shown_failures: set[str] = set()
        for index, item in enumerate(failed_results, start=1):
            lines.append(f"Check {index}: {' '.join(item.command)[:300]}")
            lines.append(f"Exit code: {item.exit_code}; timed out: {item.timed_out}")
            failures = set(_failure_lines("\n".join(p for p in (item.stdout, item.stderr) if p)))
            if failures and failures <= shown_failures:
                lines.append("(output repeats failures already shown for an earlier check)")
                continue
            shown_failures.update(failures)
            if item.stdout:
                lines.append("stdout:")
                lines.append(item.stdout[-4000:])
            if item.stderr:
                lines.append("stderr:")
                lines.append(item.stderr[-2000:])

    lines.extend((
        "Analyze the diagnostics rather than repeating the previous approach.",
        "Propose a corrected plan for the original task. Do not claim that checks passed.",
    ))

    # Last, so truncation removes old file contents before any failure details.
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
    return "\n".join(lines)[:24000]
