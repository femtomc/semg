"""Tests for structural and content hashing."""

from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from smg.hashing import content_hash, structure_hash

_LANG = Language(tspython.language())
_PARSER = Parser(_LANG)


def _parse(code: str):
    return _PARSER.parse(code.encode())


def test_content_hash_deterministic():
    source = b"def foo():\n    return 1\n"
    h1 = content_hash(source, 0, len(source))
    h2 = content_hash(source, 0, len(source))
    assert h1 == h2
    assert len(h1) == 16


def test_content_hash_differs_on_change():
    a = b"def foo():\n    return 1\n"
    b_ = b"def foo():\n    return 2\n"
    assert content_hash(a, 0, len(a)) != content_hash(b_, 0, len(b_))


def test_structure_hash_same_for_renamed_function():
    """Two functions with identical structure but different names should have the same structure hash."""
    tree_a = _parse("def foo(x):\n    return x + 1\n")
    tree_b = _parse("def bar(x):\n    return x + 1\n")
    # Get the function_definition node
    func_a = tree_a.root_node.children[0]
    func_b = tree_b.root_node.children[0]
    assert func_a.type == "function_definition"
    assert func_b.type == "function_definition"
    assert structure_hash(func_a) == structure_hash(func_b)


def test_structure_hash_differs_for_different_structure():
    """Structurally different functions should have different hashes."""
    tree_a = _parse("def foo(x):\n    return x + 1\n")
    tree_b = _parse("def foo(x):\n    if x > 0:\n        return x\n    return 0\n")
    func_a = tree_a.root_node.children[0]
    func_b = tree_b.root_node.children[0]
    assert structure_hash(func_a) != structure_hash(func_b)


def test_structure_hash_ignores_comments():
    """Adding or changing comments should not change the structure hash."""
    tree_a = _parse("def foo(x):\n    return x + 1\n")
    tree_b = _parse("def foo(x):\n    # a comment\n    return x + 1\n")
    func_a = tree_a.root_node.children[0]
    func_b = tree_b.root_node.children[0]
    assert structure_hash(func_a) == structure_hash(func_b)


def test_structure_hash_ignores_string_literals():
    """Different string literals with same structure should hash the same."""
    tree_a = _parse('def foo():\n    return "hello"\n')
    tree_b = _parse('def foo():\n    return "world"\n')
    func_a = tree_a.root_node.children[0]
    func_b = tree_b.root_node.children[0]
    assert structure_hash(func_a) == structure_hash(func_b)


def test_content_hash_sensitive_to_variable_rename():
    """Content hash changes when a variable is renamed."""
    a = b"def foo(x):\n    return x + 1\n"
    b_ = b"def foo(y):\n    return y + 1\n"
    assert content_hash(a, 0, len(a)) != content_hash(b_, 0, len(b_))


def test_structure_hash_same_for_variable_rename():
    """Renaming a variable should NOT change structure hash (identifiers are stripped)."""
    tree_a = _parse("def foo(x):\n    return x + 1\n")
    tree_b = _parse("def foo(y):\n    return y + 1\n")
    func_a = tree_a.root_node.children[0]
    func_b = tree_b.root_node.children[0]
    assert structure_hash(func_a) == structure_hash(func_b)
