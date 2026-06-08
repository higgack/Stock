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
# v3 (2026-06-07): added optional "stock_info" field — company snapshot
# (identity / market data / consensus / earnings / holders / news) from
# yfinance .info + .earnings_dates + .upgrades_downgrades +
# .institutional_holders + .news. Powers the detail-page header cards,
# company info section, consensus card, earnings table, etc.
SCHEMA_VERSION = 3
_KST = timezone(timedelta(hours=9))


def save_analysis(
    ticker: str,
    trade_date: str,
    summary: str,
    full_report: str,
    elapsed_sec: float,
    started_at: float | None = None,
) -> None:
    """Persist one analysis to the archive.

    Idempotent: re-running the same (ticker, trade_date) overwrites the
    previous JSON. Failure is non-fatal — archive errors must never
    break the analysis pipeline, since the analysis result has already
    reached the user via Telegram by the time we get here.

    ``started_at`` (epoch) lets us stamp the per-analysis Gemini cost into
    the record (``cost_krw``) by summing the usage log over the run window.
    The sum runs AFTER the price-chart build so this run's chart
    disclosure-title translation (CN/JP/TW → KR, subsystem='chart_translate')
    is included in the analysis's cost rather than left as an invisible
    background charge. Optional / additive — older records simply lack the
    field and the detail page omits the cost line.
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
        # Also runs the chart's disclosure-title translation, so it must
        # happen BEFORE the cost stamp below.
        try:
            from bot.chart_data import build_price_chart
            chart = build_price_chart(ticker)
            if chart:
                record["price_chart"] = chart
        except Exception as exc:
            log.warning("archive: price_chart build skipped for %s: %s", ticker, exc)
        # Company snapshot — identity / market / consensus / earnings /
        # holders / news from yfinance. Non-fatal: older records or
        # fetch failures just skip the field (detail page degrades).
        try:
            from bot.stock_snapshot import collect_stock_snapshot
            snap = collect_stock_snapshot(ticker)
            if snap:
                record["stock_info"] = snap
        except Exception as exc:
            log.warning("archive: stock_snapshot skipped for %s: %s", ticker, exc)
        # Per-analysis cost stamp — summed from the usage log over the run
        # window so the detail page can show each analysis's spend inline.
        if started_at:
            try:
                from bot.usage_tracker import sum_analysis_cost_krw
                cost_krw = sum_analysis_cost_krw(started_at)
                if cost_krw > 0:
                    record["cost_krw"] = cost_krw
            except Exception as exc:
                log.warning("archive: cost stamp skipped for %s: %s", ticker, exc)
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


def backfill_stock_info(limit: int | None = None) -> int:
    """One-time backfill: add the ``stock_info`` field to older archive
    entries that predate the company-snapshot feature. Fetches each
    ticker's CURRENT snapshot (not as-of — most APIs don't support
    historical queries). Idempotent: skips entries that already have
    stock_info. Returns the count filled.

    Triggered once per install via a marker file (see telegram_bot
    startup). Free — yfinance + public APIs only, no LLM.
    """
    filled = 0
    try:
        from bot.stock_snapshot import collect_stock_snapshot
    except Exception as exc:
        log.warning("archive: stock_info backfill import failed: %s", exc)
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
            if rec.get("stock_info"):
                continue
            ticker = rec.get("ticker")
            if not ticker:
                continue
            try:
                snap = collect_stock_snapshot(ticker)
            except Exception as exc:
                log.debug("archive: stock_info backfill skipped %s: %s", ticker, exc)
                continue
            if not snap:
                continue
            rec["stock_info"] = snap
            rec["schema_version"] = SCHEMA_VERSION
            try:
                tmp = jf.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(rec, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(jf)
                filled += 1
                log.info("archive: backfilled stock_info for %s/%s",
                         rec.get("trade_date", "?"), ticker)
            except Exception as exc:
                log.warning(
                    "archive: stock_info backfill write failed for %s: %s",
                    ticker, exc,
                )
                continue
            if limit and filled >= limit:
                return filled
    return filled


def backfill_detail_tabs(limit: int | None = None) -> int:
    """One-time backfill: enrich existing stock_info snapshots with
    news fallback, KR research reports, and TW/JP consensus that were
    added after the original snapshot was collected.

    Additive only — never overwrites existing data.  Each record is
    independently try/excepted.  Rate-limited (1s between records that
    actually change) to avoid API hammering.

    Free: public scraping only, no LLM.  Triggered once per install
    via a marker file (see telegram_bot startup).
    """
    import time as _time

    filled = 0
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
            si = rec.get("stock_info")
            if not si:
                continue
            ticker = rec.get("ticker", "")
            if not ticker:
                continue

            changed = False

            # ① News fallback (all markets)
            if not si.get("news"):
                try:
                    from bot.stock_snapshot import _collect_news_fallback
                    _collect_news_fallback(ticker, si)
                    if si.get("news"):
                        changed = True
                except Exception as exc:
                    log.debug("backfill_detail_tabs: news %s: %s", ticker, exc)

            # ② KR research reports (Naver Finance primary → 한경 fallback)
            if ticker.upper().endswith((".KS", ".KQ")):
                kr = si.get("kr", {})
                if not kr.get("research_reports"):
                    _kr_res = None
                    try:
                        from bot.naver_research_client import fetch_research
                        _kr_res = fetch_research(ticker)
                    except Exception:
                        pass
                    _naver_hollow = (_kr_res and _kr_res.get("reports")
                                     and not any(r.get("target") or r.get("rating")
                                                 for r in _kr_res["reports"]))
                    if not (_kr_res and _kr_res.get("reports")) or _naver_hollow:
                        try:
                            from bot.hk_consensus_client import fetch_consensus as hk_fetch
                            _hk = hk_fetch(ticker)
                        except Exception:
                            _hk = None
                        if _hk and _hk.get("reports"):
                            if _naver_hollow:
                                _hk_map = {(r.get("date"), r.get("broker")): r
                                           for r in _hk["reports"]}
                                for r in _kr_res["reports"]:
                                    match = _hk_map.get((r.get("date"), r.get("broker")))
                                    if match:
                                        if not r.get("target") and match.get("target"):
                                            r["target"] = match["target"]
                                        if not r.get("rating") and match.get("rating"):
                                            r["rating"] = match["rating"]
                                if not any(r.get("target") or r.get("rating")
                                           for r in _kr_res["reports"]):
                                    _kr_res = _hk
                            else:
                                _kr_res = _hk
                    if _kr_res and _kr_res.get("reports"):
                        si.setdefault("kr", {})["research_reports"] = _kr_res["reports"]
                        changed = True
                        if not si.get("target_mean") and not kr.get("consensus"):
                            if _kr_res.get("target_price"):
                                si.setdefault("kr", {})["consensus"] = {
                                    "source": "Naver Finance",
                                    "target_mean": _kr_res["target_price"],
                                    "rating": _kr_res.get("rating"),
                                    "n_analysts": _kr_res.get("analyst_count"),
                                    "last_report_date": _kr_res.get("last_report_date"),
                                }

            # ③ TW consensus (鉅亨網)
            if ticker.upper().endswith(".TW"):
                tw = si.get("tw", {})
                if not si.get("target_mean") and not tw.get("consensus"):
                    try:
                        from bot.cnyes_consensus import fetch_consensus as cnyes_fetch
                        cn = cnyes_fetch(ticker)
                        if cn and cn.get("target_mean"):
                            si.setdefault("tw", {})["consensus"] = {
                                "source": "鉅亨網",
                                "target_mean": cn["target_mean"],
                                "rating": cn.get("rating"),
                                "n_analysts": cn.get("n_analysts"),
                                "last_report_date": cn.get("last_report_date"),
                            }
                            changed = True
                    except Exception as exc:
                        log.debug("backfill_detail_tabs: TW consensus %s: %s", ticker, exc)

            # ④ JP consensus (Kabutan)
            if ticker.upper().endswith(".T"):
                jp = si.get("jp", {})
                if not si.get("target_mean") and not jp.get("consensus"):
                    try:
                        from bot.kabutan_consensus import fetch_consensus as kb_fetch
                        kb = kb_fetch(ticker)
                        if kb and kb.get("target_mean"):
                            si.setdefault("jp", {})["consensus"] = {
                                "source": "Kabutan",
                                "target_mean": kb["target_mean"],
                                "rating": kb.get("rating"),
                                "n_analysts": kb.get("n_analysts"),
                                "last_report_date": kb.get("last_report_date"),
                            }
                            changed = True
                    except Exception as exc:
                        log.debug("backfill_detail_tabs: JP consensus %s: %s", ticker, exc)

            if not changed:
                continue

            try:
                tmp = jf.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(rec, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(jf)
                filled += 1
                log.info("archive: backfilled detail tabs for %s/%s",
                         rec.get("trade_date", "?"), ticker)
            except Exception as exc:
                log.warning("archive: detail tabs write failed for %s: %s",
                            ticker, exc)
                continue

            _time.sleep(1)

            if limit and filled >= limit:
                return filled
    return filled
