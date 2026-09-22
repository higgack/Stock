"""dashboard ↔ price_provider — BeOn 관련종목 EOD 시세 칩 연결 테스트."""

import unittest
from unittest import mock

from trade import dashboard, price_provider
from trade.tests.test_stock_link import _js_fn  # 중괄호로 함수 본문만(#60)


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
        # 2026-09-22: `s`(6자리 KRX 코드)가 하나 더 실린다 — 칩·섹션 제목이
        # `/lookup/<코드>` 딥링크를 만드는 재료다(#399). 옛 계약("칸은 p·c
        # 둘뿐")은 지우지 말고 **다시 쓴다**(#222) — 남는 보장은 값 자체와
        # "합성키(`nm:…`)는 코드로 싣지 않는다"(#34, 아래 별도 테스트).
        self.assertEqual({k: out["삼성전자"][k] for k in ("p", "c")},
                         {"p": 351500, "c": -2.5})
        self.assertEqual(out["삼성전자"]["s"], "005930")
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

    def test_splits_composites_and_cache_only_fetch_false(self):
        # 복합명 분리(split_names) + 렌더는 캐시만(fetch=False) — 동기 API 안 함.
        with mock.patch.object(price_provider, "provider_active", return_value=True), \
             mock.patch.object(price_provider, "get_quotes_by_name") as gq:
            gq.return_value = {"삼성전자": _q("005930", "삼성전자", 100, 1.0)}
            dashboard._stock_quotes_for(
                [{"stocks": ["GST / 유니셈 등", "삼성전자"]}])
            args, kwargs = gq.call_args
            passed = args[0]
            self.assertIn("GST", passed)
            self.assertIn("유니셈", passed)
            self.assertIn("삼성전자", passed)
            self.assertFalse(kwargs.get("fetch", True))   # 렌더는 fetch=False


class FrontendWiringTests(unittest.TestCase):
    def test_js_has_stockpx_helper_and_chip_uses_it(self):
        """관련종목 칩이 이름과 시세 칩을 같이 낸다.

        2026-09-22: 옛 판은 `"esc(s)+stockPx(s)"` 라는 **소스 문자열**을 단언해,
        칩에 딥링크를 붙이며 `slLink(s,stockHref(s))+stockPx(s)` 로 바뀌자
        멀쩡한 코드를 틀렸다고 했다(#19 — 값이 걸린 곳은 동작으로 재야 한다).
        계약은 "칩이 이름 + 시세를 낸다" 이지 그 표기가 아니다(#222).
        """
        self.assertIn("function stockPx(", dashboard._JS)
        self.assertIn("STOCK_QUOTES", dashboard._JS)
        chip = _js_fn(dashboard._JS, "renderModalCard")
        self.assertIn("stockPx(s)", chip, "칩이 시세를 안 부른다")
        self.assertIn("class=\"stock\"", chip)

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

    def test_js_composite_fallback_lookup(self):
        # 복합명('A / B 등') 칩/헤더는 첫 종목으로 fallback 조회. 폴백 순서:
        # 원본 → '관련종목 : ' 제거 → 슬래시/콤마/중점 첫 토큰 → 공백 첫 단어.
        self.assertIn("function pxLookup(", dashboard._JS)
        self.assertIn("관련종목", dashboard._JS)
        self.assertIn("split(/[\\/·,]/)", dashboard._JS)
        # stockPx/sectionStockPx가 pxLookup 사용
        self.assertIn("var q=pxLookup(name)", dashboard._JS)


if __name__ == "__main__":
    unittest.main()
