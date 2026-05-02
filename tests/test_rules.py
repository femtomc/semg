"""Tests for the architectural constraint system (rules + check)."""

import json
import os

from click.testing import CliRunner

from smg.cli import main
from smg.concepts import Concept
from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType
from smg.rule_expr import evaluate_assertion, parse_assertion
from smg.rules import (
    Rule,
    check_all,
    check_deny,
    check_invariant,
    check_rule,
    parse_deny_pattern,
)

# --- Pattern parsing ---


def test_parse_deny_pattern_with_rel():
    src, rel, tgt = parse_deny_pattern("ui.* -[calls]-> db.*")
    assert src == "ui.*"
    assert rel == "calls"
    assert tgt == "db.*"


def test_parse_deny_pattern_any_rel():
    src, rel, tgt = parse_deny_pattern("ui.* -> db.*")
    assert src == "ui.*"
    assert rel is None
    assert tgt == "db.*"


def test_parse_deny_pattern_with_spaces():
    src, rel, tgt = parse_deny_pattern("  core.*   -[imports]->   ui.*  ")
    assert src == "core.*"
    assert rel == "imports"
    assert tgt == "ui.*"


def test_parse_deny_pattern_invalid():
    import pytest

    with pytest.raises(ValueError, match="invalid deny pattern"):
        parse_deny_pattern("not a pattern")


# --- Rule serialization ---


def test_rule_round_trip():
    r = Rule(name="test", type="deny", pattern="a.* -> b.*")
    d = r.to_dict()
    assert d["kind"] == "rule"
    r2 = Rule.from_dict(d)
    assert r2.name == r.name
    assert r2.type == r.type
    assert r2.pattern == r.pattern


def test_rule_invariant_round_trip():
    r = Rule(name="acyclic", type="invariant", invariant="no-cycles", params={"foo": "bar"})
    d = r.to_dict()
    r2 = Rule.from_dict(d)
    assert r2.invariant == "no-cycles"
    assert r2.params == {"foo": "bar"}


def test_rule_quantified_round_trip():
    r = Rule(name="fan-out", type="quantified", selector="api.*", assertion="fan_out <= 5", scope="api")
    d = r.to_dict()
    r2 = Rule.from_dict(d)
    assert r2.type == "quantified"
    assert r2.selector == "api.*"
    assert r2.assertion == "fan_out <= 5"
    assert r2.scope == "api"


def test_parse_assertion_identifiers():
    parsed = parse_assertion("fan_out <= 5 and not in_cycle")
    assert parsed.identifiers == frozenset({"fan_out", "in_cycle"})
    assert evaluate_assertion(parsed, {"fan_out": 3, "in_cycle": False}) is True


def test_parse_assertion_rejects_calls():
    import pytest

    with pytest.raises(ValueError, match="unsupported syntax"):
        parse_assertion("metric()")


# --- Deny rule checking ---


def _make_graph():
    """Build a small graph: ui.app -[calls]-> db.query, core.lib -[imports]-> util.helpers."""
    g = SemGraph()
    for name in ["ui.app", "db.query", "core.lib", "util.helpers"]:
        g.add_node(Node(name=name, type=NodeType.FUNCTION))
    g.add_edge(Edge(source="ui.app", target="db.query", rel=RelType.CALLS))
    g.add_edge(Edge(source="core.lib", target="util.helpers", rel=RelType.IMPORTS))
    return g


def test_check_deny_violation():
    g = _make_graph()
    r = Rule(name="no-ui-db", type="deny", pattern="ui.* -[calls]-> db.*")
    v = check_deny(r, g)
    assert v is not None
    assert len(v.edges) == 1
    assert v.edges[0]["source"] == "ui.app"
    assert v.edges[0]["target"] == "db.query"


def test_check_deny_pass():
    g = _make_graph()
    r = Rule(name="no-core-db", type="deny", pattern="core.* -> db.*")
    v = check_deny(r, g)
    assert v is None


def test_check_deny_rel_filter():
    g = _make_graph()
    # Edge exists but with 'calls', not 'imports'
    r = Rule(name="test", type="deny", pattern="ui.* -[imports]-> db.*")
    v = check_deny(r, g)
    assert v is None


def test_check_deny_any_rel():
    g = _make_graph()
    # No rel filter: matches any coupling edge
    r = Rule(name="test", type="deny", pattern="ui.* -> db.*")
    v = check_deny(r, g)
    assert v is not None


