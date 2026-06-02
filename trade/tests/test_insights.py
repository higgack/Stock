"""trade.insights — fingerprint/state + refresh_signals 변동 게이트 테스트."""

import sqlite3
import unittest
from contextlib import contextmanager
from unittest import mock

from trade import insights


def _seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE industry_series (industry TEXT PRIMARY KEY, "
                 "months_json TEXT, imports_json TEXT, updated_ts REAL)")
    conn.execute("CREATE TABLE mti_series (mti6 TEXT PRIMARY KEY, "
                 "payload_json TEXT, import_json TEXT, updated_ts REAL)")
    return conn


def _put_industry(conn, months_json, ts):
    conn.execute("DELETE FROM industry_series")
    conn.execute("INSERT INTO industry_series VALUES (?,?,?,?)",
                 ("반도체", months_json, None, ts))
    conn.commit()


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.conn = _seed_conn()

    def tearDown(self):
        self.conn.close()

    def test_stable_across_identical_values(self):
        _put_industry(self.conn, '{"2026-04": 100}', 1.0)
        a = insights.data_fingerprint(self.conn)
        _put_industry(self.conn, '{"2026-04": 100}', 999.0)   # updated_ts만 다름
        self.assertEqual(a, insights.data_fingerprint(self.conn))

    def test_changes_when_value_changes(self):
        _put_industry(self.conn, '{"2026-04": 100}', 1.0)
        a = insights.data_fingerprint(self.conn)
        _put_industry(self.conn, '{"2026-04": 200}', 1.0)     # 값이 바뀜
        self.assertNotEqual(a, insights.data_fingerprint(self.conn))

    def test_missing_tables_dont_crash(self):
        bare = sqlite3.connect(":memory:")
        self.assertIsInstance(insights.data_fingerprint(bare), str)
        bare.close()

    def test_state_roundtrip(self):
        self.assertIsNone(insights.get_state(self.conn, "k"))
        insights.set_state(self.conn, "k", "v1")
        self.assertEqual(insights.get_state(self.conn, "k"), "v1")
        insights.set_state(self.conn, "k", "v2")          # upsert
        self.assertEqual(insights.get_state(self.conn, "k"), "v2")


class RefreshSignalsGateTests(unittest.TestCase):
    """refresh_signals.main: baseline-silent → no-change-silent → notify-on-change."""

    def setUp(self):
        self.conn = _seed_conn()
        _put_industry(self.conn, '{"2026-04": 100}', 1.0)

    def tearDown(self):
        self.conn.close()

    def _run(self, argv=None, cards=None):
        from trade.scripts import refresh_signals

        @contextmanager
        def fake_session(*a, **k):
            yield self.conn

        from trade import industry_archive
        with mock.patch.object(refresh_signals.customs, "session", fake_session), \
             mock.patch.object(refresh_signals.llm_insights, "generate",
                               return_value=cards or []) as gen, \
             mock.patch.object(industry_archive, "record_snapshot",
                               return_value="2026-04"), \
             mock.patch.object(industry_archive, "regenerate"), \
             mock.patch.object(refresh_signals, "_send_dm",
                               return_value=True) as send:
            refresh_signals.main(argv or [])
        self._gen = gen
        return send

    def test_first_run_is_baseline_silent(self):
        send = self._run()
        send.assert_not_called()                       # 최초 = baseline, 무음
        self.assertIsNotNone(insights.get_state(self.conn, "data_fp"))

    def test_no_change_is_silent(self):
        self._run()                                    # baseline 기록
        send = self._run()                             # 동일 데이터
        send.assert_not_called()

    def test_change_notifies_once_and_advances_fp(self):
        self._run()                                    # baseline
        _put_industry(self.conn, '{"2026-04": 200}', 1.0)   # 데이터 변동
        before = insights.get_state(self.conn, "data_fp")
        send = self._run()
        send.assert_called_once()                       # 변동 시 DM 1회
        self.assertNotEqual(before, insights.get_state(self.conn, "data_fp"))
        # 다시 돌리면 또 안 옴(fingerprint 전진됨)
        send2 = self._run()
        send2.assert_not_called()

    def test_force_runs_even_when_unchanged(self):
        self._run()                                    # baseline 기록
        send = self._run()                             # 동일 데이터 → 평소엔 무음
        send.assert_not_called()
        # 캐시 없을 때 --force는 1회 생성 + DM(프리뷰)
        send_f = self._run(["--force"], cards=[{"title": "t", "body": "b"}])
        self._gen.assert_called_once()
        send_f.assert_called_once()

    def test_force_reuses_cached_cards_no_api(self):
        self._run()                                    # baseline
        # 저장된 카드가 있으면 --force는 LLM 미호출(재사용·0 콜)
        insights.set_state(self.conn, "llm_insight_cards",
                           '[{"title": "t", "body": "b"}]')
        send = self._run(["--force"])
        self._gen.assert_not_called()                  # 재사용 — API 0콜
        send.assert_called_once()                      # 아카이브·DM은 갱신

    def test_force_reuses_even_when_data_changed(self):
        self._run()                                    # baseline (fp=D1)
        # 데이터가 바뀌어도(+카드 캐시 존재) --force 단독은 재사용 → 0콜
        _put_industry(self.conn, '{"2026-04": 999}', 1.0)
        insights.set_state(self.conn, "llm_insight_cards",
                           '[{"title": "t", "body": "b"}]')
        send = self._run(["--force"])
        self._gen.assert_not_called()                  # 변동에도 재사용(0콜)
        send.assert_called_once()

    def test_regen_llm_forces_api_even_with_cache(self):
        self._run()                                    # baseline
        insights.set_state(self.conn, "llm_insight_cards",
                           '[{"title": "old", "body": "b"}]')
        self._run(["--force", "--regen-llm"], cards=[{"title": "new", "body": "x"}])
        self._gen.assert_called_once()                 # 명시적 재생성 — 새 콜


class DashboardInsightBoxTests(unittest.TestCase):
    """대시보드 산업트렌드 로더가 저장된 LLM 카드로 🔍 박스를 렌더하는지."""

    def test_box_rendered_from_stored_cards(self):
        import json as _json
        import tempfile
        from pathlib import Path

        from trade import customs, dashboard, industry

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "customs.db"
        months = {}
        for i in range(13):
            y = 2025 + (i // 12); mo = (i % 12) + 1
            months[f"{y}-{mo:02d}"] = 11_000_000_000
        months["2026-04"] = 31_000_000_000
        with customs.session(db) as conn:
            industry.store(conn, {"반도체": months})
            insights.set_state(conn, "llm_insight_cards",
                               _json.dumps([{"title": "AI 인프라", "body": "수요 확대"}],
                                           ensure_ascii=False))
        html = dashboard._load_industry_html(db)
        self.assertIn("ins-box", html)            # 🔍 박스 존재
        self.assertIn("AI 인프라", html)           # 카드 내용
        self.assertIn("반도체", html)              # 산업 카드도 함께
        self.assertIn("industry_archive.html", html)   # 🗄 월별 아카이브 링크


if __name__ == "__main__":
    unittest.main()
