"""Taiwan ODM/EMS — L4 under Hardware & Equipment (하드웨어 및 장비).

자동 생성(부모 L3 hardware_storage 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Taiwan ODM/EMS (Hardware & Equipment)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["taiwan"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Taiwan ODM/EMS — 미국 (Hon Hai 2317.TW (Foxconn))",
        "Taiwan ODM/EMS — 대만 (Quanta 2382.TW · Wistron 3231.TW · Compal 2324.TW · Inventec 2356.TW · Pegatron 4938.TW · Asustek 2357.TW · Acer 2353.TW · MediaTek 2454.TW)",
    ],
    "catalyst_types": [
        "Apple iPhone 16/17 분기 출하량 + 美 reshoring (India + Vietnam) + Vision Pro 채택",
        "AI 서버 분기 매출 (SMCI · Dell · HPE) + 美 Hyperscaler GPU 인수",
        "Arista AI fabric switch 매출 (Cisco vs Arista) + 800G/1.6T Ethernet 채택",
        "HDD/SSD spot 가격 (WDC · Seagate · Micron) + 분기 mix shift",
        "Sony PS5 분기 출하 + Switch 2 launch (2025-2026) + Xiaomi 분기 매출",
        "TW ODM/EMS 분기 server + cloud 매출 + iPhone 위탁 생산 가이드",
    ],
    "regional_concentration": {
        "Taiwan ODM/EMS (미국)": "Hon Hai 2317.TW (Foxconn)",
        "Taiwan ODM/EMS (대만)": "Quanta 2382.TW · Wistron 3231.TW · Compal 2324.TW · Inventec 2356.TW · Pegatron 4938.TW · Asustek 2357.TW · Acer 2353.TW · MediaTek 2454.TW · ASE 3711.TW · Realtek 2379.TW · GIGA-BYTE 2376.TW · Adlink 6166.TW",
    },
}
