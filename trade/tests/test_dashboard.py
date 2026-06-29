"""Dashboard renderer tests.

We seed an in-memory store with a few representative alerts and inspect
the rendered HTML for the structural pieces that downstream features
(filtering, tab switching, image embedding) depend on. We don't try to
execute the inline JS — that would require a browser — but we verify
the JSON payload embedded for the JS to consume.
"""

import json
import re
import unittest
from pathlib import Path

from trade.dashboard import _asof_label, _companies_for, render_html
from trade.parser import parse_caption
from trade.store import alert_to_row, open_db, upsert_alert


SAMPLES = [
    # item_first with country
    (
        100,
        "2026-05-11T02:45:00+00:00",
        "라면 (전국_중국)\n관련종목 : 삼양식품 / 농심\n\n"
        "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다.",
        ["media/2026-05-11/abc.jpg", "media/2026-05-11/def.jpg"],
    ),
    # company_first
    (
        101,
        "2026-05-15T02:45:00+00:00",
        "SK하이닉스 : 플래시 메모리 (충북 청주시)\n\n"
        "2026년 4월 확정치 수출데이터 입니다.",
        ["media/2026-05-15/ghi.jpg"],
    ),
    # item-only (placeholder stocks)
    (
        102,
        "2026-05-11T02:45:00+00:00",
        "2차전지 (전국)\n관련종목: 월별 수출 데이터\n\n"
        "2026년 4월 1일 ~ 30일 잠정치 수출데이터 입니다.",
        [],
    ),
    # import
    (
        103,
        "2026-05-11T02:45:00+00:00",
        "염화칼륨 (전국)\n관련종목 : 유니드\n\n"
        "2026년 5월 1일 ~ 10일 잠정치 수입데이터 입니다.",
        ["media/2026-05-11/jkl.jpg"],
    ),
]


def _seed_store(tmp_db_path: Path):
    conn = open_db(tmp_db_path)
    for msg_id, posted_at, caption, media in SAMPLES:
        parsed = parse_caption(caption)
        row = alert_to_row(
            parsed,
            source_chat_id=-1003715527602,
            source_message_id=msg_id,
            media_group_id=None,
            ingested_at=posted_at,
            posted_at=posted_at,
            raw_text=caption,
            media_paths=media,
        )
        upsert_alert(conn, row)
    conn.close()


