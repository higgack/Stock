"""trade.price_provider — provider-neutral 시세(KIS) READ-ONLY 레이어 테스트.

라이브 키 없이 transport 주입으로 토큰·현재가 파싱·캐시·기본 OFF를 검증.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from trade import price_provider as pp


def _kis_price_resp(prpr, sdpr, name="삼성전자", ctrt=None):
    out = {"stck_prpr": str(prpr), "stck_sdpr": str(sdpr), "hts_kor_isnm": name}
    if ctrt is not None:
        out["prdy_ctrt"] = str(ctrt)
    return {"output": out, "rt_cd": "0"}


class FakeKIS:
    """transport 스텁 — 토큰 발급 + inquire-price 응답."""
    def __init__(self, prices: dict, *, token="TOK", expires_in=86400):
        self.prices = prices              # {code: (prpr, sdpr, name)}
        self.token = token
        self.expires_in = expires_in
        self.calls = []

    def __call__(self, method, url, *, headers, body=None):
        self.calls.append((method, url))
        if url.endswith("/oauth2/tokenP"):
            return {"access_token": self.token, "expires_in": self.expires_in}
        if "inquire-price" in url:
            code = url.split("fid_input_iscd=")[1][:6]
            if code not in self.prices:
                return {"output": {}}
            prpr, sdpr, name = self.prices[code]
            return _kis_price_resp(prpr, sdpr, name)
        return {}


class _TmpEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._p = [
            mock.patch.object(pp, "_TOKEN_PATH", d / ".kis_token.json"),
            mock.patch.object(pp, "_QUOTE_CACHE", d / "kis_quotes.json"),
            mock.patch.object(pp, "_CODE_CACHE", d / "stock_codes.json"),
            mock.patch.object(pp, "_DATA_DIR", d),
        ]
        for p in self._p:
            p.start()
        # clear=True로 격리 — 앰비언트 TRADE_DATA_GO_KR_KEY가 있으면 auto가
        # dataportal로 가버려 KIS 경로 테스트가 깨지므로(전체 스위트 오염 방지).
        self.env = mock.patch.dict(os.environ, {
            "TRADE_KIS_APPKEY": "AK", "TRADE_KIS_APPSECRET": "AS",
            "TRADE_PRICE_PROVIDER": "auto", "TRADE_KIS_THROTTLE_MS": "0",
        }, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for p in self._p:
            p.stop()
        self.tmp.cleanup()


class ProviderSelectTests(unittest.TestCase):
    def test_off_when_no_keys(self):
        with mock.patch.dict(os.environ, {"TRADE_PRICE_PROVIDER": "auto"}, clear=True):
            self.assertEqual(pp._provider(), "none")
            self.assertFalse(pp.provider_active())
            # 키 없으면 외부 호출 0 — 빈 결과
            self.assertEqual(pp.get_quotes(["005930"]), {})

    def test_kis_when_keys_present(self):
        with mock.patch.dict(os.environ, {
                "TRADE_KIS_APPKEY": "AK", "TRADE_KIS_APPSECRET": "AS",
                "TRADE_PRICE_PROVIDER": "auto"}, clear=True):
            self.assertEqual(pp._provider(), "kis")
            self.assertTrue(pp.provider_active())

    def test_explicit_none_forces_off(self):
        with mock.patch.dict(os.environ, {
                "TRADE_KIS_APPKEY": "AK", "TRADE_KIS_APPSECRET": "AS",
                "TRADE_PRICE_PROVIDER": "none"}, clear=True):
            self.assertEqual(pp._provider(), "none")


class QuoteParseTests(_TmpEnv):
    def test_parse_and_derive_change(self):
        fake = FakeKIS({"005930": (74000, 72000, "삼성전자")})
        q = pp.get_quote("005930", transport=fake)
        self.assertIsNotNone(q)
        self.assertEqual(q.symbol, "005930")
        self.assertEqual(q.name, "삼성전자")
        self.assertEqual(q.price, 74000)
        self.assertEqual(q.prev_close, 72000)
        self.assertEqual(q.change, 2000)                 # 74000-72000
        self.assertAlmostEqual(q.change_pct, 2000/72000*100, places=3)
        self.assertEqual(q.currency, "KRW")

    def test_symbol_normalized_to_6digit(self):
        fake = FakeKIS({"005930": (100, 100, "X")})
        q = pp.get_quote("5930", transport=fake)          # 패딩
        self.assertIsNotNone(q)
        self.assertEqual(q.symbol, "005930")

    def test_missing_symbol_returns_none(self):
        fake = FakeKIS({"005930": (1, 1, "X")})
        self.assertIsNone(pp.get_quote("999999", transport=fake))

    def test_zero_price_dropped(self):
        fake = FakeKIS({"005930": (0, 0, "X")})
        self.assertIsNone(pp.get_quote("005930", transport=fake))

    def test_multi_symbol_batch(self):
        fake = FakeKIS({"005930": (74000, 72000, "삼성전자"),
                        "000660": (180000, 175000, "SK하이닉스")})
        out = pp.get_quotes(["005930", "000660"], transport=fake)
        self.assertEqual(set(out), {"005930", "000660"})
        self.assertEqual(out["000660"].name, "SK하이닉스")


class TokenCacheTests(_TmpEnv):
    def test_token_cached_not_reissued(self):
        # ttl_s=0으로 시세는 매번 재호출시켜 토큰 경로를 강제로 타게 한 뒤,
        # 토큰은 만료 멀어 1회만 발급되는지 확인(시세 캐시와 분리 검증).
        fake = FakeKIS({"005930": (1, 1, "X")}, expires_in=86400)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        token_calls = [c for c in fake.calls if c[1].endswith("/oauth2/tokenP")]
        self.assertEqual(len(token_calls), 1)             # 토큰 1회만 발급

    def test_expired_token_reissued(self):
        # 토큰 만료 임박(expires_in=10 → 만료 60s 전 재사용 규칙상 즉시 stale).
        fake = FakeKIS({"005930": (1, 1, "X")}, expires_in=10)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        token_calls = [c for c in fake.calls if c[1].endswith("/oauth2/tokenP")]
        self.assertEqual(len(token_calls), 2)             # 재발급

    def test_missing_expires_in_defaults_24h_not_instant_expire(self):
        # expires_in 누락 시 24h 기본 → 두 번째 호출도 토큰 재발급 안 함.
        fake = FakeKIS({"005930": (1, 1, "X")})
        fake.expires_in = None
        # FakeKIS는 expires_in 키를 항상 넣으니, 누락 응답을 직접 흉내.
        def tp(method, url, *, headers, body=None):
            if url.endswith("/oauth2/tokenP"):
                return {"access_token": "TOK"}            # expires_in 없음
            return fake(method, url, headers=headers, body=body)
        pp.get_quotes(["005930"], transport=tp, ttl_s=0)
        pp.get_quotes(["005930"], transport=tp, ttl_s=0)
        token_calls = [c for c in fake.calls if c[1].endswith("/oauth2/tokenP")]
        # fake.calls엔 tokenP가 안 잡힘(tp가 가로챔) → 토큰 파일로 검증
        d = json.loads(pp._TOKEN_PATH.read_text(encoding="utf-8"))
        self.assertGreater(d["expires_at"], time.time() + 3600)  # 멀찍이


class QuoteCacheTests(_TmpEnv):
    def test_quote_cache_skips_refetch_within_ttl(self):
        fake = FakeKIS({"005930": (74000, 72000, "삼성전자")})
        pp.get_quotes(["005930"], transport=fake, ttl_s=60)
        price_calls_1 = len([c for c in fake.calls if "inquire-price" in c[1]])
        pp.get_quotes(["005930"], transport=fake, ttl_s=60)   # 캐시 신선
        price_calls_2 = len([c for c in fake.calls if "inquire-price" in c[1]])
        self.assertEqual(price_calls_1, price_calls_2)        # 재호출 없음

    def test_quote_cache_refetches_when_stale(self):
        fake = FakeKIS({"005930": (74000, 72000, "삼성전자")})
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)    # 즉시 만료
        time.sleep(0.01)
        pp.get_quotes(["005930"], transport=fake, ttl_s=0)
        price_calls = len([c for c in fake.calls if "inquire-price" in c[1]])
        self.assertEqual(price_calls, 2)


class FakeDataPortal:
    """주식시세정보 transport 스텁 — likeItmsNm/likeSrtnCd 부분일치 필터."""
    def __init__(self, items, *, wrap=False, single_as_dict=False):
        self.items = items
        self.wrap = wrap
        self.single_as_dict = single_as_dict
        self.calls = []

    def __call__(self, method, url, *, headers, body=None):
        from urllib.parse import urlparse, parse_qs
        self.calls.append((method, url))
        q = parse_qs(urlparse(url).query)
        matched = list(self.items)
        if "likeItmsNm" in q:
            v = q["likeItmsNm"][0]
            matched = [i for i in self.items if v in i["itmsNm"]]
        elif "likeSrtnCd" in q:
            v = q["likeSrtnCd"][0]
            matched = [i for i in self.items if v in i["srtnCd"]]
        if not matched:
            body = {"items": {}, "totalCount": "0"}
        else:
            item = matched[0] if (self.single_as_dict and len(matched) == 1) else matched
            body = {"items": {"item": item}, "totalCount": str(len(matched))}
        return {"response": {"body": body}} if self.wrap else {"body": body}


_DP_ITEMS = [
    {"srtnCd": "005930", "itmsNm": "삼성전자", "clpr": "74000", "vs": 2000,
     "fltRt": 2.78, "basDt": "20260603", "mrktCtg": "KOSPI"},
    {"srtnCd": "005930", "itmsNm": "삼성전자", "clpr": "72000", "vs": -1000,
     "fltRt": -1.37, "basDt": "20260602", "mrktCtg": "KOSPI"},   # 전 영업일
    {"srtnCd": "005935", "itmsNm": "삼성전자우", "clpr": "60000", "vs": -500,
     "fltRt": -0.83, "basDt": "20260603", "mrktCtg": "KOSPI"},
    {"srtnCd": "000660", "itmsNm": "SK하이닉스", "clpr": "180000", "vs": 5000,
     "fltRt": 2.86, "basDt": "20260603", "mrktCtg": "KOSPI"},
]


class _DPEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._p = [
            mock.patch.object(pp, "_TOKEN_PATH", d / ".kis_token.json"),
            mock.patch.object(pp, "_QUOTE_CACHE", d / "kis_quotes.json"),
            mock.patch.object(pp, "_CODE_CACHE", d / "stock_codes.json"),
            mock.patch.object(pp, "_DATA_DIR", d),
        ]
        for p in self._p:
            p.start()
        self.env = mock.patch.dict(os.environ, {
            "TRADE_DATA_GO_KR_KEY": "DK", "TRADE_PRICE_PROVIDER": "auto",
        }, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for p in self._p:
            p.stop()
        self.tmp.cleanup()


class DataPortalTests(_DPEnv):
    def test_auto_selects_dataportal_when_datagokr_key(self):
        self.assertEqual(pp._provider(), "dataportal")
        self.assertTrue(pp.provider_active())

    def test_by_name_exact_match_excludes_preferred_share(self):
        # "삼성전자"로 검색하면 삼성전자우도 오지만, 정확일치로 005930만.
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.get_quotes_by_name(["삼성전자"], transport=fake)
        self.assertIn("삼성전자", out)
        q = out["삼성전자"]
        self.assertEqual(q.symbol, "005930")        # 우선주(005935) 아님
        self.assertEqual(q.price, 74000)
        self.assertEqual(q.change, 2000)
        self.assertAlmostEqual(q.change_pct, 2.78)
        self.assertEqual(q.prev_close, 72000)       # 74000 - 2000

    def test_by_name_picks_latest_basdt(self):
        # 005930이 두 영업일(20260602/20260603) → 최신 20260603(74000).
        fake = FakeDataPortal(_DP_ITEMS)
        q = pp.get_quotes_by_name(["삼성전자"], transport=fake)["삼성전자"]
        self.assertEqual(q.price, 74000)

    def test_by_name_unknown_omitted(self):
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.get_quotes_by_name(["없는회사"], transport=fake)
        self.assertEqual(out, {})                    # 틀린 종목 안 붙임

    def test_by_code(self):
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.get_quotes(["000660"], transport=fake)
        self.assertEqual(out["000660"].name, "SK하이닉스")

    def test_handles_single_item_dict_shape(self):
        # 결과 1건이면 data.go.kr이 item을 list 아닌 dict로 줄 수 있음.
        fake = FakeDataPortal(_DP_ITEMS, single_as_dict=True)
        out = pp.get_quotes_by_name(["SK하이닉스"], transport=fake)
        self.assertEqual(out["SK하이닉스"].symbol, "000660")

    def test_handles_response_wrapper(self):
        fake = FakeDataPortal(_DP_ITEMS, wrap=True)
        out = pp.get_quotes_by_name(["SK하이닉스"], transport=fake)
        self.assertEqual(out["SK하이닉스"].symbol, "000660")

    def test_name_cache_skips_refetch(self):
        fake = FakeDataPortal(_DP_ITEMS)
        pp.get_quotes_by_name(["삼성전자"], transport=fake, ttl_s=60)
        n1 = len(fake.calls)
        pp.get_quotes_by_name(["삼성전자"], transport=fake, ttl_s=60)
        self.assertEqual(len(fake.calls), n1)        # 캐시 신선 → 재호출 0

    def test_by_name_empty_when_provider_not_dataportal(self):
        with mock.patch.dict(os.environ, {"TRADE_PRICE_PROVIDER": "none"}):
            self.assertEqual(pp.get_quotes_by_name(["삼성전자"],
                                                   transport=FakeDataPortal(_DP_ITEMS)), {})

    def test_transport_exception_safe(self):
        def boom(*a, **k):
            raise RuntimeError("down")
        self.assertEqual(pp.get_quotes_by_name(["삼성전자"], transport=boom), {})


class FakeHybrid:
    """data.go.kr(이름→코드) + KIS(코드→시세) 둘 다 응답하는 transport."""
    def __init__(self, dp_items, kis_prices, *, token="TOK"):
        self.dp = FakeDataPortal(dp_items)
        self.kis = FakeKIS(kis_prices, token=token)
        self.calls = []

    def __call__(self, method, url, *, headers, body=None):
        self.calls.append(url)
        if "getStockPriceInfo" in url:
            return self.dp(method, url, headers=headers, body=body)
        return self.kis(method, url, headers=headers, body=body)


class RecommendedTtlTests(unittest.TestCase):
    def test_market_hours_short(self):
        from datetime import datetime
        with mock.patch.dict(os.environ, {}, clear=True):
            t = datetime(2026, 6, 5, 11, 0, tzinfo=pp._KST)   # 금 11:00 장중
            self.assertEqual(pp.recommended_ttl(t), 90)

    def test_after_close_long(self):
        from datetime import datetime
        with mock.patch.dict(os.environ, {}, clear=True):
            t = datetime(2026, 6, 5, 17, 0, tzinfo=pp._KST)   # 금 17:00 마감 후
            self.assertEqual(pp.recommended_ttl(t), 21600)

    def test_weekend_long(self):
        from datetime import datetime
        with mock.patch.dict(os.environ, {}, clear=True):
            t = datetime(2026, 6, 6, 11, 0, tzinfo=pp._KST)   # 토 11:00
            self.assertEqual(pp.recommended_ttl(t), 21600)


class _KisHybridEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._p = [
            mock.patch.object(pp, "_TOKEN_PATH", d / ".kis_token.json"),
            mock.patch.object(pp, "_QUOTE_CACHE", d / "kis_quotes.json"),
            mock.patch.object(pp, "_CODE_CACHE", d / "stock_codes.json"),
            mock.patch.object(pp, "_DATA_DIR", d),
        ]
        for p in self._p:
            p.start()
        self.env = mock.patch.dict(os.environ, {
            "TRADE_KIS_APPKEY": "AK", "TRADE_KIS_APPSECRET": "AS",
            "TRADE_DATA_GO_KR_KEY": "DK", "TRADE_PRICE_PROVIDER": "kis",
            "TRADE_KIS_THROTTLE_MS": "0",
        }, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for p in self._p:
            p.stop()
        self.tmp.cleanup()


class KisHybridTests(_KisHybridEnv):
    def test_name_resolves_via_datagokr_price_via_kis(self):
        # 이름→코드는 data.go.kr, 시세는 KIS 실시간.
        h = FakeHybrid(_DP_ITEMS, {"005930": (351500, 360500, "삼성전자"),
                                   "000660": (180000, 175000, "SK하이닉스")})
        out = pp.get_quotes_by_name(["삼성전자", "SK하이닉스"], transport=h)
        self.assertEqual(out["삼성전자"].price, 351500)      # KIS 가격
        self.assertEqual(out["SK하이닉스"].symbol, "000660")
        # KIS inquire-price가 실제로 호출됨(실시간 경로)
        self.assertTrue(any("inquire-price" in u for u in h.calls))

    def test_code_cache_durable_skips_resolve(self):
        h = FakeHybrid(_DP_ITEMS, {"005930": (351500, 360500, "삼성전자")})
        pp.get_quotes_by_name(["삼성전자"], transport=h, ttl_s=0)
        dp1 = len([u for u in h.calls if "getStockPriceInfo" in u])
        pp.get_quotes_by_name(["삼성전자"], transport=h, ttl_s=0)
        dp2 = len([u for u in h.calls if "getStockPriceInfo" in u])
        self.assertEqual(dp1, dp2)        # 코드 캐시 → 재-resolve 0 (가격만 재호출)

    def test_unresolved_name_omitted(self):
        h = FakeHybrid(_DP_ITEMS, {"005930": (1, 1, "삼성전자")})
        out = pp.get_quotes_by_name(["없는회사"], transport=h)
        self.assertEqual(out, {})


class ResolveCodesTests(_KisHybridEnv):
    def test_exact_match_and_negative_cache(self):
        fake = FakeDataPortal(_DP_ITEMS)
        out = pp.resolve_codes(["삼성전자"], transport=fake)
        self.assertEqual(out["삼성전자"], "005930")    # 우선주 아님
        # 미일치(없는회사)는 음성 캐시 → 재호출 안 함
        pp.resolve_codes(["없는회사"], transport=fake)
        n1 = len(fake.calls)
        pp.resolve_codes(["없는회사"], transport=fake)
        self.assertEqual(len(fake.calls), n1)

    def test_no_key_returns_empty(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pp.resolve_codes(["삼성전자"],
                                              transport=FakeDataPortal(_DP_ITEMS)), {})


class SymbolCapTests(_DPEnv):
    def test_caps_symbols(self):
        with mock.patch.object(pp, "_MAX_SYMBOLS", 2):
            fake = FakeDataPortal(_DP_ITEMS)
            pp.get_quotes_by_name(["삼성전자", "SK하이닉스", "삼성전자우"], transport=fake)
            # 3개 요청했지만 상한 2 → getStockPriceInfo 호출 ≤ 2
            self.assertLessEqual(len(fake.calls), 2)


class SafetyTests(_TmpEnv):
    def test_transport_exception_returns_empty(self):
        def boom(*a, **k):
            raise RuntimeError("network down")
        self.assertEqual(pp.get_quotes(["005930"], transport=boom), {})

    def test_no_orders_or_trading_surface(self):
        # 봇 철학: 읽기 전용. 주문/매매 함수가 모듈에 없어야 함.
        for forbidden in ("place_order", "buy", "sell", "submit_order",
                          "cancel_order", "modify_order"):
            self.assertFalse(hasattr(pp, forbidden), forbidden)


if __name__ == "__main__":
    unittest.main()
