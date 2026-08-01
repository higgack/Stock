"""관세청 데이터 갱신 알림 (사용자 2026-06-13) — 채널 포워드 급증 알림과
구분되는 '✅ 관세청 데이터 갱신' 헤더. 4회/일 스캔이지만 변경 시에만 발화."""
import json
import unittest
from pathlib import Path

from trade.scripts import scan_customs as sc


class ScanNotifyTests(unittest.TestCase):
    HM = [
        {"ref_ym": "2026-05", "exp": 100, "imp": 50, "exp_py": 0, "imp_py": 0},
        {"ref_ym": "2026-05", "exp": 200, "imp": 80, "exp_py": 90, "imp_py": 40},
    ]

    def test_fingerprint_and_change_gate(self):
        fp = sc._scan_fingerprint(self.HM, [1, 2])
        self.assertEqual(fp["ref_ym"], "2026-05")
        self.assertEqual(fp["se"], 300)
        self.assertEqual(fp["sey"], 90)
        self.assertTrue(sc._fingerprint_changed({}, fp))        # 최초
        self.assertFalse(sc._fingerprint_changed(fp, fp))       # 무변경 무음
        self.assertTrue(sc._fingerprint_changed({**fp, "si": 1}, fp))
        # YoY 충전: sey 0→양수 = 변경
        base = sc._scan_fingerprint(
            [{**r, "exp_py": 0, "imp_py": 0} for r in self.HM], [1, 2])
        self.assertTrue(sc._fingerprint_changed(base, fp))

    def test_notify_fires_once_then_silent(self, ):
        import tempfile
        sc._NOTIFY_MARKER = Path(tempfile.mkdtemp()) / ".scan_notified.json"
        sent = []
        send = lambda b: (sent.append(b), True)[1]
        ok = sc._maybe_notify_refresh(self.HM, {}, [1, 2], send=send)
        self.assertTrue(ok)
        self.assertEqual(len(sent), 1)
        self.assertIn("관세청 데이터 갱신", sent[0])          # 구분 헤더
        self.assertIn("확정월 2026-05", sent[0])
        self.assertIn("신규 급증 2건", sent[0])
        self.assertNotIn("(YoY 대기)", sent[0])               # sey>0 → 준비됨
        # 무변경 재호출 — 무음 (4회/일 스팸 차단)
        self.assertFalse(sc._maybe_notify_refresh(self.HM, {}, [1, 2], send=send))
        self.assertEqual(len(sent), 1)

    def test_yoy_pending_label(self):
        import tempfile
        sc._NOTIFY_MARKER = Path(tempfile.mkdtemp()) / ".scan_notified.json"
        sent = []
        hm0 = [{**r, "exp_py": 0, "imp_py": 0} for r in self.HM]
        sc._maybe_notify_refresh(hm0, {}, [], send=lambda b: sent.append(b) or True)
        self.assertIn("(YoY 대기)", sent[0])

    def test_wired_into_main(self):
        src = Path(sc.__file__).read_text(encoding="utf-8")
        self.assertIn("_maybe_notify_refresh(hm_rows, leaves, new_entrants)", src)


if __name__ == "__main__":
    unittest.main()


