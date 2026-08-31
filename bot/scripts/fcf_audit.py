"""FCF 정확도 감사 — 값이 맞는가 · 세 화면이 같은 값을 보는가.

읽기 전용·LLM 0·₩0. **전 시장**.

⚠️ 왜 필요한가(사용자 2026-08-21): "FCF 를 제대로 계산 or 가져오고 있는지,
이를 제대로 재무재표탭과 분기실적탭에 반영하고 있는지 … 이건 숫자를 내가
판단하기 어려운 영역이라서". 사람이 눈으로 못 재는 값은 **기계가 재야 한다**.

FCF 는 한 종목에 대해 **세 화면**에 실린다:
  · 밸류에이션 탭 분기·연간 재무추이 표 — KR 은 DART 현금흐름표
  · 재무제표 탭 수익성 추이 차트     — 전 시장 yfinance 현금흐름표
  · 분기실적 탭 인포그래픽 FCF 차트  — KR=DART · 그 외=yfinance
원천이 둘이라 **정의 차이로 값이 갈릴 수 있다**(#34 같은 이름 다른 시리즈).
그걸 가정하지 않고 **잰다**.

검사 다섯:
  ① 재계산  — 화면 값이 그 화면의 재료로 다시 계산한 값과 같은가
  ② 교차출처 — 같은 기간의 DART FCF 와 yfinance FCF 차이(KR)
  ③ 표면일치 — 인포그래픽과 밸류에이션 표가 같은 payload 를 보는가
  ④ 검산    — 회계연도에 맞춘 분기 4개 합 == 연간(#99 창 정렬)
  ⑤ 누적냄새 — 단조 증가 + 마지막 분기 ≈ 연간

⚠️ 프로브는 **화면이 쓰는 그 경로**를 그대로 부른다 — 재구현하면 제품과
다른 걸 잰다(#35). 그래서 값 수집은 전부 제품 함수 호출이다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.fcf_audit 004370.KS AAPL 7203.T
    # 인자 없으면 관심종목에서 시장을 섞어 몇 개
"""

from __future__ import annotations

import argparse
import sys
import time as _time

_AUDIT_VER = 2
_GAP_OK = 1.0        # 교차출처 허용 차이(%) — 정의가 같으면 소수점까지 맞는다
_SUM_OK = 5.0        # 분기합 vs 연간 허용 차이(%)


def _pct(a, b) -> float | None:
    """|a−b| / |b| × 100. b 가 0/None 이면 None(판정불가)."""
    if a is None or b is None or not b:
        return None
    return abs(a - b) / abs(b) * 100.0


def _mark(gap: float | None, tol: float) -> str:
    if gap is None:
        return "❓ 판정불가"
    return ("✅" if gap <= tol else "❌") + f" 차이 {gap:.2f}%"


def q_end(year: int, quarter: int) -> str:
    """DART 분기 → 달력 분기말(yfinance period 와 맞추기 위한 키).

    ⚠️ 12월 결산이 아닌 회사는 이 매핑이 틀린다 — 그래서 **맞는 기간이
    없으면 비교를 건너뛴다**(억지로 맞추면 #99 의 오보가 된다)."""
    return {1: f"{year}-03-31", 2: f"{year}-06-30",
            3: f"{year}-09-30", 4: f"{year}-12-31"}.get(quarter, "")


_Q_BACK = {"03-31": ("12-31", -1), "06-30": ("03-31", 0),
           "09-30": ("06-30", 0), "12-31": ("09-30", 0)}


def prev_quarter_end(period: str) -> str | None:
    """분기말 → **직전 분기말**. 형식을 모르면 None(추정하지 않는다)."""
    if len(period) != 10 or period[4] != "-":
        return None
    md = _Q_BACK.get(period[5:])
    if not md:
        return None
    try:
        y = int(period[:4])
    except ValueError:
        return None
    return f"{y + md[1]}-{md[0]}"


