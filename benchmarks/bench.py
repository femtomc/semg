#!/usr/bin/env python3
"""smg benchmarks — run from project root: .venv/bin/python benchmarks/bench.py

Profiles every major operation on the smg codebase itself, reports median
times, and highlights the top latency offenders.
"""
from __future__ import annotations

import statistics
import time
from pathlib import Path

from smg import graph_metrics, oo_metrics
from smg.blame import blame_entity
from smg.churn import compute_churn
from smg.context import build_context
from smg.diff import diff_graphs
from smg.graph import SemGraph
from smg.langs import load_extractors
from smg.model import Node, NodeType
from smg.scan import scan_paths
from smg.storage import find_root, load_graph, save_graph


def _bench(fn, n: int = 10) -> float:
    """Run fn n times, return median wall time in ms."""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def main() -> None:
    load_extractors()
    root = find_root()
    g = load_graph(root)

    print(f"Graph: {len(g)} nodes, {len(g.all_edges())} edges")
    print(f"Codebase: {root}\n")

    results: list[tuple[float, str, str]] = []

    def record(category: str, name: str, fn, n: int = 10) -> None:
        ms = _bench(fn, n)
        results.append((ms, category, name))

    # --- I/O ---
    record("io", "load_graph", lambda: load_graph(root), n=20)
    record("io", "save_graph", lambda: save_graph(g, root), n=10)

    # --- Scan ---
    def _scan():
        fresh = SemGraph()
        scan_paths(fresh, root, paths=[Path("src")])
    record("scan", "scan src/", _scan, n=3)

    # --- Diff ---
    old = g.clone()
    old.add_node(Node(name="fake.old", type=NodeType.FUNCTION,
                       metadata={"content_hash": "aa", "structure_hash": "bb"}))
    record("diff", "diff_graphs", lambda: diff_graphs(old, g), n=50)

    # --- Churn ---
    record("git", "compute_churn", lambda: compute_churn(g, root, days=90), n=5)

    # --- Blame ---
    node = g.get_node("smg.graph.SemGraph")
    if node:
        record("git", "blame_entity", lambda: blame_entity(node, root), n=5)

    # --- Context ---
    record("context", "build_context (8k)", lambda: build_context(g, root, "smg.graph.SemGraph", budget=8000), n=20)

    # --- Graph metrics ---
    record("analyze", "find_cycles", lambda: graph_metrics.find_cycles(g))
    record("analyze", "topological_layers", lambda: graph_metrics.topological_layers(g))
    record("analyze", "pagerank", lambda: graph_metrics.pagerank(g))
    record("analyze", "betweenness_centrality", lambda: graph_metrics.betweenness_centrality(g))
    record("analyze", "kcore_decomposition", lambda: graph_metrics.kcore_decomposition(g))
    record("analyze", "detect_bridges", lambda: graph_metrics.detect_bridges(g))
    record("analyze", "fan_in_out", lambda: graph_metrics.fan_in_out(g))
    record("analyze", "dead_code", lambda: graph_metrics.dead_code(g))
    record("analyze", "god_files", lambda: graph_metrics.god_files(g))
    record("analyze", "layering_violations", lambda: graph_metrics.layering_violations(g))
    record("analyze", "hits", lambda: graph_metrics.hits(g))

    # --- OO metrics ---
    record("analyze", "wmc", lambda: oo_metrics.wmc(g))
    record("analyze", "dit", lambda: oo_metrics.dit(g))
    record("analyze", "noc", lambda: oo_metrics.noc(g))
    record("analyze", "cbo", lambda: oo_metrics.cbo(g))
    record("analyze", "rfc", lambda: oo_metrics.rfc(g))
    record("analyze", "lcom4", lambda: oo_metrics.lcom4(g))
    record("analyze", "martin_metrics", lambda: oo_metrics.martin_metrics(g))
    record("analyze", "sdp_violations", lambda: oo_metrics.sdp_violations(g))
    record("analyze", "god_classes", lambda: oo_metrics.god_classes(g))
    record("analyze", "feature_envy", lambda: oo_metrics.feature_envy(g))
    record("analyze", "shotgun_surgery", lambda: oo_metrics.shotgun_surgery(g))
    record("analyze", "max_method_cc", lambda: oo_metrics.max_method_cc(g))

    # --- Report ---
    results.sort(key=lambda x: x[0], reverse=True)
    total = sum(ms for ms, _, _ in results)

    print(f"{'Category':<10s} {'Operation':<28s} {'Median ms':>10s} {'%':>6s}")
    print("-" * 56)
    for ms, cat, name in results:
        pct = ms / total * 100
        bar = "*" if pct > 10 else ""
        print(f"{cat:<10s} {name:<28s} {ms:10.1f} {pct:5.1f}% {bar}")
    print("-" * 56)
    print(f"{'':10s} {'TOTAL':<28s} {total:10.1f}")

    # Top offenders
    print("\n--- Top 3 latency offenders ---")
    for ms, cat, name in results[:3]:
        print(f"  {name}: {ms:.1f} ms ({ms/total*100:.0f}%)")


if __name__ == "__main__":
    main()
