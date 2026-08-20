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

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# 규칙 10a — 모든 시각은 KST 명시계산(서버 로컬타임 의존 금지).
_KST_TZ = timezone(timedelta(hours=9))


def _kst_now() -> datetime:
    return datetime.now(_KST_TZ)

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
# 지수는 **1거래일만 늦어도** 재질의한다(사용자 2026-08-20: 장 마감 6시간
# 뒤인데 전일 기준). 변동성 지수(^VIX·^MOVE)는 원천 자체가 성겨서 5일을
# 유지 — 매번 재질의하면 야후 호출만 두 배가 된다.
_IDX_RETRY_STALE_DAYS = 1


def _idx_stale_days(ticker: str) -> int:
    return (_VOL_STALE_DAYS if str(ticker).upper() in _VOL_TICKERS
            else _IDX_RETRY_STALE_DAYS)


_VOL_TICKERS = ("^VIX", "^MOVE", "MOVE", "^VXTLT", "^TYVIX")


def _payload_to_rows(p: dict | None, days: int) -> list[dict]:
    """chart payload → [{date, close, high, low, volume}] 오름차순."""
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


# ── 야후가 하루 늦을 때 네이버 지수 종가로 보강 ──────────────────────
# 사용자 2026-08-20: "한국일본 이미 장종료 6시간 지났을텐데 … 난 모두 나라다
# 가장 최신으로 하는걸 원해." 재질의(두 질의 방식)로도 야후가 KR/JP 지수를
# 하루 늦게 주는 날이 있다 — 반면 **글로벌 시장 스냅샷은 네이버**라 같은
# 시각에 최신이었다(실측: 시장타이밍 KOSPI 08-18 6,869.83 / 글로벌 스냅샷
# 08-19 6,471.17).
#
# ⚠️ 네이버 지수 시세엔 **날짜가 없다**. 그래서 날짜를 추측하지 않고
# **자기검증**한다: 네이버가 주는 `prev`(전일종가)가 야후의 마지막 봉과
# 같으면, 네이버 `close` 는 그 **다음 세션**이 확실하다. 안 맞으면 그냥
# 버린다(장중이라 close 가 미완성인 경우가 여기서 걸러진다).
#
# TW/CN_A/HK 는 시장타이밍이 **ETF**(0050.TW·510300.SS·2800.HK)를 쓰므로
# 지수(.TWII/.SSEC/.HSI)로 대체할 수 없다 — 다른 상품이다. 매핑하지 않는다.
# 티커 → (시장, 네이버종류, 네이버코드)
_NAVER_INDEX_FOR = {
    "^KS11": ("KR", "domestic", "KOSPI"),
    "^KQ11": ("KR", "domestic", "KOSDAQ"),
    "^N225": ("JP", "world", ".N225"),
    "^GSPC": ("US", "world", ".INX"),
    "^IXIC": ("US", "world", ".IXIC"),
}
_NAVER_TAIL_TOL = 0.001      # 전일종가 일치 허용오차 0.1%
_NAVER_TAIL_MAX_MOVE = 0.20  # 하루 ±20% 초과면 정렬이 틀린 것으로 보고 폐기


def _quote_tail_supported(ticker: str) -> bool:
    """이 티커에 신선도 보강 경로가 있는가(감사 표기용 — 화면과 같은 판정)."""
    if str(ticker).upper() in _NAVER_INDEX_FOR:
        return True
    try:
        from bot.market import detect_market
        return detect_market(ticker) in ("TW", "US", "JP", "HK", "CN_A")
    except Exception:                                          # noqa: BLE001
        return False


