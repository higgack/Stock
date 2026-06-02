"""trade.llm_usage — 비용·사용량 기록/집계 테스트."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from trade import llm_usage


class LlmUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "usage.jsonl"
        self.p = mock.patch.object(llm_usage, "USAGE_LOG", self.log)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_cost_usd(self):
        # pro: in $1.25/1M + out $10/1M → 1M+1M = 11.25
        self.assertAlmostEqual(
            llm_usage.cost_usd("gemini-2.5-pro", 1_000_000, 1_000_000), 11.25)
        self.assertEqual(llm_usage.cost_usd("unknown-model", 1000, 1000), 0.0)

    def test_record_and_summary(self):
        now = time.time()
        llm_usage.record("gemini-2.5-pro", 1000, 500, now=now)
        s = llm_usage.summary(now)
        self.assertEqual(s["total_calls"], 1)
        self.assertEqual(s["today"]["calls"], 1)
        self.assertGreater(s["today"]["cost_usd"], 0)
        self.assertEqual(s["today"]["cost_krw"],
                         int(round(s["today"]["cost_usd"] * llm_usage.KRW_PER_USD)))

    def test_old_record_excluded_from_today(self):
        now = time.time()
        llm_usage.record("gemini-2.5-pro", 1000, 500, now=now - 10 * 86400)
        s = llm_usage.summary(now)
        self.assertEqual(s["today"]["calls"], 0)
        self.assertEqual(s["d30"]["calls"], 1)

    def test_calls_today_empty(self):
        self.assertEqual(llm_usage.calls_today(), 0)


if __name__ == "__main__":
    unittest.main()
