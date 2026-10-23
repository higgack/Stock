"""추천 사후 채점 — 판정 방향별 규약 + ±2%p 중립 구간. 단일 출처.

사용자 2026-10-22: 외부 레포 검토(ZhuLinsen/daily_stock_analysis
`decision_signal_outcome_service.py`) 뒤 "③ 적용". 옛 규약은 두 가지가
아쉬웠다:
  · **알파 +0.1%p 도 '매수 맞음'** 이었다 — 5거래일 섹터 대비 차이는 잡음이
    크다. 아주 작은 차이를 맞음·틀림으로 세면 정확도가 동전던지기를 잰다.
  · **보유(Hold)는 채점 자체가 없었다** — 판정의 절반 가까이가 성적표 밖이었다.

새 규약(5·15·30거래일 창 모두 같다, 기준 = 섹터 ETF 대비 알파):
  · 매수·비중확대 — 알파 > +2%p 맞음 · < −2%p 틀림 · 그 사이 '판정 보류'
  · 매도·비중축소 — 알파 < −2%p 맞음 · > +2%p 틀림 · 그 사이 '판정 보류'
  · 보유          — |알파| ≤ 2%p 맞음(섹터와 비슷하게 갈 거라는 판단이 맞음)
                   · 벗어나면 틀림(방향을 놓쳤다)
'판정 보류' 는 분모에서 빠지고 화면이 그 건수를 따로 말한다(#45 — 빠진 것을
숨기지 않는다). 상단 정확도는 종전처럼 **방향성(매수·매도)** 만 세고, 보유는
별도 줄로 센다(정의가 다른 둘을 한 숫자에 섞지 않는다, #34).

⚠️ 2%p 는 외부 레포의 기본값을 가져온 것이지 우리 데이터로 잰 값이 아니다(#12).
카드·통계·가이드가 이 상수 하나를 읽는다(#38).
"""

from __future__ import annotations

import math

NEUTRAL_BAND_PP = 2.0

_BUY = ("buy", "overweight")
_SELL = ("sell", "underweight")


def verdict(rating: str | None, alpha_pp: float | None) -> str | None:
    """→ 'hit' | 'miss' | 'neutral' | None(채점 불가). 순수."""
    if not isinstance(alpha_pp, (int, float)) or not math.isfinite(alpha_pp):
        return None
    r = (rating or "").strip().lower()
    b = NEUTRAL_BAND_PP
    if r in _BUY:
        return "hit" if alpha_pp > b else "miss" if alpha_pp < -b else "neutral"
    if r in _SELL:
        return "hit" if alpha_pp < -b else "miss" if alpha_pp > b else "neutral"
    if r == "hold":
        return "hit" if abs(alpha_pp) <= b else "miss"
    return None


def verdict_text(rating: str | None, alpha_pp: float | None) -> str:
    """카드 한 줄 문구. 채점 불가면 빈 문자열. 순수."""
    v = verdict(rating, alpha_pp)
    if v is None:
        return ""
    r = (rating or "").strip().lower()
    b = f"±{NEUTRAL_BAND_PP:g}%p"
    if r in _BUY or r in _SELL:
        side = "매수" if r in _BUY else "매도"
        if v == "neutral":
            return f"{side} 추천 판정 보류(섹터 대비 {b} 안)"
        return f"{side} 추천 {'맞음' if v == 'hit' else '틀림'}"
    # hold
    if v == "hit":
        return f"보유 추천 맞음(섹터 대비 {b} 안)"
    return f"보유 추천 틀림(섹터 대비 {b} 밖)"
