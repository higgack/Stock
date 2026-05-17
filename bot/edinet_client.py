"""Thin client for EDINET (Electronic Disclosure for Investors' NETwork).

JP analogue to bot/dart_client.py. EDINET is Japan's regulatory filing
portal run by the FSA (金融庁); it carries the same class of data DART
provides for Korea — annual / quarterly reports, extraordinary filings,
5%-stake disclosures, prospectuses, etc.

Endpoints (v2 API):
  GET /api/v2/documents.json?date=YYYY-MM-DD&type=2&Subscription-Key=KEY
      → list of documents filed on that calendar day across ALL filers.
        Filter by `secCode` (5-digit: 4-digit ticker + 1-digit checksum)
        to extract one company's filings.
  GET /api/v2/documents/{docID}?Subscription-Key=KEY&type=...
      → fetch the actual document body (XBRL / PDF). Not used here; we
        only surface the filing headlines.

Auth: EDINET_API_KEY env var (free at https://disclosure2.edinet-fsa.go.jp/).
Missing key ⇒ silent graceful degradation; the rest of the JP analysis
continues without the EDINET block.

Caching strategy: per-day JSON of the full daily document list at
~/.tradingagents/cache/edinet/YYYY-MM-DD.json, TTL 12h. A 30-day lookback
needs 30 daily fetches — cache keeps the cost near zero on repeat runs
within the same trading window.

Doc-type filtering: out of ~30 EDINET doc-type codes, only a handful
move stock prices in a 5-day horizon. The default lookup surfaces:
  120/130 — 有価証券報告書 (annual report) / corrections
  140/150 — 四半期報告書 (quarterly) / corrections
  160/170 — 半期報告書 (semi-annual) / corrections
  180/190 — 臨時報告書 (extraordinary — M&A, lawsuits, mgmt changes)
  350/360 — 大量保有報告書 (5%+ shareholder disclosure)
Anything else (proxy statements, fund prospectuses, etc.) is dropped to
keep the prompt block focused.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.edinet")

_BASE = "https://api.edinet-fsa.go.jp/api/v2"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "edinet"
_CACHE_TTL_HOURS = 12
_HTTP_TIMEOUT = 15  # daily document lists can be large; small headroom over DART

# Doc type codes worth surfacing. Mapping to a Korean-language label so
# the analyst's report can render '공시' (disclosure) lines without
# leaving raw Japanese codes in the prompt.
_DOC_TYPE_LABELS = {
    "120": "유가증권보고서 (연차)",
    "130": "유가증권보고서 정정",
    "140": "분기보고서",
    "150": "분기보고서 정정",
    "160": "반기보고서",
    "170": "반기보고서 정정",
    "180": "임시보고서",
    "190": "임시보고서 정정",
    "350": "대량보유보고서 (5%+)",
    "360": "대량보유보고서 정정",
}


def _sec_code_for(ticker: str) -> Optional[str]:
    """Convert a `.T` ticker (e.g. '7203.T') into the 5-digit EDINET
    securities code (e.g. '72030'). EDINET appends a checksum digit — for
    almost every public Tokyo ticker the checksum is '0'. We try '0' first;
    callers that need full disambiguation should also try the other 9 digits.
    Returns the 5-char code or None if the input isn't a valid `.T` ticker."""
    code = (ticker or "").upper().split(".")[0]
    if not (code.isdigit() and len(code) == 4):
        return None
    return code + "0"


def _doc_type_label(doc_type_code: str) -> Optional[str]:
    return _DOC_TYPE_LABELS.get(doc_type_code)


