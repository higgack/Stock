"""Earnings calendar page — Finnhub monthly view.

Renders a standalone calendar page showing upcoming/past earnings by
date, with monthly navigation. Data from Finnhub free tier (same API
key as finnhub_client.py). 6h disk cache per month.
"""

from __future__ import annotations

import calendar
import html as _html
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
    """Fetch earnings for a full month — 미국(Finnhub) + 한국(yfinance .calendar).

    각 이벤트에 market 필드('us'|'kr') 부착해 캘린더가 색/키로 구분. 한국은
    market_overview.fetch_earnings_calendar_kr(90일·12h캐시) 결과를 해당 월로
    필터링해 병합(별도 fetch 없음). 한국이 안 되면 미국만 graceful."""
    events = [dict(e, market="us") for e in _fetch_us_month(year, month)]
    # 한국 = DART 영업(잠정)실적 + 기업설명회(IR) (사용자 요청). yfinance .calendar
    # 보다 풍부 + 실제 공시 기반. 공시일(rcept_dt)에 배치, 클릭 시 DART 원문.
    try:
        from bot.dart_feed import fetch_kr_earnings_ir
        # 표시 월이 과거여도 채워지도록 충분히 넓은 아카이브 윈도(오늘~월초+여유).
        # DART 피드 5분 타이머가 매일 누적 → 시간이 지나며 과거 월도 자동 채움.
        from datetime import date as _d
        _today = _d.today()
        _first = _d(year, month, 1)
        _back = max(14, (_today - _first).days + 35)
        mprefix = f"{year:04d}-{month:02d}"
        for it in fetch_kr_earnings_ir(days_back=_back):
            d = it.get("date", "")
            if not d.startswith(mprefix):
                continue
            code = it.get("code", "")
            events.append({
                "symbol": f"{code}.KS" if code else (it.get("name") or ""),
                "name": it.get("name", ""), "date": d, "hour": "",
                "market": "kr", "url": it.get("url", "#"),
                "ir_type": it.get("type", ""),
            })
    except Exception as exc:
        log.warning("earnings_cal: KR DART merge failed: %s", exc)
    return events


