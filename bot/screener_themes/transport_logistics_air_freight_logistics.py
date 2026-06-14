"""Air Freight & Logistics — L4 under Transportation & Logistics (운송 및 물류).

자동 생성(부모 L3 transport_logistics 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Air Freight & Logistics (Transportation & Logistics)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["freight_logistics", "freight"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Air Freight & Logistics — 미국 (UPS UPS · FedEx FDX · C.H. Robinson CHRW · Expeditors EXPD · GXO Logistics GXO · Forward Air FWRD · Hub Group HUBG · Air Transport Services ATSG)",
        "Air Freight & Logistics — 비상장 (Atlas Air 비상장)",
    ],
    "catalyst_types": [
        "ATA 트럭 tonnage index + DAT spot rate (trucking 회복 시점)",
        "AAR 분기 carload + intermodal (rail volume) + service score",
        "SCFI / Drewry 컨테이너 spot rate + Red Sea/Suez 우회 지속",
        "Jet fuel 가격 + 美 국제선 yield + Cargo (FedEx/UPS) 분기 가이드",
        "US-China 무역 deal + Section 301 관세 변경 → trans-Pacific 컨테이너 flow",
        "美 reshoring + 멕시코 nearshoring → rail · trucking 신규 corridors",
    ],
    "regional_concentration": {
        "Air Freight & Logistics (미국)": "UPS UPS · FedEx FDX · C.H. Robinson CHRW · Expeditors EXPD · GXO Logistics GXO · Forward Air FWRD · Hub Group HUBG · Air Transport Services ATSG",
        "Air Freight & Logistics (비상장)": "Atlas Air 비상장",
    },
}
