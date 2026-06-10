"""Per-user watchlist for trade-bot DM notifications.

The trade-bot picks up new BeOn forwards as channel posts; this module
adds a side-channel so a private-chat user can subscribe to specific
companies or items and receive a DM whenever a new alert matches.

State lives in a tiny SQLite table (~/.trade/watchlist.db) keyed by
Telegram user_id; the trade-bot writes it via /watch and /unwatch
slash commands and reads it on every new alert to find which DMs to
push.

Schema:
  CREATE TABLE watches (
      user_id     INTEGER NOT NULL,
      kind        TEXT    NOT NULL CHECK (kind IN ('item','company')),
      pattern     TEXT    NOT NULL,    -- case-insensitive substring
      added_at    TEXT    NOT NULL,
      PRIMARY KEY (user_id, kind, pattern)
  );

We use substring match (LIKE %pattern%) rather than exact equality so
'라면' matches '라면 + 기타 소스 및 조제품 (전국)' too.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
DEFAULT_PATH = _DATA_DIR / "watchlist.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('item','company')),
  pattern TEXT NOT NULL,
  added_at TEXT NOT NULL,
  PRIMARY KEY (user_id, kind, pattern)
);
CREATE INDEX IF NOT EXISTS idx_user ON watches(user_id);
"""


def open_db(path: Path | str = DEFAULT_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def session(path: Path | str = DEFAULT_PATH) -> Iterator[sqlite3.Connection]:
    conn = open_db(path)
    try:
        yield conn
    finally:
        conn.close()


def add(conn: sqlite3.Connection, user_id: int, kind: str, pattern: str) -> bool:
    """Add a watch. Returns True if newly inserted, False if it was a
    duplicate (idempotent /watch is friendly UX)."""
    pat = pattern.strip()
    if not pat:
        return False
    if kind not in ("item", "company"):
        raise ValueError(f"unknown watch kind: {kind!r}")
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT OR IGNORE INTO watches(user_id, kind, pattern, added_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, kind, pat, now),
    )
    return cur.rowcount > 0


def remove(conn: sqlite3.Connection, user_id: int, kind: str, pattern: str) -> bool:
    """Returns True if a watch was removed."""
    cur = conn.execute(
        "DELETE FROM watches WHERE user_id = ? AND kind = ? AND pattern = ?",
        (user_id, kind, pattern.strip()),
    )
    return cur.rowcount > 0


def list_for(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT kind, pattern, added_at FROM watches "
        "WHERE user_id = ? ORDER BY kind, pattern",
        (user_id,),
    )]


def matching_users(
    conn: sqlite3.Connection,
    item: str,
    stocks: list[str],
) -> set[int]:
    """Return the set of user_ids whose any watch pattern is a
    case-insensitive substring of the given item OR of any stock name.

    Substring matching catches '라면' watches firing on
    '라면 + 기타 소스 및 조제품 (전국)' style composite item names
    without requiring the user to spell out variants.
    """
    item_lower = (item or "").lower()
    stocks_lower = [(s or "").lower() for s in (stocks or [])]
    hits: set[int] = set()
    for row in conn.execute("SELECT user_id, kind, pattern FROM watches"):
        pat = (row["pattern"] or "").lower()
        if not pat:
            continue
        if row["kind"] == "item":
            if pat in item_lower:
                hits.add(row["user_id"])
        elif row["kind"] == "company":
            if any(pat in s for s in stocks_lower):
                hits.add(row["user_id"])
    return hits
