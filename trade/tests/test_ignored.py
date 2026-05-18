"""trade.ignored — operator-curated msg_id skip list.

Tests cover idempotent add/remove, malformed-line tolerance,
comment / blank-line handling, and round-trip persistence."""

import tempfile
import unittest
from pathlib import Path

from trade import ignored


class TestIgnored(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "ignored.txt"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_empty_returns_empty_set(self):
        self.assertEqual(ignored.load(self.path), set())

    def test_add_returns_true_first_time_false_on_dup(self):
        self.assertTrue(ignored.add(677, self.path))
        self.assertFalse(ignored.add(677, self.path))
        self.assertEqual(ignored.load(self.path), {677})

    def test_add_appends_distinct_ids(self):
        for mid in (100, 200, 300):
            ignored.add(mid, self.path)
        self.assertEqual(ignored.load(self.path), {100, 200, 300})

    def test_remove_returns_true_when_present(self):
        ignored.add(677, self.path)
        self.assertTrue(ignored.remove(677, self.path))
        self.assertFalse(ignored.remove(677, self.path))
        self.assertEqual(ignored.load(self.path), set())

    def test_remove_preserves_other_ids(self):
        for mid in (100, 200, 300):
            ignored.add(mid, self.path)
        ignored.remove(200, self.path)
        self.assertEqual(ignored.load(self.path), {100, 300})

    def test_load_skips_comments_and_blanks(self):
        self.path.write_text(
            "# operator note\n"
            "100\n"
            "\n"
            "200\n"
            "# another comment\n"
            "300\n",
            encoding="utf-8",
        )
        self.assertEqual(ignored.load(self.path), {100, 200, 300})

    def test_load_skips_malformed_lines(self):
        self.path.write_text(
            "100\nabc\n200\nx 300\n",
            encoding="utf-8",
        )
        # 'abc' and 'x 300' don't parse; the integer lines survive.
        self.assertEqual(ignored.load(self.path), {100, 200})

    def test_remove_writes_sorted(self):
        for mid in (300, 100, 200):
            ignored.add(mid, self.path)
        ignored.remove(100, self.path)
        body = self.path.read_text(encoding="utf-8")
        # Remaining 200, 300 written in sorted order.
        self.assertEqual(body, "200\n300\n")


if __name__ == "__main__":
    unittest.main()
