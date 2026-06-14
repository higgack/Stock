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

# 시장별 자식 대시보드 nav — 모두 상호 연결 (사용자 2026-06-13 '캡쳐처럼').
# (href, label). KR 은 naver _shell 과 동일 셋(kr52 가 _tw_shell 렌더라 여기 포함).
_MARKET_NAV = {
    "KR": [("theme", "🏭 업종별 시세(전체)"), ("kr52", "📈 신고가·신저가"),
           ("highlow", "🚀 급등·급락"), ("nxt", "🌙 NXT 장전·장후")],
    "TW": [("tw52", "📈 신고가·신저가"), ("twhighlow", "🚀 급등·급락")],
    "JP": [("jp52", "📈 신고가·신저가"), ("jpmovers", "🚀 급등·급락")],
    "HK": [("hk52", "📈 신고가·신저가"), ("hkmovers", "🚀 급등·급락")],
    "CN_A": [("cnmovers", "🚀 급등·급락")],   # 52주 제거(사용자 2026-06-14)
}


def _market_nav(market: str, active: str) -> str:
    """시장 자식 페이지 toggle nav (KR naver 패턴 미러). active 탭 강조."""
    tabs = _MARKET_NAV.get(market, [])
    if not tabs:
        return ""
    out = []
    for href, label in tabs:
        cls = ' class="active"' if href == active else ""
        out.append(f'<a{cls} href="{href}">{label}</a>')
    return '<div class="toggle">' + "".join(out) + "</div>"


def _tw_shell(title: str, sub: str, body: str, nav: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(title)} — NOAH</title>{_CSS}</head><body>
<a class="back-link" href="market.html">← 홈으로</a>
<h1>{_html.escape(title)}</h1>
<div class="sub">{sub}</div>
{nav}
{body}
{_THEME_SCRIPT}
</body></html>"""


def render_tw_highlow_page() -> str:
    """대만 급등·급락 (TWSE 전종목 등락 상·하위, 사용자 2026-06-14 'TW 상한가/하한가
    → 급등/급락', JP/CN/HK 무버 형태)."""
    try:
        from bot.twse_client import fetch_tw_movers
        data = fetch_tw_movers()
    except Exception as exc:
        log.warning("tw movers page fetch failed: %s", exc)
        data = {"up": [], "down": [], "ts": ""}
    ts = _html.escape(data.get("ts", ""))

    def _prep(lst):
        # TWSE 항목 → stock_panel 형식. ticker=code.TW, price=close, 거래량·거래대금.
        return [{"ticker": f"{it.get('code', '')}.TW",
                 "name": it.get("name", "") or it.get("code", ""),
                 "price": it.get("close"), "pct": it.get("pct"),
                 "vol": it.get("vol"), "value": it.get("value")} for it in lst]

    dt = _html.escape(data.get("date", ""))
    up, down = _prep(data.get("up", [])), _prep(data.get("down", []))
    if not up and not down:
        body = ('<div class="empty">급등·급락 데이터를 불러올 수 없습니다.<br>'
                '(장 시간/휴장 또는 TWSE 응답 지연 — 잠시 후 다시 시도.)</div>')
    else:
        # 종목명=영문 + 업종 + 시총 + 거래량/거래대금(TWSE). enrich=mcap/업종/영문명.
        from bot.highlow_render import (HL_SORT_JS, enrich_for_panel,
                                        sort_by_mcap, stock_panel)
        up = sort_by_mcap(enrich_for_panel(up, "TW", want_ind=True, want_name=True))
        down = sort_by_mcap(enrich_for_panel(down, "TW", want_ind=True, want_name=True))
        body = ('<div class="grid">'
                + stock_panel("🚀 가장 많이 오른 TOP 30", up, "mv-up", "TW",
                              show_vol=True, show_value=True, show_ind=True)
                + stock_panel("📉 가장 많이 내린 TOP 30", down, "mv-down", "TW",
                              show_vol=True, show_value=True, show_ind=True)
                + '</div>' + HL_SORT_JS)
    sub = (f"TWSE 전종목 당일 등락 상·하위 · 시총순·헤더 클릭 정렬 · 종목명=영문 · "
           f"업종·시총=yfinance · {(dt + ' 종가 기준 · ') if dt else ''}장중 30분"
           f"{(' · ' + ts) if ts else ''}")
    return _tw_shell("🇹🇼 대만 급등·급락", sub, body,
                     nav=_market_nav("TW", "twhighlow"))


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
        # 거래량/거래대금(=종가×거래량) — yfinance 스캔이 vol 주면 표시(사용자
        # 2026-06-14). 없으면 숨김(빈 컬럼 방지).
        _hv = any(r.get("vol") for r in hi + lo)
        body = ('<div class="grid">'
                + stock_panel("📈 52주 신고가", hi, "hl-high",
                              "TW", ind_dist_line(hi), show_vol=_hv, show_value=_hv)
                + stock_panel("📉 52주 신저가", lo, "hl-low",
                              "TW", ind_dist_line(lo), show_vol=_hv, show_value=_hv)
                + '</div>' + HL_SORT_JS)
    sub = (f"TWSE 전종목 1년 일봉 · 당일 52주 신고가/신저가 갱신 · 시총순·헤더 클릭 정렬 · "
           f"장중 1h{(' · ' + ts + ' 기준') if ts else ''}")
    return _tw_shell("🇹🇼 대만 52주 신고가·신저가", sub, body,
                     nav=_market_nav("TW", "tw52"))
