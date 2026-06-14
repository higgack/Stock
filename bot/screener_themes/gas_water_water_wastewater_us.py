"""Water/Wastewater US — L4 under Gas & Water Utilities (가스 및 수도 유틸리티).

자동 생성(부모 L3 gas_water 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Water/Wastewater US (Gas & Water Utilities)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["water_wastewater", "wastewater"],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Water/Wastewater US — 주요 (American Water Works AWK · Essential Utilities WTRG (구 Aqua America) · California Water CWT · American States Water AWR · SJW Group SJW)",
        "Water/Wastewater US — 기타·공급망 (Middlesex Water MSEX · York Water YORW · Artesian Resources ARTNA · Consolidated Water CWCO · Global Water Resources GWRS · Pure Cycle PCYO)",
    ],
    "catalyst_types": [
        "Fed funds rate path → 차입 비용 변화 + 분기 배당 매력",
        "美 EPA PFAS 'forever chemicals' 규제 + 인프라 bill 수처리 자금",
        "데이터센터 + 반도체 fab 신규 site → 수처리 수요 + 폐수 처리 capex",
        "美 IIJA 수자원 인프라 자금 (Lead Service Line replacement)",
        "분기 PUC 가격 인상 승인 + 신규 자본 capex plan",
        "EU CO2 가격 + 가스 → 전기 전환 + UK Heat Pump 보조금",
    ],
    "regional_concentration": {
        "Water/Wastewater US (주요)": "American Water Works AWK · Essential Utilities WTRG (구 Aqua America) · California Water CWT · American States Water AWR · SJW Group SJW",
        "Water/Wastewater US (확장)": "Middlesex Water MSEX · York Water YORW · Artesian Resources ARTNA · Consolidated Water CWCO · Global Water Resources GWRS · Pure Cycle PCYO",
    },
}
