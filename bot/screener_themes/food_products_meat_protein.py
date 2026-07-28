"""Meat + Protein — L4 under Food Products (식품).

자동 생성(부모 L3 food_products 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Meat + Protein (Food Products)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["meat_protein", "protein"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Meat + Protein — 미국 (Tyson Foods TSN · Hormel Foods HRL · Pilgrim's Pride PPC · JBS JBSAY (BR) · Bunge BG · Beyond Meat BYND · Vital Farms VITL)",
        "Meat + Protein — 비상장 (Sanderson Farms 비상장 · Smithfield Foods 비상장 (WH Group 자회사))",
        "Meat + Protein — 홍콩 (WH Group 0288.HK)",
    ],
    "catalyst_types": [
        "GLP-1 induced 스낵 매출 변화 (Mounjaro/Wegovy → 짠 스낵 + 단 음식 감소)",
        "美 CDC bird flu (H5N1) outbreak + 사료 가격 + 우유/계란 가격",
        "FAO 식량 가격 지수 + 美 곡물 farm bill + 브라질 콩 작황",
        "中国 + 인도 식량 수입 변화 + 식용유 가격 (대두유 + 팜유)",
        "美 분기 비료 가격 (질소 + 인산 + 칼륨) + 비료 보조금",
        "美 FTC 인수합병 차단 (Kroger/Albertsons block 후 후속) + 식품 M&A activity",
    ],
    "regional_concentration": {
        "Meat + Protein (미국)": "Tyson Foods TSN · Hormel Foods HRL · Pilgrim's Pride PPC · JBS JBSAY (BR) · Bunge BG · Beyond Meat BYND · Vital Farms VITL",
        "Meat + Protein (비상장)": "Sanderson Farms 비상장 · Smithfield Foods 비상장 (WH Group 자회사)",
        "Meat + Protein (홍콩)": "WH Group 0288.HK",
    },
}