def _market_quote(ticker: str) -> dict | None:
    """{close, prev} 또는 None — **시장 무관** 최신 시세.

    2026-08-20 감사: TW/CN/HK 는 지수가 아니라 ETF 라 네이버 지수 매핑이
    없어서 야후가 늦으면 손쓸 방법이 없었다(TW 가 실제로 08-18 에 멈춰 있었다).
    그런데 `_merge_today_bar` 가 이미 쓰는 시세 클라이언트가 전 시장을 덮는다
    — TW=tw_quote · US/JP/HK/CN=world_quote. 이들은 `{price, pct}` 를 주므로
    **전일종가를 역산**(price ÷ (1+pct/100))해 같은 자기검증에 쓸 수 있다."""
    spec = _NAVER_INDEX_FOR.get(str(ticker).upper())
    if spec:
        return _naver_index_quote(ticker)
    try:
        from bot.market import detect_market
        mkt = detect_market(ticker)
        if mkt == "TW":
            from bot.tw_quote import fetch_tw_quote
            q = fetch_tw_quote(ticker)
        elif mkt in ("US", "JP", "HK", "CN_A"):
            from bot.world_quote import fetch_world_quote
            q = fetch_world_quote(ticker)
        else:
            return None
    except Exception as exc:                                   # noqa: BLE001
        log.debug("market_timing: %s 시세 조회 실패: %s", ticker, exc)
        return None
    px = (q or {}).get("close") or (q or {}).get("price")
    pct = (q or {}).get("pct")
    if px is None or pct is None:
        return None
    try:
        prev = float(px) / (1.0 + float(pct) / 100.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return {"close": float(px), "prev": prev} if prev else None


def _naver_index_quote(ticker: str) -> dict | None:
    """{close, prev} 또는 None. 네이버 지수 시세(글로벌 스냅샷과 같은 소스)."""
    spec = _NAVER_INDEX_FOR.get(str(ticker).upper())
    if not spec:
        return None
    _mkt, kind, code = spec
    try:
        from bot import naver_marketindex as nm
        got = (nm.fetch_domestic_indices((code,)) if kind == "domestic"
               else nm.fetch_world_indices((code,)))
    except Exception as exc:                                   # noqa: BLE001
        log.debug("market_timing: 네이버 지수 %s 실패: %s", ticker, exc)
        return None
    rec = (got or {}).get(code)
    if not isinstance(rec, dict):
        return None
    c, pv = rec.get("close"), rec.get("prev")
    return {"close": c, "prev": pv} if c and pv else None


def _quote_tail(ticker: str, rows: list[dict]) -> list[dict]:
    """야후 시계열이 기대 거래일보다 뒤처졌으면 네이버 종가 한 봉을 덧붙인다.

    **정렬이 확인될 때만** 붙인다 — 네이버 `prev` 가 야후 마지막 봉과 같아야
    네이버 `close` 가 그 다음 세션임이 보장된다. 추측으로 날짜를 붙이지
    않는다(장중이면 여기서 자동으로 걸러진다)."""
    if not rows:
        return rows
    spec = _NAVER_INDEX_FOR.get(str(ticker).upper())
    if spec:
        market = spec[0]
    else:
        try:
            from bot.market import detect_market
            market = detect_market(ticker)
        except Exception:                                      # noqa: BLE001
            return rows
    expected, _grace = _expected_session(market)
    last = str(rows[-1].get("date") or "")[:10]
    if not expected or last >= expected:
        return rows                     # 이미 최신 — 건드리지 않는다
    q = _market_quote(ticker)
    if not q:
        return rows
    prev_y = float(rows[-1]["close"])
    if not prev_y or abs(q["prev"] - prev_y) / prev_y > _NAVER_TAIL_TOL:
        log.info("market_timing: %s 시세 전일종가 %.2f ≠ 야후 마지막 봉 "
                 "%.2f — 정렬 불가로 보강 생략(장중 추정)",
                 ticker, q["prev"], prev_y)
        return rows
    move = abs(q["close"] - prev_y) / prev_y
    if move > _NAVER_TAIL_MAX_MOVE:
        log.warning("market_timing: %s 시세 종가 %.2f 가 전일 대비 %.1f%% — "
                    "정렬 오류 의심으로 폐기", ticker, q["close"], move * 100)
        return rows
    log.info("market_timing: %s 야후 %s → 시세 %s 종가 %.2f 보강",
             ticker, last, expected, q["close"])
    return rows + [{"date": expected, "close": q["close"],
                    "high": q["close"], "low": q["close"], "volume": None}]


def fetch_index_history(ticker: str, days: int = 120,
                        min_rows: int | None = None) -> list[dict]:
    """지수/ETF 히스토리 [{date, close, high, low, volume}] 오름차순(과거→
    최신) — bot.chart_data.fetch_chart_payload 재사용(yfinance, 무료).
    실패 시 [].

    `min_rows`: 이만큼 안 오면 **네이버 일봉으로 재시도**한다. yfinance 가
    일부 KR ETF 에 대해 **짧은 시계열을 '성공'으로** 돌려주는 사례가 실측됐다
    (2026-08-16 KODEX 반도체·IT·철강·에너지화학이 1년 요청에 18행 — 같은
    경로로 다른 9개는 243행 정상). `fetch_chart_payload` 의 네이버 폴백은
    **빈 결과일 때만** 걸려서 이 절단을 못 잡는다. 200일 SMA 처럼 길이가
    의미를 좌우하는 호출부는 min_rows 를 넘겨 방어할 것.

    `days` → period 매핑에 '3y' 단계가 있다. 1년(≈250 거래일) 요청으로는
    252일 DD 를 잘라 쓸 여유가 없어(월말 확정분은 앞쪽을 더 잘라낸다)
    300일 초과 요청은 3년치를 받아 뒤에서 days 만큼 취한다."""
    try:
        from bot.chart_data import _fetch_naver_daily, fetch_chart_payload
        period = "3y" if days > 300 else "1y" if days > 60 else "3mo"
        p = fetch_chart_payload(ticker, interval="1d", period=period)
        out = _payload_to_rows(p, days)
        # ⚠️ **장기 레인지가 조용히 낡거나 비는 종목이 있다**(2026-08-19
        # vol_probe 실측, `^MOVE`): 야후에 최신값이 분명히 있는데(메타
        # regularMarketPrice 74.98 · 최신 봉 08-18) 우리 3년 요청은 어떤 날은
        # 07-17 에서 끊긴 400행을, 어떤 날은 **0행**을 준다. 같은 심볼을
        # 1년으로 부르면 227행이 정상적으로 온다 — 심볼이 죽은 게 아니라
        # **긴 레인지 응답이 불안정**한 것이다(그래서 카드가 나타났다
        # 사라졌다 했다). 짧은 레인지로 한 번 더 물어 더 최신인 쪽을 쓴다.
        # ⚠️ 2026-08-20 사용자: "한국일본 이미 장종료 6시간 지났을텐데 KR
        # KOSPI 같은건 금일 기준도 아니야." 재질의가 **3년 요청일 때만**
        # 걸려 있었는데 시장타이밍은 days=120(=1년)이라 한 번도 안 탔다 —
        # `^MOVE` 에서 배운 '묻는 방식이 결과를 가른다'를 정작 지수에는
        # 적용하지 않고 있었다. 이제 **모든 기간**에서 신선도로 판정한다.
        _stale_gate = _idx_stale_days(ticker)
        age = _vol_age_days(out[-1]["date"]) if out else None
        if not out or (age is not None and age > _stale_gate):
            # ⚠️ 같은 '1년'이라도 **묻는 방식**이 결과를 가른다(2026-08-19
            # `^MOVE` 실측): 날짜범위 질의는 07-17 에서 끊긴 데이터를 주고
            # 기간 키워드(`period="1y"`) 질의는 227행·최신 08-18 을 준다.
            # 어느 쪽이 이길지 날마다 뒤바뀌므로 **둘 다** 물어 가장 신선한
            # 쪽을 쓴다(같으면 긴 쪽).
            cands = []
            for pp in (True, False):
                got = _payload_to_rows(
                    fetch_chart_payload(ticker, interval="1d",
                                        period="1y", prefer_period=pp), days)
                if got:
                    cands.append(got)
            alt = min(cands,
                      key=lambda r: (_vol_age_days(r[-1]["date"]) or 10 ** 6,
                                     -len(r))) if cands else []
            alt_age = _vol_age_days(alt[-1]["date"]) if alt else None
            if alt and (age is None or (alt_age is not None
                                        and alt_age < age)):
                # 조용한 대체 금지 — 폴백을 탄 사실을 남긴다.
                log.info("market_timing: %s %s 질의 %d행(최신 %s) — "
                         "재질의 %d행(최신 %s)으로 대체",
                         ticker, period, len(out),
                         out[-1]["date"] if out else "—",
                         len(alt), alt[-1]["date"])
                out = alt
        # `out` 이 비었으면 fetch_chart_payload 가 **이미** 네이버를 시도한
        # 뒤다(그 폴백은 빈 결과에서 걸린다) — 여기서 또 부르면 10초 타임아웃
        # HTTP 호출만 티커마다 중복된다. 절단(0<len<min_rows)일 때만 재시도.
        if min_rows and 0 < len(out) < min_rows:
            # 조용한 대체 금지 — 폴백을 탄 사실을 남긴다(이 파일 기존 규약).
            alt = _payload_to_rows(_fetch_naver_daily(ticker, period), days)
            if len(alt) > len(out):
                log.info("market_timing: %s 히스토리 %d행(<%d) — 네이버 폴백 "
                         "%d행으로 대체", ticker, len(out), min_rows, len(alt))
                return alt
            log.warning("market_timing: %s 히스토리 %d행 — min_rows %d 미만이고 "
                        "네이버 폴백도 %d행(그대로 반환)",
                        ticker, len(out), min_rows, len(alt))
        # ⚠️ **여기서** 붙인다 — 시장타이밍과 Breadth 가 둘 다 이 함수를 쓰므로
        # 호출부마다 배선하면 한쪽을 빠뜨린다(실수 #12 배선 누락).
        return _quote_tail(ticker, out)
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
# GICS 11개 섹터 1:1 매핑) 의 자기 20/50/200일선 상회 비율로 근사(20일=단기,
# 50일=중기, 200일=장기 — Minervini/IBD 추세템플릿 관례와 동일 원리, 20일은
# 사용자 2026-07-26 "중기말고 단기도 추가해줘" 요청으로 추가). 개별종목
# breadth 보다 해상도는 낮지만(섹터 단위) '소수 대형주가 지수를 방어 중인지
# vs 전반적 상승인지' 판별에는 충분 — 이미 계산 중인 RSP/SPY(macro 레짐)와
# 상호보완(그쪽은 대형/소형 비율, 이쪽은 섹터 참여도).
#
# 2026-08-16 KR 추가(사용자 "US 위에 KR 을 만들어줘"). 한국은 삼성전자+하이닉스
# 비중이 커 '소수 대형주가 지수를 방어 중인가' 판별이 미국보다 더 중요하다.
# 표본은 **KODEX 섹터 시리즈** — 미국 SPDR `XL*` 가 GICS 11섹터와 1:1 인 것과
# 같은 구조적 위치(단일 운용사·KRX 업종지수 1:1·최장 히스토리). GICS 11개로
# 강제 매핑하지 않는 이유: 한국은 커뮤니케이션·부동산 섹터 ETF 가 빈약해 억지
# 매핑이 생기고, 반대로 조선·건설·증권처럼 한국 고유의 큰 업종이 GICS 버킷에
# 묻힌다. breadth 의 목적(참여도)에는 실제 업종 구조가 맞다 — 그래서 카드에도
# "GICS" 가 아니라 "KODEX 섹터 ETF(KRX 업종)" 라고 적는다.
#
# 티커→라벨 dict 인 이유: 데이터를 못 받은 섹터를 **이름으로** 알리기 위해서다
# (분모가 조용히 줄면 '수집은 됐는데 화면에서 사라진' 실패모드가 된다).
#
# 티커 출처: `TradingAgents/.../sector_strength_tools.py` 의 `_KR_INDUSTRY_
# OVERRIDES`(주석에 "Source: KRX ETF listings as of 2026"). 그 표는 산업→ETF
# **매핑**이고 이건 breadth **바스켓**이라 성격이 달라 합치지 않았지만, 여기
# 티커·라벨이 그 표와 어긋나면 테스트가 잡는다(레포 내 유일한 검증 출처와
# 대조 — 두 표가 갈라지는 걸 막는다). 중복 업종은 뺐다: 화학(102710)은
# 에너지화학과, 바이오(244580)는 헬스케어와, 게임(300950)은 미디어엔터와
# 종목이 겹쳐 breadth 에서 이중계산이 된다.
#
# JP/TW/CN_A/HK 는 아직 미등록 — JP 는 NEXT FUNDS TOPIX-17 시리즈가 위 파일에
# 이미 있어 확장 가능하고(후속), TW/CN_A/HK 는 동급 섹터 ETF 시리즈 확인이
# 안 된 상태다. 어느 쪽도 market gate 가 아니라 데이터소스 미연결이다.
_BREADTH_SECTORS: dict[str, dict[str, str]] = {
    "US": {
        "XLK": "기술", "XLF": "금융", "XLE": "에너지", "XLV": "헬스케어",
        "XLY": "경기소비재", "XLP": "필수소비재", "XLI": "산업재",
        "XLB": "소재", "XLRE": "부동산", "XLU": "유틸리티",
        "XLC": "커뮤니케이션",
    },
    "KR": {
        "091160.KS": "반도체", "266370.KS": "IT", "091170.KS": "은행",
        "102970.KS": "증권", "140700.KS": "보험", "091180.KS": "자동차",
        "117700.KS": "건설", "117680.KS": "철강", "117460.KS": "에너지화학",
        "102960.KS": "기계장비", "140710.KS": "운송", "266420.KS": "헬스케어",
        "266360.KS": "K콘텐츠",
    },
}
# 카드 표본 라벨 — 시장마다 상품군이 다르다(SPDR vs KODEX).
_BREADTH_SOURCE_LABEL = {"US": "SPDR 섹터 ETF", "KR": "KODEX 섹터 ETF"}


def sma(closes: list, period: int):
    """단순이동평균 마지막 값 — 순수함수. 데이터 부족(<period) 시 None."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def breadth_from_closes(sector_closes: dict) -> dict:
    """{ticker: closes(과거→최신 list)} → 섹터-레벨 breadth 비율. 순수함수
    (테스트용). 각 섹터가 자신의 20/50/200일 SMA 위에 있는지 비율(0-100%) —
    개별종목 A/D-line·MMTH 의 섹터 근사(모듈 상단 주석 참조). 20일선은
    사용자 2026-07-26 요청("중기말고 단기도 추가해줘") — 한 거래월(~영업일
    20일) 단위 단기 모멘텀, 50/200일과 동일 원리로 상회비율만 다른 창.
    데이터 부족한 섹터는 그 지표에서 제외(counted 분모에서도 빠짐)."""
    above20 = above50 = above200 = counted20 = counted50 = counted200 = 0
    for closes in sector_closes.values():
        if not closes:
            continue
        last = closes[-1]
        s20 = sma(closes, 20)
        if s20 is not None:
            counted20 += 1
            if last > s20:
                above20 += 1
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
        "pct_above_20dma": round(above20 / counted20 * 100, 1) if counted20 else None,
        "pct_above_50dma": round(above50 / counted50 * 100, 1) if counted50 else None,
        "pct_above_200dma": round(above200 / counted200 * 100, 1) if counted200 else None,
        # 지표별 분모 — n_sectors 와 다를 수 있다(신규상장 ETF 는 200일치가
        # 없어 200dma 에서만 빠진다). 노출 안 하면 '표본 13개'라 적어놓고
        # 실제로는 11개로 계산한 값을 보여주게 된다(2026-08-16 독립 리뷰).
        "counted_20dma": counted20, "counted_50dma": counted50,
        "counted_200dma": counted200,
        # 상회 개수 — 화면의 (n/m) 은 **이것**이 분자여야 한다. 옛 코드는
        # counted/n_sectors 를 찍어 세 지표가 전부 (13/13) 으로 같아 보였고,
        # 69%·54% 와 대놓고 모순됐다(사용자 2026-08-16 "왜 숫자는 다 같아?").
        "above_20dma": above20, "above_50dma": above50,
        "above_200dma": above200,
        "n_sectors": len(sector_closes),
    }


def fetch_market_breadth(market: str = "US") -> dict:
    """`_BREADTH_SECTORS` 에 등록된 시장(US·KR)만 지원 — 미등록은 {}.
    각 섹터 ETF 280일 히스토리(200일 SMA 계산 여유분 포함) → breadth_from_
    closes. 실패한 섹터는 개별 스킵(전체 실패 아님).

    ⚠️ 실패한 섹터를 `sectors_missing` 에 **라벨로** 담아 돌려준다. 옛 코드는
    조용히 분모에서만 빠졌는데, 그러면 ETF 가 상장폐지·티커변경돼도 화면엔
    비율만 멀쩡히 뜬다(수집 실패가 보이지 않는 실패모드)."""
    sectors = _BREADTH_SECTORS.get(market.upper())
    if not sectors:
        return {}
    sector_closes: dict = {}
    missing: list[str] = []
    for ticker, label in sectors.items():
        try:
            # min_rows=200 — 200일 SMA 가 이 지표의 절반이라 짧은 시계열이
            # 오면 조용히 200dma 분모에서만 빠진다(실측: yfinance 가 일부
            # KODEX ETF 에 18행만 반환). 네이버 폴백으로 재시도한다.
            hist = fetch_index_history(ticker, days=280, min_rows=200)
        except Exception as exc:
            log.debug("market_timing: breadth fetch failed for %s: %s", ticker, exc)
            hist = None
        if hist:
            sector_closes[ticker] = [h["close"] for h in hist]
        else:
            missing.append(label)
    if not sector_closes:
        return {}
    return {**breadth_from_closes(sector_closes),
            "market": market.upper(),
            "source_label": _BREADTH_SOURCE_LABEL.get(market.upper(), "섹터 ETF"),
            "sectors_ok": sorted(sector_closes),
            "sectors_missing": missing}


# ── 변동성(VIX + MOVE, 2026-07-26 사용자 추천 추가) ──────────────────────────
# VIX 는 이미 bot/fred_boards.py 유동성보드 점수 컴포넌트(FRED VIXCLS)에
# 있지만 시장타이밍 보드 단독 열람 시에도 바로 보이게 여기 카드로 병기.
# MOVE(ICE BofA, 채권시장 변동성)는 yfinance 커버리지가 시기/벤더에 따라
# 불안정 — 실패 시 그 필드만 생략(VIX 는 항상 시도, 서로 독립적 try).
# (2026-07-26 회고: 한때 CNN 을 VIX 값의 "최우선 소스"로 시도했으나 —
# 사용자가 스크린샷으로 재확인한 결과 사용자가 말한 "CNN 값"은 VIX 가 아닌
# CNN Fear & Greed 지수(아래 render 의 sentiment 카드) 자체였음. CNN 은
# 원시 VIX 가격을 공개적으로 노출하지 않아 그 시도는 잘못된 전제 —
# bot/fear_greed_client.py 참조, 되돌림. VIX 는 계속 네이버가 정확한
# 소스(메인 대시보드 bot/macro_snapshot.py 와 동일 — canonical 일치).)
def _fetch_vix_naver():
    """네이버 worldstock .VIX — bot/macro_snapshot.py 의 메인 대시보드 매크로9
    위젯과 동일 소스. 사용자 2026-07-26 리포트('빅스지수가 다른데
    메인대시보드랑 시장타이밍이랑') — 이 카드가 독립적으로 yfinance ^VIX 만
    썼던 게 원인(다른 소스 + 최대 6시간 stale, canonical 값 불일치). 30초
    캐시라 사실상 실시간. 실패 시 None(호출부가 yfinance 로 최종 폴백)."""
    try:
        from bot import naver_marketindex as nm
        rec = (nm.fetch_world_indices((".VIX",)) or {}).get(".VIX")
        if rec and rec.get("close") is not None:
            return float(rec["close"])
    except Exception as exc:
        log.debug("market_timing: VIX naver fetch failed: %s", exc)
    return None


# 변동성 지수 과거 비교창 — CNN F&G 카드와 **같은 라벨·같은 순서**로 통일
# (사용자 2026-08-16 "시장 센티먼트처럼 전일·1주·한달·1년"). 거래일 기준
# 오프셋: 1주=5, 1달=21, 1년=252.
_VOL_LOOKBACKS = (("전일", 1), ("1주", 5), ("1달", 21), ("1년", 252))


# 라벨 → (되짚을 달력일, 허용 오차일). 위치(-1-back) 대신 **날짜**로 되짚는다.
# 2026-08-20 실측: `^MOVE` 차트가 07-17 에서 끊긴 226행 + 08-18 한 점이면
# 위치 기반 '전일' 은 **32일 전** 값이 된다 — 라벨이 거짓말을 한다.
# 오차 안에 관측이 없으면 그 칸을 생략한다(없는 것보다 나쁜 게 틀린 것).
_VOL_LOOKBACK_DAYS = {"전일": (1, 4), "1주": (7, 5), "1달": (30, 10),
                      "1년": (365, 30)}


def vol_history(rows: list) -> dict:
    """오름차순 히스토리 → {전일, 1주, 1달, 1년} 종가. 부족하거나 기대
    시점에서 너무 먼 창은 생략한다(없는 값을 가장 오래된 값으로 대체하면
    '1년 전'이 거짓이 된다)."""
    out: dict = {}
    pts = [(str(r["date"])[:10], r["close"]) for r in rows or []
           if r.get("date") and r.get("close") is not None]
    if not pts:
        return out
    try:
        latest = datetime.strptime(pts[-1][0], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return out
    for label, _ in _VOL_LOOKBACKS:
        cal, tol = _VOL_LOOKBACK_DAYS[label]
        target = latest - timedelta(days=cal)
        hit = None
        for d, c in pts[:-1]:            # 최신 자신은 비교 대상이 아니다
            try:
                dd = datetime.strptime(d, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if dd <= target:
                hit = (dd, c)
        if hit and (target - hit[0]).days <= tol:
            out[label] = hit[1]
    return out


# VKOSPI(코스피200 변동성지수) — KRX 산출.
#
# ⚠️ 네이버는 **원리적으로 불가**다. VM 프로브(2026-08-16)가 확정했다:
#   /chart/domestic/index/VKOSPI/day → 200 이지만 본문 `[]`(빈 배열)
#   /index/VKOSPI/basic              → 409 {"code":"StockConflict",
#                                      "message":"지원하지 않는 지수입니다"}
#   siseJson VKOSPI                  → 헤더 행만, 데이터 0
#   (대조군 KOSPI 는 같은 URL 로 정상 데이터 → URL 형태가 아니라 **지수 미지원**)
# 그래서 네이버 후보 경로는 폐기하고, 메인 대시보드가 이미 쓰고 있는
# **KIS 경로를 그대로 재사용**한다(사용자 2026-08-16 "메인대시보드보면 적용돼
# 있어, 이거 참조해" · 화면 각주 '출처: KIS').
#
# 지수코드는 `naver_sector_client._KIS_VKOSPI_IDX_CODE` 를 **import 해서** 쓴다.
# 복제하면 KIS 가 코드를 바꾼 날 메인 대시보드와 이 보드가 갈라진다.
_VKOSPI_DAYS = 400          # 1년(252거래일) 창을 만들려면 200 으로는 모자라다
# 변동성지수 타당 범위 — VKOSPI 는 연율화 %(역대 저점 ~10, 2008/2020 급등기
# ~70~90). 코스피(2,000~3,000)나 코스피200(300~400)이 잘못 잡히면 범위 밖이라
# 걸린다. **소스가 KIS 로 바뀌어도 유지한다** — 지수코드 오타·KIS 코드체계
# 변경 시 '가격지수를 변동성지수로 표시'하는 실패모드는 그대로다.
_VKOSPI_MIN, _VKOSPI_MAX = 3.0, 200.0
_VKOSPI_MIN_ROWS = 30


def _vkospi_plausible(rows: list) -> bool:
    """값이 변동성지수 범위 안이고 행 수가 충분한가. 아니면 채택하지 않는다."""
    closes = [r.get("close") for r in rows or []
              if isinstance(r.get("close"), (int, float))]
    if len(closes) < _VKOSPI_MIN_ROWS:
        return False
    return all(_VKOSPI_MIN <= c <= _VKOSPI_MAX for c in closes)


def fetch_vkospi_rows(days: int = _VKOSPI_DAYS) -> list:
    """KIS 국내지수 일봉 → [{date, close}] 오름차순. 실패 시 [] (graceful).

    `kis_client.get_domestic_index_daily` 가 1시간 디스크 캐시를 갖고 있어
    3시간 주기 재생성에서 실제 호출은 회당 1번뿐이다. 크리덴셜
    (KIS_APP_KEY/KIS_APP_SECRET)이 없으면 None 을 돌려주고, 그러면 카드가
    통째로 생략된다(0 이나 VIX 값으로 채우지 않는다)."""
    try:
        from bot.kis_client import get_kis
        from bot.naver_sector_client import _KIS_VKOSPI_IDX_CODE
        rows = get_kis().get_domestic_index_daily(_KIS_VKOSPI_IDX_CODE,
                                                  days=days) or []
    except Exception as exc:
        log.debug("market_timing: VKOSPI(KIS) 실패: %s", exc)
        return []
    if not rows:
        log.info("market_timing: VKOSPI 데이터 없음 — 카드 생략"
                 "(KIS 크리덴셜 또는 지수코드 확인)")
        return []
    if not _vkospi_plausible(rows):
        log.warning("market_timing: VKOSPI 응답이 변동성지수 범위 밖 — 다른 "
                    "지수일 가능성(최근값 %s, %d행). 채택 안 함.",
                    [r.get("close") for r in rows[-3:]], len(rows))
        return []
    return rows


# ── 변동성 카드 last-good 캐시 ────────────────────────────────────────────
# 사용자 2026-08-19: "MOVE 는 나올때가 있고 안나올때가 있는데 왜 그런거야?"
# 원인은 **카드가 매 재생성마다 원천 fetch 1회에 전부를 걸고 있었기 때문**
# 이다 — `^MOVE` 가 한 번 빈 결과를 주면 그 사이클 카드가 통째로 사라지고,
# 다음 사이클에 성공하면 다시 나타난다(깜빡임). 값이 틀린 게 아니라 **없어
# 지는** 게 문제라, 마지막 성공분을 디스크에 남겨 원천이 실패한 사이클에도
# 카드를 유지한다. 단, 캐시로 그린 카드는 **캐시라고 표시**한다(조용한 대체
# 금지 — 실수 #12). 오래된 값이 실시간인 척하는 것이 사라지는 것보다 나쁘다.
_VOL_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "market_timing"
_VOL_CACHE_MAX_AGE_DAYS = 7      # 이보다 낡으면 캐시도 쓰지 않는다


def _vol_cache_path(key: str) -> Path:
    return _VOL_CACHE_DIR / f"vol_{key}.json"


def _vol_cache_save(key: str, rec: dict) -> None:
    try:
        _VOL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _vol_cache_path(key).write_text(
            json.dumps({"saved_at": _kst_now().isoformat(), "rec": rec},
                       ensure_ascii=False), encoding="utf-8")
    except Exception as exc:                                   # noqa: BLE001
        log.warning("market_timing: %s 캐시 저장 실패: %s", key, exc)


def _vol_cache_load(key: str) -> dict | None:
    """마지막 성공분. 없거나 너무 낡으면 None."""
    p = _vol_cache_path(key)
    try:
        if not p.exists():
            return None
        blob = json.loads(p.read_text(encoding="utf-8"))
        saved = datetime.fromisoformat(blob["saved_at"])
        age = (_kst_now() - saved).days
        if age > _VOL_CACHE_MAX_AGE_DAYS:
            log.warning("market_timing: %s 캐시가 %d일 낡음(>%d) — 사용 안 함",
                        key, age, _VOL_CACHE_MAX_AGE_DAYS)
            return None
        rec = dict(blob["rec"])
        # 캐시임을 화면 라벨로 드러낸다(source 는 '현재 (…)' 로 렌더된다).
        rec["source"] = f"{saved.strftime('%m-%d')} 기준 저장분"
        rec["from_cache"] = True
        return rec
    except Exception as exc:                                   # noqa: BLE001
        log.warning("market_timing: %s 캐시 읽기 실패: %s", key, exc)
        return None


# 변동성 카드의 '현재'가 며칠 전 종가인지 — 화면이 거짓말하지 않게 한다.
# 2026-08-19 vol_probe 실측: `^MOVE` 는 fetch 400행 **성공**인데 최신 관측이
# 2026-07-17(33일 전)이었다. 값이 없어서가 아니라 **원천 시계열이 멈춰서**
# 였고, 그동안 화면은 그 값을 '현재'·'전일'로 표기하고 있었다. 없는 것보다
# 나쁜 건 낡은 것을 최신인 척 보여주는 것이다(실수 #10b·#12).
_VOL_STALE_DAYS = 5          # 이보다 오래되면 카드에 지연 표시


def _vol_age_days(date_str: str | None) -> int | None:
    """'YYYY-MM-DD' → KST 오늘 기준 경과일. 못 읽으면 None."""
    if not date_str:
        return None
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (_kst_now().date() - d).days


# ── 관측 누적 시계열 + 시세 메타 폴백 ──────────────────────────────────
# 2026-08-19 vol_probe v5 확정: 야후가 `^MOVE` **차트를 1봉만** 준다(메타는
# regularMarketPrice 74.98 · 08-18 미 장마감으로 멀쩡). 우리 차트 경로는
# "선을 그으려면 2점 필요" 라 1봉을 버리고 폴백을 타는데, 지수 카드는 차트가
# 아니라 **값**이 필요하다 — 그래서 시세 메타로 한 점을 건져 온다.
# 그리고 한 점만으로는 전일·1주·1달·1년 창을 만들 수 없으므로, 관측을
# **디스크에 누적**해 창을 시간이 지나며 복원한다(원천이 무너져도 화면이
# 조용히 반쪽이 되지 않게). 전부 실제 관측치라 지어낸 값은 없다.
_VOL_SERIES_MAX = 420        # 1년(252거래일) 창 + 여유


def _vol_series_path(key: str) -> Path:
    return _VOL_CACHE_DIR / f"series_{key}.json"


def _vol_series_merge(key: str, rows: list[dict]) -> list[dict]:
    """새 관측을 누적 시계열에 합쳐 **오름차순 전체**를 돌려준다.
    같은 날짜는 새 값으로 덮고, 오래된 것부터 잘라 _VOL_SERIES_MAX 유지.

    ⚠️ 두 가지 정직성 규칙(배포전 셀프리뷰에서 잡음):
    (a) 이번에 받은 게 **없으면 빈 리스트** — 누적분을 되살리면 원천이 죽은
        날에도 '전일·1주' 칸이 옛 값으로 채워져 화면이 거짓말을 한다.
        (그 경우는 '저장분' 라벨이 붙는 캐시 경로가 담당한다.)
    (b) 이번 최신 관측보다 **미래인 누적분은 잘라낸다** — 안 그러면 창의
        기준점이 화면의 '현재'와 어긋난다.
    (c) 이번에 받은 게 **이미 최장 창을 덮으면 그대로 돌려준다** — 건강한
        원천(VIX·VKOSPI 는 300행+)에 누적분을 섞으면 얻는 것 없이 창의
        의미만 흔든다. 저장은 계속 해 둔다(그 원천이 훗날 무너질 때의 보험).
    """
    if not rows:
        return []
    path = _vol_series_path(key)
    merged: dict[str, float] = {}
    try:
        if path.exists():
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                merged = {str(d): float(v) for d, v in stored.items()
                          if v is not None}
    except Exception as exc:                                   # noqa: BLE001
        log.warning("market_timing: %s 누적 시계열 읽기 실패: %s", key, exc)
    for r in rows or []:
        d, c = r.get("date"), r.get("close")
        if d and c is not None:
            merged[str(d)[:10]] = float(c)
    if not merged:
        return list(rows or [])
    newest = max(str(r["date"])[:10] for r in rows if r.get("date"))
    out = [{"date": d, "close": merged[d]} for d in sorted(merged) if d <= newest]
    out = out[-_VOL_SERIES_MAX:]
    try:
        _VOL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({r["date"]: r["close"] for r in out}),
                        encoding="utf-8")
    except Exception as exc:                                   # noqa: BLE001
        log.warning("market_timing: %s 누적 시계열 저장 실패: %s", key, exc)
    return list(rows) if len(rows) > _VOL_LOOKBACKS[-1][1] else out


def _index_quote_row(ticker: str) -> list[dict]:
    """야후 **시세 메타**에서 현재 레벨 한 점 → [{date, close}]. 실패 시 [].

    날짜는 `regularMarketTime`(장마감 시각)을 **UTC 달력일**로 읽는다 —
    미 장마감 16:00 ET = 20:00~21:00 UTC 라 같은 날짜가 되고, 시간대
    데이터베이스 없이도 정확하다."""
    try:
        import yfinance as yf
        meta = yf.Ticker(ticker).history_metadata or {}
    except Exception as exc:                                   # noqa: BLE001
        log.debug("market_timing: %s 시세 메타 조회 실패: %s", ticker, exc)
        return []
    px, ts = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if px is None or ts is None:
        return []
    try:
        d = datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
        return [{"date": d, "close": float(px)}]
    except (TypeError, ValueError, OSError) as exc:
        log.debug("market_timing: %s 시세 메타 해석 실패: %s", ticker, exc)
        return []


# ── MOVE 심볼 사다리 + 정체 확인 ────────────────────────────────────────
# 사용자 2026-08-19: "혹시 야후파이낸스말고 다른곳에서 가져올수는 없어?"
# ICE BofA MOVE 는 유료 독점 지수라 검증 가능한 무료 비-야후 피드가 없다.
# 대신 같은 프로브(v4)에서 **캐럿 없는 `MOVE`** 가 20행·최신 당일을 줬다
# (`^MOVE` 는 두 질의 방식 모두 0행). 그런데 `MOVE` 는 **주식 티커로도 존재
# 가능한 이름**이라 그대로 갖다 쓰면 채권 변동성 자리에 엉뚱한 주가가 박힌다
# — 숫자를 지어내는 것과 같다. 그래서 대체 후보는 반드시 두 관문을 통과해야
# 쓴다: (a) 값이 MOVE 지수 대역인가 (b) 야후 메타가 **지수**로 보는가.
# 통과 못 하면 폐기하고 캐시로 간다 — 빈칸이 틀린 숫자보다 낫다.
_MOVE_SYMBOLS = ("^MOVE", "MOVE")
_MOVE_RANGE = (20.0, 400.0)   # 역사적 저점 ~36 · 고점 ~265 를 넉넉히 감싼 대역


def _is_index_symbol(ticker: str) -> bool | None:
    """야후 메타가 이 심볼을 **지수**로 보는가. 확인 불가면 None(모름)."""
    try:
        import yfinance as yf
        meta = yf.Ticker(ticker).history_metadata or {}
    except Exception as exc:                                   # noqa: BLE001
        log.debug("market_timing: %s 메타 조회 실패: %s", ticker, exc)
        return None
    kind = str(meta.get("instrumentType") or "").upper()
    return (kind == "INDEX") if kind else None


def fetch_move_rows() -> tuple[list, str | None]:
    """MOVE 후보를 순서대로 시도 — 검증 통과한 첫 심볼의 (행, 심볼).

    1순위 `^MOVE` 는 캐럿 네임스페이스라 주식과 겹칠 수 없어 대역만 본다.
    2순위부터는 **지수 확인까지** 요구한다(동명 주식 오염 차단)."""
    for i, sym in enumerate(_MOVE_SYMBOLS):
        try:
            rows = fetch_index_history(sym, days=400)
        except Exception as exc:                               # noqa: BLE001
            log.warning("market_timing: MOVE 후보 %s fetch 실패(%s)", sym, exc)
            continue
        if not rows:
            log.warning("market_timing: MOVE 후보 %s 히스토리 0행 — 다음 후보", sym)
            continue
        last = rows[-1]["close"]
        if not _MOVE_RANGE[0] <= last <= _MOVE_RANGE[1]:
            log.warning("market_timing: MOVE 후보 %s 최신값 %.2f 가 지수 대역"
                        " %s 밖 — 폐기(동명 주식 의심)", sym, last, _MOVE_RANGE)
            continue
        if i and _is_index_symbol(sym) is not True:
            log.warning("market_timing: MOVE 후보 %s 가 지수로 확인되지 않음"
                        " — 폐기(동명 주식 오염 차단)", sym)
            continue
        if i:
            log.info("market_timing: MOVE 를 대체 심볼 %s 로 받았다"
                     "(최신 %s)", sym, rows[-1]["date"])
        return _with_fresh_quote(sym, rows, _MOVE_RANGE), sym
    # 차트 시계열이 전부 비었어도 **시세 메타**엔 현재 레벨이 남아 있다
    # (2026-08-19 실측: 차트 1봉 → 차트 경로는 0행, 메타는 74.98).
    # 같은 대역 관문을 통과해야 쓴다 — 관문을 비켜 가는 뒷문이 되면 안 된다.
    q = _index_quote_row(_MOVE_SYMBOLS[0])
    if q and _MOVE_RANGE[0] <= q[-1]["close"] <= _MOVE_RANGE[1]:
        log.info("market_timing: MOVE 차트가 비어 시세 메타로 한 점 확보"
                 "(%s %.2f)", q[-1]["date"], q[-1]["close"])
        return q, _MOVE_SYMBOLS[0]
    if q:
        log.warning("market_timing: MOVE 시세 메타 %.2f 가 지수 대역 %s 밖 — 폐기",
                    q[-1]["close"], _MOVE_RANGE)
    return [], None


def _with_fresh_quote(ticker: str, rows: list[dict],
                      band: tuple[float, float]) -> list[dict]:
    """차트가 **낡았을 때** 시세 메타의 최신 한 점을 뒤에 붙인다.

    2026-08-20 실측: `^MOVE` 는 차트가 226행(07-17 에서 끊김)인데 메타는
    08-18 74.98 이다 — 둘 중 하나만 쓰면 '창은 있는데 값이 낡거나' '값은
    최신인데 창이 없거나' 가 된다. 둘을 합치면 둘 다 산다."""
    if not rows:
        return rows
    q = _index_quote_row(ticker)
    if not q or not band[0] <= q[-1]["close"] <= band[1]:
        return rows
    if q[-1]["date"] <= rows[-1]["date"]:
        return rows
    log.info("market_timing: %s 차트 최신 %s → 시세 메타 %s 한 점 추가",
             ticker, rows[-1]["date"], q[-1]["date"])
    return rows + q


# 시장별 지수 기준일이 **마지막 거래일**보다 뒤처졌는지 — 사용자 2026-08-20
# "왜 제때 못받아오는게 있어?"(KOSPI·니케이가 08-20 인데 기준 08-18).
# 야후가 KR/JP 종가를 하루 늦게 올리는 날이 있어 화면만 보면 구분이 안 된다.
# 휴장일 캘린더로 **기대 거래일**을 구해 비교하고, 뒤처진 만큼을 표기한다
# (캘린더가 없으면 판정하지 않는다 — 추측 금지).
# 기대치가 이미 '직전 세션'(오늘 장은 안 끝났을 수 있으므로)이라 여유는 0 이
# 맞다 — 사용자 2026-08-20 이 지적한 건 정확히 **1거래일** 뒤처진 화면이었다.
# 캘린더가 없어 주중으로 세는 경우만 연휴 오탐을 피해 1 을 더한다.
_IDX_GRACE_SESSIONS = 0


def _idx_stale(market: str, latest: str | None) -> str:
    """지수 기준일이 늦으면 ' ⚠️ N거래일 지연' 문구, 아니면 빈 문자열."""
    if not latest:
        return ""
    try:
        import html as _hh
        expected, grace = _expected_session(market)
        got = str(latest)[:10]
        if not expected:
            return ""
        if got > expected:
            # 오늘 장이 안 끝났는데 오늘 봉이 들어온 것 — 값이 틀린 게 아니라
            # **확정 전**이다. 분산일·FTD·MA 가 부분봉으로 계산되므로 화면이
            # 그걸 말해야 한다(사용자 2026-08-20 감사에서 전 시장이 이 상태).
            if _market_closed_today(market) is False:
                return (' <span style="color:#8ab4f8">🕒 장중(미확정)</span>')
            return ""
        if got == expected:
            return ""
        behind = _sessions_between(market, got, expected)
        if behind is None or behind <= grace:
            return ""
        return (f' <span style="color:#f0a020">⚠️ {behind}거래일 지연'
                f'(기대 {_hh.escape(expected)})</span>')
    except Exception as exc:                                   # noqa: BLE001
        log.debug("market_timing: %s 지수 신선도 판정 실패: %s", market, exc)
        return ""


# 시장별 IANA 타임존 — 마감 시각 판정용. 거래시간 문자열은
# `bot.market.MARKET_CONFIG[*]["trading_hours"]` 가 단일 출처이고, 여기선
# 그 문자열의 타임존 약어를 IANA 로 옮기기만 한다(DST 는 zoneinfo 가 처리).
_MARKET_TZ = {"US": "America/New_York", "KR": "Asia/Seoul", "JP": "Asia/Tokyo",
              "CN_A": "Asia/Shanghai", "HK": "Asia/Hong_Kong",
              "TW": "Asia/Taipei"}
_SESSION_CLOSE_GRACE_MIN = 20   # 마감 직후 원천 수록 여유


def _market_today(market: str):
    """그 시장 **현지 달력일**. ⚠️ KST 로 잡으면 미국이 하루 앞선다 —
    한국 08-20 05:00 은 뉴욕 08-19 16:00 이라 '오늘'이 다르다(직접 실측:
    US 기대세션이 08-20 으로 나와 아직 열리지도 않은 날을 기대했다)."""
    tzname = _MARKET_TZ.get(str(market).upper())
    if tzname:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tzname)).date()
        except Exception as exc:                               # noqa: BLE001
            log.debug("market_timing: %s 현지일 계산 실패: %s", market, exc)
    return _kst_now().date()


