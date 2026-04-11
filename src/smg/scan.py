from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smg.graph import SemGraph
from smg.langs import get_extractor, load_extractors
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
    ".zig-cache",
    "zig-out",
]

_PACKAGE_NAME_CACHE: dict[Path, str | None] = {}


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

    workspace_name = _workspace_module_name(rel, root)
    if workspace_name is not None:
        return workspace_name

    parts = list(rel.parts)

    # Detect src-layout: if first component is "src" and there's a package underneath
    if len(parts) > 1 and parts[0] == "src":
        candidate = root / "src" / parts[1]
        if candidate.is_dir():
            # Python: __init__.py signals a package
            # JS/TS: any directory under src/ is treated as a module root
            has_py_init = (candidate / "__init__.py").exists()
            has_js_marker = (root / "package.json").exists() or (root / "tsconfig.json").exists()
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


def _workspace_module_name(rel_path: Path, root: Path) -> str | None:
    abs_path = rel_path if rel_path.is_absolute() else root / rel_path
    current = abs_path.parent

    while True:
        package_name = _load_package_name(current / "package.json")
        if package_name is not None:
            try:
                package_rel = abs_path.relative_to(current)
            except ValueError:
                return None
            return _module_name_from_parts(package_name, list(package_rel.parts))
        if current == root or current.parent == current:
            return None
        current = current.parent


def _load_package_name(package_json: Path) -> str | None:
    cached = _PACKAGE_NAME_CACHE.get(package_json)
    if package_json in _PACKAGE_NAME_CACHE:
        return cached
    if not package_json.exists():
        _PACKAGE_NAME_CACHE[package_json] = None
        return None
    try:
        data = json.loads(package_json.read_text())
    except (OSError, json.JSONDecodeError):
        _PACKAGE_NAME_CACHE[package_json] = None
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        _PACKAGE_NAME_CACHE[package_json] = None
        return None
    normalized = name.strip()
    if normalized.startswith("@"):
        normalized = normalized[1:]
    normalized = normalized.replace("/", ".")
    _PACKAGE_NAME_CACHE[package_json] = normalized
    return normalized


def _module_name_from_parts(prefix: str, parts: list[str]) -> str:
    if len(parts) > 1 and parts[0] == "src":
        parts = parts[1:]

    if parts:
        stripped = _strip_extension(parts[-1])
        if stripped is not None:
            if stripped in ("__init__", "index"):
                parts = parts[:-1]
            else:
                parts[-1] = stripped

    suffix = [part for part in parts if part]
    return ".".join([prefix, *suffix]) if suffix else prefix


_EXTENSIONS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".zig",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cxx",
    ".hxx",
    ".cu",
    ".cuh",
    ".metal",
)


def _strip_extension(filename: str) -> str | None:
    """Strip a known extension, returning the stem. Returns None if no match."""
    for ext in _EXTENSIONS:
        if filename.endswith(ext):
            return filename[: -len(ext)]
    return None


def _split_exclude_patterns(
    patterns: list[str],
) -> tuple[list[str], list[str]]:
    """Split patterns into basename-only and path-aware groups.

    Patterns containing ``/`` are path-aware and matched against relative
    paths from root.  All others match basenames (the original behavior).
    """
    basename_pats: list[str] = []
    path_pats: list[str] = []
    for pat in patterns:
        if "/" in pat:
            # Strip leading/trailing slashes for consistent matching
            path_pats.append(pat.strip("/"))
        else:
            basename_pats.append(pat)
    return basename_pats, path_pats


def collect_files(
    paths: list[Path],
    root: Path,
    excludes: list[str] | None = None,
) -> list[Path]:
    """Walk paths and collect files with registered extensions."""
    all_excludes = DEFAULT_EXCLUDES + load_smgignore(root) + (excludes or [])
    basename_pats, path_pats = _split_exclude_patterns(all_excludes)
    files: list[Path] = []

    for path in paths:
        path = path.resolve()
        if path.is_file():
            ext = path.suffix
            if get_extractor(ext) is not None:
                files.append(path)
        elif path.is_dir():
            for dirpath, dirnames, filenames in os.walk(path):
                dp = Path(dirpath)
                try:
                    rel_dir = str(dp.relative_to(root))
                except ValueError:
                    rel_dir = str(dp)

                # Prune excluded directories in-place
                pruned: list[str] = []
                for d in dirnames:
                    if any(fnmatch.fnmatch(d, pat) for pat in basename_pats):
                        continue
                    child_rel = d if rel_dir == "." else f"{rel_dir}/{d}"
                    if any(fnmatch.fnmatch(child_rel, pat) for pat in path_pats):
                        continue
                    pruned.append(d)
                dirnames[:] = pruned

                for fname in filenames:
                    if any(fnmatch.fnmatch(fname, pat) for pat in basename_pats):
                        continue
                    file_rel = fname if rel_dir == "." else f"{rel_dir}/{fname}"
                    if any(fnmatch.fnmatch(file_rel, pat) for pat in path_pats):
                        continue
                    fpath = dp / fname
                    ext = fpath.suffix
                    if get_extractor(ext) is not None:
                        files.append(fpath)

    return sorted(set(files))


