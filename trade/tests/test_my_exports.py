"""말레이시아 수출 데이터(나쁜양파) 파서 + 저장 회귀 (사용자 2026-07-26).

tw_exports/cn_exports/jp2_exports 와 완전히 동일한 채널·포맷(마커 단어만
"말레이시아")이므로 테스트 구조도 그대로 미러링. 회사 링크 포맷(마크다운 링크 vs
평문 해시태그)이 불확실해 양쪽 다 커버(jp2_exports 테스트와 동일 이유).
1차 픽스처는 사용자 2026-07-26 스크린샷(t.me/Badonions 채널) 그대로."""
import tempfile
import unittest
from pathlib import Path

from trade import my_exports as my


# 실측 스크린샷 원문(사용자 2026-07-26) — 회사 평문 해시태그 + 히스토리 2개월.
_REAL_FULL = (
    "🇲🇾 05월 수출 말레이시아\n\n"
    "▶️ 액화천연가스\n\n"
    "26년05월: $1,273.7M  (+127.0% YoY)  (+5.6% MoM)\n\n"
    "관련기업: #LNG\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년04월: $1,206.3M  (+13.9% YoY)  (8.3% MoM)\n"
    "26년03월: $1,114.4M  (-9.6% YoY)  (+25.2% MoM)"
)

# 마크다운 링크 회사(대만/중국 스타일) — 이 채널도 링크 포맷을 섞어 쓸 수 있어
# 두 경로 다 커버.
_MARKDOWN_LINK_CO = (
    "**🇲🇾 05월 수출 말레이시아**\n\n**▶️ 테스트 품목**\n\n"
    "**26년05월: $50.0M  (+10.0% YoY)  (+2.0% MoM)**\n\n"
    "관련기업: [#TestCo A](https://www.google.com/search?q=TestCo+A+Stock)  "
    "[#TestCo B](https://www.google.com/search?q=TestCo+B+Stock)\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년04월: $45.0M  (+8.0% YoY)  (+1.0% MoM)"
)

# 회사 여러 개(평문 해시태그 다중) — 구분 확인용
_MULTI_PLAIN_CO = (
    "🇲🇾 05월 수출 말레이시아\n\n▶️ 테스트 품목2\n\n"
    "26년05월: $30.0M  (+5.0% YoY)  (+1.0% MoM)\n\n"
    "관련기업: #Co One #Co Two #Co Three\n\n"
    "최근 추이 (단위: USD M$)\n26년04월: $29.0M  (+3.0% YoY)  (-1.0% MoM)"
)

# 회사 0개 — graceful
_NO_COMPANY = "🇲🇾 05월 수출 말레이시아\n\n▶️ 어떤 품목\n\n26년05월: $10.0M  (+5.0% YoY)  (-2.0% MoM)"

_JP1_CAPTION = (
    "📈 일본 수출 데이터 업데이트: 다이싱/어셈블리 (DISCO)\n"
    "📅 최신 월: 2026-05\n"
    "💰 수출액: 27.6십억 엔\n"
)

_KR_CAPTION = "[관세청] 반도체 6월 수출 123억불 전년비 +15%"

_TW_CAPTION = "**🇹🇼 6월 수출 대만**\n\n**▶️ 품목**\n\n**26년06월: $10.0M  (+5.0% YoY)  (-2.0% MoM)**"

_CN_CAPTION = "**🇨🇳 4월 수출 중국**\n\n**▶️ 품목**\n\n**26년04월: $10.0M  (+5.0% YoY)  (-2.0% MoM)**"

_JP2_CAPTION = "🇯🇵 5월 수출 일본\n\n▶️ 품목\n\n26년05월: $10.0M  (+5.0% YoY)  (-2.0% MoM)"

_PH_CAPTION = (
    "🇵🇭 5월 수출 필리핀\n\n▶️ 품목\n\n"
    "26년05월: $10.0M  (+5.0% YoY)  (-2.0% MoM)"
)


