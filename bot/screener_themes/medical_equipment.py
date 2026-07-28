"""Health Care Equipment & Supplies — L3 under Health Care.

Medical Devices (수술 로봇 · 인슐린 펌프 · CGM · 정형외과 · 심혈관) +
Life Science Tools (Thermo · Danaher · Illumina). CMS reimbursement 변경
+ FDA 510(k) 승인 + GLP-1 spillover (CGM 수요 변화) 가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Health Care Equipment & Supplies (의료 장비 및 소모품)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "medical_equipment",
        "medical_devices_l3",
        "의료기기_l3",
        "cgm",
        "수술로봇_l3",
        "life_science_tools",
        "생명과학도구",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Surgical Devices + Robotics (Intuitive · Stryker MAKO · Globus · Vicarious)",
        "Cardiovascular (Boston Scientific · Edwards · Abbott · Medtronic)",
        "Orthopedic Implants (Stryker · Zimmer · Smith+Nephew)",
        "Diabetes Care + CGM (Insulet · Dexcom · Tandem · Medtronic)",
        "Diagnostic Imaging + Lab Tools (Thermo · Danaher · Agilent · Illumina)",
        "Dental + Vision (Align · Henry Schein · Cooper · Alcon)",
    ],
    "catalyst_types": [
        "FDA 510(k) + PMA 승인 일정 (수술 로봇 + CGM 신제품)",
        "CMS reimbursement 변경 (CGM Medicare 커버리지 확장 + TAVR/Watchman 코드)",
        "GLP-1 spillover (Mounjaro/Wegovy → insulin pump 수요 변화) 정량화",
        "ISRG 분기 수술 case volume + 신규 로봇 매출 ramp",
        "中国 VBP (Volume-Based Procurement) round 입찰 → 정형외과 단가 압박",
        "Genomic sequencing 가격 (Illumina NovaSeq X) + 단일세포 sequencing 수요",
    ],
    "regional_concentration": {
        "Surgical Devices + Robotics": "Intuitive Surgical ISRG (글로벌 #1 수술 로봇) · Stryker SYK (MAKO orthopedic robot) · Boston Scientific BSX · Globus Medical GMED · Penumbra PEN · Mevion 비상장 · Vicarious 비상장",
        "Cardiovascular": "Edwards Lifesciences EW (TAVR) · Boston Scientific BSX (Watchman) · Abbott ABT (MitraClip) · Medtronic MDT · Inari Medical NARI · Procept ProGo PRCT · Shockwave Medical SWAV",
        "Orthopedic + Implants": "Stryker SYK · Zimmer Biomet ZBH · Smith+Nephew SNN · Globus Medical GMED · NuVasive 비상장 · Integra LifeSciences IART · Envista NVST · Henry Schein HSIC",
        "Diabetes + CGM": "Insulet PODD (Omnipod) · Dexcom DXCM (CGM #1) · Tandem TNDM · Medtronic MDT · Abbott ABT (Libre CGM #2)",
        "Diagnostic Imaging + Lab Tools": "Thermo Fisher TMO · Danaher DHR · Agilent A · Illumina ILMN · Becton BDX · PerkinElmer PKI · Bruker BRKR · Waters WAT · Mettler-Toledo MTD · Bio-Rad BIO · Bio-Techne TECH · Quanterix QTRX · Veracyte VCYT · Natera NTRA · Exact Sciences EXAS · 10x Genomics TXG · Pacific Biosciences PACB · Roche ROG.SW · Sysmex 6869.T",
        "Dental + Vision": "Align Technology ALGN (Invisalign) · Henry Schein HSIC · Patterson Cos PDCO · Dentsply Sirona XRAY · Alcon ALC · Cooper Companies COO · Bausch + Lomb BLCO · EssilorLuxottica EL.PA · Hoya 7741.T",
        "Korea + Japan + Asia": "오스템임플란트 048260.KQ · 클래시스 048260.KQ · 루닛 328130.KQ · 메디톡스 086900.KQ · 휴젤 145020.KQ · 파마리서치 214450.KQ · JP (Olympus 7733.T · Terumo 4543.T · Sysmex 6869.T · HOYA 7741.T · Nipro 8086.T · Asahi Intecc 7747.T) · DE (Siemens Healthineers SHL.DE)",
    },
}
