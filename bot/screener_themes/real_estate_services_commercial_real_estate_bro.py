"""Commercial Real Estate Brokerage — L4 under Real Estate Services (부동산 서비스).

자동 생성(부모 L3 real_estate_services 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Commercial Real Estate Brokerage (Real Estate Services)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["commercial_real", "real", "estate"],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Commercial Real Estate Brokerage — 미국 (CBRE Group CBRE · Jones Lang LaSalle JLL · Cushman & Wakefield CWK · Newmark NMRK · Walker & Dunlop WD · Marcus & Millichap MMI · Colliers International CIGI · Savills SVS.L (UK))",
        "Commercial Real Estate Brokerage — 비상장 (Knight Frank 비상장 · Avison Young 비상장)",
    ],
    "catalyst_types": [
        "美 분기 CRE 매출 (CBRE · JLL · Cushman) + leasing volume 회복",
        "美 30Y 모기지 금리 + 주택 거래량 + Zillow / Redfin 분기 매출",
        "美 office vacancy + CRE 만기 wall + sub-lease 시장",
        "데이터센터 + 산업 leasing + 신규 데이터센터 capacity (Equinix · DLR)",
        "CoStar Apartments.com + LoopNet + Homes.com 분기 매출 성장",
        "Compass / eXp 분기 agent count + 분기 GAV (Gross Agent Value)",
    ],
    "regional_concentration": {
        "Commercial Real Estate Brokerage (미국)": "CBRE Group CBRE · Jones Lang LaSalle JLL · Cushman & Wakefield CWK · Newmark NMRK · Walker & Dunlop WD · Marcus & Millichap MMI · Colliers International CIGI · Savills SVS.L (UK)",
        "Commercial Real Estate Brokerage (비상장)": "Knight Frank 비상장 · Avison Young 비상장",
    },
}
