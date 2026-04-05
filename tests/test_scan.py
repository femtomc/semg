import json
import os
from pathlib import Path

import pytest

from smg.graph import SemGraph
from smg.model import Node, NodeType, RelType
from smg.scan import collect_files, file_to_module_name, scan_paths
from smg.storage import init_project, load_graph, save_graph


# --- Name resolution tests (no tree-sitter needed) ---


def test_file_to_module_name_simple(tmp_path):
    (tmp_path / "app.py").touch()
    assert file_to_module_name("app.py", tmp_path) == "app"


def test_file_to_module_name_nested(tmp_path):
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    assert file_to_module_name("pkg/sub/mod.py", tmp_path) == "pkg.sub.mod"


def test_file_to_module_name_init(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").touch()
    assert file_to_module_name("pkg/__init__.py", tmp_path) == "pkg"


def test_file_to_module_name_src_layout(tmp_path):
    (tmp_path / "src" / "mylib").mkdir(parents=True)
    (tmp_path / "src" / "mylib" / "__init__.py").touch()
    assert file_to_module_name("src/mylib/core.py", tmp_path) == "mylib.core"
    assert file_to_module_name("src/mylib/__init__.py", tmp_path) == "mylib"


def test_file_to_module_name_src_no_init(tmp_path):
    # src/ without __init__.py -> don't strip src
    (tmp_path / "src" / "scripts").mkdir(parents=True)
    assert file_to_module_name("src/scripts/run.py", tmp_path) == "src.scripts.run"


# --- File collection tests ---


def test_collect_files_excludes(tmp_path):
    from smg.langs import load_extractors
    load_extractors()

    (tmp_path / "good.py").write_text("x = 1")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "bad.py").write_text("x = 1")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hooks.py").write_text("x = 1")

    files = collect_files([tmp_path], tmp_path)
    names = [f.name for f in files]
    assert "good.py" in names
    assert "bad.py" not in names
    assert "hooks.py" not in names


def test_collect_files_custom_exclude(tmp_path):
    from smg.langs import load_extractors
    load_extractors()

    (tmp_path / "keep.py").write_text("x = 1")
    (tmp_path / "skip.py").write_text("x = 1")

    files = collect_files([tmp_path], tmp_path, excludes=["skip.py"])
    names = [f.name for f in files]
    assert "keep.py" in names
    assert "skip.py" not in names


# --- Integration tests (require tree-sitter) ---

try:
    import tree_sitter_python
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False

needs_tree_sitter = pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter-python not installed")


def _write_python_project(tmp_path):
    """Create a small Python project for testing."""
    pkg = tmp_path / "src" / "mylib"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""My library."""\n')
    (pkg / "core.py").write_text('''\
"""Core module."""
import os
from mylib import utils


class Base:
    """Base class."""
    pass


class Engine(Base):
    """The engine."""

    def run(self):
        """Run the engine."""
        pass

    def stop(self):
        pass


def helper():
    """A helper function."""
    pass


MAX_SIZE = 100
''')
    (pkg / "utils.py").write_text('''\
"""Utility functions."""


def format_name(name: str) -> str:
    """Format a name."""
    return name.strip()
''')
    return tmp_path


@needs_tree_sitter
def test_scan_python_file(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)

    stats = scan_paths(graph, root, [root / "src"])

    assert stats.files == 3
    assert graph.get_node("mylib") is not None
    assert graph.get_node("mylib.core") is not None
    assert graph.get_node("mylib.utils") is not None


@needs_tree_sitter
def test_scan_classes(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    base = graph.get_node("mylib.core.Base")
    assert base is not None
    assert base.type == NodeType.CLASS
    assert base.docstring == "Base class."

    engine = graph.get_node("mylib.core.Engine")
    assert engine is not None
    assert engine.type == NodeType.CLASS


@needs_tree_sitter
def test_scan_methods(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    run = graph.get_node("mylib.core.Engine.run")
    assert run is not None
    assert run.type == NodeType.METHOD
    assert run.docstring == "Run the engine."

    stop = graph.get_node("mylib.core.Engine.stop")
    assert stop is not None
    assert stop.type == NodeType.METHOD


@needs_tree_sitter
def test_scan_functions(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    helper = graph.get_node("mylib.core.helper")
    assert helper is not None
    assert helper.type == NodeType.FUNCTION


@needs_tree_sitter
def test_scan_constants(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    const = graph.get_node("mylib.core.MAX_SIZE")
    assert const is not None
    assert const.type == NodeType.CONSTANT


@needs_tree_sitter
def test_scan_containment(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    # Module contains class
    edges = graph.outgoing("mylib.core", rel=RelType.CONTAINS)
    targets = {e.target for e in edges}
    assert "mylib.core.Base" in targets
    assert "mylib.core.Engine" in targets
    assert "mylib.core.helper" in targets

    # Class contains methods
    edges = graph.outgoing("mylib.core.Engine", rel=RelType.CONTAINS)
    targets = {e.target for e in edges}
    assert "mylib.core.Engine.run" in targets
    assert "mylib.core.Engine.stop" in targets


@needs_tree_sitter
def test_scan_inheritance(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    # Engine inherits Base
    edges = graph.outgoing("mylib.core.Engine", rel=RelType.INHERITS)
    assert len(edges) == 1
    assert edges[0].target == "mylib.core.Base"


@needs_tree_sitter
def test_scan_imports(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    # core imports mylib (from mylib import utils -> resolves to mylib)
    edges = graph.outgoing("mylib.core", rel=RelType.IMPORTS)
    targets = {e.target for e in edges}
    assert "mylib" in targets


@needs_tree_sitter
def test_scan_relative_imports(tmp_path):
    """from .sibling import X should resolve to parent.sibling."""
    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "errors.py").write_text("class AppError(Exception): pass\n")
    (pkg / "core.py").write_text("""\
