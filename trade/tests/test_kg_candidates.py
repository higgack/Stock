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

    def test_new_against_csv(self):
        # CSV 에 이미 있는 (회사,관계,대상)은 신규에서 제외.
        d = tempfile.mkdtemp()
        p = os.path.join(d, "kg.csv")
        kg.write_candidates_csv(
            [{"company": "삼성전기", "relation": "취급품목", "target": "MLCC",
              "evidence": "", "source": "t"}], p)
        cands = [
            {"company": "삼성전기", "relation": "취급품목", "target": "MLCC"},  # 기존
            {"company": "삼성전기", "relation": "취급품목", "target": "적층세라믹"},  # 신규
            {"company": "삼성전기", "relation": "취급품목", "target": "적층세라믹"},  # dedup
        ]
        out = kg._new_against_csv(cands, p)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["target"], "적층세라믹")

    def test_run_blog_extraction_graceful(self):
        # Gemini 미설정 → 추출 0 → 신규 0, 크래시·CSV 생성 없음.
        old = {k: os.environ.pop(k, None)
               for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY",
                         "GOOGLE_GENAI_USE_VERTEXAI")}
        try:
            self.assertEqual(
                kg.run_blog_extraction(
                    [{"text": "삼성전기 MLCC 생산", "source": "blog:t"}],
                    notify=False),
                [])
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_run_blog_extraction_killswitch(self):
        os.environ["KG_CANDIDATES_ENABLED"] = "0"
        try:
            self.assertFalse(kg._kg_enabled())
            self.assertEqual(
                kg.run_blog_extraction([{"text": "x", "source": "s"}],
                                       notify=False), [])
        finally:
            os.environ.pop("KG_CANDIDATES_ENABLED", None)

    def test_ingest_approved(self):
        d = tempfile.mkdtemp()
        qp = os.path.join(d, "kg.csv")
        rp = os.path.join(d, "reinforce.csv")
        # 승인(취급품목) 1 + 후보(미승인) 1 + 승인(테마=자동등재 제외) 1
        kg.write_candidates_csv([
            {"company": "삼성전기", "relation": "취급품목", "target": "MLCC",
             "evidence": "e", "source": "blog:t"},
            {"company": "LG전자", "relation": "취급품목", "target": "TV",
             "evidence": "e", "source": "blog:t"},
            {"company": "에코프로", "relation": "테마", "target": "2차전지",
             "evidence": "e", "source": "blog:t"},
        ], qp)
        # 첫번째·세번째 행을 '승인'으로 마킹(상태 컬럼 = idx 6)
        rows = []
        with open(qp, encoding="utf-8-sig", newline="") as f:
            import csv as _c
            r = _c.reader(f)
            head = next(r)
            for row in r:
                if row[0] in ("삼성전기", "에코프로"):
                    row[6] = "승인"
                rows.append(row)
        with open(qp, "w", encoding="utf-8-sig", newline="") as f:
            import csv as _c
            w = _c.writer(f)
            w.writerow(head)
            w.writerows(rows)
        n = kg.ingest_approved(queue_path=qp, reinforce_path=rp)
        self.assertEqual(n, 1)                       # 취급품목 승인분 1건만
        with open(rp, encoding="utf-8-sig") as f:
            body = f.read()
        self.assertIn("MLCC", body)
        self.assertIn("삼성전기", body)
        self.assertNotIn("2차전지", body)             # 테마는 자동등재 안 함
        # 큐 상태: 삼성전기 → '등재', 에코프로(테마) → '승인' 유지
        with open(qp, encoding="utf-8-sig") as f:
            q = f.read()
        self.assertIn("삼성전기,취급품목,MLCC", q)
        self.assertIn("등재", q)
        # 재실행 idempotent — 이미 등재라 신규 0
        self.assertEqual(kg.ingest_approved(queue_path=qp, reinforce_path=rp), 0)


if __name__ == "__main__":
    unittest.main()
