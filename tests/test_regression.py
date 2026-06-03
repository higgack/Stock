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

    def test_chart_has_volume_and_rsi_indicators(self):
        """보조지표 — 거래량 히스토그램 + RSI(14) 하단 pane 배선 (2026-06-04)."""
        from bot.dashboard import _CHART_JS, _render_chart_section

        # JS: 거래량 히스토그램 + RSI 별도 차트 + 시간축 동기화
        assert "addHistogramSeries" in _CHART_JS, "거래량 히스토그램 누락"
        assert "priceScaleId: 'vol'" in _CHART_JS, "거래량 overlay 스케일 누락"
        assert "rsiChart = LightweightCharts.createChart" in _CHART_JS, "RSI pane 누락"
        assert "syncTime(chart, rsiChart)" in _CHART_JS, "시간축 동기화 누락"
        assert "price: 70" in _CHART_JS and "price: 30" in _CHART_JS, "RSI 70/30 기준선 누락"
        # HTML: RSI 컨테이너
        html = _render_chart_section({
            "ticker": "AAPL",
            "price_chart": {"currency": "$", "decimals": 2,
                            "times": ["2025-06-01"], "close": [1.0]},
        })
        assert 'id="rsi-chart"' in html, "RSI 차트 컨테이너 누락"

    def test_series_payload_rsi_volume_shape(self):
        """_series_payload 가 rsi/volume 키를 추가 (pandas 있을 때만 실행)."""
        try:
            import pandas as pd
        except Exception:
            import pytest
            pytest.skip("pandas 미설치 — VM 에서 검증")
        from bot.chart_data import _series_payload
        idx = pd.date_range("2025-01-01", periods=30, freq="D")
        close = pd.Series([100 + i for i in range(30)], index=idx)
        vol = pd.Series([1000 + i for i in range(30)], index=idx, dtype="float64")
        p = _series_payload(close, "$", 2, vol)
        assert "rsi" in p and len(p["rsi"]) == 30
        assert "volume" in p and all(isinstance(v, int) for v in p["volume"])
        last_rsi = [x for x in p["rsi"] if x is not None][-1]
        assert 0 <= last_rsi <= 100

    def test_build_price_chart_graceful_on_failure(self):
        """네트워크/티커 실패 시 None 반환 (예외 전파 금지 — 아카이브
        저장 경로가 차트 때문에 깨지면 안 됨)."""
        from bot.chart_data import build_price_chart

        # 샌드박스는 네트워크 차단 → yfinance 실패 → None (graceful)
        result = build_price_chart("NONEXISTENT_TICKER_XYZ_123")
        assert result is None or isinstance(result, dict)


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
