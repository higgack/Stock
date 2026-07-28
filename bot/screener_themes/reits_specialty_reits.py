"""Specialty REITs — L4 under REITs (리츠).

자동 생성(부모 L3 reits 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Specialty REITs (REITs)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": [],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Specialty REITs — 미국 (Public Storage PSA (Self-storage) · Extra Space EXR (Self-storage) · CubeSmart CUBE · National Storage Affiliates NSA · VICI Properties VICI (Casinos) · Gaming and Leisure GLPI · Lamar Advertising LAMR · Outfront OUT)",
        "Specialty REITs — 비상장 (Life Storage 비상장 (인수 후) · MGM Growth 비상장 (VICI 인수))",
    ],
    "catalyst_types": [
        "Fed funds rate path + 美 10Y → REIT 배당 매력 변동",
        "AI 데이터센터 REIT (Equinix/Digital Realty) 분기 leasing + power capacity",
        "Industrial REIT (Prologis) e-commerce + 美 reshoring + onshoring capex",
        "美 office vacancy + CRE 만기 wall + sub-lease 시장 회복",
        "Residential REIT (Sun Belt) 美 단독주택 vs 다세대 rent growth + 모기지 금리",
        "Healthcare REIT (Welltower/Ventas) 시니어 housing 회복 + Medicare 정책",
    ],
    "regional_concentration": {
        "Specialty REITs (미국)": "Public Storage PSA (Self-storage) · Extra Space EXR (Self-storage) · CubeSmart CUBE · National Storage Affiliates NSA · VICI Properties VICI (Casinos) · Gaming and Leisure GLPI · Lamar Advertising LAMR · Outfront OUT · SBA Communications SBAC · American Tower AMT · Crown Castle CCI · Uniti Group UNIT",
        "Specialty REITs (비상장)": "Life Storage 비상장 (인수 후) · MGM Growth 비상장 (VICI 인수)",
    },
}