class EdinetClient:
    """Per-call EDINET wrapper. Cheap to construct; reuse the
    process-wide instance via `get_edinet()` to amortize per-day cache hits."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or os.getenv("EDINET_API_KEY") or "").strip()

    # ---- low-level day fetch -------------------------------------------------

    def _fetch_day(self, day: date) -> list[dict]:
        """Return the raw document list for one calendar day. Returns [] on
        any failure (missing key, HTTP error, empty response)."""
        if not self.api_key:
            return []

        cache_file = _CACHE_DIR / f"{day.isoformat()}.json"
        if cache_file.exists():
            try:
                age_h = (time.time() - cache_file.stat().st_mtime) / 3600
                # Past days never change; trust the cache forever.
                # Today's file gets a 12h freshness check.
                if day < date.today() or age_h < _CACHE_TTL_HOURS:
                    return json.loads(cache_file.read_text())
            except Exception as exc:
                log.warning("edinet: cache read failed for %s: %s", day, exc)

        url = (
            f"{_BASE}/documents.json"
            f"?date={day.isoformat()}"
            f"&type=2"
            f"&Subscription-Key={self.api_key}"
        )
        try:
            resp = requests.get(url, timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            log.warning("edinet: fetch failed for %s: %s", day, exc)
            return []

        results = payload.get("results") or []
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(results, ensure_ascii=False))
        except Exception as exc:
            log.warning("edinet: cache write failed for %s: %s", day, exc)
        return results

    # ---- high-level queries --------------------------------------------------

    def get_recent_disclosures(
        self, ticker: str, days_back: int = 30, limit: int = 8
    ) -> list[dict]:
        """Return up to `limit` filings for this ticker in the last
        `days_back` calendar days, newest first.

        Each entry: {date, doc_type, doc_type_label, description, filer_name, doc_id}.
        Doc types outside `_DOC_TYPE_LABELS` are filtered out so the
        analyst sees only price-relevant filings.
        """
        sec_code = _sec_code_for(ticker)
        if not sec_code or not self.api_key:
            return []

        out: list[dict] = []
        today = date.today()
        for offset in range(days_back + 1):
            if len(out) >= limit:
                break
            day = today - timedelta(days=offset)
            docs = self._fetch_day(day)
            for d in docs:
                if d.get("secCode") != sec_code:
                    continue
                dtype = d.get("docTypeCode") or ""
                label = _doc_type_label(dtype)
                if not label:
                    continue
                out.append({
                    "date": (d.get("submitDateTime") or "")[:10] or day.isoformat(),
                    "doc_type": dtype,
                    "doc_type_label": label,
                    "description": d.get("docDescription") or "",
                    "filer_name": d.get("filerName") or "",
                    "doc_id": d.get("docID") or "",
                })
                if len(out) >= limit:
                    break
        return out

    def get_major_holders(self, ticker: str, days_back: int = 180) -> list[dict]:
        """Pull 大量保有報告書 (5%+ stake disclosures) for this ticker
        within the lookback window. Returns one entry per filing, newest
        first: {date, filer_name, description}.

        EDINET's daily-listing API doesn't directly expose the holder name
        or the % held — those live in the XBRL body. For the foundation
        layer we only surface the filings themselves (who filed, when,
        what description). A future enhancement would download each XBRL
        and parse the 'shareholdingRatio' element.
        """
        sec_code = _sec_code_for(ticker)
        if not sec_code or not self.api_key:
            return []

        out: list[dict] = []
        today = date.today()
        for offset in range(days_back + 1):
            day = today - timedelta(days=offset)
            docs = self._fetch_day(day)
            for d in docs:
                if d.get("secCode") != sec_code:
                    continue
                dtype = d.get("docTypeCode") or ""
                # Only 350 / 360 (5%-rule filings).
                if dtype not in ("350", "360"):
                    continue
                out.append({
                    "date": (d.get("submitDateTime") or "")[:10] or day.isoformat(),
                    "filer_name": d.get("filerName") or "",
                    "description": d.get("docDescription") or "",
                })
        return out

    def next_earnings_window(self, ticker: str) -> Optional[str]:
        """Infer the next quarterly / annual reporting window for a JP
        ticker. JP fiscal years overwhelmingly end on March 31 (~70% of
        TOPIX names); we hard-code that assumption and let the rare
        Dec-31 / Sep-30 names degrade silently.

        Window estimate: quarterly reports are due within 45 days of
        quarter-end (Q1 4-6 → due by Aug 14, Q2 7-9 → due by Nov 14,
        Q3 10-12 → due by Feb 14, Q4 = annual report → due by Jun 30).
        """
        sec_code = _sec_code_for(ticker)
        if not sec_code:
            return None
        today = date.today()
        # March-31 fiscal-year quarter ends
        q_ends = [
            date(today.year, 6, 30),
            date(today.year, 9, 30),
            date(today.year, 12, 31),
            date(today.year + 1, 3, 31),
        ]
        for q_end in q_ends:
            if q_end <= today:
                continue
            # Q1/Q2/Q3 → 45-day filing window. Q4 (3/31) → 90 days (annual).
            window_days = 90 if q_end.month == 3 else 45
            due_by = q_end + timedelta(days=window_days)
            return f"{q_end.isoformat()} 분기말 → ~{due_by.isoformat()} 보고 마감"
        return None

    def ticker_to_company_name(self, ticker: str, days_back: int = 90) -> Optional[str]:
        """Best-effort Japanese company name lookup by walking recent
        filings until we hit one matching this ticker's secCode. Used as
        a name source for JP tickers where yfinance's `longName` is
        English / missing. Caches implicitly via per-day disk cache.
        Returns the `filerName` from the most recent filing or None."""
        sec_code = _sec_code_for(ticker)
        if not sec_code or not self.api_key:
            return None
        today = date.today()
        for offset in range(days_back + 1):
            day = today - timedelta(days=offset)
            docs = self._fetch_day(day)
            for d in docs:
                if d.get("secCode") == sec_code:
                    name = (d.get("filerName") or "").strip()
                    if name:
                        return name
        return None


_edinet_singleton: EdinetClient | None = None


def get_edinet() -> EdinetClient:
    """Process-wide cached client. Construction is cheap but reusing
    the instance keeps any future in-memory caches (corp_code map,
    name lookup) consistent across calls."""
    global _edinet_singleton
    if _edinet_singleton is None:
        _edinet_singleton = EdinetClient()
    return _edinet_singleton


def format_edinet_jp_block(
    disclosures: list[dict],
    holders: list[dict],
    earnings_window: Optional[str],
) -> str:
    """Render the EDINET data as a prompt-ready bullet block."""
    if not disclosures and not holders and not earnings_window:
        return ""
    parts: list[str] = []

    if disclosures:
        parts.append(f"최근 공시 (EDINET, 최대 {len(disclosures)}건):")
        for d in disclosures:
            date_s = d.get("date") or "?"
            label = d.get("doc_type_label") or ""
            desc = (d.get("description") or "").strip()
            if len(desc) > 100:
                desc = desc[:97] + "..."
            parts.append(f"  • {date_s} [{label}] {desc}")

    if holders:
        parts.append(f"\n대량보유 공시 (5%+ 변동, 최근 {len(holders)}건):")
        for h in holders:
            date_s = h.get("date") or "?"
            name = (h.get("filer_name") or "").strip()
            desc = (h.get("description") or "").strip()
            if len(desc) > 80:
                desc = desc[:77] + "..."
            parts.append(f"  • {date_s} {name}: {desc}")

    if earnings_window:
        parts.append(f"\n다음 정기보고서 윈도: {earnings_window}")

    return "\n".join(parts)
