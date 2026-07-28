"""Hospitality & Leisure — L3 under Consumer Discretionary.

Hotels + Travel/OTA + Restaurants + Gambling/Casino. RevPAR + 美 국제선
회복 + 中国 inbound + Booking 분기 nights book + 美 fast-casual 매출이
주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Hospitality & Leisure (호텔 및 레저)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "hospitality",
        "hospitality_l3",
        "호텔레저",
        "hotel_l3",
        "호텔_l3",
        "cruise",
        "크루즈",
        "restaurant_l3",
        "레스토랑_l3",
        "casino",
        "카지노",
        "gambling",
        "travel_l3",
    ],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Hotels + Resorts (Marriott · Hilton · Hyatt · IHG · Wynn · MGM)",
        "Cruise Lines (Carnival · Royal Caribbean · Norwegian)",
        "Travel + OTA (Booking · Airbnb · Expedia · TripAdvisor)",
        "Fast Casual + QSR Restaurants (McD · SBUX · Chipotle · Yum · CAVA)",
        "Full-Service + Specialty Restaurants (Darden · Cheesecake · Texas Roadhouse)",
        "Gambling + Casino Operators (Las Vegas Sands · MGM · Wynn · DraftKings)",
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
        "Hotels + Resorts": "Marriott MAR · Hilton HLT · Hyatt H · IHG IHG · Choice Hotels CHH · Wyndham WH · Park Hotels PK · Host Hotels HST · Ryman RHP · Apple Hospitality APLE · Pebblebrook PEB, EU (Accor AC.PA · IHG IHG · Whitbread WTB.L) · KR (호텔신라 008770.KS · 파라다이스 034230.KQ · 강원랜드 035250.KS · GKL 114090.KS) · JP (Hilton/Marriott Japan 비상장 · Resorttrust 4681.T)",
        "Cruise Lines": "Carnival CCL · Royal Caribbean RCL · Norwegian Cruise Line NCLH · Lindblad Expeditions LIND · OneSpaWorld OSW",
        "Travel + OTA": "Booking Holdings BKNG · Airbnb ABNB · Expedia EXPE · TripAdvisor TRIP · Trivago TRVG · MakeMyTrip MMYT · Tongcheng Travel 0780.HK · Trip.com TCOM · Despegar DESP · Ctrip 携程 9961.HK · 야놀자 비상장 (KR)",
        "QSR + Fast Casual": "McDonald's MCD · Starbucks SBUX · Chipotle CMG · Yum! Brands YUM · Restaurant Brands QSR · CAVA Group CAVA · Sweetgreen SG · Wingstop WING · Wendy's WEN · Domino's DPZ · Papa John's PZZA · Shake Shack SHAK · Dutch Bros BROS · Krispy Kreme DNUT",
        "Casual Dining + Specialty": "Darden DRI · Texas Roadhouse TXRH · Brinker EAT · Cheesecake Factory CAKE · Bloomin' Brands BLMN · Cracker Barrel CBRL · BJ's Restaurants BJRI · El Pollo Loco LOCO · First Watch FWRG · Portillo's PTLO · Kura Sushi KRUS",
        "Casino + Gambling": "Las Vegas Sands LVS · MGM Resorts MGM · Wynn Resorts WYNN · Caesars CZR · Penn Entertainment PENN · DraftKings DKNG · Flutter Entertainment FLUT · Sportradar SRAD · Boyd Gaming BYD · Bally's BALY · Golden Nugget Online 비상장 · Rush Street Interactive RSI · Genting GENS.SI · SJM 0880.HK · Galaxy Entertainment 0027.HK · Sands China 1928.HK · Wynn Macau 1128.HK · MGM China 2282.HK · Melco MLCO",
        "Vail/Theme Parks": "Vail Resorts MTN · Six Flags SIX · Cedar Fair FUN · Disney DIS (parks · 분리 보고) · Oriental Land 4661.T (Disney Tokyo · Disney Sea) · Round One 4680.T · Tokyo Dome 9681.T · Seibu 9024.T (railway + Prince Hotels)",
    },
}
