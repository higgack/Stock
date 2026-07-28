"""Real Estate Services — L3 under Real Estate.

CBRE + JLL + Cushman + Zillow + Redfin. CRE leasing 회복 + 분기 capital
markets fee + 美 모기지 금리 + Zillow lead 매출이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Real Estate Services (부동산 서비스)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "real_estate_services",
        "부동산서비스",
        "real_estate_brokerage",
        "부동산중개",
        "zillow",
        "cbre",
        "jll",
    ],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Commercial Real Estate Brokerage (CBRE · JLL · Cushman · Newmark)",
        "Residential Brokerage + Mortgage (Compass · Anywhere · eXp · Zillow)",
        "Property Listings + Data (CoStar · Zillow · Redfin · Realtor)",
        "Property Management + Tech (FirstService · Walker Dunlop)",
    ],
    "catalyst_types": [
        "美 분기 CRE 매출 (CBRE · JLL · Cushman) + leasing volume 회복",
        "美 30Y 모기지 금리 + 주택 거래량 + Zillow / Redfin 분기 매출",
        "美 office vacancy + CRE 만기 wall + sub-lease 시장",
        "데이터센터 + 산업 leasing + 신규 데이터센터 capacity (Equinix · DLR)",
        "CoStar Apartments.com + LoopNet + Homes.com 분기 매출 성장",
        "Compass / eXp 분기 agent count + 분기 GAV (Gross Agent Value)",
    ],
    "regional_concentration": {
        "Commercial RE Brokerage + IB": "CBRE Group CBRE · Jones Lang LaSalle JLL · Cushman & Wakefield CWK · Newmark NMRK · Walker & Dunlop WD · Marcus & Millichap MMI · Colliers International CIGI · Savills SVS.L (UK) · Knight Frank 비상장 · Avison Young 비상장",
        "Residential Brokerage": "Anywhere Real Estate HOUS (Coldwell Banker · Sotheby's · Century 21) · Zillow Z · Redfin RDFN · Compass COMP · eXp World EXPI · Douglas Elliman DOUG · Re/Max RMAX · Realogy 비상장 (Anywhere 인수 후 이름 변경)",
        "Property Listings + Data": "CoStar Group CSGP · Zillow Z · Redfin RDFN · Realtor.com (News Corp 자회사 NWS) · Trulia (Zillow 인수 후) · Costar CSGP · Homes.com (CoStar) · Apartments.com (CoStar) · LoopNet (CoStar) · Land.com (CoStar) · Move (News Corp) · BRT Apartments BRT",
        "Property Management + Specialty": "FirstService FSV · Walker Dunlop WD · Ladder Capital LADR · BRT Apartments BRT · Sun Communities SUI (manufactured + RV) · Equity Lifestyle ELS",
        "Korea + Japan + EU + China": "JP (Mitsubishi Estate 8802.T · Mitsui Fudosan 8801.T · Sumitomo Realty 8830.T · Tokyu Fudosan 8815.T · Heiwa Real Estate 8803.T · Tokyo Tatemono 8804.T · Daikyo 8840.T · NTT Urban 비상장), KR (코람코자산신탁 비상장 · KB부동산신탁 비상장 · 한국토지신탁 034830.KS · 한국자산신탁 123890.KS · 메리츠금융지주 138040.KS), CN (Country Garden Services 6098.HK · A-Living 3319.HK · China Resources Mixc 1209.HK · Greentown Service 2869.HK · COLI 0688.HK · CR Land 1109.HK · Vanke 000002.SZ + 2202.HK · Country Garden 2007.HK)",
    },
}
