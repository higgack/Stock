"""Market detection + per-market configuration.

Single source of truth for "which country's exchange is this ticker on,
and what does that mean for benchmark / currency / trading hours". Used
by the channel router (to decide if a ticker is even valid), the sector
strength tool (to pick a benchmark ETF), and the alpha resolver (to
compute returns against the right index).

Detection is purely suffix-based — `.KS`/`.KQ` Korean, `.T` Japanese,
`.SS`/`.SZ`/`.HK` Chinese / Hong Kong, everything else US. We don't try
to parse 6-digit-no-suffix as Korean because that collides with US
exchange tickers like `Z` or `T` and the user already includes the
suffix when typing in the channel (the help text shows the form).
"""

from __future__ import annotations

from typing import TypedDict


class MarketConfig(TypedDict):
    name: str            # Human-readable Korean label.
    broad_benchmark: str # yfinance ticker for the broad market index ETF.
    broad_label: str     # Display label for the broad benchmark.
    currency: str        # ISO currency code.
    currency_symbol: str # Display symbol for prices.
    trading_hours: str   # Local trading hours (info only).


MARKET_CONFIG: dict[str, MarketConfig] = {
    "US": {
        "name": "미국",
        "broad_benchmark": "SPY",
        "broad_label": "S&P 500 (SPY)",
        "currency": "USD",
        "currency_symbol": "$",
        "trading_hours": "09:30-16:00 ET",
    },
    "KR": {
        "name": "한국",
        "broad_benchmark": "069500.KS",  # KODEX 200
        "broad_label": "KOSPI 200 (KODEX 200)",
        "currency": "KRW",
        "currency_symbol": "₩",
        "trading_hours": "09:00-15:30 KST",
    },
    "JP": {
        "name": "일본",
        "broad_benchmark": "1306.T",     # NEXT FUNDS TOPIX
        "broad_label": "TOPIX (1306)",
        "currency": "JPY",
        "currency_symbol": "¥",
        "trading_hours": "09:00-15:00 JST",
    },
    # Phase 4-CN split (2026-05-18, user-ratified Option α): CN A-share
    # mainland market (上海 + 深圳) and HK Hong Kong are STRUCTURALLY
    # different markets — different currency (CNY ¥ vs HKD HK$),
    # different daily price limits (A-share ±10% / STAR + ChiNext ±20%
    # / HK no limit), different regulator (CSRC vs SFC), different
    # GFW geofence (A-share market data partly geofenced, HK fully
    # accessible). Treating them as one "CN/HK" market would mix
    # currencies in the canonical 시총 directive and pick the wrong
    # benchmark for HK names. Two separate entries below.
    "CN_A": {
        "name": "중국 본토",
        "broad_benchmark": "510300.SS",  # CSI 300 ETF (Shanghai)
        "broad_label": "CSI 300 (510300)",
        "currency": "CNY",
        "currency_symbol": "¥",
        "trading_hours": "09:30-15:00 CST",
    },
    "HK": {
        "name": "홍콩",
        "broad_benchmark": "2800.HK",    # Tracker Fund HK (tracks HSI)
        "broad_label": "Hang Seng (2800.HK)",
        "currency": "HKD",
        "currency_symbol": "HK$",
        "trading_hours": "09:30-16:00 HKT",
    },
    "TW": {
        "name": "대만",
        "broad_benchmark": "0050.TW",    # 元大台灣50 (TAIEX 50 ETF)
        "broad_label": "TAIEX 50 (0050)",
        "currency": "TWD",
        "currency_symbol": "NT$",
        "trading_hours": "09:00-13:30 CST (Taipei)",
    },
}


def detect_market(ticker: str) -> str:
    """Return one of 'US', 'KR', 'JP', 'CN_A', 'HK', 'TW'. Defaults to
    'US' for suffix-less tickers — that's what the help text instructs
    users to type for American listings.

    Phase 4-CN (2026-05-18): split previous single 'CN' market into
    'CN_A' (.SS / .SZ → CNY ¥, ±10% / ±20% daily limits) and 'HK'
    (.HK → HKD HK$, no daily limit). Existing 'CN' string never
    appears as a return value from this commit forward — any
    downstream callers that still compared against 'CN' have been
    updated in the same commit.
    """
    t = (ticker or "").upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        return "KR"
    if t.endswith(".T"):
        return "JP"
    if t.endswith(".TW") or t.endswith(".TWO"):
        return "TW"
    if t.endswith(".SS") or t.endswith(".SZ") or t.endswith(".BJ"):
        return "CN_A"
    if t.endswith(".HK"):
        return "HK"
    return "US"


def detect_cn_sub_market(ticker: str) -> str | None:
    """Phase 4-CN sub-market detector (RULE 13 helper). CN_A and HK
    each have multiple sub-boards with different daily price limits +
    listing rules — required so the fundamentals analyst can reason
    correctly about extreme intraday moves.

    Returns one of:
     • 'CN_A_MAIN'    — 600/601/603/605.SS or 000/001.SZ  (±10%)
     • 'CN_A_STAR'    — 688.SS  (科創板, ±20%, 신상장 첫 5거래일 ±30%)
     • 'CN_A_CHINEXT' — 300/301.SZ  (創業板, ±20%, 신상장 첫 5거래일 ±30%)
     • 'CN_A_BJSE'    — .BJ  (북경증권거래소, ±30%, yfinance 커버리지 미약)
     • 'HK_MAIN'      — 0001-3999 / 6000-8999.HK  (제한 없음)
     • 'HK_GEM'       — 8XXX.HK  (창업판, 유동성 낮음)

    Returns None for non-CN/HK tickers. The caller (RULE 13 text)
    appends the resulting ±limit to the daily-move sanity check.
    """
    t = (ticker or "").upper()
    if t.endswith(".SS"):
        prefix = t.split(".")[0]
        if prefix.startswith("688"):
            return "CN_A_STAR"
        return "CN_A_MAIN"
    if t.endswith(".SZ"):
        prefix = t.split(".")[0]
        if prefix.startswith("300") or prefix.startswith("301"):
            return "CN_A_CHINEXT"
        return "CN_A_MAIN"
    if t.endswith(".BJ"):
        return "CN_A_BJSE"
    if t.endswith(".HK"):
        prefix = t.split(".")[0]
        # HK GEM uses 8XXX numeric prefix; main board uses 0001-3999
        # and 6000-8999 minus the GEM block. yfinance pads to 4 digits
        # so '8' → '0008' (CNOOC, main board). Need to check ≥ 4 chars
        # before treating the first '8' as GEM.
        if len(prefix) == 4 and prefix.startswith("8"):
            return "HK_GEM"
        return "HK_MAIN"
    return None


# Common KR company names that users type in romanized form but yfinance
# doesn't recognize without the exchange-qualified ticker. NAVER /
# 2026-05-17 burnt the full pipeline on '/NAVER' producing a hollow
# 'Sell' decision on empty data — this map intercepts that class of
# input upstream of the analyzer.
#
# Only UNAMBIGUOUS aliases live here. 'LG' / 'SK' / 'HYUNDAI' map to
# multiple listed entities (LG전자 / LG화학 / LG에너지솔루션 etc.) so
# they intentionally resolve to None and the router replies with the
# Korean-name path's 'multiple matches' message instead of guessing.
_KR_ENGLISH_ALIAS = {
    "NAVER": "035420.KS",
    "KAKAO": "035720.KS",
    "SAMSUNG": "005930.KS",      # default to 삼성전자 (보통주)
    "HYNIX": "000660.KS",        # SK하이닉스
    "SKHYNIX": "000660.KS",
    "POSCO": "005490.KS",        # POSCO 홀딩스
    "CELLTRION": "068270.KS",
    "KAKAOBANK": "323410.KS",
    "KAKAOPAY": "377300.KS",
    "KRAFTON": "259960.KS",
    "COUPANG": None,             # NYSE-listed CPNG; user should type /CPNG
    "LG": None,
    "SK": None,
    "HYUNDAI": None,
    "LOTTE": None,
    "CJ": "001040.KS",           # CJ주식회사 (지주사); 제일제당→097950.KS
    "CJFOOD": "097950.KS",       # CJ제일제당
    "CJENM": "035760.KS",        # CJ ENM
    "CJLOGISTICS": "000120.KS",  # CJ대한통운
}


def resolve_english_alias(token: str) -> str | None:
    """Map a romanized company name (NAVER / TOYOTA / etc.) to the
    yfinance-format ticker, or None when the alias is ambiguous /
    unknown. Returns the canonical ticker for direct routing into the
    analyzer; the caller still applies its own ticker validation.

    Searches _KR_ENGLISH_ALIAS / _JP_ENGLISH_ALIAS / _TW_ENGLISH_ALIAS /
    _CN_ENGLISH_ALIAS in that order. Each market has its own dict so a
    clash like 'SUMITOMO' (could be 三井住友 vs 住友商事) can be resolved
    with explicit market-specific keys. KR + JP precedence is historical
    (those markets were added first); TW added in Phase 4-TW; CN added
    in Phase 4-CN. None of the four dicts share keys today, so order
    doesn't matter in practice.

    Dual-listing default policy (user-ratified 2026-05-18 Option α):
    A-share default + HK 명시 case-by-case. BYD → 002594.SZ default
    (NOT 1211.HK), SMIC → 688981.SS default. ICBC default 1398.HK
    because HK is much more liquid for SOE banks. Tencent / Alibaba /
    Meituan / NetEase have no A-share counterpart so default to
    .HK trivially."""
    if not token:
        return None
    upper = token.upper()
    return (
        _KR_ENGLISH_ALIAS.get(upper)
        or _JP_ENGLISH_ALIAS.get(upper)
        or _TW_ENGLISH_ALIAS.get(upper)
        or _CN_ENGLISH_ALIAS.get(upper)
    )


