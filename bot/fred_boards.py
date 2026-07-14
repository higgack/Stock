"""FRED 매크로 보드 — ① PPI 투자신호(ppi.html) ② 글로벌 유동성(liquidity.html).

원본(사용자 업로드 bambooinvesting 대시보드 2종, 2026-07-02)은 데이터를 HTML에
박제한 정적 스냅샷(PPI 2026-01·유동성 2026-03에 멈춤) — 우리는 같은 구성을
**자동 갱신**으로: 자정 대시보드 regen(+startup 백그라운드)에서 FRED 히스토리
(fred_client.fetch_history, 12h 캐시) → 지표 사전계산 → 페이지 재생성.
LLM 0·FRED 무료(₩0, ~90콜/일). Chart.js 는 CDN 아닌 **로컬 벤더링**
(_ensure_chartjs — lightweight-charts 의 _ensure_chart_lib 패턴 미러) + 부재 시
표는 그대로 렌더(차트만 생략) — CDN 차단이 페이지를 백지로 만들지 않게.

시리즈 카탈로그 = bot/fred_boards_catalog.py (원본 시드 + 멀티마켓 관련주 +
PPI 15종·유동성 8종 확장). 카탈로그만 고치면 페이지 자동 반영.

단위 주의(리뷰 2026-07-02에서 교정): FRED 원시 단위 기준 — WALCL=M$,
WTREGEN(TGA)=B$, RRPONTSYD=B$ → 순유동성(B$) = WALCL/1000 − WTREGEN − RRP.
(원본 파일의 WTREGEN 832053 은 빌더가 표시용 M$ 로 변환한 값 — 원시가 아님.)

신호(PPI)·종합점수(유동성)는 **투명한 룰**로 재정의(원본은 사전계산 임베드라
공식 불명): _signal / compute_score 참고, 회귀 테스트로 고정. 점수 백분위는
**날짜 기준 5년 트레일링**(관측수 아님 — 주간/일간/월간 시리즈 창 통일).
키 부재/개별 시리즈 실패 = graceful(그 시리즈만 생략, 페이지는 나머지로).
"""
from __future__ import annotations

import html as _h
import json as _json
import logging
from datetime import datetime, timedelta, timezone

from bot.fred_boards_catalog import LIQ_SERIES, PPI_SERIES

log = logging.getLogger("bot.fred_boards")

_KST = timezone(timedelta(hours=9))   # 모든 시각 KST 명시(서버 로컬타임 의존 금지)

_PPI_START = "2019-01-01"    # 원본과 동일 기준(2019-01~)
_LIQ_START = "2018-01-01"    # 점수 히스토리 2018~(원본 동일)

# Chart.js 로컬 벤더링(오프라인/CDN 차단 내성) — dashboard._ensure_chart_lib 미러.
_CHARTJS_NAME = "chart.umd.min.js"
_CHARTJS_URL = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"


