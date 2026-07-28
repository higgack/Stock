"""E&P US Shale Focused — L4 under Oil, Gas & Consumable Fuels (석유, 가스 및 소비 연료).

자동 생성(부모 L3 oil_gas 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "E&P US Shale Focused (Oil, Gas & Consumable Fuels)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["shale_focused", "shale", "focused"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "E&P US Shale Focused — 미국 (ConocoPhillips COP · EOG EOG · Occidental OXY · Devon DVN · Diamondback FANG · APA APA · Marathon Oil MRO · Coterra CTRA)",
        "E&P US Shale Focused — 비상장 (Pioneer 비상장 (ExxonMobil 인수) · Continental 비상장 (Hamm 인수))",
    ],
    "catalyst_types": [
        "OPEC+ 생산 결정 (월별 JMMC) + 미국 SPR refill 재개 시점",
        "Permian shale 일일 출하량 plateau / 감소 데이터 + 신규 well productivity",
        "LNG Phase 2 가동 일정 (Plaquemines T1 / Corpus Christi T3 / Rio Grande T1)",
        "美 IRA 45Z (SAF) + 45Q (CCUS) 가이드 변경 + EU CBAM 효력",
        "우라늄 long-term contract pricing (유틸리티 procurement cycle)",
        "SMR 美 NRC 인허가 (NuScale · X-energy · Oklo · TerraPower · BWXT)",
    ],
    "regional_concentration": {
        "E&P US Shale Focused (미국)": "ConocoPhillips COP · EOG EOG · Occidental OXY · Devon DVN · Diamondback FANG · APA APA · Marathon Oil MRO · Coterra CTRA · Range Resources RRC · Ovintiv OVV · CNX Resources CNX · Antero AR",
        "E&P US Shale Focused (비상장)": "Pioneer 비상장 (ExxonMobil 인수) · Continental 비상장 (Hamm 인수)",
    },
}
