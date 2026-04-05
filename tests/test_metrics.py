"""Tests for AST-based metrics computation."""

import pytest

try:
    import tree_sitter_python as tsp
    from tree_sitter import Language, Parser

    PY_LANG = Language(tsp.language())
    PY_PARSER = Parser(PY_LANG)
    HAS_PYTHON = True
except ImportError:
    HAS_PYTHON = False

try:
    import tree_sitter_typescript as tsts
    from tree_sitter import Language, Parser

    TS_LANG = Language(tsts.language_typescript())
    TS_PARSER = Parser(TS_LANG)
    HAS_TS = True
except ImportError:
    HAS_TS = False

needs_python = pytest.mark.skipif(not HAS_PYTHON, reason="tree-sitter-python not installed")
needs_ts = pytest.mark.skipif(not HAS_TS, reason="tree-sitter-typescript not installed")


def _py_func_node(code: str):
    """Parse Python code and return the first function_definition node."""
    tree = PY_PARSER.parse(code.encode())
    for child in tree.root_node.children:
        if child.type == "function_definition":
            return child
    raise ValueError("No function_definition found")


def _ts_func_node(code: str):
    """Parse TypeScript code and return the first function_declaration node."""
    tree = TS_PARSER.parse(code.encode())
    for child in tree.root_node.children:
        if child.type == "function_declaration":
            return child
    raise ValueError("No function_declaration found")


# --- Python metrics ---


@needs_python
def test_py_simple_function():
    from smg.metrics import PYTHON_BRANCH_MAP, compute_metrics

    node = _py_func_node("def f():\n    pass\n")
    m = compute_metrics(node, PYTHON_BRANCH_MAP)
    assert m.cyclomatic_complexity == 1
    assert m.cognitive_complexity == 0
    assert m.max_nesting_depth == 0
    assert m.lines_of_code == 2
    assert m.parameter_count == 0
    assert m.return_count == 0


@needs_python
def test_py_if_else():
    from smg.metrics import PYTHON_BRANCH_MAP, compute_metrics

    node = _py_func_node("""\
def f(x):
    if x > 0:
        return 1
    else:
        return -1
""")
    m = compute_metrics(node, PYTHON_BRANCH_MAP)
    assert m.cyclomatic_complexity == 2  # 1 + 1 if
    assert m.return_count == 2


@needs_python
def test_py_if_elif_else():
    from smg.metrics import PYTHON_BRANCH_MAP, compute_metrics

    node = _py_func_node("""\
def f(x):
    if x > 0:
        pass
    elif x == 0:
        pass
    else:
        pass
""")
    m = compute_metrics(node, PYTHON_BRANCH_MAP)
    assert m.cyclomatic_complexity == 3  # 1 + if + elif


@needs_python
def test_py_nested_loops():
    from smg.metrics import PYTHON_BRANCH_MAP, compute_metrics

    node = _py_func_node("""\
def f(matrix):
    for row in matrix:
        for cell in row:
            if cell > 0:
                pass
""")
    m = compute_metrics(node, PYTHON_BRANCH_MAP)
    assert m.cyclomatic_complexity == 4  # 1 + for + for + if
    assert m.max_nesting_depth == 3  # for > for > if
    # Cognitive: for(+1) + for(+1+1nesting) + if(+1+2nesting) = 1+2+3 = 6
    assert m.cognitive_complexity == 6


@needs_python
def test_py_boolean_operators():
    from smg.metrics import PYTHON_BRANCH_MAP, compute_metrics

    node = _py_func_node("""\
def f(x, y):
    if x > 0 and y > 0:
        pass
""")
    m = compute_metrics(node, PYTHON_BRANCH_MAP)
    assert m.cyclomatic_complexity == 3  # 1 + if + and


@needs_python
def test_py_try_except():
    from smg.metrics import PYTHON_BRANCH_MAP, compute_metrics

    node = _py_func_node("""\
def f():
    try:
        pass
    except ValueError:
        pass
    except TypeError:
        pass
""")
    m = compute_metrics(node, PYTHON_BRANCH_MAP)
    assert m.cyclomatic_complexity == 3  # 1 + 2 except clauses


@needs_python
def test_py_parameters():
    from smg.metrics import PYTHON_BRANCH_MAP, compute_metrics

    node = _py_func_node("def f(a, b, c=1, *args, **kwargs):\n    pass\n")
    m = compute_metrics(node, PYTHON_BRANCH_MAP)
    assert m.parameter_count == 5


