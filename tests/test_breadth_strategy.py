"""Breadth 4구간 전략 회귀 (2026-08-16 사용자 캡처 전략).

이 파일의 **핵심은 `CaptureHistoryTests`** 다 — 사용자가 준 캡처의 히스토리
99~103행은 원 전략이 실제로 산출한 관측치이고, 그걸 그대로 픽스처로 넣어
`decide()` 가 같은 상태·비중을 내는지 대조한다. 내가 스펙을 옳게 읽었는지
확인하는 유일한 방법이라 이 테스트가 깨지면 전략 해석이 틀린 것이다.
"""

import unittest

from bot import breadth_strategy as bs


class CaptureHistoryTests(unittest.TestCase):
    """캡처 히스토리 표(99~103행) = 원 전략의 관측된 정답."""

    # (행, Breadth%, 지수 252일 DD%, 상태, 지수비중%, 최종비중%, 현금%)
    ROWS = [
        (103, 38.46, -23.44, "RECOVERY_LEADER_PULLBACK", 0, 50, 50),
        (102, 11.54, -27.64, "CONTRARIAN_KOSPI", 100, 100, 0),
        (101, 23.08, -7.00, "CASH", 0, 0, 100),
        (100, 73.08, 0.00, "TREND_RS_TOP3", 0, 100, 0),
        (99, 80.77, -1.38, "TREND_RS_TOP3", 0, 100, 0),
    ]
    # 캡처 당시 상황 재현 — 회복 후보 1개(반도체), RS 상위 3개.
    POOL = [{"name": "반도체", "close": 110.0, "ma120": 100.0,
             "rs_6m": 5.0, "pullback_pct": -8.0}]
    RS = [{"name": "IT하드웨어", "rs": 12.0}, {"name": "반도체", "rs": 9.0},
          {"name": "은행", "rs": 4.0}]

    def test_matches_observed_history(self):
        for row, b, dd, state, idx_w, tot_w, cash_w in self.ROWS:
            d = bs.decide(b, dd, recovery_pool=self.POOL, rs_ranked=self.RS)
            self.assertEqual(d["state"], state, f"{row}행 상태")
            self.assertEqual(round(d["index_w"] * 100), idx_w, f"{row}행 지수비중")
            self.assertEqual(round(d["total_w"] * 100), tot_w, f"{row}행 최종비중")
            self.assertEqual(round(d["cash_w"] * 100), cash_w, f"{row}행 현금")

    def test_cash_is_not_a_separate_regime(self):
        # 101행이 증거 — 역추세 구간인데 DD 가 얕아 트랜치 0 → CASH.
        d = bs.decide(23.08, -7.00, rs_ranked=self.RS)
        self.assertEqual(d["regime"], "CONTRARIAN", "구간은 여전히 역추세")
        self.assertEqual(d["state"], "CASH")
        self.assertEqual(d["cash_w"], 1.0)

    def test_weights_always_sum_to_one(self):
        for row, b, dd, *_ in self.ROWS:
            d = bs.decide(b, dd, recovery_pool=self.POOL, rs_ranked=self.RS)
            total = sum(t["weight"] for t in d["targets"]) + d["cash_w"]
            self.assertAlmostEqual(total, 1.0, places=3, msg=f"{row}행 합계")


class RegimeBoundaryTests(unittest.TestCase):
    def test_thresholds_are_inclusive_lower(self):
        cases = [(29.99, "CONTRARIAN"), (30.0, "RECOVERY"), (39.99, "RECOVERY"),
                 (40.0, "NON_TREND"), (59.99, "NON_TREND"), (60.0, "TREND"),
                 (0.0, "CONTRARIAN"), (100.0, "TREND")]
        for v, want in cases:
            self.assertEqual(bs.classify_regime(v), want, v)
        self.assertIsNone(bs.classify_regime(None))

    def test_index_tranche_boundaries(self):
        cases = [(-11.99, 0.0), (-12.0, 0.5), (-17.99, 0.5), (-18.0, 0.75),
                 (-23.99, 0.75), (-24.0, 1.0), (-50.0, 1.0), (0.0, 0.0),
                 (5.0, 0.0), (None, 0.0)]
        for dd, want in cases:
            self.assertEqual(bs.index_tranche(dd), want, dd)

    def test_rs_weight_is_a_share_quartile(self):
        # 강도 = 지수 상회 섹터 **비율**의 4분위. 캡처의 25/50/75/100 4단계가
        # 전부 도달 가능해야 하고(3에서 100%로 뛰면 75%가 영영 안 나옴),
        # 개수로 세면 표본이 클수록 쉽게 100% → 비추세가 추세로 붕괴한다
        # (2026-08-16 독립 리뷰 2회).
        cases = [(0, 13, 0.0), (1, 13, 0.25), (3, 13, 0.25), (4, 13, 0.50),
                 (6, 13, 0.50), (7, 13, 0.75), (9, 13, 0.75), (10, 13, 1.0),
                 (13, 13, 1.0), (1, 4, 0.25), (2, 4, 0.50), (3, 4, 0.75),
                 (4, 4, 1.0)]
        for n, total, want in cases:
            self.assertEqual(bs.rs_weight(n, total), want, f"{n}/{total}")

    def test_rs_weight_is_scale_free(self):
        # 표본 11(US)·13(KR)에서 같은 비율이면 같은 비중 — universal 요건.
        self.assertEqual(bs.rs_weight(6, 11), bs.rs_weight(7, 13))

    def test_rs_weight_degenerate_input(self):
        self.assertEqual(bs.rs_weight(0, 0), 0.0)
        self.assertEqual(bs.rs_weight(3, 0), 0.0)

    def test_non_trend_never_collapses_into_trend(self):
        # 비추세에서 100% 가 나오려면 지수 상회 비율이 75% 를 넘어야 한다 —
        # 그 정도면 breadth 도 추세권이라 실질적으로 안 겹친다.
        rs = [{"name": f"s{i}", "rs": 1.0 if i < 4 else -1.0} for i in range(13)]
        d = bs.decide(45.0, -5.0, rs_ranked=rs)
        self.assertLess(d["total_w"], 1.0, "비추세가 추세와 같은 100% 로 붕괴")
        self.assertGreater(d["cash_w"], 0.0)


