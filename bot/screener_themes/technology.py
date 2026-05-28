"""Technology — Wave 2 broad sector (Finviz Technology).

Finviz Technology industries: Semiconductors · Semiconductor Equipment &
Materials · Software - Infrastructure · Software - Application · IT Services
· Communication Equipment · Computer Hardware · Consumer Electronics ·
Electronic Components · Scientific & Technical Instruments.

ToC 관점 — Wave 1 bottleneck (AI Data Center 니치 lens) 보다 광범위:
 1) Semiconductor cyclical recovery — DRAM/NAND 가격 bottom 통과 (2026
    H1), HBM/AI logic 은 ASP premium 유지. Foundry capacity 는 TSMC N2/A16
    가속 + Samsung 3나 yield 우려.
 2) Semi Equipment - ASML High-NA 도입 + Lam/AMAT/KLAC 에쳐/CVD/계측
    선행지표. 美 BIS 對中 수출규제 4차 라운드 (2025-12) 이후 EU/JP 동조
    여부.
 3) Software Infrastructure - Cybersecurity (PANW/CRWD/NET) 합병 wave
    + Data Cloud (SNOW/DDOG/MDB) + Database (MSFT Fabric / Oracle Cloud).
 4) Software Application - SaaS 가격 인상 사이클 + AI agent SaaS layer
    (Salesforce Agentforce / Microsoft Copilot Studio).
 5) Hardware (DELL/HPE/PSTG/NTAP) - 서버 OEM 이 GPU 매출 의존, NIM
    margin 낮음. Storage 는 AI training/inference 수요로 회복.
 6) Communication Equipment (CSCO/ANET/JNPR) - AI fabric switch 수혜
    (ANET dominant) vs 일반 ethernet 둔화.
 7) IT Services (ACN/IBM/INFY) - GenAI 가 인력 outsourcing 모델 disrupt.
"""

from __future__ import annotations

