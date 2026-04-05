from __future__ import annotations

import json
import re

from smg.graph import SemGraph
from smg.model import Edge, Node


def to_json(graph: SemGraph, indent: bool = False) -> str:
    """Agent-optimized JSON: {"nodes": [...], "edges": [...]}"""
    data = {
        "nodes": [n.to_dict() for n in graph.all_nodes()],
        "edges": [e.to_dict() for e in graph.all_edges()],
    }
    # Strip "kind" from individual records — redundant in structured output
    for n in data["nodes"]:
        n.pop("kind", None)
    for e in data["edges"]:
        e.pop("kind", None)
    if indent:
        return json.dumps(data, indent=2)
    return json.dumps(data, separators=(",", ":"))


def to_text(graph: SemGraph) -> str:
    """Human-readable text listing."""
    lines: list[str] = []

    nodes = graph.all_nodes()
    if not nodes:
        return "Empty graph."

    lines.append(f"Nodes ({len(nodes)}):")
    for node in nodes:
        parts = [f"  [{node.type.value}] {node.name}"]
        if node.file:
            loc = node.file
            if node.line is not None:
                loc += f":{node.line}"
            parts.append(f"    @ {loc}")
        if node.docstring:
            parts.append(f"    # {node.docstring}")
        lines.append("\n".join(parts))

    edges = graph.all_edges()
    if edges:
        lines.append(f"\nEdges ({len(edges)}):")
        for edge in edges:
            lines.append(f"  {edge.source} --{edge.rel.value}--> {edge.target}")

    return "\n".join(lines)


def to_mermaid(graph: SemGraph) -> str:
    """Mermaid flowchart syntax."""
    lines: list[str] = ["graph TD"]

    for node in graph.all_nodes():
        mid = _mermaid_id(node.name)
        label = f"{node.name} ({node.type.value})"
        lines.append(f'    {mid}["{label}"]')

    for edge in graph.all_edges():
        src = _mermaid_id(edge.source)
        tgt = _mermaid_id(edge.target)
        lines.append(f"    {src} -->|{edge.rel.value}| {tgt}")

    return "\n".join(lines)


def to_dot(graph: SemGraph) -> str:
    """Graphviz DOT syntax."""
    lines: list[str] = ["digraph smg {", "    rankdir=LR;"]

    for node in graph.all_nodes():
        did = _dot_id(node.name)
        label = f"{node.name}\\n({node.type.value})"
        lines.append(f'    {did} [label="{label}"];')

    for edge in graph.all_edges():
        src = _dot_id(edge.source)
        tgt = _dot_id(edge.target)
        lines.append(f'    {src} -> {tgt} [label="{edge.rel.value}"];')

    lines.append("}")
    return "\n".join(lines)


def format_node(node: Node, incoming: list[Edge], outgoing: list[Edge], fmt: str = "text") -> str:
    """Format a single node and its connections."""
    if fmt == "json":
        data = node.to_dict()
        data.pop("kind", None)
        data["incoming"] = [{"source": e.source, "rel": e.rel.value} for e in incoming]
        data["outgoing"] = [{"target": e.target, "rel": e.rel.value} for e in outgoing]
        return json.dumps(data, indent=2)

    lines: list[str] = []
    lines.append(f"[{node.type.value}] {node.name}")
    if node.file:
        loc = node.file
        if node.line is not None:
            loc += f":{node.line}"
        lines.append(f"  file: {loc}")
    if node.docstring:
        lines.append(f"  doc:  {node.docstring}")
    if node.metadata:
        for k, v in sorted(node.metadata.items()):
            lines.append(f"  {k}: {v}")

    if incoming:
        lines.append(f"\n  Incoming ({len(incoming)}):")
        for e in incoming:
            lines.append(f"    {e.source} --{e.rel.value}--> {node.name}")

    if outgoing:
        lines.append(f"\n  Outgoing ({len(outgoing)}):")
        for e in outgoing:
            lines.append(f"    {node.name} --{e.rel.value}--> {e.target}")

    return "\n".join(lines)


def to_dsm(graph: SemGraph, level: str = "module") -> str:
    """Dependency Structure Matrix as CSV.

    Rows and columns are nodes at the given granularity (default: module).
    Cell (i,j) = number of coupling edges from node i to node j.
    Non-zero cells indicate dependencies; the diagonal is always 0.
    """
    from collections import defaultdict

    from smg.model import NodeType, RelType

    coupling_rels = frozenset(
        {
            RelType.CALLS.value,
            RelType.IMPORTS.value,
            RelType.INHERITS.value,
            RelType.IMPLEMENTS.value,
            RelType.DEPENDS_ON.value,
        }
    )

    # Select nodes at the requested granularity
    if level == "module":
        target_types = {NodeType.MODULE.value, NodeType.PACKAGE.value}
    elif level == "class":
        target_types = {
            NodeType.MODULE.value,
            NodeType.PACKAGE.value,
            NodeType.CLASS.value,
        }
    else:  # "all"
        target_types = None

    if target_types is not None:
        names = sorted(n.name for n in graph.all_nodes() if n.type.value in target_types)
    else:
        names = sorted(n.name for n in graph.all_nodes())

    if not names:
        return ""

    name_set = frozenset(names)

    # For module/class level, map each node to its nearest ancestor in the DSM
    node_to_dsm: dict[str, str] = {}
    if target_types is not None:
        for node in graph.all_nodes():
            if node.name in name_set:
                node_to_dsm[node.name] = node.name
            else:
                # Walk up containment to find the DSM-level ancestor
                current = node.name
                while True:
                    parents = [e.source for e in graph.incoming(current, rel=RelType.CONTAINS)]
                    if not parents:
                        break
                    parent = parents[0]
                    if parent in name_set:
                        node_to_dsm[node.name] = parent
                        break
                    current = parent
    else:
        for name in names:
            node_to_dsm[name] = name

    # Build the matrix
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for edge in graph.all_edges():
        if edge.rel.value not in coupling_rels:
            continue
        src_dsm = node_to_dsm.get(edge.source)
        tgt_dsm = node_to_dsm.get(edge.target)
        if src_dsm and tgt_dsm and src_dsm != tgt_dsm:
            matrix[src_dsm][tgt_dsm] += 1

    # Render as CSV
    lines: list[str] = []
    header = [""] + names
    lines.append(",".join(header))
    for row in names:
        cells = [row]
        for col in names:
            cells.append(str(matrix[row][col]))
        lines.append(",".join(cells))

    return "\n".join(lines)


def _mermaid_id(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _dot_id(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)
