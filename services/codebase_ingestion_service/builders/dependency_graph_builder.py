from models.dependency import Dependency
from models.dependency_graph import DependencyGraph
from models.parsed_document import ParsedDocument


class DependencyGraphBuilder:

    def build(
        self,
        documents: list[ParsedDocument]
    ) -> DependencyGraph:

        nodes = []

        edges = []

        for document in documents:

            source = document.relative_path

            nodes.append(source)

            for imp in document.imports:

                edges.append(
                    Dependency(
                        source=source,
                        module=imp.module,
                        target=imp.module,
                        dependency_type="import"
                    )
                )

        return DependencyGraph(
            nodes=sorted(set(nodes)),
            edges=edges
        )