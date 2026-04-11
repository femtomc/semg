"""Graph-theoretic metrics for software architecture analysis.

All functions operate on SemGraph using only coupling edges
(calls, imports, inherits, implements, depends_on) — structural
containment edges are excluded since they encode hierarchy, not coupling.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict, deque
from typing import TYPE_CHECKING

from smg.graph import SemGraph
from smg.model import RelType

if TYPE_CHECKING:
    from smg.model import Node

_log = logging.getLogger(__name__)

# Edge types that represent coupling (not containment/annotation)
_COUPLING_RELS = frozenset(
    {
        RelType.CALLS.value,
        RelType.IMPORTS.value,
        RelType.INHERITS.value,
        RelType.IMPLEMENTS.value,
        RelType.DEPENDS_ON.value,
    }
)

_BETWEENNESS_NODE_THRESHOLD = 50_000


def _coupling_adj(
    graph: SemGraph,
) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """Build directed adjacency lists from coupling edges only.

    Returns (forward_adj, reverse_adj, all_nodes_in_coupling_graph).
    """
    fwd: dict[str, set[str]] = defaultdict(set)
    rev: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()

    for edge in graph.iter_edges(rel_types=_COUPLING_RELS):
        if edge.rel.value in _COUPLING_RELS:
            fwd[edge.source].add(edge.target)
            rev[edge.target].add(edge.source)
            nodes.add(edge.source)
            nodes.add(edge.target)

    return dict(fwd), dict(rev), nodes


def _undirected_coupling_adj(graph: SemGraph) -> tuple[dict[str, set[str]], set[str]]:
    """Build undirected adjacency list from coupling edges."""
    adj: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()

    for edge in graph.iter_edges(rel_types=_COUPLING_RELS):
        if edge.rel.value in _COUPLING_RELS:
            adj[edge.source].add(edge.target)
            adj[edge.target].add(edge.source)
            nodes.add(edge.source)
            nodes.add(edge.target)

    return dict(adj), nodes


# --- Cycle detection (Tarjan's SCC) ---


def find_cycles(graph: SemGraph) -> list[list[str]]:
    """Find all strongly connected components with >1 node (circular dependencies).

    Uses Tarjan's algorithm. Returns list of cycles, each a sorted list of node names.
    """
    fwd, _, nodes = _coupling_adj(graph)

    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        for w in fwd.get(v, set()):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])

        if lowlink[v] == index[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1:
                sccs.append(sorted(scc))

    # Use iterative DFS to avoid stack overflow on large graphs
    for node in sorted(nodes):
        if node not in index:
            _tarjan_iterative(node, fwd, index, lowlink, index_counter, stack, on_stack, sccs)

    return sorted(sccs)


def _tarjan_iterative(
    start: str,
    fwd: dict[str, set[str]],
    index: dict[str, int],
    lowlink: dict[str, int],
    index_counter: list[int],
    stack: list[str],
    on_stack: set[str],
    sccs: list[list[str]],
) -> None:
    """Iterative Tarjan's SCC to avoid Python recursion limits."""
    call_stack: list[tuple[str, list[str], int]] = []
    index[start] = lowlink[start] = index_counter[0]
    index_counter[0] += 1
    stack.append(start)
    on_stack.add(start)

    neighbors = sorted(fwd.get(start, set()))
    call_stack.append((start, neighbors, 0))

    while call_stack:
        v, neighbors, i = call_stack[-1]

        if i < len(neighbors):
            w = neighbors[i]
            call_stack[-1] = (v, neighbors, i + 1)

            if w not in index:
                index[w] = lowlink[w] = index_counter[0]
                index_counter[0] += 1
                stack.append(w)
                on_stack.add(w)
                call_stack.append((w, sorted(fwd.get(w, set())), 0))
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])
        else:
            # Done with v's neighbors
            call_stack.pop()
            if call_stack:
                parent = call_stack[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[v])

            if lowlink[v] == index[v]:
                scc: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == v:
                        break
                if len(scc) > 1:
                    sccs.append(sorted(scc))


# --- Topological layering ---


