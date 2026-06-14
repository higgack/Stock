"""LNG Producers + Infra — L4 under Oil, Gas & Consumable Fuels (석유, 가스 및 소비 연료).

자동 생성(부모 L3 oil_gas 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "LNG Producers + Infra (Oil, Gas & Consumable Fuels)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["producers_infra", "producers", "infra"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "LNG Producers + Infra — 미국 (Cheniere LNG · NextDecade NEXT · Venture Global VG · Tellurian TELL · Sempra SRE · Cheniere Energy Partners CQP · Pembina Pipeline PPL · Tokyo Gas 9531.T))",
        "LNG Producers + Infra — 일본 (TC Energy TRP, KR (KOGAS 036460.KS), JP (Inpex 1605.T)",
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
        "LNG Producers + Infra (미국)": "Cheniere LNG · NextDecade NEXT · Venture Global VG · Tellurian TELL · Sempra SRE · Cheniere Energy Partners CQP · Pembina Pipeline PPL · Tokyo Gas 9531.T)",
        "LNG Producers + Infra (일본)": "TC Energy TRP, KR (KOGAS 036460.KS), JP (Inpex 1605.T",
    },
}
