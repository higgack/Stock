"""말레이시아 수출(종목별) 파서 회귀 — 나쁜양파.

2026-08-20 사용자: "말레이시아 수출 7월이 떴는데 업종+기업이 아니라 기업으로
나와서 수집이 안된것 같아." 품목 파서(`my_exports`)의 마커는 `N월 수출
말레이시아` 인데 이 포맷은 `말레이시아 수출`(어순 반대)이라 관련성 필터를
통과 못 하고 **조용히 드랍**되고 있었다 — 일본이 2026-08-16 에 겪은 것과
같은 사고.

⚠️ 아래 픽스처는 사용자가 첨부한 **렌더된 스크린샷** 기반이다(jp_stock 처럼
Telethon 실측 원문이 아님). 그래서 파서는 헤더 줄 구성을 단정하지 않고
관용 파싱하며, 여기서도 세 줄/한 줄 두 변형을 모두 고정한다.
"""

import tempfile
import unittest
from pathlib import Path

from trade import my_stock_exports as mys

# 사용자 2026-08-20 캡처(TXN) — 이 소스에만 있는 수준값 2종 + 관련 매출.
_TXN = (
    "Texas Instruments Incorporated (TXN)\n말레이시아 수출\n26년 7월 Update\n\n"
    "수출액 YoY: +170.4%\n3M 수출액 YoY: +127.1%\n\n"
    "동시상관: 0.93\n방향 일치율: 67%\n\n"
    "- CY26Q2 매출 $5.46B(+22.8% YoY)\n\n"
    "https://badonion.co.kr/trade/mapping")


class ParseTests(unittest.TestCase):
    def test_screenshot_caption(self):
        d = mys.parse_my_stock_export(_TXN)
        self.assertEqual(d["ticker"], "TXN")
        self.assertEqual(d["stock_name"], "Texas Instruments Incorporated")
        self.assertEqual(d["month"], "2026-07")
        self.assertEqual(d["export_yoy"], 170.4)
        self.assertEqual(d["export_yoy_3m"], 127.1)
        self.assertEqual(d["corr"], 0.93)
        self.assertEqual(d["dir_hit"], 67.0)
        self.assertEqual(d["revenue"], "CY26Q2 매출 $5.46B(+22.8% YoY)")
        # 매출 줄이 품목 설명으로 승격되면 안 된다(카드 📦 슬롯 오염).
        self.assertIsNone(d["item"])

    def test_revenue_line_without_dash_is_not_promoted_to_item(self):
        # `_RE_REVENUE` 는 선행 `-/•/*` 를 요구하므로 대시 없는 매출 줄은
        # revenue 로 안 잡힌다. 이때 `_SKIP_LINE` 의 `매출` 이 없으면 그 줄이
        # **품목 설명(📦)으로 승격**돼 카드가 엉뚱한 걸 보여준다.
        # ⚠️ 픽스처를 고를 때 **다른 키워드에 가리지 않게** 해야 한다 —
        # 대시가 있으면 `^[-•*]` 가, YoY 가 있으면 `YoY` 가 먼저 잡아서
        # `매출` 을 지워도 테스트가 통과했다(뮤테이션 X3 가 두 번 통과).
        # 셋 다 없는 조합이라야 이 키워드가 실제로 일한다.
        d = mys.parse_my_stock_export(
            "A (AAA)\n말레이시아 수출\n26년 7월\n\n수출액 YoY: +1.0%\n\n"
            "CY26Q2 매출 $5.46B")
        self.assertIsNone(d["item"], "매출 줄이 품목으로 승격됐다")
        self.assertIsNone(d["revenue"])

    def test_bold_markdown_does_not_break_header(self):
        d = mys.parse_my_stock_export(
            "**Texas Instruments Incorporated (TXN)**\n**말레이시아 수출**\n"
            "**26년 7월 Update**\n\n수출액 YoY: +170.4%")
        self.assertEqual((d["ticker"], d["month"]), ("TXN", "2026-07"))

    def test_one_line_header_variant_also_accepted(self):
        # 원문 마크다운 미확인이라 일본식 한 줄 헤더도 받는다(관용 파싱).
        d = mys.parse_my_stock_export(
            "Foo Corp (5347) 말레이시아 수출 Update\n26년 7월\n\n수출액 YoY: +5.0%")
        self.assertEqual((d["ticker"], d["stock_name"], d["month"]),
                         ("5347", "Foo Corp", "2026-07"))

    def test_3m_prefix_is_not_confused_with_monthly(self):
        d = mys.parse_my_stock_export(
            "A (AAA)\n말레이시아 수출\n26년 7월\n\n"
            "3M 수출액 YoY: +127.1%\n수출액 YoY: +170.4%")
        self.assertEqual(d["export_yoy"], 170.4)
        self.assertEqual(d["export_yoy_3m"], 127.1)

    def test_missing_metric_is_none_not_zero(self):
        d = mys.parse_my_stock_export(
            "A (AAA)\n말레이시아 수출\n26년 7월\n\n수출액 YoY: +1.0%")
        for k in ("export_yoy_3m", "price_yoy", "corr", "dir_hit", "revenue"):
            self.assertIsNone(d[k], k)

    def test_header_must_be_one_line_per_part(self):
        # `\s` 가 개행을 먹으면 무관한 조합이 통과한다(jp_stock 실측 함정).
        self.assertIsNone(mys.parse_my_stock_export(
            "어떤회사\n(ABCD) 말레이시아 수출\n26년 7월"))

    def test_scattered_markers_are_rejected(self):
        self.assertIsNone(mys.parse_my_stock_export(
            "말레이시아 수출 관련 잡담\n\n26년 7월에 좋았다"))

    def test_invalid_month_rejected(self):
        self.assertIsNone(mys.parse_my_stock_export(
            "A (AAA)\n말레이시아 수출\n26년 13월\n\n수출액 YoY: +1.0%"))

    def test_metrics_do_not_leak_across_companies(self):
        # 한 메시지에 두 회사가 담기면 A 카드에 B 값이 섞이면 안 된다.
        d = mys.parse_my_stock_export(
            "A (AAA)\n말레이시아 수출\n26년 7월\n\n수출액 YoY: +1.0%\n\n"
            "B (BBB)\n말레이시아 수출\n26년 7월\n\n수출액 YoY: +9.9%\n동시상관: 0.11")
        self.assertEqual(d["ticker"], "AAA")
        self.assertEqual(d["export_yoy"], 1.0)
        self.assertIsNone(d["corr"], "B 의 동시상관이 A 카드로 샜다")

    def test_does_not_claim_other_sources(self):
        from trade import badonion_sources as srcs
        for other in (
            "6월 수출 말레이시아\n\n▶️ 액화천연가스\n\n"
            "26년06월: $1.0M  (+1.0% YoY)  (+1.0% MoM)",
            "HPSP (403870)\n한국 수출\n26년 7월 Update\n\n수출액 YoY: +260.2%",
            "Kioxia (285A) 일본 수출 Update\n26년 6월\n\n수출액: YoY +95.1%",
        ):
            self.assertIsNone(mys.parse_my_stock_export(other), other[:30])
        # 반대로 우리 캡션을 남이 주장하면 안 된다
        hits = [s.key for s in srcs.SOURCES if s.parse(_TXN) is not None]
        self.assertEqual(hits, ["mys"], hits)


