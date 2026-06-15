"""Market overview data collection — yfinance batch + FRED + Finnhub.

Provides data for the market.html landing page:
  - Global market snapshot (indices, FX, commodities, crypto, FRED indicators)
  - Upcoming earnings calendar (Finnhub)
  - Recent KR research actions (한경 컨센서스 list page)

All free, LLM 0, ₩0.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests
import yfinance as yf

log = logging.getLogger("bot.market_overview")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "market_overview"
# 스냅샷 — 지수·환율·원자재·코인·KR 개별종목 **전부 네이버**(사용자 2026-06-15
# '홈 야후 완전 제거') → _all_yf_tickers 보통 0, yf 경로 무동작. 라이브 fast_info
# throttle(_LIVE_TTL)·YF_PAUSE 는 야후 잔존 시 대비로만 유지. Finviz 업종 TTL 은
# 각 클라이언트 별도(안티봇 경계 — 건드리지 말 것).
_CACHE_TTL_SEC = 30  # 30초 (사용자 2026-06-15 '네이버 다 실시간')

# fast_info(라이브 quote API) throttle — 야후가 fast_info 만 쉽게 rate-limit.
# 홈 스냅샷은 이제 지수·환율·원자재·코인·KR 개별종목 **전부 네이버**(사용자
# 2026-06-15) → fast_info 호출 사실상 0(_all_yf_tickers 보통 빈 리스트). _LIVE_TTL
# 은 야후 잔존(미래 추가 종목 등) 대비 방어적 throttle 로만 유지 — 야후 rate-limit
# 시 daily 폴백.
_LIVE_TTL = 60      # fast_info 라이브 throttle (잔존 야후 대비 방어 — 홈은 보통 미사용)
_LAST_LIVE: dict = {}
_LAST_LIVE_TS = 0.0

# 배포-인지 캐시 솔트 (사용자 2026-06-12 '대시보드 반영 너무 느려') —
# git reset --hard 배포가 이 모듈을 갱신하면 mtime 이 바뀜 → 솔트 포함
# 캐시 키가 즉시 무효화 → 위젯 로직/universe 변경이 같은 날 day-key
# 캐시(12h TTL)에 막혀 수 시간 안 보이던 클래스(#281 실적 450 확장이
# 12:01 옛 캐시에 가려진 케이스) 영구 차단. 코드 무변경 배포면 mtime
# 그대로 = 캐시 보존(불필요 refetch 0).
try:
    _CODE_SALT = str(int(os.path.getmtime(__file__)))[-6:]
except OSError:
    _CODE_SALT = "0"

# ── Market Snapshot Ticker Groups ────────────────────────────────────

# 사용자 2026-06-14 '다 네이버로' — 세계지수·코스피/코스닥 **전부 네이버**(nvi:worldstock/
# index + nvd:domestic, VM probe 확정). 니케이/대만/상해/심천/항셍/Sensex/VN 네이버.
# ⚠️ CSI300·항셍테크(HSTECH)·Nifty 는 **현재 미표시**(상해종합 .SSEC·항셍 .HSI·Sensex
# .BSESN 으로 대체) — 추가하려면 네이버 reutersCode 발굴 필요(코멘트 정정 2026-06-15).
CARD_ASIA = [
    ("KR 코스피", "nvd:KOSPI"),    # 네이버 polling/domestic (사용자 2026-06-14)
    ("KR 코스닥", "nvd:KOSDAQ"),
    ("JP 니케이 225", "nvi:.N225"),
    ("TW 대만 가권", "nvi:.TWII"),
    ("CN 상해 종합", "nvi:.SSEC"),
    ("CN 심천 종합", "nvi:.SZSC"),  # 사용자 2026-06-14 (VM probe 확정 .SZSC=2,697.17)
    ("HK 홍콩 항셍", "nvi:.HSI"),
    ("VN 베트남 (VN-Index)", "nvi:.VNI"),  # 네이버 worldstock VN지수 (ETF→실지수, 2026-06-14)
    ("VN 하노이 HNX", "nvi:.HNXI"),  # 사용자 2026-06-14 (VM probe 확정 .HNXI=302.49)
    ("IN 인도 Sensex", "nvi:.BSESN"),
    # 인도네시아·말레이시아는 아메리카&이머징으로 이동(사용자 2026-06-14 재이동).
]

# 사용자 2026-06-14 '다 네이버로' — 세계 cross-rate 는 네이버 exchangeWorld(nvx:).
# 원/달러·엔/원은 KRW-base 라 exchangeWorld 미포함(KRW 부재) → 네이버 marketindex/
# exchange(nvk:FX_*)로 가져온다. **야후 아님** (옛 'yfinance 유지' 주석 정정 2026-06-15).
CARD_FX = [
    ("KR 원/달러", "nvk:FX_USDKRW"),       # 네이버 marketindex/exchange (사용자 2026-06-14)
    ("JP 엔/원 (100엔)", "nvk:FX_JPYKRW"),  # 네이버 (100엔 기준 표기)
    ("EU 유로/달러", "nvx:EURUSD"),
    ("GB 파운드/달러", "nvx:GBPUSD"),
    ("CN 달러/위안", "nvx:USDCNY"),
    ("HK 달러/미국달러", "nvx:USDHKD"),
    ("TW 대만달러/미국달러", "nvx:USDTWD"),
    ("IN 루피/달러", "nvx:USDINR"),
    ("AU 호주달러/달러", "nvx:AUDUSD"),
    ("CH 스위스프랑/달러", "nvx:USDCHF"),
]

# 사용자 2026-06-14 '다 네이버로' — 원자재를 네이버 marketindex(nv:symbolCode)로
# 전환(fast_info 무관, 1분). 두바이유(DCB)·알루미늄합금(AA) 포함. 대두는 매크로로.
CARD_COMMODITIES = [
    ("WTI 유가", "nv:CL"),
    ("브렌트유", "nv:BRN"),
    ("두바이유", "nv:DCB"),
    ("천연가스", "nv:NG"),
    ("국제 금", "nv:GC"),
    ("국제 은", "nv:SI"),
    ("구리", "nv:HG"),
    ("알루미늄", "nv:AA"),
    ("니켈", "nv:NI"),               # 사용자 2026-06-14 (네이버 metals)
]

# 사용자 2026-06-14: 이더리움 다음 테더 추가. VIX 는 네이버 worldstock(.VIX).
CARD_SENTIMENT = [
    ("VIX (공포지수)", "nvi:.VIX"),
    # 코인 = 네이버 업비트 시세(nvc:, 원화). 사용자 2026-06-14 '코인도 다 네이버'.
    # ⚠️ 업비트 원화시장이라 값 ₩. 업비트 미상장(BNB 등)은 graceful 블랭크.
    ("비트코인", "nvc:BTC"),
    ("이더리움", "nvc:ETH"),
    ("테더", "nvc:USDT"),
    ("USD코인", "nvc:USDC"),
    ("리플", "nvc:XRP"),
    ("솔라나", "nvc:SOL"),
    ("트론", "nvc:TRX"),  # 사용자 2026-06-14 BNB(업비트 미상장) 대신 트론
    ("도지코인", "nvc:DOGE"),
]

# 사용자 2026-06-14: 러셀 다음 다우운송·필라델피아반도체·나스닥바이오·KBW은행.
# 사용자 2026-06-14 '다 네이버로' — yfinance(^) → 네이버 worldstock(nvi:reutersCode).
# 확인된 ^XXX→.XXX 패턴(.N225/.TWII/.HSI/.VIX/.SOX/.BSESN). S&P 는 네이버 .INX.
CARD_US = [
    ("us S&P 500", "nvi:.INX"),
    ("us 나스닥 종합", "nvi:.IXIC"),
    ("us 다우 존스", "nvi:.DJI"),
    ("다우 운송", "nvi:.DJT"),
    ("필라델피아 반도체", "nvi:.SOX"),
    # 사용자 2026-06-14: 러셀·나스닥바이오·KBW 제거, 섹터 ETF 4종 추가(네이버
    # worldstock/etf, nve:). VM probe 확정 — bare 티커 코드(XLF 53.34 등).
    ("XLF 금융", "nve:XLF"),
    ("XLE 에너지", "nve:XLE"),
    ("XLP 필수소비", "nve:XLP"),
    ("XLV 헬스케어", "nve:XLV"),
]

# 사용자 2026-06-14: 선물에 운송(운임지수) 추가 — 중국컨테이너(CCFI)·BDI 건화물.
# 네이버 marketindex/transport(nv:) — energy/metals 와 동일 구조, 주간 갱신.
CARD_FUTURES = [
    ("us S&P 500 선물", "nvf:EScv1"),     # 네이버 worldstock/futures (사용자 2026-06-14)
    ("us 다우 존스 선물", "nvf:YMcv1"),
    ("us 나스닥 100 선물", "nvf:NQcv1"),
    ("us 러셀 2000 선물", "nvf:RTYcv1"),
    ("BDI 건화물 운임", "nv:BADI"),
    ("중국컨테이너 운임 (CCFI)", "nv:CCFI"),
]

CARD_EU = [
    ("EU 유로 스톡스 50", "nvi:.STOXX50E"),
    ("DE 독일 DAX", "nvi:.GDAXI"),
    ("GB 영국 FTSE 100", "nvi:.FTSE"),
    ("FR 프랑스 CAC 40", "nvi:.FCHI"),
    ("IT 이탈리아 FTSE MIB", "nvi:.FTMIB"),
    ("NL 네덜란드 AEX", "nvi:.AEX"),   # 사용자 2026-06-14 스위스 SMI 대신 네덜란드 AEX
]

# 사용자 2026-06-14 순서: 호주·브라질·멕시코·아르헨티나·인도네시아·말레이시아.
CARD_AMERICAS = [
    ("AU 호주 ASX 200", "nvi:.AXJO"),
    ("BR 브라질 Bovespa", "nvi:.BVSP"),
    ("MX 멕시코 IPC", "nvi:.MXX"),
    ("AR 아르헨티나 MERVAL", "nvi:.MERV"),
    ("ID 인도네시아 JCI", "nvi:.JKSE"),
    ("MY 말레이시아 KLCI", "nvi:.KLSE"),
]

ALL_CARDS = [
    ("한국 & 아시아", CARD_ASIA),
    ("주요 환율 (FX)", CARD_FX),
    ("핵심 지표 (금리/경제)", None),  # FRED — handled separately
    ("원자재 & 귀금속", CARD_COMMODITIES),
    ("시장 심리 & 코인", CARD_SENTIMENT),
    ("미국 지수", CARD_US),
    ("미국 지수 선물 & 운송 (Futures)", CARD_FUTURES),
    ("유럽 지수", CARD_EU),
    ("아메리카 & 이머징", CARD_AMERICAS),
]

# ── FRED Economic Indicators ────────────────────────────────────────

# 사용자 2026-06-14: 달러인덱스·FFR 제거, 2년·GDP성장률 추가. 순서 = 사용자 지정.
FRED_INDICATORS = [
    ("US 미국채 2년 (금리)", "DGS2", "%", 90),
    ("US 미국채 10년 (금리)", "DGS10", "%", 90),
    ("US 미국채 30년 (금리)", "DGS30", "%", 90),
    ("JOLTS (비농업 구인)", "JTSJOL", "M", 365),
    ("실업수당 청구 (신규)", "ICSA", "", 90),
    ("GDP 성장률 (QoQ)", "A191RL1Q225SBEA", "%", 400),
    ("근원 PCE 물가 (YoY)", "PCEPILFE", "%", 730),  # 사용자 2026-06-14 GDP 다음
    ("소매판매 (YoY)", "RSAFS", "%", 400),
    ("CPI (YoY)", "CPIAUCSL", "%", 730),
    ("PPI (YoY)", "PPIACO", "%", 730),
]

_DOLLAR_INDEX_TICKER = "DX-Y.NYB"



# ── yfinance batch fetch ────────────────────────────────────────────

def _all_yf_tickers() -> list[str]:
    """Collect all yfinance tickers needed."""
    tickers = []
    for _, items in ALL_CARDS:
        if items is None:
            continue
        for _, tk in items:
            if tk.startswith(("nv:", "nvi:", "nvd:", "nvx:", "nvk:",
                              "nvf:", "nvc:", "nve:")):
                continue  # 네이버 (marketindex/worldstock/domestic/exchange/futures/coin/etf)
            if tk.endswith((".KS", ".KQ")):
                continue  # KR 개별종목 = 네이버 polling/domestic/stock (사용자 2026-06-15
                #           '홈 야후 완전 제거') — _fetch_market_snapshot 가 별도 병합.
            tickers.append(tk)
    # 지수·환율·원자재·코인·KR 개별종목 전부 네이버로 전환 → 홈 스냅샷 yfinance
    # **완전 제거**(사용자 2026-06-15). 이 함수는 보통 빈 리스트(야후 0)를 반환.
    return tickers


def _fetch_yf_batch() -> dict[str, dict]:
    """{ticker: {close, prev_close, change, pct}} — '오늘' 값 기준.

    ⚠️ 일봉(yf.download period=5d, interval=1d)의 iloc[-1]은 '확정된 마지막
    일봉'이라, 아시아 지수(코스피/대만/니케이 등)는 Yahoo 일봉 갱신 지연으로
    하루 늦게(어제 종가) 나오는 버그가 있었다. → fast_info(last_price +
    previous_close, 견적 기반 near-real-time)를 1차로 써 '오늘 값 + 오늘 등락'
    을 정확히 잡고, 실패 종목만 일봉으로 폴백(회귀 0)."""
    tickers = _all_yf_tickers()
    result: dict[str, dict] = {}
    # YF_PAUSE(사용자 2026-06-14 '정지게이트 모두 추가') → yfinance skip, 직전 성공
    # 배치를 디스크에서 반환(홈 지수 stale 유지·블랭크 방지). 네이버·FRED 는 무관.
    _bc = _CACHE_DIR / "yf_batch_snapshot.json"
    try:
        from bot.finviz_client import yf_paused
        if yf_paused():
            try:
                return json.loads(_bc.read_text())
            except Exception:
                return {}
    except Exception:
        pass

    # 1) 일봉 batch — 폴백용 (fast_info 없는 종목)
    daily: dict[str, tuple[float, float]] = {}
    try:
        # 야후 티커 0(전부 네이버, 사용자 2026-06-15) → download 생략(빈 문자열 호출
        # → 로그 스팸 차단). 아래 네이버 병합이 result 를 채운다.
        df = (yf.download(" ".join(tickers), period="5d", progress=False,
                          threads=True, timeout=20) if tickers else None)
        if df is not None and not df.empty:
            for tk in tickers:
                try:
                    closes = (df["Close"][tk] if len(tickers) > 1
                              else df["Close"]).dropna()
                    if len(closes) >= 1:
                        cur = float(closes.iloc[-1])
                        prev = float(closes.iloc[-2]) if len(closes) >= 2 else cur
                        daily[tk] = (cur, prev)
                except Exception:
                    continue
    except Exception as exc:
        log.warning("market_overview: yf daily batch error: %s", exc)

    # 2) fast_info live — 오늘 값(last_price) + 어제 종가(previous_close)
    def _live(tk: str):
        try:
            fi = yf.Ticker(tk).fast_info
            lp = getattr(fi, "last_price", None)
            pc = getattr(fi, "previous_close", None)
            if lp is not None and pc is not None and float(pc) != 0:
                return tk, (float(lp), float(pc))
        except Exception as exc:
            try:
                from bot.finviz_client import fast_info_trip, is_rate_limit_error
                if is_rate_limit_error(exc):
                    fast_info_trip("snapshot")
            except Exception:
                pass
        return tk, None

    live: dict[str, tuple[float, float]] = {}
    global _LAST_LIVE, _LAST_LIVE_TS
    try:
        from bot.finviz_client import fast_info_ok
        _fi_ok = fast_info_ok()
    except Exception:
        _fi_ok = True
    # throttle(10분) + 회로차단(쿨다운 중이면 download 만 사용)
    if tickers and _fi_ok and time.time() - _LAST_LIVE_TS >= _LIVE_TTL:
        try:
            # fast_info 라이브 — 전 종목(사용자 2026-06-14 '전부 1분으로'; (A) 축소
            # 원복). 회로차단(_fi_ok)·throttle(1분)·daily 폴백이 가드.
            with ThreadPoolExecutor(max_workers=8) as pool:
                for tk, v in pool.map(_live, tickers):
                    if v:
                        live[tk] = v
            if live:
                _LAST_LIVE = dict(live)
                _LAST_LIVE_TS = time.time()
        except Exception as exc:
            log.warning("market_overview: fast_info live error: %s", exc)
    else:
        live = dict(_LAST_LIVE)        # throttle 중 — 직전 라이브가 재사용(download 보완)

    # 3) merge — live(오늘) 우선, 없으면 daily 폴백. live 가 글리치(KLAC
    # 클래스: fast_info 분할 미조정 → ±75% 초과 phantom 등락)면 daily 봉
    # 폴백, 그것도 없으면 직전 종가 교체 (교체 우선 정책 2026-06-04).
    try:
        from bot.price_sanity import quote_glitch_gap as _qgg
    except Exception:
        _qgg = None
    for tk in tickers:
        if tk in live:
            cur, prev = live[tk]
            if _qgg and _qgg(cur, prev):
                cur, prev = daily.get(tk, (prev, prev))
            elif _qgg and tk in daily and _qgg(cur, daily[tk][0]):
                # 2차 (KLAC 클래스): live 의 last·prev 가 둘 다 같은 미조정
                # 기준이면 1차가 장님 — 조정 batch 일봉 종가와 교차해
                # ±75% 초과면 batch 쌍으로 교체 (추가 호출 0).
                cur, prev = daily[tk]
        elif tk in daily:
            cur, prev = daily[tk]
        else:
            continue
        chg = cur - prev
        pct = (chg / prev * 100) if prev != 0 else 0.0
        result[tk] = {"close": cur, "prev_close": prev,
                      "change": chg, "pct": pct}
    # 네이버 병합 (사용자 2026-06-14 '다 네이버로'). fast_info 무관.
    #   nv:  = marketindex(energy/metals/agri/transport) — 원자재·귀금속·운임지수
    #   nvi: = worldstock/index(reutersCode) — 세계지수(니케이/VIX/필반/이탈리아 등)
    #   nvd: = polling/domestic/index(itemCode) — 국내지수(코스피/코스닥)
    #   nvx: = marketindex/exchangeWorld(reutersCode) — 세계 환율 cross-rate
    #   nvk: = marketindex/exchange(reutersCode) — KR-base 환율(원/달러·원/엔)
    # yfinance 무티커(두바이유/니켈/CCFI/BDI) 포함. 코드별 graceful —
    # 네이버 미반환 코드는 result 미주입(블랭크, 크래시 없음).
    _nv_codes, _nvi_codes, _nvd_codes = [], [], []
    _nvx_codes, _nvk_codes, _nvf_codes, _nvc_codes, _nve_codes = [], [], [], [], []
    for _grp, _items in ALL_CARDS:
        if not _items:
            continue
        for _nm, _tk in _items:
            if _tk.startswith("nv:"):
                _nv_codes.append(_tk)
            elif _tk.startswith("nvi:"):
                _nvi_codes.append(_tk)
            elif _tk.startswith("nvd:"):
                _nvd_codes.append(_tk)
            elif _tk.startswith("nvx:"):
                _nvx_codes.append(_tk)
            elif _tk.startswith("nvk:"):
                _nvk_codes.append(_tk)
            elif _tk.startswith("nvf:"):
                _nvf_codes.append(_tk)
            elif _tk.startswith("nvc:"):
                _nvc_codes.append(_tk)
            elif _tk.startswith("nve:"):
                _nve_codes.append(_tk)
    if _nv_codes:
        try:
            from bot.naver_marketindex import fetch_commodities
            _nvc = fetch_commodities()
            for _tk in _nv_codes:
                _rec = _nvc.get(_tk[3:])
                if _rec and _rec.get("close") is not None:
                    result[_tk] = {"close": _rec["close"], "prev_close": _rec["prev"],
                                   "change": _rec["change"], "pct": _rec["pct"]}
        except Exception as exc:
            log.warning("market_overview: naver 원자재/운임 병합 실패: %s", exc)
    if _nvi_codes:
        try:
            from bot.naver_marketindex import fetch_world_indices
            _nvi = fetch_world_indices(tuple(_t[4:] for _t in _nvi_codes))
            for _tk in _nvi_codes:
                _rec = _nvi.get(_tk[4:])
                if _rec and _rec.get("close") is not None:
                    result[_tk] = {"close": _rec["close"], "prev_close": _rec["prev"],
                                   "change": _rec["change"], "pct": _rec["pct"]}
        except Exception as exc:
            log.warning("market_overview: naver 세계지수 병합 실패: %s", exc)
    if _nvd_codes:
        try:
            from bot.naver_marketindex import fetch_domestic_indices
            _nvd = fetch_domestic_indices(tuple(_t[4:] for _t in _nvd_codes))
            for _tk in _nvd_codes:
                _rec = _nvd.get(_tk[4:])
                if _rec and _rec.get("close") is not None:
                    result[_tk] = {"close": _rec["close"], "prev_close": _rec["prev"],
                                   "change": _rec["change"], "pct": _rec["pct"]}
        except Exception as exc:
            log.warning("market_overview: naver 국내지수 병합 실패: %s", exc)
    if _nvx_codes:                             # fetch_world_fx 는 전체 반환(인자 무시)
        try:
            from bot.naver_marketindex import fetch_world_fx
            _nvx = fetch_world_fx()
            for _tk in _nvx_codes:
                _rec = _nvx.get(_tk[4:])
                if _rec and _rec.get("close") is not None:
                    result[_tk] = {"close": _rec["close"], "prev_close": _rec["prev"],
                                   "change": _rec["change"], "pct": _rec["pct"]}
        except Exception as exc:
            log.warning("market_overview: naver 세계환율 병합 실패: %s", exc)
    if _nvk_codes:                             # fetch_kr_fx 는 전체 반환(인자 무시)
        try:
            from bot.naver_marketindex import fetch_kr_fx
            _nvk = fetch_kr_fx()
            for _tk in _nvk_codes:
                _rec = _nvk.get(_tk[4:])
                if _rec and _rec.get("close") is not None:
                    result[_tk] = {"close": _rec["close"], "prev_close": _rec["prev"],
                                   "change": _rec["change"], "pct": _rec["pct"]}
        except Exception as exc:
            log.warning("market_overview: naver KR환율 병합 실패: %s", exc)
    if _nvf_codes:                             # 미국 지수선물 (worldstock/futures)
        try:
            from bot.naver_marketindex import fetch_world_futures
            _nvf = fetch_world_futures(tuple(_t[4:] for _t in _nvf_codes))
            for _tk in _nvf_codes:
                _rec = _nvf.get(_tk[4:])
                if _rec and _rec.get("close") is not None:
                    result[_tk] = {"close": _rec["close"], "prev_close": _rec["prev"],
                                   "change": _rec["change"], "pct": _rec["pct"]}
        except Exception as exc:
            log.warning("market_overview: naver 선물 병합 실패: %s", exc)
    if _nvc_codes:                             # 코인 (업비트, fetch_naver_coins 전체 반환)
        try:
            from bot.naver_marketindex import fetch_naver_coins
            _nvc = fetch_naver_coins()
            for _tk in _nvc_codes:
                _rec = _nvc.get(_tk[4:])
                if _rec and _rec.get("close") is not None:
                    result[_tk] = {"close": _rec["close"], "prev_close": _rec["prev"],
                                   "change": _rec["change"], "pct": _rec["pct"]}
        except Exception as exc:
            log.warning("market_overview: naver 코인 병합 실패: %s", exc)
    if _nve_codes:                             # 미국 섹터 ETF (worldstock/etf)
        try:
            from bot.naver_marketindex import fetch_world_etf
            _nve = fetch_world_etf(tuple(_t[4:] for _t in _nve_codes))
            for _tk in _nve_codes:
                _rec = _nve.get(_tk[4:])
                if _rec and _rec.get("close") is not None:
                    result[_tk] = {"close": _rec["close"], "prev_close": _rec["prev"],
                                   "change": _rec["change"], "pct": _rec["pct"]}
        except Exception as exc:
            log.warning("market_overview: naver ETF 병합 실패: %s", exc)
    # KR 개별종목(.KS/.KQ) — 네이버 polling/domestic/stock (사용자 2026-06-15
    # '홈 야후 완전 제거'). 33 KR 주요종목 시세를 야후→네이버. probe 확정 구조.
    _kr_tks = [_tk for _grp, _items in ALL_CARDS if _items
               for _nm, _tk in _items if _tk.endswith((".KS", ".KQ"))]
    if _kr_tks:
        try:
            from bot.naver_marketindex import fetch_kr_stock_quotes
            _krq = fetch_kr_stock_quotes(tuple(_t.rsplit(".", 1)[0] for _t in _kr_tks))
            for _tk in _kr_tks:
                _rec = _krq.get(_tk.rsplit(".", 1)[0])
                if _rec and _rec.get("close") is not None:
                    result[_tk] = {"close": _rec["close"], "prev_close": _rec["prev"],
                                   "change": _rec["change"], "pct": _rec["pct"]}
        except Exception as exc:
            log.warning("market_overview: naver KR 개별종목 병합 실패: %s", exc)
    if result:                       # YF_PAUSE 시 폴백용 직전 배치 디스크 캐시
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _bc.write_text(json.dumps(result, ensure_ascii=False))
        except Exception:
            pass
    return result


# ── FRED fetch ──────────────────────────────────────────────────────

def _fred_fetch_series(series_id: str, lookback_days: int) -> Optional[dict]:
    """Minimal FRED series fetch (reuses fred_client pattern)."""
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return None
    cache_dir = _CACHE_DIR / "fred"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()
    cache_file = cache_dir / f"{series_id}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < 24:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={api_key}&file_type=json"
        f"&observation_start={start}&sort_order=desc&limit=15"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
    except Exception as exc:
        log.warning("fred: fetch %s failed: %s", series_id, exc)
        return None

    clean = []
    for row in obs:
        v, d = row.get("value", ""), row.get("date", "")
        if not v or v == "." or not d:
            continue
        try:
            clean.append((d, float(v)))
        except ValueError:
            continue
    if not clean:
        return None

    latest_date, latest_val = clean[0]
    prev_val = clean[1][1] if len(clean) >= 2 else None
    change = (latest_val - prev_val) if prev_val is not None else None

    result = {"value": latest_val, "time": latest_date, "prev_value": prev_val, "change": change}
    try:
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass
    return result


def _fetch_fred_yoy(series_id: str) -> Optional[dict]:
    """Fetch index-level FRED series and compute YoY %."""
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return None
    cache_dir = _CACHE_DIR / "fred"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()
    cache_file = cache_dir / f"{series_id}_yoy_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < 24:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    start = (date.today() - timedelta(days=730)).isoformat()
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={api_key}&file_type=json"
        f"&observation_start={start}&sort_order=desc&limit=30"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
    except Exception:
        return None

    clean = []
    for row in obs:
        v, d = row.get("value", ""), row.get("date", "")
        if not v or v == "." or not d:
            continue
        try:
            clean.append((d, float(v)))
        except ValueError:
            continue
    if len(clean) < 2:
        return None

    latest_date, latest_val = clean[0]
    # Find value ~12 months ago
    try:
        latest_dt = datetime.strptime(latest_date, "%Y-%m-%d")
        target_dt = latest_dt - timedelta(days=365)
        yoy_base = None
        for d, v in clean:
            dt = datetime.strptime(d, "%Y-%m-%d")
            if dt <= target_dt:
                yoy_base = v
                break
        if yoy_base and yoy_base != 0:
            yoy = (latest_val - yoy_base) / yoy_base * 100
        else:
            yoy = None
    except Exception:
        yoy = None

    prev_val = clean[1][1] if len(clean) >= 2 else None
    prev_change = None
    if prev_val is not None:
        # Find prev's YoY base
        try:
            prev_date = clean[1][0]
            prev_dt = datetime.strptime(prev_date, "%Y-%m-%d")
            prev_target = prev_dt - timedelta(days=365)
            for d, v in clean:
                dt = datetime.strptime(d, "%Y-%m-%d")
                if dt <= prev_target:
                    if v != 0:
                        prev_change = (prev_val - v) / v * 100
                    break
        except Exception:
            pass

    change = (yoy - prev_change) if yoy is not None and prev_change is not None else None
    result = {"value": round(yoy, 2) if yoy is not None else None,
              "time": latest_date, "prev_value": round(prev_change, 2) if prev_change else None,
              "change": round(change, 2) if change is not None else None}
    try:
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass
    return result


def _fetch_all_fred() -> list[dict]:
    """Fetch all FRED indicators. Returns list of dicts."""
    results = []
    for label, series_id, unit, lookback in FRED_INDICATORS:
        if series_id is None:
            continue
        if series_id in ("CPIAUCSL", "PPIACO", "PCEPILFE", "RSAFS"):
            data = _fetch_fred_yoy(series_id)
        else:
            data = _fred_fetch_series(series_id, lookback)
        if data:
            data["label"] = label
            data["unit"] = unit
        results.append({"label": label, "unit": unit, "data": data})
    return results


# ── Finnhub Earnings Calendar ───────────────────────────────────────

def fetch_earnings_calendar(days_ahead: int = 14) -> list[dict]:
    """Fetch upcoming earnings from Finnhub. Returns list of earnings events."""
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return []

    cache_dir = _CACHE_DIR / "finnhub"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"earnings_{today.isoformat()}_{_CODE_SALT}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < 6:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    from_date = today.isoformat()
    to_date = (today + timedelta(days=days_ahead)).isoformat()
    url = f"https://finnhub.io/api/v1/calendar/earnings?from={from_date}&to={to_date}&token={api_key}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        events = data.get("earningsCalendar", [])
    except Exception as exc:
        log.warning("finnhub: earnings calendar fetch error: %s", exc)
        return []

    result = []
    for e in events:
        symbol = e.get("symbol", "")
        if not symbol:
            continue
        result.append({
            "symbol": symbol,
            "date": e.get("date", ""),
            "hour": e.get("hour", ""),
            "eps_estimate": e.get("epsEstimate"),
            "revenue_estimate": e.get("revenueEstimate"),
            "quarter": e.get("quarter"),
            "year": e.get("year"),
            "name": "",
        })

    # Batch-resolve company names from yfinance (best-effort). ⛔ 회로차단/정지 중이면
    # skip — .info(검색과 동일 quote API) 30콜이 야후 차단 가중(사용자 2026-06-14).
    try:
        from bot.finviz_client import fast_info_ok, yf_paused
        if fast_info_ok() and not yf_paused():
            import yfinance as yf
            syms = list({r["symbol"] for r in result})[:30]
            tickers = yf.Tickers(" ".join(syms))
            name_map: dict[str, str] = {}
            for s in syms:
                try:
                    info = tickers.tickers[s].info or {}
                    name_map[s] = info.get("shortName") or info.get("longName") or ""
                except Exception:
                    pass
            for r in result:
                r["name"] = name_map.get(r["symbol"], "")
    except Exception:
        pass

    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return result


# 한국 주요 종목(KOSPI/KOSDAQ 대형주) — yfinance .calendar 로 예정 실적일.
# Finnhub 무료 티어가 KR 미커버 → 종목별 .calendar(무료)로 직접 산출.
_KR_EARNINGS_UNIVERSE = [
    ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"),
    ("373220.KS", "LG에너지솔루션"), ("207940.KS", "삼성바이오로직스"),
    ("005380.KS", "현대차"), ("000270.KS", "기아"),
    ("005490.KS", "POSCO홀딩스"), ("035420.KS", "NAVER"),
    ("035720.KS", "카카오"), ("051910.KS", "LG화학"),
    ("006400.KS", "삼성SDI"), ("068270.KS", "셀트리온"),
    ("105560.KS", "KB금융"), ("055550.KS", "신한지주"),
    ("012330.KS", "현대모비스"), ("028260.KS", "삼성물산"),
    ("066570.KS", "LG전자"), ("015760.KS", "한국전력"),
    ("034730.KS", "SK"), ("096770.KS", "SK이노베이션"),
    ("017670.KS", "SK텔레콤"), ("030200.KS", "KT"),
    ("086790.KS", "하나금융지주"), ("010130.KS", "고려아연"),
    ("009150.KS", "삼성전기"), ("011070.KS", "LG이노텍"),
    ("003670.KS", "포스코퓨처엠"), ("247540.KQ", "에코프로비엠"),
    ("086520.KQ", "에코프로"), ("196170.KQ", "알테오젠"),
    ("058470.KQ", "리노공업"), ("042700.KQ", "한미반도체"),
    ("091990.KQ", "셀트리온헬스케어"),
]


def _kr_earnings_universe() -> list[tuple[str, str]]:
    """KR 실적 캘린더 universe — pykrx 시총 상위(KOSPI 300 + KOSDAQ 150)
    동적 산출, 실패 시 하드코딩 33 폴백 (사용자 2026-06-12 '한국 21개가
    최선인가' — 옛 고정 33종목이 병목이었음). 7일 디스크 캐시(KRX 4콜).

    이름은 get_market_price_change(1콜/시장, 종목명 컬럼 포함) — per-ticker
    이름 조회 450콜 회피. KRX creds 부재/pykrx 미설치면 graceful 폴백.
    ⚠️ 커버리지 한계(정직): yfinance .calendar 는 KR 중소형주 대부분 빈값 —
    universe 를 늘려도 '확정 실적일'이 있는 종목만 표에 추가된다."""
    cache_file = _CACHE_DIR / "finnhub" / f"kr_earnings_universe_{_CODE_SALT}.json"
    try:
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < 7 * 86400:
            cached = json.loads(cache_file.read_text())
            if isinstance(cached, list) and len(cached) > 50:
                return [tuple(x) for x in cached]
    except Exception:
        pass
    out: list[tuple[str, str]] = []
    try:
        from bot.pykrx_client import krx_login_ready, _quiet_pykrx_logging
        if krx_login_ready():
            _quiet_pykrx_logging()
            from pykrx import stock as _pk
            from datetime import datetime as _dt2, timedelta as _td2
            d = _dt2.now()
            ds = d.strftime("%Y%m%d")
            ds_prev = (d - _td2(days=7)).strftime("%Y%m%d")
            for mkt, suffix, cap in (("KOSPI", ".KS", 300),
                                     ("KOSDAQ", ".KQ", 150)):
                mc = _pk.get_market_cap(ds, market=mkt)
                names_df = _pk.get_market_price_change(ds_prev, ds, market=mkt)
                names = (names_df["종목명"].to_dict()
                         if names_df is not None and "종목명" in names_df else {})
                top = mc.sort_values("시가총액", ascending=False).head(cap)
                for code in top.index:
                    nm = str(names.get(code) or code)
                    out.append((f"{code}{suffix}", nm))
    except Exception as exc:
        log.warning("kr earnings universe via pykrx failed: %s", exc)
        out = []
    if len(out) > 50:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(out, ensure_ascii=False))
        except Exception:
            pass
        return out
    return list(_KR_EARNINGS_UNIVERSE)


def _earning_row_from_cal(cal, tk: str, name: str, today: date,
                          cutoff: date) -> Optional[dict]:
    """yfinance .calendar dict → 예정 실적 행 또는 None. 순수(테스트 가능).

    KR + JP/TW/CN/HK 가 공유하는 파싱·윈도 필터. 추정치(EPS/매출)는
    yfinance 가 비미국 종목에 종종 None → 그대로 둠('—' 표시는 렌더층)."""
    if not isinstance(cal, dict):
        return None
    eds = cal.get("Earnings Date") or []
    if not eds:
        return None
    d0 = eds[0]
    ds = d0.strftime("%Y-%m-%d") if hasattr(d0, "strftime") else str(d0)[:10]
    try:
        dd = datetime.strptime(ds, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    if dd < today or dd > cutoff:
        return None
    return {
        "symbol": tk, "name": name, "date": ds, "hour": "",
        "eps_estimate": cal.get("Earnings Average"),
        "revenue_estimate": cal.get("Revenue Average"),
        "quarter": None, "year": None,
    }


def fetch_earnings_calendar_kr(days_ahead: int = 90) -> list[dict]:
    """KR 종목 예정 실적일 (yfinance .calendar, 무료). 12h 캐시.

    KR 실적일은 미국보다 드물게 분포 → 윈도 90일로 넓게. universe 는
    pykrx 시총 상위 450(_kr_earnings_universe, 폴백 33). 추정치(EPS/매출)는
    yfinance 가 KR 에 대해 종종 None → 그대로 '—' 표시(정직)."""
    cache_dir = _CACHE_DIR / "finnhub"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"earnings_kr_{today.isoformat()}_{_CODE_SALT}.json"
    if cache_file.exists():
        try:
            # 12h→6h (사용자 2026-06-12 '느려') — US 와 동일, 일 4회 갱신.
            # 450 yf .calendar threadpool ~1분, 무료.
            if (time.time() - cache_file.stat().st_mtime) / 3600 < 6:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    cutoff = today + timedelta(days=days_ahead)

    def _one(item):
        tk, name = item
        try:
            return _earning_row_from_cal(yf.Ticker(tk).calendar, tk, name,
                                         today, cutoff)
        except Exception:
            return None

    results: list[dict] = []
    try:
        from bot.finviz_client import yf_paused
        _paused = yf_paused()
    except Exception:
        _paused = False
    if _paused:                         # YF_PAUSE → .calendar 스캔 skip, 직전 캐시
        try:
            for p in sorted(cache_dir.glob("earnings_kr_*.json"),
                            key=lambda q: q.stat().st_mtime, reverse=True):
                if p == cache_file:
                    continue
                prior = json.loads(p.read_text())
                if prior:
                    return prior
        except Exception:
            pass
        return []
    universe = _kr_earnings_universe()
    with ThreadPoolExecutor(max_workers=12) as pool:
        for r in pool.map(_one, universe):
            if r:
                results.append(r)
    results.sort(key=lambda x: x.get("date", ""))
    try:
        cache_file.write_text(json.dumps(results, ensure_ascii=False))
    except Exception:
        pass
    return results


# JP/TW/CN/HK 실적 캘린더 — KR 패턴 일반화 (사용자 2026-06-13 '실적빌드 다국가').
# 유니버스 = bot.market 의 산업 peer 맵(검증된 주요종목 ~50-100/시장,
# 신고저 스캔과 동일 소스). yfinance .calendar(무료·무키)로 확정 실적일만.
_INTL_EARN_PEERS = {
    "JP": "_JP_INDUSTRY_PEERS",
    "TW": "_TW_INDUSTRY_PEERS",
    "CN_A": "_CN_A_INDUSTRY_PEERS",
    "HK": "_HK_INDUSTRY_PEERS",
}


def _intl_earnings_universe(market: str) -> list[tuple[str, str]]:
    """peer 맵의 unique 티커(주요종목). 이름은 ticker 기본(맵에 명칭 없음)."""
    attr = _INTL_EARN_PEERS.get(market)
    if not attr:
        return []
    try:
        from bot import market as _mkt
        peers = getattr(_mkt, attr, {}) or {}
    except Exception as exc:
        log.warning("intl earnings universe error (%s): %s", market, exc)
        return []
    seen: set = set()
    out: list[tuple[str, str]] = []
    for vals in peers.values():
        for x in (vals if isinstance(vals, (list, tuple)) else [vals]):
            t = str(x[0] if isinstance(x, (list, tuple)) else x).strip()
            if t and t not in seen:
                seen.add(t)
                out.append((t, t))
    return out


def fetch_earnings_calendar_intl(market: str, days_ahead: int = 90,
                                 cache_only: bool = False) -> list[dict]:
    """JP/TW/CN/HK 예정 실적일 (yfinance .calendar, 무료·무키). 6h 캐시.

    KR(fetch_earnings_calendar_kr) 패턴 일반화 — 산업 peer 맵 주요종목
    universe(시장당 ~50-100). 추정치(EPS/매출)는 yfinance 가 비미국에
    종종 None → '—'(정직). 미지원 시장/유니버스 부재면 빈 리스트.
    ⚠️ 커버리지 한계: yfinance .calendar 는 비미국 종목 상당수 빈값 —
    '확정 실적일' 있는 종목만 표에 추가된다(universe 크기와 무관).

    cache_only=True (실적 캘린더 페이지 on-request 경로) — **동기 스캔 금지**:
    캐시 있으면(나이 무관) 반환, 없으면 [] (페이지 hang 방지). 캐시는
    market.html 백그라운드 갱신 + 아침 pre-warm 이 데움."""
    if market not in _INTL_EARN_PEERS:
        return []
    cache_dir = _CACHE_DIR / "finnhub"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"earnings_{market}_{today.isoformat()}_{_CODE_SALT}.json"
    if cache_file.exists():
        try:
            if cache_only or (time.time() - cache_file.stat().st_mtime) / 3600 < 6:
                return json.loads(cache_file.read_text())
        except Exception:
            pass
    if cache_only:
        return []                       # on-request 경로 — 스캔 안 함
    try:
        from bot.finviz_client import yf_paused
    except Exception:
        yf_paused = lambda: False
    if yf_paused():                     # YF_PAUSE → .calendar 스캔 skip, 직전 캐시
        try:
            for p in sorted(cache_dir.glob(f"earnings_{market}_*.json"),
                            key=lambda q: q.stat().st_mtime, reverse=True):
                prior = json.loads(p.read_text())
                if prior:
                    return prior
        except Exception:
            pass
        return []
    universe = _intl_earnings_universe(market)
    if not universe:
        return []

    cutoff = today + timedelta(days=days_ahead)

    def _one(item):
        tk, name = item
        try:
            return _earning_row_from_cal(yf.Ticker(tk).calendar, tk, name,
                                         today, cutoff)
        except Exception:
            return None

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for r in pool.map(_one, universe):
            if r:
                results.append(r)
    results.sort(key=lambda x: x.get("date", ""))
    # 한글 종목명 (사용자 2026-06-13 '일본부터 티커말고 번역 한국종목명') — peer
    # 맵엔 이름 없어 name==ticker. 확정 실적일 있는 결과(희소)만 yfinance
    # longName → chart_translate(Flash·영구캐시) 번역. 6h 캐시에 이름까지 저장.
    # graceful — 실패 시 ticker 유지(렌더가 '회사명 없으면 티커' 폴백).
    if results:
        # 한글명 3-레이어 (사용자 2026-06-14 'TW·HK 실적도 한국어'):
        # (1) 네이버 worldstock 이름맵(JP/CN/HK — HK 는 5↔4자리 zfill 정규화)
        # (2) TW = TWSE 中文명 → 한국어 번역(네이버 worldstock 미지원)
        # (3) 잔여 미스 = yfinance longName → 번역. 전부 영구/7d 캐시라 ₩~0.
        try:
            from bot.naver_ranking_client import world_name_map
            nmap = world_name_map(market)
            if nmap:
                norm = {}
                if market == "HK":     # 5자리↔4자리 zfill 정수 정규화
                    for k, v in nmap.items():
                        c = str(k).split(".")[0]
                        if c.isdigit():
                            norm.setdefault(str(int(c)), v)
                for r in results:
                    sym = r["symbol"]
                    nm = nmap.get(sym)
                    if not nm and market == "HK":
                        c = str(sym).split(".")[0]
                        if c.isdigit():
                            nm = norm.get(str(int(c)))
                    if nm:
                        r["name"] = nm
        except Exception as exc:
            log.warning("intl earnings 네이버 이름 (%s): %s", market, exc)
        if market == "TW":             # TWSE 中文명 → 한국어 번역(네이버 미지원)
            try:
                from bot.chart_translate import translate_titles_kr
                from bot.twse_client import fetch_stock_day_all
                tw_names = {f"{s.get('code')}.TW": s.get("name")
                            for s in fetch_stock_day_all().get("rows", [])
                            if s.get("code") and s.get("name")}
                nat = {r["symbol"]: tw_names[r["symbol"]] for r in results
                       if tw_names.get(r["symbol"])
                       and (not r.get("name") or r["name"] == r["symbol"])}
                uniq = sorted({v for v in nat.values() if v})
                kr = translate_titles_kr(uniq) if uniq else {}
                for r in results:
                    nm = nat.get(r["symbol"])
                    if nm and kr.get(nm):
                        r["name"] = kr[nm]
            except Exception as exc:
                log.warning("TW earnings 中文→한글: %s", exc)
        miss = [r for r in results
                if not r.get("name") or r["name"] == r["symbol"]]
        if miss:
            try:
                from bot.chart_translate import translate_titles_kr
                from bot.finviz_client import _fetch_display_names
                en = _fetch_display_names([r["symbol"] for r in miss])
                uniq = sorted({n for n in en.values() if n})
                kr = translate_titles_kr(uniq) if uniq else {}
                for r in miss:
                    e = en.get(r["symbol"], "")
                    if e:
                        r["name"] = kr.get(e) or e
            except Exception as exc:
                log.warning("intl earnings 한글명 폴백 (%s): %s", market, exc)
    # 결과 빈 경우(yfinance .calendar 일시 장애·배포 _CODE_SALT 리셋) — 최근
    # 비어있지 않은 캐시로 폴백(사용자 2026-06-14 '다가오는 실적 미국만 나옴' —
    # intl 휘발 방지). 빈 결과는 캐시에 안 써 prior 를 가리지 않음.
    if not results:
        try:
            for p in sorted(cache_dir.glob(f"earnings_{market}_*.json"),
                            key=lambda q: q.stat().st_mtime, reverse=True):
                if p == cache_file:
                    continue
                prior = json.loads(p.read_text())
                if prior:
                    return prior
        except Exception:
            pass
        return results
    try:
        cache_file.write_text(json.dumps(results, ensure_ascii=False))
    except Exception:
        pass
    return results


# ── Recent KR Research (Naver Finance 리서치 목록) ──────────────────
# 한경 컨센서스는 2026 초 JS 렌더링 전환으로 정적 scrape 불가 →
# Naver Finance 전체 시장 리서치 목록(naver_research_client)으로 대체.

def fetch_recent_research_kr(limit: int = 150) -> list[dict]:
    """Fetch latest KR 종목(기업) 리서치 리포트 — 일주일치(Naver Finance).

    개별 종목 분석이 Naver 리서치를 쓰는 것과 동일 소스. 사용자 정책
    2026-06-09: 최근 7일(일주일치) 윈도. Returns
    [{code, name, broker, rating, target, title, date}]."""
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"kr_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < (10 / 60):  # 10분 — naver 1h 갱신을 빠르게 반영
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    results: list[dict] = []
    try:
        from bot.naver_research_client import fetch_recent_research_market
        results = fetch_recent_research_market(limit=limit, days_back=7,
                                               max_pages=20)
    except Exception as exc:
        log.warning("naver research market fetch error: %s", exc)

    if results:  # truthy-only — 빈 결과(일시 실패) 캐시 안 함('또 갑자기 없음' 방지)
        try:
            cache_file.write_text(json.dumps(results, ensure_ascii=False))
        except Exception:
            pass
    return results[:limit]


def fetch_recent_research_kr_industry(limit: int = 80) -> list[dict]:
    """Fetch latest KR 산업(업종) 리서치 리포트 — 일주일치(Naver Finance).

    종목 리포트와 동일 7일 윈도. Returns [{category, broker, title, date, link}]."""
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"kr_industry_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < (10 / 60):  # 10분 — naver 1h 갱신을 빠르게 반영
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    results: list[dict] = []
    try:
        from bot.naver_research_client import fetch_recent_research_industry
        results = fetch_recent_research_industry(limit=limit, days_back=7,
                                                 max_pages=12)
    except Exception as exc:
        log.warning("naver research industry fetch error: %s", exc)

    if results:  # truthy-only — 빈 결과 캐시 안 함
        try:
            cache_file.write_text(json.dumps(results, ensure_ascii=False))
        except Exception:
            pass
    return results[:limit]


def fetch_recent_research_kr_strategy(limit: int = 80) -> list[dict]:
    """Fetch latest KR 투자전략(투자정보) 리서치 리포트 — 일주일치(Naver).

    종목·산업 리포트와 동일 7일 윈도. Returns [{broker, title, date, link}]
    (분류·목표가 없음). 사용자 2026-06-12 '네이버 투자전략 → 한국 전략 탭'."""
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"kr_strategy_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < (10 / 60):  # 10분 — naver 1h 갱신을 빠르게 반영
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    results: list[dict] = []
    try:
        from bot.naver_research_client import fetch_recent_research_strategy
        results = fetch_recent_research_strategy(limit=limit, days_back=7,
                                                 max_pages=12)
    except Exception as exc:
        log.warning("naver research strategy fetch error: %s", exc)

    if results:  # truthy-only — 빈 결과 캐시 안 함
        try:
            cache_file.write_text(json.dumps(results, ensure_ascii=False))
        except Exception:
            pass
    return results[:limit]


# ── US Research (yfinance upgrades aggregated) ──────────────────────

def _rotation_slice(items: list, ordinal: int, window_days: int = 7) -> list:
    """list 를 window_days 회전 슬라이스로 — ordinal(날짜 toordinal) 기반 '오늘 몫'.
    슬라이스 크기 = ceil(N/window_days) (최소 50) → window_days 일이면 전체 1회전.
    순수 함수(단위테스트). 리서치 야후 burst 방지용 day-slice 로테이션."""
    if not items:
        return []
    size = max(50, (len(items) + window_days - 1) // window_days)
    nsl = max(1, (len(items) + size - 1) // size)
    idx = ordinal % nsl
    return items[idx * size:(idx + 1) * size] or items[:size]


def fetch_recent_research_us(limit: int = 25) -> list[dict]:
    """최근 US 등급변경 — S&P 500 **로테이션**(하루 ~72종목 day-slice, 7일 1회전)
    + 7일 롤링 누적 store 로 전체 커버 유지. 옛 구조(매 refresh 500종목 일괄
    fetch)는 upgrades_downgrades + .info = 회당 ~1,000 야후 quote-API 콜 burst →
    cloud IP 하드차단 → 검색·상세 탭(.info) 동반 사망(2026-06-14 회귀). 로테이션
    으로 회당 ~144콜(72×2)로 bound — 야후가 회복돼 .info 탭이 정상 채워짐."""
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cutoff = (today - timedelta(days=7)).isoformat()
    rolling_f = cache_dir / "us_rolling.json"
    store: dict = {}
    last_fetch = 0.0
    if rolling_f.exists():
        try:
            _d = json.loads(rolling_f.read_text())
            _it = _d.get("items")
            store = _it if isinstance(_it, dict) else {}
            last_fetch = float(_d.get("last_fetch_ts") or 0)
        except Exception:
            store, last_fetch = {}, 0.0

    def _emit() -> list[dict]:
        rows = [v for v in store.values()
                if isinstance(v, dict) and v.get("date", "") >= cutoff]
        rows.sort(key=lambda x: x.get("date", ""), reverse=True)
        return rows[:limit]

    # 6h 내 이미 fetch 했으면 누적 store 그대로(버스트 방지)
    if store and (time.time() - last_fetch) < 6 * 3600:
        return _emit()
    # ⛔ 야후 quote-API 회로차단/정지 중이면 fetch 안 함(누적 store 유지) — 차단 회복
    # 보호(사용자 2026-06-14). 차단 중 6h 만료마다 재poke 하던 것 차단.
    from bot.finviz_client import (fast_info_ok, fast_info_trip,
                                   is_rate_limit_error, yf_paused)
    if yf_paused() or not fast_info_ok():
        return _emit()

    # universe = S&P 500 의 **오늘 슬라이스**만 (로테이션 — 전체 커버는 위 7일 누적
    # store 가 유지). CSV 실패 시 메가캡 30 폴백. 회당 ~72종목 → burst 0.
    _FALLBACK_30 = ["AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
                    "AVGO", "JPM", "V", "MA", "UNH", "HD", "PG", "JNJ",
                    "NFLX", "CRM", "AMD", "INTC", "BA", "LLY", "BRK-B",
                    "WMT", "COST", "ORCL", "ADBE", "MRK", "ABBV", "PEP", "KO"]
    try:
        from bot.finviz_client import _fetch_sp500_csv
        _sp_tks, _sp_names, _sp_inds = _fetch_sp500_csv()
        _sp = _sp_tks if (_sp_tks and len(_sp_tks) > 100) else _FALLBACK_30
    except Exception:
        _sp = _FALLBACK_30
    top_us = _rotation_slice(_sp, today.toordinal())   # 오늘 슬라이스(7일 1회전)
    results = []

    def _fetch_one(tk):
        try:
            t = yf.Ticker(tk)
            ud = t.upgrades_downgrades
            if ud is None or ud.empty:
                return []
            # 목표가(.info targetMeanPrice) 생략 — 검색·상세탭과 **동일 quote API**라
            # 슬라이스당 72 .info 콜이 야후 차단 가중(사용자 2026-06-14 '계속 블락').
            # 등급변경(firm/from/to)만 표시. 목표가는 상세 페이지 컨센서스 탭에 있음.
            tp = None
            # KR 탭과 동일 7일 윈도(사용자 2026-06-11) — 과거분 제외.
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            items = []
            for idx, row in ud.head(15).iterrows():
                d = str(idx.date()) if hasattr(idx, "date") else str(idx)[:10]
                if d < cutoff:
                    continue
                items.append({
                    "symbol": tk,
                    "firm": row.get("Firm", ""),
                    "to_grade": row.get("ToGrade", ""),
                    "from_grade": row.get("FromGrade", ""),
                    "target": tp,
                    "date": d,
                })
            # 종목당 최대 3건 — 일주일치 전체에서 최신순 3건, 초과분은
            # 기록하지 않음(사용자 2026-06-11 룰 확정). yfinance 정렬에
            # 기대지 않고 명시 정렬.
            items.sort(key=lambda x: x.get("date", ""), reverse=True)
            return items[:3]
        except Exception as exc:
            if is_rate_limit_error(exc):       # 차단 감지 → 회로차단 발동(전 소비처 backoff)
                fast_info_trip("research_us")
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_fetch_one, tk): tk for tk in top_us}
        for fut in as_completed(futs):
            results.extend(fut.result())

    # 이번 슬라이스 결과를 롤링 store 에 병합 + 7일 지난 항목 prune → 전체 커버 유지
    for _it in results:
        _k = f"{_it.get('symbol')}|{_it.get('date')}|{_it.get('firm')}"
        store[_k] = _it
    store = {k: v for k, v in store.items()
             if isinstance(v, dict) and v.get("date", "") >= cutoff}
    try:
        rolling_f.write_text(json.dumps(
            {"items": store, "last_fetch_ts": time.time()}, ensure_ascii=False))
    except Exception:
        pass
    return _emit()


def fetch_recent_research_intl(market: str, limit: int = 25) -> list[dict]:
    """JP/TW/CN/HK 최근 등급변경 — US 패턴(yfinance upgrades_downgrades) 일반화
    (사용자 2026-06-13 '다른나라들도 같은 조건 리서치액션, 보고 판단'). 산업
    peer 주요종목 universe(부하 bound 60). 한글명 번역(결과만). 6h 캐시.

    ⚠️ 커버리지 한계: yfinance 해외 애널리스트 등급변경 데이터가 얇아 결과가
    희소(빈 시장 가능)할 수 있음 — 이 trial 의 '판단' 대상. 그래서 윈도는 US
    7일 대신 30일로 넓힘. graceful — 실패/없음 시 빈 리스트."""
    if market not in _INTL_EARN_PEERS:
        return []
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"intl_{market}_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            if (time.time() - cache_file.stat().st_mtime) / 3600 < 6:
                return json.loads(cache_file.read_text())
        except Exception:
            pass
    # ⛔ 야후 quote-API 회로차단/정지 중이면 fetch 안 함 — **빈 캐시 기록(6h)** 으로
    # 재시도 폭주 차단(사용자 2026-06-14 '계속 블락' 근본원인: 빈 결과 미캐시 →
    # 매 regen 5분마다 240콜 재poke → 차단 영구화). 회복 후 다음 사이클에 정상 fetch.
    from bot.finviz_client import (fast_info_ok, fast_info_trip,
                                   is_rate_limit_error, yf_paused)
    if yf_paused() or not fast_info_ok():
        try:
            cache_file.write_text(json.dumps([], ensure_ascii=False))
        except Exception:
            pass
        return []
    universe = _intl_earnings_universe(market)[:60]   # 부하 bound
    if not universe:
        return []
    cutoff = (today - timedelta(days=30)).isoformat()

    def _one(item):
        tk = item[0]
        try:
            ud = yf.Ticker(tk).upgrades_downgrades
            if ud is None or ud.empty:
                return []
            items = []
            for idx, row in ud.head(10).iterrows():
                d = str(idx.date()) if hasattr(idx, "date") else str(idx)[:10]
                if d < cutoff:
                    continue
                items.append({"symbol": tk, "firm": row.get("Firm", ""),
                              "to_grade": row.get("ToGrade", ""),
                              "from_grade": row.get("FromGrade", ""),
                              "target": None, "date": d})
            items.sort(key=lambda x: x["date"], reverse=True)
            return items[:3]
        except Exception as exc:
            if is_rate_limit_error(exc):       # 차단 감지 → 회로차단 발동(전 소비처 backoff)
                fast_info_trip("research_intl")
            return []

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(_one, universe):
            results.extend(r)
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    results = results[:limit]
    # 한글명 — 결과만(희소) yfinance longName → chart_translate(Flash·영구캐시).
    if results:
        try:
            from bot.chart_translate import translate_titles_kr
            from bot.finviz_client import _fetch_display_names
            en = _fetch_display_names([r["symbol"] for r in results])
            uniq = sorted({n for n in en.values() if n})
            kr = translate_titles_kr(uniq) if uniq else {}
            for r in results:
                e = en.get(r["symbol"], "")
                r["name"] = (kr.get(e) or e) if e else r["symbol"]
        except Exception as exc:
            log.warning("intl research 한글명 (%s): %s", market, exc)
    # ⚠️ **빈 결과도 항상 캐시(6h)** — 미캐시 시 매 regen(5분) 재시도 폭주 = 야후
    # 차단 지속의 근본원인(사용자 2026-06-14). 결과 유무와 무관하게 기록.
    try:
        cache_file.write_text(json.dumps(results, ensure_ascii=False))
    except Exception:
        pass
    return results


# ── Main assembly ───────────────────────────────────────────────────

def fetch_market_snapshot() -> dict[str, Any]:
    """Fetch complete market snapshot for the landing page.
    Returns dict with keys: yf_data, fred_data, dollar_index, timestamp."""
    cache_dir = _CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "snapshot.json"
    if cache_file.exists():
        try:
            age = time.time() - cache_file.stat().st_mtime
            if age < _CACHE_TTL_SEC:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    log.info("market_overview: fetching fresh market snapshot")

    with ThreadPoolExecutor(max_workers=3) as pool:
        yf_fut = pool.submit(_fetch_yf_batch)
        fred_fut = pool.submit(_fetch_all_fred)
        yf_data = yf_fut.result()
        fred_data = fred_fut.result()

    # 달러인덱스 = 핵심지표 카드에서 제거(사용자 2026-06-14). 렌더 생략.
    dollar_idx = None

    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    kst = now_utc + timedelta(hours=9)
    ts = kst.strftime("%m.%d. %H:%M KST")

    result = {
        "yf": {k: v for k, v in yf_data.items()},
        "fred": fred_data,
        "dollar_index": dollar_idx,
        "ts": ts,
    }

    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return result


def _fetch_macro_safe() -> dict:
    """Macro snapshot wrapper — never raises (page must render without it)."""
    try:
        from bot.macro_snapshot import fetch_macro_snapshot
        return fetch_macro_snapshot()
    except Exception as exc:
        log.warning("market_overview: macro snapshot failed: %s", exc)
        return {}


def _cache_ts(p: Path) -> str:
    """캐시 파일 mtime → 'YYYY-MM-DD HH:MM' **명시적 KST** (서버 로컬타임
    의존 제거 — 사용자 정책: 모든 표기 한국시간). 부재 시 ''."""
    try:
        if p.exists():
            from datetime import timezone as _tz
            kst = _tz(timedelta(hours=9))
            return datetime.fromtimestamp(p.stat().st_mtime, tz=kst).strftime(
                "%Y-%m-%d %H:%M")
    except Exception:
        pass
    return ""


def _widget_data_ts() -> dict:
    """위젯별 '실제 적용' 데이터 시각 — 각 fetch 의 캐시 파일 mtime
    (사용자 2026-06-11: 업종등락처럼 'ts · 소스' 를 실적/리서치에도).
    fetch_all_market_data 의 futures 가 resolve 된 '뒤' 호출 — refetch 가
    일어났으면 mtime 이 방금 시각으로 갱신돼 있음."""
    today = date.today().isoformat()
    return {
        "earn_us": _cache_ts(_CACHE_DIR / "finnhub" / f"earnings_{today}.json"),
        "earn_kr": _cache_ts(_CACHE_DIR / "finnhub" / f"earnings_kr_{today}.json"),
        "res_kr": _cache_ts(_CACHE_DIR / "research" / f"kr_{today}.json"),
        "res_us": _cache_ts(_CACHE_DIR / "research" / f"us_{today}.json"),
    }


def fetch_all_market_data() -> dict[str, Any]:
    """Fetch everything needed for market.html."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        snap_fut = pool.submit(fetch_market_snapshot)
        # 다가오는 실적 윈도 최대화 (사용자 2026-06-10 '최대한 가져오기'):
        # 미국 14→60일(Finnhub 가 확정 발표일 ~4-8주 채움, 표는 100행 cap),
        # 한국 90일(DART IR + yfinance) 유지.
        earn_fut = pool.submit(fetch_earnings_calendar, 60)
        earn_kr_fut = pool.submit(fetch_earnings_calendar_kr, 90)
        # JP/TW/CN/HK 실적 — KR 패턴 일반화(사용자 2026-06-13 '실적빌드 다국가').
        # 산업 peer 맵 주요종목 universe, yfinance .calendar 6h 캐시(소스 TTL).
        earn_jp_fut = pool.submit(fetch_earnings_calendar_intl, "JP", 90)
        earn_tw_fut = pool.submit(fetch_earnings_calendar_intl, "TW", 90)
        earn_cn_fut = pool.submit(fetch_earnings_calendar_intl, "CN_A", 90)
        earn_hk_fut = pool.submit(fetch_earnings_calendar_intl, "HK", 90)
        # limit 은 7일치 전부 커버용 상한(사용자 2026-06-11 "limit 80?" —
        # 한국 종목 리포트는 주당 200+ 가능 → 넉넉히).
        kr_fut = pool.submit(fetch_recent_research_kr, 300)
        kr_ind_fut = pool.submit(fetch_recent_research_kr_industry, 150)
        kr_strat_fut = pool.submit(fetch_recent_research_kr_strategy, 150)
        us_fut = pool.submit(fetch_recent_research_us, 80)
        # JP/TW/CN/HK 리서치 액션 — yfinance 등급변경(사용자 2026-06-13 '보고
        # 판단'). 6h 캐시·peer 60 bound. 커버리지 얇으면 빈 결과(trial 판단용).
        res_jp_fut = pool.submit(fetch_recent_research_intl, "JP", 25)
        res_tw_fut = pool.submit(fetch_recent_research_intl, "TW", 25)
        res_cn_fut = pool.submit(fetch_recent_research_intl, "CN_A", 25)
        res_hk_fut = pool.submit(fetch_recent_research_intl, "HK", 25)
        macro_fut = pool.submit(_fetch_macro_safe)
        sector_fut = pool.submit(_fetch_sector_movers_safe)
        us_sector_fut = pool.submit(_fetch_us_sector_movers_safe)
        # JP/TW/CN/HK 업종 등락 — 섹터 ETF 합성(사용자 2026-06-13 Phase 1)
        jp_sector_fut = pool.submit(_fetch_etf_sector_safe, "JP")
        tw_sector_fut = pool.submit(_fetch_tw_sector_safe)   # TWSE 類股 우선
        cn_sector_fut = pool.submit(_fetch_etf_sector_safe, "CN_A")
        hk_sector_fut = pool.submit(_fetch_etf_sector_safe, "HK")
        deposit_fut = pool.submit(_fetch_deposit_safe)

        # 실적 병합 — 한국(yfinance) 먼저, 미국(Finnhub) 다음, JP/TW/CN/HK.
        # 각 그룹 날짜순. 대시보드가 접미사로 재필터해 탭 분리(병합 순서는
        # cosmetic). 사용자 정책: 한국이 되면 한국을 앞으로.
        _key = lambda e: e.get("date", "")
        _kr_e = sorted(earn_kr_fut.result() or [], key=_key)
        _us_e = sorted(earn_fut.result() or [], key=_key)
        _jp_e = sorted(earn_jp_fut.result() or [], key=_key)
        _tw_e = sorted(earn_tw_fut.result() or [], key=_key)
        _cn_e = sorted(earn_cn_fut.result() or [], key=_key)
        _hk_e = sorted(earn_hk_fut.result() or [], key=_key)
        earnings = _kr_e + _us_e + _jp_e + _tw_e + _cn_e + _hk_e

        return {
            "snapshot": snap_fut.result(),
            "earnings": earnings,
            "research_kr": kr_fut.result(),
            "research_kr_industry": kr_ind_fut.result(),
            "research_kr_strategy": kr_strat_fut.result(),
            "research_us": us_fut.result(),
            "research_jp": res_jp_fut.result(),
            "research_tw": res_tw_fut.result(),
            "research_cn": res_cn_fut.result(),
            "research_hk": res_hk_fut.result(),
            "macro": macro_fut.result(),
            "sector_movers": sector_fut.result(),
            "us_sector_movers": us_sector_fut.result(),
            "jp_sector_movers": jp_sector_fut.result(),
            "tw_sector_movers": tw_sector_fut.result(),
            "cn_sector_movers": cn_sector_fut.result(),
            "hk_sector_movers": hk_sector_fut.result(),
            "deposit": deposit_fut.result(),
            # futures resolve 후 → refetch 분 mtime 반영된 '실제 적용' 시각
            "widget_ts": _widget_data_ts(),
        }


def _fetch_sector_movers_safe() -> dict:
    try:
        from bot.naver_sector_client import fetch_sector_movers
        return fetch_sector_movers(top_n=10)
    except Exception as exc:
        log.warning("sector movers fetch error: %s", exc)
        return {"up": [], "down": [], "ts": ""}


def _fetch_us_sector_movers_safe() -> dict:
    """미국 업종 등락 TOP 10 — **네이버 업종(USA) 우선**(사용자 2026-06-14 개선점 A:
    데이터센터 IP Finviz 403 잦음 + 섹터ETF 폴백이 fast_info 트리거 → 네이버로).
    네이버 USA 업종맵(시총가중 등락, JP/CN/HK 와 동일 경로). 빈/실패 시 Finviz L3
    폴백(top_l3_movers). 둘 다 {up:[{name,pct}],down} 동형이라 렌더 동일."""
    try:
        from bot.naver_ranking_client import fetch_intl_sector_movers_naver
        nv = fetch_intl_sector_movers_naver("US", top_n=10)
        if nv.get("up") or nv.get("down"):
            return nv
    except Exception as exc:
        log.warning("naver US 업종등락 → Finviz 폴백: %s", exc)
    try:
        from bot.finviz_client import top_l3_movers
        return top_l3_movers(top_n=10)
    except Exception as exc:
        log.warning("us sector movers fetch error: %s", exc)
        return {"up": [], "down": [], "ts": ""}


def _fetch_etf_sector_safe(market: str) -> dict:
    """JP/CN_A/HK 업종 등락 — **네이버 업종 우선**(시총가중 등락, 사용자 2026-06-14
    '업종등락 네이버'), 빈/실패 시 섹터 ETF 합성(yfinance) 폴백. graceful."""
    if market in ("JP", "CN_A", "HK"):
        try:
            from bot.naver_ranking_client import fetch_intl_sector_movers_naver
            nv = fetch_intl_sector_movers_naver(market, top_n=10)
            if nv.get("up") or nv.get("down"):
                return nv
        except Exception as exc:
            log.warning("naver sector movers (%s) → ETF 폴백: %s", market, exc)
    try:
        from bot.etf_sector_client import fetch_sector_movers_etf
        return fetch_sector_movers_etf(market, top_n=10)
    except Exception as exc:
        log.warning("etf sector movers fetch error (%s): %s", market, exc)
        return {"up": [], "down": [], "ts": "", "source": ""}


def _fetch_tw_sector_safe() -> dict:
    """TW 업종 등락 — TWSE 類股 지수(~30 업종, 풍부) 우선, 실패 시 ETF 합성
    폴백(사용자 2026-06-13 Phase 2, TWSE 200 검증)."""
    try:
        from bot.twse_client import fetch_tw_sector_movers
        r = fetch_tw_sector_movers(top_n=10)
        if r.get("up") or r.get("down"):
            return r
    except Exception as exc:
        log.warning("twse sector fetch error: %s", exc)
    return _fetch_etf_sector_safe("TW")


def _fetch_deposit_safe() -> dict:
    try:
        from bot.naver_sector_client import fetch_deposit
        return fetch_deposit()
    except Exception as exc:
        log.warning("deposit fetch error: %s", exc)
        return {}