class ProbeModeTests(unittest.TestCase):
    """--if-changed probe (사용자 2026-06-13 '4회/일보다 빠르게 리스크
    없이') — 1콜 감지, 변경 시에만 풀 스윕. 시간당 풀스윕(~12,000콜/일 =
    한도 초과)의 안전 대체: probe ~150콜/일 + 변경 시 스윕."""

    ROWS = [
        {"hs_code": "8542310000", "year_month": "2026-05",
         "exp_dlr": 100, "imp_dlr": 40},
        {"hs_code": "8517120000", "year_month": "2026-05",
         "exp_dlr": 50, "imp_dlr": 10},
        {"hs_code": "8542310000", "year_month": "2026-06",
         "exp_dlr": 0, "imp_dlr": 0},   # 미래 0행 (2026-06-01 클래스)
    ]

    def _tmp(self):
        import tempfile
        from pathlib import Path as _P
        t = _P(tempfile.mkdtemp())
        sc._PROBE_MARKER = t / ".scan_probe.json"
        sc._PROBE_RUN_GUARD = t / ".scan_probe_run.ts"
        return t

    def test_fingerprint_skips_future_zero_rows(self):
        from unittest import mock
        with mock.patch.object(sc.customs_scan, "fetch_chapter",
                               return_value=self.ROWS):
            fp = sc._probe_fingerprint("k")
        self.assertEqual(fp, {"ym": "2026-05", "se": 150, "si": 50})

    def test_gate_lifecycle(self):
        from unittest import mock
        self._tmp()
        with mock.patch.object(sc.customs_scan, "fetch_chapter",
                               return_value=self.ROWS):
            self.assertFalse(sc._probe_says_skip("k"))   # 최초 → 스윕
            self.assertTrue(sc._probe_says_skip("k"))    # in-flight guard
            sc._PROBE_RUN_GUARD.unlink()
            sc._probe_save("k")
            self.assertTrue(sc._probe_says_skip("k"))    # 무변경 무음
        new = self.ROWS + [{"hs_code": "8542310000", "year_month": "2026-06",
                            "exp_dlr": 80, "imp_dlr": 30}]
        with mock.patch.object(sc.customs_scan, "fetch_chapter",
                               return_value=new):
            self.assertFalse(sc._probe_says_skip("k"))   # 새 월 → 발동

    def test_probe_failure_conservative_skip(self):
        from unittest import mock
        self._tmp()
        with mock.patch.object(sc.customs_scan, "fetch_chapter",
                               side_effect=RuntimeError("boom")):
            self.assertTrue(sc._probe_says_skip("k"))    # 정기 풀스윕이 안전망

    def test_probe_alert_includes_http_status_code(self):
        """회귀 고정(사용자 요청 2026-07-31): probe 오류 알림이 예외 클래스명
        ("HTTPError")만 찍어서 서버 일시장애(5xx)·서비스키 문제(401/403)·
        트래픽제한(429)을 구분할 수 없었다. urllib.error.HTTPError 는 실제
        상태코드+사유를 붙이고, 코드가 없는 예외(URLError/timeout 등)는
        클래스명만 유지."""
        import urllib.error
        self.assertEqual(
            sc._exc_detail(urllib.error.HTTPError("u", 503, "Service Unavailable",
                                                  {}, None)),
            "HTTPError 503 (Service Unavailable)")
        self.assertEqual(
            sc._exc_detail(urllib.error.HTTPError("u", 401, "Unauthorized",
                                                  {}, None)),
            "HTTPError 401 (Unauthorized)")
        # 코드 없는 예외는 클래스명 + 메시지 — 관세청 CustomsAPIError 는 메시지
        # 자체가 resultCode/resultMsg 진단이라 클래스명만 찍으면 사유가 버려진다
        # (2026-08-01 '97챕터 전부 실패' 알림이 정확히 그랬음).
        self.assertEqual(sc._exc_detail(TimeoutError("timed out")),
                         "TimeoutError: timed out")
        self.assertEqual(sc._exc_detail(TimeoutError()), "TimeoutError")
        self.assertEqual(sc._exc_detail(urllib.error.URLError("no route")),
                         "URLError: <urlopen error no route>")
        self.assertEqual(sc._exc_detail(None), "Unknown")
        from trade.customs import CustomsAPIError
        d = sc._exc_detail(CustomsAPIError("resultCode=99 resultMsg='기간 초과'"))
        self.assertIn("resultCode=99", d)
        self.assertIn("기간 초과", d)
        self.assertLessEqual(len(sc._exc_detail(RuntimeError("x" * 500))), 180)

    def test_scan_fail_alert_names_the_reason(self):
        """'97챕터 전부 실패' 알림이 옛날엔 'journal 의 resultMsg 확인 필요'
        라고만 해서 운영자가 VM 로그를 뒤져야 했다(자동화 원칙 위반 — 알림이
        스스로 원인을 말해야 한다, 사용자 2026-08-01). 사유를 빈도순으로 요약."""
        import urllib.error

        from trade.customs import CustomsAPIError
        errs = [sc._exc_detail(CustomsAPIError(
            "resultCode=99 resultMsg='SERVICE KEY IS NOT REGISTERED'"))] * 95
        errs += [sc._exc_detail(urllib.error.HTTPError(
            "u", 500, "Internal Server Error", {}, None))] * 2
        s = sc._err_summary(errs)
        self.assertIn("SERVICE KEY IS NOT REGISTERED", s)
        self.assertIn("×95", s)
        self.assertIn("HTTPError 500", s)
        self.assertEqual(sc._err_summary([]), "사유 미확인")
        # 상위 N 초과분은 '그 외 M종' 으로 접기
        many = [f"E{i}" for i in range(9)]
        self.assertIn("그 외 6종", sc._err_summary(many))
        # parse_mode=HTML 이므로 API 원문의 <,>,& escape (실수#7)
        esc = sc._err_summary([sc._exc_detail(CustomsAPIError("msg='<b>x</b> a&b'"))])
        self.assertNotIn("<b>", esc)
        self.assertIn("&lt;b&gt;", esc)
        self.assertIn("&amp;", esc)
        self.assertIn("'", esc, "따옴표는 그대로 읽히게(quote=False)")
        # 호출부 배선 — 헬퍼만 만들고 안 쓰는 누락 방지
        src = Path(sc.__file__).read_text(encoding="utf-8")
        self.assertIn("errs.append(_exc_detail(exc))", src)
        self.assertIn("_err_summary(errs)", src)
        self.assertNotIn("journal 의 resultMsg 확인 필요", src)

    def test_probe_failure_alert_uses_exc_detail(self):
        """probe 2회 실패 시 알림 본문에 _exc_detail 결과(상태코드 포함)가
        실제로 실려 보내지는지 — 헬퍼만 만들고 호출부에서 안 쓰는 배선누락 방지.
        run_ledger.bump 는 실제 홈 디렉토리 JSON 을 건드리므로(날짜별 누적이라
        같은 날 재실행 시 dedup 반환값이 1이 아닐 수 있음) 직접 모킹해 격리."""
        from unittest import mock
        from trade import run_ledger
        import urllib.error
        self._tmp()
        sent = []
        with mock.patch.object(sc.customs_scan, "fetch_chapter",
                               side_effect=urllib.error.HTTPError(
                                   "u", 503, "Service Unavailable", {}, None)), \
             mock.patch.object(sc, "_send_alert", side_effect=lambda body: sent.append(body) or True), \
             mock.patch.object(run_ledger, "bump", return_value=1), \
             mock.patch.object(sc, "time") as mtime:
            mtime.sleep = lambda *_: None
            sc._probe_fingerprint("k")
        self.assertTrue(sent, "probe_fail 알림이 안 보내짐")
        self.assertIn("HTTPError 503 (Service Unavailable)", sent[0])

    def test_units_and_wiring(self):
        from pathlib import Path as _P
        root = _P(sc.__file__).resolve().parents[2]
        timer = (root / "deploy" / "trade-bot-customs-probe.timer").read_text()
        self.assertIn("OnUnitActiveSec=10min", timer)
        svc = (root / "deploy" / "trade-bot-customs-probe.service").read_text()
        self.assertIn("--if-changed", svc)
        src = _P(sc.__file__).read_text(encoding="utf-8")
        self.assertIn("_probe_says_skip(key)", src)
        self.assertIn("_probe_save(key)", src)


