"""Entertainment — L3 under Communication Services.

Film/TV + Streaming + Music + Live Events. Netflix 구독자 + 광고 tier
+ Disney+ 수익성 + Spotify podcast + 라이브 이벤트 (Live Nation/TKO) 가
주 driver.
"""

from __future__ import annotations

THEME = {
    "domain": "Entertainment (엔터테인먼트)",
    "layer": "L3_INDUSTRY",
    "aliases": [
        "entertainment",
        "엔터테인먼트",
        "streaming_l3",
        "스트리밍_l3",
        "film_tv",
        "영상",
        "music",
        "음악",
        "live_event",
        "라이브",
    ],
    "horizon": "9-18 months",
    "binding_layer_taxonomy": [
        "Streaming + Studio (Netflix · Disney · Warner Bros Discovery · Paramount · Fox)",
        "Music Streaming + Labels (Spotify · UMG · Warner Music · Sirius XM)",
        "Live Events + Sports (Live Nation · MSG · TKO)",
        "Korean Entertainment Big 4 (Hybe · SM · YG · JYP · CJ ENM)",
        "Japanese Animation + Studios (Toho · Bandai Namco · 카부토)",
        "Cinema + Movie Theater (AMC · Cinemark · IMAX)",
    ],
    "catalyst_types": [
        "Netflix 분기 구독자 + 광고 tier 침투 + ARPU + churn",
        "Disney+ 수익성 분기 회복 + Hulu 통합 + ESPN streaming launch",
        "Spotify 분기 podcast 매출 + 광고 tier + Joe Rogan 계약 갱신",
        "Live Nation/TKO 분기 ticket sales + 美 sports streaming rights (WWE · UFC)",
        "Korean Entertainment K-pop 글로벌 + 분기 BTS/Blackpink/NewJeans 매출",
        "Movie Theater 분기 box office + 분기 신작 lineup (Disney · Universal)",
    ],
    "regional_concentration": {
        "Streaming + Studio": "Netflix NFLX · Walt Disney DIS (Disney+ · Hulu · ESPN+) · Warner Bros Discovery WBD (HBO Max · CNN) · Paramount Global PARA (Paramount+ · CBS) · Fox Corporation FOXA · Lionsgate LION (Starz) · AMC Networks AMCX",
        "Music + Audio": "Spotify SPOT · Universal Music Group UMG.AS · Warner Music Group WMG · Sirius XM SIRI · iHeartMedia IHRT · Curiosity Stream CURI · LiveOne LVO · MarketAxess MKTX (음악 외 데이터)",
        "Live Events + Sports": "Live Nation LYV · MSG Sports MSGS · MSG Entertainment MSGE · Sphere Entertainment SPHR · Madison Square Garden Networks (MSGN — Charter 자회사) · TKO Group TKO (WWE · UFC 합병) · Endeavor 비상장 (TKO IPO 후 합병) · Funko FNKO (collectibles)",
        "Korean Entertainment Big 4": "Hybe 352820.KS (BTS · LE SSERAFIM · NewJeans · TXT) · SM Entertainment 041510.KQ (NCT · aespa · Red Velvet) · YG Entertainment 122870.KQ (Blackpink · BabyMonster · Treasure) · JYP Entertainment 035900.KQ (TWICE · Stray Kids · NMIXX) · CJ ENM 035760.KQ (TVING · K-pop 콘서트) · 스튜디오드래곤 253450.KQ (K-드라마) · 위지윅스튜디오 비상장 · 콘텐트리중앙 036420.KS · 카카오엔터 비상장 · TVING-CJ ENM 자회사",
        "Japanese Animation + Studios": "Toho 9602.T · Bandai Namco 7832.T · Sony 6758.T (애니메 + 게임) · TBS 9401.T · Fuji Media 9603.T · Toei 9605.T · Toei Animation 4816.T · Aniplex 비상장 · Studio Ghibli 비상장 · Production I.G 비상장 · Sotsu 비상장 (Gundam) · Square Enix 9684.T (게임 + 만화) · Konami 9766.T · Bushiroad 비상장",
        "Movie Theater": "AMC Entertainment AMC · Cinemark CNK · IMAX IMAX · Regal Cinemas 비상장 (Cineworld 자회사) · CGV 079160.KS (KR) · Lotte Cinema 비상장 · Megabox 비상장 · Movie Theaters Japan (Toho 9602.T · Kadokawa 9468.T)",
        "Streaming Hardware + Specialty": "Roku ROKU · Vizio 비상장 (Walmart 인수) · DISH DISH · Comcast CMCSA (Peacock 자회사)",
    },
}
