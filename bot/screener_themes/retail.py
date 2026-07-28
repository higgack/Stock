"""Retail — L3 under Consumer Discretionary.

E-commerce + Broadline + Home Improvement + Specialty. Amazon AWS와는
별개 lens. 美 분기 same-store sales + Black Friday + 인플레이션 영향
+ 中国 outbound 관광 + Costco/Walmart e-commerce penetration이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Retail (소매)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "retail",
        "retail_l3",
        "소매_l3",
        "ecommerce",
        "전자상거래",
        "broadline_retail",
        "home_improvement",
        "주택개량",
        "specialty_retail",
        "전문소매",
    ],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "E-commerce + Direct Marketing (Amazon · Shopify · MercadoLibre · eBay)",
        "Broadline Retail + Discounters (Target · TJX · Ross · Dollar Tree · Macy's)",
        "Home Improvement (Home Depot · Lowe's · Floor & Decor)",
        "Specialty Retail (Best Buy · Tractor Supply · Ulta · Dick's · Five Below)",
        "Beauty + Personal Care (Ulta · Sephora-LVMH · Sally Beauty)",
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
        "E-commerce Global": "Amazon AMZN · Shopify SHOP · MercadoLibre MELI · eBay EBAY · Etsy ETSY · Wayfair W · Chewy CHWY · Vroom VRM · Coupang CPNG (KR), CN (Alibaba 9988.HK · JD JD · Pinduoduo PDD · Meituan 3690.HK · Vipshop VIPS · Tongcheng Travel · Kuaishou 1024.HK + 1688.HK · DingDong DDL · Miniso MNSO), JP (Rakuten 4755.T · Mercari 4385.T · ZOZO 3092.T · Mobile Vape 비상장)",
        "Broadline + Discount": "Target TGT · TJX Companies TJX · Ross Stores ROST · Macy's M · Nordstrom JWN · Kohl's KSS · Burlington BURL · Big Lots BIG · Dollar Tree DLTR · Dollar General DG · Five Below FIVE · Ollie's Bargain OLLI · Dillard's DDS · Stein Mart 비상장 · Hudson HUD · Burlington Coat 비상장",
        "Home Improvement + Hardware": "Home Depot HD · Lowe's LOW · Floor & Decor FND · Sherwin-Williams SHW (paint) · Tractor Supply TSCO · Builders FirstSource BLDR · Ace Hardware 비상장 · True Value 비상장 · Restoration Hardware RH",
        "Specialty Retail": "Best Buy BBY · Tractor Supply TSCO · Ulta Beauty ULTA · Dick's Sporting Goods DKS · Five Below FIVE · Bath & Body Works BBWI · Yankee Candle 비상장 · Williams-Sonoma WSM · Sportsman's Warehouse SPWH · Designer Brands DBI · Foot Locker FL · Boot Barn BOOT · Carter's CRI · Children's Place PLCE · Tilly's TLYS",
        "Beauty + Cosmetic Retail": "Ulta Beauty ULTA · Sephora 비상장 (LVMH) · Sally Beauty SBH · Coty COTY · Estée Lauder EL · L'Oréal OR.PA · Inter Parfums IPAR · elf Beauty ELF · Olaplex OLPX · Honest Company HNST",
        "Korea + Japan Retail": "쿠팡 CPNG · 이마트 139480.KS · 신세계 004170.KS · 롯데쇼핑 023530.KS · 현대백화점 069960.KS · GS리테일 007070.KS · BGF리테일 282330.KS · 무신사 비상장 · 마켓컬리 비상장, JP (Seven & i 3382.T · Aeon 8267.T · Don Quijote 7532.T · Bic Camera 3048.T · Yamada Holdings 9831.T · Yodobashi 비상장 · UNIQLO 9983.T)",
    },
}
