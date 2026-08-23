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

_PROBE_VER = 3


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
        for k in ("DART_API_KEY", "DATA_GO_KR_API_KEY", "KRX_ID", "KRX_PW"):
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
    print(_identity(px, mc, snap.get("shares_outstanding"), "우리 화면"))
    # ⚠️ v1 은 수집기만 태워서 EPS·BPS 가 전부 '—' 로 나왔다 — yfinance 가
    # 국내 종목 fundamentals 를 404 로 주고, 우리 화면 값은 **렌더 단계**의
    # `_derive_missing_multiples` 가 DART 재무로 만든다. 프로브가 화면과
    # 다른 경로를 타면 통계가 통째로 거짓말한다(#35) — 그 함수를 태운다.
    try:
        from bot.dashboard import _derive_missing_multiples
        si = _derive_missing_multiples(snap)
    except Exception as exc:                                    # noqa: BLE001
        print(f"    ⚠️ 화면 파생 실패: {type(exc).__name__}: {exc}")
        si = snap
    print(f"    [화면이 그리는 값] EPS(후행) {_f(si.get('trailingEps'))} · "
          f"BPS {_f(si.get('bookValue'))} · "
          f"PER {_f(si.get('trailingPE'))} · PBR {_f(si.get('priceToBook'))}")
    print(_per_check(px, si.get("trailingEps"), si.get("trailingPE"), "우리 화면"))
    print(f"    파생 항목: {si.get('_derived_multiples') or '(없음 — 소스값 그대로)'}"
          f" · 기준 {si.get('_derived_basis') or '—'}")

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
        print(f"    유니버스 {len(bulk)}종목"
              + ("  ⚠️ 코스닥이 빠진 것 같다(KOSPI 만 ≈900)"
                 if bulk and len(bulk) < 1500 else ""))
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
        sh = _n(snap.get("shares_outstanding"))
        net = [_n(q.get("당기순이익")) for q in tail]
        vals = [v for v in net if v is not None]
        # ⚠️ 한 분기가 나머지를 압도하면 TTM 이 그 한 칸에 좌우된다 —
        # 합만 찍으면 안 보인다(2026-08-23 서희건설 2026.2Q = 나머지 3분기
        # 합의 1.6배). 분기별로 찍고 비중을 같이 적는다(#45·#55).
        for q, v in zip(tail, net):
            share = (f" · TTM 의 {v / sum(vals) * 100:.0f}%"
                     if v is not None and vals and sum(vals) else "")
            flag = ""
            if v is not None and len(vals) > 1:
                others = [x for x in vals if x is not v]
                if others and v > 3 * (sum(others) / len(others)):
                    flag = "  ⚠️ 형제 분기 평균의 3배 초과"
            print(f"      {q.get('year')}.{q.get('quarter')}Q 당기순이익 "
                  f"{_f(v, 0)}{share}{flag}")
            for k in ("_anomaly_revenue_negative", "_anomaly_account_mismatch",
                      "_mismatched_accounts", "missing_quarters"):
                if q.get(k):
                    print(f"          플래그 {k}: {q.get(k)}")
        if len(vals) == 4:
            tot = sum(vals)
            print(f"    TTM 순이익 {tot:,.0f}"
                  + (f" ÷ 주식수 = EPS {tot / sh:,.2f}" if sh else ""))
        else:
            print(f"    ⚠️ 4분기가 안 채워졌다({len(vals)}/4) — TTM 을 만들지 않는다")
        eq = next((_n(q.get("자본총계")) for q in reversed(qs)
                   if _n(q.get("자본총계"))), None)
        print(f"    자본총계(최근분기) {_f(eq, 0)}"
              + (f" ÷ 주식수 = BPS {eq / sh:,.2f}" if eq and sh else ""))
        # 네이버 EPS 가 있으면 **원천이 본 분기 이익**을 되짚어 어느 분기가
        # 갈리는지 지목한다 — 합끼리 비교하면 '어딘가 다르다'까지만 말한다.
        try:
            from bot.naver_finance_client import get_naver_valuation
            nv2 = get_naver_valuation(ticker) or {}
            n_eps, n_sh = _n(nv2.get("eps")), _n(nv2.get("shares"))
            if n_eps and n_sh and len(vals) == 4:
                fg = n_eps * n_sh
                print(f"    FnGuide TTM 순이익 ≈ {fg:,.0f} (EPS {n_eps:,.0f} × "
                      f"{n_sh:,.0f}주) · 우리와 차 {sum(vals) - fg:,.0f}")
                if len(vals) == 4:
                    print(f"    → 앞 3분기 합 {sum(vals[:3]):,.0f} 를 그대로 두면 "
                          f"FnGuide 가 본 최신 분기 ≈ {fg - sum(vals[:3]):,.0f} "
                          f"(우리 {vals[3]:,.0f})")
        except Exception as exc:                                # noqa: BLE001
            print(f"    (FnGuide 대조 실패: {type(exc).__name__}: {exc})")
    else:
        print(f"    분기 재무 없음(연간 보유: {sorted(fin) if fin else '없음'})")

    _dart_raw(ticker, snap)

    print("\n[읽는 법] ①과 ⑤/⑥이 갈리면 원천이 다른 것이고, ①의 검산이 ❌ 면 "
          "우리 화면이 자기 산수를 못 맞춘 것이다(그건 우리 버그다).")


