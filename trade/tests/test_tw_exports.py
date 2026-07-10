"""대만 수출 데이터(나쁜양파) 파서 + 저장 회귀 (사용자 2026-07-10).

한국·일본과 다른 나쁜양파 대만 수출 메시지(관련기업 여러 개, 메시지 1건에
최신월+과거 히스토리 동봉)의 파싱·(품목,월) 단위 저장·렌더가 정상이고,
한국/일본 캡션은 None(그쪽 파서로 폴백)인지 검증. 픽스처는 VM 실측 원문 그대로
(2026-07-09 t.me/Badonions 채널)."""
import tempfile
import unittest
from pathlib import Path

from trade import tw_exports as tw


# 실측 원문(사용자 2026-07-10 VM peek) — 마크다운 볼드(**) + 회사 4개 + 히스토리 12개월.
_REAL_FULL = (
    "**🇹🇼 6월 수출 대만**\n\n"
    "**▶️ 인쇄회로·통신기기·컴퓨터 부품 제조용 기계 부분품**\n\n"
    "**26년06월: $61.2M  (+27.6% YoY)  (+9.8% MoM)**\n\n"
    "관련기업: [#Foxsemicon Integrated Technology]"
    "(https://www.google.com/search?q=Foxsemicon+Integrated+Technology+Stock)  "
    "[#All Ring Tech](https://www.google.com/search?q=All+Ring+Tech+Stock)  "
    "[#Gallant Precision Machining]"
    "(https://www.google.com/search?q=Gallant+Precision+Machining+Stock)  "
    "[#Scientech](https://www.google.com/search?q=Scientech+Stock)\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년05월: $55.7M  (+4.4% YoY)  (+13.0% MoM)\n"
    "26년04월: $49.3M  (+1.4% YoY)  (-3.7% MoM)\n"
    "26년03월: $51.2M  (+1.4% YoY)  (+31.0% MoM)\n"
    "26년02월: $39.1M  (+4.3% YoY)  (-20.2% MoM)\n"
    "26년01월: $49.0M  (+36.0% YoY)  (-0.8% MoM)\n"
    "25년12월: $49.4M  (+6.9% YoY)  (+6.1% MoM)\n"
    "25년11월: $46.5M  (+4.3% YoY)  (+7.4% MoM)\n"
    "25년10월: $43.3M  (+4.0% YoY)  (+2.3% MoM)\n"
    "25년09월: $42.3M  (+7.2% YoY)  (-16.8% MoM)\n"
    "25년08월: $50.8M  (+1.8% YoY)  (-3.8% MoM)\n"
    "25년07월: $52.9M  (+20.9% YoY)  (+10.2% MoM)\n"
    "25년06월: $48.0M  (-10.7% YoY)  (-10.2% MoM)"
)

# 회사 3개, 히스토리 1개월(짧은 케이스)
_SHORT_MULTI_CO = (
    "**🇹🇼 6월 수출 대만**\n\n"
    "**▶️ 반도체 웨이퍼·소자 측정용·검사용 기기 부분품**\n\n"
    "**26년06월: $131.6M  (+41.6% YoY)  (+15.4% MoM)**\n\n"
    "관련기업: [#Chroma ATE](https://www.google.com/search?q=Chroma+ATE+Stock)  "
    "[#MPI](https://www.google.com/search?q=MPI+Stock)  "
    "[#Scientech](https://www.google.com/search?q=Scientech+Stock)\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년05월: $114.0M  (+20.7% YoY)  (+10.7% MoM)"
)

# 회사 0개(관련기업 줄 없음) — graceful
_NO_COMPANY = (
    "**🇹🇼 6월 수출 대만**\n\n"
    "**▶️ 어떤 품목**\n\n"
    "**26년06월: $10.0M  (+5.0% YoY)  (-2.0% MoM)**"
)

_JP_CAPTION = (
    "📈 일본 수출 데이터 업데이트: 다이싱/어셈블리 (DISCO)\n"
    "📅 최신 월: 2026-05\n"
    "💰 수출액: 27.6십억 엔\n"
)

_KR_CAPTION = "[관세청] 반도체 6월 수출 123억불 전년비 +15%"


