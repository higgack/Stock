"""Building products distribution — L4 under Building Products & Construction (건축 제품 및 건설).

자동 생성(부모 L3 building_products 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Building products distribution (Building Products & Construction)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["building_distribution", "building", "distribution"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Building products distribution — 주요 (Carrier CARR · Johnson Controls JCI · Trane TT · Otis OTIS · Lennox LII · A.O. Smith AOS)",
        "Building products distribution — 기타·공급망 (Owens Corning OC · Masco MAS · Allegion ALLE · Watsco WSO · Pool Corp POOL · Vulcan Materials VMC · Martin Marietta MLM)",
    ],
    "catalyst_types": [
        "HVAC R-454B 냉매 전환 (2025 effective) → AC replacement cycle 강제",
        "美 데이터센터 + 반도체 fab construction starts (CHIPS Act 시행)",
        "美 인프라 bill (IIJA) 도로·교량 capex 집행률 + 비행장 modernization",
        "美 주택 착공 + 30Y 모기지 금리 + 신규 단독주택 inventory",
        "EU 에너지 효율 직접 + heat pump 보조금 + 영국 grant scheme",
        "中国 14차 5개년 도시화 + 노후 단지 재개발 보조금",
    ],
    "regional_concentration": {
        "Building products distribution (주요)": "Carrier CARR · Johnson Controls JCI · Trane TT · Otis OTIS · Lennox LII · A.O. Smith AOS",
        "Building products distribution (확장)": "Owens Corning OC · Masco MAS · Allegion ALLE · Watsco WSO · Pool Corp POOL · Vulcan Materials VMC · Martin Marietta MLM",
    },
}
