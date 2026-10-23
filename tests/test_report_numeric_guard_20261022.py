"""리포트 수치 대조 가드 (2026-10-22) — 라벨이 붙은 값만, 현재 값만 잰다.

외부 레포 검토(FinRobot `report_drift.py`)에서 가져온 아이디어를 우리 규칙
으로 다시 짰다: 분석가가 FACTUAL ANCHOR 값을 옮기다 틀리면 섹션 끝에 말하고,
원문은 고치지 않는다. 오탐(늘 뜨는 경고, #25·#260)을 막는 건너뛰기 규칙이
이 가드의 절반이라 그 갈래를 하나씩 못박는다 — 배포 전 독립 리뷰가 첫 판에서
'현재가는 52주 최고가 대비' 를 현재가 52원으로 읽는 등 흔한 문장 수십 개의
오탐을 실측했고, 아래 parametrize 가 그 목록이다.
"""

from __future__ import annotations

import pytest

from bot import report_numeric_guard as g

KR = {
    "currency": "KRW", "current_price": 98500.0, "market_cap": 1.9e12,
    "trailing_pe": 28.86, "price_to_book": 1.21,
    "trailing_eps": 6479.0, "high_52w": 120000.0, "low_52w": 70000.0,
}
US = {
    "currency": "USD", "current_price": 187.4, "market_cap": 36.94e9,
    "trailing_pe": 42.0, "price_to_book": 8.0,
    "trailing_eps": 7.17, "high_52w": 210.0, "low_52w": 150.0,
}


def _metrics(body, can):
    return sorted((f.metric, ok) for f, ok in g.scan(body, can))


def test_faithful_copies_pass_with_rounding():
    body = ("현재가 ₩98,500 기준 PER 약 29배, PBR 1.2배.\n"
            "후행 EPS ₩6,479 · 시가총액 약 1.9조 원\n"
            "52주 최고가 ₩120,000 · 52주 최저 ₩70,000\n"
            "현재 주가 9.85만원")
    got = _metrics(body, KR)
    assert {m for m, _ in got} == {"price", "per", "pbr", "eps", "market_cap",
                                   "high_52w", "low_52w"}
    assert all(ok for _, ok in got)
    assert g.annotate(body, KR, "₩") == body          # 어긋남 없으면 그대로


def test_unit_error_and_stale_values_are_flagged():
    body = "시가총액 약 19조 원으로 대형주입니다.\n현재가 ₩120,000 입니다."
    bad = {f.metric: f for f in g.find_mismatches(body, KR)}
    assert set(bad) == {"market_cap", "price"}
    assert bad["market_cap"].quoted == pytest.approx(19e12)
    assert round(bad["market_cap"].gap_pct) == 900


def test_market_cap_ratio_is_symmetric():
    # 작게 쓴 쪽도 같은 배율로 잰다 — 1.9조 vs 1.4조(1.36배)는 어긋남.
    assert g.find_mismatches("시가총액 1.4조원", KR)[0].metric == "market_cap"
    assert g.find_mismatches("시가총액 2.4조원", KR) == []   # 1.26배 — 허용


def test_us_units_including_case_variants():
    assert _metrics("Market cap $36.9B, current price $187.40", US) == [
        ("market_cap", True), ("price", True)]
    assert _metrics("시가총액 $36.9Bn", US) == [("market_cap", True)]
    assert _metrics("Market cap 36.9 Billion", US) == [("market_cap", True)]
    assert g.find_mismatches("시가총액 $369.4B", US)[0].metric == "market_cap"


def test_korean_compound_amounts_are_summed():
    assert _metrics("시가총액 1조 9,000억원", KR) == [("market_cap", True)]
    assert _metrics("현재가 9만 8,500원", KR) == [("price", True)]
    assert _metrics("시가총액 1,900,000백만원", KR) == [("market_cap", True)]