# Corporate-form tokens that pollute a Naver-news search. Korean news
# indexes the bare brand ('네이버'), never the legal suffix ('네이버(주)')
# or the DART English registration ('NAVER'). Strip these (prefix or
# suffix, fullwidth + the ㈜ glyph included).
_KR_CORP_FORM_TOKENS = (
    "㈜", "(주)", "（주）", "(유)", "(재)", "(사)", "(학)", "(의)",
    "주식회사", "유한회사", "유한책임회사",
)


def kr_news_query_name(name: str | None) -> str:
    """Best Naver-news search term from a KR corp name.

    Korean news search returns ~0 hits for the DART English registration
    ('NAVER' for 035420) or the legal-form suffix ('네이버(주)'), so strip
    the corporate-form tokens and hand back the bare brand ('네이버').
    Returns the input cleaned of those tokens; '' when nothing is left.

    Surfaced 2026-06-08: the NAVER detail page + the main analysis
    pipeline both searched 'NAVER' (English) and got 0 Korean articles.
    Universal — every KR ticker's news query routes through this."""
    if not name:
        return ""
    s = str(name).strip()
    for tok in _KR_CORP_FORM_TOKENS:
        s = s.replace(tok, " ")
    return " ".join(s.split()).strip()


def has_hangul(text: str | None) -> bool:
    """True when ``text`` contains at least one Hangul syllable — used to
    decide whether a candidate news-search term is actually Korean (the
    English DART registration like 'NAVER' is not and returns 0 hits)."""
    if not text:
        return False
    return any("가" <= ch <= "힣" for ch in str(text))


# Romanized JP company aliases. Same problem as the KR map (NAVER /
# 035420.KS) — users type TOYOTA expecting the bot to resolve, but
# yfinance has no such ticker. Most names are unambiguous; the few
# that aren't (MITSUBISHI = 商事 vs 重工 vs UFJ, SOFTBANK = group vs
# 9434 telecom) are mapped to the most-queried form, with explicit
# disambiguated variants (MITSUBISHIUFJ, SOFTBANKCORP) for users who
# actually want the other one.
_JP_ENGLISH_ALIAS = {
    "TOYOTA": "7203.T",
    "SONY": "6758.T",
    "HONDA": "7267.T",
    "NISSAN": "7201.T",
    "SUZUKI": "7269.T",
    "SUBARU": "7270.T",
    "SOFTBANK": "9984.T",        # default: SoftBank Group (holding co)
    "SOFTBANKGROUP": "9984.T",
    "SOFTBANKCORP": "9434.T",    # explicit: telecom subsidiary
    "NINTENDO": "7974.T",
    "HITACHI": "6501.T",
    "MITSUBISHI": "8058.T",      # default: 三菱商事 (most-queried 三菱)
    "MITSUBISHIUFJ": "8306.T",
    "MUFG": "8306.T",
    "SMFG": "8316.T",
    "MIZUHO": "8411.T",
    "TOKYOELECTRON": "8035.T",
    "ADVANTEST": "6857.T",
    "KEYENCE": "6861.T",
    "FANUC": "6954.T",
    "FASTRETAILING": "9983.T",
    "UNIQLO": "9983.T",
    "RECRUIT": "6098.T",
    "TAKEDA": "4502.T",
    "ASTELLAS": "4503.T",
    "DAIICHISANKYO": "4568.T",
    "EISAI": "4523.T",
    "CHUGAI": "4519.T",
    "SHINETSU": "4063.T",
    "DENSO": "6902.T",
    "KOMATSU": "6301.T",
    "KUBOTA": "6326.T",
    "DAIKIN": "6367.T",
    "NTT": "9432.T",
    "KDDI": "9433.T",
    "RAKUTEN": "4755.T",
    "MERCARI": "4385.T",
    "NIDEC": "6594.T",
    "MURATA": "6981.T",
    "CANON": "7751.T",
    "PANASONIC": "6752.T",
    "BRIDGESTONE": "5108.T",
    "ITOCHU": "8001.T",
    "MITSUI": "8031.T",          # default: 三井物産 (trading)
    "MITSUIFUDOSAN": "8801.T",
    "MITSUBISHIESTATE": "8802.T",
    "JR": "9020.T",              # default: JR East
    "JREAST": "9020.T",
    "JRWEST": "9021.T",
    "NIPPONSTEEL": "5401.T",
    "JFE": "5411.T",
    "ROHM": "6963.T",
    "RENESAS": "6723.T",
    "DISCO": "6146.T",
    "LASERTEC": "6920.T",
    "OLYMPUS": "7733.T",
}


# Common TW company names users type in romanized form. TW market has
# strong English brand recognition (TSMC, MediaTek, Hon Hai / Foxconn,
# UMC) so we cover both forms — corporate English (TSMC) and brand /
# foreign-trade names (Foxconn for 鴻海). Unlike KR/JP, many TW names
# also have well-known ADRs (TSMC→TSM, UMC→UMC, AUO→AUO) — we route
# the bare alias to the .TW listing by default; users wanting the ADR
# should type the bare US symbol (/TSM) directly.
_TW_ENGLISH_ALIAS = {
    # ─── Semiconductor foundry / IDM
    "TSMC": "2330.TW",
    "TAIWANSEMI": "2330.TW",
    "UMC": "2303.TW",
    "VANGUARD": "5347.TWO",          # 世界先進 Vanguard International Semi
    "MOSEL": "2342.TW",              # 茂矽
    "WIN": "3105.TWO",                # 穩懋 WIN Semi (GaAs)
    # ─── Semiconductor IC design / fabless
    "MEDIATEK": "2454.TW",
    "REALTEK": "2379.TW",
    "NOVATEK": "3034.TW",            # 聯詠
    "PHISON": "8299.TWO",            # 群聯 Phison Electronics
    "PARADE": "4966.TW",             # Parade Technologies
    "EFFICHIPS": "6488.TWO",         # 環球晶 GlobalWafers (silicon wafer)
    "GLOBALWAFERS": "6488.TWO",
    # ─── OSAT / packaging-test
    "ASE": "3711.TW",                # 日月光投控 ASE Technology Holding
    "ASETECH": "3711.TW",
    "POWERTECH": "6239.TW",          # 力成
    "KINGYUAN": "2449.TW",           # 京元電子
    # ─── EMS / electronics manufacturing services
    "HONHAI": "2317.TW",
    "FOXCONN": "2317.TW",
    "QUANTA": "2382.TW",
    "PEGATRON": "4938.TW",
    "COMPAL": "2324.TW",
    "WISTRON": "3231.TW",
    "INVENTEC": "2356.TW",
    # ─── Thermal / AI server cooling (Blackwell-cycle plays)
    "AURAS": "3324.TWO",              # 雙鴻 Auras Technology
    "ASIAVITAL": "3017.TW",          # 奇鋐 AVC (AI server thermal)
    "AVC": "3017.TW",
    "SUNON": "2421.TW",              # 建準 (PC cooling fans)
    # ─── PCB / 載板
    "TRIPOD": "3044.TW",             # 健鼎
    "COMPEQ": "2358.TW",
    "UNIMICRON": "3037.TW",          # 欣興 (ABF carrier)
    "NANYAPCB": "8046.TW",           # 南電
    "GCE": "6213.TWO",
    # ─── Optical / camera
    "LARGAN": "3008.TW",             # 大立光
    "GENIUSOPTICAL": "3406.TW",      # 玉晶光 Genius Electronic Optical
    "AAC": None,                     # AAC is HK 2018.HK, not TW
    # ─── Display panel
    "AUO": "2409.TW",
    "INNOLUX": "3481.TW",
    # ─── Financial holdings
    "FUBON": "2881.TW",              # 富邦金
    "CATHAY": "2882.TW",             # 國泰金
    "MEGAFIN": "2886.TW",            # 兆豐金
    "CTBC": "2891.TW",               # 中信金
    "EVASUN": "2884.TW",             # 玉山金
    "ESUN": "2884.TW",
    "FIRSTFIN": "2892.TW",           # 第一金
    "SHINKONG": "2888.TW",           # 新光金
    # ─── Telecom
    "CHUNGHWA": "2412.TW",           # 中華電
    "FET": "4904.TW",                # 遠傳
    "TAIWANMOBILE": "3045.TW",       # 台灣大
    # ─── Shipping
    "EVERGREEN": "2603.TW",          # 長榮 (container)
    "YANGMING": "2609.TW",
    "WANHAI": "2615.TW",
    # ─── Petrochemical
    "FORMOSAPLAS": "1301.TW",        # 台塑
    "FPC": "1301.TW",
    "NANYAPLAS": "1303.TW",          # 南亞塑膠
    "FORMOSACHEM": "1326.TW",        # 台化
    # ─── Auto / EV (TW has Yulon group)
    "YULON": "2201.TW",              # 裕隆
    "HOTAI": "2207.TW",              # 和泰汽車 (Toyota Taiwan distributor)
    # ─── Steel
    "CSC": "2002.TW",                # 中鋼
    # ─── Bio / pharma
    "PHARMAESSENTIA": "6446.TW",     # 藥華藥
    "TAIMED": "4147.TWO",            # 中裕新藥
    # ─── Retail / consumer
    "PRESIDENT": "2912.TW",          # 統一超 (7-Eleven Taiwan)
    "UNIPRES": "1216.TW",            # 統一企業
    "EVASUNAIR": "2618.TW",          # 長榮航
    "EVAAIR": "2618.TW",
    "CHINAAIR": "2610.TW",
    # ─── Ambiguous — multi-entity, force user to specify
    "TAIWAN": None,
    "FOREIGN": None,
}


