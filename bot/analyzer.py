"""TradingAgents wrapper used by the Telegram bot.

Keeps the heavy initialization out of the bot file and exposes a single
synchronous `analyze(ticker, date)` call that returns (summary, full_report).
"""

import logging
import re
import sys
import time
from datetime import date as _date
from pathlib import Path

# Make the vendored TradingAgents package importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "TradingAgents"))

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from bot import cache as _cache
from bot.archive import save_analysis as _archive_save
from bot.dashboard import regenerate_index as _dashboard_regen
from bot.usage_tracker import UsageCallback, log_analysis

log = logging.getLogger("stock-bot.analyzer")

_SELECTED_ANALYSTS = ["market", "social", "news", "fundamentals"]


def _build_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "google"
    # Hybrid model strategy:
    # - deep_think_llm (flash) handles tool-using analysts and final managers
    # - quick_think_llm (flash-lite) handles the debate/risk nodes that don't
    #   need tools, trading ~25% of total cost for slightly less polish on
    #   intermediate text. graph/setup.py routes analysts to deep_think_llm.
    config["deep_think_llm"] = "gemini-2.5-flash"
    config["quick_think_llm"] = "gemini-2.5-flash-lite"
    # Final-decision tier (research_manager / trader / portfolio_manager).
    # Pro lifts the quality of the actual BUY/HOLD/SELL synthesis without
    # paying Pro prices for the analyst+debate phases — those stay on
    # Flash/Flash-Lite. Adds roughly $0.03-0.05/analysis on top of the
    # ~$0.05 baseline.
    config["decision_think_llm"] = "gemini-2.5-pro"
    config["google_thinking_level"] = "minimal"
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["output_language"] = "Korean"
    # Output caps to control cost. Analyst / manager reports (deep tier)
    # have room for full structured Markdown; debate/risk/trader nodes
    # (quick tier) only need a few paragraphs of stance text. Saves
    # roughly 30-40% on output-token spend with no measurable loss in
    # report content.
    config["deep_max_output_tokens"] = 16384
    config["quick_max_output_tokens"] = 2000
    # Decision tier shares the long-output budget with deep — the trader
    # / managers write multi-paragraph rationales that benefit from room.
    config["decision_max_output_tokens"] = 16384
    # Per-Gemini-call HTTP timeout. Without this, a single hung response
    # can chew the entire 10-minute analysis budget; SIMO hit exactly that
    # case (worker silent for 7+ min before the asyncio-side timeout fired).
    # 150s is generous for a long Korean-language analyst response with
    # tools, still leaves headroom for ~4 calls within the 600s wall budget.
    config["llm_request_timeout"] = 150
    config["data_vendors"] = {
        "core_stock_apis": "yfinance",
        "technical_indicators": "yfinance",
        "fundamental_data": "yfinance",
        "news_data": "yfinance",
    }
    # NOTE: UsageCallback is wired in directly at TradingAgentsGraph
    # construction time (see analyze()) rather than via config — the
    # graph's __init__ takes a separate `callbacks=` kwarg, anything
    # in the config dict under that key is ignored.
    return config


_BUSY_MARKER = Path.home() / ".tradingagents" / ".busy"


def mark_busy() -> None:
    """Create the .busy marker so auto-update / watchdog defer restarts.

    Called from the asyncio handler BEFORE the worker thread starts, to
    close the race where a deploy timer fires after recovery state is
    written but before analyze() has had a chance to touch the marker
    itself.
    """
    _BUSY_MARKER.parent.mkdir(parents=True, exist_ok=True)
    _BUSY_MARKER.touch()


def clear_busy() -> None:
    try:
        _BUSY_MARKER.unlink()
    except FileNotFoundError:
        pass


_ANALYST_REPORT_KEYS = {
    "market":       ("📈 시장",     "market_report"),
    "social":       ("💬 감정",     "sentiment_report"),
    "news":         ("📰 뉴스",     "news_report"),
    "fundamentals": ("💰 펀더멘털", "fundamentals_report"),
}


class AnalysisIncompleteError(RuntimeError):
    """Raised when ≥1 analyst's final report is still unusable after the
    in-graph retry. The Telegram handler converts this into a Korean error
    message that prompts the user to retry, rather than serving a hollow
    BUY/HOLD/SELL synthesis."""


class InvalidTickerError(RuntimeError):
    """Raised when yfinance returns no usable price data for the
    requested ticker — typically because the user typed a company name
    instead of a ticker (NAVER → should be 035420.KS), a typo, or a
    delisted symbol. NAVER on 2026-05-17 was the canonical case: the
    bot ran the full ~3 minute pipeline with empty data and the PM
    output 'Sell' on a 'no data = risk = sell' line of reasoning. The
    pre-flight aborts before any LLM call so the user gets a clear
    'ticker not found' message and we don't waste tokens producing a
    misleading recommendation."""


def _check_reports_or_raise(state, selected: list[str]) -> None:
    from tradingagents.agents.utils.agent_utils import looks_failed_report

    failed = []
    for analyst in selected:
        meta = _ANALYST_REPORT_KEYS.get(analyst)
        if not meta:
            continue
        label, key = meta
        if looks_failed_report((state.get(key) or "")):
            failed.append(label)
    if not failed:
        return
    log.warning(
        "analyze: %d/%d analyst reports still failed after retry: %s",
        len(failed), len(selected), failed,
    )
    raise AnalysisIncompleteError(
        "분석가 응답 누락: " + ", ".join(failed)
        + " — 잠시 후 재시도해주세요"
    )


