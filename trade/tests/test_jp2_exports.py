"""일본 수출 데이터(나쁜양파, 두 번째 소스) 파서 + 저장 회귀 (사용자 2026-07-11).

대만(tw_exports)·중국(cn_exports)과 완전히 동일한 채널·포맷(마커 단어만
"일본")이므로 테스트 구조도 그대로 미러링. 유일한 차이: 이 채널의 일본
메시지는 관련기업이 마크다운 링크가 아니라 평문 해시태그(#Mazda Motor)로
오는 경우가 있어 그 폴백 경로도 검증. 픽스처는 실측 스크린샷 그대로
(2026-07-11 t.me/Badonions 채널, "5월 수출 일본 — 디젤 승용차")."""
import tempfile
import unittest
from pathlib import Path

from trade import jp2_exports as jp2


# 실측 원문(사용자 2026-07-11 스크린샷) — 회사 평문 해시태그 1개 + 히스토리 2개월.
_REAL_FULL = (
    "🇯🇵 5월 수출 일본\n\n"
    "▶️ 디젤 승용차(1500cc 초과~2500cc 이하)\n\n"
    "26년05월: $17.6M  (+41.7% YoY)  (+13.8% MoM)\n\n"
    "관련기업: #Mazda Motor\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년04월: $15.5M  (+14.1% YoY)  (-7.0% MoM)\n"
    "26년03월: $16.6M  (+4.2% YoY)  (-1.0% MoM)"
)

# 마크다운 링크 회사(대만/중국 스타일) — 이 채널도 링크 포맷을 섞어 쓸 수 있어
# 두 경로 다 커버.
_MARKDOWN_LINK_CO = (
    "**🇯🇵 6월 수출 일본**\n\n**▶️ 반도체 제조장비**\n\n"
    "**26년06월: $50.0M  (+10.0% YoY)  (+2.0% MoM)**\n\n"
    "관련기업: [#Tokyo Electron](https://www.google.com/search?q=Tokyo+Electron+Stock)  "
    "[#Advantest](https://www.google.com/search?q=Advantest+Stock)\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년05월: $45.0M  (+8.0% YoY)  (+1.0% MoM)"
)

# 회사 여러 개(평문 해시태그 다중) — 구분 확인용
_MULTI_PLAIN_CO = (
    "🇯🇵 5월 수출 일본\n\n▶️ 자동차 부품\n\n"
    "26년05월: $30.0M  (+5.0% YoY)  (+1.0% MoM)\n\n"
    "관련기업: #Toyota Motor #Honda Motor #Nissan\n\n"
    "최근 추이 (단위: USD M$)\n26년04월: $29.0M  (+3.0% YoY)  (-1.0% MoM)"
)

# 회사 0개 — graceful
_NO_COMPANY = "🇯🇵 5월 수출 일본\n\n▶️ 어떤 품목\n\n26년05월: $10.0M  (+5.0% YoY)  (-2.0% MoM)"

_JP1_CAPTION = (
    "📈 일본 수출 데이터 업데이트: 다이싱/어셈블리 (DISCO)\n"
    "📅 최신 월: 2026-05\n"
    "💰 수출액: 27.6십억 엔\n"
)

_KR_CAPTION = "[관세청] 반도체 6월 수출 123억불 전년비 +15%"

_TW_CAPTION = "**🇹🇼 6월 수출 대만**\n\n**▶️ 품목**\n\n**26년06월: $10.0M  (+5.0% YoY)  (-2.0% MoM)**"

_CN_CAPTION = "**🇨🇳 4월 수출 중국**\n\n**▶️ 품목**\n\n**26년04월: $10.0M  (+5.0% YoY)  (-2.0% MoM)**"


