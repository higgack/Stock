"""경제 캘린더 보드 — CPI/PPI/고용/실업수당/소매/ECI/GDP/PCE/FOMC/JOLTS/소비자심리/
산업생산 발표일정 (2026-07-26 최초, 2026-08-06 3건 추가).

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

현재는 US 발표(CPI/PPI/고용/실업수당/소매/ECI/GDP/PCE/FOMC — 전부 FRED 가 커버하는 미국
연방 지표) 중심. KR/JP 는 FRED 가 개별 발표일정을 세밀하게 제공하지
않아(시리즈 관측치는 있어도 release-dates 캘린더가 얇음) 이번 배치엔
미포함 — 향후 한국은행/일본은행 공식 일정 소스가 확인되면 동일 구조로
확장 가능(market gate 아닌 데이터소스 공백, universal 원칙 예외 아님).

조회 실패/비정상 항목(release_id 미확인 · 캐이던스 이상)은 카드 자체를
렌더하지 않음(사용자 2026-07-26 "확인안되는건 대쉬보드에서 삭제해줘") —
원인 추적은 백엔드 로그(log.debug/warning)로만, 화면엔 '조회중' 같은
영구 에러 카드를 남기지 않는다.

2026-07-26 사용자 추천 4건 검토 결과:
- ✅ **실제치(actual) 오버레이** — 과거 발표일의 FRED 시리즈 관측값을 붙임
  (CPIAUCSL/CPILFESL/PPIACO/PAYEMS/CES0500000003/ICSA/CCSA/RSXFS/GDP/PCEPI/PCEPILFE/ECIWAG, 전부 수십년 안정적으로 인용되는 FRED 표준
  니모닉 — release_id 와 달리 문서마다 다르게 인용될 위험이 낮아 하드코딩).
- ⚠️→❌ **ISM 제조업/비제조업 PMI** — 이름검색(find_release_id)이 매치
  실패("release_id 미확인")로 항상 고정, 실사용 불가 확인(사용자 2026-07-26
  스크린샷) → 카탈로그에서 제거(§확인안되는건 삭제). 대체 무료 소스 확인
  전까지 미노출 — REFERENCE 에 재검토 여지 기록.
- ✅ **메가테크 실적일(AI/반도체/빅테크 24종)** — 이미 있는
  bot.earnings_calendar._fetch_us_month(Finnhub, 6h 캐시)를 재사용해
  워치리스트만 필터 — 신규 API 호출 없음.
- ❌ **QRA(미 재무부 분기 리펀딩 발표)** — 미구현. FRED release-dates 에
  없고(재무부 보도자료 이벤트라 FRED가 추적하는 '데이터 릴리스' 개념이
  아님), 정확한 일정을 검증할 무료 소스가 이 세션엔 없어 '대략 2월/5월/
  8월/11월 초'식 근사 날짜를 넣는 건 '추측보고 금지' 원칙 위반 —
  정확한 일정은 treasurydirect.gov/home.treasury.gov 재무부 공지 직접
  확인 권장(하단 가이드에 안내만, 캘린더 항목으로는 미표시).

FOMC 버그(2026-07-26 사용자 스크린샷 — "최근 발표일(14일 내)" 14개 연속
일자 표시): find_release_id("FOMC") 가 실제로는 근일간(거의 매일) 발표되는
무관한 FRED release 를 오매치한 것으로 판단(FOMC 는 연 8회, 월간지표도
월 1회뿐이라 며칠 간격 연속 발표는 전 항목에서 통계적으로 불가능). 이름검색
자체는 유지하되(release_id 하드코딩보다 안전 — 위 설계원칙), 반환된
release_dates 의 인접 간격이 비정상으로 촘촘하면(평균 10일 미만)
`_is_plausible_release_cadence` 가 그 항목을 '조회 실패'로 강등하는 범용
가드 추가 — FOMC 하드코딩 특례가 아니라 _RELEASES 전항목에
동일 적용(오매치가 다른 검색어에서 재발해도 자동 방어).

2026-08-06 사용자 요청("추가할만한 게 있는지 확인") — 기존 14개 대비 빠진
주요 미국 지표 검토, 3건 추가:
- ✅ **JOLTS(구인·이직 동향조사)** — Fed 가 노동시장 slack 판단에 직접
  인용(파월 기자회견 단골 언급). 이미 market_overview.py '핵심 지표' 카드에
  헤드라인 수치는 있지만 **발표일정**은 이 캘린더에 없었음(역할 다름 —
  캘린더=다음 발표가 언제인지, 카드=현재 값). release 명 "Job Openings and
  Labor Turnover Survey"는 BLS 공식 릴리스명이라 ISM 과 달리 FRED release-
  dates 매치 가능성 높음(검증은 배포 후 VM 렌더로 확인 — 매치 실패해도
  이 카드만 '조회 실패'로 자동 생략, 회귀 0).
- ✅ **소비자심리지수(University of Michigan)** — 기대인플레이션 서브지수를
  Fed 가 별도로 주시. FRED release 명 "Surveys of Consumers"(공식 릴리스명).
- ✅ **산업생산·가동률(Industrial Production/Capacity Utilization)** —
  연준 자체 G.17 통계 릴리스, 실물경기 직접 측정. release 명 그대로 검색.
- ❌ **ISM 제조업/비제조업 PMI** — 위 2026-07-26 리뷰에서 이미 검증
  실패(release_id 미확인) 확인됨, 재시도 안 함(민간 설문조사라 FRED 가
  '릴리스'로 추적 안 하는 구조적 한계로 판단 — 이름을 바꿔 재검색해도
  같은 결과일 가능성 높음).
- ❌ **주택착공/신규주택판매** — 후보였으나 이미 있는 14개+3개 대비
  시장 영향력이 상대적으로 낮고(월간 후행지표), 카드 과다로 스캔하기
  어려워지는 것 방지 위해 이번엔 보류.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger("bot.econ_calendar")

_RELEASES = [
    {"key": "cpi", "label": "🛒 CPI (소비자물가지수)", "search": "Consumer Price Index", "groups": ["미국 5거래일 변동성", "정책민감도"]},
    {"key": "core_cpi", "label": "🧩 Core CPI (근원 CPI)", "search": "Consumer Price Index", "groups": ["정책민감도"]},
    {"key": "ppi", "label": "🏭 PPI (생산자물가지수)", "search": "Producer Price Index", "groups": ["미국 5거래일 변동성", "경기침체 조기경보"]},
    {"key": "jobs", "label": "💼 고용동향 (Employment Situation)", "search": "Employment Situation", "groups": ["미국 5거래일 변동성", "경기침체 조기경보"]},
    {"key": "ahe", "label": "💵 시간당임금 (AHE)", "search": "Employment Situation", "groups": ["정책민감도"]},
    # ⚠️ `is_rate`: 값 자체가 **퍼센트**인 시리즈. 상대변화율(%)로 쓰면
    # 4.2%→4.1% 가 "-2.4%" 로 떠 '2.4%p 하락'으로 읽힌다(2026-08-20 프로브
    # 실측). 유동성 보드가 이미 %p 로 쓰는 규약과 맞춘다. 새 비율 시리즈를
    # 넣을 땐 이 플래그를 같이 달 것 — 회귀가 UNRATE 로 그걸 상기시킨다.
    {"key": "unemp", "label": "📊 실업률 (Unemployment Rate)", "search": "Employment Situation", "is_rate": True, "groups": ["경기침체 조기경보"]},
    {"key": "claims", "label": "📉 신규 실업수당 (Initial Claims)", "search": "Unemployment Insurance Weekly Claims Report", "min_avg_gap_days": 4, "groups": ["미국 5거래일 변동성", "경기침체 조기경보"]},
    {"key": "cont_claims", "label": "🧷 연속 실업수당 (Continuing Claims)", "search": "Unemployment Insurance Weekly Claims Report", "min_avg_gap_days": 4, "groups": ["경기침체 조기경보"]},
    {"key": "retail", "label": "🛍️ 소매판매 (Retail Sales)", "search": "Advance Monthly Sales for Retail and Food Services", "groups": ["미국 5거래일 변동성", "경기침체 조기경보"]},
    {"key": "eci", "label": "🧮 고용비용지수 (ECI)", "search": "Employment Cost Index", "actual_min_lag_days": 70, "actual_max_lag_days": 220, "groups": ["정책민감도", "경기침체 조기경보"]},
    {"key": "gdp", "label": "📈 GDP (국내총생산)", "search": "Gross Domestic Product", "actual_min_lag_days": 100, "actual_max_lag_days": 220, "groups": ["경기침체 조기경보"]},
    # ⚠️ PCE 는 Core PCE 와 **같은 릴리스·같은 월간 주기**인데 시차 설정이
    # 빠져 있었다(기본 0~45일). PCEPI 관측은 월초(M-01) 날짜인데 발표는
    # 익월 말이라 간격이 항상 ~60일 — 45일 창엔 **절대** 안 들어와 실제치가
    # 영구 빈칸이었다(사용자 2026-08-19 캡처). 형제 항목만 고치고 이쪽을
    # 빠뜨린 전형적인 열거형 누락(#24).
    {"key": "pce", "label": "💰 PCE (개인소비지출)", "search": "Personal Income and Outlays", "actual_min_lag_days": 20, "actual_max_lag_days": 70, "groups": ["정책민감도"]},
    {"key": "core_pce", "label": "🧠 Core PCE (근원 PCE)", "search": "Personal Income and Outlays", "actual_min_lag_days": 20, "actual_max_lag_days": 70, "groups": ["정책민감도"]},
    {"key": "fomc", "label": "🏛️ FOMC", "search": "FOMC", "groups": ["미국 5거래일 변동성", "정책민감도"]},
    # 2026-08-06 사용자 요청 검토 후 추가(3건, 이하 참조):
    {"key": "jolts", "label": "🧳 구인건수 (JOLTS)", "search": "Job Openings and Labor Turnover Survey",
     "actual_min_lag_days": 25, "actual_max_lag_days": 65, "groups": ["정책민감도", "경기침체 조기경보"]},
    {"key": "umich", "label": "😊 소비자심리지수 (UMich)", "search": "Surveys of Consumers", "groups": ["정책민감도"]},
    {"key": "indpro", "label": "🏗️ 산업생산·가동률", "search": "Industrial Production and Capacity Utilization",
     "groups": ["경기침체 조기경보"]},
]

# 과거 발표일의 실제치(actual) 오버레이용 FRED 시리즈 매핑(2026-07-26 사용자
# 추천) — 컨센서스(예측치)는 설문 기반 유료 데이터라 이 세션엔 소스가 없어
# 미포함(문서화된 한계, 하단 렌더에 '컨센서스 없음' 명시) — 실제치만 제공.
# 이 니모닉들은 release_id 와 달리 수십년 안정적으로 통용되는 FRED 표준
# 코드라 하드코딩 위험이 낮음(release_id 이슈와는 다른 리스크 등급).
_SERIES_FOR_ACTUAL = {
    "cpi": "CPIAUCSL", "core_cpi": "CPILFESL", "ppi": "PPIACO",
    "jobs": "PAYEMS", "ahe": "CES0500000003", "unemp": "UNRATE",
    "claims": "ICSA", "cont_claims": "CCSA", "retail": "RSAFS",
    "eci": "ECIWAG", "gdp": "GDP", "pce": "PCEPI", "core_pce": "PCEPILFE",
    # 2026-08-06 추가분 — 전부 수십년 안정적으로 인용되는 FRED 표준 니모닉.
    "jolts": "JTSJOL", "umich": "UMCSENT", "indpro": "INDPRO",
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


def upcoming_and_recent(dates: list, today: str, *, past_days: int = 45) -> dict:
    """발표일 목록(YYYY-MM-DD, 오름차순) → {next, recent} — 순수함수(테스트용).
    next=오늘 이후 가장 가까운 예정일(오늘 포함) 또는 None.
    recent=[오늘 기준 past_days일 내 과거 발표일들](오늘 미포함).
    past_days=45(2026-07-26 조정, 이전 14 — 근거 없는 임의값이었음): 이
    캘린더의 지표는 주간(실업수당)·월간(CPI/PPI/고용/PCE/소매)·분기(ECI/GDP)·연 8회(FOMC)로 구성되어,
    14일 창은 월 후반부 조회 시 직전 발표를 놓치는 경우가
    잦음(예: CPI 가 매월 10~13일경 발표되면 25일 이후엔 14일 창에 아무것도
    안 잡힘). 45일 = FOMC 최대 간격(연말 등 ~50일)에 근접한 여유폭 —
    특히 월간/분기 지표에서 '직전 1회 발표'를 안정적으로 포착."""
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


def _is_plausible_release_cadence(dates: list, *, min_avg_gap_days: int = 10) -> bool:
    """dates(YYYY-MM-DD 오름차순) 인접 간격이 각 이벤트의 기대 주기와
    통계적으로 타당한지 검사. 기본 임계값(10일)은 월간/분기/FOMC용,
    주간 지표(실업수당)는 _RELEASES의 min_avg_gap_days=4로 완화한다.
    평균 간격이 임계값 미만이면 무관 release 오매치로 보고 False."""
    if len(dates) < 2:
        return True
    ds = [date.fromisoformat(d) for d in dates]
    gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
    avg_gap = sum(gaps) / len(gaps)
    return avg_gap >= min_avg_gap_days


# 실제치 매칭 창의 **상한** 기본값. 45일이었는데, 상한은 정확도에 기여하지
# 않는다 — `find_actual_value` 는 창 안의 **가장 마지막(최신)** 관측을 고르므로
# 상한을 넓혀도 이미 잡히던 값은 바뀌지 않고(창은 아래로만 자란다) **빈칸만
# 채워진다**. 반대로 좁으면 월간·분기 지표가 통째로 빈칸이 된다(PCE 사례).
# 빈티지(발표 시점에 아직 공표되지 않은 관측) 보호는 상한이 아니라
# `actual_min_lag_days`(하한) 담당이다.
_DEFAULT_MAX_LAG = 400


def find_actual_value(observations: list, release_date: str,
                      max_lag_days: int = _DEFAULT_MAX_LAG,
                      min_lag_days: int = 0):
    """observations([(date,value)], 오름차순)에서 release_date 시점에
    유효한 실제치 근사값을 선택. [release_date-max_lag_days,
    release_date-min_lag_days] 창에 들어오는 마지막 관측치를 채택한다.
    (지표별 공표-관측 시차는 _RELEASES의 actual_min/max_lag_days로 조정)."""
    if not observations:
        return None
    rd = date.fromisoformat(release_date)
    lower = rd - timedelta(days=max_lag_days)
    upper = rd - timedelta(days=min_lag_days)
    best = None
    for d, v in observations:
        od = date.fromisoformat(d)
        if od > upper:
            break
        if lower <= od <= upper:
            best = (d, v)
    return best


def _value_on_or_before(observations: list, cutoff_date: str):
    """오름차순 observations 에서 cutoff_date 이하 마지막 관측치."""
    out = None
    for d, v in observations:
        if d > cutoff_date:
            break
        out = (d, v)
    return out


def _build_trend_summary(observations: list, actuals: list[dict],
                         is_rate: bool = False) -> dict:
    """실제치 카드용 방향성 요약(최근발표대비·1M·3M·6M·1Y).

    `is_rate` = 값 자체가 퍼센트인 시리즈(실업률 등) → 변화를 **%p 차이**로
    낸다. 상대변화율로 쓰면 4.2%→4.1% 가 "-2.4%" 가 되어 '2.4%p 하락'으로
    읽힌다(유동성 보드의 `_rate_deltas` 와 같은 규약)."""
    if not observations:
        return {}

    def _pct(cur: float, base: float | None):
        if base is None:
            return None
        if is_rate:
            return cur - base          # %p 차이
        if base == 0:
            return None
        return (cur - base) / abs(base) * 100.0

    latest_obs_date, latest_val = observations[-1]
    ld = date.fromisoformat(latest_obs_date)
    m1 = _value_on_or_before(observations, (ld - timedelta(days=30)).isoformat())
    m3 = _value_on_or_before(observations, (ld - timedelta(days=90)).isoformat())
    m6 = _value_on_or_before(observations, (ld - timedelta(days=180)).isoformat())
    y1 = _value_on_or_before(observations, (ld - timedelta(days=365)).isoformat())

    # ⚠️ **시리즈 주기보다 짧은 창은 계산할 수 없다.** 분기 시리즈(ECI·GDP)는
    # 1M 창을 그려도 직전 **분기** 값과 비교하게 돼, 1M 과 3M 이 같은 관측을
    # 가리키며 **같은 숫자**가 나온다(2026-08-20 프로브 실측:
    # ECI 1M +0.9% ← 2026-01-01 · 3M +0.9% ← 2026-01-01). 라벨이 거짓이면
    # 빈칸이 낫다. 판정은 `macro_cadence.median_month_gap` 단일 출처 —
    # 유동성 보드(series_metrics)와 **같은 잣대**여야 두 화면이 안 갈라진다.
    from bot.macro_cadence import median_month_gap
    gap = median_month_gap(observations)

    out: dict = {"latest_obs_date": latest_obs_date, "is_rate": is_rate}
    if len(actuals) >= 2:
        cur = float(actuals[-1]["value"])
        prev = float(actuals[-2]["value"])
        out["release_delta"] = cur - prev
        out["release_pct"] = _pct(cur, prev)
    if m1 and gap <= 1:
        out["m1_pct"] = _pct(float(latest_val), float(m1[1]))
    if m3 and gap <= 3:
        out["m3_pct"] = _pct(float(latest_val), float(m3[1]))
    if m6 and gap <= 6:
        out["m6_pct"] = _pct(float(latest_val), float(m6[1]))
    if y1:
        out["y1_pct"] = _pct(float(latest_val), float(y1[1]))
    return out

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
                log.debug("econ_calendar: %s release_id not found (search=%r)",
                         r["key"], r["search"])
                events.append(entry)
                continue
            dates = fred_client.fetch_release_dates(rid, start, end)
            min_gap = int(r.get("min_avg_gap_days", 10))
            if not _is_plausible_release_cadence(dates, min_avg_gap_days=min_gap):
                entry["error"] = "release_id 매치 오류로 추정 (비정상 간격)"
                log.warning("econ_calendar: %s release_id=%s implausible cadence "
                           "(dates=%s) — suppressing, likely wrong FRED release match",
                           r["key"], rid, dates)
                events.append(entry)
                continue
            info = upcoming_and_recent(dates, t)
            entry.update(release_id=rid, next=info["next"], recent=info["recent"])
            series_id = _SERIES_FOR_ACTUAL.get(r["key"])
            if series_id and info["recent"]:
                try:
                    actual_max_lag = int(r.get("actual_max_lag_days", _DEFAULT_MAX_LAG))
                    actual_min_lag = int(r.get("actual_min_lag_days", 0))
                    lookback_days = max(400, actual_max_lag + 400)
                    hist_start = (date.fromisoformat(t) - timedelta(days=lookback_days)).isoformat()
                    obs = fred_client.fetch_history(series_id, start=hist_start)
                    actuals = []
                    for rdate in info["recent"]:
                        # ① 원천 vintage — 그 발표일 **당시** FRED 에 있던 값.
                        #    시차 창 추정과 달리 틀릴 여지가 없다(2026-08-19:
                        #    창 방식은 CPI 7/14 발표에 7월 관측을 붙여 8/12 와
                        #    같은 숫자를 냈다). 키부재·실패면 None.
                        hit = fred_client.fetch_observation_asof(series_id, rdate)
                        # ② 폴백 — vintage 를 못 받으면 기존 시차 창.
                        if not hit:
                            hit = find_actual_value(
                                obs, rdate,
                                max_lag_days=actual_max_lag,
                                min_lag_days=actual_min_lag,
                            )
                        if hit:
                            actuals.append({"release_date": rdate,
                                           "obs_date": hit[0], "value": hit[1]})
                    if actuals:
                        entry["actuals"] = actuals
                    trend = _build_trend_summary(obs, actuals,
                                                 is_rate=bool(r.get("is_rate")))
                    if trend:
                        entry["trend"] = trend
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


def _obs_period_label(obs_date: str) -> str:
    """관측 **기간**을 사람 말로 — "2026-06-01 관측" → "2026년 6월분".

    사용자 2026-08-20: "7/14일, 8/12일이 실제치인데 어떻게 06/01, 07/01에
    관측이 되지?" 동작은 정상이다 — 7/14 발표는 **6월분** CPI 를 낸 것이고
    FRED 는 월간 관측을 그 달 **1일자**로 적는다. 즉 화면의 '2026-06-01' 은
    '6월 한 달'이라는 뜻이지 '6월 1일에 쟀다'가 아니다. 표기가 그걸 못
    전달해서 데이터가 틀린 것처럼 보였다 — 라벨을 기간으로 바꾼다.
    분기·주간 관측은 그대로 날짜로 둔다(월초가 아니면 월분이 아니다)."""
    d = str(obs_date or "").strip()
    if not d:
        return "관측기간 미상"
    if len(d) == 10 and d.endswith("-01"):
        y, m = d[:4], d[5:7]
        return f"{y}년 {int(m)}월분"
    return f"{d} 관측"


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
        if e.get("error"):
            # 확인 안 되는 항목은 대쉬보드 비노출(사용자 2026-07-26) — 원인은
            # _load_econ_calendar 의 log.debug/warning 으로 백엔드에서 계속 추적 가능.
            continue
        label = _h.escape(e.get("label", e.get("key", "")))
        groups = e.get("groups") or []
        group_html = ""
        if groups:
            tags = " · ".join(_h.escape(g) for g in groups)
            group_html = f'<div class="note" style="margin:4px 0 8px;font-size:15px">분류: {tags}</div>'
        nxt = e.get("next")
        recent = e.get("recent") or []
        nxt_s = _h.escape(nxt) if nxt else "예정 없음(구간 내)"
        recent_s = ", ".join(_h.escape(d) for d in recent) if recent else "—"
        actuals_html = ""
        trend_html = ""
        if e.get("actuals"):
            rows = "".join(
                f'<div class="stat"><div class="k">{_h.escape(a["release_date"])} 실제치</div>'
                f'<div class="v" style="font-size:16px">{a["value"]:,.1f} '
                f'<span class="sub" style="font-size:12px">({_obs_period_label(a["obs_date"])})</span></div></div>'
                for a in e["actuals"]
            )
            actuals_html = f'<div class="stat-grid" style="margin-top:6px">{rows}</div>'

        trend = e.get("trend") or {}
        trend_parts = []
        # 값 자체가 퍼센트인 시리즈(실업률)는 **%p** — 상대변화율로 쓰면
        # 4.2%→4.1% 가 "-2.4%" 로 떠 '2.4%p 하락'으로 읽힌다(2026-08-20).
        _u = "%p" if trend.get("is_rate") else "%"
        rd = trend.get("release_delta")
        if rd is not None:
            arrow = "▲" if rd > 0 else ("▼" if rd < 0 else "→")
            rp = trend.get("release_pct")
            rp_s = "" if rp is None else f" ({rp:+.1f}{_u})"
            trend_parts.append(f"최근 발표대비 {arrow} {rd:+,.1f}{rp_s}")
        for _lb, _k in (("1M", "m1_pct"), ("3M", "m3_pct"),
                        ("6M", "m6_pct"), ("1Y", "y1_pct")):
            if trend.get(_k) is not None:
                trend_parts.append(f"{_lb} {trend[_k]:+.1f}{_u}")
        if trend_parts:
            trend_html = (f'<div class="note" style="font-size:15px">방향성: {" · ".join(trend_parts)}'
                          f' <span style="color:#8b8fa3;font-size:13px">(최근 관측 {_h.escape(str(trend.get("latest_obs_date", "—")))})</span></div>')

        cards += f"""
