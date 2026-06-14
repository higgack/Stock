"""Integrated Majors — L4 under Oil, Gas & Consumable Fuels (석유, 가스 및 소비 연료).

자동 생성(부모 L3 oil_gas 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Integrated Majors (Oil, Gas & Consumable Fuels)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["integrated_majors", "majors"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Integrated Majors — 미국 (ExxonMobil XOM · Chevron CVX · Shell SHEL · BP BP · TotalEnergies TTE · Petrobras PBR)",
        "Integrated Majors — 유럽 (Eni ENI.MI · Equinor EQNR.OL · Repsol REP.MC · OMV OMV.VI · Galp Energia GALP.LS)",
        "Integrated Majors — 일본 (Inpex 1605.T)",
        "Integrated Majors — 홍콩 (Cnooc 0883.HK · Sinopec 0386.HK · PetroChina 0857.HK)",
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
        "Integrated Majors (미국)": "ExxonMobil XOM · Chevron CVX · Shell SHEL · BP BP · TotalEnergies TTE · Petrobras PBR",
        "Integrated Majors (유럽)": "Eni ENI.MI · Equinor EQNR.OL · Repsol REP.MC · OMV OMV.VI · Galp Energia GALP.LS",
        "Integrated Majors (일본)": "Inpex 1605.T",
        "Integrated Majors (홍콩)": "Cnooc 0883.HK · Sinopec 0386.HK · PetroChina 0857.HK",
    },
}
