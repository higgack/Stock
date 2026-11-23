"""오늘 휴장 표시 — 메인 대시보드 지수 카드 · 업종 등락 위젯(한국·미국·아시아) 공용.

사용자 2026-11-23(일본 근로감사의 날): "휴장일때는 휴장이라는 표시를 해줘" — 휴장한 시장의
카드는 직전 거래일 종가·등락을 그대로 보여 주고, 업종 위젯은 **우리가 받아 온 시각**(오늘)을
찍어 휴장인지 수집이 늦은 건지 화면만으로 못 가른다(#43·#52).

판정은 그 거래소 **현지 날짜**가 평일인데 거래소 달력(exchange_calendars)에 세션이 없을 때다.
- 주말은 표시하지 않는다 — 모든 시장이 같이 쉬어 헷갈릴 일이 없고, 매주 전 카드에 배지가
  붙으면 배지가 아무것도 안 말한다(#25·#260).
- 달력이 그 날짜를 모르면(범위 밖·라이브러리 없음) **표시하지 않는다** — 모르는 것을 휴장이라
  말하지 않는다(#165).
- 지수 선물(CME)·운임지수·환율·원자재는 거래소 휴장과 거래 시간이 따로 놀아 매핑하지 않는다.
"""
from __future__ import annotations

import html as _html
import logging
import threading
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

# 카드 심볼(market_overview CARD_*) → 거래소 달력 코드. 심천은 상해와 같은 휴장일이라 XSHG
# (exchange_calendars 에 심천 달력이 따로 없다). 유로 스톡스 50 은 독일(XETR) 달력을 쓴다.
SYMBOL_XCAL = {
    "nvd:KOSPI": "XKRX", "nvd:KOSDAQ": "XKRX",
    "nvi:.N225": "XTKS",
    "nvi:.SSEC": "XSHG", "nvi:.SZSC": "XSHG",
    "nvi:.HSI": "XHKG",
    "nvi:.TWII": "XTAI",
    "nvi:.KLSE": "XKLS",
    "nvi:.JKSE": "XIDX",
    "nvi:.BSESN": "XBOM",
    "nvi:.INX": "XNYS", "nvi:.IXIC": "XNYS", "nvi:.DJI": "XNYS", "nvi:.SOX": "XNYS",
    "nvi:.STOXX50E": "XETR", "nvi:.GDAXI": "XETR", "nvi:.FTSE": "XLON",
    "nvi:.FCHI": "XPAR", "nvi:.FTMIB": "XMIL", "nvi:.AEX": "XAMS",
}
# 업종 등락 위젯의 시장 → 달력 코드.
MARKET_XCAL = {"KR": "XKRX", "US": "XNYS", "JP": "XTKS", "CN": "XSHG", "HK": "XHKG", "TW": "XTAI"}
_EXCHANGE = {
    "XKRX": "한국거래소", "XNYS": "미국 증시", "XTKS": "도쿄거래소", "XSHG": "중국 증시",
    "XHKG": "홍콩거래소", "XTAI": "대만거래소", "XKLS": "말레이시아거래소",
    "XIDX": "인도네시아거래소", "XBOM": "인도 증시", "XETR": "독일 증시",
    "XLON": "런던거래소", "XPAR": "파리 증시", "XMIL": "밀라노 증시", "XAMS": "암스테르담 증시",
}

_CAL: dict = {}
_OUT_OF_RANGE: set = set()
_LOCK = threading.Lock()


def xcal_for_symbol(tk: str) -> str | None:
    """카드 심볼의 달력 코드. ETF(`nve:XL*`)는 미국 증시."""
    if tk in SYMBOL_XCAL:
        return SYMBOL_XCAL[tk]
    if tk.startswith("nve:"):
        return "XNYS"
    return None


def _calendar(code: str):
    """달력은 무겁다(첫 빌드 0.1~3초) — 락 **밖에서** 만들어 동시 렌더가 줄서지 않게 한다(독립 리뷰).
    두 스레드가 같이 만들면 먼저 넣은 쪽이 이긴다(같은 달력이라 무해)."""
    with _LOCK:
        if code in _CAL:
            return _CAL[code]
    try:
        import exchange_calendars as xc
        cal = xc.get_calendar(code)
    except Exception as exc:                                   # noqa: BLE001
        log.info("market_holiday: %s 달력 없음(%s) — 휴장 표시 생략", code, exc)
        cal = None
    with _LOCK:
        return _CAL.setdefault(code, cal)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def holiday(code: str | None, now: datetime | None = None) -> dict | None:
    """그 거래소가 **현지 오늘** 평일 휴장이면 {date, prev, exchange}, 아니면 None.

    prev = 직전 거래일(화면 값이 기준하는 날). 모르면 None(달력 없음·범위 밖·예외)."""
    if not code:
        return None
    cal = _calendar(code)
    if cal is None:
        return None
    try:
        import pandas as pd
        from zoneinfo import ZoneInfo
        t = now or _now()
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        local = t.astimezone(ZoneInfo(str(cal.tz))).date()
        if local.weekday() >= 5:
            return None
        ts = pd.Timestamp(local)
        if not (cal.first_session <= ts <= cal.last_session):
            if code not in _OUT_OF_RANGE:                      # 한 번은 말한다 — 조용히 사라지지 않게
                _OUT_OF_RANGE.add(code)
                log.warning("market_holiday: %s 달력이 %s 를 모른다(마지막 %s) — 휴장 표시 생략 · "
                            "exchange_calendars 갱신 필요", code, local, cal.last_session.date())
            return None
        if cal.is_session(ts):
            return None
        prev = cal.date_to_session(ts, "previous").date()
    except Exception as exc:                                   # noqa: BLE001
        log.info("market_holiday: %s 판정 실패(%s) — 휴장 표시 생략", code, exc)
        return None
    return {"date": local, "prev": prev, "exchange": _EXCHANGE.get(code, code)}


def _md(d: date) -> str:
    return f"{d:%m-%d}"


def row_badge(code: str | None, now: datetime | None = None) -> str:
    """지수 카드 한 줄의 라벨 옆 배지 — 휴장이 아니면 빈 문자열."""
    h = holiday(code, now)
    if not h:
        return ""
    tip = f"오늘({_md(h['date'])}) {h['exchange']} 휴장 — 마지막 거래일은 {_md(h['prev'])}"
    return f' <span class="hol-badge" title="{_html.escape(tip)}">휴장</span>'


def section_note(market: str, now: datetime | None = None) -> str:
    """업종 등락 위젯 머리의 한 줄 — 휴장이 아니면 빈 문자열.

    위젯 시각은 **우리가 받아 온 시각**(오늘)이라 그것만으론 오늘 값처럼 읽힌다 — 그래서
    배지만이 아니라 마지막 거래일을 글로 적는다. '아래가 그날 값' 이라고는 단정하지 않는다 —
    원천이 낡았으면 더 옛 값일 수 있고 그건 저장분 표시가 따로 말한다(#165)(#228 사유는 보이는 줄에)."""
    h = holiday(MARKET_XCAL.get(market), now)
    if not h:
        return ""
    return (f'<span class="hol-badge">오늘({_md(h["date"])}) 휴장</span>'
            f'<span class="hol-note">{_html.escape(h["exchange"])} 휴장 — 오늘은 새 등락이 없습니다 · '
            f'마지막 거래일 {_md(h["prev"])}</span>')
