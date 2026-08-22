"""PER 밴드 — **전 시장 공통** 계산기(가격 이력 + EPS 이력 → 밴드·표).

⚠️ 왜 자체계산인가(2026-08-22 조사). 외국 종목의 PER 밴드를 **완제품으로**
주는 무료 원천이 없다:
  · Yahoo Finance  — `trailingPE`/`forwardPE` **현재값만**, 과거 시계열 없음
  · FnGuide BandChart — `cmp_cd` 가 6자리 국내 종목코드라 KR 전용
  · Finviz / Alpha Vantage — 현재값만
  · Seeking Alpha / macrotrends / fullratio / stockcircle 등 — 과거 PER 을
    보여주지만 API 가 아니라 화면이고, 유료·로그인·ToS 문제가 있다
재료(가격·EPS)는 우리가 이미 받고 있으므로 **여기서 만든다**.

⚠️ 자체계산이 허용되는 자리인가(#32). "비교표에 자체계산을 넣지 말 것"의
경계는 **옆에 다른 출처가 놓이는가**다. 이 표는 한 종목의 단독 화면이라
같은 열에 남의 출처가 오지 않는다 — 규칙의 경계 안이다. 대신 표에 산식과
출처를 박아 무엇을 보고 있는지 밝힌다(#55).

정의:
  PER(t) = 주가(t) ÷ TTM EPS(t)
  TTM EPS(t) = 그 시점 **직전 4개 분기** 희석 EPS 합(연간만 있으면 그 해 EPS)
  밴드 = 관측된 PER 분포의 최고/중상/중하/최저 배수. 각 배수 × 현재 TTM EPS
        = 그 배수에서의 적정주가(FnGuide 밴드차트와 같은 읽는 법).

⚠️ EPS 가 0 이하인 시점은 **PER 을 만들지 않는다** — 적자 구간의 PER 은
음수이거나 무한대로 튀어 밴드를 통째로 망가뜨린다(빈칸이 낫다).
"""

from __future__ import annotations

import logging

log = logging.getLogger("bot.per_band")

# 밴드 4선의 분위수. FnGuide 는 VAL1=최고 … VAL4=최저 네 선을 쓴다 —
# 같은 읽는 법을 유지해 KR/비-KR 화면이 갈라지지 않게 한다(#38).
_BAND_Q = ((1.00, "최고"), (0.75, "중상"), (0.25, "중하"), (0.00, "최저"))


