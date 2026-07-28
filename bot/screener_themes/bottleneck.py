"""AI Data Center Buildout — Bottleneck Screener default domain.

Ported from the inline ``_AI_DATACENTER_THEME`` dict that lived in
bot/screener.py through Phase α/β. Content is unchanged so back-archived
Phase β runs (`screener_archive/*/HHMMSS_ai_data_center_buildout.json`)
still match this slug — anyone re-running them to compare gets the same
layer / catalyst / SPOF context.

Aliases: ``bottleneck`` (legacy default before the Wave 1 registry
shipped — /screener with no argument still routes here) plus the short
labels users naturally type.
"""

from __future__ import annotations

THEME = {
    "domain": "AI Data Center Buildout",
    "layer": "L1_TREND",
    "aliases": [
        "bottleneck",
        "ai",
        "ai_datacenter",
        "datacenter",
        "ai데이터센터",
        "데이터센터",
    ],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "HBM / 첨단 패키징 (CoWoS / ABF substrate)",
        "액체냉각 (Quick disconnect / TIM / Vapor chamber)",
        "전력 (Transformer / Busbar / GaN / SiC)",
        "광통신 (CPO / Co-packaged optics / Fiber)",
        "특수가스 / Wet chemistry",
        "Test / Burn-in / Probe card",
        "수동소자 (MLCC / 저항 / 인덕터)",
        "EMS / AI server 조립",
    ],
    "catalyst_types": [
        "하이퍼스케일러 capex 가이드 (Microsoft / Meta / Google / Amazon)",
        "TSMC / SK Hynix / Samsung HBM·CoWoS 캐파 expansion 발표",
        "美 BIS 對中 수출규제 / entity list 변경",
        "NVIDIA Blackwell / Rubin 채택률 데이터",
        "전력 인프라 grid 병목 + 데이터센터 부지 승인",
    ],
    "regional_concentration": {
        "HBM": "KR (Samsung 005930.KS / SK Hynix 000660.KS), US (Micron MU)",
        "ABF substrate": "JP (Ibiden 4062.T / Shinko 5703.T)",
        "Cooling": "TW (Auras 3324.TW / AVC 3017.TW), JP (Sunon 2421.TW)",
        "CoWoS": "TW (TSMC 2330.TW)",
        "Power": "EU (Siemens Energy ENR.DE / Schneider SU.PA), US (Eaton ETN / Vertiv VRT)",
        "Optical": "TW (Hon Hai 2317.TW), US (Coherent COHR / Lumentum LITE)",
        "Specialty gas": "JP (Air Water 4088.T), KR (SK Materials)",
    },
}
