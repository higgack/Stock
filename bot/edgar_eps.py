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

⚠️⚠️ **회계연도 4분기는 분기 프레임에 없다**(2026-08-22 MSFT 실측). 분기
프레임은 10-Q 에서만 나오는데 FY Q4 는 10-Q 를 안 낸다 — 그 분기 실적은
10-K 의 **연간** 수치에만 들어간다. 그래서 MSFT(6월 결산)는 9·12·3월만
있고 **6월이 통째로 빠졌고**, TTM 이 최근 4개 *가용* 분기를 더하는 바람에
실제로는 **15개월**을 합산했다(화면 전 행이 산수는 맞는데 값이 틀렸다).
12월 결산 회사면 12월이 빠진다 — **미국 종목 전체**에 해당한다.
대응: 연간 프레임 안에 분기가 정확히 3개면 **연간 − 3분기 합**으로 나머지
한 분기를 복원한다(DART 4분기를 연간−3분기누적으로 만드는 것과 같은 규율).
⚠️ 희석주식수가 분기마다 달라 연간 EPS ≠ 분기 EPS 합이므로 복원분은 그
차이를 흡수한다(±0.0x). 15개월 TTM 보다는 훨씬 낫고, 애초에 결산분기를
빼고 만든 밴드는 최고·최저라는 이름값을 못 한다.
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


def _parse_sig() -> str:
    """이 모듈 소스의 지문. **캐시 키에 넣는다.**

    ⚠️ 손으로 올리는 버전 상수는 이 레포에서 **네 번** 잊었다(#18 아카이브 ·
    #21b 파싱 캐시 · #95 재무 캐시 · #124 렌더 캐시). 규율로 기억할 일을
    구조로 옮긴다 — 파싱 규칙을 고치면 키가 자동으로 바뀐다.
    """
    import hashlib
    try:
        with open(__file__, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()[:8]
    except Exception:                                          # noqa: BLE001
        return "nosig"


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
    key = (f"eps_hist_{(ticker or '').upper().split('.')[0]}"
           f"_{years}_{_parse_sig()}")
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
        # end → (filed, val, start). `start` 가 있어야 연간 구간 안에 든
        # 분기를 고를 수 있다(결산분기 복원, 위 독스트링).
        q: dict[str, tuple[str, float, str]] = {}
        a: dict[str, tuple[str, float, str]] = {}
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
                bucket[e] = (filed, float(v), s)
        if q or a:
            quarters = fill_fiscal_q4(
                [(e, v, st) for e, (_f, v, st) in q.items()],
                [(e, v, st) for e, (_f, v, st) in a.items()])
            # ⚠️ **제출일(filed)을 같이 내보낸다**(#259 2차, 2026-08-27 NVDA):
            # 분할 '전'에 제출된 보고서의 주당값은 그 분할을 알 수 없으므로
            # 확실히 미조정이고, '후' 제출분은 소급조정된다(ASC 260) — 기저
            # 판정을 측정(급변)이 아니라 **원천의 사실**로 할 수 있는 열쇠다.
            # 복원한 결산분기의 기저는 그 연간 보고서의 기저다.
            a_filed = {str(e)[:10]: f for e, (f, _v, _s) in a.items()}
            q_filed = {str(e)[:10]: f for e, (f, _v, _s) in q.items()}
            for e, _v in quarters:
                if e not in q_filed and e in a_filed:
                    q_filed[e] = a_filed[e]
            out = {"quarterly": quarters,
                   "annual": sorted((e, v) for e, (_f, v, _s) in a.items()),
                   "q_filed": q_filed, "a_filed": a_filed,
                   "tag": tag}
            _save_cache(key, out)
            return out
    return None


def fill_fiscal_q4(quarters: list, annuals: list) -> list:
    """[(end, val, start)] ×2 → **결산분기를 복원한** [(end, val)] 오름차순.

    연간 구간 안에 분기가 **정확히 3개**면 나머지 한 분기를
    `연간 − 3분기 합` 으로 만들어 넣는다. 이미 그 `end` 의 분기가 있으면
    건드리지 않는다(원천이 준 값이 항상 이긴다).

    ⚠️ 복원한 분기의 기간이 분기 범위(80~100일)를 벗어나면 **버린다** —
    억지로 채우면 반기·이상치가 분기로 섞여 TTM 이 통째로 틀린다.
    """
    have = {str(e)[:10]: float(v) for e, v, _s in quarters}
    for a_end, a_val, a_start in annuals:
        a_end, a_start = str(a_end)[:10], str(a_start)[:10]
        if a_end in have:
            continue                     # 원천이 이미 그 분기를 줬다
        inside = sorted((str(e)[:10], float(v), str(st)[:10])
                        for e, v, st in quarters
                        if a_start <= str(st)[:10] and str(e)[:10] <= a_end)
        if len(inside) != 3:
            continue
        gap = _span_days(inside[-1][0], a_end)
        if gap is None or not (_Q_DAYS[0] <= gap <= _Q_DAYS[1]):
            continue                     # 남은 구간이 분기가 아니다 — 버린다
        q4 = round(float(a_val) - sum(v for _e, v, _s in inside), 4)
        if not _plausible_q4(q4, [v for _e, v, _s in inside]):
            log.warning("edgar_eps: %s 결산분기 복원 폐기 — 연간 %.2f − 3분기 "
                        "%.2f = %.2f (형제 분기와 크기가 안 맞는다)",
                        a_end, float(a_val), sum(v for _e, v, _s in inside), q4)
            continue
        have[a_end] = q4
    return sorted(have.items())


# 복원값이 형제 분기의 몇 배까지 벌어지면 못 믿는가.
_Q4_SANE = 3.0


def _plausible_q4(q4: float, siblings: list) -> bool:
    """복원한 결산분기가 **형제 분기와 같은 급인가**.

    ⚠️ 2026-08-22 전 시장 감사 실측: ❌ 3건이 전부 **회계연도말**에 있었다
    (LRCX 2023-06-25 10.8배 · KLAC 2024-06-30 9.4배 · KLAC 2017-06-30 결산검산).
    결산분기는 10-Q 가 없어 `연간 − 3분기` 로 복원하는데, EDGAR **분기**는
    후속 보고서의 분할 소급조정분이 오고(최신 접수분 채택) **연간**은
    as-reported 라 두 계열의 스케일이 다를 수 있다 — 그걸 빼면 복원값이
    쓰레기가 된다(KLAC: 2.03 − 14.12 = **-12.09**).

    스케일이 갈렸는지 직접 알아낼 방법은 없다. 대신 **결과를 잰다**: 복원값이
    형제 분기 평균의 3배를 넘거나 형제가 전부 양수인데 음수면 못 믿는다.
    억지로 채우느니 그 시점을 비운다(#29 빈칸이 틀린 값보다 낫다) — 호출부는
    TTM 을 안 만들고 연간 경로로 내려간다.
    """
    sib = [v for v in (siblings or []) if v is not None]
    if not sib:
        return False
    if q4 < 0 and all(v > 0 for v in sib):
        return False
    avg = sum(abs(v) for v in sib) / len(sib)
    if avg <= 0:
        return True
    return abs(q4) <= avg * _Q4_SANE
