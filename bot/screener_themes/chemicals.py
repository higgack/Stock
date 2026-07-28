"""Chemicals — L3 under Basic Materials.

Diversified (Linde · Air Products · APD) + Specialty (Sherwin-Williams ·
PPG) + Petrochemicals (Dow · LyondellBasell) + Agricultural (FMC ·
Mosaic · CF). 美 IRA 45X battery materials + EU CBAM + 中 反倾销
화학가 사이클이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Chemicals (화학)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "chemicals",
        "chemicals_l3",
        "화학_l3",
        "specialty_chemicals",
        "특수화학",
        "petrochemicals",
        "석유화학",
        "agricultural_chemicals",
        "비료_l3",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Industrial Gases (Linde · Air Products · Air Liquide · Iwatani)",
        "Diversified + Specialty (Dow · Eastman · Sherwin · PPG · DuPont)",
        "Petrochemicals (Dow · LyondellBasell · Westlake · Sinopec)",
        "Agricultural Chemicals + 비료 (FMC · Mosaic · CF · Corteva · Nutrien)",
        "Specialty for EV Battery + Semiconductor (Albemarle · 솔브레인 · 동진쎄미켐)",
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
        "Industrial Gases": "Linde LIN · Air Products APD · Air Liquide AI.PA · Iwatani 8088.T · Taiyo Nippon Sanso TNSC.T",
        "Diversified + Specialty": "Sherwin-Williams SHW · PPG Industries PPG · DuPont DD · Eastman Chemical EMN · Celanese CE · Olin OLN · International Flavors IFF · Ecolab ECL · Westlake Chemical WLK · Huntsman HUN · Univar Solutions UNVR (인수 후) · Avient AVNT · NewMarket NEU · Element Solutions ESI · Quaker Houghton KWR · Innospec IOSP · Stepan SCL · Cabot CBT · Tronox TROX · Chemours CC · GCP Applied Tech GCP",
        "Petrochemicals": "Dow DOW · LyondellBasell LYB · Westlake WLK · Trinseo TSE · Methanex MEOH · Olin OLN · Methanex MEOH, EU (BASF BAS.DE · Bayer BAYN.DE), JP (Mitsubishi Chemical 4188.T · Sumitomo Chemical 4005.T · Shin-Etsu 4063.T · Toray 3402.T · Asahi Kasei 3407.T · Kuraray 3405.T), KR (LG화학 051910.KS · 롯데케미칼 011170.KS · 한화솔루션 009830.KS · 효성첨단소재 298050.KS · OCI 010060.KS · 금호석유 011780.KS · 대한유화 006650.KS), CN (Wanhua 600309.SS · Hengyi 000703.SZ · CIMC 2039.HK)",
        "Agricultural + Fertilizer": "FMC FMC · Mosaic MOS · CF Industries CF · Nutrien NTR · Corteva CTVA · Scotts Miracle-Gro SMG · Compass Minerals CMP · Intrepid Potash IPI · Yara YAR.OL · K+S SDF.DE · ICL Group ICL · OCI N.V. OCI.AS · LSB Industries LXU · American Vanguard AVD · ICL ICL · Bunge BG",
        "EV Battery + Semiconductor Materials": "Albemarle ALB · SQM SQM · Livent LTHM · Pilbara PLS.AX · Mineral Resources MIN.AX · Lithium Americas LAC · Ganfeng 002460.SZ · Tianqi 002466.SZ · Sumitomo Chemical 4005.T · Shin-Etsu 4063.T · 한솔케미칼 014680.KS · 솔브레인 357780.KQ · 동진쎄미켐 005290.KS · 동화기업 025900.KQ · 천보 278280.KQ · 에코프로비엠 247540.KQ · 포스코퓨처엠 003670.KS · 엘앤에프 066970.KQ · 코스모신소재 005070.KS · 대주전자재료 078600.KQ · WCP 393890.KQ",
    },
}
