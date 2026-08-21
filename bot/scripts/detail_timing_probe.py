#!/usr/bin/env python3
"""종목 상세 로딩 **단계별 소요시간** 실측 — 읽기 전용·LLM 0·₩0.

목적(사용자 2026-08-21 "지금은 너무 로딩에 오래걸려서 그래"): 어느 블록을
클릭 로딩으로 뗄지 정하려면 **추측이 아니라 숫자**가 있어야 한다. 지금
클릭 로딩인 건 밴드차트·분기실적·기술분석 3개뿐이고, 나머지는 전부 서버가
`collect_stock_snapshot` 한 번으로 만들어 보낸다.

⚠️ 프로브가 수집을 **재구현하면** 제품과 다른 걸 재게 된다(실수 #35).
그래서 제품 코드에 계측을 심고(`stock_snapshot._TIMING`) 여기서는 읽기만
한다 — 병렬 풀 구조·타임아웃·폴백이 그대로 반영된다.

읽는 값:
  yf.info      — **직렬**. 이게 느리면 뒤를 아무리 떼도 첫 화면이 안 빨라진다
  실적이력/투자의견/기관보유/뉴스/재무제표/동종비교 — 6종 **병렬**(풀 6)
                 → 벽시계는 이 중 **최대값** 하나가 지배한다
  enrich:KR 등 — 병렬 뒤 **직렬**. 여기가 크면 클릭 로딩 1순위
  total        — 스냅샷 전체

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.detail_timing_probe
    cd ~/stock && .venv/bin/python -m bot.scripts.detail_timing_probe 005930.KS
    # --limit N   관심종목 상한(기본 8 — 매 종목이 실제 수집이라 느리다)

⚠️ 반드시 `.venv/bin/python`. 캐시를 우회하므로 **실제 콜드 비용**이다.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time

_PROBE_VER = 4


def _universe(limit: int) -> list[str]:
    """관심종목 = 우리가 실제로 여는 종목. 하드코딩 표본이 아니라 화면의
    모집단이라야 통계가 의미 있다."""
    out: list[str] = []
    try:
        from bot.market_favorites import get_favorites
        for f in get_favorites() or []:
            t = (f or {}).get("ticker") if isinstance(f, dict) else f
            if t and t not in out:
                out.append(t)
    except Exception as exc:                                   # noqa: BLE001
        print(f"   (관심종목 로드 실패: {exc})")
    return out[:limit]


# 병렬 풀에 **들어가는 것만** 남긴다. 계측 키는 세 부류다:
#   yf.info/total  — 단계가 아니라 합계·직렬 선행
#   enrich:*       — 풀 **뒤**에 도는 시장별 보강
#   kr:*           — enrich:KR **안**의 개별 task(그 자체가 병렬)
# 이걸 안 걸러내면 `병렬최대` 가 enrich 내부 값에 밀려 **엉뚱한 항목**을
# 병목으로 보고한다. 순수 함수로 빼 둔 건 의도다 — 인라인이면 회귀가
# 소스 문자열만 보게 된다(실수 #41).
_NOT_PARALLEL = ("enrich:", "kr:")


def slowest_parallel(timing: dict) -> tuple[str, float]:
    """보조 **병렬 6종** 중 가장 느린 (이름, 초). 없으면 ('—', 0.0)."""
    par = {k: v for k, v in (timing or {}).items()
           if k not in ("yf.info", "total")
           and not k.startswith(_NOT_PARALLEL)}
    return max(par.items(), key=lambda kv: kv[1], default=("—", 0.0))


def _print_key_sources() -> None:
    """자격증명 **출처**를 먼저 찍는다 — 값은 절대 안 찍는다(실수 #23).

    ⚠️ 키가 안 잡히면 그 경로가 통째로 skip 돼 **0.4초로 빨라 보인다**.
    2026-08-21 실측: `enrich KR 0.4s` 가 나왔는데 같은 실행 로그에
    `fsc: DATA_GO_KR_API_KEY 미설정` 이 있었다 — 차단기 효과가 아니라
    아예 안 부른 것이라, 출처를 안 찍으면 개선을 **오보**한다."""
    from bot.env_keys import env_source, env_why
    for n in ("DATA_GO_KR_API_KEY", "DART_API_KEY", "FRED_API_KEY"):
        src = env_source(n) or "없음"
        # 없으면 **이유**까지 — '없음'만 찍으면 그다음을 사람이 추측한다.
        # 2026-08-21 실측: .env 에 키가 있는데 미설정으로 보고됐다.
        why = "" if src != "없음" else f"  ← {env_why(n)}"
        print(f"자격증명 {n}={src}{why}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="상세 로딩 단계별 소요시간")
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args(argv)

    from bot import stock_snapshot as ss

    print(f"=== 종목 상세 단계별 소요시간 v{_PROBE_VER} ===")
    print("병렬 6종은 벽시계에서 **최대값 하나만** 비용이다(풀 6).")
    _print_key_sources()
    tickers = args.tickers or _universe(args.limit)
    if not tickers:
        print("❌ 대상 없음 — 티커를 인자로 넘겨라")
        return 1
    print(f"대상 {len(tickers)}종목 (캐시 우회 — 실제 콜드 비용)\n")

    per_stage: dict[str, list[float]] = {}
    for i, tk in enumerate(tickers, 1):
        t0 = time.time()
        snap = ss.collect_stock_snapshot(tk, use_cache=False)
        wall = time.time() - t0
        tm = ss.last_timing()
        if not tm:
            print(f"[{i:2}/{len(tickers)}] {tk:<12} 계측 없음(수집 실패?)")
            continue
        for k, v in tm.items():
            per_stage.setdefault(k, []).append(v)
        slow = slowest_parallel(tm)
        enr = next((f"{k.split(':')[1]} {v:.1f}s"
                    for k, v in tm.items() if k.startswith("enrich:")), "—")
        print(f"[{i:2}/{len(tickers)}] {tk:<12} 총 {tm.get('total', wall):6.1f}s"
              f" · info {tm.get('yf.info', 0):5.1f}s"
              f" · 병렬최대 {slow[0]} {slow[1]:.1f}s"
              f" · enrich {enr}"
              f" · {'ok' if snap else '수집실패'}")

    if not per_stage:
        print("\n❌ 계측이 하나도 안 잡혔다 — 프로브가 눈이 멀었다")
        return 1
    print("\n" + "=" * 68)
    print("단계별 (중앙값 / 최대) — 클릭 로딩 후보는 큰 것부터")
    rows = sorted(per_stage.items(),
                  key=lambda kv: -statistics.median(kv[1]))
    for k, vs in rows:
        print(f"  {k:<14} {statistics.median(vs):6.2f}s / {max(vs):6.2f}s"
              f"   (n={len(vs)})")
    # ⚠️ enrich:KR 은 **이미 병렬**이다(2026-06-10) — 합이 아니라 **최대값
    # 하나**가 지배한다. 그래서 그 안을 따로 낸다. 이름이 없으면 "느리다"
    # 까지만 알고 무엇을 고칠지는 모른다(실수 #69).
    _kr = {k[3:]: v for k, v in per_stage.items() if k.startswith("kr:")}
    if _kr:
        print("\nenrich:KR 내부 (중앙값 / 최대) — 이 중 **최대값**이 벽시계다:")
        for k in sorted(_kr, key=lambda k: -statistics.median(_kr[k])):
            v = _kr[k]
            print(f"  {k:<18} {statistics.median(v):6.2f}s / {max(v):6.2f}s"
                  f"   (n={len(v)})")
    print("\n해석: `total` 에서 `yf.info` + `병렬최대` + `enrich:*` 를 빼면")
    print("      나머지는 순수 파싱이다. 클릭 로딩으로 떼서 이득이 큰 건")
    print("      **enrich 와 병렬최대를 차지하는 항목**이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