# Phase 4-CN English alias map (2026-05-18, user-ratified Option α
# 'A주 default + HK 명시'). Covers ~60 names that channel users
# routinely type in romanized form. Each entry maps to the canonical
# yfinance ticker; dual-listed names default to the listing that
# matters more for the analysis (A-share when liquidity dominates the
# domestic story, HK when the SOE / Internet VIE story dominates).
#
# Ambiguous keys (group conglomerate names mapping to multiple listed
# entities) resolve to None and the router replies with the
# 'multiple matches' message instead of guessing — same convention as
# the KR / JP / TW maps.
_CN_ENGLISH_ALIAS = {
    # ─── Internet / Tech (HK-listed VIE structures, no A-share counterpart)
    "TENCENT": "0700.HK",
    "ALIBABA": "9988.HK",
    "BABA": "9988.HK",                 # default HK; the US ADR is BABA on NYSE — user types /BABA directly
    "JDCOM": "9618.HK",
    "JD": "9618.HK",
    "MEITUAN": "3690.HK",
    "NIO": "9866.HK",
    "XPENG": "9868.HK",
    "XPEV": "9868.HK",
    "LIAUTO": "2015.HK",
    "LI": "2015.HK",
    "KUAISHOU": "1024.HK",
    "BILIBILI": "9626.HK",
    "NETEASE": "9999.HK",
    "BAIDU": "9888.HK",
    # ─── 国有 4大 银行 + foreign banks (HK 더 liquid)
    "ICBC": "1398.HK",                 # 工商银行
    "CCB": "0939.HK",                  # 建设银行
    "BOC": "3988.HK",                  # 中国银行
    "ABC": "1288.HK",                  # 农业银行
    "BOCOM": "3328.HK",                # 交通银行
    "HSBC": "0005.HK",
    "STANCHART": "2888.HK",
    "STAN": "2888.HK",
    "PINGAN": "2318.HK",
    "PING": "2318.HK",                 # Ping An default HK
    "AIA": "1299.HK",
    # ─── 통신 / 유틸 (HK SOE telecom)
    "CHINAMOBILE": "0941.HK",
    "CHINATELECOM": "0728.HK",
    "CHINAUNICOM": "0762.HK",
    "POWERASSETS": "0006.HK",
    "CLP": "0002.HK",
    # ─── 항공 / 석유 (HK SOE energy)
    "AIRCHINA": "0753.HK",
    "CATHAY": "0293.HK",               # 國泰航空 (NOT 國泰金 TW 2882)
    "CATHAYPACIFIC": "0293.HK",
    "SINOPEC": "0386.HK",
    "PETROCHINA": "0857.HK",
    "CNPC": "0857.HK",
    "CNOOC": "0883.HK",
    # ─── A주 백주 (mainland-only)
    "MOUTAI": "600519.SS",
    "KWEICHOWMOUTAI": "600519.SS",
    "WULIANGYE": "000858.SZ",
    "LUZHOU": "000568.SZ",
    "LUZHOULAOJIAO": "000568.SZ",
    "FENJIU": "600809.SS",
    # ─── EV / Battery (A주 default per user policy)
    "BYD": "002594.SZ",                # 比亚迪 A주 default; HK 1211.HK 명시
    "CATL": "300750.SZ",               # 寧德時代 ChiNext
    "EVE": "300014.SZ",                # 億緯鋰能 ChiNext
    "EVEENERGY": "300014.SZ",
    "GANFENG": "002460.SZ",            # 贛鋒鋰業
    # ─── Tech / Semis (A주 默認, mostly STAR/ChiNext)
    "SMIC": "688981.SS",               # 中芯國際 STAR; HK 0981.HK 명시
    "HUAHONG": "1347.HK",              # 華虹半導體 — HK only
    "WILLSEMI": "603501.SS",           # 韋爾股份 — main board
    "MAXSCEND": "300782.SZ",           # 卓勝微 ChiNext
    "AMEC": "688012.SS",               # 中微公司 STAR
    "LONGI": "601012.SS",              # 隆基綠能 main board
    "TONGWEI": "600438.SS",            # 通威股份 main board
    "SUNGROW": "300274.SZ",            # 陽光電源 ChiNext
    "TCL": "002129.SZ",                # TCL中環
    # ─── Property (HK 主体)
    "POLY": "600048.SS",               # 保利發展
    "VANKE": "000002.SZ",              # 萬科
    "COUNTRYGARDEN": "2007.HK",        # 碧桂園
    "CHINAOVERSEAS": "0688.HK",
    "CRLAND": "1109.HK",               # 華潤置地
    "EVERGRANDE": "3333.HK",
    # ─── 가전 (A주 main)
    "MIDEA": "000333.SZ",              # 美的集團
    "GREE": "000651.SZ",               # 格力電器
    "YILI": "600887.SS",               # 伊利股份
    "GOERTEK": "002241.SZ",            # 歌爾股份
    # ─── 보험 (HK 주, also A주)
    "AIA1299": "1299.HK",
    "NCI": "1336.HK",                  # 新華保險
    "PICC": "1339.HK",                 # 人保
    # ─── Ambiguous — multi-entity, force user to specify
    "CHINA": None,
    "BANKOFCHINA": None,               # ambiguous: BOC (3988.HK) vs BOCHK (2388.HK) vs BOCOM (3328.HK)
}


# Hardcoded peer sets keyed by yfinance 'industry' string. The
# fundamentals analyst kept cargo-culting peer examples across
# industries (한국전력공사 → KMI/WMB/ENB; 호텔신라 → 005930/000660)
# despite a prompt-level INDUSTRY CONSTRAINT — text rules alone can't
# stop the LLM picking convenient names. Pre-fetch the right peer set
# from this dict at analyze time and inject it as MANDATORY so the
# analyst has no choice.
#
# Maintenance: add a row when a new KR-listed subject hits an industry
# we haven't covered. Each row mixes KR-listed peers first (.KS/.KQ),
# then ADR / TWSE / TSE peers second for broader context. Subject
# ticker itself is filtered out by resolve_peer_set so the analyst
# never compares a company to itself.
_KR_INDUSTRY_PEERS = {
    "Semiconductors": [
        # ⚠️ TSM(ADR) 이 아니라 홈상장 2330.TW 를 쓴다. yfinance 는 ADR 에
        # 가격은 USD, 재무는 TWD 로 줘서 자산·매출 기반 배수가 통째로
        # 틀린다 — 실측(2026-08-18): TSM PBR **89.88** vs 2330.TW **9.575**
        # (같은 회사, 같은 시점). 화면의 ⚠ 는 오차가 있다고만 알릴 뿐
        # 9배 틀린 숫자를 그대로 보여준다(사용자 판단: 홈상장으로 교체).
        "000660.KS", "005930.KS", "2330.TW", "INTC", "AMD", "NVDA",
    ],
    "Semiconductor Equipment & Materials": [
        "240810.KQ", "042700.KS", "036930.KQ", "095610.KQ",
        "319660.KQ", "AMAT", "LRCX", "KLAC",
    ],
    "Specialty Chemicals": [
        "005290.KQ", "036830.KQ", "093370.KS", "102710.KQ",
        "014680.KS",
    ],
    "Electronic Components": [
        "011070.KS", "090460.KS", "353200.KS", "009150.KS",
        "6981.T", "6762.T",
    ],
    "Consumer Electronics": [
        "005930.KS", "066570.KS", "6758.T", "0992.HK",
    ],
    "Utilities - Regulated Electric": [
        "015760.KS", "DUK", "SO", "AEP", "NEE",
    ],
    "Utilities - Regulated Gas": [
        "036460.KS", "071320.KS",
    ],
    "Specialty Retail": [
        "069960.KS", "057050.KS", "008770.KS", "TPR", "LULU",
    ],
    "Department Stores": [
        "004170.KS", "004990.KS", "069960.KS", "M", "JWN",
    ],
    "Internet Content & Information": [
        "035420.KS", "035720.KS", "067160.KQ", "035080.KQ", "4689.T",
    ],
    "Auto Manufacturers": [
        # ⚠️ TM(도요타 ADR) 이 아니라 홈상장 7203.T. 감사 실측(2026-08-18):
        # TM 은 USD 거래·JPY 재무라 PBR **15.29** / PSR **0.0043** 인데
        # 7203.T 는 PBR **0.959** 다 — 16배 오차(TSM 사례와 같은 형태).
        "005380.KS", "000270.KS", "7203.T", "F", "GM",
    ],
    "Auto Parts": [
        # ⚠️ DNZOY(덴소 ADR)가 아니라 홈상장 6902.T. 감사 실측(2026-08-18):
        # ADR 은 USD 거래·JPY 재무라 PSR **0.0039**(150배 오차) — 도구가
        # 후보 6902.T 를 조회해 `✅ 교체 가능 DENSO CORP 거래=JPY 재무=JPY`
        # 로 확인했다(TSM·TM 과 같은 형태).
        "012330.KS", "204320.KS", "018880.KS", "6902.T",
    ],
    "Banks - Regional": [
        "086790.KS", "316140.KS", "024110.KS", "138930.KS",
    ],
    "Banks - Diversified": [
        "055550.KS", "105560.KS", "086790.KS",
    ],
    "Insurance - Diversified": [
        "001450.KS", "032830.KS", "000810.KS",
    ],
    "Drug Manufacturers - General": [
        "207940.KS", "068270.KS", "326030.KS",
    ],
    # Fix I (2026-05-19 노바렉스 194700.KS surfaced) — KR pharma /
    # health / nutraceutical 종목들이 yfinance 가 'Specialty Chemicals'
    # / 'Drug Manufacturers - Specialty & Generic' / 'Personal Products'
    # / 'Packaged Foods' 중 어디로 분류할 지 예측 불가. 모든 카테고리에
    # KR health 종목 매핑 추가 → 노바렉스 / 콜마비앤에이치 등이 어느
    # 분류로 떨어지든 적절한 health peers 받음.
    "Drug Manufacturers - Specialty & Generic": [
        "170900.KS",  # 동아ST
        "271980.KS",  # 제일약품
        "002310.KS",  # 아세아제지 (대원제약 우선주 등은 일부 매핑 까다로움)
        "194700.KQ",  # 노바렉스 — 자체 OK (subject 필터 됨)
        "200130.KQ",  # 콜마비앤에이치
        "183490.KQ",  # 엔지켐생명과학
    ],
    "Personal Products": [
        "051900.KS",  # LG생활건강
        "090430.KS",  # 아모레퍼시픽
        "192820.KS",  # 코스맥스
        "200130.KQ",  # 콜마비앤에이치
        "161890.KS",  # 한국콜마
        "194700.KQ",  # 노바렉스 (건기식 ODM)
    ],
    # Fix M (2026-05-19 LG생활건강 051900.KS surfaced): yfinance 가
    # 일부 KR 종목을 'Household & Personal Products' (긴 form) 로
    # 분류 — Fix I 의 'Personal Products' (짧은 form) 와 exact-string
    # match 실패. 두 형태 모두 같은 peer set 으로 inject.
    "Household & Personal Products": [
        "051900.KS",  # LG생활건강
        "090430.KS",  # 아모레퍼시픽
        "192820.KS",  # 코스맥스
        "200130.KQ",  # 콜마비앤에이치
        "161890.KS",  # 한국콜마
        "194700.KQ",  # 노바렉스
    ],
    "Packaged Foods": [
        "097950.KS",  # CJ제일제당
        "004370.KS",  # 농심
        "007310.KS",  # 오뚜기
        "005180.KS",  # 빙그레
        "271560.KS",  # 오리온
    ],
    "Biotechnology": [
        "207940.KS", "068270.KS", "326030.KS", "REGN", "VRTX",
    ],
    "Steel": [
        "005490.KS", "004020.KS", "002380.KS",
    ],
    "Shipbuilding": [
        "010140.KS",  # 삼성중공업
        "009540.KS",  # HD한국조선해양 (지주사)
        "329180.KS",  # HD현대중공업
        "042660.KS",  # 한화오션
    ],
    "Aerospace & Defense": [
        "047810.KS", "012450.KS", "079550.KS", "LMT", "RTX",
    ],
    "Software - Application": [
        "352820.KS", "035420.KS", "035720.KS", "MSFT", "CRM",
    ],
    "Software - Infrastructure": [
        "MSFT", "ORCL", "CRM",
    ],
    "Travel Services": [
        "008770.KS", "035250.KS",  # 호텔신라, 강원랜드
    ],
    "Lodging": [
        "008770.KS", "035250.KS",
    ],
    "Resorts & Casinos": [
        "035250.KS", "114090.KS",
    ],
    # KR 지주회사 / 복합기업 (Industrials → Conglomerates) — surfaced by
    # 두산 000150.KS 2026-05-18 case where no peer set existed and the
    # analyst cargo-culted 기아 / 현대차 / 삼성화재 / LG (3 of 4 wrong
    # industries: 자동차 / 보험). KR major holding companies listed by
    # mkcap; covers 두산 (this case), 한화, GS, CJ, LG, SK, LS.
    "Conglomerates": [
        "000150.KS",  # 두산
        "000880.KS",  # 한화
        "078930.KS",  # GS
        "001040.KS",  # CJ
        "003550.KS",  # LG
        "034730.KS",  # SK
        "006260.KS",  # LS
    ],
}


