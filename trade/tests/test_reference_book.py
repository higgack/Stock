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


if __name__ == "__main__":
    unittest.main()
