from __future__ import annotations

import sys

import rich_click as click
from rich.table import Table

from smg.cli import (
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
    _auto_fmt,
    _load,
    console,
    err_console,
    main,
)
from smg.rules import KNOWN_INVARIANTS


@main.group()
def rule() -> None:
    """Declare, list, and remove architectural rules."""
    pass


@rule.command("add")
@click.argument("name")
@click.option(
    "--deny",
    "deny_pattern",
    default=None,
    help='Path denial pattern: "source_glob -[rel]-> target_glob"',
)
@click.option(
    "--invariant",
    default=None,
    type=click.Choice(sorted(KNOWN_INVARIANTS)),
    help="Structural invariant to enforce",
)
@click.option("--forall", "selector", default=None, help="Glob over subject node names for quantified rules")
@click.option("--assert", "assertion", default=None, help="Predicate over metrics for quantified rules")
@click.option(
    "--entry-points",
    default=None,
    help="Comma-separated entry points for no-dead-code (supports globs)",
)
@click.option("--scope", default=None, help="Restrict rule to nodes under this module prefix")
def rule_add(
    name: str,
    deny_pattern: str | None,
    invariant: str | None,
    selector: str | None,
    assertion: str | None,
    entry_points: str | None,
    scope: str | None,
) -> None:
    """Add an architectural rule.

    \b
    Examples:
      smg rule add layering --deny "core.* -> ui.*"
      smg rule add no-db-calls --deny "api.* -[calls]-> db.*"
      smg rule add boundaries --invariant concept-boundaries
      smg rule add acyclic --invariant no-cycles
      smg rule add acyclic-server --invariant no-cycles --scope bellboy.server
      smg rule add reachable --invariant no-dead-code --entry-points "main,cli.*"
      smg rule add service-fan-out --forall "*.service" --assert "fan_out <= 5"
    """
    from smg.rules import Rule, parse_deny_pattern, parse_quantified_assertion
    from smg.storage import load_rules, save_rules

    kind_count = sum(value is not None for value in (deny_pattern, invariant, selector))
    if kind_count > 1:
        err_console.print("[red]Error:[/] specify exactly one of --deny, --invariant, or --forall.")
        sys.exit(EXIT_VALIDATION)
    if kind_count == 0:
        err_console.print("[red]Error:[/] specify one of --deny, --invariant, or --forall.")
        sys.exit(EXIT_VALIDATION)
    if selector is not None and assertion is None:
        err_console.print("[red]Error:[/] --assert is required with --forall.")
        sys.exit(EXIT_VALIDATION)
    if selector is None and assertion is not None:
        err_console.print("[red]Error:[/] --assert may only be used with --forall.")
        sys.exit(EXIT_VALIDATION)

    if deny_pattern is not None:
        try:
            parse_deny_pattern(deny_pattern)
        except ValueError as exc:
            err_console.print(f"[red]Error:[/] {exc}")
            sys.exit(EXIT_VALIDATION)
        new_rule = Rule(name=name, type="deny", pattern=deny_pattern, scope=scope)
    elif invariant is not None:
        params: dict[str, str] = {}
        if entry_points:
            params["entry_points"] = entry_points
        new_rule = Rule(name=name, type="invariant", invariant=invariant, params=params, scope=scope)
    else:
        new_rule = Rule(name=name, type="quantified", selector=selector, assertion=assertion, scope=scope)
        try:
            parse_quantified_assertion(new_rule)
        except ValueError as exc:
            err_console.print(f"[red]Error:[/] {exc}")
            sys.exit(EXIT_VALIDATION)

    _graph, root = _load()
    rules = load_rules(root)
    if any(rule.name == name for rule in rules):
        err_console.print(
            f"[red]Error:[/] rule {name!r} already exists. Remove it first with [bold]smg rule rm {name}[/]."
        )
        sys.exit(EXIT_VALIDATION)
    rules.append(new_rule)
    save_rules(rules, root)
    console.print(f"Rule {name!r} added.")


