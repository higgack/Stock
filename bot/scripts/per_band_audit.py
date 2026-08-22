"""**PER 밴드 표 감사** — "이게 맞는걸까"에 사람 대신 답한다.

사용자 2026-08-22 (LRCX·KLAC): "이 PER History 가 맞는걸까? 제발 꼼꼼히
꼼꼼히 좀 봐줘. 다른 사이트를 찾아본던 어떻게 해서든...너무 힘들다."

사용자가 다른 사이트를 뒤져 눈으로 대조해야 한다면 그건 화면의 결함이다
(#102c). 이 도구는 **화면이 쓰는 그 경로**(`per_band.for_ticker`)를 그대로
태워(#35) 다음 불변식을 기계가 판정한다:

  ① 산수     — 나란히 놓인 칸끼리 맞는가(주가 ÷ TTM EPS = PER, #33)
  ② 분할     — 인접 기간 EPS 가 설명 안 되는 배수로 튀지 않는가(주가는 분할
               반영인데 EPS 가 as-reported 면 분할 시점에서 갈린다)
  ③ 결산검산 — 결산 시점 TTM 이 연간 EPS 와 같은가(#138 · KLAC 톱니)
  ④ 현재값   — 현재 PER 이 현재가에 비례하는가(이력 마지막 행 기준, #135)
  ⑤ 창       — 밴드가 본 창이 이력 창 안에 있고 요약과 같은가(#34)
  ⑥ 상식성   — 배수 중앙값이 자릿수·통화 사고 범위를 벗어나지 않는가(#139)

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

_AUDIT_VER = 2
_DEFAULT = "LRCX,KLAC,NVMI"
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
    bad = [r for r in rows
           if r.get("eps") and r.get("per")
           and abs(r["price"] / r["eps"] - r["per"]) > max(0.02, r["per"] * 0.01)]
    out.append(("❌" if bad else "✅", "산수",
                f"{len(rows) - len(bad)}/{len(rows)} 행에서 주가÷EPS=PER"
                + (f" · 첫 불일치 {bad[0]}" if bad else "")))

    # ② 분할 정합
    why = pb.eps_break_reason([(r["period"], r.get("eps")) for r in rows])
    out.append(("❌" if why else "✅", "분할", why or "인접 EPS 급변 없음"))

    # ③ 결산검산 — 연간 EPS 가 있어야 판정할 수 있다
    ann = tbl.get("_annual_eps") or []
    if not ann:
        out.append(("❓", "결산검산", "연간 EPS 대조본 없음 — 판정 불가"))
    else:
        m = pb.ttm_annual_mismatch([(r["period"], r.get("eps")) for r in rows],
                                   ann)
        out.append(("❌" if m else "✅", "결산검산",
                    m or f"결산 시점 {len(ann)}개와 일치"))

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

    # ⑥ 상식성
    bad2 = pb.implausible_reason([(r["period"], r["price"], r.get("eps"),
                                   r.get("per")) for r in rows])
    out.append(("❌" if bad2 else "✅", "상식성",
                bad2 or "배수 중앙값이 정상 범위"))
    return out


def audit_one(ticker: str) -> list[str]:
    """화면이 쓰는 그 경로를 그대로 태운다(#35) — 스냅샷도 같이 넘긴다(#145)."""
    import bot.per_band as pb
    out = [f"── {ticker} " + "─" * 40]
    try:
        from bot.stock_snapshot import collect_stock_snapshot
        snap = collect_stock_snapshot(ticker) or {}
    except Exception as exc:                                   # noqa: BLE001
        return out + [f"  ❓ 스냅샷 실패: {type(exc).__name__} {exc}"]
    try:
        tbl, why = pb.for_ticker(ticker, snap)
    except Exception as exc:                                   # noqa: BLE001
        return out + [f"  ❌ for_ticker 예외: {type(exc).__name__} {exc}"]
    if not tbl:
        return out + [f"  ❓ 밴드 없음 — 사유: {why}"]
    out.append(f"  출처: {tbl.get('source')} (basis={tbl.get('basis')}) · "
               f"관측 {tbl.get('n')}개")
    # 결산검산용 연간 EPS — 있는 시장만(미국). 없으면 ❓ 로 찍힌다.
    if (tbl.get("basis") or "").startswith("edgar"):
        try:
            from bot.edgar_eps import eps_history
            h = eps_history(ticker, years=tbl.get("years") or 10) or {}
            # ⚠️ 제품과 **같은 우선순위**로 분할을 받는다 — 별도 호출은 조용히
            # 비어서 감사가 제품과 다른 값을 대조하게 된다(#35).
            _sp = pb._price_history(ticker, tbl.get("years") or 10)[1] \
                or pb.split_factors(ticker)
            tbl["_annual_eps"] = pb.adjust_eps_for_splits(
                h.get("annual") or [], _sp)
        except Exception as exc:                               # noqa: BLE001
            out.append(f"  (연간 대조본 실패: {exc})")
    for mark, axis, msg in audit_rows(tbl):
        out.append(f"  {mark} {axis}: {msg}")
    return out


def main() -> None:
    import time
    from bot.scripts.probe_progress import fmt_eta, stream_stdout
    stream_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=_DEFAULT)
    ap.add_argument("--limit", type=int, default=0,
                    help="앞에서 N종목만(0=전부)")
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
        lines = audit_one(t)
        bad += sum(1 for ln in lines if "❌" in ln)
        for ln in lines:
            print(ln, flush=True)
    print(fmt_eta(len(tickers), len(tickers), t0))
    print(f"\n총 ❌ {bad}건 — ❓ 는 통과가 아니라 **판정 불가**다(#54).")


if __name__ == "__main__":
    main()
