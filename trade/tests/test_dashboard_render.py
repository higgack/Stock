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

    def test_build_html_embeds_scroll_restore(self):
        # 뒤로가기 시 보던 위치 복원 (사용자 2026-06-18 '모든 대시보드') —
        # 대시보드는 메인 스크롤 표면이라 가장 중요. </body> 직전 임베드.
        from trade import dashboard as d
        html = d._build_html([], [], {}, "")
        self.assertIn("scrollRestoration", html)
        self.assertIn("sessionStorage", html)

    def test_build_html_has_flow_note(self):
        # 📡 자동화 플로우 노트가 상단에 임베드 (사용자 2026-06-18 '플로우 상단에').
        from trade import dashboard as d
        html = d._build_html([], [], {}, "")
        self.assertIn("flow-note", html)
        self.assertIn("데이터 자동화 플로우", html)
        self.assertIn("18일", html)          # DART 전수 = 매월 18일
        self.assertIn("04:30", html)         # 파서개선 소급 드레이너 = 매일

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
        # #455 회귀 fix(2026-06-16): innerHTML 은 <script> 미실행 → 프래그먼트
        # 스크립트 재실행(window._scrollRaw 정의·월별 원자료 우측 스크롤 복구).
        self.assertIn("createElement('script')", src)        # 프래그먼트 스크립트 재실행
        self.assertIn("if(window._scrollRaw)window._scrollRaw(view)", src)  # 우측 스크롤 호출
        # 산업트렌드 백그라운드 prefetch(사용자 2026-06-16 '갭 두고 자동 로드') —
        # idle(최대 3초) 시 미리 fetch 해 탭 클릭 즉시 표시.
        self.assertIn("requestIdleCallback(_prefetchLazy", src)
        self.assertIn(".view[data-src]", src)                # prefetch 대상 = lazy 뷰

    def test_industry_csv_kept_in_main(self):
        # 산업트렌드 CSV JSON 스크립트는 lazy 분리돼도 메인에 남아야 csv-btn 이 찾음
        # (사용자 2026-06-18 '산업트렌드 CSV 안 돼'). _build_html industry_csv 인자 본문 emit.
        import trade.dashboard as d
        csv = "<script type='application/json' id='mti-csv-summary'>[1]</script>"
        html = d._build_html([], [], {}, "", None, [], "", "",
                             industry_src="industry_panel.html", industry_csv=csv)
        self.assertIn("mti-csv-summary", html)            # 메인에 존재 → 버튼이 찾음

    def test_alerts_history_split(self):
        # 사용자 2026-06-16 '최신만 인라인 + 모달 히스토리 on-demand': history_out
        # 지정 시 ALERTS 인라인=최신만, 과거 발표는 alerts_history.json 으로.
        import json
        import tempfile
        from pathlib import Path
        from trade import dashboard as d
        alerts = [
            {"id": 1, "dedup_key": "K", "direction": "export", "status": "final",
             "item": "X", "posted_at": "2026-06-10"},   # 최신
            {"id": 2, "dedup_key": "K", "direction": "export", "status": "preliminary",
             "item": "X", "posted_at": "2026-06-01"},   # 과거(history)
        ]
        latest_ids = [1]
        with tempfile.TemporaryDirectory() as td:
            hp = Path(td) / "alerts_history.json"
            html = d._build_html(alerts, latest_ids, {}, "", None, [], "", "",
                                 history_out=hp)
            # 인라인 ALERTS 엔 최신(id 1)만, 과거(id 2)는 없음
            import re
            m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
            inline_ids = {a["id"] for a in json.loads(m.group(1))}
            self.assertEqual(inline_ids, {1})            # 최신만 인라인
            self.assertIn("alerts_history.json", html)   # HISTORY_SRC 배선
            # history 파일엔 과거(id 2)
            hist = json.loads(hp.read_text(encoding="utf-8"))
            self.assertEqual({a["id"] for a in hist}, {2})
        # history_out 미지정 → 전체 인라인(back-compat, 모달 기존대로)
        html2 = d._build_html(alerts, latest_ids, {}, "")
        import re as _re2
        m2 = _re2.search(r"const ALERTS=(\[.*?\]);", html2, _re2.DOTALL)
        self.assertEqual({a["id"] for a in __import__("json").loads(m2.group(1))}, {1, 2})

    def test_alerts_history_lazy_wiring(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "dashboard.py"
               ).read_text(encoding="utf-8")
        self.assertIn("function _loadHistory", src)          # 모달 히스토리 fetch
        self.assertIn("_loadHistory();", src)                # showModal 트리거
        self.assertIn("const HISTORY_SRC=", src)             # 클라이언트 소스 상수
        self.assertIn('history_out=args.out.parent / "alerts_history.json"', src)  # main 배선


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


