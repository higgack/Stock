"""일본 수출 데이터(BeOn) 파서 + 저장 회귀 (사용자 2026-06-27).

한국 관세청 포맷과 다른 BeOn 일본 수출 메시지의 파싱·최신스냅샷 저장·렌더가
정상이고, 한국 캡션은 None(한국 파서로 폴백)인지 검증."""
import tempfile
import unittest
from pathlib import Path

from trade import jp_exports as jp


_FULL = (
    "📈 일본 수출 데이터 업데이트: 다이싱/어셈블리 (DISCO)\n"
    "🏭 디스코 (DISCO)\n"
    "─────\n"
    "📅 최신 월: 2026-05\n"
    "💰 수출액: 27.6십억 엔\n"
    "   YoY ▲ +16.5% / MoM ▲ +1.8%\n"
    "📦 수출 단가: 19.3천엔/KG\n"
    "   YoY ▼ -5.2% / MoM ▼ -11.6%\n"
)

_NO_COMPANY = (
    "📈 일본 수출 데이터 업데이트: 본딩 기기 (Bonding)\n"
    "📅 최신 월: 2026-05\n"
    "💰 수출액: 2.5십억 엔\n"
    "   YoY ▲ +12.0% / MoM ▼ -12.0%\n"
    "📦 수출 단가: 27.6천엔/KG\n"
    "   YoY ▲ +33.0% / MoM ▼ -0.5%\n"
)

_TRUNCATED = (
    "📈 일본 수출 데이터 업데이트: 광섬유 (Optical Fiber)\n"
    "📅 최신 월: 2026-05\n"
    "💰 수출액: 20.71십억 엔\n"
)


class TestJPParser(unittest.TestCase):
    def test_full(self):
        r = jp.parse_jp_export(_FULL)
        self.assertIsNotNone(r)
        self.assertEqual(r["item"], "다이싱/어셈블리 (DISCO)")
        self.assertEqual(r["company"], "디스코 (DISCO)")
        self.assertEqual(r["latest_month"], "2026-05")
        self.assertEqual(r["export_value_bn"], 27.6)
        self.assertEqual(r["export_yoy"], 16.5)
        self.assertEqual(r["export_mom"], 1.8)
        self.assertEqual(r["price_per_kg"], 19.3)
        self.assertEqual(r["price_yoy"], -5.2)
        self.assertEqual(r["price_mom"], -11.6)

    def test_no_company_and_signs(self):
        r = jp.parse_jp_export(_NO_COMPANY)
        self.assertIsNotNone(r)
        self.assertIsNone(r["company"])
        self.assertEqual(r["item"], "본딩 기기 (Bonding)")
        # 부호숫자 우선(MoM -12.0), YoY +값
        self.assertEqual(r["export_yoy"], 12.0)
        self.assertEqual(r["export_mom"], -12.0)
        self.assertEqual(r["price_yoy"], 33.0)
        self.assertEqual(r["price_mom"], -0.5)

    def test_truncated_value_only(self):
        r = jp.parse_jp_export(_TRUNCATED)
        self.assertIsNotNone(r)
        self.assertEqual(r["export_value_bn"], 20.71)
        self.assertIsNone(r["price_per_kg"])
        self.assertIsNone(r["export_yoy"])      # YoY 줄 없음

    def test_arrow_only_sign(self):
        # 명시 부호 없이 화살표만으로 하락 판정.
        cap = ("일본 수출 데이터 업데이트: 테스트\n"
               "최신 월: 2026-05\n수출액: 1.0십억 엔\n   YoY ▼ 5.0% / MoM ▲ 2.0%\n")
        r = jp.parse_jp_export(cap)
        self.assertEqual(r["export_yoy"], -5.0)   # ▼ → 음수
        self.assertEqual(r["export_mom"], 2.0)

    def test_korean_caption_returns_none(self):
        kr = ("니켈도금강판 (전국)\n관련종목: 월별 수출 데이터\n\n"
              "2026년 5월 확정치 수출데이터 입니다.")
        self.assertIsNone(jp.parse_jp_export(kr))

    def test_empty_none(self):
        self.assertIsNone(jp.parse_jp_export(""))
        self.assertIsNone(jp.parse_jp_export("그냥 잡담 메시지"))


class TestJPStore(unittest.TestCase):
    def _conn(self):
        d = Path(tempfile.mkdtemp())
        return jp.open_jp_db(d / "jp.db")

    def test_upsert_and_list(self):
        c = self._conn()
        self.assertTrue(jp.ingest(c, _FULL, source_message_id=1,
                                  media_paths=["media/2026-05/x.jpg"]))
        rows = jp.list_jp(c)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item"], "다이싱/어셈블리 (DISCO)")
        self.assertEqual(rows[0]["chart_media"], "media/2026-05/x.jpg")

    def test_latest_wins_older_skipped(self):
        c = self._conn()
        newer = _FULL  # 2026-05
        older = _FULL.replace("2026-05", "2026-04").replace("27.6십억", "99.9십억")
        self.assertTrue(jp.ingest(c, newer, source_message_id=1))
        self.assertFalse(jp.ingest(c, older, source_message_id=2))  # 과거월 → skip
        rows = jp.list_jp(c)
        self.assertEqual(rows[0]["latest_month"], "2026-05")
        self.assertEqual(rows[0]["export_value_bn"], 27.6)         # 최신 보존

    def test_same_item_newer_updates(self):
        c = self._conn()
        jp.ingest(c, _FULL, source_message_id=1)
        newer = _FULL.replace("2026-05", "2026-06").replace("27.6십억", "30.0십억")
        self.assertTrue(jp.ingest(c, newer, source_message_id=2))
        rows = jp.list_jp(c)
        self.assertEqual(len(rows), 1)                              # 같은 품목 = 1행
        self.assertEqual(rows[0]["export_value_bn"], 30.0)

    def test_render_smoke(self):
        c = self._conn()
        jp.ingest(c, _FULL, source_message_id=1,
                  media_paths=["media/2026-05/x.jpg"])
        html = jp.render_html(c, media_url_prefix="../")
        self.assertIn("일본 수출 데이터", html)
        self.assertIn("다이싱/어셈블리", html)
        self.assertIn("27.6십억 엔", html)
        self.assertIn("../media/2026-05/x.jpg", html)
        self.assertIn('href="index.html"', html)                   # 백링크

    def test_render_empty(self):
        c = self._conn()
        html = jp.render_html(c)
        self.assertIn("아직 수집된 일본 수출 데이터가 없습니다", html)


if __name__ == "__main__":
    unittest.main()
