"""Energy Drinks + Functional — L4 under Beverages (음료).

자동 생성(부모 L3 beverages 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Energy Drinks + Functional (Beverages)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["drinks", "functional"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Energy Drinks + Functional — 미국 (Monster Beverage MNST · Celsius CELH · Vita Coco COCO · Reed's Inc REED · GURU Organic GURU)",
        "Energy Drinks + Functional — 비상장 (Red Bull 비상장)",
    ],
    "catalyst_types": [
        "GLP-1 induced 음료 spend 변화 (Mounjaro/Wegovy → 탄산 매출 감소) 정량화",
        "美 알코올 hospitality 회복 + 분기 on-premise 매출 (Bud · Heineken)",
        "Monster/CELH 분기 매출 + 美 편의점 channel 침투율",
        "Diageo/Pernod 분기 미국 + 중국 + 신흥국 매출 + premium spirits cycle",
        "中国 백주 분기 매출 (茅台 1499元 정책 + 节日 수요 + 反腐 cycle)",
        "美 보드카 + 럼 + 위스키 출고 (Distilled Spirits Council) + craft 사이클",
    ],
    "regional_concentration": {
        "Energy Drinks + Functional (미국)": "Monster Beverage MNST · Celsius CELH · Vita Coco COCO · Reed's Inc REED · GURU Organic GURU",
        "Energy Drinks + Functional (비상장)": "Red Bull 비상장",
    },
}
