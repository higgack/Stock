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

    def test_index_body_is_searchable_full_text_plus_link(self):
        # 본문은 그 달 '전체 텍스트'(검색 인덱스) + 동결 페이지 링크.
        # 일년치가 쌓여도 색인 검색이 의미를 갖도록 모든 산업/신호가 본문에.
        industry_archive.record_snapshot(
            self.conn, [{"title": "반도체 생태계 활황", "body": "공급망 허브"}])
        runs = industry_archive.load_runs()
        self.assertEqual(len(runs), 1)
        body = runs[0]["body"]
        self.assertEqual(runs[0]["file"], "archive/2026-04.html")
        self.assertIn("archive/2026-04.html", body)        # 동결 페이지 링크
        self.assertIn("반도체", body)                       # 산업명(검색 대상)
        self.assertIn("초고성장", body)                     # 분류(검색 대상)
        self.assertIn("반도체 생태계 활황", body)           # 🔍 신호 제목
        self.assertIn("공급망 허브", body)                  # 🔍 신호 본문(전문)

    def test_rerecord_same_month_refreshes_content_keeps_meta(self):
        # 같은 확정월 재기록 = 현재월 라이브 추적: 본문(내용)은 갱신되되
        # 최초 기록일·비용은 보존(메타 churn 방지).
        import time
        industry_archive.record_snapshot(
            self.conn, [{"title": "v1", "body": "x"}], cost_krw=42)
        r1 = industry_archive.load_runs()[0]
        time.sleep(0.01)
        industry_archive.record_snapshot(
            self.conn, [{"title": "v2", "body": "y"}])   # cost 없이 재기록
        runs = industry_archive.load_runs()
        self.assertEqual(len(runs), 1)                   # 여전히 1건(같은 달)
        r2 = runs[0]
        self.assertEqual(r2["_date"], r1["_date"])       # 기록일 보존
        self.assertEqual(r2["ts"], r1["ts"])             # ts 보존
        self.assertEqual(r2.get("cost_krw"), 42)         # 최초 비용 보존
        self.assertIn("v2", r2["body"])                  # 내용은 최신으로 갱신
        self.assertNotIn("v1", r2["body"])

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

    def test_render_export_is_clean_shareable(self):
        # 공유용 export: 산업트렌드 전체 + 출처/단위, 깨지는 back-link 없음.
        html = industry_archive.render_export(
            self.conn, [{"title": "AI 인프라", "body": "수요 확대"}])
        self.assertIsNotNone(html)
        self.assertIn("ind-card", html)                    # 산업 카드
        self.assertIn("ins-box", html)                     # 🔍 박스
        self.assertIn("억 달러", html)                      # 단위/출처 한 줄
        self.assertIn("생성", html)                         # 생성일
        self.assertEqual(html.lower().count("<!doctype"), 1)
        # 받는 사람에게 404날 서버 상대링크가 없어야 함
        self.assertNotIn("industry_archive.html", html)
        self.assertNotIn("../index.html", html)

    def test_frozen_page_keeps_back_links(self):
        # 동결 아카이브 페이지는 여전히 색인/대시보드 back-link 유지(공유용과 구분)
        industry_archive.record_snapshot(self.conn, [])
        h = (industry_archive.SNAP_DIR / "2026-04.html").read_text(encoding="utf-8")
        self.assertIn("../industry_archive.html", h)
        self.assertIn("../index.html", h)

    def test_write_export_month_stamped_snapshot(self):
        import tempfile as _t
        from pathlib import Path as _P
        with mock.patch.object(industry_archive, "SHARE_DIR",
                               _P(_t.mkdtemp()) / "share"):
            ym = industry_archive.write_export(self.conn, [{"title": "AI", "body": "x"}])
            self.assertEqual(ym, "2026-04")                 # 최신 확정월로 동결
            snap = industry_archive.SHARE_DIR / "2026-04.html"
            self.assertTrue(snap.exists())
            h = snap.read_text(encoding="utf-8")
            self.assertIn("ind-card", h)
            self.assertNotIn("industry_archive.html", h)    # 공유용: 깨진 링크 없음

    def test_public_share_url_needs_token_and_host(self):
        with mock.patch.object(industry_archive, "_PUBLIC_TOKEN_PATH",
                               industry_archive.SHARE_DIR.parent / ".tok"):
            # 토큰/호스트 둘 다 없으면 None
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertIsNone(industry_archive.public_share_url("2026-04"))
            with mock.patch.dict("os.environ",
                                 {"TRADE_DASHBOARD_PUBLIC_TOKEN": "TK",
                                  "TRADE_PUBLIC_HOST": "http://h:8765"}):
                self.assertEqual(industry_archive.public_share_url("2026-04"),
                                 "http://h:8765/share/TK/2026-04.html")
                self.assertIsNone(industry_archive.public_share_url(None))  # 월 없으면 None

    def test_render_export_none_when_empty(self):
        empty = sqlite3.connect(":memory:")
        self.assertIsNone(industry_archive.render_export(empty, []))
        empty.close()

    def test_ensure_exists(self):
        self.assertFalse(industry_archive.ARCHIVE_HTML.exists())
        industry_archive.ensure_exists()
        self.assertTrue(industry_archive.ARCHIVE_HTML.exists())


if __name__ == "__main__":
    unittest.main()