class StoreTests(unittest.TestCase):
    def _conn(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        c = mys.open_my_stock_db(Path(tmp.name) / "my_stock.db")
        self.addCleanup(c.close)
        return c

    def test_monthly_rolling_replaces_card_and_keeps_history(self):
        c = self._conn()
        for mo, v in (("26년 7월", 170.4), ("26년 8월", 12.0)):
            mys.ingest(c, f"A (AAA)\n말레이시아 수출\n{mo}\n\n수출액 YoY: +{v}%",
                       source_message_id=1, posted_at="", media_paths=None)
        cards = mys.list_my_stock(c)
        self.assertEqual([(r["ticker"], r["month"]) for r in cards],
                         [("AAA", "2026-08")], "최신월 1장으로 교체되어야")
        self.assertEqual(len(mys.history(c, "AAA")), 2, "히스토리는 보존")

    def test_partial_resend_does_not_null_existing_fields(self):
        c = self._conn()
        mys.ingest(c, _TXN, source_message_id=1, posted_at="", media_paths=None)
        mys.ingest(c, "Texas Instruments Incorporated (TXN)\n말레이시아 수출\n"
                      "26년 7월\n\n수출액 YoY: +171.0%",
                   source_message_id=2, posted_at="", media_paths=None)
        r = mys.list_my_stock(c)[0]
        self.assertEqual(r["export_yoy"], 171.0, "새 값은 반영")
        self.assertEqual(r["corr"], 0.93, "빠진 필드는 기존 값 보존")
        self.assertEqual(r["revenue"], "CY26Q2 매출 $5.46B(+22.8% YoY)")

    def test_render_html_works_empty_and_populated(self):
        c = self._conn()
        empty = mys.render_html(c)
        self.assertIn("아직 수집된", empty)          # nav 404 방지용 빈 페이지
        mys.ingest(c, _TXN, source_message_id=1, posted_at="", media_paths=None)
        html = mys.render_html(c)
        self.assertIn("Texas Instruments Incorporated", html)
        self.assertIn("TXN", html)
        # 수준값은 부호·화살표 없이(실수 #39) — '+0.93 ▲' 로 그리면 안 된다.
        self.assertIn("0.93", html)
        self.assertNotIn("+0.93", html)
        self.assertIn("67%", html)
        self.assertNotIn("+67.0%", html)
        # 변화율은 부호·화살표 유지
        self.assertIn("▲+170.4%", html)
        # 관련 매출은 원문 그대로
        self.assertIn("CY26Q2 매출 $5.46B(+22.8% YoY)", html)


# 2026-08-20 VM 실측 원문 6건 — 소스가 **두 계열**을 쓴다는 게 여기서 드러났다.
# 배포 직후 프로브로 원문을 뽑아 보고서야 알았다(스크린샷 1장만 보고 만든
# 첫 파서는 동시 계열만 알고 있었다).
_LEAD_CAP = (
    "Analog Devices (ADI)\n말레이시아 수출\n26년 7월 Update\n\n"
    "수출액 YoY: +67.8%\n3M 수출액 YoY: +74.1%\n\n"
    "1Q 선행상관: 0.71\n선행 방향 일치율: 83%\n\n"
    "- CY26Q2 매출 $3.62B(+37.2% YoY)\n\nhttps://badonion.co.kr/trade/mapping"
)
_COIN_CAP = (
    "Advanced Micro Devices, Inc. (AMD)\n말레이시아 수출\n26년 7월 Update\n\n"
    "수출액 YoY: +170.4%\n3M 수출액 YoY: +127.1%\n\n"
    "동시상관: 0.67\n방향 일치율: 75%\n\n"
    "- CY26Q2 매출 $11.54B(+50.1% YoY)\n\nhttps://badonion.co.kr/trade/mapping"
)


class LeadVsCoincidentTests20260820(unittest.TestCase):
    """동시상관/방향일치율 과 1Q 선행상관/선행 방향일치율 은 **다른 지표**다.

    첫 배포판은 `동시상관` 만 잡고 `방향\\s*일치율` 은 부분매칭이라, 선행 계열
    캡션에서 상관은 통째로 유실되고 선행 방향일치율이 동시 칸에 조용히 담겼다
    (실수 #34). 그 줄이 _SKIP_LINE 에도 없어 품목 설명으로까지 샜다.
    """

    def test_lead_series_lands_in_lead_columns(self):
        r = mys.parse_my_stock_export(_LEAD_CAP)
        self.assertEqual(r["lead_corr"], 0.71)
        self.assertEqual(r["lead_dir_hit"], 83.0)
        # 동시 칸은 **비어 있어야** 한다 — 빈칸이 틀린 값보다 낫다.
        self.assertIsNone(r["corr"], "선행상관이 동시 칸에 들어감")
        self.assertIsNone(r["dir_hit"], "선행 방향일치율이 동시 칸에 들어감")

    def test_coincident_series_lands_in_coincident_columns(self):
        r = mys.parse_my_stock_export(_COIN_CAP)
        self.assertEqual(r["corr"], 0.67)
        self.assertEqual(r["dir_hit"], 75.0)
        self.assertIsNone(r["lead_corr"])
        self.assertIsNone(r["lead_dir_hit"])

    def test_metric_line_does_not_leak_into_item(self):
        """`1Q 선행상관: 0.71` 이 품목 설명으로 렌더되던 버그(카드 3/6)."""
        for cap in (_LEAD_CAP, _COIN_CAP):
            self.assertIsNone(mys.parse_my_stock_export(cap)["item"], cap[:20])

    def test_bare_correlation_is_not_guessed(self):
        """접두 없는 맨 `상관` 은 어느 계열인지 모르므로 받지 않는다."""
        cap = _COIN_CAP.replace("동시상관: 0.67", "상관: 0.67")
        r = mys.parse_my_stock_export(cap)
        self.assertIsNone(r["corr"])
        self.assertIsNone(r["lead_corr"])

    def test_page_guide_explains_both_series(self):
        """카드에 두 계열이 나오는데 안내문이 한쪽만 설명하면 사용자는 왜
        칸이 다른지 모른다(설명 out-of-sync = 버그)."""
        self.assertIn("선행", mys._SUB)
        self.assertIn("동시", mys._SUB)
        self.assertIn("비교할 수", mys._SUB)

    def test_card_labels_distinguish_the_two_series(self):
        row = dict(ticker="ADI", stock_name="Analog Devices", month="2026-07",
                   lead_corr=0.71, lead_dir_hit=83.0)
        html = mys._card_html(row, [], "../media/")
        self.assertIn("선행상관", html)
        self.assertIn("선행 방향 일치율", html)
        # 선행 전용 행에 '동시상관' 라벨이 붙으면 안 된다.
        self.assertNotIn("동시상관", html)


class ParseVersionMigrationTests20260820(unittest.TestCase):
    """옛 파서로 **구운** 값은 코드를 고쳐도 안 바뀐다(실수 #18·#21b).

    이 모듈의 upsert 는 필드 보존 병합이라 새 파싱이 None 을 주면 옛 값을
    도로 살린다 — 정확히 선행 3종이 그 상태였다(dir_hit=83, item='1Q
    선행상관: 0.71'). parse_ver 가 낮으면 파생 필드는 새 파싱만 쓴다.
    """

    _V1_SCHEMA = """CREATE TABLE my_stock_exports (
      ticker TEXT NOT NULL, month TEXT NOT NULL DEFAULT '', stock_name TEXT,
      item TEXT, export_yoy REAL, export_yoy_3m REAL, price_yoy REAL,
      corr REAL, dir_hit REAL, revenue TEXT, note TEXT, chart_media TEXT,
      source_message_id INTEGER, posted_at TEXT, raw_text TEXT,
      updated_at TEXT, PRIMARY KEY (ticker, month));"""

    def _v1_db(self, path):
        import sqlite3
        c = sqlite3.connect(path)
        c.executescript(self._V1_SCHEMA)
        c.execute(
            "INSERT INTO my_stock_exports (ticker,month,stock_name,item,"
            "export_yoy,export_yoy_3m,corr,dir_hit,chart_media) VALUES "
            "('ADI','2026-07','Analog Devices','1Q 선행상관: 0.71',67.8,74.1,"
            "NULL,83.0,'2026-08-20/adi.jpg')")
        c.commit()
        c.close()

    def test_stale_row_is_recorrected_and_media_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "my_stock.db"
            self._v1_db(db)
            conn = mys.open_my_stock_db(db)      # ALTER TABLE 마이그레이션
            mys.ingest(conn, _LEAD_CAP, source_message_id=1,
                       posted_at="2026-08-20T00:00:00Z", media_paths=[])
            r = dict(conn.execute("SELECT * FROM my_stock_exports").fetchone())
            self.assertIsNone(r["item"], "품목 칸에 지표가 남음")
            self.assertIsNone(r["dir_hit"], "동시 칸에 선행값이 남음")
            self.assertEqual(r["lead_corr"], 0.71)
            self.assertEqual(r["lead_dir_hit"], 83.0)
            self.assertEqual(r["parse_ver"], mys._PARSE_VER)
            # 파싱 산물이 **아닌** 필드는 지우면 안 된다.
            self.assertEqual(r["chart_media"], "2026-08-20/adi.jpg")

    def test_migration_adds_columns_to_existing_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "my_stock.db"
            self._v1_db(db)
            conn = mys.open_my_stock_db(db)
            cols = {r["name"] for r in
                    conn.execute("PRAGMA table_info(my_stock_exports)")}
            for c in ("lead_corr", "lead_dir_hit", "parse_ver"):
                self.assertIn(c, cols, "기존 DB 에 컬럼이 안 붙음 — 첫 쓰기가 터진다")

    def test_history_table_columns_follow_the_series_present(self):
        """두 계열을 한 열에 합치면 세로로 읽는 자리에서 정의가 갈린다(#32)."""
        lead = [dict(month="2026-06", export_yoy=1.0, lead_corr=0.7,
                     lead_dir_hit=80.0),
                dict(month="2026-07", export_yoy=2.0, lead_corr=0.71,
                     lead_dir_hit=83.0)]
        html = mys._hist_table(lead)
        self.assertIn("<th>선행상관</th>", html)
        self.assertNotIn("<th>동시상관</th>", html)
        coin = [dict(month="2026-06", export_yoy=1.0, corr=0.6, dir_hit=70.0),
                dict(month="2026-07", export_yoy=2.0, corr=0.67, dir_hit=75.0)]
        html2 = mys._hist_table(coin)
        self.assertIn("<th>동시상관</th>", html2)
        self.assertNotIn("<th>선행상관</th>", html2)


if __name__ == "__main__":
    unittest.main()
