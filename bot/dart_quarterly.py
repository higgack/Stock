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
# 저량(STOCK) = 시점 잔액. 4분기 단독을 낼 때 **차분하면 안 되는** 항목이다
# (연말 재고 − 3분기말 재고 = 재고의 '증감'이지 4분기의 재고가 아니다).
_STOCK_KEYS = {"유동자산", "비유동자산", "자산총계", "유동부채",
               "비유동부채", "부채총계", "이익잉여금", "자본총계",
               "재고자산"}
# 현금흐름표 계정 — DART 는 **누적**으로 준다(연초~해당분기말).
# ⚠️ 손익(sj_div=IS)은 `thstrm_amount` 자체가 '당기 3개월'(단일분기)인데
# CF 는 3개월 개념이 없어 그 필드가 곧 누적이다. 둘을 같이 다루면 분기
# 값이 누적으로 뜬다 — 실측(2026-08-21 농심): 25.2Q 1,145 → 25.3Q 1,634
# → 25.4Q 2,008 로 **단조 증가**하고 25.4Q 가 FY2025 와 **완전히 같았다**.
# 그래서 (a) 4분기 차분에서 제외해 원본 누적을 보존하고
#        (b) 시계열이 다 모인 뒤 `_undo_cumulative_cf` 가 한 번에 되돌린다.
_CUM_KEYS = {"영업활동현금흐름", "유형자산취득", "무형자산취득"}


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


def _mark_component(out: dict, src_fin: dict) -> dict:
    """`_component_accounts`(dart_client 가 심음)를 결과 dict 로 이어 나른다.

    '총액을 대표하지 못하는 계정에서 온 값' 이라는 사실만 옮긴다 — 값은
    보존하고 렌더러가 표기한다(사용자 2026-08-16 B안: 매핑 제거 시 다른
    종목이 회귀하므로 데이터는 살리고 오인만 막는다)."""
    comp = (src_fin or {}).get("_component_accounts")
    if not comp:
        return out
    # 계정 불일치 가드가 이미 None 으로 비운 항목은 제외 — 안 그러면 한 셀에
    # "매출 공백 … 비워둠" 과 "매출 = 이자수익 … 원자료 그대로" 가 동시에
    # 붙어 서로 모순된다("— ⚠️", 2026-08-16 독립 리뷰).
    kept = {k: v for k, v in comp.items() if out.get(k) is not None}
    if kept:
        out["_component_accounts"] = kept
    return out


def _group_name(canonical: str, idx) -> str:
    """계정 그룹 인덱스 → 사람이 읽는 대표 계정명. 모르면 `그룹N`."""
    try:
        from bot.dart_client import _ACCOUNT_GROUPS
        return _ACCOUNT_GROUPS[canonical][int(idx)][0]
    except Exception:
        return f"그룹{idx}"


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
        if k in _STOCK_KEYS or k in _CUM_KEYS:
            # 저량은 차분 금지, 누적(CF)은 **원본 누적을 그대로** 넘긴다
            # — 단일분기 환산은 `_undo_cumulative_cf` 가 전 분기 일괄로.
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
                # ⚠️ **어느 계정끼리 어긋났는지**를 남긴다. 정규화 키("매출")만
                # 남기면 화면 각주가 "서로 다른 계정" 이라고만 말해, 이게
                # 고칠 수 있는 건지(총액끼리 라벨만 다름) 원천 한계인지
                # (구성요소가 끼어듦) 아무도 못 가른다(사용자 2026-08-18 CJ).
                mismatched.append(f"{k}: {_group_name(k, a)} ↔ {_group_name(k, b)}")
                continue
            out[k] = v - prev_v
    if mismatched:
        out["_anomaly_account_mismatch"] = True
        out["_mismatched_accounts"] = mismatched
    # 구성요소 계정('이자수익' 등)이 승자였던 항목은 값이 총액이 아니다 —
    # 값은 그대로 두고 그 사실만 이어 나른다(사용자 2026-08-16 B안).
    _mark_component(out, cum_now)
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


