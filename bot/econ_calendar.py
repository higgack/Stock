"""경제 캘린더 보드 — CPI/고용동향/GDP/PCE/FOMC 발표일정 (2026-07-26).

claude-trading-skills 저장소 리뷰에서 발견한 갭(매크로 이벤트 캘린더 부재)을
메움 — bot/earnings_calendar.py(개별 종목 실적)와 달리 시장 전체에 영향을
주는 거시 발표일정을 다룬다. 무료 FRED release-dates API 사용, board 모듈
패턴(bot/fred_boards.py 의 공용 CSS/nav/theme_head, bot/market_timing.py 가
이미 재사용 중인 그 패턴 그대로) 재사용 — 신규 CSS 없음.

release_id 를 숫자로 하드코딩하지 않고 FRED 공식 release 명 부분일치 검색
(fred_client.find_release_id)으로 조회 — release_id 는 문서마다 다르게
인용되어 오기 위험이 있어(사용자 프로젝트 '추측보고 금지' 원칙), 검증
가능한 이름 검색이 숫자 암기보다 안전. 매치 없으면 그 항목만 '조회 실패'로
표시(전체 재생성은 항상 성공, graceful).

현재는 US 발표(CPI/고용동향/GDP/PCE/FOMC/ISM PMI — 전부 FRED 가 커버하는
미국 연방 지표) 중심. KR/JP 는 FRED 가 개별 발표일정을 세밀하게 제공하지
않아(시리즈 관측치는 있어도 release-dates 캘린더가 얇음) 이번 배치엔
미포함 — 향후 한국은행/일본은행 공식 일정 소스가 확인되면 동일 구조로
확장 가능(market gate 아닌 데이터소스 공백, universal 원칙 예외 아님).

2026-07-26 사용자 추천 4건 검토 결과:
- ✅ **실제치(actual) 오버레이** — 과거 발표일의 FRED 시리즈 관측값을 붙임
  (CPIAUCSL/PAYEMS/GDP/PCEPI, 전부 수십년 안정적으로 인용되는 FRED 표준
  니모닉 — release_id 와 달리 문서마다 다르게 인용될 위험이 낮아 하드코딩).
- ✅ **ISM 제조업/비제조업 PMI** — 기존 이름검색(find_release_id) 메커니즘
  그대로 재사용해 항목만 추가(FRED 카탈로그에 없으면 자동으로 기존
  '조회 실패' graceful 경로 — 신규 분기 불요, 위험 0).
- ✅ **메가테크 실적일(AI/반도체/빅테크 24종)** — 이미 있는
  bot.earnings_calendar._fetch_us_month(Finnhub, 6h 캐시)를 재사용해
  워치리스트만 필터 — 신규 API 호출 없음.
- ❌ **QRA(미 재무부 분기 리펀딩 발표)** — 미구현. FRED release-dates 에
  없고(재무부 보도자료 이벤트라 FRED가 추적하는 '데이터 릴리스' 개념이
  아님), 정확한 일정을 검증할 무료 소스가 이 세션엔 없어 '대략 2월/5월/
  8월/11월 초'식 근사 날짜를 넣는 건 '추측보고 금지' 원칙 위반 —
  정확한 일정은 treasurydirect.gov/home.treasury.gov 재무부 공지 직접
  확인 권장(하단 가이드에 안내만, 캘린더 항목으로는 미표시).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger("bot.econ_calendar")

_RELEASES = [
    {"key": "cpi", "label": "🛒 CPI (소비자물가지수)", "search": "Consumer Price Index"},
    {"key": "jobs", "label": "💼 고용동향 (Employment Situation)", "search": "Employment Situation"},
    {"key": "gdp", "label": "📈 GDP (국내총생산)", "search": "Gross Domestic Product"},
    {"key": "pce", "label": "💰 PCE (개인소비지출)", "search": "Personal Income and Outlays"},
    {"key": "fomc", "label": "🏛️ FOMC", "search": "FOMC"},
    {"key": "ism_mfg", "label": "🏭 ISM 제조업 PMI", "search": "ISM Manufacturing"},
    {"key": "ism_svc", "label": "🏢 ISM 비제조업 PMI", "search": "ISM Non-Manufacturing"},
]

# 과거 발표일의 실제치(actual) 오버레이용 FRED 시리즈 매핑(2026-07-26 사용자
# 추천) — 컨센서스(예측치)는 설문 기반 유료 데이터라 이 세션엔 소스가 없어
# 미포함(문서화된 한계, 하단 렌더에 '컨센서스 없음' 명시) — 실제치만 제공.
# 이 니모닉들은 release_id 와 달리 수십년 안정적으로 통용되는 FRED 표준
# 코드라 하드코딩 위험이 낮음(release_id 이슈와는 다른 리스크 등급).
_SERIES_FOR_ACTUAL = {
    "cpi": "CPIAUCSL", "jobs": "PAYEMS", "gdp": "GDP", "pce": "PCEPI",
}

# AI/반도체/빅테크 실적 워치리스트 — 4종(2026-07-26 최초) → 20종 추가(사용자
# 2026-07-26 확장). GEV(GE Vernova)·CAT(Caterpillar) 는 순수 빅테크는
# 아니지만 AI 데이터센터 전력/건설 인프라 익스포저로 사용자가 지정.
_MEGATECH_WATCHLIST = (
    "MSFT", "NVDA", "TSM", "ASML",
    "AAPL", "GOOG", "AMZN", "AVGO", "META", "TSLA", "MU", "AMD", "INTC",
    "AMAT", "CAT", "LRCX", "ORCL", "NFLX", "DELL", "ARM", "KLAC", "GEV",
    "PANW", "TXN",
)


def upcoming_and_recent(dates: list, today: str, *, past_days: int = 14) -> dict:
    """발표일 목록(YYYY-MM-DD, 오름차순) → {next, recent} — 순수함수(테스트용).
    next=오늘 이후 가장 가까운 예정일(오늘 포함) 또는 None.
    recent=[오늘 기준 past_days일 내 과거 발표일들](오늘 미포함)."""
    if not dates:
        return {"next": None, "recent": []}
    t = date.fromisoformat(today)
    cutoff = t - timedelta(days=past_days)
    nxt: Optional[str] = None
    recent: list = []
    for d in dates:
        dd = date.fromisoformat(d)
        if dd >= t and nxt is None:
            nxt = d
        if cutoff <= dd < t:
            recent.append(d)
    return {"next": nxt, "recent": recent}


def find_actual_value(observations: list, release_date: str, max_lag_days: int = 45):
    """observations([(date,value)], 오름차순, fred_client.fetch_history 형태)
    중 release_date 이하로 가장 최근 값 — '그 발표에서 실제로 나온 값' 근사
    (월간지표는 보통 발표일 당일~D-2 이내 관측치가 게시됨). 순수함수(테스트용).
    max_lag_days 밖(너무 오래된 관측치)이면 그 release_date 는 매치 없음
    취급(스테일 값을 엉뚱한 발표에 붙이는 오탐 방지). 반환 (obs_date, value)
    또는 None."""
    if not observations:
        return None
    rd = date.fromisoformat(release_date)
    cutoff = rd - timedelta(days=max_lag_days)
    best = None
    for d, v in observations:
        od = date.fromisoformat(d)
        if od > rd:
            break
        if od >= cutoff:
            best = (d, v)
    return best


def _load_megatech_earnings(today: Optional[str] = None) -> list:
    """AI/반도체/빅테크 대형주(_MEGATECH_WATCHLIST 24종) 실적 발표일(2026-07-26 사용자
    추천) — bot.earnings_calendar._fetch_us_month(Finnhub, 6h 캐시) 재사용,
    신규 API 호출 없음. 이번달+다음달 스캔(실적일은 보통 4-6주 전 확정).
    실패 시 []( graceful — 이 카드만 생략, 나머지 캘린더는 정상)."""
    from bot import earnings_calendar as ec

    t = date.fromisoformat(today or date.today().isoformat())
    months = [(t.year, t.month)]
    ny, nm = (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)
    months.append((ny, nm))

    out: list = []
    seen: set = set()
    for y, m in months:
        try:
            for e in ec._fetch_us_month(y, m):
                sym = e.get("symbol", "")
                key = (sym, e.get("date"))
                if sym in _MEGATECH_WATCHLIST and key not in seen:
                    seen.add(key)
                    out.append(e)
        except Exception as exc:
            log.debug("econ_calendar: megatech earnings fetch failed %s-%s: %s", y, m, exc)
    out.sort(key=lambda e: e.get("date") or "")
    return out


def _load_econ_calendar(today: Optional[str] = None) -> dict:
    """이벤트별 FRED release_id 조회 + 발표일 fetch + 과거발표 실제치 오버레이
    + 메가테크 실적일 — 조각별 실패는 그 항목만 error/생략(graceful), 전체는
    항상 성공."""
    from bot import fred_client

    t = today or date.today().isoformat()
    start = (date.fromisoformat(t) - timedelta(days=60)).isoformat()
    end = (date.fromisoformat(t) + timedelta(days=180)).isoformat()

    events = []
    for r in _RELEASES:
        entry = dict(r)
        try:
            rid = fred_client.find_release_id(r["search"])
            if not rid:
                entry["error"] = "release_id 미확인 (FRED 카탈로그 매치 없음)"
                events.append(entry)
                continue
            dates = fred_client.fetch_release_dates(rid, start, end)
            info = upcoming_and_recent(dates, t)
            entry.update(release_id=rid, next=info["next"], recent=info["recent"])
            series_id = _SERIES_FOR_ACTUAL.get(r["key"])
            if series_id and info["recent"]:
                try:
                    hist_start = (date.fromisoformat(t) - timedelta(days=400)).isoformat()
                    obs = fred_client.fetch_history(series_id, start=hist_start)
                    actuals = []
                    for rdate in info["recent"]:
                        hit = find_actual_value(obs, rdate)
                        if hit:
                            actuals.append({"release_date": rdate,
                                           "obs_date": hit[0], "value": hit[1]})
                    if actuals:
                        entry["actuals"] = actuals
                except Exception as exc:
                    log.debug("econ_calendar: actual-value fetch failed for %s: %s",
                             r["key"], exc)
        except Exception as exc:
            log.debug("econ_calendar: %s failed: %s", r["key"], exc)
            entry["error"] = "조회 실패"
        events.append(entry)

    megatech = []
    try:
        megatech = _load_megatech_earnings(t)
    except Exception as exc:
        log.debug("econ_calendar: megatech earnings load failed: %s", exc)

    return {"events": events, "as_of": t, "megatech_earnings": megatech}


def render_econ_calendar_page(data: dict, now=None) -> str:
    """econ_calendar.html — 이벤트별 다음 발표일 + 최근 발표일 카드.
    fred_boards 의 공용 테마/nav/CSS 재사용(신규 보드 CSS 중복 방지)."""
    import html as _h
    from datetime import datetime, timezone

    from bot.fred_boards import _BOARD_CSS, _NAV, _theme_head

    _KST = timezone(timedelta(hours=9))
    now = now or datetime.now(_KST)
    ts = now.strftime("%Y-%m-%d %H:%M KST")

    cards = ""
    for e in data.get("events", []):
        label = _h.escape(e.get("label", e.get("key", "")))
        if e.get("error"):
            cards += (f'<div class="panel"><div class="panel-title">{label}</div>'
                      f'<div class="note">⚠️ {_h.escape(e["error"])}</div></div>')
            continue
        nxt = e.get("next")
        recent = e.get("recent") or []
        nxt_s = _h.escape(nxt) if nxt else "예정 없음(구간 내)"
        recent_s = ", ".join(_h.escape(d) for d in recent) if recent else "—"
        actuals_html = ""
        if e.get("actuals"):
            rows = "".join(
                f'<div class="stat"><div class="k">{_h.escape(a["release_date"])} 실제치</div>'
                f'<div class="v" style="font-size:13px">{a["value"]:,.1f} '
                f'<span class="sub" style="font-size:10px">({_h.escape(a["obs_date"])} 관측)</span></div></div>'
                for a in e["actuals"]
            )
            actuals_html = (f'<div class="stat-grid" style="margin-top:6px">{rows}</div>'
                            '<div class="note" style="font-size:11px">컨센서스(예측치) 비교는 '
                            '무료 소스 없음 — 실제치만 표시.</div>')
        cards += f"""
