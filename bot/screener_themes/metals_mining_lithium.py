"""Lithium — L4 under Metals & Mining (금속 및 광업).

자동 생성(부모 L3 metals_mining 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Lithium (Metals & Mining)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["lithium"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Lithium — 미국 (Albemarle ALB · SQM SQM · Livent LTHM · Sigma Lithium SGML · Lithium Americas LAC · 미국 (Lithium Americas LAC · Standard Lithium SLI · Piedmont Lithium PLL)",
        "Lithium — 호주 (Pilbara PLS.AX · IGO IGO.AX · Mineral Resources MIN.AX)",
        "Lithium — 비상장 (Allkem 비상장 (Livent 합병 후 ARC))",
        "Lithium — 중국 (Tianqi 002466.SZ · Ganfeng 002460.SZ)",
    ],
    "catalyst_types": [
        "美/EU CBAM (철강 · 알루미늄 · 시멘트) 효력 시점",
        "전동화 + 그리드 capex 사이클 (구리 / 알루미늄 / 리튬) supply gap",
        "中国 14차 5개년 rare earth 수출 통제 + 美 reshoring + 호주 Pilbara",
        "Au/Ag 가격 + 中国 PBoC 금 매입 + Fed 정책금리 (Au inverse 상관)",
        "철강 가격 (HRC spot) + 인프라 bill 시행 + 중국 부동산 회복",
        "리튬 spot (Pilbara SC6 + Chile SQM brine) + 니켈 LME 가격",
    ],
    "regional_concentration": {
        "Lithium (미국)": "Albemarle ALB · SQM SQM · Livent LTHM · Sigma Lithium SGML · Lithium Americas LAC · 미국 (Lithium Americas LAC · Standard Lithium SLI · Piedmont Lithium PLL · Atlas Lithium ATLX) · BR (Sigma Lithium SGML)",
        "Lithium (호주)": "Pilbara PLS.AX · IGO IGO.AX · Mineral Resources MIN.AX",
        "Lithium (비상장)": "Allkem 비상장 (Livent 합병 후 ARC)",
        "Lithium (중국)": "Tianqi 002466.SZ · Ganfeng 002460.SZ",
    },
}
