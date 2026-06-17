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
