"""FRED 보드(bot.fred_boards) 순수 로직 회귀 — 지표계산·신호룰·순유동성·점수·렌더.
fetch(FRED I/O)는 graceful 의존이라 제외, 명시 히스토리로 순수 검증."""
import unittest

from bot import fred_boards as fb
from bot.fred_boards_catalog import CPI_SERIES, LIQ_SERIES, PPI_SERIES


def _mk_hist(vals, start_y=2023):
    """[v0, v1, …] → 월간 [(YYYY-MM-01, v)] 오름차순."""
    out = []
    y, m = start_y, 1
    for v in vals:
        out.append((f"{y:04d}-{m:02d}-01", float(v)))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


class CatalogTests(unittest.TestCase):
    """카탈로그 무결성 — id 유일 + 필수 필드(렌더가 기대하는 계약)."""

    def test_ppi_unique_and_fields(self):
        ids = [s["id"] for s in PPI_SERIES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 72)          # 원본 32 + 확장 15+24 + 페인트·제지
        for s in PPI_SERIES:
            for k in ("id", "name", "cat", "stocks"):
                self.assertTrue(s.get(k), f"{s.get('id')} missing {k}")

    def test_liq_unique_and_fields(self):
        ids = [s["id"] for s in LIQ_SERIES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 37)          # 원본 31 + 확장 8
        for s in LIQ_SERIES:
            self.assertTrue(s.get("id") and s.get("name"))
        # 확장 8종 배선 확인(사용자 2026-07-02 요청분)
        for want in ("WRESBAL", "ECBASSETSW", "VIXCLS", "T10YIE", "TOTBKCR",
                     "BUSLOANS", "IORB", "DEXKOUS"):
            self.assertIn(want, ids)

    def test_ppi_multimarket_stocks(self):
        # 관련주 멀티마켓 확장(사용자: '한국말고 다른 나라도') — US/JP 표기 존재.
        joined = " ".join(s["stocks"] for s in PPI_SERIES)
        for market in ("US:", "JP:", "TW:"):
            self.assertIn(market, joined)


class MetricsTests(unittest.TestCase):
    def test_series_metrics_basic(self):
        # 24개월 단조 증가 100→146(월 +2): YoY=+(24)/122*... 정확값 검증.
        hist = _mk_hist([100 + 2 * i for i in range(24)])
        m = fb.series_metrics(hist)
        self.assertEqual(m["latest"], 146.0)
        self.assertAlmostEqual(m["mom"], (146 - 144) / 144 * 100, places=6)
        self.assertAlmostEqual(m["yoy"], (146 - 122) / 122 * 100, places=6)
        self.assertEqual(m["peak"], 146.0)              # 단조증가 → 고점=최신
        self.assertAlmostEqual(m["from_peak"], 0.0)
        self.assertIsNone(m["recovery"])                # 고점 후 저점 없음

    def test_series_metrics_peak_trough_recovery(self):
        # 상승→고점(120)→급락(90)→반등(100): from_peak<0, recovery>0.
        hist = _mk_hist([100, 105, 110, 115, 120, 110, 100, 90, 92, 95, 98, 100])
        m = fb.series_metrics(hist)
        self.assertEqual(m["peak"], 120.0)
        self.assertLess(m["from_peak"], 0)
        self.assertEqual(m["trough_after_peak"], 90.0)
        self.assertAlmostEqual(m["recovery"], (100 - 90) / 90 * 100, places=6)

    def test_series_metrics_short_none(self):
        self.assertIsNone(fb.series_metrics(_mk_hist([1, 2, 3])))

    def test_momentum_sign_crossing_none(self):
        # 금리커브류(T10Y3M 등) 음수↔양수 교차 시 분수 거듭제곱 = 복소수 →
        # JSON 직렬화 크래시(리뷰 finding). 음수 구간 관여 시 momentum=None.
        # 6개월 전(-0.2, 음수) → 최신(+0.4) 부호 교차: 기하 변화율 불능.
        hist = _mk_hist([-0.5, -0.4, -0.3, -0.2, -0.3, -0.2, -0.1, 0.05,
                         0.1, 0.2, 0.3, 0.4])
        m = fb.series_metrics(hist)
        self.assertIsNone(m["momentum"])
        import json
        json.dumps(m)                                   # 직렬화 가능해야 함

    def test_value_at_eom_anchor(self):
        # 앵커 06-30(월말) → 1개월 전 = 05-31 포함해야(리뷰 finding: day 그대로
        # 쓰면 05-30 컷 → 05-31 누락 → 2개월 변화로 부풀려짐).
        hist = [("2026-0%d-30" % 4, 95.0), ("2026-05-31", 100.0),
                ("2026-06-30", 110.0)] + [
            (f"2025-{m:02d}-28", 80.0) for m in range(6, 13)]
        hist = sorted(hist)
        self.assertEqual(fb._value_at(hist, 1), 100.0)   # 05-31 (95 아님)


class SignalTests(unittest.TestCase):
    def _sig(self, **kw):
        base = {"yoy": 0.0, "m3": 0.0, "from_peak": 0.0, "recovery": None}
        base.update(kw)
        return fb._signal(base)[0]

    def test_thresholds(self):
        self.assertEqual(self._sig(yoy=6, m3=2), "strong")
        self.assertEqual(self._sig(yoy=6, m3=1.0), "moderate")   # 3M 미달 → 중간
        self.assertEqual(self._sig(yoy=-3, m3=-1), "decline")
        self.assertEqual(self._sig(yoy=1, m3=0.3), "mild")
        self.assertEqual(
            self._sig(yoy=-2, m3=0.5, from_peak=-10, recovery=3), "reversal")
        # 반등이지만 고점 근처(-3%)면 reversal 아님
        self.assertNotEqual(
            self._sig(yoy=-2, m3=0.5, from_peak=-3, recovery=3), "reversal")


