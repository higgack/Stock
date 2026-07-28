"""Asia Gas + Water — L4 under Gas & Water Utilities (가스 및 수도 유틸리티).

자동 생성(부모 L3 gas_water 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Asia Gas + Water (Gas & Water Utilities)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["asia_water"],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Asia Gas + Water — 일본 (JP (Tokyo Gas 9531.T · Osaka Gas 9532.T · Toho Gas 9533.T · Shizuoka Gas 9543.T · Saibu Gas 9536.T)",
        "Asia Gas + Water — 한국 (Nippon Gas 8174.T - LP gas), KR (한국가스공사 036460.KS · 삼천리 004690.KS · 서울가스 017390.KS · 부산가스 015350.KS · S-Oil 010950.KS)",
        "Asia Gas + Water — 비상장 (경동가스 비상장 · 한국전력 015760.KS), CN (Beijing Gas 비상장)",
        "Asia Gas + Water — 중국 (Shanghai Gas 600133.SS)",
        "Asia Gas + Water — 홍콩 (Sinopec Kantons 0934.HK · ENN Energy 2688.HK · Kunlun Energy 0135.HK)",
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
        "Asia Gas + Water (일본)": "JP (Tokyo Gas 9531.T · Osaka Gas 9532.T · Toho Gas 9533.T · Shizuoka Gas 9543.T · Saibu Gas 9536.T",
        "Asia Gas + Water (한국)": "Nippon Gas 8174.T - LP gas), KR (한국가스공사 036460.KS · 삼천리 004690.KS · 서울가스 017390.KS · 부산가스 015350.KS · S-Oil 010950.KS",
        "Asia Gas + Water (비상장)": "경동가스 비상장 · 한국전력 015760.KS), CN (Beijing Gas 비상장",
        "Asia Gas + Water (중국)": "Shanghai Gas 600133.SS",
        "Asia Gas + Water (홍콩)": "Sinopec Kantons 0934.HK · ENN Energy 2688.HK · Kunlun Energy 0135.HK",
        "Asia Gas + Water (미국)": "China Gas Holdings 0384.HK)",
    },
}
