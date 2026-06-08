"""Alpha Vantage News Sentiment client — pre-scored sentiment for any ticker.

Unique value: every article comes with a quantified sentiment score
(bullish/bearish/neutral), enabling structured sentiment analysis
instead of LLM tone-inference from headlines.

API: https://www.alphavantage.co/query?function=NEWS_SENTIMENT
Auth: ALPHA_VANTAGE_API_KEY required (free registration, 25 req/day).

12h disk cache per (ticker, date). Universal (all markets).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.av_sentiment")

_API = "https://www.alphavantage.co/query"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "av_sentiment"
_CACHE_TTL_HOURS = 12
_TIMEOUT = 15


def _api_key() -> Optional[str]:
    return os.environ.get("ALPHA_VANTAGE_API_KEY")


def av_key_ready() -> bool:
    return bool(_api_key())


def _cache_get(key: str) -> Optional[dict]:
    f = _CACHE_DIR / key
    if not f.exists():
        return None
    try:
        age_h = (time.time() - f.stat().st_mtime) / 3600
        if age_h >= _CACHE_TTL_HOURS:
            return None
        return json.loads(f.read_text())
    except Exception:
        return None


def _cache_set(key: str, data):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / key).write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def _av_ticker(ticker: str) -> str:
    """Convert our ticker format to Alpha Vantage format.
    AV uses bare symbols for US, and exchange-prefixed for international."""
    parts = ticker.split(".")
    if len(parts) == 1:
        return ticker
    code, suffix = parts[0], parts[-1].upper()
    suffix_map = {
        "TW": f"TPE:{code}", "TWO": f"TPE:{code}",
        "KS": f"KRX:{code}", "KQ": f"KRX:{code}",
        "T": f"TYO:{code}",
        "HK": f"HKG:{code.zfill(4)}",
        "SS": f"SHA:{code}", "SZ": f"SHE:{code}",
        "L": f"LON:{code}",
        "AS": f"AMS:{code}", "PA": f"EPA:{code}",
        "DE": f"ETR:{code}",
    }
    return suffix_map.get(suffix, ticker)


def fetch_news_sentiment(ticker: str, limit: int = 50) -> Optional[dict]:
    """Fetch news with pre-computed sentiment scores.

    Returns:
        {
            "articles": [
                {"title": str, "source": str, "time": str,
                 "sentiment_score": float, "sentiment_label": str,
                 "ticker_sentiment_score": float, "ticker_relevance": float},
                ...
            ],
            "aggregate": {
                "bullish": int, "bearish": int, "neutral": int,
                "avg_score": float, "total": int,
            },
        }
    """
    key = _api_key()
    if not key:
        return None

    sym = _av_ticker(ticker)
    ck = f"sent_{sym.replace(':', '_')}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    try:
        resp = requests.get(
            _API,
            params={
                "function": "NEWS_SENTIMENT",
                "tickers": sym,
                "limit": limit,
                "apikey": key,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            log.warning("av_sentiment: returned %d", resp.status_code)
            return None
        data = resp.json()
    except Exception as exc:
        log.warning("av_sentiment: fetch failed: %s", exc)
        return None

    if "Error Message" in data or "Note" in data:
        log.warning("av_sentiment: API error/limit: %s", data.get("Error Message") or data.get("Note", "")[:100])
        return None

    feed = data.get("feed") or []
    if not feed:
        return None

    articles: list[dict] = []
    agg = {"bullish": 0, "bearish": 0, "neutral": 0, "total": 0, "score_sum": 0.0}

    for item in feed[:limit]:
        title = item.get("title", "")
        source = item.get("source", "")
        pub_time = item.get("time_published", "")
        overall_score = float(item.get("overall_sentiment_score", 0))
        overall_label = item.get("overall_sentiment_label", "Neutral")

        ticker_score = 0.0
        ticker_relevance = 0.0
        for ts in item.get("ticker_sentiment", []):
            if ts.get("ticker", "").upper() == sym.upper():
                ticker_score = float(ts.get("ticker_sentiment_score", 0))
                ticker_relevance = float(ts.get("relevance_score", 0))
                break

        use_score = ticker_score if ticker_relevance > 0.1 else overall_score
        if use_score > 0.15:
            agg["bullish"] += 1
        elif use_score < -0.15:
            agg["bearish"] += 1
        else:
            agg["neutral"] += 1
        agg["total"] += 1
        agg["score_sum"] += use_score

        articles.append({
            "title": title,
            "source": source,
            "time": pub_time[:13] if pub_time else "",
            "sentiment_score": round(use_score, 3),
            "sentiment_label": overall_label,
            "ticker_relevance": round(ticker_relevance, 2),
        })

    avg_score = agg["score_sum"] / agg["total"] if agg["total"] > 0 else 0
    result = {
        "articles": articles,
        "aggregate": {
            "bullish": agg["bullish"],
            "bearish": agg["bearish"],
            "neutral": agg["neutral"],
            "avg_score": round(avg_score, 3),
            "total": agg["total"],
        },
    }
    _cache_set(ck, result)
    return result


def format_sentiment_block(data: Optional[dict]) -> str:
    """Format news sentiment into a compact text block."""
    if not data:
        return ""
    agg = data.get("aggregate") or {}
    total = agg.get("total", 0)
    if total == 0:
        return ""

    bullish = agg.get("bullish", 0)
    bearish = agg.get("bearish", 0)
    neutral = agg.get("neutral", 0)
    avg = agg.get("avg_score", 0)

    tone = "Bullish" if avg > 0.15 else ("Bearish" if avg < -0.15 else "Mixed/Neutral")
    bull_pct = bullish / total * 100
    bear_pct = bearish / total * 100

    lines = [
        f"• News Sentiment (Alpha Vantage, {total}건):"
        f" {tone} (avg {avg:+.3f})",
        f"  Bullish {bullish} ({bull_pct:.0f}%) /"
        f" Bearish {bearish} ({bear_pct:.0f}%) /"
        f" Neutral {neutral}",
    ]

    articles = data.get("articles") or []
    top_bull = [a for a in articles if a.get("sentiment_score", 0) > 0.25]
    top_bear = [a for a in articles if a.get("sentiment_score", 0) < -0.25]

    if top_bull:
        best = max(top_bull, key=lambda a: a["sentiment_score"])
        lines.append(
            f"  Top Bullish: \"{best['title'][:60]}...\" ({best['source']},"
            f" score {best['sentiment_score']:+.3f})"
        )
    if top_bear:
        worst = min(top_bear, key=lambda a: a["sentiment_score"])
        lines.append(
            f"  Top Bearish: \"{worst['title'][:60]}...\" ({worst['source']},"
            f" score {worst['sentiment_score']:+.3f})"
        )

    return "\n".join(lines)
