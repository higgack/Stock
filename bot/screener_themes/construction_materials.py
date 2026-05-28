"""Construction Materials — L3 under Basic Materials.

시멘트 + 골재 + 콘크리트. 美 IIJA 인프라 bill 집행률 + 美 신규 단독주택
+ 中国 부동산 위기 + EU CBAM 효력이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Construction Materials (건축 자재)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "construction_materials",
        "건축자재_l3",
        "cement",
        "시멘트",
        "aggregates",
        "골재",
        "concrete",
        "콘크리트",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "US Aggregates + Cement (Martin Marietta · Vulcan · Eagle Materials · Summit)",
        "Global Cement (CRH · Holcim · Heidelberg · CEMEX)",
        "Concrete + Ready-mix (USG · GMS · Eagle Materials)",
        "China Cement (Anhui Conch · CNBM)",
        "Asia Cement + Korea + Japan (쌍용 · 한일 · 아세아 · Taiheiyo)",
    ],
    "catalyst_types": [
        "美 IIJA 인프라 bill 집행률 + 신규 도로 + 비행장 modernization",
        "美 신규 단독주택 + 모기지 금리 + 분기 ready-mix 매출",
        "中国 부동산 위기 회복 + 14차 5개년 도시화 + 부동산 보조금",
        "EU CBAM 효력 시점 + EU 건축 자재 가격 변동",
        "美 데이터센터 + 반도체 fab construction starts (CHIPS Act)",
        "Eagle Materials 분기 wallboard 매출 + 시멘트 가격 인상 사이클",
    ],
    "regional_concentration": {
        "US Aggregates + Cement": "Martin Marietta MLM · Vulcan Materials VMC · Eagle Materials EXP · Summit Materials SUM · USG 비상장 · US Concrete 비상장 (인수 후) · Sterling Infrastructure STRL · Construction Partners ROAD · MasTec MTZ · Quanex Building NX · Continental Building Products 비상장",
        "Global Cement": "CRH plc CRH · Holcim HOLN.SW · HeidelbergCement HEI.DE · CEMEX CX (MX) · LafargeHolcim 비상장 (Holcim 합병) · Buzzi BZU.MI · Cementir Holding CEM.MI · UltraTech Cement ULTRACEMCO.NS · Grasim Industries GRASIM.NS · Shree Cement SHREECEM.NS · Ambuja Cements AMBUJACEM.NS · ACC ACC.NS",
        "Concrete + Wallboard + Specialty": "USG 비상장 · GMS GMS · Eagle Materials EXP · Continental Building Products 비상장 · Boise Cascade BCC · Builders FirstSource BLDR · Quanex Building NX · Mueller Industries MLI · MDU Resources MDU",
        "China Cement": "Anhui Conch 0914.HK · CNBM 3323.HK · Huaxin Cement 600801.SS · Tianshan Cement 000877.SZ · Jidong Cement 000401.SZ · West China Cement 2233.HK",
        "Asia + Korea + Japan": "JP (Taiheiyo Cement 5233.T · Ube 4220.T · Sumitomo Osaka Cement 5232.T · Mitsubishi Materials 5711.T), KR (쌍용C&E 003410.KS · 한일시멘트 300720.KS · 아세아시멘트 183190.KS · 성신양회 004980.KS · 한일현대시멘트 비상장)",
    },
}