def test_check_deny_non_coupling_edge_skipped():
    """Deny with no rel filter should skip contains edges."""
    g = SemGraph()
    g.add_node(Node(name="mod", type=NodeType.MODULE))
    g.add_node(Node(name="mod.func", type=NodeType.FUNCTION))
    g.add_edge(Edge(source="mod", target="mod.func", rel=RelType.CONTAINS))
    r = Rule(name="test", type="deny", pattern="mod -> mod.*")
    v = check_deny(r, g)
    assert v is None


# --- Invariant rule checking ---


def test_check_invariant_no_cycles_violation():
    g = SemGraph()
    g.add_node(Node(name="a", type=NodeType.FUNCTION))
    g.add_node(Node(name="b", type=NodeType.FUNCTION))
    g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))
    g.add_edge(Edge(source="b", target="a", rel=RelType.CALLS))
    r = Rule(name="acyclic", type="invariant", invariant="no-cycles")
    v = check_invariant(r, g)
    assert v is not None
    assert v.cycles is not None
    assert len(v.cycles) == 1


def test_check_invariant_no_cycles_pass():
    g = SemGraph()
    g.add_node(Node(name="a", type=NodeType.FUNCTION))
    g.add_node(Node(name="b", type=NodeType.FUNCTION))
    g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))
    r = Rule(name="acyclic", type="invariant", invariant="no-cycles")
    v = check_invariant(r, g)
    assert v is None


def test_check_invariant_no_dead_code():
    g = SemGraph()
    g.add_node(Node(name="main", type=NodeType.FUNCTION))
    g.add_node(Node(name="orphan", type=NodeType.FUNCTION))
    g.add_node(Node(name="used", type=NodeType.FUNCTION))
    g.add_edge(Edge(source="main", target="used", rel=RelType.CALLS))
    r = Rule(
        name="reachable",
        type="invariant",
        invariant="no-dead-code",
        params={"entry_points": "main"},
    )
    v = check_invariant(r, g)
    assert v is not None
    assert "orphan" in v.nodes
    assert "main" not in v.nodes


def test_check_invariant_no_dead_code_glob_entry():
    g = SemGraph()
    g.add_node(Node(name="cli.run", type=NodeType.FUNCTION))
    g.add_node(Node(name="cli.help", type=NodeType.FUNCTION))
    g.add_node(Node(name="orphan", type=NodeType.FUNCTION))
    r = Rule(
        name="reachable",
        type="invariant",
        invariant="no-dead-code",
        params={"entry_points": "cli.*"},
    )
    v = check_invariant(r, g)
    assert v is not None
    assert "orphan" in v.nodes
    assert "cli.run" not in v.nodes
    assert "cli.help" not in v.nodes


def test_check_invariant_no_layering_violations():
    g = SemGraph()
    g.add_node(Node(name="a", type=NodeType.FUNCTION))
    g.add_node(Node(name="b", type=NodeType.FUNCTION))
    g.add_edge(Edge(source="a", target="b", rel=RelType.CALLS))
    g.add_edge(Edge(source="b", target="a", rel=RelType.CALLS))
    r = Rule(name="layered", type="invariant", invariant="no-layering-violations")
    v = check_invariant(r, g)
    assert v is not None
    assert v.edges is not None


def test_check_invariant_concept_boundaries():
    g = SemGraph()
    g.add_node(Node(name="app", type=NodeType.PACKAGE))
    g.add_node(Node(name="app.ui", type=NodeType.MODULE))
    g.add_node(Node(name="app.core", type=NodeType.MODULE))
    g.add_node(Node(name="app.ui.render", type=NodeType.FUNCTION))
    g.add_node(Node(name="app.core.compute", type=NodeType.FUNCTION))
    g.add_edge(Edge(source="app", target="app.ui", rel=RelType.CONTAINS))
    g.add_edge(Edge(source="app", target="app.core", rel=RelType.CONTAINS))
    g.add_edge(Edge(source="app.ui", target="app.ui.render", rel=RelType.CONTAINS))
    g.add_edge(Edge(source="app.core", target="app.core.compute", rel=RelType.CONTAINS))
    g.add_edge(Edge(source="app.ui.render", target="app.core.compute", rel=RelType.CALLS))

    r = Rule(name="boundaries", type="invariant", invariant="concept-boundaries")
    v = check_invariant(
        r,
        g,
        concepts=[
            Concept(name="ui", prefixes=["app.ui"]),
            Concept(name="core", prefixes=["app.core"]),
        ],
    )

    assert v is not None
    assert v.message == "ui->core: 1 unsanctioned cross-concept edge(s)"
    assert v.edges == [{"source": "app.ui.render", "rel": "calls", "target": "app.core.compute"}]
    assert v.witnesses is not None
    assert v.witnesses[0].to_dict() == {
        "kind": "edge",
        "edges": [{"source": "app.ui.render", "rel": "calls", "target": "app.core.compute"}],
    }


