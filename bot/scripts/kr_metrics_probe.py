"""국내 종목 주당지표가 **어느 원천에서 오는지** 나란히 찍는다.

사용자 2026-08-23 서희건설(035890.KQ): 네이버(FnGuide)는 `EPS 539 ·
BPS 5,856 · PER 3.97 · PBR 0.37 · 상장주식수 207,588,536` 인데 우리
밸류에이션 탭은 `EPS 1,315.53 · BPS 6,680.92 · PER 1.63 · PBR 0.32 ·
발행주식수 185.4M` 였다. 값이 다 '있어서' 어떤 감사도 안 걸렸다.

산수는 각 화면 안에서 전부 맞는다(2,140÷1,315.53=1.63, 2,140÷539=3.97)
— 즉 **분모가 다른 것**이지 계산이 틀린 게 아니다. 그러면 남는 질문은
하나다: 우리 EPS·BPS 는 어느 원천의, 어느 기준(TTM/결산·연결/지배주주)
값인가. 그건 재서만 답할 수 있다(#12 추측보고 금지).

이 프로브는 **화면이 쓰는 그 경로**(`collect_stock_snapshot`)를 태우고
(#35), 옆에 원천별 값을 나란히 놓아 사용자가 눈으로 대조하게 한다(#51
같은 지표를 여러 축에서 대조하면 외부 자료 없이 판정할 수 있다).

    python3 -m bot.scripts.kr_metrics_probe 035890.KQ 005930.KS | tee /tmp/kr.txt
"""
from __future__ import annotations

import sys
import time

_PROBE_VER = 1


def _n(v):
    return (float(v) if isinstance(v, (int, float))
            and not isinstance(v, bool) and v == v else None)


def _f(v, d=2):
    x = _n(v)
    return "—" if x is None else f"{x:,.{d}f}"


def _banner() -> bool:
    """인터프리터와 필수 모듈을 먼저 밝힌다 — venv 밖에서 돌면 원천이
    통째로 비는데 출력은 멀쩡해 보인다(#132)."""
    print(f"=== KR 주당지표 프로브 v{_PROBE_VER} ===")
    print(f"인터프리터: {sys.executable}")
    missing = []
    for m in ("yfinance", "pykrx", "requests"):
        try:
            __import__(m)
            print(f"  모듈 {m}: OK")
        except Exception as exc:                                # noqa: BLE001
            print(f"  모듈 {m}: 없음 ({exc})")
            missing.append(m)
    try:
        from bot.env_keys import env_source, env_why
        for k in ("DART_API_KEY", "DATA_GO_KR_API_KEY"):
            src = env_source(k)
            print(f"  {k}: {src}"
                  + (f" ({env_why(k)})" if src == "없음" else ""))
    except Exception as exc:                                    # noqa: BLE001
        print(f"  자격증명 확인 실패: {exc}")
    if "yfinance" in missing:
        print("⛔ yfinance 가 없으면 스냅샷이 통째로 비어 '원천 미제공'으로 "
              "오보한다 — venv 로 다시 실행할 것.")
        return False
    return True


def _identity(price, mcap, shares, label):
    from bot.share_count import reconcile
    r = reconcile(price, mcap, shares)
    if r["ok"] is None:
        return f"    검산({label}): 재료 부족 — 판정 불가"
    mark = "✅" if r["ok"] else "❌"
    return (f"    검산({label}): 시총÷현재가 = {r['implied']:,.0f}주 vs "
            f"주식수 {_n(shares):,.0f}주 → {(r['ratio'] - 1) * 100:+.2f}% {mark}")


def _per_check(price, eps, per, label):
    p, e, v = _n(price), _n(eps), _n(per)
    if not p or not e or not v:
        return f"    검산({label}): 재료 부족 — 판정 불가"
    calc = p / e
    ok = abs(calc - v) <= max(0.05, abs(v) * 0.02)
    return (f"    검산({label}): 현재가÷EPS = {calc:,.2f} vs 표기 PER "
            f"{v:,.2f} → {'✅' if ok else '❌'}")


