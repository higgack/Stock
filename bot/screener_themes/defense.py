"""Defense, Aerospace & Space — Wave 1 domain.

ToC 관점: 미국 5대 prime (LMT/RTX/NOC/GD/HII) 은 GPU·OEM 격 1차 헤드라인
이라 rent 독식 효율 낮음 (DoD cost-plus 계약 + budget 정치 의존). 진짜
choke point 는 (a) 미사일·드론 sub-system (Iron Dome RFP 의 Rafael →
모터·warhead·seeker 가 별도 supplier), (b) Hypersonic 와 (c) Space
launch + 위성 부품. 위성 sub-tier (안테나·전력·thermal) 가 SpaceX
Starlink + DoD Tranche n 가속에 직접 노출. 한국 (한화 / KAI / LIG) +
독일 (Rheinmetall) + 이스라엘 (Elbit) 이 EU/亞 capacity 우위.

Wave 1 catalyst weighting: DoD FY 예산 + 추경 (Ukraine resupply / Israel
emergency package / Taiwan) 가 가장 큰 1차 선행지표. 다음으로 (b) 한국
방산 수출 — 폴란드 K2/K9 + UAE 천궁 + 사우디 + Romania → 한화에어로
스페이스 / LIG넥스원 매출 전이. (c) Hypersonic gap 우려 — 中国 DF-17 +
RU Avangard 대비 美 LRHW / HACM 계약 ramp. (d) Space Force 신규 RFP +
Starlink-군용 + Tranche 2 위성.
"""

from __future__ import annotations

THEME = {
    "domain": "Defense, Aerospace & Space",
    "layer": "L1_TREND",
    "aliases": [
        "defense",
        "defence",
        "방산",
        "방위산업",
        "우주",
        "aerospace",
        "space",
    ],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "미사일 방어 (Patriot / Iron Dome / THAAD / Arrow / 천궁)",
        "드론 & 대드론 (UAV / loitering munition / counter-UAS)",
        "탄약 + 정밀유도 (155mm shell / GMLRS / HIMARS / JDAM)",
        "Hypersonic (LRHW / HACM / DF-17 catch-up)",
        "위성 + Space launch (Starlink-military / Tranche 2 / SDA)",
        "함정 + 잠수함 (Columbia-class / Virginia / KSS-III)",
        "전투기 + 엔진 (F-35 / NGAD / 6세대 / 엔진 module)",
        "사이버보안 + AI 군용 (Palantir AIP / Anduril Lattice)",
    ],
    "catalyst_types": [
        "美 DoD FY 예산 + Ukraine·Israel·Taiwan 추경 발표",
        "한국 방산 수출 계약 (폴란드 K2/K9 2차분 + 사우디 + 루마니아)",
        "Hypersonic test 일정 + LRHW / HACM 다년 계약 ramp",
        "Space Force RFP + Tranche 2 위성 + Starshield 군용 capacity",
        "EU NATO 2% → 3% 목표 + 독일 Sondervermögen 집행률",
        "Iran-Israel / Taiwan Strait / Ukraine front-line 사건 동향",
        "Hyperscaler-defense AI 계약 (Palantir USG / Microsoft Azure Gov)",
    ],
    "regional_concentration": {
        "US prime": "Lockheed Martin LMT / RTX RTX / Northrop NOC / General Dynamics GD / L3Harris LHX",
        "US sub-tier & innovators": "Howmet HWM (엔진) / TransDigm TDG / Curtiss-Wright CW / Heico HEI / Palantir PLTR / AeroVironment AVAV / Kratos KTOS",
        "Naval / Shipbuilding": "Huntington Ingalls HII (US Columbia/Virginia), HHI 329180.KS (KSS-III), Mitsubishi Heavy 7011.T (마야급 destroyer)",
        "Space launch + 위성": "SpaceX (비상장 — Rocket Lab RKLB / Planet PL / Iridium IRDM / Maxar 비상장 PE), L3Harris LHX, Astranis 비상장",
        "EU prime": "Rheinmetall RHM.DE / BAE Systems BA.L / Leonardo LDO.MI / Saab SAAB-B.ST / Thales HO.PA / Dassault Aviation AM.PA / MTU Aero Engines MTX.DE",
        "Israel": "Elbit Systems ESLT / Rafael 비상장 (parent state) / Israel Aerospace 비상장",
        "Korea": "한화에어로스페이스 012450.KS (K9·천무·누리호 엔진) / LIG넥스원 079550.KS (천궁·해궁) / 한국항공우주 047810.KS (KF-21·FA-50) / 현대로템 064350.KS (K2 흑표) / 한화시스템 272210.KS (위성·레이더)",
        "Japan": "Mitsubishi Heavy 7011.T (마야급·F-X), IHI 7013.T (엔진), Kawasaki Heavy 7012.T (잠수함), NEC 6701.T (위성)",
        "Drone / UAV pure-play": "AeroVironment AVAV / Kratos KTOS (Valkyrie) / Red Cat RCAT, EU (Anduril 비상장 / EOS Defense 호주 EOS.AX)",
    },
}
