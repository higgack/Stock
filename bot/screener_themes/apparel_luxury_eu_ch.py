"""EU/CH 시계 + 보석 — L4 under Apparel, Accessories & Luxury Goods (의류, 액세서리 및 명품).

자동 생성(부모 L3 apparel_luxury 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "EU/CH 시계 + 보석 (Apparel, Accessories & Luxury Goods)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["시계", "보석"],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "EU/CH 시계 + 보석 — 유럽 (LVMH MC.PA · Hermès RMS.PA · Kering KER.PA · Richemont CFR.SW · Moncler MONC.MI · Burberry BRBY.L · Christian Dior CDI.PA · Brunello Cucinelli BC.MI)",
        "EU/CH 시계 + 보석 — 홍콩 (Prada 1913.HK)",
    ],
    "catalyst_types": [
        "LVMH/Hermès/Kering 분기 organic growth + 中国 매출 동향",
        "Hermès 분기 Birkin 대기열 + 인도 / 두바이 신규 매장 가이드",
        "Nike/Lululemon 분기 매출 + 中国 시장 회복 + 베트남 capacity",
        "美 inbound 관광 회복 (TSA 통과량 + 호텔 occupancy) → 핸드백 + 시계 매출",
        "EU 럭셔리 부가세 환급 (Tax Free) + 中国 hainan free trade 자유무역지대 매출",
        "Tapestry-Capri 인수 합의 차단 항소심 + LVMH-Tiffany 후속 인수",
    ],
    "regional_concentration": {
        "EU/CH 시계 + 보석 (유럽)": "LVMH MC.PA · Hermès RMS.PA · Kering KER.PA · Richemont CFR.SW · Moncler MONC.MI · Burberry BRBY.L · Christian Dior CDI.PA · Brunello Cucinelli BC.MI · Salvatore Ferragamo SFER.MI · Tod's TOD.MI · Pandora PNDORA.CO · Hugo Boss BOSS.DE",
        "EU/CH 시계 + 보석 (홍콩)": "Prada 1913.HK",
    },
}
