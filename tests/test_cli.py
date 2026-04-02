import json
import os

from click.testing import CliRunner

from smg.cli import main

# Note: CliRunner stdout is not a TTY, so auto-format defaults to JSON.
# Tests that check text output must use --format text explicitly.


def _init_runner(tmp_path):
    runner = CliRunner()
    os.chdir(tmp_path)
    runner.invoke(main, ["init"])
    return runner


def test_init(tmp_path):
    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(main, ["init"])
    assert result.exit_code == 0
    assert "Initialized" in result.output
    assert (tmp_path / ".smg" / "graph.jsonl").exists()


def test_add_and_list(tmp_path):
    runner = _init_runner(tmp_path)
    result = runner.invoke(main, ["add", "module", "app"])
    assert result.exit_code == 0

    # Auto-format: JSON in non-TTY
    result = runner.invoke(main, ["list"])
    data = json.loads(result.output)
    assert any(n["name"] == "app" for n in data)


def test_add_with_options(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "function", "main", "--file", "app.py", "--line", "1", "--doc", "entry"])
    result = runner.invoke(main, ["show", "main"])
    data = json.loads(result.output)
    assert data["file"] == "app.py"
    assert data["line"] == 1
    assert data["docstring"] == "entry"


def test_link_and_show(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    runner.invoke(main, ["add", "function", "app.main"])
    runner.invoke(main, ["link", "app", "contains", "app.main"])

    result = runner.invoke(main, ["show", "app.main"])
    data = json.loads(result.output)
    assert any(e["rel"] == "contains" for e in data["incoming"])


def test_rm(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    runner.invoke(main, ["add", "function", "app.main"])
    runner.invoke(main, ["link", "app", "contains", "app.main"])

    result = runner.invoke(main, ["rm", "app.main"])
    assert result.exit_code == 0

    result = runner.invoke(main, ["list"])
    data = json.loads(result.output)
    assert not any(n["name"] == "app.main" for n in data)


def test_unlink(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "a"])
    runner.invoke(main, ["add", "module", "b"])
    runner.invoke(main, ["link", "a", "imports", "b"])

    result = runner.invoke(main, ["unlink", "a", "imports", "b"])
    assert result.exit_code == 0


def test_update(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])

    result = runner.invoke(main, ["update", "app", "--type", "package", "--file", "app/__init__.py"])
    assert result.exit_code == 0

    result = runner.invoke(main, ["show", "app", "--format", "json"])
    data = json.loads(result.output)
    assert data["type"] == "package"
    assert data["file"] == "app/__init__.py"


def test_status(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    runner.invoke(main, ["add", "function", "app.main"])
    runner.invoke(main, ["link", "app", "contains", "app.main"])

    result = runner.invoke(main, ["status"])
    data = json.loads(result.output)
    assert data["nodes"] == 2
    assert data["edges"] == 1


def test_status_json(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["status", "--format", "json"])
    data = json.loads(result.output)
    assert data["nodes"] == 1


def test_query_subgraph(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "a"])
    runner.invoke(main, ["add", "module", "b"])
    runner.invoke(main, ["add", "module", "c"])
    runner.invoke(main, ["link", "a", "imports", "b"])
    runner.invoke(main, ["link", "b", "imports", "c"])

    result = runner.invoke(main, ["query", "subgraph", "b", "--depth", "1"])
    # Auto JSON output
    data = json.loads(result.output)
    names = [n["name"] for n in data["nodes"]]
    assert "a" in names
    assert "b" in names
    assert "c" in names


def test_query_path(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "a"])
    runner.invoke(main, ["add", "module", "b"])
    runner.invoke(main, ["link", "a", "imports", "b"])

    result = runner.invoke(main, ["query", "path", "a", "b"])
    data = json.loads(result.output)
    assert data == ["a", "b"]


def test_export_mermaid(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["export", "mermaid"])
    assert "graph TD" in result.output


def test_export_dot(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["export", "dot"])
    assert "digraph smg" in result.output


def test_export_json(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["export", "json"])
    assert '"nodes"' in result.output


def test_validate_clean(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["validate"])
    assert "valid" in result.output


def test_no_project_error(tmp_path):
    runner = CliRunner()
    os.chdir(tmp_path)
    result = runner.invoke(main, ["list"])
    assert result.exit_code != 0


def test_short_name_resolution(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "class", "app.Server"])
    result = runner.invoke(main, ["show", "Server"])
    data = json.loads(result.output)
    assert data["name"] == "app.Server"


def test_list_format_json(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["list", "--format", "json"])
    data = json.loads(result.output)
    assert data[0]["name"] == "app"


def test_list_type_filter(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    runner.invoke(main, ["add", "function", "app.main"])

    result = runner.invoke(main, ["list", "--type", "function"])
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["name"] == "app.main"


def test_add_idempotent(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["add", "module", "app"])
    assert result.exit_code == 0

    result = runner.invoke(main, ["list"])
    data = json.loads(result.output)
    assert len(data) == 1


def test_meta_option(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "function", "f", "--meta", "async=true", "--meta", "pure=yes"])
    result = runner.invoke(main, ["show", "f", "--format", "json"])
    data = json.loads(result.output)
    assert data["metadata"]["async"] == "true"
    assert data["metadata"]["pure"] == "yes"


# --- Force text output tests ---


def test_show_text(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "function", "main", "--file", "app.py", "--line", "1"])
    result = runner.invoke(main, ["show", "main", "--format", "text"])
    assert "app.py:1" in result.output


def test_status_text(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["status", "--format", "text"])
    assert "Nodes (1)" in result.output


# --- High-level Explore commands ---