# KSIC (한국표준산업분류) → yfinance industry tag mapping. KSIC codes
# returned by DART's /api/company.json `induty_code` field are 5-digit
# strings (no 'C' / 'K' prefix). This is the authoritative industry
# classification for KR-listed companies — yfinance industry tags are
# often wrong (010140.KS 삼성중공업 surfaced 2026-05-23 as 'Aerospace
# & Defense' when its KSIC C31111 강선건조업 = Shipbuilding).
#
# Longest-prefix matching: '31111' (강선건조) wins over '3111' fallback,
# so specific subcodes get specific tags. Unmapped codes fall through
# to yfinance industry (preserves prior behavior — never blocks an
# analysis). Mapping intentionally partial — covers the ~25 industries
# we have curated peer sets / sector ETFs for; new KSIC codes can be
# added as we surface more mis-tags in production.
#
# Rule applies to all analyses going forward — KR-specific data source
# but the override hooks into the universal info-dict patch pipeline,
# so all downstream (Comps peer set, sector ETF, RULE 10 dominant
# variable inference) inherits the corrected industry automatically.
_KSIC_PREFIX_TO_INDUSTRY: list[tuple[str, str]] = [
    # 조선 / 해양플랜트 (KSIC C31xxx)
    ("31111", "Shipbuilding"),
    ("31112", "Shipbuilding"),
    ("31120", "Shipbuilding"),
    ("3111",  "Shipbuilding"),
    ("3112",  "Shipbuilding"),
    # 항공우주 (KSIC C31313 / C31321)
    ("31311", "Aerospace & Defense"),
    ("31321", "Aerospace & Defense"),
    ("31322", "Aerospace & Defense"),
    # 자동차 완성차 (KSIC C30xxx)
    ("30110", "Auto Manufacturers"),
    ("30121", "Auto Manufacturers"),
    ("30122", "Auto Manufacturers"),
    ("30199", "Auto Manufacturers"),
    ("30201", "Auto Parts"),
    ("30203", "Auto Parts"),
    ("30310", "Auto Parts"),
    ("30391", "Auto Parts"),
    ("30399", "Auto Parts"),
    # 반도체 (KSIC C26)
    ("26110", "Semiconductors"),
    ("26111", "Semiconductors"),
    ("26112", "Semiconductors"),
    ("26120", "Semiconductors"),
    ("26121", "Semiconductors"),
    # 반도체 장비 / 재료
    ("29280", "Semiconductor Equipment & Materials"),
    # 전자부품
    ("26211", "Electronic Components"),
    ("26221", "Electronic Components"),
    ("26222", "Electronic Components"),
    ("26299", "Electronic Components"),
    # 디스플레이 / consumer electronics
    ("26410", "Consumer Electronics"),
    ("26421", "Consumer Electronics"),
    # 철강 (KSIC C24)
    ("24111", "Steel"),
    ("24112", "Steel"),
    ("24190", "Steel"),
    ("24210", "Steel"),
    ("2411",  "Steel"),
    # 화학 (KSIC C20)
    ("20111", "Specialty Chemicals"),
    ("20112", "Specialty Chemicals"),
    ("20119", "Specialty Chemicals"),
    ("20211", "Specialty Chemicals"),
    ("20299", "Specialty Chemicals"),
    # 2차전지 / 배터리
    ("28202", "Specialty Chemicals"),
    # 제약 / 바이오
    ("21100", "Drug Manufacturers - General"),
    ("21210", "Drug Manufacturers - General"),
    ("21300", "Drug Manufacturers - General"),
    # 식품
    ("10301", "Packaged Foods"),
    ("10711", "Packaged Foods"),
    ("10712", "Packaged Foods"),
    ("10499", "Packaged Foods"),
    # 화장품
    ("20423", "Personal Products"),
    # 백화점 / 소매
    ("47111", "Department Stores"),
    ("47190", "Specialty Retail"),
    # 건설
    ("41122", "Engineering & Construction"),
    ("41129", "Engineering & Construction"),
    ("41210", "Engineering & Construction"),
    # 전력 / 가스
    ("35111", "Utilities - Regulated Electric"),
    ("35112", "Utilities - Regulated Electric"),
    ("35200", "Utilities - Regulated Gas"),
    # 해운 / 항공
    ("50111", "Marine Shipping"),
    ("50112", "Marine Shipping"),
    ("51100", "Airlines"),
    # 은행 / 보험 / 증권
    ("64191", "Banks - Diversified"),
    ("64192", "Banks - Regional"),
    ("65111", "Insurance - Diversified"),
    ("65120", "Insurance - Diversified"),
    ("66120", "Capital Markets"),
    # 인터넷 / SW / 게임
    ("58211", "Electronic Gaming & Multimedia"),
    ("58221", "Software - Application"),
    ("58222", "Software - Application"),
    ("63111", "Internet Content & Information"),
    ("63112", "Internet Content & Information"),
    # 통신
    ("61210", "Telecom Services"),
    ("61220", "Telecom Services"),
]


def resolve_ksic_industry(induty_code: str | None) -> str | None:
    """Map a 5-digit KSIC code (DART `induty_code`) to a yfinance-style
    industry tag. Longest-prefix wins so a specific subcode (e.g.
    '31111' 강선건조업) matches the specific tag (Shipbuilding) over a
    generic family prefix. Returns None when no prefix matches —
    callers should keep whatever yfinance returned.

    Rule applies to all KR analyses going forward — surfaced by
    삼성중공업 010140.KS 2026-05-23 review (yfinance industry
    'Aerospace & Defense' overrode the correct Shipbuilding peer set
    + dominant-variable mapping)."""
    if not induty_code:
        return None
    code = str(induty_code).strip()
    if not code:
        return None
    best_len = 0
    best_industry: str | None = None
    for prefix, industry in _KSIC_PREFIX_TO_INDUSTRY:
        if code.startswith(prefix) and len(prefix) > best_len:
            best_len = len(prefix)
            best_industry = industry
    return best_industry