@needs_python
def test_py_lines_of_code():
    from smg.metrics import PYTHON_BRANCH_MAP, compute_metrics

    node = _py_func_node("""\
def f():
    x = 1
    y = 2
    z = 3
    return x + y + z
""")
    m = compute_metrics(node, PYTHON_BRANCH_MAP)
    assert m.lines_of_code == 5
    assert m.return_count == 1


@needs_python
def test_py_to_dict():
    from smg.metrics import PYTHON_BRANCH_MAP, compute_metrics

    node = _py_func_node("def f():\n    pass\n")
    m = compute_metrics(node, PYTHON_BRANCH_MAP)
    d = m.to_dict()
    assert isinstance(d, dict)
    assert "cyclomatic_complexity" in d
    assert "cognitive_complexity" in d
    assert "max_nesting_depth" in d


# --- TypeScript metrics ---


@needs_ts
def test_ts_simple_function():
    from smg.metrics import JS_BRANCH_MAP, compute_metrics

    node = _ts_func_node("function f(): void {}\n")
    m = compute_metrics(node, JS_BRANCH_MAP)
    assert m.cyclomatic_complexity == 1
    assert m.cognitive_complexity == 0


@needs_ts
def test_ts_if_else():
    from smg.metrics import JS_BRANCH_MAP, compute_metrics

    node = _ts_func_node("""\
function f(x: number): number {
    if (x > 0) {
        return 1;
    } else {
        return -1;
    }
}
""")
    m = compute_metrics(node, JS_BRANCH_MAP)
    assert m.cyclomatic_complexity >= 2  # if + else
    assert m.return_count == 2


@needs_ts
def test_ts_logical_operators():
    from smg.metrics import JS_BRANCH_MAP, compute_metrics

    node = _ts_func_node("""\
function f(x: number, y: number): void {
    if (x > 0 && y > 0) {
    }
}
""")
    m = compute_metrics(node, JS_BRANCH_MAP)
    assert m.cyclomatic_complexity >= 3  # 1 + if + &&


@needs_ts
def test_ts_switch():
    from smg.metrics import JS_BRANCH_MAP, compute_metrics

    node = _ts_func_node("""\
function f(x: number): string {
    switch (x) {
        case 1: return "one";
        case 2: return "two";
        default: return "other";
    }
}
""")
    m = compute_metrics(node, JS_BRANCH_MAP)
    assert m.cyclomatic_complexity >= 3  # 2 cases + default


@needs_ts
def test_ts_nested_control_flow():
    from smg.metrics import JS_BRANCH_MAP, compute_metrics

    node = _ts_func_node("""\
function f(items: number[]): void {
    for (const item of items) {
        if (item > 0) {
            while (true) {
                break;
            }
        }
    }
}
""")
    m = compute_metrics(node, JS_BRANCH_MAP)
    assert m.max_nesting_depth >= 3
    assert m.cognitive_complexity > m.cyclomatic_complexity  # nesting penalties


@needs_ts
def test_ts_ternary():
    from smg.metrics import JS_BRANCH_MAP, compute_metrics

    node = _ts_func_node("""\
function f(x: number): number {
    return x > 0 ? x : -x;
}
""")
    m = compute_metrics(node, JS_BRANCH_MAP)
    assert m.cyclomatic_complexity == 2  # 1 + ternary


# --- Integration: metrics in scanned graph ---


@needs_python
def test_scan_attaches_metrics(tmp_path):
    from smg.scan import scan_paths
    from smg.storage import init_project, load_graph

    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text("""\
def complex_func(x, y):
    if x > 0:
        for i in range(y):
            if i > 5:
                return i
    return 0
""")
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    node = graph.get_node("app.core.complex_func")
    assert node is not None
    assert "metrics" in node.metadata
    m = node.metadata["metrics"]
    assert m["cyclomatic_complexity"] >= 4
    assert m["max_nesting_depth"] >= 3
    assert m["parameter_count"] == 2
    assert m["return_count"] == 2


@needs_python
def test_scan_fan_in_fan_out(tmp_path):
    from smg.scan import scan_paths
    from smg.storage import init_project, load_graph

    root = tmp_path
    pkg = root / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").touch()
    (pkg / "core.py").write_text("""\
def a():
    pass

def b():
    a()

def c():
    a()

def d():
    b()
    c()
""")
    init_project(root)
    graph = load_graph(root)
    scan_paths(graph, root, [root / "src"])

    # a() is called by b and c -> fan_in=2
    a = graph.get_node("app.core.a")
    assert a.metadata["metrics"]["fan_in"] == 2
    assert a.metadata["metrics"]["fan_out"] == 0

    # d() calls b and c -> fan_out=2
    d = graph.get_node("app.core.d")
    assert d.metadata["metrics"]["fan_out"] == 2