def topological_layers(graph: SemGraph) -> dict[str, int]:
    """Assign layer numbers based on dependency depth.

    Layer 0 = leaf nodes (no outgoing coupling deps). Each node's layer =
    1 + max layer of its coupling targets. SCCs are condensed first.
    """
    fwd, _, nodes = _coupling_adj(graph)
    if not nodes:
        return {}

    # Find SCCs and condense
    cycles = find_cycles(graph)
    scc_map: dict[str, str] = {}  # node -> SCC representative
    for scc in cycles:
        rep = scc[0]
        for n in scc:
            scc_map[n] = rep

    # Build condensed DAG
    condensed_fwd: dict[str, set[str]] = defaultdict(set)
    condensed_nodes: set[str] = set()
    for n in nodes:
        rep = scc_map.get(n, n)
        condensed_nodes.add(rep)
        for target in fwd.get(n, set()):
            target_rep = scc_map.get(target, target)
            if rep != target_rep:
                condensed_fwd[rep].add(target_rep)

    # Compute layers via DFS with memoization
    layers: dict[str, int] = {}

    def get_layer(n: str) -> int:
        if n in layers:
            return layers[n]
        layers[n] = -1  # sentinel for cycle detection (shouldn't happen in condensed DAG)
        targets = condensed_fwd.get(n, set())
        if not targets:
            layers[n] = 0
        else:
            layers[n] = 1 + max(get_layer(t) for t in targets)
        return layers[n]

    for n in condensed_nodes:
        get_layer(n)

    # Map back to original nodes
    result: dict[str, int] = {}
    for n in nodes:
        rep = scc_map.get(n, n)
        result[n] = layers.get(rep, 0)

    return result


# --- PageRank ---


def pagerank(
    graph: SemGraph,
    damping: float = 0.85,
    iterations: int = 50,
) -> dict[str, float]:
    """Compute PageRank on coupling edges.

    Higher rank = more "important" (recursively depended upon).
    """
    fwd, rev, nodes = _coupling_adj(graph)
    n = len(nodes)
    if n == 0:
        return {}

    node_list = tuple(sorted(nodes))
    rank = {node: 1.0 / n for node in node_list}
    out_degree = {node: len(fwd.get(node, ())) for node in node_list}
    dangling_nodes = tuple(node for node in node_list if out_degree[node] == 0)
    base_rank = (1 - damping) / n

    for _ in range(iterations):
        # Collect "leaked" rank from dangling nodes (no outgoing edges)
        dangling_sum = sum(rank[node] for node in dangling_nodes)
        leaked_rank = dangling_sum / n

        new_rank: dict[str, float] = {}
        for node in node_list:
            incoming_sum = 0.0
            for source in rev.get(node, ()):
                degree = out_degree[source]
                if degree > 0:
                    incoming_sum += rank[source] / degree
            # Redistribute dangling rank evenly + teleport
            new_rank[node] = base_rank + damping * (incoming_sum + leaked_rank)
        rank = new_rank

    return rank


# --- Betweenness centrality (Brandes) ---


