"""Diversified + Specialty — L4 under Chemicals (화학).

자동 생성(부모 L3 chemicals 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Diversified + Specialty (Chemicals)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": [],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Diversified + Specialty — 주요 (Sherwin-Williams SHW · PPG Industries PPG · DuPont DD · Eastman Chemical EMN · Celanese CE · Olin OLN · International Flavors IFF · Ecolab ECL)",
        "Diversified + Specialty — 기타·공급망 (Univar Solutions UNVR (인수 후) · Avient AVNT · NewMarket NEU · Element Solutions ESI · Quaker Houghton KWR · Innospec IOSP · Stepan SCL · Cabot CBT)",
    ],
    "catalyst_types": [
        "美 IRA 45X (battery cathode/anode/cell) + 분기 PFAS 규제 + EU CBAM",
        "리튬 · 코발트 · 니켈 가격 사이클 → 배터리 소재 매출 직접",
        "美 farm bill 통과 + 비료 (질소 · 인산 · 칼륨) 가격 사이클",
        "분기 ethylene + propylene + benzene 가격 spread + 美 멕시코만 hurricane risk",
        "中国 반덤핑 (multi-c polysilicon · 유리 · titanium dioxide) 효력 시점",
        "美 HBM/CoWoS 패키지 capex → 특수가스 + 포토레지스트 + 슬러리 수요",
    ],
    "regional_concentration": {
        "Diversified + Specialty (주요)": "Sherwin-Williams SHW · PPG Industries PPG · DuPont DD · Eastman Chemical EMN · Celanese CE · Olin OLN · International Flavors IFF · Ecolab ECL · Westlake Chemical WLK · Huntsman HUN",
        "Diversified + Specialty (확장)": "Univar Solutions UNVR (인수 후) · Avient AVNT · NewMarket NEU · Element Solutions ESI · Quaker Houghton KWR · Innospec IOSP · Stepan SCL · Cabot CBT · Tronox TROX · Chemours CC · GCP Applied Tech GCP",
    },
}