class ParseTests(unittest.TestCase):
    def test_full_message_headline_and_history_plain_hashtag(self):
        p = my.parse_my_export(_REAL_FULL)
        self.assertIsNotNone(p)
        self.assertEqual(p["item"], "액화천연가스")
        self.assertEqual(p["companies"], ["LNG"])
        self.assertEqual(len(p["months"]), 3)  # 헤드라인 1 + 히스토리 2
        self.assertEqual(p["months"][0], {
            "month": "2026-05", "export_value_musd": 1273.7,
            "export_yoy": 127.0, "export_mom": 5.6,
        })
        self.assertEqual(p["months"][-1]["month"], "2026-03")

    def test_markdown_link_companies(self):
        p = my.parse_my_export(_MARKDOWN_LINK_CO)
        self.assertEqual(p["companies"], ["TestCo A", "TestCo B"])
        self.assertEqual(len(p["months"]), 2)

    def test_multi_plain_hashtag_companies_split_correctly(self):
        p = my.parse_my_export(_MULTI_PLAIN_CO)
        self.assertEqual(p["companies"], ["Co One", "Co Two", "Co Three"])

    def test_no_company_line_graceful(self):
        p = my.parse_my_export(_NO_COMPANY)
        self.assertIsNotNone(p)
        self.assertEqual(p["companies"], [])
        self.assertEqual(len(p["months"]), 1)

    def test_other_source_captions_return_none(self):
        # 7차 폴백이므로 KR/JP(BeOn)/TW/CN/JP2/TH 포맷은 절대 MY 로 오인되면 안 됨.
        self.assertIsNone(my.parse_my_export(_JP1_CAPTION))
        self.assertIsNone(my.parse_my_export(_KR_CAPTION))
        self.assertIsNone(my.parse_my_export(_TW_CAPTION))
        self.assertIsNone(my.parse_my_export(_CN_CAPTION))
        self.assertIsNone(my.parse_my_export(_JP2_CAPTION))
        self.assertIsNone(my.parse_my_export(_PH_CAPTION))
        self.assertIsNone(my.parse_my_export(""))
        self.assertIsNone(my.parse_my_export("그냥 잡담 메시지"))

    def test_no_month_line_returns_none(self):
        self.assertIsNone(my.parse_my_export("🇲🇾 05월 수출 말레이시아\n\n▶️ 품목"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "my.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_ingest_stores_all_months_and_latest_snapshot(self):
        conn = my.open_my_db(self.db_path)
        ok = my.ingest(conn, _REAL_FULL, source_message_id=1,
                        posted_at="2026-07-26T07:03:00Z",
                        media_paths=["2026-07-26/abc.jpg"])
        self.assertTrue(ok)
        latest = my.list_my(conn)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["month"], "2026-05")
        self.assertEqual(latest[0]["export_value_musd"], 1273.7)
        self.assertIn("LNG", latest[0]["companies"])
        hist = my.history(conn, "액화천연가스")
        self.assertEqual(len(hist), 3)
        self.assertEqual(hist[0]["month"], "2026-03")   # 오름차순
        self.assertEqual(hist[-1]["month"], "2026-05")

    def test_reingest_preserves_media_when_new_media_absent(self):
        conn = my.open_my_db(self.db_path)
        my.ingest(conn, _REAL_FULL, source_message_id=1,
                  posted_at="t", media_paths=["chart.jpg"])
        my.ingest(conn, _REAL_FULL, source_message_id=1,
                  posted_at="t", media_paths=None)
        latest = my.list_my(conn)
        self.assertEqual(latest[0]["chart_media"], "chart.jpg")

    def test_other_source_caption_not_ingested(self):
        conn = my.open_my_db(self.db_path)
        self.assertFalse(my.ingest(conn, _JP1_CAPTION))
        self.assertFalse(my.ingest(conn, _TW_CAPTION))
        self.assertFalse(my.ingest(conn, _CN_CAPTION))
        self.assertFalse(my.ingest(conn, _JP2_CAPTION))
        self.assertFalse(my.ingest(conn, _PH_CAPTION))
        self.assertEqual(my.list_my(conn), [])

    def test_multiple_items_independent(self):
        conn = my.open_my_db(self.db_path)
        my.ingest(conn, _REAL_FULL, source_message_id=1, posted_at="t")
        my.ingest(conn, _MARKDOWN_LINK_CO, source_message_id=2, posted_at="t")
        latest = my.list_my(conn)
        self.assertEqual(len(latest), 2)
        items = {r["item"] for r in latest}
        self.assertIn("테스트 품목", items)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "my.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_html_with_data(self):
        conn = my.open_my_db(self.db_path)
        my.ingest(conn, _REAL_FULL, source_message_id=1, posted_at="t",
                  media_paths=["2026-07-26/abc.jpg"])
        html = my.render_html(conn)
        self.assertIn("말레이시아 수출 데이터", html)
        self.assertIn("1,273.7", html)
        self.assertIn("LNG", html)
        self.assertIn("my-card", html)
        self.assertIn("2026-07-26/abc.jpg", html)

    def test_render_html_empty_graceful(self):
        conn = my.open_my_db(self.db_path)
        html = my.render_html(conn)
        self.assertIn("아직 수집된 말레이시아 수출 데이터(나쁜양파)가 없습니다", html)
        self.assertNotIn('<details class="my-card"', html)

    def test_regenerate_writes_file(self):
        out = Path(self.tmp.name) / "my.html"
        my.regenerate(self.db_path, out)
        self.assertTrue(out.exists())
        self.assertIn("말레이시아 수출 데이터", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