THEME = {
    "domain": "Technology (Finviz Sector — Semi · Software · Hardware · IT Services · Comm Equipment)",
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
        "Semiconductors — Logic (TSMC/Samsung/Intel · AI accelerator)",
        "Semiconductors — Memory (HBM · DRAM · NAND)",
        "Semiconductor Equipment & Materials (ASML/AMAT/LRCX/KLAC)",
        "Software — Infrastructure (Cybersec · Data Cloud · DB · Observability)",
        "Software — Application (SaaS · AI agent layer)",
        "IT Services (ACN/IBM/INFY · GenAI disruption)",
        "Communication Equipment (AI fabric switch · 5G RAN)",
        "Computer Hardware (Server OEM · Storage · Networking)",
        "Consumer Electronics (Apple · Sony · Samsung Electronics)",
        "Electronic Components + Distribution",
    ],
    "catalyst_types": [
        "NVIDIA Blackwell/Rubin 출하량 + Hyperscaler AI capex 가이드",
        "DRAM/NAND spot 가격 + HBM3E/HBM4 양산 비중",
        "ASML High-NA EUV 첫 양산 도입 + TSMC N2/A16 + Samsung 3나 yield",
        "美 BIS 對中 수출규제 4차 + EU/JP 동조 + entity list 확장",
        "Software 분기 net revenue retention + AI agent 채택률 데이터",
        "SaaS 가격 인상 사이클 + Salesforce/MSFT/Oracle 신규 product",
        "Cybersecurity 합병 wave (Palo Alto Platform-ization)",
        "Apple iPhone 16/17 사이클 + Vision Pro 채택 + AAPL AI 후행 risk",
    ],
    "regional_concentration": {
        "Semiconductors — Logic / Foundry": "TW (TSMC 2330.TW / UMC 2303.TW), KR (Samsung 005930.KS), US (Intel INTC / GlobalFoundries GFS / Micron MU), CN (SMIC 0981.HK / 688981.SS)",
        "Semiconductors — Memory (HBM/DRAM/NAND)": "KR (Samsung 005930.KS / SK Hynix 000660.KS), US (Micron MU), JP (Kioxia 비상장)",
        "Semiconductors — Fabless Logic": "US (NVIDIA NVDA / AMD AMD / Broadcom AVGO / Qualcomm QCOM / Marvell MRVL / Texas Instruments TXN / Analog Devices ADI / NXP NXPI / Microchip MCHP / Lattice LSCC / onsemi ON / Cirrus CRUS), TW (MediaTek 2454.TW / Realtek 2379.TW / Novatek 3034.TW), KR (LX세미콘 108320.KQ)",
        "Semiconductor Equipment & Materials": "NL (ASML ASML / ASM International ASM), US (Applied Materials AMAT / Lam Research LRCX / KLA KLAC / Onto Innovation ONTO / Axcelis ACLS / Veeco VECO / Entegris ENTG / Photronics PLAB), JP (Tokyo Electron 8035.T / Disco 6146.T / Advantest 6857.T / Screen 7735.T / Lasertec 6920.T), KR (한미반도체 042700.KS / 원익IPS 240810.KQ / 주성엔지니어링 036930.KQ / 동진쎄미켐 005290.KS)",
        "Software — Infrastructure (Cybersec)": "US (Palo Alto Networks PANW / CrowdStrike CRWD / Fortinet FTNT / Zscaler ZS / Cloudflare NET / Okta OKTA / SentinelOne S / Rapid7 RPD / Tenable TENB / Qualys QLYS / CyberArk CYBR), IL (Check Point CHKP)",
        "Software — Infrastructure (Data Cloud / DB)": "US (Snowflake SNOW / Datadog DDOG / MongoDB MDB / Confluent CFLT / Elastic ESTC / HashiCorp HCP / GitLab GTLB / JFrog FROG / Dynatrace DT / New Relic NEWR), DE (SAP SAP)",
        "Software — Application (SaaS + AI agent)": "US (Salesforce CRM / Microsoft MSFT / Oracle ORCL / Adobe ADBE / Intuit INTU / ServiceNow NOW / Workday WDAY / Atlassian TEAM / HubSpot HUBS / Shopify SHOP / Asana ASAN / monday.com MNDY / Smartsheet SMAR)",
        "IT Services": "US (Accenture ACN / IBM IBM / Cognizant CTSH / DXC DXC / EPAM EPAM), IN (Infosys INFY / Wipro WIT / TCS TCS.NS / HCL Tech HCLTECH.NS / Tech Mahindra TECHM.NS), JP (NTT Data 9613.T / Nomura Research 4307.T)",
        "Communication Equipment": "US (Cisco CSCO / Arista ANET — AI fabric leader / Juniper JNPR / Motorola Solutions MSI / F5 FFIV / Ciena CIEN / NetGear NTGR / Lumentum LITE / Coherent COHR), CN (ZTE 0763.HK / Huawei 비상장)",
        "Computer Hardware + Storage": "US (Apple AAPL / Dell DELL / HP Inc HPQ / HPE HPE / NetApp NTAP / Pure Storage PSTG / Super Micro SMCI / Western Digital WDC / Seagate STX), TW (Hon Hai 2317.TW / Quanta 2382.TW / Wistron 3231.TW)",
        "Consumer Electronics": "US (Apple AAPL / GoPro GPRO / Sonos SONO / Roku ROKU), JP (Sony 6758.T / Panasonic 6752.T / Nintendo 7974.T), KR (Samsung Electronics 005930.KS / LG Electronics 066570.KS), CN (Xiaomi 1810.HK / Lenovo 0992.HK)",
        "Electronic Components + Distribution": "US (Amphenol APH / TE Connectivity TEL / Corning GLW / CDW CDW / Arrow ARW / Avnet AVT / Belden BDC / Vishay VSH), JP (Murata 6981.T / TDK 6762.T / Kyocera 6971.T / Hirose 6806.T)",
    },
}
