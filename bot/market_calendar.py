"""거래일(trading-day) 캘린더 — 휴장일까지 반영한 만기 계산.

`auto_resolve` 의 5거래일/N캘린더일 outcome 만기 계산은 지금 "yfinance 가
영업일만 반환 → target 이 weekend/holiday 면 다음 영업일 close" 휴리스틱에
의존한다. 대부분 맞지만 **readiness gate**(언제 outcome 을 시도할지)는
`holding_days + 3` 같은 캘린더-일 근사라, 윈도 안에 공휴일이 끼면 너무 일찍
발동해 부분(4일) 결과를 최종으로 기록하거나, 없으면 하루 늦는다.

이 모듈은 `exchange_calendars`(무료·무키·전 시장: XNYS/XKRX/XTKS/XTAI/
XSHG/XHKG)로 **거래일 N일 뒤**를 정확히 계산해 그 gate 를 정밀화한다.

graceful: 라이브러리 부재(샌드박스)·미지원 시장·어떤 예외든 **None** 반환 →
호출부가 기존 캘린더-일 휴리스틱으로 폴백(회귀 0). universal — 전 시장 동일.
실측 검증은 VM(exchange_calendars + pandas 설치)에서.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

# 우리 detect_market() 코드 → exchange_calendars 코드.
_MARKET_TO_XCAL = {
    "US": "XNYS",
    "KR": "XKRX",
    "JP": "XTKS",
    "TW": "XTAI",
    "CN_A": "XSHG",
    "HK": "XHKG",
}

# get_calendar() 는 비싸므로(수년치 세션 생성) 모듈 캐시.
_CAL_CACHE: dict = {}
_FAILED: set = set()   # 한 번 실패한 코드는 재시도 안 함(로그 도배 방지)


def _calendar(market: str):
    """market → ExchangeCalendar 또는 None(graceful)."""
    code = _MARKET_TO_XCAL.get(market)
    if not code or code in _FAILED:
        return None
    if code in _CAL_CACHE:
        return _CAL_CACHE[code]
    try:
        import exchange_calendars as xcals
        cal = xcals.get_calendar(code)
        _CAL_CACHE[code] = cal
        return cal
    except Exception as exc:
        _FAILED.add(code)
        log.info("market_calendar: %s unavailable (%s) — falling back to heuristic", code, exc)
        return None


def add_trading_days(market: str, date_str: str, n: int) -> Optional[str]:
    """`date_str`(YYYY-MM-DD, 거래일 가정)의 세션 기준 **거래일 n일 뒤** 날짜.

    예: 금요일 + 5거래일 = 다음 주 금요일(공휴일 없으면), 윈도에 공휴일이
    끼면 그만큼 뒤로. n=0 이면 date_str 자신(또는 그 이후 첫 세션). graceful
    None — 라이브러리/시장 미지원·예외 시."""
    cal = _calendar(market)
    if cal is None:
        return None
    try:
        import pandas as pd
        start = pd.Timestamp(date_str)
        # 거래일 n 개를 담기 충분한 캘린더-일 윈도(공휴일 다발 가정 여유).
        end = start + pd.Timedelta(days=n * 2 + 20)
        sessions = cal.sessions_in_range(
            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )
        if sessions is None or len(sessions) == 0:
            return None
        # sessions_in_range 는 start 이상 세션을 오름차순 반환 → [0] 이 day0
        # (date_str 이 세션이면 자기 자신). [n] 이 거래일 n일 뒤.
        if n >= len(sessions):
            return None   # 윈도가 짧음(버퍼상 거의 없음) → 폴백
        return sessions[n].strftime("%Y-%m-%d")
    except Exception as exc:
        log.debug("market_calendar.add_trading_days(%s,%s,%d) failed: %s",
                  market, date_str, n, exc)
        return None


def future_sessions(market: str, date_str: str, n: int) -> Optional[list]:
    """`date_str` **다음** 거래일부터 n개 세션(YYYY-MM-DD 오름차순) — 한 번의
    `sessions_in_range` 로 뽑는다(add_trading_days 를 n 번 호출하면 같은 윈도를
    n 번 계산).

    용도: 일목균형표 선행스팬은 26봉 **앞**에 그리는데 그 자리엔 아직 캔들이
    없어 시간축 값을 만들어 줘야 한다(bot/chart_data.py). graceful None —
    라이브러리/시장 미지원 시 호출부가 주5일 근사로 폴백."""
    cal = _calendar(market)
    if cal is None or n <= 0:
        return None
    try:
        import pandas as pd
        start = pd.Timestamp(date_str) + pd.Timedelta(days=1)
        # 거래일 n 개를 담기 충분한 캘린더-일 윈도(연휴 다발 가정 여유).
        end = start + pd.Timedelta(days=n * 2 + 20)
        sessions = cal.sessions_in_range(
            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )
        if sessions is None or len(sessions) < n:
            return None      # 윈도 부족(버퍼상 거의 없음) → 폴백
        return [s.strftime("%Y-%m-%d") for s in sessions[:n]]
    except Exception as exc:
        log.debug("market_calendar.future_sessions(%s,%s,%d) failed: %s",
                  market, date_str, n, exc)
        return None


def next_trading_day(market: str, date_str: str) -> Optional[str]:
    """`date_str` **이상** 첫 거래일(YYYY-MM-DD). date_str 이 세션이면 자기
    자신. graceful None."""
    cal = _calendar(market)
    if cal is None:
        return None
    try:
        import pandas as pd
        start = pd.Timestamp(date_str)
        end = start + pd.Timedelta(days=25)   # 다음 세션 찾기 충분
        sessions = cal.sessions_in_range(
            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )
        if sessions is None or len(sessions) == 0:
            return None
        return sessions[0].strftime("%Y-%m-%d")
    except Exception as exc:
        log.debug("market_calendar.next_trading_day(%s,%s) failed: %s",
                  market, date_str, exc)
        return None


def is_trading_day(market: str, date_str: str) -> Optional[bool]:
    """date_str 이 그 시장의 거래일인가. graceful None(판단 불가)."""
    cal = _calendar(market)
    if cal is None:
        return None
    try:
        import pandas as pd
        return bool(cal.is_session(pd.Timestamp(date_str)))
    except Exception:
        return None
