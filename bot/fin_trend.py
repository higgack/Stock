"""밸류에이션 탭 '재무추이' 표의 항목 — **전 시장 공통**(yfinance 원천).

KR 은 DART 로 같은 표를 만든다(`stock_snapshot.collect_kr_financials`).
사용자 2026-08-24: "미국종목도 한국종목처럼 밸류에이션 탭 똑같이 할수 있어?
… 최대한 할 수 있는 나라 모두 적용해줘. 한국처럼." — 그 표를 다른 시장에도
붙인다.

⚠️ **추가 네트워크 0**: `stock_snapshot._collect_financials` 가 이미 손익·
재무상태·현금흐름을 분기 8개·연간 5개씩 시장 게이트 없이 담는다.

⚠️ 비율·ROE 산식은 **DART 경로와 같은 함수**를 쓴다(`calc_kr_financial_ratios`
· `apply_ttm_returns` · `apply_annual_returns`) — 복제하면 같은 지표가 시장마다
갈린다(#38·#147, ROE 평균분모 규약을 두 번 따로 고친 적이 있다). 그래서 야후
라인아이템을 canonical 한글 키로 옮긴 뒤 **그 함수들에 태운다**.

⚠️ 화면에 찍는 `당기순이익` 은 다른 탭과 **같은 라인아이템**(`Net Income`)이다
— 분기실적 인포그래픽·재무제표 탭 차트가 그 키를 쓰므로, 여기서만 다른 키를
고르면 같은 회사의 같은 분기가 탭마다 다른 숫자가 된다(#34). ROE 분자·분모는
그와 별개로 **같은 급**끼리 고른다(#225) — 아래 `_ratio_input` 참조.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# 화면에 그대로 찍는 항목 — 다른 탭과 같은 라인아이템을 쓴다.
from bot.quarterly_series import _ITEM_CANDIDATES, q_label_from_period

# 비율 계산에만 쓰는 재무상태표 항목.
# ⚠️ `자본총계`(부채비율의 분모)와 `지배주주자본`(ROE 의 분모)은 **다른 것**이다.
# 야후는 `Total Equity Gross Minority Interest` = 비지배 포함 총자본,
# `Stockholders Equity` = 지배주주 귀속분으로 준다. 부채비율은
# `Total Assets = 부채(NMI 제외) + 총자본` 항등식이 성립하는 쌍이라야 하고,
# ROE 는 분자(지배주주 귀속 순이익)와 같은 급이라야 한다(#225).
_BS_CANDIDATES: dict[str, tuple[str, ...]] = {
    "자산총계": ("Total Assets",),
    "부채총계": ("Total Liabilities Net Minority Interest", "Total Liabilities"),
    "자본총계": ("Total Equity Gross Minority Interest", "Stockholders Equity"),
    "지배주주자본": ("Stockholders Equity",),
    "유동자산": ("Current Assets",),
    "유동부채": ("Current Liabilities",),
}
# ROE 분자 후보 — 지배주주 귀속분이 명시된 키가 있으면 그것.
_NET_OWNER = ("Net Income Common Stockholders",)
_NET_TOTAL = ("Net Income Including Noncontrolling Interests", "Net Income")


def _pick(row: dict | None, names: tuple[str, ...]):
    for nm in names:
        v = (row or {}).get(nm)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == f:                       # NaN 배제
            return f
    return None


def _by_period(rows: list | None) -> dict:
    return {str(r.get("period") or ""): r for r in (rows or [])
            if isinstance(r, dict)}


def _ratio_input(is_row: dict, bs_row: dict | None) -> dict:
    """비율·ROE 계산에 넘길 canonical dict.

    ⚠️ 분자·분모는 **같은 급**이어야 한다(#225 뉴파워프라즈마: 분자만 총액으로
    떨어지고 분모는 지배주주로 남아 ROE 가 부풀었다). 지배주주 귀속 순이익이
    명시된 키로 오면 그것과 `지배주주자본` 을 짝지어 넘기고, 없으면 둘 다
    비워 `apply_*_returns` 가 총액/총액으로 가게 둔다.
    """
    fin = {k: _pick(is_row, names) for k, names in _ITEM_CANDIDATES.items()}
    fin["당기순이익"] = _pick(is_row, _NET_TOTAL)
    own = _pick(is_row, _NET_OWNER)
    for k, names in _BS_CANDIDATES.items():
        fin[k] = _pick(bs_row, names)
    if own is not None and fin.get("지배주주자본") is not None:
        fin["지배주주순이익"] = own
    else:
        fin.pop("지배주주자본", None)      # 짝이 없으면 총액/총액으로
    return fin


def build(fins: dict | None, *, quarterly: bool, n: int = 5,
          lead: int = 3) -> list[dict]:
    """yfinance 재무제표 dict → `_kr_fin_trend_table` 이 읽는 **평평한 항목**들.

    `fins` = 스냅샷의 `financials`(income_statement / balance_sheet /
    cash_flow, 각각 {"annual": [...], "quarterly": [...]}).

    ⚠️ 세 표를 **기간으로 조인**한다 — 위치로 매기면 원천이 한쪽만 한 분기
    덜 줄 때 전 구간이 밀린다(#46·#88).

    ⚠️ `lead` 는 ROE(TTM)를 **첫 칸부터** 채우기 위한 앞선 분기 수다. 창을
    정확히 n 개만 태우면 맨 오른쪽 한 칸만 값이 있다(KR 표에서 겪은 것).
    """
    key = "quarterly" if quarterly else "annual"
    is_rows = ((fins or {}).get("income_statement") or {}).get(key) or []
    if not is_rows:
        return []
    bs = _by_period(((fins or {}).get("balance_sheet") or {}).get(key))
    cf = _by_period(((fins or {}).get("cash_flow") or {}).get(key))
    is_rows = sorted(is_rows, key=lambda r: str(r.get("period") or ""))
    want = n + (lead if quarterly else 1)
    is_rows = is_rows[-want:] if want else is_rows

    from bot.dart_client import calc_kr_financial_ratios
    from bot.fcf import fcf_from_row

    entries = []
    for r in is_rows:
        period = str(r.get("period") or "")
        fin = _ratio_input(r, bs.get(period))
        entries.append({"period": period, "financials": fin,
                        "ratios": calc_kr_financial_ratios(fin),
                        "_raw": r,
                        "_fcf": fcf_from_row(cf.get(period))})
    try:
        from bot.dart_client import apply_annual_returns, apply_ttm_returns
        (apply_ttm_returns if quarterly else apply_annual_returns)(entries)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("fin_trend: ROE 재계산 실패: %s", exc)

    out = []
    for e in entries[-n:]:
        period, fin, rat = e["period"], e["financials"], e.get("ratios") or {}
        try:
            year = int(period[:4])
        except (ValueError, TypeError):
            year = 0
        item = {
            "year": year,
            "label": (q_label_from_period(period) if quarterly
                      else f"FY{period[:4]}"),
            # 화면에 찍는 값은 다른 탭과 **같은 라인아이템**이다(#34) —
            # `당기순이익` 은 비율 입력(_NET_TOTAL)이 아니라 `Net Income`.
            "매출": fin.get("매출"),
            "영업이익": fin.get("영업이익"),
            "당기순이익": _pick(e.get("_raw"), ("Net Income",)),
            "FCF": e.get("_fcf"),
        }
        if quarterly:
            try:
                item["quarter"] = (int(period[5:7]) - 1) // 3 + 1
            except (ValueError, TypeError):
                item["quarter"] = 0
        for k in ("영업이익률", "ROE", "부채비율", "_returns_basis"):
            v = rat.get(k)
            if v is not None:
                item[k] = v
        out.append(item)
    return out
