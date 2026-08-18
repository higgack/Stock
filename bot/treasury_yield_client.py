"""미 재무부 일별 국채 수익률곡선 — FRED 보다 **하루 빠른** 원천.

⚠️ 왜 필요한가(2026-08-18 실측): FRED `DGS*` 는 D일 값을 **D+1** 에 올린다.
화요일 22:51 KST 에 FRED 의 마지막 관측이 **금요일(8-14)** 이었다 — 월요일
값이 아직 없다. 미 재무부는 같은 값을 **당일 15:30 ET** 에 공표하므로
영업일 하나만큼 빠르다. 사용자 요구: "금리가 매우 중요, 가장 최신을 빠르게".

⚠️ **이 모듈만으로는 화면에 쓰지 않는다.** 필드명·XML 구조를 내가 외워서
쓰면 틀린 금리가 화면에 올라간다(실수 #12 '사전지식 stale'). 그래서:
  · 태그 이름을 **여러 철자**로 받아들이고,
  · 값이 상식 범위(0~20%)를 벗어나면 버리고,
  · 최종 판정은 **FRED 와 겹치는 날짜의 값이 0.10%p 이내로 일치**하는지로
    한다(호출부 가드). 필드를 잘못 집으면 만기가 달라 0.10%p 로는 절대
    안 맞는다 — 이게 검산이다.

읽기 전용·LLM 0·₩0·키 불요.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date

log = logging.getLogger("bot.treasury_yield")

_URL = ("https://home.treasury.gov/resource-center/data-chart-center/"
        "interest-rates/pages/xml?data=daily_treasury_yield_curve"
        "&field_tdr_date_value_month={ym}")

# FRED 시리즈 → 재무부 XML 태그 후보(철자 변형 허용).
_FIELDS = {
    "DGS2": ("BC_2YEAR", "BC_2Y"),
    "DGS10": ("BC_10YEAR", "BC_10Y"),
    "DGS30": ("BC_30YEAR", "BC_30Y", "BC_30YEARDISPLAY"),
}
_DATE_TAGS = ("NEW_DATE", "Date")


def _num(s: str) -> float | None:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if 0.0 <= v <= 20.0 else None      # 상식 범위 밖은 버린다


_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_SEC = 1800.0          # 30분 — 재무부는 하루 한 번(15:30 ET) 갱신


def fetch_daily_curve(ym: str | None = None) -> dict[str, dict[str, float]]:
    """{'YYYY-MM-DD': {'DGS2': 4.17, ...}} — 실패 시 빈 dict(graceful).

    ⚠️ 30분 메모리 캐시. 이 함수는 시리즈마다 불리므로(2Y·10Y·30Y) 캐시가
    없으면 한 렌더에 같은 XML 을 세 번 받는다."""
    import requests
    ym = ym or date.today().strftime("%Y%m")
    _hit = _CACHE.get(ym)
    if _hit and time.time() - _hit[0] < _TTL_SEC:
        return _hit[1]
    try:
        r = requests.get(_URL.format(ym=ym), timeout=12)
        r.raise_for_status()
        xml = r.text
    except Exception as exc:
        log.info("treasury: fetch %s failed: %s", ym, exc)
        return {}

    out: dict[str, dict[str, float]] = {}
    # <entry> 단위로 자른다. 태그 접두사(d:/m:)는 무시.
    for chunk in re.split(r"<entry[ >]", xml)[1:]:
        d = None
        for tag in _DATE_TAGS:
            m = re.search(rf"<(?:\w+:)?{tag}[^>]*>([^<]+)<", chunk)
            if m:
                d = m.group(1)[:10]
                break
        if not d or not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            continue
        row: dict[str, float] = {}
        for sid, tags in _FIELDS.items():
            for tag in tags:
                m = re.search(rf"<(?:\w+:)?{tag}[^>]*>([^<]*)<", chunk)
                if m:
                    v = _num(m.group(1))
                    if v is not None:
                        row[sid] = v
                    break
        if row:
            out[d] = row
    _CACHE[ym] = (time.time(), out)
    return out


def fresher_than(fred_last_date: str, fred_last_value: float, sid: str,
                 tol: float = 0.10) -> tuple[str, float] | None:
    """FRED 보다 **새 날짜**가 있고, 겹치는 날 값이 `tol`(%p) 이내로 일치하면
    (날짜, 값)을 돌려준다. 아니면 None — 그 경우 호출부는 FRED 를 그대로 쓴다.

    ⚠️ 겹치는 날 검산이 핵심이다. 태그를 잘못 집으면(2년물 자리에 1개월물)
    같은 날 값이 %p 단위로 어긋나므로 여기서 걸린다."""
    curve = fetch_daily_curve()
    if not curve:
        return None
    same = (curve.get(fred_last_date) or {}).get(sid)
    if same is None or abs(same - fred_last_value) > tol:
        if same is not None:
            log.warning("treasury: %s %s 값 불일치 (재무부 %.3f vs FRED %.3f) "
                        "— 필드 오집 의심, 사용 안 함", sid, fred_last_date,
                        same, fred_last_value)
        return None
    newer = [(d, r[sid]) for d, r in curve.items()
             if sid in r and d > fred_last_date]
    if not newer:
        return None
    newer.sort()
    return newer[-1]
