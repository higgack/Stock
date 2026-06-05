"""dashboard ↔ price_provider — BeOn 관련종목 EOD 시세 칩 연결 테스트."""

import unittest
from unittest import mock

from trade import dashboard, price_provider


def _q(code, name, price, pct):
    return price_provider.Quote(code, name, price, 0, pct, price, "KRW", "t")


class StockQuotesMapTests(unittest.TestCase):
    def test_maps_beon_stocks_to_eod(self):
        payload = [{"stocks": ["이오테크닉스", "삼성전자"]},
                   {"stocks": ["삼성전자"]}, {"stocks": []}]
        fake = {"이오테크닉스": _q("357780", "이오테크닉스", 120000, 1.7),
                "삼성전자": _q("005930", "삼성전자", 351500, -2.5)}
        with mock.patch.object(price_provider, "provider_active", return_value=True), \
             mock.patch.object(price_provider, "get_quotes_by_name", return_value=fake):
            out = dashboard._stock_quotes_for(payload)
        self.assertEqual(out["삼성전자"], {"p": 351500, "c": -2.5})
        self.assertEqual(out["이오테크닉스"]["c"], 1.7)

    def test_only_matched_names_included(self):
        # 못 찾은 종목은 맵에서 빠짐(칩은 이름만 렌더).
        with mock.patch.object(price_provider, "provider_active", return_value=True), \
             mock.patch.object(price_provider, "get_quotes_by_name",
                               return_value={"삼성전자": _q("005930", "삼성전자", 1, 0.0)}):
            out = dashboard._stock_quotes_for([{"stocks": ["삼성전자", "없는회사"]}])
        self.assertIn("삼성전자", out)
        self.assertNotIn("없는회사", out)

    def test_off_returns_empty(self):
        with mock.patch.object(price_provider, "provider_active", return_value=False):
            self.assertEqual(
                dashboard._stock_quotes_for([{"stocks": ["삼성전자"]}]), {})

    def test_no_stocks_returns_empty(self):
        with mock.patch.object(price_provider, "provider_active", return_value=True):
            self.assertEqual(dashboard._stock_quotes_for([{"stocks": []}]), {})

    def test_exception_safe(self):
        with mock.patch.object(price_provider, "provider_active",
                               side_effect=RuntimeError("boom")):
            self.assertEqual(dashboard._stock_quotes_for([{"stocks": ["x"]}]), {})


class FrontendWiringTests(unittest.TestCase):
    def test_js_has_stockpx_helper_and_chip_uses_it(self):
        self.assertIn("function stockPx(", dashboard._JS)
        self.assertIn("STOCK_QUOTES", dashboard._JS)
        # 관련종목 칩이 stockPx(s)를 부른다
        self.assertIn("esc(s)+stockPx(s)", dashboard._JS)

    def test_css_has_stock_px_classes(self):
        self.assertIn(".stock-px", dashboard._CSS)
        self.assertIn(".stock-px.up", dashboard._CSS)
        self.assertIn(".stock-px.down", dashboard._CSS)

    def test_company_section_header_shows_eod_price(self):
        # 회사별 섹션 헤더에 그 회사 EOD 가격(sectionStockPx) — 모달과 둘 다.
        self.assertIn("function sectionStockPx(", dashboard._JS)
        self.assertIn(".section-px", dashboard._CSS)
        # renderSection이 headerExtra 슬롯을 받고, 회사별이 sectionStockPx 전달
        self.assertIn("(headerExtra||'')", dashboard._JS)
        self.assertIn("sectionStockPx(name)", dashboard._JS)


if __name__ == "__main__":
    unittest.main()
