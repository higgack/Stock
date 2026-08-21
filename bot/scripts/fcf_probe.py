"""FCF 재료 진단 — 어느 기간에서 무엇이 빠졌나. 읽기 전용·LLM 0·₩0.

⚠️ 왜 필요한가(사용자 2026-08-21 SK·농심 실측): 연간 표엔 FCF 가 뜨는데
**분기 표엔 행 자체가 없고**, 연간도 최신연도(FY2025)만 `—` 였다. 분기실적
차트도 5분기 중 1분기만 값이 있었다. 화면은 "없다"까지만 말하고 **왜**는
말하지 않는다 — 셋 중 무엇이 빠졌는지(영업활동현금흐름 / 유형자산취득 /
무형자산취득) 알아야 계정명을 더할지 원천 한계인지 갈린다.

추측 금지의 도구다. 계정을 더하기 **전에** 이걸 돌려 근거를 만든다.

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.fcf_probe 004370.KS 034730.KS
    # 인자 없으면 관심종목에서 KR 종목 몇 개
"""

from __future__ import annotations

import argparse
import sys

_PROBE_VER = 2
_PARTS = ("영업활동현금흐름", "유형자산취득", "무형자산취득")


def _mark(fin: dict) -> str:
    """이 기간의 재료 상태 한 줄 — 무엇이 있고 무엇이 없나."""
    got = [k for k in _PARTS if (fin or {}).get(k) is not None]
    if not got:
        return "❌ 셋 다 없음"
    miss = [k for k in _PARTS if k not in got]
    if "영업활동현금흐름" not in got:
        return f"❌ 영업CF 없음 (있는 것: {'·'.join(got)})"
    if len(got) == 1:
        return "❌ CAPEX 없음 (영업CF만)"
    return f"✅ {'·'.join(got)}" + (f"  (없음: {'·'.join(miss)})" if miss else "")


def _kr_universe(limit: int) -> list[str]:
    out: list[str] = []
    try:
        from bot.market_favorites import get_favorites
        for f in get_favorites() or []:
            t = (f or {}).get("ticker") if isinstance(f, dict) else f
            if t and str(t).upper().endswith((".KS", ".KQ")) and t not in out:
                out.append(t)
    except Exception as exc:                                   # noqa: BLE001
        print(f"   (관심종목 로드 실패: {exc})")
    return out[:limit]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FCF 재료 진단(KR/DART)")
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--years", type=int, default=3)
    args = ap.parse_args(argv)

    from bot.dart_client import _FIN_CACHE_VER
    from bot.env_keys import env_source
    from bot.fcf import fcf_from_parts
    print(f"=== FCF 재료 진단 v{_PROBE_VER} (재무캐시 v{_FIN_CACHE_VER}) ===")
    print(f"판정 대상: {' / '.join(_PARTS)}  → FCF = 영업CF − |유형+무형|")
    print(f"자격증명 DART_API_KEY={env_source('DART_API_KEY') or '없음'}")
    # ⚠️ 실측 2026-08-21: 최근 기간만 재료가 없고 옛 기간은 멀쩡했다 —
    # 원인은 파서가 아니라 **7일 TTL 디스크 캐시**였다(키를 안 올려 CF 없는
    # 옛 결과를 서빙). 같은 모양이 또 보이면 캐시부터 의심하도록 배너에
    # 버전을 찍고, 아래에서 그 패턴을 직접 지목한다(실수 #21b).
    print("⚠️ '최근만 ❌, 옛 기간 ✅' 이면 파서가 아니라 캐시를 의심할 것"
          f" — 재무캐시 키는 qfin{_FIN_CACHE_VER}, TTL 7일\n")

    from bot.dart_client import get_dart
    from bot.dart_quarterly import get_quarterly_series
    dart = get_dart()
    if not dart:
        print("❌ DART 클라이언트 없음 — 판정 불가")
        return 2
    tickers = args.tickers or _kr_universe(args.limit)
    if not tickers:
        print("❌ 대상 없음")
        return 1

    for tk in tickers:
        print(f"── {tk}")
        _seen: list[tuple[str, bool]] = []
        # 연간 — `financials_ts` 가 쓰는 그 경로 그대로(#35).
        import datetime as _dt
        yr = _dt.date.today().year
        for y in range(yr - 1, yr - 1 - args.years, -1):
            try:
                fin = dart.get_normalized_financials(tk, year=y)
            except Exception as exc:                           # noqa: BLE001
                print(f"   FY{y}  ❌ 조회실패 {type(exc).__name__}: {exc}")
                continue
            f = (fin or {}).get("financials") or {}
            if not f:
                print(f"   FY{y}  ❌ 재무 없음")
                continue
            v = f.get("FCF")
            _seen.append((f"FY{y}", v is not None))
            print(f"   FY{y}  {_mark(f)}"
                  + (f"  → FCF {v / 1e8:,.0f}억" if v is not None else ""))
        # 분기 — 화면(분기표·차트)이 쓰는 그 경로 그대로.
        try:
            qs = get_quarterly_series(dart, tk, n=5) or []
        except Exception as exc:                               # noqa: BLE001
            print(f"   분기 ❌ 조회실패 {type(exc).__name__}: {exc}")
            continue
        if not qs:
            print("   분기 ❌ 시계열 없음")
        for q in qs:
            f = q.get("financials") or {}
            v = f.get("FCF")
            _seen.append((str(q.get("label", "?")), v is not None))
            print(f"   {q.get('label', '?'):<7} {_mark(f)}"
                  + (f"  → FCF {v / 1e8:,.0f}억" if v is not None else ""))
        # 재료가 있는데 FCF 가 없으면 그건 산식 배선 문제다.
        for q in qs:
            f = q.get("financials") or {}
            if f.get("FCF") is None and f.get("영업활동현금흐름") is not None \
                    and any(f.get(k) is not None for k in _PARTS[1:]):
                print(f"   ⚠️ {q.get('label')}: 재료는 있는데 FCF 가 없다 "
                      f"— 배선 확인 필요 "
                      f"(계산해 보면 {fcf_from_parts(f['영업활동현금흐름'], sum(abs(f[k]) for k in _PARTS[1:] if f.get(k) is not None))})")
        # 캐시 특유의 패턴을 **기계가 지목**한다 — 사람이 매번 알아보길
        # 기대하면 안 된다(이번에 실제로 못 알아볼 뻔했다).
        _ok = [lb for lb, has in _seen if has]
        _no = [lb for lb, has in _seen if not has]
        if _ok and _no and all(lb in _no for lb in _seen_recent(_seen)):
            print(f"   🔎 최근 기간만 비었다({', '.join(_no)}) — 파서가 아니라"
                  f" **캐시**일 가능성이 높다. `_FIN_CACHE_VER` 를 올렸는지"
                  f" 확인하고, 급하면 ~/.tradingagents/cache 의 qfin* 삭제.")
        print()
    return 0


def _seen_recent(seen: list) -> list:
    """관측 목록에서 **최근 절반**의 라벨 — 캐시 패턴 판정용."""
    return [lb for lb, _h in seen[len(seen) // 2:]]


if __name__ == "__main__":
    sys.exit(main())
