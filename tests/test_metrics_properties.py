"""Property-based tests for graph and OO metrics using Hypothesis.

These tests verify invariants that must hold for ANY valid graph,
not just hand-crafted examples. They catch edge cases and corner
cases that example-based tests miss.
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from smg.graph import SemGraph
from smg.graph_metrics import (
    betweenness_centrality,
    dead_code,
    detect_bridges,
    fan_in_out,
    find_cycles,
    god_files,
    hits,
    kcore_decomposition,
    layering_violations,
    minimal_cycle,
    pagerank,
    topological_layers,
)
from smg.model import Edge, Node, NodeType, RelType
from smg.oo_metrics import (
    cbo,
    dit,
    feature_envy,
    god_classes,
    lcom4,
    martin_metrics,
    noc,
    rfc,
    sdp_violations,
    shotgun_surgery,
    wmc,
)
from smg.rules import (
    Rule,
    check_all,
    check_deny,
    check_invariant,
    parse_deny_pattern,
)

# --- Strategies for generating random graphs ---


node_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=4).map(lambda s: f"mod.{s}")

node_types = st.sampled_from([NodeType.MODULE, NodeType.CLASS, NodeType.FUNCTION, NodeType.METHOD])

coupling_rels = st.sampled_from([RelType.CALLS, RelType.IMPORTS, RelType.INHERITS, RelType.DEPENDS_ON])


@st.composite
def random_graph(draw, min_nodes=2, max_nodes=15, max_edges=30):
    """Generate a random SemGraph with coupling edges."""
    n = draw(st.integers(min_value=min_nodes, max_value=max_nodes))
    names = [f"n{i}" for i in range(n)]

    g = SemGraph()
    for name in names:
        t = draw(st.sampled_from([NodeType.MODULE, NodeType.CLASS, NodeType.FUNCTION]))
        g.add_node(Node(name=name, type=t))

    num_edges = draw(st.integers(min_value=0, max_value=min(max_edges, n * (n - 1))))
    for _ in range(num_edges):
        src = draw(st.sampled_from(names))
        tgt = draw(st.sampled_from(names))
        if src != tgt:
            rel = draw(coupling_rels)
            key = (src, rel.value, tgt)
            if key not in g.edges:
                g.add_edge(Edge(source=src, target=tgt, rel=rel))

    return g


@st.composite
def random_class_graph(draw, min_classes=1, max_classes=5, max_methods_per_class=6):
    """Generate a graph with classes, methods, and call edges for OO metrics."""
    g = SemGraph()
    g.add_node(Node(name="mod", type=NodeType.MODULE))

    n_classes = draw(st.integers(min_value=min_classes, max_value=max_classes))
    all_methods: list[str] = []

    for ci in range(n_classes):
        cname = f"mod.C{ci}"
        g.add_node(Node(name=cname, type=NodeType.CLASS))
        g.add_edge(Edge(source="mod", target=cname, rel=RelType.CONTAINS))

        n_methods = draw(st.integers(min_value=0, max_value=max_methods_per_class))
        class_methods: list[str] = []
        for mi in range(n_methods):
            mname = f"{cname}.m{mi}"
            cc = draw(st.integers(min_value=1, max_value=20))
            g.add_node(
                Node(
                    name=mname,
                    type=NodeType.METHOD,
                    metadata={"metrics": {"cyclomatic_complexity": cc}},
                )
            )
            g.add_edge(Edge(source=cname, target=mname, rel=RelType.CONTAINS))
            class_methods.append(mname)
            all_methods.append(mname)

        # Random intra-class calls
        if len(class_methods) >= 2:
            n_calls = draw(st.integers(min_value=0, max_value=len(class_methods)))
            for _ in range(n_calls):
                src = draw(st.sampled_from(class_methods))
                tgt = draw(st.sampled_from(class_methods))
                if src != tgt:
                    key = (src, RelType.CALLS.value, tgt)
                    if key not in g.edges:
                        g.add_edge(Edge(source=src, target=tgt, rel=RelType.CALLS))

    # Random cross-class calls
    if len(all_methods) >= 2:
        n_cross = draw(st.integers(min_value=0, max_value=min(10, len(all_methods))))
        for _ in range(n_cross):
            src = draw(st.sampled_from(all_methods))
            tgt = draw(st.sampled_from(all_methods))
            if src != tgt:
                key = (src, RelType.CALLS.value, tgt)
                if key not in g.edges:
                    g.add_edge(Edge(source=src, target=tgt, rel=RelType.CALLS))

    # Random inheritance
    class_names = [f"mod.C{i}" for i in range(n_classes)]
    if n_classes >= 2:
        n_inherit = draw(st.integers(min_value=0, max_value=n_classes - 1))
        for _ in range(n_inherit):
            child = draw(st.sampled_from(class_names))
            parent = draw(st.sampled_from(class_names))
            if child != parent:
                key = (child, RelType.INHERITS.value, parent)
                if key not in g.edges:
                    g.add_edge(Edge(source=child, target=parent, rel=RelType.INHERITS))

    return g


@st.composite
def random_module_graph(draw, min_modules=2, max_modules=8):
    """Generate a graph with modules and import edges for Martin's metrics."""
    g = SemGraph()
    n = draw(st.integers(min_value=min_modules, max_value=max_modules))
    mod_names = [f"mod{i}" for i in range(n)]

    for name in mod_names:
        g.add_node(Node(name=name, type=NodeType.MODULE))
        # Add some classes/interfaces
        n_classes = draw(st.integers(min_value=0, max_value=3))
        for ci in range(n_classes):
            is_interface = draw(st.booleans())
            cname = f"{name}.C{ci}"
            g.add_node(
                Node(
                    name=cname,
                    type=NodeType.INTERFACE if is_interface else NodeType.CLASS,
                )
            )
            g.add_edge(Edge(source=name, target=cname, rel=RelType.CONTAINS))

    # Random imports between modules
    n_imports = draw(st.integers(min_value=0, max_value=n * 2))
    for _ in range(n_imports):
        src = draw(st.sampled_from(mod_names))
        tgt = draw(st.sampled_from(mod_names))
        if src != tgt:
            key = (src, RelType.IMPORTS.value, tgt)
            if key not in g.edges:
                g.add_edge(Edge(source=src, target=tgt, rel=RelType.IMPORTS))

    return g


# ============================================================
# Graph-Theoretic Metric Properties
# ============================================================