def resolve_peer_set(ticker: str, industry: str | None) -> list[str] | None:
    """Return a hand-curated peer ticker list for the subject's industry,
    or None when we don't have one for that industry. The subject ticker
    is filtered out so an analysis never compares a company to itself.
    Capped at 5 peers for prompt brevity.

    Market-aware: picks JP-listed peers for .T tickers, KR-listed for
    .KS/.KQ, TW-listed for .TW/.TWO, CN_A-listed for .SS/.SZ/.BJ,
    HK-listed for .HK, US-listed for bare alpha tickers. Industry
    strings use yfinance's standardized English vocabulary ('Auto
    Manufacturers', 'Banks - Diversified', etc.) so the same industry
    key works across markets — the dict choice is what makes it
    market-specific. Phase 4-CN (2026-05-18) adds `_CN_A_INDUSTRY_PEERS`
    + `_HK_INDUSTRY_PEERS`; dual-listed names appear in both dicts
    under the same industry key (e.g. BYD 002594.SZ in CN_A '자동차',
    1211.HK in HK '자동차')."""
    subj = (ticker or "").upper()
    # Mega-cap tech 정밀화 — Mag-7 급은 산업 무관 대형 테크 풀로(체급 매칭).
    # 좁은 산업 분류(AAPL='Consumer Electronics')가 GoPro/Sonos 같은 미니캡을
    # peer 로 주는 무의미 Comps 차단 (AAPL 2026-06-06 review).
    if subj in _US_MEGACAP_TECH:
        return [t for t in _US_MEGACAP_PEERS if t != subj][:5]
    if not industry:
        return None
    market = detect_market(ticker)
    # Fix M (2026-05-19) — yfinance industry tag aliases. yfinance 가
    # 종종 같은 카테고리를 다른 형태로 보고 ('Personal Products' vs
    # 'Household & Personal Products'). Fuzzy alias map 으로 cross-form
    # match. LG생활건강 051900.KS 케이스 surface.
    industry_aliases = _INDUSTRY_ALIASES.get(industry, [])
    industry_candidates = [industry] + industry_aliases

    if market == "JP":
        peer_dict = _JP_INDUSTRY_PEERS
    elif market == "KR":
        peer_dict = _KR_INDUSTRY_PEERS
    elif market == "TW":
        peer_dict = _TW_INDUSTRY_PEERS
    elif market == "CN_A":
        peer_dict = _CN_A_INDUSTRY_PEERS
    elif market == "HK":
        peer_dict = _HK_INDUSTRY_PEERS
    else:
        peer_dict = _US_INDUSTRY_PEERS

    base = None
    for candidate in industry_candidates:
        base = peer_dict.get(candidate)
        if base:
            break
    if not base:
        return None
    subject = (ticker or "").upper()
    peers = [t for t in base if t.upper() != subject]
    return peers[:5] or None


# Fix M (2026-05-19): yfinance industry tag aliases. yfinance 가
# 같은 카테고리를 다른 형태로 분류하는 경우 (예: 'Personal Products'
# vs 'Household & Personal Products', 'Drug Manufacturers - General'
# vs 'Drug Manufacturers - Specialty & Generic'). 종목 검증에서
# surface 되면 여기 alias map 에 추가. resolve_peer_set 가 양방향
# fallback — 한 가지 form 으로 peer dict 키 등록되어 있어도 다른
# form 의 industry tag 도 같은 peer set 받음.
_INDUSTRY_ALIASES: dict[str, list[str]] = {
    # Personal Products family
    "Personal Products": ["Household & Personal Products"],
    "Household & Personal Products": ["Personal Products"],
    # Drug Manufacturers family
    "Drug Manufacturers - General": ["Drug Manufacturers - Specialty & Generic"],
    "Drug Manufacturers - Specialty & Generic": ["Drug Manufacturers - General"],
    # Packaged Foods family (yfinance 'Food Distribution' / 'Food Products')
    "Packaged Foods": ["Food Distribution", "Food Products"],
    "Food Distribution": ["Packaged Foods"],
    "Food Products": ["Packaged Foods"],
    # Specialty Chemicals 와 Chemicals 양방향 (yfinance 가 일부 종목을
    # 'Chemicals' 로만 분류, 일부는 'Specialty Chemicals' 로 분류)
    "Specialty Chemicals": ["Chemicals"],
    "Chemicals": ["Specialty Chemicals"],
    # Banks family
    "Banks - Regional": ["Banks - Diversified"],
    "Banks - Diversified": ["Banks - Regional"],
    # Insurance family
    "Insurance - Diversified": ["Insurance - Life", "Insurance - Property & Casualty"],
    "Insurance - Life": ["Insurance - Diversified"],
}


# JP industry peer sets — Nikkei 225 / TOPIX 100 large caps grouped
# by yfinance 'industry' field. Same shape as _KR_INDUSTRY_PEERS:
# 3-5 peers per industry, subject filtered at resolve time, prompt
# injection forces analyst to use exactly these tickers in Comps.
# JP-specific industries (商社 Trading Houses, J-REIT 不動産) added
# as their own keys since yfinance does carry distinct industry
# strings for them.
_JP_INDUSTRY_PEERS = {
    "Auto Manufacturers": [
        "7203.T", "7267.T", "7201.T", "7269.T", "7270.T",
    ],
    "Auto Parts": [
        "6902.T", "7259.T", "5108.T", "7282.T",
    ],
    "Banks - Diversified": [
        "8306.T", "8316.T", "8411.T", "8308.T", "7182.T",
    ],
    "Banks - Regional": [
        "8306.T", "8316.T", "8411.T", "8308.T",
    ],
    "Insurance - Diversified": [
        "8766.T", "8725.T", "8630.T", "8750.T",
    ],
    "Insurance - Life": [
        "8750.T", "8725.T", "8766.T",
    ],
    "Consumer Electronics": [
        "6758.T", "6752.T", "6753.T", "7751.T",
    ],
    "Semiconductors": [
        "6857.T", "6723.T", "6963.T", "6526.T",
    ],
    "Semiconductor Equipment & Materials": [
        "8035.T", "6146.T", "7735.T", "6920.T", "4063.T",
    ],
    "Drug Manufacturers - General": [
        "4502.T", "4503.T", "4519.T", "4568.T", "4523.T",
    ],
    "Software - Application": [
        "4307.T", "6098.T", "4684.T", "4751.T",
    ],
    "Software - Infrastructure": [
        "4307.T", "4684.T",
    ],
    "Telecom Services": [
        "9432.T", "9433.T", "9434.T", "9984.T",
    ],
    "Real Estate Services": [
        "8801.T", "8802.T", "8830.T", "3289.T",
    ],
    "REIT - Diversified": [
        "8951.T", "8953.T", "8954.T", "8960.T",
    ],
    "Steel": [
        "5401.T", "5411.T", "5406.T",
    ],
    "Specialty Chemicals": [
        "4063.T", "4188.T", "4005.T", "3402.T",
    ],
    "Chemicals": [
        "4063.T", "4188.T", "4005.T", "3402.T",
    ],
    "Industrial Machinery": [
        "6301.T", "6326.T", "6367.T", "6273.T", "6954.T",
    ],
    "Specialty Retail": [
        "9983.T", "3382.T", "8267.T", "3099.T",
    ],
    "Department Stores": [
        "3099.T", "8267.T", "8233.T",
    ],
    "Internet Content & Information": [
        "4385.T", "4755.T", "4689.T", "2432.T",
    ],
    "Conglomerates": [
        "8058.T", "8001.T", "8031.T", "8002.T", "8053.T",
    ],
    # JP-specific 商社 (general trading) — yfinance often classifies
    # under 'Conglomerates' but it's a distinct business model worth
    # its own peer set for analysis quality.
    "Trading Companies": [
        "8058.T", "8001.T", "8031.T", "8002.T", "8053.T",
    ],
    "Utilities - Regulated Electric": [
        "9501.T", "9502.T", "9503.T",
    ],
    "Utilities - Regulated Gas": [
        "9531.T", "9532.T",
    ],
    "Railroads": [
        "9020.T", "9021.T", "9022.T", "9005.T", "9007.T",
    ],
    "Beverages - Non-Alcoholic": [
        "2502.T", "2503.T", "2587.T",
    ],
    "Food Distribution": [
        "8267.T", "3382.T",
    ],
    "Apparel Retail": [
        "9983.T", "8273.T",
    ],
    "Aerospace & Defense": [
        "7011.T", "7012.T", "7013.T",
    ],
    "Construction & Engineering": [
        "1801.T", "1802.T", "1803.T", "1812.T",
    ],
    "Shipping": [
        "9101.T", "9104.T", "9107.T",
    ],
}


# US dual-class share structures — Fix ② (2026-05-23, 외부 검증자 제언).
# yfinance 가 GOOG vs GOOGL, BRK.A vs BRK.B 등 차등의결권(dual-class)
# 종목의 발행주식수를 한쪽 class 기준으로 반환해 시총/EPS 계산이 왜곡되는
# 패턴. BRK.B shares가 BRK.A × 1500 인데 yfinance 가 BRK.A 기준으로
# shares 반환하면 시총이 1/1500 수준으로 잡힘. GOOG/GOOGL는 동일 모회사
# (Alphabet) 의 다른 class — total mc 는 같지만 각 class shares 는 다름.
# Rule applies to all US analyses going forward.
_US_DUAL_CLASS_TICKERS: set[str] = {
    # Alphabet (Class C no-vote = GOOG, Class A = GOOGL)
    "GOOG", "GOOGL",
    # Berkshire Hathaway (A = $600K+/share, B = A/1500)
    "BRK-A", "BRK-B", "BRK.A", "BRK.B",
    # Meta Platforms — Class A public, Class B Mark Zuckerberg
    "META",
    # Snap — Class A public, Class B/C founder super-voting
    "SNAP",
    # Lyft, Palantir — dual-class on IPO
    "LYFT", "PLTR",
    # Roblox, Robinhood
    "RBLX", "HOOD",
    # News Corp, Fox Corp — Murdoch dual-class
    "NWS", "NWSA", "FOX", "FOXA",
    # Comcast
    "CMCSA",
    # Viacom/Paramount
    "PARA", "PARAA",
}

