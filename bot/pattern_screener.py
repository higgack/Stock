"""VCP·모멘텀버스트·PEAD 패턴 탐지 — 순수함수 (2026-07-26).

claude-trading-skills(tradermonty/claude-trading-skills) 저장소의
vcp-screener / stockbee-momentum-burst-screener / pead-screener 스킬 핵심
탐지 로직을 이식. 유료 FMP API 대신 이미 시스템에 있는 OHLCV 이력
(bot/pykrx_client.get_kr_trend_template · bot/stock_screener.get_yf_trend_template
가 이미 fetch 하는 동일 일봉 데이터)으로 시장중립(market-neutral) 동작
— 어느 시장(KR/US/JP 등)이든 동일 순수함수(universal).

- detect_vcp: Minervini Volatility Contraction Pattern. Stage-2 추세 여부는
  호출부가 trend_template_from_series 의 tt 로 판정해 stage2_ok 로 주입
  (이 함수는 그 결과를 valid_vcp 게이트에만 반영 — 추세 판정 자체는 중복 안 함).
  ATR 반전폭 기반 zigzag 로 스윙 고점/저점을 뽑아 2-4회 연속 조정(각 조정폭이
  직전의 contraction_ratio 이하)을 탐지 — 원 스킬 기본값(min-contractions=2,
  contraction-ratio=0.70, t1-depth-min=10%, min-contraction-days=5,
  lookback-days=120, breakout-volume-ratio=1.5, atr-multiplier=1.5) 그대로.
- detect_momentum_burst: Stockbee '4% Days' — 당일 +4%(또는 저가주 +$0.90)
  상승 + 평균 대비 거래량 급증. range_expansion(선택)은 품질 신호일 뿐
  valid 게이트는 아님(Stockbee 원 룰이 range expansion 을 확인신호로만 씀).
- detect_pead: 실적 갭업 후 드리프트(주간 캔들 추세지속) 후보 — 순수
  가격/거래량 휴리스틱. 실제 실적일 교차검증(bot/earnings_calendar.py)은
  상위 호출부 몫 — 이 함수는 '갭업(+거래량급증)+이후 미붕괴 드리프트'
  패턴 자체만 판정(문서화된 스코프 한정: 진짜 실적일 없이도 유사 패턴이면
  탐지될 수 있음 — 실적캘린더 교차확인 시 정밀도 향상 여지).
"""
from __future__ import annotations

from typing import Optional


def _avg(seq) -> float:
    return sum(seq) / len(seq) if seq else 0.0


def _atr(highs, lows, closes, period: int = 14) -> float:
    """평균실질변동폭(ATR, 단순평균 버전) — zigzag 반전 임계값 산정용."""
    n = len(closes)
    if n < 2:
        return 0.0
    trs = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    window = trs[-period:] if len(trs) >= period else trs
    return _avg(window)


