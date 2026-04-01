from __future__ import annotations

import sys
from pathlib import Path

import rich_click as click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from semg import export, query
from semg.graph import NodeNotFoundError, SemGraph
from semg.model import Edge, Node, NodeType, RelType
from semg.storage import find_root, init_project, load_graph, save_graph

# Exit codes
EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_VALIDATION = 2
EXIT_NO_PROJECT = 3

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

# --- rich-click configuration ---

click.rich_click.TEXT_MARKUP = "rich"
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.STYLE_COMMAND = "bold cyan"
click.rich_click.STYLE_SWITCH = "bold green"
click.rich_click.STYLE_METAVAR = "dim"
click.rich_click.COMMAND_GROUPS = {
    "semg": [
        {"name": "Explore", "commands": ["about", "impact", "between", "overview", "diff"]},
        {"name": "Inspect", "commands": ["show", "list", "status", "query", "validate"]},
        {"name": "Mutate", "commands": ["init", "add", "link", "rm", "unlink", "update", "scan", "batch"]},
        {"name": "Export", "commands": ["export"]},
    ],
}


def _load() -> tuple[SemGraph, Path]:
    root = find_root()
    if root is None:
        err_console.print("[red]Error:[/] no .semg/ found. Run [bold]semg init[/] first.")
        sys.exit(EXIT_NO_PROJECT)
    return load_graph(root), root


def _resolve_or_exit(graph: SemGraph, name: str) -> str:
    """Resolve a name (possibly short) to a single node name, or exit with error."""
    matches = graph.resolve_name(name)
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 0:
        err_console.print(f"[red]Error:[/] node not found: [bold]{name}[/]")
        sys.exit(EXIT_NOT_FOUND)
    err_console.print(f"[red]Error:[/] ambiguous name [bold]{name}[/], matches:")
    for m in matches:
        err_console.print(f"  {m}")
    sys.exit(EXIT_NOT_FOUND)


def _auto_fmt(explicit: str | None) -> str:
    """Auto-detect output format: JSON when piped, rich text in terminal."""
    if explicit is not None:
        return explicit
    return "text" if sys.stdout.isatty() else "json"


