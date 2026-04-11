from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import rich_click as click

if TYPE_CHECKING:
    from smg.analyze import AnalysisResult
    from smg.concepts import ConceptAnalysis
from rich.panel import Panel
from rich.table import Table

from smg import query
from smg.cli import (
    _DEFAULT_LIMIT,
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
    _auto_fmt,
    _compact_table,
    _load,
    _rel_style,
    _resolve_or_exit,
    _scope_graph,
    _type_badge,
    console,
    err_console,
    main,
)
from smg.graph import SemGraph
from smg.model import RelType


@main.command()
@click.argument("name")
@click.option(
    "--depth",
    default=1,
    type=click.IntRange(0, 2),
    help="Detail level: 0=identity, 1=connections, 2=neighborhood",
)
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
@click.option(
    "--coupling-only/--all-rels",
    default=True,
    help="Show only architecture-significant coupling edges by default",
)
@click.option("--full", is_flag=True, help="Show all edges and neighbors (no truncation)")
def about(name: str, depth: int, fmt: str | None, coupling_only: bool, full: bool) -> None:
    """What is X? Progressive context card for a node.

    \b
    Depth controls how much detail:
      --depth 0  Identity only (name, type, file, docstring)
      --depth 1  + connections (incoming/outgoing edges, containment path)
      --depth 2  + 2-hop neighborhood (all nearby nodes)
    By default, only coupling edges are shown. Use [bold]--all-rels[/] to include
    containment and annotation edges.
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
    rel_types = set(query.COUPLING_RELS) if coupling_only else None

    if depth >= 1:
        inc = [edge for edge in graph.incoming(name) if rel_types is None or edge.rel.value in rel_types]
        out = [edge for edge in graph.outgoing(name) if rel_types is None or edge.rel.value in rel_types]
        data["containment_path"] = query.containment_path(graph, name)
        data["incoming"] = [{"source": e.source, "rel": e.rel.value} for e in inc]
        data["outgoing"] = [{"target": e.target, "rel": e.rel.value} for e in out]
        if coupling_only:
            data["hidden_rels"] = {
                "incoming": graph.incoming_count(name) - len(inc),
                "outgoing": graph.outgoing_count(name) - len(out),
            }

    if depth >= 2:
        sub = query.subgraph(graph, name, depth=2, rel_types=rel_types)
        neighbor_names = sorted(n for n in sub.nodes if n != name)
        data["neighbors"] = neighbor_names

    if fmt == "json":
        click.echo(json_mod.dumps(data, indent=2))
        return

    # Build content lines (shared between TTY and piped)
    edge_cap = 0 if full else 10

    loc = ""
    if node.file:
        loc = node.file
        if node.line is not None:
            loc += f":{node.line}"
            if node.end_line is not None and node.end_line != node.line:
                loc += f"-{node.end_line}"

    if sys.stdout.isatty():
        title = f"[bold]{node.name}[/]  [{_type_badge(node.type.value)}]"
        lines: list[str] = []
        if loc:
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
                show_inc = inc_edges if edge_cap == 0 else inc_edges[:edge_cap]
                for e in show_inc:
                    lines.append(f"  {e['source']} [dim]--{_rel_style(e['rel'])}-->[/]")
                if edge_cap and len(inc_edges) > edge_cap:
                    lines.append(f"  [dim]... and {len(inc_edges) - edge_cap} more (use --full)[/]")
            out_edges = data["outgoing"]
            if out_edges:
                lines.append("")
                lines.append(f"[bold]Outgoing[/] ({len(out_edges)})")
                show_out = out_edges if edge_cap == 0 else out_edges[:edge_cap]
                for e in show_out:
                    lines.append(f"  [dim]--{_rel_style(e['rel'])}-->[/] {e['target']}")
                if edge_cap and len(out_edges) > edge_cap:
                    lines.append(f"  [dim]... and {len(out_edges) - edge_cap} more (use --full)[/]")
            if coupling_only:
                hidden = data.get("hidden_rels", {})
                hidden_total = hidden.get("incoming", 0) + hidden.get("outgoing", 0)
                if hidden_total:
                    lines.append("")
                    lines.append(
                        f"[dim]{hidden_total} hidden non-coupling edge(s); rerun with --all-rels to include them[/]"
                    )
        if depth >= 2:
            neighbors = data["neighbors"]
            if neighbors:
                neighbor_cap = 0 if full else 10
                lines.append("")
                lines.append(f"[bold]Neighborhood[/] ({len(neighbors)} nodes within 2 hops)")
                show_neighbors = neighbors if neighbor_cap == 0 else neighbors[:neighbor_cap]
                for n in show_neighbors:
                    nnode = graph.get_node(n)
                    t = nnode.type.value if nnode else "?"
                    lines.append(f"  [{_type_badge(t)}] {n}")
                if neighbor_cap and len(neighbors) > neighbor_cap:
                    lines.append(f"  [dim]... and {len(neighbors) - neighbor_cap} more (use --full)[/]")
        console.print(Panel("\n".join(lines), title=title, border_style="dim"))
    else:
        # Plain ASCII for piped output
        click.echo(f"{node.name}  [{node.type.value}]")
        if loc:
            click.echo(f"file:  {loc}")
        if node.docstring:
            click.echo(f"doc:   {node.docstring.split(chr(10))[0]}")
        if node.metadata:
            for k, v in sorted(node.metadata.items()):
                click.echo(f"{k}:  {v}")
        if depth >= 1:
            cpath = data["containment_path"]
            if len(cpath) > 1:
                click.echo(f"\nPath: {' > '.join(cpath)}")
            inc_edges = data["incoming"]
            if inc_edges:
                click.echo(f"\nIncoming ({len(inc_edges)})")
                show_inc = inc_edges if edge_cap == 0 else inc_edges[:edge_cap]
                for e in show_inc:
                    click.echo(f"  {e['source']} --{e['rel']}-->")
                if edge_cap and len(inc_edges) > edge_cap:
                    click.echo(f"  ... and {len(inc_edges) - edge_cap} more (use --full)")
            out_edges = data["outgoing"]
            if out_edges:
                click.echo(f"\nOutgoing ({len(out_edges)})")
                show_out = out_edges if edge_cap == 0 else out_edges[:edge_cap]
                for e in show_out:
                    click.echo(f"  --{e['rel']}--> {e['target']}")
                if edge_cap and len(out_edges) > edge_cap:
                    click.echo(f"  ... and {len(out_edges) - edge_cap} more (use --full)")
            if coupling_only:
                hidden = data.get("hidden_rels", {})
                hidden_total = hidden.get("incoming", 0) + hidden.get("outgoing", 0)
                if hidden_total:
                    click.echo(f"\n{hidden_total} hidden non-coupling edge(s); rerun with --all-rels to include them")
        if depth >= 2:
            neighbors = data["neighbors"]
            if neighbors:
                neighbor_cap = 0 if full else 10
                click.echo(f"\nNeighborhood ({len(neighbors)} nodes within 2 hops)")
                show_neighbors = neighbors if neighbor_cap == 0 else neighbors[:neighbor_cap]
                for n in show_neighbors:
                    nnode = graph.get_node(n)
                    t = nnode.type.value if nnode else "?"
                    click.echo(f"  [{t}] {n}")
                if neighbor_cap and len(neighbors) > neighbor_cap:
                    click.echo(f"  ... and {len(neighbors) - neighbor_cap} more (use --full)")


@main.command()
@click.argument("name")
@click.option(
    "--rel",
    default=None,
    help="Filter by relationship type (e.g. calls, imports, inherits)",
)
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
@click.option("--limit", "limit_", default=_DEFAULT_LIMIT, type=int, help="Max rows (0 = unlimited, default 20)")
def usages(name: str, rel: str | None, fmt: str | None, limit_: int) -> None:
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
        click.echo(
            json_mod.dumps(
                {
                    "target": name,
                    "usages": usage_list,
                    "count": len(usage_list),
                },
                indent=2,
            )
        )
        return

    if not usage_list:
        if sys.stdout.isatty():
            console.print(f"[bold]{name}[/]: [dim]no usages found[/]")
        else:
            click.echo(f"{name}: no usages found")
        return

    total = len(usage_list)
    display = usage_list if limit_ == 0 else usage_list[:limit_]

    rows: list[list[str]] = []
    for u in display:
        loc = u.get("file", "")
        if "line" in u:
            loc += f":{u['line']}"
            if "end_line" in u and u["end_line"] != u["line"]:
                loc += f"-{u['end_line']}"
        rows.append([u["rel"], u["node"], loc])

    if sys.stdout.isatty():
        console.print(f"[bold]Usages of[/] {name} ({total}):\n")
        table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        table.add_column("Rel", style="dim", width=10)
        table.add_column("Node", style="bold")
        table.add_column("File", style="dim")
        for row in rows:
            table.add_row(_rel_style(row[0]), row[1], row[2])
        console.print(table)
        if limit_ > 0 and total > limit_:
            console.print(f"[dim](showing {limit_} of {total} -- use --limit 0 or --format json for all)[/]")
    else:
        click.echo(f"Usages of {name} ({total}):\n")
        click.echo(_compact_table(rows, ["Rel", "Node", "File"], limit=0, total=total))
        if limit_ > 0 and total > limit_:
            click.echo(f"(showing {limit_} of {total} -- use --limit 0 or --format json for all)")


@main.command()
@click.argument("name")
@click.option("--depth", default=None, type=int, help="Max traversal depth")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
@click.option(
    "--coupling-only/--all-rels",
    default=True,
    help="Follow only architecture-significant coupling edges by default",
)
@click.option("--limit", "limit_", default=_DEFAULT_LIMIT, type=int, help="Max rows (0 = unlimited, default 20)")
def impact(name: str, depth: int | None, fmt: str | None, coupling_only: bool, limit_: int) -> None:
    """What breaks if I change X? Reverse transitive impact analysis.

    Follows incoming coupling edges transitively by default to find the nodes
    most likely to be architecturally affected by a change to X. Use
    [bold]--all-rels[/] to include containment and annotation edges too.

    \b
    Example: smg impact auth.service
    """
    import json as json_mod

    graph, _root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)
    rel_types = set(query.COUPLING_RELS) if coupling_only else None
    affected = query.impact(graph, name, rel_types=rel_types, max_depth=depth)

    if fmt == "json":
        click.echo(
            json_mod.dumps(
                {
                    "target": name,
                    "affected": affected,
                    "count": len(affected),
                    "coupling_only": coupling_only,
                },
                indent=2,
            )
        )
        return

    if not affected:
        console.print(f"[bold]{name}[/]: [dim]no upstream dependents[/]")
        return

    total = len(affected)
    display = affected if limit_ == 0 else affected[:limit_]

    console.print(f"[bold]Impact of changing[/] {name}:")
    for a in display:
        anode = graph.get_node(a)
        t = anode.type.value if anode else "?"
        console.print(f"  [{_type_badge(t)}] {a}")
    if limit_ > 0 and total > limit_:
        console.print(f"[dim](showing {limit_} of {total} -- use --limit 0 or --format json for all)[/]")
    console.print(f"\n[dim]{total} node(s) affected[/]")


@main.command()
@click.argument("a")
@click.argument("b")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
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
        click.echo(
            json_mod.dumps(
                {
                    "source": a,
                    "target": b,
                    "path": path,
                    "direct_edges": direct,
                },
                indent=2,
            )
        )
        return

    if path:
        styled = " [dim]->[/] ".join(f"[bold]{p}[/]" for p in path)
        console.print(f"[bold]Path:[/] {styled}")
    else:
        console.print(f"[dim]No path between {a} and {b}[/]")

    if direct:
        console.print("\n[bold]Direct edges:[/]")
        for d in direct:
            console.print(f"  {d['source']} [dim]--{_rel_style(d['rel'])}-->[/] {d['target']}")


@main.command()
@click.option("--top", "top_n", default=10, type=int, help="Number of top connected nodes to show")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
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
        connectivity.append(
            {
                "name": node.name,
                "type": node.type.value,
                "incoming": inc,
                "outgoing": out,
                "total": inc + out,
            }
        )
    connectivity.sort(key=lambda x: x["total"], reverse=True)

    # Module summaries
    modules: list[dict] = []
    for node in nodes:
        if node.type.value in ("module", "package"):
            children = len(graph.outgoing(node.name, rel=RelType.CONTAINS))
            modules.append({"name": node.name, "type": node.type.value, "children": children})
    modules.sort(key=lambda x: x["children"], reverse=True)

    if fmt == "json":
        click.echo(
            json_mod.dumps(
                {
                    "nodes": len(nodes),
                    "edges": len(edges),
                    "top_connected": connectivity[:top_n],
                    "modules": modules,
                },
                indent=2,
            )
        )
        return

    conn_rows = [
        [c["name"], c["type"], str(c["incoming"]), str(c["outgoing"]), str(c["total"])] for c in connectivity[:top_n]
    ]
    mod_rows = [[m["name"], m["type"], str(m["children"])] for m in modules[:top_n]]

    if sys.stdout.isatty():
        console.print(f"[bold]Graph:[/] {len(nodes)} nodes, {len(edges)} edges\n")

        table = Table(
            title=f"[bold]Most Connected[/] (top {top_n})",
            border_style="dim",
            pad_edge=False,
        )
        table.add_column("Name", style="bold")
        table.add_column("Type", style="dim")
        table.add_column("In", justify="right")
        table.add_column("Out", justify="right")
        table.add_column("Total", justify="right", style="bold")
        for row in conn_rows:
            table.add_row(row[0], _type_badge(row[1]), row[2], row[3], row[4])
        console.print(table)

        if mod_rows:
            console.print()
            mod_table = Table(title=f"[bold]Modules[/] (top {top_n})", border_style="dim", pad_edge=False)
            mod_table.add_column("Name", style="bold")
            mod_table.add_column("Type", style="dim")
            mod_table.add_column("Children", justify="right")
            for row in mod_rows:
                mod_table.add_row(row[0], _type_badge(row[1]), row[2])
            console.print(mod_table)
            if len(modules) > top_n:
                console.print(f"[dim](showing {top_n} of {len(modules)} modules -- use --top to see more)[/]")
    else:
        click.echo(f"Graph: {len(nodes)} nodes, {len(edges)} edges\n")
        click.echo(f"Most Connected (top {top_n})")
        click.echo(_compact_table(conn_rows, ["Name", "Type", "In", "Out", "Total"], limit=0))
        if mod_rows:
            click.echo()
            click.echo(f"Modules (top {top_n})")
            click.echo(_compact_table(mod_rows, ["Name", "Type", "Children"], limit=0))
            if len(modules) > top_n:
                click.echo(f"(showing {top_n} of {len(modules)} modules -- use --top to see more)")


@main.command()
@click.argument("ref", default="HEAD")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
def diff(ref: str, fmt: str | None) -> None:
    """What changed structurally? Compare graph against a git ref.

    Defaults to comparing against HEAD (last commit). Use any git ref:
    HEAD~1, main, a commit hash, etc.
    """
    import json as json_mod

    from smg.diff import diff_graphs, load_graph_from_git

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
                {
                    "name": node.name,
                    "changes": [{"field": c.field, "old": c.old, "new": c.new} for c in changes],
                }
                for node, changes in result.changed_nodes
            ],
            "renamed_nodes": [
                {
                    "old_name": rn.old_name,
                    "new_name": rn.new_name,
                    "match_type": rn.match_type,
                }
                for rn in result.renamed_nodes
            ],
            "added_edges": [{"source": e.source, "rel": e.rel.value, "target": e.target} for e in result.added_edges],
            "removed_edges": [
                {"source": e.source, "rel": e.rel.value, "target": e.target} for e in result.removed_edges
            ],
            "summary": {
                "nodes_added": len(result.added_nodes),
                "nodes_removed": len(result.removed_nodes),
                "nodes_changed": len(result.changed_nodes),
                "nodes_renamed": len(result.renamed_nodes),
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

    if result.renamed_nodes:
        console.print(f"[blue]~[/] [bold]{len(result.renamed_nodes)} node(s) renamed/moved[/]")
        for rn in result.renamed_nodes:
            tag = "exact" if rn.match_type == "content" else "structural"
            console.print(f"  [blue]~[/] {rn.old_name} → {rn.new_name} [dim]({tag} match)[/]")

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
    if result.renamed_nodes:
        parts.append(f"[blue]↷{len(result.renamed_nodes)}[/]")
    console.print(
        f"\n[dim]Nodes: {', '.join(parts)} | Edges: +{len(result.added_edges)} -{len(result.removed_edges)}[/]"
    )


@main.command()
@click.option(
    "--top",
    "top_n",
    default=10,
    type=int,
    help="Number of top entries to show per ranking",
)
@click.option(
    "--module",
    "module_filter",
    default=None,
    help="Scope analysis to nodes under this module/package prefix",
)
@click.option(
    "--since",
    "since_ref",
    default=None,
    help="Only analyze nodes/edges added or changed since a git ref (e.g. HEAD~5)",
)
@click.option(
    "--summary",
    is_flag=True,
    help="Show only hotspots and key findings, skip full listings",
)
@click.option(
    "--concepts",
    "include_concepts",
    is_flag=True,
    help="Include declared concept/group analysis",
)
@click.option(
    "--churn-days",
    default=90,
    type=int,
    help="Time window for git churn analysis [dim](default: 90 days)[/]",
)
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
@click.option("--full", is_flag=True, help="Show all sections and full JSON (no truncation)")
@click.option(
    "--include-betweenness",
    is_flag=True,
    help="Force betweenness centrality even on large graphs (auto-skipped above 50K nodes)",
)
def analyze(
    top_n: int,
    module_filter: str | None,
    since_ref: str | None,
    summary: bool,
    include_concepts: bool,
    churn_days: int,
    fmt: str | None,
    full: bool,
    include_betweenness: bool,
) -> None:
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
      smg analyze --concepts             # add declared concept analysis
      smg analyze --summary --top 5     # just hotspots and key findings
      smg analyze --format json         # structured output for agents

    \b
    JSON output keys: hotspots, graph, pagerank, betweenness,
    kcore (with members), classes, modules, sdp_violations,
    fan_in_out, dead_code, layering_violations, smells, concepts
    """
    from smg.analyze import filter_to_delta, run_analysis
    from smg.concepts import ConceptConfigurationError

    graph, _root = _load()
    fmt = _auto_fmt(fmt)

    if module_filter:
        graph = _scope_graph(graph, module_filter, fmt)

    # Compute delta names for --since filtering
    delta_names: set[str] | None = None
    if since_ref:
        from smg.diff import diff_graphs, load_graph_from_git

        old_graph = load_graph_from_git(_root, since_ref)
        if old_graph is None:
            old_graph = SemGraph()
        diff_result = diff_graphs(old_graph, graph)
        delta_names = set()
        for n in diff_result.added_nodes:
            delta_names.add(n.name)
        for n, _changes in diff_result.changed_nodes:
            delta_names.add(n.name)
        for e in diff_result.added_edges:
            delta_names.add(e.source)
            delta_names.add(e.target)

    # Progress spinner
    use_progress = fmt == "text" and sys.stdout.isatty()
    progress_ctx = None
    task_id = None
    if use_progress:
        from rich.progress import Progress, SpinnerColumn, TextColumn

        progress_ctx = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            console=console,
            transient=True,
        )
        progress_ctx.start()
        task_id = progress_ctx.add_task("Analyzing...")

    def _step(desc: str) -> None:
        if progress_ctx is not None and task_id is not None:
            progress_ctx.update(task_id, description=desc)

    declared_concepts = None
    if include_concepts:
        from smg.storage import load_concepts

        declared_concepts = load_concepts(_root)

    # Run analysis
    try:
        r = run_analysis(
            graph,
            root=_root,
            churn_days=churn_days,
            full=not summary,
            declared_concepts=declared_concepts,
            on_step=_step,
            include_betweenness=True if include_betweenness else None,
        )
    except ConceptConfigurationError as e:
        if progress_ctx is not None:
            progress_ctx.stop()
        err_console.print(f"[red]Error:[/] {e}")
        sys.exit(EXIT_VALIDATION)

    if progress_ctx is not None:
        progress_ctx.stop()

    if delta_names is not None:
        filter_to_delta(r, delta_names, graph)

    # Text defaults to summary view; --full expands it.
    # JSON always includes everything unless --summary is set.
    json_summary = summary and not full

    if fmt == "json":
        _render_analyze_json(r, graph, top_n, json_summary)
    else:
        text_summary = not full  # default is summary; --full overrides
        _render_analyze_text(r, graph, top_n, text_summary, module_filter, since_ref, delta_names)


