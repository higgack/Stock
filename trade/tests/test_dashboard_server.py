"""trade.dashboard_server — 공개 share 토큰 게이트 + 라이브 BasicAuth 격리.

핵심 보안: 공개 prefix(/share/<token>/) 안에서도 허용된 1개 파일만 서빙되고,
다른 어떤 경로도(라이브 index 포함) 401/404가 떠야 한다. 토큰을 모르면 아예
못 들어오고, 알아도 industry_export.html만 보임."""

import os
import threading
import time
import unittest
import urllib.request
import urllib.error
from base64 import b64encode
from http.server import HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory


class PublicShareGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        data = Path(cls._tmp.name)
        (data / "dashboard" / "share").mkdir(parents=True)
        (data / "dashboard" / "share" / "2026-04.html").write_text(
            "<html>SNAPSHOT-2026-04</html>", encoding="utf-8")
        (data / "dashboard" / "index.html").write_text(
            "<html>LIVE</html>", encoding="utf-8")
        cls._old_env = {k: os.environ.get(k) for k in [
            "TRADE_DATA_DIR", "TRADE_DASHBOARD_USER", "TRADE_DASHBOARD_PASSWORD",
            "TRADE_DASHBOARD_PUBLIC_TOKEN", "TRADE_DASHBOARD_TOKEN"]}
        os.environ["TRADE_DATA_DIR"] = str(data)
        os.environ["TRADE_DASHBOARD_USER"] = "u"
        os.environ["TRADE_DASHBOARD_PASSWORD"] = "p"
        os.environ["TRADE_DASHBOARD_PUBLIC_TOKEN"] = "TESTTOKEN"
        os.environ.pop("TRADE_DASHBOARD_TOKEN", None)
        # Force fresh import so module-level constants pick up env
        import importlib, sys
        if "trade.dashboard_server" in sys.modules:
            del sys.modules["trade.dashboard_server"]
        from trade import dashboard_server as srv
        srv = importlib.reload(srv)
        cls.srv_mod = srv
        # 직접 핸들러를 띄우면 SimpleHTTPRequestHandler가 CWD에서 서빙하므로
        # data_dir로 들어가야 함. tearDownClass 에서 원복(2026-07-15 회귀 —
        # 복원 누락 시 같은 프로세스에서 뒤에 도는 CWD-상대경로 테스트가 깨짐).
        cls._old_cwd = os.getcwd()
        os.chdir(data)
        cls.httpd = HTTPServer(("127.0.0.1", 0), srv.GatedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=1)
        os.chdir(cls._old_cwd)
        cls._tmp.cleanup()
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _get(self, path, auth=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if auth:
            req.add_header("Authorization",
                           "Basic " + b64encode(auth.encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=2) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, ""

    def test_public_share_serves_month_snapshot_without_auth(self):
        code, body = self._get("/share/TESTTOKEN/2026-04.html")
        self.assertEqual(code, 200)
        self.assertIn("SNAPSHOT-2026-04", body)

    def test_public_share_other_paths_404(self):
        # 'YYYY-MM.html' 월 스냅샷 외엔 전부 차단(라이브/임의 경로 노출 방지)
        for p in ("/share/TESTTOKEN/",
                  "/share/TESTTOKEN/index.html",
                  "/share/TESTTOKEN/dashboard/index.html",
                  "/share/TESTTOKEN/store.db",
                  "/share/TESTTOKEN/2099-99.html"):  # 없는 월 → 404(파일 부재)
            code, _ = self._get(p)
            self.assertEqual(code, 404, f"path={p}")

    def test_wrong_token_blocked(self):
        # 토큰 추측은 BasicAuth(401)로 떨어짐 — 공개되지 않음
        code, _ = self._get("/share/WRONGTOKEN/")
        self.assertEqual(code, 401)

    def test_live_dashboard_still_needs_auth(self):
        code, _ = self._get("/dashboard/index.html")
        self.assertEqual(code, 401)
        code, body = self._get("/dashboard/index.html", auth="u:p")
        self.assertEqual(code, 200)
        self.assertIn("LIVE", body)


class KgApproveApiTests(unittest.TestCase):
    """레퍼런스북 미매칭 반영 버튼의 서버측 라우트(사용자 2026-07-15). GET
    전용(NOAH 프록시가 POST 미포워드) — _api_kg_approve 는 순수 함수 호출로
    직접 검증(서버 기동 불요), 라우트 배선은 별도 grep(E2E)."""

    def test_missing_params_graceful(self):
        from trade import dashboard_server as srv
        self.assertEqual(srv._api_kg_approve("", "취급품목", "X"),
                         {"ok": False, "error": "co/rel/tgt 누락"})
        self.assertEqual(srv._api_kg_approve("A", "", "X")["ok"], False)
        self.assertEqual(srv._api_kg_approve("A", "취급품목", "")["ok"], False)

    def test_delegates_to_approve_candidates_and_regenerates(self):
        from trade import dashboard_server as srv, kg_candidates
        calls = {}

        def fake_approve(keys=None, **kw):
            calls["keys"] = keys
            return {"ingested": 1, "approved": 0, "total": 1, "skipped": 0,
                    "duplicates": 0}

        orig_approve = kg_candidates.approve_candidates
        kg_candidates.approve_candidates = fake_approve
        try:
            from trade import reference_book
            orig_regen = reference_book.regenerate
            regen_called = []
            reference_book.regenerate = lambda: regen_called.append(1)
            try:
                res = srv._api_kg_approve("현대중공업터보기계", "취급품목",
                                          "ECAC/FGC 압축기")
            finally:
                reference_book.regenerate = orig_regen
        finally:
            kg_candidates.approve_candidates = orig_approve
        self.assertTrue(res["ok"])
        self.assertEqual(res["ingested"], 1)
        self.assertEqual(calls["keys"],
                         [("현대중공업터보기계", "취급품목", "ECAC/FGC 압축기")])
        self.assertEqual(regen_called, [1])

    def test_route_wired(self):
        # 헬퍼만 만들고 미배선 방지 (E2E grep).
        src = open("trade/dashboard_server.py", encoding="utf-8").read()
        self.assertIn('"/api/kg_approve"', src)
        self.assertIn("_api_kg_approve(", src)


if __name__ == "__main__":
    unittest.main()