def zigzag_swings(highs, lows, min_move: float) -> list:
    """ATR 반전폭(min_move) 기반 zigzag — 교대 고점/저점 스윙 리스트
    [(idx, 'high'|'low', price), ...] (시간순). min_move<=0 이면 빈 리스트.

    ⚠️ 스윙은 **서로 다른 봉** 사이에서만 확정한다. 일중 변동폭이 min_move 를
    넘는 큰 봉 하나는 같은 i 에서 두 블록이 모두 발화해 (i,'high')·(i,'low') 를
    연속으로 내놓을 수 있었다 — 그러면 시간폭 0 짜리 '스윙'이 만들어지고,
    피보나치 기준 다리가 그 한 봉의 고저가 되어 화면에 `07-30 → 07-30` 처럼
    시작·끝 날짜가 같은 다리가 찍혔다(사용자 2026-08-02 실측: 1년 일봉인데
    직전 1봉짜리 다리를 잡아 '100% 초과 — 통째로 무효화'). 애초에 봉 안에서는
    고가·저가의 **선후를 알 수 없어** 방향을 단정할 수 없으므로, 직전 피벗과
    같은 봉이면 확정하지 않고 후보로 계속 추적한다(더 뒤 봉에서 극점이
    갱신되면 그때 정상 확정 — 모듈 정책 '진행 중 다리는 미확정'과 동일 취지).
    zigzag 를 공유하는 VCP 탐지도 같은 이유로 0봉 수축이 사라진다."""
    n = len(highs)
    if n == 0 or min_move <= 0:
        return []
    swings = []
    direction = 0   # 0=미정(양방향 후보 추적) · 1=고점탐색중 · -1=저점탐색중
    cand_high_idx, cand_high = 0, highs[0]
    cand_low_idx, cand_low = 0, lows[0]
    for i in range(1, n):
        if direction <= 0:
            if lows[i] < cand_low:
                cand_low, cand_low_idx = lows[i], i
            if highs[i] - cand_low >= min_move and (
                    not swings or swings[-1][0] != cand_low_idx):
                swings.append((cand_low_idx, "low", cand_low))
                direction = 1
                cand_high, cand_high_idx = highs[i], i
        if direction >= 0:
            if highs[i] > cand_high:
                cand_high, cand_high_idx = highs[i], i
            if cand_high - lows[i] >= min_move and (
                    not swings or swings[-1][0] != cand_high_idx):
                swings.append((cand_high_idx, "high", cand_high))
                direction = -1
                cand_low, cand_low_idx = lows[i], i
    return swings


def detect_vcp(closes, highs, lows, volumes, *, stage2_ok: bool = True,
                lookback_days: int = 120, min_contractions: int = 2,
                max_contractions: int = 4, contraction_ratio: float = 0.70,
                t1_depth_min_pct: float = 10.0, min_contraction_days: int = 5,
                breakout_volume_ratio: float = 1.5,
                atr_multiplier: float = 1.5) -> Optional[dict]:
    """Minervini VCP 탐지 — 모듈 독스트링 참조. None=이력부족(<30봉)."""
    n = len(closes)
    if n < 30 or len(highs) < n or len(lows) < n or len(volumes) < n:
        return None
    win = min(lookback_days, n)
    c_win, h_win, l_win, v_win = closes[-win:], highs[-win:], lows[-win:], volumes[-win:]
    atr = _atr(h_win, l_win, c_win, period=14)
    if atr <= 0:
        return None
    swings = zigzag_swings(h_win, l_win, atr * atr_multiplier)

    legs = []
    for i in range(len(swings) - 1):
        if swings[i][1] == "high" and swings[i + 1][1] == "low":
            hi_idx, _, hi_price = swings[i]
            lo_idx, _, lo_price = swings[i + 1]
            if hi_price <= 0:
                continue
            legs.append({
                "hi_idx": hi_idx, "lo_idx": lo_idx, "hi": hi_price, "lo": lo_price,
                "depth_pct": (hi_price - lo_price) / hi_price * 100.0,
                "span_days": lo_idx - hi_idx,
            })

    if not legs:
        return {"valid_vcp": False, "num_contractions": 0,
                "contraction_depths_pct": [], "pivot": None, "stop": None,
                "breakout": False, "execution_state": "None", "score": 0}

    # 최신 조정부터 역순으로 '직전 조정의 contraction_ratio 이하'인 연속 체인 추출.
    recent = legs[::-1]
    chain = [recent[0]]
    for leg in recent[1:]:
        prev = chain[-1]   # prev = leg 보다 시간상 더 최근(이미 확정된 조정)
        if (leg["span_days"] >= min_contraction_days
                and prev["depth_pct"] <= leg["depth_pct"] * contraction_ratio + 1e-9):
            chain.append(leg)
        else:
            break
    chain = chain[::-1]   # 오래된→최신 순 복귀 (chain[0]=T1, chain[-1]=최신조정)

    num = len(chain)
    valid = (min_contractions <= num <= max_contractions
             and chain[0]["depth_pct"] >= t1_depth_min_pct
             and chain[-1]["span_days"] >= min_contraction_days)

    last_leg = chain[-1]
    pivot, stop = last_leg["hi"], last_leg["lo"]
    last_close, last_vol = c_win[-1], v_win[-1]
    avg_vol_before = _avg(v_win[max(0, len(v_win) - 51):-1]) if len(v_win) > 1 else 0.0
    vol_ok = avg_vol_before > 0 and last_vol >= avg_vol_before * breakout_volume_ratio
    breakout = valid and last_close > pivot and vol_ok

    if not valid:
        state = "None"
    elif last_close < stop:
        state = "Invalid"
    elif breakout:
        state = "Breakout"
    elif last_close <= pivot:
        state = "Pre-breakout"
    elif last_close > pivot * 1.10:
        state = "Extended"
    else:
        state = "Early-post-breakout"

    score = 0
    if valid:
        score = 40
        score += min(20, (num - min_contractions) * 10)             # 조정 횟수 보너스
        tightness = max(0.0, 1 - last_leg["depth_pct"] / max(chain[0]["depth_pct"], 1e-9))
        score += round(tightness * 25)                              # 최종조정 수축도
        if vol_ok:
            score += 15                                             # 거래량 확인
        if state == "Breakout":
            score += 20                                             # 돌파 확정
        score = max(0, min(100, score))

    return {
        "valid_vcp": bool(valid and stage2_ok),
        "num_contractions": num,
        "contraction_depths_pct": [round(l["depth_pct"], 1) for l in chain],
        "pivot": round(pivot, 4), "stop": round(stop, 4),
        "breakout": bool(breakout and stage2_ok),
        "execution_state": state if stage2_ok else "Not-Stage2",
        "score": score if stage2_ok else 0,
    }


