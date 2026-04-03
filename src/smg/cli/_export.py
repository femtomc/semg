from __future__ import annotations

import rich_click as click

from smg import export

from smg.cli import (
    main,
    _load,
    console,
    err_console,
)


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


@export_cmd.command("dsm")
@click.option("--level", default="module", type=click.Choice(["module", "class", "all"]), help="Granularity: module (default), class, or all nodes")
def export_dsm(level: str) -> None:
    """Export Dependency Structure Matrix as CSV.

    \b
    Rows and columns are nodes at the chosen granularity.
    Cell (i,j) = number of coupling edges from node i to node j.

    \b
    Examples:
      smg export dsm                        # module-level DSM
      smg export dsm --level class          # class-level DSM
      smg export dsm > deps.csv             # save to file
    """
    graph, _root = _load()
    result = export.to_dsm(graph, level=level)
    if not result:
        err_console.print("[yellow]Warning:[/] no nodes at the requested granularity.")
        return
    click.echo(result)
