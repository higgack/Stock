"""대만(TWSE) 상한가·하한가 페이지 (사용자 2026-06-13 Phase 2).

market.html '🇹🇼 대만 업종 등락' 위젯의 '📈 상한가·하한가' 링크 → 서버 사이드
렌더(KR/US 자식 페이지 패턴). TWSE MI_INDEX 전종목에서 ±9.5%+(TW 한도 ±10%)
필터. 무료·무키·graceful. CSS/테마는 naver_pages 재사용.
"""
from __future__ import annotations

import html as _html
import logging

from bot.naver_pages import _CSS, _THEME_SCRIPT, _fmt_vol, _pct_cell

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
            f'{_pct_cell(it.get("pct"))}'
            f'<td class="num">{_fmt_vol(it.get("vol"))}</td></tr>'
            for i, it in enumerate(items, 1))
        return (f'<div class="panel"><h2>{title} <span class="ts">{len(items)}종목</span></h2>'
                f'<table><thead><tr><th>#</th><th>종목</th>'
                f'<th style="text-align:right">종가</th>'
                f'<th style="text-align:right">등락률</th>'
                f'<th style="text-align:right">거래량</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>')

    dt = _html.escape(data.get("date", ""))
    up, low = data.get("upper", []), data.get("lower", [])
    if not up and not low:
        body = ('<div class="empty">상한가·하한가 데이터를 불러올 수 없습니다.<br>'
                '(장 시간/휴장 또는 TWSE 응답 지연 — 잠시 후 다시 시도.)</div>')
    else:
        body = ('<div class="grid">'
                + _panel("🔺 상한가권 (+9.5%↑)", up)
                + _panel("🔻 하한가권 (-9.5%↓)", low) + '</div>')
    # 자료 기준일(거래일) 명시 — 주말/장후엔 직전 거래일. 'ts'는 갱신 시각.
    sub = (f"TWSE 전종목(일반종목) 중 일일 한도 ±10% 근접(±9.5%↑). "
           f"{('<b>' + dt + ' 종가 기준</b>') if dt else ''} · 5분 캐시"
           f"{(' · 갱신 ' + ts) if ts else ''}")
    return _tw_shell("🇹🇼 대만 상한가·하한가", sub, body)


def render_tw_highlow52_page() -> str:
    """대만 52주 신고가·신저가 (yfinance TW 유니버스 백그라운드 스캔). 산출
    중이면 안내(US 신고저/급등급락 패턴)."""
    try:
        from bot.tw_highlow import fetch_tw_highlow
        data = fetch_tw_highlow()
    except Exception as exc:
        log.warning("tw 52w highlow page fetch failed: %s", exc)
        data = {"high": [], "low": [], "ts": "", "building": False, "status": {}}
    ts = _html.escape(data.get("ts", ""))
    high, low = data.get("high", []), data.get("low", [])

    if not high and not low:
        if data.get("building"):
            st = data.get("status") or {}
            tot = st.get("total")
            prog = f" (유니버스 {tot}종목)" if tot else ""
            body = ('<div class="empty">⏳ 52주 신고가·신저가 산출 중'
                    f'{_html.escape(prog)}…<br>전종목 1년 주봉 스캔(수 분). '
                    '잠시 후 새로고침해 주세요.</div>')
        else:
            body = ('<div class="empty">신고가·신저가 데이터를 불러올 수 없습니다.<br>'
                    '(잠시 후 다시 시도해 주세요.)</div>')
    else:
        # 미국 포맷 통일 — 시총·업종·정렬·업종분포. 거래량은 제거(yfinance vol
        # 미populate, 사용자 2026-06-13). 종목명=티커(한글명).
        from bot.highlow_render import (HL_SORT_JS, ind_dist_line, sort_by_mcap,
                                        stock_panel)
        hi, lo = sort_by_mcap(high), sort_by_mcap(low)
        body = ('<div class="grid">'
                + stock_panel("📈 52주 신고가 (1% 근접)", hi, "hl-high",
                              "TW", ind_dist_line(hi), show_vol=False)
                + stock_panel("📉 52주 신저가 (1% 근접)", lo, "hl-low",
                              "TW", ind_dist_line(lo), show_vol=False)
                + '</div>' + HL_SORT_JS)
    sub = (f"TWSE 전종목(일반종목) 1년 주봉 52주 고저 1% 근접. 백그라운드 산출·"
           f"30분 캐시. {('· 갱신 ' + ts) if ts else ''}")
    return _tw_shell("🇹🇼 대만 52주 신고가·신저가", sub, body)
