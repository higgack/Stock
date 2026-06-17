"""trade.dart_match — G2 제품↔관세청 품목 매칭 가드 (순수·보수적)."""

import unittest

from trade import dart_match as M


class MatchTests(unittest.TestCase):
    def test_alias_and_exact(self):
        self.assertTrue(M.product_matches_item("DRAM", "디램"))
        self.assertTrue(M.product_matches_item("NAND Flash", "낸드"))
        self.assertTrue(M.product_matches_item("TV", "텔레비전"))
        self.assertTrue(M.product_matches_item("양극재", "양극재"))

    def test_rejects_generic_and_short(self):
        self.assertFalse(M.product_matches_item("용역", "디램"))
        self.assertFalse(M.product_matches_item("제품", "반도체"))   # generic
        self.assertFalse(M.product_matches_item("전선", "전"))        # item 너무 짧음

    def test_substring_len_guard(self):
        self.assertTrue(M.product_matches_item("타이어", "타이어코드"))   # len≥3 부분일치
        # 2글자 부분일치는 노이즈라 차단(별칭/완전일치만)
        self.assertFalse(M.product_matches_item("AB", "ABCD"))

    def test_matched_items(self):
        items = ["디램", "낸드", "라면"]
        prods = [{"name": "DRAM"}, {"name": "NAND Flash"}]
        self.assertEqual(set(M.matched_items(prods, items)), {"디램", "낸드"})

    def test_suggest_companies(self):
        inv = {
            "005930": {"company": "삼성전자",
                       "products": [{"name": "DRAM"}, {"name": "TV"}]},
            "000660": {"company": "SK하이닉스", "products": [{"name": "DRAM"}]},
        }
        sug = M.suggest_companies_for_items(inv, ["디램", "텔레비전", "라면"])
        self.assertEqual(set(sug.get("디램", [])), {"삼성전자", "SK하이닉스"})
        self.assertIn("삼성전자", sug.get("텔레비전", []))   # TV→텔레비전 별칭
        self.assertNotIn("라면", sug)                        # 매칭 회사 없음


if __name__ == "__main__":
    unittest.main()
