"""Biotechnology — L4 under Pharmaceuticals & Biotechnology (제약 및 바이오테크).

자동 생성(부모 L3 pharma_biotech 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Biotechnology (Pharmaceuticals & Biotechnology)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["biotechnology"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Biotechnology — 주요 (Amgen · Gilead · Vertex)",
        "Biotechnology — 기타·공급망 (Regeneron · Moderna · BioNTech)",
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
        "Biotechnology (주요)": "Amgen · Gilead · Vertex",
        "Biotechnology (확장)": "Regeneron · Moderna · BioNTech",
    },
}
