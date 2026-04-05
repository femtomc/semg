from __future__ import annotations

from collections import deque

from smg.graph import SemGraph
from smg.model import RelType


def transitive_deps(
    graph: SemGraph,
    name: str,
    rel_types: set[str] | None = None,
    max_depth: int | None = None,
) -> list[str]:
    """BFS following outgoing edges of given rel_types. Default: depends_on, imports."""
    if rel_types is None:
        rel_types = {RelType.DEPENDS_ON.value, RelType.IMPORTS.value}
    return _bfs_outgoing(graph, name, rel_types, max_depth)


def transitive_callers(
    graph: SemGraph,
    name: str,
    max_depth: int | None = None,
) -> list[str]:
    """Follow incoming 'calls' edges transitively."""
    return _bfs_incoming(graph, name, {RelType.CALLS.value}, max_depth)


def shortest_path(graph: SemGraph, source: str, target: str) -> list[str] | None:
    """BFS shortest path (undirected — follows edges in both directions)."""
    if source not in graph.nodes or target not in graph.nodes:
        return None
    if source == target:
        return [source]

    visited: set[str] = {source}
    queue: deque[list[str]] = deque([[source]])

    while queue:
        path = queue.popleft()
        current = path[-1]
        for neighbor in graph.iter_neighbors(current, direction="both"):
            if neighbor == target:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(path + [neighbor])

    return None


def subgraph(
    graph: SemGraph,
    name: str,
    depth: int = 2,
    direction: str = "both",
) -> SemGraph:
    """Return a new SemGraph containing nodes within `depth` hops of `name`."""
    if name not in graph.nodes:
        return SemGraph()

    visited: set[str] = {name}
    frontier: set[str] = {name}

    for _ in range(depth):
        next_frontier: set[str] = set()
        for node_name in frontier:
            for neighbor in graph.iter_neighbors(node_name, direction=direction):
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.add(neighbor)
        frontier = next_frontier
        if not frontier:
            break

    # Build new graph with discovered nodes and edges between them
    sub = SemGraph()
    for n in visited:
        node = graph.get_node(n)
        if node is not None:
            sub.add_node(node)

    for edge in graph.iter_edges():
        if edge.source in visited and edge.target in visited:
            sub.add_edge(edge)

    return sub


def impact(
    graph: SemGraph,
    name: str,
    max_depth: int | None = None,
) -> list[str]:
    """All nodes reachable via incoming edges of any type (reverse transitive closure)."""
    visited: set[str] = {name}
    queue: deque[tuple[str, int]] = deque([(name, 0)])

    while queue:
        current, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for edge in graph.iter_incoming(current):
            if edge.source not in visited:
                visited.add(edge.source)
                queue.append((edge.source, depth + 1))

    return sorted(visited - {name})


def containment_path(graph: SemGraph, name: str) -> list[str]:
    """Walk up the containment chain: [root_package, ..., parent, name]."""
    path = [name]
    current = name
    while True:
        parent = next(
            (edge.source for edge in graph.iter_incoming(current, rel=RelType.CONTAINS)),
            None,
        )
        if parent is None:
            break
        current = parent
        path.append(current)
    path.reverse()
    return path


def ancestors(
    graph: SemGraph,
    name: str,
    rel: str,
    max_depth: int | None = None,
) -> list[str]:
    """Transitive incoming edges of a given type."""
    return _bfs_incoming(graph, name, {rel}, max_depth)


def descendants(
    graph: SemGraph,
    name: str,
    rel: str,
    max_depth: int | None = None,
) -> list[str]:
    """Transitive outgoing edges of a given type."""
    return _bfs_outgoing(graph, name, {rel}, max_depth)


def _bfs_outgoing(
    graph: SemGraph,
    start: str,
    rel_types: set[str],
    max_depth: int | None,
) -> list[str]:
    visited: set[str] = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])

    while queue:
        current, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for edge in graph.iter_outgoing(current):
            if edge.rel.value in rel_types and edge.target not in visited:
                visited.add(edge.target)
                queue.append((edge.target, depth + 1))

    return sorted(visited - {start})


def _bfs_incoming(
    graph: SemGraph,
    start: str,
    rel_types: set[str],
    max_depth: int | None,
) -> list[str]:
    visited: set[str] = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])

    while queue:
        current, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for edge in graph.iter_incoming(current):
            if edge.rel.value in rel_types and edge.source not in visited:
                visited.add(edge.source)
                queue.append((edge.source, depth + 1))

    return sorted(visited - {start})
