"""Mobile/Consumer Logic — L4 under Semiconductors & Equipment (Technology).

모바일 AP/SoC + 5G 모뎀·RF 프론트엔드 + 커넥티비티/오디오/터치 칩. iPhone
사이클·애플 자체칩 전환·5G 콘텐츠가 dominant. 부모 L3 = semiconductors.
"""

from __future__ import annotations

THEME = {
    "domain": "Mobile·Consumer Logic (모바일·소비자 로직 반도체)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["mobile_chip", "ap_chip", "모바일ap", "모바일반도체",
                "rffe", "modem_chip", "커넥티비티칩"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "모바일 AP/SoC (스마트폰 애플리케이션 프로세서)",
        "5G 모뎀·베이스밴드 (셀룰러 연결 — 애플 자체 모뎀 전환 리스크)",
        "RF 프론트엔드 (PA · 필터 · 스위치 — BAW/SAW)",
        "커넥티비티·오디오·터치 (Wi-Fi/BT · 코덱 · 디스플레이 드라이버)",
    ],
    "catalyst_types": [
        "iPhone 16/17 사이클 + Android 플래그십 출하 + AP ASP",
        "Qualcomm 5G 모뎀 점유 vs Apple 자체 모뎀/베이스밴드 내재화 전환",
        "온디바이스 AI(NPU) 채택 + 모바일 DRAM 콘텐츠 증가",
        "MediaTek vs Qualcomm 중·고가 AP 경쟁 + 中 스마트폰 수요",
    ],
    "regional_concentration": {
        "AP·모뎀": "Qualcomm QCOM · MediaTek 2454.TW · Apple AAPL(자체칩) · "
                 "Samsung 005930.KS(엑시노스) · UNISOC 비상장(中)",
        "RF 프론트엔드": "Skyworks SWKS · Qorvo QRVO · Broadcom AVGO · "
                    "Qualcomm QCOM · Murata 6981.T",
        "커넥티비티·오디오": "Cirrus Logic CRUS · Synaptics SYNA · "
                       "Realtek 2379.TW · Goodix 603160.SS · "
                       "Power Integrations POWI",
    },
}
