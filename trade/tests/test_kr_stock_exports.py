import re
import tempfile
import unittest
from pathlib import Path

from trade import kr_stock_exports as krs


_JUL = (
    "HPSP (403870)\n"
    "한국 수출\n"
    "26년 7월 Update\n\n"
    "단가 YoY: -6.4%\n"
    "수출액 YoY: +260.2%\n"
    "3M 수출액 YoY: +103.8%\n\n"
    "선행상관: 0.70\n"
    "선행 방향 일치율: 80%\n\n"
    "- CY26Q2 매출 ₩29.9B(-20.6% YoY)"
)

# 다음 달 = 새 메시지. rolling 검증용.
_AUG = (
    "HPSP (403870)\n"
    "한국 수출\n"
    "26년 8월 Update\n\n"
    "단가 YoY: -3.1%\n"
    "수출액 YoY: +180.5%\n"
    "3M 수출액 YoY: +95.2%\n\n"
    "선행상관: 0.72\n"
    "선행 방향 일치율: 85%\n\n"
    "- CY26Q2 매출 ₩29.9B(-20.6% YoY)"
)


class ParseTests(unittest.TestCase):
    def test_parse_full(self):
        p = krs.parse_kr_stock_export(_JUL)
        self.assertIsNotNone(p)
        self.assertEqual(p["stock_code"], "403870")
        self.assertEqual(p["stock_name"], "HPSP")
        self.assertEqual(p["month"], "2026-07")
        self.assertEqual(p["price_yoy"], -6.4)
        self.assertEqual(p["export_yoy"], 260.2)
        self.assertEqual(p["export_yoy_3m"], 103.8)
        self.assertEqual(p["lead_corr"], 0.70)
        self.assertEqual(p["lead_dir_hit"], 80.0)
        self.assertEqual(p["rev_quarter"], "CY26Q2")
        self.assertEqual(p["rev_value_krw_b"], 29.9)
        self.assertEqual(p["rev_yoy"], -20.6)

    def test_3m_does_not_leak_into_plain_export_yoy(self):
        # '3M 수출액 YoY' 가 '수출액 YoY' 정규식에 먼저 걸리면 두 값이 같아진다.
        p = krs.parse_kr_stock_export(_JUL)
        self.assertNotEqual(p["export_yoy"], p["export_yoy_3m"])

    def test_rejects_unrelated_captions(self):
        # 나쁜양파는 무관 콘텐츠가 섞인 일반 채널 — 마커 3종을 모두 요구한다.
        for bad in (
            "",
            None,
            "▶️ 반도체 제조장비\n7월 수입 미국\n26년07월: $18.8M (+1.0% YoY) (+1.0% MoM)",
            "HPSP (403870)\n26년 7월 Update",     # '한국 수출' 없음
            "한국 수출\n26년 7월 Update",           # 종목 헤더 없음
            "HPSP (403870)\n한국 수출",             # 월 없음
            "HPSP (40387)\n한국 수출\n26년 7월 Update",   # 5자리 = 종목코드 아님
            "HPSP (403870)\n한국 수출\n26년 13월 Update",  # 잘못된 월
        ):
            self.assertIsNone(krs.parse_kr_stock_export(bad), repr(bad))

    def test_latest_quarter_wins_when_several_listed(self):
        # 첫 줄을 그냥 쓰면 옛 분기가 카드에 뜬다(2026-08-16 독립 리뷰).
        p = krs.parse_kr_stock_export(
            _JUL + "\n- CY25Q4 매출 ₩20.0B(+5.0% YoY)"
                   "\n- CY26Q1 매출 ₩25.0B(+1.0% YoY)")
        self.assertEqual(p["rev_quarter"], "CY26Q2")
        self.assertEqual(p["rev_value_krw_b"], 29.9)

    def test_3m_without_space_does_not_leak(self):
        # 옛 룩비하인드는 공백을 요구해 '3M수출액' 을 못 막았다.
        p = krs.parse_kr_stock_export(_JUL.replace("3M 수출액", "3M수출액"))
        self.assertEqual(p["export_yoy"], 260.2)
        self.assertEqual(p["export_yoy_3m"], 103.8)

    def test_scattered_markers_are_rejected(self):
        # 마커 3종을 따로 찾으면 무관 메시지가 통과하는데, 이 파서는
        # unstored_check·dashboard 의 **억제 필터**로도 쓰여서 오탐이 곧
        # '저장도 안 되고 미매칭 알림에도 안 잡히는' 조용한 유실이 된다.
        self.assertIsNone(krs.parse_kr_stock_export(
            "▶️ 반도체 소재\n7월 수출 대만\n\n관련: 삼성전자 (005930)\n"
            "참고: 한국 수출 비중 높음\n\n작년 26년 7월 Update 참조"))
        self.assertIsNone(krs.parse_kr_stock_export(
            "HPSP (403870)\n어떤 다른 줄\n한국 수출\n26년 7월 Update"))

    def test_blank_lines_inside_header_block_are_tolerated(self):
        p = krs.parse_kr_stock_export(_JUL.replace("한국 수출\n", "한국 수출\n\n"))
        self.assertIsNotNone(p)
        self.assertEqual(p["stock_code"], "403870")

    def test_optional_fields_missing_is_graceful(self):
        # 포맷이 조금 바뀌어도 메시지 전체를 버리지 않는다.
        p = krs.parse_kr_stock_export(
            "삼성전자 (005930)\n한국 수출\n26년 8월 Update\n\n수출액 YoY: +12.0%")
        self.assertIsNotNone(p)
        self.assertEqual(p["export_yoy"], 12.0)
        self.assertIsNone(p["price_yoy"])
        self.assertIsNone(p["rev_quarter"])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "kr_stock.db"
        self.conn = None

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()
        self.tmp.cleanup()

    def test_ingest_and_rolling_to_next_month(self):
        self.conn = krs.open_kr_stock_db(self.db_path)
        self.assertTrue(krs.ingest(self.conn, _JUL, source_message_id=1,
                                   posted_at="t"))
        latest = krs.list_kr_stock(self.conn)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["month"], "2026-07")
        self.assertEqual(latest[0]["export_yoy"], 260.2)

        # 8월분이 오면 카드가 그것으로 교체되고 7월은 히스토리에 남는다.
        self.assertTrue(krs.ingest(self.conn, _AUG, source_message_id=2,
                                   posted_at="t2"))
        latest = krs.list_kr_stock(self.conn)
        self.assertEqual(len(latest), 1, "종목당 최신월 1행이어야(중복 카드 금지)")
        self.assertEqual(latest[0]["month"], "2026-08")
        self.assertEqual(latest[0]["export_yoy"], 180.5)
        hist = krs.history(self.conn, "403870")
        self.assertEqual([h["month"] for h in hist], ["2026-07", "2026-08"])

    def test_reingest_preserves_existing_fields(self):
        # 부분 재전송이 기존 good 필드를 null 로 덮지 않아야 한다.
        self.conn = krs.open_kr_stock_db(self.db_path)
        krs.ingest(self.conn, _JUL, source_message_id=1, posted_at="t",
                   media_paths=["2026-08-01/a.jpg"])
        krs.ingest(self.conn,
                   "HPSP (403870)\n한국 수출\n26년 7월 Update\n\n수출액 YoY: +9.9%",
                   source_message_id=1, posted_at="t")
        row = krs.list_kr_stock(self.conn)[0]
        self.assertEqual(row["export_yoy"], 9.9)          # 새 값은 반영
        self.assertEqual(row["price_yoy"], -6.4)          # 빠진 값은 보존
        self.assertEqual(row["chart_media"], "2026-08-01/a.jpg")

    def test_two_stocks_are_independent(self):
        self.conn = krs.open_kr_stock_db(self.db_path)
        krs.ingest(self.conn, _JUL, source_message_id=1, posted_at="t")
        krs.ingest(self.conn,
                   "삼성전자 (005930)\n한국 수출\n26년 7월 Update\n\n수출액 YoY: +5.0%",
                   source_message_id=2, posted_at="t")
        self.assertEqual(len(krs.list_kr_stock(self.conn)), 2)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "kr_stock.db"
        self.conn = None

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()
        self.tmp.cleanup()

    def test_render_and_regenerate(self):
        self.conn = krs.open_kr_stock_db(self.db_path)
        krs.ingest(self.conn, _JUL, source_message_id=1, posted_at="t",
                   media_paths=["2026-08-01/abc.jpg"])
        html = krs.render_html(self.conn)
        self.assertIn("한국 수출 데이터", html)
        self.assertIn("HPSP", html)
        self.assertIn("403870", html)
        self.assertIn("2026-08-01/abc.jpg", html)
        self.assertIn("CY26Q2", html)
        self.conn.close()
        self.conn = None
        out = Path(self.tmp.name) / "kr_stock.html"
        krs.regenerate(self.db_path, out)
        self.assertTrue(out.exists())

    def test_empty_db_still_renders_page(self):
        # nav 링크 404 방지 — 데이터가 없어도 페이지는 나와야 한다.
        self.conn = krs.open_kr_stock_db(self.db_path)
        html = krs.render_html(self.conn)
        self.assertIn("아직 수집된", html)
        # ⚠️ 계약 반전 — 옛 테스트는 `index.html` **존재**를 단언해 버그를
        # 고정하고 있었다. NOAH 프록시가 `/trade/index.html` 을 종목분석
        # 메인으로 302 시키므로(_OUR_ROOT_PAGES) 그 링크는 수출입 대시보드로
        # 가지 않는다(사용자 2026-08-16). 올바른 형태는 './'.
        self.assertIn('href="./"', html)
        self.assertNotIn("index.html", html)


