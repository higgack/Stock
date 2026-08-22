"""**PER 밴드 표 감사** — "이게 맞는걸까"에 사람 대신 답한다.

사용자 2026-08-22 (LRCX·KLAC): "이 PER History 가 맞는걸까? 제발 꼼꼼히
꼼꼼히 좀 봐줘. 다른 사이트를 찾아본던 어떻게 해서든...너무 힘들다."

사용자가 다른 사이트를 뒤져 눈으로 대조해야 한다면 그건 화면의 결함이다
(#102c). 이 도구는 **화면이 쓰는 그 경로**(`per_band.for_ticker`)를 그대로
태워(#35) 다음 불변식을 기계가 판정한다:

  ① 산수     — 나란히 놓인 칸끼리 맞는가(주가 ÷ TTM EPS = PER, #33)
  ② 분할     — 인접 기간 EPS 가 설명 안 되는 배수로 튀지 않는가(주가는 분할
               반영인데 EPS 가 as-reported 면 분할 시점에서 갈린다)
  ③ 결산검산 — **제품이 낸** 판정(어긋났으면 분기 경로를 폐기했는가,
               #138 · KLAC 톱니). 감사가 따로 대조본을 만들면 제품과
               다른 기준선을 비교한다(#35).
  ④ 현재값   — 현재 PER 이 현재가에 비례하는가(이력 마지막 행 기준, #135)
  ⑤ 창       — 밴드가 본 창이 이력 창 안에 있고 요약과 같은가(#34)
  ⑥ 분모 기준 — 원천이 쓴 EPS 가 계단형(분기 확정)인가 연속형(전망·보간)인가
               — 라벨을 추측으로 붙이면 화면이 거짓말한다(2026-08-22 실측)
  ⑦ 상식성   — 배수 중앙값이 자릿수·통화 사고 범위를 벗어나지 않는가(#139)
  ⑧ 최신성   — 마지막 관측이 낡지 않았는가(낡으면 현재 PER 이 옛 EPS 로
               만들어져 밴드가 '지금'을 못 담는다, #146)

⚠️ 대조 대상이 0건이면 ✅ 가 아니라 **❓(판정 불가)** 다(#54). ❌ 만 세어
'이상 없음'이라고 말하지 않는다.

사용법(VM):
    cd ~/stock && .venv/bin/python -m bot.scripts.per_band_audit \\
        --tickers LRCX,KLAC,NVMI | tee /tmp/pba.txt
⚠️ `| tail` 은 프로세스가 끝나야 출력한다 — `tee` 를 쓸 것(#103).
"""

from __future__ import annotations

import argparse
import sys

_AUDIT_VER = 9
# ⚠️ **전 시장을 기본으로 돈다**(사용자 2026-08-22 "내가 일일히 점검 안 해도
# 좀 모든 나라 밴드 제대로 되고 있는지를 잘 좀 검토해줘. 제발"). 시장마다
# 원천이 달라(EDGAR·EDINET·FinMind·바이두·FnGuide·yfinance) 한 시장이 고쳐져도
# 다른 시장은 그대로일 수 있다 — 나란히 놓고 봐야 갈라진 걸 잡는다(#31).
_MARKET_SAMPLES: tuple[tuple[str, str], ...] = (
    ("US", "AAPL"), ("US", "LRCX"), ("US", "KLAC"), ("US", "NVMI"),
    ("KR", "005930.KS"), ("KR", "000660.KS"),
    ("JP", "7203.T"), ("JP", "6758.T"), ("JP", "9984.T"), ("JP", "6501.T"),
    ("TW", "2330.TW"), ("TW", "2327.TW"),
    ("CN_A", "600519.SS"), ("CN_A", "002371.SZ"),
    ("HK", "0700.HK"), ("HK", "0002.HK"),
    ("EU", "SIE.DE"),
)
_DEFAULT = ",".join(t for _m, t in _MARKET_SAMPLES)
_EST_S_PER_TICKER = 25


def _banner() -> None:
    """인터프리터·의존성 먼저 — venv 밖에서 돌면 판정이 통째로 거짓이다(#132)."""
    import bot.per_band as pb
    print(f"per_band_audit v{_AUDIT_VER}")
    print(f"  인터프리터: {sys.executable}")
    for mod in ("yfinance", "requests"):
        try:
            __import__(mod)
            print(f"  {mod}: OK")
        except Exception as exc:                               # noqa: BLE001
            print(f"  {mod}: 없음 ({exc}) — 이 실행의 판정은 무효")
            raise SystemExit(2)
    print(f"  per_band: {pb.__file__}")


