"""Property Listings + Data — L4 under Real Estate Services (부동산 서비스).

자동 생성(부모 L3 real_estate_services 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Property Listings + Data (Real Estate Services)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["property_listings", "listings"],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Property Listings + Data — 주요 (CoStar Group CSGP · Zillow Z · Redfin RDFN · Realtor.com (News Corp 자회사 NWS) · Trulia (Zillow 인수 후) · Costar CSGP)",
        "Property Listings + Data — 기타·공급망 (Homes.com (CoStar) · Apartments.com (CoStar) · LoopNet (CoStar) · Land.com (CoStar) · Move (News Corp) · BRT Apartments BRT)",
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
        "Property Listings + Data (주요)": "CoStar Group CSGP · Zillow Z · Redfin RDFN · Realtor.com (News Corp 자회사 NWS) · Trulia (Zillow 인수 후) · Costar CSGP",
        "Property Listings + Data (확장)": "Homes.com (CoStar) · Apartments.com (CoStar) · LoopNet (CoStar) · Land.com (CoStar) · Move (News Corp) · BRT Apartments BRT",
    },
}