def _ensure_chartjs() -> bool:
    """Chart.js 를 ARCHIVE_ROOT 에 1회 벤더링(멱등). 실패해도 non-fatal —
    페이지는 표 중심으로 렌더되고 차트만 생략(JS 가 typeof Chart 가드)."""
    import urllib.request
    from bot.dashboard import ARCHIVE_ROOT
    dest = ARCHIVE_ROOT / _CHARTJS_NAME
    try:
        if dest.exists() and dest.stat().st_size > 50000:
            return True
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(_CHARTJS_URL,
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if len(data) < 50000:
            log.warning("fred_boards: chart.js download too small (%d)", len(data))
            return dest.exists()
        tmp = dest.with_suffix(".js.tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        log.info("fred_boards: chart.js vendored (%d bytes)", len(data))
        return True
    except Exception as exc:
        log.warning("fred_boards: chart.js ensure failed: %s", exc)
        return dest.exists()


# ── 시계열 지표(순수 — 테스트 대상) ────────────────────────────────────────
def _pct(cur: float, base: float | None):
    if base is None or not base:
        return None
    return (cur - base) / abs(base) * 100.0


def _value_at(hist: list[tuple[str, float]], months_back: int):
    """마지막 관측 기준 months_back 개월 전(이하 가장 가까운 과거) 값.
    앵커가 월말(예: 06-30)이면 대상월도 월말로 컷(05-31 포함) — day 그대로 쓰면
    31일 관측이 빠져 한 달 더 뒤로 밀리는 EOM off-by-one(리뷰 finding)."""
    if not hist:
        return None
    import calendar
    last_d = datetime.strptime(hist[-1][0], "%Y-%m-%d")
    y, m = last_d.year, last_d.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    eom = calendar.monthrange(last_d.year, last_d.month)[1]
    day = calendar.monthrange(y, m)[1] if last_d.day == eom else \
        min(last_d.day, calendar.monthrange(y, m)[1])
    cut = f"{y:04d}-{m:02d}-{day:02d}"
    prev = [v for d, v in hist if d <= cut]
    return prev[-1] if prev else None


def series_metrics(hist: list[tuple[str, float]]) -> dict | None:
    """히스토리 → {latest, latest_date, mom, m3, m6, yoy, total, peak,
    peak_date, from_peak, trough_after_peak, recovery, momentum}. 원본 PPI
    분석 필드와 동일 의미. 모멘텀(월평균 기하 변화율)은 **양수 시계열만**
    (음수/0 교차 시 분수 거듭제곱이 복소수 → JSON 직렬화 크래시, 리뷰 finding
    — 금리·스프레드류는 None). <8 관측 → None."""
    if len(hist) < 8:
        return None
    latest_d, latest = hist[-1]
    peak_d, peak = max(hist, key=lambda x: x[1])
    after = [x for x in hist if x[0] >= peak_d]
    trough_d, trough = min(after, key=lambda x: x[1]) if after else (latest_d, latest)
    m6 = _value_at(hist, 6)
    momentum = None
    if m6 is not None and m6 > 0 and latest > 0:
        momentum = ((latest / m6) ** (1 / 6) - 1) * 100.0
    return {
        "latest": latest, "latest_date": latest_d[:7],
        "mom": _pct(latest, _value_at(hist, 1)),
        "m3": _pct(latest, _value_at(hist, 3)),
        "m6": _pct(latest, m6),
        "yoy": _pct(latest, _value_at(hist, 12)),
        "total": _pct(latest, hist[0][1]),
        "peak": peak, "peak_date": peak_d[:7],
        "from_peak": _pct(latest, peak),
        "trough_after_peak": trough if trough_d != peak_d else None,
        "recovery": _pct(latest, trough) if trough and trough_d != peak_d else None,
        "momentum": momentum,
    }


def _monthly(hist: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """일간/주간 시계열 → 월별 마지막 관측 1개, 날짜는 그 달 01일로 정규화.

    표의 1M/3M/YoY 를 월 단위 창으로 통일(사용자 2026-07-04 스크린샷 — ECB
    Rate YoY +11.6% 오표기 적발). ⚠️ day-01 정규화 필수: _value_at 이 날짜컷
    방식이라 월말 관측 날짜(24~31일)를 그대로 두면 이전 달 관측이 컷보다
    미래로 밀려 매달 한 달씩 건너뛰는 off-by-one(리뷰 2026-07-04 Major #1
    — 값은 월말 관측 그대로, 키만 01일)."""
    out: dict[str, float] = {}
    for d, v in hist:
        out[d[:7]] = v
    return [(f"{k}-01", out[k]) for k in sorted(out)]


def _rate_deltas(m: dict, mhist: list[tuple[str, float]]) -> None:
    """금리/스프레드 계열 — 1M/3M/6M/YoY 를 %변화율 대신 %p 차이로 교체
    (2.15→2.40%는 '+11.6%'가 아니라 '+0.25%p', 사용자 2026-07-04)."""
    latest = mhist[-1][1]

    def dv(n: int):
        v = _value_at(mhist, n)
        return None if v is None else round(latest - v, 2)

    m.update({"mom": dv(1), "m3": dv(3), "m6": dv(6), "yoy": dv(12),
              "rate_delta": True})


def _staleness(latest_date: str) -> int | None:
    """기준일('YYYY-MM…') → 개월 나이. 파싱 불가 None. KST 명시(규칙 10a)."""
    try:
        y, mo = int(latest_date[:4]), int(latest_date[5:7])
        today = datetime.now(_KST).date()
        return (today.year - y) * 12 + (today.month - mo)
    except Exception:
        return None


def _mark_stale(row: dict, months: int = 6) -> None:
    """기준일이 months(기본 6)개월 이상 과거면 stale 플래그(⚠️지연 배지) —
    소스가 끊기기 시작한 시리즈의 조기 경고. 12개월 이상은 로더가 목록에서
    자동 제외(사용자 2026-07-04 '중단된거는 삭제' — BLS 2025 감축분 포함
    현재·미래 중단분 전부, 제외 내역은 로그+페이지 하단 표기)."""
    age = _staleness(str(row.get("latest_date", "")))
    if age is not None and age >= months:
        row["stale"] = True


_DROP_AFTER_MONTHS = 12   # 이 나이부터 행 자체를 제외(= 중단 간주)


def _ecos_iso(t: str) -> str:
    """ECOS TIME(YYYYMM/YYYYMMDD) → ISO 날짜."""
    t = (t or "").strip()
    if len(t) == 6 and t.isdigit():
        return f"{t[:4]}-{t[4:6]}-01"
    if len(t) == 8 and t.isdigit():
        return f"{t[:4]}-{t[4:6]}-{t[6:]}"
    return t


def _alt_history(src: str) -> list[tuple[str, float]]:
    """FRED 중단 시리즈의 대체 소스 히스토리(ISO 날짜) — 사용자 2026-07-04
    'FRED 중단분은 우리 자원으로 대체': ecos:m2(한국은행 M2 평잔) ·
    ecos:base_rate(한국은행 기준금리, 일간→호출부 월간 다운샘플) ·
    ak:lpr1y(인민은행 LPR 1년). 키부재/실패 → [] (해당 행만 생략)."""
    try:
        if src == "ecos:m2":
            from bot import bok_ecos_client
            return [(_ecos_iso(t), v)
                    for t, v in bok_ecos_client.fetch_series_points("m2")]
        if src == "ecos:base_rate":
            # lookback ≤950일 — ECOS 페이지 창(1/1000)이 오름차순이라 초과 시
            # **최신**이 잘림. 950 캘린더일 ≈ 관측 950행 미만으로 안전.
            from bot import bok_ecos_client
            return [(_ecos_iso(t), v)
                    for t, v in bok_ecos_client.fetch_series_points(
                        "base_rate", lookback_days=950)]
        if src == "ak:lpr1y":
            from bot import akshare_client
            return akshare_client.lpr_1y_history()
        if src == "ecos:kr10y":
            from bot import bok_ecos_client
            return [(_ecos_iso(t), v)
                    for t, v in bok_ecos_client.fetch_series_points(
                        "kr10y", lookback_days=950)]
    except Exception as exc:
        log.warning("fred_boards: alt source %s failed: %s", src, exc)
    return []


def _signal(m: dict) -> tuple[str, str, str]:
    """(key, label, note) — 원본 4분류를 투명한 임계값 룰로 재정의.
    strong: YoY≥5% & 3M≥1.5% / reversal: 고점 -5%↓에서 저점대비 +1.5%↑ 반등 &
    3M>0 / decline: YoY<0 & 3M<0 / moderate: YoY≥2% or 3M≥0.7% / 나머지 mild."""
    yoy, m3 = m.get("yoy"), m.get("m3")
    fp, rec = m.get("from_peak"), m.get("recovery")
    if yoy is not None and m3 is not None and yoy >= 5 and m3 >= 1.5:
        return ("strong", "🔴 강한 상승", "가격 전가력 강함 — 관련주 마진 개선 국면.")
    if (fp is not None and fp <= -5 and rec is not None and rec >= 1.5
            and m3 is not None and m3 > 0):
        return ("reversal", "🟡 바닥 반등", "저점 확인 후 반등 초기 — 침체 종료 후보.")
    if yoy is not None and m3 is not None and yoy < 0 and m3 < 0:
        return ("decline", "🔵 하락", "가격 하락 지속 — 마진 압박·수요 둔화 주의.")
    if (yoy is not None and yoy >= 2) or (m3 is not None and m3 >= 0.7):
        return ("moderate", "🟠 중간 상승", "완만한 상승 — 추세 강화 여부 관찰.")
    return ("mild", "⚪ 중립", "뚜렷한 방향성 없음 — 촉매 대기.")


# ── 유동성 파생·점수(순수 — 테스트 대상) ──────────────────────────────────
def net_liquidity(walcl, tga, rrp) -> list[tuple[str, float]]:
    """Fed 순유동성(B USD) = WALCL(M$)/1000 − TGA(B$) − RRP(B$).
    FRED 원시 단위: WALCL=백만$, WTREGEN·RRPONTSYD=십억$ (공식 FRED 그래프
    수식 WALCL/1000 − RRPONTSYD − WTREGEN 와 동일 — 리뷰 2026-07-02 교정).
    주간(WALCL) 날짜축 기준, TGA/RRP는 해당일 이하 최근값 매칭."""
    def at(hist, d):
        prev = [v for dd, v in hist if dd <= d]
        return prev[-1] if prev else None
    out = []
    for d, w in walcl:
        t, r = at(tga, d), at(rrp, d)
        if t is None or r is None:
            continue
        out.append((d, w / 1000.0 - t - r))
    return out


def _pct_rank(hist_vals: list[float], v: float) -> float:
    """v의 백분위(0~100) — 트레일링 분포 내 위치."""
    if not hist_vals:
        return 50.0
    below = sum(1 for x in hist_vals if x <= v)
    return below / len(hist_vals) * 100.0


def _window_5y(pts: list[tuple[str, float]]) -> list[float]:
    """날짜 기준 5년 트레일링 값 창 — 관측수(-260) 기반이면 일간=1년·월간=전체가
    돼 페이지의 '5년 백분위' 문구와 어긋남(리뷰 finding). 5년 미만이면 전체."""
    if not pts:
        return []
    last_d = pts[-1][0]
    cutoff = f"{int(last_d[:4]) - 5}{last_d[4:]}"
    win = [v for d, v in pts if d >= cutoff]
    return win if len(win) >= 10 else [v for _, v in pts]


def compute_score(components: dict[str, tuple[float, bool]]) -> float | None:
    """종합 유동성 점수 0~100 = 구성요소 백분위 평균. components =
    {name: (percentile_0_100, invert)}. invert=True(스프레드·VIX·NFCI 등
    높을수록 긴축)는 100-p로 뒤집음. 빈 입력 → None. 공식은 문서화·고정
    (원본 점수는 임베드라 재현 불가 — 투명 재정의)."""
    vals = [(100.0 - p if inv else p) for p, inv in components.values()
            if p is not None]
    return sum(vals) / len(vals) if vals else None


def score_verdict(score: float | None) -> tuple[str, str]:
    if score is None:
        return ("—", "데이터 부족")
    if score >= 70:
        return ("🟢 유동성 풍부", "위험자산 우호 국면 — 완화적 환경.")
    if score >= 50:
        return ("🟡 중립(완화 기울기)", "혼조 — 방향 지표(순유동성·스프레드) 주시.")
    if score >= 30:
        return ("🟠 중립(긴축 기울기)", "유동성 역풍 — 위험 관리 강화.")
    return ("🔴 긴축", "유동성 위축 — 방어적 포지셔닝 권고.")


# ── 마진 스프레드(산업 PPI − 원재료 PPI, 사용자 2026-07-02 Phase2) ─────────
# 판가(output) YoY − 원가(input) YoY (pp) = 마진 방향 프록시. 양수=마진 개선.
# input 이 단일 원재료 proxy 인 쌍만(문서화) — 정확 원가바스켓 아님, 방향 신호용.
_MARGIN_PAIRS = [
    {"key": "refining", "label": "정유 (제품−원유)",
     "out": "PCU324110324110", "inp": "WPU0561",
     "stocks": "S-Oil·SK이노베이션·GS / US: Valero"},
    {"key": "steel", "label": "철강 (제품−철광석)",
     "out": "WPU1017", "inp": "WPU1011",
     "stocks": "POSCO·현대제철 / US: Nucor / JP: 일본제철"},
    {"key": "petchem", "label": "석유화학 (수지−원유)",
     "out": "PCU325211325211", "inp": "WPU0561",
     "stocks": "LG화학·롯데케미칼 / US: Dow"},
    {"key": "box", "label": "골판지 (박스−펄프)",
     "out": "PCU322211322211", "inp": "WPU0911",
     "stocks": "태림포장·아세아제지 / US: IP"},
    {"key": "transformer", "label": "전력기기 (변압기−구리)",
     "out": "PCU335311335311", "inp": "WPUSI019011",
     "stocks": "HD현대일렉트릭·효성중공업·LS ELECTRIC / US: Eaton"},
    {"key": "tire", "label": "타이어 (제품−원유·합성고무원료 proxy)",
     "out": "PCU326211326211", "inp": "WPU0561",
     "stocks": "한국타이어 / JP: 브리지스톤"},
    {"key": "airline", "label": "항공 (운임−원유·연료 proxy)",
     "out": "PCU481111481111", "inp": "WPU0561",
     "stocks": "대한항공 / US: Delta / JP: ANA"},
    # ── 2차 확장 10쌍(사용자 2026-07-02 '커버리지 더') ──
    {"key": "battery", "label": "배터리 (전지−비철금속)",
     "out": "PCU335911335911", "inp": "WPU102",
     "stocks": "LG에너지솔루션·삼성SDI / JP: 파나소닉"},
    {"key": "shipbuild", "label": "조선 (선가−철강·후판)",
     "out": "PCU336611336611", "inp": "WPU101",
     "stocks": "HD한국조선해양·한화오션·삼성중공업"},
    {"key": "auto", "label": "자동차 (차량−철강)",
     "out": "PCU336110336110", "inp": "WPU101",
     "stocks": "현대차·기아 / JP: 토요타 / US: GM"},
    {"key": "conmach", "label": "건설기계 (장비−철강)",
     "out": "WPU112", "inp": "WPU101",
     "stocks": "두산밥캣·HD현대건설기계 / US: CAT / JP: 코마츠"},
    {"key": "wire", "label": "전선 (케이블−구리)",
     "out": "WPU10260314", "inp": "WPUSI019011",
     "stocks": "LS전선(LS)·대한전선 / IT: Prysmian"},
    {"key": "cement", "label": "시멘트 (제품−석탄·연료)",
     "out": "PCU32733273", "inp": "WPU051",
     "stocks": "쌍용C&E·한일시멘트 / US: Vulcan"},
    {"key": "paper", "label": "제지 (종이−펄프)",
     "out": "PCU322121322121", "inp": "WPU0911",
     "stocks": "한솔제지·무림페이퍼 / US: IP"},
    {"key": "paint", "label": "페인트 (도료−산업화학)",
     "out": "PCU325510325510", "inp": "WPU061",
     "stocks": "KCC·노루페인트 / US: Sherwin-Williams"},
    {"key": "fertilizer", "label": "비료 (제품−천연가스·원료)",
     "out": "WPU0652", "inp": "WPU0531",
     "stocks": "남해화학·조비 / US: Mosaic·CF"},
    {"key": "meat", "label": "육가공 (가공육−생축)",
     "out": "WPU022", "inp": "WPU013",
     "stocks": "하림·CJ제일제당 / US: Tyson"},
]


def _yoy_at(hist: list[tuple[str, float]], months_back: int):
    """months_back 개월 전 시점의 YoY% — 스프레드 3M 추세 계산용(순수)."""
    cur = _value_at(hist, months_back)
    ago = _value_at(hist, months_back + 12)
    return _pct(cur, ago) if cur is not None else None


def margin_spreads(H: dict[str, list]) -> list[dict]:
    """[{label, out_yoy, in_yoy, spread, trend3m, stocks}] — 쌍의 어느 한쪽
    데이터 부족 시 그 쌍만 생략(graceful). spread=out−in(pp), trend3m=
    spread 지금 − 3개월 전(양수=개선 가속). **두 시계열을 공통 최신월로 잘라
    정렬** — 발표 lag 로 한쪽만 최신월 있으면 다른 달 YoY 끼리 빼는 왜곡
    (예: 5월 판가 − 6월 원가) 차단(리뷰 finding). 순수(테스트)."""
    out = []
    for p in _MARGIN_PAIRS:
        ho, hi = H.get(p["out"]) or [], H.get(p["inp"]) or []
        if len(ho) < 16 or len(hi) < 16:
            continue
        common = min(ho[-1][0][:7], hi[-1][0][:7])   # 공통 최신월(YYYY-MM)
        ho = [x for x in ho if x[0][:7] <= common]
        hi = [x for x in hi if x[0][:7] <= common]
        if len(ho) < 16 or len(hi) < 16:
            continue
        oy, iy = _yoy_at(ho, 0), _yoy_at(hi, 0)
        oy3, iy3 = _yoy_at(ho, 3), _yoy_at(hi, 3)
        if oy is None or iy is None:
            continue
        spread = oy - iy
        trend = (spread - (oy3 - iy3)) if (oy3 is not None and iy3 is not None) else None
        out.append({"label": p["label"], "out_yoy": oy, "in_yoy": iy,
                    "spread": spread, "trend3m": trend, "stocks": p["stocks"],
                    "asof": common})
    return out


# ── 한국 PPI(ECOS 404Y014, 사용자 2026-07-02 Phase2) ──────────────────────
# FRED(미국) 옆에 한국 생산자물가 나란히 — 이름 부분일치로 코드 해석(강건).
# 관련주 힌트는 큐레이션. BOK_ECOS_API_KEY 부재/무매칭 = 섹션만 생략(graceful).
_KR_PPI_ITEMS = [
    ("총지수", "한국 PPI 총지수", "전산업 판가 — KOSPI 전반"),
    ("공산품", "한국 PPI 공산품", "제조업 판가 — 수출제조 전반"),
    ("반도체", "한국 PPI 반도체", "삼성전자·SK하이닉스"),
    ("자동차", "한국 PPI 자동차", "현대차·기아·현대모비스"),
    ("철강", "한국 PPI 철강(1차금속)", "POSCO·현대제철"),
    ("화학", "한국 PPI 화학제품", "LG화학·롯데케미칼·한화솔루션"),
    ("의약품", "한국 PPI 의약품", "삼성바이오로직스·셀트리온·유한양행"),
    ("전지", "한국 PPI 전지(배터리)", "LG에너지솔루션·삼성SDI"),
    # ── 확장 2차(사용자 2026-07-04 '더 늘릴 수 있으면') — 이름해석이라
    # ECOS 항목명과 무매칭이면 해당 행만 생략(graceful) + 로그 가시화.
    ("디스플레이", "한국 PPI 디스플레이", "LG디스플레이·삼성전자(디스플레이)"),
    ("선박", "한국 PPI 선박", "HD한국조선해양·한화오션·삼성중공업"),
    ("기계및장비", "한국 PPI 기계·장비", "두산에너빌리티·HD현대인프라코어·두산밥캣"),
    ("전기장비", "한국 PPI 전기장비", "LS ELECTRIC·효성중공업·HD현대일렉트릭"),
    ("음식료품", "한국 PPI 음식료품", "CJ제일제당·오리온·농심"),
    ("석유제품", "한국 PPI 석유제품", "S-Oil·SK이노베이션·GS"),
    ("화장품", "한국 PPI 화장품", "아모레퍼시픽·LG생활건강·코스맥스"),
]


def _load_kr_ppi() -> list[dict]:
    """ECOS 한국 PPI → PPI 보드 rows(동일 지표·신호 파이프라인, cat 고정)."""
    try:
        from bot import bok_ecos_client
        hists = bok_ecos_client.fetch_kr_ppi_history(
            [p for p, _, _ in _KR_PPI_ITEMS])
    except Exception as exc:
        log.warning("fred_boards: KR PPI load failed: %s", exc)
        return []
    rows = []
    for pat, name, stocks in _KR_PPI_ITEMS:
        hist = hists.get(pat) or []
        m = series_metrics(hist)
        if not m:
            continue
        key, label, note = _signal(m)
        row = {"id": f"ECOS:{pat}", "name": name, "cat": "한국 PPI(ECOS)",
               "stocks": stocks, **m, "sig": key, "sig_label": label,
               "note": note, "hist": [(d[:7], v) for d, v in hist]}
        _mark_stale(row)
        rows.append(row)
    return rows


# 점수 구성요소 한글 라벨(페이지 표기 — 영문 키 노출 방지, 리뷰 finding).
_COMP_KR = {
    "net_liq_13w": "순유동성 13주Δ", "reserves_13w": "지준 13주Δ",
    "m2_yoy": "M2 YoY", "bank_credit_yoy": "은행신용 YoY",
    "hy_oas": "HY스프레드(역)", "nfci": "NFCI(역)", "vix": "VIX(역)",
    "curve_10y3m": "10Y-3M 커브",
}


# ── 데이터 수집 ────────────────────────────────────────────────────────────
def _load_ppi() -> tuple[list[dict], list[dict], list[str]]:
    """→ (rows, margins, dropped). rows = FRED PPI + 한국 PPI(ECOS) 병합,
    margins = 마진 스프레드(카탈로그 외 input WPU1011 등 추가 fetch),
    dropped = 소스 중단/데이터 없음으로 자동 제외된 시리즈 표기 목록."""
    from bot import fred_client
    H: dict[str, list] = {}
    ids = [s["id"] for s in PPI_SERIES]
    extra = {p["out"] for p in _MARGIN_PAIRS} | {p["inp"] for p in _MARGIN_PAIRS}
    for sid in ids + sorted(extra - set(ids)):
        H[sid] = fred_client.fetch_history(sid, _PPI_START)
    rows = []
    dropped: list[str] = []
    for s in PPI_SERIES:
        hist = H.get(s["id"]) or []
        if not hist:
            # FRED 가 시리즈를 아예 지운 경우(400/무관측) — stale 경로에 안
            # 걸려 조용히 사라지던 것도 제외 목록에 표기(리뷰 #5, silent 금지).
            dropped.append(f"{s['name']} ({s['id']}) — 데이터 없음")
            continue
        m = series_metrics(_monthly(hist))
        if not m:
            continue
        key, label, note = _signal(m)
        row = {**s, **m, "sig": key, "sig_label": label, "note": note,
               "hist": [(d[:7], v) for d, v in hist]}
        _mark_stale(row)   # 6개월+ 지연 조기경고 배지(전 보드 공통)
        rows.append(row)
    rows += _load_kr_ppi()
    # 12개월+ 미갱신 = 중단 간주 → 자동 제외(사용자 2026-07-04 '삭제').
    # FRED·한국PPI(ECOS) 합산 뒤 **통합 패스** — KR 행만 drop 을 우회하던
    # 갭 제거(리뷰 #1, universal). silent 아님: 로그 + 페이지 상단 목록.
    kept = []
    for r in rows:
        age = _staleness(str(r.get("latest_date", "")))
        if age is not None and age >= _DROP_AFTER_MONTHS:
            dropped.append(f"{r['name']} ({r['id']})")
            continue
        kept.append(r)
    rows = kept
    if dropped:
        log.warning("fred_boards: ppi 소스 중단 제외 %d종: %s",
                    len(dropped), ", ".join(dropped))
    return rows, margin_spreads(H), dropped


def _naver_fx_latest_row(s: dict, reuters_code: str) -> dict | None:
    """FRED H.10 미수록 통화(대만달러 등, src='naver_fx_latest:<reutersCode>') —
    네이버 exchangeWorld 최신값만으로 최소 row 구성(히스토리·1M/3M/YoY 없음,
    'hist':[] 라 차트는 빈 상태 · mom/m3/yoy=None 이라 표에는 '—'). regen(6h)
    시점 스냅샷 + 클라 liveFx() 5분 오버레이로 계속 최신화. graceful None(드롭)."""
    try:
        from bot.naver_marketindex import fetch_world_fx
        rec = (fetch_world_fx() or {}).get(reuters_code) or {}
    except Exception as exc:
        log.warning("fred_boards: naver_fx_latest %s failed: %s", reuters_code, exc)
        return None
    close = rec.get("close")
    if close is None:
        return None
    return {**s, "latest": close, "latest_date": "네이버 실시간",
            "mom": None, "m3": None, "yoy": None, "hist": []}


def _load_liq() -> tuple[list[dict], dict, float | None]:
    """→ (rows, derived{net_liq, components}, score). rows 는 카탈로그 순.
    'src' 가 있는 항목은 FRED 대신 대체 소스(ECOS/AKShare — 2026-07-04 중단
    시리즈 대체). 표 지표는 월간 다운샘플 기준(1M/3M/YoY 정합), 차트는 원본."""
    from bot import fred_client
    H: dict[str, list] = {}
    for s in LIQ_SERIES:
        src = s.get("src") or ""
        if src.startswith("naver_fx_latest:"):
            continue    # FRED 미수록 통화(대만달러 등) — 아래서 별도 처리
        if src:
            H[s["id"]] = _alt_history(src)
        else:
            H[s["id"]] = fred_client.fetch_history(s["id"], _LIQ_START)
    rows = []
    dropped: list[str] = []
    for s in LIQ_SERIES:
        src = s.get("src") or ""
        if src.startswith("naver_fx_latest:"):
            row = _naver_fx_latest_row(s, src.split(":", 1)[1])
            if row:
                rows.append(row)
            else:
                dropped.append(f"{s['name']} ({s['id']}) — 데이터 없음")
            continue
        hist = H.get(s["id"]) or []
        if not hist:
            # 소스가 아예 안 주는 경우(FRED 400/ECOS·AKShare 실패)도 표기
            # (리뷰 #5 — stale 경로 밖 silent 소멸 금지). 키 부재 등 일시
            # 사유 포함이라 '데이터 없음'으로 구분.
            dropped.append(f"{s['name']} ({s['id']}) — 데이터 없음")
            continue
        mh = _monthly(hist)
        m = series_metrics(mh)
        if not m:
            continue
        age = _staleness(m["latest_date"])
        if age is not None and age >= _DROP_AFTER_MONTHS:
            dropped.append(f"{s['name']} ({s['id']})")
            continue
        # %p 표기는 단위가 정말 % 인 계열만 — is_rate 는 최신값 % 표시용
        # 플래그라 지수/비율(DXY·M2V·NFCI 등)에도 켜져 있어, 그대로 쓰면
        # 레벨 차이가 '%p' 로 오표기(리뷰 2026-07-04 Major #2).
        if s.get("is_rate") and s.get("unit") == "%":
            _rate_deltas(m, mh)
        row = {**s, **m, "hist": [(d, v) for d, v in hist][-260:]}
        _mark_stale(row)
        rows.append(row)
    if dropped:
        log.warning("fred_boards: liq 소스 중단 제외 %d종: %s",
                    len(dropped), ", ".join(dropped))
    derived: dict = {}
    nl = net_liquidity(H.get("WALCL") or [], H.get("WTREGEN") or [],
                       H.get("RRPONTSYD") or [])
    if nl:
        derived["net_liq"] = nl[-260:]
    # 점수 구성요소 — (date,value) 유지 → 날짜 기준 5년 창(주기 무관 통일).
    # lag 는 시리즈 주기별 명시(주간 13=13주Δ, 주간 52=YoY, 월간 12=YoY —
    # 관측수 고정 12를 주간에 쓰면 12주 변화가 'YoY' 로 둔갑, 리뷰 finding).
    comps: dict[str, tuple[float, bool]] = {}

    def add(name, pts, inv=False, transform=None, lag=0):
        if not pts or len(pts) < max(30, lag + 10):
            return
        if transform == "delta":
            pts = [(pts[i][0], pts[i][1] - pts[i - lag][1])
                   for i in range(lag, len(pts))]
        elif transform == "pct":
            out = []
            for i in range(lag, len(pts)):
                p = _pct(pts[i][1], pts[i - lag][1])
                if p is not None:
                    out.append((pts[i][0], p))
            pts = out
        win = _window_5y(pts)
        if len(win) < 10:
            return
        comps[name] = (_pct_rank(win[:-1] or win, win[-1]), inv)

    add("net_liq_13w", nl, transform="delta", lag=13)              # 주간
    add("reserves_13w", H.get("WRESBAL"), transform="delta", lag=13)   # 주간
    add("m2_yoy", H.get("M2SL"), transform="pct", lag=12)          # 월간
    add("bank_credit_yoy", H.get("TOTBKCR"), transform="pct", lag=52)  # 주간
    add("hy_oas", H.get("BAMLH0A0HYM2"), inv=True)
    add("nfci", H.get("NFCI"), inv=True)
    add("vix", H.get("VIXCLS"), inv=True)
    add("curve_10y3m", H.get("T10Y3M"))
    score = compute_score(comps)
    derived["components"] = {k: round(p, 1) for k, (p, _) in comps.items()}
    derived["dropped"] = dropped
    return rows, derived, score


# ── 렌더 ───────────────────────────────────────────────────────────────────
# 라이트 기본 + data-theme=dark 오버라이드 — 전 대시보드 공통 시간 테마
# (19~07 KST 다크, dashboard._THEME_JS 재사용, 사용자 2026-07-02 '우리하는것처럼').
_BOARD_CSS = """
<style>
:root{--bg:#f7f8f9;--card:#ffffff;--surface2:#f0f1f3;--border:#e4e5e9;
 --fg:#282a30;--muted:#7c818c;--accent:#3b78e7;--pillbd:#282a30}
:root[data-theme="dark"]{--bg:#0f1117;--card:#1a1d27;--surface2:#242836;
 --border:#2e3348;--fg:#e4e6ed;--muted:#8b8fa3;--accent:#4f8ff7;--pillbd:#ffffff}
body{background:var(--bg);color:var(--fg);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;margin:0}
.wrap{max-width:1440px;margin:0 auto;padding:20px}
.nav{margin-bottom:14px;font-size:13px}.nav a{color:var(--muted);text-decoration:none}.nav a:hover{color:var(--fg)}
h1{font-size:24px;margin:6px 0}h1 em{color:var(--accent);font-style:normal}
.sub{color:var(--muted);font-size:13px;margin:4px 0 14px}
.pills{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.pill{padding:6px 14px;border-radius:8px;font-size:12.5px;font-weight:600;cursor:pointer;border:2px solid transparent;background:var(--card);color:var(--muted);box-shadow:0 0 0 1px var(--border)}
.pill.active{border-color:var(--pillbd);color:var(--fg)}
.p-strong{color:#ef4444}.p-moderate{color:#f59e0b}.p-reversal{color:#ca8a04}.p-decline{color:#3b82f6}.p-mild{color:var(--muted)}
:root[data-theme="dark"] .p-reversal{color:#eab308}
table{width:100%;border-collapse:collapse;font-size:12px}
th{color:var(--muted);text-align:left;padding:7px 9px;border-bottom:1px solid var(--border);font-size:11px;white-space:nowrap}
td{padding:7px 9px;border-bottom:1px solid var(--border)}
tr.row{cursor:pointer}tr.row:hover{background:var(--surface2)}tr.selected{background:rgba(79,143,247,.12)}
.pos{color:#16a34a;font-weight:600}.neg{color:#ef4444;font-weight:600}.flat{color:var(--muted)}
.stale{color:#f87171;font-size:10px;font-weight:700}
:root[data-theme="dark"] .pos{color:#22c55e}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;background:var(--surface2)}
.panel{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin:14px 0}
.panel-title{font-size:15px;font-weight:600;margin-bottom:10px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:8px 0}
.stat{background:var(--surface2);border-radius:8px;padding:10px}
.stat .k{font-size:11px;color:var(--muted)}.stat .v{font-size:18px;font-weight:700;margin-top:2px}
.note{background:var(--surface2);border-left:3px solid var(--accent);border-radius:8px;padding:12px;font-size:13px;color:var(--fg);opacity:.9;margin-top:10px}
.chartbox{position:relative;height:320px}
.tbl-wrap{max-height:560px;overflow-y:auto}
.stocks{color:var(--muted);font-size:11px;max-width:340px}
.guide{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:4px 14px;margin:10px 0;font-size:13px;line-height:1.7;color:var(--fg)}
.guide summary{cursor:pointer;padding:8px 0;font-weight:600}
.footer{color:var(--muted);font-size:11px;text-align:center;padding:18px 0;border-top:1px solid var(--border);margin-top:24px}
</style>
"""


def _theme_head() -> str:
    """<head> 인라인 테마 스크립트 — dashboard._THEME_JS 재사용(19~07 KST 다크,
    60초 재체크·플래시 방지). import 실패 시(순수 테스트 등) 다크 고정 폴백."""
    try:
        from bot.dashboard import _THEME_JS
        return f"<script>{_THEME_JS}</script>"
    except Exception:
        return "<script>document.documentElement.dataset.theme='dark';</script>"

_NAV = ('<div class="nav"><a href="market.html">🌍 홈</a> · '
        '<a href="index.html">🦉 종목분석</a> · <a href="ppi.html">🏭 PPI</a> · '
        '<a href="liquidity.html">💧 유동성</a> · '
        '<a href="marketcap.html">🏆 Market cap</a></div>')

# 공용 JS: esc/pc + Chart.js 옵션(중복 3벌 → 1벌, 리뷰 finding) + 차트 가드
# (Chart 미로딩 시 표는 살리고 차트만 생략 — 벤더 파일 유실/차단 내성).
_BOARD_JS_COMMON = """
function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':s);return d.innerHTML;}
function pc(v,dg){if(v==null)return"<span class='flat'>—</span>";var c=v>=0?'pos':'neg';return "<span class='"+c+"'>"+(v>=0?'+':'')+v.toFixed(dg==null?1:dg)+"%</span>";}
// 금리/스프레드 행(rate_delta) = %p 차이 표기(2.15→2.40 은 '+0.25%p'), 그 외 = pc.
function pcd(r,v,dg){if(v==null)return"<span class='flat'>—</span>";
 if(r.rate_delta){var c=v>=0?'pos':'neg';return "<span class='"+c+"'>"+(v>=0?'+':'')+v.toFixed(2)+"%p</span>";}
 return pc(v,dg);}
// 기준일 셀 — 6개월+ 지연 조기경고 배지(12개월+ 는 서버가 목록에서 제외).
function ld(r){return esc(r.latest_date)+(r.stale?" <span class='stale'>⚠️지연</span>":"");}
var CHART_OPTS={plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8a8f98',maxTicksLimit:12},grid:{color:'rgba(128,132,140,.18)'}},y:{ticks:{color:'#8a8f98'},grid:{color:'rgba(128,132,140,.18)'}}},maintainAspectRatio:false};
function mkChart(el,labels,data,color,fill){
 if(typeof Chart==='undefined'||!el)return null;
 var ds={data:data,borderColor:color||'#4f8ff7',borderWidth:2,pointRadius:0,tension:.25};
 if(fill)ds.fill={target:'origin',above:'rgba(34,197,94,.06)'};
 return new Chart(el,{type:'line',data:{labels:labels,datasets:[ds]},options:CHART_OPTS});
}
"""


def _fmt_pct_cell(v, digits=1):
    if v is None:
        return "<span class='flat'>—</span>"
    cls = "pos" if v >= 0 else "neg"
    return f"<span class='{cls}'>{v:+.{digits}f}%</span>"


def _margin_panel(margins: list[dict]) -> str:
    """마진 스프레드 패널(서버 렌더 정적 표 — JS 불요). 빈 목록 → ''."""
    if not margins:
        return ""
    body = []
    for m in margins:
        tr = m.get("trend3m")
        if tr is None:
            tr_s = "<span class='flat'>—</span>"
        elif abs(tr) < 0.05:   # 변화 없음을 '개선'으로 오표기 금지(리뷰 finding)
            tr_s = "<span class='flat'>→ 유지 +0.0pp</span>"
        else:
            tr_s = (f"<span class='{'pos' if tr > 0 else 'neg'}'>"
                    f"{'▲ 개선' if tr > 0 else '▼ 악화'} {tr:+.1f}pp</span>")
        sp = m["spread"]
        asof = m.get("asof") or "—"
        body.append(
            f"<tr><td><b>{_h.escape(m['label'])}</b>"
            f"<div style='color:var(--muted);font-size:10px'>기준 {_h.escape(asof)}</div></td>"
            f"<td>{_fmt_pct_cell(m['out_yoy'])}</td>"
            f"<td>{_fmt_pct_cell(m['in_yoy'])}</td>"
            f"<td><b><span class='{'pos' if sp >= 0 else 'neg'}'>{sp:+.1f}pp</span></b></td>"
            f"<td>{tr_s}</td>"
            f"<td class='stocks'>{_h.escape(m['stocks'])}</td></tr>")
    return f"""
<div class="panel"><div class="panel-title">💹 마진 스프레드 <span style="color:var(--muted);font-size:11px">판가 YoY − 원가 YoY(pp) · 양수=마진 개선 · 3M 추세=가속/둔화</span></div>
<table><thead><tr><th>산업 (판가−원가)</th><th>판가 YoY</th><th>원가 YoY</th><th>스프레드</th><th>3M 추세</th><th>관련주</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>
<div class="note">원가는 단일 원재료 proxy(정확한 원가바스켓 아님) — <b>방향 신호용</b>. 스프레드 확대 = 판가가 원가보다 빠르게 상승(마진 개선 압력).</div>
</div>"""


def _dropped_note(dropped: list[str] | None) -> str:
    """중단 제외 내역 — 화면 표기는 제거(사용자 2026-07-04 '지워졌으니 별도
    코멘트 불필요'). 가시성은 journal 경고(로더의 log.warning)가 담당 —
    운영자 디버깅 경로 유지. 항상 빈 문자열."""
    return ""


def render_ppi_page(rows: list[dict], margins: list[dict] | None = None,
                    now: datetime | None = None,
                    dropped: list[str] | None = None) -> str:
    """ppi.html — 마진 스프레드 + 시리즈 테이블 + 클릭 상세(Chart.js 로컬 벤더,
    부재 시 표만). rows 비면 데이터 없음 배너(silent drop 금지)."""
    now = now or datetime.now(_KST)
    ts = now.strftime("%Y-%m-%d %H:%M KST")
    margins = margins or []
    payload = _json.dumps({"rows": rows}, ensure_ascii=False).replace("<", "\\u003c")
    # Benchmark 를 '전체 카테고리' 바로 옆(맨 앞)으로 — 나머지는 알파벳순
    # 유지 (사용자 2026-07-05 '전체 옆에 Benchmark').
    cats = sorted({r["cat"] for r in rows})
    if "Benchmark" in cats:
        cats.remove("Benchmark")
        cats.insert(0, "Benchmark")
    empty = ("" if rows else
             "<div class='note'>⚠️ FRED 데이터 없음 — FRED_API_KEY 확인 필요. "
             "키 등록 후 자정 재생성(또는 봇 재시작) 시 채워집니다.</div>")
    sig_counts = {k: sum(1 for r in rows if r["sig"] == k)
                  for k in ("strong", "moderate", "reversal", "decline", "mild")}
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PPI 투자신호</title>
{_theme_head()}
<script src="{_CHARTJS_NAME}"></script>
{_BOARD_CSS}</head><body><div class="wrap">
{_NAV}
<h1>🏭 <em>PPI</em> 투자신호 보드</h1>
<p class="sub">미 생산자물가(FRED) 산업별 가격 추세 → 관련주 신호 · {len(rows)}개 시리즈(2019-01~) ·
관련주 US·KR·JP·TW · 데이터 적용시각 {ts} · 소스 FRED API(6시간 주기 자동 갱신)</p>
{_dropped_note(dropped)}
<details class="guide"><summary>ℹ️ 사용법 — 처음이면 펼쳐 보세요</summary>
<b>1) 신호 필터</b> — 상단 알약(전체/🔴/🟠/🟡/🔵/⚪) 클릭 = 그 신호만. 두 번째 줄 = 카테고리 필터.<br>
<b>2) 신호 의미(룰 기반)</b> — 🔴 <b>강한 상승</b>: YoY≥5% & 3M≥1.5%(가격 전가력↑) ·
🟡 <b>바닥 반등</b>: 고점 대비 -5% 아래에서 저점 대비 +1.5% 반등(침체 종료 후보) ·
🔵 <b>하락</b>: YoY·3M 모두 음수(마진 압박) · 🟠 <b>중간</b>: YoY≥2% 또는 3M≥0.7% · ⚪ 중립.<br>
<b>3) 행 클릭</b> = 상세(2019년 이후 차트·고점比·저점 회복률·6M 모멘텀·관련주).<br>
<b>4) 관련주</b> — 해당 PPI 상승 = 그 산업 <b>판가 상승</b> → 관련주 실적 개선 신호(US·KR·JP·TW).
자동 신호이므로 참고용 — 확정 판단 금지.<br>
<b>5) 💹 마진 스프레드</b> — 판가(산업 PPI) YoY − 원가(원재료 PPI) YoY. 양수·확대 = 그 산업 마진 개선 압력(원가는 단일 proxy — 방향 신호용).<br>
<b>6) 🇰🇷 한국 PPI</b> — 카테고리 '한국 PPI(ECOS)' = 한국은행 생산자물가(월간)를 같은 신호 룰로 — 미국(FRED)과 나란히 비교.<br>
<b>7) 갱신</b> — 6시간 주기(자정·06·12·18시 KST) 자동 재생성(FRED·ECOS 무료 API). 원본 대비: 박제 아님·자동 갱신.
</details>
{empty}
{_margin_panel(margins)}
<div class="pills" id="pills" data-counts='{_json.dumps(sig_counts)}'></div>
<div class="pills" id="cats" data-cats='{_json.dumps(cats, ensure_ascii=False).replace("<", "&lt;")}'></div>
<div class="panel"><div class="panel-title">시리즈 개요 <span style="color:#8b8fa3;font-size:11px">(행 클릭 = 상세 차트)</span></div>
<div class="tbl-wrap"><table><thead><tr>
<th>시리즈</th><th>카테고리</th><th>신호</th><th>MoM</th><th>3M</th><th>6M</th><th>YoY</th><th>고점比</th><th>관련주</th>
</tr></thead><tbody id="tb"></tbody></table></div></div>
<div class="panel" id="detail" style="display:none">
  <div class="panel-title" id="d-title"></div>
  <div class="stat-grid" id="d-stats"></div>
  <div class="chartbox"><canvas id="d-chart"></canvas></div>
  <div class="note" id="d-note"></div>
