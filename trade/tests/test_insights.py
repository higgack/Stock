"""trade.insights — fingerprint/state + refresh_signals 변동 게이트 테스트."""

import sqlite3
import unittest
from contextlib import contextmanager
from unittest import mock

from trade import insights


def _seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE industry_series (industry TEXT PRIMARY KEY, "
                 "months_json TEXT, imports_json TEXT, updated_ts REAL)")
    conn.execute("CREATE TABLE mti_series (mti6 TEXT PRIMARY KEY, "
                 "payload_json TEXT, import_json TEXT, updated_ts REAL)")
    return conn


def _put_industry(conn, months_json, ts):
    conn.execute("DELETE FROM industry_series")
    conn.execute("INSERT INTO industry_series VALUES (?,?,?,?)",
                 ("반도체", months_json, None, ts))
    conn.commit()


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.conn = _seed_conn()

    def tearDown(self):
        self.conn.close()

    def test_stable_across_identical_values(self):
        _put_industry(self.conn, '{"2026-04": 100}', 1.0)
        a = insights.data_fingerprint(self.conn)
        _put_industry(self.conn, '{"2026-04": 100}', 999.0)   # updated_ts만 다름
        self.assertEqual(a, insights.data_fingerprint(self.conn))

    def test_changes_when_value_changes(self):
        _put_industry(self.conn, '{"2026-04": 100}', 1.0)
        a = insights.data_fingerprint(self.conn)
        _put_industry(self.conn, '{"2026-04": 200}', 1.0)     # 값이 바뀜
        self.assertNotEqual(a, insights.data_fingerprint(self.conn))

    def test_missing_tables_dont_crash(self):
        bare = sqlite3.connect(":memory:")
        self.assertIsInstance(insights.data_fingerprint(bare), str)
        bare.close()

    def test_state_roundtrip(self):
        self.assertIsNone(insights.get_state(self.conn, "k"))
        insights.set_state(self.conn, "k", "v1")
        self.assertEqual(insights.get_state(self.conn, "k"), "v1")
        insights.set_state(self.conn, "k", "v2")          # upsert
        self.assertEqual(insights.get_state(self.conn, "k"), "v2")


class RefreshSignalsGateTests(unittest.TestCase):
    """refresh_signals.main: baseline-silent → no-change-silent → notify-on-change."""

    def setUp(self):
        self.conn = _seed_conn()
        _put_industry(self.conn, '{"2026-04": 100}', 1.0)

    def tearDown(self):
        self.conn.close()

    def _run(self):
        from trade.scripts import refresh_signals

        @contextmanager
        def fake_session(*a, **k):
            yield self.conn

        with mock.patch.object(refresh_signals.customs, "session", fake_session), \
             mock.patch.object(refresh_signals, "_send_dm",
                               return_value=True) as send:
            refresh_signals.main([])
        return send

    def test_first_run_is_baseline_silent(self):
        send = self._run()
        send.assert_not_called()                       # 최초 = baseline, 무음
        self.assertIsNotNone(insights.get_state(self.conn, "data_fp"))

    def test_no_change_is_silent(self):
        self._run()                                    # baseline 기록
        send = self._run()                             # 동일 데이터
        send.assert_not_called()

    def test_change_notifies_once_and_advances_fp(self):
        self._run()                                    # baseline
        _put_industry(self.conn, '{"2026-04": 200}', 1.0)   # 데이터 변동
        before = insights.get_state(self.conn, "data_fp")
        send = self._run()
        send.assert_called_once()                       # 변동 시 DM 1회
        self.assertNotEqual(before, insights.get_state(self.conn, "data_fp"))
        # 다시 돌리면 또 안 옴(fingerprint 전진됨)
        send2 = self._run()
        send2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
