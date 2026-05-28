"""REITs — L3 under Real Estate.

Industrial + Residential + Office + Retail + Healthcare + Data Center +
Specialty + Mortgage. Fed funds rate → 배당 매력 변화 + AI 데이터센터
신규 leasing + 美 office vacancy + 美 모기지 금리가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "REITs (리츠)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "reits",
        "리츠_l3",
        "industrial_reit",
        "industrial_리츠",
        "data_center_reit",
        "데이터센터리츠",
        "residential_reit",
        "주거리츠",
        "retail_reit",
        "소매리츠",
        "healthcare_reit",
        "헬스케어리츠",
        "mortgage_reit",
        "모기지리츠",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Industrial REITs (Prologis · Rexford · First Industrial · Sun Comm)",
        "Data Center REITs (Equinix · Digital Realty · Iron Mountain)",
        "Residential REITs (AvalonBay · Equity Residential · Invitation Homes · MAA · Sun Comm)",
        "Retail REITs (Simon · Realty Income · Regency · Kimco · Federal Realty)",
        "Office REITs (Boston Properties · Vornado · Kilroy · Cousins · Highwoods)",
        "Healthcare REITs (Welltower · Ventas · Healthpeak · Omega · National Health)",
        "Specialty REITs (Public Storage · Extra Space · VICI · American Tower · Crown Castle)",
        "Mortgage REITs (Annaly · Starwood · Blackstone Mortgage · AGNC · Hannon)",
    ],
    "catalyst_types": [
        "Fed funds rate path + 美 10Y → REIT 배당 매력 변동",
        "AI 데이터센터 REIT (Equinix/Digital Realty) 분기 leasing + power capacity",
        "Industrial REIT (Prologis) e-commerce + 美 reshoring + onshoring capex",
        "美 office vacancy + CRE 만기 wall + sub-lease 시장 회복",
        "Residential REIT (Sun Belt) 美 단독주택 vs 다세대 rent growth + 모기지 금리",
        "Healthcare REIT (Welltower/Ventas) 시니어 housing 회복 + Medicare 정책",
        "Specialty REIT (Public Storage/VICI/Iron Mountain) 신규 카테고리 확장",
        "Mortgage REIT 분기 net interest margin + agency MBS spread",
    ],
    "regional_concentration": {
        "Industrial REITs": "Prologis PLD · STAG Industrial STAG · Rexford REXR · First Industrial FR · EastGroup Properties EGP · Terreno Realty TRNO · Plymouth Industrial PLYM · Lineage Logistics LINE (cold storage IPO)",
        "Data Center REITs": "Equinix EQIX · Digital Realty DLR · Iron Mountain IRM (storage + data center)",
        "Residential REITs": "AvalonBay AVB · Equity Residential EQR · Invitation Homes INVH · Sun Communities SUI · UDR UDR · Camden Property CPT · Essex Property ESS · Mid-America MAA · American Homes 4 Rent AMH · Independence Realty IRT · Veris Residential VRE · Equity Lifestyle ELS",
        "Retail REITs": "Simon Property SPG · Realty Income O · Federal Realty FRT · Regency Centers REG · Kimco KIM · Brixmor BRX · Tanger SKT · NETSTREIT NTST · Phillips Edison PECO · Acadia ACDC · Saul Centers BFS · Whitestone WSR · Cedar Realty CDR (인수 후)",
        "Office REITs": "Boston Properties BXP · Vornado VNO · Kilroy KRC · Highwoods HIW · Cousins CUZ · Empire State Realty Trust ESRT · SL Green SLG · Brandywine BDN · Piedmont Office PDM · Office Properties Income OPI · Easterly Government Properties DEA · Paramount Group PGRE",
        "Healthcare REITs": "Welltower WELL · Ventas VTR · Healthpeak DOC · Omega Healthcare OHI · National Health Investors NHI · Medical Properties MPW · Sabra Health Care SBRA · CareTrust REIT CTRE · Strawberry Fields 비상장 · Universal Health Realty UHT · NorthStar Healthcare 비상장",
        "Specialty REITs": "Public Storage PSA (Self-storage) · Extra Space EXR (Self-storage) · CubeSmart CUBE · Life Storage 비상장 (인수 후) · National Storage Affiliates NSA · VICI Properties VICI (Casinos) · Gaming and Leisure GLPI · MGM Growth 비상장 (VICI 인수) · Lamar Advertising LAMR · Outfront OUT · SBA Communications SBAC · American Tower AMT · Crown Castle CCI · Uniti Group UNIT · Iron Mountain IRM · Innovative Industrial IIPR (cannabis) · Spirit Realty SRC · NetSTREIT NTST",
        "Mortgage REITs": "Annaly NLY · Starwood STWD · Blackstone Mortgage BXMT · AGNC Investment AGNC · Two Harbors TWO · Hannon Armstrong HASI · New Residential NRZ (Rithm RITM) · Ladder Capital LADR · Apollo Commercial ARI · Granite Point GPMT · Arlington Asset AAIC · Cherry Hill CHMI · ARMOUR Residential ARR · Ellington Financial EFC · KKR Real Estate KREF · MFA Financial MFA · Western Asset WMC · Anchorman 비상장 (Western Asset 자회사) · Chimera Investment CIM · Dynex Capital DX · PennyMac Mortgage PMT",
        "Asia REIT": "JP (Nippon Building Fund 8951.T · Japan Real Estate 8952.T · Japan Retail Fund 8953.T · GLP J-REIT 3281.T · Industrial & Infrastructure 3249.T · Activia 3279.T · Premier Investment 8956.T · Daiwa Office 8976.T · Top REIT 8982.T · Sekisui House 1928.T REIT · Hankyu Hanshin 9042.T 자회사), SG REIT (CapitaLand Integrated CICT.SI · Mapletree Logistics M44U.SI · Frasers Logistics BUOU.SI · Keppel DC AJBU.SI · Mapletree Pan Asia Commercial N2IU.SI · Ascendas A17U.SI · Mapletree Industrial ME8U.SI), HK (Link REIT 0823.HK · Champion REIT 2778.HK · Sunlight REIT 0435.HK · Yuexiu Property 0123.HK)",
    },
}
