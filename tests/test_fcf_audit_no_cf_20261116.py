"""FCF 감사 ① — 시세는 왔는데 원천이 현금흐름표를 안 준 종목을 ❌ 로 세지 않는다.

2026-11-16 결산 ❌ 1건이 0162D0.KQ(동원파츠, 신규 상장)였다: 첫 정기보고서 전이라
원천에 표가 없다. 고칠 것이 없는 ❌ 는 진짜 ❌ 를 가린다(#260). 반대로 스냅샷
자체가 비면(수집 실패) ❌ 그대로다(#54·#132). 감사 함수를 통째로 태워 잰다(#20).
"""
import pytest


def _run(monkeypatch, snap):
    import bot.scripts.fcf_audit as fa
    import bot.stock_snapshot as ss
    monkeypatch.setattr(ss, "collect_stock_snapshot", lambda *a, **k: snap)
    return fa.audit_one("AAPL", None, years=1)


def test_snapshot_failure_stays_bad(monkeypatch):
    r = _run(monkeypatch, {})
    assert "①재계산(yf)" in r["bad_axes"], r["lines"]
    assert any("❌ 대조 0건" in l for l in r["lines"])


def test_new_listing_without_statements_is_unknown_with_facts(monkeypatch):
    r = _run(monkeypatch, {"current_price": 12345.0,
                           "first_trade_date": "2026-11-05"})
    assert "①재계산(yf)" not in r["bad_axes"], r["lines"]
    assert "①재계산(yf)" in r["unknown_axes"], r["lines"]
    line = next(l for l in r["lines"] if "① 재계산" in l)
    assert "❓" in line and "12,345.0" in line and "상장 2026-11-05" in line, line
    assert "재무제표를 아직 주지 않음" in line, line


def test_cash_flow_only_missing_names_that_branch(monkeypatch):
    snap = {"current_price": 10, "financials": {
        "income_statement": {"annual": [{"period": "2025-12-31"}]}}}
    r = _run(monkeypatch, snap)
    line = next(l for l in r["lines"] if "① 재계산" in l)
    assert "❓" in line and "현금흐름표만 원천에 없음" in line, line
    assert "①재계산(yf)" not in r["bad_axes"]
