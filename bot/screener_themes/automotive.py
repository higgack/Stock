"""Automotive — L3 under Consumer Discretionary.

L1 trend `ev.py` (EV cycle niche — 배터리 셀+양극재+분리막 등 좁은
binding) 와는 부분 overlap — 본 모듈은 자동차 industry 전체 (ICE +
EV OEM + 자동차 부품 + 자동차 유통 + AutoZone/O'Reilly aftermarket).
"""

from __future__ import annotations

THEME = {
    "domain": "Automotive (자동차 관련)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "automotive",
        "auto_l3",
        "자동차_l3",
        "auto_manufacturers",
        "자동차제조사",
        "auto_parts",
        "자동차부품",
        "auto_retail",
        "aftermarket",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "ICE + Hybrid OEM (Ford · GM · Stellantis · Toyota · Volkswagen · Hyundai)",
        "EV Pure-play (Tesla · Rivian · Lucid · NIO · XPeng · Li Auto · BYD)",
        "Luxury OEM (Ferrari · Porsche · BMW · Mercedes-Benz)",
        "Auto Parts + ADAS (Magna · Aptiv · BorgWarner · DENSO · 한온시스템)",
        "Auto Retail (AutoZone · O'Reilly · Genuine Parts · CarMax · Lithia)",
        "Tire (Goodyear · Bridgestone · Michelin · Pirelli)",
    ],
    "catalyst_types": [
        "美 IRA EV 크레딧 + Trump 행정명령 EV 정책 변경 + 美 100% 中 EV 관세",
        "OEM 분기 신차 출시 + 신모델 사이클 + 인센티브 변화",
        "中国 BYD 가격전 + LFP 셀 cost down + 美 / EU 진입 일정",
        "美 30년 모기지 금리 + 자동차 가격 + auto loss ratio (Ally)",
        "Aftermarket 분기 same-store sales (AZO · ORLY) + 노후 차량 비중",
        "ADAS / autonomous driving 채택률 + Mobileye/NVIDIA chip 수요",
    ],
    "regional_concentration": {
        "US OEM + EV": "Ford F · GM GM · Stellantis STLA · Tesla TSLA · Rivian RIVN · Lucid LCID · Polestar PSNY · Fisker 비상장 (파산 후) · VinFast VFS · Mullen MULN",
        "EU OEM + Luxury": "Volkswagen VOW3.DE · BMW BMW.DE · Mercedes MBG.DE · Stellantis STLA · Volvo VOLV-B.ST · Porsche P911.DE · Ferrari RACE · Renault RNO.PA · Aston Martin AML.L · Ducati 비상장 (VW 자회사) · Lamborghini 비상장 (VW 자회사) · Bentley 비상장 (VW 자회사) · Rolls-Royce 비상장 (BMW 자회사)",
        "Japan OEM": "Toyota 7203.T · Honda 7267.T · Nissan 7201.T · Suzuki 7269.T · Subaru 7270.T · Mazda 7261.T · Mitsubishi Motors 7211.T · Yamaha Motor 7272.T",
        "Korea OEM": "현대차 005380.KS · 기아 000270.KS · KG모빌리티 003620.KS",
        "China OEM + EV": "BYD 002594.SZ + 1211.HK · NIO NIO + 9866.HK · XPeng XPEV + 9868.HK · Li Auto LI + 2015.HK · Geely 0175.HK · SAIC 600104.SS · GAC 2238.HK · Great Wall 2333.HK · Changan 000625.SZ · Leapmotor 9863.HK · Zeekr ZK · Xiaomi 1810.HK (SU7 EV)",
        "Auto Parts + ADAS": "Magna MGA · Aptiv APTV · BorgWarner BWA · Goodyear GT · Lear LEA · Visteon VC · Adient ADNT · Modine MOD · Dana DAN · American Axle AXL · Mobileye MBLY · Garrett Motion GTX, JP (DENSO 6902.T · Aisin 7259.T · Bridgestone 5108.T · Sumitomo Electric 5802.T · Yokohama Rubber 5101.T · Toyota Industries 6201.T), KR (현대모비스 012330.KS · 한온시스템 018880.KS · 만도 204320.KS · 현대위아 011210.KS · 성우하이텍 015750.KS · S&T모티브 064960.KS), DE (Continental CON.DE · Schaeffler SHA.DE)",
        "Auto Retail + Aftermarket": "AutoZone AZO · O'Reilly Automotive ORLY · Genuine Parts GPC · CarMax KMX · Lithia Motors LAD · AutoNation AN · Group 1 GPI · Penske Automotive PAG · Sonic Automotive SAH · Asbury ABG · Vroom VRM · Carvana CVNA · Allied Motion AMOT · CarParts CPRT · Copart CPRT (자동차 경매)",
        "Tire": "Goodyear GT (US) · Bridgestone 5108.T (JP) · Michelin ML.PA (FR) · Pirelli PIRC.MI (IT) · Continental CON.DE (DE) · Sumitomo Rubber 5110.T (JP) · 한국타이어 161390.KS · 금호타이어 073240.KS · 넥센타이어 002350.KS",
    },
}