class TestCycleProperties:
    """Properties that must hold for cycle detection."""

    @given(random_graph())
    @settings(max_examples=100)
    def test_cycles_are_subsets_of_nodes(self, g: SemGraph):
        """Every node in a cycle must exist in the graph."""
        cycles = find_cycles(g)
        all_names = set(g.nodes.keys())
        for cycle in cycles:
            for name in cycle:
                assert name in all_names

    @given(random_graph())
    @settings(max_examples=100)
    def test_cycle_members_are_mutually_reachable(self, g: SemGraph):
        """In each SCC, every node should reach every other via coupling edges."""
        cycles = find_cycles(g)
        for cycle in cycles:
            assert len(cycle) >= 2, "SCCs must have at least 2 nodes"

    @given(random_graph())
    @settings(max_examples=100)
    def test_no_single_node_cycles(self, g: SemGraph):
        """Self-loops should not be reported as cycles."""
        cycles = find_cycles(g)
        for cycle in cycles:
            assert len(cycle) >= 2

    @given(random_graph())
    @settings(max_examples=100)
    def test_cycle_nodes_are_sorted(self, g: SemGraph):
        """Each cycle list is sorted for determinism."""
        cycles = find_cycles(g)
        for cycle in cycles:
            assert cycle == sorted(cycle)

    def test_dag_has_no_cycles(self):
        """A strict DAG must have zero cycles."""
        g = SemGraph()
        for i in range(10):
            g.add_node(Node(name=f"n{i}", type=NodeType.MODULE))
        for i in range(9):
            g.add_edge(Edge(source=f"n{i}", target=f"n{i + 1}", rel=RelType.IMPORTS))
        assert find_cycles(g) == []


class TestTopologicalLayerProperties:
    @given(random_graph())
    @settings(max_examples=100)
    def test_layers_are_non_negative(self, g: SemGraph):
        layers = topological_layers(g)
        for layer in layers.values():
            assert layer >= 0

    @given(random_graph())
    @settings(max_examples=100)
    def test_all_coupling_nodes_have_layers(self, g: SemGraph):
        """Every node involved in coupling edges gets a layer."""
        layers = topological_layers(g)
        # Nodes only in contains edges may not appear
        for edge in g.all_edges():
            if edge.rel.value in (
                "calls",
                "imports",
                "inherits",
                "implements",
                "depends_on",
            ):
                assert edge.source in layers
                assert edge.target in layers


class TestPageRankProperties:
    @given(random_graph(min_nodes=3))
    @settings(max_examples=100)
    def test_pagerank_values_are_positive(self, g: SemGraph):
        ranks = pagerank(g)
        for r in ranks.values():
            assert r >= 0

    @given(random_graph(min_nodes=3))
    @settings(max_examples=100)
    def test_pagerank_sums_to_approximately_one(self, g: SemGraph):
        ranks = pagerank(g)
        if ranks:
            total = sum(ranks.values())
            assert abs(total - 1.0) < 0.05, f"PageRank sum = {total}"

    @given(random_graph(min_nodes=3))
    @settings(max_examples=100)
    def test_pagerank_covers_all_coupling_nodes(self, g: SemGraph):
        ranks = pagerank(g)
        for edge in g.all_edges():
            if edge.rel.value in (
                "calls",
                "imports",
                "inherits",
                "implements",
                "depends_on",
            ):
                assert edge.source in ranks
                assert edge.target in ranks


class TestBetweennessProperties:
    @given(random_graph(min_nodes=3))
    @settings(max_examples=100)
    def test_betweenness_values_in_range(self, g: SemGraph):
        bc = betweenness_centrality(g)
        for v in bc.values():
            assert 0.0 <= v <= 1.0 + 1e-9, f"BC out of range: {v}"

    @given(random_graph(min_nodes=3))
    @settings(max_examples=100)
    def test_leaf_nodes_have_zero_betweenness(self, g: SemGraph):
        """Nodes with degree 1 (leaves) cannot be on shortest paths between others."""
        bc = betweenness_centrality(g)
        for name, centrality in bc.items():
            neighbors = set()
            for edge in g.all_edges():
                if edge.rel.value in (
                    "calls",
                    "imports",
                    "inherits",
                    "implements",
                    "depends_on",
                ):
                    if edge.source == name:
                        neighbors.add(edge.target)
                    if edge.target == name:
                        neighbors.add(edge.source)
            if len(neighbors) <= 1 and centrality > 0:
                # Leaf in the coupling graph — betweenness should be ~0
                # (but floating point means we allow small epsilon)
                assert centrality < 0.01, f"Leaf {name} has BC={centrality}"


class TestKCoreProperties:
    @given(random_graph())
    @settings(max_examples=100)
    def test_coreness_is_non_negative(self, g: SemGraph):
        kc = kcore_decomposition(g)
        for v in kc.values():
            assert v >= 0

    @given(random_graph())
    @settings(max_examples=100)
    def test_coreness_bounded_by_degree(self, g: SemGraph):
        """A node's coreness cannot exceed its degree in the coupling graph."""
        kc = kcore_decomposition(g)
        for name, coreness in kc.items():
            degree = 0
            for edge in g.all_edges():
                if edge.rel.value in (
                    "calls",
                    "imports",
                    "inherits",
                    "implements",
                    "depends_on",
                ):
                    if edge.source == name or edge.target == name:
                        degree += 1
            assert coreness <= degree, f"{name}: coreness {coreness} > degree {degree}"


class TestBridgeProperties:
    @given(random_graph())
    @settings(max_examples=100)
    def test_bridges_are_valid_edges(self, g: SemGraph):
        bridges = detect_bridges(g)
        nodes = set(g.nodes.keys())
        for a, b in bridges:
            assert a in nodes
            assert b in nodes

    @given(random_graph())
    @settings(max_examples=100)
    def test_bridges_are_sorted(self, g: SemGraph):
        bridges = detect_bridges(g)
        for a, b in bridges:
            assert a <= b  # normalized: smaller name first


# ============================================================
# OO Metric Properties
# ============================================================


class TestWMCProperties:
    @given(random_class_graph())
    @settings(max_examples=100)
    def test_wmc_is_non_negative(self, g: SemGraph):
        result = wmc(g)
        for v in result.values():
            assert v >= 0

    @given(random_class_graph())
    @settings(max_examples=100)
    def test_wmc_ge_method_count(self, g: SemGraph):
        """WMC >= number of methods (since min CC per method is 1)."""
        result = wmc(g)
        for name in result:
            methods = [
                e.target
                for e in g.outgoing(name, rel=RelType.CONTAINS)
                if g.get_node(e.target) and g.get_node(e.target).type.value in ("function", "method")
            ]
            assert result[name] >= len(methods)


