"""Long-term per-analysis archive on disk.

Each successful analysis writes one JSON file to
``~/.tradingagents/archive/YYYY-MM-DD/{TICKER}.json`` containing the
full markdown summary, full report, and minimal metadata. No rotation
— this is the user's permanent record of what the bot has produced.

Phase 1 of the dashboard rollout. The static HTML generator and web
server (Phases 2 / 3) read from this directory; Phase 4 search and
Phase 5 stats also key off these files. Schema is versioned so future
changes can migrate cleanly without breaking older entries.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

ARCHIVE_ROOT = Path.home() / ".tradingagents" / "archive"
# v2 (2026-06-03): added optional "price_chart" field — compact close +
# 10 EMA / 50 SMA / 200 SMA series for the detail-page lightweight-charts
# render. Older v1 entries lack it and render text-only (graceful).
SCHEMA_VERSION = 2
_KST = timezone(timedelta(hours=9))


def save_analysis(
    ticker: str,
    trade_date: str,
    summary: str,
    full_report: str,
    elapsed_sec: float,
) -> None:
    """Persist one analysis to the archive.

    Idempotent: re-running the same (ticker, trade_date) overwrites the
    previous JSON. Failure is non-fatal — archive errors must never
    break the analysis pipeline, since the analysis result has already
    reached the user via Telegram by the time we get here.
    """
    try:
        day_dir = ARCHIVE_ROOT / trade_date
        day_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": SCHEMA_VERSION,
            "ticker": ticker,
            "trade_date": trade_date,
            "analyzed_at": datetime.now(_KST).isoformat(timespec="seconds"),
            "elapsed_sec": round(elapsed_sec, 2),
            "summary": summary,
            "full_report": full_report,
        }
        # Price-chart payload (close + 10 EMA / 50 SMA / 200 SMA) for the
        # detail-page chart. Non-fatal: a fetch failure just omits the
        # field and the page renders text-only. Computed here (one extra
        # yfinance call) since the graph run's series isn't threaded back.
        try:
            from bot.chart_data import build_price_chart
            chart = build_price_chart(ticker)
            if chart:
                record["price_chart"] = chart
        except Exception as exc:
            log.warning("archive: price_chart build skipped for %s: %s", ticker, exc)
        path = day_dir / f"{ticker}.json"
        # Use a tmp+rename so a partial write can't corrupt an existing
        # archive entry (e.g. concurrent reads from the dashboard).
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
        log.info(
            "archive: saved %s/%s (full=%d chars, summary=%d chars)",
            trade_date, ticker, len(full_report), len(summary),
        )
    except Exception as exc:
        log.warning(
            "archive: save failed for %s/%s: %s", ticker, trade_date, exc,
        )


def backfill_price_charts(limit: int | None = None) -> int:
    """One-time backfill: add the `price_chart` field to older archive
    entries (schema v1) that predate the chart feature. Fetches each
    ticker's price series AS OF its analysis date (no look-ahead — the
    moving averages match that day's TECHNICAL SNAPSHOT). Idempotent:
    skips entries that already have a chart. Returns the count filled.

    Triggered once per install via a marker file (see telegram_bot
    startup). Free — yfinance only, no LLM / paid API.
    """
    filled = 0
    try:
        from bot.chart_data import build_price_chart
    except Exception as exc:
        log.warning("archive: backfill import failed: %s", exc)
        return 0
    if not ARCHIVE_ROOT.exists():
        return 0
    for day_dir in sorted(ARCHIVE_ROOT.iterdir()):
        if not day_dir.is_dir():
            continue
        for jf in sorted(day_dir.glob("*.json")):
            try:
                rec = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue
            if rec.get("price_chart"):
                continue
            ticker = rec.get("ticker")
            date = rec.get("trade_date")
            if not ticker or not date:
                continue
            chart = build_price_chart(ticker, as_of=date)
            if not chart:
                continue
            rec["price_chart"] = chart
            rec["schema_version"] = SCHEMA_VERSION
            try:
                tmp = jf.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(rec, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(jf)
                filled += 1
                log.info("archive: backfilled chart for %s/%s", date, ticker)
            except Exception as exc:
                log.warning(
                    "archive: backfill write failed for %s/%s: %s",
                    date, ticker, exc,
                )
                continue
            if limit and filled >= limit:
                return filled
    return filled