def analyze(ticker: str, target_date: str | None = None) -> tuple[str, str]:
    """Run TradingAgents on a single ticker.

    Returns (summary, full_report) — both Markdown strings. Same
    (ticker, target_date) pair is served from disk cache to avoid paying
    for Gemini twice on the same trading day.

    A `.busy` marker file is created for the duration of an actual
    TradingAgents run so that the auto-update timer can defer a restart
    and not lose an in-flight analysis.
    """
    target_date = target_date or _date.today().isoformat()
    ticker = ticker.upper()
    started_at = time.time()

    cached = _cache.get(ticker, target_date)
    if cached is not None:
        log.info("cache hit for %s/%s", ticker, target_date)
        log_analysis(ticker, time.time() - started_at, cache_hit=True)
        return cached

    # Pre-flight: ticker must exist in yfinance. NAVER on 2026-05-17
    # ran the full pipeline against an empty .info dict and produced a
    # 'Sell' decision on 'no data = risk' reasoning — actively
    # dangerous. Abort here before spending tokens. The DM / channel
    # router catches InvalidTickerError and translates it into a
    # human-readable message pointing at the right input form (KR
    # 6-digit + suffix, US bare ticker, or Korean name).
    try:
        from tradingagents.agents.utils.agent_utils import _instrument_info
        info = _instrument_info(ticker)
        has_price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not has_price:
            raise InvalidTickerError(
                f"yfinance에서 '{ticker}' 가격 데이터를 찾을 수 없습니다."
            )
    except InvalidTickerError:
        raise
    except Exception as exc:
        # yfinance .info itself blew up (network blip etc.) — let the
        # main pipeline try anyway. If it really is bad data the
        # AnalysisIncompleteError path will catch it downstream.
        log.warning(
            "analyze: pre-flight yfinance check for %s failed: %s — proceeding",
            ticker, exc,
        )

    # NOTE: .busy marker lifecycle is now owned by the main bot's handler
    # (refcount-based) so that a second queued request doesn't lose the
    # marker mid-flight when the first request's subprocess finishes.
    # Calling mark/clear_busy from inside analyze() would race with that.
    log.info("analyze: building TradingAgentsGraph for %s", ticker)
    # ETFs and leveraged funds have no company-specific social chatter or
    # news flow — the social analyst returns empty placeholders for these
    # (KORU on 2026-05-08 was the canonical case). Drop social entirely
    # for funds; the news/fundamentals analysts get a fund-specific
    # prompt via build_instrument_context.
    selected = list(_SELECTED_ANALYSTS)
    pre_flight_notes: list[str] = []
    try:
        from tradingagents.agents.utils.agent_utils import is_etf
        if is_etf(ticker):
            selected = [a for a in selected if a != "social"]
            log.info("analyze: %s detected as fund — skipping social analyst", ticker)
    except Exception as exc:
        log.warning("analyze: ETF detection failed for %s: %s", ticker, exc)

    # News pre-flight: newly-IPO'd / low-coverage tickers (IBTA on
    # 2026-05-10 was the canonical case) have zero yfinance news, the
    # analyst's fail-fast retry then aborts the whole pipeline after
    # paying ~$0.05. Skip the news analyst entirely up front and warn
    # the user instead.
    #
    # Social/sentiment is dropped together because it reads from the
    # same yfinance news / social feed — 319660.KS 피에스케이 on
    # 2026-05-17 had 'news 자동 생략' announced but the sentiment
    # analyst ran anyway and failed with the generic '모델 응답
    # 오류' placeholder because it had no data either. They share
    # one upstream so they share the pre-flight skip.
    if "news" in selected:
        try:
            from tradingagents.agents.utils.agent_utils import has_recent_news
            if not has_recent_news(ticker):
                selected = [a for a in selected if a not in ("news", "social")]
                # Market-specific skip message so the user can see which
                # fallback sources were actually tried. Default text
                # "yfinance에 기사 0건" mis-implies for KR/JP that the
                # KR / JP fallback path wasn't even attempted, when in
                # fact has_recent_news already tried Naver (KR) /
                # Kabutan (JP) and got 0 too. 코미코 2026-05-17 case
                # surfaced this: user reasonably assumed Naver fallback
                # wasn't wired when it actually had run and returned
                # empty.
                try:
                    from bot.market import detect_market
                    _market = detect_market(ticker)
                except Exception:
                    _market = "US"
                if _market == "KR":
                    _skip_msg = (
                        f"📰 뉴스·💬 감정 분석 자동 생략: yfinance + Naver(한국어)"
                        f" 양쪽 모두 최근 28일간 {ticker} 기사 0건"
                        " (저커버리지 종목 — 시장·펀더멘털만으로 분석 진행)"
                    )
                elif _market == "JP":
                    _skip_msg = (
                        f"📰 뉴스·💬 감정 분석 자동 생략: yfinance + Kabutan(일본어)"
                        f" 양쪽 모두 최근 28일간 {ticker} 기사 0건"
                        " (저커버리지 종목 — 시장·펀더멘털만으로 분석 진행)"
                    )
                elif _market == "TW":
                    _skip_msg = (
                        f"📰 뉴스·💬 감정 분석 자동 생략: yfinance + 鉅亨網(繁體中文)"
                        f" 양쪽 모두 최근 28일간 {ticker} 기사 0건"
                        " (저커버리지 종목 — 시장·펀더멘털만으로 분석 진행)"
                    )
                else:
                    _skip_msg = (
                        f"📰 뉴스·💬 감정 분석 자동 생략: yfinance에 {ticker} 기사 0건"
                        " (신생주·저커버리지 종목 추정 — 시장·펀더멘털만으로 분석 진행)"
                    )
                pre_flight_notes.append(_skip_msg)
                log.info(
                    "analyze: %s has 0 news items (market=%s) — skipping news + social",
                    ticker, _market,
                )
        except Exception as exc:
            log.warning("analyze: news pre-check failed for %s: %s", ticker, exc)

    # Earnings calendar warning. A 5-trading-day recommendation that
    # lands inside the earnings reaction window has its outcome
    # dominated by the earnings move, not by the bot's analysis.
    # Window is ±10 calendar days so we catch both pre-earnings runup
    # and the post-earnings drift that typically plays out for 1-2
    # weeks after the print.
    try:
        from tradingagents.agents.utils.agent_utils import days_until_earnings
        delta = days_until_earnings(ticker, target_date)
        if delta is not None and -10 <= delta <= 10:
            if delta > 5:
                pre_flight_notes.append(
                    f"📅 실적 발표 {delta}일 후 — 발표 임박. 추천 평가가 어닝 반응에 좌우될 가능성"
                )
            elif 0 < delta <= 5:
                pre_flight_notes.append(
                    f"⚠️ 실적 발표 {delta}일 후 — 발표 전후 변동성 ↑."
                    " 5거래일 추천 평가가 어닝 반응에 좌우될 수 있음"
                )
            elif delta == 0:
                pre_flight_notes.append(
                    "⚠️ 실적 발표 당일 — 발표 결과에 따라 큰 변동 가능"
                )
            elif -5 <= delta < 0:
                pre_flight_notes.append(
                    f"📊 실적 발표 {-delta}일 전 발표됨 — post-earnings drift 영향권"
                )
            else:  # -10 <= delta < -5
                pre_flight_notes.append(
                    f"📊 실적 발표 {-delta}일 전 발표됨 — drift 잔영 가능"
                )
    except Exception as exc:
        log.warning("analyze: earnings check failed for %s: %s", ticker, exc)

    ta = TradingAgentsGraph(
        debug=False,
        config=_build_config(),
        selected_analysts=selected,
        # Hooks every Gemini call into ~/.tradingagents/usage.jsonl so
        # /usage and the dashboard cost card stay accurate.
        callbacks=[UsageCallback()],
    )
    log.info("analyze: graph built — invoking propagate")
    state, decision = ta.propagate(ticker, target_date)
    log.info("analyze: propagate done — formatting output")

    # Fail-fast on still-broken analyst reports. The graph already retried
    # each analyst once internally (see ConditionalLogic.should_retry_*);
    # if any final report still looks unusable, the downstream debate /
    # decision LLMs will hallucinate around the gap and produce an
    # authoritative-looking BUY/SELL on a hollow foundation. Better to
    # surface a clear error so the user retries than to deliver a confident
    # decision built on missing inputs.
    _check_reports_or_raise(state, selected)

    # Surface the last few resolved recommendations for this ticker as a
    # short Korean header line — gives the reader an immediate sense of
    # whether the bot has been right or wrong on this name lately.
    past_outcomes = _format_past_outcomes(ta.memory_log, ticker)
    log.info("analyze: past_outcomes done — building full report")

    full = _format_full(state, decision, ticker, target_date, past_outcomes, selected)
    log.info("analyze: full report done (%d chars) — building summary", len(full))

    summary = _format_summary(
        state, decision, ticker, target_date, past_outcomes,
        notes=pre_flight_notes,
    )
    log.info("analyze: summary done (%d chars) — writing cache", len(summary))

    _cache.put(ticker, target_date, summary, full)
    log.info("analyze: cache write done — returning to worker")
    elapsed = time.time() - started_at
    log_analysis(ticker, elapsed, cache_hit=False)
    # Persist to the long-term archive. Cache writes expire at midnight;
    # the archive does not, and is what the dashboard reads from.
    _archive_save(ticker, target_date, summary, full, elapsed)
    # Refresh the static HTML dashboard. Internally swallows errors, so
    # a dashboard hiccup can't break the analysis path.
    _dashboard_regen()

    # Phase B-2 (Step 2A, 2026-05-19): push the completed analysis to
    # Standard View so it appears in the next 08:00 KST daily brief.
    # Best-effort, silent fail — runs AFTER cache/archive/dashboard so
    # even a total Standard View outage can't break the user's result.
    # Same-host loopback POST; no external network dependency.
    try:
        from bot.standardview_push import push_analysis
        from bot.market import detect_market as _detect_market_for_push
        _market = _detect_market_for_push(ticker) or "?"
        _verdict = _extract_rating(decision) or "N/A"
        # Reuse the same stance bar text already in the summary —
        # consumers (Standard View HTML / Telegram) want the exact
        # one-line per-analyst view, not a separate rebuild.
        _stance_bar = ""
        for ln in summary.splitlines():
            if "📈" in ln or "💬" in ln or "💰" in ln:
                if "·" in ln:  # stance bar has at least 2 chunks joined by ' · '
                    _stance_bar = ln.strip()
                    break
        # Snippet priority (richest narrative first — fallback chain):
        #   1) PM rationale via _extract_decision_rationale(state) —
        #      pulls last 2 sentences from investment_plan '근거:' block,
        #      hard-capped at 220 chars; this is the verdict + WHY.
        #   2) Trader plan first sentence — '거래 액션 + 근거' narrative.
        #   3) First meaningful sentence of the decision text itself.
        #   4) Last resort: verdict label only ("Hold" / "Buy" etc.) so
        #      the snippet is at least non-empty.
        # Surfaced by: 140860.KS Park Systems 2026-05-20 push, where
        # decision text was literally just "Hold" and the snippet
        # showed "Hold" with no narrative — Standard View dashboard
        # row gave zero context to a reader scanning by ticker.
        _snippet = (
            _extract_decision_rationale(state)
            or _first_meaningful_sentence(
                (state.get("trader_investment_plan") or "")
                if isinstance(state, dict) else ""
            )
            or _first_meaningful_sentence(decision)
            or _verdict
        )
        push_analysis(ticker, _market, _verdict, _stance_bar, _snippet)
    except Exception as exc:
        log.info("standardview push hook skipped for %s: %s", ticker, exc)

    return summary, full


def _format_past_outcomes(memory_log, ticker: str, limit: int = 2) -> str:
    """Build a one-or-two line Korean string summarizing the most recent
    RESOLVED recommendations for this ticker. Empty when no resolved
    history exists yet (first-time ticker or all entries still pending).
    """
    try:
        entries = memory_log.load_entries()
    except Exception:
        return ""
    matched = [
        e for e in reversed(entries)
        if e.get("ticker") == ticker and not e.get("pending") and e.get("raw")
    ]
    if not matched:
        return ""
    lines = []
    for e in matched[:limit]:
        raw = e.get("raw") or "n/a"
        alpha = e.get("alpha")
        rating_kr = _RATING_KR.get(e.get("rating", "").upper(), e.get("rating", ""))
        date = e.get("date", "")
        # Compose: "지난 추천 (2026-04-24): 매수 → +5.3% (벤치마크 대비 +1.2%)"
        bench = f" (벤치마크 대비 {alpha})" if alpha and alpha != "n/a" else ""
        lines.append(f"📒 지난 추천 ({date}): {rating_kr} → {raw}{bench}")
    return "\n".join(lines)


_RATING_KR = {
    "BUY": "매수",
    "OVERWEIGHT": "비중확대",
    "HOLD": "보유",
    "UNDERWEIGHT": "비중축소",
    "SELL": "매도",
}


_SECTION_LABELS_FOR_SUMMARY = [
    ("market_report", "📈", "시장"),
    ("sentiment_report", "💬", "감정"),
    ("news_report", "📰", "뉴스"),
    ("fundamentals_report", "💰", "펀더멘털"),
]

# Stance keywords ordered by specificity (longest first to avoid 'buy'
# matching '강한매수' too late). Each entry maps a search key to a short
# Korean label shown in the summary header.
_STANCE_KEYWORDS = [
    ("strong sell", "강매도"),
    ("strong buy", "강매수"),
    ("overweight", "비중확대"),
    ("underweight", "비중축소"),
    ("FINAL TRANSACTION PROPOSAL: BUY", "매수"),
    ("FINAL TRANSACTION PROPOSAL: HOLD", "보유"),
    ("FINAL TRANSACTION PROPOSAL: SELL", "매도"),
    ("매수 (BUY)", "매수"),
    ("매도 (SELL)", "매도"),
    ("보유 (HOLD)", "보유"),
    ("매수 의견", "매수"),
    ("매도 의견", "매도"),
    ("보유 의견", "보유"),
    ("홀드 의견", "보유"),
    ("거래 제안: BUY", "매수"),
    ("거래 제안: SELL", "매도"),
    ("거래 제안: HOLD", "보유"),
    ("buy", "매수"),
    ("sell", "매도"),
    ("hold", "보유"),
    ("매수", "매수"),
    ("매도", "매도"),
    ("보유", "보유"),
    ("홀드", "보유"),
]