def _render_analyze_json(r: "AnalysisResult", graph: SemGraph, top_n: int, summary: bool) -> None:
    """Render analysis results as JSON."""
    import json as json_mod

    max_layer = max(r.layers.values()) if r.layers else 0
    max_k = max(r.kcore.values()) if r.kcore else 0
    core_members = sorted(n for n, k in r.kcore.items() if k == max_k) if r.kcore else []
    pr_top = sorted(r.pagerank.items(), key=lambda x: x[1], reverse=True)[:top_n]
    bc_top = sorted(r.betweenness.items(), key=lambda x: x[1], reverse=True)[:top_n]

    data: dict = {
        "hotspots": r.hotspots[:top_n],
        "graph": {
            "nodes": r.node_count,
            "edges": r.edge_count,
            "cycles": r.cycles,
            "cycle_count": len(r.cycles),
            "max_layer": max_layer,
            "bridge_count": len(r.bridges),
            "bridges": [list(b) for b in r.bridges[:top_n]],
        },
        "pagerank": [{"name": n, "rank": round(v, 6)} for n, v in pr_top],
        "betweenness": [{"name": n, "centrality": round(v, 6)} for n, v in bc_top],
        "kcore": {
            "max_coreness": max_k,
            "core_size": len(core_members),
            "members": core_members[:top_n],
        },
    }
    if not summary:
        data["classes"] = {
            name: {
                "wmc": r.wmc.get(name, 0),
                "max_method_cc": r.max_method_cc.get(name, 0),
                "dit": r.dit.get(name, 0),
                "noc": r.noc.get(name, 0),
                "cbo": r.cbo.get(name, 0),
                "rfc": r.rfc.get(name, 0),
                "lcom4": r.lcom4.get(name, 0),
            }
            for name in sorted(r.wmc.keys())
        }
        data["modules"] = r.martin
    data["sdp_violations"] = r.sdp_violations
    data["dead_code"] = r.dead_code
    data["layering_violations"] = r.layering_violations
    data["smells"] = {
        "god_classes": r.god_classes,
        "feature_envy": r.feature_envy,
        "shotgun_surgery": r.shotgun_surgery,
        "god_files": r.god_files,
    }
    if r.churn:
        data["churn"] = {
            "total_commits": r.churn.total_commits,
            "time_range": r.churn.time_range,
            "top_entities": sorted(
                [{"name": n, "touches": t} for n, t in r.churn.entity_churn.items()],
                key=lambda x: x["touches"],
                reverse=True,
            )[:top_n],
            "top_files": sorted(
                [{"file": f, "touches": t} for f, t in r.churn.file_churn.items()],
                key=lambda x: x["touches"],
                reverse=True,
            )[:top_n],
        }
    if not summary:
        fio_top = sorted(
            r.fan_in_out.items(),
            key=lambda x: x[1]["fan_in"] + x[1]["fan_out"],
            reverse=True,
        )[:top_n]
        data["fan_in_out"] = [{"name": n, **v} for n, v in fio_top]
        hits_hubs = sorted(r.hits.items(), key=lambda x: x[1]["hub"], reverse=True)[:top_n]
        hits_auths = sorted(r.hits.items(), key=lambda x: x[1]["authority"], reverse=True)[:top_n]
        data["hits"] = {
            "top_hubs": [{"name": n, **v} for n, v in hits_hubs],
            "top_authorities": [{"name": n, **v} for n, v in hits_auths],
        }
    if r.concepts is not None:
        data["concepts"] = r.concepts.to_dict()
    click.echo(json_mod.dumps(data, indent=2))


