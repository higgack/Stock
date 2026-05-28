"""Real Estate — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (2):
 1. Real Estate Services (brokerage + property mgmt + data)
 2. REITs (Industrial + Residential + Retail + Office + Healthcare + Specialty + Mortgage)

REIT sub-categories (Industrial / Residential / Office / Specialty etc.)
는 L4 라서 본 모듈에서는 binding_layer_taxonomy 의 L3 'REITs' 안에서
catalyst + regional 로 cover. L4 도메인 추가는 Phase B 에서 결정.
"""

from __future__ import annotations

THEME = {
    "domain": "Real Estate (부동산)",
    "layer": "L2_SECTOR",
    "aliases": [
        "real_estate",
        "realestate",
        "부동산",
        "reit",
        "reits",
        "리츠",
        "property",
        "데이터센터reit",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Real Estate Services (부동산 서비스 — brokerage + property mgmt + data)",
        "REITs (리츠 — Industrial · Residential · Retail · Office · Healthcare · Specialty · Mortgage)",
    ],
    "catalyst_types": [
        "Fed funds rate path + 美 10Y → REIT 배당 매력 변동",
        "美 office vacancy + CRE 만기 wall (2024-2026) + sub-lease 시장",
        "AI 데이터센터 REIT (Equinix/Digital Realty) 분기 leasing + power capacity",
        "Industrial REIT (Prologis) e-commerce + 美 reshoring + onshoring capex",
        "Residential REIT (Sun Belt) 美 단독주택 vs 다세대 rent growth + 모기지 금리",
        "Healthcare REIT (Welltower/Ventas) 시니어 housing 회복 + Medicare 정책",
        "Specialty REIT (Public Storage / VICI / Iron Mountain) 신규 카테고리 확장",
        "Mortgage REIT 분기 net interest margin + agency MBS spread",
    ],
    "regional_concentration": {
        "Real Estate Services": "US (CBRE CBRE · Jones Lang LaSalle JLL · Cushman & Wakefield CWK · Zillow Z · Redfin RDFN · Newmark NMRK · Marcus & Millichap MMI · Compass COMP · Anywhere Real Estate HOUS · eXp World EXPI), EU (Savills SVS.L · Knight Frank 비상장), JP (Mitsubishi Estate 8802.T · Mitsui Fudosan 8801.T · Sumitomo Realty 8830.T), KR (대신증권 003540.KS — 부동산 자회사 · 코람코자산신탁 비상장 · KB부동산신탁 비상장), CN (Country Garden Services 6098.HK · A-Living 3319.HK · China Resources Mixc 1209.HK)",
        "REITs": "Industrial (Prologis PLD · STAG Industrial STAG · Rexford REXR · First Industrial FR), Data Center (Equinix EQIX · Digital Realty DLR · Iron Mountain IRM), Residential (AvalonBay AVB · Equity Residential EQR · Invitation Homes INVH · Sun Communities SUI · UDR UDR · Camden Property CPT · Essex Property ESS · Mid-America MAA · American Homes 4 Rent AMH), Retail (Simon Property SPG · Realty Income O · Federal Realty FRT · Regency Centers REG · Kimco KIM · Brixmor BRX · Tanger SKT), Office (Boston Properties BXP · Vornado VNO · Kilroy KRC · Highwoods HIW · Cousins Properties CUZ), Healthcare (Welltower WELL · Ventas VTR · Healthpeak DOC · Omega Healthcare OHI · National Health Investors NHI), Specialty (Public Storage PSA — Self-storage · Extra Space EXR — Self-storage · CubeSmart CUBE · VICI Properties VICI — Casinos · GLPI · MGM Growth · Lamar Advertising LAMR · Outfront OUT · SBA Communications SBAC · American Tower AMT · Crown Castle CCI), Mortgage (Annaly NLY · Starwood STWD · Blackstone Mortgage BXMT · AGNC Investment AGNC · Two Harbors TWO · Hannon Armstrong HASI), JP REIT (Nippon Building Fund 8951.T · Japan Real Estate 8952.T · Japan Retail Fund 8953.T · GLP J-REIT 3281.T), SG REIT (CapitaLand Integrated CICT.SI · Mapletree Logistics M44U.SI · Frasers Logistics BUOU.SI · Keppel DC AJBU.SI), HK (Link REIT 0823.HK · Hongkong Land HKL.SI · Champion REIT 2778.HK)",
    },
}
