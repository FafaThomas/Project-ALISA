"""Evaluate Engineering Specification criteria against observed command evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Any

from coding_agent.engineering_spec import EngineeringSpecification, VerificationKind
from coding_agent.models import CommandResult


class VerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    description: str
    kind: VerificationKind
    required: bool
    status: VerificationStatus
    command: tuple[str, ...] = ()
    evidence: CommandResult | None = None
    reason: str = ""
    human_evidence: str = ""


@dataclass(frozen=True)
class CoveredItemResult:
    item_id: str
    status: VerificationStatus
    criterion_ids: tuple[str, ...]


@dataclass(frozen=True)
class SpecificationVerificationReport:
    specification_id: str
    task_id: str
    criteria: tuple[CriterionResult, ...]
    requirements: tuple[CoveredItemResult, ...]
    implementation_units: tuple[CoveredItemResult, ...]

    @property
    def passed(self) -> bool:
        required = tuple(item for item in self.criteria if item.required)
        return bool(required) and all(item.status == VerificationStatus.PASS for item in required)

    @property
    def summary(self) -> str:
        passed = sum(item.status == VerificationStatus.PASS for item in self.criteria)
        failed = sum(item.status == VerificationStatus.FAIL for item in self.criteria)
        unverified = sum(item.status == VerificationStatus.UNVERIFIED for item in self.criteria)
        outcome = "passed" if self.passed else "not accepted"
        return (f"Specification verification {outcome}: {passed} PASS, "
                f"{failed} FAIL, {unverified} UNVERIFIED across {len(self.criteria)} criteria.")

    def to_dict(self) -> dict:
        def item_dict(item: CriterionResult) -> dict:
            evidence = item.evidence
            return {
                "criterion_id": item.criterion_id,
                "description": item.description,
                "kind": item.kind.value,
                "required": item.required,
                "status": item.status.value,
                "command": list(item.command),
                "reason": item.reason,
                "human_evidence": item.human_evidence,
                "evidence": None if evidence is None else {
                    "command": list(evidence.command),
                    "exit_code": evidence.exit_code,
                    "timed_out": evidence.timed_out,
                    "stdout": evidence.stdout,
                    "stderr": evidence.stderr,
                },
            }
        return {
            "specification_id": self.specification_id,
            "task_id": self.task_id,
            "passed": self.passed,
            "summary": self.summary,
            "criteria": [item_dict(item) for item in self.criteria],
            "requirements": [{"item_id": x.item_id, "status": x.status.value,
                              "criterion_ids": list(x.criterion_ids)} for x in self.requirements],
            "implementation_units": [{"item_id": x.item_id, "status": x.status.value,
                                       "criterion_ids": list(x.criterion_ids)} for x in self.implementation_units],
        }


class SpecificationVerificationError(ValueError):
    """Raised for invalid specification verification inputs."""


class SpecificationVerificationEngine:
    """Map criterion commands to exact execution evidence; never infer a pass."""

    @staticmethod
    def required_commands(specification: EngineeringSpecification) -> tuple[tuple[str, ...], ...]:
        return tuple(dict.fromkeys(
            criterion.command for criterion in specification.verification
            if criterion.kind != VerificationKind.MANUAL and criterion.command
        ))

    @staticmethod
    def _item_status(item_id: str, criteria: tuple[CriterionResult, ...],
                     coverage: dict[str, tuple[str, ...]]) -> CoveredItemResult:
        relevant = tuple(item for item in criteria
                         if item.required and item_id in coverage.get(item.criterion_id, ()))
        if not relevant:
            return CoveredItemResult(item_id, VerificationStatus.UNVERIFIED, ())
        if any(item.status == VerificationStatus.FAIL for item in relevant):
            status = VerificationStatus.FAIL
        elif all(item.status == VerificationStatus.PASS for item in relevant):
            status = VerificationStatus.PASS
        else:
            status = VerificationStatus.UNVERIFIED
        return CoveredItemResult(item_id, status, tuple(item.criterion_id for item in relevant))

    def evaluate(self, specification: EngineeringSpecification,
                 results: Iterable[CommandResult], *,
                 manual_evidence: Mapping[str, Any] | None = None) -> SpecificationVerificationReport:
        observed = tuple(results)
        if manual_evidence is None:
            manual_evidence = {}
        if not isinstance(manual_evidence, Mapping):
            raise SpecificationVerificationError("manual_evidence must be a mapping of criterion IDs to evidence records")
        manual_ids = {item.criterion_id for item in specification.verification
                      if item.kind == VerificationKind.MANUAL}
        unknown_evidence_ids = set(manual_evidence) - manual_ids
        if unknown_evidence_ids:
            raise SpecificationVerificationError(
                "manual_evidence contains unknown or non-manual criterion IDs: "
                + ", ".join(sorted(str(item) for item in unknown_evidence_ids))
            )
        if any(not isinstance(item, CommandResult) for item in observed):
            raise SpecificationVerificationError("results must contain only CommandResult objects")
        by_command: dict[tuple[str, ...], list[CommandResult]] = {}
        for result in observed:
            by_command.setdefault(tuple(result.command), []).append(result)

        criteria: list[CriterionResult] = []
        coverage: dict[str, tuple[str, ...]] = {}
        for criterion in specification.verification:
            evidence = None
            human_text = ""
            reason = ""
            if criterion.kind == VerificationKind.MANUAL:
                record = manual_evidence.get(criterion.criterion_id)
                if isinstance(record, Mapping):
                    decision = str(record.get("status", "")).strip().upper()
                    human_text = str(record.get("evidence", "")).strip()
                else:
                    decision = ""
                    human_text = ""
                if decision in {"PASS", "FAIL"} and human_text:
                    status = VerificationStatus.PASS if decision == "PASS" else VerificationStatus.FAIL
                    reason = "Human attestation recorded." if decision == "PASS" else "Human reviewer reported a failure."
                else:
                    status = VerificationStatus.UNVERIFIED
                    reason = "Manual verification requires an explicit PASS/FAIL decision and non-empty evidence."
            elif not criterion.command:
                status = VerificationStatus.UNVERIFIED
                reason = "No executable verification command is defined."
            else:
                matches = by_command.get(tuple(criterion.command), [])
                if not matches:
                    status = VerificationStatus.UNVERIFIED
                    reason = "No execution result matches the criterion command."
                else:
                    evidence = matches[-1]
                    status = VerificationStatus.PASS if evidence.succeeded else VerificationStatus.FAIL
                    reason = "Exact command completed successfully." if evidence.succeeded else (
                        "Command timed out." if evidence.timed_out else
                        f"Command exited with code {evidence.exit_code}.")
            item = CriterionResult(criterion.criterion_id, criterion.description,
                                   criterion.kind, criterion.required, status,
                                   tuple(criterion.command), evidence, reason, human_text)
            coverage[criterion.criterion_id] = tuple(criterion.covers)
            criteria.append(item)

        frozen = tuple(criteria)
        requirement_results = tuple(
            self._item_status(req.requirement_id, frozen, coverage)
            for req in specification.requirements
        )
        unit_results = tuple(
            self._item_status(unit.unit_id, frozen, coverage)
            for unit in specification.implementation_units
        )
        return SpecificationVerificationReport(
            specification.specification_id, specification.task_id,
            frozen, requirement_results, unit_results,
        )
