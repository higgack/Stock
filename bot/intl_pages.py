"""JP/CN/HK 52주 신고가·신저가 페이지 (사용자 2026-06-13 '다른 나라도 모두').
TW 52w 페이지 일반화 — bot.intl_highlow 백그라운드 스캔 소비. CSS/shell 재사용.
"""
from __future__ import annotations

import html as _html
import logging

from bot.tw_pages import _market_nav, _tw_shell

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
        # KR(KIS): 종목명만·업종 제거·거래량 O(acml_vol). JP/CN/HK(yfinance):
        # 티커+한글명·업종 O·거래량 X(yfinance vol 미populate, 사용자 2026-06-13).
        _kr = market == "KR"
        _opt = dict(name_only=_kr, show_ind=not _kr, show_vol=_kr)
        _dist = (lambda x: "") if _kr else ind_dist_line   # KR 업종분포도 제거
        # 당일 52주 고가/저가 '갱신'한 진짜 신고가/신저가 (사용자 2026-06-13).
        body = ('<div class="grid">'
                + stock_panel("📈 52주 신고가", hi, "hl-high", market, _dist(hi), **_opt)
                + stock_panel("📉 52주 신저가", lo, "hl-low", market, _dist(lo), **_opt)
                + '</div>' + HL_SORT_JS)
    src = _html.escape(data.get("source") or "전종목 1년 일봉")
    sub = (f"{flag} {src} · **당일 52주 신고가/신저가 갱신** · 시총순·헤더 클릭 "
           f"정렬 · 업종=yfinance · 장중 3h 갱신·장 마감 후 직전 종가 고정(재스캔 0). "
           f"{('· 갱신 ' + ts) if ts else ''}")
    _active = {"KR": "kr52", "JP": "jp52", "CN_A": "cn52", "HK": "hk52"}.get(market, "")
    return _tw_shell(f"{flag} 52주 신고가·신저가", sub, body,
                     nav=_market_nav(market, _active))


def render_intl_movers_page(market: str) -> str:
    """HK 급등·급락 TOP — 미국 급등급락 미러(무제한 시장이라 상한가/하한가 대신).
    거래량 없음(yfinance vol 미populate), 티커(한글명)·시총·업종 + 업종분포."""
    try:
        from bot.intl_movers import fetch_intl_movers
        data = fetch_intl_movers(market)
    except Exception as exc:
        log.warning("intl movers page (%s): %s", market, exc)
        data = {"up": [], "down": [], "ts": "", "building": False, "status": {}}
    flag = _FLAG.get(market, market)
    ts = _html.escape(data.get("ts", ""))
    up, down = data.get("up", []), data.get("down", [])
    if not up and not down:
        if data.get("building"):
            st = data.get("status") or {}
            tot = st.get("total")
            prog = f" (전종목 {tot})" if tot else ""
            body = ('<div class="empty">⏳ 급등·급락 산출 중'
                    f'{_html.escape(prog)}…<br>전종목 일봉 스캔(수 분). '
                    '잠시 후 새로고침해 주세요.</div>')
        else:
            body = ('<div class="empty">급등·급락 데이터를 불러올 수 없습니다.<br>'
                    '(잠시 후 다시 시도해 주세요.)</div>')
    else:
        from bot.highlow_render import HL_SORT_JS, ind_dist_line, stock_panel
        body = ('<div class="grid">'
                + stock_panel("🚀 가장 많이 오른 TOP 30", up, "mv-up", market,
                              ind_dist_line(up), show_vol=False)
                + stock_panel("📉 가장 많이 내린 TOP 30", down, "mv-down", market,
                              ind_dist_line(down), show_vol=False)
                + '</div>' + HL_SORT_JS)
    sc = data.get("scanned")
    sub = (f"{flag} 당일 등락률 상·하위 30 (가격제한 없는 시장 — 급등/급락) · "
           + (f"{sc}종목 스캔 · " if sc else "")
           + "종목명=티커(한글) · 헤더 클릭 정렬 · 장중 30분 갱신·장 마감 후 고정(재스캔 0). "
           + (f"· 갱신 {ts}" if ts else ""))
    return _tw_shell(f"{flag} 급등·급락", sub, body,
                     nav=_market_nav(market, "hkmovers"))


def render_jp_stop_page() -> str:
    """일본 상한가·하한가(ストップ高/安) — TSE 制限値幅 도달 종목(전종목 스캔).
    KR/TW 상한가 미러: 티커(한글명)·현재가·등락률·시총·업종(거래량은 yfinance
    미populate라 생략). 결정적(공개 제한폭 표)."""
    try:
        from bot.jp_stop import fetch_jp_stop
        data = fetch_jp_stop()
    except Exception as exc:
        log.warning("jp_stop page: %s", exc)
        data = {"upper": [], "lower": [], "ts": "", "building": False, "status": {}}
    ts = _html.escape(data.get("ts", ""))
    up, low = data.get("upper", []), data.get("lower", [])
    if not up and not low:
        if data.get("building"):
            st = data.get("status") or {}
            tot = st.get("total")
            prog = f" (전종목 {tot})" if tot else ""
            body = ('<div class="empty">⏳ 상한가·하한가 산출 중'
                    f'{_html.escape(prog)}…<br>전종목 일봉 vs 制限値幅 스캔(수 분). '
                    '잠시 후 새로고침해 주세요.</div>')
        else:
            body = ('<div class="empty">상한가·하한가 데이터를 불러올 수 없습니다.<br>'
                    '(가격제한 도달 종목이 없거나 잠시 후 재시도.)</div>')
    else:
        from bot.highlow_render import (HL_SORT_JS, ind_dist_line, sort_by_mcap,
                                        stock_panel)
        up, low = sort_by_mcap(up), sort_by_mcap(low)
        body = ('<div class="grid">'
                + stock_panel("🔺 상한가 (ストップ高)", up, "ul-up", "JP",
                              ind_dist_line(up), show_vol=False)
                + stock_panel("🔻 하한가 (ストップ安)", low, "ul-low", "JP",
                              ind_dist_line(low), show_vol=False)
                + '</div>' + HL_SORT_JS)
    sc = data.get("scanned")
    sub = ("🇯🇵 일본 상한가·하한가(ストップ高/安) — 전일종가별 TSE 制限値幅 도달 · "
           + (f"{sc}종목 스캔 · " if sc else "")
           + "종목명=티커(한글) · 시총순·헤더 클릭 정렬 · 장중 3h 갱신·장 마감 후 고정(재스캔 0). "
           + (f"· 갱신 {ts}" if ts else ""))
    return _tw_shell("🇯🇵 일본 상한가·하한가", sub, body,
                     nav=_market_nav("JP", "jphighlow"))
