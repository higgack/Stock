"""TradingAgents wrapper used by the Telegram bot.

Keeps the heavy initialization out of the bot file and exposes a single
synchronous `analyze(ticker, date)` call that returns (summary, full_report).
"""

import logging
import re
import sys
from datetime import date as _date
from pathlib import Path

# Make the vendored TradingAgents package importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "TradingAgents"))

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from bot import cache as _cache

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
    config["google_thinking_level"] = "minimal"
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["output_language"] = "Korean"
    # Output caps to control cost. Analyst / manager reports (deep tier)
    # have room for full structured Markdown; debate/risk/trader nodes
    # (quick tier) only need a few paragraphs of stance text. Saves
    # roughly 30-40% on output-token spend with no measurable loss in
    # report content.
    config["deep_max_output_tokens"] = 4000
    config["quick_max_output_tokens"] = 2000
    config["data_vendors"] = {
        "core_stock_apis": "yfinance",
        "technical_indicators": "yfinance",
        "fundamental_data": "yfinance",
        "news_data": "yfinance",
    }
    return config


_BUSY_MARKER = Path.home() / ".tradingagents" / ".busy"


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

    cached = _cache.get(ticker, target_date)
    if cached is not None:
        log.info("cache hit for %s/%s", ticker, target_date)
        return cached

    _BUSY_MARKER.parent.mkdir(parents=True, exist_ok=True)
    _BUSY_MARKER.touch()
    try:
        ta = TradingAgentsGraph(
            debug=False,
            config=_build_config(),
            selected_analysts=_SELECTED_ANALYSTS,
        )
        state, decision = ta.propagate(ticker, target_date)

        full = _format_full(state, decision, ticker, target_date)
        summary = _format_summary(state, decision, ticker, target_date)
        _cache.put(ticker, target_date, summary, full)
        return summary, full
    finally:
        try:
            _BUSY_MARKER.unlink()
        except FileNotFoundError:
            pass


_SECTION_LABELS_FOR_SUMMARY = [
    ("market_report", "📈"),
    ("news_report", "📰"),
    ("fundamentals_report", "💰"),
]


def _format_summary(state: dict, decision: str, ticker: str, date_: str) -> str:
    rating = _extract_rating(decision) or "N/A"
    parts = [
        f"📊 **{ticker}** ({date_})",
        "━━━━━━━━━━━━━━",
        f"🎯 최종 판정: **{rating}**",
        "",
    ]
    # One key sentence per analyst section, so the summary tells the user
    # WHY without forcing them to expand the full report.
    for key, icon in _SECTION_LABELS_FOR_SUMMARY:
        body = state.get(key) if isinstance(state, dict) else None
        snippet = _first_meaningful_sentence(body) if body else ""
        if snippet:
            parts.append(f"{icon} {snippet}")
    parts.append("")
    parts.append(_first_lines(decision, max_lines=4))
    return "\n".join(parts)


def _first_meaningful_sentence(text: str, max_chars: int = 140) -> str:
    """Pick the first non-trivial line from a section body."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Skip headers and short title-like lines.
        if line.startswith(("#", "•", "*", "-", "|")):
            continue
        if line.endswith(":"):
            continue
        if len(line) < 30:
            continue
        if len(line) > max_chars:
            line = line[:max_chars].rstrip() + "…"
        return line
    return ""


_REPORT_SECTIONS = [
    ("market_report", "📈 시장 분석", "market"),
    ("sentiment_report", "💬 감정 분석", "social"),
    ("news_report", "📰 뉴스 분석", "news"),
    ("fundamentals_report", "💰 펀더멘털", "fundamentals"),
    ("investment_plan", "🧭 투자 계획", None),
    ("trader_investment_plan", "💼 트레이더 제안", None),
]


def _format_full(state: dict, decision: str, ticker: str, date_: str) -> str:
    parts = [f"📋 {ticker} 전체 리포트 ({date_})\n"]
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


def _polish(body: str) -> str:
    """Strip noise patterns that agents occasionally leak into their text.

    Removes per-section 'FINAL TRANSACTION PROPOSAL' lines (the rating is
    already extracted into the summary, so showing them again — sometimes
    contradicting each other — just confuses the reader), raw tool-result
    fragments like literal '\\nLink: https://...' and '### Title (source: X)'
    headers, and collapses leftover literal '\\n\\n' escape sequences and
    runs of blank lines.
    """
    body = _FINAL_PROPOSAL_RE.sub("", body)
    body = _LINK_LINE_RE.sub("", body)
    body = _TOOL_HEADER_RE.sub("", body)
    body = _ESCAPED_NEWLINES_RE.sub("\n\n", body)
    # Any remaining stray literal '\n' becomes a real newline.
    body = body.replace("\\n", "\n")
    body = _RUNAWAY_CHAR_RE.sub("", body)
    body = _drop_repeated_section(body)
    body = _LARGE_NUM_RE.sub(_abbrev_match, body)
    body = _KO_NUM_RE.sub(_ko_num_normalize, body)
    body = _LONG_DECIMAL_RE.sub(_round_long_decimal, body)
    body = _FIN_BULLET_RE.sub(_add_unit_hint, body)
    for pat, repl in _KO_LABEL_REPLACEMENTS:
        body = pat.sub(repl, body)
    body = re.sub(r"\n{3,}", "\n\n", body)
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
    head = body.strip()[:500]
    if any(m in head for m in _GARBAGE_MARKERS):
        return _FAILURE_PLACEHOLDER
    return _polish(body)


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
