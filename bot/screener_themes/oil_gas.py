"""Oil, Gas & Consumable Fuels — L3 under Energy.

Integrated + E&P + Refining + Midstream + LNG + Uranium + Coal. OPEC+
생산 결정 + Permian shale plateau + LNG Phase 2 + 우라늄 spot pricing
+ SMR 인허가가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Oil, Gas & Consumable Fuels (석유, 가스 및 소비 연료)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "oil_gas",
        "oil_gas_l3",
        "석유가스_l3",
        "integrated_oil",
        "e_p",
        "exploration_production",
        "midstream_l3",
        "refining",
        "정제",
        "lng_l3",
        "uranium_l3",
        "smr_l3",
        "coal_l3",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Integrated Majors (ExxonMobil · Chevron · Shell · BP · TotalEnergies · Eni · Equinor)",
        "E&P US Shale Focused (ConocoPhillips · EOG · Occidental · Devon · Diamondback)",
        "Refining & Marketing (Marathon Petroleum · Valero · Phillips 66 · HF Sinclair)",
        "Midstream (Enterprise Products · Kinder Morgan · Williams · Enbridge · ONEOK)",
        "LNG Producers + Infra (Cheniere · NextDecade · Venture Global · Sempra)",
        "Uranium Mining + Enrichment (Cameco · Kazatomprom · NexGen · Centrus · BWXT)",
        "Coal Thermal + Metallurgical (Peabody · Arch · Warrior Met · 中 Shenhua)",
    ],
    "catalyst_types": [
        "OPEC+ 생산 결정 (월별 JMMC) + 미국 SPR refill 재개 시점",
        "Permian shale 일일 출하량 plateau / 감소 데이터 + 신규 well productivity",
        "LNG Phase 2 가동 일정 (Plaquemines T1 / Corpus Christi T3 / Rio Grande T1)",
        "美 IRA 45Z (SAF) + 45Q (CCUS) 가이드 변경 + EU CBAM 효력",
        "우라늄 long-term contract pricing (유틸리티 procurement cycle)",
        "SMR 美 NRC 인허가 (NuScale · X-energy · Oklo · TerraPower · BWXT)",
        "中国 + 인도 thermal coal import quota + 원전 신규 12-15기 가동 일정",
        "Trump 행정명령 - 美 strategic petroleum reserve 매도 중단",
    ],
    "regional_concentration": {
        "Integrated Majors": "ExxonMobil XOM · Chevron CVX · Shell SHEL · BP BP · TotalEnergies TTE · Eni ENI.MI · Equinor EQNR.OL · Petrobras PBR · Repsol REP.MC · OMV OMV.VI · Inpex 1605.T · Cnooc 0883.HK · Sinopec 0386.HK · PetroChina 0857.HK · Galp Energia GALP.LS",
        "E&P US Shale Focused": "ConocoPhillips COP · EOG EOG · Occidental OXY · Devon DVN · Diamondback FANG · APA APA · Marathon Oil MRO · Coterra CTRA · Pioneer 비상장 (ExxonMobil 인수) · Continental 비상장 (Hamm 인수) · Range Resources RRC · Ovintiv OVV · CNX Resources CNX · Antero AR · Comstock CRK · Northern Oil NOG · Magnolia MGY · Permian Basin PBT · Civitas CIVI · SM Energy SM · Talos TALO · Vital Energy VTLE",
        "Refining + Marketing": "Marathon Petroleum MPC · Valero Energy VLO · Phillips 66 PSX · HF Sinclair DINO · PBF Energy PBF · Delek DK · CVR Energy CVI · Par Pacific PARR, EU (Repsol REP.MC · Neste NESTE.HE — SAF leader), KR (S-Oil 010950.KS · SK이노베이션 096770.KS · GS칼텍스 비상장)",
        "Midstream": "Enterprise Products Partners EPD · Kinder Morgan KMI · Williams WMB · Enbridge ENB · TC Energy TRP · MPLX MPLX · Targa TRGP · ONEOK OKE · Cheniere LNG · Energy Transfer ET · DTM 비상장 · Williams Energy WMB · Plains All American PAA · NuStar 비상장 (인수 후) · Western Midstream WES · Hess Midstream HESM · Antero Midstream AM · DCP Midstream 비상장 (인수 후)",
        "LNG Producers": "Cheniere LNG · NextDecade NEXT · Venture Global VG · Tellurian TELL · Sempra SRE · Cheniere Energy Partners CQP · Pembina Pipeline PPL · TC Energy TRP, KR (KOGAS 036460.KS), JP (Inpex 1605.T · Tokyo Gas 9531.T)",
        "Uranium Mining + Enrichment + SMR": "Cameco CCJ · Kazatomprom KAP.IL · NexGen Energy NXE · Denison Mines DNN · Ur-Energy URG · Energy Fuels UUUU · Paladin Energy PDN.AX · Boss Energy BOE.AX · UEC UEC · Centrus Energy LEU (HALEU enrichment) · BWX Technologies BWXT (SMR + 해군 원자로) · NuScale SMR · Oklo OKLO · X-energy 비상장 · TerraPower 비상장 · Yellow Cake YCA.L · Sprott Physical Uranium U.UN.TO",
        "Coal Thermal + Metallurgical": "Peabody BTU · Arch Resources ARCH · Warrior Met Coal HCC · Alpha Met AMR · Consol Energy CEIX · NACCO NC · Hallador Energy HNRG · Ramaco RAMP · Black Hills BKH (coal 부문) · Asia (China Shenhua 1088.HK · Yanzhou 1171.HK · Coal India COALINDIA.NS · Adaro ADRO.JK), AU (Whitehaven WHC.AX · New Hope NHC.AX · Yancoal YAL.AX · Stanmore SMR.AX)",
    },
}
