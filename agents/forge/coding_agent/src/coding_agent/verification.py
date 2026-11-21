
"""Evaluate command execution results against required verification checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from coding_agent.models import CommandResult


@dataclass(frozen=True)
class VerificationIssue:
    """A specific problem encountered during verification."""

    code: str
    message: str
    command: tuple[str, ...] | None = None
    index: int | None = None


@dataclass(frozen=True)
class VerificationReport:
    """Immutable report describing the outcome of verification."""

    required_commands: tuple[tuple[str, ...], ...]
    results: tuple[CommandResult, ...]
    issues: tuple[VerificationIssue, ...]

    @property
    def passed(self) -> bool:
        """Return True only when every required check passed."""

        return (
            bool(self.required_commands)
            and len(self.results) == len(self.required_commands)
            and not self.issues
            and all(result.succeeded for result in self.results)
        )

    @property
    def successful_results(self) -> tuple[CommandResult, ...]:
        """Return command results that completed successfully."""

        return tuple(
            result for result in self.results if result.succeeded
        )

    @property
    def failed_results(self) -> tuple[CommandResult, ...]:
        """Return command results that did not complete successfully."""

        return tuple(
            result for result in self.results if not result.succeeded
        )

    @property
    def summary(self) -> str:
        """Return a concise human-readable verification summary."""

        if self.passed:
            return (
                f"Verification passed: "
                f"{len(self.required_commands)} of "
                f"{len(self.required_commands)} checks succeeded."
            )

        succeeded = len(self.successful_results)
        total = len(self.required_commands)

        return (
            f"Verification failed: {succeeded} of "
            f"{total} required checks succeeded; "
            f"{len(self.issues)} issue(s) reported."
        )


class VerificationError(ValueError):
    """Raised when verification input is structurally invalid."""


class VerificationEngine:
    """Evaluate command results without executing or authorizing commands."""


    @staticmethod
    def _normalize_required_commands(
        commands: Sequence[Sequence[str]],
    ) -> tuple[tuple[str, ...], ...]:
        """Validate and normalize the required command list."""

        if isinstance(commands, (str, bytes)) or commands is None:
            raise VerificationError(
                "Required commands must be a sequence of command sequences"
            )

        normalized_commands = []

        try:
            for index, command in enumerate(commands):
                if isinstance(command, (str, bytes)) or command is None:
                    raise VerificationError(
                        f"Required command at index {index} "
                        "must be a sequence of argument strings"
                    )

                normalized_commands.append(tuple(command))

        except TypeError as exc:
            raise VerificationError(
                "Each required command must be an iterable of arguments"
            ) from exc

        normalized = tuple(normalized_commands)

        for index, command in enumerate(normalized):
            if not command:
                raise VerificationError(
                    f"Required command at index {index} must not be empty"
                )

            if any(
                not isinstance(part, str) or not part.strip()
                for part in command
            ):
                raise VerificationError(
                    f"Required command at index {index} "
                    "contains an invalid argument"
                )

        return normalized


    @staticmethod
    def _normalize_results(
        results: Sequence[CommandResult],
    ) -> tuple[CommandResult, ...]:
        """Validate and normalize command results."""

        if isinstance(results, (str, bytes)) or results is None:
            raise VerificationError(
                "Results must be a sequence of CommandResult objects"
            )

        try:
            normalized = tuple(results)
        except TypeError as exc:
            raise VerificationError(
                "Results must be iterable"
            ) from exc

        if any(not isinstance(result, CommandResult) for result in normalized):
            raise VerificationError(
                "Every result must be a CommandResult instance"
            )

        return normalized

    def evaluate(
        self,
        required_commands: Sequence[Sequence[str]],
        results: Sequence[CommandResult],
    ) -> VerificationReport:
        """Evaluate results against the complete required command list."""

        commands = self._normalize_required_commands(required_commands)
        observed = self._normalize_results(results)

        issues: list[VerificationIssue] = []

        if not commands:
            issues.append(
                VerificationIssue(
                    code="no_required_commands",
                    message="No verification commands were specified.",
                )
            )

        shared_count = min(len(commands), len(observed))

        for index in range(shared_count):
            expected = commands[index]
            result = observed[index]
            actual = tuple(result.command)

            if actual != expected:
                issues.append(
                    VerificationIssue(
                        code="command_mismatch",
                        message=(
                            f"Result at index {index} does not match "
                            "the required command."
                        ),
                        command=expected,
                        index=index,
                    )
                )

            if not result.succeeded:
                if result.timed_out:
                    reason = "timed out"
                else:
                    reason = f"exited with code {result.exit_code}"

                issues.append(
                    VerificationIssue(
                        code="command_failed",
                        message=f"Verification command {reason}.",
                        command=expected,
                        index=index,
                    )
                )

        for index in range(shared_count, len(commands)):
            issues.append(
                VerificationIssue(
                    code="missing_result",
                    message=(
                        f"No execution result was provided for "
                        f"required command at index {index}."
                    ),
                    command=commands[index],
                    index=index,
                )
            )

        for index in range(shared_count, len(observed)):
            issues.append(
                VerificationIssue(
                    code="unexpected_result",
                    message=(
                        f"Unexpected command result at index {index}."
                    ),
                    command=tuple(observed[index].command),
                    index=index,
                )
            )

        return VerificationReport(
            required_commands=commands,
            results=observed,
            issues=tuple(issues),
        )
