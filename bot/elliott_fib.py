"""엘리엇 파동 · 피보나치 되돌림 — 순수함수 (2026-07-29).

**정석(mainstream) 기준**으로 구현한다. 최초 구현은 Credit Suisse
"Technical Analysis - Explained"(p23~p31) 만 따랐으나, 사용자 요청(2026-07-29
"정석적인 웹상의 내용을 반영")에 따라 StockCharts ChartSchool · Elliott Wave
International(Waveopedia) · Frost & Prechter 통설 · TradingView/MetaTrader/
thinkorswim 기본값과 대조해 **CS 문서와 다른 부분은 정석을 채택**했다.
CS 문서만의 값은 참고로 주석에 남긴다(어느 쪽을 왜 골랐는지 추적용).

CS 문서 대비 바뀐 것(정석 채택)
  1. 되돌림 레벨: CS 는 0.382/0.618 만(+H&S 문맥의 50%). → 업계 표준
     0.236/0.382/0.5/0.618/0.786 로 확장.
  2. 연장(projection) 산식: 초기 구현이 `end + span×k` 라 표준 대비 한 span
     밀려 있었다(k=1.618 이 261.8% 가 아니라 361.8% 를 가리킴). → 표준
     `start + span×k` 로 정정하고 레벨도 1.272/1.618/2.0/2.618 로 교체.
  3. 파동 비율 목표치를 통설 세트로 교체(아래 상수 주석 참조). 특히 W4/W3 은
     CS 가 0.382/0.618 을 들지만 통설 최빈값은 0.236/0.382/0.5 (파동4 는 보통
     파동2 보다 얕다 — 교대 지침).
  4. 임펄스 하드룰에 "파동3 은 파동1 의 끝을 넘어선다"(R2 따름정리) 추가.
  5. 다이애고널(파동4 가 파동1 과 겹치는 경우)에 통설 제약 추가 — 파동4 는
     파동2 의 끝을 넘어설 수 없다. 위반 시 다이애고널로도 인정 안 함.
  6. 절단 5파(truncated fifth — 파동5 가 파동3 끝을 못 넘김) 플래그 추가.
     "5파는 반드시 신고점"이라는 순진한 가정은 통설상 오답.
  7. 조정 분류를 지그재그 / 정규 플랫 / 확장 플랫 / 러닝 플랫 로 세분(통설
     B·C 임계). CS 는 비율 쌍만 제시.

규칙 vs 지침 — 통설의 핵심 구분(자동 카운팅이 가장 자주 틀리는 지점)
  · **규칙(rule)** 은 위반 시 카운트 자체가 무효: R1 파동2 는 파동1 기점을
    100% 넘게 되돌리지 않는다 / R2 파동3 은 1·3·5 중 최단이 아니며 파동1 의
    끝을 넘어선다 / R3 파동4 는 파동1 가격영역에 진입하지 않는다(다이애고널 예외).
  · **지침(guideline)·피보나치 비율** 은 유효성 판정이 아니라 *순위·확신도* 용.
    비율이 안 맞아도 카운트가 무효는 아니다 → 이 모듈은 규칙 위반만 탈락시키고
    비율은 confidence 로만 반영한다.

알려진 한계(통설이 지적하는 자동 카운팅의 함정 중 이 모듈이 아직 안 다루는 것)
  · 우측 끝 리페인팅 — 진행 중 파동은 라벨하지 않는 것으로 회피(analyze_waves
    독스트링 참조)하나, 확정 피벗도 이후 데이터로 재해석될 수 있다.
  · 단일 카운트만 반환(통설 권장은 대안 카운트 랭킹 + 각각의 무효화 가격).
    무효화 가격은 invalidation 으로 1개만 제공.
  · 파동 등급(degree) 계층 미구현 — 하위 degree 피벗을 안 보므로 "파동4 는 한
    등급 아래 4파 영역에서 끝난다" 류 지침은 검증 불가.
  · 길이 비교가 선형(가격차) 기준 — 장기 로그차트에서는 비율(로그) 비교가 더
    정확하다는 지적이 있다. 대부분의 차트툴이 선형을 쓰므로 선형 유지.
  · zigzag 임계(ATR×1.5) 하나만 사용 — 통설은 복수 임계로 돌려 카운트 민감도를
    보라고 권한다.

⚠️ 파동 세기는 본질적으로 주관적이라 분석가마다 다르게 센다. 이 모듈 결과는
**참고용 · 확정 판단 금지**(대시보드 가이드 문구도 동일). 순수 가격 연산이라
US/KR/JP/TW/CN_A/HK/EU 전 시장 동일 동작(universal).
"""
from __future__ import annotations

