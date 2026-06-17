"""trade.scripts.probe_dart_revenue — 순수 파싱 헬퍼 가드.

프로브의 네트워크 경로는 VM 전용(DART 키 필요)이라 샌드박스 미검증이지만,
표/평문 추출 휴리스틱은 합성 DART 마크업으로 검증 가능(검증 게이트 준수).
실 DART 양식이 다르면 프로브가 원문을 저장해 후속 보정 — 본 테스트는
'표가 표준형이면 회사→[제품,비중]이 뽑힌다'는 계약만 고정한다.
"""

import unittest

from trade.scripts import probe_dart_revenue as P

# 표준형 DART 매출구성 표 (대문자 태그, TR/TD) — 삼성식 사업부문+품목.
_TABLE = (
    '<TABLE BORDER="1">'
    "<TR><TD>사업부문</TD><TD>품목</TD><TD>매출액(백만원)</TD><TD>비중</TD></TR>"
    "<TR><TD>반도체</TD><TD>DRAM</TD><TD>1,000,000</TD><TD>33.1%</TD></TR>"
    "<TR><TD>반도체</TD><TD>NAND</TD><TD>500,000</TD><TD>16.5%</TD></TR>"
    "<TR><TD>합계</TD><TD>-</TD><TD>1,500,000</TD><TD>100.0%</TD></TR>"
    "</TABLE>"
)
_DOC = f"<SECTION><TITLE>4. 매출 및 수주상황 (주요 제품)</TITLE>{_TABLE}</SECTION>"


class PickReportTests(unittest.TestCase):
    def test_prefers_annual_then_latest(self):
        rows = [
            {"report_nm": "분기보고서 (2025.03)", "rcept_no": "1", "rcept_dt": "20250515"},
            {"report_nm": "사업보고서 (2024.12)", "rcept_no": "2", "rcept_dt": "20250320"},
            {"report_nm": "반기보고서 (2025.06)", "rcept_no": "3", "rcept_dt": "20250814"},
        ]
        rep = P.pick_business_report(rows)
        self.assertEqual(rep["rcept_no"], "2")  # 사업보고서 우선
        self.assertEqual(rep["kind"], "사업보고서")

    def test_falls_back_to_quarter_when_no_annual(self):
        rows = [
            {"report_nm": "분기보고서 (2025.03)", "rcept_no": "9", "rcept_dt": "20250515"},
            {"report_nm": "분기보고서 (2024.09)", "rcept_no": "8", "rcept_dt": "20241114"},
        ]
        rep = P.pick_business_report(rows)
        self.assertEqual(rep["rcept_no"], "9")  # 최신
        self.assertEqual(rep["kind"], "분기보고서")

    def test_empty(self):
        self.assertIsNone(P.pick_business_report([]))


class TableParseTests(unittest.TestCase):
    def test_extract_and_parse(self):
        tables = P.extract_tables_raw(_DOC)
        self.assertEqual(len(tables), 1)
        rows = P.parse_table_block(tables[0])
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1], ["반도체", "DRAM", "1,000,000", "33.1%"])

    def test_score_revenue_like(self):
        rows = P.parse_table_block(_TABLE)
        self.assertGreaterEqual(P.score_revenue_table(rows), 4)
        self.assertEqual(P.score_revenue_table([["가나", "다라"]]), 0)

    def test_products_from_rows(self):
        rows = P.parse_table_block(_TABLE)
        prods = P.products_from_rows(rows)
        names = [p["name"] for p in prods]
        self.assertEqual(names, ["DRAM", "NAND"])  # 품목 우선, 합계 제외
        self.assertEqual(prods[0]["share_pct"], 33.1)
        self.assertEqual(prods[0]["amount"], 1_000_000)
        self.assertEqual(prods[1]["share_pct"], 16.5)

    def test_segment_only_table_uses_구분(self):
        # 품목 컬럼 없는 세그먼트 표 → 구분/사업부문으로 폴백.
        seg = (
            "<TABLE><TR><TD>구분</TD><TD>비중</TD></TR>"
            "<TR><TD>석유화학</TD><TD>60.0%</TD></TR>"
            "<TR><TD>첨단소재</TD><TD>40.0%</TD></TR></TABLE>"
        )
        prods = P.products_from_rows(P.parse_table_block(seg))
        self.assertEqual([p["name"] for p in prods], ["석유화학", "첨단소재"])


class FlatTests(unittest.TestCase):
    def test_flatten_strips_tags(self):
        self.assertEqual(P.flatten("<P>가  나<BR/>다</P>"), "가 나 다")

    def test_find_sections(self):
        flat = P.flatten(_DOC)
        hits = P.find_revenue_sections(flat)
        self.assertTrue(any(kw in ("매출 및 수주", "주요 제품") for kw, _ in hits))

    def test_products_from_flat(self):
        seg = "주요 제품: DRAM 33.1%, NAND 16.5%, 기타 50.4%"
        prods = P.products_from_flat(seg)
        shares = sorted(p["share_pct"] for p in prods)
        self.assertIn(33.1, shares)
        self.assertIn(16.5, shares)
        # '기타'는 총계류 → 제외
        self.assertNotIn("기타", [p["name"] for p in prods])


class ScalarHelperTests(unittest.TestCase):
    def test_to_pct(self):
        self.assertEqual(P._to_pct("33.1%"), 33.1)
        self.assertEqual(P._to_pct("33.1"), 33.1)
        self.assertEqual(P._to_pct("100.0%"), 100.0)
        self.assertIsNone(P._to_pct("1,234,567"))  # 금액은 비중 아님
        self.assertIsNone(P._to_pct(""))

    def test_to_amount(self):
        self.assertEqual(P._to_amount("1,000,000"), 1_000_000)
        self.assertIsNone(P._to_amount("33.1%"))  # 콤마 없는 소수 = 금액 아님
        self.assertIsNone(P._to_amount("2024"))   # 연도 = 금액 아님

    def test_is_texty(self):
        self.assertTrue(P._is_texty("DRAM"))
        self.assertTrue(P._is_texty("석유화학"))
        self.assertFalse(P._is_texty("합계"))
        self.assertFalse(P._is_texty("1,000,000"))
        self.assertFalse(P._is_texty("-"))


if __name__ == "__main__":
    unittest.main()
