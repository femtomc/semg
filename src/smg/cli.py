from __future__ import annotations

import sys
from pathlib import Path

import rich_click as click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from smg import export, query
from smg.graph import NodeNotFoundError, SemGraph
from smg.model import Edge, Node, NodeType, RelType
from smg.storage import find_root, init_project, load_graph, save_graph

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
    "smg": [
        {"name": "Explore", "commands": ["about", "usages", "impact", "between", "overview", "diff", "analyze"]},
        {"name": "Enforce", "commands": ["rule", "check"]},
        {"name": "Inspect", "commands": ["show", "list", "status", "query", "validate"]},
        {"name": "Mutate", "commands": ["init", "add", "link", "rm", "unlink", "update", "scan", "watch", "batch"]},
        {"name": "Export", "commands": ["export"]},
    ],
}


def _load() -> tuple[SemGraph, Path]:
    root = find_root()
    if root is None:
        err_console.print("[red]Error:[/] no .smg/ found. Run [bold]smg init[/] first.")
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
@click.version_option(package_name="smg")
def main() -> None:
    """[bold]smg[/] — semantic graph for software architecture.

    Turns your codebase into a queryable graph of modules, classes, functions,
    and their relationships. Built for agents and humans.

    \b
    Quick start:
      smg init              # initialize in any project
      smg scan src/         # auto-populate from source (Python/JS/TS/Zig)
      smg about MyClass     # ask questions
      smg analyze           # deep architectural analysis

    Output is automatically JSON when piped, rich text in terminal.
    """
    pass


# --- Mutation commands ---


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
def add(type: str, name: str, file_: str | None, line: int | None, doc: str | None, meta: tuple[str, ...]) -> None:
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
def update(name: str, type_: str | None, file_: str | None, line: int | None, doc: str | None, meta: tuple[str, ...]) -> None:
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


# --- High-level Explore commands ---


@main.command()
@click.argument("name")
@click.option("--depth", default=1, type=click.IntRange(0, 2), help="Detail level: 0=identity, 1=connections, 2=neighborhood")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def about(name: str, depth: int, fmt: str | None) -> None:
    """What is X? Progressive context card for a node.

    \b
    Depth controls how much detail:
      --depth 0  Identity only (name, type, file, docstring)
      --depth 1  + connections (incoming/outgoing edges, containment path)
      --depth 2  + 2-hop neighborhood (all nearby nodes)
    Short names work: smg about SemGraph → smg.graph.SemGraph
    """
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
        loc = node.file
        if node.line is not None:
            loc += f":{node.line}"
            if node.end_line is not None and node.end_line != node.line:
                loc += f"-{node.end_line}"
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
@click.option("--rel", default=None, help="Filter by relationship type (e.g. calls, imports, inherits)")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def usages(name: str, rel: str | None, fmt: str | None) -> None:
    """Where is X used? Every direct reference with source location.

    Shows all nodes that reference X via coupling edges (calls, imports,
    inherits, implements, depends_on), with the file and line range of
    each caller so you can jump straight to the usage site.

    \b
    Examples:
      smg usages SemGraph               # all usages of SemGraph
      smg usages add_node --rel calls   # only call sites
    """
    import json as json_mod

    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)

    coupling_rels = {"calls", "imports", "inherits", "implements", "depends_on"}
    usage_list: list[dict] = []

    for edge in graph.incoming(name, rel=rel):
        if rel is not None or edge.rel.value in coupling_rels:
            source_node = graph.get_node(edge.source)
            entry: dict = {
                "node": edge.source,
                "rel": edge.rel.value,
            }
            if source_node:
                if source_node.file:
                    entry["file"] = source_node.file
                if source_node.line is not None:
                    entry["line"] = source_node.line
                if source_node.end_line is not None:
                    entry["end_line"] = source_node.end_line
            usage_list.append(entry)

    usage_list.sort(key=lambda u: (u.get("file", ""), u.get("line", 0)))

    if fmt == "json":
        click.echo(json_mod.dumps({
            "target": name,
            "usages": usage_list,
            "count": len(usage_list),
        }, indent=2))
        return

    if not usage_list:
        console.print(f"[bold]{name}[/]: [dim]no usages found[/]")
        return

    console.print(f"[bold]Usages of[/] {name} ({len(usage_list)}):\n")
    table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
    table.add_column("Rel", style="dim", width=10)
    table.add_column("Node", style="bold")
    table.add_column("File", style="dim")

    for u in usage_list:
        loc = u.get("file", "")
        if "line" in u:
            loc += f":{u['line']}"
            if "end_line" in u and u["end_line"] != u["line"]:
                loc += f"-{u['end_line']}"
        table.add_row(_rel_style(u["rel"]), u["node"], loc)

    console.print(table)


