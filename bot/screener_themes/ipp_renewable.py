"""Independent Power & Renewable Electricity Producers — L3 under Utilities.

Vistra · Constellation · NRG (IPP) + First Solar + Enphase + SolarEdge
+ Sunrun (Solar) + Vestas + GE Vernova + Nordex (Wind) + Bloom Energy +
NextEra Energy Partners. AI 데이터센터 전력 + IRA 45X + BOEM offshore
lease가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Independent Power & Renewable Electricity Producers (독립 발전 및 재생에너지)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "ipp_renewable",
        "ipp",
        "독립발전",
        "renewable_l3",
        "재생에너지_l3",
        "solar_l3",
        "태양광_l3",
        "wind_l3",
        "풍력_l3",
        "geothermal",
        "지열",
        "hydrogen",
        "수소",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "US IPP (Vistra · Constellation · NRG · Talen)",
        "US Solar (First Solar · Enphase · SolarEdge · Sunrun · Nextracker)",
        "Wind Turbine OEM (GE Vernova · Vestas · Siemens Energy · Nordex · Goldwind)",
        "EU/UK Offshore Wind Developer (Ørsted · Iberdrola · RWE · EDP · SSE)",
        "ESS Integrator + Storage (Fluence · Tesla Megapack · Stem · Bloom Energy)",
        "Geothermal + Hydrogen (Ormat · Plug Power · Bloom Energy · Air Products)",
    ],
    "catalyst_types": [
        "美 IRA 45X (Solar/Wind production credit) + Trump 행정명령 IRA roll-back",
        "BOEM offshore wind lease + Notice To Proceed + 한국 풍력 RFP",
        "ISO interconnection queue + 美 송전망 신규 line 승인 (CAISO · ERCOT · PJM)",
        "Enphase/SolarEdge 분기 microinverter sell-through + 美 residential solar 회복",
        "EU CBAM + 中国 polysilicon 반덤핑 + 영국 CfD AR6 입찰 결과",
        "Bloom Energy 분기 fuel cell delivery + 데이터센터 SOFC 채택",
        "Tesla Megapack 분기 GWh delivery + 美/호주 grid-scale ESS 계약",
    ],
    "regional_concentration": {
        "US IPP": "Vistra Corp VST · Constellation Energy CEG · NRG Energy NRG · Talen Energy TLN · Calpine 비상장 · Eversource ES (IPP 부문)",
        "US Solar + Inverter + Tracker": "First Solar FSLR · Enphase Energy ENPH · SolarEdge Technologies SEDG · Sunrun RUN · Sunnova NOVA · Array Technologies ARRY · Nextracker NXT · Maxeon Solar MAXN · SunPower SPWR · Daqo New Energy DQ · JinkoSolar JKS · Canadian Solar CSIQ · Trina Solar 688599.SS · LONGi 601012.SS · TCL Zhonghuan 002129.SZ · GCL-Poly 3800.HK · Shoals Technologies SHLS · Generac GNRC (재생에너지 backup) · Plug Power PLUG · FuelCell Energy FCEL",
        "Wind Turbine OEM": "GE Vernova GEV · Vestas VWS.CO · Siemens Energy ENR.DE (Gamesa 자회사) · Nordex NDX1.DE · Goldwind 2208.HK · Mingyang 601615.SS · TPI Composites TPIC · Shoals Technologies SHLS",
        "EU/UK Offshore Wind Developer": "Ørsted ORSTED.CO · Iberdrola IBE.MC · EDP EDP.LS · Enel ENEL.MI · RWE RWE.DE · SSE SSE.L · Drax DRX.L · Atlantica Sustainable AY · Brookfield Renewable BEP · Clearway Energy CWEN · NextEra Energy Partners NEP",
        "ESS Integrator + Storage": "Fluence FLNC · Tesla TSLA (Megapack) · Stem STEM · Sungrow 300274.SZ · LG에너지솔루션 373220.KS · 삼성SDI 006400.KS · SK on 비상장 · BYD 002594.SZ + 1211.HK · CATL 300750.SZ · EVE 300014.SZ · 에코프로비엠 247540.KQ · POSCO퓨처엠 003670.KS",
        "Geothermal + Hydrogen + Fuel Cell": "Ormat Technologies ORA (Geothermal) · Plug Power PLUG · Bloom Energy BE · FuelCell Energy FCEL · Ballard Power BLDP · Cummins CMI (수소 연료전지) · ITM Power ITM.L · Linde LIN (수소 인프라) · 두산퓨얼셀 336260.KS · 효성첨단소재 298050.KS",
        "Korea + Japan + Asia Renewable": "한화솔루션 009830.KS · OCI 010060.KS · 신성이엔지 011930.KS · 두산퓨얼셀 336260.KS · 우리기술 비상장, JP (Renova 9519.T · West Holdings 1407.T · MTG Capital 비상장 · Eurus Energy 비상장 - Toyota Tsusho 자회사), CN (Goldwind 2208.HK · Mingyang 601615.SS · LONGi 601012.SS · Sungrow 300274.SZ · GCL-Poly 3800.HK · Tongwei 600438.SS)",
    },
}
