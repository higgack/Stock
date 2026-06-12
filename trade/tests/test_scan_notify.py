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
