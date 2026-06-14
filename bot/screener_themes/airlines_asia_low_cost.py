"""Asia Low-cost — L4 under Airlines (항공).

자동 생성(부모 L3 airlines 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Asia Low-cost (Airlines)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["asia_cost"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Asia Low-cost — 한국 (제주항공 089590.KS · 진에어 272450.KS · 티웨이 091810.KS)",
        "Asia Low-cost — 미국 (AirAsia AIRASIA.KL)",
        "Asia Low-cost — 중국 (Spring Air 春秋航空 601021.SS)",
        "Asia Low-cost — 인도 (IndiGo INDIGO.NS)",
    ],
    "catalyst_types": [
        "美 국제선 yield + 중국·일본 inbound 관광 회복 데이터",
        "Jet fuel 가격 (Brent crack spread) + airline hedge book",
        "Boeing 737 MAX + Airbus A320neo 인도 지연 → capacity 부족 → yield 보호",
        "美 FAA ATC 인력 부족 + 항로 제한 + 슬롯 경매",
        "TSA 분기 통과량 + Skift Travel Index",
        "Ryanair / easyJet capacity allocation + 신규 base 발표",
    ],
    "regional_concentration": {
        "Asia Low-cost (한국)": "제주항공 089590.KS · 진에어 272450.KS · 티웨이 091810.KS",
        "Asia Low-cost (미국)": "AirAsia AIRASIA.KL",
        "Asia Low-cost (중국)": "Spring Air 春秋航空 601021.SS",
        "Asia Low-cost (인도)": "IndiGo INDIGO.NS",
    },
}
