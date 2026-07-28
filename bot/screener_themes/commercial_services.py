"""Commercial & Professional Services — L3 under Industrials.

HR/Staffing + Research/Consulting + Security/Facility. 美 BLS Wage Cost
+ ADP 비농업 고용 + GenAI 의 일자리 disruption 영향이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Commercial & Professional Services (상업 및 전문 서비스)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "commercial_services",
        "professional_services",
        "전문서비스",
        "hr_staffing",
        "인력서비스",
        "consulting",
        "컨설팅",
    ],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Payroll + HCM (ADP · Paychex · Workday)",
        "Staffing + Recruitment (ManpowerGroup · Robert Half · Insperity)",
        "Research / Data Analytics (Verisk · Gartner · CoStar · Booz Allen)",
        "Facility Services (Cintas · ABM · Rollins)",
        "Security (ADT · Brinks · Allied Universal 비상장)",
    ],
    "catalyst_types": [
        "美 BLS Wage Cost Index + ADP 비농업 고용 → staffing 매출 직결",
        "美 JOLTS 분기 채용 공고 + Unemployment rate + 분기 임금 상승률",
        "GenAI / LLM 의 IT consulting / outsourcing 시장 disruption",
        "美 GDP + 기업 capex 사이클 → CRE inspection + facility services 수요",
        "전기차·로봇 reshoring → 분기 자동화 컨설팅 매출 (Accenture · BAH)",
        "보안 + 가드 시장 — 정부 + 데이터센터 + 헬스케어 capex",
    ],
    "regional_concentration": {
        "US Payroll + HCM": "Automatic Data Processing ADP · Paychex PAYX · Workday WDAY · Paycom PAYC · Paylocity PCTY · Ceridian CDAY · TriNet TNET",
        "US Staffing + Recruitment": "Robert Half RHI · ManpowerGroup MAN · Insperity NSP · ASGN ASGN · Kforce KFRC · Korn Ferry KFY · Heidrick & Struggles HSII",
        "Research/Consulting": "Verisk VRSK · Gartner IT · CoStar Group CSGP · Booz Allen Hamilton BAH · Forrester FORR · ICF International ICFI · Nielsen 비상장",
        "Facility Services + Security": "Cintas CTAS · ABM Industries ABM · Rollins ROL · UniFirst UNF · Service Corp SCI · ADT ADT · Brinks BCO",
        "EU + Asia HR/Staffing": "Adecco ADEN.SW · Randstad RAND.AS · Hays HAS.L · PageGroup PAGE.L · JP (Recruit 6098.T — Indeed 모회사 · Persol 2181.T · Pasona 2168.T), KR (휴넷 비상장 · 사람인HR 143240.KQ · 리크루트 비상장)",
    },
}
