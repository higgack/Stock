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
                "exposure": [{"item": "디램", "industry": "반도체", "latest_usd": 2e9}]}
        h = C.render_free(data)
        for must in ("삼성전자", "005930", "DRAM", "디램", "제품 구성", "관세청"):
            self.assertIn(must, h)
        self.assertIn("39.0%", h)
        self.assertIn("억$", h)            # 노출 수출액 억$ 포맷

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
                "exposure": [{"item": "디램", "industry": "반도체", "latest_usd": 2e9}]}
        t = C.render_telegram(data)
        for must in ("삼성전자", "DRAM", "디램", "제품 구성", "관세청"):
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
        items = [x["item"] for x in C._company_exposure("삼성전자", by_mti, [])]
        self.assertIn("디램", items)
        self.assertNotIn("라면", items)

    def test_empty_name(self):
        self.assertEqual(C._company_exposure("", {"1": {"name": "x"}}, []), [])


if __name__ == "__main__":
    unittest.main()