def betweenness_centrality(
    graph: SemGraph,
    *,
    include: bool | None = None,
    node_threshold: int = _BETWEENNESS_NODE_THRESHOLD,
    sample_threshold: int = 5_000,
    sample_size: int = 500,
) -> dict[str, float]:
    """Compute betweenness centrality on undirected coupling edges.

    Uses Brandes' algorithm. For graphs larger than *sample_threshold*
    nodes, runs BFS from a random sample of *sample_size* source nodes
    instead of all nodes (approximate betweenness). Returns normalized
    values in [0, 1].

    The *include* parameter gates whether the computation runs at all:
    ``None`` (default) auto-skips when the coupling graph exceeds
    *node_threshold* nodes (default 50K); ``True`` forces computation
    regardless; ``False`` always skips. When skipped, returns an empty
    dict.

    If the native Zig accelerator is available, delegates to it for
    significant speedup (~50-100x for typical graphs).
    """
    adj, nodes = _undirected_coupling_adj(graph)
    n = len(nodes)

    # Gate by graph size to avoid O(VE) on large graphs.
    if include is False:
        return {}
    if include is None and n > node_threshold:
        _log.info(
            "Skipping betweenness centrality: %d coupling nodes exceeds "
            "%d-node threshold (pass include=True to override)",
            n,
            node_threshold,
        )
        return {}

    # Try native accelerator first
    try:
        from smg._accel import betweenness_centrality_native

        result = betweenness_centrality_native(adj, nodes, sample_threshold, sample_size)
        if result is not None:
            return result
    except Exception:
        pass  # Fall through to Python implementation
    if n < 3:
        return {node: 0.0 for node in nodes}

    node_list = tuple(sorted(nodes))
    bc: dict[str, float] = {node: 0.0 for node in node_list}

    # For large graphs, sample source nodes instead of iterating all
    if n > sample_threshold:
        sources = random.sample(node_list, min(sample_size, n))
        scale = n / len(sources)  # scale up to approximate full result
    else:
        sources = node_list
        scale = 1.0

    for s in sources:
        # BFS from s
        stack: list[str] = []
        predecessors: dict[str, list[str]] = defaultdict(list)
        sigma: dict[str, int] = {s: 1}
        dist: dict[str, int] = {s: 0}
        queue: deque[str] = deque([s])
        adj_get = adj.get

        while queue:
            v = queue.popleft()
            stack.append(v)
            next_distance = dist[v] + 1
            for w in adj_get(v, ()):
                current_distance = dist.get(w)
                if current_distance is None:
                    dist[w] = next_distance
                    queue.append(w)
                    current_distance = next_distance
                if current_distance == next_distance:
                    sigma[w] = sigma.get(w, 0) + sigma[v]
                    predecessors[w].append(v)

        # Back-propagation
        delta: dict[str, float] = {}
        while stack:
            w = stack.pop()
            coeff = (1.0 + delta.get(w, 0.0)) / sigma[w]
            for v in predecessors.get(w, ()):
                delta[v] = delta.get(v, 0.0) + sigma[v] * coeff
            if w != s:
                bc[w] += delta.get(w, 0.0)

    # Normalize
    norm = (n - 1) * (n - 2)
    if norm > 0:
        inv = scale / norm
        for node in bc:
            bc[node] *= inv

    return bc


# --- k-core decomposition ---


def kcore_decomposition(graph: SemGraph) -> dict[str, int]:
    """Compute coreness of each node (undirected coupling edges).

    Coreness = max k for which node belongs to the k-core subgraph.
    """
    adj, nodes = _undirected_coupling_adj(graph)
    if not nodes:
        return {}

    # Compute degrees
    degree: dict[str, int] = {n: len(adj.get(n, set())) for n in nodes}
    coreness: dict[str, int] = {}
    remaining = set(nodes)

    k = 0
    while remaining:
        # Find nodes with degree <= k
        while True:
            to_remove = {n for n in remaining if degree[n] <= k}
            if not to_remove:
                break
            for n in to_remove:
                coreness[n] = k
                remaining.discard(n)
                for neighbor in adj.get(n, set()):
                    if neighbor in remaining:
                        degree[neighbor] -= 1
        k += 1

    return coreness


# --- Bridge detection ---


def detect_bridges(graph: SemGraph) -> list[tuple[str, str]]:
    """Find bridge edges in the undirected coupling graph.

    A bridge is an edge whose removal disconnects the graph.
    Uses Tarjan's bridge-finding algorithm.
    """
    adj, nodes = _undirected_coupling_adj(graph)
    if not nodes:
        return []

    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    timer = [0]
    bridges: list[tuple[str, str]] = []

    for start in sorted(nodes):
        if start in disc:
            continue
        # Iterative DFS
        dfs_stack: list[tuple[str, str | None, list[str], int]] = []
        disc[start] = low[start] = timer[0]
        timer[0] += 1
        neighbors = list(adj.get(start, ()))
        dfs_stack.append((start, None, neighbors, 0))

        while dfs_stack:
            v, parent, neighbors, i = dfs_stack[-1]

            if i < len(neighbors):
                w = neighbors[i]
                dfs_stack[-1] = (v, parent, neighbors, i + 1)

                if w not in disc:
                    disc[w] = low[w] = timer[0]
                    timer[0] += 1
                    dfs_stack.append((w, v, list(adj.get(w, ())), 0))
                elif w != parent:
                    low[v] = min(low[v], disc[w])
            else:
                dfs_stack.pop()
                if dfs_stack:
                    parent_node = dfs_stack[-1][0]
                    low[parent_node] = min(low[parent_node], low[v])
                    if low[v] > disc[parent_node]:
                        bridges.append((min(parent_node, v), max(parent_node, v)))

    return sorted(bridges)


