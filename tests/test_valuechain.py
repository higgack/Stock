"""밸류체인 그래프 조회(bot.valuechain) 순수 로직 회귀 — neighborhood/포맷/스크리너.
load_edges(소스 I/O)는 graceful 의존이라 제외, 명시 edges 로 순수 검증."""
import unittest

from bot import valuechain as vc

_EDGES = [
    {"company": "브이엠", "relation": "납품", "target": "SK하이닉스",
     "kind": "kg", "source": "dart:브이엠", "status": "승인", "evidence": ""},
    {"company": "펨트론", "relation": "고객", "target": "SK하이닉스",
     "kind": "kg", "source": "dart:펨트론", "status": "승인", "evidence": ""},
    {"company": "SK하이닉스", "relation": "고객", "target": "마이크로소프트",
     "kind": "kg", "source": "dart:sk", "status": "후보", "evidence": ""},
    {"company": "SK하이닉스", "relation": "취급품목", "target": "HBM4",
     "kind": "kg", "source": "dart:sk", "status": "등재", "evidence": ""},
    {"company": "SK하이닉스", "relation": "수출품목", "target": "메모리반도체",
     "kind": "trade", "source": "관세청", "status": "", "evidence": "HS 8542"},
    {"company": "삼성전자", "relation": "수출품목", "target": "메모리반도체",
     "kind": "trade", "source": "관세청", "status": "", "evidence": "HS 8542"},
]


class ValueChainTests(unittest.TestCase):
    def test_neighborhood_directions(self):
        nb = vc.neighborhood("SK하이닉스", _EDGES)
        self.assertEqual(set(nb["suppliers"]), {"브이엠", "펨트론"})   # →SK 납품
        self.assertEqual(nb["customers"], ["마이크로소프트"])          # SK→ 공급
        self.assertEqual(nb["products"], ["HBM4"])
        self.assertEqual(nb["exports"], ["메모리반도체"])
        self.assertEqual(nb["peers"][0][0], "삼성전자")               # 같은 수출품목

    def test_neighborhood_norm_and_empty(self):
        # (주)/공백 정규화 + 미매칭 빈 구조.
        self.assertEqual(set(vc.neighborhood(" sk 하이닉스 ", _EDGES)["suppliers"]),
                         {"브이엠", "펨트론"})
        empty = vc.neighborhood("없는회사", _EDGES)
        self.assertFalse(vc.has_data(empty))
        self.assertEqual(vc.neighborhood("", _EDGES)["suppliers"], [])

    def test_format_for_prompt(self):
        block = vc.format_for_prompt("SK하이닉스", _EDGES)
        self.assertIn("공급사", block)
        self.assertIn("브이엠", block)
        self.assertIn("동종 회사", block)
        self.assertEqual(vc.format_for_prompt("없는회사", _EDGES), "")   # 데이터 없으면 ''

    def test_format_for_telegram(self):
        t = vc.format_for_telegram("SK하이닉스", _EDGES)
        self.assertIn("공급사", t)
        self.assertIn("수혜 후보", t)              # 공급사 있으면 수혜 팁
        self.assertIn("데이터 없음", vc.format_for_telegram("없는회사", _EDGES))

    def test_resolve_company_lenient(self):
        # 부분 일치 관대 해석 — 표기 조금 달라도 매칭(사용자 2026-06-24).
        self.assertEqual(vc.resolve_company("하이닉스", _EDGES), "SK하이닉스")
        self.assertEqual(vc.resolve_company("sk 하이닉스", _EDGES), "SK하이닉스")
        self.assertEqual(vc.resolve_company("SK하이닉스", _EDGES), "SK하이닉스")
        self.assertIsNone(vc.resolve_company("없는회사", _EDGES))
        # 포맷터가 관대 해석 사용 → 부분어로도 결과
        self.assertIn("공급사", vc.format_for_telegram("하이닉스", _EDGES))

    def test_search_by_item_and_industry(self):
        # 회사 외 품목·업종 검색도 동작(수출입 대시보드처럼, 사용자 2026-06-24).
        e = [
            {"company": "삼성전자", "relation": "수출품목", "target": "메모리반도체",
             "kind": "trade", "evidence": "HS 8542", "industry": "반도체",
             "source": "관세청", "status": ""},
            {"company": "SK하이닉스", "relation": "수출품목", "target": "메모리반도체",
             "kind": "trade", "evidence": "HS 8542", "industry": "반도체",
             "source": "관세청", "status": ""},
            {"company": "DB하이텍", "relation": "수출품목", "target": "파운드리",
             "kind": "trade", "evidence": "HS 8542", "industry": "반도체",
             "source": "관세청", "status": ""},
        ]
        self.assertEqual(vc.resolve_kind("메모리반도체", e), ("item", "메모리반도체"))
        # 정확 업종('반도체')이 부분 품목('메모리반도체')보다 우선
        self.assertEqual(vc.resolve_kind("반도체", e), ("industry", "반도체"))
        self.assertEqual(vc.resolve_kind("삼성전자", e), ("company", "삼성전자"))
        nb = vc.item_neighborhood("메모리반도체", e)
        self.assertEqual(set(nb["customs"]), {"삼성전자", "SK하이닉스"})
        self.assertEqual(nb["hs"], "HS 8542")
        self.assertEqual(set(vc.industry_companies("반도체", e)),
                         {"삼성전자", "SK하이닉스", "DB하이텍"})
        # telegram 포맷 — 품목/업종 분기
        self.assertIn("(품목)", vc.format_for_telegram("메모리반도체", e))
        self.assertIn("(업종)", vc.format_for_telegram("반도체", e))

    def test_top_suppliers_and_connected(self):
        # 다고객 납품사: 회사별 (서로 다른) 고객수.
        sup = dict(vc.top_suppliers(_EDGES))
        self.assertEqual(sup.get("브이엠"), 1)
        self.assertEqual(sup.get("SK하이닉스"), 1)   # SK도 고객(MS) 1
        self.assertNotIn("삼성전자", sup)            # 수출품목만, 납품/고객 없음
        conn = dict(vc.top_connected(_EDGES))
        self.assertGreaterEqual(conn.get("SK하이닉스", 0), 3)   # 다방향 연결


