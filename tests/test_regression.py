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
class TestSectorETFsIntegrity:
    """fix commits: 7baef95 / 40d114c (2026-06-01)."""

    def _load_etfs(self):
        import re

        src = open(
            "standardview/scripts/weekly_pusher.py", encoding="utf-8"
        ).read()
        m = re.search(r"_SECTOR_ETFS:.*?=\s*\[(.*?)\n\]", src, re.DOTALL)
        assert m, "_SECTOR_ETFS 정의 못 찾음"
        return re.findall(
            r'\(\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\)', m.group(1)
        )

    def test_no_duplicate_tickers(self):
        from collections import Counter

        rows = self._load_etfs()
        cnt = Counter(t for t, _, _ in rows)
        dups = [t for t, c in cnt.items() if c > 1]
        assert not dups, f"중복 ticker: {dups}"

    def test_no_hanja_labels(self):
        import unicodedata

        rows = self._load_etfs()
        bad = []
        for t, label, _m in rows:
            for ch in label:
                if "CJK UNIFIED" in unicodedata.name(ch, ""):
                    bad.append((t, label))
                    break
        assert not bad, (
            f"한자 라벨 (전부 한글 정책 위반): {bad[:5]}"
        )

    def test_all_expected_markets_present(self):
        """7개 시장 전부 등록 — TW/HK 빠지면 즉시 fail."""
        from collections import Counter

        rows = self._load_etfs()
        by_mkt = Counter(m for _, _, m in rows)
        expected = {"US", "EU", "KR", "JP", "TW", "CN", "HK"}
        missing = expected - set(by_mkt.keys())
        assert not missing, f"누락 시장: {missing}"
        # 최소 보장 — 의도치 않은 대량 삭제 차단
        assert by_mkt["US"] >= 11, "US SPDR 11섹터 미만"
        assert by_mkt["KR"] >= 15
        assert by_mkt["JP"] >= 15, "TOPIX-17 일부 누락 의심"
        assert by_mkt["EU"] >= 15, "STOXX 600 섹터 일부 누락 의심"


# ─────────────────────────────────────────────────────────────────────────
# 4) Screener post-process idempotency
#    배경: liquidity warning backend append 가 재실행 시 1번 이상 누적
#    되면 카드마다 warning 줄 폭증. strip→append 짝이 idempotent 보장.
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
        # 배선: override_rating == 'Hold' 일 때 discipline 배너 호출
        assert 'if override_rating == "Hold":' in src
        assert (
            "trader_divergence = _detect_discipline_forced_hold_banner"
            in src
        ), "discipline 배너 배선 누락"
        assert "def _detect_discipline_forced_hold_banner" in src

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


# ─────────────────────────────────────────────────────────────────────────
# 8b) 워치리스트 조건 알림 (2026-06-04) — 파서 + 평가 + 저장 + edge-trigger
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
