"""Resolve Korean company names (e.g. '삼성전자') to yfinance-style
tickers (e.g. '005930.KS').

Two steps:

1. **Name → stock_code** via DART's corp_code mapping (`DartClient.find_by_name`).
   Returns one canonical match for exact name lookups, or a candidate
   list for ambiguous prefixes like '현대' (which matches 현대자동차,
   현대모비스, 현대건설, etc.).

2. **stock_code → .KS / .KQ suffix** via a yfinance probe. DART's
   corp_code.xml carries no market indicator, so we try `<code>.KS`
   first (KOSPI), and fall back to `<code>.KQ` (KOSDAQ) only when
   yfinance returns no price. The result is persisted at
   `~/.tradingagents/cache/dart_market_suffix.json` so we never re-probe
   the same code, and the cache survives bot restarts.

Caller contract (`resolve_korean_name(name)` returns one of):
  - {'status': 'resolved', 'ticker': '005930.KS', 'name': '삼성전자'}
  - {'status': 'multiple', 'candidates': [{'name', 'ticker'}, ...]}
  - {'status': 'not_found'}                  (DART has no match)
  - {'status': 'no_price'}                   (DART match, yfinance dry)

The bot channel router uses `status` to decide between starting an
analysis, replying with a candidate list, or surfacing an error.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.kr_resolver")

_SUFFIX_CACHE = Path.home() / ".tradingagents" / "cache" / "dart_market_suffix.json"


def _load_suffix_cache() -> dict[str, str]:
    """Disk-backed cache of stock_code → '.KS' / '.KQ' / '' (no listing)."""
    try:
        if _SUFFIX_CACHE.exists():
            return json.loads(_SUFFIX_CACHE.read_text())
    except Exception as exc:
        log.warning("kr_resolver: suffix cache read failed: %s", exc)
    return {}


def _save_suffix_cache(cache: dict[str, str]) -> None:
    try:
        _SUFFIX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _SUFFIX_CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    except Exception as exc:
        log.warning("kr_resolver: suffix cache write failed: %s", exc)


def _probe_suffix(stock_code: str) -> Optional[str]:
    """Try `.KS` first (KOSPI), then `.KQ` (KOSDAQ). Return the first
    suffix where yfinance returns a usable price, or None if both fail.
    yfinance import is deferred so unrelated callers don't pay for it."""
    try:
        import yfinance as yf
    except Exception:
        return None
    for suffix in (".KS", ".KQ"):
        try:
            info = yf.Ticker(stock_code + suffix).info or {}
        except Exception:
            continue
        if info.get("regularMarketPrice") or info.get("currentPrice"):
            return suffix
    return None


def stock_code_to_ticker(stock_code: str) -> Optional[str]:
    """Resolve a 6-digit KRX stock code to a yfinance-format ticker.

    Uses the on-disk suffix cache when available; otherwise probes
    yfinance and persists the result. Returns None when the code
    isn't listed on either KOSPI or KOSDAQ (delisted, preferred-only,
    etc.)."""
    if not (stock_code and stock_code.isdigit() and len(stock_code) == 6):
        return None
    cache = _load_suffix_cache()
    if stock_code in cache:
        suffix = cache[stock_code]
        return f"{stock_code}{suffix}" if suffix else None

    suffix = _probe_suffix(stock_code)
    # Store '' for negative cache too so we don't re-probe broken codes
    # on every analysis attempt.
    cache[stock_code] = suffix or ""
    _save_suffix_cache(cache)
    return f"{stock_code}{suffix}" if suffix else None


def resolve_korean_name(name: str) -> dict:
    """Translate a user-typed Korean company name to a status dict.

    See module docstring for the return shape. The caller (channel
    router) reads `status` and branches:
      - 'resolved' → kick off analysis with `ticker`
      - 'multiple' → reply with `candidates` list and ask user to retype
      - 'not_found' → reply '회사명을 찾을 수 없습니다'
      - 'no_price' → reply '상장 정보를 확인할 수 없습니다 (상장폐지 가능)'

    Resolver never raises — DART / yfinance failures degrade to
    'not_found' so the bot keeps running. Look at the journal for the
    underlying cause."""
    if not name or not name.strip():
        return {"status": "not_found"}

    try:
        from bot.dart_client import get_dart
        dart = get_dart()
        candidates = dart.find_by_name(name)
    except Exception as exc:
        log.warning("kr_resolver: dart lookup failed for %r: %s", name, exc)
        return {"status": "not_found"}

    if not candidates:
        return {"status": "not_found"}

    # Single exact match — probe suffix and return resolved ticker.
    if len(candidates) == 1:
        c = candidates[0]
        ticker = stock_code_to_ticker(c["stock_code"])
        if ticker:
            return {"status": "resolved", "ticker": ticker, "name": c["name"]}
        return {"status": "no_price"}

    # Multiple candidates — resolve each to a ticker, drop unlisted ones.
    resolved: list[dict] = []
    for c in candidates[:10]:  # cap UX list at 10
        t = stock_code_to_ticker(c["stock_code"])
        if t:
            resolved.append({"name": c["name"], "ticker": t})
    if not resolved:
        return {"status": "no_price"}
    if len(resolved) == 1:
        return {
            "status": "resolved",
            "ticker": resolved[0]["ticker"],
            "name": resolved[0]["name"],
        }
    return {"status": "multiple", "candidates": resolved}
