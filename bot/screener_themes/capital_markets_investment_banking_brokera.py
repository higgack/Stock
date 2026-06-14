"""Investment Banking + Brokerage — L4 under Capital Markets & Investment (자본 시장 및 투자).

자동 생성(부모 L3 capital_markets 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Investment Banking + Brokerage (Capital Markets & Investment)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["investment", "banking"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Investment Banking + Brokerage — 미국 (Goldman Sachs GS · Morgan Stanley MS · Charles Schwab SCHW · Interactive Brokers IBKR · LPL Financial LPLA · Stifel SF · Houlihan Lokey HLI · Raymond James RJF)",
        "Investment Banking + Brokerage — 비상장 (TD Cowen 비상장)",
    ],
    "catalyst_types": [
        "美 IPO + M&A volume + 분기 investment banking fee + advisory backlog",
        "BLK · KKR · APO 분기 AUM net flow + 신규 fund 모집 + DPI/IRR 가이드",
        "ETF spot Bitcoin/Ethereum inflow + BLK iShares 점유율",
        "분기 거래량 (CME 옵션 + ICE energy + NDAQ Cash Equity)",
        "MSCI 인덱스 rebalance + 신규 ESG/AI 테마 ETF 출시",
        "美 Reg BI + EU MiFID II/III 후속 + retail brokerage 수수료 압박",
    ],
    "regional_concentration": {
        "Investment Banking + Brokerage (미국)": "Goldman Sachs GS · Morgan Stanley MS · Charles Schwab SCHW · Interactive Brokers IBKR · LPL Financial LPLA · Stifel SF · Houlihan Lokey HLI · Raymond James RJF · Lazard LAZ · Evercore EVR · Moelis MC · PJT Partners PJT",
        "Investment Banking + Brokerage (비상장)": "TD Cowen 비상장",
    },
}
