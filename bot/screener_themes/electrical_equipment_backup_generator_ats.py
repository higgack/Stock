"""Backup Generator + ATS — L4 under Electrical Equipment (전력기기).

자동 생성(부모 L3 electrical_equipment 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Backup Generator + ATS (Electrical Equipment)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["backup_generator", "backup", "generator"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Backup Generator + ATS — 주요 (Generac)",
        "Backup Generator + ATS — 기타·공급망 (Cummins · Eaton)",
    ],
    "catalyst_types": [
        "AI 데이터센터 전력 capex 가이드 (MSFT/META/AWS/Google) + 24/7 24GW+ by 2030",
        "美 ISO interconnection queue (CAISO · ERCOT · PJM) + 송전망 신규 line approval",
        "변압기 리드타임 (현재 100-128주) → capacity 증설 발표 (Eaton · HD현대일렉 신규 공장)",
        "EU RePowerEU + 독일 grid stage 인허가 + UK Nationals Grid capex",
        "中国 14차 5개년 power grid + UHV (Ultra-High Voltage) DC 신규 line",
        "HVAC + EV 전동화 → low/mid voltage 배전반 + smart meter 수요",
    ],
    "regional_concentration": {
        "Backup Generator + ATS (주요)": "Generac",
        "Backup Generator + ATS (확장)": "Cummins · Eaton",
    },
}
