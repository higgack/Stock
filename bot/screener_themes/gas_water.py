"""Gas & Water Utilities — L3 under Utilities.

Regulated Gas Distribution + Water/Wastewater. EPA PFAS 정화 자금 +
美 IIJA 수자원 인프라 + 분기 PUC 가격 인상 승인이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Gas & Water Utilities (가스 및 수도 유틸리티)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "gas_water",
        "gas_water_utility",
        "가스수도",
        "gas_utility",
        "가스유틸",
        "water_utility",
        "수도유틸",
    ],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Regulated Gas Distribution (Atmos · NiSource · Spire · UGI · NW Natural)",
        "Water/Wastewater US (American Water Works · Essential Utilities · California Water)",
        "Water Equipment + Tech (Xylem · Pentair · Watts · A.O. Smith)",
        "EU Water + Gas (Veolia · Suez · Snam · Enagás · National Grid)",
        "Asia Gas + Water (Tokyo Gas · Osaka Gas · 한국가스공사 · 서울가스)",
    ],
    "catalyst_types": [
        "Fed funds rate path → 차입 비용 변화 + 분기 배당 매력",
        "美 EPA PFAS 'forever chemicals' 규제 + 인프라 bill 수처리 자금",
        "데이터센터 + 반도체 fab 신규 site → 수처리 수요 + 폐수 처리 capex",
        "美 IIJA 수자원 인프라 자금 (Lead Service Line replacement)",
        "분기 PUC 가격 인상 승인 + 신규 자본 capex plan",
        "EU CO2 가격 + 가스 → 전기 전환 + UK Heat Pump 보조금",
    ],
    "regional_concentration": {
        "US Regulated Gas Distribution": "Atmos Energy ATO · NiSource NI · Spire Inc SR · Southwest Gas SWX · UGI UGI · ONE Gas OGS · New Jersey Resources NJR · Northwest Natural NWE · National Fuel Gas NFG · South Jersey Industries SJI (인수 후 비상장) · WGL Holdings 비상장 (인수 후) · NW Natural 비상장",
        "US Water + Wastewater": "American Water Works AWK · Essential Utilities WTRG (구 Aqua America) · California Water CWT · American States Water AWR · SJW Group SJW · Middlesex Water MSEX · York Water YORW · Artesian Resources ARTNA · Consolidated Water CWCO · Global Water Resources GWRS · Pure Cycle PCYO",
        "Water Equipment + Treatment Tech": "Xylem XYL · Pentair PNR · Mueller Water MWA · Roper Technologies ROP · Watts Water WTS · A.O. Smith AOS · Evoqua 비상장 (Xylem 인수 후) · Badger Meter BMI · Franklin Electric FELE · Lindsay Corp LNN (관개) · Toro TTC (관개) · Northwest Pipe NWPX · Forterra FRTA · Mueller Industries MLI · Donaldson DCI · Tetra Tech TTEK · Stantec STN · Ecolab ECL",
        "EU Water + Gas": "Veolia VIE.PA · Suez 비상장 (Veolia 인수 후) · Biffa BIFF.L · Snam SRG.MI · Enagás ENG.MC · National Grid NG.L · Centrica CNA.L · United Utilities UU.L · Pennon PNN.L · Severn Trent SVT.L · Yorkshire Water 비상장 · Northumbrian Water 비상장",
        "Asia Gas + Water": "JP (Tokyo Gas 9531.T · Osaka Gas 9532.T · Toho Gas 9533.T · Shizuoka Gas 9543.T · Saibu Gas 9536.T · Nippon Gas 8174.T - LP gas), KR (한국가스공사 036460.KS · 삼천리 004690.KS · 서울가스 017390.KS · 부산가스 015350.KS · 경동가스 비상장 · S-Oil 010950.KS · 한국전력 015760.KS), CN (Beijing Gas 비상장 · Shanghai Gas 600133.SS · Sinopec Kantons 0934.HK · ENN Energy 2688.HK · Kunlun Energy 0135.HK · China Gas Holdings 0384.HK)",
    },
}
