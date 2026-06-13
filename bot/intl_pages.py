"""JP/CN/HK 52주 신고가·신저가 페이지 (사용자 2026-06-13 '다른 나라도 모두').
TW 52w 페이지 일반화 — bot.intl_highlow 백그라운드 스캔 소비. CSS/shell 재사용.
"""
from __future__ import annotations

import html as _html
import logging

from bot.tw_pages import _tw_shell

log = logging.getLogger("bot.intl_pages")

_FLAG = {"JP": "🇯🇵 일본", "CN_A": "🇨🇳 중국 A주", "HK": "🇭🇰 홍콩",
         "KR": "🇰🇷 한국"}


def render_intl_highlow52_page(market: str) -> str:
    """JP/CN_A/HK/KR 52주 신고가·신저가 — 미국 포맷 통일(사용자 2026-06-13):
    종목(+이름)·현재가·등락률·거래량·시총·업종 + 시총정렬 + 헤더정렬 + 업종분포."""
    try:
        from bot.intl_highlow import fetch_intl_highlow
        data = fetch_intl_highlow(market)
    except Exception as exc:
        log.warning("intl 52w page fetch failed (%s): %s", market, exc)
        data = {"high": [], "low": [], "ts": "", "building": False, "status": {}}
    flag = _FLAG.get(market, market)
    ts = _html.escape(data.get("ts", ""))
    high, low = data.get("high", []), data.get("low", [])
    if not high and not low:
        if data.get("building"):
            st = data.get("status") or {}
            tot = st.get("total")
            prog = f" (주요종목 {tot})" if tot else ""
            body = ('<div class="empty">⏳ 52주 신고가·신저가 산출 중'
                    f'{_html.escape(prog)}…<br>주요종목 1년 주봉 스캔. '
                    '잠시 후 새로고침해 주세요.</div>')
        else:
            body = ('<div class="empty">신고가·신저가 데이터를 불러올 수 없습니다.<br>'
                    '(잠시 후 다시 시도해 주세요.)</div>')
    else:
        from bot.highlow_render import (HL_SORT_JS, ind_dist_line, sort_by_mcap,
                                        stock_panel)
        hi, lo = sort_by_mcap(high), sort_by_mcap(low)
        body = ('<div class="grid">'
                + stock_panel("📈 52주 신고가 (1% 근접)", hi, "hl-high",
                              market, ind_dist_line(hi))
                + stock_panel("📉 52주 신저가 (1% 근접)", lo, "hl-low",
                              market, ind_dist_line(lo))
                + '</div>' + HL_SORT_JS)
    src = _html.escape(data.get("source") or "주요종목(산업 대표 ~50-100) 1년 주봉")
    sub = (f"{flag} {src} · 52주 고저 근접 · 시총순·헤더 클릭 정렬 · "
           f"업종=yfinance · 백그라운드 산출·30분 캐시. "
           f"{('· 갱신 ' + ts) if ts else ''}")
    return _tw_shell(f"{flag} 52주 신고가·신저가", sub, body)
