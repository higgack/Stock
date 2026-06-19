"""trade.reference_book — 품목↔HS↔산업↔관련기업 레퍼런스북 (사용자 2026-06-18).

파일·store.db·연계표 없이 build_rows(조립)·render_page(HTML) 가드 — mti_map/
mti_companies monkeypatch."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trade import reference_book as R


class BuildRowsTests(unittest.TestCase):
    def test_build_rows_joins_hs_and_companies(self):
        from trade import mti_companies, mti_map
        with mock.patch.object(mti_map, "mti_names", return_value={
                    "831110": ("디램", "반도체"), "021130": ("말", "농수산식품")}), \
                mock.patch.object(mti_map, "load_mti", return_value={
                    "8542321000": ("831110", "반도체", "디램"),
                    "8542329000": ("831110", "반도체", "디램"),
                    "0101211000": ("021130", "농수산식품", "말")}), \
                mock.patch.object(mti_companies, "load_channel_pairs", return_value=[]), \
                mock.patch.object(mti_companies, "theme_rows", return_value=[]), \
                mock.patch.object(mti_companies, "companies_for",
                                  side_effect=lambda n: ["삼성전자", "SK하이닉스"]
                                  if "디램" in n else []), \
                mock.patch.object(mti_companies, "channel_companies_for", return_value=[]):
            rows = R.build_rows()
        by = {r["mti6"]: r for r in rows}
        self.assertEqual(sorted(by["831110"]["hs"]), ["8542321000", "8542329000"])
        self.assertEqual(by["831110"]["companies"], ["삼성전자", "SK하이닉스"])
        self.assertEqual(by["831110"]["industry"], "반도체")
        self.assertEqual(by["021130"]["companies"], [])     # 미매핑 품목 빈 리스트

    def test_build_rows_graceful_no_table(self):
        from trade import mti_map
        with mock.patch.object(mti_map, "mti_names", side_effect=Exception("no file")):
            self.assertEqual(R.build_rows(), [])


class RenderTests(unittest.TestCase):
    _ROWS = [
        {"mti6": "831110", "name": "디램", "industry": "반도체",
         "hs": ["8542321000", "8542329000"], "companies": ["삼성전자", "SK하이닉스"]},
        {"mti6": "021130", "name": "말", "industry": "농수산식품",
         "hs": ["0101211000"], "companies": []},
    ]

    def test_render_structure(self):
        h = R.render_page(self._ROWS)
        for must in ("품목 레퍼런스북", "디램", "831110", "8542321000", "삼성전자",
                     "반도체", "농수산식품", "관련 상장사", "id='q'",
                     "id='csv'", "📥 CSV"):            # CSV 다운로드 버튼(사용자 2026-06-18)
            self.assertIn(must, h)
        self.assertIn('data-s=', h)               # 검색 인덱스 속성
        self.assertIn('data-i="반도체"', h)        # 산업 필터 칩 키
        self.assertIn("총 2품목", h)
        # 라이트모드 칩 글씨 진하게(흰색→#1f2328) — 관련상장사 가독성(사용자 2026-06-18)
        self.assertIn("color:#1f2328", h)

    def test_render_time_based_theme(self):
        # 테마 = 대시보드와 동일 시간기반(body.dark, KST 19-07), OS prefers 폐기
        # (사용자 2026-06-18 'light/black 시간에 맞게 안 변해').
        h = R.render_page(self._ROWS)
        self.assertIn("body.dark{", h)                    # 다크 오버라이드
        self.assertIn("applyDark", h)                     # 시간기반 토글 JS
        self.assertIn("classList.toggle('dark'", h)
        self.assertNotIn("@media(prefers-color-scheme", h)   # OS기반 미디어쿼리 제거

    def test_render_embeds_scroll_restore(self):
        # 뒤로가기 시 보던 위치 복원 (사용자 2026-06-18 '모든 대시보드')
        h = R.render_page(self._ROWS)
        self.assertIn("scrollRestoration", h)
        self.assertIn("sessionStorage", h)

    def test_csv_search_scope_to_main_table(self):
        # CSV·검색 JS가 미매칭 패널의 tbody tr 까지 잡으면 카운트 부풀고 CSV 크래시
        # (사용자 2026-06-19 'CSV 또 안 됨' — 패널 80행 오염). 메인 표 한정 가드.
        h = R.render_page(self._ROWS, unmatched=[("미매칭X", ["듣보종목"], 3)])
        self.assertIn("<table id='tbl'>", h)
        self.assertIn("#tbl tbody tr", h)
        self.assertNotIn("querySelectorAll('tbody tr')", h)   # 비한정 선택자 금지

    def test_render_search_index_lowercase(self):
        h = R.render_page([{"mti6": "831110", "name": "DRAM디램", "industry": "반도체",
                            "hs": ["8542321000"], "companies": ["삼성전자"]}])
        self.assertIn('data-s="dram디램 831110 반도체 8542321000 삼성전자"', h)

    def test_regenerate_writes_file(self):
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(R, "build_rows", return_value=self._ROWS):
            out = R.regenerate(out_path=d / "reference.html")
        self.assertTrue(out.exists())
        self.assertIn("디램", out.read_text(encoding="utf-8"))


class UnmatchedCandidatesTests(unittest.TestCase):
    """미매칭 후보 자동 발굴 — 레퍼런스북 어디에도 없는 알림 회사 노출
    (사용자 2026-06-19 '새 언어격차 생겼는지 어떻게 알아?')."""

    _ROWS = [{"mti6": "831110", "name": "디램", "industry": "반도체",
              "hs": [], "companies": ["삼성전자", "SK하이닉스"]}]   # surfaced = {삼성전자,SK하이닉스}

    def _db(self, td, alerts):
        import json
        import sqlite3
        db = Path(td) / "store.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE alerts (item TEXT, stocks TEXT, stocks_meta TEXT)")
        for item, stocks in alerts:
            conn.execute("INSERT INTO alerts VALUES (?,?,?)",
                         (item, json.dumps(stocks), "{}"))
        conn.commit(); conn.close()
        return db

    def test_only_truly_missing_companies(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._db(td, [
                ("디램 수출", ["삼성전자"]),          # 이미 surfaced → 제외
                ("신소재 XYZ", ["듣보종목"]),         # 어디에도 없음 → 후보
                ("신소재 XYZ", ["듣보종목"]),         # 빈도 2
                ("(도치행)", ["소주"]),               # 품목어 누수 → 제외
            ])
            cands = R.unmatched_candidates(self._ROWS, db_path=db)
        self.assertEqual(len(cands), 1)
        item, cos, n = cands[0]
        self.assertEqual(item, "신소재 XYZ")
        self.assertEqual(cos, ["듣보종목"])
        self.assertEqual(n, 2)                      # 빈도 집계
        # 누수·기노출 회사는 어떤 후보에도 없음
        allco = {c for _, c, _ in cands for c in cos}
        self.assertNotIn("소주", allco)
        self.assertNotIn("삼성전자", allco)

    def test_graceful_no_db(self):
        self.assertEqual(R.unmatched_candidates(self._ROWS,
                                                db_path=Path("/no/such.db")), [])

    def test_theme_merged_into_mti_rows(self):
        """테마 회사가 HS→MTI 로 **기존 MTI 품목 행에 병합** (사용자 2026-06-19
        '별도 집계 말고 기존에 붙여'). 별도 테마 행 없음 + 테마명 검색 인덱스."""
        rows = R.build_rows()                                # 실 mti_map(HS6→MTI6 해석)
        self.assertTrue(rows)
        self.assertFalse(any(r.get("theme") for r in rows))  # 별도 테마 행 없음
        # 피부과 테마(클래시스) → HS 9018.90 → '미용및조직수복용' MTI 행에 병합
        derm = next((r for r in rows if r["name"] == "미용및조직수복용"), None)
        self.assertIsNotNone(derm)
        self.assertIn("클래시스", derm["companies"])
        # 테마명이 그 행 검색 인덱스에(피부과·SiC 등으로 검색 가능)
        self.assertIn("피부과", " ".join(derm.get("theme_kw", [])))
        html = R.render_page(rows)
        self.assertIn("클래시스", html)
        self.assertNotIn("🏷️ 테마", html)                   # 별도 테마 배지 없음

    def test_panel_renders_in_page(self):
        um = [("신소재 XYZ", ["듣보종목"], 2)]
        h = R.render_page(self._ROWS, unmatched=um)
        self.assertIn("미매칭 알림 후보", h)
        self.assertIn("신소재 XYZ", h)
        self.assertIn("듣보종목", h)
        # 비면 패널 자체가 안 나옴
        self.assertNotIn("미매칭 알림 후보", R.render_page(self._ROWS))

    def test_reinforce_roundtrip_and_panel(self):
        # DART 보강 후보 — 캐시 JSON 저장/로드(후보수순) + 패널 렌더(2026-06-19).
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rf.json"
            R.save_reinforce({"디램": ["A"], "낸드": ["B", "C", "D"]}, p)
            loaded = R.load_reinforce(p)
            self.assertEqual(loaded[0][0], "낸드")           # 후보 많은 것 먼저
            h = R.render_page(self._ROWS, reinforce=loaded)
            self.assertIn("DART 보강 후보", h)
            self.assertIn("낸드", h)
            # 비면 패널 없음
            self.assertNotIn("DART 보강 후보", R.render_page(self._ROWS))
            self.assertEqual(R.load_reinforce(Path("/no/such.json")), [])


if __name__ == "__main__":
    unittest.main()