from typing import Optional

# zigzag/ATR 은 bot/pattern_screener.py(VCP 탐지)가 이미 쓰는 것을 그대로 재사용 —
# 같은 피벗 정의를 두 벌 두면 차트 라벨과 스크리너 판정이 미묘하게 갈린다.
from bot.pattern_screener import zigzag_swings

# [CS p29] 문서에 인쇄된 수열 — 아래 비율들의 출처.
FIB_SEQUENCE = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144,
                233, 377, 610, 987, 1597, 2584)

# 되돌림(retracement) — 업계 표준 세트(TradingView·thinkorswim 기본값).
#   0.236 = 0.618³ (세 칸 건너뛴 항 비율)
#   0.382 = 0.618² (두 칸 건너뛴 항 비율)
#   0.5   = ⚠️ 피보나치 비율이 **아니다**. 다우 이론의 '평균은 직전 이동의 절반쯤
#           되돌린다'는 관찰이 관행으로 굳은 것(StockCharts·CME 명시).
#   0.618 = 1/φ 황금비
#   0.786 = √0.618 — 여기를 확실히 깨면 직전 임펄스 구조를 무효로 보는 '마지노선'
#           으로 통용(관례이지 증명된 규칙은 아님).
# (0.764=1−0.236 은 일부 브로커 변형이라 미채택. 0.886 은 하모닉 패턴 전용이라 제외.
#  MetaTrader 기본값엔 0.786 이 없는 등 플랫폼별 차이가 있어 TradingView 계열 채택.)
RETRACEMENT_LEVELS = (0.236, 0.382, 0.5, 0.618, 0.786)

# 투영(projection) — 되돌림 다리를 넘어선 목표가. 표준 세트.
#   1.272 = √1.618 · 1.618 = φ · 2.618 = φ² · 2.0 = 라운드넘버 관례(피보나치 아님)
# ⚠️ 이 모듈은 2점(스윙 저점→고점) 방식이라 TradingView 용어로는 '외부 되돌림
# (external retracement)'. 3점 anchor 를 쓰는 'Trend-Based Fib Extension'
# (C + k×(B−A))과는 다른 도구이며 C==A 일 때만 값이 같다. 이름을 projection 으로
# 통일해 혼동을 막는다.
PROJECTION_LEVELS = (1.272, 1.618, 2.0, 2.618)

# ── 파동 간 피보나치 비율 — 통설 최빈값 세트 ────────────────────────────────
# (EWI Waveopedia · StockCharts · Frost & Prechter 통설. CS p30 값은 괄호로 병기.)
_W2_OVER_W1 = (0.382, 0.5, 0.618, 0.786)      # CS: 0.382/0.618. 0.618 이 최빈.
_W3_OVER_W1 = (1.618, 2.618, 4.236)           # CS: 1.618/2.618. 1.618 이 최빈.
_W4_OVER_W3 = (0.236, 0.382, 0.5)             # CS: 0.382/0.618 → 통설 채택(더 얕음).
_W5_OVER_NET13 = (0.382, 0.618, 1.0, 1.618)   # CS: 1.0/1.618.
_W5_OVER_W1 = (0.618, 1.0)                    # 파동3 연장 시 통용되는 대안 관계.

