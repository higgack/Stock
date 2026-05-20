"""Daily flush of Standard View news-brief cache.

Runs at 00:05 KST via sv-cache-rollover.timer. The cache hit bug:
new date crosses midnight, news-brief Gemini call hasn't aggregated
that day's articles yet, returns a stub, gets cached, and every
subsequent call reads the stub forever (until a TTL fires that may
not exist). Flushing at 00:05 forces the first real call of the day
to regenerate from scratch.

Cheap (single DELETE on a small table) — runs even on idle systems."""

from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / "standardview" / "data" / "standard_view.db"

if not DB_PATH.exists():
    print(f"sv-cache-rollover: DB not found at {DB_PATH} — skip", file=sys.stderr)
    sys.exit(0)

try:
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='macro_news_cache'")
    if not cur.fetchone():
        print("sv-cache-rollover: macro_news_cache table missing — skip")
        sys.exit(0)
    cur.execute("SELECT COUNT(*) FROM macro_news_cache")
    before = cur.fetchone()[0]
    cur.execute("DELETE FROM macro_news_cache")
    con.commit()
    print(f"sv-cache-rollover: deleted {before} rows from macro_news_cache")
finally:
    try:
        con.close()
    except Exception:
        pass
