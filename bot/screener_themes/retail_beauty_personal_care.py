"""Beauty + Personal Care — L4 under Retail (소매).

자동 생성(부모 L3 retail 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Beauty + Personal Care (Retail)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["beauty_personal"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Beauty + Personal Care — 미국 (Ulta Beauty ULTA · Sally Beauty SBH · Coty COTY · Estée Lauder EL · Inter Parfums IPAR · elf Beauty ELF · Olaplex OLPX · Honest Company HNST)",
        "Beauty + Personal Care — 비상장 (Sephora 비상장 (LVMH))",
        "Beauty + Personal Care — 유럽 (L'Oréal OR.PA)",
    ],
    "catalyst_types": [
        "Amazon 분기 매출 + AWS 분리 + 광고 매출 + Prime 회원 증가",
        "미국 소매 same-store sales (Census) + 분기 Black Friday/Cyber Monday",
        "美 인플레이션 영향 + minimum wage 인상 → 소매 영업 마진",
        "Home Depot/Lowe's 분기 same-store sales + 美 주택 거래량 + DIY 동향",
        "Costco 분기 멤버십 갱신율 + e-commerce 침투율 + Kirkland 자체 브랜드",
        "Shein/Temu 美 침투율 + de minimis 관세 정책 변경 → 美 e-commerce 영향",
    ],
    "regional_concentration": {
        "Beauty + Personal Care (미국)": "Ulta Beauty ULTA · Sally Beauty SBH · Coty COTY · Estée Lauder EL · Inter Parfums IPAR · elf Beauty ELF · Olaplex OLPX · Honest Company HNST",
        "Beauty + Personal Care (비상장)": "Sephora 비상장 (LVMH)",
        "Beauty + Personal Care (유럽)": "L'Oréal OR.PA",
    },
}
