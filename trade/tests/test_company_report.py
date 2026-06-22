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
                     "🚢 수출", "📥 수입", ">YoY<", ">ΔYoY<", ">MoM<", ">ΔMoM<"):
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

    def test_alert_items_mirror_company_view(self):
        # 회사별 탭과 동일 소스: BeOn alerts 에서 그 회사가 태깅된 품목 (사용자 2026-06-18)
        alerts = [
            {"item": "디램", "stocks": ["삼성전자", "SK하이닉스"]},
            {"item": "디램", "stocks": ["삼성전자"]},          # 중복 품목 → dedupe
            {"item": "디램모듈", "stocks": ["삼성전자"]},
            {"item": "라면", "stocks": ["농심"]},              # 삼성전자 아님 → 제외
        ]
        items = C._company_alert_items("삼성전자", alerts)
        self.assertEqual(items, ["디램", "디램모듈"])
        self.assertEqual(C._company_alert_items("삼성전자", []), [])

    def test_exposure_union_no_dup_no_false_attach(self):
        # 회사별 alert 품목(풍부) + 큐레이션 union, 관세청 수치 정확일치만 부착.
        by_mti = {
            "831110": {"name": "디램", "industry": "반도체",
                       "months": {"2026-05": 186.1e8}},
            "831200": {"name": "낸드", "industry": "반도체",
                       "months": {"2026-05": 17.2e8}},
        }
        by_imp = {"831110": {"months": {"2026-05": 5e8}}}
        alerts = [{"item": "디램", "stocks": ["삼성전자"]},
                  {"item": "디램모듈", "stocks": ["삼성전자"]}]   # 관세청 미매칭
        rows = C._company_exposure("삼성전자", by_mti, [], by_imp, alerts)
        names = [r["item"] for r in rows]
        self.assertEqual(names.count("디램"), 1)               # 중복 없음
        self.assertIn("디램모듈", names)                       # 채널 전용 품목도 노출
        self.assertIn("낸드", names)                           # 큐레이션 보강
        dram = next(r for r in rows if r["item"] == "디램")
        self.assertEqual(dram["export_usd"], 186.1e8)
        self.assertEqual(dram["import_usd"], 5e8)
        dmod = next(r for r in rows if r["item"] == "디램모듈")
        self.assertIsNone(dmod["export_usd"])                  # 엉뚱한 값 부착 금지
        self.assertLess(names.index("디램"), names.index("디램모듈"))  # 값 우선 정렬

    def test_exposure_curation_only_fallback(self):
        # alerts 없어도(None) 기존 큐레이션 경로 동작 (회귀 가드)
        by_mti = {"831110": {"name": "디램", "industry": "반도체",
                             "months": {"2026-05": 2e9}}}
        rows = C._company_exposure("삼성전자", by_mti, [])
        self.assertTrue(any(r["item"] == "디램" for r in rows))

    def test_exposure_has_yoy_mom_metrics_both_directions(self):
        # 사용자 2026-06-18 — 노출 표에 수출·수입 YoY·ΔYoY·MoM·ΔMoM (산업트렌드 동일식)
        exp_months = {f"2025-{m:02d}": 100 + m for m in range(1, 13)}
        exp_months.update({f"2026-{m:02d}": 120 + m for m in range(1, 6)})
        imp_months = {f"2025-{m:02d}": 50 + m for m in range(1, 13)}
        imp_months.update({f"2026-{m:02d}": 40 + m for m in range(1, 6)})
        by_mti = {"831110": {"name": "디램", "industry": "반도체", "months": exp_months}}
        by_imp = {"831110": {"months": imp_months}}
        rows = C._company_exposure("삼성전자", by_mti, [], by_imp)
        dram = next(r for r in rows if r["item"] == "디램")
        for k in ("export_yoy", "export_dyoy", "export_mom", "export_dmom",
                  "import_yoy", "import_dyoy", "import_mom", "import_dmom"):
            self.assertIn(k, dram)
        self.assertIsNotNone(dram["export_yoy"])
        self.assertIsNotNone(dram["import_yoy"])     # 수입쪽도
        self.assertGreater(dram["export_yoy"], 0)    # 수출 100+→120+ 상승
        self.assertLess(dram["import_yoy"], 0)       # 수입 50+→40+ 하락
        h = C.render_free({"mode": "company", "name": "삼성전자", "code": "005930",
                           "products": [], "exposure": rows})
        for must in ("🚢 수출", "📥 수입", ">YoY<", ">ΔYoY<", ">MoM<", ">ΔMoM<"):
            self.assertIn(must, h)

    def test_month_metrics_empty_graceful(self):
        self.assertEqual(C._month_metrics(None), {})
        self.assertEqual(C._month_metrics({}), {})