def _render_concepts_text(concepts: "ConceptAnalysis", top_n: int) -> None:
    concept_data: dict[str, Any] = concepts.to_dict()
    declared = concept_data["declared"]
    dependencies = concept_data["dependencies"]
    violations = concept_data["violations"]

    assert isinstance(declared, list)
    assert isinstance(dependencies, list)
    assert isinstance(violations, list)

    console.print("\n[bold]Concepts[/]")
    if not declared:
        console.print("  [dim]No concept declarations found.[/]")
        return

    summary_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
    summary_table.add_column("Name", style="bold")
    summary_table.add_column("Members", justify="right")
    summary_table.add_column("Internal", justify="right")
    summary_table.add_column("Cross In", justify="right")
    summary_table.add_column("Cross Out", justify="right")
    summary_table.add_column("Density", justify="right")
    summary_table.add_column("Fan-Out", justify="right")
    summary_table.add_column("Asym", justify="right")
    for concept in declared:
        assert isinstance(concept, dict)
        summary_table.add_row(
            concept["name"],
            str(concept["members"]),
            str(concept["internal_edges"]),
            str(concept["cross_in"]),
            str(concept["cross_out"]),
            f"{concept['sync_density']:.3f}",
            str(concept["sync_fan_out"]),
            f"{concept['sync_asymmetry']:.3f}",
        )
    console.print(summary_table)

    if dependencies:
        console.print(f"\n[bold]Concept Dependencies[/] ({len(dependencies)})")
        for dependency in dependencies[:top_n]:
            assert isinstance(dependency, dict)
            sync_label = "allowed" if dependency["allowed_sync"] else "unsanctioned"
            rels = ", ".join(f"{rel}={count}" for rel, count in dependency["rels"].items())
            console.print(
                f"  {dependency['source']} -> {dependency['target']}"
                f" [dim]({dependency['edge_count']} edge(s), {rels}, {sync_label})[/]"
            )
            witness = dependency["witnesses"][0]["edges"][0] if dependency["witnesses"] else None
            if witness is not None:
                assert isinstance(witness, dict)
                console.print(f"    {witness['source']} [dim]--{_rel_style(witness['rel'])}-->[/] {witness['target']}")
        if len(dependencies) > top_n:
            console.print(f"  [dim]... and {len(dependencies) - top_n} more[/]")

    if violations:
        console.print(f"\n[red bold]Concept Violations[/] ({len(violations)})")
        for violation in violations[:top_n]:
            assert isinstance(violation, dict)
            console.print(f"  [red]-[/] {violation['source']} -> {violation['target']}: {violation['message']}")
            for witness in violation["witnesses"][:2]:
                assert isinstance(witness, dict)
                edge = witness["edges"][0]
                assert isinstance(edge, dict)
                console.print(f"      {edge['source']} [dim]--{_rel_style(edge['rel'])}-->[/] {edge['target']}")
        if len(violations) > top_n:
            console.print(f"  [dim]... and {len(violations) - top_n} more[/]")


