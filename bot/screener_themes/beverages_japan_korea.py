"""Japan + Korea 양조 — L4 under Beverages (음료).

자동 생성(부모 L3 beverages 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Japan + Korea 양조 (Beverages)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["japan_korea", "양조"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Japan + Korea 양조 — 한국 (하이트진로 000080.KS · 롯데칠성음료 005300.KS · 무학 033920.KS · 보해양조 000890.KS)",
        "Japan + Korea 양조 — 일본 (JP (Kirin 2503.T · Asahi 2502.T · Sapporo 2501.T · Coca-Cola Bottlers Japan 2579.T · Kagome 2811.T)",
        "Japan + Korea 양조 — 비상장 (Suntory 비상장)",
        "Japan + Korea 양조 — 미국 (Mercian-Kirin · Ito En 2593.T))",
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
        "Japan + Korea 양조 (한국)": "하이트진로 000080.KS · 롯데칠성음료 005300.KS · 무학 033920.KS · 보해양조 000890.KS",
        "Japan + Korea 양조 (일본)": "JP (Kirin 2503.T · Asahi 2502.T · Sapporo 2501.T · Coca-Cola Bottlers Japan 2579.T · Kagome 2811.T",
        "Japan + Korea 양조 (비상장)": "Suntory 비상장",
        "Japan + Korea 양조 (미국)": "Mercian-Kirin · Ito En 2593.T)",
    },
}
