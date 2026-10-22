"""리포트 수치 대조 가드 (2026-10-22) — 라벨이 붙은 값만, 현재 값만 잰다.

외부 레포 검토(FinRobot `report_drift.py`)에서 가져온 아이디어를 우리 규칙
으로 다시 짰다: 분석가가 FACTUAL ANCHOR 값을 옮기다 틀리면 섹션 끝에 말하고,
원문은 고치지 않는다. 오탐(늘 뜨는 경고, #25·#260)을 막는 건너뛰기 규칙이
이 가드의 절반이라 그 갈래를 하나씩 못박는다.
"""

from __future__ import annotations

import pytest

from bot import report_numeric_guard as g

KR = {
    "currency": "KRW", "current_price": 187000.0, "market_cap": 5.83e12,
    "trailing_pe": 28.86, "forward_pe": 15.3, "price_to_book": 1.21,
    "trailing_eps": 6479.0, "high_52w": 250000.0, "low_52w": 120000.0,
}
US = {
    "currency": "USD", "current_price": 301.25, "market_cap": 36.94e9,
    "trailing_pe": 42.0, "forward_pe": 30.0, "price_to_book": 8.0,
    "trailing_eps": 7.17, "high_52w": 320.0, "low_52w": 150.0,
}


def _metrics(body, can):
    return [(f.metric, ok) for f, ok in g.scan(body, can)]


def test_faithful_copies_pass_with_rounding():
    body = ("현재가 ₩187,000 기준 PER 약 29배, PBR 1.2배, EPS ₩6,479.\n"
            "시가총액 약 5.83조 원 · 52주 최고가 ₩250,000 · 52주 최저 ₩120,000\n"
            "현재 주가 18.7만원")
    got = _metrics(body, KR)
    assert {m for m, _ in got} == {"price", "per", "pbr", "eps", "market_cap",
                                   "high_52w", "low_52w"}
    assert all(ok for _, ok in got)
    assert g.annotate(body, KR, "₩") == body          # 어긋남 없으면 그대로


def test_unit_error_and_stale_values_are_flagged():
    body = "시가총액 약 58.3조 원으로 대형주입니다.\n현재가 ₩210,000 에서 출발."
    bad = {f.metric: f for f in g.find_mismatches(body, KR)}
    assert set(bad) == {"market_cap", "price"}
    assert bad["market_cap"].quoted == pytest.approx(58.3e12)
    assert round(bad["market_cap"].gap_pct) == 900


def test_us_units_billion_trillion():
    assert sorted(_metrics("Market cap $36.9B, current price $301.25", US)) == [
        ("market_cap", True), ("price", True)]
    assert g.find_mismatches("시가총액 $369.4B", US)[0].metric == "market_cap"


def test_forward_and_trailing_per_are_compared_to_their_own_reference():
    f = g.find_mismatches("Forward PER 22.1x, 후행 PER 28.9배", KR)
    assert [x.metric for x in f] == ["fwd_per"]
    assert f[0].reference == 15.3
    assert _metrics("선행 PER 15.5배", KR) == [("fwd_per", True)]


@pytest.mark.parametrize("line", [
    "업종 평균 PER 18배 수준",                 # 평균 — 남의 값
    "2025년 EPS ₩5,000 에서 성장",            # 연도 — 과거 값
    "목표가 ₩240,000 (현재가 대비 +28%)",     # 목표·퍼센트
    "현재가치(DCF) 12,000원",                 # 현재가치 ≠ 현재가
    "PER/PBR 9.9/0.5",                       # 묶인 라벨 — 누구 값인지 모름
    "PER 10~12배 구간에서 지지",               # 범위
    "EPS 성장률 15%",                         # 성장률
    "PER 밴드 하단 10배",                      # 밴드
    "SK하이닉스 대비 PER 8.5배로 할인",          # 비교 문장
    "경쟁사 PER 8.5배",                        # 동종사
    # 비교 낱말이 라벨에서 멀어도 그 줄은 여러 회사를 나란히 놓은 문장이다.
    "동종사 SK하이닉스와 삼성전자의 밸류는 각각 PER 8.5배",
    "시가총액 1위",                            # 단위 없는 작은 수
])
def test_non_current_contexts_are_not_judged(line):
    assert g.scan(line, KR) == []


