"""JP/CN/HK 52주 신고가·신저가 페이지 (사용자 2026-06-13 '다른 나라도 모두').
TW 52w 페이지 일반화 — bot.intl_highlow 백그라운드 스캔 소비. CSS/shell 재사용.
"""
from __future__ import annotations

import html as _html
import logging

from bot.naver_pages import _pct_cell
from bot.tw_pages import _tw_shell

log = logging.getLogger("bot.intl_pages")

_FLAG = {"JP": "🇯🇵 일본", "CN_A": "🇨🇳 중국 A주", "HK": "🇭🇰 홍콩"}


def _panel(title: str, items: list) -> str:
    if not items:
        return (f'<div class="panel"><h2>{title}</h2>'
                '<div class="empty">해당 종목 없음</div></div>')
    rows = "".join(
        f'<tr><td class="rk">{i}</td>'
        f'<td class="nm"><a href="lookup/{_html.escape(str(it.get("ticker","")))}">'
        f'{_html.escape(it.get("name","") or it.get("ticker",""))}</a></td>'
        f'<td class="num">{_html.escape(str(it.get("price") or "—"))}</td>'
        f'{_pct_cell(it.get("pct"))}</tr>'
        for i, it in enumerate(items, 1))
    return (f'<div class="panel"><h2>{title} <span class="ts">{len(items)}종목</span></h2>'
            f'<table><thead><tr><th>#</th><th>종목</th>'
            f'<th style="text-align:right">현재가</th>'
            f'<th style="text-align:right">등락률</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def render_intl_highlow52_page(market: str) -> str:
    """JP/CN_A/HK 52주 신고가·신저가 — 주요종목 유니버스 백그라운드 스캔."""
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
        body = ('<div class="grid">'
                + _panel("📈 52주 신고가 (1% 근접)", high)
                + _panel("📉 52주 신저가 (1% 근접)", low) + '</div>')
    sub = (f"{flag} 주요종목(산업 대표 ~50-100) 1년 주봉 52주 고저 1% 근접. "
           f"백그라운드 산출·30분 캐시. {('· 갱신 ' + ts) if ts else ''}")
    return _tw_shell(f"{flag} 52주 신고가·신저가", sub, body)
