"""Regression tests for pure/deterministic functions in bot/analyzer.py.

Covers: _extract_rating, _abbrev_korean, _has_pm_override_trigger,
and _extract_stance. All are pure functions with no network calls.

Heavy deps (tradingagents / langchain_core) are mocked in conftest.py at
module level so bot.analyzer can be imported cleanly.

Seed cases come from CLAUDE.md per-ticker review history.
"""

from __future__ import annotations

import pytest

import bot.analyzer as _a


# ── _extract_rating ────────────────────────────────────────────────────────

class TestExtractRating:
    def test_buy(self):
        assert _a._extract_rating("Rating: Buy\nEnter at $189.") == "Buy"

    def test_overweight(self):
        assert _a._extract_rating("Rating: Overweight\n비중확대 추천.") == "Overweight"

    def test_sell(self):
        assert _a._extract_rating("Sell immediately.") == "Sell"

    def test_underweight(self):
        assert _a._extract_rating("UNDERWEIGHT — exit position.") == "Underweight"

    def test_hold(self):
        assert _a._extract_rating("HOLD position — wait for catalyst.") == "Hold"

    def test_case_insensitive(self):
        assert _a._extract_rating("rating: buy — strong momentum") == "Buy"

    def test_no_rating_returns_none(self):
        # No recognizable keyword → None
        assert _a._extract_rating("Complex situation with competing factors.") is None

    def test_empty_returns_none(self):
        assert _a._extract_rating("") is None

    def test_overweight_wins_over_buy_if_present(self):
        # OVERWEIGHT appears before BUY in the scan order → Overweight returned
        text = "OVERWEIGHT — we are Buyers at this price."
        result = _a._extract_rating(text)
        assert result == "Overweight"


# ── _abbrev_korean ─────────────────────────────────────────────────────────
# 두산 000150.KS 2026-05-18: 부채비율 배 unit + 백만 polish issues.
# Unit labels ('백만 원', '억 원') must be preserved.

class TestAbbrevKorean:
    def test_trillion_jo(self):
        result = _a._abbrev_korean(2_000_000_000_000)
        assert "조" in result
        assert "2.00" in result

    def test_hundred_million_eok(self):
        result = _a._abbrev_korean(500_000_000)
        assert "억" in result
        assert "5" in result

    def test_million_baekman(self):
        result = _a._abbrev_korean(3_500_000)
        assert "백만" in result

    def test_below_million_returns_empty(self):
        # Numbers below 1M produce empty string (not abbreviated)
        assert _a._abbrev_korean(999_999) == ""

    def test_exactly_one_trillion(self):
        result = _a._abbrev_korean(10 ** 12)
        assert "조" in result

    def test_exactly_one_hundred_million(self):
        result = _a._abbrev_korean(10 ** 8)
        assert "억" in result

    def test_exactly_one_million(self):
        result = _a._abbrev_korean(10 ** 6)
        assert "백만" in result


# ── _has_pm_override_trigger ───────────────────────────────────────────────
# CLAUDE.md: PM override discipline — trigger must be explicitly named.
# Tests seed from FORM 2026-05-23 unanimous Hold → PM Overweight case.