# ── 조정 분류 임계 — 통설(EWI Waveopedia flats/zigzags) ──────────────────────
_ZIGZAG_B_BAND = (0.382, 0.786)    # 지그재그: B 가 A 의 38~79% 되돌림
_FLAT_REG_B_BAND = (0.90, 1.04)    # 정규 플랫: B ≈ A 의 90~104%
_FLAT_EXP_B_BAND = (1.05, 1.382)   # 확장/러닝 플랫: B ≥ 105% (상한은 실무 가드)
_ZIGZAG_C_OVER_A = (0.618, 1.0, 1.618)
_FLAT_REG_C_OVER_A = (1.0,)
_FLAT_EXP_C_OVER_A = (1.618, 2.618)

_DEFAULT_TOL = 0.15   # 비율 상대오차 허용치(±15%) — 통설에 허용폭 명시 없어 관례값.


# 피벗 임계 후보 — **가격 대비 상대(%)**. 굵은 것부터 훑어 내려간다.
_PIVOT_THRESHOLD_STEPS = (0.35, 0.25, 0.18, 0.13, 0.095, 0.07,
                          0.05, 0.036, 0.026, 0.018, 0.012, 0.008)
_TARGET_PIVOTS = 8      # 임펄스(6개)+여유. 이만큼 나오는 가장 굵은 임계를 채택.
# 창의 고가/저가 배율이 이 값을 넘으면 파동 길이를 로그(%) 기준으로 잰다.
# (관례값 — 통설에 명시 임계는 없다. 산술축에서 초기 파동이 뭉개지는 지점.)
_LOG_SCALE_RATIO = 3.0


def _r(v, nd: int = 4):
    return round(v, nd) if isinstance(v, (int, float)) else None


def _lg(price):
    """로그가격. 파동 길이·피벗 임계를 **상대(%) 기준**으로 다루기 위한 변환.
    0 이하(불량 데이터)면 None → 호출부가 graceful 종료."""
    import math
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    return math.log(price)


def _as_pct(log_len):
    """로그 길이 → 퍼센트 이동폭(표시용). log 0.405 → +50.0%"""
    import math
    if not isinstance(log_len, (int, float)):
        return None
    return round((math.exp(log_len) - 1.0) * 100.0, 2)


def auto_pivots(highs, lows, *, target_min: int = _TARGET_PIVOTS):
    """보고 있는 창에 맞는 **등급(degree)** 의 피벗을 고른다.

    굵은 임계부터 훑어 내려가며 피벗이 target_min 개 이상 나오는 **첫(=가장
    굵은)** 임계를 채택 → 그 창을 지배하는 구조가 잡힌다. 5년 창이면 수년짜리
    파동이, 1개월 창이면 그 안의 파동이 잡혀 프랙탈 성질과 맞는다.

    임계는 **상대(%)** 이고 로그가격에서 비교한다. 이전 구현은 ATR(최근 14봉)
    ×1.5 라는 **절대** 임계여서, 5년간 10배 오른 종목이면 그 임계가 최근 고가
    기준으로 잡혀 과거 저가 구간에선 아예 피벗이 안 생겼다 → 라벨이 늘 차트
    오른쪽 끝 몇 주에만 몰렸다(사용자 지적 2026-07-31).

    반환: (pivots[(idx, 'high'|'low', 실제가격)], 채택된 임계 비율)."""
    logs = _log_arrays(highs, lows)
    if logs is None:
        return [], 0.0
    best, best_thr = [], 0.0
    for f in _PIVOT_THRESHOLD_STEPS:
        piv = _pivots_at(logs, highs, lows, f)
        if len(piv) > len(best):
            best, best_thr = piv, f
        if len(piv) >= target_min:
            return piv, f
    return best, best_thr


