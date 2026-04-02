import json

from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType
from smg import export


def _sample_graph() -> SemGraph:
    g = SemGraph()
    g.add_node(Node(name="app", type=NodeType.MODULE))
    g.add_node(Node(name="app.main", type=NodeType.FUNCTION, file="app.py", line=1))
    g.add_edge(Edge(source="app", target="app.main", rel=RelType.CONTAINS))
    return g


def test_to_json():
    g = _sample_graph()
    result = json.loads(export.to_json(g))
    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1
    # No "kind" key in structured output
    assert "kind" not in result["nodes"][0]


def test_to_json_indent():
    g = _sample_graph()
    result = export.to_json(g, indent=True)
    assert "\n" in result


def test_to_text():
    g = _sample_graph()
    text = export.to_text(g)
    assert "Nodes (2):" in text
    assert "Edges (1):" in text
    assert "[module] app" in text


def test_to_text_empty():
    g = SemGraph()
    assert export.to_text(g) == "Empty graph."


def test_to_mermaid():
    g = _sample_graph()
    m = export.to_mermaid(g)
    assert m.startswith("graph TD")
    assert "-->|contains|" in m


def test_to_dot():
    g = _sample_graph()
    d = export.to_dot(g)
    assert d.startswith("digraph smg {")
    assert 'label="contains"' in d
    assert d.strip().endswith("}")


def test_format_node_text():
    g = _sample_graph()
    node = g.get_node("app.main")
    inc = g.incoming("app.main")
    out = g.outgoing("app.main")
    text = export.format_node(node, inc, out, fmt="text")
    assert "[function] app.main" in text
    assert "app.py:1" in text


def test_format_node_json():
    g = _sample_graph()
    node = g.get_node("app.main")
    inc = g.incoming("app.main")
    out = g.outgoing("app.main")
    result = json.loads(export.format_node(node, inc, out, fmt="json"))
    assert result["name"] == "app.main"
    assert "incoming" in result
    assert "outgoing" in result
