"""collect_stock_snapshot 단기 캐시 회귀 (사용자 2026-06-29 'cold 상세 느림').

cold 상세 1장이 스냅샷을 2~3회(core·full·quote) 중복 수집하던 것 → 120초 캐시로
중복 제거. ⚠️ 과거 실수(2026-06-15 공유 스냅샷 동시 변경 race)를 피하려고 copy-on
-read/write — 호출부가 반환 dict 를 in-place 변경해도 캐시 원본이 오염되면 안 된다."""
import unittest
from unittest import mock

import bot.stock_snapshot as ss


class TestSnapshotCache(unittest.TestCase):
    def setUp(self):
        with ss._SNAP_CACHE_LOCK:
            ss._SNAP_CACHE.clear()
        self.calls = []

        def _fake(ticker):
            self.calls.append(ticker)
            # nested 구조 — deepcopy 격리 검증용
            return {"ticker": ticker, "kr": {"research_reports": []}}

        self.patch = mock.patch.object(ss, "_collect_stock_snapshot_uncached",
                                       side_effect=_fake)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        with ss._SNAP_CACHE_LOCK:
            ss._SNAP_CACHE.clear()

    def test_second_call_uses_cache(self):
        a = ss.collect_stock_snapshot("AAPL")
        b = ss.collect_stock_snapshot("AAPL")
        self.assertEqual(self.calls, ["AAPL"])      # 본체 1회만(2번째는 캐시)
        self.assertEqual(a, b)

    def test_copy_on_read_isolation(self):
        # 반환 dict 를 in-place 변경(_ensure_detail_enrichment 처럼) → 다음 캐시
        # 읽기는 오염되면 안 됨(과거 race 의 근본 원인 차단).
        a = ss.collect_stock_snapshot("AAPL")
        a["kr"]["research_reports"].append("MUTATED")
        a["injected"] = True
        b = ss.collect_stock_snapshot("AAPL")
        self.assertEqual(b["kr"]["research_reports"], [])   # 원본 보존
        self.assertNotIn("injected", b)

    def test_use_cache_false_bypasses_read_but_writes(self):
        # use_cache=False 는 캐시 '읽기'만 우회(매번 신선 수집)…
        ss.collect_stock_snapshot("AAPL", use_cache=False)
        ss.collect_stock_snapshot("AAPL", use_cache=False)
        self.assertEqual(self.calls, ["AAPL", "AAPL"])      # 둘 다 본체 호출
        # …하지만 신선 결과는 캐시에 갱신(finding #1) → 이후 일반 읽기는 적중
        ss.collect_stock_snapshot("AAPL")
        self.assertEqual(self.calls, ["AAPL", "AAPL"])      # 본체 추가 호출 없음(캐시 적중)

    def test_ttl_expiry_recomputes(self):
        ss.collect_stock_snapshot("AAPL")
        # 저장 타임스탬프를 TTL 너머로 후퇴시켜 만료 시뮬레이션
        with ss._SNAP_CACHE_LOCK:
            ts, snap = ss._SNAP_CACHE["AAPL"]
            ss._SNAP_CACHE["AAPL"] = (ts - ss._SNAP_CACHE_TTL - 1, snap)
        ss.collect_stock_snapshot("AAPL")
        self.assertEqual(self.calls, ["AAPL", "AAPL"])      # 만료 → 재수집

    def test_expired_entries_pruned_on_write(self):
        # 장수 프로세스 무한 증가 방지 — 쓰기 시 만료 항목 제거.
        ss.collect_stock_snapshot("OLD")
        with ss._SNAP_CACHE_LOCK:
            ts, snap = ss._SNAP_CACHE["OLD"]
            ss._SNAP_CACHE["OLD"] = (ts - ss._SNAP_CACHE_TTL - 1, snap)  # 만료
        ss.collect_stock_snapshot("NEW")           # 쓰기 → OLD prune
        self.assertNotIn("OLD", ss._SNAP_CACHE)
        self.assertIn("NEW", ss._SNAP_CACHE)

    def test_none_result_not_cached(self):
        self.patch.stop()
        with mock.patch.object(ss, "_collect_stock_snapshot_uncached",
                               return_value=None):
            self.assertIsNone(ss.collect_stock_snapshot("ZZZZ"))
            self.assertNotIn("ZZZZ", ss._SNAP_CACHE)        # None 은 캐시 안 함
        self.patch.start()


if __name__ == "__main__":
    unittest.main()
