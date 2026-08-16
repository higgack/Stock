"""분기별 실적 시계열 조립 — DART 정기보고서(누적치) 차분 + 최신분기 자동탐지.

DART 정기보고서 4종은 같은 회계연도 내 **누적치**다: 11013(1분기보고서)=
1~3월 누적, 11012(반기보고서)=1~6월 누적, 11014(3분기보고서)=1~9월 누적,
11011(사업보고서)=1~12월 누적(연간). 단일분기 실적 = 누적(이번) - 누적
(직전, 같은 연도 내 — 1분기는 직전이 없어 그대로). 대시보드용 트레일링
분기 시계열을 조립하고, "다음 분기가 나오면 자동으로" 최신 분기를 갖게
되는 탐지 로직(probe_latest_reprt_code)을 제공한다 — 하드코딩된 분기
문자열은 어디에도 없다.

데이터 vs 환각(CLAUDE.md): 이 모듈은 순수 산술(뺄셈)만 한다 — DART 가 준
숫자를 그대로 쓰거나 인접 두 숫자를 뺄 뿐, 어떤 값도 창작·보정하지 않는다.
차분 결과가 논리적으로 불가능(매출 음수 등)해도 값을 조작하지 않고 anomaly
플래그만 붙인다(원인은 대개 전기 재무제표 정정 — 임의 보정은 그 자체가
날조).
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("bot.dart_quarterly")

# 4종 reprt_code 순서(같은 연도 내 진행) + 직전(차분 기준) + 분기번호.
_Q_ORDER = ["11013", "11012", "11014", "11011"]
_Q_PREV = {"11013": None, "11012": "11013", "11014": "11012", "11011": "11014"}
_Q_NUM = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}

# 손익계산서 항목(누적치 → 반드시 차분) vs 재무상태표 항목(시점값 → 차분 금지).
# 키는 bot.dart_client._DART_CODE_MAP/_DART_NAME_MAP 의 canonical 키와 동일.
_FLOW_KEYS = {"매출", "매출원가", "매출총이익", "판관비", "영업이익",
              "금융수익", "금융비용", "세전이익", "법인세비용",
              "당기순이익", "EPS"}
_STOCK_KEYS = {"유동자산", "비유동자산", "자산총계", "유동부채",
               "비유동부채", "부채총계", "이익잉여금", "자본총계"}


def quarter_label(year: int, reprt_code: str) -> str:
    """'26.2Q' 형식(참고 이미지와 동일 표기)."""
    return f"{year % 100:02d}.{_Q_NUM[reprt_code]}Q"


def _diff_quarter(cum_now: dict, cum_prev: dict | None) -> dict:
    """단일분기 = 누적(now) - 누적(prev, 같은 연도 내). prev 가 없으면(1분기)
    누적값 그대로가 곧 단일분기. 재무상태표(시점값) 항목은 차분하지 않고
    그대로 유지."""
    out: dict = {}
    for k, v in (cum_now or {}).items():
        if k in _STOCK_KEYS:
            out[k] = v
            continue
        prev_v = (cum_prev or {}).get(k)
        if v is None:
            out[k] = None
        elif prev_v is None:
            out[k] = v
        else:
            out[k] = v - prev_v
    # 매출은 K-IFRS상 논리적으로 음수가 나올 수 없는 항목 — 음수 발생은
    # 거의 항상 전기 재무제표 정정(restatement)이 반영된 것. 값은 그대로
    # 보존(날조 금지)하고 렌더러가 판단할 수 있게 플래그만 남긴다.
    if out.get("매출") is not None and out["매출"] < 0:
        out["_anomaly_revenue_negative"] = True
    return out


def _needed_year_reprt_pairs(latest_year: int, latest_reprt_code: str,
                              n: int) -> list[tuple[int, str]]:
    """최신 (year,reprt_code)에서 역순 n개 분기 조립에 필요한 원자
    (year,reprt_code) unique 목록(오름차순). 각 분기 diff 에는 그 분기
    reprt_code + 같은 연도 직전 reprt_code(1분기는 불요)가 필요 — 인접
    분기끼리 직전값을 공유하므로 실제 DART 호출 수는 n 보다 적거나 같다."""
    seq: list[tuple[int, str]] = []
    y, rc = latest_year, latest_reprt_code
    for _ in range(n):
        seq.append((y, rc))
        idx = _Q_ORDER.index(rc)
        if idx == 0:
            y, rc = y - 1, "11011"
        else:
            rc = _Q_ORDER[idx - 1]
    needed: set[tuple[int, str]] = set()
    for (yy, rr) in seq:
        needed.add((yy, rr))
        prev = _Q_PREV[rr]
        if prev:
            needed.add((yy, prev))
    return sorted(needed)


def _latest_candidate(today: date) -> tuple[int, str]:
    """오늘 날짜 기준 '이론상 가장 최근에 공시됐을 수 있는' (year,
    reprt_code) — dart_client.DartClient.next_earnings_window() 의 법정
    제출기한(분기 45일/연간 90일, Dec 결산 기준)과 동일 경계를 역으로
    사용. 실제 공시 여부는 확인 안 함 — probe_latest_reprt_code 의 탐색
    시작점일 뿐(틀려도 probe 가 역순으로 자동 보정)."""
    y, m, d = today.year, today.month, today.day
    if (m, d) < (4, 1):
        return (y - 1, "11014")        # 작년 3분기(11/14 마감)
    if (m, d) < (5, 15):
        return (y - 1, "11011")        # 작년 사업보고서(4/1 마감)
    if (m, d) < (8, 14):
        return (y, "11013")            # 올해 1분기(5/15 마감)
    if (m, d) < (11, 14):
        return (y, "11012")            # 올해 반기(8/14 마감)
    return (y, "11014")                # 올해 3분기(11/14 마감)


def probe_latest_reprt_code(dart, ticker: str, fs_div: str = "CFS"
                            ) -> tuple[int, str] | None:
    """달력상 최신 후보에서 최대 4단계 역순으로 실제 DART 응답을 프로브해
    '진짜 공시된' 최신 (year, reprt_code)를 찾는다. get_normalized_financials
    자체가 7일 디스크캐시(dart_client.py)라 반복 프로브 비용은 낮음.
    ⚠️ 이 함수 자체는 캐시 없이 항상 라이브 프로브 — 호출부(대시보드
    API 핸들러)가 결과를 6~12h 짧게 캐시하는 것을 권장(매 페이지뷰마다
    이 루프를 돌리지 않도록). creds 부재/전부 실패 시 None."""
    y, rc = _latest_candidate(date.today())
    for _ in range(4):
        r = dart.get_normalized_financials(ticker, year=y, fs_div=fs_div,
                                           reprt_code=rc)
        if r and r.get("financials"):
            return (y, rc)
        idx = _Q_ORDER.index(rc)
        y, rc = (y - 1, "11011") if idx == 0 else (y, _Q_ORDER[idx - 1])
    return None


def get_quarterly_series(dart, ticker: str, n: int = 6, fs_div: str = "CFS"
                         ) -> list[dict] | None:
    """최근 n개 분기 트레일링 시계열. 각 항목
    {label, year, quarter, reprt_code, fs_div, financials, ratios},
    오래된→최신 순. 시리즈 전체는 동일 fs_div(분기마다 CFS/OFS 혼용 금지 —
    비교 왜곡 방지) — CFS 로 최신분기 프로브가 전부 실패하면(소형/비상장
    계열 없는 회사 등) OFS 로 1회 폴백해 시리즈 전체를 그 기준으로 조회.
    프로브 실패(사업보고서 하나도 없음 등) 또는 과거로 갈수록 DART 미제공
    (상장 이력 짧음 등)이면 있는 만큼만 반환, 전무하면 None(DATA OFFLINE)."""
    latest = probe_latest_reprt_code(dart, ticker, fs_div=fs_div)
    effective_fs_div = fs_div
    if not latest and fs_div == "CFS":
        latest = probe_latest_reprt_code(dart, ticker, fs_div="OFS")
        effective_fs_div = "OFS"
    if not latest:
        return None
    latest_year, latest_rc = latest
    pairs = _needed_year_reprt_pairs(latest_year, latest_rc, n)
    raw: dict[tuple[int, str], dict | None] = {
        p: dart.get_normalized_financials(ticker, year=p[0],
                                          fs_div=effective_fs_div,
                                          reprt_code=p[1])
        for p in pairs
    }

    from bot.dart_client import calc_kr_financial_ratios
    out: list[dict] = []
    y, rc = latest_year, latest_rc
    for _ in range(n):
        cum_now = (raw.get((y, rc)) or {}).get("financials")
        if cum_now is None:
            break   # 과거로 갈수록 데이터 없음 — 있는 만큼만 반환
        prev_rc = _Q_PREV[rc]
        cum_prev = ((raw.get((y, prev_rc)) or {}).get("financials")
                   if prev_rc else None)
        fin = _diff_quarter(cum_now, cum_prev)
        out.append({
            "label": quarter_label(y, rc), "year": y, "quarter": _Q_NUM[rc],
            "reprt_code": rc, "fs_div": effective_fs_div,
            "financials": fin, "ratios": calc_kr_financial_ratios(fin),
        })
        idx = _Q_ORDER.index(rc)
        y, rc = (y - 1, "11011") if idx == 0 else (y, _Q_ORDER[idx - 1])
    out.reverse()   # 오래된 → 최신
    return out or None
