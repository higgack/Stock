"""자정 결산 + 작동 원장 (사용자 2026-06-13 '실제 작동하는 날에만 결산,
오류는 알람') — 활동 게이트·오류 일1회 dedup·시스템 경고."""
import tempfile
import unittest
from pathlib import Path

from trade import run_ledger as rl
from trade.scripts import daily_digest as dg


class RunLedgerTests(unittest.TestCase):
    def setUp(self):
        rl.LEDGER_PATH = Path(tempfile.mkdtemp()) / "run_ledger.json"

    def test_bump_returns_count_for_daily_dedup(self):
        self.assertEqual(rl.bump("probe_fail"), 1)   # ==1 → 오류 알람 발화
        self.assertEqual(rl.bump("probe_fail"), 2)   # 같은 날 → 무음
        rl.bump("entrants", 7)
        self.assertEqual(rl.day_counts(rl._today())["entrants"], 7)
        self.assertEqual(rl.day_counts("2020-01-01"), {})


class DigestComposeTests(unittest.TestCase):
    def test_silent_when_no_activity(self):
        # '실제 작동하는 날에만' — 스윕만 돈 평일(갱신 0)도 무음
        self.assertIsNone(dg.compose("2026-06-10", 0, {}, False, 5))
        self.assertIsNone(dg.compose("2026-06-10", 0, {"sweeps": 4}, False, None))

    def test_forward_only_day(self):
        b = dg.compose("2026-06-10", 100, {}, False, None)
        self.assertIn("06/10 결산", b)
        self.assertIn("BeOn 포워드 100건", b)

    def test_full_day_with_errors_and_system_warnings(self):
        b = dg.compose("2026-06-10", 100,
                       {"sweeps": 4, "refresh": 1, "entrants": 7,
                        "probe_fail": 2, "scan_partial": 1}, True, 95)
        for frag in ("포워드 100건", "스윕 4회", "갱신 1회", "급증 7건",
                     "probe 오류 2회", "부분 스캔 1회",
                     "리스너 비활성", "95분 미갱신"):
            self.assertIn(frag, b)

    def test_error_only_day_still_sends(self):
        self.assertIsNotNone(
            dg.compose("2026-06-10", 0, {"scan_fail": 1}, False, None))

    def test_error_alerts_wired_in_scan(self):
        from trade.scripts import scan_customs as sc
        src = Path(sc.__file__).read_text(encoding="utf-8")
        self.assertIn('run_ledger.bump("scan_fail") == 1', src)     # 일1회
        self.assertIn('run_ledger.bump("probe_fail") == 1', src)
        self.assertIn('run_ledger.bump("scan_partial") == 1', src)
        self.assertIn('run_ledger.bump("refresh")', src)
        root = Path(sc.__file__).resolve().parents[2]
        self.assertIn("00:03:00 Asia/Seoul",
                      (root / "deploy" / "trade-bot-daily-digest.timer").read_text())


if __name__ == "__main__":
    unittest.main()
