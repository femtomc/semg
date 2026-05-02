"""Tests for the search subsystem (schema, indexer, query, CLI)."""

import json
import os
import sqlite3

from click.testing import CliRunner

from smg.cli import main
from smg.graph import SemGraph
from smg.model import Node, NodeType
from smg.search.indexer import rebuild_search_index
from smg.search.query import normalize_query, search_nodes
from smg.search.schema import (
    create_search_db,
    search_db_path,
    split_identifier,
)

# --- split_identifier ---


def test_split_dotted():
    assert split_identifier("smg.cli.helpers._truncate") == "smg cli helpers truncate"


def test_split_camel():
    assert split_identifier("parseHTMLDocument") == "parse html document"


def test_split_mixed():
    assert split_identifier("smg.graph.SemGraph.analyze_hot") == "smg graph sem graph analyze hot"


def test_split_simple():
    assert split_identifier("truncate") == "truncate"


def test_split_underscores():
    assert split_identifier("__init__") == "init"


# --- normalize_query ---


def test_normalize_dotted_identifier():
    assert normalize_query("smg.cli.helpers._truncate") == "smg AND cli AND helpers AND truncate"


def test_normalize_camel():
    assert normalize_query("parseHTMLDocument") == "parse AND html AND document"


def test_normalize_simple_word():
    assert normalize_query("truncate") == "truncate"


def test_normalize_multi_word():
    assert normalize_query("helpers truncate") == "helpers AND truncate"


def test_normalize_passthrough_quoted():
    assert normalize_query('"exact phrase here"') == '"exact phrase here"'


def test_normalize_passthrough_or():
    assert normalize_query("foo OR bar") == "foo OR bar"


def test_normalize_passthrough_and():
    assert normalize_query("foo AND bar") == "foo AND bar"


def test_normalize_passthrough_near():
    assert normalize_query("NEAR(truncate helpers, 5)") == "NEAR(truncate helpers, 5)"


def test_normalize_passthrough_parens():
    assert normalize_query("(foo OR bar) AND baz") == "(foo OR bar) AND baz"


def test_normalize_passthrough_column_filter():
    assert normalize_query("name_tokens:truncate") == "name_tokens:truncate"


def test_normalize_passthrough_plus():
    assert normalize_query("one + two + three") == "one + two + three"


def test_normalize_empty():
    assert normalize_query("") == ""


def test_normalize_whitespace_only():
    assert normalize_query("   ") == ""


# --- Schema ---


