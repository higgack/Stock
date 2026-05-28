"""Health Care Providers & Services — L3 under Health Care.

Managed Care + Hospitals + Distribution + Diagnostics + Animal Health +
Digital Health. UNH/ELV 분기 medical loss ratio + Medicare Advantage
가입자 + 병원 labor cost가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Health Care Providers & Services (의료 서비스)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "healthcare_providers",
        "health_insurance",
        "건강보험",
        "managed_care",
        "병원",
        "hospital",
        "medical_distribution",
        "의약품유통",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Managed Health Care (UNH · ELV · CI · HUM · CVS · MOH · CNC)",
        "Hospitals & Care Facilities (HCA · UHS · THC · Encompass)",
        "Diagnostics & Research / CRO (IQVIA · Quest · Labcorp · Charles River)",
        "Medical Distribution (McKesson · Cencora · Cardinal)",
        "Animal Health + Diagnostics (Zoetis · IDEXX · Idexx · Elanco)",
        "Digital Health + Telehealth (Veeva · Doximity · Teladoc · Hims)",
    ],
    "catalyst_types": [
        "Managed Care 분기 medical loss ratio + 연간 rate filing 변동",
        "Medicare Advantage 분기 가입자 + CMS Star Ratings",
        "병원 labor cost (RN/MD 임금 인상) + private equity 인수 + roll-up 가속",
        "美 IRA Phase 2 (15개 약물) + Medicare 협상 약가 → Distribution 마진 영향",
        "Animal Health 분기 매출 (반려동물 + 농장) + 사료 가격 사이클",
        "GLP-1 + Ozempic 처방 분기 증가 + 비만 외래 환자 증가",
    ],
    "regional_concentration": {
        "Managed Health Care": "UnitedHealth UNH · Elevance Health ELV · Cigna CI · Humana HUM · CVS Health CVS · Centene CNC · Molina MOH · Oscar Health OSCR · Alignment Healthcare ALHC · Bright Health 비상장",
        "Hospitals + Care Facilities": "HCA Healthcare HCA · Universal Health Services UHS · Tenet Healthcare THC · Community Health CYH · Encompass Health EHC · Brookdale Senior Living BKD · Acadia Healthcare ACHC · LifeStance LFST · Ensign ENSG · DaVita DVA · Fresenius Medical Care FMS · Pediatrix MD MD",
        "Diagnostics + CRO": "IQVIA IQV · Quest Diagnostics DGX · Laboratory Corp LH · Charles River CRL · Medpace MEDP · ICON ICLR · Syneos 비상장 (Goldman 인수) · PRA Health 비상장 · CRO Korea (씨엔알리서치 비상장)",
        "Medical Distribution": "McKesson MCK · Cencora COR (구 AmerisourceBergen) · Cardinal Health CAH · Owens & Minor OMI · Patterson Cos PDCO · Henry Schein HSIC · Premier PINC",
        "Animal Health": "Zoetis ZTS · IDEXX Laboratories IDXX · Elanco Animal Health ELAN · Phibro PAHC · Heska 비상장 (인수 후) · Virbac VIRP.PA · Dechra Pharma 비상장",
        "Digital Health": "Veeva Systems VEEV · Doximity DOCS · Teladoc TDOC · Hims & Hers HIMS · Schrödinger SDGR · Amwell AMWL · GoodRx GDRX · 23andMe ME · Privia Health PRVA · Phreesia PHR",
        "Korea + Asia": "유진오엔지 비상장 (병원 컨설팅) · 휴마시스 205470.KQ (체외진단) · 씨젠 096530.KQ · 마크로젠 038290.KQ · 휴롬 비상장 · 라이프시맨틱스 347700.KQ · 케어랩스 263700.KQ, JP (Eisai 4523.T · M3 2413.T digital health · Recruit 6098.T)",
    },
}
