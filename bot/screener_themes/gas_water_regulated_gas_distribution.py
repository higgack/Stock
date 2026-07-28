"""Regulated Gas Distribution — L4 under Gas & Water Utilities (가스 및 수도 유틸리티).

자동 생성(부모 L3 gas_water 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Regulated Gas Distribution (Gas & Water Utilities)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["regulated_distribution"],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Regulated Gas Distribution — 미국 (Atmos Energy ATO · NiSource NI · Spire Inc SR · Southwest Gas SWX · UGI UGI · ONE Gas OGS · New Jersey Resources NJR · Northwest Natural NWE)",
        "Regulated Gas Distribution — 비상장 (South Jersey Industries SJI (인수 후 비상장) · WGL Holdings 비상장 (인수 후) · NW Natural 비상장)",
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
        "Regulated Gas Distribution (미국)": "Atmos Energy ATO · NiSource NI · Spire Inc SR · Southwest Gas SWX · UGI UGI · ONE Gas OGS · New Jersey Resources NJR · Northwest Natural NWE · National Fuel Gas NFG",
        "Regulated Gas Distribution (비상장)": "South Jersey Industries SJI (인수 후 비상장) · WGL Holdings 비상장 (인수 후) · NW Natural 비상장",
    },
}
