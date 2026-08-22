"""SEC EDGAR **EPS 이력** — 미국(및 ADR) PER 밴드용 장기 재료.

⚠️ 왜 EDGAR 인가(2026-08-22). yfinance 는 분기 손익을 8개(≈2년)만 준다 —
그걸로 만든 '밴드'는 최고·최저라는 이름값을 못 한다. EDGAR `companyconcept`
는 **공식·무료·무키**로 10년 이상의 희석 EPS 를 준다(같은 host 를 이미
`edgar_client` 가 쓰고 있어 운영에서 동작이 확인된 경로다).

엔드포인트:
  GET https://data.sec.gov/api/xbrl/companyconcept/CIK{10자리}/us-gaap/{태그}.json
  → {"units": {"USD/shares": [{start,end,fy,fp,form,val,filed,frame}, ...]}}

⚠️ **기간 길이로 분기/연간을 가른다.** `fp` 는 10-K 에도 FY 로 붙고 20-F 는
규약이 또 다르다 — 이름이 아니라 `start~end` 일수로 판정한다(#25 '능력은
이름이 아니라 실측으로').

⚠️ 같은 `end` 가 여러 번 온다(정정·후속 보고서). **가장 늦게 접수된 것**을
쓴다 — 오래된 값이 이기면 화면이 조용히 낡는다.
"""

from __future__ import annotations

import logging

log = logging.getLogger("bot.edgar_eps")

_CONCEPT = ("https://data.sec.gov/api/xbrl/companyconcept/"
            "CIK{cik}/us-gaap/{tag}.json")
# 앞이 없으면 뒤를 쓴다. 희석이 정본이고, 없으면 기본주당이익.
_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")
_UNITS = "USD/shares"
_TTL_H = 24.0
# 분기 = 80~100일, 연간 = 350~380일. IFRS 반기 보고서(≈180일)는 어느 쪽도
# 아니므로 버린다 — 반기를 분기로 섞으면 TTM 이 통째로 틀린다.
_Q_DAYS = (80, 100)
_A_DAYS = (350, 380)


def _span_days(a: str, b: str):
    import datetime as _dt
    try:
        return (_dt.date.fromisoformat(b[:10])
                - _dt.date.fromisoformat(a[:10])).days
    except Exception:                                          # noqa: BLE001
        return None


def _rows(cik: str, tag: str) -> list[dict]:
    from bot.edgar_client import _get
    try:
        r = _get(_CONCEPT.format(cik=cik, tag=tag), timeout=20)
        js = r.json()
    except Exception as exc:                                   # noqa: BLE001
        log.debug("edgar_eps: %s/%s 실패: %s", cik, tag, exc)
        return []
    return [x for x in ((js.get("units") or {}).get(_UNITS) or [])
            if isinstance(x, dict)]


def eps_history(ticker: str, years: int = 10) -> dict | None:
    """{"quarterly": [(end, eps)], "annual": [(end, eps)], "tag": …} 또는 None.

    둘 다 **기간 오름차순**. 실패·무커버리지는 None(호출부가 폴백한다).
    """
    from bot.edgar_client import _load_cache, _save_cache, _ticker_to_cik
    key = f"eps_hist_{(ticker or '').upper().split('.')[0]}_{years}"
    hit = _load_cache(key, max_age_h=_TTL_H)
    if isinstance(hit, dict) and "quarterly" in hit:
        return hit
    cik = _ticker_to_cik(ticker)
    if not cik:
        return None
    import datetime as _dt
    floor = (_dt.date.today() - _dt.timedelta(days=365 * years + 40)).isoformat()
    for tag in _TAGS:
        rows = _rows(cik, tag)
        if not rows:
            continue
        q: dict[str, tuple[str, float]] = {}
        a: dict[str, tuple[str, float]] = {}
        for x in rows:
            s, e = str(x.get("start") or ""), str(x.get("end") or "")
            v = x.get("val")
            if not s or not e or e < floor or not isinstance(v, (int, float)):
                continue
            d = _span_days(s, e)
            if d is None:
                continue
            bucket = (q if _Q_DAYS[0] <= d <= _Q_DAYS[1]
                      else a if _A_DAYS[0] <= d <= _A_DAYS[1] else None)
            if bucket is None:
                continue
            filed = str(x.get("filed") or "")
            prev = bucket.get(e)
            if prev is None or filed >= prev[0]:   # 가장 늦게 접수된 것이 정본
                bucket[e] = (filed, float(v))
        if q or a:
            out = {"quarterly": sorted((e, v) for e, (_f, v) in q.items()),
                   "annual": sorted((e, v) for e, (_f, v) in a.items()),
                   "tag": tag}
            _save_cache(key, out)
            return out
    return None
