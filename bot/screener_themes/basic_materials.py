"""Basic Materials — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (5):
 1. Chemicals (Diversified + Specialty + Petrochemicals + Agricultural)
 2. Construction Materials
 3. Containers & Packaging
 4. Metals & Mining (Steel + Aluminum + Diversified + Precious Metals)
 5. Forest & Paper Products
"""

from __future__ import annotations

THEME = {
    "domain": "Basic Materials (기초 소재)",
    "layer": "L2_SECTOR",
    "aliases": [
        "basic_materials",
        "materials",
        "기초소재",
        "소재",
        "chemicals",
        "화학",
        "mining",
        "광업",
        "steel",
        "철강",
        "gold",
        "금",
        "copper",
        "구리",
        "rare_earth",
        "희토류",
        "agricultural",
        "비료",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Chemicals (화학 — Diversified + Specialty + Petrochemicals + Agricultural)",
        "Construction Materials (건축 자재)",
        "Containers & Packaging (용기 및 포장)",
        "Metals & Mining (금속 및 광업 — Steel + Aluminum + Diversified + Precious Metals)",
        "Forest & Paper Products (임업 및 제지)",
    ],
    "catalyst_types": [
        "美 IRA 45X (battery cathode/anode/cell) + Chinese 반덤핑 시점",
        "美 인프라 bill (CHIPS + IIJA) 시멘트 + 아스팔트 수요",
        "전동화 + 그리드 capex 사이클 (구리 / 알루미늄 / 리튬) supply gap",
        "中国 14차 5개년 rare earth 수출 통제 + 美 reshoring + 호주 Pilbara",
        "FAO 식량 가격 지수 + 비료 (질소 / 인산 / 칼륨) 가격 사이클",
        "美/EU PFAS 규제 + EU CBAM (철강 / 알루미늄 / 시멘트) 효력 시점",
        "Au/Ag 가격 + 中国 PBoC 금 매입 + Fed 정책금리 (Au inverse 상관)",
        "Pulp/Paper 가격 + Amazon e-commerce 포장재 수요 + China 수출 둔화",
    ],
    "regional_concentration": {
        "Chemicals": "Diversified & Specialty US (Linde LIN · Air Products APD · Ecolab ECL · Sherwin-Williams SHW · PPG PPG · DuPont DD · Eastman EMN · Celanese CE · Olin OLN · International Flavors IFF), Petrochemicals (Dow DOW · LyondellBasell LYB · Westlake WLK), Agricultural Chemicals (FMC FMC · Mosaic MOS · CF Industries CF · Nutrien NTR · Corteva CTVA), EU (BASF BAS.DE · Bayer BAYN.DE · Air Liquide AI.PA · Sika SIK.SW · Arkema AKE.PA · Akzo Nobel AKZA.AS), JP (Mitsubishi Chemical 4188.T · Sumitomo Chemical 4005.T · Shin-Etsu 4063.T · Toray 3402.T · Asahi Kasei 3407.T), KR (LG화학 051910.KS · 롯데케미칼 011170.KS · 한화솔루션 009830.KS · 효성첨단소재 298050.KS · OCI 010060.KS), CN (Wanhua 600309.SS · Sinopec 0386.HK · Hengyi 000703.SZ)",
        "Construction Materials": "US (Martin Marietta MLM · Vulcan VMC · Eagle Materials EXP · Summit SUM · USG 비상장), CRH IE (CRH CRH), CN (Anhui Conch 0914.HK · CNBM 3323.HK), JP (Taiheiyo Cement 5233.T), KR (쌍용C&E 003410.KS · 한일시멘트 300720.KS · 아세아시멘트 183190.KS), DE (HeidelbergCement HEI.DE), MX (CEMEX CX)",
        "Containers & Packaging": "US (International Paper IP · WestRock WRK · Packaging Corp PKG · Ball BALL · Crown Holdings CCK · Amcor AMCR · Sealed Air SEE · Sonoco SON · Berry Global BERY · Graphic Packaging GPK), EU (Smurfit Kappa SKG.IR · Smurfit Westrock SW · Mondi MNDI.L · Tetra Pak 비상장), JP (Nippon Paper 3863.T · Oji Holdings 3861.T · Toyo Seikan 5901.T · Daio Paper 3880.T), KR (한솔제지 213500.KS · 무림페이퍼 009200.KS · 율촌화학 008730.KS — 식음료 용기)",
        "Metals & Mining": "Steel (Nucor NUE · Steel Dynamics STLD · Cleveland-Cliffs CLF), Aluminum (Alcoa AA · Century Aluminum CENX), Diversified (Freeport-McMoRan FCX · BHP BHP · Rio Tinto RIO · Vale VALE · Southern Copper SCCO · Anglo American AAL.L · Glencore GLEN.L · Teck TECK), Precious Metals (Newmont NEM · Barrick Gold GOLD · Agnico Eagle AEM · Wheaton Precious WPM · Franco-Nevada FNV · Pan American Silver PAAS · Hecla Mining HL · Sibanye SBSW), Rare Earth (MP Materials MP · Lynas LYC.AX · 中国 北方稀土 600111.SS · Iluka ILU.AX), Lithium (Albemarle ALB · SQM SQM · Pilbara PLS.AX · Sigma Lithium SGML · IGO IGO.AX · Mineral Resources MIN.AX · Ganfeng 002460.SZ · Tianqi 002466.SZ), KR (POSCO 005490.KS · POSCO홀딩스 005490.KS · 고려아연 010130.KS · 풍산 103140.KS · 동국제강 460860.KS), JP (Nippon Steel 5401.T · JFE 5411.T · Mitsubishi Materials 5711.T · Sumitomo Metal Mining 5713.T)",
        "Forest & Paper Products": "US (Weyerhaeuser WY · Rayonier RYN · Sylvamo SLVM · Louisiana-Pacific LPX · Boise Cascade BCC · UFP Industries UFPI), CA (West Fraser WFG · Canfor CFP.TO), EU (UPM-Kymmene UPM.HE · Stora Enso STERV.HE · Holmen HOLM-B.ST), JP (Oji Holdings 3861.T · Nippon Paper 3863.T)",
    },
}