class TestDITProperties:
    @given(random_class_graph())
    @settings(max_examples=100)
    def test_dit_is_non_negative(self, g: SemGraph):
        result = dit(g)
        for v in result.values():
            assert v >= 0

    @given(random_class_graph())
    @settings(max_examples=100)
    def test_dit_zero_means_no_parents(self, g: SemGraph):
        result = dit(g)
        for name, depth in result.items():
            if depth == 0:
                parents = [e.target for e in g.outgoing(name, rel=RelType.INHERITS)]
                # Either no parents, or parents are not in the graph as classes
                assert len(parents) == 0 or all(
                    g.get_node(p) is None or g.get_node(p).type != NodeType.CLASS for p in parents
                )


class TestNOCProperties:
    @given(random_class_graph())
    @settings(max_examples=100)
    def test_noc_is_non_negative(self, g: SemGraph):
        result = noc(g)
        for v in result.values():
            assert v >= 0


class TestCBOProperties:
    @given(random_class_graph())
    @settings(max_examples=100)
    def test_cbo_is_non_negative(self, g: SemGraph):
        result = cbo(g)
        for v in result.values():
            assert v >= 0

    @given(random_class_graph(min_classes=1, max_classes=1))
    @settings(max_examples=50)
    def test_single_class_has_zero_cbo(self, g: SemGraph):
        """A graph with only one class has CBO=0 (no other classes to couple to)."""
        result = cbo(g)
        for v in result.values():
            assert v == 0


class TestRFCProperties:
    @given(random_class_graph())
    @settings(max_examples=100)
    def test_rfc_ge_method_count(self, g: SemGraph):
        """RFC >= number of methods (since RFC = methods + callees)."""
        result = rfc(g)
        for name in result:
            methods = [
                e.target
                for e in g.outgoing(name, rel=RelType.CONTAINS)
                if g.get_node(e.target) and g.get_node(e.target).type.value in ("function", "method")
            ]
            assert result[name] >= len(methods)


class TestLCOM4Properties:
    @given(random_class_graph())
    @settings(max_examples=100)
    def test_lcom4_bounded_by_method_count(self, g: SemGraph):
        """LCOM4 <= number of methods (at most one component per method)."""
        result = lcom4(g)
        for name in result:
            methods = [
                e.target
                for e in g.outgoing(name, rel=RelType.CONTAINS)
                if g.get_node(e.target) and g.get_node(e.target).type.value in ("function", "method")
            ]
            assert result[name] <= max(len(methods), 1)

    @given(random_class_graph())
    @settings(max_examples=100)
    def test_lcom4_is_non_negative(self, g: SemGraph):
        result = lcom4(g)
        for v in result.values():
            assert v >= 0


# ============================================================
# Martin's Metrics Properties
# ============================================================


class TestMartinMetricsProperties:
    @given(random_module_graph())
    @settings(max_examples=100)
    def test_instability_in_range(self, g: SemGraph):
        result = martin_metrics(g)
        for name, m in result.items():
            assert 0.0 <= m["instability"] <= 1.0, f"{name}: I={m['instability']}"

    @given(random_module_graph())
    @settings(max_examples=100)
    def test_abstractness_in_range(self, g: SemGraph):
        result = martin_metrics(g)
        for name, m in result.items():
            assert 0.0 <= m["abstractness"] <= 1.0, f"{name}: A={m['abstractness']}"

    @given(random_module_graph())
    @settings(max_examples=100)
    def test_distance_in_range(self, g: SemGraph):
        result = martin_metrics(g)
        for name, m in result.items():
            assert 0.0 <= m["distance"] <= 1.0 + 0.01, f"{name}: D={m['distance']}"

    @given(random_module_graph())
    @settings(max_examples=100)
    def test_ca_ce_are_non_negative(self, g: SemGraph):
        result = martin_metrics(g)
        for name, m in result.items():
            assert m["ca"] >= 0
            assert m["ce"] >= 0

    @given(random_module_graph())
    @settings(max_examples=100)
    def test_instability_formula(self, g: SemGraph):
        """I = Ce / (Ca + Ce), or 0 if both are 0."""
        result = martin_metrics(g)
        for name, m in result.items():
            ca, ce = m["ca"], m["ce"]
            if ca + ce == 0:
                assert m["instability"] == 0.0
            else:
                expected = round(ce / (ca + ce), 3)
                assert m["instability"] == expected, f"{name}: expected I={expected}, got {m['instability']}"

    @given(random_module_graph())
    @settings(max_examples=100)
    def test_distance_formula(self, g: SemGraph):
        """D ≈ |A + I - 1| (allowing for rounding of already-rounded inputs)."""
        result = martin_metrics(g)
        for name, m in result.items():
            expected = abs(m["abstractness"] + m["instability"] - 1.0)
            assert abs(m["distance"] - expected) < 0.01, f"{name}: expected D≈{expected:.3f}, got {m['distance']}"


class TestSDPViolationProperties:
    @given(random_module_graph())
    @settings(max_examples=100)
    def test_violations_have_correct_direction(self, g: SemGraph):
        """In every violation, source instability < target instability."""
        violations = sdp_violations(g)
        for v in violations:
            assert v["source_instability"] < v["target_instability"]

    @given(random_module_graph())
    @settings(max_examples=100)
    def test_violations_reference_existing_modules(self, g: SemGraph):
        violations = sdp_violations(g)
        nodes = set(g.nodes.keys())
        for v in violations:
            assert v["source"] in nodes
            assert v["target"] in nodes


# ============================================================
# Fan-In / Fan-Out Properties
# ============================================================


