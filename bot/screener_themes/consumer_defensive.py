"""Consumer Staples — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (5):
 1. Beverages
 2. Food & Staples Retailing
 3. Food Products
 4. Household & Personal Products
 5. Tobacco
"""

from __future__ import annotations

THEME = {
    "domain": "Consumer Staples (필수 소비재)",
    "layer": "L2_SECTOR",
    "aliases": [
        "consumer_defensive",
        "consumer_staples",
        "staples",
        "필수소비재",
        "defensive",
        "beverage",
        "음료",
        "food",
        "식품",
        "tobacco",
        "담배",
        "alcohol",
        "주류",
        "household",
        "생활용품",
        "personal_care",
        "개인용품",
        "cosmetic",
        "화장품",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Beverages (음료 — Soft Drinks + Beer + Spirits + Wine)",
        "Food & Staples Retailing (식료품 및 필수품 소매)",
        "Food Products (식품 — Packaged + Meat + Dairy + Snacks)",
        "Household & Personal Products (가정 및 개인용품 — Cleaning + Cosmetics + Personal Care)",
        "Tobacco (담배 — Cigarettes + Smokeless + Vape)",
    ],
    "catalyst_types": [
        "GLP-1 induced 식음료 spend 변화 (Mounjaro/Wegovy → 음료/스낵 감소) 정량화",
        "Walmart/Costco 분기 same-store sales + 美 SNAP 보조금 변화",
        "FAO 식량 가격 지수 + 미국 곡물 farm bill 통과",
        "美 FTC 인수합병 차단 (Kroger/Albertsons block 후 후속) + EU CMA antitrust",
        "Tobacco 美 FDA menthol ban 시행 + 美 PMTA (Vape) 승인 일정",
        "Estée Lauder/L'Oréal 분기 중국 매출 회복 + 일본 inbound 관광 수익",
        "美 CDC bird flu (H5N1) outbreak + 사료 가격 + 우유/계란 가격",
        "EU 기후 변화 + 농산물 작황 (브라질 콩 / 인도 쌀) 수출 통제 + 印 양파 export ban",
    ],
    "regional_concentration": {
        "Beverages": "US (Coca-Cola KO · PepsiCo PEP · Monster Beverage MNST · Keurig Dr Pepper KDP · Constellation Brands STZ · Brown-Forman BF.B · Molson Coors TAP · Boston Beer SAM · Celsius CELH), EU (Diageo DGE.L · Anheuser-Busch InBev BUD · Heineken HEIA.AS · Pernod Ricard RI.PA · Carlsberg CARL-B.CO · Davide Campari CPR.MI · Rémy Cointreau RCO.PA), JP (Kirin 2503.T · Asahi 2502.T · Suntory 비상장 · Sapporo 2501.T · Coca-Cola Japan 2579.T), KR (하이트진로 000080.KS · 롯데칠성음료 005300.KS · 무학 033920.KS), CN (Tsingtao 0168.HK · 贵州茅台 600519.SS · 五粮液 000858.SZ · 泸州老窖 000568.SZ · 山西汾酒 600809.SS · 洋河 002304.SZ · Wuliangye 000858.SZ · ZX Liquor)",
        "Food & Staples Retailing": "US (Walmart WMT · Costco COST · Kroger KR · Dollar General DG · Dollar Tree DLTR · Casey's CASY · Albertsons ACI · Sprouts SFM), EU (Tesco TSCO.L · Sainsbury SBRY.L · Carrefour CA.PA · Ahold Delhaize AD.AS · Walmart México WMMVF · Jerónimo Martins JMT.LS), JP (Seven & i 3382.T · Aeon 8267.T · Lawson 2651.T · FamilyMart 비상장), KR (이마트 139480.KS · 롯데마트 비상장 · 홈플러스 비상장 · GS25-GS리테일 007070.KS · CU-BGF리테일 282330.KS · 농협하나로 비상장 · 이마트24 비상장), CN (Yonghui 601933.SS · Sun Art 6808.HK · Hema-Alibaba 자회사)",
        "Food Products": "US (Kraft Heinz KHC · Mondelez MDLZ · General Mills GIS · Hershey HSY · ADM ADM · Tyson Foods TSN · Hormel HRL · McCormick MKC · Lamb Weston LW · Conagra CAG · Campbell Soup CPB · Kellogg's K · J.M. Smucker SJM · Bunge BG · Pilgrim's Pride PPC), EU (Unilever ULVR.L · Nestlé NESN.SW · Danone BN.PA · Mowi MOWI.OL · Suedzucker SZU.DE), JP (Ajinomoto 2802.T · Kikkoman 2801.T · Nichirei 2871.T · Yakult 2267.T · Meiji 2269.T · Calbee 2229.T · Nissin Foods 2897.T · Maruha Nichiro 1333.T), KR (CJ제일제당 097950.KS · 농심 004370.KS · 오뚜기 007310.KS · 동원F&B 049770.KS · 풀무원 017810.KS · 빙그레 005180.KS · 롯데웰푸드 280360.KS), CN (Mengniu 2319.HK · Yili 600887.SS · WH Group 0288.HK · Tingyi 0322.HK · Want Want 0151.HK)",
        "Household & Personal Products": "US (Procter & Gamble PG · Colgate-Palmolive CL · Kimberly-Clark KMB · Clorox CLX · Estée Lauder EL · Coty COTY · Church & Dwight CHD · Edgewell EPC · Inter Parfums IPAR · Newell Brands NWL · elf Beauty ELF), EU (L'Oréal OR.PA · Unilever ULVR.L · Reckitt RKT.L · Beiersdorf BEI.DE · Henkel HEN3.DE), JP (Shiseido 4911.T · Kao 4452.T · Lion 4912.T · Pola Orbis 4927.T · Pigeon 7956.T), KR (LG생활건강 051900.KS · 아모레퍼시픽 090430.KS · 코스맥스 192820.KS · 한국콜마 161890.KS · 클리오 237880.KQ · 닥터자르트-해브앤비 비상장)",
        "Tobacco": "US (Philip Morris PM · Altria MO · Reynolds-합병 후), EU (British American Tobacco BTI · Imperial Brands IMB.L · Japan Tobacco 2914.T), KR (KT&G 033780.KS), CN (China Tobacco 비상장 · 上海烟草 비상장), Vape (NJOY-Altria · Juul 비상장), 차세대 IQOS Heat-Not-Burn (Philip Morris PM)",
    },
}
