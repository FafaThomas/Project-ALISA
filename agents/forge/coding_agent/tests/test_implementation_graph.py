import pytest

from coding_agent.engineering_spec import (
    EngineeringSpecification, ImplementationUnit, UnitKind
)
from coding_agent.implementation_graph import (
    ImplementationGraphBuilder, ImplementationGraphError
)


def make_spec(units):
    return EngineeringSpecification(
        specification_id="espec-graph-test",
        task_id="graph-test",
        objective="Test implementation graph",
        workspace="D:/workspace",
        implementation_units=tuple(units),
    )


def unit(name, deps=()):
    return ImplementationUnit(
        unit_id=name, title=name, target_path=f"src/{name}.py",
        kind=UnitKind.SOURCE, depends_on=tuple(deps)
    )


def test_builds_deterministic_parallel_execution_groups():
    spec = make_spec([
        unit("integration", ("store", "ui")),
        unit("ui"),
        unit("store", ("model",)),
        unit("model"),
        unit("docs"),
    ])
    graph = ImplementationGraphBuilder().build(spec)
    assert graph.execution_groups == (
        ("docs", "model", "ui"),
        ("store",),
        ("integration",),
    )
    assert graph.ordered_unit_ids == ("docs", "model", "ui", "store", "integration")


def test_critical_path_is_longest_dependency_chain():
    graph = ImplementationGraphBuilder().build(make_spec([
        unit("a"), unit("b", ("a",)), unit("c", ("b",)),
        unit("d", ("a",)), unit("e", ("d",)),
    ]))
    assert graph.critical_path == ("a", "b", "c")
    assert graph.critical_path_length == 3


def test_downstream_impact_returns_transitive_dependents():
    graph = ImplementationGraphBuilder().build(make_spec([
        unit("base"), unit("left", ("base",)),
        unit("right", ("base",)), unit("final", ("left", "right")),
        unit("unrelated"),
    ]))
    assert graph.downstream_impact("base") == ("left", "right", "final")
    assert graph.downstream_impact("left") == ("final",)


def test_unknown_impact_unit_raises():
    graph = ImplementationGraphBuilder().build(make_spec([unit("a")]))
    with pytest.raises(KeyError, match="unknown implementation unit"):
        graph.downstream_impact("missing")


def test_empty_specification_is_rejected():
    with pytest.raises(ImplementationGraphError, match="no implementation units"):
        ImplementationGraphBuilder().build(make_spec([]))


def test_graph_serialization_is_json_compatible():
    graph = ImplementationGraphBuilder().build(make_spec([unit("a"), unit("b", ("a",))]))
    data = graph.to_dict()
    assert data["execution_groups"] == [["a"], ["b"]]
    assert data["critical_path"] == ["a", "b"]
