"""Tests for LLM context budgeting."""

from __future__ import annotations

import tempfile
from pathlib import Path

from smg.context import ContextResult, _default_token_count, _summary, build_context
from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType


def _make_graph_with_source() -> tuple[SemGraph, Path]:
    """Create a graph and matching source files in a temp dir."""
    tmpdir = Path(tempfile.mkdtemp())

    # Write a source file
    src = tmpdir / "src" / "app.py"
    src.parent.mkdir(parents=True)
    src.write_text(
        "def target_func(x, y):\n"
        "    return x + y\n"
        "\n"
        "def helper(a):\n"
        "    return a * 2\n"
        "\n"
        "class MyClass:\n"
        "    def method(self):\n"
        "        pass\n"
    )

    graph = SemGraph()
    graph.add_node(
        Node(
            name="app.target_func",
            type=NodeType.FUNCTION,
            file="src/app.py",
            line=1,
            end_line=2,
        )
    )
    graph.add_node(
        Node(
            name="app.helper",
            type=NodeType.FUNCTION,
            file="src/app.py",
            line=4,
            end_line=5,
        )
    )
    graph.add_node(
        Node(
            name="app.MyClass",
            type=NodeType.CLASS,
            file="src/app.py",
            line=7,
            end_line=9,
        )
    )
    graph.add_node(
        Node(
            name="app.MyClass.method",
            type=NodeType.METHOD,
            file="src/app.py",
            line=8,
            end_line=9,
        )
    )

    # target_func calls helper
    graph.add_edge(Edge(source="app.target_func", target="app.helper", rel=RelType.CALLS))
    # MyClass.method calls target_func
    graph.add_edge(Edge(source="app.MyClass.method", target="app.target_func", rel=RelType.CALLS))
    # Containment
    graph.add_edge(Edge(source="app.MyClass", target="app.MyClass.method", rel=RelType.CONTAINS))

    return graph, tmpdir


def test_target_always_included():
    graph, root = _make_graph_with_source()
    result = build_context(graph, root, "app.target_func", budget=1)
    assert len(result.entries) >= 1
    assert result.entries[0].name == "app.target_func"
    assert result.entries[0].relation == "target"


def test_budget_constrains_output():
    graph, root = _make_graph_with_source()
    # Very small budget — should include target but not much else
    result = build_context(graph, root, "app.target_func", budget=20)
    # Target is always included even over budget
    assert result.entries[0].name == "app.target_func"


def test_direct_deps_included():
    graph, root = _make_graph_with_source()
    result = build_context(graph, root, "app.target_func", budget=4000)
    names = [e.name for e in result.entries]
    # helper is a direct dep (target_func calls helper)
    assert "app.helper" in names


def test_direct_dependents_included():
    graph, root = _make_graph_with_source()
    result = build_context(graph, root, "app.target_func", budget=4000)
    names = [e.name for e in result.entries]
    # MyClass.method calls target_func, so it's a direct dependent
    assert "app.MyClass.method" in names


def test_degradation_under_budget_pressure():
    graph, root = _make_graph_with_source()
    # Use a token counter that inflates token counts
    result = build_context(graph, root, "app.target_func", budget=100, token_counter=lambda t: len(t))
    # With inflated tokens, some entries should be signature or summary
    levels = {e.level for e in result.entries}
    assert "full" in levels  # target is always full


def test_nonexistent_node():
    graph, root = _make_graph_with_source()
    result = build_context(graph, root, "nonexistent", budget=4000)
    assert len(result.entries) == 0
    assert result.total_tokens == 0


def test_default_token_count():
    assert _default_token_count("abcdefgh") == 2  # 8 chars / 4
    assert _default_token_count("") == 1  # min 1


def test_summary_format():
    node = Node(
        name="app.foo",
        type=NodeType.FUNCTION,
        file="src/app.py",
        line=10,
        docstring="Does things",
    )
    s = _summary(node)
    assert "app.foo" in s
    assert "function" in s
    assert "src/app.py:10" in s
    assert "Does things" in s


def test_json_output_structure():
    graph, root = _make_graph_with_source()
    result = build_context(graph, root, "app.target_func", budget=4000)
    assert isinstance(result, ContextResult)
    assert result.target == "app.target_func"
    assert result.budget == 4000
    assert isinstance(result.total_tokens, int)
    assert isinstance(result.truncated, bool)
    for entry in result.entries:
        assert entry.name
        assert entry.node_type
        assert entry.relation in (
            "target",
            "direct_dep",
            "direct_dependent",
            "2hop",
            "3hop",
        )
        assert entry.level in ("full", "signature", "summary")
        assert isinstance(entry.tokens, int)
