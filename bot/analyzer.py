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

    # NOTE: .busy marker lifecycle is now owned by the main bot's handler
    # (refcount-based) so that a second queued request doesn't lose the
    # marker mid-flight when the first request's subprocess finishes.
    # Calling mark/clear_busy from inside analyze() would race with that.
    log.info("analyze: building TradingAgentsGraph for %s", ticker)
    ta = TradingAgentsGraph(
        debug=False,
        config=_build_config(),
        selected_analysts=_SELECTED_ANALYSTS,
        # Hooks every Gemini call into ~/.tradingagents/usage.jsonl so
        # /usage and the dashboard cost card stay accurate.
        callbacks=[UsageCallback()],
    )
    log.info("analyze: graph built — invoking propagate")
    state, decision = ta.propagate(ticker, target_date)
    log.info("analyze: propagate done — formatting output")

    # Surface the last few resolved recommendations for this ticker as a
    # short Korean header line — gives the reader an immediate sense of
    # whether the bot has been right or wrong on this name lately.
    past_outcomes = _format_past_outcomes(ta.memory_log, ticker)
    log.info("analyze: past_outcomes done — building full report")

    full = _format_full(state, decision, ticker, target_date, past_outcomes)
    log.info("analyze: full report done (%d chars) — building summary", len(full))

    summary = _format_summary(state, decision, ticker, target_date, past_outcomes)
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


def _extract_stance(body: str | None) -> str:
    """Pick the analyst's bottom-line stance from its report body.

    Looks for the LAST occurrence of any known stance keyword — analysts
    typically conclude with their recommendation, so the last hit is most
    representative. Returns an empty string when nothing matches.
    """
    if not body:
        return ""
    lower = body.lower()
    last_pos = -1
    last_label = ""
    for keyword, label in _STANCE_KEYWORDS:
        pos = lower.rfind(keyword.lower())
        if pos > last_pos:
            last_pos = pos
            last_label = label
    return last_label


def _format_summary(
    state: dict,
    decision: str,
    ticker: str,
    date_: str,
    past_outcomes: str = "",
) -> str:
    rating = _extract_rating(decision) or "N/A"
    parts = [
        f"📊 **{ticker}** ({date_})",
        "━━━━━━━━━━━━━━",
        f"🎯 최종 판정: **{rating}**",
    ]
    if past_outcomes:
        parts.append(past_outcomes)
    # Compact one-line per-analyst stance bar so the user sees who voted
    # what at a glance, before the longer per-section snippets.
    stance_chunks = []
    for key, icon, name in _SECTION_LABELS_FOR_SUMMARY:
        body = state.get(key) if isinstance(state, dict) else None
        stance = _extract_stance(body)
        if stance:
            stance_chunks.append(f"{icon} {name}: {stance}")
    if stance_chunks:
        parts.append("  ·  ".join(stance_chunks))
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


def _format_full(
    state: dict,
    decision: str,
    ticker: str,
    date_: str,
    past_outcomes: str = "",
) -> str:
    parts = [f"📋 {ticker} 전체 리포트 ({date_})\n"]
    if past_outcomes:
        parts.append(past_outcomes + "\n")
    for key, label, analyst_id in _REPORT_SECTIONS:
        if analyst_id is not None and analyst_id not in _SELECTED_ANALYSTS:
            continue  # we never ran this analyst — skip the section entirely
        body = state.get(key) if isinstance(state, dict) else None
        parts.append(f"\n## {label}\n{_clean_section(body)}")
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
_DASH_PADDING_RE = re.compile(r"(?:\s+[—\-]+\s+-){2,}\s*$", re.MULTILINE)

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
    r"(?:(\d{1,4})\s*조\s*)?"
    r"(?:(\d{1,4})\s*억\s*)?"
    r"(?:(\d{1,4})\s*만\s*)?"
    r"(\d{1,4})?"
    r"\s*달러"
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


def _drop_repeated_section(body: str) -> str:
    """If a major section header appears twice, the agent emitted its
    report twice — keep only up to the second occurrence."""
    matches = list(_DUP_HEADER_RE.finditer(body))
    if len(matches) >= 2:
        return body[: matches[1].start()].rstrip()
    nums = list(_DUP_NUMBERED_RE.finditer(body))
    if len(nums) >= 2 and nums[1].start() - nums[0].start() > 1000:
        return body[: nums[1].start()].rstrip()
    return body


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
def _polish(body: str) -> str:
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
        nonlocal body
        body = fn(body)
        log.info("polish: %s done (%d chars)", label, len(body))

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
    return body.strip()


def _clean_section(body) -> str:
    """Replace empty or obviously-broken agent output with a clear placeholder.

    Gemini occasionally emits a JSON error blob, raw tool_code, or no content
    at all instead of a real report. Surface that as an explicit failure marker
    so the reader knows the section is missing rather than silently dropping
    it (which used to leave the report header followed by the next section).
    Otherwise pass the body through `_polish` to remove leaked noise.
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
    polished = _polish(body)
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
