"""Carbon Capture, Utilization & Storage (CCUS) — L1 Trend (2026-05-29).

GICS quarterly check (2026-Q2) 가 식별한 신규 emerging industry trend.
시총 ~$70B + 美 45Q 세금공제 ($85/ton sequestered · $180/ton DAC) +
EU CBAM tariff + 美 IRA hydrogen hub funding catalyst. Occidental
Petroleum (OXY) 의 DAC 1MMt 가동 등 상업 운전 사례 등장.

ToC 관점:
 1) Direct Air Capture (DAC) — Occidental + Carbon Engineering 비상장
    · Climeworks 비상장 + Equinor 합작. 1MMt 단위 commercial-scale
    프로젝트 등장 — capex 사이클 시작. 美 DOE Regional DAC Hub 4개
    선정 (Project Cypress · South Texas DAC Hub 등).
 2) Post-combustion Capture (점원 capture) — 석탄/가스 화력 + 시멘트
    + 철강 공장. Aker Carbon Capture · Carbon Clean · 비상장.
 3) CO2 Transport + Storage (파이프라인 + 지하 저장) — Sequestration
    well operators (OXY · Denbury - ExxonMobil 인수 후). 美 CO2
    파이프라인 약 5,000 mile (vs 2030 80,000 mile 목표).
 4) Utilization (Power-to-X · CO2 → 화학원료 · 콘크리트 mineralization)
    — LanzaTech (LNZA) · CarbonCure 비상장. 시장 작지만 보조금 가치.
 5) Hydrogen + CCS pairing (Blue Hydrogen) — Air Products · Linde +
    Plug Power green hydrogen 경쟁. 美 IRA 45V tax credit cycle.

Catalyst weighting: 美 IRA 45Q 분기 청구 (1MMt+ project 진척) +
EU CBAM 단계적 효력 (2026 → 2034 full) + 美 DOE Regional DAC Hub 1st-
of-kind funding + UK Cluster Sequencing (HyNet · East Coast Cluster).
"""

from __future__ import annotations

THEME = {
    "domain": "Carbon Capture, Utilization & Storage (CCUS)",
    "layer": "L1_TREND",
    "aliases": [
        "carbon_capture",
        "ccs",
        "ccus",
        "탄소포집",
        "탄소저장",
        "dac",
        "direct_air_capture",
        "blue_hydrogen",
        "블루수소",
        "co2_sequestration",
    ],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Direct Air Capture (DAC — Occidental 1PointFive · Climeworks · Carbon Engineering)",
        "Post-combustion Capture (Aker Carbon Capture · Carbon Clean · Mitsubishi Heavy)",
        "CO2 Transport + Geologic Storage (OXY sequestration well · 美 파이프라인)",
        "Utilization + Mineralization (LanzaTech · CarbonCure · Twelve · Carbicrete)",
        "Blue Hydrogen + CCS Pairing (Air Products · Linde · ExxonMobil · Shell)",
        "Oil Services CCS Tools (SLB · Baker Hughes · Halliburton — 기존 capability 활용)",
    ],
    "catalyst_types": [
        "美 IRA 45Q 분기 청구액 ($85/ton sequestration · $180/ton DAC)",
        "美 DOE Regional DAC Hub funding milestone (Project Cypress · South Texas · 등)",
        "EU CBAM 단계적 효력 (2026 시작 → 2034 full implementation) + EU ETS 가격",
        "UK Cluster Sequencing (HyNet 1st · East Coast 1st · Acorn 2nd 등 진행)",
        "OXY 1PointFive 분기 sequestration metric ton + 미분기 신규 site FID",
        "ExxonMobil + Chevron + Shell 분기 CCS capex disclosure",
        "LanzaTech / Aker Carbon Capture 분기 매출 + 신규 commercial contract",
        "美 EPA Class VI well 인허가 발급 속도 (현재 200+ application backlog)",
    ],
    "regional_concentration": {
        "Direct Air Capture (DAC)": "US (Occidental OXY — 1PointFive STRATOS 1MMt 가동), 비상장 (Carbon Engineering — OXY 인수 후 · Climeworks · Heirloom · Spiritus · Avnos · Verdox · Global Thermostat · Noya), CH (Climeworks 비상장), CA (Carbon Engineering 비상장 - OXY)",
        "Post-combustion Capture": "NO (Aker Carbon Capture ACC.OL · Aker Solutions AKSO.OL), JP (Mitsubishi Heavy 7011.T - KS-21 amine), DE (Linde LIN), 비상장 (Carbon Clean · CO2 Capsol · Svante)",
        "CO2 Transport + Storage (Sequestration Wells)": "US (Occidental OXY · Denbury 비상장 - ExxonMobil 인수 · Talos Energy TALO · Williams WMB · Enterprise Products EPD · Magellan Midstream 비상장 - ONEOK 인수 · ONEOK OKE · Kinder Morgan KMI), CA (Pembina Pipeline PPL · Enbridge ENB)",
        "Utilization + Mineralization": "US (LanzaTech LNZA - SPAC · Twelve 비상장 · Carbicrete 비상장 · Solidia 비상장 · CarbonCure 비상장), CH (Sika SIK.SW - CO2 concrete), CA (CarbonCure 비상장)",
        "Blue Hydrogen + CCS": "US (Air Products APD - 노스 다코타 + 캐나다 H2 hub · Linde LIN), JP (Iwatani 8088.T), KR (한화솔루션 009830.KS · 효성첨단소재 298050.KS), CN (Sinopec 0386.HK · CNOOC 0883.HK - 신규 hub)",
        "Oil Services CCS Tools": "US (SLB SLB · Baker Hughes BKR · Halliburton HAL · NOV NOV · ChampionX CHX) — 기존 reservoir characterization + CO2 injection 노하우 활용",
        "Tech-enabled MRV (Measurement/Reporting/Verification)": "Climeworks Solutions 비상장 · Verra 비상장 · Pachama 비상장 · CarbonPlan 비상장 · BeZero 비상장 · Sylvera 비상장",
        "Korea + Japan + EU 추가": "KR (현대건설 000720.KS - CCS EPC · GS건설 006360.KS · 한국지역난방공사 071320.KS) · JP (Mitsubishi 8058.T · INPEX 1605.T - 호주 CCS) · EU (Equinor EQNR.OL · TotalEnergies TTE.PA · OMV OMV.VI · Eni ENI.MI)",
    },
}
