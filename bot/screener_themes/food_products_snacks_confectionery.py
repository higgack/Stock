"""Snacks + Confectionery — L4 under Food Products (식품).

자동 생성(부모 L3 food_products 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Snacks + Confectionery (Food Products)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["snacks_confectionery", "snacks", "confectionery"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Snacks + Confectionery — 미국 (Mondelez MDLZ (Oreo · Chips Ahoy · Cadbury) · Hershey HSY · Tootsie Roll TR · Hostess Brands TWNK · Utz Brands UTZ · J&J Snack Foods JJSF)",
        "Snacks + Confectionery — 비상장 (Mars 비상장 · Ferrero 비상장)",
        "Snacks + Confectionery — 유럽 (Lindt LISN.SW · Nestlé NESN.SW)",
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
        "Snacks + Confectionery (미국)": "Mondelez MDLZ (Oreo · Chips Ahoy · Cadbury) · Hershey HSY · Tootsie Roll TR · Hostess Brands TWNK · Utz Brands UTZ · J&J Snack Foods JJSF",
        "Snacks + Confectionery (비상장)": "Mars 비상장 · Ferrero 비상장",
        "Snacks + Confectionery (유럽)": "Lindt LISN.SW · Nestlé NESN.SW",
    },
}