def _market_closed_today(market: str) -> bool | None:
    """그 시장의 **오늘 정규장이 이미 끝났는가**. 판정 불가면 None.

    ⚠️ 이게 없으면 `_expected_session` 이 오늘을 절대 기대치로 삼지 못해,
    장 마감 뒤에 들어온 **정상 종가**를 '미확정 봉'으로 오판한다
    (2026-08-20 감사가 KR/JP/TW/CN/HK 를 08-20 로 보고했을 때의 쟁점).
    """
    tzname = _MARKET_TZ.get(str(market).upper())
    if not tzname:
        return None
    try:
        from zoneinfo import ZoneInfo

        from bot.market import MARKET_CONFIG
        hours = str(MARKET_CONFIG[str(market).upper()]["trading_hours"])
        close_hm = hours.split("-", 1)[1].strip().split()[0]
        hh, mm = (int(x) for x in close_hm.split(":"))
        now = datetime.now(ZoneInfo(tzname))
        close_min = hh * 60 + mm + _SESSION_CLOSE_GRACE_MIN
        return (now.hour * 60 + now.minute) >= close_min
    except Exception as exc:                                   # noqa: BLE001
        log.debug("market_timing: %s 마감 판정 실패: %s", market, exc)
        return None


def _prev_weekday(d):
    while d.weekday() >= 5:          # 토(5)·일(6)
        d -= timedelta(days=1)
    return d


