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

_PROBE_VER = 10


def _n(v):
    return (float(v) if isinstance(v, (int, float))
            and not isinstance(v, bool) and v == v else None)


def _f(v, d=2):
    x = _n(v)
    return "—" if x is None else f"{x:,.{d}f}"


# 재실행 루프 방지 — 자식은 이 표시를 들고 뜨므로 다시 갈아타지 않는다.
_REEXEC_FLAG = "KR_PROBE_REEXEC"


def _venv_python() -> str:
    """레포 옆 venv 의 파이썬 경로(없으면 빈 문자열).

    ⚠️ 사용자가 같은 벽에 두 번 부딪혔다(2026-08-23): `venv/bin/activate`
    가 없어서 한 번(디렉터리 이름이 `.venv`), 시스템 파이썬으로 돌아서 한 번.
    안내 문구를 다듬는 대신 **자동으로 갈아탄다**(Automation-first).
    """
    import os
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for name in (".venv", "venv"):
        cand = root / name / "bin" / "python3"
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return ""


def _reexec_target(missing: bool, flagged: bool, venv: str,
                   current: str) -> str:
    """갈아탈 파이썬 경로(안 갈아타면 빈 문자열) — **판정만** 하는 순수 함수.

    `os.execv` 를 직접 테스트할 수 없으니 결정을 떼어 값으로 고정한다(#41).
    루프 방지 조건이 둘이다 — 이미 갈아탄 프로세스(`flagged`)이거나 이미
    그 파이썬(`current`)이면 안 간다. 하나만 두면 다른 하나로 루프가 난다.
    """
    import os
    if not missing or flagged or not venv:
        return ""
    # ⚠️ **realpath 로 비교하면 안 된다**(2026-08-23 VM 실측: 재실행이 한
    # 번도 안 걸렸다). venv 의 `bin/python3` 는 base 파이썬으로 가는 심볼릭
    # 링크라 realpath 가 `/usr/bin/python3` 로 **같아진다** — venv 를 venv 로
    # 만드는 건 그 바이너리가 아니라 `pyvenv.cfg`·`sys.prefix` 다. 가드가
    # 재는 대상을 틀리면 그냥 눈이 먼다(#91b).
    same = os.path.abspath(venv) == os.path.abspath(current or "")
    return "" if same else venv


