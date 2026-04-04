"""Structural and content hashing for tree-sitter AST nodes.

Uses xxHash64 for speed — ~10x faster than SHA-256, and we don't need
cryptographic strength (these hashes are for matching within a single diff).

For functions/methods, prefer metrics.compute_metrics_and_hash() which
fuses the structure hash walk with the metrics walk in a single pass.
This module's structure_hash() is for non-function entities (classes, structs)
that don't need metrics.
"""
from __future__ import annotations

import xxhash
from tree_sitter import Node as TSNode

from smg.metrics import _HASH_SKIP, _HASH_NORMALIZE


def content_hash(source: bytes, start_byte: int, end_byte: int) -> str:
    """xxHash64 of the exact source bytes for a node's range, as 16 hex chars."""
    return xxhash.xxh64(source[start_byte:end_byte]).hexdigest()


def structure_hash(node: TSNode) -> str:
    """xxHash64 of the normalized AST structure.

    Walks the tree depth-first. Comment nodes are skipped entirely.
    Identifier and literal nodes are replaced with a placeholder.
    All other nodes contribute their type to the hash.

    For functions/methods, use metrics.compute_metrics_and_hash() instead
    to avoid a redundant second walk.
    """
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
