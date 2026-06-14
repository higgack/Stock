"""PE Sponsor Affiliated — L4 under Business Development Companies (BDCs).

자동 생성(부모 L3 bdc 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "PE Sponsor Affiliated (Business Development Companies)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["sponsor_affiliated", "sponsor", "affiliated"],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "PE Sponsor Affiliated — 미국 (Apollo BDC AINV · Carlyle Group GBDC (Golub Capital BDC 인수 후) · KKR BDC FSK · Goldman Sachs BDC GSBD · Morgan Stanley Direct Lending MSDL)",
        "PE Sponsor Affiliated — 비상장 (Blackstone Private Credit (BCRED) 비상장 · Cliffwater Direct Lending Fund 비상장)",
    ],
    "catalyst_types": [
        "SOFR + 美 사기업 default rate + BDC non-accrual rate 분기 가이드",
        "Fed funds rate path → BDC NII (floating-rate loan) 직접 영향",
        "분기 dividend 발표 + base + special + ROAA + leverage 가이드",
        "美 사기업 EBITDA cycle + middle-market PE 인수 활동 데이터",
        "BDC ETF/CEF inflow + 시가총액 대비 NAV premium/discount 변동",
        "Direct Lending market (BX · KKR · ARES · APO 자회사) 와의 경쟁 + spread compression",
    ],
    "regional_concentration": {
        "PE Sponsor Affiliated (미국)": "Apollo BDC AINV · Carlyle Group GBDC (Golub Capital BDC 인수 후) · KKR BDC FSK · Goldman Sachs BDC GSBD · Morgan Stanley Direct Lending MSDL",
        "PE Sponsor Affiliated (비상장)": "Blackstone Private Credit (BCRED) 비상장 · Cliffwater Direct Lending Fund 비상장",
    },
}
