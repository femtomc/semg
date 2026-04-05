"""Tests for object-oriented and module-level metrics."""

from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType
from smg.oo_metrics import (
    cbo,
    dit,
    lcom4,
    martin_metrics,
    noc,
    rfc,
    sdp_violations,
    wmc,
)


def _node(name: str, t: NodeType = NodeType.MODULE, **kwargs) -> Node:
    return Node(name=name, type=t, **kwargs)


def _edge(src: str, tgt: str, rel: RelType = RelType.CONTAINS) -> Edge:
    return Edge(source=src, target=tgt, rel=rel)


def _build_class_graph() -> SemGraph:
    """Build a graph with two classes, methods with known CC, and call edges.

    Structure:
      module_a
        ClassA
          method1 (CC=2)
          method2 (CC=3)
          method3 (CC=5)   -- calls ClassB.helper
        ClassB
          helper (CC=1)
          util (CC=1)       -- calls ClassB.helper (intra-class)
      module_b
        ClassC(ClassA)      -- inherits ClassA
          override1 (CC=1)
    """
    g = SemGraph()

    # Modules
    g.add_node(_node("module_a"))
    g.add_node(_node("module_b"))

    # ClassA
    g.add_node(_node("module_a.ClassA", NodeType.CLASS))
    g.add_edge(_edge("module_a", "module_a.ClassA"))

    g.add_node(
        _node(
            "module_a.ClassA.method1",
            NodeType.METHOD,
            metadata={"metrics": {"cyclomatic_complexity": 2}},
        )
    )
    g.add_node(
        _node(
            "module_a.ClassA.method2",
            NodeType.METHOD,
            metadata={"metrics": {"cyclomatic_complexity": 3}},
        )
    )
    g.add_node(
        _node(
            "module_a.ClassA.method3",
            NodeType.METHOD,
            metadata={"metrics": {"cyclomatic_complexity": 5}},
        )
    )
    g.add_edge(_edge("module_a.ClassA", "module_a.ClassA.method1"))
    g.add_edge(_edge("module_a.ClassA", "module_a.ClassA.method2"))
    g.add_edge(_edge("module_a.ClassA", "module_a.ClassA.method3"))

    # ClassB
    g.add_node(_node("module_a.ClassB", NodeType.CLASS))
    g.add_edge(_edge("module_a", "module_a.ClassB"))

    g.add_node(
        _node(
            "module_a.ClassB.helper",
            NodeType.METHOD,
            metadata={"metrics": {"cyclomatic_complexity": 1}},
        )
    )
    g.add_node(
        _node(
            "module_a.ClassB.util",
            NodeType.METHOD,
            metadata={"metrics": {"cyclomatic_complexity": 1}},
        )
    )
    g.add_edge(_edge("module_a.ClassB", "module_a.ClassB.helper"))
    g.add_edge(_edge("module_a.ClassB", "module_a.ClassB.util"))

    # ClassA.method3 calls ClassB.helper (cross-class)
    g.add_edge(
        Edge(
            source="module_a.ClassA.method3",
            target="module_a.ClassB.helper",
            rel=RelType.CALLS,
        )
    )

    # ClassB.util calls ClassB.helper (intra-class)
    g.add_edge(
        Edge(
            source="module_a.ClassB.util",
            target="module_a.ClassB.helper",
            rel=RelType.CALLS,
        )
    )

    # ClassC inherits ClassA (in module_b)
    g.add_node(_node("module_b.ClassC", NodeType.CLASS))
    g.add_edge(_edge("module_b", "module_b.ClassC"))
    g.add_edge(Edge(source="module_b.ClassC", target="module_a.ClassA", rel=RelType.INHERITS))

    g.add_node(
        _node(
            "module_b.ClassC.override1",
            NodeType.METHOD,
            metadata={"metrics": {"cyclomatic_complexity": 1}},
        )
    )
    g.add_edge(_edge("module_b.ClassC", "module_b.ClassC.override1"))

    # Module imports
    g.add_edge(Edge(source="module_b", target="module_a", rel=RelType.IMPORTS))

    return g


# --- WMC ---


def test_wmc():
    g = _build_class_graph()
    result = wmc(g)
    assert result["module_a.ClassA"] == 10  # 2+3+5
    assert result["module_a.ClassB"] == 2  # 1+1
    assert result["module_b.ClassC"] == 1  # 1


def test_wmc_empty_class():
    g = SemGraph()
    g.add_node(_node("m"))
    g.add_node(_node("m.Empty", NodeType.CLASS))
    g.add_edge(_edge("m", "m.Empty"))
    result = wmc(g)
    assert result["m.Empty"] == 0


