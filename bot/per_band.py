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


def _months_between(a: str, b: str) -> int | None:
    """'YYYY-MM' 기준 개월 차. 파싱 실패는 None."""
    try:
        return ((int(b[:4]) - int(a[:4])) * 12
                + (int(b[5:7]) - int(a[5:7])))
    except (ValueError, IndexError):
        return None


def ttm_eps_series(eps_rows: list | None) -> list[tuple[str, float]]:
    """[(기간, 분기EPS)] → [(기간, TTM EPS)]. 4분기가 모이는 시점부터.

    ⚠️ **기간 오름차순**을 강제한다 — 원천이 최신부터 주는 일이 잦은데
    그대로 굴리면 TTM 이 미래를 포함한다(조용히 틀린다).

    ⚠️ **연속한 4분기일 때만** 더한다(2026-08-22 MSFT 실측). 시계열에 구멍이
    있으면 '최근 4개 가용 분기'는 12개월이 아니다 — MSFT 는 결산분기(6월)가
    빠져 15개월을 더하고 있었고, 화면의 산수는 전부 맞는데 값만 틀렸다.
    구멍이 있으면 그 시점은 **만들지 않는다**(빈칸이 틀린 값보다 낫다, #29).
    """
    rows = [(str(p), _f(v)) for p, v in (eps_rows or [])]
    rows = [(p, v) for p, v in rows if p and v is not None]
    rows.sort(key=lambda x: x[0])
    out: list[tuple[str, float]] = []
    for i in range(3, len(rows)):
        win = rows[i - 3:i + 1]
        span = _months_between(win[0][0], win[-1][0])
        if span is None or not (8 <= span <= 10):   # 4분기 = 끝점 간 9개월
            log.debug("per_band: TTM 건너뜀(%s~%s = %s개월)",
                      win[0][0], win[-1][0], span)
            continue
        out.append((win[-1][0], sum(v for _p, v in win)))
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


def _without_outliers(rows: list, min_points: int) -> tuple[list, list]:
    """(밴드에 쓸 배수, 뺀 배수). 뺄 근거는 **분모가 무너진 구간**이다.

    ⚠️ PER 은 순이익이 0 에 가까워지면 **발산**한다(2026-08-22 MKSI: 최고
    1,073.25x — 중상 36.78x 의 29배). 한 점이 '최고'를 통째로 망가뜨리면
    밴드가 valuation 도구로 쓸모없다.

    ⚠️ **처음엔 IQR 3배 울타리로 걸렀는데 그건 원인이 아니라 증상이었다**
    (2026-08-22 Yageo 2327.TW): 배수가 크다는 이유로 걸러 정당한 재평가
    구간까지 잘랐고, 그 바람에 **현재 PER(37.31)이 밴드 최고(29.86)보다
    높아졌다** — 밴드가 '지금'을 못 담으면 존재 이유가 없다. Yageo 의 EPS 는
    오히려 최고치(중앙값의 1.3배)라 발산과는 무관했다.
    그래서 판정을 **EPS 가 제 중앙값 대비 얼마나 무너졌는가**로 바꾼다 —
    발산의 진짜 원인이다. 배수가 높아도 이익이 멀쩡하면 그건 관측이다.

    ⚠️ EPS 를 모르는 원천(KR FnGuide 는 밴드선만 준다)은 **판정하지 않는다**
    — 알 수 없는 걸 추정해 지우지 않는다. FnGuide 는 정의 불가 구간을 이미
    0 으로 보내고 우리가 그 행을 뺀다(#43 별도 경로).
    """
    vals = [r[3] for r in (rows or []) if r and r[3] is not None]
    epss = [r[2] for r in (rows or []) if r and r[3] is not None]
    if len(vals) < max(min_points, 4) or any(e is None or e <= 0 for e in epss):
        return sorted(vals), []
    med = sorted(epss)[len(epss) // 2]
    if med <= 0:
        return sorted(vals), []
    kept = [v for v, e in zip(vals, epss) if e >= med * _EPS_COLLAPSE]
    if len(kept) < min_points:           # 다 버리면 밴드가 없다 — 그대로 둔다
        return sorted(vals), []
    drop = [v for v, e in zip(vals, epss) if e < med * _EPS_COLLAPSE]
    return sorted(kept), sorted(drop)


# EPS 가 제 중앙값의 이 비율 밑으로 떨어지면 '분모 붕괴'로 본다. 1/5 은
# 분기 하나가 거의 이익을 못 낸 수준이라 그 시점 배수는 valuation 이 아니라
# 산술 결과다. 정상 재평가(2~3배)는 여기 안 걸린다.
_EPS_COLLAPSE = 0.2


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
          annual: bool = False, min_points: int = 4,
          band_years: int = 5) -> dict | None:
    """가격·EPS 이력 → {"rows", "bands", "eps_now", "n"} 또는 None.

    `annual=True` 면 `eps_rows` 가 이미 **연간 EPS**라 TTM 합산을 건너뛴다.
    `min_points` 미만이면 **만들지 않는다** — 점 두세 개로 그린 밴드는
    '최고/최저'라는 이름값을 못 한다(빈칸이 틀린 라벨보다 낫다, #29).

    ⚠️ **밴드는 최근 `band_years` 년만** 본다(사용자 2026-08-22: "history 는
    10년으로 그대로놔두는데 PER BAND 는 5년치만 해줘. 10년으로 하니까 너무
    차이가 크네"). 이력(`rows`)은 전 구간 그대로 — 표와 차트는 길게 보고
    밴드만 좁힌다. 요약도 5년이라 이제 **두 창이 같아진다**(예전엔 요약
    '5년 최저 22.20' 옆에 밴드 '최저 19.18' 이 놓여 한쪽이 틀린 것처럼
    읽혔다, #34). 창은 **날짜로** 자른다 — 위치로 자르면 관측이 성길 때
    'N년'이 거짓말이 된다(#29).
    """
    if annual:
        ttm = [(str(p), _f(v)) for p, v in (eps_rows or [])]
        ttm = sorted([(p, v) for p, v in ttm if p and v is not None])
    else:
        ttm = ttm_eps_series(eps_rows)
    return assemble(per_series(prices, ttm), min_points=min_points,
                    band_years=band_years)


