"""trade.industry_archive — 동결 페이지(산업트렌드 전체) + 색인 링크 테스트."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trade import industry, industry_archive


def _months(base, latest):
    d = {}
    for i in range(13):
        y = 2025 + (i // 12); mo = (i % 12) + 1
        d[f"{y}-{mo:02d}"] = base
    d["2026-04"] = latest
    return d


class FrozenSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._p = [
            mock.patch.object(industry_archive, "ARCHIVE_JSONL", d / "arch.jsonl"),
            mock.patch.object(industry_archive, "ARCHIVE_HTML", d / "dash" / "industry_archive.html"),
            mock.patch.object(industry_archive, "SNAP_DIR", d / "dash" / "archive"),
        ]
        for p in self._p:
            p.start()
        self.conn = sqlite3.connect(":memory:")
        industry.store(self.conn, {"반도체": _months(11_000_000_000, 31_000_000_000),
                                   "자동차": _months(6_000_000_000, 5_700_000_000)})

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.conn.close()
        self.tmp.cleanup()

    def test_freezes_full_industry_page(self):
        k = industry_archive.record_snapshot(
            self.conn, [{"title": "AI 인프라", "body": "수요 확대"}])
        self.assertEqual(k, "2026-04")
        frozen = industry_archive.SNAP_DIR / "2026-04.html"
        self.assertTrue(frozen.exists())
        h = frozen.read_text(encoding="utf-8")
        # 산업트렌드 '전체' — 카드/차트/요약보드 + 🔍박스, dashboard CSS 임베드
        self.assertEqual(h.lower().count("<!doctype"), 1)   # 단일 self-contained
        self.assertIn("ind-card", h)                        # 산업 카드
        self.assertIn("<svg", h)                            # SVG 차트
        self.assertIn(".ind-", h)                           # dashboard CSS 임베드
        self.assertIn("ins-box", h)                         # 🔍 박스
        self.assertIn("ind-tg-btn", h)                      # 월별/TTM 토글
        self.assertIn("확정 스냅샷", h)                      # 동결 헤더
        self.assertIn("반도체", h)

    def test_index_record_links_to_frozen_page(self):
        industry_archive.record_snapshot(self.conn, [{"title": "AI", "body": "x"}])
        runs = industry_archive.load_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["file"], "archive/2026-04.html")
        self.assertIn("archive/2026-04.html", runs[0]["body"])   # 링크
        self.assertIn("AI", runs[0]["body"])                     # 캡션(🔍 신호명)

    def test_idempotent_by_confirmed_month(self):
        industry_archive.record_snapshot(self.conn, [])
        industry_archive.record_snapshot(self.conn, [])   # 같은 확정월 재기록
        self.assertEqual(len(industry_archive.load_runs()), 1)
        # 동결 파일도 1개(덮어씀)
        files = list((industry_archive.SNAP_DIR).glob("*.html"))
        self.assertEqual(len(files), 1)

    def test_regenerate_index_has_link_and_title(self):
        industry_archive.record_snapshot(self.conn, [])
        out = industry_archive.regenerate()
        h = out.read_text(encoding="utf-8")
        self.assertIn("산업트렌드 월별 아카이브", h)       # 색인 타이틀
        self.assertIn("archive/2026-04.html", h)          # 동결 페이지 링크
        self.assertIn("scr-search", h)                    # 검색바(템플릿 UX)
        self.assertEqual(h.lower().count("<!doctype"), 1)

    def test_regenerate_empty_safe(self):
        out = industry_archive.regenerate()
        self.assertIn("아직 동결된 스냅샷이 없습니다",
                      out.read_text(encoding="utf-8"))

    def test_ensure_exists(self):
        self.assertFalse(industry_archive.ARCHIVE_HTML.exists())
        industry_archive.ensure_exists()
        self.assertTrue(industry_archive.ARCHIVE_HTML.exists())


if __name__ == "__main__":
    unittest.main()
