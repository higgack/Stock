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
        # 같은 심볼 반복은 하나. ⚠️ 2026-10-08(#222): 옛 판은 `a #P b #P` 처럼 글
        # 사이에 낀 태그도 받았는데, 그 모양은 라벨 줄·이름 태그와 구별되지 않아
        # 출처 판정(아래 `ProvenanceTests`)이 받지 않는다 — 계약은 '같은 심볼 반복은
        # 하나' 이지 그 줄 모양이 아니다.
        self.assertEqual(kcf.hashtag_symbol("#P\n…\n#P"), "P")
        self.assertEqual(kcf.hashtag_symbol("#P #p"), "P")

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


class ProvenanceTests(unittest.TestCase):
    """해시태그가 **그 회사의 심볼**이라는 근거는 캡션의 모양뿐이다(독립 리뷰
    2026-10-08). 실측 모양(`#P` 가 홀로 한 줄)에서 벗어난 것 — 라벨 줄 · 이름 태그 ·
    여러 태그가 든 줄(목록) — 은 받지 않는다. 틀린 링크보다 평문이 낫다(#144·#165)."""

    def test_the_observed_shape_is_taken(self):
        self.assertEqual(kcf.hashtag_symbol("#P"), "P")
        self.assertEqual(kcf.hashtag_symbol("#P 🔗 맵핑에서 보기"), "P")
        self.assertEqual(uss.parse_us_stock_export(PLAIN)["symbol"], "P")

    def test_a_label_line_lists_other_companies(self):
        """`관련기업: #NTAP` — 품목판이 쓰는 라벨 줄. 그 태그는 ▶️ 회사의 심볼이
        아니라 다른 회사들의 목록일 수 있다."""
        self.assertIsNone(kcf.hashtag_symbol("관련기업: #NTAP"))
        cap = PLAIN.replace("#P\n", "관련기업: #NTAP\n")
        self.assertIsNone(uss.parse_us_stock_export(cap)["symbol"])

    def test_a_name_tag_is_not_a_symbol(self):
        self.assertIsNone(kcf.hashtag_symbol("#Pure Storage"))
        self.assertIsNone(kcf.hashtag_symbol("#LIN #APD #AIR LIQUIDE"))
        # 이름 태그가 **다른 줄**이면 남은 하나가 심볼이다(반대 증거, #25)
        self.assertEqual(kcf.hashtag_symbol("#P\n#Pure Storage"), "P")
        # 같은 줄이면 목록이다 — 이름 태그를 빼고 남은 하나를 고르지 않는다
        self.assertIsNone(kcf.hashtag_symbol("#P #Pure Storage"))

    def test_numeric_tags_are_not_us_symbols(self):
        self.assertIsNone(kcf.hashtag_symbol("#2330"))

    def test_a_pathological_tag_line_is_judged_in_linear_time(self):
        """첫 판의 출처 판정 정규식은 `#a#a…#a foo #P` 에서 지수 역추적이었다
        (`#a`×22 에 0.43초, 두 개마다 ~4배 — 배포전 셀프리뷰 실측). 남이 쓴 캡션
        한 줄이 관련성 필터를 멈추면 안 된다(#71). 선형이면 즉시 끝난다 — n=26 은
        옛 정규식으로 수 초(실패로 드러난다), 더 크면 테스트가 **멈춘다**.
        그다음 판은 태그마다 줄 앞부분을 다시 갈라 태그 수 × 줄 길이였다(반영분
        리뷰 실측: 태그 16,000개 11.9초) — 둘째 줄이 **태그가 실제로 받히는**
        경로로 그걸 잰다(붙은 태그만 쓰면 매치가 0개라 그 경로를 안 탄다)."""
        import time
        line = "#a" * 26 + " foo #P"
        t0 = time.perf_counter()
        self.assertIsNone(kcf.hashtag_symbol(line))     # 라벨 줄 모양이라 거절
        self.assertLess(time.perf_counter() - t0, 1.0)
        many = "#a " * 20000 + "foo #P"
        t0 = time.perf_counter()
        self.assertIsNone(kcf.hashtag_symbol(many))     # 같은 줄에 다른 태그 — 목록
        self.assertEqual(kcf.hashtag_symbol("#a " * 20000), "A")   # 같은 심볼 반복은 하나
        self.assertLess(time.perf_counter() - t0, 1.0)
        # 글자에 붙은 태그(`#a#a…`)는 태그가 아니다 — 다음 줄의 `#P` 만 남는다.
        self.assertEqual(kcf.hashtag_symbol("#a" * 5000 + "\n#P"), "P")

    def test_a_line_with_several_tags_is_a_list_not_a_symbol(self):
        """후보를 **빼기만** 하면 목록이 '하나 남은 태그' 로 줄어 엉뚱한 심볼이 된다 —
        반영분 리뷰 실측: `#NTAP, #DELL, #HPE` 에서 Everpure 카드가 HPE 로 걸렸다.
        받은 태그가 있는 줄에 다른 라틴 태그가 있으면 고르지 않는다."""
        for line in ("#P, #PSTG", "#NTAP, #DELL, #HPE", "#P · #PSTG",
                     "#AAPL#MSFT #NVDA", "#P #AI", "#NTAP / #P"):
            self.assertIsNone(kcf.hashtag_symbol(line), line)
        # 끝에서 끝까지 — 카드가 남의 심볼로 걸리지 않는다
        cap = PLAIN.replace("#P\n", "#NTAP, #DELL, #HPE\n")
        self.assertIsNone(uss.parse_us_stock_export(cap)["symbol"])
        self.assertEqual(kcf.card_href({"company": "Everpure, Inc.", "raw_text": cap},
                                       uss.FLOW), "")
        # 반대 증거 — 경쟁자가 아닌 것: URL 조각 · 한글 태그 · 같은 심볼 반복
        self.assertEqual(kcf.hashtag_symbol("#P [링크](https://x.y/a?c=1#QQ)"), "P")
        self.assertEqual(kcf.hashtag_symbol("#P #한국"), "P")
        self.assertEqual(kcf.hashtag_symbol("#P #p"), "P")

    def test_a_sentence_dot_is_not_part_of_the_symbol(self):
        self.assertEqual(kcf.hashtag_symbol("#P."), "P")
        self.assertEqual(kcf.hashtag_symbol("#BRK.B"), "BRK.B")   # 가운데 점은 심볼의 일부

    def test_glued_tags_are_not_cut_into_symbols(self):
        """`#AAPL#MSFT` 를 `AAPL` 로 잘라 읽으면 붙어 있던 다른 심볼을 버린 것이다 —
        뒤가 공백·줄 끝일 때만 태그다(실측 `#P` 는 줄 끝)."""
        self.assertIsNone(kcf.hashtag_symbol("#AAPL#MSFT"))
        self.assertIsNone(kcf.hashtag_symbol("#P,"))
        self.assertEqual(kcf.hashtag_symbol("#P\t"), "P")

    def test_a_tag_glued_to_a_value_line_is_not_taken(self):
        """값 줄 끝에 붙은 태그는 그 줄이 라벨 줄과 같은 모양이라 받지 않는다 —
        실측 캡션엔 없는 모양이고, 못 받으면 평문일 뿐이다(#144)."""
        self.assertIsNone(kcf.hashtag_symbol("26년08월: $1.0M #P"))


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

    def test_without_a_symbol_the_noah_alias_name_still_links(self):
        """심볼이 없으면 이름을 넘긴다 — NOAH 영문 별칭표가 푸는 이름만 질의가
        된다(`lookup_query` 규칙 4). 이름을 안 넘기면 그 경로가 통째로 죽는다(독립
        리뷰 생존 뮤테이션). 별칭표는 경계(`_alias_hit`)에서 정한다(#399)."""
        orig = sl._alias_hit
        sl._alias_hit = lambda n: n == "Zyxcorp"
        try:
            cap = PLAIN.replace("Everpure, Inc.", "Zyxcorp").replace("#P\n", "")
            row = {"company": "Zyxcorp", "raw_text": cap}
            self.assertEqual(kcf.card_href(row, uss.FLOW), "../lookup/Zyxcorp")
            # 반대 증거 — 별칭표가 모르는 이름은 평문
            other = {"company": "Unknownco", "raw_text": cap.replace("Zyxcorp", "Unknownco")}
            self.assertEqual(kcf.card_href(other, uss.FLOW), "")
        finally:
            sl._alias_hit = orig

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
    def test_every_amount_flow_says_the_country_of_its_source_and_sibling(self):
        """금액판 Flow 의 나라는 그 소스의 나라와 같고, 형제 링크는 **같은 나라**의
        페이지를 가리킨다 — 형제를 남의 나라 페이지로 바꾼 변형이 살아남았다(독립
        리뷰). 소스는 레지스트리 전수에서 고른다(이름 열거 금지, #24)."""
        import importlib
        checked = 0
        for src in srcs.SOURCES:
            if "amount" not in src.grammars:
                continue
            mod = importlib.import_module(src.parse.__module__)
            flows = [v for v in vars(mod).values() if isinstance(v, kcf.Flow)]
            self.assertTrue(flows, f"{src.key}: 모듈에 Flow 가 없다")
            for fl in flows:
                checked += 1
                self.assertEqual(fl.country, src.country, (src.key, fl.table))
                if fl.sibling:
                    owners = [o for o in srcs.SOURCES if o.html_file == fl.sibling]
                    self.assertEqual(len(owners), 1, (src.key, fl.sibling))
                    self.assertEqual(owners[0].country, fl.country,
                                     (src.key, fl.sibling, owners[0].key))
        self.assertGreaterEqual(checked, 3)        # krs·kri·uss — 줄면 빨간불(#54)

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