def assemble(rows: list | None, *, min_points: int = 4,
             band_years: int = 5) -> dict | None:
    """[(기간, 주가, EPS, 배수)] → 표 payload. **밴드·요약 조립의 단일 출처**.

    ⚠️ 원천마다 rows 를 만드는 법은 다르지만(EPS 합산 · 원천이 준 PER 그대로)
    거기서 밴드를 뽑는 규칙은 하나여야 한다 — 두 벌로 적으면 시장마다 밴드
    정의가 갈린다(#38).
    """
    rows = [r for r in (rows or []) if r and r[3] is not None]
    if len(rows) < min_points:
        return None
    cut = _ymd_minus_years(rows[-1][0][:10], band_years)
    win = [r for r in rows if r[0][:10] >= cut]
    if len(win) < min_points:            # 5년이 얇으면 전 구간으로 되돌린다
        win = rows
    kept, dropped = _without_outliers(win, min_points)
    vals = sorted(kept)
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
            "bands": bands, "eps_now": round(eps_now, 4), "n": len(rows),
            # 밴드가 실제로 본 창 — 화면이 라벨에 쓴다(#34 같은 주제어라도
            # 기준이 다르면 라벨로 구분). 이력 전체와 다를 수 있다.
            "band_n": len(vals), "band_from": win[0][0][:10],
            "band_to": win[-1][0][:10],
            # 밴드에서 뺀 이상치 — **왜** 뺐는지 화면이 말한다(#43).
            "band_dropped": dropped}


