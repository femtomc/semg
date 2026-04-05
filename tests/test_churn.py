"""Tests for git-history churn analysis."""

from __future__ import annotations

from smg.churn import _build_file_index, _parse_range, _parse_unified_diff
from smg.graph import SemGraph
from smg.model import Node, NodeType


def test_parse_range_with_count():
    assert _parse_range("10,5") == (10, 5)


def test_parse_range_without_count():
    assert _parse_range("42") == (42, 1)


def test_parse_unified_diff_basic():
    diff_output = """\
commit abc123def456
Author: Test <test@example.com>
Date:   Mon Jan 1 00:00:00 2024

    Fix thing

diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -10,3 +10,5 @@ def foo():
"""
    hunks = _parse_unified_diff(diff_output)
    assert len(hunks) == 1
    assert hunks[0].commit == "abc123def456"
    assert hunks[0].file == "src/foo.py"
    assert hunks[0].start_line == 10
    assert hunks[0].end_line == 14  # 10 + 5 - 1


def test_parse_unified_diff_multiple_hunks():
    diff_output = """\
commit aaa111
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,3 @@ something
@@ -20,1 +21,1 @@ other
commit bbb222
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -5,0 +5,2 @@ thing
"""
    hunks = _parse_unified_diff(diff_output)
    assert len(hunks) == 3
    assert hunks[0].commit == "aaa111"
    assert hunks[0].file == "a.py"
    assert hunks[1].commit == "aaa111"
    assert hunks[1].file == "a.py"
    assert hunks[2].commit == "bbb222"
    assert hunks[2].file == "b.py"


def test_build_file_index():
    graph = SemGraph()
    graph.add_node(
        Node(
            name="mod.foo",
            type=NodeType.FUNCTION,
            file="src/mod.py",
            line=10,
            end_line=20,
        )
    )
    graph.add_node(
        Node(
            name="mod.bar",
            type=NodeType.FUNCTION,
            file="src/mod.py",
            line=25,
            end_line=30,
        )
    )
    graph.add_node(
        Node(
            name="other.baz",
            type=NodeType.FUNCTION,
            file="src/other.py",
            line=1,
            end_line=5,
        )
    )

    index = _build_file_index(graph)
    assert "src/mod.py" in index
    assert "src/other.py" in index
    assert len(index["src/mod.py"]) == 2
    # Should be sorted by start line
    assert index["src/mod.py"][0][0] == 10
    assert index["src/mod.py"][1][0] == 25


def test_build_file_index_skips_nodes_without_location():
    graph = SemGraph()
    graph.add_node(Node(name="mod", type=NodeType.MODULE))
    index = _build_file_index(graph)
    assert len(index) == 0


def test_churn_deduplicates_hunks_per_commit():
    """Two hunks in the same function in one commit should count as one touch."""
    diff_output = """\
commit aaa111
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -2,1 +2,2 @@ def foo():
@@ -8,1 +9,2 @@ def foo():
"""

    graph = SemGraph()
    graph.add_node(Node(name="mod.foo", type=NodeType.FUNCTION, file="a.py", line=1, end_line=15))

    # Patch _git_hunks to return our controlled output
    hunks = _parse_unified_diff(diff_output)
    file_index = _build_file_index(graph)

    from collections import defaultdict

    entity_touches: set[tuple[str, str]] = set()
    for hunk in hunks:
        if hunk.file not in file_index:
            continue
        for start, end, name in file_index[hunk.file]:
            if start <= hunk.end_line and hunk.start_line <= end:
                entity_touches.add((hunk.commit, name))

    entity_churn: dict[str, int] = defaultdict(int)
    for _, name in entity_touches:
        entity_churn[name] += 1

    # Two hunks in the same commit -> one touch, not two
    assert entity_churn["mod.foo"] == 1


def test_churn_counts_across_commits():
    """Same function modified in two commits should count as two touches."""
    diff_output = """\
commit aaa111
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -2,1 +2,2 @@ def foo():
commit bbb222
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -5,1 +6,2 @@ def foo():
"""
    from collections import defaultdict

    graph = SemGraph()
    graph.add_node(Node(name="mod.foo", type=NodeType.FUNCTION, file="a.py", line=1, end_line=15))

    hunks = _parse_unified_diff(diff_output)
    file_index = _build_file_index(graph)

    entity_touches: set[tuple[str, str]] = set()
    for hunk in hunks:
        if hunk.file not in file_index:
            continue
        for start, end, name in file_index[hunk.file]:
            if start <= hunk.end_line and hunk.start_line <= end:
                entity_touches.add((hunk.commit, name))

    entity_churn: dict[str, int] = defaultdict(int)
    for _, name in entity_touches:
        entity_churn[name] += 1

    assert entity_churn["mod.foo"] == 2