from .errors import AppError
from . import errors

def main():
    pass
""")
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.core", rel=RelType.IMPORTS)
    targets = {e.target for e in edges}
    assert "app.errors" in targets


@needs_tree_sitter
def test_scan_relative_import_parent(tmp_path):
    """from ..utils import X should resolve to grandparent.utils."""
    root = tmp_path
    pkg = root / "src" / "app"
    sub = pkg / "sub"
    sub.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (sub / "__init__.py").touch()
    (pkg / "utils.py").write_text("def helper(): pass\n")
    (sub / "deep.py").write_text("""\
from ..utils import helper

def do_stuff():
    helper()
""")
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.sub.deep", rel=RelType.IMPORTS)
    targets = {e.target for e in edges}
    assert "app.utils" in targets


@needs_tree_sitter
def test_scan_package_hierarchy(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    # mylib is a package (from __init__.py)
    mylib = graph.get_node("mylib")
    assert mylib is not None
    assert mylib.type == NodeType.PACKAGE

    # Package contains modules
    edges = graph.outgoing("mylib", rel=RelType.CONTAINS)
    targets = {e.target for e in edges}
    assert "mylib.core" in targets
    assert "mylib.utils" in targets


@needs_tree_sitter
def test_scan_idempotent(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)

    scan_paths(graph, root, [root / "src"])
    count1 = len(graph.nodes)
    edges1 = len(graph.all_edges())

    scan_paths(graph, root, [root / "src"])
    assert len(graph.nodes) == count1
    assert len(graph.all_edges()) == edges1


@needs_tree_sitter
def test_scan_clean(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)

    scan_paths(graph, root, [root / "src"])
    assert graph.get_node("mylib.core.helper") is not None

    # Remove helper from source
    core = root / "src" / "mylib" / "core.py"
    core.write_text('''\
class Base:
    pass
''')

    scan_paths(graph, root, [root / "src"], clean=True)
    assert graph.get_node("mylib.core.helper") is None
    assert graph.get_node("mylib.core.Base") is not None


@needs_tree_sitter
def test_scan_docstrings(tmp_path):
    root = _write_python_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    fmt = graph.get_node("mylib.utils.format_name")
    assert fmt is not None
    assert fmt.docstring == "Format a name."


@needs_tree_sitter
def test_scan_cli(tmp_path):
    """End-to-end CLI test."""
    from click.testing import CliRunner
    from smg.cli import main

    root = _write_python_project(tmp_path)
    os.chdir(root)

    runner = CliRunner()
    runner.invoke(main, ["init"])
    result = runner.invoke(main, ["scan", "src/"])
    assert result.exit_code == 0
    # Auto-format: JSON in non-TTY
    data = json.loads(result.output)
    assert data["files"] == 3

    result = runner.invoke(main, ["list", "--type", "class"])
    nodes = json.loads(result.output)
    names = [n["name"] for n in nodes]
    assert "mylib.core.Base" in names
    assert "mylib.core.Engine" in names


# --- Call graph extraction tests ---


@needs_tree_sitter
def test_scan_calls_simple(tmp_path):
    """A function calling another function in the same module."""
    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text('''\
def helper():
    pass

def main():
    helper()
''')
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.core.main", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    assert "app.core.helper" in targets


@needs_tree_sitter
def test_scan_calls_self_method(tmp_path):
    """self.method() resolves to ClassName.method."""
    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "engine.py").write_text('''\
class Engine:
    def start(self):
        pass

    def run(self):
        self.start()
''')
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.engine.Engine.run", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    assert "app.engine.Engine.start" in targets


@needs_tree_sitter
def test_scan_calls_between_modules(tmp_path):
    """Cross-module call resolved via name matching."""
    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "utils.py").write_text('''\
def format_name(name):
    return name.strip()
''')
    (pkg / "core.py").write_text('''\
def process():
    format_name("hello")
''')
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.core.process", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    assert "app.utils.format_name" in targets


@needs_tree_sitter
def test_scan_calls_builtins_skipped(tmp_path):
    """Calls to builtins (print, len, etc.) are not added as edges."""
    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text('''\
def main():
    print("hello")
    x = len([1, 2, 3])
    y = range(10)
''')
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.core.main", rel=RelType.CALLS)
    assert len(edges) == 0


@needs_tree_sitter
def test_scan_calls_deduplication(tmp_path):
    """Calling the same function twice produces only one edge."""
    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text('''\
def helper():
    pass

def main():
    helper()
    helper()
    helper()
''')
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.core.main", rel=RelType.CALLS)
    call_targets = [e.target for e in edges if e.rel.value == "calls"]
    assert call_targets.count("app.core.helper") == 1


@needs_tree_sitter
def test_scan_calls_nested(tmp_path):
    """Calls inside if/for/with blocks are still extracted."""
    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text('''\
def a():
    pass

def b():
    pass

def main():
    if True:
        a()
    for x in []:
        b()
''')
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.core.main", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    assert "app.core.a" in targets
    assert "app.core.b" in targets


# --- Stale node cleanup tests ---


@needs_tree_sitter
def test_scan_clean_removes_deleted_file_nodes(tmp_path):
    """Deleting a source file and rescanning with clean=True removes its nodes."""
    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "a.py").write_text("def foo():\n    pass\n")
    (pkg / "b.py").write_text("def bar():\n    pass\n")

    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    assert graph.get_node("app.a") is not None
    assert graph.get_node("app.a.foo") is not None
    assert graph.get_node("app.b") is not None

    # Delete a.py and rescan
    (pkg / "a.py").unlink()
    scan_paths(graph, root, [root / "src"], clean=True)

    assert graph.get_node("app.a") is None
    assert graph.get_node("app.a.foo") is None
    # b.py nodes should survive
    assert graph.get_node("app.b") is not None
    assert graph.get_node("app.b.bar") is not None


# --- .smgignore path pattern tests ---


def test_collect_files_path_pattern(tmp_path):
    """Patterns containing / match against relative paths, not just basenames."""
    from smg.langs import load_extractors
    load_extractors()

    gen = tmp_path / "src" / "generated"
    gen.mkdir(parents=True)
    (gen / "skip.py").write_text("x = 1")

    keep_dir = tmp_path / "src" / "app"
    keep_dir.mkdir(parents=True)
    (keep_dir / "keep.py").write_text("x = 1")

    # Write .smgignore with a path pattern
    (tmp_path / ".smgignore").write_text("src/generated/*\n")

    files = collect_files([tmp_path], tmp_path)
    names = [f.name for f in files]
    assert "keep.py" in names
    assert "skip.py" not in names


def test_collect_files_basename_pattern_still_works(tmp_path):
    """Basename-only patterns (no /) still match anywhere in the tree."""
    from smg.langs import load_extractors
    load_extractors()

    (tmp_path / "good.py").write_text("x = 1")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "bad_generated.py").write_text("x = 1")

    (tmp_path / ".smgignore").write_text("bad_generated.py\n")

    files = collect_files([tmp_path], tmp_path)
    names = [f.name for f in files]
    assert "good.py" in names
    assert "bad_generated.py" not in names
