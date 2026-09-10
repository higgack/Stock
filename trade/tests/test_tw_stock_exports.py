"""대만 수출(종목별) 파서 회귀 — 나쁜양파.

사용자 2026-09-10: "나쁜양파에 8월 대만기업수출올라왔는데 우리쪽으로
포워드안되고 대시보드업데이트안됐어."

재현(§Pre-commit 9 — 재현 테스트 먼저): 이 캡션이 `is_relevant` 를 통과하지
못했다 — 대만 **종목판** 파서가 레지스트리에 없었다(#83 이 예측한 다섯
번째). 엔진(`cn_stock_flow`)이 `중국` 을 리터럴로 박고 있어 나라를
`Flow.country` 로 올렸고, 여기서 (a) 스크린샷 4장의 변주가 전부 파싱되고
(b) 레지스트리가 이 소스로 라우팅하며 (c) 중국 소스가 대만을 먹지 않고
(d) 화면 문구가 중국이 아니라 대만이라고 말하는지 못박는다(#55).
"""

import tempfile
import unittest
from pathlib import Path

from trade import badonion_sources as srcs
from trade import cn_stock_exports as cns
from trade import cn_stock_imports as cni
from trade import tw_stock_exports as tws

# 사용자 2026-09-10 캡처 — 선행 계열만 오고 꼬리가 맨 도메인이다.
_ASX = (
    "ASE Technology Holding (ASX)\n대만 수출\n26년 8월 Update\n\n"
    "단가 YoY: +23.2%\n수출액 YoY: +33.1%\n3M 수출액 YoY: +33.1%\n\n"
    "선행상관: 0.79\n선행 방향 일치율: 76%\n\n"
    "badonion.co.kr")
# 동시 계열만 · 3M 없음 · 꼬리가 전체 URL.
_MXL = (
    "MaxLinear (MXL)\n대만 수출\n26년 8월 Update\n\n"
    "단가 YoY: +18.9%\n수출액 YoY: +66.7%\n\n"
    "동시상관: 0.82\n방향 일치율: 72%\n\n"
    "https://badonion.co.kr/trade/mapping")
_PTON = (
    "Peloton Interactive (PTON)\n대만 수출\n26년 8월 Update\n\n"
    "단가 YoY: +8.0%\n수출액 YoY: -9.3%\n3M 수출액 YoY: -7.8%\n\n"
    "선행상관: 0.71\n선행 방향 일치율: 71%\n\nbadonion.co.kr")


