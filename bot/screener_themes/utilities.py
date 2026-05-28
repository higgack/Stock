"""Utilities — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (3):
 1. Electric & Multi-Utilities (regulated electric + multi)
 2. Independent Power & Renewable Electricity Producers (IPP + Solar + Wind)
 3. Gas & Water Utilities (regulated gas + water)

Wave 1 trend `solar.py` (재생 cycle) 와는 부분 overlap (Renewable IPP) —
본 모듈은 유틸리티 sector 전체 lens.
"""

from __future__ import annotations

THEME = {
    "domain": "Utilities (유틸리티)",
    "layer": "L2_SECTOR",
    "aliases": [
        "utilities",
        "유틸리티",
        "utility",
        "전력",
        "electric",
        "ipp",
        "gas",
        "water",
        "수도",
        "regulated",
        "renewable_utility",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Electric & Multi-Utilities (전력 및 복합 유틸리티 — Regulated Electric + Multi-Utility)",
        "Independent Power & Renewable Electricity Producers (독립 발전 및 재생에너지 — IPP + Solar + Wind)",
        "Gas & Water Utilities (가스 및 수도 유틸리티)",
    ],
    "catalyst_types": [
        "Fed funds rate path → Utility 배당 매력 + 차입 비용 변화",
        "美 AI 데이터센터 전력 수요 (24/7 24GW+ by 2030) → IPP 매출 ramp",
        "Texas ERCOT + California CAISO heatwave + 송전망 신규 line approval",
        "美 IRA 45X (Solar/Wind production credit) + Trump 행정명령 IRA roll-back",
        "美 NRC SMR 인허가 (NuScale / X-energy / Oklo / TerraPower) + 기존 원전 수명 연장",
        "EU CBAM + ETS 가격 + 영국 CfD AR6 입찰 결과",
        "中国 14차 5개년 풍력·솔라 caparmedoel + 가동률 + 수출 통제",
        "美 PFAS 오염 정화 + EPA 수질 규제 + 미국 수도 인프라 capex",
    ],
    "regional_concentration": {
        "Electric & Multi-Utilities": "US Regulated Electric (NextEra Energy NEE · Duke Energy DUK · Southern Company SO · Dominion Energy D · American Electric Power AEP · Exelon EXC · Sempra Energy SRE · Public Service Enterprise PEG · Xcel Energy XEL · WEC Energy WEC · DTE Energy DTE · Eversource ES · FirstEnergy FE · Edison International EIX · PG&E PCG · AES AES · Entergy ETR · CMS Energy CMS · CenterPoint CNP · Ameren AEE · Pinnacle West PNW · Alliant LNT · NiSource NI · Evergy EVRG), EU (Iberdrola IBE.MC · Enel ENEL.MI · EDF 비상장 · EON.DE EON.DE · National Grid NG.L · SSE SSE.L · Engie ENGI.PA · Veolia VIE.PA · RWE RWE.DE), JP (Tokyo Electric Power 9501.T · Kansai Electric 9503.T · Chubu Electric 9502.T · Kyushu Electric 9508.T · Hokuriku 9505.T · Hokkaido 9509.T · Shikoku 9507.T · Chugoku 9504.T), KR (한국전력 015760.KS · 한전KPS 051600.KS · 한전기술 052690.KS · 한전산업개발 130660.KS), CN (Huaneng Power 0902.HK · Datang Power 0991.HK · Huadian Power 1071.HK · CR Power 0836.HK · CGN Power 1816.HK)",
        "Independent Power & Renewable Electricity Producers": "US IPP (Constellation Energy CEG · Vistra Corp VST · NRG Energy NRG · Calpine 비상장 · Talen Energy TLN), Solar (First Solar FSLR · Enphase ENPH · SolarEdge SEDG · Sunrun RUN · Sunnova NOVA · Array ARRY · Nextracker NXT · Maxeon Solar MAXN), Wind (NextEra Energy Partners NEP · Brookfield Renewable BEP · Clearway Energy CWEN · Bloom Energy BE), EU (Ørsted ORSTED.CO · Iberdrola IBE.MC · EDP EDP.LS · Enel ENEL.MI · RWE RWE.DE · Vestas VWS.CO · Siemens Energy ENR.DE · Nordex NDX1.DE), KR (한화솔루션 009830.KS · OCI 010060.KS · 신성이엔지 011930.KS · 두산퓨얼셀 336260.KS), JP (Renova 9519.T · West Holdings 1407.T)",
        "Gas & Water Utilities": "Gas US (Atmos Energy ATO · NiSource NI · Spire Inc SR · Southwest Gas SWX · UGI UGI · ONE Gas OGS · New Jersey Resources NJR · National Fuel Gas NFG · WGL Holdings 비상장), Water US (American Water Works AWK · Essential Utilities WTRG · California Water CWT · American States Water AWR · SJW Group SJW · Middlesex Water MSEX · York Water YORW · Xylem XYL · Veolia VIE.PA · Mueller Water MWA), EU Gas (Snam SRG.MI · Enagás ENG.MC · National Grid NG.L · Centrica CNA.L), KR (한국가스공사 036460.KS · 삼천리 004690.KS · 서울가스 017390.KS · 부산가스 015350.KS), JP Gas (Tokyo Gas 9531.T · Osaka Gas 9532.T · Toho Gas 9533.T)",
    },
}