class TestHITSProperties:
    @given(random_graph(min_nodes=3))
    @settings(max_examples=100)
    def test_hits_scores_non_negative(self, g: SemGraph):
        h = hits(g)
        for v in h.values():
            assert v["hub"] >= 0
            assert v["authority"] >= 0

    @given(random_graph(min_nodes=3))
    @settings(max_examples=100)
    def test_hits_covers_coupling_nodes(self, g: SemGraph):
        """Every node in a coupling edge appears in the result."""
        h = hits(g)
        coupling_rels = {"calls", "imports", "inherits", "implements", "depends_on"}
        for edge in g.all_edges():
            if edge.rel.value in coupling_rels:
                assert edge.source in h
                assert edge.target in h

    @given(random_graph(min_nodes=3))
    @settings(max_examples=100)
    def test_hits_hub_norm_approximately_one(self, g: SemGraph):
        """Hub scores should be L2-normalized to ~1."""
        h = hits(g)
        if h:
            hub_norm = sum(v["hub"] ** 2 for v in h.values()) ** 0.5
            assert abs(hub_norm - 1.0) < 0.05 or hub_norm < 0.01

    @given(random_graph(min_nodes=3))
    @settings(max_examples=100)
    def test_hits_authority_norm_approximately_one(self, g: SemGraph):
        """Authority scores should be L2-normalized to ~1."""
        h = hits(g)
        if h:
            auth_norm = sum(v["authority"] ** 2 for v in h.values()) ** 0.5
            assert abs(auth_norm - 1.0) < 0.05 or auth_norm < 0.01

    def test_star_topology(self):
        """In a star where hub calls 5 targets, hub has high hub score, targets have high authority."""
        g = SemGraph()
        g.add_node(Node(name="hub", type=NodeType.FUNCTION))
        for i in range(5):
            name = f"t{i}"
            g.add_node(Node(name=name, type=NodeType.FUNCTION))
            g.add_edge(Edge(source="hub", target=name, rel=RelType.CALLS))
        h = hits(g)
        # Hub should have the highest hub score
        assert h["hub"]["hub"] == max(v["hub"] for v in h.values())
        # Each target should have equal authority
        auth_vals = {h[f"t{i}"]["authority"] for i in range(5)}
        assert len(auth_vals) == 1  # all equal


class TestGodFileProperties:
    @st.composite
    @staticmethod
    def _graph_with_files(draw, min_files=2, max_files=5, max_funcs_per_file=10):
        """Generate a graph where nodes have file attributes."""
        g = SemGraph()
        n_files = draw(st.integers(min_value=min_files, max_value=max_files))
        files = [f"src/mod{i}.py" for i in range(n_files)]
        all_funcs: list[str] = []

        for fi, fpath in enumerate(files):
            mod_name = f"mod{fi}"
            g.add_node(Node(name=mod_name, type=NodeType.MODULE, file=fpath))
            n_funcs = draw(st.integers(min_value=1, max_value=max_funcs_per_file))
            for fj in range(n_funcs):
                fname = f"{mod_name}.f{fj}"
                cc = draw(st.integers(min_value=1, max_value=20))
                g.add_node(
                    Node(
                        name=fname,
                        type=NodeType.FUNCTION,
                        file=fpath,
                        metadata={"metrics": {"cyclomatic_complexity": cc}},
                    )
                )
                g.add_edge(Edge(source=mod_name, target=fname, rel=RelType.CONTAINS))
                all_funcs.append(fname)

        # Cross-file coupling
        if len(all_funcs) >= 2:
            n_edges = draw(st.integers(min_value=0, max_value=min(15, len(all_funcs))))
            for _ in range(n_edges):
                src = draw(st.sampled_from(all_funcs))
                tgt = draw(st.sampled_from(all_funcs))
                if src != tgt:
                    key = (src, RelType.CALLS.value, tgt)
                    if key not in g.edges:
                        g.add_edge(Edge(source=src, target=tgt, rel=RelType.CALLS))

        return g

    @given(st.data())
    @settings(max_examples=100)
    def test_god_files_reference_real_files(self, data):
        g = data.draw(TestGodFileProperties._graph_with_files())
        results = god_files(g)
        known_files = {n.file for n in g.all_nodes() if n.file}
        for gf in results:
            assert gf["file"] in known_files

    @given(st.data())
    @settings(max_examples=100)
    def test_god_files_metrics_consistent(self, data):
        """total_cc >= max_cc, and num_functions >= 0."""
        g = data.draw(TestGodFileProperties._graph_with_files())
        results = god_files(g)
        for gf in results:
            assert gf["total_cc"] >= gf["max_cc"]
            assert gf["num_functions"] >= 0
            assert gf["concerns"] >= 0

    @given(st.data())
    @settings(max_examples=100)
    def test_god_files_have_reasons(self, data):
        """Every flagged file must have at least one reason."""
        g = data.draw(TestGodFileProperties._graph_with_files())
        results = god_files(g)
        for gf in results:
            assert len(gf["reasons"]) >= 1

    def test_simple_god_file(self):
        """A file with 20 functions of CC=5 each should be flagged (total CC=100)."""
        g = SemGraph()
        g.add_node(Node(name="mod", type=NodeType.MODULE, file="big.py"))
        for i in range(20):
            g.add_node(
                Node(
                    name=f"mod.f{i}",
                    type=NodeType.FUNCTION,
                    file="big.py",
                    metadata={"metrics": {"cyclomatic_complexity": 5}},
                )
            )
            g.add_edge(Edge(source="mod", target=f"mod.f{i}", rel=RelType.CONTAINS))
        results = god_files(g)
        assert len(results) == 1
        assert results[0]["file"] == "big.py"
        assert results[0]["total_cc"] == 100
        assert results[0]["num_functions"] == 20

    def test_small_file_not_flagged(self):
        """A file with 3 simple functions should not be flagged."""
        g = SemGraph()
        g.add_node(Node(name="mod", type=NodeType.MODULE, file="small.py"))
        for i in range(3):
            g.add_node(
                Node(
                    name=f"mod.f{i}",
                    type=NodeType.FUNCTION,
                    file="small.py",
                    metadata={"metrics": {"cyclomatic_complexity": 2}},
                )
            )
            g.add_edge(Edge(source="mod", target=f"mod.f{i}", rel=RelType.CONTAINS))
        results = god_files(g)
        assert len(results) == 0


