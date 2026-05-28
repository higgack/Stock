"""Industrials — Wave 2 broad sector (Finviz Industrials).

Finviz Industrials 15+ industries: Aerospace & Defense · Specialty Industrial
Machinery · Building Products & Equipment · Industrial Distribution ·
Engineering & Construction · Electrical Equipment & Parts · Railroads ·
Trucking · Airlines · Marine Shipping · Air Freight & Logistics ·
Conglomerates · Farm & Heavy Construction Machinery · Pollution & Treatment
Controls · Waste Management · Staffing & Employment.

ToC 관점 — Wave 1 의 defense.py (재무장 cycle niche) 와 별개 lens:
 1) Aerospace & Defense — defense 모듈이 짚지 못한 commercial aviation
    (BA 737 MAX 회복 / Airbus capacity / Embraer regional) 포함.
 2) Heavy Machinery (CAT/DE/CMI) — 美 인프라 bill + IRA + 데이터센터
    construction starts. 농기계 cycle (DE / AGCO) 는 commodity 가격 의존.
 3) Building Products & HVAC (CARR/JCI/OTIS/TT/AOS) — R-454B 냉매 전환
    (2025 effective) → AC unit replacement 사이클 강제. 데이터센터
    cooling 수요 추가. HVAC 가 binding.
 4) Rail (UNP/CSX/NSC/CP/CNI) — Tucson-Pacific 합병 (2024 closed) +
    inter-modal 회복. Service quality 가 차별화.
 5) Trucking (KNX/ODFL/JBHT/XPO) — spot rate cycle bottom 통과 + Yellow
    bankruptcy 이후 capacity 재분배.
 6) Airlines + Air Freight — 美 국제선 yield 정점 + UPS/FedEx 가격
    인상 power 회복.
 7) Marine Shipping (ZIM/KEX/SBLK) — Red Sea/Suez 우회 지속 + 컨테이너
    spot rate (SCFI/Drewry) 변동성.
 8) Conglomerates - GE 분할 완료 (GE 가 GE Aerospace, GE Vernova, GE
    HealthCare 로 split) → pure-play 재평가.
"""

from __future__ import annotations