def _build_sample_graph(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "package", "app"])
    runner.invoke(main, ["add", "module", "app.core"])
    runner.invoke(main, ["add", "class", "app.core.Engine"])
    runner.invoke(main, ["add", "method", "app.core.Engine.run"])
    runner.invoke(main, ["add", "module", "app.utils"])
    runner.invoke(main, ["add", "function", "app.utils.helper"])
    runner.invoke(main, ["link", "app", "contains", "app.core"])
    runner.invoke(main, ["link", "app", "contains", "app.utils"])
    runner.invoke(main, ["link", "app.core", "contains", "app.core.Engine"])
    runner.invoke(main, ["link", "app.core.Engine", "contains", "app.core.Engine.run"])
    runner.invoke(main, ["link", "app.utils", "contains", "app.utils.helper"])
    runner.invoke(main, ["link", "app.core", "imports", "app.utils"])
    runner.invoke(main, ["link", "app.core.Engine.run", "calls", "app.utils.helper"])
    return runner


def test_about_depth_0(tmp_path):
    runner = _build_sample_graph(tmp_path)
    result = runner.invoke(main, ["about", "app.core.Engine", "--depth", "0"])
    data = json.loads(result.output)
    assert data["node"]["name"] == "app.core.Engine"
    assert data["node"]["type"] == "class"
    assert "incoming" not in data


def test_about_depth_1(tmp_path):
    runner = _build_sample_graph(tmp_path)
    result = runner.invoke(main, ["about", "app.core.Engine"])
    data = json.loads(result.output)
    assert data["node"]["name"] == "app.core.Engine"
    assert "incoming" in data
    assert "outgoing" in data
    assert "containment_path" in data
    assert data["containment_path"] == ["app", "app.core", "app.core.Engine"]


def test_about_depth_2(tmp_path):
    runner = _build_sample_graph(tmp_path)
    result = runner.invoke(main, ["about", "app.core.Engine", "--depth", "2"])
    data = json.loads(result.output)
    assert "neighbors" in data
    assert len(data["neighbors"]) > 0


def test_impact(tmp_path):
    runner = _build_sample_graph(tmp_path)
    result = runner.invoke(main, ["impact", "app.utils.helper"])
    data = json.loads(result.output)
    assert data["target"] == "app.utils.helper"
    assert "app.core.Engine.run" in data["affected"]
    assert data["count"] > 0


def test_impact_no_dependents(tmp_path):
    runner = _build_sample_graph(tmp_path)
    result = runner.invoke(main, ["impact", "app"])
    data = json.loads(result.output)
    assert data["count"] == 0


def test_between(tmp_path):
    runner = _build_sample_graph(tmp_path)
    result = runner.invoke(main, ["between", "app.core", "app.utils"])
    data = json.loads(result.output)
    assert data["source"] == "app.core"
    assert data["target"] == "app.utils"
    assert data["path"] is not None
    assert len(data["direct_edges"]) > 0


def test_between_no_path(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "a"])
    runner.invoke(main, ["add", "module", "b"])
    result = runner.invoke(main, ["between", "a", "b"])
    data = json.loads(result.output)
    assert data["path"] is None


def test_usages(tmp_path):
    runner = _build_sample_graph(tmp_path)
    result = runner.invoke(main, ["usages", "app.utils.helper"])
    data = json.loads(result.output)
    assert data["target"] == "app.utils.helper"
    assert data["count"] >= 1
    # Engine.run calls helper
    nodes = [u["node"] for u in data["usages"]]
    assert "app.core.Engine.run" in nodes


def test_usages_with_rel_filter(tmp_path):
    runner = _build_sample_graph(tmp_path)
    result = runner.invoke(main, ["usages", "app.utils.helper", "--rel", "calls"])
    data = json.loads(result.output)
    for u in data["usages"]:
        assert u["rel"] == "calls"


def test_usages_includes_location(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "function", "target", "--file", "lib.py", "--line", "10"])
    runner.invoke(main, ["add", "function", "caller", "--file", "app.py", "--line", "20"])
    runner.invoke(main, ["link", "caller", "calls", "target"])
    result = runner.invoke(main, ["usages", "target"])
    data = json.loads(result.output)
    assert data["count"] == 1
    u = data["usages"][0]
    assert u["node"] == "caller"
    assert u["file"] == "app.py"
    assert u["line"] == 20


def test_usages_no_results(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "isolated"])
    result = runner.invoke(main, ["usages", "isolated"])
    data = json.loads(result.output)
    assert data["count"] == 0
    assert data["usages"] == []


def test_overview(tmp_path):
    runner = _build_sample_graph(tmp_path)
    result = runner.invoke(main, ["overview"])
    data = json.loads(result.output)
    assert data["nodes"] == 6
    assert data["edges"] == 7
    assert len(data["top_connected"]) > 0
    assert len(data["modules"]) > 0
    # Check top connected has expected fields
    top = data["top_connected"][0]
    assert "name" in top
    assert "incoming" in top
    assert "outgoing" in top
    assert "total" in top


def test_overview_top(tmp_path):
    runner = _build_sample_graph(tmp_path)
    result = runner.invoke(main, ["overview", "--top", "2"])
    data = json.loads(result.output)
    assert len(data["top_connected"]) == 2


# --- Auto-format detection ---


def test_auto_format_json_when_piped(tmp_path):
    """CliRunner is not a TTY, so auto-format should produce JSON."""
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["show", "app"])
    # Should be valid JSON
    data = json.loads(result.output)
    assert data["name"] == "app"


def test_explicit_format_overrides(tmp_path):
    """--format text should force text even in non-TTY."""
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["add", "module", "app"])
    result = runner.invoke(main, ["show", "app", "--format", "text"])
    # Should NOT be JSON
    assert result.output.strip().startswith("╭") or "app" in result.output