# --- DIT ---


def test_dit_no_inheritance():
    g = _build_class_graph()
    result = dit(g)
    assert result["module_a.ClassA"] == 0
    assert result["module_a.ClassB"] == 0


def test_dit_single_level():
    g = _build_class_graph()
    result = dit(g)
    assert result["module_b.ClassC"] == 1  # ClassC -> ClassA


def test_dit_multi_level():
    """A -> B -> C: DIT(C) = 2."""
    g = SemGraph()
    g.add_node(_node("a", NodeType.CLASS))
    g.add_node(_node("b", NodeType.CLASS))
    g.add_node(_node("c", NodeType.CLASS))
    g.add_edge(Edge(source="b", target="a", rel=RelType.INHERITS))
    g.add_edge(Edge(source="c", target="b", rel=RelType.INHERITS))
    result = dit(g)
    assert result["a"] == 0
    assert result["b"] == 1
    assert result["c"] == 2


# --- NOC ---


def test_noc():
    g = _build_class_graph()
    result = noc(g)
    assert result["module_a.ClassA"] == 1  # ClassC inherits from ClassA
    assert result["module_a.ClassB"] == 0
    assert result["module_b.ClassC"] == 0


def test_noc_multiple_children():
    g = SemGraph()
    g.add_node(_node("parent", NodeType.CLASS))
    for i in range(4):
        name = f"child{i}"
        g.add_node(_node(name, NodeType.CLASS))
        g.add_edge(Edge(source=name, target="parent", rel=RelType.INHERITS))
    result = noc(g)
    assert result["parent"] == 4


# --- CBO ---


def test_cbo():
    g = _build_class_graph()
    result = cbo(g)
    # ClassA.method3 calls ClassB.helper -> coupled to ClassB
    assert result["module_a.ClassA"] >= 1
    # ClassB.helper is called by ClassA.method3 -> coupled to ClassA
    assert result["module_a.ClassB"] >= 1


def test_cbo_isolated():
    """A class with no external coupling has CBO=0."""
    g = SemGraph()
    g.add_node(_node("m"))
    g.add_node(_node("m.Isolated", NodeType.CLASS))
    g.add_edge(_edge("m", "m.Isolated"))
    g.add_node(_node("m.Isolated.do_stuff", NodeType.METHOD))
    g.add_edge(_edge("m.Isolated", "m.Isolated.do_stuff"))
    result = cbo(g)
    assert result["m.Isolated"] == 0


# --- RFC ---


def test_rfc():
    g = _build_class_graph()
    result = rfc(g)
    # ClassA: 3 methods + 1 external callee (ClassB.helper) = 4
    assert result["module_a.ClassA"] == 4
    # ClassB: 2 methods + 1 internal callee (helper, but it's already counted as method)
    # util calls helper which is in ClassB. So callees = {helper} but helper is already a method.
    # RFC = methods + distinct callees. helper is both a method AND a callee, but we count distinct.
    # 2 methods + {helper} = 2 + 1 = 3? No: the set of callees is {ClassB.helper}.
    # The set of methods is {helper, util}. The union would be methods + external callees.
    # RFC counts methods + ALL distinct callees (including internal).
    # ClassB: 2 methods, util->helper (1 callee) = 3
    assert result["module_a.ClassB"] == 3


def test_rfc_no_calls():
    g = SemGraph()
    g.add_node(_node("m"))
    g.add_node(_node("m.C", NodeType.CLASS))
    g.add_edge(_edge("m", "m.C"))
    g.add_node(_node("m.C.f", NodeType.METHOD))
    g.add_edge(_edge("m.C", "m.C.f"))
    result = rfc(g)
    assert result["m.C"] == 1  # 1 method, 0 callees


# --- LCOM4 ---


def test_lcom4_fully_connected():
    """All methods call each other -> 1 component."""
    g = SemGraph()
    g.add_node(_node("m"))
    g.add_node(_node("m.C", NodeType.CLASS))
    g.add_edge(_edge("m", "m.C"))
    for i in range(3):
        g.add_node(_node(f"m.C.f{i}", NodeType.METHOD))
        g.add_edge(_edge("m.C", f"m.C.f{i}"))
    g.add_edge(Edge(source="m.C.f0", target="m.C.f1", rel=RelType.CALLS))
    g.add_edge(Edge(source="m.C.f1", target="m.C.f2", rel=RelType.CALLS))
    result = lcom4(g)
    assert result["m.C"] == 1


