"""EV & Battery — Wave 1 domain.

Binding layer 깊이: 셀 단계 (양극재·음극재·분리막·전해질·집전체)는 KR/CN/JP
가 sub-tier 까지 잘게 쪼개져 있고, OEM 단계 (Tesla·BYD·Hyundai) 는 GPU 와
같은 1차 헤드라인이라 ToC 관점에서 'choke point owner' 비중 낮음. 따라서
catalyst/regional 모두 셀 + 핵심소재 우위로 가중. Charging infra (CHPT/
EVgo/Allego) 와 충전 chip (Wolfspeed·Nexperia 의 SiC) 은 별도 sub-layer
로 두고, OEM 은 catalyst 표면에서만 다룸 (BYD 가격전 / 美 100% 관세 → 셀
공급사로 rent 전이 형태).

Catalyst weighting: IRA 45X (셀 + 모듈 production credit) 가 가장 강력한
선행지표 — KR 셀 3사 ($35/kWh + $10/kWh) + 美 캐파 빌드에 직접 반영. EU
2035 ICE ban 일정 변경은 OEM 헤지 변화로 셀 mix 시프트 (NCM vs LFP) 가
catalyst path. 中国 BYD LFP 가격전은 동시에 (a) 글로벌 셀가 deflation +
(b) KR 셀 3사 NCM ASP 압박 → 양 방향. 美 對中 EV/배터리 100% 관세는
KR/JP 셀에 rent 이전 형태로 유리.
"""

from __future__ import annotations

THEME = {
    "domain": "EV & Battery",
    "layer": "L1_TREND",
    "aliases": [
        "ev",
        "battery",
        "batteries",
        "전기차",
        "배터리",
        "2차전지",
        "이차전지",
    ],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "배터리 셀 (NCM 811/9 / LFP / 4680 form factor)",
        "양극재 (NCM Ni-rich / LFP / LFMP)",
        "음극재 (천연/인조 graphite / Si-C 복합)",
        "분리막 (wet / dry / 코팅)",
        "전해질 (LiPF6 / 첨가제 / 고체전해질 R&D)",
        "리튬·니켈·코발트 원자재 (spodumene / brine / DLE)",
        "EV 충전 인프라 (DC fast / V2G)",
        "충전 SiC / GaN 전력반도체",
    ],
    "catalyst_types": [
        "美 IRA 45X production credit 가이드 / 변경 + Trump 행정명령 EV 정책",
        "美 對中 EV·배터리 100% 관세 + EU 38% 관세 효력 시점",
        "EU 2035 내연기관 금지 일정 + 2025 CO2 fleet target",
        "中国 BYD / CATL LFP 가격전 + 셀가 spot 변화",
        "리튬 spot (Pilbara SC6 / Chile SQM brine) + 니켈 LME 가격",
        "OEM 신차 출시 + 배터리 supplier 발표 (Tesla 4680 / GM Ultium / Hyundai E-GMP)",
        "고체전해질 sample 출하 + EV 도입 일정 (Toyota / Samsung SDI)",
    ],
    "regional_concentration": {
        "Battery cell (NCM)": "KR (LG엔솔 373220.KS / 삼성SDI 006400.KS / SK on 시장 비상장)",
        "Battery cell (LFP)": "CN (CATL 300750.SZ / BYD 002594.SZ / EVE 300014.SZ / Gotion 002074.SZ)",
        "Cathode (NCM)": "KR (포스코퓨처엠 003670.KS / 에코프로비엠 247540.KQ / 엘앤에프 066970.KQ / 코스모신소재 005070.KS)",
        "Cathode (LFP)": "CN (Tongwei 600438.SS / Dynanonic 300769.SZ / Ronbay 688005.SS)",
        "Anode": "JP (Mitsubishi Chemical 4188.T / Showa Denko 4004.T), KR (포스코퓨처엠 / 대주전자재료 078600.KQ Si-C)",
        "Separator": "JP (Asahi Kasei 3407.T / Toray 3402.T), KR (SK이노베이션 096770.KS / WCP 393890.KQ)",
        "Electrolyte": "KR (천보 278280.KQ / 솔브레인 357780.KQ / 동화기업 025900.KQ), JP (Mitsubishi Chemical)",
        "Lithium": "AU (Pilbara PLS.AX / IGO IGO.AX), CL (SQM), US (Albemarle ALB / Lithium Americas LAC)",
        "OEM": "US (Tesla TSLA / Rivian RIVN), CN (BYD 1211.HK / Li Auto LI / NIO), DE (Volkswagen VOW3.DE / BMW BMW.DE), KR (Hyundai 005380.KS / Kia 000270.KS), JP (Toyota 7203.T)",
        "Charging infra": "US (ChargePoint CHPT / EVgo EVGO / Blink BLNK), EU (Allego ALLG / Compleo 시장 OTC)",
        "SiC / GaN power": "US (Wolfspeed WOLF / onsemi ON), CH (STMicroelectronics STM), JP (Rohm 6963.T / Renesas 6723.T)",
    },
}
