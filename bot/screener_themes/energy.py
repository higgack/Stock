"""Energy — Wave 2 broad sector (Finviz Energy).

Finviz Energy industries: Oil & Gas Integrated · E&P · Midstream · Refining
& Marketing · Drilling · Equipment & Services · Thermal Coal · Uranium.

ToC 관점 — 글로벌 에너지 transition 의 cross-current:
 1) Permian shale productivity 한계 — 미국 daily oil 출하량이 2025-2026
    들어 plateau. spare capacity 가 OPEC+ 로 다시 이동 → 가격 결정권 회복.
 2) LNG Phase 2 wave (Plaquemines / Corpus Christi 3 / Rio Grande / Driftwood)
    가 2026-2027 가동. global gas 가격 decoupling 완화 + 가스가스 spread
    확대 → midstream takeaway capacity 가 critical bottleneck.
 3) Refining 마진 dampening — 美 SAF (sustainable aviation fuel) mandate
    + IRA 45Z + EU CBAM → cracking margin 변동성 확대.
 4) Oil services - SLB/HAL/BKR 글로벌 Capex 사이클 회복 (offshore 재
    가동). 中东 + Latin America 가 driver.
 5) 우라늄 - SMR (Small Modular Reactor) 의 첫 상용 가동 (BWXT/NuScale/
    Oklo) + 美 NRC 인허가 완화 + 우크라이나 + Niger 공급 차질 + 중국
    원전 신규 12-15기 → spot uranium $80→$120 pricing power.
 6) Coal - thermal cycle 둔화 vs metallurgical (강철용) 견조. 中国 import
    quota + 인도 power 수요.

Wave 1 와 겹침 없음 — solar.py 는 renewable 만 cover. 본 모듈이 화석 +
원자력 dedicated lens.
"""

from __future__ import annotations

THEME = {
    "domain": "Energy (Finviz Sector — Oil & Gas · Midstream · Refining · Services · Uranium · Coal)",
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
        "Oil & Gas — Integrated Majors (XOM / CVX / Shell / TTE)",
        "Oil & Gas — E&P (Permian shale productivity 한계)",
        "Oil & Gas — Midstream (Permian 가스 takeaway + LNG feed)",
        "Oil & Gas — Refining & Marketing (45Z SAF + CBAM)",
        "Oil & Gas — Drilling (해상 시추 사이클)",
        "Oil & Gas — Equipment & Services (Capex 회복)",
        "LNG — Phase 2 캐파 (Plaquemines / Driftwood)",
        "Uranium — Cameco/Kazatomprom + SMR 상용화",
        "Coal — Thermal cycle + Metallurgical 견조",
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
        "Oil & Gas — Integrated Majors": "US (ExxonMobil XOM / Chevron CVX), UK/NL (Shell SHEL.L / BP BP.L), FR (TotalEnergies TTE.PA), IT (Eni ENI.MI), NO (Equinor EQNR.OL), BR (Petrobras PBR)",
        "Oil & Gas — E&P (US shale focused)": "ConocoPhillips COP / EOG EOG / Occidental OXY / Diamondback FANG / Devon DVN / APA APA / Pioneer 합병 / Marathon Oil MRO 합병 / Continental 비상장",
        "Oil & Gas — Midstream": "Kinder Morgan KMI / Energy Transfer ET / Williams WMB / Enbridge ENB / TC Energy TRP / Enterprise Products EPD / MPLX MPLX / Targa TRGP / ONEOK OKE / Cheniere LNG / Tellurian 비상장",
        "Oil & Gas — Refining & Marketing": "Valero VLO / Phillips 66 PSX / Marathon Petroleum MPC / HF Sinclair DINO / PBF Energy PBF / Delek DK, EU (Repsol REP.MC / Neste NESTE.HE SAF leader), KR (S-Oil 010950.KS / GS칼텍스 비상장 / SK에너지 비상장)",
        "Oil & Gas — Drilling": "Transocean RIG / Valaris VAL / Noble NE / Diamond Offshore DO / Helmerich H&P HP / Patterson UTI PTEN",
        "Oil & Gas — Equipment & Services": "SLB SLB / Halliburton HAL / Baker Hughes BKR / NOV NOV / ChampionX CHX / Weatherford WFRD / Liberty Energy LBRT / TechnipFMC FTI",
        "LNG — Producer + 인프라": "Cheniere LNG / NextDecade NEXT / Venture Global VG / Tellurian 비상장 / Sempra SRE LNG, KR (KOGAS 036460.KS gas importer), JP (Inpex 1605.T / Tokyo Gas 9531.T LNG importer)",
        "Uranium — Mining": "Cameco CCJ / Kazatomprom KAP.IL ADR / Yellow Cake YCA.L (physical) / Sprott Physical Uranium U.UN.TO / NexGen Energy NXE / Denison Mines DNN / Ur-Energy URG / Energy Fuels UUUU / Paladin Energy PDN.AX / Boss Energy BOE.AX",
        "Uranium — Enrichment & SMR": "Centrus Energy LEU (HALEU enrichment) / Urenco 비상장 / BWX Technologies BWXT (SMR + 해군 원자로) / NuScale SMR / Oklo OKLO / TerraPower 비상장 / X-energy 비상장 / GE Hitachi BWRX-300",
        "Coal — Thermal + Metallurgical": "Peabody BTU / Arch Resources ARCH (met coal) / Warrior Met Coal HCC / Alpha Met AMR / Consol Energy CEIX / NACCO NC, Asia (China Shenhua 1088.HK / Yanzhou 1171.HK / Coal India COALINDIA.NS), AU (Whitehaven WHC.AX / New Hope NHC.AX)",
    },
}
