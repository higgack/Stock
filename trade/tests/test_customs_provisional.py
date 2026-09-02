"""trade.customs_provisional — 관세청 10일 단위 잠정치 '잠정 속보' 박스.

파싱(천달러→USD ×1000)·누적창 decile·작년 동순 YoY·렌더·저장을 검증.
실제 응답(2026-06 cntyMmUtPrviImpAcrs 미리보기) 일부를 픽스처로 박아
단위/구조 회귀를 잡는다.
"""

import sqlite3
import unittest

from trade import customs_provisional as prov


# 실제 미리보기 응답에서 발췌(2023-01 세 누적창). 금액 단위 '천 달러'.
_REAL_XML = """<?xml version="1.0"?>
<response><header><resultCode>00</resultCode><resultMsg>정상서비스.</resultMsg></header>
<body><items>
<item>
<itemUsdAmt00> 20,130,610</itemUsdAmt00><itemUsdAmt01> 4,803,571</itemUsdAmt01>
<itemUsdAmt02> 2,304,757</itemUsdAmt02><itemUsdAmt03> 1,758,981</itemUsdAmt03>
<itemUsdAmt04> 1,023,998</itemUsdAmt04><itemUsdAmt05> 901,729</itemUsdAmt05>
<itemUsdAmt06> 1,300,703</itemUsdAmt06><itemUsdAmt07> 820,706</itemUsdAmt07>
<itemUsdAmt08> 839,299</itemUsdAmt08><itemUsdAmt09> 295,795</itemUsdAmt09>
<itemUsdAmt10> 479,102</itemUsdAmt10>
<priodDt>01~10</priodDt><priodMon>202301</priodMon><priodYear>2023</priodYear>
</item>
<item>
<itemUsdAmt00> 43,949,168</itemUsdAmt00><itemUsdAmt01> 10,018,562</itemUsdAmt01>
<itemUsdAmt02> 5,276,596</itemUsdAmt02><itemUsdAmt03> 3,976,199</itemUsdAmt03>
<itemUsdAmt04> 2,633,464</itemUsdAmt04><itemUsdAmt05> 1,789,072</itemUsdAmt05>
<itemUsdAmt06> 3,029,053</itemUsdAmt06><itemUsdAmt07> 1,462,728</itemUsdAmt07>
<itemUsdAmt08> 1,959,734</itemUsdAmt08><itemUsdAmt09> 647,681</itemUsdAmt09>
<itemUsdAmt10> 1,107,208</itemUsdAmt10>
<priodDt>01~20</priodDt><priodMon>202301</priodMon><priodYear>2023</priodYear>
</item>
<item>
<itemUsdAmt00> 59,037,259</itemUsdAmt00><itemUsdAmt01> 13,140,061</itemUsdAmt01>
<itemUsdAmt02> 6,995,423</itemUsdAmt02><itemUsdAmt03> 5,587,935</itemUsdAmt03>
<itemUsdAmt04> 3,953,682</itemUsdAmt04><itemUsdAmt05> 2,401,963</itemUsdAmt05>
<itemUsdAmt06> 4,110,352</itemUsdAmt06><itemUsdAmt07> 1,750,162</itemUsdAmt07>
<itemUsdAmt08> 2,768,103</itemUsdAmt08><itemUsdAmt09> 792,251</itemUsdAmt09>
<itemUsdAmt10> 1,368,578</itemUsdAmt10>
<priodDt>01~31</priodDt><priodMon>202301</priodMon><priodYear>2023</priodYear>
</item>
</items><totalCount>3</totalCount></body></response>"""


class ParseTests(unittest.TestCase):
    def test_unit_is_thousand_usd_times_1000(self):
        rows = prov.parse_response(_REAL_XML)
        self.assertEqual(len(rows), 3)
        first = rows[0]
        # 20,130,610 천달러 → 20,130,610,000 USD
        self.assertEqual(first["amt"][0], 20_130_610_000)
        self.assertEqual(first["amt"][1], 4_803_571_000)
        self.assertEqual(first["ym"], "2023-01")

    def test_decile_classification(self):
        rows = prov.parse_response(_REAL_XML)
        self.assertEqual([r["decile"] for r in rows], ["D1", "D2", "FULL"])

    def test_resultcode_error_raises(self):
        bad = ("<response><header><resultCode>30</resultCode>"
               "<resultMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</resultMsg>"
               "</header><body><items/></body></response>")
        with self.assertRaises(prov.ProvisionalAPIError):
            prov.parse_response(bad)

    def test_num_handles_blank_and_commas(self):
        self.assertEqual(prov._num(" 1,234"), 1234)
        self.assertEqual(prov._num(""), 0)
        self.assertEqual(prov._num(None), 0)
        self.assertEqual(prov._num("  "), 0)


