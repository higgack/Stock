"""엘리엇 파동 · 피보나치 되돌림 — 순수함수 (2026-07-29).

Credit Suisse "Technical Analysis - Explained"(Global Technical Research)
튜토리얼 p23~p31 근거. 문서에 **실제로 적힌 것만** 상수화하고, 문서에 없는
판정규칙은 출처를 아래에 명시한다(실수#12 '데이터 vs 환각' — 문서에 없는 걸
문서 근거인 양 쓰지 않기 위해 출처를 코드에 박아둠).

규칙 출처 구분
  [CS p29] 피보나치 수열 1,2,3,5,8,13,21,34,55,89,144,233,377,610,987,1597,2584
           및 비율 — 인접항 0.618 / 1.618(황금비), 교대항 0.382 / 2.618.
  [CS p30] Wave correlations(파동 상관) — 이 모듈 점수화의 핵심:
             · 파동1&2   : W2 = W1 × (0.618 또는 0.382)
             · 파동1&3   : W3 = W1 × (1.618 또는 2.618)
             · 파동3&4   : W4 = W3 × (0.382 또는 0.618)
             · 파동1~3&5 : W5 = (파동1 시작→파동3 끝 순이동) × (1.0 또는 1.618)
             · 조정 A·B·C: B = A × (0.382 또는 0.618) · C = A × (1.0 또는 1.618)
           ⚠️ p30 은 도형(diagram)이라 PDF 텍스트 추출이 뒤섞여 나온다. 위 대응은
           그 도형의 통상 해석이며, 특히 '파동1~3 & 5' 의 기준구간을 (0→3 순이동)
           으로 잡은 것은 관례적 해석임을 명시(문서가 문장으로 못박지 않음).
  [CS p28] Head&Shoulders 의 B 반등 = 5→A 하락의 "50% to 61.80%" → 되돌림
           레벨에 0.5 포함 근거.
  [CS p31] a-b-c 조정 전체가 직전 5파 구조를 61.80% 되돌린 실례 + "wave c was
           equal in length to wave a"(C/A = 1.0) 실례.
  [Elliott canon — CS 문서엔 열거되지 않음] 임펄스 유효성 3원칙:
             ① 파동2 는 파동1 을 100% 이상 되돌리지 않는다
             ② 파동3 은 1·3·5 중 가장 짧을 수 없다
             ③ 파동4 는 파동1 의 가격영역과 겹치지 않는다
           CS p27 은 "a small number of rules and guidelines" 라고만 하고 열거하지
           않으므로 canon 출처를 별도 표기. p24 카탈로그의 'Fifth wave wedge'
           (엔딩 다이애고널)는 ③의 알려진 예외 → 겹침 시 wedge 후보로 표기하고
           탈락시키지 않되 confidence 를 깎는다.

⚠️ 자동 라벨링의 본질적 한계 — 엘리엇 파동 세기는 주관적이라 같은 차트도 분석가마다
다르게 라벨링한다(CS p27 "variable enough ... limited diversity"). 이 모듈은 zigzag
피벗 위에 기계적으로 후보를 얹고 피보나치 적합도를 점수화할 뿐이며, 결과는
**참고용 · 확정 판단 금지**(대시보드 가이드 문구도 동일). 시장 무관 순수 가격
연산이라 US/KR/JP/TW/CN_A/HK/EU 전 시장 동일 동작(universal).
"""
from __future__ import annotations

from typing import Optional

# zigzag/ATR 은 bot/pattern_screener.py(VCP 탐지)가 이미 쓰는 것을 그대로 재사용 —
# 같은 피벗 정의를 두 벌 두면 차트 라벨과 스크리너 판정이 미묘하게 갈린다.
from bot.pattern_screener import _atr, zigzag_swings

# [CS p29] 문서에 인쇄된 수열 그대로.
FIB_SEQUENCE = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144,
                233, 377, 610, 987, 1597, 2584)

# 되돌림(retracement) — 0.382/0.618 은 [CS p29·p30], 0.5 는 [CS p28] H&S 근거.
RETRACEMENT_LEVELS = (0.382, 0.5, 0.618)
# 연장(extension/projection) — [CS p30] 파동 상관에 등장하는 배수.
EXTENSION_LEVELS = (1.0, 1.618, 2.618)

# [CS p30] 파동별 기대 비율(둘 중 하나에 근접하면 적합).
_W2_OVER_W1 = (0.382, 0.618)
_W3_OVER_W1 = (1.618, 2.618)
_W4_OVER_W3 = (0.382, 0.618)
_W5_OVER_NET13 = (1.0, 1.618)
_B_OVER_A = (0.382, 0.618)
_C_OVER_A = (1.0, 1.618)

_DEFAULT_TOL = 0.15   # 비율 상대오차 허용치(±15%) — 문서엔 허용폭 언급 없어 관례값.


def _r(v, nd: int = 4):
    return round(v, nd) if isinstance(v, (int, float)) else None