<div class="panel"><div class="panel-title">{label}</div>
{group_html}
<div class="stat-grid">
<div class="stat"><div class="k">다음 발표일</div><div class="v" style="font-size:18px">{nxt_s}</div></div>
<div class="stat"><div class="k">최근 발표일(45일 내)</div><div class="v" style="font-size:16px">{recent_s}</div></div>
</div>{actuals_html}{trend_html}</div>"""

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
<p class="sub">CPI·Core CPI·PPI·고용·AHE·실업률·실업수당(신규/연속)·소매·ECI·GDP·PCE·Core PCE·FOMC·JOLTS·소비자심리·산업생산·메가테크 실적 — 데이터 적용시각 {_h.escape(ts)} ·
소스 FRED release-dates API + Finnhub(무료, 3시간 주기 자동 갱신)</p>
<details class="guide"><summary>ℹ️ 사용법 — 처음이면 펼쳐 보세요</summary>
거시지표 발표일 전후는 변동성이 커지는 구간 — 진입/청산 타이밍 참고용.
'다음 발표일'이 임박했다면 신규 진입 전 리스크 인지, '최근 발표일'은 직후
반응(gap/드리프트)을 되짚어볼 때 참고. '실제치'는 해당 발표일에 **그 시점 FRED 에 실제로 공표돼 있던 값**
(원천 vintage 질의 — 추정 없음. 컨센서스/예측치는 유료 설문데이터라 미제공).
괄호 안은 그 값이 <b>어느 기간의 지표인지</b>다 — 예컨대 7/14 발표의 실제치는
<b>6월분</b>이다(월간 지표는 익월에 발표된다). 발표일과 대상 기간이 다른 게
정상이며, 두 발표가 같은 값이면 오히려 빈티지가 오염된 것이다. 카드 상단 '분류'에서 미국 5거래일 변동성/정책민감도/경기침체 조기경보 구분을 확인할 수 있다. '최근 발표일'은 릴리스 일정(수정치 포함) 기준이며, 실제치 매칭은 지표별 발표-관측 시차를 반영한다. 하단 '방향성'은 최근 발표대비·1M·3M·6M·1Y 변화를 함께 보여준다. 메가테크
실적일(AI/반도체/빅테크 24종)은 AI/반도체 사이클 변곡점 참고용. 현재 US(연준/
BLS/BEA) 발표 중심 — KR/JP 는 FRED 개별 발표일정 커버리지 공백으로 미포함
(추후 확장 여지, 시장 게이트 아닌 데이터소스 제약). 미 재무부 QRA(분기
리펀딩 발표)는 정확한 일정을 검증할 무료 소스가 없어 미포함 —
treasurydirect.gov 재무부 공지 직접 확인 권장.
</details>
{cards}
{megatech_card}
<div class="footer">CPI·Core CPI·PPI·고용·AHE·실업률·실업수당(신규/연속)·소매·ECI·GDP·PCE·Core PCE·FOMC·JOLTS·소비자심리·산업생산·메가테크실적 — 신호는 참고용(투자 판단 아님) · NOAH</div>
</div>
</body></html>"""


def regenerate_econ_calendar() -> None:
    """econ_calendar.html 재생성 — 자정/3시간 주기 + startup. 실패해도 기존
    파일 유지(graceful). ⚠️ 네트워크 호출 — to_thread 필수(이벤트루프 차단 금지)."""
    from bot.dashboard import ARCHIVE_ROOT, _inject_update_banner
    try:
        data = _load_econ_calendar()
        html = _inject_update_banner(render_econ_calendar_page(data))
        (ARCHIVE_ROOT / "econ_calendar.html").write_text(html, encoding="utf-8")
        log.info("econ_calendar: econ_calendar.html regenerated")
    except Exception:
        log.exception("econ_calendar: regen failed")
