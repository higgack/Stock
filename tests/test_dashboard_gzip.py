"""대시보드 서버 gzip 압축 회귀 (사용자 2026-06-28 '대시보드 느려').

NOAH 서버가 큰 HTML/JS/JSON 을 무압축으로 보내던 병목 → 정적 서빙에 gzip 추가.
Accept-Encoding: gzip 이면 압축·해제 정합, 아니면 무압축 동작 동일을 E2E 검증."""
import gzip
import http.client
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import bot.dashboard_server as ds


def _serve(tmp: Path):
    # 인증 off(샌드박스/VM 무관 결정적) + 서빙 루트를 tmp 로(__init__ 가 _ARCHIVE_ROOT 고정).
    ds._TOKEN = ""
    ds._AUTH_USER = ""
    ds._AUTH_PASSWORD = ""
    ds._ARCHIVE_ROOT = tmp
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ds.DashboardHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestDashboardGzip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # >1KB 압축대상 HTML
        (self.tmp / "market.html").write_text(
            "<html><body>" + "수출입 데이터 분석 " * 2000 + "</body></html>",
            encoding="utf-8")
        (self.tmp / "tiny.html").write_text("<i>hi</i>", encoding="utf-8")
        self.srv = _serve(self.tmp)
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()

    def _get(self, path, gzip_accept):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        hdrs = {"Accept-Encoding": "gzip"} if gzip_accept else {}
        c.request("GET", path, headers=hdrs)
        r = c.getresponse()
        enc = r.getheader("Content-Encoding")
        clen = r.getheader("Content-Length")
        raw = r.read()
        c.close()
        return r.status, enc, clen, raw

    def test_html_gzipped_when_accepted(self):
        status, enc, clen, raw = self._get("/market.html", gzip_accept=True)
        self.assertEqual(status, 200)
        self.assertEqual(enc, "gzip")                       # 압축됨
        self.assertEqual(int(clen), len(raw))               # Content-Length=압축길이
        text = gzip.decompress(raw).decode("utf-8")         # 해제 정합
        self.assertIn("수출입 데이터 분석", text)
        self.assertLess(len(raw), 5000)                     # 원본(>40KB) 대비 대폭↓

    def test_no_gzip_when_not_accepted(self):
        status, enc, clen, raw = self._get("/market.html", gzip_accept=False)
        self.assertEqual(status, 200)
        self.assertIsNone(enc)                              # 무압축 동작 동일
        self.assertIn("수출입 데이터 분석", raw.decode("utf-8"))

    def test_tiny_not_gzipped(self):
        # 1KB 미만은 압축 안 함(오버헤드만) — pass-through.
        status, enc, clen, raw = self._get("/tiny.html", gzip_accept=True)
        self.assertEqual(status, 200)
        self.assertIsNone(enc)
        self.assertIn("hi", raw.decode("utf-8"))

    def test_html_keeps_no_cache(self):
        # gzip 후에도 .html no-cache(신선도) 헤더 보존.
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", "/market.html", headers={"Accept-Encoding": "gzip"})
        r = c.getresponse()
        self.assertIn("no-cache", (r.getheader("Cache-Control") or ""))
        r.read(); c.close()


if __name__ == "__main__":
    unittest.main()