# Explicit recommendation patterns — these mean "this is the verdict",
# not "this keyword appears in passing". _extract_stance scans these
# FIRST and prefers their rightmost match over the bare-keyword pass,
# so a body like "HOLD 의견을 제시합니다 ... 단기 조정 시 매수 기회를
# 모색" gets labelled 보유 (the explicit verdict) instead of 매수
# (the bare-keyword fallback's rightmost hit). Mirror every Korean
# explicit verb in both spaced and unspaced parenthesised forms because
# different analysts use both.
_STANCE_EXPLICIT_KEYWORDS = [
    ("FINAL TRANSACTION PROPOSAL: BUY", "매수"),
    ("FINAL TRANSACTION PROPOSAL: HOLD", "보유"),
    ("FINAL TRANSACTION PROPOSAL: SELL", "매도"),
    ("HOLD 의견", "보유"),
    ("BUY 의견", "매수"),
    ("SELL 의견", "매도"),
    ("매수 의견", "매수"),
    ("매도 의견", "매도"),
    ("보유 의견", "보유"),
    ("홀드 의견", "보유"),
    ("매수 (BUY)", "매수"),
    ("매도 (SELL)", "매도"),
    ("보유 (HOLD)", "보유"),
    ("매수(BUY)", "매수"),
    ("매도(SELL)", "매도"),
    ("보유(HOLD)", "보유"),
    ("거래 제안: BUY", "매수"),
    ("거래 제안: SELL", "매도"),
    ("거래 제안: HOLD", "보유"),
    ("거래 액션: BUY", "매수"),
    ("거래 액션: SELL", "매도"),
    ("거래 액션: HOLD", "보유"),
    ("추천: Buy", "매수"),
    ("추천: Overweight", "매수"),
    ("추천: Sell", "매도"),
    ("추천: Underweight", "매도"),
    ("추천: Hold", "보유"),
    # Korean section-title variants seen in analyst output. SNG
    # 2026-05-17 market body concluded with '투자 제언: HOLD' but
    # the bare-keyword fallback picked up a later 매수 mention and
    # mislabeled the stance as 매수.
    ("투자 제언: BUY", "매수"),
    ("투자 제언: SELL", "매도"),
    ("투자 제언: HOLD", "보유"),
    ("투자 제언: 매수", "매수"),
    ("투자 제언: 매도", "매도"),
    ("투자 제언: 보유", "보유"),
    ("투자 제안: BUY", "매수"),
    ("투자 제안: SELL", "매도"),
    ("투자 제안: HOLD", "보유"),
    ("투자 제안: 매수", "매수"),
    ("투자 제안: 매도", "매도"),
    ("투자 제안: 보유", "보유"),
]


def _display_ticker(ticker: str) -> str:
    """Format a ticker for display in headlines and summary cards.

    For KR tickers, prepend the Korean company name from DART so
    '005930.KS' renders as '삼성전자 / 005930.KS' — KR users find pure
    numeric tickers hard to read at a glance. US tickers pass through
    unchanged. Falls back to bare ticker on any lookup failure (DART
    key missing, name not in cache, etc.) so the report still ships.
    """
    try:
        from bot.market import detect_market
        market = detect_market(ticker)
        if market == "KR":
            from bot.dart_client import get_dart
            code = (ticker or "").upper().split(".")[0]
            name = get_dart().stock_code_to_name(code)
            if name:
                return f"{name} / {ticker}"
            return ticker
        if market == "JP":
            # JP: prefer yfinance longName (English / occasionally JP). EDINET
            # Japanese-name lookup will replace this once edinet_client lands.
            try:
                from tradingagents.agents.utils.agent_utils import _instrument_info
                info = _instrument_info(ticker) or {}
                name = info.get("longName") or info.get("shortName")
                if name and name.upper() != ticker.upper():
                    return f"{name} / {ticker}"
            except Exception:
                pass
            return ticker
        if market == "TW":
            # TW: yfinance longName usually carries the English corporate
            # name ('Taiwan Semiconductor Manufacturing Company Limited').
            # Sometimes the 繁體中文 name comes through ('鴻海精密工業'
            # for 2317.TW). Either is OK as a display prefix — the
            # numeric ticker alone (2330.TW / 8299.TWO) is unreadable
            # at a glance like KR numeric tickers.
            try:
                from tradingagents.agents.utils.agent_utils import _instrument_info
                info = _instrument_info(ticker) or {}
                name = info.get("longName") or info.get("shortName")
                if name and name.upper() != ticker.upper():
                    return f"{name} / {ticker}"
            except Exception:
                pass
            return ticker
    except Exception:
        pass
    return ticker


def _extract_stance(body: str | None) -> str:
    """Pick the analyst's bottom-line stance from its report body.

    Three-pass scan (priority order):

    1. CONCLUSION-ZONE explicit patterns: scan the last 800 characters for
       explicit recommendation patterns. Analysts write the verdict in the
       conclusion section near the end; any trailing prose that mentions
       "매도" or "매수" in passing (e.g. "단기 매도 압력을 시사합니다") is
       earlier in the document and loses to the conclusion-zone pattern.
       Fixes 경동나비엔 009450.KS 2026-05-22: market analyst concluded
       "HOLD 의견을 제시합니다" but trailing "매도 압력을 시사합니다" text
       caused rightmost-full-body scan to return "매도" instead of "보유".
    2. Full-body explicit patterns (fallback when conclusion zone has no
       explicit pattern — rare; covers analysts whose verdict appears early).
    3. Rightmost bare keyword (last resort).
    """
    if not body:
        return ""
    lower = body.lower()

    def _rightmost_match_in(text_lower: str, keyword_list):
        by_len = sorted(keyword_list, key=lambda kv: -len(kv[0]))
        accepted: list[tuple[int, int]] = []
        candidates: list[tuple[int, str]] = []
        for keyword, label in by_len:
            kw = keyword.lower()
            pos = text_lower.rfind(kw)
            while pos >= 0:
                end = pos + len(kw)
                inside = any(s <= pos and end <= e for s, e in accepted)
                if not inside:
                    accepted.append((pos, end))
                    candidates.append((pos, label))
                    break
                pos = text_lower.rfind(kw, 0, pos)
        if not candidates:
            return ""
        candidates.sort(key=lambda kv: -kv[0])
        return candidates[0][1]

    # Pass 1: explicit patterns in conclusion zone (last 800 chars)
    _CONCLUSION_ZONE = 800
    zone_lower = lower[-_CONCLUSION_ZONE:] if len(lower) > _CONCLUSION_ZONE else lower
    zone_hit = _rightmost_match_in(zone_lower, _STANCE_EXPLICIT_KEYWORDS)
    if zone_hit:
        return zone_hit

    # Pass 2: explicit patterns full body
    full_hit = _rightmost_match_in(lower, _STANCE_EXPLICIT_KEYWORDS)
    if full_hit:
        return full_hit

    # Pass 3: bare keyword fallback
    return _rightmost_match_in(lower, _STANCE_KEYWORDS)



_DECISION_DIRECTION = {
    "Buy": "buy", "Overweight": "buy",
    "Sell": "sell", "Underweight": "sell",
    "Hold": "hold",
}
_STANCE_DIRECTION_KEYWORDS = (
    ("매수", "buy"),
    ("매도", "sell"),
    ("보유", "hold"),
)
_DIRECTION_KR = {"buy": "매수", "sell": "매도", "hold": "보유"}


def _detect_stance_decision_mismatch(state: dict, final_rating: str) -> str:
    """Return a one-line warning when the analyst-section stance majority
    disagrees with the final BUY/HOLD/SELL direction. Empty string when
    they agree or the signal is too weak.

    The debate / decision LLMs sometimes legitimately override the
    analyst consensus — e.g. when the bear surfaces a new framing the
    analysts missed (the GOOGL 2026-05-10 case: 4×보유 → Underweight
    after the bear caught the Q1 26 81% earnings jump being driven by
    a one-off security sale; the PLUG 2026-05-10 case: 2 매수 + 1 보유
    + 1 매도 → Sell after the bear surfaced cash-burn / dilution risk).
    That's valuable, not a bug. But the summary card showing the stance
    bar alongside a contradicting verdict is genuinely confusing to
    read, and the user deserves to know to re-read the decision
    rationale instead of glossing over.

    Trigger rule: more analysts disagreed with the final direction than
    agreed. A 2-2 tie where the final matches one side, or a 3-1 with
    the final matching the 3, both stay quiet.
    """
    final_dir = _DECISION_DIRECTION.get(final_rating)
    if not final_dir:
        return ""
    counts = {"buy": 0, "sell": 0, "hold": 0}
    for key, _, _ in _SECTION_LABELS_FOR_SUMMARY:
        body = state.get(key) if isinstance(state, dict) else None
        stance = _extract_stance(body)
        if not stance:
            continue
        for kw, direction in _STANCE_DIRECTION_KEYWORDS:
            if kw in stance:
                counts[direction] += 1
                break
    total = sum(counts.values())
    if total < 2:
        return ""  # too few stances to form a meaningful comparison
    final_count = counts.get(final_dir, 0)
    disagreeing = total - final_count
    if disagreeing <= final_count:
        return ""
    # Show actual breakdown so the user can see WHY we're flagging.
    breakdown = " · ".join(
        f"{_DIRECTION_KR[d]} {c}" for d, c in counts.items() if c > 0
    )
    lines = [
        f"⚠️ 분석가 stance vs 결정: {breakdown} → 결정 "
        f"{_DIRECTION_KR[final_dir]} ({final_count}/{total}명만 동의, "
        f"토론·결정 단계가 다수 분석가와 다른 결론)"
    ]
    rationale = _extract_decision_rationale(state)
    if rationale:
        lines.append(f"결정 근거: {rationale}")
    return "\n".join(lines)


_RATIONALE_BLOCK_RE = re.compile(
    r"근거\s*:\s*(.+?)(?=\s*전략\s*실행\s*:|\s*거래\s*액션\s*:|$)",
    re.DOTALL,
)