class LiquidityTests(unittest.TestCase):
    def test_net_liquidity_units(self):
        # FRED 원시 단위: WALCL=M$, TGA(WTREGEN)=B$, RRP=B$ (리뷰 교정 —
        # 공식 FRED 그래프 WALCL/1000 − RRPONTSYD − WTREGEN 와 동일).
        # 7,000,000M(=7000B) − 800B − 200B = 6000B.
        walcl = [("2026-01-01", 7_000_000.0)]
        tga = [("2026-01-01", 800.0)]
        rrp = [("2026-01-01", 200.0)]
        nl = fb.net_liquidity(walcl, tga, rrp)
        self.assertEqual(nl, [("2026-01-01", 6000.0)])

    def test_net_liquidity_real_values_lock(self):
        # 2026-03 실측 잠금: WALCL 6,628,894M · TGA 832.053B · RRP 0.332B →
        # ≈5,796.5B (외부 실측 Fed Net Liquidity ~5.8T 와 일치). 단위 회귀 방지.
        nl = fb.net_liquidity([("2026-03-01", 6_628_894.0)],
                              [("2026-03-01", 832.053)],
                              [("2026-03-01", 0.332)])
        self.assertAlmostEqual(nl[0][1], 5796.509, places=2)

    def test_net_liquidity_skips_unmatched(self):
        # TGA/RRP 해당일 이전 값 없으면 그 날짜 skip(부분 시계열 graceful).
        walcl = [("2026-01-01", 7_000_000.0), ("2026-01-08", 7_100_000.0)]
        tga = [("2026-01-05", 800_000.0)]
        rrp = [("2026-01-05", 100.0)]
        nl = fb.net_liquidity(walcl, tga, rrp)
        self.assertEqual([d for d, _ in nl], ["2026-01-08"])

    def test_pct_rank_and_score(self):
        vals = [float(i) for i in range(1, 101)]
        self.assertAlmostEqual(fb._pct_rank(vals, 100.0), 100.0)
        self.assertAlmostEqual(fb._pct_rank(vals, 50.0), 50.0)
        # invert 반영: (80, False)+(80, True→20) 평균 = 50
        s = fb.compute_score({"a": (80.0, False), "b": (80.0, True)})
        self.assertAlmostEqual(s, 50.0)
        self.assertIsNone(fb.compute_score({}))

    def test_window_5y_by_date_not_count(self):
        # '5년 백분위' 는 날짜 기준(리뷰 finding: 관측수 260 고정이면 일간=1년·
        # 월간=전체가 돼 페이지 공식 문구와 어긋남). 일간 8년치 → 창은 최근 5년만.
        pts = []
        for y in range(2018, 2026):
            for m in range(1, 13):
                pts.append((f"{y:04d}-{m:02d}-15", float(y)))
        win = fb._window_5y(pts)
        self.assertEqual(len(win), 12 * 5 + 1)          # 5년치(경계일 포함 61)
        self.assertTrue(all(v >= 2020 for v in win))    # 2020-12-15 이후만
        self.assertEqual(fb._window_5y([]), [])

    def test_verdict_bands(self):
        self.assertIn("풍부", fb.score_verdict(75)[0])
        self.assertIn("완화", fb.score_verdict(55)[0])
        self.assertIn("긴축", fb.score_verdict(35)[0])
        self.assertIn("긴축", fb.score_verdict(10)[0])
        self.assertEqual(fb.score_verdict(None)[0], "—")


class MarginSpreadTests(unittest.TestCase):
    """마진 스프레드(판가 YoY − 원가 YoY, 사용자 2026-07-02 Phase2) — 수학·
    graceful·렌더 계약."""

    def _hist(self, start, monthly_step, n=30, y0=2023):
        vals = [start + monthly_step * i for i in range(n)]
        return _mk_hist(vals, y0)

    def test_spread_math(self):
        # 판가 YoY +12%대 vs 원가 YoY ~0% → 스프레드 양수(개선).
        H = {"PCU324110324110": self._hist(100, 1.0),   # 판가 상승
             "WPU0561": self._hist(100, 0.0)}           # 원가 횡보
        # 나머지 쌍 데이터 없음 → 정유만 나와야(graceful).
        out = fb.margin_spreads(H)
        self.assertEqual(len(out), 1)
        m = out[0]
        self.assertIn("정유", m["label"])
        self.assertGreater(m["spread"], 5)
        self.assertAlmostEqual(m["in_yoy"], 0.0, places=6)

    def test_spread_skips_short_data(self):
        H = {"PCU324110324110": self._hist(100, 1.0, n=10),
             "WPU0561": self._hist(100, 0.0, n=10)}
        self.assertEqual(fb.margin_spreads(H), [])

    def test_spread_aligns_to_common_month(self):
        # 발표 lag: 원가 시리즈만 한 달 더(급등 200) — 공통월로 잘라야 5월판가−
        # 6월원가 왜곡이 없다(리뷰 finding). 정렬 후 양쪽 flat → 스프레드 0.
        ho = self._hist(100, 0.0, n=24)                  # 판가 flat, ~2024-12
        hi = self._hist(100, 0.0, n=25)                  # 원가 flat + 1달 더
        hi[-1] = (hi[-1][0], 200.0)                      # 마지막 달 급등
        out = fb.margin_spreads({"PCU324110324110": ho, "WPU0561": hi})
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["spread"], 0.0, places=6)   # 급등월 제외됨

    def test_trend_zero_renders_neutral(self):
        # 변화 0 을 '▲ 개선'으로 오표기 금지(리뷰 finding) — '→ 유지'.
        panel = fb._margin_panel([{"label": "x", "out_yoy": 1.0, "in_yoy": 1.0,
                                   "spread": 0.0, "trend3m": 0.0, "stocks": "s"}])
        self.assertIn("→ 유지", panel)
        self.assertNotIn("▲ 개선", panel)   # 설명문 '마진 개선'과 구분(셀 표기만)

    def test_margin_panel_render(self):
        panel = fb._margin_panel([{"label": "정유 (제품−원유)", "out_yoy": 10.0,
                                   "in_yoy": 2.0, "spread": 8.0, "trend3m": 1.5,
                                   "stocks": "S-Oil"}])
        self.assertIn("마진 스프레드", panel)
        self.assertIn("+8.0pp", panel)
        self.assertIn("▲ 개선", panel)
        self.assertEqual(fb._margin_panel([]), "")      # 빈 → 섹션 생략
        # 페이지 통합: margins 전달 시 패널 포함, 미전달 시 미포함.
        self.assertIn("마진 스프레드",
                      fb.render_ppi_page([], [{"label": "x", "out_yoy": 1.0,
                                               "in_yoy": 0.0, "spread": 1.0,
                                               "trend3m": None, "stocks": "s"}]))

    def test_asof_label_shown(self):
        # 데이터 위젯 = 적용시각 라벨 의무(CLAUDE.md 전역 규칙) — 마진 스프레드
        # 도 시리즈개요 표와 동일하게 행마다 '기준 YYYY-MM' 노출(사용자 2026-07-11).
        H = {"PCU324110324110": self._hist(100, 1.0),
             "WPU0561": self._hist(100, 0.0)}
        out = fb.margin_spreads(H)
        self.assertEqual(len(out), 1)
        self.assertRegex(out[0]["asof"], r"^\d{4}-\d{2}$")
        panel = fb._margin_panel(out)
        self.assertIn(f"기준 {out[0]['asof']}", panel)

    def test_asof_missing_graceful(self):
        # 구 호출부(캐시된 dict 등)에 asof 키가 없어도 크래시 없이 em-dash.
        panel = fb._margin_panel([{"label": "x", "out_yoy": 1.0, "in_yoy": 1.0,
                                   "spread": 0.0, "trend3m": 0.0, "stocks": "s"}])
        self.assertIn("기준 —", panel)


