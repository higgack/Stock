"""Factory Automation — L4 under Machinery, Tools & Components (기계, 도구 및 부품).

자동 생성(부모 L3 machinery 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Factory Automation (Machinery, Tools & Components)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["factory"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Factory Automation — 주요 (Fanuc · Yaskawa)",
        "Factory Automation — 기타·공급망 (Keyence · Rockwell)",
    ],
    "catalyst_types": [
        "美 인프라 bill (CHIPS + IIJA) 집행률 → CAT/DE/PWR 매출 ramp",
        "美 농산물 가격 (옥수수·콩) + DE 분기 농기계 매출 가이드",
        "中国 14차 5개년 인프라 + 노후 단지 재개발 → SANY/Zoomlion 매출",
        "日 산업 로봇 분기 수주잔고 + 자동차 + 반도체 capex 사이클 회복",
        "美 reshoring + 멕시코 nearshoring → factory automation 신규 수요",
        "Mining capex 사이클 (구리·금·리튬) + Caterpillar mining 부문",
    ],
    "regional_concentration": {
        "Factory Automation (주요)": "Fanuc · Yaskawa",
        "Factory Automation (확장)": "Keyence · Rockwell",
    },
}
