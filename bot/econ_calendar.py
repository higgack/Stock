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

현재는 US 발표(CPI/고용동향/GDP/PCE/FOMC — 전부 FRED 가 커버하는 미국
연방 지표) 중심. KR/JP 는 FRED 가 개별 발표일정을 세밀하게 제공하지 않아
(시리즈 관측치는 있어도 release-dates 캘린더가 얇음) 이번 배치엔 미포함 —
향후 한국은행/일본은행 공식 일정 소스가 확인되면 동일 구조로 확장 가능
(market gate 아닌 데이터소스 공백, universal 원칙 예외 아님).
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
]


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


def _load_econ_calendar(today: Optional[str] = None) -> dict:
    """이벤트별 FRED release_id 조회 + 발표일 fetch — 조각별 실패는 그
    항목만 error 표기(graceful), 전체는 항상 성공."""
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
        except Exception as exc:
            log.debug("econ_calendar: %s failed: %s", r["key"], exc)
            entry["error"] = "조회 실패"
        events.append(entry)
    return {"events": events, "as_of": t}


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
        cards += f"""
<div class="panel"><div class="panel-title">{label}</div>
<div class="stat-grid">
<div class="stat"><div class="k">다음 발표일</div><div class="v" style="font-size:16px">{nxt_s}</div></div>
<div class="stat"><div class="k">최근 발표일(14일 내)</div><div class="v" style="font-size:13px">{recent_s}</div></div>
</div></div>"""

    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>경제 캘린더</title>
{_theme_head()}
{_BOARD_CSS}</head><body><div class="wrap">
{_NAV}
<h1>📅 <em>경제 캘린더</em></h1>
<p class="sub">CPI·고용동향·GDP·PCE·FOMC 발표일정 — 데이터 적용시각 {_h.escape(ts)} ·
소스 FRED release-dates API(무료, 6시간 주기 자동 갱신)</p>
<details class="guide"><summary>ℹ️ 사용법 — 처음이면 펼쳐 보세요</summary>
거시지표 발표일 전후는 변동성이 커지는 구간 — 진입/청산 타이밍 참고용.
'다음 발표일'이 임박했다면 신규 진입 전 리스크 인지, '최근 발표일'은 직후
반응(gap/드리프트)을 되짚어볼 때 참고. 현재 US(연준/BLS/BEA) 발표 중심 —
KR/JP 는 FRED 개별 발표일정 커버리지 공백으로 이번 배치엔 미포함(추후
확장 여지, 시장 게이트 아닌 데이터소스 제약).
</details>
{cards}
<div class="footer">CPI·고용동향·GDP·PCE·FOMC — 신호는 참고용(투자 판단 아님) · NOAH</div>
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
