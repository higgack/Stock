"""Regression tests for bot/dashboard.py — pure/deterministic functions.

Key regression: _parse_pct returned a NaN *float* for "+nan%" strings
(yfinance returns NaN Close for non-US ETF benchmarks) which caused
'+nan%' to pass all guards in _render_outcome_html and render on cards.
Surfaced: MediaTek 2454.TW / TSMC 2330.TW / Toyota 7203.T (2026-05-26).
Fixed: _parse_pct now returns None for NaN/inf; _render_outcome_html uses
_parse_pct as the validity gate instead of a string != 'n/a' check.
"""

from __future__ import annotations

import pytest

# dashboard.py imports `from bot.archive import ARCHIVE_ROOT` — that's
# a pure pathlib.Path with no heavy deps, so no extra mocking needed.
from bot.dashboard import (
    _badge_html,
    _format_seconds,
    _parse_pct,
    _render_outcome_html,
)


# ── _parse_pct ─────────────────────────────────────────────────────────────
# PRIMARY REGRESSION: nan% from yfinance NaN Close must be treated as None,
# not rendered as "+nan%" on dashboard cards.

class TestParsePct:
    # ── NaN variants — must return None (THE KEY FIX) ──
    def test_plus_nan_returns_none(self):
        # Core regression — surfaced MediaTek/TSMC/Toyota 2026-05-26
        assert _parse_pct("+nan%") is None

    def test_minus_nan_returns_none(self):
        assert _parse_pct("-nan%") is None

    def test_bare_nan_returns_none(self):
        assert _parse_pct("nan%") is None

    # ── Infinity variants — must return None ──
    def test_plus_inf_returns_none(self):
        assert _parse_pct("+inf%") is None

    def test_minus_inf_returns_none(self):
        assert _parse_pct("-inf%") is None

    # ── Explicit n/a ──
    def test_na_returns_none(self):
        assert _parse_pct("n/a") is None

    # ── Empty / None ──
    def test_empty_string_returns_none(self):
        assert _parse_pct("") is None

    def test_none_input_returns_none(self):
        assert _parse_pct(None) is None  # type: ignore[arg-type]

    # ── Garbage strings ──
    def test_garbage_returns_none(self):
        assert _parse_pct("garbage") is None

    def test_no_percent_sign_still_parses(self):
        # _parse_pct strips "%" if present but does not require it
        assert _parse_pct("5.0") == pytest.approx(5.0)

    # ── Valid values ──
    def test_positive_pct(self):
        assert _parse_pct("+5.0%") == pytest.approx(5.0)

    def test_negative_pct(self):
        assert _parse_pct("-3.2%") == pytest.approx(-3.2)

    def test_positive_without_plus(self):
        assert _parse_pct("12.5%") == pytest.approx(12.5)

    def test_zero_pct(self):
        assert _parse_pct("0.0%") == pytest.approx(0.0)

    def test_large_positive(self):
        assert _parse_pct("+120.3%") == pytest.approx(120.3)

    def test_large_negative(self):
        assert _parse_pct("-99.9%") == pytest.approx(-99.9)


# ── _render_outcome_html ───────────────────────────────────────────────────
# Validates that NaN-tainted log entries are silently suppressed (empty
# string) rather than rendered as '+nan%' on the dashboard card.

