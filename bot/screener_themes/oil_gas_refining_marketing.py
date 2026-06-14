"""Refining & Marketing — L4 under Oil, Gas & Consumable Fuels (석유, 가스 및 소비 연료).

자동 생성(부모 L3 oil_gas 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Refining & Marketing (Oil, Gas & Consumable Fuels)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["refining_marketing", "marketing"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Refining & Marketing — 미국 (Marathon Petroleum MPC · Valero Energy VLO · Phillips 66 PSX · HF Sinclair DINO · PBF Energy PBF · Delek DK · CVR Energy CVI)",
        "Refining & Marketing — 유럽 (Par Pacific PARR, EU (Repsol REP.MC)",
        "Refining & Marketing — 한국 (Neste NESTE.HE — SAF leader), KR (S-Oil 010950.KS · SK이노베이션 096770.KS)",
        "Refining & Marketing — 비상장 (GS칼텍스 비상장))",
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
        "Refining & Marketing (미국)": "Marathon Petroleum MPC · Valero Energy VLO · Phillips 66 PSX · HF Sinclair DINO · PBF Energy PBF · Delek DK · CVR Energy CVI",
        "Refining & Marketing (유럽)": "Par Pacific PARR, EU (Repsol REP.MC",
        "Refining & Marketing (한국)": "Neste NESTE.HE — SAF leader), KR (S-Oil 010950.KS · SK이노베이션 096770.KS",
        "Refining & Marketing (비상장)": "GS칼텍스 비상장)",
    },
}
