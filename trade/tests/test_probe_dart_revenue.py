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

    def test_business_report_candidates_fallback_order(self):
        # 최신 정정본 doc 가 014(파일없음)면 원본으로 폴백 — 후보 순서(사업>반기, 최신우선)
        # 로 다운로드 시도(사용자 2026-06-18 현대제철/한화에어로). 정정 포함.
        rows = [
            {"report_nm": "사업보고서 (2025.12)", "rcept_no": "A1", "rcept_dt": "20260319"},
            {"report_nm": "사업보고서 (2025.12) [기재정정]", "rcept_no": "A2",
             "rcept_dt": "20260401"},
            {"report_nm": "반기보고서 (2025.06)", "rcept_no": "H1", "rcept_dt": "20250814"},
        ]
        c = P.business_report_candidates(rows)
        self.assertEqual([x["rcept_no"] for x in c], ["A2", "A1", "H1"])  # 정정→원본→반기
        self.assertEqual(P.pick_business_report(rows)["rcept_no"], "A2")  # 기존=최신 보존


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

    def test_products_converged_shapes(self):
        # G1 — --wide 로 수렴 확인된 실제 매출표 형태 추출 가드.
        # 셀트리온형: 사업부문|매출유형|품목|매출액|비율
        t1 = ("<TABLE><TR><TD>사업부문</TD><TD>매출유형</TD><TD>품목</TD>"
              "<TD>매출액</TD><TD>비율</TD></TR>"
              "<TR><TD>바이오</TD><TD>제품</TD><TD>램시마</TD><TD>1,000,000</TD><TD>40.0%</TD></TR>"
              "<TR><TD>바이오</TD><TD>제품</TD><TD>트룩시마</TD><TD>500,000</TD><TD>20.0%</TD></TR>"
              "<TR><TD>합계</TD><TD>-</TD><TD>-</TD><TD>2,500,000</TD><TD>100.0%</TD></TR></TABLE>")
        ps = P.products_from_rows(P.parse_table_block(t1))
        self.assertEqual([p["name"] for p in ps], ["램시마", "트룩시마"])
        self.assertEqual(ps[0]["share_pct"], 40.0)
        self.assertEqual(ps[0]["amount"], 1_000_000)
        # 매출액(비율) 병합 헤더/셀 — 한쪽 컬럼에 금액·비율 동시
        t2 = ("<TABLE><TR><TD>품목</TD><TD>매출액(비율)</TD></TR>"
              "<TR><TD>HBM</TD><TD>1,234,567 (56.7%)</TD></TR>"
              "<TR><TD>DDR5</TD><TD>700,000 (32.1%)</TD></TR></TABLE>")
        ps2 = P.products_from_rows(P.parse_table_block(t2))
        self.assertEqual([p["name"] for p in ps2], ["HBM", "DDR5"])
        self.assertEqual(ps2[0]["share_pct"], 56.7)
        self.assertEqual(ps2[0]["amount"], 1_234_567)

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
        # '기타'는 잔여 매출 품목으로 포함(2026-06-18 — 합계/소계/총계/계만 제외)
        self.assertIn(50.4, shares)


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
        self.assertFalse(P._is_texty("소 계"))    # 공백 변형 소계 (대한항공 부문소계)
        self.assertFalse(P._is_texty("합 계"))
        self.assertFalse(P._is_texty("1,000,000"))
        self.assertFalse(P._is_texty("-"))

    def test_non_product_label_blocklist(self):
        # audit '이름 의심' 오매핑 차단 (사용자 2026-06-18) — 실제 출력 문자열로 가드.
        # ⛔ 차단(제품 아님): 손익라인·매출 대조차감·연결내부거래·비율주석·총계 leak.
        for bad in ("영업손익", "당기순손익", "계속사업손익", "기타포괄손익",
                    "매출에누리", "매출할인", "매출조정", "차감매출(매출처원재료매입분)",
                    "내부거래 매출액 제거", "연구개발비 / 매출액 비율", "매출액 대비 비율",
                    "매출 발생 여부", "총 매출액", "전체 매출", "매출액 계", "제품매출계",
                    "수익(매출액)", "매출액(백만원)", "매출액(비율)"):
            self.assertTrue(P._is_non_product_label(bad), f"비제품 미차단: {bad}")
            self.assertFalse(P._is_texty(bad), f"_is_texty 통과(오매핑): {bad}")
        # ✅ 보존(정상 매출구성 카테고리·구체 품목) — '빈칸>오매핑'이라도 진짜 제품은 유지.
        for good in ("상품매출", "제품매출", "용역매출", "임대매출", "기타매출",
                     "수출매출", "내수매출기타", "연결대상매출", "별도매출액",
                     "DRAM", "TB 3103외", "골판지원단"):
            self.assertFalse(P._is_non_product_label(good), f"정상 품목 오차단: {good}")
            self.assertTrue(P._is_texty(good), f"정상 품목 _is_texty 탈락: {good}")


