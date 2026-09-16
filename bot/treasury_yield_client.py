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

# 파생 스프레드 — 재무부는 만기별 수익률만 주고 **금리차는 안 준다**. 그래서
# `T10Y2Y`(미국 장단기금리차) 카드만 보강 대상 밖이라, 2Y·10Y 는 재무부 값으로
# 하루 당겨지는데 스프레드는 FRED 의 D+1 에 머물렀다 — 같은 화면에서 10Y − 2Y
# 를 빼면 스프레드 카드와 안 맞는다(#33 나란히 놓인 칸은 산수가 맞아야 한다.
# 사용자 2026-09-14 "미국채나 장단기금리차같은거 여전히 가장 최신이 아니잖아").
#
# ⚠️ 이건 #32("비교표에 자체계산을 넣지 말 것")의 예외가 아니라 **그 규칙의
# 경계 안쪽**이다. #32 의 경계는 "옆에 다른 출처가 놓이는가" 인데 여기선 세
# 카드가 전부 같은 재무부 날짜에서 나온다. 그리고 FRED 자신이 T10Y2Y 를
# "같은 날 DGS10 − DGS2" 로 정의한다(유동성 보드 가이드에 이미 그렇게 적혀
# 있다) — 우리가 정의를 지어내는 게 아니라 **같은 정의를 같은 원천에 적용**
# 하는 것이다. 게다가 `fresher_than` 의 겹치는 날 검산이 FRED 의 T10Y2Y 와
# 대조하므로, 다리를 잘못 집었으면 그 자리에서 걸린다.
_SPREAD_LEGS: dict[str, tuple[str, str]] = {"T10Y2Y": ("DGS10", "DGS2")}


def derive_spreads(row: dict[str, float]) -> dict[str, float]:
    """만기 행 → 파생 스프레드. 재료가 **둘 다** 있을 때만 만든다(#88).

    순수 함수 — 한쪽 다리가 없으면 그 스프레드는 아예 안 만든다(빈칸이
    틀린 값보다 낫다, #29). 역전(마이너스)이 정상값이므로 `_num` 의
    0~20 상식범위 가드를 태우지 않는다 — 그건 **수익률** 전용이다.
    """
    out: dict[str, float] = {}
    for sid, (long_leg, short_leg) in _SPREAD_LEGS.items():
        a, b = row.get(long_leg), row.get(short_leg)
        if a is None or b is None:
            continue
        out[sid] = round(a - b, 2)      # FRED T10Y2Y 도 소수 2자리
    return out


def _num(s: str) -> float | None:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if 0.0 <= v <= 20.0 else None      # 상식 범위 밖은 버린다


_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_SEC = 1800.0          # 30분 — 재무부는 하루 한 번(15:30 ET) 갱신
_FAIL_TTL_SEC = 600.0      # 실패는 10분만 믿는다(#152 빈 결과를 오래 믿지 말 것)
_TIMEOUT_SEC = 20.0        # 12초는 실측에서 6/6 read timeout — 원천이 느린 날이 있다
# 재시도 — 2026-09-13 VM 실측: 사용자가 **같은 명령을 두 번** 돌리자 1회차는
# `no_newer`(정상), 2회차는 `fetch 202609 failed(timeout)` → `no_overlap` 이었다.
# 한 번의 20초 시도로 판정하면 느린 날의 동전던지기가 그대로 결산의 판정이 된다
# (#21 간격+재시도 없이는 일시적 실패를 '죽은 데이터' 로 오보한다).
# ⚠️ 재시도는 **렌더 경로에 태우지 않는다**(기본 1회) — 20초 × N 이 화면
# 대기가 된다(#116 예산). 배치(감사·`--why`)만 `attempts` 를 올린다.
_DIAG_ATTEMPTS = 3         # 감사·진단 — 배치라 대기가 사용자에게 안 보인다
_RETRY_GAP_SEC = 1.5       # 선형 백오프(1.5s, 3.0s) — 원천을 몰아치지 않는다
_UA = ("Mozilla/5.0 (compatible; NOAH-StockBot/1.0; "
       "+https://home.treasury.gov)")
