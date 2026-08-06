"""멕시코 수출 데이터(나쁜양파) 파서 + 저장 회귀 (사용자 2026-07-26).

tw_exports/cn_exports/jp2_exports 와 완전히 동일한 채널·포맷(마커 단어만
"멕시코")이므로 테스트 구조도 그대로 미러링. 회사 링크 포맷(마크다운 링크 vs
평문 해시태그)이 불확실해 양쪽 다 커버(jp2_exports 테스트와 동일 이유).
1차 픽스처는 사용자 2026-07-26 스크린샷(t.me/Badonions 채널) 그대로."""
import tempfile
import unittest
from pathlib import Path

from trade import mx_exports as mx


# 실측 스크린샷 원문(사용자 2026-07-26) — 회사 평문 해시태그 + 히스토리 2개월.
_REAL_FULL = (
    "🇲🇽 04월 수출 멕시코\n\n"
    "▶️ 전기회로 개폐·보호·접속용 기타 기기\n\n"
    "26년04월: $499.4M  (+19.9% YoY)  (+1.0% MoM)\n\n"
    "관련기업: #APTV\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년03월: $494.5M  (+26.0% YoY)  (21.4% MoM)\n"
    "26년02월: $407.5M  (+7.2% YoY)  (+4.6% MoM)"
)

# 마크다운 링크 회사(대만/중국 스타일) — 이 채널도 링크 포맷을 섞어 쓸 수 있어
# 두 경로 다 커버.
_MARKDOWN_LINK_CO = (
    "**🇲🇽 04월 수출 멕시코**\n\n**▶️ 테스트 품목**\n\n"
    "**26년04월: $50.0M  (+10.0% YoY)  (+2.0% MoM)**\n\n"
    "관련기업: [#TestCo A](https://www.google.com/search?q=TestCo+A+Stock)  "
    "[#TestCo B](https://www.google.com/search?q=TestCo+B+Stock)\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년03월: $45.0M  (+8.0% YoY)  (+1.0% MoM)"
)

# 회사 여러 개(평문 해시태그 다중) — 구분 확인용
_MULTI_PLAIN_CO = (
    "🇲🇽 04월 수출 멕시코\n\n▶️ 테스트 품목2\n\n"
    "26년04월: $30.0M  (+5.0% YoY)  (+1.0% MoM)\n\n"
    "관련기업: #Co One #Co Two #Co Three\n\n"
    "최근 추이 (단위: USD M$)\n26년03월: $29.0M  (+3.0% YoY)  (-1.0% MoM)"
)

# 회사 0개 — graceful
_NO_COMPANY = "🇲🇽 04월 수출 멕시코\n\n▶️ 어떤 품목\n\n26년04월: $10.0M  (+5.0% YoY)  (-2.0% MoM)"

_JP1_CAPTION = (
    "📈 일본 수출 데이터 업데이트: 다이싱/어셈블리 (DISCO)\n"
    "📅 최신 월: 2026-05\n"
    "💰 수출액: 27.6십억 엔\n"
)

_KR_CAPTION = "[관세청] 반도체 6월 수출 123억불 전년비 +15%"

_TW_CAPTION = "**🇹🇼 6월 수출 대만**\n\n**▶️ 품목**\n\n**26년06월: $10.0M  (+5.0% YoY)  (-2.0% MoM)**"

_CN_CAPTION = "**🇨🇳 4월 수출 중국**\n\n**▶️ 품목**\n\n**26년04월: $10.0M  (+5.0% YoY)  (-2.0% MoM)**"

_JP2_CAPTION = "🇯🇵 5월 수출 일본\n\n▶️ 품목\n\n26년05월: $10.0M  (+5.0% YoY)  (-2.0% MoM)"

_TH_CAPTION = (
    "🇹🇭 6월 수출 태국\n\n▶️ 품목\n\n"
    "26년06월: $10.0M  (+5.0% YoY)  (-2.0% MoM)"
)

_US_CAPTION = (
    "🇺🇸 6월 수입 미국\n\n▶️ 테스트 품목\n\n"
    "26년06월: $12.3M  (+7.0% YoY)  (+1.5% MoM)\n\n"
    "관련기업: [#Test US](https://www.google.com/search?q=Test+US)\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년05월: $11.9M  (+6.0% YoY)  (+0.8% MoM)"
)


