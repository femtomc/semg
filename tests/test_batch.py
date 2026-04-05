"""Tests for batch command."""

import json
import os

from click.testing import CliRunner

from smg.cli import main


def _init_runner(tmp_path):
    runner = CliRunner()
    os.chdir(tmp_path)
    runner.invoke(main, ["init"])
    return runner


def test_batch_add(tmp_path):
    runner = _init_runner(tmp_path)
    commands = "\n".join(
        [
            '{"op":"add","type":"module","name":"app"}',
            '{"op":"add","type":"function","name":"app.main","file":"app.py","line":1}',
        ]
    )
    result = runner.invoke(main, ["batch"], input=commands)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] == 2
    assert data["errors"] == 0

    # Verify nodes exist
    result = runner.invoke(main, ["show", "app.main"])
    node = json.loads(result.output)
    assert node["name"] == "app.main"
    assert node["file"] == "app.py"


def test_batch_link(tmp_path):
    runner = _init_runner(tmp_path)
    commands = "\n".join(
        [
            '{"op":"add","type":"module","name":"a"}',
            '{"op":"add","type":"module","name":"b"}',
            '{"op":"link","source":"a","rel":"imports","target":"b"}',
        ]
    )
    result = runner.invoke(main, ["batch"], input=commands)
    data = json.loads(result.output)
    assert data["ok"] == 3

    result = runner.invoke(main, ["query", "outgoing", "a", "--rel", "imports"])
    edges = json.loads(result.output)
    assert len(edges) == 1
    assert edges[0]["target"] == "b"


def test_batch_rm(tmp_path):
    runner = _init_runner(tmp_path)
    setup = "\n".join(
        [
            '{"op":"add","type":"module","name":"app"}',
            '{"op":"add","type":"function","name":"app.main"}',
            '{"op":"link","source":"app","rel":"contains","target":"app.main"}',
        ]
    )
    runner.invoke(main, ["batch"], input=setup)

    result = runner.invoke(main, ["batch"], input='{"op":"rm","name":"app.main"}')
    data = json.loads(result.output)
    assert data["ok"] == 1

    result = runner.invoke(main, ["list"])
    nodes = json.loads(result.output)
    names = [n["name"] for n in nodes]
    assert "app.main" not in names


def test_batch_unlink(tmp_path):
    runner = _init_runner(tmp_path)
    setup = "\n".join(
        [
            '{"op":"add","type":"module","name":"a"}',
            '{"op":"add","type":"module","name":"b"}',
            '{"op":"link","source":"a","rel":"imports","target":"b"}',
        ]
    )
    runner.invoke(main, ["batch"], input=setup)

    result = runner.invoke(
        main,
        ["batch"],
        input='{"op":"unlink","source":"a","rel":"imports","target":"b"}',
    )
    data = json.loads(result.output)
    assert data["ok"] == 1


def test_batch_update(tmp_path):
    runner = _init_runner(tmp_path)
    runner.invoke(main, ["batch"], input='{"op":"add","type":"module","name":"app"}')

    result = runner.invoke(
        main,
        ["batch"],
        input='{"op":"update","name":"app","type":"package","doc":"My app","metadata":{"version":"1.0"}}',
    )
    data = json.loads(result.output)
    assert data["ok"] == 1

    result = runner.invoke(main, ["show", "app"])
    node = json.loads(result.output)
    assert node["type"] == "package"
    assert node["docstring"] == "My app"
    assert node["metadata"]["version"] == "1.0"


def test_batch_error_invalid_json(tmp_path):
    runner = _init_runner(tmp_path)
    result = runner.invoke(main, ["batch"], input="not json at all")
    data = json.loads(result.output)
    assert data["errors"] == 1
    assert "invalid JSON" in data["ops"][0]["error"]


def test_batch_error_unknown_op(tmp_path):
    runner = _init_runner(tmp_path)
    result = runner.invoke(main, ["batch"], input='{"op":"explode"}')
    data = json.loads(result.output)
    assert data["errors"] == 1
    assert "unknown op" in data["ops"][0]["error"]


def test_batch_error_missing_node(tmp_path):
    runner = _init_runner(tmp_path)
    result = runner.invoke(main, ["batch"], input='{"op":"link","source":"x","rel":"calls","target":"y"}')
    data = json.loads(result.output)
    assert data["errors"] == 1


def test_batch_mixed_success_and_errors(tmp_path):
    runner = _init_runner(tmp_path)
    commands = "\n".join(
        [
            '{"op":"add","type":"module","name":"app"}',
            '{"op":"link","source":"app","rel":"calls","target":"nonexistent"}',
            '{"op":"add","type":"function","name":"app.main"}',
        ]
    )
    result = runner.invoke(main, ["batch"], input=commands)
    data = json.loads(result.output)
    assert data["ok"] == 2
    assert data["errors"] == 1


def test_batch_empty_input(tmp_path):
    runner = _init_runner(tmp_path)
    result = runner.invoke(main, ["batch"], input="")
    data = json.loads(result.output)
    assert data["ok"] == 0
    assert data["errors"] == 0


def test_batch_blank_lines_skipped(tmp_path):
    runner = _init_runner(tmp_path)
    commands = '\n\n{"op":"add","type":"module","name":"app"}\n\n'
    result = runner.invoke(main, ["batch"], input=commands)
    data = json.loads(result.output)
    assert data["ok"] == 1
    assert data["errors"] == 0
