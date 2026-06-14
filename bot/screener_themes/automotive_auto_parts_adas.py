"""Auto Parts + ADAS — L4 under Automotive (자동차 관련).

자동 생성(부모 L3 automotive 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Auto Parts + ADAS (Automotive)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["parts", "adas"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Auto Parts + ADAS — 미국 (Magna MGA · Aptiv APTV · BorgWarner BWA · Goodyear GT · Lear LEA · Visteon VC · Adient ADNT · Modine MOD)",
        "Auto Parts + ADAS — 일본 (Garrett Motion GTX, JP (DENSO 6902.T · Aisin 7259.T · Bridgestone 5108.T · Sumitomo Electric 5802.T · Yokohama Rubber 5101.T)",
        "Auto Parts + ADAS — 한국 (Toyota Industries 6201.T), KR (현대모비스 012330.KS · 한온시스템 018880.KS · 만도 204320.KS · 현대위아 011210.KS · 성우하이텍 015750.KS)",
        "Auto Parts + ADAS — 유럽 (S&T모티브 064960.KS), DE (Continental CON.DE)",
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
        "Auto Parts + ADAS (미국)": "Magna MGA · Aptiv APTV · BorgWarner BWA · Goodyear GT · Lear LEA · Visteon VC · Adient ADNT · Modine MOD · Dana DAN · American Axle AXL · Mobileye MBLY · Schaeffler SHA.DE)",
        "Auto Parts + ADAS (일본)": "Garrett Motion GTX, JP (DENSO 6902.T · Aisin 7259.T · Bridgestone 5108.T · Sumitomo Electric 5802.T · Yokohama Rubber 5101.T",
        "Auto Parts + ADAS (한국)": "Toyota Industries 6201.T), KR (현대모비스 012330.KS · 한온시스템 018880.KS · 만도 204320.KS · 현대위아 011210.KS · 성우하이텍 015750.KS",
        "Auto Parts + ADAS (유럽)": "S&T모티브 064960.KS), DE (Continental CON.DE",
    },
}
