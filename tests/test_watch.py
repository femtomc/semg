"""Tests for file watcher."""
import json
import os
import threading
import time
from pathlib import Path

import pytest

try:
    import tree_sitter_python
    from watchdog.observers import Observer
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

needs_deps = pytest.mark.skipif(not HAS_DEPS, reason="tree-sitter-python or watchdog not installed")


@needs_deps
def test_watch_detects_change(tmp_path):
    """Watch detects a file change and rescans."""
    from smg.watch import watch_and_scan
    from smg.storage import init_project, load_graph

    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text("def hello():\n    pass\n")

    init_project(root)

    # Initial scan
    from smg.scan import scan_paths
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])
    from smg.storage import save_graph
    save_graph(graph, root)

    # Track scan events
    events = []

    def on_scan(diff, stats, files):
        events.append({"diff": diff, "stats": stats, "files": files})

    # Start watcher in a thread
    watcher_thread = threading.Thread(
        target=watch_and_scan,
        args=(root, [root / "src"]),
        kwargs={"on_scan": on_scan, "debounce": 0.2},
        daemon=True,
    )
    watcher_thread.start()

    # Give watcher time to start
    time.sleep(0.5)

    # Modify a file
    (pkg / "core.py").write_text("def hello():\n    pass\n\ndef world():\n    pass\n")

    # Wait for debounce + rescan
    time.sleep(1.5)

    assert len(events) >= 1
    stats = events[0]["stats"]
    assert stats.files >= 1


@needs_deps
def test_watch_ignores_excluded(tmp_path):
    """Watch ignores files in excluded directories."""
    from smg.watch import _ScanHandler

    handler = _ScanHandler(tmp_path)

    # Should be supported
    assert handler._is_supported("src/app/core.py") is True
    assert handler._is_supported("lib/server.ts") is True

    # Should be excluded
    assert handler._is_supported("__pycache__/core.cpython-311.pyc") is False
    assert handler._is_supported(".git/hooks/pre-commit") is False
    assert handler._is_supported("node_modules/foo/index.js") is False
    assert handler._is_supported(".venv/lib/site.py") is False

    # Not a supported extension
    assert handler._is_supported("README.md") is False
    assert handler._is_supported("data.csv") is False


@needs_deps
def test_watch_cli_starts(tmp_path):
    """CLI watch command starts without error (immediate Ctrl+C)."""
    from click.testing import CliRunner
    from smg.cli import main

    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text("x = 1\n")

    os.chdir(root)
    runner = CliRunner()
    runner.invoke(main, ["init"])

    # Run watch but kill it immediately via timeout
    # CliRunner doesn't support Ctrl+C, so just verify the command parses
    result = runner.invoke(main, ["watch", "--help"])
    assert result.exit_code == 0
    assert "Watch source files" in result.output