# ── 원천 조립 ────────────────────────────────────────────────────────────────
# 시장별로 **재료의 길이**가 다르다. 이름으로 분기하지 않고 표에서 고른다(#24).
#   US/ADR : EDGAR companyconcept (희석 EPS 10년) — 공식·무료·무키
#   그 외  : yfinance 분기 손익(8분기 ≈ TTM 5점) + 연간(4년)
# 어느 재료를 썼는지는 **결과에 실어** 화면이 밝히게 한다(#43 기준 미표기 금지).
_SRC_LABEL = {
    "edgar": "SEC EDGAR(희석 EPS) · 가격 yfinance",
    "finmind": "FinMind TaiwanStockPER(일별 PER, 월말 표본) · 가격 yfinance",
    "akshare": "AKShare stock_a_indicator_lg(일별 PER, 월말 표본) · 가격 yfinance",
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
               years: int = 10) -> tuple[dict | None, str | None]:
    """티커 → (PER 밴드 payload, 사유). 재료가 모자라면 (None, 사유).

    반환에 `source`(사람이 읽는 출처 문구)와 `basis`(edgar|yf-q|yf-a)를 싣는다.

    ⚠️ **사유를 같이 돌려준다** — 값만 꺼내고 사유를 버리면 화면이 "없는 거야?"
    에 답을 못 한다(#129 에서 같은 실수를 수주잔고에서 했다).
    """
    tk = (ticker or "").upper()
    try:
        from bot.market import detect_market
        mkt = detect_market(tk)
    except Exception:                                          # noqa: BLE001
        mkt = "US"
    px = _monthly_closes(tk, years)
    if not px:
        return None, "가격 이력을 받지 못했습니다."
    # 재무제표 통화 ≠ 거래 통화면 그 시점 환율로 EPS 를 환산한다(위 §통화 정합).
    fx_note, fx = None, None
    try:
        from bot.stock_snapshot import norm_cur as _nc
    except Exception:                                          # noqa: BLE001
        _nc = lambda c: (c or "").strip().upper()              # noqa: E731
    _fin = _nc((snap or {}).get("financial_currency"))
    _trd = _nc((snap or {}).get("currency"))
    if _fin and _trd and _fin != _trd:
        fx = _fx_monthly(_fin, _trd, years)
        if not fx:
            return None, (f"재무제표는 {_fin}, 주가는 {_trd} 로 통화가 달라"
                          f" 환율이 필요한데 받지 못했습니다 — 통화가 섞인"
                          f" 배수는 만들지 않습니다.")
        fx_note = f"EPS {_fin}→{_trd} 환산(그 시점 환율)"

    def _pack(res, basis):
        if not res:
            return None
        # ⚠️ 우리가 못 본 단위·통화 사고를 잡는 2차 그물 — TSM 은 통화 가드가
        # 1차지만, 같은 증상이 다른 경로로 또 올 수 있다(#54 대조 0건 금지).
        bad = implausible_reason(res.get("rows"))
        if bad:
            log.warning("per_band: %s 배수 비정상 — %s", tk, bad)
            _pack.reason = bad
            return None
        res["source"] = _SRC_LABEL[basis] + (f" · {fx_note}" if fx_note else "")
        res["basis"] = basis
        # 화면이 라벨을 이 값에서 만든다 — 빠지면 렌더 기본값에 기대게 되고,
        # 그 기본값이 언젠가 바뀌면 조용히 잘못된 지표명이 찍힌다(#55).
        res["kind"] = "PER"
        res["market"] = mkt
        res["years"] = years
        # 현재 PER 은 **라이브 시세**로(사용자 2026-08-22 "PER 은 주가에 따라
        # 매일 바뀌잖아"). 마지막 행의 주가는 월봉이라 최대 한 달 낡았다.
        res["price_now"] = live_price(tk, (res.get("rows") or [{}])[-1].get("price"))
        res["summary"] = summary(res.get("rows"), years=5,
                                 price_now=res["price_now"])
        # 밴드 **차트**도 같은 rows 에서 만든다(사용자 2026-08-22 "외국종목도
        # PER 밴드차트 만들수 있으면 만들어줘"). 표와 한 재료라 갈라질 수 없다.
        res["chart"] = chart_block(res, px)
        res["csym"] = currency_symbol(tk)
        return res

    _pack.reason = None

    def _conv(rows):
        """통화가 다르면 환산해서 준다(같으면 그대로)."""
        return convert_eps(rows, fx) if fx else rows

    if mkt == "US":
        try:
            from bot.edgar_eps import eps_history
            h = eps_history(tk, years=years) or {}
        except Exception as exc:                               # noqa: BLE001
            log.debug("per_band: EDGAR 실패 %s: %s", tk, exc)
            h = {}
        got = _pack(build(px, _conv(h.get("quarterly"))), "edgar")
        if got:
            return got, None
    # 원천이 PER 을 **직접** 주는 시장 — 우리가 EPS 로 만들 필요가 없다.
    _direct = {"TW": (tw_per_rows, "finmind"), "CN_A": (cn_per_rows, "akshare")}
    if mkt in _direct:
        _fn, _basis = _direct[mkt]
        got = _pack(assemble(_fn(tk, px, years)), _basis)
        if got:
            return got, None
    # 폴백 — 분기(TTM) 먼저, 그것도 모자라면 연간.
    q = _eps_rows_from_snapshot(snap, "quarterly")
    got = _pack(build(px, _conv(q)), "yf-q")
    if got:
        return got, None
    a = _eps_rows_from_snapshot(snap, "annual")
    got = _pack(build(px, _conv(a), annual=True), "yf-a")
    if got:
        return got, None
    return None, (_pack.reason
                  or "EPS 이력이 부족해 PER 밴드를 만들지 못했습니다.")


