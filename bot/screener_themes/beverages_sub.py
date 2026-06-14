"""中国 백주 — L4 under Beverages (음료).

자동 생성(부모 L3 beverages 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "中国 백주 (Beverages)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["백주"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "中国 백주 — 주요 (Kweichow Moutai 600519.SS · 五粮液 000858.SZ · 泸州老窖 000568.SZ · 山西汾酒 600809.SS)",
        "中国 백주 — 기타·공급망 (洋河 002304.SZ · 牛栏山 002247.SZ · 古井贡 000596.SZ · Wuliangye 000858.SZ · 舍得酒业 600702.SS)",
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
        "中国 백주 (주요)": "Kweichow Moutai 600519.SS · 五粮液 000858.SZ · 泸州老窖 000568.SZ · 山西汾酒 600809.SS",
        "中国 백주 (확장)": "洋河 002304.SZ · 牛栏山 002247.SZ · 古井贡 000596.SZ · Wuliangye 000858.SZ · 舍得酒业 600702.SS",
    },
}
