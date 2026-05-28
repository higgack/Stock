"""Communication Services — L2 Sector (미국 정식 분류 GICS-like).

L3 sub-industries (4):
 1. Interactive Media & Services (소셜 + 검색 + 광고)
 2. Entertainment (영상 + 음악 + 라이브)
 3. Gaming (게임)
 4. Telecommunication Services (Telecom Integrated + Cable + Infra REIT)
"""

from __future__ import annotations

THEME = {
    "domain": "Communication Services (커뮤니케이션 서비스)",
    "layer": "L2_SECTOR",
    "aliases": [
        "communication",
        "communications",
        "comm",
        "커뮤니케이션",
        "media",
        "미디어",
        "광고",
        "ad",
        "advertising",
        "telecom",
        "통신",
        "social",
        "소셜",
        "internet",
        "인터넷",
        "gaming",
        "게임",
        "streaming",
        "스트리밍",
    ],
    "horizon": "9-24 months",
    "binding_layer_taxonomy": [
        "Interactive Media & Services (인터랙티브 미디어 및 서비스)",
        "Entertainment (엔터테인먼트 — Film/TV + Music + Live)",
        "Gaming (게임)",
        "Telecommunication Services (통신 서비스 — Integrated + Cable + Infra)",
    ],
    "catalyst_types": [
        "Alphabet/Meta 광고 분기 매출 + GenAI 검색 disruption (Perplexity / ChatGPT) 정량화",
        "TikTok 미국 divest 결정 + EU Digital Services Act 시행",
        "Netflix 구독자 + 광고 tier 침투 + Disney+ 수익성 분기 회복",
        "5G ARPU 성장 + Verizon/AT&T/T-Mobile capex 사이클",
        "美 broadband BEAD 보조금 + Cable retention 손실 (Comcast/Charter)",
        "Gaming 분기 출시 일정 (Take-Two GTA 6 / EA · Activision Blizzard MS 인수 후) + Roblox/Sea Limited DAU",
        "Spotify podcast + 광고 tier + Apple Music 점유율 + 라이브 이벤트 (Live Nation) 수익화",
        "Cell tower REIT (AMT/CCI) 분기 lease 갱신 + small cell + edge 컴퓨팅 deployment",
    ],
    "regional_concentration": {
        "Interactive Media & Services": "US (Alphabet GOOGL · Meta META · Snap SNAP · Pinterest PINS · Match MTCH · Bumble BMBL · IAC IAC · Reddit RDDT · Yelp YELP · ZipRecruiter ZIP), CN (Tencent 0700.HK · Alibaba 9988.HK · Baidu 9888.HK · NetEase NTES · JD JD · Pinduoduo PDD · Meituan 3690.HK · Kuaishou 1024.HK · Bilibili BILI), KR (네이버 035420.KS · 카카오 035720.KS · 아프리카TV 067160.KQ · SOOP 067160.KQ · 위메이드 112040.KQ), JP (LINE-Yahoo Z Holdings 4689.T · CyberAgent 4751.T · DeNA 2432.T), EU (Spotify SPOT · Just Eat Takeaway TKWY.AS · Delivery Hero DHER.DE)",
        "Entertainment": "Film/TV US (Disney DIS · Netflix NFLX · Warner Bros Discovery WBD · Paramount PARA · Fox FOXA · Lionsgate LION · AMC AMC), Music/Audio (Spotify SPOT · Sirius SIRI · Universal Music Group UMG.AS · Warner Music WMG), Live (Live Nation LYV · MSG Sports MSGS · MSG Entertainment MSGE · World Wrestling-TKO TKO), Streaming Hardware (Roku ROKU), CN (iQIYI IQ · Tencent Music TME · Huya HUYA · DouYu DOYU), KR (CJ ENM 035760.KQ · 스튜디오드래곤 253450.KQ · SM 041510.KQ · YG 122870.KQ · JYP 035900.KQ · 하이브 352820.KS), JP (Toho 9602.T · TBS 9401.T · Fuji Media 9603.T)",
        "Gaming": "US (Take-Two TTWO · Electronic Arts EA · Roblox RBLX · Unity U · Activision Blizzard 비상장 MS 인수 후 · Microsoft MSFT 자회사 King), SE (Sea Limited SE · Embracer EMBRAC-B.ST), JP (Nintendo 7974.T · Sony 6758.T · Bandai Namco 7832.T · Square Enix 9684.T · Capcom 9697.T · Konami 9766.T · Nexon 3659.T · Sega Sammy 6460.T · Capcom 9697.T), CN (Tencent 0700.HK · NetEase NTES · Bilibili BILI · miHoYo 비상장), KR (Krafton 259960.KS · Netmarble 251270.KS · NCSOFT 036570.KS · 컴투스 078340.KQ · 위메이드 112040.KQ · 카카오게임즈 293490.KQ)",
        "Telecommunication Services": "Integrated/Wireless US (AT&T T · Verizon VZ · T-Mobile TMUS · US Cellular USM · United States Cellular USM), Cable/Satellite (Comcast CMCSA · Charter CHTR · Dish DISH · Liberty Media LBRDA), Infra REIT (American Tower AMT · Crown Castle CCI · SBA Communications SBAC · Equinix EQIX · Digital Realty DLR), EU (Vodafone VOD · Deutsche Telekom DTE.DE · Orange ORA.PA · Telefonica TEF.MC · BT BT-A.L · Nokia NOK · Ericsson ERIC), KR (SKT 017670.KS · KT 030200.KS · LGU+ 032640.KS), JP (NTT 9432.T · KDDI 9433.T · SoftBank 9434.T), CN (China Mobile 0941.HK · China Telecom 0728.HK · China Unicom 0762.HK), IN (Reliance Jio 비상장 / Reliance RELIANCE.NS · Bharti Airtel BHARTIARTL.NS)",
    },
}
