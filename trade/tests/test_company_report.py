"""trade.company_report — 기업 중심 보고서 (회사→제품+관세청 노출) 순수 가드.

네트워크(gather: DART)·LLM(render_llm: Gemini)은 VM 전용. 여기선 데이터→HTML 조립
(render_free)과 관세청 역조회(_company_exposure)만 — 무료 보고서의 핵심(₩0).
"""

import unittest

from trade import company_report as C


class RenderFreeTests(unittest.TestCase):
    def test_structure(self):
        data = {"query": "삼성전자", "code": "005930", "name": "삼성전자",
                "products": [{"name": "DRAM", "share_pct": 39.0},
                             {"name": "TV", "share_pct": 56.3}],
                "exposure": [{"item": "디램", "industry": "반도체",
                              "export_usd": 2e9, "import_usd": 5e8}]}
        h = C.render_free(data)
        for must in ("삼성전자", "005930", "DRAM", "디램", "제품 구성", "관세청",
                     "최신월 수출", "최신월 수입"):
            self.assertIn(must, h)
        self.assertIn("39.0%", h)
        self.assertIn("20.0억$", h)        # 수출 노출 억$ 포맷
        self.assertIn("5.0억$", h)         # 수입 노출 억$ 포맷

    def test_empty_graceful(self):
        h = C.render_free({"query": "없는회사", "name": "없는회사",
                           "products": [], "exposure": []})
        self.assertIn("없는회사", h)
        self.assertIn("미확보", h)          # DART 미확보 안내
        self.assertIn("매핑된 품목 없음", h)


class TelegramTests(unittest.TestCase):
    def test_render_telegram(self):
        data = {"name": "삼성전자", "code": "005930",
                "products": [{"name": "DRAM", "share_pct": 39.0}],
                "exposure": [{"item": "디램", "industry": "반도체",
                              "export_usd": 2e9, "import_usd": 5e8}]}
        t = C.render_telegram(data)
        for must in ("삼성전자", "DRAM", "디램", "제품 구성", "관세청",
                     "수출", "수입"):
            self.assertIn(must, t)
        self.assertLessEqual(len(t.encode("utf-16-le")) // 2, 4096)   # 텔레그램 cap
        t2 = C.render_telegram(data, ai_text="주력은 메모리반도체입니다")
        self.assertIn("AI 요약", t2)
        self.assertIn("주력은 메모리반도체입니다", t2)


class ExposureTests(unittest.TestCase):
    def test_reverse_lookup(self):
        # 삼성전자 ∈ companies_for('디램'), ∉ companies_for('라면')
        by_mti = {
            "831110": {"name": "디램", "industry": "반도체", "months": {"2026-05": 2e9}},
            "999999": {"name": "라면", "industry": "식품", "months": {"2026-05": 1e8}},
        }
        by_imp = {"831110": {"months": {"2026-05": 5e8}}}
        rows = C._company_exposure("삼성전자", by_mti, [], by_imp)
        items = [x["item"] for x in rows]
        self.assertIn("디램", items)
        self.assertNotIn("라면", items)
        dram = next(x for x in rows if x["item"] == "디램")
        self.assertEqual(dram["export_usd"], 2e9)       # 최신월 수출
        self.assertEqual(dram["import_usd"], 5e8)        # 최신월 수입(by_imp 매칭)

    def test_empty_name(self):
        self.assertEqual(C._company_exposure("", {"1": {"name": "x"}}, []), [])


class ItemModeTests(unittest.TestCase):
    """품목 역검색 (사용자 2026-06-18 '창에 품목 치면 관련기업')."""

    def test_item_matches_industry(self):
        # '반도체'(산업) → 그 산업 품목들의 관련기업 union + 행
        by_mti = {
            "831110": {"name": "디램", "industry": "반도체", "months": {"2026-05": 2e9}},
            "831120": {"name": "낸드플래시메모리", "industry": "반도체",
                       "months": {"2026-05": 1e9}},
            "999999": {"name": "라면", "industry": "식품", "months": {"2026-05": 1e8}},
        }
        by_imp = {"831110": {"months": {"2026-05": 5e8}}}
        res = C._item_matches("반도체", by_mti, [], by_imp)
        self.assertEqual(res["mode"], "item")
        self.assertIn("삼성전자", res["companies"])
        items = [x["item"] for x in res["items"]]
        self.assertIn("디램", items)
        self.assertNotIn("라면", items)               # 식품 산업 제외
        dram = next(x for x in res["items"] if x["item"] == "디램")
        self.assertEqual(dram["import_usd"], 5e8)      # by_imp 매칭

    def test_item_matches_keyword_no_node(self):
        # 저장 품목 노드가 없어도 query 자체가 큐레이션 키워드면 관련기업
        res = C._item_matches("디램", {}, [])
        self.assertIsNotNone(res)
        self.assertIn("삼성전자", res["companies"])

    def test_item_matches_none_when_unknown(self):
        # 매칭 없으면 None → gather 가 회사 모드로 폴백
        self.assertIsNone(C._item_matches("존재하지않는임의문자열X", {}, []))

    def test_render_free_item(self):
        data = {"mode": "item", "query": "반도체", "name": "반도체",
                "items": [{"item": "디램", "industry": "반도체",
                           "export_usd": 2e9, "import_usd": 5e8,
                           "companies": ["삼성전자", "SK하이닉스"]}],
                "companies": ["삼성전자", "SK하이닉스"]}
        h = C.render_free(data)
        for must in ("관련 기업", "삼성전자", "SK하이닉스", "디램",
                     "최신월 수출", "최신월 수입", "관련 상장사"):
            self.assertIn(must, h)
        self.assertIn("20.0억$", h)

    def test_render_telegram_item(self):
        data = {"mode": "item", "name": "반도체",
                "items": [{"item": "디램", "export_usd": 2e9, "import_usd": 5e8}],
                "companies": ["삼성전자"]}
        t = C.render_telegram(data)
        for must in ("품목 역검색", "삼성전자", "디램", "관련 상장사"):
            self.assertIn(must, t)
        self.assertLessEqual(len(t.encode("utf-16-le")) // 2, 4096)
        t2 = C.render_telegram(data, ai_text="메모리 슈퍼사이클")
        self.assertIn("메모리 슈퍼사이클", t2)

    def test_llm_digest_item(self):
        data = {"mode": "item", "name": "반도체", "companies": ["삼성전자"],
                "items": [{"item": "디램", "export_usd": 2e9, "import_usd": 5e8}]}
        d = C._llm_digest(data)
        self.assertIn("관련 상장사", d)
        self.assertIn("매칭 품목", d)


if __name__ == "__main__":
    unittest.main()
