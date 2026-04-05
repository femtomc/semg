from __future__ import annotations

import sys

import rich_click as click
from rich.panel import Panel
from rich.table import Table

from smg import export, query
from smg.cli import (
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
    _auto_fmt,
    _load,
    _output_edges,
    _output_graph,
    _output_names,
    _rel_style,
    _resolve_or_exit,
    _type_badge,
    console,
    err_console,
    main,
)


@main.command()
@click.argument("name")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
)
def show(name: str, fmt: str | None) -> None:
    """Show a node's details, connections, and metrics.

    Short names work: [bold]smg show SemGraph[/] resolves if unambiguous.
    Functions/methods include cyclomatic complexity, fan-in/fan-out, etc.
    """
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    node = graph.get_node(name)
    assert node is not None
    inc = graph.incoming(name)
    out = graph.outgoing(name)

    fmt = _auto_fmt(fmt)
    if fmt == "json":
        click.echo(export.format_node(node, inc, out, fmt="json"))
        return

    # Rich panel display
    title = f"[bold]{node.name}[/]  [{_type_badge(node.type.value)}]"
    lines: list[str] = []
    if node.file:
        loc = node.file
        if node.line is not None:
            loc += f":{node.line}"
            if node.end_line is not None and node.end_line != node.line:
                loc += f"-{node.end_line}"
        lines.append(f"[dim]file:[/]  {loc}")
    if node.docstring:
        doc = node.docstring.split("\n")[0]
        lines.append(f"[dim]doc:[/]   {doc}")
    if node.metadata:
        for k, v in sorted(node.metadata.items()):
            lines.append(f"[dim]{k}:[/]  {v}")

    if inc:
        lines.append("")
        lines.append(f"[bold]Incoming[/] ({len(inc)})")
        for e in inc:
            lines.append(f"  {e.source} [dim]--{_rel_style(e.rel.value)}-->[/] {node.name}")

    if out:
        lines.append("")
        lines.append(f"[bold]Outgoing[/] ({len(out)})")
        for e in out:
            lines.append(f"  {node.name} [dim]--{_rel_style(e.rel.value)}-->[/] {e.target}")

    console.print(Panel("\n".join(lines), title=title, border_style="dim"))


@main.command("list")
@click.option("--type", "type_", default=None, help="Filter by node type")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
)
def list_nodes(type_: str | None, fmt: str | None) -> None:
    """List all nodes in the graph.

    \b
    Filter by type: smg list --type class
    Valid types: package, module, class, function, method, interface,
                 variable, constant, type, endpoint, config
    """
    graph, _root = _load()
    nodes = graph.all_nodes(type=type_)
    fmt = _auto_fmt(fmt)

    if fmt == "json":
        import json

        data = [n.to_dict() for n in nodes]
        for d in data:
            d.pop("kind", None)
        click.echo(json.dumps(data, indent=2))
        return

    if not nodes:
        console.print("[dim]No nodes.[/]")
        return

    table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
    table.add_column("Type", style="dim", width=10)
    table.add_column("Name", style="bold")
    table.add_column("File", style="dim")
    table.add_column("Doc", style="dim", max_width=40, overflow="ellipsis")

    for node in nodes:
        loc = ""
        if node.file:
            loc = node.file
            if node.line is not None:
                loc += f":{node.line}"
                if node.end_line is not None and node.end_line != node.line:
                    loc += f"-{node.end_line}"
        doc = (node.docstring or "").split("\n")[0][:40]
        table.add_row(_type_badge(node.type.value), node.name, loc, doc)

    console.print(table)


@main.command()
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
)
def status(fmt: str | None) -> None:
    """Show graph summary — node/edge counts broken down by type."""
    graph, _root = _load()
    nodes = graph.all_nodes()
    edges = graph.all_edges()
    fmt = _auto_fmt(fmt)

    type_counts: dict[str, int] = {}
    for n in nodes:
        type_counts[n.type.value] = type_counts.get(n.type.value, 0) + 1
    rel_counts: dict[str, int] = {}
    for e in edges:
        rel_counts[e.rel.value] = rel_counts.get(e.rel.value, 0) + 1

    if fmt == "json":
        import json

        data = {
            "nodes": len(nodes),
            "edges": len(edges),
            "node_types": type_counts,
            "rel_types": rel_counts,
        }
        click.echo(json.dumps(data, indent=2))
        return

    # Node type table
    node_table = Table(title=f"[bold]Nodes[/] ({len(nodes)})", border_style="dim", pad_edge=False)
    node_table.add_column("Type", style="dim")
    node_table.add_column("Count", justify="right")
    for t, c in sorted(type_counts.items()):
        node_table.add_row(_type_badge(t), str(c))

    # Edge type table
    edge_table = Table(title=f"[bold]Edges[/] ({len(edges)})", border_style="dim", pad_edge=False)
    edge_table.add_column("Relationship", style="dim")
    edge_table.add_column("Count", justify="right")
    for r, c in sorted(rel_counts.items()):
        edge_table.add_row(_rel_style(r), str(c))

    from rich.columns import Columns

    console.print(Columns([node_table, edge_table], padding=(0, 4)))