def detect_momentum_burst(closes, volumes, *, highs=None, lows=None,
                           volume_window: int = 50, pct_threshold: float = 4.0,
                           dollar_threshold: float = 0.90,
                           volume_surge_ratio: float = 1.5,
                           range_days: int = 3) -> Optional[dict]:
    """Stockbee 모멘텀 버스트('4% Days') 탐지 — 모듈 독스트링 참조.
    None=이력부족(<volume_window+2봉)."""
    n = len(closes)
    if n < volume_window + 2 or len(volumes) < n:
        return None
    prev_close, last_close = closes[-2], closes[-1]
    if prev_close <= 0:
        return None
    pct_change = (last_close - prev_close) / prev_close * 100.0
    dollar_change = last_close - prev_close
    avg_vol = _avg(volumes[-(volume_window + 1):-1])
    volume_ratio = (volumes[-1] / avg_vol) if avg_vol > 0 else 0.0
    volume_ok = volume_ratio >= volume_surge_ratio
    pct_trigger = pct_change >= pct_threshold and volume_ok
    dollar_trigger = dollar_change >= dollar_threshold and volume_ok
    trigger = "pct" if pct_trigger else ("dollar" if dollar_trigger else None)
    valid = trigger is not None

    range_expansion = None
    if (highs is not None and lows is not None
            and len(highs) >= range_days + 1 and len(lows) >= range_days + 1):
        today_range = highs[-1] - lows[-1]
        prior_ranges = [highs[-(i + 2)] - lows[-(i + 2)] for i in range(range_days)]
        range_expansion = bool(prior_ranges) and today_range > max(prior_ranges)

    score = 0
    if valid:
        score = 50
        score += min(20, round(max(0.0, pct_change - pct_threshold) * 2))
        score += min(20, round(max(0.0, volume_ratio - volume_surge_ratio) * 10))
        if range_expansion:
            score += 10
        score = max(0, min(100, score))

    return {
        "valid": valid, "pct_change": round(pct_change, 2),
        "dollar_change": round(dollar_change, 4),
        "volume_ratio": round(volume_ratio, 2),
        "range_expansion": range_expansion, "trigger": trigger, "score": score,
    }


