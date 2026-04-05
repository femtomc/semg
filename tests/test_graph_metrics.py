"""Tests for graph-theoretic metrics."""

from smg.graph import SemGraph
from smg.graph_metrics import (
    betweenness_centrality,
    detect_bridges,
    find_cycles,
    kcore_decomposition,
    pagerank,
    topological_layers,
)
from smg.model import Edge, Node, NodeType, RelType


def _node(name: str, t: NodeType = NodeType.MODULE) -> Node:
    return Node(name=name, type=t)


def _edge(src: str, tgt: str, rel: RelType = RelType.IMPORTS) -> Edge:
    return Edge(source=src, target=tgt, rel=rel)


# --- Cycle detection ---


def test_find_cycles_simple():
    """A->B->A is a cycle."""
    g = SemGraph()
    g.add_node(_node("a"))
    g.add_node(_node("b"))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "a"))
    cycles = find_cycles(g)
    assert len(cycles) == 1
    assert sorted(cycles[0]) == ["a", "b"]


def test_find_cycles_triangle():
    """A->B->C->A is a single 3-node cycle."""
    g = SemGraph()
    for n in "abc":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    g.add_edge(_edge("c", "a"))
    cycles = find_cycles(g)
    assert len(cycles) == 1
    assert sorted(cycles[0]) == ["a", "b", "c"]


def test_find_cycles_multiple():
    """Two separate cycles."""
    g = SemGraph()
    for n in "abcde":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "a"))
    g.add_edge(_edge("c", "d"))
    g.add_edge(_edge("d", "e"))
    g.add_edge(_edge("e", "c"))
    cycles = find_cycles(g)
    assert len(cycles) == 2


def test_find_cycles_dag():
    """A DAG has no cycles."""
    g = SemGraph()
    for n in "abcd":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("a", "c"))
    g.add_edge(_edge("b", "d"))
    g.add_edge(_edge("c", "d"))
    assert find_cycles(g) == []


def test_find_cycles_empty():
    g = SemGraph()
    assert find_cycles(g) == []


def test_find_cycles_ignores_contains():
    """Containment edges should not create false cycles."""
    g = SemGraph()
    g.add_node(_node("a"))
    g.add_node(_node("b"))
    g.add_edge(Edge(source="a", target="b", rel=RelType.CONTAINS))
    g.add_edge(Edge(source="b", target="a", rel=RelType.CONTAINS))
    assert find_cycles(g) == []  # contains edges excluded


# --- Topological layers ---


def test_layers_linear_chain():
    """A->B->C: C is layer 0, B is layer 1, A is layer 2."""
    g = SemGraph()
    for n in "abc":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    layers = topological_layers(g)
    assert layers["c"] == 0
    assert layers["b"] == 1
    assert layers["a"] == 2


def test_layers_diamond():
    """A->{B,C}->D: D=0, B=C=1, A=2."""
    g = SemGraph()
    for n in "abcd":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("a", "c"))
    g.add_edge(_edge("b", "d"))
    g.add_edge(_edge("c", "d"))
    layers = topological_layers(g)
    assert layers["d"] == 0
    assert layers["b"] == 1
    assert layers["c"] == 1
    assert layers["a"] == 2


def test_layers_with_cycle():
    """Cycle members get the same layer."""
    g = SemGraph()
    for n in "abc":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    g.add_edge(_edge("c", "b"))  # b<->c cycle
    layers = topological_layers(g)
    assert layers["b"] == layers["c"]  # same SCC = same layer
    assert layers["a"] > layers["b"]


def test_layers_empty():
    assert topological_layers(SemGraph()) == {}


# --- PageRank ---


def test_pagerank_star():
    """Center of star (pointed to by all) gets highest rank."""
    g = SemGraph()
    for n in "abcde":
        g.add_node(_node(n))
    for n in "bcde":
        g.add_edge(_edge(n, "a"))  # all point to a
    ranks = pagerank(g)
    assert ranks["a"] > ranks["b"]
    assert ranks["a"] > ranks["c"]
    # b, c, d, e should have roughly equal rank
    assert abs(ranks["b"] - ranks["c"]) < 0.01


