"""대만(TWSE) 상한가·하한가 페이지 (사용자 2026-06-13 Phase 2).

market.html '🇹🇼 대만 업종 등락' 위젯의 '📈 상한가·하한가' 링크 → 서버 사이드
렌더(KR/US 자식 페이지 패턴). TWSE MI_INDEX 전종목에서 ±9.5%+(TW 한도 ±10%)
필터. 무료·무키·graceful. CSS/테마는 naver_pages 재사용.
"""
from __future__ import annotations

import html as _html
import logging

from bot.naver_pages import _CSS, _THEME_SCRIPT, _pct_cell

log = logging.getLogger("bot.tw_pages")


def _tw_shell(title: str, sub: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(title)} — NOAH</title>{_CSS}</head><body>
<a class="back-link" href="market.html">← 홈으로</a>
<h1>{_html.escape(title)}</h1>
<div class="sub">{sub}</div>
{body}
{_THEME_SCRIPT}
</body></html>"""


def render_tw_highlow_page() -> str:
    """대만 상한가·하한가권 (TWSE 전종목 ±9.5%+). KR highlow 미러."""
    try:
        from bot.twse_client import fetch_tw_upper_lower
        data = fetch_tw_upper_lower()
    except Exception as exc:
        log.warning("tw upper/lower page fetch failed: %s", exc)
        data = {"upper": [], "lower": [], "ts": ""}
    ts = _html.escape(data.get("ts", ""))

    def _panel(title: str, items: list) -> str:
        if not items:
            return (f'<div class="panel"><h2>{title}</h2>'
                    '<div class="empty">해당 종목 없음</div></div>')
        rows = "".join(
            f'<tr><td class="rk">{i}</td>'
            f'<td class="nm"><a href="lookup/{_html.escape(str(it.get("code","")))}.TW">'
            f'{_html.escape(it.get("name","") or it.get("code",""))}</a></td>'
            f'<td class="num">{_html.escape(str(it.get("close") or "—"))}</td>'
            f'{_pct_cell(it.get("pct"))}</tr>'
            for i, it in enumerate(items, 1))
        return (f'<div class="panel"><h2>{title} <span class="ts">{len(items)}종목</span></h2>'
                f'<table><thead><tr><th>#</th><th>종목</th>'
                f'<th style="text-align:right">종가</th>'
                f'<th style="text-align:right">등락률</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>')

    dt = _html.escape(data.get("date", ""))
    up, low = data.get("upper", []), data.get("lower", [])
    if not up and not low:
        body = ('<div class="empty">상한가·하한가 데이터를 불러올 수 없습니다.<br>'
                '(장 시간/휴장 또는 TWSE 응답 지연 — 잠시 후 다시 시도.)</div>')
    else:
        body = ('<div class="grid">'
                + _panel("🔺 상한가 (+9.9%↑)", up)
                + _panel("🔻 하한가 (-9.9%↓)", low) + '</div>')
    # 자료 기준일(거래일) 명시 — 주말/장후엔 직전 거래일. 'ts'는 갱신 시각.
    sub = (f"TWSE 전종목(일반종목) 중 일일 한도 ±10% 도달분. "
           f"{('<b>' + dt + ' 종가 기준</b>') if dt else ''} · 5분 캐시"
           f"{(' · 갱신 ' + ts) if ts else ''}")
    return _tw_shell("🇹🇼 대만 상한가·하한가", sub, body)
