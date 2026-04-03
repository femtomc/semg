from __future__ import annotations

import sys

import rich_click as click
from rich.table import Table
from rich.panel import Panel

from smg import export, query
from smg.graph import SemGraph
from smg.model import RelType

from smg.cli import (
    main,
    _load,
    _resolve_or_exit,
    _scope_graph,
    _auto_fmt,
    _type_badge,
    _rel_style,
    _output_names,
    _output_graph,
    _output_edges,
    console,
    err_console,
    EXIT_OK,
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
)


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


@main.command()
@click.option("--top", "top_n", default=10, type=int, help="Number of top entries to show per ranking")
@click.option("--module", "module_filter", default=None, help="Scope analysis to nodes under this module/package prefix")
@click.option("--since", "since_ref", default=None, help="Only analyze nodes/edges added or changed since a git ref (e.g. HEAD~5)")
@click.option("--summary", is_flag=True, help="Show only hotspots and key findings, skip full listings")
@click.option("--format", "fmt", default=None, type=click.Choice(["text", "json"]), help="Output format (auto-detects: JSON when piped)")
def analyze(top_n: int, module_filter: str | None, since_ref: str | None, summary: bool, fmt: str | None) -> None:
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
        graph = _scope_graph(graph, module_filter, fmt)

    # Scope to nodes/edges that changed since a git ref
    delta_names: set[str] | None = None
    if since_ref:
        from smg.diff import diff_graphs, load_graph_from_git
        old_graph = load_graph_from_git(_root, since_ref)
        if old_graph is None:
            old_graph = SemGraph()
        diff_result = diff_graphs(old_graph, graph)
        # Collect names of added/changed nodes
        delta_names = set()
        for n in diff_result.added_nodes:
            delta_names.add(n.name)
        for n, _changes in diff_result.changed_nodes:
            delta_names.add(n.name)
        # Also include endpoints of added edges
        for e in diff_result.added_edges:
            delta_names.add(e.source)
            delta_names.add(e.target)

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
    max_cc_data = oo_metrics.max_method_cc(graph)
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
    _step("Computing HITS (hub/authority)...")
    hits_data = graph_metrics.hits(graph)
    _step("Detecting code smells...")
    gods = oo_metrics.god_classes(graph)
    envy = oo_metrics.feature_envy(graph)
    shotgun = oo_metrics.shotgun_surgery(graph)
    _step("Detecting god files...")
    god_files = graph_metrics.god_files(graph)
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

    # If --since was used, filter results to only delta nodes
    if delta_names is not None:
        hotspots = [h for h in hotspots if h["name"] in delta_names]
        pr_top_candidates = {n for n, _ in sorted(pr.items(), key=lambda x: x[1], reverse=True)} & delta_names
        bc_top_candidates = {n for n, _ in sorted(bc.items(), key=lambda x: x[1], reverse=True)} & delta_names
        dead = [n for n in dead if n in delta_names]
        layer_violations = [v for v in layer_violations if v["source"] in delta_names or v["target"] in delta_names]
        gods = [g for g in gods if g["name"] in delta_names]
        envy = [e for e in envy if e["method"] in delta_names]
        shotgun = [s for s in shotgun if s["name"] in delta_names]
        # god_files: keep if any delta node lives in that file
        delta_files = {node_to_file for n in delta_names if (node_to_file := next((nd.file for nd in graph.all_nodes() if nd.name == n and nd.file), None))}
        god_files = [gf for gf in god_files if gf["file"] in delta_files]

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
                    "wmc": wmc_data.get(name, 0), "max_method_cc": max_cc_data.get(name, 0),
                    "dit": dit_data.get(name, 0), "noc": noc_data.get(name, 0),
                    "cbo": cbo_data.get(name, 0), "rfc": rfc_data.get(name, 0),
                    "lcom4": lcom_data.get(name, 0),
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
            "god_files": god_files,
        }
        if not summary:
            fio_top = sorted(fio.items(), key=lambda x: x[1]["fan_in"] + x[1]["fan_out"], reverse=True)[:top_n]
            data["fan_in_out"] = [{"name": n, **v} for n, v in fio_top]
            hits_hubs = sorted(hits_data.items(), key=lambda x: x[1]["hub"], reverse=True)[:top_n]
            hits_auths = sorted(hits_data.items(), key=lambda x: x[1]["authority"], reverse=True)[:top_n]
            data["hits"] = {
                "top_hubs": [{"name": n, **v} for n, v in hits_hubs],
                "top_authorities": [{"name": n, **v} for n, v in hits_auths],
            }
        click.echo(json_mod.dumps(data, indent=2))
        return

    # --- Rich text output ---

    scope_label = ""
    if module_filter:
        scope_label += f" [dim](scoped to {module_filter})[/]"
    if since_ref:
        n_delta = len(delta_names) if delta_names else 0
        scope_label += f" [dim](since {since_ref}, {n_delta} changed)[/]"
    console.print(f"\n[bold]Analysis[/]{scope_label} -- {len(graph)} nodes, {len(graph.all_edges())} edges")

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
    if gods or envy or shotgun or god_files:
        smell_count = len(gods) + len(envy) + len(shotgun) + len(god_files)
        console.print(f"\n[red bold]Code Smells[/] ({smell_count})")
        for gc in gods[:3]:
            console.print(f"  [red]God Class:[/] {gc['name']} [dim](WMC={gc['wmc']}, CBO={gc['cbo']}, LCOM4={gc['lcom4']})[/]")
        for fe in envy[:3]:
            console.print(f"  [red]Feature Envy:[/] {fe['method']} envies {fe['envied_class']} [dim]({fe['envied_refs']} refs vs {fe['own_refs']} own)[/]")
        for ss in shotgun[:3]:
            console.print(f"  [red]Shotgun Surgery:[/] {ss['name']} [dim](fan-out={ss['fan_out']})[/]")
        for gf in god_files[:3]:
            console.print(f"  [red]God File:[/] {gf['file']} [dim]({'; '.join(gf['reasons'])})[/]")

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
        ck_table.add_column("MaxCC", justify="right")
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
                str(wmc_data.get(name, 0)), str(max_cc_data.get(name, 0)),
                str(cbo_data.get(name, 0)),
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

    # HITS (Hub/Authority)
    if hits_data:
        console.print(f"\n[bold]Hubs & Authorities (HITS, top {top_n})[/]")
        hits_table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
        hits_table.add_column("Name", style="bold")
        hits_table.add_column("Hub", justify="right")
        hits_table.add_column("Authority", justify="right")
        hits_table.add_column("Role", justify="left")
        hits_combined = sorted(
            hits_data.items(),
            key=lambda x: max(x[1]["hub"], x[1]["authority"]),
            reverse=True,
        )
        for name, scores in hits_combined[:top_n]:
            role = "hub" if scores["hub"] > scores["authority"] else "authority"
            if scores["hub"] > 0.01 and scores["authority"] > 0.01:
                role = "both"
            hits_table.add_row(name, f"{scores['hub']:.4f}", f"{scores['authority']:.4f}", role)
        console.print(hits_table)