def fib_retracement_levels(start: float, end: float,
                           levels=RETRACEMENT_LEVELS) -> list:
    """start→end 한 다리(leg)를 end 쪽에서 start 쪽으로 되돌린 가격들.

    [{"ratio": 0.618, "price": ...}, ...] (ratio 오름차순). start==end 이거나
    입력이 수치가 아니면 빈 리스트(호출부가 그냥 안 그림)."""
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return []
    span = end - start
    if span == 0:
        return []
    return [{"ratio": r, "price": _r(end - span * r, 6)} for r in sorted(levels)]


def fib_extension_levels(start: float, end: float,
                         levels=EXTENSION_LEVELS) -> list:
    """start→end 다리를 end 에서 같은 방향으로 연장한 목표가들([CS p30] 배수)."""
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return []
    span = end - start
    if span == 0:
        return []
    return [{"ratio": r, "price": _r(end + span * r, 6)} for r in sorted(levels)]


def _ratio_hit(actual: float, targets, tol: float):
    """actual 이 targets 중 하나와 상대오차 tol 이내면 (target, 오차) — 아니면 None.
    상대오차를 쓰는 이유: 절대오차는 0.382(작은 값)엔 너무 헐겁고 2.618(큰 값)엔
    너무 빡빡해 파동마다 기준이 달라진다."""
    if not isinstance(actual, (int, float)) or actual <= 0:
        return None
    best = None
    for t in targets:
        if t <= 0:
            continue
        err = abs(actual / t - 1.0)
        if err <= tol and (best is None or err < best[1]):
            best = (t, err)
    return best


def _alternating(pivots) -> bool:
    """zigzag 피벗이 high/low 교대인지(연속 동종이면 라벨링 불가)."""
    for i in range(1, len(pivots)):
        if pivots[i][1] == pivots[i - 1][1]:
            return False
    return True


def label_impulse(pivots, *, tol: float = _DEFAULT_TOL) -> Optional[dict]:
    """최근 6개 피벗(P0~P5)을 5파 임펄스로 라벨링 시도.

    pivots = [(idx, 'high'|'low', price), ...] 시간순(zigzag_swings 출력).
    상승/하락 임펄스를 부호 정규화로 한 코드에서 처리한다. 하드룰(canon) 위반이면
    None, 통과하면 파동길이·[CS p30] 비율 적합도·confidence 를 담은 dict.
    """
    if len(pivots) < 6:
        return None
    p = pivots[-6:]
    if not _alternating(p):
        return None
    up = p[0][1] == "low"          # 저점에서 출발 = 상승 임펄스
    sign = 1.0 if up else -1.0
    v = [pr * sign for _, _, pr in p]   # 부호 정규화 → 항상 '상승 임펄스' 형태

    w1, w2 = v[1] - v[0], v[1] - v[2]
    w3, w4 = v[3] - v[2], v[3] - v[4]
    w5 = v[5] - v[4]
    if min(w1, w2, w3, w4, w5) <= 0:   # 교대는 맞지만 단조성이 깨진 경우
        return None

    # [Elliott canon] 하드룰 — 모듈 독스트링의 ①②③.
    rule2_ok = v[2] > v[0]                    # ① 파동2 가 시작점 아래로 안 감
    rule3_ok = w3 >= min(w1, w5)              # ② 파동3 이 최단 아님
    rule4_ok = v[4] > v[1]                    # ③ 파동4 가 파동1 영역과 안 겹침
    if not (rule2_ok and rule3_ok):
        return None                            # ①② 위반은 임펄스 자체가 성립 안 함
    # ③ 위반은 탈락이 아니라 wedge(엔딩 다이애고널) 후보 — [CS p24] 카탈로그 참조.
    wedge = not rule4_ok

    net13 = v[3] - v[0]
    checks = [
        ("W2/W1", w2 / w1 if w1 else None, _W2_OVER_W1),
        ("W3/W1", w3 / w1 if w1 else None, _W3_OVER_W1),
        ("W4/W3", w4 / w3 if w3 else None, _W4_OVER_W3),
        ("W5/net(0→3)", w5 / net13 if net13 else None, _W5_OVER_NET13),
    ]
    ratios, hits = {}, 0
    for name, actual, targets in checks:
        hit = _ratio_hit(actual, targets, tol)
        ratios[name] = {"actual": _r(actual), "target": (hit[0] if hit else None),
                        "hit": bool(hit)}
        if hit:
            hits += 1
    conf = hits / len(checks)
    if wedge:
        conf *= 0.5   # 파동4 겹침 = 정규 임펄스 아님 → 확신 절반

    return {
        "kind": "impulse",
        "dir": "up" if up else "down",
        "wedge": wedge,
        "labels": [
            {"idx": p[i][0], "kind": p[i][1], "price": p[i][2], "label": str(i)}
            for i in range(6)
        ],
        "wave_lengths": {"W1": _r(w1), "W2": _r(w2), "W3": _r(w3),
                         "W4": _r(w4), "W5": _r(w5)},
        "ratios": ratios,
        "rules": {"wave2_holds_origin": rule2_ok,
                  "wave3_not_shortest": rule3_ok,
                  "wave4_no_overlap": rule4_ok},
        "confidence": _r(conf, 2),
    }


