"""Metals & Mining — L3 under Basic Materials.

Steel + Aluminum + Copper + Iron Ore + Precious Metals + Rare Earth +
Lithium. 美/EU CBAM + 전동화 + 그리드 capex + 中国 rare earth 수출
통제 + Au/Ag inverse 사이클이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Metals & Mining (금속 및 광업)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "metals_mining",
        "metals",
        "mining_l3",
        "광업_l3",
        "steel_l3",
        "철강_l3",
        "aluminum",
        "알루미늄_l3",
        "copper_l3",
        "구리_l3",
        "gold_l3",
        "금_l3",
        "rare_earth_l3",
        "lithium_l3",
        "리튬",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Steel (Nucor · Steel Dynamics · Cleveland-Cliffs · 포스코 · Nippon Steel)",
        "Aluminum (Alcoa · Century · 호주 South32 · 中 Hongqiao)",
        "Copper + Diversified (Freeport-McMoRan · BHP · Rio Tinto · Vale · Southern Copper · Glencore)",
        "Precious Metals (Newmont · Barrick · Agnico Eagle · Wheaton)",
        "Rare Earth (MP Materials · Lynas · 中国 北方稀土 · Iluka)",
        "Lithium (Albemarle · SQM · Pilbara · Ganfeng · POSCO · 에코프로)",
    ],
    "catalyst_types": [
        "美/EU CBAM (철강 · 알루미늄 · 시멘트) 효력 시점",
        "전동화 + 그리드 capex 사이클 (구리 / 알루미늄 / 리튬) supply gap",
        "中国 14차 5개년 rare earth 수출 통제 + 美 reshoring + 호주 Pilbara",
        "Au/Ag 가격 + 中国 PBoC 금 매입 + Fed 정책금리 (Au inverse 상관)",
        "철강 가격 (HRC spot) + 인프라 bill 시행 + 중국 부동산 회복",
        "리튬 spot (Pilbara SC6 + Chile SQM brine) + 니켈 LME 가격",
    ],
    "regional_concentration": {
        "US Steel + Aluminum": "Nucor NUE · Steel Dynamics STLD · Cleveland-Cliffs CLF · United States Steel X (Nippon Steel 인수 전) · Commercial Metals CMC · Worthington Steel WS · Olympic Steel ZEUS · Alcoa AA · Century Aluminum CENX · Constellium CSTM · Kaiser Aluminum KALU · Reliance Steel RS",
        "Global Copper + Diversified Mining": "Freeport-McMoRan FCX · BHP BHP · Rio Tinto RIO · Vale VALE · Southern Copper SCCO · Anglo American AAL.L · Glencore GLEN.L · Teck Resources TECK · First Quantum FM.TO · Antofagasta ANTO.L · KGHM KGH.WA · Ivanhoe IVN.TO · Capstone CS.TO · Hudbay HBM · Lundin Mining LUN.TO · Norilsk Nickel GMKN.MM · Ero Copper ERO · MMG 1208.HK · Jiangxi Copper 600362.SS · Tongling Nonferrous 000630.SZ · CMOC 3993.HK · Zijin Mining 2899.HK",
        "Precious Metals": "Newmont NEM · Barrick Gold GOLD · Agnico Eagle AEM · Wheaton Precious WPM · Franco-Nevada FNV · Pan American Silver PAAS · Hecla Mining HL · Sibanye SBSW · Royal Gold RGLD · B2Gold BTG · Iamgold IAG · Eldorado Gold EGO · Coeur Mining CDE · First Majestic Silver AG · Pan American Silver PAAS · MAG Silver MAG · Endeavour Silver EXK · SSR Mining SSRM · OceanaGold OGC.AX · Equinox Gold EQX · Alamos Gold AGI · Yamana 비상장 (인수 후) · Centerra Gold CG.TO · Torex Gold TXG.TO · Galiano Gold GAU",
        "Rare Earth + Critical Minerals": "MP Materials MP · Lynas LYC.AX · 中国 北方稀토 600111.SS · 中国稀土 000831.SZ · Energy Fuels UUUU (REE 부산물) · Iluka ILU.AX · USA Rare Earth 비상장 · Critical Mining CMI · TMC TMC · Niocorp 비상장",
        "Lithium": "Albemarle ALB · SQM SQM · Livent LTHM · Pilbara PLS.AX · Sigma Lithium SGML · IGO IGO.AX · Mineral Resources MIN.AX · Lithium Americas LAC · Allkem 비상장 (Livent 합병 후 ARC) · Tianqi 002466.SZ · Ganfeng 002460.SZ · 미국 (Lithium Americas LAC · Standard Lithium SLI · Piedmont Lithium PLL · Atlas Lithium ATLX) · BR (Sigma Lithium SGML)",
        "Korea + Japan Steel + Battery Metals": "POSCO홀딩스 005490.KS · 현대제철 004020.KS · 동국제강 460860.KS · 고려아연 010130.KS · 풍산 103140.KS · 포스코퓨처엠 003670.KS · 에코프로비엠 247540.KQ · 에코프로 086520.KQ · 엘앤에프 066970.KQ · 코스모신소재 005070.KS · LS MnM 비상장, JP (Nippon Steel 5401.T · JFE 5411.T · Kobe Steel 5406.T · Mitsubishi Materials 5711.T · Sumitomo Metal Mining 5713.T · Daido Steel 5471.T · Mitsui Mining 1518.T · DOWA Holdings 5714.T)",
    },
}
