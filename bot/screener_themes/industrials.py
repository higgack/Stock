"""Industrials — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (8):
 1. Aerospace & Defense
 2. Airlines
 3. Building Products & Construction
 4. Electrical Equipment
 5. Commercial & Professional Services
 6. Machinery, Tools & Components
 7. Transportation & Logistics
 8. Waste & Environmental Services

Wave 1 trend `defense.py` (재무장 cycle niche) 와는 별개 lens — 본 모듈
은 산업재 sector 전체. 사용자가 의도 (cycle 베팅 vs sector 전체) 에 맞는
view 선택. 다국적 종목 분포 (US/KR/JP/EU/CN) 는 각 L3 sub-industry 별로
regional_concentration 에 명시 — Pro 가 web search 로 추가 글로벌 종목
발굴 + yfinance 검증 + tier 강제.
"""

from __future__ import annotations

THEME = {
    "domain": "Industrials (산업재)",
    "layer": "L2_SECTOR",
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
        "Aerospace & Defense (항공우주 및 방위)",
        "Airlines (항공)",
        "Building Products & Construction (건축 제품 및 건설)",
        "Electrical Equipment (전력기기)",
        "Commercial & Professional Services (상업 및 전문 서비스)",
        "Machinery, Tools & Components (기계, 도구 및 부품)",
        "Transportation & Logistics (운송 및 물류)",
        "Waste & Environmental Services (폐기물 및 환경 서비스)",
    ],
    "catalyst_types": [
        "Boeing 737 MAX 월별 출하량 + Airbus A320neo backlog 회복 + 中 COMAC C919 양산 ramp",
        "美 DoD FY 예산 + Ukraine·Israel·Taiwan 추경 + EU NATO 2% → 3% 목표",
        "美 인프라 bill (CHIPS + IIJA) 집행률 + 데이터센터 construction starts + 주거+상업 capex",
        "HVAC R-454B 냉매 전환 (2025 effective) — AC replacement cycle 강제",
        "ATA 트럭 tonnage index + DAT spot rate (trucking 회복) + AAR carload + intermodal (rail)",
        "SCFI / Drewry 컨테이너 spot rate + jet fuel + 美 국제선 yield",
        "美 BLS Wage Cost Index + ADP 비농업 고용 (HR/staffing 매출 직결)",
        "Hubble/JWST 위성 발사 + Starlink V2 + 中 14차 5개년 우주산업 보조금",
    ],
    "regional_concentration": {
        "Aerospace & Defense": "US (Boeing BA · Lockheed Martin LMT · RTX RTX · Northrop NOC · General Dynamics GD · L3Harris LHX · Howmet HWM · TransDigm TDG · HII HII · Spirit AeroSystems SPR · Textron TXT), EU (Airbus AIR.PA · Safran SAF.PA · Rolls-Royce RR.L · BAE BA.L · Rheinmetall RHM.DE · Leonardo LDO.MI · Thales HO.PA · Saab SAAB-B.ST · MTU MTX.DE), BR (Embraer ERJ), KR (한화에어로 012450.KS · LIG넥스원 079550.KS · KAI 047810.KS · 한화시스템 272210.KS · 현대로템 064350.KS), JP (MHI 7011.T · IHI 7013.T · Kawasaki Heavy 7012.T)",
        "Airlines": "US (Delta DAL · United UAL · American AAL · Southwest LUV · Alaska ALK · JetBlue JBLU · Allegiant ALGT), EU (Lufthansa LHA.DE · IAG IAG.L · Air France-KLM AF.PA · Ryanair RYAAY · easyJet EZJ.L), KR (대한항공 003490.KS · 아시아나 020560.KS · 제주항공 089590.KS · 진에어 272450.KS), JP (ANA 9202.T · JAL 9201.T), CN (China Southern ZNH · 国航 0753.HK · 东方航空 0670.HK), 中동 (Emirates 비상장 · Qatar Airways 비상장)",
        "Building Products & Construction": "US (Carrier CARR · Johnson Controls JCI · Trane TT · Otis OTIS · Lennox LII · A.O. Smith AOS · Owens Corning OC · Masco MAS · Vulcan VMC · Martin Marietta MLM · Quanta PWR · EMCOR EME · Jacobs J · MasTec MTZ · Fluor FLR · AECOM ACM · KBR KBR · Sterling STRL · Comfort Systems FIX), CH (Sika SIK.SW), JP (Daikin 6367.T)",
        "Electrical Equipment": "US (Eaton ETN · Emerson EMR · Rockwell Automation ROK · Vertiv VRT · GE GE · Hubbell HUBB · nVent NVT · Atkore ATKR · Generac GNRC), EU (Schneider SU.PA · ABB ABBN.SW · Siemens SIE.DE · Siemens Energy ENR.DE · Legrand LR.PA), KR (HD현대일렉트릭 267260.KS · 효성중공업 298040.KS · LS ELECTRIC 010120.KS · 일진전기 103590.KS), JP (Mitsubishi Electric 6503.T · Fuji Electric 6504.T · Hitachi 6501.T)",
        "Commercial & Professional Services": "US — HR/Employment (ADP ADP · Paychex PAYX · Robert Half RHI · ManpowerGroup MAN · Insperity NSP), Research/Consulting (Verisk VRSK · Gartner IT · CoStar CSGP · Booz Allen BAH · McKinsey 비상장), Security/Facility (Cintas CTAS · ADT ADT · Brinks BCO), EU (Adecco ADEN.SW · Randstad RAND.AS · Capgemini CAP.PA · WPP WPP.L), JP (Recruit 6098.T · Persol 2181.T)",
        "Machinery, Tools & Components": "US (Caterpillar CAT · Deere DE · Cummins CMI · PACCAR PCAR · ITW ITW · Parker-Hannifin PH · Stanley B&D SWK · Dover DOV · Ingersoll Rand IR · Fortive FTV · AGCO AGCO · Snap-on SNA · Graco GGG), JP (Komatsu 6301.T · Kubota 6326.T · Hitachi Construction 6305.T · Fanuc 6954.T · Yaskawa 6506.T · Keyence 6861.T · SMC 6273.T · DENSO 6902.T), DE (Siemens SIE.DE · Bosch 비상장), CH (Bobst BOBNN.SW)",
        "Transportation & Logistics": "Rail US (Union Pacific UNP · CSX CSX · Norfolk Southern NSC) + CA (CN CNI · CP CP) + MX (Grupo Mexico GMEXICOB.MX), Trucking (Knight-Swift KNX · ODFL ODFL · J.B. Hunt JBHT · Saia SAIA · ArcBest ARCB · XPO XPO · Uber UBER · Lyft LYFT), Air Freight (UPS UPS · FedEx FDX · CH Robinson CHRW · Expeditors EXPD · GXO GXO · DHL DHL.DE), Marine Shipping US (Matson MATX · Kirby KEX · ZIM ZIM) + KR (HMM 011200.KS) + JP (Mitsui OSK 9104.T · NYK 9101.T · K Line 9107.T) + DE (Hapag-Lloyd HLAG.DE)",
        "Waste & Environmental Services": "US (Waste Management WM · Republic Services RSG · Waste Connections WCN · Clean Harbors CLH · Stericycle SRCL · Casella CWST), CA (GFL Environmental GFL), EU (Veolia VIE.PA · Suez 비상장 · Biffa BIFF.L)",
    },
}