def missing_for_window(annual_period: str, have: set) -> list[str]:
    """그 회계연도 4분기 중 **원천이 안 준 분기**. 못 세면 빈 리스트.

    ⚠️ '검산 생략'만 말하면 원인을 사람이 짐작하게 된다(#82 '없음'만 말하는
    진단은 추측을 부른다) — 어느 분기가 비었는지 이름으로 말한다."""
    out, p = [], annual_period
    for _ in range(4):
        if p is None:
            return []
        if p not in have:
            out.append(p)
        p = prev_quarter_end(p)
    return out


def _materials(dart_fin: dict, yf_row: dict) -> str:
    """불일치 기간의 **재료를 나란히** — 어느 구성요소가 다른지 한 줄로.

    ⚠️ "2.75% 차이"만 찍으면 다음에 뭘 볼지 사람이 짐작하게 된다(#93 숫자는
    행동으로 이어질 때만 쓸모가 있다). 무형자산취득이 원인인지, 원천이 FCF
    를 직접 준 것인지, 연도별 정정(restatement)인지가 여기서 갈린다.
    """
    from bot.fcf import _CAPEX_NAMES, _FCF_NAMES, _OCF_NAMES, _first

    def _e(v):
        return "—" if v is None else f"{float(v) / 1e8:,.1f}억"
    d = (f"DART OCF {_e(dart_fin.get('영업활동현금흐름'))} · "
         f"유형 {_e(dart_fin.get('유형자산취득'))} · "
         f"무형 {_e(dart_fin.get('무형자산취득'))}")
    direct = _first(yf_row, _FCF_NAMES)
    y = (f"yfinance OCF {_e(_first(yf_row, _OCF_NAMES))} · "
         f"CAPEX {_e(_first(yf_row, _CAPEX_NAMES))}"
         + (f" · FCF직접 {_e(direct)}" if direct is not None else ""))
    return "↳ " + d + "  ||  " + y


def recompute_dart(fin: dict) -> float | None:
    """DART 재료로 FCF 를 **다시** 계산 — 화면 값과 대조용."""
    from bot.fcf import fcf_from_parts
    parts = [fin.get(k) for k in ("유형자산취득", "무형자산취득")
             if fin.get(k) is not None]
    if not parts:
        return None
    return fcf_from_parts(fin.get("영업활동현금흐름"),
                          sum(abs(float(x)) for x in parts))


def _yf_rows(snap: dict, kind: str) -> list[tuple[str, dict]]:
    cf = ((snap or {}).get("financials") or {}).get("cash_flow") or {}
    rs = [r for r in (cf.get(kind) or []) if isinstance(r, dict)]
    return sorted(((str(r.get("period", ""))[:10], r) for r in rs),
                  key=lambda x: x[0])


def mark_finding(tk: str, line: str) -> str:
    """결함(❌) 줄에 종목을 붙인다. 이미 있거나 결함이 아니면 그대로."""
    if "❌" not in line or tk in line:
        return line
    return f"[{tk}] {line}"


