from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from semg.graph import SemGraph
from semg.langs import ExtractResult, get_extractor, load_extractors
from semg.model import Edge, Node, NodeType, RelType

DEFAULT_EXCLUDES = [
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    "*.egg-info",
    ".semg",
]


@dataclass
class ScanStats:
    files: int = 0
    nodes_added: int = 0
    nodes_removed: int = 0
    edges_added: int = 0
    edges_removed: int = 0
    skipped_edges: int = 0
    orphaned_manual_edges: list[dict[str, str]] = field(default_factory=list)
    lang_counts: dict[str, int] = field(default_factory=dict)
    type_counts: dict[str, int] = field(default_factory=dict)


def file_to_module_name(file_path: str, root: Path) -> str:
    """Convert a file path to a qualified module name.

    Examples:
        src/semg/graph.py       -> semg.graph
        src/semg/__init__.py    -> semg
        tests/test_graph.py     -> tests.test_graph
        app.py                  -> app
    """
    p = Path(file_path)
    if p.is_absolute():
        rel = p.relative_to(root)
    else:
        rel = p
    parts = list(rel.parts)

    # Detect src-layout: if first component is "src" and there's a package underneath
    if len(parts) > 1 and parts[0] == "src":
        candidate = root / "src" / parts[1]
        if candidate.is_dir():
            # Python: __init__.py signals a package
            # JS/TS: any directory under src/ is treated as a module root
            has_py_init = (candidate / "__init__.py").exists()
            has_js_marker = (
                (root / "package.json").exists()
                or (root / "tsconfig.json").exists()
            )
            if has_py_init or has_js_marker:
                parts = parts[1:]

    # Strip known extensions from last part
    last = parts[-1]
    stripped = _strip_extension(last)
    if stripped is not None:
        # index.ts / index.js / __init__.py -> parent directory name
        if stripped in ("__init__", "index"):
            parts = parts[:-1]
        else:
            parts[-1] = stripped

    return ".".join(parts)


_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".zig")


def _strip_extension(filename: str) -> str | None:
    """Strip a known extension, returning the stem. Returns None if no match."""
    for ext in _EXTENSIONS:
        if filename.endswith(ext):
            return filename[: -len(ext)]
    return None


def collect_files(
    paths: list[Path],
    root: Path,
    excludes: list[str] | None = None,
) -> list[Path]:
    """Walk paths and collect files with registered extensions."""
    all_excludes = DEFAULT_EXCLUDES + (excludes or [])
    files: list[Path] = []

    for path in paths:
        path = path.resolve()
        if path.is_file():
            ext = path.suffix
            if get_extractor(ext) is not None:
                files.append(path)
        elif path.is_dir():
            for dirpath, dirnames, filenames in os.walk(path):
                # Prune excluded directories in-place
                dirnames[:] = [
                    d for d in dirnames
                    if not any(fnmatch.fnmatch(d, pat) for pat in all_excludes)
                ]
                for fname in filenames:
                    if any(fnmatch.fnmatch(fname, pat) for pat in all_excludes):
                        continue
                    fpath = Path(dirpath) / fname
                    ext = fpath.suffix
                    if get_extractor(ext) is not None:
                        files.append(fpath)

    return sorted(set(files))


def _ensure_package_hierarchy(graph: SemGraph, module_name: str, root: Path) -> None:
    """Ensure parent packages exist as PACKAGE nodes with CONTAINS edges."""
    parts = module_name.split(".")
    for i in range(len(parts) - 1):
        pkg_name = ".".join(parts[: i + 1])
        child_name = ".".join(parts[: i + 2])
        if graph.get_node(pkg_name) is None:
            graph.add_node(Node(name=pkg_name, type=NodeType.PACKAGE))
        # The CONTAINS edge from package to child will be added if child exists
        # We defer this to after all nodes are added