# --- Query subgroup ---


@main.group()
def query_cmd() -> None:
    """Low-level graph queries — deps, callers, paths, subgraphs, edges.

    For high-level questions, try [bold]about[/], [bold]impact[/], or [bold]between[/] instead.
    """
    pass


main.add_command(query_cmd, "query")


@query_cmd.command("deps")
@click.argument("name")
@click.option("--depth", default=None, type=int, help="Max traversal depth")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json", "mermaid", "dot"]),
    help="Output format (auto-detects: JSON when piped)",
)
def query_deps(name: str, depth: int | None, fmt: str | None) -> None:
    """Transitive dependencies of a node (follows imports/depends_on edges)."""
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    deps = query.transitive_deps(graph, name, max_depth=depth)
    _output_names(deps, f"Dependencies of {name}", fmt, graph, name)


@query_cmd.command("callers")
@click.argument("name")
@click.option("--depth", default=None, type=int, help="Max traversal depth")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json", "mermaid", "dot"]),
    help="Output format (auto-detects: JSON when piped)",
)
def query_callers(name: str, depth: int | None, fmt: str | None) -> None:
    """What calls this node (transitively, follows incoming calls edges)."""
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    callers = query.transitive_callers(graph, name, max_depth=depth)
    _output_names(callers, f"Callers of {name}", fmt, graph, name)


@query_cmd.command("path")
@click.argument("source")
@click.argument("target")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
)
def query_path(source: str, target: str, fmt: str | None) -> None:
    """Shortest path between two nodes."""
    graph, _root = _load()
    source = _resolve_or_exit(graph, source)
    target = _resolve_or_exit(graph, target)
    fmt = _auto_fmt(fmt)
    path = query.shortest_path(graph, source, target)
    if path is None:
        err_console.print(f"[red]Error:[/] no path from {source} to {target}")
        sys.exit(EXIT_NOT_FOUND)
    if fmt == "json":
        import json

        click.echo(json.dumps(path))
    else:
        styled = " [dim]->[/] ".join(f"[bold]{p}[/]" for p in path)
        console.print(styled)


@query_cmd.command("subgraph")
@click.argument("name")
@click.option("--depth", default=2, type=int, help="Number of hops [dim](default: 2)[/]")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json", "mermaid", "dot"]),
    help="Output format (auto-detects: JSON when piped)",
)
def query_subgraph(name: str, depth: int, fmt: str | None) -> None:
    """Neighborhood around a node."""
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    sub = query.subgraph(graph, name, depth=depth)
    _output_graph(sub, fmt)


@query_cmd.command("incoming")
@click.argument("name")
@click.option("--rel", default=None, help="Filter by relationship type")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
)
def query_incoming(name: str, rel: str | None, fmt: str | None) -> None:
    """Incoming edges to a node. Filter with --rel calls, --rel imports, etc."""
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    edges = graph.incoming(name, rel=rel)
    _output_edges(edges, fmt)


@query_cmd.command("outgoing")
@click.argument("name")
@click.option("--rel", default=None, help="Filter by relationship type")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
)
def query_outgoing(name: str, rel: str | None, fmt: str | None) -> None:
    """Outgoing edges from a node. Filter with --rel calls, --rel imports, etc."""
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    edges = graph.outgoing(name, rel=rel)
    _output_edges(edges, fmt)


# --- Validate ---


@main.command()
def validate() -> None:
    """Check graph integrity (dangling edges, missing nodes)."""
    graph, _root = _load()
    issues = graph.validate()
    if not issues:
        console.print("[green]Graph is valid.[/]")
    else:
        err_console.print(f"[red]Found {len(issues)} issue(s):[/]")
        for issue in issues:
            err_console.print(f"  [dim]-[/] {issue}")
        sys.exit(EXIT_VALIDATION)
