"""Health Care — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (3):
 1. Pharmaceuticals & Biotechnology
 2. Health Care Equipment & Supplies
 3. Health Care Providers & Services

Wave 1 trend `pharma.py` (좁은 GLP-1/CDMO/Biosimilar cycle) 와는 별개
lens — 본 모듈은 헬스케어 sector 전체. Med Devices · Diagnostics · Health
Insurance · Hospital · Drug Distribution · Animal Health · Digital Health
까지 cover. 다국적 종목 분포 (US/KR/JP/CH/CN/EU) 명시.
"""

from __future__ import annotations

THEME = {
    "domain": "Health Care (헬스케어)",
    "layer": "L2_SECTOR",
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
        "Pharmaceuticals & Biotechnology (제약 및 바이오테크)",
        "Health Care Equipment & Supplies (의료 장비 및 소모품)",
        "Health Care Providers & Services (의료 서비스)",
    ],
    "catalyst_types": [
        "FDA PDUFA + Advisory Committee 일정 (GLP-1 라벨 확장 + biosimilar 승인 + 임상 3상 readout)",
        "Medicare 협상 약가 발표 (1차 10개 2026 effective → 2차 15개 2027) + IRA Phase 2",
        "Healthcare Plans 분기 medical loss ratio + 연간 rate filing 변동",
        "Hospital REIT + private equity 인수 동향 + 병원 labor cost trend",
        "中国 BIOSECURE Act 후속 입법 + WuXi 매출 KR/EU 시프트 정량화",
        "Med device CMS reimbursement 변경 (CGM Medicare 커버리지 확장 등)",
        "GLP-1 spillover (cardio / CKD / sleep apnea / 알코올 의존) 임상 결과",
        "AI 신약 발견 + AI 진단 라이선스 + DOJ price-fixing 조사 + DOJ-FTC 인수합병 차단",
    ],
    "regional_concentration": {
        "Pharmaceuticals & Biotechnology": "US Big Pharma (Johnson & Johnson JNJ · Eli Lilly LLY · Merck MRK · Pfizer PFE · AbbVie ABBV · Bristol-Myers Squibb BMY), EU (Novartis NOVN.SW · Sanofi SNY · AstraZeneca AZN · GlaxoSmithKline GSK · Roche ROG.SW), DK (Novo Nordisk NVO), Biotech US (Amgen AMGN · Gilead GILD · Vertex VRTX · Regeneron REGN · Moderna MRNA · Biogen BIIB · BioNTech BNTX · Sarepta SRPT · Jazz JAZZ), KR (셀트리온 068270.KS · 한미약품 128940.KS · 유한양행 000100.KS · 종근당 185750.KS · 한올바이오파마 009420.KS), JP (Takeda 4502.T · Astellas 4503.T · Daiichi Sankyo 4568.T · Chugai 4519.T)",
        "Health Care Equipment & Supplies": "Medical Devices US (Medtronic MDT · Abbott ABT · Stryker SYK · Boston Scientific BSX · Edwards EW · Intuitive Surgical ISRG · BDX BDX · Zimmer Biomet ZBH · Align ALGN · Insulet PODD · DexCom DXCM), DE (Siemens Healthineers SHL.DE), JP (Olympus 7733.T · Terumo 4543.T · Sysmex 6869.T · HOYA 7741.T), KR (오스템임플란트 048260.KQ · 클래시스 214150.KQ · 루닛 328130.KQ), Life Science Tools (Thermo Fisher TMO · Danaher DHR · Agilent A · Illumina ILMN · Dentsply XRAY · Alcon ALC · Cooper COO)",
        "Health Care Providers & Services": "Managed Health Care US (UnitedHealth UNH · Elevance ELV · Cigna CI · Humana HUM · Centene CNC · Molina MOH), Hospitals US (HCA HCA · UHS UHS · Tenet THC · Encompass EHC · Brookdale BKD) + KR (삼성서울병원 비상장 · 서울아산 비상장), Diagnostics (IQVIA IQV · Quest DGX · Labcorp LH · Charles River CRL), Medical Distribution (McKesson MCK · Cencora COR · Cardinal CAH), Animal Health (Zoetis ZTS · IDEXX IDXX · Idexx IDXX · Elanco ELAN), Health Info Services (Veeva VEEV · Doximity DOCS · Teladoc TDOC · Hims HIMS), CDMO biologics (KR 삼성바이오 207940.KS · CH Lonza LONN.SW · CN WuXi Biologics 2269.HK)",
    },
}
