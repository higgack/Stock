"""Hotels + Resorts — L4 under Hospitality & Leisure (호텔 및 레저).

자동 생성(부모 L3 hospitality 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Hotels + Resorts (Hospitality & Leisure)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["hotels_resorts", "hotels", "resorts"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Hotels + Resorts — 미국 (Marriott MAR · Hilton HLT · Hyatt H · IHG IHG · Choice Hotels CHH · Wyndham WH · Park Hotels PK · Host Hotels HST)",
        "Hotels + Resorts — 유럽 (Pebblebrook PEB, EU (Accor AC.PA)",
        "Hotels + Resorts — 한국 (KR (호텔신라 008770.KS · 파라다이스 034230.KQ · 강원랜드 035250.KS)",
        "Hotels + Resorts — 비상장 (JP (Hilton/Marriott Japan 비상장)",
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
        "Hotels + Resorts (미국)": "Marriott MAR · Hilton HLT · Hyatt H · IHG IHG · Choice Hotels CHH · Wyndham WH · Park Hotels PK · Host Hotels HST · Ryman RHP · Apple Hospitality APLE · IHG IHG · Whitbread WTB.L)",
        "Hotels + Resorts (유럽)": "Pebblebrook PEB, EU (Accor AC.PA",
        "Hotels + Resorts (한국)": "KR (호텔신라 008770.KS · 파라다이스 034230.KQ · 강원랜드 035250.KS",
        "Hotels + Resorts (비상장)": "JP (Hilton/Marriott Japan 비상장",
    },
}
