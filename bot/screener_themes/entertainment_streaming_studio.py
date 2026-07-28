"""Streaming + Studio — L4 under Entertainment (엔터테인먼트).

자동 생성(부모 L3 entertainment 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Streaming + Studio (Entertainment)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["streaming_studio", "studio"],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Streaming + Studio — 주요 (Netflix NFLX · Walt Disney DIS (Disney+ · Hulu · ESPN+) · Warner Bros Discovery WBD (HBO Max)",
        "Streaming + Studio — 기타·공급망 (CNN) · Paramount Global PARA (Paramount+ · CBS) · Fox Corporation FOXA · Lionsgate LION (Starz) · AMC Networks AMCX)",
    ],
    "catalyst_types": [
        "Netflix 분기 구독자 + 광고 tier 침투 + ARPU + churn",
        "Disney+ 수익성 분기 회복 + Hulu 통합 + ESPN streaming launch",
        "Spotify 분기 podcast 매출 + 광고 tier + Joe Rogan 계약 갱신",
        "Live Nation/TKO 분기 ticket sales + 美 sports streaming rights (WWE · UFC)",
        "Korean Entertainment K-pop 글로벌 + 분기 BTS/Blackpink/NewJeans 매출",
        "Movie Theater 분기 box office + 분기 신작 lineup (Disney · Universal)",
    ],
    "regional_concentration": {
        "Streaming + Studio (주요)": "Netflix NFLX · Walt Disney DIS (Disney+ · Hulu · ESPN+) · Warner Bros Discovery WBD (HBO Max",
        "Streaming + Studio (확장)": "CNN) · Paramount Global PARA (Paramount+ · CBS) · Fox Corporation FOXA · Lionsgate LION (Starz) · AMC Networks AMCX",
    },
}
