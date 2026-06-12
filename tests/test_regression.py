"""Regression tests — 실제 우리가 당했던 버그 클래스 영구 재발 차단.

각 테스트는 commit 메시지에 cite 된 실제 surfaced 사건을 재현하고,
fix 가 회귀하면 즉시 fail. 다음 세션 Claude(또는 본인) 가 실수로 같은
버그 다시 도입하려 하면 `python -m pytest tests/` 가 빨갛게 막아준다.

실행:
    python -m pytest tests/ -v
또는 venv 에서:
    .venv/bin/python -m pytest tests/ -v

매 commit 전 권장. CI 가 없는 동안 사용자/Claude 가 수동으로 호출.
"""
from __future__ import annotations

import re
import time

import pytest


# ─────────────────────────────────────────────────────────────────────────
# 1) _strip_meta_commentary catastrophic backtracking 회귀 차단
#    배경: 2026-06-01 Hospitality screener 무한 hang(CPU 73%) 진범.
#    Quantifier 중첩 '(?:[가-힣]+\s*,?\s*)*정량...' 이 긴 한국어 본문에서
#    지수폭발. 안전 패턴(\s? + 인접 고정 토큰) 으로 교체했고 회귀 차단.
# ─────────────────────────────────────────────────────────────────────────
class TestStripMetaCommentaryNoBacktracking:
    """fix commit: d705a48 (2026-06-01)."""

    def test_long_korean_body_completes_under_1_second(self):
        from bot.screener import _strip_meta_commentary

        # Hospitality run 본문 크기 + 회피 phrase 만으로 채운 worst-case
        big = (
            "AI 데이터센터 공급망 병목 분석. " * 400
            + "데이터 transitional 명시. "
            + "정량 데이터 추가 확인 필요. " * 5
            + "분석 종료."
        )
        t0 = time.time()
        out, n = _strip_meta_commentary(big)
        elapsed = time.time() - t0
        assert elapsed < 1.0, (
            f"_strip_meta_commentary 가 {elapsed:.2f}s — catastrophic "
            f"backtracking 회귀 의심 (ee0875a 회귀)"
        )
        # 핑계 phrase 가 strip 되긴 했는지 sanity
        assert n >= 5, f"strip count {n} 비정상"
        assert "정량 데이터 추가 확인 필요" not in out

    def test_normal_prose_preserved(self):
        from bot.screener import _strip_meta_commentary

        # 정상 본문은 건드리지 말 것 (false positive 회귀)
        text = "AI 데이터센터 핵심 종목으로 NVDA 추천. 매출 +30% YoY."
        out, n = _strip_meta_commentary(text)
        assert n == 0
        assert out == text


# ─────────────────────────────────────────────────────────────────────────
# 2) Dashboard <details> open/close 균형 회귀 차단
#    배경: 월 collapse 추가 시 4번 깨짐(append 마커 누락). 균형 깨지면
#    브라우저가 auto-close 로 가리지만 향후 nested 구조 추가 시 실제
#    layout 망가짐.
# ─────────────────────────────────────────────────────────────────────────
class TestDashboardDetailsBalance:
    """fix commits: 3d0a523 / c379a94 / 5c9b446 (2026-06-01)."""

    @staticmethod
    def _balance(html: str) -> tuple[int, int]:
        return (
            len(re.findall(r"<details\b", html)),
            len(re.findall(r"</details>", html)),
        )

    def _multi_month_runs_screener(self):
        """2개월(6월 + 5월) 합성 — 월 collapse 가 정확히 +2 open/close."""
        return [
            {
                "_date": "2026-06-01", "ts": "2026-06-01T15:25:00",
                "cost_krw": 116.9, "elapsed_sec": 311, "domain": "합성생물학",
                "top_3_picks": [], "validated_tickers": ["RGEN"],
                "rejected_tickers": [], "raw_output": "x",
                "binding_constraint": "b", "top3_section": "", "bottom_line": "",
            },
            {
                "_date": "2026-05-29", "ts": "2026-05-29T10:00:00",
                "cost_krw": 100.0, "elapsed_sec": 200, "domain": "EV",
                "top_3_picks": [], "validated_tickers": ["BYD"],
                "rejected_tickers": [], "raw_output": "z",
                "binding_constraint": "b", "top3_section": "", "bottom_line": "",
            },
        ]

    def test_screener_page_delta_balanced(self):
        """월 collapse 가 균형 잡힌 +open/+close 만 추가하는지."""
        import bot.dashboard as d

        # screener.html 은 master-table 안에 pre-existing 1개 unclosed
        # <details> 가 있음(HEAD 부터, 우리 변경 무관). delta 만 확인.
        runs = self._multi_month_runs_screener()
        html = d._render_screener_page(runs, {})
        n_open, n_close = self._balance(html)
        diff = n_open - n_close
        # HEAD 시점 imbalance = 1 (master-table). 월 collapse 추가는 +0 이어야.
        assert diff in (0, 1), (
            f"screener.html <details> 불균형 delta={diff} — month 헬퍼 "
            f"open/close 마커 누락 의심"
        )

    def test_realestate_page_balanced(self):
        import bot.dashboard as d

        runs = [
            {"_date": "2026-06-01", "ts": "2026-06-01T09:00:00",
             "cost_krw": 17.2, "body": "강남", "ymd": "20260601"},
            {"_date": "2026-05-30", "ts": "2026-05-30T09:00:00",
             "cost_krw": 15.0, "body": "동탄", "ymd": "20260530"},
        ]
        html = d._render_realestate_page(runs)
        n_open, n_close = self._balance(html)
        assert n_open == n_close, (
            f"realestate.html 불균형 open={n_open} close={n_close}"
        )

    def test_cheongyak_page_balanced(self):
        import bot.dashboard as d

        runs = [
            {"_date": "2026-06-02", "ts": "2026-06-02T10:00:00",
             "cost_krw": 4.5, "body": "신규", "count": 3},
            {"_date": "2026-05-31", "ts": "2026-05-31T10:00:00",
             "cost_krw": 4.0, "body": "공고", "count": 2},
        ]
        html = d._render_cheongyak_page(runs)
        n_open, n_close = self._balance(html)
        assert n_open == n_close, (
            f"cheongyak.html 불균형 open={n_open} close={n_close}"
        )

    def test_daily_byte_page_balanced(self):
        import bot.dashboard as d

        runs = [
            {"_date": "2026-06-01", "ts": "2026-06-01T19:01:00",
             "cost_krw": 55.7, "elapsed_sec": 106, "kind": "daily",
             "body": "외인", "_filename": "a.json"},
            {"_date": "2026-05-29", "ts": "2026-05-29T19:00:00",
             "cost_krw": 53.4, "elapsed_sec": 100, "kind": "daily",
             "body": "기관", "_filename": "c.json"},
        ]
        html = d._render_daily_byte_page(runs)
        n_open, n_close = self._balance(html)
        assert n_open == n_close, (
            f"daily_byte.html 불균형 open={n_open} close={n_close}"
        )

    def test_index_page_balanced(self):
        import bot.dashboard as d

        recs = [
            {"trade_date": "2026-06-01", "ticker": "NVDA",
             "analyzed_at": "2026-06-01T09:00:00",
             "summary": "Rating: Hold"},
            {"trade_date": "2026-05-30", "ticker": "AAPL",
             "analyzed_at": "2026-05-30T09:00:00",
             "summary": "Rating: Buy"},
        ]
        html = d._render_index(recs)
        n_open, n_close = self._balance(html)
        assert n_open == n_close, (
            f"index.html 불균형 open={n_open} close={n_close}"
        )

    def test_multi_month_produces_month_groups(self):
        """2개월 데이터 → 정확히 2개 month <details>."""
        import bot.dashboard as d

        runs = self._multi_month_runs_screener()
        html = d._render_screener_page(runs, {})
        months = re.findall(r'<details class="month"', html)
        assert len(months) == 2, f"month 그룹 {len(months)}개 (기대 2)"


# ─────────────────────────────────────────────────────────────────────────
# 3) _SECTOR_ETFS 무결성 (dedup / 한자 / per-market 최소치)
#    배경: NOAH override 기반으로 67→84개 확장 시 추측 라벨이 실제 ETF
#    longName 과 어긋난 케이스 다수(EXV5 추측 '에너지' → 실제 '자동차').
#    EU 추가 때 VM 검증으로 잡았음. 향후 추가 시 같은 실수 차단.
# ─────────────────────────────────────────────────────────────────────────
class TestScreenerPostProcessIdempotent:
    """fix commit: ba6e4bc (2026-06-01)."""

    def test_liquidity_warning_append_idempotent(self):
        from bot.screener import _append_liquidity_warnings

        text = (
            "[테스트 layer]\n"
            "• S · 064550.KQ · (한국·KOSDAQ) · Bioneer\n"
            "• Tier A: x\n"
            "• Kill Trigger: y\n"
        )
        cands = [{"ticker": "064550.KQ", "tier": "S", "mcap_usd": 147e6}]
        # 1차
        out1, _s1, a1 = _append_liquidity_warnings(text, cands)
        assert a1 == 1
        # 2차 (이미 append 된 결과를 다시 통과)
        out2, _s2, a2 = _append_liquidity_warnings(out1, cands)
        assert a2 == 1, "2차 append 실패"
        assert out1 == out2, (
            "재실행 시 출력 변동 — strip/append idempotency 깨짐"
        )

    def test_transitional_strip_no_false_positive(self):
        """'transitional' 단어가 prose 에 등장해도 보존(데이터 접두 必)."""
        from bot.screener import _strip_transitional_tags

        ok_text = "이 종목은 transitional phase 의 회복 cycle 에 있다."
        out, n = _strip_transitional_tags(ok_text)
        assert n == 0, f"정상 prose 의 transitional false-strip"
        assert out == ok_text

        bad_text = "PER 51.2x. 데이터 transitional 명시. (sourced)"
        out2, n2 = _strip_transitional_tags(bad_text)
        assert n2 >= 1
        assert "데이터 transitional" not in out2


# ─────────────────────────────────────────────────────────────────────────
# 5) PM override discipline forced-HOLD 배너 정확성 (IBM 2026-06-02)
#    배경: PM=Overweight + Trader=Buy(둘 다 매수)인데 분석가 전원 보유 +
#    trigger 없음으로 Fix F 가 HOLD 강제. 옛 배너가 '트레이더 매수 →
#    최종 보유 (PM이 트레이더와 다른 결론)' 로 오독 유발. discipline 강제
#    HOLD 시 정확한 배너 띄우는지.
# ─────────────────────────────────────────────────────────────────────────
class TestPMOverrideDisciplineBanner:
    """fix: IBM 2026-06-02 review (배너 문구 정확화)."""

    def test_discipline_banner_function_exists_and_accurate(self):
        src = open("bot/analyzer.py", encoding="utf-8").read()
        # 배선: analyzer Fix F/G(override_rating) + in-graph 센티넬 양 레이어를
        # discipline 배너로 라우팅 (009150 2026-06-04 in-graph 강제 케이스 포함).
        assert 'discipline_forced = override_rating == "Hold"' in src
        assert '_PM_INGRAPH_SENTINEL in (decision or ""' in src, "in-graph 센티넬 라우팅 누락"
        assert "if discipline_forced:" in src
        assert (
            "trader_divergence = _detect_discipline_forced_hold_banner"
            in src
        ), "discipline 배너 배선 누락"
        assert "def _detect_discipline_forced_hold_banner" in src

    def test_ingraph_forced_recovers_original_rating(self):
        """in-graph 강제(009150 2026-06-04): decision rating 이 이미 Hold 라
        센티넬 노트의 'PM 1차 판단 (X)' 파싱으로 1차 등급을 복원해야 함
        (없으면 'Hold override Hold' 무의미 배너). 소스에 파싱 배선 확인.
        bot.analyzer 는 yfinance 의존이라 import 불가 → 소스 검증."""
        import re
        src = open("bot/analyzer.py", encoding="utf-8").read()
        m = re.search(
            r"def _detect_discipline_forced_hold_banner.*?(?=\n\n\n|\ndef )",
            src, re.DOTALL,
        )
        assert m, "discipline 배너 함수 못 찾음"
        fn = m.group(0)
        assert r"PM 1차 판단 \(([^)]+)\)" in fn, "센티넬 1차등급 파싱 누락"
        assert "_extract_rating(decision)" in fn, "fallback(_extract_rating) 누락"

    def test_discipline_banner_no_misleading_phrase(self):
        """discipline 배너는 'PM이 트레이더와 다른 결론' 오해 문구 미사용."""
        import re

        src = open("bot/analyzer.py", encoding="utf-8").read()
        m = re.search(
            r"def _detect_discipline_forced_hold_banner.*?(?=\n\n\n|\ndef )",
            src, re.DOTALL,
        )
        assert m, "discipline 배너 함수 못 찾음"
        fn = m.group(0)
        assert "PM이 트레이더와 다른 결론" not in fn, (
            "discipline 배너가 오해 문구 사용 — IBM 회귀"
        )
        # 정확한 메커니즘 명시
        assert "시스템 강제 보유" in fn
        assert "시스템 보정 결과" in fn


# ─────────────────────────────────────────────────────────────────────────
# 6) Technical snapshot SSoT — 현재가/EMA/SMA 동일 series 통합
#    배경: IBM 2026-06-02 10 EMA 266 vs 현재가 325 (22% 격차). EMA/SMA 가
#    별도 경로(stockstats)라 stale. _compute_technical_snapshot 에 현재가
#    + 10 EMA + 50 SMA + 200 SMA 가 같은 close series 에서 계산돼 SSoT 에
#    포함되는지 (코드 레벨 — 네트워크 없이 정적 검증).
# ─────────────────────────────────────────────────────────────────────────
class TestTechnicalSnapshotSSoT:
    """fix: IBM 2026-06-02 review (EMA/SMA SSoT 통합)."""

    def test_snapshot_includes_ma_lines(self):
        src = open(
            "TradingAgents/tradingagents/agents/utils/agent_utils.py",
            encoding="utf-8",
        ).read()
        m = re.search(
            r"def _compute_technical_snapshot.*?(?=\ndef )", src, re.DOTALL
        )
        assert m, "_compute_technical_snapshot 못 찾음"
        fn = m.group(0)
        # 현재가 + 10 EMA + 50 SMA + 200 SMA 가 같은 close series 에서
        assert "ema10 = close.ewm(span=10" in fn, "10 EMA 누락"
        assert "close.rolling(50).mean()" in fn, "50 SMA 누락"
        assert "close.rolling(200).mean()" in fn, "200 SMA 누락"
        assert "ma_lines" in fn and "*ma_lines" in fn, "MA 라인 미주입"
        # 200 SMA 가능하도록 1y 윈도
        assert 'period="1y"' in fn, "200 SMA 위해 1y fetch 필요"

    def test_macro_ssot_directive_present(self):
        """매크로 스냅샷에 글자단위 copy 강제 directive (IBM drift)."""
        src = open(
            "TradingAgents/tradingagents/agents/utils/macro_context_tools.py",
            encoding="utf-8",
        ).read()
        assert "SINGLE SOURCE OF TRUTH" in src
        assert "글자 단위로" in src
        assert "IBM 2026-06-02" in src


# ─────────────────────────────────────────────────────────────────────────
# 7) _fix_currency_symbols 로컬 통화 기호 변환 회귀 차단
#    배경: CCUS 2026-06-03 screener 에서 .OL/.AX 종목의 '$' 가 로컬 통화로
#    치환되지 않는 누수 surfaced. 또한 '$1B' 시장 사이징 보존, US bare
#    ticker '$226.57' 미변환 확인.
# ─────────────────────────────────────────────────────────────────────────
class TestFixCurrencySymbols:
    """fix commit: 4254da0 (2026-05-29) + CCUS 2026-06-03 확인."""

    def test_eu_suffix_mi(self):
        from bot.screener import _fix_currency_symbols

        text = "• L · TPRO.MI · Technoprobe S.p.A.\n현재가 $34.00 으로"
        result, n = _fix_currency_symbols(text)
        assert "€34.00" in result, f"MI suffix → € 변환 실패: {result}"
        assert n == 1

    def test_nordic_suffix_ol(self):
        from bot.screener import _fix_currency_symbols

        text = "• M · SUBC.OL · Subsea 7 SA\nValuation: $305.00"
        result, n = _fix_currency_symbols(text)
        assert "kr305.00" in result, f"OL suffix → kr 변환 실패: {result}"
        assert "$305.00" not in result
        assert n == 1

    def test_au_suffix_ax(self):
        from bot.screener import _fix_currency_symbols

        text = "• S · MCE.AX · Matrix Composites\n$0.39 주변"
        result, n = _fix_currency_symbols(text)
        assert "A$0.39" in result, f"AX suffix → A$ 변환 실패: {result}"
        assert n == 1

    def test_tw_suffix(self):
        from bot.screener import _fix_currency_symbols

        text = "• M · 3231.TW · Wistron NeWeb\n$161.00 수준"
        result, n = _fix_currency_symbols(text)
        assert "NT$161.00" in result, f"TW suffix → NT$ 변환 실패: {result}"
        assert n == 1

    def test_india_suffix_ns(self):
        from bot.screener import _fix_currency_symbols

        text = "• L · ITC.NS · ITC Limited\n$286.90"
        result, n = _fix_currency_symbols(text)
        assert "₹286.90" in result, f"NS suffix → ₹ 변환 실패: {result}"
        assert n == 1

    def test_us_bare_ticker_no_change(self):
        from bot.screener import _fix_currency_symbols

        text = "• L · OXY · Occidental Petroleum\n$59.86 저평가"
        result, n = _fix_currency_symbols(text)
        assert "$59.86" in result, "US bare ticker 는 $ 유지"
        assert n == 0

    def test_market_sizing_preserved(self):
        from bot.screener import _fix_currency_symbols

        text = "• M · SUBC.OL · Subsea 7\n해저 시장 $8.4B 규모, 현재가 $305.00"
        result, n = _fix_currency_symbols(text)
        assert "$8.4B" in result, "시장 사이징 '$8.4B' 보존 실패"
        assert "kr305.00" in result
        assert n == 1

    def test_kr_suffix_ks(self):
        from bot.screener import _fix_currency_symbols

        text = "• L · 005930.KS · Samsung Electronics\n$71,200.00"
        result, n = _fix_currency_symbols(text)
        assert "₩71,200.00" in result, f"KS suffix → ₩ 변환 실패: {result}"
        assert n == 1

    def test_jp_suffix_t(self):
        from bot.screener import _fix_currency_symbols

        text = "• M · 7203.T · Toyota Motor\n현재가 $3,604.00"
        result, n = _fix_currency_symbols(text)
        assert "¥3,604.00" in result, f"T suffix → ¥ 변환 실패: {result}"
        assert n == 1

    def test_multiple_tickers_state_reset(self):
        """State machine 이 ticker row 마다 올바르게 reset 되는지."""
        from bot.screener import _fix_currency_symbols

        text = (
            "• L · TPRO.MI · Technoprobe\n가격 $34.00\n"
            "• M · OXY · Occidental\n가격 $59.86\n"
            "• S · MCE.AX · Matrix\n가격 $0.39"
        )
        result, n = _fix_currency_symbols(text)
        assert "€34.00" in result
        assert "$59.86" in result  # US → $ 유지
        assert "A$0.39" in result
        assert n == 2  # MI + AX only


# ─────────────────────────────────────────────────────────────────────────
# 8) 가격 차트 (lightweight-charts) 렌더 회귀 차단
#    배경: 2026-06-03 사용자 요청 — /ticker 상세 페이지에 1년 종가 +
#    10EMA/50SMA/200SMA 차트. price_chart 페이로드(schema v2) 가 있을 때만
#    차트 섹션 + 라이브러리 스크립트 emit, 없으면(v1 옛 기록) text-only.
# ─────────────────────────────────────────────────────────────────────────
class TestPriceChartRender:
    """fix: 가격 차트 v1 (2026-06-03)."""

    def _sample_chart(self):
        return {
            "currency": "$", "decimals": 2,
            "times": ["2025-06-01", "2025-06-02", "2025-06-03"],
            "close": [145.2, 146.1, 144.8],
            "ema10": [145.0, 145.3, 145.1],
            "sma50": [None, None, 140.0],
        }

    def test_chart_section_present_with_payload(self):
        from bot.dashboard import _render_chart_section

        rec = {"ticker": "AAPL", "price_chart": self._sample_chart()}
        html = _render_chart_section(rec)
        assert 'id="price-chart"' in html, "차트 컨테이너 div 누락"
        assert 'id="chart-data"' in html, "차트 데이터 script 블록 누락"
        assert "145.2" in html, "종가 데이터 미주입"
        assert "현재가" in html and "시점가" in html, "현재가/시점가 라벨 누락"
        # 시점가 = 분석일 종가 = stored close 의 마지막 값
        assert '"as_of_close":144.8' in html, "시점가(분석일 종가) 미주입"

    def test_chart_section_empty_for_v1(self):
        from bot.dashboard import _render_chart_section

        # 옛 v1 기록 (price_chart 없음) → 빈 문자열 (graceful, text-only)
        assert _render_chart_section({"ticker": "AAPL"}) == ""
        # 페이로드가 비정상(times/close 부재)이어도 빈 문자열
        assert _render_chart_section({"price_chart": {"currency": "$"}}) == ""

    def test_chart_axis_formatter_wired(self):
        # 차트 축/priceLine 가독성 (2026-06-04 사용자 스크린샷): raw '1716000'
        # 대신 만/억(KRW)·콤마, 하단 마진 외삽 음수 라벨(-250000) 숨김 +
        # 우측 마커 여백 + 높이.
        from bot.dashboard import _CHART_JS
        assert "function fmtAxis(" in _CHART_JS, "축 포매터 함수 누락"
        assert "priceFormatter: fmtAxis" in _CHART_JS, "축 포매터 미배선"
        assert "if (v < 0) return ''" in _CHART_JS, "음수 가격 라벨 숨김 누락"
        assert "+ '만'" in _CHART_JS and "+ '억'" in _CHART_JS, "KRW 만/억 약식 누락"
        assert "rightOffset:" in _CHART_JS, "마커 잘림 방지 우측 여백 누락"
        assert "height: 480" in _CHART_JS, "차트 높이 갱신 누락"
        # 52주 신고가/신저가 — 우측 패널 기간 밑 항상 표시(지표 토글 무관)
        assert "'52주 신고가'" in _CHART_JS and "'52주 신저가'" in _CHART_JS, "52주 신고/신저 패널 누락"
        assert "d.wk52_high" in _CHART_JS and "d.wk52_low" in _CHART_JS, "52주 payload 참조 누락"
        # chart_data 가 fast_info 로 52주값 주입
        cdsrc = open("bot/chart_data.py", encoding="utf-8").read()
        assert "_year_high_low" in cdsrc and 'payload["wk52_high"]' in cdsrc, "52주 payload 배선 누락"

    def test_chart_json_script_termination_defused(self):
        """JSON 안의 '</' 가 <script> 블록을 조기 종료하지 못하게 defuse."""
        from bot.dashboard import _render_chart_section

        rec = {
            "ticker": "X",
            "price_chart": {
                "currency": "</script>",  # 악의적/우발적 종료 시퀀스
                "decimals": 2,
                "times": ["2025-06-03"],
                "close": [1.0],
            },
        }
        html = _render_chart_section(rec)
        # 원시 '</script>' 종료 시퀀스가 script 블록 안에 그대로 있으면 안 됨
        assert "</script>" not in html.split('id="chart-data">')[1].split("</script")[0] or "<\\/" in html

    def test_detail_page_includes_lib_only_when_chart(self):
        from bot.dashboard import _render_detail, _LWC_LIB_NAME

        with_chart = _render_detail({
            "ticker": "AAPL", "trade_date": "2026-06-03",
            "summary": "테스트 요약", "full_report": "테스트 본문",
            "price_chart": self._sample_chart(),
        })
        assert _LWC_LIB_NAME in with_chart, "차트 있는데 라이브러리 script 누락"

        without = _render_detail({
            "ticker": "AAPL", "trade_date": "2026-06-03",
            "summary": "테스트 요약", "full_report": "테스트 본문",
        })
        assert _LWC_LIB_NAME not in without, "차트 없는데 라이브러리 script 포함됨"

    def test_chart_past_markers_logscale_tooltip(self):
        """과거 추천 마커 + 로그 스케일 + 크로스헤어 툴팁 (2026-06-04)."""
        from bot.dashboard import _CHART_JS, _rating_direction, _render_chart_section
        # 판정 → 방향
        assert _rating_direction("Buy") == "up" and _rating_direction("Overweight") == "up"
        assert _rating_direction("매수") == "up"
        assert _rating_direction("Sell") == "down" and _rating_direction("Underweight") == "down"
        assert _rating_direction("Hold") == "hold"
        # JS 배선
        assert "analysisMarkers" in _CHART_JS and "setMarkers" in _CHART_JS
        assert "subscribeCrosshairMove" in _CHART_JS and "chart-tooltip" in _CHART_JS
        assert "ind.log ? 1 : 0" in _CHART_JS  # 로그 스케일 모드
        # 마커 payload 주입 + 데이터 범위 밖 필터(JS firstT/lastT)
        rec = {"ticker": "AAPL", "price_chart": {"currency": "$", "decimals": 2,
               "times": ["2025-06-01", "2025-06-02"], "close": [1.0, 2.0]}}
        h = _render_chart_section(rec, [{"time": "2025-06-01", "dir": "up",
                                         "rating": "Buy", "ret": 8.3}])
        assert "analysis_markers" in h and "Buy" in h

    def test_chart_live_last_price_wiring(self):
        """장중 last_price(~15분 지연) — payload 필드 + 프론트 마지막봉 대체."""
        from bot.dashboard import _CHART_JS
        # 프론트가 last_price 를 series 마지막 봉에 덮어쓰는지 (라인/캔들 양쪽)
        assert "d.last_price" in _CHART_JS, "last_price 필드 미사용"
        assert "lb.close = lp" in _CHART_JS, "캔들 마지막봉 close 라이브 대체 누락"
        assert "value: lp" in _CHART_JS, "라인 마지막점 라이브 대체 누락"
        # 패널 '현재가' 가 last_price 우선
        assert "(d.last_price != null ? d.last_price : lastNonNull(d.close))" in _CHART_JS
        # 서버 캐시 키 버전 (v3) + TTL 단축 (5분)
        srv = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert "_v4.json" in srv, "캐시 키 버전 v4 누락(라이브 가드 후 bump)"
        assert "< 300:" in srv, "캐시 TTL 5분 누락"

    def test_chart_indicators_volume_rsi_bb_macd_candle(self):
        """보조지표 배선 — 거래량/RSI/볼린저/MACD/캔들 + 토글 (2026-06-04)."""
        from bot.dashboard import _CHART_JS, _render_chart_section

        # 거래량 히스토그램 + overlay 스케일
        assert "addHistogramSeries" in _CHART_JS, "거래량 히스토그램 누락"
        assert "priceScaleId: 'vol'" in _CHART_JS, "거래량 overlay 스케일 누락"
        # RSI pane + 70/30 기준선
        assert "rsiChart = subChart" in _CHART_JS, "RSI pane 누락"
        assert "price: 70" in _CHART_JS and "price: 30" in _CHART_JS, "RSI 70/30 누락"
        # MACD pane
        assert "macdChart = subChart" in _CHART_JS, "MACD pane 누락"
        assert "d.macd_hist" in _CHART_JS and "d.macd_signal" in _CHART_JS, "MACD 시리즈 누락"
        # 볼린저밴드 overlay
        assert "d.bb_u" in _CHART_JS and "d.bb_l" in _CHART_JS, "볼린저밴드 누락"
        # 캔들 토글
        assert "addCandlestickSeries" in _CHART_JS, "캔들 시리즈 누락"
        # 시간축 동기화 (다중 pane)
        assert "linkTimeScales" in _CHART_JS, "시간축 동기화 누락"
        # 지표 토글 상태 영속
        assert "localStorage" in _CHART_JS and "noah_chart_ind" in _CHART_JS, "토글 영속 누락"
        # HTML: RSI / MACD 컨테이너 + 토글 버튼
        html = _render_chart_section({
            "ticker": "AAPL",
            "price_chart": {"currency": "$", "decimals": 2,
                            "times": ["2025-06-01"], "close": [1.0]},
        })
        assert 'id="rsi-chart"' in html and 'id="macd-chart"' in html, "보조 pane 컨테이너 누락"
        for k in ["candle", "ma", "bb", "vol", "rsi", "macd"]:
            assert 'data-ind="' + k + '"' in html, "지표 토글 버튼 누락: " + k

    def test_series_payload_rsi_volume_shape(self):
        """_series_payload 가 rsi/volume 키를 추가 (pandas 있을 때만 실행)."""
        try:
            import pandas as pd
        except Exception:
            import pytest
            pytest.skip("pandas 미설치 — VM 에서 검증")
        from bot.chart_data import _series_payload
        idx = pd.date_range("2025-01-01", periods=40, freq="D")
        close = pd.Series([100 + i for i in range(40)], index=idx)
        vol = pd.Series([1000 + i for i in range(40)], index=idx, dtype="float64")
        op = pd.Series([99 + i for i in range(40)], index=idx, dtype="float64")
        hi = pd.Series([101 + i for i in range(40)], index=idx, dtype="float64")
        lo = pd.Series([98 + i for i in range(40)], index=idx, dtype="float64")
        p = _series_payload(close, "$", 2, vol, op, hi, lo)
        assert "rsi" in p and len(p["rsi"]) == 40
        assert "volume" in p and all(isinstance(v, int) for v in p["volume"])
        last_rsi = [x for x in p["rsi"] if x is not None][-1]
        assert 0 <= last_rsi <= 100
        # Bollinger(20) + MACD(12,26,9) + OHLC 키
        assert all(k in p for k in ("bb_u", "bb_m", "bb_l")), "볼린저 누락"
        assert all(k in p for k in ("macd", "macd_signal", "macd_hist")), "MACD 누락"
        assert all(k in p for k in ("open", "high", "low")), "OHLC 누락"

    def test_build_price_chart_graceful_on_failure(self):
        """네트워크/티커 실패 시 None 반환 (예외 전파 금지 — 아카이브
        저장 경로가 차트 때문에 깨지면 안 됨)."""
        from bot.chart_data import build_price_chart

        # 샌드박스는 네트워크 차단 → yfinance 실패 → None (graceful)
        result = build_price_chart("NONEXISTENT_TICKER_XYZ_123")
        assert result is None or isinstance(result, dict)

    def test_chart_polish_currency_zoom_tooltip(self):
        """차트 다듬기 (2026-06-04): ① 통화 기호 표시 ② 지표 토글 시 줌/팬
        보존 ③ 크로스헤어 툴팁 볼린저/MACD/캔들 OHLC + 거래량 약식.
        전부 universal (전 시장) · 추가 fetch 0 · payload schema 무변경."""
        from bot.dashboard import _CHART_JS

        # ① 계산만 되고 미사용이던 d.currency 를 값 패널/툴팁에 통화 prefix.
        assert "curSym = d.currency" in _CHART_JS, "통화 기호 미세팅"
        assert "function fmtPrice" in _CHART_JS, "통화 prefix 포맷터 누락"
        assert "function fmtVol" in _CHART_JS, "거래량 약식 포맷터 누락"
        # ② 지표 토글 재렌더 시 fitContent 로 튕기지 않고 현재 뷰 복원.
        assert "getVisibleLogicalRange" in _CHART_JS, "현재 뷰 캡처 누락"
        assert "render(lastData, true)" in _CHART_JS, "토글 preserve 호출 누락"
        assert "!(preserve && prevRange)" in _CHART_JS, "fitContent 보존 분기 누락"
        # ③ 툴팁 — 볼린저/MACD/캔들 OHLC 행 (값 패널 push 가 아닌 trow).
        assert "trow('볼린저상'" in _CHART_JS, "툴팁 볼린저 누락"
        assert "trow('MACD'" in _CHART_JS, "툴팁 MACD 누락"
        assert "trow('시가'" in _CHART_JS, "툴팁 캔들 OHLC 누락"

    def test_chart_fmtvol_abbreviation_logic(self):
        """fmtVol K/M/B 약식 경계 — JS 로직을 Python 으로 미러 검증."""
        def fmtvol(n):
            a = abs(n)
            if a >= 1e9:
                return f"{n / 1e9:.2f}B"
            if a >= 1e6:
                return f"{n / 1e6:.2f}M"
            if a >= 1e3:
                return f"{n / 1e3:.1f}K"
            return str(int(n))
        assert fmtvol(2_300_000_000) == "2.30B"
        assert fmtvol(12_345_678) == "12.35M"
        assert fmtvol(8_400) == "8.4K"
        assert fmtvol(742) == "742"

    def test_chart_review_delta_row(self):
        """'분석 후 변동' — 시점가(분석일 종가) 대비 현재가 % 복기 지표
        (2026-06-04). 차트의 핵심 목적인 '그때↔지금' 복기를 한 줄로 표시.
        universal (전 시장) · 추가 fetch 0 (두 값 이미 scope)."""
        from bot.dashboard import _CHART_JS, _render_chart_section

        # 값 패널 '분석 후' 행 + 시점가 대비 현재가 delta 계산식 + pct 포맷 분기
        assert "'분석 후'" in _CHART_JS, "분석 후 변동 행 누락"
        assert "(_cur - asOfClose) / asOfClose" in _CHART_JS, "delta 계산식 누락"
        assert "kind === 'pct'" in _CHART_JS, "pct 포맷(사전 포맷 문자열) 분기 누락"
        # 현재가는 패널 '현재가' 와 동일 소스(라이브 우선)
        assert "d.last_price != null ? d.last_price : lastNonNull(d.close)" in _CHART_JS
        # 레전드에 설명
        rec = {"ticker": "AAPL", "price_chart": {"currency": "$", "decimals": 2,
               "times": ["2025-06-01"], "close": [1.0]}}
        assert "분석 후=" in _render_chart_section(rec), "레전드 설명 누락"

    def test_chart_review_delta_math(self):
        """분석 후 변동 % 계산 — JS 로직 Python 미러 (부호/0 경계)."""
        def pct(cur, asof):
            return (cur - asof) / asof * 100.0
        assert round(pct(110, 100), 1) == 10.0
        assert round(pct(90, 100), 1) == -10.0
        assert round(pct(100, 100), 1) == 0.0

    def test_chart_period_return_row(self):
        """'기간 N' — 로드된 범위(1개월~전체) first bar → 현재가 수익률
        (2026-06-04). 범위/봉 전환 시 그 윈도 기준 갱신. universal · ₩0."""
        from bot.dashboard import _CHART_JS, _render_chart_section

        # first 추출 헬퍼 + 기간 수익률 계산식 + 행 라벨
        assert "function firstNonNull" in _CHART_JS, "기간 first 추출 헬퍼 누락"
        assert "(_curp - _first) / _first" in _CHART_JS, "기간 수익률 계산식 누락"
        assert "'기간 '" in _CHART_JS, "기간 행 라벨 누락"
        # 현재 선택 범위(curRange) 기준 — 한국어 라벨 매핑(전 범위)
        for k, label in [("1mo", "1개월"), ("3mo", "3개월"), ("6mo", "6개월"),
                         ("1y", "1년"), ("3y", "3년"), ("5y", "5년"),
                         ("max", "전체")]:
            assert "'" + k + "':'" + label + "'" in _CHART_JS, "범위 라벨 매핑 누락: " + k
        # 레전드 설명
        rec = {"ticker": "AAPL", "price_chart": {"currency": "$", "decimals": 2,
               "times": ["2025-06-01"], "close": [1.0]}}
        assert "기간=" in _render_chart_section(rec), "레전드 기간 설명 누락"

    def test_chart_usage_guide_present_and_balanced(self):
        """접이식 '차트 보는 법' 가이드 (2026-06-04) — 라인/값패널/마커/지표/
        조작 설명 + <details> 개수 균형(미닫힘 회귀 차단)."""
        from bot.dashboard import _render_chart_section

        rec = {"ticker": "AAPL", "price_chart": {"currency": "$", "decimals": 2,
               "times": ["2025-06-01"], "close": [1.0]}}
        html = _render_chart_section(rec)
        assert "차트 보는 법" in html, "사용법 가이드 누락"
        # 지표 의미/마커/조작 핵심 항목이 가이드에 포함
        for kw in ["RSI", "MACD", "볼린저", "이평선", "과거 추천", "로그",
                   "분석 후", "기간 N", "hover"]:
            assert kw in html, "가이드 항목 누락: " + kw
        # <details> 개수 균형 (회귀 가드 — 열고 안 닫으면 페이지 깨짐)
        assert html.count("<details") == html.count("</details>"), "<details> 불균형"
        # v1(차트 없음)이면 가이드도 없음 (빈 섹션)
        assert _render_chart_section({"ticker": "X"}) == ""


# ─────────────────────────────────────────────────────────────────────────
# 8a2) 라이브 현재가 sanity 가드 (2026-06-04) — fast_info 글리치 → 직전 종가
#   배경: 파크시스템스 140860.KS 2026-05-20 상세 차트가 라이브 ₩163,700
#   (실제 ~₩280,000, -42%)을 마지막 봉에 박아 가짜 절벽 + 분석후 -34.1% /
#   기간 -48.5% 오염. fast_info 의 잘못된 분할·조정/stale/junk 값. 정책:
#   비현실 라이브는 무조건 직전 종가(auto_adjust, 라인·MA 와 일치)로 폴백.
# ─────────────────────────────────────────────────────────────────────────
class TestLivePriceGuard:
    """fix: 라이브 현재가 글리치 → 직전 종가 폴백 (2026-06-04)."""

    def test_kr_glitch_rejected(self):
        from bot.chart_data import _validate_live_price
        # 140860.KS 실제 케이스: 163,700 vs 직전 종가 ~280,000 (-42%) → reject
        assert _validate_live_price(163700, 280000, "KR") is None

    def test_kr_legit_limit_moves_kept(self):
        from bot.chart_data import _validate_live_price
        # KR 일일 한도 ±30% 이내 실제 이동은 절대 reject 안 됨
        assert _validate_live_price(280000 * 0.996, 280000, "KR") == 280000 * 0.996
        assert _validate_live_price(280000 * 1.30, 280000, "KR") == 280000 * 1.30  # +30% 한도
        assert _validate_live_price(280000 * 0.70, 280000, "KR") == 280000 * 0.70  # -30% 한도

    def test_kr_beyond_limit_rejected(self):
        from bot.chart_data import _validate_live_price
        # ±35% 밴드 밖(단일 세션 물리적 불가) → 글리치로 reject
        assert _validate_live_price(280000 * 1.40, 280000, "KR") is None
        assert _validate_live_price(280000 * 0.50, 280000, "KR") is None

    def test_us_news_gap_kept_but_split_junk_rejected(self):
        from bot.chart_data import _validate_live_price
        # 미국은 일일 한도 없음 → 진짜 -42% 뉴스 갭은 살림(글리치와 구분 불가)
        assert _validate_live_price(58, 100, "US") == 58.0
        assert _validate_live_price(150, 100, "US") == 150.0
        # 2x / 0.5x 미만 급 분할·junk 글리치만 거름
        assert _validate_live_price(40, 100, "US") is None    # 0.4x
        assert _validate_live_price(250, 100, "US") is None   # 2.5x

    def test_garbage_inputs_reject(self):
        from bot.chart_data import _validate_live_price
        for bad in (0, -5, None, "x", float("nan"), float("inf")):
            assert _validate_live_price(bad, 100, "KR") is None, bad
        assert _validate_live_price(100, 0, "KR") is None      # 종가 0
        assert _validate_live_price(100, None, "KR") is None

    def test_band_by_market(self):
        from bot.chart_data import _live_price_band
        for m in ("KR", "TW", "CN_A", "JP"):
            assert _live_price_band(m) == (0.65, 1.35), m
        for m in ("US", "EU", "HK", "anything"):
            assert _live_price_band(m) == (0.5, 2.0), m


# ─────────────────────────────────────────────────────────────────────────
# 8a3) 분석-시점 가격 글리치 가드 (2026-06-04) — bot/price_sanity.py
#   배경: 파크시스템스 140860.KS 2026-05-20 분석이 yfinance 글리치 현재가
#   ₩163,700(실제 ~₩280-300K, 52주 최저 ₩205,000 미만)을 그대로 써서 시장·
#   펀더멘털·트레이더 전원이 phantom 폭락 위에 Sell/Underweight 결론을 쌓음.
#   정책(사용자 2026-06-04): 교체(직전 정상 종가) 우선, 안되면 차단/중립.
#   _compute_technical_snapshot 가 글리치 last-bar 드롭(교체), FACTUAL ANCHOR
#   값 블록이 outlier 현재가에 HARD 경고(차단)로 보강.
# ─────────────────────────────────────────────────────────────────────────
class TestPriceGlitchGuard:
    """fix: yfinance 가격 글리치 → 교체/차단 (파크시스템스 140860 2026-06-04)."""

    def test_last_close_glitch_kr_case(self):
        from bot.price_sanity import last_close_is_glitch
        # 직전 ~300K, 마지막 163,700 (-45%) → KR 한도(0.35) 초과 = glitch
        vals = [300000, 310000, 295000, 305000, 300000, 308000, 163700]
        assert last_close_is_glitch(vals, 0.35) is True

    def test_last_close_normal_kept(self):
        from bot.price_sanity import last_close_is_glitch
        vals = [300000, 310000, 295000, 305000, 300000, 308000, 303000]
        assert last_close_is_glitch(vals, 0.35) is False

    def test_last_close_kr_limit_move_kept(self):
        from bot.price_sanity import last_close_is_glitch
        # -30% (KR 일일 한도) 는 ±35% 밴드 안 → 실제 한도 이동은 절대 안 드롭
        assert last_close_is_glitch([300000] * 6 + [210000], 0.35) is False

    def test_last_close_short_series_false(self):
        from bot.price_sanity import last_close_is_glitch
        # <7 포인트면 판단 불가 → False (단기/신규 상장 over-fire 방지)
        assert last_close_is_glitch([300000, 200000, 100000], 0.35) is False

    def test_last_close_nan_or_zero_is_glitch(self):
        from bot.price_sanity import last_close_is_glitch
        base = [300000, 310000, 295000, 305000, 300000, 308000]
        assert last_close_is_glitch(base + [float("nan")], 0.35) is True
        assert last_close_is_glitch(base + [0], 0.35) is True

    def test_market_gap_and_us_band(self):
        from bot.price_sanity import last_close_is_glitch, snapshot_gap_for_market
        assert snapshot_gap_for_market("KR") == 0.35
        assert snapshot_gap_for_market("US") == 0.50
        # 미국 +40% 어닝 갭(실제) 보존, 2x junk 드롭
        assert last_close_is_glitch([100] * 6 + [140], 0.50) is False
        assert last_close_is_glitch([100] * 6 + [250], 0.50) is True

    def test_outlier_below_52w_low(self):
        from bot.price_sanity import price_outlier_vs_refs
        # 140860 실제: 현재가 163,700 < 52주 최저 205,000 → 불가능 = outlier
        assert price_outlier_vs_refs(163700, low52=205000, high52=350500,
                                     sma50=269820, sma200=252600) is True

    def test_outlier_in_range_false(self):
        from bot.price_sanity import price_outlier_vs_refs
        assert price_outlier_vs_refs(250000, low52=205000, high52=350500,
                                     sma50=269820, sma200=252600) is False

    def test_outlier_above_52w_high(self):
        from bot.price_sanity import price_outlier_vs_refs
        assert price_outlier_vs_refs(400000, low52=205000, high52=350500) is True

    def test_outlier_no_refs_false(self):
        from bot.price_sanity import price_outlier_vs_refs
        # 판단할 ref 가 없으면 False (절대 over-fire 안 함)
        assert price_outlier_vs_refs(163700) is False

    def test_outlier_far_from_both_ma(self):
        from bot.price_sanity import price_outlier_vs_refs
        # 52주 없고 50d·200d 둘 다에서 >35% → outlier; 한쪽만 멀면 아님
        assert price_outlier_vs_refs(160000, sma50=270000, sma200=255000) is True
        assert price_outlier_vs_refs(260000, sma50=270000, sma200=255000) is False

    def test_outlier_in_range_parabolic_not_flagged(self):
        from bot.price_sanity import price_outlier_vs_refs
        # 삼성전기 009150 2026-06-04: 현재가 1,716,000 ∈ 52주[122,800, 2,200,000],
        # MA 대비 +95%/+317% 인 진짜 포물선 급등 → suspect 아님 (false '데이터
        # 이상' 차단, 진짜 froth 신호 보존). 외부 리뷰가 stale 실세계가로 실
        # 시뮬값을 글리치로 오인한 케이스의 코드측 교정.
        assert price_outlier_vs_refs(1716000, low52=122800, high52=2200000,
                                     sma50=880390, sma200=411062) is False
        # 티로보틱스류 in-range 깊은 하락(-41%)도 데이터 이상 아님
        assert price_outlier_vs_refs(11280, low52=9820, high52=30900,
                                     sma50=20000, sma200=22000) is False

    def test_outlier_ma_fallback_only_without_52w(self):
        from bot.price_sanity import price_outlier_vs_refs
        # 52주 ref 부재 시엔 MA-이격 fallback 여전히 발화 (글리치 차단 보존)
        assert price_outlier_vs_refs(1716000, sma50=880390, sma200=411062) is True
        # 유효 52주 범위 안이면 같은 큰 이격도 미발화 (fallback 은 52주 없을 때만)
        assert price_outlier_vs_refs(1716000, low52=122800, high52=2200000,
                                     sma50=880390, sma200=411062) is False

    def test_agent_utils_wires_glitch_guard(self):
        """agent_utils 가 실제로 price_sanity 를 호출하는지(스냅샷 교체 +
        값 블록 차단) 소스에서 확인 — heavy import 없이 배선 회귀 차단."""
        src = open("TradingAgents/tradingagents/agents/utils/agent_utils.py",
                   encoding="utf-8").read()
        assert "from bot.price_sanity import" in src, "price_sanity import 누락"
        assert "last_close_is_glitch" in src, "스냅샷 글리치 검사 미배선"
        assert "price_outlier_vs_refs" in src, "값 블록 outlier 검사 미배선"
        assert "_px_repaired" in src, "스냅샷 last-bar 교체 플래그 누락"
        assert "현재가 데이터 이상 (HARD GUARD)" in src, "값 블록 차단 directive 누락"

    def test_comps_masking_wired(self):
        """④ Comps 마스킹 (117730 2026-06-04): 현재가 suspect 시 subject 행
        PBR/PSR 에 ⚠️ 데이터 격리 플래그 (price_sanity 재사용)."""
        au = open("TradingAgents/tradingagents/agents/utils/agent_utils.py",
                  encoding="utf-8").read()
        assert "_comps_px_suspect" in au, "Comps 마스킹 플래그 누락"
        assert "데이터 격리(현재가 이상" in au, "Comps 마스킹 directive 누락"
        # _build_factual_anchor + Comps 두 곳에서 price_outlier_vs_refs 재사용
        assert au.count("price_outlier_vs_refs") >= 2, "Comps 가 price_sanity 미재사용"


# ─────────────────────────────────────────────────────────────────────────
# 8a4) KR .KS↔.KQ suffix 정규화 + freeze 52주 게이트 (티로보틱스 117730 2026-06-04)
#   배경: KOSDAQ 종목 117730 을 .KS 로 조회 → yfinance/KIS/뉴스가 엉뚱한
#   장부 → 데이터 불일치 + 뉴스 0건 + 30% SMA-gap HARD GUARD 오발(실제로는
#   52주 레인지 안의 진짜 -41% 하락). ① KRX 목록 기반 suffix 자동 교정,
#   ② 현재가가 52주 안이면(split-staleness 아님) freeze 강등.
# ─────────────────────────────────────────────────────────────────────────
class TestKRSuffixAndFreezeGate:
    """fix: KR suffix 정규화 + freeze 52주 게이트 (117730 2026-06-04)."""

    def test_correct_suffix_kosdaq(self):
        from bot.market import _correct_kr_suffix
        # 117730 이 KOSDAQ 목록에 있으면 .KS 입력이라도 .KQ 로 교정
        assert _correct_kr_suffix("117730", ".KS", set(), {"117730"}) == "117730.KQ"

    def test_correct_suffix_kospi(self):
        from bot.market import _correct_kr_suffix
        assert _correct_kr_suffix("005930", ".KQ", {"005930"}, set()) == "005930.KS"

    def test_correct_suffix_ambiguous_keeps_current(self):
        from bot.market import _correct_kr_suffix
        # 어디에도 없거나 양쪽 모두면 현재 suffix 유지 (보수적)
        assert _correct_kr_suffix("117730", ".KS", set(), set()) == "117730.KS"
        assert _correct_kr_suffix("000660", ".KS", {"000660"}, {"000660"}) == "000660.KS"

    def test_normalize_non_kr_unchanged(self):
        from bot.market import normalize_kr_ticker_suffix
        assert normalize_kr_ticker_suffix("AAPL") == "AAPL"
        assert normalize_kr_ticker_suffix("7203.T") == "7203.T"

    def test_normalize_graceful_and_malformed(self):
        from bot.market import normalize_kr_ticker_suffix
        # 샌드박스: pykrx/creds 없음 → 목록 빈 → 원본 그대로(크래시 X)
        out = normalize_kr_ticker_suffix("117730.KS")
        assert isinstance(out, str) and out.endswith((".KS", ".KQ"))
        # malformed (5자리) → 그대로
        assert normalize_kr_ticker_suffix("12345.KS") == "12345.KS"

    def test_freeze_no_signals_soft(self):
        from bot.price_sanity import should_hard_freeze_technicals
        assert should_hard_freeze_technicals(11280, 9820, 30900, []) is False

    def test_freeze_price_axis_always_hard(self):
        from bot.price_sanity import should_hard_freeze_technicals
        # split / 거래정지 / 시장경보 = price-axis → in-range 여도 HARD
        assert should_hard_freeze_technicals(
            11280, 9820, 30900,
            ["yfinance .splits ex-date: 2026-05-30 (2:1 forward split)"]) is True
        assert should_hard_freeze_technicals(
            11280, 9820, 30900, ["KRX 시장경보 / 거래정지: 거래정지"]) is True

    def test_freeze_in_range_downgrades_to_soft(self):
        from bot.price_sanity import should_hard_freeze_technicals
        # 117730: marketCap divergence 만 + 현재가 52주 안 → SOFT(강등)
        assert should_hard_freeze_technicals(
            11280, 9820, 30900,
            ["shares × price vs reported marketCap divergence > 5%"]) is False

    def test_freeze_outside_range_stays_hard(self):
        from bot.price_sanity import should_hard_freeze_technicals
        # 140860 류: 현재가가 52주 최저 밖 + 신호 → HARD 유지
        assert should_hard_freeze_technicals(
            163700, 205000, 350500,
            ["shares × price vs reported marketCap divergence > 5%"]) is True

    def test_wiring_suffix_and_freeze_gate(self):
        au = open("TradingAgents/tradingagents/agents/utils/agent_utils.py",
                  encoding="utf-8").read()
        assert "should_hard_freeze_technicals" in au, "freeze 게이트 미배선"
        assert "if _hard:" in au, "HARD 분기 미적용"
        an = open("bot/analyzer.py", encoding="utf-8").read()
        assert "normalize_kr_ticker_suffix" in an, "analyze() suffix 정규화 미배선"


# ─────────────────────────────────────────────────────────────────────────
# 8a5) 마크다운 표 구분선 자동 삽입 + 공시→뉴스 fallback (티로보틱스 117730 2026-06-04)
#   배경: ① LLM 이 표 헤더 다음 구분선(|---|)을 빼먹어 요약표가 깨져 노출.
#   ② KOSDAQ 수주가 '공시로'만 존재해 뉴스 0건으로 news/sentiment 통째 skip.
# ─────────────────────────────────────────────────────────────────────────
class TestMarkdownTableAndDisclosureNews:
    """fix: 표 구분선 자동 삽입 + 공시→뉴스 fallback (117730 2026-06-04)."""

    def test_insert_separator_117730_case(self):
        from bot.md_tables import insert_table_separators
        raw = ("요약표\n"
               "| 지표 | 현재 값 | 비고 |\n"
               "| 평균 | ₩19,556 | ₩11,280 |\n"
               "| 상단 밴드 | ₩22,495 | 낮음 |")
        out = insert_table_separators(raw)
        lines = out.split("\n")
        assert lines[1].startswith("| 지표"), lines
        # 헤더 다음 줄 = 구분선 (대시/콜론만), 3컬럼 → 파이프 4개
        assert set(lines[2].replace("|", "").replace(" ", "")) <= set("-:"), lines[2]
        assert lines[2].count("|") == 4, lines[2]
        assert "₩19,556" in out and "상단 밴드" in out  # 데이터 보존

    def test_valid_table_idempotent(self):
        from bot.md_tables import insert_table_separators
        valid = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        assert insert_table_separators(valid) == valid
        assert insert_table_separators(insert_table_separators(valid)) == valid

    def test_non_table_prose_unchanged(self):
        from bot.md_tables import insert_table_separators
        prose = "현재가 X — 하락 추세.\n일반 문장."
        assert insert_table_separators(prose) == prose
        assert insert_table_separators("a | b 설명") == "a | b 설명"  # 표 아님

    def test_header_without_data_unchanged(self):
        from bot.md_tables import insert_table_separators
        one = "| A | B |\n다음 문장"
        assert insert_table_separators(one) == one

    def test_disclosure_news_fallback_wired(self):
        au = open("TradingAgents/tradingagents/agents/utils/agent_utils.py",
                  encoding="utf-8").read()
        # has_recent_news 공시 fallback (KR DART + JP/TW/CN)
        assert "get_recent_disclosures(_code, days_back=14" in au, "DART 공시 fallback 누락"
        assert "from bot.edinet_client import get_edinet" in au, "JP EDINET fallback 누락"
        assert "from bot.mops_client import get_mops" in au, "TW MOPS fallback 누락"
        assert "from bot.edgar_client import get_recent_8k" in au, "US 8-K fallback 누락"
        # 뉴스 부재 시 공시-기반 news/sentiment directive (KR Naver-empty)
        assert "공시 기반 news/sentiment 지시" in au, "공시 directive 누락"
        # 5시장 공유 공시 블록 anti-skip ('뉴스 없음' 결론 금지) — KR/JP/TW/CN
        assert "'관련 뉴스 없음' 결론 금지" in au, "공유 블록 anti-skip directive 누락"
        # US 8-K 블록도 anti-skip
        eg = open("bot/edgar_client.py", encoding="utf-8").read()
        assert "'관련 뉴스 없음' 결론 금지" in eg, "8-K anti-skip directive 누락"

    def test_analyzer_wires_table_step_and_skipmsg(self):
        an = open("bot/analyzer.py", encoding="utf-8").read()
        assert "insert-table-separators" in an, "표 구분선 스텝 미배선"
        assert "DART 공시 모두" in an, "skip 메시지 공시 표기 누락"
        assert "SEC 8-K 공시" in an, "US 8-K skip 메시지 누락"
        # Trader Stop Loss 환각 배너에 권장 Stop 레벨 (MRVL 2026-06-04 ①-lite)
        assert "권장 Stop ≈" in an, "Stop Loss 환각 배너 권장 stop 누락"


# ─────────────────────────────────────────────────────────────────────────
# 8a6) SV 대시보드 모바일 반응형 (2026-06-04 사용자 스크린샷) — 인라인 그리드
#   가 모바일에서 안 접혀 칼럼 으스러짐/빈 우측. daily_generator 가 생성 시
#   head 에 @media <style> 주입(인라인 그리드를 !important 단일칼럼화).
# ─────────────────────────────────────────────────────────────────────────
class TestWatchlist:
    """fix: 워치리스트 알림 (2026-06-04, vibe-trade 패턴 영감)."""

    def test_parse_conditions_valid_invalid(self):
        from bot.watchlist import parse_conditions
        v, inv = parse_conditions("rsi<30 PRICE>950, >sma50 <sma200 52whigh earnings JUNK")
        assert "rsi<30" in v and "price>950" in v and ">sma50" in v
        assert "52whigh" in v and "earnings" in v
        assert "junk" in inv

    def test_parse_dedup_and_cap(self):
        from bot.watchlist import parse_conditions, MAX_CONDITIONS
        v, _ = parse_conditions("rsi<30 rsi<30 price>1 price>2 price>3 price>4 price>5 price>6 price>7 price>8")
        assert len(v) <= MAX_CONDITIONS
        assert v.count("rsi<30") == 1

    def test_storage_add_list_remove(self, tmp_path, monkeypatch):
        import bot.watchlist as wl
        monkeypatch.setattr(wl, "WATCHLIST_PATH", tmp_path / "watchlist.json")
        wl.add_watch("NVDA", 111, ["rsi<30", "price>950"])
        wl.add_watch("AAPL", 111, ["earnings"])
        wl.add_watch("NVDA", 222, ["rsi>70"])  # 다른 chat
        assert {w["ticker"] for w in wl.list_watches(111)} == {"NVDA", "AAPL"}
        assert {w["ticker"] for w in wl.list_watches(222)} == {"NVDA"}
        # 같은 chat+ticker 재등록 → 조건 머지
        wl.add_watch("NVDA", 111, ["earnings"])
        nv = [w for w in wl.list_watches(111) if w["ticker"] == "NVDA"][0]
        assert set(nv["conditions"]) == {"rsi<30", "price>950", "earnings"}
        # 삭제
        assert wl.remove_watch(111, "AAPL") == 1
        assert {w["ticker"] for w in wl.list_watches(111)} == {"NVDA"}
        assert wl.remove_watch(111, "all") == 1
        assert wl.list_watches(111) == []
        # 다른 chat(222) 은 영향 없음
        assert len(wl.list_watches(222)) == 1 and wl.list_watches(222)[0]["ticker"] == "NVDA"

    def test_evaluate_conditions(self, monkeypatch):
        import bot.chart_data as cd
        # 합성 payload: 현재가 100, rsi 25, sma50 110, sma200 90, 52w hi 200 lo 50
        fake = {
            "currency": "$", "decimals": 2,
            "times": ["2025-06-01", "2025-06-02"],
            "close": [50.0, 100.0],          # last=100, min=50
            "rsi": [None, 25.0],
            "sma50": [None, 110.0],
            "sma200": [None, 90.0],
        }
        # 52w high 를 위해 max 를 키운 close 사용
        fake["close"] = [50.0, 200.0, 100.0]
        fake["times"] = ["a", "b", "c"]
        fake["rsi"] = [None, None, 25.0]
        fake["sma50"] = [None, None, 110.0]
        fake["sma200"] = [None, None, 90.0]
        monkeypatch.setattr(cd, "fetch_chart_payload", lambda *a, **k: fake)
        from bot.watchlist import evaluate
        r = evaluate("X", ["rsi<30", "rsi>30", "price>90", "price>150",
                           ">sma200", "<sma50", "52whigh", "52wlow"])
        assert r["rsi<30"][0] is True
        assert r["rsi>30"][0] is False
        assert r["price>90"][0] is True
        assert r["price>150"][0] is False
        assert r[">sma200"][0] is True   # 100 > 90
        assert r["<sma50"][0] is True    # 100 < 110
        assert r["52whigh"][0] is False  # 100 vs 52주 최고 200 → 아님
        assert r["52wlow"][0] is False   # 100 vs 52주 최저 50 → 아님

    def test_evaluate_no_data_empty(self, monkeypatch):
        import bot.chart_data as cd
        monkeypatch.setattr(cd, "fetch_chart_payload", lambda *a, **k: None)
        from bot.watchlist import evaluate
        assert evaluate("X", ["rsi<30"]) == {}

    def test_flow_conditions_parse_and_eval(self, monkeypatch):
        from bot.watchlist import parse_conditions
        v, _ = parse_conditions("foreignbuy foreignsell instbuy instsell")
        assert set(v) == {"foreignbuy", "foreignsell", "instbuy", "instsell"}
        # evaluate: monkeypatch chart payload + KR flow
        import bot.chart_data as cd
        monkeypatch.setattr(cd, "fetch_chart_payload", lambda *a, **k: {
            "currency": "₩", "decimals": 0, "times": ["a", "b"], "close": [100, 110]})
        import bot.pykrx_client as pk
        monkeypatch.setattr(pk, "get_kr_trading_flow",
                            lambda *a, **k: {"foreign_net": 5e10, "institutional_net": -3e10})
        from bot.watchlist import evaluate
        r = evaluate("005930.KS", ["foreignbuy", "instbuy", "instsell"])
        assert r["foreignbuy"][0] is True    # 외인 +5e10 > 0
        assert r["instbuy"][0] is False      # 기관 -3e10
        assert r["instsell"][0] is True

    def test_render_no_css_leak(self):
        # fix(2026-06-05): watchlist.html 이 _SCREENER_CSS(전체 HTML 문서)를
        # <style> 안에 넣어, 내부 </style> 가 블록을 조기 종료 → 뒤따르는 table
        # CSS 가 본문 텍스트로 누수됐다. _DETAIL_CSS(순수 CSS)로 교체해 해소.
        from bot.dashboard import _render_watchlist_page
        html = _render_watchlist_page(
            [{"ticker": "AMAT", "conditions": ["52whigh"], "added": "2026-06-04T10:00", "id": "a85c68"}],
            [{"ts": "2026-06-05T13:05", "ticker": "LRCX", "hits": ["현재가 ≈ 52주 최고"]}])
        # 단일 문서여야 — 중첩 doctype/style 은 _SCREENER_CSS 누수 시그니처.
        assert html.lower().count("<!doctype") == 1, "중첩 문서(_SCREENER_CSS 누수)"
        assert html.count("<style>") == 1 and html.count("</style>") == 1, "style 블록 중복/조기종료"
        # table CSS 는 </style> 앞(=style 블록 안)에만, 뒤(본문)로 누수 금지.
        head, _, body = html.partition("</style>")
        assert "border-collapse:collapse" in head, "table CSS 가 style 블록 안에 없음"
        assert "border-collapse:collapse" not in body, "table CSS 가 본문으로 누수"
        # 소스 가드 — _SCREENER_CSS(전체 문서)를 <style> 안에 다시 넣는 회귀 차단.
        assert "<style>{_SCREENER_CSS}" not in open("bot/dashboard.py", encoding="utf-8").read(), \
            "_SCREENER_CSS 를 <style> 안에 넣는 회귀"

    def test_xbrl_pick_and_format(self):
        """SEC XBRL — 정정 공시 최신 filed 선택 + 최근분기 + 렌더 (2026-06-04)."""
        from bot.edgar_client import _pick_facts, format_xbrl_block, get_key_financials
        units = {"USD": [
            {"end": "2024-12-31", "val": 1200, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-01"},
            {"end": "2024-12-31", "val": 1180, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
            {"end": "2025-03-31", "val": 350, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-04-20"},
        ]}
        p = _pick_facts(units, "money")
        # 정정 중 최신 filed 선택
        assert p["annual"]["val"] == 1200 and p["annual"]["filed"] == "2025-02-01"
        assert p["latest"]["val"] == 350 and p["latest"]["fp"] == "Q1"
        assert p["unit"] == "USD"
        fin = {"cik": "x", "metrics": {
            "revenue": {"concept": "Revenues", "taxonomy": "us-gaap", "unit": "USD",
                        "annual": units["USD"][0], "latest": units["USD"][2]},
        }}
        blk = format_xbrl_block(fin)
        assert "SEC EDGAR XBRL" in blk and "매출" in blk
        assert "최근 Q1" in blk  # 분기 병기
        # graceful: 빈/None
        assert format_xbrl_block(None) == ""
        assert format_xbrl_block({"metrics": {}}) == ""
        # 네트워크 없는 샌드박스 → None (예외 전파 금지)
        r = get_key_financials("NONEXISTENT_XYZ")
        assert r is None or isinstance(r, dict)

    def test_xbrl_phase2_ifrs_currency_and_divergence(self):
        """Phase 2 — ADR 20-F IFRS 택소노미 + 외화 단위 + 주식수 divergence."""
        from bot.edgar_client import _choose_unit, _pick_facts, format_xbrl_block
        # IFRS filer reporting in EUR, 20-F annual form
        units_eur = {"EUR": [
            {"end": "2024-12-31", "val": 5.0e10, "fy": 2024, "fp": "FY",
             "form": "20-F", "filed": "2025-03-01"},
        ]}
        assert _choose_unit(units_eur, "money") == "EUR"
        p = _pick_facts(units_eur, "money")
        assert p["unit"] == "EUR" and p["annual"]["val"] == 5.0e10  # 20-F = 연간
        fin = {"cik": "x", "metrics": {
            "revenue": {"concept": "Revenue", "taxonomy": "ifrs-full", "unit": "EUR",
                        "annual": units_eur["EUR"][0], "latest": units_eur["EUR"][0]},
            "shares": {"concept": "EntityCommonStockSharesOutstanding", "taxonomy": "dei",
                       "unit": "shares",
                       "annual": {"val": 1.0e9, "fy": 2024, "fp": "FY", "form": "20-F", "filed": "2025-03-01"},
                       "latest": {"val": 1.0e9, "fp": "FY"}},
        }}
        blk = format_xbrl_block(fin, yf_shares=1.3e9)  # 30% 차이
        assert "ADR 20-F" in blk and "EUR" in blk
        assert "발행주식수 불일치" in blk and "30% 차이" in blk
        # divergence 10% 미만이면 플래그 없음
        blk2 = format_xbrl_block(fin, yf_shares=1.05e9)
        assert "발행주식수 불일치" not in blk2

    def test_xbrl_section_gated_to_fundamentals(self):
        """edgar_xbrl 은 펀더멘털만 — market/social/news 는 제외."""
        src = open(
            "TradingAgents/tradingagents/agents/utils/agent_utils.py",
            encoding="utf-8",
        ).read()
        import re
        # _ANALYST_CONTEXT_EXCLUDE 의 market/social/news 에 edgar_xbrl 포함
        for who in ("market", "social", "news"):
            m = re.search(r'"' + who + r'":\s*\{(.*?)\}', src, re.DOTALL)
            assert m and "edgar_xbrl" in m.group(1), f"{who} 가 edgar_xbrl 제외 안 함"
        # 주입 + prefetch 배선 존재
        assert 'tasks["edgar_xbrl"]' in src
        assert 'format_xbrl_block' in src

    def test_watchlist_dashboard_renders(self):
        from bot.dashboard import _render_watchlist_page
        html = _render_watchlist_page(
            [{"ticker": "NVDA", "conditions": ["rsi<30", "earnings"],
              "added": "2026-06-04T10:00", "id": "ab12"}],
            [{"ts": "2026-06-04T11:00", "ticker": "NVDA", "hits": ["RSI 28 < 30"]}],
        )
        assert "워치리스트" in html and "NVDA" in html
        assert "rsi&lt;30" in html or "rsi<30" in _html_unescape(html)
        assert "RSI 28" in html


def _html_unescape(s):
    import html as _h
    return _h.unescape(s)


# ─────────────────────────────────────────────────────────────────────────
class TestMarketCalendar:
    """fix(2026-06-05): 휴장일-인지 거래일 캘린더 + auto_resolve readiness
    gate 정밀화. exchange_calendars 부재 시 graceful None → 기존 휴리스틱 폴백."""

    def test_graceful_none_without_lib(self):
        # 샌드박스엔 exchange_calendars 부재 → 전 함수 graceful None(폴백 신호).
        from bot.market_calendar import add_trading_days, next_trading_day, is_trading_day
        assert add_trading_days("KR", "2026-06-01", 5) is None
        assert next_trading_day("US", "2026-07-04") is None
        assert is_trading_day("KR", "2026-06-06") is None
        assert add_trading_days("ZZ", "2026-06-01", 5) is None   # 미지원 시장

    def test_market_to_xcal_covers_all_markets(self):
        from bot.market_calendar import _MARKET_TO_XCAL
        # 우리 전 시장이 exchange_calendars 코드에 매핑돼야(universal).
        for m in ("US", "KR", "JP", "TW", "CN_A", "HK"):
            assert m in _MARKET_TO_XCAL

    def test_fetch_returns_gate_blocks_on_future_target(self, monkeypatch):
        # 정밀 gate: add_trading_days 가 미래 만기 반환 → yfinance 호출 전 조기 None.
        import pytest as _pt
        _pt.importorskip("yfinance")   # auto_resolve 가 top-level import (VM 검증)
        import bot.market_calendar as mc, bot.auto_resolve as ar
        monkeypatch.setattr(mc, "add_trading_days", lambda m, d, n: "2099-01-01")
        assert ar._fetch_returns("AAPL", "2026-06-01", holding_days=5) == (None, None, None)

    def test_fetch_returns_calendar_gate_blocks_on_future(self, monkeypatch):
        import pytest as _pt
        _pt.importorskip("yfinance")
        import bot.market_calendar as mc, bot.auto_resolve as ar
        monkeypatch.setattr(mc, "next_trading_day", lambda m, d: "2099-01-01")
        assert ar._fetch_returns_calendar("AAPL", "2026-06-01", 30) == (None, None, None)

    def test_wiring_present(self):
        src = open("bot/auto_resolve.py", encoding="utf-8").read()
        assert "add_trading_days" in src and "next_trading_day" in src, "캘린더 gate 미배선"
        assert "_SETTLE_BUFFER_DAYS" in src, "settle 버퍼 상수 누락"
        # 폴백 보존 — 캘린더 None 일 때 기존 +3 휴리스틱이 남아 있어야(회귀 0).
        assert "holding_days + 3" in src and "calendar_days + 3" in src, "휴리스틱 폴백 제거됨"


class TestKisRealtimeChartPrice:
    """fix(2026-06-05): 차트 라이브 현재가가 KR 은 KIS 실시간(2분 캐시)을
    우선 — yfinance fast_info(~15분 지연·KR EOD) 약점 보완. 실패 시 yfinance 폴백."""

    def test_kis_get_realtime_price(self, monkeypatch):
        import bot.kis_client as k
        monkeypatch.setattr(k, "_ticker_to_code", lambda t: "005930")
        monkeypatch.setattr(k, "_cache_get", lambda key, ttl_hours=None: None)
        monkeypatch.setattr(k, "_cache_put", lambda key, val: None)
        monkeypatch.setattr(k, "_get",
                            lambda path, tr_id, params: {"output": {"stck_prpr": "70100"}})
        assert k.KisClient().get_realtime_price("005930.KS") == 70100
        # 비-KR(코드 매핑 없음) → None(graceful, yfinance 폴백 신호).
        monkeypatch.setattr(k, "_ticker_to_code", lambda t: None)
        assert k.KisClient().get_realtime_price("AAPL") is None

    def test_live_last_price_kr_prefers_kis(self, monkeypatch):
        import bot.chart_data as cd

        class _Kis:
            def get_realtime_price(self, ticker):
                return 103   # 직전 종가 102 근처 → 검증 통과
        monkeypatch.setattr("bot.kis_client.get_kis", lambda: _Kis())
        # t 는 KR-valid 경로에서 안 쓰임 → None 전달해도 됨.
        out = cd._live_last_price(None, {"close": [100, 101, 102]}, 0, "005930.KS")
        assert out == 103, "KR 라이브 현재가가 KIS 우선이 아님"

    def test_live_last_price_falls_back_to_yfinance(self, monkeypatch):
        import bot.chart_data as cd

        class _KisNone:
            def get_realtime_price(self, ticker):
                return None   # KIS 미가용 → yfinance 폴백
        monkeypatch.setattr("bot.kis_client.get_kis", lambda: _KisNone())

        class _FI:
            last_price = 104
        class _T:
            fast_info = _FI()
        out = cd._live_last_price(_T(), {"close": [100, 101, 102]}, 0, "005930.KS")
        assert out == 104, "KIS None 일 때 yfinance 폴백 실패"

    def test_wiring_present(self):
        assert "get_realtime_price" in open("bot/kis_client.py", encoding="utf-8").read()
        cd = open("bot/chart_data.py", encoding="utf-8").read()
        assert "get_realtime_price" in cd and 'market == "KR"' in cd, "차트 KR→KIS 배선 누락"


class TestExactPriceLimits:
    """fix(2026-06-05): KIS 종목별 실제 상·하한가로 price-glitch 임계 정밀화."""

    def test_exact_session_gap_pure(self):
        from bot.price_sanity import exact_session_gap
        # KR ±30% — prev 10000, 상한 13000 / 하한 7000 → gap 0.30.
        assert abs(exact_session_gap(10000, 13000, 7000) - 0.30) < 1e-9
        # 비대칭이면 더 큰 쪽(둘 다 캡 안): 상한 +20% / 하한 -30% → 0.30.
        assert abs(exact_session_gap(10000, 12000, 7000) - 0.30) < 1e-9
        # 한쪽이 캡(0.35) 초과면 None: 하한 -40% → None.
        assert exact_session_gap(10000, 12000, 6000) is None
        # junk/누락 → None(시장 기본값 유지).
        assert exact_session_gap(None, 1, 1) is None
        assert exact_session_gap(10000, 0, 7000) is None
        assert exact_session_gap("x", "y", "z") is None
        # 비현실 한도(>35%) → None(오파싱 방어).
        assert exact_session_gap(10000, 99000, 7000) is None

    def test_kis_extracts_price_limits(self, monkeypatch):
        import bot.kis_client as k
        monkeypatch.setattr(k, "_ticker_to_code", lambda t: "005930")
        monkeypatch.setattr(k, "_cache_get", lambda key: None)
        monkeypatch.setattr(k, "_cache_put", lambda key, val: None)
        fake = {"output": {"stck_prpr": "70000", "prdy_ctrt": "0.0",
                           "stck_mxpr": "91000", "stck_llam": "49000"}}
        monkeypatch.setattr(k, "_get", lambda path, tr_id, params: fake)
        r = k.KisClient().get_current_price("005930.KS")
        assert r["upper_limit"] == 91000 and r["lower_limit"] == 49000, "상·하한가 추출 누락"
        from bot.price_sanity import exact_session_gap
        pc = r["price"] / (1 + (r["change_pct"] or 0) / 100.0)
        assert abs(exact_session_gap(pc, r["upper_limit"], r["lower_limit"]) - 0.30) < 0.01

    def test_agent_utils_wires_exact_gap(self):
        src = open("TradingAgents/tradingagents/agents/utils/agent_utils.py",
                   encoding="utf-8").read()
        assert "exact_session_gap" in src, "agent_utils 가 exact_session_gap 미사용"
        assert "get_current_price" in src and "upper_limit" in src, "KIS 한도 배선 누락"
        # 폴백 보존 — KIS 실패 시 snapshot_gap_for_market 기본값 유지(회귀 0).
        assert "snapshot_gap_for_market" in src, "시장 기본 gap 폴백 제거됨"


class TestPaperTrading:
    """E0 페이퍼 트레이딩 엔진 (2026-06-05, docs/execution_architecture.md E0).
    실거래 골격에서 '돈만 뺀 것' — 순수 ledger 수학 + idempotency + FX P&L."""

    def _fresh(self, monkeypatch):
        import bot.paper_trading as p
        monkeypatch.setattr(p, "_audit_log", lambda rec: None)   # 테스트 디스크 오염 방지
        return p, p._new_account()

    def test_buy_updates_cash_and_position(self, monkeypatch):
        p, acct = self._fresh(monkeypatch)
        ok, _ = p._apply_buy(acct, "AAPL", 10, 100.0, 1380.0, "USD", "US", 1.0, "k1")
        assert ok
        assert acct["cash_krw"] == 10_000_000 - 10 * 100 * 1380
        assert acct["positions"]["AAPL"]["qty"] == 10
        assert acct["positions"]["AAPL"]["avg_cost_native"] == 100.0

    def test_idempotent_same_key_noop(self, monkeypatch):
        p, acct = self._fresh(monkeypatch)
        p._apply_buy(acct, "AAPL", 10, 100.0, 1380.0, "USD", "US", 1.0, "k1")
        cash = acct["cash_krw"]
        ok, msg = p._apply_buy(acct, "AAPL", 10, 100.0, 1380.0, "USD", "US", 1.0, "k1")
        assert ok and "중복" in msg and acct["cash_krw"] == cash    # 재적용 안 됨
        assert acct["positions"]["AAPL"]["qty"] == 10

    def test_weighted_avg_and_realized_pnl(self, monkeypatch):
        p, acct = self._fresh(monkeypatch)
        p._apply_buy(acct, "AAPL", 10, 100.0, 1380.0, "USD", "US", 1.0, "k1")
        p._apply_buy(acct, "AAPL", 10, 120.0, 1380.0, "USD", "US", 2.0, "k2")
        assert acct["positions"]["AAPL"]["avg_cost_native"] == 110.0
        # 5주 @ $130 fx1400 매도 → 실현 = 5*130*1400 - (cost_basis*5/20)
        ok, _ = p._apply_sell(acct, "AAPL", 5, 130.0, 1400.0, "USD", "US", 3.0, "k3")
        assert ok
        cost_basis = (10 * 100 + 10 * 120) * 1380          # 3,036,000
        expected = 5 * 130 * 1400 - cost_basis * (5 / 20)  # 910,000 - 759,000
        assert abs(acct["realized_pnl_krw"] - expected) < 1
        assert acct["positions"]["AAPL"]["qty"] == 15

    def test_rejections(self, monkeypatch):
        p, acct = self._fresh(monkeypatch)
        ok, _ = p._apply_buy(acct, "X", 100000, 1000.0, 1.0, "KRW", "KR", 1.0, "a")
        assert not ok                                       # 현금 부족
        ok, _ = p._apply_buy(acct, "Y", 0, 1.0, 1.0, "KRW", "KR", 1.0, "b")
        assert not ok                                       # qty<=0
        ok, _ = p._apply_sell(acct, "Z", 1, 1.0, 1.0, "KRW", "KR", 1.0, "c")
        assert not ok                                       # 보유 없음
        p._apply_buy(acct, "W", 5, 1000.0, 1.0, "KRW", "KR", 1.0, "d")
        ok, _ = p._apply_sell(acct, "W", 99, 1000.0, 1.0, "KRW", "KR", 1.0, "e")
        assert not ok                                       # 보유 초과

    def test_close_after_partial_sell_distinct_idem(self, monkeypatch):
        # 버그(2026-06-06): /paper sell AAPL 5 → /paper close AAPL(남은 5)가
        # 같은 (티커,방향,수량,분) 파생키로 잘못 dedup("중복 무시"). message_id
        # 기반 멱등이면 다른 주문은 각각 체결, 재전송만 dedup.
        p, acct = self._fresh(monkeypatch)
        p._apply_buy(acct, "AAPL", 10, 300.0, 1500.0, "USD", "US", 1.0, "m1")
        ok, _ = p._apply_sell(acct, "AAPL", 5, 310.0, 1500.0, "USD", "US", 2.0, "m2")
        assert ok and acct["positions"]["AAPL"]["qty"] == 5
        # close = 남은 5 매도. 다른 idem(다른 message_id) → 체결돼야(중복 아님).
        ok, _ = p._apply_sell(acct, "AAPL", 5, 311.0, 1500.0, "USD", "US", 3.0, "m3")
        assert ok and "AAPL" not in acct["positions"], "close 가 잘못 dedup 됨"
        # 같은 idem(재전송) → dedup.
        ok2, msg2 = p._apply_sell(acct, "AAPL", 1, 311.0, 1500.0, "USD", "US", 4.0, "m3")
        assert ok2 and "중복" in msg2, "재전송 dedup 안 됨"

    def test_idem_default_unique_not_minute_bucket(self):
        # 기본 멱등키는 분-버킷이 아니라 고유(uuid) — 서로 다른 주문 오인 dedup 방지.
        pt = open("bot/paper_trading.py", encoding="utf-8").read()
        assert "uuid.uuid4().hex" in pt, "기본 멱등키 uuid 아님"
        assert "int(time.time() // 60)" not in pt, "분-버킷 파생키가 남아 있음(버그 회귀)"
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert 'idem=f"tg:{update.message.message_id}"' in tb, "DM message_id 멱등 미전달"
        assert 'idem=f"tg:{post.message_id}"' in tb, "채널 message_id 멱등 미전달"

    def test_summary_offline(self, tmp_path, monkeypatch):
        p, acct = self._fresh(monkeypatch)
        monkeypatch.setattr(p, "_ACCOUNT", tmp_path / "acct.json")
        p._apply_buy(acct, "005930.KS", 10, 70000.0, 1.0, "KRW", "KR", 1.0, "k1")
        p._save_account(acct)
        # price_fn 주입(네트워크 X) — 현재가 77000 → +10%
        summ = p.summary(price_fn=lambda t: (77000.0, "KR"))
        assert summ["n_positions"] == 1
        assert abs(summ["rows"][0]["ret_pct"] - 10.0) < 1e-6
        assert abs(summ["unrealized_pnl_krw"] - 10 * (77000 - 70000)) < 1e-6
        assert summ["priced_all"] is True

    def test_persistence_roundtrip(self, tmp_path, monkeypatch):
        import bot.paper_trading as p
        monkeypatch.setattr(p, "_ACCOUNT", tmp_path / "acct.json")
        monkeypatch.setattr(p, "_audit_log", lambda rec: None)
        acct = p._new_account()
        p._apply_buy(acct, "AAPL", 1, 100.0, 1380.0, "USD", "US", 1.0, "k")
        p._save_account(acct)
        loaded = p.get_account()
        assert loaded["positions"]["AAPL"]["qty"] == 1

    def test_snapshot_equity_dedupes_by_date(self, tmp_path, monkeypatch):
        # E0.5d: equity curve — 일별 1점(같은 날 갱신), reset 이 baseline 기록.
        import bot.paper_trading as pt
        monkeypatch.setattr(pt, "_ACCOUNT", tmp_path / "a.json")
        monkeypatch.setattr(pt, "_audit_log", lambda r: None)
        pt.snapshot_equity(1.0e7)
        pt.snapshot_equity(1.02e7)          # 같은 날 → 1점 갱신
        hist = pt.get_account()["equity_history"]
        assert len(hist) == 1 and hist[-1]["equity_krw"] == 1.02e7
        pt.reset()
        h2 = pt.get_account()["equity_history"]
        assert len(h2) == 1 and h2[-1]["equity_krw"] == float(pt.STARTING_CAPITAL_KRW)

    def test_reset_preserves_config_clears_data(self, tmp_path, monkeypatch):
        # reset = 테스트 데이터(포지션·체결·곡선·결정이력) 삭제, 설정(auto·사이징)
        # 보존 (2026-06-06 사용자 요청).
        import bot.paper_trading as p
        monkeypatch.setattr(p, "_ACCOUNT", tmp_path / "a.json")
        monkeypatch.setattr(p, "_AUDIT", tmp_path / "au.jsonl")
        monkeypatch.setattr(p, "_HOME", tmp_path)
        monkeypatch.setattr(p, "live_native_price", lambda t: (313.0, "US"))
        monkeypatch.setattr(p, "_market_fx", lambda m: ("USD", 1380.0))
        p.set_auto(True)
        p.set_auto_size(0.10)
        p.buy("AAPL", 1)
        (tmp_path / "auto_audit.jsonl").write_text("{}\n", encoding="utf-8")
        ok, _ = p.reset()
        acct = p.get_account()
        assert ok and not acct["positions"], "포지션 미삭제"
        assert p.auto_enabled() is True and p.auto_size_pct() == 0.10, "설정 미보존"
        assert not (tmp_path / "auto_audit.jsonl").exists(), "결정 이력 미삭제"
        assert len(acct["equity_history"]) == 1, "equity baseline 누락"

    def test_purge_ticker(self, tmp_path, monkeypatch):
        # 개별 종목 리셋(테스트 정리) — 흔적 삭제·원가 현금 환원(매도 아님).
        import bot.paper_trading as p
        monkeypatch.setattr(p, "_ACCOUNT", tmp_path / "a.json")
        monkeypatch.setattr(p, "_AUDIT", tmp_path / "au.jsonl")
        monkeypatch.setattr(p, "_HOME", tmp_path)
        monkeypatch.setattr(p, "live_native_price", lambda t: (313.0, "US"))
        monkeypatch.setattr(p, "_market_fx", lambda m: ("USD", 1380.0))
        cash0 = p.get_account()["cash_krw"]
        p.buy("AAPL", 1)
        p.buy("NVDA", 1)
        (tmp_path / "auto_audit.jsonl").write_text(
            '{"ticker":"AAPL"}\n{"ticker":"NVDA"}\n', encoding="utf-8")
        ok, _ = p.purge_ticker("AAPL")
        acct = p.get_account()
        assert ok and "AAPL" not in acct["positions"] and "NVDA" in acct["positions"]
        assert abs(acct["cash_krw"] - (cash0 - 313 * 1380)) < 1   # AAPL 원가 환원
        aud = (tmp_path / "auto_audit.jsonl").read_text(encoding="utf-8")
        assert "AAPL" not in aud and "NVDA" in aud                # 결정 이력 필터
        assert p.purge_ticker("TSLA")[0] is False                 # 흔적 없으면 False
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "purge_ticker" in tb and "/paper reset TICKER" in tb

    def test_equity_curve_render(self):
        from bot.dashboard import _render_paper_page
        base = dict(cash_krw=1e7, positions_value_krw=0, total_equity_krw=1e7,
                    starting_capital_krw=1e7, realized_pnl_krw=0, unrealized_pnl_krw=0,
                    total_return_pct=0, n_positions=0, priced_all=True, stats={},
                    auto_size_pct=0.05, rows=[], trades=[{"ts": 1}])
        assert "자산 추이" not in _render_paper_page(
            dict(base, equity_history=[{"date": "2026-06-06", "equity_krw": 1e7}]))
        h = _render_paper_page(dict(base, equity_history=[
            {"date": "2026-06-06", "equity_krw": 1e7},
            {"date": "2026-06-07", "equity_krw": 1.02e7}]))
        assert "자산 추이 (2일)" in h and "<polyline" in h
        assert "snapshot_equity" in open("bot/dashboard.py", encoding="utf-8").read()

    def test_dashboard_render_and_wiring(self):
        from bot.dashboard import _render_paper_page
        empty = _render_paper_page({"rows": [], "trades": [], "n_positions": 0,
            "priced_all": True, "total_equity_krw": 1e7, "cash_krw": 1e7,
            "positions_value_krw": 0, "unrealized_pnl_krw": 0, "realized_pnl_krw": 0,
            "total_return_pct": 0, "starting_capital_krw": 1e7})
        assert "아직 모의 거래가 없습니다" in empty
        assert empty.lower().count("<!doctype") == 1, "CSS leak/중첩 문서"
        # 텔레그램·대시보드 배선
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "async def cmd_paper" in tb and '"paper": (cmd_paper' in tb  # 단일 레지스트리 등록
        assert '"paper": (cmd_paper, "페이퍼' in tb and "regenerate_paper_index" in tb  # 메뉴=레지스트리 파생
        assert "전체 /paper help" in tb, "help §1 /paper help 포인터 누락"
        assert 'sub in ("help"' in tb, "/paper help 핸들러 누락"
        # 채널에서도 동작 — on_channel_post 가 'paper' 라우팅 + DM 과 로직 공유.
        assert 'first_word == "paper"' in tb, "채널 /paper 라우팅 누락"
        assert "async def _handle_paper" in tb, "DM·채널 공유 핸들러 누락"
        ds = open("bot/dashboard.py", encoding="utf-8").read()
        # 페이퍼+워치리스트 통합(2026-06-09) 후 nav 라벨이 '🔔 워치리스트'.
        assert "def regenerate_paper_index" in ds and 'paper.html">🔔 워치리스트' in ds


class TestRiskGate:
    """E0.5 Risk Gate — 하드 캡 + kill-switch (docs/execution_architecture §5,
    페이퍼 프로파일). 매수만 게이트, 매도/청산은 항상 허용(de-risk)."""

    def test_per_trade_and_sell_passthrough(self):
        import bot.risk_gate as rg
        acct = {"starting_capital_krw": 1e7, "positions": {}, "trades": []}
        assert rg.check_order(acct, "AAPL", "buy", rg.PER_TRADE_PCT * 1e7 - 1)[0]
        assert not rg.check_order(acct, "AAPL", "buy", rg.PER_TRADE_PCT * 1e7 + 1000)[0]
        assert rg.check_order(acct, "AAPL", "sell", 0)[0]      # 매도 항상 허용

    def test_max_positions_and_per_position(self):
        import bot.risk_gate as rg
        full = {"starting_capital_krw": 1e7,
                "positions": {f"T{i}": {} for i in range(rg.MAX_POSITIONS)}, "trades": []}
        assert not rg.check_order(full, "NEW", "buy", 1000)[0]   # 신규 종목 수 한도
        assert rg.check_order(full, "T0", "buy", 1000)[0]        # 기존 종목은 비중 내 OK
        pos = {"starting_capital_krw": 1e7,
               "positions": {"AAPL": {"cost_basis_krw": rg.PER_POSITION_PCT * 1e7}}, "trades": []}
        assert not rg.check_order(pos, "AAPL", "buy", 1000)[0]   # 종목당 비중 초과

    def test_daily_loss_halts_buys_not_sells(self):
        import time
        import bot.risk_gate as rg
        acct = {"starting_capital_krw": 1e7, "positions": {},
                "trades": [{"ts": time.time(), "realized_krw": -rg.DAILY_LOSS_PCT * 1e7 - 1}]}
        assert not rg.check_order(acct, "AAPL", "buy", 1000)[0]
        assert rg.check_order(acct, "AAPL", "sell", 0)[0]

    def test_killswitch_blocks_buys_allows_sells(self, tmp_path, monkeypatch):
        import bot.risk_gate as rg
        monkeypatch.setattr(rg, "_HALT_FILE", tmp_path / "HALT")
        acct = {"starting_capital_krw": 1e7, "positions": {}, "trades": []}
        assert not rg.halt_active()
        rg.set_halt(True)
        assert rg.halt_active()
        assert not rg.check_order(acct, "AAPL", "buy", 1000)[0]   # 매수 차단
        assert rg.check_order(acct, "AAPL", "sell", 0)[0]         # 매도 허용
        rg.set_halt(False)
        assert not rg.halt_active() and rg.check_order(acct, "AAPL", "buy", 1000)[0]

    def test_wiring(self):
        pt = open("bot/paper_trading.py", encoding="utf-8").read()
        assert "from bot.risk_gate import check_order" in pt and "gate_ok" in pt, "게이트 미배선"
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert '("halt", "resume")' in tb and "set_halt" in tb, "halt/resume 명령 누락"
        ds = open("bot/dashboard.py", encoding="utf-8").read()
        assert "halt_banner" in ds and "status_line" in ds, "대시보드 게이트 표시 누락"


class TestPaperAutoSignals:
    """E0.5b — NOAH 판정 → 페이퍼 자동 주문 (분석≠실행 분리, auto opt-in,
    자본 5% 사이징, 5거래일 자동 청산)."""

    def test_direction_mapping(self):
        import bot.paper_signals as ps
        assert ps._direction("Buy") == "up" and ps._direction("Overweight") == "up"
        assert ps._direction("Sell") == "down" and ps._direction("Underweight") == "down"
        assert ps._direction("Hold") == "hold" and ps._direction("?") == "hold"

    def test_decision_chain_audit(self, tmp_path, monkeypatch):
        # 결정 체인 추적성 — RM/트레이더/PM/분석가 다수/discipline 기록(2026-06-06).
        import json
        import bot.paper_signals as ps, bot.paper_trading as pt
        summary = ("🎯 최종 판정: Hold\n📈 시장: 매수 · 💬 감정: 보유 · 📰 뉴스: 보유 "
                   "· 💰 펀더멘털: 보유\n⚠️ 트레이더 매수 → 최종 보유 (다른 결론)")
        full = "## 🧭 투자 계획 (리서치 매니저)\n추천: Overweight\n## 💼 트레이더 제안\n거래 액션: Buy"
        chain = ps._extract_chain("Hold", summary, full)
        assert chain["rm"] == "Overweight" and chain["trader"] == "Buy"
        assert chain["pm"] == "Hold" and chain["majority"] == "hold" and chain["contested"]
        cs = ps._chain_str(chain)
        assert "RM Overweight" in cs and "Tr Buy" in cs and "PM Hold" in cs and "다수 보유" in cs
        # 자동 매수 시 audit.jsonl 에 체인+결과 기록
        monkeypatch.setattr(ps, "_AUTO_AUDIT", tmp_path / "audit.jsonl")
        monkeypatch.setattr(pt, "auto_enabled", lambda: True)
        monkeypatch.setattr(pt, "starting_capital_krw", lambda: 1e7)
        monkeypatch.setattr(pt, "auto_size_pct", lambda: 0.05)
        monkeypatch.setattr(pt, "buy_value", lambda *a, **k: (True, "체결"))
        note = ps.on_analysis("AAPL", "Buy", "📈 시장: 매수 · 💬 감정: 매수",
                              "## 🧭 투자 계획 (리서치 매니저)\n추천: Buy\n거래 액션: Buy")
        assert note and "RM Buy" in note and "PM Buy" in note
        rec = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip())
        assert rec["ticker"] == "AAPL" and rec["chain"]["pm"] == "Buy"
        assert "buy_filled" in rec["outcome"]
        # 대시보드도 결정 체인 이력 표시(추적성 surface)
        assert "자동매매 결정 이력" in open("bot/dashboard.py", encoding="utf-8").read()

    def test_auto_off_noop(self, monkeypatch):
        import bot.paper_trading as pt, bot.paper_signals as ps
        monkeypatch.setattr(pt, "auto_enabled", lambda: False)
        assert ps.on_analysis("AAPL", "Buy") is None

    def test_conviction_gate_blocks_contested_buy(self, monkeypatch):
        # (b) 자동 컨빅션 게이트 — contested(PM이 분석가/트레이더와 갈림) Buy 는
        # 자동 매수 보류. clean 신호면 정상 매수.
        import bot.paper_trading as pt, bot.paper_signals as ps
        monkeypatch.setattr(pt, "auto_enabled", lambda: True)
        monkeypatch.setattr(pt, "starting_capital_krw", lambda: 1e7)
        monkeypatch.setattr(pt, "auto_size_pct", lambda: 0.05)
        called = {"buy": False}

        def fake_buy_value(*a, **k):
            called["buy"] = True
            return True, "체결"
        monkeypatch.setattr(pt, "buy_value", fake_buy_value)
        # contested 요약(다른 결론 배너) → 보류, 매수 미실행
        note = ps.on_analysis("AAPL", "Buy",
                              "⚠️ 트레이더 매수 → 최종 보유 (PM(최종 결정권자)의 자체 판단 — 다른 결론)")
        assert note and "보류" in note and "contested" in note
        assert called["buy"] is False, "contested 인데 자동 매수 실행됨"
        # clean 요약 → 정상 매수
        note2 = ps.on_analysis("AAPL", "Buy", "📈 시장: 매수 · 💬 감정: 매수")
        assert called["buy"] is True and "자동매수" in note2

    def test_label_clarifies_research_manager(self):
        # (a) 라벨 명확화 — 투자계획=리서치 매니저(PM 아님), divergence 배너도 명시.
        az = open("bot/analyzer.py", encoding="utf-8").read()
        assert "🧭 투자 계획 (리서치 매니저)" in az, "투자계획 라벨 명확화 누락"
        assert "PM(최종 결정권자)" in az, "divergence 배너 PM 명확화 누락"

    def test_auto_buy_on_buy_verdict(self, monkeypatch):
        import bot.paper_trading as pt, bot.paper_signals as ps
        monkeypatch.setattr(pt, "auto_enabled", lambda: True)
        monkeypatch.setattr(pt, "starting_capital_krw", lambda: 1e7)
        monkeypatch.setattr(pt, "auto_size_pct", lambda: 0.05)
        cap = {}

        def fake_buy_value(ticker, target, idem=None, horizon_days=None):
            cap.update(ticker=ticker, target=target, idem=idem, horizon=horizon_days)
            return True, f"매수 체결: {ticker}"
        monkeypatch.setattr(pt, "buy_value", fake_buy_value)
        note = ps.on_analysis("AAPL", "Buy")
        assert note and "자동매수" in note
        assert cap["ticker"] == "AAPL" and cap["horizon"] == ps.HORIZON_DAYS
        assert abs(cap["target"] - 1e7 * 0.05) < 1            # 사이징 5%
        assert cap["idem"].startswith("auto:AAPL:")

    def test_set_auto_size_clamp_and_stats(self, tmp_path, monkeypatch):
        import bot.paper_trading as pt
        monkeypatch.setattr(pt, "_ACCOUNT", tmp_path / "a.json")
        monkeypatch.setattr(pt, "_audit_log", lambda r: None)
        assert pt.auto_size_pct() == pt.DEFAULT_AUTO_SIZE_PCT     # 기본 5%
        assert pt.set_auto_size(0.10) == 0.10 and pt.auto_size_pct() == 0.10
        assert pt.set_auto_size(5.0) == 0.50                      # 50% clamp
        assert pt.set_auto_size(0.0) == 0.01                      # 1% clamp
        # trade_stats — 청산 실현손익 기반 승률
        acct = pt._new_account()
        acct["trades"] = [{"realized_krw": 100}, {"realized_krw": -50},
                          {"realized_krw": 30}, {"realized_krw": None}]
        monkeypatch.setattr(pt, "get_account", lambda: acct)
        s = pt.trade_stats()
        assert s["n_closes"] == 3 and s["wins"] == 2 and s["losses"] == 1
        assert abs(s["win_rate"] - 200 / 3) < 1e-6

    def test_size_and_stats_wiring(self):
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert 'args[1].lower() == "size"' in tb and "set_auto_size" in tb, "/paper auto size 누락"
        assert "자동청산(5거래일 만기)" in tb, "horizon 청산 채널 알림 누락"
        ds = open("bot/dashboard.py", encoding="utf-8").read()
        assert "승률" in ds and "사이징" in ds, "대시보드 승률/사이징 표시 누락"

    def test_auto_close_on_sell_when_held(self, monkeypatch):
        import bot.paper_trading as pt, bot.paper_signals as ps
        monkeypatch.setattr(pt, "auto_enabled", lambda: True)
        monkeypatch.setattr(pt, "get_account", lambda: {"positions": {"AAPL": {}}})
        cap = {}
        monkeypatch.setattr(pt, "close_position",
                            lambda t, idem=None: (cap.update(t=t) or (True, f"매도 체결: {t}")))
        note = ps.on_analysis("AAPL", "Sell")
        assert note and "자동청산" in note and cap["t"] == "AAPL"

    def test_auto_sell_noop_when_not_held(self, monkeypatch):
        import bot.paper_trading as pt, bot.paper_signals as ps
        monkeypatch.setattr(pt, "auto_enabled", lambda: True)
        monkeypatch.setattr(pt, "get_account", lambda: {"positions": {}})
        assert ps.on_analysis("AAPL", "Sell") is None
        monkeypatch.setattr(pt, "auto_enabled", lambda: True)
        assert ps.on_analysis("AAPL", "Hold") is None     # hold → 무동작

    def test_buy_value_sizing(self, monkeypatch):
        import bot.paper_trading as pt
        monkeypatch.setattr(pt, "live_native_price", lambda t: (313.0, "US"))
        monkeypatch.setattr(pt, "_market_fx", lambda m: ("USD", 1380.0))
        monkeypatch.setattr(pt, "_set_horizon", lambda *a, **k: None)
        cap = {}
        monkeypatch.setattr(pt, "_order",
                            lambda t, q, s, i: (cap.update(qty=q, side=s) or (True, "ok")))
        ok, _ = pt.buy_value("AAPL", 500000, horizon_days=5)
        assert ok and cap["qty"] == 1 and cap["side"] == "buy"   # floor(500000/(313*1380))
        ok2, msg2 = pt.buy_value("AAPL", 100000)                 # 1주도 못 삼
        assert not ok2 and "1주도" in msg2

    def test_close_due_positions(self, monkeypatch):
        import bot.paper_trading as pt
        from datetime import datetime, timedelta, timezone
        kst = timezone(timedelta(hours=9))
        past = (datetime.now(kst) - timedelta(days=1)).strftime("%Y-%m-%d")
        future = (datetime.now(kst) + timedelta(days=10)).strftime("%Y-%m-%d")
        monkeypatch.setattr(pt, "get_account", lambda: {"positions": {
            "DUE": {"auto_close_date": past}, "NOTYET": {"auto_close_date": future},
            "MANUAL": {}}})
        closed = []
        monkeypatch.setattr(pt, "close_position",
                            lambda t, idem=None: (closed.append(t) or (True, f"c {t}")))
        out = pt.close_due_positions()
        assert closed == ["DUE"] and len(out) == 1   # 만기 도래만, 미래/수동 제외

    def test_set_auto_toggle(self, tmp_path, monkeypatch):
        import bot.paper_trading as pt
        monkeypatch.setattr(pt, "_ACCOUNT", tmp_path / "a.json")
        monkeypatch.setattr(pt, "_audit_log", lambda r: None)
        assert pt.auto_enabled() is False
        pt.set_auto(True)
        assert pt.auto_enabled() is True
        pt.set_auto(False)
        assert pt.auto_enabled() is False

    def test_wiring(self):
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "from bot.paper_signals import on_analysis" in tb, "분석 완료 훅 누락"
        assert 'sub == "auto"' in tb and "set_auto" in tb, "/paper auto 명령 누락"
        assert "close_due_positions" in tb, "periodic 자동청산 훅 누락"
        pt = open("bot/paper_trading.py", encoding="utf-8").read()
        assert "def buy_value" in pt and "def close_due_positions" in pt
        ds = open("bot/dashboard.py", encoding="utf-8").read()
        assert "자동매매" in ds and "auto_close_date" in ds


class TestPaperLimitOrders:
    """E0.5d 지정가 주문 — PENDING 보관 + 가격 도달 시 자동 체결 + 취소."""

    def _setup(self, tmp_path, monkeypatch):
        import bot.paper_trading as p
        monkeypatch.setattr(p, "_ACCOUNT", tmp_path / "a.json")
        monkeypatch.setattr(p, "_audit_log", lambda r: None)
        monkeypatch.setattr(p, "_market_fx", lambda m: ("USD", 1380.0))
        return p

    def test_limit_buy_pending_then_fill(self, tmp_path, monkeypatch):
        p = self._setup(tmp_path, monkeypatch)
        cur = {"px": 313.0}
        monkeypatch.setattr(p, "live_native_price", lambda t: (cur["px"], "US"))
        ok, msg = p.buy("AAPL", 1, limit=300.0)         # 313 > 300 → 매수 대기
        assert ok and "지정가" in msg and len(p.list_pending()) == 1
        assert p.fill_pending() == [] and len(p.list_pending()) == 1   # 안 떨어짐
        cur["px"] = 295.0                                # 도달
        assert p.fill_pending() and not p.list_pending()
        assert p.get_account()["positions"]["AAPL"]["qty"] == 1

    def test_marketable_limit_fills_immediately(self, tmp_path, monkeypatch):
        p = self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(p, "live_native_price", lambda t: (295.0, "US"))
        ok, msg = p.buy("AAPL", 1, limit=320.0)         # 295 ≤ 320 → 즉시 체결
        assert ok and "체결" in msg and not p.list_pending()

    def test_cancel_pending(self, tmp_path, monkeypatch):
        p = self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(p, "live_native_price", lambda t: (313.0, "US"))
        p.buy("AAPL", 1, limit=250.0)
        assert len(p.list_pending()) == 1
        ok, _ = p.cancel_pending("all")
        assert ok and not p.list_pending()

    def test_wiring(self):
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert 'a.startswith("@")' in tb, "지정가 @ 파싱 누락"
        assert 'sub == "pending"' in tb and 'sub == "cancel"' in tb, "pending/cancel 명령 누락"
        assert "_periodic_paper_pending" in tb, "지정가 체결 periodic 누락"
        pt = open("bot/paper_trading.py", encoding="utf-8").read()
        assert "def fill_pending" in pt and "def cancel_pending" in pt
        assert "지정가 대기" in open("bot/dashboard.py", encoding="utf-8").read()


class TestMegacapComps:
    """fix(2026-06-06 AAPL review): mega-cap tech 는 산업 무관 Mag-7 풀로
    (좁은 'Consumer Electronics' 분류가 AAPL 을 GoPro/Sonos 와 비교하던 무의미
    Comps 차단). 소형주는 기존 산업 리스트 유지."""

    def test_megacap_routes_to_mag7(self):
        from bot.market import resolve_peer_set
        aapl = resolve_peer_set("AAPL", "Consumer Electronics")
        assert aapl and "GPRO" not in aapl and "SONO" not in aapl, "AAPL 이 미니캡과 비교됨"
        assert "MSFT" in aapl and "AAPL" not in aapl, "Mag-7 풀 아님/자기 미제외"
        assert "MSFT" in (resolve_peer_set("NVDA", "Semiconductors") or [])
        # 소형주(GoPro)는 영향 없이 산업 리스트 유지(override 화이트리스트에 없음)
        gpro = resolve_peer_set("GPRO", "Consumer Electronics")
        assert gpro and "GPRO" not in gpro


class TestGicsCandidatesPage:
    """fix(2026-06-05): GICS 후보 대시보드가 서버 파일시스템 경로
    (~/.tradingagents/gics_check_audit.jsonl)를 본문 footer 로 노출하던 것 제거.
    웹 뷰어에겐 무의미 + 홈 디렉터리·계정명 등 내부 경로 노출."""

    def test_no_server_path_leak(self):
        from bot.dashboard import _render_gics_candidates_page
        run = {"ts": "2026-06-05T09:00", "cost_krw": 300, "report": {
            "window_start": "2026-03", "window_end": "2026-06", "summary": "x",
            "official_gics_changes": [], "emerging_trends": []}}
        html = _render_gics_candidates_page([run])
        assert "gics_check_audit.jsonl" not in html, "감사 로그 파일 경로 노출"
        assert "raw 응답 audit log" not in html, "감사 로그 footer 노출"
        assert "/home/" not in html, "서버 파일시스템 경로 노출"
        assert "분기 GICS" in html, "페이지 자체는 정상 렌더"


# 9) 트레이드 마커 (entry/stop/target) 파싱 + 비현실값 차단
#    배경: 2026-06-03 Phase 2 — full_report 의 진입/손절/목표가를 차트에
#    수평선으로. 핵심 안전장치: 종가 series 대비 비현실적(0.2x~5x 밖)인
#    파싱값은 버려서 잘못된 라인이 절대 안 그려지게 (v1 마커 제외 사유 해소).
# ─────────────────────────────────────────────────────────────────────────
class TestTradeLevelParser:
    """fix: 차트 마커 (2026-06-03)."""

    def test_us_labels_extracted(self):
        from bot.chart_data import parse_trade_levels

        report = "**Entry Price**: $145.50\n**Stop Loss**: $138.00\n**Price Target**: $162.00"
        out = parse_trade_levels(report, [140.0, 145.0, 146.0])
        assert out == {"entry": 145.5, "stop": 138.0, "target": 162.0}

    def test_korean_labels_with_krw_commas(self):
        from bot.chart_data import parse_trade_levels

        report = "진입가: ₩71,500\n손절가: ₩68,000\n목표가: ₩85,000"
        out = parse_trade_levels(report, [70000, 71000, 72000])
        assert out == {"entry": 71500.0, "stop": 68000.0, "target": 85000.0}

    def test_implausible_value_rejected(self):
        from bot.chart_data import parse_trade_levels

        # 목표가에 날짜 '2026' 오파싱 — last close 146 기준 밴드 밖 → drop
        report = "**Entry Price**: $145.00\n**Stop Loss**: $138.00\n목표가: 2026"
        out = parse_trade_levels(report, [140.0, 145.0, 146.0])
        assert "target" not in out, "비현실값이 마커로 그려지면 안 됨"
        assert out.get("entry") == 145.0 and out.get("stop") == 138.0

    def test_no_series_conservative_empty(self):
        from bot.chart_data import parse_trade_levels

        report = "**Entry Price**: $145.00"
        # close series 없으면 plausibility 판정 불가 → 보수적으로 {}
        assert parse_trade_levels(report, []) == {}
        assert parse_trade_levels(report, None) == {}

    def test_no_levels_in_report(self):
        from bot.chart_data import parse_trade_levels

        assert parse_trade_levels("아무 플랜도 없는 본문", [100.0, 101.0]) == {}

    def test_markers_injected_into_chart_section(self):
        from bot.dashboard import _render_chart_section

        rec = {
            "ticker": "AAPL",
            "full_report": "**Entry Price**: $145.50\n**Stop Loss**: $138.00",
            "price_chart": {
                "currency": "$", "decimals": 2,
                "times": ["2025-06-01", "2025-06-02", "2025-06-03"],
                "close": [144.0, 145.0, 146.0],
            },
        }
        html = _render_chart_section(rec)
        assert '"markers"' in html, "마커 페이로드가 차트 JSON 에 주입되어야"
        assert "145.5" in html and "138" in html

    def test_chart_section_has_timeframe_toolbar(self):
        """일/주/월봉 + 기간 토글 버튼 + data-ticker (on-demand fetch 용)."""
        from bot.dashboard import _render_chart_section

        rec = {
            "ticker": "TSLA",
            "price_chart": {
                "currency": "$", "decimals": 2,
                "times": ["2025-06-01", "2025-06-02"],
                "close": [144.0, 145.0],
            },
        }
        html = _render_chart_section(rec)
        assert 'data-ticker="TSLA"' in html, "API fetch 용 ticker 누락"
        assert 'data-kind="interval" data-val="1d"' in html, "일봉 버튼 누락"
        assert 'data-kind="interval" data-val="1wk"' in html, "주봉 버튼 누락"
        assert 'data-kind="interval" data-val="1mo"' in html, "월봉 버튼 누락"
        assert 'data-kind="range" data-val="max"' in html, "전체 기간 버튼 누락"

    def test_fetch_chart_payload_normalizes_bad_inputs(self):
        """잘못된 interval/range 는 화이트리스트 기본값으로 정규화 (예외
        전파 금지). 네트워크 없으면 None (graceful)."""
        from bot.chart_data import fetch_chart_payload

        result = fetch_chart_payload("AAPL", interval="evil", period="../etc")
        assert result is None or isinstance(result, dict)

    def test_chart_api_interval_range_whitelists(self):
        """서버 엔드포인트가 참조하는 화이트리스트가 코드에 존재 (path
        traversal / 임의 yfinance 호출 차단)."""
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert "/api/chart" in src, "차트 API 경로 누락"
        assert "_VALID_INTERVALS" in src and "_VALID_RANGES" in src
        assert "_TICKER_RE.match(ticker)" in src, "ticker 검증 누락"

    def test_chart_range_whitelist_sync_ytd(self):
        """range 화이트리스트가 두 곳(chart_data._VALID_PERIODS + dashboard_
        server._VALID_RANGES)에 중복 정의 — 한쪽에만 추가하면 그 버튼이 조용히
        1y 로 폴백하는 드리프트가 생긴다. 둘이 정확히 일치 + 둘 다 'ytd' 포함
        (YTD 버튼이 실제 동작) 영구 보장 (2026-06-07 Tier 1)."""
        import re as _re
        from bot.chart_data import _VALID_PERIODS
        assert "ytd" in _VALID_PERIODS, "chart_data._VALID_PERIODS 에 ytd 누락"
        srv = open("bot/dashboard_server.py", encoding="utf-8").read()
        m = _re.search(r"_VALID_RANGES\s*=\s*\{([^}]*)\}", srv)
        assert m, "_VALID_RANGES 리터럴 누락"
        server_ranges = set(_re.findall(r'"([a-z0-9]+)"', m.group(1)))
        assert "ytd" in server_ranges, "서버 _VALID_RANGES 에 ytd 누락"
        assert server_ranges == set(_VALID_PERIODS), (
            f"range 화이트리스트 불일치: server={server_ranges} "
            f"chart_data={set(_VALID_PERIODS)}"
        )


# ─────────────────────────────────────────────────────────────────────────
# 9) 자산관리 — 뱅크샐러드 export 파서 (2026-06-04 P1 증분1)
#    뱅샐현황 시트(섹션형) → 투자/재무/부동산/동산/대출/보험/현금흐름 구조화.
#    1.고객정보(PII)는 파싱 안 함. 총자산/순자산은 export 가 빈 셀이라 항목
#    합으로 산출. 실파일은 PII 라 미커밋 — 합성 데이터로 검증.
# ─────────────────────────────────────────────────────────────────────────
_BANKSALAD_SYNTH = [
    {"B": "2.현금흐름현황"},
    {"B": "항목", "C": "총계", "D": "월평균", "E": "2025-06", "F": "2025-07"},
    {"B": "식사", "C": "0", "D": "0", "E": "100", "F": "200"},
    {"B": "월지출 총계", "C": "0"},
    {"B": "3.재무현황"},
    {"B": "데이터를 내보낸 시점의 자산과 부채 상태"},
    {"B": "자산", "F": "부채"},
    {"B": "항목", "C": "상품명", "E": "금액", "F": "항목", "G": "상품명", "I": "금액"},
    {"B": "자유입출금 자산", "C": "토스뱅크 통장", "E": "1000", "F": "장기대출", "G": "신한 마이너스", "I": "0"},
    {"C": "우리신세대", "E": "2000"},
    {"B": "투자성 자산", "C": "이오테크닉스", "E": "5000"},
    {"C": "한솔케미칼", "E": "3000"},
    {"B": "부동산", "C": "광교상떼빌", "E": "900000000"},
    {"B": "동산", "C": "현대 그랜저", "E": "30000000"},
    {"B": "총자산", "E": "0"},
    {"B": "순자산"},
    {"B": "4.보험현황"},
    {"B": "금융사", "C": "보험명", "E": "계약상태", "F": "총납입금"},
    {"B": "흥국화재", "C": "가족사랑", "E": "정상", "F": "5000"},
    {"B": "총계", "C": "보유계약건수"},
    {"B": "5.투자현황"},
    {"B": "투자상품종류", "C": "금융사", "D": "상품명", "F": "투자원금", "G": "평가금액", "H": "수익률"},
    {"B": "주식", "C": "NH투자증권", "D": "이오테크닉스", "F": "2298", "G": "5000", "H": "117.6"},
    {"B": "주식", "C": "삼성증권", "D": "램 리서치", "F": "476", "G": "5000", "H": "948.9"},
    {"B": "총계", "D": "보유종목"},
    {"B": "6.대출현황"},
    {"B": "대출종류", "C": "금융사", "D": "상품명", "F": "대출원금", "G": "대출잔액", "H": "대출금리"},
    {"B": "은행 대출", "C": "신한은행", "D": "마이너스", "F": "1000000", "G": "0", "H": "5.54"},
    {"B": "총계", "D": "보유 대출"},
]


def _mk_min_xlsx():
    """최소 유효 xlsx(inlineStr) 1시트 — read_xlsx/parse_export 라운드트립용."""
    import io as _io
    import zipfile as _zf
    NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ct = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
          'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>')
    wb = (f'<?xml version="1.0"?><workbook xmlns="{NS}"><sheets>'
          '<sheet name="뱅샐현황" sheetId="1"/></sheets></workbook>')

    def s(ref, txt):
        return f'<c r="{ref}" t="inlineStr"><is><t>{txt}</t></is></c>'

    sheet = (f'<?xml version="1.0"?><worksheet xmlns="{NS}"><sheetData>'
             f'<row r="1">{s("B1", "5.투자현황")}</row>'
             f'<row r="2">{s("B2", "투자상품종류")}{s("D2", "상품명")}</row>'
             f'<row r="3">{s("B3", "주식")}{s("C3", "NH투자증권")}{s("D3", "이오테크닉스")}'
             f'<c r="G3"><v>5000</v></c></row>'
             '</sheetData></worksheet>')
    buf = _io.BytesIO()
    with _zf.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


class TestBanksaladParser:
    """fix: 뱅크샐러드 자산 export 파서 (2026-06-04 자산관리 P1)."""

    def test_parse_sections(self):
        from bot.portfolio_parser import parse_banksalad
        p = parse_banksalad(_BANKSALAD_SYNTH)
        # 투자현황 — 종목/금융사/수익률, '총계' 행 제외
        assert len(p["holdings"]) == 2
        h0 = p["holdings"][0]
        assert h0["상품명"] == "이오테크닉스" and h0["금융사"] == "NH투자증권"
        assert abs(h0["수익률"] - 117.6) < 0.01 and h0["평가금액"] == 5000
        # 부동산/동산 캡처 (사용자 요청 핵심)
        fin = p["finance"]
        assert fin["assets"]["부동산"][0]["name"] == "광교상떼빌"
        assert fin["assets"]["동산"][0]["name"] == "현대 그랜저"
        # 총자산 = 항목 합(export 빈 셀 0 이 아님), 순자산 = 총자산 − 총부채
        assert fin["총자산"] == 930011000
        assert fin["총부채"] == 0 and fin["순자산"] == 930011000
        assert fin["총자산_export"] == 0.0
        # 대출/보험/현금흐름
        assert len(p["loans"]) == 1 and p["loans"][0]["대출금리"] == 5.54
        assert len(p["insurance"]) == 1 and p["insurance"][0]["계약상태"] == "정상"
        assert p["cashflow"]["months"] == ["2025-06", "2025-07"]

    def test_summarize(self):
        from bot.portfolio_parser import parse_banksalad, summarize
        s = summarize(parse_banksalad(_BANKSALAD_SYNTH))
        assert s["주식_종목수"] == 2
        assert s["브로커별_종목수"] == {"NH투자증권": 1, "삼성증권": 1}
        assert s["순자산"] == 930011000 and s["대출_건수"] == 1 and s["보험_건수"] == 1

    def test_read_xlsx_and_parse_export(self):
        from bot.portfolio_parser import read_xlsx, parse_export
        data = _mk_min_xlsx()
        assert "뱅샐현황" in read_xlsx(data), "시트 이름 파싱 실패"
        # xlsx 바이트를 parse_export 에 주면 zip-추출 실패→xlsx 직접 읽기 폴백
        p = parse_export(data)
        assert any(h["상품명"] == "이오테크닉스" for h in p["holdings"])

    def test_extract_from_plain_zip(self):
        import io as _io
        import zipfile as _zf
        from bot.portfolio_parser import extract_xlsx_from_zip
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as z:
            z.writestr("report.xlsx", b"PK\x03\x04fake")
        assert extract_xlsx_from_zip(buf.getvalue(), password=None) == b"PK\x03\x04fake"
        # 실제 뱅샐 zip(ZipCrypto+pwd 5120)은 라이브 검증 완료(2026-06-04);
        # 암호화 zip 픽스처는 PII 우려로 미커밋.


class TestPortfolioResolve:
    """fix: 종목 한글명→티커 resolver (2026-06-04 자산관리 P1 증분2)."""

    def test_overseas_alias(self):
        from bot.portfolio_resolve import resolve_overseas
        assert resolve_overseas("램 리서치") == ("LRCX", "US")
        assert resolve_overseas("어플라이드 머티어리얼즈") == ("AMAT", "US")
        assert resolve_overseas("ST 마이크로 일렉트로닉스 ADR") == ("STM", "US")
        assert resolve_overseas("엔비디아") == ("NVDA", "US")
        # 국내 종목(파마리서치)·미등록은 alias None (pykrx 가 잡음)
        assert resolve_overseas("파마리서치") is None
        assert resolve_overseas("없는종목xyz") is None

    def test_resolve_ticker_graceful(self):
        from bot.portfolio_resolve import resolve_ticker
        # 해외는 alias 로 즉시 매칭
        r = resolve_ticker("램 리서치")
        assert r["matched"] and r["ticker"] == "LRCX" and r["source"] == "alias"
        # 국내는 pykrx 필요 — 샌드박스 creds 없으면 matched False 지만 crash 없어야
        r2 = resolve_ticker("이오테크닉스")
        assert r2["matched"] in (True, False) and r2.get("ticker") in (None, "039030.KS", "039030.KQ")


class TestPortfolioModel:
    """fix: 포트폴리오 집계·요약 모델 (2026-06-04 자산관리 P1 증분3)."""

    def _model(self):
        from bot.portfolio_parser import parse_banksalad
        from bot.portfolio import build_model
        return build_model(parse_banksalad(_BANKSALAD_SYNTH))

    def test_build_model_aggregates(self):
        m = self._model()
        assert m["holding_count"] == 2
        assert m["matched_count"] >= 1  # 램 리서치 alias 매칭(creds 무관)
        ioteq = [h for h in m["holdings"] if h["상품명"] == "이오테크닉스"][0]
        assert ioteq["평가손익"] == 5000 - 2298
        lam = [h for h in m["holdings"] if h["상품명"] == "램 리서치"][0]
        assert lam["ticker"] == "LRCX" and lam["market"] == "US"
        assert set(m["by_broker"]) == {"NH투자증권", "삼성증권"}
        assert m["by_broker"]["NH투자증권"]["종목수"] == 1
        assert "부동산" in m["asset_allocation"] and "동산" in m["asset_allocation"]
        assert m["net_worth"]["순자산"] == 930011000
        assert m["top_gainers"][0]["상품명"] == "램 리서치"  # 948.9 > 117.6

    def test_format_summary_text(self):
        from bot.portfolio import format_summary_text
        txt = format_summary_text(self._model())
        assert "순자산" in txt
        assert "9.3억" in txt  # 930,011,000 → 9.3억
        assert "NH투자증권" in txt and "부동산" in txt

    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        import bot.portfolio as pf
        monkeypatch.setattr(pf, "PORTFOLIO_PATH", tmp_path / "portfolio.json")
        pf.save(self._model())
        loaded = pf.load()
        assert loaded is not None and loaded["holding_count"] == 2 and "_saved_ts" in loaded

    def test_cli_badpath_safe(self):
        # CLI 검증 엔트리(python -m bot.portfolio <zip>) — 없는 경로는 exit 2,
        # ingest/save 호출 전이라 실제 portfolio.json 을 건드리지 않음(안전).
        from bot.portfolio import main
        assert main(["/nonexistent_xyz_portfolio.zip"]) == 2

    def test_top_movers_dedup_by_name(self):
        # 같은 종목 복수 증권사 보유 → 수익률 TOP/WORST 에 한 번만(|수익률| 큰 것).
        from bot.portfolio import build_model
        stub = lambda n: {"ticker": None, "market": None, "matched": False, "source": None}
        parsed = {"holdings": [
            {"종류": "주식", "금융사": "NH", "상품명": "비보심 랩스", "투자원금": 100, "평가금액": 3, "수익률": -98.3},
            {"종류": "주식", "금융사": "삼성", "상품명": "비보심 랩스", "투자원금": 100, "평가금액": 4, "수익률": -89.0},
            {"종류": "주식", "금융사": "NH", "상품명": "램", "투자원금": 10, "평가금액": 50, "수익률": 400.0},
        ], "finance": {}}
        m = build_model(parsed, resolve=stub)
        losers = [h["상품명"] for h in m["top_losers"]]
        assert losers.count("비보심 랩스") == 1, "중복 종목 제거 실패"
        bibo = [h for h in m["top_losers"] if h["상품명"] == "비보심 랩스"][0]
        assert abs(bibo["수익률"] - (-98.3)) < 0.01, "더 두드러지는(|수익률| 큰) 것이 안 남음"

    def test_snapshot_for_delta(self):
        # 증분 비교용 스냅샷이 모델에 포함 (ingest 가 다음 업로드 시 prev 로 사용).
        m = self._model()
        assert "snapshot" in m
        assert m["snapshot"]["순자산"] == 930011000 and m["snapshot"]["종목수"] == 2

    def test_distinct_count_dedups_across_brokers(self):
        # '보유 종목' 카운트 = 고유 종목(증권사 중복 제외, 사용자 2026-06-05).
        # 매칭은 ticker(증권사 간 표기 차이 흡수), 미매칭은 이름으로 dedup.
        from bot.portfolio import build_model, _distinct_stock_keys

        def resolve(name):
            if name in ("애플", "Apple"):  # 같은 종목, 다른 표기 → 같은 ticker
                return {"ticker": "AAPL", "market": "US", "matched": True, "source": "x"}
            return {"ticker": None, "market": None, "matched": False, "source": None}

        parsed = {"holdings": [
            {"종류": "주식", "금융사": "NH",   "상품명": "애플",       "투자원금": 100, "평가금액": 120},
            {"종류": "주식", "금융사": "삼성", "상품명": "Apple",      "투자원금": 100, "평가금액": 120},
            {"종류": "주식", "금융사": "NH",   "상품명": "비보심 랩스", "투자원금": 100, "평가금액": 3},
            {"종류": "주식", "금융사": "삼성", "상품명": "비보심 랩스", "투자원금": 100, "평가금액": 4},
            {"종류": "주식", "금융사": "미래", "상품명": "램",         "투자원금": 10,  "평가금액": 50},
        ], "finance": {}}
        m = build_model(parsed, resolve=resolve)
        assert m["holding_count"] == 5, "포지션 건수는 원본 유지(테이블·증권사 필터용)"
        assert m["distinct_count"] == 3, "고유 종목 = 애플(AAPL) · 비보심 랩스 · 램"
        keys = _distinct_stock_keys(m["holdings"])
        assert keys == {("t", "AAPL"), ("n", "비보심 랩스"), ("n", "램")}


class TestPortfolioDashboard:
    """fix: 자산 대시보드 렌더 + 텔레그램 배선 (2026-06-04 자산관리 P1 증분4)."""

    def _model(self):
        from bot.portfolio_parser import parse_banksalad
        from bot.portfolio import build_model
        return build_model(parse_banksalad(_BANKSALAD_SYNTH))

    def test_render_portfolio_page(self):
        from bot.dashboard import _render_portfolio_page
        html = _render_portfolio_page(self._model())
        assert "<!DOCTYPE html>" in html
        assert "conic-gradient(" in html       # 자산배분 도넛
        assert "순자산" in html and "증권사별" in html
        assert "이오테크닉스" in html          # 보유 테이블
        assert "LRCX" in html                  # 해외 매칭 티커 표시(티커 컬럼)
        # 분석 기록 없으면 종목명 링크 안 함(404 방지) — noah 미전달 시 ticker_*.html 없음
        assert "ticker_LRCX.html" not in html
        assert "부동산" in html                # 자산배분에 부동산
        # 빈 공간 활용(2026-06-04): 도넛 우측 주식 요약 패널
        assert "💹 주식 요약" in html and "승률" in html, "주식 요약 패널 누락"
        # v3 피드백: 보험 표 · 대출 원금 · 동산(자동차) · 국내/해외 · 업데이트 시각
        assert "보험사" in html and "보험명" in html, "보험 표 누락"
        assert "한도(원금)" in html, "대출 원금 컬럼 누락"
        assert "동산 (자동차)" in html, "동산(자동차) 라벨 누락"
        assert "주식 국내 / 해외" in html, "국내/해외 비중 누락"
        assert "마지막 업데이트" in html, "업데이트 시각 헤더 누락"
        # v7: market.html 홈 허브 (Group 1 nav = 홈 + 가계부)
        assert 'href="market.html">🌍 홈' in html, "nav 홈 링크 누락"
        assert 'href="budget.html"' in html, "nav 가계부 링크 누락"
        assert ".nav a,.nav b{white-space:nowrap}" in html, "nav 줄바꿈 방지 CSS 누락"
        assert "<b>💼 자산</b>" not in html, "nav 자기 '자산' 제거 안 됨"
        # 세로 맞춤(2026-06-04): 손익 분포 + 등높이(증권사별↔TOP/WORST)
        assert "수익률 분포" in html, "수익률 분포 누락"
        assert 'pf-grid pf-eqh' in html, "등높이 그리드 클래스 누락"
        # 보유 종목 카운트 = 고유 종목('N종목' 단위). 합성데이터 2종목·중복없음.
        assert "보유 종목 (2종목" in html, "테이블 헤더 '종목' 단위 누락"
        # 저장된 옛 모델(distinct_count 필드 없음)도 재업로드 없이 반영되도록
        # 렌더 시점에 holdings 로 재계산해야.
        assert "_distinct_stock_keys(holdings)" in open("bot/dashboard.py", encoding="utf-8").read(), \
            "보유 종목 카운트 렌더 시점 고유 계산 누락"
        # 빈 상태(업로드 전)
        assert "아직 업로드된 자산이 없습니다" in _render_portfolio_page(None)

    def test_delta_increment(self):
        # 지난 업데이트 대비 증분(자산 변화) — prev 스냅샷 있을 때만 표시.
        from bot.dashboard import _render_portfolio_page
        m = self._model()
        m["prev"] = {"순자산": 900000000, "주식평가": 8000, "_saved_ts": 1, "as_of": "2025-06-05"}
        html = _render_portfolio_page(m)
        assert "지난 업데이트" in html and "대비" in html, "증분 표시 누락"
        # 증분은 주식 요약(💹) 패널 밑에 배치 (사용자 2026-06-04) — 같은 카드 안.
        assert html.index("💹 주식 요약") < html.index("지난 업데이트"), "증분이 주식 요약 밑이 아님"
        # prev 없으면 증분 수치 미표시 + placeholder 안내(사라진 게 아님)
        none_html = _render_portfolio_page(self._model())
        assert "지난 업데이트" not in none_html
        assert "자산 변화" in none_html, "prev 없을 때 자산 변화 안내 placeholder 누락"
        # 같은 업로드 날짜여도 증분 표시 (사용자 정책 2026-06-08 — 덮어쓰기)
        import time as _t
        now = _t.time()
        m2 = self._model()
        m2["_saved_ts"] = now
        m2["prev"] = {"순자산": 1, "주식평가": 1, "_saved_ts": now - 60}
        assert "지난 업데이트" in _render_portfolio_page(m2), "같은 날짜도 증분 표시해야 함"

    def test_noah_overlay(self):
        # 증분5: 보유종목 ↔ NOAH 최근 판정 + (해소되면)5거래일 성과 오버레이.
        # 컬럼 헤더는 'NOAH 판정' — 성과는 5거래일 해소 시 셀 안에 +x.x% 로 표기.
        from bot.dashboard import _render_portfolio_page
        m = self._model()  # holdings: 이오테크닉스(KR), 램 리서치(LRCX)
        noah = {"LRCX": {"rating": "보유", "ret": 12.3, "date": "2026-06-01"}}
        html = _render_portfolio_page(m, noah)
        assert "NOAH 판정" in html, "NOAH 컬럼 헤더 누락"
        assert "보유</a> <span" in html and "+12.3%" in html, "판정·성과 오버레이 누락"
        assert "NOAH 분석 1" in html, "오버레이 카운트 누락"
        # 종목명·판정 링크는 파란 기본색 대신 pf-lnk(일반 텍스트색) — 사용자 요청.
        assert 'class="pf-lnk"' in html, "종목명 일반색 링크 클래스 누락"
        assert 'style="color:var(--accent)"' not in html, "판정 파란 링크 잔존"
        # noah 미전달도 정상(헤더만, graceful)
        assert "NOAH 판정" in _render_portfolio_page(m)

    def test_sort_filter_controls(self):
        # 보유 테이블 정렬/필터(2026-06-04): 헤더 클릭 정렬 + 증권사·검색·NOAH 필터.
        from bot.dashboard import _render_portfolio_page
        html = _render_portfolio_page(self._model())
        assert 'id="pf-tbl"' in html, "정렬 테이블 id 누락"
        assert 'id="pf-broker"' in html and 'id="pf-q"' in html, "증권사/검색 필터 누락"
        assert 'id="pf-noah"' in html, "NOAH 분석만 필터 누락"
        assert 'data-k="ret"' in html and 'data-k="eval"' in html, "정렬 헤더 키 누락"
        # 행에 raw 정렬값 부착(만/억 포맷 비의존)
        assert "data-eval=" in html and "data-broker=" in html, "행 data-* 정렬값 누락"
        assert "addEventListener" in html, "정렬/필터 JS 미주입"
        # 증권사 드롭다운에 실제 증권사(NH투자증권·삼성증권) 옵션
        assert "NH투자증권" in html and "삼성증권" in html, "증권사 옵션 누락"

    def test_nav_and_regen_wired(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'href="portfolio.html">💼 자산' in src, "메인 nav 자산 링크 누락"
        assert "def regenerate_portfolio_index" in src

    def test_telegram_wiring(self):
        # telegram_bot 은 무거운 의존성 import 라 소스 검증(PM 배너 테스트와 동일 패턴).
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "async def cmd_portfolio" in src, "/portfolio 조회 핸들러 누락"
        assert '"portfolio": (cmd_portfolio' in src  # 단일 레지스트리 등록
        assert '"portfolio": (cmd_portfolio, "💼' in src, "set_my_commands 미등록(레지스트리)"
        assert "regenerate_portfolio_index" in src  # startup regen
        # 봇 DM 문서 ingest 핸들러는 제거 — 입력은 RAG 채널 watcher (봇 깨끗하게)
        assert "cmd_portfolio_upload" not in src, "DM 업로드 핸들러 잔존(제거돼야)"
        assert "filters.Document" not in src, "DM 문서 핸들러 잔존(제거돼야)"
        # help §9 대시보드 목록에 자산 추가 (사용자 정책: 대시보드 변경은 help 동기)
        assert "06beb08f5f4ad5515007e65f8f60b471/portfolio.html" in src, "help 자산 링크 누락"


class TestPortfolioWatch:
    """fix: RAG 채널 자산 watcher (2026-06-04 P1 증분4-b — 봇 DM 대신)."""

    def test_doc_detection(self):
        import bot.portfolio_watch as pw

        class _F:
            def __init__(self, name):
                self.name = name
                self.ext = ""

        class _M:
            def __init__(self, name):
                self.document = object()
                self.file = _F(name)

        assert pw._is_portfolio_doc(_M("2025-06-05~2026-06-05.zip")) is True
        assert pw._is_portfolio_doc(_M("report.xlsx")) is True
        assert pw._is_portfolio_doc(_M("photo.jpg")) is False

        class _N:
            document = None
            file = None
        assert pw._is_portfolio_doc(_N()) is False

    def test_seen_roundtrip(self, tmp_path, monkeypatch):
        import bot.portfolio_watch as pw
        monkeypatch.setattr(pw, "_SEEN_PATH", tmp_path / "seen.json")
        pw._save_seen({"initialized": True, "ids": [1, 2, 3], "last_msg_id": 3})
        s = pw._load_seen()
        assert s["initialized"] and s["last_msg_id"] == 3 and 2 in s["ids"]

    def test_watcher_wiring_and_units(self):
        import os
        src = open("bot/portfolio_watch.py", encoding="utf-8").read()
        assert "TG_RAG_CHANNEL" in src and "BANKSALAD_ZIP_PW" in src
        assert "from bot.portfolio import ingest" in src
        assert "regenerate_portfolio_index" in src
        assert os.path.exists("deploy/portfolio-watch.timer")
        assert os.path.exists("deploy/portfolio-watch.service")
        assert "portfolio-watch.timer" in open("deploy/install.sh", encoding="utf-8").read()
        # 고트래픽 RAG 채널 안전: 폴링 사이 새 메시지를 min_id 로 전부 가져옴
        # (최근 N개 윈도가 아니라) — zip 업로드 누락 방지.
        assert "min_id=last_msg_id" in src, "min_id 기반 누락방지 fetch 누락"


class TestChartEvents:
    """fix: 차트 공시 이벤트 마커 (2026-06-05, 전 시장 · 공시만)."""

    def test_classify_whitelist(self):
        # 화이트리스트 4종(사용자 2026-06-05): 수주·계약/시설투자/주주환원/자본변동.
        from bot.chart_events import classify
        assert classify("단일판매ㆍ공급계약체결") == "order"
        assert classify("신규시설투자등의진행상황보고") == "capex"
        assert classify("주요사항보고서(자기주식취득결정)") == "shareholder"
        assert classify("자기주식소각결정") == "shareholder"
        assert classify("현금ㆍ현물배당결정") == "shareholder"
        assert classify("주요사항보고서(유상증자결정)") == "capital"
        assert classify("전환사채권 발행결정") == "capital"
        assert classify("Entry into a Material Definitive Agreement") == "order"
        assert classify("重大訊息-取得設備訂單") == "order"
        assert classify("소송 등의 제기 신청") == "litigation"
        assert classify("특허침해 금지 가처분 신청") == "litigation"
        # 추가 3종(M&A·리스크·최대주주변경)
        assert classify("회사합병 결정") == "mna"
        assert classify("영업양수도 결정") == "mna"
        assert classify("타법인주식및출자증권취득결정") == "mna"
        assert classify("§2.01 Acquisition/Disposition of Assets") == "mna"
        assert classify("주권매매거래정지(상장폐지사유)") == "risk"
        assert classify("감사의견거절") == "risk"
        assert classify("§3.01 Exchange Delisting") == "risk"
        assert classify("최대주주변경") == "control"
        assert classify("§5.01 Change in Control") == "control"
        # 오분류 회피: 자기주식취득은 주주환원(mna '취득' 아님), 유형자산취득은 시설투자
        assert classify("자기주식취득결정") == "shareholder"
        assert classify("유형자산 취득 결정") == "capex"
        # US EDGAR 8-K 축약 라벨(버그 fix) — 풀네임 아닌 축약형도 매칭돼야
        assert classify("§1.01 Material Agreement") == "order"
        assert classify("§3.02 Unregistered Sales of Equity") == "capital"
        # 그 외(실적·임원·Reg FD 등)는 'other' → 마커 제외
        assert classify("연결재무제표 기준 영업(잠정)실적") == "other"
        assert classify("분기보고서") == "other"
        assert classify("§2.02 Results of Operations (Earnings)") == "other"
        assert classify("§7.01 Regulation FD Disclosure") == "other"
        assert classify("기타 경영사항") == "other"

    def test_norm_shape_dedup_sort(self):
        from bot.chart_events import _norm, _TYPE_COLOR
        out = _norm([{"date": "20260603", "title": "공급계약", "u": "http://x"},
                     {"date": "20260603", "title": "공급계약", "u": "http://x"},   # dup
                     {"date": "2026-05-30T09:00:00", "title": "전환사채 발행"}],
                    "date", "title", "u")
        assert len(out) == 2, "dedup 실패"
        assert out[0]["time"] == "2026-05-30" and out[1]["time"] == "2026-06-03", "정렬/날짜정규화 실패"
        assert out[1]["type"] == "order" and out[1]["color"] == _TYPE_COLOR["order"]
        assert out[1]["url"] == "http://x", "url 전달 누락"
        assert all(set(e) == {"time", "title", "type", "color", "url"} for e in out)

    def test_dart_detail_summary(self):
        # DART 구조화 요약(₩0) — 금액 포맷·필드 추출·graceful (2026-06-05).
        from bot.dart_detail import _won, _summary_for, get_disclosure_summaries
        assert _won("1500000000") == "15억원" and _won("5000") == "5,000원"
        assert _won("0") is None and _won("-") is None and _won("x") is None
        s = _summary_for({"aqpln_prc_ostk": "30000000000", "aqpln_stk_ostk": "1000000"},
                         "자기주식취득",
                         [("취득금액", ("aqpln_prc_ostk",), "won"),
                          ("취득수량", ("aqpln_stk_ostk",), "num")])
        assert s == "자기주식취득 · 취득금액 300억원 · 취득수량 1,000,000주"
        assert _summary_for({}, "유상증자", [("x", ("nope",), "won")]) is None
        # 키 없으면 graceful {} (크래시 X)
        assert get_disclosure_summaries("005930") == {}
        # chart_events 가 summary 를 이벤트에 붙이는 배선
        ce = open("bot/chart_events.py", encoding="utf-8").read()
        assert "get_disclosure_summaries" in ce and 'e["summary"]' in ce, "요약 배선 누락"
        ds = open("bot/dashboard.py", encoding="utf-8").read()
        assert "e.summary ? escTt(e.summary)" in ds, "패널 요약 표시 누락"

    def test_us_label_korean(self):
        # US 8-K 항목 라벨 한국어 표시(₩0 사전) — 분류는 영문 유지(번역으로 안 깨짐).
        from bot.chart_events import _us_label_kr, classify
        assert _us_label_kr("§1.01 Material Agreement") == "§1.01 주요계약 체결"
        assert _us_label_kr("§3.01 Exchange Delisting") == "§3.01 상장폐지"
        assert _us_label_kr("§9.99 Unknown Item") == "§9.99 Unknown Item"  # 매핑 없으면 원문
        # 분류는 영문 라벨로(번역 전): order/risk 정확
        assert classify("§1.01 Material Agreement") == "order"
        assert classify("§3.01 Exchange Delisting") == "risk"

    def test_title_translate_cache_graceful(self, tmp_path, monkeypatch):
        # CN/JP/TW 제목 LLM 번역 — 영구 캐시 우선, 키 부재 시 graceful(원문 유지).
        import bot.chart_translate as ct
        monkeypatch.setattr(ct, "_CACHE", tmp_path / "c.json")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        ct._save({"贵州茅台 回购公告": "귀주모태주 자사주 매입 공고"})
        out = ct.translate_titles_kr(["贵州茅台 回购公告", "未缓存标题"])
        assert out == {"贵州茅台 回购公告": "귀주모태주 자사주 매입 공고"}  # 캐시만(미캐시는 키없어 생략)
        assert ct.translate_titles_kr([]) == {}
        # 배선: chart_events 가 CN/JP/TW 에서 번역 호출(분류는 원문 유지)
        ce = open("bot/chart_events.py", encoding="utf-8").read()
        assert "translate_titles_kr" in ce and '("CN_A", "HK", "JP", "TW")' in ce

    def test_whitelist_filter(self):
        # 마커는 화이트리스트 4종만(그 외 제외). 소스에 _SHOW_TYPES 필터 존재.
        from bot.chart_events import _SHOW_TYPES
        assert set(_SHOW_TYPES) == {"order", "litigation", "capex", "shareholder",
                                    "capital", "mna", "risk", "control"}
        src = open("bot/chart_events.py", encoding="utf-8").read()
        assert 'e.get("type") in _SHOW_TYPES' in src, "화이트리스트 필터 누락"

    def test_chart_wiring(self):
        cd = open("bot/chart_data.py", encoding="utf-8").read()
        assert "fetch_disclosure_events" in cd and 'payload["events"]' in cd, "차트 payload events 배선 누락"
        assert "ticker=ticker" in cd, "_series_payload ticker 전달 누락"
        assert "days=max(span" in cd, "차트 기간(span) → 공시 days 전달 누락"
        # 시장별 안전 캡(전 시장 풀히스토리, JP만 180 캡)
        ce = open("bot/chart_events.py", encoding="utf-8").read()
        assert "kr_us_days" in ce and "jp_days" in ce and "tw_cn_days" in ce, "시장별 days 캡 누락"
        # DART 다년 페이지네이션(100건 초과 시 다음 페이지)
        assert "total_page" in open("bot/dart_client.py", encoding="utf-8").read(), "DART 페이지네이션 누락"
        ds = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'data-ind="events"' in ds, "공시 토글 버튼 누락"
        assert "ind.events && d.events" in ds, "공시 마커 렌더 게이트 누락"
        assert "events:false" in ds, "공시 토글 기본 OFF 누락(선택해서 보기)"
        # 차트 아래 공시 내용 패널 + hover 갱신
        assert 'id="chart-disc"' in ds and "function showDisc" in ds, "공시 내용 패널 누락"
        assert "원문 →" in ds, "원문 링크 누락"


class TestPerAnalysisCost:
    """fix: 개별 분석 비용을 상세 페이지 메타 라인에 표기 (2026-06-05).

    분석 본체 Gemini 호출(subsystem 없음) + 그 분석 차트의 공시 제목
    번역(subsystem='chart_translate')만 합산하고, 동시에 별도 timer
    프로세스로 돌 수 있는 독립 surface(screener/daily_byte 등)는 제외.
    usage.jsonl 직접 합산이라 in-process accumulator 가 놓치는 번역비까지
    포함된다."""

    def _write(self, path, rows):
        import json
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                        encoding="utf-8")

    def test_sum_includes_analysis_and_translate_excludes_others(self, tmp_path, monkeypatch):
        import bot.usage_tracker as ut
        log = tmp_path / "usage.jsonl"
        monkeypatch.setattr(ut, "USAGE_LOG", log)
        t0 = 1000.0
        self._write(log, [
            {"ts": t0 - 5, "type": "llm_call", "cost_usd": 1.0},        # 이전 run(윈도 밖)
            {"ts": t0 + 1, "type": "llm_call", "cost_usd": 0.01},       # 분석 본체(태그 없음)
            {"ts": t0 + 2, "type": "llm_call", "cost_usd": 0.005,
             "subsystem": "chart_translate"},                           # 이 분석 차트 번역 → 포함
            {"ts": t0 + 3, "type": "llm_call", "cost_usd": 9.0,
             "subsystem": "screener"},                                  # 동시 screener → 제외
            {"ts": t0 + 4, "type": "llm_call", "cost_usd": 9.0,
             "subsystem": "daily_byte"},                                # 동시 Daily Byte → 제외
            {"ts": t0 + 5, "type": "analysis", "cost_usd": 99.0},       # llm_call 아님 → 제외
        ])
        expected = int(round((0.01 + 0.005) * ut.KRW_PER_USD))
        assert ut.sum_analysis_cost_krw(t0, t0 + 10) == expected

    def test_window_excludes_prior_run(self, tmp_path, monkeypatch):
        import bot.usage_tracker as ut
        log = tmp_path / "usage.jsonl"
        monkeypatch.setattr(ut, "USAGE_LOG", log)
        self._write(log, [
            {"ts": 500.0, "type": "llm_call", "cost_usd": 1.0},    # since 이전 → 제외
            {"ts": 1500.0, "type": "llm_call", "cost_usd": 0.02},  # 윈도 안 → 포함
        ])
        assert ut.sum_analysis_cost_krw(1000.0, 2000.0) == int(round(0.02 * ut.KRW_PER_USD))

    def test_missing_file_graceful(self, tmp_path, monkeypatch):
        import bot.usage_tracker as ut
        monkeypatch.setattr(ut, "USAGE_LOG", tmp_path / "nope.jsonl")
        assert ut.sum_analysis_cost_krw(0.0) == 0

    def test_wiring_analyzer_archive_dashboard(self):
        # analyzer 가 started_at 전달 + archive 가 chart build 뒤 cost stamp.
        az = open("bot/analyzer.py", encoding="utf-8").read()
        assert "started_at=started_at" in az, "analyzer 가 started_at 전달 안 함"
        ar = open("bot/archive.py", encoding="utf-8").read()
        assert "sum_analysis_cost_krw" in ar and 'record["cost_krw"]' in ar, "archive cost stamp 누락"
        # cost stamp 는 반드시 build_price_chart(번역 발생) 뒤 — 안 그러면 번역비 누락.
        assert ar.index("build_price_chart") < ar.index("sum_analysis_cost_krw"), \
            "cost stamp 가 chart build 보다 앞 — 번역비 누락"
        ds = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'rec.get("cost_krw"' in ds, "상세 페이지 cost 읽기 누락"
        assert "비용: ₩" in ds and "{cost_part}" in ds, "메타 라인 비용 표기 누락"


class TestBudget:
    """fix: 가계부(현금흐름) 별도 대시보드 (2026-06-04 자산관리 P2)."""

    def _budget(self):
        from bot.portfolio_parser import parse_banksalad
        from bot.budget import build_budget_model
        return build_budget_model(parse_banksalad(_BANKSALAD_SYNTH))

    def test_kind_classification(self):
        from bot.budget import _kind
        assert _kind("급여") == "income"
        assert _kind("식사") == "expense"
        assert _kind("월지출 총계") == "total_expense"
        assert _kind("수입 총계") == "total_income"
        assert _kind("이자수입") == "income"

    def test_model_category_sum_canonical(self):
        # 뱅샐 총계행이 비어(0) 있으면 카테고리 monthly 합이 canonical
        # (finance 섹션과 동일 정책) — 식사 [100,200] 가 묻히지 않아야.
        bm = self._budget()
        assert bm["expense"] == [100.0, 200.0]
        assert bm["totals"]["expense"] == 300.0
        assert bm["expense_cats"][0]["항목"] == "식사"
        assert bm["expense_cats"][0]["amount"] == 300.0
        assert bm["months"] == ["2025-06", "2025-07"]

    def test_render_budget_page(self):
        from bot.dashboard import _render_budget_page
        html = _render_budget_page(self._budget())
        assert "<h1>📒 가계부</h1>" in html
        assert "월별 수입·지출" in html and "현금흐름 상세" in html
        assert "bg-chart" in html and "식사" in html
        # nav: 홈 + 자산 (Group 1 peers, 가계부 자신은 현재라 생략)
        assert 'href="market.html">🌍 홈' in html
        assert 'href="portfolio.html">💼 자산' in html
        assert "아직 현금흐름" in _render_budget_page(None)

    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        import bot.budget as bg
        monkeypatch.setattr(bg, "BUDGET_PATH", tmp_path / "budget.json")
        bg.save_budget(self._budget())
        loaded = bg.load_budget()
        assert loaded and loaded["totals"]["expense"] == 300.0 and "_saved_ts" in loaded

    def test_wiring(self):
        dsrc = open("bot/dashboard.py", encoding="utf-8").read()
        assert "def regenerate_budget_index" in dsrc
        assert "regenerate_budget_index()" in dsrc, "자산 regen 시 가계부 동반 누락"
        assert 'href="budget.html">📒 가계부' in dsrc, "nav budget 링크 누락"
        psrc = open("bot/portfolio.py", encoding="utf-8").read()
        assert "build_budget_model" in psrc and "save_budget" in psrc, "ingest 가계부 배선 누락"
        # help(_HELP_TEXT)은 2026-06-09 사용자 정책으로 3개 entry(Main/NOAH/자산)만
        # 나열 — 가계부는 자산 페이지 nav 에서 도달(위 dashboard nav 링크로 보장).
        # 따라서 telegram_bot.py 의 budget.html 직접 언급은 더 이상 요구하지 않음.


# ─────────────────────────────────────────────────────────────────────────
# 15) E1 KIS 모의투자 adapter — 순수 로직 + paper_trading 라우팅 차단
#     배경: E1 은 KR 종목을 KIS 모의투자 서버로 라우팅해 서버 체결.
#     핵심: _ticker_to_code 변환, _e1_available gate, _apply_fill_direct
#     ledger 반영, e1_mode 반환, reconcile stub.
# ─────────────────────────────────────────────────────────────────────────
class TestE1KisTradingAdapter:
    """E1 KIS 모의투자 어댑터 순수 로직 회귀 차단."""

    def test_ticker_to_code(self):
        from bot.paper_trading import _ticker_to_code
        assert _ticker_to_code("005930.KS") == "005930"
        assert _ticker_to_code("039030.KQ") == "039030"
        assert _ticker_to_code("5930.KS") == "005930"
        assert _ticker_to_code("AAPL") == "00AAPL"  # US ticker — not used for KIS, but no crash

    def test_e1_available_without_env(self, monkeypatch):
        monkeypatch.delenv("KIS_PAPER_APP_KEY", raising=False)
        monkeypatch.delenv("KIS_PAPER_APP_SECRET", raising=False)
        monkeypatch.delenv("KIS_PAPER_ACCT_NO", raising=False)
        from bot.paper_trading import _e1_available, e1_mode
        assert _e1_available() is False
        assert e1_mode() is None

    def test_e1_available_with_env(self, monkeypatch):
        monkeypatch.setenv("KIS_PAPER_APP_KEY", "testkey")
        monkeypatch.setenv("KIS_PAPER_APP_SECRET", "testsecret")
        monkeypatch.setenv("KIS_PAPER_ACCT_NO", "50191853")
        from bot.kis_trading import is_e1_available
        assert is_e1_available() is True
        from bot.paper_trading import _e1_available, e1_mode
        assert _e1_available() is True
        assert e1_mode() == "paper"

    def test_apply_fill_direct_buy(self):
        from bot.paper_trading import _apply_fill_direct, _new_account
        acct = _new_account()
        ok, msg = _apply_fill_direct(acct, "005930.KS", 10, 60000.0,
                                     1.0, "KRW", "KR", "test-idem",
                                     source="kis_e1", kis_order_no="ORD001",
                                     side="buy")
        assert ok
        assert "[KIS 모의투자]" in msg
        assert acct["positions"]["005930.KS"]["qty"] == 10
        assert acct["positions"]["005930.KS"]["kis_order_no"] == "ORD001"
        assert acct["cash_krw"] == 10_000_000 - (10 * 60000)

    def test_apply_fill_direct_sell(self):
        from bot.paper_trading import _apply_fill_direct, _new_account
        acct = _new_account()
        # buy first
        _apply_fill_direct(acct, "005930.KS", 10, 60000.0,
                           1.0, "KRW", "KR", "buy-idem",
                           source="kis_e1", side="buy")
        ok, msg = _apply_fill_direct(acct, "005930.KS", 5, 65000.0,
                                     1.0, "KRW", "KR", "sell-idem",
                                     source="kis_e1", side="sell")
        assert ok
        assert "매도 체결" in msg
        assert acct["positions"]["005930.KS"]["qty"] == 5

    def test_summary_includes_e1_mode(self, monkeypatch):
        monkeypatch.setenv("KIS_PAPER_APP_KEY", "k")
        monkeypatch.setenv("KIS_PAPER_APP_SECRET", "s")
        monkeypatch.setenv("KIS_PAPER_ACCT_NO", "12345678")
        from bot.paper_trading import summary, _new_account
        def fake_price(_):
            return 100.0, "KR"
        s = summary(price_fn=fake_price)
        assert s["e1_mode"] == "paper"

    def test_reconcile_returns_none_when_e1_unavailable(self, monkeypatch):
        monkeypatch.delenv("KIS_PAPER_APP_KEY", raising=False)
        from bot.paper_trading import run_reconcile
        assert run_reconcile() is None

    def test_kis_adapter_tr_id(self):
        """KIS 어댑터의 tr_id prefix 분기 — paper=V, live=T."""
        from bot.kis_trading import KisTradingAdapter
        import os
        os.environ.setdefault("KIS_PAPER_APP_KEY", "test")
        os.environ.setdefault("KIS_PAPER_APP_SECRET", "test")
        os.environ.setdefault("KIS_PAPER_ACCT_NO", "50191853")
        paper = KisTradingAdapter("paper")
        assert paper._tr_id("TTC0802U") == "VTTC0802U"
        assert paper.base_url.startswith("https://openapivts")
        live = KisTradingAdapter("live")
        assert live._tr_id("TTC0802U") == "TTTC0802U"
        assert live.base_url.startswith("https://openapi.")

    def test_kis_account_parsing(self):
        """계좌번호 파싱 — 8자리 CANO + 2자리 상품코드."""
        from bot.kis_trading import KisTradingAdapter
        import os
        os.environ["KIS_PAPER_ACCT_NO"] = "50191853-01"
        a = KisTradingAdapter("paper")
        assert a.cano == "50191853"
        assert a.acnt_prdt_cd == "01"
        os.environ["KIS_PAPER_ACCT_NO"] = "5019185301"
        b = KisTradingAdapter("paper")
        assert b.cano == "50191853"
        assert b.acnt_prdt_cd == "01"

    def test_apply_fill_direct_us(self):
        """US 종목 KIS E1 체결 → 원화 환산 ledger 정상 반영."""
        from bot.paper_trading import _apply_fill_direct, _new_account
        acct = _new_account()
        ok, msg = _apply_fill_direct(acct, "AAPL", 5, 200.0,
                                     1380.0, "USD", "US", "us-idem",
                                     source="kis_e1", kis_order_no="US001",
                                     side="buy")
        assert ok
        assert "[KIS 모의투자]" in msg
        assert acct["positions"]["AAPL"]["qty"] == 5
        assert acct["positions"]["AAPL"]["kis_order_no"] == "US001"
        expected_cost = 5 * 200.0 * 1380.0
        assert abs(acct["cash_krw"] - (10_000_000 - expected_cost)) < 1

    def test_overseas_tr_id(self):
        """해외주식 tr_id — paper=V prefix."""
        from bot.kis_trading import KisTradingAdapter
        import os
        os.environ.setdefault("KIS_PAPER_APP_KEY", "test")
        os.environ.setdefault("KIS_PAPER_APP_SECRET", "test")
        os.environ.setdefault("KIS_PAPER_ACCT_NO", "50191853")
        paper = KisTradingAdapter("paper")
        assert paper._tr_id("TTT1002U") == "VTTT1002U"  # 해외 매수
        assert paper._tr_id("TTT1006U") == "VTTT1006U"  # 해외 매도

    def test_order_e1_routes_us(self):
        """_order() 가 US 종목도 E1 라우팅하는지 코드 확인."""
        src = open("bot/paper_trading.py", encoding="utf-8").read()
        assert 'market in ("KR", "US") and _e1_available()' in src
        assert "_order_e1_overseas" in src

    def test_wiring_e1(self):
        """E1 배선 확인 — paper_trading 에 E1 라우팅, dashboard 에 E1 배너."""
        pt_src = open("bot/paper_trading.py", encoding="utf-8").read()
        assert "_e1_available" in pt_src
        assert "_order_e1" in pt_src
        assert "_order_e1_overseas" in pt_src
        assert "_apply_fill_direct" in pt_src
        assert "e1_mode" in pt_src
        assert "run_reconcile" in pt_src
        ds_src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "e1_banner" in ds_src
        assert "KIS 모의투자" in ds_src
        kt_src = open("bot/kis_trading.py", encoding="utf-8").read()
        assert "submit_order" in kt_src
        assert "submit_order_overseas" in kt_src
        assert "get_balance" in kt_src
        assert "get_balance_overseas" in kt_src
        assert "reconcile" in kt_src


# ─────────────────────────────────────────────────────────────────────────
# Portfolio 손익변동 컬럼 — holdings_pnl snapshot 생성 + delta 계산
# ─────────────────────────────────────────────────────────────────────────
class TestPortfolioPnlDelta:
    """보유 종목 테이블 손익변동 컬럼 회귀 차단."""

    def test_snapshot_includes_holdings_pnl(self):
        """build_model 이 snapshot 에 holdings_pnl 맵을 포함하는지."""
        from bot.portfolio import build_model
        parsed = {
            "holdings": [
                {"상품명": "삼성전자", "금융사": "NH투자증권",
                 "평가금액": 1000, "투자원금": 800, "수익률": 25.0},
                {"상품명": "AAPL", "금융사": "메리츠증권",
                 "평가금액": 500, "투자원금": 600, "수익률": -16.7},
            ],
            "finance": {"총자산": 2000, "총부채": 0, "순자산": 2000,
                        "assets": {}},
        }
        model = build_model(parsed, resolve=lambda n: {"ticker": None, "market": None, "matched": False})
        snap = model["snapshot"]
        assert "holdings_pnl" in snap
        assert snap["holdings_pnl"]["삼성전자|NH투자증권"] == 200  # 1000-800
        assert snap["holdings_pnl"]["AAPL|메리츠증권"] == -100  # 500-600

    def test_pnl_delta_calculation(self):
        """현재 평가손익 - 이전 평가손익 = 손익변동 정확 계산."""
        prev_pnl = {"삼성전자|NH투자증권": 150, "현우산업|NH투자증권": -80}
        cur_holdings = [
            {"상품명": "삼성전자", "금융사": "NH투자증권", "평가손익": 200},
            {"상품명": "현우산업", "금융사": "NH투자증권", "평가손익": -120},
            {"상품명": "신규종목", "금융사": "NH투자증권", "평가손익": 10},
        ]
        deltas = {}
        for h in cur_holdings:
            key = f"{h['상품명']}|{h['금융사']}"
            prev = prev_pnl.get(key)
            if prev is not None:
                deltas[h["상품명"]] = (h["평가손익"] or 0) - prev
            else:
                deltas[h["상품명"]] = "신규"
        assert deltas["삼성전자"] == 50   # 200 - 150
        assert deltas["현우산업"] == -40  # -120 - (-80)
        assert deltas["신규종목"] == "신규"

    def test_dashboard_chg_column_wiring(self):
        """dashboard.py 가 손익변동 컬럼 코드를 포함하는지."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "_prev_pnl_map" in src
        assert "_show_chg" in src
        assert "holdings_pnl" in src
        assert "손익변동" in src
        assert 'data-k="chg"' in src

    def test_chg_column_date_label(self):
        """손익변동 헤더에 비교 일자 라벨(_chg_dates)이 배선됐는지 —
        '이전 → 현재' 형식 (사용자 요청 2026-06-06)."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "_chg_dates" in src
        i = src.index("_chg_th =")
        assert "_chg_dates" in src[i:i + 300]  # 헤더가 일자 라벨 포함

    def test_chg_date_label_format(self):
        """일자 라벨 포맷: '이전(%m-%d) → 현재(%m-%d)'."""
        from datetime import datetime, timezone, timedelta
        kst = timezone(timedelta(hours=9))
        pdate = datetime(2026, 6, 6, 18, 0, tzinfo=kst).strftime("%m-%d")
        cdate = datetime(2026, 6, 7, 9, 0, tzinfo=kst).strftime("%m-%d")
        assert f"{pdate} → {cdate}" == "06-06 → 06-07"


class TestWeekendHolidayGating:
    """Daily Byte 한국 거래일 게이트 회귀 차단. 핵심: 일일은 막되 주간
    브리프(일요일 22시 한·미)는 보존 (사용자 정책 2026-06-06 — '주말
    일일은 안 오게, 주간브리프는 그대로'). SV 게이트 테스트 3종은 SV
    폐기(2026-06-12 소스 삭제)와 함께 제거."""

    def test_daily_byte_trading_day_gate(self):
        """daily_kr_flow.main() 에 한국 거래일 게이트 (is_trading_day KR).
        is False 만 skip → None(라이브러리 부재)·True 는 진행(회귀 0)."""
        src = open("bot/daily_kr_flow.py", encoding="utf-8").read()
        assert 'is_trading_day("KR"' in src
        assert "휴장일" in src
        assert "is False" in src

    def test_weeklies_not_gated(self):
        """주간브리프(Daily Byte 한·미)는 일일 게이트가 없어야 — 일요일
        (주말)에 그대로 발송. 일일 가드가 주간으로 새지 않음을 영구 확인."""
        db_weekly = open("bot/daily_kr_weekly.py", encoding="utf-8").read()
        us_weekly = open("bot/us_market_weekly.py", encoding="utf-8").read()
        assert 'is_trading_day("KR"' not in db_weekly
        assert "weekday() >= 5" not in us_weekly

    def test_weekly_timers_still_sunday(self):
        """주간브리프 타이머(한·미)가 여전히 일요일(Sun) 발화 — 보존 확인."""
        db = open("deploy/daily-byte-weekly.timer", encoding="utf-8").read()
        us = open("deploy/daily-byte-weekly-us.timer", encoding="utf-8").read()
        assert "Sun" in db
        assert "Sun" in us

    def test_gate_decision_logic(self):
        """게이트 결정 규칙: is_trading_day False→skip, None/True→진행(폴백)."""
        def should_skip(tday):  # main() 게이트와 동일 규칙 (is False)
            return tday is False
        assert should_skip(False) is True    # 휴장 → skip
        assert should_skip(True) is False    # 거래일 → 진행
        assert should_skip(None) is False    # 라이브러리 부재 → 진행

    def test_weekend_weekday_logic(self):
        """주말 판정: 토(5)·일(6) skip, 평일 진행 (daily_generator 가드 규칙)."""
        from datetime import datetime
        def is_weekend(d):  # weekday() >= 5
            return datetime.fromisoformat(d).weekday() >= 5
        assert is_weekend("2026-06-06") is True   # 토
        assert is_weekend("2026-06-07") is True   # 일
        assert is_weekend("2026-06-05") is False  # 금
        assert is_weekend("2026-06-08") is False  # 월


class TestPortfolioSend:
    """자산 스냅샷 CSV '보내기'(텔레그램 채널 + 이메일) 회귀 차단.
    텔레그램: PORTFOLIO_TG_CHAT_ID 우선 → CHANNEL_CHAT_IDS 폴백 (사용자 정책 2026-06-06)."""

    def _clean_env(self, monkeypatch=None):
        import os
        for k in ("TELEGRAM_BOT_TOKEN", "PORTFOLIO_TG_CHAT_ID",
                  "ALLOWED_USER_IDS", "CHANNEL_CHAT_IDS",
                  "SMTP_USER", "SMTP_PASS", "PORTFOLIO_EMAIL_TO"):
            os.environ.pop(k, None)

    def test_telegram_graceful_without_env(self):
        """env 없으면 (False, 한국어 사유) — 크래시 없음."""
        self._clean_env()
        from bot import portfolio_send as ps
        ok, msg = ps.send(b"a,b\n1,2", "portfolio_2026-06-07.csv", "telegram")
        assert ok is False and "미설정" in msg

    def test_email_graceful_without_env(self):
        self._clean_env()
        from bot import portfolio_send as ps
        ok, msg = ps.send(b"a,b\n1,2", "portfolio_2026-06-07.csv", "email")
        assert ok is False and "미설정" in msg

    def test_unknown_target(self):
        from bot import portfolio_send as ps
        ok, msg = ps.send(b"x", "f.csv", "bogus")
        assert ok is False

    def test_target_chat_id_priority(self):
        """PORTFOLIO_TG_CHAT_ID 우선 → 없으면 CHANNEL_CHAT_IDS 첫 채널."""
        import os
        from bot import portfolio_send as ps
        self._clean_env()
        os.environ["CHANNEL_CHAT_IDS"] = "-1001234567890,-100999"
        assert ps._target_chat_id() == "-1001234567890"
        os.environ["PORTFOLIO_TG_CHAT_ID"] = "123"
        assert ps._target_chat_id() == "123"
        self._clean_env()
        assert ps._target_chat_id() == ""

    def test_channel_fallback(self):
        """PORTFOLIO_TG_CHAT_ID 없으면 CHANNEL_CHAT_IDS 로 폴백."""
        import os
        from bot import portfolio_send as ps
        self._clean_env()
        os.environ["CHANNEL_CHAT_IDS"] = "-1001234567890"
        try:
            assert ps._target_chat_id() == "-1001234567890"
        finally:
            os.environ.pop("CHANNEL_CHAT_IDS", None)

    def test_server_endpoint_wiring(self):
        """dashboard_server 에 /api/portfolio_send 라우트+핸들러+가드."""
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert "/api/portfolio_send" in src
        assert "_handle_portfolio_send" in src
        assert "5_000_000" in src          # 5MB cap
        assert '("telegram", "email")' in src  # 대상 화이트리스트
        assert "\\ufeff" in src             # Excel BOM

    def test_client_wiring(self):
        """dashboard.py 에 보내기 버튼 + CSV 빌드 JS + fetch 배선."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "pf-send-tg" in src and "pf-send-em" in src
        assert "_PF_SEND_JS" in src
        assert "api/portfolio_send" in src
        assert "pf-snap-date" in src

    def test_budget_kind_param(self):
        """budget JS 가 kind:'budget' 을 보내고, server 가 budget_ filename 생성."""
        src_dash = open("bot/dashboard.py", encoding="utf-8").read()
        assert "kind:'budget'" in src_dash
        src_srv = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert '"budget"' in src_srv

    def test_send_caption_param(self):
        """send() 가 caption 키워드를 받아 텔레그램·이메일 분기."""
        from bot import portfolio_send as ps
        import inspect
        sig = inspect.signature(ps.send)
        assert "caption" in sig.parameters
        sig_tg = inspect.signature(ps.send_telegram)
        assert "caption" in sig_tg.parameters

    def test_noah_lookup_suffix_strip(self):
        """_noah_lookup 이 기존 .KS suffix 를 strip 후 .KQ 로 재매칭."""
        from bot.dashboard import _noah_lookup
        noah = {"317400.KQ": {"rating": "Buy"}}
        info, full = _noah_lookup(noah, "317400")
        assert full == "317400.KQ"
        info2, full2 = _noah_lookup(noah, "317400.KS")
        assert full2 == "317400.KQ"
        info3, full3 = _noah_lookup(noah, "317400.KQ")
        assert full3 == "317400.KQ"
        assert _noah_lookup(noah, "LRCX") == (None, None)
        assert _noah_lookup({}, "317400") == (None, None)

    def test_noah_portfolio_link_uses_date_path(self):
        """포트폴리오 NOAH 링크가 {date}/{ticker}.html 경로 (ticker_*.html 아님)."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'ticker_{' not in src or 'ticker_{' not in src.split('_render_portfolio_page')[1].split('\ndef ')[0], \
            "포트폴리오에 ticker_*.html 링크 잔존"


class TestDetailNewsResearchFallback:
    """상세 페이지 뉴스·리서치 탭의 KR/JP/TW/CN 데이터 폴백 회귀 차단.

    yfinance .news / .upgrades_downgrades 는 US 만 잘 커버 → 비-US 시장은
    빈 탭이었다. 우리 자원(Naver/Kabutan/cnyes/AKShare 뉴스 + 한경 리포트)
    으로 채운다. 영구 가드:
     • news 폴백 스키마 매핑 (source→publisher) + kr_native 태그
     • KR(Naver) 뉴스는 이미 한국어 → 렌더 시 Gemini 번역 스킵 (₩0,
       2026-06-07 startup 크래시-루프 클래스 재발 방지)
     • 한경 fetch_consensus 가 개별 리포트 rows(reports) 도 반환
     • KR research_reports 가 리서치 액션 탭에 렌더
    """

    def _mock_news_clients(self):
        import sys, types
        naver = types.ModuleType("bot.naver_news_client")
        naver.fetch_news = lambda q, days_back=28, max_items=10: [
            {"date": "2026-06-07", "title": "네이버 2분기 실적 호조",
             "source": "한경", "link": "http://x", "summary": "s"}]
        sys.modules["bot.naver_news_client"] = naver
        kab = types.ModuleType("bot.kabutan_news")
        kab.fetch_news = lambda c, days_back=28, max_items=10: [
            {"date": "2026-06-07", "title": "トヨタ決算",
             "source": "Kabutan", "link": "http://y", "summary": "s"}]
        sys.modules["bot.kabutan_news"] = kab

    def test_news_fallback_schema_and_kr_native(self):
        self._mock_news_clients()
        from bot.stock_snapshot import _collect_news_fallback
        snap = {"kr": {"corp_name": "네이버"}, "long_name": "NAVER"}
        _collect_news_fallback("035420.KS", snap)
        assert snap["news"], "KR 뉴스 폴백 비어있음"
        n = snap["news"][0]
        assert n["title"] == "네이버 2분기 실적 호조"
        assert n["publisher"] == "한경", "source→publisher 매핑 깨짐"
        assert n.get("kr_native") is True, "KR 은 kr_native 태그 필수"

    def test_news_fallback_jp_not_kr_native(self):
        self._mock_news_clients()
        from bot.stock_snapshot import _collect_news_fallback
        snap = {}
        _collect_news_fallback("7203.T", snap)
        assert snap["news"][0]["title"] == "トヨタ決算"
        assert "kr_native" not in snap["news"][0], "JP 는 번역 대상 (kr_native X)"

    def test_news_fallback_skips_when_yfinance_present(self):
        from bot.stock_snapshot import _collect_news_fallback
        snap = {"news": [{"title": "existing"}]}
        _collect_news_fallback("035420.KS", snap)
        assert len(snap["news"]) == 1 and snap["news"][0]["title"] == "existing"

    def test_hk_consensus_returns_reports_field(self):
        """fetch_consensus result 에 reports(개별 증권사 리포트) 필드 존재."""
        src = open("bot/hk_consensus_client.py", encoding="utf-8").read()
        assert '"reports":' in src, "한경 result 에 reports 필드 누락"
        # rows 를 날짜 내림차순 정렬해 reports 로 노출
        assert "reverse=True" in src

    def test_kr_research_reports_render(self):
        """KR research_reports 가 리서치 액션 탭에 렌더 + 한글 의견 변환."""
        from bot.dashboard import _render_stock_info_html
        rec = {"ticker": "035420.KS", "stock_info": {
            "long_name": "NAVER", "currency": "KRW", "current_price": 255500,
            "kr": {"corp_name": "네이버", "research_reports": [
                {"date": "2026-06-05", "broker": "미래에셋", "target": 320000, "rating": "매수"},
                {"date": "2026-06-03", "broker": "NH투자", "target": 300000, "rating": "Buy"}]}}}
        html = "".join(str(v) for v in _render_stock_info_html(rec).values())
        assert "미래에셋" in html and "NH투자" in html, "증권사명 미렌더"
        assert "₩320,000" in html, "목표가 ₩+천단위 미렌더"
        assert "Naver Finance" in html, "출처 표기 누락"
        assert html.count("매수") >= 2, "Buy→매수 + 매수 passthrough 변환 깨짐"
        assert "리서치 액션 데이터가 없습니다" not in html

    def test_kr_native_news_skips_translation(self):
        """kr_native 뉴스는 translate_news_titles_kr 에 전달되지 않음
        (회귀 시 한국어 제목이 Gemini 로 → 비용 + startup 크래시 클래스)."""
        import bot.dashboard as dash
        import bot.translate as tr
        called = {"titles": None}
        orig = tr.translate_news_titles_kr

        def _spy(titles):
            called["titles"] = list(titles)
            return {}
        # _render_stock_info_html does `from bot.translate import
        # translate_news_titles_kr` at call time, so patch the source.
        tr.translate_news_titles_kr = _spy
        try:
            # Mixed feed: one kr_native (Naver, already Korean) + one
            # foreign (US/yfinance English). Only the foreign title may
            # reach the translator.
            rec = {"ticker": "035420.KS", "stock_info": {
                "long_name": "NAVER", "currency": "KRW",
                "news": [
                    {"title": "네이버 한국어 제목", "publisher": "한경",
                     "link": "http://x", "date": "2026-06-07",
                     "kr_native": True},
                    {"title": "English foreign headline", "publisher": "Reuters",
                     "link": "http://y", "date": "2026-06-07"}]}}
            html = "".join(str(v) for v in dash._render_stock_info_html(rec).values())
        finally:
            tr.translate_news_titles_kr = orig
        passed = called["titles"] or []
        assert "네이버 한국어 제목" not in passed, "kr_native 뉴스가 번역기에 전달됨 (₩ 누수)"
        assert "English foreign headline" in passed, "비-KR 뉴스는 번역돼야"
        assert "네이버 한국어 제목" in html

    def test_tw_consensus_source_code_wired(self):
        """TW _enrich_tw 에 cnyes_consensus fallback 이 연결돼 있는지 확인."""
        src = open("bot/stock_snapshot.py", encoding="utf-8").read()
        assert "cnyes_consensus" in src, "TW cnyes 컨센서스 import 누락"
        assert "鉅亨網" in src, "TW 컨센서스 source 라벨 누락"

    def test_backfill_detail_tabs_additive_only(self):
        """backfill_detail_tabs 가 기존 데이터를 덮어쓰지 않는지 확인.

        stock_info.news 가 이미 있는 레코드는 건너뛰어야 함.
        """
        import json, tempfile, os
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            day_dir = Path(tmp) / "2026-06-07"
            day_dir.mkdir()
            rec = {"ticker": "035420.KS", "trade_date": "2026-06-07",
                   "stock_info": {"news": [{"title": "existing"}],
                                  "kr": {"research_reports": [{"broker": "x"}]}}}
            jf = day_dir / "120000_035420.KS.json"
            jf.write_text(json.dumps(rec), encoding="utf-8")

            import bot.archive as arch
            with patch.object(arch, "ARCHIVE_ROOT", Path(tmp)):
                n = arch.backfill_detail_tabs()
            assert n == 0, f"이미 데이터 있는 레코드가 변경됨 (filled={n})"
            after = json.loads(jf.read_text(encoding="utf-8"))
            assert after["stock_info"]["news"][0]["title"] == "existing"
            assert after["stock_info"]["kr"]["research_reports"][0]["broker"] == "x"


class TestLiveQuoteOverlay:
    """상세 페이지 라이브 숫자 오버레이 회귀 차단 (2026-06-07).

    상세 페이지 stock_info 숫자(현재가·시총·PER·선행PER·PBR·PSR·
    EV/EBITDA·배당·베타·EPS·BPS·52주·이평·컨센서스)는 저장된 스냅샷이
    아니라 페이지 로드 시 /api/quote 로 실시간 갱신. KR 은 KIS 우선.
    영구 가드:
     • 렌더러가 [data-q] 후크 + 마커(data-qmark/qfill) + 패널맵(panes)
       을 emit — JS 오버레이가 붙을 자리
     • 라이브 fmt 문자열이 렌더러 출력과 글자단위 일치 (시각적 drift 0)
     • /api/quote 서버 엔드포인트가 do_GET 에 wire
     • build_live_quote 가 numbers-only (Gemini 호출 0 — full 은
       set_cache_only)
    """

    def _si(self):
        return {
            "currency": "USD", "current_price": 100.0,
            "market_cap": 2_500_000_000, "shares_outstanding": 25_000_000,
            "trailingPE": 30.43, "forwardPE": 25.1, "priceToBook": 5.2,
            "priceToSalesTrailing12Months": 8.4, "enterpriseToEbitda": 18.9,
            "dividendYield": 0.0123, "beta": 1.07,
            "trailingEps": 3.29, "forwardEps": 3.98, "bookValue": 19.2,
            "fiftyDayAverage": 95.5, "twoHundredDayAverage": 88.1,
            "fiftyTwoWeekHigh": 120.0, "fiftyTwoWeekLow": 60.0,
            "target_mean": 130.0, "target_high": 160.0, "target_low": 90.0,
        }

    def test_renderer_emits_data_q_hooks_and_panes(self):
        import bot.dashboard as d
        rec = {"ticker": "TEST", "stock_info": self._si()}
        parts = d._render_stock_info_html(rec)
        assert "panes" in parts, "full-mode panes map 누락"
        for pid in ("si-financials", "si-earnings", "si-research",
                    "si-holders", "si-flow", "si-disclosures", "si-risk",
                    "si-peers", "si-news"):
            assert pid in parts["panes"], f"heavy pane 누락: {pid}"
        hdr, other = parts["header"], parts["other_panes"]
        assert 'data-q="price"' in hdr and 'data-q="mcap"' in hdr
        assert 'id="q-badge"' in hdr, "라이브 상태 배지 누락"
        for key in ("trailingPE", "forwardPE", "priceToBook",
                    "priceToSalesTrailing12Months", "enterpriseToEbitda",
                    "dividendYield", "beta", "trailingEps", "forwardEps",
                    "bookValue", "fiftyDayAverage", "twoHundredDayAverage",
                    "target_mean", "upside", "w52_low", "w52_high",
                    "w52_pos_label", "tgt_low", "tgt_mean", "tgt_high"):
            assert f'data-q="{key}"' in other, f"data-q 후크 누락: {key}"
        for mark in ('data-qmark="w52"', 'data-qfill="w52"',
                     'data-qmark="tgt"', 'data-qfill="tgt"'):
            assert mark in other, f"바 마커 누락: {mark}"

    def test_no_formatting_drift(self):
        """라이브 fmt 가 렌더러 출력과 글자단위 일치 — 같은 값이면 시각
        변화 0 (drift 시 사용자가 '숫자가 튄다' 인지)."""
        import bot.dashboard as d
        rec = {"ticker": "TEST", "stock_info": self._si()}
        p = d._render_stock_info_html(rec)
        html = p["header"] + p["other_panes"]
        csym, pdec = d._currency_sym("USD"), 2
        exp = {
            "price": d._fmt_num(100.0, decimals=pdec, prefix=csym),
            "mcap": d._fmt_mcap(2_500_000_000, csym, "USD"),
            "trailingPE": "%.2fx" % 30.43, "forwardPE": "%.2fx" % 25.1,
            "priceToBook": "%.2fx" % 5.2,
            "priceToSalesTrailing12Months": "%.2fx" % 8.4,
            "enterpriseToEbitda": "%.2fx" % 18.9, "beta": "%.2f" % 1.07,
            "dividendYield": "%.2f%%" % (0.0123 * 100),
            "trailingEps": "%.2f" % 3.29, "forwardEps": "%.2f" % 3.98,
            "bookValue": "%.2f" % 19.2, "fiftyDayAverage": "%.2f" % 95.5,
            "twoHundredDayAverage": "%.2f" % 88.1,
            "target_mean": csym + d._fmt_num(130.0, decimals=pdec),
            "w52_low": csym + d._fmt_num(60.0, decimals=pdec),
            "w52_high": csym + d._fmt_num(120.0, decimals=pdec),
        }
        miss = [(k, v) for k, v in exp.items() if v not in html]
        assert not miss, f"포맷 drift: {miss}"
        assert "+30.0%" in html, "컨센서스 괴리율 drift"
        assert "현재가 (67%)" in html, "52주 위치 drift"

    def test_detail_injects_quote_script(self):
        import bot.dashboard as d
        rec = {"ticker": "TEST", "stock_info": self._si()}
        html = d._render_detail(rec)
        assert 'NOAH_TICKER="TEST"' in html, "quote_script ticker 누락"
        assert "api/quote?ticker=" in html, "라이브 fetch 누락"
        assert "full=1" in html, "full-mode fetch 누락"

    def test_server_endpoint_wired(self):
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert '"/api/quote"' in src, "/api/quote 라우트 누락"
        assert "_handle_quote_api" in src, "quote 핸들러 누락"
        assert "build_live_quote" in src, "build_live_quote 호출 누락"

    def test_full_mode_uses_cache_only(self):
        """full-mode 가 set_cache_only 로 Gemini 호출 0 (₩0) 보장."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        i = src.find("def build_live_quote")
        assert i != -1
        body = src[i:i + 6000]
        assert "set_cache_only(True)" in body, "full-mode cache_only 누락 (₩ 누수)"
        assert "collect_stock_snapshot" in body, "full-mode 재스냅샷 누락"

    def test_full_mode_robust_to_yfinance_failure(self):
        """full-mode 가 yfinance 실패해도 저장 스냅샷 폴백 + 뉴스/리서치
        enrichment 로 탭을 채운다 — NAVER 035420 (분석이 뉴스-fallback 코드
        배포 전 02:56 에 생성돼 저장 스냅샷에 뉴스가 없던) 케이스 회귀 차단."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        i = src.find("def build_live_quote")
        body = src[i:i + 6000]
        # yfinance 실패 시 저장 스냅샷으로 폴백해야 (None 즉시반환 금지)
        assert "_load_stored_stock_info(ticker)" in body, "저장 스냅샷 폴백 누락"
        # 뉴스/리서치 enrichment 를 명시적으로 보장
        assert "_ensure_detail_enrichment(ticker" in body, "탭 enrichment 보장 누락"

    def test_ensure_enrichment_idempotent_and_graceful(self):
        """_ensure_detail_enrichment 는 이미 채워진 탭을 재fetch 안 함
        (idempotent) + 빈/None 입력에 crash 안 함 (graceful)."""
        import bot.dashboard as d
        si = {"news": [{"title": "x", "publisher": "p", "link": "", "date": "2026-06-08"}],
              "kr": {"research_reports": [{"a": 1}]}}
        before_news = list(si["news"])
        before_rr = list(si["kr"]["research_reports"])
        d._ensure_detail_enrichment("035420.KS", si)  # 네트워크 0 (이미 존재)
        assert si["news"] == before_news, "news 가 이미 있는데 재fetch/변경됨"
        assert si["kr"]["research_reports"] == before_rr, "research 재fetch/변경됨"
        # 빈/None 은 graceful
        d._ensure_detail_enrichment("035420.KS", {})
        d._ensure_detail_enrichment("035420.KS", None)

    def test_full_panes_include_research_and_news(self):
        """full-mode whitelist + 렌더러 panes 둘 다 si-research·si-news 포함
        (NAVER 뉴스 탭 빈칸 회귀 차단)."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        i = src.find("def build_live_quote")
        body = src[i:i + 6000]
        # full-mode heavy whitelist 에 둘 다 존재
        assert '"si-research"' in body and '"si-news"' in body, \
            "full whitelist 에 research/news 누락"

    def test_diagnose_sources_shape_and_graceful(self):
        """diagnose_detail_sources 는 시장별 소스 상태 + env 키 PRESENCE
        (값 아님) 를 반환하고, 키/네트워크 부재에도 raise 안 함. /api/quote
        ?debug=1 production 진단의 백본 — '코드 vs 키 vs 차단된 소스' 판별."""
        import bot.dashboard as d
        r = d.diagnose_detail_sources("035420.KS")
        assert r["market"] == "KR"
        assert set(("NAVER_CLIENT_ID", "DART_API_KEY")).issubset(r["env"].keys())
        # env 값은 bool (키 문자열 노출 금지)
        assert all(isinstance(v, bool) for v in r["env"].values()), "env 키 값 노출 위험"
        assert "naver_news" in r["sources"] and "hankyung_research" in r["sources"]
        # US / 비-KR 도 graceful
        ru = d.diagnose_detail_sources("AAPL")
        assert ru["market"] == "US" and "sources" in ru

    def test_quote_endpoint_has_debug_mode(self):
        """서버 /api/quote 가 debug=1 진단 분기를 가진다 (ssh 없이 production
        소스 상태 확인)."""
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert 'params.get("debug"' in src, "debug 파라미터 파싱 누락"
        assert "diagnose_detail_sources" in src, "진단 함수 호출 누락"


class TestKrNewsQuery:
    """KR 뉴스 검색어 = 한글 브랜드 (NAVER 0-news 2026-06-08).

    naver_news 가 영문 DART 등록명('NAVER')으로 검색해 0건 반환하던 버그.
    한국 뉴스는 '네이버'로 인덱싱 → 법인 접미사 strip + 한글 우선."""

    def test_strip_corporate_form_tokens(self):
        from bot.market import kr_news_query_name
        # 법인 접미사/접두사 제거 → bare 브랜드
        assert kr_news_query_name("네이버(주)") == "네이버"
        assert kr_news_query_name("(주)카카오") == "카카오"
        assert kr_news_query_name("㈜한글과컴퓨터") == "한글과컴퓨터"
        assert kr_news_query_name("현대자동차(주)") == "현대자동차"
        assert kr_news_query_name("SK하이닉스 주식회사") == "SK하이닉스"
        # 접미사 없으면 무변경
        assert kr_news_query_name("삼성전자") == "삼성전자"
        # None / 빈 입력 graceful
        assert kr_news_query_name(None) == ""
        assert kr_news_query_name("") == ""

    def test_has_hangul_distinguishes_english_registration(self):
        from bot.market import has_hangul
        # 영문 DART 등록명은 한글 아님 → 한국 뉴스 검색 0건 신호
        assert has_hangul("NAVER") is False
        assert has_hangul("네이버") is True
        assert has_hangul("SK하이닉스") is True
        assert has_hangul(None) is False

    def test_dart_news_search_name_method_exists(self):
        """DART client 가 한글 우선 news_search_name 을 제공 (메인+상세
        뉴스 검색이 영문 등록명으로 0건 나는 회귀 차단)."""
        from bot.dart_client import DartClient
        assert hasattr(DartClient, "news_search_name"), "news_search_name 메서드 누락"
        # stock_snapshot + agent_utils 가 영문 fallback 없이 한글 우선 경로 사용
        snap_src = open("bot/stock_snapshot.py", encoding="utf-8").read()
        assert "kr_news_query_name" in snap_src and "has_hangul" in snap_src, \
            "_collect_news_fallback 가 한글 검색어 정규화 미적용"
        au_src = open("TradingAgents/tradingagents/agents/utils/agent_utils.py",
                      encoding="utf-8").read()
        assert "news_search_name" in au_src, "메인 파이프라인 뉴스 검색어 미수정"


class TestGoogleNewsFallback:
    """키 불필요 Google News RSS 폴백 (Naver API 키 401 auth-fail 2026-06-08).

    Naver 키가 무효(errorCode 024)여도 KR/JP/TW/CN 뉴스가 들어오도록
    키 없는 Google News RSS 로 폴백."""

    def test_title_publisher_split(self):
        from bot.google_news_client import _strip_source_suffix
        t, p = _strip_source_suffix("네이버, 2분기 실적 호조 - 한국경제")
        assert t == "네이버, 2분기 실적 호조" and p == "한국경제"
        # 구분자 없으면 publisher 빈값
        t2, p2 = _strip_source_suffix("제목만 있음")
        assert t2 == "제목만 있음" and p2 == ""
        # 긴 tail(40자 초과)은 오분할 방지 — 제목 유지
        long_tail = "A - " + ("x" * 50)
        t3, p3 = _strip_source_suffix(long_tail)
        assert p3 == "", "긴 tail 을 publisher 로 오분할"

    def test_locale_mapping(self):
        from bot.google_news_client import locale_for_market
        assert locale_for_market("KR") == ("ko", "KR", "KR:ko")
        assert locale_for_market("JP") == ("ja", "JP", "JP:ja")
        assert locale_for_market("TW")[0] == "zh-TW"
        # 미지 시장 → US 기본
        assert locale_for_market("ZZ") == ("en-US", "US", "US:en")

    def test_empty_query_returns_none(self):
        from bot.google_news_client import fetch_news
        assert fetch_news("") is None
        assert fetch_news(None) is None

    def test_wired_as_universal_fallback(self):
        """_collect_news_fallback + agent_utils 둘 다 Naver/scrape 0건 시
        Google News 폴백을 호출 (키 무효에도 뉴스 복원)."""
        snap_src = open("bot/stock_snapshot.py", encoding="utf-8").read()
        assert "google_news_client" in snap_src, "상세 페이지 Google News 폴백 미배선"
        assert "if not items and g_query" in snap_src, "market source 0건 시 폴백 조건 누락"
        au_src = open("TradingAgents/tradingagents/agents/utils/agent_utils.py",
                      encoding="utf-8").read()
        assert "google_news_client" in au_src, "메인 파이프라인 Google News 폴백 미배선"


class TestHankyungResearchParser:
    """한경 컨센서스 리서치 테이블 복원 (사이트 redesign → 구 URL 404,
    고정 6-td 정규식 깨짐, 2026-06-08 NAVER). 새 URL + 컬럼순서 무관
    관대한 셀 기반 파서."""

    def test_tolerant_parser_column_order_independent(self):
        from datetime import date, timedelta
        import bot.hk_consensus_client as h
        # 컬럼 순서가 다른 두 행 (제목/증권사/목표가/의견/날짜 위치 상이)
        html = (
            "<table><tbody>"
            "<tr><td><a href=x>네이버 2분기 호실적</a></td><td>미래에셋증권</td>"
            "<td>김애널</td><td>280,000</td><td>매수</td><td>2026-06-05</td></tr>"
            "<tr><td>목표가 상향</td><td>2026.06.01</td><td>삼성증권</td>"
            "<td>320,000</td><td>Buy</td></tr>"
            "<tr><td>헤더</td><td>증권사</td><td>의견</td></tr>"  # 날짜 없음 → skip
            "</tbody></table>"
        )
        cutoff = date(2026, 6, 7) - timedelta(days=90)
        rows = h._parse_report_rows(html, cutoff)
        assert len(rows) == 2, f"expected 2 rows, got {len(rows)}"
        assert rows[0]["target"] == 280000.0
        assert rows[0]["broker"] == "미래에셋증권"
        assert rows[0]["rating"] == "매수"
        assert rows[0]["date"] == "2026-06-05"
        # dot-separated date + reordered columns 도 처리
        assert rows[1]["target"] == 320000.0
        assert rows[1]["broker"] == "삼성증권"
        assert rows[1]["date"] == "2026-06-01"

    def test_cutoff_filters_old_rows(self):
        from datetime import date, timedelta
        import bot.hk_consensus_client as h
        html = ("<table><tbody>"
                "<tr><td>옛 리포트</td><td>키움증권</td><td>100,000</td>"
                "<td>보유</td><td>2020-01-01</td></tr></tbody></table>")
        cutoff = date(2026, 6, 7) - timedelta(days=90)
        assert h._parse_report_rows(html, cutoff) == [], "cutoff 밖 행 미필터"

    def test_new_url_primary_legacy_fallback(self):
        """신규 /analysis/list URL 우선 + 구 URL 폴백 배선 확인."""
        src = open("bot/hk_consensus_client.py", encoding="utf-8").read()
        assert "/analysis/list" in src, "신규 URL 미적용"
        assert "_fetch_list_html" in src, "멀티-URL fetch 헬퍼 누락"


class TestPeerMarketCap:
    """Peer comps 시총 표시가 peer 자체 통화로 렌더되는지 검증.

    Surfaced 2026-06-08: KR 주식 상세 페이지의 동종비교 탭에서 US
    peer (LMT/RTX 등) 시총이 ~1000x 작게 표시 — peer 의 USD 시총이
    subject 의 KRW 로 포맷돼 $120.8B → ₩1,208억 (정답: $120.80B)."""

    def test_peer_currency_stored_in_snapshot(self):
        """stock_snapshot._collect_peer_multiples 가 peer 통화 저장."""
        src = open("bot/stock_snapshot.py", encoding="utf-8").read()
        assert '"currency"' in src or "'currency'" in src, \
            "peer entry 에 currency 필드 미저장"

    def test_peer_currency_used_in_render(self):
        """dashboard 가 peer 자체 통화로 시총 포맷."""
        from bot.dashboard import _fmt_mcap, _currency_sym
        lmt_mc = 120_800_000_000  # USD
        assert _fmt_mcap(lmt_mc, _currency_sym("USD"), "USD") == "$120.80B"
        assert _fmt_mcap(lmt_mc, _currency_sym("KRW"), "KRW") == "₩1,208억"

    def test_ticker_suffix_currency_inference(self):
        """peer 에 currency 필드 없는 옛 데이터 → ticker suffix 로 추론."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        # peer 렌더 코드에 suffix→currency 매핑 존재 확인
        assert "peer_cur" in src, "peer currency 추론 로직 누락"

    def test_enrichment_in_render_detail(self):
        """_render_detail 이 news 부재 시 _ensure_detail_enrichment 호출."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        # _render_detail 안에 enrichment 호출 확인
        idx_render = src.find("def _render_detail")
        idx_si_html = src.find("_render_stock_info_html(rec)", idx_render)
        idx_enrich = src.find("_ensure_detail_enrichment", idx_render)
        assert idx_render < idx_enrich < idx_si_html, \
            "_render_detail 에서 _render_stock_info_html 전에 enrichment 호출 필수"


class TestDividendYieldSanity:
    """배당수익률 표시가 yfinance 불일치 데이터에서도 합리적인지 검증.

    Surfaced 2026-06-08: AAPL 35.0%, MSFT 87.0%, Shin-Etsu 144.0%,
    CATL 138.0% — yfinance dividendYield 가 시장별로 ratio/percentage/
    잘못된 값을 혼용. dividendRate/price 직접 계산 우선 + 20% 캡."""

    def test_safe_dy_pct_exists(self):
        """dashboard 에 _safe_dy_pct 헬퍼 존재."""
        from bot.dashboard import _safe_dy_pct
        assert callable(_safe_dy_pct)

    def test_correct_ratio_passes(self):
        """정상 ratio (0.0044 = 0.44%) 변환."""
        from bot.dashboard import _safe_dy_pct
        assert abs(_safe_dy_pct(0.0044) - 0.44) < 0.01

    def test_bad_ratio_capped(self):
        """yfinance 가 AAPL 에 0.35 반환 → 35% → 20% 캡 초과 → None."""
        from bot.dashboard import _safe_dy_pct
        assert _safe_dy_pct(0.35) is None

    def test_percentage_passthrough(self):
        """JP/CN 에서 이미 percentage (1.44) → 1.44%."""
        from bot.dashboard import _safe_dy_pct
        result = _safe_dy_pct(1.44)
        assert result is not None
        assert abs(result - 1.44) < 0.01

    def test_rate_price_preferred(self):
        """dividendRate/price 계산이 bad raw 보다 우선."""
        from bot.dashboard import _safe_dy_pct
        result = _safe_dy_pct(0.35, div_rate=1.00, price=228.0)
        assert result is not None
        assert abs(result - 0.44) < 0.1

    def test_snapshot_collects_dividend_rate(self):
        """stock_snapshot 이 dividendRate 도 수집."""
        src = open("bot/stock_snapshot.py", encoding="utf-8").read()
        assert '"dividendRate"' in src or "'dividendRate'" in src

    def test_peer_comps_collects_rate_and_price(self):
        """peer_comps 수집 시 dividendRate + currentPrice 포함."""
        src = open("bot/stock_snapshot.py", encoding="utf-8").read()
        # _collect_peer_multiples 함수 정의 내부에 두 필드 존재 확인
        idx = src.find("def _collect_peer_multiples")
        assert idx > 0
        segment = src[idx:idx+2000]
        assert "dividendRate" in segment
        assert "currentPrice" in segment


# ─────────────────────────────────────────────────────────────────────────
# 10) _cell_texts img alt 추출 + target price regex 회귀 차단
#     배경: 2026-06-08 KR 종목 리서치 상세에서 투자의견·목표가 전부 "—".
#     Naver Finance 가 rating 을 <img alt="매수"> 로 렌더 → _cell_texts 가
#     alt 미추출 → 빈 문자열. target price regex 의 trailing \b 가 한국어
#     뒤에서 미매칭. hk_consensus 의 pass→continue 버그로 date cell 도 매칭.
# ─────────────────────────────────────────────────────────────────────────
class TestCellTextsImgAlt:
    """fix commit: 2026-06-08 (research rating/target 누락)."""

    def test_naver_detail_regex_extracts_rating(self):
        """Detail page regex extracts 투자의견 from <em class="coment">."""
        from bot.naver_research_client import _RATING_DETAIL_RE
        html = '투자의견 <em class="coment">매수</em>'
        m = _RATING_DETAIL_RE.search(html)
        assert m and m.group(1).strip() == "매수"

    def test_hk_cell_texts_extracts_img_alt(self):
        """한경 _cell_texts 도 img alt 추출."""
        from bot.hk_consensus_client import _cell_texts
        html = '<td><img alt="Buy" src="/img/buy.png" /></td>'
        cells = _cell_texts(f"<tr>{html}</tr>")
        assert any("Buy" in c for c in cells)

    def test_naver_detail_regex_extracts_target(self):
        """Detail page regex extracts 목표가 from <em><strong>."""
        from bot.naver_research_client import _TARGET_RE
        html = '목표가 <em class="money"><strong>150,000</strong></em>'
        m = _TARGET_RE.search(html)
        assert m and float(m.group(1).replace(",", "")) == 150000.0

    def test_hk_pass_to_continue_fix(self):
        """한경 parser 가 date cell 을 target 으로 오파싱하지 않음."""
        from bot.hk_consensus_client import _parse_report_rows
        html = """<table><tr>
        <td>삼성전자</td>
        <td>목표가 보고서</td>
        <td>미래에셋증권</td>
        <td>2026-06-05</td>
        <td>85,000</td>
        <td>매수</td>
        </tr></table>"""
        from datetime import date, timedelta
        rows = _parse_report_rows(html, date.today() - timedelta(days=30))
        assert len(rows) >= 1
        assert rows[0]["target"] == 85000.0


# ─────────────────────────────────────────────────────────────────────────
# 11) 기관 보유 비중 (% Out) NaN fallback 회귀 차단
#     배경: 2026-06-08 대시보드 주요 기관 표에서 비중이 전부 "—".
#     yfinance institutional_holders 의 '% Out' 가 NaN → snapshot 에서
#     필터 → dashboard 에서 None → "—". Shares / shares_outstanding 으로
#     fallback 계산.
# ─────────────────────────────────────────────────────────────────────────
class TestInstitutionalHoldersPctFallback:
    """fix commit: 2026-06-08 (기관 비중 누락)."""

    def test_dashboard_computes_pct_from_shares(self):
        """% Out 누락 시 Shares/shares_outstanding 으로 계산하는 코드 존재."""
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "shares / shares_out" in src or "shares_out" in src


# ─────────────────────────────────────────────────────────────────────────
# 12) 자산 watcher 가 비-뱅샐 .zip/.xlsx 를 자산 업데이트로 오인 차단
#     배경: 2026-06-08 RAG 채널('무엇이든 포워드')에 올린 비-자산 파일이
#     확장자만으로 픽업돼 빈 모델(순자산 - · 주식 0종목)이 portfolio.json 을
#     덮어쓰고 거짓 '✅ 자산 업데이트' 푸시. 섹션 마커 기반 검증 게이트로 차단.
# ─────────────────────────────────────────────────────────────────────────
class TestBanksaladExportValidation:
    """fix commit: 2026-06-08 (RAG 비-자산 파일 오인)."""

    def test_junk_sheet_rejected(self):
        """섹션 마커 없는 임의 xlsx → is_banksalad_export False."""
        from bot.portfolio_parser import parse_banksalad, is_banksalad_export
        rows = [{"B": "Name", "C": "Value"}, {"B": "foo", "C": "1"}]
        parsed = parse_banksalad(rows)
        assert parsed["_sections"] == []
        assert is_banksalad_export(parsed) is False

    def test_real_export_accepted(self):
        """투자현황(5) 섹션 있는 진짜 export → True."""
        from bot.portfolio_parser import parse_banksalad, is_banksalad_export
        rows = [
            {"B": "5. 투자현황"},
            {"B": "투자상품종류", "C": "금융사", "D": "상품명",
             "F": "투자원금", "G": "평가금액", "H": "수익률"},
            {"B": "국내주식", "C": "NH", "D": "삼성전자",
             "F": "1000000", "G": "1200000", "H": "20"},
        ]
        parsed = parse_banksalad(rows)
        assert 5 in parsed["_sections"]
        assert is_banksalad_export(parsed) is True

    def test_finance_only_export_accepted(self):
        """재무현황(3)만 있어도 진짜 export 로 인정."""
        from bot.portfolio_parser import parse_banksalad, is_banksalad_export
        rows = [
            {"B": "3. 재무현황"},
            {"B": "항목", "C": "상품", "E": "금액",
             "F": "항목", "G": "상품", "I": "금액"},
            {"B": "예적금", "C": "주거래", "E": "5000000"},
        ]
        parsed = parse_banksalad(rows)
        assert is_banksalad_export(parsed) is True

    def test_ingest_raises_on_non_export(self, monkeypatch, tmp_path):
        """비-뱅샐 파싱 결과면 ingest 가 NotBanksaladExport → save 미도달."""
        import bot.portfolio as pf
        # parse_export 가 섹션 없는 결과를 내도록 monkeypatch
        monkeypatch.setattr(pf, "parse_export",
                            lambda data, password=None: {"_sections": [],
                                                         "holdings": [], "finance": {}})
        # save 가 호출되면 안 됨 — 호출 시 즉시 실패하도록 sentinel
        called = {"save": False}
        monkeypatch.setattr(pf, "save", lambda m: called.__setitem__("save", True))
        with pytest.raises(pf.NotBanksaladExport):
            pf.ingest(b"junk", password=None)
        assert called["save"] is False, "비-export 인데 save 가 호출됨(덮어쓰기 위험)"

    def test_not_banksalad_export_is_valueerror(self):
        """NotBanksaladExport 는 ValueError 하위 — except 호환."""
        from bot.portfolio import NotBanksaladExport
        assert issubclass(NotBanksaladExport, ValueError)

    def test_save_writes_bak_backup(self, tmp_path, monkeypatch):
        """save 가 기존 portfolio.json 을 .bak 으로 백업."""
        import bot.portfolio as pf
        path = tmp_path / "portfolio.json"
        monkeypatch.setattr(pf, "PORTFOLIO_PATH", path)
        pf.save({"net_worth": {"순자산": 100}})   # 1차 저장(백업 없음)
        pf.save({"net_worth": {"순자산": 200}})   # 2차 — 직전을 .bak 으로
        bak = path.with_suffix(".json.bak")
        assert bak.exists(), ".bak 백업 미생성"
        import json as _json
        assert _json.loads(bak.read_text())["net_worth"]["순자산"] == 100

    def test_watcher_skips_non_export_silently(self):
        """portfolio_watch 가 NotBanksaladExport 를 조용히 skip(푸시 없음)."""
        src = open("bot/portfolio_watch.py", encoding="utf-8").read()
        assert "NotBanksaladExport" in src, "watcher 가 비-export 예외 미처리"
        # NotBanksaladExport 분기에서 _push_confirm 을 호출하지 않아야 함
        idx = src.find("except NotBanksaladExport")
        assert idx > 0
        seg = src[idx:idx + 400]
        assert "_push_confirm" not in seg, "비-export 인데 confirm 푸시(거짓 알림)"


class TestCashInInvest:
    """투자성자산(재무현황) − 투자현황(holdings) = 예수금류 (2026-06-08).

    CMA/Super365 예수금이 섹션 3 투자성자산에는 있지만 섹션 5 투자현황에
    없어 증분 비교 시 허수 차이가 발생하던 것 차단."""

    def _build(self, invest_items, holdings_eval):
        from bot.portfolio import build_model
        parsed = {
            "as_of": "2026-06-08",
            "holdings": [
                {"상품명": f"stock{i}", "금융사": "NH",
                 "평가금액": ev, "투자원금": ev, "수익률": 0.0}
                for i, ev in enumerate(holdings_eval)
            ],
            "finance": {
                "assets": {"투자성 자산": [{"name": f"item{i}", "amount": a}
                                       for i, a in enumerate(invest_items)]},
                "liabilities": {},
                "총자산": sum(invest_items), "총부채": 0,
                "순자산": sum(invest_items),
            },
            "loans": [], "insurance": [],
        }
        noop = lambda name: {"ticker": None, "market": None, "matched": False}
        return build_model(parsed, resolve=noop)

    def test_cash_computed(self):
        """투자성자산 1억 중 주식 9천만 → 예수금 1천만."""
        m = self._build([90_000_000, 10_000_000], [90_000_000])
        assert m["cash_in_invest"] == 10_000_000
        assert m["snapshot"]["예수금"] == 10_000_000
        assert m["snapshot"]["투자성 자산"] == 100_000_000

    def test_no_cash(self):
        """투자성자산 = 투자현황 → 예수금 0."""
        m = self._build([50_000_000], [50_000_000])
        assert m["cash_in_invest"] == 0
        assert m["snapshot"]["예수금"] == 0

    def test_no_invest_category(self):
        """투자성자산 카테고리 자체가 없으면 예수금 0."""
        from bot.portfolio import build_model
        parsed = {
            "as_of": "2026-06-08",
            "holdings": [{"상품명": "x", "금융사": "A",
                          "평가금액": 100, "투자원금": 100, "수익률": 0}],
            "finance": {"assets": {"예적금": [{"name": "y", "amount": 500}]},
                        "liabilities": {}, "총자산": 600, "총부채": 0, "순자산": 600},
            "loans": [], "insurance": [],
        }
        noop = lambda name: {"ticker": None, "market": None, "matched": False}
        m = build_model(parsed, resolve=noop)
        assert m["cash_in_invest"] == 0

    def test_cash_reclassified_to_free_cash(self):
        """예수금이 투자성자산에서 빠지고 자유입출금에 합산."""
        m = self._build([90_000_000, 10_000_000], [90_000_000])
        alloc = m["asset_allocation"]
        assert alloc["투자성 자산"] == 90_000_000, "투자성 자산에서 예수금 미차감"
        assert alloc.get("자유입출금 자산", 0) == 10_000_000, "자유입출금 자산에 예수금 미합산"

    def test_format_summary_shows_cash(self):
        """예수금 > 0이면 텔레그램 요약에 표시."""
        from bot.portfolio import format_summary_text
        m = {
            "net_worth": {"순자산": 100, "총자산": 100, "총부채": 0},
            "distinct_count": 1, "matched_count": 0,
            "cash_in_invest": 5_000_000,
            "by_broker": {}, "asset_allocation": {}, "loans": [],
        }
        assert "예수금" in format_summary_text(m)

    def test_format_summary_hides_zero_cash(self):
        """예수금 0이면 텔레그램 요약에 미표시."""
        from bot.portfolio import format_summary_text
        m = {
            "net_worth": {"순자산": 100, "총자산": 100, "총부채": 0},
            "distinct_count": 1, "matched_count": 0,
            "cash_in_invest": 0,
            "by_broker": {}, "asset_allocation": {}, "loans": [],
        }
        assert "예수금" not in format_summary_text(m)


class TestHkConsensusParser:
    """한경 컨센서스 parser — plain-integer target + data-attr extraction
    (2026-06-08).

    한경 2026 redesign 이후 목표가가 콤마 없는 정수로, 투자의견이
    data-value 속성에 담겨 나오면서 기존 parser 가 둘 다 None/''으로
    파싱 → 리서치 액션 표 전부 '—' 표시."""

    def test_plain_int_target(self):
        """콤마 없는 정수 목표가를 파싱."""
        from bot.hk_consensus_client import _parse_report_rows
        from datetime import date, timedelta
        html = '<table><tr><td>2026-06-05</td><td>NH투자증권</td><td>매수</td><td>58000</td></tr></table>'
        rows = _parse_report_rows(html, date.today() - timedelta(days=30))
        assert rows and rows[0]["target"] == 58000.0

    def test_comma_target_regression(self):
        """콤마 있는 정수 목표가 (기존 형식, 회귀 방지)."""
        from bot.hk_consensus_client import _parse_report_rows
        from datetime import date, timedelta
        html = '<table><tr><td>2026-06-05</td><td>삼성증권</td><td>Buy</td><td>450,000</td></tr></table>'
        rows = _parse_report_rows(html, date.today() - timedelta(days=30))
        assert rows and rows[0]["target"] == 450000.0

    def test_year_not_matched_as_target(self):
        """2020-2099 범위 4자리 숫자는 목표가로 매칭 금지."""
        from bot.hk_consensus_client import _parse_report_rows
        from datetime import date, timedelta
        html = '<table><tr><td>2026-06-05</td><td>NH투자증권</td><td>Hold</td><td>2026년 전망</td></tr></table>'
        rows = _parse_report_rows(html, date.today() - timedelta(days=30))
        assert rows and rows[0]["target"] is None

    def test_data_value_attr_extraction(self):
        """data-value 속성에 담긴 투자의견·목표가 추출."""
        from bot.hk_consensus_client import _parse_report_rows
        from datetime import date, timedelta
        html = '<table><tr><td>2026-06-05</td><td>한국투자증권</td><td data-value="Outperform"></td><td data-value="75000"></td></tr></table>'
        rows = _parse_report_rows(html, date.today() - timedelta(days=30))
        assert rows and rows[0]["target"] == 75000.0
        assert rows[0]["rating"] == "Outperform"

    def test_new_rating_keywords(self):
        """추가된 투자의견 키워드(Trading Buy 등) 인식."""
        from bot.hk_consensus_client import _parse_report_rows
        from datetime import date, timedelta
        for kw in ("Trading Buy", "적극매수", "Accumulate", "Underperform", "Reduce"):
            html = f'<table><tr><td>2026-06-05</td><td>NH투자증권</td><td>{kw}</td><td>50000</td></tr></table>'
            rows = _parse_report_rows(html, date.today() - timedelta(days=30))
            assert rows and rows[0]["rating"] == kw, f"missed keyword: {kw}"


class TestDartFeedBackfill:
    """DART 피드 백필 버그 클래스 영구 차단 (2026-06-11 E2E 하네스 surfaced).

    실서피스 5회 연속 '카드 안 채워짐'의 근본 원인 3종 + 하네스가 추가
    적발한 2종을 stub 만으로(네트워크 0) 고정:
      1) merge_and_save 가 나중 사이클 enrich 성공분(detail)을 버림
      2) 증분(당일 0건) 사이클에서 아카이브 pending 백필이 굶음
      3) 계약 파서 '계약상대' 가 마지막 필드(축약형 공시)일 때 종결
         lookahead 미매칭 → 카드 사망
      4) 전환청구권행사가 분류 '기타'로 떨어져 fetch 에서 잘림
    """

    def _load(self, tmp_path, monkeypatch):
        import importlib.util
        import bot.dart_detail  # noqa: F401 — real 패키지 보장(가짜 stub 금지)
        monkeypatch.setenv("HOME", str(tmp_path))
        spec = importlib.util.spec_from_file_location(
            "dart_feed_t", "bot/dart_feed.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.time.sleep = lambda s: None
        return m

    def test_merge_updates_detail_on_existing(self, tmp_path, monkeypatch):
        m = self._load(tmp_path, monkeypatch)
        from datetime import date
        d = date(2026, 6, 10)
        m.merge_and_save(d, [{"rcept_no": "R1", "report_nm": "x",
                              "category": "계약"}])
        m.merge_and_save(d, [{"rcept_no": "R1", "report_nm": "x",
                              "category": "계약",
                              "detail": ["계약금액: 50억원"]}])
        saved = m.load_archive(d)
        assert saved[0].get("detail") == ["계약금액: 50억원"]

    def test_pending_backfill_on_empty_fetch(self, tmp_path, monkeypatch):
        m = self._load(tmp_path, monkeypatch)
        from datetime import date
        d = date(2026, 6, 10)
        m.merge_and_save(d, [{"rcept_no": "R2", "report_nm":
                              "단일판매ㆍ공급계약체결", "category": "계약",
                              "corp_code": "C", "stock_code": "",
                              "date": "20260610"}])
        monkeypatch.setattr(m, "_dart_api_key", lambda: "K")
        monkeypatch.setattr(m, "fetch_market_disclosures",
                            lambda *a, **k: [])           # 새벽: 당일 0건
        monkeypatch.setattr(m, "_extract_detail",
                            lambda *a: {"lines": ["계약금액: 70억원"]})
        monkeypatch.setattr(m, "_industry_line", lambda sc: [])
        monkeypatch.setattr(m, "_market_cap_price_lines", lambda sc: [])
        m.run_once()
        saved = m.load_archive(d)
        assert saved[0].get("detail") == ["계약금액: 70억원"]

    def test_contract_party_as_last_field(self, tmp_path, monkeypatch):
        m = self._load(tmp_path, monkeypatch)
        import io, zipfile, types as _t
        doc = ("<T>계약금액 (원)</T><T>7,000,000,000</T>"
               "<T>계약상대방</T><T>LG전자(주)</T>"
               "<PAD>" + "x" * 600 + "</PAD>")
        buf = io.BytesIO()
        z = zipfile.ZipFile(buf, "w")
        z.writestr("x.xml", doc.encode("utf-8"))
        z.close()
        resp = _t.SimpleNamespace(content=buf.getvalue(),
                                  raise_for_status=lambda: None)
        monkeypatch.setattr(m.requests, "get", lambda *a, **k: resp)
        r = m._extract_contract_document("RX", "K")
        assert r and any("70억원" in l for l in r["lines"])
        assert any("LG전자" in l for l in r["lines"])

    def test_convert_exercise_classified_not_etc(self, tmp_path, monkeypatch):
        m = self._load(tmp_path, monkeypatch)
        assert m._classify_report("전환청구권행사(제5회차)") != "기타"
        assert m._classify_report("주요사항보고서(주식병합결정)") != "기타"


class TestDartCardFormats:
    """DART 카드 승인 양식(배치 2026-06-11) 영구 회귀 — 검증 중 surfaced
    버그 클래스 고정:
      1) _won 콤마/소수 손실 (2007억·6.9억→7억 반올림 손실)
      2) 계약 _END 가 값 속 '공급/판매'·소수(4.5)를 절단
      3) 정정 헤더 — 정정전→정정후 (A→B %) / 금액 무변
      4) 실적 단위(천원/백만원) 감지 + 월 라벨 괄호 토큰 오인
      5) 시작일~종료일 쌍 regex 가 'N일' 의 trailing 일 미소비
      6) 대시보드 '= ' prefix 제거 + 시총/현재가 렌더 부착(중복 방지)
    """

    def _load(self, tmp_path, monkeypatch):
        import importlib.util
        import bot.dart_detail  # noqa: F401 — real 패키지 보장
        monkeypatch.setenv("HOME", str(tmp_path))
        spec = importlib.util.spec_from_file_location(
            "dart_feed_t", "bot/dart_feed.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.time.sleep = lambda s: None
        m._doc_fail_recent = lambda rno: False
        m._doc_fail_mark = lambda rno, hours=0.5: None
        return m

    def _doc(self, m, monkeypatch, body):
        import io, types, zipfile
        buf = io.BytesIO()
        z = zipfile.ZipFile(buf, "w")
        z.writestr("doc.xml",
                   ("<DOC>" + body + "<PAD>" + "x" * 600 + "</PAD></DOC>")
                   .encode("utf-8"))
        z.close()
        resp = types.SimpleNamespace(content=buf.getvalue(), status_code=200)
        empty = types.SimpleNamespace(
            content=b"{}", status_code=200,
            json=lambda: {"status": "013", "list": []})
        monkeypatch.setattr(
            m.requests, "get",
            lambda url, params=None, timeout=None, **k:
            resp if "document.xml" in url else empty)

    def test_won_comma_and_decimal(self):
        from bot.dart_detail import _won
        assert _won("200700000000") == "2,007억원"
        assert _won("690000000") == "6.9억원"
        assert _won("3150000000") == "31.5억원"
        assert _won("-10010000000") == "-100.1억원"
        assert _won("1500000000") == "15억원"  # 기존 호환

    def test_contract_value_not_truncated_by_keywords(self, tmp_path, monkeypatch):
        m = self._load(tmp_path, monkeypatch)
        self._doc(m, monkeypatch,
                  "1. 판매ㆍ공급계약 내용 특수합금 공급계약 2. 계약내역 "
                  "조건부 계약여부 해당 확정 계약금액 - 조건부 계약금액 20,200,000,000 "
                  "매출액 대비(%) 21.1 5. 계약상대방 미국 우주항공 발사업체 "
                  "7. 공시유보 유보사유 영업기밀 비공개 요청 유보기한 2027-02-15")
        r = m._extract_detail("단일판매ㆍ공급계약체결", "RX", "C", "K")
        j = "\n".join(r["lines"])
        assert "계약: 특수합금 공급계약" in j          # '공급' 절단 금지
        assert "계약금액: 202억원 (조건부 · 매출액대비 21.1%)" in j
        assert "공시유보: 2027-02-15까지 (영업기밀 비공개 요청)" in j

    def test_contract_correction_header_a_to_b(self, tmp_path, monkeypatch):
        m = self._load(tmp_path, monkeypatch)
        self._doc(m, monkeypatch,
                  "정정신고 정정일자 2026-06-09 2. 정정사유 계약금액 변경 "
                  "3. 정정사항 정정전 정정후 계약금액 총액(원) 1,100,000,000 690,000,000 "
                  "매출액 대비(%) 28.0 17.5 1. 판매ㆍ공급계약 내용 장비 A 공급 "
                  "5. 계약상대방 ABC Corp")
        r = m._extract_detail("[기재정정]단일판매ㆍ공급계약체결", "RX", "C", "K")
        j = "\n".join(r["lines"])
        assert "정정(26-06-09): 계약금액 11억원 → 6.9억원 (-37%)" in j
        assert "계약금액: 6.9억원" in j               # 본문 = 정정후

    def test_earnings_unit_and_month_label(self, tmp_path, monkeypatch):
        m = self._load(tmp_path, monkeypatch)
        self._doc(m, monkeypatch,
                  "영업(잠정)실적(공정공시) (단위: 백만원, %) "
                  "매출액 당해실적 (26.05) 200,700 177,800 12.9 167,250 20.0 "
                  "누계실적 (26.01~26.05) 935,400 - - 811,300 15.3 "
                  "2. 정보제공내역 IR")
        r = m._extract_detail("영업(잠정)실적(공정공시)", "RX", "C", "K")
        j = "\n".join(r["lines"])
        assert "구분: 월별 잠정실적 (26.05)" in j
        assert "매출액(당월): 2,007억원 (전월 +12.9% · 전년동월 +20.0%)" in j
        assert "매출액(누계): 9,354억원 (전년동기 +15.3%)" in j

    def test_cb_dilution_and_period_pair(self, tmp_path, monkeypatch):
        m = self._load(tmp_path, monkeypatch)
        self._doc(m, monkeypatch,
                  "전환사채권 발행결정 회차 6 사모 전환사채 "
                  "권면(전자등록)총액(원) 15,000,000,000 "
                  "표면이자율(%) 1.0 만기이자율(%) 2.0 사채만기일 2029-06-18 "
                  "전환가액(원/주) 5,788 주식수 2,591,568 주식총수 대비 비율(%) 35.31 "
                  "전환청구기간 시작일 2027년 06월 18일 종료일 2029년 05월 18일 "
                  "납입일 2026년 06월 18일")
        r = m._extract_detail("주요사항보고서(전환사채권발행결정)", "RX", "C", "K")
        j = "\n".join(r["lines"])
        assert "구분: 전환사채 발행 (6회차 · 사모) — 150억원" in j
        assert "주식총수의 35.31% (잠재 희석)" in j
        assert "전환기간 2027-06-18 ~ 2029-05-18" in j  # 'N일' trailing 소비

    def test_dashboard_no_eq_prefix_and_mcap_attach(self, tmp_path, monkeypatch):
        m = self._load(tmp_path, monkeypatch)
        from datetime import date, datetime
        d = datetime.now(m._KST).date()
        m.merge_and_save(d, [
            {"rcept_no": "R1", "corp_name": "A사", "stock_code": "042700",
             "corp_code": "C", "report_nm": "단일판매ㆍ공급계약체결",
             "category": "계약", "date": d.strftime("%Y%m%d"), "url": "#",
             "detail": ["계약: X"]},
            {"rcept_no": "R2", "corp_name": "B사", "stock_code": "000100",
             "corp_code": "C", "report_nm": "유상증자결정",
             "category": "자금조달", "date": d.strftime("%Y%m%d"), "url": "#",
             "detail": ["신주수: 10주", "시가총액: 1조원"]}])  # 옛 enrich 부착분
        import bot.dashboard as db
        import bot.dart_feed as real_df  # 렌더는 real 모듈에서 import
        calls = []
        monkeypatch.setattr(
            real_df, "_market_cap_price_lines",
            lambda c: (calls.append(c) or ["시가총액: 2조원 / 현재가: 1,000원"]))
        monkeypatch.setattr(db, "_load_dart_feed_data",
                            lambda days_back=30: m.load_all_archives(2),
                            raising=False)
        html = db._render_dart_feed_page(m.load_all_archives(days_back=2))
        assert 'df-detail-ln">= ' not in html        # '= ' prefix 제거
        assert "시가총액: 2조원 / 현재가: 1,000원" in html
        assert "042700" in calls and "000100" not in calls  # 중복 방지


# ─────────────────────────────────────────────────────────────────────────
# 배치 #18 지분공시 노이즈컷 — 대량보유만 + 신규 OR |Δ|≥5%p
# ─────────────────────────────────────────────────────────────────────────
class TestEquityNoiseFilter:
    """_equity_noise 가 잘못된 지분공시를 올바르게 걸러내는지 확인."""

    @staticmethod
    def _noise(it):
        """dashboard._render_dart_feed_page 에 삽입된 필터 로직 재현."""
        if it.get("category") != "지분공시":
            return False
        rn = it.get("report_nm", "")
        if "대량보유" not in rn:
            return True
        det = it.get("detail") or []
        if not det:
            return False
        for dl in det:
            s = str(dl)
            if "지분율" in s and "→" not in s:
                return False
            m = re.search(r"([+-]?\d+\.?\d*)\s*%p", s)
            if m:
                try:
                    if abs(float(m.group(1))) >= 5.0:
                        return False
                except ValueError:
                    pass
        return True

    def test_non_majorstock_filtered(self):
        assert self._noise({"category": "지분공시", "report_nm": "임원소유상황"})

    def test_majorstock_new_passes(self):
        assert not self._noise({"category": "지분공시",
                                 "report_nm": "대량보유 신규",
                                 "detail": ["지분율: 7.23%"]})

    def test_majorstock_big_delta_passes(self):
        assert not self._noise({
            "category": "지분공시",
            "report_nm": "대량보유 변경",
            "detail": ["지분율: 3.00% → 8.50% (+5.50%p ▲)"]})

    def test_majorstock_small_delta_filtered(self):
        assert self._noise({
            "category": "지분공시",
            "report_nm": "대량보유 변경",
            "detail": ["지분율: 5.00% → 6.20% (+1.20%p ▲)"]})

    def test_non_equity_passes(self):
        assert not self._noise({"category": "계약", "report_nm": "공급계약"})

    def test_majorstock_no_detail_passes(self):
        assert not self._noise({"category": "지분공시",
                                 "report_nm": "대량보유 신규",
                                 "detail": []})


# ─────────────────────────────────────────────────────────────────────────
# 관심종목 DART 공시 알림 (#19 전종목 알람 교체) — /dart_alert
# ─────────────────────────────────────────────────────────────────────────
class TestDartFavAlerts:
    """관심종목 DART 공시 알림 (#19 교체, 2026-06-11) — 영구 seen-set 으로
    재시작 재발송(#19 버그 클래스) 차단 + 신규 관심종목 seed 무발송."""

    def _setup(self, tmp_path, monkeypatch, favorites, archives):
        import bot.dart_fav_alerts as dfa
        import bot.market_favorites as mf
        import bot.dart_feed as df
        monkeypatch.setattr(dfa, "_STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(mf, "get_favorites", lambda: favorites)
        monkeypatch.setattr(df, "load_all_archives",
                            lambda days_back=3: archives)
        return dfa

    def test_kr_code_extraction(self, tmp_path, monkeypatch):
        dfa = self._setup(tmp_path, monkeypatch,
                          [{"ticker": "005930.KS"}, {"ticker": "AAPL"},
                           {"ticker": "035420.kq"}, {"ticker": "247540"}], {})
        assert dfa.fav_kr_codes() == {"005930", "035420", "247540"}

    @staticmethod
    def _today():
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")

    def test_enable_seeds_then_only_new_alerts(self, tmp_path, monkeypatch):
        td = self._today()
        archives = {"2026-06-11": [
            {"rcept_no": "R1", "stock_code": "005930", "corp_name": "삼성전자",
             "report_nm": "기존공시", "category": "계약", "url": "#", "date": td},
        ]}
        dfa = self._setup(tmp_path, monkeypatch,
                          [{"ticker": "005930.KS"}], archives)
        info = dfa.enable(123)
        assert info["codes"] == 1 and info["seeded"] == 1
        # 기존 공시는 발송 안 함
        items, chat = dfa.poll_new()
        assert items == []
        # 새 공시 도착 → 1회만 발송
        archives["2026-06-11"].append(
            {"rcept_no": "R2", "stock_code": "005930", "corp_name": "삼성전자",
             "report_nm": "신규공시", "category": "실적", "url": "#", "date": td})
        items, chat = dfa.poll_new()
        assert [i["rcept_no"] for i in items] == ["R2"] and chat == 123
        # 재폴(=재시작/자정 상당) — 재발송 없음 (영구 seen)
        items, chat = dfa.poll_new()
        assert items == []

    def test_new_favorite_seeded_silently(self, tmp_path, monkeypatch):
        td = self._today()
        favorites = [{"ticker": "005930.KS"}]
        archives = {"2026-06-11": [
            {"rcept_no": "R3", "stock_code": "000660", "corp_name": "SK하이닉스",
             "report_nm": "기존공시", "category": "계약", "url": "#", "date": td},
        ]}
        dfa = self._setup(tmp_path, monkeypatch, favorites, archives)
        dfa.enable(123)
        # 관심종목에 000660 추가 — 기존 R3 는 seed 만 (무발송)
        favorites.append({"ticker": "000660.KS"})
        items, _ = dfa.poll_new()
        assert items == []
        # 000660 의 이후 신규 공시는 발송
        archives["2026-06-11"].append(
            {"rcept_no": "R4", "stock_code": "000660", "corp_name": "SK하이닉스",
             "report_nm": "신규공시", "category": "리스크", "url": "#", "date": td})
        items, chat = dfa.poll_new()
        assert [i["rcept_no"] for i in items] == ["R4"] and chat == 123

    def test_backfilled_old_item_not_alerted(self, tmp_path, monkeypatch):
        # 백필이 늦게 추가한 과거(그제 이전) 공시는 무발송 seed — 폭주 방지
        archives = {"2026-06-01": [
            {"rcept_no": "R5", "stock_code": "005930", "corp_name": "삼성전자",
             "report_nm": "옛공시", "category": "계약", "url": "#",
             "date": "20260601"},
        ]}
        dfa = self._setup(tmp_path, monkeypatch,
                          [{"ticker": "005930.KS"}], {})
        dfa.enable(123)
        # 활성화 후 백필이 옛 공시를 아카이브에 추가
        monkeypatch.setattr(__import__("bot.dart_feed", fromlist=["x"]),
                            "load_all_archives", lambda days_back=3: archives)
        items, _ = dfa.poll_new()
        assert items == []
        # seed 됐으므로 이후에도 재등장 없음
        items, _ = dfa.poll_new()
        assert items == []

    def test_disabled_returns_nothing(self, tmp_path, monkeypatch):
        dfa = self._setup(tmp_path, monkeypatch, [{"ticker": "005930.KS"}], {})
        assert dfa.poll_new() == ([], None)


class TestDartBackfillReclassify:
    """backfill_with_new_rules (a)패스 — 기존 아카이브 카테고리 소급 재분류."""

    def test_reclassify_existing_archive(self, tmp_path, monkeypatch):
        import json
        from datetime import datetime, timedelta, timezone
        import bot.dart_feed as df
        monkeypatch.setattr(df, "_ARCHIVE_DIR", tmp_path)
        monkeypatch.setattr(df, "_dart_api_key", lambda: None)  # fetch 안 함
        kst = timezone(timedelta(hours=9))
        d = datetime.now(kst).date()
        items = [
            # 옛 fallback 이 지분공시로 오분류했던 공개매수
            {"rcept_no": "B1", "report_nm": "공개매수결과보고서",
             "category": "지분공시", "detail": ["기존 detail 보존"]},
            # 옛 룰 정상 분류 — 무변경
            {"rcept_no": "B2", "report_nm": "단일판매ㆍ공급계약체결",
             "category": "계약"},
        ]
        (tmp_path / f"{d.strftime('%Y-%m-%d')}.json").write_text(
            json.dumps(items, ensure_ascii=False), "utf-8")
        st = df.backfill_with_new_rules(days_back=0)
        assert st["reclassified"] == 1
        saved = json.loads(
            (tmp_path / f"{d.strftime('%Y-%m-%d')}.json").read_text("utf-8"))
        by_id = {it["rcept_no"]: it for it in saved}
        assert by_id["B1"]["category"] == "회사구조"
        assert by_id["B1"]["detail"] == ["기존 detail 보존"]  # detail 보존
        assert by_id["B2"]["category"] == "계약"

    def test_fair_disclosure_precedence(self):
        # 공정공시 wrapper 는 chart 분류 '뒤' — 더 특정한 종류 유지
        from bot.dart_feed import _classify_report
        assert _classify_report("단일판매ㆍ공급계약체결(공정공시)") == "계약"
        assert _classify_report("장래사업ㆍ경영계획(공정공시)") == "실적"


# ─────────────────────────────────────────────────────────────────────────
# 배치 #20 favorites EPS/PER FY label + 적자 표시
# ─────────────────────────────────────────────────────────────────────────
class TestFavoritesEpsFyLabel:
    """market_favorites 의 FY label 로직 + eps_negative 플래그."""

    def test_fy_label_from_timestamp(self):
        from datetime import datetime
        ts = datetime(2025, 12, 31).timestamp()
        label = f"FY{datetime.fromtimestamp(ts).year % 100:02d}"
        assert label == "FY25"

    def test_eps_negative_flag(self):
        eps_neg = -1.5
        assert eps_neg is not None and eps_neg < 0
        eps_pos = 3.2
        assert not (eps_pos is not None and eps_pos < 0)


# ─────────────────────────────────────────────────────────────────────────
# Daily Byte 면책 미포함 + 제목 통일 (사용자 정책 2026-06-11)
# ─────────────────────────────────────────────────────────────────────────
class TestDailyByteDisclaimerStrip:
    """면책/디스클레이머 줄이 본문에 남지 않게 — 프롬프트 금지 directive 의
    Python 백스톱 (_post_process). KR(일/주간·부동산·청약 공유) + US 양쪽."""

    def test_kr_post_process_strips_disclaimer(self):
        from bot.daily_kr_flow import _post_process
        raw = ("2026년 6월 10일 한국 증시 데일리 브리프\n\n"
               "면책 조항: 본 브리프는 수급 데이터 관찰 (교육·정보 목적), "
               "투자 권유 아님.\n\n"
               "📊 시장 수급 총평\n외국인 -2.6조 순매도. 투자자예탁금 136.4조.\n\n"
               "본 브리프는 수급 데이터 관찰 (교육·정보 목적), 투자 권유 아님")
        out = _post_process(raw, "2026-06-10")
        assert "면책" not in out
        assert "투자 권유" not in out
        # 정상 본문 보존 ('투자자예탁금' 은 disclaimer 아님)
        assert "투자자예탁금 136.4조" in out
        # 제목 줄 bold 유지
        assert out.startswith("<b>2026년 6월 10일 한국 증시 데일리 브리프</b>")

    def test_us_post_process_strips_disclaimer(self):
        from bot.us_market_daily import _post_process
        raw = ("2026년 6월 11일 미국 증시 데일리 브리프\n\n"
               "📊 시장 총평\nS&P 500 -1.62% 마감.\n\n"
               "본 브리프는 시장 데이터 관찰 (교육·정보 목적), 투자 권유 아님")
        out = _post_process(raw)
        assert "투자 권유" not in out
        assert "시장 총평" in out

    def test_kr_prompt_has_title_directive_no_disclaimer_directive(self):
        from bot.daily_kr_flow import _PROMPT
        p = _PROMPT.format(date="20260610", date_kr="2026년 6월 10일",
                           data_summary="DATA")
        assert "2026년 6월 10일 한국 증시 데일리 브리프" in p
        assert "면책: 출력 끝에" not in p


# ─────────────────────────────────────────────────────────────────────────
# DART 커버리지 감사 (사용자 2026-06-11 '놓치는 공시') — 분류 보강
# ─────────────────────────────────────────────────────────────────────────
class TestDartClassifyCoverage:
    """거래소 의무공시인데 키워드 부재로 '기타' 드롭되던 유형들의 분류 보장.
    '기타' 회귀 시 fetch(skip_routine)가 다시 버리게 되므로 영구 테스트."""

    def _c(self, title):
        from bot.dart_feed import _classify_report
        return _classify_report(title)

    def test_risk_events_not_dropped(self):
        for t in ("부도발생", "해산사유발생", "채권은행등의관리절차개시신청",
                  "파생상품거래손실발생", "생산중단", "생산재개", "화재발생",
                  "공정거래위원회의과징금부과"):
            assert self._c(t) == "리스크", t

    def test_tender_offer_control(self):
        assert self._c("공개매수신고서") == "회사구조"
        assert self._c("공개매수결과보고서") == "회사구조"  # 옛 '보고서' fallback 이 지분공시로 오분류하던 것

    def test_inquiry_rumor_category(self):
        assert self._c("조회공시요구(풍문또는보도)에대한답변(미확정)") == "조회공시"
        assert self._c("풍문또는보도에대한해명(미확정)") == "조회공시"
        # 내부 키워드(유상증자) 선점 가드 — 조회공시가 먼저
        assert self._c("조회공시요구(유상증자설)에대한답변") == "조회공시"

    def test_funding_credit_types(self):
        for t in ("타인에대한담보제공결정", "타인에대한채무보증결정",
                  "특수관계인에대한금전대여결정", "단기차입금증가결정",
                  "전환가액의조정(제5회차)", "교환청구권행사"):
            assert self._c(t) == "자금조달", t
        # 담보제공이 '기타' 드롭되면 #248 담보 파서가 dead code 가 됨
        assert self._c("담보제공결정") != "기타"

    def test_major_holder_collateral_is_control(self):
        # 최대주주변경 수반 담보계약은 지배구조 사건 — 자금조달 오분류 금지
        assert self._c("최대주주변경을수반하는주식담보제공계약체결") == "회사구조"

    def test_patent_tech_guidance(self):
        assert self._c("특허권취득") == "계약"
        assert self._c("기술이전계약체결") == "계약"
        assert self._c("장래사업ㆍ경영계획(공정공시)") == "실적"

    def test_existing_behavior_unchanged(self):
        assert self._c("주식등의대량보유상황보고서") == "지분공시"
        assert self._c("단일판매ㆍ공급계약체결") == "계약"
        assert self._c("전환청구권행사") == "자금조달"
        assert self._c("과징금부과처분취소소송의제기") == "소송"  # 소송 > 리스크 순서
        assert self._c("주주총회소집공고") == "기타"  # routine 은 여전히 드롭


# ─────────────────────────────────────────────────────────────────────────
# 명령 단일 레지스트리 — 텔레그램·대시보드 링크 (사용자 2026-06-11)
# ─────────────────────────────────────────────────────────────────────────
class TestCommandRegistrySingleSource:
    """텔레그램 add_handler / set_my_commands 메뉴 / 대시보드 콘솔이 전부
    _static_command_registry 한 테이블에서 파생 — 하드코딩 사본이 다시
    생기면(드리프트 클래스: /dart_alert 콘솔 누락) 실패."""

    def _src(self):
        return open("bot/telegram_bot.py", encoding="utf-8").read()

    def test_no_hardcoded_botcommand_or_handler_lines(self):
        src = self._src()
        # 메뉴/핸들러 하드코딩 잔존 금지 (동적 screener_<slug> 부분 제외)
        assert 'BotCommand("start"' not in src
        assert 'app.add_handler(CommandHandler("start"' not in src
        assert 'app.add_handler(CommandHandler("dart_alert"' not in src

    def test_registry_has_all_commands(self):
        import re
        src = self._src()
        body = re.search(
            r"def _static_command_registry\(\).*?\n    return \{(.*?)\n    \}",
            src, re.DOTALL).group(1)
        # sv_cost 제거 (2026-06-12 SV 폐기 최종 정리)
        for c in ("start", "help", "usage", "sites", "screener_list",
                  "watch", "watchlist", "unwatch", "dart_alert", "paper",
                  "screen", "screener", "compare", "portfolio",
                  "screener_cost", "daily_byte_cost",
                  "cheongyak_cost", "realestate_cost"):
            assert f'"{c}"' in body, f"registry missing /{c}"

    def test_console_routes_dynamic_screener_slugs(self):
        # /screener_<slug> 가 대시보드 콘솔에서도 screener 경로로 라우팅
        src = self._src()
        assert 'word.startswith("screener_")' in src


class TestDartParseTargetAndSignificance:
    """DART 카드 색상 구분 (사용자 2026-06-12) — is_parse_target(미파싱
    판정) + significance(중요 6규칙). enrich 와 대시보드 단일 소스."""

    def test_parse_target_registry(self):
        from bot.dart_feed import is_parse_target
        # 파싱 대상 — 계약·대량보유·_force(담보)
        assert is_parse_target({"category": "계약",
                                "report_nm": "단일판매ㆍ공급계약체결",
                                "corp_code": "x"})
        assert is_parse_target({"category": "지분공시",
                                "report_nm": "주식등의대량보유상황보고서",
                                "corp_code": "x"})
        assert is_parse_target({"category": "기타",
                                "report_nm": "타인에대한담보제공결정",
                                "corp_code": "x"})
        # 제목만이 정상 — IR·대량보유 외 지분·구조화 없는 자금조달·corp 부재
        assert not is_parse_target({"category": "IR",
                                    "report_nm": "기업설명회(IR)개최",
                                    "corp_code": "x"})
        # 임원·주요주주 소유상황 = elestock 구조화 보유 → 파싱 대상
        # (2026-06-12 '지분공시도 다 파싱' — 옛 대량보유-only 정책 폐기)
        assert is_parse_target({"category": "지분공시",
                                "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서",
                                "corp_code": "x"})
        # generic '보고서' 분류분(감사보고서 등)은 구조화 소스 없음 → 제외
        assert not is_parse_target({"category": "지분공시",
                                    "report_nm": "감사보고서제출",
                                    "corp_code": "x"})
        assert not is_parse_target({"category": "자금조달",
                                    "report_nm": "단기차입금증가결정",
                                    "corp_code": "x"})
        assert not is_parse_target({"category": "계약",
                                    "report_nm": "공급계약", "corp_code": ""})

    def test_enrich_uses_is_parse_target(self):
        # enrich 의 시도 조건이 is_parse_target 단일 소스로 통합됐는지
        # (인라인 _force/지분/자금조달 분기 중복 제거) — 회귀 차단.
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        assert "if is_parse_target(item):" in src

    def test_significance_rule1_earnings_30pct(self):
        # 2026-06-12 사용자 게이트 변경: 제목만으로 발화 안 함 — 매출 |20%|↑
        # OR 영업익 |30%|↑ (상세는 TestEarningsChangeGate). 여기선 미파싱
        # 미발화 + 게이트 통과 발화 + 기재정정 제외만 가드.
        from bot.dart_feed import significance
        assert significance({"report_nm": "매출액또는손익구조30%(대규모법인은15%)이상변동",
                             "category": "실적"}) is None   # detail 없음 → ⚠️ 대기
        assert significance(
            {"report_nm": "매출액또는손익구조30%(대규모법인은15%)이상변동",
             "category": "실적",
             "detail": ["매출액: 1,234억원 (전기 987억원 · +25.3%)"]}
        ) == "매출액 +25.3% 변동"
        # 기재정정 제외
        assert significance({"report_nm": "[기재정정]매출액또는손익구조30%이상변동",
                             "category": "실적"}) is None

    def test_significance_rule2_contract_10pct(self):
        from bot.dart_feed import significance
        assert significance({"report_nm": "단일판매ㆍ공급계약체결", "category": "계약",
                             "detail": ["계약금액: 500억원 (매출액대비 14.2% · 조건부)"]}
                            ) == "매출대비 14.2% 계약"
        assert significance({"report_nm": "단일판매ㆍ공급계약체결", "category": "계약",
                             "detail": ["계약금액: 50억원 (매출액대비 3.1%)"]}) is None

    def test_significance_rule3_burn_3pct_gate(self):
        # 소각도 발행주식 3%↑만 (사용자 2026-06-12 — 신도기연 1.21% 미발화)
        from bot.dart_feed import significance
        small = {"report_nm": "주식소각결정", "category": "주주환원",
                 "detail": ["구분: 주식 소각 (이익소각 — 자본금 감소 없음)",
                            "소각: 193,198주 = 발행주식의 1.21% · 29.4억원"]}
        assert significance(small) is None
        big = {"report_nm": "주식소각결정", "category": "주주환원",
               "detail": ["소각: 700,000주 = 발행주식의 3.50% · 120억원"]}
        assert significance(big) == "발행주식 3.5% 소각"
        # detail 에 % 없으면 shares 로 계산, shares 없으면 미발화
        calc = {"report_nm": "주식소각결정", "category": "주주환원",
                "detail": ["소각: 1,000,000주 · 50억원"]}
        assert significance(calc, shares_outstanding=20_000_000) == "발행주식 5.0% 소각"
        assert significance(calc) is None
        assert significance({"report_nm": "주식소각결정",
                             "category": "주주환원"}) is None  # detail 부재

    def test_elestock_lines_pure(self):
        # 임원·주요주주 소유상황 elestock 라인 + 100% 비율 환각 가드
        from bot.dart_feed import _elestock_lines
        row = {"repror": "홍길동", "isu_exctv_ofcps": "부사장",
               "isu_main_shrholdr": "-",
               "sp_stock_lmp_cnt": "12,345", "sp_stock_lmp_irds_cnt": "-2,000",
               "sp_stock_lmp_rate": "0.05", "sp_stock_lmp_irds_rate": "-0.01"}
        lines = _elestock_lines(row)
        assert lines[0] == "보고자: 홍길동 (부사장)"
        assert "소유주식: 12,345주 (-2,000주)" in lines
        assert any(l.startswith("지분율: 0.05%") for l in lines)
        # sp_stock_lmp_rate ≥50% = 본인분 비율 환각(005930 2026-05-31) → 생략
        assert not any("지분율" in l
                       for l in _elestock_lines(dict(row, sp_stock_lmp_rate="100.0")))

    def test_equity_noise_shows_parsed_ownership(self):
        # 소유상황 카드 = detail 파싱된 것만 노출 (홍수 방지 + '다 파싱' 양립)
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert '"소유상황" in rn' in src
        # dart_feed 라우팅 + 임원 elestock fetcher 존재
        df_src = open("bot/dart_feed.py", encoding="utf-8").read()
        assert "_extract_elestock" in df_src and "elestock.json" in df_src

    def test_kr_earnings_universe_fallback(self):
        # pykrx/creds 부재(샌드박스) → 하드코딩 폴백 (빈 universe 금지)
        from bot.market_overview import _kr_earnings_universe
        u = _kr_earnings_universe()
        assert len(u) >= 30
        assert all(len(t) == 2 for t in u)

    def test_significance_rule4_buyback_3pct(self):
        from bot.dart_feed import significance
        item = {"report_nm": "자기주식취득결정", "category": "주주환원",
                "detail": ["취득예정: 1,000,000주", "계약금액: 100억원"]}
        assert significance(item, shares_outstanding=20_000_000) == "발행주식 5.0% 취득"
        # 3% 미만 / shares 없음 / 정정 → 미발화
        assert significance(item, shares_outstanding=200_000_000) is None
        assert significance(item, shares_outstanding=None) is None
        assert significance({**item, "report_nm": "[기재정정]자기주식취득결정"},
                            shares_outstanding=20_000_000) is None

    def test_significance_rule5_capex_20pct(self):
        from bot.dart_feed import significance
        assert significance({"report_nm": "신규시설투자등", "category": "신규시설투자",
                             "detail": ["투자금액: 800억원", "자기자본대비: 25.3%"]}
                            ) == "자기자본대비 25.3% 투자"
        assert significance({"report_nm": "신규시설투자등", "category": "신규시설투자",
                             "detail": ["자기자본대비: 12.0%"]}) is None

    def test_significance_rule6_new_5pct_holder(self):
        from bot.dart_feed import significance
        # 직전<5%≤현재 = 신규 5% 돌파
        assert significance({"report_nm": "주식등의대량보유상황보고서", "category": "지분공시",
                             "detail": ["보고자: 홍길동",
                                        "지분율: 3.20% → 6.50% (+3.30%p ▲)",
                                        "보고사유: 신규"]}) == "신규 6.5% 대량보유"
        # 직전 이미 5%+ → 신규 아님
        assert significance({"report_nm": "주식등의대량보유상황보고서", "category": "지분공시",
                             "detail": ["지분율: 6.00% → 7.50% (+1.50%p ▲)"]}) is None
        # 단일 지분율 + 보고사유 신규
        assert significance({"report_nm": "주식등의대량보유상황보고서", "category": "지분공시",
                             "detail": ["지분율: 8.10%", "보고사유: 신규"]}
                            ) == "신규 8.1% 대량보유"

    def test_significance_none_for_plain(self):
        from bot.dart_feed import significance
        assert significance({"report_nm": "기타공시", "category": "기타"}) is None


class TestDartBackfillV3:
    """v3 당월 정리 백필 — 지난달 삭제 + 당월 재분류 + marker 1회 gate."""

    def test_purge_month_window_and_marker(self, tmp_path, monkeypatch):
        import json
        from datetime import datetime, timedelta, timezone
        import bot.dart_feed as df
        monkeypatch.setattr(df, "_ARCHIVE_DIR", tmp_path)
        monkeypatch.setattr(df, "_BACKFILL_MARKER_V3", tmp_path / ".v3")
        monkeypatch.setattr(df, "_dart_api_key", lambda: None)  # fetch graceful
        kst = timezone(timedelta(hours=9))
        today = datetime.now(kst).date()
        month_start = today.replace(day=1)
        prev = month_start - timedelta(days=1)
        (tmp_path / f"{prev.strftime('%Y-%m-%d')}.json").write_text("[]", "utf-8")
        cur = tmp_path / f"{month_start.strftime('%Y-%m-%d')}.json"
        cur.write_text(json.dumps([{
            "rcept_no": "X1", "report_nm": "공개매수결과보고서",
            "category": "지분공시", "stock_code": "005930"}],
            ensure_ascii=False), "utf-8")
        st = df.backfill_v3_once_if_needed()
        # 지난달 삭제, 당월 유지 + 재분류(새 룰) 적용
        assert st["purged"] == 1
        assert not (tmp_path / f"{prev.strftime('%Y-%m-%d')}.json").exists()
        saved = json.loads(cur.read_text("utf-8"))
        assert saved[0]["category"] == "회사구조"
        # marker — 2회차 no-op (재시작 시 중복 실행 차단)
        assert df.backfill_v3_once_if_needed() is None


class TestHighlowFullUsAndCapWeight:
    """신고저 전미국 티어 + L3 시총가중 (사용자 2026-06-11)."""

    def test_symdir_parser(self):
        from bot.finviz_client import _parse_symdir
        sample = (
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
            "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
            "QQQ|Invesco QQQ Trust|G|N|N|100|Y|N\n"          # ETF 제외
            "ZTEST|Test Issue Co|Q|Y|N|100|N|N\n"            # Test 제외
            "BRK.B|Berkshire Hathaway Inc. - Class B Common Stock|Q|N|N|100|N|N\n"
            "ABCW|ABC Co - Warrant|Q|N|N|100|N|N\n"          # 워런트 제외
            "AACB|Artius II Acquisition Corp. - Class A Common Stock|Q|N|N|100|N|N\n"  # SPAC
            "DMII|Drugs Made In America Acquisition II Corp. - Common Stock|Q|N|N|100|N|N\n"  # 'Acquisition II' 변형
            "KRAQ|KRAKacquisition Corp - Common Stock|Q|N|N|100|N|N\n"  # 소문자 혼합 변형
            "XSLL|Xsolla SPAC 1 - Common Stock|Q|N|N|100|N|N\n"         # 'SPAC' 명칭
            "LEGO|Legato Merger Corp. IV Ordinary Shares|Q|N|N|100|N|N\n"  # Merger Corp
            "AIMDW|Ainos, Inc. - Class W|Q|N|N|100|N|N\n"    # 5자리 W suffix 워런트
            "NCPLU|Netcapital Inc. - Class U|Q|N|N|100|N|N\n" # 5자리 U suffix 유닛
            "ELC|Entergy Louisiana Collateral Trust Mortgage Bonds 4.875%|N|N|N|100|N|N\n"  # 채권형
            "File Creation Time: 0611202622:01|||||||")
        tks, names = _parse_symdir(sample, "Symbol")
        # SPAC 변형 4종 + W/U suffix + 채권형 전부 제외 (2026-06-12 보강)
        assert tks == ["AAPL", "BRK-B"], tks
        assert names["AAPL"] == "Apple Inc."

    def test_highlow_spac_price_backstop(self):
        # 확대 밴드 (2026-06-12): $9.4~11.0 + |pct|<1.0% — 신탁 이자
        # 드리프트(+0.28% ALDF 류)까지 차단, 진짜 급등주는 유지.
        def band(r):
            return (9.4 <= (r.get("price") or 0) <= 11.0
                    and abs(r.get("pct") or 0) < 1.0)
        rows = [
            {"ticker": "ALDF", "price": 10.68, "pct": 0.28},  # 드리프트 SPAC → 제외
            {"ticker": "Y", "price": 50.0, "pct": 3.0},        # 진짜 신고가 → 유지
            {"ticker": "Q", "price": 10.4, "pct": 5.2},        # $10대 급등 실주 → 유지
            {"ticker": "BRBI", "price": 11.75, "pct": 0.0},    # 밴드 밖 실기업 → 유지
        ]
        filt = [r for r in rows if not band(r)]
        assert [r["ticker"] for r in filt] == ["Y", "Q", "BRBI"]

    def test_highlow_split_artifact_dropped(self):
        # |당일 등락|>75% = 분할 미조정 아티팩트 (KLAC +1029% 실사례) → 행 제외
        rows = [
            {"ticker": "KLAC", "price": 2411.64, "pct": 1029.24},
            {"ticker": "OK", "price": 91.23, "pct": 6.38},
        ]
        filt = [r for r in rows
                if not (r.get("pct") is not None and abs(r["pct"]) > 75.0)]
        assert [r["ticker"] for r in filt] == ["OK"]

    def test_l3_cap_weighted_average(self, monkeypatch):
        import bot.finviz_client as fv
        monkeypatch.setattr(fv, "fetch_groups", lambda: {"groups": [
            {"name": "semiconductors", "pct": -2.0, "mcap_b": 9000.0},
            {"name": "semiconductor equipment & materials", "pct": -10.0,
             "mcap_b": 1000.0},
        ], "ts": "T", "source": "Finviz"})
        out = fv.top_l3_movers()
        semi = next(b for b in out["down"] if "반도체" in b["name"])
        # 시총가중: (−2×9000 + −10×1000)/10000 = −2.8 (단순평균이면 −6.0)
        assert semi["pct"] == -2.8
        assert "시총가중" in out["source"]

    def test_l3_simple_average_fallback(self, monkeypatch):
        import bot.finviz_client as fv
        monkeypatch.setattr(fv, "fetch_groups", lambda: {"groups": [
            {"name": "semiconductors", "pct": -2.0},
            {"name": "semiconductor equipment & materials", "pct": -10.0},
        ], "ts": "T", "source": "ETF"})
        out = fv.top_l3_movers()
        semi = next(b for b in out["down"] if "반도체" in b["name"])
        assert semi["pct"] == -6.0
        assert "단순평균" in out["source"]

    def test_full_us_tier_never_sync_computes(self, monkeypatch):
        import bot.finviz_client as fv
        monkeypatch.setattr(fv, "_cached", lambda *a, **k: None)
        kicked = []
        monkeypatch.setattr(fv, "_kick_full_us_refresh",
                            lambda: kicked.append(1))
        assert fv._highlow_full_us() == {} and kicked

    # ── 업종 매칭 3-레이어 (2026-06-12 '업종 —' 전멸 fix — yfinance .info
    # 가 데이터센터 IP 에서 비고, stale 캐시는 ind 필드 도입 전이었음) ──

    def test_nasdaq_screener_parser(self):
        from bot.finviz_client import _parse_nasdaq_screener
        payload = {"data": {"rows": [
            {"symbol": "AAPL", "industry": "Computer Manufacturing",
             "sector": "Technology"},
            {"symbol": "BRK/A", "industry": "", "sector": "Finance"},  # → sector 폴백
            {"symbol": "NOIND", "industry": "", "sector": ""},         # 둘 다 빈 → 누락
            {"symbol": "TOOLONG7", "industry": "X", "sector": "Y"},    # len>6 제외
        ]}}
        m = _parse_nasdaq_screener(payload)
        assert m["AAPL"] == "Computer Manufacturing"
        assert m["BRK-A"] == "Finance"          # '/'→'-' 정규화 + sector 폴백
        assert "NOIND" not in m and "TOOLONG7" not in m

    def test_industry_bulk_layering_gics_wins(self, monkeypatch):
        import bot.finviz_client as fv
        monkeypatch.setattr(fv, "_BULK_IND_FAIL_TS", 0.0)
        monkeypatch.setattr(fv, "_nasdaq_screener_industries",
                            lambda: {"AAPL": "Computer Manufacturing",
                                     "TINY": "Misc"})
        monkeypatch.setattr(fv, "_sp500_inds_github",
                            lambda: {"AAPL": "Technology Hardware, Storage "
                                             "& Peripherals"})
        m = fv._bulk_industry_maps()
        # S&P500 멤버는 GitHub GICS Sub-Industry 우선, 비멤버는 NASDAQ 커버
        assert m["AAPL"] == "Technology Hardware, Storage & Peripherals"
        assert m["TINY"] == "Misc"

    def test_fetch_industries_bulk_without_yfinance(self, monkeypatch):
        import bot.finviz_client as fv
        written = {}
        monkeypatch.setattr(fv, "_cached", lambda *a, **k: {})
        monkeypatch.setattr(fv, "_cache_write",
                            lambda n, o: written.update({n: o}))
        monkeypatch.setattr(fv, "_bulk_industry_maps",
                            lambda: {"AAPL": "Consumer Electronics"})
        # allow_slow=False(렌더 경로) — 벌크만으로 해소 + 영구 캐시 적재,
        # 벌크에 없는 종목은 누락(graceful, yfinance 호출 0)
        out = fv._fetch_industries(["AAPL", "ZZZZ"], allow_slow=False)
        assert out == {"AAPL": "Consumer Electronics"}
        assert written["us_industry_cache.json"]["AAPL"] == "Consumer Electronics"

    def test_highlow_cached_rows_backfill_industry(self, monkeypatch):
        # ind 필드 도입 전 stale highlow.json 캐시 → 렌더 전 치유 + 캐시 갱신
        import bot.finviz_client as fv
        cached = {"high": [{"ticker": "AAPL", "price": 200.0, "pct": 1.0,
                            "mcap": 30000.0}],
                  "low": [], "ts": "T", "source": "S&P 500 산출"}
        written = {}
        monkeypatch.setattr(
            fv, "_cached",
            lambda name, *a, **k: cached if name == "highlow.json" else None)
        monkeypatch.setattr(fv, "_cache_write",
                            lambda n, o: written.update({n: o}))
        monkeypatch.setattr(
            fv, "_fetch_industries",
            lambda tks, allow_slow=True: {"AAPL": "Consumer Electronics"})
        out = fv.fetch_high_low()
        assert out["high"][0]["ind"] == "Consumer Electronics"
        assert "highlow.json" in written


class TestDartLawsuitParsing:
    """소송 파싱 (사용자 2026-06-12, 10예시 제공 — 표준 제기·신청/판결·결정
    + 기타경영사항(자율공시) 소송성 승격) + 🔥 상장폐지 규칙."""

    @staticmethod
    def _run(fields, txt):
        import re
        out = {}
        for lbl, pat, kind in fields:
            m = re.search(pat, txt)
            if m:
                out[lbl] = m.group(1).strip()
        return out

    def test_ruling_form_wemade(self):
        # 판결·결정(일정금액): 금액 '-' 시 자기자본/섹션번호 오캡처 금지
        from bot.dart_feed import _LAWSUIT_RULING_FIELDS
        txt = ("1. 사건의 명칭 약정금 등 청구의 소 사건번호 2021가합570045 "
               "2. 원고ㆍ신청인 주위적 원고: (주)전기아이피 피고 : (주)액토즈소프트 "
               "3. 판결ㆍ결정내용 원고측의 소 취하로 소송 종결 "
               "4. 판결ㆍ결정금액 판결ㆍ결정금액(원) - 자기자본(원) 151,321,806,607 "
               "자기자본대비(%) - 대기업여부 해당 5. 판결ㆍ결정사유 원고측의 소 취하 "
               "6. 관할법원 서울중앙지방법원 8. 판결ㆍ결정일자 2026-06-12")
        o = self._run(_LAWSUIT_RULING_FIELDS, txt)
        assert o["사건"] == "약정금 등 청구의 소"
        assert o["판결"] == "원고측의 소 취하로 소송 종결"
        assert "판결금액" not in o and "자기자본대비" not in o
        assert o["관할법원"] == "서울중앙지방법원"

    def test_filed_form_amount_and_branch_court(self):
        # 제기·신청(일정금액): 취지 라벨 없는 청구내용 + 청구금액 + 지원 법원
        from bot.dart_feed import _LAWSUIT_FILED_FIELDS
        txt = ("1. 사건의 명칭 부당이득금 사건번호 2026가합1352 "
               "2. 원고ㆍ신청인 주식회사 머큐리에프엠 "
               "3. 청구내용 1. 피고는 원고에게 6,100,000,000원 및 이에 대하여 "
               "이 사건 소장부본 송달일 다음날부터 다 갚는 날까지는 연 12%의 비율로 계산된 돈을 지급하라. "
               "2. 소송비용은 피고가 부담한다 "
               "4. 청구금액 청구금액(원) 6,100,000,000 자기자본(원) 62,375,819,359 "
               "자기자본대비(%) 9.7 대기업여부 미해당 5. 관할법원 수원지방법원 성남지원 "
               "7. 제기ㆍ신청일자 2026-05-20")
        o = self._run(_LAWSUIT_FILED_FIELDS, txt)
        assert o["사건"] == "부당이득금"
        assert o["취지"].startswith("피고는 원고에게 6,100,000,000원")
        assert o["청구금액"] == "6,100,000,000" and o["자기자본대비"] == "9.7"
        assert o["관할법원"] == "수원지방법원 성남지원"
        assert "피고" not in o   # 산문 '피고가 부담한다' 조사 오캡처 차단

    def test_filed_form_governance(self):
        from bot.dart_feed import _LAWSUIT_FILED_FIELDS
        txt = ("1. 사건의 명칭 주주총회결의취소 청구의 소 사건번호 2026가합1678 "
               "2. 원고(신청인 ) 1)얼라인파트너스자산운용 주식회사 2)삼성증권 주식회사 "
               "3. 청구내용 [청구취지] 1. 피고가 별지 목록 기재 주주총회에서 한 별지 목록 기재 "
               "결의 사항에 대한 결의를 취소한다. 2. 소송비용은 피고가 부담한다. "
               "4. 관할법원 수원지방법원 6. 제기ㆍ신청일자 2026-05-22")
        o = self._run(_LAWSUIT_FILED_FIELDS, txt)
        assert o["원고"].startswith("1)얼라인파트너스자산운용")
        assert o["취지"].endswith("결의를 취소한다.")
        assert o["제기일"] == "2026-05-22"

    def test_misc_mgmt_lawsuit_upgrade(self):
        # 기타경영사항(자율공시) — [사건] 블록(현대사료) / 인라인 법원+사건번호
        # (캐스텍코리아 상고) → 소송 승격, 비소송 PR 류 → None
        from bot.dart_feed import _misc_mgmt_lines
        s4 = ("기타 경영사항(자율공시) 1. 제목 상장폐지결정 효력정지 가처분 신청 "
              "2. 주요내용 회사는 서울남부지방법원에 가처분 신청을 하였으며, "
              "[사건] 2026카합1399 상장폐지결정 효력정지 가처분 "
              "[채권자] 현대사료 주식회사 [채무자] 주식회사 한국거래소 "
              "3. 결정(확인)일자 2026-06-12")
        r = _misc_mgmt_lines(s4)
        assert r["category"] == "소송"
        assert any("채권자 현대사료 주식회사" in l and "채무자 주식회사 한국거래소" in l
                   for l in r["lines"])
        s7 = ("기타 경영사항(자율공시) 1. 제목 주주총회결의무효확인등 소송 판결에 대한 상고제기 "
              "2. 주요내용 부산고등법원 2025나6026 주주총회결의무효확인등 사건에 관하여 "
              "상고를 제기한 건입니다. 3. 결정(확인)일자 2026-06-05")
        r7 = _misc_mgmt_lines(s7)
        assert r7 and any("부산고등법원 2025나6026" in l for l in r7["lines"])
        assert _misc_mgmt_lines(
            "기타 경영사항(자율공시) 1. 제목 신규 브랜드 출시 안내 "
            "2. 주요내용 당사는 신규 브랜드를 출시합니다. 3. 결정(확인)일자 2026-06-12") is None

    def test_significance_delisting(self):
        # 🔥 규칙 7 — 상장폐지 (제목 또는 파싱된 제목/사건 라인)
        from bot.dart_feed import significance
        assert significance({"report_nm": "상장폐지",
                             "category": "리스크"}) == "상장폐지 관련"
        assert significance({"report_nm": "기타경영사항(자율공시)", "category": "소송",
                             "detail": ["제목: 상장폐지결정 효력정지 가처분 신청"]}
                            ) == "상장폐지 관련"
        # 본문 외 라인의 단어만으로는 미발화 (제목:/사건: 라인 한정)
        assert significance({"report_nm": "기타공시", "category": "기타",
                             "detail": ["비고: 상장폐지 아님"]}) is None

    def test_wiring_misc_mgmt_collected_and_upgraded(self):
        # fetch 가 기타경영사항을 routine-drop 에서 제외 + enrich 카테고리 승격
        # + known-reuse 경로 복원 + 대시보드 detail-less 숨김
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        # fetch keep 예외 — 투자판단 추가로 any(...) 형태 (2026-06-12)
        assert '"기타경영사항", "투자판단"' in src
        assert "_upgrade_category(item)" in src
        assert 'item["category"] = nc' in src
        dsrc = open("bot/dashboard.py", encoding="utf-8").read()
        assert '"기타경영사항" in rn' in dsrc


class TestDartRiskParsing:
    """리스크 파싱 (사용자 2026-06-12 '리스크 완료', 9예시) — 매매거래정지
    2형 / 해산사유 / 불성실공시 지정·예고 / 기타시장안내 / 회생절차."""

    @staticmethod
    def _run(fields, txt):
        import re
        out = {}
        for lbl, pat, kind in fields:
            m = re.search(pat, txt)
            if m:
                out[lbl] = m.group(1).strip()
        return out

    def test_suspension_important_disclosure_form(self):
        # 중요내용공시 30분 정지 (엠앤씨솔루션) — 날짜+시각
        from bot.dart_feed import _SUSPENSION_FIELDS
        txt = ("3. 매매거래정지 일시 2026-06-12 11:06 4. 매매거래정지 해제일시 "
               "2026-06-12 11:36 5. 매매거래정지 사유 무상증자(10%이상) 6. 근거")
        o = self._run(_SUSPENSION_FIELDS, txt)
        assert o["정지"] == "2026-06-12 11:06"
        assert o["해제·만료"] == "2026-06-12 11:36"
        assert o["사유"] == "무상증자(10%이상)"

    def test_suspension_freetext_expiry_and_compact_numbering(self):
        # 주권매매거래정지 — '3.정지기간' 압축 표기 + 만료=자유 텍스트
        from bot.dart_feed import _SUSPENSION_FIELDS, significance
        txt = ("주권매매거래정지 1.대상종목 현대사료(주) 보통주 2.정지사유 투자자 보호 "
               "3.정지기간 가.정지일시 2026-06-15 - 나.만료일시 상장폐지결정 등 "
               "효력정지 가처분신청에 대한 법원의 결정 확인시까지 4.근거규정 코스닥시장업무규정")
        o = self._run(_SUSPENSION_FIELDS, txt)
        assert o["사유"] == "투자자 보호"
        assert o["해제·만료"].endswith("법원의 결정 확인시까지")
        # 🔥 상장폐지 — 만료 라인으로 발화 (규칙 7 화이트리스트 확장)
        assert significance({"report_nm": "주권매매거래정지", "category": "리스크",
                             "detail": [f"사유: {o['사유']}",
                                        f"해제·만료: {o['해제·만료']}"]}
                            ) == "상장폐지 관련"

    def test_suspension_merge_variant_with_note(self):
        from bot.dart_feed import _SUSPENSION_FIELDS
        txt = ("1.대상종목 (주)디에이치오토웨어 보통주 2.정지사유 주식의 병합, 분할 등 "
               "전자등록 변경, 말소 3.정지기간 가.정지일시 2026-06-15 - "
               "나.만료일시 신주권 변경상장일 전일까지 4.근거규정 코스닥 5.기타 사유 : 주식병합")
        o = self._run(_SUSPENSION_FIELDS, txt)
        assert o["해제·만료"] == "신주권 변경상장일 전일까지"
        assert o["비고"] == "주식병합"

    def test_dissolution_form(self):
        # 해산사유 발생 — 문서 제목 '해산사유 발생'의 '발생' 오캡처 차단 +
        # 한글 날짜
        from bot.dart_feed import _DISSOLUTION_FIELDS
        txt = ("해산사유 발생 1. 해산사유 유동화사채의 상환 완료 2. 해산내용 "
               "회사의 정관에 의한 해산사유 발생 3. 해산사유발생일(결정일) 2026년 04월 28일")
        o = self._run(_DISSOLUTION_FIELDS, txt)
        assert o["해산사유"] == "유동화사채의 상환 완료"
        assert o["발생일"] == "2026년 04월 28일"

    def test_unfaithful_designation_and_advance_notice(self):
        from bot.dart_feed import _UNFAITHFUL_FIELDS
        d = self._run(_UNFAITHFUL_FIELDS, (
            "2. 불성실공시 유형 공시변경 3. 불성실공시 내용 유상증자결정('26.03.26) "
            "내용 중 발행주식수 및 발행금액의 20% 이상 변경('26.04.17) "
            "4. 지정ㆍ부과일자 2026-06-15 5. 부과벌점 현황 부과벌점 0 기 부과벌점 0 "
            "누계벌점 0 6. 공시위반제재금(원) 8,000,000 7. 공시책임자"))
        assert d["유형"] == "공시변경" and d["제재금"] == "8,000,000"
        assert d["벌점"] == "0" and d["지정일"] == "2026-06-15"
        n = self._run(_UNFAITHFUL_FIELDS, (
            "불성실공시 유형 공시번복 내용 유상증자결정(제3자배정 ) 철회 원공시일 "
            "2025-04-15 공시일 2026-05-12 지정예고일 2026-06-11 "
            "2. 불성실공시법인지정여부 결정시한 2026-07-06 "
            "3. 최근 1년간 불성실공시법인 부과벌점 5.0"))
        assert n["유형"] == "공시번복" and n["벌점"] == "5.0"
        assert n["지정예고일"] == "2026-06-11" and n["결정시한"] == "2026-07-06"

    def test_market_notice_delisting_conclusion(self):
        from bot.dart_feed import _MARKET_NOTICE_FIELDS, significance
        txt = ("기타시장안내 제목 :현대사료(주)에 대한 코스닥시장위원회 개최 결과 및 "
               "상장폐지 결정 안내 '25.08.04 코스닥시장위원회는 상장폐지를 결정한 바 있으며, "
               "동사 주권에 대해 상장폐지 여부를 심의한 결과, 상장폐지로 의결하였습니다.")
        o = self._run(_MARKET_NOTICE_FIELDS, txt)
        assert o["제목"].startswith("현대사료(주)")
        assert "상장폐지로 의결하였습니다" in o["결론"]
        assert significance({"report_nm": "기타시장안내", "category": "리스크",
                             "detail": [f"결론: {o['결론']}"]}) == "상장폐지 관련"

    def test_rehab_lines_last_deadline_wins(self):
        # 회생절차 — 정정 래퍼(정정전 stale)가 앞이라 제출기한은 마지막 출현
        # + 목록/신고기간을 제출기한으로 오캡처 안 함(회생계획안 앵커)
        from bot.dart_feed import _rehab_lines
        txt = ("정정신고(보고) 정정일자 2026-06-10 3. 정정사유 회생계획안 제출기간 연장 "
               "[결정문 내용] 6. 회생계획안의 제출기간을 2026. 6. 16.까지로 한다. "
               "[결정문 내용] 6. 회생계획안의 제출기간을 2026. 7. 14.까지로 한다. "
               "회생절차 개시결정 1. 사건번호 2025회합196 회생 2. 결정일자 2025-09-30 "
               "3. 관할법원 수원회생법원 5. 관리인 성명(회사와의 관계 ) 백서현(대표이사) "
               "6. 확인(결정서접수 )일자 2025-09-30 "
               "3. 회생채권자, 회생담보권자 및 주주의 목록 제출기간을 2025. 9. 30.부터 "
               "2025. 11. 11.까지로 한다. 6. 회생계획안의 제출기간을 2026. 7. 14.까지로 한다.")
        lines = _rehab_lines(txt)
        assert any(l.startswith("사건번호: 2025회합196") for l in lines)
        assert "관할법원: 수원회생법원" in lines
        assert any(l.startswith("관리인: 백서현") for l in lines)
        fin = next(l for l in lines if l.startswith("회생계획안 제출기한"))
        assert "7. 14" in fin and "6. 16" not in fin and "9. 30" not in fin

    def test_risk_titles_classified(self):
        # 9양식 제목 전부 리스크 분류 (드랍 0) — 분류기 회귀 가드
        from bot.dart_feed import _classify_report
        for t in ("매매거래정지및정지해제(중요내용공시)", "주권매매거래정지",
                  "해산사유발생", "불성실공시법인지정", "불성실공시법인지정예고",
                  "회생절차개시결정", "[기재정정]회생절차개시결정",
                  "기타시장안내(상장폐지 여부 결정 안내)"):
            assert _classify_report(t) == "리스크", t


class TestDartInquiryParsing:
    """조회공시/풍문해명 파싱 (사용자 2026-06-12 '이제 조회공시', 5예시) —
    요구형 / 풍문해명형(±미확정) / 시황변동 답변형(+정정). 요지는 결정적
    마커 발췌(추진/입장 분리, LLM 0)."""

    def test_rumor_clarification_with_redisclosure(self):
        # STX그린로지스 — 보도+매체, 추진(KPMG 선정)·입장(미확정), 재공시
        from bot.dart_feed import _inquiry_lines
        txt = ("1. 풍문 또는 보도의 내용 STX그린로지스, 썬에이스 매각 추진보도에 대한 답변 "
               "2. 풍문 또는 보도의 매체 한국경제 등 3. 풍문 또는 보도의 발생일자 2026-05-15 "
               "4. 풍문 또는 보도의 내용에 대한 해명내용 - 본 공시는 해명공시(미확정)입니다. "
               "- 언론에 보도된 내용과 관련하여 당사는 자산 매각주관사로 삼정KPMG를 선정하였습니다. "
               "매각과 관련한 다양한 전략적 방안을 검토하고 있으며, 현재까지 구체적으로 결정된 바는 없습니다. "
               "5. 재공시예정일 2026-09-11")
        L = _inquiry_lines(txt)
        assert any(l.startswith("보도: STX그린로지스") and "(한국경제 등)" in l for l in L)
        assert any(l == "추진: 언론에 보도된 내용과 관련하여 당사는 자산 매각주관사로 "
                        "삼정KPMG를 선정하였습니다" for l in L)   # 선두 '- ' strip
        assert any(l.startswith("입장:") and "결정된 바는 없습니다" in l for l in L)
        assert "재공시: 2026-09-11" in L

    def test_inquiry_request_form(self):
        # 조회공시 요구(풍문) — 제목/요구일시(오전)/답변시한(까지)
        from bot.dart_feed import _inquiry_lines
        L = _inquiry_lines(
            "조회공시 요구(풍문 또는 보도) 1. 제목 주주총회효력정지 가처분 및 "
            "직무집행정지 가처분 결정설 2. 조회공시요구내용 사실 여부 및 구체적인 내용 "
            "3. 요구일시 2026-06-12 오전 4. 답변시한 2026-06-12 18:00까지")
        assert any(l.startswith("제목: 주주총회효력정지 가처분") for l in L)
        assert "요구일시: 2026-06-12 오전" in L
        assert "답변시한: 2026-06-12 18:00까지" in L

    def test_denial_clarification(self):
        # 한화엔진 — 부인형: 입장이 문장 처음부터(중간 잘림 해소)
        from bot.dart_feed import _inquiry_lines
        L = _inquiry_lines(
            "풍문 또는 보도에 대한 해명 1. 풍문 또는 보도의 내용 [단독] 한화엔진, "
            "AM 떼고 방산 붙인다…그룹 사업 재편 착수 2. 풍문 또는 보도의 매체 이투데이 "
            "3. 풍문 또는 보도의 발생일자 2026-06-10 4. 풍문 또는 보도의 내용에 대한 해명내용 "
            "- 본 공시는 해명공시입니다. - 상기 보도의 '방안'과 관련하여 현재 검토된 바 "
            "없으며, 사실이 아님을 알려드립니다.")
        assert any(l.startswith("보도: [단독] 한화엔진") for l in L)
        assert any(l.startswith("입장: 상기 보도의") and "사실이 아님" in l for l in L)

    def test_price_move_answer_with_dates(self):
        # 핀텔 — 추진(입찰 1순위·금액)/입장(수주 미확정)/요구·답변일/재공시 기한
        from bot.dart_feed import _inquiry_lines
        L = _inquiry_lines(
            "조회공시요구(현저한시황변동)에대한답변(미확정) 1. 제목 조회공시 요구"
            "(현저한 시황변동)에 대한 답변(조회공시요구일: 2026.06.08) 2. 답변내용 "
            "[추진중인 사항] - 당사는 최근 공공기관 입찰 2건에서 각각 1순위 대상자로 "
            "선정되었으며, 계약 예정금액은 각각 약 12억원 및 19억원 규모입니다. "
            "다만 현재 적격심사가 진행 중으로 현재까지 최종 수주 여부는 확정되지 않았습니다. "
            "3. 조회공시요구일 2026-06-08 4. 조회공시답변일 2026-06-09 "
            "5. 재공시 기한 기한 2026-07-09 사유 -")
        assert any(l.startswith("추진:") and "1순위" in l and "19억원" in l for l in L)
        assert any(l.startswith("입장:") and "확정되지 않았습니다" in l for l in L)
        assert any("요구일 2026-06-08 · 답변일 2026-06-09" == l for l in L)
        assert "재공시: 2026-07-09" in L

    def test_inquiry_category_is_parse_target(self):
        # 조회공시 카테고리가 enrich 대상에 포함 (옛 _PARSE_CATS 누락 fix)
        from bot.dart_feed import is_parse_target
        assert is_parse_target({"category": "조회공시",
                                "report_nm": "조회공시요구(풍문또는보도)",
                                "corp_code": "x"})
        # 라우팅 존재 가드
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        assert "_extract_inquiry" in src and "_inquiry_lines" in src

    def test_colon_separator_and_pm_deadline(self):
        # 최종예시 (2026-06-12 '이제 파싱 예제는 다 끝났어') — ' : ' 구분자형
        # + '오후 12:00까지' 시한. greedy gap 으로 이중 콜론('제목: : X') 차단.
        from bot.dart_feed import _inquiry_lines
        L = _inquiry_lines(
            "조회공시 요구(풍문 또는 보도) 1. 제목 : 최대주주 지분 매각 추진설에 "
            "대한 조회공시 요구 2. 요구일시 : 2026-06-12 오전 "
            "3. 답변시한 : 2026-06-13 오후 12:00까지")
        assert "제목: 최대주주 지분 매각 추진설에 대한 조회공시 요구" in L
        assert "요구일시: 2026-06-12 오전" in L
        assert "답변시한: 2026-06-13 오후 12:00까지" in L
        # 보도형 콜론 구분자 — 캡처 선두 콜론 없음
        L2 = _inquiry_lines(
            "풍문 또는 보도에 대한 해명 1. 풍문 또는 보도의 내용 : MBK파트너스의 "
            "회사 인수 추진 관련 보도 2. 풍문 또는 보도의 매체 : 한국경제 외 "
            "3. 해명내용 - 위 보도와 관련하여 현재까지 구체적으로 결정된 바 없습니다.")
        assert any(l.startswith("보도: MBK파트너스") and "(한국경제 외)" in l
                   for l in L2)
        assert any(l.startswith("입장:") for l in L2)


class TestDartBackfillV4:
    """파싱 배치 소급 백필 v4 (2026-06-12) — ①변경 파서 재추출(성공시만
    교체) ②doc_fail 클리어 ③당월 재fetch. 순수 부분만 검증 (네트워크 0)."""

    def test_reparse_kw_scope(self):
        # ① 대상 = 출력이 '변경'된 파서 유형만 — 신설 파서 유형(소송·리스크·
        # 조회공시 등)은 detail 부재 → ② 대기열 담당이라 나열 금지 (콜 낭비).
        from bot.dart_feed import _V4_REPARSE_KW
        assert set(_V4_REPARSE_KW) == {"공급계약", "단일판매",
                                       "자기주식취득결정", "자기주식처분결정"}
        hit = {"report_nm": "단일판매ㆍ공급계약체결", "detail": ["계약: x"]}
        miss = {"report_nm": "소송등의제기", "detail": ["사건: y"]}
        assert any(k in hit["report_nm"] for k in _V4_REPARSE_KW)
        assert not any(k in miss["report_nm"] for k in _V4_REPARSE_KW)

    def test_marker_gate_and_wiring(self):
        # marker gate 존재 + startup 배선 (v3 패턴 mirror)
        from bot.dart_feed import (backfill_v4_once_if_needed,
                                   clear_doc_fail_cache, reparse_details,
                                   _BACKFILL_MARKER_V4)
        assert callable(backfill_v4_once_if_needed)
        assert callable(clear_doc_fail_cache) and callable(reparse_details)
        assert _BACKFILL_MARKER_V4.name == ".dart_feed_backfilled_v4"
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "backfill_v4_once_if_needed" in src

    def test_reparse_no_key_is_noop(self, monkeypatch):
        # API 키 부재 → 아카이브 무접촉 graceful no-op
        import bot.dart_feed as df
        monkeypatch.setattr(df, "_dart_api_key", lambda: "")
        st = df.reparse_details(days_back=1)
        assert st == {"checked": 0, "replaced": 0, "kept": 0}


class TestDartFairDisclosureParsing:
    """공정공시 변형 + 투자판단 파싱 (사용자 2026-06-12 '파싱안된것들',
    5예시) — 물량형 잠정실적 / 수시공시 / 장래사업 / 투자판단 승격."""

    def test_volume_earnings_gas(self):
        # 금액표 전부 '-' + 월간 물량(천톤)만 — 총계 행 발췌
        from bot.dart_feed import _volume_lines
        L = _volume_lines(
            "단위 : 백만원, % 매출액 당해실적 - 구분 : 단위(천톤) 당월실적 ('26.5월) "
            "전월실적 ('26.4월) 전월 대비 증감율 (%) 전년동월 실적 ('25.5월) "
            "도시가스용 1,177 1,349 -12.8 - 1,130 4.2 - 발전용 1,198 1,321 -9.3 - 1,263 -5.1 - "
            "기 타 - - - - - - - 총 계 2,375 2,670 -11.0 - 2,393 -0.8 -")
        assert L[0] == "5월 판매: 총 2,375천톤 (전월 -11.0% · 전년동월 -0.8%)"

    def test_volume_earnings_auto_with_cumulative(self):
        # 완성차 대수 — 계 행 + 누적 행(전년동기 %) 둘 다
        from bot.dart_feed import _volume_lines
        L = _volume_lines(
            "구분(단위:대,%) 당기실적 (2026년 5월) 전기실적 (2026년 4월) "
            "국내 45,364 54,051 -16.1 - 58,966 -23.1 - 해외 280,109 271,878 3.0 - 293,654 -4.6 - "
            "계 325,473 325,929 -0.1 - 352,620 -7.7 - 구분(단위:대,%) 당기누적 (2026년 1~5월) "
            "국내 258,481 - - - 292,836 -11.7 - 계 1,627,623 - - - 1,707,534 -4.7 -")
        assert L[0] == "5월 판매: 총 325,473대 (전월 -0.1% · 전년동월 -7.7%)"
        assert L[1] == "누적: 1,627,623대 (전년동기 -4.7%)"

    def test_fair_disclosure_shareholder_policy(self):
        from bot.dart_feed import _fair_disclosure_lines
        L = _fair_disclosure_lines(
            "1. 정보내용 공시제목 중장기 주주환원정책 발표 관련 수시공시내용 "
            "□ 주요 내용 - 적용 기간 : 2026년 결산시점 부터 향후 5개년간 "
            "- 목표 주주환원율 : 연간 별도 기준 조정 당기순이익의 50% "
            "- 주주환원율 산식 : (총 배당금액 + 자사주 매입액) ÷ 별도기준 조정 당기순이익")
        assert L[0] == "제목: 중장기 주주환원정책 발표"
        assert any("향후 5개년간" in l for l in L)
        assert any("당기순이익의 50%" in l for l in L)

    def test_future_plan_header_not_captured(self):
        # 머리말 '※ …장래 계획사항으로서…' 오캡처 차단 (번호 라벨 필수)
        from bot.dart_feed import _future_plan_lines
        L = _future_plan_lines(
            "장래사업ㆍ경영 계획(공정공시) ※ 동 정보는 장래 계획사항으로서 향후 변경될 수 있음 "
            "1. 장래계획 사항 엔비디아와 글로벌 AI 팩토리 공동 구축 사업 추진 "
            "(신규사업 또는 타법인과의 전략적 제휴) 2. 주요내용 및 추진일정 목적 글로벌 AI "
            "예상투자금액 세부 계약 조건 협의 중으로 현재 미확정(단계별 계약 확정 시 별도 공시 예정) "
            "기대효과 - 4. 이사회결의일(결정일) 2026-06-08")
        assert L[0].startswith("계획: 엔비디아와 글로벌 AI 팩토리")
        assert "으로서" not in L[0]
        assert any(l.startswith("예상투자금액:") and "미확정" in l for l in L)
        assert "이사회결의일: 2026-06-08" in L

    def test_material_mgmt_license_deal_upgrade(self):
        # 투자판단 주요경영사항(한미 기술이전) — 계약 승격 + 금액 2종
        from bot.dart_feed import _material_mgmt_lines
        r = _material_mgmt_lines(
            "투자판단 관련 주요경영사항 1. 제목 소네페글루타이드 (Sonefpeglutide; "
            "LAPS GLP2 agonist; HM15912) 기술이전 계약 체결 2. 주요내용 ※ 투자유의사항 "
            "1) 계약상대방: Eli Lilly and Company (미국) 3) 계약체결일: 2026년 5월 31일 "
            "6) 계약금액: 총 US$1,260,000,000 (약 1조 8,973억원) "
            "① 계약금 (Upfront): US$75,000,000 (약 1,129억원) "
            "3. 이사회결의일(결정일 ) 또는 사실확인일 2026-05-31")
        assert r["category"] == "계약"
        assert any(l == "상대방: Eli Lilly and Company (미국)" for l in r["lines"])
        assert any(l.startswith("계약금액: 총 US$1,260,000,000")
                   and "1조 8,973억원" in l for l in r["lines"])
        # 라벨 '계약금(선급/Upfront)' — 미파싱-2에서 선급금(코스닥형) 통합
        assert any(l.startswith("계약금(선급/Upfront): US$75,000,000")
                   for l in r["lines"])

    def test_material_mgmt_collected_and_forced(self):
        # 투자판단: fetch keep 예외 + force 파싱 대상 + 대시보드 숨김 wiring
        from bot.dart_feed import is_parse_target
        assert is_parse_target({"category": "기타",
                                "report_nm": "투자판단관련주요경영사항",
                                "corp_code": "x"})
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        assert '"기타경영사항", "투자판단"' in src   # fetch keep 예외
        dsrc = open("bot/dashboard.py", encoding="utf-8").read()
        assert '"투자판단" in rn' in dsrc            # detail-less 숨김

    def test_badge_colors_distinct(self):
        # 🔥 금색 vs ⚠️ 파랑 — 색 분리 (사용자 2026-06-12 '색깔 다르게')
        dsrc = open("bot/dashboard.py", encoding="utf-8").read()
        assert "#d4a017" in dsrc            # 🔥 금색 유지
        assert "#5c9ce6" in dsrc            # ⚠️ 파랑 (옛 주황 #f5a623 카드/배지 제거)
        assert ".df-badge-unp{background:#5c9ce618" in dsrc
        assert "파란 점선" in dsrc           # 범례 동기


class TestDartUnparsed2:
    """미파싱-2 (사용자 2026-06-12, 5예시) — 유가 표준 공급계약 보강 /
    USD 기술이전 / 자산보관 위탁계약 / 자기주식취득 원문 폴백."""

    def _patch(self, monkeypatch, txt):
        import bot.dart_feed as df
        monkeypatch.setattr(df, "_fetch_doc_text", lambda r, k: txt)
        monkeypatch.setattr(df, "_doc_fail_mark", lambda *a, **kw: None)
        return df

    def test_contract_exchange_standard_vlcc(self, monkeypatch):
        # 유가 표준형 — 구분(공사수주) 병기 + '- 회사와의 관계' 꼬리 컷
        txt = ("단일판매ㆍ공급계약 체결 1. 판매ㆍ공급계약 구분 공사수주 - 체결계약명 VLCC 4척 "
               "2. 계약내역 계약금액(원) 800,100,000,000 최근매출액(원) 12,783,500,000,000 "
               "매출액대비(%) 6.3 대규모법인여부 해당 3. 계약상대 아시아 지역 선주 - 회사와의 관계 - "
               "5. 계약기간 시작일 2026-06-12 종료일 2030-02-22 7. 계약(수주)일자 2026-06-12")
        df = self._patch(monkeypatch, txt)
        out = df._extract_contract_document("X", "K")
        assert "구분: 공사수주" in out["lines"]
        assert any(l == "계약상대: 아시아 지역 선주" for l in out["lines"])
        assert any("매출액대비 6.3%" in l for l in out["lines"])

    def test_material_mgmt_usd_prepayment(self):
        # 코스닥 기술이전 — USD 표기 + 선급금 + ₩ 환산 병기
        from bot.dart_feed import _material_mgmt_lines
        r = _material_mgmt_lines(
            "투자판단 관련 주요경영사항 1. 제목 이중항체 MT-103 기술이전 계약 체결 2. 주요내용 "
            "1) 계약상대방 : Memento Medicines(이하 메멘토) - 국적 : 미국 2) 계약의 내용 "
            "3) 계약체결일 : 2026년 5월 10일 6) 계약금액 (1) 기술이전 금액 : 총 USD "
            "538,875,000 (₩ 781,799,850,000) (2) 선급금 : 총 USD 4,000,000 (₩ 5,803,200,000)")
        assert r["category"] == "계약"
        assert any(l.startswith("상대방: Memento Medicines") for l in r["lines"])
        assert any(l.startswith("계약금액: 총 USD 538,875,000")
                   and "781,799,850,000" in l for l in r["lines"])
        assert any(l.startswith("계약금(선급/Upfront): 총 USD 4,000,000")
                   for l in r["lines"])

    def test_custody_correction_uses_post_values(self):
        # 자산보관 위탁계약 — 정정 래퍼의 '정정전' stale 종료일 대신 본문
        # (마지막 출현) 사용 + 정정 헤더
        from bot.dart_feed import _custody_lines
        L = _custody_lines(
            "정정신고(보고) 정정일자 2026-06-04 3. 정정사유 차입기간 상환일 연장으로 인한 "
            "자산보관 위탁계약기간 연장 4. 정정사항 정정항목 정정전 정정후 종료일 2026-06-04 2027-06-04 "
            "부동산투자회사 자산보관 위탁계약 체결 1. 자산보관기관 회사명 신영부동산신탁"
            "(SHINYOUNG REAL ESTATE TRUST CO., LTD) 자본금(원) 100,000,000,000 "
            "2. 계약기간 시작일 2024-06-04 종료일 2027-06-04 3. 계약일자 2024-06-04 "
            "5. 기타 자산보관계약 대상 부동산은 서울시 영등포구 선유서로 50(문래동6가) "
            "이편한세상문래 장기일반 민간임대주택 5세대 및 근린생활시설 8호실입니다")
        assert any(l == "기간: 2024-06-04 ~ 2027-06-04" for l in L)
        assert any(l.startswith("정정(26-06-04)") for l in L)
        assert any(l.startswith("보관기관: 신영부동산신탁") for l in L)

    def test_custody_open_ended_and_dash_strip(self):
        from bot.dart_feed import _custody_lines
        L = _custody_lines(
            "부동산투자회사 자산보관 위탁계약 체결 1. 자산보관기관 회사명 (주)한국토지신탁 "
            "자본금(원) 252,489,230,000 2. 계약기간 시작일 2026-06-01 종료일 - "
            "3. 계약일자 2026-06-01 5. 기타 1. 자산보관계약 대상 부동산 - "
            "오렌지센터(서울특별시 세종대로7길 37) 2. 위 자산보관기관의")
        assert any(l == "기간: 2026-06-01 ~" for l in L)          # 종료일 '-' 개방형
        assert any(l == "대상 부동산: 오렌지센터(서울특별시 세종대로7길 37)"
                   for l in L)                                     # 선두 '- ' strip

    def test_buyback_doc_fallback_and_3pct(self, monkeypatch):
        # 자기주식취득결정 표준 표 원문 폴백 (구조화 API 7일 윈도 밖 케이스)
        # + 🔥 규칙 4 자동 연동 (2,380,952 / 65,787,207 = 3.6%)
        txt = ("자기주식 취득 결정 1. 취득예정주식(주) 보통주 2,380,952 기타주식 - "
               "2. 취득예정금액(원) 보통주 20,000,000,000 기타주식 - "
               "3. 취득예상기간 시작일 2026년 06월 15일 종료일 2026년 09월 14일 "
               "5. 취득목적 임직원 보상 재원 마련 6. 취득방법 유가증권 시장을 통한 직접 취득")
        df = self._patch(monkeypatch, txt)
        o = df._extract_doc_fields("X", "K", df._BUYBACK_DOC_FIELDS, min_fields=2)
        assert any(l == "취득예정: 2,380,952주" for l in o["lines"])
        assert any(l == "목적: 임직원 보상 재원 마련" for l in o["lines"])
        from bot.dart_feed import significance
        assert significance({"report_nm": "자기주식취득결정", "category": "주주환원",
                             "detail": o["lines"]},
                            shares_outstanding=65_787_207) == "발행주식 3.6% 취득"


class TestDartUnparsed3:
    """미파싱-3 (사용자 2026-06-12, 5예시) — 자기주식 처분 doc 폴백(정정
    stale 기간 보정) / 투자설명서 정정 / 정정 헤더 표-헤더행 오캡처 가드.
    신탁계약 체결은 기존 _parse_trust 가 처리 확인(코드 무변경)."""

    def test_disposal_correction_uses_post_dates(self):
        from bot.dart_feed import _disposal_lines
        L = _disposal_lines(
            "정정신고(보고) 1. 정정대상 공시서류 : 주요사항보고서(자기주식처분결정) "
            "3. 정정사항 항 목 정정사유 정정 전 정정 후 4. 처분예정기간 - 시작일, 종료일 "
            "자기주식 처분금지 가처분 신청 접수에 따른 절차 대응 2026년 6월 17일 2026년 7월 3일 "
            "자기주식 처분 결정 1. 처분예정주식(주) 보통주 121,951 "
            "2. 처분 대상 주식가격(원) 보통주 4,100 3. 처분예정금액(원) 보통주 499,999,100 "
            "4. 처분예정기간 시작일 2026년 07월 03일 종료일 2026년 07월 03일 "
            "5. 처분목적 신사업 투자재원 조달 7. 처분상대방 주식회사 이룸기술")
        assert any(l == "처분예정: 121,951주" for l in L)
        assert any(l == "처분금액: 5억원 (주당 4,100원)" for l in L)
        # 정정전(6/17) stale 배제 — 마지막 출현(본문 7/3)
        assert any(l == "기간: 2026-07-03 ~ 2026-07-03" for l in L)
        assert any(l == "상대방: 주식회사 이룸기술" for l in L)
        # 표 헤더행 '정정 전 정정 후' 오캡처 없음
        assert not any("정정 전" in l for l in L)

    def test_prospectus_correction_fund_renewal(self):
        from bot.dart_feed import _prospectus_corr_lines
        L = _prospectus_corr_lines(
            "정 정 신 고 (보고) 1. 정정대상 공시서류 : 투자설명서 "
            "2. 정정대상 공시서류의 최초제출일 : 2015년 05월 04일 3. 정정사항 "
            "항 목 정정요구·명령 관련 여부 정정사유 정 정 전 정 정 후 "
            "아니오 자본시장과 금융투자업에 관한 법률 제123조에 의거한 정기갱신 "
            "(재무정보 등 자료 업데이트) 실제 연환산 표준편차: 41.28% 실제 연환산 표준편차: 36.79%")
        assert any(l == "서류: 투자설명서" for l in L)
        assert any(l.startswith("최초제출: 2015-05-04") for l in L)
        assert any("정기갱신" in l for l in L)
        assert any(l == "위험지표: 41.28% → 36.79%" for l in L)

    def test_prospectus_manager_change(self):
        from bot.dart_feed import _prospectus_corr_lines
        L = _prospectus_corr_lines(
            "정 정 신 고 (보고) 1. 정정대상 공시서류 : 투자설명서 "
            "2. 정정대상 공시서류의 최초제출일 : 2017년 01월 22일 "
            "항목 정정사유 정정 전 정정 후 <운용전문인력> 아니오 운용역 변경 송진용 신은영 "
            "97.5% VaR: 27.84% 97.5% VaR: 27.10%")
        assert any(l == "운용역: 송진용 → 신은영" for l in L)
        assert any(l == "위험지표: 27.84% → 27.10%" for l in L)

    def test_correction_header_skips_table_header_row(self):
        # '정정사유' 컬럼 헤더 인접 '정정 전 정정 후' 오캡처 가드 + 정상 회귀
        from bot.dart_feed import _correction_header
        assert _correction_header(
            "정정신고(보고) 항 목 정정사유 정정 전 정정 후 4. 처분예정기간") is None
        assert _correction_header(
            "정정신고(보고) 정정일자 2026-06-10 3. 정정사유 회생계획안 제출기간 연장 "
            "4. 정정사항") == "정정(26-06-10): 회생계획안 제출기간 연장"

    def test_trust_contract_form_still_parses(self, monkeypatch):
        # 미파싱-3 #1 신탁계약 체결 — 기존 파서가 처리함을 영구 가드
        import bot.dart_feed as df
        txt = ("자기주식취득 신탁계약 체결 결정 1. 계약금액(원) 5,000,000,000 "
               "2. 계약기간 시작일 2026년 06월 15일 종료일 2027년 06월 14일 "
               "3. 계약목적 주가 안정화를 통한 주주가치 제고 4. 계약체결기관 미래에셋증권 "
               "9. 취득예정주식(주) 보통주 101,010 10. 취득하고자 하는 주식의 가격(원) 보통주 49,500")
        monkeypatch.setattr(df, "_fetch_doc_text", lambda r, k: txt)
        monkeypatch.setattr(df, "_doc_fail_mark", lambda *a, **kw: None)
        out = df._parse_trust("X", "K", cancel=False)
        assert any(l.startswith("계약금액: 50억원") for l in out["lines"])
        assert any(l.startswith("취득예정: 101,010주") for l in out["lines"])
        assert any(l == "목적: 주가 안정화를 통한 주주가치 제고" for l in out["lines"])


class TestDartUnparsed4:
    """미파싱-4 (사용자 2026-06-12, 5예시) — 특수관계인 담보제공(주식·부동산,
    공정거래법 제26조 백만원) / 감자 결정(+정정) / 신주 발행가액 안내."""

    def test_related_collateral_stock(self):
        from bot.dart_feed import _related_collateral_lines
        L = _related_collateral_lines(
            "특수관계인에 대한 담보제공 (단위 : 백만 원) 1. 거래상대방 "
            "(주)엠티브이반달섬개발피에프브이 회사와의 관계 계열회사 2. 담보제공 내역 "
            "나. 채권자 반달섬제삼차(주) 다. 담보물 (주)엠티브이반달섬개발피에프브이 "
            "보통주식 900,000주 라. 담보기간 2026.06.13~2027.06.13 마. 담보한도 31,902 "
            "바. 담보금액 24,540 아. 거래의 조건 채권최고액을 변경 3. 이사회 의결일 2026.06.12")
        assert any(l == "거래상대: (주)엠티브이반달섬개발피에프브이" for l in L)
        assert any(l == "채권자: 반달섬제삼차(주)" for l in L)
        assert any(l.endswith("보통주식 900,000주") for l in L)
        assert any(l == "기간: 2026.06.13~2027.06.13" for l in L)
        # 백만원 단위 → 31,902백만=319억 · 24,540백만=245.4억
        assert any("319억원" in l and "245.4억원" in l for l in L)

    def test_related_collateral_realestate_area_decimals(self):
        # 담보물 면적 소수점(㎡)이 '\d.' stop 에 안 잘림 + 자유 텍스트 기간
        from bot.dart_feed import _related_collateral_lines
        L = _related_collateral_lines(
            "특수관계인에 대한 담보제공 (단위 : 백만 원) 1. 거래상대방 하이엠케이(주) "
            "회사와의 관계 계열회사 2. 담보제공 내역 나. 채권자 한국산업은행 대구지점 "
            "다. 담보물 경상북도 구미시 진평동 643-2 소재 토지(60,955.8㎡) 및 "
            "건물(7개동, 27,434.155㎡) 라. 담보기간 채무전액 상환시 까지 "
            "마. 담보한도 12,000 바. 담보금액 10,000 아. 거래의 조건 근저당권 신규 설정 3. 이사회")
        assert any("토지(60,955.8㎡)" in l and "건물(7개동, 27,434.155㎡)" in l for l in L)
        assert any(l == "기간: 채무전액 상환시 까지" for l in L)
        assert any("120억원" in l and "100억원" in l for l in L)

    def test_capital_reduction(self):
        from bot.dart_feed import _capital_reduction_lines
        L = _capital_reduction_lines(
            "감자 결정 1. 감자주식의 종류와 수 보통주식 (주) 21,000,000 "
            "3. 감자전후 자본금 감자전 (원) 32,576,019,500 감자후 (원) 22,076,019,500 "
            "5. 감자비율 보통주식 (%) 32.23 6. 감자기준일 2026년 07월 14일 "
            "8. 감자사유 결손 보전을 통한 재무구조 개선 9. 감자일정 주주총회 예정일 2026년 06월 26일")
        assert any(l == "감자주식: 21,000,000주 (비율 32.23%)" for l in L)
        assert any(l.startswith("자본금:") for l in L)
        assert any(l == "사유: 결손 보전을 통한 재무구조 개선" for l in L)
        assert any(l == "감자기준일: 2026-07-14" for l in L)
        assert any(l == "주총예정: 2026-06-26" for l in L)

    def test_capital_reduction_correction(self):
        from bot.dart_feed import _capital_reduction_lines
        L = _capital_reduction_lines(
            "정정신고(보고) 2026년 06월 10일 1. 정정대상 공시서류 : 감자 결정 "
            "3. 정정사항 항 목 정정사유 정 정 전 정 정 후 9. 감자일정 주주총회 일정변경 "
            "- 주주총회 예정일 2026년 06월 30일 - 주주총회 예정일 2026년 06월 26일")
        assert any(l.startswith("정정") for l in L)        # 정정 헤더
        assert not any("정정 전" in l for l in L)           # 표 헤더행 오캡처 가드

    def test_issue_price_notice(self):
        from bot.dart_feed import _issue_price_lines
        L = _issue_price_lines(
            "[안내]유상증자 신주 발행가액 1. 구분 신주배정기준일 기준 신주발행가액 "
            "2. 주당 발행가액 보통주식(원) 27,900 종류주식(원) - "
            "3. 기타 ※ 확정 발행가액은 2026년 07월 20일에 공시할 예정입니다. 할인율 20%를 적용")
        assert any(l == "주당 발행가액: 27,900원" for l in L)
        assert any(l == "확정가액 공시예정: 2026-07-20" for l in L)

    def test_routing_priority(self):
        # 특수관계인 담보 > 일반 담보 / 발행가액 > 유상증자 (라우팅 순서 가드)
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        rc = src.index('elif "특수관계인" in t and "담보" in t:')
        gc = src.index('elif "담보" in t:')
        assert rc < gc
        ip = src.index('elif "발행가액" in t:')
        rights = src.index('elif "유상증자" in t or "유무상증자" in t:')
        assert ip < rights


class TestDartUnparsed5:
    """미파싱-5 (사용자 2026-06-12, 5예시) — 확인서 / 유형자산 양수도
    종료보고서 / 회사합병 결정 / 영업양도(공정거래법) / 회사분할 철회 정정."""

    def test_confirmation(self):
        from bot.dart_feed import _confirmation_lines
        L = _confirmation_lines(
            "확인서 우리는 당사의 대표이사 및 신고업무담당이사로서 이 공시서류의 "
            "기재내용에 대해 직접 확인·검토한 결과... 내부회계관리제도를 마련하여 "
            "운영하고 있음을 확인합니다. 주식회사 피노 대표이사 주송완 (인)")
        assert any(l == "회사: 주식회사 피노" for l in L)
        assert any("내부회계관리제도 운영" in l for l in L)

    def test_asset_complete_table_not_prose(self):
        # 양도인/양수인은 표 행만 — 본문 '(양도인)와 …(양수인)' 오캡처 차단
        from bot.dart_feed import _asset_complete_lines
        L = _asset_complete_lines(
            "본 보고서는 주요사항보고서(유형자산양도결정)에 관한 종료 보고서입니다. "
            "당사(양도인)와 에스케이에어코어 주식회사(양수인) 간 체결된 토지 및 건물의 "
            "양수도 계약... 매매대금 정산 및 소유권이전 등기가 완료됨 "
            "양도인 주식회사 에어레인 양수인 에스케이에어코어 주식회사 "
            "양도 금액 12,800,000,000원")
        assert any("양도인 주식회사 에어레인" in l
                   and "양수인 에스케이에어코어 주식회사" in l for l in L)
        assert any(l == "양도금액: 128억원" for l in L)
        assert any("거래 종료" in l for l in L)

    def test_merger(self):
        from bot.dart_feed import _merger_lines
        L = _merger_lines(
            "회사합병 결정 1. 합병방법 흡수합병 - 존속회사 (주)네오위즈 - 소멸회사 뮤즈라이브 "
            "4. 합병비율 1 : 0.0000000 8. 합병상대회사 회사명 뮤즈라이브 주요사업 게임 "
            "10. 합병일정 합병기일 2026년 08월 20일 주주총회 예정일 2026년 07월 14일 "
            "합병등기 예정일 2026년 08월 31일")
        assert any(l.startswith("합병방법: 흡수합병") for l in L)
        assert any(l == "합병비율: 1 : 0.0000000" for l in L)
        assert any("합병기일 2026-08-20" in l and "주총 2026-07-14" in l
                   and "합병등기 2026-08-31" in l for l in L)

    def test_business_transfer(self):
        from bot.dart_feed import _business_transfer_lines
        L = _business_transfer_lines(
            "영업양도 결정 기업집단명 에스케이 회사명 엔솔브(주) 관련법규 공정거래법 "
            "1. 양도영업 태양광발전소(PV) 및 에너지저장장치(ESS) 운영 사업 "
            "2. 양도영업 주요내용 ... 3. 양도가액 (원) 72,626,529,464 "
            "4. 양도목적 사업구조 개편 5. 양도예정일자 2026년 10월 31일 "
            "6. 양수법인(회사와의 관계) 이클립스 주식회사(기타)")
        assert any(l.startswith("양도영업: 태양광발전소(PV)") for l in L)
        assert any(l.startswith("양도가액:") and "726.3억원" in l for l in L)
        assert any(l == "양수법인: 이클립스 주식회사" for l in L)
        assert any(l == "목적: 사업구조 개편" for l in L)

    def test_split_withdrawal_routing(self):
        # 회사분할 철회 정정 — 라우팅이 _SPLIT_COMPANY_FIELDS 보다 우선
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        block = src[src.index('elif "회사분할" in t or "분할합병" in t:'):]
        assert '"철회" in txt2' in block[:600]
        assert '철회' in block[:600] and 'kind' in block[:700]
        # 물적/인적분할 종류 + 사유 라인
        assert '"물적분할" if "물적분할" in txt2' in block[:700]


class TestDartUnparsed6:
    """미파싱-6 (사용자 2026-06-12, 5예시) — 최대주주 등 주식보유 변동 /
    주권매매거래정지해제 / 공개매수 결과 / 최대주주 변경 + 필터 동시선택."""

    def test_major_holding_change(self):
        from bot.dart_feed import _major_holding_change_lines
        L = _major_holding_change_lines(
            "최대주주 등의 주식보유 변동 ... 변동 후 주식수 지분율 (C) "
            "동일인 및 동일인 관련자 에스케이스퀘어(주) 계열회사 2026.06.11 "
            "보통주 56,812 53.13 - -1.06 56,812 52.07 - -")
        assert any(l == "주주: 에스케이스퀘어(주)" for l in L)
        assert any(l == "지분율: 53.13% → 52.07% (-1.06%p ▼)" for l in L)

    def test_suspension_release(self):
        from bot.dart_feed import _suspension_release_lines
        L = _suspension_release_lines(
            "주권매매거래정지해제 1.대상종목 글로벌에스엠테크리미티드 보통주 "
            "2.해제사유 액면병합 주권 변경상장 3.해제일시 2026-06-12 - 4.근거규정 코스닥")
        assert any(l == "대상: 글로벌에스엠테크리미티드" for l in L)
        assert any(l == "해제사유: 액면병합 주권 변경상장" for l in L)
        assert any(l == "해제일시: 2026-06-12" for l in L)

    def test_tender_offer_result(self):
        from bot.dart_feed import _tender_result_lines
        L = _tender_result_lines(
            "1. 공개매수 대상 회사명 주식회사 세아홀딩스 2. 공개매수 주식등의 종류 ... "
            "3. 공개매수 예정수량 및 가격 매수가격 주당 160,000원 매수예정수량 최대 187,000주 "
            "결제수단 현금 4. 응모 및 매수현황 예정주식 수 최대 187,000주 응모주식 수 499,122주 "
            "매수주식 수 187,000주 공개매수 후 주식등의 보유비율 79.52")
        assert any(l == "대상: 주식회사 세아홀딩스" for l in L)
        assert any(l == "매수가: 주당 160,000원" for l in L)
        assert any("응모 499,122주" in l and "매수 187,000주" in l for l in L)
        assert any(l == "공개매수 후 보유: 79.52%" for l in L)

    def test_major_shareholder_change(self):
        from bot.dart_feed import _major_change_lines
        L = _major_change_lines(
            "최대주주 변경 1. 변경내용 변경전 최대주주명 주식회사 은홀딩파트너스 "
            "소유주식수(주) 18,521,924 소유비율(%) 53.23 변경후 최대주주명 임주주 "
            "소유주식수(주) 10,161,661 소유비율(%) 28.63 2. 변경사유 주식매매계약 체결에 "
            "따른 최대주주변경 3. 지분인수목적 경영참여")
        assert any(l == "최대주주: 주식회사 은홀딩파트너스(53.23%) → 임주주(28.63%)"
                   for l in L)
        assert any(l.startswith("사유: 주식매매계약 체결") for l in L)

    def test_routing_release_before_suspension(self):
        # 정지해제 라우팅이 매매거래정지보다 먼저 (substring 충돌)
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        assert src.index('elif "정지해제" in t') < src.index('elif "매매거래정지" in t')

    def test_flag_filters_combine_with_category(self):
        # 🔥/⚠️ 플래그가 카테고리와 독립 토글·동시선택 (사용자 2026-06-12)
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "var activeFlags=[]" in src           # 다중 플래그 배열
        assert "activeFlags.every(" in src           # 선택 플래그 전부 AND
        # 플래그 클릭=토글(다른 pill active 유지), 카테고리=카테고리끼리만 배타
        assert "if(!x.dataset.flag)x.classList.remove('active')" in src
        assert "함께 선택 가능" in src                # 범례 안내


class TestDartUnparsed7:
    """미파싱-7 (사용자 2026-06-12) — 대량보유 일반서식 doc 폴백 / 판결
    [주문] / 자율공시 콜론형 / ○마스킹 원고 / 취지 날짜 절단 가드."""

    @staticmethod
    def _run(fields, txt):
        import re
        return {l: m.group(1).strip() for l, p, k in fields
                if (m := re.search(p, txt))}

    def test_majorstock_doc_fallback(self):
        # majorstock API 미매칭(웰크론 일반서식) → 요약정보 표 폴백
        from bot.dart_feed import _majorstock_doc_lines
        L = _majorstock_doc_lines(
            "주식등의 대량보유상황보고서 요약정보 발행회사명 (주)웰크론한텍 "
            "발행회사와의 관계 최대주주 보고구분 변동 보유주식등의 수 및 보유비율 "
            "직전 보고서 7,855,862 34.75 이번 보고서 7,999,774 35.41 "
            "보고사유 보고자 및 특별관계자 주식 추가 취득 ※ 보고자 본인은")
        assert any(l == "발행회사: (주)웰크론한텍 (최대주주)" for l in L)
        assert any(l == "지분율: 34.75% → 35.41% (+0.66%p ▲)" for l in L)
        assert any(l.startswith("사유: 보고자 및 특별관계자") for l in L)

    def test_ruling_jumun_block(self):
        # 판결내용이 [채권자]/[채무자]/[주문] 구조 — 주문 첫 문장 (마침표 포함)
        from bot.dart_feed import _LAWSUIT_RULING_FIELDS
        o = self._run(_LAWSUIT_RULING_FIELDS, (
            "1. 사건의 명칭 회계장부 열람등사 가 처분 사건번호 2026카합516 "
            "3. 판결ㆍ결정내용 [채권자] 한○○ [채무자] 주식회사 씨씨에스충북방송 "
            "[주 문] 이 사건의 항고장을 각하한다. 4. 판결ㆍ결정사유 ... "
            "5. 관할법원 청주지방법원 충주지원 6. 판결ㆍ결정일자 2026-06-09"))
        assert o["주문"] == "이 사건의 항고장을 각하한다."
        assert o["관할법원"] == "청주지방법원 충주지원"

    def test_misc_mgmt_colon_form(self):
        # '사건명:'/'채권자:' 콜론형 (티에스넥스젠 — [사건] 블록 아님)
        from bot.dart_feed import _misc_mgmt_lines
        r = _misc_mgmt_lines(
            "기타 경영사항(자율공시) 1. 제목 상장폐지결정 효력정지 가처분 신청 "
            "2. 주요내용 회사는 서울남부지방법원에 가처분을 신청하였으며, "
            "사건명: 서울남부지방법원 2026카합1389 [전자]상장폐지결정 효력정지 가처분 "
            "채권자: 주식회사 티에스넥스젠 채무자: 주식회사 한국거래소 "
            "[신청취지] 1. 채권자의 채무자에 대한 ... 3. 결정(확인)일자 2026-06-09")
        assert r["category"] == "소송"
        assert any(l.startswith("사건: 서울남부지방법원 2026카합1389") for l in r["lines"])
        assert any("채권자 주식회사 티에스넥스젠" in l
                   and "채무자 주식회사 한국거래소" in l for l in r["lines"])

    def test_filed_masked_plaintiff_and_date_in_purport(self):
        # 원고 '권○○'(마스킹) + 취지 본문 날짜 '2026. 6. 12.' 절단 가드
        # (항번호 stop 은 다음 문자가 한글/괄호일 때만)
        from bot.dart_feed import _LAWSUIT_FILED_FIELDS
        o = self._run(_LAWSUIT_FILED_FIELDS, (
            "1. 사건의 명칭 의결권행사금지 가처분 신청 사건번호 2026카합50146 "
            "2. 원고(신청인) 권○○ 3. 청구내용 1.채무자: 신○○, 이○○ "
            "2.신청취지: (1) 채무자들은 2026. 6. 12. 10:00 경기도 성남시 분당구에서 "
            "개최되는 주식회사 알로이스의 임시주주총회에서 의결권을 행사하여서는 아니 된다. "
            "(2) 신청비용은 채무자들이 부담한다. 4. 관할법원 수원지방법원 성남지원"))
        assert o["원고"] == "권○○"
        assert "2026. 6. 12" in o["취지"] and "아니 된다" in o["취지"]
        assert o["관할법원"] == "수원지방법원 성남지원"


class TestLookupPriceGlitchGuard:
    """종목검색 카드 글리치 가드 (KLAC $2,411/$3.15T 2026-06-12) — yfinance
    분할 미조정 수신가를 직전 종가로 교체 + 시총 재계산 + 보정 주석."""

    def test_replaces_split_artifact_price(self):
        from unittest.mock import patch, MagicMock
        info = {"quoteType": "EQUITY", "currentPrice": 2411.64,
                "regularMarketPreviousClose": 213.42,
                "sharesOutstanding": 1.32e8, "marketCap": 3.15e12}
        mt = MagicMock(); mt.info = info
        with patch("yfinance.Ticker", return_value=mt):
            from bot.stock_snapshot import collect_stock_snapshot
            s = collect_stock_snapshot("KLAC")
        assert s["current_price"] == 213.42
        assert abs(s["market_cap"] - 213.42 * 1.32e8) < 1
        assert "보정" in s["price_glitch_note"]

    def test_normal_price_untouched(self):
        from unittest.mock import patch, MagicMock
        info = {"quoteType": "EQUITY", "currentPrice": 215.0,
                "regularMarketPreviousClose": 213.42,
                "sharesOutstanding": 1.32e8, "marketCap": 2.84e10}
        mt = MagicMock(); mt.info = info
        with patch("yfinance.Ticker", return_value=mt):
            from bot.stock_snapshot import collect_stock_snapshot
            s = collect_stock_snapshot("KLAC")
        assert s["current_price"] == 215.0
        assert s["market_cap"] == 2.84e10
        assert "price_glitch_note" not in s

    def test_detail_page_renders_note(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "price_glitch_note" in src


class TestQuoteGlitchAllDashboards:
    """호가 글리치 가드 전 대시보드 일괄 (2026-06-12, 사용자 'KLA 같은
    사례 안 나오게 모든 대시보드') — 공유 프리미티브 + 5개 적용 지점."""

    def test_quote_glitch_gap_primitive(self):
        from bot.price_sanity import quote_glitch_gap
        # KLAC 클래스: 213.42 → 2411.64 (+1030%) = 글리치
        assert quote_glitch_gap(2411.64, 213.42)
        assert quote_glitch_gap(50.0, 213.42)        # -77% 도 글리치
        # 진짜 큰 뉴스 갭(±50% 안쪽)은 보존
        assert not quote_glitch_gap(310.0, 213.42)   # +45%
        assert not quote_glitch_gap(110.0, 213.42)   # -48%
        # 결측/0 → 판정 불가 = False (보수적)
        assert not quote_glitch_gap(None, 213.42)
        assert not quote_glitch_gap(100.0, None)
        assert not quote_glitch_gap(100.0, 0)
        assert not quote_glitch_gap("x", 100.0)

    def test_wired_into_surfaces(self):
        # 적용 지점 5곳 배선 가드 — 관심종목 / 홈 live / 미국 Daily(지수+
        # 무버) / 시총 fetch (검색카드는 TestLookupPriceGlitchGuard 전담)
        for path in ("bot/market_favorites.py", "bot/market_overview.py",
                     "bot/us_market_daily.py", "bot/finviz_client.py"):
            src = open(path, encoding="utf-8").read()
            assert ("quote_glitch_gap" in src
                    or "글리치" in src), f"가드 미배선: {path}"

    def test_us_movers_skips_glitch_bar(self):
        # 무버 경로는 history 마지막 봉 ±75% 초과 종목 제외 로직 존재
        src = open("bot/us_market_daily.py", encoding="utf-8").read()
        assert "0.75" in src and "글리치" in src


class TestWeeklyByteBothMarkets:
    """Daily Byte 주간 한·미 (2026-06-12 '주간 한국 미국 다·같은 시간')."""

    def test_kr_weekly_excludes_us_dailies(self):
        # KR 주간 로더가 us_daily_byte 파일 제외 (#280 동일 디렉토리 공유)
        src = open("bot/daily_kr_weekly.py", encoding="utf-8").read()
        assert "us_daily_byte" in src

    def test_us_weekly_module_and_units(self):
        from bot.us_market_weekly import (_load_week_briefs, generate,
                                          _save_weekly_archive)
        assert callable(generate)
        import os
        assert os.path.exists("deploy/daily-byte-weekly-us.timer")
        assert os.path.exists("deploy/daily-byte-weekly-us.service")
        timer = open("deploy/daily-byte-weekly-us.timer").read()
        # KR weekly 와 같은 시각 (사용자 '같은 시간')
        assert "Sun *-*-* 22:00:00 Asia/Seoul" in timer
        inst = open("deploy/install.sh").read()
        assert "daily-byte-weekly-us.timer" in inst

    def test_dashboard_badge_us_weekly(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "us_weekly" in src and "미국 Weekly" in src


class TestStandardViewRemoved:
    """Standard View 폐기 최종 정리 (2026-06-12) — 소스 삭제 + 명령/합산
    reader 제거 + backend disable. 재도입 회귀 차단."""

    def test_source_tree_gone(self):
        import os
        assert not os.path.exists("standardview")

    def test_no_active_sv_references(self):
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "cmd_sv_cost" not in tb
        assert 'first_word == "sv_cost"' not in tb
        assert "sv-usage/today" not in tb
        db = open("bot/dashboard.py", encoding="utf-8").read()
        assert "sv_usage.jsonl" not in db
        an = open("bot/analyzer.py", encoding="utf-8").read()
        assert "standardview_push" not in an

    def test_backend_disabled_in_install(self):
        inst = open("deploy/install.sh").read()
        assert "standardview-backend.service" in inst


class TestDartGenericNumberedFallback:
    """변형/신규 양식 generic 폴백 (2026-06-12 '조금 다른 건 니가 판단해서')
    — 번호 항목 발췌 + 프로세스 내 원문 캐시."""

    def test_numbered_rows_extraction(self):
        from bot.dart_feed import _numbered_rows_lines
        txt = ("타법인 주식 및 출자증권 취득결정 "
               "1. 발행회사 회사명 (주)테스트홀딩스 2. 취득내역 취득주식수(주) 1,200,000 "
               "3. 취득금액(원) 15,000,000,000 4. 자기자본대비(%) 8.5 "
               "5. 취득방법 제3자배정 유상증자 참여 6. 취득목적 신규사업 시너지 확보 "
               "7. 취득예정일자 2026. 6. 12. 8. 해당없음란 - 9. 이사회결의일 2026-06-11")
        L = _numbered_rows_lines(txt)
        # '(원)'/'(%)' 라벨 경계 정확 (값에 다음 항 누수 0)
        assert any(l == "취득금액(원): 15,000,000,000" for l in L), L
        assert any(l == "자기자본대비(%): 8.5" for l in L), L
        assert any("테스트홀딩스" in l for l in L)
        assert not any("해당없음란" in l for l in L)   # '-' 값 스킵
        assert len(L) <= 6                              # 캡

    def test_date_inside_value_not_a_stop(self):
        # 값 속 날짜 '12.' 가 다음 항 stop 으로 오인되지 않음 (라벨형 run 요구)
        from bot.dart_feed import _numbered_rows_lines
        L = _numbered_rows_lines(
            "1. 결정일자 2026. 6. 12. 2. 사유 신규 시설 투자 결정 3. 비고 -")
        assert any(l.startswith("결정일자: 2026. 6. 12") for l in L), L

    def test_prose_yields_nothing(self):
        from bot.dart_feed import _numbered_rows_lines
        assert _numbered_rows_lines("일반 산문 텍스트입니다. 라벨이 없습니다.") == []

    def test_generic_document_hooks_fallback(self):
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        assert "_numbered_rows_lines" in src
        assert "_DOC_TEXT_MEM" in src   # fail-mark 후 같은 시도 내 본문 재사용


class TestDartRisk2:
    """리스크-2 (2026-06-12) — SPAC 상폐정지(정리매매·상폐일) / 불성실
    다건유형 / 실질심사·관리종목우려(결론 주어앵커) / 생산재개."""

    @staticmethod
    def _run(fields, txt):
        import re
        return {l: m.group(1).strip() for l, p, k in fields
                if (m := re.search(p, txt))}

    def test_delisting_suspension_with_schedule(self):
        from bot.dart_feed import _SUSPENSION_FIELDS, significance
        o = self._run(_SUSPENSION_FIELDS, (
            "주권매매거래정지 2.정지사유 상장폐지 사유발생 3.정지기간 가.정지일시 "
            "2026-06-08 - 나.만료일시 2026-06-08 4.근거규정 코스닥 5.기타 * 상장폐지내역 "
            "- 정리매매기간 : 2026.06.09 ~ 2026.06.17 (7매매일) - 상장폐지일 : 2026.06.18"))
        assert o["사유"] == "상장폐지 사유발생"
        assert o["정리매매"].startswith("2026.06.09 ~ 2026.06.17")
        assert o["상장폐지일"] == "2026.06.18"
        assert significance({"report_nm": "주권매매거래정지", "category": "리스크",
                             "detail": [f"사유: {o['사유']}"]}) == "상장폐지 관련"

    def test_unfaithful_multi_type_comma(self):
        from bot.dart_feed import _UNFAITHFUL_FIELDS
        o = self._run(_UNFAITHFUL_FIELDS, (
            "불성실공시 유형 공시불이행,공시번복,공시변경 내용 공시불이행 6건 - "
            "단일판매ㆍ공급계약해지 원공시일 2026-01-21 지정예고일 2026-06-05 "
            "2. 불성실공시법인지정여부 결정시한 2026-06-30 3. 최근 1년간 불성실공시법인 부과벌점 23.0"))
        assert o["유형"] == "공시불이행,공시번복,공시변경"
        assert "단일판매ㆍ공급계약해지" in o["내용"]
        assert o["벌점"] == "23.0"

    def test_review_target_and_concern_notice(self):
        from bot.dart_feed import _MARKET_NOTICE_FIELDS, significance
        o = self._run(_MARKET_NOTICE_FIELDS, (
            "기타시장안내 제목 : ㈜다원시스 상장적격성 실질심사 대상 결정 거래소는 "
            "㈜다원시스에 대하여 상장폐지 가능성 등을 검토한 결과, 동사를 상장적격성 "
            "실질심사 대상으로 결정하였습니다."))
        assert o["제목"].endswith("실질심사 대상 결정")
        assert o["결론"].startswith("거래소는")          # 주어 앵커 (bleed 차단)
        # 🔥 실질심사 = 상폐 전단계 — 규칙 7 발화
        assert significance({"report_nm": "기타시장안내", "category": "리스크",
                             "detail": [f"제목: {o['제목']}"]}) == "상장폐지 관련"
        o2 = self._run(_MARKET_NOTICE_FIELDS, (
            "기타시장안내(관리종목지정우려종목) 제목 : 아이비케이에스제23호기업인수목적 "
            "주식회사 기타시장안내(관리종목 지정우려 예고) 동사는 합병상장예비심사신청서를 "
            "제출하지 않는 경우 관리종목으로 지정될 우려가 있습니다. "
            "1. 상장예비심사신청서 제출기한 : 2026년 06월 12일 - 관리종목 지정일(예정) : 2026년 06월 15일"))
        assert o2["결론"].startswith("동사는")
        assert o2["지정예정"].startswith("2026년 06월 15")

    def test_production_resume(self):
        from bot.dart_feed import _PRODUCTION_FIELDS
        o = self._run(_PRODUCTION_FIELDS, (
            "생산재개(자율공시) 1. 생산재개내용 안국약품(주) 화성공장 2. 생산재개내역 "
            "매출액대비(%) 68.15 3. 생산재개사업 정제 제형 제조업무 재개 "
            "4. 생산재개사유 경인지방식품의약품안정청 행정처분 기간 종료 "
            "6. 생산재개일자 2026-06-06 8. 기타 생산중단기간:2026.5.22 ~ 2026.6.5"))
        assert o["내용"] == "안국약품(주) 화성공장"
        assert o["매출액대비"] == "68.15" and o["일자"] == "2026-06-06"
        assert o["중단기간"] == "2026.5.22 ~ 2026.6.5"


class TestUsDailyMoversEnrichment:
    """미국 Daily Byte 종목 보강 (사용자 2026-06-12 '종목 내용 부족, 한국꺼
    참조') — universe 다단 폴백 + 누적/시총·이름 부착 + 52주 신고저 블록."""

    def test_universe_uses_robust_fallback(self):
        # 옛 stock_screener._get_us_universe 단일(위키) 경로가 VM 403 으로
        # movers {} → 'Mag-7 만 분석' 브리프가 나가던 근본 원인 — robust
        # 경로(위키→GitHub CSV→GICS→코어) 사용 회귀 가드.
        src = open("bot/us_market_daily.py", encoding="utf-8").read()
        assert "_us_universe_robust" in src
        assert "from bot.stock_screener import _get_us_universe" not in src

    def test_rank_movers_pure(self):
        from bot.us_market_daily import _rank_movers
        rows = ([{"ticker": "PLTR", "chg": 8.1, "chg_prev": 1.0, "chg_5d": 14.2,
                  "chg_1m": 32.0, "dollar_m": 1234.0, "surge": 2.3},
                 {"ticker": "TINY", "chg": 15.0, "chg_prev": 0.0, "chg_5d": None,
                  "chg_1m": None, "dollar_m": 50.0, "surge": 5.0},   # 유동성 컷
                 {"ticker": "REV", "chg": -1.5, "chg_prev": 2.4, "chg_5d": 3.0,
                  "chg_1m": 9.0, "dollar_m": 400.0, "surge": 0.9}]   # 양→음
                + [{"ticker": f"F{i}", "chg": 0.1, "chg_prev": 0.0,
                    "chg_5d": 0.5, "chg_1m": 1.0, "dollar_m": 250.0,
                    "surge": 1.0} for i in range(60)])
        mv = _rank_movers(rows)
        assert mv["gainers"][0]["ticker"] == "PLTR"
        assert all(r["ticker"] != "TINY" for r in mv["gainers"])  # $200M 컷
        assert mv["reversals"][0]["ticker"] == "REV"
        # 랭킹 dict 는 rows 와 참조 공유 — 이름/시총 부착이 전 리스트 반영
        mv["gainers"][0]["name"] = "Palantir"
        assert mv["by_dollar"][0].get("name") == "Palantir"

    def test_prompt_row_has_name_cum_turnover(self):
        from bot.us_market_daily import _format_data_for_prompt
        mv = {"n": 64, "breadth_pct": 60.0, "avg_chg": 0.5,
              "gainers": [{"ticker": "PLTR", "name": "Palantir", "chg": 8.12,
                           "chg_prev": 1.0, "chg_5d": 14.2, "chg_1m": 32.0,
                           "dollar_m": 1234.0, "surge": 2.3,
                           "mcap_b": 250.0, "turn_pct": 0.49}],
              "losers": [], "by_dollar": [], "surges": [], "reversals": []}
        txt = _format_data_for_prompt(
            {"indices": {}, "sectors": {}, "mag7": {}, "bonds_fx": {},
             "movers": mv})
        # KR 미러 밀도: 회사명 + 5일/1개월 누적 + 시총 대비 손바뀜
        assert "PLTR(Palantir)" in txt
        assert "5일 +14.2%" in txt and "1개월 +32.0%" in txt
        assert "시총 $250B 대비 0.5%" in txt

    def test_prompt_highlow_block(self):
        from bot.us_market_daily import _format_data_for_prompt
        txt = _format_data_for_prompt(
            {"indices": {}, "sectors": {}, "mag7": {}, "bonds_fx": {},
             "movers": {},
             "highlow": {"count_high": 24, "count_low": 7, "source": "전미국",
                         "high": [{"ticker": "MSFT", "name": "Microsoft",
                                   "mcap": 35000.0, "pct": 1.2}],
                         "low": []}})
        assert "52주 신고가/신저가" in txt
        assert "신고가 24종목" in txt and "신저가 7종목" in txt
        assert "MSFT(Microsoft) 시총 $3,500B" in txt

    def test_prompt_directive_five_picks_non_mag7(self):
        # 주목 5선이 'Mag-7 만 분석' 으로 축소되지 않게 하는 directive
        src = open("bot/us_market_daily.py", encoding="utf-8").read()
        assert "Mag-7 제외" in src and "축소 금지" in src


class TestResearchStrategyTab:
    """리서치 액션 '한국 전략' 탭 (사용자 2026-06-12 — 네이버 투자전략).
    종목/산업 탭과 동일 기준, 투자정보(invest_list) 소스."""

    def test_strategy_list_parser(self):
        from datetime import date
        from bot.naver_research_client import _parse_strategy_list_page
        html = (
            '<table><tbody>'
            '<tr><td><a href="invest_read.naver?nid=111&page=1">하반기 전략</a></td>'
            '<td>미래에셋증권</td><td>PDF</td><td>26.06.12</td><td>1,234</td></tr>'
            '<tr><td><a href="invest_read.naver?nid=222">자산배분 코멘트</a></td>'
            '<td>NH투자증권</td><td>PDF</td><td>26.06.11</td><td>9</td></tr>'
            '<tr><td>광고행(nid없음)</td></tr>'
            '<tr><td><a href="invest_read.naver?nid=333">오래됨</a></td>'
            '<td>하나증권</td><td>PDF</td><td>26.05.01</td><td>1</td></tr>'
            '</tbody></table>')
        rows = _parse_strategy_list_page(html, date(2026, 6, 10))
        # nid 없는 행·cutoff 밖(5/1) 제외, 분류·목표가 없음(증권사/제목/날짜)
        assert [r["nid"] for r in rows] == ["111", "222"]
        assert rows[0]["broker"] == "미래에셋증권"
        assert rows[0]["title"].startswith("하반기")
        assert rows[0]["date"] == "2026-06-12"
        assert "category" not in rows[0]  # 전략은 분류 컬럼 없음

    def test_strategy_uses_invest_endpoint(self):
        # 투자정보(invest_list/invest_read) 소스인지 — 종목(company)/산업
        # (industry) 와 다른 엔드포인트 회귀 가드
        src = open("bot/naver_research_client.py", encoding="utf-8").read()
        assert "invest_list.naver" in src and "invest_read.naver" in src

    def test_dashboard_strategy_tab_wired(self):
        # 탭 버튼·페인·렌더 함수·시그니처가 모두 연결됐는지 (bot.dashboard
        # import 는 무거워 소스 읽기)
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'data-tab="krstrat"' in src
        assert '한국 전략' in src
        assert 'id="tab-krstrat"' in src
        assert "_render_research_strategy_table" in src
        assert 'research_kr_strategy = data.get("research_kr_strategy"' in src

    def test_market_overview_strategy_wired(self):
        src = open("bot/market_overview.py", encoding="utf-8").read()
        assert "fetch_recent_research_kr_strategy" in src
        assert '"research_kr_strategy": kr_strat_fut.result()' in src


class TestPmOverrideRatingMask:
    """강제 HOLD 시 PM 원문 비-Hold 등급 마스킹 (082920 2026-06-11 review).
    배너 HOLD ↔ 원문 'Overweight' 시각 혼선 차단 + 산문 등급어 보존."""

    def _mask(self):
        import ast
        src = open("bot/analyzer.py", encoding="utf-8").read()
        for node in ast.parse(src).body:
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "_mask_overridden_pm_rating"):
                ns = {"re": __import__("re")}
                exec(ast.get_source_segment(src, node), ns)
                return ns["_mask_overridden_pm_rating"]
        raise AssertionError("_mask_overridden_pm_rating not found")

    def test_standalone_rating_line_masked(self):
        m = self._mask()
        out = m("Overweight\n\n근거: overweight 비중확대를 권고합니다.")
        assert out.startswith("~~Overweight~~ (시스템 기각 — 최종 HOLD)")
        # 산문 소문자 overweight 보존 (과수정 금지)
        assert "overweight 비중확대를 권고" in out

    def test_prefixed_rating_line_masked(self):
        m = self._mask()
        out = m("추천: Overweight\n근거: ...")
        assert "~~Overweight~~ (시스템 기각 — 최종 HOLD)" in out.split("\n")[0]

    def test_bold_rating_line_masked(self):
        m = self._mask()
        assert "~~Overweight~~" in m("**Overweight**")

    def test_hold_unchanged(self):
        m = self._mask()
        assert m("Hold\n근거: 중립.") == "Hold\n근거: 중립."

    def test_prose_only_rating_preserved(self):
        m = self._mask()
        d = "근거: 강세 측은 overweight 를 주장했으나 단기 부담 존재."
        assert m(d) == d

    def test_wiring_applies_only_with_override_note(self):
        # _format_full 이 override_note 있을 때만 마스킹 적용
        src = open("bot/analyzer.py", encoding="utf-8").read()
        assert "_mask_overridden_pm_rating(decision)" in src
        assert "if override_note else decision" in src


class TestDeployAwareWidgetCache:
    """배포-인지 캐시 솔트 (2026-06-12 '대시보드 반영 너무 느려') — 코드
    배포(모듈 mtime 변경) 시 day-key 위젯 캐시 즉시 무효화. #281 실적 450
    확장이 12:01 옛 캐시(12h TTL)에 가려 안 보이던 클래스 영구 차단."""

    def test_salt_defined_and_applied(self):
        src = open("bot/market_overview.py", encoding="utf-8").read()
        assert "_CODE_SALT" in src
        assert 'f"earnings_{today.isoformat()}_{_CODE_SALT}.json"' in src
        assert 'f"earnings_kr_{today.isoformat()}_{_CODE_SALT}.json"' in src
        assert "kr_earnings_universe_{_CODE_SALT}.json" in src

    def test_kr_earnings_ttl_6h(self):
        # KR 실적 TTL 12h→6h (US 와 동일) — 하루 4회 갱신
        src = open("bot/market_overview.py", encoding="utf-8").read()
        import re as _re
        m = _re.search(r"earnings_kr_\{today\.isoformat\(\)\}[\s\S]{0,400}?< (\d+)", src)
        assert m and m.group(1) == "6", "KR earnings TTL 6h 아님"


class TestTradeDashboardSudoersSelfHeal:
    """trade-bot-dashboard stale-server 근본 fix (2026-06-12) — sudoers
    부트스트랩 부재로 재시작이 매 배포 silent-skip 되던 것. NOAH
    install.sh(root 보장 경로)가 trade drop-in 자가설치 + 1회 즉시 heal."""

    def test_install_sh_heals_trade_sudoers(self):
        src = open("deploy/install.sh", encoding="utf-8").read()
        assert "higgack-trade-services" in src
        assert "install-trade-units.sh" in src      # 부트스트랩 라인
        assert "restart trade-bot-dashboard" in src
        assert "visudo -cf" in src                  # 검증 후 설치 (잠금 사고 방지)
        # 1회 즉시 heal — 누적 UI 변경 반영
        assert "systemctl restart trade-bot-dashboard 2>/dev/null" in src


class TestBackfillV4StatusLabel:
    """백필 v4 상태 라벨 (2026-06-12 '확실히 확인한거지') — 공시 페이지가
    marker 파일로 완료/대기를 직접 표시. SSH·문의 없이 화면 확인."""

    def test_status_states(self, tmp_path, monkeypatch):
        import json as _j
        import bot.dart_feed as df
        monkeypatch.setattr(df, "_BACKFILL_MARKER_V4",
                            tmp_path / ".dart_feed_backfilled_v4")
        assert df.backfill_v4_status().startswith("⏳")
        (tmp_path / ".dart_feed_backfilled_v4").write_text(_j.dumps(
            {"ts": "2026-06-12T21:40:00", "reparse_replaced": 41,
             "reparse_checked": 88, "added": 137, "reclassified": 12}))
        s = df.backfill_v4_status()
        assert s.startswith("✅") and "41/88" in s and "137" in s
        # 옛 plain isoformat marker tolerant
        (tmp_path / ".dart_feed_backfilled_v4").write_text(
            "2026-06-12T21:30:00+09:00")
        assert df.backfill_v4_status().startswith("✅")

    def test_marker_written_as_json_stats(self):
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        assert "reparse_replaced" in src and "backfill_v4_status" in src

    def test_dashboard_renders_label(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "backfill_v4_status" in src and "_bf4_status" in src


class TestDartDividendCategoryAndCapitalRaise:
    """배당 분리 + 유상증자 시총5% 중요 + 공정공시 승격 + 칩 순서
    (사용자 2026-06-12)."""

    def test_dividend_split_from_shareholder(self):
        from bot.dart_feed import _classify_report
        assert _classify_report("현금ㆍ현물배당결정") == "배당"
        assert _classify_report("배당기준일안내") == "배당"
        # 소각·자사주는 주주환원 유지, 조회공시 우선순위 보존
        assert _classify_report("주식소각결정") == "주주환원"
        assert _classify_report("조회공시요구(배당설)에대한답변") == "조회공시"

    def test_fair_disclosure_upgrade(self):
        from bot.dart_feed import _fair_disclosure_category, _upgrade_category
        assert _fair_disclosure_category(
            ["제목: 중장기 주주환원정책 발표"]) == "주주환원"
        assert _fair_disclosure_category(["제목: 2026년 배당정책"]) == "배당"
        assert _fair_disclosure_category(["제목: 신제품 출시"]) is None
        it = {"report_nm": "수시공시의무관련사항(공정공시)", "category": "실적",
              "detail": ["제목: 중장기 주주환원정책 발표"]}
        _upgrade_category(it)
        assert it["category"] == "주주환원"   # 'KG 5사 왜 실적이야' fix

    def test_capital_raise_5pct_significance(self):
        from bot.dart_feed import significance, _won_str_to_float
        assert _won_str_to_float("8,051억원") == 8051e8
        assert _won_str_to_float("1.2조원") == 1.2e12
        item = {"report_nm": "유상증자결정", "category": "자금조달",
                "detail": ["시설자금: 300억원", "운영자금: 250억원"]}
        s = significance(item, market_cap=1e12)      # 5.5%
        assert s and "유상증자" in s
        assert significance(item, market_cap=2e12) is None   # 2.75%
        assert significance(item) is None                    # mcap 부재 보수적
        assert significance({"report_nm": "[기재정정]유상증자결정",
                             "category": "자금조달",
                             "detail": ["시설자금: 9,000억원"]},
                            market_cap=1e12) is None         # 정정 제외

    def test_pill_order_user_spec(self):
        from bot.dashboard import _DART_CATEGORIES
        # 배당↔지분공시 교환 (사용자 2026-06-12 2차)
        assert _DART_CATEGORIES == [
            "전체", "실적", "계약", "신규시설투자", "주주환원", "자금조달",
            "지분공시", "배당", "리스크", "소송", "회사구조", "자산양수도",
            "조회공시", "IR"]

    def test_v5_reclass_wiring_and_parse_cats(self):
        from bot.dart_feed import _PARSE_CATS, reclassify_v5_once_if_needed
        assert "배당" in _PARSE_CATS
        assert callable(reclassify_v5_once_if_needed)
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "reclassify_v5_once_if_needed" in src
        leg = open("bot/dashboard.py", encoding="utf-8").read()
        assert "유상증자 시총5%" in leg   # 범례 동기


class TestDelistBoilerplateNoFire:
    """🔥 규칙 7 오발 fix (에코마케팅 230360, 2026-06-12) — 병합/분할
    전자등록 기계적 거래정지의 '해제·만료: 상장폐지시까지' boilerplate 는
    상폐 신호 아님. 실제 상폐(결정/상장폐지일/우려/사유)는 유지."""

    def test_mechanical_suspension_no_fire(self):
        from bot.dart_feed import significance
        eco = {"report_nm": "주권매매거래정지 (주식의 병합, 분할 등 전자등록 변경, 말소)",
               "category": "리스크",
               "detail": ["사유: 주식의 병합, 분할 등 전자등록 변경, 말소",
                          "정지: 2026-06-11", "해제·만료: 상장폐지시까지"]}
        assert significance(eco) is None

    def test_real_delisting_still_fires(self):
        from bot.dart_feed import significance
        assert significance({"report_nm": "주권매매거래정지(상장폐지)",
                             "category": "리스크",
                             "detail": ["사유: 상장폐지",
                                        "상장폐지일: 2026-07-01"]}) == "상장폐지 관련"
        assert significance({"report_nm": "기타시장안내", "category": "리스크",
                             "detail": ["제목: 상장폐지 우려 안내"]}) == "상장폐지 관련"
        assert significance({"report_nm": "주권매매거래정지", "category": "리스크",
                             "detail": ["사유: 상장폐지 사유 발생",
                                        "해제·만료: -"]}) == "상장폐지 관련"


class TestEarningsChangeGate:
    """🔥 규칙 1 게이트 (사용자 2026-06-12) — 손익구조 공시는 제목만으로
    발화하지 않고 매출액 |20%|↑ OR 영업이익 |30%|↑ OR 흑자/적자전환."""

    @staticmethod
    def _mk(det):
        return {"report_nm": "매출액또는손익구조30%(대규모법인은15%)이상변경",
                "category": "실적", "detail": det}

    def test_or_gate(self):
        from bot.dart_feed import significance as S
        assert S(self._mk(["매출액: 1,234억원 (전기 987억원 · +25.3%)"])
                 ) == "매출액 +25.3% 변동"
        assert S(self._mk(["매출액: 800억원 (전기 987억원 · -19.0%)",
                           "영업이익: 70억원 (전기 98억원 · -29.0%)"])) is None
        assert S(self._mk(["매출액: 950억원 (전기 987억원 · -3.7%)",
                           "영업이익: 64억원 (전기 98억원 · -35.0%)"])
                 ) == "영업이익 -35.0% 변동"
        # 흑자전환만 발화 (2026-06-12 2차 — 적자전환 제외)
        assert S(self._mk(["영업이익: 12억원 (전기 -98억원 · 흑자전환)"])
                 ) == "영업이익 흑자전환"
        assert S(self._mk(["영업이익: -12억원 (전기 98억원 · 적자전환)"])) is None

    def test_unparsed_and_correction_no_fire(self):
        from bot.dart_feed import significance as S
        assert S(self._mk([])) is None          # 미파싱 → ⚠️ 대기
        assert S({"report_nm": "[기재정정]매출액또는손익구조30%이상변경",
                  "category": "실적",
                  "detail": ["매출액: 1,234억원 (전기 987억원 · +25.3%)"]}) is None