class GatherProductSourceTests(unittest.TestCase):
    """제품 구성 = 빌드된 인벤토리 우선, 미수록만 라이브 (사용자 2026-06-18)."""

    def test_inventory_first_skips_live(self):
        from unittest import mock
        inv = {"034220": {"company": "LG디스플레이",
                          "products": [{"name": "OLED", "share_pct": 60.0}]}}

        class _Dart:
            stock_code_to_name = lambda self, c: "LG디스플레이"
            find_by_name = lambda self, q: []
            stock_code_to_corp_code = lambda self, c: "0001"

        with mock.patch("bot.dart_client.get_dart", return_value=_Dart()), \
                mock.patch.object(C, "_load_alerts", return_value=[]), \
                mock.patch("trade.dart_revenue.load_inventory", return_value=inv), \
                mock.patch("trade.dart_revenue.fetch_company_products") as live, \
                mock.patch("trade.customs.session"), \
                mock.patch("trade.industry.load_mti_stored", return_value={}), \
                mock.patch("trade.industry.load_mti_imports", return_value={}), \
                mock.patch("trade.mti_companies.load_channel_pairs", return_value=[]):
            data = C.gather("034220")
        self.assertEqual([p["name"] for p in data["products"]], ["OLED"])
        live.assert_not_called()           # 인벤토리 hit → 라이브 호출 0

    def test_live_fallback_when_not_in_inventory(self):
        from unittest import mock

        class _Dart:
            stock_code_to_name = lambda self, c: "어떤회사"
            find_by_name = lambda self, q: []
            stock_code_to_corp_code = lambda self, c: "0009"

        with mock.patch("bot.dart_client.get_dart", return_value=_Dart()), \
                mock.patch.object(C, "_load_alerts", return_value=[]), \
                mock.patch("trade.dart_revenue.load_inventory", return_value={}), \
                mock.patch("trade.dart_revenue.fetch_company_products",
                           return_value={"products": [{"name": "X", "share_pct": 100.0}]}) as live, \
                mock.patch("trade.customs.session"), \
                mock.patch("trade.industry.load_mti_stored", return_value={}), \
                mock.patch("trade.industry.load_mti_imports", return_value={}), \
                mock.patch("trade.mti_companies.load_channel_pairs", return_value=[]):
            data = C.gather("000009")
        self.assertEqual([p["name"] for p in data["products"]], ["X"])
        live.assert_called_once()          # 미수록 → 라이브 폴백


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

    def test_item_matches_theme(self):
        # 큐레이션 테마 키워드(SiC·MLCC·CCTV 등)도 기업보고서에 노출
        # (사용자 2026-06-19 '테마로 추가된 것도 나와야').
        res = C._item_matches("SiC", {}, [])
        self.assertIsNotNone(res)
        self.assertIn("티씨케이", res["companies"])
        self.assertIn("반도체 소재", res["synonym"])      # 테마 카테고리명 노출
        self.assertIn("코맥스", C._item_matches("CCTV", {}, [])["companies"])
        self.assertIn("클래시스", C._item_matches("피부과", {}, [])["companies"])

    def test_theme_hs_links_to_customs(self):
        # 테마 HS Code → 관세청 수출입 품목 연계 (사용자 2026-06-19 '수출입코드랑
        # 연계돼서 안 잡힌 것 잡히게'). HS6→MTI6 해석 + by_mti 수출입 부착.
        m6 = C._hs6_to_mti6("8486.90")
        self.assertTrue(m6)                              # SiC HS → MTI6 해석
        by_mti = {m6[0]: {"name": "반도체제조용장비부품", "industry": "반도체",
                          "months": {"2026-05": 2e9}}}
        res = C._item_matches("SiC", by_mti, [])
        linked = [x for x in res["items"] if x.get("hs_linked")]
        self.assertTrue(linked)                          # HS 연계 수출입 행 추가
        self.assertEqual(linked[0]["export_usd"], 2e9)
        self.assertEqual(C._hs6_to_mti6("12"), [])       # 6자리 미만 → []

    def test_theme_company_exposure_hs_linked(self):
        # 회사명 검색도 테마 HS → 관세청 수출입 노출 (사용자 2026-06-19 회사검색 방향)
        from trade import mti_companies as mc
        self.assertEqual(mc.theme_for_company("티씨케이")[1], "8486.90-9000")
        self.assertEqual(mc.theme_for_company("삼성전자"), (None, ""))   # 비테마
        m6 = C._hs6_to_mti6("8486.90")[0]
        by_mti = {m6: {"name": "반도체제조용장비부품", "industry": "반도체",
                       "months": {"2026-05": 2e9}}}
        exp = C._company_exposure("티씨케이", by_mti, [], {}, [])
        self.assertIn("반도체제조용장비부품", [x["item"] for x in exp])

    def test_theme_hs_camera_and_hs_list(self):
        # ② 카메라모듈 → 8517.79(스마트폰부품 MTI 812820). HS 필드 단일/복수 정규화.
        from trade import mti_companies as mc
        # ② 카메라모듈 HS = 8517.79 (8529.90 아님) → 스마트폰부품 직결
        self.assertEqual(mc.theme_for_company("액트로")[1], "8517.79-1020")
        self.assertIn("812820", C._hs6_to_mti6("8517.79"))   # 스마트폰부품 직결 검증
        cam = [r for r in mc.theme_rows() if "카메라" in r["name"]][0]
        self.assertEqual(cam["hs"], ["8517.79-1020"])
        # 건기식은 단일 2106.90 유지(1211 과잉부착 역효과로 환원, 2026-06-19)
        self.assertEqual(mc.theme_for_company("노바렉스")[1], "2106.90-9099")
        geon = [r for r in mc.theme_rows() if "건강기능식품" in r["name"]][0]
        self.assertEqual(geon["hs"], ["2106.90-9099"])
        # _hs_list: 단일 str / 복수 tuple / 빈값 정규화(복수코드 구조 회귀 가드)
        self.assertEqual(mc._hs_list("8517.79-1020"), ["8517.79-1020"])
        self.assertEqual(mc._hs_list(("a", "b")), ["a", "b"])
        self.assertEqual(mc._hs_list(""), [])
        self.assertEqual(mc._hs_list(None), [])

    def test_hs_pin_and_hs_code_search(self):
        # 옵션C 후속(2026-06-19): 건기식 catch-all → 016900 기타농산가공품 핀 고정 +
        # HS코드 직접 검색 → 수출입 숫자.
        from trade import mti_companies as mc
        # 핀: 건기식은 HS6 자동해석(11 MTI 분산) 대신 016900 1곳에만
        self.assertEqual(
            mc.theme_mti6("건강기능식품 (펩타이드·식이보충제)", ["2106.90-9099"]),
            ["016900"])
        # 핀 없는 테마는 HS6 자동해석 그대로(카메라 → 스마트폰부품 812820 포함)
        self.assertIn("812820",
                      mc.theme_mti6("카메라 모듈 · OIS 액추에이터", ["8517.79-1020"]))
        # build_rows: 핀으로 노바렉스가 기타농산가공품에만, 로얄제리·커피엔 안 붙음
        from trade import reference_book as R
        nb = [r["name"] for r in R.build_rows() if "노바렉스" in (r.get("companies") or [])]
        self.assertIn("기타농산가공품", nb)
        self.assertNotIn("로얄제리", nb)
        self.assertNotIn("커피조제품", nb)
        # 과잉부착 4개 핀 (2026-06-19 전수감사) — 각 테마가 정확한 MTI6 1곳에만
        self.assertEqual(mc.theme_mti6("가정용 미용기기", ["8543.70-9020"]), ["829100"])
        self.assertEqual(mc.theme_mti6("탈철기 (자력 선별 장치)", ["8479.89-9099"]), ["729010"])
        self.assertEqual(mc.theme_mti6("연마재 (CMP공정에 쓰이는 슬러리 소재)", ["3824.99-9000"]), ["290090"])
        self.assertEqual(mc.theme_mti6("카드프린터 / 모바일프린터", ["8443.32-1000"]), ["813390"])
        # HS코드 판정: 점/긴자리 = HS, 6자리 bare = 주식코드
        self.assertTrue(C._looks_like_hs("8517.79"))
        self.assertTrue(C._looks_like_hs("2106.90-9099"))
        self.assertTrue(C._looks_like_hs("8517791020"))
        self.assertFalse(C._looks_like_hs("005930"))      # 6자리 = 주식코드
        self.assertFalse(C._looks_like_hs("삼성전자"))
        # 상위 자릿수 검색 (사용자 2026-06-22 '85 치면 다 나오게') — 2(챕터)/4(호)/8 bare
        self.assertTrue(C._looks_like_hs("85"))           # 챕터
        self.assertTrue(C._looks_like_hs("8542"))         # 호
        self.assertTrue(C._looks_like_hs("85423100"))     # HSK8
        # prefix → MTI6: 챕터(2)가 호(4)보다 넓게 잡힘
        from trade import mti_map as _MM
        m85 = _MM.hs_prefix_to_mti6("85")
        m8542 = _MM.hs_prefix_to_mti6("8542")
        self.assertTrue(len(m85) > len(m8542) > 0)
        self.assertEqual(_MM.hs_prefix_to_mti6(""), [])   # 빈 → []
        # HS코드 검색 → 해당 MTI 품목 + 수출입 숫자
        by_mti = {"812820": {"name": "스마트폰부품", "industry": "무선통신기기",
                             "months": {"2026-05": 5e8}}}
        res = C._hs_code_search("8517.79", by_mti, [])
        self.assertEqual(res["mode"], "item")
        sp = [r for r in res["items"] if r["item"] == "스마트폰부품"]
        self.assertTrue(sp and sp[0]["export_usd"] == 5e8)
        self.assertIsNone(res.get("leaf"))                # leaf 없으면 None
        # 히트맵 셀 클릭 leaf 명 → breadcrumb 표시 (사용자 2026-06-20 '코팅머신').
        res_l = C._hs_code_search("8517.79", by_mti, [], leaf="코팅머신")
        self.assertEqual(res_l["leaf"], "코팅머신")
        h_leaf = C.render_free(res_l)
        self.assertIn("클릭품목", h_leaf)
        self.assertIn("코팅머신", h_leaf)
        # leaf 없는 hs_search 는 '클릭품목' 미표시(기존 동작 보존)
        self.assertNotIn("클릭품목", C.render_free(res))

    def test_render_free_item(self):
        data = {"mode": "item", "query": "반도체", "name": "반도체",
                "items": [{"item": "디램", "industry": "반도체",
                           "export_usd": 2e9, "import_usd": 5e8,
                           "companies": ["삼성전자", "SK하이닉스"]}],
                "companies": ["삼성전자", "SK하이닉스"]}
        h = C.render_free(data)
        for must in ("관련 기업", "삼성전자", "SK하이닉스", "디램",
                     "🚢 수출", "📥 수입", "관련 상장사", "관련기업"):
            self.assertIn(must, h)
        self.assertIn("20.0억$", h)

    def test_pv_split_decompose(self):
        # 판가/물량 분해 (사용자 2026-06-22 '판가야 물량이야?'). 단가=금액÷중량.
        sp = C._pv_split(300, 100, 200, 100)   # 금액×3, 중량×2 → 단가 1.5배
        self.assertEqual(sp["value"], 200.0)   # 금액 +200%
        self.assertEqual(sp["qty"], 100.0)     # 물량 +100%
        self.assertEqual(sp["price"], 50.0)    # 판가 +50% (1.5배)
        self.assertIsNone(C._pv_split(300, 0, 200, 100))    # 기준 0 → None
        self.assertIsNone(C._pv_split(300, 100, 200, 0))    # 중량 0 → None

    def test_hs10_leaf_name_and_pv_in_breadcrumb(self):
        # 정확 HS10 검색 → 관세청 한글품목명(stat_kor) + 판가/물량 분해 breadcrumb.
        by_mti = {"111100": {"name": "금", "industry": "기타",
                             "months": {"2026-05": 1.01e9}}}
        leaf = {"7108131010": {
            "hs_code": "7108131010", "name": "금(기타 반제품)",
            "exp": 1.01e9, "exp_pm": 9.4e8, "exp_py": 2.64e8,
            "imp": 3.6e8, "imp_pm": 3.89e8, "imp_py": 2.45e8,
            "exp_wgt": 40000, "exp_wgt_pm": 39000, "exp_wgt_py": 14000,
            "imp_wgt": 15000, "imp_wgt_pm": 16000, "imp_wgt_py": 13000}}
        res = C._hs_code_search("7108.13-1010", by_mti, [], None, hs_leaf=leaf)
        self.assertEqual(res["hs_name"], "금(기타 반제품)")
        self.assertIsNotNone(res["hs_pv"]["exp_yoy"])
        h = C.render_free(res)
        self.assertIn("판가 vs 물량 분해", h)
        self.assertIn("금(기타 반제품)", h)
        self.assertIn("단가 $", h)
        # 중량 없는 구 스냅샷 leaf → 분해 None(graceful), 한글명만
        leaf2 = {"7108131010": {"hs_code": "7108131010", "name": "금",
                                "exp": 1e9, "exp_py": 2e8}}
        res2 = C._hs_code_search("7108131010", by_mti, [], None, hs_leaf=leaf2)
        self.assertIsNone(res2["hs_pv"])
        self.assertNotIn("판가 vs 물량 분해", C.render_free(res2))

    def test_pv_aggregate_at_heading_level(self):
        # 광역(호/챕터) 검색도 prefix 하위 leaf 합산해 분해 (사용자 2026-06-22
        # '각 HS 자릿수에 모두'). 8542.31(=854231) → 그 아래 leaf 2개 금액·중량 합산.
        by_mti = {"831210": {"name": "프로세서와 컨트롤러", "industry": "반도체",
                             "months": {"2026-05": 3e8}}}
        leaf = {
            "8542311000": {"hs_code": "8542311000", "name": "CPU", "exp": 100,
                           "exp_py": 50, "imp": 0, "imp_py": 0,
                           "exp_wgt": 10, "exp_wgt_py": 8, "imp_wgt": 0, "imp_wgt_py": 0},
            "8542312000": {"hs_code": "8542312000", "name": "MCU", "exp": 200,
                           "exp_py": 100, "imp": 0, "imp_py": 0,
                           "exp_wgt": 20, "exp_wgt_py": 12, "imp_wgt": 0, "imp_wgt_py": 0},
        }
        res = C._hs_code_search("8542.31", by_mti, [], None, hs_leaf=leaf)
        self.assertEqual(res["hs_pv_n"], 2)            # 두 leaf 합산
        # 합산: exp 300 vs 150(+100%), 중량 30 vs 20(+50%) → 판가 +33.3%
        self.assertEqual(res["hs_pv"]["exp_yoy"]["value"], 100.0)
        self.assertEqual(res["hs_pv"]["exp_yoy"]["qty"], 50.0)
        self.assertEqual(res["hs_pv"]["exp_yoy"]["price"], 33.3)
        h = C.render_free(res)
        self.assertIn("세부품목 합산", h)              # 광역 라벨
        self.assertIn("가중평균", h)

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


