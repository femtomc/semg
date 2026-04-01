from __future__ import annotations

from collections import defaultdict

from semg.model import Edge, Node, NodeType, RelType


class NodeNotFoundError(KeyError):
    pass


class SemGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}
        self._adj: dict[str, set[tuple[str, str]]] = defaultdict(set)  # name -> {(rel, target)}
        self._radj: dict[str, set[tuple[str, str]]] = defaultdict(set)  # name -> {(rel, source)}

    def add_node(self, node: Node) -> None:
        existing = self.nodes.get(node.name)
        if existing is not None:
            # Upsert: update non-None fields
            existing.type = node.type
            if node.file is not None:
                existing.file = node.file
            if node.line is not None:
                existing.line = node.line
            if node.end_line is not None:
                existing.end_line = node.end_line
            if node.docstring is not None:
                existing.docstring = node.docstring
            if node.metadata:
                existing.metadata.update(node.metadata)
        else:
            self.nodes[node.name] = node

    def add_edge(self, edge: Edge) -> None:
        if edge.source not in self.nodes:
            raise NodeNotFoundError(f"source node not found: {edge.source!r}")
        if edge.target not in self.nodes:
            raise NodeNotFoundError(f"target node not found: {edge.target!r}")
        self.edges[edge.key] = edge
        self._adj[edge.source].add((edge.rel.value, edge.target))
        self._radj[edge.target].add((edge.rel.value, edge.source))

    def remove_node(self, name: str) -> None:
        if name not in self.nodes:
            raise NodeNotFoundError(f"node not found: {name!r}")
        # Remove all incident edges
        to_remove = [k for k in self.edges if k[0] == name or k[2] == name]
        for k in to_remove:
            self._remove_edge_indexes(k)
            del self.edges[k]
        # Clean up adjacency entries
        self._adj.pop(name, None)
        self._radj.pop(name, None)
        del self.nodes[name]

    def remove_edge(self, source: str, rel: str, target: str) -> None:
        key = (source, rel, target)
        if key not in self.edges:
            raise KeyError(f"edge not found: {source!r} --{rel}--> {target!r}")
        self._remove_edge_indexes(key)
        del self.edges[key]

    def _remove_edge_indexes(self, key: tuple[str, str, str]) -> None:
        source, rel, target = key
        self._adj.get(source, set()).discard((rel, target))
        self._radj.get(target, set()).discard((rel, source))

    def get_node(self, name: str) -> Node | None:
        return self.nodes.get(name)

    def resolve_name(self, name: str) -> list[str]:
        """Resolve a possibly-short name to matching fully-qualified names."""
        if name in self.nodes:
            return [name]
        matches = [n for n in self.nodes if n.endswith("." + name) or n == name]
        return sorted(matches)

    def outgoing(self, name: str, rel: RelType | str | None = None) -> list[Edge]:
        rel_val = rel.value if isinstance(rel, RelType) else rel
        edges = []
        for r, target in self._adj.get(name, set()):
            if rel_val is None or r == rel_val:
                edges.append(self.edges[(name, r, target)])
        return sorted(edges, key=lambda e: (e.rel.value, e.target))

    def incoming(self, name: str, rel: RelType | str | None = None) -> list[Edge]:
        rel_val = rel.value if isinstance(rel, RelType) else rel
        edges = []
        for r, source in self._radj.get(name, set()):
            if rel_val is None or r == rel_val:
                edges.append(self.edges[(source, r, name)])
        return sorted(edges, key=lambda e: (e.rel.value, e.source))

    def neighbors(self, name: str, direction: str = "both") -> list[str]:
        result: set[str] = set()
        if direction in ("out", "both"):
            for _, target in self._adj.get(name, set()):
                result.add(target)
        if direction in ("in", "both"):
            for _, source in self._radj.get(name, set()):
                result.add(source)
        return sorted(result)

    def all_nodes(self, type: NodeType | str | None = None) -> list[Node]:
        type_val = type.value if isinstance(type, NodeType) else type
        nodes = self.nodes.values()
        if type_val is not None:
            nodes = [n for n in nodes if n.type.value == type_val]
        return sorted(nodes, key=lambda n: n.name)

    def all_edges(self) -> list[Edge]:
        return sorted(self.edges.values(), key=lambda e: (e.source, e.rel.value, e.target))

    def __len__(self) -> int:
        return len(self.nodes)

    def validate(self) -> list[str]:
        """Return a list of integrity issues."""
        issues: list[str] = []
        for key, edge in self.edges.items():
            if edge.source not in self.nodes:
                issues.append(f"dangling edge source: {edge.source!r} in {key}")
            if edge.target not in self.nodes:
                issues.append(f"dangling edge target: {edge.target!r} in {key}")
        return issues