class RecoveryCandidateTests(unittest.TestCase):
    BASE = {"name": "x", "close": 110.0, "ma120": 100.0, "rs_6m": 5.0,
            "pullback_pct": -8.0}

    def test_all_three_conditions_required(self):
        self.assertTrue(bs.is_recovery_candidate(self.BASE))
        self.assertFalse(bs.is_recovery_candidate({**self.BASE, "close": 99.0}))
        self.assertFalse(bs.is_recovery_candidate({**self.BASE, "rs_6m": -0.1}))
        # 놀림 밴드 밖 — 덜 빠졌거나(-4.9) 너무 빠졌거나(-15.1)
        self.assertFalse(bs.is_recovery_candidate({**self.BASE, "pullback_pct": -4.9}))
        self.assertFalse(bs.is_recovery_candidate({**self.BASE, "pullback_pct": -15.1}))
        # 경계는 포함
        self.assertTrue(bs.is_recovery_candidate({**self.BASE, "pullback_pct": -5.0}))
        self.assertTrue(bs.is_recovery_candidate({**self.BASE, "pullback_pct": -15.0}))

    def test_missing_value_is_not_a_pass(self):
        # 모르는 걸 통과시키면 근거 없는 매수가 된다.
        for k in ("close", "ma120", "rs_6m", "pullback_pct"):
            self.assertFalse(bs.is_recovery_candidate({**self.BASE, k: None}), k)

    def test_recovery_caps_at_three_and_splits_fifty(self):
        pool = [{**self.BASE, "name": f"s{i}"} for i in range(5)]
        d = bs.decide(35.0, -20.0, recovery_pool=pool)
        self.assertEqual(len(d["targets"]), 3, "후보 상한 3")
        self.assertAlmostEqual(d["total_w"], 0.50)
        self.assertAlmostEqual(sum(t["weight"] for t in d["targets"]), 0.50, places=3)

    def test_recovery_with_no_qualifier_is_cash(self):
        pool = [{**self.BASE, "pullback_pct": -30.0}]
        d = bs.decide(35.0, -20.0, recovery_pool=pool)
        self.assertEqual(d["state"], "CASH")
        self.assertEqual(d["cash_w"], 1.0)


class NonTrendTests(unittest.TestCase):
    def test_negative_rs_is_not_counted_as_strong(self):
        rs = [{"name": "a", "rs": 5.0}, {"name": "b", "rs": -1.0},
              {"name": "c", "rs": -3.0}]
        d = bs.decide(45.0, -5.0, rs_ranked=rs)
        self.assertEqual(d["state"], "NON_TREND_RS")
        self.assertEqual([t["name"] for t in d["targets"]], ["a"], "약한 섹터 매수")
        self.assertAlmostEqual(d["total_w"], 0.50)   # 1/3 = 33% → 2분위
        self.assertAlmostEqual(d["cash_w"], 0.50)

    def test_strength_counts_the_whole_ranking_not_just_the_top3(self):
        # 상위 3개만 세면 4분위가 영원히 안 나온다 — 매수는 3개로 제한하되
        # 강도는 시장 전체로 잰다.
        rs = [{"name": f"s{i}", "rs": 1.0} for i in range(12)]
        d = bs.decide(45.0, -5.0, rs_ranked=rs)
        self.assertEqual(d["total_w"], 1.0, "12/12 상회인데 4분위가 아니다")
        self.assertEqual(len(d["targets"]), 3, "매수는 여전히 상위 3개")

    def test_all_negative_rs_is_cash(self):
        rs = [{"name": "a", "rs": -1.0}, {"name": "b", "rs": -2.0}]
        d = bs.decide(45.0, -5.0, rs_ranked=rs)
        self.assertEqual(d["state"], "CASH")


class TrendTests(unittest.TestCase):
    def test_only_index_beating_sectors_are_bought(self):
        # 벤치마크 조회 실패(rs=None)나 열위(rs<0) 섹터에 100% 를 배분하면
        # 근거 0인 매수가 된다 — breadth 는 계산되는데 rs 만 없는 조합이
        # 실제로 가능하다(2026-08-16 독립 리뷰).
        rs = [{"name": "a", "rs": None}, {"name": "b", "rs": -2.0},
              {"name": "c", "rs": 3.0}]
        d = bs.decide(70.0, -1.0, rs_ranked=rs)
        self.assertEqual([t["name"] for t in d["targets"]], ["c"])
        self.assertEqual(d["total_w"], 1.0)

    def test_all_rs_missing_is_cash_not_a_blind_full_allocation(self):
        rs = [{"name": "a", "rs": None}, {"name": "b", "rs": None}]
        d = bs.decide(70.0, -1.0, rs_ranked=rs)
        self.assertEqual(d["state"], "CASH")
        self.assertEqual(d["cash_w"], 1.0)
        self.assertEqual(d["targets"], [])


class IndicatorTests(unittest.TestCase):
    def test_breadth_above_ma_excludes_short_series_visibly(self):
        long_up = [100.0] * 119 + [200.0]        # MA120 위
        long_down = [200.0] * 119 + [100.0]      # MA120 아래
        short = [100.0] * 10                     # 계산 불가
        r = bs.breadth_above_ma({"a": long_up, "b": long_down, "c": short}, 120)
        self.assertEqual((r["above"], r["counted"]), (1, 2))
        self.assertEqual(r["pct"], 50.0)
        self.assertEqual(r["skipped"], ["c"], "분모에서 빠진 걸 밝혀야")

    def test_drawdown_and_pullback(self):
        closes = [100.0] * 10 + [200.0] + [150.0]
        self.assertEqual(bs.drawdown_pct(closes, 252), -25.0)
        self.assertEqual(bs.pullback_from_high(closes, 20), -25.0)
        self.assertIsNone(bs.drawdown_pct([], 252))

    def test_relative_strength_is_difference_vs_bench(self):
        closes = [100.0] * 130 + [120.0]      # +20% over 126
        bench = [100.0] * 130 + [110.0]       # +10%
        rs = bs.relative_strength(closes, bench, 126)
        self.assertAlmostEqual(rs, 10.0, places=1)
        self.assertIsNone(bs.relative_strength([1.0, 2.0], bench, 126))

    def test_ma_needs_full_period(self):
        self.assertIsNone(bs.ma([1.0] * 119, 120))
        self.assertEqual(bs.ma([2.0] * 120, 120), 2.0)


class SignalLogTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig = bs._SIGNAL_DIR
        bs._SIGNAL_DIR = Path(self.tmp.name)
        self.addCleanup(lambda: setattr(bs, "_SIGNAL_DIR", self._orig))

    def test_append_is_idempotent_per_month(self):
        # 6시간 주기로 도는 잡이라 같은 월말에 여러 번 호출된다.
        rec = {"month": "2026-08", "state": "TREND_RS_TOP3", "breadth_pct": 73.0}
        self.assertTrue(bs.append_signal("KR", rec))
        self.assertFalse(bs.append_signal("KR", rec), "같은 월 중복 기록")
        self.assertEqual(len(bs.load_signals("KR")), 1)
        self.assertTrue(bs.append_signal("KR", {**rec, "month": "2026-09"}))
        self.assertEqual(len(bs.load_signals("KR")), 2)

    def test_markets_are_separate_files(self):
        bs.append_signal("KR", {"month": "2026-08"})
        self.assertEqual(len(bs.load_signals("KR")), 1)
        self.assertEqual(len(bs.load_signals("US")), 0)

    def test_month_without_key_is_rejected(self):
        self.assertFalse(bs.append_signal("KR", {"state": "TREND_RS_TOP3"}))

    def test_corrupt_line_is_skipped_not_fatal(self):
        bs.append_signal("KR", {"month": "2026-08"})
        with bs.signal_path("KR").open("a", encoding="utf-8") as fh:
            fh.write("{깨진 줄\n")
        self.assertEqual(len(bs.load_signals("KR")), 1)


class MonthEndTests(unittest.TestCase):
    """`completed_month_ends` = '월말 종가 신호' 의 정의."""

    def test_last_bar_of_each_completed_month(self):
        dates = ["2026-06-29", "2026-06-30", "2026-07-30", "2026-07-31",
                 "2026-08-03", "2026-08-04"]
        self.assertEqual(bs.completed_month_ends(dates),
                         ["2026-06-30", "2026-07-31"])

    def test_current_month_is_excluded(self):
        # 진행 중인 달은 '종가' 가 없다 — 부분 달을 확정으로 박으면 멱등성
        # 때문에 진짜 월말값이 영원히 무시된다.
        self.assertNotIn("2026-08-04",
                         bs.completed_month_ends(["2026-07-31", "2026-08-04"]))

    def test_holiday_on_calendar_month_end_still_resolves(self):
        # 7/31 이 휴장이라 봉이 없어도 '그 달 마지막 봉' 은 정의상 하나다.
        dates = ["2026-07-29", "2026-07-30", "2026-08-03"]
        self.assertEqual(bs.completed_month_ends(dates), ["2026-07-30"])

    def test_all_same_month_has_no_completed_month(self):
        self.assertEqual(bs.completed_month_ends(
            ["2026-08-03", "2026-08-04", "2026-08-05"]), [])

    def test_limit_keeps_the_most_recent_months(self):
        dates = [f"2026-{m:02d}-15" for m in range(1, 13)]
        self.assertEqual(bs.completed_month_ends(dates, limit=3),
                         ["2026-09-15", "2026-10-15", "2026-11-15"])

    def test_short_or_malformed_input(self):
        self.assertEqual(bs.completed_month_ends([]), [])
        self.assertEqual(bs.completed_month_ends(["2026-08-14"]), [])
        # 값이 이상해도 예외 없이 빈 목록 — 판정이 죽지 않는다.
        self.assertEqual(bs.completed_month_ends(["", None]), [])

    def test_confirmed_snapshot_is_cut_at_that_date(self):
        """확정분은 닫힌 달까지 잘라 계산 → 장중에 돌려도 값이 안 변한다."""
        rows = [{"date": d, "close": c} for d, c in
                zip(["2026-07-30", "2026-07-31", "2026-08-03"], [100, 110, 999])]
        conf = bs._assemble("KR", {"a": "반도체"}, "KOSPI", rows,
                            {"반도체": rows}, [], cut="2026-07-31")
        self.assertEqual(conf["asof"], "2026-07-31")
        self.assertTrue(conf["is_confirmed"])
        live = bs._assemble("KR", {"a": "반도체"}, "KOSPI", rows,
                            {"반도체": rows}, [], cut=None)
        self.assertEqual(live["asof"], "2026-08-03")
        self.assertFalse(live["is_confirmed"])

    def test_cut_is_by_date_so_uneven_series_align(self):
        """섹터마다 시계열 길이가 다르다(네이버 폴백 여부) — 공통 인덱스로
        자르면 섹터마다 다른 날에서 잘려 확정값이 오염된다(독립 리뷰)."""
        long_ = [{"date": f"2026-05-{d:02d}", "close": 100.0} for d in range(1, 29)]
        long_ += [{"date": "2026-07-31", "close": 100.0},
                  {"date": "2026-08-03", "close": 500.0}]
        short = [{"date": "2026-07-31", "close": 100.0},
                 {"date": "2026-08-03", "close": 500.0}]
        cut = "2026-07-31"
        self.assertEqual(bs._cut_rows(long_, cut)[-1]["date"], cut)
        self.assertEqual(bs._cut_rows(short, cut)[-1]["date"], cut)
        # 8/3 의 급등(500)이 확정 스냅샷에 새어들면 안 된다.
        for rows in (long_, short):
            self.assertNotIn(500.0, [r["close"] for r in bs._cut_rows(rows, cut)])

    def test_fng_is_not_stamped_onto_a_past_month(self):
        # F&G 는 현재값만 있는 지표 — 확정분에 오늘 값을 박으면 거짓 기록.
        from bot import fear_greed_client as fg
        orig = fg.fetch_fear_greed
        fg.fetch_fear_greed = lambda: {"score": 29.0, "rating_kr": "공포"}
        self.addCleanup(lambda: setattr(fg, "fetch_fear_greed", orig))
        rows = [{"date": "2026-07-31", "close": 100.0},
                {"date": "2026-08-03", "close": 110.0}]
        conf = bs._assemble("KR", {"a": "반도체"}, "KOSPI", rows,
                            {"반도체": rows}, [], cut="2026-07-31")
        self.assertIsNone(conf["fng"]["index"])
        live = bs._assemble("KR", {"a": "반도체"}, "KOSPI", rows,
                            {"반도체": rows}, [], cut=None)
        self.assertEqual(live["fng"]["index"], 29.0)
        self.assertNotIn("fng", bs._signal_record(conf))


