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


class TestHsReferenceFreshness(unittest.TestCase):
    """The annual-update reminder. Has to silently no-op when fresh /
    missing / recently-alerted — anything else nags."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ref = self.dir / "hs_codes.xlsx"
        self.marker = self.dir / ".hs_ref_alert_seen"
        self._patches = [
            mock.patch.object(customs_alert, "_HS_REF_PATHS",
                              (self.ref, self.dir / "hs_codes.csv")),
            mock.patch.object(customs_alert, "_HS_REF_MARKER", self.marker),
        ]
        for p in self._patches:
            p.start()
        os.environ["TRADE_OPERATOR_CHAT_ID"] = "555"

    def tearDown(self):
        for p in self._patches:
            p.stop()
        os.environ.pop("TRADE_OPERATOR_CHAT_ID", None)
        self.tmp.cleanup()

    def test_missing_file_returns_none(self):
        self.assertIsNone(customs_alert._check_hs_reference_freshness())

    def test_fresh_file_returns_none(self):
        self.ref.write_bytes(b"x")
        os.utime(self.ref, (time.time(), time.time()))
        self.assertIsNone(customs_alert._check_hs_reference_freshness())

    def test_stale_file_returns_alert(self):
        self.ref.write_bytes(b"x")
        old = time.time() - 200 * 86400  # 200 days old > 180-day threshold
        os.utime(self.ref, (old, old))
        msg = customs_alert._check_hs_reference_freshness()
        self.assertIsNotNone(msg)
        self.assertIn("개정", msg)
        self.assertIn("200", msg)
        self.assertIn("15049722", msg)

    def test_recent_marker_blocks_realert(self):
        self.ref.write_bytes(b"x")
        old = time.time() - 200 * 86400
        os.utime(self.ref, (old, old))
        self.marker.touch()  # alerted recently → suppressed
        self.assertIsNone(customs_alert._check_hs_reference_freshness())

    def test_old_marker_allows_realert(self):
        self.ref.write_bytes(b"x")
        old = time.time() - 200 * 86400
        os.utime(self.ref, (old, old))
        self.marker.touch()
        # Marker older than HS_REF_REALERT_DAYS → re-alert allowed
        far = time.time() - 60 * 86400
        os.utime(self.marker, (far, far))
        self.assertIsNotNone(customs_alert._check_hs_reference_freshness())

    def test_run_sends_freshness_then_touches_marker(self):
        self.ref.write_bytes(b"x")
        old = time.time() - 200 * 86400
        os.utime(self.ref, (old, old))
        c = _Collector(ok=True)
        with mock.patch.object(customs_alert.hs_map, "entries", return_value=[]):
            customs_alert.run(db_path=self.dir / "customs.db", notify=c)
        self.assertEqual(len(c.msgs), 1)
        self.assertIn("관세청 HS부호 개정", c.msgs[0][1])
        self.assertTrue(self.marker.exists())

    def test_run_does_not_touch_marker_on_send_failure(self):
        self.ref.write_bytes(b"x")
        old = time.time() - 200 * 86400
        os.utime(self.ref, (old, old))
        fail = _Collector(ok=False)
        with mock.patch.object(customs_alert.hs_map, "entries", return_value=[]):
            customs_alert.run(db_path=self.dir / "customs.db", notify=fail)
        self.assertFalse(self.marker.exists())  # retry next tick


if __name__ == "__main__":
    unittest.main()
