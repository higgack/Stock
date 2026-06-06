"""trade.scripts.fetch_quotes — 관련종목 시세 캐시 워머 테스트."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trade.scripts import fetch_quotes as fq


class WarmerTests(unittest.TestCase):
    def setUp(self):
        # 라운드로빈 커서 파일을 tmp로 격리(테스트 간 + 실제 ~/.trade 오염 방지).
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"TRADE_DATA_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

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

    def test_round_robin_covers_tail_across_ticks(self):
        # 예산이 빡빡해 한 틱에 다 못 돌아도, 커서가 이어져 저빈도 꼬리까지 커버.
        # budget=-1 → 청크 후 검사라 매 틱 정확히 1청크(20개)만 진행(최소 진행 보장).
        alerts = [{"stocks": [f"S{i:02d}"]} for i in range(30)]   # 30종목 각 freq1
        ticks: list[list[str]] = []

        def fake_gq(names, **kw):
            ticks.append(list(names))
            return {nm: object() for nm in names}     # warmed>0 (블랙아웃 회피)

        with mock.patch.dict(os.environ, {"TRADE_QUOTES_BUDGET_S": "-1"}), \
             mock.patch.object(fq.pp, "provider_active", return_value=True), \
             mock.patch.object(fq, "open_db", return_value=mock.MagicMock()), \
             mock.patch.object(fq, "list_all_alerts", return_value=alerts), \
             mock.patch.object(fq.pp, "get_quotes_by_name", side_effect=fake_gq), \
             mock.patch.object(fq, "_update_blackout_state"), \
             mock.patch.object(fq, "_store_db",
                               return_value=mock.Mock(exists=lambda: True)):
            fq.run()                                   # tick1
            flat1 = [nm for c in ticks for nm in c]
            ticks.clear()
            fq.run()                                   # tick2 — 커서 이어감
            flat2 = [nm for c in ticks for nm in c]

        self.assertEqual(len(flat1), 20)               # 1청크만
        self.assertEqual(flat1[0], "S00")              # 커서0(빈도순)부터
        self.assertEqual(flat2[0], "S20")              # tick1이 멈춘 커서20에서 이어감
        # tick1이 놓친 꼬리(S20~S29)가 tick2에서 채워짐
        self.assertTrue({"S20", "S25", "S29"}.issubset(set(flat2)))


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
