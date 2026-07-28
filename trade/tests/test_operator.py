"""trade.operator — operator chat-id capture tests."""

import os
import tempfile
import unittest
from pathlib import Path

from trade import operator


class TestOperator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "operator_chat_id"
        self._env = os.environ.pop("TRADE_OPERATOR_CHAT_ID", None)

    def tearDown(self):
        self.tmp.cleanup()
        if self._env is not None:
            os.environ["TRADE_OPERATOR_CHAT_ID"] = self._env

    def test_remember_then_get(self):
        operator.remember(12345, self.path)
        self.assertEqual(operator.get(self.path), 12345)

    def test_get_none_when_unset(self):
        self.assertIsNone(operator.get(self.path))

    def test_remember_idempotent_on_same_id(self):
        operator.remember(1, self.path)
        mtime1 = self.path.stat().st_mtime_ns
        operator.remember(1, self.path)  # no change → no rewrite
        self.assertEqual(self.path.stat().st_mtime_ns, mtime1)

    def test_remember_updates_on_change(self):
        operator.remember(1, self.path)
        operator.remember(2, self.path)
        self.assertEqual(operator.get(self.path), 2)

    def test_remember_ignores_falsy(self):
        operator.remember(None, self.path)
        operator.remember(0, self.path)
        self.assertFalse(self.path.exists())

    def test_env_overrides_file(self):
        operator.remember(111, self.path)
        os.environ["TRADE_OPERATOR_CHAT_ID"] = "999"
        self.assertEqual(operator.get(self.path), 999)

    def test_corrupt_file_returns_none(self):
        self.path.write_text("not-an-int", encoding="utf-8")
        self.assertIsNone(operator.get(self.path))


if __name__ == "__main__":
    unittest.main()