def test_check_invariant_concept_boundaries_requires_concepts():
    import pytest

    g = SemGraph()
    r = Rule(name="boundaries", type="invariant", invariant="concept-boundaries")
    with pytest.raises(ValueError, match="requires at least one concept declaration"):
        check_invariant(r, g)


def test_check_invariant_unknown():
    import pytest

    g = SemGraph()
    r = Rule(name="bad", type="invariant", invariant="no-such-thing")
    with pytest.raises(ValueError, match="unknown invariant"):
        check_invariant(r, g)


# --- check_rule / check_all ---


def test_check_rule_dispatches():
    g = _make_graph()
    r = Rule(name="test", type="deny", pattern="ui.* -> db.*")
    v = check_rule(r, g)
    assert v is not None


def test_check_quantified_graph_metric_violation():
    g = _make_graph()
    r = Rule(name="fan-out", type="quantified", selector="*", assertion="fan_out <= 0")
    v = check_rule(r, g)
    assert v is not None
    assert v.nodes == ["core.lib", "ui.app"]
    assert v.witnesses is not None
    assert [w.subject for w in v.witnesses] == ["core.lib", "ui.app"]
    assert [w.facts for w in v.witnesses] == [{"fan_out": 1}, {"fan_out": 1}]


def test_check_quantified_node_metric_violation():
    g = SemGraph()
    g.add_node(
        Node(
            name="api.handler",
            type=NodeType.FUNCTION,
            metadata={"metrics": {"cyclomatic_complexity": 7, "cognitive_complexity": 5, "max_nesting_depth": 2}},
        )
    )
    r = Rule(name="simple-handler", type="quantified", selector="api.*", assertion="cyclomatic_complexity <= 5")
    v = check_rule(r, g)
    assert v is not None
    assert v.nodes == ["api.handler"]
    assert v.witnesses is not None
    assert v.witnesses[0].facts == {"cyclomatic_complexity": 7}


def test_check_quantified_missing_metric_errors():
    import pytest

    g = SemGraph()
    g.add_node(Node(name="api.handler", type=NodeType.FUNCTION))
    r = Rule(name="class-budget", type="quantified", selector="api.*", assertion="wmc <= 10")
    with pytest.raises(ValueError, match="not defined for non-class subject"):
        check_rule(r, g)


def test_check_all_mixed():
    g = _make_graph()
    rules = [
        Rule(name="passes", type="deny", pattern="core.* -> db.*"),
        Rule(name="fails", type="deny", pattern="ui.* -> db.*"),
    ]
    violations = check_all(rules, g)
    assert len(violations) == 1
    assert violations[0].rule_name == "fails"


# --- CLI integration ---


def _init_runner(tmp_path):
    runner = CliRunner()
    os.chdir(tmp_path)
    runner.invoke(main, ["init"])
    return runner


def _init_with_graph(tmp_path):
    """Init project and add some nodes/edges for testing."""
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "function", "ui.app"])
    runner.invoke(main, ["add", "function", "db.query"])
    runner.invoke(main, ["add", "function", "core.lib"])
    runner.invoke(main, ["link", "ui.app", "calls", "db.query"])
    runner.invoke(main, ["link", "core.lib", "calls", "ui.app"])
    return runner


def test_cli_rule_add_deny(tmp_path):
    runner = _init_with_graph(tmp_path)
    result = runner.invoke(main, ["rule", "add", "layering", "--deny", "core.* -> ui.*"])
    assert result.exit_code == 0
    assert "added" in result.output
    assert (tmp_path / ".smg" / "rules").exists()


def test_cli_rule_add_invariant(tmp_path):
    runner = _init_with_graph(tmp_path)
    result = runner.invoke(main, ["rule", "add", "acyclic", "--invariant", "no-cycles"])
    assert result.exit_code == 0
    assert "added" in result.output


