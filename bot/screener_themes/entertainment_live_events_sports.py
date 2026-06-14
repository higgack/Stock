"""Live Events + Sports — L4 under Entertainment (엔터테인먼트).

자동 생성(부모 L3 entertainment 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Live Events + Sports (Entertainment)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["live_events", "live", "events"],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Live Events + Sports — 미국 (Live Nation LYV · MSG Sports MSGS · MSG Entertainment MSGE · Sphere Entertainment SPHR · Madison Square Garden Networks (MSGN — Charter 자회사) · TKO Group TKO (WWE · UFC 합병) · Funko FNKO (collectibles))",
        "Live Events + Sports — 비상장 (Endeavor 비상장 (TKO IPO 후 합병))",
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
        "Live Events + Sports (미국)": "Live Nation LYV · MSG Sports MSGS · MSG Entertainment MSGE · Sphere Entertainment SPHR · Madison Square Garden Networks (MSGN — Charter 자회사) · TKO Group TKO (WWE · UFC 합병) · Funko FNKO (collectibles)",
        "Live Events + Sports (비상장)": "Endeavor 비상장 (TKO IPO 후 합병)",
    },
}
