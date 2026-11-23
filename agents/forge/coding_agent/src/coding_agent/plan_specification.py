"""Deterministic validation of implementation plans against an Engineering Specification."""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path, PurePosixPath

from coding_agent.engineering_spec import (
    EngineeringSpecification,
    RequirementKind,
    SpecificationStatus,
)
from coding_agent.models import ImplementationPlan
from coding_agent.specification_checks import (
    compose_content_issues,
    env_example_content_issues,
    project_yaml_content_issues,
)


def _path(value: str) -> str:
    return value.replace("\\\\", "/").replace("\\", "/").strip("/").casefold()


def _is_directory_only_requirement(requirement) -> bool:
    """Identify directory-layout obligations that are verified structurally."""
    if requirement.kind == RequirementKind.CONSTRAINT:
        return False
    text = " ".join((requirement.description, *requirement.acceptance_criteria))
    if not re.search(
        r"\b(?:directory|directories|folder|folders|area|areas|layout|structure)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    if not re.search(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+)/", text):
        return False
    file_pattern = re.compile(
        r"(?<![A-Za-z0-9_./-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+"
        r"\.(?:yml|yaml|json|md|txt|toml|py|tsx?|jsx?|html|css|sql)"
        r"|(?<![A-Za-z0-9_.-])\.(?:dockerignore|gitignore|env(?:\.[A-Za-z0-9_-]+)?)",
        re.IGNORECASE,
    )
    if file_pattern.search(text):
        return False
    return True



def _explicit_directory_paths(text: str) -> tuple[str, ...]:
    """Extract explicit relative directory paths from a directory-layout obligation."""
    values = re.findall(
        r"(?<![A-Za-z0-9_.-])((?:[A-Za-z0-9_.-]+/)+)(?![A-Za-z0-9_.-])",
        text,
    )
    safe_values = []
    for value in values:
        candidate = value.rstrip("/")
        parts = candidate.split("/")
        if (
            not candidate
            or candidate.casefold() in {"root", "."}
            or any(part in {"", ".", ".."} for part in parts)
            or ":" in parts[0]
        ):
            continue
        safe_values.append(_path(candidate))
    return tuple(dict.fromkeys(safe_values))


def _matches_scope(candidate: str, declared_path: str) -> bool:
    """Match an exact declared file or a descendant of a declared directory scope."""
    candidate = _path(candidate)
    declared_path = _path(declared_path)
    # A workspace-root scope cannot be represented by a file change. ESpec
    # generation must decompose it into concrete artifact paths or verification.
    if declared_path in {"", "."}:
        return False
    return candidate == declared_path or candidate.startswith(declared_path.rstrip("/") + "/")


