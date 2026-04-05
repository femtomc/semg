"""LLM context budgeting: pack source code from graph neighbors into a token budget."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from smg.graph import SemGraph
from smg.model import Node


@dataclass
class ContextEntry:
    name: str
    node_type: str
    relation: str  # "target", "direct_dep", "direct_dependent", "2hop", "3hop"
    level: str  # "full", "signature", "summary"
    content: str
    tokens: int
    file: str | None
    line: int | None


@dataclass
class ContextResult:
    target: str
    entries: list[ContextEntry]
    total_tokens: int
    budget: int
    truncated: bool


_COUPLING = {"calls", "imports", "inherits", "implements", "depends_on"}


def build_context(
    graph: SemGraph,
    root: Path,
    name: str,
    budget: int = 4000,
    token_counter: Callable[[str], int] | None = None,
) -> ContextResult:
    """Walk outward from `name`, packing source code greedily by proximity.

    Tiers:
      1. Target: full source (always included)
      2. Direct deps/dependents: full source, downgrade to signature if over budget
      3. 2-hop: signature only
      4. 3-hop: summary only (name + type + docstring)
    """
    count_fn = token_counter or _default_token_count
    entries: list[ContextEntry] = []
    used = 0
    seen: set[str] = set()
    truncated = False

    target_node = graph.get_node(name)
    if target_node is None:
        return ContextResult(target=name, entries=[], total_tokens=0, budget=budget, truncated=False)

    # Tier 1: target full source (always)
    target_content = _read_full_source(target_node, root) or _summary(target_node)
    target_tokens = count_fn(target_content)
    entries.append(
        ContextEntry(
            name=name,
            node_type=target_node.type.value,
            relation="target",
            level="full",
            content=target_content,
            tokens=target_tokens,
            file=target_node.file,
            line=target_node.line,
        )
    )
    used += target_tokens
    seen.add(name)

    # Tier 2: direct deps and dependents — full, downgrading to signature
    direct = _get_direct_neighbors(graph, name)
    for neighbor_name, relation in direct:
        if neighbor_name in seen:
            continue
        seen.add(neighbor_name)
        node = graph.get_node(neighbor_name)
        if node is None:
            continue

        content, level = _best_fit(node, root, count_fn, used, budget)
        tokens = count_fn(content)
        if used + tokens > budget and level == "full":
            content, level = _best_fit_at(node, root, "signature")
            tokens = count_fn(content)
        if used + tokens > budget and level == "signature":
            content, level = _summary(node), "summary"
            tokens = count_fn(content)
        if used + tokens > budget:
            truncated = True
            continue

        entries.append(
            ContextEntry(
                name=neighbor_name,
                node_type=node.type.value,
                relation=relation,
                level=level,
                content=content,
                tokens=tokens,
                file=node.file,
                line=node.line,
            )
        )
        used += tokens

    # Tier 3: 2-hop — signatures
    hop2 = _get_hop_neighbors(graph, name, 2, seen)
    for neighbor_name in hop2:
        if used >= budget:
            truncated = True
            break
        node = graph.get_node(neighbor_name)
        if node is None:
            continue
        seen.add(neighbor_name)
        content, level = _best_fit_at(node, root, "signature")
        tokens = count_fn(content)
        if used + tokens > budget:
            content, level = _summary(node), "summary"
            tokens = count_fn(content)
        if used + tokens > budget:
            truncated = True
            continue
        entries.append(
            ContextEntry(
                name=neighbor_name,
                node_type=node.type.value,
                relation="2hop",
                level=level,
                content=content,
                tokens=tokens,
                file=node.file,
                line=node.line,
            )
        )
        used += tokens

    # Tier 4: 3-hop — summaries only
    hop3 = _get_hop_neighbors(graph, name, 3, seen)
    for neighbor_name in hop3:
        if used >= budget:
            truncated = True
            break
        node = graph.get_node(neighbor_name)
        if node is None:
            continue
        seen.add(neighbor_name)
        content = _summary(node)
        tokens = count_fn(content)
        if used + tokens > budget:
            truncated = True
            continue
        entries.append(
            ContextEntry(
                name=neighbor_name,
                node_type=node.type.value,
                relation="3hop",
                level="summary",
                content=content,
                tokens=tokens,
                file=node.file,
                line=node.line,
            )
        )
        used += tokens

    return ContextResult(
        target=name,
        entries=entries,
        total_tokens=used,
        budget=budget,
        truncated=truncated,
    )


def _default_token_count(text: str) -> int:
    return max(1, len(text) // 4)


def _read_full_source(node: Node, root: Path) -> str | None:
    if not node.file or node.line is None:
        return None
    file_path = root / node.file
    if not file_path.exists():
        return None
    try:
        lines = file_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    start = node.line - 1
    end = node.end_line or node.line
    return "\n".join(lines[start:end])


def _signature(node: Node, root: Path) -> str | None:
    if not node.file or node.line is None:
        return None
    file_path = root / node.file
    if not file_path.exists():
        return None
    try:
        lines = file_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None

    start = node.line - 1
    if start >= len(lines):
        return None

    if node.type.value in ("class", "interface"):
        end = min(start + 3, len(lines))
        return "\n".join(lines[start:end])

    result = [lines[start]]
    for i in range(start + 1, min(start + 5, len(lines))):
        line = lines[i]
        result.append(line)
        stripped = line.rstrip()
        if stripped.endswith(":") or stripped.endswith("{") or stripped.endswith(")"):
            break
    return "\n".join(result)


def _summary(node: Node) -> str:
    parts = [f"[{node.type.value}] {node.name}"]
    if node.file:
        loc = node.file
        if node.line is not None:
            loc += f":{node.line}"
        parts.append(f"  @ {loc}")
    if node.docstring:
        parts.append(f"  # {node.docstring.split(chr(10))[0]}")
    return "\n".join(parts)


def _best_fit(
    node: Node,
    root: Path,
    count_fn: Callable[[str], int],
    used: int,
    budget: int,
) -> tuple[str, str]:
    """Try full source, fall back to signature, then summary."""
    full = _read_full_source(node, root)
    if full and used + count_fn(full) <= budget:
        return full, "full"
    sig = _signature(node, root)
    if sig:
        return sig, "signature"
    return _summary(node), "summary"


def _best_fit_at(node: Node, root: Path, level: str) -> tuple[str, str]:
    """Get content at a specific level, falling back to summary."""
    if level == "full":
        content = _read_full_source(node, root)
        if content:
            return content, "full"
        content = _signature(node, root)
        if content:
            return content, "signature"
        return _summary(node), "summary"
    if level == "signature":
        content = _signature(node, root)
        if content:
            return content, "signature"
        return _summary(node), "summary"
    return _summary(node), "summary"


def _get_direct_neighbors(graph: SemGraph, name: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for edge in graph.iter_outgoing(name):
        if edge.rel.value in _COUPLING:
            result.append((edge.target, "direct_dep"))
    for edge in graph.iter_incoming(name):
        if edge.rel.value in _COUPLING:
            result.append((edge.source, "direct_dependent"))
    return result


def _get_hop_neighbors(graph: SemGraph, name: str, hop: int, already_seen: set[str]) -> list[str]:
    visited = {name}
    frontier = {name}
    for _ in range(hop):
        next_frontier: set[str] = set()
        for n in frontier:
            for neighbor in graph.iter_neighbors(n, direction="both"):
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.add(neighbor)
        frontier = next_frontier
    return sorted(frontier - already_seen)
