"""trade.cost — resource/cost snapshot tests.

The point of this module is to tell the truth (everything's free) plus
surface real disk/quota numbers. Tests pin the formatting + that the
snapshot reflects pinned-item count and on-disk sizes.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trade import cost


class TestFmtBytes(unittest.TestCase):
    def test_units(self):
        self.assertEqual(cost.fmt_bytes(0), "0B")
        self.assertEqual(cost.fmt_bytes(512), "512B")
        self.assertEqual(cost.fmt_bytes(1536), "1.5KB")
        self.assertEqual(cost.fmt_bytes(1419595), "1.4MB")
        self.assertEqual(cost.fmt_bytes(5 * 1024**3), "5.0GB")


class TestCollect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        # Patch the module's data dir + watched paths into the temp dir.
        (self.dir / "store.db").write_bytes(b"x" * 1000)
        (self.dir / "customs.db").write_bytes(b"y" * 2000)
        (self.dir / "inbox.jsonl").write_bytes(b"z" * 500)
        media = self.dir / "media" / "2026-05-31"
        media.mkdir(parents=True)
        (media / "a.jpg").write_bytes(b"m" * 4000)
        self._patches = [
            mock.patch.object(cost, "_WATCHED", {
                "store.db": self.dir / "store.db",
                "customs.db": self.dir / "customs.db",
                "inbox.jsonl": self.dir / "inbox.jsonl",
            }),
            mock.patch.object(cost, "_MEDIA_DIR", self.dir / "media"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_collect_sizes(self):
        with mock.patch.object(cost.hs_map, "entries", return_value=[("라면", "1902301010")]):
            snap = cost.collect()
        self.assertEqual(snap["files"]["store.db"], 1000)
        self.assertEqual(snap["files"]["customs.db"], 2000)
        self.assertEqual(snap["media_bytes"], 4000)
        self.assertEqual(snap["data_total_bytes"], 1000 + 2000 + 500 + 4000)

    def test_api_calls_estimate_includes_sweeps(self):
        # daily calls = 4 ticks × (pins + scan + industry sweep). Pins flow
        # into the per-tick total; the chapter sweeps dominate.
        with mock.patch.object(cost.hs_map, "entries",
                               return_value=[("a", "1"), ("b", "22"), ("c", "333")]):
            snap = cost.collect()
        self.assertEqual(snap["pins"], 3)
        bd = snap["api_calls_breakdown"]
        self.assertEqual(bd["pins_per_tick"], 3)
        self.assertGreater(bd["scan_per_tick"], 90)        # ~97 chapters
        self.assertGreater(bd["industry_per_tick"], bd["scan_per_tick"])  # 2 windows
        expect = (bd["pins_per_tick"] + bd["scan_per_tick"]
                  + bd["industry_per_tick"]) * bd["ticks_per_day"]
        self.assertEqual(snap["api_calls_per_day"], expect)
        self.assertLess(snap["api_calls_per_day"], snap["api_quota"])  # under cap

    def test_missing_files_zero(self):
        with mock.patch.object(cost, "_WATCHED",
                               {"store.db": self.dir / "nope.db"}), \
             mock.patch.object(cost, "_MEDIA_DIR", self.dir / "nomedia"), \
             mock.patch.object(cost.hs_map, "entries", return_value=[]):
            snap = cost.collect()
        self.assertEqual(snap["files"]["store.db"], 0)
        self.assertEqual(snap["media_bytes"], 0)
        self.assertEqual(snap["pins"], 0)                  # no pins
        # sweeps still counted even with 0 pins
        self.assertGreater(snap["api_calls_per_day"], 0)


class TestFormatters(unittest.TestCase):
    def _snap(self):
        return {
            "files": {"store.db": 1000, "customs.db": 2000, "inbox.jsonl": 500},
            "media_bytes": 4000,
            "data_total_bytes": 7500,
            "disk_free_bytes": 5 * 1024**3,
            "disk_total_bytes": 10 * 1024**3,
            "pins": 2,
            "api_calls_per_day": 2,
            "api_quota": 10000,
        }

    def test_telegram_says_free_and_shows_resources(self):
        out = cost.format_telegram(self._snap())
        self.assertIn("무료", out)
        self.assertIn("LLM", out)           # LLM 비용 라인(호출 0 또는 집계)
        self.assertIn("store.db", out)
        self.assertIn("디스크 잔여", out)
        self.assertIn("추정", out)        # API 호출은 추정치 표기

    def test_telegram_shows_llm_cost_when_used(self):
        snap = self._snap()
        snap["llm"] = {
            "today": {"calls": 1, "in_tok": 100, "out_tok": 50,
                      "cost_usd": 0.001, "cost_krw": 2},
            "d7": {"calls": 1, "in_tok": 100, "out_tok": 50,
                   "cost_usd": 0.001, "cost_krw": 2},
            "d30": {"calls": 2, "in_tok": 200, "out_tok": 100,
                    "cost_usd": 0.003, "cost_krw": 4},
            "total_calls": 2,
        }
        out = cost.format_telegram(snap)
        self.assertIn("Gemini", out)
        self.assertIn("원", out)              # 원화 비용 표기

    def test_dashboard_line_compact(self):
        line = cost.format_dashboard_line(self._snap())
        self.assertIn("관세청 API 무료", line)
        self.assertIn("2/10,000", line)
        self.assertIn("LLM 0원", line)        # 미사용 시 0원 표기
        self.assertIn("디스크 잔여", line)
        # single-line: no newlines
        self.assertNotIn("\n", line)

    def test_dashboard_line_shows_llm_cost_when_used(self):
        snap = self._snap()
        snap["llm"] = {
            "today": {"calls": 1, "cost_krw": 45, "cost_usd": 0.03,
                      "in_tok": 0, "out_tok": 0},
            "d7": {"calls": 1, "cost_krw": 45, "cost_usd": 0.03,
                   "in_tok": 0, "out_tok": 0},
            "d30": {"calls": 1, "cost_krw": 45, "cost_usd": 0.03,
                    "in_tok": 0, "out_tok": 0},
            "total_calls": 1,
        }
        line = cost.format_dashboard_line(snap)
        self.assertIn("LLM 30일 45원", line)
        self.assertNotIn("\n", line)

    def test_dashboard_line_three_windows_when_available(self):
        # summary 가 month/total 을 주면 오늘/이번달/누적 3창 표기
        # (사용자 2026-07-05, 전 대시보드 비용카드 통일).
        snap = self._snap()
        base = {"calls": 1, "cost_krw": 45, "cost_usd": 0.03,
                "in_tok": 0, "out_tok": 0}
        snap["llm"] = {
            "today": dict(base), "d7": dict(base), "d30": dict(base),
            "today_kst": dict(base),
            "month": dict(base, cost_krw=90),
            "total": dict(base, cost_krw=300, calls=5),
            "total_calls": 5,
        }
        line = cost.format_dashboard_line(snap)
        self.assertIn("LLM 오늘 45원", line)
        self.assertIn("이번달 90원", line)
        self.assertIn("누적 300원", line)
        self.assertNotIn("\n", line)


if __name__ == "__main__":
    unittest.main()
