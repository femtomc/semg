from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import rich_click as click

if TYPE_CHECKING:
    from smg.analyze import AnalysisResult
from rich.panel import Panel
from rich.table import Table

from smg import query
from smg.cli import (
    EXIT_NOT_FOUND,
    _auto_fmt,
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
    help="Output format (auto-detects: JSON when piped)",
)
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
    help="Output format (auto-detects: JSON when piped)",
)
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
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
)
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
        click.echo(
            json_mod.dumps(
                {
                    "target": name,
                    "affected": affected,
                    "count": len(affected),
                },
                indent=2,
            )
        )
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
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
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
    help="Output format (auto-detects: JSON when piped)",
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

    # Rich text
    console.print(f"[bold]Graph:[/] {len(nodes)} nodes, {len(edges)} edges\n")

    # Top connected
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
    for c in connectivity[:top_n]:
        table.add_row(
            c["name"],
            _type_badge(c["type"]),
            str(c["incoming"]),
            str(c["outgoing"]),
            str(c["total"]),
        )
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


@main.command()
@click.argument("ref", default="HEAD")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
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
    help="Output format (auto-detects: JSON when piped)",
)
def analyze(
    top_n: int,
    module_filter: str | None,
    since_ref: str | None,
    summary: bool,
    churn_days: int,
    fmt: str | None,
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
      smg analyze --summary --top 5     # just hotspots and key findings
      smg analyze --format json         # structured output for agents

    \b
    JSON output keys: hotspots, graph, pagerank, betweenness,
    kcore (with members), classes, modules, sdp_violations,
    fan_in_out, dead_code, layering_violations, smells
    """
    from smg.analyze import filter_to_delta, run_analysis

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

    # Run analysis
    r = run_analysis(graph, root=_root, churn_days=churn_days, full=not summary, on_step=_step)

    if progress_ctx is not None:
        progress_ctx.stop()

    if delta_names is not None:
        filter_to_delta(r, delta_names, graph)

    if fmt == "json":
        _render_analyze_json(r, graph, top_n, summary)
    else:
        _render_analyze_text(r, graph, top_n, summary, module_filter, since_ref, delta_names)


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
    click.echo(json_mod.dumps(data, indent=2))


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

    if summary:
        console.print(
            f"\n[dim]Architecture depth: {max_layer + 1} layers | Core: {len(core_members)} nodes (k={max_k})"
            f" | Bridges: {len(r.bridges)}[/]"
        )
        return

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
    help="Output format (auto-detects: JSON when piped)",
)
def context(name: str, tokens: int, fmt: str | None) -> None:
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

    result = build_context(graph, root, name, budget=tokens)

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
        console.print("[yellow]Budget exhausted[/] — some neighbors omitted or downgraded")


@main.command()
@click.argument("name")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format (auto-detects: JSON when piped)",
)
def blame(name: str, fmt: str | None) -> None:
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

        console.print(f"[bold]Blame for[/] {name}\n")
        table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        table.add_column("Lines", style="dim", width=10)
        table.add_column("Entity", style="bold")
        table.add_column("Type")
        table.add_column("Commit", style="dim", width=12)
        table.add_column("Author")
        table.add_column("Date", style="dim")
        table.add_column("Summary")
        for e in entries:
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
