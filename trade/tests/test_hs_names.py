"""HS부호 → 한글품목명 사전 (무실적 코드까지) — 로더·변환기·검색 폴백 가드
(사용자 2026-06-22)."""
import os
import tempfile
import unittest

from trade import hs_names as HN


class LoaderTests(unittest.TestCase):
    def _tsv(self, body: str) -> str:
        d = tempfile.mkdtemp()
        p = os.path.join(d, "hs_names.tsv")
        open(p, "w", encoding="utf-8").write(body)
        return p

    def test_load_and_exact(self):
        p = self._tsv("7108131010\t금(기타 반제품)\n710813\t금\n")
        d = HN.load_hs_names(p)
        self.assertEqual(d["7108131010"], "금(기타 반제품)")
        self.assertEqual(HN.hs_name("7108.13-1010", d), "금(기타 반제품)")  # 점·대시 정규화
        self.assertEqual(HN.hs_name("7108131010", d), "금(기타 반제품)")

    def test_prefix_fallback(self):
        # HS10 이 사전에 없으면 더 짧은 prefix(HS6)로 폴백
        p = self._tsv("710813\t금\n")
        d = HN.load_hs_names(p)
        self.assertEqual(HN.hs_name("7108139090", d), "금")    # 10 없음 → 6 폴백
        self.assertIsNone(HN.hs_name("9999999999", d))         # 어떤 prefix 도 없음

    def test_missing_file_graceful(self):
        self.assertEqual(HN.load_hs_names("/no/such/file.tsv"), {})
        self.assertIsNone(HN.hs_name("7108131010", {}))


class ConverterTests(unittest.TestCase):
    def test_csv_autodetect(self):
        from trade.scripts.build_hs_names import build
        d = tempfile.mkdtemp()
        src = os.path.join(d, "src.csv")
        # 헤더명 힌트(HS부호·한글품목명) — 영문열은 무시돼야
        open(src, "w", encoding="utf-8").write(
            "HS부호,한글품목명,영문품목명\n"
            "7108131010,금(기타),Gold other\n"
            "8542310000,프로세서와 컨트롤러,Processors\n"
            "0000000000,,Empty name skipped\n")     # 한글명 없음 → skip
        out = os.path.join(d, "hs_names.tsv")
        n = build(src, out)
        self.assertEqual(n, 2)
        loaded = HN.load_hs_names(out)
        self.assertEqual(loaded["7108131010"], "금(기타)")
        self.assertEqual(loaded["8542310000"], "프로세서와 컨트롤러")
        self.assertNotIn("0000000000", loaded)

    def test_stat_autodetect_no_header_hint(self):
        # 헤더 힌트 없어도 숫자비율(코드)·한글최다(명) 통계로 탐지
        from trade.scripts.build_hs_names import build
        d = tempfile.mkdtemp()
        src = os.path.join(d, "src.csv")
        open(src, "w", encoding="utf-8").write(
            "col1,col2\n7108131010,금(기타 반제품)\n8542310000,프로세서\n")
        out = os.path.join(d, "hs_names.tsv")
        self.assertEqual(build(src, out), 2)
        self.assertEqual(HN.load_hs_names(out)["8542310000"], "프로세서")


class SearchFallbackTests(unittest.TestCase):
    def test_hs_code_search_uses_dictionary_when_no_leaf(self):
        # 무실적 코드(leaf 스냅샷에 없음) → 사전 폴백으로 한글명 표시
        from trade import company_report as C
        import trade.hs_names as hn
        orig = hn.load_hs_names
        hn.load_hs_names = lambda path=None: {"7108131090": "금(무실적 세분)"}
        try:
            by_mti = {"111100": {"name": "금", "industry": "기타",
                                 "months": {"2026-05": 1e8}}}
            res = C._hs_code_search("7108.13-1090", by_mti, [], None, hs_leaf={})
            self.assertEqual(res["hs_name"], "금(무실적 세분)")   # 사전 폴백
            self.assertIsNone(res["hs_pv"])                       # leaf 없음 → 분해 없음
            h = C.render_free(res)
            self.assertIn("금(무실적 세분)", h)
        finally:
            hn.load_hs_names = orig


if __name__ == "__main__":
    unittest.main()