class TestDashboardRenderer(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "store.db"
        _seed_store(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_renders_full_html_document(self):
        html = render_html(self.db_path)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("<html lang=\"ko\">", html)
        self.assertIn("</html>", html)

    def test_header_meta_shows_totals(self):
        html = render_html(self.db_path)
        # 4 alerts, all distinct dedup_keys → 4 latest
        self.assertIn("총 4건", html)
        self.assertIn("(최신 4개)", html)

    def test_tabs_and_views_exist(self):
        html = render_html(self.db_path)
        self.assertIn('data-tab="items"', html)
        self.assertIn('data-tab="companies"', html)
        self.assertIn('id="items-view"', html)
        self.assertIn('id="companies-view"', html)

    def test_heatmap_and_csv_lazy_split(self):
        # 사용자 2026-06-30 속도 최적화: 히트맵(2.3MB)·산업CSV(3MB) lazy 분리.
        from trade.dashboard import _build_html
        from trade.store import list_all_alerts, open_db, stats
        c = open_db(self.db_path)
        try:
            alerts = list_all_alerts(c)
            s = stats(c)
        finally:
            c.close()
        latest = [a["id"] for a in alerts]
        html = _build_html(alerts, latest, s, "../",
                           heatmap_src="heatmap_panel.html",
                           industry_csv_src="industry_csv.html")
        # 히트맵: lazy 플레이스홀더(data-src) + 탭 노출, 인라인 데이터 없음
        self.assertIn('id="heatmap-view" class="view" data-src="heatmap_panel.html"', html)
        self.assertIn('data-tab="heatmap"', html)
        # 산업 CSV: lazy const + 📥 클릭 시 fetch
        self.assertIn('const INDUSTRY_CSV_SRC="industry_csv.html"', html)
        self.assertIn("fetch(INDUSTRY_CSV_SRC", html)

    def test_lazy_script_rerun_preserves_attributes(self):
        # code-review(2026-06-30): _lazyFetchView 가 프래그먼트 <script> 재실행 시
        # id/type 를 떨구면 히트맵의 <script type=application/json id=hm-data> 가
        # 깨져 탭 전체 사망 → 모든 속성 보존(setAttribute 루프) 계약 고정.
        src = Path("trade/dashboard.py").read_text(encoding="utf-8")
        self.assertIn("for(var i=0;i<old.attributes.length;i++)", src)
        self.assertIn("s.setAttribute(old.attributes[i].name", src)
        # 더블클릭 중복 fetch 가드(fetch 전 플래그)
        self.assertIn("_indCsvLoaded=true;   // fetch 전에", src)

    def test_regen_passes_lazy_out_paths(self):
        # regen 이 히트맵·산업CSV out 경로를 render_html 에 전달(소스 계약).
        src = Path("trade/dashboard.py").read_text(encoding="utf-8")
        self.assertIn('heatmap_out=args.out.parent / "heatmap_panel.html"', src)
        self.assertIn('industry_csv_out=args.out.parent / "industry_csv.html"', src)

    def test_axis_tabs_wired(self):
        # 산업별·국가별·지역별 탭 (사용자 2026-06-22) — 공용 buildGroupedAxisView.
        # 산업별 = 맨 앞(active default). 탭·뷰·빌더·등록·순서·칩바 E2E.
        html = render_html(self.db_path)
        self.assertIn('data-tab="industries">산업별', html)
        self.assertIn('data-tab="countries">국가별', html)
        self.assertIn('data-tab="regions">지역별', html)
        for vid in ('industries-view', 'countries-view', 'regions-view'):
            self.assertIn(f'id="{vid}"', html)
        # 산업별이 첫 탭이며 active default (맨 처음)
        self.assertIn('<button class="tab active" data-tab="industries">', html)
        self.assertIn('<main id="industries-view" class="view active">', html)
        for fn in ("buildIndustriesView", "buildCountriesView", "buildRegionsView",
                   "buildGroupedAxisView"):
            self.assertIn(f"function {fn}(", html)
        for reg in ("industries:buildIndustriesView", "countries:buildCountriesView",
                    "regions:buildRegionsView"):
            self.assertIn(reg, html)
        # 탭 순서: 산업별 < 품목별 < 회사별 < 국가별 < 지역별 < 매트릭스
        order = [html.index('data-tab="%s"' % t) for t in
                 ("industries", "items", "companies", "countries", "regions", "matrix")]
        self.assertEqual(order, sorted(order), "탭 순서 불일치")
        # 공용 빌더 축 인자(axisVals 일원화) — 산업은 sinkLabel '미분류'
        self.assertIn("buildGroupedAxisView(filtered,'country','country','country'", html)
        self.assertIn("buildGroupedAxisView(filtered,'region','region','region'", html)
        self.assertIn("buildGroupedAxisView(filtered,'industry','industry','industry'", html)
        self.assertIn("'조건에 맞는 산업이 없습니다.',['기타','미분류']", html)   # 기타→미분류 맨아래
        # 검색 hay 에 countries·regions·industry 포함
        self.assertIn("(a.countries||[]).join(' ')", html)
        self.assertIn("(a.regions||[]).join(' ')", html)
        self.assertIn("(a.industry||'')", html)
        # 축 선택 버튼 바 CSS(산업·국가·지역 공용)
        self.assertIn(".country-bar,.region-bar,.industry-bar{", html)
        self.assertIn(".country-chip,.region-chip,.industry-chip{", html)
        # payload 에 industry 부착(서버 매핑)
        m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
        for a in json.loads(m.group(1)):
            self.assertIn("industry", a)
        self.assertIn("data-'+cls+'=", html)          # 칩 data 속성 동적 빌드
        self.assertIn("closest('.country-chip,.region-chip,.industry-chip')", html)
        self.assertIn("country:'',region:'',industry:''", html)   # state 서브필터 3축

    def test_filter_controls_exist(self):
        html = render_html(self.db_path)
        self.assertIn('id="q"', html)
        self.assertIn('data-val="export"', html)
        self.assertIn('data-val="import"', html)
        self.assertIn('data-val="preliminary"', html)
        self.assertIn('data-val="final"', html)
        # 🆕 신규 filter — last chip group, gated by isAlertNew in matches()
        self.assertIn('data-key="onlynew"', html)
        self.assertIn('data-val="new"', html)
        self.assertIn("state.onlynew&&!isAlertNew(a)", html)
        # alert-NEW spans 7 days (was 'today only') so the badge/filter
        # persist across the ≥10-day 관세청 cycle gap.
        self.assertIn("function isAlertNew(a){return withinDays(a.posted_at||'',7)}", html)

    def test_embedded_payload_parses_and_carries_all_alerts(self):
        html = render_html(self.db_path)
        m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
        self.assertIsNotNone(m, "ALERTS payload not found in script")
        payload = json.loads(m.group(1))
        self.assertEqual(len(payload), 4)

        items = {p["item"] for p in payload}
        self.assertIn("라면", items)
        self.assertIn("플래시 메모리", items)
        self.assertIn("2차전지", items)
        self.assertIn("염화칼륨", items)

    def test_media_paths_get_prefix(self):
        html = render_html(self.db_path, media_url_prefix="/static/")
        m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
        payload = json.loads(m.group(1))
        all_media = [p for a in payload for p in a["media"]]
        self.assertTrue(all_media, "expected at least one media path")
        for path in all_media:
            self.assertTrue(
                path.startswith("/static/"),
                f"path should start with media prefix, got {path!r}",
            )

    def test_stocks_visible_in_payload(self):
        html = render_html(self.db_path)
        m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
        payload = json.loads(m.group(1))
        by_item = {p["item"]: p for p in payload}
        self.assertEqual(by_item["라면"]["stocks"], ["삼양식품", "농심"])
        self.assertEqual(by_item["플래시 메모리"]["stocks"], ["SK하이닉스"])
        self.assertEqual(by_item["2차전지"]["stocks"], [])  # placeholder
        self.assertEqual(by_item["염화칼륨"]["stocks"], ["유니드"])

    def test_payload_carries_direction_and_status(self):
        html = render_html(self.db_path)
        m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
        payload = json.loads(m.group(1))
        by_item = {p["item"]: p for p in payload}
        self.assertEqual(by_item["라면"]["dir"], "export")
        self.assertEqual(by_item["라면"]["status"], "preliminary")
        self.assertEqual(by_item["플래시 메모리"]["status"], "final")
        self.assertEqual(by_item["염화칼륨"]["dir"], "import")

    def test_modal_container_present(self):
        html = render_html(self.db_path)
        self.assertIn('id="modal"', html)
        self.assertIn('class="modal-backdrop"', html)
        self.assertIn('class="modal-close"', html)
        self.assertIn('id="modal-body"', html)

    def test_dark_mode_helper_embedded(self):
        html = render_html(self.db_path)
        # applyDarkMode() reads UTC + 9 and toggles body.dark for the
        # 19:00–07:00 KST window. The script must be embedded for the
        # transition to fire without page reloads.
        self.assertIn("applyDarkMode", html)
        self.assertIn("getUTCHours()+9", html)
        self.assertIn("setInterval(applyDarkMode", html)
        # CSS dark-mode variables defined.
        self.assertIn("body.dark", html)

    def test_mini_card_carries_data_id_for_modal_lookup(self):
        # Both items view and companies view emit mini-cards with
        # data-id so the click handler can resolve back to ALERTS[id]
        # without re-embedding the full alert per card.
        html = render_html(self.db_path)
        self.assertIn("renderMiniCard", html)
        self.assertIn("data-id", html)

    def test_nice_period_label_function_embedded(self):
        html = render_html(self.db_path)
        # The modal shows a Korean-language period label rather than
        # the raw ISO dates. niceLabel() needs to be in the page.
        self.assertIn("niceLabel", html)
        self.assertIn("상순", html)
        self.assertIn("중순까지", html)
        self.assertIn("확정", html)

    def test_company_view_smart_search_branch_present(self):
        # When the search query directly matches a company name, the
        # company view should narrow to only matching sections; check
        # the branch is emitted.
        html = render_html(self.db_path)
        self.assertIn("buildCompaniesView", html)
        self.assertIn("direct.length", html)

    def test_payload_carries_dedup_key_and_history_alerts(self):
        # Seed two alerts sharing one dedup_key + one alert with a
        # different dedup_key → payload should carry all 3 (so the
        # modal can find siblings), and LATEST_IDS should mark only
        # the latest one of each dedup_key.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "store.db"
            conn = open_db(db_path)
            cap_a = (
                "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
                "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
            )
            cap_b = (
                "라면 (전국_중국)\n관련종목 : 삼양식품\n\n"
                "2026년 4월 확정치 수출데이터 입니다."
            )
            cap_c = (
                "김치 (전국)\n관련종목 : 풀무원\n\n"
                "2026년 5월 1일 ~ 10일 잠정치 수출데이터 입니다."
            )
            for msg_id, posted_at, caption in [
                (1, "2026-05-11T02:45:00+00:00", cap_a),
                (2, "2026-05-15T02:45:00+00:00", cap_b),
                (3, "2026-05-11T02:45:00+00:00", cap_c),
            ]:
                parsed = parse_caption(caption)
                upsert_alert(
                    conn,
                    alert_to_row(
                        parsed,
                        source_chat_id=-100,
                        source_message_id=msg_id,
                        media_group_id=None,
                        ingested_at=posted_at,
                        posted_at=posted_at,
                        raw_text=caption,
                        media_paths=[],
                    ),
                )
            conn.close()

            html = render_html(db_path)
            m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
            self.assertIsNotNone(m)
            payload = json.loads(m.group(1))
            self.assertEqual(len(payload), 3)
            # Every alert carries its dedup_key so BY_DEDUP can be
            # rebuilt client-side.
            for a in payload:
                self.assertIn("dedup_key", a)

            # LATEST_IDS contains exactly 2 (one per dedup_key).
            m2 = re.search(r"const LATEST_IDS=new Set\((\[.*?\])\);", html, re.DOTALL)
            self.assertIsNotNone(m2)
            latest_ids = json.loads(m2.group(1))
            self.assertEqual(len(latest_ids), 2)

    def test_modal_renders_primary_and_secondary_card_helpers(self):
        html = render_html(self.db_path)
        # The modal uses a primary + secondary scheme so click-to-swap
        # works inside the modal. Both class names must be in the
        # renderModalCard helper.
        self.assertIn("renderModalCard", html)
        self.assertIn("modal-card primary", html)
        self.assertIn("modal-card secondary", html)
        self.assertIn("BY_DEDUP", html)

    def test_section_subtitle_helper_present(self):
        html = render_html(self.db_path)
        # 품목별 view shows a 관련종목 subtitle line built from the
        # union of variants' stocks; helper must be embedded.
        self.assertIn("unionStocks", html)
        self.assertIn("stocksSubtitle", html)
        self.assertIn("관련종목:", html)

    def test_sla_badge_helpers_present(self):
        html = render_html(self.db_path)
        # SLA badge for 잠정 alerts — computes expected 확정 date and
        # renders D-N / D-DAY / D+N 지연 chip.
        self.assertIn("expectedFinalKst", html)
        self.assertIn("slaBadge", html)
        # 'mini-sla' element class used on mini-cards.
        self.assertIn("mini-sla", html)
        # Modal-head SLA chip uses the badge namespace.
        self.assertIn("sla-pending", html)
        self.assertIn("sla-late", html)

    def test_csv_button_and_download_function_present(self):
        html = render_html(self.db_path)
        self.assertIn('id="csv-btn"', html)
        self.assertIn("downloadCSV", html)
        # The CSV exports the currently-filtered result, not all rows.
        self.assertIn("ALERTS.filter(matches)", html)

    def test_payload_carries_extra_metadata_for_csv(self):
        # CSV export needs item_raw, regions/countries, stocks_meta,
        # composite_parts, title_kind, commentary, ingested_at — verify
        # the payload exposes them.
        html = render_html(self.db_path)
        m = re.search(r"const ALERTS=(\[.*?\]);", html, re.DOTALL)
        payload = json.loads(m.group(1))
        for a in payload:
            for k in (
                "item_raw", "regions", "countries", "stocks_meta",
                "composite_parts", "title_kind", "commentary", "ingested_at",
            ):
                self.assertIn(k, a, f"missing CSV field {k}")

    def test_next_announcement_and_quickstats_present(self):
        html = render_html(self.db_path)
        self.assertIn("nextAnnouncement", html)
        self.assertIn("quickStats", html)
        # Header has the two empty meta lines that JS populates on render.
        self.assertIn('id="meta-next"', html)
        self.assertIn('id="meta-today"', html)

    def test_csv_exports_expanded_columns(self):
        html = render_html(self.db_path)
        # Verify the expanded header set is in the embedded downloadCSV.
        for col in (
            "title_kind", "item_raw", "composite_parts", "regions",
            "countries", "stocks_meta", "expected_final_date",
            "days_to_final", "ingested_at", "commentary",
            "parse_warnings", "media_urls",
        ):
            self.assertIn(col, html, f"CSV column {col} missing")
        # Absolute URL helper present so Excel hyperlinks resolve.
        self.assertIn("absUrl", html)

    def test_new_badge_helpers_present(self):
        html = render_html(self.db_path)
        # Three NEW chip variants: alert (today), item (debut in 7d),
        # company (debut in 7d). All driven by EARLIEST_* maps built
        # once on page load.
        self.assertIn("isAlertNew", html)
        self.assertIn("isItemNew", html)
        self.assertIn("isCompanyNew", html)
        self.assertIn("EARLIEST_ITEM_DATE", html)
        self.assertIn("EARLIEST_COMPANY_DATE", html)
        self.assertIn("mini-new", html)
        self.assertIn("section-new", html)

    def test_modal_extras_present(self):
        html = render_html(self.db_path)
        # Toolbar buttons
        self.assertIn('data-tool="copy-url"', html)
        self.assertIn('data-tool="save-image"', html)
        # Composite ↔ individual link helper
        self.assertIn("findCompositeLinks", html)
        # Peer-stock helper
        self.assertIn("findPeerStocks", html)
        # Deep-link handler
        self.assertIn("handleHashDeepLink", html)
        self.assertIn("alertShareUrl", html)
        # CSS classes for the new chips
        self.assertIn("modal-toolbar", html)
        self.assertIn("link-chip", html)
        self.assertIn("peer-chip", html)

    def test_matrix_view_helpers_present(self):
        html = render_html(self.db_path)
        self.assertIn("buildMatrixView", html)
        self.assertIn("countryMix", html)
        # New tab + view container
        self.assertIn('data-tab="matrix"', html)
        self.assertIn('id="matrix-view"', html)
        # CSS for matrix + mix
        self.assertIn("matrix-table", html)
        self.assertIn("country-mix", html)

    def test_matrix_multi_axis(self):
        # 다축 매트릭스 — 행/열 축을 품목·회사·산업·국가·지역 중 선택
        # (산업 축 추가 사용자 2026-06-24, item_industry CSV 분류 완비).
        html = render_html(self.db_path)
        self.assertIn("_MX_AXES", html)
        self.assertIn("function axisVals(", html)
        self.assertIn("mx-axisbar", html)
        self.assertIn("mx-axis-chip", html)
        self.assertIn("data-axis=", html)
        # 5개 축 라벨 모두(산업 포함)
        for lab in ("품목", "회사", "산업", "국가", "지역"):
            self.assertIn(lab, html)
        self.assertIn(
            "item:'품목',company:'회사',industry:'산업',country:'국가',region:'지역'",
            html)
        # 산업 축 분해 — axisVals 가 a.industry(미분류 fallback) 반환
        self.assertIn("if(axis==='industry')return [a.industry||'미분류']", html)
        # 행/열 축 state + 축 칩 클릭 핸들러
        self.assertIn("mxRow:'item',mxCol:'country'", html)
        self.assertIn("closest('.mx-axis-chip')", html)
        # CSV 헤더에 industry 포함(전 축 export — 화면 2축이라도 CSV 는 전부)
        self.assertIn("'item','item_raw','industry'", html)
        # 국가/지역 축 노이즈 정제(사용자 2026-06-22) — 화이트리스트 임베드 + 정규화
        self.assertIn("GEO_COUNTRIES=new Set(", html)
        self.assertIn("GEO_SIDO=", html)
        self.assertIn("function normCountry(", html)
        self.assertIn("function normRegion(", html)

    def test_empty_store_renders_without_crash(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            empty_db = Path(td) / "empty.db"
            open_db(empty_db).close()
            html = render_html(empty_db)
            self.assertIn("<!DOCTYPE html>", html)
            self.assertIn("총 0건", html)


class TestEvalMissBacklogCard(unittest.TestCase):
    """unstored_check's eval_misses.jsonl backlog must surface in the
    dashboard header so a growing backlog is visible between the daily
    ⚠️ alerts (which scroll out of the channel). Empty / missing file
    must render NO line so the operator's view stays clean after the
    last RULE was added.
    """

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "store.db"
        _seed_store(self.db_path)
        self.miss_path = Path(self.tmpdir.name) / "eval_misses.jsonl"

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_misses(self, recs):
        with self.miss_path.open("w", encoding="utf-8") as fh:
            for rec in recs:
                fh.write(json.dumps(rec) + "\n")

    def test_no_eval_miss_path_renders_no_backlog_line(self):
        html = render_html(self.db_path)
        # Empty div is fine (CSS :empty hides it) — but no header text.
        self.assertNotIn("미파싱 백로그", html)

    def test_missing_eval_miss_file_renders_no_backlog_line(self):
        html = render_html(
            self.db_path,
            eval_miss_path=self.tmpdir.name + "/does_not_exist.jsonl",
        )
        self.assertNotIn("미파싱 백로그", html)

    def test_empty_eval_miss_file_renders_no_backlog_line(self):
        self.miss_path.touch()
        html = render_html(self.db_path, eval_miss_path=self.miss_path)
        self.assertNotIn("미파싱 백로그", html)

    def test_non_empty_backlog_shows_count_and_oldest_age(self):
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self._write_misses([
            {"detected_at": old, "chat_id": 1, "message_id": 100,
             "caption": "a"},
            {"detected_at": recent, "chat_id": 1, "message_id": 101,
             "caption": "b"},
            {"detected_at": recent, "chat_id": 1, "message_id": 102,
             "caption": "c"},
        ])
        html = render_html(self.db_path, eval_miss_path=self.miss_path)
        self.assertIn("미파싱 백로그", html)
        # The strong tag shows the count …
        self.assertIn("<strong>3건</strong>", html)
        # … and the oldest age comes from the 7-day-old row, not the
        # 1-day-old one.
        self.assertIn("7일째", html)
        # The eval_misses.jsonl filename is referenced so operators
        # know where to look on disk.
        self.assertIn("eval_misses.jsonl", html)

    def test_malformed_lines_are_skipped_but_valid_rows_count(self):
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.miss_path.write_text(
            json.dumps({"detected_at": old, "chat_id": 1,
                        "message_id": 1, "caption": "x"}) + "\n"
            + "not-json\n"
            + json.dumps({"detected_at": old, "chat_id": 1,
                          "message_id": 2, "caption": "y"}) + "\n",
            encoding="utf-8",
        )
        html = render_html(self.db_path, eval_miss_path=self.miss_path)
        self.assertIn("<strong>2건</strong>", html)

    def test_rows_without_detected_at_still_count_but_no_age(self):
        # Older eval_misses rows (pre-detected_at era) should still count
        # toward the backlog total so they don't silently disappear.
        self._write_misses([
            {"chat_id": 1, "message_id": 1, "caption": "x"},
            {"chat_id": 1, "message_id": 2, "caption": "y"},
        ])
        html = render_html(self.db_path, eval_miss_path=self.miss_path)
        self.assertIn("<strong>2건</strong>", html)
        # No age suffix when we couldn't parse any detected_at.
        self.assertNotIn("일째", html)

    def test_ignored_captions_excluded_from_backlog(self):
        # 사용자 2026-06-15: IGNORED 매칭 캡션('주요 기업 수출입 확정치
        # 코멘트' 래퍼 / DART 릴레이)은 영구 파싱 불가가 정상 → actionable
        # 백로그 아님. 카운트에서 제외 (read-only, 파일 미변경).
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self._write_misses([
            {"detected_at": now, "chat_id": 1, "message_id": 1,
             "caption": "26년 5월 주요 기업 수출입 확정치 코멘트\n\n"
                        "이달의 주요 기업 수출데이터 확정치 공유드립니다"},
            {"detected_at": now, "chat_id": 1, "message_id": 2,
             "caption": "공시 https://dart.fss.or.kr/foo"},   # DART 릴레이도 제외
            {"detected_at": now, "chat_id": 1, "message_id": 3,
             "caption": "신규 양식 — 알 수 없는 수출 캡션"},   # 진짜 actionable
        ])
        html = render_html(self.db_path, eval_miss_path=self.miss_path)
        self.assertIn("미파싱 백로그", html)
        self.assertIn("<strong>1건</strong>", html)   # IGNORED 2건 제외 → 1건

    def test_all_ignored_renders_no_backlog_line(self):
        # 전부 IGNORED 면 count 0 → 줄 자체가 사라진다 (사용자가 본 msg
        # 9226·9227 '확정치 코멘트' 2건 케이스).
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        cap = ("26년 5월 주요 기업 수출입 확정치 코멘트\n"
               "이달의 주요 기업 수출데이터 확정치 공유드립니다")
        self._write_misses([
            {"detected_at": now, "chat_id": 1, "message_id": 9226, "caption": cap},
            {"detected_at": now, "chat_id": 1, "message_id": 9227, "caption": cap},
        ])
        html = render_html(self.db_path, eval_miss_path=self.miss_path)
        self.assertNotIn("미파싱 백로그", html)


class TestCustomsPanel(unittest.TestCase):
    """The 관세청 comparison panel is server-rendered under the header.
    Hidden entirely when there are no pins; shows '수집 대기' for pins
    with no cached month yet; shows numbers + 전월비 once data exists.
    Must never break the main render (additive feature)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "store.db"
        _seed_store(self.db_path)
        self.customs_db = Path(self.tmp.name) / "customs.db"
        self.hs_map = Path(self.tmp.name) / "hs_map.tsv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_pins_hides_pin_panel(self):
        # No pins AND no surge scan data → the whole 관세청 panel is absent.
        self.hs_map.write_text("", encoding="utf-8")
        html = render_html(
            self.db_path, customs_db_path=self.customs_db, hs_map_path=self.hs_map
        )
        self.assertNotIn("📌 내 핀", html)

    def test_pin_without_data_shows_waiting(self):
        self.hs_map.write_text("라면\t1902301010\n", encoding="utf-8")
        # customs_db doesn't exist yet → panel shows but row is 수집 대기
        html = render_html(
            self.db_path, customs_db_path=self.customs_db, hs_map_path=self.hs_map
        )
        self.assertIn("📌 내 핀", html)
        self.assertIn("수집 대기", html)
        self.assertIn("1902301010", html)

    def test_pin_with_data_shows_numbers(self):
        from trade import customs
        self.hs_map.write_text("라면\t1902301010\n", encoding="utf-8")
        with customs.session(self.customs_db) as conn:
            customs.upsert_month(conn, "1902301010", "2026-01", {
                "exp_dlr": 129673809, "imp_dlr": 1236615,
                "bal_payments": 128437194, "exp_wgt": 0, "imp_wgt": 0,
            })
        html = render_html(
            self.db_path, customs_db_path=self.customs_db, hs_map_path=self.hs_map
        )
        self.assertIn("📌 내 핀", html)
        self.assertIn("1.3억$", html)        # formatted export (억 달러 통일)
        self.assertNotIn("수집 대기", html)

    def test_default_paths_no_crash(self):
        # render_html with no customs args must still work (uses module
        # defaults; on a machine with no pins the panel is just hidden).
        html = render_html(self.db_path)
        self.assertIn("<!DOCTYPE html>", html)


class CompaniesForTests(unittest.TestCase):
    def test_filters_products_keeps_companies(self):
        # 회사별 뷰 그룹핑용 companies — 품목/제품명 제외, 실제 회사·복합명은 유지.
        out = _companies_for(["삼성전자", "수산화리튬", "동박", "GST / 유니셈 등",
                              "반도체 웨이퍼", "소자의 측정, 검사용 장비"])
        self.assertIn("삼성전자", out)
        self.assertIn("GST", out)
        self.assertIn("유니셈", out)
        for prod in ("수산화리튬", "동박", "반도체", "웨이퍼", "반도체 웨이퍼",
                     "측정", "검사용", "장비"):
            self.assertNotIn(prod, out)

    def test_empty_safe(self):
        self.assertEqual(_companies_for([]), [])
        self.assertEqual(_companies_for(None), [])


class AsofLabelTests(unittest.TestCase):
    def test_weekday_label(self):
        self.assertEqual(_asof_label("20260604"), "목")   # 2026-06-04 목요일
        self.assertEqual(_asof_label("20260605"), "금")
        self.assertEqual(_asof_label("20260606"), "토")

    def test_bad_input_empty(self):
        for bad in ("", "x", "2026", None):
            self.assertEqual(_asof_label(bad), "")


class HtmlNoCacheTests(unittest.TestCase):
    """정적 대시보드 HTML 은 no-cache 강제 — 브라우저가 옛 HTML/JS 를 캐싱하면
    배포된 수정(모달 perf 등)이 사용자에게 안 닿음 (사용자 2026-06-15 '여전히 느림'
    = 옛 JS 캐시). text/html 응답에 Cache-Control: no-cache 주입 배선 확인."""

    def test_html_gets_no_cache_header(self):
        src = (Path(__file__).resolve().parents[1]
               / "dashboard_server.py").read_text(encoding="utf-8")
        self.assertIn('"Cache-Control": "no-cache, must-revalidate"', src)
        self.assertIn('mime.startswith("text/html")', src)


class CardModalPerfTests(unittest.TestCase):
    """카드 클릭 모달 open 지연 — 긴 목록(1054+카드)에서 body{overflow:hidden}
    스크롤 잠금이 스크롤바를 없애 페이지 폭이 변하면 전 카드 reflow → 지연.
    scrollbar-gutter:stable 로 스크롤바 공간 항상 예약(폭 변동·reflow 제거).
    (사용자 2026-06-15 '카드 여는거 느림'.)"""

    def test_scrollbar_gutter_stable_present(self):
        import tempfile
        from trade.dashboard import render_html
        from trade.store import open_db
        d = tempfile.mkdtemp()
        db = Path(d) / "store.db"
        open_db(db).close()
        html = render_html(db)
        self.assertIn("scrollbar-gutter:stable", html)

    def test_items_view_sorts_first_appearance_top(self):
        # 사용자 2026-06-15 '첫 등장 품목이 품목별에서 맨 위로'. buildItemsView
        # 정렬이 isItemNew 를 1차 키로 (NEW 먼저), 그 다음 posted_at 최신순.
        import tempfile
        from trade.dashboard import render_html
        from trade.store import open_db
        d = tempfile.mkdtemp()
        db = Path(d) / "store.db"
        open_db(db).close()
        html = render_html(db)
        self.assertIn("if(xNew!==yNew)return xNew?-1:1", html)

    def test_lazy_render_only_active_view_built(self):
        # 사용자 2026-06-15 '카드 클릭 안 빨라졌어'. 옛 render() 는 3 클라이언트
        # 뷰를 매번 innerHTML 로 전부 빌드(1054×3 DOM) → lazy 로 활성 탭만
        # 빌드, 나머지는 더티 표시 후 탭 전환 때 빌드. 배선 마커를 가드:
        #  (a) render() 가 활성 탭만 빌드 (_buildView(_activeTab()))
        #  (b) 3 뷰를 무조건 빌드하던 옛 직접 innerHTML 줄이 사라짐
        #  (c) 탭 핸들러가 더티 클라이언트 뷰를 그때 빌드
        import tempfile
        from trade.dashboard import render_html
        from trade.store import open_db
        d = tempfile.mkdtemp()
        db = Path(d) / "store.db"
        open_db(db).close()
        html = render_html(db)
        # (a) render() builds only the active view
        self.assertIn("_buildView(_activeTab())", html)
        self.assertIn("_viewDirty", html)
        self.assertIn("_CLIENT_VIEWS", html)
        # (b) the old unconditional 3-view build is gone
        self.assertNotIn(
            "document.getElementById('companies-view').innerHTML=buildCompaniesView(filtered)",
            html,
        )
        self.assertNotIn(
            "document.getElementById('matrix-view').innerHTML=buildMatrixView(filtered)",
            html,
        )
        # (c) tab-switch lazily builds a dirty client view
        self.assertIn("if(_CLIENT_VIEWS[tab]&&_viewDirty[tab])_buildView(tab)", html)


class TestBatch20260615(unittest.TestCase):
    """사용자 2026-06-15: ④ 월별 원자료 가로스크롤 우측 디폴트 · ⑥ BeOn 백필
    cap 기본 5000."""

    def test_raw_table_scroll_right_default(self):
        # 월별 원자료 가로스크롤 우측(최신) 디폴트 — JS scrollLeft 타이밍이 깨져(탭
        # 숨김 중 scrollWidth=0) CSS direction:rtl 레이아웃 기반으로 전환(사용자
        # 2026-06-20, mistake #14). _scrollRaw 는 호출부 안전용 no-op 로 잔존. 회귀 가드.
        src = Path("trade/dashboard.py").read_text(encoding="utf-8")
        self.assertIn(".ind-raw-scroll{overflow-x:auto;direction:rtl}", src)  # 우측 디폴트(CSS)
        self.assertIn("window._scrollRaw=function(){};", src)                 # 호출부 안전 no-op
        # 자식 월별표(.ind-mti-card 내부)가 부모 폭 안 늘리도록 직속-자식만 width:100%.
        self.assertIn(".ind-sub-card>.ind-raw-scroll>.ind-table{width:100%}", src)

    def test_mti_detail_comment_left_chart_right(self):
        # 펼침 품목 상세 = 코멘트(좌)·차트(우) — grid-in-<td> 트랙깨짐을 .ind-mti-card
        # 블록래퍼(contain:inline-size)로 해결, 차트 우측정렬 고정폭 그리드(사용자
        # 2026-06-20, mistake #14). 코멘트 nowrap·우측정렬 누수 해제. 회귀 가드.
        src = Path("trade/dashboard.py").read_text(encoding="utf-8")
        self.assertIn(".ind-sub-wrap .ind-mti-d .ind-row{grid-template-columns:"
                      "minmax(230px,400px) 400px 400px}", src)
        self.assertIn(".ind-mti-card{min-width:0;contain:inline-size}", src)
        self.assertIn(".ind-sub-wrap .ind-mti-d>td{white-space:normal;text-align:left}", src)

    def test_backfill_cap_default_5000(self):
        src = Path("trade/scripts/backfill_beon.py").read_text(encoding="utf-8")
        self.assertIn('TRADE_MAX_CANDIDATES") or "5000"', src)


class TestEvalMissBacklogFilter(unittest.TestCase):
    """미파싱 백로그 카운트 제외 규칙(사용자 2026-06-30): JP(jp.db 정상처리)·
    IGNORED prefix/contains·운영자 /ignore msg_id 는 제외, 진짜 미파싱만 셈."""

    def _write(self, rows):
        import json as _j
        import tempfile
        p = Path(tempfile.mkdtemp()) / "eval.jsonl"
        p.write_text("\n".join(_j.dumps(r, ensure_ascii=False) for r in rows),
                     encoding="utf-8")
        return p

    def test_excludes_jp_ignored_and_msgid(self):
        from unittest import mock
        from trade import dashboard as td
        from trade import ignored as ig
        rows = [
            {"detected_at": "2026-06-15T00:00:00Z", "message_id": 1,
             "caption": "📈 일본 수출 데이터 업데이트: MLCC\n최신 월: 2026-05\n수출액: 5십억 엔"},
            {"detected_at": "2026-06-15T00:00:00Z", "message_id": 2,
             "caption": "이달의 주요 기업 수출데이터 코멘트"},          # IGNORED_CONTAINS
            {"detected_at": "2026-06-15T00:00:00Z", "message_id": 9999,
             "caption": "SemiAnalysis: AI 가치사슬"},                  # /ignore msg_id
            {"detected_at": "2026-06-15T00:00:00Z", "message_id": 5,
             "caption": "진짜 미파싱 신포맷 캡션"},                     # genuine
        ]
        p = self._write(rows)
        with mock.patch.object(ig, "load", return_value={9999}):
            s = td._load_eval_miss_summary(p)
        self.assertEqual(s["count"], 1)   # genuine miss 만


if __name__ == "__main__":
    unittest.main()
