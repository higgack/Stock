"""시장타이밍/브레드스 — 분산일(Distribution Day)·팔로우스루데이(FTD)·
크립토 레짐·매크로 크로스에셋 레짐 (2026-07-26).

claude-trading-skills(tradermonty/claude-trading-skills) 리뷰 3순위 갭 이식
— ibd-distribution-day-monitor·ftd-detector·crypto-regime-analyzer·
macro-regime-detector 개념. 전마켓(KR/US/JP/TW/CN_A/HK) 공통 원칙 — 지수만
시장별 교체(MARKET_INDICES, bot/market.py MARKET_CONFIG 의 broad_benchmark
재사용), FRED 보드(PPI/CPI/유동성)와는 다른 각도(시장타이밍 신호이지
물가·유동성 레벨이 아님). (최초 배치는 시간 제약으로 US/KR/JP 만 채웠던
스코프 갭 — 사용자 지적으로 2026-07-26 TW/CN_A/HK 확장.)

데이터 소스: 지수·ETF 는 bot/chart_data.fetch_chart_payload(yfinance, 무료)
재사용 — 신규 API 클라이언트 불요. 크립토만 CoinGecko 공개 API(무료·키리스)
직접 호출.

순수 로직(detect_*/classify_*)은 명시 히스토리로 단위테스트 가능 —
fetch_*는 I/O 래퍼로 분리(전 보드 공통 패턴, fred_boards.py 참조).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ── 분산일(Distribution Day, IBD 방식) ─────────────────────────────────────
_DD_MIN_DECLINE_PCT = -0.002      # -0.2%+ 하락(종가 기준)
_DD_EXPIRATION_SESSIONS = 25      # 이 나이(age_sessions) 초과 시 제외
_DD_INVALIDATION_GAIN_PCT = 0.05  # DD 종가 대비 +5% 고가 도달 시 무효화

_RISK_CAUTION_D25 = 3
_RISK_HIGH_D25 = 5
_RISK_HIGH_D15 = 3
_RISK_HIGH_D5 = 2
_RISK_SEVERE_D25 = 6
_RISK_SEVERE_D15 = 4


def detect_distribution_days(history_desc: list[dict]) -> list[dict]:
    """history_desc = 최신순(index 0=오늘) [{date, close, high, volume}].
    IBD 방식: 종가 -0.2%+ 하락 & 거래량 전일 대비 증가 = 분산일. age_sessions
    (=history 내 인덱스) 25 초과 → expired, DD 종가 대비 +5% 고가 도달(DD
    당일 제외, 그 이후 세션) → invalidated. 그 외 active. 반환은 DD 전체
    (active/expired/invalidated 상태 포함) — 필터는 count_active 가 담당."""
    records: list[dict] = []
    n = len(history_desc)
    for i in range(n - 1):
        today, yesterday = history_desc[i], history_desc[i + 1]
        c0, c1 = today.get("close"), yesterday.get("close")
        v0, v1 = today.get("volume"), yesterday.get("volume")
        if not c0 or not c1 or not v0 or not v1:
            continue
        pct = c0 / c1 - 1
        if pct <= _DD_MIN_DECLINE_PCT + 1e-12 and v0 > v1:
            records.append({"date": today.get("date"), "age_sessions": i,
                            "close": c0, "pct_change": pct, "status": "active"})
    for r in records:
        k = r["age_sessions"]
        if k > _DD_EXPIRATION_SESSIONS:
            r["status"] = "expired"
            continue
        inv_price = r["close"] * (1 + _DD_INVALIDATION_GAIN_PCT)
        # DD 당일(k) 제외, 그 이후(더 최신, index 0..k-1) 세션에서 고가 확인.
        for row in history_desc[:k]:
            h = row.get("high")
            if h is not None and h >= inv_price:
                r["status"] = "invalidated"
                break
    return records


def count_active(records: list[dict], max_age_sessions: int) -> int:
    """age_sessions <= max_age_sessions 인 active 레코드 수 — IBD 관례상
    'N elapsed sessions' 는 age 0..N 포함(직전 N거래일과 다름, 문서화)."""
    return sum(1 for r in records
              if r["status"] == "active" and r["age_sessions"] <= max_age_sessions)


def classify_risk(d5: int, d15: int, d25: int) -> str:
    """NORMAL/CAUTION/HIGH/SEVERE — IBD 스타일 분산일 클러스터 위험도."""
    if d25 >= _RISK_SEVERE_D25 or d15 >= _RISK_SEVERE_D15:
        return "SEVERE"
    if d25 >= _RISK_HIGH_D25 or d15 >= _RISK_HIGH_D15 or d5 >= _RISK_HIGH_D5:
        return "HIGH"
    if d25 >= _RISK_CAUTION_D25:
        return "CAUTION"
    return "NORMAL"


def distribution_day_summary(history_desc: list[dict]) -> dict:
    """→ {d5, d15, d25, risk_level, active_records}. 히스토리 부족(<26) 시
    d*=0/NORMAL(graceful, 크래시 대신 '판단보류' 취급)."""
    if len(history_desc) < 26:
        return {"d5": 0, "d15": 0, "d25": 0, "risk_level": "NORMAL", "active_records": []}
    records = detect_distribution_days(history_desc)
    d5 = count_active(records, 5)
    d15 = count_active(records, 15)
    d25 = count_active(records, 25)
    return {"d5": d5, "d15": d15, "d25": d25,
            "risk_level": classify_risk(d5, d15, d25),
            "active_records": [r for r in records if r["status"] == "active"]}


# ── 팔로우스루데이(Follow-Through Day, O'Neil 방식) ─────────────────────────
def detect_ftd(history_asc: list[dict]) -> dict:
    """history_asc = 오름차순(과거→최신) [{date, close, high, low, volume}].
    최근 40세션 창에서 3%+ 하락(3일+ 하락일) 스윙로우 탐색 → Day1(첫 상승일
    또는 당일 레인지 상위50% 마감) → Day2+ 무결성(Day1 저가 붕괴=실패) →
    Day4-10 FTD 조건(전일대비 +1.25%+ & 거래량 증가) 판정. 단일 패스
    간이판정 — 원본의 post-FTD 분산일·Power Trend 모니터링은 포함하지
    않음(문서화된 단순화, 별도 확장 가능)."""
    if len(history_asc) < 45:
        return {"state": "INSUFFICIENT_DATA"}
    window = history_asc[-41:]
    closes = [r["close"] for r in window]
    low_idx = min(range(len(closes)), key=lambda i: closes[i])
    recent_high = max(closes[:low_idx + 1])
    decline_pct = (closes[low_idx] / recent_high - 1) if recent_high else 0.0
    down_days = sum(1 for i in range(1, low_idx + 1) if closes[i] < closes[i - 1])
    if decline_pct > -0.03 or down_days < 3:
        return {"state": "NO_CORRECTION", "decline_pct": round(decline_pct * 100, 2)}
    if low_idx >= len(closes) - 1:
        # 오늘이 곧 최저가 — 하락이 아직 진행 중, 스윙로우 미확정(반등 전).
        return {"state": "CORRECTION", "note": "하락 진행중 — 스윙로우 미확정",
                "decline_pct": round(decline_pct * 100, 2)}
    swing_low = closes[low_idx]
    swing_low_date = window[low_idx]["date"]
    after = window[low_idx + 1:]
    prev_close = swing_low
    day1_idx = None
    for i, row in enumerate(after):
        if row["close"] < swing_low:
            return {"state": "NO_CORRECTION",
                    "note": "스윙로우 붕괴 — 새 저점 기준 재탐색 필요"}
        lo = row.get("low") if row.get("low") is not None else row["close"]
        hi = row.get("high") if row.get("high") is not None else row["close"]
        rng = hi - lo
        top_half = rng > 0 and (row["close"] - lo) / rng >= 0.5
        if row["close"] > prev_close or top_half:
            day1_idx = i
            break
        prev_close = row["close"]
    if day1_idx is None:
        return {"state": "CORRECTION", "swing_low": swing_low,
                "swing_low_date": swing_low_date}
    day1 = after[day1_idx]
    day1_low = day1.get("low") if day1.get("low") is not None else day1["close"]
    rest = after[day1_idx + 1:]
    prev_close = day1["close"]
    prev_volume = day1.get("volume")
    for day_num, row in enumerate(rest, start=2):
        if row["close"] < day1_low:
            return {"state": "RALLY_FAILED", "day1_date": day1["date"], "day": day_num}
        if day_num > 10:
            return {"state": "RALLY_ATTEMPT_EXPIRED", "day1_date": day1["date"]}
        if day_num >= 4:
            gain = (row["close"] / prev_close - 1) if prev_close else 0.0
            vol_up = (row.get("volume") or 0) > (prev_volume or 0)
            if gain >= 0.0125 and vol_up:
                prime = day_num <= 7
                base = 60 if prime else 50
                bonus = 20 if gain >= 0.02 else (10 if gain >= 0.015 else 0)
                return {"state": "FTD_CONFIRMED", "day": day_num,
                        "gain_pct": round(gain * 100, 2),
                        "window": "prime" if prime else "late",
                        "quality_score": min(100, base + bonus),
                        "ftd_date": row["date"]}
        prev_close = row["close"]
        prev_volume = row.get("volume")
    return {"state": "RALLY_ATTEMPT", "day1_date": day1["date"], "day": len(rest) + 1}


# ── 매크로 크로스에셋 레짐(ETF 비율 기반) ───────────────────────────────────
def classify_macro_regime(rsp_spy_yoy: float | None, curve_10y2y: float | None,
                          hyg_lqd_yoy: float | None, iwm_spy_yoy: float | None,
                          spy_tlt_yoy: float | None, xly_xlp_yoy: float | None) -> str:
    """크로스에셋 비율(YoY% 또는 스프레드) → Concentration/Broadening/
    Contraction/Inflationary/Transitional. 입력 부족(None 다수)이면
    Transitional(판단보류). 단순 다수결 휴리스틱 — 세부 가중치 없음(문서화)."""
    votes: list[str] = []
    if rsp_spy_yoy is not None:
        votes.append("Broadening" if rsp_spy_yoy > 0 else "Concentration")
    if iwm_spy_yoy is not None:
        votes.append("Broadening" if iwm_spy_yoy > 0 else "Concentration")
    if curve_10y2y is not None:
        votes.append("Contraction" if curve_10y2y < 0 else "Broadening")
    if hyg_lqd_yoy is not None:
        votes.append("Contraction" if hyg_lqd_yoy < 0 else "Broadening")
    if spy_tlt_yoy is not None:
        votes.append("Inflationary" if spy_tlt_yoy > 0 else "Contraction")
    if xly_xlp_yoy is not None:
        votes.append("Broadening" if xly_xlp_yoy > 0 else "Contraction")
    if not votes:
        return "Transitional"
    from collections import Counter
    top, n = Counter(votes).most_common(1)[0]
    return top if n > len(votes) / 2 else "Transitional"


# ── 크립토 레짐 스코어(CoinGecko 무료 공개 API) ─────────────────────────────
def crypto_regime_score(btc_price: float | None, btc_sma50: float | None,
                        btc_sma200: float | None, btc_dominance_pct: float | None,
                        drawdown_from_ath_pct: float | None,
                        momentum_30d_pct: float | None) -> dict:
    """6개 컴포넌트 중 확보된 것만으로 0-100 합성점수(100=risk-on). 컴포넌트
    부재는 그 항목만 생략(가중치 재분배) — 전체 실패 대신 graceful."""
    comps: dict[str, float] = {}
    if btc_price is not None and btc_sma50 is not None and btc_sma200 is not None:
        stack = (btc_price > btc_sma50 > btc_sma200)
        comps["trend"] = 100.0 if stack else (50.0 if btc_price > btc_sma200 else 0.0)
    if btc_dominance_pct is not None:
        # 도미넌스 하락(알트로 순환) = risk-on 신호로 해석(단순 휴리스틱).
        comps["dominance"] = max(0.0, min(100.0, 150.0 - btc_dominance_pct * 2))
    if drawdown_from_ath_pct is not None:
        comps["drawdown"] = max(0.0, min(100.0, 100.0 + drawdown_from_ath_pct))
    if momentum_30d_pct is not None:
        comps["momentum"] = max(0.0, min(100.0, 50.0 + momentum_30d_pct))
    if not comps:
        return {"score": None, "components": {}}
    score = sum(comps.values()) / len(comps)
    return {"score": round(score, 1), "components": comps}


# ── I/O 래퍼(fetch) ─────────────────────────────────────────────────────────
def fetch_index_history(ticker: str, days: int = 120) -> list[dict]:
    """지수/ETF 히스토리 [{date, close, high, low, volume}] 오름차순(과거→
    최신) — bot.chart_data.fetch_chart_payload 재사용(yfinance, 무료).
    실패 시 []."""
    try:
        from bot.chart_data import fetch_chart_payload
        period = "1y" if days > 60 else "3mo"
        p = fetch_chart_payload(ticker, interval="1d", period=period)
        if not p:
            return []
        times = p.get("times") or []
        closes = p.get("close") or []
        highs = p.get("high") or closes
        lows = p.get("low") or closes
        vols = p.get("volume") or []
        n = min(len(times), len(closes))
        out = []
        for i in range(n):
            out.append({"date": times[i], "close": closes[i],
                       "high": highs[i] if i < len(highs) else closes[i],
                       "low": lows[i] if i < len(lows) else closes[i],
                       "volume": vols[i] if i < len(vols) else None})
        return out[-days:]
    except Exception as exc:
        log.debug("market_timing: fetch_index_history(%s) failed: %s", ticker, exc)
        return []


# 시장별 대표지수 — universal(KR/US/JP/TW/CN_A/HK), 신규 시장 추가 시 이
# dict 만 확장. TW/CN_A/HK 는 지수 자체(^TWII/^SSEC/^HSI) 대신 bot/market.py
# MARKET_CONFIG 의 broad_benchmark ETF 티커를 그대로 재사용(단일 소스 유지
# — 캐노니컬 벤치마크가 거기서 바뀌면 여기도 같이 확인 필요). 원래 이 dict
# 는 "신규 시장 추가 시 확장" 전제로 설계됐는데 최초 배치(2026-07-26,
# claude-trading-skills 3순위 갭)가 시간 제약으로 US/KR/JP 만 채웠던 스코프
# 갭 — 사용자 지적으로 2026-07-26 확장.
MARKET_INDICES = {
    "US": [("^GSPC", "S&P 500"), ("^IXIC", "나스닥종합")],
    "KR": [("^KS11", "KOSPI")],
    "JP": [("^N225", "니케이225")],
    "TW": [("0050.TW", "TAIEX 50 (0050)")],
    "CN_A": [("510300.SS", "CSI 300 (510300)")],
    "HK": [("2800.HK", "Hang Seng (2800.HK)")],
}


def fetch_crypto_snapshot() -> dict:
    """CoinGecko 공개 API(무료·키리스) — BTC 가격/도미넌스/ATH 대비 낙폭.
    실패 시 빈 dict(graceful). SMA/모멘텀은 별도 히스토리 호출 필요라 여기선
    스냅샷 필드만(가격·도미넌스·ATH% 는 CoinGecko markets 엔드포인트 1콜로 충분)."""
    try:
        import requests
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "ids": "bitcoin"}, timeout=10)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return {}
        d = rows[0]
        return {
            "price": d.get("current_price"),
            "ath": d.get("ath"),
            "ath_change_pct": d.get("ath_change_percentage"),
            "market_cap": d.get("market_cap"),
            "price_change_30d_pct": None,   # markets 엔드포인트는 24h만 제공
        }
    except Exception as exc:
        log.debug("market_timing: fetch_crypto_snapshot failed: %s", exc)
        return {}


# ── 시장 폭(Market Breadth, 2026-07-26 사용자 추천 추가) ────────────────────
# 개별종목 breadth(MMTH 류, S&P500 500종목 전수 50/200일선 상회비율)는 yfinance
# 500콜이 비용/시간 커 이번 배치엔 미채택 — 11개 SPDR 섹터 ETF(유동성 최상위,
# GICS 11개 섹터 1:1 매핑) 의 자기 50/200일선 상회 비율로 근사. 개별종목
# breadth 보다 해상도는 낮지만(섹터 단위) '소수 대형주가 지수를 방어 중인지
# vs 전반적 상승인지' 판별에는 충분 — 이미 계산 중인 RSP/SPY(macro 레짐)와
# 상호보완(그쪽은 대형/소형 비율, 이쪽은 섹터 참여도). US 전용(SPDR 섹터
# ETF가 미국 고유 상품 — 데이터소스 공백, market gate 아님).
_BREADTH_SECTOR_ETFS_US = (
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLRE", "XLU", "XLC",
)


def sma(closes: list, period: int):
    """단순이동평균 마지막 값 — 순수함수. 데이터 부족(<period) 시 None."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def breadth_from_closes(sector_closes: dict) -> dict:
    """{ticker: closes(과거→최신 list)} → 섹터-레벨 breadth 비율. 순수함수
    (테스트용). 각 섹터가 자신의 50/200일 SMA 위에 있는지 비율(0-100%) —
    개별종목 A/D-line·MMTH 의 섹터 근사(모듈 상단 주석 참조). 데이터 부족한
    섹터는 그 지표에서 제외(counted 분모에서도 빠짐)."""
    above50 = above200 = counted50 = counted200 = 0
    for closes in sector_closes.values():
        if not closes:
            continue
        last = closes[-1]
        s50 = sma(closes, 50)
        if s50 is not None:
            counted50 += 1
            if last > s50:
                above50 += 1
        s200 = sma(closes, 200)
        if s200 is not None:
            counted200 += 1
            if last > s200:
                above200 += 1
    return {
        "pct_above_50dma": round(above50 / counted50 * 100, 1) if counted50 else None,
        "pct_above_200dma": round(above200 / counted200 * 100, 1) if counted200 else None,
        "n_sectors": len(sector_closes),
    }