class RoutingTests(unittest.TestCase):
    """ingest_inbox 는 순차 fallback 이라 **선행 파서가 한국 캡션을 먼저
    낚아채면** 조용히 엉뚱한 DB 로 들어간다. 양방향으로 고정한다."""

    def test_no_earlier_parser_claims_kr_caption(self):
        from trade import cn_exports as cn
        from trade import jp2_exports as jp2
        from trade import jp_exports as jp
        from trade import mx_exports as mx
        from trade import my_exports as my
        from trade import ph_exports as ph
        from trade import th_exports as th
        from trade import tw_exports as tw
        from trade import us_imports as us
        for name, fn in (("JP", jp.parse_jp_export), ("TW", tw.parse_tw_export),
                         ("CN", cn.parse_cn_export), ("JP2", jp2.parse_jp2_export),
                         ("TH", th.parse_th_export), ("MY", my.parse_my_export),
                         ("PH", ph.parse_ph_export), ("MX", mx.parse_mx_export),
                         ("US", us.parse_us_import)):
            self.assertIsNone(fn(_JUL), f"{name} 파서가 한국 캡션을 먼저 소비")

    def test_kr_parser_does_not_claim_other_captions(self):
        for cap in (
            "🇺🇸 6월 수입 미국\n\n▶️ 테스트 품목\n\n26년06월: $12.3M  (+7.0% YoY)  (+1.5% MoM)",
            "🌮 6월 수출 멕시코\n\n▶️ 테스트\n\n26년06월: $9.1M  (+3.0% YoY)  (+1.0% MoM)",
            "6월 수출 대만\n\n▶️ 테스트\n\n26년06월: $9.1M  (+3.0% YoY)  (+1.0% MoM)",
        ):
            self.assertIsNone(krs.parse_kr_stock_export(cap))