<div class="panel"><div class="panel-title">{label}</div>
<div class="stat-grid">
<div class="stat"><div class="k">다음 발표일</div><div class="v" style="font-size:16px">{nxt_s}</div></div>
<div class="stat"><div class="k">최근 발표일(14일 내)</div><div class="v" style="font-size:13px">{recent_s}</div></div>
</div>{actuals_html}</div>"""

    megatech = data.get("megatech_earnings") or []
    megatech_card = ""
    if megatech:
        _hour_kr = {"bmo": "장전", "amc": "장후"}
        rows = "".join(
            f'<tr><td>{_h.escape(e.get("date",""))}</td><td>{_h.escape(e.get("symbol",""))}</td>'
            f'<td>{_hour_kr.get(e.get("hour",""), "—")}</td></tr>'
            for e in megatech
        )
        megatech_card = f"""
<div class="panel"><div class="panel-title">🖥️ 메가테크 실적 발표일 (AI/반도체/빅테크 {len(_MEGATECH_WATCHLIST)}종)</div>
<table><thead><tr><th>날짜</th><th>티커</th><th>시점</th></tr></thead><tbody>{rows}</tbody></table>
<div class="note">AI/반도체 주도주 가이던스 — 거시지표 못지않게 시장 유동성/사이클 변곡점 참고.</div></div>"""

    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>경제 캘린더</title>
{_theme_head()}
{_BOARD_CSS}</head><body><div class="wrap">
{_NAV}
<h1>📅 <em>경제 캘린더</em></h1>
<p class="sub">CPI·고용동향·GDP·PCE·FOMC·ISM PMI·메가테크 실적 — 데이터 적용시각 {_h.escape(ts)} ·
소스 FRED release-dates API + Finnhub(무료, 6시간 주기 자동 갱신)</p>
<details class="guide"><summary>ℹ️ 사용법 — 처음이면 펼쳐 보세요</summary>
거시지표 발표일 전후는 변동성이 커지는 구간 — 진입/청산 타이밍 참고용.
'다음 발표일'이 임박했다면 신규 진입 전 리스크 인지, '최근 발표일'은 직후
반응(gap/드리프트)을 되짚어볼 때 참고. '실제치'는 해당 발표일에 나온 FRED
관측값(컨센서스/예측치는 유료 설문데이터라 미제공 — 실제치만). 메가테크
실적일(AI/반도체/빅테크 24종)은 AI/반도체 사이클 변곡점 참고용. 현재 US(연준/
BLS/BEA) 발표 중심 — KR/JP 는 FRED 개별 발표일정 커버리지 공백으로 미포함
(추후 확장 여지, 시장 게이트 아닌 데이터소스 제약). 미 재무부 QRA(분기
리펀딩 발표)는 정확한 일정을 검증할 무료 소스가 없어 미포함 —
treasurydirect.gov 재무부 공지 직접 확인 권장.
</details>
{cards}
{megatech_card}
<div class="footer">CPI·고용동향·GDP·PCE·FOMC·ISM PMI·메가테크실적 — 신호는 참고용(투자 판단 아님) · NOAH</div>
</div>
</body></html>"""


def regenerate_econ_calendar() -> None:
    """econ_calendar.html 재생성 — 자정/6시간 주기 + startup. 실패해도 기존
    파일 유지(graceful). ⚠️ 네트워크 호출 — to_thread 필수(이벤트루프 차단 금지)."""
    from bot.dashboard import ARCHIVE_ROOT, _inject_update_banner
    try:
        data = _load_econ_calendar()
        html = _inject_update_banner(render_econ_calendar_page(data))
        (ARCHIVE_ROOT / "econ_calendar.html").write_text(html, encoding="utf-8")
        log.info("econ_calendar: econ_calendar.html regenerated")
    except Exception:
        log.exception("econ_calendar: regen failed")
