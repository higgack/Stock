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
    """Return {per, eps, pbr, bps} from Naver Finance for a KR ticker.

    ticker: '005930.KS' or '005930' — both accepted.
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

    _save_cache(code, today, result)
    _log.info("Naver Finance %s → per=%s eps=%s pbr=%s bps=%s", code, per, eps, pbr, bps)
    return result if result else None