class KrPpiTests(unittest.TestCase):
    """한국 PPI(ECOS 404Y014) — 아이템 이름매칭·행 변환·graceful."""

    def test_match_items_exact_then_shortest_code(self):
        from bot import bok_ecos_client as ec
        # 부분일치는 ITEM_CODE 최단(= ECOS 상위 집계) 우선 — 이름이 짧은 세부
        # 품목('반도체제조용기계')이 집계('반도체및전자표시장치', 코드 짧음)를
        # 이기던 최단이름 휴리스틱 오매칭 회귀(리뷰 finding).
        rows = [{"ITEM_NAME": "총지수", "ITEM_CODE": "*AA"},
                {"ITEM_NAME": "반도체및전자표시장치", "ITEM_CODE": "335"},
                {"ITEM_NAME": "반도체제조용기계", "ITEM_CODE": "33512"},
                {"ITEM_NAME": " 화학제품 ", "ITEM_CODE": "C1"}]
        got = ec._match_items(rows, ["총지수", "반도체", "화학", "없는것"])
        self.assertEqual(got["총지수"], "*AA")           # 정확일치
        self.assertEqual(got["반도체"], "335")           # 코드 최단=상위 집계
        self.assertEqual(got["화학"], "C1")              # strip 후 부분일치
        self.assertNotIn("없는것", got)                  # 무매칭 생략

    def test_transient_failure_not_cached_as_missing(self):
        # 일시 fetch 실패는 '_missing'에 박제 금지 → 캐시 subset 불충족으로
        # 다음 실행이 재시도(리뷰 finding: 자정 blip = 하루종일 행 실종).
        import tempfile
        from pathlib import Path
        from unittest import mock
        from bot import bok_ecos_client as ec
        tmp = Path(tempfile.mkdtemp())
        items = {"StatisticItemList": {"row": [
            {"ITEM_NAME": "총지수", "ITEM_CODE": "*AA"}]}}

        def fake_get(url, timeout=0):
            r = mock.Mock()
            if "StatisticItemList" in url:
                r.json.return_value = items
                return r
            raise RuntimeError("blip")                   # StatisticSearch 실패
        with mock.patch.object(ec, "_CACHE_DIR", tmp), \
             mock.patch.dict("os.environ", {"BOK_ECOS_API_KEY": "k"}), \
             mock.patch.object(ec.requests, "get", side_effect=fake_get):
            out = ec.fetch_kr_ppi_history(["총지수"])
        self.assertEqual(out, {})
        import json as _j
        cached = _j.loads(next(tmp.glob("kr_ppi_*.json")).read_text())
        self.assertEqual(cached.get("_missing"), [])     # failed ≠ missing

    def test_load_kr_ppi_rows(self):
        from unittest import mock
        hist = [(f"{y:04d}-{m:02d}-01", 100.0 + (y - 2023) * 12 + m)
                for y in (2023, 2024, 2025) for m in range(1, 13)]
        with mock.patch("bot.bok_ecos_client.fetch_kr_ppi_history",
                        return_value={"총지수": hist}):
            rows = fb._load_kr_ppi()
        self.assertEqual(len(rows), 1)                   # 매칭된 항목만
        self.assertEqual(rows[0]["cat"], "한국 PPI(ECOS)")
        self.assertTrue(rows[0]["id"].startswith("ECOS:"))
        self.assertIn("sig", rows[0])                    # 동일 신호 파이프라인

    def test_load_kr_ppi_graceful(self):
        from unittest import mock
        with mock.patch("bot.bok_ecos_client.fetch_kr_ppi_history",
                        side_effect=RuntimeError("down")):
            self.assertEqual(fb._load_kr_ppi(), [])


