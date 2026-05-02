from __future__ import annotations

import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from smg.graph import SemGraph
from smg.model import Edge, Node
from smg.storage import GRAPH_FILE, SMG_DIR


class GitRefError(ValueError):
    pass


@dataclass
class NodeChange:
    name: str
    field: str
    old: str | None
    new: str | None


@dataclass
class RenamedNode:
    old_name: str
    new_name: str
    old_node: Node
    new_node: Node
    match_type: str  # "content" or "structure"


@dataclass
class GraphDiff:
    added_nodes: list[Node] = field(default_factory=list)
    removed_nodes: list[Node] = field(default_factory=list)
    changed_nodes: list[tuple[Node, list[NodeChange]]] = field(default_factory=list)
    renamed_nodes: list[RenamedNode] = field(default_factory=list)
    added_edges: list[Edge] = field(default_factory=list)
    removed_edges: list[Edge] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.added_nodes
            or self.removed_nodes
            or self.changed_nodes
            or self.renamed_nodes
            or self.added_edges
            or self.removed_edges
        )


def diff_graphs(old: SemGraph, new: SemGraph, detect_renames: bool = True) -> GraphDiff:
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

    # Rename/move detection via structural hashing
    if detect_renames and result.removed_nodes and result.added_nodes:
        _detect_renames(result)

    return result


def _detect_renames(result: GraphDiff) -> None:
    """Match removed+added nodes in three phases:

    Phase 1: Exact content_hash match (pure rename/move, no code changes)
    Phase 2: Unique structure_hash match (same AST shape, identifiers changed)
    Phase 3: Fuzzy Jaccard similarity on source tokens (>= 0.8 threshold)
    """
    matched_added: set[str] = set()
    matched_removed: set[str] = set()

    # --- Phase 1 & 2: hash-based matching ---
    removed_by_sh: dict[str, list[Node]] = defaultdict(list)
    for node in result.removed_nodes:
        sh = node.metadata.get("structure_hash")
        if sh:
            removed_by_sh[sh].append(node)

    for added_node in result.added_nodes:
        sh = added_node.metadata.get("structure_hash")
        if not sh or sh not in removed_by_sh:
            continue

        candidates = [n for n in removed_by_sh[sh] if n.name not in matched_removed]
        if not candidates:
            continue

        # Phase 1: prefer content_hash match (exact content, pure rename/move)
        ch = added_node.metadata.get("content_hash")
        exact = [c for c in candidates if c.metadata.get("content_hash") == ch] if ch else []

        if len(exact) == 1:
            match = exact[0]
            match_type = "content"
        elif len(candidates) == 1:
            # Phase 2: unique structure match
            match = candidates[0]
            match_type = "structure"
        else:
            continue  # ambiguous

        result.renamed_nodes.append(
            RenamedNode(
                old_name=match.name,
                new_name=added_node.name,
                old_node=match,
                new_node=added_node,
                match_type=match_type,
            )
        )
        matched_added.add(added_node.name)
        matched_removed.add(match.name)

    # --- Phase 3: fuzzy token similarity for remaining unmatched ---
    remaining_added = [n for n in result.added_nodes if n.name not in matched_added]
    remaining_removed = [n for n in result.removed_nodes if n.name not in matched_removed]

    if remaining_added and remaining_removed:
        _fuzzy_match(
            remaining_added,
            remaining_removed,
            result.renamed_nodes,
            matched_added,
            matched_removed,
        )

    result.added_nodes = [n for n in result.added_nodes if n.name not in matched_added]
    result.removed_nodes = [n for n in result.removed_nodes if n.name not in matched_removed]


_JACCARD_THRESHOLD = 0.8
_SIZE_RATIO_CUTOFF = 0.5


def _tokenize(name: str) -> set[str]:
    """Split a fully-qualified name into tokens for similarity comparison."""
    return set(name.replace(".", " ").replace("_", " ").split())


def _fuzzy_match(
    added: list[Node],
    removed: list[Node],
    renamed: list[RenamedNode],
    matched_added: set[str],
    matched_removed: set[str],
) -> None:
    """Phase 3: Jaccard similarity on node names for same-type unmatched pairs."""
    # Group by type for efficiency
    removed_by_type: dict[str, list[Node]] = defaultdict(list)
    for node in removed:
        removed_by_type[node.type.value].append(node)

    for added_node in added:
        if added_node.name in matched_added:
            continue
        candidates = removed_by_type.get(added_node.type.value, [])
        if not candidates:
            continue

        added_tokens = _tokenize(added_node.name)
        if not added_tokens:
            continue

        best_score = 0.0
        best_match: Node | None = None

        for removed_node in candidates:
            if removed_node.name in matched_removed:
                continue
            removed_tokens = _tokenize(removed_node.name)
            if not removed_tokens:
                continue

            # Early exit: size ratio check
            smaller = min(len(added_tokens), len(removed_tokens))
            larger = max(len(added_tokens), len(removed_tokens))
            if smaller / larger < _SIZE_RATIO_CUTOFF:
                continue

            # Jaccard similarity
            intersection = len(added_tokens & removed_tokens)
            union = len(added_tokens | removed_tokens)
            score = intersection / union if union else 0.0

            if score > best_score:
                best_score = score
                best_match = removed_node

        if best_match is not None and best_score >= _JACCARD_THRESHOLD:
            renamed.append(
                RenamedNode(
                    old_name=best_match.name,
                    new_name=added_node.name,
                    old_node=best_match,
                    new_node=added_node,
                    match_type="fuzzy",
                )
            )
            matched_added.add(added_node.name)
            matched_removed.add(best_match.name)


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
    old_ch = old.metadata.get("content_hash")
    new_ch = new.metadata.get("content_hash")
    if old_ch != new_ch:
        changes.append(NodeChange(old.name, "content_hash", old_ch, new_ch))
    old_sh = old.metadata.get("structure_hash")
    new_sh = new.metadata.get("structure_hash")
    if old_sh != new_sh:
        changes.append(NodeChange(old.name, "structure_hash", old_sh, new_sh))
    return changes


def load_graph_from_git(root: Path, ref: str = "HEAD") -> SemGraph | None:
    """Load a graph from a git ref (e.g., HEAD, HEAD~1, main, abc123).

    Returns None if the file doesn't exist at that ref.
    """
    graph_path = f"{SMG_DIR}/{GRAPH_FILE}"
    try:
        if ref != "HEAD" and not _git_ref_exists(root, ref):
            raise GitRefError(f"invalid git ref: {ref}")

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


def _git_ref_exists(root: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    return result.returncode == 0
