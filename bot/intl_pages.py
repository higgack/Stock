"""JP/CN/HK 52주 신고가·신저가 페이지 (사용자 2026-06-13 '다른 나라도 모두').
TW 52w 페이지 일반화 — bot.intl_highlow 백그라운드 스캔 소비. CSS/shell 재사용.
"""
from __future__ import annotations

import html as _html
import logging

from bot.tw_pages import _asia_back, _market_nav, _tw_shell

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
    from bot.highlow_render import clean_source as _clean_src, market_hours_label
    src = _html.escape(_clean_src(data.get("source") or "전종목 1년 일봉"))
    # JP/HK 52w 커버리지 = env HIGHLOW_UNIVERSE_CAP(기본 5000=사실상 전종목, 사용자
    # 2026-06-16 '전시장 다'). 기본값이면 full 이라 '전종목' 정확(옛 ~900 캡 라벨
    # 제거). env 로 캡 낮추면 시총 상위만 — 그 땐 라벨이 약간 낙관적.
    # KR=네이버(업종 그룹 멤버맵 백필), JP/CN/HK=yfinance baseline × 네이버 현재가.
    # 신선도: KR 장중 30초 · JP/CN_A/HK 장중 10분(네이버 실시간 baseline 비교, #661)
    # + **장 마감 후 EOD 자동 재산출**(페이지 무관). 이 함수는 KR/JP/CN_A/HK 만 받음
    # (TW 는 tw_pages 별도 — 1h 스캔). 옛 '장중 1h' 라벨은 #661 이후 stale 였음.
    _fresh_lbl = ("장중 30초" if market == "KR"
                  else "장중 10분(네이버 실시간)·마감후 EOD 자동")
    if market == "KR":
        _ind_lbl = "업종=네이버 · "
    elif market in ("HK", "JP", "CN_A"):
        _ind_lbl = "거래량·거래대금·시총·종목명=네이버 · 업종=네이버+yfinance · "
    else:
        _ind_lbl = "업종=yfinance · "
    _hrs = market_hours_label(market)
    # CN_A 는 CSI 300+500(대형+중형 ~800, yfinance 커버 양호) — 사용자 2026-06-17.
    # 전 A주 소형주는 yfinance 1년 커버 빈약해 제외. AKShare 미설치/실패 시 peer 폴백.
    _scope = " · CSI300+500(대형·중형)" if market == "CN_A" else ""
    # 국가명 중복 제거: 페이지 제목이 이미 flag(예 '🇯🇵 일본')를 보여주고 src 도
    # 유니버스 라벨('일본 주요종목'…)에 국가명을 포함 → 부제에 flag 를 또 붙이면
    # '🇯🇵 일본 일본 주요종목' 처럼 중복. 부제는 src 부터 시작(제목이 flag 담당).
    sub = (f"{src}{_scope} · {(_hrs + ' · ') if _hrs else ''}"
           f"{_ind_lbl}{_fresh_lbl}{(' · 마지막 갱신 ' + ts) if ts else ''}")
    _active = {"KR": "kr52", "JP": "jp52", "HK": "hk52",
               "CN_A": "cn52"}.get(market, "")
    return _tw_shell(f"{flag} 52주 신고가·신저가", sub, body,
                     nav=_market_nav(market, _active), back=_asia_back(market))


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
        from bot.highlow_render import (HL_SORT_JS, ind_dist_line, sort_by_pct,
                                        stock_panel)
        # 네이버 무버: 거래량·거래대금·시총 native + yfinance 업종(야후방식).
        # 기본 등락률순(사용자 2026-06-15, 헤더로 시총 재정렬).
        _o = dict(show_vol=True, show_value=True, show_ind=True)
        up, down = sort_by_pct(up, gainers=True), sort_by_pct(down, gainers=False)
        body = ('<div class="grid">'
                + stock_panel("🚀 가장 많이 오른 TOP 30", up, "mv-up", market,
                              ind_dist_line(up), **_o)
                + stock_panel("📉 가장 많이 내린 TOP 30", down, "mv-down", market,
                              ind_dist_line(down), **_o)
                + '</div>' + HL_SORT_JS)
    from bot.highlow_render import clean_source as _clean_src
    src = _html.escape(_clean_src(data.get("source") or f"{flag} 당일 등락"))
    # 부제 간결화 (사용자 2026-06-14 '쓸데없는건 빼고').
    # 업종 소스 정직 표기 — CN/JP=네이버 우선+야후 폴백, HK=야후 우선+네이버 폴백.
    _ind_src = ("업종=네이버+yfinance" if market in ("CN_A", "JP")
                else "업종=yfinance+네이버" if market == "HK" else "업종=네이버")
    from bot.highlow_render import market_hours_label as _mhl
    from bot.highlow_render import movers_freshness as _mf
    _hrs = _mhl(market)
    sub = (f"{flag} 당일 등락 상·하위 30 · {src} · {(_hrs + ' · ') if _hrs else ''}"
           f"{_ind_src} · {_mf(market)}"
           + (f" · 마지막 갱신 {ts}" if ts else ""))
    _active = {"JP": "jpmovers", "CN_A": "cnmovers", "HK": "hkmovers"}.get(market, "hkmovers")
    return _tw_shell(f"{flag} 급등·급락", sub, body,
                     nav=_market_nav(market, _active), back=_asia_back(market))


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
           + "종목명=티커(한글) · 장중 1h 갱신·장 마감 후 고정(재스캔 0). "
           + (f"· 갱신 {ts}" if ts else ""))
    return _tw_shell("🇯🇵 일본 상한가·하한가", sub, body,
                     nav=_market_nav("JP", "jphighlow"), back=_asia_back("JP"))


