"""FRED 보드(bot.fred_boards) 순수 로직 회귀 — 지표계산·신호룰·순유동성·점수·렌더.
fetch(FRED I/O)는 graceful 의존이라 제외, 명시 히스토리로 순수 검증."""
import unittest

from bot import fred_boards as fb
from bot.fred_boards_catalog import LIQ_SERIES, PPI_SERIES


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
        self.assertGreaterEqual(len(ids), 45)          # 원본 32 + 확장 15
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
        # WALCL 7,000,000M$(=7000B) − TGA 800,000M$(=800B) − RRP 200B = 6000B.
        walcl = [("2026-01-01", 7_000_000.0)]
        tga = [("2026-01-01", 800_000.0)]
        rrp = [("2026-01-01", 200.0)]
        nl = fb.net_liquidity(walcl, tga, rrp)
        self.assertEqual(nl, [("2026-01-01", 6000.0)])

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

    def test_verdict_bands(self):
        self.assertIn("풍부", fb.score_verdict(75)[0])
        self.assertIn("완화", fb.score_verdict(55)[0])
        self.assertIn("긴축", fb.score_verdict(35)[0])
        self.assertIn("긴축", fb.score_verdict(10)[0])
        self.assertEqual(fb.score_verdict(None)[0], "—")


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

    def test_payload_script_safe(self):
        # '<' escape(</script> 조기 종료 차단) — valuechain 패턴 동일 계약.
        row = self._ppi_row()
        row["stocks"] = "위험한 <script> 문자열"
        html = fb.render_ppi_page([row])
        import re
        m = re.search(r'<script id="ppi-data"[^>]*>(.*?)</script>', html, re.S)
        self.assertNotIn("<script>", m.group(1))


if __name__ == "__main__":
    unittest.main()
