"""Fast Casual + QSR Restaurants — L4 under Hospitality & Leisure (호텔 및 레저).

자동 생성(부모 L3 hospitality 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Fast Casual + QSR Restaurants (Hospitality & Leisure)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["fast_casual", "casual", "restaurants"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Fast Casual + QSR Restaurants — 주요 (McDonald's MCD · Starbucks SBUX · Chipotle CMG · Yum! Brands YUM · Restaurant Brands QSR · CAVA Group CAVA · Sweetgreen SG)",
        "Fast Casual + QSR Restaurants — 기타·공급망 (Wingstop WING · Wendy's WEN · Domino's DPZ · Papa John's PZZA · Shake Shack SHAK · Dutch Bros BROS · Krispy Kreme DNUT)",
    ],
    "catalyst_types": [
        "분기 RevPAR (Marriott/Hilton/IHG) + 美 호텔 점유율 + 평일/주말 mix",
        "Booking/Expedia/Airbnb 분기 nights booked + take rate + 中国 outbound 회복",
        "美 외식 매출 (Census Bureau) + SBUX/MCD 분기 same-store sales + 인플레이션 영향",
        "DraftKings/Flutter 분기 NGR (Net Gaming Revenue) + 美 sports betting 신규 주 launch",
        "Cruise 신규 ship delivery + bookings 가이드 + Caribbean/Med occupancy",
        "中国 Macau gaming GGR 회복 + 일본 IR (Integrated Resort) 라이선스",
    ],
    "regional_concentration": {
        "Fast Casual + QSR Restaurants (주요)": "McDonald's MCD · Starbucks SBUX · Chipotle CMG · Yum! Brands YUM · Restaurant Brands QSR · CAVA Group CAVA · Sweetgreen SG",
        "Fast Casual + QSR Restaurants (확장)": "Wingstop WING · Wendy's WEN · Domino's DPZ · Papa John's PZZA · Shake Shack SHAK · Dutch Bros BROS · Krispy Kreme DNUT",
    },
}
