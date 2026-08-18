#!/usr/bin/env python3
"""재무제표 **신선도** 프로브 — 읽기 전용·LLM 0·₩0.

사용자 2026-08-18(삼양식품 003230.KS): 재무제표 탭이 2025-12 까지만 보여준다.
2026년 1·2분기가 이미 공시됐는데도. 원인 후보가 셋이라 화면만 보고는 못 가른다:

  ① **소스 지연** — yfinance 가 그 종목의 2026 분기를 아직 안 준다.
     → 우리가 고칠 수 없다. 화면에 '표의 최신 분기'를 밝히는 게 최선.
  ② **아카이브에 구움** — 분석 시점 표가 그대로 굳었다(실수기록 #18 형태).
     → 수집시각 기반 재수집으로 해결(7일).
  ③ **캐시** — FULL 오버레이 디스크 캐시가 옛 HTML 을 서빙한다.
     → `_RENDER_VER` 로 무효화.

셋을 나란히 찍어 분리한다. 값을 만들지 않는다 — 있는 것만 보여준다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.fin_freshness_probe 003230.KS
    cd ~/stock && .venv/bin/python -m bot.scripts.fin_freshness_probe 003230.KS 005930.KS

⚠️ 반드시 `.venv/bin/python` — 시스템 python3 은 의존성이 없어 전부 실패한다.
"""
from __future__ import annotations

import sys

_PROBE_VER = 2


def _periods(rows) -> list[str]:
    return sorted({str(r.get("period", ""))[:10] for r in (rows or []) if r.get("period")})


def probe(ticker: str) -> None:
    print("=" * 78)
    print(f"■ {ticker}")
    print("=" * 78)
    import yfinance as yf

    from bot.dashboard import _load_stored_stock_info
    from bot.stock_snapshot import _collect_financials

    # ① 소스가 실제로 주는 것 — 이게 상한이다.
    fresh: dict = {}
    try:
        _collect_financials(yf.Ticker(ticker), fresh)
    except Exception as exc:
        print(f"  ❌ yfinance 수집 실패: {type(exc).__name__}: {exc}")
    src_q = _periods(((fresh.get("financials") or {})
                      .get("income_statement") or {}).get("quarterly"))
    src_a = _periods(((fresh.get("financials") or {})
                      .get("income_statement") or {}).get("annual"))
    print(f"  [소스 yfinance] 분기 {len(src_q)}개: {', '.join(src_q) or '없음'}")
    print(f"                  연간 {len(src_a)}개: {', '.join(src_a) or '없음'}")

    # ② 아카이브에 구워진 것 — 화면이 실제로 그리는 값.
    si = _load_stored_stock_info(ticker) or {}
    st_q = _periods(((si.get("financials") or {})
                     .get("income_statement") or {}).get("quarterly"))
    print(f"  [아카이브] 수집시각 {si.get('financials_asof') or '미기록'} · "
          f"분기 {len(st_q)}개: {', '.join(st_q) or '없음'}")

    # ③ DART(한국) — 소스가 늦어도 공시는 나와 있는지.
    kr_q = [str(q.get("period") or q.get("quarter") or "?")
            for q in ((si.get("kr") or {}).get("financials_q") or [])]
    print(f"  [DART kr.financials_q] {len(kr_q)}개: {', '.join(kr_q[-6:]) or '없음'}")

    # ③-b DART 분기 후보 — **분기실적 탭이 어디서 멈췄는지**.
    # 사용자 2026-08-18 노바렉스: 26.2Q 까지 공시됐는데 화면은 25.4Q 까지였다.
    # `probe_latest_reprt_code` 는 달력상 최신 후보에서 4단계만 역순 탐색하고,
    # CFS 가 전부 비면 OFS 로 1회 폴백한다 — 어느 후보가 비었는지 봐야 안다.
    if ticker.upper().endswith((".KS", ".KQ")):
        try:
            import datetime as _dt

            from bot.dart_client import get_dart
            from bot.dart_quarterly import quarter_label
            dart = get_dart()
            if not dart:
                print("  [DART 후보] ❌ DART_API_KEY 없음")
            else:
                _y = _dt.date.today().year
                cands = [(_y, "11012"), (_y, "11013"), (_y - 1, "11011"),
                         (_y - 1, "11014"), (_y - 1, "11012")]
                print("  [DART 후보] 분기별 응답 유무 (CFS / OFS)")
                for cy, crc in cands:
                    row = []
                    for fs in ("CFS", "OFS"):
                        r = dart.get_normalized_financials(ticker, year=cy,
                                                           fs_div=fs,
                                                           reprt_code=crc)
                        fin = (r or {}).get("financials") or {}
                        rev = fin.get("매출")
                        row.append(f"{fs}={'있음' if fin else '없음'}"
                                   + (f"(매출 {rev/1e8:,.0f}억)" if rev else ""))
                    print(f"    {quarter_label(cy, crc)}  " + " · ".join(row))
                print("    → 최신 분기가 '없음'이면 DART 미제공(원천), "
                      "CFS 만 없고 OFS 만 있으면 연결 미작성 회사다.")
        except Exception as exc:
            print(f"  [DART 후보] ❌ {type(exc).__name__}: {exc}")

    # 판정 — 셋을 비교해야 원인이 하나로 좁혀진다.
    if not src_q:
        print("  → 소스가 분기를 아예 안 준다(원인 ①). 코드로 못 고친다.")
    elif st_q and max(st_q) < max(src_q):
        print(f"  → ⚠️ 아카이브가 뒤처졌다: 저장 {max(st_q)} < 소스 {max(src_q)}"
              " (원인 ② — 수집시각 게이트가 처리해야 한다)")
    elif st_q and max(st_q) == max(src_q):
        print(f"  → 아카이브 = 소스({max(src_q)}). 화면이 더 옛것이면 캐시다"
              "(원인 ③ — `_RENDER_VER`).")
    if kr_q and src_q and str(max(kr_q))[:7] > max(src_q)[:7]:
        print(f"  → 참고: DART 는 {max(kr_q)} 까지 있는데 yfinance 는 "
              f"{max(src_q)} 까지다 — 소스 지연이 실재한다.")


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print(f"■ 신선도 프로브 v{_PROBE_VER}")
    for t in (argv[1:] or ["003230.KS"]):
        try:
            probe(t.upper())
        except Exception as exc:
            print(f"  ❌ {t} 실패: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
