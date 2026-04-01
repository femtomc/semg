import pytest

from semg.graph import NodeNotFoundError, SemGraph
from semg.model import Edge, Node, NodeType, RelType


def _make_graph() -> SemGraph:
    g = SemGraph()
    g.add_node(Node(name="app", type=NodeType.MODULE))
    g.add_node(Node(name="app.main", type=NodeType.FUNCTION))
    g.add_node(Node(name="app.Server", type=NodeType.CLASS))
    g.add_edge(Edge(source="app", target="app.main", rel=RelType.CONTAINS))
    g.add_edge(Edge(source="app", target="app.Server", rel=RelType.CONTAINS))
    g.add_edge(Edge(source="app.main", target="app.Server", rel=RelType.CALLS))
    return g


def test_add_node():
    g = SemGraph()
    g.add_node(Node(name="x", type=NodeType.MODULE))
    assert len(g) == 1
    assert g.get_node("x") is not None


def test_upsert_node():
    g = SemGraph()
    g.add_node(Node(name="x", type=NodeType.MODULE))
    g.add_node(Node(name="x", type=NodeType.CLASS, file="x.py"))
    assert len(g) == 1
    assert g.get_node("x").type == NodeType.CLASS
    assert g.get_node("x").file == "x.py"


def test_upsert_preserves_existing():
    g = SemGraph()
    g.add_node(Node(name="x", type=NodeType.MODULE, file="old.py", docstring="original"))
    g.add_node(Node(name="x", type=NodeType.CLASS))  # file=None, docstring=None
    node = g.get_node("x")
    assert node.type == NodeType.CLASS
    assert node.file == "old.py"  # preserved
    assert node.docstring == "original"  # preserved


def test_add_edge_missing_source():
    g = SemGraph()
    g.add_node(Node(name="b", type=NodeType.MODULE))
    with pytest.raises(NodeNotFoundError):
        g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))


def test_add_edge_missing_target():
    g = SemGraph()
    g.add_node(Node(name="a", type=NodeType.MODULE))
    with pytest.raises(NodeNotFoundError):
        g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))


def test_remove_node_cascades():
    g = _make_graph()
    g.remove_node("app.main")
    assert g.get_node("app.main") is None
    assert len(g.all_edges()) == 1  # only app->app.Server remains


def test_remove_node_not_found():
    g = SemGraph()
    with pytest.raises(NodeNotFoundError):
        g.remove_node("nope")


def test_remove_edge():
    g = _make_graph()
    g.remove_edge("app", "contains", "app.main")
    assert len(g.all_edges()) == 2


def test_outgoing():
    g = _make_graph()
    edges = g.outgoing("app")
    assert len(edges) == 2
    assert all(e.source == "app" for e in edges)


def test_outgoing_filtered():
    g = _make_graph()
    edges = g.outgoing("app.main", rel=RelType.CALLS)
    assert len(edges) == 1
    assert edges[0].target == "app.Server"


def test_incoming():
    g = _make_graph()
    edges = g.incoming("app.Server")
    assert len(edges) == 2  # contains + calls


def test_neighbors():
    g = _make_graph()
    assert set(g.neighbors("app.main")) == {"app", "app.Server"}


def test_resolve_name_exact():
    g = _make_graph()
    assert g.resolve_name("app") == ["app"]


def test_resolve_name_short():
    g = _make_graph()
    assert g.resolve_name("Server") == ["app.Server"]


def test_resolve_name_ambiguous():
    g = SemGraph()
    g.add_node(Node(name="a.Foo", type=NodeType.CLASS))
    g.add_node(Node(name="b.Foo", type=NodeType.CLASS))
    assert g.resolve_name("Foo") == ["a.Foo", "b.Foo"]


def test_all_nodes_filtered():
    g = _make_graph()
    classes = g.all_nodes(type=NodeType.CLASS)
    assert len(classes) == 1
    assert classes[0].name == "app.Server"


def test_validate_clean():
    g = _make_graph()
    assert g.validate() == []