@pytest.mark.parametrize("line", [
    # 숫자가 다음 낱말 것이다(기간·이평선·순위·분기)
    "현재가는 52주 최고가 대비 18% 낮은 수준",
    "현재 주가는 52주 신저가 근처",
    "The current price is near the 52-week low",
    "현재가는 200일 이동평균선 위",
    "현재가가 60일 이평선을 하회",
    "현재 주가 5일 연속 상승",
    "PER 10년 평균",
    "PER 5년 밴드 하단",
    "PER 3.5년 만에 최저",
    "PER은 2026E 기준 15.2배",
    "EPS for 2026E: $8.10",
    "EPS 1Q26 1,820원",
    # 틈에 다른 라벨·값 없음 표시·나열
    "PBR·PER 1.2배·28.9배",
    "현재가와 EPS 6,470원",
    "현재가·52주 최고 98,500원·120,000원",
    # 숫자 뒤가 조건·임계·분기 실적·가이던스·추정
    "PER 12배 적용 시 목표가 15만원",
    "PBR 0.9배까지 하락 시 매수",
    "PER 10배 이하로 하락하면",
    "PER 15배 수준의 저평가",
    "시가총액 3조원 이상 기업",
    "EPS 1,820원(1분기)",
    "EPS $1.62 in Q3",
    "EPS of $1.62 last quarter",
    "EPS came in at $1.62, beating estimates",
    "EPS beat by $0.12",
    "EPS 서프라이즈 $0.12",
    "EPS guidance of $8.50",
    "PER 15.2배(2026E)",
    # 평균·목표·연도·비교·현재가치·범위·퍼센트·단위 없는 작은 수
    "업종 평균 PER 18배 수준",
    "2025년 EPS ₩5,000 에서 성장",
    "목표가 ₩240,000 (현재가 대비 +28%)",
    "현재가치(DCF) 12,000원",
    "PER/PBR 9.9/0.5",
    "PER 10~12배 구간에서 지지",
    "EPS 성장률 15%",
    "PER 20% 할인",
    "SK하이닉스 대비 PER 8.5배로 할인",
    "경쟁사 PER 8.5배",
    "동종사 SK하이닉스와 삼성전자의 밸류는 각각 PER 8.5배",
    "시가총액 1위",
    "Forward PER 22.1x",
    "현재가 3조원",                       # 가격에 큰 단위 — 다른 값을 가리킨다
    "현재가 -98,500원",                   # 음수 가격
    "EPS ₩5,000 수준",                    # 후행 명시 없는 EPS
    "PER: N/A, 순이익 12",                 # 값 없음 표시 뒤의 숫자는 남의 것
    "현재가 EPS 6,470원",                  # 틈에 다른 라벨
    "PER 업종평균 18배",                   # 틈에 평균
    "PBR 1.0x~1.2x 구간",                 # 단위 붙은 범위
    "시총 36.9",                          # 단위 없는 작은 수
    "PER 28.9%",                          # 배수 칸에 퍼센트
    "PER 12조",                           # 배수 칸에 금액 단위
])
def test_non_current_contexts_are_not_judged(line):
    assert g.scan(line, KR) == [] and g.scan(line, US) == []


def test_value_after_a_missing_value_belongs_to_the_next_label():
    # 'PER: — / PBR: 1.2' 의 1.2 는 PBR 값이다 — PER 로 읽지 않는다(첫 판의 오탐).
    assert _metrics("PER: — / PBR: 1.2", KR) == [("pbr", True)]


@pytest.mark.parametrize("line", [
    # 델타 리뷰 2026-10-22 — 가정·과거·변화·임계 표현(허용 목록 밖)
    "PER 20배를 부여해", "PER 20배 기준 적정가", "PER 20배로 환산 시", "PER 60배라면",
    "PER 60배라도", "PER 15배만 받아도", "PER 12.5배였던 시점", "PER는 60배였다",
    "PER 60배에 달했던", "PER 20배에서 거래되던", "PER 15배로 하락", "PER 15배로 낮아지면",
    "PER 15배로 수렴", "PER 15배 하향", "PER 15배 정상화", "PER 15배 내외", "PER 10배 밑으로",
    "PBR 0.5배 아래", "PER 60배 버블 구간", "PER 15배에서 28.9배로 확대", "PER 10 → 28.9배",
    "시가총액 100조 클럽", "시가총액 2조원 증발", "시가총액이 하루 만에 1조원 늘었다",
    "시총 3천억원 규모의 자사주 매입", "현재가 9만원대 초반",
    "P/E above 40x historically", "P/E under 20x", "market cap of $1 trillion milestone",
    "market cap gain of $200B",
    # 퍼센트·날짜·월·여러 열 표·Trader 경고 줄
    "시가총액의 30%에 달하는 현금", "market cap 30% below peak",
    "현재가(10/21 종가) 98,500원", "현재가 2026.10.21 기준 98,500원", "52주 최고가는 3월의 150,000원",
    "| PER | 12.0배 | 18.5배 | 28.9배 |", "| EPS (TTM) | 1,800원 | 3,400원 | 4,500원 |",
    "| 시가총액 | 150조원 | 5.83조원 |",
    "⚠️ **가격 환각 의심** (학습 cutoff): Entry Price 20,000. 현재가 3 기준",
])
def test_delta_review_false_positives(line):
    assert g.scan(line, KR) == [] and g.scan(line, US) == []