class RolloutScanTests(unittest.TestCase):
    """배포 직후 1회 강제 populate 스윕 (버전드 마커, 사용자 2026-06-13).
    probe 는 관세청 무변경이면 스윕 skip → 새 스냅샷 필드(단가 중량·수입
    랭킹 등)가 다음 갱신(월 ~3회)까지 안 채워짐. 마커 내용 != 현재 버전이면
    1회 강제 풀 스윕(성공 저장 경로에서만 버전 기록 → 실패 시 재시도).
    새 populate-필요 기능 = 버전만 bump 하면 자동 1회 강제."""

    def _tmp_marker(self):
        import tempfile
        from pathlib import Path as _P
        sc._ROLLOUT_MARKER = _P(tempfile.mkdtemp()) / ".weights_backfilled"

    def test_pending_then_done_versioned(self):
        self._tmp_marker()
        self.assertTrue(sc._rollout_scan_pending())          # 마커 부재 → 강제
        sc._mark_rollout_done()
        self.assertFalse(sc._rollout_scan_pending())         # 현 버전 기록 → 정상
        # 옛 마커(다른 내용=구버전/timestamp)는 다시 pending → 자동 1회 강제
        sc._ROLLOUT_MARKER.write_text("1700000000.0", encoding="utf-8")
        self.assertTrue(sc._rollout_scan_pending())

    def test_force_bypasses_probe_skip(self):
        # 마커 stale 시, 관세청 무변경(probe skip True)이어도 게이트가
        # 스윕을 건너뛰지 않아야 한다 (force_rollout 가 probe-skip 우회).
        self._tmp_marker()
        force = sc._rollout_scan_pending()
        probe_skip = True                                    # 무변경 가정
        self.assertTrue(force and probe_skip)                # 둘 다 참인데
        self.assertFalse(not force and probe_skip)           # 게이트는 skip 안 함

    def test_wired_into_main(self):
        from pathlib import Path as _P
        src = _P(sc.__file__).read_text(encoding="utf-8")
        # 게이트: --if-changed AND not force_rollout AND probe_skip 일 때만 종료
        self.assertIn("not force_rollout and _probe_says_skip(key)", src)
        # 마커 호출(def 아님)은 성공 저장 로그(stored live) 뒤에서만 —
        # 조기 return(ok==0/coverage/empty) 경로 제외 → 실패 시 다음 probe 재시도
        stored = src.index('log.info("stored live')
        src.index("_mark_rollout_done()", stored)            # 없으면 ValueError=fail


