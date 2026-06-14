"""Convenience Stores — L4 under Food & Staples Retailing (식료품 및 필수품 소매).

자동 생성(부모 L3 food_retailing 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Convenience Stores (Food & Staples Retailing)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["convenience_stores", "convenience"],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Convenience Stores — 미국 (Casey's General Stores CASY · Murphy USA MUSA · Couche-Tard ATD.TO (Circle K), JP (Seven & i 3382.T — 7-Eleven)",
        "Convenience Stores — 일본 (Lawson 2651.T · Aeon 8267.T)",
        "Convenience Stores — 비상장 (FamilyMart 비상장 - Itochu 자회사 · Costco Japan 비상장), KR (이마트24 비상장 · 세븐일레븐 비상장 - 롯데 자회사 · 미니스톱 비상장 - Aeon 자회사))",
        "Convenience Stores — 한국 (GS25 - GS리테일 007070.KS · CU - BGF리테일 282330.KS)",
    ],
    "catalyst_types": [
        "美 SNAP 보조금 변화 + 분기 grocery sales (Census · USDA)",
        "Walmart 분기 same-store sales + e-commerce 침투율 + AWS-like 광고 매출",
        "Costco 멤버십 갱신율 + Kirkland 자체 브랜드 비중 + e-commerce 성장",
        "美 인플레이션 영향 + minimum wage 인상 → 슈퍼마켓 영업 마진",
        "한국 편의점 분기 매출 (CU · GS25 · 7-Eleven · Emart24) + 1인 가구 증가",
        "Dollar General 분기 same-store sales + 저소득층 소비 cycle",
    ],
    "regional_concentration": {
        "Convenience Stores (미국)": "Casey's General Stores CASY · Murphy USA MUSA · Couche-Tard ATD.TO (Circle K), JP (Seven & i 3382.T — 7-Eleven",
        "Convenience Stores (일본)": "Lawson 2651.T · Aeon 8267.T",
        "Convenience Stores (비상장)": "FamilyMart 비상장 - Itochu 자회사 · Costco Japan 비상장), KR (이마트24 비상장 · 세븐일레븐 비상장 - 롯데 자회사 · 미니스톱 비상장 - Aeon 자회사)",
        "Convenience Stores (한국)": "GS25 - GS리테일 007070.KS · CU - BGF리테일 282330.KS",
    },
}