def _extract_decision_rationale(state: dict) -> str:
    """Pull a short version of the decision rationale from research_manager's
    investment_plan. Returns the first 1–2 sentences after '근거:' or empty
    string when the marker isn't present.

    The full rationale is several paragraphs long; the summary card only has
    room for one line, so we trim aggressively. Truncation rules:
      * stop at the next major section header (전략 실행, 거래 액션)
      * keep the LAST 2 sentences (the verdict + its reasoning live there;
        the first sentences are typically scene-setting that says
        "this was a contest between bull and bear" without conveying
        which side won)
      * hard-cap at 220 chars and ellipsize if needed
    """
    plan = (state.get("investment_plan") or "") if isinstance(state, dict) else ""
    if not plan:
        return ""
    m = _RATIONALE_BLOCK_RE.search(plan)
    if not m:
        return ""
    body = m.group(1).strip()
    if not body:
        return ""
    # Split on Korean sentence enders + ASCII period; take last 2 sentences
    # so the verdict and its reasoning come through instead of the intro.
    sentences = [s.strip() for s in re.split(r"(?<=[.다요])\s+", body) if s.strip()]
    if not sentences:
        return ""
    tail = sentences[-2:] if len(sentences) >= 2 else sentences
    out = " ".join(tail).strip()
    if len(out) > 220:
        out = out[:217].rstrip() + "…"
    return out


def _format_summary(
    state: dict,
    decision: str,
    ticker: str,
    date_: str,
    past_outcomes: str = "",
    notes: list[str] | None = None,
) -> str:
    rating = _extract_rating(decision) or "N/A"
    display = _display_ticker(ticker)
    parts = [
        f"📊 **{display}** ({date_})",
        "━━━━━━━━━━━━━━",
        f"🎯 최종 판정: **{rating}**",
    ]
    if past_outcomes:
        parts.append(past_outcomes)
    # Pre-flight notes (news pre-check, ETF detection, etc) sit right
    # under the headline so the user knows up front which analyst paths
    # were skipped before reading the stance bar — otherwise a missing
    # 📰 뉴스 line in the stance bar looks like a silent failure.
    if notes:
        for note in notes:
            parts.append(note)
    # Compact one-line per-analyst stance bar so the user sees who voted
    # what at a glance, before the longer per-section snippets.
    # Fallback to '미명시' when an analyst body produces no recognizable
    # verdict — 한국전력공사 2026-05-17 had the fundamentals body skip
    # the explicit verdict line, so the stance bar silently dropped from
    # 4 entries to 3 and the user couldn't tell whether the analyst
    # actually ran. Showing '미명시' instead of omission makes the gap
    # visible and prevents the mismatch detector's denominator from
    # silently shrinking.
    stance_chunks = []
    for key, icon, name in _SECTION_LABELS_FOR_SUMMARY:
        body = state.get(key) if isinstance(state, dict) else None
        if not body or not body.strip():
            continue  # analyst was pre-flight-skipped; don't show entry at all
        stance = _extract_stance(body) or "미명시"
        stance_chunks.append(f"{icon} {name}: {stance}")
    if stance_chunks:
        parts.append("  ·  ".join(stance_chunks))
    # Surface analyst-vs-decision direction mismatch on its own line so
    # the user doesn't gloss over a verdict that contradicts the stance bar.
    mismatch = _detect_stance_decision_mismatch(state, rating)
    if mismatch:
        parts.append(mismatch)
    parts.append("")
    # One key sentence per analyst section, so the summary tells the user
    # WHY without forcing them to expand the full report.
    for key, icon, name in _SECTION_LABELS_FOR_SUMMARY:
        body = state.get(key) if isinstance(state, dict) else None
        snippet = _first_meaningful_sentence(body) if body else ""
        if snippet:
            parts.append(f"{icon} **{name}**: {snippet}")
    parts.append("")
    parts.append(_first_lines(decision, max_lines=4))
    return "\n".join(parts)


def _first_meaningful_sentence(text: str, max_chars: int = 200) -> str:
    """Pick the first non-trivial line from a section body. Tries to end at
    a sentence boundary instead of hard-cutting mid-word."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("#", "•", "*", "-", "|")):
            continue
        if line.endswith(":"):
            continue
        # Strip the rating echo lines that managers/analysts sometimes put
        # at the very top of their report — those bubble up into the user-
        # facing summary as a redundant 'FINAL TRANSACTION PROPOSAL: HOLD'
        # line that's already conveyed by the per-analyst stance bar.
        if "FINAL TRANSACTION PROPOSAL" in line.upper():
            continue
        if len(line) < 30:
            continue
        if len(line) <= max_chars:
            return line
        # Prefer ending at the closest '. ' before max_chars (Korean and
        # English both end sentences with that pattern). If none found in
        # the back half of the budget, fall back to a hard cut with '…'.
        cutoff = line.rfind(". ", 0, max_chars + 1)
        if cutoff >= max_chars - 80:
            return line[: cutoff + 1]
        return line[:max_chars].rstrip() + "…"
    return ""


_REPORT_SECTIONS = [
    ("market_report", "📈 시장 분석", "market"),
    ("sentiment_report", "💬 감정 분석", "social"),
    ("news_report", "📰 뉴스 분석", "news"),
    ("fundamentals_report", "💰 펀더멘털", "fundamentals"),
    ("investment_plan", "🧭 투자 계획", None),
    ("trader_investment_plan", "💼 트레이더 제안", None),
]


def _extract_canonical(ticker: str) -> dict:
    """Pull key numeric facts for post-processing validation.

    Used by _clean_section → _polish to cross-check analyst output numbers
    against yfinance ground truth. Returns {} on any failure.
    """
    try:
        from tradingagents.agents.utils.agent_utils import _instrument_info
        from bot.market import get_market_config
        info = _instrument_info(ticker)
        cfg  = get_market_config(ticker)
        result: dict = {
            "currency":        cfg.get("currency", "USD"),
            "currency_symbol": cfg.get("currency_symbol", "$"),
        }
        mc = info.get("marketCap")
        px = info.get("currentPrice") or info.get("regularMarketPrice")
        if isinstance(mc, (int, float)) and mc > 0:
            result["market_cap"] = mc
        if isinstance(px, (int, float)) and px > 0:
            result["current_price"] = px
        return result
    except Exception:
        return {}


def _format_full(
    state: dict,
    decision: str,
    ticker: str,
    date_: str,
    past_outcomes: str = "",
    selected: list[str] | None = None,
) -> str:
    """Render the long-form report. `selected` is the per-run list of
    analyst ids that actually ran — when the pre-flight pruning drops
    one (e.g. news for a 0-coverage KR ticker), we use the run-level
    list so the section header isn't followed by a misleading
    '_(모델 응답 오류로 미완성)_' placeholder. Falls back to the
    module-default list when callers don't pass anything."""
    parts = [f"📋 {_display_ticker(ticker)} 전체 리포트 ({date_})\n"]
    if past_outcomes:
        parts.append(past_outcomes + "\n")
    # Ticker-derived currency symbol for polish step. Trader / PM
    # sections often have zero currency marker in their own body text;
    # the per-section body-scan in _polish misses them. Pass the
    # MARKET_CONFIG symbol explicitly so 'Stop Loss 3,150' becomes
    # 'NT$3,150' for TW tickers regardless of whether Trader's body
    # mentions NT$.
    try:
        from bot.market import get_market_config
        _section_currency_symbol = get_market_config(ticker).get("currency_symbol", "")
    except Exception:
        _section_currency_symbol = ""
    # Pre-extract canonical numbers once for the whole report so every
    # section's post-processing validator can cross-check against ground truth.
    _canonical = _extract_canonical(ticker)
    run_selected = set(selected) if selected is not None else set(_SELECTED_ANALYSTS)
    module_default = set(_SELECTED_ANALYSTS)
    for key, label, analyst_id in _REPORT_SECTIONS:
        if analyst_id is not None and analyst_id not in module_default:
            # never wired into the graph at all — skip silently
            continue
        if analyst_id is not None and analyst_id not in run_selected:
            # Pre-flight pruning dropped this analyst on this run.
            # Surface a clear '자동 생략' line instead of letting the
            # section fall through to the generic FAILURE_PLACEHOLDER
            # which reads as a model error — the user already saw the
            # skip note in the summary header and shouldn't see a
            # contradicting 'oops' message in the full report.
            parts.append(
                f"\n## {label}\n_(분석가 자동 생략 — 사전 데이터 부족 또는"
                f" 종목 특성상 해당 분석을 진행하지 않았습니다. 요약 메시지"
                f" 상단의 사유를 참고하세요.)_"
            )
            continue
        body = state.get(key) if isinstance(state, dict) else None
        parts.append(
            f"\n## {label}\n{_clean_section(body, currency_symbol=_section_currency_symbol, canonical=_canonical)}"
        )
    parts.append(f"\n## ✅ 최종 결정\n{decision}")
    return "\n".join(parts)


_GARBAGE_MARKERS = (
    '{"error"',
    '"error":',
    "tool_code",
    "default_api.",
    "print(default_api",
)
_FAILURE_PLACEHOLDER = "_(이번 분석은 모델 응답 오류로 미완성. 다른 티커로 재시도해보세요.)_"

# Patterns the agents sometimes emit that read as noise to a human reader.
_FINAL_PROPOSAL_RE = re.compile(
    r"^[^\n]*FINAL TRANSACTION PROPOSAL[^\n]*$",
    re.MULTILINE | re.IGNORECASE,
)
# Raw tool-result fragments: news payload pieces that the agent should have
# summarized but instead pasted verbatim. The dumps contain literal '\n'
# escape sequences (two characters: a backslash followed by 'n'), not real
# newlines, so the patterns match those escapes too.
_LINK_LINE_RE = re.compile(r"\\?nLink:\s*[^\s\\]+", re.IGNORECASE)
_TOOL_HEADER_RE = re.compile(
    r"###\s+[^()\n\\]{1,300}\(source:\s*[^)\n\\]+\)",
    re.IGNORECASE,
)
_ESCAPED_NEWLINES_RE = re.compile(r"(?:\\n){2,}")

# build_instrument_context() output that the model occasionally pastes
# verbatim into the section body (e.g. 'The instrument to analyze is `ALAB`.
# Use this exact ticker ... (e.g. `.TO`, `.L`, …).'). Strip those leaks.
_INSTRUMENT_CTX_RE = re.compile(
    r"(?:The current date is\s+\d{4}-\d{2}-\d{2}\.\s*)?"
    r"The instrument to analyze is\s+[`'\"]?[^`'\"\n]+[`'\"]?\.\s+"
    r"Use this exact ticker[^\n]*?\([^)]+\)\.?",
    re.IGNORECASE,
)