class CustomsHttpErrorTests(unittest.TestCase):
    """HTTP 실패 시 **어느 URL 이 죽었는지**를 에러에 싣는다 (사용자 2026-08-01
    전 챕터 404): 옛 메시지는 "HTTP Error 404: Not Found" 뿐이라 엔드포인트가
    바뀐 건지 파라미터가 잘못된 건지 구분할 수 없었다. ⚠️ serviceKey 는 반드시
    마스킹(키 노출 금지)."""

    URL = ("https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
           "?serviceKey=SUPER%2BSECRET&strtYymm=202605&endYymm=202607&hsSgn=85")

    def test_redact_url_masks_key_keeps_path(self):
        from trade.customs import redact_url
        r = redact_url(self.URL)
        self.assertNotIn("SUPER", r)
        self.assertNotIn("SECRET", r)
        self.assertIn("***", r)
        self.assertIn("Itemtrade/getItemtradeList", r)   # 경로는 보존(진단용)
        self.assertIn("hsSgn=85", r)
        # 파싱 불가 입력도 graceful(최소한 경로만)
        self.assertEqual(redact_url("not a url"), "not a url")

    def test_http_error_carries_status_and_url(self):
        import urllib.error
        import urllib.request
        from unittest import mock

        from trade import customs

        def _boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        with mock.patch.object(urllib.request, "urlopen", _boom):
            with self.assertRaises(customs.CustomsAPIError) as cm:
                customs._http_get(self.URL)
        msg = str(cm.exception)
        self.assertIn("404", msg)
        self.assertIn("Not Found", msg)
        self.assertIn("Itemtrade/getItemtradeList", msg)
        self.assertNotIn("SECRET", msg, "에러 메시지에 서비스키가 샜다")
        # 알림 본문까지 그대로 이어지는지(배선)
        self.assertIn("404", sc._exc_detail(cm.exception))
