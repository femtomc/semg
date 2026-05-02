"""Shared lazy metric cache for analysis and rule checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar, cast

from smg import graph_metrics, oo_metrics
from smg.graph import SemGraph

T = TypeVar("T")


@dataclass
class AnalysisContext:
    graph: SemGraph
    include_betweenness: bool | None = None
    _cache: dict[object, Any] = field(default_factory=dict)

    def _get(self, key: object, compute: Callable[[], T]) -> T:
        if key not in self._cache:
            self._cache[key] = compute()
        return cast(T, self._cache[key])

    def cycles(self) -> list[list[str]]:
        return self._get("cycles", lambda: graph_metrics.find_cycles(self.graph))

    def layers(self) -> dict[str, int]:
        return self._get("layers", lambda: graph_metrics.topological_layers(self.graph, cycles=self.cycles()))

    def pagerank(self) -> dict[str, float]:
        return self._get("pagerank", lambda: graph_metrics.pagerank(self.graph))

    def betweenness(self) -> dict[str, float]:
        return self._get(
            ("betweenness", self.include_betweenness),
            lambda: graph_metrics.betweenness_centrality(self.graph, include=self.include_betweenness),
        )

    def kcore(self) -> dict[str, int]:
        return self._get("kcore", lambda: graph_metrics.kcore_decomposition(self.graph))

    def bridges(self) -> list[tuple[str, str]]:
        return self._get("bridges", lambda: graph_metrics.detect_bridges(self.graph))

    def wmc(self) -> dict[str, int]:
        return self._get("wmc", lambda: oo_metrics.wmc(self.graph))

    def dit(self) -> dict[str, int]:
        return self._get("dit", lambda: oo_metrics.dit(self.graph))

    def noc(self) -> dict[str, int]:
        return self._get("noc", lambda: oo_metrics.noc(self.graph))

    def cbo(self) -> dict[str, int]:
        return self._get("cbo", lambda: oo_metrics.cbo(self.graph))

    def rfc(self) -> dict[str, int]:
        return self._get("rfc", lambda: oo_metrics.rfc(self.graph))

    def lcom4(self) -> dict[str, int]:
        return self._get("lcom4", lambda: oo_metrics.lcom4(self.graph))

    def max_method_cc(self) -> dict[str, int]:
        return self._get("max_method_cc", lambda: oo_metrics.max_method_cc(self.graph))

    def martin(self) -> dict[str, dict]:
        return self._get("martin", lambda: oo_metrics.martin_metrics(self.graph))

    def sdp_violations(self) -> list[dict]:
        return self._get("sdp_violations", lambda: oo_metrics.sdp_violations(self.graph, martin=self.martin()))

    def fan_in_out(self) -> dict[str, dict[str, int]]:
        return self._get("fan_in_out", lambda: graph_metrics.fan_in_out(self.graph))

    def dead_code(self, entry_points: set[str] | frozenset[str] | None = None) -> list[str]:
        frozen_entry_points = frozenset(entry_points or ())
        return self._get(
            ("dead_code", tuple(sorted(frozen_entry_points))),
            lambda: graph_metrics.dead_code(self.graph, entry_points=set(frozen_entry_points)),
        )

    def layering_violations(self) -> list[dict]:
        return self._get(
            "layering_violations",
            lambda: graph_metrics.layering_violations(self.graph, layers=self.layers()),
        )

    def god_classes(self) -> list[dict]:
        return self._get(
            "god_classes",
            lambda: oo_metrics.god_classes(
                self.graph,
                wmc_data=self.wmc(),
                cbo_data=self.cbo(),
                lcom_data=self.lcom4(),
            ),
        )

    def feature_envy(self) -> list[dict]:
        return self._get("feature_envy", lambda: oo_metrics.feature_envy(self.graph))

    def shotgun_surgery(self) -> list[dict]:
        return self._get("shotgun_surgery", lambda: oo_metrics.shotgun_surgery(self.graph))

    def god_files(self) -> list[dict]:
        return self._get("god_files", lambda: graph_metrics.god_files(self.graph))

    def hits(self) -> dict[str, dict[str, float]]:
        return self._get("hits", lambda: graph_metrics.hits(self.graph))