def _render_analyze_summary(
    r: "AnalysisResult",
    graph: SemGraph,
    scope_label: str,
    max_layer: int,
    max_k: int,
    core_members: list[str],
) -> None:
    """Brief one-line-per-section summary for default text output."""
    console.print(f"[bold]Analysis[/]{scope_label} -- {r.node_count} nodes, {r.edge_count} edges")

    # Hotspots: top 3
    if r.hotspots:
        top3 = ", ".join(h["name"] for h in r.hotspots[:3])
        console.print(f"  [red]Hotspots[/] ({len(r.hotspots)}): {top3}")
    else:
        console.print("  [green]Hotspots: none[/]")

    # Cycles
    console.print(f"  [{'red' if r.cycles else 'green'}]Cycles[/]: {len(r.cycles)}")

    # PageRank: top 3
    pr_top = sorted(r.pagerank.items(), key=lambda x: x[1], reverse=True)[:3]
    if pr_top:
        names = ", ".join(n for n, _ in pr_top)
        console.print(f"  [bold]PageRank[/] top 3: {names}")

    # Dead code
    console.print(f"  [{'yellow' if r.dead_code else 'green'}]Dead code[/]: {len(r.dead_code)} unreferenced")

    # Layering
    color = "yellow" if r.layering_violations else "green"
    console.print(f"  [{color}]Layering violations[/]: {len(r.layering_violations)}")

    # SDP
    console.print(f"  [{'red' if r.sdp_violations else 'green'}]SDP violations[/]: {len(r.sdp_violations)}")

    # Code smells
    smells = r.god_classes + r.feature_envy + r.shotgun_surgery + r.god_files
    console.print(f"  [{'red' if smells else 'green'}]Code smells[/]: {len(smells)}")

    # Class metrics (count only)
    if r.wmc:
        console.print(f"  [bold]Classes[/]: {len(r.wmc)} analyzed")

    # Module metrics (count only)
    if r.martin:
        console.print(f"  [bold]Modules[/]: {len(r.martin)} analyzed")

    # Git churn
    if r.churn and r.churn.entity_churn:
        churn_top = sorted(r.churn.entity_churn.items(), key=lambda x: x[1], reverse=True)[:3]
        names = ", ".join(n for n, _ in churn_top)
        console.print(f"  [bold]Git churn[/] ({r.churn.total_commits} commits): {names}")

    # Concepts
    if r.concepts is not None:
        cdata = r.concepts.to_dict()
        declared: list[object] = cdata.get("declared", [])  # type: ignore[assignment]
        deps: list[object] = cdata.get("dependencies", [])  # type: ignore[assignment]
        viols: list[object] = cdata.get("violations", [])  # type: ignore[assignment]
        console.print(f"  [bold]Concepts[/]: {len(declared)} declared, {len(deps)} deps, {len(viols)} violations")

    console.print(
        f"\n[dim]{max_layer + 1} layers | core: {len(core_members)} nodes (k={max_k})"
        f" | {len(r.bridges)} bridges | use --full for details[/]"
    )


