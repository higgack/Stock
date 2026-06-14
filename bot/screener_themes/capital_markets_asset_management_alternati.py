"""Asset Management — Alternatives — L4 under Capital Markets & Investment (자본 시장 및 투자).

자동 생성(부모 L3 capital_markets 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Asset Management — Alternatives (Capital Markets & Investment)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["alternatives"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Asset Management — Alternatives — 미국 (Blackstone BX · KKR KKR · Apollo APO · Ares ARES · Carlyle CG · Brookfield BAM · TPG TPG · Hamilton Lane HLNE)",
        "Asset Management — Alternatives — 비상장 (Bridge Investment 비상장 · BlueRock 비상장)",
        "Asset Management — Alternatives — 유럽 (EQT EQT.ST)",
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
        "Asset Management — Alternatives (미국)": "Blackstone BX · KKR KKR · Apollo APO · Ares ARES · Carlyle CG · Brookfield BAM · TPG TPG · Hamilton Lane HLNE · StepStone STEP · Patria PAX · GCM Grosvenor GCMG",
        "Asset Management — Alternatives (비상장)": "Bridge Investment 비상장 · BlueRock 비상장",
        "Asset Management — Alternatives (유럽)": "EQT EQT.ST",
    },
}
