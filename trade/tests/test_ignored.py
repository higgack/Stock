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


class TestPrefixMatching(unittest.TestCase):
    def test_beon_insight_prefix_matches(self):
        self.assertTrue(
            ignored.matches_prefix(
                "[비온 인사이트] 파두 추가 수주! 올해 최소 매출 1,900억 확보"
            )
        )
        self.assertTrue(
            ignored.matches_prefix(
                "[비온 인사이트] 파두 수주잔고 2,624억 원 기록"
            )
        )

    def test_legit_export_alert_does_not_match(self):
        # Real export/import alerts have a totally different shape —
        # never start with a bracketed promo tag.
        self.assertFalse(
            ignored.matches_prefix(
                "4월 수출입 동향 — 반도체 +35%, 자동차 +12%"
            )
        )
        self.assertFalse(
            ignored.matches_prefix("📊 5월 1-20일 잠정")
        )

    def test_leading_whitespace_does_not_bypass(self):
        # Stray newline / space before the tag must still match.
        self.assertTrue(ignored.matches_prefix("   [비온 인사이트] 종목분석"))
        self.assertTrue(ignored.matches_prefix("\n[비온 인사이트] 종목분석"))

    def test_empty_and_none_return_false(self):
        self.assertFalse(ignored.matches_prefix(""))
        self.assertFalse(ignored.matches_prefix(None))

    def test_prefix_in_middle_does_not_match(self):
        # Only a true prefix counts — body mentions don't.
        self.assertFalse(
            ignored.matches_prefix(
                "오늘의 수출입 동향 — 참고: [비온 인사이트] 글도 함께 발행"
            )
        )

    def test_prefixes_constant_is_tuple(self):
        # Guard against accidental mutation to a list.
        self.assertIsInstance(ignored.IGNORED_PREFIXES, tuple)
        self.assertIn("[비온 인사이트]", ignored.IGNORED_PREFIXES)


if __name__ == "__main__":
    unittest.main()
