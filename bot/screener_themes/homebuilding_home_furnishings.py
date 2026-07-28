"""Home Furnishings — L4 under Homebuilding & Furnishings (주택 건설 및 가구).

자동 생성(부모 L3 homebuilding 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Home Furnishings (Homebuilding & Furnishings)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["home", "furnishings"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Home Furnishings — 미국 (Williams-Sonoma WSM · RH RH · Tempur Sealy TPX · Mohawk MHK · La-Z-Boy LZB · Ethan Allen ETD · Leggett & Platt LEG · Sleep Number SNBR)",
        "Home Furnishings — 비상장 (Bed Bath & Beyond 비상장 (파산))",
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
        "Home Furnishings (미국)": "Williams-Sonoma WSM · RH RH · Tempur Sealy TPX · Mohawk MHK · La-Z-Boy LZB · Ethan Allen ETD · Leggett & Platt LEG · Sleep Number SNBR · Purple Innovation PRPL · Kirkland's KIRK · The Container Store TCS · Restoration Hardware RH",
        "Home Furnishings (비상장)": "Bed Bath & Beyond 비상장 (파산)",
    },
}
