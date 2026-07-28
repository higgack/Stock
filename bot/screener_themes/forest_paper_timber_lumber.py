"""Timber + Lumber — L4 under Forest & Paper Products (임업 및 제지).

자동 생성(부모 L3 forest_paper 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Timber + Lumber (Forest & Paper Products)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["timber_lumber"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Timber + Lumber — 주요 (Weyerhaeuser WY · Rayonier RYN · PotlatchDeltic PCH · Boise Cascade BCC)",
        "Timber + Lumber — 기타·공급망 (Louisiana-Pacific LPX · UFP Industries UFPI · 西方 Forestry WY · Catchmark Timber CTT · Forestar Group FOR)",
    ],
    "catalyst_types": [
        "美 신규 주택 건설 + 30Y 모기지 금리 → lumber 수요 + 분기 매출",
        "Pulp/Paper 가격 (NBSK/BHK) + e-commerce 포장재 수요 + 中国 수출 둔화",
        "美 산불 후 timber 가격 + 분기 inventory 회복 (특히 California · Oregon)",
        "EU CBAM + ESG 의무 + 영국 plastic packaging tax → 친환경 포장재 수요",
        "Weyerhaeuser 분기 timberland 매출 + 신규 home building 입주",
        "中国 임업 수입 + 일본 친환경 건축 의무",
    ],
    "regional_concentration": {
        "Timber + Lumber (주요)": "Weyerhaeuser WY · Rayonier RYN · PotlatchDeltic PCH · Boise Cascade BCC",
        "Timber + Lumber (확장)": "Louisiana-Pacific LPX · UFP Industries UFPI · 西方 Forestry WY · Catchmark Timber CTT · Forestar Group FOR",
    },
}