# Bullet rows where the agent padded a single point-in-time value with
# trailing '— -' placeholders (e.g. '시가총액: $36.94B — - — - — -').
# Collapse to just the value(s) actually present.
_DASH_PADDING_RE = re.compile(r"(?: [—\-]{1,5} -){2,15}\s*$", re.MULTILINE)

# English structured-field labels that the research_manager / trader / risk
# debators emit even under a Korean directive. The patterns tolerate
# Markdown-bold wraps ('**Recommendation**:') as well as plain
# 'Recommendation:'. The leading-of-line anchor keeps in-prose mentions
# from being touched.
_KO_LABEL_REPLACEMENTS = [
    (re.compile(r"(?m)^(\*+)?Recommendation(\*+)?:\s*", re.IGNORECASE), r"\1추천\2: "),
    (re.compile(r"(?m)^(\*+)?Rationale(\*+)?:\s*", re.IGNORECASE), r"\1근거\2: "),
    (re.compile(r"(?m)^(\*+)?Strategic Actions(\*+)?:\s*", re.IGNORECASE), r"\1전략 실행\2: "),
    (re.compile(r"(?m)^(\*+)?Action(\*+)?:\s*", re.IGNORECASE), r"\1거래 액션\2: "),
    (re.compile(r"(?m)^(\*+)?Reasoning(\*+)?:\s*", re.IGNORECASE), r"\1근거\2: "),
    (re.compile(r"(?m)^(\*+)?Position Sizing(\*+)?:\s*", re.IGNORECASE), r"\1포지션 규모\2: "),
    # Inline English words that occasionally slip into Korean prose.
    (re.compile(r"\bprudent\b", re.IGNORECASE), "신중함"),
    (re.compile(r"\bactionable\b", re.IGNORECASE), "실행 가능한"),
]

# Comma-separated big numbers (≥ 1,000,000) and bare 9+-digit integers,
# optionally followed by 달러/원. The lookarounds also exclude word chars,
# '.', '/', and '-' so digit runs inside URLs (e.g. 'articles/.../12345678.html')
# don't get rewritten into '약 12.3백만'.
_LARGE_NUM_RE = re.compile(
    r"(?<![\w./\-])(\d{1,3}(?:,\d{3}){2,}|\d{9,})(\s*(?:달러|원))?(?![\w./\-])"
)
# Decimals with 5+ digits after the dot — collapse to 4 places for readability.
_LONG_DECIMAL_RE = re.compile(r"(?<![\d.])(\d+\.\d{5,})(?![\d.])")
# Runaway repetition Gemini sometimes emits ('--------…' x thousands of chars).
_RUNAWAY_CHAR_RE = re.compile(r"([\-=_*#~])\1{29,}")
# Whole lines that are essentially nothing but dashes / equals / underscores
# (Markdown table separators echoed dozens of times). _RUNAWAY_CHAR_RE
# misses these when whitespace breaks the run; this pattern catches lines
# made of those chars even with intermixed spaces. 15 minimum keeps real
# horizontal rules ('---' as a Markdown separator) intact.
_DASH_LINE_RE = re.compile(r"(?m)^[\s\-=_*#~]{15,}$\n?")

# Korean place-value reading like '46억 7100만 64 달러' that Gemini often emits
# when reading large dollar amounts verbatim. We rewrite them as '약 47억 달러'.
_KO_NUM_RE = re.compile(
    r"(?<![\w가-힣])"
    # Require at least one of the place-value groups OR a leading digit
    # before '달러'. The previous fully-optional structure (every group
    # `?`) gave the engine free rein to try empty matches at every
    # '달러' occurrence; on a 76K-char fundamentals body the catastrophic
    # backtracking triggered the SIGALRM step guard. The lookahead anchors
    # the prefix so the regex only fires when there's actually a number to
    # normalize.
    r"(?=\d)"
    r"(?:(\d{1,4})\s{0,3}조\s{0,3})?"
    r"(?:(\d{1,4})\s{0,3}억\s{0,3})?"
    r"(?:(\d{1,4})\s{0,3}만\s{0,3})?"
    r"(\d{1,4})?"
    r"\s{0,3}달러"
    r"(?!\w)"
)

# Bullet rows that label a financial concept and dump a series of numbers
# without a unit, e.g. '• 총 자산: 10,176 — 9,710 — …'. We append a unit hint
# only when the label contains a clear monetary keyword and the line has
# neither '달러' nor '%' already.
_FIN_KEYWORDS = (
    "자산", "부채", "자본", "매출", "이익", "흐름", "비용", "현금",
    "투자", "영업", "순이익", "EBITDA", "EPS", "배당", "장부",
)
_FIN_BULLET_RE = re.compile(
    r"^(\s*[•\*\-]\s*(?:\*\*)?[^:\n]*?:\s*)"
    r"([\-\d,.\s—]+)$",
    re.MULTILINE,
)

# A line that looks like the start of a fresh numbered report section, e.g.
# '1. 회사 개요', '1. 포괄적 재무 데이터'. When we see two of them inside a
# single agent body we treat the second as a Gemini re-emission and truncate.
_DUP_HEADER_RE = re.compile(
    r"(?m)^\s*1\.\s+"
    r"(?:회사\s*개요|기업\s*개요|회사\s*기본\s*정보|"
    r"포괄적\s*재무\s*데이터|기업\s*심층\s*분석|"
    r"기업\s*기본\s*정보|회사\s*기본\s*프로필)\b"
)
# Generic fallback: '1. <something>' appearing twice with substantial
# content in between is also a re-emission signal (catches cases like
# '1. 가격 추세' restarting after '1. 이동평균선' has already been used).
_DUP_NUMBERED_RE = re.compile(r"(?m)^\s*1\.\s+\S")
# Cross-section emoji headers that an analyst should never include in
# its OWN body — they belong to downstream nodes (research_manager /
# trader / portfolio_manager). When the fundamentals analyst goes into
# a re-emission loop (ONTO 2026-05-10 case) it sometimes mimics the
# downstream report format and writes these headers itself, repeating
# them at the tail. A second occurrence of any of these inside a single
# analyst body is unambiguous evidence of duplication — truncate at it.
_DUP_DOWNSTREAM_RE = re.compile(
    r"(?m)^\s*(?:🧭\s*투자\s*계획|💼\s*트레이더\s*제안|✅\s*최종\s*결정)\b"
)
# DCF / 결론 / 거래자 인사이트 etc — fundamentals-analyst-specific
# section labels that should appear at most once per body. Their second
# occurrence is the same kind of re-emission signal.
_DUP_FUND_TAIL_RE = re.compile(
    r"(?m)^\s*(?:DCF\s*시나리오|결론\s*및\s*투자\s*통찰|"
    r"결론\s*및\s*거래\s*시사점|거래자\s*인사이트)\b"
)


def _abbrev_korean(num: int) -> str:
    if num >= 10**12:
        return f"약 {num / 10**12:.2f}조"
    if num >= 10**8:
        return f"약 {num / 10**8:.0f}억"
    if num >= 10**6:
        return f"약 {num / 10**6:.1f}백만"
    return ""


def _abbrev_match(m: re.Match) -> str:
    raw = m.group(0)
    num_str = m.group(1).replace(",", "")
    unit = (m.group(2) or "").strip()
    try:
        num = int(num_str)
    except ValueError:
        return raw
    abbreviated = _abbrev_korean(num)
    if not abbreviated:
        return raw
    return f"{abbreviated} {unit}".rstrip() if unit else abbreviated


def _round_long_decimal(m: re.Match) -> str:
    try:
        return f"{float(m.group(1)):.4f}"
    except ValueError:
        return m.group(0)


def _ko_num_normalize(m: re.Match) -> str:
    """'46억 7100만 64 달러' → '약 46.7억 달러'. Skips single-part values
    (like '8억 달러') so the regex doesn't churn perfectly fine text."""
    jo = int(m.group(1)) if m.group(1) else 0
    eok = int(m.group(2)) if m.group(2) else 0
    man = int(m.group(3)) if m.group(3) else 0
    base = int(m.group(4)) if m.group(4) else 0
    parts = sum(1 for x in (jo, eok, man, base) if x > 0)
    if parts < 2:
        return m.group(0)
    total = jo * 10**12 + eok * 10**8 + man * 10**4 + base
    if total >= 10**12:
        return f"약 {total / 10**12:.2f}조 달러"
    if total >= 10**11:
        return f"약 {round(total / 10**8)}억 달러"
    if total >= 10**8:
        return f"약 {total / 10**8:.1f}억 달러"
    if total >= 10**6:
        return f"약 {round(total / 10**6)}백만 달러"
    return m.group(0)


def _add_unit_hint(m: re.Match) -> str:
    label = m.group(1)
    nums = m.group(2).rstrip()
    # The regex's number-only character class can't contain '%' / '달러' /
    # '원' anyway, so the only thing left to filter is whether the *label*
    # actually mentions a monetary concept. (We can't filter on "원" in the
    # label because of words like '매출원가'.)
    if not any(kw in label for kw in _FIN_KEYWORDS):
        return m.group(0)
    if nums.count("—") < 1:
        return m.group(0)
    return f"{label}{nums} (단위: 백만 달러)"


def _find_first_repeat(pattern: re.Pattern, body: str) -> int | None:
    """Return the start position of the FIRST occurrence whose matched
    text was already seen earlier in the body, else None.

    Patterns like _DUP_DOWNSTREAM_RE and _DUP_FUND_TAIL_RE have multiple
    alternatives (e.g. 🧭/💼/✅). Naively taking matches[1] would point
    at a different header's first appearance, not a duplicate. We key
    by the actual matched text so only true repetition triggers
    truncation.
    """
    seen: set[str] = set()
    for m in pattern.finditer(body):
        key = re.sub(r"\s+", " ", m.group().strip())
        if key in seen:
            return m.start()
        seen.add(key)
    return None


