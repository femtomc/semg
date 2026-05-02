from smg import graph_metrics, oo_metrics
from smg.analysis_context import AnalysisContext
from smg.analyze import run_analysis
from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType
from smg.rules import Rule, check_all


def _cycle_graph() -> SemGraph:
    graph = SemGraph()
    graph.add_node(Node(name="a", type=NodeType.FUNCTION))
    graph.add_node(Node(name="b", type=NodeType.FUNCTION))
    graph.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))
    graph.add_edge(Edge(source="b", target="a", rel=RelType.CALLS))
    return graph


def _module_graph() -> SemGraph:
    graph = SemGraph()
    graph.add_node(Node(name="app", type=NodeType.PACKAGE))
    graph.add_node(Node(name="app.core", type=NodeType.MODULE))
    graph.add_node(Node(name="app.ui", type=NodeType.MODULE))
    graph.add_node(Node(name="app.core.Engine", type=NodeType.CLASS))
    graph.add_node(Node(name="app.core.Engine.run", type=NodeType.METHOD))
    graph.add_edge(Edge(source="app", target="app.core", rel=RelType.CONTAINS))
    graph.add_edge(Edge(source="app", target="app.ui", rel=RelType.CONTAINS))
    graph.add_edge(Edge(source="app.core", target="app.core.Engine", rel=RelType.CONTAINS))
    graph.add_edge(Edge(source="app.core.Engine", target="app.core.Engine.run", rel=RelType.CONTAINS))
    graph.add_edge(Edge(source="app.core", target="app.ui", rel=RelType.IMPORTS))
    return graph


def test_analysis_context_reuses_cycles_for_layers_and_layering(monkeypatch):
    graph = _cycle_graph()
    original = graph_metrics.find_cycles
    calls = 0

    def counted(graph_arg):
        nonlocal calls
        calls += 1
        return original(graph_arg)

    monkeypatch.setattr(graph_metrics, "find_cycles", counted)
    ctx = AnalysisContext(graph)

    ctx.layers()
    ctx.layering_violations()

    assert calls == 1


def test_check_all_reuses_cycles_across_invariant_and_quantified_rule(monkeypatch):
    graph = _cycle_graph()
    original = graph_metrics.find_cycles
    calls = 0

    def counted(graph_arg):
        nonlocal calls
        calls += 1
        return original(graph_arg)

    monkeypatch.setattr(graph_metrics, "find_cycles", counted)
    rules = [
        Rule(name="acyclic", type="invariant", invariant="no-cycles"),
        Rule(name="not-cyclic", type="quantified", selector="*", assertion="not in_cycle"),
    ]

    check_all(rules, graph)

    assert calls == 1


def test_run_analysis_reuses_martin_metrics_for_sdp(monkeypatch):
    graph = _module_graph()
    original = oo_metrics.martin_metrics
    calls = 0

    def counted(graph_arg):
        nonlocal calls
        calls += 1
        return original(graph_arg)

    monkeypatch.setattr(oo_metrics, "martin_metrics", counted)

    run_analysis(graph, root=None, full=False)

    assert calls == 1


def test_run_analysis_reuses_class_metrics_for_god_classes(monkeypatch):
    graph = _module_graph()
    counters = {"wmc": 0, "cbo": 0, "lcom4": 0}
    originals = {
        "wmc": oo_metrics.wmc,
        "cbo": oo_metrics.cbo,
        "lcom4": oo_metrics.lcom4,
    }

    def counted(name):
        def wrapper(graph_arg):
            counters[name] += 1
            return originals[name](graph_arg)

        return wrapper

    monkeypatch.setattr(oo_metrics, "wmc", counted("wmc"))
    monkeypatch.setattr(oo_metrics, "cbo", counted("cbo"))
    monkeypatch.setattr(oo_metrics, "lcom4", counted("lcom4"))

    run_analysis(graph, root=None, full=False)

    assert counters == {"wmc": 1, "cbo": 1, "lcom4": 1}
