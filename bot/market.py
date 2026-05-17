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
    "CN": {
        "name": "중국/홍콩",
        "broad_benchmark": "510300.SS",  # CSI 300 ETF (Shanghai)
        "broad_label": "CSI 300 (510300)",
        "currency": "CNY",
        "currency_symbol": "¥",
        "trading_hours": "09:30-15:00 CST",
    },
}


def detect_market(ticker: str) -> str:
    """Return one of 'US', 'KR', 'JP', 'CN'. Defaults to 'US' for
    suffix-less tickers — that's what the help text instructs users to
    type for American listings."""
    t = (ticker or "").upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        return "KR"
    if t.endswith(".T"):
        return "JP"
    if t.endswith(".SS") or t.endswith(".SZ") or t.endswith(".HK"):
        return "CN"
    return "US"


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
}


def resolve_english_alias(token: str) -> str | None:
    """Map a romanized company name (NAVER / TOYOTA / etc.) to the
    yfinance-format ticker, or None when the alias is ambiguous /
    unknown. Returns the canonical ticker for direct routing into the
    analyzer; the caller still applies its own ticker validation.

    Searches both _KR_ENGLISH_ALIAS and _JP_ENGLISH_ALIAS. Each market
    has its own dict so a clash like 'SUMITOMO' (could be 三井住友 vs
    住友商事) can be resolved with explicit market-specific keys."""
    if not token:
        return None
    upper = token.upper()
    return _KR_ENGLISH_ALIAS.get(upper) or _JP_ENGLISH_ALIAS.get(upper)


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
        "000660.KS", "005930.KS", "TSM", "INTC", "AMD", "NVDA",
    ],
    "Semiconductor Equipment & Materials": [
        "240810.KS", "042700.KS", "036930.KQ", "095610.KQ",
        "319660.KS", "AMAT", "LRCX", "KLAC",
    ],
    "Specialty Chemicals": [
        "005290.KS", "036830.KS", "093370.KS", "102710.KS",
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
        "035420.KS", "035720.KS", "GOOGL", "META", "9988.HK",
    ],
    "Auto Manufacturers": [
        "005380.KS", "000270.KS", "TM", "F", "GM",
    ],
    "Auto Parts": [
        "012330.KS", "204320.KS", "018880.KS", "DNZOY",
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
    "Biotechnology": [
        "207940.KS", "068270.KS", "326030.KS", "REGN", "VRTX",
    ],
    "Steel": [
        "005490.KS", "004020.KS", "002380.KS",
    ],
    "Shipbuilding": [
        "010140.KS", "009540.KS", "042660.KS",
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
}


def resolve_peer_set(ticker: str, industry: str | None) -> list[str] | None:
    """Return a hand-curated peer ticker list for the subject's industry,
    or None when we don't have one for that industry. The subject ticker
    is filtered out so an analysis never compares a company to itself.
    Capped at 5 peers for prompt brevity.

    Market-aware: picks JP-listed peers for .T tickers, KR-listed for
    .KS/.KQ, US-listed for bare alpha tickers. Industry strings use
    yfinance's standardized English vocabulary ('Auto Manufacturers',
    'Banks - Diversified', etc.) so the same industry key works
    across markets — the dict choice is what makes it market-specific."""
    if not industry:
        return None
    market = detect_market(ticker)
    if market == "JP":
        base = _JP_INDUSTRY_PEERS.get(industry)
    else:
        # KR + US fall through to the existing KR-leaning dict which
        # also contains US ADRs for many industries. Phase 3 will add
        # a CN dict for .HK / .SS tickers.
        base = _KR_INDUSTRY_PEERS.get(industry)
    if not base:
        return None
    subject = (ticker or "").upper()
    peers = [t for t in base if t.upper() != subject]
    return peers[:5] or None


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
        "9613.T", "4307.T", "4684.T",
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


def get_market_config(ticker: str) -> MarketConfig:
    """Convenience wrapper: detect market and return its config dict."""
    return MARKET_CONFIG[detect_market(ticker)]
