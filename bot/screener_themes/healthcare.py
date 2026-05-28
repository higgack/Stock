"""Healthcare — Wave 2 broad sector (Finviz Healthcare).

Wave 1 의 `pharma.py` (GLP-1/CDMO/Biosimilar cycle 좁은 trend lens) 는 그대로
보존 — 본 모듈은 헬스케어 sector 전체 (Finviz 11 industry) 의 ToC 분석:
Drug Mfg / Biotech / Medical Devices / Medical Instruments / Diagnostics /
Healthcare Plans / Medical Care Facilities / Medical Distribution /
Pharm Retail / Health Information Services.

ToC 관점 — Wave 1 pharma 와 본 모듈의 차이:
 • Wave 1 pharma: '지금' (2026 GLP-1 cycle 정점 / 미·중 BIOSECURE / Medicare
   IRA 1차 협상) 에 집중. binding constraint = fill-finish capacity + 협상가.
 • Wave 2 healthcare: 헬스케어 산업 전반의 구조적 병목. 약가/보험/병원
   labor/medical device reimbursement/AI 진단 등 sector-wide 동인.

Sector-level 핵심 forward signal:
 1) Medicare 협상 약가 + IRA Phase 2 (15개 약물, 2027 effective) — drug
    mfg ASP 압박, biosimilar 침투 가속.
 2) AI 진단 + LLM 환자 모니터링 — diagnostics 영역 disruption.
 3) GLP-1 spillover — 비만 cycle 이 obese-comorbidity 시장 (cardio/CKD/
    sleep apnea) 까지 확장 → med device (CGM/insulin pump) 수요 변형 +
    healthcare plan medical loss ratio 영향.
 4) Hospital labor shortage — RN/MD 임금 인상 → 병원 운영 마진 압박,
    private equity 인수 후 roll-up 가속.
 5) 美 entity list + China BIOSECURE — WuXi / Generic CMO 매출 KR 송도 +
    Lonza + Catalent 로 시프트 가속.
"""

from __future__ import annotations

THEME = {
    "domain": "Healthcare (Finviz Sector — Drug Mfg · Biotech · Devices · Diagnostics · Plans · Care)",
    "aliases": [
        "healthcare",
        "health_care",
        "health",
        "헬스케어",
        "의료",
        "의료기기",
        "medical",
        "medical_devices",
        "medical_device",
        "meddevice",
        "diagnostics",
        "biotech",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Drug Manufacturers — General (대형 pharma · GLP-1 정점)",
        "Drug Manufacturers — Specialty & Generic (biosimilar 침투)",
        "Biotechnology (clinical-stage · pre-commercial)",
        "Medical Devices (수술 로봇 · 인슐린 펌프 · 정형외과)",
        "Medical Instruments & Supplies (CGM · 진단 장비 소모품)",
        "Diagnostics & Research (분자진단 · LDT · NGS sequencing)",
        "Healthcare Plans (UNH/CI/HUM medical loss ratio)",
        "Medical Care Facilities (hospital · dialysis · ASC)",
        "Medical Distribution (MCK/ABC/CAH GLP-1 유통)",
        "Pharmaceutical Retailers (CVS/WBA prescription volume)",
        "Health Information Services (Teladoc · Hims · digital therapeutic)",
    ],
    "catalyst_types": [
        "FDA PDUFA + Advisory Committee 일정 (GLP-1 라벨 확장 + biosimilar 승인)",
        "Medicare 협상 약가 발표 (1차 10개 2026 effective → 2차 15개 2027)",
        "Healthcare Plans 분기 medical loss ratio + 연간 rate filing 변동",
        "Hospital REIT + private equity 인수 동향 + 병원 labor cost trend",
        "中国 BIOSECURE Act 후속 입법 + WuXi 매출 KR/EU 시프트 정량화",
        "Med device CMS reimbursement 변경 (CGM Medicare 커버리지 확장 등)",
        "AI 신약 발견 + AI 진단 라이선스 + DOJ price-fixing 조사",
        "GLP-1 spillover (cardio / CKD / sleep apnea / 알코올 의존) 임상 결과",
    ],
    "regional_concentration": {
        "Drug Mfg — Large": "US (Lilly LLY / Pfizer PFE / Merck MRK / Bristol Myers BMY / AbbVie ABBV / J&J JNJ), DK (Novo NVO), UK (AstraZeneca AZN / GSK GSK), CH (Novartis NOVN.SW / Roche ROG.SW), FR (Sanofi SNY)",
        "Drug Mfg — Specialty & Generic": "KR (셀트리온 068270.KS / 삼성바이오로직스 207940.KS — Samsung Bioepis), CH (Sandoz SDZ.SW), IL (Teva TEVA), IN (Biocon 532523.BO / Dr. Reddy RDY / Sun Pharma SUNPHARMA.NS), HU (Gedeon Richter GLDR.BU)",
        "Biotechnology": "US (Vertex VRTX / Regeneron REGN / Amgen AMGN / Gilead GILD / Biogen BIIB / Moderna MRNA / BioNTech BNTX / Alnylam ALNY / Incyte INCY / Sarepta SRPT)",
        "Medical Devices": "US (Intuitive Surgical ISRG — 수술 로봇 / Medtronic MDT / Stryker SYK / Boston Scientific BSX / Abbott ABT / Edwards Lifesciences EW / Zimmer Biomet ZBH / Becton Dickinson BDX), DE (Siemens Healthineers SHL.DE), JP (Olympus 7733.T / Terumo 4543.T)",
        "Medical Instruments & Supplies": "US (Danaher DHR / Thermo Fisher TMO / Agilent A / Insulet PODD — Omnipod / Dexcom DXCM — CGM), DK (Coloplast COLO-B.CO), CH (Sonova SOON.SW)",
        "Diagnostics & Research": "US (Illumina ILMN — NGS / IDEXX IDXX 동물진단 / Labcorp LH / Quest Diagnostics DGX / Exact Sciences EXAS / Natera NTRA / Veracyte VCYT), CH (Roche ROG.SW Dx 자회사)",
        "Healthcare Plans": "US (UnitedHealth UNH / Elevance ELV / Cigna CI / Humana HUM / CVS Health CVS Aetna / Molina MOH / Centene CNC)",
        "Medical Care Facilities": "US (HCA Holdings HCA / Universal Health UHS / Tenet THC / DaVita DVA dialysis / Ensign ENSG)",
        "Medical Distribution": "US (McKesson MCK / Cencora COR — 구 Amerisource / Cardinal Health CAH)",
        "Pharmaceutical Retailers": "US (CVS Health CVS / Walgreens WBA / Walmart Health WMT)",
        "Health Information Services / Digital Health": "US (Teladoc TDOC / Hims HIMS / Doximity DOCS / Veeva Systems VEEV / Schrödinger SDGR AI 신약), KR (라이프시맨틱스 347700.KQ)",
        "CDMO biologics": "KR (삼성바이오로직스 207940.KS — 글로벌 1위 mAb capacity), CH (Lonza LONN.SW), US (Catalent 비상장 — Novo 인수 / Thermo Fisher TMO), CN (WuXi Biologics 2269.HK BIOSECURE 영향)",
    },
}