def _render_analyze_text(
    r: "AnalysisResult",
    graph: SemGraph,
    top_n: int,
    summary: bool,
    module_filter: str | None,
    since_ref: str | None,
    delta_names: set[str] | None,
) -> None:
    """Render analysis results as rich text."""
    max_layer = max(r.layers.values()) if r.layers else 0
    max_k = max(r.kcore.values()) if r.kcore else 0
    core_members = sorted(n for n, k in r.kcore.items() if k == max_k) if r.kcore else []
    pr_top = sorted(r.pagerank.items(), key=lambda x: x[1], reverse=True)[:top_n]
    bc_top = sorted(r.betweenness.items(), key=lambda x: x[1], reverse=True)[:top_n]

    scope_label = ""
    if module_filter:
        scope_label += f" [dim](scoped to {module_filter})[/]"
    if since_ref:
        n_delta = len(delta_names) if delta_names else 0
        scope_label += f" [dim](since {since_ref}, {n_delta} changed)[/]"

    if summary:
        _render_analyze_summary(r, graph, scope_label, max_layer, max_k, core_members)
        return

    console.print(f"\n[bold]Analysis[/]{scope_label} -- {r.node_count} nodes, {r.edge_count} edges")

    # Hotspots
    if r.hotspots:
        console.print("\n[red bold]Hotspots[/] (top problem areas)")
        hs_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        hs_table.add_column("#", style="dim", width=3)
        hs_table.add_column("Name", style="bold")
        hs_table.add_column("Score", justify="right")
        hs_table.add_column("Issues")
        for i, h in enumerate(r.hotspots[:top_n], 1):
            hs_table.add_row(str(i), h["name"], str(h["score"]), "; ".join(h["reasons"]))
        console.print(hs_table)
    else:
        console.print("\n[green]No hotspots detected[/]")

    # Cycles
    if r.cycles:
        console.print(f"\n[red bold]Circular Dependencies[/] ({len(r.cycles)} cycle(s))")
        for cycle in r.cycles[:5]:
            console.print(f"  [red]-[/] {' -> '.join(cycle)}")
        if len(r.cycles) > 5:
            console.print(f"  [dim]... and {len(r.cycles) - 5} more[/]")
    else:
        console.print("\n[green]No circular dependencies[/]")

    # SDP violations
    if r.sdp_violations:
        console.print(f"\n[red bold]SDP Violations[/] ({len(r.sdp_violations)})")
        for v in r.sdp_violations[:5]:
            console.print(
                f"  [red]-[/] {v['source']} [dim](I={v['source_instability']})[/]"
                f" depends on {v['target']} [dim](I={v['target_instability']})[/]"
            )
    else:
        console.print("\n[green]No SDP violations[/]")

    # Dead code
    if r.dead_code:
        console.print(f"\n[yellow bold]Dead Code[/] ({len(r.dead_code)} unreferenced node(s))")
        for name in r.dead_code[:top_n]:
            node = graph.get_node(name)
            type_label = node.type.value if node else "?"
            console.print(f"  [yellow]-[/] {name} [dim]({type_label})[/]")
        if len(r.dead_code) > top_n:
            console.print(f"  [dim]... and {len(r.dead_code) - top_n} more[/]")
    else:
        console.print("\n[green]No dead code detected[/]")

    # Layering violations
    if r.layering_violations:
        console.print(f"\n[yellow bold]Layering Violations[/] ({len(r.layering_violations)} back-dependency edge(s))")
        for v in r.layering_violations[:top_n]:
            console.print(
                f"  [yellow]-[/] {v['source']} [dim](L{v['source_layer']})[/]"
                f" --{v['rel']}--> {v['target']} [dim](L{v['target_layer']})[/]"
            )
        if len(r.layering_violations) > top_n:
            console.print(f"  [dim]... and {len(r.layering_violations) - top_n} more[/]")

    # Code smells
    smells = r.god_classes + r.feature_envy + r.shotgun_surgery + r.god_files
    if smells:
        console.print(f"\n[red bold]Code Smells[/] ({len(smells)})")
        for gc in r.god_classes[:3]:
            console.print(
                f"  [red]God Class:[/] {gc['name']} [dim](WMC={gc['wmc']}, CBO={gc['cbo']}, LCOM4={gc['lcom4']})[/]"
            )
        for fe in r.feature_envy[:3]:
            console.print(
                f"  [red]Feature Envy:[/] {fe['method']} envies {fe['envied_class']}"
                f" [dim]({fe['envied_refs']} refs vs {fe['own_refs']} own)[/]"
            )
        for ss in r.shotgun_surgery[:3]:
            console.print(f"  [red]Shotgun Surgery:[/] {ss['name']} [dim](fan-out={ss['fan_out']})[/]")
        for gf in r.god_files[:3]:
            console.print(f"  [red]God File:[/] {gf['file']} [dim]({'; '.join(gf['reasons'])})[/]")

    # Git churn
    if r.churn and r.churn.entity_churn:
        console.print(f"\n[bold]Git Churn[/] ({r.churn.time_range}, {r.churn.total_commits} commits)")
        churn_top = sorted(r.churn.entity_churn.items(), key=lambda x: x[1], reverse=True)[:top_n]
        churn_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        churn_table.add_column("#", style="dim", width=3)
        churn_table.add_column("Entity", style="bold")
        churn_table.add_column("Touches", justify="right")
        for i, (cname, touches) in enumerate(churn_top, 1):
            churn_table.add_row(str(i), cname, str(touches))
        console.print(churn_table)

    if r.concepts is not None:
        _render_concepts_text(r.concepts, top_n)

    # --- Full output ---

    console.print(f"\n[bold]Architecture Depth:[/] {max_layer + 1} layers")

    console.print("\n[bold]Most Important (PageRank)[/]")
    pr_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
    pr_table.add_column("#", style="dim", width=3)
    pr_table.add_column("Name", style="bold")
    pr_table.add_column("Rank", justify="right")
    for i, (name, rank) in enumerate(pr_top, 1):
        pr_table.add_row(str(i), name, f"{rank:.4f}")
    console.print(pr_table)

    bc_nonzero = [(n, c) for n, c in bc_top if c > 0]
    if bc_nonzero:
        console.print("\n[bold]Structural Bottlenecks (Betweenness)[/]")
        bc_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        bc_table.add_column("#", style="dim", width=3)
        bc_table.add_column("Name", style="bold")
        bc_table.add_column("Centrality", justify="right")
        for i, (name, cent) in enumerate(bc_nonzero[:top_n], 1):
            bc_table.add_row(str(i), name, f"{cent:.4f}")
        console.print(bc_table)

    if core_members:
        console.print(f"\n[bold]Core Structure[/] (k={max_k}, {len(core_members)} nodes)")
        for n in core_members[:top_n]:
            console.print(f"  {n}")
        if len(core_members) > top_n:
            console.print(f"  [dim]... and {len(core_members) - top_n} more[/]")

    if r.bridges:
        console.print(f"\n[yellow]Fragile Connections:[/] {len(r.bridges)} bridge edge(s)")
        for a, b in r.bridges[:5]:
            console.print(f"  [yellow]-[/] {a} -- {b}")

    if r.wmc:
        console.print("\n[bold]Class Metrics (CK Suite)[/]")
        ck_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        ck_table.add_column("Class", style="bold")
        for col in ("WMC", "MaxCC", "CBO", "RFC", "LCOM4", "DIT", "NOC"):
            ck_table.add_column(col, justify="right")
        for name in sorted(r.wmc.keys(), key=lambda n: r.wmc[n], reverse=True)[:top_n]:
            lcom_val = r.lcom4.get(name, 0)
            lcom_str = f"[red]{lcom_val}[/]" if lcom_val > 1 else str(lcom_val)
            ck_table.add_row(
                name,
                str(r.wmc.get(name, 0)),
                str(r.max_method_cc.get(name, 0)),
                str(r.cbo.get(name, 0)),
                str(r.rfc.get(name, 0)),
                lcom_str,
                str(r.dit.get(name, 0)),
                str(r.noc.get(name, 0)),
            )
        console.print(ck_table)

    if r.martin:
        console.print("\n[bold]Module Metrics (Martin)[/]")
        mod_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        for col in ("Module", "Ca", "Ce", "I", "A", "D"):
            mod_table.add_column(
                col,
                justify="right" if col != "Module" else "left",
                style="bold" if col == "Module" else None,
            )
        for name in sorted(r.martin.keys()):
            m = r.martin[name]
            d_str = f"[red]{m['distance']}[/]" if m["distance"] > 0.7 else str(m["distance"])
            mod_table.add_row(
                name,
                str(m["ca"]),
                str(m["ce"]),
                str(m["instability"]),
                str(m["abstractness"]),
                d_str,
            )
        console.print(mod_table)

    if r.fan_in_out:
        console.print(f"\n[bold]Fan-In / Fan-Out (top {top_n})[/]")
        fio_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        for col in ("Name", "Fan-In", "Fan-Out", "Total"):
            fio_table.add_column(
                col,
                justify="right" if col != "Name" else "left",
                style="bold" if col == "Name" else None,
            )
        fio_sorted = sorted(
            r.fan_in_out.items(),
            key=lambda x: x[1]["fan_in"] + x[1]["fan_out"],
            reverse=True,
        )
        for name, vals in fio_sorted[:top_n]:
            fio_table.add_row(
                name,
                str(vals["fan_in"]),
                str(vals["fan_out"]),
                str(vals["fan_in"] + vals["fan_out"]),
            )
        console.print(fio_table)

    if r.hits:
        console.print(f"\n[bold]Hubs & Authorities (HITS, top {top_n})[/]")
        hits_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        hits_table.add_column("Name", style="bold")
        hits_table.add_column("Hub", justify="right")
        hits_table.add_column("Authority", justify="right")
        hits_table.add_column("Role", justify="left")
        hits_combined = sorted(
            r.hits.items(),
            key=lambda x: max(x[1]["hub"], x[1]["authority"]),
            reverse=True,
        )
        for name, scores in hits_combined[:top_n]:
            role = "hub" if scores["hub"] > scores["authority"] else "authority"
            if scores["hub"] > 0.01 and scores["authority"] > 0.01:
                role = "both"
            hits_table.add_row(name, f"{scores['hub']:.4f}", f"{scores['authority']:.4f}", role)
        console.print(hits_table)


