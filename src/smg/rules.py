"""Declarative architectural constraint system.

Users declare rules (path denials, structural invariants) and check
them against the graph. Violations report the specific edges/nodes
that offend, inspired by Alloy's counterexample-driven feedback.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from typing import Any

from smg.graph import SemGraph
from smg.model import RelType

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
        "no-cycles",
        "no-dead-code",
        "no-layering-violations",
    }
)


@dataclass
class Rule:
    name: str
    type: str  # "deny" or "invariant"
    pattern: str | None = None
    invariant: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    scope: str | None = None  # optional module prefix to scope the rule

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": "rule", "name": self.name, "type": self.type}
        if self.pattern is not None:
            d["pattern"] = self.pattern
        if self.invariant is not None:
            d["invariant"] = self.invariant
        if self.params:
            d["params"] = self.params
        if self.scope is not None:
            d["scope"] = self.scope
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Rule:
        return cls(
            name=d["name"],
            type=d["type"],
            pattern=d.get("pattern"),
            invariant=d.get("invariant"),
            params=d.get("params", {}),
            scope=d.get("scope"),
        )


@dataclass
class Violation:
    rule_name: str
    rule_type: str
    message: str
    edges: list[dict] | None = None
    nodes: list[str] | None = None
    cycles: list[list[str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "rule": self.rule_name,
            "type": self.rule_type,
            "message": self.message,
        }
        if self.edges:
            d["edges"] = self.edges
        if self.nodes:
            d["nodes"] = self.nodes
        if self.cycles:
            d["cycles"] = self.cycles
        return d


def parse_deny_pattern(pattern: str) -> tuple[str, str | None, str]:
    """Parse a deny pattern into (source_glob, rel_or_none, target_glob).

    Accepted forms:
        "ui.* -[calls]-> db.*"    -> ("ui.*", "calls", "db.*")
        "ui.* -> db.*"            -> ("ui.*", None, "db.*")
    """
    m = _DENY_WITH_REL.match(pattern)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    m = _DENY_ANY_REL.match(pattern)
    if m:
        return m.group(1).strip(), None, m.group(2).strip()
    raise ValueError(f"invalid deny pattern: {pattern!r}")


def check_deny(
    rule: Rule,
    graph: SemGraph,
    scope: str | None = None,
) -> Violation | None:
    """Check a path denial rule against the graph."""
    assert rule.pattern is not None, f"deny rule {rule.name!r} has no pattern"
    source_glob, rel, target_glob = parse_deny_pattern(rule.pattern)

    # When scoped, only consider edges whose source is within the scope.
    scope_prefix: str | None = None
    if scope:
        scope_prefix = scope if scope.endswith(".") else scope + "."

    offending: list[dict] = []
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
    if offending:
        return Violation(
            rule_name=rule.name,
            rule_type="deny",
            message=f"{len(offending)} forbidden edge(s)",
            edges=offending,
        )
    return None


def check_invariant(rule: Rule, graph: SemGraph) -> Violation | None:
    """Check a structural invariant rule against the graph."""
    from smg import graph_metrics

    inv = rule.invariant
    if inv == "no-cycles":
        cycles = graph_metrics.find_cycles(graph)
        if cycles:
            # Extract minimal cycle paths for actionable output
            minimal_cycles = [graph_metrics.minimal_cycle(graph, scc) for scc in cycles]
            return Violation(
                rule_name=rule.name,
                rule_type="invariant",
                message=f"{len(cycles)} cycle(s)",
                cycles=minimal_cycles,
            )
    elif inv == "no-dead-code":
        entry_points: set[str] = set()
        ep_raw = rule.params.get("entry_points", "")
        if ep_raw:
            patterns = [p.strip() for p in ep_raw.split(",") if p.strip()]
            all_names = list(graph.nodes.keys())
            for pat in patterns:
                entry_points.update(fnmatch.filter(all_names, pat))
        dead = graph_metrics.dead_code(graph, entry_points=entry_points)
        if dead:
            return Violation(
                rule_name=rule.name,
                rule_type="invariant",
                message=f"{len(dead)} unreferenced node(s)",
                nodes=dead,
            )
    elif inv == "no-layering-violations":
        violations = graph_metrics.layering_violations(graph)
        if violations:
            return Violation(
                rule_name=rule.name,
                rule_type="invariant",
                message=f"{len(violations)} back-dependency edge(s)",
                edges=violations,
            )
    else:
        raise ValueError(f"unknown invariant: {inv!r}")
    return None


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


def check_rule(rule: Rule, graph: SemGraph) -> Violation | None:
    """Check a single rule against the graph."""
    if rule.type == "deny":
        # Deny rules handle scope internally so cross-boundary targets
        # are visible for pattern matching.
        return check_deny(rule, graph, scope=rule.scope)
    if rule.scope:
        graph = _scope_graph_for_rule(graph, rule.scope)
    if rule.type == "invariant":
        return check_invariant(rule, graph)
    else:
        raise ValueError(f"unknown rule type: {rule.type!r}")


def check_all(rules: list[Rule], graph: SemGraph) -> list[Violation]:
    """Check all rules, return list of violations (empty = all pass)."""
    violations: list[Violation] = []
    for rule in rules:
        v = check_rule(rule, graph)
        if v is not None:
            violations.append(v)
    return violations
