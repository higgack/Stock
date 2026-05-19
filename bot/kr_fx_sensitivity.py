"""KR USD/KRW 환율 영향 sensitivity table.

D2 (Step 2A item ⑤, 2026-05-19): 수출 의존도 큰 KR 산업 + USD/KRW
1% 변동 시 영업이익 ±X% 영향 sensitivity 자동 계산 + 매크로 분석
inject. KR 분석에서 환율이 단기 5거래일 가격을 흔드는 가장 큰
single variable 인데 분석가들이 generic 'KRW 약세 = 수출주 긍정'
narrative 만 작성하고 specific quantitative 영향 추정 안 하던 패턴
차단.

sensitivity coefficient = 영업이익 변동 % / USD/KRW 변동 %
양수 = KRW 약세 시 영업이익 증가 (수출주)
음수 = KRW 약세 시 영업이익 감소 (수입주 / 외화 부채주)

각 산업별 coefficient 는 KR 증권사 리서치 + 회사 사업보고서에 명시
된 환율 민감도를 평균한 industry-level approximation. 특정 종목의
실제 민감도는 ±50% range 가능 (사업포트폴리오 / 환헤지 비율 차이).

Industry tag 는 yfinance .info 의 industry 필드 그대로 (한국어 변환
없이) — KR ticker 가 yfinance 에서 어떤 tag 받는지에 따라 match.
"""

from __future__ import annotations
from typing import Optional


# 양수 = 수출주 (KRW 약세 수혜) / 음수 = 수입주 (KRW 약세 손해)
# 0 또는 미정의 = sensitivity 미약 또는 mixed (안 inject)
_KR_FX_SENSITIVITY: dict[str, float] = {
    # ─── 수출 의존도 매우 높음 (>+2.0)
    "Semiconductors": 2.5,           # SK하이닉스 / 삼성전자 — 매출 80%+ USD 표시
    "Marine Shipping": 3.0,           # HMM / 팬오션 — 운임 100% USD
    "Shipbuilding": 2.5,              # 한화오션 / HD현대중공업 — 수주 USD
    "Electrical Equipment & Parts": 2.5,  # LG에너지솔루션 / 삼성SDI — 글로벌 EV 매출 USD
    "Auto Manufacturers": 2.0,        # 현대차 / 기아 — 美 + 유럽 매출 USD/EUR
    # ─── 수출 의존도 높음 (+1.0 ~ +2.0)
    "Auto Parts": 1.5,                # 현대모비스 / 만도 — 해외 OEM
    "Semiconductor Equipment & Materials": 1.5,  # 동진쎄미켐 / 솔브레인
    "Electronic Components": 1.5,     # 삼성전기 — 글로벌 client
    "Consumer Electronics": 1.5,      # 삼성전자 가전 — 美/EU 수출
    "Steel": 1.0,                     # POSCO / 현대제철
    "Specialty Chemicals": 1.0,       # 한솔케미칼 / 코오롱인더 등
    "Chemicals": 1.0,
    # ─── 수출 의존도 보통 (+0.5 ~ +1.0)
    "Aerospace & Defense": 1.0,       # 한화에어로 / KAI
    "Industrial Machinery": 0.5,
    "Drug Manufacturers - General": 0.5,   # 셀트리온 / 삼성바이오 — 일부 글로벌
    "Biotechnology": 0.5,
    # ─── 수입주 / 외화 부채 (음수)
    "Airlines": -2.0,                 # 대한항공 / 아시아나 — 유류비 USD + 외화 부채
    "Tobacco": -0.5,                  # KT&G — 일부 원료 수입
    "Personal Products": -0.5,        # LG생활건강 / 아모레 — 일부 해외 brand sourcing
    "Household & Personal Products": -0.5,  # alias
    # ─── 내수 위주 (sensitivity ~0, inject 안 함)
    # "Banks - Diversified", "Telecom Services", "Utilities - Regulated Electric",
    # "Insurance - Diversified", "Specialty Retail" 등은 시장 매출 비중이
    # 거의 KRW 라 환율 영향 사소 — 명시적 entry 안 함 (compute_fx_impact 가
    # None 반환).
}


def compute_fx_impact(
    industry: Optional[str],
    krw_pct_30d: Optional[float],
) -> Optional[str]:
    """주어진 yfinance industry tag + USD/KRW 30D 변동률 로 자동 영업
    이익 영향 추정 문구 반환.

    Args:
        industry: yfinance .info industry string (예: 'Semiconductors')
        krw_pct_30d: USD/KRW 30일 % 변동 (양수 = KRW 약세, 음수 = KRW 강세)

    Returns:
        한 줄 directive ('약 +X% 영업이익 영향 추정') 또는 None
        (sensitivity 없는 산업 / 환율 변동 미약 / 입력 누락 시).

    절대값 |krw_pct| < 1.0 또는 industry 미매칭 시 None — directive 생략.
    1.0% 이상 변동 시 estimate.
    """
    if not industry or krw_pct_30d is None:
        return None
    coef = _KR_FX_SENSITIVITY.get(industry)
    if coef is None or coef == 0:
        return None
    if abs(krw_pct_30d) < 1.0:
        return None  # 1% 미만 변동은 noise 수준

    impact_pct = coef * krw_pct_30d
    krw_direction = "약세" if krw_pct_30d > 0 else "강세"
    impact_sign = "+" if impact_pct > 0 else ""
    impact_direction = (
        "긍정" if impact_pct > 0 else "부정"
    )
    business_type = (
        "수출 의존도 높음" if coef > 0
        else "수입 / 외화 부채 의존도 높음"
    )
    return (
        f"⚠️ USD/KRW 영향 추정 ({industry} = {business_type}):"
        f" USD/KRW 30D {krw_pct_30d:+.2f}% ({krw_direction}) → 영업이익"
        f" {impact_sign}{impact_pct:.1f}% {impact_direction} 영향 추정"
        f" (sensitivity coef = {coef:+.1f}; KR 증권사 리서치 평균 기반,"
        f" 실제 종목별 환헤지 비율 / 사업 포트폴리오에 따라 ±50% 변동"
        f" 가능). 5거래일 horizon 분석 시 환율 변수 본 sensitivity 와"
        f" 같이 평가."
    )