@rule.command("list")
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
def rule_list(fmt: str | None) -> None:
    """List all architectural rules."""
    import json as json_mod

    from smg.storage import load_rules

    _graph, root = _load()
    rules = load_rules(root)
    fmt = _auto_fmt(fmt)

    if fmt == "json":
        click.echo(json_mod.dumps([rule.to_dict() for rule in rules], indent=2))
        return

    if not rules:
        console.print("No rules defined. Add one with [bold]smg rule add[/].")
        return

    table = Table(show_header=True, header_style="bold", border_style="dim", pad_edge=False)
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Constraint")
    for rule_obj in rules:
        if rule_obj.type == "deny":
            constraint = rule_obj.pattern
        elif rule_obj.type == "invariant":
            constraint = rule_obj.invariant
        else:
            constraint = f"forall {rule_obj.selector}: {rule_obj.assertion}"
        if rule_obj.params:
            param_str = ", ".join(f"{key}={value}" for key, value in rule_obj.params.items())
            constraint = f"{constraint} ({param_str})"
        if rule_obj.scope:
            constraint = f"{constraint} [dim]scope={rule_obj.scope}[/]"
        table.add_row(rule_obj.name, rule_obj.type, constraint)
    console.print(table)


@rule.command("rm")
@click.argument("name")
def rule_rm(name: str) -> None:
    """Remove an architectural rule by name."""
    from smg.storage import load_rules, save_rules

    _graph, root = _load()
    rules = load_rules(root)
    new_rules = [rule for rule in rules if rule.name != name]
    if len(new_rules) == len(rules):
        err_console.print(f"[red]Error:[/] rule {name!r} not found.")
        sys.exit(EXIT_NOT_FOUND)
    save_rules(new_rules, root)
    console.print(f"Rule {name!r} removed.")


@main.command()
@click.argument("name", required=False, default=None)
@click.option(
    "--format",
    "fmt",
    default=None,
    type=click.Choice(["text", "json"]),
    help="Output format",
)
@click.option("--full", is_flag=True, help="Show all violation witnesses (no truncation)")
def check(name: str | None, fmt: str | None, full: bool) -> None:
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

    from smg.rules import check_all
    from smg.storage import load_concepts, load_rules

    graph, root = _load()
    rules = load_rules(root)
    concepts = load_concepts(root)
    fmt = _auto_fmt(fmt)

    if not rules:
        if fmt == "json":
            click.echo(json_mod.dumps({"rules": [], "violations": [], "status": "no_rules"}))
        else:
            console.print("No rules defined. Add one with [bold]smg rule add[/].")
        return

    if name:
        matched = [rule for rule in rules if rule.name == name]
        if not matched:
            err_console.print(f"[red]Error:[/] rule {name!r} not found.")
            sys.exit(EXIT_NOT_FOUND)
        rules = matched

    try:
        violations = check_all(rules, graph, concepts=concepts)
    except ValueError as exc:
        err_console.print(f"[red]Error:[/] {exc}")
        sys.exit(EXIT_VALIDATION)

    if fmt == "json":
        data = {
            "rules_checked": len(rules),
            "violations": [violation.to_dict() for violation in violations],
            "status": "fail" if violations else "pass",
        }
        click.echo(json_mod.dumps(data, indent=2))
    else:
        witness_cap = 0 if full else 10
        for rule_obj in rules:
            violation = next((item for item in violations if item.rule_name == rule_obj.name), None)
            if violation is None:
                console.print(f"[green]PASS[/]  {rule_obj.name}")
                continue
            console.print(f"[red]FAIL[/]  {rule_obj.name}: {violation.message}")
            lines = _render_violation_witnesses(violation)
            show_lines = lines if witness_cap == 0 else lines[:witness_cap]
            for line in show_lines:
                console.print(f"        {line}")
            if witness_cap and len(lines) > witness_cap:
                console.print(f"        [dim]... and {len(lines) - witness_cap} more (use --full)[/]")

    if violations:
        sys.exit(EXIT_NOT_FOUND)


def _render_violation_witnesses(violation) -> list[str]:
    lines: list[str] = []
    for witness in violation.witnesses or []:
        if witness.kind == "edge" and witness.edges:
            for edge in witness.edges:
                rel = edge.get("rel", "?")
                line = f"{edge['source']} --{rel}--> {edge['target']}"
                if "source_layer" in edge and "target_layer" in edge:
                    line += f" ({edge['source_layer']} -> {edge['target_layer']})"
                lines.append(line)
        elif witness.kind == "node" and witness.nodes:
            lines.extend(witness.nodes)
        elif witness.kind == "cycle" and witness.cycle:
            lines.append(" -> ".join(witness.cycle) + f" -> {witness.cycle[0]}")
        elif witness.kind == "predicate" and witness.subject and witness.facts:
            facts = ", ".join(f"{name}={value}" for name, value in witness.facts.items())
            lines.append(f"{witness.subject}: {facts}")
    return lines
