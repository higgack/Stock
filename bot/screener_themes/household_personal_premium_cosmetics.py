"""Premium Cosmetics — L4 under Household & Personal Products (가정 및 개인용품).

자동 생성(부모 L3 household_personal 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Premium Cosmetics (Household & Personal Products)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["premium_cosmetics", "cosmetics"],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Premium Cosmetics — 미국 (Estée Lauder EL · LVMH MC.PA (Christian Dior · Givenchy · Fenty) · Coty COTY · Beiersdorf BEI.DE (Nivea) · Sally Beauty SBH · Olaplex OLPX)",
        "Premium Cosmetics — 유럽 (L'Oréal OR.PA)",
        "Premium Cosmetics — 일본 (Shiseido 4911.T · POLA Orbis 4927.T · Kao 4452.T · Kosé 4922.T)",
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
        "Premium Cosmetics (미국)": "Estée Lauder EL · LVMH MC.PA (Christian Dior · Givenchy · Fenty) · Coty COTY · Beiersdorf BEI.DE (Nivea) · Sally Beauty SBH · Olaplex OLPX · Helen of Troy HELE · Inter Parfums IPAR · Honest Company HNST · Edgewell EPC",
        "Premium Cosmetics (유럽)": "L'Oréal OR.PA",
        "Premium Cosmetics (일본)": "Shiseido 4911.T · POLA Orbis 4927.T · Kao 4452.T · Kosé 4922.T",
    },
}