# ── 요약(최근 N년 평균·최저·최고 + 현재 PER) ────────────────────────────────
# 사용자 2026-08-22: "5년 평균과 최저점, 최고점을 추가해줘. 여기 위에 …
# 그리고 PER 은 주가에 따라 매일 바뀌잖아. 그게 고려된거 맞지?"
#
# ⚠️ **안 되고 있었다.** 표의 마지막 행은 EPS 기간(분기)에 붙은 **월봉** 종가라
# 최대 한 달 낡았고, KR 은 FnGuide 밴드를 12시간 캐시로 받는다. 그래서 현재
# PER 은 **라이브 시세**로 다시 만든다 — 못 받으면 마지막 관측으로 되돌아가되
# 어느 쪽인지 `per_now_basis` 로 밝힌다(침묵이 최악, #43).
#
# ⚠️ PER 은 주가에 **선형**이다(PER = 주가 × k). 원천별로 k 를 따로 적을 수도
# 있지만(비-KR 1÷TTM EPS · KR 최고배수÷밴드선) 그러면 두 벌이 되어 언젠가
# 갈라진다 — **표의 마지막 행**에서 `k = PER ÷ 주가` 로 만든다(#33 파생 칸은
# 화면의 다른 칸에서, #38 산식은 한 곳). 그러면 현재 PER 이 정의상 이력 행과
# 같은 기준이고, 사용자가 눈으로 나눠 봐도 맞는다.


def _ymd_minus_years(ymd: str, years: int) -> str:
    """'YYYY-MM-DD' − N년. 2/29 는 2/28 로 내린다."""
    y, m, d = (int(x) for x in ymd[:10].split("-"))
    y -= years
    if m == 2 and d == 29:
        d = 28
    return f"{y:04d}-{m:02d}-{d:02d}"


def summary(rows: list | None, *, years: int = 5,
            price_now=None) -> dict | None:
    """PER 이력 → {"avg","min","max","n","from","to","span_years",
    "per_now","per_now_basis"} 또는 None(관측 부족).

    ⚠️ 창은 **날짜로** 자른다 — 위치(`rows[-60:]`)로 자르면 관측이 성길 때
    '5년'이 거짓말이 된다(#29). 실제로 걸린 구간을 `from`/`to` 로 돌려주고
    화면이 그걸 라벨로 쓴다(요청한 5년보다 짧으면 짧다고 적힌다).
    """
    pts, px_by_period = [], {}
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        p, v = str(r.get("period") or "")[:10], _f(r.get("per"))
        if len(p) == 10 and v is not None and v > 0:
            pts.append((p, v))
            px_by_period[p] = _f(r.get("price"))
    if not pts:
        return None
    # ⚠️ 원천이 최신부터 줄 수 있다 — **정렬한 뒤**의 마지막이 최신이다
    # (문서 순서의 마지막을 쓰면 조용히 옛 행을 '현재'로 삼는다).
    pts.sort()
    last_per = pts[-1][1]
    last_px = px_by_period.get(pts[-1][0])
    cut = _ymd_minus_years(pts[-1][0], years)
    win = [(p, v) for p, v in pts if p >= cut]
    if len(win) < 4:                 # 점 두세 개짜리 '평균'은 이름값을 못 한다
        return None
    vals = [v for _p, v in win]
    y0, y1 = win[0][0], win[-1][0]
    span = (int(y1[:4]) - int(y0[:4])) + (int(y1[5:7]) - int(y0[5:7])) / 12.0
    # PER = 주가 × k. k 는 **표의 마지막 행**에서 직접 만든다 — 원천별로 따로
    # 적으면(비-KR 1÷EPS · KR 최고배수÷밴드선) 언젠가 갈라지고, 사용자가 눈으로
    # 나눠 봤을 때 이력 행과 안 맞는다(#33·#38).
    k = (last_per / last_px) if (last_px and last_px > 0 and last_per) else None
    px = _f(price_now)
    if k and k > 0 and px and px > 0:
        per_now, basis = round(px * k, 2), "live"
    else:
        per_now, basis = round(win[-1][1], 2), "last"
    return {"avg": round(sum(vals) / len(vals), 2),
            "min": round(min(vals), 2), "max": round(max(vals), 2),
            "n": len(win), "from": y0, "to": y1,
            "span_years": round(span, 1), "want_years": years,
            "per_now": per_now, "per_now_basis": basis,
            "price_now": round(px, 2) if basis == "live" else None}