class ParseTests(unittest.TestCase):
    def test_full_message_headline_and_history_plain_hashtag(self):
        p = jp2.parse_jp2_export(_REAL_FULL)
        self.assertIsNotNone(p)
        self.assertEqual(p["item"], "디젤 승용차(1500cc 초과~2500cc 이하)")
        self.assertEqual(p["companies"], ["Mazda Motor"])
        self.assertEqual(len(p["months"]), 3)  # 헤드라인 1 + 히스토리 2
        self.assertEqual(p["months"][0], {
            "month": "2026-05", "export_value_musd": 17.6,
            "export_yoy": 41.7, "export_mom": 13.8,
        })
        self.assertEqual(p["months"][-1]["month"], "2026-03")

    def test_negative_mom_signed(self):
        p = jp2.parse_jp2_export(_REAL_FULL)
        last = p["months"][-1]
        self.assertEqual(last["export_yoy"], 4.2)
        self.assertEqual(last["export_mom"], -1.0)

    def test_markdown_link_companies(self):
        p = jp2.parse_jp2_export(_MARKDOWN_LINK_CO)
        self.assertEqual(p["companies"], ["Tokyo Electron", "Advantest"])
        self.assertEqual(len(p["months"]), 2)

    def test_multi_plain_hashtag_companies_split_correctly(self):
        p = jp2.parse_jp2_export(_MULTI_PLAIN_CO)
        self.assertEqual(p["companies"], ["Toyota Motor", "Honda Motor", "Nissan"])

    def test_no_company_line_graceful(self):
        p = jp2.parse_jp2_export(_NO_COMPANY)
        self.assertIsNotNone(p)
        self.assertEqual(p["companies"], [])
        self.assertEqual(len(p["months"]), 1)

    def test_other_source_captions_return_none(self):
        # 5차 폴백이므로 KR/JP(BeOn)/TW/CN 포맷은 절대 JP2 로 오인되면 안 됨.
        self.assertIsNone(jp2.parse_jp2_export(_JP1_CAPTION))
        self.assertIsNone(jp2.parse_jp2_export(_KR_CAPTION))
        self.assertIsNone(jp2.parse_jp2_export(_TW_CAPTION))
        self.assertIsNone(jp2.parse_jp2_export(_CN_CAPTION))
        self.assertIsNone(jp2.parse_jp2_export(""))
        self.assertIsNone(jp2.parse_jp2_export("그냥 잡담 메시지"))

    def test_no_month_line_returns_none(self):
        self.assertIsNone(jp2.parse_jp2_export("🇯🇵 5월 수출 일본\n\n▶️ 품목"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "jp2.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_ingest_stores_all_months_and_latest_snapshot(self):
        conn = jp2.open_jp2_db(self.db_path)
        ok = jp2.ingest(conn, _REAL_FULL, source_message_id=1,
                        posted_at="2026-07-11T07:03:00Z",
                        media_paths=["2026-07-11/abc.jpg"])
        self.assertTrue(ok)
        latest = jp2.list_jp2(conn)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["month"], "2026-05")
        self.assertEqual(latest[0]["export_value_musd"], 17.6)
        self.assertIn("Mazda Motor", latest[0]["companies"])
        hist = jp2.history(conn, "디젤 승용차(1500cc 초과~2500cc 이하)")
        self.assertEqual(len(hist), 3)
        self.assertEqual(hist[0]["month"], "2026-03")   # 오름차순
        self.assertEqual(hist[-1]["month"], "2026-05")

    def test_reingest_preserves_media_when_new_media_absent(self):
        conn = jp2.open_jp2_db(self.db_path)
        jp2.ingest(conn, _REAL_FULL, source_message_id=1,
                  posted_at="t", media_paths=["chart.jpg"])
        jp2.ingest(conn, _REAL_FULL, source_message_id=1,
                  posted_at="t", media_paths=None)
        latest = jp2.list_jp2(conn)
        self.assertEqual(latest[0]["chart_media"], "chart.jpg")

    def test_other_source_caption_not_ingested(self):
        conn = jp2.open_jp2_db(self.db_path)
        self.assertFalse(jp2.ingest(conn, _JP1_CAPTION))
        self.assertFalse(jp2.ingest(conn, _TW_CAPTION))
        self.assertFalse(jp2.ingest(conn, _CN_CAPTION))
        self.assertEqual(jp2.list_jp2(conn), [])

    def test_multiple_items_independent(self):
        conn = jp2.open_jp2_db(self.db_path)
        jp2.ingest(conn, _REAL_FULL, source_message_id=1, posted_at="t")
        jp2.ingest(conn, _MARKDOWN_LINK_CO, source_message_id=2, posted_at="t")
        latest = jp2.list_jp2(conn)
        self.assertEqual(len(latest), 2)
        items = {r["item"] for r in latest}
        self.assertIn("반도체 제조장비", items)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "jp2.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_html_with_data(self):
        conn = jp2.open_jp2_db(self.db_path)
        jp2.ingest(conn, _REAL_FULL, source_message_id=1, posted_at="t",
                  media_paths=["2026-07-11/abc.jpg"])
        html = jp2.render_html(conn)
        self.assertIn("일본 수출 데이터", html)
        self.assertIn("17.6", html)
        self.assertIn("Mazda Motor", html)
        self.assertIn("jp2-card", html)
        self.assertIn("2026-07-11/abc.jpg", html)

    def test_render_html_empty_graceful(self):
        conn = jp2.open_jp2_db(self.db_path)
        html = jp2.render_html(conn)
        self.assertIn("아직 수집된 일본 수출 데이터(나쁜양파)가 없습니다", html)
        self.assertNotIn('<details class="jp2-card"', html)

    def test_regenerate_writes_file(self):
        out = Path(self.tmp.name) / "jp2.html"
        jp2.regenerate(self.db_path, out)
        self.assertTrue(out.exists())
        self.assertIn("일본 수출 데이터", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