# 마지막 실패 갈래(달별) — 처방이 갈린다: timeout=느림/차단 · http=상태코드 ·
# network=DNS·연결 · other. 진단이 이걸 읽어 이름을 댄다(#82).
_FAIL: dict[str, str] = {}


def _fail_kind(exc: BaseException) -> str:
    """예외 → 갈래 이름. 원천이 재포장한 예외도 풀어서 본다(#291)."""
    seen, cur = [], exc
    while cur is not None and len(seen) < 5:
        seen.append(type(cur).__name__)
        cur = cur.__cause__ or cur.__context__
    names = " ".join(seen)
    if "Timeout" in names:
        return "timeout"
    if "HTTPError" in names:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        return f"http{code}" if code else "http"
    if "ConnectionError" in names or "DNS" in names:
        return "network"
    return seen[0] if seen else "other"


def retryable(kind: str) -> bool:
    """이 실패 갈래를 다시 물어서 답이 바뀌나(#279 재시도 중단 조건).

    시간·연결 문제(timeout·network)와 원천 5xx 만 — 4xx 는 **우리 요청 모양**
    이라 백 번 물어도 같다(#82 처방이 정반대인 갈래를 한 통에 담지 말 것).
    """
    return kind in ("timeout", "network") or kind.startswith("http5")


def last_fail(ym: str | None = None) -> str | None:
    """그 달의 마지막 도달 실패 갈래(없으면 None) — 진단·감사가 읽는다."""
    return _FAIL.get(ym or date.today().strftime("%Y%m"))


def fetch_daily_curve(ym: str | None = None, *, attempts: int = 1
                     ) -> dict[str, dict[str, float]]:
    """{'YYYY-MM-DD': {'DGS2': 4.17, ...}} — 실패 시 빈 dict(graceful).

    ⚠️ 30분 메모리 캐시. 이 함수는 시리즈마다 불리므로(2Y·10Y·30Y) 캐시가
    없으면 한 렌더에 같은 XML 을 세 번 받는다.

    `attempts` — **렌더는 1회**(20초 × N 이 화면 대기가 된다, #116), 배치인
    감사·`--why` 는 `_DIAG_ATTEMPTS`. 재시도는 다시 물어 답이 바뀔 갈래에만
    건다(`retryable`, #279). 그리고 `attempts > 1` 이면 **실패 캐시를 믿지
    않는다** — 진단은 10분 전 한 번의 타임아웃이 아니라 지금 사실을 재야
    한다(#35·#54 · 성공 캐시는 그대로 존중해 공짜 조회를 늘리지 않는다).
    """
    import requests
    ym = ym or date.today().strftime("%Y%m")
    _hit = _CACHE.get(ym)
    if _hit:
        _fresh = time.time() - _hit[0] < (_TTL_SEC if _hit[1] else _FAIL_TTL_SEC)
        if _fresh and (_hit[1] or attempts <= 1):
            return _hit[1]
    xml = None
    for _i in range(max(1, int(attempts))):
        if _i:
            time.sleep(_RETRY_GAP_SEC * _i)
        try:
            # ⚠️ UA 를 안 보내면 기본 `python-requests/…` 로 나간다 — 정부 사이트는
            # 그런 요청을 WAF 가 늘어뜨리거나 막는 일이 있다. 우리가 통제할 수 있는
            # 축이므로 먼저 맞춘다("도달 실패"는 원천 장애일 수도, 우리 요청 모양
            # 때문일 수도 있다 — 둘을 못 가르면 처방이 갈린다, #82).
            r = requests.get(_URL.format(ym=ym), timeout=_TIMEOUT_SEC,
                             headers={"User-Agent": _UA,
                                      "Accept": "application/xml"})
            r.raise_for_status()
            xml = r.text
            break
        except Exception as exc:
            _FAIL[ym] = _fail_kind(exc)
            log.info("treasury: fetch %s failed(%s, %d/%d): %s",
                     ym, _FAIL[ym], _i + 1, max(1, int(attempts)), exc)
            # 더 물어서 답이 바뀌지 않는 갈래면 즉시 그만둔다(#279).
            if not retryable(_FAIL[ym]):
                break
    if xml is None:
        # ⚠️ 실패를 **짧게** 캐시한다. 안 하면 렌더 경로가 시리즈 3종 × 달 2개
        # = 6회를 매번 타임아웃까지 기다린다(2026-09-08 VM 실측 read timeout
        # 6/6). 길게 믿으면 원천 장애 한 번이 하루를 비운다(#152·#161) —
        # 그래서 성공(30분)보다 훨씬 짧게(#116 예산과 캐시는 한 세트).
        _CACHE[ym] = (time.time(), {})
        return {}
    _FAIL.pop(ym, None)

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
        row.update(derive_spreads(row))
        if row:
            out[d] = row
    _CACHE[ym] = (time.time(), out)
    return out


