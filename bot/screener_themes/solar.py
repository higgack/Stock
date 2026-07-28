"""Solar, Wind, ESS & Grid — Wave 1 도메인.

ToC 관점:
 1) 태양광 — 中国 가치사슬 (Poly-Si → Wafer → Cell → Module) 이 글로벌
    cost curve 를 80%+ 점유 → 본질적으로 deflation cycle 산업. 진짜
    rent 독식은 (a) 美/EU 反倾销 + section 45X 후 부활하는 美 cell·
    module 캐파, (b) 인버터 (Sungrow / Enphase / SolarEdge), (c)
    tracker (Array / Nextracker — 토지 평탄화 무관) sub-layer.
 2) 풍력 — Onshore 가 LCOE 경쟁력 회복 (낮은 금리 + capacity factor 개선).
    Offshore 는 Vestas / Ørsted 의 supply chain 회복이 binding (북해
    프로젝트 취소·재입찰). Wind 터빈 OEM 보다 cable (Prysmian / Nexans)
    + foundation (Cadeler) 가 진짜 capacity bottleneck.
 3) ESS (배터리 저장) — EV 셀 LFP 확산이 ESS 가격 동시 deflation. KR
    셀 3사 + CN CATL/BYD ESS 매출 분리. 핵심은 (a) ESS integrator
    (Fluence FLNC / Sungrow), (b) 1-hour vs 4-hour duration mix, (c)
    송전망 interconnection queue 적체.
 4) 전력망 (Grid) — 그리드 upgrade capex 사이클 → 변압기 / cable / GIS
    스위치기어 / SCADA 직접 수혜. AI Data Center Wave 0 (bottleneck.py)
    의 'Power' layer 와 일부 겹치지만, 신재생 commitment 가 ISO
    interconnection backlog 까지 끌어올린 형태로 표면 동인이 다름.

Catalyst weighting: 美 IRA section 45X production credit 의 다음 가이드
+ Trump 행정명령 후 IRA roll-back 일정이 최우선. EU CBAM tariff 효력 +
中国 反倾销 (multi-crystalline polysilicon / glass) 가 cost curve 직접
이동. 풍력 NTP (Notice To Proceed) + 입찰 결과 (BOEM lease / 한국 풍력
RFP) 가 EU/한국/대만 풍력 OEM 매출 trigger.
"""

from __future__ import annotations

THEME = {
    "domain": "Solar, Wind, ESS & Grid",
    "layer": "L1_TREND",
    "aliases": [
        "solar",
        "wind",
        "renewable",
        "renewables",
        "신재생",
        "재생에너지",
        "ess",
        "grid",
        "전력망",
        "태양광",
        "풍력",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Polysilicon + Wafer (上游 cost curve)",
        "Solar cell + Module (mono / TOPCon / HJT)",
        "태양광 인버터 (string / micro / DC optimizer)",
        "Tracker + BOS (Balance of System)",
        "Wind turbine OEM (onshore / offshore)",
        "Wind cable + foundation + installation vessel",
        "ESS integrator + LFP 셀 (1h / 4h duration)",
        "전력망 (Transformer / GIS / HVDC cable / SCADA)",
    ],
    "catalyst_types": [
        "美 IRA section 45X production credit 가이드 / Trump 행정명령 IRA roll-back",
        "EU CBAM 효력 일정 + 中国 반덤핑 (multi-c polysilicon / 유리)",
        "BOEM offshore wind lease + Notice To Proceed + 한국 풍력 RFP",
        "ISO interconnection queue + 美 송전망 신규 line 승인 (CAISO / ERCOT)",
        "유럽 RePowerEU + 독일 EEG 보조금 + 영국 CfD AR6 입찰 결과",
        "ESS interconnection backlog + AESO/CAISO 4h duration 비중",
        "中国 분산형 PV 보조금 변경 + 14차 5개년 풍력·솔라 caparmedoel 가동률",
    ],
    "regional_concentration": {
        "Polysilicon + Wafer (上游)": "CN (Tongwei 600438.SS / Daqo DQ / GCL-Poly 3800.HK / TCL Zhonghuan 002129.SZ)",
        "Solar cell + Module (CN)": "CN (LONGi 601012.SS / JinkoSolar JKS / Trina Solar 688599.SS / Canadian Solar CSIQ / JA Solar 002459.SZ)",
        "Solar cell + Module (US)": "US (First Solar FSLR — thin-film 美 캐파 + IRA 45X 직접 수혜)",
        "태양광 인버터": "CN (Sungrow 300274.SZ), US (Enphase ENPH / SolarEdge SEDG), DE (SMA Solar S92.DE)",
        "Tracker + BOS": "US (Nextracker NXT / Array ARRY / Shoals SHLS)",
        "Wind turbine OEM": "DK (Vestas VWS.CO), DE (Siemens Energy ENR.DE — Gamesa 자회사 / Nordex NDX1.DE), CN (Goldwind 2208.HK / Mingyang 601615.SS), US (GE Vernova GEV)",
        "Wind cable + installation": "IT (Prysmian PRY.MI), FR (Nexans NEX.PA), DK (Cadeler CADLR.OL — WTIV jackup)",
        "Offshore developer": "DK (Ørsted ORSTED.CO), ES (Iberdrola IBE.MC), DE (RWE RWE.DE)",
        "ESS integrator": "US (Fluence FLNC / Tesla TSLA Megapack / Stem STEM), CN (Sungrow), KR (LG엔솔 373220.KS)",
        "ESS LFP 셀": "CN (CATL 300750.SZ / BYD 002594.SZ / EVE 300014.SZ), KR (삼성SDI 006400.KS — 美 캐파)",
        "전력망 변압기 + GIS": "US (Eaton ETN / Hubbell HUBB), EU (Siemens Energy ENR.DE / ABB ABBN.SW / Schneider SU.PA), JP (Hitachi 6501.T / Mitsubishi Electric 6503.T), KR (HD현대일렉트릭 267260.KS / 효성중공업 298040.KS / LS ELECTRIC 010120.KS)",
        "HVDC + 전력망 cable": "IT (Prysmian PRY.MI), FR (Nexans NEX.PA), JP (Fujikura 5803.T)",
    },
}
