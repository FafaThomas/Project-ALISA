"""Attach deterministic commands to supported commandless ESpec criteria."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import replace
from pathlib import Path

from coding_agent.engineering_spec import (
    EngineeringSpecification,
    RequirementKind,
    VerificationKind,
)


class SpecificationCheckPlanner:
    """Prepare executable checks without inventing tests or shell commands.

    Supported static checks are delegated to the read-only specification_checks
    module. Criteria outside its documented rule set become explicit manual
    attestations rather than commandless automated criteria.
    """

    _AUTOMATABLE = re.compile(
        r"file|directory|folder|layout|docker|compose|postgres|postgresql|sqlite|"
        r"\.env\.example|readme|project\.yaml|secret|credential|source control|"
        r"runtime configuration|environment setting",
        re.I,
    )

    def __init__(self, workspace: str | Path) -> None:
        self.root = Path(workspace).resolve()

    def prepare(self, specification: EngineeringSpecification) -> tuple[EngineeringSpecification, tuple[str, ...]]:
        requirements = {item.requirement_id: item for item in specification.requirements}
        units = {item.unit_id: item for item in specification.implementation_units}
        prepared = []
        notes = []
        for criterion in specification.verification:
            covered_requirement_ids = set()
            for reference in criterion.covers:
                if reference in requirements:
                    covered_requirement_ids.add(reference)
                elif reference in units:
                    covered_requirement_ids.update(units[reference].satisfies)
            test_requirements = [
                requirements[item] for item in sorted(covered_requirement_ids)
                if re.search(r"\b(?:add|create|write|implement)\b", requirements[item].description, re.I)
                and re.search(r"\btests?\b", requirements[item].description, re.I)
            ]
            test_units = [
                unit for unit in specification.implementation_units
                if unit.kind.value == "test"
                and Path(unit.target_path).suffix.lower() == ".py"
                and covered_requirement_ids.intersection(unit.satisfies)
            ]
            if test_requirements and test_units and (
                criterion.kind != VerificationKind.MANUAL or criterion.criterion_id.startswith("VER-AUTO-")
            ):
                prepared.append(replace(
                    criterion, kind=VerificationKind.BEHAVIORAL,
                    command=("python", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
                    description="Run the project's automated tests for the required test artifacts. " + criterion.description,
                ))
                notes.append(f"{criterion.criterion_id}: mapped explicit test-artifact requirement to pytest")
                continue
            generated_manual_fallback = (
                criterion.kind == VerificationKind.MANUAL
                and criterion.criterion_id.startswith("VER-AUTO-")
            )
            if (criterion.kind == VerificationKind.MANUAL and not generated_manual_fallback) or criterion.command:
                prepared.append(criterion)
                continue
            if criterion.kind not in {VerificationKind.STATIC, VerificationKind.STRUCTURAL} and not generated_manual_fallback:
                prepared.append(replace(
                    criterion, kind=VerificationKind.MANUAL, command=(),
                    description=criterion.description.rstrip() + (
                        "\nHuman evidence required: this criterion was classified as "
                        f"{criterion.kind.value} but no executable command was supplied. "
                        "Inspect the completed behavior and record the specific evidence."
                    ),
                ))
                notes.append(f"{criterion.criterion_id}: non-static criterion converted to manual evidence because no executable command was supplied")
                continue
            covered_units = []
            for reference in criterion.covers:
                if reference in units:
                    covered_units.append(units[reference])
            selected_requirements = [requirements[item] for item in sorted(covered_requirement_ids)]
            selected_units = {
                unit.unit_id: unit
                for unit in covered_units
            }
            for unit in specification.implementation_units:
                if covered_requirement_ids.intersection(unit.satisfies):
                    selected_units[unit.unit_id] = unit
            description_text = " ".join(
                [criterion.description]
                + [r.description + " " + " ".join(r.acceptance_criteria) for r in selected_requirements]
                + [unit.target_path + " " + " ".join(unit.acceptance_criteria) for unit in selected_units.values()]
            )
            can_check = bool(selected_requirements) and bool(self._AUTOMATABLE.search(description_text))
            # Process rules are not artifact assertions and must be attested by a human.
            if re.search(
                r"global working rules|report changed files|next work package|only relevant verification|human evidence|"
                r"source control|tracked files|work package completion status|paired work package",
                description_text,
                re.I,
            ):
                can_check = False
            if can_check:
                payload = {
                    "requirements": [
                        {"requirement_id": item.requirement_id,
                         "description": item.description,
                         "kind": item.kind.value,
                         "acceptance_criteria": list(item.acceptance_criteria)}
                        for item in selected_requirements
                    ],
                    "units": [
                        {"unit_id": item.unit_id, "target_path": item.target_path,
                         "acceptance_criteria": list(item.acceptance_criteria)}
                        for item in sorted(selected_units.values(), key=lambda item: item.unit_id)
                    ],
                }
                command = (
                    sys.executable, "-m", "coding_agent.specification_checks",
                    str(self.root), json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                )
                prepared.append(replace(
                    criterion,
                    kind=VerificationKind.STATIC if generated_manual_fallback else criterion.kind,
                    command=command,
                ))
                notes.append(f"{criterion.criterion_id}: deterministic artifact check prepared")
            else:
                manual_description = criterion.description.rstrip() + (
                    "\nHuman evidence required: inspect the completed workspace and record the "
                    "specific observation, command output, or review decision. Do not modify files "
                    "during this verification step."
                )
                prepared.append(replace(
                    criterion, kind=VerificationKind.MANUAL, command=(),
                    description=manual_description,
                ))
                notes.append(f"{criterion.criterion_id}: converted to manual evidence because no safe automated check is available")
        return replace(specification, verification=tuple(prepared)), tuple(notes)