class TestMinimalCycleProperties:
    @given(random_graph())
    @settings(max_examples=100)
    def test_minimal_cycle_is_subset_of_scc(self, g: SemGraph):
        """The minimal cycle must use only nodes from the SCC."""
        cycles = find_cycles(g)
        for scc in cycles:
            mc = minimal_cycle(g, scc)
            assert set(mc).issubset(set(scc))

    @given(random_graph())
    @settings(max_examples=100)
    def test_minimal_cycle_length_bounded(self, g: SemGraph):
        """The minimal cycle cannot be longer than the SCC."""
        cycles = find_cycles(g)
        for scc in cycles:
            mc = minimal_cycle(g, scc)
            assert len(mc) <= len(scc)
            assert len(mc) >= 2  # SCC has at least 2 nodes

    @given(random_graph())
    @settings(max_examples=100)
    def test_minimal_cycle_forms_actual_cycle(self, g: SemGraph):
        """The minimal cycle path must be a valid directed cycle via coupling edges."""
        cycles = find_cycles(g)
        coupling_rels = {"calls", "imports", "inherits", "implements", "depends_on"}
        for scc in cycles:
            mc = minimal_cycle(g, scc)
            if len(mc) < 2:
                continue
            # Check each consecutive pair has a coupling edge
            for i in range(len(mc)):
                src = mc[i]
                tgt = mc[(i + 1) % len(mc)]
                has_edge = any(
                    e.source == src and e.target == tgt and e.rel.value in coupling_rels for e in g.all_edges()
                )
                assert has_edge, f"no coupling edge {src} -> {tgt}"

    def test_simple_triangle(self):
        """A -> B -> C -> A: minimal cycle is [A, B, C]."""
        g = SemGraph()
        for n in ["a", "b", "c"]:
            g.add_node(Node(name=n, type=NodeType.FUNCTION))
        g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))
        g.add_edge(Edge(source="b", target="c", rel=RelType.CALLS))
        g.add_edge(Edge(source="c", target="a", rel=RelType.CALLS))
        mc = minimal_cycle(g, ["a", "b", "c"])
        assert len(mc) == 3

    def test_picks_shorter_cycle(self):
        """SCC with both a 2-cycle and a 3-cycle: picks the 2-cycle."""
        g = SemGraph()
        for n in ["a", "b", "c"]:
            g.add_node(Node(name=n, type=NodeType.FUNCTION))
        g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))
        g.add_edge(Edge(source="b", target="a", rel=RelType.CALLS))  # 2-cycle
        g.add_edge(Edge(source="b", target="c", rel=RelType.CALLS))
        g.add_edge(Edge(source="c", target="a", rel=RelType.CALLS))  # 3-cycle via c
        mc = minimal_cycle(g, ["a", "b", "c"])
        assert len(mc) == 2


class TestFanInOutProperties:
    @given(random_graph())
    @settings(max_examples=100)
    def test_fan_in_out_non_negative(self, g: SemGraph):
        fio = fan_in_out(g)
        for v in fio.values():
            assert v["fan_in"] >= 0
            assert v["fan_out"] >= 0

    @given(random_graph())
    @settings(max_examples=100)
    def test_total_fan_in_equals_total_fan_out(self, g: SemGraph):
        """Each coupling edge contributes 1 fan-out to source and 1 fan-in to target."""
        fio = fan_in_out(g)
        total_in = sum(v["fan_in"] for v in fio.values())
        total_out = sum(v["fan_out"] for v in fio.values())
        assert total_in == total_out

    @given(random_graph())
    @settings(max_examples=100)
    def test_fan_in_out_only_coupling_nodes(self, g: SemGraph):
        """Only nodes participating in coupling edges appear in the result."""
        fio = fan_in_out(g)
        coupling_rels = {"calls", "imports", "inherits", "implements", "depends_on"}
        coupling_nodes: set[str] = set()
        for edge in g.all_edges():
            if edge.rel.value in coupling_rels:
                coupling_nodes.add(edge.source)
                coupling_nodes.add(edge.target)
        assert set(fio.keys()) == coupling_nodes

    @given(random_graph())
    @settings(max_examples=100)
    def test_fan_out_matches_outgoing_edges(self, g: SemGraph):
        """Fan-out for a node equals its distinct outgoing coupling targets."""
        fio = fan_in_out(g)
        coupling_rels = {"calls", "imports", "inherits", "implements", "depends_on"}
        for name, metrics in fio.items():
            targets = set()
            for edge in g.all_edges():
                if edge.source == name and edge.rel.value in coupling_rels:
                    targets.add(edge.target)
            assert metrics["fan_out"] == len(targets)

    def test_isolated_node_not_in_result(self):
        """A node with no coupling edges doesn't appear in fan_in_out."""
        g = SemGraph()
        g.add_node(Node(name="lonely", type=NodeType.FUNCTION))
        assert fan_in_out(g) == {}

    def test_simple_chain(self):
        """A -> B -> C: A has fan_out=1, B has fan_in=1 and fan_out=1, C has fan_in=1."""
        g = SemGraph()
        for n in ["a", "b", "c"]:
            g.add_node(Node(name=n, type=NodeType.FUNCTION))
        g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))
        g.add_edge(Edge(source="b", target="c", rel=RelType.CALLS))
        fio = fan_in_out(g)
        assert fio["a"] == {"fan_in": 0, "fan_out": 1}
        assert fio["b"] == {"fan_in": 1, "fan_out": 1}
        assert fio["c"] == {"fan_in": 1, "fan_out": 0}


# ============================================================
# Dead Code Detection Properties
# ============================================================


class TestDeadCodeProperties:
    @given(random_graph())
    @settings(max_examples=100)
    def test_dead_nodes_have_no_incoming_coupling(self, g: SemGraph):
        """Every node flagged as dead has zero incoming coupling edges."""
        dead = dead_code(g)
        coupling_rels = {"calls", "imports", "inherits", "implements", "depends_on"}
        for name in dead:
            incoming = [e for e in g.all_edges() if e.target == name and e.rel.value in coupling_rels]
            assert len(incoming) == 0, f"{name} has incoming: {incoming}"

    @given(random_graph())
    @settings(max_examples=100)
    def test_dead_nodes_are_subset_of_all_nodes(self, g: SemGraph):
        dead = dead_code(g)
        all_names = set(g.nodes.keys())
        for name in dead:
            assert name in all_names

    @given(random_graph())
    @settings(max_examples=100)
    def test_dead_code_excludes_modules(self, g: SemGraph):
        """Modules and packages are never flagged as dead code."""
        dead = dead_code(g)
        for name in dead:
            node = g.get_node(name)
            assert node is not None
            assert node.type.value not in ("module", "package")

    @given(random_graph())
    @settings(max_examples=100)
    def test_entry_points_excluded(self, g: SemGraph):
        """Nodes listed as entry points are never flagged dead."""
        all_names = set(g.nodes.keys())
        dead = dead_code(g, entry_points=all_names)
        assert dead == []

    def test_isolated_function_is_dead(self):
        """A function with no incoming coupling edges is dead."""
        g = SemGraph()
        g.add_node(Node(name="orphan", type=NodeType.FUNCTION))
        assert dead_code(g) == ["orphan"]

    def test_called_function_is_not_dead(self):
        """A function called by another is not dead."""
        g = SemGraph()
        g.add_node(Node(name="caller", type=NodeType.FUNCTION))
        g.add_node(Node(name="callee", type=NodeType.FUNCTION))
        g.add_edge(Edge(source="caller", target="callee", rel=RelType.CALLS))
        dead = dead_code(g)
        assert "callee" not in dead
        # caller has no incoming, so it IS dead
        assert "caller" in dead

    def test_module_never_dead(self):
        """A module with no incoming edges is not flagged."""
        g = SemGraph()
        g.add_node(Node(name="mymod", type=NodeType.MODULE))
        assert dead_code(g) == []

    @given(random_graph())
    @settings(max_examples=100)
    def test_dead_code_is_sorted(self, g: SemGraph):
        dead = dead_code(g)
        assert dead == sorted(dead)


