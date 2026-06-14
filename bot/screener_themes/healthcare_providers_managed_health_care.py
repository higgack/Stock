"""Managed Health Care — L4 under Health Care Providers & Services (의료 서비스).

자동 생성(부모 L3 healthcare_providers 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Managed Health Care (Health Care Providers & Services)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["managed_health", "care"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Managed Health Care — 미국 (UnitedHealth UNH · Elevance Health ELV · Cigna CI · Humana HUM · CVS Health CVS · Centene CNC · Molina MOH · Oscar Health OSCR)",
        "Managed Health Care — 비상장 (Bright Health 비상장)",
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
        "Managed Health Care (미국)": "UnitedHealth UNH · Elevance Health ELV · Cigna CI · Humana HUM · CVS Health CVS · Centene CNC · Molina MOH · Oscar Health OSCR · Alignment Healthcare ALHC",
        "Managed Health Care (비상장)": "Bright Health 비상장",
    },
}
