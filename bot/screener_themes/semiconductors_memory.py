"""Memory Semiconductors — L4 under Semiconductors & Equipment (Technology).

DRAM (DDR5 · LPDDR5X) + HBM (HBM3E · HBM4) + NAND (3D TLC/QLC · eSSD) +
컨트롤러/모듈 (SSD 컨트롤러 · CXL). AI 가속기 대역폭 병목의 핵심 — HBM
할당·DRAM 사이클·감산이 dominant. 부모 L3 = semiconductors.
"""

from __future__ import annotations

THEME = {
    "domain": "Memory Semiconductors (메모리 반도체 — DRAM·HBM·NAND)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["dram", "nand", "디램", "낸드", "메모리반도체",
                "hbm_memory", "ddr5", "메모리반도체l4"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "DRAM (DDR5 · LPDDR5X — 서버/PC/모바일, 감산·증산 사이클)",
        "HBM (HBM3E · HBM4 — AI 가속기 대역폭 병목, TSV·하이브리드 본딩)",
        "NAND Flash (3D TLC/QLC · 데이터센터 eSSD · 고적층 경쟁)",
        "컨트롤러·모듈·CXL (SSD 컨트롤러 · CXL 메모리 풀링)",
    ],
    "catalyst_types": [
        "DRAM/NAND spot·계약가 (TrendForce/DRAMeXchange) + 감산→증산 전환",
        "HBM3E/HBM4 양산 비중 + 엔비디아/AMD 할당 비율 (SK Hynix vs Samsung)",
        "AI 서버 DDR5 콘텐츠 증가 + CXL 메모리 풀링 채택 + eSSD QLC 전환",
        "美 BIS 對中 메모리 장비 규제 + 中 CXMT/YMTC 증설 속도",
    ],
    "regional_concentration": {
        "DRAM·HBM": "Samsung Electronics 005930.KS · SK Hynix 000660.KS · "
                    "Micron Technology MU · Nanya 2408.TW · CXMT 비상장(中)",
        "NAND": "Samsung 005930.KS · SK Hynix(Solidigm) 000660.KS · "
                "Micron MU · Kioxia 285A.T · Western Digital WDC · YMTC 비상장(中)",
        "컨트롤러·모듈": "Phison 8299.TWO · Silicon Motion SIMO · "
                     "Marvell MRVL(CXL) · Montage 688008.SS",
    },
}
