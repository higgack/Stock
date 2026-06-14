"""Travel + OTA — L4 under Hospitality & Leisure (호텔 및 레저).

자동 생성(부모 L3 hospitality 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Travel + OTA (Hospitality & Leisure)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": [],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Travel + OTA — 미국 (Booking Holdings BKNG · Airbnb ABNB · Expedia EXPE · TripAdvisor TRIP · Trivago TRVG · MakeMyTrip MMYT · Trip.com TCOM · Despegar DESP)",
        "Travel + OTA — 홍콩 (Tongcheng Travel 0780.HK · Ctrip 携程 9961.HK)",
        "Travel + OTA — 비상장 (야놀자 비상장 (KR))",
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
        "Travel + OTA (미국)": "Booking Holdings BKNG · Airbnb ABNB · Expedia EXPE · TripAdvisor TRIP · Trivago TRVG · MakeMyTrip MMYT · Trip.com TCOM · Despegar DESP",
        "Travel + OTA (홍콩)": "Tongcheng Travel 0780.HK · Ctrip 携程 9961.HK",
        "Travel + OTA (비상장)": "야놀자 비상장 (KR)",
    },
}
