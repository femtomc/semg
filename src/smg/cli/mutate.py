from __future__ import annotations

import sys
from pathlib import Path

import rich_click as click
from rich.table import Table

from smg.cli import (
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
    _auto_fmt,
    _load,
    _parse_meta,
    _rel_style,
    _resolve_or_exit,
    _type_badge,
    console,
    err_console,
    main,
)
from smg.graph import NodeNotFoundError
from smg.model import Edge, Node, NodeType, RelType
from smg.storage import init_project, save_graph


@main.command()
def init() -> None:
    """Initialize [bold].smg/[/] in the current directory.

    Creates a .smg/ directory with an empty graph. Run this once per project,
    then use [bold]smg scan[/] to populate.
    """
    root = init_project()
    console.print(f"[green]Initialized[/] .smg/ in {root}")


@main.command()
@click.argument("type")
@click.argument("name")
@click.option("--file", "file_", default=None, help="Source file path")
@click.option("--line", default=None, type=int, help="Line number")
@click.option("--doc", default=None, help="Docstring / description")
@click.option("--meta", multiple=True, help="KEY=VALUE metadata (repeatable)")
def add(
    type: str,
    name: str,
    file_: str | None,
    line: int | None,
    doc: str | None,
    meta: tuple[str, ...],
) -> None:
    """Add a node to the graph (upserts if it already exists).

    \b
    Node types: package, module, class, function, method, interface,
                variable, constant, type, endpoint, config (or any custom string)
    Examples:
      smg add module app.auth
      smg add class app.auth.AuthService --file src/auth.py --line 12
      smg add endpoint /api/login --doc "Login endpoint" --meta method=POST
    """
    graph, root = _load()
    metadata = _parse_meta(meta)
    metadata.setdefault("source", "manual")
    node = Node(
        name=name,
        type=NodeType(type),
        file=file_,
        line=line,
        docstring=doc,
        metadata=metadata,
    )
    graph.add_node(node)
    save_graph(graph, root)
    console.print(f"[green]Added[/] [{_type_badge(type)}] [bold]{name}[/]")


@main.command()
@click.argument("source")
@click.argument("rel")
@click.argument("target")
@click.option("--meta", multiple=True, help="KEY=VALUE metadata (repeatable)")
def link(source: str, rel: str, target: str, meta: tuple[str, ...]) -> None:
    """Add a typed edge between two nodes.

    \b
    Relationship types: calls, inherits, implements, contains, depends_on,
                        imports, returns, accepts, overrides, decorates, tests
                        (or any custom string)
    Examples:
      smg link app.auth calls app.db
      smg link app.auth.Service inherits app.base.Base
      smg link app.routes depends_on app.auth
    """
    graph, root = _load()
    source = _resolve_or_exit(graph, source)
    target = _resolve_or_exit(graph, target)
    metadata = _parse_meta(meta)
    metadata.setdefault("source", "manual")
    edge = Edge(source=source, target=target, rel=RelType(rel), metadata=metadata)
    try:
        graph.add_edge(edge)
    except NodeNotFoundError as e:
        err_console.print(f"[red]Error:[/] {e}")
        sys.exit(EXIT_NOT_FOUND)
    save_graph(graph, root)
    console.print(f"[green]Linked[/] {source} [dim]--{_rel_style(rel)}-->[/] {target}")


@main.command()
@click.argument("name")
def rm(name: str) -> None:
    """Remove a node and all its edges (cascade delete).

    Short names work: [bold]smg rm AuthService[/] resolves to the full name if unambiguous.
    """
    graph, root = _load()
    name = _resolve_or_exit(graph, name)
    try:
        graph.remove_node(name)
    except NodeNotFoundError as e:
        err_console.print(f"[red]Error:[/] {e}")
        sys.exit(EXIT_NOT_FOUND)
    save_graph(graph, root)
    console.print(f"[red]Removed[/] {name}")


@main.command()
@click.argument("source")
@click.argument("rel")
@click.argument("target")
def unlink(source: str, rel: str, target: str) -> None:
    """Remove a specific edge.

    \b
    Example: smg unlink app.auth calls app.db
    """
    graph, root = _load()
    source = _resolve_or_exit(graph, source)
    target = _resolve_or_exit(graph, target)
    try:
        graph.remove_edge(source, rel, target)
    except KeyError as e:
        err_console.print(f"[red]Error:[/] {e}")
        sys.exit(EXIT_NOT_FOUND)
    save_graph(graph, root)
    console.print(f"[red]Unlinked[/] {source} [dim]--{rel}-->[/] {target}")


