"""trade.period_report — 전체 기간(월) 수출입 시장 보고서 순수 가드.

네트워크(customs.db)·LLM(Gemini)은 VM 전용. 여기선 집계(gather_period, 로더
monkeypatch)·HTML/텔레그램 조립·헬퍼만 — 수출↔수입 관계 집계의 핵심(₩0).
"""

import unittest
from contextlib import contextmanager
from datetime import date
from unittest import mock

from trade import period_report as P

# 디램: 수출+수입 둘 다(중간재 신호) · 전년동월 존재. 승용차/합성수지: 수출만.
# 원유: 수입만.
_EXP = {
    "831110": {"name": "디램", "industry": "반도체",
               "months": {"2026-04": 1.8e10, "2026-05": 2.0e10, "2025-05": 1.5e10}},
    "741400": {"name": "승용차", "industry": "자동차",
               "months": {"2026-04": 5e9, "2026-05": 6e9}},
    "133420": {"name": "합성수지", "industry": "석유화학",
               "months": {"2026-04": 3.2e9, "2026-05": 3e9}},
}
_IMP = {
    "831110": {"name": "디램", "industry": "반도체",
               "months": {"2026-04": 3.8e9, "2026-05": 4e9}},
    "271000": {"name": "원유", "industry": "에너지",
               "months": {"2026-04": 6e9, "2026-05": 7e9}},
}


# 잠정 속보(선행) — exp_item/imp_item 의 전체+주요10 (latest_signal 반환 shape).
_PROV = {
    "exp_item": {"ym": "2026-06", "decile": "D2", "window": "1~20일",
                 "total_usd": 3.5e10, "total_yoy": 8.0, "total_mom": 2.0,
                 "items": [{"name": "반도체", "usd": 1.2e10, "yoy": 20.0, "mom": 3.0},
                           {"name": "승용차", "usd": 4e9, "yoy": 5.0, "mom": 1.0}]},
    "imp_item": {"ym": "2026-06", "decile": "D2", "window": "1~20일",
                 "total_usd": 3.2e10, "total_yoy": -2.0, "total_mom": 1.0,
                 "items": [{"name": "원유", "usd": 5e9, "yoy": -10.0, "mom": -1.0}]},
}


@contextmanager
def _fake_session(*a, **k):
    yield object()


def _gather(ym=None, exp=_EXP, imp=_IMP, prov=None):
    prov = prov if prov is not None else {}
    with mock.patch("trade.customs.session", _fake_session), \
            mock.patch("trade.industry.load_mti_stored", lambda c: exp), \
            mock.patch("trade.industry.load_mti_imports", lambda c: imp), \
            mock.patch("trade.customs_provisional.load_signals", lambda c: prov):
        return P.gather_period(ym)


class HelperTests(unittest.TestCase):
    def test_shift_month(self):
        self.assertEqual(P._shift_month("2026-05", -1), "2026-04")
        self.assertEqual(P._shift_month("2026-05", -12), "2025-05")
        self.assertEqual(P._shift_month("2026-01", -1), "2025-12")
        self.assertEqual(P._shift_month("bad", -1), "")

    def test_pct(self):
        self.assertAlmostEqual(P._pct(110, 100), 10.0)
        self.assertAlmostEqual(P._pct(90, 100), -10.0)
        self.assertIsNone(P._pct(100, 0))      # 기준 0 → None
        self.assertIsNone(P._pct(100, None))

    def test_usd(self):
        self.assertEqual(P._usd(2e10), "200.0억$")
        self.assertEqual(P._usd(-1e9), "-10.0억$")
        self.assertEqual(P._usd(0), "—")
        self.assertEqual(P._usd(None), "—")

    def test_release_stage(self):
        # 확정 발표 = 익월 15일. 그 전 = 잠정, 그 이후 = 확정.
        self.assertEqual(P._release_stage("2026-05", date(2026, 6, 14)), "잠정")
        self.assertEqual(P._release_stage("2026-05", date(2026, 6, 15)), "확정")
        self.assertEqual(P._release_stage("2026-05", date(2026, 7, 1)), "확정")
        # 12월 → 익월은 다음해 1월
        self.assertEqual(P._release_stage("2026-12", date(2027, 1, 14)), "잠정")
        self.assertEqual(P._release_stage("2026-12", date(2027, 1, 15)), "확정")
        self.assertEqual(P._release_stage("bad", date(2026, 6, 15)), "")