class RenderTests(unittest.TestCase):
    def _ppi_row(self):
        hist = _mk_hist([100 + i for i in range(24)])
        m = fb.series_metrics(hist)
        key, label, note = fb._signal(m)
        return {**PPI_SERIES[0], **m, "sig": key, "sig_label": label,
                "note": note, "hist": [(d[:7], v) for d, v in hist]}

    def test_ppi_render_smoke(self):
        html = fb.render_ppi_page([self._ppi_row()])
        self.assertIn("ppi-data", html)
        self.assertIn("PPI", html)
        self.assertIn("KST", html)                     # 적용시각 라벨(규칙 #10b)
        self.assertIn("FRED API", html)                # 소스 라벨
        self.assertNotIn("FRED 데이터 없음", html)

    def test_ppi_render_empty_banner(self):
        # 키 부재/0건 → silent drop 금지: 페이지는 생성 + 원인 배너.
        html = fb.render_ppi_page([])
        self.assertIn("FRED 데이터 없음", html)
        self.assertIn("FRED_API_KEY", html)

    def test_liquidity_render_smoke(self):
        hist = _mk_hist([100 + i for i in range(24)])
        m = fb.series_metrics(hist)
        row = {**LIQ_SERIES[0], **m, "hist": hist}
        html = fb.render_liquidity_page(
            [row], {"net_liq": [("2026-01-01", 6000.0)],
                    "components": {"m2_yoy": 80.0}}, 62.5)
        self.assertIn("liq-data", html)
        self.assertIn("62", html)                      # 점수 표기
        self.assertIn("순유동성", html)
        self.assertIn("5년 트레일링 백분위", html)     # 공식 문서화(투명성)
        self.assertIn("KST", html)
        self.assertIn("M2 YoY", html)                  # 구성요소 한글 라벨(영문키 노출 금지)
        self.assertIn("ℹ️ 사용법", html)               # 첫사용자 가이드(같은 commit 룰)

    def test_chart_resilience_contract(self):
        # (a) Chart.js = 로컬 벤더 파일 참조(CDN 아님 — 오프라인 내성),
        # (b) typeof Chart 가드, (c) 유동성: 표 렌더가 순유동성 차트보다 먼저
        # (CDN/파일 차단 시 표까지 백지 되던 IIFE abort — 리뷰 finding).
        hist = _mk_hist([100 + i for i in range(24)])
        m = fb.series_metrics(hist)
        row = {**LIQ_SERIES[0], **m, "hist": hist}
        html = fb.render_liquidity_page([row], {"net_liq": [("2026-01-01", 1.0)],
                                                "components": {}}, 50.0)
        self.assertIn('src="chart.umd.min.js"', html)
        self.assertNotIn("cdnjs.cloudflare.com", html)
        self.assertIn("typeof Chart==='undefined'", html)
        self.assertLess(html.index("pills();table();"), html.index("nl-chart'),"))
        # PPI 도 로컬 참조 + 가드 + 가이드.
        p = fb.render_ppi_page([])
        self.assertIn('src="chart.umd.min.js"', p)
        self.assertIn("typeof Chart==='undefined'", p)
        self.assertIn("ℹ️ 사용법", p)

    def test_liquidity_unit_map_contract(self):
        # fv() 단위 스케일링 계약 — 원시 FRED 단위 맵 + 카탈로그 교정(리뷰:
        # WALCL 'M USD' 6,628,894 를 '6.62M' 으로 내던 버그 / WTREGEN 은 B USD).
        hist = _mk_hist([100 + i for i in range(24)])
        m = fb.series_metrics(hist)
        row = {**LIQ_SERIES[0], **m, "hist": hist}
        html = fb.render_liquidity_page([row], {"net_liq": [], "components": {}}, None)
        self.assertIn("'M USD':[1e6,'$']", html)
        self.assertIn("'B USD':[1e9,'$']", html)
        self.assertIn("'100M JPY':[1e8,'¥']", html)
        wt = next(s for s in LIQ_SERIES if s["id"] == "WTREGEN")
        bm = next(s for s in LIQ_SERIES if s["id"] == "BOGMBASE")
        self.assertEqual(wt["unit"], "B USD")           # FRED 원시(표시변환 아님)
        self.assertEqual(bm["unit"], "M USD")

    def test_time_theme_wired(self):
        # 시간 테마(19~07 KST 다크) — dashboard._THEME_JS 재사용 + 라이트 기본
        # /다크 오버라이드 CSS 변수(사용자 2026-07-02 '우리하는것처럼').
        for html in (fb.render_ppi_page([]),
                     fb.render_liquidity_page([], {}, None)):
            self.assertIn("Asia/Seoul", html)              # 테마 JS(KST 시간판정)
            self.assertIn('data-theme="dark"', html)       # 다크 오버라이드 셀렉터
            self.assertIn("--bg:#f7f8f9", html)            # 라이트 기본 팔레트
            # 테마 스크립트가 <style> 앞(head) — 로드 플래시 방지 계약.
            self.assertLess(html.index("Asia/Seoul"), html.index("<style>"))

    def test_six_hour_regen_and_pairs_wired(self):
        # 6시간 주기 재생성 태스크 배선(사용자 2026-07-02) + 마진쌍 확장 계약.
        src = open("bot/telegram_bot.py", encoding="utf-8").read()
        self.assertIn("_periodic_fred_boards", src)
        self.assertIn("asyncio.sleep(6 * 3600)", src)
        self.assertIn("to_thread(regenerate_fred_boards)", src)   # 이벤트루프 보호
        self.assertGreaterEqual(len(fb._MARGIN_PAIRS), 17)        # 7 + 2차 10쌍
        keys = {p["key"] for p in fb._MARGIN_PAIRS}
        self.assertEqual(len(keys), len(fb._MARGIN_PAIRS))        # key 유일

    def test_live_fx_overlay_wired(self):
        # 환율 실시간 오버레이(사용자 2026-07-02, 2026-07-14 엔/위안 1차 +
        # 유로·파운드·스위스프랑 2차 확장 — 대만달러는 FRED 미수록 히스토리
        # 없이 추가했다가 사용자 요청으로 제외) — 유동성 페이지가 api/usd<pair>
        # 를 로드 시+5분 주기로 fetch, 대응 FRED 시리즈 최신값만 덮는 계약.
        html = fb.render_liquidity_page([], {}, None)
        self.assertIn("fetch('api/'+p[0])", html)          # 공통 fetch 경로
        pairs = [("usdkrw", "DEXKOUS"), ("usdjpy", "DEXJPUS"),
                 ("usdcny", "DEXCHUS"), ("usdeur", "DEXUSEU"),
                 ("usdgbp", "DEXUSUK"), ("usdchf", "DEXSZUS")]
        for pair, series_id in pairs:
            self.assertIn(f"'{pair}','{series_id}'", html)
            self.assertIn(series_id, html)
        self.assertIn("setInterval(liveFx,300000)", html)
        self.assertNotIn("usdtwd", html)     # 대만달러 제외(사용자 2026-07-14)
        # 서버 라우트·핸들러 배선(E2E grep — 헬퍼만 만들고 미배선 방지).
        srv = open("bot/dashboard_server.py", encoding="utf-8").read()
        self.assertIn("_handle_fx_api", srv)
        self.assertIn("fetch_kr_fx", srv)
        self.assertIn("fetch_world_fx", srv)
        for pair, _ in pairs:
            self.assertIn(f'"{pair}"', srv)
        self.assertNotIn('"usdtwd"', srv)

    def test_payload_script_safe(self):
        # '<' escape(</script> 조기 종료 차단) — valuechain 패턴 동일 계약.
        row = self._ppi_row()
        row["stocks"] = "위험한 <script> 문자열"
        html = fb.render_ppi_page([row])
        import re
        m = re.search(r'<script id="ppi-data"[^>]*>(.*?)</script>', html, re.S)
        self.assertNotIn("<script>", m.group(1))


