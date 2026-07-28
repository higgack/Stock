"""Korea + Asia — L4 under Tobacco (담배).

자동 생성(부모 L3 tobacco 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Korea + Asia (Tobacco)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["korea_asia"],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Korea + Asia — 미국 (KT&G 033780.KS (담배 + 인삼) · ITC Limited ITC.NS (인도 - 담배 + 호텔 + FMCG))",
        "Korea + Asia — 비상장 (China Tobacco 비상장 (中国烟草总公司 — 비상장 국유) · 上海烟草 비상장 · Wuhan Tobacco 비상장)",
        "Korea + Asia — 일본 (JT (Japan Tobacco) 2914.T)",
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
        "Korea + Asia (미국)": "KT&G 033780.KS (담배 + 인삼) · ITC Limited ITC.NS (인도 - 담배 + 호텔 + FMCG)",
        "Korea + Asia (비상장)": "China Tobacco 비상장 (中国烟草总公司 — 비상장 국유) · 上海烟草 비상장 · Wuhan Tobacco 비상장",
        "Korea + Asia (일본)": "JT (Japan Tobacco) 2914.T",
    },
}
