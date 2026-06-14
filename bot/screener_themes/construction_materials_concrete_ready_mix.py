"""Concrete + Ready-mix — L4 under Construction Materials (건축 자재).

자동 생성(부모 L3 construction_materials 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Concrete + Ready-mix (Construction Materials)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["concrete_ready", "ready"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Concrete + Ready-mix — 비상장 (USG 비상장 · Continental Building Products 비상장)",
        "Concrete + Ready-mix — 미국 (GMS GMS · Eagle Materials EXP · Boise Cascade BCC · Builders FirstSource BLDR · Quanex Building NX · Mueller Industries MLI · MDU Resources MDU)",
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
        "Concrete + Ready-mix (비상장)": "USG 비상장 · Continental Building Products 비상장",
        "Concrete + Ready-mix (미국)": "GMS GMS · Eagle Materials EXP · Boise Cascade BCC · Builders FirstSource BLDR · Quanex Building NX · Mueller Industries MLI · MDU Resources MDU",
    },
}
