"""Daily on-disk cache for analyze() results.

Same (ticker, date) pair within the day reuses the previous report and
incurs zero Gemini cost. Cache files live in ~/.tradingagents/daily_cache/
and are plain JSON keyed by uppercased ticker + ISO date.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("stock-bot.cache")

_CACHE_DIR = Path.home() / ".tradingagents" / "daily_cache"


def _path(ticker: str, date_: str) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}_{date_}.json"


def get(ticker: str, date_: str) -> tuple[str, str] | None:
    p = _path(ticker, date_)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data["summary"], data["full"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        log.warning("cache read failed for %s/%s: %s", ticker, date_, exc)
        return None


def put(ticker: str, date_: str, summary: str, full: str) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _path(ticker, date_)
        p.write_text(
            json.dumps({"summary": summary, "full": full}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("cache write failed for %s/%s: %s", ticker, date_, exc)
