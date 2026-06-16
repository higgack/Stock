"""Kabutan (株探) consensus scraper for JP stocks.

Background: yfinance covers JP-listed large-caps (Nikkei 225 components +
big TOPIX names) reliably — `7203.T` returns targetMeanPrice, recommendation*,
numberOfAnalystOpinions for Toyota / Sony / SMFG etc. For the long tail
(JASDAQ mid/small-caps and lower-TOPIX names), yfinance returns None.

Kabutan exposes the same Toyo Keizai / QUICK-sourced consensus block at
`kabutan.jp/stock/?code=NNNN` (no auth, public HTML). This module is the
fallback path — call ONLY when yfinance consensus fields are empty for a
`.T` ticker.

Design constraints mirror fnguide_consensus.py:
- Three independent regex strategies per field for resilience.
- Realistic Chrome User-Agent.
- 10-second timeout.
- Pure regex, no bs4.
- Graceful None on any failure — small-caps with zero coverage are
  legitimate, callers degrade to '애널리스트 커버리지 없음'.

Note on Japanese number parsing: Kabutan renders target prices as
'円' (yen) with comma thousands separators ('3,250'); we strip the
commas and return a bare integer/float yen value. Some pages render
ratings as '強気 / 中立 / 弱気' (strong-buy / neutral / underweight) —
we map those to '매수 / 보유 / 매도' to match the FnGuide/yfinance
unified schema downstream.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

log = logging.getLogger("bot.kabutan")

# Standard stock detail page; analyst consensus block is embedded there
# (saves a second HTTP hit vs. the /yutai or /finance subpages).
_URL_TMPL = "https://kabutan.jp/stock/?code={code}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
}
_TIMEOUT = 10

# Map Kabutan / Japanese-broker rating phrases to the unified schema the
# rest of the bot uses. 強気=Buy, 中立=Hold, 弱気=Sell. Kabutan also uses
# numeric scales sometimes (1=Strong Buy → 5=Sell) but we focus on the
# textual rating shown in the consensus block.
_RATING_MAP = {
    "強気": "매수",
    "やや強気": "매수",
    "買い": "매수",
    "中立": "보유",
    "ニュートラル": "보유",
    "弱気": "매도",
    "やや弱気": "매도",
    "売り": "매도",
}


def fetch_consensus(stock_code: str) -> Optional[dict]:
    """Scrape Kabutan consensus for a Tokyo-listed stock.

    Args:
        stock_code: 4-digit ticker code (`.T` suffix stripped automatically).

    Returns:
        dict with keys:
          - target_mean: float, JPY (per-share)
          - rating: str ('매수' | '보유' | '매도') or None
          - n_analysts: int or None
          - last_report_date: str 'YYYY-MM-DD' or None
        OR None when the page is unreachable, the ticker isn't covered,
        or parsing fails on every strategy. Callers MUST handle None
        gracefully — never assume the fallback succeeded.
    """
    code = (stock_code or "").upper().split(".")[0]
    if not (code.isdigit() and len(code) == 4):
        return None

    # 12h 디스크 캐시 (사용자 2026-06-16 A3) — fnguide 와 동일 사유(무게이트
    # 컨센서스가 라이브 오버레이에서 매 조회 스크레이프되는 것 차단). 성공·
    # 무커버리지 캐시, 네트워크 오류 미캐시.
    from bot.finviz_client import _cache_write, _cached
    ck = f"kabutan_consensus_{code}.json"
    hit = _cached(ck, ttl=12 * 3600)
    if isinstance(hit, dict) and "data" in hit:
        return hit["data"] or None

    try:
        resp = requests.get(
            _URL_TMPL.format(code=code), headers=_HEADERS, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        # Kabutan uses Shift-JIS historically; modern pages serve UTF-8
        # but a stale Content-Type is possible. requests.text usually
        # gets it right; force utf-8 if the apparent encoding fails the
        # Japanese-character sanity check.
        html = resp.text
        if "目標株価" not in html and "予想株価" not in html:
            resp.encoding = "utf-8"
            html = resp.text
    except Exception as exc:
        log.warning("kabutan: fetch failed for %s: %s", code, exc)
        return None   # 네트워크 오류 — 미캐시

    target = _parse_target(html)
    rating = _parse_rating(html)
    n_analysts = _parse_analyst_count(html)
    last_report_date = _parse_latest_report_date(html)

    if target is None and rating is None and n_analysts is None:
        _cache_write(ck, {"data": None})
        return None

    result = {
        "last_report_date": last_report_date,
        "target_mean": target,
        "rating": rating,
        "n_analysts": n_analysts,
    }
    _cache_write(ck, {"data": result})
    return result


def _parse_target(html: str) -> Optional[float]:
    """Find the consensus target price in JPY. Kabutan labels it as
    '目標株価' (target price) / '予想株価' (forecast price) /
    'コンセンサス目標株価' depending on the page revision."""
    patterns = [
        r"コンセンサス\s*目標\s*株価[^0-9]{0,80}?([0-9]{1,3}(?:,[0-9]{3})+|\d+(?:\.\d+)?)",
        r"目標\s*株価[^0-9]{0,80}?([0-9]{1,3}(?:,[0-9]{3})+|\d+(?:\.\d+)?)",
        r"予想\s*株価[^0-9]{0,80}?([0-9]{1,3}(?:,[0-9]{3})+|\d+(?:\.\d+)?)",
    ]
    for p in patterns:
        m = re.search(p, html)
        if m:
            try:
                v = float(m.group(1).replace(",", ""))
                # Sanity: JP single-stock prices range ¥100 ~ ¥10M.
                # Smaller values are likely a misparse of an unrelated
                # number (e.g. dividend yield 1.5 → 1).
                if 50 <= v <= 50_000_000:
                    return v
            except ValueError:
                continue
    return None


def _parse_rating(html: str) -> Optional[str]:
    """Find the analyst rating. Kabutan uses Japanese rating words
    (強気 / 中立 / 弱気 etc.) which we map to the unified '매수 /
    보유 / 매도' schema."""
    # Try labelled patterns first (most reliable).
    labelled = re.search(
        r"(?:アナリスト\s*評価|レーティング|投資判断)"
        r"[^一-龥ぁ-んァ-ヶ]{0,40}?"
        r"(強気|やや強気|買い|中立|ニュートラル|弱気|やや弱気|売り)",
        html,
    )
    if labelled:
        return _RATING_MAP.get(labelled.group(1))

    # Fallback: bare keyword scan (least reliable, easy false positive).
    for jp_word, unified in _RATING_MAP.items():
        if jp_word in html:
            return unified
    return None


