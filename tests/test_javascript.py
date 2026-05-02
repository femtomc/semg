"""Tests for JavaScript and TypeScript extraction."""

import json

import pytest

from smg.model import NodeType, RelType
from smg.scan import file_to_module_name, scan_paths
from smg.storage import init_project, load_graph

try:
    import tree_sitter_javascript

    HAS_JS = True
except ImportError:
    HAS_JS = False

try:
    import tree_sitter_typescript

    HAS_TS = True
except ImportError:
    HAS_TS = False

needs_js = pytest.mark.skipif(not HAS_JS, reason="tree-sitter-javascript not installed")
needs_ts = pytest.mark.skipif(not HAS_TS, reason="tree-sitter-typescript not installed")


# --- Name resolution ---


def test_file_to_module_name_ts(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "src" / "app").mkdir(parents=True)
    assert file_to_module_name("src/app/server.ts", tmp_path) == "app.server"


def test_file_to_module_name_index_ts(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "src" / "app").mkdir(parents=True)
    assert file_to_module_name("src/app/index.ts", tmp_path) == "app"


def test_file_to_module_name_js(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "src" / "lib").mkdir(parents=True)
    assert file_to_module_name("src/lib/utils.js", tmp_path) == "lib.utils"


def test_file_to_module_name_jsx(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "src" / "components").mkdir(parents=True)
    assert file_to_module_name("src/components/Button.jsx", tmp_path) == "components.Button"


def test_file_to_module_name_tsx(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "src" / "components").mkdir(parents=True)
    assert file_to_module_name("src/components/Button.tsx", tmp_path) == "components.Button"


def test_file_to_module_name_workspace_package(tmp_path):
    package_root = tmp_path / "packages" / "core"
    package_root.mkdir(parents=True)
    (package_root / "package.json").write_text(json.dumps({"name": "@repo/core"}))
    (package_root / "src" / "lib").mkdir(parents=True)
    assert file_to_module_name("packages/core/src/lib/util.ts", tmp_path) == "repo.core.lib.util"


# --- TypeScript extraction ---


def _write_ts_project(tmp_path):
    root = tmp_path
    (root / "tsconfig.json").write_text("{}")
    app = root / "src" / "app"
    app.mkdir(parents=True)
    (app / "index.ts").write_text('export { Server } from "./server";\n')
    (app / "server.ts").write_text("""\
import { helper } from "./utils";

interface Config {
  port: number;
}

export class Server {
  constructor(config: Config) {}

  start(): void {
    this.listen();
    helper();
  }

  private listen(): void {}
}

export class Client extends Server {
  fetch(url: string): void {
    this.start();
  }
}
""")
    (app / "utils.ts").write_text("""\
/** Format a name. */
export function helper(): string {
  return formatName("test");
}

function formatName(name: string): string {
  return name.trim();
}

const MAX_RETRIES = 3;
""")
    return root


