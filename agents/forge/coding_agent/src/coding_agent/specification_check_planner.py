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


# Wording that asks for a human opinion rather than an observable result.
# "Clear message" and similar are results a test can check, so they are not listed.
_HUMAN_JUDGMENT = re.compile(
    r"\b(?:readab\w*|visual\w*|aesthetic\w*|appearance|looks?|appears?|"
    r"user[- ]experience|usab\w*|human|person|reviewer|judg\w*|pleasant\w*)\b",
    re.IGNORECASE,
)


def _program_name(program: str) -> str:
    name = Path(str(program).replace("\\", "/")).name.casefold()
    for suffix in (".exe", ".cmd", ".bat"):
        name = name.removesuffix(suffix)
    return name


def _is_python_executable(program: str) -> bool:
    """True for python/py interpreters, including full paths such as sys.executable."""
    name = _program_name(program)
    return name in {"python", "python3", "py"} or (
        name.startswith("python") and name.removeprefix("python").replace(".", "").isdigit()
    )


# Toolchains allowed when the project contains their marker file. Python is
# always allowed: the agent's own artifact and syntax checks run on it.
_TOOLCHAIN_MARKERS = {
    "package.json": {"npm", "npx", "node", "pnpm", "yarn"},
    "go.mod": {"go"},
    "cargo.toml": {"cargo"},
    "pom.xml": {"mvn"},
    "build.gradle": {"gradle"},
    "build.gradle.kts": {"gradle"},
    "gemfile": {"bundle", "ruby", "rake"},
    "composer.json": {"composer", "php"},
}


def project_toolchain(project_paths) -> set[str]:
    """Programs a verification command may run, given the project's file paths."""
    allowed = {"pytest"}
    for path in project_paths:
        name = Path(str(path).replace("\\", "/")).name.casefold()
        allowed |= _TOOLCHAIN_MARKERS.get(name, set())
        if name.endswith((".csproj", ".sln", ".fsproj")):
            allowed.add("dotnet")
    return allowed


def command_rejection(command, allowed_programs) -> str | None:
    """Why a model-proposed command must not run, or None when it may.

    Proposed commands run unchecked, so they are limited to the project's
    toolchain, and a command that joins several commands into one argv (a
    second interpreter or the same program again among its arguments) is
    rejected, since it fails for reasons unrelated to the code.
    """
    if not command:
        return None
    program = _program_name(command[0])
    if not (_is_python_executable(command[0]) or program in allowed_programs):
        return f"program {command[0]!r} is not part of the project's toolchain"
    for part in command[1:]:
        if _is_python_executable(part) or _program_name(part) == program:
            return "it joins several commands into one"
    return None


