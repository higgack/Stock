"""P&C — Auto + Home — L4 under Insurance (보험).

자동 생성(부모 L3 insurance 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "P&C — Auto + Home (Insurance)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["auto_home"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "P&C — Auto + Home — 미국 (Progressive PGR · Allstate ALL · Travelers TRV · GEICO (BRK.B 자회사) · Mercury MCY · Erie ERIE · Kemper KMPR · Selective SIGI)",
        "P&C — Auto + Home — 비상장 (State Farm 비상장)",
    ],
    "catalyst_types": [
        "美 자연재해 cycle (허리케인 시즌 + 산불 + 토네이도) → 분기 cat loss",
        "Auto loss ratio (PGR · ALL 분기 가이드) + premium 인상 사이클",
        "Variable annuity hedge book + 美 10Y 급변 시 자본여력 swing",
        "Insurance brokers fee 사이클 + 분기 organic growth (MMC · AON · AJG)",
        "한국 보험 IFRS17 시행 후 자본 영향 + 海外 운용 손익",
        "中国 평안보험 분기 NBV + 港股통 southbound 보험 매수 변동",
    ],
    "regional_concentration": {
        "P&C — Auto + Home (미국)": "Progressive PGR · Allstate ALL · Travelers TRV · GEICO (BRK.B 자회사) · Mercury MCY · Erie ERIE · Kemper KMPR · Selective SIGI",
        "P&C — Auto + Home (비상장)": "State Farm 비상장",
    },
}
