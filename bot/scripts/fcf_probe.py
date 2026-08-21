"""FCF 재료 진단 — 어느 기간에서 무엇이 빠졌나 + **누적 오염** 검사.
읽기 전용·LLM 0·₩0. **전 시장**(KR=DART · 그 외=yfinance).

⚠️ 왜 필요한가(사용자 2026-08-21 SK·농심 실측): 연간 표엔 FCF 가 뜨는데
**분기 표엔 행 자체가 없고**, 연간도 최신연도(FY2025)만 `—` 였다. 분기실적
차트도 5분기 중 1분기만 값이 있었다. 화면은 "없다"까지만 말하고 **왜**는
말하지 않는다 — 셋 중 무엇이 빠졌는지(영업활동현금흐름 / 유형자산취득 /
무형자산취득) 알아야 계정명을 더할지 원천 한계인지 갈린다.

추측 금지의 도구다. 계정을 더하기 **전에** 이걸 돌려 근거를 만든다.

⚠️ 시장마다 원천이 다르다(KR=DART 현금흐름표 · 그 외=yfinance
cash_flow). "KR 에서 고쳤으니 다른 나라도 되겠지" 는 가정이다 —
`bot.fcf.cumulative_smell` 로 **잰다**(2026-08-21 사용자 "다른나라는
정보가 달라서 다시 꼼꼼히 봐줘봐").

사용:
    cd ~/stock && .venv/bin/python -m bot.scripts.fcf_probe 004370.KS AAPL 7203.T
    # 인자 없으면 관심종목에서 몇 개(시장 섞어서)
"""

from __future__ import annotations

import argparse
import sys

_PROBE_VER = 5
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


def _universe(limit: int) -> list[str]:
    """관심종목에서 **시장을 섞어** 고른다 — KR 만 보면 다른 나라 함정을
    영영 못 본다(사용자 2026-08-21 지적)."""
    from bot.market import detect_market
    by_mkt: dict[str, list[str]] = {}
    try:
        from bot.market_favorites import get_favorites
        for f in get_favorites() or []:
            t = (f or {}).get("ticker") if isinstance(f, dict) else f
            if not t:
                continue
            by_mkt.setdefault(detect_market(str(t).upper()) or "?",
                              []).append(str(t))
    except Exception as exc:                                   # noqa: BLE001
        print(f"   (관심종목 로드 실패: {exc})")
    out: list[str] = []
    while len(out) < limit and any(by_mkt.values()):
        for k in list(by_mkt):
            if by_mkt[k] and len(out) < limit:
                out.append(by_mkt[k].pop(0))
    return out


def _yf_periods(ticker: str) -> tuple[list[tuple[str, float | None]],
                                      list[tuple[str, float | None]]]:
    """비-KR: 스냅샷의 현금흐름표 → (분기, 연간) [(라벨, FCF)]."""
    from bot.fcf import fcf_from_row
    from bot.stock_snapshot import collect_stock_snapshot
    snap = collect_stock_snapshot(ticker, use_cache=False) or {}
    cf = ((snap.get("financials") or {}).get("cash_flow") or {})

    def _rows(kind):
        rs = sorted((r for r in (cf.get(kind) or []) if isinstance(r, dict)),
                    key=lambda r: str(r.get("period", "")))
        return [(str(r.get("period", "?"))[:10], fcf_from_row(r)) for r in rs]
    return _rows("quarterly"), _rows("annual")


def fiscal_window(quarters: list, annuals: list) -> tuple | None:
    """연간 하나와 **그 회계연도에 정확히 들어맞는 분기 4개**를 고른다.
    `(회계연도말, [분기값 4개], 연간값)` 또는 None.

    ⚠️ 왜 필요한가(2026-08-21 실측): "최근 4분기 합 vs 최신 연간"으로
    쟀더니 AAPL 38% · 7203.T 781% 로 **정상 데이터가 어긋남으로 찍혔다**.
    두 회사 다 결산월이 12월이 아니라(9월·3월) 최근 4분기가 최신 연간과
    **다른 기간**이었을 뿐이다 — 창을 안 맞추고 낸 판정은 그 자체가 오보다
    (#40 '최신'은 '완결'과 다르다 · #41 여유로 사실을 덮지 말 것).
    실제로 창을 맞추자 7203.T 는 633,559−204,078−488,899+238,990 =
    179,572 로 연간과 **한 자리도 안 틀리게** 같았다.

    창을 못 채우면 **None** — 대충 맞춰 통과시키지 않는다(빈칸 > 틀린 판정).
    """
    qs = [(str(l), v) for l, v in (quarters or [])]
    for lb, a in reversed(annuals or []):
        if a is None:
            continue
        upto = [(l, v) for l, v in qs if l <= str(lb)]
        if len(upto) < 4 or upto[-1][0] != str(lb):
            continue
        w = upto[-4:]
        if any(v is None for _l, v in w):
            continue
        return (str(lb), [v for _l, v in w], a)
    return None


