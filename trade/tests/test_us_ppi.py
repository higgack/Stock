"""미국 PPI(나쁜양파) 소스 — 파싱·저장·렌더·라우팅 계약.

캡션 형식은 사용자 2026-08-19 스크린샷 원문 그대로.
"""

import tempfile
import unittest
from pathlib import Path

from trade import us_ppi as up


_FULL = (
    "🇺🇸 7월 미국 PPI\n\n"
    "▶️ 산업용 가스: 이산화탄소\n\n"
    "26년07월: PPI 853.28  (+9.6% YoY)  (+4.8% MoM)\n\n"
    "관련기업: #LIN  #APD  #AIR LIQUIDE\n\n"
    "PPI 최근 추이\n"
    "26년06월: PPI 814.24  (+6.7% YoY)  (+0.8% MoM)\n"
    "26년05월: PPI 807.64  (+7.0% YoY)  (+2.2% MoM)"
)


class ParseTests(unittest.TestCase):
    def test_parse_full(self):
        p = up.parse_us_ppi(_FULL)
        self.assertIsNotNone(p)
        self.assertEqual(p["item"], "산업용 가스: 이산화탄소")
        self.assertEqual(p["companies"], ["LIN", "APD", "AIR LIQUIDE"])
        self.assertEqual(len(p["months"]), 3)
        self.assertEqual(p["months"][0],
                         {"month": "2026-07", "ppi_index": 853.28,
                          "ppi_yoy": 9.6, "ppi_mom": 4.8})
        self.assertEqual(p["months"][-1]["month"], "2026-05")

    def test_deltas_are_optional(self):
        """원천이 YoY/MoM 한쪽을 빠뜨린 달에 캡션 전체를 버리면 그 달이
        통째로 유실된다 — 값만 있으면 저장하고 델타는 빈칸으로 둔다."""
        p = up.parse_us_ppi("🇺🇸 7월 미국 PPI\n▶️ 품목\n26년07월: PPI 100.5")
        self.assertEqual(p["months"], [{"month": "2026-07", "ppi_index": 100.5,
                                        "ppi_yoy": None, "ppi_mom": None}])

    def test_rejects_non_ppi_captions(self):
        self.assertIsNone(up.parse_us_ppi(
            "🇺🇸 6월 수입 미국\n▶️ 품목\n26년06월: $12.3M  (+7.0% YoY)  (+1.5% MoM)"))
        self.assertIsNone(up.parse_us_ppi(""))
        # 마커는 있는데 품목·월 데이터가 없으면 저장하지 않는다.
        self.assertIsNone(up.parse_us_ppi("미국 PPI 관련 잡담"))
        # 같은 채널이 CPI 처럼 **같은 형식의 다른 지수**를 올려도 PPI 표에
        # 섞이면 안 된다. 마커('미국 PPI')와 월 라인의 'PPI' 리터럴이 둘 다
        # 막는다 — 의도된 이중 가드라 한쪽만 없애는 뮤테이션은 통과한다
        # (그래서 이 단언은 '어느 가드가 막았나'가 아니라 **동작**을 고정).
        self.assertIsNone(up.parse_us_ppi(
            "🇺🇸 7월 미국 CPI\n▶️ 품목\n26년07월: CPI 320.1  (+2.9% YoY)"))

    def test_only_this_source_claims_a_ppi_caption(self):
        """레지스트리는 순차 폴백이라 두 파서가 같은 캡션을 물면 **조용한
        오저장**이 된다. 실제 캡션으로 배타성을 강제한다."""
        from trade import badonion_sources as srcs
        hits = [s.key for s in srcs.SOURCES if s.parse(_FULL) is not None]
        self.assertEqual(hits, ["uppi"])

    def test_ppi_parser_does_not_steal_other_sources(self):
        from trade import badonion_sources as srcs
        others = [s for s in srcs.SOURCES if s.key != "uppi"]
        samples = [
            "🇺🇸 6월 수입 미국\n▶️ 품목\n26년06월: $12.3M  (+7.0% YoY)  (+1.5% MoM)",
            "🇹🇼 6월 수출 대만\n▶️ 품목\n26년06월: $12.3M  (+7.0% YoY)  (+1.5% MoM)",
        ]
        for cap in samples:
            with self.subTest(cap=cap[:12]):
                self.assertIsNone(up.parse_us_ppi(cap))
                self.assertTrue(any(s.parse(cap) is not None for s in others),
                                "샘플이 어떤 소스에도 안 걸리면 테스트가 무의미")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "us_ppi.db"
        self.conn = up.open_us_ppi_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_ingest_and_history(self):
        self.assertTrue(up.ingest(self.conn, _FULL, source_message_id=7,
                                  posted_at="2026-08-19T08:13:00+09:00",
                                  media_paths=["media/ppi.jpg"]))
        rows = up.list_us_ppi(self.conn)
        self.assertEqual(len(rows), 1, "최신월 1행만 카드가 된다")
        self.assertEqual(rows[0]["month"], "2026-07")
        self.assertAlmostEqual(rows[0]["ppi_index"], 853.28)
        self.assertEqual(rows[0]["chart_media"], "media/ppi.jpg")
        hist = up.history(self.conn, "산업용 가스: 이산화탄소")
        self.assertEqual([h["month"] for h in hist],
                         ["2026-05", "2026-06", "2026-07"])

    def test_reingest_is_idempotent_and_keeps_media(self):
        """롤링 — 다음 달 캡션이 오면 과거월을 지우지 않고 쌓이고, 차트가
        없는 재적재가 기존 차트를 지우지 않는다."""
        up.ingest(self.conn, _FULL, source_message_id=7, posted_at="",
                  media_paths=["media/ppi.jpg"])
        up.ingest(self.conn, _FULL, source_message_id=7, posted_at="",
                  media_paths=None)
        hist = up.history(self.conn, "산업용 가스: 이산화탄소")
        self.assertEqual(len(hist), 3, "재적재로 중복 행이 생기면 안 된다")
        self.assertEqual(hist[-1]["chart_media"], "media/ppi.jpg")

    def test_render_shows_index_and_history(self):
        up.ingest(self.conn, _FULL, source_message_id=7, posted_at="",
                  media_paths=["media/ppi.jpg"])
        html = up.render_html(self.conn)
        self.assertIn("853.28", html)
        self.assertIn("산업용 가스: 이산화탄소", html)
        self.assertIn("LIN", html)
        self.assertIn("<details", html, "히스토리 3개월이면 펼침 카드")
        self.assertIn("../media/ppi.jpg", html)
        # 지수를 금액으로 표기하면 안 된다(다른 소스에서 복사한 흔적).
        self.assertNotIn("$853", html)
        self.assertIn("PPI 지수", html)

    def test_render_empty_state(self):
        html = up.render_html(self.conn)
        self.assertIn("← 수출입 대시보드", html)
        self.assertIn("아직 수집된", html)


class RoutingTests(unittest.TestCase):
    def test_registered_in_nav_and_regenerated(self):
        """레지스트리 등록이 빠지면 페이지가 생성돼도 **도달 불가**다."""
        from trade import badonion_sources as srcs
        s = srcs.by_key("uppi")
        self.assertIsNotNone(s)
        self.assertEqual(s.db_file, "us_ppi.db")
        self.assertEqual(s.html_file, "us_ppi.html")
        self.assertIn("uppi", srcs.NAV_ORDER)
        self.assertIn('href="us_ppi.html"', srcs.nav_html())
        self.assertIn("미국 PPI", srcs.labels())

    def test_regenerate_writes_page(self):
        with tempfile.TemporaryDirectory() as d:
            db, out = Path(d) / "us_ppi.db", Path(d) / "us_ppi.html"
            conn = up.open_us_ppi_db(db)
            up.ingest(conn, _FULL, source_message_id=1, posted_at="",
                      media_paths=None)
            conn.close()
            up.regenerate(db, out)
            self.assertIn("853.28", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
