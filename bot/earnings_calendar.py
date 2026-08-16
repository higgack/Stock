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
_KIND_IR_URL = ("https://kind.krx.co.kr/corpgeneral/irschedule.do"
                "?method=searchIRScheduleMain&gubun=iRScheduleCalendar")
# 회사 클릭 → 그 회사 KIND 정보 팝업(회사개요·IR 등). isuCd=6자리 종목코드.
# ⚠️ GET param(isuCd) 실측 미검증 — POST-only 면 빈 팝업일 수 있어 VM 확인.
_KIND_COMPANY_URL = ("https://kind.krx.co.kr/common/companysummary.do"
                     "?method=searchCompanySummary&isuCd=")


def _api_key() -> str:
    return os.getenv("FINNHUB_API_KEY", "").strip()


def _us_name_map() -> dict[str, str]:
    try:
        from bot.us_symbol_names import us_symbol_names
        names = dict(us_symbol_names() or {})
        try:
            from bot.edgar_client import sec_ticker_names
            for tk, nm in (sec_ticker_names() or {}).items():
                names.setdefault(tk, nm)
        except Exception:
            pass
        return names
    except Exception:
        return {}
def fetch_month(year: int, month: int) -> list[dict]:
    """Fetch a full month — 미국(Finnhub 실적) + 한국(KIND IR일정·DART 폴백).

    각 이벤트에 market 필드('us'|'kr') 부착. 한국은 KIND IR일정(실제 개최일·
    미래 포함)을 1차로, 비면 DART(아카이브 + 월-fetch·공시 접수일) 폴백.
    한국이 안 되면 미국만 graceful."""
    events = [dict(e, market="us") for e in _fetch_us_month(year, month)]
    # 한국 IR 일정 — 사용자 정책 2026-06-10: KIND IR일정(실제 미래 개최일)을
    # 1차로, DART(공시 접수일·과거)는 폴백. KIND 가 미래 일정까지 줘서 DART 가
    # '완료된 일정만' 나오던 문제 해소. 둘은 날짜 의미가 달라(개최일 vs 접수일)
    # 섞지 않고 KIND 가 비었을 때만 DART 사용.
    try:
        from datetime import date as _d
        mprefix = f"{year:04d}-{month:02d}"
        kr: dict[str, dict] = {}

        def _add(it, src):
            code = it.get("code", "")
            url = it.get("url", "#")
            if url == "#" and src == "kind":
                # 회사 클릭 → 그 회사 KIND 정보 팝업(코드 있으면), 없으면 IR일정.
                url = (_KIND_COMPANY_URL + code) if code else _KIND_IR_URL
            key = (url if url not in ("#", _KIND_IR_URL)
                   and not url.startswith(_KIND_COMPANY_URL)
                   else f"{code}-{it.get('date')}-{it.get('name')}")
            kr[key] = {
                "symbol": f"{code}.KS" if code else (it.get("name") or ""),
                "name": it.get("name", ""), "date": it.get("date", ""),
                "hour": "", "market": "kr", "url": url,
                "ir_type": it.get("type", "IR"),
            }

        # 1) KIND IR일정 (실제 개최일·미래 포함)
        try:
            from bot.kind_ir_client import fetch_kind_ir_month
            for it in fetch_kind_ir_month(year, month):
                _add(it, "kind")
        except Exception as exc:
            log.warning("earnings_cal: KIND IR fetch failed: %s", exc)

        # 2) KIND 가 비면 DART 폴백 (공시 접수일)
        if not kr:
            from bot.dart_feed import fetch_kr_earnings_ir, fetch_kr_ir_month
            _today = _d.today()
            _first = _d(year, month, 1)
            _back = max(14, (_today - _first).days + 35)
            for it in fetch_kr_earnings_ir(days_back=_back):
                if it.get("date", "").startswith(mprefix):
                    _add(it, "dart")
            if len(kr) < 3:
                for it in fetch_kr_ir_month(year, month):
                    _add(it, "dart")
        events.extend(kr.values())
    except Exception as exc:
        log.warning("earnings_cal: KR IR merge failed: %s", exc)
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
    names = _us_name_map()
    for e in raw:
        sym = e.get("symbol", "")
        if not sym:
            continue
        result.append({
            "symbol": sym,
            "name": names.get(sym, ""),
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
.cal-entry{color:var(--text);margin-bottom:2px;white-space:normal;
word-break:break-word;line-height:1.35}
.cal-entry .nm{color:var(--muted);font-weight:400;font-size:11px}
.cal-entry .sym{font-weight:600}
.cal-entry .hour{color:var(--muted);margin-left:2px}
.cal-more{color:var(--muted);font-size:11px;margin-top:2px;cursor:pointer}
.cal-more:hover{color:var(--accent);text-decoration:underline}
/* 종목명 색 — 흰색(var(--text))은 가독성 별로(사용자 2026-06-10) → 차분한
   파랑으로(라이트/다크 모두 대비 확보, 링크임을 시사). 시장 구분은 탭이 담당. */
.cal-entry.kr .sym{color:#6ea8fe}
.cal-entry.us .sym{color:#6ea8fe}
[data-theme="light"] .cal-entry.kr .sym{color:#1d6fe0}
[data-theme="light"] .cal-entry.us .sym{color:#1d6fe0}
.cal-entry a:hover .sym{color:var(--accent);text-decoration:underline}
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


# 캘린더 intl 시장 — 위젯과 동일 소스(market_overview.fetch_earnings_calendar_intl,
# yfinance .calendar). 사용자 2026-06-13 '캘린더에도 다른나라들'. (cal_key → (mkt, 라벨)).
# HK 를 TW 앞으로 (사용자 2026-06-14 '대만·홍콩 순서' — market.html 통일).
_INTL_CAL_MAP = {"jp": ("JP", "🇯🇵 일본"), "hk": ("HK", "🇭🇰 홍콩"),
                 "cn": ("CN_A", "🇨🇳 중국"), "tw": ("TW", "🇹🇼 대만")}


def _market_toggle(year: int, month: int, market: str,
                   intl_avail: dict | None = None) -> str:
    """시장 전환 버튼 — 달력이 시장별로 바뀜(서버). 한국/미국 + 데이터 있는
    intl(JP/TW/HK; CN 은 yfinance 커버리지 0이면 자동 숨김 — 사용자 '내용없으면')."""
    ym = f"{year:04d}-{month:02d}"
    avail = intl_avail or {}
    def _b(m: str, lbl: str) -> str:
        act = " active" if m == market else ""
        return (f'<a class="mkt-btn{act}" '
                f'href="?month={ym}&amp;market={m}">{lbl}</a>')
    btns = [_b("kr", "🇰🇷 한국 (IR)"), _b("us", "🇺🇸 미국 (실적)")]
    # JP/TW/HK/CN 도 미국처럼 '(실적)' 라벨 (사용자 2026-06-14). _INTL_CAL_MAP 은
    # 페이지 제목에서 ' 실적' 을 따로 붙이므로(중복 방지) 토글 버튼에서만 붙인다.
    for m, (_mk, lbl) in _INTL_CAL_MAP.items():
        if avail.get(m):
            btns.append(_b(m, lbl + " (실적)"))
    return '<div class="mkt-toggle">' + "".join(btns) + '</div>'


def render_page(year: int, month: int, market: str = "kr") -> str:
    """Render the earnings calendar — market='kr'(KIND IR일정·DART 폴백) | 'us'(Finnhub 실적)."""
    try:
        # 1차 NASDAQ Trader(정규 거래소 ~8천, 이름 깔끔) + 2차 SEC company_
        # tickers(보고기업 ~1만, ADR/OTC 보고기업 포함 — HFUS 류 보완, 사용자
        # 2026-06-11). NASDAQ Trader 우선, 없는 티커만 SEC title 로 채움.
        from bot.us_symbol_names import us_symbol_names
        _us_names = dict(us_symbol_names() or {})
        try:
            from bot.edgar_client import sec_ticker_names
            for _tk, _nm in (sec_ticker_names() or {}).items():
                _us_names.setdefault(_tk, _nm)
        except Exception:
            pass
        if not _us_names:
            from bot.finviz_client import _sp500_names
            _us_names = _sp500_names() or {}
    except Exception:
        _us_names = {}
    market = market if market in ("kr", "us", "jp", "tw", "cn", "hk") else "kr"
    # intl 가용성 — cache_only(동기 스캔 0·페이지 hang 방지). 데이터 있는 시장만
    # 토글 노출. 캐시는 market.html 백그라운드 갱신 + 아침 pre-warm 이 데움.
    intl_avail: dict[str, bool] = {}
    try:
        from bot.market_overview import fetch_earnings_calendar_intl as _fei
        for _ck, (_mk, _l) in _INTL_CAL_MAP.items():
            intl_avail[_ck] = bool(_fei(_mk, cache_only=True))
    except Exception:
        pass
    if market in _INTL_CAL_MAP and not intl_avail.get(market):
        market = "kr"          # 선택 intl 미가용 → 한국 폴백
    if market in _INTL_CAL_MAP:
        _mprefix = f"{year:04d}-{month:02d}"
        try:
            from bot.market_overview import fetch_earnings_calendar_intl as _fei
            events = [dict(e, market=market)
                      for e in _fei(_INTL_CAL_MAP[market][0], cache_only=True)
                      if str(e.get("date", "")).startswith(_mprefix)]
        except Exception:
            events = []
    else:
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
                _mkt = e.get("market", "us")
                is_kr = _mkt == "kr"
                is_intl = _mkt in _INTL_CAL_MAP   # jp/tw/cn/hk
                # 한국=종목명 / intl=티커+(한글명) / 미국=티커(회사명) — 사용자
                # 2026-06-14 'intl 도 티커뒤에 ()'. 이름 미확보 시 티커만.
                if is_kr:
                    label = _html.escape(e.get("name") or sym)
                elif is_intl:
                    _nm = e.get("name") or ""
                    label = (f'{sym}<span class="nm">({_html.escape(_nm)})</span>'
                             if _nm and _nm != str(e.get("symbol", "")) else sym)
                else:
                    raw_sym = str(e.get("symbol", ""))
                    nm = (_us_names.get(raw_sym)
                          or _us_names.get(raw_sym.replace(".", "-")) or "")
                    label = (f'{sym}<span class="nm">({_html.escape(nm)})</span>'
                             if nm else sym)
                mcls = "kr" if is_kr else "us"   # intl 은 us 스타일(파랑 sym·lookup 링크)
                if is_kr:
                    # 한국: 회사 클릭 → IR 일정 원문(KIND / DART 폴백, 분석페이지 아님).
                    # 종류(IR)는 텍스트 배지로 표기.
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

    # '실제 적용' 데이터 시각 — 활성 시장의 월 캐시 mtime (사용자 2026-06-11,
    # 업종등락·신고저와 동일 'ts 기준' 표기). 캐시 부재 시 생략.
    _ts = ""
    try:
        from datetime import datetime as _dtm, timezone as _tzn
        if market == "us":
            _p = _CACHE_DIR / f"{year:04d}-{month:02d}.json"
        else:
            from bot.kind_ir_client import _CACHE as _KC
            _p = _KC / f"{year:04d}-{month:02d}_v2.json"
        if _p.exists():
            # 명시적 KST — 서버 로컬타임 의존 제거 (모든 표기 한국시간)
            _ts = _dtm.fromtimestamp(
                _p.stat().st_mtime, tz=_tzn(timedelta(hours=9))
            ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    _ts_sfx = f" · {_ts} 기준" if _ts else ""

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>실적 캘린더</title>
{_CSS}
</head><body>
<a class="back-link" href="market.html">← 홈으로</a>
<div style="font-size:11px;letter-spacing:2px;color:var(--muted);margin-bottom:4px">EARNINGS CALENDAR</div>
<h1>실적 캘린더</h1>
<div class="subtitle">한국시간 기준 일정. <b>시장 버튼</b>으로 달력이 시장별로 전환됩니다. 한국=<b>KIND IR일정</b>(실제 개최일·미래 포함, 클릭 시 KIND — DART 폴백), 미국=Finnhub 실적(장전 BMO / 장후 AMC), 일본·홍콩·대만=yfinance 확정 실적일(주요종목·한글명·향후 90일, 종목 클릭 시 분석). <b>+N 더보기(또는 날짜) 클릭</b>으로 그날 전체 펼침.</div>
{_market_toggle(year, month, market, intl_avail)}
<div class="cal-header">
  <h2>{month_kr}</h2>
  <span class="cnt">{({'kr': '🇰🇷 한국 IR', 'us': '🇺🇸 미국 실적'}.get(market) or (_INTL_CAL_MAP.get(market, ('', '🌐'))[1] + ' 실적'))} {total}건</span>
</div>
<div class="month-nav">{nav_html}</div>
<div class="cal-grid">{grid}</div>
<div style="margin-top:16px;font-size:11px;color:var(--muted)">한국 KIND IR일정(DART 폴백) · 미국 Finnhub 실적 · 일본·홍콩·대만 yfinance 확정 실적일(주요종목) · 장전=BMO / 장후=AMC{_ts_sfx}</div>
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
