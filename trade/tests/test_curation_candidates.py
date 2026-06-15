"""trade.scripts.curation_candidates — 미매핑 고수출 품목 큐레이션 후보 (사용자
2026-06-15). 저장 by_mti 재사용·재스캔 0. companies_for(수동)·channel_companies_for
(채널) 둘 다 없는 고수출 품목만, 후보 0 이면 무음."""

import unittest

from trade import mti_companies as mc
from trade.scripts import curation_candidates as cc


class CurationCandidateTests(unittest.TestCase):
    def setUp(self):
        self._cf, self._ch = mc.companies_for, mc.channel_companies_for
        mc.companies_for = lambda n: ["삼성전자"] if "반도체" in n else []
        mc.channel_companies_for = lambda n, p: ["LG에너지솔루션"] if "배터리" in n else []

    def tearDown(self):
        mc.companies_for, mc.channel_companies_for = self._cf, self._ch

    def test_filters_to_unmapped_highvalue(self):
        by_mti = {
            "1": {"name": "반도체", "industry": "전자", "months": {"2026-05": 1e10}},     # 수동→제외
            "2": {"name": "배터리", "industry": "2차전지", "months": {"2026-05": 9e9}},   # 채널→제외
            "3": {"name": "합성수지", "industry": "화학",
                  "months": {"2026-04": 2e9, "2026-05": 8e9}},                          # 미매핑 고수출→후보
            "4": {"name": "소형품목", "industry": "기타", "months": {"2026-05": 1e8}},   # <min→제외
        }
        cands = cc.find_candidates(by_mti, [], top=12, min_usd=5e8)
        self.assertEqual([c[0] for c in cands], ["합성수지"])
        self.assertEqual(cands[0][2], 8e9)   # 최신월 수출액

    def test_ranks_by_value_and_caps_top(self):
        by_mti = {str(i): {"name": f"품목{i}", "industry": "x",
                           "months": {"2026-05": (10 - i) * 1e9}} for i in range(6)}
        cands = cc.find_candidates(by_mti, [], top=3, min_usd=1e8)
        self.assertEqual(len(cands), 3)                       # top 3
        self.assertEqual([c[0] for c in cands], ["품목0", "품목1", "품목2"])  # 값 내림차순

    def test_compose_silent_on_empty(self):
        self.assertIsNone(cc.compose([]))

    def test_compose_formats_header_and_label(self):
        body = cc.compose([("합성수지", "화학", 8e9)])
        self.assertIn("큐레이션 후보 1건", body)
        self.assertIn("합성수지", body)
        self.assertIn("80.0억$", body)


if __name__ == "__main__":
    unittest.main()