def _reexec_in_venv() -> None:
    """필수 모듈이 없고 venv 가 옆에 있으면 **그 파이썬으로 다시 뜬다**."""
    import os
    import sys as _s
    try:
        __import__("yfinance")
        missing = False
    except Exception:                                           # noqa: BLE001
        missing = True
    vp = _reexec_target(missing, bool(os.environ.get(_REEXEC_FLAG)),
                        _venv_python(), _s.executable)
    if not vp:
        return
    print(f"↪ yfinance 가 없어 venv 로 다시 실행합니다: {vp}", flush=True)
    os.environ[_REEXEC_FLAG] = "1"
    os.execv(vp, [vp, "-m", "bot.scripts.kr_metrics_probe", *_s.argv[1:]])


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
              "오보한다(#132).")
        print(f"   다시 실행: {_venv_python() or 'python3'} -m "
              f"bot.scripts.kr_metrics_probe <티커> | tee /tmp/kr.txt")
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
    # ⚠️ 계산해 놓고 안 찍으면 '왜 그 값인가'를 사람이 짐작하게 된다
    # (#123·#129·#131·#189 에 이은 같은 실수 — 프로브에서도 반복하지 말 것).
    print(f"    분자 기준: {si.get('_derived_scope') or '(미기록)'}")
    if si.get("_ttm_note"):
        print(f"    ⚠️ {si['_ttm_note']}")
    # ⚠️ POSCO홀딩스 005490.KS(2026-08-23): BPS 809,716 vs 네이버 759,917.
    # 후보가 둘이다 — (a) 지배주주자본이 없어 **연결 총액**으로 나눴나
    # (b) 분모에서 **자사주를 안 뺐나**. 둘 다 찍어야 갈린다(#149 단계별로).
    _kr = snap.get("kr") or {}
    _st = _kr.get("share_totals") or {}
    print(f"    주식의 총수(DART): 발행주식수 {_f(_st.get('issued'), 0)} · "
          f"자기주식 {_f(_st.get('treasury'), 0)} · "
          f"유통 {_f(_st.get('distributed'), 0)} · 기준 {_st.get('basis') or '—'}")
    _q = (_kr.get("financials_q") or [])
    if _q:
        _lq = _q[-1]
        print(f"    최신 분기 자본 계정: 지배주주자본 "
              f"{_f(_lq.get('지배주주자본'), 0)} · 자본총계 "
              f"{_f(_lq.get('자본총계'), 0)} → BPS 분자는 "
              f"{'지배주주' if _lq.get('지배주주자본') is not None else '연결 총액'}")
        # ⚠️ 스냅샷의 분기 항목은 비율을 **평평하게** 싣는다(`ratios` 중첩이
        # 아니다) — v9 는 중첩만 읽어 멀쩡한 ROE 를 `—` 로 오보했다(#35 의
        # 프로브판: 화면이 읽는 그 자리를 읽어야 한다).
        _rb = (_lq.get("_returns_basis")
               or (_lq.get("ratios") or {}).get("_returns_basis"))
        _roe = (_lq.get("ROE") if _lq.get("ROE") is not None
                else (_lq.get("ratios") or {}).get("ROE"))
        print(f"    ROE 기준: {_rb or '(미기록)'} · ROE {_f(_roe)}%")
    # 배당수익률 — 우리 계산 vs 네이버 원천(FnGuide)
    try:
        from bot.dashboard import dividend_yield_pct
        _dv, _dsrc = dividend_yield_pct(si)
        print(f"    배당수익률 {_f(_dv)}% ({_dsrc}) · yfinance dividendRate "
              f"{_f(snap.get('dividendRate'), 0)} · dividendYield "
              f"{_f(snap.get('dividendYield'), 4)}")
    except Exception as exc:                                    # noqa: BLE001
        print(f"    배당수익률 판정 실패: {type(exc).__name__}: {exc}")

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
            print(f"    추정PER {_f(nv.get('cns_per'))} · 추정EPS "
                  f"{_f(nv.get('cns_eps'), 0)} · 배당수익률 "
                  f"{_f(nv.get('dvr'))}% ({nv.get('dvr_asof') or '기준 미기록'})")
            # 분모 역산 — 값이 아니라 **분모 규약**이 다른 것인지 갈린다(#204)
            for lab, num, val in (("EPS", si.get("_kr_net_ttm"), nv.get("eps")),
                                  ("BPS", si.get("_kr_equity"), nv.get("bps"))):
                if num and val:
                    print(f"    └ 네이버 {lab} 역산 분모 = {num / val:,.0f}주")
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
            # ⚠️ 화면은 **지배주주 귀속분**을 분자로 쓴다(FnGuide 산식) —
            # 여기서 총액으로만 찍으면 프로브가 화면과 다른 EPS 를 말한다
            # (POSCO 실측: 총액 17,428 vs 화면 17,004, #35).
            owns = [_n(q.get("지배주주순이익")) for q in tail]
            tot_own = sum(owns) if all(v is not None for v in owns) else None
            print(f"    TTM 순이익(총액) {tot:,.0f}"
                  + (f" ÷ 주식수 = EPS {tot / sh:,.2f}" if sh else ""))
            if tot_own is not None:
                print(f"    TTM 순이익(지배주주 — 화면 분자) {tot_own:,.0f}"
                      + (f" ÷ 주식수 = EPS {tot_own / sh:,.2f}" if sh else ""))
        else:
            print(f"    ⚠️ 4분기가 안 채워졌다({len(vals)}/4) — TTM 을 만들지 않는다")
        # BPS 도 화면과 같은 분자·분모로 — 지배주주자본 ÷ 유통주식수
        eq = next((_n(q.get("자본총계")) for q in reversed(qs)
                   if _n(q.get("자본총계"))), None)
        eq_own = next((_n(q.get("지배주주자본")) for q in reversed(qs)
                       if _n(q.get("지배주주자본"))), None)
        _dist = _n(((snap.get("kr") or {}).get("share_totals") or {})
                   .get("distributed"))
        print(f"    자본총계(최근분기) {_f(eq, 0)}"
              + (f" ÷ 상장주식수 = BPS {eq / sh:,.2f}" if eq and sh else ""))
        if eq_own is not None and (_dist or sh):
            _d = _dist or sh
            print(f"    지배주주자본(화면 분자) {eq_own:,.0f} ÷ "
                  f"{'유통' if _dist else '상장'}주식수 = BPS {eq_own / _d:,.2f}")
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
        # ⚠️ FnGuide 산식(사용자 제공 2026-08-23)은 **지배주주지분** 기준이다
        # — EPS = (지배주주지분)당기순이익 / 수정평균발행주식수. 총액과 얼마나
        # 갈리는지가 EPS 차이의 답이므로 나란히 찍는다.
        for e in ser:
            fin = e.get("financials") or {}
            own = fin.get("지배주주순이익")
            tot = fin.get("당기순이익")
            share = (f" ({own / tot * 100:.0f}%)"
                     if _n(own) and _n(tot) else "")
            print(f"      {e.get('label')} rc={e.get('reprt_code')} "
                  f"{e.get('fs_div')} 당기순이익(총액) {_f(tot, 0)}"
                  f" · 지배주주 {_f(own, 0)}{share}"
                  f" · 자본총계 {_f(fin.get('자본총계'), 0)}"
                  f" · 지배주주자본 {_f(fin.get('지배주주자본'), 0)}"
                  f" · 비지배지분 {_f(fin.get('비지배지분'), 0)}"
                  + (f" [{(fin.get('_derived_from') or {}).get('지배주주자본')}]"
                     if (fin.get("_derived_from") or {}).get("지배주주자본")
                     else ""))
        if not any((e.get("financials") or {}).get("지배주주순이익")
                   for e in ser):
            print("      ⚠️ 지배주주순이익 계정이 한 분기도 없다 — 원천 미제공"
                  "이거나 계정 매핑이 못 잡은 것이다(아래 채택 계정 확인).")
        # ⚠️ DART 는 **보고된 기본주당이익**(K-IFRS 1033)을 직접 준다 —
        # 그건 정의상 **가중평균유통보통주식수** 기준이라 FnGuide 와 같은
        # 자다. 우리가 기말 주식수로 나눠 만든 값과 얼마나 다른지, 그리고
        # 분기 EPS 가 당기(3개월)인지 누적인지(#96)를 여기서 가른다.
        eps_q = [(e.get("label"), (e.get("financials") or {}).get("EPS"))
                 for e in ser]
        print("    보고 EPS(K-IFRS 기본주당이익, 가중평균 기준): "
              + " / ".join(f"{lb} {_f(v, 0)}" for lb, v in eps_q[-4:]))
        vals4 = [v for _lb, v in eps_q[-4:] if _n(v) is not None]
        if len(vals4) == 4:
            print(f"    → 최근 4분기 합 {sum(vals4):,.0f}"
                  " (우리 화면 EPS 와 비교: 위 ① 참조)")
        else:
            print(f"    → 4분기가 안 채워졌다({len(vals4)}/4) — 합을 만들지 않는다")
        last = ser[-1]
        y, rc = last.get("year"), last.get("reprt_code")
        raw = dart.get_normalized_financials(ticker, year=y,
                                             fs_div=last.get("fs_div"),
                                             reprt_code=rc) or {}
        cur = (raw.get("financials") or {})
        cum = (raw.get("financials_cumulative") or {})
        print(f"    최신 보고서 {y}/{rc} ({last.get('fs_div')})")
        # ⚠️ FCF 재료를 나란히 — 사용자 2026-08-23 "우리 FCF 계산하는 방법이
        # 잘못된것 같은데". FnGuide 산식은 `CAPEX = 유형자산의증가` 이고
        # 무형은 안 들어간다. LG이노텍 실측으로 그렇게 맞춰 놨는데, **그
        # 예측이 맞는지는 유형자산취득 단독 값이 FnGuide CAPEX 와 같은가**로
        # 판정된다 — 그래서 구성요소를 전부 찍는다(#109 표본을 같이 찍을 것).
        _ann = dart.get_normalized_financials(ticker) or {}
        _af = _ann.get("financials") or {}
        print(f"    [FCF 재료 · 최신 사업보고서 {_ann.get('year') or '—'}] "
              f"영업활동현금흐름 {_f(_af.get('영업활동현금흐름'), 0)} · "
              f"유형자산취득 {_f(_af.get('유형자산취득'), 0)} · "
              f"무형자산취득 {_f(_af.get('무형자산취득'), 0)} → "
              f"FCF {_f(_af.get('FCF'), 0)}")
        _o, _t = _n(_af.get("영업활동현금흐름")), _n(_af.get("유형자산취득"))
        _i = _n(_af.get("무형자산취득"))
        if _o is not None and _t is not None:
            print(f"      유형만: {(_o - abs(_t)) / 1e8:,.0f}억"
                  + (f" · 유형+무형: {(_o - abs(_t) - abs(_i)) / 1e8:,.0f}억"
                     if _i is not None else "")
                  + "  ← FnGuide FCF 와 같은 쪽이 정답이다")
        for k in ("매출", "영업이익", "당기순이익", "EPS"):
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
    _reexec_in_venv()
    if not _banner():
        return 2
    tickers = [a for a in argv[1:] if not a.startswith("-")] or ["035890.KQ"]
    for tk in tickers:
        probe(tk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