# EU dual-class share tickers — full Yahoo ticker form (SUFFIX 포함) since
# class A/B 는 suffix 단위로 분리 (`EPI-A.ST` vs `EPI-B.ST`). 2026-05-29
# Metals & Mining screener review surfaced: EPI-A.ST (Epiroc Class A)
# fired Fix C (shares × price vs marketCap > 5%) because yfinance
# sharesOutstanding 이 Class A 만 (~485M) 반환했으나 marketCap 은 A+B
# 합산 (~1217M × px) 기준 → 40% mismatch → false 'corp action 의심'.
# 스웨덴 A/B 차등의결권 (Investor·Volvo·Ericsson·Wallenberg 계열) +
# 덴마크 A/B (Carlsberg·Novo Nordisk) + 노르웨이 (Schibsted A/B) +
# 독일 Vz/St (Henkel·Porsche·VW preferred) 모두 동일 패턴.
# Rule applies to all analyses going forward.
_EU_DUAL_CLASS_TICKERS: set[str] = {
    # Sweden — Wallenberg + entrepreneur families. .ST 거래소. A 통상
    # 1주 1표, B 통상 1주 0.1표 (또는 1/10). 일부 종목은 C class 도 존재.
    "EPI-A.ST", "EPI-B.ST",          # Epiroc
    "ATCO-A.ST", "ATCO-B.ST",        # Atlas Copco
    "VOLV-A.ST", "VOLV-B.ST",        # Volvo
    "ERIC-A.ST", "ERIC-B.ST",        # Ericsson
    "HM-B.ST",                       # H&M (B 만 public, A 비공개)
    "INVE-A.ST", "INVE-B.ST",        # Investor AB
    "SKF-A.ST", "SKF-B.ST",          # SKF
    "SCA-A.ST", "SCA-B.ST",          # SCA
    "SAAB-B.ST",                     # Saab (B 만 public)
    "KINV-A.ST", "KINV-B.ST",        # Kinnevik
    "LATO-A.ST", "LATO-B.ST",        # Investment Latour
    "INDU-A.ST", "INDU-C.ST",        # Industrivärden
    "SSAB-A.ST", "SSAB-B.ST",        # SSAB
    "TEL2-A.ST", "TEL2-B.ST",        # Tele2
    "ELUX-A.ST", "ELUX-B.ST",        # Electrolux
    "HOLM-A.ST", "HOLM-B.ST",        # Holmen
    # Denmark — .CO 거래소. Foundation-controlled.
    "CARL-A.CO", "CARL-B.CO",        # Carlsberg
    "NOVO-B.CO",                     # Novo Nordisk (B 만 public, A 재단)
    "MAERSK-A.CO", "MAERSK-B.CO",    # A.P. Moller-Maersk
    "ROCK-A.CO", "ROCK-B.CO",        # Rockwool
    # Norway — .OL 거래소.
    "SCHA.OL", "SCHB.OL",            # Schibsted
    # Germany — Vz (Vorzugsaktien preferred non-voting) vs St
    # (Stammaktien voting). yfinance 가 Vz 만 ticker 로 등록한 경우
    # marketCap 은 종종 두 class 합산이라 동일 mismatch 패턴.
    "HEN3.DE",                       # Henkel Vz
    "PAH3.DE",                       # Porsche Automobil Holding Vz
    "VOW3.DE",                       # Volkswagen Vz (VOW.DE = St)
    "FIE.DE",                        # Fielmann
    "BMW3.DE",                       # BMW Vz (BMW.DE = St)
    "RWE3.DE",                       # RWE Vz
    "MEO.DE",                        # Metro Vz
    "DRW3.DE",                       # Drägerwerk Vz
    "SDF3.DE",                       # K+S preferred (historical)
}


def has_kr_preferred_shares(ticker: str) -> bool:
    """KR 보통주에 상장 우선주(코드 끝 5/7)가 존재하면 True.

    삼성전자 005930(보통) ↔ 005935(우선) 처럼 KR 우선주가 별도 코드로
    상장된 경우, yfinance sharesOutstanding 은 보통주만 반환하지만
    marketCap 은 보통주+우선주 합산이라 shares×price vs marketCap 가
    구조적으로 5-15% 괴리 → corp-action 'transitional' false fire 의 원인
    (Samsung 005930 2026-05-31 review surfaced). EU dual-class 와 동일
    클래스 버그.

    KR 우선주 코드 규칙: 보통주 6자리 끝자리가 0 → 우선주는 +5 (005930
    →005935), 2·3우선주는 +7/+K. pykrx 상장목록에 해당 코드가 있으면
    True. pykrx 부재/실패 시 False (보수적 — 진짜 corp-action 은 놓치지
    않음). 네트워크 호출 없이 캐시된 상장목록만 사용."""
    code = (ticker or "").upper().split(".")[0]
    if not (code.isdigit() and len(code) == 6 and code.endswith("0")):
        return False
    pref_candidates = [code[:5] + "5", code[:5] + "7", code[:5] + "K"]
    try:
        from bot.screener import _load_kr_ticker_map
        kr_map = _load_kr_ticker_map()  # 6-digit code → 'KS'/'KQ', 캐시됨
        return any(pc in kr_map for pc in pref_candidates)
    except Exception:
        return False

# US industry peer sets — same shape and intent as the KR / JP dicts.
# Without this, US Comps tables had no MANDATORY PEER SET injection and
# the fundamentals analyst cargo-culted whatever 4-5 names sounded peer-
# adjacent. SNDK 2026-05-11 listed "WDC / MU / STX" mixing storage and
# memory; DELL 2026-05-12 listed "HPQ / IBM" mixing client + services.
# Pre-fetched peer multiples (Rule C) keep the Comps table populated
# even when the LLM has nothing else to anchor on.
#
# TW industry peer sets — same shape as KR/JP/US. TW market is dominated
# by tech supply-chain plays so the granular semiconductor + downstream
# rows here are richer than the equivalent US dict. Mainland CN peers
# are NOT mixed in (different macro variable, different currency) —
# CN gets its own dict in Phase 4-CN. ADR cross-listings (TSM ↔
# 2330.TW, UMC ↔ 2303.TW, AUO ↔ 2409.TW) intentionally NOT mixed in
# either; they trade in different currencies / time zones so mixing
# them in a peer table would confuse multiples.
_TW_INDUSTRY_PEERS = {
    # ─── Semiconductors (most active TW segment)
    "Semiconductors": [
        "2330.TW", "2303.TW", "2454.TW", "2379.TW",  # TSMC/UMC/MTK/Realtek
        "3034.TW", "8299.TWO",                         # Novatek/Phison
    ],
    "Semiconductor Equipment & Materials": [
        "6488.TWO", "3105.TWO", "5347.TWO",            # GlobalWafers/WIN/VIS
    ],
    # ─── OSAT / packaging (TW dominates the back-end)
    "Electronic Components": [
        "3711.TW", "6239.TW", "2449.TW",              # ASE/Powertech/KingYuan
        "2329.TW",                                     # 華泰
    ],
    # ─── EMS / Electronic Manufacturing Services (TW core export)
    "Electronic Equipment & Instruments": [
        "2317.TW", "2382.TW", "4938.TW", "2324.TW",   # HonHai/Quanta/Pegatron/Compal
        "3231.TW",                                     # Wistron
    ],
    # yfinance sometimes tags EMS as 'Computer Hardware' for the same
    # group; cover both keys.
    "Computer Hardware": [
        "2382.TW", "4938.TW", "2324.TW", "2356.TW",   # Quanta/Pegatron/Compal/Inventec
        "3231.TW", "2317.TW",
    ],
    # ─── Thermal / cooling (AI server cycle)
    "Industrial Machinery": [
        "3017.TW", "3324.TWO", "2421.TW",              # AVC/Auras/Sunon
    ],
    # ─── PCB / 載板 (AI substrate cycle)
    "Electronic Components — PCB": [
        "3044.TW", "2358.TW", "3037.TW", "8046.TW",   # Tripod/Compeq/Unimicron/NanyaPCB
    ],
    # ─── Optical / camera modules
    "Specialty Industrial Machinery": [
        "3008.TW", "3406.TW",                          # Largan/GeniusOptical
    ],
    # ─── Display panels
    "Consumer Electronics": [
        "2330.TW", "2454.TW", "2317.TW", "3008.TW",   # TSMC/MTK/HonHai/Largan
        "2382.TW",
    ],
    # ─── Banks / financial holdings
    "Banks - Regional": [
        "2881.TW", "2882.TW", "2886.TW", "2891.TW",   # Fubon/Cathay/Mega/CTBC
        "2884.TW",                                     # ESun
    ],
    "Banks - Diversified": [
        "2881.TW", "2882.TW", "2886.TW", "2891.TW",
        "2884.TW",
    ],
    "Asset Management": [
        "2881.TW", "2882.TW", "2891.TW",
    ],
    "Insurance - Diversified": [
        "2881.TW", "2882.TW", "2885.TW",   # Fubon/Cathay/Yuanta (2888 신광금 소멸)
    ],
    # ─── Telecom
    "Telecom Services": [
        "2412.TW", "3045.TW", "4904.TW",              # Chunghwa/TWM/FET
    ],
    # ─── Shipping (containers + tankers)
    "Marine Shipping": [
        "2603.TW", "2609.TW", "2615.TW",              # Evergreen/YangMing/WanHai
    ],
    # ─── Airlines
    "Airlines": [
        "2610.TW", "2618.TW",                          # ChinaAir/EvaAir
    ],
    # ─── Chemicals / petrochemical (Formosa Plastics group)
    "Specialty Chemicals": [
        "1301.TW", "1303.TW", "1326.TW",              # FPC/NanyaPlas/FormosaChem
    ],
    "Chemicals": [
        "1301.TW", "1303.TW", "1326.TW",
    ],
    # ─── Steel
    "Steel": [
        "2002.TW", "2014.TW", "2015.TW",              # CSC/Chung Hung/Feng Hsin
    ],
    # ─── Autos / parts (TW has small but present industry)
    "Auto Manufacturers": [
        "2201.TW", "2207.TW",                          # Yulon/HoTai (Toyota distrib.)
    ],
    "Auto Parts": [
        "1503.TW", "2231.TW", "2227.TW",
    ],
    # ─── Food / beverage / retail
    "Packaged Foods": [
        "1216.TW", "1227.TW", "1701.TW",              # UniPres/JiaJia/Yili
    ],
    "Specialty Retail": [
        "2912.TW", "9921.TW",                          # President(7-11)/GiantBike
    ],
    # ─── Biotech / pharma (TW small biotech)
    "Drug Manufacturers - General": [
        "1707.TW", "1736.TW", "6446.TW",              # XinTaiNeng/JointBio/PharmaEss
    ],
    "Biotechnology": [
        "6446.TW", "4147.TWO", "4736.TW",
    ],
    # ─── REITs (TW REIT market thin)
    "REIT - Diversified": [
        "01001T.TW",                                   # Cathay REIT 國泰一號
    ],
}


