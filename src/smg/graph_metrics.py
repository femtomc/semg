"""Graph-theoretic metrics for software architecture analysis.

All functions operate on SemGraph using only coupling edges
(calls, imports, inherits, implements, depends_on) — structural
containment edges are excluded since they encode hierarchy, not coupling.
"""
from __future__ import annotations

from collections import defaultdict, deque

from smg.graph import SemGraph
from smg.model import RelType

# Edge types that represent coupling (not containment/annotation)
_COUPLING_RELS = frozenset({
    RelType.CALLS.value,
    RelType.IMPORTS.value,
    RelType.INHERITS.value,
    RelType.IMPLEMENTS.value,
    RelType.DEPENDS_ON.value,
})


def _coupling_adj(graph: SemGraph) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """Build directed adjacency lists from coupling edges only.

    Returns (forward_adj, reverse_adj, all_nodes_in_coupling_graph).
    """
    fwd: dict[str, set[str]] = defaultdict(set)
    rev: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()

    for edge in graph.all_edges():
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

    for edge in graph.all_edges():
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

    node_list = sorted(nodes)
    rank = {node: 1.0 / n for node in node_list}

    for _ in range(iterations):
        # Collect "leaked" rank from dangling nodes (no outgoing edges)
        dangling_sum = sum(rank[node] for node in node_list if not fwd.get(node))

        new_rank: dict[str, float] = {}
        for node in node_list:
            incoming_sum = 0.0
            for source in rev.get(node, set()):
                out_degree = len(fwd.get(source, set()))
                if out_degree > 0:
                    incoming_sum += rank[source] / out_degree
            # Redistribute dangling rank evenly + teleport
            new_rank[node] = (1 - damping) / n + damping * (incoming_sum + dangling_sum / n)
        rank = new_rank

    return rank


# --- Betweenness centrality (Brandes) ---


def betweenness_centrality(graph: SemGraph) -> dict[str, float]:
    """Compute betweenness centrality on undirected coupling edges.

    Uses Brandes' algorithm. Returns normalized values in [0, 1].
    """
    adj, nodes = _undirected_coupling_adj(graph)
    n = len(nodes)
    if n < 3:
        return {node: 0.0 for node in nodes}

    node_list = sorted(nodes)
    bc: dict[str, float] = {node: 0.0 for node in node_list}

    for s in node_list:
        # BFS from s
        stack: list[str] = []
        predecessors: dict[str, list[str]] = {node: [] for node in node_list}
        sigma: dict[str, int] = {node: 0 for node in node_list}
        sigma[s] = 1
        dist: dict[str, int] = {node: -1 for node in node_list}
        dist[s] = 0
        queue: deque[str] = deque([s])

        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adj.get(v, set()):
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    predecessors[w].append(v)

        # Back-propagation
        delta: dict[str, float] = {node: 0.0 for node in node_list}
        while stack:
            w = stack.pop()
            for v in predecessors[w]:
                delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
            if w != s:
                bc[w] += delta[w]

    # Normalize
    norm = (n - 1) * (n - 2)
    if norm > 0:
        for node in bc:
            bc[node] /= norm

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
        neighbors = sorted(adj.get(start, set()))
        dfs_stack.append((start, None, neighbors, 0))

        while dfs_stack:
            v, parent, neighbors, i = dfs_stack[-1]

            if i < len(neighbors):
                w = neighbors[i]
                dfs_stack[-1] = (v, parent, neighbors, i + 1)

                if w not in disc:
                    disc[w] = low[w] = timer[0]
                    timer[0] += 1
                    dfs_stack.append((w, v, sorted(adj.get(w, set())), 0))
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


def dead_code(
    graph: SemGraph,
    entry_points: set[str] | None = None,
) -> list[str]:
    """Find nodes with zero incoming coupling edges (potential dead code).

    Excludes:
    - Modules and packages (structural containers, not callable code)
    - Nodes explicitly listed in entry_points
    - Nodes whose type is 'endpoint' or 'config' (presumed externally invoked)

    Returns sorted list of node names.
    """
    from smg.model import NodeType

    _STRUCTURAL_TYPES = frozenset({
        NodeType.MODULE.value,
        NodeType.PACKAGE.value,
    })
    _ENTRY_TYPES = frozenset({
        NodeType.ENDPOINT.value,
        NodeType.CONFIG.value,
    })

    if entry_points is None:
        entry_points = set()

    _, rev, coupling_nodes = _coupling_adj(graph)

    dead: list[str] = []
    for node in graph.all_nodes():
        name = node.name
        # Skip structural / entry-point types
        if node.type.value in _STRUCTURAL_TYPES:
            continue
        if node.type.value in _ENTRY_TYPES:
            continue
        if name in entry_points:
            continue
        # Dead if the node participates in coupling but has no incoming,
        # OR if the node has no coupling edges at all (truly isolated)
        incoming = rev.get(name, set())
        if len(incoming) == 0:
            dead.append(name)

    return sorted(dead)


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
    for edge in graph.all_edges():
        if edge.rel.value not in _COUPLING_RELS:
            continue
        sl = layers.get(edge.source)
        tl = layers.get(edge.target)
        if sl is not None and tl is not None and sl <= tl:
            violations.append({
                "source": edge.source,
                "target": edge.target,
                "rel": edge.rel.value,
                "source_layer": sl,
                "target_layer": tl,
            })

    return sorted(violations, key=lambda v: (v["target_layer"] - v["source_layer"], v["source"]))
