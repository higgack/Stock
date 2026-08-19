"""DART 가 총액을 안 주는 회사의 **매출 총액을 FnGuide 로 채운다** — 검산 필수.

⚠️ 배경(사용자 2026-08-19). 증권·은행·보험사는 DART 표준 손익 API 에
영업수익(총액) 계정이 없어 '매출' 자리에 이자수익이 올라갔다(VM 스윕: 27종목
중 15종목). 네이버/FnGuide Financial Summary 에는 총액이 있다.

⚠️ **그 값을 그냥 덮지 않는다.** 다른 출처의 숫자를 우리 표에 넣는 순간
"어느 값이 어디서 왔는지" 를 잃기 쉽다. 그래서 세 가지를 검산하고, 하나라도
어긋나면 **아무것도 바꾸지 않는다**(옛 동작 = 구성요소 + 비율 비움 유지):

  1) 기간 키 정확 일치 — 결산월이 12월이 아닌 회사에 엉뚱한 분기를 붙이지 않는다.
  2) 영업이익 교차 확인 — 같은 기간 영업이익이 DART 와 ±2% 안에서 일치해야
     한다. 다른 회사·다른 회계기준(연결↔별도) 표를 붙였다면 여기서 걸린다.
  3) 총액 > 구성요소 — 부분이 전체보다 크면 파싱이 틀린 것이다.

성공하면 매출을 총액으로 바꾸고, 매출로 나누는 비율을 **다시 계산**하며,
`_revenue_source` 로 출처를 남긴다(화면이 그걸 표기한다).
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("bot.kr_revenue_fallback")

_OP_TOL = 0.02          # 영업이익 교차 확인 허용 오차(2%)


def _period_key(year: Optional[int], quarter: Optional[int]) -> Optional[str]:
    """(2026, 1) → '2026/03' · (2025, None) → '2025/12'."""
    if not year:
        return None
    if quarter is None:
        return f"{int(year)}/12"
    q = int(quarter)
    return f"{int(year)}/{q * 3:02d}" if 1 <= q <= 4 else None


def fill_total_revenue(stock_code: str, entry: dict, *, year=None,
                       quarter=None, summary: Optional[dict] = None) -> bool:
    """`entry`(매출/영업이익/… dict)의 구성요소 매출을 총액으로 교체. 성공 시 True.

    `summary` 를 주면 그것을 쓰고(여러 기간을 채울 때 1회 fetch 재사용),
    없으면 직접 받는다. 실패·검산 탈락 시 entry 를 **건드리지 않는다**."""
    comp = (entry.get("_component_accounts") or {}).get("매출")
    if not comp:
        return False                     # 총액이 이미 있는 회사 — 손대지 않는다
    key = _period_key(year, quarter)
    if not key:
        return False
    if summary is None:
        from bot.wisereport_financials import fetch_financial_summary
        summary = fetch_financial_summary(stock_code)
    if not summary:
        return False
    bucket = summary.get("quarter" if quarter else "annual") or {}
    row = bucket.get(key)
    if not row:
        return False                     # (1) 기간 불일치 — 추측하지 않는다
    total = row.get("매출액")
    if total is None or total <= 0:
        return False

    op_dart, op_fg = entry.get("영업이익"), row.get("영업이익")
    if op_dart is None or op_fg is None:
        return False
    if abs(op_dart) < 1 or abs(op_fg - op_dart) / abs(op_dart) > _OP_TOL:
        log.info("kr_revenue_fallback: %s %s 영업이익 불일치(DART %.0f vs "
                 "FnGuide %.0f) — 교체 안 함", stock_code, key, op_dart, op_fg)
        return False                     # (2) 다른 표를 붙였다

    part = entry.get("매출")
    if part is not None and total <= part:
        log.info("kr_revenue_fallback: %s %s 총액(%.0f)이 구성요소(%.0f) 이하 "
                 "— 교체 안 함", stock_code, key, total, part)
        return False                     # (3) 부분 > 전체는 파싱 오류

    entry["매출"] = total
    entry["_revenue_source"] = "FnGuide"
    entry["_revenue_component_was"] = comp
    _c = dict(entry.get("_component_accounts") or {})
    _c.pop("매출", None)
    if _c:
        entry["_component_accounts"] = _c
    else:
        entry.pop("_component_accounts", None)
    # 매출로 나누는 비율을 **다시** 계산한다(옛 값은 비어 있거나 틀렸다).
    for k, num in (("영업이익률", entry.get("영업이익")),
                   ("순이익률", entry.get("당기순이익"))):
        if num is None:
            entry.pop(k, None)
        else:
            entry[k] = num / total * 100
    return True
