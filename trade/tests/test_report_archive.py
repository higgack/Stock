"""trade.report_archive — 유료 AI 보고서 동결 + 색인 (사용자 2026-06-18).

네트워크·LLM 없음 — record(동결 페이지 write + jsonl append) / regenerate(색인) /
ensure_exists 만. industry_archive 테스트 패턴 미러(경로 monkeypatch)."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from trade import report_archive as RA

_KST = timezone(timedelta(hours=9))


class ReportArchiveTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self._patches = [
            mock.patch.object(RA, "ARCHIVE_JSONL", self.d / "report_archive.jsonl"),
            mock.patch.object(RA, "ARCHIVE_HTML", self.d / "dash" / "report_archive.html"),
            mock.patch.object(RA, "SNAP_DIR", self.d / "dash" / "report_archive_pages"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _rec(self, **kw):
        base = dict(kind="🏢 기업", title="삼성전자 · 005930",
                    html_body="<div>보고서 본문 DRAM</div>",
                    summary="🏢 삼성전자\n제품: DRAM", cost_krw=12.3,
                    now=datetime(2026, 6, 18, 14, 30, tzinfo=_KST))
        base.update(kw)
        return RA.record(**base)

    def test_record_freezes_page_and_indexes(self):
        rel = self._rec()
        frozen = RA.SNAP_DIR / Path(rel).name
        self.assertTrue(frozen.exists())
        page = frozen.read_text(encoding="utf-8")
        self.assertIn("DRAM", page)                      # 본문 동결
        self.assertIn("../report_archive.html", page)    # back-link
        runs = RA.load_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["cost_krw"], 12.3)
        self.assertEqual(runs[0]["title"], "삼성전자 · 005930")
        self.assertEqual(runs[0]["file"], rel)

    def test_record_appends_multiple(self):
        self._rec()
        self._rec(title="LG에너지솔루션", now=datetime(2026, 6, 18, 15, 0, tzinfo=_KST))
        self.assertEqual(len(RA.load_runs()), 2)
        files = list(RA.SNAP_DIR.glob("*.html"))
        self.assertEqual(len(files), 2)                  # 두 동결 페이지

    def test_regenerate_index(self):
        rel = self._rec()
        out = RA.regenerate()
        idx = out.read_text(encoding="utf-8")
        self.assertIn("AI 보고서 아카이브", idx)         # 색인 타이틀
        self.assertIn("삼성전자", idx)
        self.assertIn(rel, idx)                          # 동결 페이지 링크
        self.assertIn("₩12", idx)                        # 누적 비용 stat

    def test_regenerate_empty_safe(self):
        out = RA.regenerate()
        self.assertTrue(out.exists())
        self.assertIn("자동 적립", out.read_text(encoding="utf-8"))  # empty 안내

    def test_ensure_exists(self):
        self.assertFalse(RA.ARCHIVE_HTML.exists())
        RA.ensure_exists()
        self.assertTrue(RA.ARCHIVE_HTML.exists())

    def test_period_kind(self):
        rel = self._rec(kind="🗂️ 전체", title="2026-05 수출입 시장 보고서",
                        summary="🗂️ 2026-05 보고서\n총수출 100억$")
        idx = RA.regenerate().read_text(encoding="utf-8")
        self.assertIn("2026-05 수출입 시장 보고서", idx)
        self.assertIn("🗂️ 전체", idx)                   # kind 배지


if __name__ == "__main__":
    unittest.main()
