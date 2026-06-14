"""E-commerce + Direct Marketing — L4 under Retail (소매).

자동 생성(부모 L3 retail 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "E-commerce + Direct Marketing (Retail)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["commerce_direct", "commerce"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "E-commerce + Direct Marketing — 미국 (Amazon AMZN · Shopify SHOP · MercadoLibre MELI · eBay EBAY · Etsy ETSY · Wayfair W · Chewy CHWY · Vroom VRM)",
        "E-commerce + Direct Marketing — 홍콩 (Coupang CPNG (KR), CN (Alibaba 9988.HK · Meituan 3690.HK · Kuaishou 1024.HK + 1688.HK)",
        "E-commerce + Direct Marketing — 일본 (Miniso MNSO), JP (Rakuten 4755.T · Mercari 4385.T · ZOZO 3092.T)",
        "E-commerce + Direct Marketing — 비상장 (Mobile Vape 비상장))",
    ],
    "catalyst_types": [
        "Amazon 분기 매출 + AWS 분리 + 광고 매출 + Prime 회원 증가",
        "미국 소매 same-store sales (Census) + 분기 Black Friday/Cyber Monday",
        "美 인플레이션 영향 + minimum wage 인상 → 소매 영업 마진",
        "Home Depot/Lowe's 분기 same-store sales + 美 주택 거래량 + DIY 동향",
        "Costco 분기 멤버십 갱신율 + e-commerce 침투율 + Kirkland 자체 브랜드",
        "Shein/Temu 美 침투율 + de minimis 관세 정책 변경 → 美 e-commerce 영향",
    ],
    "regional_concentration": {
        "E-commerce + Direct Marketing (미국)": "Amazon AMZN · Shopify SHOP · MercadoLibre MELI · eBay EBAY · Etsy ETSY · Wayfair W · Chewy CHWY · Vroom VRM · JD JD · Pinduoduo PDD · Vipshop VIPS · Tongcheng Travel",
        "E-commerce + Direct Marketing (홍콩)": "Coupang CPNG (KR), CN (Alibaba 9988.HK · Meituan 3690.HK · Kuaishou 1024.HK + 1688.HK",
        "E-commerce + Direct Marketing (일본)": "Miniso MNSO), JP (Rakuten 4755.T · Mercari 4385.T · ZOZO 3092.T",
        "E-commerce + Direct Marketing (비상장)": "Mobile Vape 비상장)",
    },
}