def _Q_SORT_KEY(yr_rc: tuple[int, str]) -> tuple[int, int]:
    """(연도, 분기순번) — 어느 쪽이 더 최신 분기인지 비교용."""
    y, rc = yr_rc
    return (y, _Q_ORDER.index(rc) if rc in _Q_ORDER else -1)


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
    if fs_div == "CFS":
        # ⚠️ CFS 가 **아예 없을 때만** OFS 로 넘어가면, 연결 작성을 중단한
        # 회사는 옛 분기에서 멈춘 채로 굳는다 — 노바렉스 194700 은 26.1Q·
        # 26.2Q 가 OFS 에만 있는데 CFS 에서 25.4Q 가 잡혀 화면이 **두 분기
        # 뒤처졌다**(2026-08-18 프로브: `26.2Q CFS=없음 · OFS=있음`).
        # 어느 쪽이 더 **최신 분기**를 갖는지로 정한다.
        alt = probe_latest_reprt_code(dart, ticker, fs_div="OFS")
        if alt and (not latest or _Q_SORT_KEY(alt) > _Q_SORT_KEY(latest)):
            latest, effective_fs_div = alt, "OFS"
    if not latest:
        return None
    latest_year, latest_rc = latest

    from bot.dart_client import calc_kr_financial_ratios
    out: list[dict] = []
    y, rc = latest_year, latest_rc
    # ⚠️ **한 분기 더** 받는다. 현금흐름은 누적이라 단일분기로 되돌리려면
    # 직전 분기의 누적이 필요하다 — 정확히 n 개만 받으면 창의 **가장
    # 오래된 분기가 늘 빈칸**이 된다(경계값 요청은 원천이 하루만 덜 줘도
    # 조용히 실패한다, 실수 #44a). 여분은 환산 뒤 잘라낸다.
    _fetch_n = n + 1
    for _ in range(_fetch_n):
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
            _mark_component(fin, entry["financials"])
        out.append({
            "label": quarter_label(y, rc), "year": y, "quarter": _Q_NUM[rc],
            "reprt_code": rc, "fs_div": effective_fs_div,
            "financials": fin, "ratios": calc_kr_financial_ratios(fin),
        })
        idx = _Q_ORDER.index(rc)
        y, rc = (y - 1, "11011") if idx == 0 else (y, _Q_ORDER[idx - 1])
    out.reverse()   # 오래된 → 최신
    # 총액 계정을 안 주는 회사(증권·은행·보험)의 매출을 FnGuide 총액으로.
    # ⚠️ 이 경로가 빠져 있었다(2026-08-19 NH투자증권 분기 인포그래픽):
    # `stock_snapshot` 만 보강해서 K-IFRS 요약은 총액인데 **분기 차트·타일은
    # 이자수익**이라 "매출 5,787억 < 영업이익 6,812억" 이 그대로 남았다.
    # 검산·혼합금지는 헬퍼 안에 있다 — 하나라도 못 채우면 전부 원래대로.
    try:
        from bot.kr_revenue_fallback import fill_series
        from bot.dart_client import calc_kr_financial_ratios as _ratios
        if fill_series(ticker, [(e["year"], e["quarter"], e["financials"])
                                for e in out]):
            for e in out:
                e["ratios"] = _ratios(e["financials"])
    except Exception as exc:                                   # noqa: BLE001
        log.info("dart_quarterly: %s 매출 총액 보강 건너뜀: %s", ticker, exc)
    # ROE·ROA 는 **TTM(최근 4분기 합)** 으로 — 분기 순이익 하나로 계산하면
    # 시장 표기(네이버·FnGuide)와 3배 어긋난다(사용자 2026-08-19).
    # ⚠️ 반드시 매출 보강 **뒤에** — 위 블록이 ratios 를 통째로 다시 만든다.
    try:
        from bot.dart_client import apply_ttm_returns
        apply_ttm_returns(out)
    except Exception as exc:                                   # noqa: BLE001
        log.info("dart_quarterly: %s TTM 수익성 계산 건너뜀: %s", ticker, exc)
    # 현금흐름 누적 → 단일분기. **FCF 계산 전에** 되돌려야 한다.
    _undo_cumulative_cf(out)
    # 기준으로만 쓴 여분 분기를 잘라낸다(호출부 계약은 n 개 그대로).
    if len(out) > n:
        out = out[-n:]
    # FCF — 산식은 `bot.fcf` 한 곳(#38). 분기실적 차트와 밸류에이션 표가
    # 같은 값을 보게 **여기서** 붙인다(화면마다 계산하면 갈라진다).
    _attach_fcf(out)
    return out or None