def _log_arrays(highs, lows):
    """(로그고가, 로그저가) — 가격에 0 이하가 섞이면 None(graceful)."""
    n = len(highs)
    if n == 0 or len(lows) != n:
        return None
    lh = [_lg(x) for x in highs]
    ll = [_lg(x) for x in lows]
    if any(x is None for x in lh) or any(x is None for x in ll):
        return None
    return lh, ll


def _pivots_at(logs, highs, lows, frac: float):
    """상대임계 frac(예: 0.13 = 13%) 로 잡은 피벗 — 표시용 실제 가격으로 환원."""
    import math
    lh, ll = logs
    raw = zigzag_swings(lh, ll, math.log(1.0 + frac))
    return [(i, k, (highs[i] if k == "high" else lows[i])) for i, k, _ in raw]


def fib_retracement_levels(start: float, end: float,
                           levels=RETRACEMENT_LEVELS) -> list:
    """start→end 한 다리(leg)의 되돌림 가격들.

    `start + (end-start) × (1-r)` 과 동치인 `end - span×r` 로 계산 — 상승/하락
    다리 모두 부호 분기 없이 그대로 동작(하락 다리면 되돌림이 위로 잡힘).
    [{"ratio": .., "price": ..}, ...] ratio 오름차순. 길이 0·비수치면 빈 리스트."""
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return []
    span = end - start
    if span == 0:
        return []
    return [{"ratio": r, "price": _r(end - span * r, 6)} for r in sorted(levels)]


def fib_projection_levels(start: float, end: float,
                          levels=PROJECTION_LEVELS) -> list:
    """start→end 다리의 투영(외부 되돌림) 목표가 — 표준 `start + span×k`.

    k=1.618, start=100, end=200 → 261.8 (표준). 초기 구현의 `end + span×k`
    (=361.8) 는 한 span 밀린 값이라 정정됨(모듈 독스트링 2번)."""
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return []
    span = end - start
    if span == 0:
        return []
    return [{"ratio": r, "price": _r(start + span * r, 6)} for r in sorted(levels)]


def _ratio_hit(actual, targets, tol: float):
    """actual 이 targets 중 하나와 상대오차 tol 이내면 (target, 오차) — 아니면 None.
    상대오차인 이유: 절대오차는 0.382 엔 너무 헐겁고 2.618 엔 너무 빡빡해 파동마다
    기준이 달라진다."""
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