def _prev_session(market: str, date_str: str) -> str | None:
    """`date_str`(세션) **직전** 거래일."""
    try:
        from bot.market_calendar import add_trading_days
        return add_trading_days(market, date_str, -1)
    except Exception as exc:                                   # noqa: BLE001
        log.debug("market_timing: %s 직전 세션 계산 실패: %s", market, exc)
        return None


def _expected_session(market: str) -> tuple[str | None, int]:
    """(기대 최신 거래일, 허용 세션수). 휴장일 캘린더가 있으면 정확히,
    없으면 **주중 기준**으로 판정하되 연휴 오탐을 피하려 여유를 넓힌다 —
    캘린더가 없다고 판정을 통째로 포기하면 화면이 조용해진다(실수 #12)."""
    today = _market_today(market)      # ⚠️ KST 아님 — 시장 현지일
    # ⚠️ 오늘 장이 **이미 끝났으면 오늘이 마지막 완결 세션**이다. 이걸 빼면
    # 마감 뒤 정상 종가가 '기대보다 미래'로 보여 판정이 뒤집힌다.
    closed = _market_closed_today(market)
    try:
        from bot.market_calendar import (is_trading_day,
                                          last_session_on_or_before)
        t = today.isoformat()
        trading = is_trading_day(market, t)
        if trading is not None:
            if trading and closed:
                return t, _IDX_GRACE_SESSIONS
            # ⚠️ 과거 방향은 `last_session_on_or_before` 로. 예전엔
            # `add_trading_days(.., -1/0)` 을 썼는데 그 함수는 **앞방향**
            # 의미라 휴일·음수에서 미래 날짜를 돌려줬다(2026-08-20 실측:
            # KR -1 → 2026-09-07). 오늘 장이 안 끝났으면 오늘 **이전** 세션.
            base = (t if not trading else
                    (last_session_on_or_before(market, t) or t))
            exp = (last_session_on_or_before(market, t) if not trading
                   else _prev_session(market, base))
            if exp:
                return exp, _IDX_GRACE_SESSIONS
    except Exception as exc:                                   # noqa: BLE001
        log.debug("market_timing: %s 캘린더 조회 실패: %s", market, exc)
    if closed and today.weekday() < 5:
        return today.isoformat(), _IDX_GRACE_SESSIONS + 1
    return _prev_weekday(today - timedelta(days=1)).isoformat(), _IDX_GRACE_SESSIONS + 1