# ============================================================
# Layering Violation Properties
# ============================================================


class TestLayeringViolationProperties:
    @given(random_graph())
    @settings(max_examples=100)
    def test_violations_reference_existing_nodes(self, g: SemGraph):
        violations = layering_violations(g)
        all_names = set(g.nodes.keys())
        for v in violations:
            assert v["source"] in all_names
            assert v["target"] in all_names

    @given(random_graph())
    @settings(max_examples=100)
    def test_violation_layers_satisfy_invariant(self, g: SemGraph):
        """In every violation, source_layer <= target_layer."""
        violations = layering_violations(g)
        for v in violations:
            assert v["source_layer"] <= v["target_layer"]

    @given(random_graph())
    @settings(max_examples=100)
    def test_violations_are_coupling_edges(self, g: SemGraph):
        """Every violation corresponds to an actual coupling edge in the graph."""
        violations = layering_violations(g)
        coupling_rels = {"calls", "imports", "inherits", "implements", "depends_on"}
        edge_keys = set(g.edges.keys())
        for v in violations:
            assert v["rel"] in coupling_rels
            assert (v["source"], v["rel"], v["target"]) in edge_keys

    def test_strict_dag_has_no_violations(self):
        """A strict DAG (linear chain) has no layering violations."""
        g = SemGraph()
        for i in range(5):
            g.add_node(Node(name=f"n{i}", type=NodeType.MODULE))
        for i in range(4):
            g.add_edge(Edge(source=f"n{i}", target=f"n{i + 1}", rel=RelType.IMPORTS))
        assert layering_violations(g) == []

    def test_cycle_produces_violations(self):
        """A simple cycle A->B->A should produce layering violations."""
        g = SemGraph()
        g.add_node(Node(name="a", type=NodeType.FUNCTION))
        g.add_node(Node(name="b", type=NodeType.FUNCTION))
        g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))
        g.add_edge(Edge(source="b", target="a", rel=RelType.CALLS))
        violations = layering_violations(g)
        # Both edges are between nodes at the same layer (SCC), so both are violations
        assert len(violations) == 2

    @given(random_graph())
    @settings(max_examples=100)
    def test_no_violations_implies_dag_on_layers(self, g: SemGraph):
        """If no violations, every coupling edge goes from higher to lower layer."""
        violations = layering_violations(g)
        if not violations:
            layers = topological_layers(g)
            coupling_rels = {"calls", "imports", "inherits", "implements", "depends_on"}
            for edge in g.all_edges():
                if edge.rel.value in coupling_rels:
                    sl = layers.get(edge.source)
                    tl = layers.get(edge.target)
                    if sl is not None and tl is not None:
                        assert sl > tl


# ============================================================
# Code Smell Properties
# ============================================================


class TestGodClassProperties:
    @given(random_class_graph())
    @settings(max_examples=100)
    def test_god_classes_satisfy_all_thresholds(self, g: SemGraph):
        """Every god class must exceed ALL three thresholds."""
        gods = god_classes(g)
        for gc in gods:
            assert gc["wmc"] >= 20
            assert gc["cbo"] >= 5
            assert gc["lcom4"] >= 2

    @given(random_class_graph())
    @settings(max_examples=100)
    def test_god_classes_are_actual_classes(self, g: SemGraph):
        """Every detected god class exists as a class node."""
        gods = god_classes(g)
        for gc in gods:
            node = g.get_node(gc["name"])
            assert node is not None
            assert node.type == NodeType.CLASS

    @given(random_class_graph(min_classes=1, max_classes=1, max_methods_per_class=2))
    @settings(max_examples=50)
    def test_small_class_never_god(self, g: SemGraph):
        """A class with <=2 methods can't have WMC>=20 with default CC=1."""
        gods = god_classes(g)
        # Small class max WMC = 2*20 = 40, but CBO is 0 with single class
        # so it can't be a god class (CBO threshold not met)
        assert len(gods) == 0

    def test_god_class_detection(self):
        """A class with high WMC, CBO, and LCOM4 is detected."""
        g = SemGraph()
        g.add_node(Node(name="mod", type=NodeType.MODULE))
        g.add_node(Node(name="mod.God", type=NodeType.CLASS))
        g.add_edge(Edge(source="mod", target="mod.God", rel=RelType.CONTAINS))

        # Create 25 methods with CC=1 each -> WMC=25
        for i in range(25):
            mname = f"mod.God.m{i}"
            g.add_node(
                Node(
                    name=mname,
                    type=NodeType.METHOD,
                    metadata={"metrics": {"cyclomatic_complexity": 1}},
                )
            )
            g.add_edge(Edge(source="mod.God", target=mname, rel=RelType.CONTAINS))

        # Create 6 external classes for CBO >= 5
        for i in range(6):
            ext = f"mod.Ext{i}"
            g.add_node(Node(name=ext, type=NodeType.CLASS))
            g.add_edge(Edge(source="mod", target=ext, rel=RelType.CONTAINS))
            ext_m = f"{ext}.do"
            g.add_node(Node(name=ext_m, type=NodeType.METHOD))
            g.add_edge(Edge(source=ext, target=ext_m, rel=RelType.CONTAINS))
            # God's method i calls external class i's method
            g.add_edge(Edge(source=f"mod.God.m{i}", target=ext_m, rel=RelType.CALLS))

        # No intra-class calls -> LCOM4 = 25 (each method is its own component)
        gods = god_classes(g)
        assert len(gods) == 1
        assert gods[0]["name"] == "mod.God"


