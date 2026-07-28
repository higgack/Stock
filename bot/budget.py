"""가계부(현금흐름) 모델·저장 — 뱅크샐러드 export '2.현금흐름현황' (자산관리 P2).

자산 대시보드(portfolio)와 **별도 surface**. 같은 export 를 ingest 할 때 현금흐름
섹션을 모델링해 budget.json 저장 → `dashboard._render_budget_page` 가 budget.html
렌더. 사용자 요청 2026-06-04: "가계부는 또 다른 별도 대시보드".

현금흐름 매트릭스(parser `_parse_cashflow`): {months:[YYYY-MM…], rows:[{항목,총계,월평균,
monthly:[…]}]}. 여기서 수입/지출/총계 행을 분류해 월별 수입·지출·순저축·저축률을 산출.
뱅샐 export 의 정확한 행 라벨이 버전마다 달라 **키워드 분류 + 총계행 우선** 으로 견고
하게 처리하고, 분류가 빗나가도 원본 매트릭스를 그대로 표에 노출해 데이터 유실 0.

순수 함수(build_budget_model/_kind)는 단위테스트 가능. save/load 만 I/O.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

BUDGET_PATH = Path.home() / ".tradingagents" / "budget.json"

# 수입 측 키워드 — 뱅샐 현금흐름의 수입 카테고리(버전별 변형 흡수).
_INCOME_KW = ("수입", "급여", "월급", "상여", "보너스", "성과급", "이자", "배당",
              "연금수령", "사업소득", "임대", "환급", "캐시백", "용돈", "지원금",
              "수당", "소득")
_TOTAL_KW = ("총계", "합계", "총액", "계 ")


def _kind(item: str) -> str:
    """현금흐름 행 라벨 → 'income'|'expense'|'total_income'|'total_expense'|'total'."""
    s = str(item or "")
    is_total = any(k in s for k in _TOTAL_KW)
    is_income = ("수입" in s) or any(k in s for k in _INCOME_KW)
    is_expense = ("지출" in s) or ("소비" in s)
    if is_total:
        if is_income:
            return "total_income"
        if is_expense:
            return "total_expense"
        return "total"
    if is_income:
        return "income"
    return "expense"


def _m(row: dict, i: int, n: int) -> float:
    """row.monthly[i] 안전 추출 → 절대값 float (없으면 0)."""
    v = row.get("monthly") or []
    if i < len(v) and v[i] is not None:
        try:
            return abs(float(v[i]))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _cat_total(row: dict) -> float:
    """카테고리 기간 합 — 총계가 **0/빈값이 아닐 때만** 사용, 아니면 monthly 합.
    뱅샐은 finance 처럼 총계 셀을 비워두는(0) 경우가 많아 monthly 합이 canonical."""
    t = row.get("총계")
    try:
        if t is not None and abs(float(t)) > 0:
            return abs(float(t))
    except (TypeError, ValueError):
        pass
    return sum(abs(x) for x in (row.get("monthly") or []) if x is not None)


def build_budget_model(parsed: dict) -> dict:
    """parse_export 결과 → 가계부 모델.

    월별 수입/지출/순저축/저축률 + 수입·지출 카테고리 breakdown + 원본 매트릭스.
    총계행(월수입/월지출 총계)이 있으면 그것을, 없으면 카테고리 합을 쓴다."""
    cf = parsed.get("cashflow") or {}
    months: list[str] = list(cf.get("months") or [])
    raw = cf.get("rows") or []
    rows = [{**r, "kind": _kind(r.get("항목"))} for r in raw]
    n = len(months)

    # 매트릭스용 총계/월평균 — 뱅샐이 이 셀들을 비워두는(None) 경우가 많아
    # monthly 로 직접 산출(표에 '-' 대신 실제 값). 월평균은 데이터 있는 달 기준.
    for r in rows:
        mv = [x for x in (r.get("monthly") or []) if x is not None]
        if r.get("총계") in (None, 0) and mv:
            r["총계"] = round(sum(mv), 2)
        if r.get("월평균") in (None, 0) and mv:
            r["월평균"] = round(sum(mv) / len(mv), 2)

    inc_total = next((r for r in rows if r["kind"] == "total_income"), None)
    exp_total = next((r for r in rows if r["kind"] == "total_expense"), None)

    income = [0.0] * n
    expense = [0.0] * n
    for i in range(n):
        # 카테고리 monthly 합이 canonical(뱅샐 총계행은 비어있는 경우 많음).
        # 카테고리가 0이면 총계행 값으로 폴백.
        cat_inc = sum(_m(r, i, n) for r in rows if r["kind"] == "income")
        cat_exp = sum(_m(r, i, n) for r in rows if r["kind"] == "expense")
        income[i] = cat_inc if cat_inc else (_m(inc_total, i, n) if inc_total else 0.0)
        expense[i] = cat_exp if cat_exp else (_m(exp_total, i, n) if exp_total else 0.0)
    net = [round(income[i] - expense[i], 2) for i in range(n)]
    save_rate = [round(net[i] / income[i] * 100, 1) if income[i] else None
                 for i in range(n)]

    inc_cats = sorted(
        ({"항목": r["항목"], "amount": _cat_total(r)} for r in rows if r["kind"] == "income"),
        key=lambda x: -x["amount"])
    exp_cats = sorted(
        ({"항목": r["항목"], "amount": _cat_total(r)} for r in rows if r["kind"] == "expense"),
        key=lambda x: -x["amount"])

    # 기간 합계(전 월) + 월평균
    inc_sum = sum(income)
    exp_sum = sum(expense)
    latest = {}
    if n:
        latest = {
            "month": months[-1], "income": income[-1], "expense": expense[-1],
            "net": net[-1], "savings_rate": save_rate[-1],
        }
    return {
        "as_of": parsed.get("as_of"),
        "period": f"{months[0]} ~ {months[-1]}" if months else None,
        "months": months,
        "income": income, "expense": expense, "net": net, "savings_rate": save_rate,
        "income_cats": inc_cats, "expense_cats": exp_cats,
        "rows": rows,
        "totals": {
            "income": inc_sum, "expense": exp_sum, "net": inc_sum - exp_sum,
            "savings_rate": round((inc_sum - exp_sum) / inc_sum * 100, 1) if inc_sum else None,
            "income_avg": inc_sum / n if n else None,
            "expense_avg": exp_sum / n if n else None,
        },
        "latest": latest,
    }


def save_budget(model: dict) -> None:
    """atomic write to budget.json (+ 직전 1개 .bak 백업, portfolio.save 패리티).

    2026-06-26 사고에서 budget 은 .bak 이 없어 portfolio 와 달리 자동복구 불가였음
    → 덮어쓰기 전 기존 파일을 budget.json.bak 으로 복사(백업 실패는 비치명적)."""
    BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if BUDGET_PATH.exists():
        try:
            bak = BUDGET_PATH.with_suffix(".json.bak")
            bak.write_text(BUDGET_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    payload = {**model, "_saved_ts": time.time()}
    tmp = BUDGET_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, BUDGET_PATH)


def load_budget() -> dict | None:
    try:
        return json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