class DiscontinuedSweepTests(unittest.TestCase):
    """FRED 중단 시리즈 전수 검토(사용자 2026-07-04): 대체/삭제 + stale 가드
    + 금리 %p + 월간 다운샘플. 삭제 근거: OECD MEI 중단(2023-11, M3 대체계열
    MABMM301 도 동일 중단) — BOJ/ECB 총자산이 해당국 유동성 현행 커버."""

    def test_catalog_dead_series_removed(self):
        ids = {s["id"] for s in LIQ_SERIES}
        for dead in ("MANMM101JPM189S", "MANMM101EZM189S", "MANMM101KRM189S",
                     "INTDSRKRM193N", "INTDSRCNM193N"):
            self.assertNotIn(dead, ids)
        # 39 −CNM2 +플래그십 8(2026-07-05) +환율 3(유로·파운드·스위스프랑, 2026-07-14
        # — 대만달러는 FRED 미수록 히스토리 없이 추가했다가 사용자 요청으로 제외)
        # +5(리서치 에이전트 4차 확장, 2026-07-24 — China M2 는 AK:CNM2 재발
        # 리스크로 제외)
        self.assertEqual(len(LIQ_SERIES), 54)

    def test_catalog_alt_sources_wired(self):
        srcs = {s["id"]: s.get("src") for s in LIQ_SERIES if s.get("src")}
        self.assertEqual(srcs, {"ECOS:M2": "ecos:m2",
                                "ECOS:BASE": "ecos:base_rate",
                                "AK:LPR1Y": "ak:lpr1y",
                                # AK:CNM2 는 라이브에서 12개월+ stale 판정
                                # (라벨/소스 원인 미상) → 삭제(2026-07-04).
                                # 2026-07-24 재검토에서도 재발 리스크 미해소로
                                # 재등재 보류(LiqExpansion20260724Tests 참조)
                                "ECOS:KR10Y": "ecos:kr10y",
                                "ECOS:FXRESERVE": "ecos:fx_reserve"})
        # 대체 소스 함수 실재(배선 E2E)
        from bot import bok_ecos_client, akshare_client
        self.assertIn("m2", bok_ecos_client._SERIES)
        self.assertTrue(callable(akshare_client.lpr_1y_history))

    def test_monthly_downsample(self):
        # 월별 마지막 관측 + 날짜는 day-01 정규화(리뷰 2026-07-04 Major #1 —
        # 월말 날짜를 그대로 두면 _value_at 날짜컷이 매달 한 달씩 건너뜀).
        daily = [("2026-05-01", 1.0), ("2026-05-30", 1.1),
                 ("2026-06-15", 1.2), ("2026-06-30", 1.3)]
        self.assertEqual(fb._monthly(daily),
                         [("2026-05-01", 1.1), ("2026-06-01", 1.3)])
        monthly = [(f"2026-0{m}-01", float(m)) for m in range(1, 8)]
        self.assertEqual(fb._monthly(monthly), monthly)  # 월간 무변화

    def test_monthly_no_month_skip_on_eom_dates(self):
        # 월말 관측(주간 수요일 패턴 포함)에서 1M 이 두 달 전으로 밀리지
        # 않는지 — 정규화 전엔 06-24 앵커의 1M 이 04-29 를 집던 회귀.
        weekly = []
        import datetime as dt
        d = dt.date(2025, 1, 1)
        while d <= dt.date(2026, 6, 24):
            if d.weekday() == 2:                      # 수요일
                weekly.append((d.isoformat(), float(d.month)))
            d += dt.timedelta(days=1)
        m = fb.series_metrics(fb._monthly(weekly))
        # 앵커 2026-06 → 1M 전 = 2026-05 관측(값 5.0). mom = (6-5)/5 = +20%
        self.assertAlmostEqual(m["mom"], 20.0, places=1)

    def test_rate_deltas_unit_gate(self):
        # is_rate 라도 unit != '%' (DXY·M2V·NFCI 등 지수/비율)면 %p 미적용
        # (리뷰 2026-07-04 Major #2) — _load_liq 게이트 계약(소스 검사).
        src = open("bot/fred_boards.py", encoding="utf-8").read()
        self.assertIn("s.get(\"is_rate\") and s.get(\"unit\") == \"%\"", src)

    def test_rate_deltas_pp(self):
        mh = [(f"2025-{m:02d}-01", 2.15) for m in range(1, 13)] + [("2026-01-01", 2.40)]
        m = fb.series_metrics(mh)
        fb._rate_deltas(m, mh)
        self.assertEqual(m["mom"], 0.25)
        self.assertEqual(m["yoy"], 0.25)   # 11.6% 가 아니라 +0.25%p
        self.assertTrue(m["rate_delta"])

    def test_mark_stale(self):
        r = {"latest_date": "2023-10"}
        fb._mark_stale(r)
        self.assertTrue(r.get("stale"))
        r2 = {"latest_date": "2026-06"}
        fb._mark_stale(r2)
        self.assertNotIn("stale", r2)
        r3 = {"latest_date": "실시간"}
        fb._mark_stale(r3)          # 파싱 불가 graceful
        self.assertNotIn("stale", r3)

    def test_alt_history_normalizes(self):
        from bot import bok_ecos_client as bec
        orig = bec.fetch_series_points
        bec.fetch_series_points = lambda key, lookback_days=None: [("202605", 3000.0)]
        try:
            self.assertEqual(fb._alt_history("ecos:m2"), [("2026-05-01", 3000.0)])
        finally:
            bec.fetch_series_points = orig
        self.assertEqual(fb._alt_history("unknown:x"), [])

    def test_render_stale_badge_and_pp(self):
        row = {"id": "X", "name": "t", "category": "기준금리", "unit": "%",
               "is_rate": True, "latest": 2.4, "latest_date": "2023-10",
               "mom": 0.25, "m3": 0.25, "m6": 0.25, "yoy": 0.25, "total": 1.0,
               "peak": 2.4, "peak_date": "2026-01", "from_peak": 0.0,
               "trough_after_peak": None, "recovery": None, "momentum": None,
               "rate_delta": True, "stale": True, "hist": [["2023-10-01", 2.4]]}
        html = fb.render_liquidity_page([row], {}, None)
        self.assertIn("function pcd(r,v,dg)", html)
        self.assertIn("%p", html)
        self.assertIn("⚠️지연", html)
        self.assertIn(".stale{", html)
        self.assertIn("한국은행 ECOS", html)   # 소스 라벨(가이드·헤더)
        # 행→페이로드 배선(리뷰 Minor #4 — 위 리터럴은 정적 JS 라 항상 존재,
        # 서버가 플래그를 실제로 싣는지는 JSON 에서 확인).
        import re
        mjs = re.search(r'<script id="liq-data"[^>]*>(.*?)</script>', html, re.S)
        self.assertIn('"stale": true', mjs.group(1))
        self.assertIn('"rate_delta": true', mjs.group(1))


if __name__ == "__main__":
    unittest.main()


