"""Earnings calendar page — Finnhub monthly view.

Renders a standalone calendar page showing upcoming/past earnings by
date, with monthly navigation. Data from Finnhub free tier (same API
key as finnhub_client.py). 6h disk cache per month.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("bot.earnings_calendar")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "earnings_cal"
_CACHE_TTL_SEC = 6 * 3600


def _api_key() -> str:
    return os.getenv("FINNHUB_API_KEY", "").strip()


def fetch_month(year: int, month: int) -> list[dict]:
    """Fetch earnings for a full month from Finnhub. 6h cache."""
    key = _api_key()
    if not key:
        return []

    tag = f"{year:04d}-{month:02d}"
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / f"{tag}.json"
    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime < _CACHE_TTL_SEC:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    first = date(year, month, 1)
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)

    url = (
        f"https://finnhub.io/api/v1/calendar/earnings"
        f"?from={first.isoformat()}&to={last.isoformat()}&token={key}"
    )
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        raw = resp.json().get("earningsCalendar", [])
    except Exception as exc:
        log.warning("earnings_cal: fetch failed for %s: %s", tag, exc)
        return []

    result = []
    for e in raw:
        sym = e.get("symbol", "")
        if not sym:
            continue
        result.append({
            "symbol": sym,
            "date": e.get("date", ""),
            "hour": e.get("hour", ""),
            "eps_estimate": e.get("epsEstimate"),
            "revenue_estimate": e.get("revenueEstimate"),
            "quarter": e.get("quarter"),
            "year": e.get("year"),
        })

    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return result


def _hour_label(h: str) -> str:
    if h == "bmo":
        return "장전"
    if h == "amc":
        return "장후"
    return ""


_CSS = """
<style>
:root{--bg:#0e1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
--muted:#8b949e;--accent:#58a6ff;--badge:#e6edf3;--badge-text:#0e1117}
@media(prefers-color-scheme:light){
:root{--bg:#fff;--card:#f6f8fa;--border:#d0d7de;--text:#1f2328;
--muted:#656d76;--accent:#0969da;--badge:#1f2328;--badge-text:#fff}}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
background:var(--bg);color:var(--text);padding:24px;max-width:1100px;margin:0 auto}
h1{font-size:28px;font-weight:700;margin-bottom:4px}
.subtitle{color:var(--muted);font-size:13px;margin-bottom:24px;line-height:1.5}
.cal-header{display:flex;align-items:baseline;gap:12px;margin-bottom:16px}
.cal-header h2{font-size:20px;font-weight:600}
.cal-header .cnt{color:var(--muted);font-size:14px}
.month-nav{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:20px;align-items:center}
.month-btn{background:var(--card);border:1px solid var(--border);border-radius:20px;
padding:6px 14px;color:var(--text);font-size:13px;cursor:pointer;text-decoration:none;
transition:background .15s}
.month-btn:hover{background:var(--border)}
.month-btn.active{background:var(--text);color:var(--bg);font-weight:600}
.month-nav-sep{flex:1}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:1px;
background:var(--border);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.cal-hdr{background:var(--card);padding:8px;text-align:center;font-size:12px;
font-weight:600;color:var(--muted)}
.cal-cell{background:var(--bg);padding:10px 8px;min-height:110px;font-size:12px;
vertical-align:top}
.cal-cell.empty{background:var(--card);opacity:.4}
.cal-day{display:flex;align-items:center;gap:6px;margin-bottom:6px}
.cal-day .d{font-size:15px;font-weight:600}
.cal-badge{display:inline-flex;align-items:center;justify-content:center;
background:var(--badge);color:var(--badge-text);font-size:11px;font-weight:700;
min-width:22px;height:22px;border-radius:6px;padding:0 5px}
.cal-entry{color:var(--text);margin-bottom:2px;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}
.cal-entry .sym{font-weight:600}
.cal-entry .hour{color:var(--muted);margin-left:2px}
.cal-more{color:var(--muted);font-size:11px;margin-top:2px}
.cal-entry a{color:inherit;text-decoration:none}
.cal-entry a:hover .sym{text-decoration:underline}
.back-link{display:inline-block;margin-bottom:16px;color:var(--accent);
text-decoration:none;font-size:13px}
.back-link:hover{text-decoration:underline}
.sup{font-size:10px;color:var(--muted);vertical-align:super}
@media(max-width:700px){
.cal-cell{min-height:80px;padding:6px 4px;font-size:11px}
.cal-day .d{font-size:13px}
.cal-badge{font-size:10px;min-width:18px;height:18px}
body{padding:12px}
}
</style>
"""

_MAX_PER_CELL = 3


def render_page(year: int, month: int) -> str:
    """Render the full earnings calendar HTML page for a given month."""
    events = fetch_month(year, month)
    by_date: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_date[e["date"]].append(e)

    total = len(events)
    today = date.today()
    cur = date(year, month, 1)

    # Month navigation: ±2 months
    nav_months: list[date] = []
    for delta in range(-2, 3):
        y, m = year, month + delta
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        nav_months.append(date(y, m, 1))

    nav_html = ""
    for d in nav_months:
        active = "active" if d.year == year and d.month == month else ""
        nav_html += (
            f'<a class="month-btn {active}" '
            f'href="?month={d.year:04d}-{d.month:02d}">'
            f'{d.year:04d}-{d.month:02d}</a>\n'
        )

    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1
    nav_html += '<span class="month-nav-sep"></span>'
    nav_html += f'<a class="month-btn" href="?month={prev_y:04d}-{prev_m:02d}">← 이전</a>\n'
    nav_html += f'<a class="month-btn" href="?month={next_y:04d}-{next_m:02d}">다음 →</a>\n'

    # Build calendar grid
    cal = calendar.Calendar(firstweekday=0)  # Monday first
    weeks = cal.monthdayscalendar(year, month)

    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    grid = "".join(f'<div class="cal-hdr">{w}</div>' for w in weekdays)

    for week in weeks:
        for day_num in week:
            if day_num == 0:
                grid += '<div class="cal-cell empty"></div>'
                continue
            dt_str = f"{year:04d}-{month:02d}-{day_num:02d}"
            day_events = by_date.get(dt_str, [])
            count = len(day_events)

            badge = f'<span class="cal-badge">{count}</span>' if count else ""
            day_head = f'<div class="cal-day"><span class="d">{day_num}</span>{badge}</div>'

            entries = ""
            for e in day_events[:_MAX_PER_CELL]:
                sym = e["symbol"]
                hl = _hour_label(e["hour"])
                hl_span = f' <span class="hour">{hl}</span>' if hl else ""
                entries += (
                    f'<div class="cal-entry">'
                    f'<a href="lookup/{sym}"><span class="sym">{sym}</span>{hl_span}</a>'
                    f'</div>\n'
                )
            overflow = count - _MAX_PER_CELL
            more = f'<div class="cal-more">+{overflow}</div>' if overflow > 0 else ""

            grid += f'<div class="cal-cell">{day_head}{entries}{more}</div>\n'

    month_kr = f"{year}년 {month}월"

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>실적 캘린더 — NOAH</title>
{_CSS}
</head><body>
<a class="back-link" href="market.html">← 홈으로</a>
<div style="font-size:11px;letter-spacing:2px;color:var(--muted);margin-bottom:4px">EARNINGS CALENDAR</div>
<h1>실적 캘린더</h1>
<div class="subtitle">한국시간 기준 다가오는 실적 일정입니다. 컨센서스 EPS·매출과 발표 시각(장 시작 전 BMO / 장 마감 후 AMC)을 함께 표시합니다.</div>
<div class="cal-header">
  <h2>{month_kr}</h2>
  <span class="cnt">{total}건</span>
</div>
<div class="month-nav">{nav_html}</div>
<div class="cal-grid">{grid}</div>
<div style="margin-top:16px;font-size:11px;color:var(--muted)">출처: Finnhub · 장전=BMO(Before Market Open) · 장후=AMC(After Market Close)</div>
</body></html>"""