def live_price(ticker: str, last_obs=None) -> float | None:
    """현재가(실시간). 실패·이상치면 None → 표가 마지막 관측으로 되돌아간다.

    ⚠️ 검증 기준(`last_obs`)이 **월봉**이라 최대 한 달 낡았다. 그래서
    `chart_data._validate_live_price` 의 일일밴드(KR ±35%)를 그대로 쓰면
    정당한 한 달 등락을 reject 한다 — 여기서 거를 대상은 일일 글리치가 아니라
    **통화 뒤섞임·분할 미조정** 같은 자릿수 사고이므로 0.2~5배로 넓게 본다.
    """
    try:
        from bot.market import detect_market
        mkt = detect_market(ticker)
    except Exception:                                          # noqa: BLE001
        mkt = "US"
    q = None
    try:
        if mkt == "KR":
            from bot.naver_quote import fetch_kr_quote as _q
        elif mkt == "TW":
            from bot.tw_quote import fetch_tw_quote as _q
        else:
            from bot.world_quote import fetch_world_quote as _q
        q = _q(ticker)
    except Exception as exc:                                   # noqa: BLE001
        log.debug("per_band: 현재가 실패 %s: %s", ticker, exc)
        return None
    px = _f((q or {}).get("price"))
    if px is None or px <= 0:
        return None
    ref = _f(last_obs)
    if ref and ref > 0 and not (0.2 <= px / ref <= 5.0):
        log.warning("per_band: 현재가 이상치 %s: %s (마지막 관측 %s) — 무시",
                    ticker, px, ref)
        return None
    return px


# ── 밴드 **차트** (사용자 2026-08-22 "외국종목도 PER 밴드차트 만들수 있으면") ──
# FnGuide 는 KR 만 밴드선을 준다. 비-KR 은 재료(주가·TTM EPS)가 이미 표에 있고
# 밴드선의 정의가 곧 `배수 × 그 시점 EPS` 이므로 **표에서 그대로 만든다** —
# 표와 차트가 같은 rows 에서 나오므로 둘이 갈라질 수 없다(#38).
#
# ⚠️ 화면의 `drawBand` 가 FnGuide 모양을 그대로 먹으므로 **그 모양으로** 낸다
# (렌더러를 두 벌로 만들면 축·색·갭 규약이 갈라진다). PBR 은 하지 않는다 —
# BPS 이력을 안 받고 있고, 사용자가 "PBR 은 안해도 돼" 라고 했다.


def _ms(ymd: str) -> int | None:
    """'YYYY-MM-DD' → epoch ms(UTC). drawBand 가 ms 축을 쓴다."""
    import datetime as _dt
    try:
        d = _dt.date(int(ymd[:4]), int(ymd[5:7]), int(ymd[8:10]))
    except (ValueError, IndexError):
        return None
    return int(_dt.datetime(d.year, d.month, d.day,
                            tzinfo=_dt.timezone.utc).timestamp() * 1000)


