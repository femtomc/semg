from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Iterator

from smg.model import Edge, Node, NodeType, RelType


class NodeNotFoundError(KeyError):
    pass


class SemGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}
        self._adj: dict[str, set[tuple[str, str]]] = defaultdict(set)  # name -> {(rel, target)}
        self._radj: dict[str, set[tuple[str, str]]] = defaultdict(set)  # name -> {(rel, source)}
        self._adj_by_rel: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self._radj_by_rel: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self._edges_by_rel: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        self._suffix_index: dict[str, set[str]] = defaultdict(set)

    def _iter_suffixes(self, name: str) -> Iterator[str]:
        parts = name.split(".")
        for idx in range(len(parts)):
            yield ".".join(parts[idx:])

    def _index_node_name(self, name: str) -> None:
        for suffix in self._iter_suffixes(name):
            self._suffix_index[suffix].add(name)

    def _deindex_node_name(self, name: str) -> None:
        for suffix in self._iter_suffixes(name):
            matches = self._suffix_index.get(suffix)
            if not matches:
                continue
            matches.discard(name)
            if not matches:
                self._suffix_index.pop(suffix, None)

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
            self._index_node_name(node.name)

    def add_edge(self, edge: Edge) -> None:
        if edge.source not in self.nodes:
            raise NodeNotFoundError(f"source node not found: {edge.source!r}")
        if edge.target not in self.nodes:
            raise NodeNotFoundError(f"target node not found: {edge.target!r}")
        key = edge.key
        rel = edge.rel.value
        self.edges[key] = edge
        self._adj[edge.source].add((rel, edge.target))
        self._radj[edge.target].add((rel, edge.source))
        self._adj_by_rel[edge.source][rel].add(edge.target)
        self._radj_by_rel[edge.target][rel].add(edge.source)
        self._edges_by_rel[rel].add(key)

    def remove_node(self, name: str) -> None:
        if name not in self.nodes:
            raise NodeNotFoundError(f"node not found: {name!r}")
        # Remove all incident edges
        to_remove = {(name, rel, target) for rel, target in self._adj.get(name, set())}
        to_remove.update((source, rel, name) for rel, source in self._radj.get(name, set()))
        for k in to_remove:
            self._remove_edge_indexes(k)
            del self.edges[k]
        # Clean up adjacency entries
        self._adj.pop(name, None)
        self._radj.pop(name, None)
        self._adj_by_rel.pop(name, None)
        self._radj_by_rel.pop(name, None)
        self._deindex_node_name(name)
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
        source_index = self._adj_by_rel.get(source)
        if source_index is not None:
            targets = source_index.get(rel)
            if targets is not None:
                targets.discard(target)
                if not targets:
                    source_index.pop(rel, None)
            if not source_index:
                self._adj_by_rel.pop(source, None)
        target_index = self._radj_by_rel.get(target)
        if target_index is not None:
            sources = target_index.get(rel)
            if sources is not None:
                sources.discard(source)
                if not sources:
                    target_index.pop(rel, None)
            if not target_index:
                self._radj_by_rel.pop(target, None)
        rel_edges = self._edges_by_rel.get(rel)
        if rel_edges is not None:
            rel_edges.discard(key)
            if not rel_edges:
                self._edges_by_rel.pop(rel, None)

    def get_node(self, name: str) -> Node | None:
        return self.nodes.get(name)

    def resolve_name(self, name: str) -> list[str]:
        """Resolve a possibly-short name to matching fully-qualified names."""
        if name in self.nodes:
            return [name]
        return sorted(self._suffix_index.get(name, ()))

    def iter_outgoing(self, name: str, rel: RelType | str | None = None) -> Iterator[Edge]:
        rel_val = rel.value if isinstance(rel, RelType) else rel
        if rel_val is None:
            for edge_rel, target in self._adj.get(name, ()):
                yield self.edges[(name, edge_rel, target)]
            return
        for target in self._adj_by_rel.get(name, {}).get(rel_val, ()):
            yield self.edges[(name, rel_val, target)]

    def iter_incoming(self, name: str, rel: RelType | str | None = None) -> Iterator[Edge]:
        rel_val = rel.value if isinstance(rel, RelType) else rel
        if rel_val is None:
            for edge_rel, source in self._radj.get(name, ()):
                yield self.edges[(source, edge_rel, name)]
            return
        for source in self._radj_by_rel.get(name, {}).get(rel_val, ()):
            yield self.edges[(source, rel_val, name)]

    def outgoing_count(self, name: str, rel: RelType | str | None = None) -> int:
        rel_val = rel.value if isinstance(rel, RelType) else rel
        if rel_val is None:
            return len(self._adj.get(name, ()))
        return len(self._adj_by_rel.get(name, {}).get(rel_val, ()))

    def incoming_count(self, name: str, rel: RelType | str | None = None) -> int:
        rel_val = rel.value if isinstance(rel, RelType) else rel
        if rel_val is None:
            return len(self._radj.get(name, ()))
        return len(self._radj_by_rel.get(name, {}).get(rel_val, ()))

    def outgoing(self, name: str, rel: RelType | str | None = None) -> list[Edge]:
        return sorted(self.iter_outgoing(name, rel=rel), key=lambda e: (e.rel.value, e.target))

    def incoming(self, name: str, rel: RelType | str | None = None) -> list[Edge]:
        return sorted(self.iter_incoming(name, rel=rel), key=lambda e: (e.rel.value, e.source))

    def iter_neighbors(self, name: str, direction: str = "both") -> Iterator[str]:
        if direction == "out":
            for _, target in self._adj.get(name, ()):
                yield target
            return
        if direction == "in":
            for _, source in self._radj.get(name, ()):
                yield source
            return
        seen: set[str] = set()
        for _, target in self._adj.get(name, ()):
            if target not in seen:
                seen.add(target)
                yield target
        for _, source in self._radj.get(name, ()):
            if source not in seen:
                seen.add(source)
                yield source

    def neighbors(self, name: str, direction: str = "both") -> list[str]:
        return sorted(self.iter_neighbors(name, direction=direction))

    def iter_nodes(self, type: NodeType | str | None = None) -> Iterator[Node]:
        type_val = type.value if isinstance(type, NodeType) else type
        for node in self.nodes.values():
            if type_val is None or node.type.value == type_val:
                yield node

    def all_nodes(self, type: NodeType | str | None = None) -> list[Node]:
        return sorted(self.iter_nodes(type=type), key=lambda n: n.name)

    def iter_edges(self, rel_types: set[str] | frozenset[str] | None = None) -> Iterator[Edge]:
        if rel_types is None:
            yield from self.edges.values()
            return
        for rel in rel_types:
            for key in self._edges_by_rel.get(rel, ()):
                yield self.edges[key]

    def all_edges(self) -> list[Edge]:
        return sorted(self.iter_edges(), key=lambda e: (e.source, e.rel.value, e.target))

    def clone(self) -> SemGraph:
        cloned = SemGraph()
        for node in self.nodes.values():
            cloned.add_node(
                Node(
                    name=node.name,
                    type=node.type,
                    file=node.file,
                    line=node.line,
                    end_line=node.end_line,
                    docstring=node.docstring,
                    metadata=deepcopy(node.metadata),
                )
            )
        for edge in self.edges.values():
            cloned.add_edge(
                Edge(
                    source=edge.source,
                    target=edge.target,
                    rel=edge.rel,
                    metadata=deepcopy(edge.metadata),
                )
            )
        return cloned

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
