"""trade.scripts.fetch_customs — rolling-window math + silent run paths.

The fetch script is deliberately Telegram-silent; these tests pin two
things that matter: (1) the YYYYMM rolling window is computed correctly
across year boundaries, and (2) the no-key / no-pins paths exit cleanly
without touching the network.
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from trade.scripts import fetch_customs


class TestWindow(unittest.TestCase):
    def test_12_month_window_same_year(self):
        now = datetime(2026, 5, 15, tzinfo=timezone.utc)
        start, end = fetch_customs._window(12, now)
        self.assertEqual(end, "202605")
        self.assertEqual(start, "202506")  # 12 months inclusive

    def test_window_crosses_year_boundary(self):
        now = datetime(2026, 2, 10, tzinfo=timezone.utc)
        start, end = fetch_customs._window(12, now)
        self.assertEqual(end, "202602")
        self.assertEqual(start, "202503")

    def test_three_month_window(self):
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        start, end = fetch_customs._window(3, now)
        self.assertEqual(end, "202601")
        self.assertEqual(start, "202511")

    def test_single_month_window(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        start, end = fetch_customs._window(1, now)
        self.assertEqual((start, end), ("202606", "202606"))


class TestSilentExits(unittest.TestCase):
    def test_no_key_exits_zero_without_fetch(self):
        old = os.environ.pop("TRADE_DATA_GO_KR_KEY", None)
        try:
            with mock.patch.object(fetch_customs.customs, "fetch") as m_fetch:
                rc = fetch_customs.run(12)
                self.assertEqual(rc, 0)
                m_fetch.assert_not_called()
        finally:
            if old is not None:
                os.environ["TRADE_DATA_GO_KR_KEY"] = old

    def test_no_pins_exits_zero_without_fetch(self):
        with tempfile.TemporaryDirectory() as td:
            empty_map = Path(td) / "hs_map.tsv"
            empty_map.touch()
            with mock.patch.dict(os.environ, {"TRADE_DATA_GO_KR_KEY": "X"}), \
                 mock.patch.object(fetch_customs.hs_map, "entries", return_value=[]), \
                 mock.patch.object(fetch_customs.customs, "fetch") as m_fetch:
                rc = fetch_customs.run(12)
                self.assertEqual(rc, 0)
                m_fetch.assert_not_called()

    def test_per_item_failure_does_not_abort_others(self):
        # First pin raises, second succeeds → run completes, second cached.
        from trade import customs as _customs
        _XML = (
            '<?xml version="1.0"?><response>'
            '<header><resultCode>00</resultCode><resultMsg>ok</resultMsg></header>'
            '<body><items><item>'
            '<hsCode>1902301010</hsCode><statKor>라면</statKor><year>2026.01</year>'
            '<expDlr>100</expDlr><impDlr>10</impDlr><balPayments>90</balPayments>'
            '<expWgt>5</expWgt><impWgt>1</impWgt>'
            '</item></items></body></response>'
        )
        from contextlib import contextmanager

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "customs.db"

            def fake_fetch(hs, s, e, key=None):
                if hs == "BAD":
                    raise _customs.CustomsAPIError("boom")
                return _customs.parse_response(_XML)

            @contextmanager
            def fake_session():
                # Build the cm from open_db (NOT customs.session, which we're
                # patching) so there's no recursion onto the patched attr.
                conn = _customs.open_db(db)
                try:
                    yield conn
                finally:
                    conn.close()

            with mock.patch.dict(os.environ, {"TRADE_DATA_GO_KR_KEY": "X"}), \
                 mock.patch.object(
                     fetch_customs.hs_map, "entries",
                     return_value=[("실패품목", "BAD"), ("라면", "1902301010")],
                 ), \
                 mock.patch.object(fetch_customs.customs, "fetch", side_effect=fake_fetch), \
                 mock.patch.object(fetch_customs.customs, "session", fake_session):
                rc = fetch_customs.run(12)
                self.assertEqual(rc, 0)
            with _customs.session(db) as conn:
                series = _customs.get_series(conn, "1902301010")
            self.assertEqual(len(series), 1)
            self.assertEqual(series[0]["exp_dlr"], 100)


if __name__ == "__main__":
    unittest.main()