def chart_block(tbl: dict | None, prices: list | None = None) -> dict | None:
    """표 payload(+월봉 가격) → FnGuide 모양 밴드차트 블록. 재료가 없으면 None.

    밴드선(t) = 배수 × TTM EPS(t) = '그 배수였다면 그때 주가가 얼마'.

    ⚠️ `prices`(월봉)를 주면 **가격을 그 해상도로** 그린다. 표의 rows 만 쓰면
    EPS 기간마다 한 점이라 10년이 40점뿐이고, 분기 사이 등락이 통째로 사라진다
    (FnGuide 는 월 해상도다 — 같은 화면에서 국내만 촘촘하면 안 된다).
    그 시점 EPS 는 **직전 확정 분기**를 계단으로 쓴다 — 미래 EPS 를 끌어오면
    그때 존재하지 않던 밴드가 된다(#28 빈티지 오염).

    ⚠️ EPS ≤ 0 인 시점은 **선을 잇지 않는다**(None) — 적자 구간의 PER 은 음수·
    발산이라 0 으로 이으면 축이 통째로 망가진다(FnGuide 도 그 구간을 0 으로
    보내고 화면이 갭 처리한다 — 같은 규약을 지킨다).
    """
    rows = (tbl or {}).get("rows") or []
    bands = (tbl or {}).get("bands") or []
    if len(rows) < 4 or len(bands) < 4:
        return None
    # 표의 rows 가 곧 TTM EPS 시계열이다(기간 → 그 기간의 TTM EPS).
    ttm = sorted((str(r.get("period") or "")[:10], _f(r.get("eps")))
                 for r in rows if str(r.get("period") or ""))
    src = ([(str(d)[:10], _f(v)) for d, v in prices] if prices
           else [(str(r.get("period") or "")[:10], _f(r.get("price")))
                 for r in rows])
    # 첫 EPS 기간보다 앞선 주가는 **버린다** — 밴드가 없는 구간이 길게 붙으면
    # 정작 볼 구간이 오른쪽 끝으로 짜부라진다(가격 이력이 EPS 보다 길다).
    first = ttm[0][0] if ttm else ""
    pts = []
    for d, px in sorted(src):
        ms = _ms(d)
        if ms is None or px is None or d < first:
            continue
        e = None
        for p, v in ttm:                # 직전 확정 분기(계단)
            if p > d:
                break
            e = v
        pts.append((ms, px, e))
    if len(pts) < 4:
        return None
    mult = [_f(b.get("mult")) for b in bands[:4]]
    if not any(m for m in mult):
        return None
    return {"mult": [m or 0.0 for m in mult],
            "price": [[ms, px] for ms, px, _e in pts],
            "bands": [[[ms, (round(m * e, 4) if (m and e and e > 0) else None)]
                       for ms, _px, e in pts] for m in mult]}


def currency_symbol(ticker: str) -> str:
    """밴드차트 y축 통화 기호. 시장 설정 **단일 출처**에서 읽는다(#38) —
    화면에 '₩' 를 박아 두면 해외 종목 축이 원화로 거짓말한다."""
    try:
        from bot.market import MARKET_CONFIG, detect_market
        return MARKET_CONFIG[detect_market(ticker)].get("currency_symbol") or "$"
    except Exception:                                          # noqa: BLE001
        return "$"


# ── 통화 정합 (사용자 2026-08-22 TSM "PER 도 너무 낮고") ─────────────────────
# ⚠️ **재무제표 통화 ≠ 거래 통화**인 종목이 있다. TSM 실측: yfinance 가 주는
# 주당순이익이 NT$327.35(ADR 주당, 보통주×5)인데 주가는 US$302.33 이라 그냥
# 나누면 PER 0.92 가 나왔다 — 실제로는 ≈28 이다. ADR(TSM·ASML·BABA…) 에서
# 흔하고, 화면은 "너무 낮다"로만 보인다(#34 의 통화판, #88 부호 규약의 사촌).
#
# 처방: 두 통화가 다르면 **그 시점 환율로 EPS 를 환산**한다(과거 PER 은 실제로
# 환율에 의존했으므로 이게 근사가 아니라 정의다). 환율을 못 받으면 밴드를
# **만들지 않는다** — 통화가 섞인 배수는 빈칸보다 나쁘다(#32).
#
# ⚠️ 거래통화를 접미사로 **추정**한 경우엔 비교하지 않는다 — 미등록 접미사가
# USD 로 떨어져 멀쩡한 종목을 막는다(대시보드 동종비교의 같은 교훈).


