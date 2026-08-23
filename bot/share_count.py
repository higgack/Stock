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


def resolve(price, mcap, src_shares, src_label: str = "소스",
            reg_shares=None, reg_label: str = "등록 주식수",
            tol: float = TOL) -> dict:
    """등록 주식수가 있으면 **그게 기준**이다 — 항등식만 보면 눈이 먼다.

    ⚠️ 2026-08-23 VM 실측(서희건설 035890.KQ): yfinance 가 시총 3,967억 ·
    주식수 185,368,615 로 **자기들끼리는 정확히 맞았다**(시총÷현재가 =
    185,368,607, 오차 0.00%). 둘 다 **같은 낡은 주식수 위**에 있었을 뿐이고
    진짜 시총은 2,140 × 207,588,536 = 4,442억 이다. 항등식만 보는 가드는 이
    상태를 그냥 통과시킨다(#37 임계값이 증상을 덮는다 · #143 대조군이 없으면
    '없음'과 '못 받음'을 못 가른다).

    그래서 축을 둘로 둔다 — ① 등록 주식수(거래소가 아는 사실)와 대조하고,
    ② 등록 주식수가 없는 시장에서만 항등식으로 어긋남을 말한다.

    반환 `{"shares", "source", "market_cap", "note"}`.

    ⚠️ 시총 재계산은 **등록 주식수가 있을 때만** 한다. 복수 클래스 상장은
    한 클래스 주식수 × 주가 ≠ 전체 시총이라(`market.py` Class A 사례) 일반
    규칙으로 쓰면 안 된다.
    """
    src, reg, px = _num(src_shares), _num(reg_shares), _num(price)
    out = {"shares": src, "source": src_label if src else "",
           "market_cap": _num(mcap), "note": ""}
    if reg and reg > 0:
        # ⚠️ 등록 주식수가 있으면 **오차와 무관하게** 그게 기준이다.
        # 옛 판은 `> tol` 일 때만 교체해서, 차이가 작으면(슈프리마 0.11% —
        # yfinance 6,966,583 vs KRX 6,974,311) yfinance 값이 남고 시총도
        # 3,908억으로 네이버 3,913억과 어긋났다. 더 나쁜 건 **조회할 때마다
        # 기준이 바뀌는 것** — 오차가 문턱을 넘나들면 화면 값이 흔들린다.
        # 거래소 등록 주식수는 정의상 사실이라 문턱을 둘 이유가 없다.
        out["shares"], out["source"] = reg, reg_label
        if px:
            out["market_cap"] = px * reg
            out["source"] += " · 시총 재계산"
        if not src:
            out["note"] = f"{src_label} 가 주식수를 안 줘 등록 주식수 사용"
        elif abs(src / reg - 1.0) > tol:
            # 사소한 차이까지 떠들면 잡음이다 — 큰 차이만 말한다.
            out["note"] = (f"{src_label} {src:,.0f}주는 등록 주식수와 "
                           f"{(src / reg - 1) * 100:+.1f}% 달라 교체")
        return out
    if not src:
        return out
    r = reconcile(px, mcap, src, tol)
    if r["ok"] is False:
        out["note"] = (f"{src_label} {src:,.0f}주가 시가총액÷현재가"
                       f"({r['implied']:,.0f}주)와 {(r['ratio'] - 1) * 100:+.1f}% "
                       f"어긋납니다 — 등록 주식수 원천이 없는 시장입니다")
    return out


def note(price, mcap, shares, source: str = "", tol: float = TOL) -> str:
    """헤더에 실을 한 줄. 맞으면 출처만, 어긋나면 **어긋난 사실**을 말한다.

    조용히 두면 사용자가 눈으로 나눠 보고 물어야 한다(#33·#43).
    """
    r = reconcile(price, mcap, shares, tol)
    if r["ok"] is False:
        return (f"⚠️ 시가총액÷현재가 = {r['implied']:,.0f}주 와 "
                f"{(r['ratio'] - 1) * 100:+.1f}% 차이")
    return source or ""


def weighted_issued(series, periods) -> tuple:
    """수정평균 발행주식수 — `(값, 사람이 읽는 근거, 쓴 분기 수)`.

    FnGuide 산식(사용자 2026-08-23 제공)은 EPS 분모를 **수정평균
    발행주식수(보통주+우선주)** 로 쓴다. 우리는 **기말** 상장주식수로 나눠
    왔다 — 주식수가 기중에 변한 회사(자사주 소각·증자)에서만 갈린다.

    `series` = `dart_client.get_share_totals_series` 산출(최신 → 과거),
    `periods` = 창에 쓸 기간 라벨 집합(`{"2026.06", "2026.03", …}`).
    창에 해당하는 분기말 발행주식수의 **산술평균**을 쓴다 — 분기 안에서
    바뀐 시점까지는 원천이 안 주므로 그 이상은 추정이 된다(#32).

    ⚠️ 창의 분기가 하나라도 비면 **만들지 않는다**(None). 3개로 평균을
    내면 그건 다른 창의 값이고, 그걸 'TTM' 이라 부르면 거짓말이다(#99).
    ⚠️ 값이 전부 같으면(주식수 변동 없음) 평균 = 기말이라 **아무것도
    바뀌지 않는다** — 그게 정상이고, 그때 남는 차이는 분모가 아니라
    분자에서 온다.
    """
    want = {str(p) for p in (periods or []) if p}
    if not want:
        return None, "", 0
    got = {}
    for e in series or []:
        p = str((e or {}).get("period") or "")
        v = (e or {}).get("issued")
        if p in want and isinstance(v, (int, float)) and not isinstance(v, bool) \
                and v > 0:
            got.setdefault(p, float(v))
    if len(got) < len(want):
        return None, "", len(got)
    avg = sum(got.values()) / len(got)
    same = len(set(got.values())) == 1
    lab = ("발행주식수 변동 없음" if same
           else " · ".join(f"{p} {int(v):,}" for p, v in sorted(got.items())))
    return avg, lab, len(got)
