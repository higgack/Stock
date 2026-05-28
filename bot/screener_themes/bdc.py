"""Business Development Companies (BDCs) — L3 under Financials.

Middle-market 사기업 (PE-backed) 에 대한 senior secured loan 제공. SOFR
+ 신용 스프레드 + non-accrual rate (NAR) + base dividend 가능성 + special
dividend 트리거가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Business Development Companies (BDCs)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "bdc",
        "bdcs",
        "business_development_company",
        "프라이빗_크레딧",
        "private_credit",
        "middle_market_loan",
    ],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Tier 1 — Externally Managed Mega-BDCs ($15B+ AUM, Ares · Blue Owl · Owl Rock)",
        "Tier 2 — Internally Managed Quality (Main Street · Hercules)",
        "Tier 3 — Specialty Niche (Saratoga · TPG Specialty · Trinity Capital)",
        "PE Sponsor Affiliated (Goldman BDC · KKR · Apollo · Carlyle)",
        "Venture Debt Focused (Hercules · TriplePoint · Trinity)",
    ],
    "catalyst_types": [
        "SOFR + 美 사기업 default rate + BDC non-accrual rate 분기 가이드",
        "Fed funds rate path → BDC NII (floating-rate loan) 직접 영향",
        "분기 dividend 발표 + base + special + ROAA + leverage 가이드",
        "美 사기업 EBITDA cycle + middle-market PE 인수 활동 데이터",
        "BDC ETF/CEF inflow + 시가총액 대비 NAV premium/discount 변동",
        "Direct Lending market (BX · KKR · ARES · APO 자회사) 와의 경쟁 + spread compression",
    ],
    "regional_concentration": {
        "Tier 1 Mega-BDCs": "Ares Capital ARCC · Blue Owl Capital OBDC · Owl Rock 비상장 (Blue Owl 자회사) · Main Street Capital MAIN · FS KKR Capital FSK · BlackRock TCP Capital TCPC · Capital Southwest CSWC · Sixth Street Specialty TSLX",
        "Tier 2 Quality Mid": "Main Street Capital MAIN · Hercules Capital HTGC · Capital Southwest CSWC · Carlyle Secured Lending CGBD · Bain Capital Specialty BCSF · Goldman Sachs BDC GSBD · TriplePoint Venture Growth TPVG · Gladstone Investment GAIN",
        "Tier 3 Niche": "Saratoga Investment SAR · Trinity Capital TRIN · Runway Growth RWAY · Horizon Technology HRZN · Investcorp BDC ICMB · Logan Ridge Finance LRFC · Stellus Capital SCM · BlackRock Capital Investment BKCC · MidCap Financial Investment MFIC · Crescent Capital BDC CCAP · Capital Trust 비상장 (BlackRock 자회사) · WhiteHorse Finance WHF",
        "PE Sponsor Affiliated": "Apollo BDC AINV · Carlyle Group GBDC (Golub Capital BDC 인수 후) · KKR BDC FSK · Goldman Sachs BDC GSBD · Morgan Stanley Direct Lending MSDL · Blackstone Private Credit (BCRED) 비상장 · Cliffwater Direct Lending Fund 비상장",
        "Venture Debt Specialist": "Hercules Capital HTGC · TriplePoint Venture TPVG · Trinity Capital TRIN · Runway Growth RWAY · Horizon Technology HRZN",
        "BDC Adjacent (Direct Lending)": "Blackstone BX (BCRED) · KKR KKR · Apollo APO · Ares ARES · Carlyle CG · Brookfield BAM · 비공개 시장 직접 lending 영역",
    },
}