def test_current_value_phrasings_still_judged():
    # 허용 목록이 정상 서술은 받는다 — 수준·규모·으로+서술·표의 단일 값 칸.
    assert _metrics("시가총액 58.3조원 수준", KR) == [("market_cap", False)]
    assert _metrics("시가총액은 약 58.3조원 수준이다", KR) == [("market_cap", False)]
    assert _metrics("PER 28.9배로 업종 평균 대비 할인", KR) == [("per", True)]
    assert _metrics("| PER | 28.9배 |", KR) == [("per", True)]
    assert _metrics("| 현재가 | ₩98,500 | 52주 최고 ₩120,000 |", KR) == [
        ("high_52w", True), ("price", True)]
    assert _metrics("현재가 98천원.", KR) == [("price", True)]
    assert _metrics("현재가 9만8천원.", KR) == [("price", True)]
    assert _metrics("시가총액 1조 9천억원.", KR) == [("market_cap", True)]


def test_value_guards_that_only_they_catch():
    # 원화 표기가 조사와 붙어도(‘원이며’) 그 통화다 — 달러 종목에서 판정하지 않는다.
    assert g.scan("현재가는 약 25만원이며", US) == []
    # 시총에 퍼센트가 붙으면 금액이 아니다.
    assert g.scan("시가총액 30%.", KR) == []
    # 표현할 수 없는 큰 수(inf)는 판정하지 않는다.
    assert g.scan("현재가 " + "1" * 400 + ".", KR) == []


def test_annotate_is_idempotent():
    once = g.annotate("PER 50배.", KR, "₩")
    assert g.annotate(once, KR, "₩") == once


def test_trailing_per_judged_even_with_forward_note_after():
    # 뒤에 붙은 선행 표기는 다음 항목이다 — 후행 28.9 는 후행 기준과 잰다(첫 판은
    # 꼬리의 '선행' 을 보고 이 값을 선행 PER 로 잘못 분류했다).
    assert _metrics("PER 28.9배, Fwd 15.2배", KR) == [("per", True)]
    assert _metrics("PER 28.9배(선행 15.2배)", KR) == []   # 괄호 꼬리는 판정 보류


def test_currency_from_particle_and_other_currency():
    # 달러 종목의 '25만원으로' 는 원화 값이다 — 판정하지 않는다.
    assert g.scan("현재가는 약 25만원으로 환산", US) == []
    assert g.scan("시가총액 약 $4.2B (원화 환산 전)", KR) == []
    gbp = dict(US, currency="GBp", current_price=1234.0)
    assert _metrics("현재가 £12.34, PER 42배", gbp) == [("per", True)]


def test_yen_sign_follows_reference_currency():
    cny = dict(US, currency="CNY", current_price=1500.0)
    assert _metrics("현재가 ¥1,500", cny) == [("price", True)]
    assert g.scan("현재가 ¥1,500", US) == []


def test_post_window_stops_at_next_label():
    # 뒤 창은 다음 라벨에서 끊는다 — PBR 의 '(2026E…)' 가 PER 을 지우지 않는다.
    assert _metrics("PER 28.9배 PBR 1.2배 (2026E 추정)", KR) == [("per", True)]