def label_impulse(pivots, *, tol: float = _DEFAULT_TOL,
                  log_scale: bool = False) -> Optional[dict]:
    """최근 6개 피벗(P0~P5)을 5파 임펄스로 라벨링 시도.

    pivots = [(idx, 'high'|'low', price), ...] 시간순(zigzag_swings 출력).
    상승/하락을 부호 정규화로 한 코드에서 처리한다. **규칙 위반이면 None**,
    통과하면 파동길이·비율 적합도·confidence 를 담은 dict(지침/비율 불일치는
    탈락이 아니라 confidence 하락 — 모듈 독스트링 '규칙 vs 지침' 참조)."""
    if len(pivots) < 6:
        return None
    p = pivots[-6:]
    if not _alternating(p):
        return None
    up = p[0][1] == "low"          # 저점에서 출발 = 상승 임펄스
    sign = 1.0 if up else -1.0
    # 파동 길이 기준(log_scale) — 호출부가 창의 가격 배율을 보고 정한다.
    #  · 선형(기본): 차트가 산술축이라 사용자가 화면에서 재는 값과 일치. 통설
    #    도구들도 대개 가격차로 잰다.
    #  · 로그: 장기 차트(수배~수십배 상승)에서는 선형으로 재면 초기 저가 구간
    #    파동이 터무니없이 짧게 나와 '파동3 최단' 같은 규칙 판정까지 왜곡된다.
    # log 는 단조증가라 어느 쪽이든 규칙의 대소 비교(v[2]>v[0] 등)는 동일하게
    # 성립하고, 바뀌는 것은 길이의 '비율' 뿐이다.
    base = [_lg(pr) for _, _, pr in p] if log_scale else [pr for _, _, pr in p]
    if any(x is None for x in base):
        return None
    v = [x * sign for x in base]   # 부호 정규화 → 항상 '상승 임펄스' 형태

    w1, w2 = v[1] - v[0], v[1] - v[2]
    w3, w4 = v[3] - v[2], v[3] - v[4]
    w5 = v[5] - v[4]
    if min(w1, w2, w3, w4, w5) <= 0:   # 교대는 맞지만 단조성이 깨진 경우
        return None

    # ── 규칙(위반 = 카운트 무효) ──────────────────────────────────────────
    r1_wave2 = v[2] > v[0]              # R1 파동2 가 파동1 기점을 안 깬다
    r2_exceeds = v[3] > v[1]            # R2 따름정리: 파동3 이 파동1 끝을 넘는다
    r2_not_shortest = w3 >= min(w1, w5)  # R2 파동3 은 최단이 아니다
    r3_no_overlap = v[4] > v[1]         # R3 파동4 가 파동1 영역에 안 들어간다
    if not (r1_wave2 and r2_exceeds and r2_not_shortest):
        return None
    # R3 위반은 다이애고널(쐐기)일 수 있다 — 통설상 이때 파동4 는 파동2 의 끝을
    # 넘어설 수 없다. 그것마저 깨지면 다이애고널로도 성립 안 함.
    diagonal = not r3_no_overlap
    if diagonal and not (v[4] > v[2]):
        return None
    # 절단 5파: 파동5 가 파동3 끝을 못 넘김. 유효한 형태이므로 탈락 아님(플래그).
    truncated = v[5] <= v[3]

    # ── 지침·비율(확신도만 반영) ─────────────────────────────────────────
    net13 = v[3] - v[0]
    ratios, hits = {}, 0

    def _check(name, actual, targets):
        nonlocal hits
        hit = _ratio_hit(actual, targets, tol)
        ratios[name] = {"actual": _r(actual), "target": (hit[0] if hit else None),
                        "hit": bool(hit)}
        if hit:
            hits += 1

    _check("W2/W1", w2 / w1 if w1 else None, _W2_OVER_W1)
    _check("W3/W1", w3 / w1 if w1 else None, _W3_OVER_W1)
    _check("W4/W3", w4 / w3 if w3 else None, _W4_OVER_W3)
    # 파동5 는 통설이 두 관계를 모두 인용(0→3 순이동 대비 / 파동1 대비) —
    # 둘 중 하나만 맞아도 적합으로 본다(체크 개수는 4개 유지).
    a_net = w5 / net13 if net13 else None
    a_w1 = w5 / w1 if w1 else None
    h_net = _ratio_hit(a_net, _W5_OVER_NET13, tol)
    h_w1 = _ratio_hit(a_w1, _W5_OVER_W1, tol)
    ratios["W5/net(0→3)"] = {"actual": _r(a_net),
                             "target": (h_net[0] if h_net else None),
                             "hit": bool(h_net)}
    ratios["W5/W1"] = {"actual": _r(a_w1), "target": (h_w1[0] if h_w1 else None),
                       "hit": bool(h_w1)}
    if h_net or h_w1:
        hits += 1

    conf = hits / 4.0
    if diagonal:
        conf *= 0.5     # 정규 임펄스가 아님(다이애고널) → 확신 절반
    if truncated:
        conf *= 0.8     # 절단 5파는 유효하나 덜 전형적

    # 연장 파동(1·3·5 중 하나가 나머지 둘의 1.618배 이상) — 통설 지침.
    lens = {"1": w1, "3": w3, "5": w5}
    longest = max(lens, key=lambda k: lens[k])
    rest = [val for k, val in lens.items() if k != longest]
    extended = longest if lens[longest] >= 1.618 * max(rest) else None

    return {
        "kind": "impulse",
        "dir": "up" if up else "down",
        "diagonal": diagonal,
        "truncated": truncated,
        "extended_wave": extended,
        "labels": [
            {"idx": p[i][0], "kind": p[i][1], "price": p[i][2], "label": str(i)}
            for i in range(6)
        ],
        # 로그 기준이면 %이동폭으로 환산해 표기(로그 원값은 사람이 못 읽는다).
        "length_unit": "pct" if log_scale else "price",
        "wave_lengths": ({"W1": _as_pct(w1), "W2": _as_pct(w2), "W3": _as_pct(w3),
                          "W4": _as_pct(w4), "W5": _as_pct(w5)} if log_scale else
                         {"W1": _r(w1), "W2": _r(w2), "W3": _r(w3),
                          "W4": _r(w4), "W5": _r(w5)}),
        "ratios": ratios,
        "rules": {"wave2_holds_origin": r1_wave2,
                  "wave3_exceeds_wave1": r2_exceeds,
                  "wave3_not_shortest": r2_not_shortest,
                  "wave4_no_overlap": r3_no_overlap},
        # 무효화 가격 = 파동1 기점. 여기를 되돌리면 이 임펄스 해석 자체가 깨진다.
        "invalidation": p[0][2],
        "confidence": _r(conf, 2),
    }


