"""Electrical Equipment — L3 under Industrials.

AI 데이터센터 전력 폭증 + 美 reshoring 그리드 capex + EV/heat pump
전동화 사이클의 가장 직접적인 수혜 layer. 변압기 / GIS / SCADA / 케이블
공급 부족 binding.
"""

from __future__ import annotations

THEME = {
    "domain": "Electrical Equipment (전력기기)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "electrical_equipment",
        "전력기기",
        "전력장비",
        "transformer",
        "변압기",
        "switchgear",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Transformer (대형 변압기 — Eaton · Hitachi Energy · HD현대일렉 · ABB)",
        "GIS (Gas-Insulated Switchgear) + 배전반",
        "Power Cable (HV/MV underground + offshore — Prysmian · Nexans · LS전선)",
        "UPS + Data Center Power Mgmt (Vertiv · Schneider · Eaton)",
        "Industrial Automation (Rockwell · Emerson · Siemens · Mitsubishi)",
        "Backup Generator + ATS (Generac · Cummins · Eaton)",
    ],
    "catalyst_types": [
        "AI 데이터센터 전력 capex 가이드 (MSFT/META/AWS/Google) + 24/7 24GW+ by 2030",
        "美 ISO interconnection queue (CAISO · ERCOT · PJM) + 송전망 신규 line approval",
        "변압기 리드타임 (현재 100-128주) → capacity 증설 발표 (Eaton · HD현대일렉 신규 공장)",
        "EU RePowerEU + 독일 grid stage 인허가 + UK Nationals Grid capex",
        "中国 14차 5개년 power grid + UHV (Ultra-High Voltage) DC 신규 line",
        "HVAC + EV 전동화 → low/mid voltage 배전반 + smart meter 수요",
    ],
    "regional_concentration": {
        "US Power Equipment": "Eaton ETN · Vertiv VRT · Hubbell HUBB · nVent NVT · Atkore ATKR · Generac GNRC · Acuity Brands AYI · Encore Wire WIRE · MYR Group MYRG · IES Holdings IESC · GE Vernova GEV (transformer 자회사)",
        "EU Power Equipment": "ABB ABBN.SW · Schneider SU.PA · Siemens SIE.DE · Siemens Energy ENR.DE · Legrand LR.PA · Prysmian PRY.MI · Nexans NEX.PA · Hitachi Energy 비상장 (스위스)",
        "Korea Transformer + Grid": "HD현대일렉트릭 267260.KS · 효성중공업 298040.KS · LS ELECTRIC 010120.KS · 일진전기 103590.KS · LS Cable 비상장 · 대한전선 001440.KS · 가온전선 000500.KS",
        "Japan": "Mitsubishi Electric 6503.T · Hitachi 6501.T · Fuji Electric 6504.T · Fujikura 5803.T · Sumitomo Electric 5802.T · Furukawa Electric 5801.T",
        "Industrial Automation": "Rockwell Automation ROK · Emerson EMR · Honeywell HON · Schneider SU.PA · Siemens SIE.DE · ABB ABBN.SW · Mitsubishi Electric 6503.T · Yaskawa 6506.T · Fanuc 6954.T · Keyence 6861.T",
    },
}
