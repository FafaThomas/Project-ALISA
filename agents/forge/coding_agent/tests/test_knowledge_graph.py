from coding_agent.code_index import CodeIndex, ModuleRecord
from coding_agent.knowledge_graph import GraphEdge, GraphNode, KnowledgeGraph
from coding_agent.parsers.base import Symbol
from coding_agent.resolvers.models import Relationship


def make_index():
    modules = (
        ModuleRecord(
            relative_path="app/models.py", module_names=("app.models",),
            symbols=(Symbol("User", "User", "class", "app/models.py", 1, 5),),
            imports=(), language="python",
        ),
        ModuleRecord(
            relative_path="app/service.py", module_names=("app.service",),
            symbols=(
                Symbol("UserService", "UserService", "class", "app/service.py", 1, 8),
                Symbol("create", "UserService.create", "method", "app/service.py", 2, 8),
            ), imports=(), language="python",
        ),
        ModuleRecord(
            relative_path="app/api.py", module_names=("app.api",),
            symbols=(Symbol("create_user", "create_user", "function", "app/api.py", 1, 3),),
            imports=(), language="python",
        ),
    )
    relationships = (
        Relationship(
            kind="contains", source_path="app/service.py", source_module="app.service",
            source_symbol="UserService", target_path="app/service.py", target_module="app.service",
            target_symbol="UserService.create", line=2, evidence="qualified_name_scope",
        ),
        Relationship(
            kind="imports", source_path="app/api.py", source_module="app.api",
            source_symbol=None, target_path="app/service.py", target_module="app.service",
            target_symbol=None, line=1,
        ),
        Relationship(
            kind="calls", source_path="app/api.py", source_module="app.api",
            source_symbol="create_user", target_path="app/service.py", target_module="app.service",
            target_symbol="UserService.create", line=2,
        ),
        Relationship(
            kind="imports", source_path="app/service.py", source_module="app.service",
            source_symbol=None, target_path=None, target_module="external_pkg",
            target_symbol="external_pkg.client", line=1, status="external",
        ),
    )
    return CodeIndex(modules, (), 3, 3, 0, relationships)


def test_builds_module_symbol_and_relationship_graph():
    graph = make_index().build_knowledge_graph()
    assert len(graph.find_nodes(kind="module")) == 3
    assert len(graph.find_nodes(kind="symbol")) == 4
    assert any(edge.kind == "imports" for edge in graph.edges)
    assert any(edge.kind == "contains" for edge in graph.edges)
    assert any(node.kind == "reference" and node.status == "external" for node in graph.nodes)


def test_finds_nodes_with_filters():
    graph = make_index().build_knowledge_graph()
    found = graph.find_nodes("UserService", kind="symbol", language="python")
    assert len(found) == 2


def test_outgoing_and_incoming_relationship_queries():
    graph = make_index().build_knowledge_graph()
    api = graph.get_node("module:app/api.py")
    assert api is not None
    assert any(edge.kind == "imports" for edge in graph.outgoing(api.id))
    service_symbol = graph.find_nodes("UserService.create", kind="symbol")[0]
    assert any(edge.kind == "calls" for edge in graph.incoming(service_symbol.id))


def test_impact_analysis_finds_upstream_dependents():
    graph = make_index().build_knowledge_graph()
    target = graph.find_nodes("UserService.create", kind="symbol")[0]
    impact = graph.impact_analysis(target.id, max_depth=2)
    assert any(node.label == "create_user" for node in impact.nodes)


def test_traversal_is_bounded_and_filters_relationship_types():
    graph = make_index().build_knowledge_graph()
    api = graph.get_node("module:app/api.py")
    result = graph.traverse(api.id, direction="out", max_depth=1, kinds={"imports"})
    assert len(result.nodes) == 1
    assert result.nodes[0].path == "app/service.py"


def test_unknown_start_and_invalid_traversal_are_safe():
    graph = make_index().build_knowledge_graph()
    assert graph.traverse("missing").nodes == ()
    try:
        graph.traverse("missing", direction="sideways")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid direction should fail")


def test_graph_constructor_drops_edges_with_missing_endpoints():
    graph = KnowledgeGraph(
        [GraphNode("a", "module", "a")],
        [GraphEdge("a", "missing", "imports")],
    )
    assert graph.edges == ()


def test_ambiguous_relationship_preserves_candidates_without_treating_them_as_resolved():
    modules = make_index().modules
    candidate = modules[0].symbols[0]
    candidate_id = f"{candidate.relative_path}::{candidate.qualified_name}@{candidate.line}:{candidate.kind}"
    ambiguous = Relationship(
        kind="calls", source_path="app/api.py", source_module="app.api",
        source_symbol="create_user", target_path=None, target_module=None,
        target_symbol="User", line=3, status="ambiguous", candidates=(candidate_id,),
    )
    index = CodeIndex(modules, (), 3, 3, 0, (ambiguous,))
    graph = index.build_knowledge_graph()
    reference = graph.find_nodes("User", kind="reference")[0]
    candidate_edges = graph.outgoing(reference.id, kinds={"candidate"})
    assert len(candidate_edges) == 1
    assert candidate_edges[0].status == "ambiguous"
    assert graph.traverse(reference.id, direction="out").nodes == ()
