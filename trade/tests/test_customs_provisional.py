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


if __name__ == "__main__":
    unittest.main()
