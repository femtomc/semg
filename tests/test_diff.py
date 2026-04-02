"""Tests for graph diffing."""
from smg.diff import GraphDiff, diff_graphs
from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType


def _make_graph() -> SemGraph:
    g = SemGraph()
    g.add_node(Node(name="app", type=NodeType.MODULE))
    g.add_node(Node(name="app.main", type=NodeType.FUNCTION, file="app.py", line=1, docstring="Entry"))
    g.add_node(Node(name="app.Server", type=NodeType.CLASS, file="app.py", line=10))
    g.add_edge(Edge(source="app", target="app.main", rel=RelType.CONTAINS))
    g.add_edge(Edge(source="app", target="app.Server", rel=RelType.CONTAINS))
    return g


def test_diff_identical():
    g = _make_graph()
    result = diff_graphs(g, g)
    assert result.is_empty


def test_diff_added_node():
    old = _make_graph()
    new = _make_graph()
    new.add_node(Node(name="app.helper", type=NodeType.FUNCTION))
    result = diff_graphs(old, new)
    assert len(result.added_nodes) == 1
    assert result.added_nodes[0].name == "app.helper"
    assert len(result.removed_nodes) == 0


def test_diff_removed_node():
    old = _make_graph()
    new = SemGraph()
    new.add_node(Node(name="app", type=NodeType.MODULE))
    new.add_node(Node(name="app.main", type=NodeType.FUNCTION, file="app.py", line=1, docstring="Entry"))
    new.add_edge(Edge(source="app", target="app.main", rel=RelType.CONTAINS))
    result = diff_graphs(old, new)
    assert len(result.removed_nodes) == 1
    assert result.removed_nodes[0].name == "app.Server"


def test_diff_changed_node_type():
    old = _make_graph()
    new = _make_graph()
    new.nodes["app.Server"].type = NodeType.INTERFACE
    result = diff_graphs(old, new)
    assert len(result.changed_nodes) == 1
    node, changes = result.changed_nodes[0]
    assert node.name == "app.Server"
    assert changes[0].field == "type"
    assert changes[0].old == "class"
    assert changes[0].new == "interface"


def test_diff_changed_node_docstring():
    old = _make_graph()
    new = _make_graph()
    new.nodes["app.main"].docstring = "New docstring"
    result = diff_graphs(old, new)
    assert len(result.changed_nodes) == 1
    _, changes = result.changed_nodes[0]
    assert changes[0].field == "docstring"
    assert changes[0].old == "Entry"
    assert changes[0].new == "New docstring"


def test_diff_changed_node_line():
    old = _make_graph()
    new = _make_graph()
    new.nodes["app.main"].line = 5
    result = diff_graphs(old, new)
    assert len(result.changed_nodes) == 1
    _, changes = result.changed_nodes[0]
    assert changes[0].field == "line"


def test_diff_added_edge():
    old = _make_graph()
    new = _make_graph()
    new.add_edge(Edge(source="app.main", target="app.Server", rel=RelType.CALLS))
    result = diff_graphs(old, new)
    assert len(result.added_edges) == 1
    assert result.added_edges[0].rel == RelType.CALLS


def test_diff_removed_edge():
    old = _make_graph()
    new = SemGraph()
    new.add_node(Node(name="app", type=NodeType.MODULE))
    new.add_node(Node(name="app.main", type=NodeType.FUNCTION, file="app.py", line=1, docstring="Entry"))
    new.add_node(Node(name="app.Server", type=NodeType.CLASS, file="app.py", line=10))
    # Only one edge instead of two
    new.add_edge(Edge(source="app", target="app.main", rel=RelType.CONTAINS))
    result = diff_graphs(old, new)
    assert len(result.removed_edges) == 1
    assert result.removed_edges[0].target == "app.Server"


def test_diff_empty_vs_populated():
    old = SemGraph()
    new = _make_graph()
    result = diff_graphs(old, new)
    assert len(result.added_nodes) == 3
    assert len(result.added_edges) == 2
    assert len(result.removed_nodes) == 0


def test_diff_populated_vs_empty():
    old = _make_graph()
    new = SemGraph()
    result = diff_graphs(old, new)
    assert len(result.removed_nodes) == 3
    assert len(result.removed_edges) == 2
    assert len(result.added_nodes) == 0


def test_diff_multiple_changes():
    """Complex diff with adds, removes, and changes."""
    old = _make_graph()
    new = SemGraph()
    # Keep app, modify main, remove Server, add helper
    new.add_node(Node(name="app", type=NodeType.MODULE))
    new.add_node(Node(name="app.main", type=NodeType.FUNCTION, file="app.py", line=5, docstring="Updated"))
    new.add_node(Node(name="app.helper", type=NodeType.FUNCTION))
    new.add_edge(Edge(source="app", target="app.main", rel=RelType.CONTAINS))
    new.add_edge(Edge(source="app", target="app.helper", rel=RelType.CONTAINS))
    new.add_edge(Edge(source="app.main", target="app.helper", rel=RelType.CALLS))

    result = diff_graphs(old, new)
    assert len(result.added_nodes) == 1  # helper
    assert result.added_nodes[0].name == "app.helper"
    assert len(result.removed_nodes) == 1  # Server
    assert result.removed_nodes[0].name == "app.Server"
    assert len(result.changed_nodes) == 1  # main (line + docstring changed)
    assert len(result.added_edges) == 2  # app->helper, main->helper
    assert len(result.removed_edges) == 1  # app->Server


def test_diff_cli(tmp_path):
    """CLI diff against HEAD when no git history exists."""
    import json
    import os

    from click.testing import CliRunner

    from smg.cli import main

    os.chdir(tmp_path)
    runner = CliRunner()
    # init a git repo
    os.system(f"git init {tmp_path} -q")
    runner.invoke(main, ["init"])
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["diff"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    # No baseline in git, so everything is "added"
    assert data["summary"]["nodes_added"] == 1
