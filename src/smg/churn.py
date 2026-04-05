"""Git-history churn analysis.

Maps git commit hunks to graph entities by file+line overlap.
Computes per-entity churn counts without rebuilding graphs.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from smg.graph import SemGraph


@dataclass
class ChurnResult:
    entity_churn: dict[str, int]
    file_churn: dict[str, int]
    total_commits: int
    time_range: str


def compute_churn(
    graph: SemGraph,
    root: Path,
    days: int = 90,
    max_commits: int | None = None,
    since_ref: str | None = None,
) -> ChurnResult:
    """Count how many commits touched each entity in the graph."""
    file_index = _build_file_index(graph)
    hunks = _git_hunks(root, days=days, max_commits=max_commits, since_ref=since_ref)

    entity_touches: set[tuple[str, str]] = set()  # (commit, entity)
    file_churn: dict[str, int] = defaultdict(int)
    commit_set: set[str] = set()

    for hunk in hunks:
        commit_set.add(hunk.commit)
        file_churn[hunk.file] += 1

        if hunk.file not in file_index:
            continue
        for start, end, name in file_index[hunk.file]:
            if start <= hunk.end_line and hunk.start_line <= end:
                entity_touches.add((hunk.commit, name))

    entity_churn: dict[str, int] = defaultdict(int)
    for _, name in entity_touches:
        entity_churn[name] += 1

    time_desc = f"last {days} days"
    if since_ref:
        time_desc = f"since {since_ref}"
    if max_commits:
        time_desc = f"last {max_commits} commits"

    return ChurnResult(
        entity_churn=dict(entity_churn),
        file_churn=dict(file_churn),
        total_commits=len(commit_set),
        time_range=time_desc,
    )


@dataclass
class _Hunk:
    commit: str
    file: str
    start_line: int
    end_line: int


def _build_file_index(graph: SemGraph) -> dict[str, list[tuple[int, int, str]]]:
    """Build file -> [(start_line, end_line, node_name)] index."""
    index: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.file and node.line is not None:
            end = node.end_line if node.end_line is not None else node.line
            index[node.file].append((node.line, end, node.name))
    for entries in index.values():
        entries.sort()
    return dict(index)


def _git_hunks(
    root: Path,
    days: int = 90,
    max_commits: int | None = None,
    since_ref: str | None = None,
) -> list[_Hunk]:
    """Parse git log to extract per-file changed line ranges."""
    cmd = ["git", "log", "--unified=0", "--no-color", "-p"]

    if since_ref:
        cmd.append(f"{since_ref}..HEAD")
    else:
        cmd.extend(["--since", f"{days}.days.ago"])

    if max_commits:
        cmd.extend(["-n", str(max_commits)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
    except FileNotFoundError:
        return []

    if result.returncode != 0:
        return []

    return _parse_unified_diff(result.stdout)


def _parse_unified_diff(output: str) -> list[_Hunk]:
    """Parse unified diff output to extract hunks with line numbers."""
    hunks: list[_Hunk] = []
    current_commit = ""
    current_file = ""

    for line in output.splitlines():
        if line.startswith("commit "):
            current_commit = line[7:].strip()[:12]
        elif line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("@@ ") and current_file and current_commit:
            parts = line.split(" ")
            if len(parts) >= 3:
                new_range = parts[2]
                start, count = _parse_range(new_range.lstrip("+"))
                if start > 0:
                    hunks.append(
                        _Hunk(
                            commit=current_commit,
                            file=current_file,
                            start_line=start,
                            end_line=start + max(count - 1, 0),
                        )
                    )

    return hunks


def _parse_range(s: str) -> tuple[int, int]:
    """Parse '10,5' or '10' into (start, count)."""
    if "," in s:
        parts = s.split(",", 1)
        return int(parts[0]), int(parts[1])
    return int(s), 1
