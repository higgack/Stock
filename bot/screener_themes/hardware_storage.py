"""Hardware & Equipment — L3 under Technology.

Tech Hardware (Apple · Dell · HPQ · HPE · Super Micro) + Storage (NetApp
· WDC · Seagate · Pure) + Communications Equipment (Cisco · Arista ·
Juniper). AI 서버 + AI fabric switch (Arista) + HDD/SSD 가격 사이클 +
Apple iPhone 16/17 사이클이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Hardware & Equipment (하드웨어 및 장비)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "hardware_storage",
        "hardware_l3",
        "하드웨어_l3",
        "tech_hardware",
        "storage",
        "스토리지",
        "communications_equipment",
        "통신장비",
        "ai_server",
        "ai서버",
        "consumer_electronics",
        "소비자전자",
    ],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Tech Hardware (Apple · Dell · HPQ · HPE · Super Micro)",
        "Storage Devices + Memory Form Factor (NetApp · WDC · Seagate · Pure · Pure Storage)",
        "Communications Equipment + AI Fabric Switch (Cisco · Arista · Juniper · Motorola Solutions)",
        "Consumer Electronics (Apple · Sony · Samsung Electronics · Xiaomi · Lenovo)",
        "Networking + Connectivity (Cisco · Arista · Juniper · F5 · NetGear · Lumentum · Coherent)",
        "Taiwan ODM/EMS (Hon Hai · Quanta · Wistron · Compal · Pegatron · Inventec)",
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
        "US Tech Hardware + AI Server": "Apple AAPL · Dell DELL · HP Inc HPQ · HPE HPE · Super Micro SMCI · NetApp NTAP · Pure Storage PSTG · Western Digital WDC · Seagate STX · Belden BDC · Vishay Intertechnology VSH · CDW CDW · Insight Enterprises NSIT · Arrow Electronics ARW · Avnet AVT",
        "Storage Devices + Memory": "Western Digital WDC · Seagate STX · Micron MU · Samsung 005930.KS · SK Hynix 000660.KS · Kioxia 비상장 (IPO 2025) · NetApp NTAP · Pure Storage PSTG · Veeam 비상장 · Cohesity 비상장 · Rubrik RBRK · IDrive 비상장",
        "Communications Equipment + AI Fabric": "Cisco CSCO · Arista Networks ANET · Juniper Networks JNPR · Motorola Solutions MSI · F5 FFIV · Ciena CIEN · NetGear NTGR · Lumentum LITE · Coherent COHR · Acacia Communications 비상장 (Cisco 인수) · Calix CALX · Viavi Solutions VIAV · CommScope COMM · Extreme Networks EXTR",
        "Consumer Electronics": "Apple AAPL · Sony 6758.T · Panasonic 6752.T · Nintendo 7974.T · Samsung Electronics 005930.KS · LG Electronics 066570.KS · Xiaomi 1810.HK · Lenovo 0992.HK · ZTE 0763.HK · Huawei 비상장 · GoPro GPRO · Sonos SONO · Roku ROKU · Bose 비상장 · Logitech LOGI · Garmin GRMN · Boats 비상장 · Resideo Technologies REZI",
        "Taiwan ODM/EMS + AI Server Manufacturing": "Hon Hai 2317.TW (Foxconn) · Quanta 2382.TW · Wistron 3231.TW · Compal 2324.TW · Inventec 2356.TW · Pegatron 4938.TW · Asustek 2357.TW · Acer 2353.TW · MediaTek 2454.TW · ASE 3711.TW · Realtek 2379.TW · GIGA-BYTE 2376.TW · Adlink 6166.TW",
        "Korean Hardware + EMS": "Samsung Electronics 005930.KS · LG Electronics 066570.KS · LG디스플레이 034220.KS · 삼성SDI 006400.KS · 삼성SDS 018260.KS · LG이노텍 011070.KS · 삼성전기 009150.KS · 삼성디스플레이 비상장 · 매그나칩반도체 MX",
        "EU + Other Hardware": "EU (Ericsson ERIC · Nokia NOK · Logitech LOGI · STMicroelectronics STM · Spectris SXS.L · Renishaw RSW.L · Halma HLMA.L · Smiths Group SMIN.L · Roper Technologies ROP), JP Hardware (Sony 6758.T · Panasonic 6752.T · Fujitsu 6702.T · NEC 6701.T · Hitachi 6501.T · Toshiba 6502.T · Sharp 6753.T · Casio Computer 6952.T · Brother Industries 6448.T)",
    },
}