# CN A-share industry peer sets — covers 13 RULE 13 industries with
# ChiNext + STAR + Main Board representatives. Phase 4-CN (2026-05-18).
# Industry strings mirror yfinance's standardized vocabulary; subject
# filtered out at resolve time. Mainland peers only — HK SOE / VIE
# peers live in `_HK_INDUSTRY_PEERS` so dual-listed names (BYD A 002594
# vs BYD H 1211) don't mix currency / market context in one Comps table.
_CN_A_INDUSTRY_PEERS = {
    # ─── 백주 (mainland-only, no HK counterpart)
    "Beverages - Wineries & Distilleries": [
        "600519.SS", "000858.SZ", "000568.SZ", "600809.SS",
        "000596.SZ",                                  # 古井贡酒
    ],
    # ─── 4대 国有 银行 (A-share lines; HK lines in _HK_INDUSTRY_PEERS)
    "Banks - Diversified": [
        "601398.SS", "601939.SS", "601988.SS", "601288.SS",
        "601328.SS",                                  # 交通银行 A-share
    ],
    "Banks - Regional": [
        "600036.SS", "601166.SS", "600000.SS", "600015.SS",
    ],
    # ─── Property (大型 부동산)
    "Real Estate - Development": [
        "600048.SS", "000002.SZ", "001979.SZ", "600340.SS",
    ],
    "Real Estate Services": [
        "600048.SS", "000002.SZ", "001979.SZ",
    ],
    # ─── Internet Content (mostly HK-listed VIE; the few A-share players)
    "Internet Content & Information": [
        "300059.SZ", "002841.SZ",                     # 东方财富, 视觉中国
    ],
    # ─── 半導體 (STAR + ChiNext + Main concentrated)
    "Semiconductors": [
        "688981.SS", "603501.SS", "002180.SZ", "300782.SZ",
        "603160.SS",                                  # 韦尔股份/紫光国微/卓胜微/汇顶
    ],
    "Semiconductor Equipment & Materials": [
        "688012.SS", "002129.SZ", "300598.SZ",        # 中微/TCL中环/诚迈
    ],
    # ─── EV (자동차 + 신에너지 차)
    "Auto Manufacturers": [
        "002594.SZ",                                  # BYD A
        "601127.SS",                                  # 賽力斯 (Huawei AITO partner)
        "600104.SS",                                  # SAIC 上汽集团
        "601633.SS",                                  # 長城汽車 A
        "000625.SZ",                                  # 長安汽車
    ],
    # ─── 锂电池 (Battery, ChiNext 主체)
    "Electrical Equipment & Parts": [
        "300750.SZ", "300014.SZ", "002460.SZ",
        "300207.SZ",                                  # 欣旺达
    ],
    # ─── Solar 光伏 (Main board + ChiNext mix)
    "Solar": [
        "601012.SS", "600438.SS", "300274.SZ", "002129.SZ",
    ],
    # yfinance also tags some solar names as 'Semiconductor Equipment'
    # — cross-covered above.
    # ─── 保险 (A주 ines)
    "Insurance - Life": [
        "601318.SS", "601628.SS", "601336.SS",        # 中国平安 A/中国人寿 A/新华保险 A
    ],
    "Insurance - Diversified": [
        "601318.SS", "601601.SS", "601336.SS",        # 平安/太保/新华
    ],
    # ─── 通信 (A주 메인)
    "Telecom Services": [
        "600050.SS", "600941.SS", "601728.SS",        # 中国联通 A / 中国移动 A / 中国电信 A
    ],
    # ─── Airlines (A주)
    "Airlines": [
        "601111.SS", "600115.SS", "600029.SS", "601021.SS",
        # 中国国航/东方航空/南方航空/春秋航空
    ],
    # ─── Petro + Steel
    "Oil & Gas Integrated": [
        "601857.SS", "600028.SS", "601808.SS",        # 中国石油/中石化/中海油服
    ],
    "Steel": [
        "600019.SS", "000898.SZ", "000932.SZ",        # 宝钢/鞍钢/华菱钢铁
    ],
    # ─── Consumer + Appliance
    "Packaged Foods": [
        "600887.SS", "600519.SS", "603288.SS",        # 伊利/茅台/海天味业
    ],
    "Furnishings, Fixtures & Appliances": [
        "000333.SZ", "000651.SZ", "600690.SS", "002508.SZ",
        # 美的/格力/海尔/老板电器
    ],
    "Consumer Electronics": [
        "002241.SZ",                                  # 歌尔股份
        "000333.SZ", "000651.SZ",
    ],
}


# HK industry peer sets — focused on the structurally-HK names (4大
# 国有 银行 H-shares, Internet VIE, property, 항공, 통신, 보험).
# Mainland A-share peers stay in `_CN_A_INDUSTRY_PEERS` to keep
# currency consistency in Comps tables (HK row in CNY ¥ would confuse
# the reader). Dual-listed names appear in both dicts.
_HK_INDUSTRY_PEERS = {
    # ─── Internet VIE structures (HK + ADR cross-listings — HK default)
    "Internet Content & Information": [
        "0700.HK", "9988.HK", "9618.HK", "3690.HK",
        "9888.HK", "9999.HK",                         # Tencent/BABA/JD/Meituan/Baidu/NetEase
    ],
    "Internet Retail": [
        "9988.HK", "9618.HK", "3690.HK",              # BABA/JD/Meituan
    ],
    # ─── 4대 국유 은행 (H-shares — 더 liquid than A-shares)
    "Banks - Diversified": [
        "1398.HK", "0939.HK", "3988.HK", "1288.HK",
        "3328.HK", "0005.HK",                         # ICBC/CCB/BOC/ABC/BOCOM/HSBC
    ],
    "Banks - Regional": [
        "0005.HK", "2888.HK", "2388.HK", "0023.HK",   # HSBC/StanChart/BOCHK/Bank of E Asia
    ],
    # ─── Insurance (HK 주력)
    "Insurance - Diversified": [
        "2318.HK", "1299.HK", "2628.HK", "1336.HK",
        "1339.HK",                                    # PingAn/AIA/China Life/NCI/PICC
    ],
    "Insurance - Life": [
        "1299.HK", "2628.HK", "2318.HK",
    ],
    # ─── Property (HK developers + China property)
    "Real Estate Services": [
        "1109.HK", "0688.HK", "2007.HK", "1813.HK",
        "3333.HK",                                    # CR Land/COLI/Country Garden/Kaisa/Evergrande
    ],
    "Real Estate - Diversified": [
        "1109.HK", "0688.HK", "0016.HK", "0012.HK",   # CR Land/COLI/Sun Hung Kai/Henderson
    ],
    # ─── 통신 / 유틸 (HK SOE)
    "Telecom Services": [
        "0941.HK", "0728.HK", "0762.HK", "0008.HK",   # ChinaMobile/Telecom/Unicom/PCCW
    ],
    "Utilities - Regulated Electric": [
        "0002.HK", "0006.HK", "0003.HK",              # CLP/Power Assets/HK Gas
    ],
    # ─── 항공
    "Airlines": [
        "0753.HK", "0293.HK", "0670.HK", "1055.HK",   # AirChina/Cathay/EastAir H/SouthAir H
    ],
    # ─── 석유 / 가스
    "Oil & Gas Integrated": [
        "0857.HK", "0386.HK", "0883.HK",              # CNPC/Sinopec/CNOOC
    ],
    # ─── EV / Auto Manufacturers (HK H-shares)
    "Auto Manufacturers": [
        "1211.HK", "9866.HK", "9868.HK", "2015.HK",
        "0175.HK",                                    # BYD H/NIO/XPeng/LiAuto/Geely
    ],
    # ─── 半導體 (HK only — SMIC H, Hua Hong)
    "Semiconductors": [
        "0981.HK", "1347.HK",                         # SMIC H / Hua Hong
    ],
    # ─── 가전 / 소비
    "Furnishings, Fixtures & Appliances": [
        "0997.HK",                                    # Haier Smart Home H (limited HK roster)
    ],
    "Restaurants": [
        "9869.HK", "2096.HK",                         # Haidilao etc.
    ],
}


# Industries below cover ~90% of US ticker traffic the bot sees (S&P
# 500 mega/large caps + active mid-cap names). Each row: 4-6 peers from
# the same yfinance 'industry' string. Subject ticker filtered out by
# resolve_peer_set. Add a new row when a US subject hits an industry
# we haven't covered.
# Mega-cap tech 비교군 (AAPL 2026-06-06 review) — Mag-7 급 종목은 좁은
# yfinance 산업 분류로 체급이 안 맞는 Comps 가 나온다(AAPL='Consumer
# Electronics' → GoPro($수억)·Sonos 와 비교 = 무의미). 이 종목들은 산업
# 무관하게 서로(대형 테크)와 비교하도록 라우팅. 정적 화이트리스트라 소형주
# (예: GoPro)는 영향 없이 기존 산업 리스트 유지(같은 'Consumer Electronics'
# 안에 $4.6T 와 $수억이 공존하는 문제를 cap-aware 로 분리).
_US_MEGACAP_TECH = {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA",
                    "META", "AVGO", "ORCL", "TSLA"}
_US_MEGACAP_PEERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META"]

