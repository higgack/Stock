"""Food Products — L3 under Consumer Staples.

Packaged Foods + Meat + Dairy + Snacks. 美 CDC bird flu (H5N1) +
GLP-1 induced 스낵 매출 감소 + FAO 식량 가격 지수 + 브라질 콩 수출이
주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Food Products (식품)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "food_products",
        "식품_l3",
        "packaged_food",
        "가공식품",
        "meat",
        "육류",
        "dairy",
        "유제품",
        "snack",
        "스낵",
        "agri",
        "농업",
        "fertilizer",
        "비료",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Packaged Foods (Kraft Heinz · Mondelez · General Mills · Kellogg · Hershey)",
        "Meat + Protein (Tyson · Hormel · Pilgrim's Pride · JBS)",
        "Dairy + Yogurt (Dean Foods · Lactalis · 농심·롯데)",
        "Agricultural Commodities + Trading (ADM · Bunge · Cargill · 일본 거상 5대)",
        "Snacks + Confectionery (Mondelez · Hershey · Tootsie Roll · Lindt)",
        "Frozen + Specialty (Lamb Weston · Conagra · McCormick · J.M. Smucker)",
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
        "Packaged Foods (US)": "Kraft Heinz KHC · Mondelez MDLZ · General Mills GIS · Kellogg K (Kellanova KLG + WK Kellogg KLG) · Hershey HSY · J.M. Smucker SJM · Conagra CAG · Campbell Soup CPB · McCormick MKC · Lamb Weston LW · TreeHouse Foods THS · Hain Celestial HAIN · B&G Foods BGS · Post Holdings POST",
        "Meat + Protein": "Tyson Foods TSN · Hormel Foods HRL · Pilgrim's Pride PPC · JBS JBSAY (BR) · Bunge BG · Sanderson Farms 비상장 · Beyond Meat BYND · Vital Farms VITL · WH Group 0288.HK · Smithfield Foods 비상장 (WH Group 자회사)",
        "Dairy + Yogurt": "Dean Foods 비상장 (파산) · Lactalis 비상장 (FR) · Danone BN.PA · Yili 600887.SS · Mengniu 2319.HK · Bright Dairy 600597.SS · Saputo SAP.TO · Glanbia GLB.IR · Fonterra 비상장 · Lifeway Foods LWAY · Vital Farms VITL",
        "Agricultural Commodities + Trading": "Archer-Daniels-Midland ADM · Bunge BG · Cargill 비상장 · Wilmar International F34.SI · Olam International OHL.SI, JP (Mitsubishi 8058.T · Mitsui 8031.T · Itochu 8001.T · Marubeni 8002.T · Sumitomo 8053.T), CN (China Mengniu 2319.HK · Yili 600887.SS · WH Group 0288.HK · Tingyi 0322.HK · Want Want 0151.HK)",
        "Snacks + Confectionery": "Mondelez MDLZ (Oreo · Chips Ahoy · Cadbury) · Hershey HSY · Tootsie Roll TR · Hostess Brands TWNK · Utz Brands UTZ · J&J Snack Foods JJSF · Mars 비상장 · Ferrero 비상장 · Lindt LISN.SW · Nestlé NESN.SW",
        "Frozen + Specialty": "Lamb Weston LW · Conagra CAG · Pinnacle Foods 비상장 · McCormick MKC · J.M. Smucker SJM · Treehouse Foods THS · Hain Celestial HAIN · United Natural Foods UNFI · Sysco SYY · US Foods USFD · Performance Food Group PFGC",
        "Korea + Japan + EU": "CJ제일제당 097950.KS · 농심 004370.KS · 오뚜기 007310.KS · 동원F&B 049770.KS · 풀무원 017810.KS · 빙그레 005180.KS · 롯데웰푸드 280360.KS · 매일유업 267980.KS · 남양유업 003920.KS · 한국야쿠르트 비상장, JP (Ajinomoto 2802.T · Kikkoman 2801.T · Nichirei 2871.T · Yakult 2267.T · Meiji 2269.T · Calbee 2229.T · Nissin Foods 2897.T · Maruha Nichiro 1333.T · Kewpie 2809.T · Itoham Yonekyu 2296.T · Nippon Suisan 1332.T), EU (Unilever ULVR.L · Nestlé NESN.SW · Danone BN.PA · Mowi MOWI.OL · Suedzucker SZU.DE · Associated British Foods ABF.L · Diageo DGE.L)",
    },
}
