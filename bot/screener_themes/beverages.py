"""Beverages — L3 under Consumer Staples.

Soft Drinks (KO/PEP) + Beer (BUD/Heineken) + Spirits (Diageo/Pernod) +
Energy (Monster · CELH) + 中国 백주 (Moutai · 五粮液). GLP-1 induced
소비 변화 + 알코올 hospitality 회복이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Beverages (음료)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "beverages",
        "beverage_l3",
        "음료_l3",
        "soft_drinks",
        "탄산음료",
        "beer",
        "맥주",
        "spirits",
        "주류",
        "wine",
        "와인",
        "energy_drinks",
        "에너지음료",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Non-Alcoholic / Soft Drinks (Coca-Cola · PepsiCo · Keurig Dr Pepper · Monster)",
        "Energy Drinks + Functional (Monster · Celsius · Red Bull 비상장)",
        "Beer (Anheuser-Busch InBev · Heineken · Molson Coors · Carlsberg)",
        "Spirits + Wine (Diageo · Pernod Ricard · Brown-Forman · Constellation)",
        "中国 백주 (Kweichow Moutai · 五粮液 · 泸州老窖 · 山西汾酒)",
        "Japan + Korea 양조 (Kirin · Asahi · 하이트진로 · 롯데칠성)",
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
        "Non-Alcoholic / Soft Drinks": "Coca-Cola KO · PepsiCo PEP · Keurig Dr Pepper KDP · Monster Beverage MNST · Celsius CELH · National Beverage FIZZ · Cott COT · A.G. BARR BAG.L (UK)",
        "Energy + Functional Drinks": "Monster Beverage MNST · Celsius CELH · Red Bull 비상장 · Vita Coco COCO · Reed's Inc REED · GURU Organic GURU",
        "Beer Global": "Anheuser-Busch InBev BUD · Heineken HEIA.AS · Molson Coors TAP · Carlsberg CARL-B.CO · Constellation Brands STZ · Boston Beer SAM · Diageo DGE.L · Asahi 2502.T · Kirin 2503.T · Sapporo 2501.T · Tsingtao 0168.HK · China Resources Beer 0291.HK · Budweiser APAC 1876.HK",
        "Spirits + Wine": "Diageo DGE.L · Pernod Ricard RI.PA · Brown-Forman BF.B · Constellation Brands STZ · Davide Campari CPR.MI · Rémy Cointreau RCO.PA · LVMH MC.PA (Moët Hennessy 자회사) · Pernod Ricard RI.PA · Treasury Wine TWE.AX · Vintage Wine Estates VWE",
        "China 백주 + Asia": "Kweichow Moutai 600519.SS · 五粮液 000858.SZ · 泸州老窖 000568.SZ · 山西汾酒 600809.SS · 洋河 002304.SZ · 牛栏山 002247.SZ · 古井贡 000596.SZ · Wuliangye 000858.SZ · 舍得酒业 600702.SS",
        "Korea + Japan": "하이트진로 000080.KS · 롯데칠성음료 005300.KS · 무학 033920.KS · 보해양조 000890.KS · JP (Kirin 2503.T · Asahi 2502.T · Sapporo 2501.T · Suntory 비상장 · Coca-Cola Bottlers Japan 2579.T · Kagome 2811.T · Mercian-Kirin · Ito En 2593.T)",
        "Tea + Coffee Adjacent": "Starbucks SBUX (food adjacent — 분기 분류는 외식이지만 음료 cycle 영향 큼) · Keurig Dr Pepper KDP (K-Cup 캡슐) · Tata Consumer Products TATACONSUM.NS · Unilever ULVR.L (Lipton + Pukka)",
    },
}