class WiringTests(unittest.TestCase):
    """파이프라인 배선 — 이게 빠지면 메시지가 조용히 드랍된다.

    2026-08-16 레지스트리 통합 이후, 소비처마다 하드코딩을 grep 하는 대신
    **레지스트리 등재**를 검증한다(등재만 되면 리스너·백필·ingest·
    unstored_check·dashboard 5곳이 자동으로 따라온다). 그 5곳이 실제로
    레지스트리를 보는지는 `test_badonion_sources.py` 가 고정한다."""

    def test_registered_in_badonion_registry(self):
        # 레지스트리에 없으면 리스너 필터를 못 통과해 forward 자체가 안 되고,
        # 저장 로직이 아무리 맞아도 데이터가 0건이 된다.
        from trade import badonion_sources as bsrc
        s = bsrc.by_key("krs")
        self.assertIsNotNone(s, "한국 수출(종목별)이 레지스트리에 미등재")
        # ⚠️ 2026-09-16 계약 변경(#370·#222): 한국 수출은 **문법이 둘**이 됐다
        # (옛 지표판 + 새 금액판). 레지스트리 필터는 그래서 `parse_any` 이고,
        # 남는 보장은 "옛 판 캡션을 여전히 이 소스가 받는다" 이다.
        self.assertIs(s.parse, krs.parse_any)
        self.assertIsNotNone(s.parse(_JUL), "옛 지표판을 더 이상 안 받는다")
        self.assertIs(s.open_db, krs.open_kr_stock_db)
        self.assertIs(s.ingest, krs.ingest)
        self.assertEqual(s.db_file, "kr_stock.db")
        self.assertEqual(s.html_file, "kr_stock.html")
        self.assertTrue(bsrc.is_relevant(_JUL), "필터가 한국 캡션을 거부")

    def test_dashboard_nav_and_regenerate(self):
        # nav 링크가 index.html 에 실제로 들어가는지 검증하는 테스트가
        # 기존엔 없어서 </div> 위치를 잘못 건드려도 못 잡았다.
        src = Path("trade/dashboard.py").read_text(encoding="utf-8")
        # nav 링크는 이제 dashboard 소스가 아니라 레지스트리가 만든다 —
        # **렌더 결과**를 본다(소스 grep 보다 강한 검증).
        from trade.dashboard import _srcs_nav_html
        self.assertIn('href="kr_stock.html"', _srcs_nav_html())
        # regenerate 는 더 이상 dashboard 에 하드코딩돼 있지 않다 —
        # badonion_sources 레지스트리가 돌린다. 그래서 grep 이 아니라
        # **레지스트리 엔트리**를 검증한다(2026-08-16 레지스트리 통합).
        from trade import badonion_sources as _srcs
        from trade import kr_stock_exports as _krs
        s = _srcs.by_key("krs")
        self.assertIsNotNone(s, "krs 소스가 레지스트리에서 사라짐")
        self.assertEqual(s.html_file, "kr_stock.html")
        self.assertIs(s.regenerate, _krs.regenerate)
        # 렌더 실패를 조용히 삼키면 nav 가 404 인데 단서가 0 이다(실수 #12)
        blk = src.split("for _s in _srcs.SOURCES:", 1)[1][:800]
        self.assertIn("logging.getLogger", blk, "regenerate 실패가 무로그")
        # 들여쓰기를 리터럴로 박으면 어떤 코드에도 안 맞는 무력한 assert 가
        # 된다 — 핸들러 본문이 pass 뿐인 **형태**를 정규식으로 잡는다.
        self.assertIsNone(re.search(r"except[^\n]*:\s*\n\s*pass", blk),
                          "regenerate 실패를 조용히 삼킴(실수 #12)")


