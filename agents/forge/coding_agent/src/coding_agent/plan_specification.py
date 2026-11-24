"""Deterministic validation of implementation plans against an Engineering Specification."""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import PurePosixPath

from coding_agent.engineering_spec import (
    EngineeringSpecification,
    RequirementKind,
)
from coding_agent.models import ImplementationPlan
from coding_agent.specification_checks import (
    compose_content_issues,
    env_example_content_issues,
    project_yaml_content_issues,
    readme_missing_topics,
)


def _path(value: str) -> str:
    return value.replace("\\\\", "/").replace("\\", "/").strip("/").casefold()


_FORBIDDEN_IMPORT_PHRASE = re.compile(r"\bnot\s+import\s+(?:the\s+)?([A-Za-z_][\w.]*)", re.IGNORECASE)
_IMPORT_STATEMENT = re.compile(r"(?m)^\s*import\s+([\w.,\s]+?)\s*$|^\s*from\s+([\w.]+)\s+import\b")


def _forbidden_import_roots(text: str) -> set[str]:
    """Top-level modules a requirement forbids, e.g. 'must not import Pygame' -> {'pygame'}."""
    return {match.group(1).split(".")[0].casefold() for match in _FORBIDDEN_IMPORT_PHRASE.finditer(text)}


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for match in _IMPORT_STATEMENT.finditer(source):
        if match.group(1):
            roots.update(part.strip().split(".")[0].casefold() for part in match.group(1).split(","))
        elif match.group(2):
            roots.add(match.group(2).split(".")[0].casefold())
    roots.discard("")
    return roots


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
            if change.change_type.value != "delete"
        }
        units_by_requirement: dict[str, list] = {}
        for unit in specification.implementation_units:
            for requirement_id in unit.satisfies:
                units_by_requirement.setdefault(requirement_id, []).append(unit)
        issues: list[str] = []
        for requirement in specification.requirements:
            units = units_by_requirement.get(requirement.requirement_id, [])
            # An unmapped constraint is a compliance boundary, not a planned
            # artifact obligation. Do not validate an absent planned file as
            # though the plan were required to create or modify it. If a
            # constraint is explicitly mapped to implementation units, its
            # content remains subject to the same semantic checks as any other
            # implementation requirement.
            if requirement.kind == RequirementKind.CONSTRAINT and not units:
                continue
            text = " ".join((requirement.description, *requirement.acceptance_criteria))
            paths = [unit.target_path for unit in units]
            lowered = text.casefold()
            forbidden_roots = _forbidden_import_roots(text)
            if forbidden_roots:
                for unit in units:
                    if PurePosixPath(unit.target_path).suffix.lower() != ".py":
                        continue
                    source = content_by_path.get(_path(unit.target_path), "")
                    for root in sorted(forbidden_roots & _imported_roots(source)):
                        issues.append(
                            f"{requirement.requirement_id}: {unit.target_path} imports "
                            f"'{root}', which this requirement forbids"
                        )
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
            if "readme" in lowered:
                target = next((path for path in paths if PurePosixPath(path).name.lower() == "readme.md"), "README.md")
                readme = content_by_path.get(_path(target), "")
                for label in readme_missing_topics(readme, text):
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
        if not specification.implementation_units:
            return PlanSpecificationReport(True, (), (), (), ())

        changed_paths = [_path(change.path) for change in plan.changes]
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

            # An unmapped constraint is a compliance boundary. It must be
            # respected and verified, but must not force an artificial file edit.
            if is_constraint and not mapped_units:
                continue

            required_requirements.append(requirement.requirement_id)
            if not mapped_units:
                missing_requirements.append(requirement.requirement_id)
                continue

            # All units mapped to a requirement must be implemented. Using
            # `any()` here would let one changed file mask omitted sibling units.
            if all(unit.unit_id in covered_unit_set for unit in mapped_units):
                covered_requirements.append(requirement.requirement_id)
            else:
                missing_requirements.append(requirement.requirement_id)

        undeclared = tuple(sorted(
            change.path for change in plan.changes
            if not any(_matches_scope(change.path, scope) for scope in declared_scopes)
        ))

        semantic_issues = self._semantic_content_issues(plan, specification)
        return PlanSpecificationReport(
            valid=not missing_requirements and not missing_units and not undeclared and not semantic_issues,
            covered_requirements=tuple(covered_requirements),
            missing_requirements=tuple(missing_requirements),
            undeclared_paths=undeclared,
            uncovered_plan_paths=(),
            covered_units=tuple(covered_unit_ids),
            missing_units=tuple(missing_units),
            required_requirements=tuple(required_requirements),
            semantic_issues=semantic_issues,
        )
