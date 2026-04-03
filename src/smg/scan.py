from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smg.graph import SemGraph
from smg.langs import ExtractResult, get_extractor, load_extractors
from smg.model import Edge, Node, NodeType, RelType

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
    ".hypothesis",
    ".tox",
    "dist",
    "build",
    "*.egg-info",
    ".smg",
    "site-packages",
    "vendor",
    "third_party",
    "zig-cache",
    "zig-out",
]


def load_smgignore(root: Path) -> list[str]:
    """Load additional exclude patterns from .smgignore file (gitignore syntax)."""
    ignore_file = root / ".smgignore"
    if not ignore_file.exists():
        return []
    patterns: list[str] = []
    for line in ignore_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


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
        src/smg/graph.py       -> smg.graph
        src/smg/__init__.py    -> smg
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


_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".zig", ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hxx", ".cu", ".cuh", ".metal")


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
    all_excludes = DEFAULT_EXCLUDES + load_smgignore(root) + (excludes or [])
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
    on_progress: Any = None,
) -> ScanStats:
    """Scan source files and populate the graph."""
    load_extractors()
    stats = ScanStats()

    files = collect_files(paths, root, excludes)

    # Smart clean phase: only remove scan-sourced nodes from files about to be rescanned.
    # Collect orphaned manual edges before cascade-deleting nodes.
    if clean:
        rel_paths = {
            str(fpath.relative_to(root)) if fpath.is_relative_to(root) else str(fpath)
            for fpath in files
        }
        to_remove = [
            name for name, node in list(graph.nodes.items())
            if node.file is not None
            and node.file in rel_paths
            and node.metadata.get("source") == "scan"
        ]
        for name in to_remove:
            # Before removing, check for manual edges that will be orphaned
            seen_manual_edges: set[tuple[str, str, str]] = set()
            for edge in graph.iter_incoming(name):
                if edge.metadata.get("source") != "manual" or edge.key in seen_manual_edges:
                    continue
                seen_manual_edges.add(edge.key)
                stats.orphaned_manual_edges.append({
                    "source": edge.source,
                    "rel": edge.rel.value,
                    "target": edge.target,
                    "reason": f"{'source' if edge.source == name else 'target'} node removed",
                })
                stats.edges_removed += 1
            for edge in graph.iter_outgoing(name):
                if edge.metadata.get("source") != "manual" or edge.key in seen_manual_edges:
                    continue
                seen_manual_edges.add(edge.key)
                stats.orphaned_manual_edges.append({
                    "source": edge.source,
                    "rel": edge.rel.value,
                    "target": edge.target,
                    "reason": f"{'source' if edge.source == name else 'target'} node removed",
                })
                stats.edges_removed += 1
            stats.nodes_removed += 1
            graph.remove_node(name)

    # Streaming extract phase: insert nodes immediately, defer unresolved edges
    deferred_edges: list[Edge] = []
    module_names: set[str] = set()
    scanned_nodes: list[str] = []  # track names for fan-in/fan-out post-pass

    graph_nodes = graph.nodes  # local ref for faster lookups

    for file_idx, fpath in enumerate(files):
        ext = fpath.suffix
        extractor = get_extractor(ext)
        if extractor is None:
            continue

        source = fpath.read_bytes()
        try:
            rel_path = str(fpath.relative_to(root))
        except ValueError:
            rel_path = str(fpath)

        if on_progress:
            on_progress(file_idx + 1, len(files), rel_path)
        module_name = file_to_module_name(rel_path, root)
        module_names.add(module_name)

        # Create the module node (__init__.py / index.ts / index.js -> PACKAGE, else MODULE)
        is_init = fpath.stem in ("__init__", "index")
        mod_node = Node(
            name=module_name,
            type=NodeType.PACKAGE if is_init else NodeType.MODULE,
            file=rel_path,
            metadata={"source": "scan"},
        )
        graph.add_node(mod_node)
        stats.nodes_added += 1
        stats.type_counts[mod_node.type.value] = stats.type_counts.get(mod_node.type.value, 0) + 1

        result = extractor.extract(source, rel_path, module_name)

        # Insert nodes immediately
        for node in result.nodes:
            node.metadata["source"] = "scan"
            graph.add_node(node)
            stats.nodes_added += 1
            stats.type_counts[node.type.value] = stats.type_counts.get(node.type.value, 0) + 1
            if node.type.value in ("function", "method"):
                scanned_nodes.append(node.name)

        # Partition edges: resolved go in now, unresolved deferred
        for edge in result.edges:
            if edge.metadata.get("unresolved"):
                deferred_edges.append(edge)
            else:
                edge.metadata["source"] = "scan"
                if edge.source in graph_nodes and edge.target in graph_nodes:
                    if edge.key not in graph.edges:
                        graph.add_edge(edge)
                        stats.edges_added += 1
                else:
                    stats.skipped_edges += 1

        stats.files += 1
        lang = type(extractor).__name__.replace("Extractor", "")
        stats.lang_counts[lang] = stats.lang_counts.get(lang, 0) + 1

    # Add package hierarchy
    for mod_name in module_names:
        parts = mod_name.split(".")
        for i in range(len(parts) - 1):
            pkg_name = ".".join(parts[: i + 1])
            if graph.get_node(pkg_name) is None:
                pkg_node = Node(name=pkg_name, type=NodeType.PACKAGE, metadata={"source": "scan"})
                graph.add_node(pkg_node)

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

    # Resolve deferred (unresolved) edges — all nodes are now in the graph
    for edge in deferred_edges:
        resolved_target = _resolve_edge_target(graph, edge.target)
        if resolved_target is None:
            stats.skipped_edges += 1
            continue
        resolved_edge = Edge(
            source=edge.source,
            target=resolved_target,
            rel=edge.rel,
            metadata={k: v for k, v in edge.metadata.items() if k != "unresolved"},
        )
        resolved_edge.metadata["source"] = "scan"

        if resolved_edge.source not in graph_nodes or resolved_edge.target not in graph_nodes:
            stats.skipped_edges += 1
            continue

        if resolved_edge.key not in graph.edges:
            graph.add_edge(resolved_edge)
            stats.edges_added += 1

    # Post-pass: compute fan-in/fan-out only for scanned functions/methods
    for name in scanned_nodes:
        node = graph.get_node(name)
        if node is not None:
            fan_in = graph.incoming_count(name, rel=RelType.CALLS)
            fan_out = graph.outgoing_count(name, rel=RelType.CALLS)
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
