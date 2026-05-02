"""Declarative architectural constraint system."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from typing import Any, cast

from smg import oo_metrics
from smg.analysis_context import AnalysisContext
from smg.concepts import Concept, ConceptViolation, analyze_concepts
from smg.graph import SemGraph
from smg.model import Node, NodeType, RelType
from smg.rule_expr import ParsedAssertion, evaluate_assertion, parse_assertion
from smg.witness import Witness, cycle_witness, edge_witness, node_witness, predicate_witness

_COUPLING_RELS = frozenset(
    {
        RelType.CALLS.value,
        RelType.IMPORTS.value,
        RelType.INHERITS.value,
        RelType.IMPLEMENTS.value,
        RelType.DEPENDS_ON.value,
    }
)

_DENY_WITH_REL = re.compile(r"^(.+?)\s+-\[(\w+)\]->\s+(.+)$")
_DENY_ANY_REL = re.compile(r"^(.+?)\s+->\s+(.+)$")

KNOWN_INVARIANTS = frozenset(
    {
        "concept-boundaries",
        "no-cycles",
        "no-dead-code",
        "no-layering-violations",
    }
)

_TOTAL_METRIC_IDENTIFIERS = frozenset(
    {
        "fan_in",
        "fan_out",
        "layer",
        "pagerank",
        "betweenness",
        "kcore",
        "dead",
        "in_cycle",
    }
)
_NODE_METRIC_IDENTIFIERS = frozenset(
    {
        "cyclomatic_complexity",
        "cognitive_complexity",
        "nesting",
    }
)
_CLASS_METRIC_IDENTIFIERS = frozenset(
    {
        "wmc",
        "cbo",
        "rfc",
        "lcom4",
        "dit",
        "noc",
        "max_method_cc",
    }
)
_MODULE_METRIC_IDENTIFIERS = frozenset(
    {
        "instability",
        "abstractness",
        "distance",
    }
)
KNOWN_METRIC_IDENTIFIERS = (
    _TOTAL_METRIC_IDENTIFIERS | _NODE_METRIC_IDENTIFIERS | _CLASS_METRIC_IDENTIFIERS | _MODULE_METRIC_IDENTIFIERS
)


@dataclass
class Rule:
    name: str
    type: str  # "deny", "invariant", or "quantified"
    pattern: str | None = None
    invariant: str | None = None
    selector: str | None = None
    assertion: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": "rule", "name": self.name, "type": self.type}
        if self.pattern is not None:
            data["pattern"] = self.pattern
        if self.invariant is not None:
            data["invariant"] = self.invariant
        if self.selector is not None:
            data["selector"] = self.selector
        if self.assertion is not None:
            data["assertion"] = self.assertion
        if self.params:
            data["params"] = self.params
        if self.scope is not None:
            data["scope"] = self.scope
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        return cls(
            name=data["name"],
            type=data["type"],
            pattern=data.get("pattern"),
            invariant=data.get("invariant"),
            selector=data.get("selector"),
            assertion=data.get("assertion"),
            params=data.get("params", {}),
            scope=data.get("scope"),
        )


@dataclass
class Violation:
    rule_name: str
    rule_type: str
    message: str
    edges: list[dict[str, Any]] | None = None
    nodes: list[str] | None = None
    cycles: list[list[str]] | None = None
    witnesses: list[Witness] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "rule": self.rule_name,
            "type": self.rule_type,
            "message": self.message,
        }
        witnesses = self.witnesses or _legacy_witnesses(self)
        if witnesses:
            data["witnesses"] = [witness.to_dict() for witness in witnesses]
        if self.edges:
            data["edges"] = self.edges
        if self.nodes:
            data["nodes"] = self.nodes
        if self.cycles:
            data["cycles"] = self.cycles
        return data


def parse_deny_pattern(pattern: str) -> tuple[str, str | None, str]:
    """Parse a deny pattern into (source_glob, rel_or_none, target_glob)."""
    match = _DENY_WITH_REL.match(pattern)
    if match:
        return match.group(1).strip(), match.group(2).strip(), match.group(3).strip()
    match = _DENY_ANY_REL.match(pattern)
    if match:
        return match.group(1).strip(), None, match.group(2).strip()
    raise ValueError(f"invalid deny pattern: {pattern!r}")


def parse_quantified_assertion(rule: Rule) -> ParsedAssertion:
    """Parse a quantified rule assertion and reject unknown identifiers."""
    if rule.assertion is None:
        raise ValueError(f"quantified rule {rule.name!r} has no assertion")
    parsed = parse_assertion(rule.assertion)
    unknown = sorted(parsed.identifiers - KNOWN_METRIC_IDENTIFIERS)
    if unknown:
        raise ValueError(f"unknown metric identifier(s) in rule {rule.name!r}: {', '.join(unknown)}")
    return parsed


def check_deny(rule: Rule, graph: SemGraph, scope: str | None = None) -> Violation | None:
    """Check a path denial rule against the graph."""
    assert rule.pattern is not None, f"deny rule {rule.name!r} has no pattern"
    source_glob, rel, target_glob = parse_deny_pattern(rule.pattern)

    scope_prefix: str | None = None
    if scope:
        scope_prefix = scope if scope.endswith(".") else scope + "."

    offending: list[dict[str, Any]] = []
    for edge in graph.all_edges():
        if rel is not None:
            if edge.rel.value != rel:
                continue
        elif edge.rel.value not in _COUPLING_RELS:
            continue
        if scope_prefix is not None:
            if edge.source != scope and not edge.source.startswith(scope_prefix):
                continue
        if fnmatch.fnmatch(edge.source, source_glob) and fnmatch.fnmatch(edge.target, target_glob):
            offending.append(
                {
                    "source": edge.source,
                    "rel": edge.rel.value,
                    "target": edge.target,
                }
            )
    if not offending:
        return None
    return Violation(
        rule_name=rule.name,
        rule_type="deny",
        message=f"{len(offending)} forbidden edge(s)",
        edges=offending,
        witnesses=[edge_witness(edge) for edge in offending],
    )


def check_invariant(
    rule: Rule,
    graph: SemGraph,
    concepts: list[Concept] | None = None,
    analysis_context: AnalysisContext | None = None,
) -> Violation | None:
    """Check a structural invariant rule against the graph."""
    from smg import graph_metrics

    inv = rule.invariant
    ctx = analysis_context or AnalysisContext(graph)
    if inv == "concept-boundaries":
        if not concepts:
            raise ValueError("concept-boundaries requires at least one concept declaration")
        analysis = analyze_concepts(graph, concepts)
        if not analysis.violations:
            return None
        edges, witnesses = _concept_violation_witnesses(analysis.violations)
        messages = "; ".join(
            f"{violation.source}->{violation.target}: {violation.message}" for violation in analysis.violations
        )
        return Violation(
            rule_name=rule.name,
            rule_type="invariant",
            message=messages,
            edges=edges,
            witnesses=witnesses,
        )
    if inv == "no-cycles":
        cycles = ctx.cycles()
        if not cycles:
            return None
        minimal_cycles = [graph_metrics.minimal_cycle(graph, scc) for scc in cycles]
        return Violation(
            rule_name=rule.name,
            rule_type="invariant",
            message=f"{len(cycles)} cycle(s)",
            cycles=minimal_cycles,
            witnesses=[cycle_witness(cycle) for cycle in minimal_cycles],
        )
    if inv == "no-dead-code":
        entry_points: set[str] = set()
        raw = rule.params.get("entry_points", "")
        if raw:
            patterns = [pattern.strip() for pattern in raw.split(",") if pattern.strip()]
            all_names = list(graph.nodes.keys())
            for pattern in patterns:
                entry_points.update(fnmatch.filter(all_names, pattern))
        dead = ctx.dead_code(entry_points)
        if not dead:
            return None
        return Violation(
            rule_name=rule.name,
            rule_type="invariant",
            message=f"{len(dead)} unreferenced node(s)",
            nodes=dead,
            witnesses=[node_witness(node) for node in dead],
        )
    if inv == "no-layering-violations":
        violations = ctx.layering_violations()
        if not violations:
            return None
        return Violation(
            rule_name=rule.name,
            rule_type="invariant",
            message=f"{len(violations)} back-dependency edge(s)",
            edges=violations,
            witnesses=[edge_witness(edge) for edge in violations],
        )
    raise ValueError(f"unknown invariant: {inv!r}")


def _scope_graph_for_rule(graph: SemGraph, scope: str) -> SemGraph:
    """Restrict graph to nodes matching a module prefix."""
    scoped = SemGraph()
    prefix = scope if scope.endswith(".") else scope + "."
    for node in graph.all_nodes():
        if node.name == scope or node.name.startswith(prefix):
            scoped.add_node(node)
    for edge in graph.all_edges():
        if edge.source in scoped.nodes and edge.target in scoped.nodes:
            scoped.add_edge(edge)
    return scoped


class QuantifiedMetricCatalog:
    """Lazy metric bag for quantified rules."""

    def __init__(self, graph: SemGraph, analysis_context: AnalysisContext | None = None) -> None:
        self.graph = graph
        self.analysis_context = analysis_context or AnalysisContext(graph)
        self._cache: dict[str, Any] = {}

    def facts_for(self, subject: str, identifiers: frozenset[str]) -> dict[str, Any]:
        node = self.graph.get_node(subject)
        if node is None:
            raise ValueError(f"unknown subject {subject!r}")
        facts: dict[str, Any] = {}
        for identifier in sorted(identifiers):
            facts[identifier] = self.value_for(subject, node, identifier)
        return facts

    def value_for(self, subject: str, node: Node, identifier: str) -> Any:
        if identifier in _TOTAL_METRIC_IDENTIFIERS:
            return self._total_metric_value(subject, identifier)
        if identifier in _NODE_METRIC_IDENTIFIERS:
            return self._node_metric_value(subject, node, identifier)
        if identifier in _CLASS_METRIC_IDENTIFIERS:
            if node.type != NodeType.CLASS:
                raise ValueError(
                    f"quantified rule metric {identifier!r} is not defined for non-class subject {subject!r}"
                )
            values = self._cache_for(identifier)
            return values[subject]
        if identifier in _MODULE_METRIC_IDENTIFIERS:
            if node.type not in {NodeType.MODULE, NodeType.PACKAGE}:
                raise ValueError(
                    f"quantified rule metric {identifier!r} is not defined for non-module subject {subject!r}"
                )
            values = self._cache_for("martin")
            return values[subject][identifier]
        raise ValueError(f"unknown metric identifier: {identifier!r}")

    def _total_metric_value(self, subject: str, identifier: str) -> Any:
        if identifier in {"fan_in", "fan_out"}:
            values = self._cache_for("fan_in_out")
            return values.get(subject, {}).get(identifier, 0)
        if identifier == "layer":
            return self._cache_for("layers").get(subject, 0)
        if identifier == "pagerank":
            return self._cache_for("pagerank").get(subject, 0.0)
        if identifier == "betweenness":
            return self._cache_for("betweenness").get(subject, 0.0)
        if identifier == "kcore":
            return self._cache_for("kcore").get(subject, 0)
        if identifier == "dead":
            return subject in self._cache_for("dead")
        if identifier == "in_cycle":
            return subject in self._cache_for("in_cycle")
        raise ValueError(f"unknown metric identifier: {identifier!r}")

    def _node_metric_value(self, subject: str, node: Node, identifier: str) -> Any:
        metrics = node.metadata.get("metrics", {})
        key = "max_nesting_depth" if identifier == "nesting" else identifier
        if key not in metrics:
            raise ValueError(f"quantified rule metric {identifier!r} is not defined for subject {subject!r}")
        return metrics[key]

    def _cache_for(self, key: str) -> Any:
        if key in self._cache:
            return self._cache[key]

        if key == "fan_in_out":
            value = self.analysis_context.fan_in_out()
        elif key == "layers":
            value = self.analysis_context.layers()
        elif key == "pagerank":
            value = self.analysis_context.pagerank()
        elif key == "betweenness":
            value = self.analysis_context.betweenness()
        elif key == "kcore":
            value = self.analysis_context.kcore()
        elif key == "dead":
            value = set(self.analysis_context.dead_code())
        elif key == "in_cycle":
            value = {node for cycle in self.analysis_context.cycles() for node in cycle}
        elif key == "martin":
            value = self.analysis_context.martin()
        elif key == "wmc":
            value = self.analysis_context.wmc()
        elif key == "dit":
            value = self.analysis_context.dit()
        elif key == "noc":
            value = self.analysis_context.noc()
        elif key == "cbo":
            value = self.analysis_context.cbo()
        elif key == "rfc":
            value = self.analysis_context.rfc()
        elif key == "lcom4":
            value = self.analysis_context.lcom4()
        elif key == "max_method_cc":
            value = self.analysis_context.max_method_cc()
        else:
            value = getattr(oo_metrics, key)(self.graph)
        self._cache[key] = value
        return value


def check_quantified(
    rule: Rule,
    graph: SemGraph,
    *,
    parsed_assertion: ParsedAssertion | None = None,
    metric_catalog: QuantifiedMetricCatalog | None = None,
) -> Violation | None:
    """Check a quantified rule against the current graph."""
    if rule.selector is None:
        raise ValueError(f"quantified rule {rule.name!r} has no selector")
    parsed = parsed_assertion or parse_quantified_assertion(rule)
    catalog = metric_catalog or QuantifiedMetricCatalog(graph)

    subjects = sorted(node.name for node in graph.all_nodes() if fnmatch.fnmatch(node.name, rule.selector))
    if rule.scope:
        prefix = rule.scope if rule.scope.endswith(".") else rule.scope + "."
        subjects = [subject for subject in subjects if subject == rule.scope or subject.startswith(prefix)]
    if not subjects:
        return None

    failing_subjects: list[str] = []
    witnesses: list[Witness] = []
    for subject in subjects:
        facts = catalog.facts_for(subject, parsed.identifiers)
        try:
            matched = evaluate_assertion(parsed, facts)
        except KeyError as exc:
            raise ValueError(
                f"quantified rule {rule.name!r} needs metric {exc.args[0]!r} for subject {subject!r}"
            ) from exc
        if not isinstance(matched, bool):
            raise ValueError(f"quantified rule {rule.name!r} did not evaluate to a boolean")
        if not matched:
            failing_subjects.append(subject)
            witnesses.append(predicate_witness(subject, parsed.source, facts))

    if not failing_subjects:
        return None
    return Violation(
        rule_name=rule.name,
        rule_type="quantified",
        message=f"{len(failing_subjects)} subject(s) failed {parsed.source}",
        nodes=failing_subjects,
        witnesses=witnesses,
    )


def check_rule(
    rule: Rule,
    graph: SemGraph,
    *,
    concepts: list[Concept] | None = None,
    analysis_context: AnalysisContext | None = None,
    parsed_assertion: ParsedAssertion | None = None,
    metric_catalog: QuantifiedMetricCatalog | None = None,
) -> Violation | None:
    """Check a single rule against the graph."""
    if rule.type == "deny":
        return check_deny(rule, graph, scope=rule.scope)
    if rule.type == "quantified":
        return check_quantified(
            rule,
            graph,
            parsed_assertion=parsed_assertion,
            metric_catalog=metric_catalog or QuantifiedMetricCatalog(graph, analysis_context=analysis_context),
        )
    if rule.scope:
        graph = _scope_graph_for_rule(graph, rule.scope)
        analysis_context = None
    if rule.type == "invariant":
        return check_invariant(rule, graph, concepts=concepts, analysis_context=analysis_context)
    raise ValueError(f"unknown rule type: {rule.type!r}")


def check_all(rules: list[Rule], graph: SemGraph, concepts: list[Concept] | None = None) -> list[Violation]:
    """Check all rules, return list of violations."""
    parsed_assertions = {rule.name: parse_quantified_assertion(rule) for rule in rules if rule.type == "quantified"}
    analysis_context = AnalysisContext(graph)
    metric_catalog = QuantifiedMetricCatalog(graph, analysis_context=analysis_context) if parsed_assertions else None

    violations: list[Violation] = []
    for rule in rules:
        violation = check_rule(
            rule,
            graph,
            concepts=concepts,
            analysis_context=analysis_context,
            parsed_assertion=parsed_assertions.get(rule.name),
            metric_catalog=metric_catalog,
        )
        if violation is not None:
            violations.append(violation)
    return violations


def _concept_violation_witnesses(violations: list[ConceptViolation]) -> tuple[list[dict[str, Any]], list[Witness]]:
    edges: list[dict[str, Any]] = []
    witnesses: list[Witness] = []
    for violation in violations:
        for witness in violation.witnesses:
            raw_edges = witness.get("edges")
            if not isinstance(raw_edges, list):
                continue
            for edge in raw_edges:
                if not isinstance(edge, dict):
                    continue
                edge_dict = dict(cast(dict[str, Any], edge))
                edges.append(edge_dict)
                witnesses.append(edge_witness(edge_dict))
    return edges, witnesses


def _legacy_witnesses(violation: Violation) -> list[Witness]:
    witnesses: list[Witness] = []
    if violation.edges:
        witnesses.extend(edge_witness(edge) for edge in violation.edges)
    if violation.nodes:
        witnesses.extend(node_witness(node) for node in violation.nodes)
    if violation.cycles:
        witnesses.extend(cycle_witness(cycle) for cycle in violation.cycles)
    return witnesses