class SuppressTests(unittest.TestCase):
    """🗑️ 잘못된 관계 숨김(사용자 2026-06-29) — add/load/멱등/거부 + load_edges 제외."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._orig = vc._SUPPRESS_PATH
        vc._SUPPRESS_PATH = Path(tempfile.mkdtemp()) / "sup.json"

    def tearDown(self):
        vc._SUPPRESS_PATH = self._orig

    def test_add_load_idempotent(self):
        eid = vc._edge_id("GST", "수출품목", "반도체제조용장비")
        self.assertEqual(vc.load_suppressed(), set())
        self.assertTrue(vc.add_suppressed(eid))
        self.assertTrue(vc.add_suppressed(eid))          # 멱등
        self.assertIn(eid, vc.load_suppressed())

    def test_rejects_malformed_id(self):
        self.assertFalse(vc.add_suppressed("bad"))       # 파이프 < 2
        self.assertFalse(vc.add_suppressed(""))
        self.assertFalse(vc.add_suppressed("a|b"))       # 2파트(파이프 1)뿐

    def test_suppress_filter_excludes_edge(self):
        # load_edges 가 끝에 적용하는 필터와 동일 식으로 제외/보존 검증.
        vc.add_suppressed(vc._edge_id("삼성전자", "수출품목", "메모리반도체"))
        sup = vc.load_suppressed()
        kept = [e for e in _EDGES
                if vc._edge_id(e["company"], e["relation"], e["target"]) not in sup]
        names = {(e["company"], e["target"]) for e in kept}
        self.assertNotIn(("삼성전자", "메모리반도체"), names)   # 숨김 제외
        self.assertIn(("SK하이닉스", "메모리반도체"), names)    # 나머지 보존


if __name__ == "__main__":
    unittest.main()
