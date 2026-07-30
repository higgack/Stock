"""bot.chart_data — 야후 미제공 종목 일봉 폴백(네이버/KIS) 순수 파서 가드.

네트워크 경로(_fetch_naver_daily/_fetch_kis_daily)는 외부 API라 VM 전용·샌드박스
미검증이지만, JSON→rows→DataFrame→payload 변환 휴리스틱은 합성 데이터로 검증
가능(검증 게이트). 실 네이버/KIS 양식이 다르면 graceful None 으로 폴스루(회귀 0),
VM probe 로 필드명 확정 후 정밀화.
"""

import unittest

from bot import chart_data as C


class ParseNaverDailyTests(unittest.TestCase):
    def test_list_shape(self):
        raw = [
            {"localDate": "20250602", "openPrice": "100", "highPrice": "110",
             "lowPrice": "95", "closePrice": "105", "accumulatedTradingVolume": "1000"},
            {"localDate": "20250603", "openPrice": "105", "highPrice": "115",
             "lowPrice": "104", "closePrice": "112", "accumulatedTradingVolume": "1200"},
        ]
        rows = C._parse_naver_daily(raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "20250602")
        self.assertEqual(rows[0]["close"], "105")
        self.assertEqual(rows[1]["high"], "115")

    def test_dict_wrapped(self):
        raw = {"priceInfos": [
            {"localDate": "20250602", "closePrice": 105},
            {"localDate": "20250603", "closePrice": 112}]}
        rows = C._parse_naver_daily(raw)
        self.assertEqual([r["date"] for r in rows], ["20250602", "20250603"])

    def test_garbage(self):
        self.assertEqual(C._parse_naver_daily(None), [])
        self.assertEqual(C._parse_naver_daily({"x": 1}), [])
        self.assertEqual(C._parse_naver_daily([{"no": "date"}]), [])


class RowsToDailyDfTests(unittest.TestCase):
    def test_sorted_deduped(self):
        rows = [
            {"date": "2025-06-03", "open": 105, "high": 115, "low": 104, "close": 112, "volume": 1200},
            {"date": "2025-06-02", "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
            {"date": "2025-06-03", "open": 106, "high": 116, "low": 105, "close": 113, "volume": 1300},  # 중복 → last
        ]
        df = C._rows_to_daily_df(rows)
        self.assertEqual(len(df), 2)                       # 중복 제거
        self.assertEqual(list(df["Close"]), [105.0, 113.0])  # 오름차순 + last 우선
        self.assertEqual(df.index[0].strftime("%Y-%m-%d"), "2025-06-02")

    def test_missing_ohl_defaults_to_close(self):
        rows = [{"date": "20250602", "close": 105},
                {"date": "20250603", "close": 112}]
        df = C._rows_to_daily_df(rows)
        self.assertEqual(list(df["Open"]), [105.0, 112.0])   # 결측 O/H/L = 종가
        self.assertEqual(list(df["Volume"]), [0.0, 0.0])     # 결측 거래량 = 0

    def test_too_few(self):
        self.assertIsNone(C._rows_to_daily_df([{"date": "20250602", "close": 1}]))
        self.assertIsNone(C._rows_to_daily_df([]))


class DfToPayloadTests(unittest.TestCase):
    def test_payload_shape(self):
        rows = [{"date": f"2025-06-{d:02d}", "close": 100 + d} for d in range(2, 12)]
        df = C._rows_to_daily_df(rows)
        # 비-KR(US) → '$' decimals 2. ticker 인자로 events fetch 시도하나 try/except.
        p = C._df_to_daily_payload(df, "TESTX", "1y")
        self.assertEqual(p["interval"], "1d")
        self.assertEqual(p["period"], "1y")
        self.assertEqual(len(p["times"]), len(p["close"]))
        self.assertEqual(p["times"][0], "2025-06-02")
        self.assertIn("ema21", p)

    def test_none_passthrough(self):
        self.assertIsNone(C._df_to_daily_payload(None, "X", "1y"))


class PeriodRangeTests(unittest.TestCase):
    def test_ytd_and_default(self):
        from datetime import datetime
        s, e = C._period_start_end("ytd")
        self.assertEqual((s.month, s.day), (1, 1))
        self.assertEqual(s.year, datetime.now().year)
        s2, _ = C._period_start_end("1y")
        self.assertGreater((e - s2).days, 300)   # ~1년


if __name__ == "__main__":
    unittest.main()