def test_pagerank_chain():
    """In a chain A->B->C, rank increases toward the end (most pointed to)."""
    g = SemGraph()
    for n in "abc":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    ranks = pagerank(g)
    # C is pointed to by B, B is pointed to by A
    # C should have highest rank (most "authority")
    assert ranks["c"] > ranks["a"]


def test_pagerank_sums_to_one():
    g = SemGraph()
    for n in "abcde":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    g.add_edge(_edge("c", "a"))
    g.add_edge(_edge("d", "e"))
    ranks = pagerank(g)
    assert abs(sum(ranks.values()) - 1.0) < 0.01


def test_pagerank_empty():
    assert pagerank(SemGraph()) == {}


# --- Betweenness centrality ---


def test_betweenness_path():
    """In path A-B-C-D-E, middle nodes have highest betweenness."""
    g = SemGraph()
    for n in "abcde":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    g.add_edge(_edge("c", "d"))
    g.add_edge(_edge("d", "e"))
    bc = betweenness_centrality(g)
    # C is the most central (on the most shortest paths)
    assert bc["c"] >= bc["b"]
    assert bc["c"] >= bc["d"]
    # Endpoints have zero betweenness
    assert bc["a"] == 0.0
    assert bc["e"] == 0.0


def test_betweenness_star():
    """Center of star has highest betweenness."""
    g = SemGraph()
    for n in "abcde":
        g.add_node(_node(n))
    for n in "bcde":
        g.add_edge(_edge("a", n))
    bc = betweenness_centrality(g)
    assert bc["a"] > bc["b"]
    assert bc["a"] > bc["c"]


def test_betweenness_cycle():
    """In a cycle, all nodes have equal betweenness."""
    g = SemGraph()
    for n in "abcd":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    g.add_edge(_edge("c", "d"))
    g.add_edge(_edge("d", "a"))
    bc = betweenness_centrality(g)
    values = list(bc.values())
    assert max(values) - min(values) < 0.01  # roughly equal


def test_betweenness_empty():
    assert betweenness_centrality(SemGraph()) == {}


# --- k-core decomposition ---


def test_kcore_triangle_plus_pendant():
    """Triangle {A,B,C} + pendant D connected to A.
    Triangle members have coreness 2, pendant has coreness 1.
    """
    g = SemGraph()
    for n in "abcd":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    g.add_edge(_edge("c", "a"))
    g.add_edge(_edge("a", "d"))
    kc = kcore_decomposition(g)
    assert kc["a"] == 2
    assert kc["b"] == 2
    assert kc["c"] == 2
    assert kc["d"] == 1


def test_kcore_isolated():
    """Isolated nodes (no coupling edges) have coreness 0."""
    g = SemGraph()
    g.add_node(_node("a"))
    g.add_node(_node("b"))
    # Only a contains edge, not coupling
    g.add_edge(Edge(source="a", target="b", rel=RelType.CONTAINS))
    kc = kcore_decomposition(g)
    assert kc == {}  # no nodes in coupling graph


def test_kcore_path():
    """In a path, all nodes have coreness 1."""
    g = SemGraph()
    for n in "abcd":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    g.add_edge(_edge("c", "d"))
    kc = kcore_decomposition(g)
    for n in "abcd":
        assert kc[n] == 1


def test_kcore_empty():
    assert kcore_decomposition(SemGraph()) == {}


# --- Bridge detection ---


def test_bridges_path():
    """In a path A-B-C-D, every edge is a bridge."""
    g = SemGraph()
    for n in "abcd":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    g.add_edge(_edge("c", "d"))
    bridges = detect_bridges(g)
    assert len(bridges) == 3


def test_bridges_cycle():
    """In a cycle, no edges are bridges."""
    g = SemGraph()
    for n in "abcd":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    g.add_edge(_edge("c", "d"))
    g.add_edge(_edge("d", "a"))
    assert detect_bridges(g) == []


def test_bridges_mixed():
    """Cycle {A,B,C} connected to D via single edge C-D. C-D is a bridge."""
    g = SemGraph()
    for n in "abcd":
        g.add_node(_node(n))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    g.add_edge(_edge("c", "a"))  # cycle
    g.add_edge(_edge("c", "d"))  # bridge
    bridges = detect_bridges(g)
    assert len(bridges) == 1
    assert ("c", "d") in bridges or ("d", "c") in bridges


def test_bridges_empty():
    assert detect_bridges(SemGraph()) == []
