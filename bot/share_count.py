"""발행주식수 · 시가총액 · 현재가의 **항등식**을 한 곳에서 판정한다.

⚠️ 2026-08-23 서희건설(035890.KQ) 실측으로 드러난 것: 헤더가
`현재가 2,140 · 시가총액 4,442억 · 발행주식수 185.4M` 를 나란히 띄웠는데
4,442억 ÷ 185.4M = 2,396 이라 **화면이 자기 산수를 못 맞췄다**(실수 #33).
네이버(FnGuide)의 상장주식수는 207,588,536 이고 2,140 × 207,588,536 =
4,442억 으로 정확히 맞는다 — 즉 시총·현재가가 맞고 **주식수만 틀렸다**
(yfinance `sharesOutstanding` 이 국내 종목에서 낡는다).

주식수는 EPS·BPS 의 분모라 한 번 틀리면 주당지표가 통째로 그만큼 밀린다.
그래서 판정을 순수 함수로 빼서 **화면·수집기·프로브가 같은 규칙**을 쓴다
(#38) — 그리고 "어느 값이 맞나"를 추측하지 않고 **항등식을 얼마나 만족
하는가**로 고른다(#162 원인을 추측하지 말고 효과로 판정).
"""
from __future__ import annotations

# 시총은 원천마다 반올림·기준시각이 달라 정확히 안 맞는다 — 2% 는 그
# 오차는 통과시키고 주식수 세대차이(서희건설 12%)는 잡는 폭이다.
TOL = 0.02


def _num(v):
    return (float(v) if isinstance(v, (int, float))
            and not isinstance(v, bool) and v == v else None)


def implied_shares(price, mcap):
    """시가총액 ÷ 현재가 — 화면이 이미 띄운 두 칸에서 나오는 주식수."""
    p, m = _num(price), _num(mcap)
    if not p or not m or p <= 0 or m <= 0:
        return None
    return m / p


def reconcile(price, mcap, shares, tol: float = TOL) -> dict:
    """`{"ok", "implied", "ratio"}`.

    `ok` 는 3-상태다 — True(맞음) / False(어긋남) / **None(판정 불가)**.
    재료가 없으면 통과가 아니라 판정 불가다(#54 대조 0건은 ✅ 가 아니다).
    """
    imp = implied_shares(price, mcap)
    s = _num(shares)
    if imp is None or not s or s <= 0:
        return {"ok": None, "implied": imp, "ratio": None}
    ratio = s / imp
    return {"ok": abs(ratio - 1.0) <= tol, "implied": imp, "ratio": ratio}


def pick(price, mcap, candidates, tol: float = TOL):
    """후보 주식수 중 **항등식을 가장 잘 만족하는** 것을 고른다.

    `candidates` = [(값, 출처라벨), …] — 앞이 현행 값이다. 돌려주는 값은
    `(값, 출처라벨, 사유)`. 판정할 재료가 없거나 현행이 이미 맞으면
    **현행을 그대로** 둔다(소스값을 함부로 덮지 않는다).
    """
    cands = [(v, lab) for v, lab in candidates if _num(v) and _num(v) > 0]
    if not cands:
        return None, None, "주식수 후보 없음"
    cur_v, cur_lab = cands[0]
    imp = implied_shares(price, mcap)
    if imp is None:
        return cur_v, cur_lab, "현재가·시가총액이 없어 검산 불가"
    scored = sorted(cands, key=lambda c: abs(_num(c[0]) / imp - 1.0))
    best_v, best_lab = scored[0]
    cur_err = abs(_num(cur_v) / imp - 1.0)
    best_err = abs(_num(best_v) / imp - 1.0)
    if cur_err <= tol or best_lab == cur_lab:
        return cur_v, cur_lab, ""
    return best_v, best_lab, (
        f"{cur_lab} {_num(cur_v):,.0f}주는 시가총액÷현재가({imp:,.0f}주)와 "
        f"{cur_err * 100:.1f}% 어긋나 {best_lab} 값으로 교체")


def note(price, mcap, shares, source: str = "", tol: float = TOL) -> str:
    """헤더에 실을 한 줄. 맞으면 출처만, 어긋나면 **어긋난 사실**을 말한다.

    조용히 두면 사용자가 눈으로 나눠 보고 물어야 한다(#33·#43).
    """
    r = reconcile(price, mcap, shares, tol)
    if r["ok"] is False:
        return (f"⚠️ 시가총액÷현재가 = {r['implied']:,.0f}주 와 "
                f"{(r['ratio'] - 1) * 100:+.1f}% 차이")
    return source or ""
