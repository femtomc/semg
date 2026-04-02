from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from smg.graph import SemGraph
from smg.model import Edge, Node
from smg.storage import GRAPH_FILE, SMG_DIR, load_graph


@dataclass
class NodeChange:
    name: str
    field: str
    old: str | None
    new: str | None


@dataclass
class GraphDiff:
    added_nodes: list[Node] = field(default_factory=list)
    removed_nodes: list[Node] = field(default_factory=list)
    changed_nodes: list[tuple[Node, list[NodeChange]]] = field(default_factory=list)
    added_edges: list[Edge] = field(default_factory=list)
    removed_edges: list[Edge] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.added_nodes
            or self.removed_nodes
            or self.changed_nodes
            or self.added_edges
            or self.removed_edges
        )


def diff_graphs(old: SemGraph, new: SemGraph) -> GraphDiff:
    """Compare two graphs and return structural differences."""
    result = GraphDiff()

    old_names = set(old.nodes.keys())
    new_names = set(new.nodes.keys())

    # Added / removed nodes
    for name in sorted(new_names - old_names):
        result.added_nodes.append(new.nodes[name])
    for name in sorted(old_names - new_names):
        result.removed_nodes.append(old.nodes[name])

    # Changed nodes (same name, different fields)
    for name in sorted(old_names & new_names):
        changes = _diff_node(old.nodes[name], new.nodes[name])
        if changes:
            result.changed_nodes.append((new.nodes[name], changes))

    # Added / removed edges
    old_keys = set(old.edges.keys())
    new_keys = set(new.edges.keys())

    for key in sorted(new_keys - old_keys):
        result.added_edges.append(new.edges[key])
    for key in sorted(old_keys - new_keys):
        result.removed_edges.append(old.edges[key])

    return result


def _diff_node(old: Node, new: Node) -> list[NodeChange]:
    """Compare two nodes with the same name, return list of field changes."""
    changes: list[NodeChange] = []
    if old.type.value != new.type.value:
        changes.append(NodeChange(old.name, "type", old.type.value, new.type.value))
    if old.file != new.file:
        changes.append(NodeChange(old.name, "file", old.file, new.file))
    if old.line != new.line:
        changes.append(NodeChange(old.name, "line", str(old.line), str(new.line)))
    if old.docstring != new.docstring:
        changes.append(NodeChange(old.name, "docstring", old.docstring, new.docstring))
    return changes


def load_graph_from_git(root: Path, ref: str = "HEAD") -> SemGraph | None:
    """Load a graph from a git ref (e.g., HEAD, HEAD~1, main, abc123).

    Returns None if the file doesn't exist at that ref.
    """
    graph_path = f"{SMG_DIR}/{GRAPH_FILE}"
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{graph_path}"],
            capture_output=True,
            text=True,
            cwd=root,
        )
    except FileNotFoundError:
        return None  # git not installed

    if result.returncode != 0:
        return None  # file doesn't exist at that ref

    # Parse JSONL from stdout
    import json

    graph = SemGraph()
    nodes: list[Node] = []
    edges: list[Edge] = []

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        kind = record.get("kind")
        if kind == "node":
            nodes.append(Node.from_dict(record))
        elif kind == "edge":
            edges.append(Edge.from_dict(record))

    for node in nodes:
        graph.add_node(node)
    for edge in edges:
        graph.add_edge(edge)

    return graph