if __name__ == "__main__":
    unittest.main()


class Nov2026LayoutTests(unittest.TestCase):
    """2026-11-12 사용자 캡처 — `A` 접두 코드 · 맨 `한국` · `무역액 YoY`.
    옛 헤더가 이 셋을 못 받아 관련성 필터에서 드랍됐다."""

    SDI = ("Samsung SDI Co., Ltd. (A006400)\n한국\n26년 10월 Update\n\n"
           "무역액 YoY: +128.9%\n\n상관: 0.85\n방향 일치율: 92%\n\n"
           "https://badonion.co.kr/trade/mapping")
    VINA = ("비나텍 (A126340)\n한국\n26년 10월 Update\n\n단가 YoY: +35.0%\n"
            "무역액 YoY: +61.6%\n\n선행상관: 0.89\n선행 방향 일치율: 75%\n\n"
            "* BE보다 주가 좋음\n\nhttps://badonion.co.kr/trade/mapping")

    def test_parses_both_captions(self):
        # SDI 는 차트 제목이 `Korea Import Basket` — 운영자 표가 수입으로 확인해
        # 수출 파서는 안 받고 수입 방향으로만 읽힌다(사용자 2026-11-12).
        self.assertIsNone(krs.parse_kr_stock_export(self.SDI))
        a = krs.parse_kr_stock_export(self.SDI, direction="수입")
        self.assertEqual((a["stock_code"], a["month"], a["trade_yoy"], a["corr"],
                          a["dir_hit"]), ("006400", "2026-10", 128.9, 0.85, 92.0))
        # 무역액은 수출액 칸에 들어가지 않는다(원문이 방향을 안 밝혔다).
        self.assertIsNone(a["export_yoy"])
        b = krs.parse_kr_stock_export(self.VINA)
        self.assertEqual((b["stock_code"], b["price_yoy"], b["trade_yoy"],
                          b["lead_corr"], b["lead_dir_hit"], b["note"]),
                         ("126340", 35.0, 61.6, 0.89, 75.0, "BE보다 주가 좋음"))

    def test_revenue_line_is_not_a_note(self):
        self.assertIsNone(krs.parse_kr_stock_export(_JUL)["note"])

    def test_import_header_is_still_rejected(self):
        self.assertIsNone(krs.parse_kr_stock_export(
            self.SDI.replace("\n한국\n", "\n한국 수입\n")))

    def test_card_shows_trade_yoy_and_note(self):
        with tempfile.TemporaryDirectory() as td:
            conn = krs.open_kr_stock_db(Path(td) / "k.db")
            for cap in (self.SDI.replace("A006400", "A005930"), self.VINA):
                self.assertTrue(krs.ingest(conn, cap, source_message_id=1,
                                           posted_at="2026-11-12T04:31:00",
                                           media_paths=["m/x.jpg"]))
            html = krs.render_html(conn)
            self.assertIn("무역액 YoY", html)
            self.assertIn("▲+128.9%", html)
            self.assertIn("BE보다 주가 좋음", html)
            self.assertIn("수출·수입을 밝히지 않은", html)