def _undo_cumulative_cf(entries: list[dict] | None) -> int:
    """현금흐름 계정의 **누적 → 단일분기** 환산. 바꾼 항목 수를 돌려준다.

    입력은 **오래된 → 최신** 순의 분기 시계열. 회계연도가 바뀌면 누적이
    0 에서 다시 시작하므로 연도별로 끊는다.

    ⚠️ **직전 분기가 바로 앞 분기일 때만** 차분한다. 25.2Q 다음이 25.4Q
    라면(3분기 보고서 누락) 두 분기치를 한 분기로 표기하게 된다 — 그럴
    땐 값을 **비운다**(틀린 숫자보다 빈칸, 실수 #29 의 같은 규율).
    """
    n = 0
    prev_q: dict[int, int] = {}        # 연도 → 직전 분기 번호
    prev_v: dict[int, dict] = {}       # 연도 → 그 분기의 누적값
    for e in entries or []:
        fin = (e or {}).get("financials") or {}
        y, q = e.get("year"), e.get("quarter")
        if not y or not q:
            continue
        cum = {k: fin.get(k) for k in _CUM_KEYS}
        if q > 1:
            base = prev_v.get(y) if prev_q.get(y) == q - 1 else None
            for k in _CUM_KEYS:
                if cum[k] is None:
                    continue
                if base is not None and base.get(k) is not None:
                    fin[k] = cum[k] - base[k]
                    n += 1
                else:
                    # 직전 누적을 모르면 단일분기를 만들 수 없다.
                    fin[k] = None
        prev_q[y], prev_v[y] = q, cum
    return n


def _attach_fcf(entries: list[dict] | None) -> int:
    """DART 현금흐름 계정 → `financials["FCF"]`. 채운 개수를 돌려준다.

    ⚠️ DART 는 CAPEX 를 **단일 계정으로 주지 않는다** — 유형자산·무형자산
    취득이 따로 온다. 둘을 더해야 FnGuide CAPEX 와 맞는다. 한쪽만 있는
    회사도 있어 있는 것만 합산하되, **둘 다 없으면 FCF 를 만들지 않는다**
    (영업현금흐름을 그대로 FCF 로 쓰면 설비투자가 큰 회사가 크게 부풀려진다).
    """
    from bot.fcf import fcf_from_parts
    n = 0
    for e in entries or []:
        fin = (e or {}).get("financials") or {}
        capex_parts = [fin.get(k) for k in ("유형자산취득", "무형자산취득")
                       if fin.get(k) is not None]
        v = None
        if capex_parts:
            v = fcf_from_parts(fin.get("영업활동현금흐름"),
                               sum(abs(float(x)) for x in capex_parts))
        if v is None:
            # ⚠️ **지운다.** 누적 dict 에 이미 FCF 가 있으면 4분기 차분이
            # 그걸 그대로 차분해 남긴다 — 그런데 같은 차분에서 구성요소가
            # `_src` 불일치로 None 이 될 수 있다(계정 라벨이 보고서마다
            # 다를 때). 그러면 화면엔 재료가 빈칸인데 FCF 만 숫자가 남아
            # **눈으로 검산하면 안 맞는다**(실수 #33). 재료와 결과는 항상
            # 같이 있거나 같이 없어야 한다.
            fin.pop("FCF", None)
            continue
        fin["FCF"] = v
        n += 1
    return n