def probe(ticker: str) -> None:
    print(f"\n{'=' * 66}\n■ {ticker}\n{'=' * 66}")

    # ① 화면이 쓰는 그 경로 (#35)
    t0 = time.time()
    try:
        from bot.stock_snapshot import collect_stock_snapshot
        snap = collect_stock_snapshot(ticker) or {}
    except Exception as exc:                                    # noqa: BLE001
        print(f"① 스냅샷 수집 실패: {type(exc).__name__}: {exc}")
        return
    print(f"① 우리 스냅샷 (수집 {time.time() - t0:.1f}초)")
    px, mc = snap.get("current_price"), snap.get("market_cap")
    print(f"    현재가 {_f(px, 0)} · 시가총액 {_f(mc, 0)} "
          f"· 발행주식수 {_f(snap.get('shares_outstanding'), 0)}")
    print(f"    주식수 출처: {snap.get('shares_source') or '(미기록)'}"
          + (f" · {snap['shares_note']}" if snap.get("shares_note") else ""))
    print(f"    EPS(후행) {_f(snap.get('trailingEps'))} · "
          f"BPS {_f(snap.get('bookValue'))} · "
          f"PER {_f(snap.get('trailingPE'))} · PBR {_f(snap.get('priceToBook'))}")
    print(_identity(px, mc, snap.get("shares_outstanding"), "우리 화면"))
    print(_per_check(px, snap.get("trailingEps"), snap.get("trailingPE"), "우리 화면"))
    print(f"    파생 항목: {snap.get('_derived_multiples') or '(없음 — 소스값 그대로)'}")

    # ② yfinance 원본 — 우리가 손대기 전 값
    print("② yfinance .info 원본")
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        print(f"    sharesOutstanding {_f(info.get('sharesOutstanding'), 0)} · "
              f"marketCap {_f(info.get('marketCap'), 0)}")
        print(f"    trailingEps {_f(info.get('trailingEps'))} · "
              f"bookValue {_f(info.get('bookValue'))} · "
              f"trailingPE {_f(info.get('trailingPE'))} · "
              f"priceToBook {_f(info.get('priceToBook'))}")
        print(_identity(info.get("currentPrice") or px, info.get("marketCap"),
                        info.get("sharesOutstanding"), "yfinance 자체"))
    except Exception as exc:                                    # noqa: BLE001
        print(f"    실패: {type(exc).__name__}: {exc}")

    # ③ 거래소 등록 주식수 (KRX pykrx → 금융위 FSC)
    print("③ KRX/FSC 등록 주식수")
    try:
        from bot.pykrx_client import get_kr_market_cap
        q = get_kr_market_cap(ticker) or {}
        if q:
            print(f"    상장주식수 {_f(q.get('shares'), 0)} · 시총 "
                  f"{_f(q.get('market_cap'), 0)} · 종가 {_f(q.get('close'), 0)} "
                  f"· {q.get('date')} · 원천 {q.get('_source', 'pykrx')}")
            print(_identity(q.get("close"), q.get("market_cap"),
                            q.get("shares"), "거래소 자체"))
        else:
            print("    받지 못했습니다(KRX 로그인/DATA_GO_KR 키 확인).")
    except Exception as exc:                                    # noqa: BLE001
        print(f"    실패: {type(exc).__name__}: {exc}")

    # ④ KRX 투자지표 벌크 (스크리너가 쓰는 그 값)
    print("④ KRX 투자지표(pykrx 벌크)")
    try:
        from bot.stock_screener import _fetch_kr_bulk
        bulk = _fetch_kr_bulk() or {}
        row = bulk.get(ticker.split(".")[0].zfill(6)) or {}
        if row:
            print(f"    EPS {_f(row.get('EPS'), 0)} · BPS {_f(row.get('BPS'), 0)} "
                  f"· PER {_f(row.get('PER'))} · PBR {_f(row.get('PBR'))} "
                  f"· 종가 {_f(row.get('종가'), 0)}")
            print(_per_check(row.get("종가"), row.get("EPS"), row.get("PER"),
                             "KRX 투자지표"))
        else:
            print(f"    이 종목이 벌크에 없습니다(벌크 {len(bulk)}종목).")
    except Exception as exc:                                    # noqa: BLE001
        print(f"    실패: {type(exc).__name__}: {exc}")

    # ⑤ 네이버(FnGuide) — 사용자의 신뢰 기준
    print("⑤ 네이버(FnGuide) 투자지표 — 사용자의 신뢰 기준")
    try:
        from bot.naver_finance_client import get_naver_valuation
        nv = get_naver_valuation(ticker) or {}
        if nv:
            print(f"    EPS {_f(nv.get('eps'), 0)} · BPS {_f(nv.get('bps'), 0)} "
                  f"· PER {_f(nv.get('per'))} · PBR {_f(nv.get('pbr'))} "
                  f"· 상장주식수 {_f(nv.get('shares'), 0)}")
            print(_identity(px, mc, nv.get("shares"), "우리 시총·현재가 ÷ 네이버 주식수"))
            print(_per_check(px, nv.get("eps"), nv.get("per"), "네이버"))
        else:
            print("    받지 못했습니다.")
    except Exception as exc:                                    # noqa: BLE001
        print(f"    실패: {type(exc).__name__}: {exc}")

    # ⑥ 밴드 탭이 쓰는 경로 — 같은 화면 안에서 갈리는지 본다(#186)
    print("⑥ 밴드 탭(FnGuide)")
    try:
        from bot.band_source import resolve
        r = resolve(ticker, snap)
        per = r.get("per") or {}
        sm = per.get("summary") or {}
        print(f"    basis={r.get('basis')} · 현재 PER {_f(sm.get('per_now'))}"
              f"({sm.get('per_now_basis') or '?'}) · 창 {sm.get('from')}~{sm.get('to')}"
              f" · 관측 {per.get('band_n')}개")
        print(f"    분모: {per.get('band_basis') or '(미기록)'}")
        if r.get("why"):
            print(f"    사유: {r['why']}")
    except Exception as exc:                                    # noqa: BLE001
        print(f"    실패: {type(exc).__name__}: {exc}")

    # ⑦ DART 연결 재무 — 우리가 파생에 쓰는 재료(비지배지분 포함)
    print("⑦ DART 연결 재무(비지배지분 포함)")
    kr = snap.get("kr") or {}
    qs = kr.get("financials_q") or []
    fin = kr.get("financials") or {}
    if qs:
        tail = qs[-4:]
        lab = " → ".join(f"{q.get('year')}.{q.get('quarter')}Q" for q in tail)
        net = [_n(q.get("당기순이익")) for q in tail]
        print(f"    최근 4분기 {lab}")
        print("    당기순이익 " + " / ".join(_f(v, 0) for v in net))
        if all(v is not None for v in net):
            s = sum(net)
            sh = _n(snap.get("shares_outstanding"))
            print(f"    TTM 순이익 {s:,.0f}"
                  + (f" ÷ 주식수 = EPS {s / sh:,.2f}" if sh else ""))
        eq = next((_n(q.get("자본총계")) for q in reversed(qs)
                   if _n(q.get("자본총계"))), None)
        sh = _n(snap.get("shares_outstanding"))
        print(f"    자본총계(최근분기) {_f(eq, 0)}"
              + (f" ÷ 주식수 = BPS {eq / sh:,.2f}" if eq and sh else ""))
    else:
        print(f"    분기 재무 없음(연간 보유: {sorted(fin) if fin else '없음'})")

    print("\n[읽는 법] ①과 ⑤/⑥이 갈리면 원천이 다른 것이고, ①의 검산이 ❌ 면 "
          "우리 화면이 자기 산수를 못 맞춘 것이다(그건 우리 버그다).")


def main(argv: list[str]) -> int:
    from bot.scripts.probe_progress import stream_stdout
    stream_stdout()
    if not _banner():
        return 2
    tickers = [a for a in argv[1:] if not a.startswith("-")] or ["035890.KQ"]
    for tk in tickers:
        probe(tk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
