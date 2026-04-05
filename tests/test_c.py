"""Tests for C and C++ extraction."""

import pytest

from smg.model import NodeType, RelType
from smg.scan import scan_paths
from smg.storage import init_project, load_graph

try:
    import tree_sitter_c

    HAS_C = True
except ImportError:
    HAS_C = False

try:
    import tree_sitter_cpp

    HAS_CPP = True
except ImportError:
    HAS_CPP = False

needs_c = pytest.mark.skipif(not HAS_C, reason="tree-sitter-c not installed")
needs_cpp = pytest.mark.skipif(not HAS_CPP, reason="tree-sitter-cpp not installed")


# --- C tests ---


def _write_c_project(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "server.h").write_text("""\
#ifndef SERVER_H
#define SERVER_H

#define MAX_CONN 100

typedef struct {
    int port;
} Config;

typedef struct Server {
    Config config;
} Server;

Server* server_init(Config config);
void server_start(Server* self);

#endif
""")
    (src / "server.c").write_text("""\
#include "server.h"
#include <stdlib.h>

Server* server_init(Config config) {
    Server* s = malloc(sizeof(Server));
    s->config = config;
    return s;
}

void server_start(Server* self) {
    server_listen(self);
}

static void server_listen(Server* self) {
    if (self->config.port > 0) {
        for (int i = 0; i < 10; i++) {
            if (i > 5) break;
        }
    }
}
""")
    (src / "main.c").write_text("""\
#include "server.h"

int main(void) {
    Config cfg = {8080};
    Server* s = server_init(cfg);
    server_start(s);
    return 0;
}
""")
    return tmp_path


@needs_c
def test_c_scan_files(tmp_path):
    root = _write_c_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    stats = scan_paths(graph, root, [root / "src"])
    assert stats.files == 3


@needs_c
def test_c_functions(tmp_path):
    root = _write_c_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    init = graph.get_node("src.server.server_init")
    assert init is not None
    assert init.type == NodeType.FUNCTION

    start = graph.get_node("src.server.server_start")
    assert start is not None


@needs_c
def test_c_structs(tmp_path):
    root = _write_c_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    config = graph.get_node("src.server.Config")
    assert config is not None
    assert config.type == NodeType.CLASS

    server = graph.get_node("src.server.Server")
    assert server is not None
    assert server.type == NodeType.CLASS


@needs_c
def test_c_constants(tmp_path):
    root = _write_c_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    c = graph.get_node("src.server.MAX_CONN")
    assert c is not None
    assert c.type == NodeType.CONSTANT


@needs_c
def test_c_calls(tmp_path):
    root = _write_c_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("src.server.server_start", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    assert "src.server.server_listen" in targets


@needs_c
def test_c_includes(tmp_path):
    root = _write_c_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("src.main", rel=RelType.IMPORTS)
    targets = {e.target for e in edges}
    assert "src.server" in targets


@needs_c
def test_c_metrics(tmp_path):
    root = _write_c_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    listen = graph.get_node("src.server.server_listen")
    assert "metrics" in listen.metadata
    m = listen.metadata["metrics"]
    assert m["cyclomatic_complexity"] >= 3


@needs_c
def test_c_idempotent(tmp_path):
    root = _write_c_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])
    count1 = len(graph.nodes)
    edges1 = len(graph.all_edges())
    scan_paths(graph, root, [root / "src"])
    assert len(graph.nodes) == count1
    assert len(graph.all_edges()) == edges1


# --- C++ tests ---


def _write_cpp_project(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.cpp").write_text("""\
namespace app {

class Base {
public:
    virtual void run() = 0;
};

class Server : public Base {
    int port_;
public:
    Server(int port) : port_(port) {}

    void run() override {
        listen();
    }

    void listen() {
        if (port_ > 0) {
            for (int i = 0; i < 10; i++) {}
        }
    }
};

int helper() { return 42; }

}
""")
    return tmp_path


@needs_cpp
def test_cpp_scan(tmp_path):
    root = _write_cpp_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    stats = scan_paths(graph, root, [root / "src"])
    assert stats.files == 1


@needs_cpp
def test_cpp_classes(tmp_path):
    root = _write_cpp_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    base = graph.get_node("src.app.app.Base")
    assert base is not None
    assert base.type == NodeType.CLASS

    server = graph.get_node("src.app.app.Server")
    assert server is not None
    assert server.type == NodeType.CLASS


@needs_cpp
def test_cpp_inheritance(tmp_path):
    root = _write_cpp_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("src.app.app.Server", rel=RelType.INHERITS)
    targets = {e.target for e in edges}
    assert "src.app.app.Base" in targets


@needs_cpp
def test_cpp_methods(tmp_path):
    root = _write_cpp_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    run = graph.get_node("src.app.app.Server.run")
    assert run is not None
    assert run.type == NodeType.METHOD

    listen = graph.get_node("src.app.app.Server.listen")
    assert listen is not None


@needs_cpp
def test_cpp_namespace(tmp_path):
    root = _write_cpp_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    ns = graph.get_node("src.app.app")
    assert ns is not None
    assert ns.type == NodeType.PACKAGE

    edges = graph.outgoing("src.app.app", rel=RelType.CONTAINS)
    targets = {e.target for e in edges}
    assert "src.app.app.Base" in targets
    assert "src.app.app.Server" in targets
    assert "src.app.app.helper" in targets


@needs_cpp
def test_cpp_calls(tmp_path):
    root = _write_cpp_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("src.app.app.Server.run", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    # listen() is called — should resolve to Server.listen or just "listen"
    assert len(targets) >= 1


@needs_cpp
def test_cpp_metrics(tmp_path):
    root = _write_cpp_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    listen = graph.get_node("src.app.app.Server.listen")
    assert "metrics" in listen.metadata
    m = listen.metadata["metrics"]
    assert m["cyclomatic_complexity"] >= 2


@needs_cpp
def test_cpp_idempotent(tmp_path):
    root = _write_cpp_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])
    count1 = len(graph.nodes)
    scan_paths(graph, root, [root / "src"])
    assert len(graph.nodes) == count1