def _prev_ym(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[4:])
    return f"{y - 1}12" if m == 1 else f"{y}{m - 1:02d}"


def curve_for(fred_last_date: str, ym: str | None = None, *,
              attempts: int = 1
              ) -> tuple[dict[str, dict[str, float]], list[str]]:
    """(합친 곡선, 조회한 달 목록).

    ⚠️ 현재 달만 받으면 **매달 초 2~3영업일 동안 보강이 조용히 죽는다** —
    그때 FRED 의 최신일은 아직 지난달이라 겹치는 날이 표에 없고, 검산이
    성립하지 않아 그냥 None 이 된다(2026-09-08 코드 재독으로 발각).
    겹치는 날이 없으면 **직전 달을 한 번 더** 받는다(30분 캐시라 유계)."""
    ym = ym or date.today().strftime("%Y%m")
    months = [ym]
    curve = dict(fetch_daily_curve(ym, attempts=attempts))
    if fred_last_date and fred_last_date not in curve:
        pm = _prev_ym(ym)
        months.append(pm)
        for d, row in fetch_daily_curve(pm, attempts=attempts).items():
            curve.setdefault(d, row)
    return curve, months


# 보강 판정을 갈래로 — '없음'만 말하는 진단은 추측을 부른다(#82). 옛 판은
# 네 갈래 중 **셋이 조용**해서(곡선 미수신·겹치는 날 없음·더 새 날짜 없음)
# 화면이 왜 안 당겨졌는지 로그로도 알 수 없었다(#12 silent-fail).
#   no_curve   → 재무부 XML 미수신(네트워크·원천 장애)
#   month_failed → FRED 최신일이 든 **달을 우리가 못 받았다**(일시적·재시도)
#   no_overlap → FRED 최신일이 재무부 표에 없다(달 경계·원천 결측)
#   mismatch   → 겹치는 날 값이 다르다(태그 오집 의심 — 만기가 다른 값)
#   no_newer   → 재무부에도 더 새 날짜가 없다 = **이게 원천의 최선**
#   ok         → 당길 수 있다
# 대조가 **성립한** 갈래는 둘뿐이다 — 당길 수 있거나(ok), 재무부에도 더 새
# 날짜가 없거나(no_newer). 나머지 넷은 전부 "재무부와 못 맞춰 봤다"이다.
_PROBE_OK = ("ok", "no_newer")


