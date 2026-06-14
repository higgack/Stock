"""Card Networks Duopoly — L4 under Consumer Finance (소비자 금융).

자동 생성(부모 L3 consumer_finance 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Card Networks Duopoly (Consumer Finance)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["card_networks", "card", "networks"],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Card Networks Duopoly — 주요 (Visa V · Mastercard MA)",
        "Card Networks Duopoly — 기타·공급망 (American Express AXP · Discover DFS (Capital One 인수 후))",
    ],
    "catalyst_types": [
        "美 신용카드 delinquency rate (Fed/NY) + 분기 charge-off",
        "Visa-Mastercard merchant 합의금 + Durbin 확대 (Bowman amendment) 표결",
        "AXP 분기 매출 + T&E 회복 + 영국·중국 inbound 카드 소비",
        "Stablecoin 결제 disrupt + USDC/USDT merchant 채택률 (Stripe/Shopify)",
        "美 30Y 모기지 금리 + 주택 거래량 + Rocket/UWM 분기 매출",
        "美 자동차 sub-prime 부실 + Ally 분기 net charge-off",
    ],
    "regional_concentration": {
        "Card Networks Duopoly (주요)": "Visa V · Mastercard MA",
        "Card Networks Duopoly (확장)": "American Express AXP · Discover DFS (Capital One 인수 후)",
    },
}
