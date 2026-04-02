"""Tests for the architectural constraint system (rules + check)."""
import json
import os

from click.testing import CliRunner

from smg.cli import main
from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType
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
    r = Rule(name="reachable", type="invariant", invariant="no-dead-code",
             params={"entry_points": "main"})
    v = check_invariant(r, g)
    assert v is not None
    assert "orphan" in v.nodes
    assert "main" not in v.nodes


def test_check_invariant_no_dead_code_glob_entry():
    g = SemGraph()
    g.add_node(Node(name="cli.run", type=NodeType.FUNCTION))
    g.add_node(Node(name="cli.help", type=NodeType.FUNCTION))
    g.add_node(Node(name="orphan", type=NodeType.FUNCTION))
    r = Rule(name="reachable", type="invariant", invariant="no-dead-code",
             params={"entry_points": "cli.*"})
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
    result = runner.invoke(main, ["rule", "list"])
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
    result = runner.invoke(main, ["rule", "list"])
    data = json.loads(result.output)
    assert len(data) == 0


def test_cli_rule_rm_not_found(tmp_path):
    runner = _init_with_graph(tmp_path)
    result = runner.invoke(main, ["rule", "rm", "nonexistent"])
    assert result.exit_code != 0


def test_cli_check_pass(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "r1", "--deny", "db.* -> ui.*"])
    result = runner.invoke(main, ["check"])
    data = json.loads(result.output)
    assert data["status"] == "pass"
    assert result.exit_code == 0


def test_cli_check_violation(tmp_path):
    runner = _init_with_graph(tmp_path)
    # ui.app calls db.query — this deny rule should catch it
    runner.invoke(main, ["rule", "add", "no-ui-db", "--deny", "ui.* -> db.*"])
    result = runner.invoke(main, ["check"])
    data = json.loads(result.output)
    assert data["status"] == "fail"
    assert len(data["violations"]) == 1
    assert data["violations"][0]["rule"] == "no-ui-db"
    assert result.exit_code == 1


def test_cli_check_specific_rule(tmp_path):
    runner = _init_with_graph(tmp_path)
    runner.invoke(main, ["rule", "add", "r1", "--deny", "ui.* -> db.*"])
    runner.invoke(main, ["rule", "add", "r2", "--deny", "db.* -> ui.*"])
    result = runner.invoke(main, ["check", "r2"])
    data = json.loads(result.output)
    assert data["status"] == "pass"
    assert data["rules_checked"] == 1


def test_cli_check_no_rules(tmp_path):
    runner = _init_with_graph(tmp_path)
    result = runner.invoke(main, ["check"])
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
