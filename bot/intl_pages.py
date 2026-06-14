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
        # JP 시총 100억엔↑만 (사용자 2026-06-14 '일본 100억엔 이상'). 네이버 overlay
        # 가 mcap 채운 뒤 필터.
        if market == "JP":
            from bot.highlow_render import filter_min_mcap
            hi, lo = filter_min_mcap(hi, 100), filter_min_mcap(lo, 100)
        # KR: 종목명만(한글)·거래량 O(네이버). JP/CN/HK(yfinance): 티커+한글명·
        # 거래량 X(yfinance vol 미populate, 사용자 2026-06-13). 업종은 전 시장 표시.
        _kr = market == "KR"
        if _kr:
            # KR 업종(한글) 백필 — 네이버 업종 그룹 멤버맵 (사용자 2026-06-14
            # 'KR 신고가에 업종 추가, 그냥 한글로'). SWR·graceful(빌드 중이면 —).
            try:
                from bot.naver_sector_client import apply_kr_industry
                apply_kr_industry(hi)
                apply_kr_industry(lo)
            except Exception:
                pass
        # KR(네이버)=거래량+거래대금 항상. JP/CN/HK=yfinance 스캔이 거래량 주면
        # 표시(거래대금≈종가×거래량, 사용자 2026-06-14 '모두 거래량/거래대금').
        # vol 부재 시 숨김(빈 컬럼 방지) — 네이버는 52주 정렬 미지원이라 야후 검출.
        _has_vol = any(r.get("vol") for r in hi + lo)
        _show = _kr or _has_vol
        # 업종은 KR 포함 전 시장 표시(KR=네이버 업종맵, 나머지=yfinance).
        _opt = dict(name_only=_kr, show_ind=True, show_vol=_show, show_value=_show)
        _dist = ind_dist_line
        # 당일 52주 고가/저가 '갱신'한 진짜 신고가/신저가 (사용자 2026-06-13).
        body = ('<div class="grid">'
                + stock_panel("📈 52주 신고가", hi, "hl-high", market, _dist(hi), **_opt)
                + stock_panel("📉 52주 신저가", lo, "hl-low", market, _dist(lo), **_opt)
                + '</div>' + HL_SORT_JS)
    from bot.highlow_render import clean_source as _clean_src
    src = _html.escape(_clean_src(data.get("source") or "전종목 1년 일봉"))
    # KR=네이버(업종 그룹 멤버맵 백필), JP/CN/HK=yfinance — 신선도는 전 시장 장중
    # 1h 통일(사용자 2026-06-13 '모두 장중에만 1h'). 부제 군더더기 제거 — 출처·정렬·갱신만.
    if market == "KR":
        _ind_lbl = "업종=네이버 · "
    elif market == "HK":   # 사용자 2026-06-14 'HK 거래량·시총 네이버·업종 야후'
        _ind_lbl = "거래량·거래대금·시총·종목명=네이버 · 업종=yfinance · "
    elif market == "JP":   # 사용자 2026-06-14 'JP 시총 네이버·100억엔↑'
        _ind_lbl = "시총 100억엔↑ · 거래량·거래대금·시총·종목명=네이버 · "
    else:
        _ind_lbl = "업종=yfinance · "
    sub = (f"{flag} {src} · 시총순·헤더 클릭 정렬 · {_ind_lbl}장중 1h"
           f"{(' · ' + ts + ' 기준') if ts else ''}")
    _active = {"KR": "kr52", "JP": "jp52", "CN_A": "cn52", "HK": "hk52"}.get(market, "")
    return _tw_shell(f"{flag} 52주 신고가·신저가", sub, body,
                     nav=_market_nav(market, _active))


def render_intl_movers_page(market: str) -> str:
    """JP/CN/HK 급등·급락 TOP — 미국 급등급락 미러(사용자 2026-06-13 '중국·홍콩·
    일본은 미국따라'). 네이버 worldstock(한글명·현재가·거래량·거래대금·시총) +
    yfinance 업종/업종분포(야후방식). 상한가/하한가 있는 시장도 상승/하락 TOP 로."""
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
            body = ('<div class="empty">⏳ 급등·급락 산출 중…<br>'
                    '네이버 등락 랭킹 수집 중. 잠시 후 새로고침해 주세요.</div>')
        else:
            body = ('<div class="empty">급등·급락 데이터를 불러올 수 없습니다.<br>'
                    '(잠시 후 다시 시도해 주세요.)</div>')
    else:
        from bot.highlow_render import (HL_SORT_JS, ind_dist_line, sort_by_mcap,
                                        stock_panel)
        # 네이버 무버: 거래량·거래대금·시총 native + yfinance 업종(야후방식).
        # 모든 자식 기본 시총순(사용자 2026-06-14, 헤더로 등락률 재정렬).
        _o = dict(show_vol=True, show_value=True, show_ind=True)
        up, down = sort_by_mcap(up), sort_by_mcap(down)
        # JP 시총 100억엔↑만 (사용자 2026-06-14 '일본 100억엔 이상').
        if market == "JP":
            from bot.highlow_render import filter_min_mcap
            up, down = filter_min_mcap(up, 100), filter_min_mcap(down, 100)
        body = ('<div class="grid">'
                + stock_panel("🚀 가장 많이 오른 TOP 30", up, "mv-up", market,
                              ind_dist_line(up), **_o)
                + stock_panel("📉 가장 많이 내린 TOP 30", down, "mv-down", market,
                              ind_dist_line(down), **_o)
                + '</div>' + HL_SORT_JS)
    from bot.highlow_render import clean_source as _clean_src
    src = _html.escape(_clean_src(data.get("source") or f"{flag} 당일 등락"))
    # 부제 간결화 (사용자 2026-06-14 '쓸데없는건 빼고').
    _mc_note = "시총 100억엔↑ · " if market == "JP" else ""   # 사용자 2026-06-14
    _ind_src = "업종=네이버+yfinance" if market == "CN_A" else "업종=네이버"  # CN 이중
    sub = (f"{flag} 당일 등락 상·하위 30 · {_mc_note}{src} · 시총순·헤더 클릭 정렬 · "
           f"{_ind_src} · 장중 30분" + (f" · {ts} 기준" if ts else ""))
    _active = {"JP": "jpmovers", "CN_A": "cnmovers", "HK": "hkmovers"}.get(market, "hkmovers")
    return _tw_shell(f"{flag} 급등·급락", sub, body,
                     nav=_market_nav(market, _active))


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
           + "종목명=티커(한글) · 시총순·헤더 클릭 정렬 · 장중 1h 갱신·장 마감 후 고정(재스캔 0). "
           + (f"· 갱신 {ts}" if ts else ""))
    return _tw_shell("🇯🇵 일본 상한가·하한가", sub, body,
                     nav=_market_nav("JP", "jphighlow"))
