"""FnGuide CompanyGuide consensus scraper for KR stocks.

Background research established that yfinance covers KOSPI Top 100
reliably (targetMeanPrice / numberOfAnalystOpinions / recommendationMean
all populated) but thins out fast below mid-cap. For those gaps, the
free public CompanyGuide pages at `comp.fnguide.com/SVO2/asp/SVD_Consensus.asp`
expose the same FnGuide consensus block that paid feeds aggregate from,
including mid-caps yfinance returns None for.

This module is the fallback path. Callers should try yfinance FIRST and
only invoke `fetch_consensus()` when the yfinance consensus fields are
empty for a `.KS`/`.KQ` ticker. Small-cap KOSDAQ may have NO coverage
anywhere — the function returns None silently in that case, and the
caller renders "분석가 커버리지 없음" rather than fabricating numbers.

Design constraints:
- Defensive parsing — three independent regex strategies per field so
  one CSS class change doesn't kill the whole signal.
- Realistic User-Agent so FnGuide doesn't 403 us as a bot.
- Tight 10s timeout — a slow scrape must not delay the analyzer.
- No bs4 dependency; pure regex on response text keeps the install
  footprint identical to the rest of the bot.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

log = logging.getLogger("bot.fnguide")

# `gicode=A{6digit}` is the page's required parameter form (the 'A'
# prefix is FnGuide's internal namespace for KRX-listed names).
_URL_TMPL = (
    "https://comp.fnguide.com/SVO2/asp/SVD_Consensus.asp"
    "?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=108&stkGb=701"
)
_HEADERS = {
    # Real Chrome UA — FnGuide actively 403s default python-requests UA.
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
}
_TIMEOUT = 10


def fetch_consensus(stock_code: str) -> Optional[dict]:
    """Scrape FnGuide CompanyGuide consensus for a Korean stock.

    Args:
        stock_code: 6-digit KRX code (`.KS`/`.KQ` suffix stripped
            automatically).

    Returns:
        dict with keys:
          - target_mean: float, KRW
          - rating: str ('매수' | '보유' | '매도') or None
          - n_analysts: int or None
        OR None when the page is unreachable, the ticker isn't covered,
        or parsing fails on every strategy. Callers MUST handle None
        gracefully — never assume the fallback succeeded.
    """
    code = (stock_code or "").upper().split(".")[0]
    if not (code.isdigit() and len(code) == 6):
        return None

    try:
        resp = requests.get(
            _URL_TMPL.format(code=code), headers=_HEADERS, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        log.warning("fnguide: fetch failed for %s: %s", code, exc)
        return None

    target = _parse_target(html)
    rating = _parse_rating(html)
    n_analysts = _parse_analyst_count(html)

    # Empty page (small-cap with zero coverage) — return None so the
    # caller can label '분석가 커버리지 없음' instead of showing a
    # half-empty consensus line that looks broken.
    if target is None and rating is None and n_analysts is None:
        return None

    return {
        "target_mean": target,
        "rating": rating,
        "n_analysts": n_analysts,
    }


def _parse_target(html: str) -> Optional[float]:
    """Find the consensus target price (KRW). FnGuide labels it as
    '목표주가' / '평균 목표주가' / '컨센서스 목표주가' in different
    blocks — try each near-label pattern in turn."""
    patterns = [
        r"평균\s*목표\s*주가[^0-9]{0,80}?([0-9]{1,3}(?:,[0-9]{3})+)",
        r"컨센서스\s*목표[^0-9]{0,80}?([0-9]{1,3}(?:,[0-9]{3})+)",
        r"목표\s*주가[^0-9]{0,80}?([0-9]{1,3}(?:,[0-9]{3})+)",
    ]
    for p in patterns:
        m = re.search(p, html)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def _parse_rating(html: str) -> Optional[str]:
    """Find investment opinion: 매수 / 보유 / 매도. The page labels it
    as '투자의견' followed shortly by the rating word."""
    m = re.search(r"투자\s*의견[^가-힣]{0,40}?(매수|보유|매도)", html)
    if m:
        return m.group(1)
    return None


def _parse_analyst_count(html: str) -> Optional[int]:
    """Find analyst count. FnGuide labels it as '추정기관수' or via
    'N개 증권사' phrasing — try both."""
    patterns = [
        r"추정\s*기관\s*수[^0-9]{0,40}?([0-9]+)",
        r"([0-9]+)\s*개\s*증권사",
        r"증권사\s*([0-9]+)\s*개",
    ]
    for p in patterns:
        m = re.search(p, html)
        if m:
            try:
                n = int(m.group(1))
                # Sanity: 0~100 analysts plausible, anything else is
                # likely a misparsed number from elsewhere on the page.
                if 0 < n <= 100:
                    return n
            except ValueError:
                continue
    return None
