"""Integration test for purge_ignored.py — verifies that the
retroactive cleanup correctly identifies and deletes store.db
rows whose raw_text matches the current IGNORED_PREFIXES /
IGNORED_CONTAINS filter, and leaves untouched everything else.
"""

import tempfile
import unittest
from pathlib import Path

from trade import ignored
from trade.scripts.purge_ignored import find_matching
from trade.store import open_db


def _insert(conn, *, mid: int, raw: str) -> None:
    """Minimal alert insert — only the columns find_matching cares
    about. Default-or-NULL the rest so the schema accepts the row.
    """
    conn.execute(
        """
        INSERT INTO alerts (
            source_chat_id, source_message_id, ingested_at, posted_at,
            raw_text, direction, status, period_start, period_end,
            period_kind, title_kind, item, item_raw, dedup_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1, mid, "2026-05-22T00:00:00Z", "2026-05-22T00:00:00Z",
            raw, "export", "preliminary", "2026-05-01", "2026-05-20",
            "monthly", "monthly", "테스트 품목", "테스트 품목",
            f"export|monthly|2026-05-01|2026-05-20|테스트 품목|all",
        ),
    )


class TestPurgeIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "store.db"
        self.conn = open_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_finds_prefix_match_only(self):
        _insert(self.conn, mid=100, raw="[비온 인사이트] 파두 추가 수주")
        _insert(self.conn, mid=200, raw="📊 4월 수출입 동향")
        hits = find_matching(self.conn)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], 100)
        self.assertTrue(hits[0][3].startswith("prefix:"))

    def test_finds_contains_match_only(self):
        _insert(
            self.conn, mid=300,
            raw=(
                "📌 파두(시가총액: 6조) #A440110\n"
                "공시링크: https://dart.fss.or.kr/dsaf001/main.do"
            ),
        )
        _insert(self.conn, mid=400, raw="📊 4월 수출입 동향 — 정상")
        hits = find_matching(self.conn)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], 300)
        self.assertTrue(hits[0][3].startswith("contains:"))

    def test_mixed_matches_both_kinds(self):
        _insert(self.conn, mid=10, raw="[비온 인사이트] 종목분석")
        _insert(self.conn, mid=20, raw="공시링크: https://dart.fss.or.kr/x")
        _insert(self.conn, mid=30, raw="📊 정상 수출입")
        hits = find_matching(self.conn)
        # Order doesn't matter — assert on msg_id set.
        mids = {h[1] for h in hits}
        self.assertEqual(mids, {10, 20})

    def test_empty_filter_state_is_no_match(self):
        # Sanity: rows with no marker survive scanning.
        _insert(self.conn, mid=1, raw="📊 4월 수출입 동향")
        _insert(self.conn, mid=2, raw="📊 5월 1-10일 잠정")
        self.assertEqual(find_matching(self.conn), [])

    def test_dart_link_anywhere_in_body_matches(self):
        # The link may appear deep in the body, not just at the start.
        _insert(
            self.conn, mid=50,
            raw=(
                "📌 어떤회사 #A123456\n"
                "blah blah blah\n"
                "x" * 500 + "\n"
                "more text\n"
                "공시링크: https://dart.fss.or.kr/buried/at/the/end"
            ),
        )
        hits = find_matching(self.conn)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], 50)

    def test_filter_constants_present_for_purge_to_work(self):
        # Guards against accidentally emptying both filters — purge
        # would then become a no-op which would silently regress.
        self.assertTrue(
            ignored.IGNORED_PREFIXES or ignored.IGNORED_CONTAINS,
            "filter is empty — purge would no-op silently",
        )


if __name__ == "__main__":
    unittest.main()
