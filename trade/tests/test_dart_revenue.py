"""trade.dart_revenue — G1 인벤토리 빌더 가드.

추출 primitives(parse_table_block/products_from_rows/best_revenue_table)는 프로브
테스트(test_probe_dart_revenue)가 커버. 여기선 G1 wrapper(키 부재 graceful·인벤토리
조립·저장)만 — 네트워크 없이 monkeypatch.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from trade import dart_revenue as D


class DartRevenueTests(unittest.TestCase):
    def test_fetch_no_key_returns_none(self):
        # 키 없으면 네트워크 전에 None (샌드박스·creds 부재 graceful).
        self.assertIsNone(D.fetch_company_products("005930", api_key=""))

    def test_clean_products(self):
        raw = [{"name": "매출액", "share_pct": None},
               {"name": "DRAM 등", "share_pct": 39.0},
               {"name": "제7조 (허위, 과장된 정보제공)", "share_pct": None},
               {"name": "2024년(제40기)", "share_pct": None},
               {"name": "양극재(양극활물질)", "share_pct": 96.7}]
        names = [p["name"] for p in D._clean_products(raw)]
        self.assertIn("DRAM", names)               # '등' 꼬리 제거
        self.assertIn("양극재(양극활물질)", names)
        self.assertNotIn("매출액", names)           # 헤더 토큰 제거
        self.assertTrue(all("제7조" not in n for n in names))   # 약관 조항 제거
        self.assertTrue(all("2024년" not in n for n in names))  # 기수 라벨 제거

    def test_clean_products_drops_pnl_and_subtotals(self):
        # 2026-06-18 audit: 손익계산서 항목·합계/소계 행이 제품으로 누수(비중합 200%
        # ·이름 의심 다수)를 차단. 매출유형(상품/용역매출)은 유지(G2 단계 자료).
        raw = [{"name": "II. 매출원가"}, {"name": "매출총이익"}, {"name": "영업이익(손실)"},
               {"name": "기타포괄손익"}, {"name": "매출액 합계"}, {"name": "연결매출액"},
               {"name": "매출총계"}, {"name": "내부매출 제거"},
               {"name": "DRAM"}, {"name": "상품매출"}]
        names = [p["name"] for p in D._clean_products(raw)]
        self.assertIn("DRAM", names)
        self.assertIn("상품매출", names)        # 매출유형 = 유지(품목매칭 단계 자료)
        for bad in ("II. 매출원가", "매출총이익", "영업이익(손실)", "기타포괄손익",
                    "매출액 합계", "연결매출액", "매출총계", "내부매출 제거"):
            self.assertTrue(all(bad not in n for n in names), f"{bad} 미제거")

    def test_audit_inventory(self):
        inv = {
            "A": {"company": "A", "products": [{"name": "DRAM", "share_pct": 39.0},
                                               {"name": "NAND", "share_pct": 61.0}]},
            "B": {"company": "B", "products": []},                       # 제품 0
            "C": {"company": "C", "products": [{"name": "매출액", "share_pct": None}]},
            "D": {"company": "D", "products": [{"name": "X", "share_pct": 10.0}]},
        }
        sus = {s["code"] for s in D.audit_inventory(inv)}
        self.assertNotIn("A", sus)     # 2제품·비중합 100·노이즈無 → clean
        self.assertIn("B", sus)        # 제품 0
        self.assertIn("C", sus)        # 1개+비중전무+이름의심
        self.assertIn("D", sus)        # 1개+비중합 10%

    def test_needs_rebuild(self):
        # 변경분만 — rcept 같고 products 있으면 skip(False), 아니면 재파싱(True)
        self.assertTrue(D._needs_rebuild(None, "r1"))
        self.assertTrue(D._needs_rebuild({"rcept_no": "r1", "products": []}, "r1"))
        self.assertTrue(D._needs_rebuild({"rcept_no": "r1", "products": [{"name": "x"}]}, "r2"))
        self.assertFalse(D._needs_rebuild({"rcept_no": "r1", "products": [{"name": "x"}]}, "r1"))

    def test_build_inventory_assembles_and_saves(self):
        tmp = tempfile.mkdtemp()
        fake = {"code": "005930", "company": "삼성전자", "report": "사업보고서",
                "rcept_no": "1", "products": [{"name": "DRAM", "share_pct": 39.0,
                                               "amount": 1301282}]}

        def _fake(code, key=None):
            return fake if code == "005930" else None

        with mock.patch.dict(os.environ, {"TRADE_DATA_DIR": tmp}), \
                mock.patch.object(D, "fetch_company_products", _fake):
            inv = D.build_inventory(["005930", "000000"], api_key="x", sleep=0)
        self.assertIn("005930", inv)
        self.assertNotIn("000000", inv)              # 실패 종목 생략
        saved = json.loads(open(os.path.join(tmp, "dart_revenue_inventory.json"),
                                encoding="utf-8").read())
        self.assertEqual(saved["005930"]["products"][0]["name"], "DRAM")


if __name__ == "__main__":
    unittest.main()