def render_kr_prepost_page() -> str:
    """KR NXT(넥스트레이드) 장전·장후 급등·급락 TOP30 — 네이버 overMarketPriceInfo
    (= NXT After-Market, 네이버페이 표기) 가격 등락(정규장 종가 대비, 사용자
    2026-06-16 'NXT≠시간외, NXT로 정정'). 미국 '🌙 장전·장후' 보드의 KR 버전. NXT
    수급 보드(외국인·기관 흐름)와 별개 — 이건 'NXT 가격'. 정규장 무버 유니버스를
    종목별 네이버 NXT 가로 스캔."""
    try:
        from bot.prepost_client import fetch_kr_prepost_movers, kr_prepost_status
        data = fetch_kr_prepost_movers()
        st = kr_prepost_status()
    except Exception as exc:
        log.warning("kr prepost page: %s", exc)
        data = {"up": [], "down": [], "ts": "", "building": False,
                "status": {}, "session": ""}
        st = {}
    ts = _html.escape(data.get("ts", ""))
    up, down = data.get("up", []), data.get("down", [])
    sess = data.get("session") or ""
    sess_kr = "장전" if sess == "pre" else "장후" if sess == "post" else "장전·장후"
    if not up and not down:
        if data.get("building"):
            body = ('<div class="empty">⏳ NXT 급등·급락 산출 중…<br>'
                    '네이버 NXT(넥스트레이드) 스캔 중. 잠시 후 새로고침해 주세요.</div>')
        else:
            body = ('<div class="empty">NXT 급등·급락 데이터가 없습니다.<br>'
                    'NXT 장전(08:00–09:00) · 장후(15:40–20:00) KST 에 '
                    '확인해 주세요.</div>')
    else:
        from bot.highlow_render import HL_SORT_JS, sort_by_pct, stock_panel
        _o = dict(show_vol=True, show_value=True, show_ind=False)  # 가격·거래량·거래대금·시총
        up, down = sort_by_pct(up, gainers=True), sort_by_pct(down, gainers=False)
        body = ('<div class="grid">'
                + stock_panel(f"🚀 {sess_kr} 가장 많이 오른 TOP 30", up,
                              "kpp-up", "KR", "", **_o)
                + stock_panel(f"📉 {sess_kr} 가장 많이 내린 TOP 30", down,
                              "kpp-down", "KR", "", **_o)
                + '</div>' + HL_SORT_JS)
    # 스캔 상태 배너 — 직전 성공 스냅샷을 서빙 중인데 최근 집계가 실패/진행이면
    # 사용자가 화면에서 '왜 오늘 장전이 안 보이는지' 즉시 인지(silent-fail 제거,
    # 실수 #12). state=done(최신 반영)이면 배너 없음 — 스냅샷이 곧 최신.
    _state = (st or {}).get("state")
    _stl = _html.escape(str((st or {}).get("ts_label") or ""))
    if _state == "failed":
        _detail = _html.escape(str((st or {}).get("detail") or ""))
        _scanned = (st or {}).get("scanned")
        _scan_txt = f" · 스캔 {_scanned}종목" if _scanned is not None else ""
        body = (
            '<div style="margin:10px 0;padding:10px 14px;border-radius:8px;'
            'background:rgba(248,81,73,.12);border:1px solid rgba(248,81,73,.45);'
            'font-size:13px;line-height:1.55">'
            f'⚠️ <b>최근 NXT 집계 실패</b> — {_stl}{_scan_txt} · 사유: {_detail}<br>'
            '아래는 직전 성공 스냅샷입니다. NXT 장전(08:00–09:00)·장후'
            '(15:40–20:00) KST 창에서 자동 재집계됩니다.</div>') + body
    elif _state == "running":
        body = (
            '<div style="margin:10px 0;padding:10px 14px;border-radius:8px;'
            'background:rgba(56,139,253,.12);border:1px solid rgba(56,139,253,.45);'
            'font-size:13px;line-height:1.55">'
            f'⏳ <b>NXT 집계 진행 중</b> — {_stl} · 잠시 후 새로고침하면 최신으로 '
            '갱신됩니다. 아래는 직전 스냅샷.</div>') + body
    sub = (f"🇰🇷 {sess_kr} NXT 급등·급락 상·하위 30 · 등락률=NXT가 vs 정규장 "
           "종가(NXT-정규장 격차) · 거래량·거래대금=NXT 세션 누적(당일 NXT 거래소 체결, "
           "정규장 별개) · 실제 NXT 체결 종목만(미체결=전일가 placeholder 제외) · 네이버 "
           "실시간 · NXT 장전 08:00–09:00 · 장후 15:40–20:00 KST · NXT 창에서 2분"
           + (f" · {ts} 기준" if ts else ""))
    return _tw_shell("🇰🇷 한국 NXT 장전·장후 급등·급락", sub, body,
                     nav=_market_nav("KR", "krprepost"), back=_asia_back("KR"))