_US_INDUSTRY_PEERS = {
    # ─── Technology
    "Software - Infrastructure": ["MSFT", "ORCL", "IBM", "NOW", "PANW"],
    "Software - Application": ["ADBE", "CRM", "INTU", "SNOW", "WDAY"],
    "Semiconductors": ["NVDA", "AMD", "AVGO", "QCOM", "TXN", "MU"],
    "Semiconductor Equipment & Materials": ["AMAT", "LRCX", "KLAC", "ASML", "TER"],
    "Information Technology Services": ["ACN", "CTSH", "IBM", "INFY"],
    "Communication Equipment": ["CSCO", "MSI", "ANET", "JNPR"],
    "Computer Hardware": ["DELL", "HPQ", "ANET", "STX", "WDC"],
    "Consumer Electronics": ["AAPL", "GPRO", "SONO"],
    "Internet Content & Information": ["GOOGL", "META", "NFLX", "SPOT", "PINS"],
    "Internet Retail": ["AMZN", "EBAY", "ETSY", "MELI"],
    # ─── Financials
    "Banks - Diversified": ["JPM", "BAC", "C", "WFC"],
    "Banks - Regional": ["USB", "PNC", "TFC", "MTB", "RF"],
    "Capital Markets": ["GS", "MS", "SCHW", "BLK", "BX"],
    "Asset Management": ["BLK", "BX", "KKR", "AMP", "TROW"],
    "Insurance - Diversified": ["AIG", "MET", "PRU", "AFL"],
    "Insurance - Property & Casualty": ["TRV", "CB", "PGR", "ALL", "HIG"],
    "Insurance - Life": ["MET", "PRU", "AFL"],
    "Credit Services": ["V", "MA", "AXP", "COF", "SYF", "DFS"],
    "Mortgage Finance": ["RKT", "UWMC", "PFSI"],
    # ─── Healthcare
    "Drug Manufacturers - General": ["PFE", "MRK", "JNJ", "LLY", "BMY", "ABBV"],
    "Drug Manufacturers - Specialty & Generic": ["TEVA", "VTRS", "AMGN"],
    "Biotechnology": ["VRTX", "GILD", "REGN", "BIIB", "MRNA"],
    "Medical Devices": ["MDT", "ABT", "BSX", "SYK", "EW"],
    "Diagnostics & Research": ["TMO", "DHR", "IDXX", "ILMN"],
    "Healthcare Plans": ["UNH", "CVS", "HUM", "CI", "ELV"],
    "Medical Instruments & Supplies": ["ABT", "BAX", "BDX", "ZBH"],
    # ─── Energy
    "Oil & Gas Integrated": ["XOM", "CVX", "COP", "SHEL", "BP"],
    "Oil & Gas E&P": ["EOG", "FANG", "OXY", "DVN", "PXD"],
    "Oil & Gas Midstream": ["KMI", "WMB", "ENB", "EPD", "MPLX"],
    "Oil & Gas Refining & Marketing": ["VLO", "PSX", "MPC", "DK"],
    "Oil & Gas Equipment & Services": ["SLB", "HAL", "BKR"],
    # ─── Real Estate (REITs)
    "REIT - Specialty": ["AMT", "CCI", "EQIX", "DLR"],
    "REIT - Residential": ["AVB", "ESS", "EQR", "MAA"],
    "REIT - Retail": ["SPG", "REG", "KIM", "MAC"],
    "REIT - Industrial": ["PLD", "EGP", "REXR", "FR"],
    "REIT - Office": ["BXP", "KRC", "VNO", "SLG"],
    "REIT - Healthcare Facilities": ["WELL", "VTR", "OHI"],
    # ─── Utilities
    "Utilities - Regulated Electric": ["NEE", "DUK", "SO", "AEP", "EXC"],
    "Utilities - Regulated Gas": ["ATO", "NJR", "NWE"],
    "Utilities - Renewable": ["NEE", "BEPC", "BEP"],
    # ─── Consumer Discretionary
    "Auto Manufacturers": ["TSLA", "F", "GM", "RIVN", "LCID"],
    "Auto Parts": ["APTV", "BWA", "LEA", "GNTX"],
    "Specialty Retail": ["TJX", "ROST", "ULTA", "BBY"],
    "Apparel Retail": ["NKE", "LULU", "RL"],
    "Restaurants": ["MCD", "SBUX", "CMG", "YUM"],
    "Lodging": ["MAR", "HLT", "H", "IHG"],
    "Resorts & Casinos": ["LVS", "WYNN", "MGM", "CZR"],
    "Travel Services": ["BKNG", "EXPE", "ABNB", "TRIP"],
    "Home Improvement Retail": ["HD", "LOW"],
    "Discount Stores": ["WMT", "COST", "TGT", "BJ"],
    # ─── Consumer Staples
    "Beverages - Non-Alcoholic": ["KO", "PEP", "MNST", "CELH"],
    "Beverages - Wineries & Distilleries": ["STZ", "DEO"],
    "Beverages - Brewers": ["BUD", "TAP"],
    "Packaged Foods": ["GIS", "CPB", "SJM", "KHC", "MDLZ"],
    "Confectioners": ["HSY", "MDLZ"],
    "Household & Personal Products": ["PG", "CL", "KMB", "EL"],
    "Tobacco": ["MO", "PM", "BTI"],
    # ─── Industrials
    "Aerospace & Defense": ["BA", "LMT", "RTX", "GD", "NOC", "LHX"],
    "Industrial Distribution": ["GWW", "FAST", "WSO"],
    "Building Products & Equipment": ["MAS", "OC", "IBP"],
    "Specialty Industrial Machinery": ["HON", "EMR", "ITW", "ROK", "ETN"],
    "Farm & Heavy Construction Machinery": ["CAT", "DE", "AGCO"],
    "Conglomerates": ["GE", "HON", "MMM", "ITW"],
    "Railroads": ["UNP", "CSX", "NSC"],
    "Trucking": ["ODFL", "JBHT", "KNX", "XPO"],
    "Airlines": ["DAL", "UAL", "AAL", "LUV"],
    "Integrated Freight & Logistics": ["UPS", "FDX", "CHRW"],
    "Specialty Business Services": ["ADP", "PAYX", "RHI", "WTW"],
    # ─── Materials
    "Specialty Chemicals": ["PPG", "LIN", "APD", "ECL"],
    "Chemicals": ["DOW", "DD", "LYB", "CE", "EMN"],
    "Agricultural Inputs": ["NTR", "MOS", "CF"],
    "Containers & Packaging": ["IP", "PKG", "AMCR"],
    "Steel": ["NUE", "STLD", "X"],
    "Copper": ["FCX", "SCCO"],
    "Gold": ["NEM", "GOLD"],
    # ─── Communication Services
    "Telecom Services": ["VZ", "T", "TMUS"],
    "Entertainment": ["DIS", "NFLX", "PARA", "WBD"],
    "Broadcasting": ["FOX", "FOXA", "NWSA"],
}


def get_market_config(ticker: str) -> MarketConfig:
    """Convenience wrapper: detect market and return its config dict."""
    return MARKET_CONFIG[detect_market(ticker)]


# ── KR .KS↔.KQ suffix 정규화 (티로보틱스 117730 2026-06-04) ──────────────
# yfinance / KIS(시장구분 J/Q) / 뉴스 쿼리는 .KS(KOSPI)와 .KQ(KOSDAQ)를
# 구분한다. KOSDAQ 종목을 .KS 로 잘못 조회하면 시세·시총·뉴스가 엉뚱한
# 장부에서 와 데이터 불일치 + 뉴스 0건 + 기술분석 freeze 오발을 부른다.
# 권위 있는 KRX 종목 목록(pykrx)으로 6자리 코드의 진짜 시장을 조회해 suffix
# 를 자동 교정. 목록은 드물게 변하므로 7일 디스크 캐시. pykrx/creds 부재 시
# graceful (원본 그대로 반환 — 절대 파이프라인 중단 안 함).
_KR_CODES_CACHE_TTL = 7 * 24 * 3600  # 7d — 상장/폐지는 느리게 변함


def _correct_kr_suffix(base6: str, current_suffix: str,
                       kospi: set, kosdaq: set) -> str:
    """순수 코어: 6자리 코드 + 현재 suffix + KOSPI/KOSDAQ 코드 집합 →
    권위 있는 suffix 가 붙은 ticker. 모호/미상이면 현재 suffix 유지."""
    if base6 in kosdaq and base6 not in kospi:
        return base6 + ".KQ"
    if base6 in kospi and base6 not in kosdaq:
        return base6 + ".KS"
    return base6 + current_suffix


def _kr_market_code_sets():
    """(kospi_set, kosdaq_set) of 6-digit codes — pykrx + 7d 디스크 캐시.
    실패(creds/pykrx 부재) 시 (빈, 빈) → 호출부 no-op."""
    import json
    import os
    import time

    cache = os.path.expanduser("~/.tradingagents/kr_market_codes.json")
    try:
        if (os.path.exists(cache)
                and time.time() - os.path.getmtime(cache) < _KR_CODES_CACHE_TTL):
            d = json.loads(open(cache, encoding="utf-8").read())
            return set(d.get("KOSPI", [])), set(d.get("KOSDAQ", []))
    except Exception:
        pass
    try:
        from pykrx import stock as _krx
        kospi = list(_krx.get_market_ticker_list(market="KOSPI"))
        kosdaq = list(_krx.get_market_ticker_list(market="KOSDAQ"))
        if kospi or kosdaq:
            try:
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                with open(cache, "w", encoding="utf-8") as fh:
                    json.dump({"KOSPI": kospi, "KOSDAQ": kosdaq}, fh)
            except Exception:
                pass
        return set(kospi), set(kosdaq)
    except Exception:
        return set(), set()


def normalize_kr_ticker_suffix(ticker: str) -> str:
    """Correct a wrong .KS↔.KQ suffix via the authoritative KRX market
    lists (e.g. KOSDAQ 117730 mis-typed as 117730.KS → 117730.KQ). Non-KR
    tickers, malformed codes, or any lookup failure return the input
    unchanged (graceful — never raises, never blocks the pipeline).
    Rule applies to all KR analyses going forward."""
    t = (ticker or "").strip().upper()
    if not (t.endswith(".KS") or t.endswith(".KQ")):
        return ticker
    base, suffix = t[:-3], t[-3:]
    if not (len(base) == 6 and base.isdigit()):
        return ticker
    try:
        kospi, kosdaq = _kr_market_code_sets()
        if not kospi and not kosdaq:
            return ticker  # lists unavailable → keep as-is
        return _correct_kr_suffix(base, suffix, kospi, kosdaq)
    except Exception:
        return ticker