def test_post_window_stops_at_next_item():
    # '목표가' 는 다음 항목 것 — 현재가는 잰다.
    assert _metrics("현재가 98,500원, 목표가 240,000원", KR) == [("price", True)]


def test_eps_needs_explicit_trailing_qualifier():
    assert _metrics("TTM EPS ₩6,479", KR) == [("eps", True)]
    f = g.find_mismatches("후행 EPS -1,234원(적자)", KR)
    assert [x.metric for x in f] == ["eps"]


def test_comparison_heading_skips_until_next_heading():
    body = "### 동종업계 비교\nSK하이닉스 PER 8.5배\n## 결론\nPER 50배"
    got = g.find_mismatches(body, KR)
    assert [x.quote_text for x in got] == ["PER 50배"]


def test_bold_line_is_a_heading_too():
    body = "### 결론\n**동종업계 비교**\nSK하이닉스 PER 8.5배\n**정리**\nPER 50배"
    got = g.find_mismatches(body, KR)
    assert [x.quote_text for x in got] == ["PER 50배"]


def test_guard_does_not_count_its_own_note():
    body = "PER 50배로 고평가"
    once = g.annotate(body, KR, "₩")
    assert once.count(g.MARKER) == 1
    assert len(g.find_mismatches(once, KR)) == len(g.find_mismatches(body, KR)) == 1


def test_note_lists_items_with_quote_and_caps_length():
    body = "\n".join(f"PER {50 + i}배" for i in range(9))
    note = g.render_note(g.find_mismatches(body, KR), "₩", limit=6)
    assert note.startswith(g.MARKER)
    assert note.count("↔ 기준 28.86배") == 6
    assert "외 3건" in note
    assert "「PER 50배」" in note


def test_excerpt_marks_trims_and_drops_bold():
    line = "가" * 80 + " **PER** 50배. " + "나" * 80
    f = g.find_mismatches(line, KR)[0]
    assert f.quote_text.startswith("…") and f.quote_text.endswith("…")
    assert "**" not in f.quote_text


def test_missing_reference_is_not_a_pass_or_fail():
    assert g.scan("PER 50배", {"currency": "KRW"}) == []
    assert g.scan("PER 50배", None) == []


def test_long_line_is_linear():
    import time
    line = "PER 28.9배 " * 10000
    t0 = time.time()
    assert len(g.scan(line, KR)) == 10000
    assert time.time() - t0 < 3.0


# ── 기준값: 앵커가 보여 준 값만 ──────────────────────────────────────────

def test_canonical_hides_what_the_anchor_hides():
    c = g.canonical_from_info({
        "currentPrice": 100.0, "marketCap": 1e9, "trailingPE": 12000.0,
        "forwardPE": 15.0, "priceToBook": -2.0, "trailingEps": -1.5,
        "fiftyTwoWeekHigh": 120.0, "fiftyTwoWeekLow": 101.0})
    assert c["trailing_eps"] == -1.5 and c["high_52w"] == 120.0
    # PER ≥10000 · 음수 PBR · 선행 PER(앵커에 없음) · 최저>현재가(앵커 ⚠️데이터오류)
    for k in ("trailing_pe", "price_to_book", "forward_pe", "low_52w"):
        assert k not in c


def test_canonical_drops_price_when_anchor_declares_glitch():
    # 현재가가 52주 최저 아래 → 앵커는 보정 현재가를 쓰라고 한다.
    c = g.canonical_from_info({"currentPrice": 50.0, "fiftyTwoWeekLow": 100.0,
                               "fiftyTwoWeekHigh": 200.0, "marketCap": 1e9})
    assert "current_price" not in c and "high_52w" not in c and c["market_cap"] == 1e9


# ── 배선: _format_full 이 모든 섹션 + 최종 결정에 건다 ──────────────────────

# _clean_section 은 200자 미만 섹션을 '모델 응답 오류' 자리표시로 바꾼다 —
# 픽스처는 실제 섹션처럼 충분한 본문을 단다.
_FILL = "\n\n" + "반도체 업황 회복과 수요 개선으로 실적 모멘텀이 이어지는 구간입니다. " * 5