</div>
<div class="footer">FRED PPI · 신호는 룰 기반 참고 신호(투자 판단 아님) · NOAH</div>
</div>
<script id="ppi-data" type="application/json">{payload}</script>
<script>
(function(){{
var R=JSON.parse(document.getElementById('ppi-data').textContent).rows||[];
var SIG={{strong:'🔴 강한 상승',moderate:'🟠 중간 상승',reversal:'🟡 바닥 반등',decline:'🔵 하락',mild:'⚪ 중립',all:'전체'}};
var fsig='all',fcat='all',sel=null,chart=null;
{_BOARD_JS_COMMON}
function pills(){{var cnt=JSON.parse(document.getElementById('pills').getAttribute('data-counts'));
 var keys=['all','strong','moderate','reversal','decline','mild'];
 document.getElementById('pills').innerHTML=keys.map(function(k){{
  var n=k==='all'?R.length:(cnt[k]||0);
  return "<span class='pill p-"+k+(fsig===k?' active':'')+"' data-k="+k+">"+SIG[k]+" "+n+"</span>";}}).join('');
 var cats=JSON.parse(document.getElementById('cats').getAttribute('data-cats'));
 document.getElementById('cats').innerHTML=["<span class='pill"+(fcat==='all'?' active':'')+"' data-c='all'>전체 카테고리</span>"]
  .concat(cats.map(function(c){{return "<span class='pill"+(fcat===c?' active':'')+"' data-c=\\""+esc(c)+"\\">"+esc(c)+"</span>";}})).join('');
}}
function rows(){{return R.filter(function(r){{return (fsig==='all'||r.sig===fsig)&&(fcat==='all'||r.cat===fcat);}});}}
function table(){{document.getElementById('tb').innerHTML=rows().map(function(r,i){{
 return "<tr class='row"+(sel===r.id?' selected':'')+"' data-id='"+esc(r.id)+"'>"+
 "<td><b>"+esc(r.name)+"</b><div style='color:#8b8fa3;font-size:10px'>"+esc(r.id)+" · "+ld(r)+"</div></td>"+
 "<td>"+esc(r.cat)+"</td><td><span class='badge p-"+r.sig+"'>"+esc(r.sig_label)+"</span></td>"+
 "<td>"+pc(r.mom,2)+"</td><td>"+pc(r.m3)+"</td><td>"+pc(r.m6)+"</td><td>"+pc(r.yoy)+"</td><td>"+pc(r.from_peak)+"</td>"+
 "<td class='stocks'>"+esc(r.stocks)+"</td></tr>";}}).join('');}}
function detail(id){{var r=R.find(function(x){{return x.id===id;}});if(!r)return;sel=id;
 document.getElementById('detail').style.display='block';
 document.getElementById('d-title').textContent=r.name+' ('+r.id+') — '+r.sig_label;
 var st=[['최신',r.latest.toFixed(1)+' ('+r.latest_date+')'],['YoY',(r.yoy==null?'—':r.yoy.toFixed(1)+'%')],
  ['2019년 이후',(r.total==null?'—':r.total.toFixed(1)+'%')],['고점 대비',(r.from_peak==null?'—':r.from_peak.toFixed(1)+'%')],
  ['저점 회복',(r.recovery==null?'—':r.recovery.toFixed(1)+'%')],['6M 모멘텀',(r.momentum==null?'—':r.momentum.toFixed(2)+'%/월')]];
 document.getElementById('d-stats').innerHTML=st.map(function(s){{return "<div class='stat'><div class='k'>"+s[0]+"</div><div class='v'>"+s[1]+"</div></div>";}}).join('');
 document.getElementById('d-note').innerHTML='💡 '+esc(r.note)+'<br>📌 관련주: '+esc(r.stocks);
 if(chart)chart.destroy();
 chart=mkChart(document.getElementById('d-chart'),r.hist.map(function(h){{return h[0];}}),r.hist.map(function(h){{return h[1];}}));
 table();}}
// 필터 변경 시 현재 선택이 목록 밖이면 첫 행으로 차트 자동 전환 — '반도체
// 눌렀는데 차트 그대로'(사용자 2026-07-02 모바일) 직관 불일치 fix.
function applyFilter(){{pills();table();var f=rows();
 if(f.length&&!f.some(function(r){{return r.id===sel;}}))detail(f[0].id);}}
document.addEventListener('click',function(ev){{
 var p=ev.target.closest('.pill');
 if(p){{if(p.hasAttribute('data-k'))fsig=p.getAttribute('data-k');
  if(p.hasAttribute('data-c'))fcat=p.getAttribute('data-c');applyFilter();return;}}
 var tr=ev.target.closest('tr.row');
 if(tr){{detail(tr.getAttribute('data-id'));
  var d=document.getElementById('detail');
  if(d&&d.scrollIntoView)d.scrollIntoView({{behavior:'smooth',block:'nearest'}});}}}});
pills();table();if(R.length)detail(R[0].id);
}})();
</script></body></html>"""


def render_liquidity_page(rows: list[dict], derived: dict, score: float | None,
                          now: datetime | None = None) -> str:
    """liquidity.html — 종합점수 + 순유동성 차트 + 지표 테이블·상세(해설 포함).
    표 먼저 렌더 → 차트는 mkChart 가드(Chart 부재 시 표는 유지, 리뷰 finding)."""
    now = now or datetime.now(_KST)
    ts = now.strftime("%Y-%m-%d %H:%M KST")
    v_label, v_note = score_verdict(score)
    payload = _json.dumps(
        {"rows": rows, "net_liq": derived.get("net_liq") or [],
         "components": derived.get("components") or {}},
        ensure_ascii=False).replace("<", "\\u003c")
    cats = []
    for r in rows:
        c = r.get("category") or "기타"
        if c not in cats:
            cats.append(c)
    empty = ("" if rows else
             "<div class='note'>⚠️ FRED 데이터 없음 — FRED_API_KEY 확인 필요.</div>")
    score_s = "—" if score is None else f"{score:.0f}"
    comp_rows = "".join(
        f"<div class='stat'><div class='k'>{_h.escape(_COMP_KR.get(k, k))}</div>"
        f"<div class='v'>{p:.0f}</div></div>"
        for k, p in (derived.get("components") or {}).items())
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>글로벌 유동성</title>
{_theme_head()}
<script src="{_CHARTJS_NAME}"></script>
{_BOARD_CSS}</head><body><div class="wrap">
{_NAV}
<h1>💧 <em>글로벌 유동성</em> 보드</h1>
<p class="sub">Fed 순유동성(WALCL−TGA−RRP)·M2·중앙은행 자산·크레딧 스프레드·스트레스 지표 {len(rows)}종 ·
데이터 적용시각 {ts} · 소스 FRED + 한국은행 ECOS + 인민은행 LPR(6시간 주기 자동 갱신)</p>
{_dropped_note(derived.get("dropped"))}
<details class="guide"><summary>ℹ️ 사용법 — 처음이면 펼쳐 보세요</summary>
<b>1) 종합점수(0~100)</b> — 구성요소 8개 각각의 <b>최근값이 최근 5년 분포에서 어디쯤인지</b>(백분위)를
평균. 순유동성 13주Δ·지준 13주Δ·M2 YoY·은행신용 YoY 는 높을수록 완화, HY스프레드·NFCI·VIX 는
높을수록 긴축이라 <b>역방향(역)</b>으로 반영. 70↑ 풍부 / 50~70 중립(완화) / 30~50 중립(긴축) / 30↓ 긴축.<br>
<b>2) 순유동성 차트</b> — Fed 총자산 − 재무부계정(TGA) − 역레포(RRP), 단위 B$.
TGA 급증(국채 대량발행)·RRP 증가 = 시장 유동성 흡수.<br>
<b>3) 지표 일람</b> — 분류 알약으로 필터, <b>행 클릭</b> = 차트 + 해설(정의·해석·읽는법·🇰🇷 한국 영향).<br>
<b>4) 소스·표기</b> — FRED 중단 시리즈는 원천으로 대체(한국 M2·기준금리=한국은행 ECOS, 중국 LPR=인민은행/AKShare, 2026-07-04).
금리·스프레드 계열의 1M/3M/YoY 는 <b>%p 차이</b>(예: 2.15→2.40 = +0.25%p), 그 외는 %변화율. 6개월+ 지연 시리즈는 <b>⚠️지연</b> 배지, 12개월+ 미갱신(중단)은 목록에서 자동 제외(상단 제외 안내).<br>
<b>5) 갱신</b> — 6시간 주기(자정·06·12·18시 KST) 자동 재생성(전부 무료 API).
</details>
{empty}
<div class="panel"><div class="panel-title">종합 유동성 점수</div>
<div class="stat-grid">
 <div class="stat"><div class="k">점수 (0~100)</div><div class="v" style="font-size:30px">{score_s}</div></div>
 <div class="stat"><div class="k">판정</div><div class="v" style="font-size:16px">{_h.escape(v_label)}</div></div>
 {comp_rows}
</div>
<div class="note">공식: 구성요소별 최근값의 <b>5년 트레일링 백분위</b>(0~100, 날짜 기준) 평균 —
순유동성 13주Δ · 지준 13주Δ · M2 YoY · 은행신용 YoY · HY스프레드(역) · NFCI(역) · VIX(역) · 10Y-3M 커브.
{_h.escape(v_note)} (원본 대시보드의 점수는 공식 비공개 임베드 — 본 보드는 투명 재정의·테스트 고정)</div></div>
<div class="panel"><div class="panel-title">Fed 순유동성 (B USD) = 총자산 − TGA − 역레포</div>
<div class="chartbox"><canvas id="nl-chart"></canvas></div></div>
<div class="pills" id="cats" data-cats='{_json.dumps(cats, ensure_ascii=False).replace("<", "&lt;")}'></div>
<div class="panel"><div class="panel-title">지표 일람 <span style="color:#8b8fa3;font-size:11px">(행 클릭 = 차트·해설)</span></div>
<div class="tbl-wrap"><table><thead><tr>
<th>지표</th><th>분류</th><th>최신</th><th>1M</th><th>3M</th><th>YoY</th><th>기준일</th>
</tr></thead><tbody id="tb"></tbody></table></div></div>
<div class="panel" id="detail" style="display:none">
  <div class="panel-title" id="d-title"></div>
  <div class="chartbox"><canvas id="d-chart"></canvas></div>
  <div class="note" id="d-note"></div>
</div>
<div class="footer">FRED · 한국은행 ECOS · 인민은행 LPR(AKShare) · 점수·해설은 참고 신호(투자 판단 아님) — NOAH</div>
</div>
<script id="liq-data" type="application/json">{payload}</script>
<script>
(function(){{
var D=JSON.parse(document.getElementById('liq-data').textContent);
var R=D.rows||[],fcat='all',sel=null,chart=null;
{_BOARD_JS_COMMON}
// 단위 인지 표기(리뷰 finding — WALCL 6,628,894 'M USD' 를 '6.62M' 으로 내던 버그):
// 배수 단위는 절대값으로 환산 후 T/B 압축 + 통화기호, 미지 단위는 원값+단위 그대로.
var UM={{'M USD':[1e6,'$'],'B USD':[1e9,'$'],'M EUR':[1e6,'€'],'100M JPY':[1e8,'¥'],
 'JPY':[1,'¥'],'EUR':[1,'€'],'KRW':[1,'₩'],'USD':[1,'$']}};
function fv(r){{var v=r.latest,u=r.unit||'';
 if(u==='%')return v.toFixed(2)+'%';
 if(u==='Index'||u==='pt')return v.toFixed(1);
 if(r.is_rate)return v.toFixed(2)+'%';
 var m=UM[u];
 if(!m)return v.toLocaleString(undefined,{{maximumFractionDigits:2}})+(u?' '+u:'');
 var a=v*m[0],c=m[1],ab=Math.abs(a);
 if(ab>=1e12)return c+(a/1e12).toFixed(2)+'T';
 if(ab>=1e9)return c+(a/1e9).toFixed(1)+'B';
 if(ab>=1e6)return c+(a/1e6).toFixed(1)+'M';
 return c+a.toLocaleString(undefined,{{maximumFractionDigits:1}});}}
function pills(){{var cats=JSON.parse(document.getElementById('cats').getAttribute('data-cats'));
 document.getElementById('cats').innerHTML=["<span class='pill"+(fcat==='all'?' active':'')+"' data-c='all'>전체</span>"]
 .concat(cats.map(function(c){{return "<span class='pill"+(fcat===c?' active':'')+"' data-c=\\""+esc(c)+"\\">"+esc(c)+"</span>";}})).join('');}}
function frows(){{return R.filter(function(r){{return fcat==='all'||(r.category||'기타')===fcat;}});}}
function table(){{document.getElementById('tb').innerHTML=frows()
 .map(function(r){{return "<tr class='row"+(sel===r.id?' selected':'')+"' data-id='"+esc(r.id)+"'>"+
 "<td><b>"+esc(r.name)+"</b><div style='color:#8b8fa3;font-size:10px'>"+esc(r.id)+"</div></td>"+
 "<td>"+esc(r.category||'—')+"</td><td><b>"+fv(r)+"</b></td>"+
 "<td>"+pcd(r,r.mom,2)+"</td><td>"+pcd(r,r.m3)+"</td><td>"+pcd(r,r.yoy)+"</td><td style='color:#8b8fa3'>"+ld(r)+"</td></tr>";}}).join('');}}
function detail(id){{var r=R.find(function(x){{return x.id===id;}});if(!r)return;sel=id;
 document.getElementById('detail').style.display='block';
 document.getElementById('d-title').textContent=r.name+' ('+r.id+') — 최신 '+fv(r);
 var n=['📖 '+(r.desc||''),'🧭 '+(r.interpret||''),'👁 '+(r.how_to_read||''),'🇰🇷 '+(r.kr_impact||'')]
  .filter(function(x){{return x.length>4;}}).map(esc).join('<br>');
 document.getElementById('d-note').innerHTML=n;
 if(chart)chart.destroy();
 chart=mkChart(document.getElementById('d-chart'),r.hist.map(function(h){{return h[0];}}),r.hist.map(function(h){{return h[1];}}));
 table();}}
// 필터 변경 시 선택이 목록 밖이면 첫 행으로 차트 자동 전환(PPI 와 동일 fix).
function applyFilter(){{pills();table();var f=frows();
 if(f.length&&!f.some(function(r){{return r.id===sel;}}))detail(f[0].id);}}
document.addEventListener('click',function(ev){{
 var p=ev.target.closest('.pill');if(p&&p.hasAttribute('data-c')){{fcat=p.getAttribute('data-c');applyFilter();return;}}
 var tr=ev.target.closest('tr.row');
 if(tr){{detail(tr.getAttribute('data-id'));
  var d=document.getElementById('detail');
  if(d&&d.scrollIntoView)d.scrollIntoView({{behavior:'smooth',block:'nearest'}});}}}});
// 표 먼저(차트 실패가 페이지를 백지로 만들지 않게) → 그 다음 차트.
pills();table();if(R.length)detail(R[0].id);
if(D.net_liq&&D.net_liq.length){{mkChart(document.getElementById('nl-chart'),
 D.net_liq.map(function(h){{return h[0];}}),D.net_liq.map(function(h){{return h[1];}}),'#22c55e',true);}}
// 환율 실시간 오버레이(사용자 2026-07-02 '네이버같은곳에서 실시간으로', 2026-07-14
// 엔/위안 1차 확장 + 대만달러/유로/파운드/스위스프랑 2차 확장 '나머지 환율도
// 네이버실시간으로') — 최신값·기준일만 네이버로 덮고 기간지표(1M/3M/YoY)·차트는
// FRED 히스토리 유지(단 USDTWD 는 FRED 미수록이라 히스토리 자체가 없음 — 서버가
// naver_fx_latest 로 최신값만 시딩, 이 오버레이가 5분마다 갱신). 로드 시 + 5분마다.
// 실패 시 조용히 기존값 유지(graceful, pair 단위 독립).
var FXPAIRS=[['usdkrw','DEXKOUS'],['usdjpy','DEXJPUS'],['usdcny','DEXCHUS'],
 ['usdeur','DEXUSEU'],['usdgbp','DEXUSUK'],['usdchf','DEXSZUS'],['usdtwd','USDTWD']];
function liveFx(){{
 FXPAIRS.forEach(function(p){{
  fetch('api/'+p[0]).then(function(r){{return r.json();}}).then(function(j){{
   if(!j||j.rate==null)return;
   var r=R.find(function(x){{return x.id===p[1];}});if(!r)return;
   r.latest=j.rate;r.latest_date=j.src||'실시간';
   table();if(sel===p[1])detail(p[1]);
  }}).catch(function(){{}});
 }});
}}
liveFx();setInterval(liveFx,300000);
}})();
</script></body></html>"""


