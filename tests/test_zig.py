"""Tests for Zig language extraction."""
import json
import os
from pathlib import Path

import pytest

from smg.graph import SemGraph
from smg.model import NodeType, RelType
from smg.scan import file_to_module_name, scan_paths
from smg.storage import init_project, load_graph

try:
    import tree_sitter_zig
    HAS_ZIG = True
except ImportError:
    HAS_ZIG = False

needs_zig = pytest.mark.skipif(not HAS_ZIG, reason="tree-sitter-zig not installed")


def _write_zig_project(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.zig").write_text("""\
const std = @import("std");
const server = @import("server");

pub fn main() !void {
    var s = server.Server.init(std.heap.page_allocator);
    try s.start();
}

const MAX_CONNECTIONS: usize = 100;
""")
    (src / "server.zig").write_text("""\
const std = @import("std");

pub const Config = struct {
    port: u16,
    host: []const u8,
};

pub const Server = struct {
    allocator: std.mem.Allocator,

    pub fn init(allocator: std.mem.Allocator) Server {
        return Server{ .allocator = allocator };
    }

    pub fn start(self: *Server) !void {
        self.listen();
    }

    fn listen(self: *Server) void {
        if (true) {
            for (0..10) |i| {
                _ = i;
            }
        }
    }
};

fn helper() u32 {
    return 42;
}

test "server init" {
    const s = Server.init(std.testing.allocator);
    _ = s;
}
""")
    return tmp_path


def test_file_to_module_name_zig(tmp_path):
    assert file_to_module_name("src/server.zig", tmp_path) == "src.server"


@needs_zig
def test_zig_scan_files(tmp_path):
    root = _write_zig_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    stats = scan_paths(graph, root, [root / "src"])
    assert stats.files == 2


@needs_zig
def test_zig_structs(tmp_path):
    root = _write_zig_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    server = graph.get_node("src.server.Server")
    assert server is not None
    assert server.type == NodeType.CLASS

    config = graph.get_node("src.server.Config")
    assert config is not None
    assert config.type == NodeType.CLASS


@needs_zig
def test_zig_functions(tmp_path):
    root = _write_zig_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    main = graph.get_node("src.main.main")
    assert main is not None
    assert main.type == NodeType.FUNCTION

    helper = graph.get_node("src.server.helper")
    assert helper is not None
    assert helper.type == NodeType.FUNCTION


@needs_zig
def test_zig_methods(tmp_path):
    root = _write_zig_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    start = graph.get_node("src.server.Server.start")
    assert start is not None
    assert start.type == NodeType.METHOD

    listen = graph.get_node("src.server.Server.listen")
    assert listen is not None
    assert listen.type == NodeType.METHOD

    # init has no self param -> function
    init = graph.get_node("src.server.Server.init")
    assert init is not None
    assert init.type == NodeType.FUNCTION


@needs_zig
def test_zig_constants(tmp_path):
    root = _write_zig_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    c = graph.get_node("src.main.MAX_CONNECTIONS")
    assert c is not None
    assert c.type == NodeType.CONSTANT


@needs_zig
def test_zig_containment(tmp_path):
    root = _write_zig_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("src.server.Server", rel=RelType.CONTAINS)
    targets = {e.target for e in edges}
    assert "src.server.Server.init" in targets
    assert "src.server.Server.start" in targets
    assert "src.server.Server.listen" in targets


@needs_zig
def test_zig_calls_self(tmp_path):
    root = _write_zig_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("src.server.Server.start", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    assert "src.server.Server.listen" in targets


@needs_zig
def test_zig_tests_extracted(tmp_path):
    root = _write_zig_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    test_node = graph.get_node("src.server.test_server_init")
    assert test_node is not None
    assert test_node.type == NodeType.FUNCTION
    assert test_node.metadata.get("test") is True


@needs_zig
def test_zig_metrics(tmp_path):
    root = _write_zig_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    listen = graph.get_node("src.server.Server.listen")
    assert "metrics" in listen.metadata
    m = listen.metadata["metrics"]
    assert m["cyclomatic_complexity"] >= 3  # if + for
    assert m["max_nesting_depth"] >= 2


@needs_zig
def test_zig_idempotent(tmp_path):
    root = _write_zig_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])
    count1 = len(graph.nodes)
    edges1 = len(graph.all_edges())
    scan_paths(graph, root, [root / "src"])
    assert len(graph.nodes) == count1
    assert len(graph.all_edges()) == edges1