@dataclass(frozen=True)
class PlanSpecificationReport:
    valid: bool
    covered_requirements: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    undeclared_paths: tuple[str, ...]
    uncovered_plan_paths: tuple[str, ...]
    covered_units: tuple[str, ...] = ()
    missing_units: tuple[str, ...] = ()
    required_requirements: tuple[str, ...] = ()
    semantic_issues: tuple[str, ...] = ()
    covered_acceptance_criteria: tuple[str, ...] = ()
    missing_acceptance_criteria: tuple[str, ...] = ()
    missing_acceptance_verification: tuple[str, ...] = ()

    @property
    def issues(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.missing_requirements:
            values.append(
                "Plan does not cover implementation requirements: "
                + ", ".join(self.missing_requirements)
            )
        if self.missing_units:
            values.append(
                "Plan does not implement required ESpec implementation units: "
                + ", ".join(self.missing_units)
            )
        if self.undeclared_paths:
            values.append(
                "Plan changes paths not declared by the ESpec: "
                + ", ".join(self.undeclared_paths)
            )
        if self.missing_acceptance_criteria:
            values.append(
                "Plan does not implement required acceptance criteria: "
                + ", ".join(self.missing_acceptance_criteria)
            )
        if self.missing_acceptance_verification:
            values.append(
                "Engineering specification does not verify acceptance criteria: "
                + ", ".join(self.missing_acceptance_verification)
            )
        values.extend(self.semantic_issues)
        return tuple(values)


class PlanSpecificationValidator:
    """Require every implementation unit while treating unmapped constraints as boundaries.

    Functional and non-functional requirements are implementation obligations.
    Constraints are compliance boundaries by default, not automatic file-change
    obligations. A constraint explicitly mapped to implementation units is still
    enforced through those units.
    """

    @staticmethod
    def _semantic_content_issues(plan: ImplementationPlan, specification: EngineeringSpecification) -> tuple[str, ...]:
        """Reject a few high-confidence mismatches between acceptance text and proposed content."""
        content_by_path = {
            _path(change.path): (change.content or "")
            for change in plan.changes
            if change.change_type.value not in {"delete", "mkdir"}
        }
        units_by_requirement: dict[str, list] = {}
        for unit in specification.implementation_units:
            for requirement_id in unit.satisfies:
                units_by_requirement.setdefault(requirement_id, []).append(unit)
        issues: list[str] = []
        for requirement in specification.requirements:
            text = " ".join((requirement.description, *requirement.acceptance_criteria))
            units = units_by_requirement.get(requirement.requirement_id, [])
            paths = [unit.target_path for unit in units]
            lowered = text.casefold()
            compose_paths = [path for path in paths if PurePosixPath(path).name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}]
            if requirement.acceptance_criteria and compose_paths and re.search(r"docker|compose|postgres|postgresql|health|volume|credential", lowered, re.I):
                compose = next((content_by_path.get(_path(path), "") for path in compose_paths), "")
                if not compose:
                    issues.append(f"{requirement.requirement_id}: plan does not provide docker-compose.yml content")
                    continue
                for issue in compose_content_issues(compose, text):
                    issues.append(f"{requirement.requirement_id}: {issue}")
            if ".env.example" in lowered:
                target = next((path for path in paths if PurePosixPath(path).name == ".env.example"), ".env.example")
                env = content_by_path.get(_path(target), "")
                for issue in env_example_content_issues(env):
                    issues.append(f"{requirement.requirement_id}: {issue}")
            if "readme" in lowered and any(token in lowered for token in ("prerequisite", "environment setup", "compose workflow")):
                target = next((path for path in paths if PurePosixPath(path).name.lower() == "readme.md"), "README.md")
                readme = content_by_path.get(_path(target), "").casefold()
                groups = (("prerequisites", ("prerequisite", "requirements", "docker")),
                          ("environment setup", ("environment", ".env", "env.example")),
                          ("compose workflow", ("docker compose", "docker-compose", "compose up")))
                for label, tokens in groups:
                    if not any(token in readme for token in tokens):
                        issues.append(f"{requirement.requirement_id}: README does not document {label}")
            if "project.yaml" in lowered:
                target = next((path for path in paths if PurePosixPath(path).name == "project.yaml"), "project.yaml")
                project = content_by_path.get(_path(target), "")
                for issue in project_yaml_content_issues(project):
                    issues.append(f"{requirement.requirement_id}: {issue}")
            if ".dockerignore" in lowered:
                for path in paths:
                    if PurePosixPath(path).name != ".dockerignore":
                        continue
                    content = content_by_path.get(_path(path), "")
                    parent = PurePosixPath(path).parent.as_posix()
                    if parent != "." and f"!{parent}/" in content.replace("\\", "/"):
                        issues.append(f"{requirement.requirement_id}: {path} uses a build-context-inappropriate negation")
            if re.search(r"\b(?:add|create|write|implement)\b", text, re.I) and re.search(r"\btests?\b", text, re.I):
                test_units = [unit for unit in units if unit.kind.value == "test"]
                if not test_units:
                    issues.append(f"{requirement.requirement_id}: explicit test requirement has no test implementation unit")
                for unit in test_units:
                    source = content_by_path.get(_path(unit.target_path), "")
                    suffix = PurePosixPath(unit.target_path).suffix.lower()
                    if suffix == ".py":
                        has_test = bool(re.search(r"(?m)^\s*(?:def\s+test_|class\s+Test)", source))
                        has_assertion = bool(re.search(r"\bassert\b|\.assert[A-Za-z]*\s*\(", source))
                    elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
                        has_test = bool(re.search(r"\b(?:test|it)\s*\(", source))
                        has_assertion = bool(re.search(r"\bexpect\s*\(|\bassert\s*\(", source))
                    elif suffix == ".go":
                        has_test = bool(re.search(r"(?m)^func\s+Test[A-Z]", source))
                        has_assertion = bool(re.search(r"\bt\.(?:Error|Fatal|Fail|Run)", source))
                    elif suffix == ".rs":
                        has_test = "#[test]" in source
                        has_assertion = bool(re.search(r"\bassert!?\s*!?\s*\(", source))
                    elif suffix == ".java":
                        has_test = bool(re.search(r"@Test\b", source))
                        has_assertion = bool(re.search(r"\bassert(?:Equals|True|False|Throws)?\s*\(", source))
                    elif suffix in {".cs"}:
                        has_test = bool(re.search(r"\[(?:Fact|Test|TestMethod)\]", source))
                        has_assertion = bool(re.search(r"\bAssert\.", source))
                    else:
                        has_test = bool(re.search(r"test", source, re.I))
                        has_assertion = bool(re.search(r"assert|expect", source, re.I))
                    if not has_test or not has_assertion:
                        issues.append(f"{requirement.requirement_id}: {unit.target_path} must contain a recognizable test case and assertion")
        return tuple(dict.fromkeys(issues))

    def validate(
        self, plan: ImplementationPlan, specification: EngineeringSpecification
    ) -> PlanSpecificationReport:
        if plan.task_id != specification.task_id:
            raise ValueError("Implementation plan and ESpec task IDs do not match")

        # Older callers may supply a verification-only ESpec. Strict plan
        # coverage is enforceable only when implementation units are present.
        if not specification.implementation_units and not any(
            _is_directory_only_requirement(requirement)
            or (
                requirement.kind != RequirementKind.CONSTRAINT
                and bool(_explicit_directory_paths(
                    " ".join((requirement.description, *requirement.acceptance_criteria))
                ))
            )
            for requirement in specification.requirements
        ):
            return PlanSpecificationReport(True, (), (), (), ())

        changed_paths = [_path(change.path) for change in plan.changes]
        mkdir_paths = {
            _path(change.path)
            for change in plan.changes
            if change.change_type.value == "mkdir"
        }
        directory_requirement_paths = {
            path
            for requirement in specification.requirements
            if requirement.kind != RequirementKind.CONSTRAINT and re.search(
                r"\b(?:directory|directories|folder|folders|area|areas|layout|structure)\b",
                " ".join((requirement.description, *requirement.acceptance_criteria)),
                re.IGNORECASE,
            )
            for path in _explicit_directory_paths(" ".join((requirement.description, *requirement.acceptance_criteria)))
        }
        architecture_paths = {
            _path(path)
            for module in specification.architecture
            for path in module.file_paths
        }

        units_by_requirement: dict[str, list] = {}
        declared_scopes = set(architecture_paths)
        for unit in specification.implementation_units:
            scope = _path(unit.target_path)
            declared_scopes.add(scope)
            for requirement_id in unit.satisfies:
                units_by_requirement.setdefault(requirement_id, []).append(unit)

        # A plan must implement every declared unit, including multiple units
        # that contribute to one requirement (for example, one .dockerignore
        # file in each required service directory).
        covered_unit_ids: list[str] = []
        missing_units: list[str] = []
        for unit in specification.implementation_units:
            scope = _path(unit.target_path)
            if scope in {"", "."}:
                missing_units.append(
                    f"{unit.unit_id} -> {unit.target_path} "
                    "(workspace-root targets cannot be represented as file changes)"
                )
            elif scope in directory_requirement_paths:
                existing_directory = (
                    Path(specification.workspace) / scope
                )
                if scope in mkdir_paths or (
                    existing_directory.is_dir() and not existing_directory.is_symlink()
                ):
                    covered_unit_ids.append(unit.unit_id)
                else:
                    missing_units.append(f"{unit.unit_id} -> {unit.target_path} (requires an explicit mkdir operation)")
            elif any(_matches_scope(changed, scope) for changed in changed_paths):
                covered_unit_ids.append(unit.unit_id)
            else:
                missing_units.append(f"{unit.unit_id} -> {unit.target_path}")

        covered_unit_set = set(covered_unit_ids)
        covered_requirements: list[str] = []
        missing_requirements: list[str] = []
        required_requirements: list[str] = []

        for requirement in specification.requirements:
            mapped_units = units_by_requirement.get(requirement.requirement_id, [])
            is_constraint = requirement.kind == RequirementKind.CONSTRAINT

            # Unmapped constraints and directory-only requirements do not
            # require file edits. Directory existence must have a structural
            # verification criterion instead of incidental child-file coverage.
            if is_constraint and not mapped_units:
                continue

            required_requirements.append(requirement.requirement_id)
            text = " ".join((requirement.description, *requirement.acceptance_criteria))
            required_directories = set(_explicit_directory_paths(text))
            workspace_root = Path(specification.workspace)
            has_all_directories = bool(required_directories) and all(
                path in mkdir_paths
                or (
                    (workspace_root / path).is_dir()
                    and not (workspace_root / path).is_symlink()
                )
                for path in required_directories
            )
            if required_directories and not has_all_directories:
                missing_requirements.append(requirement.requirement_id)
                continue
            if _is_directory_only_requirement(requirement):
                has_structural_check = any(
                    criterion.required
                    and criterion.kind.value == "structural"
                    and requirement.requirement_id in criterion.covers
                    for criterion in specification.verification
                )
                if has_all_directories and has_structural_check:
                    covered_requirements.append(requirement.requirement_id)
                else:
                    missing_requirements.append(requirement.requirement_id)
                continue
            if not mapped_units:
                missing_requirements.append(requirement.requirement_id)
                continue

            # All units mapped to a requirement must be implemented. Using
            # `any()` here would let one changed file mask omitted sibling units.
            if all(unit.unit_id in covered_unit_set for unit in mapped_units):
                covered_requirements.append(requirement.requirement_id)
            else:
                missing_requirements.append(requirement.requirement_id)

        acceptance_criterion_units: dict[str, set[str]] = {}
        for unit in specification.implementation_units:
            for criterion_id in unit.implements_acceptance_criteria:
                acceptance_criterion_units.setdefault(criterion_id, set()).add(unit.unit_id)
        covered_acceptance_criteria = []
        missing_acceptance_criteria = []
        all_acceptance_ids = {
            criterion_id for requirement in specification.requirements
            for criterion_id in requirement.acceptance_criterion_ids
        }
        verified_acceptance_ids = {
            criterion_id for criterion in specification.verification
            if criterion.required
            for criterion_id in criterion.covers_acceptance_criteria
        }
        # Legacy ESpecs only mapped verification at the parent requirement
        # level. Keep those readable, while newly generated ESpecs persist
        # criterion-level references explicitly.
        for criterion in specification.verification:
            if not criterion.required or criterion.covers_acceptance_criteria:
                continue
            for requirement_id in criterion.covers:
                requirement = next((
                    item for item in specification.requirements
                    if item.requirement_id == requirement_id
                ), None)
                if requirement is not None:
                    verified_acceptance_ids.update(requirement.acceptance_criterion_ids)
        missing_acceptance_verification = (
            sorted(all_acceptance_ids - verified_acceptance_ids)
            if specification.status == SpecificationStatus.VALIDATED
            else []
        )
        for requirement in specification.requirements:
            if (
                requirement.kind == RequirementKind.CONSTRAINT
                or _is_directory_only_requirement(requirement)
            ):
                continue
            for criterion_id in requirement.acceptance_criterion_ids:
                mapped_units = acceptance_criterion_units.get(criterion_id, set())
                if not mapped_units:
                    # Backward compatibility for specifications created before
                    # criterion-level implementation mappings were introduced.
                    mapped_units = {
                        unit.unit_id for unit in units_by_requirement.get(
                            requirement.requirement_id, []
                        )
                    }
                if mapped_units and mapped_units.issubset(covered_unit_set):
                    covered_acceptance_criteria.append(criterion_id)
                else:
                    missing_acceptance_criteria.append(criterion_id)

        undeclared_values = []
        for change in plan.changes:
            path = _path(change.path)
            if change.change_type.value == "mkdir":
                declared_directory = (
                    path in directory_requirement_paths
                    or any(
                        architecture_path.startswith(path.rstrip("/") + "/")
                        for architecture_path in architecture_paths
                    )
                    or any(
                        _path(unit.target_path) == path
                        for unit in specification.implementation_units
                    )
                )
                if not declared_directory:
                    undeclared_values.append(change.path)
            elif not any(_matches_scope(path, scope) for scope in declared_scopes):
                undeclared_values.append(change.path)
        undeclared = tuple(sorted(undeclared_values))

        semantic_issues = self._semantic_content_issues(plan, specification)
        return PlanSpecificationReport(
            valid=(
                not missing_requirements
                and not missing_units
                and not missing_acceptance_criteria
                and not missing_acceptance_verification
                and not undeclared
                and not semantic_issues
            ),
            covered_requirements=tuple(covered_requirements),
            missing_requirements=tuple(missing_requirements),
            undeclared_paths=undeclared,
            uncovered_plan_paths=(),
            covered_units=tuple(covered_unit_ids),
            missing_units=tuple(missing_units),
            required_requirements=tuple(required_requirements),
            semantic_issues=semantic_issues,
            covered_acceptance_criteria=tuple(covered_acceptance_criteria),
            missing_acceptance_criteria=tuple(missing_acceptance_criteria),
            missing_acceptance_verification=tuple(missing_acceptance_verification),
        )