class EcosM2NameResolutionTests(unittest.TestCase):
    """한국 M2 아이템 이름해석(2026-07-04 라이브 fix) — 하드코딩 BBHA00 이
    INFO-200(데이터 없음)이라 KR PPI 와 같은 StatisticItemList 런타임 매칭 +
    말잔(101Y003) 표 폴백으로 전환. 코드 하드코딩 재발 방지 계약."""

    def test_m2_config_uses_name_resolution(self):
        from bot import bok_ecos_client as bec
        cfg = bec._SERIES["m2"]
        self.assertEqual(cfg.get("item_name"), "M2")
        self.assertNotIn("item", cfg)              # 코드 하드코딩 금지
        # 현행 신계열(1.1장) 표 — 구계열 101Y00x(1.7장, ~2004 종료) 금지
        # (VM TableList 확인 2026-07-04).
        self.assertEqual(cfg["table"], "161Y006")
        self.assertIn("161Y005", cfg.get("alt_tables", []))
        self.assertFalse(any(t.startswith("101Y") for t in
                             [cfg["table"]] + cfg.get("alt_tables", [])))

    def test_item_list_table_param(self):
        # _fetch_item_list 가 table 인자화(KR PPI 기본값 유지) — 소스 계약.
        import inspect
        from bot import bok_ecos_client as bec
        sig = inspect.signature(bec._fetch_item_list)
        self.assertEqual(sig.parameters["table"].default, bec._KR_PPI_TABLE)

    def test_match_items_exact_over_variant(self):
        from bot import bok_ecos_client as bec
        rows = [{"ITEM_NAME": "M2(계절조정)", "ITEM_CODE": "XZZ999"},
                {"ITEM_NAME": "M2", "ITEM_CODE": "ABC100"}]
        self.assertEqual(bec._match_items(rows, ["M2"]), {"M2": "ABC100"})

    def test_filter_series_items_blocks_legacy(self):
        # 실사례(2026-07-04): 101Y004 'M2' 가 A/M/Q 로 존재하나 전부 END 2004
        # (구계열) — 주기 일치 + 신선 END_TIME 만 통과.
        from bot.bok_ecos_client import _filter_series_items
        rows = [
            {"ITEM_CODE": "BBHA00", "CYCLE": "A", "END_TIME": "2003"},
            {"ITEM_CODE": "BBHA00", "CYCLE": "M", "END_TIME": "200409"},
            {"ITEM_CODE": "NEW100", "CYCLE": "M", "END_TIME": "202605"},
            {"ITEM_CODE": "QQQ", "CYCLE": "Q", "END_TIME": "2026Q1"},
            {"ITEM_CODE": "BAD", "CYCLE": "M", "END_TIME": ""},
        ]
        out = _filter_series_items(rows, "M", "202506")
        self.assertEqual([r["ITEM_CODE"] for r in out], ["NEW100"])

    def test_dead_missile_ppi_removed(self):
        # PCU336414336414 — FRED 400(미존재), BLS 미발행 확인(2026-07-04) →
        # 삭제(항공우주 상위그룹 PCU3364133641 이 커버). 사용자 '없는건 삭제'.
        self.assertFalse(any(s["id"] == "PCU336414336414" for s in PPI_SERIES))
        self.assertEqual(len(PPI_SERIES), 105)  # +발굴 3차 14(2026-07-05) +4차 10(2026-07-24)

    def test_stale_drop_and_note(self):
        # 12개월+ 미갱신 = 목록 자동 제외 + 하단 제외 안내(사용자 2026-07-04
        # '중단된거는 삭제'). 6~12개월 = ⚠️지연 배지(조기경고).
        self.assertEqual(fb._DROP_AFTER_MONTHS, 12)
        src = open("bot/fred_boards.py", encoding="utf-8").read()
        self.assertIn("_dropped_note", src)
        self.assertEqual(src.count("age is not None and age >= _DROP_AFTER_MONTHS"), 3)  # PPI+CPI+LIQ
        # 화면 코멘트는 제거(사용자 2026-07-04) — journal 경고가 가시성 담당.
        html = fb.render_ppi_page([], [], dropped=["Small Arms Ammunition Mfg (PCU332992332992)"])
        self.assertNotIn("소스 중단(12개월+ 미갱신)", html)
        self.assertNotIn("PCU332992332992", html)
        # 지연 배지 경계: 6개월+ True / 미만 없음
        import datetime as _dt
        from zoneinfo import ZoneInfo
        now = _dt.datetime.now(ZoneInfo("Asia/Seoul"))
        old = (now - _dt.timedelta(days=200)).strftime("%Y-%m")
        fresh = (now - _dt.timedelta(days=60)).strftime("%Y-%m")
        r1, r2 = {"latest_date": old}, {"latest_date": fresh}
        fb._mark_stale(r1); fb._mark_stale(r2)
        self.assertTrue(r1.get("stale"))
        self.assertNotIn("stale", r2)

    def test_verified_additions_20260704(self):
        # 검증 에이전트 통과 신규 4종(방산전자·농기계 P-variant·원료의약품·
        # 창고물류) + 중복 카테고리('Construction & Infra') 통합 계약.
        ids = {s["id"] for s in PPI_SERIES}
        for want in ("PCU334511334511", "PCU333111333111P",
                     "PCU325411325411", "PCU493110493110"):
            self.assertIn(want, ids)
        self.assertNotIn("PCU336992336992", ids)   # FRED 미존재 — 미등재
        # 탄약 2종 = BLS 2025-06 중단 확정 → 물리 삭제(런타임 제외와 별개)
        self.assertNotIn("PCU332992332992", ids)
        self.assertNotIn("PCU332993332993", ids)
        # PCU334220334220(통신장비)은 중단 '미확정' — 카탈로그 유지, 런타임
        # 자동제외 가드가 판정(죽었으면 제외 목록 표기)
        cats = {s["cat"] for s in PPI_SERIES}
        self.assertNotIn("Construction & Infra", cats)   # 중복 필 통합
        from collections import Counter
        dup = [k for k, v in Counter(s["id"] for s in PPI_SERIES).items() if v > 1]
        self.assertEqual(dup, [])

    def test_review_fixes_20260704b(self):
        # 리뷰 fix 고정: ① KR PPI 포함 통합 drop 패스 ② FRED 삭제(빈 hist)도
        # 제외 목록 표기 ③ Q 포맷 END_TIME 정규화 ④ CN M2 발표일 시프트.
        src = open("bot/fred_boards.py", encoding="utf-8").read()
        assert "rows += _load_kr_ppi()" in src
        # 통합 패스가 병합 '뒤'에 있는지 — KR 행 우회 갭 재발 방지
        assert src.index("rows += _load_kr_ppi()") < src.index(
            "age is not None and age >= _DROP_AFTER_MONTHS")
        assert src.count("— 데이터 없음") >= 2          # PPI+LIQ 양쪽
        from bot.bok_ecos_client import _filter_series_items
        rows = [{"ITEM_CODE": "Q1", "CYCLE": "Q", "END_TIME": "2026Q2"},
                {"ITEM_CODE": "Q2", "CYCLE": "Q", "END_TIME": "2004Q3"}]
        self.assertEqual(
            [r["ITEM_CODE"] for r in _filter_series_items(rows, "Q", "202506")],
            ["Q1"])
        # (구 CN M2 발표월 보정 계약은 AK:CNM2 삭제와 함께 제거, 2026-07-04)


