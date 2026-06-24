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

    def test_top_suppliers_and_connected(self):
        # 다고객 납품사: 회사별 (서로 다른) 고객수.
        sup = dict(vc.top_suppliers(_EDGES))
        self.assertEqual(sup.get("브이엠"), 1)
        self.assertEqual(sup.get("SK하이닉스"), 1)   # SK도 고객(MS) 1
        self.assertNotIn("삼성전자", sup)            # 수출품목만, 납품/고객 없음
        conn = dict(vc.top_connected(_EDGES))
        self.assertGreaterEqual(conn.get("SK하이닉스", 0), 3)   # 다방향 연결


if __name__ == "__main__":
    unittest.main()
