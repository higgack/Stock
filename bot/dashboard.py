"""Static HTML dashboard generator for the analysis archive.

Reads ``~/.tradingagents/archive/YYYY-MM-DD/*.json`` and emits:

  ``~/.tradingagents/archive/index.html``        — date-grouped catalog
  ``~/.tradingagents/archive/<DATE>/<T>.html``   — per-analysis detail page

Phase 2 of the dashboard rollout. The pages are plain HTML with inline
CSS — no CDN, no JS framework — so Phase 3 (systemd hosting) can serve
them straight from any static file server, and Phase 4 can layer
JS-side search/filter on top without restructuring the file tree.

Called from ``bot.analyzer`` after each successful archive write. All
errors are caught and logged; dashboard generation never breaks the
analysis pipeline.
"""

from __future__ import annotations

import datetime
import html as _html
import json
import logging
import re
from collections import Counter
from pathlib import Path

from bot.archive import ARCHIVE_ROOT

log = logging.getLogger(__name__)

# ─── extraction helpers ──────────────────────────────────────────────
# The summary string format is owned by bot.analyzer._format_summary;
# these patterns mirror that output.
_RATING_RE = re.compile(r"🎯 최종 판정:\s*\*\*([^*]+?)\*\*")
_STANCE_LINE_RE = re.compile(r"^(?:📈|💬|📰|💰)[^\n]*·[^\n]*$", re.MULTILINE)
_PAST_OUTCOMES_RE = re.compile(r"^(📒\s*지난 추천[^\n]+)$", re.MULTILINE)


def _extract(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text or "")
    return m.group(1).strip() if (m and m.lastindex) else (m.group(0).strip() if m else "")


# ─── stats sources ───────────────────────────────────────────────────
_USAGE_LOG_PATH = Path.home() / ".tradingagents" / "usage.jsonl"
_MEMORY_LOG_PATH = Path.home() / ".tradingagents" / "memory" / "trading_memory.md"
_KRW_PER_USD = 1380  # mirrors usage_tracker's constant; keep in sync


# ─── issue detection ─────────────────────────────────────────────────
# Patterns that indicate something went wrong inside an otherwise-
# completed analysis. Hard failures (timeout / process exit) are tracked
# separately via usage_tracker.log_failure → usage.jsonl type:'failure'.
_FAILURE_PLACEHOLDER = (
    "_(이번 분석은 모델 응답 오류로 미완성. 다른 티커로 재시도해보세요.)_"
)
_SECTION_HEADER_RE = re.compile(r"^##\s+([^\n]+)$", re.MULTILINE)
# Markdown header (## / ### / #### ...) for section-label tracking in the
# index-page snippet search. _SECTION_HEADER_RE itself is `##`-only because
# the issues detector needs the analyst-level grouping; the index search
# walks h3+ too so '### 결론' style sub-headers also attach context.
_ANY_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")


def _index_lines_from_full_report(
    full: str, max_lines: int = 200, max_line_chars: int = 200
) -> list[dict]:
    """Walk a saved analysis's full_report markdown, return a compact
    line index for the index-page snippet search. Each entry =
    ``{"sec": <last seen section header>, "txt": <line, stripped + capped>}``.

    Empty lines + header lines themselves are excluded — the section
    header rides along as ``sec`` on subsequent body lines. Lines shorter
    than 3 chars are dropped (table separators, bullet markers alone).

    When total exceeds ``max_lines`` we take front half + back half so
    BOTH the market-analyst block (early in the report) and the
    Portfolio Manager verdict (late) stay searchable. The middle gets
    dropped silently — the search hit rate on Trader / Risk debate text
    is rarely the deciding factor, while a missing PM verdict makes the
    search look broken to the user.
    """
    lines: list[dict] = []
    current_section = ""
    for raw in (full or "").splitlines():
        s = raw.strip()
        m = _ANY_HEADER_RE.match(s)
        if m:
            # Strip leading bullet/marker noise + cap at 60 so very long
            # h3 lines don't bloat the JSON attribute.
            current_section = m.group(2).strip()[:60]
            continue
        if len(s) < 3:
            continue
        lines.append({"sec": current_section, "txt": s[:max_line_chars]})
    if len(lines) > max_lines:
        front = max_lines // 2
        back = max_lines - front
        lines = lines[:front] + lines[-back:]
    return lines
_ISSUE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"리스크 지표 계산 실패"),
     "리스크 지표 계산 실패 (도구)"),
    (re.compile(r"거시 지표 데이터를 가져오지 못했습니다"),
     "거시 지표 fetch 실패 (도구)"),
    (re.compile(r"`?get_risk_metrics`?.*?(?:오류|실패|N/A|사용할 수 없|데이터 없음|not available|unavailable)",
                re.DOTALL),
     "리스크 지표 도구 사용 안 됨 (모델)"),
    (re.compile(r"`?get_macro_context`?.*?(?:오류|실패|N/A|사용할 수 없|데이터 없음|not available|unavailable)",
                re.DOTALL),
     "거시 배경 도구 사용 안 됨 (모델)"),
    (re.compile(r"`?get_sector_relative_strength`?.*?(?:오류|실패|사용할 수 없|not available|unavailable)",
                re.DOTALL),
     "섹터 상대 강도 도구 사용 안 됨 (모델)"),
    (re.compile(r"매크로 컨텍스트.*?(?:오류|도구 오류)"),
     "매크로 컨텍스트 오류 (모델)"),
    # Generic tool-apology phrases anywhere in the body — catches the
    # GOOGL 2026-05-10 case where the market analyst opened in English
    # ("tools are not available") and the news analyst buried "죄송합니다.
    # 도구를 사용할 수 없" deep in the body. These bypass the older
    # tool-name-anchored patterns above.
    (re.compile(r"tools? (?:are|is) (?:not available|unavailable)", re.IGNORECASE),
     "도구 사용 불가 사과 (영어)"),
    (re.compile(r"Therefore,?\s*I cannot (?:provide|generate)", re.IGNORECASE),
     "도구 사용 불가 사과 (영어)"),
    (re.compile(r"도구를 사용할 수 없|도구가 유효하지 않"),
     "도구 사용 불가 사과 (한국어)"),
    (re.compile(r"API 호출 실패"),
     "API 호출 실패 사과 (모델)"),
]


# Per-report section markers — should appear EXACTLY once in a finished
# report. When the fundamentals analyst goes into a repetition loop (the
# ONTO 2026-05-10 case) it sometimes emits the trailing valuation /
# decision blocks twice, and _polish's drop-repeated pass either misses
# the duplication pattern or hits the SIGALRM step guard. The dashboard
# audits the assembled body separately so duplications still surface.
_DUP_SECTION_MARKERS = (
    "🧭 투자 계획",
    "💼 트레이더 제안",
    "✅ 최종 결정",
)


def _detect_issues(record: dict) -> list[str]:
    """Walk a single archived analysis and return a list of human-readable
    issue strings. Empty list means clean run."""
    issues: list[str] = []
    full = record.get("full_report", "") or ""
    summary = record.get("summary", "") or ""

    # Map each FAILURE_PLACEHOLDER occurrence in the body to whichever
    # '## …' section header most recently preceded it.
    headers = list(_SECTION_HEADER_RE.finditer(full))
    for ph in re.finditer(re.escape(_FAILURE_PLACEHOLDER), full):
        # Find most recent header before this placeholder.
        section = None
        for h in headers:
            if h.start() < ph.start():
                section = h.group(1).strip()
            else:
                break
        label = f"{section} 섹션 미완성" if section else "섹션 미완성"
        if label not in issues:
            issues.append(label)

    # Generic placeholder in summary too (rare — usually summary skips it)
    if _FAILURE_PLACEHOLDER in summary and not any(
        "미완성" in i for i in issues
    ):
        issues.append("요약 미완성")

    # Tool-related issues across the body
    seen_msgs: set[str] = set()
    for pat, msg in _ISSUE_PATTERNS:
        if pat.search(full) and msg not in seen_msgs:
            seen_msgs.add(msg)
            issues.append(msg)

    # Section duplication: each of the 투자 계획 / 트레이더 / 최종 결정
    # markers should appear once. If any appears twice or more, an
    # analyst (most often fundamentals) emitted its valuation / decision
    # block in a repetition loop and _polish didn't deduplicate. Surface
    # as a dashboard issue so the user notices the report quality dip.
    dup_labels = [
        marker for marker in _DUP_SECTION_MARKERS
        if full.count(marker) > 1
    ]
    if dup_labels:
        issues.append(
            "섹션 중복 — " + " · ".join(dup_labels)
            + " (분석가 반복 루프, polish 미정리)"
        )
    return issues


