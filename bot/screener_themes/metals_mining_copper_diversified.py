"""Copper + Diversified — L4 under Metals & Mining (금속 및 광업).

자동 생성(부모 L3 metals_mining 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Copper + Diversified (Metals & Mining)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": [],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Copper + Diversified — 미국 (Freeport-McMoRan FCX · BHP BHP · Rio Tinto RIO · Vale VALE · Southern Copper SCCO · Teck Resources TECK · KGHM KGH.WA · Hudbay HBM)",
        "Copper + Diversified — 유럽 (Anglo American AAL.L · Glencore GLEN.L · Antofagasta ANTO.L)",
        "Copper + Diversified — 캐나다 (First Quantum FM.TO · Ivanhoe IVN.TO · Capstone CS.TO · Lundin Mining LUN.TO)",
        "Copper + Diversified — 홍콩 (MMG 1208.HK · CMOC 3993.HK · Zijin Mining 2899.HK)",
        "Copper + Diversified — 중국 (Jiangxi Copper 600362.SS · Tongling Nonferrous 000630.SZ)",
    ],
    "catalyst_types": [
        "美/EU CBAM (철강 · 알루미늄 · 시멘트) 효력 시점",
        "전동화 + 그리드 capex 사이클 (구리 / 알루미늄 / 리튬) supply gap",
        "中国 14차 5개년 rare earth 수출 통제 + 美 reshoring + 호주 Pilbara",
        "Au/Ag 가격 + 中国 PBoC 금 매입 + Fed 정책금리 (Au inverse 상관)",
        "철강 가격 (HRC spot) + 인프라 bill 시행 + 중국 부동산 회복",
        "리튬 spot (Pilbara SC6 + Chile SQM brine) + 니켈 LME 가격",
    ],
    "regional_concentration": {
        "Copper + Diversified (미국)": "Freeport-McMoRan FCX · BHP BHP · Rio Tinto RIO · Vale VALE · Southern Copper SCCO · Teck Resources TECK · KGHM KGH.WA · Hudbay HBM · Norilsk Nickel GMKN.MM · Ero Copper ERO",
        "Copper + Diversified (유럽)": "Anglo American AAL.L · Glencore GLEN.L · Antofagasta ANTO.L",
        "Copper + Diversified (캐나다)": "First Quantum FM.TO · Ivanhoe IVN.TO · Capstone CS.TO · Lundin Mining LUN.TO",
        "Copper + Diversified (홍콩)": "MMG 1208.HK · CMOC 3993.HK · Zijin Mining 2899.HK",
        "Copper + Diversified (중국)": "Jiangxi Copper 600362.SS · Tongling Nonferrous 000630.SZ",
    },
}
