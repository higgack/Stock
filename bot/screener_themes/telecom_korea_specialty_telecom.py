"""Korea + Specialty Telecom — L4 under Telecommunication Services (통신 서비스).

자동 생성(부모 L3 telecom 의 binding_layer 파생, 사용자 2026-06-14
'전 GICS L4'). 콘텐츠는 L3 큐레이션 재구조화 — 실제 종목·카탈리스트.
"""

from __future__ import annotations

THEME = {
    "domain": "Korea + Specialty Telecom (Telecommunication Services)",
    "layer": "L4_SUBINDUSTRY",
    "aliases": ["korea_telecom"],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Korea + Specialty Telecom — 미국 (Vodafone VOD · Nokia NOK · Ericsson ERIC)",
        "Korea + Specialty Telecom — 유럽 (Deutsche Telekom DTE.DE · Orange ORA.PA · Telefonica TEF.MC · BT BT-A.L · Tele2 TEL2-B.ST · Elisa ELISA.HE · Telenor TEL.OL · Tele2 Sweden TEL2-B.ST)",
    ],
    "catalyst_types": [
        "5G ARPU 성장 + 美 Verizon/AT&T/T-Mobile capex 사이클",
        "美 broadband BEAD 보조금 + Cable retention 손실 (Comcast/Charter)",
        "Cell tower REIT (AMT/CCI) 분기 lease 갱신 + small cell + edge 컴퓨팅 deployment",
        "EU 통신사 합병 + 5G capex 회수 + 영국 fiber rollout BT/Openreach",
        "中国 14차 5개년 5G ARPU + 国家 数字경제 + 美 SDN entity list",
        "한국 5G 28GHz 정책 + 일본 NTT vs SoftBank vs KDDI 경쟁",
    ],
    "regional_concentration": {
        "Korea + Specialty Telecom (미국)": "Vodafone VOD · Nokia NOK · Ericsson ERIC",
        "Korea + Specialty Telecom (유럽)": "Deutsche Telekom DTE.DE · Orange ORA.PA · Telefonica TEF.MC · BT BT-A.L · Tele2 TEL2-B.ST · Elisa ELISA.HE · Telenor TEL.OL · Tele2 Sweden TEL2-B.ST · Cellnex Telecom CLNX.MC · BT Group BT-A.L",
    },
}
