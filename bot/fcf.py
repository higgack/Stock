"""잉여현금흐름(FCF) 단일 산출기 — 전 시장 공통.

사용자 2026-08-21: "FCF 를 밸류에이션탭에 분기/연간 모두 부채비율 밑에 …
분기실적탭에도 당기순이익 밑에 별도의 차트로 … 이건 모든 나라에 적용이야."

⚠️ 산식은 **여기 한 곳**이다. 화면이 셋(밸류에이션 분기표·연간표·분기실적
차트)인데 각자 계산하면 같은 회사가 화면마다 다른 FCF 를 갖는다(실수 #38).

산식(원천이 확정해 줬다 — FnGuide 실측 FY2025: 영업활동현금흐름 1,241 −
CAPEX 230 = FCF 1,011):

    FCF = 영업활동현금흐름 − |CAPEX|

⚠️ `abs()` 를 쓰는 이유. CAPEX 는 원천마다 부호 규약이 갈린다 — yfinance 는
음수(유출)로, FnGuide·DART 는 양수로 준다. 부호를 그대로 더하거나 빼면
**한쪽 시장에서 FCF 가 영업현금흐름의 두 배**가 된다(둘 다 '맞는 값'처럼
보여서 눈으로는 안 잡힌다, 실수 #34 의 부호판). CAPEX 는 정의상 유출이므로
크기만 쓴다.

⚠️ 원천이 `Free Cash Flow` 를 **직접** 주면 그걸 쓴다. 우리가 다시 계산하면
원천의 정의(임차자산·무형자산 포함 여부)와 갈라진다 — 비교표에 자체계산을
넣지 말라는 규칙(#32)의 같은 이유다.

⚠️ 재료가 없으면 **None**. 0 을 넣으면 '현금흐름 0' 이라는 없는 사실을
그린 게 된다(빈칸이 틀린 숫자보다 낫다).
"""

from __future__ import annotations

# yfinance 라인아이템 후보. 앞이 없으면 뒤를 쓴다.
# ⚠️ 이름은 yfinance 판올림마다 바뀐다 — 하나만 적으면 조용히 전 종목이
# 빈칸이 된다. 실제로 'Total Cash From Operating Activities'(구판) →
# 'Operating Cash Flow'(신판)로 바뀐 전례가 있다.
_FCF_NAMES = ("Free Cash Flow", "FreeCashFlow")
_OCF_NAMES = ("Operating Cash Flow",
              "Total Cash From Operating Activities",
              "Cash Flow From Continuing Operating Activities",
              "Net Cash Provided By Used In Operating Activities")
_CAPEX_NAMES = ("Capital Expenditure", "Capital Expenditures",
                "Purchase Of PPE", "Net PPE Purchase And Sale")


def _num(v):
    """숫자만 통과. 문자열·None·NaN 은 None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN 배제


def _first(row: dict, names: tuple[str, ...]):
    for nm in names:
        v = _num((row or {}).get(nm))
        if v is not None:
            return v
    return None


def fcf_from_row(row: dict | None) -> float | None:
    """현금흐름표 한 기간(dict) → FCF. 재료가 없으면 None.

    `row` 는 라인아이템명 → 값 매핑(yfinance `_df_to_rows` 산출과 동형).
    """
    if not row:
        return None
    direct = _first(row, _FCF_NAMES)
    if direct is not None:
        return direct                     # 원천이 직접 준 값이 정본
    return fcf_from_parts(_first(row, _OCF_NAMES), _first(row, _CAPEX_NAMES))


def fcf_from_parts(ocf, capex) -> float | None:
    """영업활동현금흐름 · CAPEX → FCF(= OCF − |CAPEX|).

    ⚠️ 둘 중 하나라도 없으면 None. CAPEX 만 없을 때 **OCF 를 그대로 FCF
    로 쓰면 안 된다** — 설비투자가 큰 회사일수록 크게 부풀려진다."""
    o, c = _num(ocf), _num(capex)
    if o is None or c is None:
        return None
    return o - abs(c)


def attach_to_series(qs: list | None, cf_rows: list | None) -> int:
    """분기 시계열에 `financials["FCF"]` 를 채운다 → 채운 개수.

    ⚠️ **기간(period)으로 조인**한다. 두 표(손익·현금흐름)가 같은 순서로
    온다고 가정하면 원천이 한쪽만 한 분기 덜 줄 때 **전 분기가 한 칸씩
    밀린다**(실수 #46 — 위치로 매기지 말 것). 화면은 멀쩡해 보이고 값만
    통째로 틀린다.
    """
    if not qs or not cf_rows:
        return 0
    by_period = {str(r.get("period", "")): r for r in cf_rows
                 if isinstance(r, dict)}
    n = 0
    for q in qs:
        if not isinstance(q, dict):
            continue
        row = by_period.get(str(q.get("period", "")))
        if row is None:
            continue
        v = fcf_from_row(row)
        if v is not None:
            q.setdefault("financials", {})["FCF"] = v
            n += 1
    return n
