"""Thin client for DART (전자공시시스템) OpenAPI.

Provides three pieces of KR-market data that yfinance either doesn't
have or returns garbage for:

1. Recent disclosures (공시) — what the company filed in the last
   N days; surfaces guidance updates, lawsuits, M&A announcements
   etc. that move the stock but never show up in English news feeds.
2. Insider / major shareholder holdings (임원·주요주주 지분) — Form 4
   equivalent for Korea. yfinance returns 0% / N/A for these on
   KRX-listed names.
3. Next-earnings window estimate — DART doesn't publish a 'next
   earnings date' field, but quarterly reports are due within 45
   days of quarter end by law, so we infer a window from the
   current date and Q-end pattern.

Design goals:
- **Graceful degradation**: if `DART_API_KEY` is unset, the network
  is down, or DART returns an error, every method returns empty / None
  and logs a warning. The rest of the bot must keep running for non-KR
  analyses, and a KR analysis with missing DART data is better than no
  analysis at all.
- **Disk-cached corp_code mapping**: DART uses an 8-digit `corp_code`
  internal ID, not the 6-digit stock code. The mapping is downloaded
  as a zipped XML from `/api/corpCode.xml` and cached for 30 days at
  `~/.tradingagents/cache/dart_corpcode.json`. ~80k entries, ~3 MB.
- **No singleton magic**: callers construct `DartClient()` once and
  reuse it. Module-level `get_dart()` returns a process-wide cached
  instance.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.dart")

_DART_BASE = "https://opendart.fss.or.kr/api"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache"
# v2 cache includes both stock_code→corp_code and normalized_name→entries.
# Old v1 file (stock_code → corp_code only) is ignored and superseded;
# anyone upgrading just re-downloads the corp_code.xml on first call.
_CORPCODE_CACHE = _CACHE_DIR / "dart_corpcode_v2.json"
_CORPCODE_TTL_DAYS = 30
_HTTP_TIMEOUT = 10  # seconds — keep tight so a slow DART doesn't stall analysis


def _normalize_name(name: str) -> str:
    """Lowercase + strip common Korean corporate suffixes / whitespace
    so '삼성전자', '삼성전자(주)', '주식회사 삼성전자' all collapse to
    the same key. Used to make name lookup forgiving of how the user
    types it vs how DART stores it."""
    n = (name or "").strip()
    for noise in ("(주)", "㈜", "(유)", "주식회사 ", "주식회사", "유한회사 ", "유한회사"):
        n = n.replace(noise, "")
    return n.strip().lower()


class DartClient:
    """Single-key DART client. Cheap to instantiate; reuse across calls
    to amortize the corp_code mapping load."""

    def __init__(self, api_key: Optional[str] = None):
        # Read key lazily so a missing env var doesn't crash module import.
        self.api_key = (api_key or os.getenv("DART_API_KEY") or "").strip()
        self._corp_code_map: dict[str, str] | None = None  # stock_code → corp_code
        # normalized name → list of {name, stock_code, corp_code} entries.
        # One normalized key can map to multiple companies when a search
        # like "현대" matches several entries — caller decides what to do.
        self._name_map: dict[str, list[dict]] | None = None
        # Reverse map: stock_code → corp_name. Built lazily on first
        # stock_code_to_name() call from _name_map; not persisted to
        # disk because it's cheap to rebuild from the v2 cache.
        self._stock_to_name: dict[str, str] | None = None

    # ── corp_code mapping ───────────────────────────────────────────────
    def _load_corp_code_map(self) -> dict[str, str]:
        """Stock code (6-digit) → corp_code (8-digit). DART exposes the
        mapping as a single zipped XML; we cache it locally for 30 days
        so we don't re-download on every analysis. The cache also carries
        the reverse name→entries map so `find_by_name()` doesn't have to
        re-parse the XML."""
        if self._corp_code_map is not None and self._name_map is not None:
            return self._corp_code_map

        # Disk cache check (v2 format: dict with 'stock_to_corp' and
        # 'name_to_entries' keys).
        if _CORPCODE_CACHE.exists():
            try:
                age_days = (time.time() - _CORPCODE_CACHE.stat().st_mtime) / 86400
                if age_days < _CORPCODE_TTL_DAYS:
                    data = json.loads(_CORPCODE_CACHE.read_text())
                    self._corp_code_map = data.get("stock_to_corp", {})
                    self._name_map = data.get("name_to_entries", {})
                    return self._corp_code_map
            except Exception as exc:
                log.warning("dart: corp_code cache read failed: %s", exc)

        # Fetch fresh.
        if not self.api_key:
            log.warning("dart: DART_API_KEY missing — corp_code map unavailable")
            self._corp_code_map = {}
            self._name_map = {}
            return self._corp_code_map

        try:
            resp = requests.get(
                f"{_DART_BASE}/corpCode.xml",
                params={"crtfc_key": self.api_key},
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_bytes = zf.read("CORPCODE.xml")
            root = ET.fromstring(xml_bytes)
        except Exception as exc:
            log.warning("dart: corp_code download failed: %s", exc)
            self._corp_code_map = {}
            self._name_map = {}
            return self._corp_code_map

        stock_to_corp: dict[str, str] = {}
        name_to_entries: dict[str, list[dict]] = {}
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code = (item.findtext("corp_code") or "").strip()
            corp_name = (item.findtext("corp_name") or "").strip()
            # Only KRX-listed entries have a 6-digit stock_code; skip the
            # rest (DART also tracks unlisted entities).
            if not (stock_code and len(stock_code) == 6 and corp_code):
                continue
            stock_to_corp[stock_code] = corp_code
            norm = _normalize_name(corp_name)
            if norm:
                name_to_entries.setdefault(norm, []).append({
                    "name": corp_name,
                    "stock_code": stock_code,
                    "corp_code": corp_code,
                })
        log.info(
            "dart: loaded %d corp_code / %d unique normalized names",
            len(stock_to_corp), len(name_to_entries),
        )

        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _CORPCODE_CACHE.write_text(json.dumps(
                {"stock_to_corp": stock_to_corp, "name_to_entries": name_to_entries},
                ensure_ascii=False,
            ))
        except Exception as exc:
            log.warning("dart: corp_code cache write failed: %s", exc)

        self._corp_code_map = stock_to_corp
        self._name_map = name_to_entries
        return stock_to_corp

    def find_by_name(self, query: str) -> list[dict]:
        """Resolve a Korean / English company name to listed-entity entries.

        Returns a list of {name, stock_code, corp_code} dicts ordered by
        match specificity:
          - Exact normalized match (e.g. '삼성전자' → 005930)
          - Prefix or substring match if no exact hit
          - Empty list when nothing matches at all

        Capped at 20 to keep ambiguous queries (e.g. '현대') tractable
        for the caller's UX."""
        norm = _normalize_name(query)
        if not norm:
            return []
        self._load_corp_code_map()
        if not self._name_map:
            return []

        # Exact-normalized match wins.
        exact = self._name_map.get(norm) or []
        if exact:
            return exact[:20]

        # Fallback: prefix match first (more specific), then substring.
        prefix: list[dict] = []
        contains: list[dict] = []
        for n, entries in self._name_map.items():
            if n.startswith(norm):
                prefix.extend(entries)
            elif norm in n:
                contains.extend(entries)
        return (prefix + contains)[:20]

    def stock_code_to_corp_code(self, stock_code: str) -> Optional[str]:
        """Resolve 6-digit KRX stock code → 8-digit DART corp_code.
        Strips a trailing `.KS`/`.KQ` for caller convenience."""
        code = (stock_code or "").upper().split(".")[0]
        if not (code.isdigit() and len(code) == 6):
            return None
        return self._load_corp_code_map().get(code)

    def stock_code_to_name(self, stock_code: str) -> Optional[str]:
        """Reverse lookup: 6-digit stock code → Korean corp name.
        Used by the analyzer to render '삼성전자 / 005930.KS' style
        titles. Builds a stock→name map lazily on first call from the
        cached _name_map so we don't pay the O(n) cost more than once
        per bot lifetime."""
        code = (stock_code or "").upper().split(".")[0]
        if not (code.isdigit() and len(code) == 6):
            return None
        self._load_corp_code_map()
        if not self._name_map:
            return None
        if self._stock_to_name is None:
            self._stock_to_name = {
                e["stock_code"]: e["name"]
                for entries in self._name_map.values()
                for e in entries
            }
        return self._stock_to_name.get(code)

    # ── /api/list.json — recent disclosures ─────────────────────────────
    def get_recent_disclosures(
        self, stock_code: str, days_back: int = 30, limit: int = 20
    ) -> list[dict]:
        """Return up to `limit` most recent disclosure entries within the
        last `days_back` calendar days. Each entry has 'date', 'title',
        'reporter', 'url'. Empty list when key missing / network fails /
        corp_code unresolved — never raises."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []

        end = date.today()
        bgn = end - timedelta(days=days_back)
        try:
            resp = requests.get(
                f"{_DART_BASE}/list.json",
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bgn_de": bgn.strftime("%Y%m%d"),
                    "end_de": end.strftime("%Y%m%d"),
                    "page_count": min(limit, 100),
                },
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: list.json fetch for %s failed: %s", stock_code, exc)
            return []

        # DART error envelope: status "000" = success, anything else = no data.
        if payload.get("status") not in ("000",):
            return []
        rows = payload.get("list") or []
        out: list[dict] = []
        for r in rows[:limit]:
            out.append({
                "date": r.get("rcept_dt") or "",
                "title": (r.get("report_nm") or "").strip(),
                "reporter": (r.get("flr_nm") or "").strip(),
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.get('rcept_no', '')}",
            })
        return out

    # ── /api/elestock.json — insider / major shareholder holdings ──────
    def get_insider_holdings(self, stock_code: str) -> list[dict]:
        """Return rows of officer / major shareholder current holdings.
        Each row: 'name', 'role', 'shares', 'pct', 'changed_on'. Empty
        list on any failure mode."""
        if not self.api_key:
            return []
        corp_code = self.stock_code_to_corp_code(stock_code)
        if not corp_code:
            return []

        try:
            resp = requests.get(
                f"{_DART_BASE}/elestock.json",
                params={"crtfc_key": self.api_key, "corp_code": corp_code},
                timeout=_HTTP_TIMEOUT,
            )
            payload = resp.json()
        except Exception as exc:
            log.warning("dart: elestock for %s failed: %s", stock_code, exc)
            return []

        if payload.get("status") not in ("000",):
            return []
        rows = payload.get("list") or []
        out: list[dict] = []
        for r in rows:
            # DART returns holding rows with rolling history; we only want
            # the latest per-person snapshot. Caller can dedupe later if
            # needed — for now expose everything and let the prompt
            # builder cap the list.
            #
            # Field names per DART /api/elestock.json spec (verified
            # against 한솔케미칼 2026-05-17 case where all 5 entries
            # showed 0.00% with the old 'stkqy' / 'stkrt' guesses —
            # those field names don't exist on this endpoint):
            #   sp_stock_lmp_cnt   — 특정증권등 소유수
            #   sp_stock_lmp_rate  — 특정증권등 소유비율 (%, e.g. "1.65")
            # Fallback to the old names just in case DART exposes them
            # for some response shapes; defensive against future schema
            # changes.
            shares_raw = str(
                r.get("sp_stock_lmp_cnt") or r.get("stkqy") or "0"
            ).replace(",", "")
            try:
                shares = int(shares_raw)
            except ValueError:
                shares = 0
            pct_raw = str(
                r.get("sp_stock_lmp_rate") or r.get("stkrt") or "0"
            ).replace(",", "")
            try:
                pct = float(pct_raw)
            except ValueError:
                pct = 0.0
            out.append({
                "name": (r.get("repror") or "").strip(),
                "role": (r.get("isu_exctv_ofcps") or "").strip(),
                "shares": shares,
                "pct": pct,
                "changed_on": (r.get("rcept_dt") or "").strip(),
            })
        return out

    # ── earnings window estimate ────────────────────────────────────────
    def next_earnings_window(self, stock_code: str, today: date | None = None) -> Optional[tuple[date, date]]:
        """Infer the most-likely next KR earnings disclosure window.

        Korean listed companies must file quarterly reports within 45
        days of quarter end (분기보고서) and annual/Q4 reports within
        90 days of fiscal year end (사업보고서). Assuming a Dec fiscal
        year (true for ~95% of KOSPI), the windows are:
          - Q1 (Mar-end) → due by May 15
          - Q2 (Jun-end) → due by Aug 14
          - Q3 (Sep-end) → due by Nov 14
          - Q4 + annual (Dec-end) → due by Apr 1 next year

        Returns (window_start, window_end) of the NEXT upcoming window,
        or None if computation fails."""
        today = today or date.today()
        year = today.year
        # Build the four nominal due-by dates for this fiscal year.
        candidates = [
            (date(year, 5, 15), date(year, 4, 15)),    # Q1 window 4/15-5/15
            (date(year, 8, 14), date(year, 7, 14)),    # Q2 window 7/14-8/14
            (date(year, 11, 14), date(year, 10, 14)),  # Q3 window 10/14-11/14
            (date(year + 1, 4, 1), date(year + 1, 3, 1)),  # Q4/annual 3/1-4/1
        ]
        for due, window_start in candidates:
            if today <= due:
                return (window_start, due)
        # All windows for this calendar year passed — return next year's Q1.
        return (date(year + 1, 4, 15), date(year + 1, 5, 15))


# Process-wide cached instance so the corp_code map only loads once
# per bot lifetime. Reset by restarting the bot.
_singleton: DartClient | None = None


def get_dart() -> DartClient:
    """Return the shared DartClient. Call this from analysts / pre-fetch
    helpers instead of constructing a new client per analysis."""
    global _singleton
    if _singleton is None:
        _singleton = DartClient()
    return _singleton
