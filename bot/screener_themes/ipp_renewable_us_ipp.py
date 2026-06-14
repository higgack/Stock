"""US IPP — L4 under Independent Power & Renewable Electricity Producers (독립 발전 및 재생에너지).

자동 생성(부모 L3 ipp_renewable 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "US IPP (Independent Power & Renewable Electricity Producers)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": [],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "US IPP — 미국 (Vistra Corp VST · Constellation Energy CEG · NRG Energy NRG · Talen Energy TLN · Eversource ES (IPP 부문))",
        "US IPP — 비상장 (Calpine 비상장)",
    ],
    "catalyst_types": [
        "美 IRA 45X (Solar/Wind production credit) + Trump 행정명령 IRA roll-back",
        "BOEM offshore wind lease + Notice To Proceed + 한국 풍력 RFP",
        "ISO interconnection queue + 美 송전망 신규 line 승인 (CAISO · ERCOT · PJM)",
        "Enphase/SolarEdge 분기 microinverter sell-through + 美 residential solar 회복",
        "EU CBAM + 中国 polysilicon 반덤핑 + 영국 CfD AR6 입찰 결과",
        "Bloom Energy 분기 fuel cell delivery + 데이터센터 SOFC 채택",
    ],
    "regional_concentration": {
        "US IPP (미국)": "Vistra Corp VST · Constellation Energy CEG · NRG Energy NRG · Talen Energy TLN · Eversource ES (IPP 부문)",
        "US IPP (비상장)": "Calpine 비상장",
    },
}
