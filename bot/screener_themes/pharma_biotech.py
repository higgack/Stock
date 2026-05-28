"""Pharmaceuticals & Biotechnology — L3 under Health Care.

L1 trend `pharma.py` (좁은 GLP-1/CDMO/Biosimilar cycle) 와는 별개 lens
— 본 모듈은 제약 + 바이오테크 industry 전체 (대형 제약 + 임상 단계
바이오 + 차세대 modality). 임상 결과 + Medicare 협상 약가 + FDA PDUFA
+ 中国 BIOSECURE 후 CDMO 시프트.
"""

from __future__ import annotations

THEME = {
    "domain": "Pharmaceuticals & Biotechnology (제약 및 바이오테크)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "pharma_biotech",
        "major_pharma",
        "대형제약",
        "big_pharma",
        "biotech_l3",
        "바이오테크_l3",
        "drug_manufacturers",
        "신약",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Major Pharmaceuticals (대형 제약 — Lilly · Novo · Merck · Pfizer · AbbVie)",
        "Biotechnology (Amgen · Gilead · Vertex · Regeneron · Moderna · BioNTech)",
        "Specialty & Generic Pharma (Sandoz · Teva · 셀트리온 · Biocon)",
        "Cell & Gene Therapy (Vertex · CRISPR · BioMarin · Sarepta · Solid)",
        "AI Drug Discovery (Recursion · Schrödinger · Insilico)",
        "ADC (Antibody-Drug Conjugate) (Daiichi Sankyo · Seagen-Pfizer · 알테오젠 · 리가켐바이오)",
    ],
    "catalyst_types": [
        "FDA PDUFA + Advisory Committee + Phase 3 readout (GLP-1 라벨 확장)",
        "Medicare 협상 약가 발표 (1차 10개 2026 effective → 2차 15개 2027)",
        "GLP-1 매출 가이드 (Mounjaro / Wegovy / Zepbound) + cardio/CKD 라벨",
        "中国 BIOSECURE Act 통과 일정 → CDMO 시프트 (한국·EU 매출 ramp)",
        "EU EMA CHMP 의견 + 영국 NICE HTA 승인 + 일본 후생노동성 가이드",
        "AI 신약 발견 + Big Pharma 라이선스 계약 + DeepMind/Recursion 임상",
    ],
    "regional_concentration": {
        "US Major Pharma": "Johnson & Johnson JNJ · Eli Lilly LLY · Merck MRK · Pfizer PFE · AbbVie ABBV · Bristol-Myers BMY",
        "EU Major Pharma": "Novartis NOVN.SW · Sanofi SNY · AstraZeneca AZN · GlaxoSmithKline GSK · Roche ROG.SW · Bayer BAYN.DE · Novo Nordisk NVO (DK)",
        "US Biotech": "Amgen AMGN · Gilead GILD · Vertex VRTX · Regeneron REGN · Moderna MRNA · Biogen BIIB · BioNTech BNTX · Sarepta SRPT · Jazz JAZZ · Alnylam ALNY · Incyte INCY · BeiGene BGNE · Exelixis EXEL · Halozyme HALO · CRISPR CRSP · BioMarin BMRN",
        "Korea Pharma + Biotech": "삼성바이오로직스 207940.KS · 셀트리온 068270.KS · 한미약품 128940.KS · 유한양행 000100.KS · 종근당 185750.KS · 한올바이오파마 009420.KS · 알테오젠 196170.KQ · 리가켐바이오 141080.KQ · 에이비엘바이오 298380.KQ · 메디톡스 086900.KQ · 휴젤 145020.KQ",
        "Japan Pharma": "Takeda 4502.T · Astellas 4503.T · Daiichi Sankyo 4568.T · Chugai 4519.T (Roche 자회사) · Eisai 4523.T · Otsuka 4578.T · Shionogi 4507.T",
        "China + India": "WuXi Biologics 2269.HK · WuXi AppTec 2359.HK · Hengrui 600276.SS · BeiGene BGNE · Innovent 1801.HK · Sino Biopharm 1177.HK · 3SBio 1530.HK, IN (Sun Pharma SUNPHARMA.NS · Dr. Reddy's RDY · Cipla CIPLA.NS · Biocon 532523.BO · Zydus ZYDUSLIFE.NS)",
        "Specialty & Generic": "Teva TEVA · Viatris VTRS · Bausch BHC · Endo 비상장 · Mallinckrodt 비상장 · Perrigo PRGO · Sandoz SDZ.SW",
    },
}
