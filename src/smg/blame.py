"""Entity-level blame: map graph entities to their most recent commits."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from smg.graph import SemGraph
from smg.model import Node


@dataclass
class BlameEntry:
    name: str
    node_type: str
    file: str
    line: int
    end_line: int
    commit: str
    author: str
    date: str
    summary: str


def blame_entity(node: Node, root: Path) -> BlameEntry | None:
    """Find the most recent commit that touched an entity's line range."""
    if not node.file or node.line is None:
        return None
    end = node.end_line or node.line
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "-1",
                "--format=%H%n%ae%n%ai%n%s",
                f"-L{node.line},{end}:{node.file}",
            ],
            capture_output=True,
            text=True,
            cwd=root,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None

    lines = result.stdout.strip().splitlines()
    if len(lines) < 4:
        return None

    return BlameEntry(
        name=node.name,
        node_type=node.type.value,
        file=node.file,
        line=node.line,
        end_line=end,
        commit=lines[0][:12],
        author=lines[1],
        date=lines[2][:10],
        summary=lines[3],
    )


def blame_file(graph: SemGraph, file_path: str, root: Path) -> list[BlameEntry]:
    """Blame all entities in a given file."""
    entries: list[BlameEntry] = []
    for node in graph.nodes.values():
        if node.file != file_path:
            continue
        entry = blame_entity(node, root)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda e: e.line)
    return entries
