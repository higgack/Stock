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
