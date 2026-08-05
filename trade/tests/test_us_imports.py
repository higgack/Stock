import tempfile
import unittest
from pathlib import Path

from trade import us_imports as us


_FULL = (
    "🇺🇸 6월 수입 미국\n\n"
    "▶️ 테스트 품목\n\n"
    "26년06월: $12.3M  (+7.0% YoY)  (+1.5% MoM)\n\n"
    "관련기업: [#Test US](https://www.google.com/search?q=Test+US)\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년05월: $11.9M  (+6.0% YoY)  (+0.8% MoM)"
)


class ParseTests(unittest.TestCase):
    def test_parse_full(self):
        p = us.parse_us_import(_FULL)
        self.assertIsNotNone(p)
        self.assertEqual(p["item"], "테스트 품목")
        self.assertEqual(p["companies"], ["Test US"])
        self.assertEqual(len(p["months"]), 2)
        self.assertEqual(p["months"][0]["month"], "2026-06")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "us.db"
        self.conn = None

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()
        self.tmp.cleanup()

    def test_ingest_and_history(self):
        self.conn = us.open_us_db(self.db_path)
        self.assertTrue(us.ingest(self.conn, _FULL, source_message_id=1, posted_at="t"))
        latest = us.list_us(self.conn)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["month"], "2026-06")
        hist = us.history(self.conn, "테스트 품목")
        self.assertEqual(len(hist), 2)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "us.db"
        self.conn = None

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()
        self.tmp.cleanup()

    def test_render_and_regenerate(self):
        self.conn = us.open_us_db(self.db_path)
        us.ingest(self.conn, _FULL, source_message_id=1, posted_at="t",
                  media_paths=["2026-08-01/abc.jpg"])
        html = us.render_html(self.conn)
        self.assertIn("미국 수입 데이터", html)
        self.assertIn("2026-08-01/abc.jpg", html)
        self.conn.close()
        self.conn = None
        out = Path(self.tmp.name) / "us.html"
        us.regenerate(self.db_path, out)
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
