"""수출 스크리닝 primitive 테스트 — P/Q 분해·진도율·4그룹·기저캡 (Tier 1+2,
보고서 차용 2026-06-17). 순수함수라 네트워크 0·결정적."""
import unittest

from trade import industry, screening


class PqDecomposeTests(unittest.TestCase):
    def test_price_volume_split(self):
        # 수출액 100→300(+200%) = 단가 10→15(+50%) × 물량 10→20(+100%).
        months = {"2025-05": 100, "2026-05": 300}
        wgts = {"2025-05": 10, "2026-05": 20}
        d = screening.pq_decompose(months, wgts)
        self.assertAlmostEqual(d["val_yoy"], 200.0, places=1)
        self.assertAlmostEqual(d["price_yoy"], 50.0, places=1)
        self.assertAlmostEqual(d["vol_yoy"], 100.0, places=1)
        self.assertEqual(d["driver"], "P·Q 동반↑(물량+판가)")

    def test_price_led(self):
        # 물량 동일, 단가만 상승 → 단가 주도.
        d = screening.pq_decompose({"2025-05": 100, "2026-05": 150},
                                   {"2025-05": 10, "2026-05": 10})
        self.assertGreater(d["price_yoy"], 0)
        self.assertAlmostEqual(d["vol_yoy"], 0.0, places=1)
        self.assertEqual(d["driver"], "단가 주도(판가↑·물량↓)")

    def test_no_weight_returns_none(self):
        self.assertIsNone(screening.pq_decompose({"2026-05": 100}, {}))
        self.assertIsNone(screening.pq_decompose({}, {}))


class ProgressRateTests(unittest.TestCase):
    def test_quarter_pace(self):
        # 2026-05 = Q2 2번째월 → pace 66.7%. QTD(4+5)=200 / 전년 Q2전체(50*3=150)
        # = 133.3% > 66.7% → 우수.
        months = {"2025-04": 50, "2025-05": 50, "2025-06": 50,
                  "2026-04": 100, "2026-05": 100}
        p = screening.progress_rate(months)
        self.assertEqual(p["quarter"], 2)
        self.assertEqual(p["elapsed"], 2)
        self.assertAlmostEqual(p["pace"], 66.6667, places=1)
        self.assertAlmostEqual(p["rate_yoy"], 200 / 150 * 100, places=1)
        self.assertEqual(p["status_yoy"], "우수")

    def test_below_pace(self):
        # QTD 작아 pace 미달.
        months = {"2025-04": 100, "2025-05": 100, "2025-06": 100,
                  "2026-04": 20, "2026-05": 20}
        p = screening.progress_rate(months)
        self.assertLess(p["rate_yoy"], p["pace"])
        self.assertEqual(p["status_yoy"], "미달")

    def test_january_q1(self):
        p = screening.progress_rate({"2026-01": 10}, "2026-01")
        self.assertEqual((p["quarter"], p["elapsed"]), (1, 1))
        self.assertAlmostEqual(p["pace"], 33.3333, places=1)


class ClassifyGroupTests(unittest.TestCase):
    def test_g1_surprise(self):
        # MoM↑ + 진도율 우수 → G1.
        months = {"2025-04": 50, "2025-05": 50, "2025-06": 50,
                  "2026-04": 100, "2026-05": 200}   # MoM +100%, QTD 300/150=200%
        g, _ = screening.classify_group(months)
        self.assertEqual(g, 1)

    def test_g4_declining(self):
        months = {"2025-05": 100, "2026-04": 60, "2026-05": 40}  # YoY -60%, MoM -33%
        g, _ = screening.classify_group(months)
        self.assertEqual(g, 4)

    def test_empty(self):
        self.assertEqual(screening.classify_group({}), (0, "데이터부족"))


class CapYoyTests(unittest.TestCase):
    def test_cap_helper(self):
        self.assertEqual(screening.cap_yoy(1500.0), (999.9, True))
        self.assertEqual(screening.cap_yoy(50.0), (50.0, False))
        self.assertEqual(screening.cap_yoy(None), (None, False))

    def test_pct_display_cap(self):
        # industry._pct 기저 캡 — 보고서 '+999.9% (기저)'.
        self.assertEqual(industry._pct(1500.0, cap=999.9), "+999.9% (기저)")
        self.assertEqual(industry._pct(50.0, cap=999.9), "+50.0%")
        self.assertEqual(industry._pct(None, cap=999.9), "—")
        self.assertEqual(industry._pct(-30.0), "-30.0%")   # 캡 없는 기존 동작 보존


class LongUptrendTests(unittest.TestCase):
    def test_uptrend_true(self):
        months = {"2025-04": 50, "2025-05": 50, "2025-06": 50,
                  "2026-04": 100, "2026-05": 100}   # YoY+, 진도율 우수
        self.assertTrue(screening.long_uptrend(months))

    def test_uptrend_false_when_declining(self):
        self.assertFalse(screening.long_uptrend({"2025-05": 100, "2026-05": 40}))


if __name__ == "__main__":
    unittest.main()
