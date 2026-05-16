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


def get_market_config(ticker: str) -> MarketConfig:
    """Convenience wrapper: detect market and return its config dict."""
    return MARKET_CONFIG[detect_market(ticker)]
