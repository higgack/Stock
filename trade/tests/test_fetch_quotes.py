"""trade.scripts.fetch_quotes — 관련종목 시세 캐시 워머 테스트."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trade.scripts import fetch_quotes as fq


class WarmerTests(unittest.TestCase):
    def test_provider_off_skips(self):
        with mock.patch.object(fq.pp, "provider_active", return_value=False):
            self.assertEqual(fq.run(), 0)

    def test_frequency_ordered_warming(self):
        # 고빈도 종목(리노공업×3)이 희귀종목보다 먼저 처리되는지.
        alerts = [
            {"stocks": ["리노공업"]}, {"stocks": ["리노공업"]},
            {"stocks": ["리노공업"]}, {"stocks": ["희귀종목엔지니어링"]},
        ]
        processed = []

        def fake_gq(names, **kw):
            processed.extend(names)
            self.assertFalse(kw.get("fetch") is False)   # 워머는 fetch=True
            return {}

        with mock.patch.object(fq.pp, "provider_active", return_value=True), \
             mock.patch.object(fq, "open_db", return_value=mock.MagicMock()), \
             mock.patch.object(fq, "list_all_alerts", return_value=alerts), \
             mock.patch.object(fq.pp, "get_quotes_by_name", side_effect=fake_gq), \
             mock.patch.object(fq, "_update_blackout_state"), \
             mock.patch.object(fq, "_store_db",
                               return_value=mock.Mock(exists=lambda: True)):
            self.assertEqual(fq.run(), 0)

        self.assertEqual(processed[0], "리노공업")          # 고빈도 먼저
        self.assertIn("희귀종목엔지니어링", processed)

    def test_no_store_skips(self):
        with mock.patch.object(fq.pp, "provider_active", return_value=True), \
             mock.patch.object(fq, "_store_db",
                               return_value=mock.Mock(exists=lambda: False)):
            self.assertEqual(fq.run(), 0)


class BlackoutAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"TRADE_DATA_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_enter_blackout_notifies_and_marks(self):
        with mock.patch.object(fq, "_notify") as n:
            fq._update_blackout_state(True, total=450)
        n.assert_called_once()
        self.assertIn("⚠️", n.call_args.args[0])
        self.assertTrue(fq._marker().exists())

    def test_still_blackout_no_duplicate_alert(self):
        fq._marker().parent.mkdir(parents=True, exist_ok=True)
        fq._marker().touch()
        with mock.patch.object(fq, "_notify") as n:
            fq._update_blackout_state(True, total=450)
        n.assert_not_called()                          # 이미 블랙아웃 → 재알림 X

    def test_recover_notifies_and_clears_marker(self):
        fq._marker().parent.mkdir(parents=True, exist_ok=True)
        fq._marker().touch()
        with mock.patch.object(fq, "_notify") as n:
            fq._update_blackout_state(False, total=450)
        n.assert_called_once()
        self.assertIn("✅", n.call_args.args[0])
        self.assertFalse(fq._marker().exists())

    def test_healthy_stays_silent(self):
        with mock.patch.object(fq, "_notify") as n:
            fq._update_blackout_state(False, total=450)
        n.assert_not_called()
        self.assertFalse(fq._marker().exists())

    def test_no_operator_or_token_silent_skip(self):
        # 운영자 chat_id/토큰 없으면 전송 안 함(크레드 없어도 배포·워밍 안 깨짐).
        with mock.patch.dict(os.environ, {"TRADE_BOT_TOKEN": ""}, clear=False), \
             mock.patch("trade.operator.get", return_value=None), \
             mock.patch.object(fq.subprocess, "run") as run:
            fq._notify("hi")
        run.assert_not_called()

    def test_run_warns_and_marks_on_total_blackout(self):
        alerts = [{"stocks": ["삼성전자"]}]
        with mock.patch.object(fq.pp, "provider_active", return_value=True), \
             mock.patch.object(fq, "open_db", return_value=mock.MagicMock()), \
             mock.patch.object(fq, "list_all_alerts", return_value=alerts), \
             mock.patch.object(fq.pp, "get_quotes_by_name", return_value={}), \
             mock.patch.object(fq, "_notify"), \
             mock.patch.object(fq, "_store_db",
                               return_value=mock.Mock(exists=lambda: True)):
            with self.assertLogs(fq.log, level="WARNING") as cm:
                self.assertEqual(fq.run(), 0)
        self.assertTrue(any("warmed 0" in m for m in cm.output))
        self.assertTrue(fq._marker().exists())         # 블랙아웃 진입 마킹


if __name__ == "__main__":
    unittest.main()
