from __future__ import annotations

import sys

import rich_click as click
from rich.table import Table

from smg.cli import (
    main,
    _load,
    _auto_fmt,
    console,
    err_console,
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
)


@main.group()
def rule() -> None:
    """Declare, list, and remove architectural rules."""
    pass


@rule.command("add")
@click.argument("name")
@click.option("--deny", "deny_pattern", default=None, help='Path denial pattern: "source_glob -[rel]-> target_glob"')
@click.option("--invariant", default=None, type=click.Choice(["no-cycles", "no-dead-code", "no-layering-violations"]), help="Structural invariant to enforce")
@click.option("--entry-points", default=None, help="Comma-separated entry points for no-dead-code (supports globs)")
@click.option("--scope", default=None, help="Restrict rule to nodes under this module prefix")
def rule_add(name: str, deny_pattern: str | None, invariant: str | None, entry_points: str | None, scope: str | None) -> None:
    """Add an architectural rule.

    \b
    Examples:
      smg rule add layering --deny "core.* -> ui.*"
      smg rule add no-db-calls --deny "api.* -[calls]-> db.*"
      smg rule add acyclic --invariant no-cycles
      smg rule add acyclic-server --invariant no-cycles --scope bellboy.server
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
        new_rule = Rule(name=name, type="deny", pattern=deny_pattern, scope=scope)
    else:
        params: dict = {}
        if entry_points:
            params["entry_points"] = entry_points
        new_rule = Rule(name=name, type="invariant", invariant=invariant, params=params, scope=scope)

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
        if r.scope:
            constraint = f"{constraint} [dim]scope={r.scope}[/]"
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
                        path = " -> ".join(cycle) + f" -> {cycle[0]}"
                        console.print(f"        {path}")
                    if len(v.cycles) > 5:
                        console.print(f"        [dim]... and {len(v.cycles) - 5} more[/]")

    if violations:
        sys.exit(EXIT_NOT_FOUND)