def test_comparison_heading_skips_until_next_heading():
    body = ("### 동종업계 비교\nSK하이닉스 PER 8.5배\n**밸류에이션 밴드**\n"
            "PER 50배\n## 결론\nPER 50배")
    got = g.find_mismatches(body, KR)
    assert len(got) == 1 and got[0].quote_text == "PER 50배"


def test_other_currency_and_minor_units_are_not_judged():
    assert g.scan("시가총액 약 $4.2B (원화 환산 전)", KR) == []
    gbp = dict(US, currency="GBp", current_price=1234.0)
    # 펜스·파운드 표기가 갈리므로 금액은 판정하지 않고 배수만 잰다.
    assert _metrics("현재가 £12.34, PER 42배", gbp) == [("per", True)]


def test_yen_sign_follows_reference_currency():
    cny = dict(US, currency="CNY", current_price=1500.0)
    assert _metrics("현재가 ¥1,500", cny) == [("price", True)]
    assert g.scan("현재가 ¥1,500", US) == []          # 달러 종목의 ¥ 는 다른 통화


def test_eps_sign_flip_is_a_mismatch():
    f = g.find_mismatches("EPS -1,234원 적자", KR)
    assert [x.metric for x in f] == ["eps"]


def test_guard_does_not_count_its_own_note():
    body = "PER 50배로 고평가"
    once = g.annotate(body, KR, "₩")
    assert once.count(g.MARKER) == 1
    # 제 출력의 인용(기준값·원문 발췌)을 다시 결함으로 세지 않는다(#250).
    assert len(g.find_mismatches(once, KR)) == len(g.find_mismatches(body, KR)) == 1
    # 옛 시총 경고 줄(아카이브)도 본문으로 세지 않는다.
    legacy = "⚠️ [시가총액 불일치: 위 값이 시스템 canonical (약 5.83조 원) 대비 10.0배 차이]"
    assert g.scan(legacy, KR) == []


def test_note_lists_items_with_quote_and_caps_length():
    body = "\n".join(f"PER {50 + i}배" for i in range(9))
    note = g.render_note(g.find_mismatches(body, KR), "₩", limit=6)
    assert note.startswith(g.MARKER)
    assert note.count("↔ 기준 28.86배") == 6
    assert "외 3건" in note
    assert "「PER 50배」" in note


def test_missing_reference_is_not_a_pass_or_fail():
    assert g.scan("PER 50배", {"currency": "KRW"}) == []
    assert g.scan("PER 50배", None) == []


# ── 배선: _format_full 이 모든 섹션 + 최종 결정에 건다 ──────────────────────

# _clean_section 은 200자 미만 섹션을 '모델 응답 오류' 자리표시로 바꾼다 —
# 픽스처는 실제 섹션처럼 충분한 본문을 단다.
_FILL = "\n\n" + "반도체 업황 회복과 수요 개선으로 실적 모멘텀이 이어지는 구간입니다. " * 5


def _state():
    return {
        "market_report": "현재가 ₩187,000" + _FILL,
        "sentiment_report": "감정 중립" + _FILL,
        "news_report": "뉴스 없음" + _FILL,
        "fundamentals_report": "시가총액 약 58.3조 원, PER 28.9배" + _FILL,
        "investment_plan": "계획" + _FILL,
        "trader_investment_plan": "Entry Price: 187,000" + _FILL,
    }