def _fetch_us_month(year: int, month: int) -> list[dict]:
    """Fetch US earnings for a full month from Finnhub. 6h cache."""
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
:root,[data-theme="dark"]{--bg:#0e1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
--muted:#8b949e;--accent:#58a6ff;--badge:#e6edf3;--badge-text:#0e1117}
[data-theme="light"]{--bg:#fff;--card:#f6f8fa;--border:#d0d7de;--text:#1f2328;
--muted:#656d76;--accent:#0969da;--badge:#1f2328;--badge-text:#fff}
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
.cal-more{color:var(--muted);font-size:11px;margin-top:2px;cursor:pointer}
.cal-more:hover{color:var(--accent);text-decoration:underline}
.cal-entry.kr .sym{color:#2dd4bf}
.cal-entry.us .sym{color:#58a6ff}
.cal-cell.has-extra .cal-day{cursor:pointer}
.cal-cell.has-extra .cal-day:hover .d{text-decoration:underline}
.cal-legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--muted);
margin-bottom:14px;align-items:center}
.cal-legend .sq{display:inline-block;width:10px;height:10px;border-radius:2px;
margin-right:5px;vertical-align:middle}
.mkt-toggle{display:flex;gap:8px;margin-bottom:18px}
.mkt-btn{padding:8px 18px;border-radius:20px;border:1px solid var(--border);
background:var(--card);color:var(--text);font-size:14px;font-weight:600;
text-decoration:none;transition:background .15s}
.mkt-btn:hover{background:var(--border)}
.mkt-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.kir-sec{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin-bottom:20px}
.kir-hd{font-size:15px;font-weight:700;margin-bottom:10px}
.kir-sub{font-size:11px;font-weight:400;color:var(--muted);margin-left:6px}
.kir-k{color:#fff;font-size:10px;padding:1px 5px;border-radius:4px}
.kir-list{display:flex;flex-direction:column;gap:5px;max-height:260px;overflow-y:auto}
.kir-row{display:flex;align-items:center;gap:8px;font-size:12px;line-height:1.4}
.kir-dt{color:var(--muted);flex:0 0 78px;font-variant-numeric:tabular-nums}
.kir-badge{color:#fff;font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;flex:0 0 auto}
.kir-nm{font-weight:600;flex:0 0 130px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kir-nm a{color:inherit;text-decoration:none}.kir-nm a:hover{text-decoration:underline}
.kir-title{color:var(--muted);text-decoration:none;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kir-title:hover{color:var(--accent);text-decoration:underline}
.cal-entry a{color:inherit;text-decoration:none}
.cal-entry a.dart-ln{color:var(--muted);font-size:10px}
.cal-entry a.dart-ln:hover{color:var(--accent);text-decoration:underline}
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


def _market_toggle(year: int, month: int, market: str) -> str:
    """한국/미국 전환 버튼 — 달력 자체가 시장별로 바뀜(서버 사이드)."""
    ym = f"{year:04d}-{month:02d}"
    def _b(m: str, lbl: str) -> str:
        act = " active" if m == market else ""
        return (f'<a class="mkt-btn{act}" '
                f'href="?month={ym}&amp;market={m}">{lbl}</a>')
    return ('<div class="mkt-toggle">'
            + _b("kr", "🇰🇷 한국 (실적·IR)")
            + _b("us", "🇺🇸 미국 (실적)")
            + '</div>')


def render_page(year: int, month: int, market: str = "kr") -> str:
    """Render the earnings calendar — market='kr'(DART 영업잠정실적+IR) | 'us'(Finnhub)."""
    market = market if market in ("kr", "us") else "kr"
    events = [e for e in fetch_month(year, month)
              if e.get("market", "us") == market]
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
    _mq = f"&amp;market={market}"  # 월 이동 시 시장 선택 유지
    for d in nav_months:
        active = "active" if d.year == year and d.month == month else ""
        nav_html += (
            f'<a class="month-btn {active}" '
            f'href="?month={d.year:04d}-{d.month:02d}{_mq}">'
            f'{d.year:04d}-{d.month:02d}</a>\n'
        )

    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1
    nav_html += '<span class="month-nav-sep"></span>'
    nav_html += f'<a class="month-btn" href="?month={prev_y:04d}-{prev_m:02d}{_mq}">← 이전</a>\n'
    nav_html += f'<a class="month-btn" href="?month={next_y:04d}-{next_m:02d}{_mq}">다음 →</a>\n'

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
            for i, e in enumerate(day_events):
                sym = _html.escape(str(e.get("symbol", "")))
                is_kr = e.get("market") == "kr"
                # 한국=종목명(가독), 미국=티커
                label = _html.escape((e.get("name") or sym) if is_kr else sym)
                mcls = "kr" if is_kr else "us"
                if is_kr:
                    # 한국: 회사 클릭 → DART 공시 원문(사용자 정정 — 분석페이지 아님).
                    # 종류(실적/IR)는 텍스트 배지로 표기.
                    tag = _html.escape(e.get("ir_type", ""))
                    hl_span = f' <span class="hour">{tag}</span>' if tag else ""
                    link = _html.escape(e.get("url", "#"))
                    ext = ' target="_blank" rel="noopener"'
                else:
                    hl = _hour_label(e["hour"])
                    hl_span = f' <span class="hour">{hl}</span>' if hl else ""
                    link = f"lookup/{sym}"
                    ext = ""
                hidden = (' cal-extra" style="display:none'
                          if i >= _MAX_PER_CELL else '')
                entries += (
                    f'<div class="cal-entry {mcls}{hidden}">'
                    f'<a href="{link}"{ext}><span class="sym">{label}</span></a>{hl_span}'
                    f'</div>\n'
                )
            overflow = count - _MAX_PER_CELL
            cell_cls = "cal-cell has-extra" if overflow > 0 else "cal-cell"
            more = (f'<div class="cal-more">+{overflow} 더보기</div>'
                    if overflow > 0 else "")

            grid += f'<div class="{cell_cls}">{day_head}{entries}{more}</div>\n'

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
<div class="subtitle">한국시간 기준 실적·IR 일정. <b>한국/미국 버튼</b>으로 달력이 시장별로 전환됩니다. 한국은 DART 의 <b>영업(잠정)실적·기업설명회(IR)</b> 공시(공시일에 배치, <b>클릭 시 DART 원문</b>), 미국은 Finnhub 실적(장전 BMO / 장후 AMC). <b>+N 더보기(또는 날짜) 클릭</b>으로 그날 전체 펼침.</div>
{_market_toggle(year, month, market)}
<div class="cal-header">
  <h2>{month_kr}</h2>
  <span class="cnt">{('🇰🇷 한국' if market == 'kr' else '🇺🇸 미국')} {total}건</span>
</div>
<div class="month-nav">{nav_html}</div>
<div class="cal-grid">{grid}</div>
<div style="margin-top:16px;font-size:11px;color:var(--muted)">한국 DART(영업잠정실적·기업설명회 IR) · 미국 Finnhub · 장전=BMO / 장후=AMC</div>
<script>
(function(){{var h=parseInt(new Intl.DateTimeFormat('en-US',{{timeZone:'Asia/Seoul',hour:'numeric',hour12:false}}).format(new Date()),10)%24;document.documentElement.dataset.theme=(h>=19||h<7)?'dark':'light';}})();
(function(){{
  document.querySelectorAll('.cal-cell.has-extra').forEach(function(cell){{
    var more=cell.querySelector('.cal-more');
    function toggle(){{
      var ex=cell.querySelectorAll('.cal-extra');
      if(!ex.length) return;
      var show=ex[0].style.display==='none';
      ex.forEach(function(x){{x.style.display=show?'':'none';}});
      if(more) more.textContent=show?'접기':('+'+ex.length+' 더보기');
    }}
    var day=cell.querySelector('.cal-day');
    if(day) day.addEventListener('click',toggle);
    if(more) more.addEventListener('click',toggle);
  }});
}})();
</script>
</body></html>"""