THEME = {
    "domain": "Industrials (Finviz Sector — A&D · Machinery · HVAC · Rail · Trucking · Airlines · Shipping)",
    "aliases": [
        "industrials",
        "industrial",
        "산업재",
        "machinery",
        "기계",
        "construction",
        "건설",
        "aerospace",
        "rail",
        "철도",
        "trucking",
        "운송",
        "airlines",
        "항공",
        "shipping",
        "해운",
        "hvac",
        "logistics",
        "물류",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Aerospace & Defense (Boeing 회복 + Airbus capacity)",
        "Specialty Industrial Machinery (CAT/DE/CMI capex cycle)",
        "Building Products & HVAC (R-454B 냉매 전환)",
        "Industrial Distribution (FAST/GWW/MSM)",
        "Engineering & Construction (인프라 bill + 데이터센터)",
        "Electrical Equipment (Eaton/Hubbell — Wave 1 AI DC 와 부분 중복)",
        "Railroads (UNP/CSX/NSC · service quality)",
        "Trucking + Air Freight + Logistics",
        "Airlines (yield cycle)",
        "Marine Shipping (Red Sea + 컨테이너 spot)",
        "Conglomerates (GE 분할 후 pure-play)",
    ],
    "catalyst_types": [
        "Boeing 737 MAX 월별 출하량 + Airbus A320neo backlog 회복",
        "美 인프라 bill (CHIPS + IIJA) 집행률 + 데이터센터 construction starts",
        "HVAC R-454B 냉매 전환 (2025 effective) — AC replacement cycle 강제",
        "분기 ATA 트럭 tonnage index + DAT spot rate (trucking 회복)",
        "AAR 분기 carload + intermodal (rail volume) + service score",
        "SCFI / Drewry 컨테이너 spot rate + jet fuel + 美 국제선 yield",
        "HVAC + Building Products 분기 backlog (CARR/JCI/OTIS)",
        "GE 분할 후 pure-play (GE Aerospace / Vernova / HealthCare) 재평가",
    ],
    "regional_concentration": {
        "Aerospace & Defense — Commercial": "US (Boeing BA / GE Aerospace GE / Howmet HWM / TransDigm TDG / Heico HEI / Curtiss-Wright CW / Spirit AeroSystems SPR), EU (Airbus AIR.PA / Safran SAF.PA / Rolls-Royce RR.L / MTU MTX.DE), BR (Embraer ERJ), CA (Bombardier BBD-B.TO)",
        "Aerospace & Defense — Military": "US (Lockheed Martin LMT / Northrop NOC / RTX RTX / General Dynamics GD / L3Harris LHX / Huntington Ingalls HII / Palantir PLTR / Kratos KTOS / AeroVironment AVAV), EU (BAE BA.L / Rheinmetall RHM.DE / Leonardo LDO.MI / Thales HO.PA / Saab SAAB-B.ST), IL (Elbit ESLT), KR (한화에어로 012450.KS / LIG넥스원 079550.KS / KAI 047810.KS / 한화시스템 272210.KS / 현대로템 064350.KS)",
        "Specialty Industrial Machinery": "US (Caterpillar CAT / Deere DE / Cummins CMI / Illinois Tool Works ITW / Parker Hannifin PH / Emerson EMR / Roper ROP / Rockwell ROK / Dover DOV / Xylem XYL / Pentair PNR / Watts Water WTS / Fortive FTV / Ametek AME), JP (Komatsu 6301.T / Kubota 6326.T / Hitachi Construction 6305.T / Daikin 6367.T / Fanuc 6954.T / Yaskawa 6506.T / Keyence 6861.T)",
        "Building Products & HVAC": "US (Carrier CARR / Johnson Controls JCI / Otis OTIS / Trane TT / Lennox LII / A.O. Smith AOS / Masco MAS / Allegion ALLE / Vulcan Materials VMC / Martin Marietta MLM / Eagle Materials EXP / Summit Materials SUM), CH (Sika SIK.SW)",
        "Industrial Distribution": "Fastenal FAST / W.W. Grainger GWW / MSC Industrial MSM / Ferguson FERG / Applied Industrial AIT",
        "Engineering & Construction": "US (AECOM ACM / Jacobs J / Fluor FLR / MasTec MTZ / EMCOR EME / Quanta Services PWR / Comfort Systems FIX / Granite GVA / Tutor Perini TPC / Sterling STRL / Construction Partners ROAD)",
        "Electrical Equipment & Parts": "US (Eaton ETN / Hubbell HUBB / Vertiv VRT / Acuity AYI / Generac GNRC / Atkore ATKR / nVent NVT / Encore Wire WIRE), EU (Schneider SU.PA / ABB ABBN.SW / Siemens SIE.DE / Siemens Energy ENR.DE / Legrand LR.PA / Prysmian PRY.MI), KR (HD현대일렉트릭 267260.KS / 효성중공업 298040.KS / LS ELECTRIC 010120.KS / 일진전기 103590.KS)",
        "Railroads": "US (Union Pacific UNP / CSX CSX / Norfolk Southern NSC), CA (Canadian Pacific Kansas CP / Canadian National CNI), MX (Grupo Mexico GMEXICOB.MX)",
        "Trucking": "Knight-Swift KNX / Old Dominion ODFL / J.B. Hunt JBHT / XPO XPO / Saia SAIA / Werner WERN / Schneider SNDR / Heartland HTLD / Hub Group HUBG / ArcBest ARCB",
        "Air Freight & Logistics": "UPS UPS / FedEx FDX / Expeditors EXPD / C.H. Robinson CHRW / Forward Air FWRD / GXO Logistics GXO, DE (DHL Group DHL.DE), KR (한진 002320.KS), JP (Yamato 9064.T / Nippon Express 9147.T)",
        "Airlines": "US (Delta DAL / United UAL / American AAL / Southwest LUV / Alaska ALK / JetBlue JBLU), AE (Emirates 비상장), EU (Lufthansa LHA.DE / IAG IAG.L / Air France-KLM AF.PA / Ryanair RYAAY), KR (대한항공 003490.KS / 아시아나 020560.KS), JP (ANA 9202.T / JAL 9201.T), CN (China Southern ZNH / 国航 0753.HK)",
        "Marine Shipping": "ZIM ZIM / Star Bulk SBLK / Frontline FRO / Scorpio Tankers STNG / DHT Holdings DHT / Costamare CMRE / Genco GNK / Eagle Bulk EGLE / International Seaways INSW, DE (Hapag-Lloyd HLAG.DE), KR (HMM 011200.KS), JP (Mitsui OSK 9104.T / NYK 9101.T / K Line 9107.T)",
        "Conglomerates": "GE Aerospace GE / GE Vernova GEV / GE HealthCare GEHC (GE 분할) / 3M MMM / Honeywell HON / Berkshire Hathaway BRK.B / Illinois Tool Works ITW, JP (Mitsubishi 8058.T / Mitsui 8031.T / Itochu 8001.T / Marubeni 8002.T / Sumitomo 8053.T), KR (삼성물산 028260.KS / SK 034730.KS / LG 003550.KS)",
    },
}
