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
