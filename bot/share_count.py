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
        out["note"] = mismatch_note(px, _num(mcap), src, r, src_label)
    return out


def mismatch_note(price, mcap, shares, rec: dict, src_label: str = "소스",
                  mcap_label: str = "") -> str:
    """항등식이 깨졌을 때 **무엇과 비교했는지**까지 말한다.

    ⚠️ 사용자 2026-08-24 (BYD 1211.HK) "왜 이런 오차가 나오지? 그냥 야후에서
    받아오는거잖아." — 옛 문구는 `시가총액÷현재가 = 9,117,197,357주 와
    -59.6% 차이` 라고만 적어, 어느 시가총액으로 나눈 건지 화면에서 알 수
    없었다(헤더는 라이브 원천의 값을 따로 보여준다). 비교 대상을 **숫자로**
    적는다(#33·#202 '다르다'만 말하면 안 통한다).

    그리고 A주+H주처럼 **복수 클래스·복수 상장**이면 시총은 전 클래스,
    발행주식수는 한 클래스라 항등식이 원래 안 맞는다 — 우리 수집 오류가
    아닐 수 있음을 밝힌다(#146 원인이 아니라 증상을 막지 말 것). 단
    **단정하지 않는다** — 우리는 다른 클래스의 존재를 재지 않았다(#165).
    """
    if not rec or rec.get("ok") is not False:
        return ""
    gap = (rec["ratio"] - 1) * 100
    # 화면의 시가총액 카드와 **같은 문자열**을 쓴다 — 원시 숫자를 적으면
    # 사용자가 카드와 대조할 수 없다(#43·#181 표시용 포맷 헬퍼를 쓸 것).
    m = mcap_label or (f"{mcap:,.0f}" if isinstance(mcap, (int, float)) else "—")
    # 헤더는 바로 앞에 발행주식수를 이미 적는다 — 되풀이하지 않는다.
    out = (f"⚠️ 시가총액({m}) ÷ 현재가 = {rec['implied']:,.0f}주 로 "
           f"{gap:+.1f}% 차이")
    if abs(gap) >= 20:
        out += (" · 복수 클래스·복수 상장(A주+H주 등)이면 시총은 전 클래스, "
                "발행주식수는 한 클래스라 원래 안 맞을 수 있습니다")
    return out


def note(price, mcap, shares, source: str = "", tol: float = TOL,
         mcap_label: str = "") -> str:
    """헤더에 실을 한 줄. 맞으면 출처만, 어긋나면 **어긋난 사실**을 말한다.

    조용히 두면 사용자가 눈으로 나눠 보고 물어야 한다(#33·#43).
    """
    r = reconcile(price, mcap, shares, tol)
    if r["ok"] is False:
        return mismatch_note(price, _num(mcap), _num(shares), r,
                             source or "소스", mcap_label)
    return source or ""


def clean_ratio(x: float):
    """`x` 가 **정수배(≥2)** 또는 그 역수면 그 값을, 아니면 None.

    주식분할·무상증자·병합은 정확한 정수비로 일어난다(5:1 · 2:1 · 1:10…).
    유상증자는 그렇지 않다 — 그래서 이 판정이 둘을 가른다.
    """
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if x <= 0:
        return None
    for cand in (x, 1.0 / x):
        n = round(cand)
        if n >= 2 and abs(cand - n) <= 0.005 * n:
            return float(n) if cand is x else 1.0 / n
    return None