class NextAnnouncementRollForwardTests(unittest.TestCase):
    """'다음 발표 D-N' 주말+공휴일 순연 (사용자 2026-06-21 '21일 잠정인데
    일요일'). 종전: nextAnnouncement 가 고정 캘린더(11/15/21/1/15)에서 today
    초과만 골라, KST 가 6/21(일)이 되자 아직 안 나온 6월 1-20일 잠정을 건너뛰고
    7/1 을 다음으로 표시. fix: nominal→다음 영업일 순연 + today 이상 선택."""

    def test_kr_holiday_dates_graceful_and_weekday_only(self):
        from trade import dashboard as d
        out = d._kr_holiday_dates()
        self.assertIsInstance(out, list)              # 라이브러리 부재여도 []·예외X
        import datetime as _dt
        for ds in out:                                # 평일만(주말은 JS 가 순연)
            wd = _dt.date.fromisoformat(ds).weekday()
            self.assertLess(wd, 5, f"{ds} 는 주말 — 평일 휴장만 수집해야")

    def test_rollforward_wiring_embedded(self):
        # JS 배선이 본문에 실렸는지(주입 const + 순연 함수 + today 이상 필터).
        from trade import dashboard as d
        html = d._build_html([], [], {}, "")
        self.assertIn("KR_HOLIDAYS=new Set(", html)   # 서버 주입 공휴일 셋
        self.assertIn("rollToBusinessDay", html)      # 순연 헬퍼
        self.assertIn("c.date>=today", html)          # 엄격 초과(>)→이상(>=)

    def test_nextannouncement_rolls_sunday_to_monday(self):
        # JS nextAnnouncement 를 추출해 today=2026-06-21(일) 시나리오 평가.
        # node 부재 환경이면 skip (회귀는 wiring 테스트가 1차 가드).
        import re, shutil, subprocess, tempfile, os
        if not shutil.which("node"):
            self.skipTest("node 미설치")
        from trade import dashboard as d
        html = d._build_html([], [], {}, "")
        blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
        js = "\n;\n".join(b for b in blocks
                          if b.strip() and "application/json" not in b[:60])
        harness = (
            "const js=" + __import__("json").dumps(js) + ";"
            "function grab(n){const m=js.match(new RegExp('function '+n+'\\\\([^]*?\\\\n}','m'));"
            "if(!m)throw new Error('miss '+n);return m[0];}"
            "const src=grab('rollToBusinessDay')+'\\n'+grab('daysBetween')+'\\n'+grab('nextAnnouncement');"
            "function run(today,hol){const fn=new Function('KR_HOLIDAYS','kstTodayString',"
            "src+'\\nreturn nextAnnouncement();');return fn(new Set(hol),()=>today);}"
            "console.log(JSON.stringify(run('2026-06-21',[])));"
            "console.log(JSON.stringify(run('2026-06-12',[])));"
            "console.log(JSON.stringify(run('2026-09-21',['2026-09-21','2026-09-22','2026-09-23'])));"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(harness); path = f.name
        try:
            out = subprocess.run(["node", path], capture_output=True, text=True, timeout=20)
        finally:
            os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr)
        import json as _j
        sun, fri, chuseok = [_j.loads(x) for x in out.stdout.strip().splitlines()]
        # 6/21(일) → 6/22(월) 6월 1-20일 잠정 (7/1 로 건너뛰지 않음)
        self.assertEqual(sun["date"], "2026-06-22")
        self.assertEqual(sun["kind"], "6월 1-20일 잠정")
        self.assertEqual(sun["daysUntil"], 1)
        # 종전 6/12 확정-누락 fix 보존 — 6/15 5월 전체 확정
        self.assertEqual(fri["date"], "2026-06-15")
        self.assertEqual(fri["kind"], "5월 전체 확정")
        # 공휴일(추석 연휴) 순연 — 9/21~23 휴장 → 9/24(목)
        self.assertEqual(chuseok["date"], "2026-09-24")
