"""Tests for provenance tracking, smart clean, orphan detection, and incremental scan."""
import json
import os
from pathlib import Path

import pytest

from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType
from smg.scan import scan_paths
from smg.storage import init_project, load_graph, save_graph

try:
    import tree_sitter_python
    HAS_TS = True
except ImportError:
    HAS_TS = False

needs_tree_sitter = pytest.mark.skipif(not HAS_TS, reason="tree-sitter-python not installed")


def _write_project(tmp_path):
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text("""\
def helper():
    pass

def main():
    helper()
""")
    (pkg / "utils.py").write_text("""\
def format_name(name):
    return name.strip()
""")
    return tmp_path


# --- Provenance tracking ---


@needs_tree_sitter
def test_scan_sets_provenance_on_nodes(tmp_path):
    root = _write_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    node = graph.get_node("app.core.helper")
    assert node is not None
    assert node.metadata.get("source") == "scan"


@needs_tree_sitter
def test_scan_sets_provenance_on_edges(tmp_path):
    root = _write_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.core", rel=RelType.CONTAINS)
    assert len(edges) > 0
    for edge in edges:
        assert edge.metadata.get("source") == "scan"


@needs_tree_sitter
def test_manual_add_sets_provenance(tmp_path):
    from click.testing import CliRunner
    from smg.cli import main

    root = _write_project(tmp_path)
    os.chdir(root)
    runner = CliRunner()
    runner.invoke(main, ["init"])
    runner.invoke(main, ["add", "endpoint", "/api/test"])

    result = runner.invoke(main, ["show", "/api/test"])
    data = json.loads(result.output)
    assert data["metadata"]["source"] == "manual"


@needs_tree_sitter
def test_manual_link_sets_provenance(tmp_path):
    from click.testing import CliRunner
    from smg.cli import main

    root = _write_project(tmp_path)
    os.chdir(root)
    runner = CliRunner()
    runner.invoke(main, ["init"])
    runner.invoke(main, ["add", "module", "a"])
    runner.invoke(main, ["add", "module", "b"])
    runner.invoke(main, ["link", "a", "tests", "b"])

    result = runner.invoke(main, ["query", "outgoing", "a", "--rel", "tests"])
    edges = json.loads(result.output)
    assert len(edges) == 1
    assert edges[0].get("metadata", {}).get("source") == "manual"


# --- Smart clean: preserves manual nodes ---


@needs_tree_sitter
def test_smart_clean_preserves_manual_nodes(tmp_path):
    root = _write_project(tmp_path)
    init_project(root)
    graph = load_graph(root)

    # First scan
    scan_paths(graph, root, [root / "src"])

    # Manually add a node with the same file
    manual_node = Node(
        name="app.core.MY_CUSTOM",
        type=NodeType.CONSTANT,
        file="src/app/core.py",
        metadata={"source": "manual"},
    )
    graph.add_node(manual_node)
    save_graph(graph, root)

    # Rescan with clean
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"], clean=True)

    # Manual node should survive
    node = graph.get_node("app.core.MY_CUSTOM")
    assert node is not None
    assert node.metadata.get("source") == "manual"


@needs_tree_sitter
def test_smart_clean_removes_scan_nodes(tmp_path):
    root = _write_project(tmp_path)
    init_project(root)
    graph = load_graph(root)

    # First scan
    scan_paths(graph, root, [root / "src"])
    assert graph.get_node("app.core.helper") is not None

    # Remove helper from source
    (root / "src" / "app" / "core.py").write_text("def main():\n    pass\n")

    # Rescan with clean — helper should be gone
    scan_paths(graph, root, [root / "src"], clean=True)
    assert graph.get_node("app.core.helper") is None
    assert graph.get_node("app.core.main") is not None


# --- Orphan detection ---


