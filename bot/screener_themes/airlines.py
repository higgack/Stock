"""Airlines — L3 under Industrials (미국 정식 분류).

여객 + 화물 항공 사이클. 美 국제선 yield + jet fuel + capacity 회복 +
중·동남아 inbound 관광 수요가 主 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Airlines (항공)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "airlines",
        "airline",
        "항공_l3",
        "여객항공",
    ],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "US Network Carriers (Delta · United · American — 국제선 yield)",
        "US Low-cost (Southwest · Alaska · JetBlue · Allegiant)",
        "EU Flag Carriers (Lufthansa · IAG · Air France-KLM)",
        "EU Low-cost (Ryanair · easyJet · Wizz)",
        "Asia Premium (ANA · JAL · 대한항공 · 싱가포르)",
        "Asia Low-cost (제주항공 · 진에어 · 春秋 · AirAsia)",
    ],
    "catalyst_types": [
        "美 국제선 yield + 중국·일본 inbound 관광 회복 데이터",
        "Jet fuel 가격 (Brent crack spread) + airline hedge book",
        "Boeing 737 MAX + Airbus A320neo 인도 지연 → capacity 부족 → yield 보호",
        "美 FAA ATC 인력 부족 + 항로 제한 + 슬롯 경매",
        "TSA 분기 통과량 + Skift Travel Index",
        "Ryanair / easyJet capacity allocation + 신규 base 발표",
    ],
    "regional_concentration": {
        "US Network": "Delta DAL · United UAL · American AAL",
        "US Low-cost": "Southwest LUV · Alaska ALK · JetBlue JBLU · Allegiant ALGT · Spirit SAVE · Frontier ULCC · Sun Country SNCY",
        "EU": "Lufthansa LHA.DE · IAG IAG.L · Air France-KLM AF.PA · Ryanair RYAAY · easyJet EZJ.L · Wizz Air WIZZ.L · SAS SAS.ST · Finnair FIA1S.HE · TAP TAP.LS",
        "Asia Premium": "ANA 9202.T · JAL 9201.T · 대한항공 003490.KS · 아시아나 020560.KS · Singapore Airlines C6L.SI · Cathay 0293.HK",
        "Asia Low-cost": "제주항공 089590.KS · 진에어 272450.KS · 티웨이 091810.KS · AirAsia AIRASIA.KL · Spring Air 春秋航空 601021.SS · IndiGo INDIGO.NS",
        "China": "China Southern ZNH · 国航 0753.HK · 东方航空 0670.HK · 海航 600221.SS",
        "Middle East/Gulf": "Emirates 비상장 · Qatar Airways 비상장 · Etihad 비상장",
    },
}
