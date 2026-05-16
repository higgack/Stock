"""Dashboard renderer tests.

We seed an in-memory store with a few representative alerts and inspect
the rendered HTML for the structural pieces that downstream features
(filtering, tab switching, image embedding) depend on. We don't try to
execute the inline JS — that would require a browser — but we verify
the JSON payload embedded for the JS to consume.
"""

import json
import re
import unittest
from pathlib import Path

from trade.dashboard import render_html
from trade.parser import parse_caption
from trade.store import alert_to_row, open_db, upsert_alert


SAMPLES = [
    # item_first with country
    (
        100,
        "2026-05-11T02:45:00+00:00",
        "라면 (전국_중국)\n관련종목 : 삼양식품 / 농심\n\n"
        "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다.",
        ["media/2026-05-11/abc.jpg", "media/2026-05-11/def.jpg"],
    ),
    # company_first
    (
        101,
        "2026-05-15T02:45:00+00:00",
        "SK하이닉스 : 플래시 메모리 (충북 청주시)\n\n"
        "2026년 4월 확정치 수출데이터 입니다.",
        ["media/2026-05-15/ghi.jpg"],
    ),
    # item-only (placeholder stocks)
    (
        102,
        "2026-05-11T02:45:00+00:00",
        "2차전지 (전국)\n관련종목: 월별 수출 데이터\n\n"
        "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다.",
        [],
    ),
    # import
    (
        103,
        "2026-05-11T02:45:00+00:00",
        "염화칼륨 (전국)\n관련종목 : 유니드\n\n"
        "2026년 5월 1일 ~ 10일 잠정치 수입데이터 입니다.",
        ["media/2026-05-11/jkl.jpg"],
    ),
]


def _seed_store(tmp_db_path: Path):
    conn = open_db(tmp_db_path)
    for msg_id, posted_at, caption, media in SAMPLES:
        parsed = parse_caption(caption)
        row = alert_to_row(
            parsed,
            source_chat_id=-1003715527602,
            source_message_id=msg_id,
            media_group_id=None,
            ingested_at=posted_at,
            posted_at=posted_at,
            raw_text=caption,
            media_paths=media,
        )
        upsert_alert(conn, row)
    conn.close()