def _dart_raw(ticker: str, snap: dict) -> None:
    """⑧ 한 분기가 TTM 을 지배하면 **그 분기의 원본**을 봐야 한다.

    ⚠️ 서희건설 2026.2Q 가 TTM 의 62% 였다(나머지 3분기 합의 1.6배).
    합만 찍는 진단은 '어딘가 다르다'까지만 말한다 — DART 가 그 보고서에서
    준 **당기 3개월(thstrm)** 과 **당기 누적(thstrm_add)** 을 나란히 놓으면
    누적이 분기 자리에 앉은 것인지 진짜 일회성 이익인지 갈린다(#96).
    ⚠️ 그리고 창을 맞춰야 한다 — 6분기를 받아 전년 동기까지 보여준다(#99).
    """
    print("⑧ DART 원본 대조(창 6분기 + 당기/누적)")
    try:
        from bot.dart_client import get_dart
        from bot.dart_quarterly import get_quarterly_series
        dart = get_dart()
        if not dart:
            print("    DART 클라이언트 없음(키 확인)")
            return
        ser = get_quarterly_series(dart, ticker, n=6) or []
        if not ser:
            print("    분기 시리즈 없음")
            return
        for e in ser:
            fin = e.get("financials") or {}
            print(f"      {e.get('label')} rc={e.get('reprt_code')} "
                  f"{e.get('fs_div')} 당기순이익 {_f(fin.get('당기순이익'), 0)}"
                  f" · 매출 {_f(fin.get('매출'), 0)}")
        last = ser[-1]
        y, rc = last.get("year"), last.get("reprt_code")
        raw = dart.get_normalized_financials(ticker, year=y,
                                             fs_div=last.get("fs_div"),
                                             reprt_code=rc) or {}
        cur = (raw.get("financials") or {})
        cum = (raw.get("financials_cumulative") or {})
        print(f"    최신 보고서 {y}/{rc} ({last.get('fs_div')})")
        for k in ("매출", "영업이익", "당기순이익"):
            print(f"      {k}: 당기(thstrm) {_f(cur.get(k), 0)}"
                  f" · 누적(thstrm_add) {_f(cum.get(k), 0)}")
        src = cur.get("_src") or {}
        if src:
            print(f"      채택 계정: {src}")
        # 연간도 같이 — KRX 투자지표 EPS(최근 결산 기준)와 대조된다
        ann = dart.get_normalized_financials(ticker, year=(y - 1),
                                             fs_div=last.get("fs_div"),
                                             reprt_code="11011") or {}
        af = ann.get("financials") or {}
        if af:
            sh = _n(snap.get("shares_outstanding"))
            print(f"    {y - 1} 연간 당기순이익 {_f(af.get('당기순이익'), 0)}"
                  + (f" ÷ 주식수 = EPS {af['당기순이익'] / sh:,.2f}"
                     if af.get("당기순이익") and sh else ""))
    except Exception as exc:                                    # noqa: BLE001
        print(f"    실패: {type(exc).__name__}: {exc}")


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