@needs_ts
def test_ts_scan_files(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    stats = scan_paths(graph, root, [root / "src"])
    assert stats.files == 3


@needs_ts
def test_ts_classes(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    server = graph.get_node("app.server.Server")
    assert server is not None
    assert server.type == NodeType.CLASS

    client = graph.get_node("app.server.Client")
    assert client is not None
    assert client.type == NodeType.CLASS


@needs_ts
def test_ts_interfaces(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    config = graph.get_node("app.server.Config")
    assert config is not None
    assert config.type == NodeType.INTERFACE


@needs_ts
def test_ts_methods(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    start = graph.get_node("app.server.Server.start")
    assert start is not None
    assert start.type == NodeType.METHOD


@needs_ts
def test_ts_functions(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    helper = graph.get_node("app.utils.helper")
    assert helper is not None
    assert helper.type == NodeType.FUNCTION


@needs_ts
def test_ts_arrow_and_function_expression_exports(tmp_path):
    root = tmp_path
    (root / "tsconfig.json").write_text("{}")
    app = root / "src" / "app"
    app.mkdir(parents=True)
    (app / "handlers.ts").write_text("""\
function helper(): string {
  return "ok";
}

const localHandler = () => helper();
export const exportedHandler = function (): string {
  return helper();
};
export default () => helper();
""")
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    for name in ["app.handlers.localHandler", "app.handlers.exportedHandler", "app.handlers.default"]:
        node = graph.get_node(name)
        assert node is not None
        assert node.type == NodeType.FUNCTION
        targets = {edge.target for edge in graph.outgoing(name, rel=RelType.CALLS)}
        assert "app.handlers.helper" in targets


@needs_ts
def test_ts_constants(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    const = graph.get_node("app.utils.MAX_RETRIES")
    assert const is not None
    assert const.type == NodeType.CONSTANT


@needs_ts
def test_ts_containment(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    # Module contains class
    edges = graph.outgoing("app.server", rel=RelType.CONTAINS)
    targets = {e.target for e in edges}
    assert "app.server.Server" in targets
    assert "app.server.Client" in targets
    assert "app.server.Config" in targets

    # Class contains methods
    edges = graph.outgoing("app.server.Server", rel=RelType.CONTAINS)
    targets = {e.target for e in edges}
    assert "app.server.Server.start" in targets
    assert "app.server.Server.listen" in targets
    assert "app.server.Server.constructor" in targets


@needs_ts
def test_ts_inheritance(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.server.Client", rel=RelType.INHERITS)
    assert len(edges) == 1
    assert edges[0].target == "app.server.Server"


@needs_ts
def test_ts_calls_this_method(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.server.Server.start", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    assert "app.server.Server.listen" in targets


@needs_ts
def test_ts_calls_cross_module(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    # Server.start calls helper() which resolves to app.utils.helper
    edges = graph.outgoing("app.server.Server.start", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    assert "app.utils.helper" in targets


@needs_ts
def test_ts_calls_within_module(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    # helper calls formatName
    edges = graph.outgoing("app.utils.helper", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    assert "app.utils.formatName" in targets


@needs_ts
def test_ts_package_hierarchy(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    # index.ts creates app as a package
    app = graph.get_node("app")
    assert app is not None
    assert app.type == NodeType.PACKAGE

    edges = graph.outgoing("app", rel=RelType.CONTAINS)
    targets = {e.target for e in edges}
    assert "app.server" in targets
    assert "app.utils" in targets


@needs_ts
def test_ts_imports(tmp_path):
    root = _write_ts_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    edges = graph.outgoing("app.server", rel=RelType.IMPORTS)
    targets = {e.target for e in edges}
    assert "app.utils" in targets


def _write_ts_workspace(tmp_path):
    root = tmp_path
    (root / "package.json").write_text(json.dumps({"private": True, "workspaces": ["packages/*"]}))

    core = root / "packages" / "core"
    (core / "src").mkdir(parents=True)
    (core / "package.json").write_text(json.dumps({"name": "@repo/core"}))
    (core / "src" / "utils.ts").write_text("""\
export function helper(name: string): string {
  return name.trim();
}
""")

    app = root / "packages" / "app"
    (app / "src").mkdir(parents=True)
    (app / "package.json").write_text(json.dumps({"name": "@repo/app"}))
    (app / "src" / "index.ts").write_text("""\
import { helper } from "@repo/core/utils";

export function run(name: string): string {
  return helper(name);
}
""")
    return root


@needs_ts
def test_ts_workspace_package_names_and_imports(tmp_path):
    root = _write_ts_workspace(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "packages"])

    assert graph.get_node("repo.app") is not None
    assert graph.get_node("repo.core.utils") is not None

    import_targets = {e.target for e in graph.outgoing("repo.app", rel=RelType.IMPORTS)}
    assert "repo.core.utils" in import_targets

    call_targets = {e.target for e in graph.outgoing("repo.app.run", rel=RelType.CALLS)}
    assert "repo.core.utils.helper" in call_targets


# --- JavaScript extraction ---


def _write_js_project(tmp_path):
    root = tmp_path
    (root / "package.json").write_text("{}")
    lib = root / "src" / "lib"
    lib.mkdir(parents=True)
    (lib / "index.js").write_text('module.exports = require("./server");\n')
    (lib / "server.js").write_text("""\
class Server {
  constructor(port) {
    this.port = port;
  }
  start() {
    this.listen();
  }
  listen() {}
}

function createServer(port) {
  return new Server(port);
}

module.exports = { Server, createServer };
""")
    return root


@needs_js
def test_js_scan(tmp_path):
    root = _write_js_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    stats = scan_paths(graph, root, [root / "src"])
    assert stats.files == 2
    assert graph.get_node("lib.server.Server") is not None
    assert graph.get_node("lib.server.createServer") is not None


@needs_js
def test_js_methods(tmp_path):
    root = _write_js_project(tmp_path)
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    start = graph.get_node("lib.server.Server.start")
    assert start is not None
    assert start.type == NodeType.METHOD

    # this.listen() resolved
    edges = graph.outgoing("lib.server.Server.start", rel=RelType.CALLS)
    targets = {e.target for e in edges}
    assert "lib.server.Server.listen" in targets


@needs_js
def test_js_idempotent(tmp_path):
    root = _write_js_project(tmp_path)
    init_project(root)
    graph = load_graph(root)

    scan_paths(graph, root, [root / "src"])
    count1 = len(graph.nodes)
    edges1 = len(graph.all_edges())

    scan_paths(graph, root, [root / "src"])
    assert len(graph.nodes) == count1
    assert len(graph.all_edges()) == edges1
