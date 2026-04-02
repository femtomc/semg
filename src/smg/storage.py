from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from smg.graph import SemGraph
from smg.model import Edge, Node
from smg.rules import Rule

SMG_DIR = ".smg"
GRAPH_FILE = "graph.jsonl"
RULES_FILE = "rules"


def find_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` looking for a `.smg/` directory."""
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / SMG_DIR).is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def init_project(path: Path | None = None) -> Path:
    """Create .smg/ directory and empty graph file. Return the project root."""
    root = (path or Path.cwd()).resolve()
    smg_dir = root / SMG_DIR
    smg_dir.mkdir(exist_ok=True)
    graph_file = smg_dir / GRAPH_FILE
    if not graph_file.exists():
        graph_file.touch()
    return root


def load_graph(root: Path) -> SemGraph:
    """Read .smg/graph.jsonl and return a SemGraph."""
    graph = SemGraph()
    graph_file = root / SMG_DIR / GRAPH_FILE
    if not graph_file.exists():
        return graph

    nodes: list[Node] = []
    edges: list[Edge] = []

    with open(graph_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            kind = record.get("kind")
            if kind == "node":
                nodes.append(Node.from_dict(record))
            elif kind == "edge":
                edges.append(Edge.from_dict(record))

    # Load nodes first, then edges (edges require nodes to exist)
    for node in nodes:
        graph.add_node(node)
    for edge in edges:
        graph.add_edge(edge)

    return graph


def save_graph(graph: SemGraph, root: Path) -> None:
    """Serialize graph to .smg/graph.jsonl atomically."""
    smg_dir = root / SMG_DIR
    graph_file = smg_dir / GRAPH_FILE

    lines: list[str] = []
    # Nodes first (sorted by name), then edges (sorted by key)
    for node in graph.all_nodes():
        lines.append(node.to_json())
    for edge in graph.all_edges():
        lines.append(edge.to_json())

    # Atomic write: write to temp file, then rename
    fd, tmp_path = tempfile.mkstemp(dir=smg_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for line in lines:
                f.write(line + "\n")
        os.replace(tmp_path, graph_file)
    except BaseException:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_rules(root: Path) -> list[Rule]:
    """Read .smg/rules and return a list of Rule objects."""
    rules_file = root / SMG_DIR / RULES_FILE
    if not rules_file.exists():
        return []
    rules: list[Rule] = []
    with open(rules_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rules.append(Rule.from_dict(json.loads(line)))
    return rules


def save_rules(rules: list[Rule], root: Path) -> None:
    """Serialize rules to .smg/rules atomically."""
    smg_dir = root / SMG_DIR
    rules_file = smg_dir / RULES_FILE

    lines = [r.to_json() for r in sorted(rules, key=lambda r: r.name)]

    fd, tmp_path = tempfile.mkstemp(dir=smg_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for line in lines:
                f.write(line + "\n")
        os.replace(tmp_path, rules_file)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