def test_cli_rule_add_quantified(tmp_path):
    runner = _init_with_graph(tmp_path)
    result = runner.invoke(main, ["rule", "add", "fan-out", "--forall", "*", "--assert", "fan_out <= 5"])
    assert result.exit_code == 0
    listed = runner.invoke(main, ["rule", "list", "--format", "json"])
    data = json.loads(listed.output)
    assert data[0]["type"] == "quantified"
    assert data[0]["selector"] == "*"
    assert data[0]["assertion"] == "fan_out <= 5"


def test_cli_rule_add_quantified_unknown_metric(tmp_path):
    runner = _init_with_graph(tmp_path)
    result = runner.invoke(main, ["rule", "add", "bad", "--forall", "*", "--assert", "mystery <= 1"])
    assert result.exit_code == 2
    assert "unknown metric identifier" in result.output


def test_cli_rule_add_duplicate(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "r1", "--invariant", "no-cycles"])
    result = runner.invoke(main, ["rule", "add", "r1", "--invariant", "no-cycles"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_cli_rule_add_invalid_deny(tmp_path):
    runner = _init_with_graph(tmp_path)
    result = runner.invoke(main, ["rule", "add", "bad", "--deny", "not a pattern"])
    assert result.exit_code != 0


def test_cli_rule_add_no_option(tmp_path):
    runner = _init_with_graph(tmp_path)
    result = runner.invoke(main, ["rule", "add", "bad"])
    assert result.exit_code != 0


def test_cli_rule_list(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "r1", "--deny", "a.* -> b.*"])
    runner.invoke(main, ["rule", "add", "r2", "--invariant", "no-cycles"])
    result = runner.invoke(main, ["rule", "list", "--format", "json"])
    data = json.loads(result.output)
    assert len(data) == 2
    names = {r["name"] for r in data}
    assert names == {"r1", "r2"}


def test_cli_rule_rm(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "r1", "--deny", "a.* -> b.*"])
    result = runner.invoke(main, ["rule", "rm", "r1"])
    assert result.exit_code == 0
    assert "removed" in result.output.lower()
    # Verify it's gone
    result = runner.invoke(main, ["rule", "list", "--format", "json"])
    data = json.loads(result.output)
    assert len(data) == 0


def test_cli_rule_rm_not_found(tmp_path):
    runner = _init_with_graph(tmp_path)
    result = runner.invoke(main, ["rule", "rm", "nonexistent"])
    assert result.exit_code != 0


def test_cli_check_pass(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "r1", "--deny", "db.* -> ui.*"])
    result = runner.invoke(main, ["check", "--format", "json"])
    data = json.loads(result.output)
    assert data["status"] == "pass"
    assert result.exit_code == 0


def test_cli_check_violation(tmp_path):
    runner = _init_with_graph(tmp_path)
    # ui.app calls db.query — this deny rule should catch it
    runner.invoke(main, ["rule", "add", "no-ui-db", "--deny", "ui.* -> db.*"])
    result = runner.invoke(main, ["check", "--format", "json"])
    data = json.loads(result.output)
    assert data["status"] == "fail"
    assert len(data["violations"]) == 1
    assert data["violations"][0]["rule"] == "no-ui-db"
    assert "witnesses" in data["violations"][0]
    assert result.exit_code == 1


def test_cli_check_concept_boundaries(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "package", "app"])
    runner.invoke(main, ["add", "module", "app.ui"])
    runner.invoke(main, ["add", "module", "app.core"])
    runner.invoke(main, ["add", "function", "app.ui.render"])
    runner.invoke(main, ["add", "function", "app.core.compute"])
    runner.invoke(main, ["link", "app", "contains", "app.ui"])
    runner.invoke(main, ["link", "app", "contains", "app.core"])
    runner.invoke(main, ["link", "app.ui", "contains", "app.ui.render"])
    runner.invoke(main, ["link", "app.core", "contains", "app.core.compute"])
    runner.invoke(main, ["link", "app.ui.render", "calls", "app.core.compute"])
    runner.invoke(main, ["concept", "add", "ui", "--prefix", "app.ui"])
    runner.invoke(main, ["concept", "add", "core", "--prefix", "app.core"])
    add_rule = runner.invoke(main, ["rule", "add", "boundaries", "--invariant", "concept-boundaries"])
    assert add_rule.exit_code == 0

    result = runner.invoke(main, ["check", "--format", "json"])
    data = json.loads(result.output)

    assert result.exit_code == 1
    assert data["status"] == "fail"
    assert data["violations"] == [
        {
            "rule": "boundaries",
            "type": "invariant",
            "message": "ui->core: 1 unsanctioned cross-concept edge(s)",
            "witnesses": [
                {
                    "kind": "edge",
                    "edges": [
                        {
                            "source": "app.ui.render",
                            "rel": "calls",
                            "target": "app.core.compute",
                        }
                    ],
                }
            ],
            "edges": [
                {
                    "source": "app.ui.render",
                    "rel": "calls",
                    "target": "app.core.compute",
                }
            ],
        }
    ]


