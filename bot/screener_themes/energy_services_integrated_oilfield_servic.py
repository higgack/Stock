"""Integrated Oilfield Services — L4 under Energy Equipment & Services (에너지 장비 및 서비스).

자동 생성(부모 L3 energy_services 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Integrated Oilfield Services (Energy Equipment & Services)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["integrated_oilfield", "integrated", "oilfield"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Integrated Oilfield Services — 주요 (Schlumberger SLB)",
        "Integrated Oilfield Services — 기타·공급망 (Halliburton HAL · Baker Hughes BKR)",
    ],
    "catalyst_types": [
        "SLB · HAL · BKR 분기 international revenue + 中东 + Latin Amer 사이클",
        "美 rig count (Baker Hughes 주간) + frac fleet utilization",
        "Offshore deepwater 신규 FID (Final Investment Decision) — 브라질 · 가이아나 · West Africa",
        "Permian shale 잔존 inventory + 신규 well productivity",
        "TechnipFMC · Subsea7 분기 backlog + 풍력 + offshore 신규 EPC 발주",
        "Trump 행정명령 - 美 LNG export 부활 + offshore lease 재개",
    ],
    "regional_concentration": {
        "Integrated Oilfield Services (주요)": "Schlumberger SLB",
        "Integrated Oilfield Services (확장)": "Halliburton HAL · Baker Hughes BKR",
    },
}
