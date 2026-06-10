"""SQLite-backed store for parsed BeOn alerts.

One table, one schema, indices tuned for the two dashboard views
(latest-per-dedup-key for 품목별, stocks-membership for 회사별). JSON
columns hold list/dict fields so we don't need a relational sub-schema
for stocks/countries — a single row per alert is the working unit.

Concurrency: WAL + busy_timeout so the live trade-bot's upserts and the
dashboard renderer's reads can overlap without explicit locking.
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from trade.parser import ParsedAlert


SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,

  -- ingest provenance
  source_chat_id INTEGER NOT NULL,
  source_message_id INTEGER NOT NULL,
  media_group_id TEXT,
  ingested_at TEXT NOT NULL,
  posted_at TEXT NOT NULL,
  raw_text TEXT NOT NULL,

  -- parser output
  direction TEXT NOT NULL,
  status TEXT NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  period_kind TEXT NOT NULL,
  title_kind TEXT NOT NULL,
  item TEXT NOT NULL,
  item_raw TEXT NOT NULL,
  is_composite INTEGER NOT NULL DEFAULT 0,
  composite_parts TEXT NOT NULL DEFAULT '[]',
  region TEXT,
  country TEXT,
  regions TEXT NOT NULL DEFAULT '[]',
  countries TEXT NOT NULL DEFAULT '[]',
  stocks TEXT NOT NULL DEFAULT '[]',
  stocks_meta TEXT NOT NULL DEFAULT '{}',
  has_etc INTEGER NOT NULL DEFAULT 0,
  commentary TEXT,
  parse_warnings TEXT NOT NULL DEFAULT '[]',

  -- dashboard-driven derived fields
  dedup_key TEXT NOT NULL,
  media_paths TEXT NOT NULL DEFAULT '[]',

  superseded_by INTEGER REFERENCES alerts(id),

  UNIQUE (source_chat_id, source_message_id)
);

CREATE INDEX IF NOT EXISTS idx_dedup_latest
  ON alerts (dedup_key, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_item_recent
  ON alerts (item, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_period
  ON alerts (period_start, period_end);

CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open (and migrate on first use) the alerts database.

    WAL gives us concurrent read-while-writing; a 5 s busy_timeout
    absorbs the short contention windows when the live bot upserts
    while the dashboard render is reading.
    """
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta(key, value) VALUES('version', ?)",
        (str(SCHEMA_VERSION),),
    )
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction wrapper since we opened in autocommit."""
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# ---------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------

def _jdump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def alert_to_row(
    parsed: ParsedAlert,
    *,
    source_chat_id: int,
    source_message_id: int,
    media_group_id: str | None,
    ingested_at: str,
    posted_at: str,
    raw_text: str,
    media_paths: list[str] | None = None,
) -> dict:
    """ParsedAlert + ingest provenance → row dict ready for upsert.

    Lists/dicts get JSON-encoded; dates get ISO-encoded; the dedup_key
    is computed once at write time so the index can use it directly.
    """
    media_paths = media_paths or []
    return {
        "source_chat_id": source_chat_id,
        "source_message_id": source_message_id,
        "media_group_id": media_group_id,
        "ingested_at": ingested_at,
        "posted_at": posted_at,
        "raw_text": raw_text,
        "direction": parsed.direction,
        "status": parsed.status,
        "period_start": parsed.period_start.isoformat(),
        "period_end": parsed.period_end.isoformat(),
        "period_kind": parsed.period_kind,
        "title_kind": parsed.title_kind,
        "item": parsed.item,
        "item_raw": parsed.item_raw,
        "is_composite": 1 if parsed.is_composite else 0,
        "composite_parts": _jdump(parsed.composite_parts),
        "region": parsed.region,
        "country": parsed.country,
        "regions": _jdump(parsed.regions),
        "countries": _jdump(parsed.countries),
        "stocks": _jdump(parsed.stocks),
        "stocks_meta": _jdump(parsed.stocks_meta),
        "has_etc": 1 if parsed.has_etc else 0,
        "commentary": parsed.commentary,
        "parse_warnings": _jdump(parsed.parse_warnings),
        "dedup_key": parsed.dedup_key(),
        "media_paths": _jdump(media_paths),
    }


def row_to_dict(row: sqlite3.Row) -> dict:
    """sqlite3.Row → plain dict, decoding JSON columns back to objects."""
    d = dict(row)
    for k in (
        "composite_parts", "regions", "countries", "stocks", "stocks_meta",
        "parse_warnings", "media_paths",
    ):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    d["is_composite"] = bool(d.get("is_composite"))
    d["has_etc"] = bool(d.get("has_etc"))
    return d


# ---------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------

_INSERT_SQL = """
INSERT INTO alerts (
  source_chat_id, source_message_id, media_group_id,
  ingested_at, posted_at, raw_text,
  direction, status, period_start, period_end, period_kind,
  title_kind, item, item_raw, is_composite, composite_parts,
  region, country, regions, countries,
  stocks, stocks_meta, has_etc, commentary, parse_warnings,
  dedup_key, media_paths
) VALUES (
  :source_chat_id, :source_message_id, :media_group_id,
  :ingested_at, :posted_at, :raw_text,
  :direction, :status, :period_start, :period_end, :period_kind,
  :title_kind, :item, :item_raw, :is_composite, :composite_parts,
  :region, :country, :regions, :countries,
  :stocks, :stocks_meta, :has_etc, :commentary, :parse_warnings,
  :dedup_key, :media_paths
)
ON CONFLICT(source_chat_id, source_message_id) DO NOTHING
"""


def upsert_alert(conn: sqlite3.Connection, row: dict) -> bool:
    """Insert a parsed alert. Returns True if a new row was created,
    False if the (source_chat_id, source_message_id) was already
    present (idempotent rerun).
    """
    cur = conn.execute(_INSERT_SQL, row)
    return cur.rowcount > 0


def update_media_paths(
    conn: sqlite3.Connection,
    source_chat_id: int,
    source_message_id: int,
    media_paths: list[str],
) -> None:
    """Replace the media_paths array on an existing alert.

    Used when sibling photo-only messages of an album arrive after
    the captioned message has already been upserted.
    """
    conn.execute(
        "UPDATE alerts SET media_paths = ? "
        "WHERE source_chat_id = ? AND source_message_id = ?",
        (_jdump(media_paths), source_chat_id, source_message_id),
    )


# ---------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------

def count_alerts(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM alerts")
    return cur.fetchone()[0]


def list_all_alerts(conn: sqlite3.Connection) -> list[dict]:
    """Every alert, grouped by dedup_key with each group's latest first.

    Tie-break inside a group: newest posted_at; 'final' wins against
    'preliminary' at identical posted_at (matches latest_per_dedup_key
    semantics so the first row of each dedup_key block is the same
    'latest' that view rendering uses).

    Powers the dashboard's modal-history feature: render_html walks
    the result once, splits the first row of each dedup_key group as
    'latest' (used for the views) and the remainder as 'history'
    (rendered inline in the modal so the operator can eyeball prior
    잠정/확정 reports next to the current one without OCR).
    """
    return [
        row_to_dict(r)
        for r in conn.execute(
            "SELECT * FROM alerts "
            "ORDER BY dedup_key, "
            "posted_at DESC, "
            "CASE WHEN status='final' THEN 0 ELSE 1 END"
        )
    ]


def latest_per_dedup_key(conn: sqlite3.Connection) -> list[dict]:
    """One row per (direction, item, region, country) — the most
    recently published, with 'final' winning ties against 'preliminary'
    when posted_at is identical. Drives the 품목별 view.
    """
    sql = """
    SELECT a.* FROM alerts a
    WHERE NOT EXISTS (
      SELECT 1 FROM alerts b
      WHERE b.dedup_key = a.dedup_key
        AND (
          b.posted_at > a.posted_at
          OR (
            b.posted_at = a.posted_at
            AND b.status = 'final' AND a.status = 'preliminary'
          )
        )
    )
    ORDER BY a.posted_at DESC
    """
    return [row_to_dict(r) for r in conn.execute(sql)]


def query_by_stock(conn: sqlite3.Connection, stock_name: str) -> list[dict]:
    """All alerts that mention the given stock anywhere in stocks JSON.

    SQLite JSON1 extension is part of the standard build since 3.38, so
    json_each is safe to use without checking. Drives the 회사별 drill-in.
    """
    sql = """
    SELECT a.* FROM alerts a, json_each(a.stocks) j
    WHERE j.value = ?
    ORDER BY a.posted_at DESC
    """
    return [row_to_dict(r) for r in conn.execute(sql, (stock_name,))]


def query_by_item(conn: sqlite3.Connection, item: str) -> list[dict]:
    sql = "SELECT * FROM alerts WHERE item = ? ORDER BY posted_at DESC"
    return [row_to_dict(r) for r in conn.execute(sql, (item,))]


def stats(conn: sqlite3.Connection) -> dict:
    """Aggregate counts useful for the ingest report. The dashboard
    footer can show a subset of these as a freshness signal.
    """
    out: dict = {}
    out["total"] = count_alerts(conn)
    out["by_direction"] = {
        r["direction"]: r["c"]
        for r in conn.execute(
            "SELECT direction, COUNT(*) AS c FROM alerts GROUP BY direction"
        )
    }
    out["by_status"] = {
        r["status"]: r["c"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS c FROM alerts GROUP BY status"
        )
    }
    out["by_period_kind"] = {
        r["period_kind"]: r["c"]
        for r in conn.execute(
            "SELECT period_kind, COUNT(*) AS c FROM alerts GROUP BY period_kind"
        )
    }
    out["by_title_kind"] = {
        r["title_kind"]: r["c"]
        for r in conn.execute(
            "SELECT title_kind, COUNT(*) AS c FROM alerts GROUP BY title_kind"
        )
    }
    out["with_warnings"] = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE parse_warnings != '[]'"
    ).fetchone()[0]
    out["distinct_items"] = conn.execute(
        "SELECT COUNT(DISTINCT item) FROM alerts"
    ).fetchone()[0]
    out["distinct_dedup_keys"] = conn.execute(
        "SELECT COUNT(DISTINCT dedup_key) FROM alerts"
    ).fetchone()[0]
    return out