class ParseTests(unittest.TestCase):
    def test_screenshot_asx_lead_series_only(self):
        d = tws.parse_tw_stock_export(_ASX)
        self.assertEqual(d["ticker"], "ASX")
        self.assertEqual(d["stock_name"], "ASE Technology Holding")
        self.assertEqual(d["month"], "2026-08")
        self.assertEqual(d["amount_yoy"], 33.1)
        self.assertEqual(d["amount_yoy_3m"], 33.1)
        self.assertEqual(d["price_yoy"], 23.2)
        self.assertEqual(d["lead_corr"], 0.79)
        self.assertEqual(d["lead_dir_hit"], 76.0)
        self.assertIsNone(d["corr"], "안 온 동시 계열을 지어내면 안 된다")
        self.assertIsNone(d["dir_hit"])

    def test_screenshot_mxl_coincident_only_no_3m(self):
        d = tws.parse_tw_stock_export(_MXL)
        self.assertEqual(d["ticker"], "MXL")
        self.assertEqual(d["amount_yoy"], 66.7)
        self.assertIsNone(d["amount_yoy_3m"], "없는 3M 을 당월로 채우면 안 된다")
        self.assertEqual(d["corr"], 0.82)
        self.assertEqual(d["dir_hit"], 72.0)
        self.assertEqual(d["corr_basis"], "동시")
        self.assertIsNone(d["lead_corr"])

    def test_negative_yoy_keeps_sign(self):
        d = tws.parse_tw_stock_export(_PTON)
        self.assertEqual(d["amount_yoy"], -9.3)
        self.assertEqual(d["amount_yoy_3m"], -7.8)

    def test_bare_domain_footer_is_not_the_item(self):
        """꼬리가 `https://` 없는 맨 `badonion.co.kr` 로 오면 품목 슬롯에
        앉았다(실측). 도메인 모양으로 거른다 — 진짜 설명 줄은 남는다."""
        self.assertIsNone(tws.parse_tw_stock_export(_ASX)["item"])
        self.assertIsNone(tws.parse_tw_stock_export(_MXL)["item"])
        with_desc = _ASX.replace("badonion.co.kr",
                                 "패키지 기판·인터커넥트\nbadonion.co.kr")
        self.assertEqual(tws.parse_tw_stock_export(with_desc)["item"],
                         "패키지 기판·인터커넥트")
        # 줄 전체가 도메인일 때만 거른다 — 도메인 낱말이 든 진짜 설명은 남고,
        # 꼬리 마침표가 붙은 도메인은 걸러진다(독립 리뷰 2026-09-10 실측 셋).
        vendor = _ASX.replace("badonion.co.kr", "AI 서버용 GPU · NVIDIA.com 공급\nbadonion.co.kr.")
        self.assertEqual(tws.parse_tw_stock_export(vendor)["item"],
                         "AI 서버용 GPU · NVIDIA.com 공급")
        self.assertIsNone(tws.parse_tw_stock_export(
            _ASX.replace("badonion.co.kr", "badonion.co.kr."))["item"])

    def test_registry_routes_the_caption_here(self):
        """이 파서가 곧 관련성 필터다 — 없으면 조용히 드랍(#83)."""
        for cap in (_ASX, _MXL, _PTON):
            self.assertTrue(srcs.is_relevant(cap))
            first = next(s.key for s in srcs.SOURCES if s.parse(cap) is not None)
            self.assertEqual(first, "tws")

    def test_china_sources_do_not_claim_taiwan(self):
        """엔진을 공유하니 나라 게이트가 빠지면 중국 DB 로 샌다(#83 반대 방향)."""
        self.assertIsNone(cns.parse_cn_stock_export(_ASX))
        self.assertIsNone(cni.parse_cn_stock_import(_ASX))
        self.assertIsNone(tws.parse_tw_stock_export(
            _ASX.replace("대만 수출", "중국 수출")))

    def test_item_level_taiwan_parser_is_untouched(self):
        """품목판(`N월 수출 대만`)과 종목판(`대만 수출`)은 어순이 반대다 —
        서로 먹지 않는다."""
        from trade import tw_exports as tw
        self.assertIsNone(tw.parse_tw_export(_ASX))

    def test_invalid_month_rejected(self):
        self.assertIsNone(tws.parse_tw_stock_export(
            _ASX.replace("26년 8월", "26년 13월")))


class StoreTests(unittest.TestCase):
    def test_separate_table_from_china(self):
        self.assertNotEqual(tws.FLOW.table, cns.FLOW.table)
        self.assertEqual(tws.FLOW.country, "대만")
        with tempfile.TemporaryDirectory() as tmp:
            conn = tws.open_tw_stock_db(Path(tmp) / "t.db")
            self.assertTrue(tws.ingest(conn, _ASX))
            self.assertTrue(tws.ingest(conn, _MXL))
            self.assertEqual(len(tws.list_tw_stock(conn)), 2)

    def test_monthly_rolling_replaces_card_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = tws.open_tw_stock_db(Path(tmp) / "t.db")
            tws.ingest(conn, _ASX.replace("26년 8월", "26년 7월"))
            tws.ingest(conn, _ASX)
            rows = tws.list_tw_stock(conn)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["month"], "2026-08")
            self.assertEqual(len(tws.history(conn, "ASX")), 2)


class RenderTests(unittest.TestCase):
    def _html(self, caption=_ASX):
        with tempfile.TemporaryDirectory() as tmp:
            conn = tws.open_tw_stock_db(Path(tmp) / "t.db")
            if caption:
                tws.ingest(conn, caption)
            return tws.render_html(conn)

    def test_page_says_taiwan_not_china(self):
        """공용 엔진을 쓰면서 나라 문구가 중국으로 남으면 화면이 거짓말한다(#55)."""
        h = self._html()
        self.assertIn("대만 수출 데이터(종목별)", h)
        self.assertIn("Badonions 대만 수출 캡션", h)
        self.assertIn("대만에서 수출하는 기업", h)
        self.assertNotIn("중국", h)

    def test_empty_page_still_renders(self):
        h = self._html(caption="")
        self.assertIn("아직 수집된 대만 수출 데이터", h)
        self.assertIn("<!DOCTYPE html>", h)
        self.assertNotIn("중국", h)

    def test_sibling_link_points_at_the_item_page(self):
        h = self._html()
        self.assertIn("tw.html", h)
        self.assertIn("대만 수출 데이터(품목)", h)

    def test_lead_series_is_labelled_lead(self):
        h = self._html()
        self.assertIn("0.79", h)
        self.assertNotIn("+0.79", h)


if __name__ == "__main__":
    unittest.main()
