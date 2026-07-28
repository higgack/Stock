"""Security — L4 under Commercial & Professional Services (상업 및 전문 서비스).

자동 생성(부모 L3 commercial_services 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Security (Commercial & Professional Services)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["security"],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Security — 주요 (Cintas CTAS · ABM Industries ABM · Rollins ROL)",
        "Security — 기타·공급망 (UniFirst UNF · Service Corp SCI · ADT ADT · Brinks BCO)",
    ],
    "catalyst_types": [
        "美 BLS Wage Cost Index + ADP 비농업 고용 → staffing 매출 직결",
        "美 JOLTS 분기 채용 공고 + Unemployment rate + 분기 임금 상승률",
        "GenAI / LLM 의 IT consulting / outsourcing 시장 disruption",
        "美 GDP + 기업 capex 사이클 → CRE inspection + facility services 수요",
        "전기차·로봇 reshoring → 분기 자동화 컨설팅 매출 (Accenture · BAH)",
        "보안 + 가드 시장 — 정부 + 데이터센터 + 헬스케어 capex",
    ],
    "regional_concentration": {
        "Security (주요)": "Cintas CTAS · ABM Industries ABM · Rollins ROL",
        "Security (확장)": "UniFirst UNF · Service Corp SCI · ADT ADT · Brinks BCO",
    },
}
