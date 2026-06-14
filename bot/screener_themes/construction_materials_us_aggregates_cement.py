"""US Aggregates + Cement — L4 under Construction Materials (건축 자재).

자동 생성(부모 L3 construction_materials 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "US Aggregates + Cement (Construction Materials)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["aggregates_cement"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "US Aggregates + Cement — 미국 (Martin Marietta MLM · Vulcan Materials VMC · Eagle Materials EXP · Summit Materials SUM · Sterling Infrastructure STRL · Construction Partners ROAD · MasTec MTZ · Quanex Building NX)",
        "US Aggregates + Cement — 비상장 (USG 비상장 · US Concrete 비상장 (인수 후) · Continental Building Products 비상장)",
    ],
    "catalyst_types": [
        "美 IIJA 인프라 bill 집행률 + 신규 도로 + 비행장 modernization",
        "美 신규 단독주택 + 모기지 금리 + 분기 ready-mix 매출",
        "中国 부동산 위기 회복 + 14차 5개년 도시화 + 부동산 보조금",
        "EU CBAM 효력 시점 + EU 건축 자재 가격 변동",
        "美 데이터센터 + 반도체 fab construction starts (CHIPS Act)",
        "Eagle Materials 분기 wallboard 매출 + 시멘트 가격 인상 사이클",
    ],
    "regional_concentration": {
        "US Aggregates + Cement (미국)": "Martin Marietta MLM · Vulcan Materials VMC · Eagle Materials EXP · Summit Materials SUM · Sterling Infrastructure STRL · Construction Partners ROAD · MasTec MTZ · Quanex Building NX",
        "US Aggregates + Cement (비상장)": "USG 비상장 · US Concrete 비상장 (인수 후) · Continental Building Products 비상장",
    },
}