class BuildAllTests(unittest.TestCase):
    """build_all = 화면(중간점검) + 이력(확정) 두 산출물을 한 번에 만든다."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig = bs._SIGNAL_DIR
        bs._SIGNAL_DIR = Path(self.tmp.name)
        self.addCleanup(lambda: setattr(bs, "_SIGNAL_DIR", self._orig))
        self.calls: list[str] = []

        # 2026-08-04 로 끝나는 600 영업일(≈2.4년) — 백필 상한 12개월 전부가
        # MA120·RS126 을 만들 만큼 길어야 판정이 결정적이다.
        from datetime import date, timedelta
        days_: list[str] = []
        d = date(2026, 8, 4)
        while len(days_) < 600:
            if d.weekday() < 5:
                days_.append(d.isoformat())
            d -= timedelta(days=1)
        days_.reverse()
        self.dates = days_

        def rows(kick):
            px, out = 100.0, []
            for dt in days_:
                px *= 1.0 + kick
                out.append({"date": dt, "close": round(px, 4)})
            return out

        # 벤치마크보다 빠르게 오르는 섹터가 RS 양(+) — 티커별로 결정적 분산.
        strengths = {"^KS11": 0.0010, "^GSPC": 0.0010}

        def fake_series(ticker, days=400):
            self.calls.append(ticker)
            kick = strengths.get(ticker)
            if kick is None:
                kick = 0.0010 + (sum(map(ord, ticker)) % 5) * 0.0002
            return rows(kick)

        self._os = bs._series
        bs._series = fake_series
        self.addCleanup(lambda: setattr(bs, "_series", self._os))

        from bot import fear_greed_client as fg
        self._ofg = fg.fetch_fear_greed
        fg.fetch_fear_greed = lambda: {"score": 29.19, "rating_kr": "공포"}
        self.addCleanup(lambda: setattr(fg, "fetch_fear_greed", self._ofg))

    def test_backfills_confirmed_months_with_top3_and_is_idempotent(self):
        first = bs.build_all()
        self.assertEqual(set(first), {"KR", "US"})
        self.assertFalse(first["KR"]["is_confirmed"], "화면 값은 중간점검")
        self.assertEqual(first["KR"]["asof"], "2026-08-04")

        recs = bs.load_signals("KR")
        # 신규 배포 시 이력이 통째로 비어 있으므로 상한만큼 백필된다 —
        # 회복 후보 풀이 첫날부터 실제 Top3 이력을 갖게 하는 게 목적.
        self.assertEqual(len(recs), bs._BACKFILL_MONTHS)
        months = [r["month"] for r in recs]
        self.assertEqual(months, sorted(months), "오래된→최신 순서")
        self.assertEqual(months[-1], "2026-07", "진행 중인 8월은 확정 아님")
        self.assertTrue(recs[-1]["asof"].startswith("2026-07-3"),
                        f"7월 마지막 봉이어야 함: {recs[-1]['asof']}")
        for r in recs:
            self.assertTrue(r["top3"], f"{r['month']} top3 비었음(회복 풀 원천)")
            self.assertLessEqual(len(r["top3"]), 3)

        bs.build_all()          # 6시간 뒤 재실행
        self.assertEqual(len(bs.load_signals("KR")), bs._BACKFILL_MONTHS,
                         "월 1건 멱등")

    def test_confirmed_value_does_not_move_intraday(self):
        """오늘 봉이 추가돼도 이미 확정된 달의 값은 그대로여야 한다."""
        bs.build_all()
        before = {r["month"]: r["breadth_pct"] for r in bs.load_signals("KR")}
        # 장중 급등봉이 하나 더 붙은 상황
        base = bs._series

        def with_extra(ticker, days=400):
            rows = list(base(ticker, days))
            rows.append({"date": "2026-08-05", "close": rows[-1]["close"] * 2})
            return rows

        bs._series = with_extra
        self.addCleanup(lambda: setattr(bs, "_series", base))
        bs.build_all()
        after = {r["month"]: r["breadth_pct"] for r in bs.load_signals("KR")}
        self.assertEqual(before, after, "확정 이력이 장중 봉에 오염됨")

    def test_series_fetched_once_per_ticker(self):
        # 중간점검·확정이 같은 한 벌을 잘라 쓴다(티커당 1회).
        bs.build_all()
        self.assertEqual(len(self.calls), len(set(self.calls)))

    def test_recovery_pool_prefers_recorded_leaders_over_current_rs(self):
        # 지금 RS 꼴찌라도 과거 리더면 후보 — '놀림목' 의 정의.
        bs.append_signal("KR", {"month": "2026-06", "top3": ["철강"]})
        ranked = [{"name": "반도체", "rs": 30.0}, {"name": "IT", "rs": 20.0},
                  {"name": "철강", "rs": -9.0}]
        self.assertEqual([m["name"] for m in bs._recovery_pool("KR", ranked)],
                         ["철강"])

    def test_recovery_pool_bootstraps_when_log_empty(self):
        ranked = [{"name": f"S{i}", "rs": float(10 - i)} for i in range(8)]
        pool = bs._recovery_pool("KR", ranked)
        self.assertEqual([m["name"] for m in pool],
                         ["S0", "S1", "S2", "S3", "S4", "S5"])

    def test_recovery_pool_orders_recent_months_first(self):
        # decide 가 3개로 자르므로 6개월 전 리더가 지난달 리더를 밀어내면
        # 안 된다(2026-08-16 독립 리뷰).
        for i, name in enumerate(["A", "B", "C", "D"], start=1):
            bs.append_signal("KR", {"month": f"2026-0{i}", "top3": [name]})
        ranked = [{"name": n, "rs": 1.0} for n in ("A", "B", "C", "D")]
        self.assertEqual([m["name"] for m in bs._recovery_pool("KR", ranked)],
                         ["D", "C", "B", "A"])

    def test_recovery_pool_ignores_future_months_when_backfilling(self):
        # 과거 달을 계산할 땐 그 이후 달의 Top3 를 보면 미래 정보 유입이다.
        bs.append_signal("KR", {"month": "2026-01", "top3": ["과거"]})
        bs.append_signal("KR", {"month": "2026-05", "top3": ["미래"]})
        ranked = [{"name": "과거", "rs": 1.0}, {"name": "미래", "rs": 2.0}]
        pool = bs._recovery_pool("KR", ranked, before="2026-03")
        self.assertEqual([m["name"] for m in pool], ["과거"])

    def test_one_market_failure_does_not_kill_the_other(self):
        orig = bs.build_with_signals

        def flaky(market):
            if market == "KR":
                raise RuntimeError("네이버 down")
            return orig(market)

        bs.build_with_signals = flaky
        self.addCleanup(lambda: setattr(bs, "build_with_signals", orig))
        out = bs.build_all()
        self.assertEqual(set(out), {"US"})

    def test_missing_month_is_backfilled_not_lost(self):
        # 6시간 잡이 한 달 내내 실패해도 그 달 신호가 유실되면 안 된다.
        bs.build_all()
        recs = bs.load_signals("KR")
        gone = recs[3]["month"]
        keep = [r for r in recs if r["month"] != gone]
        bs.signal_path("KR").write_text(
            "".join(__import__("json").dumps(r, ensure_ascii=False) + "\n"
                    for r in keep), encoding="utf-8")
        bs.build_all()
        after = [r["month"] for r in bs.load_signals("KR")]
        self.assertIn(gone, after, "빠진 달이 복구되지 않았다")
        self.assertEqual(len(after), len(set(after)), "중복 기록")
        # 백필분은 파일 끝에 append 되므로 load_signals 가 월로 정렬해야
        # '최근 6개월' 후보 풀과 화면 이력이 뒤섞이지 않는다.
        self.assertEqual(after, sorted(after), "이력이 시간순이 아니다")


class RenderTests(unittest.TestCase):
    @staticmethod
    def _snap(mkt, b, state, regime, total, cash, idx=0.0, missing=None):
        return {"market": mkt, "regime": regime, "state": state,
                "targets": [{"name": "반도체", "weight": total}],
                "index_w": idx, "total_w": total, "cash_w": cash,
                "breadth_pct": b, "dd_pct": -23.44, "bench_name": "KOSPI",
                "breadth": {"pct": b, "above": 5, "counted": 13,
                            "skipped": [], "period": 120},
                "source_label": "KODEX 섹터 ETF",
                "sectors_missing": missing or [],
                "rs_ranked": [{"name": "반도체", "rs": 12.3}],
                "fng": {"index": 29.19, "label": "Fear"},
                "asof": "2026-08-14", "is_confirmed": False,
                "resolution_note": "표본 13개 — 1개 = 7.7%p"}

    def test_renders_both_markets_with_current_regime_highlighted(self):
        data = {"KR": self._snap("KR", 38.46, "RECOVERY_LEADER_PULLBACK",
                                 "RECOVERY", 0.5, 0.5),
                "US": self._snap("US", 73.08, "TREND_RS_TOP3", "TREND", 1.0, 0.0)}
        html = bs.render_page(data)
        self.assertIn("🧭 KR", html)
        self.assertIn("🧭 US", html)
        self.assertEqual(html.count("class='on'"), 2, "시장마다 현재 구간 1개 강조")
        self.assertIn("38.46%", html)
        # 4구간 설명이 전부 보여야(전략을 화면만 보고 이해할 수 있게)
        for label in ("역추세 구간", "회복 구간", "비추세 구간", "추세 구간"):
            self.assertIn(label, html)

    def test_intraday_badge_distinguishes_from_confirmed(self):
        # 월중 값을 확정 신호로 오인하면 매매가 어긋난다.
        d = self._snap("KR", 38.46, "RECOVERY_LEADER_PULLBACK", "RECOVERY", .5, .5)
        self.assertIn("중간점검", bs.render_page({"KR": d}))
        self.assertIn("월말 종가 확정",
                      bs.render_page({"KR": {**d, "is_confirmed": True}}))

    def test_missing_sectors_are_named(self):
        d = self._snap("KR", 38.46, "RECOVERY_LEADER_PULLBACK", "RECOVERY",
                       .5, .5, missing=["철강", "보험"])
        self.assertIn("철강·보험", bs.render_page({"KR": d}))

    def test_fng_is_shown_but_documented_as_non_gating(self):
        html = bs.render_page({"KR": self._snap("KR", 38.46,
                                                "RECOVERY_LEADER_PULLBACK",
                                                "RECOVERY", .5, .5)})
        self.assertIn("29", html)
        self.assertIn("판정에는 쓰지 않습니다", html)

    def test_empty_data_does_not_crash(self):
        html = bs.render_page({})
        self.assertIn("데이터를 받지 못했습니다", html)
        self.assertNotIn("{", html.split("<style>")[0])   # 미치환 포맷 없음

    def test_cross_market_comparison_warning_present(self):
        # 표본이 달라 KR 38% 와 US 38% 가 같은 의미가 아니다.
        html = bs.render_page({"KR": self._snap("KR", 38.46, "CASH",
                                                "RECOVERY", 0.0, 1.0)})
        self.assertIn("직접 비교하지 마세요", html)


class WiringTests(unittest.TestCase):
    def test_page_registered_in_navs_and_scheduler(self):
        nav = open("bot/fred_boards.py", encoding="utf-8").read()
        self.assertIn("breadth_strategy.html", nav, "보드 공용 nav 미등록")
        home = open("bot/dashboard.py", encoding="utf-8").read()
        self.assertGreaterEqual(home.count("breadth_strategy.html"), 2,
                                "홈(market.html) nav 미등록")
        bot = open("bot/telegram_bot.py", encoding="utf-8").read()
        # 주기(6시간) + startup 두 곳 모두. startup 이 없으면 배포 직후
        # 최소 6시간 nav 링크가 404 다(실수 #11 동형).
        self.assertGreaterEqual(
            bot.count("from bot.breadth_strategy import regenerate"), 2,
            "주기 재생성 또는 startup 즉시 생성 미배선")
        self.assertIn("_breadth_strategy_initial", bot, "startup 스레드 미등록")

    def test_uses_shared_sector_registry_not_a_copy(self):
        """표본을 복제하면 시장타이밍 breadth 카드와 값이 갈라진다.

        문자열 grep 은 주석만으로도 통과하므로 **레지스트리를 바꿔치기해
        결과가 따라오는지** 로 확인한다(2026-08-16 독립 리뷰)."""
        from bot import market_timing as mt
        orig = mt._BREADTH_SECTORS
        mt._BREADTH_SECTORS = {"KR": {"999999.KS": "가짜섹터"}}
        self.addCleanup(lambda: setattr(mt, "_BREADTH_SECTORS", orig))
        seen = []
        os_ = bs._series
        bs._series = lambda t, days=400: seen.append(t) or []
        self.addCleanup(lambda: setattr(bs, "_series", os_))
        bs.build_market("KR")
        self.assertIn("999999.KS", seen, "공용 레지스트리를 안 읽는다(복제본)")

    def test_history_fetch_guards_length(self):
        """MA120 이 전략의 축이라 짧은 시계열은 판정을 통째로 바꾼다.

        `min_rows` 를 **실제 kwarg 로 넘기는지** 확인한다 — 소스 문자열
        검사는 호출 위 주석에도 매칭돼 가드를 지워도 통과한다."""
        from bot import market_timing as mt
        got = {}
        orig = mt.fetch_index_history
        mt.fetch_index_history = lambda t, **kw: got.update(kw) or []
        self.addCleanup(lambda: setattr(mt, "fetch_index_history", orig))
        bs._series("^KS11")
        self.assertEqual(got.get("min_rows"), 200)


if __name__ == "__main__":
    unittest.main()


class ConfirmedSignalAuditTests(unittest.TestCase):
    """사용자 2026-09-07 "8월말 현금대기가 맞는거야? 8월 마지막날엔 극단적
    과매도로 봤는데" — 화면이 Breadth **%만** 보여줘 그 질문에 답할 수 없었다.

    셋을 고쳤다: ① 판정 전 반올림 제거(4/13 이 `30.80%` 로 보였다) ② 확정
    기록에 분모 저장 ③ 이력 표에 분모 표시. 그리고 그 달을 재계산해 대조하는
    `--why` 를 심었다(#252 반복 확인은 제품에).
    """

    def test_breadth_is_not_rounded_before_the_regime_is_judged(self):
        """⚠️ 옛 판은 `round(pct, 1)` 이라 **판정이 반올림된 값**을 봤고,
        화면은 2자리로 찍어 4/13 이 `30.80%` 로 보였다 — 사용자가 눈으로
        나눠 봐도 안 맞는다(#33). 반올림은 표시에서만 한다."""
        up = [100 + i * 0.1 for i in range(200)]
        down = [100 - i * 0.1 for i in range(200)]
        sectors = {f"S{i}": (up if i < 4 else down) for i in range(13)}
        b = bs.breadth_above_ma(sectors, 120)
        self.assertEqual((b["above"], b["counted"]), (4, 13))
        self.assertAlmostEqual(b["pct"], 400 / 13, places=9)   # 30.769…
        self.assertEqual(bs._pct_s(b["pct"]), "30.77%")        # 30.80% 아님
        self.assertEqual(bs.classify_regime(b["pct"]), "RECOVERY")

    def test_confirmed_record_keeps_the_denominator(self):
        """⚠️ %만 남기면 그 30.8% 가 4/13 인지, 원천 절단으로 분모가 줄어든
        4/12 인지 **영원히 못 가른다**. 기록은 멱등이라 한 번 잘못 들어가면
        그대로 굳는다(#18·#45)."""
        rec = bs._signal_record({
            "asof": "2026-08-29", "state": "CASH", "regime": "RECOVERY",
            "breadth_pct": 30.77, "dd_pct": -25.52,
            "breadth": {"above": 4, "counted": 13, "skipped": ["철강"]},
            "index_w": 0.0, "total_w": 0.0, "cash_w": 1.0,
            "targets": [], "rs_ranked": []})
        self.assertEqual(rec["breadth_above"], 4)
        self.assertEqual(rec["breadth_counted"], 13)
        self.assertEqual(rec["breadth_skipped"], ["철강"])

    def test_history_cell_shows_the_denominator_but_never_invents_one(self):
        """분모가 있으면 `30.77% (4/13)`, 옛 기록이면 % 만(#32 지어내지 않는다)."""
        cell = bs._breadth_cell({"breadth_pct": 400 / 13,
                                 "breadth_above": 4, "breadth_counted": 13})
        self.assertEqual(cell, "30.77% (4/13)")
        with_skip = bs._breadth_cell({"breadth_pct": 30.0, "breadth_above": 3,
                                      "breadth_counted": 10,
                                      "breadth_skipped": ["a", "b"]})
        self.assertIn("(3/10, 제외 2)", with_skip)
        self.assertEqual(bs._breadth_cell({"breadth_pct": 30.8}), "30.80%")

    def test_verdict_is_three_state_and_tolerates_old_rounding(self):
        """⚠️ 대조할 게 없으면 통과가 아니라 **판정 불가**(#54·#274). 옛 기록은
        1자리로 반올림돼 있으므로 0.05%p 여유 — 반올림 차이를 '불일치' 로
        부르면 진짜 불일치를 가린다."""
        snap = {"breadth_pct": 400 / 13, "dd_pct": -25.52, "regime": "RECOVERY",
                "state": "CASH", "total_w": 0.0, "index_w": 0.0}
        rec = {"breadth_pct": 30.8, "dd_pct": -25.52, "regime": "RECOVERY",
               "state": "CASH", "total_w": 0.0, "index_w": 0.0}
        self.assertTrue(bs._why_verdict(rec, snap)[0].startswith("✅"))
        self.assertTrue(bs._why_verdict({}, snap)[0].startswith("❓"))
        self.assertTrue(
            bs._why_verdict(rec, {"breadth_pct": None})[0].startswith("❓"))
        v, bad = bs._why_verdict({**rec, "breadth_pct": 23.1}, snap)
        self.assertTrue(v.startswith("❌"))
        self.assertTrue(any("Breadth" in x for x in bad), bad)

    def test_why_is_read_only(self):
        """⚠️ 진단이 자기가 읽을 신호를 오염시키면 안 된다(#30·#264·#283) —
        `build_with_signals`·`append_signal` 은 확정 신호를 **쓴다**."""
        import ast
        src = open("bot/breadth_strategy.py", encoding="utf-8").read()
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "_cli_why")
        called = {n.func.id for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn("build_with_signals", called)
        self.assertNotIn("append_signal", called)
        self.assertIn("_assemble", called)          # 화면이 쓰는 그 경로(#35)
        self.assertIn("_fetch_market", called)

    def test_why_prints_the_sample_and_the_last_days_of_the_month(self):
        """⚠️ 사용자의 질문("월말엔 과매도였는데")은 **날짜별 재계산** 없이는
        답이 안 나온다 — 원시 float 을 노출하지 않고(#43b) 섹터 표본과 그 달
        마지막 거래일들을 찍는지 값으로 확인한다."""
        import datetime as _dt
        import io
        import json
        import re
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path

        days, d = [], _dt.date(2025, 7, 1)
        while len(days) < 300:
            if d.weekday() < 5:
                days.append(d.isoformat())
            d += _dt.timedelta(days=1)
        days = [x for x in days if x <= "2026-08-31"][-300:]
        fall = [100 - i * 0.1 for i in range(300)]
        rise = [100 + i * 0.1 for i in range(300)]
        late = list(fall)
        late[-1] = 200.0                    # 마지막 종가에만 MA120 위로
        labels = {f"S{i:02d}": (rise if i < 3 else list(fall))
                  for i in range(13)}
        labels["S12"] = late

        def ser(v):
            return [{"date": a, "close": c} for a, c in zip(days, v)]

        from bot import market_timing as mt
        old_series, old_sec = bs._series, mt._BREADTH_SECTORS
        old_lbl, old_dir = mt._BREADTH_SOURCE_LABEL, bs._SIGNAL_DIR
        try:
            with tempfile.TemporaryDirectory() as td:
                bs._SIGNAL_DIR = Path(td)
                bs._series = (lambda t, days_=400:
                              ser(fall) if t == "^KS11" else ser(labels[t]))
                mt._BREADTH_SECTORS = {"KR": {k: k for k in labels}}
                mt._BREADTH_SOURCE_LABEL = {"KR": "테스트"}
                bs.signal_path("KR").write_text(json.dumps({
                    "month": "2026-08", "asof": days[-1], "state": "CASH",
                    "regime": "RECOVERY", "breadth_pct": 30.8,
                    "dd_pct": -26.37, "index_w": 0, "total_w": 0,
                    "cash_w": 1.0, "targets": [], "top3": []}) + "\n",
                    encoding="utf-8")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = bs._cli_why("KR", "2026-08")
                out = buf.getvalue()
        finally:
            bs._series, mt._BREADTH_SECTORS = old_series, old_sec
            mt._BREADTH_SOURCE_LABEL, bs._SIGNAL_DIR = old_lbl, old_dir

        self.assertEqual(rc, 0, out)
        self.assertIn("상회 4개 / 표본 13개 = 30.77%", out)   # ③ 분모를 센다
        self.assertIn("23.08% · 역추세 구간", out)            # ⑤ 달 안의 변화
        self.assertIn("30.77% · 회복 구간", out)              # ⑤ 월말 종가
        self.assertIn("← 월말 확정", out)
        self.assertIn("경계 민감도", out)                     # ④ 3개였다면?
        self.assertRegex(out, r"상회\s+3개 →\s+23\.1% · 역추세")
        # ⚠️ 원시 float 금지 — `30.76923076923077%` 같은 노출이 있으면 실패
        self.assertNotRegex(out, r"\d\.\d{4,}", out)

    def test_history_table_actually_renders_the_denominator(self):
        """⚠️ 헬퍼만 재면 **배선을 떼는 변형을 못 잡는다**(#20 — 실측: 이력 표를
        옛 `_pct_s` 로 되돌리는 뮤테이션이 회귀 60개를 전부 통과했다).
        렌더된 HTML 에 분모가 실리는지 값으로 본다."""
        import json
        import tempfile
        from pathlib import Path

        old_dir = bs._SIGNAL_DIR
        try:
            with tempfile.TemporaryDirectory() as td:
                bs._SIGNAL_DIR = Path(td)
                bs.signal_path("KR").write_text(json.dumps({
                    "month": "2026-08", "asof": "2026-08-29", "state": "CASH",
                    "regime": "RECOVERY", "breadth_pct": 400 / 13,
                    "breadth_above": 4, "breadth_counted": 13,
                    "breadth_skipped": [], "dd_pct": -25.52,
                    "index_w": 0, "total_w": 0, "cash_w": 1.0,
                    "targets": [], "top3": []}) + "\n", encoding="utf-8")
                html = bs.render_page({"KR": RenderTests._snap(
                    "KR", 400 / 13, "CASH", "RECOVERY", 0.0, 1.0)})
        finally:
            bs._SIGNAL_DIR = old_dir
        self.assertIn("30.77% (4/13)", html)
        self.assertNotIn("30.80%", html)

    def test_history_row_says_which_regime_and_why_cash(self):
        """사용자 2026-09-07 "8월말에 왜 현금대기인지 안알려줘? 이거는 역추세·
        추세·회복·비추세중에 어떤건거야?" — 확정 이력에 **구간 열이 아예
        없었고**, `현금 대기` 는 네 구간 모두에서 나오는 라벨이라 상태만으론
        못 가른다(#82·#34).

        ⚠️ 저장된 필드에서 렌더타임에 파생하므로 **옛 기록도 그대로 따라온다**
        (#270) — 이 픽스처가 바로 그 옛 형식이다(분모·사유 필드 없음).
        """
        import json
        import re
        import tempfile
        from pathlib import Path

        old_dir = bs._SIGNAL_DIR
        try:
            with tempfile.TemporaryDirectory() as td:
                bs._SIGNAL_DIR = Path(td)
                bs.signal_path("KR").write_text(json.dumps({
                    "month": "2026-08", "asof": "2026-08-29", "state": "CASH",
                    "regime": "RECOVERY", "breadth_pct": 30.8,
                    "dd_pct": -25.52, "index_w": 0, "total_w": 0,
                    "cash_w": 1.0, "targets": [], "top3": []}) + "\n",
                    encoding="utf-8")
                html = bs.render_page({"KR": RenderTests._snap(
                    "KR", 30.8, "CASH", "RECOVERY", 0.0, 1.0)})
        finally:
            bs._SIGNAL_DIR = old_dir
        row = re.search(r"<tbody>(.*?)</tbody>", html, re.S).group(1)
        assert "회복 구간" in row, row          # 구간 열
        assert "현금 대기" in row
        assert "회복조건" in row and "충족한 섹터가" in row   # 왜 현금인지
        # ⚠️ escape 를 거치므로 마크다운 볼드는 기호가 그대로 나온다(실측)
        assert "**" not in row, row
        # 헤더에도 열이 늘어야 셀과 어긋나지 않는다(#45)
        assert html.count("<th>구간</th>") >= 1

    def test_cash_reason_names_the_branch_and_never_guesses(self):
        """`CASH` 는 **네 갈래**로 도달한다 — 처방이 다 다르므로 이름을 댄다
        (#82). 현금이 아니면 아무 말도 안 하고(늘 뜨는 문구 금지 #25·#260),
        모르는 조합은 지어내지 않는다(#165)."""
        assert "회복조건" in bs.cash_reason("RECOVERY", "CASH")
        assert "트랜치" in bs.cash_reason("CONTRARIAN", "CASH", -5.2)
        assert "-5.20%" in bs.cash_reason("CONTRARIAN", "CASH", -5.2)
        assert "RS" in bs.cash_reason("NON_TREND", "CASH")
        assert "RS" in bs.cash_reason("TREND", "CASH")
        assert "판정 불가" in bs.cash_reason(None, "CASH")
        # 현금이 아닌 상태엔 한 글자도 안 붙는다
        assert bs.cash_reason("TREND", "TREND_RS_TOP3") == ""
        assert bs.cash_reason("RECOVERY", "RECOVERY_LEADER_PULLBACK") == ""
        # 네 갈래가 **서로 다른 문장**이어야 라벨 노릇을 한다
        outs = {bs.cash_reason(r, "CASH") for r in
                ("CONTRARIAN", "RECOVERY", "NON_TREND")}
        assert len(outs) == 3, outs

    def test_month_end_is_corrected_when_a_later_close_arrives(self):
        """⚠️ VM 실측(2026-09-07): 2026-08 확정이 `asof=08-28`(금)인데 실제
        마지막 거래일은 **08-31**(월)이었다 — 09-01 에 기록될 때 야후 시계열에
        08-31 이 아직 없었고, 멱등 가드가 `month` 만 봐서 나중에 그 봉이 와도
        영영 안 고쳐졌다(#18 구워진 데이터).

        무해하지 않다 — 같은 실행에서 08-24 는 `과거 리더 놀림목`(50% 투자),
        08-25~31 은 `현금 대기` 였다. **어느 날을 월말로 잡느냐가 신호를 바꾼다.**
        """
        import json
        import tempfile
        from pathlib import Path

        old_dir = bs._SIGNAL_DIR
        try:
            with tempfile.TemporaryDirectory() as td:
                bs._SIGNAL_DIR = Path(td)
                early = {"month": "2026-08", "asof": "2026-08-28",
                         "state": "CASH", "regime": "RECOVERY",
                         "breadth_pct": 30.8, "top3": ["IT"]}
                assert bs.append_signal("KR", early) is True
                # 같은 기준일 재실행 → 멱등(종전 그대로)
                assert bs.append_signal("KR", early) is False
                # 더 **이른** 종가는 무시한다(뒤로 가지 않는다)
                assert bs.append_signal(
                    "KR", {**early, "asof": "2026-08-27"}) is False
                # 더 **늦은** 종가가 오면 정정본을 쓴다
                late = {**early, "asof": "2026-08-31", "state": "CASH",
                        "breadth_pct": 30.77, "top3": ["보험"]}
                assert bs.append_signal("KR", late) is True
                recs = bs.load_signals("KR")
                # 화면·후보 풀 모두 달마다 **하나**여야 한다
                assert len(recs) == 1, recs
                assert recs[0]["asof"] == "2026-08-31"
                assert recs[0]["top3"] == ["보험"]
        finally:
            bs._SIGNAL_DIR = old_dir

    def test_backfill_loop_reruns_a_month_when_a_later_close_arrives(self):
        """⚠️ 헬퍼(append_signal)만 재면 **루프의 스킵 조건**을 되돌리는 변형을
        못 잡는다(#20 실측: `if d[:7] in have` 로 되돌려도 회귀 65개가 전부
        통과했다). 수집기를 통째로 태워 정정이 실제로 일어나는지 본다."""
        import datetime as _dt
        import json
        import tempfile
        from pathlib import Path

        days, d = [], _dt.date(2025, 7, 1)
        while len(days) < 320:
            if d.weekday() < 5:
                days.append(d.isoformat())
            d += _dt.timedelta(days=1)
        days = [x for x in days if x <= "2026-09-04"][-320:]
        assert "2026-08-31" in days and "2026-09-01" in days
        fall = [100 - i * 0.1 for i in range(len(days))]
        rise = [100 + i * 0.1 for i in range(len(days))]
        labels = {f"S{i:02d}": (rise if i < 4 else list(fall))
                  for i in range(13)}

        def ser(v):
            return [{"date": a, "close": c} for a, c in zip(days, v)]

        from bot import market_timing as mt
        old = (bs._series, mt._BREADTH_SECTORS, mt._BREADTH_SOURCE_LABEL,
               bs._SIGNAL_DIR)
        try:
            with tempfile.TemporaryDirectory() as td:
                bs._SIGNAL_DIR = Path(td)
                bs._series = (lambda t, days_=400:
                              ser(fall) if t == "^KS11" else ser(labels[t]))
                mt._BREADTH_SECTORS = {"KR": {k: k for k in labels}}
                mt._BREADTH_SOURCE_LABEL = {"KR": "테스트"}
                # 원천이 늦어 **08-28 로 굳은** 옛 기록을 심는다(실측 재현)
                bs.signal_path("KR").write_text(json.dumps({
                    "month": "2026-08", "asof": "2026-08-28", "state": "CASH",
                    "regime": "RECOVERY", "breadth_pct": 30.8,
                    "top3": ["옛값"]}) + "\n", encoding="utf-8")
                bs.build_with_signals("KR")
                recs = [r for r in bs.load_signals("KR")
                        if r.get("month") == "2026-08"]
        finally:
            (bs._series, mt._BREADTH_SECTORS, mt._BREADTH_SOURCE_LABEL,
             bs._SIGNAL_DIR) = old
        assert len(recs) == 1, recs
        assert recs[0]["asof"] == "2026-08-31", recs[0]
        assert recs[0]["top3"] != ["옛값"], "정정본이 아니라 옛 기록이다"

    def test_history_row_shows_which_close_confirmed_it(self):
        """월만 적으면 월말이 며칠 이르게 굳은 사실이 안 보인다(#43)."""
        import json
        import re
        import tempfile
        from pathlib import Path

        old_dir = bs._SIGNAL_DIR
        try:
            with tempfile.TemporaryDirectory() as td:
                bs._SIGNAL_DIR = Path(td)
                bs.signal_path("KR").write_text(json.dumps({
                    "month": "2026-08", "asof": "2026-08-28", "state": "CASH",
                    "regime": "RECOVERY", "breadth_pct": 30.8,
                    "dd_pct": -25.52, "index_w": 0, "total_w": 0,
                    "cash_w": 1.0, "targets": [], "top3": []}) + "\n",
                    encoding="utf-8")
                html = bs.render_page({"KR": RenderTests._snap(
                    "KR", 30.8, "CASH", "RECOVERY", 0.0, 1.0)})
        finally:
            bs._SIGNAL_DIR = old_dir
        row = re.search(r"<tbody>(.*?)</tbody>", html, re.S).group(1)
        assert "2026-08-28" in row, row

    def test_entrypoint_is_last_and_dispatches_why(self):
        """⚠️ 엔트리포인트가 파일 중간이면 그 아래 정의는 영영 안 닿는다
        (#276). 그리고 배선은 **존재가 아니라 호출**이다(#120)."""
        import ast
        src = open("bot/breadth_strategy.py", encoding="utf-8").read()
        tree = ast.parse(src)
        mains = [n for n in tree.body
                 if isinstance(n, ast.If) and "__main__" in ast.dump(n.test)]
        self.assertEqual(len(mains), 1)
        self.assertIs(mains[0], tree.body[-1], "엔트리포인트가 맨 끝이 아니다")
        # ⚠️ 소스 문자열(`"'--why'" in src`)로 재면 따옴표 종류 하나에 깨진다
        # (#19 — 실제로 깨졌다). 그 블록의 **상수와 호출**을 AST 로 본다.
        consts = {n.value for n in ast.walk(mains[0])
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertIn("--why", consts)
        calls = {n.func.id for n in ast.walk(mains[0])
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertIn("_cli_why", calls)
