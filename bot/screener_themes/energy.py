"""Energy — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (2):
 1. Oil, Gas & Consumable Fuels (Integrated + E&P + Refining + Midstream + LNG)
 2. Energy Equipment & Services

Wave 1 trend `solar.py` (재생 cycle) 는 Utilities sector 에 속함 — 본
모듈은 화석 연료 + LNG 만. 우라늄 / SMR / Coal 등도 cover.
"""

from __future__ import annotations

THEME = {
    "domain": "Energy (에너지)",
    "layer": "L2_SECTOR",
    "aliases": [
        "energy",
        "에너지",
        "oil",
        "gas",
        "oil_gas",
        "석유",
        "원유",
        "lng",
        "midstream",
        "uranium",
        "우라늄",
        "smr",
        "원자력",
        "원전",
        "coal",
        "석탄",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Oil, Gas & Consumable Fuels (석유, 가스 및 소비 연료 — Integrated + E&P + Refining + Midstream + LNG)",
        "Energy Equipment & Services (에너지 장비 및 서비스)",
    ],
    "catalyst_types": [
        "OPEC+ 생산 결정 (월별 JMMC) + 미국 SPR refill 재개 시점",
        "Permian shale 일일 출하량 plateau / 감소 데이터 + 신규 well productivity",
        "LNG Phase 2 가동 일정 (Plaquemines T1 / Corpus Christi T3 / Rio Grande T1)",
        "美 IRA 45Z (SAF) + 45Q (CCUS) 가이드 변경 + EU CBAM 효력",
        "中东 + Latin America offshore 신규 deepwater 발주 (SLB/HAL/BKR)",
        "SMR 美 NRC 인허가 (NuScale / X-energy / Oklo / TerraPower)",
        "우라늄 long-term contract pricing (유틸리티 procurement cycle)",
        "中国 + 인도 thermal coal import quota + 원전 신규 12-15기 가동 일정",
    ],
    "regional_concentration": {
        "Oil, Gas & Consumable Fuels": "Integrated US (ExxonMobil XOM · Chevron CVX) + EU (Shell SHEL · BP BP · TotalEnergies TTE · Eni ENI.MI · Equinor EQNR.OL) + BR (Petrobras PBR), E&P (ConocoPhillips COP · EOG EOG · Occidental OXY · Devon DVN · Diamondback FANG · APA APA · Marathon Oil MRO), Refining (Marathon Petroleum MPC · Valero VLO · Phillips 66 PSX · HF Sinclair DINO · PBF PBF), Midstream (Enterprise Products EPD · Kinder Morgan KMI · Williams WMB · ONEOK OKE · Enbridge ENB · TC Energy TRP · MPLX MPLX · Targa TRGP), LNG (Cheniere LNG · Tellurian TELL · NextDecade NEXT · Venture Global VG · Sempra SRE LNG), KR (S-Oil 010950.KS · 한국가스공사 036460.KS · SK이노베이션 096770.KS), JP (Inpex 1605.T · Tokyo Gas 9531.T · Idemitsu 5019.T), Uranium (Cameco CCJ · Kazatomprom KAP.IL · NexGen Energy NXE · Denison Mines DNN · Ur-Energy URG · Energy Fuels UUUU · Centrus LEU · BWXT BWXT · SMR NuScale · Oklo OKLO), Coal (Peabody BTU · Arch Resources ARCH · Warrior Met HCC · CN Shenhua 1088.HK · AU Whitehaven WHC.AX · New Hope NHC.AX)",
        "Energy Equipment & Services": "US (Schlumberger SLB · Halliburton HAL · Baker Hughes BKR · NOV NOV · ChampionX CHX · Weatherford WFRD · Liberty LBRT · TechnipFMC FTI · Helmerich H&P HP · Patterson UTI PTEN · Transocean RIG · Valaris VAL · Noble NE · Diamond Offshore DO), EU (Saipem SPM.MI · Subsea7 SUBC.OL), KR (현대중공업 329180.KS — Hyundai Heavy 자회사 + offshore platform)",
    },
}