@main.command()
@click.argument("name")
@click.option("--type", "type_", default=None, help="New node type")
@click.option("--file", "file_", default=None, help="Source file path")
@click.option("--line", default=None, type=int, help="Line number")
@click.option("--doc", default=None, help="Docstring / description")
@click.option("--meta", multiple=True, help="KEY=VALUE metadata (repeatable)")
def update(
    name: str,
    type_: str | None,
    file_: str | None,
    line: int | None,
    doc: str | None,
    meta: tuple[str, ...],
) -> None:
    """Update a node's fields (only specified fields are changed).

    \b
    Example: smg update app.auth --doc "Auth module" --meta owner=alice
    """
    graph, root = _load()
    name = _resolve_or_exit(graph, name)
    node = graph.get_node(name)
    assert node is not None
    if type_ is not None:
        node.type = NodeType(type_)
    if file_ is not None:
        node.file = file_
    if line is not None:
        node.line = line
    if doc is not None:
        node.docstring = doc
    if meta:
        node.metadata.update(_parse_meta(meta))
    save_graph(graph, root)
    console.print(f"[green]Updated[/] [bold]{name}[/]")


@main.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--clean",
    is_flag=True,
    help="Remove scan-sourced nodes from scanned files before repopulating",
)
@click.option(
    "--changed",
    is_flag=True,
    help="Only rescan files changed since last commit (implies --clean)",
)
@click.option(
    "--since",
    default=None,
    help="Only rescan files changed since REF (implies --clean)",
)
@click.option("--exclude", multiple=True, help="Additional exclude patterns (repeatable)")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
)
def scan(
    paths: tuple[str, ...],
    clean: bool,
    changed: bool,
    since: str | None,
    exclude: tuple[str, ...],
    fmt: str | None,
) -> None:
    """Scan source files with tree-sitter and populate the graph.

    \b
    Supported: Python (.py), JavaScript (.js/.jsx), TypeScript (.ts/.tsx), Zig (.zig)
    Extracts: classes, functions, methods, constants, containment, imports,
              inheritance, call graph, and per-function complexity metrics.

    \b
    Examples:
      smg scan src/                 # full scan
      smg scan src/ --clean         # remove stale nodes, then rescan
      smg scan --changed            # only files changed since last commit
      smg scan --since HEAD~3       # only files changed in last 3 commits
    Manual nodes/edges (source=manual) are preserved across --clean rescans.
    """
    try:
        from smg.scan import changed_files, scan_paths
    except ImportError:
        err_console.print("[red]Error:[/] tree-sitter not installed. Install with: [bold]uv pip install smg\\[scan][/]")
        sys.exit(EXIT_VALIDATION)

    graph, root = _load()

    if changed or since:
        ref = since or "HEAD"
        file_list = changed_files(root, ref)
        if not file_list:
            fmt = _auto_fmt(fmt)
            if fmt == "json":
                import json

                click.echo(
                    json.dumps(
                        {
                            "files": 0,
                            "message": f"no supported files changed since {ref}",
                        }
                    )
                )
            else:
                console.print(f"[dim]No supported files changed since {ref}.[/]")
            return
        scan_dirs = file_list
        clean = True  # --changed implies --clean
    else:
        scan_dirs = [Path(p).resolve() for p in paths] if paths else [Path.cwd().resolve()]

    fmt = _auto_fmt(fmt)

    # Progress callback for text mode
    progress_cb = None
    progress_ctx = None
    if fmt == "text" and sys.stdout.isatty():
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
        )

        progress_ctx = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=30),
            MofNCompleteColumn(),
            console=console,
            transient=True,
        )
        progress_ctx.start()
        task_id = progress_ctx.add_task("Scanning...", total=None)

        def _on_progress(current: int, total: int, file_path: str) -> None:
            progress_ctx.update(  # type: ignore[union-attr]
                task_id,
                total=total,
                completed=current,
                description=f"[dim]{file_path}[/]",
            )

        progress_cb = _on_progress

    stats = scan_paths(
        graph,
        root,
        scan_dirs,
        clean=clean,
        excludes=list(exclude) or None,
        on_progress=progress_cb,
    )

    if progress_ctx is not None:
        progress_ctx.stop()

    save_graph(graph, root)

    if fmt == "json":
        import json

        data: dict = {
            "files": stats.files,
            "nodes_added": stats.nodes_added,
            "nodes_removed": stats.nodes_removed,
            "edges_added": stats.edges_added,
            "edges_removed": stats.edges_removed,
            "skipped_edges": stats.skipped_edges,
            "languages": stats.lang_counts,
            "types": stats.type_counts,
        }
        if stats.orphaned_manual_edges:
            data["orphaned_manual_edges"] = stats.orphaned_manual_edges
        click.echo(json.dumps(data, indent=2))
    else:
        langs = ", ".join(f"{v} {k}" for k, v in sorted(stats.lang_counts.items()))
        console.print(f"[green]Scanned[/] {stats.files} files ({langs})")

        table = Table(show_header=False, border_style="dim", pad_edge=False, box=None)
        table.add_column("Label", style="dim")
        table.add_column("Value")
        type_parts = ", ".join(f"{v} {_type_badge(k)}" for k, v in sorted(stats.type_counts.items()))
        table.add_row(
            "Nodes",
            f"+{stats.nodes_added} -{stats.nodes_removed}" if stats.nodes_removed else type_parts,
        )
        table.add_row(
            "Edges",
            f"+{stats.edges_added} -{stats.edges_removed}" if stats.edges_removed else str(stats.edges_added),
        )
        if stats.skipped_edges:
            table.add_row("Skipped", f"{stats.skipped_edges} unresolved")
        # Warn if most edges were skipped (likely scanned too narrow a scope)
        total_edges = stats.edges_added + stats.skipped_edges
        if stats.skipped_edges > 0 and total_edges > 0:
            skip_ratio = stats.skipped_edges / total_edges
            if skip_ratio > 0.5:
                console.print(
                    f"\n[yellow]Hint:[/] {stats.skipped_edges}/{total_edges} edges were skipped"
                    " (targets outside the scanned scope). Try scanning a wider directory"
                    " to resolve cross-module edges."
                )
        console.print(table)

        if stats.orphaned_manual_edges:
            console.print(f"\n[yellow]Warning:[/] {len(stats.orphaned_manual_edges)} manual edge(s) orphaned:")
            for oe in stats.orphaned_manual_edges:
                console.print(f"  {oe['source']} [dim]--{oe['rel']}-->[/] {oe['target']} [dim]({oe['reason']})[/]")