def _drop_repeated_section(body: str) -> str:
    """If a major section header appears twice, the agent emitted its
    report twice — keep only up to the second occurrence."""
    candidates: list[int] = []
    for pattern in (_DUP_HEADER_RE, _DUP_DOWNSTREAM_RE, _DUP_FUND_TAIL_RE):
        pos = _find_first_repeat(pattern, body)
        if pos is not None:
            candidates.append(pos)
    nums = list(_DUP_NUMBERED_RE.finditer(body))
    if len(nums) >= 2 and nums[1].start() - nums[0].start() > 1000:
        candidates.append(nums[1].start())
    if candidates:
        return body[: min(candidates)].rstrip()
    return body


def _magnitude_check(body: str, canonical: dict) -> str:
    """Scan RULE 1 financial series for unit-magnitude jumps within the
    same line (e.g. 'FY24 ₩89,201억 | FY23 ₩90.19조' — 10,000x gap
    = 조/억 mix). Inserts a ⚠️ warning line immediately after any
    financial series line where adjacent KRW/JPY values differ by > 500x.

    Only applies to KRW / JPY output (large-unit languages).
    """
    currency = canonical.get("currency", "USD") if canonical else "USD"
    if currency not in ("KRW", "JPY"):
        return body

    # Capture optional leading minus so adverse periods (FY22 -8,544억)
    # don't get silently flipped to positive in the normalized companion
    # line. Preceded by lookbehind for word-boundary-ish char so we don't
    # accidentally grab the dash in '|—|' table separators.
    _val_re = re.compile(r'(-?\d[\d,\.]*)\s*(조|억)')

    def _to_raw(num_str: str, unit: str) -> float:
        try:
            return float(num_str.replace(",", "")) * (1e12 if unit == "조" else 1e8)
        except ValueError:
            return 0.0

    result: list[str] = []
    for line in body.split("\n"):
        result.append(line)
        matches = list(_val_re.finditer(line))
        vals = [(_to_raw(m.group(1), m.group(2)), m.group(2))
                for m in matches]
        if len(vals) >= 2:
            for i in range(len(vals) - 1):
                v1, u1 = vals[i]
                v2, u2 = vals[i + 1]
                # Any 조/억 unit mix within the same series line is suspicious.
                # LG전자 case: 'FY24 ₩89,201억 | FY23 ₩90.19조' — both
                # represent similar revenue scales but expressed inconsistently.
                if abs(v1) > 0 and abs(v2) > 0 and u1 != u2:
                    # 2026-05-23 (010140.KS surfaced): the warning alone
                    # was leaking through to reader 6+ times in one
                    # analysis. Add a normalized companion line so the
                    # reader sees the corrected series alongside the
                    # warning, without disturbing the LLM's original
                    # text (transparency). Pick the larger unit (조 if
                    # any value ≥ 1조 in magnitude, else 억) as the
                    # canonical so decimals stay sane. Use abs() in the
                    # magnitude pick so a series with -8.5조 still
                    # renders in 조.
                    raw_vals = [v for v, _ in vals if v != 0]
                    use_jo = any(abs(v) >= 1e12 for v in raw_vals)
                    unit_label = "조 원" if currency == "KRW" else (
                        "兆 円" if currency == "JPY" else None
                    )
                    scale = 1e12 if use_jo else 1e8
                    if not use_jo:
                        unit_label = "억 원" if currency == "KRW" else (
                            "億 円" if currency == "JPY" else None
                        )
                    if unit_label:
                        normalized = [
                            (f"-{abs(v) / scale:,.2f}{unit_label[0]}"
                             if v < 0
                             else f"{v / scale:,.2f}{unit_label[0]}")
                            for v in raw_vals
                        ]
                        result.append(
                            f"  ⚠️ [단위 오류 의심: 동일 항목 내 {u1} / {u2} 혼용"
                            f" — 같은 시계열에는 단일 단위 사용 필요."
                            f" 원본 데이터 확인 후 통일 표기 요망]"
                        )
                        result.append(
                            f"  ↳ 정규화 ({unit_label} 단일 기준): "
                            + " — ".join(normalized)
                        )
                    else:
                        result.append(
                            f"  ⚠️ [단위 오류 의심: 동일 항목 내 {u1} / {u2} 혼용"
                            f" — 같은 시계열에는 단일 단위 사용 필요."
                            f" 원본 데이터 확인 후 통일 표기 요망]"
                        )
                    break
    return "\n".join(result)


def _canonical_crosscheck(body: str, canonical: dict) -> str:
    """Check 시가총액 mentions in the output against the canonical value.

    If canonical 시총 is ₩5.83조 but the text mentions ₩58조 or ₩0.58조
    (> ±30% off), inserts a correction banner so the reader can spot the
    discrepancy. Only applied for KRW / JPY markets where 조/억 confusion
    is the primary failure mode.
    """
    mc = canonical.get("market_cap") if canonical else None
    currency = canonical.get("currency", "USD") if canonical else "USD"
    if not mc or currency not in ("KRW", "JPY"):
        return body

    _mc_kw_re  = re.compile(r'시가총액|시총')
    _val_re    = re.compile(r'약\s*(\d[\d,\.]*)\s*(조|억)')
    unit_word  = "원" if currency == "KRW" else "엔"
    mc_disp    = (f"약 {mc / 1e12:,.2f}조 {unit_word}"
                  if mc >= 1e12 else f"약 {mc / 1e8:,.0f}억 {unit_word}")

    def _norm(num_str: str, unit: str) -> float:
        try:
            return float(num_str.replace(",", "")) * (1e12 if unit == "조" else 1e8)
        except ValueError:
            return 0.0

    result: list[str] = []
    for line in body.split("\n"):
        result.append(line)
        if not _mc_kw_re.search(line):
            continue
        for m in _val_re.finditer(line):
            found = _norm(m.group(1), m.group(2))
            if found <= 0:
                continue
            ratio = max(found, mc) / min(found, mc)
            if ratio > 1.3:
                result.append(
                    f"  ⚠️ [시가총액 불일치: 위 값이 시스템 canonical ({mc_disp}) 대비"
                    f" {ratio:.1f}배 차이 — canonical 값 우선 사용]"
                )
                break
    return "\n".join(result)


# If a section body is longer than this, skip the polish-pass regexes
# entirely and serve the raw content. Polishing a 100K+ char Korean
# response can hit catastrophic backtracking on _INSTRUMENT_CTX_RE /
# _KO_NUM_RE and burn the whole 10-min wall budget. The reader sees a
# slightly noisier report, which is far better than no report at all.
_POLISH_LENGTH_GUARD = 100_000

