"""Technology — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (4):
 1. Software (Infrastructure + Application)
 2. Hardware & Equipment (Tech Hardware + Storage + Comm Equipment)
 3. Semiconductors & Equipment (Design + Equipment + EDA + IP)
 4. IT Services & Fintech (IT Services + Consulting + Fintech + Data Processing)

Wave 1 trend `bottleneck.py` (AI Data Center niche) 보다 광범위 — sector
전체. 다국적 종목 (US/TW/KR/JP/CN/EU) 분포 명시.
"""

from __future__ import annotations

THEME = {
    "domain": "Technology (기술)",
    "layer": "L2_SECTOR",
    "aliases": [
        "technology",
        "tech",
        "기술",
        "테크",
        "semi",
        "semiconductor",
        "semiconductors",
        "반도체",
        "software",
        "소프트웨어",
        "saas",
        "cloud",
        "클라우드",
        "cybersecurity",
        "사이버",
        "hardware",
        "하드웨어",
    ],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Software (소프트웨어 — Infrastructure + Application)",
        "Hardware & Equipment (하드웨어 및 장비 — Tech Hardware + Storage + Comm Equipment)",
        "Semiconductors & Equipment (반도체 및 장비 — Design + Equipment + EDA + IP)",
        "IT Services & Fintech (IT 서비스 및 핀테크)",
    ],
    "catalyst_types": [
        "NVIDIA Blackwell/Rubin 출하량 + Hyperscaler AI capex 가이드 (MSFT/META/GOOG/AMZN)",
        "DRAM/NAND spot 가격 + HBM3E/HBM4 양산 비중 (Samsung/SK Hynix/Micron)",
        "ASML High-NA EUV 첫 양산 도입 + TSMC N2/A16 + Samsung 3나 yield",
        "美 BIS 對中 수출규제 4차 + EU/JP 동조 + entity list 확장",
        "Software 분기 net revenue retention + AI agent 채택률 데이터",
        "SaaS 가격 인상 사이클 + Salesforce Agentforce / MSFT Copilot Studio 매출",
        "Cybersecurity 합병 wave (Palo Alto Platform-ization · CRWD M&A)",
        "Apple iPhone 16/17 사이클 + Vision Pro 채택 + AAPL AI 후행 risk",
    ],
    "regional_concentration": {
        "Software": "Infrastructure US (Microsoft MSFT · Oracle ORCL · Snowflake SNOW · Datadog DDOG · MongoDB MDB · Splunk SPLK · CrowdStrike CRWD · Palo Alto PANW · Fortinet FTNT · Zscaler ZS · Okta OKTA · Cloudflare NET · Atlassian TEAM · GitLab GTLB · HashiCorp HCP · Confluent CFLT · Elastic ESTC · Dynatrace DT · JFrog FROG), Application US (Salesforce CRM · Adobe ADBE · Intuit INTU · ServiceNow NOW · Workday WDAY · Autodesk ADSK · Zoom ZM · Palantir PLTR · HubSpot HUBS · Trade Desk TTD · DocuSign DOCU · Twilio TWLO · Shopify SHOP · Asana ASAN · monday MNDY), EU (SAP SAP · Dassault DSY.PA), IL (Check Point CHKP), KR (네이버 035420.KS · 카카오 035720.KS · 한글과컴퓨터 030520.KQ · 더존비즈온 012510.KS)",
        "Hardware & Equipment": "Tech Hardware & Storage (Apple AAPL · HP HPQ · Dell DELL · HPE HPE · NetApp NTAP · Western Digital WDC · Seagate STX · Super Micro SMCI · Pure Storage PSTG), Communications Equipment (Cisco CSCO · Arista ANET · Juniper JNPR · Motorola Solutions MSI · Ciena CIEN · F5 FFIV · Coherent COHR · Lumentum LITE), TW (Hon Hai 2317.TW · Quanta 2382.TW · Wistron 3231.TW · Compal 2324.TW · Inventec 2356.TW · Pegatron 4938.TW · Asustek 2357.TW), KR (Samsung Electronics 005930.KS · LG Electronics 066570.KS), JP (Sony 6758.T · Panasonic 6752.T · Nintendo 7974.T · Fujitsu 6702.T · NEC 6701.T), CN (Lenovo 0992.HK · Xiaomi 1810.HK · ZTE 0763.HK · Huawei 비상장)",
        "Semiconductors & Equipment": "Design & Manufacturing (NVIDIA NVDA · Broadcom AVGO · TSMC TSM · AMD AMD · Intel INTC · Qualcomm QCOM · Texas Instruments TXN · Micron MU · Analog Devices ADI · NXP NXPI · Microchip MCHP · Marvell MRVL · Skyworks SWKS · Lattice LSCC · onsemi ON · Cirrus CRUS), Memory (Samsung 005930.KS · SK Hynix 000660.KS · Micron MU · Kioxia 비상장), Foundry (TSMC 2330.TW · UMC 2303.TW · Samsung 005930.KS · GlobalFoundries GFS · SMIC 0981.HK · 688981.SS), Fabless TW (MediaTek 2454.TW · Realtek 2379.TW · Novatek 3034.TW), Equipment (ASML ASML · Applied Materials AMAT · Lam Research LRCX · KLA KLAC · Onto ONTO · Axcelis ACLS · Entegris ENTG · Photronics PLAB · Tokyo Electron 8035.T · Disco 6146.T · Advantest 6857.T · Lasertec 6920.T · 한미반도체 042700.KS · 원익IPS 240810.KQ · 주성엔지니어링 036930.KQ), EDA (Synopsys SNPS · Cadence CDNS · Ansys ANSS), IP (Arm Holdings ARM)",
        "IT Services & Fintech": "IT Services (Accenture ACN · IBM IBM · Cognizant CTSH · DXC DXC · EPAM EPAM), IN (Infosys INFY · Wipro WIT · TCS TCS.NS · HCL Tech HCLTECH.NS · Tech Mahindra TECHM.NS), JP (NTT Data 9613.T · Nomura Research 4307.T), Fintech & Data Processing (Visa V · Mastercard MA · PayPal PYPL · Block SQ · Fiserv FISV · Global Payments GPN · Adyen ADYEY · Robinhood HOOD · SoFi SOFI · Affirm AFRM), EU (Worldline WLN.PA · Nexi NEXI.MI), KR (NHN KCP 060250.KQ · KG이니시스 035600.KQ · 다날 064260.KQ)",
    },
}