@main.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--debounce",
    default=0.5,
    type=float,
    help="Seconds to wait before rescanning after a change",
)
def watch(paths: tuple[str, ...], debounce: float) -> None:
    """Watch source files and auto-rescan on changes.

    Monitors the filesystem for changes to supported source files and
    triggers an incremental rescan (with --clean) automatically. Runs
    until interrupted with Ctrl+C.
    """
    try:
        from smg.watch import watch_and_scan
    except ImportError:
        err_console.print("[red]Error:[/] watchdog not installed. Install with: [bold]uv pip install smg\\[scan][/]")
        sys.exit(EXIT_VALIDATION)

    _graph, root = _load()

    watch_dirs = [Path(p).resolve() for p in paths] if paths else [Path.cwd().resolve()]

    def on_scan(diff, stats, files):
        names = [str(f.relative_to(root)) for f in files]
        file_list = ", ".join(names[:3])
        if len(names) > 3:
            file_list += f" (+{len(names) - 3} more)"

        if diff.is_empty:
            console.print(f"[dim]{file_list}[/] → [dim]no structural changes[/]")
            return

        parts = []
        if diff.added_nodes:
            parts.append(f"[green]+{len(diff.added_nodes)} nodes[/]")
        if diff.removed_nodes:
            parts.append(f"[red]-{len(diff.removed_nodes)} nodes[/]")
        if diff.changed_nodes:
            parts.append(f"[yellow]~{len(diff.changed_nodes)} changed[/]")
        if diff.added_edges:
            parts.append(f"[green]+{len(diff.added_edges)} edges[/]")
        if diff.removed_edges:
            parts.append(f"[red]-{len(diff.removed_edges)} edges[/]")

        console.print(f"[dim]{file_list}[/] → {', '.join(parts)}")

        # Show specific additions/removals for small diffs
        for node in diff.added_nodes[:3]:
            console.print(f"  [green]+[/] [{_type_badge(node.type.value)}] {node.name}")
        for node in diff.removed_nodes[:3]:
            console.print(f"  [red]-[/] [{_type_badge(node.type.value)}] {node.name}")
        for node, changes in diff.changed_nodes[:3]:
            for c in changes:
                console.print(f"  [yellow]~[/] {node.name} {c.field}: {c.old} → {c.new}")

        if stats.orphaned_manual_edges:
            for oe in stats.orphaned_manual_edges:
                console.print(f"  [yellow]orphaned:[/] {oe['source']} --{oe['rel']}--> {oe['target']}")

    console.print(f"[bold]Watching[/] {', '.join(str(p) for p in watch_dirs)}")
    console.print("[dim]Press Ctrl+C to stop.[/]\n")

    watch_and_scan(root, watch_dirs, on_scan=on_scan, debounce=debounce)
    console.print("\n[dim]Stopped.[/]")


