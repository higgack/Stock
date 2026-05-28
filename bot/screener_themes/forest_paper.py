"""Forest & Paper Products — L3 under Basic Materials.

Timber + Pulp + Paper + Wood Products. Weyerhaeuser timberland REIT
지위 (Specialty REIT 와 cross-listed) + 美 신규 주택 건설 + e-commerce
포장재 수요가 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Forest & Paper Products (임업 및 제지)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "forest_paper",
        "임업제지",
        "timber",
        "목재",
        "pulp_paper",
        "펄프",
        "lumber",
        "wood_products",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Timber + Lumber (Weyerhaeuser · Rayonier · West Fraser · Canfor)",
        "Pulp + Paper Brands (UPM-Kymmene · Stora Enso · Suzano · Mondi)",
        "Wood Products + Building (Louisiana-Pacific · Boise Cascade · UFP)",
        "Specialty Paper + Tissue (Sylvamo · Clearwater Paper · Glatfelter)",
    ],
    "catalyst_types": [
        "美 신규 주택 건설 + 30Y 모기지 금리 → lumber 수요 + 분기 매출",
        "Pulp/Paper 가격 (NBSK/BHK) + e-commerce 포장재 수요 + 中国 수출 둔화",
        "美 산불 후 timber 가격 + 분기 inventory 회복 (특히 California · Oregon)",
        "EU CBAM + ESG 의무 + 영국 plastic packaging tax → 친환경 포장재 수요",
        "Weyerhaeuser 분기 timberland 매출 + 신규 home building 입주",
        "中国 임업 수입 + 일본 친환경 건축 의무",
    ],
    "regional_concentration": {
        "US Timber + Lumber + REIT": "Weyerhaeuser WY · Rayonier RYN · PotlatchDeltic PCH · Boise Cascade BCC · Louisiana-Pacific LPX · UFP Industries UFPI · 西方 Forestry WY · Catchmark Timber CTT · Forestar Group FOR",
        "Pulp + Paper": "Sylvamo SLVM · Clearwater Paper CLW · Domtar 비상장 (인수 후) · International Paper IP (제지) · WestRock WRK · Smurfit Westrock SW · Stora Enso STERV.HE · UPM-Kymmene UPM.HE · Sappi SAP.JO · Suzano SUZ · Cascades CAS.TO · Mercer International MERC · Resolute Forest 비상장 (인수 후)",
        "Wood Products + Building Materials": "Boise Cascade BCC · Louisiana-Pacific LPX · UFP Industries UFPI · Trex TREX (composite decking) · AZEK AZEK (PVC decking) · Quanex Building NX · PGT Innovations PGTI · JELD-WEN JELD · Continental Building Products 비상장",
        "Specialty Paper + Tissue": "Sylvamo SLVM · Clearwater Paper CLW · Glatfelter GLT · Schweitzer-Mauduit SWM · Domtar 비상장 · Glatfelter GLT · Verso Corporation 비상장 (인수 후) · KapStone Paper 비상장 · Neenah NP",
        "EU + Asia Forest": "UPM-Kymmene UPM.HE · Stora Enso STERV.HE · Mondi MNDI.L · Holmen HOLM-B.ST · Billerud Korsnäs BILL.ST · Norske Skog NSKOG.OL · Suzano SUZ (BR) · Klabin KLBN11.SA (BR), JP (Oji Holdings 3861.T · Nippon Paper 3863.T · Daio Paper 3880.T · Mitsubishi Paper 3864.T)",
    },
}
