"""분기별 실적 시계열 조립 — DART 정기보고서에서 단일분기 실적 추출 +
최신분기 자동탐지.

2026-08-19 VM 실측(삼성전자 25.반기/25.3분기 원본 API 응답) 재확인 — DART
분기/반기보고서(reprt_code 11012=반기·11013=1분기·11014=3분기)의 손익계산서
항목은 **thstrm_amount 자체가 이미 '당기 3개월'(단일분기) 값**이다(라벨이
"반기"/"3분기"라 누적처럼 보이지만 실측 결과 반대). 별도 컬럼
thstrm_add_amount 가 '당기누적'(연초~해당분기말)이다. 그래서 1~3분기는
DART 가 이미 표준화한 값을 그대로 쓰면 되고, **4분기만** 예외 — 사업보고서
(11011)는 thstrm_amount 가 연간 전체이고 당기누적 컬럼 자체가 없어서, 4분기
단독 = 연간(11011) − 9개월누적(11014 의 thstrm_add_amount) 으로 직접
차분해야 한다. (초판은 반대로 짐작해 모든 분기를 누적으로 오인하고 차분해
2~3배 부풀린/음수인 값을 냈던 결함 — 2026-08-19 실사용자 VM 검증으로 발견,
이 파일은 그 수정판.)

대시보드용 트레일링 분기 시계열을 조립하고, "다음 분기가 나오면 자동으로"
최신 분기를 갖게 되는 탐지 로직(probe_latest_reprt_code)을 제공한다 —
하드코딩된 분기 문자열은 어디에도 없다.

데이터 vs 환각(CLAUDE.md): DART 가 준 숫자를 그대로 쓰거나(1~3분기) 두
누적치를 뺄 뿐(4분기), 어떤 값도 창작·보정하지 않는다. 차분 결과가
논리적으로 불가능(매출 음수 등)해도 값을 조작하지 않고 anomaly 플래그만
붙인다.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("bot.dart_quarterly")

# 정기보고서 reprt_code 순서(같은 연도 내 진행) + 분기번호.
_Q_ORDER = ["11013", "11012", "11014", "11011"]
_Q_NUM = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}

# 손익계산서 항목(4분기 파생 시 차분 대상) vs 재무상태표 항목(시점값 →
# 차분 금지, 연간 시점값 그대로 사용). 키는 bot.dart_client 의
# _DART_CODE_MAP/_DART_NAME_MAP canonical 키와 동일.
_FLOW_KEYS = {"매출", "매출원가", "매출총이익", "판관비", "영업이익",
              "금융수익", "금융비용", "세전이익", "법인세비용",
              "당기순이익", "EPS"}
_STOCK_KEYS = {"유동자산", "비유동자산", "자산총계", "유동부채",
               "비유동부채", "부채총계", "이익잉여금", "자본총계"}


def quarter_label(year: int, reprt_code: str) -> str:
    """'26.2Q' 형식(참고 이미지와 동일 표기)."""
    return f"{year % 100:02d}.{_Q_NUM[reprt_code]}Q"


def _flag_revenue_anomaly(fin: dict) -> dict:
    """매출은 K-IFRS상 논리적으로 음수가 나올 수 없는 항목 — 음수 발생은
    거의 항상 전기 재무제표 정정(restatement)이 반영된 것(4분기 파생뿐
    아니라 DART 가 준 1~3분기 원자값 자체가 음수인 경우도 이론상 있을 수
    있어 두 경로 모두에 적용, 2026-08-19 code-review 발견 — 4분기 파생
    경로에만 있던 걸 공용화). 값은 그대로 보존(날조 금지)하고 렌더러가
    판단할 수 있게 플래그만 남긴다."""
    if fin.get("매출") is not None and fin["매출"] < 0:
        fin["_anomaly_revenue_negative"] = True
    return fin


def _diff_quarter(cum_now: dict, cum_prev: dict | None) -> dict:
    """4분기 단독 = 연간(now) − 9개월누적(prev). 저량(STOCK) 항목은
    차분하지 않고 연간 시점값 그대로 유지.

    ⚠️ **계정 일치 가드**: 한 canonical 키에 여러 DART 계정이 매핑되는
    항목(금융지주의 `매출` = 영업수익/매출액/이자수익)은 두 보고서에서
    서로 다른 계정이 채택될 수 있고, 그러면 **다른 계정끼리 빼게 된다**.
    실제로 메리츠금융지주 TTM 매출이 -10.30조로 나온 원인이 이것이었다
    (사용자 2026-08-16). `_extract_dart_financials` 가 남긴 `_src`(채택된
    **동의어 그룹 인덱스**)를 비교해 다르면 그 항목을 `None` 으로 두고
    플래그만 남긴다 — 틀린 숫자보다 빈칸이 낫다(날조 금지).

    ⚠️ 비교 단위가 계정 '이름'이 아니라 '그룹'인 이유: 같은 회사도 연간
    보고서엔 '매출액', 분기보고서엔 '수익(매출액)' 처럼 다른 라벨을 쓴다.
    이름을 직접 비교하면 멀쩡한 회사의 4분기 매출·TTM·PSR 이 통째로
    비어 버린다(2026-08-16 독립 리뷰 지적)."""
    now_src = (cum_now or {}).get("_src") or {}
    prev_src = (cum_prev or {}).get("_src") or {}
    out: dict = {}
    mismatched: list[str] = []
    for k, v in (cum_now or {}).items():
        if k.startswith("_"):
            continue        # 사이드채널(_src 등)은 산술 대상이 아니다
        if k in _STOCK_KEYS:
            out[k] = v
            continue
        prev_v = (cum_prev or {}).get(k)
        if v is None:
            out[k] = None
        elif prev_v is None:
            out[k] = v
        else:
            # 양쪽 다 그룹을 알 때만 비교(옛 캐시엔 _src 가 없다 →
            # 비교 불가면 종전대로 차분, graceful).
            a, b = now_src.get(k), prev_src.get(k)
            if a is not None and b is not None and a != b:
                out[k] = None
                mismatched.append(k)
                continue
            out[k] = v - prev_v
    if mismatched:
        out["_anomaly_account_mismatch"] = True
        out["_mismatched_accounts"] = mismatched
    return _flag_revenue_anomaly(out)


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
    오래된→최신 순. 1~3분기는 DART 가 이미 표준화한 단일분기 값(thstrm_
    amount)을 그대로 쓰고, 4분기만 연간−9개월누적으로 파생한다(모듈
    docstring 참조). 시리즈 전체는 동일 fs_div(분기마다 CFS/OFS 혼용
    금지) — CFS 로 최신분기 프로브가 전부 실패하면(소형/비상장계열 없는
    회사 등) OFS 로 1회 폴백해 시리즈 전체를 그 기준으로 조회. 프로브
    실패 또는 과거로 갈수록 DART 미제공(상장 이력 짧음 등)이면 있는
    만큼만 반환, 전무하면 None(DATA OFFLINE)."""
    latest = probe_latest_reprt_code(dart, ticker, fs_div=fs_div)
    effective_fs_div = fs_div
    if not latest and fs_div == "CFS":
        latest = probe_latest_reprt_code(dart, ticker, fs_div="OFS")
        effective_fs_div = "OFS"
    if not latest:
        return None
    latest_year, latest_rc = latest

    from bot.dart_client import calc_kr_financial_ratios
    out: list[dict] = []
    y, rc = latest_year, latest_rc
    for _ in range(n):
        entry = dart.get_normalized_financials(ticker, year=y,
                                               fs_div=effective_fs_div,
                                               reprt_code=rc)
        if entry is None or not entry.get("financials"):
            break   # 과거로 갈수록 데이터 없음 — 있는 만큼만 반환
        if rc == "11011":
            # 4분기 단독 = 연간(thstrm_amount, 이미 연간 전체) − 9개월
            # 누적(3분기보고서 thstrm_add_amount). 9개월 누적을 못 구하면
            # 4분기를 안전하게 산출할 수 없어 그대로 중단(날조 금지).
            q3 = dart.get_normalized_financials(ticker, year=y,
                                                fs_div=effective_fs_div,
                                                reprt_code="11014")
            nine_mo = q3.get("financials_cumulative") if q3 else None
            if nine_mo is None:
                break
            fin = _diff_quarter(entry["financials"], nine_mo)
        else:
            # 이미 단일분기(재차분 금지) — 그래도 DART 원자값 자체가
            # 음수인 극단 케이스에 대비해 동일 anomaly 체크 적용.
            # `_src`(채택 계정명)는 보고서 간 차분 검증용 내부 재료라
            # 공개 시리즈 스키마엔 싣지 않는다(4분기 경로는 _diff_quarter
            # 가 이미 `_` 접두 키를 걸러낸다).
            fin = _flag_revenue_anomaly(
                {k: v for k, v in entry["financials"].items() if k != "_src"})
        out.append({
            "label": quarter_label(y, rc), "year": y, "quarter": _Q_NUM[rc],
            "reprt_code": rc, "fs_div": effective_fs_div,
            "financials": fin, "ratios": calc_kr_financial_ratios(fin),
        })
        idx = _Q_ORDER.index(rc)
        y, rc = (y - 1, "11011") if idx == 0 else (y, _Q_ORDER[idx - 1])
    out.reverse()   # 오래된 → 최신
    return out or None