def test_cli_check_quantified_violation_json(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "fan-out", "--forall", "*", "--assert", "fan_out <= 0"])
    result = runner.invoke(main, ["check", "--format", "json"])
    data = json.loads(result.output)
    assert data["status"] == "fail"
    violation = data["violations"][0]
    assert violation["type"] == "quantified"
    assert violation["nodes"] == ["core.lib", "ui.app"]
    assert violation["witnesses"] == [
        {
            "kind": "predicate",
            "subject": "core.lib",
            "assertion": "fan_out <= 0",
            "facts": {"fan_out": 1},
        },
        {
            "kind": "predicate",
            "subject": "ui.app",
            "assertion": "fan_out <= 0",
            "facts": {"fan_out": 1},
        },
    ]
    assert result.exit_code == 1


def test_cli_check_quantified_config_error(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "bad", "--forall", "*", "--assert", "wmc <= 1"])
    result = runner.invoke(main, ["check"])
    assert result.exit_code == 2
    assert "not defined for non-class subject" in result.output


def test_cli_check_specific_rule(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "r1", "--deny", "ui.* -> db.*"])
    runner.invoke(main, ["rule", "add", "r2", "--deny", "db.* -> ui.*"])
    result = runner.invoke(main, ["check", "r2", "--format", "json"])
    data = json.loads(result.output)
    assert data["status"] == "pass"
    assert data["rules_checked"] == 1


def test_cli_check_no_rules(tmp_path):
    runner = _init_with_graph(tmp_path)
    result = runner.invoke(main, ["check", "--format", "json"])
    data = json.loads(result.output)
    assert data["status"] == "no_rules"


def test_cli_check_text_output(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "no-ui-db", "--deny", "ui.* -> db.*"])
    runner.invoke(main, ["rule", "add", "ok-rule", "--deny", "db.* -> ui.*"])
    result = runner.invoke(main, ["check", "--format", "text"])
    assert "FAIL" in result.output
    assert "PASS" in result.output
    assert "ui.app" in result.output


def test_cli_check_quantified_text_output(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "fan-out", "--forall", "*", "--assert", "fan_out <= 0"])
    result = runner.invoke(main, ["check", "--format", "text"])
    assert "FAIL" in result.output
    assert "core.lib: fan_out=1" in result.output
    assert "ui.app: fan_out=1" in result.output


# --- Scoped deny rules ---


def test_scoped_deny_catches_cross_boundary():
    """A scoped deny rule must detect edges whose target is outside the scope."""
    g = SemGraph()
    g.add_node(Node(name="api.service", type=NodeType.FUNCTION))
    g.add_node(Node(name="db.query", type=NodeType.FUNCTION))
    g.add_edge(Edge(source="api.service", target="db.query", rel=RelType.CALLS))

    r = Rule(name="no-api-db", type="deny", pattern="api.* -> db.*", scope="api")
    v = check_rule(r, g)
    assert v is not None
    assert len(v.edges) == 1
    assert v.edges[0]["source"] == "api.service"
    assert v.edges[0]["target"] == "db.query"


def test_scoped_deny_ignores_out_of_scope_source():
    """A scoped deny rule should not flag edges originating outside the scope."""
    g = SemGraph()
    g.add_node(Node(name="api.service", type=NodeType.FUNCTION))
    g.add_node(Node(name="db.query", type=NodeType.FUNCTION))
    g.add_node(Node(name="web.handler", type=NodeType.FUNCTION))
    g.add_edge(Edge(source="api.service", target="db.query", rel=RelType.CALLS))
    g.add_edge(Edge(source="web.handler", target="db.query", rel=RelType.CALLS))

    # scope=api: only api.service -> db.query should be checked
    r = Rule(name="no-all-db", type="deny", pattern="*.* -> db.*", scope="api")
    v = check_rule(r, g)
    assert v is not None
    assert len(v.edges) == 1
    assert v.edges[0]["source"] == "api.service"
