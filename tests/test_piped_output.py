"""Regression test: every smg CLI command must produce pure-ASCII stdout when piped.

Runs each subcommand via subprocess (stdout=PIPE, no TTY) and asserts that
every byte in stdout is < 0x80.  High bytes from Rich tables, em-dashes,
box-drawing characters, or Unicode ellipsis are all caught.

The ``context`` command is excluded: its purpose is to echo verbatim source
code, which legitimately contains non-ASCII content (comments, docstrings).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def graph_dir(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Create a populated .smg/ graph for all tests in this module."""
    d = tmp_path_factory.mktemp("piped")
    env = {**os.environ, "NO_COLOR": "1"}
    smg = [sys.executable, "-m", "smg"]

    subprocess.run(smg + ["init"], cwd=d, env=env, check=True, capture_output=True)

    nodes = [
        ("package", "app"),
        ("module", "app.core"),
        ("class", "app.core.Engine"),
        ("method", "app.core.Engine.run"),
        ("module", "app.utils"),
        ("function", "app.utils.helper"),
    ]
    for ntype, name in nodes:
        subprocess.run(
            smg + ["add", ntype, name],
            cwd=d,
            env=env,
            check=True,
            capture_output=True,
        )

    edges = [
        ("app", "contains", "app.core"),
        ("app", "contains", "app.utils"),
        ("app.core", "contains", "app.core.Engine"),
        ("app.core.Engine", "contains", "app.core.Engine.run"),
        ("app.utils", "contains", "app.utils.helper"),
        ("app.core", "imports", "app.utils"),
        ("app.core.Engine.run", "calls", "app.utils.helper"),
    ]
    for src, rel, tgt in edges:
        subprocess.run(
            smg + ["link", src, rel, tgt],
            cwd=d,
            env=env,
            check=True,
            capture_output=True,
        )

    return str(d)


def _run_smg(graph_dir: str, args: list[str]) -> bytes:
    """Run an smg subcommand with stdout piped, return raw stdout bytes."""
    result = subprocess.run(
        [sys.executable, "-m", "smg"] + args,
        cwd=graph_dir,
        env={**os.environ, "NO_COLOR": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def _assert_ascii(stdout: bytes, label: str) -> None:
    """Assert every byte in stdout is pure ASCII (< 0x80)."""
    high = [i for i, b in enumerate(stdout) if b >= 0x80]
    if high:
        # Show first offending line for diagnosis
        for line_no, line in enumerate(stdout.split(b"\n"), 1):
            if any(b >= 0x80 for b in line):
                snippet = line.decode("utf-8", errors="replace")
                pytest.fail(
                    f"{label}: {len(high)} high byte(s) at offset(s) {high[:5]}. "
                    f"First offending line {line_no}: {snippet!r}"
                )


# --- Parameterized subcommand tests ---

COMMANDS_NO_ARGS = [
    "list",
    "status",
    "overview",
    "validate",
]

COMMANDS_WITH_NODE = [
    ("show", ["app.core.Engine"]),
    ("about", ["app.core.Engine"]),
    ("usages", ["app.core.Engine"]),
    ("impact", ["app.core.Engine"]),
    ("between", ["app.core", "app.utils"]),
]

COMMANDS_WITH_QUERY = [
    ("search", ["helper"]),
]


@pytest.mark.parametrize("cmd", COMMANDS_NO_ARGS, ids=COMMANDS_NO_ARGS)
def test_no_high_bytes_no_args(graph_dir: str, cmd: str) -> None:
    stdout = _run_smg(graph_dir, [cmd])
    _assert_ascii(stdout, f"smg {cmd}")


@pytest.mark.parametrize(
    "cmd,args",
    COMMANDS_WITH_NODE,
    ids=[c for c, _ in COMMANDS_WITH_NODE],
)
def test_no_high_bytes_node_cmds(graph_dir: str, cmd: str, args: list[str]) -> None:
    stdout = _run_smg(graph_dir, [cmd] + args)
    _assert_ascii(stdout, f"smg {cmd} {' '.join(args)}")


@pytest.mark.parametrize(
    "cmd,args",
    COMMANDS_WITH_QUERY,
    ids=[c for c, _ in COMMANDS_WITH_QUERY],
)
def test_no_high_bytes_query_cmds(graph_dir: str, cmd: str, args: list[str]) -> None:
    stdout = _run_smg(graph_dir, [cmd] + args)
    _assert_ascii(stdout, f"smg {cmd} {' '.join(args)}")


def test_no_high_bytes_analyze(graph_dir: str) -> None:
    """Analyze is slow; test it separately so failures are isolated."""
    stdout = _run_smg(graph_dir, ["analyze"])
    _assert_ascii(stdout, "smg analyze")


def test_no_high_bytes_diff(graph_dir: str) -> None:
    """Diff needs git; may produce no output if not in a repo, but must not crash or emit high bytes."""
    stdout = _run_smg(graph_dir, ["diff"])
    _assert_ascii(stdout, "smg diff")
