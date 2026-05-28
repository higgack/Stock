"""Consumer Discretionary — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (6):
 1. Automotive (Auto Mfg + EV + Auto Parts + Auto Retail)
 2. Apparel, Accessories & Luxury Goods
 3. Hospitality & Leisure (Hotels + Travel + Restaurants + Gambling)
 4. Retail (E-commerce + Broadline + Home Improvement + Specialty)
 5. Homebuilding & Furnishings
 6. Education Services

Wave 1 trend `ev.py` (EV cycle niche) 와는 부분 overlap (자동차) — 본
모듈은 임의소비재 sector 전체 lens.
"""

from __future__ import annotations

THEME = {
    "domain": "Consumer Discretionary (임의 소비재)",
    "layer": "L2_SECTOR",
    "aliases": [
        "consumer_cyclical",
        "consumer_discretionary",
        "임의소비재",
        "discretionary",
        "auto",
        "자동차",
        "luxury",
        "럭셔리",
        "hotel",
        "호텔",
        "travel",
        "여행",
        "restaurant",
        "레스토랑",
        "retail",
        "소매",
        "homebuilding",
        "주택건설",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Automotive (자동차 관련 — Auto Mfg + EV + Auto Parts + Auto Retail)",
        "Apparel, Accessories & Luxury Goods (의류, 액세서리 및 명품)",
        "Hospitality & Leisure (호텔 및 레저 — Hotels + Travel + Restaurants + Gambling)",
        "Retail (소매 — E-commerce + Broadline + Home Improvement + Specialty)",
        "Homebuilding & Furnishings (주택 건설 및 가구)",
        "Education Services (교육 서비스)",
    ],
    "catalyst_types": [
        "美 IRA EV 크레딧 + Trump 행정명령 EV 정책 변경 + 美 100% 中 EV 관세",
        "LVMH/Hermès/Kering 분기 organic growth + 中国 럭셔리 cycle 회복",
        "Marriott/Hilton/IHG 분기 RevPAR + 美 국제선 회복 + Booking/Airbnb 예약 데이터",
        "Amazon 분기 매출 + AWS + 美 소비자 신뢰지수 + Black Friday/Cyber Monday",
        "美 30Y 모기지 금리 + 주택 착공 + DR Horton/Lennar 수주 backlog",
        "Nike/Lululemon 분기 매출 + 中国 시장 회복 + 베트남 capacity",
        "美 학자금 대출 상환 재개 + Chegg/Coursera B2B 매출",
        "GLP-1 induced 식음료 spend 변화 (Mounjaro/Wegovy → 음료/스낵 감소)",
    ],
    "regional_concentration": {
        "Automotive": "Auto Mfg US (Ford F · GM GM · Stellantis STLA), Toyota TM · Ferrari RACE, EV (Tesla TSLA · Rivian RIVN · Lucid LCID), Auto Parts (Magna MGA · Aptiv APTV · BorgWarner BWA · Goodyear GT · Dorman DORM), Auto Retail (AutoZone AZO · O'Reilly ORLY · Genuine Parts GPC · CarMax KMX · Lithia LAD · AutoNation AN), EU (Volkswagen VOW3.DE · BMW BMW.DE · Mercedes MBG.DE · Stellantis STLA · Volvo VOLV-B.ST · Porsche P911.DE · Ferrari RACE), JP (Toyota 7203.T · Honda 7267.T · Nissan 7201.T · Suzuki 7269.T · Subaru 7270.T · Mazda 7261.T · Mitsubishi Motors 7211.T · Yamaha 7272.T · DENSO 6902.T · Aisin 7259.T), KR (현대차 005380.KS · 기아 000270.KS · 현대모비스 012330.KS · 한온시스템 018880.KS · 만도 204320.KS), CN (BYD 002594.SZ + 1211.HK · NIO NIO · XPeng XPEV · Li Auto LI · Geely 0175.HK · SAIC 600104.SS)",
        "Apparel, Accessories & Luxury Goods": "US (Nike NKE · Lululemon LULU · Deckers DECK · Skechers SKX · Crocs CROX · Tapestry TPR · Capri CPRI · PVH PVH · Ralph Lauren RL · VF Corp VFC · Under Armour UA · Gap GAP), EU 럭셔리 (LVMH MC.PA · Hermès RMS.PA · Kering KER.PA · Richemont CFR.SW · Burberry BRBY.L · Moncler MONC.MI · Prada 1913.HK · Christian Dior CDI.PA · Brunello Cucinelli BC.MI · Salvatore Ferragamo SFER.MI · Ferragamo SFER.MI · Pandora PNDORA.CO), JP (Fast Retailing 9983.T — Uniqlo · Onward 8016.T · World 3612.T · Adastria 2685.T), KR (한섬 020000.KS · F&F 383220.KS · LF 093050.KS · 한세실업 105630.KS · 영원무역 111770.KS)",
        "Hospitality & Leisure": "Hotels US (Marriott MAR · Hilton HLT · Hyatt H · IHG IHG · Wynn WYNN · Las Vegas Sands LVS · MGM MGM), Cruise (Carnival CCL · Royal Caribbean RCL · Norwegian NCLH · Lindblad LIND), Travel (Booking BKNG · Airbnb ABNB · Expedia EXPE · TripAdvisor TRIP · Trivago TRVG), Restaurants (McDonald's MCD · Starbucks SBUX · Chipotle CMG · Yum YUM · Darden DRI · Restaurant Brands QSR · CAVA CAVA · Sweetgreen SG · Wendy's WEN · Texas Roadhouse TXRH · Domino's DPZ · Brinker EAT), Gambling/Leisure (Vail Resorts MTN · Six Flags SIX · Cedar Fair FUN · Penn Entertainment PENN · DraftKings DKNG · Flutter FLUT), EU (Accor AC.PA · IHG IHG · Whitbread WTB.L · Compass CPG.L), KR (호텔신라 008770.KS · 한화호텔앤드리조트 비상장 · 강원랜드 035250.KS · 파라다이스 034230.KQ · GKL 114090.KS), JP (Oriental Land 4661.T — Disney Tokyo · Resorttrust 4681.T · Round One 4680.T)",
        "Retail": "E-commerce (Amazon AMZN · Shopify SHOP · MercadoLibre MELI · eBay EBAY · Etsy ETSY · Wayfair W · Chewy CHWY · Coupang CPNG · Pinduoduo PDD), Broadline (Target TGT · TJX TJX · Ross ROST · Macy's M · Nordstrom JWN · Burlington BURL · Dollar Tree DLTR · Big Lots BIG), Home Improvement (Home Depot HD · Lowe's LOW · Floor & Decor FND), Specialty (Best Buy BBY · Tractor Supply TSCO · Ulta Beauty ULTA · Dick's DKS · Five Below FIVE · Bath & Body Works BBWI · Sephora 비상장 LVMH), KR (쿠팡 CPNG · 이마트 139480.KS · 신세계 004170.KS · 롯데쇼핑 023530.KS · 현대백화점 069960.KS · GS리테일 007070.KS · BGF리테일 282330.KS · 무신사 비상장), JP (Aeon 8267.T · Seven & i 3382.T · Pan Pacific 7532.T · Don Quijote · Yodobashi 비상장 · Bic Camera 3048.T), CN (Alibaba 9988.HK · JD JD · Pinduoduo PDD · Meituan 3690.HK · Vipshop VIPS)",
        "Homebuilding & Furnishings": "Homebuilding (DR Horton DHI · Lennar LEN · PulteGroup PHM · NVR NVR · Toll Brothers TOL · KB Home KBH · Taylor Morrison TMHC · M/I Homes MHO · Meritage MTH), Furnishings (Williams-Sonoma WSM · RH RH · Tempur Sealy TPX · Mohawk MHK · La-Z-Boy LZB · Ethan Allen ETD · Leggett & Platt LEG), KR (현대건설 000720.KS · 대우건설 047040.KS · GS건설 006360.KS · DL이앤씨 375500.KS · 한샘 009240.KS · 현대리바트 079430.KS), JP (Daiwa House 1925.T · Sumitomo Forestry 1911.T · Sekisui House 1928.T · Toto 5332.T · LIXIL 5938.T)",
        "Education Services": "US (Chegg CHGG · Coursera COUR · 2U TWOU · Stride LRN · Grand Canyon LOPE · Adtalem ATGE · Strategic Education STRA), CN (TAL TAL · New Oriental EDU · Gaotu GOTU), KR (메가스터디교육 215200.KQ · 비상교육 100220.KQ · 청담러닝 096240.KQ · 아이스크림에듀 376300.KS), JP (Benesse 9783.T · 学究社 9769.T)",
    },
}
