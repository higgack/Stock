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


if __name__ == "__main__":
    unittest.main()
