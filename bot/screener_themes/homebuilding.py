"""Homebuilding & Furnishings — L3 under Consumer Discretionary.

美 30Y 모기지 금리 + 신규 단독주택 inventory + DR Horton/Lennar 분기
수주 backlog + Williams-Sonoma/RH 분기 매출 가이드가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Homebuilding & Furnishings (주택 건설 및 가구)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "homebuilding",
        "주택건설_l3",
        "homebuilder",
        "home_furnishings",
        "가구",
        "furniture",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Single-Family Homebuilders (DR Horton · Lennar · PulteGroup · NVR · Toll)",
        "Multi-Family + Build-to-Rent (KB Home · Taylor Morrison · M/I Homes)",
        "Home Furnishings (Williams-Sonoma · RH · Tempur Sealy · Mohawk · La-Z-Boy)",
        "Building Products (Builders FirstSource · Mohawk · Owens Corning)",
        "Manufactured Homes + Modular (Cavco · Skyline Champion · Legacy)",
    ],
    "catalyst_types": [
        "美 30Y 모기지 금리 + Fed funds rate path → 주택 거래량 + 분기 수주",
        "신규 단독주택 inventory + 매물 회복 + 가격 변동 (Census · Zillow)",
        "DR Horton/Lennar/PulteGroup 분기 수주 backlog + 신규 community 발표",
        "Williams-Sonoma/RH/Tempur 분기 매출 + 美 인테리어 + 리모델링 사이클",
        "美 IIJA + 신규 인프라 capex → 건자재 수요 변화",
        "Fed 양적완화 종료 + 中国 부동산 위기 후 글로벌 furniture trade impact",
    ],
    "regional_concentration": {
        "Single-Family Homebuilders": "DR Horton DHI · Lennar LEN · PulteGroup PHM · NVR NVR · Toll Brothers TOL · KB Home KBH · Taylor Morrison TMHC · M/I Homes MHO · Meritage MTH · Tri Pointe TPH · M.D.C. Holdings MDC · Hovnanian HOV · Beazer BZH · Forestar Group FOR · LGI Homes LGIH · UICI 비상장 · Century Communities CCS · Dream Finders DFH · Landsea LSEA",
        "Multi-Family + Build-to-Rent": "AvalonBay AVB (REIT) · Camden CPT · Equity Residential EQR · Sun Communities SUI (manufactured) · UDR UDR · Tricon TCN · Mid-America MAA · Essex ESS",
        "Home Furnishings": "Williams-Sonoma WSM · RH RH · Tempur Sealy TPX · Mohawk MHK · La-Z-Boy LZB · Ethan Allen ETD · Leggett & Platt LEG · Sleep Number SNBR · Purple Innovation PRPL · Bed Bath & Beyond 비상장 (파산) · Kirkland's KIRK · The Container Store TCS · Restoration Hardware RH · Designer Brands DBI · Floor & Decor FND",
        "Building Products + Materials": "Builders FirstSource BLDR · Eagle Materials EXP · Vulcan Materials VMC · Martin Marietta MLM · Summit Materials SUM · Mohawk MHK · Owens Corning OC · Masco MAS · A. O. Smith AOS · Fortune Brands FBHS · USG 비상장 · Beacon Roofing BECN · GMS GMS",
        "Manufactured + Modular Homes": "Cavco Industries CVCO · Skyline Champion SKY · Legacy Housing LEGH · Patrick Industries PATK · Cavco CVCO · LCI Industries LCII",
        "Korea + Japan + EU": "현대건설 000720.KS · 대우건설 047040.KS · GS건설 006360.KS · DL이앤씨 375500.KS · 한샘 009240.KS · 현대리바트 079430.KS · 일룸 비상장 · 까사미아 비상장 · 이케아코리아 비상장, JP (Daiwa House 1925.T · Sumitomo Forestry 1911.T · Sekisui House 1928.T · Toto 5332.T · LIXIL 5938.T · Misawa Homes 1722.T · Asahi Kasei Homes 3407.T · Nissan Build 비상장), EU (Vinci DG.PA · Bouygues EN.PA · Hochtief HOT.DE · Persimmon PSN.L · Barratt BDEV.L · Bellway BWY.L · Taylor Wimpey TW.L)",
    },
}