def test_lcom4_disjoint():
    """Two groups of methods with no interaction -> 2 components."""
    g = SemGraph()
    g.add_node(_node("m"))
    g.add_node(_node("m.C", NodeType.CLASS))
    g.add_edge(_edge("m", "m.C"))
    # Group 1: f0 calls f1
    g.add_node(_node("m.C.f0", NodeType.METHOD))
    g.add_node(_node("m.C.f1", NodeType.METHOD))
    g.add_edge(_edge("m.C", "m.C.f0"))
    g.add_edge(_edge("m.C", "m.C.f1"))
    g.add_edge(Edge(source="m.C.f0", target="m.C.f1", rel=RelType.CALLS))
    # Group 2: f2 calls f3
    g.add_node(_node("m.C.f2", NodeType.METHOD))
    g.add_node(_node("m.C.f3", NodeType.METHOD))
    g.add_edge(_edge("m.C", "m.C.f2"))
    g.add_edge(_edge("m.C", "m.C.f3"))
    g.add_edge(Edge(source="m.C.f2", target="m.C.f3", rel=RelType.CALLS))
    result = lcom4(g)
    assert result["m.C"] == 2


def test_lcom4_all_isolated():
    """Methods with no intra-class calls -> each is its own component."""
    g = SemGraph()
    g.add_node(_node("m"))
    g.add_node(_node("m.C", NodeType.CLASS))
    g.add_edge(_edge("m", "m.C"))
    for i in range(4):
        g.add_node(_node(f"m.C.f{i}", NodeType.METHOD))
        g.add_edge(_edge("m.C", f"m.C.f{i}"))
    result = lcom4(g)
    assert result["m.C"] == 4


def test_lcom4_from_test_graph():
    g = _build_class_graph()
    result = lcom4(g)
    # ClassB: util->helper, so {util, helper} are connected. LCOM4=1.
    assert result["module_a.ClassB"] == 1
    # ClassA: method1, method2, method3 have no intra-class calls. LCOM4=3.
    assert result["module_a.ClassA"] == 3


def test_lcom4_empty_class():
    g = SemGraph()
    g.add_node(_node("m"))
    g.add_node(_node("m.C", NodeType.CLASS))
    g.add_edge(_edge("m", "m.C"))
    result = lcom4(g)
    assert result["m.C"] == 0


# --- Martin's metrics ---


def _build_module_graph() -> SemGraph:
    """Two modules: core (stable, depended on) and app (unstable, depends on core).

    core: no external deps, depended on by app
    app: depends on core, no one depends on it
    """
    g = SemGraph()
    g.add_node(_node("core"))
    g.add_node(_node("app"))
    g.add_node(_node("core.Engine", NodeType.CLASS))
    g.add_node(_node("core.Config", NodeType.INTERFACE))
    g.add_edge(_edge("core", "core.Engine"))
    g.add_edge(_edge("core", "core.Config"))
    g.add_node(_node("app.Main", NodeType.CLASS))
    g.add_edge(_edge("app", "app.Main"))
    g.add_edge(Edge(source="app", target="core", rel=RelType.IMPORTS))
    return g


def test_martin_ca_ce():
    g = _build_module_graph()
    result = martin_metrics(g)
    # core: Ca=1 (app depends on it), Ce=0 (depends on nothing)
    assert result["core"]["ca"] == 1
    assert result["core"]["ce"] == 0
    # app: Ca=0 (nothing depends on it), Ce=1 (depends on core)
    assert result["app"]["ca"] == 0
    assert result["app"]["ce"] == 1


def test_martin_instability():
    g = _build_module_graph()
    result = martin_metrics(g)
    # core: I = 0 / (1+0) = 0.0 (maximally stable)
    assert result["core"]["instability"] == 0.0
    # app: I = 1 / (0+1) = 1.0 (maximally unstable)
    assert result["app"]["instability"] == 1.0


def test_martin_abstractness():
    g = _build_module_graph()
    result = martin_metrics(g)
    # core: 1 interface + 1 class = abstractness 0.5
    assert result["core"]["abstractness"] == 0.5
    # app: 1 class, 0 interfaces = abstractness 0.0
    assert result["app"]["abstractness"] == 0.0


def test_martin_distance():
    g = _build_module_graph()
    result = martin_metrics(g)
    # core: |0.5 + 0.0 - 1| = 0.5
    assert result["core"]["distance"] == 0.5
    # app: |0.0 + 1.0 - 1| = 0.0 (on the main sequence!)
    assert result["app"]["distance"] == 0.0


# --- SDP violations ---


