"""미국 수출(종목별·나쁜양파) — 받는 파서가 없어 조용히 드랍되던 캡션(실수 #441).

사용자 2026-10-08 캡처: `🇺🇸 8월 수출 미국` / `▶️ Everpure, Inc. — FlashBlade …` /
`26년08월: $489.9M (+173.9% YoY) (-7.0% MoM)` + 최근 추이 + `#P`. 한국 회사별
금액판과 같은 문법이라 엔진(`kr_company_flow`)을 나라만 바꿔 쓴다 — 그러려면
엔진 안의 '한국' 리터럴과 KRX 전용 딥링크 규칙을 `Flow` 로 올려야 했다(#330).

⚠️ 픽스처는 **스크린샷 재구성**이다(#155·#334). 봇이 inbox 에 쓰는 원문은
python-telegram-bot 의 평문(`post.caption`)이고, 리스너가 관련성을 판정하는 원문은
telethon 마크다운(`**굵게**` · `[글](url)`)이다 — 둘 다 받는지 잰다.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from trade import badonion_sources as srcs
from trade import kr_company_flow as kcf
from trade import kr_stock_imports as kri
from trade import stock_link as sl
from trade import us_stock_exports as uss

# 봇이 inbox 에 쓰는 평문(post.caption) 모양.
PLAIN = ("🇺🇸 8월 수출 미국\n\n"
         "▶️ Everpure, Inc. — FlashBlade 데이터 저장시스템, FlashArray 올플래시 스토리지\n\n"
         "26년08월: $489.9M  (+173.9% YoY)  (-7.0% MoM)\n\n"
         "최근 추이 (단위: USD M$)\n"
         "26년07월: $526.7M  (+145.8% YoY)  (+7.4% MoM)\n"
         "26년06월: $490.4M  (+166.7% YoY)  (+48.1% MoM)\n"
         "#P\n🔗 맵핑에서 보기")
# 리스너(telethon)가 보는 마크다운 모양 — 링크 URL 에 `#` 조각이 있어도 심볼이 아니다.
MD = ("**🇺🇸 8월 수출 미국**\n\n"
      "**▶️ Everpure, Inc. — FlashBlade 데이터 저장시스템, FlashArray 올플래시 스토리지**\n\n"
      "**26년08월: $489.9M  (+173.9% YoY)  (-7.0% MoM)**\n\n"
      "최근 추이 (단위: USD M$)\n"
      "26년07월: $526.7M  (+145.8% YoY)  (+7.4% MoM)\n"
      "26년06월: $490.4M  (+166.7% YoY)  (+48.1% MoM)\n"
      "#P\n[🔗 맵핑에서 보기](https://badonion.co.kr/mapping?c=P#QQQ)")


def _db(td: str, name: str = "us_stock.db"):
    return uss.open_us_stock_db(Path(td) / name)


class ParseTests(unittest.TestCase):
    def test_both_wire_shapes_parse_to_the_same_rows(self):
        for cap in (PLAIN, MD):
            p = uss.parse_us_stock_export(cap)
            self.assertIsNotNone(p, cap[:40])
            self.assertEqual(p["stock_name"], "Everpure, Inc.")
            self.assertEqual(p["item"],
                             "FlashBlade 데이터 저장시스템, FlashArray 올플래시 스토리지")
            self.assertEqual([m["month"] for m in p["months"]],
                             ["2026-08", "2026-07", "2026-06"])
            self.assertEqual(p["months"][0], {"month": "2026-08", "value_musd": 489.9,
                                              "value_yoy": 173.9, "value_mom": -7.0})
            self.assertEqual(p["symbol"], "P")

    def test_only_this_source_claims_it(self):
        """관련성 필터가 곧 파서다 — 아무도 안 받으면 조용한 유실, 둘이 받으면
        순서에 따라 저장처가 갈린다(#83·#261)."""
        for cap in (PLAIN, MD):
            self.assertEqual(srcs.matching_keys(cap), ("uss",))
            self.assertTrue(srcs.is_relevant(cap))

    def test_neighbouring_captions_are_not_claimed(self):
        kr = PLAIN.replace("수출 미국", "수출 한국")
        us_import = PLAIN.replace("수출 미국", "수입 미국")
        self.assertIsNone(uss.parse_us_stock_export(kr))
        self.assertIsNone(uss.parse_us_stock_export(us_import))
        # 한국 금액판은 여전히 한국 소스가 받는다(나라 축이 실제로 가른다).
        self.assertIn("krs", srcs.matching_keys(kr))

    def test_an_item_board_without_a_company_dash_is_refused(self):
        """대시 없는 ▶️ 는 **품목**판이다 — 품목을 종목 칸에 넣으면 화면이 거짓말한다(#34)."""
        item = PLAIN.replace("Everpure, Inc. — FlashBlade 데이터 저장시스템, "
                             "FlashArray 올플래시 스토리지", "데이터 저장장치")
        self.assertIsNone(uss.parse_us_stock_export(item))

    def test_amount_is_required_but_yoy_mom_are_not(self):
        bare = ("🇺🇸 8월 수출 미국\n▶️ X Corp — 품목\n26년08월: $1.5M\n")
        p = uss.parse_us_stock_export(bare)
        self.assertEqual(p["months"], [{"month": "2026-08", "value_musd": 1.5,
                                        "value_yoy": None, "value_mom": None}])
        self.assertIsNone(uss.parse_us_stock_export(
            "🇺🇸 8월 수출 미국\n▶️ X Corp — 품목\n최근 추이\n"))


class HashtagSymbolTests(unittest.TestCase):
    def test_one_symbol_is_taken_and_uppercased(self):
        self.assertEqual(kcf.hashtag_symbol("…\n#pstg\n"), "PSTG")
        self.assertEqual(kcf.hashtag_symbol("a #P b #P"), "P")       # 같은 심볼 반복은 하나

    def test_two_different_symbols_are_not_guessed(self):
        """어느 것이 ▶️ 회사의 심볼인지 캡션이 말하지 않으면 고르지 않는다(#165)."""
        self.assertIsNone(kcf.hashtag_symbol("#P\n#NTAP"))

    def test_url_fragments_and_glued_hashes_are_not_symbols(self):
        self.assertIsNone(kcf.hashtag_symbol("https://x.y/map#P"))
        self.assertIsNone(kcf.hashtag_symbol("[링크](https://x.y/a?c=1#QQ)"))
        self.assertIsNone(kcf.hashtag_symbol("#한글태그"))
        self.assertIsNone(kcf.hashtag_symbol("#TOOLONGSYMBOLX"))      # 잘라 읽지 않는다

    def test_a_second_company_symbol_stays_in_its_own_segment(self):
        """두 회사가 한 캡션이면 첫 회사 구간의 심볼만 — 남의 심볼을 달지 않는다."""
        two = (PLAIN.replace("#P\n", "") + "\n\n▶️ NetApp, Inc. — 스토리지\n"
               "26년08월: $10.0M  (+1.0% YoY)  (+1.0% MoM)\n#NTAP\n")
        p = uss.parse_us_stock_export(two)
        self.assertEqual(p["stock_name"], "Everpure, Inc.")
        self.assertIsNone(p["symbol"])


class StoreAndRenderTests(unittest.TestCase):
    def test_ingest_then_render_links_the_symbol_and_says_the_country(self):
        with tempfile.TemporaryDirectory() as td:
            conn = _db(td)
            self.assertTrue(uss.ingest(conn, PLAIN, source_message_id=7,
                                       posted_at="2026-10-07T14:42:00Z",
                                       media_paths=["photos/c.jpg"]))
            rows = uss.list_us_stock(conn)
            self.assertEqual([(r["company"], r["month"]) for r in rows],
                             [("Everpure, Inc.", "2026-08")])
            self.assertEqual(len(uss.history(conn, "Everpure, Inc.")), 3)
            html = uss.render_html(conn, media_url_prefix="../m/")
        head = re.search(r'<span class="krf-item">(.*?)</span>', html).group(1)
        self.assertIn('href="../lookup/P"', head)
        sub = re.search(r"<div class='sub'>(.*?)</div>", html).group(1)
        self.assertIn("미국 수출", sub)
        self.assertIn("<b>종목별</b>", sub)
        self.assertIn("href='us.html'", sub)
        self.assertNotIn("한국", html, "엔진의 한국 리터럴이 미국 페이지에 샜다(#330)")
        self.assertRegex(html, r"1개 종목 · 최신 2026-08")
        self.assertIn('src="../m/photos/c.jpg"', html)
        self.assertIn("🦅 미국 수출 데이터(종목별)", html)

    def test_empty_page_still_renders_in_its_own_words(self):
        with tempfile.TemporaryDirectory() as td:
            html = uss.render_html(_db(td))
        self.assertIn("아직 수집된 미국 수출 데이터(나쁜양파·종목별)가 없습니다", html)
        self.assertIn("0개 종목", html)
        self.assertNotIn("한국", html)

    def test_a_symbol_from_another_company_raw_text_is_not_used(self):
        """행의 원문 첫 회사가 그 행의 회사가 아니면 심볼은 남의 것이다 — 평문."""
        row = {"company": "Other, Inc.", "raw_text": PLAIN}
        self.assertEqual(kcf.card_href(row, uss.FLOW), "")
        self.assertEqual(kcf.card_href({"company": "Everpure, Inc.", "raw_text": PLAIN},
                                       uss.FLOW), "../lookup/P")

    def test_no_symbol_means_no_link(self):
        with tempfile.TemporaryDirectory() as td:
            conn = _db(td)
            uss.ingest(conn, PLAIN.replace("#P\n", ""), source_message_id=8,
                       posted_at="")
            html = uss.render_html(conn)
        head = re.search(r'<span class="krf-item">(.*?)</span>', html).group(1)
        self.assertEqual(head, "Everpure, Inc.", "심볼 없이 링크를 지어냈다(#165)")

    def test_the_hashtag_flow_never_asks_the_krx_master(self):
        """미국 회사명을 KRX 목록에 물으면 헛돌고(#61) 동명 상장사가 남의 화면을
        연다(#34) — 한국 흐름만 그 수단을 쓴다."""
        calls = []
        orig = sl.kr_codes
        sl.kr_codes = lambda names: calls.append(list(names)) or {"Everpure, Inc.": "005930"}
        try:
            with tempfile.TemporaryDirectory() as td:
                conn = _db(td)
                uss.ingest(conn, PLAIN, source_message_id=9, posted_at="")
                html = uss.render_html(conn)
                self.assertEqual(calls, [], "미국 흐름이 KRX 목록을 물었다")
                self.assertNotIn("005930", html)
                # 반대 증거 — 한국 수입 흐름은 여전히 KRX 이름 대조로 건다.
                kconn = kri.open_kr_stock_import_db(Path(td) / "kri.db")
                kri.ingest(kconn, ("🇰🇷 8월 수입 한국\n▶️ 텔레칩스 — 차량용 AP\n"
                                   "26년08월: $2,175.2M  (+49.4% YoY)  (+7.3% MoM)\n"),
                           source_message_id=10, posted_at="")
                calls.clear()
                sl.kr_codes = lambda names: (calls.append(list(names))
                                             or {"텔레칩스": "054450"})
                khtml = kri.render_html(kconn)
                self.assertEqual(calls, [["텔레칩스"]])
                self.assertIn('href="../lookup/054450"', khtml)
                self.assertIn("한국 수입", khtml)
        finally:
            sl.kr_codes = orig


class FlowContractTests(unittest.TestCase):
    def test_an_unknown_link_rule_fails_loudly(self):
        with self.assertRaises(ValueError):
            kcf.Flow(key="export", marker="수출", amount="수출액", table="t",
                     title="t", country="가상국", unit="종목", link="krx_nmae")

    def test_the_engine_carries_no_country_literal(self):
        """합성 나라로 엔진을 태운다 — 화면 문구가 Flow 에서만 와야 다음 나라가
        어댑터 한 줄로 붙는다(#330 이 같은 자리에서 막혔다)."""
        flow = kcf.Flow(key="import", marker="수입", amount="수입액",
                        table="fake_flow", title="🧪 가상국 수입 데이터(회사별)",
                        country="가상국", unit="회사", link="hashtag")
        cap = ("8월 수입 가상국\n▶️ Foo Ltd — 부품\n"
               "26년08월: $3.0M  (+1.0% YoY)  (+2.0% MoM)\n")
        with tempfile.TemporaryDirectory() as td:
            conn = kcf.open_db(Path(td) / "f.db", flow)
            empty = kcf.render_html(conn, flow)
            self.assertTrue(kcf.ingest(conn, flow, cap, source_message_id=1,
                                       posted_at=""))
            full = kcf.render_html(conn, flow)
        for html in (empty, full):
            self.assertIn("가상국 수입", html)
            self.assertNotIn("한국", html)
        self.assertIn("<b>회사별</b>", full)
        self.assertIn("1개 회사", full)


if __name__ == "__main__":
    unittest.main()