# --- Fan-in / Fan-out ---


def fan_in_out(graph: SemGraph) -> dict[str, dict[str, int]]:
    """Compute fan-in and fan-out for each node in the coupling graph.

    Fan-in = number of distinct incoming coupling edges.
    Fan-out = number of distinct outgoing coupling edges.
    Returns dict keyed by node name with {"fan_in": int, "fan_out": int}.
    """
    fwd, rev, nodes = _coupling_adj(graph)
    result: dict[str, dict[str, int]] = {}
    for node in sorted(nodes):
        result[node] = {
            "fan_in": len(rev.get(node, set())),
            "fan_out": len(fwd.get(node, set())),
        }
    return result


# --- Dead code detection ---


def _is_auto_entry_point(name: str, node: "Node") -> bool:
    """Detect likely entry points that shouldn't be flagged as dead code.

    Uses two sources: explicit metadata from extractors (language-aware)
    and language-agnostic naming heuristics. Extractors can set
    metadata["entry_point"] = True to mark nodes as entry points.
    """
    # Explicit extractor annotation (preferred, language-aware)
    if node.metadata.get("entry_point"):
        return True

    short = name.rsplit(".", 1)[-1] if "." in name else name

    # Language-agnostic: main is an entry point in most languages
    if short == "main":
        return True

    # Language-agnostic: constants are referenced at runtime, not via call edges
    if node.type.value in ("constant", "variable") and short.isupper():
        return True

    # Language-agnostic: test functions/classes across conventions
    # Python: test_*, Test*; Go: Test*; Rust: test_*; Java: *Test
    if short.startswith("test_") or short.startswith("Test"):
        return True

    # Detect language from file extension for language-specific heuristics
    ext = ""
    if node.file:
        ext = node.file.rsplit(".", 1)[-1] if "." in node.file else ""

    if ext == "py":
        # __main__ modules
        if short == "__main__":
            return True
        # Dunder methods (called by runtime: __init__, __str__, etc.)
        if short.startswith("__") and short.endswith("__"):
            return True

    return False


def dead_code(
    graph: SemGraph,
    entry_points: set[str] | None = None,
    auto_entry: bool = True,
) -> list[str]:
    """Find nodes with zero incoming coupling edges (potential dead code).

    Excludes:
    - Modules and packages (structural containers, not callable code)
    - Nodes explicitly listed in entry_points
    - Nodes whose type is 'endpoint' or 'config' (presumed externally invoked)
    - When auto_entry=True: __main__, main(), test_*, Test*, and dunder methods

    Returns sorted list of node names.
    """
    from smg.model import NodeType

    _STRUCTURAL_TYPES = frozenset(
        {
            NodeType.MODULE.value,
            NodeType.PACKAGE.value,
        }
    )
    _ENTRY_TYPES = frozenset(
        {
            NodeType.ENDPOINT.value,
            NodeType.CONFIG.value,
        }
    )

    if entry_points is None:
        entry_points = set()

    _, rev, coupling_nodes = _coupling_adj(graph)

    # Build set of nodes that have incoming decorates edges
    decorated: set[str] = set()
    for edge in graph.iter_edges():
        if edge.rel.value == "decorates":
            decorated.add(edge.target)

    # Build containment map: child -> parent
    contained_by: dict[str, str] = {}
    for edge in graph.iter_edges(rel_types={RelType.CONTAINS.value}):
        if edge.rel.value == "contains":
            contained_by[edge.target] = edge.source

    # First pass: identify candidate dead nodes (ignoring containment)
    candidates: list[str] = []
    for node in graph.all_nodes():
        name = node.name
        if node.type.value in _STRUCTURAL_TYPES:
            continue
        if node.type.value in _ENTRY_TYPES:
            continue
        if name in entry_points:
            continue
        if auto_entry and _is_auto_entry_point(name, node):
            continue
        if name in decorated:
            continue
        incoming = rev.get(name, set())
        if len(incoming) == 0:
            candidates.append(name)

    # Second pass: a method/member is only dead if its containing
    # class is also dead (syntactic call resolution can't trace self.method())
    candidate_set = set(candidates)
    dead: list[str] = []
    for name in candidates:
        parent = contained_by.get(name)
        if parent and parent not in candidate_set:
            # Parent is alive -- this member is likely reachable via self/instance
            continue
        dead.append(name)

    return sorted(dead)


