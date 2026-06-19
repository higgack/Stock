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
        # HS코드 판정: 점/긴자리 = HS, 6자리 bare = 주식코드
        self.assertTrue(C._looks_like_hs("8517.79"))
        self.assertTrue(C._looks_like_hs("2106.90-9099"))
        self.assertTrue(C._looks_like_hs("8517791020"))
        self.assertFalse(C._looks_like_hs("005930"))      # 6자리 = 주식코드
        self.assertFalse(C._looks_like_hs("삼성전자"))
        # HS코드 검색 → 해당 MTI 품목 + 수출입 숫자
        by_mti = {"812820": {"name": "스마트폰부품", "industry": "무선통신기기",
                             "months": {"2026-05": 5e8}}}
        res = C._hs_code_search("8517.79", by_mti, [])
        self.assertEqual(res["mode"], "item")
        sp = [r for r in res["items"] if r["item"] == "스마트폰부품"]
        self.assertTrue(sp and sp[0]["export_usd"] == 5e8)

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