class TestHasPmOverrideTrigger:
    # ── RSI extreme (numeric, ≥70 or ≤30) ──
    def test_rsi_above_75(self):
        assert _a._has_pm_override_trigger("RSI: 78.3 — 과매수 구간") is True

    def test_rsi_exactly_70(self):
        assert _a._has_pm_override_trigger("현재 RSI: 70 진입") is True

    def test_rsi_below_30(self):
        # The RSI regex matches "RSI: <number>" / "RSI(<label>): <number>" forms.
        # "RSI 지표: 22.1" has non-numeric/non-paren text between RSI and colon
        # → not matched by the regex. Use the canonical colon form instead.
        assert _a._has_pm_override_trigger("RSI: 22.1") is True

    def test_rsi_moderate_not_triggered(self):
        # RSI 55 is not an extreme
        assert _a._has_pm_override_trigger("RSI: 55.36") is False

    def test_rsi_no_number_not_triggered(self):
        # RSI mention without a numeric value should NOT trigger
        # (the function looks for RSI + numeric pattern)
        assert _a._has_pm_override_trigger("RSI 분석 참조") is False

    # ── Catalyst countdown (D-N format) ──
    def test_d1_countdown(self):
        assert _a._has_pm_override_trigger("D-3일 어닝 발표 예정") is True

    def test_n_days_countdown(self):
        assert _a._has_pm_override_trigger("5일 이내 FOMC 결정") is True

    # ── Keyword triggers ──
    def test_keyword_과매수(self):
        assert _a._has_pm_override_trigger("현재 과매수 구간 진입") is True

    def test_keyword_과매도(self):
        assert _a._has_pm_override_trigger("RSI가 과매도 영역") is True

    def test_keyword_corp_action(self):
        assert _a._has_pm_override_trigger("HARD GUARD: 주식분할 감지") is True

    def test_keyword_데이터없음(self):
        assert _a._has_pm_override_trigger("재무 데이터 없음 — HOLD") is True

    def test_keyword_fomc(self):
        assert _a._has_pm_override_trigger("FOMC 회의 D-2") is True

    def test_keyword_어닝발표(self):
        assert _a._has_pm_override_trigger("어닝 발표 예정") is True

    # ── No trigger ──
    def test_no_trigger_generic(self):
        # 코미코 2026-05-17: "단기 모멘텀 약화" alone must NOT trigger
        assert _a._has_pm_override_trigger("단기 모멘텀 약화로 매도 권장") is False

    def test_empty_no_trigger(self):
        assert _a._has_pm_override_trigger("") is False


# ── _extract_stance ────────────────────────────────────────────────────────
# Three-pass scan: (1) conclusion zone explicit, (2) full body explicit,
# (3) bare keyword. Tests seed from 경동나비엔 009450.KS 2026-05-22 bug
# where "매도 압력을 시사합니다" in body overrode "HOLD 의견" in conclusion.

class TestExtractStance:
    # ── Conclusion zone wins over trailing noise ──
    def test_conclusion_zone_beats_body_noise(self):
        # Mirrors 경동나비엔 2026-05-22: body has 매도 mention but
        # conclusion says HOLD — conclusion zone (last 800 chars) wins.
        body = (
            "단기적으로 매도 압력을 시사합니다.\n" * 10
            + "결론: HOLD 의견을 제시합니다."
        )
        assert _a._extract_stance(body) == "보유"

    def test_explicit_buy_opinion_kr(self):
        assert _a._extract_stance("결론: BUY 의견을 유지합니다.") == "매수"

    def test_explicit_sell_opinion_kr(self):
        assert _a._extract_stance("최종 입장: SELL 의견 — 손절 권고.") == "매도"

    def test_final_transaction_proposal_buy(self):
        assert _a._extract_stance(
            "FINAL TRANSACTION PROPOSAL: **BUY**"
        ) == "매수"

    def test_final_transaction_proposal_hold(self):
        assert _a._extract_stance(
            "FINAL TRANSACTION PROPOSAL: HOLD"
        ) == "보유"

    def test_final_transaction_proposal_sell(self):
        assert _a._extract_stance(
            "FINAL TRANSACTION PROPOSAL: SELL"
        ) == "매도"

    def test_투자제언_hold(self):
        # SNG 2026-05-17: '투자 제언: HOLD' pattern
        assert _a._extract_stance("투자 제언: HOLD — 5거래일 보유 유지.") == "보유"

    def test_투자제언_buy(self):
        assert _a._extract_stance("투자 제언: 매수 추천.") == "매수"

    # ── Horizon-based conclusion ──
    def test_horizon_hold(self):
        assert _a._extract_stance(
            "5거래일 horizon에서는 HOLD를 권고합니다."
        ) == "보유"

    # ── Fallback bare keyword ──
    def test_bare_매수_fallback(self):
        body = "전반적으로 매수 관점입니다."
        result = _a._extract_stance(body)
        assert result == "매수"

    def test_bare_매도_fallback(self):
        body = "종합적으로 매도를 권장합니다."
        assert _a._extract_stance(body) == "매도"

    # ── Empty / None ──
    def test_none_input(self):
        assert _a._extract_stance(None) == ""

    def test_empty_string(self):
        assert _a._extract_stance("") == ""

    def test_no_stance_keyword(self):
        # Report with no stance keyword → empty string
        result = _a._extract_stance("펀더멘털 분석 결과 PER 25배 수준입니다.")
        assert result == ""