def augmentable_sids() -> frozenset[str]:
    """재무부로 당길 수 있는 시리즈 — 직접 만기(`_FIELDS`) + 파생 금리차
    (`_SPREAD_LEGS`). 화면(`market_overview._TREASURY_SIDS`)·`--why`·감사가
    **여기 하나**에서 파생한다 — 각자 적으면 T10Y2Y 처럼 한쪽만 늘어난다
    (2026-09-16 독립 리뷰 실측: `--why T10Y2Y` 가 '미지원'이라 답했다, #24·#38)."""
    return frozenset(_FIELDS) | frozenset(_SPREAD_LEGS)


def probe_failed(code: str) -> bool:
    """이번 대조가 성립하지 않았나(#361c).

    ⚠️ 갈래를 **열거하면 새 갈래가 샌다**(#24) — 2026-09-13 독립 리뷰가
    `("no_curve", "month_failed")` 만 보던 판을 잡았다(`mismatch`·
    `no_overlap` 도 대조 실패인데 조용했다). 성립하는 쪽이 닫힌 집합이므로
    **여집합**으로 판정한다. 그리고 `--why` 와 `macro_staleness_audit` 이
    **같은 술어**를 쓴다 — 각자 열거하면 두 화면이 갈린다(#35·#38).
    """
    return code not in _PROBE_OK


def fresher_diag(fred_last_date: str, fred_last_value: float, sid: str,
                 tol: float = 0.10, *, attempts: int = 1) -> tuple[str, dict]:
    """(갈래, 수치) — 순수 판정. 화면·로그·진단이 같이 쓴다(#35·#38)."""
    curve, months = curve_for(fred_last_date, attempts=attempts)
    d: dict = {"sid": sid, "fred_date": fred_last_date,
               "fred_value": fred_last_value, "months": months,
               "curve_days": sorted(curve), "tol": tol}
    if not curve:
        d["fail"] = " · ".join(
            f"{m}:{last_fail(m)}" for m in months if last_fail(m)) or "미상"
        return "no_curve", d
    same = (curve.get(fred_last_date) or {}).get(sid)
    d["overlap_value"] = same
    if same is None:
        # ⚠️ 2026-09-13 VM 실측 — 같은 명령을 두 번 돌리자 갈렸다:
        #   1회차: no_newer · 표에 있는 날 2026-09-01~09-11 (8일)  ✅
        #   2회차: `fetch 202609 failed(timeout)` → 표에 있는 날
        #          2026-08-03~08-31 (21일) → **no_overlap**
        # 즉 202609 를 **우리가 못 받으면** `curve_for` 가 직전 달로 폴백해
        # 8월 표만 남고, 9월 날짜가 거기 없으니 '원천 표에 없다' 로 찍혔다.
        # 실제로는 **우리가 그 달을 못 받은 것**이다 — 처방이 정반대다
        # (재시도 vs 원천 결측·창 확대, #82 갈래는 이름으로 · #143 대조군
        # 없이 '없음' 과 '못 받음' 을 가르지 말 것). 오늘 아침 감사의
        # `❌ 3건(no_overlap)` 이 바로 이것이었다.
        # ⚠️ `last_fail` 은 **모듈 전역**이라 오래전 실패가 남아 있을 수 있다 —
        # 이번 호출이 그 달을 조회조차 안 했으면(FRED 최신일이 두 달 전인
        # 경우) 그 기록은 이 판정과 무관하다. `months`(= curve_for 가 이번에
        # 시도한 달)에 있을 때만 믿는다 — 아니면 `month_failed` 와
        # `no_overlap` 의 처방이 정반대라 운영자를 반대쪽으로 보낸다(#82).
        _ym = fred_last_date[:4] + fred_last_date[5:7]
        _why_m = last_fail(_ym) if _ym in (d.get("months") or ()) else None
        if _why_m:
            d["failed_month"], d["fail"] = _ym, _why_m
            return "month_failed", d
        return "no_overlap", d
    d["overlap_gap"] = abs(same - fred_last_value)
    if d["overlap_gap"] > tol:
        return "mismatch", d
    newer = sorted((dd, r[sid]) for dd, r in curve.items()
                   if sid in r and dd > fred_last_date)
    if not newer:
        return "no_newer", d
    d["newer"] = newer[-1]
    return "ok", d