def _state():
    return {
        "market_report": "현재가 ₩98,500" + _FILL,
        "sentiment_report": "감정 중립" + _FILL,
        "news_report": "뉴스 없음" + _FILL,
        "fundamentals_report": "시가총액 약 19조 원, PER 28.9배" + _FILL,
        "investment_plan": "계획" + _FILL,
        "trader_investment_plan": "Entry Price: 98,500" + _FILL,
    }


def test_format_full_annotates_sections_and_decision(monkeypatch):
    import bot.analyzer as a
    monkeypatch.setattr(a, "_extract_canonical", lambda t: dict(KR))
    full = a._format_full(_state(), "최종: Hold. 현재가 ₩150,000", "005930.KS",
                          "2026-10-22")
    fund = full.split("## 💰 펀더멘털")[1].split("\n## ")[0]
    assert g.MARKER in fund and "시가총액" in fund.split(g.MARKER)[1]
    final = full.split("## ✅ 최종 결정")[1]
    assert g.MARKER in final and "현재가" in final.split(g.MARKER)[1]
    market = full.split("## 📈 시장 분석")[1].split("\n## ")[0]
    assert g.MARKER not in market                     # 맞게 옮긴 섹션은 조용하다
    assert "시가총액 불일치" not in full               # 옛 교차검사 없음(#38)