def _fx_one(sym: str, years: int) -> list[tuple[str, float]]:
    """한 환율 심볼의 월 종가. 실패는 빈 리스트."""
    try:
        from bot.chart_data import fetch_chart_payload
        pay = fetch_chart_payload(sym, interval="1mo",
                                  period="max" if years >= 10 else "5y",
                                  lite=True) or {}
    except Exception as exc:                                   # noqa: BLE001
        log.debug("per_band: 환율 실패 %s: %s", sym, exc)
        return []
    ts, cl = pay.get("times") or [], pay.get("close") or []
    return sorted((str(t)[:10], float(c)) for t, c in zip(ts, cl)
                  if c is not None and float(c) > 0)


def _fx_monthly(from_cur: str, to_cur: str, years: int) -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, from→to 환율)] 월 해상도. 실패는 빈 리스트.

    ⚠️ 야후는 쌍에 따라 **한 방향만** 있다(`USDTWD=X` 는 있는데 `TWDUSD=X`
    는 비는 식). 정방향이 비면 **역방향을 받아 뒤집는다** — 한 방향만 보고
    포기하면 멀쩡한 종목이 통째로 밴드를 잃는다. 폴백은 로그로 알린다(#42a).
    """
    if not from_cur or not to_cur or from_cur == to_cur:
        return []
    fwd = _fx_one(f"{from_cur}{to_cur}=X", years)
    if fwd:
        return fwd
    inv = _fx_one(f"{to_cur}{from_cur}=X", years)
    if inv:
        log.info("per_band: 환율 역방향 사용 %s%s=X → 역수", to_cur, from_cur)
        return [(d, 1.0 / r) for d, r in inv if r]
    return []


def convert_eps(eps_rows: list | None, fx: list | None) -> list:
    """EPS 행을 **그 기간의 환율**로 환산. 환율이 없는 기간은 **버린다**.

    ⚠️ 현재 환율 하나로 전 기간을 환산하면 과거 PER 이 그때 존재하지 않던
    값이 된다(#28 빈티지 오염). 기간 **이하의 마지막** 환율을 쓴다.
    """
    rates = sorted((str(d)[:10], _f(v)) for d, v in (fx or [])
                   if _f(v) and _f(v) > 0)
    if not rates:
        return []
    out = []
    for p, v in (eps_rows or []):
        d, val = str(p)[:10], _f(v)
        if not d or val is None:
            continue
        prior = [r for dd, r in rates if dd <= d]
        if not prior:
            continue                     # 그 시점 환율을 모른다 — 버린다
        out.append((d, val * prior[-1]))
    return out


# PER 이 이 범위를 벗어나면 **단위·통화 사고**로 본다. 정상 기업의 PER 이
# 1배 미만이거나 500배를 넘는 일은 드물고, 그런 값이 밴드에 섞이면 최고·최저가
# 통째로 망가진다. 통화 가드가 1차, 이건 우리가 못 본 실패모드를 잡는 2차다.
_PER_SANE = (1.0, 500.0)


def implausible_reason(rows: list | None) -> str | None:
    """PER 시리즈가 단위·통화 사고로 보이면 사유, 아니면 None."""
    vals = sorted(v for v in ((_f(r.get("per")) if isinstance(r, dict) else None)
                              for r in (rows or [])) if v is not None)
    if not vals:
        return None
    med = vals[len(vals) // 2]
    if med < _PER_SANE[0]:
        return (f"계산된 PER 중앙값이 {med:.2f} 배로 비정상입니다 — 주가와 "
                f"주당순이익의 단위·통화가 어긋난 것으로 보여 표시하지 않습니다.")
    if med > _PER_SANE[1]:
        return (f"계산된 PER 중앙값이 {med:,.0f} 배로 비정상입니다 — 주가와 "
                f"주당순이익의 단위·통화가 어긋난 것으로 보여 표시하지 않습니다.")
    return None


# ── 대만: 원천이 PER 을 **직접** 준다 (사용자 2026-08-22 TSMC) ───────────────
# ⚠️ 사용자 "기간이 짧은건 야후때문에 어쩔수 없는거야? 그리고 분기로는 안되는
# 거야? 연단위밖에 안돼?" — yfinance 는 분기 손익을 4~5개만 줘서 TTM 이 1~2점
# 뿐이라 밴드 최소치(4점)를 못 넘고, 연간도 4개년뿐이다. 그래서 TSMC 가 연 4점
# 짜리 밴드였다.
# 대만은 FinMind `TaiwanStockPER` 가 **일별 PER/PBR** 을 준다(무료·무키) —
# 우리가 EPS 로 만들 필요 없이 원천이 완제품을 준다. 월말로 추려 쓴다.
# ⚠️ EPS 칸은 `주가 ÷ PER` 로 되짚는다 — 화면의 다른 칸에서 파생시켜야
# 사용자가 눈으로 나눠 봐도 맞는다(#33).


def _month_end_samples(rows: list) -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, 값)] → 달마다 **마지막 관측** 하나. 일별을 월로 추린다."""
    by_month: dict[str, tuple[str, float]] = {}
    for d, v in sorted(rows):
        by_month[str(d)[:7]] = (str(d)[:10], float(v))
    return [by_month[m] for m in sorted(by_month)]


def rows_from_per_history(pts: list | None, prices: list | None) -> list:
    """[(YYYY-MM-DD, PER)] + 월봉 주가 → [(기간, 주가, EPS, PER)].

    **원천이 PER 을 직접 주는 시장의 공용 조립기**(대만 FinMind · 중국
    AKShare). 시장마다 복제하면 표본 추리기·주가 조인 규약이 갈라진다(#38).

    주가는 **우리 월봉**에서 그 시점 이하 마지막 관측을 쓴다(#28) — 두 원천을
    섞되 EPS 를 `주가 ÷ PER` 로 되짚어 화면 세 칸이 서로 맞게 한다(#33).
    """
    pts = [(str(d)[:10], _f(v)) for d, v in (pts or [])]
    pts = [(d, v) for d, v in pts if len(d) == 10 and v is not None and v > 0]
    if not pts:
        return []
    px = sorted((str(d)[:10], _f(v)) for d, v in (prices or [])
                if _f(v) and _f(v) > 0)
    out = []
    for d, per in _month_end_samples(pts):
        prior = [v for dd, v in px if dd <= d]
        if not prior:
            continue
        price = prior[-1]
        out.append((d, price, round(price / per, 4), round(per, 2)))
    return out


def tw_per_rows(ticker: str, prices: list | None, years: int = 10) -> list:
    """대만 — FinMind `TaiwanStockPER` 일별 PER."""
    try:
        from bot.finmind_client import fetch_per_pbr
        raw = fetch_per_pbr(ticker, days=365 * years + 40) or []
    except Exception as exc:                                   # noqa: BLE001
        log.debug("per_band: FinMind PER 실패 %s: %s", ticker, exc)
        return []
    pts = [(r.get("date"), r.get("PER")) for r in raw if isinstance(r, dict)]
    return rows_from_per_history(pts, prices)


def cn_per_rows(ticker: str, prices: list | None, years: int = 10) -> list:
    """중국 A주 — AKShare `stock_a_indicator_lg` 일별 PER.

    ⚠️ 이 호출은 스크리너가 **이미 쓰고 있었다**(`get_valuation`) — 다만 마지막
    한 줄만 쓰고 이력을 버렸다. 재료를 모아 계산하기 전에 **원천이 그 값을
    직접 주는지** 확인할 것(#141).
    """
    try:
        from bot.akshare_client import get_akshare
        raw = get_akshare().per_history(ticker, years=years) or []
    except Exception as exc:                                   # noqa: BLE001
        log.debug("per_band: AKShare PER 실패 %s: %s", ticker, exc)
        return []
    return rows_from_per_history(raw, prices)
