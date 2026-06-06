"""trade.scripts.resolve_check — 미매칭 신규 관련종목 일일 점검 테스트."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trade import price_provider as pp
from trade.scripts import resolve_check as rc


class ResolveCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "TRADE_DATA_DIR": self.tmp.name, "TRADE_DATA_GO_KR_KEY": "DK"})
        self.env.start()
        self._db = mock.patch.object(rc, "_store_db",
                                     return_value=mock.Mock(exists=lambda: True))
        self._db.start()
        self._open = mock.patch.object(rc, "open_db", return_value=mock.MagicMock())
        self._open.start()

    def tearDown(self):
        self._open.stop()
        self._db.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _run(self, alerts, resolved, **kw):
        with mock.patch.object(rc, "list_all_alerts", return_value=alerts), \
             mock.patch.object(pp, "resolve_codes", return_value=resolved), \
             mock.patch.object(rc, "_notify") as notify:
            self.assertEqual(rc.run(**kw), 0)
        return notify

    def _state(self):
        p = Path(self.tmp.name) / "resolve_check.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def test_first_run_baseline_silent(self):
        # 첫 실행: 현재 미매칭을 seen으로 기록만, 알림 0(수십 건 flood 방지).
        notify = self._run([{"stocks": ["삼성전자", "없는회사"]}], {"삼성전자": "005930"})
        notify.assert_not_called()
        self.assertEqual(set(self._state()["seen"]), {"없는회사"})

    def test_alerts_only_new_unresolved(self):
        self._run([{"stocks": ["A주", "B주"]}], {"A주": "1"})        # baseline seen={B주}
        notify = self._run([{"stocks": ["A주", "B주", "C주"]}], {"A주": "1"})
        notify.assert_called_once()
        msg = notify.call_args.args[0]
        self.assertIn("C주", msg)
        self.assertNotIn("B주", msg)                                 # 이미 seen → 재알림 X
        self.assertEqual(set(self._state()["seen"]), {"B주", "C주"})

    def test_silent_when_no_new(self):
        self._run([{"stocks": ["B주"]}], {})                          # baseline seen={B주}
        notify = self._run([{"stocks": ["B주"]}], {})
        notify.assert_not_called()

    def test_resolved_away_drops_from_seen(self):
        self._run([{"stocks": ["B주"]}], {})                          # seen={B주}
        notify = self._run([{"stocks": ["B주"]}], {"B주": "9"})        # 이제 해석됨
        notify.assert_not_called()
        self.assertEqual(self._state()["seen"], [])                   # seen에서 빠짐

    def test_if_due_skips_same_day(self):
        self._run([{"stocks": ["B주"]}], {})                          # day 기록
        with mock.patch.object(rc, "list_all_alerts") as la, \
             mock.patch.object(pp, "resolve_codes") as rcodes, \
             mock.patch.object(rc, "_notify"):
            self.assertEqual(rc.run(if_due=True), 0)                  # 같은 날 → skip
        la.assert_not_called()
        rcodes.assert_not_called()

    def test_cap_limits_message(self):
        many = [f"종목{i:02d}" for i in range(30)]
        self._run([{"stocks": ["기준종목"]}], {})                     # baseline seen={기준종목}
        notify = self._run([{"stocks": many + ["기준종목"]}], {})      # 30개 신규 미매칭
        msg = notify.call_args.args[0]
        self.assertIn("30개", msg)                                    # 총계는 30
        self.assertIn("외 ", msg)                                     # cap 초과분 표기

    def test_skip_when_no_resolution_means(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(pp, "_KRX_MASTER", Path(self.tmp.name) / "none.json"), \
             mock.patch.object(rc, "list_all_alerts") as la:
            self.assertEqual(rc.run(), 0)
        la.assert_not_called()

    def test_audit_classifies_no_code_vs_no_quote(self):
        # --audit은 렌더 함수(_stock_quotes_for)로 실제 표시여부 판정 후 분류:
        #   시세 박힌 것=빈칸 아님 / 코드 없음(①) / 코드 있는데 시세 없음(●).
        alerts = [{"stocks": ["삼성전자", "코드없음회사", "코드있고시세없음"]}]
        with mock.patch.object(rc, "list_all_alerts", return_value=alerts), \
             mock.patch.object(pp, "resolve_codes",
                               return_value={"삼성전자": "005930",
                                             "코드있고시세없음": "111111"}), \
             mock.patch("trade.dashboard._stock_quotes_for",
                        return_value={"삼성전자": {"p": 70000, "c": 1.0}}), \
             mock.patch("builtins.print") as pr:
            self.assertEqual(rc.audit(), 0)
        out = "\n".join(str(c.args[0]) for c in pr.call_args_list if c.args)
        self.assertIn("코드없음회사", out)          # ■ 코드 없음
        self.assertIn("코드있고시세없음", out)        # ● 코드 있으나 시세 없음
        self.assertIn("[111111]", out)               # ● 섹션은 코드 표기
        self.assertNotIn("삼성전자", out)            # 시세 박힘 → 빈칸 목록에 없음


if __name__ == "__main__":
    unittest.main()
