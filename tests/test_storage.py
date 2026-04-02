from pathlib import Path

from smg.graph import SemGraph
from smg.model import Edge, Node, NodeType, RelType
from smg.storage import find_root, init_project, load_graph, save_graph


def test_init_project(tmp_path: Path):
    root = init_project(tmp_path)
    assert (root / ".smg").is_dir()
    assert (root / ".smg" / "graph.jsonl").exists()


def test_init_project_idempotent(tmp_path: Path):
    init_project(tmp_path)
    init_project(tmp_path)  # should not fail
    assert (tmp_path / ".smg" / "graph.jsonl").exists()


def test_save_and_load(tmp_path: Path):
    init_project(tmp_path)
    g = SemGraph()
    g.add_node(Node(name="app", type=NodeType.MODULE, file="app.py"))
    g.add_node(Node(name="app.main", type=NodeType.FUNCTION, line=1))
    g.add_edge(Edge(source="app", target="app.main", rel=RelType.CONTAINS))
    save_graph(g, tmp_path)

    loaded = load_graph(tmp_path)
    assert len(loaded) == 2
    assert len(loaded.all_edges()) == 1
    assert loaded.get_node("app").file == "app.py"
    assert loaded.get_node("app.main").line == 1


def test_save_load_with_metadata(tmp_path: Path):
    init_project(tmp_path)
    g = SemGraph()
    g.add_node(Node(name="x", type=NodeType.FUNCTION, metadata={"async": True}))
    save_graph(g, tmp_path)
    loaded = load_graph(tmp_path)
    assert loaded.get_node("x").metadata == {"async": True}


def test_find_root(tmp_path: Path):
    init_project(tmp_path)
    subdir = tmp_path / "src" / "pkg"
    subdir.mkdir(parents=True)
    found = find_root(subdir)
    assert found == tmp_path.resolve()


def test_find_root_not_found(tmp_path: Path):
    found = find_root(tmp_path)
    assert found is None


def test_load_empty_graph(tmp_path: Path):
    init_project(tmp_path)
    g = load_graph(tmp_path)
    assert len(g) == 0
    assert len(g.all_edges()) == 0


def test_save_deterministic(tmp_path: Path):
    init_project(tmp_path)
    g = SemGraph()
    # Add in reverse order
    g.add_node(Node(name="z", type=NodeType.MODULE))
    g.add_node(Node(name="a", type=NodeType.MODULE))
    g.add_edge(Edge(source="z", target="a", rel=RelType.IMPORTS))
    save_graph(g, tmp_path)

    content = (tmp_path / ".smg" / "graph.jsonl").read_text()
    lines = content.strip().split("\n")
    # Nodes sorted by name, then edges
    assert '"name":"a"' in lines[0]
    assert '"name":"z"' in lines[1]
    assert '"kind":"edge"' in lines[2]