class ParseTests(unittest.TestCase):
    def test_full_message_headline_and_history(self):
        p = tw.parse_tw_export(_REAL_FULL)
        self.assertIsNotNone(p)
        self.assertEqual(p["item"], "인쇄회로·통신기기·컴퓨터 부품 제조용 기계 부분품")
        self.assertEqual(len(p["companies"]), 4)
        self.assertEqual(p["companies"][0], "Foxsemicon Integrated Technology")
        self.assertEqual(len(p["months"]), 13)  # 헤드라인 1 + 히스토리 12
        self.assertEqual(p["months"][0], {
            "month": "2026-06", "export_value_musd": 61.2,
            "export_yoy": 27.6, "export_mom": 9.8,
        })
        self.assertEqual(p["months"][-1]["month"], "2025-06")

    def test_negative_yoy_mom_signed(self):
        p = tw.parse_tw_export(_REAL_FULL)
        last = p["months"][-1]
        self.assertEqual(last["export_yoy"], -10.7)
        self.assertEqual(last["export_mom"], -10.2)

    def test_short_multi_company(self):
        p = tw.parse_tw_export(_SHORT_MULTI_CO)
        self.assertEqual(p["companies"], ["Chroma ATE", "MPI", "Scientech"])
        self.assertEqual(len(p["months"]), 2)

    def test_no_company_line_graceful(self):
        p = tw.parse_tw_export(_NO_COMPANY)
        self.assertIsNotNone(p)
        self.assertEqual(p["companies"], [])
        self.assertEqual(len(p["months"]), 1)

    def test_jp_and_kr_captions_return_none(self):
        # 3차 폴백이므로 KR/JP 포맷은 절대 TW 로 오인되면 안 됨(폴백 순서 안전).
        self.assertIsNone(tw.parse_tw_export(_JP_CAPTION))
        self.assertIsNone(tw.parse_tw_export(_KR_CAPTION))
        self.assertIsNone(tw.parse_tw_export(""))
        self.assertIsNone(tw.parse_tw_export("그냥 잡담 메시지"))

    def test_no_month_line_returns_none(self):
        # 마커+품목은 있지만 파싱 가능한 월 라인이 전혀 없으면 미인식.
        self.assertIsNone(tw.parse_tw_export("**🇹🇼 6월 수출 대만**\n\n**▶️ 품목**"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "tw.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_ingest_stores_all_months_and_latest_snapshot(self):
        conn = tw.open_tw_db(self.db_path)
        ok = tw.ingest(conn, _REAL_FULL, source_message_id=5716,
                       posted_at="2026-07-09T23:43:58Z",
                       media_paths=["2026-07-09/abc.jpg"])
        self.assertTrue(ok)
        latest = tw.list_tw(conn)
        self.assertEqual(len(latest), 1)  # 품목 1개
        self.assertEqual(latest[0]["month"], "2026-06")
        self.assertEqual(latest[0]["export_value_musd"], 61.2)
        self.assertIn("Foxsemicon", latest[0]["companies"])
        hist = tw.history(conn, "인쇄회로·통신기기·컴퓨터 부품 제조용 기계 부분품")
        self.assertEqual(len(hist), 13)
        self.assertEqual(hist[0]["month"], "2025-06")   # 오름차순
        self.assertEqual(hist[-1]["month"], "2026-06")

    def test_reingest_preserves_media_when_new_media_absent(self):
        conn = tw.open_tw_db(self.db_path)
        tw.ingest(conn, _REAL_FULL, source_message_id=1,
                 posted_at="t", media_paths=["chart.jpg"])
        tw.ingest(conn, _REAL_FULL, source_message_id=1,
                 posted_at="t", media_paths=None)
        latest = tw.list_tw(conn)
        self.assertEqual(latest[0]["chart_media"], "chart.jpg")

    def test_non_tw_caption_not_ingested(self):
        conn = tw.open_tw_db(self.db_path)
        self.assertFalse(tw.ingest(conn, _JP_CAPTION))
        self.assertFalse(tw.ingest(conn, _KR_CAPTION))
        self.assertEqual(tw.list_tw(conn), [])

    def test_multiple_items_independent(self):
        conn = tw.open_tw_db(self.db_path)
        tw.ingest(conn, _REAL_FULL, source_message_id=1, posted_at="t")
        tw.ingest(conn, _SHORT_MULTI_CO, source_message_id=2, posted_at="t")
        latest = tw.list_tw(conn)
        self.assertEqual(len(latest), 2)
        items = {r["item"] for r in latest}
        self.assertIn("반도체 웨이퍼·소자 측정용·검사용 기기 부분품", items)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "tw.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_html_with_data(self):
        conn = tw.open_tw_db(self.db_path)
        tw.ingest(conn, _REAL_FULL, source_message_id=1, posted_at="t",
                 media_paths=["2026-07-09/abc.jpg"])
        html = tw.render_html(conn)
        self.assertIn("대만 수출 데이터", html)
        self.assertIn("61.2", html)
        self.assertIn("Foxsemicon", html)
        self.assertIn("tw-card", html)
        self.assertIn("2026-07-09/abc.jpg", html)  # chart media wired

    def test_render_html_empty_graceful(self):
        conn = tw.open_tw_db(self.db_path)
        html = tw.render_html(conn)
        self.assertIn("아직 수집된 대만 수출 데이터가 없습니다", html)
        self.assertNotIn('<details class="tw-card"', html)  # 카드 엘리먼트 없음(CSS 정의 자체는 항상 존재)

    def test_regenerate_writes_file(self):
        out = Path(self.tmp.name) / "tw.html"
        tw.regenerate(self.db_path, out)
        self.assertTrue(out.exists())
        self.assertIn("대만 수출 데이터", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
