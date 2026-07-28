"""Food & Staples Retailing — L3 under Consumer Staples.

Walmart + Costco + Kroger + Dollar Stores + 편의점. 美 SNAP 보조금 +
인플레이션 + 분기 same-store sales + e-commerce penetration 이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Food & Staples Retailing (식료품 및 필수품 소매)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "food_retailing",
        "food_staples_retail",
        "식품유통",
        "grocery",
        "슈퍼마켓",
        "convenience_store",
        "편의점",
        "warehouse_club",
        "코스트코",
    ],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Mass Merchant + Warehouse Club (Walmart · Costco · Sam's Club)",
        "Traditional Grocery (Kroger · Albertsons · Sprouts · Casey's)",
        "Dollar Stores (Dollar General · Dollar Tree · Five Below)",
        "Convenience Stores (Casey's · Murphy USA · 일본 Seven & i · KR GS25/CU)",
        "EU + Asia Grocery (Tesco · Carrefour · Ahold · Aeon)",
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
        "US Mass Merchant + Warehouse Club": "Walmart WMT · Costco COST · BJ's Wholesale BJ · Sam's Club (Walmart 자회사) · PriceSmart PSMT",
        "US Traditional Grocery": "Kroger KR · Albertsons ACI · Sprouts SFM · Casey's CASY · Weis Markets WMK · Ingles IMKTA · Village Super Market VLGEA · Wegmans 비상장 · Whole Foods (Amazon 자회사) · Trader Joe's 비상장 · Aldi 비상장 (DE)",
        "Dollar Stores": "Dollar General DG · Dollar Tree DLTR · Five Below FIVE · Big Lots BIG · Ollie's Bargain OLLI",
        "Convenience Stores (US + Global)": "Casey's General Stores CASY · Murphy USA MUSA · Couche-Tard ATD.TO (Circle K), JP (Seven & i 3382.T — 7-Eleven · Lawson 2651.T · FamilyMart 비상장 - Itochu 자회사 · Aeon 8267.T · Costco Japan 비상장), KR (이마트24 비상장 · GS25 - GS리테일 007070.KS · CU - BGF리테일 282330.KS · 세븐일레븐 비상장 - 롯데 자회사 · 미니스톱 비상장 - Aeon 자회사)",
        "EU Grocery": "Tesco TSCO.L · Sainsbury SBRY.L · Carrefour CA.PA · Ahold Delhaize AD.AS · Walmart México WMMVF · Jerónimo Martins JMT.LS · Casino CO.PA · Metro AG B4B.DE · X5 Retail FIVE.IL · Magnit MGNT.MM · Lenta LNTA.IL · Auchan 비상장 · Lidl 비상장 (Schwarz)",
        "Asia + EM Grocery": "JP (Aeon 8267.T · Pan Pacific 7532.T - Don Quijote · Life Corp 8194.T · MaxValu 8201.T), KR (이마트 139480.KS · 신세계 004170.KS · 롯데마트 비상장 · 홈플러스 비상장), CN (Yonghui 601933.SS · Sun Art 6808.HK · Hema-Alibaba 자회사 · 7-Eleven China · Walmart Sam's China), Latin (Walmart de México WMMVF · Cencosud CENCOSUD.SN · Grupo Éxito GRUEX), IN (Reliance Retail-Reliance RELIANCE.NS · DMart DMART.NS · Avenue Supermarts AVENUE.NS)",
    },
}
