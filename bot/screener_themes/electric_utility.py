"""Electric & Multi-Utilities — L3 under Utilities.

Regulated Electric (NextEra · Duke · Southern · Dominion) + Multi-utility
(WEC · Sempra · DTE). AI 데이터센터 전력 수요 + Texas heatwave + 美
NRC SMR 인허가 + Fed funds rate 가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Electric & Multi-Utilities (전력 및 복합 유틸리티)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "electric_utility",
        "electric_utilities",
        "전력유틸",
        "regulated_electric",
        "multi_utility",
        "복합유틸",
    ],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Mega-cap Regulated (NextEra · Duke · Southern · Dominion)",
        "Mid-cap Regulated (AEP · Sempra · Exelon · WEC · PSEG)",
        "Texas + ERCOT (Vistra · Constellation · NRG)",
        "California (Edison International · PG&E · Sempra)",
        "Multi-utility Combined (DTE · CMS · Eversource · CenterPoint)",
    ],
    "catalyst_types": [
        "Fed funds rate path → Utility 배당 매력 + 차입 비용 변화",
        "美 AI 데이터센터 전력 수요 (24/7 24GW+ by 2030) → IPP 매출 ramp",
        "Texas ERCOT + California CAISO heatwave + 송전망 신규 line approval",
        "美 NRC SMR 인허가 (NuScale · X-energy · Oklo · TerraPower) + 기존 원전 수명 연장",
        "분기 PUC 가격 인상 승인 + 새 capex plan 발표",
        "Trump 행정명령 - 美 송전망 + grid modernization 부활",
    ],
    "regional_concentration": {
        "Mega-cap Regulated US": "NextEra Energy NEE · Duke Energy DUK · Southern Company SO · Dominion Energy D · American Electric Power AEP · Exelon EXC · Sempra Energy SRE · Public Service Enterprise PEG · Xcel Energy XEL · Constellation Energy CEG (분리 후)",
        "Mid-cap Regulated US": "WEC Energy WEC · DTE Energy DTE · Eversource ES · FirstEnergy FE · Edison International EIX · PG&E PCG · AES Corp AES · Entergy ETR · CMS Energy CMS · CenterPoint CNP · Ameren AEE · Pinnacle West PNW · Alliant LNT · NiSource NI · Evergy EVRG · OGE Energy OGE · IDACORP IDA · Hawaiian Electric HE · MGE Energy MGEE · Black Hills BKH · Avista AVA · Portland General POR · NorthWestern NWE · Otter Tail OTTR",
        "Texas + ERCOT": "Vistra Corp VST · Constellation Energy CEG · NRG Energy NRG · Talen Energy TLN · Atlantic Power 비상장 · TransAlta TA.TO",
        "California Specific": "Edison International EIX · PG&E PCG · Sempra Energy SRE · California Water Service CWT · Pacific Gas & Electric (PG&E) PCG",
        "EU Utilities": "Iberdrola IBE.MC · Enel ENEL.MI · EDF 비상장 · EON.DE EON.DE · National Grid NG.L · SSE SSE.L · Engie ENGI.PA · Veolia VIE.PA · RWE RWE.DE · Iberdrola IBE.MC · EDP EDP.LS · Centrica CNA.L · Drax DRX.L",
        "Japan + Korea + China Utilities": "JP (Tokyo Electric Power 9501.T · Kansai Electric 9503.T · Chubu Electric 9502.T · Kyushu Electric 9508.T · Hokuriku 9505.T · Hokkaido 9509.T · Shikoku 9507.T · Chugoku 9504.T · Tohoku Electric 9506.T · Okinawa Electric 9511.T), KR (한국전력 015760.KS · 한전KPS 051600.KS · 한전기술 052690.KS · 한전산업개발 130660.KS), CN (Huaneng Power 0902.HK · Datang Power 0991.HK · Huadian Power 1071.HK · CR Power 0836.HK · CGN Power 1816.HK · China General Nuclear 1816.HK · China National Nuclear 601985.SS)",
    },
}
