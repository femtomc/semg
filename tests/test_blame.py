"""Tests for entity-level blame."""

from __future__ import annotations

from smg.blame import BlameEntry, blame_entity, blame_file
from smg.graph import SemGraph
from smg.model import Node, NodeType


def test_blame_entity_no_file():
    node = Node(name="app", type=NodeType.MODULE)
    result = blame_entity(node, root="/nonexistent")
    assert result is None


def test_blame_entity_no_line():
    node = Node(name="app.foo", type=NodeType.FUNCTION, file="app.py")
    result = blame_entity(node, root="/nonexistent")
    assert result is None


def test_blame_file_empty_graph():
    graph = SemGraph()
    result = blame_file(graph, "app.py", root="/nonexistent")
    assert result == []


def test_blame_file_no_matching_file():
    graph = SemGraph()
    graph.add_node(Node(name="app.foo", type=NodeType.FUNCTION, file="other.py", line=1, end_line=5))
    result = blame_file(graph, "app.py", root="/nonexistent")
    assert result == []


def test_blame_entry_dataclass():
    entry = BlameEntry(
        name="app.foo",
        node_type="function",
        file="app.py",
        line=1,
        end_line=5,
        commit="abc123",
        author="test@example.com",
        date="2024-01-01",
        summary="Fix bug",
    )
    assert entry.name == "app.foo"
    assert entry.commit == "abc123"
    assert entry.date == "2024-01-01"