def fetch_market_breadth(market: str = "US") -> dict:
    """market='US' 만 지원(SPDR 섹터 ETF 데이터소스 공백 — 모듈 상단 주석).
    각 섹터 ETF 280일 히스토리(200일 SMA 계산 여유분 포함) → breadth_from_
    closes. 실패한 섹터는 개별 스킵(전체 실패 아님)."""
    if market.upper() != "US":
        return {}
    sector_closes: dict = {}
    for ticker in _BREADTH_SECTOR_ETFS_US:
        try:
            hist = fetch_index_history(ticker, days=280)
            if hist:
                sector_closes[ticker] = [h["close"] for h in hist]
        except Exception as exc:
            log.debug("market_timing: breadth fetch failed for %s: %s", ticker, exc)
    if not sector_closes:
        return {}
    return {**breadth_from_closes(sector_closes), "sectors_ok": sorted(sector_closes)}


# ── 변동성(VIX + MOVE, 2026-07-26 사용자 추천 추가) ──────────────────────────
# VIX 는 이미 bot/fred_boards.py 유동성보드 점수 컴포넌트(FRED VIXCLS)에
# 있지만 시장타이밍 보드 단독 열람 시에도 바로 보이게 여기 카드로 병기.
# MOVE(ICE BofA, 채권시장 변동성)는 yfinance 커버리지가 시기/벤더에 따라
# 불안정 — 실패 시 그 필드만 생략(VIX 는 항상 시도, 서로 독립적 try).
def fetch_volatility_snapshot() -> dict:
    """{"vix": {value, date}, "move": {value, date}|None} — 각각 독립 fetch,
    실패한 쪽만 생략(그 항목 없이 반환). yfinance 사용(무료, 신규 API 없음)."""
    out: dict = {}
    try:
        vix_hist = fetch_index_history("^VIX", days=10)
        if vix_hist:
            out["vix"] = {"value": vix_hist[-1]["close"], "date": vix_hist[-1]["date"]}
    except Exception as exc:
        log.debug("market_timing: VIX fetch failed: %s", exc)
    try:
        move_hist = fetch_index_history("^MOVE", days=10)
        if move_hist:
            out["move"] = {"value": move_hist[-1]["close"], "date": move_hist[-1]["date"]}
    except Exception as exc:
        log.debug("market_timing: MOVE fetch failed (커버리지 불안정 — graceful): %s", exc)
    return out


