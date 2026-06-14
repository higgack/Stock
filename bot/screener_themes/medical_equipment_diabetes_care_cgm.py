"""Diabetes Care + CGM — L4 under Health Care Equipment & Supplies (의료 장비 및 소모품).

자동 생성(부모 L3 medical_equipment 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Diabetes Care + CGM (Health Care Equipment & Supplies)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["diabetes_care", "diabetes"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Diabetes Care + CGM — 주요 (Insulet PODD (Omnipod) · Dexcom DXCM (CGM #1))",
        "Diabetes Care + CGM — 기타·공급망 (Tandem TNDM · Medtronic MDT · Abbott ABT (Libre CGM #2))",
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
        "Diabetes Care + CGM (주요)": "Insulet PODD (Omnipod) · Dexcom DXCM (CGM #1)",
        "Diabetes Care + CGM (확장)": "Tandem TNDM · Medtronic MDT · Abbott ABT (Libre CGM #2)",
    },
}
