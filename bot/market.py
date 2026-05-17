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
    """Map a romanized KR company name (NAVER / KAKAO / etc.) to the
    yfinance-format KR ticker, or None when the alias is ambiguous /
    unknown. Returns the canonical ticker for direct routing into the
    analyzer; the caller still applies its own ticker validation."""
    if not token:
        return None
    return _KR_ENGLISH_ALIAS.get(token.upper())


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
    Capped at 5 peers for prompt brevity."""
    if not industry:
        return None
    base = _KR_INDUSTRY_PEERS.get(industry)
    if not base:
        return None
    subject = (ticker or "").upper()
    peers = [t for t in base if t.upper() != subject]
    return peers[:5] or None


def get_market_config(ticker: str) -> MarketConfig:
    """Convenience wrapper: detect market and return its config dict."""
    return MARKET_CONFIG[detect_market(ticker)]