@needs_tree_sitter
def test_orphan_detection(tmp_path):
    root = _write_project(tmp_path)
    init_project(root)
    graph = load_graph(root)

    # Scan to populate
    scan_paths(graph, root, [root / "src"])

    # Add a manual edge from a scan node to a manual node
    manual_target = Node(name="external.service", type=NodeType.CLASS, metadata={"source": "manual"})
    graph.add_node(manual_target)
    graph.add_edge(Edge(
        source="app.core.helper",
        target="external.service",
        rel=RelType.CALLS,
        metadata={"source": "manual"},
    ))
    save_graph(graph, root)

    # Now remove helper from source
    (root / "src" / "app" / "core.py").write_text("def main():\n    pass\n")

    # Rescan with clean — should report the orphaned manual edge
    graph = load_graph(root)
    stats = scan_paths(graph, root, [root / "src"], clean=True)

    assert len(stats.orphaned_manual_edges) >= 1
    orphaned = stats.orphaned_manual_edges[0]
    assert orphaned["source"] == "app.core.helper"
    assert orphaned["target"] == "external.service"
    assert orphaned["rel"] == "calls"


@needs_tree_sitter
def test_no_orphans_when_manual_edges_intact(tmp_path):
    root = _write_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    # Add manual edge between two scan nodes
    graph.add_edge(Edge(
        source="app.core.helper",
        target="app.core.main",
        rel=RelType.TESTS,
        metadata={"source": "manual"},
    ))
    save_graph(graph, root)

    # Rescan same files (no code change) — nodes still exist, edge not orphaned
    graph = load_graph(root)
    stats = scan_paths(graph, root, [root / "src"], clean=True)

    # Both nodes still exist after rescan, so no orphans
    assert graph.get_node("app.core.helper") is not None
    assert graph.get_node("app.core.main") is not None


# --- ScanStats ---


@needs_tree_sitter
def test_stats_track_removals(tmp_path):
    root = _write_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])
    save_graph(graph, root)

    # Remove a function from source
    (root / "src" / "app" / "core.py").write_text("def main():\n    pass\n")

    graph = load_graph(root)
    stats = scan_paths(graph, root, [root / "src"], clean=True)

    assert stats.nodes_removed > 0


# --- CLI --changed flag ---


@needs_tree_sitter
def test_scan_changed_cli(tmp_path):
    from click.testing import CliRunner
    from smg.cli import main

    root = tmp_path
    os.chdir(root)
    os.system(f"git init -q {root}")
    os.system(f"git -C {root} config user.email test@test.com")
    os.system(f"git -C {root} config user.name test")

    # Create project and initial commit
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text("def hello():\n    pass\n")

    os.system(f"git -C {root} add -A && git -C {root} commit -q -m init")

    runner = CliRunner()
    runner.invoke(main, ["init"])
    runner.invoke(main, ["scan", "src/"])

    # Modify a file
    (pkg / "core.py").write_text("def hello():\n    pass\n\ndef world():\n    pass\n")

    # Scan only changed files
    result = runner.invoke(main, ["scan", "--changed"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["files"] >= 1


@needs_tree_sitter
def test_scan_since_cli(tmp_path):
    from click.testing import CliRunner
    from smg.cli import main

    root = tmp_path
    os.chdir(root)
    os.system(f"git init -q {root}")
    os.system(f"git -C {root} config user.email test@test.com")
    os.system(f"git -C {root} config user.name test")

    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text("def hello():\n    pass\n")

    os.system(f"git -C {root} add -A && git -C {root} commit -q -m init")

    runner = CliRunner()
    runner.invoke(main, ["init"])
    runner.invoke(main, ["scan", "src/"])

    # Make change and commit
    (pkg / "core.py").write_text("def hello():\n    pass\n\ndef added():\n    pass\n")
    os.system(f"git -C {root} add -A && git -C {root} commit -q -m change")

    # Scan since previous commit
    result = runner.invoke(main, ["scan", "--since", "HEAD~1"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["files"] >= 1


# --- Batch provenance ---


@needs_tree_sitter
def test_batch_sets_provenance(tmp_path):
    from click.testing import CliRunner
    from smg.cli import main

    os.chdir(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["init"])

    commands = '{"op":"add","type":"module","name":"x"}\n{"op":"add","type":"module","name":"y"}\n{"op":"link","source":"x","rel":"imports","target":"y"}'
    runner.invoke(main, ["batch"], input=commands)

    result = runner.invoke(main, ["show", "x"])
    data = json.loads(result.output)
    assert data["metadata"]["source"] == "manual"
