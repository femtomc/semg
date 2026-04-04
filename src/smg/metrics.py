"""Language-agnostic AST metrics computed from tree-sitter nodes.

All metrics are computed from the AST structure alone. The only
language-specific input is a BranchMap that maps tree-sitter node
types to semantic roles (branches, loops, boolean operators, etc.).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import xxhash
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


@dataclass
class ExtractionMeta:
    """Combined result of the fused metrics + structure hash walk."""

    metrics: NodeMetrics
    structure_hash: str


def compute_metrics(func_node: TSNode, branch_map: BranchMap) -> NodeMetrics:
    """Compute metrics for a function/method node (metrics only, no hash)."""
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


def compute_metrics_and_hash(func_node: TSNode, branch_map: BranchMap) -> ExtractionMeta:
    """Compute metrics AND structure hash in a single AST walk.

    Fuses what were previously two separate DFS traversals (compute_metrics
    + structure_hash) into one pass over the AST. This halves the number of
    Python-C boundary crossings for tree-sitter node access.
    """
    metrics = NodeMetrics()
    metrics.lines_of_code = func_node.end_point[0] - func_node.start_point[0] + 1

    # Parameter count (small walk, not worth fusing)
    params = func_node.child_by_field_name("parameters") or func_node.child_by_field_name("formal_parameters")
    if params is not None:
        metrics.parameter_count = sum(1 for c in params.children if c.is_named and c.type not in ("comment",))

    # Fused walk: metrics (on body) + structure hash (on full node)
    h = xxhash.xxh64()

    # Hash the function node itself and its preamble (before body)
    body = func_node.child_by_field_name("body")

    # Structure hash walk over the full node, metrics walk over the body
    cc, cog, max_depth, returns = _walk_fused(func_node, body, branch_map, h)
    metrics.cyclomatic_complexity = 1 + cc
    metrics.cognitive_complexity = cog
    metrics.max_nesting_depth = max_depth
    metrics.return_count = returns

    return ExtractionMeta(metrics=metrics, structure_hash=h.hexdigest())


# Sets shared with hashing.py — keep in sync
_HASH_SKIP = frozenset({
    "comment", "line_comment", "block_comment",
})

_HASH_NORMALIZE = frozenset({
    "identifier", "type_identifier", "field_identifier",
    "string", "string_content", "string_literal",
    "integer", "integer_literal", "float", "float_literal",
    "number", "true", "false", "none", "null",
})


def _walk_fused(
    root: TSNode,
    body: TSNode | None,
    bm: BranchMap,
    h: xxhash.xxh64,
) -> tuple[int, int, int, int]:
    """Single DFS walk that computes both structure hash and metrics.

    The structure hash covers the entire `root` subtree.
    The metrics (CC, cognitive, nesting, returns) cover only the `body` subtree
    (skipping nested function/class definitions, matching compute_metrics behavior).

    Returns (cc_increments, cognitive, max_depth, return_count).
    """
    cc = 0
    cog = 0
    max_depth = 0
    returns = 0

    _metrics_skip = bm.function_nodes | frozenset({"class_definition", "class_declaration"})
    # Identify body node by byte range (not Python object id, since tree-sitter
    # creates new wrapper objects for the same underlying node)
    body_range = (body.start_byte, body.end_byte) if body is not None else (-1, -1)

    # Stack entries: (node, nesting_depth, in_body, is_end_marker)
    stack: list = [(root, 0, False, False)]

    while stack:
        entry = stack.pop()

        if entry[3]:
            # End-of-children marker for structure hash
            h.update(b")")
            continue

        node, nest, in_body, _ = entry
        ctype = node.type

        # Structure hash logic
        if ctype in _HASH_SKIP:
            continue
        if ctype in _HASH_NORMALIZE:
            h.update(b"_")
            continue

        h.update(ctype.encode())
        h.update(b"(")

        # Check if we're entering the body
        child_in_body = in_body or (node.start_byte, node.end_byte) == body_range

        # Metrics logic (only within body, skip nested functions/classes)
        if child_in_body and in_body:
            if ctype in bm.branch_nodes:
                cc += 1
                cog += 1 + nest
            if ctype in bm.boolean_operators:
                if bm.logical_operator_tokens:
                    if _has_logical_operator(node, bm.logical_operator_tokens):
                        cc += 1
                        cog += 1
                else:
                    cc += 1
                    cog += 1
            if ctype == "return_statement":
                returns += 1

        # Nesting depth for metrics
        child_nest = nest
        if child_in_body and ctype in bm.nesting_nodes:
            child_nest = nest + 1
            if child_nest > max_depth:
                max_depth = child_nest

        # Push end-of-children marker for hash, then children in reverse
        stack.append((None, 0, False, True))

        children = node.children
        for i in range(len(children) - 1, -1, -1):
            child = children[i]
            child_type = child.type
            if child_type in _HASH_SKIP:
                continue
            # For metrics: skip nested functions/classes within body
            skip_metrics = child_in_body and child_type in _metrics_skip
            stack.append((child, child_nest, child_in_body and not skip_metrics, False))

    return cc, cog, max_depth, returns


def compute_structure_hash(node: TSNode) -> str:
    """Compute structure hash only (no metrics). For classes and non-function entities."""
    h = xxhash.xxh64()
    stack: list[TSNode | None] = [node]
    while stack:
        n = stack.pop()
        if n is None:
            h.update(b")")
            continue
        if n.type in _HASH_SKIP:
            continue
        if n.type in _HASH_NORMALIZE:
            h.update(b"_")
            continue
        h.update(n.type.encode())
        h.update(b"(")
        stack.append(None)
        for i in range(n.child_count - 1, -1, -1):
            child = n.children[i]
            if child.type not in _HASH_SKIP:
                stack.append(child)
    return h.hexdigest()


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