def _parse_meta(meta: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in meta:
        if "=" not in item:
            err_console.print(f"[red]Error:[/] --meta must be KEY=VALUE, got {item!r}")
            sys.exit(EXIT_VALIDATION)
        k, v = item.split("=", 1)
        result[k] = v
    return result


# --- Type style helpers ---

_TYPE_COLORS = {
    "package": "magenta",
    "module": "blue",
    "class": "yellow",
    "function": "green",
    "method": "cyan",
    "interface": "yellow",
    "variable": "white",
    "constant": "red",
    "type": "magenta",
    "endpoint": "bright_green",
    "config": "dim",
}

_REL_COLORS = {
    "contains": "dim",
    "imports": "blue",
    "calls": "green",
    "inherits": "yellow",
    "implements": "yellow",
    "depends_on": "red",
    "returns": "cyan",
    "accepts": "cyan",
    "overrides": "magenta",
    "decorates": "bright_magenta",
    "tests": "bright_green",
}


def _type_badge(type_val: str) -> str:
    color = _TYPE_COLORS.get(type_val, "white")
    return f"[{color}]{type_val}[/]"


def _rel_style(rel_val: str) -> str:
    color = _REL_COLORS.get(rel_val, "white")
    return f"[{color}]{rel_val}[/]"


# --- Main group ---


@click.group()
@click.version_option(package_name="semg")
def main() -> None:
    """[bold]semg[/] — semantic graph for software architecture.

    A CLI tool for agents and humans to sketch, query, and export
    software architecture as a typed graph.
    """
    pass


# --- Mutation commands ---


@main.command()
def init() -> None:
    """Initialize [bold].semg/[/] in the current directory."""
    root = init_project()
    console.print(f"[green]Initialized[/] .semg/ in {root}")


@main.command()
@click.argument("type")
@click.argument("name")
@click.option("--file", "file_", default=None, help="Source file path")
@click.option("--line", default=None, type=int, help="Line number")
@click.option("--doc", default=None, help="Docstring / description")
@click.option("--meta", multiple=True, help="KEY=VALUE metadata (repeatable)")
def add(type: str, name: str, file_: str | None, line: int | None, doc: str | None, meta: tuple[str, ...]) -> None:
    """Add a node to the graph."""
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
    """Add an edge between two nodes."""
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
    """Remove a node and all its edges."""
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
    """Remove an edge."""
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
def update(name: str, type_: str | None, file_: str | None, line: int | None, doc: str | None, meta: tuple[str, ...]) -> None:
    """Update a node's fields."""
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


# --- High-level Explore commands ---


@main.command()
@click.argument("name")
@click.option("--depth", default=1, type=click.IntRange(0, 2), help="Detail level: 0=identity, 1=connections, 2=neighborhood")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def about(name: str, depth: int, fmt: str | None) -> None:
    """What is X? Progressive context card for a node."""
    import json as json_mod

    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    node = graph.get_node(name)
    assert node is not None
    fmt = _auto_fmt(fmt)

    # Always compute these
    node_dict = node.to_dict()
    node_dict.pop("kind", None)

    data: dict = {"node": node_dict}

    if depth >= 1:
        inc = graph.incoming(name)
        out = graph.outgoing(name)
        data["containment_path"] = query.containment_path(graph, name)
        data["incoming"] = [{"source": e.source, "rel": e.rel.value} for e in inc]
        data["outgoing"] = [{"target": e.target, "rel": e.rel.value} for e in out]

    if depth >= 2:
        sub = query.subgraph(graph, name, depth=2)
        neighbor_names = sorted(n for n in sub.nodes if n != name)
        data["neighbors"] = neighbor_names

    if fmt == "json":
        click.echo(json_mod.dumps(data, indent=2))
        return

    # Rich text output
    title = f"[bold]{node.name}[/]  [{_type_badge(node.type.value)}]"
    lines: list[str] = []

    if node.file:
        loc = node.file + (f":{node.line}" if node.line else "")
        lines.append(f"[dim]file:[/]  {loc}")
    if node.docstring:
        lines.append(f"[dim]doc:[/]   {node.docstring.split(chr(10))[0]}")
    if node.metadata:
        for k, v in sorted(node.metadata.items()):
            lines.append(f"[dim]{k}:[/]  {v}")

    if depth >= 1:
        cpath = data["containment_path"]
        if len(cpath) > 1:
            lines.append("")
            lines.append(f"[bold]Path:[/] {' > '.join(cpath)}")

        inc_edges = data["incoming"]
        if inc_edges:
            lines.append("")
            lines.append(f"[bold]Incoming[/] ({len(inc_edges)})")
            for e in inc_edges:
                lines.append(f"  {e['source']} [dim]--{_rel_style(e['rel'])}-->[/]")

        out_edges = data["outgoing"]
        if out_edges:
            lines.append("")
            lines.append(f"[bold]Outgoing[/] ({len(out_edges)})")
            for e in out_edges:
                lines.append(f"  [dim]--{_rel_style(e['rel'])}-->[/] {e['target']}")

    if depth >= 2:
        neighbors = data["neighbors"]
        if neighbors:
            lines.append("")
            lines.append(f"[bold]Neighborhood[/] ({len(neighbors)} nodes within 2 hops)")
            for n in neighbors[:20]:
                nnode = graph.get_node(n)
                t = nnode.type.value if nnode else "?"
                lines.append(f"  [{_type_badge(t)}] {n}")
            if len(neighbors) > 20:
                lines.append(f"  [dim]... and {len(neighbors) - 20} more[/]")

    console.print(Panel("\n".join(lines), title=title, border_style="dim"))


@main.command()
@click.argument("name")
@click.option("--depth", default=None, type=int, help="Max traversal depth")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def impact(name: str, depth: int | None, fmt: str | None) -> None:
    """What breaks if I change X? Reverse transitive impact analysis."""
    import json as json_mod

    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    affected = query.impact(graph, name, max_depth=depth)

    if fmt == "json":
        click.echo(json_mod.dumps({
            "target": name,
            "affected": affected,
            "count": len(affected),
        }, indent=2))
        return

    if not affected:
        console.print(f"[bold]{name}[/]: [dim]no upstream dependents[/]")
        return

    console.print(f"[bold]Impact of changing[/] {name}:")
    for a in affected:
        anode = graph.get_node(a)
        t = anode.type.value if anode else "?"
        console.print(f"  [{_type_badge(t)}] {a}")
    console.print(f"\n[dim]{len(affected)} node(s) affected[/]")


@main.command()
@click.argument("a")
@click.argument("b")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def between(a: str, b: str, fmt: str | None) -> None:
    """How do A and B relate? Shortest path + direct edges."""
    import json as json_mod

    graph, _root = _load()
    a = _resolve_or_exit(graph, a)
    b = _resolve_or_exit(graph, b)
    fmt = _auto_fmt(fmt)

    path = query.shortest_path(graph, a, b)
    # Find direct edges between a and b
    direct = []
    for edge in graph.all_edges():
        if (edge.source == a and edge.target == b) or (edge.source == b and edge.target == a):
            direct.append({"source": edge.source, "rel": edge.rel.value, "target": edge.target})

    if fmt == "json":
        click.echo(json_mod.dumps({
            "source": a,
            "target": b,
            "path": path,
            "direct_edges": direct,
        }, indent=2))
        return

    if path:
        styled = " [dim]->[/] ".join(f"[bold]{p}[/]" for p in path)
        console.print(f"[bold]Path:[/] {styled}")
    else:
        console.print(f"[dim]No path between {a} and {b}[/]")

    if direct:
        console.print(f"\n[bold]Direct edges:[/]")
        for d in direct:
            console.print(f"  {d['source']} [dim]--{_rel_style(d['rel'])}-->[/] {d['target']}")


@main.command()
@click.option("--top", "top_n", default=10, type=int, help="Number of top connected nodes to show")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def overview(top_n: int, fmt: str | None) -> None:
    """Orient me. High-level summary of the graph."""
    import json as json_mod

    graph, _root = _load()
    fmt = _auto_fmt(fmt)
    nodes = graph.all_nodes()
    edges = graph.all_edges()

    # Connectivity ranking
    connectivity: list[dict] = []
    for node in nodes:
        inc = len(graph.incoming(node.name))
        out = len(graph.outgoing(node.name))
        connectivity.append({
            "name": node.name,
            "type": node.type.value,
            "incoming": inc,
            "outgoing": out,
            "total": inc + out,
        })
    connectivity.sort(key=lambda x: x["total"], reverse=True)

    # Module summaries
    modules: list[dict] = []
    for node in nodes:
        if node.type.value in ("module", "package"):
            children = len(graph.outgoing(node.name, rel=RelType.CONTAINS))
            modules.append({"name": node.name, "type": node.type.value, "children": children})
    modules.sort(key=lambda x: x["children"], reverse=True)

    if fmt == "json":
        click.echo(json_mod.dumps({
            "nodes": len(nodes),
            "edges": len(edges),
            "top_connected": connectivity[:top_n],
            "modules": modules,
        }, indent=2))
        return

    # Rich text
    console.print(f"[bold]Graph:[/] {len(nodes)} nodes, {len(edges)} edges\n")

    # Top connected
    table = Table(title=f"[bold]Most Connected[/] (top {top_n})", border_style="dim", pad_edge=False)
    table.add_column("Name", style="bold")
    table.add_column("Type", style="dim")
    table.add_column("In", justify="right")
    table.add_column("Out", justify="right")
    table.add_column("Total", justify="right", style="bold")
    for c in connectivity[:top_n]:
        table.add_row(c["name"], _type_badge(c["type"]), str(c["incoming"]), str(c["outgoing"]), str(c["total"]))
    console.print(table)

    # Modules
    if modules:
        console.print()
        mod_table = Table(title="[bold]Modules[/]", border_style="dim", pad_edge=False)
        mod_table.add_column("Name", style="bold")
        mod_table.add_column("Type", style="dim")
        mod_table.add_column("Children", justify="right")
        for m in modules:
            mod_table.add_row(m["name"], _type_badge(m["type"]), str(m["children"]))
        console.print(mod_table)


# --- Inspect commands ---


@main.command()
@click.argument("name")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def show(name: str, fmt: str | None) -> None:
    """Show a node and its connections."""
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
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def list_nodes(type_: str | None, fmt: str | None) -> None:
    """List all nodes."""
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
        doc = (node.docstring or "").split("\n")[0][:40]
        table.add_row(_type_badge(node.type.value), node.name, loc, doc)

    console.print(table)


@main.command()
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def status(fmt: str | None) -> None:
    """Show graph summary."""
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
    """Query the graph."""
    pass


main.add_command(query_cmd, "query")


@query_cmd.command("deps")
@click.argument("name")
@click.option("--depth", default=None, type=int, help="Max traversal depth")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json", "mermaid", "dot"]), help="Output format (auto-detects: JSON when piped)")
def query_deps(name: str, depth: int | None, fmt: str | None) -> None:
    """Transitive dependencies of a node."""
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    deps = query.transitive_deps(graph, name, max_depth=depth)
    _output_names(deps, f"Dependencies of {name}", fmt, graph, name)


@query_cmd.command("callers")
@click.argument("name")
@click.option("--depth", default=None, type=int, help="Max traversal depth")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json", "mermaid", "dot"]), help="Output format (auto-detects: JSON when piped)")
def query_callers(name: str, depth: int | None, fmt: str | None) -> None:
    """What calls this node (transitively)."""
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    callers = query.transitive_callers(graph, name, max_depth=depth)
    _output_names(callers, f"Callers of {name}", fmt, graph, name)


@query_cmd.command("path")
@click.argument("source")
@click.argument("target")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
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
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json", "mermaid", "dot"]), help="Output format (auto-detects: JSON when piped)")
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
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def query_incoming(name: str, rel: str | None, fmt: str | None) -> None:
    """Incoming edges to a node."""
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    edges = graph.incoming(name, rel=rel)
    _output_edges(edges, fmt)


@query_cmd.command("outgoing")
@click.argument("name")
@click.option("--rel", default=None, help="Filter by relationship type")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def query_outgoing(name: str, rel: str | None, fmt: str | None) -> None:
    """Outgoing edges from a node."""
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    edges = graph.outgoing(name, rel=rel)
    _output_edges(edges, fmt)


# --- Export subgroup ---


@main.group("export")
def export_cmd() -> None:
    """Export the full graph."""
    pass


@export_cmd.command("json")
@click.option("--indent/--no-indent", default=False, help="Pretty print")
def export_json(indent: bool) -> None:
    """Export graph as JSON."""
    graph, _root = _load()
    click.echo(export.to_json(graph, indent=indent))


@export_cmd.command("mermaid")
def export_mermaid() -> None:
    """Export graph as Mermaid flowchart."""
    graph, _root = _load()
    click.echo(export.to_mermaid(graph))


@export_cmd.command("dot")
def export_dot() -> None:
    """Export graph as Graphviz DOT."""
    graph, _root = _load()
    click.echo(export.to_dot(graph))


@export_cmd.command("text")
def export_text() -> None:
    """Export graph as human-readable text."""
    graph, _root = _load()
    click.echo(export.to_text(graph))


# --- Validate ---


@main.command()
def validate() -> None:
    """Check graph integrity."""
    graph, _root = _load()
    issues = graph.validate()
    if not issues:
        console.print("[green]Graph is valid.[/]")
    else:
        err_console.print(f"[red]Found {len(issues)} issue(s):[/]")
        for issue in issues:
            err_console.print(f"  [dim]-[/] {issue}")
        sys.exit(EXIT_VALIDATION)


# --- Diff ---


@main.command()
@click.argument("ref", default="HEAD")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def diff(ref: str, fmt: str | None) -> None:
    """What changed structurally? Compare graph against a git ref.

    Defaults to comparing against HEAD (last commit). Use any git ref:
    HEAD~1, main, a commit hash, etc.
    """
    import json as json_mod

    from semg.diff import GraphDiff, diff_graphs, load_graph_from_git

    graph, root = _load()
    fmt = _auto_fmt(fmt)

    old_graph = load_graph_from_git(root, ref)
    if old_graph is None:
        if fmt == "json":
            # No baseline — treat everything as added
            old_graph = SemGraph()
        else:
            console.print(f"[dim]No graph found at ref [bold]{ref}[/bold]. Showing full graph as new.[/]")
            old_graph = SemGraph()

    result = diff_graphs(old_graph, graph)

    if fmt == "json":
        data: dict = {
            "ref": ref,
            "added_nodes": [n.name for n in result.added_nodes],
            "removed_nodes": [n.name for n in result.removed_nodes],
            "changed_nodes": [
                {"name": node.name, "changes": [{"field": c.field, "old": c.old, "new": c.new} for c in changes]}
                for node, changes in result.changed_nodes
            ],
            "added_edges": [{"source": e.source, "rel": e.rel.value, "target": e.target} for e in result.added_edges],
            "removed_edges": [{"source": e.source, "rel": e.rel.value, "target": e.target} for e in result.removed_edges],
            "summary": {
                "nodes_added": len(result.added_nodes),
                "nodes_removed": len(result.removed_nodes),
                "nodes_changed": len(result.changed_nodes),
                "edges_added": len(result.added_edges),
                "edges_removed": len(result.removed_edges),
            },
        }
        click.echo(json_mod.dumps(data, indent=2))
        return

    if result.is_empty:
        console.print(f"[dim]No structural changes vs {ref}.[/]")
        return

    console.print(f"[bold]Diff vs {ref}[/]\n")

    if result.added_nodes:
        console.print(f"[green]+[/] [bold]{len(result.added_nodes)} node(s) added[/]")
        for node in result.added_nodes:
            console.print(f"  [green]+[/] [{_type_badge(node.type.value)}] {node.name}")

    if result.removed_nodes:
        console.print(f"[red]-[/] [bold]{len(result.removed_nodes)} node(s) removed[/]")
        for node in result.removed_nodes:
            console.print(f"  [red]-[/] [{_type_badge(node.type.value)}] {node.name}")

    if result.changed_nodes:
        console.print(f"[yellow]~[/] [bold]{len(result.changed_nodes)} node(s) changed[/]")
        for node, changes in result.changed_nodes:
            console.print(f"  [yellow]~[/] {node.name}")
            for c in changes:
                console.print(f"      {c.field}: [red]{c.old}[/] → [green]{c.new}[/]")

    if result.added_edges:
        console.print(f"[green]+[/] [bold]{len(result.added_edges)} edge(s) added[/]")
        for e in result.added_edges[:20]:
            console.print(f"  [green]+[/] {e.source} [dim]--{_rel_style(e.rel.value)}-->[/] {e.target}")
        if len(result.added_edges) > 20:
            console.print(f"  [dim]... and {len(result.added_edges) - 20} more[/]")

    if result.removed_edges:
        console.print(f"[red]-[/] [bold]{len(result.removed_edges)} edge(s) removed[/]")
        for e in result.removed_edges[:20]:
            console.print(f"  [red]-[/] {e.source} [dim]--{_rel_style(e.rel.value)}-->[/] {e.target}")
        if len(result.removed_edges) > 20:
            console.print(f"  [dim]... and {len(result.removed_edges) - 20} more[/]")

    # Summary line
    parts = []
    if result.added_nodes:
        parts.append(f"[green]+{len(result.added_nodes)}[/]")
    if result.removed_nodes:
        parts.append(f"[red]-{len(result.removed_nodes)}[/]")
    if result.changed_nodes:
        parts.append(f"[yellow]~{len(result.changed_nodes)}[/]")
    console.print(f"\n[dim]Nodes: {', '.join(parts)} | Edges: +{len(result.added_edges)} -{len(result.removed_edges)}[/]")


# --- Scan ---


@main.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option("--clean", is_flag=True, help="Remove scan-sourced nodes from scanned files before repopulating")
@click.option("--changed", is_flag=True, help="Only rescan files changed since last commit (implies --clean)")
@click.option("--since", default=None, help="Only rescan files changed since REF (implies --clean)")
@click.option("--exclude", multiple=True, help="Additional exclude patterns (repeatable)")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def scan(paths: tuple[str, ...], clean: bool, changed: bool, since: str | None, exclude: tuple[str, ...], fmt: str | None) -> None:
    """Scan source files with tree-sitter and populate the graph.

    Use --changed to only rescan files modified since the last commit.
    Use --since REF to rescan files changed since a specific git ref.
    Both imply --clean (scoped to changed files only).
    """
    try:
        from semg.scan import changed_files, scan_paths
    except ImportError:
        err_console.print(
            "[red]Error:[/] tree-sitter not installed. Install with: [bold]uv pip install semg\\[scan][/]"
        )
        sys.exit(EXIT_VALIDATION)

    graph, root = _load()

    if changed or since:
        ref = since or "HEAD"
        file_list = changed_files(root, ref)
        if not file_list:
            fmt = _auto_fmt(fmt)
            if fmt == "json":
                import json
                click.echo(json.dumps({"files": 0, "message": f"no supported files changed since {ref}"}))
            else:
                console.print(f"[dim]No supported files changed since {ref}.[/]")
            return
        scan_dirs = file_list
        clean = True  # --changed implies --clean
    else:
        scan_dirs = [Path(p).resolve() for p in paths] if paths else [Path.cwd().resolve()]

    stats = scan_paths(graph, root, scan_dirs, clean=clean, excludes=list(exclude) or None)
    save_graph(graph, root)
    fmt = _auto_fmt(fmt)

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
        table.add_row("Nodes", f"+{stats.nodes_added} -{stats.nodes_removed}" if stats.nodes_removed else type_parts)
        table.add_row("Edges", f"+{stats.edges_added} -{stats.edges_removed}" if stats.edges_removed else str(stats.edges_added))
        if stats.skipped_edges:
            table.add_row("Skipped", f"{stats.skipped_edges} unresolved")
        console.print(table)

        if stats.orphaned_manual_edges:
            console.print(f"\n[yellow]Warning:[/] {len(stats.orphaned_manual_edges)} manual edge(s) orphaned:")
            for oe in stats.orphaned_manual_edges:
                console.print(f"  {oe['source']} [dim]--{oe['rel']}-->[/] {oe['target']} [dim]({oe['reason']})[/]")


# --- Batch ---


@main.command()
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def batch(fmt: str | None) -> None:
    """Execute JSONL commands from stdin in one load/save cycle.

    Each line is a JSON object with an "op" field and operation-specific fields.
    This is much faster than running individual CLI commands when making many
    mutations, since the graph is only loaded and saved once.

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
                stats["ops"].append({"line": line_no, "op": "link", "source": cmd["source"], "target": cmd["target"]})

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


# --- Output helpers ---


def _output_names(names: list[str], title: str, fmt: str, graph: SemGraph, center: str) -> None:
    if fmt == "json":
        import json

        click.echo(json.dumps(names))
    elif fmt in ("mermaid", "dot"):
        sub = SemGraph()
        center_node = graph.get_node(center)
        if center_node:
            sub.add_node(center_node)
        for n in names:
            node = graph.get_node(n)
            if node:
                sub.add_node(node)
        for edge in graph.all_edges():
            if edge.source in sub.nodes and edge.target in sub.nodes:
                sub.add_edge(edge)
        if fmt == "mermaid":
            click.echo(export.to_mermaid(sub))
        else:
            click.echo(export.to_dot(sub))
    else:
        if not names:
            console.print(f"[bold]{title}:[/] [dim](none)[/]")
        else:
            console.print(f"[bold]{title}:[/]")
            for n in names:
                console.print(f"  {n}")


def _output_graph(graph: SemGraph, fmt: str) -> None:
    if fmt == "json":
        click.echo(export.to_json(graph, indent=True))
    elif fmt == "mermaid":
        click.echo(export.to_mermaid(graph))
    elif fmt == "dot":
        click.echo(export.to_dot(graph))
    else:
        click.echo(export.to_text(graph))


def _output_edges(edges: list[Edge], fmt: str) -> None:
    if fmt == "json":
        import json

        data = [e.to_dict() for e in edges]
        for d in data:
            d.pop("kind", None)
        click.echo(json.dumps(data, indent=2))
    else:
        if not edges:
            console.print("[dim](none)[/]")
        else:
            for e in edges:
                console.print(f"  {e.source} [dim]--{_rel_style(e.rel.value)}-->[/] {e.target}")