def test_sdp_violation_detected():
    """Stable module importing unstable module = violation."""
    g = SemGraph()
    g.add_node(_node("stable"))
    g.add_node(_node("unstable"))
    g.add_node(_node("dep1"))
    g.add_node(_node("dep2"))
    g.add_node(_node("dep3"))
    g.add_node(_node("user1"))
    g.add_node(_node("user2"))
    # Make "stable" have high Ca (many depend on it) and low Ce
    g.add_edge(Edge(source="user1", target="stable", rel=RelType.IMPORTS))
    g.add_edge(Edge(source="user2", target="stable", rel=RelType.IMPORTS))
    # stable: Ca=2, Ce=1 -> I = 1/3 = 0.333
    # Make "unstable" have high Ce (depends on many) and low Ca
    g.add_edge(Edge(source="unstable", target="dep1", rel=RelType.IMPORTS))
    g.add_edge(Edge(source="unstable", target="dep2", rel=RelType.IMPORTS))
    g.add_edge(Edge(source="unstable", target="dep3", rel=RelType.IMPORTS))
    # unstable: Ca=1, Ce=3 -> I = 3/4 = 0.75
    # The violation: stable depends on unstable (stable is MORE stable, depending on LESS stable)
    g.add_edge(Edge(source="stable", target="unstable", rel=RelType.IMPORTS))
    violations = sdp_violations(g)
    assert len(violations) >= 1
    v = violations[0]
    assert v["source"] == "stable"
    assert v["target"] == "unstable"
    assert v["source_instability"] < v["target_instability"]


def test_sdp_no_violation():
    """Dependencies flow toward stability -> no violations."""
    g = _build_module_graph()
    violations = sdp_violations(g)
    assert len(violations) == 0  # app (unstable) depends on core (stable) — correct direction


def test_sdp_empty():
    assert sdp_violations(SemGraph()) == []


# --- Integration: analyze on a real-ish graph ---


def test_all_metrics_on_class_graph():
    """Verify all metric functions return without error on the test graph."""
    g = _build_class_graph()
    assert isinstance(wmc(g), dict)
    assert isinstance(dit(g), dict)
    assert isinstance(noc(g), dict)
    assert isinstance(cbo(g), dict)
    assert isinstance(rfc(g), dict)
    assert isinstance(lcom4(g), dict)
    assert isinstance(martin_metrics(g), dict)
    assert isinstance(sdp_violations(g), list)


def test_sdp_detects_member_level_violation():
    """SDP should detect violations when the dependency is at class/method level."""
    g = SemGraph()
    # stable_mod: depended on by two users, low Ce -> low instability
    g.add_node(_node("stable_mod"))
    g.add_node(_node("stable_mod.StableClass", NodeType.CLASS))
    g.add_edge(_edge("stable_mod", "stable_mod.StableClass"))
    g.add_node(_node("stable_mod.StableClass.method", NodeType.METHOD))
    g.add_edge(_edge("stable_mod.StableClass", "stable_mod.StableClass.method"))

    # unstable_mod: depends on three others, no one depends on it -> high instability
    g.add_node(_node("unstable_mod"))
    g.add_node(_node("unstable_mod.UnstableClass", NodeType.CLASS))
    g.add_edge(_edge("unstable_mod", "unstable_mod.UnstableClass"))
    g.add_node(_node("unstable_mod.UnstableClass.do_thing", NodeType.METHOD))
    g.add_edge(_edge("unstable_mod.UnstableClass", "unstable_mod.UnstableClass.do_thing"))

    # Dependencies to establish instability values
    g.add_node(_node("dep1"))
    g.add_node(_node("dep2"))
    g.add_node(_node("dep3"))
    g.add_node(_node("user1"))
    g.add_node(_node("user2"))

    g.add_edge(Edge(source="user1", target="stable_mod", rel=RelType.IMPORTS))
    g.add_edge(Edge(source="user2", target="stable_mod", rel=RelType.IMPORTS))
    g.add_edge(Edge(source="unstable_mod", target="dep1", rel=RelType.IMPORTS))
    g.add_edge(Edge(source="unstable_mod", target="dep2", rel=RelType.IMPORTS))
    g.add_edge(Edge(source="unstable_mod", target="dep3", rel=RelType.IMPORTS))

    # The violation: stable class method calls unstable class method (member-level)
    g.add_edge(
        Edge(
            source="stable_mod.StableClass.method",
            target="unstable_mod.UnstableClass.do_thing",
            rel=RelType.CALLS,
        )
    )

    violations = sdp_violations(g)
    assert len(violations) >= 1
    v = violations[0]
    assert v["source"] == "stable_mod"
    assert v["target"] == "unstable_mod"