class CatchAllHeatmapFallbackTests(unittest.TestCase):
    """'기타'(CATCH_ALL) MTI 가 by_mti 에서 통째 제외돼 별칭 검색(MLCC 등)에서
    수출입 숫자가 누락되던 것 — 히트맵 leaf gap-fill 로 해소(사용자 2026-06-19)."""

    def _heatmap_conn(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE customs_heatmap_leaf (hs_code TEXT, name TEXT, "
            "ref_ym TEXT, exp INT, exp_pm INT, exp_py INT, imp INT, "
            "imp_pm INT, imp_py INT)")
        # 8532* = 고정식축전기(MTI 833310, 산업 '기타') — shipped 연계표에 존재.
        conn.executemany(
            "INSERT INTO customs_heatmap_leaf VALUES (?,?,?,?,?,?,?,?,?)",
            [("8532240000", "고정식축전기", "2026-05", 1000, 800, 500, 300, 200, 100),
             ("8532210000", "고정식축전기", "2026-05", 1000, 800, 500, 200, 100, 100)])
        conn.commit()
        return conn

    def test_load_mti_heatmap_includes_catch_all(self):
        from trade import industry
        conn = self._heatmap_conn()
        try:
            by_exp, by_imp = industry.load_mti_heatmap(conn)
        finally:
            conn.close()
        self.assertIn("833310", by_exp)                   # 기타 MTI 포함
        node = by_exp["833310"]
        self.assertEqual(node["name"], "고정식축전기")     # MTI 품목명(연계표)
        self.assertEqual(node["industry"], "기타")
        self.assertEqual(node["months"]["2026-05"], 2000)  # 두 leaf 합산
        self.assertAlmostEqual(node["metrics"]["yoy"], 100.0)   # (2000-1000)/1000
        self.assertAlmostEqual(node["metrics"]["mom"], 25.0)    # (2000-1600)/1600
        self.assertIsNone(node["metrics"]["dyoy"])         # 3-포인트 → 가속 None
        self.assertIsNone(node["metrics"]["dmom"])
        self.assertIn("833310", by_imp)
        self.assertEqual(by_imp["833310"]["months"]["2026-05"], 500)

    def test_load_mti_heatmap_empty_graceful(self):
        import sqlite3
        from trade import industry
        conn = sqlite3.connect(":memory:")           # 테이블 부재
        try:
            self.assertEqual(industry.load_mti_heatmap(conn), ({}, {}))
        finally:
            conn.close()

    def test_dir_metrics_uses_precomputed(self):
        # 히트맵 노드의 precomputed metrics 우선(sparse-series 오산정 회피).
        exp_node = {"months": {"2026-05": 1000},
                    "metrics": {"yoy": 12.0, "dyoy": None, "mom": 3.0, "dmom": None}}
        m = C._dir_metrics(exp_node, None)
        self.assertEqual(m["export_yoy"], 12.0)
        self.assertEqual(m["export_mom"], 3.0)
        self.assertIsNone(m["export_dyoy"])

    def test_mlcc_alias_resolves_real_item_and_numbers(self):
        # MLCC(별칭) → 테마 HS(8532.24) → MTI6 833310(고정식축전기) 수출입 부착.
        # by_mti 는 gather 의 히트맵 gap-fill 모사(기타 품목 노드 주입).
        self.assertIn("833310", C._hs6_to_mti6("8532.24"))
        by_mti = {"833310": {"name": "고정식축전기", "industry": "기타",
                             "months": {"2026-05": 2000.0},
                             "metrics": {"yoy": 100.0, "dyoy": None,
                                         "mom": 25.0, "dmom": None}}}
        res = C._item_matches("MLCC", by_mti, [])
        self.assertEqual(res["mode"], "item")
        self.assertIn("삼성전기", res["companies"])         # 테마 관련기업
        linked = [x for x in res["items"] if x.get("hs_linked")]
        self.assertTrue(linked)
        self.assertEqual(linked[0]["item"], "고정식축전기")  # 실제 품목명
        self.assertEqual(linked[0]["export_usd"], 2000.0)
        self.assertAlmostEqual(linked[0]["export_yoy"], 100.0)
        # 렌더에 재검색 클릭 속성(품목명 클릭 → 실제 품목명 재검색)
        html = C.render_free(res)
        self.assertIn('data-rb-search="고정식축전기"', html)


class TypoAndAliasBatchTests(unittest.TestCase):
    """회사명 오타 교정 + 미매칭 알림 후보 별칭 매칭(사용자 2026-06-19 xlsx)."""

    def test_company_typo_correction(self):
        from trade import mti_companies as mc
        # 운영자 확인 오타 → 정확 표기(매칭/표시 레이어)
        self.assertEqual(mc.canon_company("에스테아이"), "에스티아이")
        self.assertEqual(mc.canon_company("SK바이오센서"), "SK바이오사이언스")
        self.assertEqual(mc.canon_company("메티바이오메드"), "메타바이오메드")
        # 미등재는 원본 보존
        self.assertEqual(mc.canon_company("삼성전기"), "삼성전기")

    def test_price_alias_has_typos(self):
        from trade import price_provider as pp
        self.assertEqual(pp._NAME_ALIASES.get("SK바이오센서"), "SK바이오사이언스")
        self.assertEqual(pp._NAME_ALIASES.get("메티바이오메드"), "메타바이오메드")

    def test_new_aliases_resolve_company_and_hs(self):
        from trade import mti_companies as mc
        rows = {r["name"]: r for r in mc.theme_rows()}
        # 신규 테마 — 회사·HS 연결
        self.assertIn("코스모신소재",
                      rows["NCM 양극재 (니켈코발트망간 리튬염)"]["companies"])
        self.assertEqual(mc.theme_for_company("코셈")[0], "SEM (주사전자현미경)")
        # 기존 테마 회사 추가
        self.assertIn("다이요유덴", rows["MLCC (적층세라믹콘덴서)"]["companies"])
        self.assertIn("경산제지", rows["골심지 / 특수지 (판지 원지)"]["companies"])
        self.assertIn("이녹스리튬", rows["수산화리튬 / 수입 수산화리튬"]["companies"])

    def test_new_alias_hs_links_to_mti(self):
        # NCM 2841.90 → MTI6 해석되어 수출입 부착 가능(히트맵 gap-fill 과 결합).
        self.assertTrue(C._hs6_to_mti6("2841.90"))
        self.assertTrue(C._hs6_to_mti6("9012.10"))     # SEM
        self.assertTrue(C._hs6_to_mti6("2402.20"))     # 담배


if __name__ == "__main__":
    unittest.main()