@main.command()
@click.argument("name")
@click.option("--tokens", default=4000, type=int, help="Token budget [dim](default: 4000)[/]")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
@click.option("--with-source", is_flag=True, help="Include full source bodies (default: signatures only)")
def context(name: str, tokens: int, fmt: str | None, with_source: bool) -> None:
    """Pack relevant source code for LLM context within a token budget.

    Walks outward from the target entity, greedily packing source code by
    graph proximity. Degrades from full source to signatures to summaries
    as the budget fills up.

    \b
    Examples:
      smg context SemGraph --tokens 8000
      smg context add_node --tokens 2000
      smg context SemGraph | pbcopy           # pipe JSON to clipboard
    """
    import json as json_mod
    from pathlib import Path

    from smg.context import build_context

    graph, root = _load()
    name = _resolve_or_exit(graph, name)
    fmt = _auto_fmt(fmt)

    result = build_context(graph, root, name, budget=tokens, with_source=with_source)

    if fmt == "json":
        data = {
            "target": result.target,
            "total_tokens": result.total_tokens,
            "budget": result.budget,
            "truncated": result.truncated,
            "entries": [
                {
                    "name": e.name,
                    "type": e.node_type,
                    "relation": e.relation,
                    "level": e.level,
                    "tokens": e.tokens,
                    "content": e.content,
                }
                for e in result.entries
            ],
        }
        click.echo(json_mod.dumps(data, indent=2))
        return

    console.print(f"[bold]Context for[/] {result.target} [dim]({result.total_tokens}/{result.budget} tokens)[/]\n")

    _level_badge = {
        "full": "[green]full[/]",
        "signature": "[yellow]sig[/]",
        "summary": "[dim]sum[/]",
    }

    for entry in result.entries:
        badge = _level_badge.get(entry.level, entry.level)
        header = (
            f"[bold]{entry.name}[/] [{_type_badge(entry.node_type)}] {badge}"
            f" [dim]({entry.relation}, ~{entry.tokens} tok)[/]"
        )

        if entry.level in ("full", "signature") and entry.file:
            from rich.syntax import Syntax

            ext = Path(entry.file).suffix if entry.file else ".txt"
            _lang_map = {
                ".py": "python",
                ".js": "javascript",
                ".ts": "typescript",
                ".c": "c",
                ".cpp": "cpp",
                ".zig": "zig",
            }
            lang = _lang_map.get(ext, "text")
            console.print(header)
            console.print(Syntax(entry.content, lang, line_numbers=True, start_line=entry.line or 1))
            console.print()
        else:
            console.print(header)
            console.print(f"  [dim]{entry.content}[/]\n")

    if result.truncated:
        console.print("[yellow]Budget exhausted[/] -- some neighbors omitted or downgraded")


