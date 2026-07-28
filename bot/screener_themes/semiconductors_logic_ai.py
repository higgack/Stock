"""AI/Datacenter Logic — L4 under Semiconductors & Equipment (Technology).

AI 가속기 (GPU · 커스텀 ASIC/XPU · NPU) + 데이터센터 네트워킹 칩 + 고속
인터커넥트. 하이퍼스케일러 capex·엔비디아 출하·커스텀 ASIC 전환이 dominant.
부모 L3 = semiconductors.
"""

from __future__ import annotations

THEME = {
    "domain": "AI·Datacenter Logic (AI·데이터센터 로직 반도체)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["ai_logic", "npu", "ai_accelerator", "가속기반도체", "gpu_chip",
                "custom_asic", "xpu", "ai반도체"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "범용 AI 가속기 (GPU — 학습·추론, NVIDIA·AMD)",
        "커스텀 ASIC/XPU (하이퍼스케일러 자체 칩 설계 — Broadcom·Marvell)",
        "데이터센터 네트워킹·인터커넥트 (스위치 ASIC · DPU · NIC · CPO)",
        "AI 서버 CPU·IP (Arm 기반 서버 CPU · 칩렛 IP)",
    ],
    "catalyst_types": [
        "NVIDIA Blackwell/Rubin 출하량 + 데이터센터 매출 가이드",
        "하이퍼스케일러 AI capex (MSFT/META/GOOG/AMZN) + 커스텀 ASIC 비중↑",
        "Broadcom/Marvell 커스텀 실리콘 backlog + 이더넷 스위칭 점유",
        "美 BIS 對中 AI 칩 규제(H20 등) + 中 화웨이/캄브리콘 대체",
    ],
    "regional_concentration": {
        "범용 GPU": "NVIDIA NVDA · AMD AMD · Intel INTC(Gaudi) · "
                  "Cambricon 688256.SS(中)",
        "커스텀 ASIC·네트워킹": "Broadcom AVGO · Marvell Technology MRVL · "
                          "Astera Labs ALAB · Credo CRDO · Arm Holdings ARM",
        "스위치·인터커넥트": "Broadcom AVGO(Tomahawk) · Arista ANET · "
                        "Coherent COHR · Lumentum LITE(CPO 광)",
    },
}
