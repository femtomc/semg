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


def _scope_graph(graph: SemGraph, module_filter: str, fmt: str | None = None) -> SemGraph:
    """Scope a graph to nodes matching a module prefix, with fuzzy suggestion on empty result."""
    scoped = SemGraph()
    prefix = module_filter if module_filter.endswith(".") else module_filter + "."
    for node in graph.all_nodes():
        if node.name == module_filter or node.name.startswith(prefix):
            scoped.add_node(node)
    for edge_obj in graph.all_edges():
        if edge_obj.source in scoped.nodes and edge_obj.target in scoped.nodes:
            scoped.add_edge(edge_obj)

    if len(scoped) == 0 and len(graph) > 0:
        # Suggest alternatives via suffix matching
        suffix = module_filter.rsplit(".", 1)[-1]
        candidates = sorted({
            n.name.rsplit("." + suffix, 1)[0] + "." + suffix
            for n in graph.all_nodes()
            if ("." + suffix + ".") in n.name or n.name.endswith("." + suffix)
        })
        msg = f"no nodes matching [bold]{module_filter}[/]"
        if candidates:
            msg += ". Did you mean:"
            for c in candidates[:5]:
                msg += f"\n  {c}"
        if fmt == "json":
            err_console.print(f"[yellow]Warning:[/] {msg}")
        else:
            err_console.print(f"[yellow]Warning:[/] {msg}")

    return scoped


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


# Import submodules to register commands on `main`
from smg.cli import explore, enforce, inspect, mutate, _export  # noqa: F401, E402
