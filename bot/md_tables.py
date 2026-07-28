"""Lightweight markdown-table repair (stdlib only, unit-testable).

Analyst LLMs occasionally emit a GFM table without the mandatory header-
separator row ('|---|---|'), so the whole block renders as raw '|...|' text
(or flattens to bullets / a blob in Telegram). 티로보틱스 117730.KS
2026-06-04 raw:

    요약표
    | 지표 | 현재 값 | 비고 |
    | 평균 | ₩19,556 | ₩11,280 (데이터 불일치) |

— header followed directly by a data row, no '|---|' separator → broken.
`insert_table_separators` inserts the missing separator so both the dashboard
(markdown→HTML) and Telegram render the table. Idempotent + never raises.
Universal — every analysis output.
"""
from __future__ import annotations

import re

# A separator row: every cell is dashes (optionally :--: alignment).
_SEP_ROW_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")


def _is_pipe_row(line: str) -> bool:
    """A GFM table row: starts and ends with '|' and has ≥2 pipes (≥1 cell)."""
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def insert_table_separators(text: str) -> str:
    """Insert a '|---|...|' separator after a table header row that is
    immediately followed by a data row but lacks the separator.

    Only the FIRST row of a pipe-delimited block (the header) gets a
    separator; valid tables (header already followed by a separator) are
    left untouched (idempotent). Column count is taken from the header.
    Never raises — returns the input on any error."""
    try:
        lines = text.split("\n")
    except Exception:
        return text
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if (_is_pipe_row(line) and not _SEP_ROW_RE.match(line)
                and i + 1 < n and _is_pipe_row(lines[i + 1])
                and not _SEP_ROW_RE.match(lines[i + 1])
                and (not out or not _is_pipe_row(out[-1]))):
            ncol = line.strip().count("|") - 1
            if ncol >= 1:
                out.append(line)
                out.append("|" + "|".join([" --- "] * ncol) + "|")
                i += 1
                continue
        out.append(line)
        i += 1
    return "\n".join(out)
