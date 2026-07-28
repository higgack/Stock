"""Insurance — L3 under Financials.

P&C + Life/Health + Brokers/Reinsurance. 美 자연재해 (허리케인 +
산불) cycle + variable annuity hedge book + auto loss ratio + 보험 brokers
fee 사이클이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Insurance (보험)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "insurance",
        "insurance_l3",
        "보험_l3",
        "p_c",
        "life_insurance",
        "생명보험",
        "reinsurance",
        "재보험",
        "insurance_broker",
        "보험중개",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "P&C — Auto + Home (Progressive · Allstate · Travelers)",
        "P&C — Commercial + Specialty (Chubb · WR Berkley · Markel)",
        "Life + Health (MetLife · Prudential · Aflac · AIG · Manulife)",
        "Reinsurance (RGA · RenaissanceRe · Everest · Swiss Re · Munich Re)",
        "Insurance Brokers (Marsh McLennan · Aon · WTW · Arthur J. Gallagher · Brown & Brown)",
    ],
    "catalyst_types": [
        "美 자연재해 cycle (허리케인 시즌 + 산불 + 토네이도) → 분기 cat loss",
        "Auto loss ratio (PGR · ALL 분기 가이드) + premium 인상 사이클",
        "Variable annuity hedge book + 美 10Y 급변 시 자본여력 swing",
        "Insurance brokers fee 사이클 + 분기 organic growth (MMC · AON · AJG)",
        "한국 보험 IFRS17 시행 후 자본 영향 + 海外 운용 손익",
        "中国 평안보험 분기 NBV + 港股통 southbound 보험 매수 변동",
    ],
    "regional_concentration": {
        "US P&C — Auto + Home": "Progressive PGR · Allstate ALL · Travelers TRV · State Farm 비상장 · GEICO (BRK.B 자회사) · Mercury MCY · Erie ERIE · Kemper KMPR · Selective SIGI",
        "US P&C — Commercial + Specialty": "Chubb CB · WR Berkley WRB · Markel MKL · Hartford HIG · Hanover THG · CNA Financial CNA · Cincinnati Financial CINF · Old Republic ORI · Arch Capital ACGL · Kinsale KNSL · RLI RLI · ProAssurance PRA · James River JRVR",
        "Life + Health": "MetLife MET · Prudential PRU · Aflac AFL · AIG AIG · Lincoln Financial LNC · Globe Life GL · Reinsurance Group RGA · Equitable Holdings EQH · Brighthouse BHF · Unum UNM · CNO Financial CNO · Primerica PRI · Manulife MFC · Sun Life SLF · Great-West GWO.TO",
        "Reinsurance": "Everest EG · RenaissanceRe RNR · Reinsurance Group RGA · Arch Capital ACGL · Munich Re MUV2.DE · Swiss Re SREN.SW · Hannover Rück HNR1.DE · SCOR SCR.PA · Lloyd's 비상장 (Lloyd's of London)",
        "Insurance Brokers": "Marsh McLennan MMC · Aon AON · Willis Towers Watson WTW · Arthur J. Gallagher AJG · Brown & Brown BRO · Ryan Specialty RYAN · Erie Indemnity ERIE · BRP Group BRP · Goosehead GSHD · ePlus PLUS",
        "Berkshire Hathaway": "Berkshire Hathaway BRK.B (보험 + 다각화)",
        "Korea + Japan + China": "삼성생명 032830.KS · 한화생명 088350.KS · 삼성화재 000810.KS · DB손해보험 005830.KS · 메리츠금융지주 138040.KS · 현대해상 001450.KS · 코리안리 003690.KS, JP (Dai-ichi 8750.T · T&D 8795.T · MS&AD 8725.T · Tokio Marine 8766.T · Sompo 8630.T · Sony Financial 비상장 · Japan Post 7181.T), CN (Ping An 2318.HK + 601318.SS · AIA 1299.HK · China Life 2628.HK · CPIC 2601.HK · NCI 1336.HK · PICC 1339.HK)",
    },
}
