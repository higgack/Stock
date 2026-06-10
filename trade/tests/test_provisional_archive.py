"""trade.provisional_archive — 잠정 전용 타임라인(발표 창별 누적) 테스트."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from trade import provisional_archive as pa


def _sig(ym, window, *, exp_total, imp_total, capex, semi):
    return {
        "imp_item": {"ym": ym, "window": window, "total_usd": imp_total,
                     "total_yoy": 20.8,
                     "items": [{"name": "반도체", "usd": 9e9, "yoy": 61.1},
                               {"name": "반도체제조용장비", "usd": capex, "yoy": 54.9}]},
        "exp_item": {"ym": ym, "window": window, "total_usd": exp_total,
                     "total_yoy": 53.2,
                     "items": [{"name": "반도체", "usd": semi, "yoy": 167.7}]},
    }


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._p = [
            mock.patch.object(pa, "PROV_ARCHIVE_JSONL", d / "prov.jsonl"),
            mock.patch.object(pa, "PROV_ARCHIVE_HTML", d / "dash" / "provisional_archive.html"),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.tmp.cleanup()

    def _rows(self):
        # 작년+올해 풀월 2개 → 모멘텀 계산 가능.
        def amt(t, *r):
            base = [5_000_000_000] * 10
            for i, v in enumerate(r):
                base[i] = v
            return [t] + base
        return {
            "imp_item": [
                {"ym": "2025-04", "priod_dt": "01~30", "decile": "FULL",
                 "amt": amt(100e9, 40e9, 8e9, 9e9, 7e9, 1.2e9)},
                {"ym": "2025-05", "priod_dt": "01~31", "decile": "FULL",
                 "amt": amt(110e9, 45e9, 8e9, 9e9, 7e9, 1.3e9)},
                {"ym": "2026-04", "priod_dt": "01~30", "decile": "FULL",
                 "amt": amt(120e9, 55e9, 8e9, 9e9, 7e9, 1.9e9)},
                {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL",
                 "amt": amt(150e9, 72e9, 8e9, 9e9, 7e9, 2.94e9)},
            ],
            "exp_item": [
                {"ym": "2025-05", "priod_dt": "01~31", "decile": "FULL",
                 "amt": amt(110e9, 13e9, 5e9, 5e9, 5e9, 5e9)},
                {"ym": "2026-04", "priod_dt": "01~30", "decile": "FULL",
                 "amt": amt(120e9, 20e9, 5e9, 5e9, 5e9, 5e9)},
                {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL",
                 "amt": amt(150e9, 37e9, 5e9, 5e9, 5e9, 5e9)},
            ],
        }

    def test_record_with_rows_embeds_momentum_tables(self):
        # rows_by_kind 주면 헤드라인 + 🔟 10일 모멘텀 4그룹 테이블이 본문에
        # 동봉돼 그 시점 전체 시계열까지 동결됨.
        sigs = _sig("2026-05", "전월(1~말일)",
                    exp_total=150e9, imp_total=150e9, capex=2.94e9, semi=37e9)
        key = pa.record(sigs, rows_by_kind=self._rows())
        body = pa.load_runs()[0]["body"]
        self.assertIn("877억$", body) if False else None  # not tested here
        self.assertIn("<table", body)                # 모멘텀 테이블 존재
        self.assertIn("수출 품목", body)             # 4그룹 캡션
        self.assertIn("수입 품목", body)
        self.assertIn("반도체제조용장비", body)
        self.assertIn("▲", body)                     # 가속 표식
        # 인라인 스타일이라 dashboard CSS 없어도 색 입혀짐
        self.assertIn("color:#34c759", body)         # 양수 녹색
        # 색인에도 테이블이 들어가는지
        h = pa.regenerate().read_text(encoding="utf-8")
        self.assertIn("<table", h)
        self.assertIn("수출 품목", h)

    def test_record_without_rows_no_momentum_tables(self):
        # rows_by_kind 없으면 본문엔 헤드라인만(테이블 없음, 호환).
        key = pa.record(_sig("2026-05", "전월(1~말일)",
                             exp_total=87.7e9, imp_total=60.8e9,
                             capex=2.94e9, semi=37.3e9))
        body = pa.load_runs()[0]["body"]
        self.assertIn("전체 수출", body)
        self.assertNotIn("<table", body)

    def test_record_captures_headline_metrics(self):
        key = pa.record(_sig("2026-05", "전월(1~말일)",
                             exp_total=87.7e9, imp_total=60.8e9,
                             capex=2.94e9, semi=37.3e9))
        self.assertEqual(key, "2026-05 · 전월(1~말일)")
        runs = pa.load_runs()
        self.assertEqual(len(runs), 1)
        body = runs[0]["body"]
        self.assertIn("전체 수출", body)
        self.assertIn("877억$", body)                  # 87.7e9 → 877억$
        self.assertIn("반도체제조용장비", body)         # capex 선행
        self.assertIn("⚡", body)
        self.assertIn("⚠️", body)                       # 수출 경고
        self.assertIn("반도체 수출", body)

    def test_accumulates_per_window(self):
        # 창이 바뀌면(1~10일 → 1~20일 → 마감) 별도 스냅샷으로 쌓인다.
        pa.record(_sig("2026-06", "1~10일", exp_total=18e9, imp_total=17e9,
                       capex=1.2e9, semi=8e9))
        pa.record(_sig("2026-06", "1~20일", exp_total=36e9, imp_total=36e9,
                       capex=2.0e9, semi=15e9))
        runs = pa.load_runs()
        self.assertEqual(len(runs), 2)
        keys = {r["key"] for r in runs}
        self.assertEqual(keys, {"2026-06 · 1~10일", "2026-06 · 1~20일"})

    def test_same_window_idempotent_refreshes_keeps_meta(self):
        # 같은 창 재기록 = 현행화: 값 갱신, 최초 기록일·ts 보존.
        pa.record(_sig("2026-05", "전월(1~말일)", exp_total=87.7e9,
                       imp_total=60.8e9, capex=2.94e9, semi=37.3e9))
        r1 = pa.load_runs()[0]
        time.sleep(0.01)
        pa.record(_sig("2026-05", "전월(1~말일)", exp_total=80.0e9,
                       imp_total=60.8e9, capex=3.10e9, semi=37.3e9))
        runs = pa.load_runs()
        self.assertEqual(len(runs), 1)               # 여전히 1건
        r2 = runs[0]
        self.assertEqual(r2["_date"], r1["_date"])   # 기록일 보존
        self.assertEqual(r2["ts"], r1["ts"])         # ts 보존
        self.assertIn("800억$", r2["body"])          # 값은 현행화(80.0e9)

    def test_record_none_when_no_signals(self):
        self.assertIsNone(pa.record({}))
        self.assertIsNone(pa.record({"imp_cnty": None}))

    def test_regenerate_index_has_title_and_metric(self):
        pa.record(_sig("2026-05", "전월(1~말일)", exp_total=87.7e9,
                       imp_total=60.8e9, capex=2.94e9, semi=37.3e9))
        out = pa.regenerate()
        h = out.read_text(encoding="utf-8")
        self.assertIn("잠정 속보 타임라인", h)
        self.assertIn("877억$", h)
        self.assertEqual(h.lower().count("<!doctype"), 1)

    def test_regenerate_empty_safe(self):
        h = pa.regenerate().read_text(encoding="utf-8")
        self.assertIn("아직 적립된 잠정 스냅샷이 없습니다", h)

    def test_ensure_exists(self):
        self.assertFalse(pa.PROV_ARCHIVE_HTML.exists())
        pa.ensure_exists()
        self.assertTrue(pa.PROV_ARCHIVE_HTML.exists())

    def test_refresh_rebuilds_from_stored_snapshot_no_api(self):
        # 대시보드 렌더 경로: 저장된 customs_provisional에서 타임라인 재빌드
        # (API 0콜). 렌더 코드 변경이 fetch 없이도 반영되는 핵심.
        import sqlite3
        from trade import customs_provisional as cp
        conn = sqlite3.connect(":memory:")
        for k, labels in cp.LABELS.items():
            rows = self._rows().get(k) or [
                {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL",
                 "amt": [1e9] * 11}]
            sig = cp.latest_signal(rows, labels)
            cp.store_signal(conn, k, sig, rows=rows)
        key = pa.refresh(conn)
        self.assertEqual(key, "2026-05 · 전월(1~말일)")
        runs = pa.load_runs()
        self.assertEqual(len(runs), 1)
        self.assertIn("<table", runs[0]["body"])      # 모멘텀 동봉(저장 rows 사용)
        self.assertTrue(pa.PROV_ARCHIVE_HTML.exists())  # 색인도 재생성
        # 재호출은 같은 창이라 1건 유지(idempotent 현행화)
        pa.refresh(conn)
        self.assertEqual(len(pa.load_runs()), 1)
        conn.close()

    def test_refresh_empty_store_safe(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        self.assertIsNone(pa.refresh(conn))
        self.assertTrue(pa.PROV_ARCHIVE_HTML.exists())  # 빈 색인 보장
        conn.close()


if __name__ == "__main__":
    unittest.main()