# 원천이 PER 을 직접 주는 경로 — 분할 기준 불일치가 성립하지 않는다.
_DIRECT_PER_BASES = frozenset({"finmind", "baidu", "fnguide"})
# 이력이 이보다 낡으면 현재 PER 이 옛 EPS 로 만들어진다 — 연간 계열도 결산
# 직후엔 1년 가까이 낡으므로 그보다 넉넉히 잡는다.
_STALE_DAYS = 500


def audit_rows(tbl: dict) -> list[tuple[str, str, str]]:
    """[(마크, 축, 설명)] — 순수 함수라 픽스처로 동작을 고정할 수 있다(#41).

    마크: ✅ 통과 · ❌ 불일치 · ❓ 대조 0건(판정 불가 — 통과가 아니다).
    """
    import bot.per_band as pb
    out: list[tuple[str, str, str]] = []
    rows = [r for r in (tbl.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        return [("❓", "전체", "이력 행이 0개 — 판정 불가")]

    # ① 산수 — 화면에 나란히 놓인 세 칸이 서로 맞아야 한다(#33)
    # ⚠️ **검산한 행 수를 세어야 한다.** 국내(FnGuide)는 분모를 안 줘서 `eps`
    # 가 전 행 None 인데, 옛 판은 "49/49 행에서 주가÷EPS=PER ✅" 라고 찍었다
    # — **한 행도 안 재고 통과**시킨 것이다(#54 대조 0건은 ✅ 가 아니다).
    # 감사가 국내 경로를 타게 만든 바로 그 커밋에서 이 거짓 초록이 생겼다.
    kind = tbl.get("kind") or "PER"
    denom = "BPS" if kind == "PBR" else "EPS"
    checkable = [r for r in rows if r.get("eps") and r.get("per") and r.get("price")]
    bad = [r for r in checkable
           if abs(r["price"] / r["eps"] - r["per"]) > max(0.02, r["per"] * 0.01)]
    if not checkable:
        out.append(("❓", "산수",
                    f"검산 가능한 행이 0개 — 원천이 분모({denom})를 주지 않는다"
                    f"(배수는 원천이 직접 준 값)"))
    else:
        out.append(("❌" if bad else "✅", "산수",
                    f"{len(checkable) - len(bad)}/{len(checkable)} 행에서 "
                    f"주가÷{denom}={kind}"
                    + (f" · 첫 불일치 {bad[0]}" if bad else "")))

    # ② 분할 정합
    # ⚠️ 원천이 **PER 을 직접 주는** 경로(FinMind·바이두·FnGuide)에서는 EPS 가
    # 우리가 나눠 만든 값이라 급변이 있어도 그건 **실적**이지 기준 불일치가
    # 아니다 — 2026-08-22 실측에서 0002.HK 가 그 이유로 ❌ 였다(실적 적자기).
    if (tbl.get("basis") or "") in _DIRECT_PER_BASES:
        out.append(("✅", "분할",
                    "해당 없음 — 원천이 PER 을 직접 준다(우리가 EPS 로 "
                    "나누지 않으므로 분할 기준이 갈릴 수 없다)"))
    elif not checkable:
        # 잴 게 없으면 ✅ 가 아니다 — ① 과 같은 함정(#54).
        out.append(("❓", "분할", f"{denom} 가 한 행도 없어 판정 불가"))
    else:
        why = pb.eps_break_reason([(r["period"], r.get("eps")) for r in rows])
        out.append(("❌" if why else "✅", "분할",
                    why or f"인접 {denom} 급변 없음"))

    # ③ 결산검산 — **제품의 판정을 그대로 읽는다**.
    # ⚠️ 감사가 연간 대조본을 따로 만들어 대조했더니 2026-08-22 KLAC 이
    # 감사에서만 ❌ 였다 — 제품은 분할 환산을 되돌렸는데(효과가 나빠져서)
    # 감사본은 환산된 채라 **다른 기준선**을 비교한 것이다(#35 감사는 화면이
    # 쓰는 그 경로를 태울 것). 이제 제품이 `fiscal_check` 로 판정을 싣는다.
    fc = tbl.get("fiscal_check")
    if not fc:
        out.append(("❓", "결산검산", "제품이 판정하지 않는 경로 — 판정 불가"))
    else:
        verdict, why = fc[0], fc[1]
        if verdict == "ok":
            out.append(("✅", "결산검산", why))
        elif verdict in ("skipped", "none"):
            out.append(("❓", "결산검산", why + " — 판정 불가"))
        elif (tbl.get("basis") or "") == "edgar":
            # 어긋났다고 판정해 놓고 그 분기 경로를 화면에 실었다면 배선 결함이다.
            out.append(("❌", "결산검산", f"{why} — 그런데 분기 경로를 채택했다"))
        else:
            out.append(("✅", "결산검산",
                        f"어긋나 분기 경로를 폐기하고 {tbl.get('basis')} 로 "
                        f"내려갔습니다 — {why}"))

    # ⑧ 최신성 — 마지막 관측이 낡으면 **현재 PER 이 옛 EPS 로** 만들어진다.
    # ⚠️ 2026-08-23 KLAC: 이력이 2024-03-31 에서 끊긴 채 현재 PER 96.08x 가
    # 밴드 최고 36.48x 를 훌쩍 넘었다 — 산수·창·상식성이 전부 ✅ 라 감사가
    # 그냥 통과시켰다(#146 밴드가 '지금'을 못 담으면 존재 이유가 없다).
    import datetime as _dt
    _today = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).date()
    try:
        _last = _dt.date.fromisoformat(str(rows[-1]["period"])[:10])
        _age = (_today - _last).days
    except Exception:                                          # noqa: BLE001
        _age = None
    if _age is None:
        out.append(("❓", "최신성", "마지막 관측일을 못 읽음 — 판정 불가"))
    else:
        out.append(("❌" if _age > _STALE_DAYS else "✅", "최신성",
                    f"마지막 관측 {rows[-1]['period']} · {_age}일 전"
                    + (" — 현재 PER 이 옛 EPS 로 만들어진다"
                       if _age > _STALE_DAYS else "")))

    # ④ 현재값 — 현재 PER 은 현재가에 비례한다(마지막 행이 기준, #135)
    sm, px_now = tbl.get("summary") or {}, tbl.get("price_now")
    last = rows[-1]
    if not sm.get("per_now") or not px_now or not last.get("per"):
        out.append(("❓", "현재값", "현재 PER 또는 실시간 시세 없음 — 판정 불가"))
    else:
        k = last["per"] / last["price"]
        exp = px_now * k
        ok = abs(exp - sm["per_now"]) <= max(0.05, exp * 0.02)
        out.append(("✅" if ok else "❌", "현재값",
                    f"현재가 {px_now:,.2f} × k({k:.6f}) = {exp:,.2f} vs "
                    f"화면 {sm['per_now']:,.2f}"))

    # ⑤ 창 — 밴드 창이 이력 창 안에 있고 요약과 같아야 한다(#34)
    bf, bt = tbl.get("band_from"), tbl.get("band_to")
    if not bf or not bt:
        out.append(("❓", "창", "밴드 창 라벨 없음 — 판정 불가"))
    else:
        inside = rows[0]["period"] <= bf <= bt <= rows[-1]["period"]
        out.append(("✅" if inside else "❌", "창",
                    f"밴드 {bf}~{bt} · 이력 {rows[0]['period']}~"
                    f"{rows[-1]['period']}"))

    # ⑥ 분모 기준 — 원천이 무엇을 쓰는지 **재서** 말한다(추측 금지).
    # PBR 표의 분모는 BPS 다 — 지표 이름을 섞으면 화면이 거짓말한다(#34).
    cad, why = pb.eps_cadence(rows, denom)
    out.append(("❓" if cad is None else "✅", "분모 기준",
                (f"{cad} — {why}" if cad else why)))

    # ⑦ 상식성
    bad2 = pb.implausible_reason([(r["period"], r["price"], r.get("eps"),
                                   r.get("per")) for r in rows])
    out.append(("❌" if bad2 else "✅", "상식성",
                bad2 or "배수 중앙값이 정상 범위"))
    return out


def dump_edgar(ticker: str, limit: int = 40) -> list[str]:
    """EDGAR 가 준 분기·연간 EPS 를 **원문 그대로** 찍는다.

    ⚠️ KLAC 은 연간 EPS 가 2026-06-30 에 3.66, 그 전 해가 그 12배로 오고
    분기 TTM 이 -19.98 이었다(2026-08-22 실측). 이건 분할도 통화도 아니고
    원천 데이터 자체의 문제로 보이는데, **원문을 안 보면 계속 추측만 하게
    된다**(#109 '커버리지 진단은 처음부터 표본 원문을 같이 찍을 것').
    """
    try:
        from bot.edgar_eps import eps_history
        h = eps_history(ticker, years=10) or {}
    except Exception as exc:                                   # noqa: BLE001
        return [f"  · EDGAR 덤프 실패: {type(exc).__name__} {exc}"]
    if not h:
        return ["  · EDGAR 커버리지 없음"]
    out = [f"  · EDGAR 태그 {h.get('tag')}"]
    for k in ("quarterly", "annual"):
        rows = h.get(k) or []
        out.append(f"    {k}: {len(rows)}개")
        for p, v in rows[-limit:]:
            out.append(f"      {p}  {v}")
    return out


def audit_one(ticker: str, dump: bool = False) -> list[str]:
    """화면이 쓰는 그 경로를 그대로 태운다(#35) — 스냅샷도 같이 넘긴다(#145)."""
    import bot.per_band as pb
    out = [f"── {ticker} " + "─" * 40]
    try:
        from bot.stock_snapshot import collect_stock_snapshot
        snap = collect_stock_snapshot(ticker) or {}
    except Exception as exc:                                   # noqa: BLE001
        return out + [f"  ❓ 스냅샷 실패: {type(exc).__name__} {exc}"]
    # ⚠️ **화면이 쓰는 그 경로**를 태운다(#35). 예전엔 `pb.for_ticker` 만 불러
    # 국내 종목을 `yf-a 관측 4개` 로 판정했는데, 화면은 FnGuide 밴드(관측
    # 49개)를 그린다 — 감사가 화면이 안 쓰는 경로를 재고 있었다.
    try:
        from bot.band_source import resolve as _resolve
        got = _resolve(ticker, snap)
    except Exception as exc:                                   # noqa: BLE001
        return out + [f"  ❌ 밴드 해석 예외: {type(exc).__name__} {exc}"]
    tbl, why = got.get("per"), got.get("why")
    if not tbl:
        out.append(f"  ❓ 밴드 없음 — 사유: {why}")
        return out + (dump_edgar(ticker) if dump else [])
    out.append(f"  출처: {tbl.get('source')} (basis={tbl.get('basis')}) · "
               f"관측 {tbl.get('n')}개")
    if tbl.get("trim_note"):
        out.append(f"  ⚠️ 잘라냄: {tbl['trim_note']}")
    for mark, axis, msg in audit_rows(tbl):
        out.append(f"  {mark} {axis}: {msg}")
    # 국내는 PBR 표도 같이 나온다 — 한쪽만 재면 나머지가 조용히 낡는다(#38).
    pbr = got.get("pbr")
    if pbr:
        out.append(f"  [PBR] 관측 {pbr.get('n')}개")
        for mark, axis, msg in audit_rows(pbr):
            out.append(f"  {mark} PBR:{axis}: {msg}")
    if dump:
        out.extend(dump_edgar(ticker))
    return out


def main() -> None:
    import time
    from bot.scripts.probe_progress import fmt_eta, stream_stdout
    stream_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=_DEFAULT)
    ap.add_argument("--limit", type=int, default=0,
                    help="앞에서 N종목만(0=전부)")
    ap.add_argument("--dump-edgar", action="store_true",
                    help="EDGAR 원본 행(기간·값·폼)을 그대로 찍는다 — 값이 "
                         "이상할 때 원인을 원문으로 가른다(#109)")
    args = ap.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if args.limit:
        tickers = tickers[:args.limit]
    _banner()
    print(f"  대상 {len(tickers)}종목 · 예상 "
          f"{len(tickers) * _EST_S_PER_TICKER / 60:.1f}분")
    t0 = time.time()
    bad = 0
    for i, t in enumerate(tickers):
        print(fmt_eta(i, len(tickers), t0), flush=True)
        lines = audit_one(t, dump=args.dump_edgar)
        bad += sum(1 for ln in lines if "❌" in ln)
        for ln in lines:
            print(ln, flush=True)
    print(fmt_eta(len(tickers), len(tickers), t0))
    print(f"\n총 ❌ {bad}건 — ❓ 는 통과가 아니라 **판정 불가**다(#54).")


if __name__ == "__main__":
    main()
