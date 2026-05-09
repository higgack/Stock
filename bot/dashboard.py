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
_ISSUE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"리스크 지표 계산 실패"),
     "리스크 지표 계산 실패 (도구)"),
    (re.compile(r"거시 지표 데이터를 가져오지 못했습니다"),
     "거시 지표 fetch 실패 (도구)"),
    (re.compile(r"`?get_risk_metrics`?.*?(?:오류|실패|N/A|사용할 수 없|데이터 없음)",
                re.DOTALL),
     "리스크 지표 도구 사용 안 됨 (모델)"),
    (re.compile(r"`?get_macro_context`?.*?(?:오류|실패|N/A|사용할 수 없|데이터 없음)",
                re.DOTALL),
     "거시 배경 도구 사용 안 됨 (모델)"),
    (re.compile(r"`?get_sector_relative_strength`?.*?(?:오류|실패|사용할 수 없)",
                re.DOTALL),
     "섹터 상대 강도 도구 사용 안 됨 (모델)"),
    (re.compile(r"매크로 컨텍스트.*?(?:오류|도구 오류)"),
     "매크로 컨텍스트 오류 (모델)"),
]


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
            out["resolved"].append({
                "date": date, "ticker": ticker, "rating": rating,
                "raw": fields[3], "alpha": fields[4],
            })
    return out


