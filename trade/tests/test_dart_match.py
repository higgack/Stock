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

    def test_additional_candidates_excludes_current(self):
        # 각 품목에 더 많은 상장사 발굴 — 현재 큐레이션에 없는 DART 회사만(2026-06-19).
        # 주력(share_pct ≥ min_share)만 — 부수 세그먼트 제외.
        inv = {
            "005930": {"company": "삼성전자", "products": [{"name": "DRAM", "share_pct": 80}]},
            "000660": {"company": "SK하이닉스", "products": [{"name": "DRAM", "share_pct": 90}]},
        }
        add = M.additional_candidates(inv, {"디램": {"삼성전자"}})
        self.assertEqual(add.get("디램"), ["SK하이닉스"])    # 삼성전자 제외, 신규만
        self.assertEqual(
            M.additional_candidates(inv, {"디램": {"삼성전자", "sk하이닉스"}}), {})

    def test_additional_candidates_excludes_rejected(self):
        # 운영자 거절(품목,회사)은 영구 제외 — 다시 후보로 안 뜸(사용자 2026-06-20).
        inv = {
            "001": {"company": "거절사", "products": [{"name": "변압기", "share_pct": 80}]},
            "002": {"company": "정상사", "products": [{"name": "변압기", "share_pct": 80}]},
        }
        ic = {"초고압 변압기": set()}
        # 거절 없으면 둘 다
        self.assertEqual(set(M.additional_candidates(inv, ic)["초고압 변압기"]),
                         {"거절사", "정상사"})
        # 거절사 거절 → 정상사만
        add = M.additional_candidates(
            inv, ic, rejected={"초고압 변압기": {"거절사"}})
        self.assertEqual(add.get("초고압 변압기"), ["정상사"])

    def test_additional_candidates_jooryeok_only(self):
        # 부수 세그먼트(저비중·share None)는 제외 — '진짜 주력만'(사용자 2026-06-19).
        inv = {
            "001": {"company": "주력사", "products": [{"name": "화장품", "share_pct": 60}]},
            "002": {"company": "잡주", "products": [{"name": "화장품", "share_pct": 3}]},   # 저비중
            "003": {"company": "노셰어", "products": [{"name": "화장품"}]},                # share 없음
        }
        add = M.additional_candidates(inv, {"화장품": set()}, min_share=30.0)
        self.assertEqual(add.get("화장품"), ["주력사"])      # 주력사만, 잡주·노셰어 제외


if __name__ == "__main__":
    unittest.main()
