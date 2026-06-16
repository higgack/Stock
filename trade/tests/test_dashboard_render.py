"""대시보드 E2E 렌더 스모크 — _build_html 파라미터/본문 이름 불일치
(NameError) 클래스 회귀 차단.

2026-06-12: render() 가 heatmap_html 을 만들고도 _build_html 에 안 넘겼고
시그니처에도 없어, 12:52 이후 5분 refresh 가 전부 NameError 크래시 →
index.html 동결 (순서/잠정/히트맵 미표시의 최종 진범). 기본값-only 호출이
본문 전체 이름 해석을 강제하므로 이 클래스가 다시는 조용히 못 들어온다."""
import unittest


class DashboardRenderSmokeTests(unittest.TestCase):
    def test_build_html_defaults_only(self):
        from trade import dashboard as d
        html = d._build_html([], [], {}, "")
        self.assertIsInstance(html, str)
        self.assertIn("<html", html.lower())

    def test_render_passes_heatmap_to_build(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "dashboard.py"
               ).read_text(encoding="utf-8")
        self.assertIn("industry_html, heatmap_html,", src)

    def test_industry_lazy_split(self):
        # 사용자 2026-06-16 '느려': 산업트렌드(588 SVG ~7MB)를 index.html 인라인
        # 대신 industry_src(별도 파일)로 빼 탭 열 때 lazy fetch → 초기 11MB→~3MB.
        from trade import dashboard as d
        # industry_src 지정 → 인라인(BIGINLINE) 대신 data-src placeholder + 탭 버튼.
        lazy = d._build_html([], [], {}, "", None, [], "BIGINLINE", "",
                             industry_src="industry_panel.html")
        self.assertIn('data-tab="industry"', lazy)          # 탭 버튼 존재
        self.assertIn('data-src="industry_panel.html"', lazy)
        self.assertNotIn("BIGINLINE", lazy)                 # 인라인 안 됨(src 우선=lazy)
        # industry_src 없으면 기존대로 인라인(아카이브/share·자체완결 보존).
        inline = d._build_html([], [], {}, "", None, [], "BIGINLINE", "")
        self.assertIn("BIGINLINE", inline)

    def test_industry_lazy_wiring(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "dashboard.py"
               ).read_text(encoding="utf-8")
        self.assertIn("function _lazyFetchView", src)        # 탭 열 때 fetch 헬퍼
        self.assertIn("_lazyFetchView(view)", src)           # 탭 클릭 핸들러 배선
        self.assertIn('industry_out=args.out.parent / "industry_panel.html"', src)  # main 배선


if __name__ == "__main__":
    unittest.main()


class SearchAndLayoutTests(unittest.TestCase):
    """검색 확장(산업트렌드·히트맵) + 모멘텀 2×2 (사용자 2026-06-12)."""

    def test_search_filters_industry_and_heatmap(self):
        from trade import dashboard as d, heatmap
        rows = [{"hs_code": "8542310000", "name": "디램", "ref_ym": "2026-05",
                 "exp": 1000.0, "exp_pm": 900.0, "exp_py": 500.0,
                 "imp": 100.0, "imp_pm": 90.0, "imp_py": 50.0}]
        hm = heatmap.render_heatmap_html(rows)
        self.assertIn("window.hmFilter", hm)        # 전역 필터 노출
        self.assertIn("opacity", hm)                 # 비매칭 dim
        full = d._build_html([], [], {}, "", None, [], "", hm)
        self.assertIn("filterIndustryCards", full)   # 산업 카드 필터
        self.assertIn("산업 / HS", full)             # placeholder 동기

    def test_momentum_two_by_two(self):
        src = open(__file__.rsplit("/tests/", 1)[0] + "/dashboard.py",
                   encoding="utf-8").read()
        self.assertIn("repeat(2,minmax(0,1fr))", src)
        self.assertIn("grid-column:1/-1", src)       # 정렬바·노트 풀스팬

    def test_heatmap_js_no_broken_braces(self):
        # f-string 이중브레이스 누락 시 JS 가 '{{' 리터럴로 깨짐 — 영구 가드
        from trade import heatmap
        rows = [{"hs_code": "8542310000", "name": "디램", "ref_ym": "2026-05",
                 "exp": 1.0, "exp_pm": 1.0, "exp_py": 1.0,
                 "imp": 1.0, "imp_pm": 1.0, "imp_py": 1.0}]
        js = heatmap.render_heatmap_html(rows).split("<script>")[-1]
        # '}}' 는 정상 JS(객체리터럴+함수 인접 닫힘)에 존재 — 깨진
        # f-string 의 진짜 시그니처는 '{{' 리터럴 잔존.
        self.assertNotIn("{{", js)