# --- God file detection ---


def god_files(
    graph: SemGraph,
    cc_threshold: int = 50,
    function_threshold: int = 15,
    concern_threshold: int = 5,
) -> list[dict]:
    """Detect files with too much complexity or too many concerns.

    A god file is flagged when ANY of:
    - Total cyclomatic complexity across all functions >= cc_threshold
    - Number of functions/methods >= function_threshold
    - Number of distinct coupling targets (external files) >= concern_threshold
      AND total CC >= cc_threshold

    Returns list of dicts sorted by total complexity, each with:
    file, total_cc, max_cc, num_functions, num_classes, concerns, reasons.
    """
    # Group nodes by file
    file_nodes: dict[str, list] = defaultdict(list)
    for node in graph.iter_nodes():
        if node.file:
            file_nodes[node.file].append(node)

    if not file_nodes:
        return []

    # Build node -> file mapping for concern counting
    node_to_file: dict[str, str] = {}
    for node in graph.iter_nodes():
        if node.file:
            node_to_file[node.name] = node.file

    results: list[dict] = []
    for file_path, nodes in file_nodes.items():
        functions = [n for n in nodes if n.type.value in ("function", "method")]
        classes = [n for n in nodes if n.type.value == "class"]

        total_cc = 0
        max_cc = 0
        for fn in functions:
            cc = fn.metadata.get("metrics", {}).get("cyclomatic_complexity", 1)
            total_cc += cc
            max_cc = max(max_cc, cc)

        # Count distinct external files this file's nodes couple to
        external_files: set[str] = set()
        for node in nodes:
            for edge in graph.iter_outgoing(node.name):
                if edge.rel.value in _COUPLING_RELS:
                    target_file = node_to_file.get(edge.target)
                    if target_file and target_file != file_path:
                        external_files.add(target_file)

        reasons: list[str] = []
        if total_cc >= cc_threshold:
            reasons.append(f"high total complexity (CC={total_cc})")
        if len(functions) >= function_threshold:
            reasons.append(f"many functions ({len(functions)})")
        if len(external_files) >= concern_threshold and total_cc >= cc_threshold:
            reasons.append(f"many concerns ({len(external_files)} external files)")

        if reasons:
            results.append(
                {
                    "file": file_path,
                    "total_cc": total_cc,
                    "max_cc": max_cc,
                    "num_functions": len(functions),
                    "num_classes": len(classes),
                    "concerns": len(external_files),
                    "reasons": reasons,
                }
            )

    return sorted(results, key=lambda r: r["total_cc"], reverse=True)


# --- Layering violations ---


def layering_violations(graph: SemGraph) -> list[dict]:
    """Find coupling edges where the source is at the same or lower layer than the target.

    In a well-layered architecture, dependencies should flow strictly
    downward (higher layer depends on lower layer). Edges where
    layer(source) <= layer(target) indicate back-dependencies —
    either cycle-participating edges or architectural inversions.

    Returns list of dicts with source, target, rel, source_layer, target_layer.
    """
    layers = topological_layers(graph)
    if not layers:
        return []

    violations: list[dict] = []
    for edge in graph.iter_edges(rel_types=_COUPLING_RELS):
        sl = layers.get(edge.source)
        tl = layers.get(edge.target)
        if sl is not None and tl is not None and sl <= tl:
            violations.append(
                {
                    "source": edge.source,
                    "target": edge.target,
                    "rel": edge.rel.value,
                    "source_layer": sl,
                    "target_layer": tl,
                }
            )

    return sorted(violations, key=lambda v: (v["target_layer"] - v["source_layer"], v["source"]))


