"""China Cement — L4 under Construction Materials (건축 자재).

자동 생성(부모 L3 construction_materials 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "China Cement (Construction Materials)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["china_cement"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "China Cement — 홍콩 (Anhui Conch 0914.HK · CNBM 3323.HK · West China Cement 2233.HK)",
        "China Cement — 중국 (Huaxin Cement 600801.SS · Tianshan Cement 000877.SZ · Jidong Cement 000401.SZ)",
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
        "China Cement (홍콩)": "Anhui Conch 0914.HK · CNBM 3323.HK · West China Cement 2233.HK",
        "China Cement (중국)": "Huaxin Cement 600801.SS · Tianshan Cement 000877.SZ · Jidong Cement 000401.SZ",
    },
}