class HeatmapIndustryGroupAndCSVTests(unittest.TestCase):
    """히트맵 [HS류|산업] 토글 (사용자 2026-06-13 '전기차는 자동차로' —
    HS 류는 관세 분류라 산업 관점과 어긋남 → HSK-MTI 연계표 병행 집계)
    + 산업트렌드·히트맵 CSV 내보내기."""

    _ROWS = [
        {"hs_code": "8473301000", "name": "디램 모듈", "ref_ym": "2026-05",
         "exp": 300.0, "exp_pm": 280.0, "exp_py": 200.0,
         "imp": 30.0, "imp_pm": 28.0, "imp_py": 20.0},
    ]

    def test_industries_tree_and_toggle(self):
        from trade import heatmap
        data = heatmap.build_heatmap_data(self._ROWS)
        self.assertIn("industries", data)
        self.assertTrue(data["industries"])         # 미매핑도 '기타(미매핑)' 그룹
        html = heatmap.render_heatmap_html(self._ROWS)
        self.assertIn('id="hm-grp"', html)          # [HS류|산업] 토글
        self.assertIn("window.hmCSV", html)         # CSV 익스포트
        self.assertNotIn("{{", html.split("<script>")[-1])

    def test_csv_button_tab_dispatch(self):
        from trade import dashboard as d, heatmap
        hm = heatmap.render_heatmap_html(self._ROWS)
        full = d._build_html([], [], {}, "", None, [], "", hm)
        self.assertIn("downloadIndustryCSV", full)
        self.assertIn("downloadRowsCSV", full)
        self.assertIn("tab==='heatmap'&&window.hmCSV", full)

    def test_industry_csv_payload_embedded(self):
        src = open(__file__.rsplit("/tests/", 1)[0] + "/dashboard.py",
                   encoding="utf-8").read()
        self.assertIn("ind-csv-data", src)
        self.assertIn("ind_csv + mti_csv + prov_zone_div", src)   # 임베드 배선(품목 CSV 포함)


class IndustryTtmToggleScopeTests(unittest.TestCase):
    """월별/12M TTM 토글 스코프 (사용자 2026-06-13 '안 눌러지고 디스플레이
    이상') — 두 버그: (1) JS 가 .ind-card 스코프라 품목 랭킹표 행클릭 확장
    카드(<table> 안, .ind-card 없음)에서 토글이 죽음, (2) querySelector 단수
    라 cell1(좌)만 swap·cell2(우 YoY) 안 바뀜. fix = .ind-row 스코프 +
    querySelectorAll. dashboard.py + industry_archive.py(_STANDALONE_JS) 양쪽."""

    def _card_html(self):
        from trade import industry
        months = {}
        for i in range(26):                       # 26개월 → TTM·TTM YoY 둘 다
            y, m = 2024 + (3 + i) // 12, (3 + i) % 12 + 1
            months[f"{y}-{m:02d}"] = 100 + i * 4
        pts = industry.industry_series({"836110": months})["836110"]
        return industry._card_body(pts, "수출액", extra="")

    def test_card_body_is_one_ind_row_with_toggle_and_both_panels(self):
        html = self._card_html()
        self.assertIn("class='ind-row'", html)
        self.assertIn("data-ind-view='ttm'", html)           # 토글 버튼
        self.assertIn("data-ind-view='monthly'", html)
        # cell1(좌·수출액) + cell2(우·YoY) = 패널 각 2쌍 → querySelectorAll 대상
        self.assertEqual(html.count("ind-panel ind-monthly"), 2)
        self.assertEqual(html.count("ind-panel ind-ttm"), 2)

    def test_js_scopes_to_ind_row_not_card_dashboard(self):
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parents[1] / "dashboard.py").read_text("utf-8")
        # 토글 핸들러가 .ind-row 스코프 + querySelectorAll 둘 다 사용
        self.assertIn("b.closest('.ind-row')", src)
        self.assertIn("row.querySelectorAll('.ind-monthly')", src)
        self.assertIn("row.querySelectorAll('.ind-ttm')", src)
        # 옛 버그 패턴(.ind-card 스코프 + 단수)이 토글 핸들러에 남지 않음
        self.assertNotIn("card.querySelector('.ind-monthly')", src)

    def test_js_scopes_to_ind_row_archive_standalone(self):
        from trade import industry_archive as ia
        js = ia._STANDALONE_JS
        self.assertIn("b.closest('.ind-row')", js)
        self.assertIn("row.querySelectorAll('.ind-monthly')", js)
        self.assertNotIn("card.querySelector('.ind-monthly')", js)


class ProvLabelVocabularyTests(unittest.TestCase):
    """발표 일정 어휘 분리 (사용자 2026-06-13) — 속보 존은 '월초(전월
    풀월)', 산업트렌드만 '익월 1일'. 같은 단어 재유입 시 혼동 회귀."""

    def test_prov_zone_no_ikwol(self):
        from pathlib import Path as _P
        root = _P(__file__).resolve().parents[2]
        dash = (root / "trade" / "dashboard.py").read_text(encoding="utf-8")
        prov = (root / "trade" / "customs_provisional.py").read_text(encoding="utf-8")
        assert "월초(전월 풀월)" in dash and "월초(전월 풀월)" in prov
        # 속보 라벨·태그에 '익월1일' 부재 (산업트렌드 divider 의 '익월 1일'
        # 은 dashboard.py 에 별도 존재 — 공백 있는 형태라 충돌 없음)
        assert "익월1일" not in dash and "익월1일" not in prov
