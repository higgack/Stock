"""US Big 4 Diversified — L4 under Banks (은행).

자동 생성(부모 L3 banks 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "US Big 4 Diversified (Banks)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": [],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "US Big 4 Diversified — 주요 (JPMorgan Chase JPM · Bank of America BAC)",
        "US Big 4 Diversified — 기타·공급망 (Wells Fargo WFC · Citigroup C)",
    ],
    "catalyst_types": [
        "Fed funds rate path + Powell 의장 임기 만료 후임자 인선",
        "Basel III endgame 최종 룰 + CCAR stress test → 배당/자사주 ceiling",
        "美 CRE 만기 wall (2024-2026 ~$2T) + office vacancy + delinquency",
        "Regional bank NIM compression + 신주발행 dilution 압박",
        "中国 PBoC LPR + 부동산 신용 압박 + 港股통 southbound 은행 매수",
        "한국 DLF·부동산 PF 후속 규제 + 자본 적정성 (CET1) 변화",
    ],
    "regional_concentration": {
        "US Big 4 Diversified (주요)": "JPMorgan Chase JPM · Bank of America BAC",
        "US Big 4 Diversified (확장)": "Wells Fargo WFC · Citigroup C",
    },
}