# Each polish step paired with a short label so we can log which step
# is currently running. Past hangs in this function happened silently
# between the propagate log and the timeout — the labels make the
# culprit obvious in the journal next time.
def _polish(body: str, currency_symbol: str = "", canonical: dict | None = None) -> str:
    """Strip noise patterns that agents occasionally leak into their text.

    Removes per-section 'FINAL TRANSACTION PROPOSAL' lines (the rating is
    already extracted into the summary, so showing them again — sometimes
    contradicting each other — just confuses the reader), raw tool-result
    fragments like literal '\\nLink: https://...' and '### Title (source: X)'
    headers, and collapses leftover literal '\\n\\n' escape sequences and
    runs of blank lines.
    """
    # Cheap, high-impact passes ALWAYS run, even before the length guard.
    # When a fundamentals analyst response goes off the rails and emits a
    # 50K-char run of dashes, the body can balloon past the polish-length
    # threshold and bypass the runaway cleanup, leaving a wall of '---'
    # in the user-facing report. Stripping runaway chars + dash-only
    # lines is O(n) regex with no backtracking risk, so doing it up
    # front is essentially free and fixes the common case.
    pre_len = len(body)
    body = _RUNAWAY_CHAR_RE.sub("", body)
    body = _DASH_LINE_RE.sub("", body)
    if len(body) != pre_len:
        log.info("polish: pre-strip removed %d chars of runaway/dash garbage",
                 pre_len - len(body))

    if len(body) > _POLISH_LENGTH_GUARD:
        log.warning(
            "polish: body too long (%d chars > %d) — skipping regex pass",
            len(body), _POLISH_LENGTH_GUARD,
        )
        return body.strip()

    def _step(label: str, fn):
        """Run a single polish pass with a per-step wall-clock guard.

        Past incidents (ONTO 2026-05-10 hung _format_full for 7+ minutes
        between propagate-done and the subprocess timeout, no journal
        evidence of which regex was responsible) showed that a single
        pathological input can push one of these regexes into worst-case
        backtracking and silently burn the whole 10-minute budget. Each
        step now gets 10 wall seconds; if it doesn't return in time we
        skip it and continue with the unchanged body. The skipped log
        line names the step so the regex can be hardened later, and
        readability of the section degrades gracefully (one missing
        cosmetic pass) instead of the whole analysis getting killed.

        SIGALRM works here because we run in the analyze_worker
        subprocess — main thread, no other timers in flight.
        """
        nonlocal body
        import signal as _signal

        class _StepTimeout(Exception):
            pass

        def _handler(_signum, _frame):
            raise _StepTimeout()

        prev = _signal.signal(_signal.SIGALRM, _handler)
        _signal.alarm(10)
        t0 = time.time()
        try:
            body = fn(body)
            log.info("polish: %s done (%d chars)", label, len(body))
        except _StepTimeout:
            log.warning(
                "polish: %s exceeded 10s wall budget — skipping (body kept "
                "as-is, %d chars)", label, len(body),
            )
        finally:
            _signal.alarm(0)
            _signal.signal(_signal.SIGALRM, prev)
            elapsed = time.time() - t0
            if elapsed > 1.0:
                log.info("polish: %s took %.1fs", label, elapsed)

    _step("final-proposal",     lambda b: _FINAL_PROPOSAL_RE.sub("", b))
    _step("link-line",          lambda b: _LINK_LINE_RE.sub("", b))
    _step("tool-header",         lambda b: _TOOL_HEADER_RE.sub("", b))
    _step("instrument-ctx",      lambda b: _INSTRUMENT_CTX_RE.sub("", b))
    _step("dash-padding",        lambda b: _DASH_PADDING_RE.sub("", b))
    _step("escaped-newlines",    lambda b: _ESCAPED_NEWLINES_RE.sub("\n\n", b))
    _step("literal-bs-n",        lambda b: b.replace("\\n", "\n"))
    _step("runaway-char",        lambda b: _RUNAWAY_CHAR_RE.sub("", b))
    _step("drop-repeated",       _drop_repeated_section)
    _step("large-num",           lambda b: _LARGE_NUM_RE.sub(_abbrev_match, b))
    _step("ko-num",              lambda b: _KO_NUM_RE.sub(_ko_num_normalize, b))
    _step("long-decimal",        lambda b: _LONG_DECIMAL_RE.sub(_round_long_decimal, b))
    _step("fin-bullet",          lambda b: _FIN_BULLET_RE.sub(_add_unit_hint, b))
    for idx, (pat, repl) in enumerate(_KO_LABEL_REPLACEMENTS, 1):
        _step(f"ko-label-{idx}", lambda b, p=pat, r=repl: p.sub(r, b))
    _step("blank-lines",         lambda b: re.sub(r"\n{3,}", "\n\n", b))
    # 4-digit-comma 'X,XXXX' integers (Japanese 万 / Korean 만 style)
    # are unreadable when mixed into a Korean prose paragraph. Toyota
    # 7203.T 2026-05-18 had '매출 (억 엔): FY25: 48,0367' (raw
    # 480,367 with the comma in the wrong place). We don't try to
    # auto-relocate the comma (risky — would change the magnitude
    # silently); instead we strip the misplaced comma so the number
    # becomes a plain integer that the reader can interpret without
    # guessing the digit grouping. Replace '\d,\d{4}\b' patterns only.
    # Won't touch legit 3-digit-grouped numbers (1,234 / 12,345 /
    # 123,456) or properly-grouped large numbers (1,234,567).
    _step("strip-4digit-comma", lambda b: re.sub(
        r"(\d),(\d{4})(?!\d)", r"\1\2", b,
    ))
    # '백만' / '백만 원' style numbers (e.g. '₩약 1.6백만', '약 1.5백만 원')
    # are explicitly forbidden by our currency directive — '백만' is a
    # million-style English unit awkward in Korean. Surface in 두산
    # 000150.KS 2026-05-18 (₩약 1.6백만 throughout all four analyst
    # sections + DCF + stop loss) and 삼성전기 2026-05-17 (₩약 1.0백만).
    # Text directive alone keeps getting ignored; auto-convert at the
    # polish layer. Maps '₩약 X.XX백만' / '약 X.XX백만 원' → '약 XYZ만 원'
    # (X.X × 100 = XYZ 만 원). Handles 1-2 decimal places.
    def _convert_baekman(b: str) -> str:
        def repl(m: re.Match) -> str:
            try:
                val = float(m.group(1))
            except ValueError:
                return m.group(0)
            man = val * 100  # 백만 = 100만
            if man.is_integer() and man < 100_000:
                return f"약 {int(man):,}만 원"
            return f"약 {man:,.1f}만 원"
        # Three permitted spellings: '₩약 X.X백만', '약 X.X백만 원',
        # '약 X.X백만'. The optional '(?:\s*원)?' tail means we only
        # consume trailing ' 원' when 원 is actually present —
        # otherwise we'd swallow the trailing space before the next
        # token (e.g. '백만 —' would become '약 XXX만 원—' losing the
        # separator space).
        # NEGATIVE LOOKAHEAD `(?!\s*[엔円¥$€元주株])`: don't fire when the
        # following token is a non-KRW currency marker. JP / TW / CN /
        # US analysts writing '약 1.6백만 엔' / '약 X백만 元' / '약 X
        # 백만 $' would otherwise be silently corrupted to '약 N0만 원
        # X' (KRW unit slapped on a non-KRW number). The cross-market
        # audit caught this — '백만' is intended to be a KR-only fix
        # and the lookahead enforces that scope. 元 is critical: TWD
        # uses '元' / '兆 元' / '億 元', CNY uses '元' / '亿元', JPY
        # ALSO uses '円' which is already covered by '円'.
        out = re.sub(
            r"₩\s*약\s*(\d+(?:\.\d+)?)\s*백만(?:\s*원)?(?!\s*[엔円¥$€元주株])",
            repl, b,
        )
        out = re.sub(
            r"약\s*(\d+(?:\.\d+)?)\s*백만(?:\s*원)?(?!\s*[엔円¥$€元주株])",
            repl, out,
        )
        return out
    _step("convert-baekman", _convert_baekman)
    # Debt-ratio '배' (multiples) unit error. yfinance .info debtToEquity
    # is a percentage (e.g. 82.77 = 82.77%), but the LLM occasionally
    # renders it as '부채비율: 82.77배' which would mean debt = 82.77× of
    # equity — a near-insolvent reading that misleads the reader. 두산
    # 000150.KS 2026-05-18 fundamentals had this exact error. Convert
    # the '배' suffix to '%' specifically when the leading label is
    # 부채비율 (other ratios like 유동비율 1.03배 are legitimately
    # expressed as multiples and left alone).
    _step("debt-ratio-unit", lambda b: re.sub(
        r"(부채비율[:\s]*\d+(?:\.\d+)?)\s*배",
        r"\1%",
        b,
    ))
    # Currency-symbol + thousands-comma for bare integers in trader /
    # stop-loss / target-price cells. Toyota / 두산 reports had
    # 'Stop Loss: 1321500.0' — should be '₩1,321,500'. The polish layer
    # doesn't know the ticker's market, so we use a context-aware
    # pattern: any decimal-zero integer immediately preceded by a
    # currency context keyword (Stop Loss / 손절 / 목표 / 진입가).
    def _format_bare_currency(b: str) -> str:
        # Match '<keyword>: <integer>.0' and re-format integer with commas
        # + prepend market currency symbol (NT$ / ₩ / HK$ / ¥ / $) when
        # the body's other text already establishes one. Trader emits
        # bare integers like 'Stop Loss: 1950' even for TWD/KRW/etc;
        # readers expect 'NT$1,950' / '₩1,321,500'.
        #
        # Markdown-bold support: the Trader's render template emits
        # '**Stop Loss**: 25000.0' (asterisks around label). The optional
        # \*{0,2} groups around the label name handle the bold case
        # while still matching unformatted '진입가: 12345.0' equally.
        # English labels (Entry Price / Target Price / Stop Loss) added
        # because Trader emits English here even for KR / JP / TW tickers.
        #
        # Currency symbol priority:
        # 1. Ticker-derived `currency_symbol` from analyzer caller (most
        #    reliable — comes from MARKET_CONFIG via detect_market). The
        #    Trader section often has no currency marker in its own body
        #    text (MediaTek 2454.TW 2026-05-18: Trader emitted '진입가
        #    3,350' / 'Stop Loss 3,150' with zero NT$ marker), so per-
        #    section body scan misses TW/KR/JP/HK Trader output.
        # 2. Fallback: scan body for first currency prefix. 'NT$' >
        #    'HK$' > '₩' > '¥' > '$' (multi-glyph prefixes first to
        #    avoid '$' inside 'NT$' / 'HK$' matching first).
        if currency_symbol:
            sym = currency_symbol
        elif re.search(r"NT\$\d", b):
            sym = "NT$"
        elif re.search(r"HK\$\d", b):
            sym = "HK$"
        elif re.search(r"₩\s*\d|₩\d", b):
            sym = "₩"
        elif re.search(r"¥\s*\d|¥\d", b):
            sym = "¥"
        elif re.search(r"\$\s*\d|\$\d", b):
            sym = "$"
        else:
            sym = ""  # no detectable currency context — leave bare
        pattern = re.compile(
            r"(\*{0,2})"
            r"(Stop\s*Loss|손절\s*가|손절매|목표\s*가|진입\s*가|매도\s*가|"
            r"Entry\s*Price|Target\s*Price)"
            r"(\*{0,2})"
            r"(\s*[:=]?\s*)"
            r"(\d{4,})(?:\.0+)?\b"
        )
        def repl(m: re.Match) -> str:
            open_b, label, close_b, sep, num = m.groups()
            # Skip if the value already has a currency prefix (avoid
            # double-prepending on re-runs / partial polish).
            full = m.group(0)
            already_has_sym = (
                sym and f"{sep.strip()} {sym}" in full
                or any(s in full for s in ("NT$", "HK$", "₩", "¥", "$"))
            )
            num_fmt = f"{int(num):,}"
            if already_has_sym or not sym:
                return f"{open_b}{label}{close_b}{sep}{num_fmt}"
            # Trim trailing space in sep so '**Stop Loss**:  ' becomes
            # '**Stop Loss**: ' (single space before currency symbol).
            sep_clean = sep.rstrip() + " " if sep.endswith(" ") else sep
            if not sep_clean.endswith(" "):
                sep_clean = sep_clean + " "
            return f"{open_b}{label}{close_b}{sep_clean}{sym}{num_fmt}"
        return pattern.sub(repl, b)
    _step("format-bare-currency", _format_bare_currency)
    # Corporate-action HARD GUARD enforcement at the output layer.
    # Surfaced by 프로텍 053610.KS 2026-05-18: build_instrument_context
    # injected the HARD GUARD directive (감자완료 2026-05-04 hit our
    # _detect_kr_corp_action keyword scan) telling the LLM to NOT cite
    # MA/EMA/MACD/RSI/Bollinger/ATR because yfinance historical was
    # stale (current ₩26,700 vs 50 SMA ₩65,120 = −59% gap). The market
    # analyst acknowledged 감자 in prose then went ahead and wrote full
    # 이동평균선 / MACD 지표 / RSI 지표 / ATR 지표 subsections anyway.
    # Text-only HARD GUARD is ignored. Output-layer polish prepends a
    # visible banner so a reader can't miss that the technical analysis
    # is unreliable, even when the LLM defies the prompt.
    def _hard_guard_warn(b: str) -> str:
        # Universal corp-action keyword set — KR (감자/무상증자/분할),
        # JP (株式分割/併合), TW (減資/無償配股/股票分割/庫藏股), US
        # (stock split / reverse split). Detect presence in body prose;
        # analyst usually mentions the event. Universal-by-default per
        # CLAUDE.md — keywords for all 4 markets in one alternation so
        # the same banner fires regardless of which market the subject
        # belongs to.
        has_corp_action = re.search(
            r"감자(?:결정|완료)?|무상증자|주식분할|액면분할|주식병합|"
            r"株式分割|株式併合|株式無償割当|"
            r"減資|無償配股|股票分割|股票合併|庫藏股|"
            r"stock\s*split|reverse\s*split|forward\s*split",
            b, re.IGNORECASE,
        )
        if not has_corp_action:
            return b
        # Detect any technical-indicator subsection. If the LLM is
        # discussing MA/MACD/RSI/Bollinger/ATR while a corp action is
        # in flight, the analysis values are corrupt.
        has_technical = re.search(
            r"(?:^|\n)\s*(?:이동평균선|단기\s*이동평균|중기\s*이동평균|"
            r"MACD\s*지표|RSI\s*지표|ATR\s*지표|볼린저\s*밴드|Bollinger|"
            r"추세\s*지표\s*분석|모멘텀\s*지표\s*분석|변동성\s*지표\s*분석|"
            r"거래량\s*기반\s*지표)",
            b,
        )
        if not has_technical:
            return b
        # Both signals present — prepend a single banner at top of body.
        # Per-section prepend was considered but a single big banner is
        # less repetitive and harder to scroll past.
        banner = (
            "⛔ **CORPORATE ACTION HARD GUARD 위반 감지** — corp action"
            " (감자/분할/무상증자/주식병합) 진행 중인 종목인데 본 보고서가"
            " 기술 지표 (이동평균선 / MACD / RSI / 볼린저 / ATR) 를 인용함."
            " yfinance historical 시계열이 corp action 이전 가격이라 모든"
            " MA / 모멘텀 비교 수치는 stale (현재가와 50-200% 괴리 가능)."
            " 본 보고서의 기술 분석 부분은 신뢰 불가 — 결정은 펀더멘털 +"
            " corp action 이벤트 분석만 기반해주세요. 프롬프트 룰을 LLM이"
            " 무시한 케이스이며, 다음 보고 주기에서 가격 시계열이 안정화될"
            " 때까지는 기술 분석 보류가 정답입니다.\n\n"
        )
        return banner + b.lstrip()
    _step("hard-guard-warn", _hard_guard_warn)
    # RULE 1 (PERIOD LABELS) auto-warn — detect multi-dash time series
    # without period labels (FY25 / Q4 / etc). 두산 000150.KS 2026-05-18:
    #   '순이익: 758억 원 — -2,262억 원 — -3,883억 원 — -6,964억 원 — ...'
    # Eight values, zero period labels. RULE 1 forbids this but the
    # text rule keeps being skipped. We can't auto-relabel (don't know
    # which value is which FY) but we CAN append a visible warning so
    # the reader knows the LLM produced an under-labeled series.
    def _flag_unlabeled_series(b: str) -> str:
        # Match bullet lines with 4+ ' — ' separators where none of the
        # values is preceded by a 'FY'/'Q'/'TTM'/'FY25'-style label.
        # Period-label tokens we tolerate: FY{digits}, Q{digit}, Q{digit} {digits},
        # TTM, {YYYY}, {YYYY}.{MM}, '연간' / '분기'.
        period_label_re = re.compile(
            r"(?:FY\d{2,4}|Q[1-4](?:\s*\d{2,4})?|TTM|연간|분기|"
            r"\d{4}(?:[./-]\d{1,2})?|\d{4}년)"
        )
        lines = b.split("\n")
        flagged = 0
        out = []
        for line in lines:
            stripped = line.strip()
            # Bullet line with at least 4 dash separators? KR/JP analysts
            # use ' — ' or ' - ' as separator; match both.
            if (
                stripped.startswith(("•", "*", "-"))
                and stripped.count(" — ") >= 4
                and not period_label_re.search(stripped)
            ):
                out.append(line + " ⚠️(RULE 1: period labels 누락 — 값 순서 불명확)")
                flagged += 1
            else:
                out.append(line)
        return "\n".join(out)
    _step("rule1-unlabeled-series", _flag_unlabeled_series)
    # Empty markdown table headers — analyst started a table then fell
    # back to prose, leaving '| 지표 | 현재 값 | 변화 |' header + ack
    # separator '|---|---|---|' followed by no data rows. MediaTek
    # 2454.TW 2026-05-18: both News + Sentiment '요약 테이블' sections
    # had this pattern. Strip the orphan header so readers don't see a
    # broken-looking table — the narrative below it carries the data.
    def _strip_empty_tables(b: str) -> str:
        # Match: header row '| col1 | col2 | ... |\n| --- | --- | ... |'
        # NOT followed by a data row (line starts with '|').
        empty_table_re = re.compile(
            r"^\s*\|[^\n]+\|\s*\n\s*\|\s*[:\-]+\s*(?:\|\s*[:\-]+\s*)+\|\s*\n"
            r"(?!\s*\|)",  # next line is NOT a data row
            re.MULTILINE,
        )
        return empty_table_re.sub("", b)
    _step("strip-empty-tables", _strip_empty_tables)
    # Markdown table merged onto a single line (e.g. '|---|---|---| |
    # row1 | val1 |') — the LLM concatenated the separator line with
    # the first data row. MediaTek 2454.TW 2026-05-18 Market 분석:
    # '| 지표명 | ... | 해석 |\n|---|---|---| | 종가 | NT$3,260 | ...'.
    # Insert a newline between the separator and the first row so
    # markdown parsers can render the table correctly.
    def _split_inline_tables(b: str) -> str:
        # Match '|---|---|...| ' (separator) immediately followed by
        # another '|' (start of data row) on the SAME line.
        inline_re = re.compile(
            r"(\|\s*[:\-]+\s*(?:\|\s*[:\-]+\s*)+\|)\s+(\|[^\n]+\|)",
        )
        return inline_re.sub(r"\1\n\2", b)
    _step("split-inline-tables", _split_inline_tables)
    # Conservative dedup for short Korean approximation words that
    # analysts occasionally double-print before a number ("약 약 1776조"
    # — SNG 2026-05-17). Only handles a fixed allowlist; we don't do
    # a general "(\w+) \1" pass because legitimate Korean phrases
    # ("그 그 사람", "이 이 종목" etc.) would get clobbered.
    _step("ko-double-prefix",    lambda b: re.sub(
        r"\b(약|대략|혹은|또는|즉)\s+\1\b", r"\1", b,
    ))
    # News-section "요약 테이블" / "요약표" / "Summary table" repeated
    # headers — 한국전력공사 2026-05-17 emitted three of them with
    # broken markdown in between. Keep the FIRST occurrence, strip
    # subsequent ones along with the line they sit on.
    def _dedup_summary_table(b):
        pattern = re.compile(
            r"^[ \t]*요약\s*(?:테이블|표|table)[ \t]*$",
            re.IGNORECASE | re.MULTILINE,
        )
        count = 0
        def repl(_match):
            nonlocal count
            count += 1
            return _match.group(0) if count == 1 else ""
        return pattern.sub(repl, b)
    _step("dup-summary-table-header", _dedup_summary_table)
    # Post-generation numeric validators — run after all cosmetic passes
    # so they inspect the final text the user will actually see.
    if canonical:
        _step("magnitude-check",
              lambda b: _magnitude_check(b, canonical))
        _step("canonical-crosscheck",
              lambda b: _canonical_crosscheck(b, canonical))
    return body.strip()


