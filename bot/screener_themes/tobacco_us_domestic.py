"""US Domestic — L4 under Tobacco (담배).

자동 생성(부모 L3 tobacco 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "US Domestic (Tobacco)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["domestic"],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "US Domestic — 미국 (Philip Morris International PM (IQOS 글로벌 + NJOY) · Altria MO (Marlboro US · NJOY 합병 후) · British American Tobacco BTI (Vuse · glo · Velo) · Japan Tobacco 2914.T (Camel · Winston)",
        "US Domestic — 유럽 (Imperial Brands IMB.L)",
        "US Domestic — 비상장 (Reynolds American 비상장 (BAT 자회사))",
    ],
    "catalyst_types": [
        "美 FDA menthol ban 시행 + PMTA (Vape) 신규 승인 일정",
        "Philip Morris IQOS 미국 진출 (NJOY 인수 후) + 분기 매출 가이드",
        "British American Tobacco Vuse 분기 매출 + EU 일회용 vape 규제",
        "美 ZYN/Velo nicotine pouch 매출 폭증 + 청소년 흡연 통계",
        "中国 담배 산업 (China Tobacco 비상장) 价格 인상 + 한국 KT&G 수출",
        "EU 새로운 담배 세금 + tobacco product directive 개정",
    ],
    "regional_concentration": {
        "US Domestic (미국)": "Philip Morris International PM (IQOS 글로벌 + NJOY) · Altria MO (Marlboro US · NJOY 합병 후) · British American Tobacco BTI (Vuse · glo · Velo) · Japan Tobacco 2914.T (Camel · Winston · Ploom) · Vector Group VGR · Universal Corporation UVV (담배 거래 leaf)",
        "US Domestic (유럽)": "Imperial Brands IMB.L",
        "US Domestic (비상장)": "Reynolds American 비상장 (BAT 자회사)",
    },
}