class ParseTests(unittest.TestCase):
    def test_full_message_headline_and_history_plain_hashtag(self):
        p = mx.parse_mx_export(_REAL_FULL)
        self.assertIsNotNone(p)
        self.assertEqual(p["item"], "전기회로 개폐·보호·접속용 기타 기기")
        self.assertEqual(p["companies"], ["APTV"])
        self.assertEqual(len(p["months"]), 3)  # 헤드라인 1 + 히스토리 2
        self.assertEqual(p["months"][0], {
            "month": "2026-04", "export_value_musd": 499.4,
            "export_yoy": 19.9, "export_mom": 1.0,
        })
        self.assertEqual(p["months"][-1]["month"], "2026-02")

    def test_markdown_link_companies(self):
        p = mx.parse_mx_export(_MARKDOWN_LINK_CO)
        self.assertEqual(p["companies"], ["TestCo A", "TestCo B"])
        self.assertEqual(len(p["months"]), 2)

    def test_multi_plain_hashtag_companies_split_correctly(self):
        p = mx.parse_mx_export(_MULTI_PLAIN_CO)
        self.assertEqual(p["companies"], ["Co One", "Co Two", "Co Three"])

    def test_no_company_line_graceful(self):
        p = mx.parse_mx_export(_NO_COMPANY)
        self.assertIsNotNone(p)
        self.assertEqual(p["companies"], [])
        self.assertEqual(len(p["months"]), 1)

    def test_other_source_captions_return_none(self):
        # 9차 폴백이므로 KR/JP(BeOn)/TW/CN/JP2/TH/MY/PH 포맷은 절대 MX 로 오인되면 안 됨.
        self.assertIsNone(mx.parse_mx_export(_JP1_CAPTION))
        self.assertIsNone(mx.parse_mx_export(_KR_CAPTION))
        self.assertIsNone(mx.parse_mx_export(_TW_CAPTION))
        self.assertIsNone(mx.parse_mx_export(_CN_CAPTION))
        self.assertIsNone(mx.parse_mx_export(_JP2_CAPTION))
        self.assertIsNone(mx.parse_mx_export(_TH_CAPTION))
        self.assertIsNone(mx.parse_mx_export(_US_CAPTION))
        self.assertIsNone(mx.parse_mx_export(""))
        self.assertIsNone(mx.parse_mx_export("그냥 잡담 메시지"))

    def test_no_month_line_returns_none(self):
        self.assertIsNone(mx.parse_mx_export("🇲🇽 04월 수출 멕시코\n\n▶️ 품목"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "mx.db"
        self.conn = None

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()
        self.tmp.cleanup()

    def test_ingest_stores_all_months_and_latest_snapshot(self):
        self.conn = mx.open_mx_db(self.db_path)
        ok = mx.ingest(self.conn, _REAL_FULL, source_message_id=1,
                        posted_at="2026-07-26T07:03:00Z",
                        media_paths=["2026-07-26/abc.jpg"])
        self.assertTrue(ok)
        latest = mx.list_mx(self.conn)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["month"], "2026-04")
        self.assertEqual(latest[0]["export_value_musd"], 499.4)
        self.assertIn("APTV", latest[0]["companies"])
        hist = mx.history(self.conn, "전기회로 개폐·보호·접속용 기타 기기")
        self.assertEqual(len(hist), 3)
        self.assertEqual(hist[0]["month"], "2026-02")   # 오름차순
        self.assertEqual(hist[-1]["month"], "2026-04")

    def test_reingest_preserves_media_when_new_media_absent(self):
        self.conn = mx.open_mx_db(self.db_path)
        mx.ingest(self.conn, _REAL_FULL, source_message_id=1,
                  posted_at="t", media_paths=["chart.jpg"])
        mx.ingest(self.conn, _REAL_FULL, source_message_id=1,
                  posted_at="t", media_paths=None)
        latest = mx.list_mx(self.conn)
        self.assertEqual(latest[0]["chart_media"], "chart.jpg")

    def test_other_source_caption_not_ingested(self):
        self.conn = mx.open_mx_db(self.db_path)
        self.assertFalse(mx.ingest(self.conn, _JP1_CAPTION))
        self.assertFalse(mx.ingest(self.conn, _TW_CAPTION))
        self.assertFalse(mx.ingest(self.conn, _CN_CAPTION))
        self.assertFalse(mx.ingest(self.conn, _JP2_CAPTION))
        self.assertFalse(mx.ingest(self.conn, _TH_CAPTION))
        self.assertEqual(mx.list_mx(self.conn), [])

    def test_multiple_items_independent(self):
        self.conn = mx.open_mx_db(self.db_path)
        mx.ingest(self.conn, _REAL_FULL, source_message_id=1, posted_at="t")
        mx.ingest(self.conn, _MARKDOWN_LINK_CO, source_message_id=2, posted_at="t")
        latest = mx.list_mx(self.conn)
        self.assertEqual(len(latest), 2)
        items = {r["item"] for r in latest}
        self.assertIn("테스트 품목", items)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "mx.db"
        self.conn = None

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()
        self.tmp.cleanup()

    def test_render_html_with_data(self):
        self.conn = mx.open_mx_db(self.db_path)
        mx.ingest(self.conn, _REAL_FULL, source_message_id=1, posted_at="t",
                  media_paths=["2026-07-26/abc.jpg"])
        html = mx.render_html(self.conn)
        self.assertIn("멕시코 수출 데이터", html)
        self.assertIn("499.4", html)
        self.assertIn("APTV", html)
        self.assertIn("mx-card", html)
        self.assertIn("2026-07-26/abc.jpg", html)

    def test_render_html_empty_graceful(self):
        self.conn = mx.open_mx_db(self.db_path)
        html = mx.render_html(self.conn)
        self.assertIn("아직 수집된 멕시코 수출 데이터(나쁜양파)가 없습니다", html)
        self.assertNotIn('<details class="mx-card"', html)

    def test_regenerate_writes_file(self):
        out = Path(self.tmp.name) / "mx.html"
        mx.regenerate(self.db_path, out)
        self.assertTrue(out.exists())
        self.assertIn("멕시코 수출 데이터", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
