"""한경 컨센서스 (consensus.hankyung.com) scraper for KR mid-cap KOSDAQ.

A3 (Step 2A item ⑥, 2026-05-19): FnGuide 가 mid-cap / KOSDAQ 일부
종목 누락하는 영역 보강. 한경 컨센서스는 한국 증권사 sell-side 리서치
리포트를 종목별로 집계 — FnGuide 보다 mid-cap 커버리지 넓음.

Background: yfinance 가 KOSPI Top 100 cover, FnGuide 가 yfinance 보다
넓게 mid-cap cover, 한경 컨센서스가 그보다 더 넓게 small-mid cap
cover. 3 단계 fallback:
  1) yfinance .info (targetMeanPrice / numberOfAnalystOpinions)
  2) FnGuide CompanyGuide (consensus.hankyung 보다 빠르고 안정적)
  3) 한경 컨센서스 (mid-cap KOSDAQ 보강용)

호출 패턴: get_market_signals_for() 가 이미 2단계 fallback wired. A3
는 3단계 추가.

자료 출처: consensus.hankyung.com — 종목별 리포트 list + 평균 목표가
+ 평균 투자의견. Static HTML scrape.

URL pattern: consensus.hankyung.com/apps.analysis/analysis.list?sk=
{ticker_code}&search_type=2

자료 구조 (HTML table 의 each row):
 • 발행일 / 증권사 / 애널리스트 / 목표가 / 투자의견 / 보고서 제목
일부 종목은 'Hot Report' tag 가 별도 — 단순화 위해 무시.

Caching: per-(ticker, day) JSON at ~/.tradingagents/cache/hk_consensus/
with 12h TTL — 한경 리포트는 일별 갱신.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.hk_consensus")

_BASE_URL = "https://consensus.hankyung.com/apps.analysis/analysis.list"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "hk_consensus"
_CACHE_TTL_HOURS = 12
_HTTP_TIMEOUT = 15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
}

# Rating → direction mapping (한경 의견 분류는 보통 5단계).
_RATING_TO_DIRECTION = {
    "매수": "buy", "Buy": "buy", "BUY": "buy", "강력매수": "buy",
    "보유": "hold", "Hold": "hold", "HOLD": "hold", "중립": "hold",
    "Neutral": "hold", "Marketperform": "hold", "Market Perform": "hold",
    "매도": "sell", "Sell": "sell", "SELL": "sell",
    "비중축소": "sell", "Underweight": "sell",
    "비중확대": "buy", "Overweight": "buy",
}


def _normalize_code(ticker: str) -> Optional[str]:
    """Strip .KS/.KQ suffix → 6-digit code."""
    if not ticker:
        return None
    code = ticker.upper().split(".")[0]
    if code.isdigit() and len(code) == 6:
        return code
    return None


def fetch_consensus(ticker: str, days_back: int = 90) -> Optional[dict]:
    """Scrape 한경 컨센서스 종목별 페이지.

    Returns dict {target_price: float, rating: str, analyst_count: int,
    last_report_date: str (YYYY-MM-DD), report_count: int} or None
    on failure / empty coverage.

    days_back: 리포트 lookback window. 한경은 보통 최근 ~50개 정도
    페이지 첫 화면에 표시.
    """
    code = _normalize_code(ticker)
    if not code:
        return None

    cache_key = f"hk_consensus_{code}_{date.today().isoformat()}.json"
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                return cached if cached else None
        except Exception as exc:
            log.warning("hk_consensus: cache read failed for %s: %s", code, exc)

    try:
        resp = requests.get(
            _BASE_URL,
            params={"sk": code, "search_type": "2"},
            headers=_HEADERS,
            timeout=_HTTP_TIMEOUT,
        )
        resp.encoding = "utf-8"
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        log.warning("hk_consensus: fetch failed for %s: %s", code, exc)
        return None

    # Parse report rows. 한경 페이지 구조 (변동 빈도 낮음):
    #   <table class="table_style ...">
    #     <tbody>
    #       <tr>
    #         <td>제목</td>
    #         <td class="text_l">증권사</td>
    #         <td>애널리스트</td>
    #         <td><strong>목표가</strong></td>
    #         <td>투자의견</td>
    #         <td>YYYY-MM-DD</td>
    #       </tr>
    #     </tbody>
    #   </table>

    # Defensive parsing — 3 strategy fallback.
    # Strategy A: tr-row regex matching 6 td cells.
    row_re = re.compile(
        r"<tr[^>]*>"
        r"\s*<td[^>]*>(?:<a[^>]*>)?\s*([^<]+?)\s*(?:</a>)?</td>"  # 1. 제목
        r"\s*<td[^>]*>\s*([^<]+?)\s*</td>"                          # 2. 증권사
        r"\s*<td[^>]*>\s*([^<]+?)\s*</td>"                          # 3. 애널리스트
        r"\s*<td[^>]*>\s*(?:<strong[^>]*>)?\s*([\d,.\s원-]+|[\-]+)\s*(?:</strong>)?</td>"  # 4. 목표가
        r"\s*<td[^>]*>\s*(?:<strong[^>]*>)?\s*([^<]+?)\s*(?:</strong>)?</td>"  # 5. 투자의견
        r"\s*<td[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</td>",              # 6. 발행일
        re.DOTALL,
    )

    today = date.today()
    cutoff = today - timedelta(days=days_back)
    rows: list[dict] = []
    for m in row_re.finditer(html):
        try:
            target_raw = m.group(4).strip().replace(",", "").replace("원", "")
            target_raw = re.sub(r"[^\d.-]", "", target_raw)
            if not target_raw or target_raw in ("-", ""):
                target_val = None
            else:
                target_val = float(target_raw)
        except Exception:
            target_val = None

        rating_raw = m.group(5).strip()
        date_str = m.group(6).strip()
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue

        rows.append({
            "title": m.group(1).strip()[:80],
            "broker": m.group(2).strip(),
            "analyst": m.group(3).strip(),
            "target": target_val,
            "rating": rating_raw,
            "date": date_str,
        })

    if not rows:
        # No coverage found — cache empty result to avoid re-fetch storms
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text("null")
        except Exception:
            pass
        log.info("hk_consensus: no recent reports for %s (%d-day window)", code, days_back)
        return None

    # Aggregate: average target, dominant rating
    target_vals = [r["target"] for r in rows if r["target"]]
    avg_target = sum(target_vals) / len(target_vals) if target_vals else None

    rating_counts: dict[str, int] = {}
    for r in rows:
        direction = _RATING_TO_DIRECTION.get(r["rating"], "")
        if direction:
            rating_counts[direction] = rating_counts.get(direction, 0) + 1
    dominant_rating = max(rating_counts, key=rating_counts.get) if rating_counts else ""

    # Distinct analyst count (broker + analyst 조합)
    distinct_analysts = len({(r["broker"], r["analyst"]) for r in rows})
    last_date = max(r["date"] for r in rows)

    result = {
        "target_price": avg_target,
        "rating": dominant_rating,
        "analyst_count": distinct_analysts,
        "last_report_date": last_date,
        "report_count": len(rows),
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        log.warning("hk_consensus: cache write failed for %s: %s", code, exc)

    return result


def format_hk_consensus_for_prompt(consensus: Optional[dict]) -> str:
    """Render hk consensus result as a prompt-ready directive. Empty
    string when consensus is None (no coverage) — caller can compose
    'KR mid-cap 분석가 커버리지 없음 (yfinance + FnGuide + 한경 모두 0건)'
    한 줄 separately."""
    if not consensus:
        return ""
    parts: list[str] = ["한경 컨센서스 (consensus.hankyung.com):"]
    if consensus.get("target_price"):
        parts.append(f"  • 평균 목표가: ₩{consensus['target_price']:,.0f}")
    if consensus.get("rating"):
        direction_label = {
            "buy": "매수 우세",
            "sell": "매도 우세",
            "hold": "보유 / 중립",
        }.get(consensus["rating"], consensus["rating"])
        parts.append(f"  • 평균 투자의견: {direction_label}")
    if consensus.get("analyst_count"):
        parts.append(f"  • 커버리지: {consensus['analyst_count']}명 / {consensus.get('report_count', '?')}건 리포트")
    if consensus.get("last_report_date"):
        parts.append(f"  • 최근 리포트: {consensus['last_report_date']}")
    return "\n".join(parts)
