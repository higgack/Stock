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

    def test_new_unmatched_triggers_send_and_escapes(self):
        # 신규 미매칭 후보만 있어도 발화 (활동 없는 날도) + HTML 이스케이프
        b = dg.compose("2026-06-10", 0, {}, False, None,
                       new_unmatched=["부채비율<200 소재", "신소재 XYZ"])
        self.assertIsNotNone(b)
        self.assertIn("신규 미매칭 품목 후보 2건", b)
        self.assertIn("신소재 XYZ", b)
        self.assertIn("&lt;200", b)             # < 이스케이프(실수 #7)
        self.assertNotIn("<200", b)

    def test_new_unmatched_seen_set_first_run_seeds(self):
        from unittest import mock
        from trade import reference_book
        rows_ret, cand_ret = [], [("신소재 XYZ", ["듣보종목"], 2)]
        dg._UNMATCHED_SEEN = Path(tempfile.mkdtemp()) / "seen.json"
        with mock.patch.object(reference_book, "build_rows", return_value=rows_ret), \
                mock.patch.object(reference_book, "unmatched_candidates",
                                  return_value=cand_ret):
            first = dg._new_unmatched()          # 첫 실행 → seed 후 무보고
            second = dg._new_unmatched()         # 동일 후보 → 이미 seen, 무보고
            with mock.patch.object(reference_book, "unmatched_candidates",
                                   return_value=cand_ret + [("새거", ["새종목"], 1)]):
                third = dg._new_unmatched()      # 신규 1건만 보고
        self.assertEqual(first, [])              # 백로그 flood 방지(실수 #9)
        self.assertEqual(second, [])
        self.assertEqual(third, ["새거"])

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


class ForwardCountKstBoundaryTests(unittest.TestCase):
    """검토에서 잡은 버그 2종 회귀 차단: open_db(path) 필수 + ingested_at
    UTC → KST 날짜 경계 (새벽 수집분 = UTC 전일 문자열이라 substr 비교
    불가 — 전일·당일 프리필터 후 행별 KST 정밀 판정)."""

    def test_kst_boundary(self):
        import itertools
        import tempfile
        from trade.store import open_db
        dg._STORE_PATH = Path(tempfile.mkdtemp()) / "store.db"
        conn = open_db(dg._STORE_PATH)
        cols_row = conn.execute("PRAGMA table_info(alerts)").fetchall()
        seq = itertools.count(1)

        def ins(ts):
            i = next(seq)
            vals = {}
            for (_c, name, ctype, _nn, _d, _pk) in cols_row:
                if name == "id":
                    continue
                if name in ("ingested_at", "posted_at"):
                    vals[name] = ts
                elif name == "superseded_by":
                    vals[name] = None
                elif name == "source_message_id":
                    vals[name] = i
                elif "INT" in str(ctype).upper():
                    vals[name] = 0
                else:
                    vals[name] = f"x{i}" if name == "dedup_key" else ""
            ph = ",".join(":" + c for c in vals)
            conn.execute(
                f"INSERT INTO alerts ({','.join(vals)}) VALUES ({ph})", vals)

        ins("2026-06-09T16:30:00+00:00")   # KST 06/10 01:30 — 포함되어야
        ins("2026-06-10T05:00:00+00:00")   # KST 06/10 14:00 — 포함
        ins("2026-06-10T16:00:00+00:00")   # KST 06/11 — 제외
        ins("2026-06-09T10:00:00+00:00")   # KST 06/09 — 제외
        conn.commit()
        conn.close()
        self.assertEqual(dg._forward_count("2026-06-10"), 2)
