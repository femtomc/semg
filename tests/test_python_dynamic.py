"""Tests for deferred and dynamic import extraction in PythonExtractor."""

from smg.langs.python import PythonExtractor
from smg.model import RelType

# --- Fixture 1: deferred imports inside function/method bodies ---

SOURCE_DEFERRED = b"""\
import os

def init():
    import json
    from pathlib import Path

class Loader:
    def load(self):
        from xml.etree import ElementTree
"""


def test_deferred_imports_produce_edges():
    extractor = PythonExtractor()
    result = extractor.extract(SOURCE_DEFERRED, "app/loader.py", "app.loader")
    import_edges = [e for e in result.edges if e.rel == RelType.IMPORTS]
    targets = {e.target for e in import_edges}
    assert "os" in targets
    assert "json" in targets
    assert "pathlib" in targets
    assert "xml.etree" in targets


def test_deferred_imports_have_metadata():
    extractor = PythonExtractor()
    result = extractor.extract(SOURCE_DEFERRED, "app/loader.py", "app.loader")
    deferred = [e for e in result.edges if e.rel == RelType.IMPORTS and e.metadata.get("deferred")]
    assert len(deferred) == 3  # json, pathlib, xml.etree


# --- Fixture 2: importlib.import_module / __import__ ---

SOURCE_IMPORTLIB = b"""\
import importlib

mod = importlib.import_module("plugins.auth")
other = __import__("legacy.compat")
dynamic = importlib.import_module(compute_name())
"""


def test_importlib_string_literal_produces_import_edge():
    extractor = PythonExtractor()
    result = extractor.extract(SOURCE_IMPORTLIB, "app/main.py", "app.main")
    import_edges = [e for e in result.edges if e.rel == RelType.IMPORTS]
    targets = {e.target for e in import_edges}
    assert "plugins.auth" in targets
    assert "legacy.compat" in targets
    dynamic_targets = {e.target for e in import_edges if e.metadata.get("dynamic")}
    assert "plugins.auth" in dynamic_targets
    assert "legacy.compat" in dynamic_targets


def test_importlib_non_literal_skipped():
    extractor = PythonExtractor()
    result = extractor.extract(SOURCE_IMPORTLIB, "app/main.py", "app.main")
    import_edges = [e for e in result.edges if e.rel == RelType.IMPORTS]
    # importlib (top-level static), plugins.auth (dynamic), legacy.compat (dynamic)
    assert len(import_edges) == 3


# --- Fixture 3: dotted-relative strings are skipped ---

SOURCE_RELATIVE = b"""\
import importlib
mod = importlib.import_module(".sub", "parent")
"""


def test_dotted_relative_dynamic_import_skipped():
    extractor = PythonExtractor()
    result = extractor.extract(SOURCE_RELATIVE, "app/plug.py", "app.plug")
    dynamic = [e for e in result.edges if e.rel == RelType.IMPORTS and e.metadata.get("dynamic")]
    assert len(dynamic) == 0