def label_correction(pivots, *, tol: float = _DEFAULT_TOL,
                     log_scale: bool = False) -> Optional[dict]:
    """최근 4개 피벗(시작·A·B·C)을 조정 패턴으로 분류·라벨링 시도.

    통설 임계로 지그재그 / 정규 플랫 / 확장 플랫(러닝 포함) 을 **먼저 분류**하고,
    그 패턴의 C/A 기대비율이 맞는지로 confidence 를 매긴다.

    ⚠️ 회귀 고정(2026-07-29 스모크 실측): 초기 구현은 B/A·C/A 를 좁은 값 집합에
    각각 대조하기만 해서, 진행 중인 임펄스의 파동2·3·4 가 A·B·C 로 오라벨링됐다
    (B/A=2.618 로 기대치를 크게 빗나갔는데 C/A 만 맞아 confidence 0.5). 지금은
    B/A 가 어떤 패턴 밴드에도 안 들어가면(2.618 은 어디에도 없음) 그 시점에
    탈락한다."""
    if len(pivots) < 4:
        return None
    p = pivots[-4:]
    if not _alternating(p):
        return None
    px = [pr for _, _, pr in p]
    # 길이 기준은 임펄스와 동일 규칙(log_scale) — 방향/초과 판정은 항상 실제
    # 가격으로(log 가 단조증가라 두 방식의 대소 비교 결과는 같다).
    lp = [_lg(x) for x in px] if log_scale else list(px)
    if any(x is None for x in lp):
        return None
    a = abs(lp[1] - lp[0])
    b = abs(lp[2] - lp[1])
    c = abs(lp[3] - lp[2])
    if min(a, b, c) <= 0:
        return None

    rb = b / a
    if _ZIGZAG_B_BAND[0] <= rb <= _ZIGZAG_B_BAND[1]:
        pattern, c_targets = "zigzag", _ZIGZAG_C_OVER_A
    elif _FLAT_REG_B_BAND[0] <= rb <= _FLAT_REG_B_BAND[1]:
        pattern, c_targets = "flat_regular", _FLAT_REG_C_OVER_A
    elif _FLAT_EXP_B_BAND[0] <= rb <= _FLAT_EXP_B_BAND[1]:
        pattern, c_targets = "flat_expanded", _FLAT_EXP_C_OVER_A
    else:
        return None    # 어떤 조정 패턴 밴드에도 안 들어감 → 라벨 안 붙임

    down = px[1] < px[0]        # 조정 방향 = A 파의 방향
    # 러닝 플랫: B 가 A 기점을 넘었는데 C 가 A 의 끝에 못 미치는 형태.
    reached_a_end = (px[3] <= px[1]) if down else (px[3] >= px[1])
    if pattern == "flat_expanded" and not reached_a_end:
        pattern = "flat_running"

    hit_c = _ratio_hit(c / a, c_targets, tol)
    ratios = {
        "B/A": {"actual": _r(rb), "band": list(
            _ZIGZAG_B_BAND if pattern == "zigzag"
            else _FLAT_REG_B_BAND if pattern == "flat_regular"
            else _FLAT_EXP_B_BAND), "hit": True},
        "C/A": {"actual": _r(c / a), "target": (hit_c[0] if hit_c else None),
                "hit": bool(hit_c)},
    }
    return {
        "kind": "correction",
        "pattern": pattern,
        "dir": "down" if down else "up",
        # B 가 조정 시작점을 넘어선 형태([EWI] expanded/running flat, 통설상 정상).
        "irregular": (px[2] > px[0]) if down else (px[2] < px[0]),
        "labels": [{"idx": p[i][0], "kind": p[i][1], "price": p[i][2],
                    "label": ["0", "A", "B", "C"][i]} for i in range(4)],
        "length_unit": "pct" if log_scale else "price",
        "wave_lengths": ({"A": _as_pct(a), "B": _as_pct(b), "C": _as_pct(c)}
                         if log_scale else
                         {"A": _r(a), "B": _r(b), "C": _r(c)}),
        "ratios": ratios,
        "invalidation": px[0],
        "confidence": 1.0 if hit_c else 0.5,
    }


