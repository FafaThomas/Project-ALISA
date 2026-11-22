"""Deterministic implementation dependency graph derived from an ESpec.

The graph is an execution view of the specification; it never mutates the
specification or performs code generation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from coding_agent.engineering_spec import EngineeringSpecification


class ImplementationGraphError(ValueError):
    """Raised when an implementation graph cannot be scheduled."""


@dataclass(frozen=True)
class ImplementationNode:
    unit_id: str
    target_path: str
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class ImplementationGraph:
    nodes: tuple[ImplementationNode, ...]
    execution_groups: tuple[tuple[str, ...], ...]
    critical_path: tuple[str, ...]
    critical_path_length: int

    @property
    def ordered_unit_ids(self) -> tuple[str, ...]:
        return tuple(unit_id for group in self.execution_groups for unit_id in group)

    def downstream_impact(self, unit_id: str) -> tuple[str, ...]:
        """Return all transitive dependents, in deterministic execution order."""
        known = {node.unit_id for node in self.nodes}
        if unit_id not in known:
            raise KeyError(f"unknown implementation unit: {unit_id}")
        dependents: dict[str, set[str]] = {key: set() for key in known}
        for node in self.nodes:
            for dependency in node.depends_on:
                dependents[dependency].add(node.unit_id)
        affected: set[str] = set()
        frontier = list(sorted(dependents[unit_id], reverse=True))
        while frontier:
            current = frontier.pop()
            if current in affected:
                continue
            affected.add(current)
            frontier.extend(sorted(dependents[current] - affected, reverse=True))
        order = {key: index for index, key in enumerate(self.ordered_unit_ids)}
        return tuple(sorted(affected, key=lambda key: order[key]))

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {"unit_id": n.unit_id, "target_path": n.target_path,
                 "depends_on": list(n.depends_on)}
                for n in self.nodes
            ],
            "execution_groups": [list(group) for group in self.execution_groups],
            "ordered_unit_ids": list(self.ordered_unit_ids),
            "critical_path": list(self.critical_path),
            "critical_path_length": self.critical_path_length,
        }


class ImplementationGraphBuilder:
    """Build a deterministic DAG schedule from implementation-unit dependencies."""

    def build(self, specification: EngineeringSpecification) -> ImplementationGraph:
        units = {unit.unit_id: unit for unit in specification.implementation_units}
        if not units:
            raise ImplementationGraphError("specification contains no implementation units")

        dependencies = {key: set(unit.depends_on) for key, unit in units.items()}
        for unit_id, deps in dependencies.items():
            missing = deps - units.keys()
            if missing:
                raise ImplementationGraphError(
                    f"unit {unit_id!r} references unknown dependencies: {sorted(missing)}"
                )
            if unit_id in deps:
                raise ImplementationGraphError(f"unit {unit_id!r} depends on itself")

        # Kahn's algorithm, with lexical ordering for reproducible schedules.
        remaining = {key: set(value) for key, value in dependencies.items()}
        groups: list[tuple[str, ...]] = []
        while remaining:
            ready = tuple(sorted(key for key, deps in remaining.items() if not deps))
            if not ready:
                cycle_nodes = sorted(remaining)
                raise ImplementationGraphError(
                    f"implementation dependency cycle detected among: {cycle_nodes}"
                )
            groups.append(ready)
            for key in ready:
                remaining.pop(key)
            for deps in remaining.values():
                deps.difference_update(ready)

        critical_path = self._critical_path(dependencies)
        nodes = tuple(
            ImplementationNode(
                unit_id=key,
                target_path=units[key].target_path,
                depends_on=tuple(sorted(dependencies[key])),
            )
            for key in sorted(units)
        )
        return ImplementationGraph(
            nodes=nodes,
            execution_groups=tuple(groups),
            critical_path=critical_path,
            critical_path_length=len(critical_path),
        )

    @staticmethod
    def _critical_path(dependencies: dict[str, set[str]]) -> tuple[str, ...]:
        """Return a deterministic longest dependency chain (unit count)."""
        memo: dict[str, tuple[str, ...]] = {}

        def longest_from(node: str) -> tuple[str, ...]:
            if node in memo:
                return memo[node]
            parents = sorted(dependencies[node])
            if not parents:
                result = (node,)
            else:
                candidates = [longest_from(parent) + (node,) for parent in parents]
                result = min(candidates, key=lambda path: (-len(path), path))
            memo[node] = result
            return result

        paths = [longest_from(node) for node in sorted(dependencies)]
        return min(paths, key=lambda path: (-len(path), path))