def _parse_analyst_count(html: str) -> Optional[int]:
    """Find the analyst count. Kabutan labels it as 'アナリスト数' or
    'N人のアナリスト' or 'N社のアナリスト'."""
    patterns = [
        r"アナリスト\s*数[^0-9]{0,40}?([0-9]+)",
        r"([0-9]+)\s*人\s*の\s*アナリスト",
        r"([0-9]+)\s*社\s*の\s*アナリスト",
        r"対象\s*アナリスト[^0-9]{0,40}?([0-9]+)",
    ]
    for p in patterns:
        m = re.search(p, html)
        if m:
            try:
                n = int(m.group(1))
                if 0 < n <= 100:
                    return n
            except ValueError:
                continue
    return None


def _parse_latest_report_date(html: str) -> Optional[str]:
    """Find the latest analyst-report date on the Kabutan page.
    Useful for staleness detection mirroring fnguide_consensus.

    Two-pass: labelled patterns ('更新日' / '最終更新') first; then
    fall back to picking the most recent plausible date.
    Returns 'YYYY-MM-DD' or None."""
    # Pass 1: labelled patterns. Note JP date format variants:
    # '2026/05/17' / '2026年5月17日' / '2026-05-17'.
    labelled = re.search(
        r"(?:更新日|最終\s*更新|発表日|レポート\s*日付)"
        r"[^0-9]{0,40}?(\d{4})[年./\-](\d{1,2})[月./\-](\d{1,2})",
        html,
    )
    if labelled:
        y, m, d = int(labelled.group(1)), int(labelled.group(2)), int(labelled.group(3))
        if 2020 <= y <= 2030 and 1 <= m <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{m:02d}-{d:02d}"

    candidates: list[tuple[int, int, int]] = []
    for match in re.finditer(r"(\d{4})[年./\-](\d{1,2})[月./\-](\d{1,2})", html):
        try:
            y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if 2020 <= y <= 2030 and 1 <= m <= 12 and 1 <= d <= 31:
                candidates.append((y, m, d))
        except Exception:
            continue
    if not candidates:
        return None
    y, m, d = max(candidates)
    return f"{y:04d}-{m:02d}-{d:02d}"
