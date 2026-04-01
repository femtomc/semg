from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from semg.model import Edge, Node


@dataclass
class ExtractResult:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


@runtime_checkable
class LanguageExtractor(Protocol):
    extensions: list[str]

    def extract(self, source: bytes, file_path: str, module_name: str) -> ExtractResult: ...


# Extension -> extractor mapping, populated by register()
REGISTRY: dict[str, LanguageExtractor] = {}


def register(extractor: LanguageExtractor) -> None:
    for ext in extractor.extensions:
        REGISTRY[ext] = extractor


def get_extractor(extension: str) -> LanguageExtractor | None:
    return REGISTRY.get(extension)


def load_extractors() -> None:
    """Import all lang modules to trigger registration. Silently skip missing grammars."""
    # Each module catches its own ImportError if the grammar isn't installed
    try:
        from semg.langs import python as _  # noqa: F401
    except ImportError:
        pass