@main.command()
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
)
def batch(fmt: str | None) -> None:
    """Execute JSONL commands from stdin in one load/save cycle.

    Much faster than individual commands for bulk mutations — the graph is
    loaded and saved only once. Partial failure tolerant: errors on individual
    lines are reported but don't stop processing.

    \b
    Supported operations:
      {"op": "add", "type": "function", "name": "app.main", "file": "app.py", "line": 1, "doc": "...", "metadata": {}}
      {"op": "link", "source": "app", "rel": "contains", "target": "app.main", "metadata": {}}
      {"op": "rm", "name": "app.main"}
      {"op": "unlink", "source": "app", "rel": "contains", "target": "app.main"}
      {"op": "update", "name": "app.main", "type": "method", "file": "...", "line": 2, "doc": "...", "metadata": {}}
    """
    import json as json_mod

    graph, root = _load()
    fmt = _auto_fmt(fmt)

    stats = {"ok": 0, "errors": 0, "ops": []}
    input_text = click.get_text_stream("stdin").read()

    for line_no, line in enumerate(input_text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json_mod.loads(line)
        except json_mod.JSONDecodeError as e:
            stats["errors"] += 1
            stats["ops"].append({"line": line_no, "error": f"invalid JSON: {e}"})
            continue

        op = cmd.get("op")
        try:
            if op == "add":
                metadata = cmd.get("metadata", {})
                metadata.setdefault("source", "manual")
                node = Node(
                    name=cmd["name"],
                    type=NodeType(cmd["type"]),
                    file=cmd.get("file"),
                    line=cmd.get("line"),
                    docstring=cmd.get("doc"),
                    metadata=metadata,
                )
                graph.add_node(node)
                stats["ok"] += 1
                stats["ops"].append({"line": line_no, "op": "add", "name": cmd["name"]})

            elif op == "link":
                metadata = cmd.get("metadata", {})
                metadata.setdefault("source", "manual")
                edge = Edge(
                    source=cmd["source"],
                    target=cmd["target"],
                    rel=RelType(cmd["rel"]),
                    metadata=metadata,
                )
                graph.add_edge(edge)
                stats["ok"] += 1
                stats["ops"].append(
                    {
                        "line": line_no,
                        "op": "link",
                        "source": cmd["source"],
                        "target": cmd["target"],
                    }
                )

            elif op == "rm":
                name = cmd["name"]
                matches = graph.resolve_name(name)
                if len(matches) == 1:
                    graph.remove_node(matches[0])
                    stats["ok"] += 1
                    stats["ops"].append({"line": line_no, "op": "rm", "name": matches[0]})
                else:
                    stats["errors"] += 1
                    stats["ops"].append({"line": line_no, "error": f"cannot resolve: {name!r}"})

            elif op == "unlink":
                graph.remove_edge(cmd["source"], cmd["rel"], cmd["target"])
                stats["ok"] += 1
                stats["ops"].append({"line": line_no, "op": "unlink"})

            elif op == "update":
                name = cmd["name"]
                matches = graph.resolve_name(name)
                if len(matches) != 1:
                    stats["errors"] += 1
                    stats["ops"].append({"line": line_no, "error": f"cannot resolve: {name!r}"})
                    continue
                node = graph.get_node(matches[0])
                if "type" in cmd:
                    node.type = NodeType(cmd["type"])
                if "file" in cmd:
                    node.file = cmd["file"]
                if "line" in cmd:
                    node.line = cmd["line"]
                if "doc" in cmd:
                    node.docstring = cmd["doc"]
                if "metadata" in cmd:
                    node.metadata.update(cmd["metadata"])
                stats["ok"] += 1
                stats["ops"].append({"line": line_no, "op": "update", "name": matches[0]})

            else:
                stats["errors"] += 1
                stats["ops"].append({"line": line_no, "error": f"unknown op: {op!r}"})

        except (KeyError, NodeNotFoundError) as e:
            stats["errors"] += 1
            stats["ops"].append({"line": line_no, "error": str(e)})

    save_graph(graph, root)

    if fmt == "json":
        click.echo(json_mod.dumps(stats, indent=2))
    else:
        console.print(f"[green]Batch complete:[/] {stats['ok']} ok, {stats['errors']} errors")
        for entry in stats["ops"]:
            if "error" in entry:
                console.print(f"  [red]line {entry['line']}:[/] {entry['error']}")
