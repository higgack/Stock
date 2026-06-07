"""Collect a company info snapshot from yfinance for archive storage.

Called by ``archive.save_analysis`` — one extra yfinance ``.info`` call
per analysis (~0.5s, non-fatal). The snapshot powers the detail-page
header cards, company info section, and consensus card. All fields are
optional — missing data renders gracefully as "—".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def collect_stock_snapshot(ticker: str) -> dict | None:
    """Return a dict of company/market facts, or *None* on failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        if not info or info.get("quoteType") is None:
            return None

        def _g(k, default=None):
            v = info.get(k)
            return v if v is not None else default

        snap: dict = {}

        # ── identity ────────────────────────────────────────────────
        snap["long_name"] = _g("longName") or _g("shortName") or ""
        snap["sector"] = _g("sector", "")
        snap["industry"] = _g("industry", "")
        snap["exchange"] = _g("exchange", "")
        snap["quote_type"] = _g("quoteType", "")
        snap["currency"] = _g("currency", "")
        snap["financial_currency"] = _g("financialCurrency", "")
        snap["country"] = _g("country", "")
        snap["city"] = _g("city", "")
        snap["state"] = _g("state", "")
        snap["website"] = _g("website", "")
        snap["description"] = _g("longBusinessSummary", "")
        snap["employees"] = _g("fullTimeEmployees")

        # fiscal year end — derive from lastFiscalYearEnd epoch
        fy_epoch = _g("lastFiscalYearEnd")
        if fy_epoch and isinstance(fy_epoch, (int, float)):
            try:
                dt = datetime.fromtimestamp(fy_epoch, tz=timezone.utc)
                snap["fiscal_year_end"] = f"{dt.month:02d}-{dt.day:02d}"
            except Exception:
                pass

        # listing date — from firstTradeDateEpochUtc
        ftd = _g("firstTradeDateEpochUtc")
        if ftd and isinstance(ftd, (int, float)):
            try:
                dt = datetime.fromtimestamp(ftd, tz=timezone.utc)
                snap["first_trade_date"] = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        # ── market data ─────────────────────────────────────────────
        price = _g("currentPrice") or _g("regularMarketPrice")
        if price:
            snap["current_price"] = price
        snap["market_cap"] = _g("marketCap")
        snap["shares_outstanding"] = _g("sharesOutstanding")

        # ── valuation multiples ─────────────────────────────────────
        for k in ("trailingPE", "forwardPE", "priceToBook",
                  "priceToSalesTrailing12Months", "enterpriseToEbitda",
                  "trailingEps", "forwardEps", "bookValue",
                  "dividendYield", "beta",
                  "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
                  "fiftyDayAverage", "twoHundredDayAverage"):
            v = _g(k)
            if v is not None:
                snap[k] = v

        # ── consensus ───────────────────────────────────────────────
        snap["target_mean"] = _g("targetMeanPrice")
        snap["target_high"] = _g("targetHighPrice")
        snap["target_low"] = _g("targetLowPrice")
        snap["recommendation_key"] = _g("recommendationKey", "")
        snap["recommendation_mean"] = _g("recommendationMean")
        snap["num_analysts"] = _g("numberOfAnalystOpinions")

        # ── holdings ────────────────────────────────────────────────
        snap["held_pct_insiders"] = _g("heldPercentInsiders")
        snap["held_pct_institutions"] = _g("heldPercentInstitutions")
        snap["shares_short"] = _g("sharesShort")
        snap["short_ratio"] = _g("shortRatio")

        # ── next earnings ───────────────────────────────────────────
        for ek in ("earningsTimestampStart", "earningsTimestampEnd",
                   "earningsTimestamp"):
            ev = _g(ek)
            if ev and isinstance(ev, (int, float)) and ev > 0:
                try:
                    dt = datetime.fromtimestamp(ev, tz=timezone.utc)
                    if dt > datetime.now(tz=timezone.utc):
                        snap["next_earnings"] = dt.strftime("%Y-%m-%d")
                        snap["next_earnings_key"] = ek
                        break
                except Exception:
                    pass

        # ── earnings history (compact — last 8 quarters) ────────────
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                rows = []
                for idx, row in ed.head(8).iterrows():
                    entry: dict = {}
                    if hasattr(idx, "strftime"):
                        entry["date"] = idx.strftime("%Y-%m-%d")
                    else:
                        entry["date"] = str(idx)[:10]
                    for col in ed.columns:
                        v = row.get(col)
                        if v is not None and str(v) != "nan":
                            entry[col] = round(float(v), 4) if isinstance(v, float) else v
                    rows.append(entry)
                if rows:
                    snap["earnings_history"] = rows
        except Exception:
            pass

        # ── analyst upgrades/downgrades (last 15) ───────────────────
        try:
            ud = t.upgrades_downgrades
            if ud is not None and not ud.empty:
                rows = []
                for idx, row in ud.head(15).iterrows():
                    entry: dict = {}
                    if hasattr(idx, "strftime"):
                        entry["date"] = idx.strftime("%Y-%m-%d")
                    else:
                        entry["date"] = str(idx)[:10]
                    for col in ud.columns:
                        v = row.get(col)
                        if v is not None and str(v) != "nan":
                            entry[col] = str(v) if not isinstance(v, (int, float)) else v
                    rows.append(entry)
                if rows:
                    snap["upgrades_downgrades"] = rows
        except Exception:
            pass

        # ── institutional holders (top 10) ──────────────────────────
        try:
            ih = t.institutional_holders
            if ih is not None and not ih.empty:
                rows = []
                for _, row in ih.head(10).iterrows():
                    entry: dict = {}
                    for col in ih.columns:
                        v = row.get(col)
                        if v is None or str(v) == "nan":
                            continue
                        if hasattr(v, "strftime"):
                            entry[col] = v.strftime("%Y-%m-%d")
                        elif isinstance(v, float):
                            entry[col] = round(v, 6) if abs(v) < 1 else round(v, 2)
                        else:
                            entry[col] = str(v) if not isinstance(v, (int, float)) else v
                    rows.append(entry)
                if rows:
                    snap["institutional_holders"] = rows
        except Exception:
            pass

        # ── news (last 10) ──────────────────────────────────────────
        try:
            news = t.news
            if news:
                rows = []
                for item in news[:10]:
                    entry = {}
                    for k in ("title", "publisher", "link",
                              "providerPublishTime"):
                        v = item.get(k)
                        if v is not None:
                            if k == "providerPublishTime" and isinstance(v, (int, float)):
                                entry["date"] = datetime.fromtimestamp(
                                    v, tz=timezone.utc
                                ).strftime("%Y-%m-%d")
                            else:
                                entry[k] = v
                    if entry.get("title"):
                        rows.append(entry)
                if rows:
                    snap["news"] = rows
        except Exception:
            pass

        # strip None values to keep JSON compact
        return {k: v for k, v in snap.items() if v is not None}

    except Exception as exc:
        log.warning("stock_snapshot: failed for %s: %s", ticker, exc)
        return None
