"""Tests for graph diffing."""

from smg.diff import diff_graphs
from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType


def _make_graph() -> SemGraph:
    g = SemGraph()
    g.add_node(Node(name="app", type=NodeType.MODULE))
    g.add_node(
        Node(
            name="app.main",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            docstring="Entry",
        )
    )
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
    new.add_node(
        Node(
            name="app.main",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            docstring="Entry",
        )
    )
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
    new.add_node(
        Node(
            name="app.main",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            docstring="Entry",
        )
    )
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
    new.add_node(
        Node(
            name="app.main",
            type=NodeType.FUNCTION,
            file="app.py",
            line=5,
            docstring="Updated",
        )
    )
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


def test_diff_rename_by_content_hash():
    """Exact content match detects a pure rename."""
    old = SemGraph()
    old.add_node(
        Node(
            name="app.foo",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            metadata={
                "content_hash": "abcd1234abcd1234",
                "structure_hash": "efgh5678efgh5678",
            },
        )
    )
    new = SemGraph()
    new.add_node(
        Node(
            name="app.bar",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            metadata={
                "content_hash": "abcd1234abcd1234",
                "structure_hash": "efgh5678efgh5678",
            },
        )
    )

    result = diff_graphs(old, new)
    assert len(result.renamed_nodes) == 1
    assert result.renamed_nodes[0].old_name == "app.foo"
    assert result.renamed_nodes[0].new_name == "app.bar"
    assert result.renamed_nodes[0].match_type == "content"
    assert len(result.added_nodes) == 0
    assert len(result.removed_nodes) == 0


def test_diff_rename_by_structure_hash():
    """Structure match detects a rename with minor content changes."""
    old = SemGraph()
    old.add_node(
        Node(
            name="app.foo",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            metadata={"content_hash": "aaaa", "structure_hash": "same_struct_hash"},
        )
    )
    new = SemGraph()
    new.add_node(
        Node(
            name="app.bar",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            metadata={"content_hash": "bbbb", "structure_hash": "same_struct_hash"},
        )
    )

    result = diff_graphs(old, new)
    assert len(result.renamed_nodes) == 1
    assert result.renamed_nodes[0].match_type == "structure"
    assert len(result.added_nodes) == 0
    assert len(result.removed_nodes) == 0


def test_diff_ambiguous_rename_skipped():
    """Two removed nodes with same structure hash — ambiguous, no rename detected."""
    old = SemGraph()
    old.add_node(
        Node(
            name="app.foo",
            type=NodeType.FUNCTION,
            metadata={"content_hash": "aaaa", "structure_hash": "same"},
        )
    )
    old.add_node(
        Node(
            name="app.baz",
            type=NodeType.FUNCTION,
            metadata={"content_hash": "cccc", "structure_hash": "same"},
        )
    )
    new = SemGraph()
    new.add_node(
        Node(
            name="app.bar",
            type=NodeType.FUNCTION,
            metadata={"content_hash": "bbbb", "structure_hash": "same"},
        )
    )

    result = diff_graphs(old, new)
    assert len(result.renamed_nodes) == 0
    assert len(result.added_nodes) == 1
    assert len(result.removed_nodes) == 2


def test_diff_no_hashes_no_renames():
    """Nodes without hashes fall through to normal add/remove."""
    old = SemGraph()
    old.add_node(Node(name="app.foo", type=NodeType.FUNCTION))
    new = SemGraph()
    new.add_node(Node(name="app.bar", type=NodeType.FUNCTION))

    result = diff_graphs(old, new)
    assert len(result.renamed_nodes) == 0
    assert len(result.added_nodes) == 1
    assert len(result.removed_nodes) == 1


def test_diff_rename_disabled():
    """detect_renames=False skips rename detection."""
    old = SemGraph()
    old.add_node(
        Node(
            name="app.foo",
            type=NodeType.FUNCTION,
            metadata={"content_hash": "same", "structure_hash": "same"},
        )
    )
    new = SemGraph()
    new.add_node(
        Node(
            name="app.bar",
            type=NodeType.FUNCTION,
            metadata={"content_hash": "same", "structure_hash": "same"},
        )
    )

    result = diff_graphs(old, new, detect_renames=False)
    assert len(result.renamed_nodes) == 0
    assert len(result.added_nodes) == 1
    assert len(result.removed_nodes) == 1


def test_diff_fuzzy_rename():
    """Phase 3: fuzzy Jaccard match on names with high token overlap (>= 0.8)."""
    old = SemGraph()
    # 4 tokens: app, utils, parse, config
    old.add_node(
        Node(
            name="app.utils.parse_config",
            type=NodeType.FUNCTION,
            metadata={"content_hash": "aaaa", "structure_hash": "xxxx"},
        )
    )
    new = SemGraph()
    # 5 tokens: app, utils, helpers, parse, config — intersection=4, union=5, J=0.8
    new.add_node(
        Node(
            name="app.utils.helpers.parse_config",
            type=NodeType.FUNCTION,
            metadata={"content_hash": "bbbb", "structure_hash": "yyyy"},
        )
    )

    result = diff_graphs(old, new)
    assert len(result.renamed_nodes) == 1
    assert result.renamed_nodes[0].match_type == "fuzzy"
    assert result.renamed_nodes[0].old_name == "app.utils.parse_config"
    assert result.renamed_nodes[0].new_name == "app.utils.helpers.parse_config"


def test_diff_fuzzy_no_match_different_type():
    """Phase 3: fuzzy matching only considers same-type entities."""
    old = SemGraph()
    old.add_node(Node(name="app.foo.bar", type=NodeType.FUNCTION))
    new = SemGraph()
    new.add_node(Node(name="app.foo.bar", type=NodeType.CLASS))

    result = diff_graphs(old, new)
    # Same name but different type — shows as changed, not renamed
    assert len(result.renamed_nodes) == 0


def test_diff_fuzzy_no_match_low_similarity():
    """Phase 3: low similarity names don't match."""
    old = SemGraph()
    old.add_node(Node(name="app.alpha.beta", type=NodeType.FUNCTION))
    new = SemGraph()
    new.add_node(Node(name="lib.gamma.delta", type=NodeType.FUNCTION))

    result = diff_graphs(old, new)
    assert len(result.renamed_nodes) == 0
    assert len(result.added_nodes) == 1
    assert len(result.removed_nodes) == 1


def test_diff_detects_content_hash_change():
    """Same name, different content_hash — must not be is_empty."""
    old = SemGraph()
    old.add_node(
        Node(
            name="app.foo",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            metadata={"content_hash": "aaaa", "structure_hash": "ssss"},
        )
    )
    new = SemGraph()
    new.add_node(
        Node(
            name="app.foo",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            metadata={"content_hash": "bbbb", "structure_hash": "ssss"},
        )
    )
    result = diff_graphs(old, new)
    assert not result.is_empty
    node, changes = result.changed_nodes[0]
    fields = {c.field for c in changes}
    assert "content_hash" in fields


def test_diff_detects_structure_hash_change():
    """Same name, different structure_hash — must not be is_empty."""
    old = SemGraph()
    old.add_node(
        Node(
            name="app.foo",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            metadata={"content_hash": "aaaa", "structure_hash": "s1"},
        )
    )
    new = SemGraph()
    new.add_node(
        Node(
            name="app.foo",
            type=NodeType.FUNCTION,
            file="app.py",
            line=1,
            metadata={"content_hash": "aaaa", "structure_hash": "s2"},
        )
    )
    result = diff_graphs(old, new)
    assert not result.is_empty
    _, changes = result.changed_nodes[0]
    fields = {c.field for c in changes}
    assert "structure_hash" in fields
