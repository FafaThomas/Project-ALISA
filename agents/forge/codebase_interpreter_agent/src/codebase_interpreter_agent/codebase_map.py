
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .dependency_resolver import DependencyReport
from .scanner import ScanReport


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


@dataclass
class CodebaseMap:
    root: str
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def build_codebase_map(
    scan_report: ScanReport,
    dependency_report: DependencyReport,
) -> CodebaseMap:
    """Combine file inventory, symbols, and dependency edges."""

    symbols_by_path: dict[str, list[dict]] = {}

    for symbol in scan_report.symbols:
        path = _normalize_path(symbol.path)
        symbols_by_path.setdefault(path, []).append(asdict(symbol))

    nodes = []

    for record in scan_report.files:
        path = _normalize_path(record.path)

        nodes.append({
            **asdict(record),
            "path": path,
            "symbols": sorted(
                symbols_by_path.get(path, []),
                key=lambda symbol: (
                    symbol["line"],
                    symbol["qualified_name"],
                ),
            ),
        })

    nodes.sort(key=lambda node: node["path"])

    edges = []

    for dependency in dependency_report.edges:
        edge = asdict(dependency)
        edge["source_path"] = _normalize_path(
            dependency.source_path
        )
        edge["target_paths"] = sorted(
            _normalize_path(path)
            for path in dependency.target_paths
        )
        edges.append(edge)

    edges.sort(
        key=lambda edge: (
            edge["source_path"],
            edge["line"],
            edge["imported_module"],
            edge["imported_names"].__str__(),
        )
    )

    statuses = [edge["status"] for edge in edges]

    summary = {
        "file_count": len(nodes),
        "symbol_count": sum(
            len(node["symbols"]) for node in nodes
        ),
        "import_edge_count": len(edges),
        "local_edge_count": statuses.count("resolved_local"),
        "external_edge_count": statuses.count("external"),
        "unresolved_edge_count": statuses.count("unresolved"),
        "parse_error_count": sum(
            record.status == "parse_error"
            for record in scan_report.files
        ),
    }

    return CodebaseMap(
        root=scan_report.root,
        nodes=nodes,
        edges=edges,
        errors=list(scan_report.errors),
        skipped=list(scan_report.skipped),
        summary=summary,
    )
