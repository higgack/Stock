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


def _prev_ym(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[4:])
    return f"{y - 1}12" if m == 1 else f"{y}{m - 1:02d}"


def curve_for(fred_last_date: str, ym: str | None = None
              ) -> tuple[dict[str, dict[str, float]], list[str]]:
    """(합친 곡선, 조회한 달 목록).

    ⚠️ 현재 달만 받으면 **매달 초 2~3영업일 동안 보강이 조용히 죽는다** —
    그때 FRED 의 최신일은 아직 지난달이라 겹치는 날이 표에 없고, 검산이
    성립하지 않아 그냥 None 이 된다(2026-09-08 코드 재독으로 발각).
    겹치는 날이 없으면 **직전 달을 한 번 더** 받는다(30분 캐시라 유계)."""
    ym = ym or date.today().strftime("%Y%m")
    months = [ym]
    curve = dict(fetch_daily_curve(ym))
    if fred_last_date and fred_last_date not in curve:
        pm = _prev_ym(ym)
        months.append(pm)
        for d, row in fetch_daily_curve(pm).items():
            curve.setdefault(d, row)
    return curve, months


# 보강 판정을 갈래로 — '없음'만 말하는 진단은 추측을 부른다(#82). 옛 판은
# 네 갈래 중 **셋이 조용**해서(곡선 미수신·겹치는 날 없음·더 새 날짜 없음)
# 화면이 왜 안 당겨졌는지 로그로도 알 수 없었다(#12 silent-fail).
#   no_curve   → 재무부 XML 미수신(네트워크·원천 장애)
#   no_overlap → FRED 최신일이 재무부 표에 없다(달 경계·원천 결측)
#   mismatch   → 겹치는 날 값이 다르다(태그 오집 의심 — 만기가 다른 값)
#   no_newer   → 재무부에도 더 새 날짜가 없다 = **이게 원천의 최선**
#   ok         → 당길 수 있다
def fresher_diag(fred_last_date: str, fred_last_value: float, sid: str,
                 tol: float = 0.10) -> tuple[str, dict]:
    """(갈래, 수치) — 순수 판정. 화면·로그·진단이 같이 쓴다(#35·#38)."""
    curve, months = curve_for(fred_last_date)
    d: dict = {"sid": sid, "fred_date": fred_last_date,
               "fred_value": fred_last_value, "months": months,
               "curve_days": sorted(curve), "tol": tol}
    if not curve:
        return "no_curve", d
    same = (curve.get(fred_last_date) or {}).get(sid)
    d["overlap_value"] = same
    if same is None:
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
        return f"재무부 XML 을 못 받았다(조회 달 {months})"
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
                 tol: float = 0.10) -> tuple[str, float] | None:
    """FRED 보다 **새 날짜**가 있고, 겹치는 날 값이 `tol`(%p) 이내로 일치하면
    (날짜, 값)을 돌려준다. 아니면 None — 그 경우 호출부는 FRED 를 그대로 쓴다.

    ⚠️ 겹치는 날 검산이 핵심이다. 태그를 잘못 집으면(2년물 자리에 1개월물)
    같은 날 값이 %p 단위로 어긋나므로 여기서 걸린다."""
    code, d = fresher_diag(fred_last_date, fred_last_value, sid, tol)
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
    "no_curve": "재무부(home.treasury.gov) 도달 실패 — 네트워크·원천 장애",
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
    for sid in sids:
        sid = sid.upper()
        print(f"── {sid} ──────────────────────────────")
        if sid not in _FIELDS:
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
        code, d = fresher_diag(fdate, fval, sid)
        print(f"  재무부 대조: {code} — {fresher_reason(code, d)}")
        print(f"  처방: {_WHY_FIX.get(code, code)}")
        shown = d.get("newer", (fdate, fval))[0] if code == "ok" else fdate
        if best and shown < best:
            print(f"  ⚠️ 최선({best})보다 뒤처져 있다")
            behind.append(f"{sid}@{shown}")
        elif best:
            print(f"  ✅ 최선({best})까지 왔다")
        print()

    if failed:
        print(f"판정: ❌ 조회 실패 {len(failed)}건 — {' · '.join(failed)}")
    if behind:
        print(f"판정: ⚠️ 최선보다 뒤처짐 {len(behind)}건 — {' · '.join(behind)}")
    if failed or behind:
        return 1
    if not checked:
        print("판정: ❓ 대조한 시리즈가 0건 — 인자를 확인할 것")
        return 1
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
    sys.exit(_why(a.why or list(_FIELDS)))
