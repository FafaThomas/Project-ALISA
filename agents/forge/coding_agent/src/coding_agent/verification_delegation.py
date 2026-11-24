"""Delegate infrastructure-specific verification to a specialist agent.

ALISA continues to run its ordinary structural and code-level checks. Checks
that require infrastructure tooling or infrastructure runtime judgment are
recorded in a machine-readable handoff instead of being executed or manually
attested by the coding agent.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
from typing import Iterable

from coding_agent.engineering_spec import (
    EngineeringSpecification,
    VerificationKind,
)


INFRASTRUCTURE_AGENT = "infrastructure_verification_agent"

_INFRASTRUCTURE_TERMS = re.compile(
    r"(?:\b(?:docker(?:[- ]compose)?|container(?:d)?|kubernetes|k8s|helm|"
    r"terraform|pulumi|ansible|cloudformation|postgres(?:ql)?|mysql|mariadb|"
    r"redis|infrastructure|deployment|health\s*checks?|healthchecks?|"
    r"service\s+startup|service\s+connectivity|network\s+connectivity|"
    r"port\s+mapping|persistent\s+volumes?|environment\s+configuration|"
    r"environment\s+variables?)\b|compose\s+(?:yaml|configuration|file)|\.dockerignore|\.env\.example)",
    re.IGNORECASE,
)
_INFRASTRUCTURE_COMMANDS = {
    "docker", "docker-compose", "kubectl", "helm", "terraform", "tofu",
    "pulumi", "ansible-playbook", "packer", "vagrant",
}

def is_infrastructure_command(command: Iterable[str]) -> bool:
    """Return whether a command directly invokes infrastructure tooling."""
    parts = tuple(str(part).strip().casefold() for part in command)
    if not parts:
        return False
    executable = Path(parts[0].replace("\\", "/")).name
    if executable.endswith((".exe", ".cmd", ".bat")):
        executable = executable.rsplit(".", 1)[0]
    return executable in _INFRASTRUCTURE_COMMANDS


def _criterion_context(specification: EngineeringSpecification, criterion) -> str:
    pieces = [criterion.description, " ".join(criterion.command)]
    requirement_ids = set(criterion.covers)
    unit_ids = set(criterion.covers)
    acceptance_ids = set(criterion.covers_acceptance_criteria)

    units = {unit.unit_id: unit for unit in specification.implementation_units}
    requirements = {item.requirement_id: item for item in specification.requirements}
    for unit_id in tuple(unit_ids):
        unit = units.get(unit_id)
        if unit is not None:
            pieces.extend((unit.title, unit.target_path))
            requirement_ids.update(unit.satisfies)
            acceptance_ids.update(unit.implements_acceptance_criteria)

    for requirement in specification.requirements:
        if acceptance_ids.intersection(requirement.acceptance_criterion_ids):
            requirement_ids.add(requirement.requirement_id)

    for requirement_id in requirement_ids:
        requirement = requirements.get(requirement_id)
        if requirement is not None:
            pieces.extend((requirement.description, *requirement.acceptance_criteria))

    for unit in specification.implementation_units:
        if unit.satisfies and requirement_ids.intersection(unit.satisfies):
            pieces.extend((unit.title, unit.target_path))

    return " ".join(str(piece) for piece in pieces if piece)


def classify_infrastructure_verification_criteria(
    specification: EngineeringSpecification,
) -> tuple[str, ...]:
    """Return required or optional ESpec checks owned by the infrastructure agent.

    Any criterion whose linked requirement or artifact context is
    infrastructure-related is delegated, including static configuration
    checks. Infrastructure verification belongs to the specialist regardless
    of whether the model labels the check static, structural, or runtime.
    """
    delegated = []
    for criterion in specification.verification:
        if is_infrastructure_command(criterion.command):
            delegated.append(criterion.criterion_id)
            continue
        if _INFRASTRUCTURE_TERMS.search(_criterion_context(specification, criterion)):
            delegated.append(criterion.criterion_id)
    return tuple(delegated)


def _safe_task_slug(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id).strip("._") or "task"


def build_infrastructure_handoff(
    specification: EngineeringSpecification,
    criterion_ids: Iterable[str],
) -> dict:
    """Build a versioned handoff payload without claiming verification passed."""
    selected = set(criterion_ids)
    criteria_by_id = {item.criterion_id: item for item in specification.verification}
    unknown = selected - set(criteria_by_id)
    if unknown:
        raise ValueError("Unknown infrastructure verification criteria: " + ", ".join(sorted(unknown)))

    requirements_by_id = {item.requirement_id: item for item in specification.requirements}
    units_by_id = {item.unit_id: item for item in specification.implementation_units}
    records = []
    for criterion in specification.verification:
        if criterion.criterion_id not in selected:
            continue
        requirement_ids = set(criterion.covers).intersection(requirements_by_id)
        unit_ids = set(criterion.covers).intersection(units_by_id)
        criterion_text = criterion.description.casefold()
        unit_ids.update(
            unit.unit_id for unit in specification.implementation_units
            if unit.target_path.casefold() in criterion_text
        )
        acceptance_ids = set(criterion.covers_acceptance_criteria)
        unit_ids.update(
            unit.unit_id for unit in specification.implementation_units
            if acceptance_ids.intersection(unit.implements_acceptance_criteria)
        )
        for unit_id in tuple(unit_ids):
            unit = units_by_id[unit_id]
            requirement_ids.update(unit.satisfies)
            acceptance_ids.update(unit.implements_acceptance_criteria)
        for requirement in specification.requirements:
            if acceptance_ids.intersection(requirement.acceptance_criterion_ids):
                requirement_ids.add(requirement.requirement_id)
        records.append({
            "criterion_id": criterion.criterion_id,
            "description": criterion.description,
            "kind": criterion.kind.value,
            "required": criterion.required,
            "proposed_command": list(criterion.command),
            "requirement_ids": sorted(requirement_ids),
            "acceptance_criterion_ids": sorted(acceptance_ids),
            "implementation_units": [
                {"unit_id": unit_id, "target_path": units_by_id[unit_id].target_path}
                for unit_id in sorted(unit_ids)
            ],
            "requested_evidence": [
                "Exact commands or environment actions performed",
                "Exit status and relevant stdout/stderr or diagnostic output",
                "Observed service/container health and connectivity where applicable",
                "PASS, FAIL, or BLOCKED decision with criterion-specific rationale",
            ],
        })

    return {
        "schema_version": 1,
        "handoff_type": "infrastructure_verification",
        "handoff_id": f"infra-verification-{specification.task_id}",
        "task_id": specification.task_id,
        "specification_id": specification.specification_id,
        "workspace": specification.workspace,
        "assigned_agent": INFRASTRUCTURE_AGENT,
        "status": "PENDING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result_contract": {
            "expected_path": (
                ".coding-agent/handoffs/"
                f"{_safe_task_slug(specification.task_id)}.infrastructure-verification.result.json"
            ),
            "required_top_level_fields": [
                "schema_version", "task_id", "specification_id", "agent", "status", "results"
            ],
            "result_fields": [
                "criterion_id", "status", "statement", "commands", "observed_at"
            ],
            "allowed_statuses": ["PASS", "FAIL", "BLOCKED", "NOT_VERIFIED"],
        },
        "policy": {
            "execution_owner": INFRASTRUCTURE_AGENT,
            "must_not_assume_pass": True,
            "may_modify_workspace": False,
            "must_return_evidence_for_each_criterion": True,
        },
        "criteria": records,
    }


def write_infrastructure_handoff(
    workspace_root: str | Path,
    specification: EngineeringSpecification,
    criterion_ids: Iterable[str],
) -> Path:
    """Atomically persist a specialist handoff inside the workspace metadata."""
    payload = build_infrastructure_handoff(specification, criterion_ids)
    handoff_dir = (Path(workspace_root).resolve() / ".coding-agent" / "handoffs").resolve()
    metadata_root = (Path(workspace_root).resolve() / ".coding-agent").resolve()
    if handoff_dir.parent != metadata_root:
        raise ValueError("Infrastructure handoff path escaped metadata directory")
    handoff_dir.mkdir(parents=True, exist_ok=True)
    safe_task_id = _safe_task_slug(specification.task_id)
    destination = handoff_dir / f"{safe_task_id}.infrastructure-verification.json"
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=handoff_dir, prefix=".handoff-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(serialized)
        handle.flush()
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