def label_correction(pivots, *, tol: float = _DEFAULT_TOL) -> Optional[dict]:
    """최근 4개 피벗(시작, A, B, C)을 a-b-c 조정으로 라벨링 시도([CS p30] 비율).

    ⚠️ 임펄스와 달리 검증 체크가 B/A·C/A **2개뿐**이라 근거가 얇다. 부분일치
    (1/2)만으로 라벨을 붙이면 *진행 중인 임펄스의 파동2·3·4* 를 A·B·C 로
    오라벨링한다(2026-07-29 스모크에서 실측: 교과서 임펄스인데 마지막 피벗이
    아직 미확정이라 6피벗이 안 모이자 조정으로 흘러가 B/A=2.618(불일치)인데도
    confidence 0.5 로 표시됨). → **둘 다 적중할 때만** 라벨링한다.
    B 가 조정 시작점을 넘어서는 형태는 [CS p25] Irregular Flat 로 존재하므로
    탈락시키지 않고 irregular 플래그로만 표기한다."""
    if len(pivots) < 4:
        return None
    p = pivots[-4:]
    if not _alternating(p):
        return None
    px = [pr for _, _, pr in p]
    a = abs(px[1] - px[0])
    b = abs(px[2] - px[1])
    c = abs(px[3] - px[2])
    if min(a, b, c) <= 0:
        return None
    # 조정 방향 = A 파의 방향(하락조정이면 down).
    down = px[1] < px[0]
    checks = [("B/A", b / a, _B_OVER_A), ("C/A", c / a, _C_OVER_A)]
    ratios, hits = {}, 0
    for name, actual, targets in checks:
        hit = _ratio_hit(actual, targets, tol)
        ratios[name] = {"actual": _r(actual), "target": (hit[0] if hit else None),
                        "hit": bool(hit)}
        if hit:
            hits += 1
    if hits < len(checks):      # 부분일치 = 오라벨링 위험 → 라벨 안 붙임(위 독스트링)
        return None
    irregular = (px[2] > px[0]) if down else (px[2] < px[0])
    return {
        "kind": "correction",
        "dir": "down" if down else "up",
        "irregular": irregular,
        "labels": [{"idx": p[i][0], "kind": p[i][1], "price": p[i][2],
                    "label": ["0", "A", "B", "C"][i]} for i in range(4)],
        "wave_lengths": {"A": _r(a), "B": _r(b), "C": _r(c)},
        "ratios": ratios,
        "confidence": _r(hits / len(checks), 2),
    }


def analyze_waves(times, highs, lows, closes, *, atr_multiplier: float = 1.5,
                  tol: float = _DEFAULT_TOL, min_bars: int = 30) -> Optional[dict]:
    """차트 오버레이용 진입점 — 피보나치 되돌림 + (가능하면) 파동 라벨.

    times/highs/lows/closes = 같은 길이의 시간축·OHLC 배열(오래된→최신).
    반환 dict 는 chart payload 에 그대로 실린다(JSON 직렬화 가능). 데이터 부족·
    스윙 부족이면 None → 호출부가 오버레이를 그냥 안 그린다(graceful).

    피보나치 기준 다리(leg) = **마지막으로 완성된 스윙 구간**(직전 피벗 → 최신
    피벗). 최신 피벗 이후 구간은 아직 진행 중이라 되돌림의 기준이 될 수 없다.

    ⚠️ zigzag 특성상 **마지막 진행 중 다리는 피벗으로 확정되지 않는다**(반대방향
    으로 min_move 만큼 되돌려야 직전 극점이 피벗으로 확정). 즉 라벨은 항상
    '확정된 구조'까지만 붙고, 지금 만들어지는 중인 파동은 번호가 안 붙는다 —
    미확정 구간까지 추정해 붙이면 그게 바로 사후 재라벨링(되돌아보니 틀림)의
    주범이라 의도적으로 배제한다.
    """
    n = len(closes)
    if n < min_bars or len(highs) != n or len(lows) != n or len(times) != n:
        return None
    atr = _atr(highs, lows, closes, period=14)
    if atr <= 0:
        return None
    pivots = zigzag_swings(highs, lows, atr * atr_multiplier)
    if len(pivots) < 2:
        return None

    lo_idx, _, lo_price = pivots[-2]
    hi_idx, _, hi_price = pivots[-1]
    out = {
        "leg": {
            "from_time": times[lo_idx], "to_time": times[hi_idx],
            "from": lo_price, "to": hi_price,
            "dir": "up" if hi_price > lo_price else "down",
        },
        "retracements": fib_retracement_levels(lo_price, hi_price),
        "extensions": fib_extension_levels(lo_price, hi_price),
        "pivot_count": len(pivots),
        "atr_threshold": _r(atr * atr_multiplier, 6),
    }

    # 임펄스 우선 시도 → 실패 시 조정 패턴. 둘 다 실패면 피보나치만 반환.
    wave = label_impulse(pivots, tol=tol) or label_correction(pivots, tol=tol)
    if wave:
        for lb in wave["labels"]:
            lb["time"] = times[lb["idx"]]
        out["wave"] = wave
    return out
