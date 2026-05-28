"""Quantum Computing — L1 Trend (2026-05-29 GICS check 추가).

GICS quarterly check (2026-Q2) 가 식별한 신규 emerging industry trend.
시총 ~$65B + 美 QED-C consortium 80+ 가입 + DARPA QBI 프로그램 + EU
Quantum Flagship + 中 国家 양자기관 catalyst. 상장 pure-play 등장
(IonQ · Rigetti · D-Wave · Quantum Computing Inc.) 으로 buy-side 별도
lens 분석 가치.

ToC 관점:
 1) Qubit hardware (IonQ 트랩이온 · Rigetti 초전도 · D-Wave annealing
    · IBM/Google 비상장 - 클라우드 통합). 각 modality 별 stability +
    qubit count race — 진짜 binding 은 error correction overhead.
 2) Cryogenics + 제어 전자 (Bluefors · Oxford Instruments · Keysight).
    Dilution refrigerator + RF/마이크로파 제어 — qubit count 증가 시
    핵심 capacity bottleneck. JP/CH/UK 강세.
 3) Quantum-safe cryptography (PQC) — NIST CRYSTALS-Kyber 표준 후
    네트워크 장비 (Cisco · Juniper) 업그레이드 사이클. IBM · MSFT
    Azure Quantum 의 클라우드 quantum 서비스 매출.
 4) Sensing + Metrology (atomic clock · gravimeter · magnetometer) —
    국방·자율주행·의료 imaging. 시장 작지만 niche.

Catalyst weighting: 美 QED-C / DARPA QBI 의 분기 capex 가이드 + IonQ /
Rigetti / Quantum Computing Inc. 분기 매출 가이드 + IBM Quantum
roadmap (10K+ qubit by 2029) + 中 国家 quantum lab 발표.
"""

from __future__ import annotations

THEME = {
    "domain": "Quantum Computing (양자 컴퓨팅)",
    "layer": "L1_TREND",
    "aliases": [
        "quantum",
        "quantum_computing",
        "양자",
        "양자컴퓨팅",
        "qubit",
        "큐비트",
        "pqc",
        "quantum_safe",
        "양자안전",
    ],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Qubit Hardware (IonQ 트랩이온 · Rigetti 초전도 · D-Wave annealing · IBM 클라우드)",
        "Cryogenics + 제어 전자 (Bluefors · Oxford Instruments · Keysight)",
        "Quantum-safe Cryptography (PQC — Cisco · Juniper · IBM · Thales)",
        "Cloud Quantum Service (IBM Quantum · AWS Braket · Azure Quantum · Google Quantum AI)",
        "Quantum Sensing + Metrology (Microsemi atomic clock · Honeywell · Quantum-Si)",
        "Compound Semi for Quantum (Wolfspeed SiC · Coherent photonics · 中 화합물)",
    ],
    "catalyst_types": [
        "美 DARPA Quantum Benchmarking Initiative (QBI) + QED-C consortium 분기 capex",
        "IonQ / Rigetti / Quantum Computing Inc. / D-Wave 분기 매출 + qubit count milestone",
        "IBM Quantum roadmap (Heron / Condor / Flamingo / Blue Jay → 100K qubit) 진행 상황",
        "美 NIST PQC (CRYSTALS-Kyber/Dilithium) 표준 후 네트워크 장비 PQC 마이그레이션 capex",
        "EU Quantum Flagship + 영국 NQTP + 中 国家 quantum lab 신규 발표",
        "Google / Microsoft / AWS 분기 클라우드 quantum 서비스 매출 (commercial 첫 운영)",
        "MIT / Caltech / Princeton 등 학계 quantum advantage demonstration",
        "양자 스타트업 SPAC / IPO (Atom Computing · QuEra · Pasqal · Quantinuum 등)",
    ],
    "regional_concentration": {
        "Qubit Hardware Pure-play (US)": "IonQ IONQ (트랩이온 · 글로벌 #1 pure-play) · Rigetti Computing RGTI (초전도) · Quantum Computing Inc. QUBT (광 양자) · D-Wave Quantum QBTS (annealing) · Atom Computing 비상장 (neutral atom) · Quantinuum 비상장 (Honeywell + Cambridge 합병 · IPO 임박)",
        "Tech Mega-cap + Quantum 부문": "IBM IBM (Quantum roadmap leader) · Microsoft MSFT (Azure Quantum + topological qubit) · Google (Alphabet GOOGL — Willow chip · Quantum AI) · Amazon (AMZN — AWS Braket) · Intel INTC (Tunnel Falls silicon qubit) · NVIDIA NVDA (cuQuantum SDK · GPU 시뮬레이션)",
        "EU + UK + Asia Pure-play": "EU (Pasqal 비상장 - FR · IQM 비상장 - FI · Alpine Quantum 비상장 - AT · ParityQC 비상장 - AT), UK (Arqit ARQQ · Universal Quantum 비상장 · Oxford Quantum Circuits 비상장 · Riverlane 비상장), AU (Silicon Quantum Computing 비상장 · Quantum Brilliance 비상장), CA (Xanadu 비상장 · Photonic Inc 비상장 · D-Wave QBTS), JP (NEC 6701.T · Toshiba 6502.T 양자 부문 · Fujitsu 6702.T · NTT 9432.T)",
        "China Quantum": "Origin Quantum 비상장 · Spinq Technology 비상장 · QuantumCTek 688027.SS · CASTC 비상장 (Chinese Academy of Sciences spinoff) · QuantumPhi 비상장",
        "Cryogenics + 제어 전자": "FI (Bluefors 비상장 — dilution refrigerator 글로벌 #1), UK (Oxford Instruments OXIG.L), US (Keysight KEYS · Janis Research 비상장 - Lake Shore Cryotronics 자회사 · Cryomech 비상장), JP (Sumitomo Heavy 6302.T - cryo부문 · Iwatani 8088.T)",
        "Quantum-safe Cryptography (PQC)": "US (Cisco CSCO · Juniper JNPR · IBM IBM · Palo Alto Networks PANW · CrowdStrike CRWD · Cloudflare NET · Fortinet FTNT · F5 FFIV), EU (Thales HO.PA · Gemalto-Thales 자회사 · Atos ATO.PA), IL (CYBR), KR (한컴라이프케어 비상장 · 라온시큐어 042510.KQ)",
        "Quantum Sensing + Metrology + 광자": "US (Microsemi MSI · Honeywell HON · Quantum-Si QSI · Lumentum LITE · Coherent COHR · MKS Instruments MKSI), DE (Trumpf 비상장 · Carl Zeiss 비상장), JP (Hamamatsu Photonics 6965.T · Furukawa Electric 5801.T)",
    },
}