def audit_one(tk: str, dart, years: int = 3) -> dict:
    """한 종목 감사 → {"lines": [...], "bad": n, "unknown": n}."""
    from bot.fcf import cumulative_smell, fcf_from_row
    from bot.market import detect_market
    from bot.scripts.fcf_probe import fiscal_window
    from bot.stock_snapshot import collect_stock_snapshot

    mkt = detect_market(tk.upper()) or "?"
    out: list[str] = []
    bad = unknown = 0

    def say(s):
        # ⚠️ 결함 줄엔 **어느 종목인지** 붙인다 — 안 붙이면 sweep 이 바로 위
        # 줄을 섹션으로 집어 `[③ 두 화면이…] ① 재계산 ❌` 처럼 종목이 없는
        # 보고가 된다(2026-08-31 실측). 감사는 무엇을 보고 말하는지 출처를
        # 같이 찍어야 한다(#114).
        out.append(mark_finding(tk, s))

    def flag(ok: bool | None):
        nonlocal bad, unknown
        if ok is None:
            unknown += 1
        elif not ok:
            bad += 1

    say(f"── {tk}  [{mkt}]")
    snap = collect_stock_snapshot(tk, use_cache=False) or {}
    yq, ya = _yf_rows(snap, "quarterly"), _yf_rows(snap, "annual")

    # ── 재무제표 탭(전 시장 공통) — 화면이 쓰는 그 행에서 그대로 뽑는다
    say("  [재무제표 탭] yfinance 현금흐름표")
    yq_fcf = {p: fcf_from_row(r) for p, r in yq}
    ya_fcf = {p: fcf_from_row(r) for p, r in ya}
    for p, v in list(ya_fcf.items())[-years:]:
        say(f"     연 {p}  " + (f"{v:,.0f}" if v is not None else "— 재료없음"))
    for p, v in list(yq_fcf.items())[-5:]:
        say(f"     분 {p}  " + (f"{v:,.0f}" if v is not None else "— 재료없음"))
    # ① 재계산 — 원천이 직접 준 FCF 가 아니면 OCF−|CAPEX| 와 같아야 한다
    from bot.fcf import _CAPEX_NAMES, _FCF_NAMES, _OCF_NAMES, _first
    from bot.fcf import fcf_from_parts as _parts
    _re_bad, _re_n = [], 0
    for p, r in yq + ya:
        v = fcf_from_row(r)
        if v is None or _first(r, _FCF_NAMES) is not None:
            continue                       # 직접 제공분은 재계산 대상 아님
        _re_n += 1
        if v != _parts(_first(r, _OCF_NAMES), _first(r, _CAPEX_NAMES)):
            _re_bad.append(p)
    # ⚠️ **대조 0건은 통과가 아니다**(#54). 2026-08-22 실측: VM 에서 venv
    # 밖 인터프리터로 돌려 yfinance 가 없자 스냅샷이 통째로 비었는데
    # `① 재계산 ✅ 전 기간 일치` 가 찍혔다 — 감사가 거짓 안심을 준 것.
    if not yq and not ya:
        say("     ① 재계산 ❌ 대조 0건 — 현금흐름표를 못 받았다"
            "(스냅샷 실패·원천 차단·의존성 누락 중 하나)")
        flag(False)
    else:
        say(f"     ① 재계산 " + (f"✅ 전 기간 일치({_re_n}건)" if not _re_bad
                                else f"❌ 불일치 {_re_bad}"))
        flag(not _re_bad)
    # ④ 검산(회계연도 정렬)
    win = fiscal_window(list(yq_fcf.items()), list(ya_fcf.items()))
    if win:
        fy, vals, a = win
        g = _pct(sum(vals), a)
        say(f"     ④ {fy} 분기합 {sum(vals):,.0f} vs 연간 {a:,.0f} "
            + _mark(g, _SUM_OK))
        flag(None if g is None else g <= _SUM_OK)
    else:
        _last_a = next((p for p, v in reversed(list(ya_fcf.items()))
                        if v is not None), "")
        _miss = missing_for_window(_last_a, {p for p, v in yq_fcf.items()
                                             if v is not None})
        say("     ④ ❓ 검산 생략 — "
            + (f"FY말 {_last_a} 창에서 원천이 안 준 분기: "
               f"{', '.join(_miss)}" if _miss
               else "연간 기준일과 맞는 분기 시계열이 없다")
            + " (yfinance 는 분기 현금흐름을 5개 안팎만 준다)")
        flag(None)
    # ⑤ 누적냄새 — ⚠️ 판정에 쓸 값이 없으면 **✅ 가 아니다**(#54).
    # `cumulative_smell` 은 값 3개 미만이면 None(판단보류)을 주는데, 그걸
    # "냄새 없음"으로 찍으면 데이터가 통째로 빈 종목이 통과한다.
    _sm_vals = [v for _p, v in list(yq_fcf.items())[-4:] if v is not None]
    if len(_sm_vals) < 3:
        say(f"     ⑤ ❓ 판정 불가 — 분기 FCF 가 {len(_sm_vals)}개뿐"
            "(3개 이상 있어야 누적 여부를 가른다)")
        flag(None)
    else:
        sm = cumulative_smell(
            [v for _p, v in list(yq_fcf.items())[-4:]],
            next((v for _p, v in reversed(list(ya_fcf.items()))
                  if v is not None), None))
        say("     ⑤ " + (f"❌ 누적 오염 의심: {sm}"
                         if sm else f"✅ 누적 냄새 없음({len(_sm_vals)}분기)"))
        flag(not sm)

    if mkt != "KR" or not dart:
        # 비-KR 은 인포그래픽도 같은 yfinance 현금흐름을 쓴다 — 원천이
        # 하나뿐이라 교차출처 비교 대상이 없다(그 사실을 말한다, #82).
        say("  [분기실적 탭] 비-KR 은 재무제표 탭과 **같은 원천**"
            "(yfinance) — 교차출처 검사 대상 없음"
            if mkt != "KR" else "  ❓ DART 없음 — KR 경로 판정 불가")
        if mkt == "KR":
            flag(None)
        return {"lines": out, "bad": bad, "unknown": unknown}

    # ── 밸류에이션 탭 · 분기실적 탭(KR) — 둘 다 이 시계열 하나를 본다
    from bot.dart_quarterly import get_quarterly_series
    qs = get_quarterly_series(dart, tk, n=5) or []
    say("  [밸류에이션 탭 · 분기실적 탭] DART 현금흐름표")
    say("     ③ 두 화면이 같은 payload(get_quarterly_series)를 본다 ✅"
        if qs else "     ③ ❓ 분기 시계열 없음")
    if not qs:
        flag(None)
        return {"lines": out, "bad": bad, "unknown": unknown}
    _re_bad = []
    for q in qs:
        fin = q.get("financials") or {}
        v, again = fin.get("FCF"), recompute_dart(fin)
        say(f"     {q.get('label', '?'):<7} "
            + (f"{v / 1e8:,.0f}억" if v is not None else "— 재료없음"))
        if v != again:
            _re_bad.append(f"{q.get('label')}: 화면 {v} ≠ 재계산 {again}")
    say("     ① 재계산 " + ("✅ 전 분기 일치" if not _re_bad
                           else f"❌ {_re_bad}"))
    flag(not _re_bad)
    # ② 교차출처 — 같은 기간의 DART 값과 yfinance 값
    say("     ② 교차출처(DART ↔ yfinance)")
    seen = 0
    for q in qs:
        v = (q.get("financials") or {}).get("FCF")
        p = q_end(q.get("year") or 0, q.get("quarter") or 0)
        y = yq_fcf.get(p)
        if v is None or y is None:
            continue
        seen += 1
        g = _pct(v, y)
        say(f"        {q.get('label')} ({p})  DART {v / 1e8:,.1f}억 vs "
            f"yfinance {y / 1e8:,.1f}억  " + _mark(g, _GAP_OK))
        if g is not None and g > _GAP_OK:
            say("           " + _materials(q.get("financials") or {},
                                           dict(yq).get(p) or {}))
        flag(None if g is None else g <= _GAP_OK)
    if not seen:
        # ⚠️ 대조 대상이 0건이면 '이상 없음'이 아니라 판정 실패다(#54).
        say("        ❌ 대조된 기간이 0건 — 기간 키가 안 맞는다"
            "(12월 결산이 아니거나 원천이 그 분기를 안 준다)")
        flag(False)
    # 연간도 같은 방식으로
    import datetime as _dt
    yr = _dt.date.today().year
    for y0 in range(yr - 1, yr - 1 - years, -1):
        fin = ((dart.get_normalized_financials(tk, year=y0) or {})
               .get("financials") or {})
        v = fin.get("FCF")
        if v is None:
            continue
        p = f"{y0}-12-31"
        a = ya_fcf.get(p)
        if a is None:
            continue
        g = _pct(v, a)
        say(f"        FY{y0} ({p})  DART {v / 1e8:,.1f}억 vs "
            f"yfinance {a / 1e8:,.1f}억  " + _mark(g, _GAP_OK))
        if g is not None and g > _GAP_OK:
            say("           " + _materials(fin, dict(ya).get(p) or {}))
        flag(None if g is None else g <= _GAP_OK)
    return {"lines": out, "bad": bad, "unknown": unknown}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FCF 정확도 감사(전 시장)")
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--years", type=int, default=3)
    args = ap.parse_args(argv)

    from bot.dart_client import _FIN_CACHE_VER, get_dart
    from bot.env_keys import env_source
    from bot.market import detect_market
    from bot.scripts.fcf_probe import _universe
    from bot.scripts.probe_progress import fmt_eta, stream_stdout
    stream_stdout()
    _t0 = _time.time()
    print(f"=== FCF 정확도 감사 v{_AUDIT_VER} (재무캐시 v{_FIN_CACHE_VER}) ===")
    print("① 재계산  ② 교차출처(DART↔yfinance)  ③ 표면일치  "
          "④ 분기합↔연간  ⑤ 누적냄새")
    print(f"허용 차이: 교차출처 {_GAP_OK}% · 검산 {_SUM_OK}%")
    print(f"자격증명 DART_API_KEY={env_source('DART_API_KEY') or '없음'}")
    # ⚠️ **어느 파이썬으로 도는지 찍는다.** 2026-08-22 실측: venv 밖에서
    # 돌려 `yfinance` 가 없자 스냅샷이 통째로 비었는데 감사는 ✅ 를 찍었다
    # — 진단 도구가 제품과 **다른 환경**에서 돌면 결과가 거짓이 된다
    # (#23 '.env 를 안 읽는다' 의 인터프리터판).
    import sys as _sys
    print(f"인터프리터 {_sys.executable}")
    _missing = [m for m in ("yfinance",)
                if __import__("importlib.util", fromlist=["util"])
                .find_spec(m) is None]
    if _missing:
        print(f"❌ 필수 모듈 없음: {', '.join(_missing)} — 봇과 **같은**"
              " 인터프리터로 돌릴 것:")
        print("   ~/stock/.venv/bin/python -m bot.scripts.fcf_audit ASML")
        return 2
    print()

    tickers = args.tickers or _universe(args.limit)
    if not tickers:
        print("❌ 대상 없음")
        return 1
    dart = get_dart() if any(detect_market(t.upper()) == "KR"
                             for t in tickers) else None
    print(f"대상 {len(tickers)}종목 · 종목당 스냅샷을 새로 받는다 — "
          f"예상 {len(tickers) * 0.3:.0f}~{len(tickers) * 1.0:.0f}분")
    print("⚠️ `| tail` 로 받으면 끝날 때까지 한 줄도 안 보인다 — "
          "`| tee /tmp/fcf.log`\n")
    tot_bad = tot_unknown = 0
    for _i, tk in enumerate(tickers, 1):
        try:
            r = audit_one(tk, dart, years=args.years)
        except Exception as exc:                               # noqa: BLE001
            print(f"── {tk}  ❌ 감사 실패 {type(exc).__name__}: {exc}\n")
            tot_bad += 1
            continue
        print("\n".join(r["lines"]))
        v = ("✅ 이상 없음" if not r["bad"] and not r["unknown"]
             else (f"❌ 불일치 {r['bad']}건" if r["bad"]
                   else f"❓ 판정불가 {r['unknown']}건"))
        print(f"  판정: {v}"
              + (f" (판정불가 {r['unknown']}건)"
                 if r["bad"] and r["unknown"] else "")
              + f"  {fmt_eta(_i, len(tickers), _t0)}\n")
        tot_bad += r["bad"]
        tot_unknown += r["unknown"]
    print("=" * 60)
    print(f"종합: 불일치 {tot_bad}건 · 판정불가 {tot_unknown}건 "
          f"/ 종목 {len(tickers)}개")
    # ⚠️ 판정불가를 통과로 찍지 않는다(#41). 종료코드로도 갈라 준다.
    return 2 if tot_bad else (3 if tot_unknown else 0)


if __name__ == "__main__":
    sys.exit(main())
