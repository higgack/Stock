"""Store layer tests: schema creation, upsert idempotency, JSON
round-trip, the latest-per-dedup-key view, and stocks-membership query.

Uses an in-memory SQLite database per test so the suite doesn't touch
~/.trade/store.db.
"""

import unittest
from datetime import datetime, timezone

from trade.parser import parse_caption
from trade.store import (
    alert_to_row,
    count_alerts,
    latest_per_dedup_key,
    open_db,
    query_by_item,
    query_by_stock,
    row_to_dict,
    stats,
    update_media_paths,
    upsert_alert,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_for(caption: str, *, msg_id: int, posted_at: str, media: list[str] | None = None):
    parsed = parse_caption(caption)
    assert parsed is not None, f"caption did not parse: {caption!r}"
    return alert_to_row(
        parsed,
        source_chat_id=-1003715527602,
        source_message_id=msg_id,
        media_group_id=None,
        ingested_at=_now(),
        posted_at=posted_at,
        raw_text=caption,
        media_paths=media or [],
    )


class TestStore(unittest.TestCase):
    def setUp(self):
        self.conn = open_db(":memory:")

    def tearDown(self):
        self.conn.close()

    # --- schema + insert basics ---

    def test_schema_is_created(self):
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
        )
        self.assertIsNotNone(cur.fetchone())

    def test_insert_returns_true_first_time_false_on_repeat(self):
        caption = (
            "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
        )
        row = _row_for(caption, msg_id=42, posted_at="2026-05-11T02:45:00+00:00")

        self.assertTrue(upsert_alert(self.conn, row))
        self.assertFalse(upsert_alert(self.conn, row))  # idempotent
        self.assertEqual(count_alerts(self.conn), 1)

    # --- JSON round-trip ---

    def test_json_columns_decoded_on_read(self):
        caption = (
            "보툴리눔 톡신 (전국_브라질)\n"
            "관련종목 : 휴젤 / 대웅제약 / 파마리서치바이오 (비상장)\n\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
        )
        upsert_alert(
            self.conn,
            _row_for(caption, msg_id=1, posted_at="2026-05-11T02:45:00+00:00"),
        )
        cur = self.conn.execute("SELECT * FROM alerts")
        out = row_to_dict(cur.fetchone())

        self.assertEqual(
            out["stocks"], ["휴젤", "대웅제약", "파마리서치바이오"]
        )
        self.assertEqual(out["stocks_meta"], {"파마리서치바이오": "비상장"})
        self.assertEqual(out["country"], "브라질")
        self.assertFalse(out["is_composite"])
        self.assertEqual(out["parse_warnings"], [])

    # --- latest-per-dedup-key ---

    def test_latest_per_dedup_key_picks_newest_posted_at(self):
        # 잠정 (1-10), 잠정 (1-20), 확정 월 — same (item, country) → one row out
        for msg_id, posted_at, caption in [
            (10, "2026-05-11T02:45:00+00:00",
             "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
             "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."),
            (20, "2026-05-21T02:45:00+00:00",
             "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
             "2026년 5월 1일 ~ 20일 잠정치 수출데이터 입니다."),
            (30, "2026-06-15T02:45:00+00:00",
             "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
             "2026년 5월 확정치 수출데이터 입니다."),
        ]:
            upsert_alert(
                self.conn, _row_for(caption, msg_id=msg_id, posted_at=posted_at)
            )

        latest = latest_per_dedup_key(self.conn)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["source_message_id"], 30)  # the 확정
        self.assertEqual(latest[0]["status"], "final")

    def test_latest_prefers_final_when_posted_at_tied(self):
        # Same posted_at but one final, one preliminary — final wins
        ts = "2026-06-15T02:45:00+00:00"
        cap_preliminary = (
            "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
            "2026년 5월 1일 ~ 31일 잠정치 수출데이터 입니다."
        )
        cap_final = (
            "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
            "2026년 5월 확정치 수출데이터 입니다."
        )
        upsert_alert(self.conn, _row_for(cap_preliminary, msg_id=1, posted_at=ts))
        upsert_alert(self.conn, _row_for(cap_final, msg_id=2, posted_at=ts))

        latest = latest_per_dedup_key(self.conn)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["status"], "final")

    def test_latest_keeps_different_countries_separate(self):
        for msg_id, country in [(1, "중국"), (2, "미국"), (3, "일본")]:
            caption = (
                f"라면 (전국_{country})\n관련종목 : 삼양식품\n\n"
                "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
            )
            upsert_alert(
                self.conn,
                _row_for(caption, msg_id=msg_id, posted_at="2026-05-11T02:45:00+00:00"),
            )
        latest = latest_per_dedup_key(self.conn)
        self.assertEqual(len(latest), 3)
        countries = {r["country"] for r in latest}
        self.assertEqual(countries, {"중국", "미국", "일본"})

    # --- stocks membership (회사별 view) ---

    def test_query_by_stock_returns_all_mentions(self):
        captions = [
            "라면 (전국_중국)\n관련종목 : 삼양식품 / 농심\n\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다.",
            "라면 (전국_미국)\n관련종목 : 삼양식품 / 농심 등\n\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다.",
            "김치 (전국)\n관련종목 : 풀무원 / 대상홀딩스\n\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다.",
        ]
        for i, c in enumerate(captions, 1):
            upsert_alert(
                self.conn,
                _row_for(c, msg_id=i, posted_at="2026-05-11T02:45:00+00:00"),
            )

        samyang = query_by_stock(self.conn, "삼양식품")
        self.assertEqual(len(samyang), 2)
        for r in samyang:
            self.assertIn("삼양식품", r["stocks"])

        nongshim = query_by_stock(self.conn, "농심")
        self.assertEqual(len(nongshim), 2)

        unrelated = query_by_stock(self.conn, "SK하이닉스")
        self.assertEqual(len(unrelated), 0)

    def test_query_by_stock_excludes_item_only_alerts(self):
        # placeholder stocks (item-only) should not match a 회사별 query
        caption = (
            "2차전지 (전국)\n관련종목: 월별 수출 데이터\n\n"
            "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다."
        )
        upsert_alert(
            self.conn,
            _row_for(caption, msg_id=1, posted_at="2026-05-11T02:45:00+00:00"),
        )
        # No stock should match it
        self.assertEqual(query_by_stock(self.conn, "월별 수출 데이터"), [])
        self.assertEqual(query_by_stock(self.conn, "2차전지"), [])

    # --- media path mutation ---

    def test_update_media_paths_replaces_array(self):
        caption = (
            "라면 (전국)\n관련종목 : 삼양식품\n\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
        )
        upsert_alert(
            self.conn,
            _row_for(caption, msg_id=1, posted_at="2026-05-11T02:45:00+00:00",
                     media=["media/2026-05-11/a.jpg"]),
        )
        update_media_paths(
            self.conn, -1003715527602, 1,
            ["media/2026-05-11/a.jpg", "media/2026-05-11/b.jpg"],
        )
        out = row_to_dict(self.conn.execute("SELECT * FROM alerts").fetchone())
        self.assertEqual(
            out["media_paths"],
            ["media/2026-05-11/a.jpg", "media/2026-05-11/b.jpg"],
        )

    # --- stats ---

    def test_stats_returns_buckets(self):
        captions = [
            "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
            "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다.",
            "염화칼륨 (전국)\n관련종목 : 유니드\n\n"
            "2026년 5월 1일 ~ 10일 잠정치 수입데이터 입니다.",
            "라면 (전국)\n관련종목 : 삼양식품\n\n"
            "2026년 4월 확정치 수출데이터 입니다.",
        ]
        for i, c in enumerate(captions, 1):
            upsert_alert(
                self.conn,
                _row_for(c, msg_id=i, posted_at=f"2026-05-1{i}T02:45:00+00:00"),
            )

        s = stats(self.conn)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_direction"], {"export": 2, "import": 1})
        self.assertEqual(s["by_status"], {"preliminary": 2, "final": 1})
        self.assertEqual(s["distinct_items"], 2)  # 라면, 염화칼륨
        self.assertGreaterEqual(s["distinct_dedup_keys"], 2)


if __name__ == "__main__":
    unittest.main()
