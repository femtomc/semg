"""Language-agnostic AST metrics computed from tree-sitter nodes.

All metrics are computed from the AST structure alone. The only
language-specific input is a BranchMap that maps tree-sitter node
types to semantic roles (branches, loops, boolean operators, etc.).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from tree_sitter import Node as TSNode


@dataclass(frozen=True)
class BranchMap:
    """Per-language mapping of tree-sitter node types to semantic roles."""

    branch_nodes: frozenset[str]
    """Nodes that represent a decision point (if, elif, for, while, case, catch, etc.)."""

    boolean_operators: frozenset[str]
    """Node types for boolean/logical operators (and/or, &&/||)."""

    nesting_nodes: frozenset[str]
    """Nodes that increase nesting depth for cognitive complexity."""

    loop_nodes: frozenset[str]
    """Loop constructs (for, while, do-while, etc.)."""

    function_nodes: frozenset[str]
    """Function/method definition nodes (to avoid descending into nested functions)."""

    # For JS/TS where binary_expression covers all operators, not just logical ones
    logical_operator_tokens: frozenset[str] = frozenset()
    """If boolean_operators match a general node type (e.g. binary_expression),
    these are the operator tokens that count as logical (e.g. '&&', '||')."""


@dataclass
class NodeMetrics:
    """Metrics for a single function, method, or class."""

    cyclomatic_complexity: int = 1
    cognitive_complexity: int = 0
    max_nesting_depth: int = 0
    lines_of_code: int = 0
    parameter_count: int = 0
    return_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_metrics(func_node: TSNode, branch_map: BranchMap) -> NodeMetrics:
    """Compute metrics for a function/method node."""
    metrics = NodeMetrics()

    # Lines of code
    metrics.lines_of_code = func_node.end_point[0] - func_node.start_point[0] + 1

    # Parameter count
    params = func_node.child_by_field_name("parameters") or func_node.child_by_field_name("formal_parameters")
    if params is not None:
        metrics.parameter_count = sum(1 for c in params.children if c.is_named and c.type not in ("comment",))

    # Walk the body for complexity metrics
    body = func_node.child_by_field_name("body")
    if body is not None:
        cc, cog, max_depth, returns = _walk_for_metrics(body, branch_map, nesting=0)
        metrics.cyclomatic_complexity = 1 + cc
        metrics.cognitive_complexity = cog
        metrics.max_nesting_depth = max_depth
        metrics.return_count = returns

    return metrics


def _walk_for_metrics(
    root: TSNode,
    bm: BranchMap,
    nesting: int,
) -> tuple[int, int, int, int]:
    """Iteratively walk AST, returning (cc_increments, cognitive, max_depth, return_count)."""
    cc = 0
    cog = 0
    max_depth = nesting
    returns = 0

    _skip = bm.function_nodes | frozenset({"class_definition", "class_declaration"})
    stack: list[tuple[TSNode, int]] = [(root, nesting)]

    while stack:
        node, nest = stack.pop()
        for child in node.children:
            ctype = child.type
            # Don't descend into nested function/class definitions
            if ctype in _skip:
                continue

            # Branch node: contributes to both CC and cognitive complexity
            if ctype in bm.branch_nodes:
                cc += 1
                cog += 1 + nest  # cognitive: +1 base, +nesting penalty

            # Boolean operators
            if ctype in bm.boolean_operators:
                if bm.logical_operator_tokens:
                    if _has_logical_operator(child, bm.logical_operator_tokens):
                        cc += 1
                        cog += 1
                else:
                    cc += 1
                    cog += 1

            # Return statements
            if ctype == "return_statement":
                returns += 1

            # Track nesting depth
            child_nesting = nest + 1 if ctype in bm.nesting_nodes else nest
            if child_nesting > max_depth:
                max_depth = child_nesting

            stack.append((child, child_nesting))

    return cc, cog, max_depth, returns


def _has_logical_operator(node: TSNode, tokens: frozenset[str]) -> bool:
    """Check if a binary_expression node uses a logical operator (&&, ||, etc.)."""
    for child in node.children:
        if not child.is_named and child.text is not None:
            if child.text.decode() in tokens:
                return True
    return False


# --- Per-language branch maps ---

PYTHON_BRANCH_MAP = BranchMap(
    branch_nodes=frozenset({
        "if_statement", "elif_clause", "for_statement", "while_statement",
        "except_clause", "with_statement", "conditional_expression",
        "match_statement", "case_clause",
    }),
    boolean_operators=frozenset({"boolean_operator"}),
    nesting_nodes=frozenset({
        "if_statement", "for_statement", "while_statement",
        "try_statement", "with_statement", "match_statement",
    }),
    loop_nodes=frozenset({"for_statement", "while_statement"}),
    function_nodes=frozenset({"function_definition"}),
)

JS_BRANCH_MAP = BranchMap(
    branch_nodes=frozenset({
        "if_statement", "else_clause",
        "for_statement", "while_statement", "do_statement",
        "for_in_statement", "for_of_statement",
        "switch_case", "catch_clause", "ternary_expression",
    }),
    boolean_operators=frozenset({"binary_expression"}),
    nesting_nodes=frozenset({
        "if_statement", "for_statement", "while_statement", "do_statement",
        "for_in_statement", "for_of_statement",
        "try_statement", "switch_statement",
    }),
    loop_nodes=frozenset({
        "for_statement", "while_statement", "do_statement",
        "for_in_statement", "for_of_statement",
    }),
    function_nodes=frozenset({"function_declaration", "method_definition", "arrow_function"}),
    logical_operator_tokens=frozenset({"&&", "||", "??"}),
)
