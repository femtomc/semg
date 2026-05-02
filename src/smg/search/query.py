"""Query the search cache."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from smg.search.schema import (
    check_schema_version,
    split_identifier,
)

if TYPE_CHECKING:
    from smg.graph import SemGraph

# Whitelist regex: only purely identifier-shaped input gets rewritten
_IDENTIFIER_SHAPED_RE = re.compile(r"^[A-Za-z0-9._\s]+$")
_FTS5_OPERATORS = {"AND", "OR", "NOT", "NEAR"}


def normalize_query(q: str) -> str:
    """Normalize a user query for FTS5 MATCH.

    Whitelist rule: the query is rewritten to AND-joined tokens ONLY when
    it is strictly identifier-shaped and contains no FTS5 operator keywords.
    Everything else passes through unchanged.
    """
    s = q.strip()
    if not s:
        return s
    if not _IDENTIFIER_SHAPED_RE.match(s):
        return s
    tokens = s.split()
    if any(t in _FTS5_OPERATORS for t in tokens):
        return s
    parts = split_identifier(s).split()
    if not parts:
        return s
    return " AND ".join(parts)


@dataclass
class SearchHit:
    """A single search result."""

    rank: int
    name: str
    kind: str
    file: str | None
    line_start: int | None
    line_end: int | None
    docstring: str | None
    score: float

    @property
    def location(self) -> str:
        if self.file:
            if self.line_start is not None:
                return f"{self.file}:{self.line_start}"
            return self.file
        return "-"

    @property
    def snippet(self) -> str:
        if self.docstring:
            first_line = self.docstring.split("\n")[0].strip()
            return first_line[:80]
        return "-"


def search_nodes(
    db_path_or_conn: Path | sqlite3.Connection,
    query: str,
    *,
    kind: str | None = None,
    limit: int = 10,
    graph: SemGraph | None = None,
    root: Path | None = None,
) -> tuple[list[SearchHit], int]:
    """Search the FTS index. Returns (hits, total_matching).

    If *db_path_or_conn* is a Path, opens a connection. If the db is
    missing and *graph* + *root* are provided, auto-rebuilds first.
    """
    if isinstance(db_path_or_conn, Path):
        db_path = db_path_or_conn
        if not db_path.exists():
            if graph is not None and root is not None:
                from smg.search.indexer import rebuild_search_index

                rebuild_search_index(graph, root)
            else:
                raise FileNotFoundError(f"Search index not found: {db_path}. Run 'smg scan' or 'smg index'.")
        conn = sqlite3.connect(str(db_path))
        own_conn = True
        # Check schema version; rebuild if stale
        if not check_schema_version(conn):
            conn.close()
            if graph is not None and root is not None:
                from smg.search.indexer import rebuild_search_index

                db_path.unlink(missing_ok=True)
                rebuild_search_index(graph, root)
                conn = sqlite3.connect(str(db_path))
            else:
                raise RuntimeError("Search index schema is outdated. Run 'smg scan'.")
    else:
        conn = db_path_or_conn
        own_conn = False

    normalized = normalize_query(query)

    try:
        # Count total matches
        count_sql = (
            "SELECT COUNT(*) FROM nodes JOIN nodes_fts ON nodes.node_id = nodes_fts.rowid WHERE nodes_fts MATCH ?"
        )
        count_params: list[object] = [normalized]
        if kind:
            count_sql += " AND nodes.kind = ?"
            count_params.append(kind)

        total = conn.execute(count_sql, count_params).fetchone()[0]

        # Fetch ranked results
        select_sql = (
            "SELECT nodes.name, nodes.kind, nodes.file, nodes.line_start, "
            "nodes.line_end, nodes.docstring, rank "
            "FROM nodes "
            "JOIN nodes_fts ON nodes.node_id = nodes_fts.rowid "
            "WHERE nodes_fts MATCH ?"
        )
        params: list[object] = [normalized]
        if kind:
            select_sql += " AND nodes.kind = ?"
            params.append(kind)

        select_sql += " ORDER BY rank ASC, nodes.name ASC"
        if limit > 0:
            select_sql += " LIMIT ?"
            params.append(limit)

        cursor = conn.execute(select_sql, params)
        hits: list[SearchHit] = []
        for i, row in enumerate(cursor, 1):
            hits.append(
                SearchHit(
                    rank=i,
                    name=row[0],
                    kind=row[1],
                    file=row[2],
                    line_start=row[3],
                    line_end=row[4],
                    docstring=row[5],
                    score=row[6],
                )
            )
        return hits, total
    except sqlite3.OperationalError as exc:
        raise ValueError(f"invalid search query: {query!r}") from exc
    finally:
        if own_conn:
            conn.close()
