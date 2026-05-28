"""Transportation & Logistics — L3 under Industrials.

Rail + Trucking + Air Freight + Marine Shipping. 美 ATA tonnage +
컨테이너 spot rate (SCFI/Drewry) + Red Sea / Suez 우회 지속 + 美
국제선 yield 가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Transportation & Logistics (운송 및 물류)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "transport_logistics",
        "transportation",
        "운송_l3",
        "logistics_l3",
        "물류_l3",
        "rail",
        "철도",
        "trucking",
        "트럭",
        "shipping",
        "해운_l3",
        "air_freight",
        "항공화물",
    ],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Railroads (Union Pacific · CSX · NSC · CN · CP)",
        "Trucking & Ground (ODFL · Knight-Swift · J.B. Hunt · XPO · Saia)",
        "Air Freight & Logistics (UPS · FedEx · CH Robinson · Expeditors · DHL)",
        "Marine Shipping (ZIM · Matson · Kirby · Hapag-Lloyd · 한국HMM)",
        "Ride-share / Last-mile (Uber · Lyft · Instacart · Doordash)",
    ],
    "catalyst_types": [
        "ATA 트럭 tonnage index + DAT spot rate (trucking 회복 시점)",
        "AAR 분기 carload + intermodal (rail volume) + service score",
        "SCFI / Drewry 컨테이너 spot rate + Red Sea/Suez 우회 지속",
        "Jet fuel 가격 + 美 국제선 yield + Cargo (FedEx/UPS) 분기 가이드",
        "US-China 무역 deal + Section 301 관세 변경 → trans-Pacific 컨테이너 flow",
        "美 reshoring + 멕시코 nearshoring → rail · trucking 신규 corridors",
    ],
    "regional_concentration": {
        "US Rail": "Union Pacific UNP · CSX CSX · Norfolk Southern NSC · Canadian National CNI · Canadian Pacific Kansas CP",
        "US Trucking": "Knight-Swift KNX · Old Dominion ODFL · J.B. Hunt JBHT · Saia SAIA · ArcBest ARCB · XPO XPO · Werner WERN · Schneider SNDR · Heartland HTLD · Hub Group HUBG · TFI International TFII · Forward Air FWRD",
        "US Air Freight": "UPS UPS · FedEx FDX · C.H. Robinson CHRW · Expeditors EXPD · GXO Logistics GXO · Forward Air FWRD · Hub Group HUBG · Atlas Air 비상장 · Air Transport Services ATSG",
        "Marine Shipping Container": "ZIM ZIM · Matson MATX · A.P. Moller-Maersk MAERSK-B.CO · Hapag-Lloyd HLAG.DE · COSCO Shipping 601919.SS · Evergreen 2603.TW · Yang Ming 2609.TW · Wan Hai 2615.TW · HMM 011200.KS",
        "Marine Shipping Bulk + Tanker": "Star Bulk SBLK · Frontline FRO · Scorpio Tankers STNG · DHT DHT · Genco GNK · International Seaways INSW · Tsakos TNP · Costamare CMRE · Eagle Bulk EGLE · Mitsui OSK 9104.T · NYK 9101.T · K Line 9107.T",
        "Ride-share + last-mile": "Uber UBER · Lyft LYFT · DoorDash DASH · Instacart CART · Grab GRAB · Lalamove 비상장 · Delivery Hero DHER.DE · Just Eat Takeaway TKWY.AS",
        "Europe Logistics": "DHL Group DHL.DE · DSV DSV.CO · Kuehne+Nagel KNIN.SW · CMA CGM 비상장 · Deutsche Post DPW.DE",
    },
}
