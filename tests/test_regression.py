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
class TestSVMobileResponsive:
    """fix: SV 대시보드 모바일 그리드 미접힘 (2026-06-04)."""

    def test_mobile_css_and_injection_wired(self):
        src = open("standardview/scripts/daily_generator.py",
                   encoding="utf-8").read()
        # 반응형 @media (데스크탑 >768px 무영향) + 인라인 그리드 단일칼럼 override
        assert "@media (max-width:1100px){" in src, "모바일 @media(1100px) 누락"
        assert "*{grid-template-columns:1fr !important" in src, "전 그리드 단일칼럼 override 누락"
        assert "def _inject_mobile_responsive" in src, "주입 함수 누락"
        assert "noah-mobile" in src, "style id 누락"
        assert "viewport" in src, "viewport 보강 누락"
        # 생성 시 호출 (timestamped + latest.html 둘 다 적용되게 str(soup) 전에)
        assert "_inject_mobile_responsive(soup)" in src, "생성 시 호출 미배선"

    def test_macro_news_multicolumn_and_readability(self):
        # 데스크탑+모바일 Macro News 가독성 (2026-06-04 사용자 스크린샷):
        # 전체폭 카드를 채우는 반응형 다단 그리드 + 헤드라인 줄바꿈/폰트↑,
        # grid-column:auto 로 산업/Deal 카드도 모바일 풀폭.
        src = open("standardview/scripts/daily_generator.py",
                   encoding="utf-8").read()
        assert "grid-column:auto !important" in src, "스팬 리셋(산업/Deal 풀폭) 누락"
        assert "news-grid" in src, "Macro News 다단 그리드 래퍼 누락"
        assert "minmax(520px,1fr)" in src, "반응형 다단 minmax 누락"
        assert "white-space:normal;font-size:15px" in src, "헤드라인 줄바꿈+폰트 보강 누락"
        assert ".news-item{flex-direction:column" in src, "모바일 태그-헤드라인 스택 누락"
        assert ".news-section-header, .news-grid" in src, "strip 2-pass(.news-grid) 누락"


# ─────────────────────────────────────────────────────────────────────────
# 8a7) SV 대시보드 '오늘 NOAH 분석' per-ticker 섹션 완전 삭제 (2026-06-04
#   사용자 요청 "이 부분은 아예 삭제"). daily_generator 가 더 이상 latest.html
#   에 해당 섹션을 주입하면 안 됨. 샌드박스에서 bs4/생성기 실행 불가하므로
#   주입 코드/헬퍼/로그가 소스에서 사라졌는지 grep 으로 영구 차단.
# ─────────────────────────────────────────────────────────────────────────
class TestSVNoahSectionRemoved:
    """fix: SV 대시보드 '오늘 NOAH 분석' 섹션 완전 삭제 (2026-06-04)."""

    def _src(self):
        return open("standardview/scripts/daily_generator.py",
                    encoding="utf-8").read()

    def test_no_noah_section_injection(self):
        src = self._src()
        assert "오늘 NOAH" not in src, "NOAH 섹션 헤더 주입 잔존"
        assert "NOAH analyses section inserted" not in src, "NOAH 섹션 삽입 로그 잔존"

    def test_no_load_noah_today_helper(self):
        # 유일한 호출처가 사라졌으므로 헬퍼도 제거 (orphan dead-code 차단).
        src = self._src()
        assert "_load_noah_today" not in src, "미사용 _load_noah_today 헬퍼 잔존"

    def test_industry_section_preserved(self):
        # 인접 섹션(산업 트렌드)은 보존 — 삭제가 과하지 않았는지 가드.
        src = self._src()
        assert "Industry trends + Deal Highlights" in src, "산업 트렌드 섹션 누락(과삭제)"
        assert "산업 트렌드 section AFTER takeaway-card." in src, "anchor 단순화 누락"


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
        # v4·v5: 풀 nav(메인 NOAH 맨앞) · nav 단어 줄바꿈 방지 · 자기 '자산' 제거(제목 중복)
        assert 'href="index.html">🦉 NOAH 종목분석' in html, "nav 메인 첫 링크 누락"
        assert "screener.html" in html and "daily_byte.html" in html, "풀 nav 누락"
        assert ".nav a,.nav b{white-space:nowrap}" in html, "nav 줄바꿈 방지 CSS 누락"
        assert "<b>💼 자산</b>" not in html, "nav 자기 '자산' 제거 안 됨"
        # 세로 맞춤(2026-06-04): 손익 분포 + 등높이(증권사별↔TOP/WORST)
        assert "수익률 분포" in html, "수익률 분포 누락"
        assert 'pf-grid pf-eqh' in html, "등높이 그리드 클래스 누락"
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
        # 같은 업로드 날짜면 증분 스킵 (사용자 정책 2026-06-04)
        import time as _t
        now = _t.time()
        m2 = self._model()
        m2["_saved_ts"] = now
        m2["prev"] = {"순자산": 1, "주식평가": 1, "_saved_ts": now - 60}
        assert "지난 업데이트" not in _render_portfolio_page(m2), "같은 날짜 증분 스킵 안 됨"

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
        assert 'CommandHandler("portfolio", cmd_portfolio)' in src
        assert 'BotCommand("portfolio"' in src, "set_my_commands 미등록"
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
        # nav: 자산 first, NOAH second (가계부 자신은 현재라 생략)
        assert 'href="portfolio.html">💼 자산' in html
        assert 'href="index.html">🦉 NOAH' in html
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
        assert "budget.html" in open("bot/telegram_bot.py", encoding="utf-8").read()