def fresher_reason(code: str, d: dict) -> str:
    """사람이 읽는 사유 — 수치를 같이 적어 눈으로 검산되게 한다(#202)."""
    sid, fd = d.get("sid", "?"), d.get("fred_date", "?")
    months = " · ".join(d.get("months") or []) or "—"
    days = d.get("curve_days") or []
    span = f"{days[0]}~{days[-1]} ({len(days)}일)" if days else "0일"
    if code == "no_curve":
        return (f"재무부 XML 을 못 받았다(조회 달 {months}) — "
                f"갈래 {d.get('fail') or '미상'}")
    if code == "month_failed":
        return (f"FRED 최신일 {fd} 가 든 달({d.get('failed_month')})을 "
                f"**우리가 못 받았다** — 갈래 {d.get('fail') or '미상'}. "
                f"직전 달로 폴백해 표에 있는 날은 {span} 뿐이다"
                f"(조회 달 {months}) — 원천 결측이 아니다")
    if code == "no_overlap":
        return (f"FRED 최신일 {fd} 가 재무부 표에 없다 — 조회 달 {months}, "
                f"표에 있는 날 {span}")
    if code == "mismatch":
        return (f"{fd} 값이 재무부 {d.get('overlap_value')} vs FRED "
                f"{d.get('fred_value')} (차이 {d.get('overlap_gap'):.3f}%p > "
                f"허용 {d.get('tol')}%p) — 태그 오집 의심이라 쓰지 않는다")
    if code == "no_newer":
        return (f"재무부에도 {fd} 보다 새 날짜가 없다 — 원천의 최선이 {fd} "
                f"다(표에 있는 날 {span})")
    if code == "ok":
        n = d.get("newer") or ("?", "?")
        return f"{sid} 를 {fd} → {n[0]} ({n[1]}%) 로 당길 수 있다"
    return code


def fresher_than(fred_last_date: str, fred_last_value: float, sid: str,
                 tol: float = 0.10, *, attempts: int = 1
                 ) -> tuple[str, float] | None:
    """FRED 보다 **새 날짜**가 있고, 겹치는 날 값이 `tol`(%p) 이내로 일치하면
    (날짜, 값)을 돌려준다. 아니면 None — 그 경우 호출부는 FRED 를 그대로 쓴다.

    ⚠️ 겹치는 날 검산이 핵심이다. 태그를 잘못 집으면(2년물 자리에 1개월물)
    같은 날 값이 %p 단위로 어긋나므로 여기서 걸린다."""
    code, d = fresher_diag(fred_last_date, fred_last_value, sid, tol,
                           attempts=attempts)
    if code == "ok":
        return d["newer"]
    # 조용한 생략 금지 — 어느 갈래인지 남긴다(#12).
    (log.warning if code == "mismatch" else log.info)(
        "treasury: %s 보강 생략(%s) — %s", sid, code, fresher_reason(code, d))
    return None


