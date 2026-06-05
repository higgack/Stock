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
            mock.patch.object(pp, "_DATA_DIR", d),
        ]
        for p in self._p:
            p.start()
        self.env = mock.patch.dict(os.environ, {
            "TRADE_KIS_APPKEY": "AK", "TRADE_KIS_APPSECRET": "AS",
            "TRADE_PRICE_PROVIDER": "auto",
        }, clear=False)
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
