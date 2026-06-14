"""Auto Retail — L4 under Automotive (자동차 관련).

자동 생성(부모 L3 automotive 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Auto Retail (Automotive)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": [],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Auto Retail — 주요 (AutoZone AZO · O'Reilly Automotive ORLY · Genuine Parts GPC · CarMax KMX · Lithia Motors LAD · AutoNation AN · Group 1 GPI)",
        "Auto Retail — 기타·공급망 (Penske Automotive PAG · Sonic Automotive SAH · Asbury ABG · Vroom VRM · Carvana CVNA · Allied Motion AMOT · CarParts CPRT · Copart CPRT (자동차 경매))",
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
        "Auto Retail (주요)": "AutoZone AZO · O'Reilly Automotive ORLY · Genuine Parts GPC · CarMax KMX · Lithia Motors LAD · AutoNation AN · Group 1 GPI",
        "Auto Retail (확장)": "Penske Automotive PAG · Sonic Automotive SAH · Asbury ABG · Vroom VRM · Carvana CVNA · Allied Motion AMOT · CarParts CPRT · Copart CPRT (자동차 경매)",
    },
}
