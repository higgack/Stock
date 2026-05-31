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

    def test_api_calls_track_pins(self):
        with mock.patch.object(cost.hs_map, "entries",
                               return_value=[("a", "1"), ("b", "22"), ("c", "333")]):
            snap = cost.collect()
        self.assertEqual(snap["pins"], 3)
        self.assertEqual(snap["api_calls_per_day"], 3)

    def test_missing_files_zero(self):
        with mock.patch.object(cost, "_WATCHED",
                               {"store.db": self.dir / "nope.db"}), \
             mock.patch.object(cost, "_MEDIA_DIR", self.dir / "nomedia"), \
             mock.patch.object(cost.hs_map, "entries", return_value=[]):
            snap = cost.collect()
        self.assertEqual(snap["files"]["store.db"], 0)
        self.assertEqual(snap["media_bytes"], 0)
        self.assertEqual(snap["api_calls_per_day"], 0)


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
        self.assertIn("LLM: 없음", out)
        self.assertIn("store.db", out)
        self.assertIn("디스크 잔여", out)
        self.assertIn("핀 2개", out)

    def test_dashboard_line_compact(self):
        line = cost.format_dashboard_line(self._snap())
        self.assertIn("외부 API 무료", line)
        self.assertIn("2/10,000", line)
        self.assertIn("디스크 잔여", line)
        # single-line: no newlines
        self.assertNotIn("\n", line)


if __name__ == "__main__":
    unittest.main()
