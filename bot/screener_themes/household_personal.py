"""Household & Personal Products — L3 under Consumer Staples.

P&G · Colgate · Kimberly-Clark · Estée Lauder · LVMH 화장품 자회사 +
韩 K-beauty + JP Shiseido. 中国 outbound 관광 회복 + 美 인플레이션 +
GLP-1 induced 화장품 spend 변화가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Household & Personal Products (가정 및 개인용품)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "household_personal",
        "household",
        "생활용품",
        "personal_care",
        "개인용품",
        "cosmetic_l3",
        "화장품_l3",
        "k_beauty",
        "케이뷰티",
        "skincare",
        "스킨케어",
    ],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Household Cleaning + Paper (P&G · Kimberly-Clark · Clorox · Reckitt)",
        "Oral Care + Hygiene (Colgate · P&G · Church & Dwight)",
        "Premium Cosmetics (Estée Lauder · LVMH · L'Oréal · Coty · Shiseido)",
        "Mass Cosmetics + Personal Care (Coty · elf · Inter Parfums)",
        "Korea K-Beauty (LG생활건강 · 아모레퍼시픽 · 코스맥스 · 한국콜마 · 클리오)",
        "Japan Cosmetics + Skincare (Shiseido · Kao · Pola Orbis · Kosé · Pigeon)",
    ],
    "catalyst_types": [
        "P&G/Unilever/L'Oréal 분기 organic growth + 중국 시장 회복 데이터",
        "Estée Lauder/L'Oréal 분기 中国 + 일본 inbound 관광 매출 (Hainan + 면세점)",
        "K-Beauty 글로벌 매출 확장 (미국 + 동남아 + 일본) + 아모레/LG생활건강",
        "美 인플레이션 영향 + minimum wage 인상 → mass cosmetic 점유율 변화",
        "elf Beauty / Coty / Bath & Body 분기 매출 + 美 sephora/ulta 입점 확대",
        "GLP-1 induced 미용 spend 변화 (살빠짐 → 의류·화장품 수요 증가)",
    ],
    "regional_concentration": {
        "Household Cleaning + Paper": "Procter & Gamble PG · Colgate-Palmolive CL · Kimberly-Clark KMB · Clorox CLX · Church & Dwight CHD · Edgewell Personal Care EPC · Newell Brands NWL · Spectrum Brands SPB · Energizer ENR · Reckitt RKT.L · Henkel HEN3.DE",
        "Premium Cosmetics": "Estée Lauder EL · LVMH MC.PA (Christian Dior · Givenchy · Fenty) · L'Oréal OR.PA · Coty COTY · Shiseido 4911.T · POLA Orbis 4927.T · Kao 4452.T · Kosé 4922.T · Beiersdorf BEI.DE (Nivea) · Sally Beauty SBH · Olaplex OLPX · Helen of Troy HELE · Inter Parfums IPAR · Honest Company HNST · Edgewell EPC",
        "Mass Cosmetics + Direct-to-Consumer": "elf Beauty ELF · Coty COTY · Revlon 비상장 (파산) · e.l.f. Beauty ELF · Honest Company HNST · Sundial 비상장 (BIPOC 화장품) · Bath & Body Works BBWI",
        "Korea K-Beauty": "LG생활건강 051900.KS · 아모레퍼시픽 090430.KS · 코스맥스 192820.KS · 한국콜마 161890.KS · 클리오 237880.KQ · 신세계인터내셔날 031430.KS · 휴젤 145020.KQ · 메디톡스 086900.KQ · 파마리서치 214450.KQ · 닥터자르트-해브앤비 비상장 · 아모레G 002790.KS · 잇츠한불 226320.KQ · 토니모리 214420.KQ",
        "Japan Cosmetics + Skincare": "Shiseido 4911.T · Kao 4452.T · Lion 4912.T · POLA Orbis 4927.T · Kosé 4922.T · Pigeon 7956.T · Mandom 4917.T · Fancl 4921.T · Albion 비상장 · DHC 비상장 · Pola 비상장",
        "EU + Mass Brand": "L'Oréal OR.PA · Beiersdorf BEI.DE · Henkel HEN3.DE · Reckitt RKT.L · Unilever ULVR.L · LVMH MC.PA · Coty COTY (US ADR) · Salvatore Ferragamo SFER.MI · Ferragamo SFER.MI · Christian Dior CDI.PA",
        "DTC + Direct Sales": "Avon 비상장 (Natura 자회사) · Mary Kay 비상장 · Natura NTCO · Beauty Health SKIN · Olaplex OLPX · Honest HNST · BellRing Brands BRBR (Premier Protein · Dymatize) · Edgewell EPC",
    },
}
