"""Skipped-edge budgets for small first-party programs in each extractor."""

from __future__ import annotations

import importlib.util

import pytest

from smg.model import RelType
from smg.scan import scan_paths
from smg.storage import init_project, load_graph

HAS_PYTHON = importlib.util.find_spec("tree_sitter_python") is not None
HAS_JAVASCRIPT = importlib.util.find_spec("tree_sitter_javascript") is not None
HAS_TYPESCRIPT = importlib.util.find_spec("tree_sitter_typescript") is not None
HAS_ZIG = importlib.util.find_spec("tree_sitter_zig") is not None
HAS_C = importlib.util.find_spec("tree_sitter_c") is not None
HAS_CPP = importlib.util.find_spec("tree_sitter_cpp") is not None


@pytest.mark.parametrize(
    ("files", "language", "caller", "callee"),
    [
        pytest.param(
            {
                "app/__init__.py": "",
                "app/core.py": """\
def helper():
    pass

def main():
    helper()
""",
            },
            "Python",
            "app.core.main",
            "app.core.helper",
            marks=pytest.mark.skipif(not HAS_PYTHON, reason="tree-sitter-python not installed"),
            id="python",
        ),
        pytest.param(
            {
                "app.js": """\
function helper() {}
function main() { helper(); }
""",
            },
            "JavaScript",
            "src.app.main",
            "src.app.helper",
            marks=pytest.mark.skipif(not HAS_JAVASCRIPT, reason="tree-sitter-javascript not installed"),
            id="javascript",
        ),
        pytest.param(
            {
                "app.ts": """\
function helper(): void {}
export function main(): void { helper(); }
""",
            },
            "TypeScript",
            "src.app.main",
            "src.app.helper",
            marks=pytest.mark.skipif(not HAS_TYPESCRIPT, reason="tree-sitter-typescript not installed"),
            id="typescript",
        ),
        pytest.param(
            {
                "app.zig": """\
pub fn helper() void {}
pub fn main() void { helper(); }
""",
            },
            "Zig",
            "src.app.main",
            "src.app.helper",
            marks=pytest.mark.skipif(not HAS_ZIG, reason="tree-sitter-zig not installed"),
            id="zig",
        ),
        pytest.param(
            {
                "app.c": """\
static void helper(void) {}
void main_fn(void) { helper(); }
""",
            },
            "C",
            "src.app.main_fn",
            "src.app.helper",
            marks=pytest.mark.skipif(not HAS_C, reason="tree-sitter-c not installed"),
            id="c",
        ),
        pytest.param(
            {
                "app.cpp": """\
namespace util { void helper(); }
void util::helper() {}
int main() { util::helper(); }
""",
            },
            "Cpp",
            "src.app.main",
            "src.app.util.helper",
            marks=pytest.mark.skipif(not HAS_CPP, reason="tree-sitter-cpp not installed"),
            id="cpp",
        ),
    ],
)
def test_first_party_programs_have_zero_skipped_edges(tmp_path, files, language, caller, callee):
    src = tmp_path / "src"
    src.mkdir()
    for rel_path, source in files.items():
        path = src / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)

    init_project(tmp_path)
    graph = load_graph(tmp_path)
    stats = scan_paths(graph, tmp_path, [src])

    assert stats.lang_counts[language] == len(files)
    assert stats.skipped_edges == 0
    assert stats.skipped_edge_categories == {}

    targets = {edge.target for edge in graph.outgoing(caller, rel=RelType.CALLS)}
    assert callee in targets
