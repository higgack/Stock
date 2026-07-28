"""Building Products & Construction — L3 under Industrials.

HVAC R-454B 냉매 전환 (2025 effective) + 데이터센터 cooling + 주거+상업
capex 사이클. E&C (Jacobs · Fluor · Quanta) 는 인프라 bill 직접 수혜.
"""

from __future__ import annotations

THEME = {
    "domain": "Building Products & Construction (건축 제품 및 건설)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "building_products",
        "건축자재",
        "hvac_l3",
        "engineering_construction",
        "ec",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "HVAC (Carrier · Johnson Controls · Trane · Lennox · Daikin)",
        "Elevators + Escalators (Otis · Schindler · KONE · Hitachi)",
        "Insulation + Roofing (Owens Corning · Masco)",
        "Water Heating (A.O. Smith · Rheem 비상장)",
        "Engineering & Construction (Jacobs · Fluor · AECOM · Quanta · EMCOR · MasTec)",
        "Building products distribution (Ferguson · Builders FirstSource)",
    ],
    "catalyst_types": [
        "HVAC R-454B 냉매 전환 (2025 effective) → AC replacement cycle 강제",
        "美 데이터센터 + 반도체 fab construction starts (CHIPS Act 시행)",
        "美 인프라 bill (IIJA) 도로·교량 capex 집행률 + 비행장 modernization",
        "美 주택 착공 + 30Y 모기지 금리 + 신규 단독주택 inventory",
        "EU 에너지 효율 직접 + heat pump 보조금 + 영국 grant scheme",
        "中国 14차 5개년 도시화 + 노후 단지 재개발 보조금",
    ],
    "regional_concentration": {
        "US HVAC + Building": "Carrier CARR · Johnson Controls JCI · Trane TT · Otis OTIS · Lennox LII · A.O. Smith AOS · Owens Corning OC · Masco MAS · Allegion ALLE · Watsco WSO · Pool Corp POOL · Vulcan Materials VMC · Martin Marietta MLM",
        "US E&C": "Jacobs J · Fluor FLR · AECOM ACM · KBR KBR · Quanta Services PWR · EMCOR EME · MasTec MTZ · Tutor Perini TPC · Comfort Systems FIX · Sterling STRL · Granite GVA · Construction Partners ROAD · Primoris PRIM · Dycom DY",
        "EU": "Vinci DG.PA · Bouygues EN.PA · Hochtief HOT.DE · ACS ACS.MC · Sika SIK.SW · Saint-Gobain SGO.PA · CRH CRH · Holcim HOLN.SW",
        "Japan": "Daikin 6367.T (HVAC 글로벌 #1) · Mitsubishi Electric 6503.T · Toto 5332.T · Lixil 5938.T",
        "Korea": "현대건설 000720.KS · 대우건설 047040.KS · GS건설 006360.KS · DL이앤씨 375500.KS · 삼성물산 028260.KS",
        "Elevators": "US (Otis OTIS), CH (Schindler SCHN.SW), FI (KONE KNEBV.HE), JP (Hitachi 6501.T · Mitsubishi Electric 6503.T)",
    },
}
