"""중국 수출 데이터(나쁜양파) 파서 + 저장 회귀 (사용자 2026-07-11).

대만(tw_exports)과 완전히 동일한 채널·포맷(마커 단어만 "중국")이므로 테스트
구조도 test_tw_exports.py 를 그대로 미러링. 픽스처는 실측 스크린샷 그대로
(2026-07-11 t.me/Badonions 채널, "4월 수출 중국 — 전기차")."""
import tempfile
import unittest
from pathlib import Path

from trade import cn_exports as cn


# 실측 원문(사용자 2026-07-11 스크린샷) — 회사 6개 + 히스토리 2개월(스크린샷 노출분).
_REAL_FULL = (
    "**🇨🇳 4월 수출 중국**\n\n"
    "**▶️ 전기차**\n\n"
    "**26년04월: $4,688.8M  (+32.0% YoY)  (+27.4% MoM)**\n\n"
    "관련기업: [#CATL](https://www.google.com/search?q=CATL+Stock)  "
    "[#BYD](https://www.google.com/search?q=BYD+Stock)  "
    "[#Li Auto](https://www.google.com/search?q=Li+Auto+Stock)  "
    "[#NIO](https://www.google.com/search?q=NIO+Stock)  "
    "[#XPeng](https://www.google.com/search?q=XPeng+Stock)  "
    "[#Xiaomi](https://www.google.com/search?q=Xiaomi+Stock)\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년03월: $3,681.0M  (+63.2% YoY)  (+21.8% MoM)\n"
    "26년02월: $3,022.2M  (+98.3% YoY)  (-24.6% MoM)"
)

# 회사 2개, 히스토리 1개월(짧은 케이스)
_SHORT_MULTI_CO = (
    "**🇨🇳 4월 수출 중국**\n\n"
    "**▶️ 반도체**\n\n"
    "**26년04월: $200.0M  (+10.0% YoY)  (+5.0% MoM)**\n\n"
    "관련기업: [#SMIC](https://www.google.com/search?q=SMIC+Stock)  "
    "[#Hua Hong](https://www.google.com/search?q=Hua+Hong+Stock)\n\n"
    "최근 추이 (단위: USD M$)\n"
    "26년03월: $190.0M  (+8.0% YoY)  (+2.0% MoM)"
)

# 회사 0개(관련기업 줄 없음) — graceful
_NO_COMPANY = (
    "**🇨🇳 4월 수출 중국**\n\n**▶️ 어떤 품목**\n\n"
    "**26년04월: $10.0M  (+5.0% YoY)  (-2.0% MoM)**"
)

_JP_CAPTION = (
    "📈 일본 수출 데이터 업데이트: 다이싱/어셈블리 (DISCO)\n"
    "📅 최신 월: 2026-05\n"
    "💰 수출액: 27.6십억 엔\n"
)

_KR_CAPTION = "[관세청] 반도체 6월 수출 123억불 전년비 +15%"

_TW_CAPTION = (
    "**🇹🇼 6월 수출 대만**\n\n**▶️ 품목**\n\n"
    "**26년06월: $10.0M  (+5.0% YoY)  (-2.0% MoM)**"
)


