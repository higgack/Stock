"""중국 수입(종목별) 파서 회귀 — 나쁜양파.

사용자 2026-08-21: "중국수입 7월 기업도 있어. 수출건이랑 똑같이 하면 돼."

⚠️ **수출과 방향이 반대다.** 중국이 사는 쪽이고 티커는 파는 회사다
(SQM = 칠레 리튬). 문법 엔진(`cn_stock_flow`)은 공유하지만 라벨·문구가
수출 것으로 남으면 화면이 스스로 거짓말한다(실수 #55) — 여기서 그걸
못박는다.
"""

import tempfile
import unittest
from pathlib import Path

from trade import badonion_sources as srcs
from trade import cn_stock_exports as cns
from trade import cn_stock_imports as cni

# 사용자 2026-08-21 캡처(SQM) — 동시 계열만 오고 선행은 없다.
_SQM = (
    "SQM (SQM)\n중국 수입\n26년 7월 Update\n\n"
    "단가 YoY: +97.0%\n수입액 YoY: +280.6%\n3M 수입액 YoY: +238.9%\n\n"
    "동시상관: 0.91\n방향 일치율: 88%\n\n"
    "- 중국 양극재·배터리 체인의 재고 보충이 이어지면서 리튬 판매 회복을 "
    "수요 측에서 확인하는 근거가 강화됐습니다.\n\n"
    "https://badonion.co.kr/trade/mapping")


class ParseTests(unittest.TestCase):
    def test_screenshot_caption(self):
        d = cni.parse_cn_stock_import(_SQM)
        self.assertEqual(d["ticker"], "SQM")
        self.assertEqual(d["stock_name"], "SQM")
        self.assertEqual(d["month"], "2026-07")
        self.assertEqual(d["amount_yoy"], 280.6)
        self.assertEqual(d["amount_yoy_3m"], 238.9)
        self.assertEqual(d["price_yoy"], 97.0)
        self.assertEqual(d["corr"], 0.91)
        self.assertEqual(d["dir_hit"], 88.0)
        self.assertIsNone(d["lead_corr"], "안 온 선행 계열을 지어내면 안 된다")
        self.assertIsNone(d["lead_dir_hit"])
        self.assertIn("양극재", d["comment"])

    def test_import_and_export_do_not_claim_each_other(self):
        """마커 한 단어 차이라 서로 먹으면 조용한 오저장이 된다."""
        exp = ("TTM Technologies (TTMI)\n중국 수출\n26년 7월 Update\n\n"
               "수출액 YoY: +55.8%")
        self.assertIsNone(cni.parse_cn_stock_import(exp), "수출을 수입이 먹음")
        self.assertIsNone(cns.parse_cn_stock_export(_SQM), "수입을 수출이 먹음")

    def test_registry_routes_the_caption_here(self):
        self.assertTrue(srcs.is_relevant(_SQM))
        first = next(s.key for s in srcs.SOURCES if s.parse(_SQM) is not None)
        self.assertEqual(first, "cni")

    def test_missing_metric_is_none_not_zero(self):
        d = cni.parse_cn_stock_import(_SQM.replace("단가 YoY: +97.0%\n", ""))
        self.assertIsNone(d["price_yoy"])

    def test_invalid_month_rejected(self):
        self.assertIsNone(cni.parse_cn_stock_import(
            _SQM.replace("26년 7월", "26년 13월")))


class StoreTests(unittest.TestCase):
    def test_separate_table_from_exports(self):
        """수출·수입이 한 테이블을 쓰면 같은 티커가 서로를 덮는다."""
        self.assertNotEqual(cni.FLOW.table, cns.FLOW.table)
        with tempfile.TemporaryDirectory() as tmp:
            conn = cni.open_cn_stock_import_db(Path(tmp) / "i.db")
            self.assertTrue(cni.ingest(conn, _SQM))
            self.assertEqual(len(cni.list_cn_stock_import(conn)), 1)

    def test_monthly_rolling_replaces_card_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = cni.open_cn_stock_import_db(Path(tmp) / "i.db")
            cni.ingest(conn, _SQM.replace("26년 7월", "26년 6월"))
            cni.ingest(conn, _SQM)
            rows = cni.list_cn_stock_import(conn)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["month"], "2026-07")
            self.assertEqual(len(cni.history(conn, "SQM")), 2)


class RenderTests(unittest.TestCase):
    def _html(self, caption=_SQM):
        with tempfile.TemporaryDirectory() as tmp:
            conn = cni.open_cn_stock_import_db(Path(tmp) / "i.db")
            if caption:
                cni.ingest(conn, caption)
            return cni.render_html(conn)

    def test_page_says_import_not_export(self):
        """공용 엔진을 쓰면서 라벨이 수출로 남으면 화면이 거짓말한다(#55)."""
        h = self._html()
        self.assertIn("중국 수입 데이터(종목별)", h)
        self.assertIn("수입액 YoY", h)
        self.assertNotIn("수출액", h)
        self.assertNotIn("수출 데이터", h)

    def test_empty_page_still_renders(self):
        h = self._html(caption="")
        self.assertIn("아직 수집된 중국 수입 데이터", h)
        self.assertIn("<!DOCTYPE html>", h)

    def test_no_sibling_link_until_the_item_page_exists(self):
        """중국 품목 기준 **수입** 페이지는 없다 — 억지 링크는 404 다."""
        self.assertEqual(cni.FLOW.sibling, "")
        self.assertNotIn("cn.html", self._html())

    def test_comment_is_rendered(self):
        self.assertIn("양극재", self._html())

    def test_levels_have_no_sign_or_arrow(self):
        h = self._html()
        self.assertIn("0.91", h)
        self.assertNotIn("+0.91", h)


if __name__ == "__main__":
    unittest.main()