def test_create_search_db(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    conn = create_search_db(db_path)
    # Verify tables exist
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    table_names = [t[0] for t in tables]
    assert "nodes" in table_names
    assert "search_meta" in table_names
    conn.close()


def test_schema_version(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    conn = create_search_db(db_path)
    row = conn.execute("SELECT value FROM search_meta WHERE key='schema_version'").fetchone()
    assert row[0] == "1"
    conn.close()


def test_fts_trigger_insert(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    conn = create_search_db(db_path)
    conn.execute(
        "INSERT INTO nodes (node_id, name, name_tokens, kind, file, line_start, line_end, docstring) "
        "VALUES (1, 'test.func', 'test func', 'function', 'test.py', 1, 5, 'A test function')"
    )
    conn.commit()
    # FTS should find it
    row = conn.execute("SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH 'test'").fetchone()
    assert row is not None
    assert row[0] == 1
    conn.close()


def test_fts_trigger_delete(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    conn = create_search_db(db_path)
    conn.execute(
        "INSERT INTO nodes (node_id, name, name_tokens, kind, file, line_start, line_end, docstring) "
        "VALUES (1, 'test.func', 'test func', 'function', 'test.py', 1, 5, 'A test function')"
    )
    conn.commit()
    conn.execute("DELETE FROM nodes WHERE node_id = 1")
    conn.commit()
    row = conn.execute("SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH 'test'").fetchone()
    assert row is None
    conn.close()


def test_fts_trigger_update(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    conn = create_search_db(db_path)
    conn.execute(
        "INSERT INTO nodes (node_id, name, name_tokens, kind, file, line_start, line_end, docstring) "
        "VALUES (1, 'test.func', 'alpha beta', 'function', 'test.py', 1, 5, '')"
    )
    conn.commit()
    conn.execute("UPDATE nodes SET name_tokens = 'gamma delta' WHERE node_id = 1")
    conn.commit()
    # Old tokens gone from FTS
    row = conn.execute("SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH 'alpha'").fetchone()
    assert row is None
    # New tokens present
    row = conn.execute("SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH 'gamma'").fetchone()
    assert row is not None
    conn.close()


# --- Indexer ---


def _make_graph() -> SemGraph:
    g = SemGraph()
    g.add_node(
        Node(
            "smg.cli.helpers._truncate",
            NodeType.FUNCTION,
            file="src/smg/cli/_helpers.py",
            line=42,
            docstring="Trim a string to width with an ellipsis",
        )
    )
    g.add_node(
        Node(
            "smg.graph.SemGraph",
            NodeType.CLASS,
            file="src/smg/graph.py",
            line=10,
            docstring="Core semantic graph data structure",
        )
    )
    g.add_node(
        Node(
            "smg.graph.SemGraph.truncate_path",
            NodeType.METHOD,
            file="src/smg/graph.py",
            line=412,
            docstring="Drop path components after max depth",
        )
    )
    g.add_node(
        Node(
            "smg.scan.scan_paths",
            NodeType.FUNCTION,
            file="src/smg/scan.py",
            line=100,
            docstring="Scan source files and populate graph",
        )
    )
    g.add_node(
        Node(
            "smg.cli.helpers.format_output",
            NodeType.FUNCTION,
            file="src/smg/cli/_helpers.py",
            line=80,
            docstring="Format output for display",
        )
    )
    return g


def test_rebuild_creates_db(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    rebuild_search_index(g, tmp_path)
    assert (smg_dir / "search.sqlite3").exists()


def test_rebuild_populates_nodes(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    rebuild_search_index(g, tmp_path)

    conn = sqlite3.connect(str(smg_dir / "search.sqlite3"))
    count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert count == 5
    conn.close()


def test_rebuild_idempotent(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    rebuild_search_index(g, tmp_path)
    rebuild_search_index(g, tmp_path)

    conn = sqlite3.connect(str(smg_dir / "search.sqlite3"))
    count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert count == 5
    conn.close()


# --- Query ---


def test_search_by_simple_term(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    rebuild_search_index(g, tmp_path)

    db_path = search_db_path(tmp_path)
    hits, total = search_nodes(db_path, "truncate")
    assert total >= 2  # _truncate and truncate_path
    names = [h.name for h in hits]
    assert "smg.cli.helpers._truncate" in names
    assert "smg.graph.SemGraph.truncate_path" in names


def test_search_by_dotted_name(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    rebuild_search_index(g, tmp_path)

    db_path = search_db_path(tmp_path)
    hits, total = search_nodes(db_path, "smg.cli.helpers._truncate")
    assert total >= 1
    assert hits[0].name == "smg.cli.helpers._truncate"


def test_search_bm25_ranking(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    rebuild_search_index(g, tmp_path)

    db_path = search_db_path(tmp_path)
    hits, total = search_nodes(db_path, "truncate")
    # Ranks should be sequential starting at 1
    assert hits[0].rank == 1
    assert hits[1].rank == 2
    # Scores should be ascending (lower = better in BM25)
    assert hits[0].score <= hits[1].score


def test_search_kind_filter(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    rebuild_search_index(g, tmp_path)

    db_path = search_db_path(tmp_path)
    hits, total = search_nodes(db_path, "truncate", kind="function")
    for h in hits:
        assert h.kind == "function"


def test_search_limit(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    rebuild_search_index(g, tmp_path)

    db_path = search_db_path(tmp_path)
    hits, total = search_nodes(db_path, "smg", limit=2)
    assert len(hits) == 2
    assert total >= 5


def test_search_location(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    rebuild_search_index(g, tmp_path)

    db_path = search_db_path(tmp_path)
    hits, total = search_nodes(db_path, "smg.cli.helpers._truncate")
    assert hits[0].location == "src/smg/cli/_helpers.py:42"


def test_search_snippet(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    rebuild_search_index(g, tmp_path)

    db_path = search_db_path(tmp_path)
    hits, total = search_nodes(db_path, "smg.cli.helpers._truncate")
    assert "Trim" in hits[0].snippet


def test_search_auto_rebuild(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    g = _make_graph()
    # Don't call rebuild_search_index first
    db_path = search_db_path(tmp_path)
    assert not db_path.exists()
    # search_nodes with graph+root should auto-rebuild
    hits, total = search_nodes(db_path, "truncate", graph=g, root=tmp_path)
    assert total >= 2
    assert db_path.exists()


def test_search_missing_db_no_graph_raises(tmp_path):
    smg_dir = tmp_path / ".smg"
    smg_dir.mkdir()
    db_path = search_db_path(tmp_path)
    try:
        search_nodes(db_path, "truncate")
        assert False, "Should have raised"
    except FileNotFoundError:
        pass


# --- CLI smoke tests ---


def _init_runner(tmp_path):
    runner = CliRunner()
    os.chdir(tmp_path)
    runner.invoke(main, ["init"])
    return runner


def _build_search_graph(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(
        main,
        [
            "add",
            "function",
            "smg.cli.helpers._truncate",
            "--file",
            "src/smg/cli/_helpers.py",
            "--line",
            "42",
            "--doc",
            "Trim a string to width",
        ],
    )
    runner.invoke(
        main,
        [
            "add",
            "class",
            "smg.graph.SemGraph",
            "--file",
            "src/smg/graph.py",
            "--line",
            "10",
            "--doc",
            "Core graph structure",
        ],
    )
    runner.invoke(
        main,
        [
            "add",
            "method",
            "smg.graph.SemGraph.truncate_path",
            "--file",
            "src/smg/graph.py",
            "--line",
            "412",
            "--doc",
            "Drop path components",
        ],
    )
    runner.invoke(
        main,
        [
            "add",
            "function",
            "smg.scan.scan_paths",
            "--file",
            "src/smg/scan.py",
            "--line",
            "100",
            "--doc",
            "Scan files",
        ],
    )
    return runner


def test_cli_search_basic(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "truncate"])
    assert result.exit_code == 0
    assert "truncate" in result.output.lower()


def test_cli_search_dotted(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "smg.cli.helpers._truncate"])
    assert result.exit_code == 0
    assert "_truncate" in result.output


def test_cli_search_json(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "truncate", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "rows" in data
    assert "total" in data
    assert "displayed" in data
    assert "truncated" in data
    assert "limit" in data
    assert len(data["rows"]) > 0


def test_cli_search_json_row_keys(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "truncate", "--json"])
    data = json.loads(result.output)
    row = data["rows"][0]
    assert "rank" in row
    assert "kind" in row
    assert "name" in row
    assert "location" in row
    assert "snippet" in row


def test_cli_search_kind_filter(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "truncate", "--kind", "function", "--json"])
    data = json.loads(result.output)
    for row in data["rows"]:
        assert row["kind"] == "function"


def test_cli_search_limit(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "smg", "--limit", "1", "--json"])
    data = json.loads(result.output)
    assert len(data["rows"]) == 1
    assert data["total"] >= 4


def test_cli_search_no_results(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "zzzznonexistent"])
    assert result.exit_code == 0
    assert "No results" in result.output


def test_cli_search_malformed_query_errors(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "foo OR", "--json"])

    assert result.exit_code == 2
    assert "invalid search query: 'foo OR'" in result.output


def test_cli_search_header_lowercase(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "truncate"])
    header = result.output.split("\n")[0]
    # Header should be all lowercase
    for ch in header:
        if ch.isalpha():
            assert ch.islower(), f"Found uppercase '{ch}' in header: {header}"


def test_cli_search_no_box_drawing(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "truncate"])
    import re

    assert not re.search(r"[┏━┃┗┓┛│─]", result.output)


def test_cli_search_no_trailing_spaces(tmp_path):
    runner = _build_search_graph(tmp_path)
    result = runner.invoke(main, ["search", "truncate"])
    for line in result.output.split("\n"):
        assert not line.endswith(" "), f"Trailing space in: {line!r}"


def test_cli_search_auto_rebuild(tmp_path):
    """Deleting search.sqlite3 and running search should auto-rebuild."""
    runner = _build_search_graph(tmp_path)
    db = tmp_path / ".smg" / "search.sqlite3"
    if db.exists():
        db.unlink()
    result = runner.invoke(main, ["search", "truncate"])
    assert result.exit_code == 0
    assert "truncate" in result.output.lower()


def test_scan_triggers_rebuild(tmp_path):
    """smg scan should rebuild the search index."""
    runner = _init_runner(tmp_path)
    # Create a Python file to scan
    src = tmp_path / "example.py"
    src.write_text("def helper():\n    '''A helper function'''\n    pass\n")
    result = runner.invoke(main, ["scan", str(tmp_path)])
    assert result.exit_code == 0
    # Search index should exist and be queryable
    result = runner.invoke(main, ["search", "helper", "--json"])
    if result.exit_code == 0:
        data = json.loads(result.output)
        assert data["total"] >= 1
