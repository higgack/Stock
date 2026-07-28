"""Application — CRM/ERP — L4 under Software (소프트웨어).

자동 생성(부모 L3 software 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Application — CRM/ERP (Software)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["application"],
    "horizon": "6-18 months",
    "binding_layer_taxonomy": [
        "Application — CRM/ERP — 미국 (Salesforce CRM · Oracle ORCL · SAP SE SAP · ServiceNow NOW · Workday WDAY · HubSpot HUBS · Adobe ADBE · Intuit INTU)",
        "Application — CRM/ERP — 비상장 (n8n 비상장 · Notion 비상장 · ClickUp 비상장 · Airtable 비상장 · Wrike 비상장)",
    ],
    "catalyst_types": [
        "MSFT Azure 분기 GPU/ML capacity + OpenAI revenue share + Copilot 채택률",
        "Software 분기 net revenue retention + AI agent 신규 product 분기 매출",
        "Cybersecurity 합병 wave (Palo Alto Platform-ization · CRWD M&A)",
        "SaaS 가격 인상 사이클 + Salesforce Agentforce / Workday AI 모듈 매출",
        "ORCL Cloud 분기 신규 contract + GPU capacity + multi-cloud transition",
        "DBT Labs / Snowflake / Databricks 분기 consumption 사이클 + AI workload 비중",
    ],
    "regional_concentration": {
        "Application — CRM/ERP (미국)": "Salesforce CRM · Oracle ORCL · SAP SE SAP · ServiceNow NOW · Workday WDAY · HubSpot HUBS · Adobe ADBE · Intuit INTU · Asana ASAN · monday.com MNDY · Smartsheet SMAR · Bill.com BILL",
        "Application — CRM/ERP (비상장)": "n8n 비상장 · Notion 비상장 · ClickUp 비상장 · Airtable 비상장 · Wrike 비상장",
    },
}