# ── 진단: 미국채 기준일이 왜 그 날인가(`--why`) ─────────────────────────────
# 사용자 2026-09-08 "미국채는 오늘 9/8 일인데 이게 가져올수 있는 최선인거야?"
# (화면 기준 2026-09-03). 갈래마다 처방이 다르고, 무엇보다 **원천의 최선**이
# 며칠인지를 화면·사람이 답할 수 있어야 한다 — 미 휴장일(노동절 등)이 끼면
# '오늘'이 최선이 아니다(#82·#274).
#
# ⚠️ 무엇을 쓰나(#284): 아무것도 쓰지 않는다. FRED 는 화면과 같은 경로
# (`market_overview._fred_fetch_series`)를 태우므로 그 함수의 일별 캐시는
# 채워질 수 있다 — 제품이 매 사이클 채우는 그 캐시다(가짜 값 아님).
_WHY_FIX = {
    "no_curve": "재무부 도달 실패 — timeout=원천이 느리거나 요청이 늘어짐"
                " · http4xx=차단·경로변경 · network=DNS·연결. 갈래는 위 사유에 있다",
    "month_failed": "그 달을 못 받았다 — 진단·감사는 이미 "
                    f"{_DIAG_ATTEMPTS}회 재시도한 뒤다(렌더는 1회). 여기까지 "
                    "왔으면 일시적이 아니다 — no_curve 와 같은 갈래를 본다"
                    "(timeout·차단·DNS)",
    "no_overlap": "겹치는 날이 없다 — 달 경계면 직전 달까지 받아야 한다",
    "mismatch": "태그 오집 의심 — `_FIELDS` 만기 매핑을 원문으로 확인할 것",
    "no_newer": "원천이 이미 최선이다 — 우리 문제가 아니다",
    "ok": "다음 재생성에서 당겨진다(캐시 TTL 1시간)",
}