def _f(v):
    """숫자만 통과. NaN·문자열·bool 은 None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def ttm_eps_series(eps_rows: list | None) -> list[tuple[str, float]]:
    """[(기간, 분기EPS)] → [(기간, TTM EPS)]. 4분기가 모이는 시점부터.

    ⚠️ **기간 오름차순**을 강제한다 — 원천이 최신부터 주는 일이 잦은데
    그대로 굴리면 TTM 이 미래를 포함한다(조용히 틀린다).
    """
    rows = [(str(p), _f(v)) for p, v in (eps_rows or [])]
    rows = [(p, v) for p, v in rows if p and v is not None]
    rows.sort(key=lambda x: x[0])
    out: list[tuple[str, float]] = []
    for i in range(3, len(rows)):
        out.append((rows[i][0], sum(v for _p, v in rows[i - 3:i + 1])))
    return out


def per_series(prices: list | None, eps_ttm: list | None
               ) -> list[tuple[str, float, float, float]]:
    """[(기간, 주가, TTM EPS, PER)] — EPS 기간마다 **그 시점 주가**로.

    `prices` = [(YYYY-MM-DD, 종가)] (오름차순 아니어도 됨).
    ⚠️ 주가는 그 기간 **이하의 마지막 관측**을 쓴다 — 미래 주가를 끌어오면
    과거 PER 이 실제로 존재하지 않았던 값이 된다(#28 빈티지 오염).
    """
    px = sorted(((str(d), _f(v)) for d, v in (prices or [])
                 if _f(v) is not None), key=lambda x: x[0])
    out = []
    for p, e in (eps_ttm or []):
        if not e or e <= 0:
            continue                      # 적자 구간은 PER 을 만들지 않는다
        prior = [v for d, v in px if d <= str(p)]
        if not prior:
            continue
        price = prior[-1]
        out.append((str(p), price, float(e), price / float(e)))
    return out


def _quantile(sorted_vals: list[float], q: float) -> float:
    """선형보간 분위수. 표준편차가 아니라 **관측 분포**로 밴드를 잡는다."""
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def build(prices: list | None, eps_rows: list | None, *,
          annual: bool = False, min_points: int = 4) -> dict | None:
    """가격·EPS 이력 → {"rows", "bands", "eps_now", "n"} 또는 None.

    `annual=True` 면 `eps_rows` 가 이미 **연간 EPS**라 TTM 합산을 건너뛴다.
    `min_points` 미만이면 **만들지 않는다** — 점 두세 개로 그린 밴드는
    '최고/최저'라는 이름값을 못 한다(빈칸이 틀린 라벨보다 낫다, #29).
    """
    if annual:
        ttm = [(str(p), _f(v)) for p, v in (eps_rows or [])]
        ttm = sorted([(p, v) for p, v in ttm if p and v is not None])
    else:
        ttm = ttm_eps_series(eps_rows)
    rows = per_series(prices, ttm)
    if len(rows) < min_points:
        return None
    vals = sorted(r[3] for r in rows)
    eps_now = rows[-1][2]
    # ⚠️ '그 배수에서의 주가'는 **화면에 찍히는 배수**로 만든다. 원(raw)
    # 분위수로 만들면 사용자가 눈으로 곱했을 때 안 맞는다(22.73 × 10.2 =
    # 231.85 인데 231.8 로 찍힘) — 표가 거짓말하는 것이다(#33).
    bands = []
    for q, lab in _BAND_Q:
        m = round(_quantile(vals, q), 2)
        bands.append({"label": lab, "mult": m, "fair": round(m * eps_now, 2)})
    return {"rows": [{"period": p, "price": round(pr, 4),
                      "eps": round(e, 4), "per": round(v, 2)}
                     for p, pr, e, v in rows],
            "bands": bands, "eps_now": round(eps_now, 4), "n": len(rows)}


# ── 원천 조립 ────────────────────────────────────────────────────────────────
# 시장별로 **재료의 길이**가 다르다. 이름으로 분기하지 않고 표에서 고른다(#24).
#   US/ADR : EDGAR companyconcept (희석 EPS 10년) — 공식·무료·무키
#   그 외  : yfinance 분기 손익(8분기 ≈ TTM 5점) + 연간(4년)
# 어느 재료를 썼는지는 **결과에 실어** 화면이 밝히게 한다(#43 기준 미표기 금지).
_SRC_LABEL = {
    "edgar": "SEC EDGAR(희석 EPS) · 가격 yfinance",
    "yf-q": "yfinance 분기 손익(TTM) · 가격 yfinance",
    "yf-a": "yfinance 연간 손익 · 가격 yfinance",
}


def _monthly_closes(ticker: str, years: int) -> list[tuple[str, float]]:
    """월봉 종가 [(YYYY-MM-DD, close)]. 밴드는 월 해상도면 충분하다."""
    try:
        from bot.chart_data import fetch_chart_payload
        pay = fetch_chart_payload(ticker, interval="1mo",
                                  period="max" if years >= 10 else "5y",
                                  lite=True) or {}
    except Exception as exc:                                   # noqa: BLE001
        log.debug("per_band: 가격 이력 실패 %s: %s", ticker, exc)
        return []
    ts, cl = pay.get("times") or [], pay.get("close") or []
    return [(str(t), float(c)) for t, c in zip(ts, cl)
            if c is not None]


def _eps_rows_from_snapshot(snap: dict | None, kind: str) -> list:
    """스냅샷 손익계산서에서 EPS 행 — 추가 호출 0(이미 수집돼 있다)."""
    rows = (((snap or {}).get("financials") or {})
            .get("income_statement") or {}).get(kind) or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        v = r.get("Diluted EPS")
        if v is None:
            v = r.get("Basic EPS")
        if v is not None:
            out.append((str(r.get("period", ""))[:10], v))
    return out


def for_ticker(ticker: str, snap: dict | None = None,
               years: int = 10) -> dict | None:
    """티커 → PER 밴드 payload. 재료가 모자라면 None(화면이 섹션을 생략).

    반환에 `source`(사람이 읽는 출처 문구)와 `basis`(edgar|yf-q|yf-a)를 싣는다.
    """
    tk = (ticker or "").upper()
    try:
        from bot.market import detect_market
        mkt = detect_market(tk)
    except Exception:                                          # noqa: BLE001
        mkt = "US"
    px = _monthly_closes(tk, years)
    if not px:
        return None

    def _pack(res, basis):
        if not res:
            return None
        res["source"] = _SRC_LABEL[basis]
        res["basis"] = basis
        res["market"] = mkt
        res["years"] = years
        return res

    if mkt == "US":
        try:
            from bot.edgar_eps import eps_history
            h = eps_history(tk, years=years) or {}
        except Exception as exc:                               # noqa: BLE001
            log.debug("per_band: EDGAR 실패 %s: %s", tk, exc)
            h = {}
        got = _pack(build(px, h.get("quarterly")), "edgar")
        if got:
            return got
    # 폴백 — 분기(TTM) 먼저, 그것도 모자라면 연간.
    q = _eps_rows_from_snapshot(snap, "quarterly")
    got = _pack(build(px, q), "yf-q")
    if got:
        return got
    a = _eps_rows_from_snapshot(snap, "annual")
    return _pack(build(px, a, annual=True), "yf-a")