def _ensure_package_hierarchy(graph: SemGraph, module_name: str, root: Path) -> None:
    """Ensure parent packages exist as PACKAGE nodes with CONTAINS edges."""
    parts = module_name.split(".")
    for i in range(len(parts) - 1):
        pkg_name = ".".join(parts[: i + 1])
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
    # Also remove nodes whose source file has been deleted from disk (within
    # the scanned paths) so that renames/deletes don't leave stale nodes.
    # Collect orphaned manual edges before cascade-deleting nodes.
    if clean:
        rel_paths = {str(fpath.relative_to(root)) if fpath.is_relative_to(root) else str(fpath) for fpath in files}

        # Resolve which directories are being scanned so we can detect
        # stale nodes from deleted files under those directories.
        scan_prefixes: list[str] = []
        for p in paths:
            p = p.resolve()
            try:
                rp = str(p.relative_to(root))
            except ValueError:
                rp = str(p)
            if p.is_dir():
                scan_prefixes.append(rp if rp == "." else rp + "/")
            else:
                # Individual file (possibly deleted): include its parent
                parent = str(Path(rp).parent)
                scan_prefixes.append(parent if parent == "." else parent + "/")

        def _under_scan_paths(file_path: str) -> bool:
            for prefix in scan_prefixes:
                if prefix == "." or file_path.startswith(prefix):
                    return True
            return False

        to_remove = []
        for name, node in list(graph.nodes.items()):
            if node.file is None or node.metadata.get("source") != "scan":
                continue
            if node.file in rel_paths:
                to_remove.append(name)
            elif not (root / node.file).exists() and _under_scan_paths(node.file):
                to_remove.append(name)
        for name in to_remove:
            # Before removing, check for manual edges that will be orphaned
            seen_manual_edges: set[tuple[str, str, str]] = set()
            for edge in graph.iter_incoming(name):
                if edge.metadata.get("source") != "manual" or edge.key in seen_manual_edges:
                    continue
                seen_manual_edges.add(edge.key)
                stats.orphaned_manual_edges.append(
                    {
                        "source": edge.source,
                        "rel": edge.rel.value,
                        "target": edge.target,
                        "reason": f"{'source' if edge.source == name else 'target'} node removed",
                    }
                )
                stats.edges_removed += 1
            for edge in graph.iter_outgoing(name):
                if edge.metadata.get("source") != "manual" or edge.key in seen_manual_edges:
                    continue
                seen_manual_edges.add(edge.key)
                stats.orphaned_manual_edges.append(
                    {
                        "source": edge.source,
                        "rel": edge.rel.value,
                        "target": edge.target,
                        "reason": f"{'source' if edge.source == name else 'target'} node removed",
                    }
                )
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
                    graph.add_edge(
                        Edge(
                            source=parent,
                            target=child,
                            rel=RelType.CONTAINS,
                            metadata={"source": "scan"},
                        )
                    )
                    stats.edges_added += 1

    # Resolve deferred (unresolved) edges — all nodes are now in the graph
    for edge in deferred_edges:
        resolved_target = _resolve_edge_target(graph, edge)
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
            node.metadata.setdefault("metrics", {}).update(
                {
                    "fan_in": fan_in,
                    "fan_out": fan_out,
                }
            )

    return stats


def changed_files(root: Path, since: str = "HEAD") -> list[Path]:
    """Get files changed since a git ref, filtered to supported extensions.

    Includes deleted files (which no longer exist on disk) so that callers
    using ``clean=True`` can remove their stale graph nodes.
    """
    try:
        # Changed tracked files
        diff = subprocess.run(
            ["git", "diff", "--name-only", since],
            capture_output=True,
            text=True,
            cwd=root,
        )
        # Untracked new files
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=root,
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
            result.append(root / f)
    return sorted(result)


def _resolve_edge_target(graph: SemGraph, edge: Edge) -> str | None:
    """Try to resolve an unresolved edge target to a node in the graph."""
    target = edge.target
    # Exact match
    if target in graph.nodes:
        return target

    matches = graph.resolve_name(target)
    if not matches:
        return None
    if edge.rel == RelType.CALLS:
        return _resolve_call_target(graph, edge, matches)
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_call_target(graph: SemGraph, edge: Edge, matches: list[str]) -> str | None:
    target = edge.target
    source_module = _containing_ancestor(
        graph,
        edge.source,
        {NodeType.MODULE.value, NodeType.PACKAGE.value},
    )
    source_class = _containing_ancestor(graph, edge.source, {NodeType.CLASS.value})

    if "." not in target:
        if source_module is not None:
            candidate = f"{source_module}.{target}"
            if _node_type_is(graph, candidate, NodeType.FUNCTION):
                return candidate
        if source_class is not None:
            candidate = f"{source_class}.{target}"
            if _node_type_is(graph, candidate, NodeType.METHOD):
                return candidate

        function_matches = [name for name in matches if _node_type_is(graph, name, NodeType.FUNCTION)]
        if len(function_matches) == 1:
            return function_matches[0]
        if source_module is not None:
            local_function_matches = [name for name in function_matches if name.startswith(source_module + ".")]
            if len(local_function_matches) == 1:
                return local_function_matches[0]
        return None

    if len(matches) == 1:
        return matches[0]
    if source_module is not None:
        local_matches = [name for name in matches if name.startswith(source_module + ".")]
        if len(local_matches) == 1:
            return local_matches[0]
    return None


def _containing_ancestor(graph: SemGraph, name: str, node_types: set[str]) -> str | None:
    current = name
    seen: set[str] = set()

    while current not in seen:
        seen.add(current)
        parent = next(
            (edge.source for edge in graph.iter_incoming(current, rel=RelType.CONTAINS)),
            None,
        )
        if parent is None:
            return None
        parent_node = graph.get_node(parent)
        if parent_node is not None and parent_node.type.value in node_types:
            return parent
        current = parent

    return None


def _node_type_is(graph: SemGraph, name: str, node_type: NodeType) -> bool:
    node = graph.get_node(name)
    return node is not None and node.type == node_type