class CollectTests(unittest.TestCase):
    """--wide 케이스 수집·분류 헬퍼 (헤더 시그니처 그룹핑)."""

    def test_collect_score(self):
        rows = P.parse_table_block(_TABLE)
        self.assertGreaterEqual(P.collect_score(rows), 4)
        self.assertEqual(P.collect_score([["가나", "다라"]]), 0)

    def test_collect_score_excludes_accounting(self):
        # 2026-06-17 정밀화 — 회계 주석/정책 표(추정내용연수·재무제표·감가상각)는 0.
        for acct in (
            "<TABLE><TR><TD>구분</TD><TD>추정내용연수</TD></TR>"
            "<TR><TD>건물</TD><TD>40년</TD></TR></TABLE>",
            "<TABLE><TR><TD>다음은 연결재무제표 작성에 적용된</TD></TR>"
            "<TR><TD>감가상각</TD><TD>정액법</TD></TR></TABLE>",
        ):
            self.assertEqual(P.collect_score(P.parse_table_block(acct)), 0)

    def test_collect_score_excludes_purchase(self):
        # 2026-06-18 기아 raw — '매입유형/매입액/주요매입처' = 원자재 매입표(매입처
        # 현대모비스), 매출구성 아님. 매입 신호+매출 없음 → 0.
        buy = ("<TABLE><TR><TD>구분</TD><TD>매입유형</TD><TD>주요품목</TD>"
               "<TD>매입액</TD><TD>비중</TD><TD>주요 매입처</TD></TR>"
               "<TR><TD>국내공장</TD><TD>부품</TD><TD>엔진,미션,모듈</TD>"
               "<TD>487,286</TD><TD>91.4%</TD><TD>현대모비스</TD></TR></TABLE>")
        self.assertEqual(P.collect_score(P.parse_table_block(buy)), 0)

    def test_collect_score_excludes_pnl(self):
        # 2026-06-18 audit — 손익계산서(매출원가+매출총이익+영업이익 세로) 표는 0 (매출
        # 구성 표 아님). 항목이 흩어져 rows 전체 검사. POSCO형(매출+영업이익만)은 통과.
        pnl = ("<TABLE><TR><TD>구분</TD><TD>금액</TD></TR>"
               "<TR><TD>매출액</TD><TD>1,000</TD></TR>"
               "<TR><TD>매출원가</TD><TD>700</TD></TR>"
               "<TR><TD>매출총이익</TD><TD>300</TD></TR>"
               "<TR><TD>영업이익</TD><TD>100</TD></TR></TABLE>")
        self.assertEqual(P.collect_score(P.parse_table_block(pnl)), 0)
        posco = ("<TABLE><TR><TD>사업부문</TD><TD>매출액</TD><TD>영업이익</TD>"
                 "<TD>비중</TD></TR>"
                 "<TR><TD>철강</TD><TD>5,000</TD><TD>400</TD><TD>80%</TD></TR></TABLE>")
        self.assertGreater(P.collect_score(P.parse_table_block(posco)), 0)  # 매출구성표 유지

    def test_parse_table_grid_merge(self):
        # rowspan/colspan 전파 — 병합셀 값 복제로 컬럼 정렬(대한항공 클래스).
        t = ("<TABLE><TR><TD ROWSPAN=2>A</TD><TD COLSPAN=2>2025</TD></TR>"
             "<TR><TD>매출액</TD><TD>비율</TD></TR>"
             "<TR><TD>B</TD><TD>100</TD><TD>50%</TD></TR></TABLE>")
        g = P.parse_table_grid(t)
        self.assertEqual(g[0], ["A", "2025", "2025"])     # colspan 복제
        self.assertEqual(g[1], ["A", "매출액", "비율"])   # rowspan 전파(A)
        self.assertEqual(g[2], ["B", "100", "50%"])
        # 단순표(병합 없음)는 parse_table_block 과 동일 → 회귀 0
        s = "<TABLE><TR><TD>품목</TD><TD>비중</TD></TR><TR><TD>DRAM</TD><TD>60%</TD></TR></TABLE>"
        self.assertEqual(P.parse_table_grid(s), P.parse_table_block(s))

    def test_products_from_grid_multiperiod(self):
        # 다기간(매출액|비율 ×연도) → 최신연도(첫 비율)만, 연도별 중복합산(760%) 차단.
        t = ("<TABLE>"
             "<TR><TD ROWSPAN=2>구분</TD><TD COLSPAN=2>2025</TD><TD COLSPAN=2>2024</TD></TR>"
             "<TR><TD>매출액</TD><TD>비율</TD><TD>매출액</TD><TD>비율</TD></TR>"
             "<TR><TD>여객</TD><TD>900</TD><TD>90%</TD><TD>800</TD><TD>88%</TD></TR>"
             "<TR><TD>화물</TD><TD>100</TD><TD>10%</TD><TD>120</TD><TD>12%</TD></TR></TABLE>")
        ps = P.products_from_grid(P.parse_table_grid(t))
        got = {p["name"]: p["share_pct"] for p in ps}
        self.assertEqual(got.get("여객"), 90.0)        # 2025 비율만(2024 88 미사용)
        self.assertEqual(got.get("화물"), 10.0)
        self.assertEqual(sum(p["share_pct"] for p in ps), 100.0)  # 188 아님

    def test_products_from_grid_segment_metric_rows(self):
        # 부문별 표(구분: 매출액/영업이익/내부매출액) — 매출액 행만, 이름=부문 컬럼
        # (현대차 005380 raw 2026-06-18). 영업이익·내부매출 비중 합산 → sanity 폴백 차단.
        t = ("<TABLE><TBODY>"
             "<TR><TD COLSPAN=2 ROWSPAN=2>주요제품</TD><TD ROWSPAN=2>구분</TD>"
             "<TD COLSPAN=2>2025</TD><TD COLSPAN=2>2024</TD></TR>"
             "<TR><TD>금액</TD><TD>비중</TD><TD>금액</TD><TD>비중</TD></TR>"
             "<TR><TD ROWSPAN=3>차량부문</TD><TD ROWSPAN=3>자동차 등</TD>"
             "<TD>매출액</TD><TD>145,631,818</TD><TD>78.2</TD><TD>136,725,011</TD><TD>78.1</TD></TR>"
             "<TR><TD>영업이익</TD><TD>8,470,939</TD><TD>73.9</TD><TD>11,411,499</TD><TD>80.1</TD></TR>"
             "<TR><TD>내부매출액</TD><TD>(87,248,014)</TD><TD>97.3</TD><TD>(85,166,239)</TD><TD>97.6</TD></TR>"
             "<TR><TD ROWSPAN=3>기타부문</TD><TD ROWSPAN=3>철도제작 등</TD>"
             "<TD>매출액</TD><TD>10,389,879</TD><TD>5.6</TD><TD>10,059,492</TD><TD>5.7</TD></TR>"
             "<TR><TD>영업이익</TD><TD>832,869</TD><TD>7.2</TD><TD>1,032,844</TD><TD>7.3</TD></TR>"
             "<TR><TD>내부매출액</TD><TD>(2,064,276)</TD><TD>2.3</TD><TD>(1,756,849)</TD><TD>2.0</TD></TR>"
             "</TBODY></TABLE>")
        got = {p["name"]: p["share_pct"] for p in P.products_from_grid(P.parse_table_grid(t))}
        self.assertEqual(got.get("자동차 등"), 78.2)     # 매출액 행 비중만
        self.assertEqual(got.get("철도제작 등"), 5.6)
        self.assertNotIn("영업이익", got)                # 지표 행 제외
        self.assertNotIn("내부매출액", got)
        self.assertNotIn("매출액", got)                  # 구분 컬럼이 이름 아님

    def test_products_from_grid_multicompany_break(self):
        # 대한항공 2026-06-18 — 연결 표(모회사+자회사 각 100%): 회사별 합계 행('합 계')
        # 에서 break → 모회사만. '기타'는 품목 포함, 진에어(자회사) 제외. 비중합 100%.
        t = ("<TABLE>"
             "<TR><TD COLSPAN=3 ROWSPAN=2>구분</TD><TD ROWSPAN=2>주요사업</TD>"
             "<TD COLSPAN=2>2025</TD><TD COLSPAN=2>2024</TD></TR>"
             "<TR><TD>매출액</TD><TD>비율</TD><TD>매출액</TD><TD>비율</TD></TR>"
             "<TR><TD ROWSPAN=3>대한항공</TD><TD COLSPAN=2 ROWSPAN=2>항공운송</TD>"
             "<TD>여객국제</TD><TD>93744</TD><TD>56.8%</TD><TD>93059</TD><TD>57.7%</TD></TR>"
             "<TR><TD>기타</TD><TD>14683</TD><TD>43.2%</TD><TD>13334</TD><TD>42.3%</TD></TR>"
             "<TR><TD COLSPAN=3>합 계</TD><TD>165019</TD><TD>100.0%</TD><TD>161166</TD><TD>100.0%</TD></TR>"
             "<TR><TD ROWSPAN=2>진에어</TD><TD COLSPAN=2 ROWSPAN=2>항공운송</TD>"
             "<TD>여객국내</TD><TD>2407</TD><TD>17.4%</TD><TD>2649</TD><TD>18.2%</TD></TR></TABLE>")
        got = {p["name"]: p["share_pct"] for p in P.products_from_grid(P.parse_table_grid(t))}
        self.assertEqual(got.get("여객국제"), 56.8)
        self.assertEqual(got.get("기타"), 43.2)         # '기타' = 품목 포함
        self.assertNotIn("여객국내", got)                # 진에어(합계 행 후) 제외
        self.assertTrue(all("합" not in n for n in got))
        self.assertEqual(sum(got.values()), 100.0)       # 모회사 100%(진에어 미합산)

    def test_products_from_grid_single_period_none(self):
        # 단일연도(매출액1·비율1) → None → 호출부 products_from_rows 폴백(회귀 0)
        t = ("<TABLE><TR><TD>품목</TD><TD>매출액</TD><TD>비중</TD></TR>"
             "<TR><TD>DRAM</TD><TD>1000</TD><TD>60%</TD></TR></TABLE>")
        self.assertIsNone(P.products_from_grid(P.parse_table_grid(t)))

    def test_best_revenue_table_skips_accounting_only(self):
        # 회계 표만 있는 문서 → 매출표 미발견(가짜 시그니처 방지).
        doc = ("<TABLE><TR><TD>구분</TD><TD>추정내용연수</TD></TR>"
               "<TR><TD>건물</TD><TD>40년</TD></TR></TABLE>")
        self.assertEqual(P.best_revenue_table(doc), (None, 0))

    def test_header_signature(self):
        sig = P.header_signature(P.parse_table_block(_TABLE))
        self.assertEqual(sig[0], "사업부문")   # 키워드 헤더 행 채택
        self.assertIn("품목", sig)

    def test_best_revenue_table(self):
        best, sc = P.best_revenue_table(_DOC)
        self.assertIsNotNone(best)
        self.assertGreater(sc, 0)
        _t, rows = best
        self.assertEqual(P.header_signature(rows)[0], "사업부문")

    def test_best_revenue_table_none(self):
        self.assertEqual(P.best_revenue_table("<p>표 없음</p>"), (None, 0))

    def test_signature_groups_distinct_shapes(self):
        # 삼성형(부문/품목/매출액/비중) ≠ POSCO형(사업부문/자산/매출/영업이익)
        posco = ("<TABLE><TR><TD>사업부문</TD><TD>자산</TD><TD>매출</TD>"
                 "<TD>영업이익</TD></TR>"
                 "<TR><TD>철강부문</TD><TD>65</TD><TD>59</TD><TD>1.9</TD></TR>"
                 "<TR><TD>인프라</TD><TD>23</TD><TD>42</TD><TD>1.1</TD></TR></TABLE>")
        s1 = P.header_signature(P.parse_table_block(_TABLE))
        s2 = P.header_signature(P.parse_table_block(posco))
        self.assertNotEqual(s1, s2)


if __name__ == "__main__":
    unittest.main()
