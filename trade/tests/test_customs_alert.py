"""trade.scripts.customs_alert — ±30% alert gating tests.

The alert is the only Telegram output of the customs feature, so its
safety rails are what these tests pin:
  - baseline-silent: first run over existing months sends nothing
  - a NEW month over threshold fires exactly once (then never again)
  - under-threshold new months stay silent
  - a freshly-pinned item's history doesn't blast
  - over-cap collapses to one summary
  - send failure does NOT mark seen (retries); no-operator skips
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from trade import customs
from trade.scripts import customs_alert


def _seed(db, hs, months):
    """months: list of (year_month, exp_dlr)."""
    with customs.session(db) as conn:
        for ym, exp in months:
            customs.upsert_month(conn, hs, ym, {
                "exp_dlr": exp, "imp_dlr": 0, "bal_payments": exp,
                "exp_wgt": 0, "imp_wgt": 0,
            })


class _Collector:
    def __init__(self, ok=True):
        self.msgs = []
        self.ok = ok

    def __call__(self, chat_id, text):
        self.msgs.append((chat_id, text))
        return self.ok


class TestCustomsAlert(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "customs.db"
        os.environ["TRADE_OPERATOR_CHAT_ID"] = "555"  # operator present

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("TRADE_OPERATOR_CHAT_ID", None)

    def _run(self, pins, notify):
        with mock.patch.object(customs_alert.hs_map, "entries", return_value=pins):
            return customs_alert.run(db_path=self.db, notify=notify)

    def test_baseline_silent_on_first_run(self):
        # 3 months incl. a +100% jump — but first run is baseline → silent.
        _seed(self.db, "1902301010", [
            ("2026-01", 100), ("2026-02", 100), ("2026-03", 200),
        ])
        c = _Collector()
        self._run([("라면", "1902301010")], c)
        self.assertEqual(c.msgs, [])

    def test_new_month_over_threshold_fires_once(self):
        _seed(self.db, "1902301010", [("2026-01", 100), ("2026-02", 100)])
        c = _Collector()
        # first run: baseline (marks 01,02), silent
        self._run([("라면", "1902301010")], c)
        self.assertEqual(c.msgs, [])
        # new month arrives: +60% over Feb → fires
        _seed(self.db, "1902301010", [("2026-03", 160)])
        self._run([("라면", "1902301010")], c)
        self.assertEqual(len(c.msgs), 1)
        self.assertIn("라면", c.msgs[0][1])
        self.assertIn("+60.0%", c.msgs[0][1])
        # subsequent run with no new month: no re-alert
        self._run([("라면", "1902301010")], c)
        self.assertEqual(len(c.msgs), 1)

    def test_under_threshold_new_month_silent(self):
        _seed(self.db, "1902301010", [("2026-01", 100), ("2026-02", 100)])
        c = _Collector()
        self._run([("라면", "1902301010")], c)            # baseline
        _seed(self.db, "1902301010", [("2026-03", 110)])   # +10% < 30%
        self._run([("라면", "1902301010")], c)
        self.assertEqual(c.msgs, [])

    def test_new_pin_history_does_not_blast(self):
        # Pin A baselined; later pin B added with a swingy history → B is
        # baselined silently, not blasted.
        _seed(self.db, "A", [("2026-01", 100), ("2026-02", 100)])
        c = _Collector()
        self._run([("itemA", "A")], c)                     # baseline A
        _seed(self.db, "B", [("2026-01", 10), ("2026-02", 100)])  # +900%
        self._run([("itemA", "A"), ("itemB", "B")], c)
        self.assertEqual(c.msgs, [])                       # B baselined, silent

    def test_over_cap_collapses_to_summary(self):
        # CAP is read at import time, so patch the module attribute.
        with mock.patch.object(customs_alert, "CAP", 2):
            # 3 pins, each baselined then given a >30% new month → 3 candidates
            pins = []
            for k in ("A", "B", "C"):
                _seed(self.db, k, [("2026-01", 100), ("2026-02", 100)])
                pins.append((f"item{k}", k))
            c = _Collector()
            self._run(pins, c)                              # baseline all
            for k in ("A", "B", "C"):
                _seed(self.db, k, [("2026-03", 200)])       # +100% each
            self._run(pins, c)
            self.assertEqual(len(c.msgs), 1)                # ONE summary
            body = c.msgs[0][1]
            self.assertIn("3건", body)
            self.assertIn("cap 2", body)

    def test_send_failure_does_not_mark_seen(self):
        _seed(self.db, "1902301010", [("2026-01", 100), ("2026-02", 100)])
        fail = _Collector(ok=False)
        self._run([("라면", "1902301010")], fail)           # baseline
        _seed(self.db, "1902301010", [("2026-03", 200)])    # +100%
        self._run([("라면", "1902301010")], fail)           # send fails
        self.assertEqual(len(fail.msgs), 1)                 # attempted
        # retry with a working sender → re-attempts the SAME month (not lost)
        ok = _Collector(ok=True)
        self._run([("라면", "1902301010")], ok)
        self.assertEqual(len(ok.msgs), 1)
        self.assertIn("+100.0%", ok.msgs[0][1])

    def test_no_operator_chat_skips_and_retries(self):
        os.environ.pop("TRADE_OPERATOR_CHAT_ID", None)
        with mock.patch.object(customs_alert.operator, "get", return_value=None):
            _seed(self.db, "1902301010", [("2026-01", 100), ("2026-02", 100)])
            c = _Collector()
            self._run([("라면", "1902301010")], c)           # baseline
            _seed(self.db, "1902301010", [("2026-03", 200)])
            self._run([("라면", "1902301010")], c)           # no chat → skip
            self.assertEqual(c.msgs, [])
        # operator appears later → the month still fires (wasn't marked)
        os.environ["TRADE_OPERATOR_CHAT_ID"] = "555"
        ok = _Collector()
        self._run([("라면", "1902301010")], ok)
        self.assertEqual(len(ok.msgs), 1)

    def test_no_pins_noop(self):
        c = _Collector()
        rc = self._run([], c)
        self.assertEqual(rc, 0)
        self.assertEqual(c.msgs, [])


def _ts(y, m, d, hh=12):
    """KST wall clock → epoch (file mtimes / `now` pinned to real dates)."""
    from datetime import datetime, timedelta, timezone
    return datetime(y, m, d, hh, tzinfo=timezone(timedelta(hours=9))).timestamp()


class TestHsReferenceFreshness(unittest.TestCase):
    """The HS revision reminder judges the REVISION, not the file's age.

    Rewritten 2026-11-27 (#222): the old contract was 'older than 180 days
    → alert', and it fired on a file saved 2026-05-31 that already WAS the
    2026-01-01 revision the portal still offered (the alert's own link
    preview showed 관세청_HS부호_20260101) — and would have re-fired every
    30 days until the next revision. Kept guarantees: silent when missing /
    current / re-alerted within 30 days; marker only after a good send."""

    D = __import__("datetime").date

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ref = self.dir / "hs_codes.xlsx"
        self.marker = self.dir / ".hs_ref_alert_seen"
        self.state = self.dir / ".hs_ref_revision.json"
        self.portal = (self.D(2026, 1, 1), "")
        self.now = _ts(2026, 11, 27)
        self._patches = [
            mock.patch.object(customs_alert, "_HS_REF_PATHS",
                              (self.ref, self.dir / "hs_codes.csv")),
            mock.patch.object(customs_alert, "_HS_REF_MARKER", self.marker),
            mock.patch.object(customs_alert, "_HS_REF_STATE", self.state),
            mock.patch.object(customs_alert, "portal_revision",
                              side_effect=lambda: self.portal),
        ]
        for p in self._patches:
            p.start()
        os.environ["TRADE_OPERATOR_CHAT_ID"] = "555"

    def tearDown(self):
        for p in self._patches:
            p.stop()
        os.environ.pop("TRADE_OPERATOR_CHAT_ID", None)
        self.tmp.cleanup()

    def _file(self, y, m, d, body=b"x"):
        self.ref.write_bytes(body)
        t = _ts(y, m, d)
        os.utime(self.ref, (t, t))

    def _check(self, now=None):
        return customs_alert._check_hs_reference_freshness(
            now=self.now if now is None else now)

    def test_missing_file_returns_none(self):
        self.assertIsNone(self._check())

    def test_reported_case_180_day_old_current_revision_is_silent(self):
        """The 2026-11-27 alert: saved 180 days ago, after the 2026-01-01
        revision the portal still names → current, no alert, and the
        attribution is recorded for later runs."""
        self._file(2026, 5, 31)
        self.assertIsNone(self._check())
        rec = __import__("json").loads(self.state.read_text())
        self.assertEqual(rec["revision"], "2026-01-01")

    def test_newer_portal_revision_alerts(self):
        self._file(2026, 5, 31)
        self.assertIsNone(self._check())              # records 2026-01-01
        self.portal = (self.D(2027, 1, 1), "")
        msg = self._check(now=_ts(2027, 1, 20))
        self.assertIsNotNone(msg)
        self.assertIn("2027-01-01</b> 개정", msg)
        self.assertIn("2026-05-31 저장 → 2026-01-01 판", msg)
        self.assertIn("15049722", msg)

    def test_first_sight_file_older_than_revision_alerts_without_record(self):
        self._file(2025, 12, 1)
        msg = self._check()
        self.assertIsNotNone(msg)
        self.assertIn("그보다 앞선 판", msg)
        self.assertFalse(self.state.exists())         # not attributed

    def test_replaced_file_is_the_portal_latest_even_if_saved_before_effective(self):
        """A pre-published revision downloaded in December must not read as
        old after the record exists (the date-only rule would nag forever)."""
        self._file(2026, 5, 31)
        self.assertIsNone(self._check())               # record: old file
        self.portal = (self.D(2027, 1, 1), "")
        self._file(2026, 12, 20, body=b"new revision")  # operator replaced it
        self.assertIsNone(self._check(now=_ts(2026, 12, 21)))
        self.assertIsNone(self._check(now=_ts(2027, 2, 20)))
        rec = __import__("json").loads(self.state.read_text())
        self.assertEqual(rec["revision"], "2027-01-01")

    def test_portal_unreadable_falls_back_to_calendar_and_says_why(self):
        self.portal = (None, "포털 접속 실패(URLError)")
        self._file(2026, 5, 31)
        self.assertIsNone(self._check())               # saved after Jan 1
        self._file(2025, 12, 1)
        msg = self._check()
        self.assertIsNotNone(msg)
        self.assertIn("달력 기준(1월 1일 개정) — 포털 접속 실패(URLError)", msg)
        self.assertIn("2026-01-01</b> 개정", msg)
        self.assertFalse(self.state.exists())          # nothing attributed

    def test_recent_marker_blocks_realert(self):
        self._file(2025, 12, 1)
        self.marker.touch()
        t = self.now - 5 * 86400
        os.utime(self.marker, (t, t))
        self.assertIsNone(self._check())

    def test_old_marker_allows_realert(self):
        self._file(2025, 12, 1)
        self.marker.touch()
        t = self.now - 60 * 86400
        os.utime(self.marker, (t, t))
        self.assertIsNotNone(self._check())

    def test_run_sends_freshness_then_touches_marker(self):
        self._file(2025, 12, 1)
        c = _Collector(ok=True)
        with mock.patch.object(customs_alert.hs_map, "entries", return_value=[]):
            customs_alert.run(db_path=self.dir / "customs.db", notify=c)
        self.assertEqual(len(c.msgs), 1)
        self.assertIn("관세청 HS부호 개정", c.msgs[0][1])
        self.assertTrue(self.marker.exists())

    def test_run_does_not_touch_marker_on_send_failure(self):
        self._file(2025, 12, 1)
        fail = _Collector(ok=False)
        with mock.patch.object(customs_alert.hs_map, "entries", return_value=[]):
            customs_alert.run(db_path=self.dir / "customs.db", notify=fail)
        self.assertFalse(self.marker.exists())  # retry next tick

    def test_hs_check_is_read_only_and_speaks_on_healthy_branch(self):
        import contextlib
        import io
        self._file(2026, 5, 31)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(customs_alert.sys, "argv", ["x", "--hs-check"]):
            rc = customs_alert.main()
        self.assertEqual(rc, 0)
        self.assertIn("✅ 최신판", out.getvalue())
        self.assertIn("포털 최신판: 2026-01-01 개정", out.getvalue())
        self.assertFalse(self.state.exists())
        self.assertFalse(self.marker.exists())

    def test_hs_check_calendar_fallback_never_says_older_next_to_ok(self):
        """Portal unreadable + no record + saved after Jan 1: the verdict
        is ✅ by save date, so the 'held' line must not claim an older
        revision on the same screen (self-review 2026-11-27)."""
        import contextlib
        import io
        self.portal = (None, "포털 접속 실패(URLError)")
        self._file(2026, 5, 31)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(customs_alert.sys, "argv", ["x", "--hs-check"]):
            rc = customs_alert.main()
        text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("✅ 최신판", text)
        self.assertIn("판 기록 없음 — 저장일이 개정일 뒤라 최신판으로 봄", text)
        self.assertNotIn("그보다 앞선 판", text)

    def test_hs_check_stale_returns_1(self):
        import contextlib
        import io
        self._file(2025, 12, 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(customs_alert.sys, "argv", ["x", "--hs-check"]):
            rc = customs_alert.main()
        self.assertEqual(rc, 1)
        self.assertIn("⚠️ 새 판을 받아", out.getvalue())
        self.assertIn("그보다 앞선 판", out.getvalue())


class TestPortalRevision(unittest.TestCase):
    """The portal reader: latest date on the page, and two named failure
    branches (unreachable vs. layout without the marker)."""

    def _body(self, text):
        class _R:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s, n=-1):
                return text.encode("utf-8")
        return mock.patch.object(customs_alert.urllib.request, "urlopen",
                                 return_value=_R())

    def test_latest_date_wins(self):
        from datetime import date
        # Older first so 'first match' can't pass for 'latest'; 20251399
        # is not a date and must be skipped, not raise.
        with self._body("이력 관세청_HS부호_20250101 "
                        "<title>관세청_HS부호_20260101</title> 관세청_HS부호_20251399"):
            self.assertEqual(customs_alert.portal_revision(),
                             (date(2026, 1, 1), ""))

    def test_page_without_marker_names_layout(self):
        with self._body("<html>다른 화면</html>"):
            rev, why = customs_alert.portal_revision()
        self.assertIsNone(rev)
        self.assertIn("표기가 없음", why)

    def test_unreachable_names_exception(self):
        with mock.patch.object(customs_alert.urllib.request, "urlopen",
                               side_effect=OSError("down")):
            rev, why = customs_alert.portal_revision()
        self.assertIsNone(rev)
        self.assertEqual(why, "포털 접속 실패(OSError)")


class SplitTelegramTests(unittest.TestCase):
    """전체 내용 전송 — 4096 한도 분할 (사용자 2026-06-18 '현재는 일부만')."""

    def test_short_one_chunk(self):
        self.assertEqual(customs_alert.split_telegram("짧은 내용"), ["짧은 내용"])

    def test_long_splits_at_line_boundary_roundtrip(self):
        text = "\n".join(f"• 품목{i} <b>값</b> 1.2억$" for i in range(400))
        chunks = customs_alert.split_telegram(text, limit=4000)
        self.assertGreaterEqual(len(chunks), 2)
        for c in chunks:
            self.assertLessEqual(customs_alert._u16(c), 4000)
        self.assertEqual("\n".join(chunks), text)        # 줄 경계 → 무손실

    def test_single_overlong_line_hard_split(self):
        chunks = customs_alert.split_telegram("X" * 9000, limit=4000)
        self.assertEqual(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(customs_alert._u16(c), 4000)

    def test_send_long_sends_each_chunk(self):
        text = "\n".join(f"line{i} " + "y" * 200 for i in range(60))   # >4000
        with mock.patch.object(customs_alert, "_send", return_value=True) as snd:
            n = customs_alert.send_long(111, text)
        self.assertGreaterEqual(snd.call_count, 2)        # 다중 메시지
        self.assertEqual(n, snd.call_count)


if __name__ == "__main__":
    unittest.main()