def requirement_marker(requirement_id: str) -> str:
    """Pytest marker name for a requirement ID, e.g. REQ-002 -> req_002."""
    return re.sub(r"[^0-9a-z]+", "_", requirement_id.casefold()).strip("_")


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
        python_test_paths = tuple(sorted({
            unit.target_path for unit in specification.implementation_units
            if unit.kind.value == "test" and Path(unit.target_path).suffix.lower() == ".py"
        }))
        try:
            existing_files = [item.name for item in self.root.iterdir() if item.is_file()]
        except OSError:
            existing_files = []
        allowed_programs = project_toolchain(
            [unit.target_path for unit in specification.implementation_units] + existing_files
        )
        documentation_requirement_ids = {
            requirement_id
            for unit in specification.implementation_units
            if unit.kind.value == "documentation"
            for requirement_id in unit.satisfies
        }
        prepared = []
        notes = []
        for criterion in specification.verification:
            # Commands proposed by the model run unchecked, so only the project's
            # own toolchain may run (see command_rejection); a dropped command's
            # criterion is then checked by tests, an artifact check, or a human.
            rejection = command_rejection(criterion.command, allowed_programs)
            if rejection:
                notes.append(f"{criterion.criterion_id}: dropped proposed command because {rejection}")
                criterion = replace(criterion, command=())
            covered_requirement_ids = set()
            for reference in criterion.covers:
                if reference in requirements:
                    covered_requirement_ids.add(reference)
                elif reference in units:
                    covered_requirement_ids.update(units[reference].satisfies)
            # A manual review of documentation is accepted when the document exists
            # and is not empty (plus any topics the spec explicitly requires), so it
            # goes to the artifact check instead of waiting for a human.
            documentation_review = (
                criterion.kind == VerificationKind.MANUAL
                and bool(covered_requirement_ids)
                and all(
                    item in documentation_requirement_ids
                    or re.search(r"\breadme\b", requirements[item].description, re.I)
                    for item in covered_requirement_ids
                )
            )
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
            # A behavioral, runtime, or build criterion without a command is
            # checked by running the tests tagged for its requirements. With no
            # tagged test, pytest collects nothing (exit 5), so it fails rather
            # than passing on unrelated tests.
            behavior_requirement_ids = sorted(
                item for item in covered_requirement_ids
                if requirements[item].kind != RequirementKind.CONSTRAINT
            )
            # A manual criterion about program behavior (output, exit status, a
            # result) is checkable by a test, so it is verified by the tagged
            # tests too. Only criteria that need human judgment stay manual.
            judgment_only = bool(_HUMAN_JUDGMENT.search(criterion.description))
            checkable_manual = criterion.kind == VerificationKind.MANUAL and not judgment_only
            # A requirement about a file or document (README, layout, config) is an
            # artifact check, not a behavior test; no test would ever be tagged for it.
            artifact_requirement = any(
                self._AUTOMATABLE.search(
                    " ".join((requirements[item].description, *requirements[item].acceptance_criteria))
                )
                for item in behavior_requirement_ids
            )
            if (
                not criterion.command
                and python_test_paths
                and behavior_requirement_ids
                and not artifact_requirement
                and (
                    criterion.kind in {VerificationKind.BEHAVIORAL, VerificationKind.RUNTIME, VerificationKind.BUILD}
                    or checkable_manual
                )
            ):
                markers = " or ".join(requirement_marker(item) for item in behavior_requirement_ids)
                prepared.append(replace(
                    criterion,
                    kind=VerificationKind.BEHAVIORAL,
                    command=("python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", markers, *python_test_paths),
                    description=(
                        f"Run the tests tagged for {', '.join(behavior_requirement_ids)} "
                        f"(pytest -m \"{markers}\"). " + criterion.description
                    ),
                ))
                notes.append(f"{criterion.criterion_id}: mapped to requirement-tagged tests ({markers})")
                continue
            if (
                criterion.kind == VerificationKind.MANUAL
                and not generated_manual_fallback
                and not documentation_review
            ) or criterion.command:
                prepared.append(criterion)
                continue
            if (
                criterion.kind not in {VerificationKind.STATIC, VerificationKind.STRUCTURAL}
                and not generated_manual_fallback
                and not documentation_review
            ):
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
                    kind=(
                        VerificationKind.STATIC
                        if generated_manual_fallback or documentation_review
                        else criterion.kind
                    ),
                    command=command,
                ))
                notes.append(
                    f"{criterion.criterion_id}: "
                    + ("documentation review accepted as an artifact check (document exists and is not empty)"
                       if documentation_review else "deterministic artifact check prepared")
                )
            elif generated_manual_fallback and python_test_paths and behavior_requirement_ids and not artifact_requirement:
                # A gap-fill checklist exists only because the model supplied no
                # check. With no artifact check available, require tagged tests
                # for the requirement before falling back to a human checklist.
                markers = " or ".join(requirement_marker(item) for item in behavior_requirement_ids)
                prepared.append(replace(
                    criterion,
                    kind=VerificationKind.BEHAVIORAL,
                    command=("python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", markers, *python_test_paths),
                    description=(
                        f"Run the tests tagged for {', '.join(behavior_requirement_ids)} "
                        f"(pytest -m \"{markers}\"). " + criterion.description
                    ),
                ))
                notes.append(f"{criterion.criterion_id}: gap-fill mapped to requirement-tagged tests ({markers})")
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

        # A criterion with nothing to execute is a human review item when it
        # covers only constraints, or when it asks for a judgment (readability,
        # maintainability) about non-functional requirements. When executable
        # required checks exist, they decide acceptance and the review is
        # reported without blocking. When none exist, the review is the only
        # gate, so it stays required. Functional behavior is never made optional.
        has_executable_gate = any(item.command and item.required for item in prepared)
        for index, criterion in enumerate(prepared):
            if criterion.command:
                continue
            covered = set()
            for reference in criterion.covers:
                if reference in requirements:
                    covered.add(reference)
                elif reference in units:
                    covered.update(units[reference].satisfies)
            if not covered:
                continue
            constraint_only = all(requirements[item].kind == RequirementKind.CONSTRAINT for item in covered)
            judgment_review = (
                criterion.kind == VerificationKind.MANUAL
                and bool(_HUMAN_JUDGMENT.search(criterion.description))
                and all(
                    requirements[item].kind in {RequirementKind.CONSTRAINT, RequirementKind.NON_FUNCTIONAL}
                    for item in covered
                )
            )
            if not (constraint_only or judgment_review):
                continue
            should_require = not has_executable_gate
            if criterion.required == should_require:
                continue
            prepared[index] = replace(
                criterion,
                required=should_require,
                description=(
                    criterion.description.replace("(non-blocking constraint)", "(constraint)")
                    if should_require else criterion.description
                ),
            )
            notes.append(
                f"{criterion.criterion_id}: "
                + ("constraint-only review " if constraint_only else "judgment review of non-functional requirements ")
                + ("is required because no executable check decides acceptance"
                   if should_require else "is non-blocking; executable checks decide acceptance")
            )
        return replace(specification, verification=tuple(prepared)), tuple(notes)
