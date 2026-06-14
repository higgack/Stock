"""ESS Integrator + Storage — L4 under Independent Power & Renewable Electricity Producers (독립 발전 및 재생에너지).

자동 생성(부모 L3 ipp_renewable 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "ESS Integrator + Storage (Independent Power & Renewable Electricity Producers)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["integrator_storage", "integrator"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "ESS Integrator + Storage — 미국 (Fluence FLNC · Tesla TSLA (Megapack) · Stem STEM)",
        "ESS Integrator + Storage — 중국 (Sungrow 300274.SZ · CATL 300750.SZ · EVE 300014.SZ)",
        "ESS Integrator + Storage — 한국 (LG에너지솔루션 373220.KS · 삼성SDI 006400.KS · 에코프로비엠 247540.KQ · POSCO퓨처엠 003670.KS)",
        "ESS Integrator + Storage — 비상장 (SK on 비상장)",
        "ESS Integrator + Storage — 홍콩 (BYD 002594.SZ + 1211.HK)",
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
        "ESS Integrator + Storage (미국)": "Fluence FLNC · Tesla TSLA (Megapack) · Stem STEM",
        "ESS Integrator + Storage (중국)": "Sungrow 300274.SZ · CATL 300750.SZ · EVE 300014.SZ",
        "ESS Integrator + Storage (한국)": "LG에너지솔루션 373220.KS · 삼성SDI 006400.KS · 에코프로비엠 247540.KQ · POSCO퓨처엠 003670.KS",
        "ESS Integrator + Storage (비상장)": "SK on 비상장",
        "ESS Integrator + Storage (홍콩)": "BYD 002594.SZ + 1211.HK",
    },
}
