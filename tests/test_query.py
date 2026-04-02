from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType
from smg import query


def _chain_graph() -> SemGraph:
    """a -> b -> c -> d (all 'calls')"""
    g = SemGraph()
    for name in ["a", "b", "c", "d"]:
        g.add_node(Node(name=name, type=NodeType.FUNCTION))
    g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))
    g.add_edge(Edge(source="b", target="c", rel=RelType.CALLS))
    g.add_edge(Edge(source="c", target="d", rel=RelType.CALLS))
    return g


def _dep_graph() -> SemGraph:
    """app depends_on lib, lib depends_on core"""
    g = SemGraph()
    for name in ["app", "lib", "core"]:
        g.add_node(Node(name=name, type=NodeType.MODULE))
    g.add_edge(Edge(source="app", target="lib", rel=RelType.DEPENDS_ON))
    g.add_edge(Edge(source="lib", target="core", rel=RelType.DEPENDS_ON))
    return g


def test_transitive_deps():
    g = _dep_graph()
    deps = query.transitive_deps(g, "app")
    assert deps == ["core", "lib"]


def test_transitive_deps_with_depth():
    g = _dep_graph()
    deps = query.transitive_deps(g, "app", max_depth=1)
    assert deps == ["lib"]


def test_transitive_callers():
    g = _chain_graph()
    callers = query.transitive_callers(g, "d")
    assert callers == ["a", "b", "c"]


def test_transitive_callers_with_depth():
    g = _chain_graph()
    callers = query.transitive_callers(g, "d", max_depth=1)
    assert callers == ["c"]


def test_shortest_path():
    g = _chain_graph()
    path = query.shortest_path(g, "a", "d")
    assert path == ["a", "b", "c", "d"]


def test_shortest_path_not_found():
    g = SemGraph()
    g.add_node(Node(name="a", type=NodeType.FUNCTION))
    g.add_node(Node(name="b", type=NodeType.FUNCTION))
    # No edges
    path = query.shortest_path(g, "a", "b")
    assert path is None


def test_shortest_path_same_node():
    g = SemGraph()
    g.add_node(Node(name="a", type=NodeType.FUNCTION))
    assert query.shortest_path(g, "a", "a") == ["a"]


def test_subgraph():
    g = _chain_graph()
    sub = query.subgraph(g, "b", depth=1)
    assert set(sub.nodes.keys()) == {"a", "b", "c"}
    assert len(sub.all_edges()) == 2


def test_subgraph_depth_0():
    g = _chain_graph()
    sub = query.subgraph(g, "b", depth=0)
    assert set(sub.nodes.keys()) == {"b"}
    assert len(sub.all_edges()) == 0


def test_ancestors():
    g = _dep_graph()
    anc = query.ancestors(g, "core", RelType.DEPENDS_ON.value)
    assert anc == ["app", "lib"]


def test_descendants():
    g = _dep_graph()
    desc = query.descendants(g, "app", RelType.DEPENDS_ON.value)
    assert desc == ["core", "lib"]


def test_cycle_handling():
    """Ensure BFS doesn't loop on cycles."""
    g = SemGraph()
    g.add_node(Node(name="a", type=NodeType.FUNCTION))
    g.add_node(Node(name="b", type=NodeType.FUNCTION))
    g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))
    g.add_edge(Edge(source="b", target="a", rel=RelType.CALLS))
    callers = query.transitive_callers(g, "a")
    assert callers == ["b"]