def test_guard_failure_leaves_report_intact(monkeypatch):
    import bot.analyzer as a
    import bot.report_numeric_guard as mod
    monkeypatch.setattr(a, "_extract_canonical", lambda t: dict(KR))

    def boom(*a_, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(mod, "annotate", boom)
    full = a._format_full(_state(), "Hold", "005930.KS", "2026-10-22")
    assert "시가총액 약 19조 원, PER 28.9배" in full and g.MARKER not in full


def test_extract_canonical_uses_the_shared_rule(monkeypatch):
    import bot.analyzer as a
    import tradingagents.agents.utils.agent_utils as au
    info = {"currentPrice": 100.0, "marketCap": 1e9, "trailingPE": 12000.0,
            "priceToBook": 2.0, "fiftyTwoWeekHigh": 120.0, "fiftyTwoWeekLow": 90.0}
    monkeypatch.setattr(au, "_instrument_info", lambda t: dict(info))
    c = a._extract_canonical("AAPL")
    expect = g.canonical_from_info(info)
    assert expect and {k: c[k] for k in expect} == expect
    assert "trailing_pe" not in c and c["currency"] == "USD"


def test_trader_warning_uses_markdown_bold_not_html():
    import bot.analyzer as a
    out = a._flag_trader_price_hallucination(
        "Entry Price: 20,000", canonical={"current_price": 98500.0})
    assert "**가격 환각 의심**" in out and "<b>" not in out
    # 그 경고 줄 안의 현재가는 기준값 그대로라 가드가 다시 걸지 않는다.
    assert g.find_mismatches(out, KR) == []


# ── 감사 CLI ───────────────────────────────────────────────────────────────

def _rec(report, **si):
    base = {"current_price": 98500.0, "market_cap": 1.9e12, "trailingPE": 28.86}
    base.update(si)
    return {"ticker": "005930.KS", "trade_date": "2026-10-21",
            "full_report": report, "stock_info": base}


def test_audit_counts_judged_and_missed_per_metric():
    from bot.scripts.report_guard_audit import audit, report_lines
    recs = [
        _rec("## 💰 펀더멘털\n시가총액 약 19조 원, PER 28.9배"),
        _rec("## 📈 시장 분석\n현재가 ₩98,500"),
        {"ticker": "X", "full_report": "PER 1배"},          # 기준 없음
    ]
    res = audit(recs)
    assert (res["records"], res["with_ref"], res["flagged_records"]) == (3, 2, 1)
    assert res["judged"]["market_cap"] == 1 and res["missed"]["market_cap"] == 1
    assert res["judged"]["per"] == 1 and res["missed"]["per"] == 0
    assert res["sec_missed"]["💰 펀더멘털"] == 1
    text = "\n".join(report_lines(res))
    assert "경고가 붙었을 리포트: 1/2" in text


def test_audit_splits_only_at_product_sections():
    # LLM 의 본문 소제목('## 동종업계 비교')은 섹션 경계가 아니다 — 제품처럼
    # 그 아래 비교 문단을 건너뛴다(독립 리뷰: 감사만 오탐을 세고 있었다).
    from bot.scripts.report_guard_audit import audit
    res = audit([_rec("## 💰 펀더멘털\nPER 28.9배\n## 동종업계 비교\n"
                      "SK하이닉스 PER 12.1배")])
    assert res["judged"]["per"] == 1 and res["missed"]["per"] == 0


def test_audit_with_nothing_to_compare_says_undeterminable():
    from bot.scripts.report_guard_audit import audit, report_lines
    text = "\n".join(report_lines(audit([{"ticker": "X", "full_report": "x"}])))
    assert "❓ 판정 불가" in text and "✅" not in text


def test_audit_reference_uses_market_currency_and_shared_rule():
    from bot.scripts.report_guard_audit import canonical_from_record
    c = canonical_from_record(_rec("", trailingPE=20000.0, priceToBook=0.0))
    assert c["currency"] == "KRW"                       # 응답 통화가 없으면 시장 설정
    assert canonical_from_record(_rec("", currency="INR"))["currency"] == "INR"
    assert "trailing_pe" not in c and "price_to_book" not in c


def test_audit_noisy_guard_is_a_sweep_finding():
    # 경고가 리포트 셋 중 하나 넘게 붙으면 고칠 수 있는 결함 — 주간 결산에 실린다.
    from bot.audit_sweep import _findings
    from bot.scripts.report_guard_audit import audit, report_lines
    res = audit([_rec("## 💰 펀더멘털\nPER 50배"), _rec("## 💰 펀더멘털\nPER 28.9배")])
    found = _findings("\n".join(report_lines(res)))
    assert len(found) == 1 and "1/2건(50%)" in found[0]
    quiet = audit([_rec("## 💰 펀더멘털\nPER 28.9배")] * 4)
    assert _findings("\n".join(report_lines(quiet))) == []


def test_audit_main_runs_with_sweep_argv(tmp_path, monkeypatch, capsys):
    # 주간 감사는 main([]) 로 부른다 — 봇 프로세스의 sys.argv 를 읽으면 안 된다.
    import json
    import bot.archive as arc
    from bot.scripts import report_guard_audit as rga
    from datetime import datetime, timedelta, timezone
    day = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    (tmp_path / day).mkdir()
    (tmp_path / day / "005930.KS.json").write_text(
        json.dumps(_rec("## 💰 펀더멘털\nPER 50배")), encoding="utf-8")
    monkeypatch.setattr(arc, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["bot", "--unknown-flag"])
    assert rga.main([]) == 0
    out = capsys.readouterr().out
    assert "report_guard_audit v1" in out and "1/1" in out


def test_audit_skips_report_head_and_scrubs_cross_marks():
    from bot.audit_sweep import _findings
    from bot.scripts.report_guard_audit import audit, report_lines
    # 머리(제목·지난 추천)는 제품이 가드를 안 건다 — 감사도 안 센다.
    res = audit([_rec("📋 리포트\nPER 50배\n## 💰 펀더멘털\nPER 28.9배")])
    assert res["judged"]["per"] == 1 and res["missed"]["per"] == 0
    # 원문 발췌의 ❌ 는 sweep 결함으로 세지 않는다 — 50% 경고 한 줄만.
    res2 = audit([_rec("## 💰 펀더멘털\n❌ PER 50배."), _rec("## 💰 펀더멘털\nPER 28.9배")])
    found = _findings("\n".join(report_lines(res2)))
    assert len(found) == 1 and "수치 대조 경고가 붙습니다" in found[0]


def test_extract_canonical_prefers_response_currency(monkeypatch):
    import bot.analyzer as a
    import tradingagents.agents.utils.agent_utils as au
    monkeypatch.setattr(au, "_instrument_info", lambda t: {
        "currentPrice": 1300.0, "currency": "INR", "marketCap": 1e12})
    c = a._extract_canonical("RELIANCE.NS")
    assert c["currency"] == "INR" and c["currency_symbol"] == "INR "
    assert g.scan("현재가 $15.6", c) == []          # 달러 환산값은 다른 통화
