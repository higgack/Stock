"""글로벌 스냅샷 · Macro Snapshot 의 **발표지표 신선도 감사**.

사용자 2026-08-18: "실시간으로 가져오지 않는 지표에 대해서 최신의 것을
제때제때 잘 가져오는지 꼼꼼히 확인해줘."

화면 카드를 눈으로 훑는 대신, **화면이 쓰는 바로 그 경로**로 관측일을 받아
`bot/macro_cadence.CADENCE` 의 공표 규약과 대조한다. 값은 안 본다 — 신선도만.

    cd ~/stock && .venv/bin/python -m bot.scripts.macro_staleness_audit

`--history` = 카드 계열마다 **우리 캐시가 새 기간을 처음 본 날**(관측기간 종료 +N일)을
공표 규약과 나란히 찍는다 — "원천이 늦게 싣나, 우리가 늦게 받나" 의 답이다(실수 #413·
#415: 한국 수출은 이 측정으로 ECOS 재게시가 +34일임을 알았다). 캐시 파일만 읽는다
(네트워크 0).

읽기 전용 · LLM 0 · ₩0.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

_PROBE_VER = 4


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



def empty_diag(src: str, sid: str, window_start: str = "") -> tuple[str, str]:
    """관측이 **하나도 없을 때**의 갈래와 문구 — (bucket, 화면 줄).

    ⚠️ 2026-09-17 일일 감사가 `❌ PPI 원자재 (YoY) fred:PPIACO 관측 없음 —
    원천이 비었다` 를 냈는데, 그 한 줄로는 **처방이 정해지지 않는다**(#82):
      (a) 우리 조회가 실패했다        → 다시 받는다 / 코드를 고친다  ❌
      (b) 원천에 그 창의 관측이 없다  → 우리가 고칠 게 없다          ⚠️
          (계열 중단·개편이면 카탈로그를 바꿔야 하고, 그것도 사실을
           **재고 나서** 할 일이다)
      (c) 못 물어봤다(키 없음·메타 실패) → 판정 불가                 ⚪
    원천이 **스스로 보고하는** `observation_end` 를 **우리가 요청한 창의
    시작일**(`window_start`)과 대조해 (a)와 (b)를 가른다(#86·#318·#366).

    ⚠️⚠️ 첫 판은 그 **대조를 안 하고** `oe` 가 있기만 하면 `src_lag` 를
    돌려줬다 — 독립 리뷰가 배포 전에 잡았다. 그러면 FRED 조회 실패(429·
    타임아웃)가 '우리가 고칠 게 없다' 로 둔갑하고 ❌ 가 **도달 불가**가 되어
    일일 결산이 조용해진다(#41·#54·#82·#260 의 정반대 방향 — 고칠 수 있는
    것을 안 알리는 쪽). 게다가 원천이 오늘까지 데이터가 있다고 말하는데
    화면은 '없다' 고 적는 **거짓 진술**이다(#165·#292).

    ⚠️ 창을 모르면(`window_start=""`) 대조를 **하지 않고** 판정 불가로
    남긴다 — 못 잰 것을 단정하지 않는다(#54·#165). ECOS 는 이 메타 축이
    다르므로 여기서 갈래를 주장하지 않는다.
    """
    if src == "ecos":
        return "late", "❌ 관측 없음 — ECOS 응답에 행이 없다"
    try:
        from bot.fred_client import fetch_series_meta
        meta = fetch_series_meta(sid) or {}
    except Exception as exc:                                   # noqa: BLE001
        return "unknown", f"⚪ 판정 불가 — 원천 메타 조회 실패({exc})"
    oe = str(meta.get("observation_end") or "")
    if not oe:
        # ⚠️ `fetch_series_meta` 는 네트워크·HTTP 실패에도 None 을 준다 —
        # "원천이 안 준다" 고 적으면 원천 탓으로 읽힌다(#82·#292 · 리뷰 L4).
        return "unknown", "⚪ 판정 불가 — 원천 메타를 못 받았다(조회 실패 또는 미제공)"
    if not window_start:
        return "unknown", (f"⚪ 판정 불가 — 요청 창을 몰라 대조 못 함"
                           f"(원천 observation_end={oe})")
    if oe >= window_start:
        return "late", (f"❌ 우리 조회가 빈손이었다 — 원천은 그 창에 관측이 있다"
                        f"(observation_end={oe} ≥ 창 시작 {window_start})")
    return "src_lag", (f"⚠️ 원천에 그 창의 관측이 없다"
                       f"(observation_end={oe} < 창 시작 {window_start}) — "
                       f"우리가 고칠 게 없다")


def customs_fallback_line(cs: dict) -> str:
    """관세청 카드가 **관세청을 못 써 폴백했으면** 그 한 줄(순수), 아니면 "".

    폴백은 조용하면 안 된다(#42a) — 화면엔 'ECOS(관세청 …)' 로 뜨지만 결산은 ❌·⚠️ 만
    올린다(#303). 갈래로 기호를 가른다(#82·#260): 원천이 잠깐 막힌 것(조회 실패 —
    타임아웃·429·5xx·한도)은 기다리면 풀리니 ⚠️, 키·경로·응답 형식·단위 불일치·행 없음은
    **우리가 고칠 것**이라 ❌. 줄이 혼자서 행동 가능하게 사유 원문을 같이 싣는다(#356).
    ⚠️ ECOS 까지 비었으면 **카드가 화면에서 빠진다** — 갈래와 무관하게 ❌ 한 줄로,
    관세청 사유를 같이 싣는다. 폴백 줄과 '관측 없음' 줄을 따로 내면 한 카드가 결산에서
    두 번 세어진다(#45·#250)."""
    if not cs:
        return ""
    if cs.get("src") == "관세청":
        # 관세청으로 그렸지만 **단위를 못 잰 채**다(ECOS 와 겹치는 달이 없음) — 조용하면 단위가
        # 바뀐 날 1000배 틀린 값이 ✅ 로 통과한다(리뷰 M1 · #54). ECOS 가 비었거나 뒤처진 탓이라
        # 기다리면 풀린다 — ⚠️.
        if "verified" in cs and cs.get("verified") is None:
            return (f"⚠️ 관세청 값을 ECOS 대조 없이 그렸다 — {str(cs.get('check') or '')[:200]}"
                    " (ECOS 가 돌아오면 다음 수집에서 대조된다)")
        return ""
    why = str(cs.get("why") or "못 받음")
    detail = str(cs.get("detail") or "")[:200]
    if not cs.get("points"):
        return f"❌ 관측 없음 — 관세청({why}: {detail})·ECOS 둘 다 행이 없다"
    mark = "⚠️" if why == "조회 실패" else "❌"
    return f"{mark} 관세청 원천을 못 써 ECOS 로 그렸다 — {why}: {detail}"


def audit_rows(ms, mo) -> list[tuple[str, str, str, str, int]]:
    """감사가 훑는 행 — (표면, 라벨, "src:id", 경로, 창 일수). 순수에 가깝게.

    ⚠️ 행마다 **그 표면의 화면이 쓰는 선택기**를 같이 싣는다(#35). 매크로
    스냅샷은 `_fred_fetch_series(sid, 400)` 로 그리고, 글로벌 스냅샷만 YoY
    디스패치(`fred_indicator_fetch`)를 탄다 — 하나로 뭉뚱그리면
    CPIAUCSL·PCEPILFE 처럼 **두 화면에 다 있는** 시리즈가 자기 화면과 다른
    창으로 재어진다(독립 리뷰 H1 실측 2행).
    ⚠️ `main` 안에 인라인으로 두면 네트워크 없이 값으로 못 잰다(#176).
    """
    rows: list[tuple[str, str, str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for surface, defs in (("Macro/국내", ms.DOMESTIC), ("Macro/글로벌", ms.GLOBAL)):
        for _k, label, _u, src, sid, _d in defs:
            if src in ("fred", "fred_yoy", "ecos", "customs") and (src, sid) not in seen:
                seen.add((src, sid))
                rows.append((surface, label, f"{src}:{sid}", "spot", 400))
    for label, sid, _u, lb in mo.FRED_INDICATORS:
        if sid and ("fred", sid) not in seen:
            seen.add(("fred", sid))
            rows.append(("글로벌 스냅샷", label, f"fred:{sid}", "screen", lb))
    return rows


_ECOS_FILE = re.compile(r"^series_v\d+_(?P<key>.+)_\d+_(?P<d>\d{4}-\d{2}-\d{2})\.json$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def first_seen(daily: list[tuple[str, str]], freq: str) -> list[tuple[str, str, str]]:
    """[(파일 날짜 YYYY-MM-DD, 그날 본 최신 기간 원문)] → [(기간, 처음 본 날, 직전 기록 날)](순수).

    날짜순으로 훑어 **최신 기간이 새로 바뀐 날**만 남긴다. `직전 기록 날` 은 그 기간이 아직
    없던 마지막 파일의 날짜다 — 원천은 그 다음 날부터 처음 본 날 사이 어딘가에서 실었다.
    기록의 첫 파일에 이미 있던 기간은 그 전에 실렸을 수 있어 직전 기록이 '' 다(#54 모르는
    것을 사실처럼 적지 않는다). 못 읽은 파일은 '없었다' 는 증거가 아니라 건너뛴다.
    기간 비교는 공표 규약과 같은 함수(`parse_period_end`)로 한다(#38)."""
    from bot.macro_cadence import parse_period_end
    out: list[tuple[str, str, str]] = []
    best: Optional[date] = None
    prev = ""
    for d, raw in sorted(daily):
        end = parse_period_end(raw, freq)
        if end is None:
            continue
        if best is None or end > best:
            out.append((raw, d, prev))          # 첫 기록이면 prev 는 아직 '' 다
            best = end
        prev = d
    return out


def _history_daily(src: str, sid: str, ecos_dir: Path, fred_dir: Path) -> list[tuple[str, str]]:
    """캐시 파일들 → [(파일 날짜, 그날 본 최신 기간)]. 못 읽는 파일은 건너뛴다(#331)."""
    rows: list[tuple[str, str]] = []
    if src in ("ecos", "customs"):
        for f in ecos_dir.glob("series_v*.json"):
            m = _ECOS_FILE.match(f.name)
            if not m or m.group("key") != sid:
                continue
            try:
                pts = json.loads(f.read_text(encoding="utf-8"))
                last = max(str(t) for t, _v in pts) if pts else ""
            except Exception:                                  # noqa: BLE001
                continue
            if last:
                rows.append((m.group("d"), last))
    elif src in ("fred", "fred_yoy"):
        # 파일명은 `{sid}_{YYYY-MM-DD}.json`(`market_overview._fred_fetch_series`) — glob 의
        # `_` 가 다른 계열(`DGS1` vs `DGS10_…`)을 이미 막으므로, 남는 일은 가운데가 **날짜인지**
        # 보는 것이다(날짜가 아니면 뒤의 기간 계산이 깨진다).
        for f in fred_dir.glob(f"{sid}_*.json"):
            d = f.name[len(sid) + 1:-len(".json")]
            if not _DATE.fullmatch(d):
                continue
            try:
                t = str((json.loads(f.read_text(encoding="utf-8")) or {}).get("time") or "")
            except Exception:                                  # noqa: BLE001
                continue
            if t:
                rows.append((d, t))
    return rows


def history_lines(ms, **kw) -> list[str]:
    """`history_report` 의 줄만."""
    return history_report(ms, **kw)[0]


def history_report(ms, *, ecos_dir: Optional[Path] = None, fred_dir: Optional[Path] = None,
                   keep: int = 4) -> tuple[list[str], int, int]:
    """카드 계열마다 새 기간을 **처음 본 날**과 공표 규약을 나란히(`--history`)
    → (줄, 기록을 잰 계열 수, 대상 계열 수).

    원천이 새 기간을 실은 날 ≈ 우리 캐시가 그걸 처음 담은 날(하루 오차 — 캐시 파일은 날짜별
    마지막 수집본이다). 규약보다 늦으면 '원천이 늦게 싣는다' 이고, 규약 안인데 화면이 늦으면
    우리 캐시·렌더 쪽이다(#82 갈래). 관세청 계열은 캐시가 날짜별로 안 남아 **ECOS 대조본**의
    기록을 싣는다 — 라벨이 그렇다고 밝힌다(#34)."""
    from bot.macro_cadence import CADENCE, GRACE_DAYS, parse_period_end
    if ecos_dir is None:
        from bot.bok_ecos_client import _CACHE_DIR as ecos_dir
    if fred_dir is None:
        from bot.market_overview import _CACHE_DIR as _mo_dir
        fred_dir = _mo_dir / "fred"
    out: list[str] = []
    measured = total = 0
    for defs in (ms.DOMESTIC, ms.GLOBAL):
        for _k, label, _u, src, sid, _d in defs:
            if src not in ("fred", "fred_yoy", "ecos", "customs"):
                continue
            total += 1
            spec = CADENCE.get(sid)
            name = f"{src}:{sid}" + (" · ECOS 대조본" if src == "customs" else "")
            daily = _history_daily(src, sid, Path(ecos_dir), Path(fred_dir))
            if not spec:
                out.append(f"  {label:<14} {name:<30} ❓ 공표 규약 없음 — 대조 불가")
                continue
            if not daily:
                out.append(f"  {label:<14} {name:<30} ❓ 캐시 기록 없음 — 첫 등장을 잴 재료가 없다")
                continue
            freq, lag, why = spec
            if freq == "E":
                out.append(f"  {label:<14} {name:<30} ⚪ 이벤트성({why}) — 첫 등장 판정 안 함")
                continue
            days = sorted(d for d, _r in daily)
            measured += 1
            out.append(f"  {label:<14} {name:<30} 캐시 {len(daily)}일치 {days[0]}~{days[-1]}"
                       f" · 규약 +{lag}일({why})")
            limit = lag + GRACE_DAYS
            for raw, d, prev in first_seen(daily, freq)[-keep:]:
                end = parse_period_end(raw, freq)
                hi = (date.fromisoformat(d) - end).days          # 늦어도 이날엔 있었다
                gap = ""
                if not prev:
                    verdict = "❓ 기록 시작 전부터 있었다 — 처음 실린 날은 모른다"
                else:
                    lo = (date.fromisoformat(prev) - end).days + 1   # 빨라도 직전 기록 다음 날
                    if (date.fromisoformat(d) - date.fromisoformat(prev)).days > 1:
                        gap = f" · 직전 기록 {prev}(사이가 비어 그 안 어디서 실렸는지 모른다)"
                    if lo > limit and src == "customs":
                        # 이 행은 ECOS **대조본**이다 — 카드는 관세청이라 이 지연을 안 탄다(리뷰 L13).
                        # 경고 글자를 붙이면 카드가 늦다고 읽힌다(#34).
                        verdict = (f"ECOS 재게시가 규약보다 최소 {lo - lag}일 늦다 "
                                   "(카드 원천 관세청은 이 지연을 안 탄다)")
                    elif lo > limit:
                        verdict = f"⚠️ 규약보다 최소 {lo - lag}일 늦게 실렸다"
                    elif hi <= limit:
                        verdict = "✅ 규약 안"
                    else:
                        verdict = "❓ 기록 사이가 비어 규약 안인지 못 가른다"
                out.append(f"      {raw:<12} 처음 본 날 {d} (기간 종료 +{hi}일){gap}  {verdict}")
    return out, measured, total


def history() -> int:
    """→ rc 0 한 계열이라도 기록을 쟀다 · 1 잰 계열 0(대조 0건은 통과가 아니다, #54)."""
    from bot import macro_snapshot as ms
    _p(f"macro_staleness_audit --history v{_PROBE_VER} · 캐시 첫 등장(우리가 처음 받은 날) "
       f"— 네트워크 0 · 하루 오차(캐시는 날짜별 마지막 수집본)")
    lines, measured, total = history_report(ms)
    for ln in lines:
        _p(ln)
    _p(f"── 기록을 잰 계열 {measured}/{total}개")
    if not measured:
        _p("  ❓ 잰 계열이 0 — 캐시 기록이 없다(대조 0건은 통과가 아니다, #54)")
        return 1
    return 0


def main(argv: Optional[list] = None) -> int:
    if "--history" in (argv or []):
        return history()
    from bot.macro_cadence import (CADENCE, GRACE_DAYS, _CADENCE_VER, judge)
    from bot.env_keys import env_source
    from bot import macro_snapshot as ms
    from bot import market_overview as mo

    today = (datetime.utcnow() + timedelta(hours=9)).date()
    _p(f"macro_staleness_audit v{_PROBE_VER} · cadence v{_CADENCE_VER} · "
       f"grace {GRACE_DAYS}일 · 기준 {today} (KST)")
    _keysrc = {"fred": env_source("FRED_API_KEY"),
               "fred_yoy": env_source("FRED_API_KEY"),
               "ecos": env_source("BOK_ECOS_API_KEY"),
               "customs": env_source("DATA_GO_KR_API_KEY")}
    _p(f"키: FRED_API_KEY={_keysrc['fred']} · "
       f"BOK_ECOS_API_KEY={_keysrc['ecos']} · "
       f"DATA_GO_KR_API_KEY={_keysrc['customs']}")
    _p("")

    # 화면에 실제로 뜨는 발표지표만(실시간 가격 카드 src='yf' 는 대상 아님).
    rows = audit_rows(ms, mo)

    late: list[str] = []
    src_lag: list[str] = []
    # 기다리면 풀리는 상태(관세청 조회 실패 · ECOS 대조 불가) — '원천 공표 지연' 과 처방은 같아도
    # 사실이 다르다: 원천은 실었는데 **우리가 잠깐 못 받았다**(리뷰 L5 · #34·#292)
    wait: list[str] = []
    unknown: list[str] = []
    for surface, label, key, mode, lb in rows:
        src, sid = key.split(":", 1)
        raw = ""
        win_start = ""
        fell_back = False          # 관세청 카드가 ECOS 로 폴백 — 그 탓의 지연은 한 번만 센다
        try:
            if src == "ecos":
                pts = ms._ecos_series(sid)
                raw = pts[-1][0] if pts else ""
            elif src == "customs":
                # 화면과 **같은 함수**(관세청 → ECOS 대조 → 폴백)로 묻는다(#35·#176).
                _cs = ms._customs_series(sid)
                pts = _cs.get("points") or []
                raw = pts[-1][0] if pts else ""
                key += f" ·{_cs.get('src') or '원천 없음'}"
                _fb = customs_fallback_line(_cs)
                if _fb:
                    _p(f"  {label:<18} {key:<28} {_fb}")
                    (wait if _fb.startswith("⚠️") else late).append(
                        f"{label}(관세청 {_cs.get('why') or '단위 미대조'})")
                    if not pts:
                        continue       # 카드가 빠졌다 — 위 한 줄이 이미 셌다(#45)
                    # 관세청으로 그렸다면(단위 미대조 줄) 폴백이 아니다 — 지연 판정은 따로 센다
                    fell_back = _cs.get("src") != "관세청"
            else:
                # ⚠️ 화면이 쓰는 그 선택기로 묻는다 — 옛 판은 전 행을
                # `_fred_fetch_series(sid, 400)` 로 물어 **YoY 카드**(730일
                # 창)를 다른 경로로 쟀다(#35·#169). 그리고 그걸 고치며
                # 전 행을 YoY 디스패치로 보내면 이번엔 **매크로 스냅샷**
                # 행이 자기 화면과 갈린다 — 표면별로 가른다(리뷰 H1).
                if mode == "screen":
                    spot = mo.fred_indicator_fetch(sid, lb)
                    win = mo.fred_indicator_window(sid, lb)
                else:
                    spot = mo._fred_fetch_series(sid, lb)
                    win = lb
                win_start = str(today - timedelta(days=win))
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
                # '관측 없음' 은 갈래가 셋인데 처방이 다 다르다(#82):
                #   우리 조회 실패 / 원천이 그 창에 관측이 없음(계열 중단·
                #   개편) / 못 물어봄. 원천이 **스스로 보고하는**
                #   `observation_end` 가 그걸 가른다(#86·#318·#366) —
                #   고칠 수 없는 것을 ❌ 로 매일 내면 진짜 ❌ 를 가린다(#260).
                # ⚠️ 원천 메타는 **한 번만** 묻고 판정·문구가 나눠 쓴다
                # (두 번 물으면 그 사이 갱신된 값의 나이를 옛 판정에
                # 붙인다, #160).
                _b, _txt = empty_diag(src, sid, win_start)
                _p(f"  {label:<18} {key:<28} {_txt}")
                {"src_lag": src_lag, "unknown": unknown}.get(
                    _b, late).append(f"{label}(관측 없음)")
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
        elif j["stale"] and fell_back:
            # 폴백한 원천(ECOS)이 뒤처진 것은 위 폴백 줄과 **같은 원인**이다 — 두 번 세면
            # 결산의 ❌ 가 두 배가 된다(#45·#250). 판정 글자 없이 사실만 적는다.
            verdict = f"뒤처짐(기대 {j['expected']}) — 위 폴백 탓, 따로 세지 않는다"
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
       f"원천 공표 지연 {len(src_lag)}개 · 일시 상태 {len(wait)}개 · 규약 없음 {len(unknown)}개")
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
    if wait:
        # 판정 글자 없이 처방만(글자가 있으면 sweep 이 같은 결함을 한 번 더 센다, #289)
        _p("   (일시 상태 = 관세청 조회가 잠깐 막혔거나 대조할 ECOS 가 비었다 — 다음 수집에서 "
           "다시 잰다)")
    if late or unknown:
        # ⚠️ "판정 불가"를 "정상"으로 요약하지 않는다 — 그게 오보의 씨앗이다.
        _p("   (⚪ 는 판정을 못 한 것이지 정상이 아니다)")
    elif not src_lag and not wait:
        _p("   전부 통상 공표 일정 안쪽 — 늦게 보이는 건 원천 공표지연이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