# ── 데이터 수집(전체 스냅샷) + 렌더 ──────────────────────────────────────────
def _load_market_timing() -> dict:
    """전체 스냅샷 — 시장별(US/KR/JP/TW/CN_A/HK) DD/FTD + 매크로 크로스에셋 레짐 +
    크립토. 조각별 실패는 그 필드만 비고(graceful), 전체 재생성은 항상 성공."""
    markets: dict[str, dict] = {}
    for mkt, indices in MARKET_INDICES.items():
        ticker, name = indices[0]
        hist = fetch_index_history(ticker, days=120)
        if len(hist) < 26:
            markets[mkt] = {"ticker": ticker, "name": name, "error": "데이터 없음"}
            continue
        hist_desc = list(reversed(hist))
        markets[mkt] = {
            "ticker": ticker, "name": name,
            "dd": distribution_day_summary(hist_desc),
            "ftd": detect_ftd(hist),
            "latest_date": hist[-1]["date"], "latest_close": hist[-1]["close"],
        }

    # 매크로 레짐 — ETF 1년 수익률(YoY 근사) 차이로 비율 판정. 10Y-2Y 는
    # FRED(fred_client, 기존 유동성 보드 T10Y2Y 재사용 — 신규 소스 없음).
    macro: dict = {"regime": "Transitional"}
    try:
        def _yoy(ticker: str):
            h = fetch_index_history(ticker, days=280)
            if len(h) < 252:
                return None
            return (h[-1]["close"] / h[-252]["close"] - 1) * 100

        rsp, spy, iwm = _yoy("RSP"), _yoy("SPY"), _yoy("IWM")
        hyg, lqd, tlt = _yoy("HYG"), _yoy("LQD"), _yoy("TLT")
        xly, xlp = _yoy("XLY"), _yoy("XLP")
        rsp_spy = (rsp - spy) if rsp is not None and spy is not None else None
        iwm_spy = (iwm - spy) if iwm is not None and spy is not None else None
        hyg_lqd = (hyg - lqd) if hyg is not None and lqd is not None else None
        spy_tlt = (spy - tlt) if spy is not None and tlt is not None else None
        xly_xlp = (xly - xlp) if xly is not None and xlp is not None else None
        curve = None
        try:
            from bot import fred_client
            ch = fred_client.fetch_history("T10Y2Y", "2024-01-01")
            if ch:
                curve = ch[-1][1]
        except Exception:
            curve = None
        macro = {
            "regime": classify_macro_regime(rsp_spy, curve, hyg_lqd, iwm_spy,
                                            spy_tlt, xly_xlp),
            "rsp_spy": rsp_spy, "iwm_spy": iwm_spy, "hyg_lqd": hyg_lqd,
            "spy_tlt": spy_tlt, "xly_xlp": xly_xlp, "curve_10y2y": curve,
        }
    except Exception as exc:
        log.debug("market_timing: macro regime failed: %s", exc)

    # 크립토 — CoinGecko 스냅샷 1콜(가격·ATH대비낙폭만 확보, SMA/도미넌스는
    # 별도 히스토리 필요해 이번 배치엔 생략 — 부분 컴포넌트로 graceful).
    crypto: dict = {}
    try:
        snap = fetch_crypto_snapshot()
        if snap:
            score = crypto_regime_score(
                snap.get("price"), None, None, None,
                snap.get("ath_change_pct"), None)
            crypto = {**snap, "score": score.get("score"),
                     "components": score.get("components")}
    except Exception as exc:
        log.debug("market_timing: crypto panel failed: %s", exc)

    # COT(CFTC Commitments of Traders) 역발상 게이트(2026-07-26, 선물전용
    # 예외 — bot/cot_gate.py 독스트링 참조) — S&P500 E-mini 대형투기자
    # 포지셔닝. 개별종목 게이트 아닌 매크로 부가신호(실패해도 graceful).
    cot: dict = {}
    try:
        from bot.cot_gate import get_cot_signal
        sig = get_cot_signal("SP500")
        if sig.get("index") is not None:
            cot = sig
    except Exception as exc:
        log.debug("market_timing: COT gate failed: %s", exc)

    # 시장 폭(2026-07-26 사용자 추천) — US 전용(SPDR 섹터 ETF 데이터소스 공백).
    breadth: dict = {}
    try:
        breadth = fetch_market_breadth("US")
    except Exception as exc:
        log.debug("market_timing: breadth panel failed: %s", exc)

    # 변동성 VIX+MOVE(2026-07-26 사용자 추천).
    volatility: dict = {}
    try:
        volatility = fetch_volatility_snapshot()
    except Exception as exc:
        log.debug("market_timing: volatility panel failed: %s", exc)

    return {"markets": markets, "macro": macro, "crypto": crypto, "cot": cot,
            "breadth": breadth, "volatility": volatility}


