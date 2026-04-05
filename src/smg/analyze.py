"""Deep architectural analysis: compute all metrics and synthesize hotspots.

Extracted from cli/explore.py to separate computation from presentation.
This module is purely functional — no CLI, no rendering, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from smg import graph_metrics, oo_metrics
from smg.churn import ChurnResult, compute_churn
from smg.graph import SemGraph


@dataclass
class AnalysisResult:
    """Complete analysis output — all metrics, smells, and hotspots."""

    # Graph structure
    node_count: int = 0
    edge_count: int = 0
    cycles: list[list[str]] = field(default_factory=list)
    layers: dict[str, int] = field(default_factory=dict)
    bridges: list[tuple[str, str]] = field(default_factory=list)

    # Centrality
    pagerank: dict[str, float] = field(default_factory=dict)
    betweenness: dict[str, float] = field(default_factory=dict)
    kcore: dict[str, int] = field(default_factory=dict)
    hits: dict[str, dict[str, float]] = field(default_factory=dict)

    # Class metrics (CK suite)
    wmc: dict[str, int] = field(default_factory=dict)
    dit: dict[str, int] = field(default_factory=dict)
    noc: dict[str, int] = field(default_factory=dict)
    cbo: dict[str, int] = field(default_factory=dict)
    rfc: dict[str, int] = field(default_factory=dict)
    lcom4: dict[str, int] = field(default_factory=dict)
    max_method_cc: dict[str, int] = field(default_factory=dict)

    # Module metrics (Martin)
    martin: dict[str, dict] = field(default_factory=dict)
    sdp_violations: list[dict] = field(default_factory=list)

    # Fan-in/fan-out
    fan_in_out: dict[str, dict[str, int]] = field(default_factory=dict)

    # Issues
    dead_code: list[str] = field(default_factory=list)
    layering_violations: list[dict] = field(default_factory=list)
    god_classes: list[dict] = field(default_factory=list)
    feature_envy: list[dict] = field(default_factory=list)
    shotgun_surgery: list[dict] = field(default_factory=list)
    god_files: list[dict] = field(default_factory=list)

    # Churn
    churn: ChurnResult | None = None

    # Synthesized
    hotspots: list[dict] = field(default_factory=list)


def run_analysis(
    graph: SemGraph,
    root: Path | None = None,
    churn_days: int = 90,
    full: bool = True,
    on_step: Callable[[str], None] | None = None,
) -> AnalysisResult:
    """Run all analyses on a graph and return structured results.

    Args:
        graph: The graph to analyze.
        root: Project root (needed for churn). None to skip churn.
        churn_days: Time window for git churn analysis.
        full: If False, skip expensive non-summary metrics (fan-in/out, HITS, max_method_cc).
        on_step: Optional callback for progress reporting.
    """
    r = AnalysisResult()
    step = on_step or (lambda _: None)

    r.node_count = len(graph)
    r.edge_count = len(graph.all_edges())

    # Graph-theoretic
    step("Finding cycles...")
    r.cycles = graph_metrics.find_cycles(graph)
    step("Computing layers...")
    r.layers = graph_metrics.topological_layers(graph)
    step("Computing PageRank...")
    r.pagerank = graph_metrics.pagerank(graph)
    step("Computing betweenness centrality...")
    r.betweenness = graph_metrics.betweenness_centrality(graph)
    step("Computing k-core decomposition...")
    r.kcore = graph_metrics.kcore_decomposition(graph)
    step("Detecting bridges...")
    r.bridges = graph_metrics.detect_bridges(graph)

    # OO metrics
    step("Computing class metrics (CK suite)...")
    r.wmc = oo_metrics.wmc(graph)
    r.dit = oo_metrics.dit(graph)
    r.noc = oo_metrics.noc(graph)
    r.cbo = oo_metrics.cbo(graph)
    r.rfc = oo_metrics.rfc(graph)
    r.lcom4 = oo_metrics.lcom4(graph)
    step("Computing module metrics (Martin)...")
    r.martin = oo_metrics.martin_metrics(graph)
    step("Checking SDP violations...")
    r.sdp_violations = oo_metrics.sdp_violations(graph)
    step("Detecting dead code...")
    r.dead_code = graph_metrics.dead_code(graph)
    step("Checking layering violations...")
    r.layering_violations = graph_metrics.layering_violations(graph)
    step("Detecting code smells...")
    r.god_classes = oo_metrics.god_classes(graph)
    r.feature_envy = oo_metrics.feature_envy(graph)
    r.shotgun_surgery = oo_metrics.shotgun_surgery(graph)
    step("Detecting god files...")
    r.god_files = graph_metrics.god_files(graph)

    # Git churn
    if root is not None:
        step("Computing git churn...")
        try:
            r.churn = compute_churn(graph, root, days=churn_days)
        except Exception:
            pass

    # Full-only metrics
    if full:
        step("Computing max method complexity...")
        r.max_method_cc = oo_metrics.max_method_cc(graph)
        step("Computing fan-in/fan-out...")
        r.fan_in_out = graph_metrics.fan_in_out(graph)
        step("Computing HITS (hub/authority)...")
        r.hits = graph_metrics.hits(graph)

    # Hotspot synthesis
    step("Computing hotspots...")
    r.hotspots = _synthesize_hotspots(graph, r)

    return r


def filter_to_delta(result: AnalysisResult, delta_names: set[str], graph: SemGraph) -> None:
    """Filter analysis results to only include nodes in the delta set (mutates result)."""
    result.hotspots = [h for h in result.hotspots if h["name"] in delta_names]
    result.dead_code = [n for n in result.dead_code if n in delta_names]
    result.layering_violations = [
        v for v in result.layering_violations if v["source"] in delta_names or v["target"] in delta_names
    ]
    result.god_classes = [g for g in result.god_classes if g["name"] in delta_names]
    result.feature_envy = [e for e in result.feature_envy if e["method"] in delta_names]
    result.shotgun_surgery = [s for s in result.shotgun_surgery if s["name"] in delta_names]
    node_files = {node.name: node.file for node in graph.iter_nodes() if node.file}
    delta_files = {node_files[n] for n in delta_names if n in node_files}
    result.god_files = [gf for gf in result.god_files if gf["file"] in delta_files]


def _synthesize_hotspots(graph: SemGraph, r: AnalysisResult) -> list[dict]:
    """Composite hotspot scoring from multiple signals."""
    hotspots: list[dict] = []

    # Class-level hotspots
    for name in r.wmc:
        score = 0.0
        reasons: list[str] = []
        w = r.wmc.get(name, 0)
        c = r.cbo.get(name, 0)
        lcom = r.lcom4.get(name, 0)
        rf = r.rfc.get(name, 0)
        b = r.betweenness.get(name, 0.0)
        p = r.pagerank.get(name, 0.0)
        if w > 20:
            score += w / 10
            reasons.append(f"high complexity (WMC={w})")
        if c > 5:
            score += c
            reasons.append(f"high coupling (CBO={c})")
        if lcom > 1:
            score += lcom * 3
            reasons.append(f"low cohesion (LCOM4={lcom})")
        if rf > 20:
            score += rf / 5
            reasons.append(f"large response set (RFC={rf})")
        if b > 0.05:
            score += b * 20
            reasons.append(f"structural bottleneck (BC={b:.3f})")
        if p > 0.02:
            score += p * 50
            reasons.append(f"high importance (PR={p:.4f})")
        if r.churn:
            churn_count = r.churn.entity_churn.get(name, 0)
            if churn_count > 10:
                score += churn_count / 5
                reasons.append(f"high churn ({churn_count} touches)")
        if reasons:
            hotspots.append(
                {
                    "name": name,
                    "type": "class",
                    "score": round(score, 2),
                    "reasons": reasons,
                }
            )

    # Module-level hotspots (high distance from main sequence)
    for name, m in r.martin.items():
        if m["distance"] > 0.7:
            hotspots.append(
                {
                    "name": name,
                    "type": "module",
                    "score": round(m["distance"] * 5, 2),
                    "reasons": [
                        f"far from main sequence (D={m['distance']}, I={m['instability']}, A={m['abstractness']})"
                    ],
                }
            )

    # Function-level churn hotspots (high churn + high complexity)
    if r.churn:
        hotspot_names = {h["name"] for h in hotspots}
        for name, touches in r.churn.entity_churn.items():
            if name in hotspot_names:
                continue
            node = graph.get_node(name)
            if node is None or node.type.value not in ("function", "method"):
                continue
            cc = node.metadata.get("metrics", {}).get("cyclomatic_complexity", 1)
            score = 0.0
            reasons: list[str] = []
            if touches > 5:
                score += touches / 5
                reasons.append(f"high churn ({touches} touches)")
            if cc > 10:
                score += cc / 5
                reasons.append(f"high complexity (CC={cc})")
            if score > 2.0:
                hotspots.append(
                    {
                        "name": name,
                        "type": node.type.value,
                        "score": round(score, 2),
                        "reasons": reasons,
                    }
                )

    hotspots.sort(key=lambda h: h["score"], reverse=True)
    return hotspots
