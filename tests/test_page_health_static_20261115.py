"""화면 감사가 고정 문구를 '며칠째 그대로' 로 세지 않는다 (2026-11-15 결산 ❌ 17건).

첫 주 결산 ❌ 17건 중 15건이 상태가 아니라 고정 글이었다 — 오류 카탈로그의 과거
기록 라벨, Daily Byte 브리프 본문의 소제목, 밸류체인 면책문, 크립토 카드의 설계
설명. 나머지 둘(리서치 수율 279/300 · 모델 미기록 과거분)은 상태지만 결함이
아니었다. 판정은 생성부가 밝힌다(`static_region` · ℹ️ 접두) — 감사가 낱말로
추측하지 않는다. 화면 함수를 그대로 태워 잰다(#20·#35).
"""
import time

from bot import page_health as ph


def _warns(html):
    return [f["text"] for f in ph.findings(html)]


class TestStaticRegion:
    def test_static_region_is_skipped_but_outside_is_not(self):
        html = ("<div>" + ph.static_region("<div>⚠️ 고정 설명</div>")
                + "</div><div>⚠️ 진짜 상태 경고</div>")
        got = _warns(html)
        assert got == ["⚠️ 진짜 상태 경고"], got

    def test_two_regions_do_not_swallow_the_middle(self):
        """비탐욕 매칭 — 두 구역 사이의 상태 경고는 남는다(탐욕이면 통째로 먹는다)."""
        html = (ph.static_region("<p>⚠️ a</p>") + "<p>⚠️ 사이 경고</p>"
                + ph.static_region("<p>⚠️ b</p>"))
        assert _warns(html) == ["⚠️ 사이 경고"]


class TestScreensDeclareStaticText:
    def test_errors_catalog_records(self, monkeypatch):
        import bot.dashboard as d
        monkeypatch.setattr(d, "_detect_issues", lambda r: ["💬 감정 분석 섹션 미완성"])
        html = d._render_errors_page(
            [{"ticker": "AAPL", "trade_date": "2026-11-01",
              "analyzed_at": "2026-11-01T10:00:00", "rating": "Hold"}], [])
        assert "섹션 미완성" in html                      # 화면엔 그대로 보인다
        assert not [t for t in _warns(html) if "섹션 미완성" in t]

    def test_daily_byte_brief_body(self):
        import bot.dashboard as d
        runs = [{"_date": "2026-11-10", "ts": "2026-11-10T19:01:00",
                 "cost_krw": 1.0, "elapsed_sec": 1, "kind": "daily",
                 "body": "<b>⚠️ 경고 시그널</b>\n삼성전자 양→음", "_filename": "a.json"}]
        html = d._render_daily_byte_page(runs)
        assert "경고 시그널" in html
        assert not [t for t in _warns(html) if "경고 시그널" in t]

    def test_realestate_brief_body(self):
        import bot.dashboard as d
        runs = [{"_date": "2026-11-10", "ts": "2026-11-10T19:01:00",
                 "cost_krw": 1.0, "body": "⚠️ 리스크 요인\n금리", "_filename": "r.json"}]
        html = d._render_realestate_page(runs)
        assert "리스크 요인" in html
        assert not [t for t in _warns(html) if "리스크 요인" in t]

    def test_valuechain_disclaimer(self):
        import bot.dashboard as d
        html = d._render_valuechain_page([])
        assert "자동발굴(LLM·관세청)" in html
        assert not [t for t in _warns(html) if "자동발굴" in t]

    def test_crypto_design_note(self):
        import bot.market_timing as mt
        html = mt.render_market_timing_page({
            "markets": {}, "macro": {},
            "crypto": {"price": 60000, "ath_change_pct": -12.5, "score": 70.0}})
        assert "ATH 대비 낙폭 한 가지" in html
        assert not [t for t in _warns(html) if "낙폭" in t]


class TestUnpricedNoteGrade:
    _NOW = 1_800_000_000.0

    def _sp(self, last):
        return {"no_model": 15, "no_model_first": last - 240, "no_model_last": last}

    def test_past_unrecorded_is_info_but_keeps_facts(self):
        from bot.usage_tracker import unpriced_notes
        n = unpriced_notes(self._sp(self._NOW - 8 * 86400), "현재 원장", now=self._NOW)[0]
        assert n.startswith("ℹ️"), n
        assert "15콜(현재 원장)" in n and "실제 비용은 더 큼" in n and "과거분" in n, n
        assert not ph.findings(f"<div>{n}</div>")

    def test_recent_unrecorded_stays_warning(self):
        from bot.usage_tracker import unpriced_notes
        n = unpriced_notes(self._sp(self._NOW - 2 * 86400), "현재 원장", now=self._NOW)[0]
        assert n.startswith("⚠️") and "과거분" not in n, n

    def test_untimed_unrecorded_stays_warning(self):
        """시각이 없으면 최근인지 못 잰다 — 과거분이라 단정하지 않는다(#54)."""
        from bot.usage_tracker import unpriced_notes
        n = unpriced_notes({"no_model": 3}, "30일", now=self._NOW)[0]
        assert n.startswith("⚠️"), n

    def test_default_now_is_wall_clock(self):
        from bot.usage_tracker import unpriced_notes
        n = unpriced_notes(self._sp(time.time() - 60), "30일")[0]
        assert n.startswith("⚠️"), n


class TestResearchYieldGrade:
    _ROWS = [{"target": 1.0, "rating": "매수"}] * 279 + [{"target": None, "rating": ""}] * 21

    def test_converged_yield_is_info(self):
        import bot.dashboard as d
        from bot.naver_research_client import detail_yield_note
        note = detail_yield_note(self._ROWS, budget_left=0)
        assert note.startswith("ℹ️") and "279/300" in note, note
        html = d._research_window_html({"window": note})
        assert "⚠️" not in html and "279/300" in html, html
        assert not ph.findings(html)

    def test_filling_yield_and_window_cut_stay_warning(self):
        import bot.dashboard as d
        from bot.naver_research_client import detail_yield_note
        filling = detail_yield_note(self._ROWS, budget_left=21, fetched=40)
        assert not filling.startswith("ℹ️"), filling
        assert "⚠️" in d._research_window_html({"window": filling})
        # 창 절단(진짜 경고)이 앞에 붙으면 수렴 문구가 뒤따라도 ⚠️ 다
        cut = "30일 중 20일만 받음 · " + detail_yield_note(self._ROWS)
        assert "⚠️" in d._research_window_html({"window": cut})
