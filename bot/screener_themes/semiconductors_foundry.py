"""Foundry — L4 under Semiconductors & Equipment (Technology).

위탁 생산 (선단·성숙 노드) + IDM 파운드리 진입. 글로벌 #1 TSMC 집중,
N2/A16·EUV yield·美 對中 노드 규제가 dominant. 부모 L3 = semiconductors.
"""

from __future__ import annotations

THEME = {
    "domain": "Foundry (파운드리 — 반도체 위탁생산)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["wafer_fab", "위탁생산", "fab", "파운드리l4",
                "foundry_l4", "tsmc_foundry", "선단노드"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "선단 노드 (N3/N2/A16 · GAA · High-NA EUV — AI/모바일 SoC)",
        "성숙·특수 노드 (28nm~ · 전력·아날로그·이미지센서 · 자동차)",
        "IDM 파운드리 진입 (Intel Foundry · Samsung Foundry 외부 수주)",
        "中 파운드리 (국산화 · 美 entity list 영향)",
    ],
    "catalyst_types": [
        "TSMC N2/A16 양산 일정 + High-NA EUV 첫 도입 + 가동률·ASP 인상",
        "엔비디아/애플/AMD wafer 발주 + CoWoS 선단 패키징 연계 캐파",
        "Intel 18A/14A yield + 외부 파운드리 고객 확보 (IFS 손익분기)",
        "美 BIS 對中 선단 노드(<14nm) 규제 + 中 SMIC 7nm 우회 증설",
    ],
    "regional_concentration": {
        "선단 노드": "TSMC TSM + 2330.TW (글로벌 #1) · "
                  "Samsung Foundry(Samsung 005930.KS) · Intel INTC(IFS)",
        "성숙·특수": "UMC UMC + 2303.TW · GlobalFoundries GFS · "
                  "Vanguard International 5347.TWO · Tower Semiconductor TSEM · "
                  "PSMC 6770.TW",
        "中 파운드리": "SMIC 0981.HK + 688981.SS · Hua Hong 1347.HK + 688347.SS",
    },
}
