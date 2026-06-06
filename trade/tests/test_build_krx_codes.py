"""trade.scripts.build_krx_codes — KRX 이름→코드 로컬 마스터 빌더 테스트."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trade import price_provider as pp
from trade.scripts import build_krx_codes as bk


class BuildKrxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.master = Path(self.tmp.name) / "krx_codes.json"
        self._p = mock.patch.object(pp, "_KRX_MASTER", self.master)
        self._p.start()
        self.env = mock.patch.dict(os.environ, {"TRADE_DATA_GO_KR_KEY": "DK"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._p.stop()
        self.tmp.cleanup()

    def test_builds_and_writes_atomically(self):
        with mock.patch.object(pp, "fetch_krx_master",
                               return_value={"삼성전자": "005930"}):
            self.assertEqual(bk.run(), 0)
        self.assertEqual(json.loads(self.master.read_text())["삼성전자"], "005930")

    def test_no_key_skips(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(pp, "fetch_krx_master") as f:
            self.assertEqual(bk.run(), 0)
        f.assert_not_called()
        self.assertFalse(self.master.exists())

    def test_empty_fetch_keeps_existing_master(self):
        self.master.write_text(json.dumps({"old": "005930"}))
        with mock.patch.object(pp, "fetch_krx_master", return_value={}):
            self.assertEqual(bk.run(), 0)
        self.assertEqual(json.loads(self.master.read_text()), {"old": "005930"})

    def test_if_stale_skips_when_fresh(self):
        self.master.write_text(json.dumps({"x": "005930"}))   # 방금 생성 = 신선
        with mock.patch.object(pp, "fetch_krx_master") as f:
            self.assertEqual(bk.run(if_stale=True), 0)
        f.assert_not_called()                                 # 신선 → 재빌드 안 함

    def test_if_stale_builds_when_missing(self):
        with mock.patch.object(pp, "fetch_krx_master",
                               return_value={"삼성전자": "005930"}) as f:
            self.assertEqual(bk.run(if_stale=True), 0)
        f.assert_called_once()                                # 파일 없음 → 빌드
        self.assertTrue(self.master.exists())


if __name__ == "__main__":
    unittest.main()