def _clean_section(body, currency_symbol: str = "", canonical: dict | None = None) -> str:
    """Replace empty or obviously-broken agent output with a clear placeholder.

    Gemini occasionally emits a JSON error blob, raw tool_code, or no content
    at all instead of a real report. Surface that as an explicit failure marker
    so the reader knows the section is missing rather than silently dropping
    it (which used to leave the report header followed by the next section).
    Otherwise pass the body through `_polish` to remove leaked noise.

    currency_symbol: optional market currency from caller's ticker context.
    Threaded into _polish so Trader output (which often has no currency
    marker in its own body) still gets the correct ₩ / NT$ / HK$ / ¥ / $
    prefix on Stop Loss / Entry Price values. MediaTek 2454.TW 2026-05-18
    surfaced this — Trader emitted '진입가 3,350 / Stop Loss 3,150' bare
    because Fix 5's body-scan couldn't find any NT$ in the Trader's own
    section. Ticker-derived symbol bypasses that detection gap.
    """
    if not body or not body.strip():
        return _FAILURE_PLACEHOLDER
    # Defensively reject pathologically long sections. A normal analyst
    # response tops out around 20-30K chars; anything past 200K is almost
    # always raw tool-call output (news payload, OHLCV CSV, indicator
    # dump) accidentally echoed verbatim by the analyst into its 'final
    # report' instead of summarised. Fail fast — passing it through would
    # blow the Telegram message budget and produce unreadable garbage.
    if len(body) > 200_000:
        log.warning(
            "clean_section: pathological length %d chars — treating as failure",
            len(body),
        )
        return _FAILURE_PLACEHOLDER
    head = body.strip()[:500]
    if any(m in head for m in _GARBAGE_MARKERS):
        return _FAILURE_PLACEHOLDER
    polished = _polish(body, currency_symbol=currency_symbol, canonical=canonical)
    # If the polished body is mostly headers + empty placeholders ('• :: :',
    # '요약표' alone), treat it as a failed run rather than serving the husk.
    stripped = polished.strip()
    if len(stripped) < 200:
        return _FAILURE_PLACEHOLDER
    text_chars = sum(1 for c in stripped if c.isalnum())
    if text_chars < 80:
        return _FAILURE_PLACEHOLDER
    return polished


def _extract_rating(decision: str) -> str | None:
    upper = decision.upper()
    for kw in ("OVERWEIGHT", "UNDERWEIGHT", "BUY", "SELL", "HOLD"):
        if kw in upper:
            return kw.title()
    return None


def _first_lines(text: str, max_lines: int = 8) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    head = lines[:max_lines]
    return "\n".join(head) + ("\n…" if len(lines) > max_lines else "")
