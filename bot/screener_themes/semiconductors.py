"""Semiconductors & Equipment — L3 under Technology.

Design (NVIDIA · AMD · Qualcomm · Broadcom) + Memory (Samsung · SK
Hynix · Micron) + Foundry (TSMC · Samsung · Intel · GlobalFoundries +
SMIC) + Equipment (ASML · AMAT · LRCX · KLA · Tokyo Electron · 한미반도체)
+ EDA (Synopsys · Cadence · Ansys) + IP (Arm).

NVIDIA Blackwell/Rubin + HBM3E/HBM4 양산 + ASML High-NA EUV + 美 BIS
對中 수출규제가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Semiconductors & Equipment (반도체 및 장비)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "semiconductors",
        "semiconductors_l3",
        "반도체_l3",
        "semiconductor_l3",
        "semi_l3",
        "semi_design",
        "반도체설계",
        "foundry",
        "파운드리",
        "semi_equipment",
        "반도체장비",
        "eda",
        "memory",
        "메모리",
        "hbm",
    ],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Logic Design — AI/Datacenter (NVIDIA · AMD · Broadcom · Marvell · Arm)",
        "Logic Design — Mobile/Consumer (Apple · Qualcomm · MediaTek · Realtek)",
        "Memory (Samsung · SK Hynix · Micron · Kioxia · Western Digital)",
        "Foundry (TSMC · Samsung Foundry · Intel · UMC · GlobalFoundries · SMIC)",
        "Analog + Power + IDM (Texas Instruments · Analog Devices · onsemi · NXP · Microchip · STM · Renesas · Infineon · Rohm)",
        "Semiconductor Equipment (ASML · Applied · Lam · KLA · Tokyo Electron · Disco · Advantest · 한미반도체)",
        "EDA (Synopsys · Cadence · Ansys)",
        "Specialty + Photonics + Compound (Coherent · onsemi · Wolfspeed · CREE · 6세대 GaN/SiC)",
    ],
    "catalyst_types": [
        "NVIDIA Blackwell/Rubin 출하량 + Hyperscaler AI capex 가이드 (MSFT/META/GOOG/AMZN)",
        "DRAM/NAND spot 가격 + HBM3E/HBM4 양산 비중 (Samsung/SK Hynix/Micron)",
        "ASML High-NA EUV 첫 양산 도입 + TSMC N2/A16 + Samsung 3나 yield",
        "美 BIS 對中 수출규제 4차 + EU/JP 동조 + entity list 확장",
        "Apple iPhone 16/17 사이클 + Qualcomm 5G 모뎀 vs Apple 자체 chip transition",
        "Synopsys/Cadence 분기 backlog + AI chip 설계 license + 中 EDA 분리 영향",
        "Wolfspeed SiC 4공장 가동 + 자동차 + 데이터센터 SiC 채택률",
    ],
    "regional_concentration": {
        "Logic Design — AI/Datacenter": "NVIDIA NVDA · AMD AMD · Broadcom AVGO · Marvell Technology MRVL · Arm Holdings ARM · MIPS 비상장 · SiFive 비상장",
        "Logic Design — Mobile/Consumer": "Qualcomm QCOM · MediaTek 2454.TW · Realtek 2379.TW · Apple AAPL (자체 chip) · Texas Instruments TXN · onsemi ON · Skyworks SWKS · Qorvo QRVO · Cirrus Logic CRUS · Synaptics SYNA · Lattice Semiconductor LSCC · Lattice LSCC · Sitime SITM · Power Integrations POWI · Diodes DIOD",
        "Memory": "Samsung Electronics 005930.KS · SK Hynix 000660.KS · Micron Technology MU · Kioxia 비상장 (IPO 2025 announced) · Western Digital WDC · Nanya Technology 2408.TW · Winbond Electronics 2344.TW · Powerchip Semiconductor 6770.TW · NEXTCHIP 091590.KQ",
        "Foundry": "TSMC TSM + 2330.TW (글로벌 #1) · Samsung Foundry (Samsung Electronics 005930.KS) · Intel INTC (IFS Foundry 부문) · UMC UMC + 2303.TW · GlobalFoundries GFS · SMIC 0981.HK + 688981.SS · Vanguard International VIS 5347.TWO · Tower Semiconductor TSEM",
        "Analog + Power + IDM": "Texas Instruments TXN · Analog Devices ADI · NXP Semiconductors NXPI · Microchip MCHP · onsemi ON · STMicroelectronics STM · Infineon Technologies IFX.DE · Renesas 6723.T · Rohm 6963.T · ASM International ASM · ams OSRAM AMS.SW · Allegro MicroSystems ALGM · Power Integrations POWI · Monolithic Power MPWR · Wolfspeed WOLF (SiC) · Allegro ALGM",
        "Semi Equipment + Materials (Global)": "ASML ASML (EUV/DUV 글로벌 독점) · Applied Materials AMAT · Lam Research LRCX · KLA Corporation KLAC · Onto Innovation ONTO · Axcelis ACLS · Veeco VECO · Photronics PLAB · Entegris ENTG · Coherent COHR (laser + photonics) · Brooks Automation BRKS · Ichor ICHR · Cohu COHU · MKS Instruments MKSI · Aehr Test Systems AEHR · ACM Research ACMR · Camtek CAMT · Nova NVMI · UCT Holdings UCTT",
        "Semi Equipment + Materials (Japan + Korea)": "JP (Tokyo Electron 8035.T · Disco 6146.T · Advantest 6857.T · Screen Holdings 7735.T · Lasertec 6920.T · Tokyo Seimitsu 7729.T · Daifuku 6383.T · Kokusai 6525.T · SUMCO 3436.T · Shin-Etsu 4063.T - 웨이퍼 · Resonac 4004.T - 약물 · JSR 4185.T - 포토레지스트), KR (한미반도체 042700.KS · 원익IPS 240810.KQ · 주성엔지니어링 036930.KQ · 동진쎄미켐 005290.KS · 솔브레인 357780.KQ · 하나마이크론 067310.KQ · 리노공업 058470.KQ · 이오테크닉스 039030.KQ · 피에스케이 319660.KQ · 케이씨텍 281820.KQ · 테스 095610.KQ · 디아이 003160.KS · 미코 059090.KQ)",
        "EDA + Semi IP": "Synopsys SNPS · Cadence Design CDNS · Ansys ANSS · Arm Holdings ARM · Imagination Technologies 비상장 · Ceva CEVA · Mentor Graphics 비상장 (Siemens 자회사) · SiFive 비상장 · MIPS 비상장 · LeoCAD 비상장",
        "Specialty SiC + Compound": "Wolfspeed WOLF (구 Cree) · onsemi ON (SiC) · STMicroelectronics STM · Infineon IFX.DE · Renesas 6723.T · Rohm 6963.T · Allegro MicroSystems ALGM · Power Integrations POWI · Navitas Semiconductor NVTS · Transphorm TGAN · GaN Systems 비상장 (Infineon 인수) · IQE 비상장 · MagnaChip MX · Akoustis AKTS",
    },
}