def detect_pead(closes, opens, volumes, *, lookback_days: int = 20,
                 gap_threshold_pct: float = 5.0, volume_window: int = 50,
                 volume_surge_ratio: float = 1.5) -> Optional[dict]:
    """실적 갭업 후 드리프트(PEAD) 후보 탐지 — 모듈 독스트링 참조.
    None=이력부족(<lookback_days+volume_window+2봉)."""
    n = len(closes)
    if n < lookback_days + volume_window + 2 or len(opens) < n or len(volumes) < n:
        return None
    win_start = n - lookback_days - 1
    best = None
    for i in range(max(1, win_start), n - 1):   # n-1: 최소 1거래일 드리프트 관측 필요
        prev_close = closes[i - 1]
        if prev_close <= 0:
            continue
        gap_pct = (opens[i] - prev_close) / prev_close * 100.0
        if gap_pct < gap_threshold_pct:
            continue
        avg_vol = _avg(volumes[max(0, i - volume_window):i])
        if avg_vol <= 0 or volumes[i] < avg_vol * volume_surge_ratio:
            continue
        if best is None or i > best["idx"]:
            best = {"idx": i, "gap_pct": gap_pct, "gap_close": closes[i],
                    "gap_floor": min(opens[i], closes[i])}

    if best is None:
        return {"valid": False, "gap_date_days_ago": None, "gap_pct": None,
                "drift_pct": None, "score": 0}

    last_close = closes[-1]
    collapsed = any(closes[j] < best["gap_floor"] for j in range(best["idx"] + 1, n))
    drift_pct = (last_close - best["gap_close"]) / best["gap_close"] * 100.0
    valid = (not collapsed) and drift_pct > 0
    score = 0
    if valid:
        score = 40 + min(30, round(best["gap_pct"])) + min(30, round(drift_pct * 2))
        score = max(0, min(100, score))

    return {
        "valid": valid, "gap_date_days_ago": n - 1 - best["idx"],
        "gap_pct": round(best["gap_pct"], 2), "drift_pct": round(drift_pct, 2),
        "score": score,
    }


def rsi14_from_closes(closes) -> Optional[float]:
    """Wilder RSI(14) — 마지막 값만 반환(순수, chart_data.py _series_payload
    의 RSI(14) 와 동일 알고리즘 계열이나 시딩 방식이 다름: 여기는 고전
    Wilder 시딩(첫 14봉 단순평균 시드 후 지수평활) — 정상상태로 수렴하면
    두 방식 값은 사실상 같아짐(스크리너 임계값 판정 용도로 무시 가능한
    오차). None=이력부족(<15봉)."""
    n = len(closes)
    if n < 15:
        return None
    gains, losses = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:14]) / 14
    avg_loss = sum(losses[:14]) / 14
    alpha = 1.0 / 14
    for i in range(14, len(gains)):
        avg_gain += alpha * (gains[i] - avg_gain)
        avg_loss += alpha * (losses[i] - avg_loss)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def merge_into_trend_result(result: dict, closes, opens, highs, lows, volumes) -> None:
    """trend_template_from_series() 결과 dict 에 VCP/모멘텀버스트/PEAD 필드를
    in-place 병합 — bot/pykrx_client.get_kr_trend_template 과
    bot/stock_screener.get_yf_trend_template 이 이미 fetch 한 동일 일봉
    데이터를 재사용(추가 API 호출 없음). 각 탐지 실패(결과 None)는 무시
    (해당 필드 미기록 — 조건식이 없는 값으로 취급돼 스크리너에서 자동 탈락)."""
    stage2_ok = bool(result.get("tt"))
    vcp = detect_vcp(closes, highs, lows, volumes, stage2_ok=stage2_ok)
    if vcp is not None:
        result["vcp_valid"] = 1.0 if vcp["valid_vcp"] else 0.0
        result["vcp_score"] = float(vcp["score"])

    mb = detect_momentum_burst(closes, volumes, highs=highs, lows=lows)
    if mb is not None:
        result["mb_valid"] = 1.0 if mb["valid"] else 0.0
        result["mb_score"] = float(mb["score"])

    pead = detect_pead(closes, opens, volumes)
    if pead is not None:
        result["pead_valid"] = 1.0 if pead["valid"] else 0.0
        result["pead_score"] = float(pead["score"])

    rsi = rsi14_from_closes(closes)
    if rsi is not None:
        result["rsi14"] = rsi