def scan_paths(
    graph: SemGraph,
    root: Path,
    paths: list[Path],
    clean: bool = False,
    excludes: list[str] | None = None,
) -> ScanStats:
    """Scan source files and populate the graph."""
    load_extractors()
    stats = ScanStats()

    files = collect_files(paths, root, excludes)

    # Smart clean phase: only remove scan-sourced nodes from files about to be rescanned.
    # Collect orphaned manual edges before cascade-deleting nodes.
    if clean:
        rel_paths = set()
        for fpath in files:
            try:
                rel_paths.add(str(fpath.relative_to(root)))
            except ValueError:
                rel_paths.add(str(fpath))
        to_remove = [
            name for name, node in list(graph.nodes.items())
            if node.file is not None
            and node.file in rel_paths
            and node.metadata.get("source") == "scan"
        ]
        for name in to_remove:
            # Before removing, check for manual edges that will be orphaned
            for edge in graph.incoming(name) + graph.outgoing(name):
                if edge.metadata.get("source") == "manual":
                    stats.orphaned_manual_edges.append({
                        "source": edge.source,
                        "rel": edge.rel.value,
                        "target": edge.target,
                        "reason": f"{'source' if edge.source == name else 'target'} node removed",
                    })
                    stats.edges_removed += 1
            stats.nodes_removed += 1
            graph.remove_node(name)

    # Extract phase: collect all nodes and edges
    all_nodes: list[Node] = []
    all_edges: list[Edge] = []
    module_names: set[str] = set()

    for fpath in files:
        ext = fpath.suffix
        extractor = get_extractor(ext)
        if extractor is None:
            continue

        source = fpath.read_bytes()
        try:
            rel_path = str(fpath.relative_to(root))
        except ValueError:
            rel_path = str(fpath)
        module_name = file_to_module_name(rel_path, root)
        module_names.add(module_name)

        # Create the module node (__init__.py / index.ts / index.js -> PACKAGE, else MODULE)
        is_init = fpath.stem in ("__init__", "index")
        all_nodes.append(Node(
            name=module_name,
            type=NodeType.PACKAGE if is_init else NodeType.MODULE,
            file=rel_path,
        ))

        result = extractor.extract(source, rel_path, module_name)
        all_nodes.extend(result.nodes)
        all_edges.extend(result.edges)

        stats.files += 1
        lang = type(extractor).__name__.replace("Extractor", "")
        stats.lang_counts[lang] = stats.lang_counts.get(lang, 0) + 1

    # Add package hierarchy (only for packages not already in all_nodes)
    known_names = {n.name for n in all_nodes}
    for mod_name in module_names:
        parts = mod_name.split(".")
        for i in range(len(parts) - 1):
            pkg_name = ".".join(parts[: i + 1])
            if pkg_name not in known_names and graph.get_node(pkg_name) is None:
                all_nodes.append(Node(name=pkg_name, type=NodeType.PACKAGE))
                known_names.add(pkg_name)

    # Stamp provenance on all scan-produced nodes
    for node in all_nodes:
        node.metadata["source"] = "scan"

    # Populate nodes (upsert)
    for node in all_nodes:
        graph.add_node(node)
        stats.nodes_added += 1
        stats.type_counts[node.type.value] = stats.type_counts.get(node.type.value, 0) + 1

    # Add package CONTAINS edges
    for mod_name in module_names:
        parts = mod_name.split(".")
        for i in range(len(parts) - 1):
            parent = ".".join(parts[: i + 1])
            child = ".".join(parts[: i + 2])
            if graph.get_node(parent) is not None and graph.get_node(child) is not None:
                edge_key = (parent, RelType.CONTAINS.value, child)
                if edge_key not in graph.edges:
                    graph.add_edge(Edge(
                        source=parent, target=child, rel=RelType.CONTAINS,
                        metadata={"source": "scan"},
                    ))
                    stats.edges_added += 1

    # Resolve and add edges (stamp provenance)
    for edge in all_edges:
        is_unresolved = edge.metadata.get("unresolved", False)

        if is_unresolved:
            resolved_target = _resolve_edge_target(graph, edge.target)
            if resolved_target is None:
                stats.skipped_edges += 1
                continue
            edge = Edge(
                source=edge.source,
                target=resolved_target,
                rel=edge.rel,
                metadata={k: v for k, v in edge.metadata.items() if k != "unresolved"},
            )

        # Stamp provenance
        edge.metadata["source"] = "scan"

        # Verify both endpoints exist
        if edge.source not in graph.nodes or edge.target not in graph.nodes:
            stats.skipped_edges += 1
            continue

        if edge.key not in graph.edges:
            graph.add_edge(edge)
            stats.edges_added += 1

    # Post-pass: compute fan-in/fan-out for functions and methods
    for node in graph.all_nodes():
        if node.type.value in ("function", "method"):
            fan_in = len(graph.incoming(node.name, rel=RelType.CALLS))
            fan_out = len(graph.outgoing(node.name, rel=RelType.CALLS))
            node.metadata.setdefault("metrics", {}).update({
                "fan_in": fan_in,
                "fan_out": fan_out,
            })

    return stats


def changed_files(root: Path, since: str = "HEAD") -> list[Path]:
    """Get files changed since a git ref, filtered to supported extensions."""
    try:
        # Changed tracked files
        diff = subprocess.run(
            ["git", "diff", "--name-only", since],
            capture_output=True, text=True, cwd=root,
        )
        # Untracked new files
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=root,
        )
    except FileNotFoundError:
        return []  # git not installed

    all_files: set[str] = set()
    if diff.returncode == 0:
        all_files.update(f.strip() for f in diff.stdout.splitlines() if f.strip())
    if untracked.returncode == 0:
        all_files.update(f.strip() for f in untracked.stdout.splitlines() if f.strip())

    result: list[Path] = []
    for f in all_files:
        if _strip_extension(Path(f).name) is not None:
            fpath = root / f
            if fpath.exists():
                result.append(fpath)
    return sorted(result)


def _resolve_edge_target(graph: SemGraph, target: str) -> str | None:
    """Try to resolve an unresolved edge target to a node in the graph."""
    # Exact match
    if target in graph.nodes:
        return target
    # Try suffix match (e.g. "Bar" -> "app.models.Bar")
    matches = graph.resolve_name(target)
    if len(matches) == 1:
        return matches[0]
    # Unresolved
    return None
