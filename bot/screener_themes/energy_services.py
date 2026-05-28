"""Energy Equipment & Services — L3 under Energy.

Schlumberger + Halliburton + Baker Hughes + 시추선 (Transocean · Valaris)
+ 中东 + Latin America offshore deepwater 사이클. SLB/HAL/BKR 분기 international
+ 中동 capex 가이드가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Energy Equipment & Services (에너지 장비 및 서비스)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "energy_services",
        "oilfield_services",
        "유전서비스",
        "ofs",
        "drilling",
        "시추",
        "offshore_oil",
        "오일필드",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Integrated Oilfield Services (SLB · Halliburton · Baker Hughes)",
        "Frac + Pressure Pumping (Liberty Energy · ProPetro · NexTier)",
        "Drilling Contractors — Offshore (Transocean · Valaris · Noble · Diamond Offshore)",
        "Drilling Contractors — Onshore (Helmerich & Payne · Patterson UTI · Nabors)",
        "OFS Equipment + Tools (NOV · ChampionX · TechnipFMC · Weatherford)",
        "Subsea + 해상 EPC (TechnipFMC · Subsea7 · Saipem)",
    ],
    "catalyst_types": [
        "SLB · HAL · BKR 분기 international revenue + 中东 + Latin Amer 사이클",
        "美 rig count (Baker Hughes 주간) + frac fleet utilization",
        "Offshore deepwater 신규 FID (Final Investment Decision) — 브라질 · 가이아나 · West Africa",
        "Permian shale 잔존 inventory + 신규 well productivity",
        "TechnipFMC · Subsea7 분기 backlog + 풍력 + offshore 신규 EPC 발주",
        "Trump 행정명령 - 美 LNG export 부활 + offshore lease 재개",
    ],
    "regional_concentration": {
        "Integrated OFS — US (Big 3)": "Schlumberger SLB · Halliburton HAL · Baker Hughes BKR",
        "US Pressure Pumping + Frac": "Liberty Energy LBRT · ProPetro PUMP · NexTier NEX · ProFrac PFHC · Calfrac WSO-A.TO · Pioneer (Sat) 비상장 · Sponsor Capital 비상장 · Stewart's 비상장 · STEP Energy STEP.TO",
        "Drilling — Offshore": "Transocean RIG · Valaris VAL · Noble NE · Diamond Offshore DO · Saipem SPM.MI · Seadrill SDRL · Borr Drilling BORR · Helix Energy HLX · Vantage Drilling 비상장 · Shelf Drilling 비상장 · Odfjell Drilling ODL.OL · Ocean Yield OCY.OL",
        "Drilling — Onshore": "Helmerich H&P HP · Patterson UTI PTEN · Nabors NBR · Independence Contract 비상장 · Precision Drilling PDS · Ensign Energy ESI.TO",
        "OFS Equipment + Tools": "NOV NOV · ChampionX CHX · TechnipFMC FTI · Weatherford WFRD · Innospec IOSP · CTS Corp CTS · Core Laboratories CLB · Forum Energy FET · Cactus WHD · KLX Energy KLXE · Solaris Energy SOI · Mammoth Energy TUSK · Profire PFIE",
        "Subsea + 해상 EPC": "TechnipFMC FTI · Subsea7 SUBC.OL · Saipem SPM.MI · McDermott 비상장 · Petrofac PFC.L · Wood Group WG.L · Aker Solutions AKSO.OL · TGS TGS.OL · DOF DOF.OL · Cadeler CADLR.OL (WTIV jackup) · Eidesvik 비상장 · Solstad SOFF.OL",
        "Offshore Wind Cable + Foundation": "IT (Prysmian PRY.MI), FR (Nexans NEX.PA), DK (Cadeler CADLR.OL — WTIV jackup), NO (DOF DOF.OL · Solstad SOFF.OL · Havila Shipping HAVI.OL)",
    },
}