def _why(sids: list[str]) -> int:
    """미국채 신선도 진단. rc=0 이면 원천의 최선까지 왔다."""
    import pathlib
    import sys
    print(f"# 미국채 신선도 진단 — 인터프리터 {sys.executable}")
    print(f"#   cwd={pathlib.Path.cwd()}  오늘(KST) {date.today()}")
    try:
        from bot.env_keys import env_diag
        print("#   " + (env_diag("FRED_API_KEY") or "자격증명 FRED_API_KEY: 있음"))
    except Exception as exc:                                   # noqa: BLE001
        print(f"#   ⚠️ 자격증명 확인 실패: {exc}")

    # 원천의 최선 = 미국 시장의 **마지막 완결 세션**(휴장일 캘린더 반영).
    # 판정은 시장타이밍과 **같은 함수**를 쓴다 — 복제하면 갈라진다(#38·#176).
    best = None
    try:
        from bot.market_timing import _expected_session
        best, _grace = _expected_session("US")
        print(f"#   원천이 낼 수 있는 최신 세션(미 휴장일 반영): {best}")
    except Exception as exc:                                   # noqa: BLE001
        print(f"#   ⚠️ 기대 세션 판정 불가({exc}) — 최선 대비 판정은 생략한다")
    print("#   ⚠️ 화면을 재생성하지 않는다(FRED 일별 캐시만 화면과 공유).\n")

    behind, failed, checked = [], [], 0
    # ⚠️ 대조 **자체가 실패한** 건은 따로 센다 — 화면 값이 이미 최선이면
    # `✅ 최선까지 왔다` 가 찍히는데, 그러면 "재무부를 못 받았다" 는 사실이
    # ✅ 에 덮인다(2026-09-13 VM 2회차 실측: 3건 전부 202609 timeout 인데
    # 최종 판정이 `✅ 대조 3건 전부 원천의 최선까지 왔다` 였다). 여유·우연으로
    # 사실을 덮지 말 것(#41) — 오늘은 무해해도 내일은 보강이 안 된다.
    probe_bad: list = []
    for sid in sids:
        sid = sid.upper()
        print(f"── {sid} ──────────────────────────────")
        if sid not in augmentable_sids():
            print(f"  ❌ 재무부 매핑에 {sid} 이 없다 — 오타이거나 미지원\n")
            failed.append(sid)
            continue
        try:
            from bot.market_overview import _fred_fetch_series
            rec = _fred_fetch_series(sid, 90)
        except Exception as exc:                               # noqa: BLE001
            print(f"  ❌ FRED 조회 실패: {exc}\n")
            failed.append(sid)
            continue
        if not rec or rec.get("value") is None:
            print("  ❌ FRED 가 값을 안 줬다(키·네트워크 확인)\n")
            failed.append(sid)
            continue
        checked += 1
        fdate, fval = str(rec.get("time") or "")[:10], float(rec["value"])
        used = rec.get("src") or "FRED"
        print(f"  화면이 쓰는 값: {fval}% ({fdate}) · 출처 {used}")
        # ⚠️ 진단은 **배치**다 — 사용자가 기다리는 화면이 아니므로 재시도한다.
        # 없으면 `_WHY_FIX['month_failed']` 가 '이미 재시도한 뒤다' 라고
        # 적어 놓고 실제로는 1회만 물어, 한 번 더 물으면 풀릴 상황에
        # 운영자를 차단·DNS 확인으로 보낸다(2026-09-16 독립 리뷰 실측 · #187b).
        code, d = fresher_diag(fdate, fval, sid, attempts=_DIAG_ATTEMPTS)
        print(f"  재무부 대조: {code} — {fresher_reason(code, d)}")
        print(f"  처방: {_WHY_FIX.get(code, code)}")
        shown = d.get("newer", (fdate, fval))[0] if code == "ok" else fdate
        if best and shown < best:
            print(f"  ⚠️ 최선({best})보다 뒤처져 있다")
            behind.append(f"{sid}@{shown}")
        elif best:
            print(f"  ✅ 최선({best})까지 왔다")
            # ⚠️ 화면이 최선이어도 **대조는 못 했을 수 있다** — 그 사실을
            # 그대로 말한다(#41 우연으로 사실을 덮지 말 것). 이 통지는
            # **'최선까지 왔다' 안에만** 둔다: 뒤처진 줄에 붙이면 바로 위
            # ⚠️ 와 모순되고(그 줄은 이미 갈래·사유를 말한다), `best` 를
            # 못 구한 실행(= _expected_session 실패)에 붙이면 "우연히
            # 최선이었을 뿐"이라는 **재지 않은 주장**이 된다(#165).
            if probe_failed(code):
                print(f"  ⚠️ 다만 이번 대조는 실패했다({code}) — 화면 값이"
                      " 우연히 최선이었을 뿐, 보강 경로는 확인되지 않았다")
                probe_bad.append(f"{sid}:{code}")
        print()

    if failed:
        print(f"판정: ❌ 조회 실패 {len(failed)}건 — {' · '.join(failed)}")
    if behind:
        print(f"판정: ⚠️ 최선보다 뒤처짐 {len(behind)}건 — {' · '.join(behind)}")
    if probe_bad:
        # rc 는 바꾸지 않는다 — 화면은 최선이므로 사용자에게 문제가 없다.
        # 다만 **말은 한다**(#43 침묵이 최악 · #25 늘 뜨는 경보도 금물이라
        # 실제 실패했을 때만 뜬다).
        print(f"판정: ⚠️ 대조 실패 {len(probe_bad)}건 — {' · '.join(probe_bad)}"
              " (화면은 최선이나 보강 경로 미확인 — 반복되면 갈래를 볼 것)")
    if failed or behind:
        return 1
    if not checked:
        print("판정: ❓ 대조한 시리즈가 0건 — 인자를 확인할 것")
        return 1
    if probe_bad:
        return 0          # 화면은 정상 — 다만 위 ⚠️ 로 사실을 남겼다
    print(f"판정: ✅ 대조 {checked}건 전부 원천의 최선까지 왔다.")
    return 0


if __name__ == "__main__":                # pragma: no cover - 수동 진단
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        prog="python -m bot.treasury_yield_client",
        description="미국채 기준일 진단(읽기 전용 · 화면 재생성 안 함)",
        epilog="예: cd ~/stock && .venv/bin/python -m "
               "bot.treasury_yield_client --why")
    ap.add_argument("--why", nargs="*", metavar="SERIES",
                    help="기준일이 왜 그 날인지 갈래로 진단(생략 시 전 시리즈)")
    a = ap.parse_args()
    if a.why is None:
        ap.print_help()
        sys.exit(0)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(_why(a.why or sorted(augmentable_sids())))