def analyze_waves(times, highs, lows, closes, *, tol: float = _DEFAULT_TOL,
                  min_bars: int = 30,
                  target_pivots: int = _TARGET_PIVOTS) -> Optional[dict]:
    """차트 오버레이용 진입점 — 피보나치 되돌림 + (가능하면) 파동 라벨.

    times/highs/lows/closes = 같은 길이의 시간축·OHLC 배열(오래된→최신).
    반환 dict 는 chart payload 에 그대로 실린다(JSON 직렬화 가능). 데이터 부족·
    스윙 부족이면 None → 호출부가 오버레이를 그냥 안 그린다(graceful).

    **보이는 창에 맞춰 등급이 자동으로 정해진다**(auto_pivots): 5년 창이면
    수년짜리 파동을, 1개월 창이면 그 안의 파동을 센다. 파동 원리가 프랙탈이라
    창마다 다른 등급이 잡히는 게 정상이며, 그래서 범위/봉을 바꾸면 라벨 위치도
    바뀐다. 이전 구현은 ATR(최근 14봉) 기반 **절대** 임계라 장기 차트에서도 늘
    최근 몇 주의 잔파동만 라벨링됐다(사용자 지적 2026-07-31).

    피보나치 기준 다리(leg) = **마지막으로 완성된 스윙 구간**(직전 피벗 → 최신
    피벗). 고가/저가(꼬리 포함)를 앵커로 쓴다 — 종가 기준 스윙과 꼬리 기준 범위를
    섞는 것이 이 계열의 가장 흔한 버그라 zigzag 와 앵커를 일치시킨다. 되돌림
    가격 자체는 차트가 기본 산술축이라 **선형**으로 계산한다(파동 길이 비교만
    로그 기준 — 장기 차트에서 초기 구간이 뭉개지는 것을 막기 위해).

    ⚠️ zigzag 특성상 **마지막 진행 중 다리는 피벗으로 확정되지 않는다**(반대방향
    으로 임계만큼 되돌려야 직전 극점이 피벗으로 확정). 즉 라벨은 항상 '확정된
    구조'까지만 붙고, 지금 만들어지는 중인 파동엔 번호가 안 붙는다 — 미확정
    구간까지 추정해 붙이는 것이 통설이 지적하는 '우측 끝 리페인팅'의 주범이라
    의도적으로 배제한다. 대신 마지막 확정 라벨 이후 현재가가 어디까지 왔는지를
    `progress` 로 제공한다.
    """
    n = len(closes)
    if n < min_bars or len(highs) != n or len(lows) != n or len(times) != n:
        return None
    # 등급 선택: 굵은 임계부터 훑어 **유효한 파동 카운트가 나오는 첫(=가장 큰)
    # 등급**을 채택한다. 한 등급만 보면 그 창에서 규칙을 못 맞춰 라벨이 통째로
    # 사라지는 일이 잦다(합성 5년 데이터 실측) — 통설도 '복수 임계로 돌려보라'
    # 고 권한다. 어느 등급에서도 카운트가 안 나오면 피보나치만 그린다.
    logs = _log_arrays(highs, lows)
    if logs is None:
        return None
    # 길이 기준: 창의 가격 배율이 크면(기본 3배 초과) 로그(%), 아니면 선형(가격차).
    # 산술축 차트에서 사용자가 재는 값과 맞추는 게 기본이되, 수배씩 오른 장기
    # 창에서는 선형이 초기 파동을 뭉개 규칙 판정까지 왜곡되므로 전환한다.
    _lo, _hi = min(lows), max(highs)
    log_scale = bool(_lo > 0 and (_hi / _lo) > _LOG_SCALE_RATIO)
    pivots, thr, wave = [], 0.0, None
    fib_only = None
    for f in _PIVOT_THRESHOLD_STEPS:
        piv = _pivots_at(logs, highs, lows, f)
        if len(piv) < 2:
            continue
        if fib_only is None and len(piv) >= target_pivots:
            fib_only = (piv, f)        # 라벨 실패 시 쓸 기본 등급
        if len(piv) >= 4:
            w = (label_impulse(piv, tol=tol, log_scale=log_scale)
                 or label_correction(piv, tol=tol, log_scale=log_scale))
            if w:
                pivots, thr, wave = piv, f, w
                break
    if wave is None:
        if fib_only is None:
            return None
        pivots, thr = fib_only
    if len(pivots) < 2:
        return None

    a_idx, _, a_price = pivots[-2]
    b_idx, _, b_price = pivots[-1]
    out = {
        "leg": {
            "from_time": times[a_idx], "to_time": times[b_idx],
            "from": a_price, "to": b_price,
            "dir": "up" if b_price > a_price else "down",
        },
        "retracements": fib_retracement_levels(a_price, b_price),
        "projections": fib_projection_levels(a_price, b_price),
        "pivot_count": len(pivots),
        # 이 창에서 '스윙' 으로 인정한 최소 등락폭(%) — 등급을 눈으로 확인용.
        "pivot_threshold_pct": _r(thr * 100.0, 2),
    }

    if wave:
        for lb in wave["labels"]:
            lb["time"] = times[lb["idx"]]
        # 5파가 완성됐으면 이어질 조정의 되돌림 목표(전체 0→5 구간 기준)를 함께
        # 제공 — 통설·[CS p31] 모두 '직전 임펄스 전체의 38.2~61.8% 되돌림'을 든다.
        if wave["kind"] == "impulse":
            wave["correction_targets"] = fib_retracement_levels(
                wave["labels"][0]["price"], wave["labels"][5]["price"],
                levels=(0.382, 0.5, 0.618))
        # '지금 어디까지 왔나' — 마지막 확정 라벨 이후 현재가까지의 진행.
        last = wave["labels"][-1]
        cur = closes[-1]
        if isinstance(cur, (int, float)) and last.get("price"):
            wave["progress"] = {
                "since_label": last["label"],
                "since_price": last["price"],
                "since_time": last.get("time"),
                "current": cur,
                "pct": _r((cur / last["price"] - 1.0) * 100.0, 2),
                "bars": max(0, (n - 1) - last["idx"]),
            }
        out["wave"] = wave
    return out
