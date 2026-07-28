"""Watchlist module tests — schema, add/remove idempotency, substring
match for company + item, multi-user isolation."""

import tempfile
import unittest
from pathlib import Path

from trade import watchlist


class TestWatchlist(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "watchlist.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_add_returns_true_first_time_false_on_duplicate(self):
        with watchlist.session(self.path) as conn:
            self.assertTrue(watchlist.add(conn, 100, "item", "라면"))
            self.assertFalse(watchlist.add(conn, 100, "item", "라면"))
            self.assertEqual(len(watchlist.list_for(conn, 100)), 1)

    def test_add_rejects_unknown_kind(self):
        with watchlist.session(self.path) as conn:
            with self.assertRaises(ValueError):
                watchlist.add(conn, 100, "country", "중국")

    def test_remove_returns_true_when_present(self):
        with watchlist.session(self.path) as conn:
            watchlist.add(conn, 100, "item", "라면")
            self.assertTrue(watchlist.remove(conn, 100, "item", "라면"))
            self.assertFalse(watchlist.remove(conn, 100, "item", "라면"))

    def test_remove_all_clears_only_this_user(self):
        # /unwatch all — 본인 워치 전체 해제, 타인 보존, 멱등 (2026-06-12)
        with watchlist.session(self.path) as conn:
            watchlist.add(conn, 100, "item", "라면")
            watchlist.add(conn, 100, "company", "삼양식품")
            watchlist.add(conn, 100, "item", "MR-MUF")
            watchlist.add(conn, 999, "item", "남의것")
            self.assertEqual(watchlist.remove_all(conn, 100), 3)
            self.assertEqual(watchlist.list_for(conn, 100), [])
            self.assertEqual(len(watchlist.list_for(conn, 999)), 1)
            self.assertEqual(watchlist.remove_all(conn, 100), 0)  # 멱등

    def test_list_for_returns_watches_sorted(self):
        with watchlist.session(self.path) as conn:
            watchlist.add(conn, 100, "company", "삼양식품")
            watchlist.add(conn, 100, "item", "김치")
            watchlist.add(conn, 100, "item", "라면")
            out = watchlist.list_for(conn, 100)
            self.assertEqual(
                [(w["kind"], w["pattern"]) for w in out],
                [("company", "삼양식품"), ("item", "김치"), ("item", "라면")],
            )

    def test_matching_users_item_substring(self):
        with watchlist.session(self.path) as conn:
            watchlist.add(conn, 100, "item", "라면")
            watchlist.add(conn, 200, "item", "김치")
            # '라면 + 기타 소스' contains '라면' → user 100 matched
            users = watchlist.matching_users(
                conn, "라면 + 기타 소스 및 조제품", ["삼양식품"]
            )
            self.assertEqual(users, {100})

    def test_matching_users_company_substring(self):
        with watchlist.session(self.path) as conn:
            watchlist.add(conn, 100, "company", "삼양식품")
            watchlist.add(conn, 200, "company", "농심")
            users = watchlist.matching_users(
                conn, "라면", ["삼양식품", "오뚜기"]
            )
            self.assertEqual(users, {100})

    def test_matching_users_returns_multiple_when_multiple_match(self):
        with watchlist.session(self.path) as conn:
            watchlist.add(conn, 100, "item", "라면")
            watchlist.add(conn, 200, "company", "삼양식품")
            users = watchlist.matching_users(
                conn, "라면", ["삼양식품", "농심"]
            )
            self.assertEqual(users, {100, 200})

    def test_matching_users_empty_when_no_overlap(self):
        with watchlist.session(self.path) as conn:
            watchlist.add(conn, 100, "item", "김치")
            users = watchlist.matching_users(
                conn, "라면", ["삼양식품"]
            )
            self.assertEqual(users, set())

    def test_case_insensitive_match(self):
        with watchlist.session(self.path) as conn:
            watchlist.add(conn, 100, "company", "sk하이닉스")
            users = watchlist.matching_users(
                conn, "플래시 메모리", ["SK하이닉스"]
            )
            self.assertEqual(users, {100})


if __name__ == "__main__":
    unittest.main()
