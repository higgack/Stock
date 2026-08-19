"""발표지표의 **공표 주기·통상 지연** 규약 — "이 숫자, 늦은 건가?"의 단일 소스.

⚠️ 왜 필요한가(사용자 2026-08-18: "실시간으로 가져오지 않는 지표가 최신 것을
제때제때 잘 가져오는지 꼼꼼히 확인해줘"). 지금까지 카드는 `(2개월 전)` 처럼
**경과만** 찍었다. 경상수지가 2개월 전인 건 정상이고 수출이 2개월 전인 건
지연인데, 화면은 둘을 똑같이 보여줘서 사람이 매번 원천 공표일정을 외워야
했다 — 그래서 아무도 안 봤다.

여기 규약을 두고 **기대 최신 관측기간**을 계산해 실제 관측과 비교한다.
`pub_lag_days` = 관측기간이 **끝난 뒤** 공표까지 통상 걸리는 일수.
`GRACE_DAYS` 만큼 여유를 더 준 뒤에도 뒤처지면 '지연 의심'.

네트워크·키 불요(순수 함수) — 화면 배지와 감사 프로브가 **같은 표**를 쓴다.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Optional

_CADENCE_VER = 1

# 공표 일정에 여유(주말·공휴일·원천 수록 지연). 이 안이면 배지 없음.
GRACE_DAYS = 4

# id → (freq, pub_lag_days, 근거)
#   freq: "D" 영업일 · "W" 주간 · "M" 월간 · "Q" 분기 · "E" 이벤트(정기공표 없음)
#   id 는 ECOS 시리즈 키 또는 FRED series_id — 두 네임스페이스가 겹치지 않는다.
CADENCE: dict[str, tuple[str, int, str]] = {
    # ── ECOS (한국은행) ─────────────────────────────────────────────
    "base_rate": ("E", 0, "금통위 결정 시에만 변한다 — 오래돼도 정상"),
    "kr3y": ("D", 1, "시장금리 일별, 익영업일 수록"),
    "kr10y": ("D", 1, "시장금리 일별, 익영업일 수록"),
    "cpi_idx": ("M", 5, "통계청 익월 초 발표 → ECOS 수록"),
    "export_amt": ("M", 20, "관세청 통관 확정 익월 15일 전후"),
    "import_amt": ("M", 20, "관세청 통관 확정 익월 15일 전후"),
    "current_account": ("M", 40, "국제수지 익익월 초"),
    "fx_reserve": ("M", 5, "익월 초 발표"),
    "kr_gdp": ("Q", 30, "속보치 분기 종료 후 약 4주"),
    # ── FRED (미국) ────────────────────────────────────────────────
    "DGS2": ("D", 1, "국채 수익률 일별 (재무부 당일 → FRED D+1)"),
    "DGS10": ("D", 1, "국채 수익률 일별 (재무부 당일 → FRED D+1)"),
    "DGS30": ("D", 1, "국채 수익률 일별 (재무부 당일 → FRED D+1)"),
    "T10Y2Y": ("D", 1, "위 둘의 차 — 같은 일정"),
    "BAMLH0A0HYM2": ("D", 1, "ICE BofA 하이일드 OAS, 영업일"),
    "BAMLC0A0CM": ("D", 1, "ICE BofA IG OAS, 영업일"),
    "ICSA": ("W", 5, "주간 신규 실업수당, 목요일 공표(주 종료=토)"),
    "CPIAUCSL": ("M", 15, "익월 중순 BLS"),
    "PPIFIS": ("M", 15, "익월 중순 BLS"),
    "PPIACO": ("M", 15, "익월 중순 BLS"),
    "PCEPILFE": ("M", 30, "익월 말 BEA"),
    "UNRATE": ("M", 8, "익월 첫 금요일 고용보고서"),
    "RSAFS": ("M", 17, "익월 중순 Census"),
    "JTSJOL": ("M", 40, "JOLTS — 관측월 +약 6주"),
    "A191RL1Q225SBEA": ("Q", 30, "GDP 속보치, 분기 종료 +약 4주"),
    # ── 유동성 보드(bot/fred_boards_catalog.LIQ_SERIES) ─────────────
    # 사용자 2026-08-19 "유동성 대시보드 항목 모두 확인 — 최신 데이터를
    # 제때 불러오는지". 주간(W) 시리즈가 많다 — Fed H.4.1 은 목요일 공표.
    "M2SL": ("M", 30, "M2 월간, 익월 말 공표"),
    "M1SL": ("M", 30, "M1 월간, 익월 말 공표"),
    "RMFSL": ("M", 30, "리테일 MMF 월간, 익월 말"),
    "FEDFUNDS": ("M", 5, "실효 FF 월평균, 익월 초"),
    "ECBMRRFR": ("M", 5, "ECB 정책금리 — 변경 시에만 움직인다"),
    "EFFR": ("D", 1, "실효 FF 일별, 익영업일"),
    "IORB": ("D", 1, "지준부리, 익영업일"),
    "SOFR": ("D", 1, "SOFR 일별, 익영업일"),
    "WALCL": ("W", 2, "Fed H.4.1 주간(수 기준·목 공표)"),
    "WTREGEN": ("W", 2, "TGA 주간평균, H.4.1 동반"),
    "WRESBAL": ("W", 2, "지준잔고 주간, H.4.1 동반"),
    "WLCFLPCL": ("W", 2, "할인창구 주간, H.4.1 동반"),
    "RRPONTSYD": ("D", 1, "ON RRP 일별"),
    "BOGMBASE": ("M", 20, "본원통화 월간"),
    "JPNASSETS": ("M", 25, "BOJ 총자산 월간"),
    "ECBASSETSW": ("W", 7, "ECB 주간 재무제표"),
    "T10Y3M": ("D", 1, "장단기차 일별"),
    "DFII10": ("D", 1, "10Y TIPS 실질금리 일별"),
    "T10YIE": ("D", 1, "10Y 기대인플레 일별"),
    "BAA10Y": ("D", 1, "Baa-10Y 스프레드 일별"),
    "BAMLH0A3HYC": ("D", 1, "CCC 이하 HY OAS 일별"),
    "BAMLEMHBHYCRPIOAS": ("D", 1, "EM HY OAS 일별"),
    "STLFSI4": ("W", 7, "세인트루이스 금융스트레스 주간"),
    "NFCI": ("W", 7, "시카고 연은 NFCI 주간"),
    "ANFCI": ("W", 7, "조정 NFCI 주간"),
    "DTWEXBGS": ("D", 3, "달러지수(광범위) 일별"),
    "VIXCLS": ("D", 1, "VIX 종가 일별"),
    "CBBTCUSD": ("D", 1, "비트코인 일별"),
    "M2V": ("Q", 75, "통화유통속도 분기, GDP 확정 후"),
    "M1V": ("Q", 75, "통화유통속도 분기, GDP 확정 후"),
    "TOTBKCR": ("W", 10, "상업은행 총자산 주간(H.8)"),
    # ⚠️ BUSLOANS 는 **월간** 시리즈다(주간 H.8 은 별도 계열) — 관측일이
    # 월초로 오는데 주간으로 잡아 매번 '지연' 오탐이 났다(2026-08-19 실측).
    "BUSLOANS": ("M", 20, "상업·산업대출 월간(H.8), 익월 중순"),
    "COMPOUT": ("W", 10, "기업어음 잔액 주간"),
    "DPSACBW027SBOG": ("W", 10, "은행 예금 주간(H.8)"),
    "DEXKOUS": ("D", 3, "원/달러 일별"),
    "DEXJPUS": ("D", 3, "엔/달러 일별"),
    "DEXCHUS": ("D", 3, "위안/달러 일별"),
    "DEXUSEU": ("D", 3, "달러/유로 일별"),
    "DEXUSUK": ("D", 3, "달러/파운드 일별"),
    "DEXSZUS": ("D", 3, "프랑/달러 일별"),
    "TRESEGCNM052N": ("M", 45, "중국 보유 미 국채(TIC), 관측월 +약 6주"),
    "FDHBFIN": ("Q", 75, "외국인 보유 연방부채 분기"),
    # ECOS / AKShare
    "ECOS:M2": ("M", 40, "한국 M2 평잔, 익익월 중순"),
    "ECOS:BASE": ("E", 0, "한은 기준금리 — 금통위 때만"),
    "ECOS:KR10Y": ("D", 1, "국고채 10년 일별"),
    "ECOS:FXRESERVE": ("M", 5, "외환보유액 익월 초"),
    "AK:LPR1Y": ("M", 3, "중국 LPR 매월 20일 고시"),
    "IRSTCI01JPM156N": ("M", 40, "일본 콜금리 월간(OECD 경유 지연)"),
}


def _kst_today() -> date:
    return (datetime.utcnow() + timedelta(hours=9)).date()


def _eom(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _prev_weekday(d: date) -> date:
    while d.weekday() >= 5:            # 토(5)·일(6)
        d -= timedelta(days=1)
    return d


def parse_period_end(raw: str, freq: str) -> Optional[date]:
    """관측 라벨을 그 **기간의 끝 날짜**로. 못 읽으면 None."""
    s = (raw or "").strip()
    if not s:
        return None
    try:
        if len(s) == 6 and s[4] in ("Q", "q"):            # 2026Q2
            y, q = int(s[:4]), int(s[5])
            return _eom(y, q * 3) if 1 <= q <= 4 else None
        if len(s) == 8 and s.isdigit():                   # 20260817 (ECOS 일별)
            d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        elif len(s) == 6 and s.isdigit():                 # 202607 (ECOS 월간)
            return _eom(int(s[:4]), int(s[4:]))
        elif len(s) >= 10 and s[4] == "-" and s[7] == "-":  # 2026-08-17
            d = date(int(s[:4]), int(s[5:7]), int(s[8:10]))
        elif len(s) == 7 and s[4] == "-":                 # 2026-07
            return _eom(int(s[:4]), int(s[5:7]))
        else:
            return None
    except (ValueError, IndexError):
        return None
    if freq == "M":
        return _eom(d.year, d.month)      # FRED 월간은 관측월 1일로 온다
    if freq == "Q":
        return _eom(d.year, ((d.month - 1) // 3) * 3 + 3)
    return d


def expected_period_end(freq: str, lag: int, today: Optional[date] = None,
                        grace: int = GRACE_DAYS) -> Optional[date]:
    """오늘까지 **나와 있어야 할** 가장 최근 관측기간의 끝. 이벤트성은 None."""
    t = today or _kst_today()
    cutoff = t - timedelta(days=lag + grace)
    if freq == "E":
        return None
    if freq == "D":
        return _prev_weekday(cutoff)
    if freq == "W":                        # 주 종료 = 토요일
        d = cutoff
        while d.weekday() != 5:
            d -= timedelta(days=1)
        return d
    if freq == "M":
        y, m = t.year, t.month
        for _ in range(24):
            e = _eom(y, m)
            if e <= cutoff:
                return e
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        return None
    if freq == "Q":
        y, q = t.year, (t.month - 1) // 3 + 1
        for _ in range(12):
            e = _eom(y, q * 3)
            if e <= cutoff:
                return e
            q -= 1
            if q == 0:
                y, q = y - 1, 4
        return None
    return None


def _periods_behind(actual: date, expected: date, freq: str) -> int:
    if actual >= expected:
        return 0
    if freq == "M":
        return (expected.year - actual.year) * 12 + (expected.month - actual.month)
    if freq == "Q":
        return ((expected.year - actual.year) * 12
                + (expected.month - actual.month)) // 3
    if freq == "W":
        return (expected - actual).days // 7
    return (expected - actual).days          # D — 달력일수(영업일 근사)


def judge(sid: str, raw: str, today: Optional[date] = None) -> Optional[dict]:
    """{'freq','lag','why','actual','expected','behind','stale'} 또는 None
    (규약 미등록·라벨 판독 불가·이벤트성). None = **판정 안 함**이지 정상이
    아니다 — 호출부는 배지를 안 띄우되 감사 프로브는 '규약 없음'으로 찍는다."""
    spec = CADENCE.get(sid)
    if not spec:
        return None
    freq, lag, why = spec
    actual = parse_period_end(raw, freq)
    expected = expected_period_end(freq, lag, today)
    if actual is None or expected is None:
        return {"freq": freq, "lag": lag, "why": why, "actual": actual,
                "expected": expected, "behind": 0, "stale": False}
    behind = _periods_behind(actual, expected, freq)
    return {"freq": freq, "lag": lag, "why": why, "actual": actual,
            "expected": expected, "behind": behind, "stale": behind > 0}
