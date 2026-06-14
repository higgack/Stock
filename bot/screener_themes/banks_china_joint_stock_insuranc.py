"""China 国有 + Joint-Stock + Insurance Bank — L4 under Banks (은행).

자동 생성(부모 L3 banks 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "China 国有 + Joint-Stock + Insurance Bank (Banks)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["china_joint", "china", "joint"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "China 国有 + Joint-Stock + Insurance Bank — 중국 (ICBC 1398.HK + 601398.SS · CCB 0939.HK + 601939.SS · 兴业 601166.SS · 浦发 600000.SS)",
        "China 国有 + Joint-Stock + Insurance Bank — 홍콩 (Bank of China 3988.HK · ABC 1288.HK · BOCOM 3328.HK · 招商 3968.HK · 中信 0998.HK · 民生 1988.HK)",
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
        "China 国有 + Joint-Stock + Insurance Bank (중국)": "ICBC 1398.HK + 601398.SS · CCB 0939.HK + 601939.SS · 兴业 601166.SS · 浦发 600000.SS",
        "China 国有 + Joint-Stock + Insurance Bank (홍콩)": "Bank of China 3988.HK · ABC 1288.HK · BOCOM 3328.HK · 招商 3968.HK · 中信 0998.HK · 民生 1988.HK",
    },
}
