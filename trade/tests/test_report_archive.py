"""trade.report_archive — 유료 AI 보고서 동결 + 색인 (사용자 2026-06-18).

네트워크·LLM 없음 — record(동결 페이지 write + jsonl append) / regenerate(색인) /
ensure_exists 만. industry_archive 테스트 패턴 미러(경로 monkeypatch)."""

import os
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

    def test_unique_filename_same_second(self):
        # 같은 초 2건이라도 파일명 유니크 (사용자 16:03 2건 충돌 방지)
        f1 = self._rec(title="이오테크닉스")
        f2 = self._rec(title="이오테크닉스")            # 같은 now → 충돌 회피
        self.assertNotEqual(f1, f2)
        self.assertTrue((RA.SNAP_DIR / Path(f1).name).exists())
        self.assertTrue((RA.SNAP_DIR / Path(f2).name).exists())

    def test_delete_card_and_page(self):
        f1 = self._rec(title="A")
        f2 = self._rec(title="B")
        RA.regenerate()
        self.assertTrue(RA.delete(f1))
        self.assertEqual([r["file"] for r in RA.load_runs()], [f2])   # f1 record 제거
        self.assertFalse((RA.SNAP_DIR / Path(f1).name).exists())      # 동결 페이지 제거
        self.assertTrue((RA.SNAP_DIR / Path(f2).name).exists())       # f2 보존

    def test_delete_path_traversal_guard(self):
        for bad in ("../../../etc/passwd", "report_archive_pages/../x.html",
                    "nonexistent_pages/x.html", "", "report_archive_pages/x.txt"):
            self.assertFalse(RA.delete(bad))

    def test_index_has_delete_affordance(self):
        self._rec(title="A")
        idx = RA.regenerate().read_text(encoding="utf-8")
        self.assertIn("card-del", idx)                    # 🗑️ 버튼
        self.assertIn("api/report_archive_delete", idx)   # 삭제 API JS

    def test_index_has_send_affordance(self):
        self._rec(title="A", tg="🏢 본문")
        idx = RA.regenerate().read_text(encoding="utf-8")
        self.assertIn("card-send", idx)                   # 📤 버튼
        self.assertIn("api/report_archive_send", idx)     # 전송 API JS

    def test_send_to_channel_uses_stored_tg(self):
        # 저장된 tg 를 재과금 없이 send_long 으로 전송 (사용자 2026-06-18)
        from unittest import mock
        f = self._rec(title="이오테크닉스", tg="🏢 <b>이오테크닉스</b>\n전체 본문")
        with mock.patch.dict(os.environ, {"TRADE_CHANNEL_CHAT_IDS": "123"}), \
                mock.patch("trade.scripts.customs_alert.send_long", return_value=1) as sl:
            res = RA.send_to_channel(f)
        self.assertTrue(res["ok"])
        self.assertEqual(res["sent"], 1)
        self.assertTrue(sl.call_args[0][1].startswith("🏢"))   # 저장 tg 본문

    def test_send_to_channel_falls_back_to_summary(self):
        # tg 없는 옛 기록 → 색인 본문(summary, 링크 <a> 제거)으로 폴백 전송
        f = self._rec(title="옛것", summary="🏢 옛것\n제품: ABC")
        with mock.patch.dict(os.environ, {"TRADE_CHANNEL_CHAT_IDS": "123"}), \
                mock.patch("trade.scripts.customs_alert.send_long", return_value=1) as sl:
            res = RA.send_to_channel(f)
        self.assertTrue(res["ok"])
        body = sl.call_args[0][1]
        self.assertIn("🏢 옛것", body)
        self.assertNotIn("<a", body)                      # 링크 제거됨

    def test_send_to_channel_path_guard(self):
        self.assertFalse(RA.send_to_channel("../../x.html")["ok"])
        self.assertFalse(RA.send_to_channel("report_archive_pages/../x.html")["ok"])

    def test_migrate_frozen_navs(self):
        # #505 이전 동결 페이지의 '../index.html'(NOAH 가로챔) → '../'(trade 루트)
        RA.SNAP_DIR.mkdir(parents=True, exist_ok=True)
        old = RA.SNAP_DIR / "old.html"
        old.write_text("<a href='../index.html'>대시보드</a>", encoding="utf-8")
        RA._migrate_frozen_navs()
        t = old.read_text(encoding="utf-8")
        self.assertNotIn("../index.html", t)
        self.assertIn("href='../'", t)


if __name__ == "__main__":
    unittest.main()
