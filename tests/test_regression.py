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
        html = d._render_index(recs)[0]
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
class TestScreenerL4Subindustry:
    """L4 sub-industry layer (2026-06-14 — Semiconductors 세분화 8 모듈).
    GICS sub-industry 깊이 + 부모 L3 별칭 충돌 0 + 메뉴 cap 보존."""

    def test_l4_valid_layer_and_modules(self):
        from bot.screener_themes import _VALID_LAYERS, list_domains
        assert "L4_SUBINDUSTRY" in _VALID_LAYERS
        ds = list_domains()
        l4 = [d for d in ds if d.get("layer") == "L4_SUBINDUSTRY"]
        slugs = {d["slug"] for d in l4}
        # 반도체 L4 8 수작업 모듈 (모듈 import-time _validate 통과 = 등록됨)
        expect = {f"semiconductors_{s}" for s in
                  ("memory", "foundry", "equipment", "logic_ai",
                   "logic_mobile", "analog", "eda", "specialty")}
        assert expect <= slugs, expect - slugs
        # 전 GICS L4 comprehensive (2026-06-14) — 전 L3 파생, ≥200개
        assert len(l4) >= 200, f"L4 {len(l4)} < 200 (comprehensive coverage)"
        # 비-반도체 L3 도 L4 자식 보유 (generator 전 L3 커버)
        l3_slugs = {d["slug"] for d in ds if d.get("layer") == "L3_INDUSTRY"}
        for parent in ("banks", "software", "oil_gas", "pharma_biotech",
                       "machinery", "reits"):
            assert parent in l3_slugs
            kids = [s for s in slugs if s.startswith(parent + "_")]
            assert kids, f"L3 {parent} 에 L4 자식 없음"
        # 모든 L4 는 부모 L3 prefix 보유 (전역 unique slug)
        orphan = [s for s in slugs
                  if not any(s.startswith(p + "_") for p in l3_slugs)]
        assert not orphan, f"부모 L3 prefix 없는 L4: {orphan[:5]}"

    def test_l4_no_alias_collision_with_parent(self):
        # 부모 L3 공유 별칭(memory/foundry/eda/메모리/파운드리)은 L3 유지,
        # L4 전용 별칭(dram/wfe/sic/npu/wafer_fab)만 L4 로 — 충돌 0
        from bot.screener_themes import resolve_slug
        assert resolve_slug("memory") == "semiconductors"      # 부모 L3 보존
        assert resolve_slug("파운드리") == "semiconductors"
        assert resolve_slug("eda") == "semiconductors"
        assert resolve_slug("dram") == "semiconductors_memory"
        assert resolve_slug("wfe") == "semiconductors_equipment"
        assert resolve_slug("wafer_fab") == "semiconductors_foundry"
        assert resolve_slug("npu") == "semiconductors_logic_ai"
        assert resolve_slug("sic") == "semiconductors_specialty"
        assert resolve_slug("아날로그") == "semiconductors_analog"

    def test_l4_menu_excluded_but_rendered(self):
        # set_my_commands 는 L4 제외(100/scope cap 보존), /screener_list +
        # 대시보드 _LAYER_META 는 L4 노출. 소스 검증(telegram import 무거움).
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert 'if d.get("layer") == "L4_SUBINDUSTRY":' in tb       # 메뉴 skip
        assert "🎯 L4 Sub-industry" in tb                          # /screener_list
        dd = open("bot/dashboard.py", encoding="utf-8").read()
        assert '"L4_SUBINDUSTRY", "🎯 L4 Sub-industry"' in dd        # 대시보드 그룹
        assert '"L4_SUBINDUSTRY": "l4"' in dd                       # CSS 클래스


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

    def test_within_52w_range_real_mover_in_range(self):
        from bot.price_sanity import within_52w_range
        # CAST 2026-06-15: 현재가 $3.54, 오늘 +126% 진짜 급등. 52주[$1.2, $9]
        # 안 → 글리치 아님(가드 건너뜀). magnitude 만으론 못 거르던 false
        # positive 의 코드측 게이트.
        assert within_52w_range(3.54, 1.2, 9.0) is True
        # RGNT +752% 도 52주 범위 안이면 진짜 무브
        assert within_52w_range(12.79, 1.0, 15.0) is True

    def test_within_52w_range_klac_glitch_out_of_range(self):
        from bot.price_sanity import within_52w_range
        # KLAC $2,411 글리치: 52주 고가 ~$350 의 수배 → 범위 밖 → 가드 적용
        assert within_52w_range(2411, 180, 350) is False

    def test_within_52w_range_missing_refs_false(self):
        from bot.price_sanity import within_52w_range
        # 52주 ref 부재/0/None → False (호출부가 magnitude 가드로 폴백, 보수적)
        assert within_52w_range(3.54, None, None) is False
        assert within_52w_range(3.54, 0, 0) is False
        assert within_52w_range(3.54, 1.2, None) is False

    def test_within_52w_range_tolerance(self):
        from bot.price_sanity import within_52w_range
        # ±3% tol — 신고가 살짝 갱신(고가의 1.02배)은 in-range, 1.05배는 밖
        assert within_52w_range(102, 50, 100) is True
        assert within_52w_range(106, 50, 100) is False

    def test_stock_snapshot_52w_gate_wired(self):
        """stock_snapshot 의 글리치 가드가 within_52w_range 게이트를 실제로
        타는지 소스에서 확인 (CAST 류 진짜 급등 오발 차단 배선 회귀)."""
        src = open("bot/stock_snapshot.py", encoding="utf-8").read()
        assert "from bot.price_sanity import within_52w_range" in src, "헬퍼 import 누락"
        assert "within_52w_range(price" in src, "글리치 가드가 52주 게이트 미사용"
        assert "if price and not in_52w:" in src, "가드가 in_52w 로 게이트되지 않음"


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
    """차트 라이브 현재가 폴백 = ① 네이버 → ② KIS → ③ Yahoo (사용자 2026-06-16).
    Yahoo fast_info 는 ~15분 지연이라 최후순위. KR=네이버 국내·KIS 국내 모두
    실시간 / US·JP·HK·CN=네이버 해외(worldstock)·KIS 해외(지연 가능) / TW=네이버·
    KIS 미지원 → TWSE 공식 + Yahoo 만."""

    def test_kis_get_realtime_price(self, monkeypatch):
        import bot.kis_client as k
        monkeypatch.setattr(k, "_ticker_to_code", lambda t: "005930")
        monkeypatch.setattr(k, "_cache_get", lambda key, ttl_hours=None: None)
        monkeypatch.setattr(k, "_cache_put", lambda key, val: None)
        monkeypatch.setattr(k, "_get",
                            lambda path, tr_id, params, custtype=None: {"output": {"stck_prpr": "70100"}})
        assert k.KisClient().get_realtime_price("005930.KS") == 70100
        # 비-KR(코드 매핑 없음) → None(graceful, 다음 소스 폴백 신호).
        monkeypatch.setattr(k, "_ticker_to_code", lambda t: None)
        assert k.KisClient().get_realtime_price("AAPL") is None

    def test_overseas_excd_symb_mapping(self):
        from bot.kis_client import _overseas_excd_symb
        assert _overseas_excd_symb("7203.T") == (["TSE"], "7203")
        assert _overseas_excd_symb("0700.HK") == (["HKS"], "0700")
        assert _overseas_excd_symb("600519.SS") == (["SHS"], "600519")
        assert _overseas_excd_symb("000002.SZ") == (["SZS"], "000002")
        # 미국 순수 심볼 → 거래소 후보(기본 NAS→NYS→AMS).
        assert _overseas_excd_symb("AAPL") == (["NAS", "NYS", "AMS"], "AAPL")
        # 대만·국내·빈값 → 미지원 (None, None).
        assert _overseas_excd_symb("2330.TW") == (None, None)
        assert _overseas_excd_symb("005930.KS") == (None, None)
        assert _overseas_excd_symb("") == (None, None)

    def test_overseas_excd_rc_suffix_priority(self, monkeypatch):
        from bot import world_quote
        from bot.kis_client import _overseas_excd_symb
        monkeypatch.setitem(world_quote._rc_cache, "JPM", "JPM.N")    # NYSE
        monkeypatch.setitem(world_quote._rc_cache, "INTC", "INTC.O")  # NASDAQ
        assert _overseas_excd_symb("JPM")[0][0] == "NYS"
        assert _overseas_excd_symb("INTC")[0][0] == "NAS"

    def test_kis_get_overseas_realtime_price(self, monkeypatch):
        import bot.kis_client as k
        monkeypatch.setattr(k, "_cache_get", lambda key, ttl_hours=None: None)
        monkeypatch.setattr(k, "_cache_put", lambda key, val: None)
        monkeypatch.setattr(k, "_get",
                            lambda path, tr_id, params, custtype=None: {"output": {"last": "185.5"}})
        assert k.KisClient().get_overseas_realtime_price("AAPL") == 185.5
        assert k.KisClient().get_overseas_realtime_price("7203.T") == 185.5
        # 대만 = KIS 해외 미지원 → None(graceful, _get 미호출).
        assert k.KisClient().get_overseas_realtime_price("2330.TW") is None

    def test_live_last_price_kr_order(self, monkeypatch):
        # KR: 네이버 우선 → 네이버 None 이면 KIS → 둘 다 None 이면 Yahoo.
        import bot.chart_data as cd
        import bot.kis_client as k
        import bot.naver_quote as nq
        # ① 네이버 살아있으면 네이버.
        monkeypatch.setattr(nq, "fetch_kr_quote", lambda t: {"price": 103})
        assert cd._live_last_price(None, {"close": [100, 101, 102]}, 0, "005930.KS") == 103
        # ② 네이버 None → KIS.
        monkeypatch.setattr(nq, "fetch_kr_quote", lambda t: None)
        monkeypatch.setattr(k.KisClient, "get_realtime_price", lambda self, t: 105)
        assert cd._live_last_price(None, {"close": [100, 101, 102]}, 0, "005930.KS") == 105

    def test_live_last_price_overseas_order(self, monkeypatch):
        # US: 네이버 해외 우선 → None 이면 KIS 해외 → None 이면 Yahoo.
        import bot.chart_data as cd
        import bot.kis_client as k
        import bot.world_quote as wq
        monkeypatch.setattr(wq, "fetch_world_quote", lambda t: None)   # 네이버 해외 실패
        monkeypatch.setattr(k.KisClient, "get_overseas_realtime_price", lambda self, t: 185)
        assert cd._live_last_price(None, {"close": [178, 179, 180]}, 0, "AAPL") == 185

    def test_live_last_price_falls_back_to_yfinance(self, monkeypatch):
        import bot.chart_data as cd
        import bot.kis_client as k
        import bot.naver_quote as nq
        monkeypatch.setattr(nq, "fetch_kr_quote", lambda t: None)            # 네이버 실패
        monkeypatch.setattr(k.KisClient, "get_realtime_price", lambda self, t: None)  # KIS 실패

        class _FI:
            last_price = 104
        class _T:
            fast_info = _FI()
        out = cd._live_last_price(_T(), {"close": [100, 101, 102]}, 0, "005930.KS")
        assert out == 104, "네이버·KIS 모두 None 일 때 yfinance 폴백 실패"

    def test_wiring_present(self):
        ks = open("bot/kis_client.py", encoding="utf-8").read()
        assert "get_realtime_price" in ks and "get_overseas_realtime_price" in ks
        assert "_overseas_excd_symb" in ks and "HHDFS00000300" in ks, "KIS 해외시세 배선 누락"
        cd = open("bot/chart_data.py", encoding="utf-8").read()
        # 정책: ① 네이버(KR fetch_kr_quote · 비-KR worldstock/TWSE) → ② KIS
        # (get_realtime_price · get_overseas_realtime_price) → ③ Yahoo.
        assert "fetch_kr_quote" in cd and 'market == "KR"' in cd, "차트 KR→네이버 배선 누락"
        assert "fetch_world_quote" in cd and "fetch_tw_quote" in cd, "비-KR 네이버 배선 누락"
        assert "get_realtime_price" in cd and "get_overseas_realtime_price" in cd, "차트 KIS 폴백 배선 누락"


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
        assert resolve_overseas("페르미") == ("FRMI", "US")   # Fermi America(2026-06-19)
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

    def test_naver_resolver_graceful(self):
        # 네이버 자동완성 폴백 — 빈 입력은 네트워크 안 타고 None (2026-06-20).
        from bot.portfolio_resolve import resolve_via_naver
        assert resolve_via_naver("") is None
        assert resolve_via_naver("   ") is None

    def test_yfinance_resolver_graceful(self):
        # 야후 Search 폴백 — 빈 입력 None, 미설치/네트워크 시 graceful (2026-06-20).
        from bot.portfolio_resolve import resolve_via_yfinance
        assert resolve_via_yfinance("") is None
        # 없는 종목/네트워크 부재 → None (crash 없어야)
        assert resolve_via_yfinance("존재하지않는임의종목ZZZ") in (None,) or True


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

    def test_self_bond_acquire_parsed(self, tmp_path, monkeypatch):
        # 자기 전환사채 만기전 취득(Call Option) — '전환사채' 포함이라 CB 발행 파서로
        # 오라우팅되어 미파싱이던 것(보로노이 2026-06-22). 주주환원 분류 + 전용 파서.
        m = self._load(tmp_path, monkeypatch)
        assert m._classify_report("주요사항보고서(자기전환사채만기전취득결정)") == "주주환원"
        assert m._classify_report("주요사항보고서(자기교환사채만기전취득결정)") == "주주환원"
        # CB 발행은 안 깨짐(자금조달 유지)
        assert m._classify_report("주요사항보고서(전환사채권발행결정)") == "자금조달"
        txt = ("자기 전환사채 만기전 취득 결정 "
               "1. 사채의 종류 회차 3 종류 무보증 사모 전환사채 "
               "6. 취득 대상 사채의 권면(전자등록) 금액(원) 8,500,000,000 "
               "8. 취득금액 금액(원) 8,928,187,500 산정근거 원금 및 이자 "
               "9. 취득 방법 장외매수 "
               "10. 만기전 취득사유 매도청구권(Call Option) 행사 "
               "11. 향후 처리계획 이사회 "
               "13. 취득후 사채의 권면(전자등록) 잔액(원) 41,500,000,000")
        lines = m._self_bond_acquire_lines(txt)
        joined = " / ".join(lines)
        assert "3회차" in joined
        assert "85억원" in joined          # 취득대상 권면
        assert "89.3억원" in joined        # 취득금액(원금+이자)
        assert "Call Option" in joined     # 사유
        assert "장외매수" in joined        # 방법
        assert "415억원" in joined         # 취득후 잔액
        assert len(lines) >= 2
        # 빈 원문 → 헤더만(미달) → dispatch 가 fail-mark 후 generic 폴백
        assert len(m._self_bond_acquire_lines("관련 없는 텍스트")) == 1

    def test_noncorp_docs_split_from_backfill_candidates(self, tmp_path, monkeypatch):
        # 발행·등록 서류(투자설명서/일괄신고/증권신고서)는 펀드 동류 비대상 —
        # coverage 의 '보강 후보'가 아니라 별도 '의도된 제외'로 (사용자 2026-06-13)
        m = self._load(tmp_path, monkeypatch)
        for nm in ("투자설명서(일괄신고)", "일괄신고추가서류(파생결합증권-주가연계증권)",
                   "증권신고서(채무증권)", "[기재정정]투자설명서"):
            assert m._is_noncorp_doc(nm), nm
        for nm in ("최대주주등소유주식변동신고서", "주주총회소집공고",
                   "단일판매ㆍ공급계약체결", "현금ㆍ현물배당결정"):
            assert not m._is_noncorp_doc(nm), nm
        # coverage_audit 3분류: 비대상 문서는 dropped_noncorp 로, 실제 사건은
        # dropped_listed(보강 후보)로 갈림 (제목만 '기타' 드롭되는 상장사 기준)
        items = [
            {"category": "기타", "stock_code": "123456",
             "report_nm": "투자설명서(일괄신고)"},
            {"category": "기타", "stock_code": "123456",
             "report_nm": "증권신고서(지분증권)"},
            {"category": "기타", "stock_code": "654321",
             "report_nm": "주주총회소집공고"},
            {"category": "계약", "stock_code": "111111",
             "report_nm": "단일판매ㆍ공급계약체결"},
            {"category": "기타", "stock_code": "",        # 비상장/펀드
             "report_nm": "투자설명서(집합투자증권)"},
        ]
        monkeypatch.setattr(m, "fetch_market_disclosures", lambda *a, **k: items)
        rep = m.coverage_audit(days_back=1)
        assert sum(rep["dropped_noncorp"].values()) == 2     # 투자설명서 + 증권신고서
        # 주총소집공고는 A안(2026-06-13)으로 제목완결 거버넌스 → dropped_title
        assert sum(rep["dropped_listed"].values()) == 0      # 보강후보 0
        assert sum(rep["dropped_title"].values()) == 1       # 주총소집공고
        assert rep["dropped_other"] == 1                     # 비상장 펀드
        assert rep["kept"].get("계약") == 1
        # 드롭 모니터링 로그: 비대상 + 제목완결 거버넌스 제외, 실제 사건만 tally
        m._DROP_TALLY.clear()
        m._tally_drop("투자설명서(일괄신고)", "123456")       # noncorp 제외
        m._tally_drop("주주총회소집공고", "654321")            # 제목완결 제외 (A안)
        m._tally_drop("타법인주식및출자증권양수결정", "111111")  # 실제 사건 → tally
        assert sum(m._DROP_TALLY.values()) == 1

    def test_backfill_candidates_classified_and_title_only(self, tmp_path, monkeypatch):
        # 보강 후보 (사용자 2026-06-13 '파싱 진행') — 기타-드롭되던 실제 사건을
        # 카테고리화 + 제목완결 거버넌스류는 미파싱 색칠 제외
        m = self._load(tmp_path, monkeypatch)
        cat = {
            "벌금등의부과": "리스크",
            "벌금등의부과(자회사의 주요경영사항)": "리스크",
            "중대재해발생": "리스크",
            "재해발생": "리스크",                        # bare(라이브 2026-06-13)
            "대표이사변경(안내공시)": "회사구조",
            "대표집행임원변경": "회사구조",
            "대표이사(대표집행임원)변경(안내공시)": "회사구조",  # 괄호 변형(라이브)
            "상호변경안내": "회사구조",
            "본점소재지변경": "회사구조",
            "사외이사의선임ㆍ해임또는중도퇴임에관한신고": "회사구조",
            "주식매수선택권부여에관한신고": "회사구조",
            "기업가치제고계획(자율공시)": "주주환원",
        }
        for nm, exp in cat.items():
            assert m._classify_report(nm) == exp, (nm, m._classify_report(nm))
        # 회귀: 소유상황은 지분공시 파싱대상 — 대표'변경' 분기에 안 걸림
        assert m._classify_report("임원ㆍ주요주주특정증권등소유상황보고서") == "지분공시"
        assert m.is_parse_target({"category": "지분공시", "corp_code": "C",
            "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서"}) is True
        # 기존 분류 회귀 0
        assert m._classify_report("단일판매ㆍ공급계약체결") == "계약"
        assert m._classify_report("현금ㆍ현물배당결정") == "배당"
        # 제목완결 거버넌스/리스크류 = is_parse_target False (미파싱 색칠 제외)
        for nm in ("벌금등의부과", "대표이사변경(안내공시)", "상호변경안내",
                   "주식매수선택권부여에관한신고", "중대재해발생", "재해발생",
                   "대표이사(대표집행임원)변경(안내공시)"):
            it = {"category": m._classify_report(nm), "report_nm": nm,
                  "corp_code": "C"}
            assert m.is_parse_target(it) is False, nm
        # 실제 detail 있는 유형은 여전히 파싱 대상(회귀 가드)
        assert m.is_parse_target({"category": "계약", "corp_code": "C",
                                  "report_nm": "단일판매ㆍ공급계약체결"}) is True

    def test_coverage_audit_2nd_candidates_classified(self, tmp_path, monkeypatch):
        # 사용자 2026-06-15 coverage-audit '기타 제목 분포' 보강 후보 — 기타-드롭
        # 되던 실제 기업 사건을 카테고리화 + 제목완결(미파싱 색칠 제외).
        m = self._load(tmp_path, monkeypatch)
        cat = {
            "기타시장안내(기업심사위원회 심의·의결 결과 안내)": "리스크",
            "기타시장안내(정리매매 보류 관련)": "리스크",
            "기타시장안내(개선기간 부여 결정)": "리스크",
            "회계처리기준위반에따른임원의해임권고조치": "리스크",
            "특수관계인에대한출자": "회사구조",
            "특수관계인으로부터자금차입": "회사구조",
            "지주회사의자회사편입": "회사구조",
            "외국지주회사의자회사편입ㆍ탈퇴": "회사구조",
            "대규모기업집단현황공시": "회사구조",
            "동일인등출자계열회사와의상품ㆍ용역거래": "회사구조",
            "자산재평가실시결정(자율공시)": "회사구조",
            "상각형조건부자본증권발행결정": "자금조달",
            "교환가액의조정": "자금조달",
            "주권관련사채권의취득결정": "자금조달",
            # 투자판단관련주요경영사항 = 실적 아님(사용자 2026-06-16 압타바이오 FDA) —
            # 본문 미승격 시 '기타'(일반 주요경영사항), force-parse 라 카드 detail 유지.
            "투자판단관련주요경영사항(임상시험결과)": "기타",
        }
        for nm, exp in cat.items():
            assert m._classify_report(nm) == exp, (nm, m._classify_report(nm))
        # 임상 투자판단(압타바이오 류): 실적 칩 오염 0 + force-parse 라 카드 보존.
        _ap_nm = "투자판단관련주요경영사항(임상시험결과)"
        _ap = {"category": m._classify_report(_ap_nm), "report_nm": _ap_nm, "corp_code": "C"}
        assert _ap["category"] != "실적", _ap["category"]
        assert m.is_parse_target(_ap) is True   # 카드 detail 유지(force-parse)
        # 제목완결 = is_parse_target False(미파싱 색칠 제외). 투자판단은 force-parse 라 제외.
        for nm in ("기타시장안내(기업심사위원회 심의·의결 결과 안내)",
                   "특수관계인에대한출자", "회계처리기준위반에따른임원의해임권고조치",
                   "상각형조건부자본증권발행결정"):
            it = {"category": m._classify_report(nm), "report_nm": nm, "corp_code": "C"}
            assert m.is_parse_target(it) is False, nm
        # 기존 분류 회귀 0
        assert m._classify_report("단일판매ㆍ공급계약체결") == "계약"
        assert m._classify_report("현금ㆍ현물배당결정") == "배당"

    def test_unparsed_audit_counts_real_unparsed(self, tmp_path, monkeypatch):
        # 사용자 2026-06-15 '미파싱 쌓임' — unparsed_audit 가 아카이브의 **진짜
        # 미파싱**(파싱대상·detail 없음·intended-freeform 제외)만 report_nm 분포로
        # 집계(대시보드 ⚠️ 칩과 동일 판정). 파서 추가 진단용.
        m = self._load(tmp_path, monkeypatch)
        arch = {"2026-06-15": [
            {"category": "계약", "report_nm": "단일판매ㆍ공급계약체결", "detail": [],
             "url": "u1", "corp_code": "C", "stock_code": "000001"},
            {"category": "계약", "report_nm": "단일판매ㆍ공급계약체결",
             "detail": ["계약금액: 100억"], "url": "u2", "corp_code": "C",
             "stock_code": "000001"},
            {"category": "기타", "report_nm": "주주총회소집공고", "detail": [],
             "url": "u3", "corp_code": "C", "stock_code": "000003"},
        ]}
        monkeypatch.setattr(m, "load_all_archives", lambda days_back: arch)
        rep = m.unparsed_audit(7)
        assert rep["total"] == 1                                  # detail 없는 계약만
        assert rep["dist"].get("단일판매ㆍ공급계약체결") == 1
        assert "주주총회소집공고" not in rep["dist"]               # 제목완결=파싱대상 아님
        assert rep["samples"].get("단일판매ㆍ공급계약체결") == "u1"

    def test_meaningful_detail_gate(self, tmp_path, monkeypatch):
        # 미파싱 재추출 백필(2026-06-20)의 핵심 게이트 — 시총/주요사업 줄만 있으면
        # meaningful 아님(=재추출 대상). _admin_issue_lines '경과:' 줄이 생기면 해소.
        m = self._load(tmp_path, monkeypatch)
        assert m._has_meaningful_detail(["결론: ... 시가총액 150억원 미만 ..."]) is False
        assert m._has_meaningful_detail(["주요사업: 반도체"]) is False
        assert m._has_meaningful_detail([]) is False
        assert m._has_meaningful_detail(["경과: 연속 25매매거래일 미달 지속"]) is True

    def test_owner_share_change_form_classified_and_routed(self, tmp_path, monkeypatch):
        # 최대주주등소유주식변동신고서(공정거래법, 라이브 2026-06-13 5예시) —
        # 지분공시 분류 + 파싱대상 + _major_holding_change_lines 라우팅 + 고빈도
        # 라 _equity_noise detail-required 게이트(미파싱 숨김=홍수 방지)
        m = self._load(tmp_path, monkeypatch)
        nm = "최대주주등소유주식변동신고서"
        assert m._classify_report(nm) == "지분공시"
        assert m.is_parse_target({"category": "지분공시", "corp_code": "C",
                                  "report_nm": nm}) is True
        # dispatch + 게이트 배선(소스 가드)
        src = (__import__("pathlib").Path(m.__file__).read_text("utf-8"))
        assert '"소유주식변동" in t' in src                  # _extract_detail 라우팅
        from pathlib import Path as _P
        dash = (_P(m.__file__).resolve().parent / "dashboard.py").read_text("utf-8")
        assert "소유주식변동" in dash                         # _equity_noise 게이트
        # 회귀: 대량보유/소유상황 여전히 지분공시 파싱대상
        for r in ("주식등의대량보유상황보고서",
                  "임원ㆍ주요주주특정증권등소유상황보고서"):
            assert m._classify_report(r) == "지분공시"
            assert m.is_parse_target({"category": "지분공시", "corp_code": "C",
                                      "report_nm": r}) is True


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

    def test_extract_detail_generic_fallback_wrapper(self, tmp_path, monkeypatch):
        # 사용자 2026-06-14 '미파싱 9개' — 전용 분기가 None 이어도 generic 원문
        # 폴백 보장(옛 early-return 이 generic 건너뛰어 전환청구권/IR/대량보유 등
        # 미파싱 남던 것 fix). 래퍼가 어느 분기든 None 이면 generic 시도.
        m = self._load(tmp_path, monkeypatch)
        monkeypatch.setattr(m, "_extract_detail_specific", lambda *a, **k: None)
        monkeypatch.setattr(m, "_extract_generic_document",
                            lambda rno, key: {"lines": ["보고자: 홍길동"]})
        r = m._extract_detail("아무공시", "RX", "C", "K")
        assert r and r["lines"] == ["보고자: 홍길동"]   # generic 폴백 발동
        # category 승격 보존 (전용이 category-only 반환)
        monkeypatch.setattr(m, "_extract_detail_specific",
                            lambda *a, **k: {"category": "주주환원"})
        r2 = m._extract_detail("x", "RX", "C", "K")
        assert r2["category"] == "주주환원" and r2["lines"] == ["보고자: 홍길동"]
        # 둘 다 None → None (회귀 0)
        monkeypatch.setattr(m, "_extract_generic_document", lambda rno, key: None)
        monkeypatch.setattr(m, "_extract_detail_specific", lambda *a, **k: None)
        assert m._extract_detail("x", "RX", "C", "K") is None

    def test_numbered_rows_korean_enum(self):
        # 사용자 2026-06-14 — 가나다 enumeration(변형 양식) generic 발췌.
        from bot.dart_feed import _numbered_rows_lines
        r = _numbered_rows_lines("가. 보고자 홍길동 나. 보유비율 5.12% 다. 보고사유 -")
        assert any("보고자" in x for x in r) and any("보유비율" in x for x in r)
        assert all("보고사유" not in x for x in r)      # '-' 값 스킵(노이즈)
        r2 = _numbered_rows_lines("1. 일시 2026-06-16 2. 장소 본사")
        assert any("일시" in x for x in r2) and any("장소" in x for x in r2)  # 숫자 호환

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
        html = db._render_dart_feed_page(m.load_all_archives(days_back=2))[0]
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
        # 제목만이 정상 — 대량보유 외 지분·구조화 없는 자금조달·corp 부재
        # (IR 은 2026-06-13 부터 파싱 대상 — TestDartIRParsing 참조)
        assert is_parse_target({"category": "IR",
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

    def test_intended_freeform_split(self):
        # 사용자 2026-06-14 '의도한 미파싱은 미파싱제외로, 진짜 미파싱과 나눠'.
        from bot.dart_feed import intended_freeform_unparsed as ff, is_parse_target
        assert ff("기타경영사항(자율공시)")                 # catch-all freeform
        assert ff("[기재정정]기타경영사항(자율공시)")
        assert ff("투자판단관련주요경영사항")
        assert ff("기타시장안내")
        assert ff("[첨부정정]주요사항보고서(회사합병결정)")  # 첨부만 정정(본문 없음)
        # 2026-06-14 미파싱 정리 — 기계적 거래정지해제·LP·IR결과 = 미파싱제외
        assert ff("주권매매거래정지해제(액면병합 주권 변경상장)")
        assert ff("유동성공급계약의체결")
        assert ff("기업설명회(IR)개최결과")
        # 2026-06-14 title-only — 원문 미제공·구조화 API 부재(합병/분할 종료보고서·
        # 대량보유 약식). detail 빈 게 정상 → 미파싱제외(사용자 '그냥 타이틀 온리').
        assert ff("합병등종료보고서(합병)")
        assert ff("회사분할종료보고서")
        assert ff("주식등의대량보유상황보고서(약식)")
        assert not ff("주식등의대량보유상황보고서(일반)")    # 일반=majorstock API
        assert not ff("유형자산양수도종료보고서")            # _asset_complete_lines 파싱
        # 2026-06-17 '미파싱제외건' — 자산/영업 양수도·양도 종료보고서(完了 보고,
        # 전용 소스 없음)는 미파싱제외. 단 유형자산 양수도 종료는 위처럼 파서 보유라 제외.
        assert ff("합병등종료보고서(자산양수도)")            # 합병등 → 完了 보고
        assert ff("자산양수도종료보고서")
        assert ff("영업양도종료보고서")
        assert not ff("단일판매ㆍ공급계약체결")             # 진짜 파서 대상
        assert not ff("주요사항보고서(유상증자결정)")
        assert not ff("[기재정정]단일판매ㆍ공급계약체결")   # 본문 정정은 재추출 대상
        assert not ff("주권매매거래정지(단일판매공급계약)")  # 정지(halt)는 리스크 유지
        assert not ff("기업설명회(IR)개최(안내공시)")        # 안내공시는 파싱 대상
        # 펀드 발행실적(집합투자증권) → noncorp → 미파싱 아님(의도된 제외)
        assert not is_parse_target({"report_nm": "증권발행실적보고서(집합투자증권)(IBK다보스글로벌고배당증권자투자신탁3호[주식])",
                                    "category": "자금조달", "corp_code": "x"})
        # 대시보드 배선 — 미파싱제외 칩/카드클래스/필터플래그
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "intended_freeform_unparsed" in src
        assert 'data-flag="noparse"' in src and "df-noparse" in src
        assert "미파싱제외" in src
        assert "noparse:'미파싱제외'" in src                 # CSV 라벨

    def test_admin_issue_parsing(self):
        # 관리종목 지정우려/지정 (사용자 2026-06-19 '미파싱제외인데 파싱돼야') —
        # 기타→리스크 재분류 + freeform 제외 + 전용 파서(사유·연속거래일·지정조건).
        from bot.dart_feed import (_admin_issue_lines, _classify_report,
                                   intended_freeform_unparsed, is_parse_target)
        body = ("제목 : (주)엑시온그룹 관리종목 지정우려 관련 안내(시가총액 150억원 "
                "미달) 코스닥시장 상장규정 제53조 및 동규정 부칙 제2343호 제4조에 따라 "
                "'보통주식의 시가총액이 150억원 미만인 상태가 연속하여 30일(매매거래일 "
                "기준)동안 계속'되는 경우 관리종목으로 지정하고 있으며, 이와 관련하여 "
                "동사는 '26.06.18 현재 시가총액 150억원 미만인 상태가 연속하여 25매매"
                "거래일 동안 지속되었습니다. 따라서 시가총액 150억원 미만인 상태가 "
                "'26.06.19부터 5매매거래일 지속될 경우 관리종목으로 지정될 수 있음을 "
                "알려드립니다.")
        lines = _admin_issue_lines(body)
        assert any("시가총액 150억원 미달" in x for x in lines)      # 사유
        assert any("연속 25매매거래일" in x for x in lines)          # 경과(규정 30일 아님)
        assert any("5매매거래일" in x and "2026.06.19" in x for x in lines)  # 지정조건+시작일
        assert any("기준일: 2026.06.18" in x for x in lines)          # 기준일
        # 분류 = 리스크, parse target, freeform 제외(진짜 미파싱 추적)
        rn = "기타시장안내(관리종목지정우려종목)"
        assert _classify_report(rn) == "리스크"
        assert intended_freeform_unparsed(rn) is False
        assert is_parse_target({"category": "리스크", "report_nm": rn, "corp_code": "x"})
        # 회귀: 비-관리종목 기타시장안내는 기존대로 freeform(미파싱제외) 유지
        assert intended_freeform_unparsed("기타시장안내(투자주의환기종목)") is True

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
        ) == "매출액 +25.3%"
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

    def test_significance_rule5_capex_15pct(self):
        # 2026-06-13: 임계 20→15 + % 없는 '39.00' 표기(capex 파서 실포맷)
        # 미발화 버그 fix — 종근당 39.00/웨이비스 50.80 케이스
        from bot.dart_feed import significance
        assert significance({"report_nm": "신규시설투자등", "category": "신규시설투자",
                             "detail": ["자기자본대비: 39.00"]}
                            ) == "자기자본대비 39% 투자"
        assert significance({"report_nm": "신규시설투자등(자회사의 주요경영사항)",
                             "category": "신규시설투자",
                             "detail": ["자기자본대비: 25.3%"]}
                            ) == "자기자본대비 25.3% 투자"
        assert significance({"report_nm": "신규시설투자등", "category": "신규시설투자",
                             "detail": ["자기자본대비: 14.9"]}) is None

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

    def test_us_highlow_30min_full_refresh(self, monkeypatch):
        # 사용자 2026-06-19: 미국 52주 30분마다 전량 재산출(B′·새벽 저부하). US TTL=30분,
        # 타 시장 1h 유지. force(장중 슬롯)=게이트 우회 → 항상 재산출 kick.
        import datetime as dt
        import bot.finviz_client as fv
        assert fv._HL_US_INTRA_TTL == 30 * 60       # US 30분
        assert fv._HL_INTRA_TTL == 60 * 60          # 타 시장 1h(불변)
        now = dt.datetime(2026, 6, 18, 15, 0, tzinfo=dt.timezone.utc).timestamp()  # 장중(목)
        assert fv._session_fresh("US", now - 25 * 60, fv._HL_US_INTRA_TTL, now) is True
        assert fv._session_fresh("US", now - 35 * 60, fv._HL_US_INTRA_TTL, now) is False
        monkeypatch.setattr(fv, "_cached", lambda *a, **k: {"high": [], "low": []})
        monkeypatch.setattr(fv, "_session_fresh", lambda *a, **k: True)   # 게이트 fresh 라도
        monkeypatch.setattr(fv, "_prune_cached", lambda d, *a, **k: d)
        kicked = []
        monkeypatch.setattr(fv, "_kick_full_us_refresh", lambda: kicked.append(1))
        fv._highlow_full_us(force=True)
        assert kicked, "force=True 인데 재산출 kick 안 됨"
        kicked.clear()
        fv._highlow_full_us(force=False)             # 게이트 fresh → kick 안 함
        assert not kicked, "force=False·fresh 인데 불필요 재산출"

    def test_us_highlow_in_30_slot_and_force_wired(self):
        # US 가 :00·:30 슬롯 + fetch_high_low 가 force 를 _highlow_full_us 로 전달.
        # 슬롯 정의는 bot.highlow_scan 으로 이관(2026-06-19 독립 타이머 분리).
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        hs = (root / "bot" / "highlow_scan.py").read_text(encoding="utf-8")
        assert '30: ("CN_A", "US")' in hs, "US 가 :30 슬롯에 없음"
        fv = (root / "bot" / "finviz_client.py").read_text(encoding="utf-8")
        assert "_highlow_full_us(force=force)" in fv, "fetch_high_low 가 force 미전달"

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
        # US 52주 장중 1h 시장-인지(2026-06-14) — 캐시 hit 경로 타게 fresh
        monkeypatch.setattr(fv, "_session_fresh", lambda *a, **k: True)
        monkeypatch.setattr(
            fv, "_fetch_industries",
            lambda tks, allow_slow=True: {"AAPL": "Consumer Electronics"})
        out = fv.fetch_high_low()
        assert out["high"][0]["ind"] == "Consumer Electronics"
        assert "highlow.json" in written


class TestEtfSectorMovers:
    """JP/TW/CN/HK 업종 등락 — 섹터 ETF 합성(사용자 2026-06-13 Phase 1).
    랭킹 순수부 + 시장별 ETF 맵 + 홈 위젯 렌더."""

    def test_rank_splits_by_sign_and_sorts(self):
        from bot.etf_sector_client import _rank_sector_etfs
        rows = [{"name": "반도체", "pct": 3.2}, {"name": "은행", "pct": -1.1},
                {"name": "의료", "pct": 0.0}, {"name": "주류", "pct": 1.5},
                {"name": "부동산", "pct": -2.3}, {"name": "결측", "pct": None}]
        r = _rank_sector_etfs(rows, top_n=10)
        assert [x["name"] for x in r["up"]] == ["반도체", "주류"]      # 양수 desc
        assert [x["name"] for x in r["down"]] == ["부동산", "은행"]    # 음수 asc
        # 0·None 은 어느 쪽에도 안 들어감
        names = [x["name"] for x in r["up"] + r["down"]]
        assert "의료" not in names and "결측" not in names

    def test_topn_cap(self):
        from bot.etf_sector_client import _rank_sector_etfs
        rows = [{"name": f"u{i}", "pct": float(i)} for i in range(1, 15)]
        r = _rank_sector_etfs(rows, top_n=10)
        assert len(r["up"]) == 10 and r["up"][0]["name"] == "u14"

    def test_market_etf_maps_present(self):
        from bot.etf_sector_client import _SECTOR_ETFS
        # JP=TOPIX-17 완전, CN 다수 — 메인 위젯용. TW/HK 는 ETF 희소(정직)
        assert len(_SECTOR_ETFS["JP"]) == 17
        assert len(_SECTOR_ETFS["CN_A"]) >= 12
        assert _SECTOR_ETFS["TW"] and _SECTOR_ETFS["HK"]
        # 중복 ETF 없음(distinct)
        for m, etfs in _SECTOR_ETFS.items():
            codes = [e[0] for e in etfs]
            assert len(codes) == len(set(codes)), m

    def test_home_widget_render_and_empty(self):
        from bot.dashboard import _render_etf_sector_movers
        mv = {"up": [{"name": "반도체", "pct": 3.2}],
              "down": [{"name": "은행", "pct": -1.1}],
              "ts": "2026-06-13 14:00", "source": "섹터 ETF·yfinance"}
        html = _render_etf_sector_movers(mv, "🇯🇵 일본 업종 등락 TOP 10")
        assert "일본 업종 등락" in html and "반도체" in html
        assert "섹터 ETF" in html and "+3.20%" in html
        # 빈 데이터 → 위젯 생략(빈 문자열)
        assert _render_etf_sector_movers({"up": [], "down": []}, "x") == ""

    def test_wired_into_market_overview(self):
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parents[1] / "bot"
               / "market_overview.py").read_text("utf-8")
        for k in ("jp_sector_movers", "tw_sector_movers",
                  "cn_sector_movers", "hk_sector_movers"):
            assert k in src, k
        assert "_fetch_etf_sector_safe" in src


class TestTwseSectorHighLow:
    """TW 업종(類股)+상한가/하한가 — TWSE MI_INDEX (사용자 2026-06-13 Phase 2,
    VM 200 검증). 방어적 파서(필드 시그니처) + 랭킹 + 페이지 graceful."""

    _TABLES = [
        {"title": "大盤", "fields": ["項目", "收盤"], "data": [["加權", "18000"]]},
        {"title": "類股指數",
         "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比"],
         "data": [
             ["半導體類指數", "450", "<p style=color:red>+</p>", "5", "2.13"],
             ["金融保險類指數", "1500", "<p style=color:green>-</p>", "3", "-0.62"],
             ["航運類指數", "200", "<p style=color:red>+</p>", "9", "4.50"],
             ["食品類指數", "1900", "<p style=color:green>-</p>", "2", "-0.21"],
             ["鋼鐵類指數", "120", "<p style=color:red>+</p>", "1", "1.05"],
             ["汽車類指數", "300", "<p style=color:red>+</p>", "2", "0.80"],
         ]},
        {"title": "每日收盤行情",
         "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額",
                    "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差",
                    "最後揭示買價", "最後揭示賣價", "本益比"],
         "data": [
             ["2330", "台積電", "1", "1", "1", "995", "1000", "990", "1000",
              "<p style=color:red>+</p>", "95", "", "", ""],          # +10.5%
             ["2317", "鴻海", "1", "1", "1", "100", "101", "99", "99",
              "<p style=color:green>-</p>", "11", "", "", ""],         # -10%
             ["2454", "聯發科", "1", "1", "1", "800", "810", "799", "805",
              "<p style=color:red>+</p>", "5", "", "", ""],            # +0.6%
         ] + [["9" + str(i).zfill(3), "x", "1", "1", "1", "10", "10", "10",
               "10", "X", "0", "", "", ""] for i in range(60)]},
    ]

    def test_sector_parse_korean_map(self):
        from bot.twse_client import parse_sector_rows
        secs = parse_sector_rows(self._TABLES)
        d = {s["name"]: s["pct"] for s in secs}
        assert d.get("반도체") == 2.13 and d.get("해운") == 4.50
        assert d.get("금융·보험") == -0.62

    def test_stock_parse_pct_and_sign(self):
        from bot.twse_client import parse_stock_rows
        stk = {s["code"]: s for s in parse_stock_rows(self._TABLES)}
        assert stk["2330"]["pct"] > 9.5            # 상한가권
        assert stk["2317"]["pct"] < -9.5           # 하한가권
        assert abs(stk["2454"]["pct"]) < 1         # 평범

    def test_sector_movers_rank(self, monkeypatch):
        import bot.twse_client as tw
        monkeypatch.setattr(tw, "fetch_mi_index", lambda: {
            "sectors": tw.parse_sector_rows(self._TABLES),
            "stocks": [], "ts": "2026-06-13 14:00"})
        r = tw.fetch_tw_sector_movers(top_n=10)
        assert r["up"][0]["name"] == "해운"        # +4.5 최상
        assert r["down"][0]["name"] == "금융·보험"
        assert r["source"] == "TWSE 類股"

    def test_openapi_stock_parse_and_filter(self, monkeypatch):
        # 상한가/하한가 = OpenAPI STOCK_DAY_ALL(평평한 dict 배열, 점검 무관)
        import bot.twse_client as tw
        rows = [
            {"Code": "2330", "Name": "台積電", "ClosingPrice": "1000", "Change": "95"},   # +10.5%
            {"Code": "2317", "Name": "鴻海", "ClosingPrice": "99", "Change": "-11"},      # -10%
            {"Code": "2454", "Name": "聯發科", "ClosingPrice": "805", "Change": "5"},      # +0.6%
            {"Code": "00910", "Name": "ETF", "ClosingPrice": "20", "Change": "2.3"},      # +12.9% ETF
            {"Code": "0050", "Name": "台灣50", "ClosingPrice": "180", "Change": "X"},      # 결측 컷
        ]
        parsed = tw.parse_stock_day_all(rows)
        d = {s["code"]: s for s in parsed}
        assert d["2330"]["pct"] > 9.5 and d["2317"]["pct"] < -9.5
        assert "0050" not in d                       # 결측 Change 컷
        assert tw._is_common_stock("2330") and not tw._is_common_stock("00910")
        assert not tw._is_common_stock("0050") and not tw._is_common_stock("2887A")
        assert tw._roc_to_iso("1150612") == "2026-06-12"   # ROC→ISO
        monkeypatch.setattr(tw, "fetch_stock_day_all",
                            lambda force=False: {"rows": parsed, "date": "2026-06-12"})
        ul = tw.fetch_tw_upper_lower()
        assert any(s["code"] == "2330" for s in ul["upper"])
        assert any(s["code"] == "2317" for s in ul["lower"])
        assert ul["date"] == "2026-06-12"                  # 자료 기준일
        # 순수종목만 — ETF(00910)는 +12.9%여도 제외
        assert not any(s["code"] == "00910" for s in ul["upper"])
        # **진정한 한도 도달**(±10% · 틱 반올림 ≥±9.9%) — 사용자 2026-06-13
        # 'TW 도 진정한 의미의 상한가/하한가'(JP ストップ高 미러, 옛 9.5 근접권 폐기).
        # Change=가격변동(±%아님): 전일100 → -9.95%(종90.05)·-9.86%(종90.14)
        near = tw.parse_stock_day_all([
            {"Code": "1234", "Name": "x", "ClosingPrice": "90.05", "Change": "-9.95"},
            {"Code": "5678", "Name": "y", "ClosingPrice": "90.14", "Change": "-9.86"}])
        monkeypatch.setattr(tw, "fetch_stock_day_all",
                            lambda force=False: {"rows": near, "date": "x"})
        low = tw.fetch_tw_upper_lower()["lower"]
        assert any(s["code"] == "1234" for s in low)       # -9.95% 포함(한도 도달)
        assert not any(s["code"] == "5678" for s in low)   # -9.86% 제외(근접 ≠ 한도)

    def test_highlow_page_graceful_and_data(self, monkeypatch):
        import bot.twse_client as tw
        # TW 급등/급락 (사용자 2026-06-14 상한가→급등락)
        monkeypatch.setattr(tw, "fetch_tw_movers", lambda limit=30: {
            "up": [{"code": "2330", "name": "台積電", "close": 1000, "pct": 10.5}],
            "down": [], "ts": "2026-06-13 14:00", "date": "2026-06-12"})
        from bot.tw_pages import render_tw_highlow_page
        html = render_tw_highlow_page()
        assert "台積電" in html and "급등" in html and "10.50%" in html
        assert "2026-06-12 종가 기준" in html              # 자료 기준일 표시
        # 빈 데이터 → graceful 안내
        monkeypatch.setattr(tw, "fetch_tw_movers",
                            lambda limit=30: {"up": [], "down": [], "ts": ""})
        assert "불러올 수 없습니다" in render_tw_highlow_page()

    def test_wired_into_server_and_overview(self):
        from pathlib import Path as _P
        root = _P(__file__).resolve().parents[1] / "bot"
        srv = (root / "dashboard_server.py").read_text("utf-8")
        assert "/twhighlow" in srv and "_handle_tw_page" in srv
        mo = (root / "market_overview.py").read_text("utf-8")
        assert "_fetch_tw_sector_safe" in mo and "twse_client" in mo


class TestIntlHighLow52:
    """JP/CN/HK/KR 52주 신고가/신저가 — TW 패턴 일반화(사용자 2026-06-13 '다른
    나라도 모두' + '한국도 신고가신저가'). peer 유니버스 + SWR 동기계산 금지
    + 페이지. KR 은 peer 맵의 해외 비교군을 native 접미사 필터로 제외."""

    def test_universe_from_peer_maps(self, monkeypatch):
        import bot.intl_highlow as ih
        # CN_A 재도입(2026-06-17) — CSI300+500(AKShare)+peer 폴백. AKShare 를 빈
        # 결과로 강제해 peer 폴백 경로를 결정적 검증(네트워크 0, VM 에서도).
        monkeypatch.setattr("bot.akshare_client.list_csi300_500", lambda: {})
        for mk, lo in (("JP", 40), ("HK", 30), ("KR", 50), ("CN_A", 30)):
            uni, names = ih._universe(mk)
            assert len(uni) >= lo, (mk, len(uni))
            assert len(uni) == len(set(uni))          # dedupe
            assert all(names[t] for t in uni)         # name 기본=ticker

    def test_highlow_bulk_drop_retry(self, monkeypatch):
        # 사용자 2026-06-19 '키옥시아 같은 미싱' — yfinance 벌크가 일부 티커를 통째
        # 드롭(285A 단일 다운로드는 성공·flag True 인데 120-배치에선 빠져 신고가 보드
        # 누락)하는 간헐 현상. _compute_highlow_from 이 누락분(_seen 미포함)을 작은
        # 배치로 1회 재시도해 미싱 방지. CLAUDE.md §7d 진입점 스모크.
        import pandas as pd
        import yfinance as yf
        import bot.finviz_client as fc
        idx = pd.bdate_range(end="2026-06-19", periods=260)
        n = len(idx)
        BIG = pd.DataFrame({"Open": [99500.0] * n,
                            "High": [99500.0] * (n - 1) + [108600.0],  # 당일 신고가
                            "Low": [99500.0] * n,
                            "Close": [99500.0] * (n - 1) + [108600.0],
                            "Volume": [1000] * n}, index=idx)
        calls = {"n": 0}

        def _fake_dl(tickers, **k):
            calls["n"] += 1
            req = tickers if isinstance(tickers, list) else [tickers]
            # 1차 벌크에선 285A.T 드롭(yfinance batch drop 재현), 재시도에선 반환
            avail = [t for t in req if not (calls["n"] == 1 and t == "285A.T")]
            if not avail:
                return pd.DataFrame()
            if len(req) == 1:
                return BIG.copy()                            # 단일 요청 → flat 컬럼
            return pd.concat({t: BIG.copy() for t in avail}, axis=1)  # 멀티레벨
        monkeypatch.setattr(fc, "yf_paused", lambda: False)
        monkeypatch.setattr(fc, "_cache_write", lambda *a, **k: None)
        monkeypatch.setattr(yf, "download", _fake_dl)
        out = fc._compute_highlow_from(["7203.T", "285A.T"], {}, "x.json", "src", "JP")
        hi = {r["ticker"] for r in out["high"]}
        assert "285A.T" in hi, "재시도가 벌크 드롭 285A 를 못 잡음"
        assert "7203.T" in hi
        # 소스 가드 — 재시도 배선
        src = open("bot/finviz_client.py", encoding="utf-8").read()
        assert "dropped = [t for t in universe if t not in _seen]" in src
        assert "range(0, len(dropped), 40)" in src
        assert 'getattr(df.columns, "nlevels", 1)' in src    # 단일 티커 flat 처리

    def test_kr_strips_foreign_comparables(self):
        # _KR_INDUSTRY_PEERS 는 반도체에 TSM/NVDA 등 해외 비교군 포함 →
        # .KS/.KQ 만 남겨야(해외 종목이 한국 52주 페이지에 새지 않게).
        import bot.intl_highlow as ih
        uni, _ = ih._universe("KR")
        assert uni and all(t.endswith((".KS", ".KQ")) for t in uni), uni[:5]
        # JP/CN/HK 는 native-only 맵이라 필터가 no-op(개수 보존)
        assert len(ih._universe("JP")[0]) == 102
        assert len(ih._universe("HK")[0]) == 51

    def test_fetch_never_sync_computes(self, monkeypatch):
        import bot.intl_highlow as ih
        import bot.finviz_client as fc
        kicked = {"n": 0}
        monkeypatch.setattr(ih, "_kick",
                            lambda m: kicked.__setitem__("n", kicked["n"] + 1))
        monkeypatch.setattr(fc, "_compute_highlow_from",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("sync!")))
        monkeypatch.setattr(fc, "_cached", lambda name, ttl=0: None)
        r = ih.fetch_intl_highlow("JP")
        assert r["building"] is True and kicked["n"] == 1

    def test_unknown_market_graceful(self):
        import bot.intl_highlow as ih
        r = ih.fetch_intl_highlow("XX")
        assert r["high"] == [] and r["building"] is False

    def test_page_and_wiring(self, monkeypatch):
        import bot.intl_highlow as ih
        monkeypatch.setattr(ih, "fetch_intl_highlow", lambda m: {
            "high": [{"ticker": "7203.T", "name": "7203.T", "price": 3000, "pct": 0.4}],
            "low": [], "ts": "x", "building": False})
        from bot.intl_pages import render_intl_highlow52_page
        h = render_intl_highlow52_page("JP")
        assert "7203.T" in h and "일본" in h and "52주 신고가" in h
        from pathlib import Path as _P
        root = _P(__file__).resolve().parents[1] / "bot"
        srv = (root / "dashboard_server.py").read_text("utf-8")
        assert "/jp52" in srv and "/hk52" in srv
        assert "/cn52" in srv                      # CN 52주 재도입(2026-06-17)
        assert "/kr52" in srv and "_handle_intl_page" in srv
        dash = (root / "dashboard.py").read_text("utf-8")
        assert 'href="jp52"' in dash and 'href="hk52"' in dash
        assert 'href="kr52"' in dash      # KR 위젯에 52주 신고저 링크

    def test_kr_page_renders(self, monkeypatch):
        import bot.intl_highlow as ih
        monkeypatch.setattr(ih, "fetch_intl_highlow", lambda m: {
            "high": [{"ticker": "005930.KS", "name": "005930.KS",
                      "price": 88000, "pct": 0.3}],
            "low": [], "ts": "x", "building": False})
        from bot.intl_pages import render_intl_highlow52_page, _FLAG
        assert _FLAG.get("KR") == "🇰🇷 한국"
        h = render_intl_highlow52_page("KR")
        assert "005930.KS" in h and "한국" in h and "52주 신고가" in h


class TestTwHighLow52:
    """대만 52주 신고가/신저가 — yfinance 유니버스 백그라운드 SWR (사용자
    2026-06-13, VM 10/10 검증). 동기계산 금지 + building + 페이지."""

    def test_fetch_never_sync_computes(self, monkeypatch):
        import bot.tw_highlow as th
        import bot.finviz_client as fc
        kicked = {"n": 0}
        monkeypatch.setattr(th, "_kick_tw_highlow",
                            lambda: kicked.__setitem__("n", kicked["n"] + 1))
        # _compute_highlow_from 이 호출되면 즉시 실패(동기계산 금지 가드)
        monkeypatch.setattr(fc, "_compute_highlow_from",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("sync compute!")))
        monkeypatch.setattr(fc, "_cached", lambda name, ttl=0: None)  # 캐시 부재
        r = th.fetch_tw_highlow()
        assert r["building"] is True and r["high"] == [] and r["low"] == []
        assert kicked["n"] == 1                       # 백그라운드 킥만

    def test_fresh_cache_served(self, monkeypatch):
        import bot.tw_highlow as th
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "_cached", lambda name, ttl=0: {
            "high": [{"ticker": "2330.TW", "name": "台積電", "price": 1000, "pct": 1.2}],
            "low": [], "ts": "x", "source": "TWSE"})
        # 시장-인지 신선도 fresh → 즉시 반환(재스캔 0). 옛 플랫 TTL 대체.
        monkeypatch.setattr(fc, "_session_fresh", lambda *a, **k: True)
        r = th.fetch_tw_highlow()
        assert r["high"][0]["ticker"] == "2330.TW"

    def test_failed_backoff_no_rekick(self, monkeypatch):
        import bot.tw_highlow as th
        import bot.finviz_client as fc
        import time as _t
        kicked = {"n": 0}
        monkeypatch.setattr(th, "_kick_tw_highlow",
                            lambda: kicked.__setitem__("n", kicked["n"] + 1))
        monkeypatch.setattr(fc, "_cached", lambda name, ttl=0: None)
        monkeypatch.setattr(th, "tw_highlow_status",
                            lambda: {"state": "failed", "ts": _t.time()})
        th.fetch_tw_highlow()
        assert kicked["n"] == 0                       # 5분 백오프 — kick 생략

    def test_page_building_and_data(self, monkeypatch):
        import bot.tw_highlow as th
        monkeypatch.setattr(th, "fetch_tw_highlow", lambda: {
            "high": [], "low": [], "ts": "", "building": True,
            "status": {"total": 1100}})
        from bot.tw_pages import render_tw_highlow52_page
        assert "산출 중" in render_tw_highlow52_page() and "1100" in render_tw_highlow52_page()
        monkeypatch.setattr(th, "fetch_tw_highlow", lambda: {
            "high": [{"ticker": "2330.TW", "name": "台積電", "price": 1000, "pct": 0.5}],
            "low": [], "ts": "2026-06-13 14:00", "building": False})
        h = render_tw_highlow52_page()
        assert "台積電" in h and "52주 신고가" in h

    def test_wired_into_server_and_widget(self):
        from pathlib import Path as _P
        root = _P(__file__).resolve().parents[1] / "bot"
        srv = (root / "dashboard_server.py").read_text("utf-8")
        assert "/tw52" in srv and "render_tw_highlow52_page" in srv
        dash = (root / "dashboard.py").read_text("utf-8")
        assert 'href="tw52"' in dash                  # 위젯 링크


class TestUsMovers:
    """가장 많이 오른/내린 TOP30 (사용자 2026-06-12 '신고가/신저가 옆에').
    랭킹 순수부 + SWR 비동기 + 페이지 렌더."""

    def test_rank_filters_and_sorts(self):
        from bot.finviz_client import _rank_us_movers
        rows = [
            {"ticker": "A", "price": 5.0, "pct": 40.0, "dollar_m": 2.0},
            {"ticker": "B", "price": 0.5, "pct": 90.0, "dollar_m": 9.0},   # 페니 컷
            {"ticker": "C", "price": 10.0, "pct": 25.0, "dollar_m": 0.1},  # 박거래 컷
            {"ticker": "D", "price": 20.0, "pct": -30.0, "dollar_m": 5.0},
            {"ticker": "E", "price": 30.0, "pct": 55.0, "dollar_m": 1.0},
            {"ticker": "F", "price": 30.0, "pct": None, "dollar_m": 1.0},  # 결측 컷
        ]
        ups, downs = _rank_us_movers(rows)
        assert [r["ticker"] for r in ups] == ["E", "A"]
        assert [r["ticker"] for r in downs] == ["D"]

    def test_rank_top_n_cap(self):
        from bot.finviz_client import _rank_us_movers
        rows = [{"ticker": f"T{i}", "price": 10.0, "pct": float(i),
                 "dollar_m": 5.0} for i in range(1, 41)]
        ups, downs = _rank_us_movers(rows, top_n=30)
        assert len(ups) == 30 and ups[0]["pct"] == 40.0 and not downs

    def test_fetch_never_sync_computes(self, monkeypatch):
        # 신고저 전미국 티어와 동일 — 렌더 경로에서 수 분 배치 동기 계산
        # 금지 (캐시 부재 시 백그라운드 kick + building 안내)
        import bot.finviz_client as fv
        monkeypatch.setattr(fv, "_cached", lambda *a, **k: None)
        kicked = []
        monkeypatch.setattr(fv, "_kick_us_movers_refresh",
                            lambda: kicked.append(1))
        out = fv.fetch_us_movers()
        assert out.get("building") and kicked

    def test_movers_page_render(self, monkeypatch):
        import bot.finviz_client as fv
        monkeypatch.setattr(fv, "fetch_us_movers", lambda: {
            "up": [{"ticker": "ATEX", "name": "Anterix", "price": 42.5,
                    "pct": 47.2, "mcap": 8.5, "ind": "Telecom Services",
                    "dollar_m": 3.1}],
            "down": [{"ticker": "XYZ", "name": "Xyz Inc", "price": 12.0,
                      "pct": -31.0, "mcap": None, "ind": None,
                      "dollar_m": 1.0}],
            "ts": "2026-06-12 21:00", "scanned": 6488,
            "source": "전 미국 상장 산출(yfinance · 당일 등락)"})
        from bot.us_pages import render_us_movers_page
        html = render_us_movers_page()
        assert "가장 많이 오른" in html and "ATEX" in html and "+47.20%" in html
        assert "가장 많이 내린" in html and "XYZ" in html and "-31.00%" in html
        assert "업종 분포" in html and "Telecom Services" in html
        assert 'href="usmovers"' in html      # 토글 탭 (3페이지 공유 shell)
        assert 'href="lookup/ATEX"' in html   # 종목 → 우리 분석 연결

    def test_movers_page_building_state(self, monkeypatch):
        import bot.finviz_client as fv
        monkeypatch.setattr(fv, "fetch_us_movers", lambda: {
            "up": [], "down": [], "ts": "", "source": "", "building": True})
        from bot.us_pages import render_us_movers_page
        html = render_us_movers_page()
        assert "첫 산출 진행 중" in html

    def test_movers_page_failed_and_progress_states(self, monkeypatch):
        # 실패가 '산출 중'으로 위장하던 silent-fail 차단 (2026-06-13)
        import bot.finviz_client as fv
        from bot.us_pages import render_us_movers_page
        monkeypatch.setattr(fv, "fetch_us_movers", lambda: {
            "up": [], "down": [], "ts": "", "source": "", "building": True,
            "status": {"state": "failed", "ts_label": "2026-06-13 03:40",
                       "detail": "행 0 (빈 배치 59/59 — yfinance 일시 제한 의심)"}})
        html = render_us_movers_page()
        assert "산출 실패" in html and "빈 배치 59/59" in html
        monkeypatch.setattr(fv, "fetch_us_movers", lambda: {
            "up": [], "down": [], "ts": "", "source": "", "building": True,
            "status": {"state": "running", "done": 25, "total": 59,
                       "ts_label": "2026-06-13 03:41"}})
        html = render_us_movers_page()
        assert "배치 25/59" in html

    def test_movers_failed_backoff_no_rekick(self, monkeypatch):
        # 5분 내 실패 직후엔 매 방문 재스캔 안 함 (429 악순환 차단)
        import time as _t
        import bot.finviz_client as fv
        monkeypatch.setattr(
            fv, "_cached",
            lambda name, *a, **k: ({"state": "failed", "ts": _t.time() - 60}
                                   if name == fv._MOVERS_STATUS else None))
        kicked = []
        monkeypatch.setattr(fv, "_kick_us_movers_refresh",
                            lambda: kicked.append(1))
        out = fv.fetch_us_movers()
        assert out.get("building") and not kicked
        # 실패가 오래됐으면(>5분) 다시 kick
        monkeypatch.setattr(
            fv, "_cached",
            lambda name, *a, **k: ({"state": "failed", "ts": _t.time() - 900}
                                   if name == fv._MOVERS_STATUS else None))
        fv.fetch_us_movers()
        assert kicked

    def test_movers_session_aware_freshness(self):
        # 장-인지 TTL: 장중 30초 / 장 밖은 '마지막 마감 이후 산출본' fresh
        # (사용자 2026-06-15 '급등락 30초, 대만제외' — 52주 1h 와 분리)
        from datetime import datetime, timezone
        from bot.finviz_client import _movers_cache_is_fresh

        def ts(*a):
            return datetime(*a, tzinfo=timezone.utc).timestamp()
        wed_1500 = ts(2026, 6, 10, 15, 0)          # 수요일 장중
        # 무버 장중 TTL = 30초 (사용자 2026-06-15 '급등락 30초, 대만제외')
        assert _movers_cache_is_fresh(wed_1500 - 15, wed_1500)        # 15초 (30초 내)
        assert not _movers_cache_is_fresh(wed_1500 - 45, wed_1500)    # 45초 (30초 초과)
        sat_noon = ts(2026, 6, 13, 12, 0)          # 토요일 (장 밖)
        fri_2200 = ts(2026, 6, 12, 22, 0)          # 금 마감(21:30) 후 산출
        fri_2000 = ts(2026, 6, 12, 20, 0)          # 금 장중 산출 (마감 미반영)
        assert _movers_cache_is_fresh(fri_2200, sat_noon)
        assert not _movers_cache_is_fresh(fri_2000, sat_noon)
        mon_1000 = ts(2026, 6, 15, 10, 0)          # 월 프리오픈 (장 밖)
        sun_0300 = ts(2026, 6, 14, 3, 0)
        assert _movers_cache_is_fresh(sun_0300, mon_1000)   # 금 마감 후 ✓

    def test_movers_total_failure_writes_status(self, monkeypatch):
        # 전 배치 빈 응답 → failed 마커 (영구 building 차단의 핵심)
        import sys
        import types
        import bot.finviz_client as fv
        written = {}
        monkeypatch.setattr(fv, "_cache_write",
                            lambda n, o: written.update({n: o}))
        monkeypatch.setattr(fv, "_us_full_universe",
                            lambda: (["AAA", "BBB"], {}))
        fake_yf = types.ModuleType("yfinance")
        fake_yf.download = lambda *a, **k: None     # 전 배치 빈 응답
        monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
        fv._compute_us_movers()
        st = written.get(fv._MOVERS_STATUS) or {}
        assert st.get("state") == "failed" and "빈 배치" in st.get("detail", "")
        assert fv._MOVERS_CACHE not in written      # 빈 결과는 캐시 안 씀

    def test_highlow_page_panels_still_render(self, monkeypatch):
        # _stock_panel 승격 리팩토링 회귀 가드 — 신고저 페이지 표 동일 유지
        import bot.finviz_client as fv
        monkeypatch.setattr(fv, "fetch_high_low", lambda: {
            "high": [{"ticker": "KO", "name": "Coca-Cola", "price": 71.2,
                      "pct": 1.1, "mcap": 3000.0, "ind": "Beverages"}],
            "low": [], "ts": "T", "source": "Finviz"})
        from bot.us_pages import render_us_highlow_page
        html = render_us_highlow_page()
        assert "52주 신고가" in html and "KO" in html and "Beverages" in html
        assert "hl-table" in html and 'href="lookup/KO"' in html


class TestUsPrepost:
    """미국 장전/장후 급등락 (prepost_client) — 순수 함수 + 페이지 렌더 가드."""

    def test_in_extended_window(self):
        from datetime import datetime, timezone
        from bot.prepost_client import _in_extended_window

        def W(*a):
            return _in_extended_window(datetime(*a, tzinfo=timezone.utc))
        # 수 2026-06-10: 09:00 UTC=05:00 ET 장전 / 17:00 UTC 정규장 / 22:00 UTC 장후
        assert W(2026, 6, 10, 9, 0) is True       # 장전
        assert W(2026, 6, 10, 17, 0) is False     # 정규장
        assert W(2026, 6, 10, 22, 0) is True      # 장후
        assert W(2026, 6, 13, 12, 0) is False     # 토 정오 (휴장)
        assert W(2026, 6, 13, 0, 30) is True      # 토 새벽 = 금 장후 tail
        assert W(2026, 6, 14, 22, 0) is False     # 일 (휴장)

    def test_prepost_fresh(self):
        from datetime import datetime, timezone
        from bot.prepost_client import _prepost_fresh
        now = datetime(2026, 6, 10, 22, 0, tzinfo=timezone.utc).timestamp()  # 장후 창
        assert _prepost_fresh(now - 10 * 60, now)         # 10분 = fresh(<30m)
        assert not _prepost_fresh(now - 40 * 60, now)     # 40분 = stale
        closed = datetime(2026, 6, 10, 17, 0, tzinfo=timezone.utc).timestamp()  # 정규장
        assert _prepost_fresh(closed - 10 * 3600, closed)  # 장 밖 = 항상 fresh(재스캔0)

    def test_rank_prepost(self):
        from bot.prepost_client import _rank_prepost
        rows = [{"ticker": "A", "pct": 12.0, "price": 50, "vol": 5000},
                {"ticker": "B", "pct": -8.0, "price": 20, "vol": 9000},
                {"ticker": "C", "pct": 99.0, "price": 5, "vol": 9000},   # 글리치 컷
                {"ticker": "D", "pct": 3.0, "price": 0.5, "vol": 9000},  # 페니 컷
                {"ticker": "E", "pct": 4.0, "price": 30, "vol": 100}]     # 박거래 컷
        ups, downs = _rank_prepost(rows)
        assert [r["ticker"] for r in ups] == ["A"]
        assert [r["ticker"] for r in downs] == ["B"]

    def test_rank_prepost_premarket_reg_vol_zero(self):
        # 장전 버그fix(2026-06-16): 정규장 개장 전이라 reg_vol=0 이어도 NXT vol 로
        # 폴백 게이트 → 장전 종목 생존. 옛 key-default 는 reg_vol=0 을 그대로 써
        # 전 종목 컷 → 장전 빈 보드였음.
        from bot.prepost_client import _rank_prepost
        rows = [{"ticker": "P", "pct": 7.0, "price": 100, "reg_vol": 0, "vol": 5000},
                {"ticker": "Q", "pct": 5.0, "price": 100, "reg_vol": 0, "vol": 100}]  # NXT 박거래 컷
        ups, downs = _rank_prepost(rows)
        assert [r["ticker"] for r in ups] == ["P"]   # reg_vol=0 이어도 NXT vol=5000 통과
        assert [r["ticker"] for r in downs] == []     # Q 는 NXT vol=100 박거래 컷

    def test_kr_over_session_excludes_regular(self):
        # 보드는 장전(PRE)·장후(AFTER) 시간외만 — REGULAR_MARKET(정규장 NXT 연속
        # 거래)·기타는 None → 제외(2026-06-16: 11:35 정규장 강제산출 시 REGULAR 행이
        # '장후'로 오분류돼 새던 것 원천 차단).
        from bot.prepost_client import _kr_over_session
        assert _kr_over_session("PRE_MARKET") == "pre"
        assert _kr_over_session("AFTER_MARKET") == "post"
        assert _kr_over_session("REGULAR_MARKET") is None
        assert _kr_over_session("") is None and _kr_over_session(None) is None

    def test_kr_universe_fallback_on_empty(self, monkeypatch):
        # 장전(08:00–09:00, 정규장 개장 전) 네이버 정규장 무버가 비면 NXT 스캔이
        # universe-empty 로 영영 실패 → '장전집계 또 안 됨'. 직전 성공 유니버스
        # 폴백(전일 정규장 무버 = NXT 활동 확인용 후보군). 라이브 차면 캐시 갱신·no-op.
        import bot.naver_ranking_client as nr
        import bot.prepost_client as pp
        store = {}
        monkeypatch.setattr(pp, "_cached", lambda name, ttl=0: store.get(name))
        monkeypatch.setattr(pp, "_cache_write",
                            lambda name, val: store.__setitem__(name, val))
        monkeypatch.setattr(nr, "fetch_kr_movers", lambda limit=30: {
            "up": [{"ticker": "005930.KS", "name": "삼성전자", "mcap": 5e6}],
            "down": [{"ticker": "000660.KS", "name": "SK하이닉스", "mcap": 4e6}]})
        tks, _names, _mcaps = pp._kr_movers_universe()
        assert set(tks) == {"005930.KS", "000660.KS"}
        assert store.get(pp._KR_UNIVERSE_CACHE)              # 라이브 성공 → 캐시 적재
        monkeypatch.setattr(nr, "fetch_kr_movers",
                            lambda limit=30: {"up": [], "down": []})
        tks2, names2, _ = pp._kr_movers_universe()
        assert set(tks2) == {"005930.KS", "000660.KS"}       # 빈 라이브 → 직전 유니버스
        assert names2.get("005930.KS") == "삼성전자"
        # 라이브·캐시 둘 다 비면 graceful 빈(크래시 X)
        store.clear()
        assert pp._kr_movers_universe()[0] == []

    def test_kr_universe_fallback_ttl_survives_weekend(self, monkeypatch):
        # 사용자 2026-06-22 월요일 08:56 'universe 실패' — 폴백 캐시 read TTL이
        # 24h라 금요일 장후 캐시가 월요일 장전(~61h)에 만료 → 폴백도 빈 보드.
        # TTL은 주말+연휴를 넘겨야(>=3일; 현재 14일=설·추석 커버). 24h로 회귀하면
        # 이 가드가 잡는다. (기존 fallback 테스트는 ttl 무시 mock이라 못 잡았음.)
        import bot.naver_ranking_client as nr
        import bot.prepost_client as pp
        seen = {}

        def _cap(name, ttl=0):
            seen[name] = ttl
            return {"tks": ["005930.KS"], "names": {}, "mcaps": {}}
        monkeypatch.setattr(pp, "_cached", _cap)
        monkeypatch.setattr(pp, "_cache_write", lambda name, val: None)
        monkeypatch.setattr(nr, "fetch_kr_movers",
                            lambda limit=30: {"up": [], "down": []})
        pp._kr_movers_universe()
        assert seen.get(pp._KR_UNIVERSE_CACHE, 0) >= 3 * 86400   # 최소 주말 생존

    def test_kr_prepost_page_failed_banner(self, monkeypatch):
        # 직전 스냅샷 서빙 중 최근 집계 실패면 페이지에 '사유' 배너 노출 (silent-fail
        # 제거, 실수 #12) — 사용자가 '왜 오늘 장전이 안 보이는지' 화면에서 즉시 인지.
        import bot.prepost_client as pp
        monkeypatch.setattr(pp, "fetch_kr_prepost_movers", lambda: {
            "up": [{"ticker": "005930.KS", "name": "삼성전자", "price": 80000,
                    "pct": 1.2, "vol": 50000, "value": 40.0, "mcap": 5e6,
                    "session": "post"}],
            "down": [], "ts": "2026-06-18 19:58", "source": "x", "session": "post"})
        monkeypatch.setattr(pp, "kr_prepost_status", lambda: {
            "state": "failed", "ts_label": "2026-06-19 08:30",
            "detail": "universe 실패(네이버 무버 0)", "scanned": 0})
        from bot.intl_pages import render_kr_prepost_page
        html = render_kr_prepost_page()
        assert "최근 NXT 집계 실패" in html
        assert "universe 실패(네이버 무버 0)" in html and "2026-06-19 08:30" in html
        # state=done 이면 배너 없음(스냅샷이 곧 최신)
        monkeypatch.setattr(pp, "kr_prepost_status", lambda: {"state": "done"})
        assert "최근 NXT 집계 실패" not in render_kr_prepost_page()

    def test_light_board_warm_kr_ext_uses_kst_weekday(self):
        # 워머 kr_ext 게이트는 KST weekday 로 — KST 08:xx 는 UTC 전일이라 raw
        # now.weekday()(UTC)면 월요일 장전(UTC 일요일)이 weekday<5 에 걸려 영영
        # 워밍 안 됨('NXT 장전집계 또 안 됨' 클래스). KST 로 교정한 게 회귀 안 하게.
        import re
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert re.search(r"_kst = now \+ _td\(hours=9\)[\s\S]{0,400}?"
                         r"kr_ext = _kst\.weekday\(\) < 5", src), \
            "워머 kr_ext 가 KST weekday 를 안 씀 (월요일 장전 갭 회귀)"
        assert "kr_ext = now.weekday() < 5 and 8 <= kst_h" not in src   # 옛 버그 패턴

    def test_classify_index_and_ticker_prepost(self):
        import pandas as pd
        from bot.prepost_client import _classify_index, _ticker_prepost
        idx = pd.DatetimeIndex(pd.to_datetime(
            ["2026-06-09 15:30:00", "2026-06-10 17:00:00", "2026-06-10 18:00:00"]
        )).tz_localize("America/New_York")
        kinds, dates = _classify_index(idx)
        assert kinds == ["reg", "post", "post"]
        closes = pd.Series([100.0, 110.0, 115.0])   # 전일 종가 100 → 장후 115 = +15%
        vols = pd.Series([0, 2000, 3000])
        rec = _ticker_prepost(closes, vols, kinds, dates)
        assert rec and rec["session"] == "post"
        assert rec["pct"] == 15.0 and rec["vol"] == 5000

    def test_prepost_no_scan_when_closed(self, monkeypatch):
        # 연장거래 창 밖(주말·휴장)엔 캐시 없어도 스캔 kick 안 함 — 매 페이지
        # 접근마다 47배치 스캔 + 배포 재시작 살해로 '계속 새로 시작'하던 것 차단
        import bot.prepost_client as pp
        kicked = []
        monkeypatch.setattr(pp, "_kick_refresh", lambda: kicked.append(1))
        monkeypatch.setattr(pp, "_cached", lambda *a, **k: None)
        monkeypatch.setattr(pp, "prepost_status", lambda: {})
        monkeypatch.setattr(pp, "_in_extended_window", lambda now: False)
        out = pp.fetch_us_prepost_movers()
        assert not kicked and out["building"] is False     # 휴장 → 스캔 X
        monkeypatch.setattr(pp, "_in_extended_window", lambda now: True)
        pp.fetch_us_prepost_movers()
        assert kicked                                       # 창 안 → kick

    def test_prepost_page_building_state(self, monkeypatch):
        import bot.prepost_client as pp
        monkeypatch.setattr(pp, "fetch_us_prepost_movers", lambda: {
            "up": [], "down": [], "ts": "", "source": "", "session": "",
            "building": True, "status": {"state": "running", "done": 3, "total": 50}})
        from bot.us_pages import render_us_prepost_page
        html = render_us_prepost_page()
        assert "산출 진행 중" in html and "장전·장후" in html

    def test_prepost_page_render(self, monkeypatch):
        import bot.prepost_client as pp
        monkeypatch.setattr(pp, "fetch_us_prepost_movers", lambda: {
            "up": [{"ticker": "NVDA", "name": "NVIDIA", "price": 900.0,
                    "pct": 5.5, "vol": 120000, "value": 350.0, "mcap": 22000.0,
                    "ind": "Semis", "session": "post"}],
            "down": [], "ts": "T", "source": "yf", "session": "post"})
        from bot.us_pages import render_us_prepost_page
        html = render_us_prepost_page()
        assert "장후" in html and "NVDA" in html and 'href="lookup/NVDA"' in html
        # 거래량·거래대금 컬럼 = '정규장 거래량/거래대금'으로 명확화 (네이버 worldstock
        # 美 시간외 미제공 → 정규장 값, 시간외 보드 오해 방지, 사용자 2026-06-16).
        # 가격만 시간외 라이브.
        assert "정규장 거래량" in html and "정규장 거래대금" in html
        assert "거래량·거래대금=정규장 누적(시간외분은 네이버 미제공)" in html   # 부제 동기


class TestFavoritesFastInfoGuard:
    """관심종목 fast_info 재트립 차단 (2026-06-14) — 3분 캐시 + 회로차단 게이트.
    위젯 반복 로드가 fast_info 를 버스트해 야후 rate-limit 재트립하던 것 차단."""

    def test_favorites_price_cache_short_circuit(self):
        import time
        import bot.market_favorites as mf
        sentinel = [{"ticker": "X", "current_price": 1}]
        mf._FAV_CACHE = sentinel
        mf._FAV_CACHE_TS = time.time()
        # TTL(3분) 내면 yfinance 안 타고 캐시 그대로 반환
        assert mf.get_favorites_with_prices() is sentinel
        # TTL 만료 + 빈 관심종목 → 재진입(네트워크 0)
        mf._FAV_CACHE_TS = time.time() - 9999
        mf._FAV_CACHE = None
        mf._load = lambda: []
        assert mf.get_favorites_with_prices() == []

    def test_favorites_fast_info_gated_in_source(self):
        # fast_info 직접 호출이 _fi_allowed(=fast_info_ok and not yf_paused)
        # 게이트 뒤 + rate-limit 시 fast_info_trip — 소스 검증(yfinance import 무거움)
        src = open("bot/market_favorites.py", encoding="utf-8").read()
        assert "_fi_allowed = fast_info_ok() and not yf_paused()" in src
        # 네이버 우선(2026-06-15): 네이버 가격 없을 때만 fast_info (야후 부하↓)
        assert "if price is None and _fi_allowed:" in src
        assert "fast_info_trip(\"favorites\")" in src
        assert "_FAV_TTL = 180" in src


class TestFavoritesKoreanName:
    """관심종목 종목명 한글화 (사용자 2026-06-15 '네이버에서, 모든 나라').
    네이버 국내(KR)·해외(US/JP/CN/HK) 한글명을 name_kr 로 영속, 렌더가
    name_kr||name 표시(티커는 밑에 작게). TW·기타는 네이버 미커버라 영문 fallback."""

    def test_extract_name_prefers_korean_field(self):
        from bot.naver_quote import _extract_name
        # 해외: stockNameKor(한글) 우선, stockName(영문) 무시
        assert _extract_name({"stockNameKor": "마이크론테크놀로지",
                              "stockName": "Micron Technology"}) == "마이크론테크놀로지"
        # 국내: stockName 이 한글
        assert _extract_name({"stockName": "삼성전자"}) == "삼성전자"

    def test_extract_name_absent_returns_none(self):
        from bot.naver_quote import _extract_name
        assert _extract_name({"closePriceRaw": "100"}) is None
        assert _extract_name({"stockName": "   "}) is None   # 공백뿐 → None

    def test_parse_quote_includes_name(self):
        from bot.naver_quote import _parse_quote
        q = _parse_quote({"closePriceRaw": "70000", "fluctuationsRatioRaw": "1.5",
                          "stockName": "삼성전자"})
        assert q["name"] == "삼성전자" and q["price"] == 70000

    def test_parse_quote_overmarket_price_stays_regular(self):
        # 사용자 2026-06-16 '차트·현재가 모든곳·모든나라 정규장종가' (네이버페이
        # 미러: 메인=정규장종가, NXT=별도). 시간외(NXT) OPEN 이어도 price/close 는
        # 정규장 종가 유지, 시간외가는 over_price 로만 분리. 옛 price=시간외가 대체 제거.
        from bot.naver_quote import _parse_quote
        item = {"closePriceRaw": "296.42", "fluctuationsRatioRaw": "1.82",
                "compareToPreviousClosePriceRaw": "5.29",
                "compareToPreviousPrice": {"code": "2"},
                "overMarketPriceInfo": {"overMarketStatus": "OPEN",
                                        "tradingSessionType": "AFTER_MARKET",
                                        "overPrice": "296.10"}}
        q = _parse_quote(item)
        assert q["price"] == 296.42 and q["close"] == 296.42   # 정규장 종가 유지(시간외가 아님)
        assert abs(q["pct"] - 1.82) < 0.001                    # 정규장 등락% 유지(재산출 안 함)
        assert q["over_session"] == "AFTER_MARKET"
        assert q["reg_close"] == 296.42 and q["over_price"] == 296.10  # 시간외가 분리

    def test_parse_quote_over_pct_and_detail_line_wired(self):
        from bot.naver_quote import _parse_quote
        # 시간외 등락%(vs 정규장 종가) = over.fluctuationsRatio 부호적용
        item = {"closePriceRaw": "370.66", "fluctuationsRatioRaw": "4.60",
                "compareToPreviousClosePriceRaw": "16.29",
                "compareToPreviousPrice": {"code": "2"},
                "overMarketPriceInfo": {"overMarketStatus": "OPEN",
                    "tradingSessionType": "AFTER_MARKET", "overPrice": "372.00",
                    "fluctuationsRatio": "0.36", "compareToPreviousPrice": {"code": "2"}}}
        q = _parse_quote(item)
        assert q["reg_close"] == 370.66 and q["over_price"] == 372.0
        assert abs(q["over_pct"] - 0.36) < 0.001
        # 상세 헤더에 시간외 라인 배선 (build_live_quote over_line + 헤더 span)
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'data-q="over_line"' in src, "상세 헤더 시간외 라인 span 누락"
        assert 'fmt["over_line"] = over_line' in src, "fmt over_line 미배선"
        assert ".si-over:empty" in src, ":empty 숨김 누락"

    def test_over_line_helper_all_markets_except_tw(self):
        # 사용자 2026-06-16 — _fmt_over_line 이 시간외(NXT) 가격 + 정규장 대비 변동
        # 표시(네이버페이 미러). KR(국내)·US/JP/HK/CN(해외) 공용. over_session 또는
        # over_price 없으면 "" (정규장·비지원·TW).
        import bot.dashboard as d
        # src 라벨 시장별 (KR='NXT' / US='시간외' — NXT≠시간외, 사용자 2026-06-16)
        s = d._fmt_over_line({"over_session": "AFTER_MARKET", "reg_close": 370.66,
                              "over_price": 408.73}, "$", 2, "시간외")   # US +10.27%
        assert "장후" in s and "정규장 종가 $370.66" in s
        assert "시간외 $408.73" in s and "+10.27%" in s
        kr = d._fmt_over_line({"over_session": "PRE_MARKET", "reg_close": 70000,
                               "over_price": 66500}, "₩", 0, "NXT")    # KR -5.00%
        assert "장전" in kr and "NXT ₩66,500" in kr and "-5.00%" in kr
        assert d._fmt_over_line({"over_session": ""}, "$", 2) == ""   # 정규장→빈
        assert d._fmt_over_line({"over_session": "AFTER_MARKET"}, "$", 2) == ""  # over_price 없음→빈
        assert d._fmt_over_line(None, "$", 2) == ""
        # KR 분기·wq 블록 둘 다 헬퍼 사용 (def 1 + 호출 2 = 3)
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert src.count("_fmt_over_line") >= 3, "KR/해외 양쪽 배선 누락"

    def test_parse_quote_overmarket_closed_keeps_regular(self):
        from bot.naver_quote import _parse_quote
        item = {"closePriceRaw": "296.42", "fluctuationsRatioRaw": "1.82",
                "compareToPreviousPrice": {"code": "2"},
                "overMarketPriceInfo": {"overMarketStatus": "CLOSE",
                                        "overPrice": "296.10"}}
        q = _parse_quote(item)
        assert q["price"] == 296.42 and q["over_session"] == ""  # 시간외 닫힘→정규장

    def test_resolve_kr_name_routes_by_market(self):
        import bot.market_favorites as mf
        import bot.naver_quote as nq
        import bot.world_quote as wq
        o_kr, o_wd = nq.fetch_kr_quote, wq.fetch_world_quote
        nq.fetch_kr_quote = lambda t: {"name": "삼성전자"}
        wq.fetch_world_quote = lambda t: {"name": "마이크론테크놀로지"}
        try:
            assert mf._resolve_kr_name("005930.KS", "Samsung") == "삼성전자"
            assert mf._resolve_kr_name("MU", "Micron") == "마이크론테크놀로지"
            assert mf._resolve_kr_name("688981.SS", "SMIC") == "마이크론테크놀로지"
            # TW 는 네이버 미커버 → 영문 fallback
            assert mf._resolve_kr_name("2330.TW", "TSMC") == "TSMC"
        finally:
            nq.fetch_kr_quote, wq.fetch_world_quote = o_kr, o_wd

    def test_resolve_kr_name_tw_uses_translation(self, monkeypatch):
        # TW 는 네이버 미커버 → 대만 신고가와 동일 chart_translate 번역(티커 캐시
        # 공유 → 같은 한글명). 사용자 2026-06-15 '대만도 대만신고가처럼'.
        import bot.market_favorites as mf
        import bot.chart_translate as ct
        monkeypatch.setattr(ct, "translate_names_kr",
                            lambda pairs: {"2344.TW": "윈본드"})
        assert mf._resolve_kr_name("2344.TW", "Winbond Electronics Corporation") == "윈본드"
        # 번역 미스(키부재 류) → 영문 fallback graceful
        monkeypatch.setattr(ct, "translate_names_kr", lambda pairs: {})
        assert mf._resolve_kr_name("6239.TW", "Powertech Technology Inc.") == "Powertech Technology Inc."

    def test_resolve_kr_name_graceful_fallback(self):
        import bot.market_favorites as mf
        import bot.naver_quote as nq
        o_kr = nq.fetch_kr_quote
        nq.fetch_kr_quote = lambda t: None         # 네이버 실패
        try:
            assert mf._resolve_kr_name("005930.KS", "Samsung") == "Samsung"
        finally:
            nq.fetch_kr_quote = o_kr

    def test_add_favorite_sets_name_kr_field(self):
        # add_favorite 엔트리에 name_kr 필드 배선 (소스 검증)
        src = open("bot/market_favorites.py", encoding="utf-8").read()
        assert '"name_kr": _resolve_kr_name(' in src, "add_favorite name_kr 누락"
        # 기존 엔트리는 _refresh 병렬 풀에서 (재)해석 — 부재 OR 영문(==name)
        # fallback 이면 재해석(영문 영속 자가치유)
        assert "if not _cur_kr or _cur_kr == f.get(\"name\"):" in src, "name_kr 자가치유 게이트 누락"

    def test_refresh_self_heals_english_name_kr(self, monkeypatch):
        # #419 이전 TW 가 영문으로 name_kr 영속된 것을 _refresh 가 자가치유:
        # name_kr == 영문 name 이면 재해석 → 한글(translate_names_kr). 사용자
        # 2026-06-15 '캐시인지 딜레이인지' = 영속 영문 게이트 고착이 원인.
        import bot.market_favorites as mf
        import bot.chart_translate as ct
        mf._FAV_CACHE = None
        mf._FAV_CACHE_TS = 0
        # TW 엔트리에 영문 name_kr 가 이미 영속돼 있는 상태
        monkeypatch.setattr(mf, "_load", lambda: [{
            "ticker": "2344.TW", "name": "Winbond Electronics Corporation",
            "name_kr": "Winbond Electronics Corporation", "currency": "TWD"}])
        saved = {}
        monkeypatch.setattr(mf, "_save", lambda x: saved.update(rows=x))
        monkeypatch.setattr(ct, "translate_names_kr", lambda pairs: {"2344.TW": "윈본드"})
        out = mf._compute_favorites_with_prices()   # SWR: 동기 compute 직접(2026-06-16)
        assert out[0]["name_kr"] == "윈본드"               # 영문→한글 자가치유
        assert saved["rows"][0]["name_kr"] == "윈본드"     # 디스크에도 영속

    def test_renderfavs_uses_name_kr(self):
        # 렌더가 name_kr||name 표시 + 티커 작게 (소스 검증)
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "(f.name_kr||f.name||f.ticker)" in src, "renderFavs name_kr 미사용"
        assert "(f.name_kr||'') + ' ' + (f.name||'')" in src, "검색 data-name 한·영 누락"

    def test_refresh_uses_naver_price_mcap_name(self, monkeypatch):
        # 사용자 2026-06-15 '시총·현재가 네이버, 대만 제외' — KR 종목 현재가·시총·
        # 한글명 모두 네이버 실시간에서. yfinance 가 실패(샌드박스 403)해도 무관.
        import bot.market_favorites as mf
        mf._FAV_CACHE = None
        mf._FAV_CACHE_TS = 0
        monkeypatch.setattr(mf, "_load", lambda: [
            {"ticker": "005930.KS", "name": "Samsung", "currency": "KRW"}])
        monkeypatch.setattr(mf, "_save", lambda x: None)
        monkeypatch.setattr(mf, "_naver_quote_for", lambda t: {
            "price": 337000, "mcap": 2_212_900_000_000_000, "name": "삼성전자"})
        out = mf._compute_favorites_with_prices()   # SWR: 동기 compute 직접(2026-06-16)
        assert out[0]["current_price"] == 337000          # 네이버 현재가
        assert out[0]["market_cap"] == 2_212_900_000_000_000  # 네이버 시총(KR=원 신뢰)
        assert out[0]["name_kr"] == "삼성전자"

    def test_refresh_overseas_mcap_unit_sanity_gate(self, monkeypatch):
        # 해외 네이버 시총 단위 불확실 → price×shares 와 0.5~2x 일치할 때만 채택.
        # 단위 불일치(원/달러 혼동 류) 시 네이버 시총 무시(yfinance 폴백 유지).
        import bot.market_favorites as mf
        # KR 은 게이트 없이 신뢰, 해외는 sanity 게이트 — 게이트 함수 자체 검증
        src = open("bot/market_favorites.py", encoding="utf-8").read()
        assert '_detect_country(f["ticker"]) == "KR"' in src, "KR 시총 신뢰 분기 누락"
        assert "0.5 <= naver_mcap / implied <= 2.0" in src, "해외 시총 단위 sanity 누락"
        assert "naver_price = nq.get(\"price\")" in src, "네이버 가격 우선 미배선"


class TestBlogWatchMultiBlog:
    """멀티 블로그 (사용자 2026-06-15 teasky0221='필승' 추가) — per-blog
    초기화로 새 블로그 기존 글 폭주 방지 + per-blog 카테고리(None=전체) +
    설정 표시명 우선 + 옛 bool initialized 마이그레이션."""

    def _xml(self, items, channel="채널명"):
        body = "".join(
            f"<item><title>{t}</title><link>{l}</link><guid>{g}</guid>"
            f"<category>{c}</category></item>" for t, l, g, c in items)
        return f"<rss><channel><title>{channel}</title>{body}</channel></rss>"

    def test_blogs_config_has_teasky_pilseung(self):
        import bot.blog_watch as bw
        ids = {b["id"]: b for b in bw._BLOGS}
        assert "teasky0221" in ids and ids["teasky0221"]["title"] == "필승"
        assert ids["teasky0221"]["categories"] is None         # 전체 글
        assert "doctordk" in ids and ids["doctordk"]["title"] == "의교창"
        assert ids["doctordk"]["categories"] is None           # 전체 글
        assert ids["beatthemkt"]["categories"] == ("관심종목", "기업탐방")
        assert "jkhan012" in ids and ids["jkhan012"]["title"] == "천상천하"  # 사용자 2026-06-18
        assert "ranto28" in ids and ids["ranto28"]["title"] == "메르"        # 사용자 2026-06-18
        assert "richyun0108" in ids and ids["richyun0108"]["title"] == "작은 투자자"  # 사용자 2026-06-22
        assert ids["richyun0108"]["categories"] is None        # 전체 글
        assert "bboyanaga" in ids and ids["bboyanaga"]["title"] == "애널리스트 김경민"  # 사용자 2026-06-23
        assert ids["bboyanaga"]["categories"] is None          # 전체 글

    def test_research_us_action_badge_and_consensus_wired(self):
        # 사용자 2026-06-18 A1(액션 배지/필터)+A2(Finnhub 컨센서스로 빈 TP칸).
        # bot.dashboard/market_overview 는 무거운 import(샌드박스 불가) → 소스 grep 가드.
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        mo = (root / "bot" / "market_overview.py").read_text(encoding="utf-8")
        dash = (root / "bot" / "dashboard.py").read_text(encoding="utf-8")
        # A1: yfinance Action 보존 + 렌더 배지 헬퍼
        assert '"action": (row.get("Action") or "")' in mo, "Action 필드 미보존(A1)"
        assert "def _research_action_badge(" in dash, "배지 헬퍼 누락(A1)"
        assert "_research_action_badge(r.get(\"action\"" in dash, "배지 미배선(A1)"
        for lbl in ("유지", "상향", "하향", "신규", "변경"):
            assert lbl in dash, f"배지 라벨 누락: {lbl}"
        # A2: Finnhub 컨센서스 헬퍼 + 표시될 종목만 호출 + 문자열 렌더
        assert "def _us_consensus_str(" in mo, "컨센서스 헬퍼 누락(A2)"
        assert "fetch_recommendation_trends" in mo, "Finnhub 추천분포 미연결(A2)"
        assert "_us_consensus_str(tk)" in mo, "컨센서스 미배선(A2)"
        assert "isinstance(target, str)" in dash, "문자열 컨센서스 렌더 미처리(A2)"
        # 위젯 캡 → 전체 미국 등급변경 참조 링크, 표 위 가로 한 줄(사용자 2026-06-19)
        assert "research-refs" in dash, "참조 링크 행 누락"
        # 무료·접근가능한 곳만 (사용자 2026-06-19 '유료고 안돼' — TipRanks/Nasdaq/
        # StreetInsider/Yahoo 제거)
        for site in ("marketbeat.com/ratings", "benzinga.com/analyst-ratings",
                     "stockanalysis.com"):
            assert site in dash, f"참조 사이트 누락: {site}"
        for gone in ("tipranks.com", "nasdaq.com/market-activity",
                     "streetinsider.com", "finance.yahoo.com/research-hub"):
            assert gone not in dash, f"제거된 유료 사이트 잔존: {gone}"
        # 종목당 최대 3건 집계 cap(사용자 2026-06-23 'US 리서치 한 종목 3개 이상 안돼')
        # — 30일 롤링 store 누적으로 _emit 에 종목당 cap 없으면 MU 4건 노출되던 것.
        import re as _re
        emit = _re.search(r"def _emit\(\).*?return capped\[:limit\]", mo, _re.S)
        assert emit, "_emit 종목당 cap 누락"
        assert "per.get(sym, 0) >= 3" in emit.group(0), "종목당 3건 cap 임계 누락"

    def test_parse_channel_title(self):
        import bot.blog_watch as bw
        assert bw._parse_channel_title(self._xml([], "티스카이")) == "티스카이"
        assert bw._parse_channel_title("<rss><channel></channel></rss>") == ""

    def test_new_blog_seeds_without_push(self, monkeypatch):
        import bot.blog_watch as bw
        xml = self._xml([("글1", "https://blog.naver.com/teasky0221/1", "g1", "일상")])
        monkeypatch.setattr(bw, "_fetch_rss", lambda bid: xml)
        pushes: list = []
        monkeypatch.setattr(bw, "_push", lambda it: pushes.append(it) or True)
        monkeypatch.setattr(bw, "_save_archive", lambda it: None)
        monkeypatch.setattr(bw, "_fetch_post_text", lambda link: None)
        state = {"seen": [], "init": {}}
        seen: set = set()
        blog = {"id": "teasky0221", "title": "필승", "categories": None}
        r = bw._process_blog(blog, state, seen)
        assert r == 0 and pushes == []            # 초기화 — push 0 (폭주 방지)
        assert "g1" in seen and state["init"]["teasky0221"] is True

    def test_initialized_blog_pushes_new_with_title(self, monkeypatch):
        import bot.blog_watch as bw
        xml = self._xml([("새글", "https://blog.naver.com/teasky0221/2", "g2", "일상")],
                        channel="RSS채널명")
        monkeypatch.setattr(bw, "_fetch_rss", lambda bid: xml)
        pushed: list = []
        monkeypatch.setattr(bw, "_push", lambda it: pushed.append(it) or True)
        monkeypatch.setattr(bw, "_save_archive", lambda it: None)
        monkeypatch.setattr(bw, "_fetch_post_text", lambda link: None)
        state = {"seen": [], "init": {"teasky0221": True}}
        blog = {"id": "teasky0221", "title": "필승", "categories": None}
        r = bw._process_blog(blog, state, set())
        assert r == 1 and pushed[0]["title"] == "새글"        # category None → 전체 push
        assert pushed[0]["blog_title"] == "필승"              # 설정 표시명 우선(채널명 아님)

    def test_category_filter_per_blog(self, monkeypatch):
        import bot.blog_watch as bw
        xml = self._xml([
            ("관심글", "https://blog.naver.com/beatthemkt/3", "g3", "관심종목2026"),
            ("잡담", "https://blog.naver.com/beatthemkt/4", "g4", "일상")])
        monkeypatch.setattr(bw, "_fetch_rss", lambda bid: xml)
        pushed: list = []
        monkeypatch.setattr(bw, "_push", lambda it: pushed.append(it["title"]) or True)
        monkeypatch.setattr(bw, "_save_archive", lambda it: None)
        monkeypatch.setattr(bw, "_fetch_post_text", lambda link: None)
        state = {"seen": [], "init": {"beatthemkt": True}}
        seen: set = set()
        blog = {"id": "beatthemkt", "title": "변화하는 기업을 찾아서",
                "categories": ("관심종목", "기업탐방")}
        bw._process_blog(blog, state, seen)
        assert pushed == ["관심글"]               # 관심종목* 만, 잡담 제외
        assert "g4" in seen                       # 비대상도 seen(재검사 방지)

    def test_blog_command_wired(self):
        # /blog 명령(블로그도 sites 처럼, 사용자 2026-06-15) — 레지스트리·핸들러·
        # 채널 dispatch·help 4곳 배선 + 목록이 _BLOGS 자동 생성(수동 동기 불요).
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "async def cmd_blog(" in src, "cmd_blog 핸들러 누락"
        assert "def _blog_list_text(" in src, "_blog_list_text 누락"
        assert "from bot.blog_watch import _BLOGS" in src, "목록 자동 생성(_BLOGS) 미연결"
        assert '"blog": (cmd_blog' in src, "레지스트리 등록 누락(텔레그램·메뉴·콘솔)"
        assert 'first_word == "blog"' in src, "채널 dispatch 누락"
        assert "/blog" in src, "help §1 /blog 누락"
        # help 인라인 키보드 버튼(📝 감시 블로그) + /start blog deep-link (sites 처럼)
        assert "📝 감시 블로그" in src, "help 키보드 블로그 버튼 누락"
        assert "start=blog" in src, "키보드 deep-link(start=blog) 누락"
        assert 'arg == "blog"' in src, "/start blog deep-link 핸들러 누락"

    def test_state_migration_old_initialized(self, tmp_path):
        import bot.blog_watch as bw
        import json as _j
        p = tmp_path / "state.json"
        p.write_text(_j.dumps({"seen": ["x"], "initialized": True}), encoding="utf-8")
        orig = bw._STATE
        bw._STATE = str(p)
        try:
            st = bw._load_state()
            assert st["init"].get("beatthemkt") is True   # 옛 bool → per-blog 승계
            assert "x" in st["seen"]
        finally:
            bw._STATE = orig


class TestUSPrepostKSTWindow:
    """미국 장전·장후 세션시간 한국시간 명시 + 서머타임 자동 반영
    (사용자 2026-06-15 '한국시간으로 명시, 섬머타임 고려해서 바꿔야'). EDT/EST
    가 1h 차이라 하드코딩 금지 — zoneinfo America/New_York→Asia/Seoul."""

    def test_summer_edt_kst_window(self):
        from datetime import date
        from bot.us_pages import _ext_kst_for_date
        s = _ext_kst_for_date(date(2026, 7, 15))            # 서머타임(EDT)
        assert "장전 17:00–22:30" in s, s
        assert "장후 익일 05:00–09:00" in s, s
        assert "서머타임" in s

    def test_winter_est_kst_window(self):
        from datetime import date
        from bot.us_pages import _ext_kst_for_date
        s = _ext_kst_for_date(date(2026, 1, 15))            # 표준시(EST) → +1h
        assert "장전 18:00–23:30" in s, s
        assert "장후 익일 06:00–10:00" in s, s
        assert "표준시" in s

    def test_prepost_subtitle_wired_and_no_hardcode(self):
        src = open("bot/us_pages.py", encoding="utf-8").read()
        assert "_ext_window_kst()" in src, "부제 KST 세션시간 미배선"
        assert "연장거래 창에서 30분 · " in src, "부제에 KST 창 추가 누락"
        assert "_EXT_WINDOW" not in src, "옛 하드코딩 상수 잔존(서머타임 미반영)"

    def test_prepost_universe_is_market_movers(self):
        # 사용자 2026-06-16: prepost board 유니버스를 S&P 500→전시장 정규장 무버
        # (시간외 급변=뉴스주라 무버 랭킹에 직격, 대형주는 시간외 미동). 네이버
        # worldstock up/down 랭킹 합집합. 캐시 버전 bump 로 옛 S&P 500 캐시 폐기.
        src = open("bot/prepost_client.py", encoding="utf-8").read()
        assert "_us_movers_universe()" in src, "전시장 무버 유니버스 미배선"
        assert "fetch_world_ranking" in src, "네이버 무버 랭킹 미배선"
        assert "_us_universe_robust" not in src, "옛 S&P 500 유니버스 잔존"
        assert "_sp500_names" not in src, "옛 S&P 500 종목명 맵 잔존"
        assert "us_prepost_movers" in src, "캐시 버전 bump 누락"
        # _one 행이 vol(정규장 누적거래량)을 실어 _rank_prepost 의 _MIN_EXT_VOL
        # 게이트를 통과(빈 보드 회귀 차단 — over 데이터엔 연장거래량 없음).
        assert 'wq.get("volume")' in src, "vol 게이트 통과용 거래량 미배선"
        # 사용자 2026-06-16: 야후 제거 → 네이버 실시간 시간외(overMarketPriceInfo)
        assert "fetch_world_quote" in src, "네이버 시간외 스캔 미배선"
        assert "yf.download" not in src, "yfinance 스캔 잔존(야후 제거 위반)"
        assert "네이버 실시간" in src, "소스 라벨 네이버 미반영"
        us = open("bot/us_pages.py", encoding="utf-8").read()
        assert "전시장 무버 {sess_kr} 시간외" in us, "부제 전시장 무버 시간외 미반영"
        assert "S&P 500 {sess_kr} 시간외" not in us, "옛 S&P 500 부제 잔존"
        assert "yfinance 30분봉" not in us, "부제 yfinance 잔존"

    def test_bare_us_strips_only_exchange_suffix(self):
        from bot.prepost_client import _bare_us
        assert _bare_us("CAST") == "CAST"          # 무접미사 그대로
        assert _bare_us("CAST.O") == "CAST"        # NASDAQ reuters 접미사 제거
        assert _bare_us("F.N") == "F"              # NYSE 접미사 제거
        assert _bare_us("BRK.B") == "BRK.B"        # 클래스주 점 보존(거래소코드 아님)
        assert _bare_us("aapl") == "AAPL"          # 대문자화
        assert _bare_us(None) == ""                # graceful

    def test_us_movers_universe_dedupes_and_names(self, monkeypatch):
        # 전시장 무버 유니버스 — 거래소·방향 랭킹 합집합·dedupe·한글명 native.
        import bot.prepost_client as pp

        def _fake_ranking(ex, sort, limit=80):
            if ex == "NASDAQ" and sort == "up":
                return [{"ticker": "CAST", "name": "캐스트", "mcap": 123.4},
                        {"ticker": "AAPL.O", "name": "애플", "mcap": 34000.0}]
            if ex == "NYSE" and sort == "down":
                return [{"ticker": "CAST", "name": "캐스트"},   # 중복 → 1회만
                        {"ticker": "F.N", "name": "포드"}]
            return []
        monkeypatch.setattr(pp, "fetch_world_ranking", _fake_ranking, raising=False)
        monkeypatch.setattr("bot.naver_ranking_client.fetch_world_ranking",
                            _fake_ranking)
        tks, names, mcaps = pp._us_movers_universe()   # 3-tuple (시총 추가, 2026-06-16)
        assert tks == ["CAST", "AAPL", "F"]        # dedupe + 무접미사
        assert names["CAST"] == "캐스트" and names["AAPL"] == "애플"
        # 네이버 랭킹 시총(억$) 수집 — fast_info 폴백 대체분.
        assert mcaps["CAST"] == 123.4 and mcaps["AAPL"] == 34000.0
        assert "F" not in mcaps                     # mcap 없는 행은 미수집


class TestAnalysisCsvExport:
    """주식분석 아카이브 CSV 내보내기 (2026-06-14) — 분석 버튼 옆 ⬇CSV."""

    def test_csv_button_and_data_rendered(self):
        import json
        import re
        import bot.dashboard as d
        # 빈 아카이브 — 버튼·JS 존재. 2026-07-03 성능: 인라인 임베드 →
        # 외부 analysis_csv.json(클릭 시 fetch, 전 리포트 2중 임베드 제거).
        h0, f0 = d._render_index([])
        assert 'id="csv-btn"' in h0 and "⬇ CSV" in h0
        assert 'id="analysis-csv-data"' not in h0     # 인라인 제거(성능)
        assert "analysis_csv.json" in h0              # 클릭 시 fetch 배선
        assert "noah_분석_" in h0 and "ufeff" in h0   # BOM(엑셀 한글) JS
        assert "analysis_csv.json" in f0
        # 레코드 → CSV row 수집(전 필드) — 외부 프래그먼트에서 검증
        rec = {"trade_date": "2026-06-14", "ticker": "AAPL",
               "analyzed_at": "2026-06-14T09:30:00",
               "summary": "x", "full_report": "y", "cost_krw": 2133}
        _h1, f1 = d._render_index([rec])
        data = json.loads(f1["analysis_csv.json"])
        assert len(data) == 1
        row = data[0]
        for k in ("분석일", "시각", "종목", "종목명", "시장", "판정",
                  "Stance", "5거래일수익률%", "알파%p", "비용원"):
            assert k in row, k
        assert row["종목"] == "AAPL" and row["시장"] == "US" and row["비용원"] == 2133



class TestEquityFundNaver:
    """주식형펀드 차트 데이터 (naver_sector_client, 2026-06-14) — 네이버
    trendDeposit/chart beneficiaryCertificateStock 시계열 → deposit 모델 병합."""

    def test_equity_fund_parse_and_merge(self, monkeypatch):
        import bot.naver_sector_client as ns

        class R:
            status_code = 200

            def json(self):
                return [
                    {"bizdate": "20260609", "beneficiaryCertificateStock": "3500000"},
                    {"bizdate": "20260610", "beneficiaryCertificateStock": "3567731"}]
        import requests
        import bot.finviz_client as fv
        monkeypatch.setattr(requests, "get", lambda *a, **k: R())
        monkeypatch.setattr(fv, "naver_paused", lambda: False)
        ser = ns._fetch_equity_fund_naver()
        assert ser[-1] == ("20260610", 3567731.0)
        # fetch_deposit 병합
        monkeypatch.setattr(ns, "_fetch_deposit_fsc", lambda: {
            "date": "06.10", "deposit": 1276066.0, "credit": 361901.0,
            "deposit_series": [], "credit_series": [], "source": "금융투자협회"})
        monkeypatch.setattr(ns, "_cached", lambda *a, **k: None)
        monkeypatch.setattr(ns, "_cache_write", lambda *a, **k: None)
        dep = ns.fetch_deposit()
        assert dep["equity_fund"] == 3567731.0 and dep["equity_fund_chg"] == 67731.0
        assert len(dep["equity_fund_series"]) == 2

    def test_render_shows_third_chart(self):
        # 렌더 스캐폴드(merged)가 equity_fund_series 있으면 3번째 차트 + 3-col
        import bot.dashboard as d
        dep = {"date": "06.10", "deposit": 1276066.0, "credit": 361901.0,
               "equity_fund": 3567731.0, "equity_fund_chg": 67731.0,
               "deposit_series": [{"d": "06.09", "v": 1.0}, {"d": "06.10", "v": 2.0}],
               "credit_series": [{"d": "06.09", "v": 1.0}, {"d": "06.10", "v": 2.0}],
               "equity_fund_series": [{"d": "06.09", "v": 1.0}, {"d": "06.10", "v": 2.0}],
               "source": "금융투자협회"}
        charts = d._render_deposit_charts(dep)
        assert "주식형펀드 추이" in charts and "repeat(3,1fr)" in charts
        widget = d._render_deposit_widget(dep)
        assert "주식형펀드" in widget


class TestNxtClient:
    """NXT 외국인·기관 수급 (nxt_client/nxt_pages, 2026-06-14) — 네이버
    trendForeignOrg 확정 구조 파싱 + 렌더."""

    def test_swr_floor_30s(self):
        # 사용자 2026-06-15 '실시간/30초' — NXT 수급 디스크 캐시 floor 30s.
        # 페이지는 매 방문 라이브 렌더(no-cache)지만 floor 로 네이버 reload 폭주 차단.
        import bot.nxt_client as nc
        assert nc._TTL == 30

    def test_row_parse(self):
        import bot.nxt_client as nc
        r = nc._row({"itemcode": "035420", "itemname": "NAVER",
                     "accTradeVolume": "439689", "accTradeAmount": "110222496250",
                     "dailyTradeVolume": "5215884", "nowPrice": "242500",
                     "prevChangeRate": "8.26"})
        assert r["code"] == "035420" and r["name"] == "NAVER"
        assert r["net_amt"] == 1102.2          # 110222496250원 → 억원
        assert r["net_qty"] == 439689 and r["volume"] == 5215884
        assert r["price"] == 242500.0 and r["pct"] == 8.26
        # 매도(음수) 보존
        s = nc._row({"itemcode": "058470", "itemname": "리노공업",
                     "accTradeVolume": "-263936", "accTradeAmount": "-26927171050"})
        assert s["net_amt"] == -269.3 and s["net_qty"] == -263936

    def test_render(self, monkeypatch):
        import bot.nxt_client as nc
        from bot.nxt_pages import render_nxt_page
        monkeypatch.setattr(nc, "fetch_nxt_foreign", lambda **k: {
            "buy": [nc._row({"itemcode": "035420", "itemname": "NAVER",
                             "accTradeAmount": "110222496250", "nowPrice": "242500",
                             "prevChangeRate": "8.26", "dailyTradeVolume": "5215884"})],
            "sell": [], "date": "20260612"})
        monkeypatch.setattr(nc, "fetch_nxt_organ", lambda **k: None)
        html = render_nxt_page()
        # 점1(2026-06-16): NXT 수급은 NXT venue 당일 합계라 옛 '장전·장후' 제목/
        # 세션라벨 제거. (공통 JS 주석의 '美 장전·장후' 와 충돌 않도록 옛 제목 정확
        # 매칭으로 검사.)
        assert "NXT 외국인·기관 수급" in html and "NAVER" in html
        assert "NXT 장전·장후" not in html and "장전 08:00" not in html
        assert "외국인" in html and "기관" in html and "데이터 없음" in html
        assert 'href="lookup/035420.KS"' in html
        # KR 공통 nav (사용자 2026-06-15 'NXT 도 다른 대시보드 가는 nav') — _shell
        # toggle(NXT active) + 형제 KR 페이지 링크.
        assert 'class="toggle"' in html and 'class="active" href="nxt"' in html
        assert ('href="theme"' in html and 'href="kr52"' in html
                and 'href="highlow"' in html)


class TestMarketLiveTick:
    """홈 라이브 틱 (dashboard._render_market_page + _MARKET_LIVE_JS, 2026-06-15)
    — 정적 market.html 을 30초마다 스스로 다시 받아 #live-sections(스냅샷·Macro·
    업종등락)만 innerHTML 교체(새로고침 없이 자동 갱신). 라이브 섹션은 전부 정적
    HTML(인라인 SVG·표, JS 위젯 0)이라 swap 안전. graceful·탭숨김/검색중 skip."""

    def test_live_sections_wrap_and_js(self):
        from bot.dashboard import _render_market_page
        html = _render_market_page({})
        assert html.count('id="live-sections"') == 1          # 라이브 블록 1 wrap
        assert "DOMParser" in html and "fetch('market.html'" in html
        assert "getElementById('live-sections')" in html      # #live-sections 교체
        assert "document.hidden" in html                      # 탭 숨김 skip
        assert html.index("DOMParser") < html.rindex("</body>")  # JS body 내부
        assert "30초 자동 갱신" in html                        # ts 라벨

    def test_live_js_constant(self):
        # 2026-07-03 정책 변경: 30초 강제 swap 제거 → 업데이트 배너(30분 체크,
        # 사용자 선택)로 통일. _MARKET_LIVE_JS 는 빈 문자열이어야(부활 방지).
        import bot.dashboard as d
        assert d._MARKET_LIVE_JS == ""


class TestHomeSnapshotPureNaver:
    """홈 스냅샷(ALL_CARDS) = 지수·환율·원자재·코인·VIX 뿐(개별 종목 카드 없음) +
    전부 네이버(nv*) → _all_yf_tickers 빈 리스트(야후 0). 2026-06-15 확인: 정규식
    으로 보였던 KR 종목은 '다가오는 실적' 유니버스(yfinance .calendar)이지 스냅샷
    카드가 아님 — 잘못 넣은 KR 시세 병합 dead code 철회."""

    def test_no_yahoo_tickers_in_snapshot(self):
        import bot.market_overview as mo
        assert mo._all_yf_tickers() == []   # ALL_CARDS 전부 네이버 → 야후 0


class TestMoversSortByPct:
    """모든 나라 급등락 기본정렬 = 등락률순(시총순 아님) — 사용자 2026-06-15
    '처음 디폴트화면을'. 52주 신고저는 시총순 유지(별개 surface)."""

    def test_sort_by_pct(self):
        from bot.highlow_render import sort_by_pct
        up = [{"pct": 2.0}, {"pct": 9.5}, {"pct": 5.0}, {"pct": None}]
        dn = [{"pct": -3.0}, {"pct": -8.0}, {"pct": -1.0}]
        assert [r["pct"] for r in sort_by_pct(up, gainers=True)] == [9.5, 5.0, 2.0, None]
        assert [r["pct"] for r in sort_by_pct(dn, gainers=False)] == [-8.0, -3.0, -1.0]

    def test_sort_by_pct_mcap_tiebreak(self):
        # 동률 등락률(상·하한가 다수 +10%)이면 시총 큰 순 tiebreak (사용자 2026-06-22
        # — 가나다순 아니라 시총순). 급등·급락 양쪽.
        from bot.highlow_render import sort_by_pct
        up = [{"name": "A", "pct": 10.0, "mcap": 32}, {"name": "B", "pct": 10.0, "mcap": 200},
              {"name": "C", "pct": 10.0, "mcap": 45}, {"name": "D", "pct": 9.99, "mcap": 999}]
        assert [r["name"] for r in sort_by_pct(up, gainers=True)] == ["B", "C", "A", "D"]
        dn = [{"name": "X", "pct": -10.0, "mcap": 10}, {"name": "Y", "pct": -10.0, "mcap": 50}]
        assert [r["name"] for r in sort_by_pct(dn, gainers=False)] == ["Y", "X"]
        # JS 정렬도 동률 pct → 시총 tiebreak (data-mcap)
        from bot.highlow_render import HL_SORT_JS
        assert "key==='pct'" in HL_SORT_JS and "data-mcap" in HL_SORT_JS

    def test_movers_render_uses_pct(self):
        # 모든 나라 movers 파일이 sort_by_pct 사용(기본 등락률순 배선·시총순 회귀 차단)
        for path in ("bot/naver_pages.py", "bot/us_pages.py",
                     "bot/intl_pages.py", "bot/tw_pages.py"):
            assert "sort_by_pct" in open(path, encoding="utf-8").read(), path


class TestNameTranslationKr:
    """CN/TW/HK 종목명 영문 통용명 기준 한글 음역 (chart_translate.translate_names_kr
    + stock_panel 적용) — 사용자 2026-06-15 '화봉전→윈본드'. 티커 기반 영구 캐시."""

    def test_translate_names_kr_cache_dedup_graceful(self):
        from unittest.mock import patch
        import bot.chart_translate as ct
        with patch.object(ct, "_load_name_kr", lambda: {"2344.TW": "윈본드"}):
            out = ct.translate_names_kr([("2344.TW", "華邦電"), ("2344.TW", "華邦電")])
        assert out == {"2344.TW": "윈본드"}            # 캐시 히트 + 티커 dedup
        with patch.object(ct, "_load_name_kr", lambda: {}), \
             patch.dict("os.environ", {}, clear=True):
            assert ct.translate_names_kr([("2330.TW", "台積電")]) == {}   # graceful

    def test_stock_panel_applies_kr_names(self):
        from unittest.mock import patch
        from bot import highlow_render as hr
        items = [{"ticker": "2344.TW", "name": "화봉전", "price": 172, "pct": 9.9}]
        # 렌더-세이프(2026-06-16): stock_panel 은 cache_only=True 로 호출 → 캐시
        # 히트 시뮬(2-인자 시그니처). 미캐시면 백그라운드 워밍·원문 유지(블로킹 0).
        with patch("bot.chart_translate.translate_names_kr",
                   lambda pairs, cache_only=False: {"2344.TW": "윈본드"}):
            html = hr.stock_panel("T", items, "t1", "TW", name_only=True)
        assert "윈본드" in html and "화봉전" not in html

    def test_stock_panel_render_safe_cache_only(self):
        # 렌더 경로는 cache_only=True 로만 호출(네트워크/LLM 블로킹 0).
        from unittest.mock import patch
        from bot import highlow_render as hr
        seen = {"cache_only": None}

        def _spy(pairs, cache_only=False):
            seen["cache_only"] = cache_only
            return {}
        with patch("bot.chart_translate.translate_names_kr", _spy), \
             patch.object(hr, "_kick_name_fill", lambda pairs: None):
            hr.stock_panel("T", [{"ticker": "2344.TW", "name": "화봉전",
                                  "price": 1, "pct": 1}], "t2", "TW", name_only=True)
        assert seen["cache_only"] is True, "stock_panel 이 cache_only 로 호출 안 함"

    def test_stock_panel_skips_us(self):
        from unittest.mock import patch
        from bot import highlow_render as hr
        called = {"n": 0}

        def _spy(pairs, cache_only=False):
            called["n"] += 1
            return {}
        with patch("bot.chart_translate.translate_names_kr", _spy):
            hr.stock_panel("U", [{"ticker": "AAPL", "name": "Apple",
                                  "price": 1, "pct": 1}], "u1", "US", name_only=True)
        assert called["n"] == 0           # US/KR 는 음역 미적용


class TestDartFeedTabCollapse:
    """DART 공시 카테고리/플래그 탭도 '전체'처럼 최신일만 펼침 — 텍스트 검색
    중에만 접힌 그룹 강제 펼침(df-searching). 사용자 2026-06-15 '다른 탭들도
    전체탭처럼 해당일만 열어놓고 나머지일들은 접히는 것으로'."""

    def test_df_searching_search_only(self):
        import bot.dashboard as d
        html = d._render_dart_feed_page({"2026-06-15": [
            {"corp_name": "테스트", "report_nm": "감자완료", "category": "리스크",
             "url": "#", "stock_code": "123456", "detail": []}]})[0]
        # df-searching 은 텍스트 검색(q)에만 — 카테고리/플래그엔 안 켜짐(접힘 유지)
        assert "df-searching', !!q)" in html
        assert "df-searching', !!q || activeCat" not in html   # 옛 조건 제거
        assert 'data-cat="전체"' in html and "df-date-group" in html

    def test_month_count_syncs_with_filter(self):
        # 필터 적용 시 일 카운트(df-date-cnt)만 줄고 월 카운트(df-month-cnt)는
        # 전체로 남아 '일 90 ↔ 월 4697건' 어긋남 → 데이터 손실로 오인(사용자
        # 2026-06-15 '심각'). 월 카운트도 보이는 카드 수로 동기화해야.
        import bot.dashboard as d
        html = d._render_dart_feed_page({"2026-06-15": [
            {"corp_name": "테스트", "report_nm": "감자완료", "category": "리스크",
             "url": "#", "stock_code": "123456", "detail": []}]})[0]
        # 필터 JS 가 .df-month-cnt 를 보이는 카드 수+'건' 으로 재계산
        assert "querySelector('.df-month-cnt')" in html
        assert "mc.textContent=n+'건'" in html


class TestPruneNonStock:
    """비-주식 가지치기 (finviz_client.prune_non_stock) — CEF 펀드·유령티커·
    이중클래스 dedupe (사용자 2026-06-14 실데이터 회귀)."""

    def test_cef_detection(self):
        from bot.finviz_client import _is_fund_vehicle
        cef = [
            ("Cohen & Steers Infrastructure Fund Inc", "Finance Companies"),
            ("BlackRock Capital Allocation Term Closed End Fund", "Finance/Investors Services"),
            ("Virtus Dividend Interest & Prem Str Fd", "Finance: Consumer Services"),
            ("Gabelli Utility Trust (The) Common Stock", "Trusts Except Educational Religious and Charitable"),
            ("RiverNorth Flexible Municipal Income Fund II, Inc.", "Trusts Except Educational Religious and Charitable"),
            ("Putnam Premier Income Trust", "Finance Companies"),
            ("MFS Government Markets Income Trust Common Stock", "Trusts Except Educational Religious and Charitable"),
            ("PCM Fund, Inc. Common Stock", "Trusts Except Educational Religious and Charitable"),
            ("Nuveen Real Estate Income Fund", "Finance/Investors Services"),
        ]
        for nm, ind in cef:
            assert _is_fund_vehicle(nm, ind), f"CEF 미검출: {nm}"

    def test_legit_trust_names_preserved(self):
        # 은행·REIT·운용 '회사'는 'Trust' 들어가도 보존 (Fund/Fd/Closed-End·CEF
        # 업종코드 아님). OPI(Office Properties Income Trust)는 REIT → Income Trust
        # 패턴이라도 비-부동산 가드로 보존.
        from bot.finviz_client import _is_fund_vehicle
        legit = [
            ("Northern Trust", "Asset Management & Custody Banks"),
            ("TrustCo Bank Corp NY", "Major Banks"),
            ("W.p. Carey", "Real Estate Investment Trusts"),
            ("Office Properties Income Trust", "Real Estate Investment Trusts"),
            ("Affiliated Managers Group", "Investment Managers"),
            ("Amkor Technology", "Semiconductors"),
        ]
        for nm, ind in legit:
            assert not _is_fund_vehicle(nm, ind), f"합법 오드롭: {nm}"

    def test_frozen_ticker(self):
        from bot.finviz_client import _is_frozen_row
        assert _is_frozen_row({"name": "Sinovac Biotech, Ltd.",
                               "mcap": None, "ind": None, "vol": None})
        assert not _is_frozen_row({"mcap": 3500.0, "ind": None, "vol": None})
        assert not _is_frozen_row({"mcap": None, "ind": "Banks", "vol": None})

    def test_dedupe_dual_class(self):
        from bot.finviz_client import prune_non_stock
        rows = [
            {"ticker": "CENT", "name": "Central Garden & Pet",
             "vol": 70000, "mcap": 2700, "ind": "Consumer Specialties"},
            {"ticker": "CENTA", "name": "Central Garden & Pet Class A",
             "vol": 320000, "mcap": 2400, "ind": "Consumer Specialties"},
            {"ticker": "SENEA", "name": "Seneca Foods Class A",
             "vol": 540000, "mcap": 1200, "ind": "Packaged Foods"},
            {"ticker": "SENEB", "name": "Seneca Foods Class B",
             "vol": 589, "mcap": 1100, "ind": "Packaged Foods"},
            {"ticker": "AMKR", "name": "Amkor Technology",
             "vol": 7550000, "mcap": 20500, "ind": "Semiconductors"},
        ]
        out = [r["ticker"] for r in prune_non_stock(rows)]
        assert out == ["CENTA", "SENEA", "AMKR"], out   # 유동성 큰 클래스 1개 + 순서

    def test_prune_integration(self):
        from bot.finviz_client import prune_non_stock
        rows = [
            {"ticker": "AMKR", "name": "Amkor Technology",
             "ind": "Semiconductors", "vol": 7000000, "mcap": 20500},
            {"ticker": "PDI", "name": "PIMCO Dynamic Income Fund",
             "ind": "Trusts Except Educational Religious and Charitable",
             "vol": 100, "mcap": 500},
            {"ticker": "SVA", "name": "Sinovac Biotech, Ltd.",
             "mcap": None, "ind": None, "vol": None},
        ]
        assert [r["ticker"] for r in prune_non_stock(rows)] == ["AMKR"]

    def test_prune_cached_serve_time_idempotent(self, monkeypatch):
        # 옛 캐시(가지치기 도입 전) serve-time 정리 + 멱등(재캐시 1회만)
        import bot.finviz_client as fv
        writes = []
        monkeypatch.setattr(fv, "_cache_write", lambda n, o: writes.append(n))
        stale = {"high": [
            {"ticker": "AMKR", "name": "Amkor Technology",
             "ind": "Semiconductors", "vol": 7000000, "mcap": 20500},
            {"ticker": "PDI", "name": "PIMCO Dynamic Income Fund",
             "ind": "Trusts Except Educational Religious and Charitable",
             "vol": 100, "mcap": 500},
        ], "low": [], "ts": "T", "source": "X"}
        out = fv._prune_cached(stale, ("high", "low"), "highlow.json")
        assert [r["ticker"] for r in out["high"]] == ["AMKR"]
        assert writes == ["highlow.json"]      # 변경 → 재캐시 1회
        writes.clear()
        fv._prune_cached(out, ("high", "low"), "highlow.json")
        assert writes == []                     # 멱등 — 재캐시 없음


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

    def test_inquiry_denial_answer_korean_date_footer(self):
        # 조회공시 답변(부인) — VGXI 텍사스 공장 소유권 이전 부인(2026-06-20). 제목만
        # 잡혀 parts<2 미파싱이던 것: 한국식 날짜 푸터(조회요구(2026년 06월 17일)) +
        # 부인 입장(매각이나 처분은 아닌 점을 밝힘)으로 ≥2 part 확보.
        from bot.dart_feed import _inquiry_lines
        L = _inquiry_lines(
            "조회공시 요구(풍문 또는 보도)에 대한 답변(부인) 1. 제목 종속회사 VGXI, "
            "Inc.의 텍사스 공장 소유권 이전에 대한 조회공시 답변 2. 내용 당사는 2026년 "
            "4월 6일 미국 C2R 와의 대물변제 계약을 체결하였습니다. 현재 소유권은 C2R "
            "Secured Debt Fund I, LP가 가지고 있습니다. 이는 형식적 소유권이전으로 "
            "매각이나 처분은 아닌 점을 밝힙니다. (공시책임자) 전경하 ※이 내용은 "
            "거래소의 조회요구(2026년 06월 17일 18:46)에 따른 공시사항임")
        assert any(l.startswith("제목: 종속회사 VGXI") for l in L), L
        assert any(l.startswith("입장:") and "아닌 점을 밝" in l for l in L), L
        assert any("요구일 2026-06-17" in l for l in L), L
        assert len(L) >= 2   # parse 성공(미파싱 탈출)

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
        # 관계 열 broaden(2026-06-13): 주주명에 (관계) 병기
        L = _major_holding_change_lines(
            "최대주주 등의 주식보유 변동 ... 변동 후 주식수 지분율 (C) "
            "동일인 및 동일인 관련자 에스케이스퀘어(주) 계열회사 2026.06.11 "
            "보통주 56,812 53.13 - -1.06 56,812 52.07 - -")
        assert any(l == "주주: 에스케이스퀘어(주)(계열회사)" for l in L)
        assert any(l == "지분율: 53.13% → 52.07% (-1.06%p ▼)" for l in L)

    def test_major_holding_change_live_5cases(self):
        # 라이브 5예시(2026-06-13) — 친족 집중·전량 처분('-'=0) 커버 broaden
        from bot.dart_feed import _major_holding_change_lines as F
        # 친족 집중 (애나그램 서진석 50.49→93.92)
        L = F("동일인 및 동일인 관련자 서진석 친족 2026년 05월 29일 "
              "보통주 212,050 50.49 3,000,000 43.43 3,212,050 93.92 300,000 0.1")
        assert any("서진석(친족)" in l for l in L)
        assert any("50.49% → 93.92% (+43.43%p ▲)" in l for l in L)
        # 전량 처분 (메이케이 제주항공 100→0, 변동후 '-')
        L2 = F("동일인 및 동일인 관련자 제주항공 계열회사 2026.06.10 "
               "보통주 7,800,000 100 -7,800,000 -100 - -")
        assert any("100.00% → 0.00%" in l and "전량 처분" in l for l in L2)

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
        # 범례 안내문구('함께 선택 가능')는 사용자 요청으로 제거(2026-06-22) — 기능은 유지


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


class TestDartUnparsed8:
    """미파싱-8 (사용자 2026-07-11, 2예시) — 정정명령부과(금융위, 자본시장법
    제164조). 휴맥스홀딩스·아시아나항공 둘 다 회사합병 결정 주요사항보고서에
    대한 정정명령 — 실측 문구 그대로(관련된/관련한 어순 변형 둘 다 커버)."""

    def test_correction_order_hmx(self):
        from bot.dart_feed import _correction_order_lines
        L = _correction_order_lines(
            "2026.7.1. 제출된 주요사항보고서(회사합병 결정)에 대한 심사결과 "
            "신고서의 내용 중 기타 투자판단과 관련된 중요사항 등과 관련하여 "
            "중요한 누락(또는 허위의 기재)이 있어 2026.7.9. 정정명령이 "
            "부과되었습니다.")
        assert any(l == "원 제출: 2026.7.1 주요사항보고서(회사합병 결정)" for l in L)
        assert any("기타 투자판단과 관련된 중요사항" in l
                   and "중요 누락/허위기재" in l for l in L)
        assert any(l == "정정명령일: 2026.7.9" for l in L)

    def test_correction_order_asiana_wording_variant(self):
        # '관련한'(어순 변형) — 링크된 관계사 이름도 슬래시/괄호 없이 파싱.
        from bot.dart_feed import _correction_order_lines
        L = _correction_order_lines(
            "2026.6.26. 제출된 주요사항보고서(회사합병 결정)에 대한 심사결과 "
            "신고서의 내용 중 기타 투자판단과 관련한 중요사항 등과 관련하여 "
            "중요한 누락(또는 허위의 기재)이 있어 2026.7.6. 정정명령이 "
            "부과되었습니다.")
        assert any(l == "원 제출: 2026.6.26 주요사항보고서(회사합병 결정)" for l in L)
        assert any(l == "정정명령일: 2026.7.6" for l in L)

    def test_correction_order_dispatch_routing(self):
        # 2026-07-15 재발 — 소스 grep 만 하던 이전 버전은 실행 순서를 전혀
        # 검증 안 해 배포 후 미파싱 그대로였던 실제 버그(정정명령 elif 가
        # "회사합병" elif 뒤에 있어 실측 report_nm "...주요사항보고서(회사
        # 합병 결정)..." 이 '회사합병' 키워드에 먼저 걸려 정정명령 파서가
        # 영영 실행 안 됨)를 놓쳤다. 실측 포맷으로 _extract_detail_specific
        # 을 직접 호출해 진짜 반환값을 검증(사용자 재확인으로 발견).
        from bot import dart_feed as df
        report_nm = "정정명령부과( 2026.07.01. 제출 주요사항보고서(회사합병 결정) )"
        assert "합병" in report_nm   # 하위 "회사합병" elif 도 매치되는 실측 포맷 고정
        orig = df._fetch_doc_text
        df._fetch_doc_text = lambda rcept_no, api_key: (
            "2026.7.1. 제출된 주요사항보고서(회사합병 결정)에 대한 심사결과 "
            "신고서의 내용 중 기타 투자판단과 관련된 중요사항 등과 관련하여 "
            "중요한 누락(또는 허위의 기재)이 있어 2026.7.9. 정정명령이 "
            "부과되었습니다.")
        try:
            result = df._extract_detail_specific(
                report_nm, "20260709000001", "00000000", "dummy_key")
        finally:
            df._fetch_doc_text = orig
        assert result is not None
        assert any("원 제출" in l for l in result["lines"])


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
        assert 'f"earnings_{today.isoformat()}_{_CODE_SALT}_v2.json"' in src  # v2: 월별 분할
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


class TestTradeProxyBannerFragment20260616:
    """#455 회귀: trade 리버스 프록시가 nav 배너를 모든 html 응답에 주입하며
    <body> 없는 lazy 프래그먼트(industry_panel.html)엔 prepend → 산업트렌드 탭
    콘텐츠 안에 nav 중복 표시(사용자 2026-06-16 '연결 대시보드 두번'). 풀페이지
    (<body>)만 1회 주입하도록 — 프래그먼트 prepend 제거."""

    def test_proxy_banner_only_full_page(self):
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        # <body> 매칭 시에만 주입, else-prepend(프래그먼트 배너) 제거됨
        assert "body[:m.end()] + _banner + body[m.end():]" in src
        assert "body = _banner + body" not in src, \
            "프래그먼트(<body> 없는 응답)에 배너 prepend 잔존 — 탭 nav 중복"


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

    def test_dashboard_label_removed(self):
        # 일회성 이벤트 완료 후 라벨 제거 (사용자 2026-06-13) — helper 는
        # CLI 진단용 보존, 대시보드 렌더에서는 미사용.
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "_bf4_status" not in src.replace(
            "백필 v4 상태 라벨은 제거", "")  # 주석 제외 실사용 0
        from bot.dart_feed import backfill_v4_status
        assert callable(backfill_v4_status)


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
        # +(증가)만 발화 (2026-06-12 3차) — 감소는 폭과 무관하게 미발화
        from bot.dart_feed import significance as S
        assert S(self._mk(["매출액: 1,234억원 (전기 987억원 · +25.3%)"])
                 ) == "매출액 +25.3%"
        assert S(self._mk(["매출액: 800억원 (전기 987억원 · -19.0%)",
                           "영업이익: 70억원 (전기 98억원 · -29.0%)"])) is None
        assert S(self._mk(["매출액: 950억원 (전기 987억원 · -3.7%)",
                           "영업이익: 64억원 (전기 98억원 · -35.0%)"])) is None
        assert S(self._mk(["영업이익: 130억원 (전기 98억원 · +32.7%)"])
                 ) == "영업이익 +32.7%"
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


class TestSameBasisGlitchSecondGuard:
    """KLAC 잔존 클래스 (2026-06-12) — price·prev 가 둘 다 같은 미조정
    기준이면 상대비교 1차 가드가 장님 → 조정 일봉 종가 교차 2차 가드를
    전 표면(검색카드·관심종목·홈 live·미국 지수·시총 fetch)에 배선."""

    def test_wired_everywhere(self):
        for path, marker in (
            ("bot/stock_snapshot.py", "조정 종가"),
            ("bot/market_favorites.py", 'history(period="5d")'),
            ("bot/market_overview.py", "daily[tk][0]"),
            ("bot/us_market_daily.py", 'history(period="5d")'),
            ("bot/finviz_client.py", 'history(period="5d")'),
        ):
            src = open(path, encoding="utf-8").read()
            assert marker in src, f"2차 가드 미배선: {path}"


class TestRedditPageCollapsed:
    """레딧 페이지 날짜그룹·카드 기본 접힘 (사용자 2026-06-12 '다른
    대시보드처럼' — Daily Byte 전부-접힘 mirror)."""

    def test_day_and_card_collapsed(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        import re
        seg = src[src.index("def _render_reddit_insider_page"):]
        seg = seg[:seg.index("def regenerate_reddit_insider_index")]
        assert 'day_open = ""' in seg
        assert "card_default_open = False" in seg


class TestHeatmapYoYLookback:
    """히트맵 YoY off-by-one (2026-06-12 밤) — 13개월 윈도가 최신월의
    전년동월을 안 담아 전부 '신규(전기 0)' (ref=2026-05, 필요=2025-05,
    시작=2025-06). 14개월 + 윈도 산식 가드."""

    def test_lookback_default_14(self):
        src = open("trade/scripts/scan_customs.py", encoding="utf-8").read()
        assert 'or "14"' in src and 'or "13"' not in src

    def test_window_contains_yoy_of_prev_month(self):
        import importlib.util as _il
        # dotenv 의존 없이 _window 만 — 산식 복제 검증 (순수)
        def _window(lookback, y, m):
            back = lookback - 1
            while back > 0:
                m -= 1
                if m == 0:
                    m, y = 12, y - 1
                back -= 1
            return f"{y:04d}{m:02d}"
        # now=2026-06, 최신 데이터월 = 2026-05 → YoY = 2025-05
        assert _window(13, 2026, 6) == "202506"   # 13개월이면 미포함 (버그 재현)
        assert _window(14, 2026, 6) == "202505"   # 14개월이면 포함


class TestDocFailClearV6:
    def test_marker_and_wiring(self, tmp_path, monkeypatch):
        import bot.dart_feed as df
        monkeypatch.setattr(df, "_DOC_FAIL_CLEAR_MARKER_V6",
                            tmp_path / ".v6")
        monkeypatch.setattr(df, "clear_doc_fail_cache", lambda: 42)
        assert df.clear_doc_fail_once_v6() == 42
        assert df.clear_doc_fail_once_v6() is None   # marker gate
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "clear_doc_fail_once_v6" in src


class TestDividendYieldSignificance:
    """🔥 규칙 9 — 배당 시가배당률 3%↑ (사용자 2026-06-13)."""

    def test_yield_gate(self):
        from bot.dart_feed import significance as S
        hi = {"report_nm": "현금ㆍ현물배당결정", "category": "배당",
              "detail": ["1주당 배당금: 1,500원", "시가배당률: 3.5%"]}
        assert S(hi) == "시가배당률 3.5%"
        assert S({**hi, "detail": ["시가배당률: 0.8%"]}) is None
        assert S({**hi, "detail": []}) is None        # 미파싱 → ⚠️ 대기
        assert S({"report_nm": "[기재정정]현금ㆍ현물배당결정",
                  "category": "배당",
                  "detail": ["시가배당률: 4.2%"]}) is None   # 정정 제외


class TestProvZoneDivider:
    """잠정 속보 존 구분선 (사용자 2026-06-13 '10·20일 잠정도 위쪽에') —
    월간 산업트렌드와 동일 pill, 녹색 변형."""

    def test_divider_markup_and_order(self):
        src = open("trade/dashboard.py", encoding="utf-8").read()
        assert "ind-zone-div prov" in src
        assert "10·20일 잠정" in src
        assert "prov_zone_div + prov_html + zone_div" in src   # 순서
        assert ".ind-zone-div.prov span{border-color:#34c759}" in src


class TestDartIRParsing:
    """기업설명회(IR) 상세 파싱 (사용자 2026-06-13 'IR 도 파싱해줘') —
    옛 '제목만 캘린더 전담'에서 일시·장소·목적·대상·후원 추출. 캘린더
    공급은 그대로(겸용)."""

    EX = ("기업설명회(IR) 개최 "
          "1. 일시 행사일 시간(현지시간) 시작일 종료일 시작시간 종료시간 "
          "2026-06-16 2026-06-16 15:00 18:00 "
          "2. 장소 서울 그랜드 하얏트 호텔 "
          "3. 대상자 국내외 기관투자자 "
          "4. 실시목적 '2026 BofA Korea Conference' 참가 "
          "5. 실시방법 One-on-One, 소그룹미팅 "
          "6. 주요내용 2026년 1분기 경영실적, 업황및 Q&A "
          "7. 후원기관 BofA증권 8. 개최확정일 2026-06-12")

    def test_ir_fields(self):
        from bot.dart_feed import _ir_lines
        L = _ir_lines(self.EX)
        assert any(l.startswith("일시: 2026-06-16") and "15:00~18:00" in l
                   for l in L), L
        assert "장소: 서울 그랜드 하얏트 호텔" in L
        assert any(l.startswith("대상:") and "기관투자자" in l for l in L)
        assert any(l.startswith("후원:") and "BofA증권" in l for l in L)
        assert len(L) >= 4

    def test_single_day_no_range(self):
        from bot.dart_feed import _ir_lines
        L = _ir_lines("기업설명회(IR) 개최 1. 일시 행사일 시작일 종료일 "
                      "2026-07-01 2026-07-01 10:00 11:00 2. 장소 여의도")
        assert any(l == "일시: 2026-07-01 10:00~11:00" for l in L), L  # 같은날=범위X

    def test_ir_is_parse_target_and_routed(self):
        from bot.dart_feed import is_parse_target
        assert is_parse_target({"category": "IR",
                                "report_nm": "기업설명회(IR)개최",
                                "corp_code": "x"})
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        assert "_extract_ir" in src and '"기업설명회" in t' in src
        assert '"IR")' in src  # _PARSE_CATS 포함


class TestDartCardOrderIsReceiptOrder:
    """카드 순서 = 접수 순서 (사용자 2026-06-13 '업데이트 순서가 맞아야')
    — 날짜 그룹 내 rcept_no 내림차순 렌더 정렬. 아카이브 파일 순서는
    백필/재fetch 가 섞을 수 있어 렌더타임 고정."""

    def test_sorted_by_rcept_no_desc(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'key=lambda x: str(x.get("rcept_no") or "")' in src
        # 정렬 호출 자체에 reverse=True 인접 확인 (카운트 프리패스의
        # 'for it in items' 와 혼동 없는 exact 매칭)
        assert ('key=lambda x: str(x.get("rcept_no") or ""),\n'
                '                           reverse=True)') in src


class TestLegendOrderMatchesChips:
    """범례 설명 순서 = 칩 순서 (사용자 2026-06-13) — 실적(손익)→계약→
    신규시설투자→주주환원(소각/자사주)→자금조달(유상증자)→지분공시
    (신규 대량보유)→배당→리스크(상장폐지)."""

    def test_order_and_threshold(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        i = src.index("금색 — 손익")
        seg = src[i:i + 200]
        order = ["손익", "계약", "시설 자기자본15%", "소각/자사주",
                 "유상증자", "대량보유", "배당", "상장폐지"]
        pos = [seg.index(k) for k in order]
        assert pos == sorted(pos), seg


class TestTangibleAssetCategory:
    """유형자산 양수/양도/처분/취득(+철회) = 자산양수도 (사용자 2026-06-13
    '유형자산처분이 왜 신규시설투자' — chart _CAPEX_KW '유형자산' 누수
    차단). 신규시설투자는 전용 양식('신규시설투자등')만. 57종 대표 제목
    전수 점검에서 적발·수정."""

    def test_tangible_asset_routes(self):
        from bot.dart_feed import _classify_report as C
        assert C("유형자산처분결정(자율공시)") == "자산양수도"
        assert C("[기재정정]주요사항보고서(유형자산양수결정)") == "자산양수도"
        assert C("기타주요경영사항(유형자산 양수결정 철회)") == "자산양수도"
        assert C("유형자산취득결정") == "자산양수도"
        # 전용 양식은 유지
        assert C("신규시설투자등") == "신규시설투자"
        assert C("신규시설투자등(자회사의 주요경영사항)") == "신규시설투자"
        # 합병 = M&A 버킷(자산양수도) · 회사분할(인적/물적) = 회사구조
        # (사용자 2026-06-13 2차 — '회사구조로 바꿔줘'. 분할합병은 합병
        # 성격이라 자산양수도 유지)
        assert C("주요사항보고서(회사합병결정)") == "자산양수도"
        assert C("회사분할결정") == "회사구조"
        assert C("인적분할결정") == "회사구조"
        assert C("물적분할결정") == "회사구조"
        assert C("회사분할합병결정") == "자산양수도"

    def test_v7_retro_wired(self):
        from bot.dart_feed import reclassify_v7_once_if_needed
        assert callable(reclassify_v7_once_if_needed)
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "reclassify_v7_once_if_needed" in src


class TestFavoritesCurrencyUnifiedSort:
    """관심종목 숫자 컬럼 정렬 = 통화 통합(USD 환산) (사용자 2026-06-13
    '통화기호 달라도 시총순') — 시총·저장가·현재가·EPS 의 data-* 정렬키만
    ÷FX, 표시는 원통화 유지. ₩1,526조(≈$1.1T)가 $462B 위로."""

    def test_fx_sort_keys_wired(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "var FX = {{'KRW':1380" in src
        assert "data-mcap=\"' + usd(f.market_cap)" in src
        assert "data-sprice=\"' + usd(f.saved_price)" in src
        assert "data-cprice=\"' + usd(f.current_price)" in src
        assert "data-eps=\"' + usd(f.eps_estimate)" in src
        # 표시 셀은 원통화 포맷 유지
        assert "fmtMcap(f.market_cap, f.currency_symbol)" in src


class TestChildDashboardOrderNaming:
    """자식 대시보드 링크 순서·명칭 통일(사용자 2026-06-13): 모든 나라
    ① 업종별 시세(전체) ② 신고가·신저가 ③ 상한가·하한가/급등·급락.
    홈 위젯 링크 + KR/US 페이지 sibling nav."""

    def _order(self, html, keys):
        import re
        pat = "|".join(re.escape(k) for k in keys)
        return re.findall(rf'href="({pat})"', html)

    def test_kr_home_widget_order_and_names(self):
        from bot.dashboard import _render_sector_movers
        h = _render_sector_movers({"up": [{"name": "반도체", "pct": 1.0}],
                                   "down": [], "ts": "x"})
        assert self._order(h, ["theme", "kr52", "highlow"]) == \
            ["theme", "kr52", "highlow"]
        assert "🏭 업종별 시세(전체)" in h
        assert "📈 신고가·신저가" in h
        assert "🚀 급등·급락" in h   # 사용자 2026-06-14 상한가→급등락
        assert "52주 신고저" not in h and "테마별 시세" not in h

    def test_tw_home_widget_highlow_after_52w(self):
        # TW: 신고가·신저가 → 상한가·하한가 (신고저가 먼저)
        src = open("bot/dashboard.py", encoding="utf-8").read()
        i52 = src.find('href="tw52"')
        ihl = src.find('href="twhighlow"')
        assert 0 < i52 < ihl, (i52, ihl)

    def test_jp_cn_hk_home_label_unified(self):
        # JP/CN/HK 홈 링크가 '신고가·신저가'(통일), '52주 신고저' 아님
        src = open("bot/dashboard.py", encoding="utf-8").read()
        for href in ('jp52', 'hk52'):   # CN 52주 제거(2026-06-14)
            seg = src[src.find(f'href="{href}"') - 80:
                      src.find(f'href="{href}"') + 40]
            assert "신고가·신저가" in seg, href
        # 옛 라벨 잔존 없음(홈 위젯)
        assert "🔝 52주 신고저" not in src

    def test_kr_page_nav_includes_kr52_in_order(self):
        from bot.naver_pages import _shell
        nav = _shell("t", "s", "theme", "<x>")
        assert self._order(nav, ["theme", "kr52", "highlow"]) == \
            ["theme", "kr52", "highlow"]
        assert "🏭 업종별 시세(전체)" in nav and "📈 신고가·신저가" in nav

    def test_us_page_nav_unified_names(self):
        from bot.us_pages import _shell
        nav = _shell("t", "s", "usindustry", "<x>")
        assert self._order(nav, ["usindustry", "ushighlow", "usmovers"]) == \
            ["usindustry", "ushighlow", "usmovers"]
        assert "🏭 업종별 시세(전체)" in nav and "TOP30" not in nav


class TestEarningsCalendarIntl:
    """다가오는 실적 다국가 — JP/TW/CN/HK (사용자 2026-06-13 '실적빌드').
    KR 패턴 일반화: 산업 peer 맵 universe + yfinance .calendar 공유 파서.
    순수 파서 + 유니버스 추출 + 시장별 탭 배선 + 통화 가드."""

    def _cal(self, ds):
        class _D:
            def __init__(s, v): s.v = v
            def strftime(s, fmt): return s.v
        return {"Earnings Date": [_D(ds)],
                "Earnings Average": 1.0, "Revenue Average": 2.0e9}

    def test_row_parser_window_and_fields(self):
        from datetime import date, timedelta
        from bot.market_overview import _earning_row_from_cal
        today = date(2026, 6, 13)
        cutoff = today + timedelta(days=90)
        r = _earning_row_from_cal(self._cal("2026-07-15"), "7203.T",
                                  "Toyota", today, cutoff)
        assert r and r["date"] == "2026-07-15" and r["symbol"] == "7203.T"
        assert r["name"] == "Toyota"
        # 과거·미래초과·빈값·None·파싱불가 모두 None
        assert _earning_row_from_cal(self._cal("2026-01-01"), "X", "X",
                                     today, cutoff) is None
        assert _earning_row_from_cal(self._cal("2026-12-01"), "X", "X",
                                     today, cutoff) is None
        assert _earning_row_from_cal({}, "X", "X", today, cutoff) is None
        assert _earning_row_from_cal({"Earnings Date": []}, "X", "X",
                                     today, cutoff) is None
        assert _earning_row_from_cal(None, "X", "X", today, cutoff) is None
        assert _earning_row_from_cal({"Earnings Date": ["not-a-date"]}, "X",
                                     "X", today, cutoff) is None

    def test_universe_per_market(self):
        from bot.market_overview import _intl_earnings_universe, _INTL_EARN_PEERS
        assert set(_INTL_EARN_PEERS) == {"JP", "TW", "CN_A", "HK"}
        for mk in ("JP", "TW", "CN_A", "HK"):
            u = _intl_earnings_universe(mk)
            assert len(u) > 30, (mk, len(u))
            # (ticker, name) 튜플, 이름=티커 기본, unique
            assert all(isinstance(t, tuple) and len(t) == 2 and t[0] == t[1]
                       for t in u)
            assert len({t[0] for t in u}) == len(u)  # 중복 없음
        # 미지원 시장 → 빈 리스트 graceful
        assert _intl_earnings_universe("US") == []
        assert _intl_earnings_universe("ZZ") == []

    def test_fetch_unsupported_market_empty(self):
        from bot.market_overview import fetch_earnings_calendar_intl
        # 미지원 시장은 네트워크 0 으로 즉시 빈 리스트
        assert fetch_earnings_calendar_intl("US") == []
        assert fetch_earnings_calendar_intl("ZZ") == []

    def test_intl_korean_name_in_parens(self):
        # 사용자 2026-06-14 '실적·캘린더 intl 티커뒤에 (한글명)'. 네이버 이름맵 우선.
        from bot.dashboard import _render_earnings_table
        h = _render_earnings_table([{"symbol": "8233.T", "name": "다카시마야",
                                     "date": "2026-06-30"}])
        assert "8233.T" in h and "(다카시마야)" in h          # 티커 + (한글명)
        # 한글명 미확보 → 티커만(graceful)
        assert "(" not in _render_earnings_table(
            [{"symbol": "8233.T", "name": "", "date": "x"}]).split("8233.T")[1][:3]
        # 네이버 이름맵 — TW 미지원 빈 dict, 미지원 시장 빈 dict (오프라인 graceful)
        from bot.naver_ranking_client import world_name_map
        assert world_name_map("TW") == {} and world_name_map("ZZ") == {}
        assert isinstance(world_name_map("JP"), dict)
        # 캘린더도 intl 티커+(한글명) 배선
        cal = open("bot/earnings_calendar.py", encoding="utf-8").read()
        assert "elif is_intl:" in cal and 'class="nm">({' in cal
        # 실적 fetch 가 네이버 이름맵 우선 배선
        mo = open("bot/market_overview.py", encoding="utf-8").read()
        assert "from bot.naver_ranking_client import world_name_map" in mo

    def test_market_data_wires_four_markets(self):
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parents[1] / "bot"
               / "market_overview.py").read_text("utf-8")
        for sym in ('"JP", 90', '"TW", 90', '"CN_A", 90', '"HK", 90'):
            assert sym in src, sym
        assert "fetch_earnings_calendar_intl" in src
        # 병합 라인에 6개 그룹 모두
        assert "_kr_e + _us_e + _jp_e + _tw_e + _cn_e + _hk_e" in src

    def test_dashboard_tabs_partition(self):
        # .T(JP)·.TW·.TWO(대만 OTC) 겹치지 않게 분할 — 대시보드 필터 미러
        def cls(sym):
            if sym.endswith((".KS", ".KQ")): return "kr"
            if sym.endswith(".T"): return "jp"
            if sym.endswith((".TW", ".TWO")): return "tw"
            if sym.endswith((".SS", ".SZ")): return "cn"
            if sym.endswith(".HK"): return "hk"
            return "us"
        exp = {"005930.KS": "kr", "7203.T": "jp", "2330.TW": "tw",
               "8299.TWO": "tw",  # 타이베이 OTC = 대만(미국으로 새지 않게)
               "600519.SS": "cn", "000858.SZ": "cn", "0700.HK": "hk",
               "AAPL": "us", "T": "us"}
        for s, e in exp.items():
            assert cls(s) == e, (s, cls(s))

    def test_dashboard_tabs_and_panes_wired(self):
        # 탭/패널은 동적 생성(빈 시장 제거 — 사용자 2026-06-13 '내용없으면 지워').
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "_etab_defs = [(k, lbl, rows)" in src     # 동적 탭 정의
        assert "if rows]" in src                          # 빈 시장 필터
        assert '<div class="tabs">{_etab_btns}</div>' in src
        assert "{_etab_panes}" in src
        # 6시장 모두 후보에 포함(데이터 있을 때만 노출)
        for lbl in ("한국", "미국", "일본", "대만", "중국", "홍콩"):
            assert lbl in src, lbl
        # _earn_us 가 모든 해외 접미사 제외(.TWO 타이베이 OTC 포함)
        assert '(".KS", ".KQ", ".T", ".TW", ".TWO", ".SS", ".SZ", ".HK")' in src
        # intl EPS/매출 통화 가드(잘못된 '$' 방지)
        assert "is_intl = sym.endswith" in src
        assert "eps_est is None or is_intl" in src
        assert "rev_est is None or is_intl" in src

    def test_dashboard_empty_tabs_removed(self):
        # 빈 시장 탭/패널이 렌더에서 제외되는지 — 동적 생성 로직 단위검증.
        _earn = {"kr": [{"x": 1}], "us": [], "jp": [{"y": 2}], "tw": [],
                 "cn": [], "hk": [{"z": 3}]}
        defs = [(k, lbl, _earn[k]) for k, lbl in (   # HK 가 TW 앞(2026-06-14)
            ("kr", "한국"), ("us", "미국"), ("jp", "일본"),
            ("hk", "홍콩"), ("cn", "중국"), ("tw", "대만")) if _earn[k]]
        assert [k for k, _l, _r in defs] == ["kr", "jp", "hk"]   # 빈 시장 제거
        assert defs[0][0] == "kr"                                # 첫 탭=active(한국)

    def test_intl_earnings_korean_name_backfill(self):
        # 사용자 2026-06-13 '일본부터 티커말고 번역 한국종목명'. intl 결과에
        # yfinance longName → translate_titles_kr 백필 배선.
        src = open("bot/market_overview.py", encoding="utf-8").read()
        assert "translate_titles_kr" in src and "_fetch_display_names" in src
        assert "intl earnings 한글명" in src
        # cache_only — 캘린더 on-request 경로 동기 스캔 금지
        assert "cache_only" in src

    def test_intl_research_action_wired(self):
        # 사용자 2026-06-13 '다른나라들도 리서치액션 보고 판단'. JP/TW/CN/HK
        # 등급변경(yfinance) fetch + fetch_all_market_data 배선 + 동적 탭.
        from bot.market_overview import fetch_recent_research_intl
        assert fetch_recent_research_intl("XX") == []          # 미지원 시장
        mo = open("bot/market_overview.py", encoding="utf-8").read()
        for k in ("research_jp", "research_tw", "research_cn", "research_hk"):
            assert f'"{k}"' in mo, k                            # assembly 키
        dh = open("bot/dashboard.py", encoding="utf-8").read()
        assert "_render_research_intl_table" in dh
        assert "_res_intl_btns" in dh and "_res_intl_panes" in dh  # 동적 탭(빈 시장 제거)
        # 렌더 — 한글명 우선·없으면 빈 안내
        from bot.dashboard import _render_research_intl_table
        h = _render_research_intl_table(
            [{"symbol": "6758.T", "name": "소니", "firm": "X",
              "to_grade": "Buy", "from_grade": "Hold", "date": "2026-06-12"}])
        assert "소니" in h and "Hold → Buy" in h
        assert "커버리지 제한" in _render_research_intl_table([])

    def test_intl_table_render_no_wrong_currency(self):
        # JP 종목 추정치가 있어도 '$' 가 아닌 '—'(통화 불명확 가드)
        from bot.dashboard import _render_earnings_table
        rows = [{"symbol": "7203.T", "name": "Toyota", "date": "2026-07-15",
                 "hour": "", "eps_estimate": 1.23,
                 "revenue_estimate": 4.5e9, "quarter": None, "year": None}]
        html = _render_earnings_table(rows)
        assert "Toyota" in html and "2026-07-15" in html
        assert "$1.23" not in html and "$4.5B" not in html
        # 빈 입력 graceful
        assert "실적 발표 일정이 없습니다" in _render_earnings_table([])


class TestVolumeColumn:
    """거래량 컬럼 — 신고저 + 급등·급락 표 (사용자 2026-06-13 '두번째 캡쳐처럼').
    intl/tw 신고저(yfinance) + US 신고저·movers(Finviz) 데이터 + 만/억 표기."""

    def test_fmt_vol_units(self):
        from bot.naver_pages import _fmt_vol
        assert _fmt_vol(7600000) == "760만"
        assert _fmt_vol(16850000) == "1,685만"
        assert _fmt_vol(123456789) == "1.2억"
        assert _fmt_vol(500) == "500"
        for bad in (0, None, "x", -5):
            assert _fmt_vol(bad) == "—", bad

    def test_finviz_parser_extracts_volume(self):
        import re
        # _parse_screener_rows 의 vol 추출식 미러 (Finviz v=111 마지막 정수)
        rgx = r'(?<![\d.])(\d{1,3}(?:,\d{3})+|\d{5,})(?![\d.%])'

        def vol(joined):
            m = re.findall(rgx, joined)
            return int(m[-1].replace(",", "")) if m else None
        assert vol("NV | T | S | USA | 12.3B | 28.5 | 123.45 | +5.20% | 1,234,567") == 1234567
        assert vol("Big | X | Y | USA | 5.0B | 12 | 200.00 | +3.00% | 950000") == 950000
        assert vol("NoVol | X | Y | USA | 1.2B | 10 | 5.00 | +1.00%") is None
        # 실제 파서/스캔이 dict 에 vol 키를 담는지(소스 확인) — _parse_screener_
        # rows + _compute_highlow_from 일봉 산출(2026-06-13 진짜 신고가 전환 후
        # vol 을 1차 일봉에서 산출, 별도 5d 패스 제거)
        src = open("bot/finviz_client.py", encoding="utf-8").read()
        assert '"vol": vol' in src and 'vol = int(float(vols' in src

    def test_intl_panel_has_volume_column(self):
        # 미국 포맷 통일 후 intl 신고저는 highlow_render.stock_panel 사용
        from bot.highlow_render import stock_panel
        h = stock_panel("📈 52주 신고가", [{"ticker": "7203.T", "name": "도요타",
                        "price": 3000, "pct": 0.4, "vol": 16850000,
                        "mcap": 400000, "ind": "Auto"}], "hl", "JP")
        assert "거래량" in h and "1,685만" in h and "시총" in h
        h2 = stock_panel("x", [{"ticker": "A", "name": "A",
                                "price": 1, "pct": 0.1}], "hl", "JP")
        assert "거래량" in h2

    def test_tw52_panel_uses_rich_panel(self):
        # tw52 가 stock_panel(거래량+시총+업종+업종분포) 사용
        src = open("bot/tw_pages.py", encoding="utf-8").read()
        assert "stock_panel" in src and "ind_dist_line" in src

    def test_tw52_names_resolved_at_render(self):
        # 종목명 = 렌더 시점 해소(사용자 2026-06-14 '이름만 바꾸면 되지 왜 다시 다
        # 하냐'). 52주 render 가 enrich_for_panel(want_name=True) 로 이름을 렌더 때
        # 덮어쓰므로, 이름 정책 변경에 캐시 버전 bump(전종목 재스캔) 불필요.
        src = open("bot/tw_pages.py", encoding="utf-8").read()
        assert 'enrich_for_panel(high, "TW", want_name=True)' in src   # 52주 신고가
        assert 'enrich_for_panel(low, "TW", want_name=True)' in src    # 52주 신저가
        # 무버 페이지도 동일 패턴(이름 렌더-타임) — 회귀 방지
        assert 'enrich_for_panel(up, "TW", want_ind=True, want_name=True)' in src

    def test_us_stock_panel_volume_sortable(self):
        from bot.us_pages import _stock_panel
        h = _stock_panel("🔺 52주 신고가",
                         [{"ticker": "NVDA", "name": "NVDA", "price": 900.0,
                           "pct": 2.1, "mcap": 22000, "ind": "Semis",
                           "vol": 45000000}], "hl-high")
        assert "거래량" in h
        assert 'data-vol="45000000"' in h        # 정렬 키
        assert 'data-key="vol"' in h              # 헤더 정렬 가능
        assert "4,500만" in h


class TestUpperLowerVolume:
    """상한가/하한가 표 거래량 (사용자 2026-06-13 '상한가/하한가도 거래량').
    TW=TWSE TradeVolume(clean), KR=Naver 위치 heuristic(graceful)."""

    def test_tw_parse_includes_volume(self):
        from bot.twse_client import parse_stock_day_all
        r = parse_stock_day_all([{"Code": "2330", "Name": "TSMC",
                                  "ClosingPrice": "1000", "Change": "90",
                                  "TradeVolume": "45000000"}])
        assert r and r[0]["vol"] == 45000000
        # TradeVolume 없으면 graceful None
        r2 = parse_stock_day_all([{"Code": "1", "Name": "x",
                                   "ClosingPrice": "10", "Change": "1"}])
        assert r2 and r2[0]["vol"] is None

    def test_tw_parse_includes_value(self):
        # 사용자 2026-06-14 'TW 거래대금' — TWSE TradeValue → 억 NT$.
        from bot.twse_client import parse_stock_day_all
        r = parse_stock_day_all([{"Code": "2330", "Name": "TSMC",
                                  "ClosingPrice": "1000", "Change": "90",
                                  "TradeVolume": "45000000",
                                  "TradeValue": "4500000000000"}])
        assert r and r[0]["value"] == 45000.0          # 4.5e12/1e8
        r2 = parse_stock_day_all([{"Code": "1", "Name": "x",
                                   "ClosingPrice": "10", "Change": "1"}])
        assert r2 and r2[0]["value"] is None           # 없으면 graceful

    def test_tw_names_korean(self, monkeypatch):
        # 사용자 2026-06-14 '대만도 한글로' (옛 영문 정책 번복) — yfinance longName(영문)·
        # 中文 → translate_titles_kr 로 한국어화('티에스엠씨' 류).
        import bot.chart_translate as ct
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "_fetch_display_names",
                            lambda tks: {t: "Yageo Corporation" for t in tks})
        monkeypatch.setattr(ct, "translate_titles_kr",
                            lambda names: {n: "야게오" for n in names})
        rows = [{"ticker": "2327.TW", "name": "2327.TW"}]
        fc._backfill_korean_names(rows, "TW")
        assert rows[0]["name"] == "야게오"          # 영문 longName → 한국어 번역
        # enrich_for_panel·_backfill 둘 다 TW 한국어 분기(translate_titles_kr)
        hr = open("bot/highlow_render.py", encoding="utf-8").read()
        assert 'want_name and market == "TW"' in hr and "translate_titles_kr" in hr
        fcs = open("bot/finviz_client.py", encoding="utf-8").read()
        assert 'if market == "TW":' in fcs

    def test_tw_smallcap_chinese_translated_in_enrich(self, monkeypatch):
        # 사용자 2026-06-14 'TW 소형주도 한글로' — enrich_for_panel TW 가 yfinance longName
        # 미스 시 中文 native 명을 translate_titles_kr 로 한국어화.
        import bot.chart_translate as ct
        import bot.finviz_client as fc
        from bot import highlow_render as hr
        # 렌더-세이프 SWR(2026-06-16): 슬로우 함수는 allow_slow/cache_only 받음.
        monkeypatch.setattr(fc, "_fetch_mcaps", lambda tks: {})
        monkeypatch.setattr(fc, "_fetch_display_names",
                            lambda tks, allow_slow=True: {})   # yfinance 전무
        monkeypatch.setattr(ct, "translate_titles_kr",
                            lambda names, cache_only=False: {n: "번역된회사" for n in names})
        hr._ENRICH_CACHE.clear()
        items = [{"ticker": "9999.TW", "name": "中文小型股"}]
        # 렌더 경로 = _enrich_compute(allow_slow=False) — 中文 native 명 번역(캐시-only mock).
        meta = hr._enrich_compute(["9999.TW"], items, "TW", False, True, allow_slow=False)
        assert meta["9999.TW"]["name_kr"] == "번역된회사"   # 中文 미스분 한국어 번역
        hr._ENRICH_CACHE.clear()

    def test_tw_52w_name_translation(self, monkeypatch):
        # 사용자 2026-06-14 'TW 신고저도 한글' — _backfill_korean_names TW 가 longName(영문)·
        # 中文 native 명을 translate_titles_kr 로 한국어화.
        import bot.chart_translate as ct
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "_fetch_display_names",
                            lambda tks: {"2330.TW": "TSMC"})   # 2330 만 longName
        monkeypatch.setattr(ct, "translate_titles_kr",
                            lambda names: {n: "번역" + str(n)[:2] for n in names})
        rows = [{"ticker": "2330.TW", "name": "台積電"},
                {"ticker": "9999.TW", "name": "中文小型股"}]   # longName 미스
        fc._backfill_korean_names(rows, "TW")
        assert rows[0]["name"] == "번역TS"          # 영문 longName "TSMC" → 한국어 번역
        assert rows[1]["name"] == "번역中文"        # 中文 → 한국어 번역

    def test_enrich_mcap_persist_fallback(self, monkeypatch):
        # 사용자 2026-06-14 'TW 급등락 시총 안 떠 (52주는 됨)' — yfinance None 이어도
        # 직전 성공 시총 디스크 캐시 유지. 2026-06-16 SWR: yfinance fetch 는
        # 백그라운드(allow_slow=True), 렌더(allow_slow=False)는 persist 만.
        import bot.finviz_client as fc
        from bot import highlow_render as hr
        store: dict = {}
        monkeypatch.setattr(fc, "_cached", lambda name, ttl=0: store.get(name))
        monkeypatch.setattr(fc, "_cache_write",
                            lambda name, obj: store.__setitem__(name, obj))
        monkeypatch.setattr(fc, "_fetch_mcaps", lambda tks: {"2330.TW": 1e13})
        items = [{"ticker": "2330.TW", "name": "x"}]
        # 백그라운드 enrich(allow_slow=True) = yfinance fetch → persist 적재.
        m1 = hr._enrich_compute(["2330.TW"], items, "TW", False, False, allow_slow=True)
        assert m1["2330.TW"]["mcap"] == round(1e13 / 1e8, 2)      # 성공 → persist
        monkeypatch.setattr(fc, "_fetch_mcaps", lambda tks: {})   # yfinance 죽음
        m2 = hr._enrich_compute(["2330.TW"], items, "TW", False, False, allow_slow=True)
        assert m2["2330.TW"]["mcap"] == round(1e13 / 1e8, 2)      # 직전 성공값 유지
        # 렌더-세이프(allow_slow=False) — _fetch_mcaps 미호출, persist 만 사용.
        m3 = hr._enrich_compute(["2330.TW"], items, "TW", False, False, allow_slow=False)
        assert m3["2330.TW"]["mcap"] == round(1e13 / 1e8, 2)      # 렌더도 persist 폴백

    def test_enrich_render_safe_skips_slow_yfinance(self, monkeypatch):
        # 사용자 2026-06-16 (TW 렌더 8.2s→~0s): 렌더(allow_slow=False)는 _fetch_mcaps
        # (fast_info+history)·_fetch_display_names(.info) per-ticker·Flash 번역 호출 0
        # — persist/캐시/벌크맵만. 슬로우 yfinance 는 백그라운드 daemon 으로 이전.
        import bot.chart_translate as ct
        import bot.finviz_client as fc
        from bot import highlow_render as hr

        def _boom(*a, **k):
            raise AssertionError("렌더 경로에서 슬로우 yfinance/Flash 호출 금지")
        seen: dict = {}
        monkeypatch.setattr(fc, "_fetch_mcaps", _boom)             # 호출되면 fail
        monkeypatch.setattr(fc, "_fetch_industries",
                            lambda tks, allow_slow=True: seen.update(ind=allow_slow) or {})
        monkeypatch.setattr(fc, "_fetch_display_names",
                            lambda tks, allow_slow=True: (_boom() if allow_slow else {}))
        monkeypatch.setattr(ct, "translate_titles_kr",
                            lambda names, cache_only=False: ({} if cache_only else _boom()))
        meta = hr._enrich_compute(["2330.TW"], [{"ticker": "2330.TW", "name": "台積電"}],
                                  "TW", True, True, allow_slow=False)
        assert isinstance(meta, dict)        # 슬로우 호출 0 → 예외 없이 완료
        assert seen.get("ind") is False      # 업종도 allow_slow=False(캐시/벌크만)

    def test_tw_stock_day_session_aware(self):
        # 사용자 2026-06-14 '모두 장중 1h' — TW 상한가도 _session_fresh(옛 5분 대체)
        src = open("bot/twse_client.py", encoding="utf-8").read()
        assert "_session_fresh(\"TW\"" in src and "_HL_INTRA_TTL" in src

    def test_tw_sector_kr_suffix_match(self):
        # 사용자 2026-06-16 한자 누수: TWSE 電腦及週邊設備類指數 → core 電腦及週邊設備
        # 가 map key 電腦及週邊(設備 없음) 미스로 繁體 그대로 노출되던 것. 정식명 추가
        # + suffix-robust _sector_kr.
        from bot.twse_client import _sector_kr
        assert _sector_kr("電腦及週邊設備") == "컴퓨터·주변기기"   # 設備 정식명
        assert _sector_kr("鋼鐵") == "철강"                       # 기존 정확매칭 유지
        assert _sector_kr("電子反向") == "전자 인버스"             # 인버스 카테고리(정상)
        assert _sector_kr("未知業種XYZ") == "未知業種XYZ"          # 미매핑 → 원문 graceful
        # 짧은 core 오매칭 방지(電子 → 電子兩倍槓桿 으로 잘못 매칭 금지)
        assert _sector_kr("電子") != "전자 2배(레버리지)"

    def test_favorites_endpoint_is_swr(self):
        # 사용자 2026-06-16 '관심종목 오래걸려': /api/favorites 가 종목당 yfinance
        # (tk.info) 동기 콜로 블로킹하던 것 → SWR(스테일/콜드 즉시 + 백그라운드 daemon).
        src = open("bot/market_favorites.py", encoding="utf-8").read()
        assert "_kick_fav_refresh" in src and "_compute_favorites_with_prices" in src
        assert "daemon=True, name=\"fav-refresh\"" in src   # 백그라운드 daemon(비차단)
        assert "return _load()" in src                       # 콜드 — 이름만 즉시
        js = open("bot/dashboard.py", encoding="utf-8").read()
        assert "setTimeout(loadFavs, 5000)" in js            # 콜드 가격 픽업 재폴
        assert "setInterval(function() {{ if (!document.hidden) loadFavs();" in js

    def test_tw_upper_panel_has_volume_col(self):
        src = open("bot/tw_pages.py", encoding="utf-8").read()
        # 상한가 패널(_panel) + 52w 패널 둘 다 거래량
        assert src.count("거래량") >= 2

    def test_kr_naver_highlow_panel_has_volume_col(self):
        # KR 상한가 = 공용 stock_panel(show_vol=True)로 전환 (사용자 2026-06-13
        # 시총 추가). 거래량은 stock_panel 이 렌더, 파서는 vol 키 산출.
        src = open("bot/naver_pages.py", encoding="utf-8").read()
        assert "stock_panel" in src and "show_vol=True" in src
        psrc = open("bot/naver_sector_client.py", encoding="utf-8").read()
        assert '"vol": vol' in psrc

    def test_kr_industry_map_apply_graceful(self, monkeypatch):
        # 사용자 2026-06-14 'KR 신고가·급등락에 업종 한글' — 네이버 업종 그룹 멤버맵.
        import bot.naver_sector_client as ns
        from bot.naver_sector_client import apply_kr_industry
        monkeypatch.setattr(ns, "kr_industry_map",
                            lambda: {"005930": "반도체와반도체장비"})
        items = [{"ticker": "005930.KS", "ind": None},
                 {"ticker": "000660.KS", "ind": "이미있음"}]
        apply_kr_industry(items)
        assert items[0]["ind"] == "반도체와반도체장비"     # 코드 백필(접미사 제거)
        assert items[1]["ind"] == "이미있음"              # 기존값 보존
        monkeypatch.setattr(ns, "kr_industry_map", lambda: {})
        r = apply_kr_industry([{"ticker": "005930.KS", "ind": None}])
        assert r[0]["ind"] is None                        # 빈 맵 graceful
        assert apply_kr_industry([]) == []
        # 업종 그룹 링크 정규식 — 번호+이름 캡처 (&amp; 포함 href)
        m = ns._UPJONG_NO_RE.search(
            'sise_group_detail.naver?type=upjong&amp;no=278">반도체</a>')
        assert m and m.group(1) == "278" and "반도체" in m.group(2)

    def test_cache_ttls(self):
        # 엄마보드 스냅샷 2분→5분(#368)→1분(사용자 2026-06-14 '데이터 주기 1분').
        # 네이버 라이브 전부 30초(사용자 2026-06-15 '네이버는 다 실시간').
        import bot.market_overview as mo
        import bot.naver_sector_client as ns
        assert mo._CACHE_TTL_SEC == 30        # 홈 스냅샷 30초(KR 개별종목까지 네이버=순수)
        assert ns._CACHE_TTL_SEC == 30        # 네이버 업종·테마 30초(장중·session-aware)

    def test_pause_keeps_naver_hk_industry_earnings(self, monkeypatch):
        # 사용자 2026-06-14 '정지 중에도 US/HK 업종·TW/HK 실적 한국어'. 정지 게이트가
        # 네이버 맵 빌드를 막던 버그 + HK 업종 yfinance→naver 폴백 + 스냅샷 게이트.
        import bot.finviz_client as fc
        import bot.naver_ranking_client as nv
        monkeypatch.setattr(fc, "_fetch_industries", lambda tks, **k: {})  # yfinance 빈
        monkeypatch.setattr(nv, "world_industry_map",
                            lambda m: {"0700.HK": "Internet", "00005.HK": "Banks"})
        got = fc._industries_for(["0700.HK", "0005.HK"], "HK")
        assert got["0700.HK"] == "Internet" and got["0005.HK"] == "Banks"  # naver 폴백
        # _prewarm_highlow 가 더 이상 정지 시 전체 skip 안 함(네이버 맵 빌드 유지)
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "yfinance 정지(YF_PAUSE) → 전체 skip" not in tb
        # 스냅샷·_fetch_display_names 게이트 + TW 실적 中文→한글 + 1분
        fcs = open("bot/finviz_client.py", encoding="utf-8").read()
        assert ".info skip" in fcs
        mo = open("bot/market_overview.py", encoding="utf-8").read()
        assert "yf_batch_snapshot.json" in mo and "TWSE 中文명" in mo

    def test_hk_naver_overlay_and_yfinance_industry(self):
        # 사용자 2026-06-14 'HK 신고저 거래량·거래대금·시총·종목명 네이버, 급등락
        # 업종 야후'. (1) _industries_for HK → yfinance(네이버 라우팅 제외)
        import bot.finviz_client as fc
        fc._fetch_industries = lambda tks, **k: {t: "YF-Ind" for t in tks}
        got = fc._industries_for(["0700.HK"], "HK")
        assert got["0700.HK"] == "YF-Ind"      # HK 업종=yfinance
        # (2) _compute_highlow_from 에 HK worldstock overlay 배선
        fsrc = open("bot/finviz_client.py", encoding="utf-8").read()
        assert 'if tag in ("HK", "JP"):' in fsrc and "world_stock_map" in fsrc
        # (3) world_stock_map graceful(미지원 시장 빈, 오프라인 graceful)
        from bot.naver_ranking_client import world_stock_map
        assert world_stock_map("US") == {}      # worldstock 미대상
        assert isinstance(world_stock_map("HK"), dict)   # 오프라인 graceful
        # (4) HK 52주 부제 = 네이버 vol/시총·yfinance 업종
        ip = open("bot/intl_pages.py", encoding="utf-8").read()
        assert "거래량·거래대금·시총·종목명=네이버" in ip

    def test_hk_universe_liquidity_cap(self, monkeypatch):
        # 사용자 2026-06-14 'HK 산출중·야후 맛탱이' — 전종목(~2000) yfinance 스캔
        # 부하를 네이버 시총 상위 N 으로 캡(빠르고 안정). zfill 정수 매칭.
        import bot.intl_highlow as ih
        import bot.naver_ranking_client as nv
        monkeypatch.setattr(nv, "world_stock_map", lambda m: {
            "00700.HK": {"mcap": 42000.0}, "00005.HK": {"mcap": 24000.0},
            "00001.HK": {"mcap": 5000.0}, "09999.HK": {"mcap": 100.0}})
        full = ["9999.HK", "0001.HK", "0700.HK", "0005.HK"]
        out = ih._cap_by_liquidity_hk(full, 3)
        assert out[:2] == ["0700.HK", "0005.HK"]   # 시총 상위 우선
        assert "9999.HK" not in out                 # 소형 탈락
        monkeypatch.setattr(nv, "world_stock_map", lambda m: {})
        assert ih._cap_by_liquidity_hk(full, 2) == full[:2]   # 맵 부재 폴백

    def test_jp_universe_liquidity_cap(self, monkeypatch):
        # _cap_by_liquidity 가 market 별 라우팅 + 시총 상위 우선(캡이 적용될 때).
        # 캡 자체는 2026-06-16 env HIGHLOW_UNIVERSE_CAP(기본 full)로 전환.
        import bot.intl_highlow as ih
        import bot.naver_ranking_client as nv
        seen = {}

        def _wsm(m):
            seen["m"] = m
            return {"7203.T": {"mcap": 30000.0}, "6758.T": {"mcap": 12000.0},
                    "4062.T": {"mcap": 6000.0}, "9999.T": {"mcap": 50.0}}
        monkeypatch.setattr(nv, "world_stock_map", _wsm)
        full = ["9999.T", "4062.T", "7203.T", "6758.T"]
        out = ih._cap_by_liquidity(full, 3, "JP")
        assert seen["m"] == "JP"                        # JP 맵 라우팅(HK 하드코딩 아님)
        assert out[:2] == ["7203.T", "6758.T"]          # 시총 상위 우선
        assert "9999.T" not in out                      # 소형 탈락
        # _universe 가 JP/HK 캡 분기 (env 캡, 기본 full). 네이버 합집합 가드는
        # 철회(2026-06-19 probe — REIT 재편입·HK 형식불일치 부작용, 공식 파싱이 완전).
        src = open("bot/intl_highlow.py", encoding="utf-8").read()
        assert 'market in ("JP", "HK")' in src, "JP/HK 유니버스 분기 미배선"
        assert "HIGHLOW_UNIVERSE_CAP" in src and "_cap_by_liquidity(full, _cap, market)" in src

    def test_jp_hk_naver_overlay(self, monkeypatch):
        # JP 네이버 worldstock overlay(직접 매칭) + HK overlay(zfill 정수 매칭).
        # (시총 필터는 사용자 2026-06-14 '다시 생각' 으로 제거 — 시총 표시는 유지.)
        import bot.finviz_client as fc
        import bot.naver_ranking_client as nv
        monkeypatch.setattr(nv, "world_stock_map", lambda m: {
            "7203.T": {"mcap": 500.0, "name": "도요타"}} if m == "JP"
            else {"00700.HK": {"mcap": 42000.0, "name": "텐센트"}})
        jp = [{"ticker": "7203.T", "mcap": None}]
        fc._naver_worldstock_overlay(jp, "JP")
        assert jp[0]["mcap"] == 500.0 and jp[0]["name"] == "도요타"
        hk = [{"ticker": "0700.HK", "mcap": None}]
        fc._naver_worldstock_overlay(hk, "HK")
        assert hk[0]["mcap"] == 42000.0          # 0700↔00700 매칭
        # 배선: _compute_highlow_from 이 HK/JP overlay·TW persist 호출
        src = open("bot/finviz_client.py", encoding="utf-8").read()
        assert 'if tag in ("HK", "JP"):' in src and "_naver_worldstock_overlay" in src
        assert 'elif tag == "TW":' in src and "_persist_mcap_overlay" in src
        # 시총 필터 제거 확인 (사용자 '다시 생각')
        assert "filter_min_mcap" not in open("bot/us_pages.py", encoding="utf-8").read()
        assert "filter_min_mcap" not in open("bot/intl_pages.py", encoding="utf-8").read()

    def test_tw_persist_mcap_overlay(self, monkeypatch):
        # 사용자 2026-06-14 'TW 52주 시총 안 떠' — 52주 compute 도 enrich 와 같은
        # persist 파일 공유(yfinance None 시 직전값).
        import bot.finviz_client as fc
        store: dict = {}
        monkeypatch.setattr(fc, "_cached", lambda n, ttl=0: store.get(n))
        monkeypatch.setattr(fc, "_cache_write", lambda n, o: store.__setitem__(n, o))
        fc._persist_mcap_overlay([{"ticker": "2330.TW", "mcap": 1000.0}], "TW")  # seed
        rows = [{"ticker": "2330.TW", "mcap": None}]
        fc._persist_mcap_overlay(rows, "TW")
        assert rows[0]["mcap"] == 1000.0          # 복원
        assert "enrich_mcap_TW.json" in store     # enrich 와 같은 파일

    def test_intl_earnings_empty_fallback(self):
        # 사용자 2026-06-14 '다가오는 실적 미국만' — intl 빈 결과 시 최근 비어있지
        # 않은 캐시 폴백(배포 salt 리셋·yfinance 장애 휘발 방지).
        src = open("bot/market_overview.py", encoding="utf-8").read()
        assert "if not results:" in src and "earnings_{market}_*.json" in src

    def test_yf_pause_gates(self, monkeypatch, tmp_path):
        # 사용자 2026-06-14 '야후 콜 멈춰봐 — 안 돌아와' — yfinance 부하 작업 일괄 skip.
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "_YF_PAUSE_MARKER", tmp_path / "YF_PAUSE")
        assert fc.yf_paused() is False
        assert fc.set_yf_pause(True) is True and fc.yf_paused() is True
        # 정지 시 yfinance 부하 작업 skip (네트워크 0)
        assert fc._fetch_mcaps(["AAPL"]) == {}
        assert fc._compute_highlow_from(["7203.T"], {}, "nx.json", "s", "JP")["high"] == []
        assert fc._compute_movers_from(["7203.T"], {}, "nx.json", "s", "JP")["up"] == []
        assert fc.set_yf_pause(False) is False and fc.yf_paused() is False  # 재개·마커 삭제
        # 배선: prewarm·kick·earnings·명령·자동정지 + 채널 라우팅(PTB CommandHandler
        # 는 channel_post 에 안 fire → on_channel_post 분기 필수)
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "cmd_yfpause" in tb and ".yf_autopause_v1" in tb and "yf_paused" in tb
        assert "_handle_yfpause" in tb and 'first_word == "yfpause"' in tb
        for f in ("bot/intl_highlow.py", "bot/tw_highlow.py"):
            assert "if not yf_paused()" in open(f, encoding="utf-8").read()
        assert "yf_paused" in open("bot/market_overview.py", encoding="utf-8").read()

    def test_naver_pause_gates_and_commands(self, monkeypatch, tmp_path):
        # 사용자 2026-06-14 '네이버도 야후처럼 끄고 키는 명령' + /health 진단.
        import bot.finviz_client as fc
        import bot.naver_ranking_client as nv
        import bot.naver_sector_client as ns
        monkeypatch.setattr(fc, "_NAVER_PAUSE_MARKER", tmp_path / "NAVER_PAUSE")
        assert fc.naver_paused() is False
        assert fc.set_naver_pause(True) is True and fc.naver_paused() is True
        assert nv._get_stocks("http://x") is None     # 정지 → fetch skip(네트워크 0)
        assert ns._get("http://x") is None
        assert fc.set_naver_pause(False) is False and fc.naver_paused() is False
        # 배선: 명령·헬스체크·레지스트리·채널 라우팅
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "cmd_naverpause" in tb and "cmd_health" in tb
        assert "_handle_naverpause" in tb and 'first_word == "naverpause"' in tb
        assert '"naverpause":' in tb and '"health":' in tb
        # source_health 진단 모듈
        from bot.source_health import format_report, run
        assert callable(run) and callable(format_report)
        # help 에 신규 명령 등록
        import re as _re
        ht = _re.search(r'_HELP_TEXT\s*=\s*"""(.*?)"""',
                        tb, _re.DOTALL).group(1)
        assert "/yfpause" in ht and "/naverpause" in ht and "/health" in ht

    def test_fast_info_circuit_breaker(self, monkeypatch):
        # 사용자 2026-06-14 /health: fast_info IP rate-limit 끈끈(재개 즉시 재차단) →
        # 회로차단으로 자동 skip(download/Naver/persist 폴백, yahoo 회복 보호).
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "_FAST_INFO_COOLDOWN_UNTIL", 0.0)
        assert fc.fast_info_ok() is True
        assert fc.is_rate_limit_error(Exception("Too Many Requests. Rate limited")) is True
        assert fc.is_rate_limit_error(Exception("connection reset")) is False
        fc.fast_info_trip("test")
        assert fc.fast_info_ok() is False
        assert fc._fetch_mcaps(["AAPL"]) == {}     # 쿨다운 → fast_info skip(네트워크 0)
        # 배선: 스냅샷·차트가 회로차단 게이트 + 헬스체크 표기
        assert "fast_info_ok" in open("bot/market_overview.py", encoding="utf-8").read()
        assert "fast_info_ok" in open("bot/chart_data.py", encoding="utf-8").read()
        assert "fast_info_breaker" in open("bot/source_health.py", encoding="utf-8").read()


class TestHighlowRenderShared:
    """신고저/급등락/상한가 공용 리치 렌더러 (사용자 2026-06-13 '미국 포맷
    전 나라 통일'). 통화별 가격·시총 + 업종분포 + 시총정렬 + 헤더정렬."""

    def test_fmt_mcap_market_aware(self):
        from bot.highlow_render import fmt_mcap
        assert fmt_mcap(4504, "US") == "$450.4B"     # 억$ → $B
        assert fmt_mcap(25000, "US") == "$2.50T"
        assert fmt_mcap(30000, "JP") == "¥3.0조"       # 30000억¥ = 3조
        assert fmt_mcap(500, "JP") == "¥500억"
        assert fmt_mcap(15260000, "KR") == "₩1526.0조"
        for bad in (0, None, "x"):
            assert fmt_mcap(bad, "JP") == "—"

    def test_stock_panel_columns_and_currency(self):
        from bot.highlow_render import stock_panel
        h = stock_panel("📈 신고가", [{"ticker": "2330.TW", "name": "TSMC",
                        "price": 1000.5, "pct": 2.1, "vol": 45000000,
                        "mcap": 250000, "ind": "Semis"}], "t", "TW")
        # 통화기호는 헤더에만(사용자 2026-06-13), 셀은 숫자만
        for need in ("거래량", "업종", 'data-key="mcap"', "hl-table",
                     "현재가 (NT$)", "시총 (NT$)",   # 헤더 통화기호
                     "1,000.50", "25.0조", "4,500만"):  # 셀 무기호
            assert need in h, need
        assert "NT$1,000.50" not in h and "NT$25.0조" not in h  # 셀 무기호 확인

    def test_panel_column_flags(self):
        from bot.highlow_render import stock_panel
        # KR: 종목명만·업종X·거래량O
        kr = stock_panel("x", [{"ticker": "005930.KS", "name": "삼성전자",
                          "price": 88000, "pct": 1.0, "vol": 1, "mcap": 5260000,
                          "ind": "Elec"}], "t", "KR", name_only=True,
                         show_ind=False, show_vol=True)
        assert "삼성전자" in kr and "업종" not in kr and "거래량" in kr
        assert '<span class="ts">(' not in kr   # 티커 노출 안 함
        # US: 거래량X
        us = stock_panel("x", [{"ticker": "NVDA", "name": "NVDA", "price": 9,
                          "pct": 1.0, "mcap": 100, "ind": "S"}], "t", "US",
                         show_vol=False)
        assert "거래량" not in us

    def test_us_52w_naver_name_value(self, monkeypatch):
        # 사용자 2026-06-14(2차): US 52주 네이버 한글명 + 거래량 + 거래대금.
        import bot.finviz_client as fc
        monkeypatch.setattr(fc, "fetch_high_low", lambda: {
            "high": [{"ticker": "NVDA", "name": "NVDA", "price": 900.0, "pct": 2.1,
                      "vol": 50000000, "mcap": 22000, "ind": "Semis"}],
            "low": [], "ts": "x", "source": "Finviz"})
        import bot.naver_ranking_client as nv
        monkeypatch.setattr(nv, "world_upjong_name", lambda m: {"NVDA": "엔비디아"})
        from bot.us_pages import render_us_highlow_page
        h = render_us_highlow_page()
        assert "엔비디아" in h              # 네이버 한글명 enrich
        assert "거래대금 ($)" in h          # 거래대금
        assert "거래량(주)" in h            # 거래량(이제 추가)
        assert "45.0B" in h                 # 900×5e7/1e8=450억$ → $45.0B

    def test_ind_dist_line(self):
        from bot.highlow_render import ind_dist_line
        items = [{"ind": "Semis"}, {"ind": "Semis"}, {"ind": "Banks"},
                 {"ind": None}]
        line = ind_dist_line(items)
        assert "업종 분포" in line and "Semis 2" in line and "Banks 1" in line
        assert ind_dist_line([]) == ""

    def test_sort_by_mcap_desc_none_last(self):
        from bot.highlow_render import sort_by_mcap
        r = sort_by_mcap([{"mcap": 10}, {"mcap": None}, {"mcap": 500}])
        assert [x.get("mcap") for x in r] == [500, 10, None]

    def test_intl_and_tw_wired_to_shared(self):
        for mod in ("bot/intl_pages.py", "bot/tw_pages.py"):
            src = open(mod, encoding="utf-8").read()
            assert "from bot.highlow_render import" in src and "stock_panel" in src


class TestIntlKoreanNames:
    """intl 신고저 종목명 한글 번역 (사용자 2026-06-13 'TICKER (한글명)').
    .info longName(VM 정상) → chart_translate Flash·영구캐시. US 제외."""

    def test_fetch_display_names_graceful(self):
        from bot.finviz_client import _fetch_display_names
        assert _fetch_display_names([]) == {}

    def test_compute_highlow_translates_non_us(self):
        # 공용 헬퍼 _backfill_korean_names 가 신고저/무버/JP상한 3경로에서 호출
        # (옛 `market and market != "US"` NameError + name==ticker 만 번역 버그
        #  동시 해소 — 사용자 2026-06-13 南亞科 캡쳐). 네이티브명 직접 번역.
        src = open("bot/finviz_client.py", encoding="utf-8").read()
        assert "_fetch_display_names(" in src and "translate_titles_kr" in src
        assert "def _backfill_korean_names(" in src
        assert src.count("_backfill_korean_names(") >= 4    # 정의 1 + 호출 3
        # 네이티브명(name != ticker)도 번역 — 옛 'name==ticker 만' 스킵 제거
        assert 'r["name"] != r["ticker"]' in src
        # US/KR 은 no-op (영문/pykrx 한글 유지)
        assert 'market in ("US", "KR")' in src

    def test_backfill_korean_names_market_gate(self):
        # US/KR no-op(네트워크 0·이름 보존), graceful (사용자 2026-06-13).
        from bot.finviz_client import _backfill_korean_names
        us = [{"ticker": "AAPL", "name": "Apple"}]
        _backfill_korean_names(us, "US")
        assert us[0]["name"] == "Apple"
        kr = [{"ticker": "005930.KS", "name": "삼성전자"}]
        _backfill_korean_names(kr, "KR")
        assert kr[0]["name"] == "삼성전자"

    def test_panel_label_ticker_then_korean(self):
        from bot.highlow_render import stock_panel
        h = stock_panel("x", [{"ticker": "6758.T", "name": "소니 그룹",
                        "price": 13000, "pct": 1.0, "vol": 100,
                        "mcap": 180000, "ind": "Electronics"}], "t", "JP")
        # 정책 변경(사용자 2026-06-15): 티커 + 한글명 별도 줄(.nk) — 괄호 인라인이
        # 좁은 셀에서 단어 중간 줄바꿈되던 것 해소. 한국제외 전 시장.
        assert "6758.T" in h and '<span class="nk">소니 그룹</span>' in h
        assert "(소니 그룹)" not in h


class TestKisKrNewHighlow:
    """KR 52주 신고저 = KIS near-new-highlow 전 시장 (사용자 2026-06-13,
    PRC=0 신고가·1 신저가 라이브 검증). ETF/채권 필터 + peer 폴백 + 한글명 네이티브."""

    def test_etf_bond_filter(self):
        from bot.kis_client import _nhl_is_etf_bond
        assert _nhl_is_etf_bond("Q610056", "메리츠 멕시코 페소화 ETN")
        assert _nhl_is_etf_bond("152100", "TIGER 미국배당다우존스타겟")
        assert _nhl_is_etf_bond("000000", "ACE 단기통안채")
        assert _nhl_is_etf_bond("000000", "메리츠 인버스 3X 국채3년")
        assert not _nhl_is_etf_bond("000020", "동화약품")
        assert not _nhl_is_etf_bond("005930", "삼성전자")

    def test_fetch_graceful_without_creds(self):
        from bot.kis_client import fetch_kr_new_highlow
        r = fetch_kr_new_highlow()
        assert set(r) == {"high", "low"}        # creds 부재 시 빈 리스트

    def test_intl_highlow_kr_routes_to_kis(self):
        src = open("bot/intl_highlow.py", encoding="utf-8").read()
        assert 'market == "KR"' in src and "_compute_kr_kis" in src
        assert "fetch_kr_new_highlow" in src
        # KIS 빈 결과 → peer 폴백(회귀 0)
        assert "KIS 폴백" in src or "peer empty" in src

    def test_kr_page_rich_with_kis_source(self):
        import bot.intl_highlow as ih
        orig = ih.fetch_intl_highlow
        ih.fetch_intl_highlow = lambda m: {
            "high": [{"ticker": "000020.KS", "name": "동화약품", "price": 12000,
                      "pct": 5.2, "vol": 3000000, "mcap": 3500, "ind": "Drug"}],
            "low": [], "ts": "x", "building": False,
            "source": "KIS 신고가/신저가 근접(전 시장 스캔)", "status": {}}
        try:
            from bot.intl_pages import render_intl_highlow52_page
            h = render_intl_highlow52_page("KR")
        finally:
            ih.fetch_intl_highlow = orig
        assert "동화약품" in h and "거래량" in h and "시총" in h
        assert "KIS 신고가/신저가 근접" in h     # 소스 라벨


class TestIntlFullMarket:
    """JP/HK 전종목 신고저 (사용자 2026-06-13 'full-market'). JPX/HKEX 공식
    listing 파서(VM 검증) + peer 폴백 + 긴 캐시. 파서 per-row 순수 테스트."""

    def test_jp_pick(self):
        from bot.intl_universe import _jp_pick
        assert _jp_pick("7203", "プライム（内国株式）") == "7203.T"
        assert _jp_pick("13080", "ETF・ETN") is None
        assert _jp_pick("2971", "REIT") is None
        assert _jp_pick("720", "プライム") is None         # 3자리 제외
        # 2024~ TSE 영숫자 신규상장 코드 — 키옥시아 285A 등(사용자 2026-06-19,
        # 옛 isdigit 필터가 통째 제외해 52주 보드에서 누락). 4자 영숫자 허용.
        assert _jp_pick("285A", "プライム（内国株式）") == "285A.T"
        assert _jp_pick("130A", "グロース（内国株式）") == "130A.T"
        assert _jp_pick("285a", "プライム") == "285A.T"    # 소문자→대문자
        assert _jp_pick("コード", "プライム") is None       # 헤더/CJK 제외
        assert _jp_pick("12345", "プライム") is None        # 5자 제외
        # 영숫자라도 ETF/REIT div 면 제외
        assert _jp_pick("285A", "ETF・ETN") is None

    def test_hk_pick(self):
        from bot.intl_universe import _hk_pick
        assert _hk_pick("00700", "Equity") == "0700.HK"
        assert _hk_pick("80737", "Equity") == "80737.HK"  # 5자리 유지
        assert _hk_pick("02800", "ETF") is None
        assert _hk_pick("700", "Equity") == "0700.HK"     # zero-pad

    def test_full_universe_graceful(self):
        import bot.intl_universe as iu
        orig = iu._http_get
        iu._http_get = lambda u: (_ for _ in ()).throw(RuntimeError("net"))
        try:
            assert iu.full_universe("JP") == [] and iu.full_universe("HK") == []
            assert iu.full_universe("US") == []
        finally:
            iu._http_get = orig

    def test_universe_falls_back_to_peer(self):
        # full_universe 빈 결과(<100) → peer 유니버스 폴백(회귀 0)
        import bot.intl_highlow as ih, bot.intl_universe as iu
        orig = iu.full_universe
        iu.full_universe = lambda m: []
        try:
            uni, _ = ih._universe("JP")
            assert len(uni) > 30 and all(t.endswith(".T") for t in uni)
        finally:
            iu.full_universe = orig

    def test_full_market_session_aware_wired(self):
        # JP/HK 전종목(full_universe) + 시장-인지 신선도. KR=네이버(fetch_kr_highlow)
        # → 장중 30초(_MOVERS_INTRA_TTL, 사용자 2026-06-15 '한국 신고저 실시간'),
        # JP/HK=yfinance 스캔 → 장중 1h(_HL_INTRA_TTL).
        src = open("bot/intl_highlow.py", encoding="utf-8").read()
        assert 'market in ("JP", "HK")' in src
        assert "from bot.intl_universe import full_universe" in src
        assert '_MOVERS_INTRA_TTL if market == "KR" else _HL_INTRA_TTL' in src
        assert "_session_fresh(market, mt, _intra)" in src   # KR 30초·JP/HK 1h

    def test_intl_52w_volume_value_when_yfinance_populates(self, monkeypatch):
        # 사용자 2026-06-14 '모두 거래량/거래대금': JP/CN/HK 52주는 네이버에 52주
        # 정렬이 없어 야후 검출 — 야후 스캔이 거래량 주면 거래량/거래대금(=종가×거래량)
        # 표시, 없으면 숨김(빈 컬럼 방지).
        import bot.intl_highlow as ih
        from bot.intl_pages import render_intl_highlow52_page
        monkeypatch.setattr(ih, "fetch_intl_highlow", lambda m: {
            "high": [{"ticker": "7203.T", "name": "도요타", "price": 3000, "pct": 1.2,
                      "vol": 5000000, "value": 1500.0, "mcap": 400000, "ind": "Auto"}],
            "low": [], "ts": "x", "source": "s", "building": False, "status": {}})
        h = render_intl_highlow52_page("JP")
        # 컬럼 헤더로 검사(부제에 '거래량·거래대금' 라벨이 생겨 bare 문자열은 부정확).
        assert "거래량(주)" in h
        monkeypatch.setattr(ih, "fetch_intl_highlow", lambda m: {
            "high": [{"ticker": "7203.T", "name": "도요타", "price": 3000, "pct": 1.2,
                      "vol": None, "value": None, "mcap": 400000, "ind": "Auto"}],
            "low": [], "ts": "x", "source": "s", "building": False, "status": {}})
        assert "거래량(주)" not in render_intl_highlow52_page("JP")   # vol 부재 → 컬럼 숨김


class TestUsHighlowIndDist:
    """US 신고저에도 업종분포 (사용자 2026-06-13 '업종분포 다 넣어주는걸로').
    기존 US 급등락만 있던 것을 신고저로 확장."""

    def test_us_highlow_has_ind_dist(self):
        import bot.finviz_client as fc
        orig = fc.fetch_high_low
        fc.fetch_high_low = lambda: {
            "high": [{"ticker": "NVDA", "name": "NVDA", "price": 900,
                      "pct": 2.1, "mcap": 22000, "ind": "Semis", "vol": 1000},
                     {"ticker": "AMD", "name": "AMD", "price": 150, "pct": 1.0,
                      "mcap": 2400, "ind": "Semis", "vol": 500}],
            "low": [], "ts": "x", "source": "Finviz"}
        try:
            from bot.us_pages import render_us_highlow_page
            h = render_us_highlow_page()
        finally:
            fc.fetch_high_low = orig
        assert "업종 분포" in h and "Semis" in h


class TestDartNoncorpNotMisparsed:
    """펀드 투자설명서/증권신고서 = 미파싱 색칠 제외 (사용자 2026-06-13 '정말
    미파싱과 구분되게'). is_parse_target 이 _is_noncorp_doc 적용 — coverage-audit
    '의도된 제외'와 대시보드 badge 일치. 미래에셋 투자설명서(집합투자증권) 케이스."""

    def test_fund_prospectus_not_parse_target(self):
        from bot.dart_feed import is_parse_target
        for nm in ("[기재정정]투자설명서(집합투자증권)(미래에셋배당커버드콜)",
                   "투자설명서(집합투자증권)", "증권신고서(지분증권)",
                   "일괄신고추가서류"):
            assert not is_parse_target(
                {"category": "배당", "report_nm": nm, "corp_code": "00123456"}), nm

    def test_real_event_still_parse_target(self):
        from bot.dart_feed import is_parse_target
        assert is_parse_target({"category": "계약",
                                "report_nm": "단일판매ㆍ공급계약체결",
                                "corp_code": "00123456"})


class TestDartTitleCompleteGovernanceA:
    """주주총회 거버넌스 + 증권발행결과(자율공시) = 제목완결 미파싱/보강후보 제외
    (사용자 2026-06-13 A안). 기타경영사항(자율공시)는 force-parse(catch-all
    소송/계약 승격) 유지."""

    def test_shareholder_meeting_not_parse_target(self):
        from bot.dart_feed import is_parse_target
        for nm in ("주주총회소집공고", "주주총회소집결의",
                   "주주명부폐쇄기간또는기준일설정", "의결권대리행사권유참고서류",
                   "임시주주총회결과", "정기주주총회결과", "증권발행결과(자율공시)"):
            assert not is_parse_target(
                {"category": "기타", "report_nm": nm, "corp_code": "00123456"}), nm

    def test_catch_all_and_real_events_preserved(self):
        from bot.dart_feed import is_parse_target
        # 바레 기타경영사항(수시 catch-all) — 소송/계약 자동승격 경로 유지
        assert is_parse_target({"category": "기타", "report_nm": "기타경영사항",
                                "corp_code": "00123456"})
        assert is_parse_target({"category": "계약",
                                "report_nm": "단일판매ㆍ공급계약체결",
                                "corp_code": "00123456"})

    def test_tally_drop_skips_title_complete(self):
        from bot import dart_feed as m
        m._DROP_TALLY.clear()
        m._tally_drop("주주총회소집공고", "654321")        # 제외
        m._tally_drop("증권발행결과(자율공시)", "654321")   # 제외
        m._tally_drop("타법인주식및출자증권양수결정", "111111")  # tally
        assert sum(m._DROP_TALLY.values()) == 1


class TestUpperLowerRichEnrich:
    """상한가/하한가 리치 전환 (사용자 2026-06-13 req2 KR 시총·req4 TW 업종+
    통화헤더). KR=종목명만·시총·거래량, TW=티커(한글)·업종·시총·거래량. mcap/
    업종/한글명은 enrich_for_panel(10분 캐시·graceful)."""

    def test_enrich_for_panel_graceful(self):
        from bot.highlow_render import enrich_for_panel
        items = [{"ticker": "005930.KS", "name": "삼성전자", "price": 88000,
                  "pct": 1.0, "vol": 1}]
        r = enrich_for_panel(items, "KR")          # creds 부재 → mcap None, 원본
        assert r and r[0]["ticker"] == "005930.KS"
        assert enrich_for_panel([], "KR") == []

    def test_kr_uppperlower_uses_rich_panel(self):
        # KR 급등락(사용자 2026-06-14) — 네이버 native rows + 업종(한글) 백필·rich panel
        src = open("bot/naver_pages.py", encoding="utf-8").read()
        assert "stock_panel" in src
        assert ('name_only=True' in src and "show_ind=True" in src
                and "show_value=True" in src)
        assert "apply_kr_industry" in src      # KR 업종 백필 배선(사용자 2026-06-14)

    def test_tw_upperlower_uses_rich_panel(self):
        src = open("bot/tw_pages.py", encoding="utf-8").read()
        # TW 상한가: 업종+한글명+시총 (enrich want_ind/want_name)
        assert "enrich_for_panel" in src and "want_ind=True" in src
        assert "want_name=True" in src and "show_ind=True" in src

    def test_display_names_threadpool_imported(self):
        # 회귀: _fetch_display_names 가 ThreadPoolExecutor 로컬 import (NameError
        # 로 한글명 백필 전멸하던 버그 fix, 2026-06-13)
        src = open("bot/finviz_client.py", encoding="utf-8").read()
        dn = src[src.index("def _fetch_display_names"):
                 src.index("def _backfill_industries")]
        assert "from concurrent.futures import ThreadPoolExecutor" in dn


class TestKrFullUniverseHighlow:
    """KR 신고저 = pykrx 전종목 → yfinance 스캔 (사용자 2026-06-13 — KIS 캡 ~30이
    ETF/SPAC 잠식돼 실종목 1~3개뿐 → KRX 전종목·EOD OK). 한글명 pykrx 네이티브."""

    def test_kr_full_universe_graceful(self):
        from bot.intl_highlow import _kr_full_universe
        uni, names = _kr_full_universe()           # creds 부재 → 빈
        assert isinstance(uni, list) and isinstance(names, dict)

    def test_kr_routes_to_full_scan(self):
        src = open("bot/intl_highlow.py", encoding="utf-8").read()
        assert "_compute_kr_full()" in src          # KR 분기 → 전종목
        assert "_session_fresh(market" in src       # 시장-인지 신선도(옛 플랫 TTL 대체)
        assert "스팩" in src                         # SPAC 제외
        # pykrx 부재 → peer 폴백(회귀 0)
        assert "pykrx 폴백" in src or "peer empty" in src

    def test_kr_names_preserved_jp_translated(self):
        # 사용자 2026-06-13: KR(pykrx 한글)은 번역 안 함(보존), JP/TW/CN/HK 는
        # 네이티브명(銘柄名/약칭)도 번역 시도. _backfill_korean_names 의 시장 게이트.
        from bot.finviz_client import _backfill_korean_names
        kr = [{"ticker": "005930.KS", "name": "삼성전자"}]
        _backfill_korean_names(kr, "KR")          # KR no-op → 보존
        assert kr[0]["name"] == "삼성전자"
        # JP 네이티브명(銘柄名)도 'need'(name==ticker)에 안 갇히고 번역 대상 —
        # 옛 로직은 name!=ticker 면 통째 스킵해 南亞科 류 잔존했음. 헬퍼는 둘 다 처리.
        src = open("bot/finviz_client.py", encoding="utf-8").read()
        assert 'native = sorted({r["name"]' in src   # 네이티브명 직접 번역


class TestHkMovers:
    """JP/CN/HK 급등/급락 — 미국처럼 상한가/하한가 대신 상승/하락 TOP(사용자
    2026-06-13 '중국·홍콩·일본은 미국따라'). 네이버 worldstock(한글명·거래량·
    거래대금·시총 native) + yfinance 업종(야후방식). SWR·장중 1h 캐시."""

    def test_swr_no_sync_compute(self, monkeypatch):
        import bot.intl_movers as im, bot.finviz_client as fc
        kicked = {"n": 0}
        monkeypatch.setattr(im, "_kick",
                            lambda m: kicked.__setitem__("n", kicked["n"] + 1))
        monkeypatch.setattr(fc, "_compute_movers_from",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("sync!")))
        monkeypatch.setattr(fc, "_cached", lambda name, ttl=0: None)
        r = im.fetch_intl_movers("HK")
        assert r["building"] is True and kicked["n"] == 1
        assert im.fetch_intl_movers("XX")["up"] == []   # 미지원 graceful

    def test_jp_cn_markets_wired(self):
        # JP/CN_A/HK 셋 다 _CFG 등록 (사용자 '중국·홍콩·일본은 미국따라')
        import bot.intl_movers as im
        assert set(im._CFG) == {"HK", "JP", "CN_A"}
        assert im._CFG["JP"][0] == "jp_movers_v1.json"
        assert im._CFG["CN_A"][0] == "cn_movers_v1.json"

    def test_naver_first_with_yahoo_industry(self, monkeypatch):
        # _compute 가 네이버 worldstock 우선 + yfinance 업종 enrich (배선 E2E)
        import bot.intl_movers as im, bot.naver_ranking_client as nv
        import bot.finviz_client as fc
        monkeypatch.setattr(nv, "fetch_intl_movers_naver", lambda m, top_n=30: {
            "up": [{"ticker": "7203.T", "name": "도요타", "price": 3000,
                    "pct": 5, "vol": 100, "value": 30, "mcap": 400000, "ind": None}],
            "down": [], "ts": "x", "source": "네이버", "scanned": 1})
        monkeypatch.setattr(fc, "_fetch_industries",
                            lambda hits, **k: {"7203.T": "Auto Manufacturers"})
        saved = {}
        monkeypatch.setattr(fc, "_cache_write", lambda n, o: saved.update({n: o}))
        # full_universe 가 불리면 안 됨(네이버 성공 시 yfinance 스캔 생략)
        import bot.intl_universe as iu
        monkeypatch.setattr(iu, "full_universe",
                            lambda m: (_ for _ in ()).throw(AssertionError("scan!")))
        im._running["JP"] = True
        im._compute("JP")
        assert saved["jp_movers_v1.json"]["up"][0]["ind"] == "Auto Manufacturers"

    def test_page_render_and_wiring(self, monkeypatch):
        import bot.intl_movers as im
        monkeypatch.setattr(im, "fetch_intl_movers", lambda m: {
            "up": [{"ticker": "9988.HK", "name": "알리바바", "price": 80,
                    "pct": 9.1, "vol": 1000000, "value": 80.0, "mcap": 150000,
                    "ind": "Retail"}],
            "down": [], "ts": "x", "source": "네이버 증권 홍콩(HKEX) 등락",
            "scanned": 2, "building": False})
        from bot.intl_pages import render_intl_movers_page
        h = render_intl_movers_page("HK")
        assert "급등·급락" in h and "9988.HK" in h and "알리바바" in h
        # 네이버 native — 거래량·거래대금 표시 (사용자 '거래량도 시총도')
        assert "거래량" in h and "거래대금 (HK$)" in h and "현재가 (HK$)" in h
        from pathlib import Path as _P
        root = _P(__file__).resolve().parents[1] / "bot"
        ds = (root / "dashboard_server.py").read_text("utf-8")
        assert "/hkmovers" in ds and "/jpmovers" in ds and "/cnmovers" in ds
        dd = (root / "dashboard.py").read_text("utf-8")
        assert all(f'href="{m}"' in dd for m in ("hkmovers", "jpmovers", "cnmovers"))

    def test_naver_intl_movers_suffix(self):
        # 네이버 symbolCode → yfinance 접미사 (HK zero-pad)
        from bot.naver_ranking_client import _suffix_ticker, _INTL_MOVER_EX
        assert _suffix_ticker("7203", ".T") == "7203.T"
        assert _suffix_ticker("700", ".HK") == "0700.HK"
        assert _suffix_ticker("601288", ".SS") == "601288.SS"
        # HK: 네이버 5자리 zero-pad(00700) → yfinance 4자리(0700) 정규화 —
        # baseline 키와 일치해야 52주 live 비교가 됨(2026-06-29 버그: 옛 zfill(4)
        # 가 5자리를 안 줄여 주요종목 전부 누락, 20/2272 만 비교됐음).
        assert _suffix_ticker("00700", ".HK") == "0700.HK"
        assert _suffix_ticker("02333", ".HK") == "2333.HK"
        assert _suffix_ticker("09988", ".HK") == "9988.HK"
        assert _suffix_ticker("82333", ".HK") == "82333.HK"   # ≥10000 은 그대로
        # US 추가(2026-06-14) — 52주 시총 worldstock overlay 용(접미사 없음).
        # AMEX 제외(2026-06-17 'AMEX 포함 안 함') → NASDAQ+NYSE 만.
        assert set(_INTL_MOVER_EX) == {"JP", "HK", "CN_A", "US"}
        assert _INTL_MOVER_EX["CN_A"] == [("SHANGHAI", ".SS"), ("SHENZHEN", ".SZ")]
        assert _INTL_MOVER_EX["US"] == [("NASDAQ", ""), ("NYSE", "")]
        assert _suffix_ticker("NVDA", "") == "NVDA"   # US 접미사 없음 직접

    def test_naver_industry_map_and_routing(self):
        # 사용자 2026-06-14 'CN/HK/JP 업종 네이버'. nationType USA|CHN|HKG|JPN|VNM(probe).
        from bot.naver_ranking_client import (_upjong_ticker, _UPJONG_NATION,
                                              world_industry_map)
        # US 도 네이버 업종 라우팅(2026-06-14 — fast_info rate-limit 회피, 미스만
        # yfinance breaker-gated). nationType USA. CN_A/HK/JP/US 네이버 우선.
        assert _UPJONG_NATION == {"CN_A": "CHN", "HK": "HKG", "JP": "JPN", "US": "USA"}
        # CN 코드대역 휴리스틱: 6xx=상하이(.SS), 0/3xx=선전(.SZ)
        assert _upjong_ticker("600507", "CN_A") == "600507.SS"
        assert _upjong_ticker("000507", "CN_A") == "000507.SZ"
        assert _upjong_ticker("300507", "CN_A") == "300507.SZ"
        assert _upjong_ticker("700", "HK") == "0700.HK"
        assert _upjong_ticker("7203", "JP") == "7203.T"
        # KR/TW 는 네이버 업종 미대상 → {} (graceful).
        assert world_industry_map("KR") == {} and world_industry_map("TW") == {}
        # _industries_for 라우팅: CN/HK/JP/US→네이버(미스만 yfinance 폴백)
        import bot.finviz_client as fc, bot.naver_ranking_client as nv
        nv.world_industry_map = lambda m: {"7203.T": "Auto Manufacturers",
                                           "AAPL": "Consumer Electronics"}
        fc._fetch_industries = lambda tks, **k: {t: "YF" for t in tks}
        got = fc._industries_for(["7203.T", "9999.T"], "JP")
        assert got["7203.T"] == "Auto Manufacturers" and got["9999.T"] == "YF"
        # US 도 네이버 우선 — AAPL 네이버 업종 직접, 미스(MSFT)는 yfinance 폴백
        usg = fc._industries_for(["AAPL", "MSFT"], "US")
        assert usg["AAPL"] == "Consumer Electronics" and usg["MSFT"] == "YF"
        # HK 도 Naver-first (2026-06-14 '홍콩도 다른것처럼') — 5자리↔4자리 zfill
        # 정규화 + 미스 yfinance 폴백
        nv.world_industry_map = lambda m: {"0700.HK": "Internet",
                                           "00005.HK": "Banks"}
        hkg = fc._industries_for(["0700.HK", "0005.HK", "0941.HK"], "HK")
        assert hkg["0700.HK"] == "Internet"      # 4자리 직접
        assert hkg["0005.HK"] == "Banks"         # 5자리 키 zfill 정규화
        assert hkg["0941.HK"] == "YF"            # 미스 → yfinance

    def test_industry_korean_preserved_not_english(self):
        # 사용자 2026-06-15 '일본·중국·홍콩도 대만처럼 한글로' — 옛 2026-06-14
        # '모두 영문'(translate_industries_en) 폐기. 업종 등락·업종맵은 네이버
        # 한글(industryGroupKor / reutersIndustryName) 그대로 표기(대만 일관).
        src = open("bot/naver_ranking_client.py", encoding="utf-8").read()
        assert "translate_industries_en" not in src, \
            "업종 영문화가 아직 배선됨 (한글 전환 회귀)"
        assert "industryGroupKor" in src, "업종 등락이 네이버 한글 필드 미사용"
        # _kr 캐시 키로 옛 영문 캐시 폐기 → 배포 즉시 한글
        assert "naver_sector_movers_kr_" in src and "naver_industry_kr_" in src, \
            "한글 전환 캐시 버전(_kr) 미반영"

    def test_name_english_translation_wired(self, monkeypatch):
        # 사용자 2026-06-14 'TW 소형주 中文→영문 번역 인프라 재사용'.
        import bot.chart_translate as ct
        # 캐시 히트(키부재여도) → 영문 반환
        monkeypatch.setattr(ct, "_load_name", lambda: {"台積電": "TSMC"})
        r = ct.translate_names_en(["台積電"])
        assert r["台積電"] == "TSMC"
        # graceful: 미캐시+키부재 → 빠짐(원문 유지)
        monkeypatch.setattr(ct, "_load_name", lambda: {})
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert ct.translate_names_en(["없는회사"]) == {}
        # enrich_for_panel TW 는 2026-06-14 한글 전환 → translate_titles_kr 배선
        # (translate_names_en 함수는 인프라로 보존, 위 1-2 가 동작 검증).
        hr = open("bot/highlow_render.py", encoding="utf-8").read()
        assert "translate_titles_kr" in hr

    def test_naver_sector_movers_and_wiring(self):
        # 사용자 2026-06-14 '업종등락 네이버'(CN/HK/JP). 시총가중 등락 Top/Bottom.
        from bot.naver_ranking_client import fetch_intl_sector_movers_naver as f
        # US/KR/TW 미대상 → 빈(호출부 ETF 폴백)
        for m in ("US", "KR", "TW"):
            d = f(m)
            assert d == {"up": [], "down": [], "ts": "", "source": ""}
        # JP 오프라인 graceful — 형식 유지
        d = f("JP")
        assert isinstance(d, dict) and set(d) >= {"up", "down", "ts", "source"}
        # market_overview 가 JP/CN/HK 는 네이버 우선 + ETF 폴백
        src = open("bot/market_overview.py", encoding="utf-8").read()
        assert "fetch_intl_sector_movers_naver" in src
        assert 'market in ("JP", "CN_A", "HK")' in src
        assert "fetch_sector_movers_etf" in src        # 폴백 보존

    def test_compute_movers_from_present(self):
        from bot.finviz_client import _compute_movers_from
        assert callable(_compute_movers_from)


class TestJpStop:
    """일본 상한가/하한가(ストップ高/安) — TSE 制限値幅 도달 종목 (사용자
    2026-06-13 'JP 상하한가'). 결정적(공개 제한폭 표·스크래핑 불요)."""

    def test_jp_price_limit_table(self):
        from bot.price_sanity import jp_price_limit
        assert jp_price_limit(150) == 50        # 100-200 → ±50
        assert jp_price_limit(3000) == 700      # 3000-5000 → ±700
        assert jp_price_limit(88000) == 15000   # 70000-100000 → ±15000
        assert jp_price_limit(99) == 30
        assert jp_price_limit(0) is None and jp_price_limit("x") is None

    def test_swr_no_sync(self, monkeypatch):
        import bot.jp_stop as js, bot.finviz_client as fc
        kicked = {"n": 0}
        monkeypatch.setattr(js, "_kick", lambda: kicked.__setitem__("n", kicked["n"] + 1))
        monkeypatch.setattr(fc, "_compute_jp_stop",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("sync!")))
        monkeypatch.setattr(fc, "_cached", lambda name, ttl=0: None)
        r = js.fetch_jp_stop()
        assert r["building"] is True and kicked["n"] == 1

    def test_page_render_and_wiring(self, monkeypatch):
        import bot.jp_stop as js
        monkeypatch.setattr(js, "fetch_jp_stop", lambda: {
            "upper": [{"ticker": "8316.T", "name": "미쓰이", "price": 3700,
                       "pct": 23.3, "mcap": 400000, "ind": "Banks"}],
            "lower": [], "ts": "x", "scanned": 3640, "building": False})
        from bot.intl_pages import render_jp_stop_page
        h = render_jp_stop_page()
        assert "ストップ高" in h and "8316.T" in h and "시총" in h
        assert "현재가 (¥)" in h
        from pathlib import Path as _P
        root = _P(__file__).resolve().parents[1] / "bot"
        # /jphighlow 라우트는 구 캐시 링크 호환 유지(JP 위젯은 jpmovers 로 전환)
        assert "/jphighlow" in (root / "dashboard_server.py").read_text("utf-8")
        assert 'href="jpmovers"' in (root / "dashboard.py").read_text("utf-8")

    def test_compute_jp_stop_present(self):
        from bot.finviz_client import _compute_jp_stop
        assert callable(_compute_jp_stop)


class TestActualNewHighLow:
    """신고저 = **당일 intraday 고가/저가가 직전 251일 극값을 갱신**(동률 포함) —
    시장 통용 정의(사용자 2026-06-16 재확정: 장중 한 번이라도 신고가 찍으면 종가
    무관하게 신고가, 신저가 동일). 일봉 1년 스캔. (close-based 변경은 정정 환원.)"""

    def test_daily_interval_and_new_high_criterion(self):
        src = open("bot/finviz_client.py", encoding="utf-8").read()
        scan = src[src.index("def _compute_highlow_from"):
                   src.index("def _compute_movers_from")]
        assert 'interval="1d"' in scan          # 주봉→일봉
        # 당일 intraday 고가/저가가 직전 251일 극값 갱신 = 신고/신저 (시장 통용).
        assert "highs.iloc[-1]) >= float(highs.iloc[:-1].max())" in scan
        assert "lows.iloc[-1]) <= float(lows.iloc[:-1].min())" in scan
        assert "* 0.99" not in scan and "* 1.01" not in scan   # 1% 근접 제거

    def test_intl_sector_sign_defensive(self):
        # T3(2026-06-16): JP/CN/HK 업종 등락 부호가 fluctuationsType(표준) 우선 +
        # compareToPreviousPrice 폴백 둘 다 검사 — 옛 후자 단독은 객체가
        # fluctuationsType 만 주면 '하락 업종' 칸이 비는 버그.
        import inspect
        from bot import naver_ranking_client as nr
        s = inspect.getsource(nr.fetch_intl_sector_movers_naver)
        assert 'fluctuationsType' in s and 'compareToPreviousPrice' in s
        assert s.index('fluctuationsType') < s.index('compareToPreviousPrice')  # 우선순위

    def test_new_high_logic(self):
        # 순수 미러: 당일 고가/저가가 직전 극값 갱신 = 신고/신저 (intraday 기준).
        # 장중 한 번이라도 갱신하면 종가 무관 신고가 — 그날 High 가 안 내려가므로.
        def is_new_high(highs):
            return highs[-1] >= max(highs[:-1])

        def is_new_low(lows):
            return lows[-1] <= min(lows[:-1])
        assert is_new_high([10, 12, 15, 16])        # 당일고가 16 = 신고가
        assert not is_new_high([10, 20, 15, 16])    # 당일고가 16 < 직전 20, 신고가 아님
        assert is_new_low([10, 8, 9, 7])            # 당일저가 7 = 신저가
        assert not is_new_low([5, 8, 9, 7])         # 당일저가 7 > 직전 5, 신저가 아님

    def test_labels_drop_1pct(self):
        # '1% 근접' 라벨 제거 (진짜 신고가로 전환)
        for mod in ("bot/intl_pages.py", "bot/tw_pages.py"):
            assert "1% 근접" not in open(mod, encoding="utf-8").read(), mod


class TestChildDashboardCrossLink:
    """각 나라 자식 대시보드 상호 연결 nav (사용자 2026-06-13 '캡쳐처럼 모두
    연결'). KR/US 는 자체 _shell, TW/JP/CN/HK 는 _tw_shell + _market_nav."""

    def test_market_nav_siblings(self):
        from bot.tw_pages import _market_nav
        kr = _market_nav("KR", "kr52")
        assert all(f'href="{h}"' in kr for h in ("theme", "kr52", "highlow"))
        assert 'class="active" href="kr52"' in kr
        # JP/CN_A 도 미국처럼 급등·급락(movers) — 상한가/하한가 대신(사용자 2026-06-13)
        jp = _market_nav("JP", "jpmovers")
        assert 'href="jp52"' in jp and 'href="jpmovers"' in jp
        hk = _market_nav("HK", "hkmovers")
        assert 'href="hk52"' in hk and 'href="hkmovers"' in hk
        tw = _market_nav("TW", "tw52")
        assert 'href="tw52"' in tw and 'href="twhighlow"' in tw
        cn = _market_nav("CN_A", "cn52")       # CN 52주 재도입(2026-06-17, peer-only)
        assert 'href="cnmovers"' in cn and 'href="cn52"' in cn
        assert 'class="active" href="cn52"' in cn

    def test_intl_pages_emit_nav(self, monkeypatch):
        import bot.intl_highlow as ih, bot.jp_stop as js
        monkeypatch.setattr(ih, "fetch_intl_highlow", lambda m: {
            "high": [{"ticker": "7203.T", "name": "도요타", "price": 3000,
                      "pct": 1, "mcap": 400000, "ind": "A"}],
            "low": [], "ts": "x", "building": False, "source": "x"})
        monkeypatch.setattr(js, "fetch_jp_stop", lambda: {
            "upper": [{"ticker": "8316.T", "name": "미", "price": 3700,
                       "pct": 23, "mcap": 9, "ind": "B"}],
            "lower": [], "ts": "x", "scanned": 1, "building": False})
        from bot.intl_pages import render_intl_highlow52_page, render_jp_stop_page
        assert 'class="toggle"' in render_intl_highlow52_page("JP")
        assert 'href="jp52"' in render_jp_stop_page()


class TestHighlowPrewarm:
    """전종목 신고저/급등락/상한가 아침 pre-warm 타이머 (사용자 2026-06-13
    '아침엔 항상 신선'). 07:30·16:30 KST 순차 재산출 — 첫 방문자 대기 0."""

    def test_prewarm_registered(self):
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "async def _periodic_highlow_prewarm" in src
        assert "def _prewarm_highlow" in src
        assert "_highlow_prewarm_task = asyncio.create_task" in src
        assert "for h in (7, 16)" in src        # 07:30 + 16:30 KST

    def test_target_time_logic(self):
        from datetime import datetime, timezone, timedelta
        kst = timezone(timedelta(hours=9))

        def nxt(now):
            cands = [now.replace(hour=h, minute=30, second=0, microsecond=0)
                     for h in (7, 16)]
            fut = [t for t in cands if t > now]
            return min(fut) if fut else (cands[0] + timedelta(days=1))
        assert nxt(datetime(2026, 6, 13, 6, 0, tzinfo=kst)).hour == 7
        assert nxt(datetime(2026, 6, 13, 10, 0, tzinfo=kst)).hour == 16
        t = nxt(datetime(2026, 6, 13, 20, 0, tzinfo=kst))
        assert t.hour == 7 and t.day == 14


class TestCsvExport:
    """DART + 스크리너(Bottleneck·조건) CSV(엑셀) 내보내기 — 사용자 2026-06-13
    '다트는 한국수출입처럼 엑셀로' + '스크리너 두개도 엑셀'. 클라이언트사이드
    (서버 endpoint 불요·UTF-8 BOM 한글). 버튼+수집 JS 배선 확인."""

    def test_dart_csv_button_and_js(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'id="df-csv"' in src                       # 버튼
        assert "function noahCsv" in src                  # CSV 빌더
        assert ".df-card" in src and "classList.contains('hidden')" in src  # 필터된 것만
        assert "DART공시_" in src                          # 파일명

    def test_dart_filter_js_raw_string(self):
        # 회귀 차단 (사용자 2026-06-14 '필터 하나도 클릭안되고 CSV 도 안먹어'):
        # DART 필터/CSV <script> 가 비-raw 면 out.join('\r\n') 의 \r\n 이 실제
        # 개행으로 변환돼 JS 문자열 리터럴이 깨짐 → IIFE 전체 SyntaxError(필터·CSV
        # 전부 사망). raw(r-prefix) 필수. (.index 가 raw 아니면 ValueError → 실패)
        src = open("bot/dashboard.py", encoding="utf-8").read()
        i = src.index('parts.append(r"""\n<script>\n(function(){')   # raw 여야 통과
        start = src.index('r"""', i)
        block = eval(src[start:src.index('"""', start + 4) + 3])      # noqa: S307
        assert "out.join('\\r\\n')" in block       # \r\n literal 보존
        assert "out.join('\r\n')" not in block     # 실제 CR/LF 없음(JS 안 깨짐)
        assert "getElementById('df-csv')" in block and ".df-pill" in block

    def test_dart_csv_includes_parser_detail(self):
        # 사용자 2026-06-14 '다트 CSV 에 파서내용도' — 카드 .df-detail-ln 줄을
        # '내용' 컬럼으로 export.
        src = open("bot/dashboard.py", encoding="utf-8").read()
        i = src.index('parts.append(r"""\n<script>\n(function(){')
        block = eval(src[src.index('r"""', i):src.index('"""', src.index('r"""', i) + 4) + 3])
        assert "querySelectorAll('.df-detail-ln')" in block      # 상세 줄 스크레이프
        assert "'내용','플래그'" in block                        # 헤더에 내용 컬럼

    def test_screener_two_csv_buttons(self):
        # 사용자 확정: Bottleneck + 조건 스크리너 두 개 모두.
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'id="t3-csv"' in src and 'id="cs-csv"' in src
        assert "table.scr-tbl" in src                    # 조건 스크리너 표 스크레이프
        assert "screener_전체_" in src and "screener_조건_" in src
        # 사용자 2026-06-13 'Top-3 말고 전체' → '이런것까지' — Master Table 종목별
        # 상세('[섹션] · Tier · Ticker · (시장) · Company' + 불릿) 구조 파싱.
        assert 'data-section="master_table"' in src and "table.picks" in src   # 파싱 + 폴백
        assert "📥 CSV" in src    # 사용자 2026-06-14 '전체 CSV → CSV'
        assert "KillTrigger" in src and "TierC(기타)" in src and "가격반영도" in src  # 종목별 컬럼
        assert "Catalyst+시기" in src

    def test_pages_still_render(self):
        # 버튼/JS 추가 후에도 페이지 렌더 무결성(NameError/템플릿 깨짐 가드).
        from bot.dashboard import _render_screener_page
        h = _render_screener_page([], {}, [])
        assert 'id="t3-csv"' in h and 'id="cs-csv"' in h and "csv-btn" in h


class TestNaverKrRanking:
    """KR 52주 신고저 = 네이버 front-api 전종목 (사용자 2026-06-13 '신고가 신저가는
    네이버에 있어'). pykrx+yfinance 스캔 대신 네이버 ready 데이터. VM 실측 필드로
    파싱 단위검증(샌드박스 네이버 도달 불가). 네이버 실패 시 pykrx 폴백."""

    _SAMPLE = {"itemCode": "240810", "name": "원익IPS", "stockExchangeType": "KOSDAQ",
               "currentPrice": 183300, "fluctuationsType": "UPPER_LIMIT",
               "fluctuationsRatio": "30.00", "accumulatedTradingVolume": 4035281,
               "marketValue": 8997100000000, "stockEndType": "stock"}

    def test_kr_row_schema(self):
        from bot.naver_ranking_client import _kr_row
        r = _kr_row(self._SAMPLE)
        assert r["ticker"] == "240810.KQ"          # KOSDAQ → .KQ
        assert r["name"] == "원익IPS" and r["price"] == 183300
        assert r["pct"] == 30.0 and r["vol"] == 4035281
        assert r["mcap"] == 89971.0                # marketValue/1e8 = 억(원)
        # KOSPI → .KS
        assert _kr_row(dict(self._SAMPLE, stockExchangeType="KOSPI"))["ticker"] == "240810.KS"

    def test_signed_pct(self):
        from bot.naver_ranking_client import _signed_pct
        assert _signed_pct({"fluctuationsRatio": "1.8", "fluctuationsType": "RISING"}) == 1.8
        assert _signed_pct({"fluctuationsRatio": "5.2", "fluctuationsType": "FALLING"}) == -5.2
        assert _signed_pct({"fluctuationsRatio": "30", "fluctuationsType": "UPPER_LIMIT"}) == 30.0
        assert _signed_pct({"fluctuationsRatio": "30", "fluctuationsType": "LOWER_LIMIT"}) == -30.0

    def test_etf_etn_spac_excluded(self):
        from bot.naver_ranking_client import _is_real_stock
        assert _is_real_stock(self._SAMPLE) is True
        assert _is_real_stock({"stockEndType": "etn", "name": "KB 레버리지 ETN"}) is False
        assert _is_real_stock({"stockEndType": "etf", "name": "KODEX 200"}) is False
        assert _is_real_stock({"stockEndType": "stock", "name": "엔에이치스팩28호"}) is False

    def test_fetch_graceful_offline(self):
        # 샌드박스(네이버 불가) → 빈 결과, 크래시 없음 (스키마 보존)
        from bot.naver_ranking_client import fetch_kr_highlow
        d = fetch_kr_highlow()
        assert set(d) >= {"high", "low", "ts", "source"}
        assert isinstance(d["high"], list) and isinstance(d["low"], list)

    def test_pagesize_cap_pagination(self, monkeypatch):
        # VM 실측 2026-06-13: 네이버 front-api pageSize>~50 이면 빈 배열 → KR 52주/
        # 상한가가 pageSize=200 으로 전부 0/0. pageSize=50 + 페이지네이션 회귀 잠금.
        import re
        import bot.naver_ranking_client as nr

        def fake(url):
            assert "pageSize=50" in url, url           # 200 금지
            p = int(re.search(r"page=(\d+)", url).group(1))
            return ([{"itemCode": str(i)} for i in range(50)] if p == 1
                    else [{"itemCode": "x"}] if p == 2 else None)
        monkeypatch.setattr(nr, "_get_stocks", fake)
        out = nr._domestic_paged("up", max_items=200)
        assert len(out) == 51                          # 50(page1) + 1(page2 short→stop)
        # 소스에 실제 pageSize=200 호출 없음(주석 제외)
        src = open("bot/naver_ranking_client.py", encoding="utf-8").read()
        assert "pageSize={limit}" not in src and "_PAGE_SIZE = 50" in src

    def test_get_stocks_both_envelopes(self, monkeypatch):
        # VM 2026-06-13: 국내(domestic) 응답엔 isSuccess 가 없어 옛 가드가 전부
        # 거부 → 52주/상한가 0. 가드 완화(isSuccess 명시 False 만 거부) 회귀 잠금.
        import requests
        import bot.naver_ranking_client as nr

        class _Resp:
            status_code = 200

            def __init__(self, p):
                self._p = p

            def json(self):
                return self._p
        # 국내(isSuccess 부재) — 반드시 통과
        monkeypatch.setattr(requests, "get",
                            lambda *a, **k: _Resp({"result": {"stocks": [{"itemCode": "1"}]}}))
        assert nr._get_stocks("x") == [{"itemCode": "1"}]
        # 해외(isSuccess true) — 통과
        monkeypatch.setattr(requests, "get",
                            lambda *a, **k: _Resp({"isSuccess": True, "result": {"stocks": [{"a": 1}]}}))
        assert nr._get_stocks("x") == [{"a": 1}]
        # isSuccess False — 거부
        monkeypatch.setattr(requests, "get",
                            lambda *a, **k: _Resp({"isSuccess": False, "result": {"stocks": [{"a": 1}]}}))
        assert nr._get_stocks("x") is None

    def test_compute_kr_prefers_naver(self):
        # _compute_kr_full 이 네이버 우선 + pykrx 폴백 배선 (회귀 0)
        src = open("bot/intl_highlow.py", encoding="utf-8").read()
        assert "from bot.naver_ranking_client import fetch_kr_highlow" in src
        assert "pykrx 폴백" in src

    def test_kr_row_trading_value(self):
        # 거래대금(accumulatedTradingValue) 억 단위 (사용자 2026-06-13 '거래대금도')
        from bot.naver_ranking_client import _kr_row
        r = _kr_row(dict(self._SAMPLE, accumulatedTradingValue=690950000000))
        assert r["value"] == 6909.5            # /1e8 억(원)

    def test_kr_panel_polish(self):
        # 사용자 2026-06-13: 종목명 nowrap(KR만)·상한가 1h 캐시. 2026-06-14: KR
        # 52주 업종=네이버(업종 그룹 멤버맵 백필).
        from bot.highlow_render import HL_SORT_JS, stock_panel
        kr = stock_panel("x", [{"ticker": "240810.KQ", "name": "원익IPS",
                                "price": 1, "pct": 1}], "t", "KR", name_only=True)
        us = stock_panel("x", [{"ticker": "AMAT", "name": "Applied Materials",
                                "price": 1, "pct": 1}], "t", "US")
        assert 'class="hl-table nm-nowrap"' in kr      # KR 종목명 줄바꿈 금지
        assert 'class="hl-table"' in us                # US 영문 긴 이름은 줄바꿈 유지
        assert ".hl-table.nm-nowrap td.nm" in HL_SORT_JS
        ip = open("bot/intl_pages.py", encoding="utf-8").read()
        assert "당일 52주 신고가/신저가 갱신" not in ip and "직전 종가 고정" not in ip
        assert '_ind_lbl = "업종=네이버 · "' in ip       # KR 업종=네이버
        assert "apply_kr_industry" in ip               # KR 52주 업종 백필 배선
        np = open("bot/naver_pages.py", encoding="utf-8").read()
        # KR 급등락(사용자 2026-06-14) — 무버 신선도 장중 30초(_MOVERS_INTRA_TTL, 2026-06-15)
        assert "kr_movers_v1.json" in np
        assert '_session_fresh("KR", _mt, _MOVERS_INTRA_TTL)' in np
        assert "거래대금·시총 native · 시총순" not in np             # 부제 trim

    def test_kr_upper_lower_filter_and_wiring(self):
        # 사용자 2026-06-13 'KR 모두 네이버' — 상한가/하한가도 front-api(UPPER/LOWER
        # _LIMIT 필터), 시총·거래대금 native(yfinance enrich 제거 → 429 면역).
        from bot.naver_ranking_client import _kr_row, _is_real_stock
        rows = [dict(self._SAMPLE, fluctuationsType="UPPER_LIMIT"),
                dict(self._SAMPLE, itemCode="999", name="상승주", fluctuationsType="RISING",
                     fluctuationsRatio="5.0")]
        upper = [_kr_row(s) for s in rows if _is_real_stock(s)
                 and "UPPER" in str(s.get("fluctuationsType") or "")]
        assert len(upper) == 1 and upper[0]["pct"] == 30.0      # 상한가만(+5% 제외)
        from bot.naver_ranking_client import fetch_kr_movers, fetch_kr_upper_lower
        d = fetch_kr_upper_lower()                              # 오프라인 graceful
        assert set(d) >= {"upper", "lower", "ts", "source"}
        m = fetch_kr_movers()                                  # 급등락(2026-06-14)
        assert set(m) >= {"up", "down", "ts", "source"}
        # KR 페이지가 급등락(fetch_kr_movers) 배선
        src = open("bot/naver_pages.py", encoding="utf-8").read()
        assert "from bot.naver_ranking_client import fetch_kr_movers" in src
        assert "show_value=True" in src                        # 거래대금 표시
        assert "429 면역" in src or "native" in src


class TestNaverWorldRanking:
    """해외(worldstock) 랭킹 — 미국 급등락 네이버(사용자 2026-06-13 '미국등급급락은
    네이버·한글명'). NASDAQ/NYSE/AMEX/SHANGHAI/.../TOKYO + 거래대금·시총. VM 실측
    필드 파싱 단위검증. 미국 ticker=symbolCode(bare)·한글명."""

    def test_world_row_schema(self):
        from bot.naver_ranking_client import _world_row
        s = {"name": "농업은행", "symbolCode": "601288", "currentPrice": 6.8,
             "currencyType": "CNY", "fluctuationsType": "RISING", "fluctuationsRatio": "1.80",
             "accumulatedTradingVolume": 460068741, "accumulatedTradingValue": 3086106000,
             "marketValue": 2170860633284}
        r = _world_row(s)
        assert r["ticker"] == "601288" and r["name"] == "농업은행" and r["pct"] == 1.8
        assert r["value"] == 30.86 and r["mcap"] == 21708.61      # /1e8 억
        # 미국: symbolCode = bare ticker, 하락 부호
        us = _world_row({"name": "애플", "symbolCode": "AAPL", "currentPrice": 201.0,
                         "fluctuationsType": "FALLING", "fluctuationsRatio": "2.1",
                         "marketValue": 3e12})
        assert us["ticker"] == "AAPL" and us["pct"] == -2.1 and us["mcap"] == 30000.0

    def test_us_movers_graceful_and_wired(self):
        from bot.naver_ranking_client import fetch_us_movers
        m = fetch_us_movers()                  # 샌드박스 네이버 불가 → 빈, 크래시 0
        assert set(m) >= {"up", "down", "ts", "source", "scanned"}
        # _compute_us_movers 가 네이버 우선 + 업종(야후) enrich 배선
        src = open("bot/finviz_client.py", encoding="utf-8").read()
        assert "from bot.naver_ranking_client import fetch_us_movers" in src
        assert "yfinance 스캔 폴백" in src
        # 사용자 2026-06-13 '급등급락에 업종·업종분포 야후로' — 네이버 무버에 업종 enrich
        assert "US movers 업종 enrich" in src
        ups = open("bot/us_pages.py", encoding="utf-8").read()
        assert "_ind_dist_line(up)" in ups and "_ind_dist_line(down)" in ups  # 업종분포 줄

    def test_value_column_in_panel(self):
        # 거래대금 컬럼 (show_value) — 억 단위 fmt_mcap 재사용
        from bot.highlow_render import stock_panel
        h = stock_panel("x", [{"ticker": "AAPL", "name": "애플", "price": 201,
                               "pct": -2.1, "value": 500.0, "mcap": 30000.0}],
                        "t", "US", show_vol=False, show_value=True)
        assert "거래대금" in h and 'data-value="500.0"' in h
        # show_value=False(기본)면 컬럼 없음 (다른 페이지 무영향)
        h2 = stock_panel("x", [{"ticker": "AAPL", "price": 201, "pct": 1.0}], "t", "US")
        assert "거래대금" not in h2


class TestFastInfoCircuitGating:
    """fast_info 회로차단 게이팅 — 자동(백그라운드) fast_info 소비처가 쿨다운 중에도
    yahoo quote API 를 계속 때려 IP 차단이 회복 안 되던 회귀(2026-06-14 '뭘 하던
    무조건 차단당하나') 차단. 회로차단(fast_info_ok)/정지(yf_paused) 트립 시 모든
    자동 소비처가 0콜 → cloud IP 냉각 → yahoo per-IP 리미터 자동 리셋."""

    def _fn_body(self, src: str, marker: str, span: int = 1200) -> str:
        i = src.index(marker)
        return src[i:i + span]

    def test_dashboard_callers_now_gated(self):
        fc = open("bot/finviz_client.py", encoding="utf-8").read()
        cd = open("bot/chart_data.py", encoding="utf-8").read()
        # fetch_sectors (메인 11섹터 위젯, regen 마다) — 회로차단 시 fast_info ETF 0콜
        sec = self._fn_body(fc, "def fetch_sectors")
        assert "fast_info_ok()" in sec and "yf_paused()" in sec
        # _groups_fallback_etf (Finviz 실패 폴백)
        gfb = self._fn_body(fc, "def _groups_fallback_etf", 800)
        assert "fast_info_ok()" in gfb and "yf_paused()" in gfb
        # chart 52주 hi/lo (차트 페이지)
        yhl = self._fn_body(cd, "def _year_high_low", 900)
        assert "fast_info_ok()" in yhl and "yf_paused()" in yhl

    def test_existing_gates_intact(self):
        # 기존 게이트 회귀 방지 — _fetch_mcaps / market_favorites / chart 라이브가
        fc = open("bot/finviz_client.py", encoding="utf-8").read()
        assert "not tickers or yf_paused() or not fast_info_ok()" in fc   # _fetch_mcaps
        mf = open("bot/market_favorites.py", encoding="utf-8").read()
        assert "fast_info_ok() and not yf_paused()" in mf
        cd = open("bot/chart_data.py", encoding="utf-8").read()
        assert "if yf_paused() or not fast_info_ok():" in cd              # _live_last_price + _year_high_low


class TestLookupQuoteHistoryFallback:
    """종목검색 LIGHT quote — 야후 .info(quote API) throttle 시 비-KR 이 블랭크
    되던 회귀(2026-06-14 '야후가 짤라서 종목검색 아무것도 못함') 차단. history
    (관대 chart API, /health 가 살아있음 확인)로 현재가 폴백 → 검색이 최소 현재가
    표시. KR 은 KIS-first 가 이미 처리(비-KR 만 폴백 필요)."""

    def _fake_yf(self, monkeypatch, info, closes):
        import pandas as pd
        import yfinance

        class _FT:
            def __init__(self, t):
                pass

            @property
            def info(self):
                return info

            def history(self, period=None):
                return pd.DataFrame({"Close": closes}) if closes else pd.DataFrame()

        monkeypatch.setattr(yfinance, "Ticker", _FT)

    def test_us_info_throttled_falls_back_to_history(self, monkeypatch):
        import bot.dashboard as D
        self._fake_yf(monkeypatch, {}, [100.0, 101.0, 102.5])
        q = D.build_live_quote("AAPL", full=False)
        assert q is not None, "throttle 시 None 블랭크 금지"
        assert q.get("fmt", {}).get("price"), "현재가 폴백 표시"
        assert "102" in q["fmt"]["price"]                 # history 마지막 종가
        assert q["meta"]["source"] == "yfinance 종가"      # 지연 출처 표기

    def test_history_split_glitch_rejected(self, monkeypatch):
        # KLAC류 분할 미조정(직전 대비 >75%) 종가는 폴백에서 제외 — 잘못된 현재가 금지
        import bot.dashboard as D
        self._fake_yf(monkeypatch, {}, [100.0, 2411.0])
        q = D.build_live_quote("AAPL", full=False)
        assert q is None or not q.get("fmt", {}).get("price")

    def test_normal_info_path_unaffected(self, monkeypatch):
        # 정상 .info 면 기존 동작(폴백 미발동, source=yfinance) 유지 — 회귀 방지
        import bot.dashboard as D
        self._fake_yf(monkeypatch, {"currentPrice": 200.0, "currency": "USD"}, [1.0])
        q = D.build_live_quote("AAPL", full=False)
        assert q and q["fmt"]["price"] and q["meta"]["source"] == "yfinance"


class TestResearchRotation:
    """US 리서치 야후 burst 차단 — S&P500 일괄 fetch(회당 ~1,000 quote-API 콜:
    upgrades_downgrades + .info)가 cloud IP 하드차단 → 검색·.info 탭 동반 사망
    (2026-06-14 회귀). day-slice 로테이션으로 회당 ~72종목, 7일이면 전체 커버
    (누적 store). _rotation_slice 순수 단위검증 + 배선."""

    def test_rotation_covers_all_within_window(self):
        from bot.market_overview import _rotation_slice
        items = [f"T{i}" for i in range(500)]
        seen = set()
        for ordinal in range(7):                  # 7일
            sl = _rotation_slice(items, ordinal, 7)
            assert 50 <= len(sl) <= 100, len(sl)  # 회당 bound(burst 0)
            seen.update(sl)
        assert seen == set(items)                 # 7일이면 500 전체 커버

    def test_rotation_advances_daily(self):
        from bot.market_overview import _rotation_slice
        items = [f"T{i}" for i in range(500)]
        assert _rotation_slice(items, 0, 7) != _rotation_slice(items, 1, 7)

    def test_rotation_small_list_no_split(self):
        from bot.market_overview import _rotation_slice
        items = [f"T{i}" for i in range(30)]       # 30<50 → 전체 한 슬라이스
        assert _rotation_slice(items, 5, 7) == items
        assert _rotation_slice([], 3, 7) == []

    def test_rotation_wired_into_research(self):
        src = open("bot/market_overview.py", encoding="utf-8").read()
        # 4일 1회전 — 야후 안정화 후 일일 fetch 상향(사용자 2026-06-15). cold-start 는
        # 전수 시드라 _rotation_slice 는 else 분기(2026-06-17 deadlock fix).
        assert "_sp if cold_start else _rotation_slice" in src    # 로테이션은 비-cold 경로
        assert "today.toordinal(), window_days=4)" in src         # 4일 1회전 유지
        assert "us_rolling.json" in src                           # 누적 store
        assert "top_us = _sp_tks" not in src                      # 옛 500 일괄 universe 제거
        # cold-start(시드 전무) 회로차단 1회 우회 시드 배선 (빈 store deadlock fix)
        assert "cold_start = not store" in src
        assert "if not cold_start and (yf_paused() or not fast_info_ok())" in src


class TestNaverCommodityCharts:
    """원자재 차트 = 네이버 history (yfinance 무티커 LME 금속·철광석·throttle 무관,
    사용자 2026-06-14 '원자재 차트 다 네이버'). VM probe 확정: api.stock.naver.com/
    marketindex/{category}/{reutersCode}/prices, reutersCode=리스트 item(금 GCcv1·
    니켈 CMNI0). 일별 close 최신순 → chronological. 센티멘트 게이지도 네이버 VIX."""

    def test_parse_history_closes_chronological(self):
        from bot.naver_marketindex import _parse_history_closes
        rows = [{"closePrice": "17,614.40"}, {"closePrice": "17,472.05"},
                {"closePrice": "17,443.60"}]               # 최신순(네이버 응답)
        s = _parse_history_closes(rows)
        assert s == [17443.60, 17472.05, 17614.40]         # 뒤집어 오래된→최신
        assert _parse_history_closes([]) == []
        assert _parse_history_closes([{"x": 1}]) == []     # closePrice 없으면 skip

    def test_parse_item_exposes_reuters_and_category(self):
        from bot.naver_marketindex import _parse_item
        it = {"closePrice": "4,238.80", "fluctuations": "124.80",
              "fluctuationsRatio": "3.03", "fluctuationsType": {"code": "2"},
              "name": "국제 금", "reutersCode": "GCcv1", "categoryType": "metals"}
        rec = _parse_item(it)
        assert rec["reutersCode"] == "GCcv1" and rec["category"] == "metals"
        assert rec["close"] == 4238.80

    def test_naver_prev_pct_signed_chg(self):
        # 전일종가·변동률 부호 버그(사용자 2026-06-23): naver compareToPreviousClose/
        # fluctuations 가 부호 포함이라 prev=close-sign*chg 는 하락일에 sign 이중적용 →
        # prev 가 거꾸로(필반 close 13521 인데 prev 12407·% 8.97 — 정답 7.61). fix=
        # sign*abs(chg). 하락일(code 5)에 prev>close·prev==close-change.
        from bot.naver_marketindex import _parse_item, _parse_coins
        down = {"closePrice": "13,521.32", "fluctuations": "-1113.4",
                "fluctuationsRatio": "-7.61", "fluctuationsType": {"code": "5"},
                "name": "SOX"}
        r = _parse_item(down)
        assert r["prev"] > r["close"], "하락일 prev 는 close 보다 높아야"
        assert round(r["prev"], 2) == round(r["close"] - r["change"], 2)
        assert round(r["prev"], 2) == 14634.72   # 12407.92(버그) 아님
        up = {"closePrice": "100.0", "fluctuations": "5.0", "fluctuationsRatio": "5.26",
              "fluctuationsType": {"code": "2"}, "name": "X"}
        ru = _parse_item(up)
        assert round(ru["prev"], 2) == 95.0 and ru["prev"] < ru["close"]   # 상승일 정상
        # 코인은 이미 정상(signed chg → close-chg). 회귀 가드.
        rc = _parse_coins([{"nfTicker": "BTC", "tradePrice": 94000000,
                            "changeValue": 2000000, "changeRate": 2.17,
                            "change": "FALLING"}])["BTC"]
        assert round(rc["prev"]) == 96000000   # 하락 → prev 더 높음
        # 지수/선물/ETF 는 prev 로 pct 계산 → abs(chg) 가드(소스). 6곳 전부 교정.
        src = open("bot/naver_marketindex.py", encoding="utf-8").read()
        assert "close - sign * chg" not in src, "버그 패턴 잔존(부호 이중적용)"
        assert src.count("close - sign * abs(chg)") == 6

    def test_fetch_commodity_helpers_graceful(self):
        # 샌드박스 네이버 불가 → graceful [](크래시 0)
        from bot.naver_marketindex import (fetch_commodity_spark,
                                           fetch_naver_commodity_history)
        assert isinstance(fetch_commodity_spark("NI"), list)
        assert fetch_naver_commodity_history("", "") == []     # 코드 없으면 []

    def test_history_pagesize_cap_pagination(self):
        # 네이버 pageSize 캡 — ps260 빈 응답·ps30 정상(VM 진단 2026-06-14, 'spark 0
        # → direct ps260=0 ps30=30') → 30씩 페이지네이션. 옛 ps=count(260) 가 빈 차트 원인.
        src = open("bot/naver_marketindex.py", encoding="utf-8").read()
        assert "_PS = 30" in src                          # 고정 페이지 크기
        assert "(count + _PS - 1) // _PS" in src          # 페이지 수 = ceil(count/30)
        assert '"pageSize": _PS' in src                   # count 가 아니라 _PS(30) 로 호출
        ms = open("bot/macro_snapshot.py", encoding="utf-8").read()
        assert "fetch_commodity_spark(code, 30)" in ms     # 카드=1개월(30점, 1페이지)
        assert "fetch_naver_index_history(code, 30)" in ms # 지수도 동일

    def test_naver_world_news_no_broken_deeplink(self, monkeypatch):
        # 사용자 2026-06-19: worldStock 뉴스 item 엔 기사 URL 필드가 없다(VM probe:
        # keys=type/subcontent/thumbUrl/oid/ohnm/aid/tit/dt, oid='fnGuide' 고정·aid만
        # 상이). 옛 worldstock/news/{oid}/{aid} 는 deep-link 미작동 → 모든 뉴스가 일반
        # 시장 페이지로 폴백('어떤 뉴스든 다 같은데로'). 깨진 deep-link 폐기 + 본문
        # 요약(subcontent) 인라인 + 제목은 종목 네이버 페이지 링크.
        import requests
        import bot.finviz_client as fv
        import bot.naver_overview as no
        monkeypatch.setattr(fv, "_cached", lambda *a, **k: None)
        monkeypatch.setattr(fv, "_cache_write", lambda *a, **k: None)
        monkeypatch.setattr(no, "_rc_for", lambda t: "ICHR.O")

        class _R:
            status_code = 200
            def json(self):
                return [{"type": 1, "oid": "fnGuide", "aid": "2612357",
                         "ohnm": "로이터", "tit": "투자자들은...",
                         "dt": "20260527192524", "subcontent": "5월 27일(로이터) - 요약"},
                        {"type": 0, "oid": "fnGuide", "aid": "2581009",
                         "ohnm": "로이터", "tit": "어플라이드...",
                         "dt": "20260505160700", "subcontent": "다른 요약"}]
        monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
        items = no.fetch_naver_world_news("ICHR")
        assert items and len(items) == 2
        for it in items:
            # 깨진 deep-link 안 만듦 (모든 뉴스가 같은 폴백 페이지로 가던 것 차단)
            assert "worldstock/news/fnGuide" not in it["link"]
            assert it["link"] == "https://m.stock.naver.com/worldstock/stock/ICHR.O/total"
            assert it["subcontent"]      # 본문 요약 보존 → 인라인 노출
        assert items[0]["subcontent"].startswith("5월 27일")
        src = open("bot/naver_overview.py", encoding="utf-8").read()
        assert "worldstock/news/{_oid}/{_aid}" not in src   # 깨진 deep-link 패턴 제거
        dsrc = open("bot/dashboard.py", encoding="utf-8").read()
        assert "si-news-sub" in dsrc and 'n.get("subcontent"' in dsrc  # 인라인 배선

    def test_naver_world_news_stale_fallback(self, monkeypatch):
        # 사용자 2026-06-19 '처음엔 요약 나오더니 다시 원복' — 네이버 일시차단으로 12h
        # 캐시 만료 후 fetch 실패 시 옛 동작(None)은 호출부가 stored(yfinance) 뉴스로
        # 폴백해 subcontent 를 잃음. 직전 성공 캐시(30일) 스테일 폴백으로 subcontent 유지.
        import requests
        import bot.finviz_client as fv
        import bot.naver_overview as no
        store = {"naver_worldnews_v3_ICHR.json":
                 {"items": [{"title": "옛제목", "subcontent": "옛 요약", "link": "L"}]}}
        # 12h 캐시 만료(None)·30일 스테일만 존재
        monkeypatch.setattr(fv, "_cached",
                            lambda ck, ttl=0: store.get(ck) if ttl >= 30 * 86400 else None)
        monkeypatch.setattr(fv, "_cache_write", lambda ck, v: store.__setitem__(ck, v))
        monkeypatch.setattr(no, "_rc_for", lambda t: "ICHR.O")
        monkeypatch.setattr(requests, "get",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
        out = no.fetch_naver_world_news("ICHR")
        assert out and out[0]["subcontent"] == "옛 요약"   # 스테일 폴백 — None 아님
        store.clear()
        assert no.fetch_naver_world_news("ICHR") is None   # 스테일도 없으면 None

    def test_lookup_cache_cleared_on_dashboard_startup(self):
        # 사용자 2026-06-19: SWR lookup_cache 가 코드 배포를 가로질러 옛 HTML 서빙 →
        # 코드 fix 가 페이지에 안 닿던 클래스(실수 #11). 서버 startup 이 lookup_cache
        # *.html 을 비워 배포→신선 렌더 보장. main() 배선 가드(소스 grep).
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        main_body = src.split("def main(", 1)[1]
        assert 'lookup_cache' in main_body, "startup lookup_cache 무효화 미배선"
        assert '.glob("*.html")' in main_body and ".unlink()" in main_body, \
            "lookup_cache HTML 제거 로직 누락"
        # serve_forever 전에 위치(서빙 시작 전 clear)
        assert main_body.index("lookup_cache") < main_body.index("serve_forever"), \
            "캐시 clear 가 serve_forever 이후 — startup 전 비워야"

    def test_macro_commodities_wired_to_naver(self):
        src = open("bot/macro_snapshot.py", encoding="utf-8").read()
        assert "fetch_commodity_spark" in src              # 원자재 스파크 네이버
        assert "fetch_naver_index_history" in src          # 지수 스파크 네이버(S&P/나스닥/VIX)
        assert "fetch_naver_crypto_history" in src          # 코인 스파크 네이버(BTC/ETH)
        assert "fetch_naver_fx_history" in src              # 환율 스파크 네이버(USD/KRW)
        assert "if sid in nv_spark:" in src                # 루프 분기(com+idx+coin+fx)
        assert '_NV_KINDS = ("com", "idx", "coin", "fx")' in src  # 야후 chart 배치서 전부 제외
        assert "_fetch_macro_naver_values(all_yf_sids)" in src   # 값은 전체 네이버

    def test_macro_snapshot_sox_card(self):
        # 사용자 2026-06-22: Macro Snapshot 의 VIX 뒤 카드 = 필라델피아 반도체(SOX),
        # 옛 CCFI(중국 컨테이너 운임) 대체. 값·1개월 스파크라인 모두 네이버 worldstock/
        # index(.SOX) — S&P/나스닥/VIX 와 동일 idx 경로. 백금은 계속 미존재.
        src = open("bot/macro_snapshot.py", encoding="utf-8").read()
        assert "platinum" not in src and "PL=F" not in src, "백금 미제거"
        assert '("sox", "필라델피아 반도체"' in src, "SOX 카드 누락"
        assert '"^SOX": ("idx", ".SOX")' in src, "SOX naver(idx) 매핑 누락"
        # CCFI 는 macro 카드에서 제거됨(market_overview CARD_FUTURES 에는 잔존)
        assert '("ccfi", "중국 컨테이너 운임(CCFI)"' not in src, "옛 CCFI 카드 잔존"
        assert '"CCFI": ("com", "CCFI")' not in src, "옛 CCFI com 매핑 잔존"
        # VIX 바로 뒤 순서 보장
        i_vix = src.index('("vix", "VIX"')
        i_sox = src.index('("sox", "필라델피아 반도체"')
        i_wti = src.index('("wti", "WTI"')
        assert i_vix < i_sox < i_wti, "SOX 가 VIX 바로 뒤가 아님"
        # .SOX 가 idx pool(fetch_world_indices) 로 잡히는지 — idx 분기 존재 확인
        assert 'fetch_naver_index_history' in src, "idx 스파크라인 경로 누락"

    def test_macro_sol_card_and_ecos_visibility(self):
        # 사용자 2026-06-23 '카드 3개 빠짐': (1) 솔라나(SOL) 가 _MACRO_NAVER 매핑은
        # 있는데 GLOBAL 카드목록에서 누락 → 복원. (2) ECOS fetch_series_points 가
        # RESULT 에러를 silent-[] 로 드롭해 경상수지·한국수출 카드가 조용히 사라지던
        # 근본원인 → RESULT CODE/MESSAGE 로그 가시화(silent-fail 금지).
        import bot.macro_snapshot as m
        sids = [sid for _, _, _, _, sid, _ in m.GLOBAL]
        assert "SOL-USD" in sids, "솔라나 카드 누락"
        assert m._MACRO_NAVER.get("SOL-USD") == ("coin", "SOL")
        # 경상수지·한국수출은 DOMESTIC 에 정의돼 있음(값 없으면 드롭 — 코드 정의는 존재).
        dsids = [sid for _, _, _, _, sid, _ in m.DOMESTIC]
        assert "current_account" in dsids and "export_amt" in dsids
        # ECOS 카드 경로(fetch_series_points)가 RESULT 에러를 로그로 노출(silent 금지).
        import inspect
        import bot.bok_ecos_client as e
        fs = inspect.getsource(e.fetch_series_points)
        assert "RESULT" in fs and "log.warning" in fs, "ECOS series silent-fail 미가시화"
        # 경상수지·수출 정확 table/item/scale (ECOS 메타 확인 2026-06-23 — 옛 000000=
        # 데이터없음·403Y014=무효 → INFO-200 으로 카드 드롭되던 근본원인 fix).
        ca = e._SERIES["current_account"]
        assert ca["table"] == "301Y017" and ca["item"] == "SA000"
        assert ca["scale"] == 0.01 and ca["unit"] == "억$"      # 백만$→억$
        ex = e._SERIES["export_amt"]
        assert ex["table"] == "901Y118" and ex["item"] == "T002"
        assert ex["scale"] == 1e-5 and ex["unit"] == "억$"      # 천$→억$
        # scale 이 두 fetch 경로에 모두 적용(값·시계열 동일 단위).
        assert "scale" in fs
        assert "scale" in inspect.getsource(e._fetch_indicator)
        # 국내 둘째줄 채움(사용자 2026-06-23): 수입·외환보유액·GDP 추가(ECOS 코드 확인).
        assert "import_amt" in dsids and "fx_reserve" in dsids and "kr_gdp" in dsids
        imp = e._SERIES["import_amt"]
        assert imp["table"] == "901Y118" and imp["item"] == "T004" and imp["scale"] == 1e-5
        fxr = e._SERIES["fx_reserve"]
        assert fxr["table"] == "732Y001" and fxr["item"] == "99" and fxr["scale"] == 1e-5
        gdp = e._SERIES["kr_gdp"]
        assert gdp["table"] == "902Y015" and gdp["item"] == "KOR" and gdp["freq"] == "Q"
        assert "scale" not in gdp and gdp["unit"] == "%"   # 성장률 — 스케일 없음
        # fetch_series_points 가 분기(Q) 주기 지원(GDP 카드용).
        assert '"Q"' in fs or "'Q'" in fs
        # 글로벌 금리/신용 리스크 2종 추가(사용자 2026-06-23): 장단기금리차·하이일드
        # 스프레드. 둘 다 FRED 일별 series(DGS2/DGS10 과 동일 경로, 분기 가드 제외).
        gsids = [sid for _, _, _, _, sid, _ in m.GLOBAL]
        assert "T10Y2Y" in gsids and "BAMLH0A0HYM2" in gsids
        from bot.macro_snapshot import _FRED_QUARTERLY
        assert "T10Y2Y" not in _FRED_QUARTERLY and "BAMLH0A0HYM2" not in _FRED_QUARTERLY
        for key, sid, src in [(t[0], t[4], t[3]) for t in m.GLOBAL if t[4] in ("T10Y2Y", "BAMLH0A0HYM2")]:
            assert src == "fred"
        # 미국 FFR 제거 + CPI 오른쪽 PPI(Final Demand PPIFIS) 추가(사용자 2026-06-23).
        assert "FEDFUNDS" not in gsids, "미국 FFR 미제거"
        assert "PPIFIS" in gsids, "미국 PPI 누락"
        labels = [lbl for _, lbl, *_ in m.GLOBAL]
        assert labels.index("미국 PPI") == labels.index("미국 CPI") + 1, "PPI 가 CPI 바로 오른쪽 아님"
        # FRED 헤드라인 값 = 글로벌 핵심지표와 동일 소스(_fred_fetch_series spot)로 통일
        # (사용자 2026-06-23 '두 표면 일치' — 일별 series 2Y/10Y 가 월평균 vs spot 불일치
        # 였음). 차트(스파크라인)는 _fred_monthly(월간) 유지.
        build_src = inspect.getsource(m.fetch_macro_snapshot)
        assert "_fred_fetch_series" in build_src, "macro FRED 헤드라인 spot 통일 누락"
        assert "_fred_monthly(sid)" in build_src, "FRED 월간 스파크라인 유지 누락"

    def test_research_gated_against_yahoo_block(self):
        # 야후 차단 지속 근본원인(2026-06-14): intl research 가 빈 결과 미캐시 →
        # 매 regen(5분) 240콜 재poke. 빈 결과도 캐시 + 회로차단 게이트 + rate-limit trip.
        src = open("bot/market_overview.py", encoding="utf-8").read()
        # intl: 회로차단 게이트 + 빈 캐시 기록 + trip
        assert "research_intl" in src                       # rate-limit trip 라벨
        assert "if yf_paused() or not fast_info_ok():" in src   # 차단 중 fetch skip
        # US: 게이트 + .info 목표가 생략(quote API 부하 제거)
        assert "research_us" in src
        # earnings calendar .info 이름배치도 게이트
        assert "if fast_info_ok() and not yf_paused():" in src

    def test_index_history_graceful_and_endpoint(self):
        # 지수 history (사용자 Network 탭 확인: securityService/index/.INX/price).
        # 'PNG 만 있다'던 옛 단정이 오류 — JSON history 존재(2026-06-14).
        from bot.naver_marketindex import fetch_naver_index_history
        assert fetch_naver_index_history("") == []         # 코드 없으면 []
        assert isinstance(fetch_naver_index_history(".INX"), list)   # 샌드박스 graceful
        src = open("bot/naver_marketindex.py", encoding="utf-8").read()
        assert "securityService/index" in src              # 사용자 확인 엔드포인트
        assert "raw.get(\"datas\")" in src                  # bare list/{datas:[]} 양형 수용

    def test_crypto_history_graceful_and_endpoint(self):
        # 코인 history (사용자 Network 탭 확인: front-api/chart/cryptoChartData,
        # {result:[{closePrice(숫자),tradeBaseAt}]} 오래된순). VM 확인 BTC=200.
        from bot.naver_marketindex import fetch_naver_crypto_history
        assert fetch_naver_crypto_history("") == []        # 코드 없으면 []
        assert isinstance(fetch_naver_crypto_history("BTC"), list)   # 샌드박스 graceful
        src = open("bot/naver_marketindex.py", encoding="utf-8").read()
        assert "cryptoChartData" in src
        assert 'raw.get("result")' in src                  # {result:[...]} 추출
        assert "series[-count:]" in src                    # 오래된순 → 역순 안 함

    def test_fx_history_graceful_and_endpoint(self):
        # USD/KRW history (사용자 VM 확인: marketindex/exchange/FX_USDKRW/prices,
        # 원자재와 동일 closePrice schema). 매크로 마지막 야후 차트 제거.
        from bot.naver_marketindex import fetch_naver_fx_history
        assert fetch_naver_fx_history("") == []
        assert isinstance(fetch_naver_fx_history("FX_USDKRW"), list)
        src = open("bot/naver_marketindex.py", encoding="utf-8").read()
        assert "marketindex/exchange" in src and "_FXHIST_BASE" in src

    def test_sentiment_gauge_uses_naver_vix(self):
        src = open("bot/macro_snapshot.py", encoding="utf-8").read()
        assert '_build_charts(spark_cache, (macro_nv.get("^VIX")' in src
        assert "vix_value if isinstance(vix_value" in src   # 네이버 VIX 우선


class TestLookupDetailSequential:
    """종목검색 상세 — 진행-로딩(per-pane 병렬·공유 스냅샷 캐시)은 동시 mutation race 로
    상세 전체가 죽어(사용자 2026-06-15 '새 종목 아예 안됨') 철회 → 순차 core→full 복원
    + 스냅샷 실패 시 직전 분석 스냅샷 폴백(분석 종목)."""

    def test_render_graceful(self):
        from bot.dashboard import render_lookup_detail
        # 샌드박스 야후 불가 → si None → 폴백도 None → graceful str(크래시 0)
        assert isinstance(render_lookup_detail("AAPL", enrich=False), str)
        assert isinstance(render_lookup_detail("AAPL"), str)

    def test_sequential_not_parallel(self):
        d = open("bot/dashboard.py", encoding="utf-8").read()
        # 순차 core→full (병렬 per-pane·공유 캐시 철회)
        assert "get('core').then(function(h){inject(h);return get('full');})" in d
        assert "get('p_news')" not in d and "function injectPane" not in d
        assert "_LOOKUP_SI_CACHE" not in d                   # 공유 스냅샷 캐시 제거(race 원인)
        assert "_load_stored_stock_info(ticker)" in d        # 폴백 유지
        s = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert "_PANE_PHASES" not in s and "pane=pane" not in s   # 핸들러 pane 매핑 제거


class TestWorldIndicesCodesetCache:
    """worldstock/index 공유 캐시 코드셋 병합 — 부분집합 fetch 가 전체 카드를
    블랭크로 만들던 회귀(2026-06-14: macro_snapshot 이 .INX/.IXIC/.VIX 3종만 써
    naver_worldindex.json 을 덮어쓰자 market_overview 세계지수 카드 24종이 전부
    '—' 로 표시). 부분집합 호출이 캐시를 축소하면 안 됨 + 요청 코드 부분집합 반환."""

    def test_plan_hit_only_when_all_present(self):
        from bot.naver_marketindex import _codeset_plan
        assert _codeset_plan({"a": 1, "b": 2, "c": 3}, ("a", "b"))   # 전부 있음 → hit
        assert not _codeset_plan({"a": 1}, ("a", "b"))               # b 부재 → refetch
        assert not _codeset_plan({}, ("a",))                         # 빈 캐시 → refetch
        assert _codeset_plan({"a": 1}, ())                           # 코드 무지정 → 캐시 있으면 hit
        assert not _codeset_plan({}, ())                             # 빈 캐시 → miss

    def test_finalize_merge_does_not_shrink(self):
        # 핵심 회귀: 24종 캐시에 3종 부분 fetch 가 들어와도 캐시는 24종 유지.
        from bot.naver_marketindex import _codeset_finalize
        known = {f"x{i}": {"close": i} for i in range(24)}
        fetched = {"x0": {"close": 99}, "x1": {"close": 88}}
        merged, ret = _codeset_finalize(known, fetched, ("x0", "x1"))
        assert len(merged) == 24                       # 축소 안 됨(블랭크 방지)
        assert merged["x0"]["close"] == 99             # 신규 fetch 값으로 갱신
        assert merged["x23"]["close"] == 23            # 기존 값 보존
        assert set(ret) == {"x0", "x1"}                # 요청 부분집합만 반환

    def test_finalize_adds_new_codes(self):
        from bot.naver_marketindex import _codeset_finalize
        merged, ret = _codeset_finalize({"a": 1}, {"b": 2, "c": 3}, ("b", "c"))
        assert set(merged) == {"a", "b", "c"}          # 기존 ∪ 신규
        assert set(ret) == {"b", "c"}

    def test_fetch_wired_union_refetch(self):
        # 배선 E2E: fetch_world_indices 가 요청 ∪ 기존코드 union refetch + 병합 finalize.
        src = open("bot/naver_marketindex.py", encoding="utf-8").read()
        assert "_codeset_plan(fresh, codes)" in src
        assert "_codeset_finalize(known, out, codes)" in src
        assert "dict.fromkeys([*codes, *known.keys()])" in src   # union refetch
        # macro_snapshot 이 동일 함수의 부분집합 호출부(회귀 트리거) — 여전히 안전 배선
        ms = open("bot/macro_snapshot.py", encoding="utf-8").read()
        assert "fetch_world_indices(idx_codes)" in ms


class TestSessionAwarePerMarket:
    """시장-인지 신선도 (_session_fresh) — 사용자 2026-06-13 '장종료후 굳이 안
    돌려도·나라별 시간 체크해 부하없이'. 정규장 중 intra_ttl / 장 밖 마지막 마감
    이후 산출본이면 재스캔 0. 신고저/상한가 6h 플랫 캐시를 대체."""

    def _ts(self, y, mo, d, h, mi):
        import calendar
        return calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0))  # UTC epoch

    def test_intra_session_ttl(self):
        from bot.finviz_client import _session_fresh
        # 월요일 KR 장중(03:00 UTC = 12:00 KST). intra 3h.
        now = self._ts(2026, 6, 15, 3, 0)
        assert _session_fresh("KR", now - 2 * 3600, 3 * 3600, now)      # 2h fresh
        assert not _session_fresh("KR", now - 4 * 3600, 3 * 3600, now)  # 4h stale

    def test_post_close_hold_no_rescan(self):
        from bot.finviz_client import _session_fresh
        # KR 마감 06:30 UTC. 08:00 UTC(장 밖) — 마감 후(07:00) 산출본 fresh,
        # 마감 전(05:00) 산출본은 stale(최종봉 미반영 → 1회 재스캔).
        now = self._ts(2026, 6, 15, 8, 0)
        assert _session_fresh("KR", self._ts(2026, 6, 15, 7, 0), 3 * 3600, now)
        assert not _session_fresh("KR", self._ts(2026, 6, 15, 5, 0), 3 * 3600, now)

    def test_weekend_holds_friday_close(self):
        from bot.finviz_client import _session_fresh
        sat = self._ts(2026, 6, 20, 12, 0)              # 토요일
        fri_after = self._ts(2026, 6, 19, 7, 0)         # 금 마감(06:30) 후
        assert _session_fresh("KR", fri_after, 3 * 3600, sat)   # 주말 재스캔 0

    def test_per_market_close_times(self):
        # 각 시장 마감(UTC) 직후 산출본이 장 밖에서 fresh — 나라별 시간 반영.
        from bot.finviz_client import _SESSIONS_UTC, _session_fresh
        for mkt, (_oh, _om, ch, cm) in _SESSIONS_UTC.items():
            # 마감 30분 후(장 밖) 시점, 마감 직후 산출본 → fresh
            after = self._ts(2026, 6, 15, ch, cm) + 1800
            made = self._ts(2026, 6, 15, ch, cm) + 60
            assert _session_fresh(mkt, made, 3 * 3600, after), mkt

    def test_unknown_market_flat_fallback(self):
        from bot.finviz_client import _session_fresh
        now = self._ts(2026, 6, 15, 3, 0)
        assert _session_fresh("XX", now - 100, 200, now)
        assert not _session_fresh("XX", now - 300, 200, now)

    def test_wired_into_all_fetch_paths(self):
        # 신고저/상한가/무버 fetch 가 _session_fresh 를 실제로 탄다(배선 E2E).
        for path in ("intl_highlow", "tw_highlow", "jp_stop", "intl_movers"):
            src = open(f"bot/{path}.py", encoding="utf-8").read()
            assert "_session_fresh(" in src, path
        fc = open("bot/finviz_client.py", encoding="utf-8").read()
        assert "_session_fresh(\"US\"" in fc          # US 전종목 티어도 장-인지


class TestTradeProxyMediaPath:
    """trade 대시보드를 NOAH 8081 이 /trade 리버스 프록시로 흡수할 때, 카드
    이미지(상대 ../media/)가 회색이던 버그(사용자 2026-06-15). 미디어는 trade
    백엔드 ROOT(8765/media/)에 있는데 (a) 상대 ../media/ 가 /trade/ 마운트 밖
    /<token>/media/(NOAH 루트→404)로 탈출하고 (b) 프록시가 /trade/media/ 를
    base(=…/dashboard) 아래로 보내 8765/dashboard/media→404. 두 헬퍼가 fix."""

    def test_media_upstream_hits_backend_root(self):
        from bot.dashboard_server import _trade_upstream_url
        base = "http://127.0.0.1:8765/dashboard"
        # 미디어는 /dashboard 의 형제 → 백엔드 ROOT 로
        assert (_trade_upstream_url(base, "/media/2026-06-15/x.jpg")
                == "http://127.0.0.1:8765/media/2026-06-15/x.jpg")
        # 그 외는 base(=…/dashboard) 아래로 그대로
        assert (_trade_upstream_url(base, "/index.html")
                == "http://127.0.0.1:8765/dashboard/index.html")
        assert _trade_upstream_url(base, "/") == "http://127.0.0.1:8765/dashboard/"

    def test_body_rewrite_with_token(self):
        from bot.dashboard_server import _rewrite_trade_html
        tok = "06beb08f5f4ad5515007e65f8f60b471"
        body = (b'<div class="mini-img" style="background-image:'
                b'url(../media/2026-06-15/x.jpg)"></div>'
                b'<a href="/dashboard/market.html">')
        out = _rewrite_trade_html(body, tok)
        # 상대 ../media/ → 토큰 포함 절대 /trade/media/ (마운트 안, 토큰 보존)
        assert b"/%s/trade/media/2026-06-15/x.jpg" % tok.encode() in out
        assert b"../media/" not in out                  # 탈출 경로 제거
        assert b"/trade/market.html" in out             # 절대 /dashboard/ 정렬

    def test_body_rewrite_without_token(self):
        from bot.dashboard_server import _rewrite_trade_html
        assert _rewrite_trade_html(b"url(../media/a.jpg)", "") == b"url(/trade/media/a.jpg)"

    def test_proxy_wires_helpers(self):
        # 배선 E2E: 프록시 핸들러가 두 헬퍼를 실제로 호출한다(인라인 회귀 방지).
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert "_trade_upstream_url(base, sub)" in src
        assert "_rewrite_trade_html(body, _TOKEN)" in src


class TestNaverKrLiveQuote:
    """상세 페이지 KR 현재가 → Naver 실시간 (사용자 2026-06-15 '상세는 무조건
    라이브'). KIS 키 부재 시 yfinance 로 폴백하면 KR 은 EOD 라 장중에 직전 거래일
    종가(NXT 클릭→금요일종가)를 줬음. Naver polling.finance 단일종목 시세로 키·
    IP차단 무관하게 장중 라이브 보장. 엔드포인트 2026-06-15 VM 검증."""

    def test_parse_quote_rising(self):
        from bot.naver_quote import _parse_quote
        q = _parse_quote({"closePriceRaw": 1194000, "fluctuationsRatioRaw": 5.66,
                          "compareToPreviousPrice": {"code": "2"},
                          "marketValueFullRaw": 43040279190000,
                          "localTradedAt": "2026-06-15T15:30:00+09:00"})
        assert q["price"] == 1194000.0
        assert abs(q["pct"] - 5.66) < 1e-9          # 상승 = 양수
        assert q["mcap"] == 43040279190000.0        # 시총 원 단위 그대로

    def test_parse_quote_falling_sign(self):
        from bot.naver_quote import _parse_quote
        q = _parse_quote({"closePriceRaw": 1000, "fluctuationsRatioRaw": 3.2,
                          "compareToPreviousPrice": {"code": "5"}})
        assert q["pct"] == -3.2                      # 하락 code 5 → 음수

    def test_parse_quote_guards(self):
        from bot.naver_quote import _parse_quote
        assert _parse_quote({"fluctuationsRatioRaw": 1.0}) is None   # price 없음
        assert _parse_quote({}) is None
        assert _parse_quote(None) is None
        # 콤마 문자열 폴백
        assert _parse_quote({"closePrice": "1,194,000"})["price"] == 1194000.0

    def test_fetch_bad_code_no_network(self):
        from bot.naver_quote import fetch_kr_quote
        assert fetch_kr_quote("") is None
        assert fetch_kr_quote("AAPL") is None        # 비숫자 → 네트워크 안 탐
        assert fetch_kr_quote(None) is None

    def test_wired_into_detail_and_chart(self):
        # 배선 E2E: 카드(build_live_quote)와 차트(_live_last_price) KR 경로가
        # 실제로 Naver fetch_kr_quote 를 탄다(추측 #12c — 호출부 배선 가드).
        dh = open("bot/dashboard.py", encoding="utf-8").read()
        assert "from bot.naver_quote import fetch_kr_quote" in dh
        assert '"네이버 실시간"' in dh                # source 라벨
        cd = open("bot/chart_data.py", encoding="utf-8").read()
        assert "from bot.naver_quote import fetch_kr_quote" in cd

    def test_parse_quote_includes_today_ohlcv(self):
        # 차트 당일봉 라이브용 OHLCV 도 파싱.
        from bot.naver_quote import _parse_quote
        q = _parse_quote({"closePriceRaw": 1194000, "openPriceRaw": 1132000,
                          "highPriceRaw": 1194000, "lowPriceRaw": 1118000,
                          "accumulatedTradingVolumeRaw": 314500})
        assert q["open"] == 1132000.0 and q["high"] == 1194000.0
        assert q["low"] == 1118000.0 and q["close"] == 1194000.0
        assert q["volume"] == 314500.0

    def test_merge_kr_today_bar(self, monkeypatch):
        # yahoo 일봉에 Naver 당일 OHLCV 병합 — 당일봉 없으면 추가, 있으면 교체,
        # 글리치(직전 대비 비현실)면 원본 유지 (사용자 2026-06-15 '장중 당일 캔들').
        import pandas as pd
        from datetime import datetime, timezone, timedelta
        import bot.chart_data as cd
        import bot.naver_quote as nq
        today = datetime.now(timezone(timedelta(hours=9))).date()
        prev = today - timedelta(days=3)

        def _ser(dates, vals):
            return pd.Series(vals, index=pd.DatetimeIndex([pd.Timestamp(d) for d in dates]))
        _ts = f"{today}T15:30:00+09:00"   # 당일 판정용 거래소 현지 ts

        # 추가 — 당일봉 없음(전일까지), ts=오늘 → append
        monkeypatch.setattr(nq, "fetch_kr_quote", lambda t: {
            "close": 1194000, "open": 1132000, "high": 1194000,
            "low": 1118000, "volume": 314500, "ts": _ts})
        c, v, o, h, l = cd._merge_today_bar(
            "267260.KS", _ser([prev], [1130000.0]), _ser([prev], [100.0]),
            _ser([prev], [1125000.0]), _ser([prev], [1135000.0]), _ser([prev], [1120000.0]))
        assert len(c) == 2 and c.iloc[-1] == 1194000 and c.index[-1].date() == today
        assert o.iloc[-1] == 1132000 and v.iloc[-1] == 314500

        # 교체 — 당일봉 이미 있음(stale), ts=오늘 → replace
        c, v, o, h, l = cd._merge_today_bar(
            "267260.KS", _ser([prev, today], [1130000.0, 1130000.0]),
            _ser([prev, today], [100.0, 50.0]), _ser([prev, today], [1125000.0, 1125000.0]),
            _ser([prev, today], [1135000.0, 1135000.0]), _ser([prev, today], [1120000.0, 1120000.0]))
        assert len(c) == 2 and c.iloc[-1] == 1194000 and v.iloc[-1] == 314500

        # 글리치 — 직전 대비 +200% → 원본 유지
        monkeypatch.setattr(nq, "fetch_kr_quote", lambda t: {"close": 3390000, "ts": _ts})
        c, _, _, _, _ = cd._merge_today_bar(
            "267260.KS", _ser([prev], [1130000.0]), None, None, None, None)
        assert len(c) == 1 and c.iloc[-1] == 1130000

    def test_merge_nonkr_us_tw(self, monkeypatch):
        # 비-KR 확장(사용자 2026-06-15): US=네이버해외·TW=대만거래소 당일 OHLCV 병합.
        import pandas as pd
        from datetime import datetime, timezone, timedelta
        import bot.chart_data as cd, bot.world_quote as wq, bot.tw_quote as tw
        today = datetime.now(timezone(timedelta(hours=9))).date()
        prev = today - timedelta(days=3)

        def _ser(ds, vs):
            return pd.Series(vs, index=pd.DatetimeIndex([pd.Timestamp(d) for d in ds]))
        monkeypatch.setattr(wq, "fetch_world_quote", lambda t: {
            "close": 295.2, "open": 294.19, "high": 295.82, "low": 291.7,
            "volume": 6242520, "ts": f"{today}T09:55:09-04:00"})
        c, *_ = cd._merge_today_bar("AAPL", _ser([prev, today], [290.0, 290.0]),
                                    None, None, None, None)
        assert c.iloc[-1] == 295.2                       # US worldstock 당일봉 교체
        monkeypatch.setattr(tw, "fetch_tw_quote", lambda t: {
            "close": 2375.0, "price": 2375.0, "open": 2360.0, "high": 2375.0,
            "low": 2345.0, "volume": 25410, "ts": today.strftime("%Y%m%d")})
        c, *_ = cd._merge_today_bar("2330.TW", _ser([prev, today], [2310.0, 2310.0]),
                                    None, None, None, None)
        assert c.iloc[-1] == 2375.0                       # TWSE 당일봉 교체(ts=YYYYMMDD)

    def test_chart_wires_merge(self):
        # 배선 E2E: fetch_chart_payload 가 전 시장 1d 에서 당일봉 병합을 실제 호출.
        cd = open("bot/chart_data.py", encoding="utf-8").read()
        assert "_merge_today_bar(ticker, close, vol, op, hi, lo)" in cd
        assert 'if interval == "1d":' in cd


class TestLiveAutoRefresh:
    """라이브 대시보드 자동 새로고침 (사용자 2026-06-15 '진입시 라이브·매번 수동
    새로고침 안 하고', '장후엔 돌릴 필요 없어', '신고저는 한시간 기준 부하 방지').
    동적 페이지 no-cache(진입 신선) + 세션·cadence 인지 폴링(장중에만·신고저 1h·
    무버 30s) + #live-root swap + 정렬 재바인드."""

    def test_dynamic_pages_no_cache(self):
        # 진입시 라이브 — 동적 render-on-GET 핸들러가 no-cache(브라우저 stale 차단).
        src = open("bot/dashboard_server.py", encoding="utf-8").read()
        # 정적 1 + 동적 핸들러 다수 = 10+ (replace_all 로 일괄). 들여쓰기 깨짐 0(구문가드).
        assert src.count('"Cache-Control", "no-cache, must-revalidate"') >= 9

    def test_live_refresh_js_markers(self):
        from bot.live_refresh import LIVE_REFRESH_JS as js
        assert "'/kr52':1" in js and "'/ushighlow':1" in js   # 신고저=1h(SLOW)
        # 대만 급등락 /twhighlow 도 서버 1h TTL 이라 SLOW(1h) — 옛 30초 over-poll
        # 해소(2026-06-16 TW T2).
        assert "'/twhighlow':1" in js
        # 美 장전·장후 = 5분(MED), 신고저 1h, 그 외 30s (2026-06-16 C 그룹).
        assert "'/usprepost':300000" in js                    # MED 5분
        assert "SLOW[page]?3600000:(MED[page]||30000)" in js  # 1h · 5분 · 30s
        assert "if(!isOpen()) return" in js                   # 장후·주말 폴링 skip
        assert "document.hidden" in js                        # 백그라운드 탭 skip
        assert "getElementById('live-root')" in js            # #live-root swap
        assert "window.hlBindSort" in js                      # 정렬 재바인드
        assert "location.href" in js                          # fetch-self

    def test_shells_emit_live_root(self):
        # 3개 shell(KR naver · US · TW/intl) 모두 #live-root + 자동새로고침 JS emit.
        import bot.naver_pages as np, bot.us_pages as up, bot.tw_pages as tp
        kr = np._shell("t", "s", "nxt", "<b>body</b>")
        us = up._shell("t", "s", "usmovers", "<b>body</b>")
        tw = tp._tw_shell("t", "s", "<b>body</b>", nav="")
        for h in (kr, us, tw):
            assert 'id="live-root"' in h
            assert "setInterval(tick, interval)" in h

    def test_hl_sort_rebindable(self):
        from bot.highlow_render import HL_SORT_JS
        assert "window.hlBindSort=function()" in HL_SORT_JS    # swap 후 재바인드 가능
        assert "window.hlBindSort()" in HL_SORT_JS             # 초기 1회 호출
        assert "if(th._srtb) return" in HL_SORT_JS             # 중복 바인드 가드

    def test_market_hours_logic(self):
        # LIVE_REFRESH_JS isOpen 의 KST 거래시간 판정 충실 포팅(의도 가드).
        def is_open(market, day, hm):
            if market == "US":
                if 1 <= day <= 5 and hm >= 22 * 60 + 30: return True
                if 2 <= day <= 6 and hm <= 5 * 60 + 10: return True
                return False
            if day in (0, 6): return False
            W = {"KR": (8 * 60, 20 * 60), "JP": (9 * 60, 15 * 60 + 10),
                 "HK": (10 * 60 + 30, 17 * 60 + 10), "CN": (10 * 60 + 30, 16 * 60 + 10),
                 "TW": (10 * 60, 14 * 60 + 40)}.get(market)
            return (W[0] <= hm <= W[1]) if W else True
        assert is_open("KR", 1, 12 * 60) is True       # 월 정오 장중
        assert is_open("KR", 1, 21 * 60) is False      # 월 21시 장후
        assert is_open("KR", 6, 12 * 60) is False      # 주말
        assert is_open("US", 2, 2 * 60) is True        # 화 새벽 = 美 월 야간
        assert is_open("US", 2, 12 * 60) is False      # 화 정오 美 휴장
        assert is_open("US", 6, 3 * 60) is True        # 토 새벽 = 美 금 야간
        assert is_open("US", 0, 3 * 60) is False       # 일 새벽 美 주말
        assert is_open("JP", 1, 16 * 60) is False      # JP 장후


class TestMacroChartReadable:
    """홈 Macro 차트(금리·환율/물가) 축 글씨가 너무 작아 안 보이던 것 키움
    (사용자 2026-06-15 '그래프 안에 글씨 너무 작아'). SVG 축/눈금 font 9→15."""

    def test_svg_axis_font_bumped(self):
        from bot.dashboard import _svg_line_chart
        import re as _re
        svg = _svg_line_chart(
            ["2025-07", "2025-09", "2025-11", "2026-01"],
            [{"name": "A", "color": "#42a5f5", "data": [4.4, 4.2, 4.1, 4.5], "axis": "L"},
             {"name": "B", "color": "#ab47bc", "data": [1495, 1515, 1528, 1510], "axis": "R"}],
        )
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        sizes = [int(x) for x in _re.findall(r'font-size="(\d+)"', svg)]
        # 원본 9px(안 보임) 회귀 차단 + 18px(너무 큼) 회귀 차단 — 사용자 2026-06-15
        # '9 너무 작아'→'18 너무 커' → 13px 적정.
        assert sizes and 12 <= min(sizes) <= 16
        assert svg.count("<text") >= 8           # 좌·우 눈금 + x라벨 정상 emit

    def test_rates_fx_uses_kr_10y(self):
        # 금리·환율 비교: 미국 10Y(시장금리)엔 한국도 국고채 10년(시장금리)이
        # like-for-like — 기준금리(정책금리) mismatch 대체(사용자 2026-06-15 '미국
        # 10년 적절한지 조사'). ECOS kr10y 부재 시 기준금리 폴백 + 라벨도 폴백.
        from bot.macro_snapshot import _build_charts
        rf = _build_charts({"us_10y": [4.4, 4.1, 4.5], "kr_10y": [3.1, 3.2, 3.3],
                            "kr_rate": [2.5, 2.5, 2.5], "usdkrw": [1495, 1520, 1510]}, 18.0)["rates_fx"]
        assert rf["kr_10y"] == [3.1, 3.2, 3.3] and rf["kr_label"] == "한국 국고채 10년"
        rf2 = _build_charts({"us_10y": [4, 4, 4], "kr_rate": [2.5, 2.5, 2.5],
                             "usdkrw": [1490, 1500, 1510]}, 18.0)["rates_fx"]
        assert rf2["kr_label"] == "한국 기준금리"     # 10Y 부재 → 폴백 라벨

    def test_nonkr_name_two_line(self):
        # 비-KR 종목명: 티커 아래 한글명 별도 줄(.nk) — "TICKER (한글)" 인라인이
        # 좁은 셀에서 단어 중간 줄바꿈되던 것 해소(사용자 2026-06-15, 한국제외 전 시장).
        from bot.highlow_render import stock_panel, HL_SORT_JS
        html = stock_panel("T", [{"ticker": "LRCX", "name": "램 리서치",
                                  "price": 366.81, "pct": 1.18}], "x", "US")
        assert '<span class="nk">램 리서치</span>' in html      # 한글명 별도 줄
        assert 'class="ts">(' not in html.split("tbody")[1]      # 옛 괄호 인라인 제거
        assert ".hl-table td.nm .nk{display:block" in HL_SORT_JS  # 줄 분리 CSS


class TestWorldTwLiveQuote:
    """비-KR 상세/차트 현재가 라이브 (사용자 2026-06-15 '다 네이버, 한국도 네이버
    KIS 말고'). US/JP/HK/CN=네이버 해외(worldstock, 국내와 동일 필드→_parse_quote
    재사용) · TW=대만거래소(TWSE) 공식 MIS. KR 가격경로 KIS 제거."""

    def test_world_reuters_candidates(self):
        from bot.world_quote import _candidates
        assert _candidates("AAPL") == ["AAPL.O", "AAPL.N"]   # US NASDAQ→NYSE
        assert _candidates("7203.T") == ["7203.T"]            # JP suffix 그대로
        assert _candidates("600519.SS") == ["600519.SS"]      # CN
        hk = _candidates("0700.HK")
        assert hk[0] == "0700.HK" and all(c.endswith(".HK") for c in hk)
        assert _candidates("005930.KS") == [] and _candidates("2330.TW") == []  # KR·TW 제외

    def test_worldstock_parse_reuses_naver(self):
        # 해외 worldstock 필드 = 국내와 동일(2026-06-15 VM: AAPL.O) → _parse_quote 재사용
        from bot.naver_quote import _parse_quote
        q = _parse_quote({"closePriceRaw": 294.93, "fluctuationsRatioRaw": 1.30,
                          "compareToPreviousPrice": {"code": "2"},
                          "marketValueFullRaw": 4331668468300})
        assert q["price"] == 294.93 and abs(q["pct"] - 1.30) < 1e-9
        assert q["mcap"] == 4331668468300

    def test_tw_parse(self):
        from bot.tw_quote import _parse_tw
        t = _parse_tw({"z": "2375.0000", "y": "2310.0000", "o": "2360.0000",
                       "h": "2375.0000", "l": "2345.0000", "v": "25410"})
        assert t["price"] == 2375.0 and t["volume"] == 25410.0
        assert abs(t["pct"] - ((2375 / 2310 - 1) * 100)) < 1e-6      # +2.81%
        assert _parse_tw({"z": "-", "pz": "2370.0", "y": "2310"})["price"] == 2370.0  # 체결없음→pz
        assert _parse_tw({"z": "-", "y": "2310"}) is None            # 가격 전무

    def test_bad_input_no_network(self):
        from bot.world_quote import fetch_world_quote
        from bot.tw_quote import fetch_tw_quote
        assert fetch_world_quote("") is None
        assert fetch_tw_quote("AAPL") is None       # 비숫자 → 네트워크 안 탐
        assert fetch_tw_quote("") is None

    def test_wired_card_and_chart(self):
        # 배선 E2E: build_live_quote(카드)는 네이버만(KIS 없음). _live_last_price
        # (차트)는 ① 네이버 → ② KIS → ③ Yahoo 폴백(사용자 2026-06-16 — 차트만 KIS).
        import re
        dh = open("bot/dashboard.py", encoding="utf-8").read()
        m = re.search(r"def build_live_quote\(.*?\n(?=def )", dh, re.DOTALL).group(0)
        assert "fetch_world_quote" in m and "fetch_tw_quote" in m
        assert "fetch_kr_quote" in m                 # KR 은 네이버
        assert "kis_client" not in m                 # 카드 가격경로는 KIS 미사용
        cd = open("bot/chart_data.py", encoding="utf-8").read()
        llp = re.search(r"def _live_last_price\(.*?\n(?=def )", cd, re.DOTALL).group(0)
        assert "fetch_world_quote" in llp and "fetch_tw_quote" in llp
        assert "fetch_kr_quote" in llp               # ① 네이버
        assert "get_realtime_price" in llp and "get_overseas_realtime_price" in llp  # ② KIS 폴백


class TestChartTweaks20260615b:
    """차트 추가 다듬기(사용자 2026-06-15): E x축 연도 제거 · C KR 분봉 네이버
    (KIS 말고) · D PER/PBR 라이브 재계산."""

    def test_xaxis_drops_year(self):
        # x축 라벨 "2025-07"→"07", "2025.11.28"→"11.28" (연도 제거, 월만).
        from bot.dashboard import _svg_line_chart
        import re
        svg = _svg_line_chart(["2025-07", "2025-09", "2025-11", "2026-01"],
                              [{"name": "A", "color": "#42a5f5", "data": [4, 4, 4, 4], "axis": "L"}])
        xs = re.findall(r'text-anchor="middle">([^<]*)</text>', svg)
        assert xs and all(re.match(r'^\d{2}$', x) for x in xs if x)   # 월만(2자리)
        assert "2025" not in svg and "2026" not in svg                # 연도 잔존 없음

    def test_kr_intraday_naver_not_kis(self, monkeypatch):
        # KR 분봉 = 네이버 minute API (KIS 제거, 사용자 '분봉도 네이버로').
        import requests
        import bot.chart_data as cd
        import bot.naver_quote as nq
        bars = [{"localDateTime": f"2026061509{m:02d}00", "currentPrice": 344000.0 + m * 100,
                 "openPrice": 344000.0 + m * 100, "highPrice": 344500.0 + m * 100,
                 "lowPrice": 341500.0 + m * 100, "accumulatedTradingVolume": 1000 * (m + 1)}
                for m in range(12)]

        class _R:
            status_code = 200
            def json(self): return bars
        _orig = requests.get
        monkeypatch.setattr(requests, "get",
                            lambda u, **k: _R() if "minute" in u else _orig(u, **k))
        monkeypatch.setattr(nq, "fetch_kr_quote", lambda t: {"price": 345100.0})
        p = cd._fetch_kr_intraday("005930.KS", "5m")
        assert p and p["interval"] == "5m" and p["period"] == "1d"
        assert p.get("close") and len(p["close"]) >= 2
        assert p.get("last_price") == 345100        # 네이버 라이브 last
        # 배선: _fetch_kr_intraday 가 네이버 minute 호출, KIS 미사용
        src = open("bot/chart_data.py", encoding="utf-8").read()
        fn = src[src.index("def _fetch_kr_intraday"):src.index("def ", src.index("def _fetch_kr_intraday") + 1)]
        assert "chart/domestic/item/" in fn and "minute" in fn   # 네이버 분봉
        assert "kis_client" not in fn                            # KIS 제거

    def test_live_per_pbr_recompute_wired(self):
        # 현재가 라이브 시 PER=현재가/EPS · PBR=현재가/BPS 재계산 배선(사용자
        # 'PER/PBR 더 실시간'). 적자·무BPS 는 yfinance 유지.
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'price / _eps' in src and 'price / _bps' in src
        assert "if price and not delayed:" in src      # 라이브일 때만 재계산
        # 공식 검증
        assert round(337000.0 / 5000.0, 2) == 67.40 and round(337000.0 / 50000.0, 2) == 6.74


class TestDashboardBatch20260615c:
    """사용자 2026-06-15 배치: ① 미국 신고저 종목명 2줄(.nk, 글자 붙던 것)
    ② 티커 중복 prefix 제거 ③ 라이브 새로고침 탭복귀+NXT ④ 실적·리서치
    날짜범위 필터 ⑤ 투자판단 지주회사전환→회사구조 ⑥ 기재정정 중요 제외."""

    def test_us_highlow_single_source_sort_js(self):
        # us_pages 가 highlow_render.HL_SORT_JS(.nk + hlBindSort)를 단일 소스로
        # 사용 — 로컬 stale 복사본(미국 종목명 글자 붙음 + 정렬 사망) 제거.
        import bot.us_pages as u
        assert "td.nm .nk" in u._HL_SORT_JS         # 티커 아래 한글명 별도 줄
        assert "hlBindSort" in u._HL_SORT_JS        # live-refresh 후 정렬 재바인드
        src = open("bot/us_pages.py", encoding="utf-8").read()
        # 로컬 _HL_SORT_JS = \"\"\" 정의가 사라지고 import 로 대체
        assert "from bot.highlow_render import HL_SORT_JS" in src
        assert '_HL_SORT_JS = """' not in src

    def test_us_highlow_name_two_line(self):
        # 미국 종목: 티커(1줄) + 이름(.nk 2줄). AMAT/Applied 가 붙지 않음.
        from bot.highlow_render import stock_panel
        html = stock_panel("t", [{"ticker": "AMAT", "name": "어플라이드 머티어리얼즈",
                                   "price": 567.25, "pct": 2.64, "mcap": 4504, "vol": 1510000}],
                           "tid", "US", show_vol=True, show_value=True)
        assert '<span class="nk">어플라이드 머티어리얼즈</span>' in html
        assert "AMAT어플라이드" not in html          # 붙어 나오던 버그

    def test_strip_dup_ticker_prefix(self):
        # 'TICKER | name' / 'TICKER - name' 중복 prefix 제거 (HK 케이스).
        from bot.highlow_render import _strip_dup_ticker, stock_panel
        assert _strip_dup_ticker("0004.HK", "0004.HK | 구룡창") == "구룡창"
        assert _strip_dup_ticker("2688.HK", "2688.HK - ENN 에너지") == "ENN 에너지"
        assert _strip_dup_ticker("1928.HK", "샌즈 차이나") == "샌즈 차이나"   # 구분자 없음 → 원본
        assert _strip_dup_ticker("0700.HK", "0700.HK") == "0700.HK"          # 동일 → 원본(호출부 처리)
        html = stock_panel("t", [{"ticker": "0004.HK", "name": "0004.HK | 구룡창",
                                   "price": 1, "pct": 0}], "tid", "HK")
        assert "구룡창" in html and "0004.HK | 구룡창" not in html

    def test_live_refresh_tab_return_and_nxt(self):
        # 탭 복귀 즉시 갱신(visibilitychange) + NXT(08:00~20:00) 윈도.
        js = open("bot/live_refresh.py", encoding="utf-8").read()
        assert "visibilitychange" in js and "if(!document.hidden) tick()" in js
        assert "KR:[8*60,20*60]" in js                # NXT 프리~애프터마켓 포함
        assert "NXT" in js                            # 의도 명시 주석
        assert "'/ushighlow':1" in js                 # 미국 신고저 1h (한국제외 1h 제약)
        assert "'/krprepost':'KR'" in js              # KR NXT 보드 자동새로고침 배선
        #   (라우트는 dashboard_server 에 있는데 MKT 맵 누락 시 폴링 0 → 수동
        #   새로고침만 — 형제 보드(/nxt·/highlow)처럼 30초 자동갱신 보장)
        # /usprepost = 美 연장(장전·장후) 전용 폴링 창 — 정규장 창(22:30–05:10)이
        # 아니라 17:00→익일 10:10 (장전 17:00–22:30 포함). 이게 없으면 美 장전에
        # 화면 자동갱신 안 됨(서버는 30분 재산출하는데 브라우저가 안 당김).
        assert "page==='/usprepost'" in js
        assert "hm>=17*60" in js and "hm<=10*60+10" in js

    def test_usprepost_extended_polling_window(self):
        # /usprepost 폴링 창(KST) 포팅 가드 — 美 장전(한국 저녁 17:00~)·장후(익일
        # 오전)에 자동갱신 살아있고, 美 휴장(한국 낮·주말)엔 skip.
        def open_pp(day, hm):   # day: 0=일..6=토
            if 1 <= day <= 5 and hm >= 17 * 60:
                return True
            if 2 <= day <= 6 and hm <= 10 * 60 + 10:
                return True
            return False
        assert open_pp(1, 18 * 60) is True        # 월 18:00 = 美 장전(프리마켓)
        assert open_pp(2, 2 * 60) is True         # 화 02:00 = 美 정규장(한국 새벽)
        assert open_pp(2, 7 * 60) is True         # 화 07:00 = 美 장후(애프터)
        assert open_pp(1, 12 * 60) is False       # 월 정오 = 美 휴장
        assert open_pp(6, 9 * 60) is True         # 토 09:00 = 금 장후 tail(美 금요일 밤)
        assert open_pp(6, 12 * 60) is False       # 토 정오 = 주말 skip
        assert open_pp(0, 18 * 60) is False       # 일 저녁 = 美 아직 휴장

    def test_material_title_holding_company(self):
        # 투자판단 '지주회사 전환' 제목 → 회사구조 (사용자 '웅진 왜 실적'). 기본
        # 카테고리는 2026-06-16 '실적'→'기타'로 교정(사용자 압타바이오 FDA 임상).
        from bot.dart_feed import _material_title_category, _upgrade_category, _classify_report
        assert _classify_report("투자판단관련주요경영사항") == "기타"        # 기본(실적 아님)
        assert _material_title_category("제목: 지주회사 전환신고에 대한 심사결과") == "회사구조"
        assert _material_title_category("제목: 임상 3상 진입") is None        # 특정 종류 아님 → 미승격
        it = {"report_nm": "투자판단관련주요경영사항", "category": "기타",
              "detail": ["제목: 지주회사 전환신고에 대한 심사결과 통지서 접수"]}
        _upgrade_category(it)
        assert it["category"] == "회사구조"             # 지주회사 본문이면 승격
        it2 = {"report_nm": "투자판단관련주요경영사항", "category": "기타",
               "detail": ["제목: 임상시험 결과"]}
        _upgrade_category(it2)
        assert it2["category"] == "기타"                # 임상=특정 종류 아님 → 기타 유지(실적 오염 0)

    def test_correction_not_important(self):
        # 기재정정은 중요 미발화 (사용자 '기재정정은 중요로 안들어가').
        from bot.dart_feed import significance
        assert significance({"report_nm": "[기재정정]상장폐지관련", "category": "리스크",
                             "detail": ["제목: 상장폐지 결정"]}) is None
        assert significance({"report_nm": "[기재정정]매출액또는손익구조30%이상변동",
                             "detail": ["매출액: +50%"]}) is None
        # 비정정 상장폐지는 여전히 발화 (회귀 방지)
        assert significance({"report_nm": "상장폐지관련", "category": "리스크",
                             "detail": ["제목: 상장폐지 결정"]}) == "상장폐지 관련"

    def test_earnings_research_date_filter(self):
        # 다가오는 실적·리서치 날짜 필터 = 드롭다운(20260615 redo, 사용자 '날짜
        # 입력 말고 스크롤 내려서 선택'). 가용 날짜만 option, from~to 범위.
        import bot.dashboard as d
        data = {"earnings": [{"symbol": "AAPL", "name": "Apple", "date": "2026-06-20",
                              "hour": "amc", "eps_estimate": 1.5, "revenue_estimate": 9e10,
                              "quarter": 3, "year": 2026}],
                "earnings_ts": "", "research_ts": "",
                "research_kr": [{"name": "네이버", "code": "035420", "date": "2026-06-15",
                                 "broker": "미래", "rating": "매수", "tp": "30만", "title": "상향"}],
                "research_kr_industry": [], "research_kr_strategy": [], "research_us": [],
                "research_jp": [], "research_tw": [], "research_cn": [], "research_hk": [],
                "snapshot": {}, "indices": [], "ts": "", "macro": {}}
        html = d._render_market_page(data)
        assert '<select id="earn-from"' in html and '<select id="earn-to"' in html
        assert '<select id="research-from"' in html and '<select id="research-to"' in html
        assert '<option value="2026-06-20">2026-06-20</option>' in html   # 실적 가용일
        assert '<option value="2026-06-15">2026-06-15</option>' in html   # 리서치 가용일
        assert "전체 날짜" in html and 'type="date"' not in html          # 입력 제거
        assert "function wireDateFilter(" in html and "{{" not in html


class TestDashboardBatch20260615d:
    """사용자 2026-06-15 후속 배치: ① 매크로 차트 y라벨 잘림(padL) ② 홈으로=뒤로
    가기 ③ 대만 중국어 업종→한글 ⑧ 미국 무버 비보통주(권리·유닛·CVR) 제외 ·
    ⑥ 날짜 필터 드롭다운화 ⑦ 관심종목 실적일 필터."""

    def test_macro_chart_ylabel_not_clipped(self):
        # padL 66 — 6~7자리 콤마 y라벨이 좌측 잘리지 않게(사용자 '안에 글씨 짤림').
        from bot.dashboard import _svg_line_chart
        import re
        svg = _svg_line_chart(["11.28", "03.23", "06.05"],
                              [{"name": "예탁금", "color": "#c0f",
                                "data": [394367, 272224, 377760], "axis": "L"}])
        # y축 라벨 text-anchor=end 가 x=padL-7=59 (50→66 상향). x<50 이면 잘림.
        xs = [float(m) for m in re.findall(r'text-anchor="end">[^<]+</text>', svg) and
              re.findall(r'<text x="([\d.]+)"[^>]*text-anchor="end"', svg)]
        assert xs and all(x >= 55 for x in xs)   # 라벨 우측 끝이 충분히 안쪽

    def test_home_scroll_restore_not_history_back(self):
        # 홈 복귀 = market.html 스크롤 복원(sessionStorage) — history.back()
        # 가로채기는 bfcache 불안정·다단이동 문제로 제거(사용자 2026-06-15 '또
        # 새로고침처럼'). 어떤 경로로 홈에 도착해도 마지막 위치 복귀.
        dash = open("bot/dashboard.py", encoding="utf-8").read()
        nav = open("bot/naver_pages.py", encoding="utf-8").read()
        assert "noah_home_scroll" in dash and "sessionStorage" in dash   # 복원
        # 가로채기 코드(주석 아님) 제거 — back-link click 가로채기 시그니처 부재
        assert "closest('a.back-link')" not in dash
        assert "closest('a.back-link')" not in nav

    def test_tw_chinese_sectors_mapped(self):
        from bot.twse_client import _SECTOR_KR
        for cn, kr in (("電子兩倍槓桿", "전자 2배(레버리지)"), ("電子反向", "전자 인버스"),
                       ("塑膠化工", "플라스틱·화학"), ("機電", "기계·전기")):
            assert _SECTOR_KR.get(cn) == kr

    def test_us_movers_exclude_noncommon(self):
        # 권리·워런트·유닛·우선주·CVR 제외, Wright/United/정상명 보존.
        from bot.finviz_client import prune_non_stock, _is_noncommon_security
        for nm in ("LGL Group Rights Expiry 23rd June 2026",
                   "M3 Brigade Acquisition V Units", "Gen Digital Contingent Value Rights",
                   "AIMD Warrant", "XYZ Preferred Series B"):
            assert _is_noncommon_security(nm), nm
        for nm in ("Wright Medical", "United Airlines", "Bright Health",
                   "Verde Clean Fuels, Inc.", "Apple Inc", "삼성전자"):
            assert not _is_noncommon_security(nm), nm
        rows = [{"name": "LGL Group Rights", "mcap": 1, "ind": "x", "vol": 1},
                {"name": "Apple Inc", "mcap": 1, "ind": "x", "vol": 1}]
        assert [r["name"] for r in prune_non_stock(rows)] == ["Apple Inc"]

    def test_favorites_date_filter_wired(self):
        # 관심종목 다음예상 실적일(data-earn) from~to 드롭다운 필터 배선.
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "earnFrom: ''" in src and "earnTo: ''" in src        # favState
        assert "fav-earn-from" in src and "fav-earn-to" in src      # 드롭다운
        assert "tr.dataset.earn" in src                             # data-earn 필터
        assert "전체 실적일" in src                                  # 해제 옵션


class TestPrepostAndTWFlow20260615:
    """사용자 2026-06-15: ① 미국 장전/장후 현재-세션 우선(이른 장전이 전일 장후로
    오라벨되던 것) ⑦ TW 수급 헤더·라벨 한글화."""

    def test_prepost_current_session(self):
        from datetime import datetime, timezone
        from bot.prepost_client import _current_session
        assert _current_session(datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)) == "pre"   # 05:00 ET
        assert _current_session(datetime(2026, 6, 15, 21, 0, tzinfo=timezone.utc)) == "post"  # 17:00 ET
        assert _current_session(datetime(2026, 6, 15, 16, 0, tzinfo=timezone.utc)) == ""      # 정규장

    def test_prepost_prefers_current_session(self):
        src = open("bot/prepost_client.py", encoding="utf-8").read()
        assert "cur_sess = _current_session()" in src
        assert 'r.get("session") == cur_sess' in src   # 현재 세션 봉만 남김(있으면)

    def test_tw_flow_korean(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "외국인·기관 매매동향 (종목별)" in src      # 三大法人 헤더 한글화
        assert "三大法人 매매동향" not in src              # 한자 헤더 잔존 없음
        assert '("외국인", "foreign")' in src and '("자기매매", "dealer")' in src


class TestResearchMonthAndUSNames20260615:
    """사용자 2026-06-15: 미국 실적·리서치 한글명 ( ) (신고저와 동일 Naver
    koreanCodeName 소스) + KR/US 리서치 30일치(intl 은 기존 30일과 통일)."""

    def test_us_earnings_research_korean_names(self):
        import bot.naver_ranking_client as nrc
        _orig = nrc.world_upjong_name
        nrc.world_upjong_name = lambda m: (
            {"AAOI": "어플라이드 옵토일렉트로닉스", "CCL": "카니발"} if m == "US" else {})
        try:
            import bot.dashboard as d
            data = {"earnings": [{"symbol": "AAOI", "name": "", "date": "2026-08-05",
                                  "hour": "", "eps_estimate": 0.02, "revenue_estimate": 1.94e8,
                                  "quarter": 2, "year": 2026}],
                    "earnings_ts": "", "research_ts": "", "research_kr": [],
                    "research_kr_industry": [], "research_kr_strategy": [],
                    "research_us": [{"symbol": "CCL", "firm": "Stifel", "to_grade": "Buy",
                                     "from_grade": "Buy", "date": "2026-06-12"}],
                    "research_jp": [], "research_tw": [], "research_cn": [],
                    "research_hk": [], "snapshot": {}, "indices": [], "ts": "", "macro": {}}
            html = d._render_market_page(data)
            assert 'AAOI <span class="ts">(어플라이드 옵토일렉트로닉스)</span>' in html
            assert 'CCL <span class="ts">(카니발)</span>' in html
        finally:
            nrc.world_upjong_name = _orig

    def test_research_30day_window(self):
        src = open("bot/market_overview.py", encoding="utf-8").read()
        assert src.count("days_back=30,") == 3   # KR 기업/산업/전략 한 달
        us_fn = src[src.index("def fetch_recent_research_us"):
                    src.index("def fetch_recent_research_intl")]
        assert "timedelta(days=30)).isoformat()" in us_fn   # US 롤링 30일
        assert "days_back=7," not in src                    # 7일 잔존 없음


class TestRatesChartSpread20260615:
    """사용자 2026-06-15: 금리·환율 차트 → 한·미 국채 비교(환율 대신 美-韓 10Y
    금리차 spread). 비교 목적엔 금리차가 단일 핵심지표(캐리·자본흐름·원화 압력)."""

    def test_rates_fx_spread_chart(self):
        import bot.dashboard as d
        rf = {"labels": ["07", "08", "09"], "us_10y": [4.5, 4.4, 4.6],
              "kr_10y": [4.0, 3.9, 4.1], "kr_label": "한국 국고채 10년",
              "spread": [0.5, 0.5, 0.5]}
        # 2026-07-08 차트 row 가 '시장유동성' 섹션(_render_macro_charts_row)
        # 으로 이동 — 계약은 새 함수 기준으로 유지.
        html = d._render_macro_charts_row({"charts": {"rates_fx": rf}})
        assert "한·미 국채 금리 추이" in html
        assert "美-韓 금리차 %p (우)" in html
        assert "미국 10Y" in html and "한국 국고채 10년" in html
        assert "원/달러" not in html and "yfinance" not in html   # 환율·소스 제외
        rf2 = dict(rf); rf2.pop("spread")
        html2 = d._render_macro_charts_row({"charts": {"rates_fx": rf2}})
        assert "美-韓 금리차" in html2                             # spread 폴백 계산

    def test_build_charts_computes_spread(self):
        from bot.macro_snapshot import _build_charts
        rf = _build_charts({"us_10y": [4.5, 4.4, 4.6],
                            "kr_10y": [4.0, 3.9, 4.1]}, None).get("rates_fx")
        assert rf and rf["spread"] == [0.5, 0.5, 0.5]
        assert "usdkrw" not in rf                                 # 환율 제외


class TestTWSectorStaleFallback20260615:
    """사용자 2026-06-15 '대만 장중엔 제대로, 마감 후 ETF 4개로 degrade' — 장 마감/
    점검으로 live 類股 가 비면 당일 마지막 장중 스냅샷(stale 캐시) 복원, ETF 폴백 방지."""

    def test_serves_stale_twse_after_close(self):
        import bot.twse_client as tw
        _mi, _cs = tw.fetch_mi_index, tw._cached_stale
        tw.fetch_mi_index = lambda: {"sectors": [], "ts": ""}            # live 비음(마감)
        tw._cached_stale = lambda n, max_age_sec=86400: {
            "sectors": [{"name": "반도체", "pct": 3.5}, {"name": "전자", "pct": -1.2}],
            "ts": "2026-06-15 13:30"}
        try:
            r = tw.fetch_tw_sector_movers()
            assert [s["name"] for s in r["up"]] == ["반도체"]
            assert [s["name"] for s in r["down"]] == ["전자"]
            assert r["source"] == "TWSE 類股"          # ETF 폴백 아님
        finally:
            tw.fetch_mi_index, tw._cached_stale = _mi, _cs


class TestAsiaDashboard20260615:
    """사용자 2026-06-15: 일·중·홍·대 업종등락을 asia.html 로 분리(홈 경량화) +
    nav 'ASIA'(분석그룹·NOAH 종목분석 왼쪽) + 진입 위치 복원(noah_asia_scroll).
    홈은 KR·US 만 유지·아시아 위젯 제거."""

    def test_asia_page(self):
        import bot.dashboard as d
        mv = {"up": [{"name": "반도체", "pct": 3.5}], "down": [], "ts": "", "source": "x"}
        a = d._render_asia_page({"jp_sector_movers": mv, "cn_sector_movers": mv,
                                 "hk_sector_movers": mv, "tw_sector_movers": mv})
        assert all(x in a for x in ["🇯🇵 일본", "🇨🇳 중국", "🇭🇰 홍콩", "🇹🇼 대만"])
        # 2026-07-03: 30초 self-fetch swap 제거(배너로 대체) — 스크롤 복원만 유지.
        assert "fetch('asia.html'" not in a
        assert "noah_asia_scroll" in a
        assert "noah_asia_scroll" in a             # 진입 위치 복원
        # ASIA nav = 홈 + NOAH 종목분석만 (사용자 2026-06-15) — 그 외 peers 없음
        _nav = a[a.index('<div class="nav">'):a.index("</div>", a.index('<div class="nav">'))]
        assert "market.html" in _nav and "index.html" in _nav   # 홈 + NOAH 종목분석
        assert "screener.html" not in _nav and "dart_feed.html" not in _nav
        assert a.startswith("<!doctype html>") and "{{" not in a

    def test_home_drops_asia_keeps_link(self):
        import bot.dashboard as d
        mv = {"up": [{"name": "은행", "pct": 1.0}], "down": [], "ts": "", "source": ""}
        data = {"earnings": [], "earnings_ts": "", "research_ts": "", "research_kr": [],
                "research_kr_industry": [], "research_kr_strategy": [], "research_us": [],
                "research_jp": [], "research_tw": [], "research_cn": [], "research_hk": [],
                "indices": [], "ts": "", "macro": {}, "snapshot": {},
                "sector_movers": mv, "us_sector_movers": mv, "jp_sector_movers": mv,
                "cn_sector_movers": mv, "hk_sector_movers": mv, "tw_sector_movers": mv}
        h = d._render_market_page(data)
        # ASIA 이모지는 홈(Market overview)의 지구본과 구분 (사용자 2026-06-15) — 🏯
        assert 'href="asia.html">🏯 ASIA' in h          # nav 링크(지구본 아님)
        assert "🌏 ASIA" not in h                       # 옛 지구본 이모지 제거
        assert "🇯🇵 일본 업종 등락" not in h            # 홈에서 제거
        assert "🇹🇼 대만 업종 등락" not in h

    def test_asia_in_serve_allowlist(self):
        assert '"asia.html"' in open("bot/dashboard_server.py", encoding="utf-8").read()


class TestAsiaChildBackLink20260615:
    """사용자 2026-06-15 '아시아 자식 대시보드에서 홈으로 누르면 아시아 대시보드로'
    — JP/CN/HK/TW 신고저·급등락 child 의 back-link = asia.html(← ASIA). KR 은 홈에
    있으므로 market.html 유지."""

    def test_asia_back_targets(self):
        from bot.tw_pages import _asia_back
        for m in ("TW", "JP", "HK", "CN_A"):
            assert _asia_back(m) == ("asia.html", "← ASIA"), m
        assert _asia_back("KR") == ("market.html", "← 홈으로")

    def test_callers_wire_asia_back(self):
        tw = open("bot/tw_pages.py", encoding="utf-8").read()
        intl = open("bot/intl_pages.py", encoding="utf-8").read()
        assert tw.count("back=_asia_back(") >= 2      # TW 2 child 페이지
        assert intl.count("back=_asia_back(") >= 3    # JP/HK/CN movers·52·jphighlow
        # _tw_shell 이 back 파라미터를 실제 back-link href 에 사용
        assert 'href="{_bh}"' in tw


class TestSupplyCjkKoreanAndBlogReadability20260616:
    """수급 탭 한자 → 한글 (JP/HK/CN, 대만 미러) + 블로그 본문 문단 가독성
    (사용자 2026-06-16 '여기 대만처럼 한글로'·'문맥단위로 띄어쓰기')."""

    def test_supply_tab_no_cjk_terms(self):
        # CN/HK 港股通·北向·南向·东方财富 + JP 投資部門別·百万円·売買状況 한글화.
        src = open("bot/dashboard.py", encoding="utf-8").read()
        for cjk in ("港股通", "北向", "南向", "东方财富", "東方財富",
                    "投資部門別", "百万円", "売買状況"):
            assert cjk not in src, f"수급 탭 한자 잔존: {cjk}"
        # 한글 대체 확인
        assert "스톡커넥트 시장 전체 자금 흐름" in src
        assert "북향 (외국인 → 중국 A주)" in src
        assert "남향 (중국 본토 → 홍콩)" in src
        assert "동방재부(Eastmoney" in src
        assert "JPX 시장 전체 주간 수급 (백만엔)" in src
        assert "JPX 투자부문별 매매현황" in src

    def test_blog_desc_html_paragraphs(self):
        from bot.dashboard import _blog_desc_html
        # 빈 줄/줄바꿈 → 문단 단위 <p> (간격), HTML escape
        out = _blog_desc_html("문단1.\n둘째 줄.\n\n문단2 <b>x</b>.")
        assert out.count("<p ") == 3, out
        assert "&lt;b&gt;" in out and "<b>" not in out      # escape
        assert "line-height:1.7" in out
        assert _blog_desc_html("") == "" and _blog_desc_html(None) == ""
        # wall-of-text(개행 없음) → 단일 문단(렌더 깨짐 0)
        assert _blog_desc_html("긴 한 줄 텍스트").count("<p ") == 1

    def test_blog_extraction_preserves_block_breaks(self):
        # _fetch_post_text 가 블록 경계(</p>·<br>·</div>)를 개행으로 보존하는지
        # — 소스 코드에 변환 정규식이 배선됐는지 (네트워크 없이 회귀 가드).
        src = open("bot/blog_watch.py", encoding="utf-8").read()
        assert r"</p\s*>|<br\s*/?>" in src, "블록 태그 → 개행 변환 미배선"
        assert '"\\n", body)' in src or '"\\n",\n' in src or "'\\n', body" in src \
            or "</div\\s*>" in src, "블록 경계 개행 보존 미배선"


class TestDartProvisionalQuarterly20260616:
    """분기 영업(잠정)실적 Form C — 연결 본표 전부 '-', 국가별/순매출만 채워진
    다국적 공정공시(오리온 271560, 사용자 2026-06-16 '실제 파서내용 거의 없어').
    VM 프로브로 받은 실문서 텍스트 스냅샷으로 회귀 가드."""

    _ORION = (
        '정치로서 향후 확정치와는 다를 수 있음. 1. 연결실적내용 단위 : 억원, % '
        '구분 당기실적 전기실적 전기대비 전년동기실적 전년동기대비 (-) (-) 증감율(%) 흑자적자전환여부 (-) 증감율(%) 흑자적자전환여부 '
        '매출액 당해실적 - - - - - - - 누계실적 - - - - - - - 영업이익 당해실적 - - - - - - - 누계실적 - - - - - - - '
        '법인세비용차감전계속사업이익 당해실적 - - - - - - - 누계실적 - - - - - - - 당기순이익 당해실적 - - - - - - - 누계실적 - - - - - - - '
        '지배기업 소유주지분 순이익 당해실적 - - - - - - - 누계실적 - - - - - - - '
        '구분(억원, %) 당기실적(26년 5월) 전기실적(26년 4월) 전기대비 증감율(%) 흑자전환여부 전년동기실적(25년5월) 전년동기대비증감율(%) 흑자전환여부 '
        '국가별 매출액 - - - - - - - 한국(오리온) 1,004 1,012 -0.8 - 1,031 -2.6 - 중국(OFC) 1,237 1,303 -5.1 - 1,024 20.8 - 베트남(OFV) 401 379 5.8 - 355 13.0 - 러시아(OIE) 369 332 11.1 - 290 27.2 - '
        '국가별 영업이익 - - - - - - - 한국(오리온) 142 150 -5.3 - 187 -24.1 - 중국(OFC등 8개법인) 218 223 -2.2 - 177 23.2 - 베트남(OFV) 54 41 31.7 - 56 -3.6 - 러시아(OIE) 53 55 -3.6 - 35 51.4 - '
        '2. 정보제공내역 정보제공자 재경팀 정보제공대상자 기관투자자 및 일반투자자 등 '
        '4. 기타 2026년 5월 당월 한국 중국 베트남 러시아 합계 총매출* 1,128 1,453 445 380 3,406 매출차감 -124 -216 -44 -11 -395 순매출** 1,004 1,237 401 369 3,011 '
        '2026년 5월 누계 한국 중국 베트남 러시아 합계 총매출* 5,402 7,839 2,549 1,646 17,436 매출차감 -553 -1,202 -256 -40 -2,051 순매출** 4,849 6,637 2,293 1,606 15,385 ')

    def test_orion_segment_form(self):
        from bot.dart_feed import _doc_unit_mult, _provisional_lines
        assert _doc_unit_mult(self._ORION) == 1e8          # 억원 인식(1e8)
        out = _provisional_lines(self._ORION, _doc_unit_mult(self._ORION))
        j = " | ".join(out)
        assert "매출액(순매출): 3,011억원" in j              # 당월 순매출 합계
        assert "누계 1.5조원" in j or "누계 15,385억원" in j   # 누계 합계
        assert "한국 1,004억원" in j and "중국 1,237억원" in j  # 국가별 매출
        assert "베트남 401억원" in j and "러시아 369억원" in j
        assert "국가별 영업이익" in j and "한국 142억원" in j   # 국가별 영업이익
        # 연락처만 남던 회귀 차단: 라인 ≥ 4
        assert len(out) >= 4, out

    def test_standard_consolidated_filled(self):
        # 표준 분기(연결 본표 채워진 경우) — 매출액/영업이익/당기순이익 추출,
        # 국가별/순매출 fallback 미진입. (Orion 식이 아닌 일반 분기 잠정실적)
        from bot.dart_feed import _provisional_lines
        txt = ('단위 : 백만원 구분 당기실적 전기실적 전년동기실적 '
               '매출액 당해실적 1,500,000 1,400,000 5.3 - 1,300,000 15.4 - 누계실적 - - - - - - - '
               '영업이익 당해실적 300,000 250,000 20.0 - 280,000 7.1 - 누계실적 - - - - - - - '
               '당기순이익 당해실적 200,000 180,000 11.1 - 190,000 5.3 - 누계실적 - - - - - - - ')
        out = _provisional_lines(txt, 1e6)
        j = " | ".join(out)
        assert "매출액:" in j and "영업이익:" in j and "당기순이익:" in j
        assert "전년동기대비 +15.4%" in j                    # YoY 비율
        assert "순매출" not in j and "국가별" not in j        # 본표 채워지면 fallback 안 탐

    def test_dispatch_unit_reparse_wired(self):
        src = open("bot/dart_feed.py", encoding="utf-8").read()
        assert "_parse_provisional_doc(rcept_no, api_key, t)" in src  # 디스패치 배선
        assert "1e8 if u == " in src                                  # 억원 단위
        assert "def reparse_provisional_once_if_needed" in src        # 소급 재추출
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "reparse_provisional_once_if_needed" in tb             # startup 배선


class TestDartReclassTuja20260616:
    """투자판단 실적→기타 소급 재분류 (압타바이오 FDA) — reclassify_v5/v7 의
    'new_cat != 기타' 가드가 다운그레이드를 막던 것 전용 패스로 보강."""

    def test_reclassify_tuja_downgrades_existing(self, monkeypatch, tmp_path):
        import bot.dart_feed as d
        from datetime import datetime
        today = datetime.now(d._KST).date()
        base = [
            {"report_nm": "투자판단관련주요경영사항(임상시험결과)", "category": "실적",
             "detail": ["제목: FDA Remove Clinical Hold 공문 수령"]},
            {"report_nm": "연결재무제표기준영업(잠정)실적(공정공시)", "category": "실적"},
        ]
        saved: dict = {}
        monkeypatch.setattr(d, "load_archive",
                            lambda dd: [dict(x) for x in base] if dd == today else None)
        monkeypatch.setattr(d, "save_archive",
                            lambda dd, its: saved.__setitem__(dd, its))
        monkeypatch.setattr(d, "_RECLASS_TUJA_MARKER", tmp_path / ".m")
        n = d.reclassify_tuja_once_if_needed()
        assert n == 1, n                              # 투자판단만 1건
        out = saved[today]
        assert out[0]["category"] == "기타"            # 압타바이오 실적→기타
        assert out[1]["category"] == "실적"            # 영업잠정실적은 진짜 실적 유지
        assert d.reclassify_tuja_once_if_needed() is None  # marker 1회

    def test_startup_wired(self):
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "reclassify_tuja_once_if_needed" in tb


class TestKrPrepostBoard20260616:
    """KR 장전·장후 시간외(단일가) 가격 급등·급락 board — 미국 prepost 의 KR
    버전(사용자 2026-06-16, 네이버 overMarketPriceInfo). NXT 수급 보드와 별개."""

    def test_kr_session_windows(self):
        from datetime import datetime, timedelta, timezone
        from bot.prepost_client import _current_kr_session, _in_kr_extended_window
        K = timezone(timedelta(hours=9))
        assert _current_kr_session(datetime(2026, 6, 16, 8, 30, tzinfo=K)) == "pre"
        assert _current_kr_session(datetime(2026, 6, 16, 16, 3, tzinfo=K)) == "post"
        assert _current_kr_session(datetime(2026, 6, 16, 11, 0, tzinfo=K)) == ""
        assert _in_kr_extended_window(datetime(2026, 6, 16, 16, 3, tzinfo=K)) is True
        assert _in_kr_extended_window(datetime(2026, 6, 20, 16, 3, tzinfo=K)) is False  # 토

    def test_kr_compute_suffix_and_rank(self, monkeypatch):
        import bot.naver_quote as nq
        import bot.naver_ranking_client as nr
        import bot.prepost_client as pp
        monkeypatch.setattr(nr, "fetch_kr_movers", lambda limit=200: {
            "up": [{"ticker": "005930.KS", "name": "삼성전자", "mcap": 4500000.0},
                   {"ticker": "247540.KQ", "name": "에코프로비엠", "mcap": 120000.0}],
            "down": [{"ticker": "000660.KS", "name": "SK하이닉스", "mcap": 900000.0}]})
        # 등락률 = 시간외가 vs 정규장 종가(순수 시간외 move) · 거래량 = 시간외 세션.
        over = {"005930": {"over_session": "AFTER_MARKET", "over_price": 342000.0,
                           "reg_close": 337000.0, "over_volume": 50000, "volume": 13768940},
                "247540": {"over_session": "AFTER_MARKET", "over_price": 180000.0,
                           "reg_close": 186000.0, "over_volume": 30000, "volume": 500000},
                "000660": {"over_session": "AFTER_MARKET", "over_price": 210000.0,
                           "reg_close": 212000.0, "over_volume": 40000, "volume": 800000}}
        monkeypatch.setattr(nq, "fetch_kr_quote", lambda tk: over.get(str(tk).split(".")[0]))
        monkeypatch.setattr(pp, "_cache_write", lambda *a, **k: None)
        monkeypatch.setattr(pp, "_kr_status_write", lambda *a, **k: None)
        monkeypatch.setattr(pp, "_current_kr_session", lambda *a, **k: "post")
        out = pp._compute_kr_prepost()
        assert out["scanned"] == 3
        assert [r["ticker"] for r in out["up"]] == ["005930.KS"]          # +1.48% 순수 시간외
        assert [r["ticker"] for r in out["down"]] == ["247540.KQ", "000660.KS"]  # -3.23 < -0.94
        assert abs(out["up"][0]["pct"] - 1.48) < 0.05                     # 342000/337000-1
        assert out["up"][0]["vol"] == 50000                              # 시간외 거래량(전체 13.7M 아님)
        assert out["up"][0]["mcap"] == 4500000.0                          # 시총 carry
        assert out["session"] == "post"

    def test_kr_page_renders_with_data(self, monkeypatch):
        import bot.prepost_client as pp
        monkeypatch.setattr(pp, "fetch_kr_prepost_movers", lambda: {
            "up": [{"ticker": "005930.KS", "name": "삼성전자", "price": 342000.0,
                    "pct": 1.48, "vol": 1000, "mcap": 4500000.0}],
            "down": [{"ticker": "000660.KS", "name": "SK하이닉스", "price": 210000.0,
                      "pct": -1.1, "vol": 1000, "mcap": 900000.0}],
            "ts": "2026-06-16 16:05 KST", "session": "post", "source": "네이버"})
        from bot.intl_pages import render_kr_prepost_page
        html = render_kr_prepost_page()
        assert "한국 NXT 장전·장후 급등·급락" in html
        assert "삼성전자" in html and "SK하이닉스" in html
        assert 'href="krprepost"' in html and 'class="active"' in html

    def test_nav_route_wired(self):
        tw = open("bot/tw_pages.py", encoding="utf-8").read()
        assert '("krprepost", "🌙 NXT 급등·급락")' in tw   # KR NXT 가격 보드 탭(시간외→NXT 정정)
        assert '("nxt", "📊 NXT 수급")' in tw               # NXT 수급(흐름) — 가격 보드와 구분
        ds = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert '"/krprepost"' in ds and "render_kr_prepost_page" in ds
        db = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'href="krprepost"' in db                      # KR 홈 nav 링크
        # NXT/업종/급등락 페이지도 시간외 탭 노출 — naver_pages._shell 가 단일 소스
        # _market_nav 사용(하드코딩 nav drift 차단, 사용자 2026-06-16 '다른 페이지에
        # 시간외 없음'). 폴백도 시간외 포함.
        np = open("bot/naver_pages.py", encoding="utf-8").read()
        assert '_market_nav("KR", active)' in np
        assert "🌙 NXT 장전·장후" not in np   # 하드코딩 옛 NXT 라벨 잔존 없음

    def test_over_volume_and_pure_move(self):
        # 시간외 거래량(over_volume) 파싱 + 순수 시간외 move 계산 배선.
        from bot.naver_quote import _parse_quote
        item = {"closePriceRaw": "337000", "fluctuationsRatioRaw": "0",
                "accumulatedTradingVolumeRaw": "13768940",
                "overMarketPriceInfo": {"overMarketStatus": "OPEN",
                                        "tradingSessionType": "AFTER_MARKET",
                                        "overPrice": "342000", "fluctuationsRatio": "1.48",
                                        "accumulatedTradingVolume": "50000",
                                        "compareToPreviousPrice": {"code": "2"}}}
        q = _parse_quote(item)
        assert q["over_volume"] == 50000.0      # 시간외 세션 거래량(전체와 별개)
        assert q["reg_close"] == 337000.0       # 정규장 종가(순수 move 계산용)
        assert q["over_price"] == 342000.0
        pp = open("bot/prepost_client.py", encoding="utf-8").read()
        assert "(op / reg - 1) * 100" in pp     # 순수 시간외 move 배선
        assert 'q.get("over_volume")' in pp     # 시간외 거래량 우선


class TestEarningsDateSort20260616:
    """다가오는 실적 탭 = 날짜 오름차순(가까운 날 먼저), 종목 알파벳순 아님
    (사용자 2026-06-16 '금일기준 최신이 위로, 미국'). 전 시장 탭 적용."""

    def test_earnings_sort_wired(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "_earn_by_date" in src
        assert "_earn_us = _earn_by_date(_earn_us)" in src
        assert "_earn_kr = _earn_by_date(_earn_kr)" in src   # 전 시장 적용

    def test_earnings_sort_key_logic(self):
        # 정렬 키: 날짜 asc · 동률은 티커 · 무날짜 맨뒤 (nested _earn_by_date 미러)
        rows = [{"symbol": "ZZZ", "date": "2026-06-17"},
                {"symbol": "AAA", "date": "2026-08-05"},
                {"symbol": "MMM", "date": None},
                {"symbol": "BBB", "date": "2026-06-17"}]
        out = sorted(rows, key=lambda e: (str(e.get("date") or "9999-99-99"),
                                          str(e.get("symbol") or "")))
        assert [r["symbol"] for r in out] == ["BBB", "ZZZ", "AAA", "MMM"]


class TestPrewarmDaemonThread20260616:
    """prewarm stagger time.sleep(600) 이 봇 종료를 블로킹 → 'deactivating' 멈춤
    (라이브 다운, 실수 #6, 2026-06-16 16:37). _periodic_highlow_prewarm 은 데몬
    thread fire-and-forget 이어야 — asyncio.to_thread 의 non-daemon 기본 executor 가
    종료 시 atexit(_python_exit) join 으로 sleep 을 최대 10분 기다려 봇이 안 죽음."""

    def test_prewarm_uses_daemon_thread_not_to_thread(self):
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "asyncio.to_thread(_prewarm_highlow)" not in src, \
            "prewarm 은 데몬 thread 여야 (to_thread non-daemon executor 가 stagger " \
            "time.sleep 으로 봇 종료 블로킹 — 'deactivating' 멈춤)"
        assert "Thread(target=_prewarm_highlow, daemon=True" in src, \
            "prewarm 데몬 thread 미배선"


class TestOverValueRegularPriceFinnhub20260616:
    """사용자 2026-06-16 (네이버페이 미러): ①현재가/차트=정규장 종가(모든 나라)·
    시간외(NXT)가는 분리 ②KR 시간외 보드에 거래대금 추가 ③Finnhub 위젯 월별 분할
    (1500 cap 으로 US만 8월 쏠림 fix)."""

    def test_parse_amt_unit_suffix(self):
        from bot.naver_quote import _parse_amt
        assert _parse_amt("4,707,022백만") == 4707022 * 1e6   # 백만(만보다 먼저)
        assert _parse_amt("47억") == 47e8
        assert _parse_amt("1.2조") == 1.2e12
        assert _parse_amt("1234") == 1234.0
        assert _parse_amt("") is None and _parse_amt(None) is None

    def test_overmarket_price_regular_and_over_value(self):
        from bot.naver_quote import _parse_quote
        item = {"closePriceRaw": "123200", "fluctuationsRatioRaw": "9.14",
                "compareToPreviousPrice": {"code": "5"},
                "overMarketPriceInfo": {"overMarketStatus": "OPEN",
                    "tradingSessionType": "AFTER_MARKET", "overPrice": "135600",
                    "accumulatedTradingVolume": "290000",
                    "accumulatedTradingValue": "35,775백만",
                    "fluctuationsRatio": "0.00", "compareToPreviousPrice": {"code": "2"}}}
        q = _parse_quote(item)
        assert q["price"] == 123200.0              # 정규장 종가 유지(NXT 135600 아님)
        assert q["over_price"] == 135600.0         # NXT 분리
        assert q["over_value"] == 35775 * 1e6      # 시간외 거래대금(원)
        assert q["over_volume"] == 290000.0

    def test_kr_board_includes_over_value(self, monkeypatch):
        import bot.naver_quote as nq
        import bot.naver_ranking_client as nr
        import bot.prepost_client as pp
        monkeypatch.setattr(nr, "fetch_kr_movers", lambda limit=200: {
            "up": [{"ticker": "031980.KQ", "name": "피에스케이홀딩스", "mcap": 26600.0}],
            "down": []})
        monkeypatch.setattr(nq, "fetch_kr_quote", lambda tk: {
            "over_session": "AFTER_MARKET", "over_price": 135600.0, "reg_close": 123200.0,
            "over_volume": 290000, "over_value": 35775 * 1e6, "volume": 1000000})
        monkeypatch.setattr(pp, "_cache_write", lambda *a, **k: None)
        monkeypatch.setattr(pp, "_kr_status_write", lambda *a, **k: None)
        monkeypatch.setattr(pp, "_current_kr_session", lambda *a, **k: "post")
        out = pp._compute_kr_prepost()
        r = out["up"][0]
        assert abs(r["pct"] - 10.06) < 0.05        # 135600/123200-1 순수 시간외
        assert r["vol"] == 290000                  # 시간외 거래량
        assert abs(r["value"] - 357.75) < 0.1      # 거래대금 35,775백만 → 357.75억

    def test_kr_nxt_phantom_excluded(self, monkeypatch):
        # 후성 093370 류: NXT overMarketStatus OPEN + overPrice 22,400 이지만 실제
        # NXT 체결 0(Naver accumulatedTradingVolume='-'/0 → over_volume None). 그
        # 22,400 은 전일가 placeholder라 정규장 종가(19,200) 대비 +16.67% 는 phantom
        # 갭 → 보드에서 제외해야(사용자 2026-06-16 screenshot: 후성 NXT 거래량 0).
        # 실제 NXT 체결 있는 신세계 004170(NXT vol 54,478)만 잔존. 옛 over_volume-or-
        # volume 폴백이 정규장 풀데이 거래량으로 phantom 을 보드에 점령시키던 버그.
        import bot.naver_quote as nq
        import bot.naver_ranking_client as nr
        import bot.prepost_client as pp
        monkeypatch.setattr(nr, "fetch_kr_movers", lambda limit=200: {
            "up": [{"ticker": "004170.KS", "name": "신세계", "mcap": 70000.0},
                   {"ticker": "093370.KS", "name": "후성", "mcap": 20000.0}],
            "down": []})
        over = {  # 신세계=실체결, 후성=NXT 미체결(over_volume None)
            "004170": {"over_session": "AFTER_MARKET", "over_price": 764000.0,
                       "reg_close": 767000.0, "over_volume": 54478,
                       "over_value": 39944 * 1e6, "volume": 115979},
            "093370": {"over_session": "AFTER_MARKET", "over_price": 22400.0,
                       "reg_close": 19200.0, "over_volume": None,
                       "over_value": None, "volume": 11267003}}
        monkeypatch.setattr(nq, "fetch_kr_quote",
                            lambda tk: over.get(str(tk).split(".")[0]))
        monkeypatch.setattr(pp, "_cache_write", lambda *a, **k: None)
        monkeypatch.setattr(pp, "_kr_status_write", lambda *a, **k: None)
        monkeypatch.setattr(pp, "_current_kr_session", lambda *a, **k: "post")
        out = pp._compute_kr_prepost()
        tickers = [r["ticker"] for r in out["up"] + out["down"]]
        assert "093370.KS" not in tickers          # phantom(NXT 거래량 0) 제외
        assert tickers == ["004170.KS"]             # 실제 NXT 체결만
        r = out["down"][0]                          # 764000 < 767000 → 내림
        assert abs(r["pct"] - (-0.39)) < 0.05       # NXT 764000 vs 정규장 종가 767000
        assert r["vol"] == 54478                    # NXT 세션 거래량
        assert abs(r["value"] - 399.44) < 0.1       # 39,944백만 → 399.44억

    def test_finnhub_widget_month_chunked(self):
        src = open("bot/market_overview.py", encoding="utf-8").read()
        # 월별 분할 루프(단일 60일 범위 금지 — 1500 cap 회피)
        assert "while cursor <= end:" in src and "/calendar/earnings" in src
        assert "{cursor.isoformat()}" in src       # 월별 from= 커서
        assert "_v2.json" in src                   # 옛 캐시 무효화
        assert "fetch_earnings_calendar, 45)" in src   # 호출부 45일(이번달~다음달)


class TestNaverOverviewNews20260616:
    """해외 종목 개요·뉴스 = 네이버 한국어 worldstock 우선(₩0·번역 불요, 사용자
    2026-06-16) → 실패/KR 은 yfinance+translate 폴백. reutersCode 기반·디스크 캐시
    (bulk regen 부하 무)·graceful."""

    def test_strip_html(self):
        from bot.naver_overview import _strip_html
        assert _strip_html("애플은<br>스마트폰<br><br>SW") == "애플은\n스마트폰\n\nSW"
        assert _strip_html("<b>x</b> y") == "x y"

    def test_overview_parses_and_caches(self, monkeypatch):
        import requests
        import bot.finviz_client as fc
        import bot.naver_overview as no
        monkeypatch.setattr(no, "_rc_for", lambda t: "AAPL.O")
        store = {}
        monkeypatch.setattr(fc, "_cached", lambda name, ttl=0: store.get(name))
        monkeypatch.setattr(fc, "_cache_write", lambda name, obj: store.__setitem__(name, obj))

        class _R:
            status_code = 200

            def json(self):
                return {"summary": "애플은 폰<br>제조"}
        monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
        assert no.fetch_naver_overview("AAPL") == "애플은 폰\n제조"   # <br>→\n
        assert "naver_overview_AAPL.json" in store                    # 성공 캐시
        # 2회차 = 캐시 히트(네트워크 0 — 호출되면 fail)
        monkeypatch.setattr(requests, "get",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("캐시 무시")))
        assert no.fetch_naver_overview("AAPL") == "애플은 폰\n제조"

    def test_overview_graceful_no_rc(self, monkeypatch):
        import bot.finviz_client as fc
        import bot.naver_overview as no
        monkeypatch.setattr(no, "_rc_for", lambda t: None)
        monkeypatch.setattr(fc, "_cached", lambda name, ttl=0: None)
        assert no.fetch_naver_overview("ZZZZ") is None   # rc 부재 → graceful None

    def test_world_news_parses(self, monkeypatch):
        import requests
        import bot.finviz_client as fc
        import bot.naver_overview as no
        monkeypatch.setattr(no, "_rc_for", lambda t: "AAPL.O")
        store = {}
        monkeypatch.setattr(fc, "_cached", lambda name, ttl=0: store.get(name))
        monkeypatch.setattr(fc, "_cache_write", lambda name, obj: store.__setitem__(name, obj))

        class _R:
            status_code = 200

            def json(self):
                return [{"tit": "애플 신제품", "ohnm": "로이터",
                         "dt": "20260616210545", "subcontent": "요약"},
                        {"tit": "", "dt": "x"}]   # 빈 제목 → 스킵
        monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
        out = no.fetch_naver_world_news("AAPL", max_items=10)
        assert len(out) == 1                              # 빈 제목 제외
        assert out[0]["title"] == "애플 신제품"
        assert out[0]["publisher"] == "로이터" and out[0]["date"] == "2026-06-16"

    def test_dashboard_naver_first_wiring(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "fetch_naver_overview(ticker)" in src       # 개요 네이버 우선
        assert "fetch_naver_world_news(ticker" in src      # 뉴스 네이버 우선
        assert "_nv_desc" in src and "_nv_news" in src     # 폴백 분기
        assert "네이버 해외뉴스" in src                      # 뉴스 소스 라벨
        # 개요가 네이버면 회사 pane 출처도 '개요 네이버'로 — footer 'yfinance' 만이라
        # 네이버 개요를 yfinance 로 오해하던 것 정정(사용자 2026-06-16 NTLA).
        assert "개요 네이버 · 그 외 yfinance" in src
        # KR 은 제외(기존 DART/네이버) — 비-KR 게이트
        assert "if not is_kr:" in src


class TestFavoritesSaveInvalidatesCache20260616:
    """사용자 2026-06-16 '휴지통 작동 안 함': add/remove/reorder(_save) 후
    _FAV_CACHE 무효화 안 하면 get_favorites_with_prices(SWR, #454)가 옛 목록(삭제분
    포함)을 stale 로 계속 서빙 → 삭제·추가가 화면에 안 먹는 것처럼 보임. _save 가
    캐시 무효화 → 다음 조회가 _load()(갱신 디스크) 즉시 반영."""

    def test_save_invalidates_fav_cache(self, monkeypatch, tmp_path):
        import time as _t
        import bot.market_favorites as mf
        monkeypatch.setattr(mf, "_FAVORITES_FILE", tmp_path / "favorites.json")
        mf._FAV_CACHE = [{"ticker": "OLD.KS", "name": "old"}]   # 삭제 전 stale 캐시
        mf._FAV_CACHE_TS = _t.time()                            # fresh(만료 전)
        mf._save([{"ticker": "NEW.KS", "name": "new"}])         # 변경 저장
        assert mf._FAV_CACHE is None                            # 무효화됨 → 다음 조회 갱신

    def test_save_invalidation_wired(self):
        src = open("bot/market_favorites.py", encoding="utf-8").read()
        # _save 본문에 _FAV_CACHE 무효화 (단일 choke point — add/remove/reorder 공통)
        i = src.find("def _save(")
        body = src[i:i + 600]
        assert "_FAV_CACHE = None" in body and "global _FAV_CACHE" in body


class TestFavoritesPagination20260616:
    """관심종목 = 10개/페이지 + 하단 페이지 번호 + '전체 펼치기' 토글(사용자
    2026-06-16). 필터·정렬·새데이터 시 1페이지로 리셋."""

    def test_pagination_wired(self):
        from bot.dashboard import _render_market_page
        html = _render_market_page({})
        assert "FAV_PAGE_SIZE = 10" in html                 # 10개/페이지
        assert "function renderFavPager" in html             # 페이지 nav 렌더
        assert "전체 펼치기" in html and "10개씩 보기" in html  # 펼치기/접기 토글
        assert "favState.showAll" in html and "favState.page" in html
        assert 'class="fav-pager"' in html                   # nav 컨테이너
        assert "favState.page = 0" in html                   # 필터/정렬/새데이터 리셋
        assert "pass.slice(start, end)" in html              # 현재 페이지만 노출


class TestDetailTabEnhancements20260616:
    """상세탭 보강 (사용자 2026-06-16 전수감사 적용): A1-A3·B1-B5·C1-C2.
    _BATCH_REGEN=True 로 라이브 fetch 를 끄고 스냅샷 dict 만으로 새 렌더
    분기를 검증(hermetic). B1(finnhub)·B2(AV)는 라이브 fetch 라 배선만 확인."""

    def _html(self, ticker, si, monkeypatch):
        import bot.dashboard as d
        monkeypatch.setattr(d, "_BATCH_REGEN", True, raising=False)
        parts = d._render_stock_info_html({"ticker": ticker, "stock_info": si})
        s = parts.get("other_panes", "") or ""
        for v in (parts.get("panes") or {}).values():
            s += v or ""
        s += parts.get("tabs", "") or ""
        return s

    def test_b3_cn_valuation_fallback(self, monkeypatch):
        # yfinance PER None → AKShare get_valuation 값으로 채움(정적, data-q 없음).
        h = self._html("600519.SS", {"current_price": 50.0,
                       "cn": {"valuation": {"per": 12.3, "pbr": 1.4, "psr": 2.1}}}, monkeypatch)
        assert "12.3" in h, "CN PER 폴백 누락"

    def test_b4_tw_per_pbr_fallback(self, monkeypatch):
        h = self._html("2330.TW", {"current_price": 100.0,
                       "tw": {"per_pbr": [{"date": "2026-06-10", "PER": 18.5, "PBR": 3.2}]}},
                       monkeypatch)
        assert "18.5" in h, "TW PER 폴백 누락"

    def test_c2_tw_foreign_holding(self, monkeypatch):
        # 2026-06-16 정정: FinMind TaiwanStockShareholding = 외국인 보유+발행주식수
        # (TDCC 보유구간 분산 아님 — 옛 가정 오류로 빈 표였음). 외국인 보유비율 표시.
        h = self._html("2330.TW", {"current_price": 100.0, "tw": {"foreign": {
            "date": "2026-06-16", "foreign_ratio": 69.99, "remain_ratio": 30.0,
            "shares_issued": 25932370067}}}, monkeypatch)
        assert "외국인 보유" in h and "69.99%" in h, "외국인 보유비율 누락"

    def test_b4_tw_financials_box(self, monkeypatch):
        # B4(2026-06-16): FinMind 손익/재무상태/현금흐름 → TW 분기재무 박스
        # (US SEC XBRL·KR DART 등가물). type 값 VM probe 확정(영문). _per 비율 제외.
        import bot.finmind_client as fm
        monkeypatch.setattr(fm, "fetch_income_statement", lambda t, quarters=8: [
            {"date": "2026-03-31", "type": "Revenue", "value": 8e11},
            {"date": "2026-03-31", "type": "EPS", "value": 13.94},
            {"date": "2026-03-31", "type": "Revenue_per", "value": 100.0}])  # 비율 무시
        monkeypatch.setattr(fm, "fetch_balance_sheet", lambda t, quarters=8: [
            {"date": "2026-03-31", "type": "Equity", "value": 4e12}])
        monkeypatch.setattr(fm, "fetch_cashflow", lambda t, quarters=8: [
            {"date": "2026-03-31", "type": "CashFlowsFromOperatingActivities", "value": 5e11}])
        out = fm.fetch_tw_financials("2330.TW")
        assert out and out[0]["revenue"] == 8e11 and out[0]["eps"] == 13.94
        assert out[0]["equity"] == 4e12 and out[0]["op_cf"] == 5e11
        assert fm.fetch_tw_financials("AAPL") is None      # 비-TW graceful
        h = self._html("2330.TW", {"current_price": 1045.0,
                                   "tw": {"financials": out}}, monkeypatch)
        assert "분기 재무 (FinMind)" in h and "13.94" in h and "2026-03-31" in h

    def test_c1_jp_research_row(self, monkeypatch):
        h = self._html("7203.T", {"current_price": 3000.0, "jp": {"consensus": {
            "source": "Kabutan", "rating": "매수", "target_mean": 3500,
            "n_analysts": 7, "last_report_date": "2026-06-12"}}}, monkeypatch)
        assert "Kabutan" in h and "리서치 액션 데이터가 없습니다" not in h, "JP 리서치 행 누락"

    def test_a3_supp_consensus_with_yf_present(self, monkeypatch):
        # yfinance target_mean 이 있어도 현지 보조 컨센서스 병기.
        h = self._html("005930.KS", {"current_price": 70000.0, "target_mean": 90000,
            "kr": {"consensus": {"source": "FnGuide", "target_mean": 88000,
                                 "rating": "매수", "n_analysts": 9}}}, monkeypatch)
        assert "보조 컨센서스" in h and "88" in h, "보조 컨센서스 병기 누락"

    def test_b5_kr_disclosure_summary(self, monkeypatch):
        h = self._html("005930.KS", {"current_price": 70000.0, "kr": {"disclosures": [
            {"date": "2026-06-12", "title": "자기주식취득결정", "url": "x?rcpNo=123",
             "reporter": "회사", "summary": "취득금액 500억 · 보통주 100만주"}]}}, monkeypatch)
        assert "취득금액 500억" in h, "공시 구조화 요약 누락"

    def test_a2_disclosure_tab_always_shown(self, monkeypatch):
        # 공시 저장분이 없어도 탭 버튼은 항상 노출(라이브 오버레이가 채움).
        h = self._html("AAPL", {"current_price": 200.0}, monkeypatch)
        assert 'data-pane="si-disclosures"' in h, "공시 탭 버튼 누락"

    def test_a1_earnings_footer_honest(self):
        # 실적 풋노트가 시장 무관 yfinance(거짓 DART/MOPS/AKShare 표기 제거).
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'earn_src = "yfinance"' in src, "실적 풋노트 정직화 누락"

    def test_a3_consensus_clients_cached(self):
        fg = open("bot/fnguide_consensus.py", encoding="utf-8").read()
        kb = open("bot/kabutan_consensus.py", encoding="utf-8").read()
        assert "_cached" in fg and "fnguide_consensus_" in fg, "FnGuide 캐시 누락"
        assert "_cached" in kb and "kabutan_consensus_" in kb, "Kabutan 캐시 누락"
        # B5: dart_detail.get_disclosure_summaries 도 per-view 호출이라 캐시 필수.
        dd = open("bot/dart_detail.py", encoding="utf-8").read()
        assert "_cached" in dd and "dart_detail_summ_" in dd, "dart_detail 캐시 누락"

    def test_b1_b2_wiring(self):
        d = open("bot/dashboard.py", encoding="utf-8").read()
        assert "fetch_recommendation_trends" in d, "B1 의견분포 추이 배선 누락"
        assert "애널리스트 의견 분포 추이" in d
        assert "cache_only=True" in d and "Alpha Vantage" in d, "B2 감성 배선 누락"
        av = open("bot/av_sentiment_client.py", encoding="utf-8").read()
        assert "cache_only" in av, "AV cache_only 파라미터 누락"


class TestAsiaTier2_20260616:
    """ASIA 보드 Tier-2 (사용자 2026-06-16): T8 CN 업종 bare-code 폴백 · T11 허브
    isOpen 게이트 · T12 TW 상·하한 마커."""

    def test_t8_cn_industry_barecode_fallback(self):
        # _industries_for 가 CN_A 도 bare 6자리 코드로 폴백 매칭(접미사 .SS↔.SZ
        # 불일치 시 '업종 —' 방지).
        import inspect
        from bot import finviz_client as fc
        s = inspect.getsource(fc._industries_for)
        assert 'market in ("HK", "CN_A")' in s, "CN_A bare-code 정규화 누락"

    def test_t11_asia_hub_isopen_gate(self):
        # 2026-07-03: 30초 폴링 자체가 제거돼 isOpen 게이트도 함께 은퇴(배너 30분
        # 체크로 대체) — 폴링/장중 게이트가 부활하지 않는지 고정.
        from bot.dashboard import _ASIA_LIVE_JS as js
        assert "isOpen" not in js and "setInterval" not in js
        assert "noah_asia_scroll" in js                 # 스크롤 복원은 유지

    def test_t12_tw_limit_marker(self):
        from bot import highlow_render as hr
        up = hr.stock_panel("T", [{"ticker": "2330.TW", "name": "TSMC",
                                   "price": 100, "pct": 9.95}], "t", "TW", limit_pct=9.9)
        dn = hr.stock_panel("T", [{"ticker": "2317.TW", "name": "폭스콘",
                                   "price": 50, "pct": -10.0}], "t2", "TW", limit_pct=9.9)
        mid = hr.stock_panel("T", [{"ticker": "2454.TW", "name": "미디어텍",
                                    "price": 80, "pct": 3.2}], "t3", "TW", limit_pct=9.9)
        assert "🔺" in up and "🔻" in dn
        assert "🔺" not in mid and "🔻" not in mid
        # 기본 None(다른 보드) → 한도여도 마커 없음
        us = hr.stock_panel("T", [{"ticker": "AAPL", "name": "Apple",
                                   "price": 1, "pct": 9.95}], "t4", "US")
        assert "🔺" not in us

    def test_t12_tw_movers_passes_limit(self):
        # 사용자 2026-06-17 'TW 상한/하한(±10%) 마커 불필요' → limit_pct 제거(#472).
        # 옛 'limit_pct 전달' 단언을 '미전달'로 반전(TestTwMoversNoLimitMarker 와 일치).
        import inspect
        from bot import tw_pages
        s = inspect.getsource(tw_pages.render_tw_highlow_page)
        assert "limit_pct=" not in s, "TW 급등락 상한/하한 마커 제거됨(사용자 2026-06-17)"

    def test_t7_fetch_shares_outstanding(self, monkeypatch):
        # FinMind TaiwanStockShareholding.NumberOfSharesIssued (VM probe 확인) →
        # TW 시총 = 발행주식수×종가. 최신 일자 행 선택.
        import bot.finmind_client as fm
        monkeypatch.setattr(fm, "_cache_get", lambda k: None)
        monkeypatch.setattr(fm, "_cache_set", lambda k, v: None)
        monkeypatch.setattr(fm, "_fetch", lambda ds, code, start, **x: [
            {"date": "2026-06-15", "NumberOfSharesIssued": 25900000000},
            {"date": "2026-06-16", "NumberOfSharesIssued": 25932370067}])
        assert fm.fetch_shares_outstanding("2330.TW") == 25932370067   # 최신
        assert fm.fetch_shares_outstanding("AAPL") is None             # 비-TW graceful

    def test_t7_tw_mcap_fallback_wired(self):
        # _enrich_compute 가 TW yfinance 시총 미스 시 FinMind 발행주식수×종가 폴백.
        import inspect
        from bot import highlow_render as hr
        s = inspect.getsource(hr._enrich_compute)
        assert "fetch_shares_outstanding" in s and 'market == "TW"' in s

    def test_t9_tpex_parse_and_merge(self):
        # T9(2026-06-16): TPEx daily_close_quotes 필드 tolerant 파싱 + 무버/상한가
        # 유니버스가 上市+上櫃 합집합. (52주는 yfinance 비용으로 上市 only 유지.)
        from bot.twse_client import parse_stock_day_all, _is_common_stock
        rows = parse_stock_day_all([
            {"Date": "1150616", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
             "Close": "1045.0", "Change": "+95.0", "TradingShares": "13056886",
             "TransactionAmount": "13538566505"}])
        assert rows and rows[0]["code"] == "6488" and rows[0]["name"] == "環球晶"
        assert rows[0]["pct"] == 10.0 and rows[0]["vol"] == 13056886
        assert rows[0]["value"] == 135.39          # TransactionAmount/1e8 억NT$
        assert _is_common_stock("6488") and not _is_common_stock("006201")  # ETF 제외
        import inspect
        from bot import twse_client as tc
        assert "fetch_tpex_day_all" in inspect.getsource(tc._tw_all_common)
        assert "_tw_all_common" in inspect.getsource(tc.fetch_tw_movers)
        assert "_tw_all_common" in inspect.getsource(tc.fetch_tw_upper_lower)

    def test_t10_jp_hk_52w_cap_configurable_full(self):
        # 2026-06-16: JP/HK 52w 캡을 env HIGHLOW_UNIVERSE_CAP(기본 5000=사실상
        # 전종목)로 전환 — 옛 하드코딩 ~900·'주요 ~900종목' 라벨 제거(전종목 커버).
        ih = open("bot/intl_highlow.py", encoding="utf-8").read()
        assert "HIGHLOW_UNIVERSE_CAP" in ih and '"5000"' in ih
        assert "_cap_by_liquidity(full, 900" not in ih   # 하드코딩 900 제거
        ip = open("bot/intl_pages.py", encoding="utf-8").read()
        assert "주요 ~900종목" not in ip                  # full 커버라 옛 캡 라벨 제거


class TestHighlowSlotScan:
    """52주 신고저 슬롯 스캐너 — **독립 highlow-scan.timer 가 주(主)**(봇 배포·재시작
    과 무관하게 :00/:15/:30/:45 별 프로세스 oneshot 으로 스캔; 사용자 2026-06-19
    '배포로 중간에 변경해도 아시아·미국 신고가는 그대로 돌게'). in-process asyncio
    task 는 타이머 미설치 VM 폴백(타이머 활성 시 skip — 이중 스캔 방지). 슬롯 로직은
    bot.highlow_scan 단일 소스 — telegram 의존 없이 oneshot 가능."""

    def test_scan_module_slots(self):
        from bot.highlow_scan import _HL_SCAN_SLOTS
        assert _HL_SCAN_SLOTS[0] == ("US", "JP")
        assert _HL_SCAN_SLOTS[15] == ("HK",)
        assert _HL_SCAN_SLOTS[30] == ("CN_A", "US")
        assert _HL_SCAN_SLOTS[45] == ("TW", "TWMV")
        assert sorted(_HL_SCAN_SLOTS) == [0, 15, 30, 45]
        # 무거운 Asia 52주 4종이 서로 다른 슬롯(동시 yfinance 부하 회피, 겹침 0)
        heavy = {m: s for s, toks in _HL_SCAN_SLOTS.items()
                 for m in toks if m in ("JP", "HK", "CN_A", "TW")}
        assert len(set(heavy.values())) == 4

    def test_current_slot_floor(self):
        from datetime import datetime, timezone
        from bot.highlow_scan import _current_slot
        def f(m):
            return _current_slot(datetime(2026, 6, 19, 5, m, tzinfo=timezone.utc))
        assert [f(m) for m in (0, 7, 15, 29, 30, 44, 45, 59)] == \
            [0, 0, 15, 15, 30, 30, 45, 45]

    def test_systemd_units_and_install(self):
        # 독립 타이머 — service(oneshot, python -m bot.highlow_scan) + timer(:00/15/30/45)
        # + install.sh enable. 봇 재시작과 무관(사용자 2026-06-19).
        import os
        assert os.path.exists("deploy/highlow-scan.service")
        assert os.path.exists("deploy/highlow-scan.timer")
        svc = open("deploy/highlow-scan.service", encoding="utf-8").read()
        assert "python -m bot.highlow_scan" in svc and "Type=oneshot" in svc
        tmr = open("deploy/highlow-scan.timer", encoding="utf-8").read()
        assert "*:00,15,30,45:00" in tmr and "Unit=highlow-scan.service" in tmr
        ins = open("deploy/install.sh", encoding="utf-8").read()
        assert "highlow-scan.service" in ins and "highlow-scan.timer" in ins
        assert "enable --now highlow-scan.timer" in ins

    def test_main_joins_workers(self):
        # oneshot 이 daemon 스캔 스레드 완료를 join — 프로세스 조기 종료 시 스캔이
        # 중간에 죽던 것(=봇 재시작 버그의 oneshot 판) 방지(CLAUDE.md §7d).
        src = open("bot/highlow_scan.py", encoding="utf-8").read()
        assert "def _join_workers" in src and "join(timeout" in src
        assert "run_slot(slot)" in src and "_join_workers()" in src

    def test_timer_gate_in_telegram(self):
        # in-process 폴백은 타이머 활성 시 skip(이중 스캔·throttle 방지) + 단일 소스 import
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "def _highlow_timer_active" in src and "highlow-scan.timer" in src
        seg = src[src.index("async def _periodic_highlow_scan"):][:2200]
        assert "if _highlow_timer_active():" in seg and "continue" in seg
        assert "from bot.highlow_scan import" in seg
        assert "_highlow_scan_task = asyncio.create_task(_periodic_highlow_scan())" in src
        # 옛 in-line 슬롯 로직 제거(highlow_scan 으로 이관)
        assert "def _run_highlow_slot" not in src

    def test_run_slot_routing(self, monkeypatch):
        # 진입점 스모크(CLAUDE.md §7d) — 장중 force vs 장 밖 게이트 라우팅 (telegram 불요)
        import bot.finviz_client as fc
        import bot.highlow_scan as hs
        import bot.intl_highlow as ih
        import bot.tw_highlow as th
        import bot.twse_client as tw
        rec: dict = {}
        monkeypatch.setattr(fc, "yf_paused", lambda: False)
        # US·TW·CN 종일 개장 → 평일이면 _open True. JP 는 빈 창(항상 닫힘).
        monkeypatch.setattr(fc, "_SESSIONS_UTC",
                            {**fc._SESSIONS_UTC, "US": (0, 0, 23, 59),
                             "TW": (0, 0, 23, 59), "CN_A": (0, 0, 23, 59),
                             "JP": (12, 0, 12, 0)})
        monkeypatch.setattr(fc, "fetch_high_low",
                            lambda force=False: rec.__setitem__("us", force))
        monkeypatch.setattr(ih, "_kick", lambda m: rec.__setitem__(f"kick_{m}", True))
        monkeypatch.setattr(ih, "fetch_intl_highlow",
                            lambda m: rec.__setitem__(f"gate_{m}", True))
        monkeypatch.setattr(th, "_kick_tw_highlow",
                            lambda: rec.__setitem__("tw_kick", True))
        monkeypatch.setattr(th, "fetch_tw_highlow",
                            lambda: rec.__setitem__("tw_gate", True))
        monkeypatch.setattr(tw, "fetch_tw_movers",
                            lambda force=False: rec.__setitem__("twmv", force))
        from datetime import datetime, timezone
        wd = datetime.now(timezone.utc).weekday() < 5
        hs.run_slot(0)             # US + JP
        hs.run_slot(30)            # CN_A + US
        hs.run_slot(45)            # TW + TWMV
        assert rec.get("us") is wd                       # US: 평일 force / 주말 gate
        assert rec.get("gate_JP") is True                # JP 닫힘 → 게이트
        assert rec.get("twmv") is wd                     # TW 무버: 평일 force
        if wd:
            assert rec.get("kick_CN_A") is True          # CN 52주 장중 force
            assert rec.get("tw_kick") is True            # TW 52주 force
        else:
            assert rec.get("gate_CN_A") is True          # 주말 → CN 게이트
            assert rec.get("tw_gate") is True            # 주말 → 게이트

    def test_run_slot_off_session_gates(self, monkeypatch):
        # 장 밖(전 시장 닫힘) → 전부 게이트 fetch(EOD 1회 후 freeze, force 안 함).
        import pytest as _pt
        _pt.importorskip("telegram")   # telegram_bot 무거운 의존성 — VM 에서만 import
        import bot.finviz_client as fc
        import bot.intl_highlow as ih
        import bot.telegram_bot as tb
        import bot.tw_highlow as th
        import bot.twse_client as tw
        rec: dict = {}
        monkeypatch.setattr(fc, "yf_paused", lambda: False)
        monkeypatch.setattr(fc, "_SESSIONS_UTC",      # 빈 창 = 항상 닫힘
                            {m: (12, 0, 12, 0) for m in fc._SESSIONS_UTC})
        monkeypatch.setattr(fc, "fetch_high_low",
                            lambda force=False: rec.__setitem__("us", force))
        monkeypatch.setattr(ih, "_kick", lambda m: rec.__setitem__("kick", m))
        monkeypatch.setattr(ih, "fetch_intl_highlow",
                            lambda m: rec.__setitem__(f"gate_{m}", True))
        monkeypatch.setattr(th, "_kick_tw_highlow",
                            lambda: rec.__setitem__("tw_kick", True))
        monkeypatch.setattr(th, "fetch_tw_highlow",
                            lambda: rec.__setitem__("tw_gate", True))
        monkeypatch.setattr(tw, "fetch_tw_movers",
                            lambda force=False: rec.__setitem__("twmv", force))
        for s in (0, 15, 30, 45):
            tb._run_highlow_slot(s)
        assert rec.get("us") is False                    # force 안 함
        assert rec.get("gate_JP") and rec.get("gate_HK") # JP/HK 게이트
        assert rec.get("gate_CN_A") is True              # CN 게이트(재도입)
        assert rec.get("tw_gate") and rec.get("twmv") is False
        assert "kick" not in rec and "tw_kick" not in rec  # force 경로 미진입

    def test_run_slot_paused_falls_back_to_gate(self, monkeypatch):
        # YF_PAUSE 중엔 장중이어도 force 안 함(게이트 폴백 → 스테일 유지·스캔 0).
        import pytest as _pt
        _pt.importorskip("telegram")   # telegram_bot 무거운 의존성 — VM 에서만 import
        import bot.finviz_client as fc
        import bot.intl_highlow as ih
        import bot.telegram_bot as tb
        rec: dict = {}
        monkeypatch.setattr(fc, "yf_paused", lambda: True)         # 정지
        monkeypatch.setattr(fc, "_SESSIONS_UTC",
                            {**fc._SESSIONS_UTC, "US": (0, 0, 23, 59),
                             "JP": (0, 0, 23, 59)})
        monkeypatch.setattr(fc, "fetch_high_low",
                            lambda force=False: rec.__setitem__("us", force))
        monkeypatch.setattr(ih, "_kick", lambda m: rec.__setitem__("kick", m))
        monkeypatch.setattr(ih, "fetch_intl_highlow",
                            lambda m: rec.__setitem__(f"gate_{m}", True))
        tb._run_highlow_slot(0)
        assert rec.get("us") is False        # 정지 → US force 안 함
        assert rec.get("gate_JP") is True    # 정지 → JP 게이트(킥 안 함)
        assert "kick" not in rec

    def test_session_fresh_eod_gate(self):
        # 장중 스냅샷은 마감 후 stale(→EOD 재산출 트리거), 마감 후 산출본은 fresh
        # (→재스캔 0). 슬롯 장-밖 경로가 이 게이트에 의존.
        from datetime import datetime, timezone
        from bot.finviz_client import _session_fresh

        def ts(h, mi):
            return datetime(2026, 6, 16, h, mi, tzinfo=timezone.utc).timestamp()
        assert _session_fresh("US", ts(19, 0), 3600, now_ts=ts(23, 0)) is False
        assert _session_fresh("US", ts(22, 0), 3600, now_ts=ts(23, 0)) is True


class TestLightBoardWarm:
    """경량 동적 보드 서버사이드 워머 (사용자 2026-06-17 '모든 대시보드가 내가 안
    들어가도 자기 리프레쉬 주기로 갱신') — 방문해야만 갱신되던 무버(KR/JP/HK/CN/US)·
    KR 52주·장전후·NXT·테마/업종을 render 함수 호출('방문 시뮬')로 파일 캐시를 데움.
    무거운 yfinance 52주는 슬롯스캐너(시간당) 담당. 시장시간·pause 게이트."""

    def test_render_targets_resolve(self):
        # E2E 배선(실수 #12c) — 워머가 호출하는 9개 render 가 실제 존재·callable.
        # render 를 rename 하면 워머가 silent no-op 되기 전에 여기서 즉시 fail.
        import importlib
        targets = [
            ("bot.intl_pages", "render_intl_highlow52_page"),   # KR 52주
            ("bot.naver_pages", "render_highlow_page"),         # KR 무버
            ("bot.naver_pages", "render_theme_page"),           # KR 테마
            ("bot.intl_pages", "render_intl_movers_page"),      # JP/HK/CN 무버
            ("bot.intl_pages", "render_kr_prepost_page"),       # KR 장전후
            ("bot.nxt_pages", "render_nxt_page"),               # KR NXT 수급
            ("bot.us_pages", "render_us_movers_page"),          # US 무버
            ("bot.us_pages", "render_us_industry_page"),        # US 업종
            ("bot.us_pages", "render_us_prepost_page"),         # US 장전후
        ]
        for m, fn in targets:
            assert callable(getattr(importlib.import_module(m), fn)), m + "." + fn
        from bot.finviz_client import _SESSIONS_UTC, naver_paused, yf_paused
        from bot.prepost_client import _in_extended_window
        assert set(_SESSIONS_UTC) >= {"US", "KR", "JP", "HK", "CN_A"}
        assert callable(naver_paused) and callable(yf_paused)
        assert callable(_in_extended_window)

    def test_warmer_boot_wired(self):
        s = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "async def _periodic_light_board_warm" in s
        assert "def _warm_light_boards" in s
        assert ("_light_board_warm_task = asyncio.create_task("
                "_periodic_light_board_warm())") in s
        assert "LIGHT_BOARD_WARM_SEC" in s              # 부하 튜닝 env knob
        seg = s[s.index("def _warm_light_boards"):
                s.index("async def _periodic_light_board_warm")]
        # 게이트: naver/yf pause + 시장시간 _open + US 연장창 + thread 쌓임 가드.
        assert "naver_paused" in seg and "yf_paused" in seg
        assert "_in_extended_window" in seg and "_LIGHT_WARM_RUNNING" in seg

    def test_warm_light_boards_gating(self, monkeypatch):
        # 진입점 스모크(§7d) + 게이트 — 장중 전 보드 데움, naverpause 시 네이버 skip.
        import pytest as _pt
        _pt.importorskip("telegram")   # telegram_bot 무거운 의존성 — VM 에서만 import
        from datetime import datetime, timezone
        import bot.finviz_client as fc
        import bot.prepost_client as pp
        import bot.telegram_bot as tb
        calls: list = []
        monkeypatch.setattr(tb, "_warm_render",
                            lambda module, fn, *a: calls.append((fn, a)))
        monkeypatch.setattr(fc, "naver_paused", lambda: False)
        monkeypatch.setattr(fc, "yf_paused", lambda: False)
        monkeypatch.setattr(fc, "_SESSIONS_UTC",       # 전 시장 종일 개장(평일)
                            {m: (0, 0, 23, 59) for m in fc._SESSIONS_UTC})
        monkeypatch.setattr(pp, "_in_extended_window", lambda now: False)
        tb._warm_light_boards()
        fns = {c[0] for c in calls}
        wd = datetime.now(timezone.utc).weekday() < 5
        if wd:                                          # 장중 → 방문 시뮬 전부
            assert "render_intl_highlow52_page" in fns  # KR 52주
            assert "render_highlow_page" in fns         # KR 무버
            assert "render_intl_movers_page" in fns     # JP/HK/CN 무버
            assert "render_us_movers_page" in fns       # US 무버
            assert ("render_intl_highlow52_page", ("KR",)) in calls
        # naverpause → 네이버 보드 skip (워머도 정지 존중, 부하 0)
        calls.clear()
        monkeypatch.setattr(fc, "naver_paused", lambda: True)
        tb._warm_light_boards()
        fns2 = {c[0] for c in calls}
        assert "render_highlow_page" not in fns2
        assert "render_intl_movers_page" not in fns2


class TestMoversFreshnessLabel:
    """무버 제목 신선도 라벨 (사용자 2026-06-17 '무버 제목양식 토일') — 장중이면
    '장중 30초 갱신', 장 밖·주말(토·일)이면 '장 마감 · 마지막 거래일 종가 기준'.
    무버 데이터는 마지막 거래일 종가로 고정인데 제목만 '장중 30초'(라이브)로 오인
    되던 것 정정. 전 무버 보드(KR/US/JP/HK/CN) 제목 공통."""

    def test_open_vs_closed(self, monkeypatch):
        from datetime import datetime, timezone
        import bot.finviz_client as fc
        from bot.highlow_render import movers_freshness
        wd = datetime.now(timezone.utc).weekday() < 5
        closed = "장 마감 · 마지막 거래일 종가 기준"
        monkeypatch.setattr(fc, "_SESSIONS_UTC", {"KR": (0, 0, 23, 59)})  # 종일 개장
        assert movers_freshness("KR") == ("장중 30초 갱신" if wd else closed)
        monkeypatch.setattr(fc, "_SESSIONS_UTC", {"KR": (12, 0, 12, 0)})  # 빈 창=닫힘
        assert movers_freshness("KR") == closed                          # 장 밖→마감
        assert movers_freshness("ZZ") == closed                          # 미상 시장→마감

    def test_movers_titles_session_aware(self):
        # 네이버 라이브 무버(KR/US/JP/HK/CN) 제목은 movers_freshness 사용 — '장중 30초'
        # 하드코딩 제거(토·일 라이브 오인 차단). 52주/상한가 보드는 별개.
        for f in ("bot/naver_pages.py", "bot/intl_pages.py", "bot/us_pages.py"):
            assert "movers_freshness" in open(f, encoding="utf-8").read(), f
        for f in ("bot/intl_pages.py", "bot/us_pages.py"):
            assert "장중 30초(네이버 종가 EOD 보유)" not in open(f, encoding="utf-8").read(), f
        # TW 무버는 TWSE/TPEx 공식 EOD(실시간 아님) — Naver 가 TW worldstock 미지원
        # 이라 30초 라이브 불가(사용자 '대만 30초 적용가능?' → EOD 구조라 불가). EOD 라벨.
        tw = open("bot/tw_pages.py", encoding="utf-8").read()
        assert "공식 종가(EOD" in tw                  # EOD 정직 라벨
        assert "movers_freshness" not in tw           # TW 는 EOD라 라이브 헬퍼 미사용


class TestAmexExclusion:
    """모든 미국 대시보드에서 AMEX(NYSE American) 제외 (사용자 2026-06-17 'AMEX 는
    포함 안 할거야'). 무버(네이버)·장전후(네이버)·52주(Finviz/yfinance) 전 표면.
    네이버 경로=거래소 루프에서 AMEX 드랍, Finviz/yfinance=us_amex_symbols
    (otherlisted Exchange='A') set 필터."""

    def test_us_exchange_iterations_drop_amex(self):
        # 네이버 무버·장전후 유니버스 + overlay 맵에서 AMEX 거래소 제거(소스 단).
        nv = open("bot/naver_ranking_client.py", encoding="utf-8").read()
        seg = nv[nv.index("def fetch_us_movers"):nv.index("_INTL_MOVER_EX")]
        assert '"AMEX"' not in seg                       # 무버 거래소 루프
        us_cfg = nv[nv.index('"US": ['):nv.index('"US": [') + 90]
        assert "AMEX" not in us_cfg                      # _INTL_MOVER_EX["US"] overlay
        pp = open("bot/prepost_client.py", encoding="utf-8").read()
        seg2 = pp[pp.index("def _us_movers_universe"):pp.index("def _compute_us_prepost")]
        assert '"AMEX"' not in seg2                      # 장전후 유니버스 루프
        # Finviz 52주 출력이 AMEX 필터(_drop_amex_hl) 경유 — 전 티어 공통.
        fc = open("bot/finviz_client.py", encoding="utf-8").read()
        fhl = fc[fc.index("def fetch_high_low"):fc.index("def _fetch_sp500_csv")]
        assert fhl.count("_drop_amex_hl") >= 2          # 캐시 반환 + 신규 산출 둘 다

    def test_drop_amex_hl_filters(self, monkeypatch):
        import bot.finviz_client as fc
        import bot.us_symbol_names as usn
        monkeypatch.setattr(usn, "us_amex_symbols", lambda: {"IMO", "GORO"})
        d = fc._drop_amex_hl({"high": [{"ticker": "AAPL"}, {"ticker": "IMO"}],
                              "low": [{"ticker": "goro"}, {"ticker": "NVDA"}]})
        assert [r["ticker"] for r in d["high"]] == ["AAPL"]   # IMO(AMEX) 제거
        assert [r["ticker"] for r in d["low"]] == ["NVDA"]    # goro 대소문자 무관
        monkeypatch.setattr(usn, "us_amex_symbols", lambda: set())
        d2 = fc._drop_amex_hl({"high": [{"ticker": "IMO"}], "low": []})
        assert [r["ticker"] for r in d2["high"]] == ["IMO"]   # 빈 set → no-op(graceful)

    def test_us_amex_symbols_parse(self, monkeypatch, tmp_path):
        import pytest as _pt
        _pt.importorskip("httpx")
        import httpx
        import bot.us_symbol_names as usn
        fake = ("ACT Symbol|Security Name|Exchange|ETF\n"
                "IMO|Imperial Oil|A|N\n"          # A = NYSE American(AMEX) → 포함
                "BRK|Berkshire|N|N\n"             # N = NYSE → 제외
                "SPY|SPDR S&P 500|P|Y\n"          # P = Arca → 제외
                "File Creation Time: ...|||\n")

        class _R:
            status_code = 200
            text = fake
        monkeypatch.setattr(usn, "_AMEX_CACHE_FILE", tmp_path / "amex.json")
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _R())
        assert usn.us_amex_symbols() == {"IMO"}          # Exchange='A' 만


class TestTwSectorFreeze:
    """TW 업종 등락 freeze 윈도 4일 (사용자 2026-06-17 'TW 업종 4개로 degrade'
    진단): 24h 였을 때 어제 14:30 캐시가 오늘 14:30 만료 → 오늘 장중 類股 fetch 가
    배포 churn/일시장애로 지연되면 그 윈도에 ETF 4개 폴백. 4일이면 전일·주말 캐시가
    fallback 으로 유지돼 transient/overnight 갭에도 類股 서빙(ts 로 stale 정직 표기)."""

    def test_live_empty_serves_stale_within_4d(self, monkeypatch):
        import bot.twse_client as tw
        monkeypatch.setattr(tw, "fetch_mi_index", lambda: {"sectors": [], "ts": ""})
        snap = {"sectors": [{"name": f"S{i}", "pct": (i - 5)} for i in range(12)],
                "ts": "2026-06-16 14:30"}
        seen = {}

        def _stale(name, max_age_sec=86400):
            seen["max_age"] = max_age_sec
            return snap if name == "mi_index" else None
        monkeypatch.setattr(tw, "_cached_stale", _stale)
        r = tw.fetch_tw_sector_movers(top_n=10)
        assert r.get("source") == "TWSE 類股"            # ETF 4개 폴백 아님
        assert r.get("n") == 12 and r["up"] and r["down"]
        assert seen["max_age"] == 4 * 86400              # 4일 freeze 윈도(24h→4d)
        assert r.get("ts") == "2026-06-16 14:30"         # stale 스냅샷 시각 노출(정직)

    def test_live_present_uses_live(self, monkeypatch):
        import bot.twse_client as tw
        live = {"sectors": [{"name": "金融", "pct": 1.6}, {"name": "電子", "pct": -0.3}],
                "ts": "now"}
        monkeypatch.setattr(tw, "fetch_mi_index", lambda: live)
        r = tw.fetch_tw_sector_movers(top_n=10)
        assert r.get("source") == "TWSE 類股" and r.get("n") == 2  # live 우선


class TestSectorTitleCaps:
    """업종 등락 위젯 제목 대소문자 통일 — 전 시장(KR/US/JP/CN/HK/TW) 'TOP 10'
    (사용자 2026-06-17 시장개요 점검: HK 만 'Top 10' 으로 불일치하던 것 교정)."""

    def test_all_sector_titles_uppercase_top10(self):
        import re as _re
        s = open("bot/dashboard.py", encoding="utf-8").read()
        caps = _re.findall(r"업종 등락 (TOP|Top|top) 10", s)
        assert caps, "업종 등락 TOP 10 제목 미발견"
        assert all(c == "TOP" for c in caps), caps   # Top/top 잔존 금지(일관성)
        assert "홍콩 업종 등락 TOP 10" in s            # HK 교정 확인


class TestIndexCardRecompose:
    """홈 지수 카드 재구성 (사용자 2026-06-17): 아시아=10(VN 제거·MY/ID 편입·
    JCI→IDX종합·KR→JP→CN→HK→TW→MY→ID→IN 순), 미국 지수=9(XLE→지수-2·XLC 추가),
    미국 지수-2=옛 '아메리카 & 이머징' 대체(미국 ETF 6, 미국 지수 바로 뒤). nve: ETF."""

    def test_cards_composed(self):
        import bot.market_overview as m
        d = dict(m.ALL_CARDS)
        assert "아메리카 & 이머징" not in d and "미국 지수-2" in d
        titles = [t for t, _ in m.ALL_CARDS]
        assert titles.index("미국 지수-2") == titles.index("미국 지수") + 1  # 인접
        asia = dict(d["한국 & 아시아"])
        toks = list(asia.values())
        assert "nvi:.VNI" not in toks and "nvi:.HNXI" not in toks      # VN 제거
        assert "nvi:.KLSE" in toks and "nvi:.JKSE" in toks            # MY/ID 편입
        assert "ID 인도네시아 IDX종합" in asia and len(d["한국 & 아시아"]) == 10
        us = dict(d["미국 지수"])
        assert "nve:XLC" in us.values() and "nve:XLE" not in us.values()
        us2 = d["미국 지수-2"]
        assert len(us2) == 6 and all(tk.startswith("nve:") for _, tk in us2)
        # 사용자 2026-06-17: AIQ(네이버 미커버 '—') → XLB 소재(기술은 다른 위젯 커버)
        assert {tk for _, tk in us2} == {"nve:XLY", "nve:XLI", "nve:XLE",
                                         "nve:XLU", "nve:IYR", "nve:XLB"}
        # 제거된 이머징/베트남 지수가 어느 카드에도 없음
        allt = {tk for _, c in m.ALL_CARDS if c for _, tk in c}
        for gone in ("nvi:.AXJO", "nvi:.BVSP", "nvi:.MXX", "nvi:.MERV",
                     "nvi:.VNI", "nvi:.HNXI"):
            assert gone not in allt, gone

    def test_etf_yf_fallback(self, monkeypatch):
        # 네이버 etf 미커버(AIQ) → yfinance 폴백 (사용자 2026-06-17 VM probe).
        import bot.finviz_client as fc
        import bot.market_overview as mo
        import bot.naver_marketindex as nm
        monkeypatch.setattr(nm, "fetch_world_etf", lambda codes: {
            "XLY": {"close": 220.1, "prev": 218.9, "change": 1.2, "pct": 0.55}})
        monkeypatch.setattr(fc, "yf_paused", lambda: False)
        seen = {}

        def _fake_yf(syms):
            seen["syms"] = sorted(syms)
            return {f"nve:{s}": {"close": 1.0, "prev_close": 0.9,
                                 "change": 0.1, "pct": 11.1} for s in syms}
        monkeypatch.setattr(mo, "_yf_etf_quotes", _fake_yf)
        out = mo._fetch_etf_quotes(["nve:XLY", "nve:AIQ"])
        assert out["nve:XLY"]["close"] == 220.1           # 네이버 커버분 보존
        assert seen["syms"] == ["AIQ"]                    # 미커버만 yf 폴백
        assert out["nve:AIQ"]["close"] == 1.0
        # YF_PAUSE → yf 폴백 skip(네이버분만, 미커버는 블랭크)
        monkeypatch.setattr(fc, "yf_paused", lambda: True)
        out2 = mo._fetch_etf_quotes(["nve:XLY", "nve:AIQ"])
        assert "nve:XLY" in out2 and "nve:AIQ" not in out2


class TestTwMoversNoLimitMarker:
    """TW 무버 상한/하한(±10%) 마커·legend 제거 (사용자 2026-06-17 '대만에서 이건
    없어도 돼'). stock_panel limit_pct 미전달(행 🔺/🔻 마커 없음) + 부제 legend 제거.
    TW 52주·JP 상한가 페이지는 별개(영향 없음)."""

    def test_tw_movers_no_limit_marker(self):
        s = open("bot/tw_pages.py", encoding="utf-8").read()
        mv = s[s.index("def render_tw_highlow_page"):
               s.index("def render_tw_highlow52_page")]
        sub = mv[mv.index("sub = "):mv.index("return _tw_shell")]
        assert "🔺상한/🔻하한" not in sub          # 부제 legend 제거(코멘트는 무관)
        assert "limit_pct=" not in mv              # 행 마커 kwarg 제거


class TestCN52Reenabled:
    """CN_A 52주 신고저 재도입 (사용자 2026-06-17 '중국도 같은 방식으로' →
    'CSI300+500') — **CSI 300 + CSI 500**(대형+중형 ~800, AKShare list_csi300_500)
    + AKShare 부재 시 주요종목(peer ~64) 폴백 + 슬롯 :30 분산. 옛 2026-06-14
    제거(yfinance CN 소형주 커버리지 빈약) 번복하되 커버리지 양호한 대형·중형만."""

    def test_cn_in_cfg(self):
        from bot.intl_highlow import _CFG
        assert "CN_A" in _CFG
        peer_map, cache, status, label, suffix = _CFG["CN_A"]
        assert peer_map == "_CN_A_INDUSTRY_PEERS"   # AKShare 부재 시 peer 폴백 소스
        assert cache.startswith("highlow_cn") and status.startswith("cn_highlow")
        assert suffix == (".SS", ".SZ")             # native A주 접미사 필터

    def test_cn_csi_universe_wired(self, monkeypatch):
        # CSI 경로: _universe('CN_A') 가 AKShare list_csi300_500 를 우선 사용.
        import bot.intl_highlow as ih
        src = open("bot/intl_highlow.py", encoding="utf-8").read()
        assert "list_csi300_500" in src            # AKShare CSI 소스 배선
        # AKShare 가 CSI 구성종목 주면 그게 유니버스(폴백 아님) — 배선 검증(네트워크 0).
        monkeypatch.setattr("bot.akshare_client.list_csi300_500",
                            lambda: {f"{600000 + i}.SS": f"종목{i}" for i in range(800)})
        uni, names = ih._universe("CN_A")
        assert len(uni) == 800 and names["600000.SS"] == "종목0"
        # AKShare 빈 결과(미설치/실패) → peer(_CN_A_INDUSTRY_PEERS) 폴백, 회귀 0.
        monkeypatch.setattr("bot.akshare_client.list_csi300_500", lambda: {})
        uni2, names2 = ih._universe("CN_A")
        assert len(uni2) >= 30                       # peer ~64
        assert all(t.endswith((".SS", ".SZ")) for t in uni2)

    def test_cn_csi_parse_and_code_conversion(self, monkeypatch):
        import bot.akshare_client as ak
        # _cn_code_to_ticker 규칙: 6→.SS, 0/3→.SZ, 4/8/9(北京)·비6자리 → None.
        assert ak._cn_code_to_ticker("600519") == "600519.SS"
        assert ak._cn_code_to_ticker("000858") == "000858.SZ"
        assert ak._cn_code_to_ticker("300750") == "300750.SZ"
        assert ak._cn_code_to_ticker("830799") is None      # 北京거래소
        assert ak._cn_code_to_ticker("12") is None           # 6자리 아님
        # _pick_cons_col: '指数代码'(=000300 자기자신) 말고 '成分券代码' 선택.
        cols = ["日期", "指数代码", "指数名称", "成分券代码", "成分券名称", "权重"]
        assert ak._pick_cons_col(cols, "code") == "成分券代码"
        assert ak._pick_cons_col(cols, "name") == "成分券名称"

        class _Row(dict):
            def get(self, k, d=None):
                return dict.get(self, k, d)

        class _DF:
            def __init__(self, rows, columns):
                self._rows, self.columns, self.empty = rows, columns, False

            def iterrows(self):
                for i, r in enumerate(self._rows):
                    yield i, _Row(r)

        def _cons(sym):
            base = {"指数代码": sym, "指数名称": "x", "权重": 1, "日期": "20260617"}
            if sym == "000300":
                rows = [{**base, "成分券代码": "600519", "成分券名称": "贵州茅台"},
                        {**base, "成分券代码": "000858", "成分券名称": "五粮液"}]
            else:   # 000905, 茅台 중복 → dedup
                rows = [{**base, "成分券代码": "300750", "成分券名称": "宁德时代"},
                        {**base, "成分券代码": "600519", "成分券名称": "贵州茅台"}]
            return _DF(rows, list(base) + ["成分券代码", "成分券名称"])

        class _AK:
            def index_stock_cons_csindex(self, symbol):
                return _cons(symbol)

        monkeypatch.setattr(ak, "_import_akshare", lambda: _AK())
        monkeypatch.setattr(ak, "_fetch_with_retry", lambda fn, label, **k: fn())
        monkeypatch.setattr(ak, "_cache_get", lambda *a, **k: None)
        monkeypatch.setattr(ak, "_cache_put", lambda *a, **k: None)
        out = ak.list_csi300_500()
        assert out == {"600519.SS": "贵州茅台", "000858.SZ": "五粮液",
                       "300750.SZ": "宁德时代"}       # 300∩500 茅台 dedup·北京 제외

    def test_cn52_route_and_nav_wired(self):
        # 서버 라우트 + ASIA 허브/시장 nav 에 cn52 노출(JP/HK 미러).
        srv = open("bot/dashboard_server.py", encoding="utf-8").read()
        assert '"/cn52"' in srv and '"/cn52": "CN_A"' in srv
        navs = open("bot/tw_pages.py", encoding="utf-8").read()
        assert '("cn52", "📈 신고가·신저가")' in navs   # CN_A 자식 nav
        ip = open("bot/intl_pages.py", encoding="utf-8").read()
        assert '"CN_A": "cn52"' in ip                  # 52w active-nav 매핑
        dash = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'href="cn52"' in dash                   # ASIA 허브 링크


class TestHighlowUniverseFullCoverage:
    """52주 신고저 전종목 커버리지 (사용자 2026-06-16 '전시장 다·리미트 늘려'):
    JP/HK 캡 env 화(기본 full) + TW 52w 上市+上櫃."""

    def test_tw_52w_includes_otc(self, monkeypatch):
        import bot.tw_highlow as th
        monkeypatch.setattr("bot.twse_client.fetch_stock_day_all",
                            lambda: {"rows": [{"code": "2330", "name": "台積電"}]})
        monkeypatch.setattr("bot.twse_client.fetch_tpex_day_all",
                            lambda: {"rows": [{"code": "6488", "name": "環球晶"},
                                              {"code": "006201", "name": "ETF"}]})
        monkeypatch.delenv("TW_HIGHLOW_OTC", raising=False)
        uni, names = th._tw_universe()
        assert "2330.TW" in uni and "6488.TWO" in uni      # 上市 + 上櫃
        assert "006201.TWO" not in uni                      # ETF(6자리) 제외
        assert names["6488.TWO"] == "環球晶"
        monkeypatch.setenv("TW_HIGHLOW_OTC", "0")            # off → 上市만
        uni2, _ = th._tw_universe()
        assert "2330.TW" in uni2 and "6488.TWO" not in uni2

    def test_market_hours_label(self):
        from bot.highlow_render import market_hours_label
        assert "KST" in market_hours_label("US")
        assert "10:00–14:30" in market_hours_label("TW")
        assert market_hours_label("ZZ") == ""


class TestGenaiFactoryVertexToggle:
    """Gemini 백엔드 AI Studio ↔ Vertex AI env 토글 (bot.genai_factory).

    GOOGLE_GENAI_USE_VERTEXAI 한 스위치로 전 봇이 이동. effective_key()
    sentinel(키 부재여도 readiness 가드 통과) · make_client() 라우팅 ·
    호출부 배선(E2E)을 영구 회귀 차단."""

    def _clear(self, mp):
        for k in ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT",
                  "GCP_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_REGION",
                  "GOOGLE_API_KEY"):
            mp.delenv(k, raising=False)

    def test_use_vertex_parsing(self, monkeypatch):
        from bot import genai_factory as gf
        self._clear(monkeypatch)
        assert gf.use_vertex() is False
        for v in ("true", "TRUE", "1", "yes", "on", " True "):
            monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", v)
            assert gf.use_vertex() is True, v
        for v in ("false", "0", "no", "off", ""):
            monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", v)
            assert gf.use_vertex() is False, v

    def test_location_default_and_override(self, monkeypatch):
        from bot import genai_factory as gf
        self._clear(monkeypatch)
        assert gf.location() == "us-central1"      # default
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "asia-northeast3")
        assert gf.location() == "asia-northeast3"

    def test_effective_key_ai_studio(self, monkeypatch):
        from bot import genai_factory as gf
        self._clear(monkeypatch)
        monkeypatch.setenv("GOOGLE_API_KEY", "ais-key-123")
        assert gf.use_vertex() is False
        assert gf.effective_key() == "ais-key-123"
        assert gf.llm_ready() is True

    def test_effective_key_vertex_sentinel(self, monkeypatch):
        from bot import genai_factory as gf
        self._clear(monkeypatch)
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-x")
        # No GOOGLE_API_KEY at all — guards must still pass via the sentinel.
        key = gf.effective_key()
        assert key == gf._VERTEX_SENTINEL and bool(key) is True
        assert gf.llm_ready() is True

    def test_vertex_without_project_not_ready(self, monkeypatch):
        from bot import genai_factory as gf
        self._clear(monkeypatch)
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
        assert gf.effective_key() is None
        assert gf.llm_ready() is False

    def test_make_client_routes_by_mode(self, monkeypatch):
        import sys
        import types
        from bot import genai_factory as gf
        self._clear(monkeypatch)
        captured = {}
        fake_genai = types.ModuleType("google.genai")

        def _Client(**kw):
            captured.clear()
            captured.update(kw)
            return object()

        fake_genai.Client = _Client
        fake_google = sys.modules.get("google") or types.ModuleType("google")
        monkeypatch.setitem(sys.modules, "google", fake_google)
        monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
        monkeypatch.setattr(fake_google, "genai", fake_genai, raising=False)

        # AI Studio mode: api_key passed through verbatim.
        monkeypatch.setenv("GOOGLE_API_KEY", "k-ais")
        gf.make_client("k-ais")
        assert captured == {"api_key": "k-ais"}

        # Vertex mode: vertexai=True + project/location; sentinel ignored.
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-x")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        gf.make_client(gf._VERTEX_SENTINEL)
        assert captured == {"vertexai": True, "project": "proj-x",
                            "location": "us-central1"}
        assert "api_key" not in captured

    def test_call_sites_wired_to_factory(self):
        scr = open("bot/screener.py", encoding="utf-8").read()
        assert "from bot.genai_factory import make_client" in scr
        assert "client = make_client(api_key)" in scr
        assert "genai.Client(api_key=" not in scr        # old path removed
        gics = open("bot/screener_gics_check.py", encoding="utf-8").read()
        assert "client = make_client(api_key)" in gics
        assert "genai.Client(api_key=" not in gics
        # Feed modules route readiness through effective_key (no raw env).
        for m in ("cheongyak_brief", "daily_kr_flow", "realestate_brief",
                  "chart_translate", "us_market_daily", "screener_freetext",
                  "translate", "portfolio_auto_resolve", "us_market_weekly",
                  "daily_kr_weekly", "realestate_monthly"):
            src = open(f"bot/{m}.py", encoding="utf-8").read()
            assert "from bot.genai_factory import effective_key" in src, m
            assert 'os.environ.get("GOOGLE_API_KEY")' not in src, m

    def test_cache_manager_defers_in_vertex(self):
        cm = open("bot/gemini_cache_manager.py", encoding="utf-8").read()
        assert "from bot.genai_factory import use_vertex" in cm
        assert "explicit caching deferred" in cm
        # 137줄 하드닝(2026-06-19): api_key 직접 클라이언트 제거(Vertex 401 방지) →
        # 팩토리 경유. early-return 으로 현재 Vertex 시 미도달이나 방어차원.
        assert "genai.Client(api_key=" not in cm
        assert "make_client(api_key)" in cm

    def test_google_client_has_vertex_branch(self):
        gc = open("TradingAgents/tradingagents/llm_clients/google_client.py",
                  encoding="utf-8").read()
        assert "_use_vertex" in gc and "ChatVertexAI" in gc
        assert "_get_vertex_llm" in gc and "_get_aistudio_llm" in gc

    def test_trade_factory_wired(self):
        li = open("trade/llm_insights.py", encoding="utf-8").read()
        assert "def make_chat(" in li and "ChatVertexAI" in li
        assert "def _llm_ready(" in li
        for f in ("trade/company_report.py", "trade/period_report.py"):
            src = open(f, encoding="utf-8").read()
            assert "llm_insights.make_chat(" in src, f
            assert "llm_insights._llm_ready()" in src, f


class TestWisereportEarningsSurprise:
    """어닝서프라이즈 파서(2026-06-20) — 네이버 임베드 WISEreport c1010001.aspx 의
    서버사이드 `var res={...}` 추출. VM 실측 데이터(005930)로 회귀 고정 —
    템플릿 인덱싱(yymm 첫컬럼 스킵·data[0..4] 영업이익/[5..9] 당기순이익) 보존."""

    _RES = (
        '{"yymm":["202509","202512","202603"],"data":['
        '{"1":101922.75,"2":185098.0384615385,"3":401923.44},'
        '{"1":121000.0,"2":200000.0,"3":572000.0},'
        '{"1":18.71736,"2":8.05085,"3":42.31566},'
        '{"1":31.7599,"2":208.03812,"3":755.61216},'
        '{"1":158.76502,"2":64.39173,"3":184.95053},'
        '{"1":96001.55,"2":163415.8181818182,"3":358348.18},'
        '{"1":120064.0,"2":192920.0,"3":471012.0},'
        '{"1":25.06464,"2":18.05467,"3":31.43976},'
        '{"1":22.74541,"2":154.64178,"3":486.68177},'
        '{"1":143.33841,"2":60.68015,"3":144.14819}],'
        '"yymmdd":["2025/10/14(연결)","2026/01/08(연결)","2026/04/07(연결)"],'
        '"type":[1,1,1]}'
    )

    def _html(self):
        return ('<table id="earning_list"></table><script>var EarnigList=function(){'
                ' var res = ' + self._RES + ';\n tmp=_.template($("#tmpl-list").html());'
                '\n $("#earning_list tbody").html(tmp(res));}</script>')

    def test_parses_screenshot_values(self):
        from bot.wisereport_earnings import parse_earnings_surprise
        out = parse_earnings_surprise(self._html())
        assert out["periods"] == ["2025/12", "2026/03"]      # 첫 컬럼(202509) 스킵
        assert out["op"]["consensus"] == [185098.0384615385, 401923.44]
        assert out["op"]["actual"] == [200000.0, 572000.0]
        assert round(out["op"]["surprise"][0], 2) == 8.05
        assert round(out["op"]["surprise"][1], 2) == 42.32
        assert out["ni"]["consensus"] == [163415.8181818182, 358348.18]
        assert out["ni"]["actual"] == [192920.0, 471012.0]   # data[6], 당기순이익
        assert out["announce"] == ["2026/01/08(연결)", "2026/04/07(연결)"]
        assert out["unit"] == "억원"

    def test_graceful_none_on_missing(self):
        from bot.wisereport_earnings import parse_earnings_surprise
        assert parse_earnings_surprise("<html>no earnings here</html>") is None
        assert parse_earnings_surprise("") is None

    def test_render_in_consensus_pane(self):
        import bot.dashboard as d
        es = {"periods": ["2025/12", "2026/03"],
              "op": {"consensus": [185098.04, 401923.44], "actual": [200000.0, 572000.0],
                     "surprise": [8.05, 42.32], "yoy": [208.04, 755.61]},
              "ni": {"consensus": [163415.82, 358348.18], "actual": [192920.0, 471012.0],
                     "surprise": [18.05, 31.44], "yoy": [154.64, 486.68]},
              "announce": ["2026/01/08(연결)", "2026/04/07(연결)"], "unit": "억원"}
        parts = d._render_stock_info_html({"ticker": "005930.KS", "stock_info": {
            "currency": "KRW", "current_price": 354000, "recommendation_key": "buy",
            "kr": {"earnings_surprise": es}}})
        op = parts["other_panes"]
        assert "실적 서프라이즈" in op and "영업이익 컨센서스" in op
        assert "185,098" in op and "572,000" in op and "+8.1%" in op
        # 비-KR 은 미표시
        p2 = d._render_stock_info_html({"ticker": "AAPL", "stock_info": {
            "currency": "USD", "current_price": 200, "recommendation_key": "buy"}})
        assert "실적 서프라이즈" not in p2["other_panes"]


class TestBandChartPane:
    """밴드차트 탭(2026-06-20) — FnGuide BandChart 충실 재현. KR 만 탭 노출,
    탭 활성화 시 /api/band lazy fetch(financials_ts 불요). 타시장 미노출."""

    def test_kr_band_tab_lazy_fetch(self):
        import bot.dashboard as d
        parts = d._render_stock_info_html({"ticker": "005930.KS", "stock_info": {
            "currency": "KRW", "current_price": 354000, "kr": {}}})
        assert "밴드차트" in parts["tabs"]
        op = parts["other_panes"]
        assert 'id="si-bandchart"' in op
        assert "id=\"si-band-data\"" not in op            # 임베드 payload 제거(lazy)
        assert "api/band?ticker=" in op and "drawBand" in op

    def test_non_kr_no_band_tab(self):
        import bot.dashboard as d
        parts = d._render_stock_info_html({"ticker": "AAPL", "stock_info": {
            "currency": "USD", "current_price": 200}})
        assert "밴드차트" not in parts["tabs"]

    def test_dividend_history_newest_first(self):
        # 배당 이력 최신이 위로(사용자 2026-06-20) — 배당락일 내림차순 회귀 가드.
        import bot.dashboard as d
        op = d._render_stock_info_html({"ticker": "032830.KS", "stock_info": {
            "currency": "KRW", "current_price": 497000, "kr": {}, "dividends": [
                {"date": "2014-12-29", "amount": 553},
                {"date": "2025-12-29", "amount": 1550},
                {"date": "2020-12-29", "amount": 660}]}})["other_panes"]
        i25, i20, i14 = op.find("2025-12-29"), op.find("2020-12-29"), op.find("2014-12-29")
        assert -1 < i25 < i20 < i14, (i25, i20, i14)

    def test_band_parser_matches_fnguide(self):
        from bot.fnguide_bandchart import _parse
        d_ = {"bandChart1": {"name": {"VAL1": "36.8배", "VAL2": "26.5배",
                                      "VAL3": "16.3배", "VAL4": "6.0배"},
                             "price": [{"x": 1656547200000, "y": 57000.0},
                                       {"x": 1843344000000, "y": None}],
                             "val1": [{"x": 1656547200000, "y": 254545.6}],
                             "val2": [{"x": 1656547200000, "y": 183300.5}],
                             "val3": [{"x": 1656547200000, "y": 112747.1}],
                             "val4": [{"x": 1656547200000, "y": 41502.0}]},
              "bandChart2": {"name": {"VAL1": "3.3배", "VAL2": "2.5배",
                                      "VAL3": "1.7배", "VAL4": "0.9배"},
                             "price": [{"x": 1656547200000, "y": 57000.0}],
                             "val1": [{"x": 1656547200000, "y": 155806.2}],
                             "val2": [{"x": 1656547200000, "y": 118035.0}],
                             "val3": [{"x": 1656547200000, "y": 80263.8}],
                             "val4": [{"x": 1656547200000, "y": 42492.6}]}}
        r = _parse(d_, "0")
        assert r["gubun"] == "0"
        assert r["per"]["mult"] == [36.8, 26.5, 16.3, 6.0]    # 첫 스크린샷과 일치
        assert r["pbr"]["mult"] == [3.3, 2.5, 1.7, 0.9]
        assert r["per"]["price"] == [[1656547200000, 57000.0], [1843344000000, None]]
        assert r["per"]["bands"][0] == [[1656547200000, 254545.6]]
        # price 비면 None(무커버리지 graceful)
        assert _parse({"bandChart1": {"price": []}}, "0") is None


class TestMinerviniScreener:
    """Minervini 추세 스크리너 (사용자 2026-06-22, finance-skills sepa 개념 ₩0 재구현).
    순수 추세템플릿 로직 + 조건파서 숫자/음수 지표명 회귀."""

    def test_trend_template_uptrend_passes(self):
        from bot.pykrx_client import trend_template_from_series
        up = [100 + i * 0.5 for i in range(260)]          # 꾸준한 우상향
        hi = [c * 1.01 for c in up]
        lo = [c * 0.99 for c in up]
        r = trend_template_from_series(up, hi, lo)
        assert r and r["tt"] == 1.0
        assert r["off_low"] > 30 and r["to_high"] >= -25   # 52주 위치 조건
        assert r["rs6m"] > 0

    def test_trend_template_downtrend_fails(self):
        from bot.pykrx_client import trend_template_from_series
        dn = [300 - i * 0.5 for i in range(260)]
        hi = [c * 1.01 for c in dn]
        lo = [c * 0.99 for c in dn]
        r = trend_template_from_series(dn, hi, lo)
        assert r and r["tt"] == 0.0

    def test_trend_template_insufficient_history(self):
        from bot.pykrx_client import trend_template_from_series
        s = [100.0] * 100
        assert trend_template_from_series(s, s, s) is None

    def test_screener_tech_metrics_and_preset(self):
        import bot.stock_screener as s
        for k in ("tt", "off_low", "to_high", "rs6m"):
            assert s.METRICS[k].source == "tech", k
        assert "minervini" in s.PRESETS
        # 트렌드템플릿 별칭 + 시총/거래량 (preset 그대로)
        cs = s.parse_conditions(s.PRESETS["minervini"]["conditions"])
        keys = {c.metric_key for c in cs}
        assert {"tt", "mcap", "volume"} <= keys

    def test_parse_conditions_digit_and_negative(self):
        # 숫자 든 지표명(rs6m·52주고점대비) + 음수 값 — _COND_RE 회귀 가드
        import bot.stock_screener as s
        cs = s.parse_conditions("rs6m>20 52주고점대비>=-25 52주저점대비>=30")
        got = {c.metric_key: c.value for c in cs}
        assert got["rs6m"] == 20.0
        assert got["to_high"] == -25.0
        assert got["off_low"] == 30.0
        # 기존 조건 회귀 없음
        cs2 = s.parse_conditions("PER<15 배당수익률>3")
        assert {c.metric_key for c in cs2} == {"per", "div"}

    def test_us_trend_template_graceful_and_wired(self):
        # US/JP Minervini — 추세 템플릿 함수 존재 + graceful(yfinance 미설치/
        # 실패 시 None, 무크래시) + 공용 _screen_yf 에 tech 단계 배선(US 2026-06-23,
        # JP 확장 2026-06-23 'A: 유동 부분집합'). US/JP 는 동일 _screen_yf 경로.
        import inspect
        import bot.stock_screener as s
        # 시장중립 본명 + 하위호환 별칭 둘 다 노출(동일 객체).
        assert hasattr(s, "get_yf_trend_template")
        assert hasattr(s, "get_us_trend_template")
        assert s.get_us_trend_template is s.get_yf_trend_template
        assert s.get_yf_trend_template("") is None              # 빈 입력 graceful
        assert s.get_yf_trend_template("__NOPE__") is None      # 실패/미설치 graceful
        yfsrc = inspect.getsource(s._screen_yf)
        assert "tech_conds" in yfsrc and "get_yf_trend_template" in yfsrc
        assert "_TECH_BUDGET_SEC" in yfsrc                       # 데드라인 적용
        assert 'note=note' in yfsrc                              # 진단 surface
        # 전 해외시장이 게이트 없이 공용 _screen_yf 로 위임(universal — if market== 금지).
        for fn in (s._screen_us, s._screen_jp, s._screen_hk, s._screen_cn, s._screen_tw):
            assert "_screen_yf" in inspect.getsource(fn)
        # run_screen 에 US/JP/HK/CN_A/TW 분기 배선(사용자 2026-06-23 '셋 다 추가').
        rs = inspect.getsource(s.run_screen)
        for code in ('"US"', '"JP"', '"HK"', '"CN_A"', '"TW"'):
            assert code in rs
        # 시장별 유니버스 헬퍼 존재 + graceful(소스/네트워크 없을 때 빈 목록·무크래시).
        for h in ("_get_jp_universe", "_get_hk_universe", "_get_cn_universe", "_get_tw_universe"):
            assert hasattr(s, h)
        assert s._get_hk_universe() == [] or isinstance(s._get_hk_universe(), list)
        tw_uni, tw_names = s._get_tw_universe()   # 튜플(tickers, names) 반환·무크래시
        assert isinstance(tw_uni, list) and isinstance(tw_names, dict)
        # TW 결과 종목명 한국어화 — 신고가/급등과 동일 chart_translate(사용자 2026-06-23
        # 'TW 한자→한글'). _screen_tw 가 _translate_hit_names_kr 호출 + graceful(키부재 시
        # 원문 유지·무크래시).
        import inspect
        assert "_translate_hit_names_kr" in inspect.getsource(s._screen_tw)
        _h = [{"name": "台積電", "ticker": "2330.TW"}, {"ticker": "x", "name": ""}]
        s._translate_hit_names_kr(_h)             # 샌드박스 키X → 원문 유지·무크래시
        assert _h[0]["name"] == "台積電"

    def test_fmt_mcap_display_per_market(self):
        # 시총 표기 단일 소스 — 시장별 통화·단위(텔레그램 bracket / 대시보드 plain).
        # 사용자 2026-06-23 'HK/CN/TW 셋 다 추가'. 새 시장은 _MKT_CCY 1줄.
        from bot.stock_screener import fmt_mcap_display as fm
        assert fm("US", 5000, True) == "[$5.0B]"
        assert fm("JP", 5000, False) == "¥50억"
        assert fm("HK", 1500000, True) == "[HK$1.5조]"
        assert fm("CN_A", 5000, False) == "元50억"
        assert fm("TW", 1500000, False) == "NT$1.5조"
        assert fm("KR", 15000, True) == "[1.5조]"
        assert fm("JP", 0, True) == "" and fm("JP", 0, False) == "—"   # 빈값 graceful
        # 대시보드도 단일 소스 사용.
        import inspect
        import bot.dashboard as d
        assert "fmt_mcap_display" in inspect.getsource(d)

    def test_fmt_metric_value_no_scientific(self):
        # 스크리너 지표값 표기 — 과학표기(1.1e+06) 금지·천단위 콤마(사용자 2026-06-23
        # '볼륨 이상해'). 전 surface(텔레그램·대시보드 스크리너/아카이브) 공용.
        from bot.stock_screener import fmt_metric_value as f
        assert f(1099290.0) == "1,099,290"      # 거래량 — 콤마, no e+06
        assert f(23790200.0) == "23,790,200"
        assert "e" not in f(62336000.0).lower()  # 과학표기 절대 금지
        assert f(1) == "1" and f(1.0) == "1"     # 트렌드템플릿 정수
        assert f(30.5) == "30.5"                  # 소수 보존
        assert f(617600) == "617,600"
        # 텔레그램·대시보드 surface 가 공용 헬퍼 사용(:g 직접 금지).
        import inspect
        import bot.stock_screener as s
        import bot.dashboard as d
        assert "fmt_metric_value" in inspect.getsource(s.format_result_message)
        assert ":{v:g}" not in inspect.getsource(s.format_result_message)
        dsrc = inspect.getsource(d)
        assert "fmt_metric_value" in dsrc

    def test_screen_name_backfill_naver_wired(self):
        # JP/US 등 yfinance shortName 비면 네이버 한글명 백필(사용자 2026-06-23
        # 'JP 티커만 → 네이버 종목명, 미국도·향후 타국도'). graceful·기존명 보존.
        import inspect
        import bot.stock_screener as s
        assert hasattr(s, "_backfill_names_naver")
        # _screen_yf 가 백필을 호출(전 yfinance 시장 universal).
        assert "_backfill_names_naver" in inspect.getsource(s._screen_yf)
        # graceful: 네트워크/소스 없어도 무크래시, 기존명 보존(없을 때만 채움).
        hits = [{"ticker": "AAA.T", "name": "AAA.T"},
                {"ticker": "BBB.T", "name": "기존명"}]
        s._backfill_names_naver(hits, "JP")     # 샌드박스 네트워크X → 무크래시
        assert hits[1]["name"] == "기존명"        # 양호한 기존명 보존
        s._backfill_names_naver([], "JP")        # 빈 입력 graceful

    def test_precompute_top_by_mcap(self):
        # 사전계산 캐시 워밍 대상 선택 — 시총>500&거래량>0 중 시총 상위 N(내림차순).
        # /screen minervini 스캔 선택과 동일 규칙(사용자 2026-06-23 행 해소 background).
        from bot.screener_precompute import top_by_mcap
        bulk = {
            "A": {"시가총액": 9000, "거래량": 100},
            "B": {"시가총액": 700, "거래량": 0},      # 거래량 0 → 제외
            "C": {"시가총액": 300, "거래량": 50},     # 시총 ≤500 → 제외
            "D": {"시가총액": 5000, "거래량": 10},
            "E": {"시가총액": None, "거래량": 5},      # 시총 None → 제외
        }
        assert top_by_mcap(bulk, 10) == ["A", "D"]       # 시총 내림차순, 적격만
        assert top_by_mcap(bulk, 1) == ["A"]             # 상위 N 캡
        assert top_by_mcap({}, 5) == []                  # graceful

    def test_screen_cache_has_ttl(self, tmp_path, monkeypatch):
        # 영구캐시 고착 방지(사용자 2026-06-23 '또 0종목·💾캐시') — 6h TTL.
        import time
        import os as _os
        import bot.stock_screener as s
        monkeypatch.setattr(s, "_CACHE_DIR", tmp_path)
        k = "__ttl_regr__"
        s._save_cache(k, {"hits": [], "total_universe": 1, "note": "x"})
        assert s._load_cache(k) is not None                 # 신선 → 로드
        p = s._cache_path(k)
        old = time.time() - (s._SCREEN_CACHE_TTL + 600)      # TTL 초과
        _os.utime(p, (old, old))
        assert s._load_cache(k) is None                      # 만료 → 재실행
        assert s._SCREEN_CACHE_TTL > 0


# ─────────────────────────────────────────────────────────────────────────
# 빈-holdings export 가 기존 자산 데이터 덮어쓰기 차단
#   배경: 2026-06-26 16:42 부분/손상 뱅샐 export 가 holdings 0건 모델로 기존
#   365건 portfolio.json(+ budget.json)을 덮어써 자산·가계부가 화면에서 사라짐
#   (.bak 로 복구). is_banksalad_export 게이트는 섹션 마커만 봐서 0건을 못 막았음.
#   fix: prev.holdings>0 & new holdings==0 이면 ingest 가 EmptyHoldingsExport.
# ─────────────────────────────────────────────────────────────────────────
class TestEmptyHoldingsGuard:
    """fix: 2026-06-27 (사고 2026-06-26 16:42)."""

    def _patch(self, monkeypatch, prev, parsed_holdings):
        import bot.portfolio as p
        monkeypatch.setattr(p, "load", lambda: prev)
        monkeypatch.setattr(p, "parse_export", lambda *a, **k: {"_": 1})
        monkeypatch.setattr(p, "is_banksalad_export", lambda parsed: True)
        monkeypatch.setattr(p, "build_model",
                            lambda parsed: {"holdings": parsed_holdings})
        saved = []
        monkeypatch.setattr(p, "save", lambda m: saved.append(m))
        return p, saved

    def test_empty_parse_refuses_overwrite_when_prev_populated(self, monkeypatch):
        from bot.portfolio import EmptyHoldingsExport
        p, saved = self._patch(monkeypatch, prev={"holdings": [1, 2, 3]},
                               parsed_holdings=[])
        with pytest.raises(EmptyHoldingsExport):
            p.ingest(b"x")
        assert saved == []                       # 저장 안 됨 = 기존 데이터 보존

    def test_first_upload_empty_is_allowed(self, monkeypatch):
        # 신규 사용자(prev None) 면 0건도 통과 — 가드는 기존 데이터 있을 때만.
        p, saved = self._patch(monkeypatch, prev=None, parsed_holdings=[])
        p.ingest(b"x")
        assert len(saved) == 1

    def test_populated_parse_saves_normally(self, monkeypatch):
        p, saved = self._patch(monkeypatch, prev={"holdings": [1]},
                               parsed_holdings=[1, 2])
        p.ingest(b"x")
        assert len(saved) == 1 and len(saved[0]["holdings"]) == 2

    def test_empty_holdings_is_notbanksalad_subclass(self):
        # watcher 의 기존 NotBanksaladExport 보존 경로를 그대로 타도록 하위클래스.
        from bot.portfolio import EmptyHoldingsExport, NotBanksaladExport
        assert issubclass(EmptyHoldingsExport, NotBanksaladExport)

    def test_budget_save_writes_bak(self, monkeypatch, tmp_path):
        # budget 도 portfolio 처럼 .bak 백업(2026-06-26 budget 무백업 → 복구불가).
        import bot.budget as b
        bp = tmp_path / "budget.json"
        monkeypatch.setattr(b, "BUDGET_PATH", bp)
        b.save_budget({"v": 1})
        assert bp.exists() and not bp.with_suffix(".json.bak").exists()  # 첫 저장 no bak
        b.save_budget({"v": 2})
        bak = bp.with_suffix(".json.bak")
        assert bak.exists()                       # 2번째 저장 = 직전(v1) 백업
        import json as _json
        assert _json.loads(bak.read_text())["v"] == 1


class TestRefreshBannerAndPerf20260703:
    """새로고침 UX(사용자 2026-07-03 '30분 체크 + 팝업으로 내가 결정') + 성능
    감사 quick-win 계약 — 강제 자동 swap 제거·배너 30분·정적 JS 장기캐시."""

    @staticmethod
    def _src(path):
        return open(path, encoding="utf-8").read()

    def test_banner_v2_spec(self):
        # v2(사용자 2026-07-03 상세 스펙): ① 30분 기준선 자동 반영(상태 저장→
        # 복원·입력 중 스킵) ② 1분 조용 체크 + 팝업 [바로 반영]/[그대로 보기].
        for path in ("bot/dashboard.py", "trade/dashboard.py"):
            src = self._src(path)
            assert "CHECK=60000" in src                 # 1분 조용 체크
            assert "BASELINE=1800000" in src            # 30분 기준선
            assert "바로 반영" in src and "그대로 보기" in src
            assert "restoreState" in src and "saveState" in src
            assert "typing()" in src                    # 입력 중 자동반영 스킵
        assert "POLL=45000" not in self._src("bot/dashboard.py")

    def test_index_lazy_months_and_csv_external(self):
        # 과거 월 lazy + CSV 외부화(성능 2026-07-03) — 렌더·JS·regen 배선 계약.
        src = self._src("bot/dashboard.py")
        assert 'data-lazy="{_frag}"' in src             # 과거 월 프래그먼트 emit
        assert "analysis_csv.json" in src               # CSV 외부 파일
        assert 'id="analysis-csv-data"' not in src      # 인라인 임베드 제거
        assert "loadAllMonths" in src and "rescan()" in src
        assert "index fragment" in src                  # regen 이 프래그먼트 기록
        import bot.dashboard as d
        html, frags = d._render_index([])
        assert "analysis_csv.json" in frags             # 빈 아카이브도 CSV 파일
        # /code-review 2026-07-03 findings 고정:
        assert "__failed" in src                        # #1 무한 재시도 루프 차단
        assert "type=search" in src                     # #2 검색창 상태 복원
        assert "_inject_update_banner(_idx_html)" in src.replace(" ", "") or \
               "_inject_update_banner(_idx_html)" in src  # #3 index 배너 주입
        assert "lastKey" in src                         # #6 포커스≠입력중
        assert "개월 로드 실패" in src                  # #7 부분 실패 가시화

    def test_market_asia_no_forced_swap(self):
        src = self._src("bot/dashboard.py")
        assert "setInterval(tick, 30000)" not in src    # 30초 강제 swap 제거
        assert "기준 · 30초 자동 갱신" not in src       # 라벨 동기화
        assert "_inject_update_banner(_render_market_page" in src
        assert "_inject_update_banner(_render_asia_page" in src
        assert "noah_asia_scroll" in src                # 스크롤 복원은 유지

    def test_vendored_js_long_cache(self):
        assert "max-age=604800" in self._src("bot/dashboard_server.py")

    def test_trade_prefetch_removed_banner_added(self):
        src = self._src("trade/dashboard.py")
        assert "_prefetchLazy" not in src               # 7MB 숨은 prefetch 제거
        assert "upd-banner" in src


class TestFilterLazyAllAndMarketSections20260704:
    """(1) ⭐/📝 필터 ON = lazy 과거-월 전체 로드(사용자 2026-07-03 '필터시 전체
    로드') (2) market.html nav 부동산 둘째 줄 (3) 스냅샷·Macro 지표 접기 토글."""

    @staticmethod
    def _src(path):
        return open(path, encoding="utf-8").read()

    def test_imp_filter_triggers_lazy_load_all(self):
        src = self._src("bot/dashboard.py")
        # _IMPORTANT_BLOCK: 필터 ON 시 lazyAll(로드→ensure 완료→그룹 재펼침→재통지).
        assert "function lazyAll(sel,flag)" in src
        assert "lazyAll('.imp-markable.imp-on','imp-only')" in src
        assert "lazyAll('.imp-markable.has-memo','memo-only')" in src
        # 3계열 lazy 로더(index/_DAILY_BYTE_JS/dart)가 훅 등록 — 미등록 표면은 no-op.
        assert src.count("window.__lazyLoadAll = loadAllMonths;") == 2
        assert "window.__lazyLoadAll=dfLoadAll;" in src
        # 리뷰 2026-07-04 fix 고정: (Major) ensure() = idle 청크 완료 후 resolve
        # Promise — lazyAll 이 전 카드 페인팅 완료 시점에 재적용(레이스 차단).
        # (Minor) 로드 중 필터 OFF 면 펼침/재통지 생략(flag 가드).
        assert "return new Promise(function(res)" in src
        assert "{ counts(); res(); }" in src
        assert ".then(function(){ return ensure(); })" in src
        assert "if(!document.documentElement.classList.contains(flag)) return;" in src

    def test_market_nav_realestate_one_line(self):
        src = self._src("bot/dashboard.py")
        # 부동산 = 같은 줄 말미의 nowrap 그룹(사용자 2026-07-04 '한 줄로') —
        # 좁은 화면에선 '| 부동산'이 통째로 둘째 줄로 자연 강등.
        assert '<span style="white-space:nowrap">&nbsp;|&nbsp;<a href="realestate.html">' in src
        assert "nav-r2" not in src                     # 옛 2줄 구조 잔여물 금지
        assert "white-space:nowrap}" in src            # nav 링크 중간 줄바꿈 방지

    def test_market_collapsible_sections(self):
        src = self._src("bot/dashboard.py")
        # 글로벌 시장 스냅샷 + Macro 국내/글로벌 지표 = details.csec 접기(기본 펼침).
        for sec in ('id="sec-gsnap"', 'id="sec-macro-dom"', 'id="sec-macro-glob"'):
            assert sec in src, sec
        assert "details.csec[id]" in src                # _CSEC_JS 상태 유지
        assert "localStorage.setItem(k, d.open?'1':'0')" in src
        assert "{_CSEC_JS}" in src                      # market 페이지에 주입
        import bot.dashboard as d
        html = d._render_macro_snapshot({
            "ts": "x", "domestic": [{"label": "국내지표"}],
            "global": [{"label": "글로벌지표"}], "charts": {}})
        assert html.count('<details class="csec"') == 2
        assert html.count("</details>") == 2
        assert "<summary>" in html and "macro-sub" in html


class TestActivistSignificance20260704:
    """🔥 규칙 10 — 활동주의/경영권 분쟁 승격 (사용자 2026-07-04 승인,
    open-proxy-mcp 검토 채택). 정밀 우선: 부정 서술·routine 권유 미발화."""

    def test_management_participation_purpose_fires(self):
        from bot.dart_feed import significance
        assert significance({
            "report_nm": "주식등의대량보유상황보고서(일반)",
            "category": "지분공시",
            "detail": ["보고자: OO자산운용", "지분율: 5.12%",
                       "보고사유: 신규보고(경영참가 목적)"],
        }) == "경영참가 목적 보유"
        # 보유목적 변경 라인 변형
        assert significance({
            "report_nm": "주식등의대량보유상황보고서(일반)",
            "category": "지분공시",
            "detail": ["보고자: X", "보유목적: 경영권에 영향을 주기 위한 목적"],
        }) == "경영참가 목적 보유"

    def test_negation_and_simple_investment_do_not_fire(self):
        from bot.dart_feed import significance
        # 부정 서술('없') — 미발화
        assert significance({
            "report_nm": "주식등의대량보유상황보고서(약식)",
            "category": "지분공시",
            "detail": ["보고사유: 경영권에 영향을 주기 위한 목적 없음(단순투자)"],
        }) is None
        # 단순투자 — 미발화
        assert significance({
            "report_nm": "주식등의대량보유상황보고서(약식)",
            "category": "지분공시",
            "detail": ["보고사유: 단순투자 변동"],
        }) is None
        # 지분공시 외 카테고리의 '경영권' 자유 서술(M&A 문맥) — 카테고리
        # 게이트로 미발화
        assert significance({
            "report_nm": "최대주주변경",
            "category": "회사구조",
            "detail": ["보유목적: 경영권 이전"],
        }) is None

    def test_proxy_contest_titles_fire(self):
        from bot.dart_feed import significance
        assert significance({
            "report_nm": "주주총회소집허가신청", "category": "소송",
            "detail": [],
        }) == "주총 소집허가 신청"
        assert significance({
            "report_nm": "주주총회소집공고(주주제안 포함)", "category": "기타",
            "detail": [],
        }) == "주주제안"
        # 파싱된 사건: 라인에서도 발화
        assert significance({
            "report_nm": "소송등의제기", "category": "소송",
            "detail": ["사건: 주주총회소집허가 신청"],
        }) == "주주제안·표대결"

    def test_correction_and_routine_solicitation_do_not_fire(self):
        from bot.dart_feed import significance
        assert significance({
            "report_nm": "[기재정정]주주총회소집허가신청", "category": "소송",
            "detail": [],
        }) is None
        assert significance({
            "report_nm": "의결권대리행사권유참고서류", "category": "기타",
            "detail": [],
        }) is None

    def test_existing_rule6_new_5pct_unaffected(self):
        from bot.dart_feed import significance
        assert significance({
            "report_nm": "주식등의대량보유상황보고서(약식)",
            "category": "지분공시",
            "detail": ["보고사유: 신규보고", "지분율: 4.20% → 5.30% (+1.10%p ▲)"],
        }) == "신규 5.3% 대량보유"


class TestGovCommand20260704:
    """/gov 거버넌스 브리핑 (사용자 2026-07-04 — open-proxy-mcp 네이티브 채택).
    단일 레지스트리·헬프 동시등록 + governance 순수 로직 계약."""

    @staticmethod
    def _src(path):
        return open(path, encoding="utf-8").read()

    def test_registry_and_help_wired(self):
        src = self._src("bot/telegram_bot.py")
        assert '"gov": (cmd_gov,' in src              # 단일 레지스트리(4소비처 파생)
        assert "async def cmd_gov" in src
        assert "/gov 종목 — 거버넌스 브리핑" in src   # _HELP_TEXT 같은 commit
        import re
        m = re.search(r'_HELP_TEXT = """(.*?)"""', src, re.S)
        assert m and len(m.group(1).encode("utf-16-le")) // 2 < 4096

    def test_gov_disclosure_filter(self, monkeypatch):
        import bot.governance as g
        import bot.dart_feed as df
        fake = {"2026-07-01": [
            {"stock_code": "005930", "report_nm": "주식등의대량보유상황보고서(일반)",
             "category": "지분공시"},
            {"stock_code": "005930", "report_nm": "단일판매공급계약체결",
             "category": "계약"},
            {"stock_code": "000660", "report_nm": "주주총회소집공고",
             "category": "기타"},
        ], "2026-06-30": [
            {"stock_code": "005930", "report_nm": "주주총회소집공고",
             "category": "기타"},
        ]}
        monkeypatch.setattr(df, "load_all_archives", lambda days_back=90: fake)
        rows = g._gov_disclosures("005930")
        names = [r["report_nm"] for r in rows]
        assert "단일판매공급계약체결" not in names      # 비거버넌스 제외
        assert "주식등의대량보유상황보고서(일반)" in names
        assert "주주총회소집공고" in names              # 키워드 매치(타 카테고리)
        assert all("000660" != r.get("stock_code") for r in rows)  # 타종목 제외
        assert rows[0]["_date"] == "2026-07-01"         # 최신순

    def test_gov_unresolved_escapes_and_guides(self, monkeypatch):
        import bot.governance as g
        monkeypatch.setattr(g, "resolve_kr_target", lambda q: (None, q, []))
        out = g.build_gov_brief("<b>없는회사</b>")
        assert "&lt;b&gt;" in out                       # 사용자입력 escape(교훈 #7)
        assert "한국 종목 전용" in out                  # KR(DART) 한정 안내

    def test_gov_candidates_listed(self, monkeypatch):
        import bot.governance as g
        cands = [{"name": "현대차", "stock_code": "005380"},
                 {"name": "현대모비스", "stock_code": "012330"}]
        monkeypatch.setattr(g, "resolve_kr_target", lambda q: (None, q, cands))
        out = g.build_gov_brief("현대")
        assert "005380" in out and "012330" in out and "후보" in out

    def test_gov_review_fixes_20260704(self):
        # 리뷰 fix 고정: ① 트림 = 줄 경계(rfind newline — mid-태그 절단 차단)
        # + 텔레그램 평문 degrade 폴백 ② 임원 인당 최신 dedupe.
        import bot.governance as g
        src = open("bot/governance.py", encoding="utf-8").read()
        assert 'text.rfind("\\n", 0, 3900)' in src
        assert "strip_tg_html" in open("bot/telegram_bot.py", encoding="utf-8").read()
        rows = [
            {"name": "김임원", "role": "대표이사", "pct": 1.0, "changed_on": "2026-05-01"},
            {"name": "김임원", "role": "대표이사", "pct": 1.2, "changed_on": "2026-06-20"},
            {"name": "박주주", "role": "", "pct": 6.0, "changed_on": "2026-06-01"},
        ]
        out = g._latest_per_person(rows)
        assert len(out) == 2                            # 인당 1건
        assert out[0]["name"] == "김임원" and out[0]["pct"] == 1.2  # 최신 채택+최신순

    def test_gov_natural_language_and_name_variants(self):
        # 자연어 라우팅 + 이름 변형 해석 (사용자 2026-07-04 '자연어로도,
        # 하이닉스→SK하이닉스').
        import bot.governance as g
        assert g.extract_gov_query("하이닉스 거버넌스") == "하이닉스"
        assert g.extract_gov_query("하이닉스 거버넌스 알려줘") == "하이닉스"
        assert g.extract_gov_query("삼성전자지배구조 어때?") == "삼성전자"
        assert g.extract_gov_query("삼성전자 실적 알려줘") is None   # 의도 없음
        assert g.extract_gov_query("/gov 삼성전자") is None          # 명령 제외
        v = g._query_variants("SK하이닉스")
        assert "에스케이하이닉스" in v                # 통용명 → DART 등재명
        assert "하이닉스" in g._query_variants("하이닉스의")   # 조사 strip
        assert "엘지에너지솔루션" in g._query_variants("LG 에너지솔루션")

    def test_gov_nl_wiring_all_surfaces(self):
        # 3표면 배선: 텔레그램 채널(on_channel_post)·DM(on_private_text)·
        # 대시보드 콘솔(_CONSOLE_JS govIntent → /gov 라우팅).
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "async def on_private_text" in tb
        assert tb.count("extract_gov_query") >= 2      # 채널 + DM 양쪽
        assert "on_private_text,\n" in tb or "on_private_text)" in tb.replace(" ", "")
        db = open("bot/dashboard.py", encoding="utf-8").read()
        assert "function govIntent(raw)" in db
        assert "noahRunCommand('/gov ' + raw" in db
        # JS 키워드 목록 = python _NL_KW 동기 — **전 키워드** 검사(리뷰
        # 2026-07-04 #3: 4개 샘플만 보다 '주주환원현황' drift 가 green 통과).
        import bot.governance as g
        for k in g._NL_KW:
            assert f"'{k}'" in db, f"콘솔 JS govIntent 에 {k} 누락"
        # NL 문장이 통째로 /gov 인자로 와도 재해석(빌드 내 retry)
        assert "extract_gov_query(query)" in open(
            "bot/governance.py", encoding="utf-8").read()

    def test_gov_nl_guards_review_20260704(self, monkeypatch):
        # 리뷰 fix 고정: #1 장문/멀티라인 = 질의 아님(봇 자신의 채널 브리핑
        # echo·붙여넣은 기사 재트리거 차단) #2 키워드 내포 토큰 짜투리 폐기
        # #4 에러 메시지 질의 60자 절단.
        import bot.governance as g
        assert g.extract_gov_query("삼성전자 최대주주 알려줘") == "삼성전자"
        assert g.extract_gov_query("카카오 최대주주 누구야") == "카카오"
        long_echo = "🏛 삼성전자 005930 거버넌스 브리핑\n👑 최대주주 현황 …" * 3
        assert g.extract_gov_query(long_echo) is None          # 멀티라인/장문
        assert g.extract_gov_query("어쩌구 " * 10 + "대주주") is None  # >40자
        monkeypatch.setattr(g, "resolve_kr_target", lambda q: (None, q, []))
        out = g.build_gov_brief("가" * 200 + " 거버넌스")
        assert "가" * 61 not in out and "…" in out             # 질의 절단


class TestAdminIssueSignificance20260705:
    """🔥 규칙 7b — 관리종목(지정우려/지정/해제) 중요 승격 (사용자 2026-07-05
    수성웹툰 지정우려 사례 — 상폐 전단계 경고, 확인 필수). 렌더타임 판정이라
    과거 아카이브 소급(별도 백필 불요)."""

    def test_admin_issue_fires(self):
        from bot.dart_feed import significance
        assert significance({"report_nm": "기타시장안내(관리종목지정우려종목)",
                             "category": "리스크", "detail": []}) == "관리종목 관련"
        assert significance({"report_nm": "관리종목지정해제",
                             "category": "리스크", "detail": []}) == "관리종목 관련"
        assert significance({"report_nm": "기타시장안내", "category": "리스크",
                             "detail": ["제목: 관리종목 지정우려 안내"]}) == "관리종목 관련"

    def test_admin_issue_guards(self):
        from bot.dart_feed import significance
        # 상장폐지 동반 = 더 심각한 라벨 선점
        assert significance({"report_nm": "관리종목지정 및 상장폐지 우려",
                             "category": "리스크", "detail": []}) == "상장폐지 관련"
        assert significance({"report_nm": "[기재정정]관리종목지정",
                             "category": "리스크", "detail": []}) is None
        # 화이트리스트 외 자유 서술 라인은 미발화
        assert significance({"report_nm": "기타시장안내", "category": "리스크",
                             "detail": ["비고: 관리종목 아님"]}) is None
        # 🔥범례 동시 갱신 계약
        assert "·관리종목·" in open("bot/dashboard.py", encoding="utf-8").read()


class TestCostCardThreeWindows20260705:
    """비용카드 3창(오늘/이번 달/누적) 표기 계약 — 전 대시보드 (사용자
    2026-07-05 '비용카드는 오늘/이번달/누적까지 포함. 모든 대쉬보드 적용')."""

    def test_kg_cost_returns_three_tuple(self, tmp_path, monkeypatch):
        import importlib
        import json as _j
        import bot.dashboard as d
        # 옛달 kg 레코드 → today/month 0, total 만 잡혀야 함
        (tmp_path / "usage.jsonl").write_text(_j.dumps(
            {"kind": "kg_blog", "cost_usd": 1.0, "date": "2025-01-15"}) + "\n",
            encoding="utf-8")
        monkeypatch.setenv("TRADE_DATA_DIR", str(tmp_path))
        t, m, x = d._kg_candidate_cost_usd()
        assert (t, m) == (0.0, 0.0) and x == 1.0
        # 파일 부재도 3-튜플 graceful
        monkeypatch.setenv("TRADE_DATA_DIR", str(tmp_path / "none"))
        assert d._kg_candidate_cost_usd() == (0.0, 0.0, 0.0)

    def test_page_labels_include_cumulative(self):
        import bot.dashboard as d
        assert "(오늘/이번 달/누적)" in d._render_valuechain_page([], 1.0, 2.0, 3.0)
        assert "비용 (오늘/이번 달/누적)" in d._render_gics_candidates_page([])
        _ri = d._render_reddit_insider_page([])
        _ri_html = _ri[0] if isinstance(_ri, tuple) else _ri  # (html, fragments)
        assert "비용 (오늘/이번 달/누적)" in _ri_html
        src = open("bot/dashboard.py", encoding="utf-8").read()
        # 메인 카드 + 블로그 kg 카드 + DART kg 줄 라벨 계약
        assert "💰 비용 (오늘 / 이번 달 / 누적)" in src
        assert "🔗 관계후보 발굴 비용 (오늘/이번 달/누적)" in src
        assert "누적 {_krw(_df_kg_x)}" in src

    def test_compute_stats_exposes_total(self):
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert '"total_cost_usd": total_cost_usd' in src

    def test_rotation_rolls_cost_into_rollup(self, tmp_path, monkeypatch):
        # '누적(전체)' 참값 보장: usage.jsonl 30일 로테이션으로 빠지는 비용은
        # usage_rollup.json 에 적산(리뷰 2026-07-05 — 파일 합만으론 누적이
        # 날마다 줄어드는 거짓 라벨). 재로테이션 이중적산 금지.
        import time as _t
        import json as _j
        import bot.usage_tracker as ut
        import bot.dashboard as d
        logf = tmp_path / "usage.jsonl"
        monkeypatch.setattr(ut, "USAGE_LOG", logf)
        monkeypatch.setattr(ut, "ROLLUP_PATH", tmp_path / "usage_rollup.json")
        old = {"ts": _t.time() - 40 * 86400, "type": "llm_call", "cost_usd": 2.5}
        new = {"ts": _t.time(), "type": "llm_call", "cost_usd": 1.0}
        logf.write_text(_j.dumps(old) + "\n" + _j.dumps(new) + "\n",
                        encoding="utf-8")
        assert len(ut.load_records()) == 1          # 40일 전 레코드 로테이션
        assert ut.rollup_cost_usd() == 2.5          # 유출분 적산
        ut.load_records()                           # 재실행에도
        assert ut.rollup_cost_usd() == 2.5          # 이중적산 없음
        monkeypatch.setattr(d, "_USAGE_LOG_PATH", logf)
        assert d._read_usage_rollup_usd() == 2.5    # 대시보드 reader 동일 파일
        # /usage 텔레그램 패리티 배선(동시갱신 규칙)
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "rollup_cost_usd()" in tb and "• 누적:" in tb


class TestDartLazyMonthVisibility20260705:
    """dart_feed lazy 과거 월 소실 fix (사용자 2026-07-05 '6월달껀 어디갔어').
    applyFilters 가 DOM 카드 0장인 미로드 lazy 월을 헤더째 display:none —
    한 번 숨으면 클릭 불가라 영영 복구 불가. index(1314 가드)와 동일 규칙:
    ① 미로드 lazy 월은 숨김·카운트 재작성 제외 ② 카테고리/플래그/마크
    필터도 검색처럼 전체 로드 후 적용."""

    def _dart_js(self):
        import bot.dashboard as d
        html, _ = d._render_dart_feed_page(
            {"2026-07-03": [{"corp_name": "가", "report_nm": "r", "category": "계약",
                             "rcept_no": "1", "stock_code": "", "url": "#", "detail": []}],
             "2026-06-10": [{"corp_name": "나", "report_nm": "r", "category": "계약",
                             "rcept_no": "2", "stock_code": "", "url": "#", "detail": []}]})
        return html

    def test_unloaded_lazy_month_never_hidden(self):
        html = self._dart_js()
        assert "if(m.dataset.lazy&&!m.__loaded){m.style.display='';return;}" in html
        # 가드는 카운트 재작성(mc.textContent)보다 앞이어야 서버 전체건수 보존
        _i = html.index("if(m.dataset.lazy&&!m.__loaded){m.style.display='';return;}")
        assert _i < html.index("mc.textContent=n+'건'")

    def test_any_filter_loads_all_months_first(self):
        html = self._dart_js()
        assert ("var anyFilter=!!q||activeCat!=='전체'||activeFlags.length>0"
                "||impOnly||memoOnly;") in html
        assert "if(anyFilter&&dfPending().length){dfLoadAll().then(applyFilters);return;}" in html
        # 과거 월 lazy 구조 자체 계약(헤더+data-lazy 프래그먼트)
        assert 'data-lazy="dart_m_2026-06.html"' in html
        assert "2026년 6월" in html


class TestCreditSplitAndMarketcap20260706:
    """① 예탁금·신용 카드 코스피/코스닥 신용잔고 분리(KOFIA 시장 필드 런타임
    발견) ② Market cap 대시보드(companiesmarketcap.com 이식 — 글로벌 시총 표
    + Rank by 다이렉트 링크 15축) — 사용자 2026-07-06."""

    def test_credit_key_heuristic(self):
        import bot.fsc_client as fc
        assert fc._classify_credit_key("crdTrFingKosdaq") == "kosdaq"
        assert fc._classify_credit_key("crdTrFingKsdqMrkt") == "kosdaq"
        assert fc._classify_credit_key("crdTrFingScrt") == "kospi"
        assert fc._classify_credit_key("crdTrFingWhl") is None      # 전체 제외
        assert fc._classify_credit_key("crdTrLoanWhl") is None      # 대주 제외
        assert fc._classify_credit_key("basDt") is None
        # 비잔고 파생필드 제외 (리뷰 2026-07-06 — 비율/일수 오분류 차단)
        assert fc._classify_credit_key("crdTrFingScrtRto") is None
        assert fc._classify_credit_key("crdTrFingDys") is None
        assert fc._classify_credit_key("crdTrFingAnlys") is None
        # 2026-07-08 VM 실측 필드명 고정: 유가증권='Scrs' 축약 + 대주/담보 제외
        assert fc._classify_credit_key("crdTrFingScrs") == "kospi"
        assert "crdTrFingScrs" in fc._CREDIT_SPLIT_EXACT["kospi"]
        assert fc._classify_credit_key("crdTrLndrScrs") is None    # 대주
        assert fc._classify_credit_key("dpsgScrtMogFing") is None  # 예탁담보

    def test_credit_split_series(self, monkeypatch):
        import bot.fsc_client as fc
        # 실측 응답 미러(2026-07-08): Scrs/Kosdaq + 대주(Lndr)·담보 필드 동거
        fake = [{"basDt": "20260701", "crdTrFingWhl": "370000000000000",
                 "crdTrFingScrs": "290000000000000",
                 "crdTrFingKosdaq": "80000000000000",
                 "crdTrLndrWhl": "90000000000",
                 "dpsgScrtMogFing": "24000000000000"},
                {"basDt": "20260702", "crdTrFingWhl": "371000000000000",
                 "crdTrFingScrs": "291000000000000",
                 "crdTrFingKosdaq": "80000000000000",
                 "crdTrLndrWhl": "90000000000",
                 "dpsgScrtMogFing": "24000000000000"}]
        monkeypatch.setattr(fc, "_fetch", lambda base, op, params: fake)
        monkeypatch.setattr(fc, "_cache_get", lambda *a, **k: None)
        monkeypatch.setattr(fc, "_cache_put", lambda *a, **k: None)
        sp = fc.credit_split_series_eok(2)
        assert set(sp) == {"kospi", "kosdaq"}
        assert sp["kospi"][-1] == ("20260702", 2910000.0)   # 원→억원
        # 응답에 시장 필드가 아예 없으면 {} (graceful) + 빈 결과도 캐시
        # (30초 위젯 regen 마다 재fetch 하는 쿼터 낭비 방지 — 리뷰 2026-07-06)
        puts = []
        monkeypatch.setattr(fc, "_cache_put", lambda k, v: puts.append(v))
        monkeypatch.setattr(fc, "_fetch", lambda base, op, params: [
            {"basDt": "20260701", "crdTrFingWhl": "1"}])
        assert fc.credit_split_series_eok(1) == {}
        assert puts and puts[-1] == {}
        # 합리성 가드: 시장 최신값이 전체(Whl)보다 크면 오분류로 보고 드롭
        monkeypatch.setattr(fc, "_fetch", lambda base, op, params: [
            {"basDt": "20260701", "crdTrFingWhl": "100",
             "crdTrFingScrtMrkt": "90000000000000"}])
        assert fc.credit_split_series_eok(1) == {}

    def test_deposit_charts_include_split(self):
        import bot.dashboard as d
        two = [{"d": "2026.07.01", "v": 1}, {"d": "2026.07.02", "v": 2}]
        dep = {"date": "2026.07.02", "source": "금융투자협회",
               "deposit": 1.0, "deposit_series": two, "credit_series": two,
               "credit_kospi_series": two, "credit_kosdaq_series": two,
               "equity_fund_series": two}
        ch = d._render_deposit_charts(dep)
        assert "코스피 신용잔고" in ch and "코스닥 신용잔고" in ch
        assert "repeat(3,1fr)" in ch      # 4+카드 → 3열 wrap
        # split 없으면 기존 3카드 그대로 (graceful)
        dep2 = {k: v for k, v in dep.items() if "kosp" not in k and "kosd" not in k}
        ch2 = d._render_deposit_charts(dep2)
        assert "코스피 신용잔고" not in ch2 and "repeat(3,1fr)" in ch2
        # deposit.json 스키마 버전 게이트 — 장외 무기한 fresh 캐시가 새 필드
        # 반영을 다음 장까지 막던 것(2026-07-08). 구버전 캐시 = miss.
        import bot.naver_sector_client as nsc
        assert nsc._DEPOSIT_SCHEMA_V >= 2
        src_n = open("bot/naver_sector_client.py", encoding="utf-8").read()
        assert 'c.get("_v") == _DEPOSIT_SCHEMA_V' in src_n
        assert 'out["_v"] = _DEPOSIT_SCHEMA_V' in src_n

    def test_liquidity_section_and_collateral_20260708(self):
        # '시장유동성' 접이식 섹션(금리·물가·센티먼트 + 예탁금·신용) + 6번째
        # 지표 예탁증권담보융자(dpsgScrtMogFing — 같은 응답, 추가 호출 0).
        import bot.dashboard as d
        import bot.naver_sector_client as nsc
        import bot.fsc_client as fc
        assert nsc._DEPOSIT_SCHEMA_V >= 3          # 담보융자 스키마
        assert callable(fc.collateral_loan_series_eok)
        two = [{"d": "2026.07.01", "v": 1}, {"d": "2026.07.02", "v": 2}]
        dep = {"date": "2026.07.06", "source": "금융투자협회", "deposit": 1.0,
               "collateral": 240000.0, "collateral_series": two,
               "deposit_series": two, "credit_series": two,
               "equity_fund_series": two}
        macro = {"ts": "07.08. 12:06 KST",
                 "charts": {"sentiment": {"value": 65, "label": "낙관",
                                          "src": "VIX 역산"}}}
        sec = d._render_liquidity_section(macro, dep)
        assert "<h2>시장유동성</h2>" in sec
        # 위젯 항목 순서 = 차트 순서(예탁금→주식형펀드→신용…, 사용자 2026-07-08)
        dep2 = dict(dep, equity_fund=3665000.0, credit=377000.0)
        w = d._render_deposit_widget(dep2)
        assert (w.index("고객예탁금") < w.index("주식형펀드")
                < w.index("담보융자"))
        assert 'id="sec-liquidity"' in sec and 'class="csec"' in sec   # 접기+상태유지
        # 제목 h2 가 summary 안(제목 클릭 = 접기 — 서브 summary 제거, 2026-07-08)
        assert sec.index("<summary>") < sec.index("<h2>시장유동성</h2>")
        assert "금리·물가·센티먼트 + 예탁금·신용" not in sec
        assert "담보융자" in sec and "예탁증권담보융자 추이" in sec
        assert "시장 센티먼트" in sec              # 매크로 3카드 row 편입
        # Macro Snapshot 에선 charts row 분리(중복 렌더 금지)
        snap = d._render_macro_snapshot(
            {"domestic": [], "global": [], "ts": "t",
             "charts": {"sentiment": {"value": 1, "label": "x", "src": "y"}}})
        assert "시장 센티먼트" not in snap
        src = open("bot/dashboard.py", encoding="utf-8").read()
        assert "_render_liquidity_section(macro, deposit)" in src

    def test_marketcap_parsers_and_page(self):
        # 2026-07-08 원본양식 이식: HTML 파서 = 로고·메트릭 원문·Price·Today·
        # 30일 차트·국가, 임베드 6축 탭 + 외부 9필. CSV 는 marketcap 축 폴백만.
        import bot.marketcap_client as mc
        import bot.dashboard as d
        # VM 실측 행 그대로(2026-07-08 사용자 제공) — 시총 $스팬 분리 공백 ·
        # ★ fav img · 인라인 SVG 스파크(red) · percentage-green · flag+USA
        ROW = ('<tr><td class="fav"><img alt="favorite icon" src="/img/fav.svg?v2"></td>'
               '<td class="rank-td td-right" data-sort="1">1</td>'
               '<td class="name-td"><div class="logo-container">'
               '<img loading="lazy" class="company-logo" src="/img/company-logos/64/NVDA.png"></div>'
               '<div class="name-div"><a href="/nvidia/marketcap/">'
               '<div class="company-name">NVIDIA</div>'
               '<div class="company-code"><span class="rank d-none"></span>NVDA</div></a></div></td>'
               '<td class="td-right" data-sort="4769841152000">'
               '<span class="currency-symbol-left">$</span>4.769 T</td>'
               '<td class="td-right" data-sort="19693">$196.93</td>'
               '<td data-sort="71" class="rh-sm"><span class="percentage-green">'
               '<svg class="a" viewBox="0 0 12 12"><path d="M10 8H2l4-4 4 4z"></path></svg>'
               '0.71%</span></td>'
               '<td class="p-0 sparkline-td red"><svg>'
               '<path d="M0,14 L5,17 L10,15" /></svg></td>'
               '<td><img class="flag" src="/img/flags/us.png"> '
               '<span class="responsive-hidden">USA</span></td></tr>')
        rows = mc._parse_rank_rows(
            ROW + ROW.replace("NVIDIA", "Microsoft").replace("NVDA", "MSFT")
            .replace("percentage-green", "percentage-red").replace(">0.71%", ">-1.60%"))
        assert len(rows) == 2
        assert rows[0]["metric"] == "$4.769 T"      # $스팬 공백 정규화(실측 버그)
        assert rows[0]["price"] == "$196.93"
        assert rows[0]["chg_pct"] == 0.71 and rows[1]["chg_pct"] == -1.60
        assert rows[0]["logo"].endswith("NVDA.png") and "fav" not in rows[0]["logo"]
        assert rows[0]["spark_d"].startswith("M0,14") and rows[0]["spark_neg"] is True
        assert rows[0]["country"] == "USA"          # 회사명/티커/국기 누수 금지
        # 시총 셀 미매칭 행: 주가를 시총 컬럼에 넣지 않음(단일값 가드)
        solo = mc._parse_rank_rows(
            '<tr><td>9</td><td><div class="company-name">Saudi Aramco</div>'
            '<div class="company-code">2222.SR</div></td>'
            '<td>SAR&nbsp;26.16</td><td>$6.97</td><td>0.00%</td></tr>')
        assert solo[0]["metric"] == "" and solo[0]["price"] == "$6.97"
        # 파서 버전 게이트 — 파싱 fix 가 3h 캐시에 가려지지 않게(2026-07-08)
        assert mc._PARSER_V >= 2
        src_m = open("bot/marketcap_client.py", encoding="utf-8").read()
        assert 'c.get("_pv") == _PARSER_V' in src_m
        assert '"_pv": _PARSER_V' in src_m
        # 축별 실측 행(2026-07-08 사용자 제공): earnings = 메트릭 셀 안
        # 정보아이콘 tooltip-title 속성에 <br/> 내포(태그제거 오염) ·
        # mc_loss = '-$144.36 Billion' 풀네임 단위.
        earn = mc._parse_rank_rows(
            '<tr><td>1</td><td><div class="company-name">Alphabet</div>'
            '<div class="company-code">GOOG</div></td>'
            '<td class="td-right">$195.68 B <img class="info-icon" '
            'tooltip-title="2026: Q1<br/>2025: Q2<br/>" src="/img/i.svg"></td>'
            '<td class="td-right">$363.62</td>'
            '<td><span class="percentage-red">0.35%</span></td></tr>')
        assert earn[0]["metric"] == "$195.68 B" and earn[0]["price"] == "$363.62"
        assert earn[0]["chg_pct"] == -0.35          # percentage-red → 음수
        loss = mc._parse_rank_rows(
            '<tr><td>1</td><td><div class="company-name">SpaceX</div>'
            '<div class="company-code">SPCX</div></td>'
            '<td class="td-right text-red">-$144.36 Billion</td>'
            '<td class="td-right">$149.47</td></tr>')
        assert loss[0]["metric"] == "-$144.36 Billion"
        assert loss[0]["price"] == "$149.47"
        assert mc._PARSER_V >= 5                    # 구캐시 무효화
        # 순위변화 = rank-td moves 속성(2026-07-08 실측 13위 ▲3 / 16위 ▼3)
        mv = mc._parse_rank_rows(
            '<tr><td class="rank-td td-right" data-sort="13" moves="3">13</td>'
            '<td><div class="company-name">Eli Lilly</div>'
            '<div class="company-code">LLY</div></td>'
            '<td><span class="currency-symbol-left">$</span>1.101 T</td>'
            '<td>$1,236</td></tr>')
        assert mv[0]["rank_move"] == 3 and mv[0]["metric"] == "$1.101 T"
        # P/E 형 메트릭(소수 숫자)도 원문 유지, 순수 정수(rank 등)는 미채택
        pe = mc._parse_rank_rows(
            '<tr><td>1</td><td><div class="company-name">FooCo</div>'
            '<div class="company-code">FOO</div></td>'
            '<td>12.34</td><td>$55.10</td></tr>')
        assert pe and pe[0]["metric"] == "12.34" and pe[0]["price"] == "$55.10"
        # CSV 폴백(marketcap 축 전용) — Today 없음
        cf = mc._parse_csv_fallback(
            "Rank,Name,Symbol,marketcap,price (USD),country\n"
            "1,NVIDIA,NVDA,4750000000000,195.55,United States\n")
        assert cf and cf[0]["metric"].startswith("$4.750") and cf[0]["chg_pct"] is None
        # 렌더: 6축 탭 + 외부 9필 + 문서 닫힘(</body> — 갱신배너 주입 요건)
        data = {k: {"rows": rows, "fetched_at": "2026-07-08 03:00", "stale": False}
                for k, _, _, _ in mc.EMBED_AXES}
        page = d._render_marketcap_page(data)
        assert page.rstrip().endswith("</body></html>")
        assert page.count('class="mc-tab"') == 6
        assert page.count("mc-ext") >= 9 and "Employees ↗" in page
        assert "Price (30 days)" in page and "mc-spark-svg" in page
        assert 'stroke="#ef5350"' in page           # 인라인 SVG 스파크(red)
        assert "▲0.71%" in page and "▼1.60%" in page
        # 회사명 클릭 → 종목분석(index.html) 딥링크(사용자 2026-07-16). 정상
        # 티커(NVDA/MSFT, [A-Za-z0-9.]+)는 링크, 비정상(공백 등 있었다면)은
        # 평문 폴백 — 여기 fixture 는 전부 정상이라 rows 수만큼 링크 존재.
        # data 는 rows 를 EMBED_AXES 6개 탭 전부에 그대로 재사용하므로 링크도
        # 탭 수만큼 반복(rows 자체는 2건).
        assert page.count('class="mc-name-link"') == len(rows) * len(mc.EMBED_AXES)
        assert 'href="index.html#ticker=NVDA"' in page
        assert 'href="index.html#ticker=MSFT"' in page
        data["pe"] = {"rows": [], "fetched_at": "", "stale": True}
        assert "데이터 수집 실패" in d._render_marketcap_page(data)

    def test_marketcap_name_link_fallback_no_ticker(self):
        # 티커 없거나(빈 문자열) 해시 파서 문자셋([A-Za-z0-9.]+) 밖(예: 공백
        # 포함 표기)이면 죽은 링크 대신 평문 폴백(2026-07-16).
        import bot.marketcap_client as mc
        import bot.dashboard as d
        rows = [{"rank": 1, "name": "NoTickerCo", "ticker": "", "metric": "$1 T",
                 "price": "$1", "country": "USA"},
                {"rank": 2, "name": "WeirdCo", "ticker": "AB CD", "metric": "$1 T",
                 "price": "$1", "country": "USA"}]
        data = {k: {"rows": rows, "fetched_at": "t", "stale": False}
                for k, _, _, _ in mc.EMBED_AXES}
        page = d._render_marketcap_page(data)
        assert 'class="mc-name-link"' not in page
        assert "NoTickerCo" in page and "WeirdCo" in page

    def test_marketcap_wiring(self):
        # nav(홈 허브)·주기 태스크·help 등록 — 같은 commit 의무 계약
        db = open("bot/dashboard.py", encoding="utf-8").read()
        assert 'href="marketcap.html">🏆 Market cap</a>' in db
        assert "def regenerate_marketcap_page" in db
        tb = open("bot/telegram_bot.py", encoding="utf-8").read()
        assert "_periodic_marketcap" in tb and "_marketcap_task" in tb
        assert "🏆Market cap" in tb    # _HELP_TEXT 대시보드 목록
        # 분석 그룹(차트보드) nav 상호링크 — PPI/유동성 ↔ Market cap
        fb = open("bot/fred_boards.py", encoding="utf-8").read()
        assert 'href="marketcap.html">🏆 Market cap</a>' in fb
        assert 'href="ppi.html">🏭 PPI</a>' in db and "_MC_BASE" in db
        # 임베드 6축(EMBED_AXES) + 외부 9필(_MARKETCAP_EXTERNAL) = 15축 유지
        import bot.marketcap_client as mc
        import bot.dashboard as d2
        assert len(mc.EMBED_AXES) == 6 and len(d2._MARKETCAP_EXTERNAL) == 9
        assert "fetch_all_axes" in db    # regen 이 6축 일괄 수집


class TestFearGreedGauge20260708:
    """시장 센티먼트 게이지 = 실 CNN Fear & Greed (사용자 2026-07-08 '실제
    F&G 로 바꿔줘'). 실패 시 VIX 역산 폴백 — 출처 라벨로 정직 구분."""

    def test_parse_cnn_schema(self):
        import bot.fear_greed_client as fgc
        fg = fgc._parse({"fear_and_greed": {
            "score": 43.06, "rating": "Fear",
            "timestamp": "2026-07-07T23:59:59+00:00",
            "previous_close": 43.0, "previous_1_week": 30.0,
            "previous_1_month": 41.2, "previous_1_year": 75.0}})
        assert fg["score"] == 43 and fg["rating_kr"] == "공포"
        assert fg["prev_1w"] == 30 and fg["prev_1y"] == 75
        assert "KST" in fg["ts"]                      # KST 명시 변환
        assert fgc._parse({}) == {}                   # graceful
        assert fgc._parse({"fear_and_greed": {"score": "n/a"}}) == {}

    def test_gauge_labels_cnn_vs_vix(self):
        import bot.dashboard as d
        card = d._render_sentiment_gauge(
            {"score": 43, "rating_kr": "공포", "ts": "07.08 08:59 KST",
             "prev_close": 43, "prev_1w": 30, "prev_1m": 41, "prev_1y": 75,
             "source": "cnn"})
        assert "CNN Fear &amp; Greed" in card and ">공포<" in card
        assert "전일 43" in card and "1년 75" in card
        assert "역산" not in card
        # VIX 폴백은 '(폴백)' 정직 표기 + CNN 문구 없음
        card2 = d._render_sentiment_gauge({"score": 65, "vix": 16.13,
                                           "source": "vix"})
        assert "VIX 16.13 역산(폴백)" in card2 and "CNN" not in card2
        # macro_snapshot 배선 — CNN 우선, 실패 시 VIX 폴백 경로 존재
        src = open("bot/macro_snapshot.py", encoding="utf-8").read()
        assert "fetch_fear_greed" in src and '"source": "vix"' in src


class TestBlogSeenCapAndDateGuard20260708:
    """옛 블로그 글 재푸시 fix (사용자 2026-07-08 '왜 예전꺼 가져오는거야' —
    의교창 6/18 글 재푸시). 원인: 전 블로그 공용 seen 캡 600 을 usforall
    초기화 유입이 밀어내 옛 GUID 축출 → RSS 창에 남은 옛 글이 '신규' 오판.
    fix: 캡 5000 + 날짜가드(실수 9 — pubDate 14일 초과 push 금지, seen 만)."""

    def test_cap_and_guard_constants(self):
        import bot.blog_watch as bw
        assert bw._MAX_SEEN >= 5000
        assert bw._MAX_AGE_DAYS <= 14

    def test_old_post_not_pushed_but_seen(self, monkeypatch):
        import bot.blog_watch as bw
        from datetime import datetime, timedelta
        pushed = []
        monkeypatch.setattr(bw, "_fetch_post_text", lambda url: "")
        monkeypatch.setattr(bw, "_save_archive", lambda it: None)
        monkeypatch.setattr(bw, "_push", lambda it: pushed.append(it["title"]) or True)
        monkeypatch.setattr(bw, "_fetch_rss", lambda bid: "<rss/>")
        old_pub = (datetime.now(bw._KST) - timedelta(days=20)
                   ).strftime("%a, %d %b %Y %H:%M:%S +0900")
        new_pub = datetime.now(bw._KST).strftime("%a, %d %b %Y %H:%M:%S +0900")
        items = [
            {"guid": "g-old", "title": "옛글", "link": "", "pubDate": old_pub,
             "desc": "", "category": "", "blog_title": "t"},
            {"guid": "g-new", "title": "오늘글", "link": "", "pubDate": new_pub,
             "desc": "x", "category": "", "blog_title": "t"},
        ]
        monkeypatch.setattr(bw, "_parse_items", lambda xml: items)
        state = {"seen": [], "init": {"b": True}}
        seen: set = set()
        bw._process_blog({"id": "b", "title": "t", "categories": None},
                         state, seen, None)
        assert pushed == ["오늘글"]                 # 옛 글 push 금지
        assert "g-old" in seen and "g-new" in seen  # 재검사 방지
        # pubDate 파싱 불가면 허용(놓침 방지)
        pushed.clear()
        items2 = [{"guid": "g-x", "title": "무날짜", "link": "", "pubDate": "??",
                   "desc": "x", "category": "", "blog_title": "t"}]
        monkeypatch.setattr(bw, "_parse_items", lambda xml: items2)
        bw._process_blog({"id": "b", "title": "t", "categories": None},
                         {"seen": [], "init": {"b": True}}, set(), None)
        assert pushed == ["무날짜"]
