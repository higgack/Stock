"""EDA & Semiconductor IP — L4 under Semiconductors & Equipment (Technology).

설계 자동화 툴 (synthesis · P&R · verification) + 반도체 IP (CPU/GPU 코어 ·
인터페이스 IP · 칩렛). AI 칩 설계 폭증·license backlog·中 EDA 분리가 dominant.
부모 L3 = semiconductors.
"""

from __future__ import annotations

THEME = {
    "domain": "EDA & Semiconductor IP (설계 자동화·반도체 IP)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["eda_ip", "설계자동화", "semi_ip", "chiplet_ip",
                "반도체ip", "eda_l4", "설계툴"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "EDA 툴 (synthesis · place&route · 검증 · DFT)",
        "프로세서 IP (CPU/GPU/NPU 코어 라이선스 — Arm 등)",
        "인터페이스·칩렛 IP (PCIe/UCIe/HBM PHY · 칩렛 인터커넥트)",
        "멀티피직스·패키징 시뮬레이션 (열·전자기 · 3D 패키징)",
    ],
    "catalyst_types": [
        "AI 칩 설계 폭증 → EDA license·royalty backlog (Synopsys/Cadence)",
        "Arm 라이선스·로열티 (스마트폰 + 데이터센터 CPU 침투율)",
        "UCIe 칩렛 표준 채택 + 인터페이스 IP 수요 (선단 노드 비용↑)",
        "美 BIS 對中 EDA 수출 규제 + 中 국산 EDA(Empyrean) 분리 영향",
    ],
    "regional_concentration": {
        "EDA 툴": "Synopsys SNPS · Cadence Design CDNS · Siemens EDA(SIE.DE) · "
                "Empyrean 301269.SZ(中)",
        "프로세서·인터페이스 IP": "Arm Holdings ARM · Cadence CDNS(IP) · "
                          "Synopsys SNPS(IP) · CEVA CEVA · Alphawave AWE.L · "
                          "VeriSilicon 688521.SS",
        "시뮬레이션": "Ansys ANSS(Synopsys 인수) · Cadence CDNS · Synopsys SNPS",
    },
}
