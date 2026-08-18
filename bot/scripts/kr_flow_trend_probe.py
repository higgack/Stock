#!/usr/bin/env python3
"""수급 「다기간 추이」가 비는 원인 판별 프로브 — 읽기 전용·LLM 0·₩0.

사용자 2026-08-18(삼성에스디에스 수급 탭): 외국인 보유율 **18.30%**, 공매도
잔고율 **1.92%** 는 나오는데 5/10/20/30/60일 칸이 **전부 `—`** 다.
현재값이 나온다는 건 조회 자체는 됐다는 뜻이라, 원인이 셋으로 갈린다:

  ① **자격증명 미설정** — KRX 가 2025-12-27 부터 로그인 필수라
     `KRX_ID`/`KRX_PW` 가 없으면 pykrx 경로가 통째로 skip 되고, 현재값만
     Seibro/네이버 폴백으로 채워진다. → .env 에 키를 넣으면 해결.
  ② **원자료 구간 부족** — 로그인은 되는데 KRX 가 돌려준 일별 시계열이
     짧아(신규상장·거래정지·조회제한) N거래일 전 값이 없다. → 못 고친다,
     화면이 `—` 로 두는 게 정답.
  ③ **컬럼 매칭/계산 배선** — 원자료는 충분한데 우리가 쓰는 컬럼명이
     안 맞아 periods 가 빈다. → 코드 문제, 고쳐야 한다.

각 단계를 순서대로 찍어 셋을 가른다. ⚠️ 자격증명은 **설정 여부만** 찍고
값은 절대 출력하지 않는다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.kr_flow_trend_probe 018260.KS
    cd ~/stock && .venv/bin/python -m bot.scripts.kr_flow_trend_probe 018260.KS 005930.KS

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없어 전부 실패한다.
"""
from __future__ import annotations

import sys

_PROBE_VER = 1
_PERIODS = [5, 10, 20, 30, 60]


def _span(dated: list) -> str:
    if not dated:
        return "없음"
    return f"{dated[0][0]} ~ {dated[-1][0]} ({len(dated)}거래일)"


def probe(ticker: str) -> None:
    import os
    from datetime import date, timedelta

    print("=" * 78)
    print(f"■ {ticker}")
    print("=" * 78)

    from bot import pykrx_client as pk

    # ① 자격증명 — **설정 여부만**. 값은 찍지 않는다.
    has_id, has_pw = bool(os.environ.get("KRX_ID")), bool(os.environ.get("KRX_PW"))
    ready = pk.krx_login_ready()
    print(f"  [① 자격증명] KRX_ID={'설정' if has_id else '미설정'} · "
          f"KRX_PW={'설정' if has_pw else '미설정'} → krx_login_ready={ready}")
    if not ready:
        print("    ⚠️ 원인 ① 확정 — pykrx 경로가 통째로 skip 된다.")
        print("       현재값만 Seibro/네이버 폴백으로 채워지고 기간 칸은 못 만든다.")
        print("       .env 에 KRX_ID/KRX_PW 추가 후 재확인(값 확인은"
              " `cat ~/stock/.env | sed 's/=.*$/=***REDACTED***/'`).")

    code = pk._normalize_code(ticker)
    end, start = date.today(), date.today() - timedelta(days=150)
    s_str, e_str = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    # ② 원자료 — 로그인이 될 때만 의미가 있다.
    if ready:
        try:
            from pykrx import stock
        except ImportError:
            print("  [② 원자료] ❌ pykrx 미설치")
            return
        for label, fn, cols in (
            ("외국인 보유율", lambda: stock.get_exhaustion_rates_of_foreign_investment(
                s_str, e_str, code), ("지분율", "한도소진률", "보유비율")),
            ("공매도 잔고율", lambda: stock.get_shorting_balance_by_date(
                s_str, e_str, code), ("비중", "공매도비중", "잔고비중", "공매도잔고비중")),
        ):
            try:
                df = fn()
            except Exception as exc:
                print(f"  [② {label}] ❌ 조회 실패: {type(exc).__name__}: {exc}")
                continue
            if df is None or df.empty:
                print(f"  [② {label}] ⚠️ 빈 응답 — 원인 ②(원자료 없음)")
                continue
            print(f"  [② {label}] {len(df)}행 · 컬럼 {list(df.columns)}")
            # ③ 우리가 쓰는 컬럼이 실제로 있는지 + 기간 계산이 되는지.
            hit = next((c for c in cols if c in df.columns), None)
            if not hit:
                print(f"    ⚠️ 원인 ③ 의심 — 우리가 찾는 컬럼 {cols} 가 없다."
                      " 위 컬럼 목록을 보고 매칭을 고쳐야 한다.")
                continue
            dated = pk._extract_dated_series(df, hit)
            print(f"    컬럼 '{hit}' → {_span(dated)}")
            if len(dated) < 2:
                print("    ⚠️ 원인 ② — 2거래일 미만이라 변화량 자체가 안 나온다.")
                continue
            pds = pk._pp_from_dated_series(dated, _PERIODS)
            got = {p: v for p, v in (pds or {}).items() if v is not None}
            print(f"    기간 계산: {got if got else '전부 None'}")
            if not got:
                need = max(_PERIODS)
                print(f"    ⚠️ 거래일이 {len(dated)}일뿐이면 {need}일 칸은 원래 못 만든다"
                      f"(원인 ②). 그보다 길면 원인 ③(계산 배선).")

    # ④ Seibro 폴백 — 외국인 현재값이 여기서 왔을 수 있다.
    try:
        from bot.seibro_client import seibro_key_ready
        print(f"  [④ Seibro 폴백] 키 {'설정' if seibro_key_ready() else '미설정'}")
    except Exception as exc:
        print(f"  [④ Seibro 폴백] 확인 불가: {type(exc).__name__}")

    # ⑤ 디스크 캐시 — 부분 캐시가 재시도를 막고 있는지.
    cf = pk._CACHE_DIR / f"multi_trend_{code}_{date.today().isoformat()}.json"
    if cf.exists():
        import time
        age = (time.time() - cf.stat().st_mtime) / 3600
        print(f"  [⑤ 캐시] {cf.name} · {age:.1f}시간 전 (TTL {pk._CACHE_TTL_HOURS}h)")
    else:
        print(f"  [⑤ 캐시] 없음 — 이번 호출은 신선 수집이다")

    # ⑥ 화면이 실제로 받는 값.
    mp = pk.get_kr_multi_period_trends(ticker)
    if not mp:
        print("  [⑥ 화면 입력] None — 표 자체가 안 그려진다")
        return
    for key, label in (("foreign", "외국인 보유율"), ("short", "공매도 잔고율")):
        d = mp.get(key) or {}
        pds = {p: v for p, v in (d.get("periods") or {}).items() if v is not None}
        print(f"  [⑥ 화면 입력] {label}: 현재={d.get('current_pct')} · "
              f"기간={pds if pds else '전부 None → 화면에 — 로 찍힌다'}")


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print(f"■ 수급 다기간추이 프로브 v{_PROBE_VER}")
    for t in (argv[1:] or ["018260.KS"]):
        try:
            probe(t.upper())
        except Exception as exc:
            print(f"  ❌ {t} 실패: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
