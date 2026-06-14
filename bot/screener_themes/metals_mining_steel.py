"""Steel — L4 under Metals & Mining (금속 및 광업).

자동 생성(부모 L3 metals_mining 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Steel (Metals & Mining)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": [],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Steel — 주요 (Nucor NUE · Steel Dynamics STLD · Cleveland-Cliffs CLF · United States Steel X (Nippon Steel 인수 전) · Commercial Metals CMC · Worthington Steel WS)",
        "Steel — 기타·공급망 (Olympic Steel ZEUS · Alcoa AA · Century Aluminum CENX · Constellium CSTM · Kaiser Aluminum KALU · Reliance Steel RS)",
    ],
    "catalyst_types": [
        "美/EU CBAM (철강 · 알루미늄 · 시멘트) 효력 시점",
        "전동화 + 그리드 capex 사이클 (구리 / 알루미늄 / 리튬) supply gap",
        "中国 14차 5개년 rare earth 수출 통제 + 美 reshoring + 호주 Pilbara",
        "Au/Ag 가격 + 中国 PBoC 금 매입 + Fed 정책금리 (Au inverse 상관)",
        "철강 가격 (HRC spot) + 인프라 bill 시행 + 중국 부동산 회복",
        "리튬 spot (Pilbara SC6 + Chile SQM brine) + 니켈 LME 가격",
    ],
    "regional_concentration": {
        "Steel (주요)": "Nucor NUE · Steel Dynamics STLD · Cleveland-Cliffs CLF · United States Steel X (Nippon Steel 인수 전) · Commercial Metals CMC · Worthington Steel WS",
        "Steel (확장)": "Olympic Steel ZEUS · Alcoa AA · Century Aluminum CENX · Constellium CSTM · Kaiser Aluminum KALU · Reliance Steel RS",
    },
}