_FTD_LABEL = {
    "FTD_CONFIRMED": "🟢 FTD 확정", "RALLY_ATTEMPT": "🟡 랠리 시도 중",
    "RALLY_FAILED": "🔴 랠리 실패", "RALLY_ATTEMPT_EXPIRED": "🟠 랠리 기한만료",
    "CORRECTION": "🔵 조정 진행", "NO_CORRECTION": "⚪ 조정 없음(평시)",
    "INSUFFICIENT_DATA": "— 데이터 부족",
}
_RISK_COLOR = {"NORMAL": "#16a34a", "CAUTION": "#f59e0b", "HIGH": "#ef4444",
              "SEVERE": "#991b1b"}


def render_market_timing_page(data: dict, now=None) -> str:
    """market_timing.html — 시장별 분산일·FTD 카드 + 매크로 레짐 + 크립토.
    fred_boards 의 공용 테마/nav/CSS 재사용(신규 보드 CSS 중복 방지)."""
    import html as _h
    import json as _json
    from datetime import datetime, timedelta, timezone

    from bot.fred_boards import _BOARD_CSS, _NAV, _theme_head

    _KST = timezone(timedelta(hours=9))
    now = now or datetime.now(_KST)
    ts = now.strftime("%Y-%m-%d %H:%M KST")

    cards = ""
    for mkt in ("US", "KR", "JP", "TW", "CN_A", "HK"):
        m = data.get("markets", {}).get(mkt, {})
        if m.get("error"):
            cards += (f'<div class="panel"><div class="panel-title">{mkt}</div>'
                      f'<div class="note">⚠️ {_h.escape(m["error"])}</div></div>')
            continue
        dd = m.get("dd", {})
        ftd = m.get("ftd", {})
        risk = dd.get("risk_level", "NORMAL")
        ftd_label = _FTD_LABEL.get(ftd.get("state"), ftd.get("state", "—"))
        extra = ""
        if ftd.get("state") == "FTD_CONFIRMED":
            extra = (f' · 품질점수 {ftd.get("quality_score")} '
                     f'({ftd.get("window")}, Day{ftd.get("day")})')
        cards += f"""
<div class="panel"><div class="panel-title">{_h.escape(mkt)} — {_h.escape(m.get("name",""))}</div>
<div class="stat-grid">
<div class="stat"><div class="k">분산일(D5/D15/D25)</div>
<div class="v">{dd.get("d5",0)}/{dd.get("d15",0)}/{dd.get("d25",0)}</div></div>
<div class="stat"><div class="k">분산일 위험도</div>
<div class="v" style="color:{_RISK_COLOR.get(risk,'#888')}">{_h.escape(risk)}</div></div>
<div class="stat"><div class="k">팔로우스루데이(FTD)</div>
<div class="v" style="font-size:14px">{ftd_label}{_h.escape(extra)}</div></div>
</div>
<div class="sub" style="margin:4px 0 0">기준 {_h.escape(str(m.get("latest_date","—")))} ·
최근 종가 {m.get("latest_close","—")}</div></div>"""

    macro = data.get("macro", {})
    macro_card = f"""
<div class="panel"><div class="panel-title">🌐 매크로 크로스에셋 레짐</div>
<div class="stat-grid"><div class="stat"><div class="k">국면</div>
<div class="v" style="font-size:16px">{_h.escape(macro.get("regime","Transitional"))}</div></div></div>
<div class="note">RSP-SPY(집중도) · IWM-SPY(대소형) · HYG-LQD(신용) · SPY-TLT(주식·채권) ·
10Y-2Y(커브) 다수결 — 세부 가중치 없는 단순 휴리스틱(참고용).</div></div>"""

    crypto = data.get("crypto", {})
    crypto_card = ""
    if crypto:
        score = crypto.get("score")
        score_s = f"{score:.0f}" if score is not None else "—"
        crypto_card = f"""
<div class="panel"><div class="panel-title">🪙 크립토 레짐</div>
<div class="stat-grid">
<div class="stat"><div class="k">BTC 가격</div><div class="v">${crypto.get("price","—"):,}</div></div>
<div class="stat"><div class="k">ATH 대비</div><div class="v">{crypto.get("ath_change_pct","—")}%</div></div>
<div class="stat"><div class="k">레짐 스코어(0-100)</div><div class="v">{score_s}</div></div>
</div>
<div class="note">CoinGecko 무료 공개 API — 가격·ATH낙폭 컴포넌트만(SMA·도미넌스는 추후 확장).</div></div>"""

    cot = data.get("cot", {})
    cot_card = ""
    if cot and cot.get("index") is not None:
        cot_card = f"""
<div class="panel"><div class="panel-title">📐 COT 역발상 게이트 (선물전용)</div>
<div class="stat-grid">
<div class="stat"><div class="k">S&amp;P500 E-mini COT Index</div><div class="v">{cot["index"]:.0f}</div></div>
<div class="stat"><div class="k">포지셔닝</div><div class="v" style="font-size:14px">{_h.escape(cot.get("label",""))}</div></div>
</div>
<div class="sub" style="margin:4px 0 0">기준 {_h.escape(str(cot.get("as_of","—")))} (CFTC 주간 보고)</div>
<div class="note">대형투기자(non-commercial) 순포지션의 트레일링 3년 대비 백분위(고전 COT Index) —
≥80 과열매수(역발상 매도경계) · ≤20 과열매도(역발상 매수경계). 개별종목 게이트 아닌 매크로 참고신호.</div></div>"""

    breadth = data.get("breadth", {})
    breadth_card = ""
    if breadth and breadth.get("pct_above_50dma") is not None:
        _b50 = f'{breadth["pct_above_50dma"]:.0f}%'
        _b200 = (f'{breadth["pct_above_200dma"]:.0f}%'
                if breadth.get("pct_above_200dma") is not None else "—")
        breadth_card = f"""
<div class="panel"><div class="panel-title">📊 시장 폭 (섹터 breadth, US 전용)</div>
<div class="stat-grid">
<div class="stat"><div class="k">50일선 상회 섹터</div><div class="v">{_b50}</div></div>
<div class="stat"><div class="k">200일선 상회 섹터</div><div class="v">{_b200}</div></div>
<div class="stat"><div class="k">표본</div><div class="v" style="font-size:14px">SPDR 섹터 ETF {breadth.get("n_sectors",0)}개</div></div>
</div>
<div class="note">11개 GICS 섹터 ETF(XLK/XLF/XLE/XLV/XLY/XLP/XLI/XLB/XLRE/XLU/XLC) 중 자신의 50/200일
이평선 위에 있는 비율 — 개별종목(500종목) breadth 의 섹터-레벨 근사(비용상 전수스캔 대신 채택,
문서화된 스코프). 낮으면 소수 대형주만 지수를 방어 중일 가능성, 높으면 전반적 참여.</div></div>"""

    vol = data.get("volatility", {})
    vol_card = ""
    if vol:
        vix = vol.get("vix")
        move = vol.get("move")
        vol_rows = ""
        if vix:
            vol_rows += (f'<div class="stat"><div class="k">VIX</div>'
                        f'<div class="v">{vix["value"]:.1f}</div></div>')
        if move:
            vol_rows += (f'<div class="stat"><div class="k">MOVE</div>'
                        f'<div class="v">{move["value"]:.1f}</div></div>')
        if vol_rows:
            vol_card = f"""
<div class="panel"><div class="panel-title">🌪️ 변동성 (VIX·MOVE)</div>
<div class="stat-grid">{vol_rows}</div>
<div class="note">VIX=주식시장 변동성(공포지수) · MOVE=채권시장 변동성(ICE BofA, 커버리지 불안정 시 생략).
금리변동성이 기술주/반도체 장세에 선행 신호가 되는 경우가 있어 병기.</div></div>"""

    payload = _json.dumps(data, ensure_ascii=False, default=str).replace("<", "\\u003c")
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>시장타이밍</title>
{_theme_head()}
{_BOARD_CSS}</head><body><div class="wrap">
{_NAV}
<h1>🚦 <em>시장타이밍</em> 보드</h1>
<p class="sub">분산일(IBD)·팔로우스루데이(O'Neil)·시장폭·변동성·매크로 레짐·크립토·COT — 데이터 적용시각 {ts} ·
소스 yfinance + FRED + CoinGecko + CFTC(전부 무료, 6시간 주기 자동 갱신)</p>
<details class="guide"><summary>ℹ️ 사용법 — 처음이면 펼쳐 보세요</summary>
<b>1) 분산일(Distribution Day)</b> — 종가 -0.2%+ 하락 & 거래량 증가 = 기관 매도 신호.
D5/D15/D25 = 최근 5/15/25거래일 내 활성 건수. CAUTION(3+)·HIGH(5+)·SEVERE(6+) — 높을수록
경계.<br>
<b>2) 팔로우스루데이(FTD)</b> — 3%+ 조정 후 반등 4~10일째 +1.25%+ 상승·거래량 증가 =
바닥 확인 신호(O'Neil 방법론). 🟢 FTD 확정만 유효 신호, 나머지는 대기/무효.<br>
<b>3) 시장 폭</b> — 11개 GICS 섹터 ETF 중 50/200일선 상회 비율(US 전용, 개별종목 breadth
의 섹터-레벨 근사). 낮으면 소수 대형주만 지수 방어, 높으면 전반적 참여.<br>
<b>4) 변동성(VIX·MOVE)</b> — VIX=주식 공포지수, MOVE=채권 변동성(커버리지 불안정 시 생략).<br>
<b>5) 매크로 레짐</b> — 크로스에셋 비율로 시장 국면(집중/확산/긴축/인플레) 참고.<br>
<b>6) 크립토 레짐</b> — BTC 중심 0-100 점수(참고용, 컴포넌트 일부만 반영).<br>
<b>7) COT 역발상 게이트</b> — S&amp;P500 E-mini 대형투기자 포지셔닝(CFTC 주간보고,
선물전용) 트레일링 3년 백분위. 극단 쏠림(≥80/≤20)은 과거 반전 빈도가 높았던
구간이라는 참고 신호일 뿐(선물전용).<br>
자동 신호이므로 참고용 — 확정 판단 금지.
</details>
{cards}
{breadth_card}
{vol_card}
{macro_card}
{crypto_card}
{cot_card}
<div class="footer">분산일·FTD·시장폭·변동성·매크로레짐·크립토·COT — 신호는 참고용(투자 판단 아님) · NOAH</div>
</div>
<script id="mt-data" type="application/json">{payload}</script>
</body></html>"""


def regenerate_market_timing() -> None:
    """market_timing.html 재생성 — 자정/6시간 주기 + startup. 실패해도 기존
    파일 유지(graceful). ⚠️ 네트워크 다수콜 — to_thread 필수(이벤트루프 차단 금지)."""
    from bot.dashboard import ARCHIVE_ROOT, _inject_update_banner
    try:
        data = _load_market_timing()
        html = _inject_update_banner(render_market_timing_page(data))
        (ARCHIVE_ROOT / "market_timing.html").write_text(html, encoding="utf-8")
        log.info("market_timing: market_timing.html regenerated")
    except Exception:
        log.exception("market_timing: regen failed")