# ── 재생성(자정 regen·startup 훅) ─────────────────────────────────────────
def regenerate_fred_boards() -> None:
    """ppi.html + liquidity.html 재생성 — 자정 대시보드 regen + startup 백그라운드
    (둘 다 무조건 — 배포 후 최신 렌더 코드가 즉시 화면에, 실수 #11. FRED 캐시
    12h 라 같은 날 재실행은 사실상 무료). 실패 시 기존 파일 유지(graceful).
    ⚠️ 네트워크 ~90콜 — 반드시 스레드/to_thread 로 호출(이벤트루프 차단 금지)."""
    from bot.dashboard import ARCHIVE_ROOT, _inject_update_banner
    _ensure_chartjs()
    try:
        rows, margins, dropped = _load_ppi()
        html = _inject_update_banner(render_ppi_page(rows, margins,
                                                     dropped=dropped))
        (ARCHIVE_ROOT / "ppi.html").write_text(html, encoding="utf-8")
        log.info("fred_boards: ppi.html regenerated (%d series, %d margins)",
                 len(rows), len(margins))
    except Exception:
        log.exception("fred_boards: ppi regen failed")
    try:
        rows, derived, score = _load_liq()
        html = _inject_update_banner(render_liquidity_page(rows, derived, score))
        (ARCHIVE_ROOT / "liquidity.html").write_text(html, encoding="utf-8")
        log.info("fred_boards: liquidity.html regenerated (%d series, score=%s)",
                 len(rows), score)
    except Exception:
        log.exception("fred_boards: liquidity regen failed")
