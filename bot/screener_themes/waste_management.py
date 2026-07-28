"""Waste & Environmental Services — L3 under Industrials.

규제 driven 안정적 cash flow. EPA PFAS 규제 + 美 인프라 bill (수처리) +
EU CBAM (재활용) + 기후 변화 + 분리수거 의무가 sub-driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Waste & Environmental Services (폐기물 및 환경 서비스)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "waste_management",
        "waste",
        "폐기물",
        "환경서비스",
        "recycling",
        "재활용",
        "water_treatment",
        "수처리",
    ],
    "horizon": "12-24 months",
    "binding_layer_taxonomy": [
        "Solid Waste Collection (Waste Mgmt · Republic · Waste Connections)",
        "Hazardous Waste + Industrial Cleaning (Clean Harbors · Stericycle · Veolia)",
        "Recycling + Recovery (Casella · Schnitzer · Stericycle)",
        "Water/Wastewater Treatment (Xylem · Veolia · Suez · American Water Works)",
        "Environmental Consulting + Remediation (Tetra Tech · Stantec)",
    ],
    "catalyst_types": [
        "美 EPA PFAS 'forever chemicals' 규제 + 인프라 bill 수처리 자금",
        "美 폐기물 가격 인상 (WM/RSG 분기 가이드) + collected volume",
        "EU CBAM + 기후 변화 의무 + 영국 plastic packaging tax",
        "데이터센터 + 반도체 fab 신규 site → 산업 폐기물 + 수처리 수요",
        "기후 재해 (허리케인 · 산불) 복구 → 환경 정화 매출",
        "中国 14차 5개년 재활용 의무 + 일본 PET·플라스틱 수출 통제",
    ],
    "regional_concentration": {
        "US Solid Waste + Hazardous": "Waste Management WM · Republic Services RSG · Waste Connections WCN · Clean Harbors CLH · Stericycle SRCL · Casella CWST · Heritage-Crystal Clean HCCI · US Ecology 비상장 · Quest Resource QRHC",
        "Canada": "GFL Environmental GFL",
        "EU Environmental": "Veolia VIE.PA · Suez 비상장 · Biffa BIFF.L · Renewi RWI.L · Saint-Gobain SGO.PA (recycling 자회사)",
        "Water Treatment": "Xylem XYL · Pentair PNR · Mueller Water MWA · Roper ROP · Watts Water WTS · A.O. Smith AOS · Evoqua 비상장 (Xylem 인수 후) · Forterra FRTA · Northwest Pipe NWPX · American Water Works AWK · Essential Utilities WTRG",
        "Environmental Consulting": "Tetra Tech TTEK · Stantec STN · Montrose Environmental MEG · Ecolab ECL (water treatment 자회사) · EMCOR EME · Granite GVA · ICF International ICFI",
        "Korea + Japan": "코엔텍 029960.KQ · 인선이엔티 060150.KQ · KG ETS 151860.KQ · Daiseki 9793.T (JP) · Front Group 7820.T (JP) · 에코마이스터 비상장",
    },
}
