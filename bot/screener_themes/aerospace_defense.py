"""Aerospace & Defense — L3 under Industrials (미국 정식 분류).

L1 trend `defense.py` (재무장 cycle niche — 포탄·SRM·AESA 등 좁은
binding) 와는 별개 lens — 본 모듈은 A&D industry 전체 (commercial
aviation + military prime + sub-tier + space launch). Wave 1 / L2 /
L3 의 3-layer 중복 허용 정책 (사용자 의도에 맞는 view 선택).
"""

from __future__ import annotations

THEME = {
    "domain": "Aerospace & Defense (항공우주 및 방위)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "aerospace_defense",
        "a_d",
        "항공우주",
        "방위산업_l3",
        "보잉",
        "boeing",
    ],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Commercial Aviation OEM (Boeing 737 MAX · Airbus A320neo · COMAC C919)",
        "Military Prime (LMT / RTX / NOC / GD / L3Harris · 전투기 + 미사일 방어)",
        "Sub-tier (TransDigm / Howmet / Heico — 항공기 부품 + 엔진 component)",
        "Space Launch + 위성 (SpaceX · Rocket Lab · Planet · Iridium)",
        "Naval Shipbuilding (HII · Babcock UK · 한화오션 · 三菱重工)",
        "Drones & Counter-UAS (AeroVironment · Kratos · Anduril 비상장)",
    ],
    "catalyst_types": [
        "Boeing 737 MAX 월별 출하량 + Airbus A320neo backlog + 中 COMAC C919 ramp",
        "美 DoD FY 예산 + Ukraine·Israel·Taiwan 추경 + EU NATO 2% → 3% 목표",
        "Hypersonic test 일정 + LRHW / HACM 다년 계약 ramp",
        "Space Force RFP + Tranche 2 위성 + Starshield 군용 capacity",
        "한국 방산 수출 계약 (폴란드 K2/K9 2차분 + 사우디 + 루마니아)",
        "AUKUS Pillar 1 잠수함 인도 일정 + 美 NRC SMR 해군 추진 인허가",
    ],
    "regional_concentration": {
        "Commercial OEM": "US (Boeing BA · GE Aerospace GE · Howmet HWM · TransDigm TDG · Heico HEI · Curtiss-Wright CW · Spirit AeroSystems SPR), EU (Airbus AIR.PA · Safran SAF.PA · Rolls-Royce RR.L · MTU MTX.DE), BR (Embraer ERJ), CA (Bombardier BBD-B.TO)",
        "US Military Prime": "Lockheed Martin LMT · RTX RTX · Northrop NOC · General Dynamics GD · L3Harris LHX · Huntington Ingalls HII · Textron TXT · Palantir PLTR · Kratos KTOS · AeroVironment AVAV · Mercury Systems MRCY",
        "EU Military": "BAE BA.L · Rheinmetall RHM.DE · Leonardo LDO.MI · Thales HO.PA · Saab SAAB-B.ST · Dassault Aviation AM.PA · Hensoldt HAG.DE · Indra IDR.MC",
        "Israel": "Elbit ESLT · Rafael 비상장 · Israel Aerospace 비상장",
        "Korea": "한화에어로스페이스 012450.KS · LIG넥스원 079550.KS · KAI 047810.KS · 한화시스템 272210.KS · 현대로템 064350.KS · 풍산 103140.KS",
        "Japan + Asia": "Mitsubishi Heavy 7011.T · IHI 7013.T · Kawasaki Heavy 7012.T · NEC 6701.T · AU (EOS Defense EOS.AX)",
        "Space launch + 위성": "SpaceX 비상장 · Rocket Lab RKLB · Planet PL · Iridium IRDM · Maxar 비상장 · Astranis 비상장 · Intuitive Machines LUNR · Redwire RDW",
    },
}
