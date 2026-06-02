"""trade.industry_archive — 스냅샷 본문·기록(idempotent)·재생성 테스트."""

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


class BuildBodyTests(unittest.TestCase):
    def test_body_has_classification_and_signals(self):
        by_ind = {"반도체": _months(11_000_000_000, 31_000_000_000)}
        latest, body = industry_archive.build_snapshot_body(
            by_ind, {}, [{"title": "AI 인프라", "body": "수요 확대"}])
        self.assertEqual(latest, "2026-04")
        self.assertIn("반도체", body)
        self.assertIn("초고성장", body)            # 분류 블록
        self.assertIn("🔍", body)                  # 신호 블록
        self.assertIn("AI 인프라", body)


class RecordRegenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._p = [
            mock.patch.object(industry_archive, "ARCHIVE_JSONL", d / "arch.jsonl"),
            mock.patch.object(industry_archive, "ARCHIVE_HTML", d / "dash" / "arch.html"),
        ]
        for p in self._p:
            p.start()
        self.conn = sqlite3.connect(":memory:")
        industry.store(self.conn, {"반도체": _months(11_000_000_000, 31_000_000_000)})

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.conn.close()
        self.tmp.cleanup()

    def test_record_then_idempotent_by_month(self):
        k1 = industry_archive.record_snapshot(self.conn, [{"title": "t", "body": "b"}])
        self.assertEqual(k1, "2026-04")
        self.assertEqual(len(industry_archive.load_runs()), 1)
        # 같은 확정월 재기록 → 교체(중복 X)
        industry_archive.record_snapshot(self.conn, [{"title": "t2", "body": "b2"}])
        runs = industry_archive.load_runs()
        self.assertEqual(len(runs), 1)
        self.assertIn("t2", runs[0]["body"])       # 최신 카드로 교체됨

    def test_regenerate_writes_archive_html(self):
        industry_archive.record_snapshot(self.conn, [])
        out = industry_archive.regenerate()
        html = out.read_text(encoding="utf-8")
        self.assertIn("산업트렌드 월별 아카이브", html)   # 우리 타이틀
        self.assertIn("반도체", html)
        self.assertIn("scr-search", html)              # 검색바(템플릿 UX)
        self.assertIn("data-theme", html)              # 테마 토글 스크립트
        self.assertEqual(html.lower().count("<!doctype"), 1)  # 단일 문서

    def test_regenerate_empty_safe(self):
        out = industry_archive.regenerate()             # 기록 0건
        html = out.read_text(encoding="utf-8")
        self.assertIn("아직 기록이 없습니다", html)

    def test_ensure_exists_creates_then_noops(self):
        self.assertFalse(industry_archive.ARCHIVE_HTML.exists())
        industry_archive.ensure_exists()
        self.assertTrue(industry_archive.ARCHIVE_HTML.exists())


if __name__ == "__main__":
    unittest.main()
