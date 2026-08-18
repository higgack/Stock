"""글로벌 스냅샷 · Macro Snapshot 의 **발표지표 신선도 감사**.

사용자 2026-08-18: "실시간으로 가져오지 않는 지표에 대해서 최신의 것을
제때제때 잘 가져오는지 꼼꼼히 확인해줘."

화면 카드를 눈으로 훑는 대신, **화면이 쓰는 바로 그 경로**로 관측일을 받아
`bot/macro_cadence.CADENCE` 의 공표 규약과 대조한다. 값은 안 본다 — 신선도만.

    cd ~/stock && .venv/bin/python -m bot.scripts.macro_staleness_audit

읽기 전용 · LLM 0 · ₩0.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

_PROBE_VER = 2


def _p(*a):
    print(*a, flush=True)


def _treasury_status(mo) -> None:
    """국채금리가 **재무부로 당겨졌는지** 그 자리에서 검증한다.

    ⚠️ 보강은 try/except 안에 있어 실패해도 화면엔 아무 표시가 없다 —
    조용히 FRED 값(D+1)으로 되돌아갈 뿐이다(실수 #12 silent-fail 가시화).
    여기서 ① FRED 디스크 캐시 나이·내용 ② 재무부 원천 직접 조회 ③ 검산
    결과를 셋 다 찍어, '왜 어제 날짜인가'를 추측 없이 가른다."""
    import time
    from datetime import date as _date
    _p("── 재무부 보강(국채금리 DGS2/10/30) 상태")
    cache_dir = mo._CACHE_DIR / "fred"
    for sid in sorted(mo._TREASURY_SIDS):
        f = cache_dir / f"{sid}_{_date.today().isoformat()}.json"
        if f.exists():
            import json
            age_m = (time.time() - f.stat().st_mtime) / 60
            try:
                d = json.loads(f.read_text())
            except Exception:
                d = {}
            src = d.get("src") or "FRED"
            _p(f"   {sid}: 디스크캐시 {age_m:.0f}분 전 · 관측 {d.get('time')} "
               f"· 원천 {src}"
               + ("" if src == "UST" else
                  f"  ⚠️ 재무부 미적용 (캐시 TTL {mo._FRED_TTL_DAILY_H}h 지나면 재시도)"))
        else:
            _p(f"   {sid}: 디스크캐시 없음")
    try:
        from bot.treasury_yield_client import fetch_daily_curve, fresher_than
        curve = fetch_daily_curve()
    except Exception as exc:                          # noqa: BLE001
        _p(f"   ❗ 재무부 원천 조회 자체가 실패: {type(exc).__name__}: {exc}")
        return
    if not curve:
        _p("   ❗ 재무부 원천이 빈 값 — 차단·양식변경 의심. FRED(D+1)로만 돈다.")
        return
    days = sorted(curve)
    _p(f"   재무부 원천: 관측 {len(days)}일 · 최신 {days[-1]} {curve[days[-1]]}")
    for sid in sorted(mo._TREASURY_SIDS):
        f = cache_dir / f"{sid}_{_date.today().isoformat()}.json"
        base_date = base_val = None
        if f.exists():
            import json
            try:
                d = json.loads(f.read_text())
                # 캐시가 이미 UST 면 prev_value 가 FRED 마지막 값이다.
                base_date = d.get("time")
                base_val = d.get("value")
            except Exception:
                pass
        if base_date is None or base_val is None:
            _p(f"   {sid}: 비교 기준(캐시) 없음 — 판정 생략")
            continue
        nf = fresher_than(base_date, float(base_val), sid)
        _p(f"   {sid}: 현재값 {base_date}={base_val} → "
           + (f"재무부 더 최신 {nf[0]}={nf[1]}" if nf
              else "재무부에 더 새 관측 없음(또는 겹치는 날 불일치로 거부)"))


def main() -> int:
    from bot.macro_cadence import (CADENCE, GRACE_DAYS, _CADENCE_VER, judge)
    from bot.env_keys import env_source
    from bot import macro_snapshot as ms
    from bot import market_overview as mo

    today = (datetime.utcnow() + timedelta(hours=9)).date()
    _p(f"macro_staleness_audit v{_PROBE_VER} · cadence v{_CADENCE_VER} · "
       f"grace {GRACE_DAYS}일 · 기준 {today} (KST)")
    _keysrc = {"fred": env_source("FRED_API_KEY"),
               "fred_yoy": env_source("FRED_API_KEY"),
               "ecos": env_source("BOK_ECOS_API_KEY")}
    _p(f"키: FRED_API_KEY={_keysrc['fred']} · "
       f"BOK_ECOS_API_KEY={_keysrc['ecos']}")
    _p("")

    # 화면에 실제로 뜨는 발표지표만(실시간 가격 카드 src='yf' 는 대상 아님).
    rows: list[tuple[str, str, str]] = []      # (표면, 라벨, "src:id")
    seen: set[tuple[str, str]] = set()
    for surface, defs in (("Macro/국내", ms.DOMESTIC), ("Macro/글로벌", ms.GLOBAL)):
        for _k, label, _u, src, sid, _d in defs:
            if src in ("fred", "fred_yoy", "ecos") and (src, sid) not in seen:
                seen.add((src, sid))
                rows.append((surface, label, f"{src}:{sid}"))
    for label, sid, _u, _lb in mo.FRED_INDICATORS:
        if ("fred", sid) not in seen:
            seen.add(("fred", sid))
            rows.append(("글로벌 스냅샷", label, f"fred:{sid}"))

    late: list[str] = []
    unknown: list[str] = []
    for surface, label, key in rows:
        src, sid = key.split(":", 1)
        raw = ""
        try:
            if src == "ecos":
                pts = ms._ecos_series(sid)
                raw = pts[-1][0] if pts else ""
            else:
                spot = mo._fred_fetch_series(sid, 400)
                raw = (spot or {}).get("time", "")
                if (spot or {}).get("src") == "UST":
                    key += " ·UST"          # 재무부로 하루 당겨진 행
        except Exception as exc:                     # noqa: BLE001
            _p(f"  {label:<18} {key:<28} ❗ 조회 실패: {exc}")
            continue
        if not raw:
            # ⚠️ 키가 없어서 못 받은 것을 '지연'으로 세면 오보다(실수 #23).
            if _keysrc.get(src) == "없음":
                _p(f"  {label:<18} {key:<28} ⚪ 판정 불가 — API 키 없음")
                unknown.append(f"{label}(키 없음)")
            else:
                _p(f"  {label:<18} {key:<28} ❗ 관측 없음 — 원천이 비었다"
                   f"(키는 {_keysrc.get(src)})")
                late.append(f"{label}(관측 없음)")
            continue
        j = judge(sid, raw, today)
        if j is None:
            _p(f"  {label:<18} {key:<28} 관측 {raw:<12} ⚪ 규약 없음 "
               f"— macro_cadence.CADENCE 에 추가 필요")
            unknown.append(f"{label}({key})")
            continue
        freq_kr = {"D": "영업일", "W": "주간", "M": "월간",
                   "Q": "분기", "E": "이벤트"}[j["freq"]]
        if j["freq"] == "E":
            verdict = "⚪ 이벤트성 — 지연 판정 안 함"
        elif j["expected"] is None or j["actual"] is None:
            verdict = "❗ 관측 라벨 판독 실패"
            late.append(f"{label}(라벨 {raw})")
        elif j["stale"]:
            unit = {"M": "개월", "Q": "분기", "W": "주"}.get(j["freq"], "일")
            verdict = (f"❌ 지연 의심 — {j['expected']} 까지 나왔어야 함 "
                       f"({j['behind']}{unit} 뒤짐)")
            late.append(f"{label} {raw} (기대 {j['expected']})")
        else:
            verdict = "✅ 정상"
        _p(f"  {label:<18} {key:<28} 관측 {raw:<12} "
           f"{freq_kr}/공표+{j['lag']}일  {verdict}")
        if j["freq"] != "E" and j.get("why"):
            _p(f"  {'':<18} {'':<28} └ {j['why']}")

    _p("")
    _treasury_status(mo)
    _p("")
    _p(f"── 요약: 대상 {len(rows)}개 · 지연 의심 {len(late)}개 · "
       f"규약 없음 {len(unknown)}개")
    for s in late:
        _p(f"   ❌ {s}")
    for s in unknown:
        _p(f"   ⚪ {s}")
    if late or unknown:
        # ⚠️ "판정 불가"를 "정상"으로 요약하지 않는다 — 그게 오보의 씨앗이다.
        _p("   (⚪ 는 판정을 못 한 것이지 정상이 아니다)")
    else:
        _p("   전부 통상 공표 일정 안쪽 — 늦게 보이는 건 원천 공표지연이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