class FetchTests(unittest.TestCase):
    def test_fetch_uses_injected_fetcher(self):
        captured = {}

        def fake(url):
            captured["url"] = url
            return _REAL_XML

        rows = prov.fetch("imp_cnty", "202201", "202312",
                          key="K", fetcher=fake)
        self.assertEqual(len(rows), 3)
        # 올바른 엔드포인트 경로 + serviceKey 인코딩
        self.assertIn("/cntyMmUtPrviImpAcrs/getCntyMmUtPrviImpAcrs?", captured["url"])
        self.assertIn("serviceKey=K", captured["url"])
        self.assertIn("strtYymm=202201", captured["url"])

    def test_fetch_unknown_kind_raises(self):
        with self.assertRaises(prov.ProvisionalAPIError):
            prov.fetch("nope", "202201", "202312", key="K", fetcher=lambda u: "")

    def test_fetch_missing_key_raises(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(prov.ProvisionalAPIError):
                prov.fetch("imp_item", "202201", "202312", fetcher=lambda u: "")


class SignalTests(unittest.TestCase):
    def _rows(self):
        # 작년/올해 5월, 올해 5월엔 D1·D2 둘 다 → freshest=D2, YoY=같은 D2.
        return [
            {"ym": "2025-05", "priod_dt": "01~20", "decile": "D2",
             "amt": [1000] + [100] * 10},
            {"ym": "2025-05", "priod_dt": "01~10", "decile": "D1",
             "amt": [500] + [50] * 10},
            {"ym": "2026-05", "priod_dt": "01~10", "decile": "D1",
             "amt": [600] + [60] * 10},
            {"ym": "2026-05", "priod_dt": "01~20", "decile": "D2",
             "amt": [1200] + [120] * 10},
        ]

    def test_picks_latest_month_freshest_window(self):
        sig = prov.latest_signal(self._rows(), tuple(f"품목{i}" for i in range(1, 11)))
        self.assertEqual(sig["ym"], "2026-05")
        self.assertEqual(sig["decile"], "D2")          # D1 아닌 가장 진행된 창
        self.assertEqual(sig["window"], "1~20일")
        self.assertEqual(sig["total_usd"], 1200)

    def test_yoy_matches_same_decile_prior_year(self):
        sig = prov.latest_signal(self._rows(), tuple(f"품목{i}" for i in range(1, 11)))
        # 1200 vs 작년 동월·동순(D2) 1000 = +20% (D1 500과 비교하면 안 됨)
        self.assertAlmostEqual(sig["total_yoy"], 20.0)
        self.assertAlmostEqual(sig["items"][0]["yoy"], 20.0)
        self.assertEqual(sig["items"][0]["name"], "품목1")

    def test_yoy_none_when_no_prior_year(self):
        rows = [{"ym": "2026-05", "priod_dt": "01~20", "decile": "D2",
                 "amt": [1200] + [120] * 10}]
        sig = prov.latest_signal(rows, tuple(f"품목{i}" for i in range(1, 11)))
        self.assertIsNone(sig["total_yoy"])
        self.assertIsNone(sig["items"][0]["yoy"])

    def test_mom_matches_same_decile_prior_month(self):
        # 전월 동순(MoM) — 6월 D1 vs 5월 D1 같은 누적창 (사용자 2026-06-12).
        rows = [
            {"ym": "2026-05", "priod_dt": "01~10", "decile": "D1",
             "amt": [300] + [30] * 10},
            {"ym": "2026-06", "priod_dt": "01~10", "decile": "D1",
             "amt": [372] + [37] * 10},
        ]
        sig = prov.latest_signal(rows, tuple(f"품목{i}" for i in range(1, 11)))
        self.assertEqual(sig["ym"], "2026-06")
        self.assertAlmostEqual(sig["total_mom"], (372 - 300) / 300 * 100)
        self.assertAlmostEqual(sig["items"][0]["mom"], (37 - 30) / 30 * 100)

    def test_mom_none_when_no_prior_month(self):
        rows = [{"ym": "2026-06", "priod_dt": "01~10", "decile": "D1",
                 "amt": [372] + [37] * 10}]
        sig = prov.latest_signal(rows, tuple(f"품목{i}" for i in range(1, 11)))
        self.assertIsNone(sig["total_mom"])

    def test_dashboard_recomputes_headline_from_series(self):
        # 헤드라인 박스 MoM 즉시 반영 — dashboard 가 저장 payload(옛 스키마)
        # 가 아니라 load_rows(series)에서 latest_signal 재계산하는지 (배포 직후
        # 표시, fetch 재실행 안 기다림). 소스 읽기(트레이드 dashboard 는 dotenv
        # 의존이라 import 회피) — 구조 회귀 가드.
        import os
        src = open(os.path.join(os.path.dirname(__file__), "..", "dashboard.py"),
                   encoding="utf-8").read()
        self.assertIn("customs_provisional.latest_signal(", src)
        self.assertIn("prov_signals[_kind] = _sig", src)

    def test_empty_rows_none(self):
        self.assertIsNone(prov.latest_signal([], ("a",)))


class RenderTests(unittest.TestCase):
    def _signals(self):
        imp_items = [{"name": n, "usd": u, "yoy": y} for n, u, y in [
            ("반도체", 5_000_000_000, 12.0),
            ("원유", 4_000_000_000, -3.0),
            ("기계류", 3_000_000_000, 5.0),
            ("가스", 2_000_000_000, 1.0),
            ("반도체제조용장비", 1_500_000_000, 48.0),
            ("정밀기기", 1_000_000_000, 2.0),
            ("석유제품", 900_000_000, -1.0),
            ("무선통신기기", 800_000_000, 4.0),
            ("승용차", 700_000_000, 9.0),
            ("석탄", 500_000_000, -8.0),
        ]]
        exp_items = [{"name": n, "usd": u, "yoy": y} for n, u, y in [
            ("반도체", 12_000_000_000, 33.0),
            ("철강제품", 3_000_000_000, -2.0),
        ] + [(f"x{i}", 100_000_000, 0.0) for i in range(8)]]
        return {
            "imp_item": {"ym": "2026-05", "decile": "D2", "window": "1~20일",
                         "priod_dt": "01~20", "total_usd": 40_000_000_000,
                         "total_yoy": -3.0, "items": imp_items},
            "exp_item": {"ym": "2026-05", "decile": "D2", "window": "1~20일",
                         "priod_dt": "01~20", "total_usd": 35_000_000_000,
                         "total_yoy": 9.0, "items": exp_items},
        }

    def test_box_has_headline_markers(self):
        html = prov.render_box(self._signals())
        self.assertIn("잠정 속보", html)
        self.assertIn("ind-prov", html)
        self.assertIn("관세청 10일 단위", html)
        self.assertIn("2026-05", html)
        self.assertIn("1~20일", html)
        self.assertIn("전체 수출", html)
        self.assertIn("전체 수입", html)
        self.assertIn("반도체제조용장비 수입", html)
        self.assertIn("⚡선행", html)               # capex 선행 강조
        self.assertIn("반도체 수출", html)
        self.assertIn("억$", html)                   # 단위 표기
        self.assertIn("provisional_archive.html", html)  # 🗄 잠정 타임라인 링크
        self.assertIn("잠정 타임라인", html)

    def test_current_window_badge(self):
        # 최신 잠정 창 명시 badge (사용자 2026-06-15 '현재 말고 최신으로' — 확정
        # 라벨과 통일). _signals() 는 D2(1~20일) → 21일 발표 힌트.
        html = prov.render_box(self._signals())
        self.assertIn("ind-prov-cur", html)      # 최신-창 강조 badge
        self.assertIn("최신", html)               # '현재' 아님(사용자 정정)
        self.assertIn("21일 발표", html)           # D2 → 21일 발표

    def test_export_cells_no_caveat(self):
        # ⚠️ 상시 캐비엇 제거 (사용자 2026-06-12 '검증 완료, 없애도 됨') —
        # 잠정의 성질은 '잠정 속보' 라벨 + 잠정 타임라인이 전달.
        html = prov.render_box(self._signals())
        self.assertNotIn("⚠️", html)
        self.assertNotIn("ind-prov-warn", html)
        self.assertNotIn("추세로 참고", html)

    def test_no_export_no_caveat(self):
        # 수입만 있으면 수출 ⚠️ 캡션은 안 뜬다.
        sig = self._signals()
        html = prov.render_box({"imp_item": sig["imp_item"]})
        self.assertNotIn("ind-prov-cav", html)
        self.assertNotIn("ind-prov-warn", html)

    def test_box_yoy_sign_classes(self):
        html = prov.render_box(self._signals())
        self.assertIn("ind-prov-pos", html)          # 양수 YoY(녹색)
        self.assertIn("ind-prov-neg", html)          # 음수도 존재 가능

    def test_empty_signals_returns_blank(self):
        self.assertEqual(prov.render_box({}), "")

    def test_partial_signals_still_render(self):
        # imp_item만 있어도(=수출 미수집) 박스는 그려진다.
        sig = self._signals()
        html = prov.render_box({"imp_item": sig["imp_item"]})
        self.assertIn("잠정 속보", html)
        self.assertIn("반도체제조용장비 수입", html)


class StoreTests(unittest.TestCase):
    def test_store_load_roundtrip(self):
        conn = sqlite3.connect(":memory:")
        prov.ensure_schema(conn)
        sig = {"ym": "2026-05", "decile": "D2", "window": "1~20일",
               "priod_dt": "01~20", "total_usd": 1, "total_yoy": 2.0,
               "items": [{"name": "반도체", "usd": 1, "yoy": 3.0}]}
        prov.store_signal(conn, "imp_item", sig)
        loaded = prov.load_signals(conn)
        self.assertIn("imp_item", loaded)
        self.assertEqual(loaded["imp_item"]["ym"], "2026-05")
        # 재저장(같은 kind)은 덮어씀 — 1건 유지
        prov.store_signal(conn, "imp_item", {**sig, "total_usd": 9})
        self.assertEqual(prov.load_signals(conn)["imp_item"]["total_usd"], 9)
        conn.close()

    def test_load_signals_safe_when_table_absent(self):
        conn = sqlite3.connect(":memory:")
        self.assertEqual(prov.load_signals(conn), {})
        conn.close()

    def test_store_load_rows_roundtrip(self):
        conn = sqlite3.connect(":memory:")
        rows = [{"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL",
                 "amt": [1] * 11}]
        prov.store_signal(conn, "imp_item", {"ym": "2026-05"}, rows=rows)
        self.assertEqual(prov.load_rows(conn)["imp_item"], rows)
        # signal도 같이 저장돼 박스는 그대로
        self.assertIn("imp_item", prov.load_signals(conn))
        conn.close()

    def test_load_rows_migrates_old_table(self):
        # series_json 컬럼 없는 구버전 테이블도 ensure_schema가 마이그레이션
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE customs_provisional (kind TEXT PRIMARY KEY, "
                     "payload TEXT NOT NULL, fetched_at TEXT NOT NULL)")
        conn.execute("INSERT INTO customs_provisional VALUES ('imp_item','{}','t')")
        self.assertEqual(prov.load_rows(conn), {})        # 옛 행은 series 없음
        rows = [{"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL",
                 "amt": [2] * 11}]
        prov.store_signal(conn, "imp_item", {"ym": "x"}, rows=rows)
        self.assertEqual(prov.load_rows(conn)["imp_item"], rows)
        conn.close()


class StaleTests(unittest.TestCase):
    _ROW = [{"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL", "amt": [1] * 11}]

    def test_empty_is_stale(self):
        conn = sqlite3.connect(":memory:")
        self.assertTrue(prov.is_stale(conn))
        conn.close()

    def test_complete_fresh_not_stale(self):
        conn = sqlite3.connect(":memory:")
        for k in prov.ENDPOINTS:
            prov.store_signal(conn, k, {"ym": "2026-05"}, rows=self._ROW)
        self.assertFalse(prov.is_stale(conn, max_age_h=6))
        conn.close()

    def test_incomplete_series_is_stale(self):
        # 한 종류라도 시계열이 없으면(구버전·미수집) self-heal 대상
        conn = sqlite3.connect(":memory:")
        for i, k in enumerate(prov.ENDPOINTS):
            prov.store_signal(conn, k, {"ym": "x"},
                              rows=None if i == 0 else self._ROW)
        self.assertTrue(prov.is_stale(conn))
        conn.close()

    def test_old_fetch_is_stale(self):
        conn = sqlite3.connect(":memory:")
        for k in prov.ENDPOINTS:
            prov.store_signal(conn, k, {"ym": "2026-05"}, rows=self._ROW)
        conn.execute("UPDATE customs_provisional SET fetched_at=?",
                     ("2020-01-01T00:00:00+00:00",))
        self.assertTrue(prov.is_stale(conn, max_age_h=6))
        conn.close()


class MomentumTests(unittest.TestCase):
    def _rows(self):
        # 두 해 풀월. item1=가속, item2=둔화 → 정렬·모멘텀 검증.
        def amt(t, a, b):
            return [t, a, b] + [5] * 8
        return [
            {"ym": "2025-04", "priod_dt": "01~30", "decile": "FULL", "amt": amt(100, 10, 10)},
            {"ym": "2025-05", "priod_dt": "01~31", "decile": "FULL", "amt": amt(110, 11, 11)},
            {"ym": "2026-04", "priod_dt": "01~30", "decile": "FULL", "amt": amt(120, 12, 20)},
            {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL", "amt": amt(150, 18, 21)},
        ]

    def test_momentum_values(self):
        mv = prov.momentum_rows(self._rows(), tuple(f"품목{i}" for i in range(1, 11)))
        self.assertEqual(mv["ym"], "2026-05")
        by = {it["name"]: it for it in mv["items"]}
        # 전체: 최신 YoY 36.4 − 직전 풀월(4월) YoY 20 = +16.4
        self.assertAlmostEqual(by["전체"]["momentum"], 40 / 110 * 100 - 20.0, places=1)
        # 품목1 가속(▲ 큰 양수), 품목2 둔화(▼ 음수)
        self.assertGreater(by["품목1"]["momentum"], 30)
        self.assertLess(by["품목2"]["momentum"], 0)

    def test_momentum_none_without_history(self):
        rows = [{"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL",
                 "amt": [1] * 11}]
        mv = prov.momentum_rows(rows, tuple(f"품목{i}" for i in range(1, 11)))
        self.assertIsNone(mv["items"][0]["momentum"])     # 직전 풀월 없음
        self.assertIsNone(prov.momentum_rows([], ("a",)))


class MomentumRenderTests(unittest.TestCase):
    def _rows_by_kind(self):
        def amt(t, a, b):
            return [t, a, b] + [5] * 8
        base = [
            {"ym": "2025-05", "priod_dt": "01~31", "decile": "FULL", "amt": amt(110, 11, 11)},
            {"ym": "2026-04", "priod_dt": "01~30", "decile": "FULL", "amt": amt(120, 12, 20)},
            {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL", "amt": amt(150, 18, 21)},
            {"ym": "2025-04", "priod_dt": "01~30", "decile": "FULL", "amt": amt(100, 10, 10)},
        ]
        return {"imp_item": base, "exp_item": base}

    def test_details_panel_markers(self):
        html = prov.render_momentum(self._rows_by_kind())
        self.assertIn("<details", html)
        self.assertIn("10일 모멘텀 속보", html)
        self.assertIn("수출 품목", html)
        self.assertIn("수입 품목", html)
        self.assertIn("▲", html)                          # 가속 표식
        self.assertIn("ind-prov-tbl", html)
        self.assertIn("반도체제조용장비", html)            # 수입 품목 05 라벨
        self.assertIn("⚡", html)                          # capex 강조
        self.assertNotIn("⚠️", html)   # 수출 경고 제거 (사용자 2026-06-12)

    def test_sorted_by_absolute_value_desc(self):
        # 정렬은 최신창 절대액 큰 순. 01=반도체(작게), 02=원유(크게) 설정 →
        # 원유가 반도체보다 먼저. 전체(합계)는 맨 위 고정.
        def amt(t, *rest):
            base = [5] * 10
            for i, v in enumerate(rest):
                base[i] = v
            return [t] + base
        rows = [
            {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL", "amt": amt(150, 9, 40)},
        ]
        html = prov.render_momentum({"imp_item": rows})
        # 전체(150) 맨 위, 그 다음 절대액 큰 원유(40) → 반도체(9)
        self.assertLess(html.index("전체"), html.index(">원유"))
        self.assertLess(html.index(">원유"), html.index(">반도체</td"))

    def test_empty_when_no_rows(self):
        self.assertEqual(prov.render_momentum({}), "")
        self.assertEqual(prov.render_momentum({"imp_item": []}), "")

    def test_render_box_appends_momentum(self):
        box = prov.render_box(
            {"imp_item": {"ym": "2026-05", "window": "전월(1~말일)",
                          "total_usd": 1, "total_yoy": 1.0,
                          "items": [{"name": "반도체", "usd": 1, "yoy": 1.0}]}},
            momentum_html="<details>MOMENTUM</details>")
        self.assertIn("MOMENTUM", box)
        self.assertIn("ind-prov", box)

    def test_box_order_momentum_before_timeline(self):
        # 운영자 요청: 모멘텀이 위, 🗄 타임라인이 아래.
        box = prov.render_box(
            {"imp_item": {"ym": "2026-05", "window": "전월(1~말일)",
                          "total_usd": 1, "total_yoy": 1.0,
                          "items": [{"name": "반도체", "usd": 1, "yoy": 1.0}]}},
            momentum_html="<details>MOMENTUM_MARKER</details>")
        self.assertLess(box.index("MOMENTUM_MARKER"), box.index("잠정 타임라인"))


class MomentumArchiveHtmlTests(unittest.TestCase):
    def _rows(self):
        def amt(t, *r):
            base = [5_000_000_000] * 10
            for i, v in enumerate(r):
                base[i] = v
            return [t] + base
        return {"imp_item": [
            {"ym": "2025-05", "priod_dt": "01~31", "decile": "FULL",
             "amt": amt(110e9, 45e9, 8e9, 9e9, 7e9, 1.3e9)},
            {"ym": "2026-04", "priod_dt": "01~30", "decile": "FULL",
             "amt": amt(120e9, 55e9, 8e9, 9e9, 7e9, 1.9e9)},
            {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL",
             "amt": amt(150e9, 72e9, 8e9, 9e9, 7e9, 2.94e9)},
        ]}

    def test_archive_html_uses_inline_styles(self):
        # dashboard CSS 없는 컨텍스트(아카이브 페이지)용 인라인 스타일판
        html = prov.momentum_archive_html(self._rows())
        self.assertIn("<table", html)
        self.assertIn("border-collapse", html)        # 인라인 스타일
        self.assertIn("color:#34c759", html)          # 양수 녹색 인라인
        self.assertIn("반도체제조용장비", html)
        self.assertIn("▲", html)
        self.assertIn("⚡", html)                      # capex 강조 유지
        # ind-prov-* 클래스는 안 씀(다른 컨텍스트라)
        self.assertNotIn("class='ind-prov-tbl'", html)

    def test_archive_html_readability_dark_text_and_tabular(self):
        # 핸드오프 ①: 항목명·절대액이 진하게(본문색·tabular-nums) 보이게.
        html = prov.momentum_archive_html(self._rows())
        self.assertIn("color:#1d1d1f", html)          # 항목·절대액 본문 진한색
        self.assertIn("font-weight:600", html)        # 항목명 굵게
        self.assertIn("tabular-nums", html)           # 자릿수 정렬

    def test_archive_html_no_warn_anywhere(self):
        # ⚠️ 캡션 배지 제거 (사용자 2026-06-12) — 캡션·셀 어디에도 없음.
        rows_with_exp = {"exp_item": self._rows()["imp_item"]}
        html = prov.momentum_archive_html(rows_with_exp)
        self.assertNotIn("잠정 — 확정 전 수치", html)
        self.assertNotIn("⚠️", html)

    def test_archive_html_no_warn_when_only_imports(self):
        # 수입 그룹만이면(warn=False) 캡션 배지도 안 뜬다.
        html = prov.momentum_archive_html(self._rows())
        self.assertNotIn("잠정 — 확정 전 수치", html)

    def test_archive_html_empty_when_no_rows(self):
        self.assertEqual(prov.momentum_archive_html({}), "")


class SortToggleTests(unittest.TestCase):
    """라이브 모멘텀 토글: 절대액/모멘텀/YoY 3 모드, JS가 data-* 키로 재정렬.
    YoY·모멘텀은 부호 구분(signed) 정렬(성장 높은 순→하락 큰 순)."""

    def _rows(self):
        def amt(t, *r):
            base = [5_000_000_000] * 10
            for i, v in enumerate(r):
                base[i] = v
            return [t] + base
        return {"imp_item": [
            {"ym": "2025-05", "priod_dt": "01~31", "decile": "FULL",
             "amt": amt(110e9, 45e9, 8e9, 9e9, 7e9, 1.3e9)},
            {"ym": "2026-04", "priod_dt": "01~30", "decile": "FULL",
             "amt": amt(120e9, 55e9, 8e9, 9e9, 7e9, 1.9e9)},
            {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL",
             "amt": amt(150e9, 72e9, 8e9, 9e9, 7e9, 2.94e9)},
        ]}

    def test_panel_has_three_mode_toggle(self):
        html = prov.render_momentum(self._rows())
        self.assertIn("ind-prov-sort", html)
        self.assertIn("data-sort='usd'", html)
        self.assertIn("data-sort='mom'", html)
        self.assertIn("data-sort='yoy'", html)
        # 기본 활성=절대액
        self.assertIn("is-active' data-sort='usd'", html)

    def test_rows_carry_data_attrs_for_js_sort(self):
        html = prov.render_momentum(self._rows())
        self.assertIn("data-usd=", html)
        self.assertIn("data-mom=", html)
        self.assertIn("data-yoy=", html)
        self.assertIn('data-pin="1"', html)         # 전체 행 고정 마커

    def test_yoy_data_attr_is_signed_not_absolute(self):
        # 부호 구분: 음수 YoY는 data-yoy에 마이너스 그대로(절댓값 아님).
        # imp_item 가스(03)=YoY 음수가 나오게 구성.
        def amt(t, *r):
            base = [5_000_000_000] * 10
            for i, v in enumerate(r):
                base[i] = v
            return [t] + base
        rows = {"imp_item": [
            {"ym": "2025-05", "priod_dt": "01~31", "decile": "FULL",
             "amt": amt(110e9, 10e9, 10e9, 20e9)},
            {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL",
             "amt": amt(150e9, 18e9, 12e9, 10e9)},  # 03(기계류)=20→10 = -50%
        ]}
        html = prov.render_momentum(rows)
        self.assertIn('data-yoy="-50', html)        # 마이너스 보존(절댓값이면 "50")

    def test_archive_inline_also_has_data_attrs_but_no_toggle(self):
        # 아카이브(inline=True)는 시점 동결 기록이라 토글 미노출 — data-* 만.
        html = prov.momentum_archive_html(self._rows())
        self.assertIn("data-usd=", html)
        self.assertNotIn("ind-prov-sort-btn", html)


if __name__ == "__main__":
    unittest.main()


class MomAndStatusLabelTest(unittest.TestCase):
    """YoY 옆 MoM(전월 동순) + 잠정/확정 동적 라벨 + ⚠️ 캐비엇 제거
    (사용자 2026-06-12)."""

    def _rows(self):
        return [
            {"ym": "2026-06", "priod_dt": "01~10", "decile": "D1",
             "amt": [28_600_000_000, 11_100_000_000] + [0] * 9},
            {"ym": "2026-05", "priod_dt": "01~10", "decile": "D1",
             "amt": [26_000_000_000, 10_000_000_000] + [0] * 9},
            {"ym": "2025-06", "priod_dt": "01~10", "decile": "D1",
             "amt": [15_385_000_000, 3_630_000_000] + [0] * 9},
            # 다른 순(D2) — 동순 매칭 가드
            {"ym": "2026-05", "priod_dt": "01~20", "decile": "D2",
             "amt": [60_000_000_000, 0] + [0] * 9},
        ]

    def test_mom_same_decile_prev_month(self):
        sig = prov.latest_signal(self._rows(), ("반도체",) + ("x",) * 9)
        self.assertAlmostEqual(sig["total_yoy"], 85.9, delta=0.1)
        self.assertAlmostEqual(sig["total_mom"], 10.0, delta=0.1)  # vs 5월 D1
        self.assertAlmostEqual(sig["items"][0]["mom"], 11.0, delta=0.1)

    def test_mom_january_boundary(self):
        rows = [
            {"ym": "2026-01", "priod_dt": "01~10", "decile": "D1",
             "amt": [110] + [0] * 10},
            {"ym": "2025-12", "priod_dt": "01~10", "decile": "D1",
             "amt": [100] + [0] * 10},
        ]
        sig = prov.latest_signal(rows, ("x",) * 10)
        self.assertAlmostEqual(sig["total_mom"], 10.0, delta=0.01)

    def test_render_box_no_caveat_has_mom(self):
        sig = prov.latest_signal(self._rows(), ("반도체",) + ("x",) * 9)
        html = prov.render_box({"exp_item": sig, "imp_item": None})
        self.assertNotIn("이례적으로", html)   # 상시 캐비엇 제거
        self.assertNotIn("⚠️", html)
        self.assertIn("MoM", html)
        self.assertIn("전월 동순", html)

    def test_month_status_label(self):
        from datetime import date
        from trade.industry import _month_status_label as lbl
        self.assertEqual(lbl("2026-05", today=date(2026, 6, 12)),
                         "잠정(6/15 확정 예정)")
        self.assertEqual(lbl("2026-05", today=date(2026, 6, 15)),
                         "확정(익월 ~15일)")
        self.assertEqual(lbl("2026-12", today=date(2027, 1, 10)),
                         "잠정(1/15 확정 예정)")


class ZoneSeparationTest(unittest.TestCase):
    """10일 잠정 ↔ 월간 영역 구분 (사용자 2026-06-12 '헷갈려') — 잠정 속보
    pill 발표일정 + dashboard 조립의 zone 구분선 소스 가드."""

    def test_prov_pill_has_schedule(self):
        sig = prov.latest_signal([
            {"ym": "2026-06", "priod_dt": "01~10", "decile": "D1",
             "amt": [100] + [0] * 10},
        ], ("x",) * 10)
        html = prov.render_box({"exp_item": sig})
        self.assertIn("11일·21일·월초(전월 풀월) 발표", html)   # 어휘 분리 2026-06-13

    def test_dashboard_zone_divider_present(self):
        src = open("trade/dashboard.py", encoding="utf-8").read()
        self.assertIn("ind-zone-div", src)
        self.assertIn("월간 산업트렌드", src)
        self.assertIn("~15일 확정 정제", src)


class MomentumTableMomColumnTest(unittest.TestCase):
    """모멘텀 표 MoM(전월 동순) 컬럼 (사용자 2026-06-12 '표에도')."""

    def _rows(self):
        return [
            {"ym": "2026-06", "priod_dt": "01~10", "decile": "D1",
             "amt": [110] + [10] * 10},
            {"ym": "2026-05", "priod_dt": "01~10", "decile": "D1",
             "amt": [100] + [8] * 10},
            {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL",
             "amt": [300] + [30] * 10},
            {"ym": "2025-05", "priod_dt": "01~31", "decile": "FULL",
             "amt": [200] + [20] * 10},
            {"ym": "2025-06", "priod_dt": "01~10", "decile": "D1",
             "amt": [55] + [5] * 10},
        ]

    def test_mom_chg_computed(self):
        mv = prov.momentum_rows(self._rows(), ("반도체",) + ("x",) * 9)
        self.assertAlmostEqual(mv["items"][0]["mom_chg"], 10.0, delta=0.01)

    def test_table_has_mom_column(self):
        _, tables = prov._momentum_tables({"exp_item": self._rows()},
                                          inline=False)
        self.assertIn("<th>MoM</th>", tables)
        self.assertIn("data-momchg", tables)
        self.assertIn("data-sort='momchg'", prov.render_momentum(
            {"exp_item": self._rows()}))



class MomChgDeltaTests(unittest.TestCase):
    """ΔMoM (MoM 모멘텀, 사용자 2026-06-13) — 최신창 MoM − 직전월 같은 창 MoM
    (2026-07-23 재설계: 예전엔 '직전 풀월' 기준이라 부분누적 창일 때 비교
    기준의 창이 안 맞았음 — 사용자 '20일 vs 20일로' 요청, 항상 같은 decile
    끼리). 계절효과 미보정 캐비엇 노트 + 6컬럼(YoY·ΔYoY·MoM·ΔMoM) + 정렬키."""

    @staticmethod
    def _rows():
        n = len(prov.LABELS["exp_item"]) + 1
        def amts(total, a):
            v = [0.0] * n; v[0] = total; v[1] = a; return v
        out = []
        for ym, dec, tot, a in (
            ("2026-06", "D10", 110.0, 60.0), ("2026-05", "D10", 100.0, 50.0),
            ("2025-06", "D10", 55.0, 30.0),
            # ΔYoY·ΔMoM 비교기준 = 직전월(2026-05) 같은 창(D10) 자체의
            # YoY·MoM — 그 창의 전년 동월(2025-05 D10)과 전월(2026-04 D10).
            ("2025-05", "D10", 80.0, 40.0), ("2026-04", "D10", 125.0, 62.5),
            # FULL 행은 더는 Δ 계산에 안 쓰이지만(창 불일치 회귀 방지 —
            # 실수로 다시 섞이면 아래 숫자가 안 맞아 즉시 실패) 그대로 둠.
            ("2026-05", "FULL", 300.0, 150.0),
            ("2026-04", "FULL", 250.0, 100.0), ("2025-05", "FULL", 200.0, 100.0),
        ):
            out.append({"ym": ym, "decile": dec, "amt": amts(tot, a),
                        "priod_dt": "x"})
        return out

    def test_delta_math_and_render(self):
        mv = prov.momentum_rows(self._rows(), prov.LABELS["exp_item"])
        tot = mv["items"][0]
        self.assertAlmostEqual(tot["mom_chg"], 10.0, places=1)
        # ΔYoY: cy(100.0) − 직전월(D10) YoY(25.0, =100→80) = 75.0
        self.assertAlmostEqual(tot["momentum"], 75.0, places=1)
        # ΔMoM: mc(10.0) − 직전월(D10) MoM(-20.0, =100→125) = 30.0
        self.assertAlmostEqual(tot["momchg_delta"], 30.0, places=1)
        self.assertAlmostEqual(mv["items"][1]["momchg_delta"], 40.0, places=1)
        html = prov.render_momentum({"exp_item": self._rows()})
        self.assertIn("<th>ΔMoM</th>", html)
        self.assertIn("data-momchgd=", html)
        self.assertIn("data-sort='momchgd'", html)
        self.assertIn("계절효과 미보정", html)

    def test_headline_yoy_labeled(self):
        box = prov.render_box({"exp_item": {
            "total_usd": 28600000000, "total_yoy": 85.9, "total_mom": 59.1,
            "ym": "2026-06", "window": "1~10일", "items": []}})
        self.assertIn("YoY +85.9%", box)
        self.assertIn("MoM +59.1%", box)


class ProvFetchTimerTests(unittest.TestCase):
    """旬 집중 폴링 systemd 유닛 (사용자 2026-06-21 'data.go.kr D2 올리면 즉시
    반영'). install-trade-units.sh가 deploy/trade-bot*.{service,timer}를 자동
    발견하므로, 파일 존재 + 배선(ExecStart=fetch_provisional, OnCalendar 旬일)을
    회귀로 고정. 누가 유닛명/경로를 바꾸면 조용히 미설치되는 걸 차단."""

    def _deploy(self, name):
        from pathlib import Path
        return (Path(__file__).resolve().parents[2] / "deploy" / name).read_text(encoding="utf-8")

    def test_service_runs_fetch_provisional(self):
        svc = self._deploy("trade-bot-prov-fetch.service")
        self.assertIn("trade.scripts.fetch_provisional", svc)
        self.assertIn("WorkingDirectory=/home/higgack/stock-trade", svc)
        self.assertIn("Type=oneshot", svc)

    def test_timer_covers_sun_boundary_days(self):
        tmr = self._deploy("trade-bot-prov-fetch.timer")
        # 旬 경계일(11/21/1) + 익영업일 버퍼 2일(02/03·12/13·22/23) — 무료(관세청)는
        # 주말·휴일 순연하므로 연휴 적층 대비(사용자 2026-06-21 진흥원 무료표 확인)
        self.assertIn("01,02,03,11,12,13,21,22,23", tmr)
        self.assertIn("Asia/Seoul", tmr)
        self.assertIn("00/30", tmr)                 # 30분 간격
        self.assertIn("Unit=trade-bot-prov-fetch.service", tmr)
        self.assertIn("Persistent=true", tmr)       # 부팅 후 보정


class BalanceCardTests(unittest.TestCase):
    """무역수지 헤드라인 카드 (사용자 2026-07-01) — 수출−수입 절대 + 전년·전월
    대비 증감(억$, %아님=부호반전·0분모 회피). 유료 없이 가진 값 뺄셈으로 확보."""

    def test_balance_and_yoy_delta(self):
        # 화면값: 수출 1023억$ YoY+70.9% MoM+16.4% / 수입 661억$ YoY+30.1% MoM+8.7%.
        exp = {"total_usd": 1023e8, "total_yoy": 70.9, "total_mom": 16.4}
        imp = {"total_usd": 661e8, "total_yoy": 30.1, "total_mom": 8.7}
        cell = prov._balance_cell(exp, imp)
        self.assertIn("무역수지", cell)
        self.assertIn("362억$", cell)                 # 1023−661
        # 작년수지 역산: 1023/1.709−661/1.301 ≈ 90.5 → 증감 +271.5억$ 개선(pos).
        d = prov._balance_delta(1023e8, 70.9, 661e8, 30.1)
        self.assertAlmostEqual(d / 1e8, 271.5, delta=2)
        self.assertIn("ind-prov-pos", cell)
        self.assertIn("YoY +", cell)                  # 다른 카드와 동일 라벨
        self.assertIn("MoM +", cell)
        self.assertIn("white-space:nowrap", cell)     # '억$' 고아 줄바꿈 방지

    def test_deficit_and_missing_pct(self):
        # 적자(-50억$) + YoY% 없으면 증감 span 생략(카드는 절대 수지만).
        neg = prov._balance_cell({"total_usd": 100e8, "total_yoy": None},
                                 {"total_usd": 150e8, "total_yoy": None})
        self.assertIn("-50.0억$", neg)
        self.assertNotIn("YoY", neg)
        # 한쪽 %가 -100 → 역산 분모 0 → None(생략, 크래시 없음).
        self.assertIsNone(prov._balance_delta(1e8, -100.0, 1e8, 10.0))
        self.assertIsNone(prov._balance_delta(1e8, None, 1e8, 10.0))

    def test_card_absent_when_one_side_missing(self):
        # 수출/수입 중 하나라도 없으면 카드 생략(빈 문자열).
        self.assertEqual(prov._balance_cell({"total_usd": 1e8}, None), "")
        self.assertEqual(prov._balance_cell(None, {"total_usd": 1e8}), "")

    def test_render_box_includes_balance_after_import(self):
        def sig(t, y, m):
            return {"total_usd": t, "total_yoy": y, "total_mom": m, "items": [],
                    "ym": "2026-06", "decile": "FULL", "window": "전월(1~말일)"}
        box = prov.render_box({"exp_item": sig(1023e8, 70.9, 16.4),
                               "imp_item": sig(661e8, 30.1, 8.7)})
        self.assertIn("무역수지", box)
        self.assertLess(box.index("전체 수입"), box.index("무역수지"))


class MomentumSeriesCountTests20260820(unittest.TestCase):
    def test_summary_count_derives_from_labels(self):
        """'40개 시계열' 을 하드코딩하면 LABELS 가 바뀔 때 조용히 낡는다(#24).
        LABELS 를 줄인 상태에서 렌더하면 숫자가 따라와야 한다."""
        import unittest.mock as mock
        rows = [
            {"ym": "2025-07", "priod_dt": "01~10", "decile": "D1",
             "amt": [1000] + [100] * 10},
            {"ym": "2026-07", "priod_dt": "01~10", "decile": "D1",
             "amt": [1200] + [120] * 10},
        ]
        small = {k: v[:3] for k, v in prov.LABELS.items()}   # 4×3 = 12
        with mock.patch.object(prov, "LABELS", small):
            html = prov.render_momentum(
                {k: rows for k in ("exp_item", "imp_item",
                                   "exp_cnty", "imp_cnty")})
        if html:                       # 픽스처가 모멘텀을 못 만들면 '' — 그때도
            self.assertIn("12개 시계열", html)     # 하드코딩 40 은 절대 금지
            self.assertNotIn("40개 시계열", html)
        src = open("trade/customs_provisional.py", encoding="utf-8").read()
        self.assertNotIn("40개 시계열", src, "하드코딩 재발")


class WindowSanityProbe20260902(unittest.TestCase):
    """사용자 2026-09-02: "09/01 에 8월 전체 잠정이 나오는데 이거 8월 잠정 맞어?"

    타이밍은 정상이다(관세청은 익월 1일에 전월 1~말일 잠정을 낸다 — 모듈
    독스트링에도 그렇게 적혀 있다). 의심스러운 건 **크기**다. 그런데 '한 달
    수출이 이 정도일 리 없다'는 내 사전지식이지 측정이 아니다(#12) —
    데이터 스스로 답하게, **누적창의 불변식**으로 잰다:

      · 같은 달에서 FULL ≥ D2 ≥ D1 (월초 누적이므로 단조 증가)
      · 창 폭 비율이 대략 30:20:10 을 따른다(FULL/D2 ≈ 1.5, D2/D1 ≈ 2.0)

    이게 깨지면 원천이 누적이 아니거나 우리가 창을 잘못 고른 것이다.
    """

    def test_monotonic_windows_pass(self):
        from trade.customs_provisional import window_sanity
        rows = [{"ym": "2026-08", "decile": d, "priod_dt": p,
                 "amt": [v] + [0] * 10}
                for d, p, v in (("D1", "01~10", 190), ("D2", "01~20", 380),
                                ("FULL", "01~31", 570))]
        bad = window_sanity(rows, "2026-08")
        assert bad == [], bad

    def test_non_monotonic_is_flagged(self):
        """FULL < D2 면 누적이 아니다 — 원천 해석이 틀린 것."""
        from trade.customs_provisional import window_sanity
        rows = [{"ym": "2026-08", "decile": d, "priod_dt": p,
                 "amt": [v] + [0] * 10}
                for d, p, v in (("D2", "01~20", 380), ("FULL", "01~31", 300))]
        bad = window_sanity(rows, "2026-08")
        assert any("단조" in b for b in bad), bad

    def test_window_ratio_outlier_is_flagged(self):
        """FULL 이 D2 의 1.5배 언저리가 아니라 2.6배면 합산 의심."""
        from trade.customs_provisional import window_sanity
        rows = [{"ym": "2026-08", "decile": d, "priod_dt": p,
                 "amt": [v] + [0] * 10}
                for d, p, v in (("D1", "01~10", 190), ("D2", "01~20", 380),
                                ("FULL", "01~31", 983))]
        bad = window_sanity(rows, "2026-08")
        assert any("비율" in b for b in bad), bad

    def test_probe_prints_windows_and_yoy(self):
        """프로브는 창별 절대액과 작년 동창을 **나란히** 찍는다 — 어느 쪽이
        부풀었는지는 나란히 놔야 보인다(#51)."""
        from trade.customs_provisional import explain_windows
        rows = [{"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [98_300_000_000] + [0] * 10},
                {"ym": "2025-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [58_300_000_000] + [0] * 10}]
        out = "\n".join(explain_windows({"exp_item": rows}, "2026-08"))
        assert "2026-08" in out and "2025-08" in out, out
        assert "983" in out.replace(",", ""), out      # 억$ 로 환산해 찍는다
        assert "YoY" in out, out

    def test_probe_states_the_verdict_even_when_clean(self):
        """이상이 없어도 **판정을 말해야** 한다(2026-09-02 VM 실측).

        `--why 2026-08` 출력에 ✅/⚠️ 가 한 줄도 없었다 — 검사가 돌아서 통과한
        건지 아예 안 돈 건지 사용자가 알 수 없다(실수 #41 감사는 사실을 항상
        말할 것 · #43 침묵이 최악 · #54 대조 0건은 통과가 아니다).
        그리고 무엇을 쟀는지(실측 비율)를 같이 적어야 검산이 된다(#202).
        """
        from trade.customs_provisional import explain_windows
        rows = [{"ym": "2026-08", "decile": d, "priod_dt": p,
                 "amt": [v] + [0] * 10}
                for d, p, v in (("D1", "01~10", 21_290_000_000),
                                ("D2", "01~20", 55_210_000_000),
                                ("FULL", "01~31", 98_250_000_000))]
        out = "\n".join(explain_windows({"exp_item": rows}, "2026-08"))
        assert "✅" in out, f"통과를 말하지 않는다:\n{out}"
        # 무엇을 쟀는지 값으로 적는다 — D1→D2 2.59배, D2→FULL 1.78배.
        assert "2.59" in out and "1.78" in out, out

    def test_probe_says_undecidable_when_one_window(self):
        """창이 하나면 비율을 못 잰다 — ✅ 가 아니라 판정 불가다(#54)."""
        from trade.customs_provisional import explain_windows
        rows = [{"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [98_250_000_000] + [0] * 10}]
        out = "\n".join(explain_windows({"exp_item": rows}, "2026-08"))
        assert "✅" not in out, f"한 창뿐인데 통과라고 한다:\n{out}"
        assert "판정 불가" in out, out

    def test_top10_over_total_is_flagged(self):
        """상위10 합 > 전체 는 **불가능**하다 — 전체 칸이 틀린 것이다.

        2026-09-02: 8월 수출이 YoY +68.7% 로 나왔는데 창 불변식은 전부
        통과했다(누적 단조·창 폭 비율). 그 검사들은 한 달 **안에서**만 보므로
        전체 칸 자체가 어긋난 경우를 못 잡는다 — 구성요소(상위10)와 대조해야
        갈린다(#51 여러 축 대조).
        """
        from trade.customs_provisional import window_sanity
        rows = [{"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [100] + [20] * 10}]      # 상위10 합 200 > 전체 100
        bad = window_sanity(rows, "2026-08")
        assert any("상위10" in b for b in bad), bad

    def test_probe_prints_top10_coverage_both_years(self):
        """상위10 비중을 작년과 **나란히** 찍는다 — 전체만 부풀면 비중이
        떨어지고, 전 품목이 고루 늘었으면 비중은 그대로다(#51)."""
        from trade.customs_provisional import explain_windows
        rows = [{"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [98_250_000_000] + [4_000_000_000] * 10},
                {"ym": "2025-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [58_260_000_000] + [3_000_000_000] * 10}]
        lines = explain_windows({"exp_item": rows}, "2026-08")
        # 라벨 없이 두 값만 있으면 어느 쪽이 올해인지 알 수 없다 — 그 줄
        # **하나**를 집어서 본다(#75 옆 값이 대신 만족시키지 않게).
        line = next((x for x in lines if "상위10" in x), "")
        assert line, lines
        assert "40.7%" in line, line          # 올해 40.0/98.25
        assert "작년" in line and "51.5%" in line, line   # 작년 30.0/58.26

    # ── 2026-09-02 독립 리뷰가 잡은 눈멂 7건 ────────────────────────────
    def _row(self, ym, dec, total, item=None, n=10):
        amt = [total] + [(item if item is not None else total * 0.06)] * n
        return {"ym": ym, "decile": dec,
                "priod_dt": {"D1": "01~10", "D2": "01~20"}.get(dec, "01~31"),
                "amt": amt}

    def test_share_collapse_is_judged_not_just_printed(self):
        """상위10 비중을 **찍기만** 하면 #274 가 근거로 든 그 시나리오에
        여전히 ✅ 가 찍힌다 — 전체만 부풀어도 창 불변식은 전부 통과한다."""
        from trade.customs_provisional import explain_windows
        rows = [self._row("2026-08", "D1", 21_290_000_000, 1_500_000_000),
                self._row("2026-08", "D2", 55_210_000_000, 3_900_000_000),
                self._row("2026-08", "FULL", 98_250_000_000, 4_150_000_000),
                self._row("2025-08", "D1", 14_650_000_000, 1_500_000_000),
                self._row("2025-08", "D2", 35_390_000_000, 3_900_000_000),
                self._row("2025-08", "FULL", 58_260_000_000, 4_150_000_000)]
        out = "\n".join(explain_windows({"exp_item": rows}, "2026-08"))
        assert "✅" not in out, f"비중이 무너졌는데 정상이라 한다:\n{out}"
        assert "비중" in out, out

    def test_verdict_names_every_check_that_ran(self):
        """✅ 는 **무엇을 쟀는지** 말해야 한다 — 상위10 검사가 돌았는지
        알 수 없으면 침묵이 한 층 아래에서 재발한다."""
        from trade.customs_provisional import explain_windows
        rows = [self._row("2026-08", "D1", 200), self._row("2026-08", "D2", 400),
                self._row("2026-08", "FULL", 600),
                self._row("2025-08", "D1", 100), self._row("2025-08", "D2", 200),
                self._row("2025-08", "FULL", 300)]
        out = "\n".join(explain_windows({"exp_item": rows}, "2026-08"))
        assert "✅" in out, out
        assert "상위10" in out.split("✅")[1], f"✅ 가 상위10 검사를 안 말한다:\n{out}"

    def test_violation_never_gets_a_pass_line(self):
        """⚠️ 와 ✅ 가 같이 찍히면 안 된다(`elif not bad` → `else` 뮤테이션)."""
        from trade.customs_provisional import explain_windows
        rows = [self._row("2026-08", "D2", 400), self._row("2026-08", "FULL", 300)]
        out = "\n".join(explain_windows({"exp_item": rows}, "2026-08"))
        assert "⚠️" in out and "✅" not in out, out

    def test_zero_total_with_filled_top10_is_flagged(self):
        """전체=0 인데 상위10 이 채워진 행은 가장 확실한 손상인데
        `not amt[0]` 가드에 걸려 검사가 통째로 안 돌았다."""
        from trade.customs_provisional import window_sanity
        rows = [{"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [0] + [4_000_000_000] * 10}]
        bad = window_sanity(rows, "2026-08")
        assert bad, "전체 0 · 상위10 채움인데 이상 없다고 한다"

    def test_undecidable_reason_states_the_real_condition(self):
        """❓ 사유가 '창이 하나뿐'으로 고정이라, 표에 3줄이 찍힌 뒤 자기 표를
        뒤집는다 — 실제 조건은 '금액이 있는 창이 2개 미만'이다."""
        from trade.customs_provisional import explain_windows
        rows = [self._row("2026-08", "D1", 0), self._row("2026-08", "D2", 0),
                self._row("2026-08", "FULL", 600)]
        out = "\n".join(explain_windows({"exp_item": rows}, "2026-08"))
        assert "하나뿐" not in out, f"표에 3줄인데 '하나뿐'이라 한다:\n{out}"

    def test_all_zero_month_is_a_failure_not_undecidable(self):
        """전 창이 0 이면 판정 불가가 아니라 손상이다(#54)."""
        from trade.customs_provisional import explain_windows
        rows = [self._row("2026-08", d, 0) for d in ("D1", "D2", "FULL")]
        out = "\n".join(explain_windows({"exp_item": rows}, "2026-08"))
        assert "❌" in out, out

    def test_duplicate_decile_is_flagged(self):
        """중복 decile 이면 표(첫 행)와 판정(마지막 행)이 다른 값을 본다 —
        사용자의 눈검산과 ✅ 줄의 실측배수가 어긋난다."""
        from trade.customs_provisional import window_sanity
        rows = [self._row("2026-08", "D2", 400), self._row("2026-08", "D2", 700),
                self._row("2026-08", "FULL", 600)]
        bad = window_sanity(rows, "2026-08")
        assert any("중복" in b for b in bad), bad

    def test_duplicate_decile_ratio_uses_the_row_the_table_shows(self):
        """표는 첫 행을 찍는데 판정이 마지막 행을 보면, ✅ 줄의 실측배수가
        사용자의 눈검산과 어긋난다(표 552.1→982.5 인데 판정 1.27배)."""
        from trade.customs_provisional import window_ratios
        rows = [self._row("2026-08", "D2", 400), self._row("2026-08", "D2", 700),
                self._row("2026-08", "FULL", 600)]
        got = {(a, b): act for a, b, act, _exp in window_ratios(rows, "2026-08")}
        assert got[("D2", "FULL")] == 600 / 400, got   # 첫 행(400) 기준

    def test_verdict_reports_the_measured_share_shift(self):
        """✅ 는 상위10 비중을 **얼마로 쟀는지** 적어야 한다 — 이름만 적으면
        비중 계산을 지워도 통과한다(#75)."""
        from trade.customs_provisional import explain_windows
        # 항목 10개 합이 전체의 60% 가 되게 — 두 해 비중이 같아 shift 1.00
        rows = [self._row("2026-08", "D1", 200, 12),
                self._row("2026-08", "D2", 400, 24),
                self._row("2026-08", "FULL", 600, 36),
                self._row("2025-08", "D1", 100, 6),
                self._row("2025-08", "D2", 200, 12),
                self._row("2025-08", "FULL", 300, 18)]
        line = next(x for x in explain_windows({"exp_item": rows}, "2026-08")
                    if "✅" in x and "누적" in x)
        assert "상위10 비중 작년 대비" in line and "배" in line, line
        assert "1.00~1.00배" in line, line

    def test_cross_kind_totals_must_agree(self):
        """exp_item 전체 == exp_cnty 전체(같은 총수출액) — 문턱 없는 정확
        불변식이라 전체 칸 이상을 가장 직접 잰다."""
        from trade.customs_provisional import cross_kind_mismatch
        a = [{"ym": "2026-08", "decile": "FULL", "amt": [100] + [6] * 10}]
        b = [{"ym": "2026-08", "decile": "FULL", "amt": [169] + [6] * 10}]
        bad = cross_kind_mismatch({"exp_item": a, "exp_cnty": b}, "2026-08")
        assert bad, "두 breakdown 총계가 1.69배 다른데 통과한다"
        ok = cross_kind_mismatch({"exp_item": a, "exp_cnty": a}, "2026-08")
        assert ok == [], ok

    def test_amt_missing_does_not_crash_the_probe(self):
        """손상 데이터를 보려는 도구가 손상 데이터에서 먼저 죽으면 안 된다."""
        from trade.customs_provisional import explain_windows
        rows = [{"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31"},
                {"ym": "2026-08", "decile": "D1", "priod_dt": "01~10", "amt": []}]
        out = "\n".join(explain_windows({"exp_item": rows}, "2026-08"))
        assert out, out

    def test_item_breakdown_attributes_the_increase(self):
        """'어디서 늘었나' 는 품목별 **증가 기여**로만 답한다(사용자 요청
        2026-09-02). 총계만 보면 +68.7% 가 어디서 왔는지 알 수 없다."""
        from trade.customs_provisional import item_breakdown
        # 최대 기여를 **두 번째 슬롯**에 둔다 — 첫 슬롯이면 정렬을 지워도
        # 통과해 아무것도 안 재는 테스트가 된다(#91c).
        cur = {"ym": "2026-08", "decile": "FULL",
               "amt": [1000] + [100, 400] + [10] * 8}
        prv = {"ym": "2025-08", "decile": "FULL",
               "amt": [600] + [100, 100] + [10] * 8}
        labels = tuple(f"품목{i}" for i in range(1, 11))
        rows = item_breakdown(cur, prv, labels)
        top = rows[0]
        assert top["name"] == "품목2", [r["name"] for r in rows]
        assert top["delta"] == 300 and abs(top["yoy"] - 300.0) < 1e-6, top
        # 기여율은 전체 증가(400) 대비 — 눈으로 검산된다(#33).
        assert abs(top["share_of_delta"] - 0.75) < 1e-6, top
        # 상위10 + 나머지 = 전체. 항등식이 깨지면 표가 거짓말한다.
        assert abs(sum(r["delta"] for r in rows) - 400) < 1e-6, rows
        assert any(r["name"] == "나머지" for r in rows), rows

    def test_item_breakdown_without_last_year_says_so(self):
        """작년이 없으면 YoY 를 지어내지 말 것 — None 이고 기여율도 없다."""
        from trade.customs_provisional import item_breakdown
        cur = {"ym": "2026-08", "decile": "FULL", "amt": [1000] + [50] * 10}
        rows = item_breakdown(cur, None, tuple(f"품목{i}" for i in range(1, 11)))
        assert rows and all(r["yoy"] is None for r in rows), rows

    def test_probe_prints_item_attribution(self):
        """진단이 품목별 기여를 찍는다 — 계산해 놓고 안 보여주면 없는 것과
        같다(#123·#129·#131·#189·#228 의 반복)."""
        from trade.customs_provisional import explain_windows
        cur = [self._row("2026-08", "FULL", 1000, 40),
               self._row("2025-08", "FULL", 600, 20)]
        out = "\n".join(explain_windows({"exp_item": cur}, "2026-08"))
        assert "반도체" in out, out          # LABELS['exp_item'][0]
        assert "기여" in out, out

    def test_declining_month_does_not_invert_contribution(self):
        """총계가 줄어든 달에 '증가 기여'라고 적으면 부호가 뒤집혀 읽힌다 —
        늘어난 품목이 기여 -200% 로 찍혔다(2026-09-02 독립 리뷰)."""
        from trade.customs_provisional import explain_windows
        cur = {"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
               "amt": [900] + [300, 100] + [50] * 8}
        prv = {"ym": "2025-08", "decile": "FULL", "priod_dt": "01~31",
               "amt": [1000] + [100, 300] + [50] * 8}
        out = "\n".join(explain_windows({"exp_item": [cur, prv]}, "2026-08"))
        assert "증가 기여" not in out, f"줄어든 달인데 '증가 기여'라 한다:\n{out}"
        assert "감소" in out, out

    def test_window_pick_matches_the_single_source(self):
        """창 선택이 _decile_amounts(전체 0 은 건너뜀)와 갈리면, 손상된 FULL 을
        집어 '나머지 -400억 · 기여 +111%' 를 손상 경고보다 **먼저** 찍는다."""
        from trade.customs_provisional import explain_windows
        rows = [{"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [0] + [4_000_000_000] * 10},
                {"ym": "2026-08", "decile": "D2", "priod_dt": "01~20",
                 "amt": [50_000_000_000] + [3_000_000_000] * 10}]
        out = "\n".join(explain_windows({"exp_item": rows}, "2026-08"))
        assert "[FULL 품목" not in out, f"전체 0 인 창을 품목 분해에 썼다:\n{out}"

    def test_prior_year_zero_total_is_explained_not_blank(self):
        """작년 전체만 0 이고 항목은 채워져 있으면 '작년 —' 로 조용히 비우지
        말고 왜 비었는지 말할 것(#43·#131)."""
        from trade.customs_provisional import explain_windows
        cur = {"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
               "amt": [90_000_000_000] + [4_000_000_000] * 10}
        prv = {"ym": "2025-08", "decile": "FULL", "priod_dt": "01~31",
               "amt": [0] + [3_000_000_000] * 10}
        out = "\n".join(explain_windows({"exp_item": [cur, prv]}, "2026-08"))
        assert "⚠️" in out, f"작년 전체가 0 인데 아무 말도 없다:\n{out}"

    def test_header_names_the_right_breakdown(self):
        """국가별 계열에 '품목별'이라고 적으면 화면이 거짓말한다(#34)."""
        from trade.customs_provisional import explain_windows
        rows = [{"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [90_000_000_000] + [4_000_000_000] * 10}]
        # 페이지 전체 grep 은 교차 대조 문구('품목별·국가별 짝이 없다')가
        # 대신 만족시킨다 — 그 **헤더 줄 하나**를 잘라서 본다(#55).
        head = next(x for x in explain_windows({"exp_cnty": rows}, "2026-08")
                    if x.lstrip().startswith("[FULL"))
        assert "품목별" not in head, head
        assert "국가별" in head, head

    def test_unverified_slot_order_is_disclosed(self):
        """슬롯 순서가 원천 확인이 안 된 계열은 이름을 사실처럼 적지 말 것
        (#165 재지 않은 귀속을 단정하지 말 것). 소스 주석이 exp_cnty 를
        '미리보기 추가 검증 권장'이라 적어 두었다."""
        from trade.customs_provisional import explain_windows, LABELS_VERIFIED
        rows = [{"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [90_000_000_000] + [4_000_000_000] * 10}]
        head = next(x for x in explain_windows({"exp_cnty": rows}, "2026-08")
                    if x.lstrip().startswith("[FULL"))
        assert "exp_cnty" not in LABELS_VERIFIED
        assert "미검증" in head, head

    def test_contribution_column_is_actually_printed(self):
        """헤더의 '기여' 가 단언을 대신 만족시키면, 이 기능이 존재하는 이유인
        그 칸을 지워도 통과한다(#75 — 리뷰가 뮤테이션으로 확인)."""
        from trade.customs_provisional import explain_windows
        cur = {"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
               "amt": [100_000_000_000] + [40_000_000_000] + [1_000_000_000] * 9}
        prv = {"ym": "2025-08", "decile": "FULL", "priod_dt": "01~31",
               "amt": [60_000_000_000] + [10_000_000_000] + [1_000_000_000] * 9}
        line = next(x for x in explain_windows({"exp_item": [cur, prv]}, "2026-08")
                    if "반도체" in x)
        assert "기여" in line, line
        assert "%" in line.split("기여")[1], line

    def test_no_prior_year_does_not_promise_contribution(self):
        """작년이 없으면 기여를 못 낸다 — 헤더가 '기여'를 약속하면 전 칸이
        '—' 인 표를 보고 사용자가 결함으로 읽는다(#43)."""
        from trade.customs_provisional import explain_windows
        rows = [{"ym": "2026-08", "decile": "FULL", "priod_dt": "01~31",
                 "amt": [90_000_000_000] + [4_000_000_000] * 10}]
        head = next(x for x in explain_windows({"exp_item": rows}, "2026-08")
                    if x.lstrip().startswith("[FULL"))
        assert "기여" not in head, head
        assert "작년 없음" in head, head

    def test_cli_dispatches_to_the_probe(self, ):
        """정의만 있고 배선이 없으면 아무도 못 쓴다(#120). `--why` 는 읽기
        전용이라 수집(run)으로 흘러가면 안 된다."""
        import io
        import contextlib
        import sys as _sys
        from unittest import mock
        from trade.scripts import fetch_provisional as fp
        ran = []
        buf = io.StringIO()
        with mock.patch.object(fp, "run", lambda **k: ran.append(k) or 0), \
                mock.patch.object(fp.prov, "load_rows",
                                  lambda c: {"exp_item": []}), \
                mock.patch.object(fp.customs, "open_db",
                                  lambda *a, **k: mock.MagicMock()), \
                mock.patch.object(_sys, "argv",
                                  ["fetch_provisional", "--why", "2026-08"]), \
                contextlib.redirect_stdout(buf):
            rc = fp.main()
        out = buf.getvalue()
        assert rc == 0, out
        assert not ran, "진단인데 수집이 돌았다"
        assert "잠정 창 진단" in out and _sys.executable in out, out
