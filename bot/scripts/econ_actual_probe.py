"""경제캘린더 '실제치'가 왜 빈칸인가 — 발표일↔관측 매칭을 단계별로.

사용자 2026-08-19(캡처 4·5): PCE·소비자심리 카드에 방향성(1M/3M/6M/1Y)은
나오는데 **실제치만** 없다. 방향성이 나온다는 건 관측 시계열은 정상이라는
뜻이라, 빈 건 `find_actual_value` 의 **매칭 창**이다.

PCE 는 원인이 확정됐다(관측=월초 날짜, 발표=익월 말 → 간격 ~60일인데 창
상한이 45일이라 구조적으로 못 맞춤). 나머지는 여기서 실측한다:

  이벤트마다 최근 발표일 · 설정된 창 · 그 창에 들어온 관측 · 실제 간격을
  나란히 찍는다. 히트가 없으면 **가장 가까운 관측이 며칠 밖이었는지**까지
  보여 준다 — 그 숫자가 곧 고쳐야 할 창 크기다(추측 0).

    cd ~/stock && .venv/bin/python -m bot.scripts.econ_actual_probe
    cd ~/stock && .venv/bin/python -m bot.scripts.econ_actual_probe pce umich

읽기 전용 · LLM 0 · ₩0(FRED 무료).
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

_PROBE_VER = 4


def _p(*a):
    print(*a, flush=True)


def main() -> int:
    from bot import econ_calendar as ec
    from bot import fred_client

    want = {a.lower() for a in sys.argv[1:]}
    today = date.today().isoformat()
    _p(f"econ_actual_probe v{_PROBE_VER} · 기준일 {today} · "
       f"기본 창 상한 {ec._DEFAULT_MAX_LAG}일")
    # 실수 #23 — 프로브는 봇 엔트리포인트가 아니라 .env 가 안 들어온다.
    from bot.env_keys import env_key
    _p(f"FRED_API_KEY: {'있음' if env_key('FRED_API_KEY') else '❌ 없음 — 아래 전부 조회 실패한다'}")

    for r in ec._RELEASES:
        if want and r["key"] not in want:
            continue
        sid = ec._SERIES_FOR_ACTUAL.get(r["key"])
        _p("")
        _p(f"── {r['key']}  {r['label']}")
        if not sid:
            _p("   실제치 매핑 없음(_SERIES_FOR_ACTUAL) — 이 카드는 원래 값이 없다")
            continue
        lo = int(r.get("actual_min_lag_days", 0))
        hi = int(r.get("actual_max_lag_days", ec._DEFAULT_MAX_LAG))
        _p(f"   시리즈 {sid} · 창 = 발표일 −{hi}일 ~ −{lo}일")
        try:
            rid = fred_client.find_release_id(r["search"])
            if not rid:
                _p(f"   ❌ release_id 미확인(search={r['search']!r}) — 카드 자체가 비노출")
                continue
            t0 = date.fromisoformat(today)
            dates = fred_client.fetch_release_dates(
                rid, (t0 - timedelta(days=60)).isoformat(),
                (t0 + timedelta(days=180)).isoformat())
            info = ec.upcoming_and_recent(dates, today)
        except Exception as exc:                               # noqa: BLE001
            _p(f"   ❌ 발표일 조회 실패 {type(exc).__name__}: {exc}")
            continue
        if not info["recent"]:
            _p("   최근 45일 내 발표 없음 — 실제치 칸이 없는 게 정상")
            continue
        try:
            hist_start = (date.fromisoformat(today)
                          - timedelta(days=max(400, hi + 400))).isoformat()
            obs = fred_client.fetch_history(sid, start=hist_start)
        except Exception as exc:                               # noqa: BLE001
            _p(f"   ❌ 관측 조회 실패 {type(exc).__name__}: {exc}")
            continue
        if not obs:
            _p("   ❌ 관측 0개 — 시리즈가 바뀌었거나 중단(매핑을 고쳐야 한다)")
            continue
        _p(f"   관측 {len(obs)}개 · 최신 {obs[-1][0]} = {obs[-1][1]}")
        for rd in info["recent"]:
            # v2 — 프로덕션과 **같은 순서**로: 원천 vintage 먼저, 창은 폴백.
            # (v1 은 창만 봐서 "7/14·8/12 가 같은 값" 인 걸 ✅ 로 찍었다.)
            vin = fred_client.fetch_observation_asof(sid, rd)
            if vin:
                gap = (date.fromisoformat(rd) - date.fromisoformat(vin[0])).days
                _p(f"   {rd}  ✅ {vin[1]}  ({vin[0]} 관측 · 간격 {gap}일) · vintage")
                continue
            hit = ec.find_actual_value(obs, rd, max_lag_days=hi, min_lag_days=lo)
            if hit:
                gap = (date.fromisoformat(rd) - date.fromisoformat(hit[0])).days
                _p(f"   {rd}  ✅ {hit[1]}  ({hit[0]} 관측 · 간격 {gap}일) · 창 폴백")
                continue
            # 왜 못 맞췄나 — 가장 가까운 관측과의 간격을 그대로 찍는다.
            near = min(obs, key=lambda o: abs(
                (date.fromisoformat(rd) - date.fromisoformat(o[0])).days))
            gap = (date.fromisoformat(rd) - date.fromisoformat(near[0])).days
            why = ("관측이 발표일보다 나중" if gap < 0 else
                   f"간격 {gap}일 > 상한 {hi}일" if gap > hi else
                   f"간격 {gap}일 < 하한 {lo}일")
            _p(f"   {rd}  ❌ 빈칸 — 가장 가까운 관측 {near[0]}({near[1]}) · {why}")

        # v3 — **방향성 칸까지 검산**한다(사용자 2026-08-20 "이 숫자랑 방향성도
        # 맞는거 맞어?"). 화면은 1M/3M/6M/1Y 를 %로만 보여줘서 어느 관측과
        # 비교한 건지 알 수 없다 — 여기선 **비교 대상 관측일·값**을 함께 찍어
        # 사용자가 직접 나눗셈할 수 있게 한다.
        trend = ec._build_trend_summary(obs, [], is_rate=bool(r.get("is_rate")))
        if trend:
            from bot.macro_cadence import median_month_gap
            base = obs[-1]
            _gap = median_month_gap(obs)
            _u = "%p" if trend.get("is_rate") else "%"
            _p(f"   방향성 기준 관측 {base[0]} = {base[1]}"
               f" · 관측주기 {_gap}개월 · 단위 {_u}")
            for lbl, days_back, key in (("1M", 30, "m1_pct"), ("3M", 90, "m3_pct"),
                                        ("6M", 180, "m6_pct"), ("1Y", 365, "y1_pct")):
                cutoff = (date.fromisoformat(base[0])
                          - timedelta(days=days_back)).isoformat()
                hit = ec._value_on_or_before(obs, cutoff)
                pct = trend.get(key)
                if not hit:
                    calc = None
                elif trend.get("is_rate"):
                    calc = base[1] - hit[1]
                elif hit[1]:
                    calc = (base[1] - hit[1]) / abs(hit[1]) * 100
                else:
                    calc = None
                agree = (pct is not None and calc is not None
                         and abs(pct - calc) < 0.05)
                # 창이 시리즈 주기보다 짧으면 **빈칸이 정상**이다 — 숫자가
                # 있으면 그게 오히려 거짓 라벨(1M 이 사실 직전 분기 비교).
                _short = _gap > {"1M": 1, "3M": 3, "6M": 6, "1Y": 12}[lbl]
                mark = ("✅ (주기 초과 — 빈칸 정상)" if pct is None and _short
                        else "❌ **주기보다 짧은 창에 숫자**" if _short
                        else "—" if pct is None else "✅" if agree else "❌")
                _p(f"     {lbl:3} 화면 {(f'%+.1f{_u}' % pct) if pct is not None else '—':>8}"
                   f" ← {hit[0] if hit else '—'} {hit[1] if hit else '—'}"
                   f"  (검산 {(f'%+.2f{_u}' % calc) if calc is not None else '—'})"
                   f" {mark}")

    _p("")
    _p("읽는 법: 'vintage' = 원천이 그 발표일 당시 갖고 있던 값(추정 0).")
    _p("        '방향성' 줄의 ← 뒤가 **비교 대상 관측**이다 — 화면 %와 검산 %가")
    _p("        같으면 ✅. 발표일과 관측기간이 다른 건 정상(7/14 발표=6월분).")
    _p("        분기 시리즈(ECI·GDP)의 1M 은 **빈칸이 정상** — 직전 분기와")
    _p("        비교하게 돼 1M·3M 이 같은 숫자가 되기 때문. 실업률처럼 값이")
    _p("        퍼센트인 시리즈는 %p 로 표기한다(4.2→4.1 = -0.1%p).")
    _p("        '창 폴백' 은 vintage 를 못 받아 시차 창으로 고른 근사값 —")
    _p("        인접 발표일이 **같은 값**이면 그 근사가 틀린 것이다.")
    _p("        ❌ 가 '간격 N일 > 상한' 이면 그 이벤트의 actual_max_lag_days 를")
    _p("        N 이상으로. '< 하한' 이면 actual_min_lag_days 가 과하다.")
    _p("        '관측 0개' 면 _SERIES_FOR_ACTUAL 매핑이 죽은 시리즈를 가리킨다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
