"""Traditional Grocery — L4 under Food & Staples Retailing (식료품 및 필수품 소매).

자동 생성(부모 L3 food_retailing 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Traditional Grocery (Food & Staples Retailing)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["traditional_grocery"],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Traditional Grocery — 미국 (Kroger KR · Albertsons ACI · Sprouts SFM · Casey's CASY · Weis Markets WMK · Ingles IMKTA · Village Super Market VLGEA · Whole Foods (Amazon 자회사))",
        "Traditional Grocery — 비상장 (Wegmans 비상장 · Trader Joe's 비상장 · Aldi 비상장 (DE))",
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
        "Traditional Grocery (미국)": "Kroger KR · Albertsons ACI · Sprouts SFM · Casey's CASY · Weis Markets WMK · Ingles IMKTA · Village Super Market VLGEA · Whole Foods (Amazon 자회사)",
        "Traditional Grocery (비상장)": "Wegmans 비상장 · Trader Joe's 비상장 · Aldi 비상장 (DE)",
    },
}
