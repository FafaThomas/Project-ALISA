"""Deterministic quality checks for generated architecture designs.

The architecture model proposes the design; this module checks a small set of
high-confidence structural properties before implementation planning proceeds.
It deliberately avoids trying to infer a complete architecture from filenames.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Iterable

from coding_agent.engineering_spec import ArchitectureModule, Requirement


@dataclass(frozen=True)
class ArchitectureValidationReport:
    """Result of deterministic architecture-design checks."""

    valid: bool
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"valid": self.valid, "issues": list(self.issues)}


class ArchitectureDesignValidator:
    """Reject obvious responsibility collisions and ambiguous file ownership.

    These checks are intentionally conservative. They do not enforce a fixed
    number of modules or a particular directory layout. They only reject
    duplicated file ownership, explicitly requested separation that the design
    collapses into one module, and clearly omnibus module responsibilities.
    """

    _SEPARATION_RE = re.compile(
        r"\b(?:independent(?:ly)?|separat(?:e|ed|ely|ion)|decoupl(?:e|ed|ing)|"
        r"isolat(?:e|ed|ion)|headless)\b|"
        r"\bwithout\s+(?:pygame|render(?:ing)?|ui|gui|"
        r"(?:a\s+)?(?:graphical\s+)?display|database|network|external services?)\b",
        re.IGNORECASE,
    )
    _PATH_ROLES = {
        "domain": re.compile(
            r"(?:^|[/_.-])(?:domain|logic|game|core|engine|rules|business)(?:[/_.-]|$)",
            re.IGNORECASE,
        ),
        "presentation": re.compile(
            r"(?:^|[/_.-])(?:renderer|rendering|render|view|ui|gui|frontend|presentation|screen|component)(?:[/_.-]|$)",
            re.IGNORECASE,
        ),
        "data": re.compile(
            r"(?:^|[/_.-])(?:repository|persistence|database|storage|dao)(?:[/_.-]|$)",
            re.IGNORECASE,
        ),
        "entrypoint": re.compile(
            r"(?:^|[/_.-])(?:main|app|cli|__main__|bootstrap)(?:[/_.-]|$)",
            re.IGNORECASE,
        ),
    }
    _RESPONSIBILITY_CONCERNS = {
        "domain": re.compile(r"\b(?:domain|business|game|core)\s+(?:logic|rules)\b|\blogic\b", re.I),
        "presentation": re.compile(r"\b(?:render(?:er|ing)?|presentation|user interface|\bui\b|visual)\b", re.I),
        "entrypoint": re.compile(r"\b(?:entry point|startup|bootstrap|application launch)\b", re.I),
        "configuration": re.compile(r"\b(?:dependenc(?:y|ies)|configuration|build setup|environment setup)\b", re.I),
        "documentation": re.compile(r"\b(?:documentation|readme|user guide)\b", re.I),
        "testing": re.compile(r"\b(?:tests?|testing|test suite)\b", re.I),
        "data": re.compile(r"\b(?:database|persistence|storage|repository)\b", re.I),
    }

    def validate(
        self,
        modules: Iterable[ArchitectureModule],
        *,
        objective: str = "",
        requirements: Iterable[Requirement] = (),
        constraints: Iterable[str] = (),
        acceptance_criteria: Iterable[str] = (),
    ) -> ArchitectureValidationReport:
        modules = tuple(modules)
        requirements = tuple(requirements)
        text_parts = [objective, *constraints, *acceptance_criteria]
        for requirement in requirements:
            text_parts.extend((requirement.description, *requirement.acceptance_criteria))
        task_text = " ".join(part for part in text_parts if part)
        separation_requested = bool(self._SEPARATION_RE.search(task_text))

        issues: list[str] = []
        path_owners: dict[str, str] = {}
        for module in modules:
            for raw_path in module.file_paths:
                normalized = PurePosixPath(raw_path.replace("\\", "/")).as_posix().casefold()
                previous_owner = path_owners.get(normalized)
                if previous_owner is not None and previous_owner != module.module_id:
                    issues.append(
                        f"file path {raw_path!r} is assigned to multiple architecture modules "
                        f"({previous_owner}, {module.module_id})"
                    )
                else:
                    path_owners[normalized] = module.module_id

        # If the task explicitly requests separation/independence, domain logic,
        # presentation, and persistence must not be collapsed into one module.
        if separation_requested:
            for module in modules:
                roles = {
                    role
                    for path in module.file_paths
                    for role, pattern in self._PATH_ROLES.items()
                    if pattern.search(path.replace("\\", "/"))
                }
                if len(roles) > 1:
                    issues.append(
                        f"module {module.module_id!r} combines distinct responsibilities "
                        f"{', '.join(sorted(roles))} despite an explicit separation/independence requirement; "
                        "assign those responsibilities to separate modules"
                    )

        # A single responsibility statement that claims ownership of many
        # unrelated concerns is a strong signal that the architecture has been
        # flattened into a repository-wide catch-all module.
        for module in modules:
            concern_count = sum(
                bool(pattern.search(module.responsibility))
                for pattern in self._RESPONSIBILITY_CONCERNS.values()
            )
            if concern_count >= 4:
                issues.append(
                    f"module {module.module_id!r} has an omnibus responsibility spanning "
                    f"{concern_count} concern areas; split it into cohesive modules"
                )

        # Preserve order while avoiding duplicate diagnostics from overlapping
        # path/responsibility checks.
        unique_issues = tuple(dict.fromkeys(issues))
        return ArchitectureValidationReport(not unique_issues, unique_issues)