# --- HITS (Hub/Authority) ---


def hits(
    graph: SemGraph,
    iterations: int = 50,
) -> dict[str, dict[str, float]]:
    """Compute HITS hub and authority scores on coupling edges.

    Authorities are nodes pointed to by many hubs (core utilities).
    Hubs are nodes that point to many authorities (orchestrators).
    Returns dict keyed by node name with {"hub": float, "authority": float}.

    If the native Zig accelerator is available, delegates to it.
    """
    fwd, rev, nodes = _coupling_adj(graph)

    # Try native accelerator first
    try:
        from smg._accel import hits_native

        result = hits_native(fwd, rev, nodes, iterations)
        if result is not None:
            return result
    except Exception:
        pass
    if not nodes:
        return {}

    node_list = tuple(sorted(nodes))
    hub = {n: 1.0 for n in node_list}
    auth = {n: 1.0 for n in node_list}

    for _ in range(iterations):
        # Authority update: auth(v) = sum of hub(u) for all u -> v
        new_auth: dict[str, float] = {}
        for n in node_list:
            new_auth[n] = sum(hub[src] for src in rev.get(n, set()))

        # Hub update: hub(v) = sum of auth(u) for all v -> u
        new_hub: dict[str, float] = {}
        for n in node_list:
            new_hub[n] = sum(new_auth[tgt] for tgt in fwd.get(n, set()))

        # Normalize
        auth_norm = max(sum(v * v for v in new_auth.values()) ** 0.5, 1e-10)
        hub_norm = max(sum(v * v for v in new_hub.values()) ** 0.5, 1e-10)
        auth = {n: v / auth_norm for n, v in new_auth.items()}
        hub = {n: v / hub_norm for n, v in new_hub.items()}

    return {n: {"hub": round(hub[n], 6), "authority": round(auth[n], 6)} for n in node_list}


# --- Minimal cycle extraction ---


def minimal_cycle(graph: SemGraph, scc: list[str]) -> list[str]:
    """Extract the shortest cycle from a strongly connected component.

    Returns a list of node names forming the shortest directed cycle
    within the SCC, using only coupling edges. The cycle starts from
    the lexicographically smallest node for determinism.
    """
    if len(scc) <= 1:
        return scc

    scc_set = frozenset(scc)
    fwd, _, _ = _coupling_adj(graph)

    # Restrict adjacency to nodes in the SCC
    local_fwd: dict[str, set[str]] = {}
    for n in scc:
        local_fwd[n] = fwd.get(n, set()) & scc_set

    # BFS from each node to find shortest cycle through it
    best: list[str] | None = None
    for start in sorted(scc):
        # BFS: find shortest path from start back to start
        visited: dict[str, str | None] = {start: None}
        queue: deque[str] = deque()
        # Seed with start's neighbors (not start itself)
        for neighbor in sorted(local_fwd.get(start, set())):
            if neighbor == start:
                # Self-loop shouldn't happen in SCC with >1 node, but handle it
                return [start]
            if neighbor not in visited:
                visited[neighbor] = start
                queue.append(neighbor)

        found = False
        while queue and not found:
            current = queue.popleft()
            for neighbor in sorted(local_fwd.get(current, set())):
                if neighbor == start:
                    # Found a cycle back to start -- reconstruct path
                    path = [start]
                    n: str | None = current
                    while n is not None and n != start:
                        path.append(n)
                        n = visited[n]
                    path.reverse()
                    # path is now [start, ..., current] and current -> start closes it
                    cycle = path
                    if best is None or len(cycle) < len(best):
                        best = cycle
                    found = True
                    break
                if neighbor not in visited:
                    visited[neighbor] = current
                    queue.append(neighbor)

    return best if best is not None else sorted(scc)