@main.command()
@click.argument("name")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
@click.option("--limit", "limit_", default=_DEFAULT_LIMIT, type=int, help="Max rows (0 = unlimited, default 20)")
def blame(name: str, fmt: str | None, limit_: int) -> None:
    """Who last touched this entity? Entity-level git blame.

    Accepts a node name or a file path. If a node name is given, blames that
    single entity. If a file path is given, blames all entities in that file.

    \b
    Examples:
      smg blame SemGraph              # single entity
      smg blame src/smg/graph.py      # all entities in file
    """
    import json as json_mod

    from smg.blame import blame_entity, blame_file

    graph, root = _load()
    fmt = _auto_fmt(fmt)

    # Check if the argument is a file path
    is_file = (
        "/" in name
        or name.endswith(".py")
        or name.endswith(".js")
        or name.endswith(".ts")
        or name.endswith(".c")
        or name.endswith(".zig")
    )

    if is_file:
        entries = blame_file(graph, name, root)
        if not entries:
            err_console.print(f"[red]Error:[/] no entities found in [bold]{name}[/]")
            sys.exit(EXIT_NOT_FOUND)

        if fmt == "json":
            data = [
                {
                    "name": e.name,
                    "type": e.node_type,
                    "file": e.file,
                    "line": e.line,
                    "end_line": e.end_line,
                    "commit": e.commit,
                    "author": e.author,
                    "date": e.date,
                    "summary": e.summary,
                }
                for e in entries
            ]
            click.echo(json_mod.dumps(data, indent=2))
            return

        total = len(entries)
        rows: list[list[str]] = []
        for e in entries:
            rows.append(
                [
                    f"{e.line}-{e.end_line}",
                    e.name,
                    e.node_type,
                    e.commit or "",
                    e.author or "",
                    e.date or "",
                    e.summary or "",
                ]
            )

        if sys.stdout.isatty():
            display = entries if limit_ == 0 else entries[:limit_]
            console.print(f"[bold]Blame for[/] {name}\n")
            table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
            table.add_column("Lines", style="dim", width=10)
            table.add_column("Entity", style="bold")
            table.add_column("Type")
            table.add_column("Commit", style="dim", width=12)
            table.add_column("Author")
            table.add_column("Date", style="dim")
            table.add_column("Summary")
            for e in display:
                table.add_row(
                    f"{e.line}-{e.end_line}",
                    e.name,
                    _type_badge(e.node_type),
                    e.commit,
                    e.author,
                    e.date,
                    e.summary,
                )
            console.print(table)
            if limit_ > 0 and total > limit_:
                console.print(f"[dim](showing {limit_} of {total} -- use --limit 0 or --format json for all)[/]")
        else:
            cols = ["Lines", "Entity", "Type", "Commit", "Author", "Date", "Summary"]
            click.echo(f"Blame for {name}\n")
            click.echo(_compact_table(rows, cols, limit=limit_, total=total))
    else:
        resolved = _resolve_or_exit(graph, name)
        node = graph.get_node(resolved)
        if node is None:
            err_console.print(f"[red]Error:[/] node not found: [bold]{name}[/]")
            sys.exit(EXIT_NOT_FOUND)

        entry = blame_entity(node, root)
        if entry is None:
            err_console.print(
                f"[yellow]Warning:[/] no git blame data for [bold]{resolved}[/] (no file/line or not in git)"
            )
            sys.exit(EXIT_NOT_FOUND)

        if fmt == "json":
            data = {
                "name": entry.name,
                "type": entry.node_type,
                "file": entry.file,
                "line": entry.line,
                "end_line": entry.end_line,
                "commit": entry.commit,
                "author": entry.author,
                "date": entry.date,
                "summary": entry.summary,
            }
            click.echo(json_mod.dumps(data, indent=2))
            return

        console.print(f"[bold]{entry.name}[/] [{_type_badge(entry.node_type)}]")
        console.print(f"  [dim]{entry.file}:{entry.line}-{entry.end_line}[/]")
        console.print(f"  [bold]{entry.commit}[/] {entry.author} [dim]({entry.date})[/]")
        console.print(f"  {entry.summary}")