class TestRenderOutcomeHtml:
    # ── None / missing input → empty string ──
    def test_none_input(self):
        assert _render_outcome_html(None) == ""

    def test_no_raw_field(self):
        assert _render_outcome_html({"ticker": "NVDA", "rating": "buy"}) == ""

    # ── NaN entries — must not render (THE KEY FIX) ──
    def test_nan_raw_hidden(self):
        # Pre-fix: "+nan%" was truthy + not "n/a" → rendered.
        # Post-fix: _parse_pct("+nan%") is None → window rejected → "".
        resolved = {"raw": "+nan%", "alpha": "+nan%", "rating": "buy", "ticker": "2454.TW"}
        assert _render_outcome_html(resolved) == ""

    def test_nan_raw_minus_hidden(self):
        resolved = {"raw": "-nan%", "alpha": "-nan%", "rating": "sell", "ticker": "7203.T"}
        assert _render_outcome_html(resolved) == ""

    # ── n/a entries — must not render ──
    def test_na_raw_hidden(self):
        resolved = {"raw": "n/a", "alpha": "n/a", "rating": "hold", "ticker": "NVDA"}
        assert _render_outcome_html(resolved) == ""

    # ── Valid entries — must render ──
    def test_valid_buy_renders(self):
        resolved = {
            "raw": "+5.0%", "alpha": "+3.0%",
            "rating": "buy", "ticker": "NVDA",
        }
        html = _render_outcome_html(resolved)
        assert html != ""
        assert "5거래일 후" in html
        assert "+5.0%" in html

    def test_valid_sell_renders(self):
        resolved = {
            "raw": "-2.1%", "alpha": "-1.5%",
            "rating": "sell", "ticker": "NVDA",
        }
        html = _render_outcome_html(resolved)
        assert html != ""
        assert "매도 추천 맞음" in html

    def test_valid_sell_miss_renders(self):
        # Sell call but alpha > 0 → miss
        resolved = {
            "raw": "+3.0%", "alpha": "+2.0%",
            "rating": "sell", "ticker": "NVDA",
        }
        html = _render_outcome_html(resolved)
        assert "매도 추천 틀림" in html

    def test_valid_hold_renders_no_hit_miss(self):
        resolved = {
            "raw": "+1.0%", "alpha": "+0.5%",
            "rating": "hold", "ticker": "NVDA",
        }
        html = _render_outcome_html(resolved)
        assert "보유 추천" in html
        # Hold: no 맞음/틀림 verdict
        assert "맞음" not in html
        assert "틀림" not in html

    def test_alpha_zero_is_equal(self):
        resolved = {
            "raw": "+1.0%", "alpha": "0.0%",
            "rating": "buy", "ticker": "NVDA",
        }
        html = _render_outcome_html(resolved)
        assert "섹터대비 동등" in html

    def test_alpha_negative_is_low(self):
        resolved = {
            "raw": "+1.0%", "alpha": "-1.0%",
            "rating": "hold", "ticker": "NVDA",
        }
        html = _render_outcome_html(resolved)
        assert "섹터대비 low" in html

    # ── NaN alpha with valid raw → show raw, skip verdict (alpha gate) ──
    def test_nan_alpha_with_valid_raw(self):
        # alpha NaN → _parse_pct returns None → no verdict/magnitude;
        # raw is valid so the window is kept and the line is shown.
        resolved = {
            "raw": "+5.0%", "alpha": "+nan%",
            "rating": "buy", "ticker": "NVDA",
        }
        html = _render_outcome_html(resolved)
        # raw is still shown
        assert "+5.0%" in html
        # No verdict since alpha parsing failed
        assert "맞음" not in html
        assert "틀림" not in html
        assert "섹터대비" not in html

    # ── outcomes_extra (15d/30d windows) ──
    def test_outcomes_extra_nan_skipped(self):
        resolved = {
            "raw": "+5.0%", "alpha": "+3.0%",
            "rating": "buy", "ticker": "NVDA",
            "outcomes_extra": [
                {"days": 15, "raw": "+nan%", "alpha": "+nan%"},
                {"days": 30, "raw": "+8.0%", "alpha": "+5.0%"},
            ],
        }
        html = _render_outcome_html(resolved)
        assert "nan" not in html
        assert "30거래일" in html  # valid 30d entry still shows


# ── _format_seconds ────────────────────────────────────────────────────────

class TestFormatSeconds:
    def test_under_minute(self):
        assert _format_seconds(45) == "45초"

    def test_exactly_minute(self):
        assert _format_seconds(60) == "1분 0초"

    def test_ninety_seconds(self):
        assert _format_seconds(90) == "1분 30초"

    def test_one_hour(self):
        assert _format_seconds(3600) == "60분 0초"

    def test_zero(self):
        assert _format_seconds(0) == "0초"


# ── _badge_html ────────────────────────────────────────────────────────────

class TestBadgeHtml:
    def test_buy_badge_contains_buy_text(self):
        html = _badge_html("buy")
        assert html != ""
        assert "badge" in html.lower() or "매수" in html or "buy" in html.lower()

    def test_sell_badge_not_empty(self):
        assert _badge_html("sell") != ""

    def test_hold_badge_not_empty(self):
        assert _badge_html("hold") != ""

    def test_overweight_badge_not_empty(self):
        assert _badge_html("overweight") != ""

    def test_underweight_badge_not_empty(self):
        assert _badge_html("underweight") != ""

    def test_unknown_rating_not_empty(self):
        # Unknown ratings should still produce some output, not crash
        result = _badge_html("unknown")
        assert isinstance(result, str)