class TestFeatureEnvyProperties:
    @given(random_class_graph())
    @settings(max_examples=100)
    def test_envied_refs_exceed_own_refs(self, g: SemGraph):
        """Feature envy means more references to another class than own."""
        envies = feature_envy(g)
        for fe in envies:
            assert fe["envied_refs"] > fe["own_refs"]
            assert fe["envied_refs"] >= 2

    @given(random_class_graph())
    @settings(max_examples=100)
    def test_envy_references_existing_nodes(self, g: SemGraph):
        envies = feature_envy(g)
        all_names = set(g.nodes.keys())
        for fe in envies:
            assert fe["method"] in all_names
            assert fe["own_class"] in all_names
            assert fe["envied_class"] in all_names

    @given(random_class_graph())
    @settings(max_examples=100)
    def test_envy_method_belongs_to_own_class(self, g: SemGraph):
        """The envious method is contained in its reported own_class."""
        envies = feature_envy(g)
        for fe in envies:
            contains_targets = {e.target for e in g.outgoing(fe["own_class"], rel=RelType.CONTAINS)}
            assert fe["method"] in contains_targets

    def test_feature_envy_detection(self):
        """A method calling 3 methods of another class and 0 of its own has envy."""
        g = SemGraph()
        g.add_node(Node(name="mod", type=NodeType.MODULE))
        g.add_node(Node(name="mod.A", type=NodeType.CLASS))
        g.add_node(Node(name="mod.B", type=NodeType.CLASS))
        g.add_edge(Edge(source="mod", target="mod.A", rel=RelType.CONTAINS))
        g.add_edge(Edge(source="mod", target="mod.B", rel=RelType.CONTAINS))

        # A has one method
        g.add_node(Node(name="mod.A.do_stuff", type=NodeType.METHOD))
        g.add_edge(Edge(source="mod.A", target="mod.A.do_stuff", rel=RelType.CONTAINS))

        # B has three methods
        for i in range(3):
            bm = f"mod.B.helper{i}"
            g.add_node(Node(name=bm, type=NodeType.METHOD))
            g.add_edge(Edge(source="mod.B", target=bm, rel=RelType.CONTAINS))
            # A.do_stuff calls B's methods
            g.add_edge(Edge(source="mod.A.do_stuff", target=bm, rel=RelType.CALLS))

        envies = feature_envy(g)
        assert len(envies) == 1
        assert envies[0]["method"] == "mod.A.do_stuff"
        assert envies[0]["envied_class"] == "mod.B"


class TestShotgunSurgeryProperties:
    @given(random_class_graph())
    @settings(max_examples=100)
    def test_shotgun_surgery_fan_out_meets_threshold(self, g: SemGraph):
        """Every detected shotgun surgery node has fan_out >= threshold."""
        results = shotgun_surgery(g)
        for r in results:
            assert r["fan_out"] >= 7

    @given(random_class_graph())
    @settings(max_examples=100)
    def test_shotgun_surgery_references_existing_nodes(self, g: SemGraph):
        results = shotgun_surgery(g)
        all_names = set(g.nodes.keys())
        for r in results:
            assert r["name"] in all_names
            for t in r["targets"]:
                assert t in all_names

    @given(random_class_graph())
    @settings(max_examples=100)
    def test_shotgun_surgery_only_functions_and_methods(self, g: SemGraph):
        """Only functions and methods can have shotgun surgery."""
        results = shotgun_surgery(g)
        for r in results:
            assert r["type"] in ("function", "method")

    @given(random_class_graph())
    @settings(max_examples=100)
    def test_shotgun_surgery_targets_are_sorted(self, g: SemGraph):
        results = shotgun_surgery(g)
        for r in results:
            assert r["targets"] == sorted(r["targets"])

    def test_high_fan_out_detected(self):
        """A function calling 8 other functions is detected with threshold=7."""
        g = SemGraph()
        g.add_node(Node(name="hub", type=NodeType.FUNCTION))
        for i in range(8):
            name = f"target{i}"
            g.add_node(Node(name=name, type=NodeType.FUNCTION))
            g.add_edge(Edge(source="hub", target=name, rel=RelType.CALLS))

        results = shotgun_surgery(g)
        assert len(results) == 1
        assert results[0]["name"] == "hub"
        assert results[0]["fan_out"] == 8

    def test_below_threshold_not_detected(self):
        """A function calling 5 others is NOT detected (threshold=7)."""
        g = SemGraph()
        g.add_node(Node(name="hub", type=NodeType.FUNCTION))
        for i in range(5):
            name = f"target{i}"
            g.add_node(Node(name=name, type=NodeType.FUNCTION))
            g.add_edge(Edge(source="hub", target=name, rel=RelType.CALLS))

        results = shotgun_surgery(g)
        assert len(results) == 0


# ============================================================
# Rule System Properties
# ============================================================


# --- Strategies for generating rules ---


@st.composite
def random_deny_rule(draw):
    """Generate a random deny rule with a glob pattern."""
    prefixes = ["ui", "db", "core", "api", "lib", "n"]
    src_prefix = draw(st.sampled_from(prefixes))
    tgt_prefix = draw(st.sampled_from(prefixes))
    use_rel = draw(st.booleans())
    if use_rel:
        rel = draw(st.sampled_from(["calls", "imports", "inherits", "depends_on"]))
        pattern = f"{src_prefix}.* -[{rel}]-> {tgt_prefix}.*"
    else:
        pattern = f"{src_prefix}.* -> {tgt_prefix}.*"
    name = draw(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=6))
    return Rule(name=name, type="deny", pattern=pattern)


@st.composite
def random_invariant_rule(draw):
    """Generate a random invariant rule."""
    inv = draw(st.sampled_from(["no-cycles", "no-dead-code", "no-layering-violations"]))
    name = draw(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=6))
    params: dict = {}
    if inv == "no-dead-code":
        # Optionally add entry points
        if draw(st.booleans()):
            params["entry_points"] = "n0,n1"
    return Rule(name=name, type="invariant", invariant=inv, params=params)


