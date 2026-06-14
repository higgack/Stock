"""Mega-cap Regulated — L4 under Electric & Multi-Utilities (전력 및 복합 유틸리티).

자동 생성(부모 L3 electric_utility 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Mega-cap Regulated (Electric & Multi-Utilities)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["mega_regulated"],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Mega-cap Regulated — 주요 (NextEra Energy NEE · Duke Energy DUK · Southern Company SO · Dominion Energy D · American Electric Power AEP)",
        "Mega-cap Regulated — 기타·공급망 (Exelon EXC · Sempra Energy SRE · Public Service Enterprise PEG · Xcel Energy XEL · Constellation Energy CEG (분리 후))",
    ],
    "catalyst_types": [
        "Fed funds rate path → Utility 배당 매력 + 차입 비용 변화",
        "美 AI 데이터센터 전력 수요 (24/7 24GW+ by 2030) → IPP 매출 ramp",
        "Texas ERCOT + California CAISO heatwave + 송전망 신규 line approval",
        "美 NRC SMR 인허가 (NuScale · X-energy · Oklo · TerraPower) + 기존 원전 수명 연장",
        "분기 PUC 가격 인상 승인 + 새 capex plan 발표",
        "Trump 행정명령 - 美 송전망 + grid modernization 부활",
    ],
    "regional_concentration": {
        "Mega-cap Regulated (주요)": "NextEra Energy NEE · Duke Energy DUK · Southern Company SO · Dominion Energy D · American Electric Power AEP",
        "Mega-cap Regulated (확장)": "Exelon EXC · Sempra Energy SRE · Public Service Enterprise PEG · Xcel Energy XEL · Constellation Energy CEG (분리 후)",
    },
}
