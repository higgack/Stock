"""EU Flag Carriers — L4 under Airlines (항공).

자동 생성(부모 L3 airlines 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "EU Flag Carriers (Airlines)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["flag_carriers", "flag"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "EU Flag Carriers — 유럽 (Lufthansa LHA.DE · IAG IAG.L · Air France-KLM AF.PA · easyJet EZJ.L · Wizz Air WIZZ.L · SAS SAS.ST · Finnair FIA1S.HE · TAP TAP.LS)",
        "EU Flag Carriers — 미국 (Ryanair RYAAY)",
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
        "EU Flag Carriers (유럽)": "Lufthansa LHA.DE · IAG IAG.L · Air France-KLM AF.PA · easyJet EZJ.L · Wizz Air WIZZ.L · SAS SAS.ST · Finnair FIA1S.HE · TAP TAP.LS",
        "EU Flag Carriers (미국)": "Ryanair RYAAY",
    },
}
