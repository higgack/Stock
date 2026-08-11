"""Telecommunication Services — L3 under Communication Services.

미국 무선 통신(AT&T · Verizon · T-Mobile) + Cable (Comcast ·
Charter) + Infra REIT (American Tower · Crown Castle). 5G ARPU 성장 +
broadband BEAD 보조금 + cell tower lease 갱신이 주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "통신 서비스",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "telecom",
        "telecom_l3",
        "통신_l3",
        "wireless",
        "무선",
        "broadband",
        "광대역",
        "cable",
        "케이블",
        "fiber",
        "5g",
        "5g_telecom",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "미국 무선 통신(AT&T · Verizon · T-Mobile)",
        "미국 케이블 + 광대역(Comcast · Charter · Dish · Altice)",
        "통신 인프라 리츠(American Tower · Crown Castle · SBA · Equinix)",
        "유럽 통신 통합(Vodafone · Deutsche Telekom · Orange · BT · Telefónica)",
        "아시아 4대 메가( NTT · KDDI · SoftBank · China Mobile · China Telecom · Reliance Jio )",
        "한국 + 특화 통신(SK텔레콤 · KT · LG U+ + 알뜰폰",
    ],
    "catalyst_types": [
        "5G ARPU 성장 + 美 Verizon/AT&T/T-Mobile capex 사이클",
        "미국 broadband BEAD 보조금 + Cable retention 손실(Comcast/Charter)",
        "셀타워 리츠(AMT/CCI) 분기 lease 갱신 + small cell + edge 컴퓨팅 배치",
        "유럽 통신사 합병 + 5G capex 회수 + 영국 fiber rollout BT/Openreach",
        "중국 14차 5개년 5G ARPU + 국가 디지털경제 + 미국 SDN entity list",
        "한국 5G 28GHz 정책 + 일본 NTT vs SoftBank vs KDDI 경쟁",
    ],
    "regional_concentration": {
        "US Integrated Wireless": "AT&T T · Verizon VZ · T-Mobile US TMUS · US Cellular USM · Liberty Latin America LILA · Tigo · Frontier Communications FYBR · Lumen Technologies LUMN · Consolidated Communications 비상장 (인수 후) · Verizon Communications VZ",
        "US Cable + Broadband": "Comcast CMCSA · Charter Communications CHTR · Dish Network DISH (SATS) · Altice USA ATUS · Cable One CABO · Mediacom 비상장 · Cox Communications 비상장",
        "Communication Infrastructure REIT": "American Tower AMT · Crown Castle CCI · SBA Communications SBAC · Equinix EQIX · Digital Realty DLR · Iron Mountain IRM (data center 일부) · Uniti Group UNIT · DigitalBridge DBRG",
        "EU Telecom": "Vodafone VOD · Deutsche Telekom DTE.DE · Orange ORA.PA · Telefonica TEF.MC · BT BT-A.L · Nokia NOK · Ericsson ERIC · Tele2 TEL2-B.ST · Elisa ELISA.HE · Telenor TEL.OL · Tele2 Sweden TEL2-B.ST · Cellnex Telecom CLNX.MC · BT Group BT-A.L",
        "Japan + Korea + China + India": "JP (NTT 9432.T · KDDI 9433.T · SoftBank 9434.T · Rakuten 4755.T · Internet Initiative Japan IIJ 3774.T), KR (SKT 017670.KS · KT 030200.KS · LGU+ 032640.KS · 알뜰폰 - SK텔링크 · KT 스카이라이프 053210.KS), CN (China Mobile 0941.HK · China Telecom 0728.HK · China Unicom 0762.HK), IN (Reliance Industries RELIANCE.NS (Jio) · Bharti Airtel BHARTIARTL.NS · Vodafone Idea IDEA.NS · Indus Towers INDUSTOWER.NS)",
        "SE Asia + EM + Latin": "SG Singapore Telecom STH.SI (Singtel Z74.SI) · StarHub CC3.SI · MY Maxis 6012.KL · TH AIS 6398.BK · ID Telkom Indonesia TLK · Mobile TeleSystems MTSC · Vimpelcom (VEON), Latin (America Movil AMX · Telefonica Brasil VIV · TIM Brasil TIM · Vodacom VOD.JO · MTN Group MTN.JO)",
    },
}
