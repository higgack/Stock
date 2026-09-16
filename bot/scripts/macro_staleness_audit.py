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

    ⚠️⚠️ 판정 글자는 **sweep 이 세는 것**만 쓴다. 2026-09-08 까지 이 섹션은
    도달 실패를 `❗` 로 찍었는데 `audit_sweep._findings` 는 `❌` 만 세고
    `warn` 은 `⚠️` 만 센다 — 즉 재무부가 며칠을 못 닿아도 **일일 결산이
    한 글자도 말하지 않았다**. 사용자가 화면을 눈으로 보고 다섯 번째로
    물어야 했던 이유다(#250 판정 글자는 판정에만 · #24 새 글자는 집계 밖).

    ⚠️ 그리고 판정을 여기서 **재구현하지 않는다** — 제품(`fresher_diag`)이
    쓰는 그 갈래를 그대로 받아 적는다(#35·#38·#169).
    """
    import time
    from datetime import date as _date

    # ⚠️ 제목에 시리즈를 손으로 적지 않는다 — 아래 루프는
    # `mo._TREASURY_SIDS` 를 도는데 제목만 `DGS2/10/30` 이라 T10Y2Y 가
    # 붙은 날부터 화면이 거짓말했다(2026-09-16 독립 리뷰, #55·#34).
    try:
        from bot.market_overview import _TREASURY_SIDS as _TS
        _names = "·".join(sorted(_TS))
    except Exception:                                 # noqa: BLE001
        _names = "판정 불가"
    _p(f"── 재무부 보강({_names}) 상태")
    try:
        from bot.treasury_yield_client import (
            _DIAG_ATTEMPTS, fresher_diag, fresher_reason, probe_failed)
    except Exception as exc:                          # noqa: BLE001
        _p(f"   ❌ 재무부 클라이언트를 못 불러왔다: {type(exc).__name__}: {exc}")
        return
    # 원천이 낼 수 있는 최신 세션(미 휴장일 반영) — 시장타이밍과 같은 함수를
    # 쓴다. 노동절이 끼면 '오늘'이 최선이 아니다(#302).
    best = None
    try:
        from bot.market_timing import _expected_session
        best, _g = _expected_session("US")
    except Exception as exc:                          # noqa: BLE001
        _p(f"   ⚠️ 기대 세션 판정 불가({type(exc).__name__}) — 최선 대비는 생략")
    if best:
        _p(f"   원천이 낼 수 있는 최신 세션: {best}")

    cache_dir = mo._CACHE_DIR / "fred"
    for sid in sorted(mo._TREASURY_SIDS):
        f = cache_dir / f"{sid}_{_date.today().isoformat()}.json"
        base_date = base_val = None
        src = "FRED"
        if f.exists():
            import json
            age_m = (time.time() - f.stat().st_mtime) / 60
            try:
                d = json.loads(f.read_text())
                base_date, base_val = d.get("time"), d.get("value")
                src = d.get("src") or "FRED"
            except Exception:
                d, age_m = {}, 0.0
            _p(f"   {sid}: 디스크캐시 {age_m:.0f}분 전 · 관측 {base_date} · 원천 {src}")
        else:
            _p(f"   {sid}: 디스크캐시 없음")
        if base_date is None or base_val is None:
            # 대조할 게 없으면 통과가 아니다(#54) — 다만 '우리 결함'도 아니다.
            _p(f"      ⚠️ 비교 기준(캐시)이 없어 판정 불가")
            continue
        # ⚠️ 배치라 재시도를 건다. 단발 20초로 판정하면 원천이 느린 날의
        # **동전던지기**가 그대로 결산의 판정이 된다(2026-09-13 실측: 같은
        # 명령 1회차 `no_newer` ✅ / 2회차 `month_failed` — #21·#361b).
        # 렌더 경로는 기본 1회 그대로다(#116 화면 대기 금지).
        code, dg = fresher_diag(str(base_date), float(base_val), sid,
                                attempts=_DIAG_ATTEMPTS)
        _p(f"      대조: {code} — {fresher_reason(code, dg)}")
        shown = dg.get("newer", (base_date,))[0] if code == "ok" else base_date
        if not best:
            continue
        if str(shown) >= best:
            _p(f"      ✅ {sid} 최선({best})까지 왔다")
            # ⚠️ 화면이 최선이어도 **대조 자체는 실패했을 수 있다** — 그러면
            # ✅ 가 "재무부를 못 받았다" 를 덮는다(2026-09-13 VM 실측: 3건
            # 전부 202609 timeout 인데 `--why` 최종 판정이 `✅ 전부 최선까지
            # 왔다` 였다, #41 여유·우연으로 사실을 덮지 말 것). 오늘은 무해해도
            # 내일은 보강이 안 된다. 형제 표면(`--why`)과 **같은 규약**이다(#38).
            # ⚠️ 갈래를 **열거하지 않는다** — 옛 판이 `("no_curve",
            # "month_failed")` 만 봐서 `mismatch`·`no_overlap` 이 조용했다
            # (#24, 2026-09-13 독립 리뷰). `probe_failed` 단일 술어를
            # `--why` 와 **같이** 쓴다 — 각자 열거하면 두 화면이 갈린다(#38).
            #
            # ⚠️ 이 줄이 **못 보는 축**(#274): ⚠️ 는 `audit_sweep` 에서 warn
            # 으로만 세어지고 `report_text` 는 ❌ 가 하나도 없으면 빈 문자열을
            # 돌려주므로, **대조 실패만 있는 날의 일일 결산은 조용하다**.
            # 그래도 ❌ 로 올리지 않는 이유는 재서 답한다 — 재무부 보강은
            # FRED 보다 하루 앞선 값을 당기는 **보조** 경로라 실패해도 화면은
            # FRED 의 최선까지 와 있다(그래서 이 분기다). 며칠 이어지면 화면이
            # 기대 세션보다 뒤처지고, 그건 같은 감사의 **신선도 축**
            # (`stale_bucket`)이 ❌ 로 잡는다. 여기서 ❌ 를 내면 일시적
            # 타임아웃 한 번이 매일 못 고칠 ❌ 가 되어 진짜 ❌ 를 가린다
            # (#260·#25). 전문은 `python -m bot.audit_sweep` 에 실린다.
            if probe_failed(code):
                _p(f"      ⚠️ {sid} 다만 이번 대조는 실패했다({code}) — 화면"
                   f" 값이 우연히 최선이었을 뿐이다. {fresher_reason(code, dg)}")
        elif code in ("no_newer",):
            # 원천에도 그보다 새 관측이 없다 = 우리가 고칠 게 없다(#260).
            _p(f"      ⚠️ {sid} 최선({best})보다 뒤지지만 재무부에도 더 새 값이"
               " 없다 — 원천 공표 지연")
        else:
            # 도달 실패·겹침 없음·검산 불일치 = **우리가 고칠 축이 있다**.
            #
            # ⚠️ 판정 줄은 **혼자서도 행동 가능**해야 한다(실수 #356). sweep 은
            # ❌ 줄만 결산에 올리므로, 시리즈 이름과 갈래 근거가 여기 없으면
            # 세 만기가 **글자까지 같은 줄 셋**이 되고 바로 윗줄이 들고 있던
            # 사유(조회 달·표에 있는 날)는 통째로 버려진다(#292·#320·#114).
            #
            # ⚠️ 그리고 `src` 를 읽어 놓고 판정에 안 쓰면 거짓을 적는다 —
            # `UST` 면 보강은 **이미 걸린 것**이고 실패한 건 재검증이다.
            # 둘은 처방이 다르다(배선 확인 vs 원천 대조, #82·#55·#165).
            what = ("보강 뒤 재검증이 실패했다" if src == "UST"
                    else "보강이 걸리지 않았다")
            _p(f"      ❌ {sid}(원천 {src}) 화면 {shown} 이 최선({best})보다"
               f" 뒤처졌고 {what}({code}) — {fresher_reason(code, dg)}")


# ⚠️ 판정은 `bot/macro_cadence.py` 로 옮겼다(2026-09-09) — `liquidity_audit`
# 이 같은 판정을 **자체 재구현**해 1주기 지연을 매일 ❌ 로 찍고 있었다(#38·
# #147 한 화면에서 고치면 같은 계산을 하는 다른 화면을 즉시 grep). 옛 import
# 경로는 그대로 살려 둔다(#222) — 이 모듈을 부르던 회귀가 계속 계약을 잰다.
from bot.macro_cadence import stale_verdict  # noqa: E402,F401



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
    src_lag: list[str] = []
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
            _p(f"  {label:<18} {key:<28} ❌ 조회 실패: {exc}")
            late.append(f"{label}(조회 실패)")
            continue
        if not raw:
            # ⚠️ 키가 없어서 못 받은 것을 '지연'으로 세면 오보다(실수 #23).
            if _keysrc.get(src) == "없음":
                _p(f"  {label:<18} {key:<28} ⚪ 판정 불가 — API 키 없음")
                unknown.append(f"{label}(키 없음)")
            else:
                _p(f"  {label:<18} {key:<28} ❌ 관측 없음 — 원천이 비었다"
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
            verdict = "❌ 관측 라벨 판독 실패"
            late.append(f"{label}(라벨 {raw})")
        elif j["stale"]:
            bucket, verdict = stale_verdict(j)
            (src_lag if bucket == "src_lag" else late).append(
                f"{label} {raw} (기대 {j['expected']})")
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
       f"원천 공표 지연 {len(src_lag)}개 · 규약 없음 {len(unknown)}개")
    # ⚠️ **요약이 항목을 다시 나열하면 같은 결함이 두 번 세어진다.** 위 표가
    # 이미 지연 항목마다 ❌ 한 줄씩 찍는데 여기서 또 찍어, sweep 의 '❌ N건'
    # 이 정확히 두 배가 됐다(2026-08-26 실측: 실제 2건 → 4건, #45 같은
    # 모집단을 두 번 세면 갈라진다). 요약은 **세기만** 하고 이름은 위 표가
    # 댄다. ⚪(판정 불가)는 위 표에 ❌ 로 안 찍히므로 여기 남긴다.
    for s in unknown:
        _p(f"   ⚪ {s}")
    if src_lag:
        # ⚠️ 사실은 위 표가 이미 폭까지 말했다(#41) — 여기선 **처방**만.
        _p("   (⚠️ 원천 공표 지연은 우리가 고칠 게 없다 — 원천이 실으면 "
           "다음 수집에서 자동 반영된다)")
    if late or unknown:
        # ⚠️ "판정 불가"를 "정상"으로 요약하지 않는다 — 그게 오보의 씨앗이다.
        _p("   (⚪ 는 판정을 못 한 것이지 정상이 아니다)")
    elif not src_lag:
        _p("   전부 통상 공표 일정 안쪽 — 늦게 보이는 건 원천 공표지연이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