def _parse_pct(s: str) -> float | None:
    """Parse '+5.0%' / '-3.2%' / 'n/a' → float (5.0 / -3.2 / None)."""
    if not s or s == "n/a":
        return None
    try:
        return float(s.rstrip("%").replace("+", ""))
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
    for r in usage:
        if r.get("type") != "llm_call":
            continue
        ts = r.get("ts")
        if not ts:
            continue
        rec_day = datetime.datetime.fromtimestamp(ts, kst).strftime("%Y-%m-%d")
        cost = r.get("cost_usd", 0) or 0
        if rec_day.startswith(month_prefix):
            month_cost_usd += cost
            m = r.get("model") or "unknown"
            month_cost_by_model[m] = month_cost_by_model.get(m, 0.0) + cost
            if rec_day == today_str:
                today_cost_usd += cost

    # ── recommendation accuracy from memory log ──
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
        if ret is None:
            continue
        if rating in ("buy", "overweight"):
            evaluated += 1
            if ret > 0:
                correct += 1
        elif rating in ("sell", "underweight"):
            evaluated += 1
            if ret < 0:
                correct += 1
        # 'hold' is excluded from the accuracy denominator (no clean
        # directional bet) but still rolls into the average-return number.
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
        sub_parts.append(f"Hold {hold_total}건 (분모 제외)")
    if stats["avg_return"] is not None:
        sub_parts.append(f"평균 수익 {stats['avg_return']:+.2f}%")
    if stats["avg_alpha"] is not None:
        sub_parts.append(f"벤치 {stats['avg_alpha']:+.2f}%p")

    note = "※ 분석 후 5거래일 + 동일 종목 재분석 시 자동 평가"
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
    "Pretendard", "Helvetica Neue", "Segoe UI", sans-serif;
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
  padding: 10px 14px; font-size: 14px; cursor: pointer;
  background: var(--card); color: var(--fg-soft);
  border: 1px solid var(--border); border-radius: 8px;
}
.search-bar button:hover { color: var(--fg); border-color: var(--accent); }
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
.past { color: var(--fg-soft); font-size: 12px; margin-top: 6px; }
.empty {
  color: var(--fg-soft); font-size: 14px; padding: 32px 0; text-align: center;
}
"""


_INDEX_JS = """
(function() {
  const searchEl = document.getElementById('search');
  const clearBtn = document.getElementById('clear-btn');
  const statusEl = document.getElementById('status');
  const emptyEl = document.getElementById('empty-search');
  const cards = Array.from(document.querySelectorAll('.card'));
  const days = Array.from(document.querySelectorAll('details.day'));
  const total = cards.length;

  function applyFilter() {
    const q = (searchEl.value || '').trim().toUpperCase();
    let matched = 0;
    for (const c of cards) {
      const tk = (c.dataset.ticker || '').toUpperCase();
      const visible = !q || tk.includes(q);
      c.style.display = visible ? '' : 'none';
      if (visible) matched++;
    }
    for (const d of days) {
      const anyVisible = Array.from(d.querySelectorAll('.card'))
        .some(c => c.style.display !== 'none');
      d.style.display = anyVisible ? '' : 'none';
      // Auto-expand days that match — easier to spot the result
      if (anyVisible && q) d.open = true;
    }
    if (q) {
      statusEl.textContent = matched + '건 매칭 (검색: "' + q + '")';
      emptyEl.style.display = matched === 0 ? 'block' : 'none';
    } else {
      statusEl.textContent = '총 ' + total + '건의 분석 기록';
      emptyEl.style.display = 'none';
    }
  }

  function syncFromHash() {
    const m = (location.hash || '').match(/^#ticker=([A-Za-z0-9.]+)/);
    if (m) {
      searchEl.value = m[1].toUpperCase();
    }
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
    either failed outright (hard) or completed with placeholder/error
    sections (soft)."""
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

    # ── hard failures (timeouts / exceptions) ──
    if hard_failures:
        hard_html_rows = []
        for f in hard_failures:
            ts = f.get("ts", 0)
            when = datetime.datetime.fromtimestamp(
                ts, tz=datetime.timezone(datetime.timedelta(hours=9))
            ).strftime("%Y-%m-%d %H:%M") if ts else "-"
            ticker = f.get("ticker", "?")
            reason = f.get("reason") or "(사유 없음)"
            hard_html_rows.append(f"""
            <div class="fail-row">
              <span class="ticker">📊 {_html.escape(ticker)}</span>
              <span class="reason">❌ {_html.escape(reason)}</span>
              <span class="when">{_html.escape(when)}</span>
            </div>
            """)
        hard_section = (
            f'<div class="section-head">❌ 분석 자체 실패 ({len(hard_failures)}건)</div>'
            + "".join(hard_html_rows)
        )
    else:
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
                items_html = "".join(
                    f'<div class="item">⚠️ {_html.escape(i)}</div>'
                    for i in issues
                )
                cards_html.append(f"""
                <div class="issue-card">
                  <div class="issue-head">
                    <a class="ticker" href="{href}">📊 {_html.escape(ticker)}</a>
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

    body_empty = not hard_failures and not soft
    if body_empty:
        body = '<div class="empty">기록된 오류가 없습니다. 🎉</div>'
    else:
        body = hard_section + soft_section

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
  <p class="sub">하드 실패 (타임아웃/예외) 와 부분 미완성 (도구 실패·섹션 placeholder) 통합</p>
  {body}
</div>
</body>
</html>
"""


def _count_total_issues(records: list[dict], hard_failures: list[dict]) -> int:
    n = len(hard_failures)
    for r in records:
        if _detect_issues(r):
            n += 1
    return n


def _render_index(records: list[dict]) -> str:
    by_date: dict[str, list[dict]] = {}
    for r in records:
        by_date.setdefault(r["trade_date"], []).append(r)

    if not records:
        body = '<div class="empty">아직 분석 기록이 없습니다.</div>'
    else:
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
                cards.append(f"""
                <div class="card" data-ticker="{_html.escape(ticker)}">
                  <div class="card-row">
                    <a class="ticker" href="{href}">📊 {_html.escape(ticker)}</a>
                    {_badge_html(rating)}
                    <div class="stance">{_html.escape(stance)}</div>
                    <div class="time">{_html.escape(time_str)}</div>
                  </div>
                  {past_html}
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
        body = "".join(sections)

    stats_panel = _render_stats_panel(_compute_stats(records))
    # Headline link to the errors page; count includes hard failures
    # (usage.jsonl) plus archive entries with placeholder/tool issues.
    issue_count = _count_total_issues(records, _read_hard_failures())
    if issue_count > 0:
        errors_link = (
            f' · <a href="errors.html">🚨 오류 / 미완성 {issue_count}건</a>'
        )
    else:
        errors_link = ' · <a href="errors.html">🚨 오류 기록 (없음)</a>'

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
  <p class="sub">카드 클릭 시 전체 리포트 · 검색창에서 종목 필터{errors_link}</p>
  {stats_panel}
  <div class="search-bar">
    <input id="search" type="text" placeholder="종목 검색 (예: NVDA, AMD, GOOGL)" autocomplete="off" autocapitalize="characters" spellcheck="false">
    <button id="clear-btn" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="status" class="status-line">총 {len(records)}건의 분석 기록</p>
  <div id="empty-search" class="empty-search">검색 결과가 없습니다.</div>
  {body}
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
  font-size: 14px; margin: 0; color: var(--fg);
  background: var(--card); padding: 16px; border: 1px solid var(--border);
  border-radius: 8px;
}
pre.report strong { color: var(--fg); }
pre.report h3, pre.report h4, pre.report h5, pre.report h6 {
  margin: 12px 0 4px; font-size: 14px;
}
"""


def _render_detail(rec: dict) -> str:
    ticker = rec.get("ticker", "?")
    date = rec.get("trade_date", "")
    analyzed_at = (rec.get("analyzed_at") or "")[:16].replace("T", " ")
    elapsed = float(rec.get("elapsed_sec", 0) or 0)
    rating = _extract(_RATING_RE, rec.get("summary", "")) or "?"
    summary = rec.get("summary", "") or ""
    full = rec.get("full_report", "") or ""

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>📊 {_html.escape(ticker)} ({_html.escape(date)})</title>
<script>{_THEME_JS}</script>
<style>{_DETAIL_CSS}</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="../index.html">← 아카이브로 돌아가기</a>
  <div class="title-row">
    <h1>📊 {_html.escape(ticker)}</h1>
    {_badge_html(rating)}
  </div>
  <div class="meta">
    분석일: {_html.escape(date)} · 실행 시각: {_html.escape(analyzed_at)} ·
    소요: {elapsed:.1f}초
  </div>
  <section class="report-section">
    <h2>📋 요약</h2>
    {_md_to_html(summary)}
  </section>
  <section class="report-section">
    <h2>📋 전체 리포트</h2>
    {_md_to_html(full)}
  </section>
</div>
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