def split_adjust(got: dict) -> tuple:
    """분기말 발행주식수 map → (**소급조정된** map, 사람이 읽는 사유).

    ⚠️ 왜(2026-08-24 LS ELECTRIC 010120.KS, 사용자 "PER (후행) 숫자가 너무
    안맞는것 같은데"): 2026.06 에 5:1 액면분할이 있어 창이
    `30,000,000 · 30,000,000 · 30,000,000 · 150,000,000` 이었고 단순 계단
    평균이 **60,000,000** 이 됐다 — TTM 지배주주순이익 388,760,977,637 을
    그걸로 나눠 EPS 6,479.35(네이버 2,592 의 2.5배)가 화면에 올랐다.

    주식분할·무상증자는 **자원 유입 없이 주식 수만 늘어난다** — K-IFRS 1033
    도 FnGuide 산식도 표시되는 **전 기간을 분할 후 기준으로 소급조정**한다.
    (유상증자는 자원이 들어오므로 소급하지 않는다 — `clean_ratio` 가 정수비
    여부로 둘을 가른다. 증상이 아니라 **원인으로 판정**한다, #146.)

    최신에서 과거로 훑으며 배수 점프를 만나면 그 **앞의 전 분기**에 비율을
    곱한다. 곱한 뒤에는 그 구간의 비율이 1 이 되므로 중복 적용되지 않는다.
    """
    keys = sorted(got or {})
    out = {k: float(v) for k, v in (got or {}).items()}
    notes = []
    for i in range(len(keys) - 1, 0, -1):
        cur, prev = out[keys[i]], out[keys[i - 1]]
        if not prev or not cur:
            continue
        r = clean_ratio(cur / prev)
        if r is None or r == 1.0:
            continue
        for j in range(i):
            out[keys[j]] *= r
        notes.append(
            f"{keys[i - 1]}→{keys[i]} "
            + (f"{r:g}:1 분할·무상증자" if r > 1 else f"1:{1 / r:g} 병합")
            + " 소급 반영")
    return out, " · ".join(reversed(notes))


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
    want = sorted({str(p) for p in (periods or []) if p})
    if not want:
        return None, "", 0
    obs = sorted(
        ((str((e or {}).get("period") or ""), float((e or {}).get("issued")))
         for e in (series or [])
         if isinstance((e or {}).get("issued"), (int, float))
         and not isinstance((e or {}).get("issued"), bool)
         and float((e or {}).get("issued")) > 0
         and (e or {}).get("period")))
    if not obs:
        return None, "", 0
    # ⚠️ **원천이 분기마다 주지 않는다.** DART `stockTotqySttus` 는 실측상
    # 사업보고서·반기보고서에만 실린다(POSCO 2026-08-23: 2026.06 · 2025.12 ·
    # 2025.06 만 옴). 창의 네 분기를 **정확히** 요구하면 평균이 영영 안
    # 만들어진다(#171 가드가 '못 만든다'로 끝나면 그 자리가 영원히 빈다).
    # 주식수는 **사건이 있을 때만 바뀌는 계단 함수**이므로 각 분기말에는
    # '그 시점 이전의 가장 최근 관측'을 쓴다 — 추정이 아니라 마지막 사실이다.
    got, used = {}, {}
    for p in want:
        prev = [(op, ov) for op, ov in obs if op <= p]
        if not prev:
            return None, "", 0      # 가장 오래된 분기 앞에 관측이 없다 = 모른다
        op, ov = prev[-1]
        got[p] = ov
        used[p] = op
    # 분할·무상증자는 **소급조정**한다(위 `split_adjust`) — 안 하면 분할이
    # 낀 창에서 분모가 분할비만큼 작아져 EPS 가 그만큼 부푼다.
    got, _split_note = split_adjust(got)
    avg = sum(got.values()) / len(got)
    same = len(set(got.values())) == 1
    lab = ("발행주식수 변동 없음" if same
           else " · ".join(f"{p} {int(got[p]):,}"
                           + ("" if used[p] == p else f"({used[p]} 기준)")
                           for p in want))
    if _split_note:
        # 조정했으면 **조정했다고 말한다** — 숫자만 바뀌면 왜 바뀌었는지
        # 알 수 없다(#43·#123).
        lab = (f"{_split_note} → 전 기간 {int(avg):,}주 기준"
               if same else f"{lab} · {_split_note}")
    return avg, lab, len(got)


# 시총과 발행주식수의 **모집단이 다를 때** 무엇을 시총으로 삼을지.
# ⚠️ 적용 범위는 호출부가 정한다(#256, 2026-08-27): 이중상장(HK/CN_A)만
# 거래 클래스로 재계산하고, 미국 등은 **원천(전 클래스) 시총 유지** —
# 사용자 "야후나 구글도 전 클래스로 보여주지? 맞으면 미국도 전 클래스로".
# 야후의 EPS·순이익·배수가 전 클래스 기준이라 급이 맞는 쪽도 전 클래스다.
# 사용자 2026-08-24 (BYD 1211.HK) 결정: **거래되는 클래스(H주) 기준**.
#   yfinance `marketCap` = A주+H주 전체(HK$8,479억)
#   yfinance `sharesOutstanding` = H주만(3,683,400,000)
# 그래서 `시총 ÷ 현재가` 가 발행주식수와 2.5배 어긋났고, 헤더(네이버 H주
# 시총 3,431억)와 동종비교표(전 클래스 8,493억)가 같은 회사를 다르게 적었다.
MULTI_CLASS_TOL = 0.2


def listed_market_cap(price, mcap, shares, tol: float = MULTI_CLASS_TOL) -> tuple:
    """(시가총액, 사유) — 어긋나면 **현재가 × 발행주식수**로 재계산.

    단일 클래스 종목에서는 둘이 같으므로 **아무것도 바뀌지 않는다**(no-op).
    복수 클래스·복수 상장에서만 발동해 화면 전체를 한 모집단으로 맞춘다.

    ⚠️ 재료가 없으면 원래 값을 그대로 둔다 — 지어내지 않는다.
    ⚠️ 이 판정은 '어느 쪽이 최신인가'를 모른다(#190) — 발행주식수가 낡은
    시장에서는 거래소 등록 주식수가 먼저다(`resolve` 가 그걸 맡는다).
    """
    px, mc, sh = _num(price), _num(mcap), _num(shares)
    if not (px and px > 0 and sh and sh > 0):
        return mc, ""
    if not mc or mc <= 0:
        return px * sh, "현재가 × 발행주식수"
    if abs((mc / px) / sh - 1.0) <= tol:
        return mc, ""
    return px * sh, (f"거래 클래스 기준 재계산(원천 시총 {mc:,.0f} 은 "
                     f"{(mc / px) / sh:.1f}배 모집단)")
