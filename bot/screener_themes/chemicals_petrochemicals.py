"""Petrochemicals — L4 under Chemicals (화학).

자동 생성(부모 L3 chemicals 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Petrochemicals (Chemicals)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": [],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Petrochemicals — 미국 (Dow DOW · LyondellBasell LYB · Westlake WLK · Trinseo TSE · Methanex MEOH · Olin OLN · CIMC 2039.HK))",
        "Petrochemicals — 유럽 (Methanex MEOH, EU (BASF BAS.DE)",
        "Petrochemicals — 일본 (Bayer BAYN.DE), JP (Mitsubishi Chemical 4188.T · Sumitomo Chemical 4005.T · Shin-Etsu 4063.T · Toray 3402.T · Asahi Kasei 3407.T)",
        "Petrochemicals — 한국 (Kuraray 3405.T), KR (LG화학 051910.KS · 롯데케미칼 011170.KS · 한화솔루션 009830.KS · 효성첨단소재 298050.KS · OCI 010060.KS · 금호석유 011780.KS)",
        "Petrochemicals — 중국 (대한유화 006650.KS), CN (Wanhua 600309.SS · Hengyi 000703.SZ)",
    ],
    "catalyst_types": [
        "美 IRA 45X (battery cathode/anode/cell) + 분기 PFAS 규제 + EU CBAM",
        "리튬 · 코발트 · 니켈 가격 사이클 → 배터리 소재 매출 직접",
        "美 farm bill 통과 + 비료 (질소 · 인산 · 칼륨) 가격 사이클",
        "분기 ethylene + propylene + benzene 가격 spread + 美 멕시코만 hurricane risk",
        "中国 반덤핑 (multi-c polysilicon · 유리 · titanium dioxide) 효력 시점",
        "美 HBM/CoWoS 패키지 capex → 특수가스 + 포토레지스트 + 슬러리 수요",
    ],
    "regional_concentration": {
        "Petrochemicals (미국)": "Dow DOW · LyondellBasell LYB · Westlake WLK · Trinseo TSE · Methanex MEOH · Olin OLN · CIMC 2039.HK)",
        "Petrochemicals (유럽)": "Methanex MEOH, EU (BASF BAS.DE",
        "Petrochemicals (일본)": "Bayer BAYN.DE), JP (Mitsubishi Chemical 4188.T · Sumitomo Chemical 4005.T · Shin-Etsu 4063.T · Toray 3402.T · Asahi Kasei 3407.T",
        "Petrochemicals (한국)": "Kuraray 3405.T), KR (LG화학 051910.KS · 롯데케미칼 011170.KS · 한화솔루션 009830.KS · 효성첨단소재 298050.KS · OCI 010060.KS · 금호석유 011780.KS",
        "Petrochemicals (중국)": "대한유화 006650.KS), CN (Wanhua 600309.SS · Hengyi 000703.SZ",
    },
}
