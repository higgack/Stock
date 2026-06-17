"""대만(TWSE) 상한가·하한가 페이지 (사용자 2026-06-13 Phase 2).

market.html '🇹🇼 대만 업종 등락' 위젯의 '📈 상한가·하한가' 링크 → 서버 사이드
렌더(KR/US 자식 페이지 패턴). TWSE MI_INDEX 전종목에서 ±9.5%+(TW 한도 ±10%)
필터. 무료·무키·graceful. CSS/테마는 naver_pages 재사용.
"""
from __future__ import annotations

import html as _html
import logging

from bot.live_refresh import LIVE_REFRESH_JS as _LIVE_REFRESH_JS
from bot.naver_pages import _CSS, _THEME_SCRIPT, _fmt_vol, _pct_cell

log = logging.getLogger("bot.tw_pages")

# 시장별 자식 대시보드 nav — 모두 상호 연결 (사용자 2026-06-13 '캡쳐처럼').
# (href, label). KR 은 naver _shell 과 동일 셋(kr52 가 _tw_shell 렌더라 여기 포함).
_MARKET_NAV = {
    "KR": [("theme", "🏭 업종별 시세(전체)"), ("kr52", "📈 신고가·신저가"),
           ("highlow", "🚀 급등·급락"), ("krprepost", "🌙 NXT 급등·급락"),
           ("nxt", "📊 NXT 수급")],   # 시간외=가격 Top30(신설), NXT=외국인·기관 수급
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


def _asia_back(market: str) -> tuple[str, str]:
    """ASIA 자식(JP/CN/HK/TW)의 '홈으로'는 메인 홈이 아니라 ASIA 대시보드로
    (사용자 2026-06-15 '아시아 자식에서 홈으로 누르면 아시아 대시보드로'). KR 은
    홈(market.html) — KR 은 홈에 그대로 있으므로. → (href, label)."""
    return (("market.html", "← 홈으로") if (market or "").upper().startswith("KR")
            else ("asia.html", "← ASIA"))


def _tw_shell(title: str, sub: str, body: str, nav: str = "",
              back: tuple[str, str] = ("market.html", "← 홈으로")) -> str:
    _bh, _bl = back
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(title)} — NOAH</title>{_CSS}</head><body>
<a class="back-link" href="{_bh}">{_html.escape(_bl)}</a>
<h1>{_html.escape(title)}</h1>
<div class="sub">{sub}</div>
{nav}
<div id="live-root">
{body}
</div>
{_THEME_SCRIPT}
{_LIVE_REFRESH_JS}
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
                                        sort_by_pct, stock_panel)
        up = sort_by_pct(enrich_for_panel(up, "TW", want_ind=True, want_name=True),
                         gainers=True)        # 기본 등락률순(사용자 2026-06-15)
        down = sort_by_pct(enrich_for_panel(down, "TW", want_ind=True, want_name=True),
                           gainers=False)
        body = ('<div class="grid">'
                + stock_panel("🚀 가장 많이 오른 TOP 30", up, "mv-up", "TW",
                              show_vol=True, show_value=True, show_ind=True,
                              limit_pct=9.9)
                + stock_panel("📉 가장 많이 내린 TOP 30", down, "mv-down", "TW",
                              show_vol=True, show_value=True, show_ind=True,
                              limit_pct=9.9)
                + '</div>' + HL_SORT_JS)
    from bot.highlow_render import market_hours_label as _mhl
    sub = (f"上市(TWSE)+上櫃(TPEx) 전종목 당일 등락 상·하위 · {_mhl('TW')} · "
           f"등락률순·헤더 클릭 정렬 · 종목명=한글 · 🔺상한/🔻하한(±10%) · 업종·시총="
           f"yfinance · {(dt + ' 종가 기준 · ') if dt else ''}장중 1h·마감후 EOD 자동"
           f"{(' · 마지막 갱신 ' + ts) if ts else ''}")
    return _tw_shell("🇹🇼 대만 급등·급락", sub, body,
                     nav=_market_nav("TW", "twhighlow"), back=_asia_back("TW"))


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
        # 미국 포맷 통일 — 시총·업종·정렬·업종분포.
        from bot.highlow_render import (HL_SORT_JS, enrich_for_panel,
                                        ind_dist_line, sort_by_mcap, stock_panel)
        # 종목명 = **렌더 시점** 한국어 해소(사용자 2026-06-14 '이름만 바꾸면
        # 되지 왜 다시 다 하냐'). 옛 구조는 번역명을 스캔 캐시 rows 에 박아, 이름
        # 표기를 바꿀 때마다 캐시 버전 bump → 전종목 1년 재스캔이 필요했음.
        # enrich_for_panel(want_name) 이 렌더 때 TWSE 中文/yfinance longName →
        # 한국어(translate_titles_kr, 영구캐시)로 덮어쓰므로, 앞으로 TW 이름 정책
        # 변경 = 이 호출만 수정(재스캔 0). TW 전용 — TW 는 네이버 미지원이라 이름이
        # 별도 translate 경로(문서화된 data-source 예외). 무버 패널과 동일 패턴.
        hi = sort_by_mcap(enrich_for_panel(high, "TW", want_name=True))
        lo = sort_by_mcap(enrich_for_panel(low, "TW", want_name=True))
        # 거래량/거래대금(=종가×거래량) — yfinance 스캔이 vol 주면 표시(사용자
        # 2026-06-14). 없으면 숨김(빈 컬럼 방지).
        _hv = any(r.get("vol") for r in hi + lo)
        body = ('<div class="grid">'
                + stock_panel("📈 52주 신고가", hi, "hl-high",
                              "TW", ind_dist_line(hi), show_vol=_hv, show_value=_hv)
                + stock_panel("📉 52주 신저가", lo, "hl-low",
                              "TW", ind_dist_line(lo), show_vol=_hv, show_value=_hv)
                + '</div>' + HL_SORT_JS)
    from bot.highlow_render import market_hours_label as _mhl
    sub = (f"上市(TWSE)+上櫃(TPEx) 전종목 1년 일봉 · 당일 52주 신고가/신저가 갱신 · "
           f"{_mhl('TW')} · 시총순·헤더 클릭 정렬 · 장중 1h·마감후 EOD 자동"
           f"{(' · 마지막 갱신 ' + ts) if ts else ''}")
    return _tw_shell("🇹🇼 대만 52주 신고가·신저가", sub, body,
                     nav=_market_nav("TW", "tw52"), back=_asia_back("TW"))