class LiqExpansion20260705Tests(unittest.TestCase):
    """유동성 확장 2차(사용자 '더 추가') — Fed 원천 플래그십 8종 + CNM2 삭제
    + 제외 코멘트 배너 제거(journal 로그만)."""

    def test_flagship_additions(self):
        ids = {s["id"] for s in LIQ_SERIES}
        for w in ("DGS30", "DEXJPUS", "DEXCHUS", "CBBTCUSD",
                  "WLCFLPCL", "COMPOUT", "DPSACBW027SBOG", "RMFSL"):
            self.assertIn(w, ids)
        self.assertNotIn("AK:CNM2", ids)     # stale 판정 → 삭제(2026-07-04)
        dgs30 = next(s for s in LIQ_SERIES if s["id"] == "DGS30")
        self.assertTrue(dgs30["is_rate"])    # %p 표기 대상
        fx = next(s for s in LIQ_SERIES if s["id"] == "DEXJPUS")
        self.assertFalse(fx["is_rate"])      # 환율 레벨은 %변화 유지

    def test_dropped_note_removed(self):
        self.assertEqual(fb._dropped_note(["X (Y)"]), "")   # 화면 코멘트 없음
        src = open("bot/fred_boards.py", encoding="utf-8").read()
        self.assertIn("소스 중단 제외", src)                # journal 경고는 유지

    def test_second_batch_verified_additions(self):
        # 2차 확장(2026-07-05) — 웹 교차검증 'current' 확정분만. 탈락:
        # 광케이블 335921(2025-06 중단)·아연(시리즈 없음)·택배 492110(중단
        # 의심)·무선 517312(FRED 미존재 — WPU3721 상품지수로 대체).
        ids = {s["id"] for s in PPI_SERIES}
        for w in ("PCU336411336411", "PCU481112481112", "PCU482111482111",
                  "WPU0543", "WPU1012", "PCU5182105182105", "WPU3721"):
            self.assertIn(w, ids)
        for dead in ("PCU335921335921", "PCU492110492110", "PCU517312517312"):
            self.assertNotIn(dead, ids)

    def test_third_batch_discovered_additions(self):
        # 발굴 3차(2026-07-05) — 4렌즈 스캔, 2026 관측 증거 있는 것만 14종.
        # 탄약(PCU332992332992)은 에이전트가 '현행'이라 봤으나 **우리 라이브
        # 실측(2025-06 stale)이 반대 증거 → 실측 우선 재등재 금지**.
        ids = {s["id"] for s in PPI_SERIES}
        for w in ("PCU3339233339233", "PCU33323332", "PCU325212325212",
                  "PCU326199326199A", "PCU3352233522", "WPU1178", "WPU102406",
                  "WPU057203", "WPU057303", "PCU22121022121012",
                  "PCU622110622110", "PCU541330541330", "PCU541810541810",
                  "PCU523110523110201"):
            self.assertIn(w, ids)
        self.assertNotIn("PCU332992332992", ids)
        self.assertNotIn("PCU5221105221101", ids)   # 은행 sub — Medium 보류


class FxExpansion20260714Tests(unittest.TestCase):
    """환율 2차 확장(사용자 '대만달러, 유로, 파운드, 스위스프랑도 추가... 네이버
    실시간으로') — FRED 3종(DEXUSEU/DEXUSUK/DEXSZUS). 대만은 FRED 미수록이라
    naver_fx_latest 전용 최소 row 로 추가했으나, 사용자 '대만달러없으면 이건
    그냥 없애줘' 요청으로 제외(naver_fx_latest 메커니즘 자체도 함께 제거 —
    다른 통화가 재사용할 때까지 죽은 코드 유지 안 함)."""

    def test_new_currencies_present(self):
        ids = {s["id"] for s in LIQ_SERIES}
        for w in ("DEXUSEU", "DEXUSUK", "DEXSZUS"):
            self.assertIn(w, ids)
            s = next(x for x in LIQ_SERIES if x["id"] == w)
            self.assertEqual(s["category"], "환율")
            self.assertFalse(s["is_rate"])
        self.assertNotIn("USDTWD", ids)

    def test_naver_fx_latest_mechanism_removed(self):
        # 대만달러 제외와 함께 naver_fx_latest 라우팅 자체도 제거(미사용 코드
        # 방치 금지) — _load_liq 는 원래의 단순 FRED/_alt_history 경로만.
        src = open("bot/fred_boards.py", encoding="utf-8").read()
        self.assertNotIn("naver_fx_latest", src)
        self.assertNotIn("_naver_fx_latest_row", src)


