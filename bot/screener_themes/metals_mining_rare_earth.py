"""Rare Earth — L4 under Metals & Mining (금속 및 광업).

자동 생성(부모 L3 metals_mining 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Rare Earth (Metals & Mining)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["rare", "earth"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Rare Earth — 미국 (MP Materials MP · Energy Fuels UUUU (REE 부산물) · Critical Mining CMI · TMC TMC)",
        "Rare Earth — 호주 (Lynas LYC.AX · Iluka ILU.AX)",
        "Rare Earth — 중국 (中国 北方稀토 600111.SS · 中国稀土 000831.SZ)",
        "Rare Earth — 비상장 (USA Rare Earth 비상장 · Niocorp 비상장)",
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
        "Rare Earth (미국)": "MP Materials MP · Energy Fuels UUUU (REE 부산물) · Critical Mining CMI · TMC TMC",
        "Rare Earth (호주)": "Lynas LYC.AX · Iluka ILU.AX",
        "Rare Earth (중국)": "中国 北方稀토 600111.SS · 中国稀土 000831.SZ",
        "Rare Earth (비상장)": "USA Rare Earth 비상장 · Niocorp 비상장",
    },
}