class GatherTests(unittest.TestCase):
    def test_period_latest_and_totals(self):
        d = _gather()
        self.assertEqual(d["period"], "2026-05")
        self.assertEqual(d["prev"], "2026-04")
        self.assertEqual(d["yoy"], "2025-05")
        t = d["totals"]
        self.assertAlmostEqual(t["export"], 2.0e10 + 6e9 + 3e9)     # 2.9e10
        self.assertAlmostEqual(t["import"], 4e9 + 7e9)              # 1.1e10
        self.assertAlmostEqual(t["balance"], 1.8e10)
        self.assertGreater(t["export_mom"], 0)                     # 26.2e9 → 29e9

    def test_top_and_both_traded(self):
        d = _gather()
        self.assertEqual(d["top_export"][0]["name"], "디램")        # 2.0e10 최대
        self.assertEqual(d["top_import"][0]["name"], "원유")        # 7e9 최대
        both = {r["name"] for r in d["both_traded"]}
        self.assertIn("디램", both)            # 수출+수입 둘 다 → 중간재 신호
        self.assertNotIn("승용차", both)        # 수출만
        self.assertNotIn("원유", both)          # 수입만

    def test_industries_and_swings(self):
        d = _gather()
        self.assertEqual(d["industries"][0]["industry"], "반도체")   # 수출 최대
        # 반도체 net = 수출2.0e10 - 수입4e9
        semi = next(x for x in d["industries"] if x["industry"] == "반도체")
        self.assertAlmostEqual(semi["net"], 2.0e10 - 4e9)
        # 급변동: 승용차(+20%) > 디램(+11.1%) > 합성수지(-6.25%)
        self.assertEqual(d["swings"][0]["name"], "승용차")

    def test_industry_yoy(self):
        # 사용자 2026-06-18 — 산업 롤업에 export_yoy (MoM 옆 YoY)
        semi = next(x for x in _gather()["industries"] if x["industry"] == "반도체")
        self.assertIn("export_yoy", semi)
        self.assertIsNotNone(semi["export_yoy"])     # 반도체 수출 전년동월 데이터 존재

    def test_ym_override(self):
        self.assertEqual(_gather("2026-04")["period"], "2026-04")
        self.assertEqual(_gather("9999-99")["period"], "2026-05")   # 부재월 → 최신

    def test_yoy_computed(self):
        d = _gather()
        dram = next(r for r in d["top_export"] if r["name"] == "디램")
        self.assertAlmostEqual(dram["export_yoy"], (2.0e10 - 1.5e10) / 1.5e10 * 100)

    def test_empty_graceful(self):
        d = _gather(exp={}, imp={})
        self.assertIsNone(d["period"])
        self.assertEqual(d["n_items"], 0)
        self.assertIsNone(d["provisional"])

    def test_status_label(self):
        self.assertIn(_gather()["status"], ("잠정", "확정"))

    def test_provisional_attached(self):
        d = _gather(prov=_PROV)
        p = d["provisional"]
        self.assertIsNotNone(p)
        self.assertEqual(p["ym"], "2026-06")
        self.assertEqual(p["window"], "1~20일")
        self.assertAlmostEqual(p["export"]["total_usd"], 3.5e10)
        self.assertAlmostEqual(p["import"]["total_usd"], 3.2e10)
        # 잠정 없으면 None (확정 품목 보고서는 그대로)
        self.assertIsNone(_gather()["provisional"])

    def test_provisional_only_on_latest(self):
        # 잠정 속보는 **최신 보고서에만** (사용자 2026-06-19 '2025-02 인데 잠정은 최신
        # 꺼' — 과거 보고서엔 혼선). 최신월(2026-05)=붙음, 과거월(2026-04)=None.
        self.assertIsNotNone(_gather(ym="2026-05", prov=_PROV)["provisional"])
        self.assertIsNone(_gather(ym="2026-04", prov=_PROV)["provisional"])


class RenderTests(unittest.TestCase):
    def test_render_free_structure(self):
        h = P.render_free(_gather())
        for must in ("2026-05 수출입 시장 보고서", "총수출", "총수입", "무역수지",
                     "디램", "원유", "🔁 수출입 동시", "산업별", "급변동"):
            self.assertIn(must, h)

    def test_render_free_empty(self):
        h = P.render_free({"period": None})
        self.assertIn("미확보", h)

    def test_render_free_provisional_section(self):
        h = P.render_free(_gather(prov=_PROV))
        for must in ("잠정 속보 (선행)", "확정 품목 집계와 별개", "1~20일", "반도체"):
            self.assertIn(must, h)
        # 상태 배지(잠정/확정) 헤더에 표기
        self.assertTrue("확정" in h or "잠정" in h)
        # 라이트/다크 가독 + MoM (사용자 2026-06-19) — 테마변수·반투명그린·YoY→MoM
        self.assertIn("var(--text,#1d1d1f)", h)        # KPI·잠정 글씨 테마변수
        self.assertIn("rgba(38,166,122,.12)", h)       # 잠정 박스 반투명 그린
        self.assertNotIn("#10231a", h)                 # 옛 고정 다크배경 제거
        self.assertNotIn("background:#1c1f26", h)       # 옛 KPI 다크배경 제거
        self.assertIn("(YoY ", h)
        self.assertIn("(MoM ", h)                                # MoM 도 포함
        self.assertLess(h.index("(YoY "), h.index("(MoM "))      # YoY 앞

    def test_render_free_no_provisional(self):
        # 잠정 없으면 선행 섹션 미표시(확정 보고서 정상)
        self.assertNotIn("잠정 속보 (선행)", P.render_free(_gather()))

    def test_render_telegram(self):
        # 전문(全文) — 대시보드 보고서의 전 섹션 (사용자 2026-06-18 '전문 다 나오게').
        # 길이 cap 은 render 가 아니라 send_long(분할)이 담당 → 여기선 섹션 전부 확인.
        t = P.render_telegram(_gather())
        for sec in ("2026-05 수출입 시장 보고서", "디램", "🏭 <b>산업별 수출입</b>",
                    "🚢 <b>수출 상위 품목</b>", "📥 <b>수입 상위 품목</b>",
                    "🔁 <b>수출입 동시 품목</b>", "급변동"):
            self.assertIn(sec, t)
        t2 = P.render_telegram(_gather(), ai_text="반도체 수출 회복이 무역수지를 견인")
        self.assertIn("AI 분석", t2)
        self.assertIn("반도체 수출 회복", t2)

    def test_render_telegram_provisional(self):
        t = P.render_telegram(_gather(prov=_PROV))
        self.assertIn("잠정 속보(선행", t)
        self.assertIn("1~20일", t)

    def test_render_telegram_empty(self):
        self.assertIn("데이터 미확보", P.render_telegram({"period": None}))


if __name__ == "__main__":
    unittest.main()