class MarginPpiLiqExpansion20260724Tests(unittest.TestCase):
    """4차 확장(사용자 2026-07-24 '마진스프레드/PPI/유동성 다 업데이트지속되는거면
    모두 적용') — 3개 리서치 에이전트 검증분: 마진쌍 10 + PPI 10 + 유동성 5.
    China M2(AKShare)는 후보였으나 동일 접근(AK:CNM2)이 2026-07-04 라이브에서
    이미 한 번 원인불명 stale 로 삭제된 이력이 있어 재검증 없이 재등재하지
    않음(⛔ 과거 실수 반복 금지)."""

    def test_margin_pairs_third_batch(self):
        from bot.fred_boards import _MARGIN_PAIRS
        self.assertEqual(len(_MARGIN_PAIRS), 27)          # 17 + 3차 10쌍
        keys = {p["key"] for p in _MARGIN_PAIRS}
        self.assertEqual(len(keys), len(_MARGIN_PAIRS))   # key 유일
        for want in ("semis", "aluminum", "aerospace", "gasutil", "cosmetics",
                     "pharma", "apparel", "dairy", "telecom_eq", "glass"):
            self.assertIn(want, keys)

    def test_ppi_fourth_batch_ids(self):
        ids = {s["id"] for s in PPI_SERIES}
        for want in ("PCU3344133344131", "PCU313313", "PCU21222122",
                     "PCU21212121", "PCU339920339920", "PCU312230312230",
                     "PCU311615311615", "PCU311511311511",
                     "PCU312140312140P", "PCU3372133721"):
            self.assertIn(want, ids)
        cats = {s["cat"] for s in PPI_SERIES}
        for want in ("Textile & Apparel", "Mining & Resources",
                     "Tobacco & Beverage", "Furniture & Home"):
            self.assertIn(want, cats)

    def test_liq_fourth_batch_ids_and_wiring(self):
        ids = {s["id"] for s in LIQ_SERIES}
        for want in ("EFFR", "ECOS:FXRESERVE", "TRESEGCNM052N", "FDHBFIN",
                     "ANFCI"):
            self.assertIn(want, ids)
        self.assertNotIn("AK:CNM2", ids)   # 재검증 없이 재등재 금지(위 독스트링)
        fxr = next(s for s in LIQ_SERIES if s["id"] == "ECOS:FXRESERVE")
        self.assertEqual(fxr["src"], "ecos:fx_reserve")
        # fx_reserve 는 bok_ecos_client 에 2026-06-23 부터 정의돼 있었으나
        # 미배선 상태였던 소스 — _SERIES 실재 + _alt_history 배선 둘 다 확인.
        from bot import bok_ecos_client
        self.assertIn("fx_reserve", bok_ecos_client._SERIES)

    def test_alt_history_fx_reserve_wired(self):
        from bot import fred_boards as fb
        from bot import bok_ecos_client as bec
        orig = bec.fetch_series_points
        bec.fetch_series_points = lambda key, lookback_days=None: (
            [("202606", 4321.0)] if key == "fx_reserve" else [])
        try:
            self.assertEqual(fb._alt_history("ecos:fx_reserve"),
                             [("2026-06-01", 4321.0)])
        finally:
            bec.fetch_series_points = orig


class CpiBoardTests(unittest.TestCase):
    """CPI 보드(cpi.html, 사용자 2026-07-24 'PPI 처럼 CPI 도') — PPI 와 동일
    인프라(신호·staleness·차트) 재사용 계약. 마진 스프레드 패널은 CPI 에
    대응 개념 없어 render_cpi_page 에 없음(의도적, 누락 아님)."""

    def test_catalog_fields(self):
        ids = [s["id"] for s in CPI_SERIES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 52)   # 1차 40(2026-07-24) + 2차 감사 12종
        for s in CPI_SERIES:
            for k in ("id", "name", "cat", "stocks"):
                self.assertTrue(s.get(k), f"{s.get('id')} missing {k}")
        for want in ("CPIAUCSL", "CPILFESL", "STICKCPIM157SFRBATL",
                     "CUSR0000SAH1", "CPIMEDSL", "CUSR0000SETG01"):
            self.assertIn(want, ids)
        # 2차 감사(2026-07-24, 사용자 'PPI 처럼 최대한 반영') 추가분 — 신발·
        # 아동복·자동차부품·상하수도·영상음향·케이블·IT·완구·교육보육·펫푸드.
        for want in ("CUSR0000SEAE", "CUSR0000SEAF", "CUSR0000SAA1",
                     "CUSR0000SAA2", "CUSR0000SETC", "CUSR0000SEHG",
                     "CUSR0000SERA", "CUSR0000SERA02", "CUSR0000SEEE",
                     "CUSR0000SERE01", "CUSR0000SEEB", "CUSR0000SS61031"):
            self.assertIn(want, ids)
        # 분기 실질임금 시리즈는 월간 파이프라인과 주기 안 맞아 이번 배치엔
        # 의도적으로 제외(에이전트 권고 — 별도 콜아웃 카드 후보).
        self.assertNotIn("LES1252881600Q", ids)
        # 감사에서 확인 불가로 스킵된 후보들(추측 등재 금지 계약).
        for skipped in ("CUSR0000SEAG", "CUSR0000SEHD"):
            self.assertNotIn(skipped, ids)

    def test_load_kr_cpi_rows(self):
        from unittest import mock
        hist = [(f"{y:04d}-{m:02d}-01", 100.0 + (y - 2023) * 12 + m)
                for y in (2023, 2024, 2025) for m in range(1, 13)]
        with mock.patch("bot.bok_ecos_client.fetch_kr_cpi_history",
                        return_value={"총지수": hist}):
            rows = fb._load_kr_cpi()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cat"], "한국 CPI(ECOS)")
        self.assertTrue(rows[0]["id"].startswith("ECOS:"))
        self.assertIn("sig", rows[0])

    def test_load_kr_cpi_graceful(self):
        from unittest import mock
        with mock.patch("bot.bok_ecos_client.fetch_kr_cpi_history",
                        side_effect=RuntimeError("down")):
            self.assertEqual(fb._load_kr_cpi(), [])

    def test_kr_cpi_uses_dedicated_table_not_ppi(self):
        # KR CPI 는 901Y009(cpi_idx 총지수와 같은 표), KR PPI 는 404Y014 —
        # 공용 fetch_kr_price_index_history 로 리팩터링 후에도 두 물가종류가
        # 서로 다른 표/캐시파일을 쓰는지 고정(캐시 충돌 방지 계약).
        from bot import bok_ecos_client as bec
        self.assertEqual(bec._KR_CPI_TABLE, "901Y009")
        self.assertEqual(bec._KR_PPI_TABLE, "404Y014")
        self.assertNotEqual(bec._KR_CPI_TABLE, bec._KR_PPI_TABLE)

    def _cpi_row(self):
        hist = _mk_hist([100 + i for i in range(24)])
        m = fb.series_metrics(hist)
        key, label, note = fb._signal(m)
        return {**CPI_SERIES[0], **m, "sig": key, "sig_label": label,
                "note": note, "hist": [(d[:7], v) for d, v in hist]}

    def test_render_smoke(self):
        html = fb.render_cpi_page([self._cpi_row()])
        self.assertIn("cpi-data", html)
        self.assertIn("CPI", html)
        self.assertIn("KST", html)
        self.assertIn("FRED API", html)
        self.assertNotIn("FRED 데이터 없음", html)
        self.assertNotIn("마진 스프레드", html)   # CPI 대응 개념 없음(의도적)

    def test_render_empty_banner(self):
        html = fb.render_cpi_page([])
        self.assertIn("FRED 데이터 없음", html)
        self.assertIn("FRED_API_KEY", html)

    def test_nav_and_regen_wired(self):
        self.assertIn('href="cpi.html">🛒 CPI</a>', fb._NAV)
        src = open("bot/fred_boards.py", encoding="utf-8").read()
        assert '(ARCHIVE_ROOT / "cpi.html").write_text' in src
        assert "_load_cpi()" in src and "render_cpi_page(" in src
