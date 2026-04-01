from __future__ import annotations

import json
import re

from semg.graph import SemGraph
from semg.model import Edge, Node


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
        lines.append(f"    {mid}[\"{label}\"]")

    for edge in graph.all_edges():
        src = _mermaid_id(edge.source)
        tgt = _mermaid_id(edge.target)
        lines.append(f"    {src} -->|{edge.rel.value}| {tgt}")

    return "\n".join(lines)


def to_dot(graph: SemGraph) -> str:
    """Graphviz DOT syntax."""
    lines: list[str] = ["digraph semg {", "    rankdir=LR;"]

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


def _mermaid_id(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _dot_id(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)