# --- Search ---

_SEARCH_LIMIT = 10

_SEARCH_COLUMNS: list[tuple[str, str, dict]] = [
    ("rank", "rank", {"align": "right", "max_width": 6}),
    ("kind", "kind", {"max_width": 12}),
    ("name", "name", {"max_width": 40}),
    ("location", "location", {"max_width": 30}),
    ("snippet", "snippet", {"max_width": 40}),
]


@main.command()
@click.argument("query_str", metavar="QUERY")
@click.option("--kind", "kind_", default=None, help="Filter by node kind (post-match)")
@click.option("--limit", "limit_", default=_SEARCH_LIMIT, type=int, help="Max results (0 = unlimited, default 10)")
@click.option("--json", "use_json", is_flag=True, help="Emit canonical JSON envelope")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    hidden=True,
    help="Output format (hidden, use --json instead)",
)
@click.option("--full", is_flag=True, help="Show expanded detail per hit")
@click.option("--json-legacy", "json_legacy", is_flag=True, hidden=True, help="Emit bare JSON array (legacy)")
def search(
    query_str: str,
    kind_: str | None,
    limit_: int,
    use_json: bool,
    fmt: str | None,
    full: bool,
    json_legacy: bool,
) -> None:
    """Fuzzy-search the code graph by identifier or docstring.

    \b
    Uses FTS5 full-text search over identifier tokens and docstrings.
    Dotted names are automatically split: smg.cli.helpers._truncate
    matches as if you searched "smg cli helpers truncate".

    \b
    Default ordering: best relevance first (BM25).
    """
    import json as json_mod

    from smg.cli._compact import compact_json_envelope, compact_table
    from smg.search import search_nodes
    from smg.search.schema import search_db_path

    graph, root = _load()
    db_path = search_db_path(root)

    # Auto-rebuild if missing
    if not db_path.exists():
        from smg.search import rebuild_search_index

        rebuild_search_index(graph, root)

    effective_json = use_json or fmt == "json"

    hits, total = search_nodes(
        db_path,
        query_str,
        kind=kind_,
        limit=limit_,
        graph=graph,
        root=root,
    )

    if json_legacy:
        data = [
            {
                "name": h.name,
                "kind": h.kind,
                "file": h.file,
                "line_start": h.line_start,
                "score": h.score,
            }
            for h in hits
        ]
        click.echo(json_mod.dumps(data, indent=2))
        return

    rows: list[dict[str, object]] = []
    for h in hits:
        row: dict[str, object] = {
            "rank": h.rank,
            "kind": h.kind,
            "name": h.name,
            "location": h.location,
            "snippet": h.snippet,
        }
        if full and effective_json:
            row["extra"] = {
                "file": h.file,
                "line_start": h.line_start,
                "line_end": h.line_end,
                "docstring": h.docstring,
                "score": h.score,
            }
        rows.append(row)

    if effective_json:
        envelope = compact_json_envelope(
            rows,
            _SEARCH_COLUMNS,
            total=total,
            limit=limit_,
        )
        click.echo(json_mod.dumps(envelope, indent=2))
        return

    if not hits:
        click.echo("No results.")
        return

    if full:
        # Expanded multi-line view
        for h in hits:
            click.echo(f"{h.rank}. [{h.kind}] {h.name}")
            click.echo(f"   {h.location}")
            if h.docstring:
                for line in h.docstring.split("\n")[:5]:
                    click.echo(f"   {line}")
            click.echo()
        if total > len(hits):
            click.echo(f"(showing {len(hits)} of {total} -- use --limit 0 for all, --json for machine-readable)")
        return

    displayed_total = total if total > len(rows) else None
    click.echo(compact_table(rows, _SEARCH_COLUMNS, total=displayed_total))


@main.command()
def index() -> None:
    """Rebuild the search index from the current graph.

    Equivalent to running smg scan with no new sources. Useful when the
    search cache has been deleted or corrupted.
    """
    from smg.search import rebuild_search_index

    graph, root = _load()
    rebuild_search_index(graph, root)
    node_count = len(graph.all_nodes())
    console.print(f"[green]Indexed[/] {node_count} nodes into search cache")
