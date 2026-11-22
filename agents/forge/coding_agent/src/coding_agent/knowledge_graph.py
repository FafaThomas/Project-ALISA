"""In-memory knowledge graph built from a CodeIndex.

The graph is a query layer over existing parser and resolver output. It does
not parse source or attempt to invent relationships that the index does not
contain.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable, Literal



@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str
    label: str
    path: str | None = None
    module: str | None = None
    language: str | None = None
    symbol_kind: str | None = None
    line: int | None = None
    end_line: int | None = None
    status: str = "resolved"
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GraphEdge:
    source_id: str
    target_id: str
    kind: str
    status: str = "resolved"
    line: int = 0
    evidence: str | None = None
    candidates: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GraphTraversal:
    start_id: str
    direction: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


class KnowledgeGraph:
    """Immutable directed graph with bounded traversal and impact queries."""

    def __init__(self, nodes: Iterable[GraphNode], edges: Iterable[GraphEdge]) -> None:
        node_map = {node.id: node for node in nodes}
        edge_list = tuple(sorted(
            {edge for edge in edges if edge.source_id in node_map and edge.target_id in node_map},
            key=lambda edge: (edge.source_id, edge.kind, edge.target_id, edge.line, edge.status),
        ))
        self.nodes = tuple(sorted(node_map.values(), key=lambda node: node.id))
        self.edges = edge_list
        self._node_map = node_map
        outgoing: dict[str, list[GraphEdge]] = defaultdict(list)
        incoming: dict[str, list[GraphEdge]] = defaultdict(list)
        for edge in edge_list:
            outgoing[edge.source_id].append(edge)
            incoming[edge.target_id].append(edge)
        self._outgoing = {key: tuple(value) for key, value in outgoing.items()}
        self._incoming = {key: tuple(value) for key, value in incoming.items()}

    def get_node(self, node_id: str) -> GraphNode | None:
        return self._node_map.get(node_id)

    def find_nodes(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        path: str | None = None,
        language: str | None = None,
    ) -> tuple[GraphNode, ...]:
        """Find nodes by case-insensitive label/id substring and optional filters."""
        needle = query.casefold()
        return tuple(
            node for node in self.nodes
            if (not needle or needle in node.label.casefold() or needle in node.id.casefold())
            and (kind is None or node.kind == kind)
            and (path is None or node.path == path.replace("\\", "/"))
            and (language is None or node.language == language)
        )

    def outgoing(self, node_id: str, *, kinds: Iterable[str] | None = None,
                 statuses: Iterable[str] | None = None) -> tuple[GraphEdge, ...]:
        return self._filter_edges(self._outgoing.get(node_id, ()), kinds, statuses)

    def incoming(self, node_id: str, *, kinds: Iterable[str] | None = None,
                 statuses: Iterable[str] | None = None) -> tuple[GraphEdge, ...]:
        return self._filter_edges(self._incoming.get(node_id, ()), kinds, statuses)

    @staticmethod
    def _filter_edges(edges, kinds, statuses):
        kind_set = set(kinds) if kinds is not None else None
        status_set = set(statuses) if statuses is not None else None
        return tuple(
            edge for edge in edges
            if (kind_set is None or edge.kind in kind_set)
            and (status_set is None or edge.status in status_set)
        )

    def traverse(
        self,
        start_id: str,
        *,
        direction: Literal["out", "in", "both"] = "out",
        max_depth: int = 1,
        kinds: Iterable[str] | None = None,
        statuses: Iterable[str] | None = ("resolved",),
    ) -> GraphTraversal:
        """Breadth-first traversal, excluding the starting node from results."""
        if direction not in {"out", "in", "both"}:
            raise ValueError("direction must be 'out', 'in', or 'both'")
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        if start_id not in self._node_map:
            return GraphTraversal(start_id, direction, (), ())

        visited = {start_id}
        queue = deque([(start_id, 0)])
        found_nodes: list[GraphNode] = []
        found_edges: list[GraphEdge] = []
        seen_edges: set[GraphEdge] = set()
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            edges = []
            if direction in {"out", "both"}:
                edges.extend(self.outgoing(current, kinds=kinds, statuses=statuses))
            if direction in {"in", "both"}:
                edges.extend(self.incoming(current, kinds=kinds, statuses=statuses))
            for edge in edges:
                if edge not in seen_edges:
                    found_edges.append(edge)
                    seen_edges.add(edge)
                neighbor = edge.target_id if edge.source_id == current else edge.source_id
                if neighbor not in visited:
                    visited.add(neighbor)
                    found_nodes.append(self._node_map[neighbor])
                    queue.append((neighbor, depth + 1))
        return GraphTraversal(start_id, direction, tuple(found_nodes), tuple(found_edges))

    def impact_analysis(
        self,
        node_id: str,
        *,
        max_depth: int = 2,
        kinds: Iterable[str] | None = None,
    ) -> GraphTraversal:
        """Return resolved upstream dependents potentially affected by a change."""
        return self.traverse(
            node_id, direction="in", max_depth=max_depth, kinds=kinds,
            statuses=("resolved",),
        )

    def render(self, *, max_nodes: int = 100, max_edges: int = 200) -> str:
        """Render a compact deterministic graph summary."""
        lines = [
            "KNOWLEDGE GRAPH",
            f"Nodes: {len(self.nodes)}",
            f"Edges: {len(self.edges)}",
            "",
            "NODES:",
        ]
        for node in self.nodes[:max_nodes]:
            location = f" ({node.path}:{node.line})" if node.path and node.line else (f" ({node.path})" if node.path else "")
            lines.append(f"  {node.kind}: {node.label}{location} [{node.id}]")
        if len(self.nodes) > max_nodes:
            lines.append(f"  ... {len(self.nodes) - max_nodes} more nodes")
        lines.append("\nEDGES:")
        for edge in self.edges[:max_edges]:
            lines.append(f"  {edge.kind}: {edge.source_id} -> {edge.target_id} [{edge.status}]")
        if len(self.edges) > max_edges:
            lines.append(f"  ... {len(self.edges) - max_edges} more edges")
        return "\n".join(lines)

    @classmethod
    def from_code_index(cls, index) -> "KnowledgeGraph":
        """Construct graph nodes and edges from a CodeIndex-like object."""
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        module_ids: dict[str, str] = {}
        symbol_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
        resolver_symbol_ids: dict[str, str] = {}

        def module_id(path: str) -> str:
            return f"module:{path}"

        def symbol_id(symbol) -> str:
            return f"symbol:{symbol.relative_path}::{symbol.qualified_name}@{symbol.line}:{symbol.kind}"

        for module in index.modules:
            mid = module_id(module.relative_path)
            module_ids[module.relative_path] = mid
            label = module.module_names[-1] if module.module_names else module.relative_path
            nodes[mid] = GraphNode(
                id=mid, kind="module", label=label, path=module.relative_path,
                module=label, language=module.language,
                status="error" if module.parse_error else "resolved",
                metadata=(("parse_error", module.parse_error),) if module.parse_error else (),
            )
            for symbol in module.symbols:
                sid = symbol_id(symbol)
                symbol_ids[(symbol.relative_path, symbol.qualified_name)].append(sid)
                resolver_symbol_ids[
                    f"{symbol.relative_path}::{symbol.qualified_name}@{symbol.line}:{symbol.kind}"
                ] = sid
                nodes[sid] = GraphNode(
                    id=sid, kind="symbol", label=symbol.qualified_name,
                    path=symbol.relative_path, module=label, language=module.language,
                    symbol_kind=symbol.kind, line=symbol.line, end_line=symbol.end_line,
                    metadata=tuple(symbol.metadata),
                )

        # Add structural module -> top-level symbol edges.
        for module in index.modules:
            for symbol in module.symbols:
                parent_name = symbol.qualified_name.rpartition(".")[0]
                parent_candidates = symbol_ids.get((symbol.relative_path, parent_name), ()) if parent_name else ()
                child_id = symbol_id(symbol)
                parent_id = parent_candidates[0] if len(parent_candidates) == 1 else module_ids[module.relative_path]
                edges.append(GraphEdge(parent_id, child_id, "contains", line=symbol.line, evidence="index_structure"))

        def resolve_symbol(path: str, name: str | None) -> str | None:
            if not path or not name:
                return None
            candidates = symbol_ids.get((path, name), ())
            return candidates[0] if len(candidates) == 1 else None

        for relationship in index.relationships:
            # Module/symbol containment is constructed directly from the symbol
            # hierarchy above, so avoid duplicating the resolver's same edges.
            if relationship.kind == "contains" and relationship.source_symbol and relationship.target_symbol:
                continue
            source_id = resolve_symbol(relationship.source_path, relationship.source_symbol)
            if source_id is None:
                source_id = module_ids.get(relationship.source_path)
            if source_id is None:
                continue

            target_id = None
            if relationship.target_path:
                target_id = resolve_symbol(relationship.target_path, relationship.target_symbol)
                if target_id is None and not relationship.target_symbol:
                    target_id = module_ids.get(relationship.target_path)
            if target_id is None:
                ref_key = (relationship.source_path, relationship.line, relationship.kind,
                           relationship.target_symbol or relationship.target_module or "unknown")
                target_id = "reference:" + ":".join(str(item) for item in ref_key)
                nodes.setdefault(target_id, GraphNode(
                    id=target_id, kind="reference",
                    label=relationship.target_symbol or relationship.target_module or "unknown",
                    path=relationship.target_path, module=relationship.target_module,
                    line=relationship.line, status=relationship.status,
                    metadata=(("candidates", "|".join(relationship.candidates)),) if relationship.candidates else (),
                ))
            edges.append(GraphEdge(
                source_id=source_id, target_id=target_id, kind=relationship.kind,
                status=relationship.status, line=relationship.line,
                evidence=relationship.evidence, candidates=relationship.candidates,
                metadata=relationship.metadata,
            ))

            # Ambiguous candidate references remain queryable as explicit edges.
            for candidate in relationship.candidates:
                candidate_id = resolver_symbol_ids.get(candidate)
                if candidate_id:
                    edges.append(GraphEdge(
                        target_id, candidate_id, "candidate", status="ambiguous",
                        line=relationship.line, evidence="ambiguous_candidate",
                    ))

        return cls(nodes.values(), edges)
