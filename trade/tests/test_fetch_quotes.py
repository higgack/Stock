"""trade.scripts.fetch_quotes — 관련종목 시세 캐시 워머 테스트."""

import unittest
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


if __name__ == "__main__":
    unittest.main()
