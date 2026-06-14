"""Analog · Power · IDM — L4 under Semiconductors & Equipment (Technology).

아날로그·믹스드시그널 + 전력 반도체 + MCU + 자동차/산업 IDM. 재고 사이클·
자동차/산업 수요·전동화가 dominant (소비자 로직보다 사이클 완만). 부모 L3 =
semiconductors.
"""

from __future__ import annotations

THEME = {
    "domain": "Analog·Power·IDM (아날로그·전력·마이크로컨트롤러)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["analog", "아날로그", "power_semi", "전력반도체", "mcu",
                "idm", "마이크로컨트롤러", "아날로그반도체"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "범용 아날로그·믹스드시그널 (신호체인 · 전원관리 PMIC)",
        "전력 반도체 (실리콘 IGBT/MOSFET — 자동차·산업 전동화)",
        "MCU (자동차·산업·IoT 마이크로컨트롤러)",
        "센서·인터페이스 (이미지·MEMS · 자동차 인터페이스)",
    ],
    "catalyst_types": [
        "자동차/산업 재고 조정 종료 + book-to-bill 1.0 상회 전환",
        "전동화(EV) per-car 반도체 콘텐츠 증가 + 전력 반도체 수요",
        "공장 가동률 + ASP·마진 (자동차향 장기 계약 가격)",
        "中 아날로그/전력 국산화 + 美·EU 자동차 반도체 공급망 재편",
    ],
    "regional_concentration": {
        "아날로그·믹스드 (US)": "Texas Instruments TXN · Analog Devices ADI · "
                          "Microchip MCHP · Monolithic Power MPWR · "
                          "Power Integrations POWI · Allegro ALGM",
        "전력·자동차 (EU/JP)": "Infineon IFX.DE · STMicroelectronics STM · "
                          "NXP NXPI · onsemi ON · Renesas 6723.T · Rohm 6963.T",
        "MCU·센서": "STMicroelectronics STM · Renesas 6723.T · "
                  "Microchip MCHP · NXP NXPI · ams OSRAM AMS.SW",
    },
}