class TestDashboardRenderer(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "store.db"
        _seed_store(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_renders_full_html_document(self):
        html = render_html(self.db_path)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("<html lang=\"ko\">", html)
        self.assertIn("</html>", html)

    def test_header_meta_shows_totals(self):
        html = render_html(self.db_path)
        # 4 alerts, all distinct dedup_keys → 4 latest
        self.assertIn("총 4건", html)
        self.assertIn("(최신 4개)", html)

    def test_tabs_and_views_exist(self):
        html = render_html(self.db_path)
        self.assertIn('data-tab="items"', html)
        self.assertIn('data-tab="companies"', html)
        self.assertIn('id="items-view"', html)
        self.assertIn('id="companies-view"', html)

    def test_filter_controls_exist(self):
        html = render_html(self.db_path)
        self.assertIn('id="q"', html)
        self.assertIn('data-val="export"', html)
        self.assertIn('data-val="import"', html)
        self.assertIn('data-val="preliminary"', html)
        self.assertIn('data-val="final"', html)

    def test_embedded_payload_parses_and_carries_all_alerts(self):
        html = render_html(self.db_path)
        m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
        self.assertIsNotNone(m, "ALERTS payload not found in script")
        payload = json.loads(m.group(1))
        self.assertEqual(len(payload), 4)

        items = {p["item"] for p in payload}
        self.assertIn("라면", items)
        self.assertIn("플래시 메모리", items)
        self.assertIn("2차전지", items)
        self.assertIn("염화칼륨", items)

    def test_media_paths_get_prefix(self):
        html = render_html(self.db_path, media_url_prefix="/static/")
        m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
        payload = json.loads(m.group(1))
        all_media = [p for a in payload for p in a["media"]]
        self.assertTrue(all_media, "expected at least one media path")
        for path in all_media:
            self.assertTrue(
                path.startswith("/static/"),
                f"path should start with media prefix, got {path!r}",
            )

    def test_stocks_visible_in_payload(self):
        html = render_html(self.db_path)
        m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
        payload = json.loads(m.group(1))
        by_item = {p["item"]: p for p in payload}
        self.assertEqual(by_item["라면"]["stocks"], ["삼양식품", "농심"])
        self.assertEqual(by_item["플래시 메모리"]["stocks"], ["SK하이닉스"])
        self.assertEqual(by_item["2차전지"]["stocks"], [])  # placeholder
        self.assertEqual(by_item["염화칼륨"]["stocks"], ["유니드"])

    def test_payload_carries_direction_and_status(self):
        html = render_html(self.db_path)
        m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
        payload = json.loads(m.group(1))
        by_item = {p["item"]: p for p in payload}
        self.assertEqual(by_item["라면"]["dir"], "export")
        self.assertEqual(by_item["라면"]["status"], "preliminary")
        self.assertEqual(by_item["플래시 메모리"]["status"], "final")
        self.assertEqual(by_item["염화칼륨"]["dir"], "import")

    def test_modal_container_present(self):
        html = render_html(self.db_path)
        self.assertIn('id="modal"', html)
        self.assertIn('class="modal-backdrop"', html)
        self.assertIn('class="modal-close"', html)
        self.assertIn('id="modal-body"', html)

    def test_dark_mode_helper_embedded(self):
        html = render_html(self.db_path)
        # applyDarkMode() reads UTC + 9 and toggles body.dark for the
        # 19:00–07:00 KST window. The script must be embedded for the
        # transition to fire without page reloads.
        self.assertIn("applyDarkMode", html)
        self.assertIn("getUTCHours()+9", html)
        self.assertIn("setInterval(applyDarkMode", html)
        # CSS dark-mode variables defined.
        self.assertIn("body.dark", html)

    def test_mini_card_carries_data_id_for_modal_lookup(self):
        # Both items view and companies view emit mini-cards with
        # data-id so the click handler can resolve back to ALERTS[id]
        # without re-embedding the full alert per card.
        html = render_html(self.db_path)
        self.assertIn("renderMiniCard", html)
        self.assertIn("data-id", html)

    def test_nice_period_label_function_embedded(self):
        html = render_html(self.db_path)
        # The modal shows a Korean-language period label rather than
        # the raw ISO dates. niceLabel() needs to be in the page.
        self.assertIn("niceLabel", html)
        self.assertIn("상순", html)
        self.assertIn("중순까지", html)
        self.assertIn("확정", html)

    def test_company_view_smart_search_branch_present(self):
        # When the search query directly matches a company name, the
        # company view should narrow to only matching sections; check
        # the branch is emitted.
        html = render_html(self.db_path)
        self.assertIn("buildCompaniesView", html)
        self.assertIn("direct.length", html)

    def test_payload_carries_dedup_key_and_history_alerts(self):
        # Seed two alerts sharing one dedup_key + one alert with a
        # different dedup_key → payload should carry all 3 (so the
        # modal can find siblings), and LATEST_IDS should mark only
        # the latest one of each dedup_key.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "store.db"
            conn = open_db(db_path)
            cap_a = (
                "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
                "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
            )
            cap_b = (
                "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
                "2026년 4월 확정치 수출데이터 입니다."
            )
            cap_c = (
                "김치 (전국)\n관련종목 : 풀무원\n\n"
                "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
            )
            for msg_id, posted_at, caption in [
                (1, "2026-05-11T02:45:00+00:00", cap_a),
                (2, "2026-05-15T02:45:00+00:00", cap_b),
                (3, "2026-05-11T02:45:00+00:00", cap_c),
            ]:
                parsed = parse_caption(caption)
                upsert_alert(
                    conn,
                    alert_to_row(
                        parsed,
                        source_chat_id=-100,
                        source_message_id=msg_id,
                        media_group_id=None,
                        ingested_at=posted_at,
                        posted_at=posted_at,
                        raw_text=caption,
                        media_paths=[],
                    ),
                )
            conn.close()

            html = render_html(db_path)
            m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
            self.assertIsNotNone(m)
            payload = json.loads(m.group(1))
            self.assertEqual(len(payload), 3)
            # Every alert carries its dedup_key so BY_DEDUP can be
            # rebuilt client-side.
            for a in payload:
                self.assertIn("dedup_key", a)

            # LATEST_IDS contains exactly 2 (one per dedup_key).
            m2 = re.search(r"const LATEST_IDS=new Set\((\[.*?\])\);", html, re.DOTALL)
            self.assertIsNotNone(m2)
            latest_ids = json.loads(m2.group(1))
            self.assertEqual(len(latest_ids), 2)

    def test_modal_renders_primary_and_secondary_card_helpers(self):
        html = render_html(self.db_path)
        # The modal uses a primary + secondary scheme so click-to-swap
        # works inside the modal. Both class names must be in the
        # renderModalCard helper.
        self.assertIn("renderModalCard", html)
        self.assertIn("modal-card primary", html)
        self.assertIn("modal-card secondary", html)
        self.assertIn("BY_DEDUP", html)

    def test_section_subtitle_helper_present(self):
        html = render_html(self.db_path)
        # 품목별 view shows a 관련종목 subtitle line built from the
        # union of variants' stocks; helper must be embedded.
        self.assertIn("unionStocks", html)
        self.assertIn("stocksSubtitle", html)
        self.assertIn("관련종목:", html)

    def test_empty_store_renders_without_crash(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            empty_db = Path(td) / "empty.db"
            open_db(empty_db).close()
            html = render_html(empty_db)
            self.assertIn("<!DOCTYPE html>", html)
            self.assertIn("총 0건", html)


if __name__ == "__main__":
    unittest.main()
