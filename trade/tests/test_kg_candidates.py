"""kg_candidates — 레퍼런스북 관계 후보 발굴(kg-gen 패턴) 순수 로직 회귀.
LLM 호출은 키 없으면 graceful([]) — 순수 헬퍼(parse/filter/csv)만 검증."""
import os
import tempfile
import unittest

from trade import kg_candidates as kg


class KgCandidatesTests(unittest.TestCase):
    def test_parse_triples_filters_bad(self):
        raw = ('```json\n'
               '[{"company":"삼성전기","relation":"취급품목","target":"MLCC","evidence":"생산"},'
               '{"company":"X","relation":"엉뚱","target":"Y"},'      # 관계 어휘 밖
               '{"company":"A","relation":"납품","target":"A"},'       # 자기참조
               '{"company":"","relation":"테마","target":"AI"}]\n```')  # 회사 결손
        t = kg.parse_triples(raw)
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["company"], t[0]["relation"], t[0]["target"]),
                         ("삼성전기", "취급품목", "MLCC"))

    def test_parse_triples_garbage(self):
        self.assertEqual(kg.parse_triples("not json"), [])
        self.assertEqual(kg.parse_triples(""), [])

    def test_filter_known_existing_dedup(self):
        trips = [
            {"company": "삼성전기", "relation": "취급품목", "target": "MLCC"},
            {"company": "무명사", "relation": "테마", "target": "AI"},     # 미상장 → 제외
            {"company": "삼성전기", "relation": "취급품목", "target": "MLCC"},  # dedup
            {"company": "LG전자", "relation": "취급품목", "target": "TV"},   # 기존 → 제외
        ]
        out = kg.filter_candidates(
            trips, known_companies={"삼성전기", "LG전자"},
            existing_pairs={(kg._norm("LG전자"), kg._norm("TV"))}, source="s")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["company"], "삼성전기")
        self.assertEqual(out[0]["source"], "s")

    def test_filter_no_known_keeps_all(self):
        trips = [{"company": "무명사", "relation": "테마", "target": "AI"}]
        out = kg.filter_candidates(trips, known_companies=None)
        self.assertEqual(len(out), 1)        # 회사필터 생략 시 통과

    def test_write_csv_dedup_append(self):
        cands = [{"company": "삼성전기", "relation": "취급품목",
                  "target": "MLCC", "evidence": "e", "source": "t"}]
        d = tempfile.mkdtemp()
        p = os.path.join(d, "kg.csv")
        self.assertEqual(kg.write_candidates_csv(cands, p), 1)   # 신규 1
        self.assertEqual(kg.write_candidates_csv(cands, p), 0)   # 중복 skip
        with open(p, encoding="utf-8-sig") as f:
            body = f.read()
        self.assertIn("회사,관계,대상,근거,출처,추출일,상태", body)  # 헤더
        self.assertIn("삼성전기,취급품목,MLCC", body)
        self.assertEqual(body.count("삼성전기"), 1)              # 중복 미적재

    def test_extract_graceful_without_gemini(self):
        # 키/백엔드 없으면(샌드박스) graceful [] — 자동등재·크래시 0.
        old = {k: os.environ.pop(k, None)
               for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY",
                         "GOOGLE_GENAI_USE_VERTEXAI")}
        try:
            self.assertEqual(
                kg.extract_candidates([{"text": "삼성전기 MLCC 생산", "source": "x"}]),
                [])
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