def test_format_full_annotates_sections_and_decision(monkeypatch):
    import bot.analyzer as a
    monkeypatch.setattr(a, "_extract_canonical", lambda t: dict(KR))
    full = a._format_full(_state(), "최종: Hold. 현재가 ₩100,000", "005930.KS",
                          "2026-10-22")
    fund = full.split("## 💰 펀더멘털")[1].split("\n## ")[0]
    assert g.MARKER in fund and "시가총액" in fund.split(g.MARKER)[1]
    final = full.split("## ✅ 최종 결정")[1]
    assert g.MARKER in final and "현재가" in final.split(g.MARKER)[1]
    market = full.split("## 📈 시장 분석")[1].split("\n## ")[0]
    assert g.MARKER not in market                     # 맞게 옮긴 섹션은 조용하다
    # 옛 시총 교차검사는 없어졌다 — 같은 값에 경고가 두 번 붙지 않는다(#38).
    assert "시가총액 불일치" not in full


def test_guard_failure_leaves_report_intact(monkeypatch):
    import bot.analyzer as a
    import bot.report_numeric_guard as mod
    monkeypatch.setattr(a, "_extract_canonical", lambda t: dict(KR))

    def boom(*a_, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(mod, "annotate", boom)
    full = a._format_full(_state(), "Hold", "005930.KS", "2026-10-22")
    assert "시가총액 약 58.3조 원, PER 28.9배" in full and g.MARKER not in full


def test_extract_canonical_hides_what_the_anchor_hides(monkeypatch):
    import bot.analyzer as a
    import tradingagents.agents.utils.agent_utils as au
    monkeypatch.setattr(au, "_instrument_info", lambda t: {
        "currentPrice": 100.0, "marketCap": 1e9, "trailingPE": 12000.0,
        "forwardPE": 15.0, "priceToBook": -2.0, "trailingEps": -1.5,
        "fiftyTwoWeekHigh": 120.0, "fiftyTwoWeekLow": 0.0})
    c = a._extract_canonical("AAPL")
    assert c["forward_pe"] == 15.0 and c["trailing_eps"] == -1.5
    assert c["high_52w"] == 120.0
    for k in ("trailing_pe", "price_to_book", "low_52w"):
        assert k not in c                         # 앵커가 N/A 로 숨긴 값


def test_trader_warning_uses_markdown_bold_not_html():
    import bot.analyzer as a
    out = a._flag_trader_price_hallucination(
        "Entry Price: 20,000", canonical={"current_price": 187000.0})
    assert "**가격 환각 의심**" in out and "<b>" not in out


# ── 감사 CLI ───────────────────────────────────────────────────────────────

def _rec(report, **si):
    base = {"current_price": 187000.0, "market_cap": 5.83e12, "trailingPE": 28.86}
    base.update(si)
    return {"ticker": "005930.KS", "trade_date": "2026-10-21",
            "full_report": report, "stock_info": base}


def test_audit_counts_judged_and_missed_per_metric():
    from bot.scripts.report_guard_audit import audit, report_lines
    recs = [
        _rec("## 💰 펀더멘털\n시가총액 약 58.3조 원, PER 28.9배"),
        _rec("## 📈 시장 분석\n현재가 ₩187,000"),
        {"ticker": "X", "full_report": "PER 1배"},          # 기준 없음
    ]
    res = audit(recs)
    assert (res["records"], res["with_ref"], res["flagged_records"]) == (3, 2, 1)
    assert res["judged"]["market_cap"] == 1 and res["missed"]["market_cap"] == 1
    assert res["judged"]["per"] == 1 and res["missed"]["per"] == 0
    assert res["sec_missed"]["💰 펀더멘털"] == 1
    text = "\n".join(report_lines(res))
    assert "경고가 붙었을 리포트: 1/2" in text and "58.3조" in text


def test_audit_with_nothing_to_compare_says_undeterminable():
    from bot.scripts.report_guard_audit import audit, report_lines
    text = "\n".join(report_lines(audit([{"ticker": "X", "full_report": "x"}])))
    assert "❓ 판정 불가" in text and "✅" not in text


def test_audit_reference_uses_market_currency_and_anchor_filters():
    from bot.scripts.report_guard_audit import canonical_from_record
    c = canonical_from_record(_rec("", trailingPE=20000.0, priceToBook=0.0))
    assert c["currency"] == "KRW"
    assert "trailing_pe" not in c and "price_to_book" not in c