class ParseTests(unittest.TestCase):
    def test_full_message_headline_and_history(self):
        p = cn.parse_cn_export(_REAL_FULL)
        self.assertIsNotNone(p)
        self.assertEqual(p["item"], "전기차")
        self.assertEqual(len(p["companies"]), 6)
        self.assertEqual(p["companies"][0], "CATL")
        self.assertEqual(len(p["months"]), 3)  # 헤드라인 1 + 히스토리 2
        self.assertEqual(p["months"][0], {
            "month": "2026-04", "export_value_musd": 4688.8,
            "export_yoy": 32.0, "export_mom": 27.4,
        })
        self.assertEqual(p["months"][-1]["month"], "2026-02")

    def test_negative_mom_signed(self):
        p = cn.parse_cn_export(_REAL_FULL)
        last = p["months"][-1]
        self.assertEqual(last["export_yoy"], 98.3)
        self.assertEqual(last["export_mom"], -24.6)

    def test_short_multi_company(self):
        p = cn.parse_cn_export(_SHORT_MULTI_CO)
        self.assertEqual(p["companies"], ["SMIC", "Hua Hong"])
        self.assertEqual(len(p["months"]), 2)

    def test_no_company_line_graceful(self):
        p = cn.parse_cn_export(_NO_COMPANY)
        self.assertIsNotNone(p)
        self.assertEqual(p["companies"], [])
        self.assertEqual(len(p["months"]), 1)

    def test_jp_kr_tw_captions_return_none(self):
        # 4차 폴백이므로 KR/JP/TW 포맷은 절대 CN 으로 오인되면 안 됨(폴백 순서 안전).
        self.assertIsNone(cn.parse_cn_export(_JP_CAPTION))
        self.assertIsNone(cn.parse_cn_export(_KR_CAPTION))
        self.assertIsNone(cn.parse_cn_export(_TW_CAPTION))
        self.assertIsNone(cn.parse_cn_export(""))
        self.assertIsNone(cn.parse_cn_export("그냥 잡담 메시지"))

    def test_no_month_line_returns_none(self):
        self.assertIsNone(cn.parse_cn_export("**🇨🇳 4월 수출 중국**\n\n**▶️ 품목**"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cn.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_ingest_stores_all_months_and_latest_snapshot(self):
        conn = cn.open_cn_db(self.db_path)
        ok = cn.ingest(conn, _REAL_FULL, source_message_id=1,
                       posted_at="2026-07-11T15:34:00Z",
                       media_paths=["2026-07-11/abc.jpg"])
        self.assertTrue(ok)
        latest = cn.list_cn(conn)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["month"], "2026-04")
        self.assertEqual(latest[0]["export_value_musd"], 4688.8)
        self.assertIn("CATL", latest[0]["companies"])
        hist = cn.history(conn, "전기차")
        self.assertEqual(len(hist), 3)
        self.assertEqual(hist[0]["month"], "2026-02")   # 오름차순
        self.assertEqual(hist[-1]["month"], "2026-04")

    def test_reingest_preserves_media_when_new_media_absent(self):
        conn = cn.open_cn_db(self.db_path)
        cn.ingest(conn, _REAL_FULL, source_message_id=1,
                 posted_at="t", media_paths=["chart.jpg"])
        cn.ingest(conn, _REAL_FULL, source_message_id=1,
                 posted_at="t", media_paths=None)
        latest = cn.list_cn(conn)
        self.assertEqual(latest[0]["chart_media"], "chart.jpg")

    def test_non_cn_caption_not_ingested(self):
        conn = cn.open_cn_db(self.db_path)
        self.assertFalse(cn.ingest(conn, _JP_CAPTION))
        self.assertFalse(cn.ingest(conn, _KR_CAPTION))
        self.assertFalse(cn.ingest(conn, _TW_CAPTION))
        self.assertEqual(cn.list_cn(conn), [])

    def test_multiple_items_independent(self):
        conn = cn.open_cn_db(self.db_path)
        cn.ingest(conn, _REAL_FULL, source_message_id=1, posted_at="t")
        cn.ingest(conn, _SHORT_MULTI_CO, source_message_id=2, posted_at="t")
        latest = cn.list_cn(conn)
        self.assertEqual(len(latest), 2)
        items = {r["item"] for r in latest}
        self.assertIn("반도체", items)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cn.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_html_with_data(self):
        conn = cn.open_cn_db(self.db_path)
        cn.ingest(conn, _REAL_FULL, source_message_id=1, posted_at="t",
                 media_paths=["2026-07-11/abc.jpg"])
        html = cn.render_html(conn)
        self.assertIn("중국 수출 데이터", html)
        self.assertIn("4,688.8", html)
        self.assertIn("CATL", html)
        self.assertIn("cn-card", html)
        self.assertIn("2026-07-11/abc.jpg", html)

    def test_render_html_empty_graceful(self):
        conn = cn.open_cn_db(self.db_path)
        html = cn.render_html(conn)
        self.assertIn("아직 수집된 중국 수출 데이터가 없습니다", html)
        self.assertNotIn('<details class="cn-card"', html)

    def test_regenerate_writes_file(self):
        out = Path(self.tmp.name) / "cn.html"
        cn.regenerate(self.db_path, out)
        self.assertTrue(out.exists())
        self.assertIn("중국 수출 데이터", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