def _yf_report(tk: str, cumulative_smell, seen: list) -> None:
    """yfinance 현금흐름표 경로 한 종목 — 재무재표 차트가 쓰는 그 원천(#35)."""
    print("   ── yfinance 경로(재무재표 차트)")
    qs_v, an_v = _yf_periods(tk)
    if not qs_v and not an_v:
        print("      ❌ 현금흐름표 없음")
        return
    for lb, v in an_v:
        seen.append((lb, v is not None))
        print(f"      연 {lb}  " + ("✅" if v is not None else "❌ 재료 없음")
              + (f"  → FCF {v:,.0f}" if v is not None else ""))
    for lb, v in qs_v:
        print(f"      분 {lb}  " + ("✅" if v is not None else "❌ 재료 없음")
              + (f"  → FCF {v:,.0f}" if v is not None else ""))
    _smell = cumulative_smell([v for _l, v in qs_v][-4:],
                              next((v for _l, v in reversed(an_v)
                                    if v is not None), None))
    if _smell:
        print(f"      ⚠️ 누적 오염 의심: {_smell}")
        return
    win = fiscal_window(qs_v, an_v)
    if not win:
        print("      🔎 회계연도에 맞는 분기 4개가 없어 합계 검산 생략"
              " (원천이 그 분기를 안 줌)")
        return
    fy, vals, a = win
    _sum = sum(vals)
    _gap = abs(_sum - a) / abs(a) * 100 if a else 0.0
    print(f"      🔎 {fy} 분기합 {_sum:,.0f} vs 연간 {a:,.0f}"
          f"  (차이 {_gap:.0f}%)"
          + ("  ✅ 단일분기로 보임" if _gap <= 5
             else "  ⚠️ 어긋남 — 기간 정의 확인 필요"))


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
    from bot.fcf import cumulative_smell
    from bot.market import detect_market
    tickers = args.tickers or _universe(args.limit)
    if not tickers:
        print("❌ 대상 없음")
        return 1
    # DART 는 **KR 이 섞여 있을 때만** 필요하다 — AAPL 하나 보려는데
    # 키가 없다고 통째로 멈추면 다른 나라를 영영 못 본다(이번 질문 그 자체).
    dart = get_dart() if any(detect_market(t.upper()) == "KR"
                             for t in tickers) else None
    if dart is None and any(detect_market(t.upper()) == "KR"
                            for t in tickers):
        print("❌ DART 클라이언트 없음 — KR 종목은 판정 불가")
        return 2

    for tk in tickers:
        _mkt = detect_market(tk.upper()) or "?"
        print(f"── {tk}  [{_mkt}]")
        _seen: list[tuple[str, bool]] = []
        # ── yfinance 경로 — **전 시장 공통**. KR 도 예외가 아니다:
        # 재무재표 차트(`_profit_trend`)는 KR 종목도 yfinance 현금흐름을
        # 쓴다(같은 그림의 매출·영익·순이익이 yfinance 라 기준을 맞춘 것).
        # 그래서 KR 은 **두 경로**가 각각 옳아야 한다(DART 는 아래에서).
        _yf_report(tk, cumulative_smell, _seen)
        if _mkt != "KR":
            print()
            continue
        print("   ── DART 경로(밸류에이션 분기표·분기실적 인포그래픽)")
        # 연간 — `financials_ts` 가 쓰는 그 경로 그대로(#35).
        import datetime as _dt
        _ann: dict[int, float | None] = {}
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
            _ann[y] = v
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
        # KR — 분기합 vs 연간 검산(#33 눈으로 나눗셈). 누적 오염이면 안 맞는다.
        # ⚠️ 연간을 안 넘기면 `cumulative_smell` 의 두 신호 중 하나가 늘
        # 거짓이라 **검사가 통째로 죽는다**(#54 대조 대상 0건).
        _by_y: dict[int, list] = {}
        for q in qs:
            _by_y.setdefault(q.get("year"), []).append(
                (q.get("quarter"), (q.get("financials") or {}).get("FCF")))
        for _y, _qs4 in sorted((y, v) for y, v in _by_y.items() if y):
            _vals = [v for _n, v in sorted(_qs4)]
            _smell = cumulative_smell(_vals, _ann.get(_y))
            if _smell:
                print(f"   ⚠️ FY{_y} 누적 오염 의심: {_smell}")
            elif len(_qs4) == 4 and all(v is not None for _n, v in _qs4) \
                    and _ann.get(_y):
                _sum, _a = sum(_vals), _ann[_y]
                _gap = abs(_sum - _a) / abs(_a) * 100
                print(f"   🔎 FY{_y} 분기합 {_sum / 1e8:,.0f}억 vs 연간 "
                      f"{_a / 1e8:,.0f}억 (차이 {_gap:.0f}%)"
                      + ("  ✅ 단일분기로 보임" if _gap <= 15
                         else "  ⚠️ 어긋남 — 기간 정의 확인 필요"))
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