def _read_hard_failures(window_days: int = 365) -> list[dict]:
    """Read type:'failure' records from usage.jsonl. These are analyses
    that didn't complete (timeout / exception)."""
    if not _USAGE_LOG_PATH.exists():
        return []
    cutoff = _now_ts() - window_days * 86400
    out: list[dict] = []
    try:
        with open(_USAGE_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "failure" and rec.get("ts", 0) >= cutoff:
                    out.append(rec)
    except Exception as exc:
        log.warning("dashboard: hard-failure read failed: %s", exc)
    out.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return out


def _read_tool_failures(window_hours: int = 24) -> dict[str, int]:
    """Aggregate type:'tool_failure' records over the recent window. Returns
    {tool_name: count}, ordered by count descending. Used for the
    dashboard's tool-health card so a yfinance / alpha vantage outage
    becomes visible before it cascades into analyst failures."""
    if not _USAGE_LOG_PATH.exists():
        return {}
    cutoff = _now_ts() - window_hours * 3600
    counts: dict[str, int] = {}
    try:
        with open(_USAGE_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "tool_failure":
                    continue
                if rec.get("ts", 0) < cutoff:
                    continue
                tool = rec.get("tool") or "unknown"
                counts[tool] = counts.get(tool, 0) + 1
    except Exception as exc:
        log.warning("dashboard: tool_failure read failed: %s", exc)
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def _now_ts() -> float:
    import time
    return time.time()


def _read_usage_records() -> list[dict]:
    """Read raw llm_call records from usage.jsonl. Read-only — does NOT
    rotate the file (that's owned by usage_tracker)."""
    if not _USAGE_LOG_PATH.exists():
        return []
    out: list[dict] = []
    try:
        with open(_USAGE_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        log.warning("dashboard: usage.jsonl read failed: %s", exc)
    return out


def _read_memory_resolved() -> list[dict]:
    """Read trading_memory.md and return only resolved entries (those
    with realized return data). Pending entries get skipped."""
    info = _read_memory_full()
    return info["resolved"]


def _read_memory_full() -> dict:
    """Single pass over trading_memory.md returning both the resolved
    entries (for accuracy calc) and the pending bucket counts (so the
    UI can be transparent about why accuracy might be based on a tiny
    sample). The dashboard's accuracy card stops being misleading when
    we surface 'evaluated 1 / pending 32 / hold 12' instead of just '1/1'.
    """
    out = {
        "resolved": [],          # list of {date, ticker, rating, raw, alpha}
        "pending_directional": 0,  # pending Buy/Sell/Over/Underweight
        "pending_hold": 0,         # pending Hold (won't ever count in accuracy)
        "resolved_hold": 0,        # resolved Hold (excluded from accuracy denom)
    }
    if not _MEMORY_LOG_PATH.exists():
        return out
    try:
        text = _MEMORY_LOG_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("dashboard: memory log read failed: %s", exc)
        return out
    blocks = text.split("\n\n<!-- ENTRY_END -->\n\n")
    # Match the format used by memory.py — line-by-line within the
    # OUTCOMES section of each block, capturing 15d / 30d follow-ups.
    outcome_line_re = re.compile(
        r"^\s*(\d+)d\s*\|\s*([+-]?\d+\.?\d*%)\s*\|\s*([+-]?\d+\.?\d*%p?)\s*$",
        re.MULTILINE,
    )
    seen: set[tuple[str, str]] = set()  # de-dupe by (date, ticker)
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        tag = lines[0].strip()
        if not (tag.startswith("[") and tag.endswith("]")):
            continue
        fields = [f.strip() for f in tag[1:-1].split("|")]
        if len(fields) < 4:
            continue
        date, ticker, rating = fields[0], fields[1], fields[2]
        rating_l = rating.lower()
        directional = rating_l in ("buy", "overweight", "sell", "underweight")
        is_hold = rating_l == "hold"
        is_pending = (fields[3] == "pending") or len(fields) < 6

        key = (date, ticker)
        if key in seen:
            continue
        seen.add(key)

        if is_pending:
            if directional:
                out["pending_directional"] += 1
            elif is_hold:
                out["pending_hold"] += 1
        else:
            if is_hold:
                out["resolved_hold"] += 1
            # Parse long-horizon outcomes from the body's OUTCOMES section.
            # Iterating finditer over the joined body keeps the scan O(n)
            # per block; misformatted lines are silently dropped.
            body = "\n".join(lines[1:])
            outcomes_extra: list[dict] = []
            if "OUTCOMES:" in body:
                # Restrict the scan to the OUTCOMES sub-block so we don't
                # accidentally match e.g. '15d back-test' in REFLECTION prose.
                outcomes_idx = body.find("OUTCOMES:")
                outcomes_body = body[outcomes_idx:]
                for m in outcome_line_re.finditer(outcomes_body):
                    outcomes_extra.append({
                        "days": int(m.group(1)),
                        "raw": m.group(2),
                        "alpha": m.group(3),
                    })
                outcomes_extra.sort(key=lambda o: o["days"])
            out["resolved"].append({
                "date": date, "ticker": ticker, "rating": rating,
                "raw": fields[3], "alpha": fields[4],
                "outcomes_extra": outcomes_extra,
            })
    return out


def _parse_pct(s: str) -> float | None:
    """Parse '+5.0%' / '-3.2%' / 'n/a' → float (5.0 / -3.2 / None).
    Returns None for NaN/inf — yfinance occasionally produces NaN Close
    values for non-US ETFs, which propagates as '+nan%' into the log."""
    import math
    if not s or s == "n/a":
        return None
    try:
        v = float(s.rstrip("%").replace("+", ""))
        return None if (math.isnan(v) or math.isinf(v)) else v
    except ValueError:
        return None


def _compute_stats(records: list[dict]) -> dict:
    """Roll up archive + usage + memory into the headline numbers shown
    on the dashboard's stats panel."""
    # ── analysis counts and timing ──
    total = len(records)
    dates = [r.get("trade_date") for r in records if r.get("trade_date")]
    first_date = min(dates) if dates else None
    last_date = max(dates) if dates else None
    span_days = 0
    if first_date and last_date:
        try:
            d0 = datetime.date.fromisoformat(first_date)
            d1 = datetime.date.fromisoformat(last_date)
            span_days = (d1 - d0).days + 1
        except Exception:
            pass
    elapsed_vals = [
        float(r.get("elapsed_sec") or 0)
        for r in records
        if r.get("elapsed_sec")
    ]
    avg_elapsed = sum(elapsed_vals) / len(elapsed_vals) if elapsed_vals else 0.0
    ticker_counter = Counter(
        r["ticker"] for r in records if r.get("ticker")
    )
    top_ticker, top_count = (
        ticker_counter.most_common(1)[0] if ticker_counter else ("-", 0)
    )

    # ── cost: today (KST) and current month (KST) ──
    # Two windows the user actually thinks in. The KST date of each
    # record is computed once and reused for both buckets.
    kst = datetime.timezone(datetime.timedelta(hours=9))
    now_kst = datetime.datetime.now(kst)
    today_str = now_kst.strftime("%Y-%m-%d")
    month_prefix = now_kst.strftime("%Y-%m")

    usage = _read_usage_records()
    today_cost_usd = 0.0
    month_cost_usd = 0.0
    month_cost_by_model: dict[str, float] = {}
    # Per-subsystem breakdown (분석 / Screener / SV) so the main dashboard
    # surfaces where the total bill is coming from. Screener Pro calls
    # land in usage.jsonl with subsystem='screener'; SV calls live in
    # ~/standardview/sv_usage.jsonl (separate file, KST-date tagged).
    today_cost_by_sub_usd: dict[str, float] = {"분석": 0.0, "Screener": 0.0, "Daily Byte": 0.0, "SV": 0.0}
    month_cost_by_sub_usd: dict[str, float] = {"분석": 0.0, "Screener": 0.0, "Daily Byte": 0.0, "SV": 0.0}
    for r in usage:
        if r.get("type") != "llm_call":
            continue
        ts = r.get("ts")
        if not ts:
            continue
        rec_day = datetime.datetime.fromtimestamp(ts, kst).strftime("%Y-%m-%d")
        cost = r.get("cost_usd", 0) or 0
        _subsys = r.get("subsystem")
        sub = ("Screener" if _subsys == "screener"
               else "Daily Byte" if _subsys == "daily_byte"
               else "분석")
        if rec_day.startswith(month_prefix):
            month_cost_usd += cost
            m = r.get("model") or "unknown"
            month_cost_by_model[m] = month_cost_by_model.get(m, 0.0) + cost
            month_cost_by_sub_usd[sub] += cost
            if rec_day == today_str:
                today_cost_usd += cost
                today_cost_by_sub_usd[sub] += cost

    # Standard View cost — read ~/standardview/sv_usage.jsonl which stores
    # cost_krw directly (KST date pre-tagged). Convert KRW → USD via the
    # same 1330 rate the dashboard uses for KRW display. Failure here is
    # silent — SV may be running on a separate host without local file.
    _sv_usage_path = Path.home() / "standardview" / "sv_usage.jsonl"
    if _sv_usage_path.exists():
        try:
            import json as _j
            with open(_sv_usage_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = _j.loads(line)
                    except Exception:
                        continue
                    cost_krw_sv = rec.get("cost_krw", 0) or 0
                    if cost_krw_sv <= 0:
                        continue
                    cost_usd_sv = cost_krw_sv / 1330.0
                    rec_day_sv = rec.get("date") or ""
                    if rec_day_sv.startswith(month_prefix):
                        month_cost_usd += cost_usd_sv
                        month_cost_by_sub_usd["SV"] += cost_usd_sv
                        # Roll SV into the gemini-2.5-flash bucket so the
                        # by-model breakdown stays accurate (SV uses flash).
                        month_cost_by_model["gemini-2.5-flash"] = (
                            month_cost_by_model.get("gemini-2.5-flash", 0.0)
                            + cost_usd_sv
                        )
                        if rec_day_sv == today_str:
                            today_cost_usd += cost_usd_sv
                            today_cost_by_sub_usd["SV"] += cost_usd_sv
        except Exception as exc:
            log.warning("dashboard: SV usage read failed: %s", exc)

    # ── recommendation accuracy from memory log ──
    # Accuracy criterion: ALPHA (raw − sector ETF benchmark), not raw.
    # Two reasons:
    #  (1) Consistency with the per-card outcome display (_render_outcome_html
    #      below) which has been alpha-based all along. The two surfaces
    #      disagreed under the previous raw-based logic — same recommendation
    #      could show '✓' on its card while counting as 'miss' in the headline
    #      accuracy denominator (e.g. Buy + raw +5% / bench SPY +8% → alpha
    #      −3%p → card miss, stat correct).
    #  (2) Alpha is the more meaningful "did the bot pick well?" signal.
    #      Raw-only would mark every Buy 'correct' in a +10% market regardless
    #      of stock-picking skill; alpha controls for the sector tide.
    mem = _read_memory_full()
    resolved = mem["resolved"]
    correct = 0
    evaluated = 0
    returns: list[float] = []
    alphas: list[float] = []
    for e in resolved:
        ret = _parse_pct(e.get("raw", ""))
        alpha = _parse_pct(e.get("alpha", ""))
        if ret is not None:
            returns.append(ret)
        if alpha is not None:
            alphas.append(alpha)
        rating = (e.get("rating") or "").lower()
        # Alpha is the accuracy criterion. If alpha couldn't be computed
        # (benchmark fetch failed), the entry doesn't count toward accuracy —
        # same handling we already use for raw when raw is None.
        if alpha is None:
            continue
        if rating in ("buy", "overweight"):
            evaluated += 1
            if alpha > 0:
                correct += 1
        elif rating in ("sell", "underweight"):
            evaluated += 1
            if alpha < 0:
                correct += 1
        # 'hold' is excluded from the accuracy denominator (no clean
        # directional bet) but still rolls into the avg_return / avg_alpha
        # totals — the headline numbers cover Hold + directional picks alike.
    accuracy = correct / evaluated if evaluated else None
    avg_return = sum(returns) / len(returns) if returns else None
    avg_alpha = sum(alphas) / len(alphas) if alphas else None

    return {
        "total": total,
        "first_date": first_date,
        "last_date": last_date,
        "span_days": span_days,
        "avg_elapsed_sec": avg_elapsed,
        "top_ticker": top_ticker,
        "top_count": top_count,
        "ticker_distinct": len(ticker_counter),
        "today_cost_usd": today_cost_usd,
        "month_cost_usd": month_cost_usd,
        "month_cost_by_model": month_cost_by_model,
        "today_cost_by_sub_usd": today_cost_by_sub_usd,
        "month_cost_by_sub_usd": month_cost_by_sub_usd,
        "today_label": now_kst.strftime("%-m월 %-d일"),
        "month_label": now_kst.strftime("%Y년 %-m월"),
        "tool_failures_24h": _read_tool_failures(window_hours=24),
        "evaluated": evaluated,
        "correct": correct,
        "accuracy": accuracy,
        "avg_return": avg_return,
        "avg_alpha": avg_alpha,
        "resolved_count": len(resolved),
        "pending_directional": mem["pending_directional"],
        "pending_hold": mem["pending_hold"],
        "resolved_hold": mem["resolved_hold"],
    }


def _format_seconds(sec: float) -> str:
    sec = int(sec)
    if sec >= 60:
        return f"{sec // 60}분 {sec % 60}초"
    return f"{sec}초"


def _krw(usd: float) -> str:
    # Space between ₩ and the digits: in large bold weights the Won
    # glyph's two horizontal strokes visually merge with the following
    # digit and look like a strikethrough. The space fixes that.
    return f"₩ {int(round(usd * _KRW_PER_USD)):,}"


def _stat_card(label: str, value: str, sub: str = "") -> str:
    if sub:
        # Newlines in `sub` become explicit <br> so multi-line subs
        # (e.g. accuracy card with a footnote) render correctly while
        # the rest of the content stays HTML-escaped.
        sub_escaped = _html.escape(sub).replace("\n", "<br>")
        sub_html = f'<div class="stat-sub">{sub_escaped}</div>'
    else:
        sub_html = ""
    return f"""
    <div class="stat-card">
      <div class="stat-label">{label}</div>
      <div class="stat-value">{value}</div>
      {sub_html}
    </div>
    """


def _render_stats_panel(stats: dict) -> str:
    if stats["total"] == 0:
        return ""

    # Card 1: 총 분석
    span_sub = ""
    if stats["first_date"] and stats["last_date"]:
        if stats["first_date"] == stats["last_date"]:
            span_sub = f"{stats['first_date']} ({stats['span_days']}일)"
        else:
            span_sub = (
                f"{stats['first_date']} ~ {stats['last_date']} "
                f"({stats['span_days']}일)"
            )
    card_total = _stat_card(
        "📊 총 분석", f"{stats['total']}건",
        span_sub + f" · {stats['ticker_distinct']}개 종목" if span_sub else "",
    )

    # Card 2: 비용 (오늘 / 이번 달)
    cost_label_parts = []
    for model in ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"):
        usd = stats["month_cost_by_model"].get(model, 0)
        if usd > 0:
            short = model.replace("gemini-2.5-", "")
            cost_label_parts.append(f"{short} {_krw(usd)}")
    cost_value = f"{_krw(stats['today_cost_usd'])} / {_krw(stats['month_cost_usd'])}"
    cost_sub_parts = [f"{stats['today_label']} / {stats['month_label']}"]
    if cost_label_parts:
        cost_sub_parts.append(" / ".join(cost_label_parts))
    # Per-subsystem breakdown (분석 / Screener / SV). Surface only buckets
    # with non-zero this-month cost — keeps the sub-label compact.
    sub_parts: list[str] = []
    for key, label in [("분석", "분석"), ("Screener", "screener"), ("Daily Byte", "Daily Byte"), ("SV", "SV")]:
        m_usd = stats["month_cost_by_sub_usd"].get(key, 0) or 0
        if m_usd > 0:
            sub_parts.append(f"{label} {_krw(m_usd)}")
    if sub_parts:
        cost_sub_parts.append("월 합산: " + " · ".join(sub_parts))
    # Surface upstream tool health on the same card. A persistent
    # yfinance / alpha vantage outage shows up here BEFORE it cascades
    # into analyst failures on the errors page, so the operator can
    # diagnose root cause without staring at journalctl.
    tool_fails = stats.get("tool_failures_24h") or {}
    if tool_fails:
        top = list(tool_fails.items())[:3]
        short = {
            "get_macro_context": "macro",
            "get_risk_metrics": "risk",
            "get_sector_relative_strength": "sector",
        }
        line = "⚠️ 24h 도구 실패 — " + ", ".join(
            f"{short.get(t, t)} ×{n}" for t, n in top
        )
        cost_sub_parts.append(line)
    card_cost = _stat_card(
        "💰 비용 (오늘 / 이번 달)", cost_value, "\n".join(cost_sub_parts),
    )

    # Card 3: 평균 시간
    top_part = (
        f"가장 많이 분석: {stats['top_ticker']} × {stats['top_count']}"
        if stats["top_count"] > 0 else ""
    )
    card_time = _stat_card(
        "⏱ 평균 분석 시간",
        _format_seconds(stats["avg_elapsed_sec"]),
        top_part,
    )

    # Card 4: 추천 정확도
    # Surface the denominator transparency so a 1/1 = 100% case doesn't
    # read as 'the bot has been right every time'. Show pending and
    # Hold-excluded counts alongside, plus a footnote explaining why
    # most analyses haven't matured into the score yet.
    pending_dir = stats.get("pending_directional", 0)
    pending_hold = stats.get("pending_hold", 0)
    resolved_hold = stats.get("resolved_hold", 0)
    hold_total = pending_hold + resolved_hold

    if stats["accuracy"] is not None:
        acc_pct = stats["accuracy"] * 100
        acc_value = f"{acc_pct:.0f}% ({stats['correct']}/{stats['evaluated']})"
    else:
        acc_value = "—"

    sub_parts = []
    sub_parts.append(f"평가 {stats['evaluated']}건")
    if pending_dir:
        sub_parts.append(f"미해소 {pending_dir}건")
    if hold_total:
        # Clarify that Hold's exclusion is *accuracy-denominator-only* — Hold
        # picks DO contribute to 평균 수익 / 알파. Previous label '(분모
        # 제외)' alone misled readers into 'Hold is excluded from every stat'.
        sub_parts.append(f"Hold {hold_total}건 (정확도 분모만 제외)")
    if stats["avg_return"] is not None:
        sub_parts.append(f"평균 수익 {stats['avg_return']:+.2f}%")
    if stats["avg_alpha"] is not None:
        # Rename '벤치 X%p' → '알파 X%p (vs 섹터 ETF)' — '벤치' alone reads
        # ambiguously as 'benchmark dropped X%p' instead of the intended
        # 'bot vs benchmark alpha = X%p' (negative = underperformance).
        sub_parts.append(f"알파 {stats['avg_alpha']:+.2f}%p (vs 섹터 ETF)")

    note = (
        "※ 정확도는 알파(raw 수익 − 섹터 ETF) 기준 · 분석 후 5거래일 경과 시"
        " 자동 평가 (백그라운드 12시간마다)"
    )
    acc_sub = " · ".join(sub_parts) + "\n" + note
    card_acc = _stat_card("🎯 추천 정확도", acc_value, acc_sub)

    return f"""
    <section class="stats-grid">
      {card_total}{card_cost}{card_time}{card_acc}
    </section>
    """


# ─── date / rating utilities ─────────────────────────────────────────
_DAY_OF_WEEK = ["월", "화", "수", "목", "금", "토", "일"]
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _ticker_display_name(ticker: str) -> str | None:
    """Return the human-readable company name for the ticker, or None.

    Resolves: KR → DART corp_name, JP → yfinance longName, TW →
    yfinance longName, US → None (US tickers are usually recognizable
    enough that the bare symbol works). Used by card / detail / search
    rendering so users see '삼성전자' / 'Toyota Motor Corporation' /
    'Taiwan Semiconductor Manufacturing Company Limited' instead of
    '005930.KS' / '7203.T' / '2330.TW' in a mixed-market card list.
    Falls back to None on any lookup miss (KR ETFs not in DART, JP/TW
    longName missing, etc.) and the caller renders the bare ticker."""
    try:
        from bot.market import detect_market
        m = detect_market(ticker)
        if m == "KR":
            from bot.dart_client import get_dart
            code = (ticker or "").upper().split(".")[0]
            return get_dart().stock_code_to_name(code)
        if m in ("JP", "TW"):
            # JP / TW both rely on yfinance longName — for JP it's
            # typically English ('Toyota Motor Corporation') or 漢字 +
            # English mix; for TW it's typically English corporate name
            # ('Taiwan Semiconductor Manufacturing Company Limited') with
            # some entries returning 繁體中文. Either is OK as a display
            # prefix — anything beats a bare '7203.T' / '2330.TW'.
            from tradingagents.agents.utils.agent_utils import _instrument_info
            info = _instrument_info(ticker) or {}
            name = info.get("longName") or info.get("shortName")
            if name and isinstance(name, str) and name.upper() != (ticker or "").upper():
                return name
            return None
    except Exception:
        pass
    return None


# Backwards-compat alias — the helper was KR-only originally; existing
# call sites still use _ticker_kr_name. Same callable, more general scope.
_ticker_kr_name = _ticker_display_name


def _format_date_kr(date_str: str) -> str:
    m = _DATE_RE.match(date_str)
    if not m:
        return date_str
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return f"{date_str} ({_DAY_OF_WEEK[d.weekday()]})"
    except Exception:
        return date_str


_RATING_BADGE = {
    "Buy":         ("매수",   "#16a34a"),
    "Overweight":  ("비중확대", "#22c55e"),
    "Hold":        ("보유",   "#737373"),
    "Underweight": ("비중축소", "#f97316"),
    "Sell":        ("매도",   "#dc2626"),
}


def _badge_html(rating: str) -> str:
    label, color = _RATING_BADGE.get(rating, (rating or "?", "#737373"))
    return f'<span class="badge" style="background:{color}">{_html.escape(label)}</span>'


# ─── markdown → HTML (minimal) ───────────────────────────────────────
# Telegram bot uses its own polish for Telegram HTML; the dashboard
# needs plain browser HTML. Keep it small: escape, then bold + headers.
_BOLD_RE = re.compile(r"\*\*([^\*\n]+?)\*\*")
_HEADER_RE = re.compile(r"(?m)^(#{1,4})\s+(.+)$")


def _md_to_html(text: str) -> str:
    if not text:
        return ""
    out = _html.escape(text)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)

    def _h(m: re.Match) -> str:
        level = min(len(m.group(1)) + 2, 6)
        return f"<h{level}>{m.group(2)}</h{level}>"

    out = _HEADER_RE.sub(_h, out)
    # `<pre>` preserves newlines + indentation. Wrap with white-space:pre-wrap
    # via the .report class so long lines still break.
    return f'<pre class="report">{out}</pre>'


# ─── archive scan ────────────────────────────────────────────────────
def _load_all() -> list[dict]:
    out: list[dict] = []
    if not ARCHIVE_ROOT.exists():
        return out
    for day_dir in ARCHIVE_ROOT.iterdir():
        if not day_dir.is_dir() or not _DATE_RE.match(day_dir.name):
            continue
        for json_path in day_dir.glob("*.json"):
            try:
                rec = json.loads(json_path.read_text(encoding="utf-8"))
                out.append(rec)
            except Exception as exc:
                log.warning("dashboard: skip unreadable %s: %s", json_path, exc)
    return out


# ─── HTML rendering ──────────────────────────────────────────────────
# Light theme only. The earlier dark-mode media query rendered as a
# near-black background on phones whose OS was in dark mode, which the
# user found unreadable. Dropping the override means the page always
# uses the light palette regardless of system preference.
# Inline theme script — runs synchronously in <head> before <style>
# applies, so the dashboard never flashes the wrong theme on load.
# 19:00–07:00 KST → dark; rest → light. Re-checks every 60s so a
# tab left open across the boundary flips automatically.
_THEME_JS = """
(function() {
  function apply() {
    var h = parseInt(new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Seoul', hour: 'numeric', hour12: false
    }).format(new Date()), 10) % 24;
    var dark = (h >= 19 || h < 7);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  }
  apply();
  setInterval(apply, 60000);
})();
"""


# Inline 태극기 SVG — 14×10 px. 🇰🇷 regional-indicator pair 는 일부 OS
# (특히 Linux Chromium 기본 폰트) 에서 'KR' 글자로 fallback 렌더되어
# 사용자가 "국기처럼" 인식 못 함. 인라인 SVG 로 환경 무관 보장. 4 괘
# 는 시각적 노이즈가 되어 생략 — 단색 흰 배경 + 청홍 태극 원으로 인식
# 충분 (14px 폭에서 4괘는 어차피 잡티). vertical-align:middle 로 텍스트
# baseline 정렬.
_KR_FLAG_SVG = (
    '<svg width="16" height="11" viewBox="0 0 60 40" '
    'style="vertical-align:-1px;display:inline-block" aria-label="한국 국기">'
    '<rect width="60" height="40" fill="#fff" stroke="#d0d0d0" stroke-width="0.5"/>'
    '<circle cx="30" cy="20" r="9" fill="#cd2e3a"/>'
    '<path d="M21 20 A9 4.5 0 0 1 39 20 A4.5 4.5 0 0 0 30 20 '
    'A4.5 4.5 0 0 1 21 20 Z" fill="#0047a0"/>'
    '</svg>'
)


_BASE_CSS = """
:root {
  --fg: #1f2937; --fg-soft: #6b7280; --bg: #f8fafc; --card: #ffffff;
  --border: #e5e7eb; --accent: #0ea5e9;
}
:root[data-theme="dark"] {
  --fg: #e5e7eb; --fg-soft: #9ca3af; --bg: #111827; --card: #1f2937;
  --border: #374151; --accent: #38bdf8;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
    "Pretendard", "Helvetica Neue", "Segoe UI", "Apple Color Emoji",
    "Segoe UI Emoji", "Noto Color Emoji", "Twemoji Mozilla", sans-serif;
  color: var(--fg); background: var(--bg); line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 980px; margin: 0 auto; padding: 24px 16px 64px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--fg-soft); font-size: 13px; margin: 0 0 24px; }
.badge {
  font-size: 11px; padding: 2px 9px; border-radius: 999px;
  color: white; font-weight: 600; white-space: nowrap;
}
"""

_INDEX_CSS = _BASE_CSS + """
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px; margin: 16px 0 8px;
}
.stat-card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px;
}
.stat-label {
  font-size: 12px; color: var(--fg-soft); margin-bottom: 6px;
  font-weight: 500;
}
.stat-value {
  font-size: 22px; font-weight: 700; color: var(--fg);
  line-height: 1.2;
}
.stat-sub {
  font-size: 11px; color: var(--fg-soft); margin-top: 6px;
  word-break: keep-all;
}
.search-bar {
  display: flex; align-items: center; gap: 8px; margin: 16px 0 24px;
  padding: 4px;
}
.search-bar input {
  flex: 1; padding: 10px 14px; font-size: 15px; color: var(--fg);
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  outline: none; transition: border-color 0.12s;
}
.search-bar input:focus { border-color: var(--accent); }
.search-bar button {
  padding: 10px 22px; font-size: 14px; font-weight: 500;
  cursor: pointer; white-space: nowrap;
  background: #22c55e; color: #ffffff;
  border: 1px solid transparent; border-radius: 9999px;
  transition: background 0.12s ease, transform 0.06s ease;
}
.search-bar button:hover { background: #16a34a; }
.search-bar button:active { transform: scale(0.97); }
:root[data-theme="dark"] .search-bar button { background: #16a34a; }
:root[data-theme="dark"] .search-bar button:hover { background: #15803d; }
.status-line {
  color: var(--fg-soft); font-size: 13px; margin: 0 4px 16px;
}
.empty-search {
  color: var(--fg-soft); font-size: 14px; padding: 32px 0;
  text-align: center; display: none;
}
details.day { margin-bottom: 18px; }
summary.day-head {
  font-size: 16px; font-weight: 600; padding: 10px 4px; cursor: pointer;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
  list-style: none;
}
summary.day-head::-webkit-details-marker { display: none; }
summary.day-head::before {
  content: "▶"; display: inline-block; margin-right: 8px;
  transition: transform 0.15s; font-size: 11px; color: var(--fg-soft);
}
details[open] summary.day-head::before { transform: rotate(90deg); }
summary.day-head .count {
  font-size: 12px; color: var(--fg-soft); font-weight: 400;
}
.cards { display: grid; grid-template-columns: 1fr; gap: 8px; padding: 12px 0; }
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 14px; transition: border-color 0.12s;
}
.card:hover { border-color: var(--accent); }
.card-row {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}
.ticker {
  font-weight: 700; font-size: 15px; color: var(--fg);
  text-decoration: none; min-width: 70px;
}
.ticker:hover { color: var(--accent); text-decoration: none; }
.stance {
  color: var(--fg-soft); font-size: 13px; flex: 1; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.time { color: var(--fg-soft); font-size: 12px; min-width: 38px; text-align: right; }
.del-btn {
  background: none; border: none; cursor: pointer; padding: 4px 6px;
  color: #f87171; font-size: 14px; line-height: 1;
  transition: color 0.15s;
}
.del-btn:hover { color: #ef4444; }
:root[data-theme="dark"] .del-btn { color: #f87171; }
:root[data-theme="dark"] .del-btn:hover { color: #fca5a5; }
.past { color: var(--fg-soft); font-size: 12px; margin-top: 6px; }
.outcome { font-size: 12px; margin-top: 4px; color: var(--fg-soft); }
.outcome.hit { color: #10b981; }       /* directional call matched alpha */
.outcome.miss { color: #ef4444; }      /* directional call missed */
:root[data-theme="dark"] .outcome.hit { color: #34d399; }
:root[data-theme="dark"] .outcome.miss { color: #f87171; }
.orphan-day { opacity: 0.85; margin-top: 12px; }
.orphan-list { padding: 8px 12px; }
.orphan-row {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 6px 0; border-bottom: 1px solid var(--border);
}
.orphan-row:last-child { border-bottom: none; }
.orphan-meta { color: var(--fg-soft); font-size: 12px; }
.orphan-outcome { margin-top: 0; }
.empty {
  color: var(--fg-soft); font-size: 14px; padding: 32px 0; text-align: center;
}
.archive-footer {
  margin-top: 32px; padding-top: 16px;
  border-top: 1px solid var(--border);
  text-align: center; color: var(--fg-soft); font-size: 12px;
}
/* Snippet-highlight search panel (mirrors Bottleneck Screener pattern).
   Each snippet is an <a> wrapping section label + highlighted line; click
   navigates to the per-analysis detail page with #mark= so the detail
   page JS can scroll-to + highlight the source paragraph. */
.snippets {
  display: flex; flex-direction: column; gap: 8px;
  margin: 0 0 24px;
}
.snippet {
  background: var(--card); border: 1px solid var(--border);
  border-left: 3px solid var(--accent); border-radius: 8px;
  padding: 10px 14px; text-decoration: none; color: inherit;
  transition: background 0.1s, border-color 0.1s;
  display: block;
}
.snippet:hover {
  background: rgba(14, 165, 233, 0.06);
  border-left-color: #38bdf8;
}
.snippet-meta {
  display: flex; gap: 10px; font-size: 11px;
  color: var(--fg-soft); margin-bottom: 4px; align-items: center;
  flex-wrap: wrap;
}
.snippet-sec {
  background: rgba(14, 165, 233, 0.12); color: var(--accent);
  padding: 2px 8px; border-radius: 4px; font-weight: 600;
  white-space: nowrap;
}
.snippet-card {
  font-family: 'IBM Plex Mono', 'JetBrains Mono', monospace;
  word-break: break-all;
}
.snippet-text {
  color: var(--fg); font-size: 13px; line-height: 1.55;
  white-space: pre-wrap; word-break: break-word;
}
mark {
  background: rgba(245, 158, 11, 0.35); color: inherit;
  padding: 1px 3px; border-radius: 3px; font-weight: 600;
}
"""


_INDEX_JS = """
(function() {
  // Snippet-highlight search across BOTH card metadata (ticker / 한국명 /
  // stance / 평가 등) AND analyst body content (full_report indexed at
  // render time). Matches '변압기' / 'GLP-1' etc that live deep inside an
  // analysis surface as clickable snippets; clicking navigates to the
  // detail page with a #mark= hash so the detail-page JS scrolls to +
  // highlights the same line. Empty query restores the default card
  // list (full archive view); same UX as the Bottleneck Screener page.
  const searchEl = document.getElementById('search');
  const clearBtn = document.getElementById('clear-btn');
  const statusEl = document.getElementById('status');
  const emptyEl = document.getElementById('empty-search');
  const snp = document.getElementById('snippets');
  const cards = Array.from(document.querySelectorAll('.card'));
  const days = Array.from(document.querySelectorAll('details.day'));
  const total = cards.length;
  const MAX_SNIPPETS = 80;

  // Parse each card's body line index once. Synthetic 'metadata' line
  // adds the visible card-row text (ticker chip + stance + rating)
  // so a plain ticker query like 'NVDA' still surfaces the card as
  // a snippet rather than 0 matches when the body section labels
  // don't contain 'NVDA' literally.
  const cardData = cards.map(function(c) {
    let lines = [];
    try { lines = JSON.parse(c.dataset.lines || '[]'); } catch (e) {}
    const tk = (c.dataset.ticker || '').trim();
    const nm = (c.dataset.name || '').trim();
    const rowEl = c.querySelector('.card-row');
    const rowTxt = rowEl ? rowEl.textContent.replace(/\\s+/g, ' ').trim() : '';
    if (tk || nm || rowTxt) {
      lines.unshift({sec: '카드', txt: ((nm ? nm + ' · ' : '') + tk + ' · ' + rowTxt).trim()});
    }
    return {card: c, lines: lines};
  });

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function(ch) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch];
    });
  }
  function escapeReg(s) {
    return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
  }
  function highlight(text, q) {
    if (!q) return escapeHtml(text);
    const re = new RegExp(escapeReg(q), 'gi');
    const safe = escapeHtml(text);
    let out = '', last = 0, m;
    while ((m = re.exec(safe)) !== null) {
      out += safe.slice(last, m.index);
      out += '<mark>' + safe.slice(m.index, m.index + m[0].length) + '</mark>';
      last = m.index + m[0].length;
      if (m.index === re.lastIndex) re.lastIndex++;
    }
    out += safe.slice(last);
    return out;
  }

  function showCardsMode() {
    snp.style.display = 'none';
    snp.innerHTML = '';
    emptyEl.style.display = 'none';
    for (const c of cards) c.style.display = '';
    for (const d of days) {
      d.style.display = '';
      d.open = true;
    }
    statusEl.textContent = '총 ' + total + '건의 분석 기록';
  }

  function showSnippetsMode(q) {
    // Hide cards + day groups; snippet list becomes primary view.
    for (const c of cards) c.style.display = 'none';
    for (const d of days) d.style.display = 'none';

    const ql = q.toLowerCase();
    const hits = [];
    for (const cd of cardData) {
      for (const ln of cd.lines) {
        if ((ln.txt || '').toLowerCase().indexOf(ql) >= 0) {
          hits.push({card: cd.card, sec: ln.sec, txt: ln.txt});
          if (hits.length >= MAX_SNIPPETS) break;
        }
      }
      if (hits.length >= MAX_SNIPPETS) break;
    }

    if (hits.length === 0) {
      snp.style.display = 'none';
      emptyEl.style.display = 'block';
      statusEl.textContent = '0건 매칭 (검색: "' + q + '")';
      return;
    }
    emptyEl.style.display = 'none';

    const cardHitCounts = new Map();
    const parts = [];
    for (const h of hits) {
      const cid = h.card.id;
      cardHitCounts.set(cid, (cardHitCounts.get(cid) || 0) + 1);
      const ticker = h.card.dataset.ticker || '';
      const name = h.card.dataset.name || '';
      const date = h.card.dataset.date || '';
      const href = h.card.dataset.href || '#';
      const cardLabel = (name ? name + ' / ' : '') + ticker;
      const sec = h.sec || '본문';
      parts.push(
        '<a class="snippet" href="' + href +
          '#mark=' + encodeURIComponent(q) +
          '" data-target="' + cid + '">' +
          '<div class="snippet-meta">' +
            '<span class="snippet-sec">' + escapeHtml(sec) + '</span>' +
            '<span class="snippet-card">' + escapeHtml(cardLabel) +
            ' · ' + escapeHtml(date) + '</span>' +
          '</div>' +
          '<div class="snippet-text">' + highlight(h.txt, q) + '</div>' +
        '</a>'
      );
    }
    snp.innerHTML = parts.join('');
    snp.style.display = 'flex';
    const uniq = cardHitCounts.size;
    const cap = hits.length >= MAX_SNIPPETS ? ' (상위 ' + MAX_SNIPPETS + '건 표시)' : '';
    statusEl.textContent = hits.length + '개 라인 · ' + uniq + '개 분석 매칭' + cap +
                            ' (검색: "' + q + '")';
  }

  function applyFilter() {
    const raw = (searchEl.value || '').trim();
    if (!raw) { showCardsMode(); return; }
    showSnippetsMode(raw);
  }

  function syncFromHash() {
    const m = (location.hash || '').match(/^#ticker=([A-Za-z0-9.]+)/);
    if (m) searchEl.value = m[1].toUpperCase();
    applyFilter();
  }

  searchEl.addEventListener('input', applyFilter);
  clearBtn.addEventListener('click', function() {
    searchEl.value = '';
    if (location.hash) history.replaceState(null, '', location.pathname);
    applyFilter();
    searchEl.focus();
  });
  window.addEventListener('hashchange', syncFromHash);
  syncFromHash();

  // Card deletion: POST /api/delete with {date, ticker}, then remove the
  // card from the DOM on success. We don't location.reload() because
  // that would kick the user back to the top of the page; the
  // regenerate_index() call on the server side has already rewritten
  // index.html so a manual refresh later picks up everything.
  document.querySelectorAll('.del-btn').forEach(function(btn) {
    btn.addEventListener('click', function(ev) {
      ev.stopPropagation();
      ev.preventDefault();
      const card = btn.closest('.card');
      if (!card) return;
      const date = card.dataset.date;
      const ticker = card.dataset.ticker;
      if (!date || !ticker) return;
      if (!confirm('📊 ' + ticker + ' (' + date + ') 분석 기록을 삭제할까요?')) return;
      btn.disabled = true;
      btn.textContent = '⏳';
      fetch('api/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({date: date, ticker: ticker})
      }).then(function(r) {
        return r.json().then(function(d) { return {status: r.status, body: d}; });
      }).then(function(res) {
        if (res.status === 200 && res.body && res.body.ok) {
          card.style.transition = 'opacity 0.2s';
          card.style.opacity = '0';
          setTimeout(function() { card.remove(); }, 200);
        } else {
          alert('삭제 실패: ' + (res.body && res.body.error || res.status));
          btn.disabled = false;
          btn.textContent = '🗑️';
        }
      }).catch(function(err) {
        alert('삭제 실패: ' + err);
        btn.disabled = false;
        btn.textContent = '🗑️';
      });
    });
  });
})();
"""


_ERRORS_CSS = _BASE_CSS + """
.section-head {
  font-size: 16px; font-weight: 600; padding: 10px 4px;
  border-bottom: 1px solid var(--border);
  margin: 24px 0 12px;
}
.issue-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 14px; margin-bottom: 8px;
}
.issue-head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 6px;
}
.issue-head .ticker {
  font-weight: 700; font-size: 15px; color: var(--fg); text-decoration: none;
}
.issue-head .ticker:hover { color: var(--accent); }
.issue-head .when { color: var(--fg-soft); font-size: 12px; margin-left: auto; }
.issue-list { color: var(--fg); font-size: 13px; line-height: 1.6; }
.issue-list .item { color: #f97316; }
.fail-row {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 14px; margin-bottom: 6px;
}
.fail-row .ticker { font-weight: 700; }
.fail-row .reason { color: #dc2626; flex: 1; min-width: 0; }
.fail-row .when { color: var(--fg-soft); font-size: 12px; }
.empty {
  color: var(--fg-soft); font-size: 14px; padding: 24px 0; text-align: center;
}
.back { color: var(--fg-soft); font-size: 13px; }
.back:hover { color: var(--accent); }
"""


def _render_errors_page(
    records: list[dict],
    hard_failures: list[dict],
) -> str:
    """Build errors.html — a chronological catalog of analyses that
    completed with placeholder/error sections (soft issues only).

    Hard failures (timeout / exception) are deliberately NOT shown here:
    the user gets an immediate Telegram error message at the time of the
    failure, so re-listing them on the dashboard is redundant noise.
    The records still live in usage.jsonl for /usage and tool-failure
    aggregation; we just don't surface them on this catalog page.
    """
    _ = hard_failures  # accepted for API compatibility; intentionally unused
    soft: list[tuple[dict, list[str]]] = []
    for r in records:
        issues = _detect_issues(r)
        if issues:
            soft.append((r, issues))
    # Newest first
    soft.sort(
        key=lambda pair: (
            pair[0].get("trade_date", ""), pair[0].get("analyzed_at", "")
        ),
        reverse=True,
    )

    hard_section = ""

    # ── soft issues, grouped by date ──
    if soft:
        by_date: dict[str, list[tuple[dict, list[str]]]] = {}
        for r, issues in soft:
            by_date.setdefault(r["trade_date"], []).append((r, issues))

        soft_groups = []
        for date in sorted(by_date.keys(), reverse=True):
            day_items = by_date[date]
            cards_html = []
            for r, issues in day_items:
                ticker = r.get("ticker", "?")
                analyzed_at = r.get("analyzed_at") or ""
                time_str = analyzed_at[11:16] if len(analyzed_at) >= 16 else ""
                href = f"./{date}/{_html.escape(ticker)}.html"
                rating = _extract(_RATING_RE, r.get("summary", "")) or "?"
                # KR tickers show their Korean corp name on this catalog
                # page too — the user reported the errors panel was the
                # last place still rendering bare numeric tickers
                # (019680/014680/039030 in their 2026-05-17 view).
                kr_name = _ticker_kr_name(ticker)
                label = kr_name or ticker
                items_html = "".join(
                    f'<div class="item">⚠️ {_html.escape(i)}</div>'
                    for i in issues
                )
                cards_html.append(f"""
                <div class="issue-card">
                  <div class="issue-head">
                    <a class="ticker" href="{href}">📊 {_html.escape(label)}</a>
                    {_badge_html(rating)}
                    <span class="when">{_html.escape(time_str)}</span>
                  </div>
                  <div class="issue-list">{items_html}</div>
                </div>
                """)
            soft_groups.append(f"""
            <div class="section-head">
              📅 {_format_date_kr(date)} — {len(day_items)}건 부분 미완성
            </div>
            {"".join(cards_html)}
            """)
        soft_section = "".join(soft_groups)
    else:
        soft_section = '<div class="empty">부분 미완성 케이스가 없습니다.</div>'

    body_empty = not soft
    if body_empty:
        body = '<div class="empty">부분 미완성 케이스가 없습니다. 🎉</div>'
    else:
        body = soft_section

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>🚨 NOAH 오류 / 미완성 기록</title>
<script>{_THEME_JS}</script>
<style>{_ERRORS_CSS}</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="./index.html">← 아카이브로 돌아가기</a>
  <h1 style="margin-top:8px">🚨 오류 / 미완성 분석 기록</h1>
  <p class="sub">분석가 부분 미완성 (도구 실패·섹션 placeholder·사과형 응답)만 추적합니다. 하드 실패는 텔레그램에서 즉시 알림됨.</p>
  {body}
</div>
</body>
</html>
"""


def _count_total_issues(records: list[dict], hard_failures: list[dict]) -> int:
    """Count only soft (partial-completion) issues for the index nav badge.
    Hard failures are excluded — they're shown to the user via Telegram in
    real time and don't belong on this catalog page anymore."""
    _ = hard_failures  # accepted for compatibility; no longer counted
    n = 0
    for r in records:
        if _detect_issues(r):
            n += 1
    return n


def _build_resolved_lookup() -> dict[tuple[str, str], dict]:
    """Map (trade_date, ticker) → resolved memory-log entry. Used by the
    index card renderer to surface 5-trading-day realized return + alpha
    next to each historical analysis once auto_resolve has caught up."""
    mem = _read_memory_full()
    return {(e["date"], e["ticker"]): e for e in mem.get("resolved", [])}


_BENCHMARK_LABEL_CACHE: dict[str, str] = {}


def _benchmark_label_for(ticker: str) -> str:
    """Return the sector ETF human-readable label for this ticker's alpha
    benchmark — e.g. '반도체 (SOXX)' for AMAT, '금융 (XLF)' for JPM,
    'KOSPI 200 (KODEX 200)' for KR blue chips, 'TOPIX (1306)' for JP.

    Uses `_resolve_benchmark`'s tuple[1] (the Korean label that already
    has the ETF ticker in parens) rather than tuple[0] (raw ticker
    only). The richer label is much clearer to Korean readers than
    bare codes like '069500.KS' — surfaced by the cross-market audit
    2026-05-18 where KR/JP outcome lines were less informative than
    US ones due to numeric ticker codes.

    Falls back to 'SPY' when nothing resolves. Process-level cache
    keeps yfinance .info round-trips bounded across dashboard regens."""
    if ticker in _BENCHMARK_LABEL_CACHE:
        return _BENCHMARK_LABEL_CACHE[ticker]
    label = "SPY"
    try:
        from tradingagents.agents.utils.sector_strength_tools import _resolve_benchmark
        bm = _resolve_benchmark(ticker)
        if bm:
            # Prefer the Korean label (bm[1]) — already contains the
            # ETF ticker in parens. Fall back to bare ticker (bm[0])
            # only if the label slot is empty for some reason.
            label = bm[1] or bm[0] or "SPY"
    except Exception:
        pass
    _BENCHMARK_LABEL_CACHE[ticker] = label
    return label


def _render_outcome_html(resolved: dict | None) -> str:
    """Render outcome lines beneath an analysis card. Stacks 5d / 15d /
    30d when each is available — the 5d outcome lives in the entry tag
    (resolved['raw'] / resolved['alpha']) while longer windows live in
    resolved['outcomes_extra'] (list of {days, raw, alpha}). Empty when
    the entry is still pending or no realized return is parsed.

    Each window renders as its own line with independent hit/miss color
    so a Sell call that scores ✓ at 5d but reverses to ✗ at 30d is
    visible at a glance.

    Format (per line):
        📒 Nd 후 -1.6% (알파 +3.0%p vs 반도체 (SOXX)) — 매도 추천 맞음 · 섹터대비 high

    User-controllable knobs that influence this:
    - Direction verdict ('매수/매도 추천 맞음/틀림') from rating + alpha sign.
    - Magnitude ('섹터대비 high/low/동등') from alpha sign alone.
    - Benchmark label from _benchmark_label_for (sector ETF or broad
      market fallback per the ticker's industry / market).
    - CSS hit/miss class for color cue."""
    if not resolved:
        return ""
    rating = (resolved.get("rating") or "").lower()
    ticker = (resolved.get("ticker") or "").strip()

    # Collect windows in display order: 5d (from tag) first, then 15d
    # and 30d (from outcomes_extra). Filter unparseable rows so a
    # half-written OUTCOMES block doesn't crash the renderer.
    windows: list[dict] = []
    raw_5d = (resolved.get("raw") or "").strip()
    alpha_5d = (resolved.get("alpha") or "").strip()
    # Use _parse_pct as the validity gate — rejects "n/a", "+nan%", "+inf%"
    if _parse_pct(raw_5d) is not None:
        windows.append({"days": 5, "raw": raw_5d, "alpha": alpha_5d})
    for entry in (resolved.get("outcomes_extra") or []):
        days = entry.get("days")
        raw = (entry.get("raw") or "").strip()
        alpha = (entry.get("alpha") or "").strip()
        if not isinstance(days, int) or _parse_pct(raw) is None:
            continue
        windows.append({"days": days, "raw": raw, "alpha": alpha})
    if not windows:
        return ""
    windows.sort(key=lambda w: w["days"])  # 5 → 15 → 30

    bench_label = _benchmark_label_for(ticker) if ticker else "섹터"
    bench_safe = _html.escape(bench_label)

    lines: list[str] = []
    for w in windows:
        days = w["days"]
        raw = w["raw"]
        alpha = w["alpha"]
        cls = "outcome"
        verdict_text = ""
        alpha_num = _parse_pct(alpha)
        if alpha_num is not None and ticker:
            magnitude = "섹터대비 high" if alpha_num > 0 else (
                "섹터대비 low" if alpha_num < 0 else "섹터대비 동등"
            )
            if rating in ("buy", "overweight"):
                hit = alpha_num > 0
                cls += " hit" if hit else " miss"
                direction = "매수 추천 맞음" if hit else "매수 추천 틀림"
                verdict_text = f"{direction} · {magnitude}"
            elif rating in ("sell", "underweight"):
                hit = alpha_num < 0
                cls += " hit" if hit else " miss"
                direction = "매도 추천 맞음" if hit else "매도 추천 틀림"
                verdict_text = f"{direction} · {magnitude}"
            elif rating == "hold":
                verdict_text = f"보유 추천 · {magnitude}"
        alpha_part = (
            f" (알파 {_html.escape(alpha)}p vs {bench_safe})"
            if alpha and alpha != "n/a"
            else ""
        )
        verdict_part = f" — {_html.escape(verdict_text)}" if verdict_text else ""
        lines.append(
            f'<div class="{cls}">📒 {days}거래일 후 '
            f'{_html.escape(raw)}{alpha_part}{verdict_part}</div>'
        )
    return "".join(lines)


def _render_index(records: list[dict]) -> str:
    by_date: dict[str, list[dict]] = {}
    for r in records:
        by_date.setdefault(r["trade_date"], []).append(r)

    if not records:
        body = '<div class="empty">아직 분석 기록이 없습니다.</div>'
    else:
        # One memory-log read per index render; reused across every card.
        resolved_lookup = _build_resolved_lookup()
        sections = []
        for date in sorted(by_date.keys(), reverse=True):
            day_records = sorted(
                by_date[date],
                key=lambda r: r.get("analyzed_at", ""),
                reverse=True,
            )
            cards = []
            for rec in day_records:
                summary_text = rec.get("summary", "") or ""
                rating = _extract(_RATING_RE, summary_text) or "?"
                stance = _extract(_STANCE_LINE_RE, summary_text)
                past = _extract(_PAST_OUTCOMES_RE, summary_text)
                analyzed_at = rec.get("analyzed_at") or ""
                time_str = analyzed_at[11:16] if len(analyzed_at) >= 16 else ""
                ticker = rec.get("ticker", "?")
                href = f"./{date}/{_html.escape(ticker)}.html"
                past_html = (
                    f'<div class="past">{_html.escape(past)}</div>' if past else ""
                )
                # Show realized 5-day outcome if auto_resolve has caught up.
                # Empty string before the window elapses or when the entry
                # never made it into the memory log (older runs, Hold-only).
                outcome_html = _render_outcome_html(
                    resolved_lookup.get((date, ticker))
                )
                # For KR tickers with a DART match, display the Korean
                # corp name instead of the raw '005930.KS' / '014680.KS'
                # numeric ticker — pure digits are hard to scan in a
                # mixed-market list. data-name is added so JS search
                # matches the Korean name too (in addition to the
                # ticker via data-ticker).
                kr_name = _ticker_kr_name(ticker)
                label = kr_name or ticker
                data_name_attr = (
                    f' data-name="{_html.escape(kr_name)}"' if kr_name else ""
                )
                # Snippet-search line index — built from full_report at
                # render time so '변압기' / 'GLP-1' / 'CHIPS Act' etc.
                # match anywhere in the analyst body, not just card
                # metadata. JSON-encoded then HTML-escaped so quotes /
                # angle brackets inside analyst text can't break the
                # data attribute. Lines list capped at 200 (front 100 +
                # back 100 when overflow) per card; ~50 KB / card max.
                _idx_lines = _index_lines_from_full_report(
                    rec.get("full_report") or ""
                )
                _idx_lines.append(
                    {"sec": "요약", "txt": (rec.get("summary") or "").strip()[:600]}
                )
                _lines_attr = _html.escape(
                    json.dumps(_idx_lines, ensure_ascii=False)
                )
                cards.append(f"""
                <div class="card" id="card-{_html.escape(date)}-{_html.escape(ticker).replace('.','_')}" data-ticker="{_html.escape(ticker)}"{data_name_attr} data-date="{_html.escape(date)}" data-href="{href}" data-lines="{_lines_attr}">
                  <div class="card-row">
                    <a class="ticker" href="{href}">📊 {_html.escape(label)}</a>
                    {_badge_html(rating)}
                    <div class="stance">{_html.escape(stance)}</div>
                    <div class="time">{_html.escape(time_str)}</div>
                    <button class="del-btn" type="button" title="이 분석 기록 삭제">🗑️</button>
                  </div>
                  {past_html}
                  {outcome_html}
                </div>
                """)
            sections.append(f"""
            <details class="day" open>
              <summary class="day-head">
                <span>📅 {_format_date_kr(date)}</span>
                <span class="count">{len(day_records)}건</span>
              </summary>
              <div class="cards">{"".join(cards)}</div>
            </details>
            """)
        # Orphan resolved entries: in the memory log but with no matching
        # archive record (typically analyses that predate the archive
        # system rollout). Surface them in a small footer so the
        # accuracy card's "평가 N건" denominator stays auditable — the
        # user can see exactly which past calls feed the percentage even
        # when there's no card to attach the outcome to.
        archive_keys = {(r.get("trade_date", ""), r.get("ticker", "")) for r in records}
        orphans = [
            e for (k, e) in resolved_lookup.items() if k not in archive_keys
        ]
        if orphans:
            orphans.sort(key=lambda e: (e.get("date", ""), e.get("ticker", "")), reverse=True)
            orphan_rows = []
            for e in orphans:
                row_outcome = _render_outcome_html(e).replace(
                    'class="outcome', 'class="outcome orphan-outcome'
                )
                orphan_rows.append(f"""
                <div class="orphan-row">
                  <span class="orphan-meta">📊 {_html.escape(e.get('ticker', '?'))}
                    · {_html.escape(e.get('date', ''))}
                    · {_html.escape(e.get('rating', ''))}</span>
                  {row_outcome}
                </div>
                """)
            sections.append(f"""
            <details class="day orphan-day">
              <summary class="day-head">
                <span>📒 archive 이전 평가 결과</span>
                <span class="count">{len(orphans)}건</span>
              </summary>
              <div class="orphan-list">{"".join(orphan_rows)}</div>
            </details>
            """)
        body = "".join(sections)

    stats_panel = _render_stats_panel(_compute_stats(records))
    # Footer line shown at the bottom of the page so the user can see at
    # a glance when the dashboard was regenerated and that the archive
    # has no rotation policy. Matches the convention used by the
    # search_my_brain dashboard the user asked us to mirror.
    kst = datetime.timezone(datetime.timedelta(hours=9))
    generated_at = datetime.datetime.now(kst).strftime("%Y-%m-%d %H:%M")
    footer_html = (
        f'<div class="archive-footer">'
        f'생성: {generated_at} · {len(records)}건 누적 · 무제한 보관'
        f'</div>'
    )
    # Headline link to the errors page; count includes hard failures
    # (usage.jsonl) plus archive entries with placeholder/tool issues.
    issue_count = _count_total_issues(records, _read_hard_failures())
    # External dashboards live at known LAN addresses; rel=noopener on the
    # external links prevents window.opener leakage to the third-party tab.
    # 🇰🇷 regional-indicator pair은 일부 OS (특히 Linux Chromium) 에서 'KR'
    # 글자로 fallback 렌더되므로 inline SVG 태극기로 대체 — 어디서나 보장.
    _external_links = (
        ' · <a href="http://34.50.23.221:8002/dashboard" target="_blank" rel="noopener">📈 Standard View</a>'
        f' · <a href="http://34.50.23.221:8765/dashboard/" target="_blank" rel="noopener">{_KR_FLAG_SVG} 한국 수출입 데이터</a>'
    )
    if issue_count > 0:
        errors_link = (
            f' · <a href="errors.html">🚨 오류 / 미완성 {issue_count}건</a>'
            f' · <a href="screener.html">📊 Bottleneck Screener</a>'
            f' · <a href="screener_domains.html">🗂️ 도메인 목록</a>'
            f' · <a href="daily_byte.html">📊 Daily Byte</a>'
            + _external_links
        )
    else:
        errors_link = (
            ' · <a href="errors.html">🚨 오류 기록 (없음)</a>'
            ' · <a href="screener.html">📊 Bottleneck Screener</a>'
            ' · <a href="screener_domains.html">🗂️ 도메인 목록</a>'
            ' · <a href="daily_byte.html">📊 Daily Byte</a>'
            + _external_links
        )

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>🦉 NOAH 주식분석 아카이브</title>
<script>{_THEME_JS}</script>
<style>{_INDEX_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>🦉 NOAH 주식분석 아카이브</h1>
  <p class="sub">카드 클릭 시 전체 리포트{errors_link}</p>
  {stats_panel}
  <div class="search-bar">
    <input id="search" type="text" placeholder="종목 / 본문 검색 (예: NVDA, 삼성전자, 변압기, GLP-1, CHIPS Act)" autocomplete="off" spellcheck="false">
    <button id="clear-btn" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="status" class="status-line">총 {len(records)}건의 분석 기록</p>
  <div id="snippets" class="snippets" style="display:none"></div>
  <div id="empty-search" class="empty-search">검색 결과가 없습니다.</div>
  {body}
  {footer_html}
</div>
<script>{_INDEX_JS}</script>
</body>
</html>
"""


_DETAIL_CSS = _BASE_CSS + """
.back { color: var(--fg-soft); font-size: 13px; }
.back:hover { color: var(--accent); }
.title-row {
  display: flex; align-items: center; gap: 12px; margin: 14px 0 6px;
  flex-wrap: wrap;
}
.title-row h1 { margin: 0; font-size: 24px; }
.meta { color: var(--fg-soft); font-size: 13px; margin-bottom: 24px; }
section.report-section { margin-top: 24px; }
section.report-section > h2 {
  font-size: 16px; margin: 0 0 10px; padding: 6px 0;
  border-bottom: 1px solid var(--border);
}
pre.report {
  white-space: pre-wrap; word-wrap: break-word; font-family: inherit;
  font-size: 14px; line-height: 1.7; margin: 0; color: var(--fg);
  background: var(--card); padding: 16px; border: 1px solid var(--border);
  border-radius: 8px;
}
pre.report strong { color: var(--fg); }
pre.report h3, pre.report h4, pre.report h5, pre.report h6 {
  margin: 12px 0 4px; font-size: 14px;
}
/* Deep-link mark — index-page snippet click navigates here with
   #mark=<phrase>; JS below wraps the first occurrence and pulses. */
mark.snippet-target {
  background: rgba(245, 158, 11, 0.6); color: #ffffff;
  padding: 1px 4px; border-radius: 3px;
  box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.45);
  animation: markPulse 1.8s ease-out;
}
@keyframes markPulse {
  0%   { background: rgba(245, 158, 11, 0.95);
         box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.6); }
  100% { background: rgba(245, 158, 11, 0.6);
         box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.45); }
}
"""


# Deep-link snippet scroll: when navigating from the index page's snippet
# panel, the URL carries #mark=<URL-encoded query>. This script walks the
# report-section text nodes, wraps the first occurrence with
# <mark.snippet-target>, scrolls it into view, and lets the CSS pulse run.
# Bails silently when the query no longer matches (analysis regenerated /
# stale link). Runs in DOMContentLoaded so the markdown render has settled.
_DETAIL_DEEP_LINK_JS = """
(function() {
  function run() {
    const m = (location.hash || '').match(/^#mark=(.+)$/);
    if (!m) return;
    let q;
    try { q = decodeURIComponent(m[1]); } catch (e) { return; }
    q = (q || '').trim();
    if (!q) return;
    const root = document.querySelector('.report-section + .report-section')
              || document.querySelector('.report-section')
              || document.body;
    const ql = q.toLowerCase();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function(node) {
        if (!node.parentNode) return NodeFilter.FILTER_REJECT;
        const tag = node.parentNode.tagName;
        if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'MARK') {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    let node;
    while ((node = walker.nextNode())) {
      const text = node.textContent;
      const idx = text.toLowerCase().indexOf(ql);
      if (idx >= 0) {
        const parent = node.parentNode;
        const before = text.slice(0, idx);
        const match = text.slice(idx, idx + q.length);
        const after = text.slice(idx + q.length);
        if (before) parent.insertBefore(document.createTextNode(before), node);
        const mk = document.createElement('mark');
        mk.className = 'snippet-target';
        mk.textContent = match;
        parent.insertBefore(mk, node);
        if (after) parent.insertBefore(document.createTextNode(after), node);
        parent.removeChild(node);
        setTimeout(function() {
          mk.scrollIntoView({behavior: 'smooth', block: 'center'});
        }, 60);
        return;
      }
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
  // Also re-run on hash change so the user can re-trigger by clicking a
  // different snippet within the same tab via browser back/forward.
  window.addEventListener('hashchange', run);
})();
"""


def _render_detail(rec: dict) -> str:
    ticker = rec.get("ticker", "?")
    date = rec.get("trade_date", "")
    analyzed_at = (rec.get("analyzed_at") or "")[:16].replace("T", " ")
    elapsed = float(rec.get("elapsed_sec", 0) or 0)
    rating = _extract(_RATING_RE, rec.get("summary", "")) or "?"
    summary = rec.get("summary", "") or ""
    full = rec.get("full_report", "") or ""

    # Title / header use the Korean corp name for KR tickers so the
    # browser tab and the in-page H1 read '한솔케미칼 / 014680.KS'
    # rather than the bare numeric ticker. The bare ticker stays in
    # the meta line below for users who navigated by ticker.
    kr_name = _ticker_kr_name(ticker)
    h1_label = f"{kr_name} / {ticker}" if kr_name else ticker

    # 5-trading-day outcome — surface here on the detail page too, not
    # just on the index card list. Users navigating directly to
    # /<DATE>/<TICKER>.html (e.g. via Telegram link to '📋 전체 리포트')
    # previously had to bounce back to the index to see if their
    # analysis got an outcome. Now visible inline right under the
    # meta line. Empty string when the 5-day window hasn't elapsed
    # or auto_resolve hasn't caught up yet — same falsy-handling as
    # the card renderer.
    try:
        resolved_entry = _build_resolved_lookup().get((date, ticker))
    except Exception:
        resolved_entry = None
    outcome_html = _render_outcome_html(resolved_entry)

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>📊 {_html.escape(h1_label)} ({_html.escape(date)})</title>
<script>{_THEME_JS}</script>
<style>{_DETAIL_CSS}</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="../index.html">← 아카이브로 돌아가기</a>
  <div class="title-row">
    <h1>📊 {_html.escape(h1_label)}</h1>
    {_badge_html(rating)}
  </div>
  <div class="meta">
    분석일: {_html.escape(date)} · 실행 시각: {_html.escape(analyzed_at)} ·
    소요: {elapsed:.1f}초
  </div>
  {outcome_html}
  <section class="report-section">
    <h2>📋 요약</h2>
    {_md_to_html(summary)}
  </section>
  <section class="report-section">
    <h2>📋 전체 리포트</h2>
    {_md_to_html(full)}
  </section>
</div>
<script>{_DETAIL_DEEP_LINK_JS}</script>
</body>
</html>
"""


# ─── public entry point ──────────────────────────────────────────────
def regenerate_index() -> None:
    """Scan archive dir, rewrite index.html and per-analysis detail pages.

    Called from ``bot.analyzer.analyze`` after each archive write.
    Idempotent. Safe to call repeatedly. All errors are swallowed —
    dashboard issues must never break the analysis pipeline.
    """
    try:
        records = _load_all()
        # Newest-first: dates descending, then analyzed_at descending
        records.sort(
            key=lambda r: (r.get("trade_date", ""), r.get("analyzed_at", "")),
            reverse=True,
        )
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "index.html").write_text(
            _render_index(records), encoding="utf-8"
        )
        # Errors / partial-completion catalog (Option A from the spec).
        hard_failures = _read_hard_failures()
        (ARCHIVE_ROOT / "errors.html").write_text(
            _render_errors_page(records, hard_failures), encoding="utf-8"
        )
        for rec in records:
            day = rec.get("trade_date", "")
            ticker = rec.get("ticker", "")
            if not day or not ticker:
                continue
            day_dir = ARCHIVE_ROOT / day
            day_dir.mkdir(parents=True, exist_ok=True)
            (day_dir / f"{ticker}.html").write_text(
                _render_detail(rec), encoding="utf-8"
            )
        log.info("dashboard: regenerated with %d entries", len(records))
    except Exception as exc:
        log.warning("dashboard: regenerate failed: %s", exc)


# ── Screener archive view ────────────────────────────────────────────────
# Separate from the per-ticker /analysis dashboard. Screener picks are
# multi-stock theme generation (6-18M thesis), so the rendering is a
# date-grouped run list with Top-3 mini-tables showing 5/15/30d outcomes
# (alpha vs sector ETF). Outcomes come from screener_memory.md via
# auto_resolve.py 's screener pass.

_SCREENER_ARCHIVE_DIR = Path.home() / ".tradingagents" / "screener_archive"
_SCREENER_MEMORY_PATH = (
    Path.home() / ".tradingagents" / "memory" / "screener_memory.md"
)

# Daily Byte (장 마감 후 KR 수급 브리프) archive — mirrors the screener
# archive layout: ~/.tradingagents/daily_byte_archive/YYYY-MM-DD/HHMMSS_
# daily_byte.json. Rendered to daily_byte.html with the same theme + search
# + trash UX as screener.html (reuses _SCREENER_CSS).
_DAILY_BYTE_ARCHIVE_DIR = Path.home() / ".tradingagents" / "daily_byte_archive"


def _load_screener_runs() -> list[dict]:
    """Scan ~/.tradingagents/screener_archive/YYYY-MM-DD/*.json and return
    a list of run dicts (newest first). Each run has {ts, domain,
    raw_output, validated_tickers, rejected_tickers, elapsed_sec,
    cost_krw, top_3_picks, _path, _date}.

    Lazy migration (2026-05-29): old JSONs (saved before commit 01b3957)
    lack binding_constraint / top3_section / bottom_line fields, and old
    JSONs (saved before d9ddd25) carry the legacy '(Phase β · 실시간
    데이터)' suffix in their domain string. We parse sections from
    raw_output on the fly + strip the suffix at load time so the
    dashboard renders consistently for ALL runs without a separate
    migration job. Cost is negligible (~5 regex passes per file)."""
    import json as _json
    runs: list[dict] = []
    if not _SCREENER_ARCHIVE_DIR.exists():
        return runs
    try:
        for date_dir in sorted(_SCREENER_ARCHIVE_DIR.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for json_file in sorted(date_dir.iterdir(), reverse=True):
                if not json_file.name.endswith(".json"):
                    continue
                try:
                    with open(json_file, encoding="utf-8") as f:
                        rec = _json.load(f)
                    rec["_path"] = str(json_file)
                    rec["_date"] = date_dir.name
                    rec["_filename"] = json_file.name
                    # Migrate old archives without parsed sections — or
                    # re-parse if previous migration produced empty strings
                    # (regex couldn't anchor because Pro emitted <b>...</b>
                    # around the section headers). Always re-parse when the
                    # stored fields are blank but raw_output exists.
                    need_parse = (
                        rec.get("raw_output")
                        and (not rec.get("binding_constraint")
                             or not rec.get("master_table")
                             or not rec.get("top3_section")
                             or not rec.get("bottom_line"))
                    )
                    if need_parse:
                        sections = _parse_screener_sections_local(rec["raw_output"])
                        # Use parsed value when stored is empty; preserve
                        # stored value when it's non-empty (already migrated).
                        for k in ("binding_constraint", "master_table",
                                  "top3_section", "bottom_line"):
                            if not rec.get(k) and sections.get(k):
                                rec[k] = sections[k]
                    # Strip legacy Phase β suffix from domain for display
                    raw_domain = rec.get("domain", "") or ""
                    rec["domain"] = re.sub(
                        r"\s*\(Phase\s*β[^)]*\)", "", raw_domain
                    ).strip() or raw_domain
                    runs.append(rec)
                except Exception as exc:
                    log.warning("dashboard: screener load %s failed: %s", json_file, exc)
    except Exception as exc:
        log.warning("dashboard: screener archive scan failed: %s", exc)
    return runs


def _parse_screener_sections_local(raw: str) -> dict:
    """Mirror of bot.screener._parse_screener_sections — duplicated here so
    dashboard.py doesn't import from screener.py at module-load time.
    Extracts 📍 binding constraint / 🏆 Top 3 / 💡 Bottom line.

    Tolerates Pro's `<b>...</b>` / markdown formatting around the section
    headers (2026-05-29 surfaced: Pro emits '<b>📍 현재 binding
    constraint</b>' so anchoring on plain '📍 현재 binding constraint\n'
    failed → sections came back empty, lazy migration produced nothing).
    Fix: strip HTML tags + markdown bold + markdown headers before regex."""
    out = {"binding_constraint": "", "master_table": "",
           "top3_section": "", "bottom_line": ""}
    if not raw:
        return out
    # Normalise away noise that breaks header anchoring
    clean = raw
    clean = re.sub(r"<\/?[a-zA-Z]+[^>]*>", "", clean)   # <b>, </b>, <i>, etc.
    clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", clean)    # **bold** → bold
    clean = re.sub(r"^#+\s*", "", clean, flags=re.MULTILINE)  # ## headers
    m = re.search(
        r"📍\s*현재\s*binding\s*constraint\s*\n+(.*?)(?=\n+(?:📊|🏆|💡|⚠️)|\Z)",
        clean, re.DOTALL,
    )
    if m: out["binding_constraint"] = m.group(1).strip()
    # Master Table 전체 (4단계 결과 — 검증된 모든 ticker 행)
    m = re.search(
        r"📊\s*Master\s*Table\s*\n+(.*?)(?=\n+(?:🏆|💡|⚠️)|\Z)",
        clean, re.DOTALL,
    )
    if m: out["master_table"] = m.group(1).strip()
    m = re.search(
        r"🏆\s*Top\s*3\s*conviction\s*picks\s*\n+(.*?)(?=\n+(?:💡|⚠️)|\Z)",
        clean, re.DOTALL,
    )
    if m: out["top3_section"] = m.group(1).strip()
    m = re.search(
        r"💡\s*Bottom\s*line\s*\n+(.*?)(?=\n+(?:⚠️|🤖)|\Z)",
        clean, re.DOTALL,
    )
    if m: out["bottom_line"] = m.group(1).strip()
    return out


def _load_screener_outcomes() -> dict[tuple[str, str], dict]:
    """Parse screener_memory.md → dict keyed by (date, ticker). Each value
    has the TradingMemoryLog parsed entry shape — raw/alpha (5d) in the
    tag + outcomes_extra list for 15d/30d. auto_resolve.py keeps this
    current at 12h cadence."""
    out: dict[tuple[str, str], dict] = {}
    if not _SCREENER_MEMORY_PATH.exists():
        return out
    try:
        from tradingagents.agents.utils.memory import TradingMemoryLog
        cfg = {"memory_log_path": str(_SCREENER_MEMORY_PATH)}
        memory = TradingMemoryLog(cfg)
        for e in memory.load_entries():
            key = (e.get("date", ""), (e.get("ticker") or "").upper())
            if key[0] and key[1]:
                out[key] = e
    except Exception as exc:
        log.warning("dashboard: screener memory load failed: %s", exc)
    return out


def _fmt_pct_signed(s: str | None) -> str:
    """'+2.3%' → '+2.3%' (pass-through with sign coloring done via CSS class)."""
    if not s:
        return "—"
    return s.strip()


def _outcome_class(s: str | None) -> str:
    """CSS class for a return string: positive / negative / neutral."""
    if not s:
        return "neu"
    s = s.strip()
    if s.startswith("+") and not s.startswith("+0.0"):
        return "pos"
    if s.startswith("-") and not s.startswith("-0.0"):
        return "neg"
    return "neu"


def _render_screener_page(runs: list[dict], outcomes: dict) -> str:
    """Render screener.html — date-grouped run cards with Top-3 mini-tables
    showing 5/15/30d outcomes (alpha vs sector ETF). Self-contained HTML
    with embedded CSS matching NOAH dashboard dark-on-light palette."""
    import html as _html
    from collections import defaultdict

    # Group runs by date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_date[r.get("_date", "")].append(r)

    # Build summary stats
    total_runs = len(runs)
    total_cost_krw = sum(r.get("cost_krw", 0) or 0 for r in runs)
    total_picks = sum(len(r.get("top_3_picks", []) or []) for r in runs)
    resolved_count = sum(
        1 for r in runs for p in (r.get("top_3_picks") or [])
        if outcomes.get((r.get("_date", ""), (p.get("ticker") or "").upper()), {}).get("raw")
    )

    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="index.html">← NOAH 종목 분석</a>
    · <a href="screener_domains.html">🗂️ 도메인 목록</a>
    · <a href="http://34.50.23.221:8002/dashboard" target="_blank" rel="noopener">📈 Standard View</a>
    · <a href="http://34.50.23.221:8765/dashboard/" target="_blank" rel="noopener">{_KR_FLAG_SVG} 한국 수출입 데이터</a>
  </div>
  <h1>📊 Bottleneck Screener — Archive</h1>
  <p class="sub">테마별 다종목 idea generation · 6-18M thesis (NOAH /ticker 5거래일 평가와 별개 horizon)</p>

  <div class="stats">
    <div class="stat"><div class="stat-v">{total_runs}</div><div class="stat-l">총 실행</div></div>
    <div class="stat"><div class="stat-v">₩{total_cost_krw:,.0f}</div><div class="stat-l">누적 비용</div></div>
    <div class="stat"><div class="stat-v">{total_picks}</div><div class="stat-l">Top-3 picks</div></div>
    <div class="stat"><div class="stat-v">{resolved_count}</div><div class="stat-l">5d resolved</div></div>
  </div>

  <div class="search-bar">
    <input id="scr-search" type="text" placeholder="ticker / 회사명 / 테마 / 본문 검색 (예: 변압기, 액체냉각, ETN, Eaton)" autocomplete="off" spellcheck="false">
    <button id="scr-clear" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="scr-status" class="status-line">총 {total_runs}건의 screener 실행</p>
  <div id="scr-snippets" class="snippets" style="display:none"></div>
  <div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>
""")

    if not runs:
        parts.append("""
  <div class="empty">
    아직 screener 실행 기록이 없습니다. 텔레그램 채널에서
    <code>/screener</code> 를 실행하세요.
  </div>
</div>""")
        return "".join(parts)

    # Sort dates newest-first; expand only today's group by default so the
    # archive stays compact once many days accumulate (NOAH index.html
    # uses the same pattern). Pre-resolve `today` once outside the loop.
    from datetime import datetime as _dt_sc, timezone as _tz_sc, timedelta as _td_sc
    _today_kst = _dt_sc.now(_tz_sc(_td_sc(hours=9))).date().isoformat()
    for date in sorted(by_date.keys(), reverse=True):
        day_open = " open" if date == _today_kst else ""
        day_count = len(by_date[date])
        parts.append(
            f'<details class="day"{day_open}>'
            f'<summary class="day-head">'
            f'<span>📅 {_html.escape(date)}</span>'
            f'<span class="count">{day_count}건</span>'
            f'</summary>'
            f'<div class="day-body">'
        )
        for r in by_date[date]:
            domain = _html.escape(r.get("domain", "Unknown"))
            # Extract HH:MM from ISO 'YYYY-MM-DDTHH:MM:SS+09:00'. Previous
            # slice ts[-8:-3] returned ':00+09' (suffix of timezone), not
            # the clock — 2026-05-29 surfaced. Use 'T' split for safety.
            raw_ts = r.get("ts") or ""
            ts_clock = ""
            if "T" in raw_ts:
                ts_clock = raw_ts.split("T", 1)[1][:5]  # 'HH:MM'
            ts = _html.escape(ts_clock)
            cost = r.get("cost_krw", 0) or 0
            elapsed = r.get("elapsed_sec", 0) or 0
            validated_list = r.get("validated_tickers", []) or []
            tickers_n = len(validated_list)
            # Tooltip: full ticker list on hover over '11개 ticker' chip
            tickers_title = (
                "Master Table 종목: " + ", ".join(validated_list)
                if validated_list else "검증된 종목 없음"
            )
            picks = r.get("top_3_picks", []) or []

            # Narrative sections (binding constraint / Top 3 rationale /
            # bottom line) — collapsed by default; user clicks summary to
            # expand. NOAH index.html uses similar <details> pattern for
            # past-outcomes accordions, kept visual parity here.
            binding = (r.get("binding_constraint") or "").strip()
            master_table_txt = (r.get("master_table") or "").strip()
            top3_section_txt = (r.get("top3_section") or "").strip()
            bottom_line = (r.get("bottom_line") or "").strip()
            has_analysis = bool(binding or master_table_txt or top3_section_txt or bottom_line)
            analysis_html = ""
            if has_analysis:
                pieces = []
                if binding:
                    pieces.append(
                        f'<div class="analysis-sec"><div class="analysis-h">'
                        f'📍 현재 binding constraint</div>'
                        f'<div class="analysis-b" data-section="binding">{_html.escape(binding)}</div></div>'
                    )
                if master_table_txt:
                    # Master Table 은 11행 처럼 길어서 nested collapsible
                    # (디폴트 접힘) — 사용자가 클릭해 모든 ticker 행 펼침.
                    # Section title `[전력: 변압기]` / `[냉각: 액체냉각 CDU]`
                    # 같이 대괄호로 감싼 줄을 굵은 글씨로 강조 — 11개 ticker
                    # 가 4-6개 테마 그룹으로 시각적 구분되게.
                    mt_safe = _html.escape(master_table_txt)
                    mt_bolded = re.sub(
                        r"(^|\n)(\[[^\]\n]+\])",
                        r'\1<b class="mt-section">\2</b>',
                        mt_safe,
                    )
                    pieces.append(
                        f'<details class="analysis-mt"><summary>'
                        f'📊 Master Table 펼치기 (검증된 {tickers_n}개 ticker 전체 — 테마별 Tier A/B/C 신호 + 가격 반영도 + catalyst + kill trigger)'
                        f'</summary>'
                        f'<div class="analysis-sec"><div class="analysis-b" data-section="master_table">'
                        f'{mt_bolded}</div></div>'
                        f'</details>'
                    )
                if top3_section_txt:
                    pieces.append(
                        f'<div class="analysis-sec"><div class="analysis-h">'
                        f'🏆 Top 3 conviction picks (추천 근거)</div>'
                        f'<div class="analysis-b" data-section="top3">{_html.escape(top3_section_txt)}</div></div>'
                    )
                if bottom_line:
                    pieces.append(
                        f'<div class="analysis-sec"><div class="analysis-h">'
                        f'💡 Bottom line</div>'
                        f'<div class="analysis-b" data-section="bottom">{_html.escape(bottom_line)}</div></div>'
                    )
                analysis_html = (
                    f'<details class="analysis">'
                    f'<summary>📖 분석 내용 펼치기 (binding · Master Table · Top 3 · bottom line)</summary>'
                    + "".join(pieces) +
                    f'</details>'
                )

            filename = _html.escape(r.get("_filename", ""))
            # Build per-line snippet index: search matches show the
            # specific line containing the term, with surrounding context
            # tightly capped (200-char single line) so the snippet panel
            # stays readable. SV dashboard pattern — typing '변압기'
            # surfaces the master-table row that mentions it; clicking the
            # snippet jumps to the card and opens it in place. Falls back
            # to whole-card filter via card-level hay below when content
            # is empty (legacy archive entries without parsed sections).
            def _lines(label: str, text: str, cap: int = 300) -> list[dict]:
                out = []
                for ln in (text or "").splitlines():
                    s = ln.strip()
                    if len(s) >= 3:
                        out.append({"sec": label, "txt": s[:cap]})
                return out
            card_lines: list[dict] = []
            card_lines.extend(_lines("binding", binding))
            card_lines.extend(_lines("master_table", master_table_txt))
            card_lines.extend(_lines("top3", top3_section_txt))
            card_lines.extend(_lines("bottom", bottom_line))
            # Cap per-card lines to stop pathological archives from
            # bloating the page; 200 lines per card covers full Master
            # Table (11 rows × ~10 fields) + binding + Top-3 + bottom_line
            # with margin.
            if len(card_lines) > 200:
                card_lines = card_lines[:200]

            # Card-level haystack as fallback (legacy/empty cards or
            # short-token matches inside the domain header).
            search_parts: list[str] = [
                domain.lower(),
                binding.lower()[:1500],
                master_table_txt.lower()[:3000],
                top3_section_txt.lower()[:1000],
                bottom_line.lower()[:500],
            ]
            for pick in picks:
                if not isinstance(pick, dict):
                    continue
                search_parts.append((pick.get("ticker") or "").lower())
                search_parts.append((pick.get("company") or "").lower())
                search_parts.append((pick.get("theme") or "").lower())
                search_parts.append((pick.get("thesis_line") or "").lower()[:300])
            for vt in validated_list:
                search_parts.append(vt.lower())
            search_attr = _html.escape(" ".join(p for p in search_parts if p))
            # JSON-encode line index for data attribute. Use json.dumps
            # with ensure_ascii=False then HTML-escape so quotes / angle
            # brackets in master_table content can't break the attribute.
            import json as _json_sc
            lines_attr = _html.escape(_json_sc.dumps(card_lines, ensure_ascii=False))

            # Card itself collapsible — when multiple domains accumulate
            # per day (AI 데이터센터 / 화학 / 기계 / 방산 ...) each one
            # collapses to just the header. Default open ONLY when this is
            # the only card on the date AND it's today; otherwise closed
            # for compactness. NOAH index.html has flat cards (per-ticker
            # cards live inside day groups), but screener cards carry rich
            # multi-section content so individual collapse is needed.
            day_card_count = len(by_date[date])
            card_default_open = (
                date == _today_kst and day_card_count == 1
            )
            card_open_attr = " open" if card_default_open else ""

            # Stable card id used by snippet click handlers to scroll +
            # open the source card. `_filename` is unique within a date,
            # and `_date` disambiguates across days.
            card_id = f"card-{_html.escape(r.get('_date',''))}-{filename}".replace(".", "_")
            parts.append(f"""
  <details class="card"{card_open_attr} id="{card_id}" data-date="{_html.escape(r.get('_date',''))}" data-filename="{filename}" data-search="{search_attr}" data-lines="{lines_attr}" data-default-open="{'true' if card_default_open else 'false'}">
    <summary class="card-h">
      <span class="card-toggle">▸</span>
      <span class="domain">{domain}</span>
      <span class="meta">⏱ {ts} · ₩{cost:,.1f} · {elapsed:.0f}s · <span class="ticker-chip" title="{_html.escape(tickers_title)}">✅ {tickers_n}개 ticker</span></span>
      <button class="del-btn" type="button" title="이 screener 기록 삭제">🗑️</button>
    </summary>
    <div class="card-body">
    {analysis_html}
""")
            if picks:
                parts.append('    <table class="picks"><thead><tr>'
                             '<th>#</th><th>Ticker</th><th>Tier</th><th>Company</th>'
                             '<th>5d</th><th>15d</th><th>30d</th><th>α vs sector</th>'
                             '</tr></thead><tbody>')
                for pick in picks:
                    if not isinstance(pick, dict):
                        continue
                    rank = pick.get("rank", "?")
                    ticker = (pick.get("ticker") or "").upper()
                    tier = pick.get("tier", "?")
                    company = _html.escape(pick.get("company", "")[:50])
                    # Look up resolved outcomes
                    out = outcomes.get((r.get("_date", ""), ticker), {}) or {}
                    raw5 = out.get("raw")
                    alpha5 = out.get("alpha")
                    extras = {o["days"]: o for o in (out.get("outcomes_extra") or [])}
                    raw15 = extras.get(15, {}).get("raw")
                    raw30 = extras.get(30, {}).get("raw")
                    pending_cell = '<span class="pending">⏳</span>'
                    cell_5d  = _fmt_pct_signed(raw5)  if raw5  else pending_cell
                    cell_15d = _fmt_pct_signed(raw15) if raw15 else "—"
                    cell_30d = _fmt_pct_signed(raw30) if raw30 else "—"
                    cell_alpha = _fmt_pct_signed(alpha5) if alpha5 else "—"
                    parts.append(
                        f'<tr>'
                        f'<td class="rank">#{rank}</td>'
                        f'<td><code>{_html.escape(ticker)}</code></td>'
                        f'<td><span class="tier-{tier}">{tier}</span></td>'
                        f'<td class="co">{company}</td>'
                        f'<td class="{_outcome_class(raw5)}">{cell_5d}</td>'
                        f'<td class="{_outcome_class(raw15)}">{cell_15d}</td>'
                        f'<td class="{_outcome_class(raw30)}">{cell_30d}</td>'
                        f'<td class="{_outcome_class(alpha5)}">{cell_alpha}</td>'
                        f'</tr>'
                    )
                parts.append('</tbody></table>')
            # Close card-body + card details
            parts.append('  </div></details>\n')
        # Close the date's details + body wrapper
        parts.append('</div></details>')

    parts.append("</div>")
    # JS — delete button POSTs to /api/screener_delete (mirror of NOAH
    # /api/delete pattern). On success, fade + remove the card. Server
    # regen rewrites screener.html so a later reload picks up everything.
    parts.append("""
<script>
// Snippet-highlight search (SV dashboard pattern). Typing '변압기' surfaces
// the specific lines mentioning it from binding / master_table / Top-3 /
// bottom_line content, with the match wrapped in <mark>. Clicking a
// snippet jumps to + opens the source card with a transient highlight.
// Empty query restores the default card view.
(function() {
  const inp = document.getElementById('scr-search');
  const clr = document.getElementById('scr-clear');
  const sts = document.getElementById('scr-status');
  const emp = document.getElementById('scr-empty');
  const snp = document.getElementById('scr-snippets');
  const cards = Array.from(document.querySelectorAll('.card'));
  const dayGroups = Array.from(document.querySelectorAll('details.day'));
  if (!inp) return;
  const total = cards.length;

  const SECTION_LABELS = {
    'binding': '📍 binding',
    'master_table': '📊 Master Table',
    'top3': '🏆 Top-3',
    'bottom': '💡 Bottom line',
  };
  const MAX_SNIPPETS = 80;  // soft cap to keep panel rendering fast

  // Pre-parse each card's line index once (JSON.parse is the hot path
  // on every keystroke otherwise). Domain header included as a synthetic
  // 'domain' entry so typing 'AI 데이터센터' returns at least one snippet.
  const cardData = cards.map(function(c) {
    let lines = [];
    try { lines = JSON.parse(c.dataset.lines || '[]'); } catch (e) {}
    const domainEl = c.querySelector('.domain');
    const domainTxt = domainEl ? domainEl.textContent.trim() : '';
    if (domainTxt) lines.unshift({sec: 'domain', txt: domainTxt});
    return {card: c, lines: lines, hay: (c.dataset.search || '')};
  });

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function(ch) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch];
    });
  }
  function escapeReg(s) {
    return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
  }
  function highlight(text, q) {
    if (!q) return escapeHtml(text);
    const re = new RegExp(escapeReg(q), 'gi');
    let out = '';
    let last = 0;
    let m;
    const safe = escapeHtml(text);
    // Re-derive match positions against the escaped string (HTML escape
    // preserves byte-for-byte ordering for the chars we search over —
    // CJK / latin / digit — so positions line up).
    while ((m = re.exec(safe)) !== null) {
      out += safe.slice(last, m.index);
      out += '<mark>' + safe.slice(m.index, m.index + m[0].length) + '</mark>';
      last = m.index + m[0].length;
      if (m.index === re.lastIndex) re.lastIndex++;  // safety for empty match
    }
    out += safe.slice(last);
    return out;
  }

  function showCardsMode() {
    snp.style.display = 'none';
    snp.innerHTML = '';
    emp.style.display = 'none';
    clearInlineHighlight();
    for (const c of cards) {
      c.style.display = '';
      c.open = (c.dataset.defaultOpen === 'true');
      c.classList.remove('hit-flash');
    }
    for (const d of dayGroups) {
      d.style.display = '';
    }
    sts.textContent = '총 ' + total + '건의 screener 실행';
  }

  function showSnippetsMode(q) {
    // Hide all cards + day groups while in search mode — snippet list
    // becomes the primary view. Card visibility is restored on snippet
    // click (only the clicked card) or on clear.
    for (const c of cards) c.style.display = 'none';
    for (const d of dayGroups) d.style.display = 'none';

    const ql = q.toLowerCase();
    const hits = [];
    for (const cd of cardData) {
      for (const ln of cd.lines) {
        if ((ln.txt || '').toLowerCase().indexOf(ql) >= 0) {
          hits.push({card: cd.card, sec: ln.sec, txt: ln.txt});
          if (hits.length >= MAX_SNIPPETS) break;
        }
      }
      if (hits.length >= MAX_SNIPPETS) break;
    }

    if (hits.length === 0) {
      snp.style.display = 'none';
      emp.style.display = 'block';
      sts.textContent = '0건 매칭 (검색: "' + q + '")';
      return;
    }
    emp.style.display = 'none';

    const cardHitCounts = new Map();
    const parts = [];
    for (const h of hits) {
      const cid = h.card.id;
      cardHitCounts.set(cid, (cardHitCounts.get(cid) || 0) + 1);
      const dateAttr = h.card.dataset.date || '';
      const domainEl = h.card.querySelector('.domain');
      const domainTxt = domainEl ? domainEl.textContent.trim() : '';
      const secLabel = SECTION_LABELS[h.sec] || h.sec;
      parts.push(
        '<div class="snippet" data-target="' + cid +
          '" data-section="' + escapeHtml(h.sec) + '">' +
          '<div class="snippet-meta">' +
            '<span class="snippet-sec">' + escapeHtml(secLabel) + '</span>' +
            '<span class="snippet-card">' + escapeHtml(domainTxt) +
            ' · ' + escapeHtml(dateAttr) + '</span>' +
          '</div>' +
          '<div class="snippet-text">' + highlight(h.txt, q) + '</div>' +
        '</div>'
      );
    }
    snp.innerHTML = parts.join('');
    snp.style.display = 'block';
    const uniq = cardHitCounts.size;
    const cap = hits.length >= MAX_SNIPPETS ? ' (상위 ' + MAX_SNIPPETS + '건 표시)' : '';
    sts.textContent = hits.length + '개 라인 · ' + uniq + '개 카드 매칭' + cap +
                      ' (검색: "' + q + '")';
  }

  function applyFilter() {
    const q = (inp.value || '').trim();
    if (!q) { showCardsMode(); return; }
    showSnippetsMode(q);
  }

  // Walk text nodes inside an element, wrap the first occurrence of q
  // with <mark.snippet-target> so the user can both see + scroll to the
  // exact phrase. Returns the mark element (or null when no match landed).
  // Prior calls' marks are cleared so a second snippet click doesn't
  // accumulate highlights. Text-node walking preserves existing inline
  // structure (e.g. Master Table 의 <b class="mt-section">[전력: 변압기]</b>
  // bolding) — a naive innerHTML replace would wipe that.
  function clearInlineHighlight() {
    document.querySelectorAll('mark.snippet-target').forEach(function(m) {
      const parent = m.parentNode;
      if (!parent) return;
      parent.replaceChild(document.createTextNode(m.textContent), m);
      parent.normalize();
    });
  }
  function highlightInElement(el, q) {
    if (!el || !q) return null;
    const ql = q.toLowerCase();
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: function(node) {
        // Skip text nodes that are already inside a mark (shouldn't
        // happen after clearInlineHighlight, but defensive).
        if (node.parentNode && node.parentNode.tagName === 'MARK') {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    let node;
    while ((node = walker.nextNode())) {
      const text = node.textContent;
      const idx = text.toLowerCase().indexOf(ql);
      if (idx >= 0) {
        const parent = node.parentNode;
        const before = text.slice(0, idx);
        const match = text.slice(idx, idx + q.length);
        const after = text.slice(idx + q.length);
        if (before) parent.insertBefore(document.createTextNode(before), node);
        const mk = document.createElement('mark');
        mk.className = 'snippet-target';
        mk.textContent = match;
        parent.insertBefore(mk, node);
        if (after) parent.insertBefore(document.createTextNode(after), node);
        parent.removeChild(node);
        return mk;
      }
    }
    return null;
  }

  // Snippet click → restore source card to view, open it AND its nested
  // details so the matched section is actually rendered, wrap the exact
  // match in <mark.snippet-target>, and scroll that mark into view.
  snp.addEventListener('click', function(ev) {
    const sn = ev.target.closest('.snippet');
    if (!sn) return;
    const tgt = sn.dataset.target;
    if (!tgt) return;
    const card = document.getElementById(tgt);
    if (!card) return;
    clearInlineHighlight();
    // Reveal everything that was hidden by the search filter, then open
    // ONLY the target card + its day group so the user lands somewhere
    // calm rather than every card expanding.
    for (const c of cards) {
      c.style.display = '';
      c.open = false;
      c.classList.remove('hit-flash');
    }
    for (const d of dayGroups) {
      d.style.display = '';
      d.open = false;
    }
    const dayG = card.closest('details.day');
    if (dayG) dayG.open = true;
    card.open = true;
    // Open the analysis details (top-level) + master-table nested details
    // when needed, otherwise the data-section element is still display:
    // none inside its closed <details> and scrollIntoView misses.
    const analysisEl = card.querySelector('details.analysis');
    if (analysisEl) analysisEl.open = true;
    const sec = sn.dataset.section || '';
    if (sec === 'master_table') {
      const mtEl = card.querySelector('details.analysis-mt');
      if (mtEl) mtEl.open = true;
    }
    snp.style.display = 'none';

    // Locate the section's analysis-b and wrap the exact match. Domain
    // matches (synthetic 'domain' line) don't have a body element, so
    // fall back to scrolling the card header.
    let scrollTarget = null;
    if (sec && sec !== 'domain') {
      const bodyEl = card.querySelector('.analysis-b[data-section="' + sec + '"]');
      if (bodyEl) {
        const q = (inp.value || '').trim();
        scrollTarget = highlightInElement(bodyEl, q) || bodyEl;
      }
    }
    if (!scrollTarget) {
      scrollTarget = card.querySelector('.card-h') || card;
    }
    card.classList.add('hit-flash');
    setTimeout(function() {
      scrollTarget.scrollIntoView({behavior: 'smooth', block: 'center'});
    }, 50);
    setTimeout(function() {
      card.classList.remove('hit-flash');
    }, 2400);
  });

  inp.addEventListener('input', applyFilter);
  clr.addEventListener('click', function() {
    inp.value = '';
    showCardsMode();
    inp.focus();
  });
})();

document.querySelectorAll('.del-btn').forEach(function(btn) {
  btn.addEventListener('click', function(ev) {
    ev.stopPropagation();
    ev.preventDefault();
    const card = btn.closest('.card');
    if (!card) return;
    const date = card.dataset.date;
    const filename = card.dataset.filename;
    if (!date || !filename) return;
    if (!confirm('📊 ' + date + ' / ' + filename + ' screener 기록을 삭제할까요?')) return;
    btn.disabled = true;
    btn.textContent = '⏳';
    fetch('api/screener_delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date: date, filename: filename})
    }).then(function(r) {
      return r.json().then(function(d) { return {status: r.status, body: d}; });
    }).then(function(res) {
      if (res.status === 200 && res.body && res.body.ok) {
        card.style.transition = 'opacity 0.2s';
        card.style.opacity = '0';
        setTimeout(function() { card.remove(); }, 200);
      } else {
        alert('삭제 실패: ' + (res.body && res.body.error || res.status));
        btn.disabled = false;
        btn.textContent = '🗑️';
      }
    }).catch(function(err) {
      alert('삭제 실패: ' + err);
      btn.disabled = false;
      btn.textContent = '🗑️';
    });
  });
});
</script>
</body></html>
""")
    return "".join(parts)


_SCREENER_CSS = (
    """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bottleneck Screener — Archive</title>
<script>""" + _THEME_JS + """</script>
<style>
/* Time-based light/dark theme (Asia/Seoul) — _THEME_JS toggles
   `data-theme="dark"` on the <html> element between 19:00-07:00.
   Mirrors the NOAH index/detail pages. Variables below adapt: light
   defaults at `:root`, dark overrides at `:root[data-theme="dark"]`. */
:root {
  --bg:#f8fafc; --card:#ffffff; --border:#e5e7eb;
  --text:#1f2937; --muted:#6b7280; --accent:#0ea5e9;
  --pos:#059669; --neg:#dc2626; --neu:#6b7280; --pending:#d97706;
  --accent-soft:rgba(14,165,233,0.07);
  --accent-soft2:rgba(14,165,233,0.14);
  --surface-tint:rgba(0,0,0,0.05);
  --surface-tint-strong:rgba(0,0,0,0.07);
  --row-border:rgba(0,0,0,0.05);
  --tier-l-bg:rgba(14,165,233,0.12); --tier-l-fg:#0369a1;
  --tier-m-bg:rgba(16,185,129,0.14); --tier-m-fg:#047857;
  --tier-s-bg:rgba(245,158,11,0.18); --tier-s-fg:#b45309;
  --search-btn-bg:#16a34a; --search-btn-hover:#15803d;
  --mark-bg:rgba(245,158,11,0.45); --mark-fg:#7c2d12;
  --mark-target-bg:rgba(245,158,11,0.7); --mark-target-fg:#1f2937;
}
:root[data-theme="dark"] {
  --bg:#0F1219; --card:#1A1F2B; --border:#2A3142;
  --text:#E8ECF4; --muted:#94A3B8; --accent:#3B82F6;
  --pos:#10B981; --neg:#EF4444; --neu:#6B7280; --pending:#F59E0B;
  --accent-soft:rgba(59,130,246,0.06);
  --accent-soft2:rgba(59,130,246,0.15);
  --surface-tint:rgba(255,255,255,0.04);
  --surface-tint-strong:rgba(255,255,255,0.06);
  --row-border:rgba(255,255,255,0.04);
  --tier-l-bg:rgba(59,130,246,0.15); --tier-l-fg:#93C5FD;
  --tier-m-bg:rgba(16,185,129,0.15); --tier-m-fg:#6EE7B7;
  --tier-s-bg:rgba(245,158,11,0.15); --tier-s-fg:#FCD34D;
  --search-btn-bg:#16a34a; --search-btn-hover:#15803d;
  --mark-bg:rgba(245,158,11,0.35); --mark-fg:#FCD34D;
  --mark-target-bg:rgba(245,158,11,0.55); --mark-target-fg:#fff;
}
* { box-sizing: border-box; }
body { background:var(--bg); color:var(--text); margin:0;
  font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',
    'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji','Twemoji Mozilla',sans-serif;
  line-height:1.55; -webkit-font-smoothing:antialiased; }
.wrap { max-width:1100px; margin:0 auto; padding:24px 16px 64px; }
.nav { margin-bottom:12px; }
.nav a { color:var(--accent); text-decoration:none; font-size:13px; }
.nav a:hover { text-decoration:underline; }
h1 { font-size:22px; margin:0 0 4px; }
h2.date { font-size:14px; color:var(--muted); margin:28px 0 12px;
  padding-bottom:6px; border-bottom:1px solid var(--border); }
details.day { margin:24px 0 0; }
details.day summary.day-head { cursor:pointer; font-size:15px;
  font-weight:600; padding:10px 4px; border-bottom:1px solid var(--border);
  display:flex; align-items:center; justify-content:space-between;
  list-style:none; color:var(--text); user-select:none; }
details.day summary.day-head::-webkit-details-marker { display:none; }
details.day summary.day-head::before { content:"▸"; color:var(--accent);
  margin-right:8px; transition:transform 0.15s; }
details.day[open] summary.day-head::before { content:"▾"; }
details.day summary.day-head:hover { background:var(--accent-soft); }
details.day .count { color:var(--muted); font-size:12px; font-weight:normal; }
details.day .day-body { padding-top:14px; }
.sub { color:var(--muted); font-size:13px; margin:0 0 24px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:10px; margin-bottom:24px; }
.stat { background:var(--card); border:1px solid var(--border);
  border-radius:10px; padding:14px 16px; }
.stat-v { font-size:20px; font-weight:600; }
.stat-l { color:var(--muted); font-size:11px; margin-top:2px;
  text-transform:uppercase; letter-spacing:0.5px; }
.card, details.card { background:var(--card); border:1px solid var(--border);
  border-radius:12px; padding:16px 18px; margin-bottom:14px; }
details.card { padding:0; }
details.card summary.card-h { cursor:pointer; list-style:none;
  padding:16px 18px; user-select:none; border-radius:12px; }
details.card[open] summary.card-h {
  border-bottom:1px solid var(--border); border-radius:12px 12px 0 0; }
details.card summary.card-h::-webkit-details-marker { display:none; }
details.card .card-toggle { color:var(--accent); font-weight:600;
  margin-right:2px; transition:transform 0.15s; }
details.card[open] .card-toggle { transform:rotate(90deg); display:inline-block; }
details.card .card-body { padding:14px 18px 18px; }
.card-h { display:flex; justify-content:space-between; align-items:center;
  gap:12px; flex-wrap:wrap; margin-bottom:12px; }
.domain { font-weight:600; font-size:15px; flex:1; min-width:200px; }
.meta { color:var(--muted); font-size:13px; font-family:'IBM Plex Mono',monospace; }
.del-btn { background:none; border:none; cursor:pointer;
  color:var(--muted); font-size:16px; padding:4px 8px; border-radius:6px;
  margin-left:auto; }
.del-btn:hover { color:var(--neg); background:rgba(239,68,68,0.10); }
.del-btn:disabled { opacity:0.5; cursor:wait; }
.search-bar { display:flex; gap:8px; margin-bottom:14px; }
.search-bar input { flex:1; background:var(--card);
  border:1px solid var(--border); border-radius:8px; padding:10px 14px;
  color:var(--text); font-size:14px; font-family:inherit; outline:none; }
.search-bar input:focus { border-color:var(--accent); }
.search-bar button { background:var(--search-btn-bg); color:white; border:none;
  border-radius:8px; padding:0 18px; font-size:13px; font-weight:600;
  cursor:pointer; transition:transform 0.05s, background 0.1s; }
.search-bar button:hover { background:var(--search-btn-hover); }
.search-bar button:active { transform:scale(0.97); }
.status-line { color:var(--muted); font-size:12px; margin:0 0 12px; }
table.picks { width:100%; border-collapse:collapse; font-size:15px; }
table.picks th { text-align:left; color:var(--muted); font-weight:500;
  padding:8px 6px; border-bottom:1px solid var(--border); font-size:12px;
  text-transform:uppercase; letter-spacing:0.3px; }
table.picks td { padding:8px 6px; border-bottom:1px solid var(--row-border); }
table.picks tr:last-child td { border-bottom:none; }
td.rank { font-weight:600; color:var(--accent); width:36px; }
td.co { color:var(--muted); }
td code { background:var(--surface-tint); padding:2px 6px;
  border-radius:4px; font-size:12px; }
td .tier-L { background:var(--tier-l-bg); color:var(--tier-l-fg);
  padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
td .tier-M { background:var(--tier-m-bg); color:var(--tier-m-fg);
  padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
td .tier-S { background:var(--tier-s-bg); color:var(--tier-s-fg);
  padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
td.pos { color:var(--pos); font-weight:600; }
td.neg { color:var(--neg); font-weight:600; }
td.neu { color:var(--muted); }
.pending { color:var(--pending); }
.empty { background:var(--card); border:1px solid var(--border);
  border-radius:10px; padding:30px; text-align:center; color:var(--muted);
  font-size:14px; }
.empty code { background:var(--surface-tint-strong); padding:2px 8px;
  border-radius:4px; }
details.analysis { margin:10px 0 14px; }
details.analysis summary { cursor:pointer; color:var(--accent);
  font-size:15px; padding:7px 11px; background:var(--accent-soft);
  border-radius:6px; user-select:none; list-style:none; }
details.analysis summary::-webkit-details-marker { display:none; }
details.analysis summary::before { content:"▸ "; margin-right:4px; }
details.analysis[open] summary::before { content:"▾ "; }
details.analysis summary:hover { background:var(--accent-soft2); }
.analysis-sec { margin:12px 4px 0; padding:10px 12px;
  background:var(--surface-tint); border-left:2px solid var(--border);
  border-radius:0 6px 6px 0; }
.analysis-h { color:var(--muted); font-size:13px; font-weight:600;
  margin-bottom:6px; text-transform:none; letter-spacing:0; }
/* 카드 본문 (Screener Top-3 근거·binding·bottom_line + Daily Byte 브리프).
   13px 는 모바일에서 작음 → 15px + line-height 1.65 (SV 본문 수준).
   2026-05-29 사용자 요청. */
.analysis-b { color:var(--text); font-size:14px; line-height:1.65;
  white-space:pre-wrap; }
.ticker-chip { cursor:help; border-bottom:1px dotted var(--muted); }
.ticker-chip:hover { color:var(--accent); }
details.analysis-mt { margin:12px 4px 0; }
details.analysis-mt summary { cursor:pointer; color:var(--accent);
  font-size:15px; padding:6px 10px; background:var(--accent-soft2);
  border-radius:6px; list-style:none; user-select:none; }
details.analysis-mt summary::-webkit-details-marker { display:none; }
details.analysis-mt summary::before { content:"▸ "; margin-right:4px; }
details.analysis-mt[open] summary::before { content:"▾ "; }
details.analysis-mt summary:hover { background:var(--accent-soft2); }
details.analysis-mt .analysis-sec { margin-top:8px; }
.mt-section { color:var(--accent); font-weight:700; font-size:13.5px;
  display:inline-block; padding:2px 0; margin-top:4px; }
/* Snippet-highlight search panel (SV dashboard pattern). Shown only
   when a query is active; clicking a snippet jumps to its source card. */
.snippets { display:flex; flex-direction:column; gap:8px; margin:12px 0 24px; }
.snippet { background:var(--card); border:1px solid var(--border);
  border-left:3px solid var(--accent); border-radius:8px;
  padding:10px 14px; cursor:pointer; transition:background 0.1s,
  border-color 0.1s; }
.snippet:hover { background:var(--accent-soft); border-left-color:var(--accent); }
.snippet-meta { display:flex; gap:10px; font-size:11px; color:var(--muted);
  margin-bottom:4px; align-items:center; }
.snippet-sec { background:var(--tier-l-bg); color:var(--tier-l-fg);
  padding:2px 8px; border-radius:4px; font-weight:600; }
.snippet-card { font-family:'IBM Plex Mono',monospace; }
.snippet-text { color:var(--text); font-size:13px; line-height:1.55;
  white-space:pre-wrap; word-break:break-word; }
mark { background:var(--mark-bg); color:var(--mark-fg); padding:1px 3px;
  border-radius:3px; font-weight:600; }
/* In-body highlight when a snippet is clicked — stronger than the
   snippet-panel mark so the user's eye finds the match after the
   smooth-scroll lands. Pulses briefly then settles. */
mark.snippet-target { background:var(--mark-target-bg); color:var(--mark-target-fg);
  box-shadow:0 0 0 2px rgba(245,158,11,0.4); animation:markPulse 1.8s ease-out; }
@keyframes markPulse {
  0%   { background:rgba(245,158,11,0.95); box-shadow:0 0 0 4px rgba(245,158,11,0.6); }
  100% { background:var(--mark-target-bg); box-shadow:0 0 0 2px rgba(245,158,11,0.4); }
}
/* Brief pulse when a snippet click scrolls to a card — fades after 2s
   so the user immediately sees which card the match came from. */
@keyframes hitFlash {
  0%   { box-shadow:0 0 0 2px rgba(245,158,11,0.7); }
  60%  { box-shadow:0 0 0 2px rgba(245,158,11,0.35); }
  100% { box-shadow:0 0 0 0 rgba(245,158,11,0); }
}
details.card.hit-flash { animation:hitFlash 2.4s ease-out; }
</style></head><body>
"""
)


def _render_screener_domains_page() -> str:
    """Generate a self-contained HTML page listing all registered screener
    domains (auto-discovered from bot.screener_themes). Renders the same
    info as the /screener_list Telegram command but as a browseable page
    so users can click and see the full taxonomy alongside the run
    archive. Per CLAUDE.md 'Screener 도메인 목록은 _HELP_TEXT inline
    금지' rule (2026-05-29), this page is the canonical user-facing
    surface for the domain catalog. Future Wave 2-B / Wave 3 / Wave ∞
    domain additions = module drop only — this page + /screener_list
    auto-update; _HELP_TEXT never grows.

    2026-05-29 follow-up: append a '📜 변경 이력' section so the user
    can see when each Wave shipped + what was added/removed at each
    step. Sourced from `bot.screener_history.load_history()`.
    """
    import html as _html
    from collections import defaultdict
    from bot.screener_themes import list_domains
    from bot.screener_history import load_history
    ds = list_domains()

    # Group by layer (L1_TREND / L2_SECTOR / L3_INDUSTRY) per user request
    # 2026-05-29: dashboard reorganized by 3-layer model; previous full
    # change-history section collapsed into a single footer line ("최근
    # 추가 N개" — actionable summary instead of chronological log).
    _LAYER_META = [
        ("L1_TREND",    "📈 L1 Trend",   "Cross-cutting cycle 베팅 — 공식 sector 분류 외"),
        ("L2_SECTOR",   "🏢 L2 Sector",  "11 공식 sector (미국 GICS-like)"),
        ("L3_INDUSTRY", "🔬 L3 Industry","각 L2 아래 sub-industry"),
        # AD_HOC = `/screener <자유어>` 5회+ 사용 후 자동 promoted 정식 모듈
        # (bot/screener_freetext.promote_to_module). 사용자 수동 reclassify
        # 전까지 별도 layer 로 노출.
        ("AD_HOC",      "🆕 자유어 promoted", "자유어 5회+ 사용 → 정식 모듈 자동 생성 (수동 reclassify 대기)"),
    ]
    by_layer: dict[str, list[dict]] = defaultdict(list)
    for d in ds:
        by_layer[d.get("layer") or "L1_TREND"].append(d)

    def _render_card(d: dict) -> str:
        slug = d["slug"]
        domain = _html.escape(d["domain"])
        aliases = [a for a in d["aliases"] if a.lower() != slug]
        alias_html = ""
        if aliases:
            chips = "".join(
                f'<span class="alias">{_html.escape(a)}</span>' for a in aliases
            )
            alias_html = f'<div class="aliases">{chips}</div>'
        # Per-slug shortcut commands (`/screener_<slug>`) — single tap
        # fires the right domain run.
        return (
            f'<div class="dom-card">'
            f'<div class="dom-head">'
            f'<code class="slug">/screener_{_html.escape(slug)}</code>'
            f'<span class="dom-name">{domain}</span>'
            f'</div>'
            f'{alias_html}'
            f'</div>'
        )

    sections: list[str] = []
    _layer_css_class = {
        "L1_TREND": "l1",
        "L2_SECTOR": "l2",
        "L3_INDUSTRY": "l3",
        "AD_HOC": "ad_hoc",
    }
    # Per-layer collapsible — L1 (6) default open since smallest, L2/L3
    # (11/48) default closed so initial paint stays light on mobile.
    # User taps the section header to expand. The /domain-search input
    # below applies cross-layer so collapsed sections still surface in
    # the filtered view.
    _layer_default_open = {
        "L1_TREND": True,
        "L2_SECTOR": False,
        "L3_INDUSTRY": False,
        # AD_HOC default open (보통 소수 — 사용자가 promote 흐름 추적 중)
        "AD_HOC": True,
    }
    for layer_key, layer_label, layer_desc in _LAYER_META:
        cards = by_layer.get(layer_key, [])
        if not cards:
            continue
        cards_html = "\n".join(_render_card(d) for d in cards)
        css_class = _layer_css_class.get(layer_key, "")
        open_attr = " open" if _layer_default_open.get(layer_key, False) else ""
        sections.append(
            f'<details class="layer-details {css_class}"{open_attr}>'
            f'<summary class="layer-section {css_class}">'
            f'<h2 class="layer-h">{layer_label} '
            f'<span class="layer-count">({len(cards)}개)</span></h2>'
            f'<p class="layer-desc">{_html.escape(layer_desc)}</p>'
            f'</summary>'
            f'<div class="layer-body">{cards_html}</div>'
            f'</details>'
        )
    body = "\n".join(sections) if sections else (
        '<div class="empty">아직 등록된 도메인이 없습니다.</div>'
    )
    # Filter input — instant client-side filtering across all layers.
    # Forces every <details> open while filter is active so matches in
    # collapsed sections still surface. Clearing the input restores the
    # default open/closed state per layer.
    filter_html = (
        '<div class="filter-bar">'
        '<input id="dom-filter" type="text" '
        'placeholder="🔍 도메인/별칭 검색 (예: 반도체, banks, 휴머노이드)" '
        'autocomplete="off" spellcheck="false">'
        '<button id="dom-filter-clear" type="button" title="초기화">×</button>'
        '</div>'
    )

    # 변경 이력 footer — single line summary (사용자 요청 2026-05-29
    # "변경이력으로 남기지 말고 그냥 3-layer 기준으로 나눠져 ... 추가,
    # 변경, 제거가 있으면 그걸 반영"). Full chronological log replaced
    # by a tight summary of the most recent change: latest ts + added
    # slugs. Initial seed entry suppressed from the footer.
    history = load_history()
    history_html = ""
    if history:
        recent_change = next(
            (e for e in history if not e.get("initial")),
            None,
        )
        if recent_change:
            ts = _html.escape(
                str(recent_change.get("ts", ""))[:16].replace("T", " ")
            )
            added = recent_change.get("added") or []
            removed = recent_change.get("removed") or []
            parts: list[str] = []
            if added:
                parts.append(
                    "추가 "
                    + ", ".join(
                        f'<code>/screener_{_html.escape(s)}</code>' for s in added[:5]
                    )
                    + (f" 외 {len(added) - 5}개" if len(added) > 5 else "")
                )
            if removed:
                parts.append(
                    "제거 "
                    + ", ".join(
                        f'<code>{_html.escape(s)}</code>' for s in removed[:5]
                    )
                    + (f" 외 {len(removed) - 5}개" if len(removed) > 5 else "")
                )
            history_html = (
                '<p class="sub history-footer" style="margin-top:24px;'
                'padding-top:12px;border-top:1px solid var(--border)">'
                f'📜 최근 변경 ({ts} KST) — '
                + " · ".join(parts) +
                f' → 총 {recent_change.get("total_after", 0)}개</p>'
            )
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>📊 Screener 도메인 목록</title>
<script>{_THEME_JS}</script>
<style>{_INDEX_CSS}
.dom-card {{ background:var(--card); border:1px solid var(--border);
  border-left:3px solid var(--accent); border-radius:8px;
  padding:12px 16px; margin-bottom:10px;
  transition:border-left-width 0.1s, box-shadow 0.1s; }}
.dom-card:hover {{ border-left-width:5px;
  box-shadow:0 1px 4px rgba(0,0,0,0.06); }}
:root[data-theme="dark"] .dom-card:hover {{
  box-shadow:0 1px 4px rgba(0,0,0,0.3); }}
.dom-head {{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; }}
.slug {{ background:rgba(14,165,233,0.15); color:var(--accent);
  padding:4px 11px; border-radius:6px; font-size:13px; font-weight:700;
  font-family:'IBM Plex Mono',monospace;
  border:1px solid rgba(14,165,233,0.25); }}
:root[data-theme="dark"] .slug {{
  background:rgba(59,130,246,0.18);
  border-color:rgba(59,130,246,0.3); }}
.dom-name {{ font-size:16px; color:var(--fg); flex:1; min-width:200px;
  font-weight:500; }}
.aliases {{ margin-top:10px; display:flex; gap:6px; flex-wrap:wrap; }}
/* Alias chips — explicit colors per theme so contrast holds on both
   light (washed-out gray on white) and dark (subtle gray on charcoal).
   2026-05-29 사용자 가독성 fix: 라이트 모드에서 alias 가 거의 안 보
   여서 chip 배경 #f1f5f9 (slate-100) + 본문 색 #475569 (slate-600)
   으로 명도 대비 확보. */
.alias {{ background:#e2e8f0; color:#475569;
  padding:3px 9px; border-radius:4px; font-size:11px;
  font-family:'IBM Plex Mono',monospace; font-weight:500; }}
:root[data-theme="dark"] .alias {{ background:rgba(255,255,255,0.07);
  color:#cbd5e1; }}
.hist-entry {{ background:var(--card); border:1px solid var(--border);
  border-radius:8px; padding:10px 14px; margin-bottom:8px; }}
.hist-head {{ display:flex; gap:12px; align-items:center;
  flex-wrap:wrap; font-size:13px; color:var(--fg-soft); margin-bottom:6px; }}
.hist-label {{ font-weight:600; color:var(--fg); }}
.hist-ts {{ font-family:'IBM Plex Mono',monospace; }}
.hist-total {{ margin-left:auto; color:var(--accent); font-weight:600; }}
.hist-row {{ font-size:14px; margin:4px 0; line-height:1.7; }}
.hist-row b {{ color:var(--fg-soft); font-weight:500;
  margin-right:6px; font-size:11px; }}
.hist-add, .hist-rem {{ display:inline-block; margin:2px 4px 2px 0; }}
.hist-add code {{ background:rgba(16,185,129,0.12); color:#10B981;
  padding:2px 8px; border-radius:4px; font-size:11px; }}
.hist-rem code {{ background:rgba(239,68,68,0.12); color:#EF4444;
  padding:2px 8px; border-radius:4px; font-size:11px;
  text-decoration:line-through; }}
/* 3-layer grouping (L1 / L2 / L3) — section headers between domain
   card stacks. Each layer has its own accent strip on the left so the
   user can visually distinguish L1 (sky) / L2 (emerald) / L3 (purple)
   as they scroll. 2026-05-29 가독성 fix. */
.layer-section {{ margin:36px 0 16px;
  padding:18px 0 6px 16px;
  border-top:1px solid var(--border);
  border-left:3px solid var(--accent);
  background:linear-gradient(90deg,
    rgba(14,165,233,0.05) 0%,
    transparent 240px); }}
.layer-section:first-of-type {{ margin-top:24px; border-top:none; padding-top:8px; }}
.layer-section.l1 {{ border-left-color:#0ea5e9; }}
.layer-section.l1 {{ background:linear-gradient(90deg,
  rgba(14,165,233,0.07) 0%, transparent 280px); }}
.layer-section.l2 {{ border-left-color:#10b981; }}
.layer-section.l2 {{ background:linear-gradient(90deg,
  rgba(16,185,129,0.07) 0%, transparent 280px); }}
.layer-section.l3 {{ border-left-color:#8b5cf6; }}
.layer-section.l3 {{ background:linear-gradient(90deg,
  rgba(139,92,246,0.07) 0%, transparent 280px); }}
.layer-section.ad_hoc {{ border-left-color:#f59e0b; }}
.layer-section.ad_hoc {{ background:linear-gradient(90deg,
  rgba(245,158,11,0.08) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.l1 {{
  background:linear-gradient(90deg, rgba(59,130,246,0.10) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.l2 {{
  background:linear-gradient(90deg, rgba(16,185,129,0.10) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.l3 {{
  background:linear-gradient(90deg, rgba(167,139,250,0.10) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.ad_hoc {{
  background:linear-gradient(90deg, rgba(251,191,36,0.12) 0%, transparent 280px); }}
.layer-h {{ font-size:20px; margin:0 0 4px;
  color:var(--fg); font-weight:700; }}
.layer-count {{ color:var(--fg-soft); font-size:14px; font-weight:500;
  margin-left:8px; }}
.layer-desc {{ margin:0 0 14px; color:var(--fg-soft); font-size:13px; }}
.history-footer code {{ background:rgba(16,185,129,0.12);
  color:#059669; padding:1px 6px; border-radius:4px;
  font-size:11px; font-weight:600; }}
:root[data-theme="dark"] .history-footer code {{
  background:rgba(16,185,129,0.18); color:#34d399; }}
/* Collapsible layer sections — L1 default open, L2/L3 closed so
   initial paint stays light on mobile (48 L3 cards 한꺼번에 펼치니
   느리다는 사용자 보고 2026-05-29). 헤더 탭 시 펼침. */
details.layer-details {{ margin:0; }}
details.layer-details summary {{ cursor:pointer; list-style:none;
  user-select:none; }}
details.layer-details summary::-webkit-details-marker {{ display:none; }}
details.layer-details summary::after {{ content:"▾";
  position:absolute; right:18px; top:18px;
  color:var(--fg-soft); font-size:14px;
  transition:transform 0.15s; }}
details.layer-details:not([open]) summary::after {{
  content:"▸"; }}
details.layer-details summary {{ position:relative; }}
.layer-body {{ padding-top:6px; }}
/* Quick filter — instant client-side search across all layer cards.
   Forces every <details open> while typing so matches in collapsed
   sections still surface. */
.filter-bar {{ display:flex; gap:8px; margin:20px 0 16px;
  position:sticky; top:0; z-index:10;
  background:var(--bg); padding:12px 0;
  border-bottom:1px solid var(--border); }}
.filter-bar input {{ flex:1; padding:10px 14px; font-size:14px;
  color:var(--fg); background:var(--card);
  border:1px solid var(--border); border-radius:8px;
  outline:none; transition:border-color 0.12s; }}
.filter-bar input:focus {{ border-color:var(--accent); }}
.filter-bar button {{ padding:10px 16px; font-size:14px;
  background:var(--card); color:var(--fg-soft);
  border:1px solid var(--border); border-radius:8px;
  cursor:pointer; transition:color 0.1s, border-color 0.1s; }}
.filter-bar button:hover {{ color:var(--fg);
  border-color:var(--accent); }}
.dom-card.hidden {{ display:none; }}
.layer-details.hidden {{ display:none; }}
</style>
</head>
<body>
<div class="wrap">
  <p class="sub">
    <a href="index.html">← NOAH 종목 분석</a> ·
    <a href="screener.html">📊 Bottleneck Screener Archive</a>
  </p>
  <h1>📊 Screener 도메인 목록 <span style="color:var(--fg-soft);font-size:16px;font-weight:400">({len(ds)}개 · auto-discovered)</span></h1>
  <p class="sub">텔레그램: <code>/screener_&lt;슬러그&gt;</code> 클릭 한 번으로 즉시 실행 · 별칭은 <code>/screener &lt;별칭&gt;</code> 으로 지원
  · 동일 목록 텔레그램 = <code>/screener_list</code>.</p>
  <p class="sub"><b>3-layer 도메인 모델</b> — L1 Trend (cross-cutting cycle 베팅) · L2 Sector (미국 GICS-like 정식 분류) · L3 Industry (각 L2 sector 의 sub-industry).
  새 도메인 추가는 <code>bot/screener_themes/&lt;slug&gt;.py</code> 모듈 1 개 drop 만으로 본 페이지에 자동 반영.</p>
  {filter_html}
  {body}
  {history_html}
</div>
<script>
(function() {{
  var inp = document.getElementById('dom-filter');
  var clr = document.getElementById('dom-filter-clear');
  if (!inp) return;
  var cards = Array.from(document.querySelectorAll('.dom-card'));
  var details = Array.from(document.querySelectorAll('details.layer-details'));
  // Cache the original default-open state so clearing the filter
  // restores L1 open / L2 L3 closed (instead of leaving them all open).
  var defaultOpen = details.map(function(d) {{ return d.hasAttribute('open'); }});
  // Pre-extract searchable text per card so each keystroke is O(N).
  var cardText = cards.map(function(c) {{
    return (c.textContent || '').toLowerCase().replace(/\\s+/g, ' ');
  }});
  function applyFilter() {{
    var q = (inp.value || '').trim().toLowerCase();
    if (!q) {{
      cards.forEach(function(c) {{ c.classList.remove('hidden'); }});
      details.forEach(function(d, i) {{
        d.classList.remove('hidden');
        if (defaultOpen[i]) d.setAttribute('open', '');
        else d.removeAttribute('open');
      }});
      return;
    }}
    cards.forEach(function(c, i) {{
      var match = cardText[i].indexOf(q) >= 0;
      c.classList.toggle('hidden', !match);
    }});
    // Open every <details> with at least one visible card; hide ones
    // that have no match at all so the user can scan results faster.
    details.forEach(function(d) {{
      var any = d.querySelectorAll('.dom-card:not(.hidden)').length > 0;
      d.classList.toggle('hidden', !any);
      if (any) d.setAttribute('open', '');
    }});
  }}
  inp.addEventListener('input', applyFilter);
  clr.addEventListener('click', function() {{
    inp.value = '';
    applyFilter();
    inp.focus();
  }});
}})();
</script>
</body>
</html>
"""


def regenerate_screener_index() -> None:
    """Scan screener archive + memory, write screener.html + screener_
    domains.html under ARCHIVE_ROOT. Called from screener.py after a
    successful run AND from auto_resolve.py after the 5/15/30d outcome
    pass. All errors swallowed."""
    try:
        runs = _load_screener_runs()
        outcomes = _load_screener_outcomes()
        html = _render_screener_page(runs, outcomes)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "screener.html").write_text(html, encoding="utf-8")
        log.info("dashboard: screener.html regenerated (%d runs, %d outcomes)",
                 len(runs), len(outcomes))
    except Exception as exc:
        log.warning("dashboard: screener regen failed: %s", exc)
    # Domain registry page — separate try so a render error here doesn't
    # block the main screener archive write above. Records a history
    # snapshot BEFORE rendering so any new domains land in the page's
    # '📜 변경 이력' section on the same regen pass.
    try:
        from bot.screener_history import record_snapshot
        record_snapshot()
    except Exception as exc:
        log.warning("dashboard: screener_history record failed: %s", exc)
    try:
        domains_html = _render_screener_domains_page()
        (ARCHIVE_ROOT / "screener_domains.html").write_text(
            domains_html, encoding="utf-8"
        )
        log.info("dashboard: screener_domains.html regenerated")
    except Exception as exc:
        log.warning("dashboard: screener_domains regen failed: %s", exc)


# ── Daily Byte archive view ──────────────────────────────────────────────
# 장 마감 후 KR 수급 브리프 (bot/daily_kr_flow.py). Daily(평일 19:00) +
# Weekly(일 22:00, SV weekly 와 동일 시각) run 을 한 페이지에 date-그룹
# 카드로 렌더. screener.html 의 theme(_SCREENER_CSS)·검색창·🗑️ 휴지통
# UX 를 그대로 mirror — 차이는 카드가 단일 브리프 본문(섹션 분리 없음)
# 이라는 점 + Daily/Weekly kind 뱃지.

_DAILY_BYTE_JS = """
<script>
// Snippet-highlight search + 🗑️ delete (mirrors Bottleneck Screener UX).
// Reuses scr-* element ids + .card/.day classes from _SCREENER_CSS. Daily
// Byte cards carry a single 'brief' section, so the search indexes brief
// lines; clicking a snippet opens + scrolls to the source card.
(function() {
  const inp = document.getElementById('scr-search');
  const clr = document.getElementById('scr-clear');
  const sts = document.getElementById('scr-status');
  const emp = document.getElementById('scr-empty');
  const snp = document.getElementById('scr-snippets');
  const cards = Array.from(document.querySelectorAll('.card'));
  const dayGroups = Array.from(document.querySelectorAll('details.day'));
  if (!inp) return;
  const total = cards.length;
  const MAX_SNIPPETS = 80;

  const cardData = cards.map(function(c) {
    let lines = [];
    try { lines = JSON.parse(c.dataset.lines || '[]'); } catch (e) {}
    const t = c.querySelector('.domain');
    const titleTxt = t ? t.textContent.trim() : '';
    if (titleTxt) lines.unshift({sec: 'title', txt: titleTxt});
    return {card: c, lines: lines};
  });

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function(ch) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch];
    });
  }
  function highlight(text, q) {
    const safe = escapeHtml(text);
    if (!q) return safe;
    const lt = text.toLowerCase(), lq = q.toLowerCase();
    let out = '', last = 0, idx;
    while ((idx = lt.indexOf(lq, last)) >= 0) {
      out += escapeHtml(text.slice(last, idx));
      out += '<mark>' + escapeHtml(text.slice(idx, idx + q.length)) + '</mark>';
      last = idx + q.length;
    }
    out += escapeHtml(text.slice(last));
    return out;
  }

  function showCardsMode() {
    snp.style.display = 'none'; snp.innerHTML = ''; emp.style.display = 'none';
    for (const c of cards) { c.style.display = ''; c.open = (c.dataset.defaultOpen === 'true'); c.classList.remove('hit-flash'); }
    for (const d of dayGroups) d.style.display = '';
    sts.textContent = '총 ' + total + '건의 Daily Byte 브리프';
  }

  function showSnippetsMode(q) {
    for (const c of cards) c.style.display = 'none';
    for (const d of dayGroups) d.style.display = 'none';
    const ql = q.toLowerCase();
    const hits = [];
    for (const cd of cardData) {
      for (const ln of cd.lines) {
        if ((ln.txt || '').toLowerCase().indexOf(ql) >= 0) {
          hits.push({card: cd.card, txt: ln.txt});
          if (hits.length >= MAX_SNIPPETS) break;
        }
      }
      if (hits.length >= MAX_SNIPPETS) break;
    }
    if (hits.length === 0) {
      snp.style.display = 'none'; emp.style.display = 'block';
      sts.textContent = '0건 매칭 (검색: "' + q + '")';
      return;
    }
    emp.style.display = 'none';
    const counts = new Map(); const parts = [];
    for (const h of hits) {
      const cid = h.card.id; counts.set(cid, (counts.get(cid) || 0) + 1);
      const dateAttr = h.card.dataset.date || '';
      const tEl = h.card.querySelector('.domain');
      const tTxt = tEl ? tEl.textContent.trim() : '';
      parts.push('<div class="snippet" data-target="' + cid + '">' +
        '<div class="snippet-meta"><span class="snippet-card">' +
        escapeHtml(tTxt) + ' · ' + escapeHtml(dateAttr) + '</span></div>' +
        '<div class="snippet-text">' + highlight(h.txt, q) + '</div></div>');
    }
    snp.innerHTML = parts.join(''); snp.style.display = 'block';
    const cap = hits.length >= MAX_SNIPPETS ? ' (상위 ' + MAX_SNIPPETS + '건)' : '';
    sts.textContent = hits.length + '개 라인 · ' + counts.size + '개 카드 매칭' + cap + ' (검색: "' + q + '")';
  }

  function applyFilter() {
    const q = (inp.value || '').trim();
    if (!q) { showCardsMode(); return; }
    showSnippetsMode(q);
  }

  snp.addEventListener('click', function(ev) {
    const sn = ev.target.closest('.snippet'); if (!sn) return;
    const tgt = sn.dataset.target; const card = document.getElementById(tgt); if (!card) return;
    for (const c of cards) { c.style.display = ''; c.open = false; c.classList.remove('hit-flash'); }
    for (const d of dayGroups) { d.style.display = ''; d.open = false; }
    const dayG = card.closest('details.day'); if (dayG) dayG.open = true;
    card.open = true; snp.style.display = 'none';
    card.classList.add('hit-flash');
    const tgtEl = card.querySelector('.card-h') || card;
    setTimeout(function() { tgtEl.scrollIntoView({behavior: 'smooth', block: 'center'}); }, 50);
    setTimeout(function() { card.classList.remove('hit-flash'); }, 2400);
  });

  inp.addEventListener('input', applyFilter);
  clr.addEventListener('click', function() { inp.value = ''; showCardsMode(); inp.focus(); });
})();

document.querySelectorAll('.del-btn').forEach(function(btn) {
  btn.addEventListener('click', function(ev) {
    ev.stopPropagation(); ev.preventDefault();
    const card = btn.closest('.card'); if (!card) return;
    const date = card.dataset.date; const filename = card.dataset.filename;
    if (!date || !filename) return;
    if (!confirm('📊 ' + date + ' / ' + filename + ' Daily Byte 기록을 삭제할까요?')) return;
    btn.disabled = true; btn.textContent = '⏳';
    fetch('api/daily_byte_delete', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date: date, filename: filename})
    }).then(function(r) { return r.json().then(function(d) { return {status: r.status, body: d}; }); })
      .then(function(res) {
        if (res.status === 200 && res.body && res.body.ok) {
          card.style.transition = 'opacity 0.2s'; card.style.opacity = '0';
          setTimeout(function() { card.remove(); }, 200);
        } else {
          alert('삭제 실패: ' + (res.body && res.body.error || res.status));
          btn.disabled = false; btn.textContent = '🗑️';
        }
      }).catch(function(err) {
        alert('삭제 실패: ' + err); btn.disabled = false; btn.textContent = '🗑️';
      });
  });
});
</script>
</body></html>
"""


def _load_daily_byte_runs() -> list[dict]:
    """Scan ~/.tradingagents/daily_byte_archive/YYYY-MM-DD/*.json → run
    dicts newest-first. Each: {ts, date, body, cost_krw, elapsed_sec,
    kind, _path, _date, _filename}. All errors swallowed per-file."""
    import json as _json
    runs: list[dict] = []
    if not _DAILY_BYTE_ARCHIVE_DIR.exists():
        return runs
    try:
        for date_dir in sorted(_DAILY_BYTE_ARCHIVE_DIR.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for json_file in sorted(date_dir.iterdir(), reverse=True):
                if not json_file.name.endswith(".json"):
                    continue
                try:
                    with open(json_file, encoding="utf-8") as f:
                        rec = _json.load(f)
                    rec["_path"] = str(json_file)
                    rec["_date"] = date_dir.name
                    rec["_filename"] = json_file.name
                    runs.append(rec)
                except Exception as exc:
                    log.warning("dashboard: daily_byte load %s failed: %s", json_file, exc)
    except Exception as exc:
        log.warning("dashboard: daily_byte archive scan failed: %s", exc)
    return runs


def _render_daily_byte_page(runs: list[dict]) -> str:
    """Render daily_byte.html — date-grouped brief cards. Reuses
    _SCREENER_CSS (theme + card + search-bar + snippet styles) and the
    same scr-* element ids so the look matches the screener archive."""
    import html as _html
    import json as _json_db
    from collections import defaultdict
    from datetime import datetime as _dt_db, timezone as _tz_db, timedelta as _td_db

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_date[r.get("_date", "")].append(r)

    total_runs = len(runs)
    total_cost_krw = sum(r.get("cost_krw", 0) or 0 for r in runs)
    weekly_n = sum(1 for r in runs if r.get("kind") == "weekly")

    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="index.html">← NOAH 종목 분석</a>
    · <a href="screener.html">📊 Bottleneck Screener</a>
    · <a href="http://34.50.23.221:8002/dashboard" target="_blank" rel="noopener">📈 Standard View</a>
    · <a href="http://34.50.23.221:8765/dashboard/" target="_blank" rel="noopener">{_KR_FLAG_SVG} 한국 수출입 데이터</a>
  </div>
  <h1>📊 Daily Byte — Archive</h1>
  <p class="sub">장 마감 후 KR 수급 브리프 · 평일 19:00 Daily + 일 22:00 Weekly (KST) · 수급 데이터 관찰(교육·정보), 투자 권유 아님</p>

  <div class="stats">
    <div class="stat"><div class="stat-v">{total_runs}</div><div class="stat-l">총 브리프</div></div>
    <div class="stat"><div class="stat-v">₩{total_cost_krw:,.0f}</div><div class="stat-l">누적 비용</div></div>
    <div class="stat"><div class="stat-v">{weekly_n}</div><div class="stat-l">Weekly 종합</div></div>
  </div>

  <div class="search-bar">
    <input id="scr-search" type="text" placeholder="종목 / 섹터 / 본문 검색 (예: 삼성전자, 외국인, 원전, 로테이션)" autocomplete="off" spellcheck="false">
    <button id="scr-clear" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="scr-status" class="status-line">총 {total_runs}건의 Daily Byte 브리프</p>
  <div id="scr-snippets" class="snippets" style="display:none"></div>
  <div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>
""")

    if not runs:
        parts.append("""
  <div class="empty">
    아직 Daily Byte 기록이 없습니다. 평일 19:00 KST 자동 생성됩니다.
  </div>
</div></body></html>""")
        return "".join(parts)

    _today_kst = _dt_db.now(_tz_db(_td_db(hours=9))).date().isoformat()
    for date in sorted(by_date.keys(), reverse=True):
        day_open = " open" if date == _today_kst else ""
        day_count = len(by_date[date])
        parts.append(
            f'<details class="day"{day_open}>'
            f'<summary class="day-head">'
            f'<span>📅 {_html.escape(date)}</span>'
            f'<span class="count">{day_count}건</span>'
            f'</summary>'
            f'<div class="day-body">'
        )
        for r in by_date[date]:
            raw_ts = r.get("ts") or ""
            ts_clock = raw_ts.split("T", 1)[1][:5] if "T" in raw_ts else ""
            ts = _html.escape(ts_clock)
            cost = r.get("cost_krw", 0) or 0
            elapsed = r.get("elapsed_sec", 0) or 0
            kind = r.get("kind", "daily")
            is_weekly = kind == "weekly"
            kind_badge = "📅 Weekly" if is_weekly else "📊 Daily"
            title = f"{kind_badge} · {_html.escape(date)}"
            # Body is already Telegram-safe HTML (<b>/<i> only) from
            # daily_kr_flow._post_process. Strip the leading title line
            # (Python adds '📊 <b>Daily Byte - ...</b>' + <i>subtitle</i>)
            # so the card doesn't double up on the heading.
            body = (r.get("body") or "").strip()
            # 수평선/구분선 줄 제거 (render 시점 — strip-fix 이전에 아카이브된
            # 옛 run 의 '---' / '--- / ---' 도 소급 정리). 단어문자 없이 대시류
            # 2+ 만 있는 줄 + 그로 인한 연속 빈 줄.
            body = re.sub(r"(?m)^[^\w\n<]*[-*_]{2,}[^\w\n<]*$", "", body)
            body = re.sub(r"\n{3,}", "\n\n", body).strip()

            # Per-line snippet index for search (sec='brief'). Strip tags
            # for the searchable text so '<b>' noise doesn't pollute hits.
            plain = re.sub(r"<[^>]+>", "", body)
            card_lines: list[dict] = []
            for ln in plain.splitlines():
                s = ln.strip()
                if len(s) >= 3:
                    card_lines.append({"sec": "brief", "txt": s[:300]})
            if len(card_lines) > 200:
                card_lines = card_lines[:200]
            search_attr = _html.escape(plain.lower()[:6000])
            lines_attr = _html.escape(_json_db.dumps(card_lines, ensure_ascii=False))

            filename = _html.escape(r.get("_filename", ""))
            day_card_count = len(by_date[date])
            card_default_open = (date == _today_kst and day_card_count == 1)
            card_open_attr = " open" if card_default_open else ""
            card_id = f"card-{_html.escape(r.get('_date',''))}-{filename}".replace(".", "_")
            # 인포그래픽 이미지 (archive/ 기준 상대경로) — 있으면 카드 상단 임베드
            png_rel = (r.get("png") or "").strip()
            img_html = ""
            if png_rel and re.match(r"^daily_byte_img/[\w.\-]+\.png$", png_rel):
                img_html = (f'<img class="db-info" src="{_html.escape(png_rel)}" '
                            f'alt="Daily Byte 인포그래픽" loading="lazy" '
                            f'style="width:100%;max-width:680px;border-radius:10px;'
                            f'margin:8px auto 14px;display:block">')

            parts.append(f"""
  <details class="card"{card_open_attr} id="{card_id}" data-date="{_html.escape(r.get('_date',''))}" data-filename="{filename}" data-search="{search_attr}" data-lines="{lines_attr}" data-default-open="{'true' if card_default_open else 'false'}">
    <summary class="card-h">
      <span class="card-toggle">▸</span>
      <span class="domain">{title}</span>
      <span class="meta">⏱ {ts} · ₩{cost:,.1f} · {elapsed:.0f}s</span>
      <button class="del-btn" type="button" title="이 Daily Byte 기록 삭제">🗑️</button>
    </summary>
    <div class="card-body">
      {img_html}
      <div class="analysis-sec"><div class="analysis-b" data-section="brief">{body}</div></div>
    </div>
  </details>
""")
        parts.append('</div></details>')

    parts.append("</div>")
    parts.append(_DAILY_BYTE_JS)
    return "".join(parts)


def regenerate_daily_byte_index() -> None:
    """Scan daily_byte archive → write daily_byte.html under ARCHIVE_ROOT.
    Called from daily_kr_flow after a run + from _periodic_dashboard_refresh.
    All errors swallowed."""
    try:
        runs = _load_daily_byte_runs()
        html = _render_daily_byte_page(runs)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "daily_byte.html").write_text(html, encoding="utf-8")
        log.info("dashboard: daily_byte.html regenerated (%d runs)", len(runs))
    except Exception as exc:
        log.warning("dashboard: daily_byte regen failed: %s", exc)
