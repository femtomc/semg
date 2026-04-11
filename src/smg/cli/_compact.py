"""Canonical compact-table formatter Canonical compact-table formatter..

One function, one spec. Every listing command in SMG uses this to produce
byte-identical tabular output in pipes and TTYs.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence

# em dash and ellipsis are allowed by the character-set policy
_FOOTER_TEMPLATE = "(showing {n} of {m} -- use --limit 0 for all, --json for machine-readable)"
_TRUNCATION_LINE = "(output truncated at 16 KB -- run with --json for full data or --limit to scope down)"
_TRUNCATION_LINE_BYTES = len(_TRUNCATION_LINE.encode("utf-8")) + 1  # +1 for \n = 86
_BODY_BUDGET = 16384 - _TRUNCATION_LINE_BYTES  # 16298

# Column spec: (header_lowercase, row_key, options)
# options: {"align": "left"|"right", "max_width": int}
Column = tuple[str, str, dict]


def _wcswidth(s: str) -> int:
    """Display width of *s* using East Asian width properties."""
    w = 0
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith("M"):  # combining marks
            continue
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("W", "F") else 1
    return w


def _normalize_cell(value: object) -> str:
    """Normalize a cell value to a display string."""
    if value is None:
        return "-"
    s = str(value)
    if not s:
        return ""
    # collapse whitespace variants
    s = s.replace("\t", " ").replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    # drop control bytes (except the ones we already handled)
    out: list[str] = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith("C") and ch not in ("\t",):
            continue
        if cat == "Cn":  # unassigned
            continue
        out.append(ch)
    return "".join(out)


def _truncate_to_width(s: str, max_w: int) -> str:
    """Truncate *s* so its display width is at most *max_w*."""
    if max_w <= 0:
        return ""
    w = _wcswidth(s)
    if w <= max_w:
        return s
    # keep prefix up to max_w - 2, then append ".."
    target = max_w - 2  # reserve 2 columns for ".."
    acc = 0
    result: list[str] = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith("M"):
            result.append(ch)
            continue
        eaw = unicodedata.east_asian_width(ch)
        cw = 2 if eaw in ("W", "F") else 1
        if acc + cw > target:
            break
        acc += cw
        result.append(ch)
    return "".join(result) + ".."


def compact_table(
    rows: Sequence[Mapping[str, object]],
    columns: Sequence[Column],
    *,
    total: int | None = None,
) -> str:
    """Render rows as the canonical compact table.

    Column = tuple[header_lowercase, row_key, options]
      options accepts:
        "align":     "left" | "right"   (default: "left")
        "max_width": int                 (default: 40)
    total: if provided and total > len(rows), a footer is emitted.
    """
    n_cols = len(columns)

    # Extract column metadata
    headers: list[str] = []
    keys: list[str] = []
    aligns: list[str] = []
    max_widths: list[int] = []
    for header, key, opts in columns:
        headers.append(header)
        keys.append(key)
        aligns.append(opts.get("align", "left"))
        max_widths.append(opts.get("max_width", 40))

    # Normalize and truncate all cells
    display_cells: list[list[str]] = []
    for row in rows:
        cells: list[str] = []
        for i in range(n_cols):
            raw = row.get(keys[i])
            norm = _normalize_cell(raw)
            cells.append(_truncate_to_width(norm, max_widths[i]))
        display_cells.append(cells)

    # Truncate headers under max_width
    trunc_headers = [_truncate_to_width(h, mw) for h, mw in zip(headers, max_widths)]

    # Compute column widths
    col_widths: list[int] = []
    for i in range(n_cols):
        hw = _wcswidth(trunc_headers[i])
        cell_max = max((_wcswidth(r[i]) for r in display_cells), default=0)
        col_widths.append(max(hw, cell_max))

    # Build rows
    def _pad(s: str, w: int, align: str, is_last: bool) -> str:
        sw = _wcswidth(s)
        pad = w - sw
        if pad < 0:
            pad = 0
        if is_last:
            # last column: no trailing padding (rstrip will handle it)
            if align == "right":
                return " " * pad + s
            return s
        if align == "right":
            return " " * pad + s
        return s + " " * pad

    gutter = "  "
    lines: list[str] = []

    # Header row
    parts = [_pad(trunc_headers[i], col_widths[i], aligns[i], i == n_cols - 1) for i in range(n_cols)]
    lines.append(gutter.join(parts).rstrip())

    # Separator row
    sep_parts = ["-" * col_widths[i] for i in range(n_cols)]
    lines.append(gutter.join(sep_parts).rstrip())

    # Body rows
    for cells in display_cells:
        parts = [_pad(cells[i], col_widths[i], aligns[i], i == n_cols - 1) for i in range(n_cols)]
        lines.append(gutter.join(parts).rstrip())

    # Footer
    if total is not None and total > len(rows):
        lines.append(_FOOTER_TEMPLATE.format(n=len(rows), m=total))

    # Join and apply 16 KB cap
    output = "\n".join(lines) + "\n"
    output_bytes = output.encode("utf-8")
    if len(output_bytes) <= _BODY_BUDGET:
        return output.rstrip("\n")

    # Truncate at line boundaries
    budget = _BODY_BUDGET
    kept: list[str] = []
    cumulative = 0
    for line in lines:
        line_bytes = len((line + "\n").encode("utf-8"))
        if cumulative + line_bytes > budget:
            break
        cumulative += line_bytes
        kept.append(line)

    if not kept:
        # First line overflow fallback
        return _TRUNCATION_LINE

    kept.append(_TRUNCATION_LINE)
    return "\n".join(kept)


def compact_json_envelope(
    rows: Sequence[Mapping[str, object]],
    columns: Sequence[Column],
    *,
    total: int,
    limit: int,
) -> dict:
    """Build the canonical JSON listing envelope.

    Returns {rows, total, displayed, truncated, limit} where each row
    is a dict keyed by the column row_keys.
    """
    keys = [key for _, key, _ in columns]
    row_dicts = []
    for row in rows:
        row_dicts.append({k: row.get(k) for k in keys})
    displayed = len(row_dicts)
    return {
        "rows": row_dicts,
        "total": total,
        "displayed": displayed,
        "truncated": displayed < total,
        "limit": limit,
    }