@main.command()
@click.argument("name")
@click.option("--depth", default=None, type=int, help="Max traversal depth")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def impact(name: str, depth: int | None, fmt: str | None) -> None:
    """What breaks if I change X? Reverse transitive impact analysis.

    Follows ALL incoming edges (calls, imports, contains, etc.) transitively
    to find every node that could be affected by a change to X.

    \b
    Example: smg impact auth.service
    """
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
    """How do A and B relate? Shortest path + direct edges.

    \b
    Example: smg between api.routes db.models
    """
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
    """Orient me. High-level summary of the graph.

    Shows node/edge counts, the most connected nodes (by total incoming +
    outgoing edges), and modules ranked by size.
    """
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
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
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
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
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
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json", "mermaid", "dot"]), help="Output format (auto-detects: JSON when piped)")
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
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json", "mermaid", "dot"]), help="Output format (auto-detects: JSON when piped)")
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
    """Incoming edges to a node. Filter with --rel calls, --rel imports, etc."""
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
    """Outgoing edges from a node. Filter with --rel calls, --rel imports, etc."""
    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    edges = graph.outgoing(name, rel=rel)
    _output_edges(edges, fmt)


# --- Export subgroup ---


@main.group("export")
def export_cmd() -> None:
    """Export the full graph in various formats.

    \b
    Examples:
      smg export mermaid                         # paste into markdown
      smg export dot | dot -Tpng -o graph.png    # render with Graphviz
      smg export json --indent > graph.json      # machine-readable
    """
    pass


@export_cmd.command("json")
@click.option("--indent/--no-indent", default=False, help="Pretty print")
def export_json(indent: bool) -> None:
    """Export graph as JSON ({nodes: [...], edges: [...]})."""
    graph, _root = _load()
    click.echo(export.to_json(graph, indent=indent))


@export_cmd.command("mermaid")
def export_mermaid() -> None:
    """Export graph as Mermaid flowchart (paste into markdown or live editor)."""
    graph, _root = _load()
    click.echo(export.to_mermaid(graph))


@export_cmd.command("dot")
def export_dot() -> None:
    """Export graph as Graphviz DOT (pipe to dot, neato, fdp, etc.)."""
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

    from smg.diff import GraphDiff, diff_graphs, load_graph_from_git

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


# --- Analyze ---