def _sessions_between(market: str, start: str, end: str) -> int | None:
    """start(제외) ~ end(포함) 사이 거래일 수. 캘린더 없으면 주중일 수."""
    try:
        from bot.market_calendar import add_trading_days
        cur, n = end, 0
        while cur and cur > start and n < 40:
            n += 1
            nxt = add_trading_days(market, cur, -1)
            if not nxt:
                break
            cur = nxt
        else:
            return n
    except Exception as exc:                                   # noqa: BLE001
        log.debug("market_timing: %s 세션수 계산 실패: %s", market, exc)
    try:
        a = datetime.strptime(start, "%Y-%m-%d").date()
        b = datetime.strptime(end, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    n, cur = 0, b
    while cur > a and n < 40:
        if cur.weekday() < 5:
            n += 1
        cur -= timedelta(days=1)
    return n


def fetch_volatility_snapshot() -> dict:
    """{"vix": {...}, "vkospi": {...}|None, "move": {...}|None}.

    VIX 는 네이버(메인 대시보드와 canonical 값 일치) → yfinance 2단 폴백.
    VIX·VKOSPI 는 CNN F&G 카드와 같은 형식으로 **전일·1주·1달·1년** 과거값을
    함께 싣는다(사용자 2026-08-16). 실패한 쪽만 생략한다."""
    out: dict = {}
    try:
        vix_hist = fetch_index_history("^VIX", days=400, min_rows=200)
        nv = _fetch_vix_naver()
        if nv is not None:
            out["vix"] = {"value": nv, "date": None, "source": "네이버(실시간)"}
        elif vix_hist:
            out["vix"] = {"value": vix_hist[-1]["close"], "date": vix_hist[-1]["date"],
                          "source": "yfinance(폴백)"}
        if out.get("vix"):
            # 과거값은 항상 히스토리에서 — 현재값 소스(네이버 실시간)와
            # 달라도 '전일 대비' 의 비교 대상은 종가 시계열이 맞다.
            out["vix"]["history"] = vol_history(_vol_series_merge("vix", vix_hist))
    except Exception as exc:
        log.debug("market_timing: VIX fetch failed: %s", exc)
    try:
        vk = fetch_vkospi_rows()
        if vk:
            out["vkospi"] = {"value": vk[-1]["close"], "date": vk[-1]["date"],
                             "source": "KIS",
                             "history": vol_history(
                                 _vol_series_merge("vkospi", vk))}
    except Exception as exc:
        log.debug("market_timing: VKOSPI fetch failed: %s", exc)
    try:
        # ⚠️ days=10 이면 1달(21)·1년(252) 창을 만들 수 없다 — VIX 와 같은
        # 기간 비교를 붙이려면 히스토리를 그만큼 받아야 한다(사용자 2026-08-19
        # "채권변동성도 VIX 처럼 기간으로"). 커버리지가 불안정한 지수라
        # min_rows 를 걸지 않는다 — 있는 창만 채우고 없으면 그 칸을 생략한다.
        move_hist, move_sym = fetch_move_rows()
        if move_hist:
            # ⚠️ 값·기준일은 **이번에 받은** 관측에서만 — 누적 시계열은
            # 창(전일·1주·…) 복원에만 쓴다. 누적분을 값으로 쓰면 원천이
            # 죽은 날 옛날 값이 '최신'으로 둔갑한다(그건 캐시 경로 담당).
            merged = _vol_series_merge("move", move_hist)
            # 어느 심볼로 받았는지 화면에 드러낸다 — 대체 심볼로 받은 걸
            # 'yfinance' 로만 적으면 화면이 출처를 숨기는 셈이다(규칙 10b).
            out["move"] = {"value": move_hist[-1]["close"],
                           "date": move_hist[-1]["date"],
                           "source": ("yfinance" if move_sym == _MOVE_SYMBOLS[0]
                                      else f"yfinance {move_sym}"),
                           "history": vol_history(merged)}
            _vol_cache_save("move", out["move"])
        else:
            # 조용히 사라지지 않는다 — 왜 없는지 로그에 남긴다(실수 #12).
            log.warning("market_timing: MOVE 후보 %s 전부 실패 — 캐시로 대체 시도",
                        list(_MOVE_SYMBOLS))
    except Exception as exc:
        log.warning("market_timing: MOVE fetch 실패(%s) — 캐시로 대체 시도", exc)
    if not out.get("move"):
        cached = _vol_cache_load("move")
        if cached:
            out["move"] = cached
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

    # 시장 폭(2026-07-26 사용자 추천, 2026-08-16 KR 추가) — 시장별로 분리해
    # 한쪽이 죽어도 다른 쪽은 살린다(기존 graceful 관례).
    breadth: dict = {}
    for _mkt in _BREADTH_SECTORS:
        try:
            _b = fetch_market_breadth(_mkt)
            if _b:
                breadth[_mkt] = _b
        except Exception as exc:
            log.debug("market_timing: breadth panel %s failed: %s", _mkt, exc)

    # 변동성 VIX+MOVE(2026-07-26 사용자 추천).
    volatility: dict = {}
    try:
        volatility = fetch_volatility_snapshot()
    except Exception as exc:
        log.debug("market_timing: volatility panel failed: %s", exc)

    # CNN Fear & Greed 지수(2026-07-26, 사용자 "이 fear and greed를 양쪽에
    # 똑같이 적용해줄수 없는거야" — 메인 대시보드 bot/dashboard.py 의 시장
    # 센티먼트 게이지와 동일 fetch_fear_greed() 재사용, canonical 값 일치
    # 보장). 실패해도 graceful(그 카드만 생략).
    sentiment: dict = {}
    try:
        from bot.fear_greed_client import fetch_fear_greed
        sentiment = fetch_fear_greed()
    except Exception as exc:
        log.debug("market_timing: sentiment panel failed: %s", exc)

    return {"markets": markets, "macro": macro, "crypto": crypto, "cot": cot,
            "breadth": breadth, "volatility": volatility, "sentiment": sentiment}


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
<div class="sub" style="margin:4px 0 0">기준 {_h.escape(str(m.get("latest_date","—")))}{_idx_stale(mkt, m.get("latest_date"))} ·
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

    # 시장 폭 — 시장별 카드(사용자 2026-08-16 "US 위에 KR"). HTML 이 한 벌뿐이라
    # 헬퍼로 뽑아 두 번 호출한다(복붙 금지).
    # 설명 문구의 섹터 나열·개수는 **레지스트리에서 만든다** — 손으로 적으면
    # 티커를 하나 바꾸는 순간 거짓말이 된다(2026-08-16 독립 리뷰).
    _BREADTH_WHY = {
        "US": "개별종목(500종목) breadth 의 섹터-레벨 근사(비용상 전수스캔 대신 채택, "
              "문서화된 스코프).",
        "KR": "KRX 업종 기준(GICS 강제 매핑이 아니라 한국 시장의 실제 업종 구조). "
              "삼성전자·하이닉스 비중이 큰 시장이라 '소수 대형주만 지수를 방어 중인지' "
              "판별에 특히 유용.",
    }

    def _breadth_card(mkt: str, b: dict) -> str:
        """mkt 는 **호출부가 명시**한다 — payload 에서 꺼내 기본값 'US' 를 쓰면
        market 키가 없는 KR payload 가 US 제목·US 설명으로 렌더된다(독립 리뷰)."""
        sectors = _BREADTH_SECTORS.get(mkt) or {}
        if not b:
            # 등록된 시장인데 데이터가 통째로 없으면 **그 사실을 보여준다**.
            # 카드가 그냥 사라지면 안내문("KR=KODEX 13개")과 화면이 어긋난다.
            if not sectors:
                return ""
            return (f'<div class="panel"><div class="panel-title">📊 시장 폭 '
                    f'(섹터 breadth, {_h.escape(mkt)})</div>'
                    f'<div class="sub">데이터 없음 — 섹터 ETF 히스토리를 하나도 '
                    f'받지 못했습니다(티커·네트워크 확인 필요).</div></div>')
        if b.get("pct_above_50dma") is None:
            return ""

        def _pct(key: str) -> str:
            v = b.get(f"pct_above_{key}")
            if v is None:
                return "—"
            # (상회/집계) — 퍼센트와 **같은 분수**를 적는다. 옛 코드는
            # (집계/표본) 을 적어 세 지표가 전부 (13/13) 으로 같았다.
            up, n = b.get(f"above_{key}"), b.get(f"counted_{key}")
            if up is None or n is None:
                return f"{v:.0f}%"
            # 집계 수가 표본보다 적으면(신규상장 ETF 는 200일치가 없다) 그
            # 사실도 함께 밝힌다 — '표본 13개'라 써놓고 11개로 계산한 값을
            # 보여주는 일이 없게.
            total = b.get("n_sectors", 0)
            short = "" if n == total else f" · 집계 {n}/{total}"
            return f"{v:.0f}% <small>({up}/{n}{short})</small>"

        _miss = b.get("sectors_missing") or []
        _miss_html = (f'<div class="sub" style="margin:4px 0 0">⚠️ 제외: '
                      f'{_h.escape("·".join(_miss))} (데이터 없음 — 티커 확인 필요)</div>'
                      if _miss else "")
        _names = "·".join(sectors.values())
        return f"""
<div class="panel"><div class="panel-title">📊 시장 폭 (섹터 breadth, {_h.escape(mkt)})</div>
<div class="stat-grid">
<div class="stat"><div class="k">20일선 상회 섹터</div><div class="v">{_pct("20dma")}</div></div>
<div class="stat"><div class="k">50일선 상회 섹터</div><div class="v">{_pct("50dma")}</div></div>
<div class="stat"><div class="k">200일선 상회 섹터</div><div class="v">{_pct("200dma")}</div></div>
<div class="stat"><div class="k">표본</div><div class="v" style="font-size:14px">{_h.escape(str(b.get("source_label","섹터 ETF")))} {b.get("n_sectors",0)}개</div></div>
</div>{_miss_html}
<div class="note">{_h.escape(str(b.get("source_label","섹터 ETF")))} {len(sectors)}개({_h.escape(_names)})
중 자신의 20/50/200일 이평선 위에 있는 비율 — {_BREADTH_WHY.get(mkt, "")}
20일=단기 모멘텀·50일=중기·200일=장기 추세. 낮으면 소수 대형주만 지수를 방어 중일 가능성,
높으면 전반적 참여. <b>⚠️ 시장 간 %를 직접 비교하지 말 것</b> — 표본(SPDR/GICS vs KODEX/KRX
업종)과 섹터 수가 달라 같은 값이 같은 의미가 아니다. 같은 시장의 시계열 변화를 볼 것.</div></div>"""

    breadth = data.get("breadth") or {}
    # KR 을 US 위에(사용자 지시). 없는 시장은 빈 문자열이라 자동 생략.
    breadth_card = _breadth_card("KR", breadth.get("KR") or {}) + _breadth_card(
        "US", breadth.get("US") or {})

    vol = data.get("volatility", {})
    vol_card = ""
    if vol:
        vix = vol.get("vix")
        vkospi = vol.get("vkospi")
        move = vol.get("move")

        def _vol_panel(title, rec, note):
            """CNN F&G 카드와 **같은 형식** — 현재 + 전일·1주·1달·1년
            (사용자 2026-08-16). 없는 창은 칸 자체를 만들지 않는다."""
            src = rec.get("source", "")
            # 종가 기반 값은 **며칠 종가인지**를 같이 낸다(규칙 10b).
            age = _vol_age_days(rec.get("date"))
            if rec.get("date"):
                src = f'{src} · {str(rec["date"])[5:]} 종가' if src \
                    else f'{str(rec["date"])[5:]} 종가'
            stale_note = ""
            if age is not None and age > _VOL_STALE_DAYS:
                src += " ⚠"
                stale_note = (f'<div class="note">⚠️ 이 지수는 원천 시계열이 '
                              f'<b>{age}일째</b> 갱신되지 않았습니다 — 아래 '
                              f'현재·전일·1주·1달·1년은 모두 '
                              f'<b>{rec["date"]} 종가</b>를 기준으로 뒤로 센 '
                              f'값입니다(오늘 기준이 아닙니다).</div>')
            cells = (f'<div class="stat"><div class="k">현재'
                     f'{f" ({_h.escape(src)})" if src else ""}</div>'
                     f'<div class="v">{rec["value"]:.1f}</div></div>')
            hist = rec.get("history") or {}
            for _lb, _ in _VOL_LOOKBACKS:
                if hist.get(_lb) is not None:
                    cells += (f'<div class="stat"><div class="k">{_lb}</div>'
                              f'<div class="v" style="font-size:16px">'
                              f'{hist[_lb]:.1f}</div></div>')
            return (f'<div class="panel"><div class="panel-title">{title}</div>'
                    f'<div class="stat-grid">{cells}</div>'
                    f'{stale_note}'
                    f'<div class="note">{note}</div></div>')

        # ⚠️ 국내(VKOSPI)를 위, 미국(VIX)을 아래 — 사용자 2026-08-16
        # "현재 VIX 는 미국꺼니까 그 VIX 바로 위에 VKOSPI 를".
        if vkospi:
            vol_card += _vol_panel(
                "🌪️ 변동성 — 국내 (VKOSPI)", vkospi,
                "VKOSPI = 코스피200 옵션 내재변동성(KRX 산출) — 국내 증시의 "
                "공포지수. VIX 와 <b>직접 비교하지 마세요</b>: 기초자산·산출 "
                "옵션군이 달라 같은 값이 같은 긴장도가 아닙니다.")
        if vix:
            vol_card += _vol_panel(
                "🌪️ 변동성 — 미국 (VIX)", vix,
                "VIX = S&amp;P 500 옵션 내재변동성(공포지수). 현재값은 메인 "
                "대시보드와 동일 네이버 소스(canonical 일치)이고, 과거 비교값은 "
                "종가 시계열 기준입니다.")
        if move:
            # VIX·VKOSPI 와 **같은 패널 함수**를 쓴다 — 따로 만들었더니 기간
            # 비교가 이 카드에만 없었다(사용자 2026-08-19).
            vol_card += _vol_panel(
                "🌪️ 채권 변동성 (MOVE)", move,
                "MOVE = 미국 국채 옵션의 내재변동성(ICE BofA MOVE Index) — "
                "<b>채권시장의 공포지수</b>입니다. 1개월 만기 국채 옵션"
                "(2·5·10·30년물)에서 역산한 연율 변동성을 <b>bp(베이시스포인트)"
                "</b>로 나타내므로, %로 표시되는 VIX 와 <b>단위가 달라 숫자를 "
                "직접 비교할 수 없습니다</b>."
                "<br><b>읽는 법</b> — 대략 80 아래면 금리시장이 잠잠한 국면, "
                "120 위면 불안이 커진 국면으로 봅니다(2023년 은행 사태 때 "
                "190 부근까지 치솟은 적이 있습니다). 절대 수준보다 "
                "<b>자기 시계열 대비 방향</b>이 더 유용합니다 — 옆 칸의 "
                "전일·1주·1달·1년과 비교하세요."
                "<br><b>왜 주식 화면에 있나</b> — 금리 변동성이 뛰면 할인율이 "
                "흔들려 이익이 먼 미래에 몰린 <b>장기 성장주·기술주·반도체가 "
                "먼저 반응</b>하는 경우가 많습니다. 주가지수만 보면 안 보이는 "
                "위험이 채권 쪽에서 먼저 나타나는지 확인하는 용도입니다."
                "<br><span style=\"opacity:.75\">현재값·과거 비교값 모두 종가 "
                "시계열 기준. 이 지수는 <b>야후 파이낸스(ICE BofA MOVE 미러)</b>"
                "에서 받는데 긴 기간 요청이 간헐적으로 비거나 낡게 오므로, "
                "짧은 기간으로 한 번 더 물어 더 최신인 쪽을 씁니다. 그래도 "
                "비면 <b>대체 심볼</b>로 한 번 더 받아 보되, 값이 지수 대역에 "
                "들어오고 야후 메타가 <b>지수</b>로 확인해 준 경우에만 씁니다"
                "(같은 이름의 주식이 섞여 들어오지 않게). 대체 심볼로 받은 "
                "값은 '현재' 옆 출처에 심볼이 함께 표시됩니다. 끝내 못 받으면 "
                "마지막 성공분으로 카드를 유지하고 기준일을 표시합니다.</span>")

    sent = data.get("sentiment") or {}
    sent_card = ""
    if sent.get("score") is not None:
        _prevs = [("전일", sent.get("prev_close")), ("1주", sent.get("prev_1w")),
                 ("1달", sent.get("prev_1m")), ("1년", sent.get("prev_1y"))]
        _prev_rows = "".join(
            f'<div class="stat"><div class="k">{_l}</div><div class="v" style="font-size:16px">{_v}</div></div>'
            for _l, _v in _prevs if _v is not None)
        _stale = " · ⚠️ 최신 수집 실패(마지막 성공분)" if sent.get("stale") else ""
        _ts = _h.escape(str(sent.get("ts") or ""))
        sent_card = f"""
<div class="panel"><div class="panel-title">🎯 시장 센티먼트 (CNN Fear &amp; Greed)</div>
<div class="stat-grid">
<div class="stat"><div class="k">현재</div><div class="v">{sent["score"]} <span style="font-size:13px">{_h.escape(sent.get("rating_kr",""))}</span></div></div>
{_prev_rows}
</div>
<div class="note">CNN Fear &amp; Greed 지수(0=극단공포 · 100=극단탐욕) — 메인 대시보드 시장 센티먼트
게이지와 동일 소스(canonical 일치, 사용자 2026-07-26 "양쪽에 똑같이"). 기준 {_ts}{_stale}</div></div>"""

    payload = _json.dumps(data, ensure_ascii=False, default=str).replace("<", "\\u003c")
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>시장타이밍</title>
{_theme_head()}
{_BOARD_CSS}</head><body><div class="wrap">
{_NAV}
<h1>🚦 <em>시장타이밍</em> 보드</h1>
<p class="sub">분산일(IBD)·팔로우스루데이(O'Neil)·시장폭·변동성·센티먼트·매크로 레짐·크립토·COT — 데이터 적용시각 {ts} ·
소스 yfinance + FRED + CoinGecko + CFTC + 네이버(VIX) + CNN(센티먼트)(전부 무료, 3시간 주기 자동 갱신)</p>
<details class="guide"><summary>ℹ️ 사용법 — 처음이면 펼쳐 보세요</summary>
<b>1) 분산일(Distribution Day)</b> — 종가 -0.2%+ 하락 & 거래량 증가 = 기관 매도 신호.
D5/D15/D25 = 최근 5/15/25거래일 내 활성 건수. 위험도는 셋 중 가장 높은 신호로 판정
(예: D5≥2 또는 D15≥3 만으로도 HIGH, D25 가 낮아도 무관) — CAUTION D25≥3 · HIGH
D25≥5·D15≥3·D5≥2 중 하나만 충족 · SEVERE D25≥6·D15≥4 중 하나만 충족. 높을수록
경계.<br>
<b>2) 팔로우스루데이(FTD)</b> — 3%+ 조정(3거래일+ 연속 하락) 후 반등 4~10일째
+1.25%+ 상승·거래량 증가 = 바닥 확인 신호(O'Neil 방법론). 🟢 FTD 확정만 유효 신호,
나머지는 대기/무효.<br>
<b>3) 시장 폭</b> — 섹터 ETF 중 20/50/200일선 상회 비율(개별종목 breadth 의 섹터-레벨
근사) — 20일=단기 모멘텀·50일=중기·200일=장기 추세. 낮으면 소수 대형주만 지수 방어,
높으면 전반적 참여. <b>KR</b>=KODEX 섹터 12개(KRX 업종) · <b>US</b>=SPDR GICS 11개.
표본이 다르므로 두 시장의 %를 직접 비교하지 말 것(같은 시장의 시계열 변화를 볼 것).<br>
<b>4) 변동성(VIX·MOVE)</b> — VIX=주식 공포지수(메인 대시보드와 동일 네이버 소스),
MOVE=채권 변동성(ICE BofA, bp 단위). 종가 기반 카드는 '현재' 칸에 <b>몇 월 며칠
종가</b>인지 같이 적습니다 — 원천 시계열이 며칠째 멈춰 있으면 <b>⚠ 표시와 지연
일수</b>를 붙여, 낡은 값이 오늘 값처럼 보이지 않게 합니다. 원천이 한 사이클 통째로
비면 <b>마지막 성공분</b>으로 카드를 유지하고 '현재' 칸에 「MM-DD 기준 저장분」이라고
적습니다(7일 넘게 낡으면 그때는 생략).<br>
<b>5) 시장 센티먼트</b> — CNN Fear &amp; Greed 지수(메인 대시보드와 동일 소스). VIX 와는 다른
지표(VIX=변동성 하나, F&amp;G=7개 컴포넌트 종합 심리지수) — 혼동 주의.<br>
<b>6) 매크로 레짐</b> — 크로스에셋 비율 다수결로 시장 국면(집중/확산/긴축/인플레) 판정,
투표 부족·동률이면 Transitional(판단보류)로 표시 — 참고용.<br>
<b>7) 크립토 레짐</b> — BTC 중심 0-100 점수. 현재는 ATH대비낙폭 컴포넌트만 실제 반영
(SMA추세·도미넌스·모멘텀은 설계됐으나 데이터소스 미연결 — 추후 확장, 참고용).<br>
<b>8) COT 역발상 게이트</b> — S&amp;P500 E-mini 대형투기자 포지셔닝(CFTC 주간보고,
선물전용) 트레일링 3년(156주) 백분위. 극단 쏠림(≥80/≤20)은 과거 반전 빈도가 높았던
구간이라는 참고 신호일 뿐(선물전용).<br>
자동 신호이므로 참고용 — 확정 판단 금지.
</details>
{cards}
{breadth_card}
{vol_card}
{sent_card}
{macro_card}
{crypto_card}
{cot_card}
<div class="footer">분산일·FTD·시장폭·변동성·센티먼트·매크로레짐·크립토·COT — 신호는 참고용(투자 판단 아님) · NOAH</div>
</div>
<script id="mt-data" type="application/json">{payload}</script>
</body></html>"""


def regenerate_market_timing() -> None:
    """market_timing.html 재생성 — 자정/3시간 주기 + startup. 실패해도 기존
    파일 유지(graceful). ⚠️ 네트워크 다수콜 — to_thread 필수(이벤트루프 차단 금지)."""
    from bot.dashboard import ARCHIVE_ROOT, _inject_update_banner
    try:
        data = _load_market_timing()
        html = _inject_update_banner(render_market_timing_page(data))
        (ARCHIVE_ROOT / "market_timing.html").write_text(html, encoding="utf-8")
        log.info("market_timing: market_timing.html regenerated")
    except Exception:
        log.exception("market_timing: regen failed")
