"""Recycling + Recovery — L4 under Waste & Environmental Services (폐기물 및 환경 서비스).

자동 생성(부모 L3 waste_management 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Recycling + Recovery (Waste & Environmental Services)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["recycling_recovery", "recovery"],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Recycling + Recovery — 주요 (Casella)",
        "Recycling + Recovery — 기타·공급망 (Schnitzer · Stericycle)",
    ],
    "catalyst_types": [
        "美 EPA PFAS 'forever chemicals' 규제 + 인프라 bill 수처리 자금",
        "美 폐기물 가격 인상 (WM/RSG 분기 가이드) + collected volume",
        "EU CBAM + 기후 변화 의무 + 영국 plastic packaging tax",
        "데이터센터 + 반도체 fab 신규 site → 산업 폐기물 + 수처리 수요",
        "기후 재해 (허리케인 · 산불) 복구 → 환경 정화 매출",
        "中国 14차 5개년 재활용 의무 + 일본 PET·플라스틱 수출 통제",
    ],
    "regional_concentration": {
        "Recycling + Recovery (주요)": "Casella",
        "Recycling + Recovery (확장)": "Schnitzer · Stericycle",
    },
}