class UndirectedRoutingTests(unittest.TestCase):
    """방향 미표기 판(`한국` 만)의 운영자 확인 라우팅 표 — 수입이면 수입 보드."""

    def test_route_table_sends_sdi_to_import_board(self):
        from trade import badonion_sources as bs
        from trade import kr_stock_imports as kri
        self.assertEqual(krs.undirected_route("006400"), "수입")
        self.assertEqual(krs.undirected_route("999999"), "수출")   # 표 밖 = 기본
        self.assertEqual(bs.matching_keys(Nov2026LayoutTests.SDI), ("kri",))
        self.assertEqual(bs.matching_keys(Nov2026LayoutTests.VINA), ("krs",))
        with tempfile.TemporaryDirectory() as td:
            conn = kri.open_kr_stock_import_db(Path(td) / "i.db")
            self.assertTrue(kri.ingest(conn, Nov2026LayoutTests.SDI,
                                       source_message_id=1, posted_at="",
                                       media_paths=["m/a.jpg"]))
            html = kri.render_html(conn)
            self.assertIn("Samsung SDI", html)
            self.assertIn("운영자 확인 표", html)
            self.assertIn("1개 회사", html)

    def test_explicit_direction_word_wins_over_table(self):
        cap = Nov2026LayoutTests.SDI.replace("\n한국\n", "\n한국 수출\n")
        self.assertIsNotNone(krs.parse_kr_stock_export(cap))
        self.assertIsNone(krs.parse_kr_stock_export(cap, direction="수입"))

    def test_rows_baked_before_routing_leave_export_board(self):
        with tempfile.TemporaryDirectory() as td:
            conn = krs.open_kr_stock_db(Path(td) / "k.db")
            row = krs.parse_kr_stock_export(Nov2026LayoutTests.SDI, direction="수입")
            krs.upsert_kr_stock(conn, row, chart_media=None, source_message_id=1,
                                posted_at="", raw_text="")
            krs.ingest(conn, Nov2026LayoutTests.VINA, source_message_id=2,
                       posted_at="")
            html = krs.render_html(conn)
            self.assertNotIn("Samsung SDI", html)
            self.assertIn("비나텍", html)
