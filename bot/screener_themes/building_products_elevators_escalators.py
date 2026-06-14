"""Elevators + Escalators — L4 under Building Products & Construction (건축 제품 및 건설).

자동 생성(부모 L3 building_products 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Elevators + Escalators (Building Products & Construction)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["elevators_escalators", "elevators", "escalators"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Elevators + Escalators — 일본 (US (Otis OTIS), CH (Schindler SCHN.SW), FI (KONE KNEBV.HE), JP (Hitachi 6501.T)",
        "Elevators + Escalators — 미국 (Mitsubishi Electric 6503.T))",
    ],
    "catalyst_types": [
        "HVAC R-454B 냉매 전환 (2025 effective) → AC replacement cycle 강제",
        "美 데이터센터 + 반도체 fab construction starts (CHIPS Act 시행)",
        "美 인프라 bill (IIJA) 도로·교량 capex 집행률 + 비행장 modernization",
        "美 주택 착공 + 30Y 모기지 금리 + 신규 단독주택 inventory",
        "EU 에너지 효율 직접 + heat pump 보조금 + 영국 grant scheme",
        "中国 14차 5개년 도시화 + 노후 단지 재개발 보조금",
    ],
    "regional_concentration": {
        "Elevators + Escalators (일본)": "US (Otis OTIS), CH (Schindler SCHN.SW), FI (KONE KNEBV.HE), JP (Hitachi 6501.T",
        "Elevators + Escalators (미국)": "Mitsubishi Electric 6503.T)",
    },
}
