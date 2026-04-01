import json

from semg.model import Edge, Node, NodeType, RelType


def test_node_type_known():
    assert NodeType("function") == NodeType.FUNCTION
    assert NodeType("class").value == "class"


def test_node_type_custom():
    t = NodeType("widget")
    assert t.value == "widget"
    assert isinstance(t, NodeType)


def test_rel_type_known():
    assert RelType("calls") == RelType.CALLS


def test_rel_type_custom():
    r = RelType("wraps")
    assert r.value == "wraps"


def test_node_roundtrip():
    node = Node(name="app.main", type=NodeType.FUNCTION, file="app.py", line=1, docstring="entry")
    d = node.to_dict()
    assert d["kind"] == "node"
    restored = Node.from_dict(d)
    assert restored.name == node.name
    assert restored.type == node.type
    assert restored.file == node.file
    assert restored.line == node.line
    assert restored.docstring == node.docstring


def test_node_json_compact():
    node = Node(name="x", type=NodeType.MODULE)
    j = node.to_json()
    parsed = json.loads(j)
    assert parsed["name"] == "x"
    assert " " not in j  # compact


def test_node_omits_none():
    node = Node(name="x", type=NodeType.MODULE)
    d = node.to_dict()
    assert "file" not in d
    assert "line" not in d
    assert "docstring" not in d
    assert "metadata" not in d


def test_edge_roundtrip():
    edge = Edge(source="a", target="b", rel=RelType.CALLS, metadata={"async": True})
    d = edge.to_dict()
    assert d["kind"] == "edge"
    restored = Edge.from_dict(d)
    assert restored.source == edge.source
    assert restored.target == edge.target
    assert restored.rel == edge.rel
    assert restored.metadata == {"async": True}


def test_edge_key():
    edge = Edge(source="a", target="b", rel=RelType.CALLS)
    assert edge.key == ("a", "calls", "b")
