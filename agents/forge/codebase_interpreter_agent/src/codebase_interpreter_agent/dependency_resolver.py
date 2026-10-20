
from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath

from .scanner import ScanReport


@dataclass
class DependencyEdge:
    source_path: str
    line: int
    imported_module: str
    imported_names: list[str] = field(default_factory=list)
    relative_level: int = 0
    status: str = "unresolved"
    target_paths: list[str] = field(default_factory=list)
    classification: str = "unknown"
    note: str = ""


@dataclass
class DependencyReport:
    root: str
    edges: list[DependencyEdge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def local_edges(self) -> list[DependencyEdge]:
        return [
            edge for edge in self.edges
            if edge.status == "resolved_local"
        ]

    @property
    def external_edges(self) -> list[DependencyEdge]:
        return [
            edge for edge in self.edges
            if edge.status == "external"
        ]


@dataclass
class _ModuleIndex:
    modules: dict[tuple[str, str], str]
    modules_by_name: dict[str, list[str]]
    roots_by_path: dict[str, str]
    packages_by_path: dict[str, str]


def _module_and_root(path: str) -> tuple[str, str]:
    """Infer a source root and dotted module name from a Python path."""
    parts = list(PurePosixPath(path).parts)

    if not parts or PurePosixPath(path).suffix.lower() not in {
        ".py", ".pyi"
    }:
        return "", ""

    if "src" in parts[:-1]:
        index = parts.index("src")
        root = PurePosixPath(*parts[:index + 1]).as_posix()
        module_parts = parts[index + 1:]
    else:
        root = ""
        module_parts = parts

    filename = module_parts[-1]

    if filename in {"__init__.py", "__init__.pyi"}:
        module_parts = module_parts[:-1]
    else:
        module_parts[-1] = PurePosixPath(filename).stem

    module = ".".join(module_parts)
    return root, module



def _build_index(report: ScanReport) -> _ModuleIndex:
    modules: dict[tuple[str, str], str] = {}
    modules_by_name: dict[str, list[str]] = defaultdict(list)
    roots_by_path: dict[str, str] = {}
    packages_by_path: dict[str, str] = {}

    python_files = sorted(
        record.path.replace("\\", "/")
        for record in report.files
        if record.language == "python"
        and record.status in {"analyzed", "parse_error", "inventoried"}
    )

    for path in python_files:
        root, module = _module_and_root(path)

        if not module:
            continue

        roots_by_path[path] = root
        modules[(root, module)] = path
        modules_by_name[module].append(path)

        # Support imports written as src.package.module.
        if root:
            src_alias = f"src.{module}"
            modules[(root, src_alias)] = path
            modules_by_name[src_alias].append(path)

    for path in python_files:
        _, module = _module_and_root(path)

        if not module:
            packages_by_path[path] = ""
        elif PurePosixPath(path).name in {
            "__init__.py", "__init__.pyi"
        }:
            packages_by_path[path] = module
        else:
            packages_by_path[path] = module.rpartition(".")[0]

    return _ModuleIndex(
        modules=modules,
        modules_by_name=dict(modules_by_name),
        roots_by_path=roots_by_path,
        packages_by_path=packages_by_path,
    )


def _relative_module(
    package: str,
    module: str,
    level: int,
) -> str | None:
    """Resolve relative-import dots against the importing package."""
    if level == 0:
        return module

    parts = package.split(".") if package else []
    ascend = level - 1

    if ascend > len(parts):
        return None

    if ascend:
        parts = parts[:-ascend]

    if module:
        parts.extend(module.split("."))

    return ".".join(parts)


def _find_local(
    module: str,
    source_root: str,
    index: _ModuleIndex,
) -> list[str]:
    if not module:
        return []

    same_root = index.modules.get((source_root, module))
    if same_root:
        return [same_root]

    # Accept a cross-root match only when it is unambiguous.
    candidates = index.modules_by_name.get(module, [])
    if len(candidates) == 1:
        return candidates

    return []


def resolve_imports(report: ScanReport) -> DependencyReport:
    """Resolve imports using the files and import records in ScanReport."""
    index = _build_index(report)
    result = DependencyReport(root=report.root)
    stdlib = getattr(sys, "stdlib_module_names", frozenset())

    for ref in report.imports:
        source = ref.path.replace("\\", "/")
        source_root = index.roots_by_path.get(source, "")
        package = index.packages_by_path.get(source, "")
        names = list(ref.names)

        base_module = _relative_module(
            package,
            ref.module,
            ref.relative_level,
        )

        if base_module is None:
            result.edges.append(DependencyEdge(
                source_path=source,
                line=ref.line,
                imported_module=ref.module,
                imported_names=names,
                relative_level=ref.relative_level,
                status="unresolved",
                classification="relative",
                note="Relative import goes above the inferred package root.",
            ))
            continue

        targets: set[str] = set()

        if not ref.module:
            # `import package.submodule`, or `from . import submodule`.
            candidates = names if ref.relative_level == 0 else [
                f"{base_module}.{name}" if base_module else name
                for name in names
                if name != "*"
            ]

            for candidate in candidates:
                targets.update(
                    _find_local(candidate, source_root, index)
                )

        else:
            # Prefer imported submodules, e.g. `from package import models`.
            for name in names:
                if name == "*":
                    continue

                child = f"{base_module}.{name}" if base_module else name
                targets.update(
                    _find_local(child, source_root, index)
                )

            # If no imported name is a local module, resolve the module
            # itself, e.g. `from package.models import Model`.
            if not targets and base_module:
                targets.update(
                    _find_local(base_module, source_root, index)
                )

        if targets:
            result.edges.append(DependencyEdge(
                source_path=source,
                line=ref.line,
                imported_module=ref.module,
                imported_names=names,
                relative_level=ref.relative_level,
                status="resolved_local",
                target_paths=sorted(targets),
                classification="local",
                note="Matched against inventoried Python module paths.",
            ))
            continue

        if ref.relative_level:
            status = "unresolved"
            classification = "relative"
            note = "No matching local target was found for this relative import."
        else:
            top_level = (
                ref.module.split(".", 1)[0]
                if ref.module
                else names[0].split(".", 1)[0] if names else ""
            )

            status = "external"
            if top_level in stdlib:
                classification = "standard_library"
                note = "Top-level import name belongs to this Python runtime's standard library."
            else:
                classification = "likely_third_party"
                note = (
                    "No local target matched. Could be a third-party "
                    "dependency or an unavailable local module."
                )

        result.edges.append(DependencyEdge(
            source_path=source,
            line=ref.line,
            imported_module=ref.module,
            imported_names=names,
            relative_level=ref.relative_level,
            status=status,
            classification=classification,
            note=note,
        ))

    return result