@main.command()
@click.option("--top", "top_n", default=10, type=int, help="Number of top entries to show per ranking")
@click.option("--module", "module_filter", default=None, help="Scope analysis to nodes under this module/package prefix")
@click.option("--summary", is_flag=True, help="Show only hotspots and key findings, skip full listings")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def analyze(top_n: int, module_filter: str | None, summary: bool, fmt: str | None) -> None:
    """Deep architectural analysis with hotspot detection.

    \b
    Runs: cycle detection, PageRank, betweenness centrality, k-core,
    CK class metrics (WMC/CBO/RFC/LCOM4/DIT/NOC), Martin's package
    metrics (I/A/D), SDP violations, fan-in/fan-out, dead code detection,
    layering violations, code smells (God Class, Feature Envy, Shotgun
    Surgery), and synthesized hotspot ranking.

    \b
    Examples:
      smg analyze                        # full analysis
      smg analyze --module bellman       # scope to bellman.* nodes
      smg analyze --summary --top 5     # just hotspots and key findings
      smg analyze --format json         # structured output for agents

    \b
    JSON output keys: hotspots, graph, pagerank, betweenness,
    kcore (with members), classes, modules, sdp_violations,
    fan_in_out, dead_code, layering_violations, smells
    """
    import json as json_mod

    from smg import graph_metrics, oo_metrics

    graph, _root = _load()
    fmt = _auto_fmt(fmt)

    # Scope to a module prefix if requested
    if module_filter:
        from smg.query import subgraph as _subgraph
        # Build a scoped graph: all nodes whose name starts with the prefix
        scoped = SemGraph()
        prefix = module_filter if module_filter.endswith(".") else module_filter + "."
        for node in graph.all_nodes():
            if node.name == module_filter or node.name.startswith(prefix):
                scoped.add_node(node)
        for edge_obj in graph.all_edges():
            if edge_obj.source in scoped.nodes and edge_obj.target in scoped.nodes:
                scoped.add_edge(edge_obj)
        graph = scoped

    use_progress = fmt == "text" and sys.stdout.isatty()
    if use_progress:
        from rich.progress import Progress, SpinnerColumn, TextColumn
        progress = Progress(SpinnerColumn(), TextColumn("[bold]{task.description}"), console=console, transient=True)
        progress.start()
        task_id = progress.add_task("Analyzing...")

    def _step(desc: str):
        if use_progress:
            progress.update(task_id, description=desc)

    # Graph-theoretic
    _step("Finding cycles...")
    cycles = graph_metrics.find_cycles(graph)
    _step("Computing layers...")
    layers = graph_metrics.topological_layers(graph)
    _step("Computing PageRank...")
    pr = graph_metrics.pagerank(graph)
    _step("Computing betweenness centrality...")
    bc = graph_metrics.betweenness_centrality(graph)
    _step("Computing k-core decomposition...")
    kc = graph_metrics.kcore_decomposition(graph)
    _step("Detecting bridges...")
    bridges = graph_metrics.detect_bridges(graph)

    # OO metrics
    _step("Computing class metrics (CK suite)...")
    wmc_data = oo_metrics.wmc(graph)
    dit_data = oo_metrics.dit(graph)
    noc_data = oo_metrics.noc(graph)
    cbo_data = oo_metrics.cbo(graph)
    rfc_data = oo_metrics.rfc(graph)
    lcom_data = oo_metrics.lcom4(graph)
    _step("Computing module metrics (Martin)...")
    martin_data = oo_metrics.martin_metrics(graph)
    _step("Checking SDP violations...")
    sdp = oo_metrics.sdp_violations(graph)
    _step("Computing fan-in/fan-out...")
    fio = graph_metrics.fan_in_out(graph)
    _step("Detecting dead code...")
    dead = graph_metrics.dead_code(graph)
    _step("Checking layering violations...")
    layer_violations = graph_metrics.layering_violations(graph)
    _step("Detecting code smells...")
    gods = oo_metrics.god_classes(graph)
    envy = oo_metrics.feature_envy(graph)
    shotgun = oo_metrics.shotgun_surgery(graph)
    _step("Computing hotspots...")

    if use_progress:
        progress.stop()

    # Summaries
    max_layer = max(layers.values()) if layers else 0
    max_k = max(kc.values()) if kc else 0
    core_members = sorted(n for n, k in kc.items() if k == max_k) if kc else []
    pr_top = sorted(pr.items(), key=lambda x: x[1], reverse=True)[:top_n]
    bc_top = sorted(bc.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # --- Hotspot synthesis ---
    # Composite score: normalize and combine PageRank, betweenness, WMC, CBO, LCOM4
    hotspots: list[dict] = []
    # Collect class-level hotspots
    for name in wmc_data:
        score = 0.0
        reasons: list[str] = []
        w = wmc_data.get(name, 0)
        c = cbo_data.get(name, 0)
        l = lcom_data.get(name, 0)
        r = rfc_data.get(name, 0)
        b = bc.get(name, 0.0)
        p = pr.get(name, 0.0)
        if w > 20:
            score += w / 10
            reasons.append(f"high complexity (WMC={w})")
        if c > 5:
            score += c
            reasons.append(f"high coupling (CBO={c})")
        if l > 1:
            score += l * 3
            reasons.append(f"low cohesion (LCOM4={l})")
        if r > 20:
            score += r / 5
            reasons.append(f"large response set (RFC={r})")
        if b > 0.05:
            score += b * 20
            reasons.append(f"structural bottleneck (BC={b:.3f})")
        if p > 0.02:
            score += p * 50
            reasons.append(f"high importance (PR={p:.4f})")
        if reasons:
            hotspots.append({"name": name, "type": "class", "score": round(score, 2), "reasons": reasons})

    # Collect module-level hotspots (high distance from main sequence)
    for name, m in martin_data.items():
        if m["distance"] > 0.7:
            hotspots.append({
                "name": name, "type": "module", "score": round(m["distance"] * 5, 2),
                "reasons": [f"far from main sequence (D={m['distance']}, I={m['instability']}, A={m['abstractness']})"],
            })

    hotspots.sort(key=lambda h: h["score"], reverse=True)

    if fmt == "json":
        data: dict = {
            "hotspots": hotspots[:top_n],
            "graph": {
                "nodes": len(graph),
                "edges": len(graph.all_edges()),
                "cycles": cycles,
                "cycle_count": len(cycles),
                "max_layer": max_layer,
                "bridge_count": len(bridges),
                "bridges": [list(b) for b in bridges[:top_n]],
            },
            "pagerank": [{"name": n, "rank": round(r, 6)} for n, r in pr_top],
            "betweenness": [{"name": n, "centrality": round(c, 6)} for n, c in bc_top],
            "kcore": {"max_coreness": max_k, "core_size": len(core_members), "members": core_members[:top_n]},
        }
        if not summary:
            data["classes"] = {
                name: {
                    "wmc": wmc_data.get(name, 0), "dit": dit_data.get(name, 0),
                    "noc": noc_data.get(name, 0), "cbo": cbo_data.get(name, 0),
                    "rfc": rfc_data.get(name, 0), "lcom4": lcom_data.get(name, 0),
                }
                for name in sorted(wmc_data.keys())
            }
            data["modules"] = martin_data
        data["sdp_violations"] = sdp
        data["dead_code"] = dead
        data["layering_violations"] = layer_violations
        data["smells"] = {
            "god_classes": gods,
            "feature_envy": envy,
            "shotgun_surgery": shotgun,
        }
        if not summary:
            fio_top = sorted(fio.items(), key=lambda x: x[1]["fan_in"] + x[1]["fan_out"], reverse=True)[:top_n]
            data["fan_in_out"] = [{"name": n, **v} for n, v in fio_top]
        click.echo(json_mod.dumps(data, indent=2))
        return

    # --- Rich text output ---

    scope_label = f" [dim](scoped to {module_filter})[/]" if module_filter else ""
    console.print(f"\n[bold]Analysis[/]{scope_label} — {len(graph)} nodes, {len(graph.all_edges())} edges")

    # Hotspots (always shown)
    if hotspots:
        console.print(f"\n[red bold]Hotspots[/] (top problem areas)")
        hs_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        hs_table.add_column("#", style="dim", width=3)
        hs_table.add_column("Name", style="bold")
        hs_table.add_column("Score", justify="right")
        hs_table.add_column("Issues")
        for i, h in enumerate(hotspots[:top_n], 1):
            hs_table.add_row(str(i), h["name"], str(h["score"]), "; ".join(h["reasons"]))
        console.print(hs_table)
    else:
        console.print("\n[green]No hotspots detected[/]")

    # Cycles
    if cycles:
        console.print(f"\n[red bold]Circular Dependencies[/] ({len(cycles)} cycle(s))")
        for cycle in cycles[:5]:
            console.print(f"  [red]-[/] {' -> '.join(cycle)}")
        if len(cycles) > 5:
            console.print(f"  [dim]... and {len(cycles) - 5} more[/]")
    else:
        console.print("\n[green]No circular dependencies[/]")

    # SDP violations
    if sdp:
        console.print(f"\n[red bold]SDP Violations[/] ({len(sdp)})")
        for v in sdp[:5]:
            console.print(
                f"  [red]-[/] {v['source']} [dim](I={v['source_instability']})[/]"
                f" depends on {v['target']} [dim](I={v['target_instability']})[/]"
            )
    else:
        console.print(f"\n[green]No SDP violations[/]")

    # Dead code
    if dead:
        console.print(f"\n[yellow bold]Dead Code[/] ({len(dead)} unreferenced node(s))")
        for name in dead[:top_n]:
            node = graph.get_node(name)
            type_label = node.type.value if node else "?"
            console.print(f"  [yellow]-[/] {name} [dim]({type_label})[/]")
        if len(dead) > top_n:
            console.print(f"  [dim]... and {len(dead) - top_n} more[/]")
    else:
        console.print("\n[green]No dead code detected[/]")

    # Layering violations
    if layer_violations:
        console.print(f"\n[yellow bold]Layering Violations[/] ({len(layer_violations)} back-dependency edge(s))")
        for v in layer_violations[:top_n]:
            console.print(
                f"  [yellow]-[/] {v['source']} [dim](L{v['source_layer']})[/]"
                f" --{v['rel']}--> {v['target']} [dim](L{v['target_layer']})[/]"
            )
        if len(layer_violations) > top_n:
            console.print(f"  [dim]... and {len(layer_violations) - top_n} more[/]")

    # Code smells
    if gods or envy or shotgun:
        smell_count = len(gods) + len(envy) + len(shotgun)
        console.print(f"\n[red bold]Code Smells[/] ({smell_count})")
        for gc in gods[:3]:
            console.print(f"  [red]God Class:[/] {gc['name']} [dim](WMC={gc['wmc']}, CBO={gc['cbo']}, LCOM4={gc['lcom4']})[/]")
        for fe in envy[:3]:
            console.print(f"  [red]Feature Envy:[/] {fe['method']} envies {fe['envied_class']} [dim]({fe['envied_refs']} refs vs {fe['own_refs']} own)[/]")
        for ss in shotgun[:3]:
            console.print(f"  [red]Shotgun Surgery:[/] {ss['name']} [dim](fan-out={ss['fan_out']})[/]")

    if summary:
        # Summary mode: just hotspots + cycles + violations, done
        console.print(f"\n[dim]Architecture depth: {max_layer + 1} layers | Core: {len(core_members)} nodes (k={max_k}) | Bridges: {len(bridges)}[/]")
        return

    # --- Full output below (non-summary) ---

    console.print(f"\n[bold]Architecture Depth:[/] {max_layer + 1} layers")

    # PageRank
    console.print(f"\n[bold]Most Important (PageRank)[/]")
    pr_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
    pr_table.add_column("#", style="dim", width=3)
    pr_table.add_column("Name", style="bold")
    pr_table.add_column("Rank", justify="right")
    for i, (name, rank) in enumerate(pr_top, 1):
        pr_table.add_row(str(i), name, f"{rank:.4f}")
    console.print(pr_table)

    # Betweenness
    bc_nonzero = [(n, c) for n, c in bc_top if c > 0]
    if bc_nonzero:
        console.print(f"\n[bold]Structural Bottlenecks (Betweenness)[/]")
        bc_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        bc_table.add_column("#", style="dim", width=3)
        bc_table.add_column("Name", style="bold")
        bc_table.add_column("Centrality", justify="right")
        for i, (name, cent) in enumerate(bc_nonzero[:top_n], 1):
            bc_table.add_row(str(i), name, f"{cent:.4f}")
        console.print(bc_table)

    # K-core with members
    if core_members:
        console.print(f"\n[bold]Core Structure[/] (k={max_k}, {len(core_members)} nodes)")
        for n in core_members[:top_n]:
            console.print(f"  {n}")
        if len(core_members) > top_n:
            console.print(f"  [dim]... and {len(core_members) - top_n} more[/]")

    # Bridges
    if bridges:
        console.print(f"\n[yellow]Fragile Connections:[/] {len(bridges)} bridge edge(s)")
        for a, b in bridges[:5]:
            console.print(f"  [yellow]-[/] {a} -- {b}")

    # Class metrics
    if wmc_data:
        console.print(f"\n[bold]Class Metrics (CK Suite)[/]")
        ck_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        ck_table.add_column("Class", style="bold")
        ck_table.add_column("WMC", justify="right")
        ck_table.add_column("CBO", justify="right")
        ck_table.add_column("RFC", justify="right")
        ck_table.add_column("LCOM4", justify="right")
        ck_table.add_column("DIT", justify="right")
        ck_table.add_column("NOC", justify="right")
        for name in sorted(wmc_data.keys(), key=lambda n: wmc_data[n], reverse=True)[:top_n]:
            lcom_val = lcom_data.get(name, 0)
            lcom_str = f"[red]{lcom_val}[/]" if lcom_val > 1 else str(lcom_val)
            ck_table.add_row(
                name,
                str(wmc_data.get(name, 0)), str(cbo_data.get(name, 0)),
                str(rfc_data.get(name, 0)), lcom_str,
                str(dit_data.get(name, 0)), str(noc_data.get(name, 0)),
            )
        console.print(ck_table)

    # Module metrics
    if martin_data:
        console.print(f"\n[bold]Module Metrics (Martin)[/]")
        mod_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        mod_table.add_column("Module", style="bold")
        mod_table.add_column("Ca", justify="right")
        mod_table.add_column("Ce", justify="right")
        mod_table.add_column("I", justify="right")
        mod_table.add_column("A", justify="right")
        mod_table.add_column("D", justify="right")
        for name in sorted(martin_data.keys()):
            m = martin_data[name]
            d_str = f"[red]{m['distance']}[/]" if m["distance"] > 0.7 else str(m["distance"])
            mod_table.add_row(
                name, str(m["ca"]), str(m["ce"]),
                str(m["instability"]), str(m["abstractness"]), d_str,
            )
        console.print(mod_table)

    # Fan-in/Fan-out (top nodes by total coupling)
    if fio:
        console.print(f"\n[bold]Fan-In / Fan-Out (top {top_n})[/]")
        fio_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        fio_table.add_column("Name", style="bold")
        fio_table.add_column("Fan-In", justify="right")
        fio_table.add_column("Fan-Out", justify="right")
        fio_table.add_column("Total", justify="right")
        fio_sorted = sorted(fio.items(), key=lambda x: x[1]["fan_in"] + x[1]["fan_out"], reverse=True)
        for name, vals in fio_sorted[:top_n]:
            total = vals["fan_in"] + vals["fan_out"]
            fio_table.add_row(name, str(vals["fan_in"]), str(vals["fan_out"]), str(total))
        console.print(fio_table)


# --- Rules & Check ---


@main.group()
def rule() -> None:
    """Declare, list, and remove architectural rules."""
    pass


@rule.command("add")
@click.argument("name")
@click.option("--deny", "deny_pattern", default=None, help='Path denial pattern: "source_glob -[rel]-> target_glob"')
@click.option("--invariant", default=None, type=click.Choice(["no-cycles", "no-dead-code", "no-layering-violations"]), help="Structural invariant to enforce")
@click.option("--entry-points", default=None, help="Comma-separated entry points for no-dead-code (supports globs)")
def rule_add(name: str, deny_pattern: str | None, invariant: str | None, entry_points: str | None) -> None:
    """Add an architectural rule.

    \b
    Examples:
      smg rule add layering --deny "core.* -> ui.*"
      smg rule add no-db-calls --deny "api.* -[calls]-> db.*"
      smg rule add acyclic --invariant no-cycles
      smg rule add reachable --invariant no-dead-code --entry-points "main,cli.*"
    """
    from smg.rules import Rule, parse_deny_pattern
    from smg.storage import load_rules, save_rules

    if deny_pattern and invariant:
        err_console.print("[red]Error:[/] specify --deny or --invariant, not both.")
        sys.exit(EXIT_VALIDATION)
    if not deny_pattern and not invariant:
        err_console.print("[red]Error:[/] specify --deny or --invariant.")
        sys.exit(EXIT_VALIDATION)

    if deny_pattern:
        try:
            parse_deny_pattern(deny_pattern)
        except ValueError as e:
            err_console.print(f"[red]Error:[/] {e}")
            sys.exit(EXIT_VALIDATION)
        new_rule = Rule(name=name, type="deny", pattern=deny_pattern)
    else:
        params: dict = {}
        if entry_points:
            params["entry_points"] = entry_points
        new_rule = Rule(name=name, type="invariant", invariant=invariant, params=params)

    _graph, root = _load()
    rules = load_rules(root)
    if any(r.name == name for r in rules):
        err_console.print(f"[red]Error:[/] rule {name!r} already exists. Remove it first with [bold]smg rule rm {name}[/].")
        sys.exit(EXIT_VALIDATION)
    rules.append(new_rule)
    save_rules(rules, root)
    console.print(f"Rule {name!r} added.")


@rule.command("list")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format")
def rule_list(fmt: str | None) -> None:
    """List all architectural rules."""
    import json as json_mod

    from smg.storage import load_rules

    _graph, root = _load()
    rules = load_rules(root)
    fmt = _auto_fmt(fmt)

    if fmt == "json":
        click.echo(json_mod.dumps([r.to_dict() for r in rules], indent=2))
        return

    if not rules:
        console.print("No rules defined. Add one with [bold]smg rule add[/].")
        return

    table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Constraint")
    for r in rules:
        constraint = r.pattern if r.type == "deny" else r.invariant
        if r.params:
            param_str = ", ".join(f"{k}={v}" for k, v in r.params.items())
            constraint = f"{constraint} ({param_str})"
        table.add_row(r.name, r.type, constraint)
    console.print(table)


@rule.command("rm")
@click.argument("name")
def rule_rm(name: str) -> None:
    """Remove an architectural rule by name."""
    from smg.storage import load_rules, save_rules

    _graph, root = _load()
    rules = load_rules(root)
    new_rules = [r for r in rules if r.name != name]
    if len(new_rules) == len(rules):
        err_console.print(f"[red]Error:[/] rule {name!r} not found.")
        sys.exit(EXIT_NOT_FOUND)
    save_rules(new_rules, root)
    console.print(f"Rule {name!r} removed.")


@main.command()
@click.argument("name", required=False, default=None)
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format")
def check(name: str | None, fmt: str | None) -> None:
    """Check architectural rules against the current graph.

    \b
    With no argument, checks all rules. With NAME, checks only that rule.
    Exit code 0 if all rules pass, 1 if any are violated.

    \b
    Examples:
      smg check                  # check all rules
      smg check layering         # check a specific rule
      smg check --format json    # structured output for agents
    """
    import json as json_mod

    from smg.rules import check_all, check_rule
    from smg.storage import load_rules

    graph, root = _load()
    rules = load_rules(root)
    fmt = _auto_fmt(fmt)

    if not rules:
        if fmt == "json":
            click.echo(json_mod.dumps({"rules": [], "violations": [], "status": "no_rules"}))
        else:
            console.print("No rules defined. Add one with [bold]smg rule add[/].")
        return

    if name:
        matched = [r for r in rules if r.name == name]
        if not matched:
            err_console.print(f"[red]Error:[/] rule {name!r} not found.")
            sys.exit(EXIT_NOT_FOUND)
        rules = matched

    violations = check_all(rules, graph)

    if fmt == "json":
        data = {
            "rules_checked": len(rules),
            "violations": [v.to_dict() for v in violations],
            "status": "fail" if violations else "pass",
        }
        click.echo(json_mod.dumps(data, indent=2))
    else:
        for r in rules:
            v = next((v for v in violations if v.rule_name == r.name), None)
            if v is None:
                console.print(f"[green]PASS[/]  {r.name}")
            else:
                console.print(f"[red]FAIL[/]  {r.name}: {v.message}")
                if v.edges:
                    for e in v.edges[:10]:
                        rel = e.get("rel", "?")
                        console.print(f"        {e['source']} --{rel}--> {e['target']}")
                    if len(v.edges) > 10:
                        console.print(f"        [dim]... and {len(v.edges) - 10} more[/]")
                if v.nodes:
                    for n in v.nodes[:10]:
                        console.print(f"        {n}")
                    if len(v.nodes) > 10:
                        console.print(f"        [dim]... and {len(v.nodes) - 10} more[/]")
                if v.cycles:
                    for cycle in v.cycles[:5]:
                        console.print(f"        {' -> '.join(cycle)}")
                    if len(v.cycles) > 5:
                        console.print(f"        [dim]... and {len(v.cycles) - 5} more[/]")

    if violations:
        sys.exit(EXIT_NOT_FOUND)


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
        err_console.print(
            "[red]Error:[/] tree-sitter not installed. Install with: [bold]uv pip install smg\\[scan][/]"
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

    fmt = _auto_fmt(fmt)

    # Progress callback for text mode
    progress_cb = None
    if fmt == "text" and sys.stdout.isatty():
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=30),
            MofNCompleteColumn(),
            console=console,
            transient=True,
        )
        progress.start()
        task_id = progress.add_task("Scanning...", total=None)

        def progress_cb(current, total, file_path):
            progress.update(task_id, total=total, completed=current, description=f"[dim]{file_path}[/]")

    stats = scan_paths(graph, root, scan_dirs, clean=clean, excludes=list(exclude) or None, on_progress=progress_cb)

    if progress_cb is not None:
        progress.stop()

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
        table.add_row("Nodes", f"+{stats.nodes_added} -{stats.nodes_removed}" if stats.nodes_removed else type_parts)
        table.add_row("Edges", f"+{stats.edges_added} -{stats.edges_removed}" if stats.edges_removed else str(stats.edges_added))
        if stats.skipped_edges:
            table.add_row("Skipped", f"{stats.skipped_edges} unresolved")
        console.print(table)

        if stats.orphaned_manual_edges:
            console.print(f"\n[yellow]Warning:[/] {len(stats.orphaned_manual_edges)} manual edge(s) orphaned:")
            for oe in stats.orphaned_manual_edges:
                console.print(f"  {oe['source']} [dim]--{oe['rel']}-->[/] {oe['target']} [dim]({oe['reason']})[/]")


# --- Watch ---


@main.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option("--debounce", default=0.5, type=float, help="Seconds to wait before rescanning after a change")
def watch(paths: tuple[str, ...], debounce: float) -> None:
    """Watch source files and auto-rescan on changes.

    Monitors the filesystem for changes to supported source files and
    triggers an incremental rescan (with --clean) automatically. Runs
    until interrupted with Ctrl+C.
    """
    try:
        from smg.watch import watch_and_scan
    except ImportError:
        err_console.print(
            "[red]Error:[/] watchdog not installed. Install with: [bold]uv pip install smg\\[scan][/]"
        )
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


# --- Batch ---


@main.command()
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
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
