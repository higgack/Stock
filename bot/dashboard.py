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
_BASE_CSS = """
:root {
  --fg: #1f2937; --fg-soft: #6b7280; --bg: #f8fafc; --card: #ffffff;
  --border: #e5e7eb; --accent: #0ea5e9;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #f3f4f6; --fg-soft: #9ca3af; --bg: #0f172a; --card: #1e293b;
    --border: #334155; --accent: #38bdf8;
  }
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
                <div class="card">
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

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🦉 NOAH 주식분석 아카이브</title>
<style>{_INDEX_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>🦉 NOAH 주식분석 아카이브</h1>
  <p class="sub">총 {len(records)}건의 분석 기록 · 카드 클릭 시 전체 리포트</p>
  {body}
</div>
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
<title>📊 {_html.escape(ticker)} ({_html.escape(date)})</title>
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
