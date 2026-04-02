from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from smg.model import Edge, Node

if TYPE_CHECKING:
    from smg.metrics import BranchMap


@dataclass
class ExtractResult:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


@runtime_checkable
class LanguageExtractor(Protocol):
    extensions: list[str]
    branch_map: BranchMap

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
        from smg.langs import python as _  # noqa: F401
    except ImportError:
        pass
    try:
        from smg.langs import javascript as _  # noqa: F401, F811
    except ImportError:
        pass
    try:
        from smg.langs import zig as _  # noqa: F401, F811
    except ImportError:
        pass
    try:
        from smg.langs import c as _  # noqa: F401, F811
    except ImportError:
        pass
