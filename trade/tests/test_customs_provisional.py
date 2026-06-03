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

    def test_export_cells_warned_with_caveat(self):
        # B안: 수출 절대액 ⚠️ 표식 + 캡션(확정 시 조정 가능).
        html = prov.render_box(self._signals())
        self.assertIn("⚠️", html)
        self.assertIn("ind-prov-warn", html)         # 수출 셀 경고 테두리
        self.assertIn("ind-prov-cav", html)          # 캡션
        self.assertIn("추세로 참고", html)

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
        self.assertIn("⚠️", html)                          # 수출 절대액 경고

    def test_sorted_by_momentum_accel_first(self):
        # imp_item 라벨에서 품목 매핑이 아니라 실제 LABELS이므로, 가속 항목이
        # 둔화 항목보다 앞서는지 위치로 확인(02=원유 가속, 03=기계류 둔화 설정).
        def amt(t, *rest):
            base = [5] * 10
            for i, v in enumerate(rest):
                base[i] = v
            return [t] + base
        rows = [
            {"ym": "2025-04", "priod_dt": "01~30", "decile": "FULL", "amt": amt(100, 5, 10, 10)},
            {"ym": "2025-05", "priod_dt": "01~31", "decile": "FULL", "amt": amt(110, 5, 11, 11)},
            {"ym": "2026-04", "priod_dt": "01~30", "decile": "FULL", "amt": amt(120, 5, 12, 20)},
            {"ym": "2026-05", "priod_dt": "01~31", "decile": "FULL", "amt": amt(150, 5, 30, 21)},
        ]
        html = prov.render_momentum({"imp_item": rows})
        # 원유(02, 가속)가 기계류(03, 둔화)보다 먼저 등장
        self.assertLess(html.index("원유"), html.index("기계류"))

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


if __name__ == "__main__":
    unittest.main()
