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


# 실제 BeOn 본딩 메시지 — 🔺🔻 이모지 화살표 + 관련회사 줄(2026-06-28 화면 누락 건).
_REAL_BONDING = (
    "📈 일본 수출 데이터 업데이트: 본딩 기기 (Bonding)\n"
    "🏭 신카와, ASM PT\n"
    "─────────\n"
    "📅 최신 월: 2026-05\n"
    "💰 수출액: 2.5십억 엔\n"
    "   YoY 🔺 +12.0% / MoM 🔻 -12.0%\n"
    "📦 수출 단가: 27.6천엔/KG\n"
    "   YoY 🔺 +33.0% / MoM 🔻 -0.5%\n"
)


class TestJPParser(unittest.TestCase):
    def test_real_bonding_emoji_arrows_and_company(self):
        # 화면에서 빠졌던 관련회사 + YoY/MoM(🔺🔻 이모지) 보강 검증.
        r = jp.parse_jp_export(_REAL_BONDING)
        self.assertIsNotNone(r)
        self.assertEqual(r["item"], "본딩 기기 (Bonding)")
        self.assertEqual(r["company"], "신카와, ASM PT")
        self.assertEqual(r["export_yoy"], 12.0)
        self.assertEqual(r["export_mom"], -12.0)
        self.assertEqual(r["price_yoy"], 33.0)
        self.assertEqual(r["price_mom"], -0.5)

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

    def test_history_accumulates_dashboard_shows_latest(self):
        # 한국처럼 월별 누적: 과거+최신 둘 다 저장, 대시보드는 최신월만 표시.
        c = self._conn()
        older = _FULL.replace("2026-05", "2026-04").replace("27.6십억", "99.9십억")
        self.assertTrue(jp.ingest(c, older, source_message_id=1))   # 2026-04 저장
        self.assertTrue(jp.ingest(c, _FULL, source_message_id=2))   # 2026-05 저장(누적)
        rows = jp.list_jp(c)
        self.assertEqual(len(rows), 1)                              # 품목별 최신 1행
        self.assertEqual(rows[0]["latest_month"], "2026-05")        # 최신월
        self.assertEqual(rows[0]["export_value_bn"], 27.6)
        hist = jp.history(c, "다이싱/어셈블리 (DISCO)")
        self.assertEqual([h["latest_month"] for h in hist], ["2026-04", "2026-05"])

    def test_same_item_newer_updates(self):
        c = self._conn()
        jp.ingest(c, _FULL, source_message_id=1)
        newer = _FULL.replace("2026-05", "2026-06").replace("27.6십억", "30.0십억")
        self.assertTrue(jp.ingest(c, newer, source_message_id=2))
        rows = jp.list_jp(c)
        self.assertEqual(len(rows), 1)                              # 대시보드 = 최신 1행
        self.assertEqual(rows[0]["export_value_bn"], 30.0)
        self.assertEqual(len(jp.history(c, "다이싱/어셈블리 (DISCO)")), 2)  # 이력 2개월

    def test_no_month_partial_does_not_clobber(self):
        # 월 없는 truncated 재포워드는 (item,'') 버킷으로 → 실월(2026-05) 행 무손상.
        c = self._conn()
        jp.ingest(c, _FULL, source_message_id=1,
                  media_paths=["media/2026-05/x.jpg"])
        partial = ("일본 수출 데이터 업데이트: 다이싱/어셈블리 (DISCO)\n"
                   "수출액: 0.1십억 엔\n")  # 월·단가·차트 없음
        jp.ingest(c, partial, source_message_id=9)
        r = jp.list_jp(c)[0]                              # MAX(month) → 2026-05
        self.assertEqual(r["latest_month"], "2026-05")
        self.assertEqual(r["export_value_bn"], 27.6)      # 0.1 로 안 덮임
        self.assertEqual(r["chart_media"], "media/2026-05/x.jpg")  # 차트 보존

    def test_same_month_merge_preserves_chart(self):
        # 같은 달 재전송이 차트 없이 와도 기존 차트 유지(필드 보존 병합).
        c = self._conn()
        jp.ingest(c, _FULL, source_message_id=1,
                  media_paths=["media/2026-05/x.jpg"])
        # 같은 달, 차트 없는 재전송(가격만 갱신)
        resend = _FULL.replace("19.3천엔", "20.0천엔")
        self.assertTrue(jp.ingest(c, resend, source_message_id=2, media_paths=[]))
        r = jp.list_jp(c)[0]
        self.assertEqual(r["price_per_kg"], 20.0)                 # 갱신
        self.assertEqual(r["chart_media"], "media/2026-05/x.jpg")  # 차트 보존

    def test_migration_from_old_single_pk_preserves_rows(self):
        import sqlite3
        d = Path(tempfile.mkdtemp())
        p = d / "jp.db"
        # 옛 단일 PK 스키마 + 1행 시드
        c0 = sqlite3.connect(str(p))
        c0.executescript(
            "CREATE TABLE jp_exports (item TEXT PRIMARY KEY, latest_month TEXT, "
            "company TEXT, export_value_bn REAL, export_yoy REAL, export_mom REAL, "
            "price_per_kg REAL, price_yoy REAL, price_mom REAL, chart_media TEXT, "
            "source_message_id INTEGER, posted_at TEXT, raw_text TEXT, updated_at TEXT);")
        c0.execute("INSERT INTO jp_exports(item,latest_month,export_value_bn) "
                   "VALUES('본딩 기기 (Bonding)','2026-05',2.5)")
        c0.commit(); c0.close()
        # open_jp_db 가 마이그레이션 → 행 보존 + 새 (item,month) 누적 가능
        c = jp.open_jp_db(p)
        rows = jp.list_jp(c)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["latest_month"], "2026-05")
        # 새 월 추가가 누적되는지(새 PK 적용 확인)
        c.execute("INSERT INTO jp_exports(item,latest_month,export_value_bn) "
                  "VALUES('본딩 기기 (Bonding)','2026-06',3.0)")
        self.assertEqual(len(jp.history(c, "본딩 기기 (Bonding)")), 2)

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
