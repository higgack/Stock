"""Uranium Mining + Enrichment — L4 under Oil, Gas & Consumable Fuels (석유, 가스 및 소비 연료).

자동 생성(부모 L3 oil_gas 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Uranium Mining + Enrichment (Oil, Gas & Consumable Fuels)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["uranium_mining", "enrichment"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Uranium Mining + Enrichment — 미국 (Cameco CCJ · Kazatomprom KAP.IL · NexGen Energy NXE · Denison Mines DNN · Ur-Energy URG · Energy Fuels UUUU · UEC UEC · Centrus Energy LEU (HALEU enrichment))",
        "Uranium Mining + Enrichment — 호주 (Paladin Energy PDN.AX · Boss Energy BOE.AX)",
        "Uranium Mining + Enrichment — 비상장 (X-energy 비상장 · TerraPower 비상장)",
        "Uranium Mining + Enrichment — 유럽 (Yellow Cake YCA.L)",
        "Uranium Mining + Enrichment — 캐나다 (Sprott Physical Uranium U.UN.TO)",
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
        "Uranium Mining + Enrichment (미국)": "Cameco CCJ · Kazatomprom KAP.IL · NexGen Energy NXE · Denison Mines DNN · Ur-Energy URG · Energy Fuels UUUU · UEC UEC · Centrus Energy LEU (HALEU enrichment) · BWX Technologies BWXT (SMR + 해군 원자로) · NuScale SMR · Oklo OKLO",
        "Uranium Mining + Enrichment (호주)": "Paladin Energy PDN.AX · Boss Energy BOE.AX",
        "Uranium Mining + Enrichment (비상장)": "X-energy 비상장 · TerraPower 비상장",
        "Uranium Mining + Enrichment (유럽)": "Yellow Cake YCA.L",
        "Uranium Mining + Enrichment (캐나다)": "Sprott Physical Uranium U.UN.TO",
    },
}
