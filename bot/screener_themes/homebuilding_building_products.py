"""Building Products — L4 under Homebuilding & Furnishings (주택 건설 및 가구).

자동 생성(부모 L3 homebuilding 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Building Products (Homebuilding & Furnishings)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": [],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Building Products — 미국 (Builders FirstSource BLDR · Eagle Materials EXP · Vulcan Materials VMC · Martin Marietta MLM · Summit Materials SUM · Mohawk MHK · Owens Corning OC · Masco MAS)",
        "Building Products — 비상장 (USG 비상장)",
    ],
    "catalyst_types": [
        "美 30Y 모기지 금리 + Fed funds rate path → 주택 거래량 + 분기 수주",
        "신규 단독주택 inventory + 매물 회복 + 가격 변동 (Census · Zillow)",
        "DR Horton/Lennar/PulteGroup 분기 수주 backlog + 신규 community 발표",
        "Williams-Sonoma/RH/Tempur 분기 매출 + 美 인테리어 + 리모델링 사이클",
        "美 IIJA + 신규 인프라 capex → 건자재 수요 변화",
        "Fed 양적완화 종료 + 中国 부동산 위기 후 글로벌 furniture trade impact",
    ],
    "regional_concentration": {
        "Building Products (미국)": "Builders FirstSource BLDR · Eagle Materials EXP · Vulcan Materials VMC · Martin Marietta MLM · Summit Materials SUM · Mohawk MHK · Owens Corning OC · Masco MAS · A. O. Smith AOS · Fortune Brands FBHS · Beacon Roofing BECN · GMS GMS",
        "Building Products (비상장)": "USG 비상장",
    },
}
