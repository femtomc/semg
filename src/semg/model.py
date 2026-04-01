from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    PACKAGE = "package"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    VARIABLE = "variable"
    CONSTANT = "constant"
    TYPE = "type"
    ENDPOINT = "endpoint"
    CONFIG = "config"

    @classmethod
    def _missing_(cls, value: str) -> NodeType | None:
        # Escape hatch: accept any string as a custom node type
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj._name_ = value.upper()
        return obj


class RelType(str, Enum):
    CALLS = "calls"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    CONTAINS = "contains"
    DEPENDS_ON = "depends_on"
    IMPORTS = "imports"
    RETURNS = "returns"
    ACCEPTS = "accepts"
    OVERRIDES = "overrides"
    DECORATES = "decorates"
    TESTS = "tests"

    @classmethod
    def _missing_(cls, value: str) -> RelType | None:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj._name_ = value.upper()
        return obj


@dataclass
class Node:
    name: str
    type: NodeType
    file: str | None = None
    line: int | None = None
    docstring: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": "node", "name": self.name, "type": self.type.value}
        if self.file is not None:
            d["file"] = self.file
        if self.line is not None:
            d["line"] = self.line
        if self.docstring is not None:
            d["docstring"] = self.docstring
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Node:
        return cls(
            name=d["name"],
            type=NodeType(d["type"]),
            file=d.get("file"),
            line=d.get("line"),
            docstring=d.get("docstring"),
            metadata=d.get("metadata", {}),
        )


@dataclass
class Edge:
    source: str
    target: str
    rel: RelType
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source, self.rel.value, self.target)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": "edge",
            "source": self.source,
            "rel": self.rel.value,
            "target": self.target,
        }
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Edge:
        return cls(
            source=d["source"],
            target=d["target"],
            rel=RelType(d["rel"]),
            metadata=d.get("metadata", {}),
        )
