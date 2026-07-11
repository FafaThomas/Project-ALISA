from pathlib import Path

from models.dependency_graph import DependencyGraph


class DependencyResolver:

    def resolve(
        self,
        graph: DependencyGraph
    ) -> DependencyGraph:

        lookup = {}

        for node in graph.nodes:

            path = Path(node)

            module = ".".join(path.with_suffix("").parts[-2:])

            lookup[module] = node

        for edge in graph.edges:

            module = edge.module

            parts = module.split(".")

            # remove imported class/function

            while len(parts) > 2:

                candidate = ".".join(parts[:-1])

                if candidate in lookup:

                    edge.target = lookup[candidate]

                    break

                parts.pop()

        return graph