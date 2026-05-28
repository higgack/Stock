"""Machinery, Tools & Components — L3 under Industrials.

CAT/DE/CMI 같은 heavy machinery 의 cyclical capex + 美 인프라 bill +
중국 14차 5개년 + 일본 자동화 (factory automation) 사이클.
"""

from __future__ import annotations

THEME = {
    "domain": "Machinery, Tools & Components (기계, 도구 및 부품)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "machinery",
        "기계_l3",
        "heavy_machinery",
        "중장비",
        "farm_machinery",
        "농기계",
        "factory_automation",
        "공장자동화",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Heavy Construction (Caterpillar · Deere · Komatsu · Volvo CE)",
        "Farm Machinery (Deere · AGCO · CNH Industrial · Kubota)",
        "Diesel Engines + Powertrain (Cummins · PACCAR · Volvo Penta)",
        "Factory Automation (Fanuc · Yaskawa · Keyence · Rockwell)",
        "General Industrial (ITW · Parker · Dover · Stanley B&D · Emerson)",
        "Bearings + Power Transmission (Timken · SKF · Schaeffler)",
    ],
    "catalyst_types": [
        "美 인프라 bill (CHIPS + IIJA) 집행률 → CAT/DE/PWR 매출 ramp",
        "美 농산물 가격 (옥수수·콩) + DE 분기 농기계 매출 가이드",
        "中国 14차 5개년 인프라 + 노후 단지 재개발 → SANY/Zoomlion 매출",
        "日 산업 로봇 분기 수주잔고 + 자동차 + 반도체 capex 사이클 회복",
        "美 reshoring + 멕시코 nearshoring → factory automation 신규 수요",
        "Mining capex 사이클 (구리·금·리튬) + Caterpillar mining 부문",
    ],
    "regional_concentration": {
        "US Heavy Machinery": "Caterpillar CAT · Deere DE · Cummins CMI · PACCAR PCAR · AGCO AGCO · Allison Transmission ALSN · Oshkosh OSK · Terex TEX · Watts Water WTS · Donaldson DCI · Toro TTC · BWX Technologies BWXT",
        "US General Industrial": "Illinois Tool Works ITW · Parker-Hannifin PH · Dover DOV · Stanley B&D SWK · Emerson EMR · Ingersoll Rand IR · Fortive FTV · Snap-on SNA · Graco GGG · Lincoln Electric LECO · Kennametal KMT · Roper ROP · IDEX IEX · Nordson NDSN · Watts Water WTS",
        "Japan Heavy + FA": "Komatsu 6301.T · Kubota 6326.T · Hitachi Construction 6305.T · Yamaha Motor 7272.T · Fanuc 6954.T · Yaskawa 6506.T · Keyence 6861.T · DENSO 6902.T · SMC 6273.T · Misumi 9962.T · THK 6481.T · Disco 6146.T · Daifuku 6383.T",
        "EU + UK": "Siemens SIE.DE · Bosch 비상장 · Atlas Copco ATCO-A.ST · Sandvik SAND.ST · Volvo Group VOLV-B.ST · CNH Industrial CNH · Hexagon HEXA-B.ST · Spirax-Sarco SPX.L · Rotork ROR.L · Weir Group WEIR.L",
        "Korea": "현대중공업지주 267250.KS · 두산밥캣 241560.KS · HD현대인프라코어 비상장 · 두산에너빌리티 034020.KS · 효성중공업 298040.KS · 한미반도체 042700.KS",
        "China": "SANY Heavy 600031.SS · Zoomlion 1157.HK · XCMG 000425.SZ · Inovance 300124.SZ · Estun 002747.SZ · Siasun 300024.SZ",
    },
}
