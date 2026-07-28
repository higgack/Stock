"""Naver Finance PER / PBR / EPS / BPS scraper for KR equities.

Naver Finance (finance.naver.com/item/main.naver?code=NNNNNN) is the
most widely-used Korean consumer-facing stock data portal and carries
real-time consensus PER / PBR / EPS / BPS derived from QuantiWise/FnGuide.
Useful as a 4th-pass fallback in the D1 KR valuation chain:
  yfinance → pykrx → KIS → Naver Finance (this module)
when upstream sources miss or return 0 for these valuation metrics.

No API key required. 24h disk cache per (code, date).
Returns None on any network / parse failure so callers fall through to N/A.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date as _date
from typing import Optional

_log = logging.getLogger(__name__)
_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
os.makedirs(_CACHE_DIR, exist_ok=True)


def _cache_path(code: str, today: str) -> str:
    return os.path.join(_CACHE_DIR, f"naver_fin_{code}_{today}.json")


def _load_cached(code: str, today: str) -> Optional[dict]:
    path = _cache_path(code, today)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _save_cache(code: str, today: str, data: dict) -> None:
    path = _cache_path(code, today)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _parse_float(text: str | None) -> Optional[float]:
    if not text:
        return None
    text = text.strip().replace(",", "")
    # reject placeholder values
    if text in ("N/A", "-", "", "0"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def get_naver_valuation(ticker: str) -> Optional[dict]:
    """Return {per, eps, pbr, bps, shares} from Naver Finance for a KR ticker.

    ticker: '005930.KS' or '005930' — both accepted.
    shares (상장주식수) extracted from the company-info side panel — useful
    as a 4th-pass fallback for sharesOutstanding when yfinance/pykrx/KIS
    all miss it (root cause of 047810.KS N/A cascade 2026-05-23).
    Returns None on network / parse failure so callers can fall to N/A.
    24h disk cache keyed by (code, today-ISO).
    """
    code = ticker.upper().split(".")[0]
    if not re.fullmatch(r"\d{6}", code):
        return None

    today = _date.today().isoformat()
    cached = _load_cached(code, today)
    if cached is not None:
        return cached if cached else None  # {} = confirmed empty

    try:
        import requests

        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": "https://finance.naver.com/",
        }
        resp = requests.get(url, headers=headers, timeout=12)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        _log.warning("Naver Finance fetch failed for %s: %s", code, exc)
        return None

    def _extract_em(em_id: str) -> Optional[float]:
        # Naver Finance uses <em id="_per">47.56</em> (or blank / N/A).
        m = re.search(
            rf'<em[^>]+id=["\']?{re.escape(em_id)}["\']?[^>]*>([^<]*)</em>',
            html,
        )
        if m:
            return _parse_float(m.group(1))
        return None

    result: dict = {}
    per = _extract_em("_per")
    eps = _extract_em("_eps")
    pbr = _extract_em("_pbr")
    bps = _extract_em("_bps")

    if per is not None and per > 0:
        result["per"] = per
    if eps is not None:
        result["eps"] = eps
    if pbr is not None and pbr > 0:
        result["pbr"] = pbr
    if bps is not None:
        result["bps"] = bps

    # 상장주식수 — Naver Finance side panel renders this as
    # "상장주식수<...>...</...><...>97,475,107</...>". The label-value
    # distance varies (table cell or definition-list), so match the
    # label anywhere and capture the next number with commas.
    shares = None
    m = re.search(
        r"상장주식수[\s\S]{0,200}?([\d,]{6,20})",
        html,
    )
    if m:
        raw = m.group(1).replace(",", "")
        try:
            sh = int(raw)
            # Sanity: KR listed share counts range from ~1M to ~10B.
            if 100_000 <= sh <= 100_000_000_000:
                shares = sh
                result["shares"] = sh
        except ValueError:
            pass

    _save_cache(code, today, result)
    _log.info(
        "Naver Finance %s → per=%s eps=%s pbr=%s bps=%s shares=%s",
        code, per, eps, pbr, bps, shares,
    )
    return result if result else None


def get_naver_foreign_holding(ticker: str) -> Optional[dict]:
    """Return foreign ownership data from Naver Finance for a KR ticker.

    Returns:
        {
            "date": str (today ISO or page date),
            "foreign_pct": float (외국인보유비율 %),
            "foreign_shares": int,
            "listed_shares": int,
            "limit_exhaustion_pct": float (외국인소진율 %, if available),
            "source": "naver",
        }
        or None on failure.
    """
    code = ticker.upper().split(".")[0]
    if not re.fullmatch(r"\d{6}", code):
        return None

    today = _date.today().isoformat()
    ck = f"naver_frgn_{code}_{today}"
    cached = _load_cached(code, today)
    if cached is not None and cached.get("_frgn"):
        return cached.get("_frgn") or None

    try:
        import requests

        url = f"https://finance.naver.com/item/frgn.naver?code={code}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": f"https://finance.naver.com/item/main.naver?code={code}",
        }
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            _log.info("Naver frgn %s → HTTP %d", code, resp.status_code)
            return None
        html = resp.text
        if len(html) < 500:
            _log.info("Naver frgn %s → page too short (%d)", code, len(html))
            return None
    except Exception as exc:
        _log.warning("Naver frgn fetch failed for %s: %s", code, exc)
        return None

    foreign_pct = None
    foreign_shares = None
    listed_shares = None
    exhaust_pct = None

    m = re.search(
        r"보유비율[\s\S]{0,120}?([\d,]+\.?\d*)\s*%",
        html,
    )
    if m:
        foreign_pct = _parse_float(m.group(1))

    m = re.search(
        r"소진율[\s\S]{0,120}?([\d,]+\.?\d*)\s*%",
        html,
    )
    if m:
        exhaust_pct = _parse_float(m.group(1))

    m = re.search(
        r"보유주식수[\s\S]{0,150}?([\d,]{4,20})",
        html,
    )
    if m:
        try:
            foreign_shares = int(m.group(1).replace(",", ""))
        except ValueError:
            pass

    m = re.search(
        r"한도주식수[\s\S]{0,150}?([\d,]{4,20})",
        html,
    )
    if m:
        try:
            listed_shares = int(m.group(1).replace(",", ""))
        except ValueError:
            pass

    if foreign_pct is None and foreign_shares is None:
        url2 = f"https://finance.naver.com/item/main.naver?code={code}"
        try:
            resp2 = requests.get(url2, headers=headers, timeout=12)
            html2 = resp2.text if resp2.status_code == 200 else ""
        except Exception:
            html2 = ""

        if html2 and len(html2) > 500:
            m = re.search(
                r"외국인소진율[\s\S]{0,120}?([\d,]+\.?\d*)\s*%",
                html2,
            )
            if m:
                exhaust_pct = _parse_float(m.group(1))

            m = re.search(
                r"외국인[\s\S]{0,30}?보유[\s\S]{0,30}?비율[\s\S]{0,80}?([\d,]+\.?\d*)\s*%",
                html2,
            )
            if m:
                foreign_pct = _parse_float(m.group(1))

    if foreign_pct is None and exhaust_pct is None:
        _log.info("Naver frgn %s → no foreign data found", code)
        return None

    result: dict = {
        "date": today,
        "source": "naver",
    }
    if foreign_pct is not None:
        result["foreign_pct"] = round(foreign_pct, 2)
    if foreign_shares is not None:
        result["foreign_shares"] = foreign_shares
    if listed_shares is not None:
        result["listed_shares"] = listed_shares
    if exhaust_pct is not None:
        result["limit_exhaustion_pct"] = round(exhaust_pct, 2)

    try:
        cache_data = _load_cached(code, today) or {}
        cache_data["_frgn"] = result
        _save_cache(code, today, cache_data)
    except Exception:
        pass

    _log.info(
        "Naver frgn %s → pct=%s exhaust=%s shares=%s",
        code, foreign_pct, exhaust_pct, foreign_shares,
    )
    return result
