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

    def test_fill_amount_based_share(self):
        # C4 — 비중 전무 + 금액 → 매출액 기반 비중(한미·기아 류)
        got = {p["name"]: p["share_pct"] for p in D._fill_amount_based_share(
            [{"name": "반도체장비", "amount": 800, "share_pct": None},
             {"name": "Conversion Kit", "amount": 200, "share_pct": None}])}
        self.assertEqual(got["반도체장비"], 80.0)
        self.assertEqual(got["Conversion Kit"], 20.0)
        # 비중이 이미 있으면 원본 유지(혼합 표 안 건드림)
        keep = D._fill_amount_based_share(
            [{"name": "A", "amount": 800, "share_pct": 50.0},
             {"name": "B", "amount": 200, "share_pct": None}])
        self.assertEqual(keep[0]["share_pct"], 50.0)
        self.assertIsNone(keep[1]["share_pct"])
        # 금액 1개뿐 → 원본(비중 산출 안 함)
        one = D._fill_amount_based_share([{"name": "A", "amount": 800, "share_pct": None}])
        self.assertIsNone(one[0]["share_pct"])

    def test_needs_rebuild(self):
        # 변경분만 — rcept 같고 products 있으면 skip(False), 아니면 재파싱(True)
        self.assertTrue(D._needs_rebuild(None, "r1"))
        self.assertTrue(D._needs_rebuild({"rcept_no": "r1", "products": []}, "r1"))
        self.assertTrue(D._needs_rebuild({"rcept_no": "r1", "products": [{"name": "x"}]}, "r2"))
        self.assertFalse(D._needs_rebuild({"rcept_no": "r1", "products": [{"name": "x"}]}, "r1"))

    def test_failures_save_load_roundtrip(self):
        # 사용자 2026-06-18 — 파싱 실패 가시성(어느 상장사가 왜 실패했나)
        tmp = tempfile.mkdtemp()
        fails = [{"code": "005930", "company": "삼성전자", "reason": "parse_empty",
                  "rcept_no": "2026"},
                 {"code": "900110", "company": "스팩", "reason": "no_report",
                  "rcept_no": ""}]
        with mock.patch.dict(os.environ, {"TRADE_DATA_DIR": tmp}):
            D._save_failures(fails)
            self.assertEqual(D.load_failures(), fails)

    def test_failures_load_absent_graceful(self):
        with mock.patch.dict(os.environ, {"TRADE_DATA_DIR": tempfile.mkdtemp()}):
            self.assertEqual(D.load_failures(), [])

    def test_rescan_failures_only_uninventoried(self):
        # --rescan-failures = 전 상장사 − 인벤토리 수록분만 재시도(전수 재스캔 회피)
        import trade.scripts.probe_dart_revenue as P
        with mock.patch.object(D, "all_listed_codes",
                               return_value=["000001", "000002", "000003"]), \
                mock.patch.object(D, "load_inventory",
                                  return_value={"000001": {"products": [{"name": "x"}]}}), \
                mock.patch.object(D, "refresh_inventory",
                                  return_value={"built": 0, "failed": 2}) as ri, \
                mock.patch.object(P, "_load_env"), \
                mock.patch.dict(os.environ, {"DART_API_KEY": "k"}):
            self.assertEqual(D.main(["--rescan-failures"]), 0)
        self.assertEqual(sorted(ri.call_args.kwargs["codes"]), ["000002", "000003"])

    def test_reparse_suspect_targets_name_doubt_only(self):
        # --reparse-suspect = audit '이름 의심'(오매핑) 종목만 force 재파싱(전수 force 회피).
        # '비중 전무'·'과소추출'은 이름 정상이라 제외(사용자 2026-06-18).
        import trade.scripts.probe_dart_revenue as P
        suspects = [
            {"code": "000001", "company": "A", "n": 5, "reasons": ["이름 의심: 영업손익"]},
            {"code": "000002", "company": "B", "n": 1, "reasons": ["제품 1개(과소추출 의심)"]},
            {"code": "000003", "company": "C", "n": 8, "reasons": ["비중 전무"]},
        ]
        with mock.patch.object(D, "audit_inventory", return_value=suspects), \
                mock.patch.object(D, "refresh_inventory",
                                  return_value={"built": 1, "failed": 0}) as ri, \
                mock.patch.object(P, "_load_env"), \
                mock.patch.dict(os.environ, {"DART_API_KEY": "k"}):
            self.assertEqual(D.main(["--reparse-suspect"]), 0)
        self.assertEqual(ri.call_args.kwargs["codes"], ["000001"])   # 이름 의심만
        self.assertTrue(ri.call_args.kwargs["force"])                # force 재파싱

    def test_reparse_stale_drains_by_parser_version(self):
        # 파서 세대(pv) 오른 뒤 stale 항목만 budget 개 재파싱 — 코드 개선 자동 소급
        # (사용자 2026-06-18). None=제거(빈칸>stale), 성공=교체, current pv=skip.
        import json as _j
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        cur = D._PARSER_VERSION
        inv = {
            "001": {"code": "001", "products": [{"name": "영업손익"}], "pv": cur - 1},
            "002": {"code": "002", "products": [{"name": "DRAM"}], "pv": cur - 1},
            "003": {"code": "003", "products": [{"name": "칩"}], "pv": cur},   # current
            "004": {"code": "004", "products": [{"name": "x"}]},               # pv 없음=0
        }
        (d / "dart_revenue_inventory.json").write_text(_j.dumps(inv), encoding="utf-8")

        def fake_fetch(code, key):
            if code == "001":
                return None     # 새 파서 추출 0(전부 회계라인) → 제거
            return {"code": code, "products": [{"name": "신제품"}], "pv": cur}

        with mock.patch.object(D, "_data_dir", return_value=d), \
                mock.patch.object(D, "fetch_company_products", side_effect=fake_fetch), \
                mock.patch("time.sleep"):
            res = D.reparse_stale_inventory(budget=2, api_key="k")
        self.assertEqual(res["stale"], 3)            # 001·002·004 (003 은 current)
        self.assertEqual(res["remaining"], 1)        # budget 2 → 004 다음 런
        new = _j.loads((d / "dart_revenue_inventory.json").read_text(encoding="utf-8"))
        self.assertNotIn("001", new)                 # None → 제거(빈칸 우선)
        self.assertEqual(new["002"]["pv"], cur)      # 재파싱 교체(새 pv)
        self.assertIn("003", new)                    # current 그대로
        self.assertIn("004", new)                    # budget 밖 — 다음 런 드레인

    def test_no_revenue_breakdown_classifier(self):
        # 금융·스팩·리츠류 = 구조적 제외(파서 버그 아님), 제조/일반기업 = 보강 대상
        for fin in ("신한은행", "NH투자증권", "삼성화재해상보험", "KB금융",
                    "케이비캐피탈", "삼성카드", "카카오뱅크", "동양생명",
                    "에이티넘인베스트", "스톤브릿지벤처스", "롯데리츠",
                    "한국토지신탁", "교보15호스팩", "미래에셋비전기업인수목적1호"):
            self.assertTrue(D._no_revenue_breakdown(fin), fin)
        for real in ("NAVER", "현대자동차", "케이티앤지", "하이브", "엘앤에프",
                     "현대제철", "플럼라인생명과학", "지에프씨생명과학",
                     "HD현대", "제일파마홀딩스", "코스메카코리아"):
            self.assertFalse(D._no_revenue_breakdown(real), real)   # 보강대상 오제외 0

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
