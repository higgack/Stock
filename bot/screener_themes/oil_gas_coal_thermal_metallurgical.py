"""Coal Thermal + Metallurgical — L4 under Oil, Gas & Consumable Fuels (석유, 가스 및 소비 연료).

자동 생성(부모 L3 oil_gas 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Coal Thermal + Metallurgical (Oil, Gas & Consumable Fuels)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["coal_thermal", "thermal", "metallurgical"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Coal Thermal + Metallurgical — 미국 (Peabody BTU · Arch Resources ARCH · Warrior Met Coal HCC · Alpha Met AMR · Consol Energy CEIX · NACCO NC · Hallador Energy HNRG · Ramaco RAMP)",
        "Coal Thermal + Metallurgical — 홍콩 (Asia (China Shenhua 1088.HK · Yanzhou 1171.HK)",
        "Coal Thermal + Metallurgical — 인도 (Coal India COALINDIA.NS)",
        "Coal Thermal + Metallurgical — 호주 (Adaro ADRO.JK), AU (Whitehaven WHC.AX · New Hope NHC.AX · Yancoal YAL.AX)",
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
        "Coal Thermal + Metallurgical (미국)": "Peabody BTU · Arch Resources ARCH · Warrior Met Coal HCC · Alpha Met AMR · Consol Energy CEIX · NACCO NC · Hallador Energy HNRG · Ramaco RAMP · Black Hills BKH (coal 부문) · Stanmore SMR.AX)",
        "Coal Thermal + Metallurgical (홍콩)": "Asia (China Shenhua 1088.HK · Yanzhou 1171.HK",
        "Coal Thermal + Metallurgical (인도)": "Coal India COALINDIA.NS",
        "Coal Thermal + Metallurgical (호주)": "Adaro ADRO.JK), AU (Whitehaven WHC.AX · New Hope NHC.AX · Yancoal YAL.AX",
    },
}