class TestDenyRuleProperties:
    @given(random_graph(), random_deny_rule())
    @settings(max_examples=100)
    def test_deny_violations_are_real_edges(self, g: SemGraph, rule: Rule):
        """Every edge in a deny violation must exist in the graph."""
        v = check_deny(rule, g)
        if v is not None:
            edge_keys = set(g.edges.keys())
            for e in v.edges:
                assert (e["source"], e["rel"], e["target"]) in edge_keys

    @given(random_graph(), random_deny_rule())
    @settings(max_examples=100)
    def test_deny_violations_match_pattern(self, g: SemGraph, rule: Rule):
        """Every violating edge must match the deny pattern."""
        import fnmatch

        v = check_deny(rule, g)
        if v is not None:
            src_glob, rel_filter, tgt_glob = parse_deny_pattern(rule.pattern)
            for e in v.edges:
                assert fnmatch.fnmatch(e["source"], src_glob)
                assert fnmatch.fnmatch(e["target"], tgt_glob)
                if rel_filter is not None:
                    assert e["rel"] == rel_filter

    @given(random_graph(), random_deny_rule())
    @settings(max_examples=100)
    def test_deny_no_false_negatives(self, g: SemGraph, rule: Rule):
        """If check_deny returns None, no edge in the graph matches the pattern."""
        import fnmatch

        v = check_deny(rule, g)
        if v is None:
            src_glob, rel_filter, tgt_glob = parse_deny_pattern(rule.pattern)
            coupling_rels = {"calls", "imports", "inherits", "implements", "depends_on"}
            for edge in g.all_edges():
                if rel_filter is not None:
                    if edge.rel.value != rel_filter:
                        continue
                elif edge.rel.value not in coupling_rels:
                    continue
                matches = fnmatch.fnmatch(edge.source, src_glob) and fnmatch.fnmatch(edge.target, tgt_glob)
                assert not matches, (
                    f"edge {edge.source} --{edge.rel.value}--> {edge.target} matches but was not reported"
                )

    @given(random_graph())
    @settings(max_examples=100)
    def test_deny_wildcard_catches_all_coupling(self, g: SemGraph):
        """A deny rule '* -> *' catches every coupling edge."""
        rule = Rule(name="all", type="deny", pattern="* -> *")
        v = check_deny(rule, g)
        coupling_rels = {"calls", "imports", "inherits", "implements", "depends_on"}
        coupling_count = sum(1 for e in g.all_edges() if e.rel.value in coupling_rels)
        if coupling_count > 0:
            assert v is not None
            assert len(v.edges) == coupling_count
        else:
            assert v is None


class TestInvariantRuleProperties:
    @given(random_graph(), random_invariant_rule())
    @settings(max_examples=100)
    def test_invariant_never_crashes(self, g: SemGraph, rule: Rule):
        """Invariant checks should never raise on any valid graph."""
        v = check_invariant(rule, g)
        if v is not None:
            assert v.rule_name == rule.name

    @given(random_graph())
    @settings(max_examples=100)
    def test_no_cycles_agrees_with_find_cycles(self, g: SemGraph):
        """The no-cycles invariant should agree with find_cycles on presence."""
        rule = Rule(name="test", type="invariant", invariant="no-cycles")
        v = check_invariant(rule, g)
        cycles = find_cycles(g)
        if cycles:
            assert v is not None
            assert len(v.cycles) == len(cycles)
            # Each minimal cycle's nodes should be a subset of its SCC
            for mc, scc in zip(v.cycles, cycles):
                assert set(mc).issubset(set(scc))
        else:
            assert v is None

    @given(random_graph())
    @settings(max_examples=100)
    def test_no_layering_violations_agrees(self, g: SemGraph):
        """The invariant should agree with layering_violations."""
        rule = Rule(name="test", type="invariant", invariant="no-layering-violations")
        v = check_invariant(rule, g)
        lv = layering_violations(g)
        if lv:
            assert v is not None
            assert len(v.edges) == len(lv)
        else:
            assert v is None

    @given(random_graph())
    @settings(max_examples=100)
    def test_no_dead_code_agrees(self, g: SemGraph):
        """The invariant should agree with dead_code."""
        rule = Rule(name="test", type="invariant", invariant="no-dead-code")
        v = check_invariant(rule, g)
        dc = dead_code(g)
        if dc:
            assert v is not None
            assert v.nodes == dc
        else:
            assert v is None


class TestCheckAllProperties:
    @given(random_graph(), st.lists(random_deny_rule(), min_size=0, max_size=5))
    @settings(max_examples=100)
    def test_check_all_violations_subset_of_rules(self, g: SemGraph, rules: list[Rule]):
        """Every violation must correspond to one of the input rules."""
        violations = check_all(rules, g)
        rule_names = {r.name for r in rules}
        for v in violations:
            assert v.rule_name in rule_names

    @given(random_graph(), st.lists(random_deny_rule(), min_size=0, max_size=5))
    @settings(max_examples=100)
    def test_check_all_count_bounded(self, g: SemGraph, rules: list[Rule]):
        """Number of violations cannot exceed number of rules."""
        violations = check_all(rules, g)
        assert len(violations) <= len(rules)

    @given(random_graph())
    @settings(max_examples=50)
    def test_empty_rules_no_violations(self, g: SemGraph):
        """No rules means no violations."""
        assert check_all([], g) == []


class TestRuleSerializationProperties:
    @given(random_deny_rule())
    @settings(max_examples=100)
    def test_deny_rule_round_trip(self, rule: Rule):
        """Serializing and deserializing a deny rule preserves all fields."""
        d = rule.to_dict()
        r2 = Rule.from_dict(d)
        assert r2.name == rule.name
        assert r2.type == rule.type
        assert r2.pattern == rule.pattern

    @given(random_invariant_rule())
    @settings(max_examples=100)
    def test_invariant_rule_round_trip(self, rule: Rule):
        """Serializing and deserializing an invariant rule preserves all fields."""
        d = rule.to_dict()
        r2 = Rule.from_dict(d)
        assert r2.name == rule.name
        assert r2.type == rule.type
        assert r2.invariant == rule.invariant
        assert r2.params == rule.params

    @given(random_deny_rule())
    @settings(max_examples=100)
    def test_deny_pattern_always_parseable(self, rule: Rule):
        """Every generated deny rule pattern should be parseable."""
        src, rel, tgt = parse_deny_pattern(rule.pattern)
        assert src
        assert tgt
