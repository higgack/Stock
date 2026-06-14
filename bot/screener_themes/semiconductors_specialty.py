"""Specialty & Compound (SiC·GaN·Photonics) — L4 under Semiconductors.

화합물 반도체 (SiC · GaN — 전력·RF) + 포토닉스/광반도체 + 이종집적 소재.
EV/데이터센터 SiC 채택률·GaN 고속충전·실리콘카바이드 캐파가 dominant. 부모
L3 = semiconductors.
"""

from __future__ import annotations

THEME = {
    "domain": "Specialty·Compound Semi (특수·화합물 반도체 SiC·GaN)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["sic", "gan", "화합물반도체", "compound_semi", "sic_gan",
                "전력화합물", "광반도체", "포토닉스"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "SiC (실리콘카바이드 — EV 인버터·산업 전력, 웨이퍼·디바이스)",
        "GaN (질화갈륨 — 고속충전·RF·데이터센터 전력)",
        "포토닉스·광반도체 (광트랜시버 · CPO · 레이저)",
        "이종집적·기판 (화합물 웨이퍼 · 파워 모듈 패키징)",
    ],
    "catalyst_types": [
        "EV/산업 SiC 채택률 + 800V 플랫폼 전환 + SiC 웨이퍼 6→8인치",
        "데이터센터·고속충전 GaN 침투 + 전력밀도 요구 상승",
        "AI 데이터센터 광인터커넥트(CPO/광트랜시버) 수요 폭증",
        "SiC 캐파 과잉·가격 하락 리스크(Wolfspeed) vs 자동차 장기 계약",
    ],
    "regional_concentration": {
        "SiC": "Wolfspeed WOLF · onsemi ON · STMicroelectronics STM · "
               "Infineon IFX.DE · Rohm 6963.T · 中 TankeBlue 비상장",
        "GaN": "Navitas NVTS · Power Integrations POWI · Infineon IFX.DE · "
               "Transphorm(Renesas) · EPC 비상장",
        "포토닉스·광": "Coherent COHR · Lumentum LITE · 비상장 광모듈 · "
                  "InnoLight 300308.SZ · Eoptolink 300502.SZ(中)",
    },
}
